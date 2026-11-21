"""The `token` cluster provider: any conformant cluster, with short-lived tokens (#770 §4.2, K1).

A cluster AgentBreeder has no cloud API for (EKS, AKS, on-premises, k3s) is reached with bound
service account tokens that the operator's own credentials mint for one operation:

- **Mint.** The only call that runs the operator's credentials is `kubectl create token <sa>`
  against a *copy* of the operator's dedicated mint kubeconfig, taken into the session directory
  and hashed against the binding's pin before kubectl reads it (no check/use race). The file holds
  one cluster, one context and one user, data fields only; an `exec` plugin must live where the
  operator cannot write and is hashed before each mint. The mint file's server and CA must equal
  the binding's, so a token from cluster A is never sent to B. The mint environment is an
  allowlist (`PATH` of the tool directories, `HOME`, `LANG`, `TMPDIR`).
- **Use.** Every other kubectl call runs on a session kubeconfig in a per-operation directory
  (0700; files created `O_CREAT|O_EXCL|O_NOFOLLOW`), with `--server`, `--certificate-authority`,
  `--tls-server-name` and `--cache-dir` on argv from recorded values, and an allowlisted
  environment whose `HOME` is the session's, so no proxy, `KUBECONFIG`, `KUBECACHEDIR`,
  `KUBECTL_*` or CA override reaches it. kubectl is the recorded absolute path, re-hashed before
  every exec (a stat-keyed cache skips the hash when the file's identity is unchanged).
- **Remove.** The directory is deleted in `finally`, on SIGTERM and SIGHUP (raised as
  `SystemExit` while a session is open, main thread only) and SIGINT (`KeyboardInterrupt`).
  SIGKILL or a crash can leave one: every new session first deletes sibling directories older
  than 70 minutes (their tokens have expired), and `doctor` reports any left.

The token is not revocable on its own; the incident step is deleting and recreating the service
account (#770 T1). Nothing here writes a token, the mint file or its exec plugin's output
anywhere but the session directory, and kubectl's stderr is never copied into an error.

This module must not import `kubernetes_providers` at import time: that module registers this
provider at its end.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from agentbreeder.operator_config import (
    TOKEN_CONTEXT,
    KubernetesTools,
    OperatorConfigError,
    TokenBinding,
    api_server_refusal,
    parse_yaml,
    pem_certificates,
    read_private,
)
from agentbreeder.store import DeploymentError

TOKEN = "token"
IDENTITIES = ("deployer", "installer", "operator", "canary")
SESSION_MAX_AGE = 70 * 60  # seconds: older session directories hold expired tokens
RENEW_MARGIN = 5 * 60  # a session is reopened this long before its token expires
MINT_TIMEOUT = 60
_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,4096}\.[A-Za-z0-9_-]{1,8192}\.[A-Za-z0-9_-]{1,4096}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,252}")
_PREFIX = "s-"
_MINT_TOP = {
    "apiVersion",
    "kind",
    "clusters",
    "contexts",
    "users",
    "current-context",
    "preferences",
}
_MINT_CLUSTER = {"server", "certificate-authority-data", "tls-server-name", "proxy-url"}
_MINT_USER = {"token", "client-certificate-data", "client-key-data", "exec"}
_MINT_EXEC = {
    "apiVersion",
    "command",
    "args",
    "env",
    "installHint",
    "provideClusterInfo",
    "interactiveMode",
}
_ENV_KEEP = ("HOME", "LANG", "TMPDIR")
# kubectl 1.33+ reads preferences (default flags, aliases) from ~/.kube/kuberc; the mint runs with
# the operator's HOME, so both switches that turn it off are set (alpha: KUBECTL_KUBERC, later:
# KUBERC=off). Not measured against every kubectl version.
_NO_KUBERC = {"KUBECTL_KUBERC": "false", "KUBERC": "off"}


def _error(code: str, message: str) -> DeploymentError:
    return DeploymentError(f"[{code}] {message}")


# -- files and tools -----------------------------------------------------------------------------


def file_sha256(path: str | os.PathLike) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


_verified: dict[str, tuple[tuple, str]] = {}
_verified_lock = threading.Lock()


def verify_tool(path: str, expected: str, what: str = "kubectl") -> None:
    """Refuse when the tool at `path` no longer has the recorded hash. The hash is skipped when
    the file's identity (device, inode, size, mtime, ctime) equals the one last verified, since
    a write changes ctime and a user cannot set it back."""
    try:
        info = os.stat(path)
        if not stat.S_ISREG(info.st_mode):
            raise OSError
        key = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        with _verified_lock:
            cached = _verified.get(path)
        if cached is not None and cached == (key, expected):
            return
        found = file_sha256(path)
    except OSError:
        raise _error(
            "KUBERNETES_TOOL_MISSING", f"Cannot read {what} at its recorded path"
        ) from None
    if found != expected:
        raise _error(
            "KUBERNETES_TOOL_CHANGED",
            f"{what} changed since the cluster was connected; run `agentbreeder cluster connect "
            "token` again to review it",
        )
    with _verified_lock:
        _verified[path] = (key, expected)


def writable_by_user(path: str) -> bool:
    """Whether the operator's user can write `path`, the file it resolves to, or any directory
    above either (so the file could be replaced)."""
    candidates = {Path(path), Path(os.path.realpath(path))}
    for candidate in list(candidates):
        candidates.update(candidate.parents)
    return any(os.access(item, os.W_OK) for item in candidates)


# -- the mint kubeconfig --------------------------------------------------------------------------


@dataclass(frozen=True)
class MintFile:
    context: str
    server: str
    ca: tuple[bytes, ...]
    exec_command: str | None


def _only(items: Any, what: str) -> dict:
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise _error("MINT_KUBECONFIG_INVALID", f"The mint kubeconfig has exactly one {what}")
    return items[0]


def parse_mint(data: bytes) -> MintFile:
    """The dedicated mint kubeconfig: one cluster, one context, one user, data fields only (no
    file references, no auth-provider); an exec plugin by absolute path."""
    try:
        config = parse_yaml(data, "The mint kubeconfig")
    except OperatorConfigError as error:
        raise _error("MINT_KUBECONFIG_INVALID", str(error)) from None
    if not isinstance(config, dict) or set(config) - _MINT_TOP:
        raise _error("MINT_KUBECONFIG_INVALID", "The mint kubeconfig has unknown top-level keys")
    if config.get("preferences") not in (None, {}):
        raise _error("MINT_KUBECONFIG_INVALID", "The mint kubeconfig sets preferences")
    cluster_entry = _only(config.get("clusters"), "cluster")
    context_entry = _only(config.get("contexts"), "context")
    user_entry = _only(config.get("users"), "user")
    cluster = cluster_entry.get("cluster")
    context = context_entry.get("context")
    user = user_entry.get("user")
    if not all(isinstance(item, dict) for item in (cluster, context, user)):
        raise _error("MINT_KUBECONFIG_INVALID", "The mint kubeconfig is malformed")
    name = context_entry.get("name")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise _error("MINT_KUBECONFIG_INVALID", "The mint kubeconfig's context name is invalid")
    if context.get("cluster") != cluster_entry.get("name") or context.get(
        "user"
    ) != user_entry.get("name"):
        raise _error("MINT_KUBECONFIG_INVALID", "The mint context names another cluster or user")
    if set(cluster) - _MINT_CLUSTER or set(user) - _MINT_USER or not user:
        raise _error(
            "MINT_KUBECONFIG_INVALID",
            "The mint kubeconfig may hold data fields only: no file references, no "
            "auth-provider, no insecure-skip-tls-verify",
        )
    try:
        ca = base64.b64decode(str(cluster["certificate-authority-data"]), validate=True)
        certificates = tuple(pem_certificates(ca.decode("ascii")))
    except (KeyError, binascii.Error, ValueError, UnicodeDecodeError):
        raise _error(
            "MINT_KUBECONFIG_INVALID", "The mint kubeconfig needs certificate-authority-data"
        ) from None
    command = None
    plugin = user.get("exec")
    if plugin is not None:
        if not isinstance(plugin, dict) or set(plugin) - _MINT_EXEC:
            raise _error("MINT_KUBECONFIG_INVALID", "The mint exec entry has unknown keys")
        command = plugin.get("command")
        if not isinstance(command, str) or not command.startswith("/") or "\0" in command:
            raise _error("MINT_EXEC_RELATIVE", "The mint exec command must be an absolute path")
    server = cluster.get("server")
    if not isinstance(server, str):
        raise _error("MINT_KUBECONFIG_INVALID", "The mint kubeconfig has no server")
    return MintFile(name, server, certificates, command)


def check_mint(mint: MintFile, token: Mapping[str, Any]) -> None:
    """The mint file must reach the binding's server under the binding's CA; its exec plugin
    must sit where the operator cannot write it and match its pin."""
    if mint.server != token["server"]:
        raise _error(
            "KUBERNETES_MINT_MISMATCH", "The mint kubeconfig's server is not the binding's"
        )
    if set(mint.ca) != set(pem_certificates(token["ca_pem"])):
        raise _error("KUBERNETES_MINT_MISMATCH", "The mint kubeconfig's CA is not the binding's")
    if mint.exec_command is None:
        if token.get("mint_exec_sha256"):
            raise _error(
                "KUBERNETES_MINT_MISMATCH", "The binding pins an exec plugin the file lacks"
            )
        return
    if writable_by_user(mint.exec_command):
        raise _error(
            "MINT_EXEC_WRITABLE",
            "The mint exec plugin, or a directory above it, is writable by your user; install "
            "it where only an administrator can write (for example a root-owned /usr/local/bin)",
        )
    if not token.get("mint_exec_sha256"):
        raise _error("MINT_EXEC_UNPINNED", "The mint exec plugin has no recorded hash")
    verify_tool(mint.exec_command, token["mint_exec_sha256"], "The mint exec plugin")


# -- environments ---------------------------------------------------------------------------------


def mint_environment(tool_dirs: list[str], source: Mapping[str, str] | None = None) -> dict:
    """The mint's environment: `PATH` of the tool directories only, plus `HOME` (exec plugins
    read their configuration there), `LANG` and `TMPDIR`. Nothing else, so `KUBECONFIG`,
    `KUBECACHEDIR`, `KUBECTL_*`, proxies and `SSL_CERT_*` never reach it; proxies come only
    from the mint file's `proxy-url`."""
    source = os.environ if source is None else source
    environment = {key: source[key] for key in _ENV_KEEP if key in source}
    environment["PATH"] = os.pathsep.join(dict.fromkeys(tool_dirs))
    environment.update(_NO_KUBERC)
    return environment


def use_environment(tool_dir: str, home: str, source: Mapping[str, str] | None = None) -> dict:
    """kubectl's environment with a session token: as the mint's, with `HOME` the session's own
    directory, so no `~/.kube` file, cache or plugin of the operator's is read."""
    source = os.environ if source is None else source
    environment = {key: source[key] for key in ("LANG", "TMPDIR") if key in source}
    environment.update(PATH=tool_dir, HOME=home)
    environment.update(_NO_KUBERC)
    return environment


# -- session directories --------------------------------------------------------------------------


def sessions_root() -> Path:
    """`~/.agentbreeder/kube-sessions`: per user and fixed, like the cluster lock."""
    return Path.home() / ".agentbreeder" / "kube-sessions"


def _root(create: bool) -> Path | None:
    root = sessions_root()
    try:
        if create:
            root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            root.mkdir(mode=0o700, exist_ok=True)
        info = root.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise _error("STATE_UNSAFE", "Cannot create the session directory") from error
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise _error("STATE_UNSAFE", f"{root} must be a directory you own (not a symlink)")
    if info.st_mode & 0o077:
        os.chmod(root, 0o700, follow_symlinks=False)
    return root


def remove_tree(path: Path) -> None:
    """Delete a session directory without following any symlink in it."""
    try:
        if not stat.S_ISDIR(path.lstat().st_mode):
            path.unlink(missing_ok=True)
            return
    except FileNotFoundError:
        return
    for directory, subdirectories, files in os.walk(path, topdown=False, followlinks=False):
        for name in files:
            os.unlink(os.path.join(directory, name))
        for name in subdirectories:
            entry = os.path.join(directory, name)
            if stat.S_ISLNK(os.lstat(entry).st_mode):
                os.unlink(entry)
            else:
                os.rmdir(entry)
    os.rmdir(path)


def _remove_quietly(path: Path) -> None:
    """`remove_tree` in a `finally`: a failure (a directory made unwritable) is retried after
    restoring owner permissions and never replaces the operation's own error; whatever is left
    is removed by the next sweep and reported by `doctor`."""
    try:
        remove_tree(path)
    except OSError:
        try:
            for directory, subdirectories, _ in os.walk(path, followlinks=False):
                for name in subdirectories:
                    entry = os.path.join(directory, name)
                    if not os.path.islink(entry):
                        os.chmod(entry, 0o700)
            os.chmod(path, 0o700)
            remove_tree(path)
        except OSError:
            pass


def _entries(root: Path) -> Iterator[tuple[Path, os.stat_result]]:
    for entry in root.iterdir():
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        if entry.name.startswith(_PREFIX) and stat.S_ISDIR(info.st_mode):
            yield entry, info


def sweep(now: float | None = None) -> int:
    """Delete session directories older than SESSION_MAX_AGE that this user owns; returns how
    many. A symlinked root is refused; entries are `lstat`ed, never followed."""
    root = _root(create=False)
    if root is None:
        return 0
    now = time.time() if now is None else now
    removed = 0
    for entry, info in list(_entries(root)):
        if info.st_uid == os.getuid() and now - info.st_mtime > SESSION_MAX_AGE:
            try:
                remove_tree(entry)
                removed += 1
            except OSError:
                continue
    return removed


def leftovers(now: float | None = None) -> tuple[int, int]:
    """(session directories present, of which older than SESSION_MAX_AGE): read-only, for
    `doctor`. A missing or unsafe root reads as none."""
    try:
        root = _root(create=False)
        if root is None:
            return 0, 0
        now = time.time() if now is None else now
        entries = list(_entries(root))
    except (OSError, DeploymentError):
        return 0, 0
    stale = sum(1 for _, info in entries if now - info.st_mtime > SESSION_MAX_AGE)
    return len(entries), stale


def _write_new(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


@contextmanager
def _signals_raise() -> Iterator[None]:
    """While a session is open, SIGTERM and SIGHUP raise SystemExit so `finally` removes it."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def handler(signum, _frame):
        raise SystemExit(128 + signum)

    previous = {}
    for number in (signal.SIGTERM, signal.SIGHUP):
        try:
            previous[number] = signal.signal(number, handler)
        except (ValueError, OSError):
            continue
    try:
        yield
    finally:
        for number, old in previous.items():
            signal.signal(number, old)


# -- the provider ---------------------------------------------------------------------------------


def token_lifetime(token: str) -> int | None:
    """The minutes between a bound token's `iat` and `exp` claims, read without verifying it
    (the API server enforces them; this is what `connect` reports as observed)."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return int((int(claims["exp"]) - int(claims["iat"])) // 60)
    except (IndexError, ValueError, KeyError, TypeError, binascii.Error):
        return None


class TokenProvider:
    """A cluster reached with short-lived service account tokens (#770 §4.2). Recorded by
    reference: its pins live in a `cluster_bindings` row whose digest is audited (K0)."""

    name: ClassVar[str] = TOKEN
    inline: ClassVar[bool] = False

    def __init__(self, binding, *, run: Callable | None = None):
        self.binding = binding
        document = binding.document
        # The row is re-validated as the operator config would be: its digest and audit match
        # only prove consistency within the state file, and these values reach kubectl's argv.
        try:
            if set(document) != {"context", "token", "tools"}:
                raise ValueError
            self.token = TokenBinding.model_validate(document["token"]).model_dump()
            self.tools = KubernetesTools.model_validate(document["tools"]).model_dump()
            self.context = str(document["context"])
            if self.context != TOKEN_CONTEXT:
                raise ValueError
            self.accounts = dict(self.token["service_accounts"])
        except (KeyError, TypeError, ValueError):
            raise DeploymentError("Cannot read this deployment's cluster binding") from None
        self.namespace = binding.namespace
        self.run = run if run is not None else subprocess.run
        self.observed_minutes: int | None = None

    @classmethod
    def document(cls, target) -> dict:
        return {
            "context": target.context,
            "token": target.token.model_dump(),
            "tools": target.tools.model_dump(),
        }

    def _kubectl(self) -> str:
        return str(self.tools["kubectl"])

    def _verify_kubectl(self) -> None:
        verify_tool(self._kubectl(), str(self.tools["kubectl_sha256"]))

    def session(self):
        """The deployer's session: every lifecycle call (#770 §7.1)."""
        return self.session_as("deployer")

    @contextmanager
    def session_as(self, identity: str) -> Iterator:
        from agentbreeder.kubernetes_providers import Session

        if identity not in IDENTITIES:
            raise _error("KUBERNETES_UNCONFIGURED", "Unknown cluster identity")
        self._static_checks()
        root = _root(create=True)
        sweep()
        with _signals_raise():
            directory: Path | None = None
            try:
                directory = Path(tempfile.mkdtemp(prefix=_PREFIX, dir=root))
                os.chmod(directory, 0o700)
                for name in ("cache", "home"):
                    (directory / name).mkdir(mode=0o700)
                token = self._mint(directory, identity)
                # The API server may shorten tokens (--service-account-max-token-expiration):
                # the session is renewed on the observed lifetime when it is shorter.
                lifetime = min(
                    int(self.token["token_minutes"]),
                    self.observed_minutes or int(self.token["token_minutes"]),
                )
                if lifetime * 60 <= RENEW_MARGIN + 60:
                    raise _error(
                        "KUBERNETES_TOKEN_TOO_SHORT",
                        f"The cluster issued a token valid for {lifetime} minutes; at least "
                        f"{RENEW_MARGIN // 60 + 2} are needed",
                    )
                ca = directory / "ca.crt"
                _write_new(ca, self.token["ca_pem"].encode())
                config = directory / "config"
                _write_new(config, self._session_config(ca, token).encode())
                del token
                argv = [
                    "--kubeconfig",
                    str(config),
                    "--context",
                    TOKEN_CONTEXT,
                    "--server",
                    self.token["server"],
                    "--certificate-authority",
                    str(ca),
                    "--cache-dir",
                    str(directory / "cache"),
                ]
                if self.token.get("tls_server_name"):
                    argv += ["--tls-server-name", self.token["tls_server_name"]]
                kubectl = self._kubectl()
                yield Session(
                    tuple(argv),
                    use_environment(os.path.dirname(kubectl), str(directory / "home")),
                    binary=kubectl,
                    verify=self._verify_kubectl,
                    expires=time.monotonic() + lifetime * 60 - RENEW_MARGIN,
                )
            finally:
                if directory is not None:
                    _remove_quietly(directory)

    def _static_checks(self) -> None:
        """What the binding itself must satisfy before anything runs."""
        refusal = api_server_refusal(str(self.token.get("server", "")))
        if refusal:
            raise _error("KUBERNETES_SERVER_REFUSED", refusal)
        if (
            hashlib.sha256(str(self.token["ca_pem"]).encode()).hexdigest()
            != self.token["ca_sha256"]
        ):
            raise _error("KUBERNETES_CA_CHANGED", "The binding's CA does not match its pin")
        if not os.path.isabs(self._kubectl()):
            raise _error("KUBERNETES_TOOL_MISSING", "kubectl is not recorded by absolute path")

    def _mint(self, directory: Path, identity: str) -> str:
        """`kubectl create token` for `identity`'s service account, on a hashed copy of the mint
        kubeconfig, its stdout read from a pipe. Returns the token."""
        try:
            data = read_private(Path(self.token["mint_kubeconfig"]), "The mint kubeconfig")
        except OperatorConfigError as error:
            raise _error("MINT_KUBECONFIG_INVALID", str(error)) from None
        if hashlib.sha256(data).hexdigest() != self.token["mint_kubeconfig_sha256"]:
            raise _error(
                "MINT_KUBECONFIG_CHANGED",
                "The mint kubeconfig changed since the cluster was connected; run `agentbreeder "
                "cluster connect token` again to review it",
            )
        copy = directory / "mint"
        _write_new(copy, data)
        mint = parse_mint(data)
        check_mint(mint, self.token)
        self._verify_kubectl()
        kubectl = self._kubectl()
        tool_dirs = [os.path.dirname(kubectl)]
        if mint.exec_command:
            tool_dirs.append(os.path.dirname(mint.exec_command))
        argv = [
            kubectl,
            "--kubeconfig",
            str(copy),
            "--context",
            mint.context,
            "--cache-dir",
            str(directory / "cache"),
            "--namespace",
            self.namespace,
            "create",
            "token",
            "--duration",
            f"{int(self.token['token_minutes'])}m",
            "--",
            str(self.accounts[identity]),
        ]
        try:
            result = self.run(
                argv,
                capture_output=True,
                timeout=MINT_TIMEOUT,
                check=False,
                env=mint_environment(tool_dirs),
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise _error("KUBERNETES_MINT_FAILED", "Minting a token failed or timed out") from None
        if result.returncode:
            raise _error(
                "KUBERNETES_MINT_FAILED",
                f"Minting a token for {identity} failed; check the mint kubeconfig's credentials "
                "and its create serviceaccounts/token right",
            )
        output = result.stdout
        token = (
            output.decode("ascii", "replace") if isinstance(output, bytes) else output
        ).strip()
        if not _TOKEN.fullmatch(token):
            raise _error("KUBERNETES_MINT_FAILED", "The mint did not return a token")
        self.observed_minutes = token_lifetime(token)
        copy.unlink()
        return token

    def _session_config(self, ca: Path, token: str) -> str:
        cluster: dict[str, Any] = {
            "server": self.token["server"],
            "certificate-authority": str(ca),
        }
        if self.token.get("tls_server_name"):
            cluster["tls-server-name"] = self.token["tls_server_name"]
        return json.dumps(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "clusters": [{"name": TOKEN_CONTEXT, "cluster": cluster}],
                "users": [{"name": TOKEN_CONTEXT, "user": {"token": token}}],
                "contexts": [
                    {
                        "name": TOKEN_CONTEXT,
                        "context": {
                            "cluster": TOKEN_CONTEXT,
                            "user": TOKEN_CONTEXT,
                            "namespace": self.namespace,
                        },
                    }
                ],
                "current-context": TOKEN_CONTEXT,
            }
        )

    def check(self, session) -> None:
        """Before kubectl runs: the binding's own pins, and kubectl's hash. The cluster's UID is
        compared by `Kubernetes.authorize` after the first call with the token."""
        self._static_checks()
        self._verify_kubectl()

    @classmethod
    def deploy_refusal(cls) -> tuple[str, str] | None:
        """Why a deploy to this kind of cluster is refused before anything runs, or None."""
        if deploys_verified():
            return None
        return (
            "KUBERNETES_POLICY_UNCHECKED",
            "Deploys to a token cluster need the admission policy check, which is not built yet "
            "(#770 K2)",
        )

    def registry(self):
        from agentbreeder.kubernetes_providers import Registry

        return Registry(scheme="https", loopback_only=False, authenticated=True)

    def blocked_cidrs(self, session, **context) -> list[str]:
        """Computed at `connect` (`cluster_token.blocked_cidrs`), never at deploy."""
        from agentbreeder import cluster_token

        return cluster_token.blocked_cidrs(session, run=self.run, **context)


def deploys_verified() -> bool:
    """Whether deploys to a `token` cluster may run. They need the §7.2 admission policies to
    be present and unchanged, checked at every deploy (K2). Until K2 builds that check, every
    deploy to a `token` cluster is refused (`KUBERNETES_POLICY_UNCHECKED`); the lifecycle
    commands that stop or remove (remove, disable, reconcile, status, logs) still run."""
    return False


def admission_policy(session, namespace: str, *, run: Callable = subprocess.run) -> str:
    """The digest of the §7.2 admission policies in `namespace` (#770 §7.2, K2). Not built in
    K1: `connect` refuses here, after every other check has run and before anything is
    recorded, so no `token` binding is ever recorded without the policy."""
    raise _error(
        "KUBERNETES_POLICY_UNCHECKED",
        "Checking the cluster's admission policies is not built yet (#770 K2); a token cluster "
        "cannot be connected until it is. Nothing was recorded",
    )
