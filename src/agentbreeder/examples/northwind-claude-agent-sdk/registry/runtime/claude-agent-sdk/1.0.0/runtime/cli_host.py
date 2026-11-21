"""The CLI host: the CLI container's only process before a turn (owner decision D15, two uids).

It runs as CLI_UID in a container with no secret mount (and no network on local Docker), and
starts one Claude Code CLI per turn for the kit in the agent container:

- **It dials the kit, never the reverse** (`link_protocol.py`): it connects to the kit's control
  socket, checks the kit is KIT_UID, and says hello with the CLI version it found. The CLI must
  be the pinned one (PINNED_CLI) or the host does not start.
- **`lease`:** a per-turn directory under TURNS (0700) with `home/` (HOME and CLAUDE_CONFIG_DIR),
  `tmp/` (TMPDIR) and an empty `work/` (the working directory), and the conversation's
  transcript written into it (`transcript.py`). The id is random and never logged.
- **`run`:** the host connects to the kit's data socket, presents the kit's one-time nonce, and
  starts the CLI with that connection as its stdin and stdout, in a new session (`setsid`), with
  an environment built from scratch (the `env -i` rule, §8.4): PATH, the turn's directories, the
  model proxy, the turn token, the model alias, the SDK's own variables and the traffic switches.
  Nothing else from any process reaches it. The command line must keep every control the
  adapter sets (`check_argv`): no built-in tools, no setting sources, strict MCP config, default
  permissions, only the kit's in-process MCP server, and no extra directories or plugins.
- **`end`, a lost connection, the lease's deadline, or SIGTERM:** the turn's process group is
  killed and its directory removed. At start-up every left-over turn directory is removed.
- **The model:** the CLI's ANTHROPIC_BASE_URL is a loopback port in this container; the host
  forwards each connection to the kit's model proxy socket, checking the kit is KIT_UID.
- **Concurrent turns** are capped (`settings.json`, `max_leases`); the kit's adapter has its own
  lower cap (owner decision D16; the value is unmeasured).

The host makes itself non-dumpable, so a CLI (the same uid) cannot read its memory. Its log lines
carry no turn id, path, message or token. Stdlib only.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import transcript  # noqa: E402
from link_protocol import (  # noqa: E402
    CLI_UID,
    CONTROL,
    DATA,
    KIT_UID,
    MODEL,
    PINNED_CLI,
    SHARED_DIR,
    LineReader,
    LinkError,
    connect,
    kit_dir,
    load_settings,
    read_exact_line,
    send_line,
)

TURNS = Path("/tmp/turns")
PATH = "/usr/local/bin:/usr/bin:/bin"
TOKEN = re.compile(r"[0-9a-f]{32}")
ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,6}")
NONCE = re.compile(r"[0-9a-f]{32}")
TURN_ID = re.compile(r"[0-9a-f]{32}")
MCP_SERVER = "agentbreeder"
# The model variables the CLI reads; every one is the agent's alias (the proxy rewrites any
# model name to it anyway, §7.2.5).
MODEL_VARIABLES = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)
# No telemetry, error reports, auto-update or other non-essential traffic (F0: without these the
# CLI made a hidden model call and a HEAD request per turn), and no CLI retries (§7.2.7).
SWITCHES = {
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "DISABLE_AUTOUPDATER": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_MAX_RETRIES": "0",
}
# Flags the adapter never sets; any of them refuses the run.
FORBIDDEN_FLAGS = (
    "--add-dir",
    "--plugin-dir",
    "--continue",
    "--fork-session",
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "--settings",
    "--agents",
    "--system-prompt-file",
    "--append-system-prompt",
    "--session-mirror",
    "--debug",
    "--debug-file",
)
SECRET_PATHS = (Path("/run/secrets"), Path("/var/run/secrets"))
# The flags the pinned SDK (0.2.177) emits for the adapter's options, fresh and resumed
# (runtime_tests check the real command line); anything else refuses the run.
VALUE_FLAGS = frozenset(
    {
        "--output-format",
        "--input-format",
        "--system-prompt",
        "--tools",
        "--allowedTools",
        "--disallowedTools",
        "--max-turns",
        "--model",
        "--permission-prompt-tool",
        "--permission-mode",
        "--setting-sources",
        "--mcp-config",
        "--resume",
        "--session-id",
    }
)
BARE_FLAGS = frozenset({"--verbose", "--strict-mcp-config"})


def log(text: str) -> None:
    print(f"cli-host: {text}", flush=True)


# -- settings and start-up checks ---------------------------------------------------------------


def find_cli() -> tuple[Path, str]:
    """The SDK's bundled CLI and its version, found without importing the SDK."""
    for entry in sys.path:
        package = Path(entry or ".") / "claude_agent_sdk"
        binary = package / "_bundled" / "claude"
        version_file = package / "_cli_version.py"
        if binary.is_file() and version_file.is_file():
            match = re.search(
                r'__cli_version__\s*=\s*"([^"]+)"', version_file.read_text(encoding="utf-8")
            )
            if match is None:
                raise RuntimeError("cannot read the bundled CLI version")
            return binary, match.group(1)
    raise RuntimeError("the claude-agent-sdk wheel's bundled CLI is not installed")


def check_isolation(uid: int, environ: dict[str, str], secret_paths=SECRET_PATHS) -> None:
    """Refuse to run where the two-uid contract does not hold: as root, as the kit's uid, with a
    secret file named in the environment, or with a secret mount visible."""
    if uid in (0, KIT_UID):
        raise RuntimeError("the CLI host must not run as root or as the kit's uid")
    if any(
        name.endswith("_FILE") or name in ("DATABASE_URL", "LLM_GATEWAY_KEY") for name in environ
    ):
        raise RuntimeError("the CLI container must have no secret in its environment")
    for path in secret_paths:
        if path.exists() and any(path.iterdir()):
            raise RuntimeError("the CLI container must have no secret mount")


def no_dump() -> None:
    """PR_SET_DUMPABLE 0: a CLI (the same uid) cannot ptrace this process or read its memory."""
    if not sys.platform.startswith("linux"):
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE
        raise RuntimeError("cannot make the CLI host non-dumpable")


def sweep(turns: Path = TURNS) -> None:
    """Remove every turn directory a killed turn or a restarted process left behind (§8.4)."""
    if turns.exists():
        shutil.rmtree(turns, ignore_errors=True)
    turns.mkdir(mode=0o700, parents=True, exist_ok=True)
    turns.chmod(0o700)


# -- the command line and environment of one CLI ------------------------------------------------


def _flag_values(argv: list[str], flag: str) -> list[str]:
    values = []
    for position, part in enumerate(argv):
        if part == flag:
            if position + 1 >= len(argv):
                raise LinkError(f"{flag} has no value")
            values.append(argv[position + 1])
        elif part.startswith(flag + "="):
            values.append(part[len(flag) + 1 :])
    return values


def check_argv(argv: object, session_id: str, resume: bool) -> list[str]:
    """The CLI's arguments (without the program name), as the adapter's options produce them.
    Returns them unchanged, or raises LinkError naming the broken control."""
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv) or len(argv) > 64:
        raise LinkError("argv must be a list of strings")
    # Linux caps one argument at 128 KiB (MAX_ARG_STRLEN); the system prompt carries the notes.
    if sum(len(a) for a in argv) > 512 * 1024 or any(len(a.encode()) > 120 * 1024 for a in argv):
        raise LinkError("argv too long")
    for part in argv:
        if any(part == f or part.startswith(f + "=") for f in FORBIDDEN_FLAGS):
            raise LinkError("a forbidden flag")
    position = 0
    while position < len(argv):  # every token is a flag the pinned SDK emits, or its value
        flag, _, inline = argv[position].partition("=")
        if flag in VALUE_FLAGS:
            position += 1 if inline or "=" in argv[position] else 2
        elif flag in BARE_FLAGS and not inline and "=" not in argv[position]:
            position += 1
        else:
            raise LinkError("an unknown flag")
    if position != len(argv):
        raise LinkError("a flag has no value")
    required = {
        "--tools": [""],
        "--setting-sources": [""],
        "--permission-mode": ["default"],
        "--input-format": ["stream-json"],
        "--output-format": ["stream-json"],
    }
    for flag, expected in required.items():
        if _flag_values(argv, flag) != expected:
            raise LinkError(f"{flag} must be {expected[0]!r}")
    # The deny-by-default `can_use_tool` routes permission prompts to the SDK over stdio.
    if _flag_values(argv, "--permission-prompt-tool") not in ([], ["stdio"]):
        raise LinkError("--permission-prompt-tool may only be stdio")
    if "--strict-mcp-config" not in argv:
        raise LinkError("--strict-mcp-config is required")
    configs = _flag_values(argv, "--mcp-config")
    try:
        servers = json.loads(configs[0])["mcpServers"] if len(configs) == 1 else None
    except (ValueError, KeyError, TypeError) as error:
        raise LinkError("--mcp-config must be JSON") from error
    if servers != {MCP_SERVER: {"type": "sdk", "name": MCP_SERVER}}:
        raise LinkError("only the kit's in-process MCP server is allowed")
    allowed = _flag_values(argv, "--allowedTools")
    prefix = f"mcp__{MCP_SERVER}__"
    if len(allowed) > 1 or any(
        not name.startswith(prefix) for value in allowed for name in value.split(",") if name
    ):
        raise LinkError("only the kit's tools may be allowed")
    resumed = _flag_values(argv, "--resume")
    if resume:
        if resumed != [session_id]:
            raise LinkError("--resume must name the lease's session")
    elif resumed or _flag_values(argv, "--session-id") not in ([], [session_id]):
        raise LinkError("a fresh turn resumes nothing")
    return argv


def cli_env(request: object, home: Path, tmp: Path, forward_port: int) -> dict[str, str]:
    """The CLI's whole environment, built here; the kit sends only the token, alias and SDK
    version, each checked."""
    if not isinstance(request, dict) or set(request) != {"token", "alias", "sdk_version"}:
        raise LinkError("env must hold token, alias and sdk_version only")
    token, alias, version = request["token"], request["alias"], request["sdk_version"]
    if not (
        isinstance(token, str)
        and TOKEN.fullmatch(token)
        and isinstance(alias, str)
        and ALIAS.fullmatch(alias)
        and isinstance(version, str)
        and VERSION.fullmatch(version)
    ):
        raise LinkError("env values are malformed")
    return {
        "PATH": PATH,
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(home),
        "TMPDIR": str(tmp),
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{forward_port}",
        "ANTHROPIC_AUTH_TOKEN": token,
        **{name: alias for name in MODEL_VARIABLES},
        "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
        "CLAUDE_AGENT_SDK_VERSION": version,
        "CLAUDE_CODE_SDK_READS_SESSION_STATE": "1",
        **SWITCHES,
    }


# -- turns -------------------------------------------------------------------------------------


@dataclass
class Lease:
    turn: str
    root: Path
    session_id: str
    resume: bool
    deadline: float
    processes: list = field(default_factory=list)

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def work(self) -> Path:
        return self.root / "work"


def remove_tree(root: Path) -> None:
    """Remove a turn's directory even where the CLI (the same uid) took a mode away."""

    def restore(path: str) -> None:
        if not os.path.islink(path):  # never follow a link the CLI planted
            os.chmod(path, 0o700)

    def retry(function, path, _error):
        try:
            restore(os.path.dirname(path))
            restore(path)
            function(path)
        except OSError:
            pass

    for path, directories, _ in os.walk(root):
        for name in directories:
            try:
                restore(os.path.join(path, name))
            except OSError:
                pass
    shutil.rmtree(root, onerror=retry)


def reap() -> None:
    """As the CLI container's PID 1, collect exited processes nobody waits for (a CLI's own
    children that outlived its group), so they do not fill the pid limit."""
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


class Host:
    def __init__(
        self,
        shared: Path = SHARED_DIR,
        turns: Path = TURNS,
        *,
        cli: Path,
        cli_version: str,
        settings: dict,
        kit_uid: int = KIT_UID,
    ):
        self.directory = kit_dir(shared)
        self.turns = turns
        self.cli = cli
        self.cli_version = cli_version
        self.settings = settings
        self.kit_uid = kit_uid
        self.leases: dict[str, Lease] = {}
        self.guard = threading.Lock()
        self.send_guard = threading.Lock()
        self.forward_port = 0
        self.forwarder: socket.socket | None = None
        self.control: socket.socket | None = None
        self.stopping = threading.Event()

    # -- the model proxy forwarder ------------------------------------------------------------

    def start_forwarder(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(64)
        self.forwarder = server
        self.forward_port = server.getsockname()[1]
        threading.Thread(target=self._forward_loop, name="forward", daemon=True).start()

    def _forward_loop(self) -> None:
        active = threading.BoundedSemaphore(64)
        while not self.stopping.is_set():
            try:
                client, _ = self.forwarder.accept()
            except OSError:
                return
            if not active.acquire(blocking=False):
                client.close()
                continue
            threading.Thread(
                target=self._forward, args=(client, active), name="forward-conn", daemon=True
            ).start()

    def _forward(self, client: socket.socket, active) -> None:
        try:
            upstream = connect(self.directory / MODEL, self.kit_uid)
        except (OSError, LinkError):
            client.close()
            active.release()
            return

        def pump(source: socket.socket, target: socket.socket) -> None:
            try:
                while chunk := source.recv(65536):
                    target.sendall(chunk)
            except OSError:
                pass
            finally:
                for sock, how in ((target, socket.SHUT_WR), (source, socket.SHUT_RD)):
                    try:
                        sock.shutdown(how)
                    except OSError:
                        pass

        back = threading.Thread(target=pump, args=(upstream, client), daemon=True)
        back.start()
        pump(client, upstream)
        back.join(timeout=200)
        client.close()
        upstream.close()
        active.release()

    # -- the control connection ---------------------------------------------------------------

    def serve(self) -> None:
        """Dial the kit and serve it until the connection ends; then end every turn."""
        self.control = connect(self.directory / CONTROL, self.kit_uid, timeout=5.0)
        try:
            self._send({"op": "hello", "cli_version": self.cli_version, "pid": os.getpid()})
            reader = LineReader(self.control)
            while not self.stopping.is_set():
                message = reader.read()
                if message is None:
                    break
                threading.Thread(
                    target=self._handle, args=(message,), name="request", daemon=True
                ).start()
        finally:
            self.control.close()
            self.end_all()

    def _send(self, message: dict) -> None:
        with self.send_guard:
            send_line(self.control, message)

    def _handle(self, message: dict) -> None:
        request = message.get("id")
        try:
            op = message.get("op")
            if op == "lease":
                answer = self.lease(message)
            elif op == "run":
                answer = self.run(message)
            elif op == "end":
                answer = self.end(message.get("turn"))
            else:
                raise LinkError("unknown op")
            reply = {"id": request, "ok": True, **answer}
        except (LinkError, ValueError) as error:
            # The reason is fixed text from this module, never input or a path.
            reply = {"id": request, "ok": False, "error": str(error)[:200]}
        except OSError as error:
            reply = {"id": request, "ok": False, "error": type(error).__name__}
        try:
            self._send(reply)
        except OSError:
            pass

    def lease(self, message: dict) -> dict:
        session_id = message.get("session_id")
        history = message.get("history")
        if not isinstance(session_id, str) or not transcript.SESSION.fullmatch(session_id):
            raise LinkError("session_id must be a lower-case UUID")
        history = transcript.valid_history(history)
        with self.guard:
            if len(self.leases) >= self.settings["max_leases"]:
                raise LinkError("too many concurrent turns")
            turn = secrets.token_hex(16)
            root = self.turns / turn
            lease = Lease(
                turn,
                root,
                session_id,
                bool(history),
                time.monotonic() + self.settings["lease_seconds"],
            )
            self.leases[turn] = lease
        try:
            root.mkdir(mode=0o700)
            for part in (lease.home, lease.tmp, lease.work):
                part.mkdir(mode=0o700)
            if history:
                transcript.write(lease.home, str(lease.work), session_id, history)
        except BaseException:
            self.end(turn)
            raise
        return {"turn": turn}

    def _lease(self, turn: object) -> Lease:
        with self.guard:
            lease = self.leases.get(turn) if isinstance(turn, str) else None
        if lease is None or time.monotonic() > lease.deadline:
            raise LinkError("unknown turn")
        return lease

    def run(self, message: dict) -> dict:
        lease = self._lease(message.get("turn"))
        nonce = message.get("nonce")
        if not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
            raise LinkError("nonce is malformed")
        argv = check_argv(message.get("argv"), lease.session_id, lease.resume)
        environment = cli_env(message.get("env"), lease.home, lease.tmp, self.forward_port)
        with self.guard:
            if lease.processes:
                raise LinkError("a turn runs one CLI")
            lease.processes.append(None)  # claimed while the CLI starts
        data = connect(self.directory / DATA, self.kit_uid, timeout=10.0)
        try:
            send_line(data, {"nonce": nonce})
            if read_exact_line(data).get("ok") is not True:
                raise LinkError("the kit refused the data connection")
            process = subprocess.Popen(  # noqa: S603 - fixed binary, checked arguments
                [str(self.cli), *argv],
                stdin=data.fileno(),
                stdout=data.fileno(),
                stderr=subprocess.DEVNULL,  # may hold message text; never kept
                cwd=lease.work,
                env=environment,
                start_new_session=True,  # setsid: the turn's process group (§8.4)
                close_fds=True,
                umask=0o077,
            )
        finally:
            data.close()  # the CLI holds the connection now; the kit sees EOF when it exits
        with self.guard:
            lease.processes[:] = [process]
            alive = lease.turn in self.leases
        if not alive:  # the turn ended while the CLI started
            kill_group(process)
            raise LinkError("unknown turn")
        return {}

    def end(self, turn: object) -> dict:
        with self.guard:
            lease = self.leases.pop(turn, None) if isinstance(turn, str) else None
        if lease is None:
            return {}
        for process in lease.processes:
            if process is not None:
                kill_group(process)
        remove_tree(lease.root)
        return {}

    def end_all(self) -> None:
        with self.guard:
            turns = list(self.leases)
        for turn in turns:
            self.end(turn)

    def watchdog(self) -> None:
        """End every lease past its deadline, whatever the kit does."""
        while not self.stopping.wait(1.0):
            if os.getpid() == 1:
                reap()
            now = time.monotonic()
            with self.guard:
                expired = [t for t, lease in self.leases.items() if now > lease.deadline]
            for turn in expired:
                self.end(turn)


def main() -> None:
    check_isolation(os.getuid(), dict(os.environ))
    no_dump()
    os.umask(0o077)
    settings = load_settings()
    cli, version = find_cli()
    if version != PINNED_CLI:
        raise SystemExit(
            f"cli-host: refusing CLI {version}; this runtime was checked on {PINNED_CLI}"
        )
    sweep()
    host = Host(cli=cli, cli_version=version, settings=settings)
    host.start_forwarder()
    threading.Thread(target=host.watchdog, name="watchdog", daemon=True).start()

    def on_sigterm(signum, frame):
        host.stopping.set()
        host.end_all()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, on_sigterm)
    log(f"ready (CLI {version}, uid {CLI_UID})")
    while not host.stopping.is_set():
        try:
            host.serve()
            log("the kit closed the connection; turns ended")
        except (OSError, LinkError) as error:
            log(f"waiting for the kit ({type(error).__name__})")
        sweep()
        time.sleep(1.0)
