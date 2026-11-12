"""`agentbreeder quickstart`: build, gate, deploy, invoke and remove the sample (spec §5.5).

The five steps of the old `examples/http-agent/quickstart.py`, moved into the package so an
installed AgentBreeder (pip, uvx, npx) can run them. The sample image is built from a copy of the
shipped `http-agent` example made by `agentbreeder example` (files 0644, so the container's
`USER 65532` can read them). AgentBreeder commands run in-process through `cli.main` with their
output captured, against a private state directory passed as `--state-dir`. The CLI reads the
operator config only from `AGENTBREEDER_OPERATOR_CONFIG` and the Docker context from
`DOCKER_CONTEXT`, so both are set for the run and restored afterwards (quickstart is
single-threaded). `docker build` has a 180 s timeout and the image cleanup commands 60 s; the
in-process deploy, status and remove are bounded by the runtime's own Docker command timeouts and
its 30 s health wait. Only messages the quickstart or the engine wrote for users are printed;
other exceptions are reported by type, since their text can carry local details.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from agentbreeder import cli, example, hints
from agentbreeder.operator_config import CONFIG_ENV as OPERATOR_ENV
from agentbreeder.store import DeploymentError

CONTEXT_ENV = "DOCKER_CONTEXT"
TIMEOUT = 180
SAMPLE = "http-agent"

DEMO_PRINCIPALS = """# Demo only: real operators verify agents in their identity provider.
principals:
  demo-agent:
    kind: agent
    enabled: true
    environment: dev
    review_by: "2099-01-01"
    sponsors: [{id: demo-sponsor, name: demo@example.com, active: true}]
"""
DEMO_OPERATOR = """# Demo operator config: enforcement on; a static adapter stands in for an IdP.
enforcement: enforce
default_environment: dev
environments:
  dev: {targets: [local]}
providers:
  demo: {type: static, authority: demo, file: principals.yaml}
"""
STEPS = (
    "1/5 Check Docker, copy the shipped http-agent sample and build its image",
    "2/5 Check the demo identity gate (static adapter), deploy locally and check health",
    "3/5 Invoke the sample's /invoke endpoint",
    "4/5 Repeat the deploy (reuses the healthy deployment) and read the history",
    "5/5 Remove the sample container and its image; keep the history",
)


def write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(text)


def run(*arguments: str, timeout: int = 60) -> str:
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # External build/CLI output can include local details; don't copy it into error reports.
        raise RuntimeError(f"{arguments[0]} failed (exit {result.returncode})")
    return result.stdout.strip()


def _call(state: Path, *arguments: str) -> tuple[int, str]:
    """Run one AgentBreeder command in-process; its stdout and stderr are captured."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            code = cli.main([*arguments, "--state-dir", str(state), "--json"])
        except SystemExit as error:  # argparse; not expected for the fixed argv used here
            code = error.code if isinstance(error.code, int) else 2
    return code, stdout.getvalue()


def _cli(state: Path, *arguments: str) -> dict:
    code, output = _call(state, *arguments)
    if code:
        raise RuntimeError(f"agentbreeder {arguments[0]} failed (exit {code})")
    return json.loads(output)


def invoke(endpoint: str) -> dict:
    request = Request(endpoint + "/invoke", data=b'{"message":"hello"}')
    with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
        return json.load(response)


# Exceptions whose messages are written for users; anything else is reported by type only.
_REPORTED = (
    RuntimeError,
    DeploymentError,
    example.ExampleError,
    OSError,
    subprocess.TimeoutExpired,
)


def _workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="agentbreeder-demo-"))


@contextmanager
def _environment():
    """Restore the operator config and Docker context variables, set or not, afterwards."""
    saved = {key: os.environ.get(key) for key in (OPERATOR_ENV, CONTEXT_ENV)}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main(dry_run: bool = False) -> int:
    if dry_run:
        return _dry_run()
    with _environment():
        return _run()


def _dry_run() -> int:
    """`doctor` and `validate` on a copy of the sample; prints the steps; no Docker changes."""
    print("Dry run: no image is built and nothing is deployed.", flush=True)
    failed = cli.main(["doctor"]) != 0
    directory = None
    try:
        directory = _workdir()
        sample = example.copy(SAMPLE, directory / "sample")
        failed = cli.main(["validate", str(sample / "agent.yaml")]) != 0 or failed
    except (example.ExampleError, OSError) as error:
        print(f"Dry run stopped: {error}", file=sys.stderr)
        failed = True
    finally:
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
    print("The quickstart would:", *STEPS, sep="\n  ", flush=True)
    return int(failed)


def _run() -> int:
    directory = _workdir()
    state = directory / "state"
    tag = "agentbreeder-demo-" + uuid.uuid4().hex
    identifier = None
    build_attempted = False
    failed = False
    context = None

    print(f"Demo history and recovery files: {directory}", flush=True)
    write_private(directory / "principals.yaml", DEMO_PRINCIPALS)
    write_private(directory / "operator.yaml", DEMO_OPERATOR)
    os.environ[OPERATOR_ENV] = str(directory / "operator.yaml")
    try:
        from agentbreeder.runtime import Docker

        runtime = Docker()
        runtime.authorize()
        context = runtime.context
        os.environ[CONTEXT_ENV] = context
        sample = example.copy(SAMPLE, directory / "sample")
        build_attempted = True
        print("1/5 Docker ready. Building the sample…", flush=True)
        run(
            "docker",
            "--context",
            context,
            "build",
            "--tag",
            tag,
            "--iidfile",
            str(directory / "image-id"),
            str(sample),
            timeout=TIMEOUT,
        )
        image = (directory / "image-id").read_text().strip()
        manifest = directory / "agent.yaml"
        manifest.write_text(
            f"schema_version: 2\nname: {tag}\nimage: {image}\ntarget: local\n"
            "identity: {provider: demo, ref: demo-agent, environment: dev}\n"
        )
        print("2/5 Checking the demo identity gate, deploying and checking health…", flush=True)
        # Retain the ID even if deploy records a runtime failure and exits nonzero.
        code, output = _call(state, "deploy", str(manifest), "--local", "--environment", "dev")
        if output.strip():
            deployment = json.loads(output)
            identifier = deployment["id"]
        if code or identifier is None:
            raise RuntimeError("Deployment failed; inspect the retained state and retry")
        endpoint = deployment["endpoint"]
        if invoke(endpoint).get("input") != {"message": "hello"}:
            raise RuntimeError("Unexpected sample response")
        print(f"3/5 Invocation succeeded: {endpoint}", flush=True)
        repeated = _cli(state, "deploy", str(manifest), "--local", "--environment", "dev")
        if repeated["id"] != identifier or repeated["state"] != "ready":
            raise RuntimeError("Retry did not reuse the healthy deployment")
        history = _cli(state, "status", identifier)
        if len(history["events"]) < 4:
            raise RuntimeError("Deployment history was not persisted")
        print(f"4/5 Retry reused {identifier}; history persisted.", flush=True)
    except Exception as error:  # in-process engine: report any failure, then clean up
        shown = isinstance(error, _REPORTED) and str(error)
        print(f"Demo stopped: {shown or 'unexpected ' + type(error).__name__}", file=sys.stderr)
        print(
            "Check your installation and start Docker; use agentbreeder doctor.", file=sys.stderr
        )
        failed = True
    finally:
        if identifier is None and (state / "deployments.sqlite3").exists():
            try:
                from agentbreeder.store import Store

                store = Store(state)
                try:
                    record = store.active(tag)
                    identifier = record["id"] if record else None
                finally:
                    store.close()
            except Exception:
                failed = True
                print(
                    f"State recovery required at {state}. "
                    f"Retry: AGENTBREEDER_OPERATOR_CONFIG={directory / 'operator.yaml'} "
                    f"agentbreeder deploy {directory / 'agent.yaml'} --local --environment dev "
                    f"--state-dir {state}, then remove the returned ID.",
                    file=sys.stderr,
                )
        if identifier:
            try:
                if _cli(state, "remove", identifier, "--local")["state"] != "removed":
                    raise RuntimeError("Removal not verified")
                print("5/5 Sample container removed. History retained.", flush=True)
            except Exception:
                print(
                    f"Cleanup incomplete. Run: "
                    f"AGENTBREEDER_OPERATOR_CONFIG={directory / 'operator.yaml'} "
                    f"agentbreeder remove {identifier} --local "
                    f"--state-dir {state}",
                    file=sys.stderr,
                )
                failed = True
        if build_attempted and context:
            try:
                if run(
                    "docker",
                    "--context",
                    context,
                    "image",
                    "ls",
                    "--quiet",
                    "--filter",
                    "reference=" + tag,
                ):
                    run("docker", "--context", context, "image", "rm", tag)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                print(
                    f"Check sample tag cleanup: docker --context {context} image rm {tag}",
                    file=sys.stderr,
                )
                failed = True
    if not failed:  # the guided path's next step (guided §3): a starter agent of your own
        print(hints.next_line("new", "agent", "hello", "--model", "gemini"), flush=True)
    return int(failed)
