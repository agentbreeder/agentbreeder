"""The local Docker environment `agentbreeder conformance IMAGE` builds around an image.

Topology, all throwaway and removed afterwards:

- an `--internal` network holding the agent (alias `agent`), a Postgres with pgvector (alias
  `postgres`) and the fakes container (alias `gateway`);
- the agent created by `runtime.Docker.create`, the function `deploy` uses, so it runs with the
  same flags (read-only root, no capabilities, uid 65532, 512 MB, 128 pids, a 64 MB `/tmp`),
  on the internal network only, publishing nothing;
- the fakes container (`fakes.py` on a digest-pinned Python base) also on a second network,
  publishing its control API and its ingress forwarder on host loopback. The checks reach the
  agent through the forwarder.

Secrets reach the agent as read-only files, as at deploy: `DATABASE_URL_FILE` and
`LLM_GATEWAY_KEY_FILE`, and one relay token per `mcp:` server in the image's manifest. Their
values are random, made for this run, and are what the hygiene checks search for.

Not built yet: the DNS and TCP log of other connection attempts (spec §10.3). The internal
network already gives the agent nowhere else to connect; the `hygiene.egress` check stays
`not_run` until that log exists.

**This module has only been run against a fake Docker runner (`tests/test_conformance_
environment.py`).** No run on a real engine is recorded; see
`docs/superpowers/verification/F2a-adk-baseline.md`.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import re
import secrets
import shutil
import signal
import tarfile
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from agentbreeder import mcp as mcp_relay
from agentbreeder import registry, runtime_profile
from agentbreeder.conformance import checks, fakes, protocol, report
from agentbreeder.egress.boundary import ISOLATED_GATEWAY, SIDECAR_BASE
from agentbreeder.manifest import Manifest, ManifestError, load_manifest
from agentbreeder.registry import AGENT_NAME, RegistryError, read_project_file
from agentbreeder.runtime import MAX_TAIL, Docker, probe_http
from agentbreeder.secret_files import MOUNT_ROOT, SecretMount
from agentbreeder.store import DeploymentError

# The image `service/postgres-pgvector` runs (a test keeps the two equal).
POSTGRES_IMAGE = (
    "pgvector/pgvector@sha256:0dd3149a06e4579a0d70d8fef9945636cd514005e7237deb27f9b67886f8dc4f"
)
GATEWAY_PORT, CONTROL_PORT, INGRESS_PORT = 4000, 9000, 9001
GATEWAY_ALIAS, DATABASE_ALIAS = "gateway", "postgres"
BUILD_RECORD_LIMIT = 1024 * 1024
MAX_BUILD_RECORDS = 200
HEALTH_SECONDS = 30  # deploy's budget (`probe_http`); a slower start fails `serving.health`
DATABASE_SECONDS = 90
FAKES_SECONDS = 30
PORT_SECONDS = 10
WARM_SECONDS = 180  # a first turn builds the agent (imports, schema, knowledge sync)
LABEL = "io.agentbreeder.conformance"
_CODE_FILES = (
    ("agentbreeder/__init__.py", ("agentbreeder", "__init__.py")),
    ("agentbreeder/conformance/__init__.py", ("agentbreeder.conformance", "__init__.py")),
    ("agentbreeder/conformance/fakes.py", ("agentbreeder.conformance", "fakes.py")),
)
_PORT = re.compile(r"[0-9]{1,5}")


class ConformanceError(Exception):
    """The run could not start or finish (exit 2). Never a check result."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Subject:
    """The image under test and what its build said about it."""

    image: str
    agent: str
    runtime: str  # the runtime ref
    profile: str  # v1 or kit
    framework: str
    manifest: Manifest


def _build_record(image: str, project: Path) -> dict:
    deploy = project / "deploy"
    try:
        names = sorted(p.name for p in deploy.glob("*.build.json"))[:MAX_BUILD_RECORDS]
    except OSError:
        names = []
    for name in names:
        try:
            raw = read_project_file(deploy / name, BUILD_RECORD_LIMIT)
            data = json.loads(raw) if raw else None
        except (RegistryError, ValueError, RecursionError):
            continue
        if isinstance(data, dict) and data.get("image") == image:
            agent = data.get("name")
            if (
                isinstance(agent, str)
                and AGENT_NAME.fullmatch(agent)
                and name == (f"{agent}.build.json")
            ):
                return data
    raise ConformanceError(
        "CONFORMANCE_NO_BUILD_RECORD",
        f"no build record in {registry.clean(str(deploy), name=True)[0]} names this image; "
        "run `agentbreeder build` in the agent's project, then pass its directory with "
        "--project",
    )


def subject(image: str, project: Path) -> Subject:
    """Find what was built: the build record that names `image`, its deploy manifest and the
    profile of its runtime version. Reads files only."""
    if not report.IMAGE.fullmatch(image):
        raise ConformanceError(
            "CONFORMANCE_IMAGE",
            "IMAGE must be an image ID (sha256: and 64 hex digits), as `agentbreeder build` "
            "prints it",
        )
    record = _build_record(image, project)
    agent = record["name"]
    try:
        ref = registry.Ref.parse(record.get("runtime"))
        if ref.kind != "runtime":
            raise RegistryError("REF_INVALID", "the build record's runtime is not a runtime ref")
        found = registry.resolve(ref, registry.roots(project))
        profile = runtime_profile.load(found.path)
    except (RegistryError, runtime_profile.ProfileError) as error:
        code = getattr(error, "code", "REF_INVALID")
        raise ConformanceError(code, registry.clean(str(error))[0]) from error
    if profile.protocol != protocol.VERSION or profile.profile not in protocol.PROFILES:
        raise ConformanceError(
            "PROTOCOL_UNSUPPORTED",
            f"{ref} serves protocol {profile.protocol} ({profile.profile}); this conformance "
            f"kit checks protocol {protocol.VERSION} ({', '.join(protocol.PROFILES)})",
        )
    try:
        manifest = load_manifest(project / "deploy" / f"{agent}.deploy.yaml")
    except ManifestError as error:
        raise ConformanceError(
            "CONFORMANCE_MANIFEST", f"deploy/{agent}.deploy.yaml: {error}"
        ) from error
    if manifest.image != image:
        raise ConformanceError(
            "CONFORMANCE_MANIFEST",
            f"deploy/{agent}.deploy.yaml is for another image; build the agent again",
        )
    return Subject(image, agent, str(ref), profile.profile, profile.framework, manifest)


def fakes_context() -> tuple[bytes, str]:
    """A deterministic build context for the fakes image: `fakes.py` and a Dockerfile."""
    files = {}
    for target, (package, name) in _CODE_FILES:
        files[target] = resources.files(package).joinpath(name).read_bytes()
    digest = hashlib.sha256()
    for target in sorted(files):
        digest.update(target.encode() + b"\0" + files[target] + b"\0")
    code_digest = "sha256:" + digest.hexdigest()
    files["Dockerfile"] = (
        f"FROM {SIDECAR_BASE}\n"
        "COPY agentbreeder /opt/agentbreeder/agentbreeder\n"
        "ENV PYTHONPATH=/opt/agentbreeder PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n"
        f"LABEL {LABEL}-code={code_digest}\n"
        "USER 65532:65532\n"
        'ENTRYPOINT ["python", "-m", "agentbreeder.conformance.fakes"]\n'
    ).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(files[name]), 0o644, 0
            archive.addfile(info, io.BytesIO(files[name]))
    return buffer.getvalue(), code_digest


def _secret_file(directory: Path, name: str, env: str, value: str) -> SecretMount:
    """A value as deploy mounts one: 0444 inside a 0700 directory, read-only in the agent."""
    path = directory / name
    path.write_text(value, encoding="utf-8")
    path.chmod(0o444)
    return SecretMount((env,), name, path, f"{MOUNT_ROOT}/{name}")


class Environment:
    """Build the environment, hand the checks a `Context`, and remove everything again.

    `docker` is a `runtime.Docker` (the tests pass a fake with the same methods)."""

    def __init__(self, target: Subject, docker=None, base: Path | None = None, sleep=time.sleep):
        self.subject = target
        self.docker = docker or Docker()
        self.sleep = sleep
        self.run_id = secrets.token_hex(16)
        self.name = "agentbreeder-conformance-" + self.run_id[:12]
        self.directory = (base or report.root()) / ".runs" / self.run_id
        self.record = {"container": self.name, "id": self.run_id, "digest": target.image}
        self.token = secrets.token_urlsafe(32)
        self.secrets = {
            "the gateway key": "sk-conf-" + secrets.token_hex(24),
            "the database password": secrets.token_hex(24),
        }
        self.created: list[tuple[str, str]] = []  # (kind, name), in creation order
        self.agent_info: dict | None = None

    # -- pieces ------------------------------------------------------------------------------

    def _labels(self) -> list[str]:
        return ["--label", f"{LABEL}={self.run_id}"]

    def _isolation(self) -> list[str]:
        """Bridge options for the internal network. A Linux engine gives the host an address on
        every bridge, internal or not, so the agent could reach host services; the isolated
        gateway mode removes it (Docker Engine 28 or later; older engines accept the option
        without enforcing it). Docker Desktop keeps the gateway inside its VM."""
        try:
            system = self.docker.command("info", "--format", "{{.OperatingSystem}}")
            if "Docker Desktop" in system:
                return []
            version = self.docker.command("version", "--format", "{{.Server.Version}}")
            major = int(version.split(".", 1)[0])
        except ValueError:
            major = 0
        if major < 28:
            raise ConformanceError(
                "CONFORMANCE_ENVIRONMENT",
                "on a Linux engine the test network needs Docker Engine 28 or later",
            )
        return [f"--opt={option}" for option in ISOLATED_GATEWAY]

    def _network(self, name: str, internal: bool) -> None:
        options = ["--internal", *self._isolation()] if internal else []
        self.created.append(("network", name))
        self.docker.command("network", "create", *options, *self._labels(), name)

    def _fakes_image(self) -> str:
        context, code_digest = fakes_context()
        tag = "agentbreeder-conformance-fakes:" + code_digest[7:23]
        try:
            info = json.loads(self.docker.command("image", "inspect", tag))[0]
            if (info["Config"]["Labels"] or {}).get(f"{LABEL}-code") == code_digest:
                return tag
        except (DeploymentError, ValueError, KeyError, IndexError, TypeError):
            pass
        self.docker.command("build", "--quiet", "--tag", tag, "-", timeout=300, stdin=context)
        return tag

    def _start_database(self) -> None:
        name = self.name + "-db"
        self.created.append(("container", name))
        self.docker.command(
            "container",
            "create",
            "--name",
            name,
            *self._labels(),
            "--network",
            self.name + "-int",
            "--network-alias",
            DATABASE_ALIAS,
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--pids-limit",
            "256",
            "--security-opt",
            "no-new-privileges",
            "--log-driver",
            "local",
            "--log-opt",
            "max-size=10m",
            "--env",
            "POSTGRES_PASSWORD",  # value from the CLI's environment, not argv
            POSTGRES_IMAGE,
            timeout=300,
            env={"POSTGRES_PASSWORD": self.secrets["the database password"]},
        )
        self.docker.command("container", "start", name)
        deadline = time.monotonic() + DATABASE_SECONDS
        while True:
            try:
                # Over TCP: the image's first-start server listens on its socket only.
                self.docker.command(
                    "container", "exec", name, "pg_isready", "-q", "-h", "127.0.0.1",
                    "-U", "postgres", timeout=10,
                )  # fmt: skip
                return
            except DeploymentError:
                if time.monotonic() > deadline:
                    raise ConformanceError(
                        "CONFORMANCE_ENVIRONMENT", "the test database did not start"
                    ) from None
                self.sleep(1.0)

    def _start_fakes(self) -> tuple[int, int]:
        """Start the fakes container; (control port, ingress port) on host loopback."""
        name = self.name + "-fakes"
        self.created.append(("container", name))
        config = {
            "gateway_port": GATEWAY_PORT,
            "control_port": CONTROL_PORT,
            "ingress_port": INGRESS_PORT,
            "agent_host": "agent",
            "agent_port": self.subject.manifest.port,
        }
        self.docker.command(
            "container",
            "create",
            "--name",
            name,
            "--pull",
            "never",
            *self._labels(),
            "--network",
            self.name + "-pub",
            "--publish",
            f"127.0.0.1::{CONTROL_PORT}",
            "--publish",
            f"127.0.0.1::{INGRESS_PORT}",
            "--user",
            "65532:65532",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "256m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "256",
            "--log-driver",
            "local",
            "--env",
            f"{fakes.CONFIG_ENV}=" + json.dumps(config, separators=(",", ":")),
            "--env",
            fakes.TOKEN_ENV,  # value from the CLI's environment, not argv
            self._fakes_image(),
            timeout=60,
            env={fakes.TOKEN_ENV: self.token},
        )
        self.docker.command(
            "network", "connect", "--alias", GATEWAY_ALIAS, self.name + "-int", name
        )
        self.docker.command("container", "start", name)
        try:
            # A published port can be missing for a moment after start (as `await_binding`
            # handles for a deployment): look again until both are bound.
            deadline = time.monotonic() + PORT_SECONDS
            while True:
                info = json.loads(self.docker.command("container", "inspect", name))[0]
                ports = info["NetworkSettings"]["Ports"] or {}
                bound = all(ports.get(f"{p}/tcp") for p in (CONTROL_PORT, INGRESS_PORT))
                if bound or time.monotonic() > deadline:
                    break
                self.sleep(0.2)
            found = []
            for port in (CONTROL_PORT, INGRESS_PORT):
                (binding,) = ports[f"{port}/tcp"]
                if binding["HostIp"] != "127.0.0.1" or not _PORT.fullmatch(binding["HostPort"]):
                    raise ValueError
                found.append(int(binding["HostPort"]))
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ConformanceError(
                "CONFORMANCE_ENVIRONMENT", "the fakes container has no loopback ports"
            ) from error
        return found[0], found[1]

    def _mounts(self) -> tuple[list[SecretMount], dict[str, str]]:
        """The secret files and the plain environment the agent gets, as at deploy."""
        report.private_directory(self.directory.parent)  # 0700, yours, not a link
        self.directory.mkdir(mode=0o700)
        database = (
            f"postgresql://postgres:{self.secrets['the database password']}"
            f"@{DATABASE_ALIAS}:5432/postgres"
        )
        mounts = [
            _secret_file(self.directory, "database-url", "DATABASE_URL", database),
            _secret_file(
                self.directory,
                "llm-gateway-key",
                "LLM_GATEWAY_KEY",
                self.secrets["the gateway key"],
            ),
        ]
        gateway = f"http://{GATEWAY_ALIAS}:{GATEWAY_PORT}"
        environment = {
            "AGENTBREEDER_GATEWAY_URL": gateway,
            "AGENTBREEDER_SERVICE_TRACES_URL": gateway + "/v1/traces",
            "AGENTBREEDER_DEPLOYMENT_ID": self.run_id,
        }
        for access in self.subject.manifest.mcp_access or []:
            server = access.ref.split("/")[1].split("@")[0]
            variable = mcp_relay.env_name(server)
            token = secrets.token_hex(32)
            self.secrets[f"the relay token for {server}"] = token
            mounts.append(
                _secret_file(
                    self.directory,
                    f"mcp-{server}-token",
                    f"AGENTBREEDER_MCP_{variable}_TOKEN",
                    token,
                )
            )
            environment[f"AGENTBREEDER_MCP_{variable}_URL"] = f"{gateway}/mcp/{server}"
        return mounts, environment

    # -- lifecycle ---------------------------------------------------------------------------

    def __enter__(self) -> checks.Context:
        try:
            return self._start()
        except BaseException:
            self.close()
            raise

    def _start(self) -> checks.Context:
        docker = self.docker
        try:
            docker.authorize()
            self._network(self.name + "-int", internal=True)
            self._network(self.name + "-pub", internal=False)
            self._start_database()
            control_port, ingress_port = self._start_fakes()
            mounts, environment = self._mounts()
            self.created.append(("container", self.name))
            docker.create(
                self.record,
                self.subject.manifest,
                network=self.name + "-int",
                environment=environment,
                mounts=mounts,
                labels={LABEL: self.run_id},
            )
            self.agent_info = docker.inspect(self.record)
            if self.agent_info is None:
                raise ConformanceError(
                    "CONFORMANCE_ENVIRONMENT", "the agent container was not created"
                )
            docker.start(self.agent_info)
        except DeploymentError as error:
            raise ConformanceError("CONFORMANCE_ENVIRONMENT", str(error)) from error
        control = checks.Control(f"http://127.0.0.1:{control_port}", self.token)
        deadline = time.monotonic() + FAKES_SECONDS
        while not control.healthy():
            if time.monotonic() > deadline:
                raise ConformanceError(
                    "CONFORMANCE_ENVIRONMENT", "the fakes container did not start"
                )
            self.sleep(0.5)
        # The same wait deploy gives an agent. A miss is not an environment error: the checks
        # run and `serving.health` reports it.
        probe_http(ingress_port, self.subject.manifest.health_path, HEALTH_SECONDS)
        expected = {"runtime": self.subject.runtime, "framework": self.subject.framework}
        agent = checks.Agent("127.0.0.1", ingress_port)
        # A v1 runtime may answer /health before it is built, and builds on its first turn.
        # One turn outside the report, so no check times out on that build; its outcome is
        # not judged here (the checks do that), but its reply is kept for the hygiene checks.
        try:
            agent.invoke("Hello.", session="conf-warm-" + self.run_id[:12], timeout=WARM_SECONDS)
        except (OSError, http.client.HTTPException):
            pass
        return checks.Context(
            agent=agent,
            control=control,
            profile=self.subject.profile,
            run_id=self.run_id,
            health_path=self.subject.manifest.health_path,
            expected=expected,
            secrets=dict(self.secrets),
            deployment_id=self.run_id,
            logs=self.logs,
        )

    def logs(self) -> str:
        """The agent container's whole output so far. A log too long to read whole is not
        searched in part: an agent could push a line out of the part that is read."""
        if self.agent_info is None:
            raise checks.NotRun("no container log to read")
        try:
            text, truncated = self.docker.logs(self.agent_info, tail=MAX_TAIL)
        except DeploymentError as error:
            raise checks.NotRun("the container log could not be read") from error
        if truncated or text.count("\n") >= MAX_TAIL - 1:
            raise checks.NotRun("the container log is too long to read whole")
        return text

    def close(self) -> None:
        """Remove what this run created, newest first. Never raises: a leftover is named by
        its `io.agentbreeder.conformance` label. SIGTERM and SIGHUP wait until it is done, so
        the command's handler cannot stop the cleanup halfway."""
        blocked = {signal.SIGTERM, signal.SIGHUP}
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
        try:
            self._remove()
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)

    def _remove(self) -> None:
        for kind, name in reversed(self.created):
            try:
                if kind == "container":
                    self.docker.command("container", "rm", "--force", "--volumes", name)
                else:
                    self.docker.command("network", "rm", name)
            except DeploymentError:
                pass
        self.created = []
        shutil.rmtree(self.directory, ignore_errors=True)

    def __exit__(self, *exc) -> None:
        self.close()


def run(image: str, project: Path, docker=None) -> dict:
    """Run the suite against `image` and return the report (not yet written)."""
    target = subject(image, project)
    started = report.now()
    with Environment(target, docker) as context:
        rows = checks.run(context)
    return report.build(
        image=target.image,
        runtime=target.runtime,
        profile=target.profile,
        agent=target.agent,
        rows=rows,
        started=started,
    )
