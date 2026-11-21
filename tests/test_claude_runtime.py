"""runtime/claude-agent-sdk@1.0.0's stdlib and kit parts, in the repo suite (#1089, F7).

The runtime is not shipped by AgentBreeder (owner decision D8): it is an example in Northwind's
Claude port's own project registry. These tests never import `claude_agent_sdk` (CI must not
download the wheel and its bundled CLI): the adapter's tests are in the runtime's own
`runtime_tests/`, for a user who installed the lock. Here, on real Unix sockets in one process
(so both "uids" are this process's uid, given to each side as the peer to expect):

- the kit's link and the CLI host: hello, one host at a time, leases and transcripts, a CLI
  started with a scrubbed environment and the data connection as its stdin and stdout, `end`,
  the deadline and a lost connection ending the turn;
- the host's command-line and environment rules, settings and start-up checks;
- the Anthropic Messages route of the model proxy: route, token, allow-list, alias rewrite,
  SSE, the step cap that withholds a tool-calling Nth answer, coded errors, the peer check;
- the vendored kit is byte-identical to `runtime_kit`.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor as concurrent_pool
from pathlib import Path

import httpx
import pytest

from agentbreeder import runtime_profile
from agentbreeder.runtime_kit import KIT_VERSION
from agentbreeder.strict_yaml import load_file

ROOT = Path(__file__).resolve().parents[1]
PORT = ROOT / "src/agentbreeder/examples/northwind-claude-agent-sdk"
VERSION_DIR = PORT / "registry/runtime/claude-agent-sdk/1.0.0"
RUNTIME = VERSION_DIR / "runtime"
sys.path.insert(0, str(RUNTIME))

import anthropic_proxy  # noqa: E402
import cli_host  # noqa: E402
import cli_link  # noqa: E402
import link_protocol  # noqa: E402
import transcript  # noqa: E402
from kit import errors  # noqa: E402
from kit.proxy import TurnState  # noqa: E402

UID = os.getuid()
SETTINGS = {"max_concurrent_turns": 2, "max_leases": 3, "lease_seconds": 200}
SESSION = "0f8fad5b-d9cb-469f-a165-70867728950e"
TOKEN = "a" * 32


@pytest.fixture
def shared():
    # macOS caps a Unix socket path at 104 bytes: pytest's tmp_path is too long.
    directory = Path(tempfile.mkdtemp(prefix="ab-", dir="/tmp"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


# -- the runtime directory -----------------------------------------------------------------------


def _files(directory: Path) -> dict[str, bytes]:
    return {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def test_the_runtime_vendors_the_kit_byte_for_byte():
    profile = runtime_profile.load(VERSION_DIR)
    assert profile.kit == KIT_VERSION
    assert _files(RUNTIME / "kit") == _files(ROOT / "src/agentbreeder/runtime_kit")


def test_the_manifest_declares_the_anthropic_route_and_two_uids():
    profile = runtime_profile.load(VERSION_DIR)
    assert (profile.model_api, profile.isolation, profile.profile) == (
        "anthropic-messages",
        "two-uid",
        "kit",
    )
    assert profile.capabilities == frozenset({"knowledge", "notes"})
    assert profile.limits["mcp_servers"] == 0
    meta = load_file(VERSION_DIR / "manifest.yaml")
    assert meta["framework"] == "claude-agent-sdk"


def test_the_runtime_is_not_in_the_builtin_registry():
    """D8: AgentBreeder publishes no `builtin_registry` entry with the CLI."""
    assert not (ROOT / "src/agentbreeder/builtin_registry/runtime/claude-agent-sdk").exists()


def test_the_lock_pins_the_probed_sdk_with_hashes_and_the_dockerfile_is_fixed():
    lock = (RUNTIME / "requirements.lock").read_text()
    assert "claude-agent-sdk==0.2.177 \\" in lock
    pins = [line for line in lock.splitlines() if line and line[0].isalpha()]
    assert pins and all(line.endswith("\\") for line in pins)
    dockerfile = (VERSION_DIR / "Dockerfile").read_text()
    assert 'org.agentbreeder.runtime="runtime/claude-agent-sdk@1.0.0"' in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert "--require-hashes --only-binary=:all:" in dockerfile
    assert "ARG " not in dockerfile
    # The pins the runtime checks itself against match the lock.
    assert link_protocol.PINNED_SDK == "0.2.177"


def test_no_runtime_module_but_the_adapter_imports_the_sdk():
    for path in RUNTIME.glob("*.py"):
        text = path.read_text()
        if path.name != "claude_adapter.py":
            assert "claude_agent_sdk" not in text.replace("claude_agent_sdk/", "").replace(
                '"claude_agent_sdk"', ""
            ), path.name


# -- settings and start-up -------------------------------------------------------------------


def test_settings_are_bounded_integers(tmp_path):
    assert link_protocol.load_settings() == {
        "max_concurrent_turns": 2,
        "max_leases": 3,
        "lease_seconds": 200,
    }
    for bad in (
        {**SETTINGS, "extra": 1},
        {**SETTINGS, "max_leases": 0},
        {**SETTINGS, "lease_seconds": True},
        {**SETTINGS, "max_concurrent_turns": 4, "max_leases": 3},
    ):
        path = tmp_path / "s.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            link_protocol.load_settings(path)


# D16, measured on Docker (macOS 13 x86_64, 2026-11-20; F7-claude-agent-sdk.md): the CLI
# container's peak memory in MiB with N truly concurrent Northwind turns (the highest of three
# runs), and the idle container's.
CLI_PEAK_MIB = {1: 136.3, 2: 271.3, 3: 387.6, 4: 416.7}
CLI_IDLE_MIB = 17.1
CLI_LIMIT_MIB = 512  # the CLI container's --memory (egress/kit.py) and the kind pod's limit


def test_the_concurrent_turn_cap_is_set_from_the_measurement():
    """D16: the cap is the most turns whose measured peak keeps a quarter of the CLI container's
    memory free; `max_leases` (a turn whose lease outlives its slot) is the most CLIs that fit
    under the limit at the worst measured memory per CLI."""
    settings = link_protocol.load_settings()
    cap, leases = settings["max_concurrent_turns"], settings["max_leases"]
    assert CLI_PEAK_MIB[cap] <= 0.75 * CLI_LIMIT_MIB < CLI_PEAK_MIB[cap + 1]
    per_cli = max((peak - CLI_IDLE_MIB) / n for n, peak in CLI_PEAK_MIB.items())
    assert CLI_IDLE_MIB + leases * per_cli < CLI_LIMIT_MIB <= CLI_IDLE_MIB + (leases + 1) * per_cli
    kit_source = (ROOT / "src/agentbreeder/egress/kit.py").read_text()
    assert f'"--memory",\n            "{CLI_LIMIT_MIB}m"' in kit_source


def test_the_host_refuses_to_run_where_two_uids_do_not_hold(tmp_path):
    cli_host.check_isolation(65533, {"PATH": "/bin"}, secret_paths=())
    for uid in (0, 65532):
        with pytest.raises(RuntimeError):
            cli_host.check_isolation(uid, {}, secret_paths=())
    for name in ("LLM_GATEWAY_KEY_FILE", "DATABASE_URL", "LLM_GATEWAY_KEY"):
        with pytest.raises(RuntimeError):
            cli_host.check_isolation(65533, {name: "x"}, secret_paths=())
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    cli_host.check_isolation(65533, {}, secret_paths=(secrets,))  # an empty mount point
    (secrets / "key").write_text("k")
    with pytest.raises(RuntimeError):
        cli_host.check_isolation(65533, {}, secret_paths=(secrets,))


def test_find_cli_reads_the_bundled_version_without_importing_the_sdk(tmp_path, monkeypatch):
    package = tmp_path / "claude_agent_sdk"
    (package / "_bundled").mkdir(parents=True)
    (package / "_bundled" / "claude").write_text("")
    (package / "_cli_version.py").write_text('__cli_version__ = "2.1.317"\n')
    monkeypatch.setattr(sys, "path", [str(tmp_path)])
    assert cli_host.find_cli() == (package / "_bundled" / "claude", "2.1.317")
    monkeypatch.setattr(sys, "path", [str(tmp_path / "none")])
    with pytest.raises(RuntimeError):
        cli_host.find_cli()


def test_sweep_removes_every_left_over_turn(tmp_path):
    turns = tmp_path / "turns"
    (turns / "old").mkdir(parents=True)
    (turns / "old" / "message").write_text("customer text")
    cli_host.sweep(turns)
    assert turns.is_dir() and not any(turns.iterdir())
    assert turns.stat().st_mode & 0o777 == 0o700


# -- the CLI's command line and environment ---------------------------------------------------


def argv(**changes) -> list[str]:
    """The arguments the pinned SDK builds for the adapter's options (`_build_command`)."""
    parts = {
        "--output-format": "stream-json",
        "--verbose": None,
        "--system-prompt": "You are Northwind's support agent.",
        "--tools": "",
        "--allowedTools": "mcp__agentbreeder__orders,mcp__agentbreeder__search_knowledge_base",
        "--max-turns": "12",
        "--disallowedTools": "Bash,Read",
        "--model": "support-model",
        "--permission-prompt-tool": "stdio",
        "--permission-mode": "default",
        "--setting-sources=": None,
        "--mcp-config": json.dumps(
            {"mcpServers": {"agentbreeder": {"type": "sdk", "name": "agentbreeder"}}}
        ),
        "--strict-mcp-config": None,
        "--input-format": "stream-json",
    }
    parts.update(changes)
    result = []
    for flag, value in parts.items():
        if value is False:
            continue
        result.append(flag)
        if value is not None:
            result.append(value)
    return result


def test_the_adapters_command_line_passes():
    assert cli_host.check_argv(argv(), SESSION, resume=False) == argv()
    resumed = [*argv(), f"--resume={SESSION}"]
    assert cli_host.check_argv(resumed, SESSION, resume=True) == resumed


@pytest.mark.parametrize(
    "broken",
    [
        {"--tools": "default"},
        {"--tools": False},
        {"--setting-sources=": False},
        {"--setting-sources=user": None},
        {"--permission-mode": "bypassPermissions"},
        {"--strict-mcp-config": False},
        {"--permission-prompt-tool": "mcp__x__y"},
        {"--add-dir": "/"},
        {"--plugin-dir": "/tmp/p"},
        {"--dangerously-skip-permissions": None},
        {"--settings": "{}"},
        {"--continue": None},
        {"--mcp-config": json.dumps({"mcpServers": {"x": {"type": "stdio", "command": "sh"}}})},
        {"--mcp-config": "not json"},
        {"--allowedTools": "Bash"},
        {"--output-format": "text"},
    ],
)
def test_a_command_line_that_drops_a_control_is_refused(broken):
    with pytest.raises(link_protocol.LinkError):
        cli_host.check_argv(argv(**broken), SESSION, resume=False)


def test_resume_must_name_the_leases_session():
    with pytest.raises(link_protocol.LinkError):
        cli_host.check_argv(argv(), SESSION, resume=True)
    with pytest.raises(link_protocol.LinkError):
        cli_host.check_argv([*argv(), "--resume=" + "1" * 36], SESSION, resume=True)
    with pytest.raises(link_protocol.LinkError):
        cli_host.check_argv([*argv(), f"--resume={SESSION}"], SESSION, resume=False)
    with pytest.raises(link_protocol.LinkError):
        cli_host.check_argv("--tools", SESSION, resume=False)


def test_the_cli_environment_is_built_from_scratch(tmp_path):
    env = cli_host.cli_env(
        {"token": TOKEN, "alias": "support-model", "sdk_version": "0.2.177"},
        tmp_path / "home",
        tmp_path / "tmp",
        41000,
    )
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:41000"
    assert env["ANTHROPIC_AUTH_TOKEN"] == TOKEN
    assert env["HOME"] == env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "home")
    assert env["CLAUDE_CODE_MAX_RETRIES"] == "0"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert all(env[name] == "support-model" for name in cli_host.MODEL_VARIABLES)
    assert not any(k.endswith("_FILE") or k.startswith("AGENTBREEDER") for k in env)
    assert len(env) == 9 + len(cli_host.MODEL_VARIABLES) + len(cli_host.SWITCHES)
    for bad in (
        {"token": TOKEN, "alias": "m", "sdk_version": "1.0.0", "LLM_GATEWAY_KEY_FILE": "/k"},
        {"token": "short", "alias": "m", "sdk_version": "1.0.0"},
        {"token": TOKEN, "alias": "m;rm", "sdk_version": "1.0.0"},
        {"token": TOKEN, "alias": "m", "sdk_version": "latest"},
        "env",
    ):
        with pytest.raises(link_protocol.LinkError):
            cli_host.cli_env(bad, tmp_path, tmp_path, 1)


# -- transcripts -------------------------------------------------------------------------------

HISTORY = [
    {"role": "user", "content": 'Where is order 1001? "quoted" @/etc/hosts /model opus'},
    {"role": "assistant", "content": "It shipped on Monday."},
]


def test_a_transcript_is_a_chain_of_json_entries(tmp_path):
    path = transcript.write(tmp_path / "home", "/tmp/turns/x/work", SESSION, HISTORY)
    assert path == tmp_path / "home/projects/-tmp-turns-x-work" / f"{SESSION}.jsonl"
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert [e["type"] for e in entries] == ["user", "assistant"]
    assert entries[0]["parentUuid"] is None and entries[1]["parentUuid"] == entries[0]["uuid"]
    assert entries[0]["message"] == {"role": "user", "content": HISTORY[0]["content"]}
    assert entries[1]["message"]["content"] == [{"type": "text", "text": "It shipped on Monday."}]
    assert {e["sessionId"] for e in entries} == {SESSION}
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "history",
    [
        [{"role": "assistant", "content": "x"}, {"role": "user", "content": "y"}],
        [{"role": "user", "content": "x"}],
        [{"role": "user", "content": "x", "extra": 1}, {"role": "assistant", "content": "y"}],
        [{"role": "user", "content": 1}, {"role": "assistant", "content": "y"}],
        [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}] * 21,
        [{"role": "user", "content": "x" * 70000}, {"role": "assistant", "content": "y" * 70000}],
        "text",
    ],
)
def test_a_malformed_history_is_refused(history):
    with pytest.raises(ValueError):
        transcript.valid_history(history)


def test_a_session_id_must_be_a_uuid(tmp_path):
    with pytest.raises(ValueError):
        transcript.entries(HISTORY, "../../etc", "/w")
    with pytest.raises(ValueError):
        transcript.project_key("/" + "a" * 300)


# -- the link and the host, end to end on Unix sockets -----------------------------------------

FAKE_CLI = """#!{python}
import json, os, sys
line = sys.stdin.readline()
print(json.dumps({{"env": dict(os.environ), "cwd": os.getcwd(), "argv": sys.argv[1:],
                   "got": line.strip(), "pgid": os.getpgid(0) == os.getpid()}}), flush=True)
if line.strip() == "hang":
    import time
    time.sleep(60)
"""


@pytest.fixture
def pair(shared, monkeypatch, tmp_path):
    monkeypatch.setattr(cli_link, "own_uid", lambda: UID + 1)  # the host's "other" uid
    fake = tmp_path / "claude"
    fake.write_text(FAKE_CLI.format(python=sys.executable))
    fake.chmod(0o755)
    link = cli_link.CliLink(shared, host_uid=UID)
    link.start()
    turns = shared / "turns"
    cli_host.sweep(turns)
    host = cli_host.Host(
        shared, turns, cli=fake, cli_version="2.1.317", settings=dict(SETTINGS), kit_uid=UID
    )
    host.start_forwarder()
    thread = threading.Thread(target=host.serve, daemon=True)
    thread.start()
    assert link.wait_ready(5)
    yield link, host
    host.stopping.set()
    host.forwarder.close()
    link.close()
    thread.join(5)


def test_the_kit_refuses_a_host_under_its_own_uid(shared):
    with pytest.raises(link_protocol.LinkError):
        cli_link.CliLink(shared, host_uid=UID).start()


def test_lease_run_and_end(pair):
    link, host = pair
    turn = link.lease(SESSION, HISTORY)
    lease = host.leases[turn]
    written = (
        lease.home / "projects" / transcript.project_key(str(lease.work)) / f"{SESSION}.jsonl"
    )
    assert written.exists()
    resumed = [*argv(), f"--resume={SESSION}"]
    sock = link.run(
        turn, resumed, {"token": TOKEN, "alias": "support-model", "sdk_version": "0.2.177"}
    )
    sock.sendall(b"hello\n")
    sock.settimeout(10)
    data = b""
    while not data.endswith(b"\n"):
        data += sock.recv(65536)
    report = json.loads(data)
    assert report["got"] == "hello" and report["argv"] == resumed
    assert report["pgid"] is True  # its own process group (setsid)
    assert Path(report["cwd"]).resolve() == lease.work.resolve()
    env = report["env"]
    assert env["ANTHROPIC_AUTH_TOKEN"] == TOKEN
    assert env["ANTHROPIC_BASE_URL"] == f"http://127.0.0.1:{host.forward_port}"
    assert set(env) <= set(
        cli_host.cli_env(
            {"token": TOKEN, "alias": "a", "sdk_version": "1.0.0"}, lease.home, lease.tmp, 1
        )
    ) | {"__CF_USER_TEXT_ENCODING", "LC_CTYPE", "PWD", "SHLVL", "_"}
    sock.close()
    link.end(turn)
    assert turn not in host.leases and not lease.root.exists()


def test_end_kills_a_running_cli_and_removes_its_directory(pair):
    link, host = pair
    turn = link.lease(SESSION, [])
    sock = link.run(turn, argv(), {"token": TOKEN, "alias": "m", "sdk_version": "0.2.177"})
    sock.sendall(b"hang\n")
    sock.settimeout(10)
    assert sock.recv(65536)
    process = host.leases[turn].processes[0]
    root = host.leases[turn].root
    link.end(turn)
    assert process.poll() is not None and not root.exists()
    sock.close()


def test_a_turn_runs_one_cli_and_an_unknown_turn_none(pair):
    link, host = pair
    turn = link.lease(SESSION, [])
    env = {"token": TOKEN, "alias": "m", "sdk_version": "0.2.177"}
    link.run(turn, argv(), env).close()
    with pytest.raises(link_protocol.LinkError, match="one CLI"):
        link.run(turn, argv(), env)
    with pytest.raises(link_protocol.LinkError, match="unknown turn"):
        link.run("f" * 32, argv(), env)
    with pytest.raises(link_protocol.LinkError, match="forbidden"):
        link.run(link.lease(SESSION, []), [*argv(), "--add-dir", "/"], env)
    link.end(turn)


def test_leases_are_capped_and_expire(pair):
    link, host = pair
    turns = [link.lease(SESSION, []) for _ in range(SETTINGS["max_leases"])]
    with pytest.raises(link_protocol.LinkError, match="too many"):
        link.lease(SESSION, [])
    host.leases[turns[0]].deadline = time.monotonic() - 1
    with pytest.raises(link_protocol.LinkError, match="unknown turn"):
        link.run(turns[0], argv(), {"token": TOKEN, "alias": "m", "sdk_version": "0.2.177"})
    host.stopping.clear()
    watchdog = threading.Thread(target=host.watchdog, daemon=True)
    watchdog.start()
    deadline = time.monotonic() + 5
    while turns[0] in host.leases and time.monotonic() < deadline:
        time.sleep(0.05)
    assert turns[0] not in host.leases
    host.stopping.set()


def test_malformed_requests_are_refused(pair):
    link, host = pair
    for message in (
        {"op": "lease", "session_id": "nope", "history": []},
        {"op": "lease", "session_id": SESSION, "history": "x"},
        {"op": "teleport"},
        {"op": "run", "turn": link.lease(SESSION, []), "nonce": "x", "argv": [], "env": {}},
    ):
        with pytest.raises(link_protocol.LinkError):
            link.request(message)
    assert link.request({"op": "end", "turn": "nothing"})["ok"] is True


def test_a_second_host_is_refused_while_one_is_connected(pair, shared):
    link, host = pair
    intruder = link_protocol.connect(link_protocol.kit_dir(shared) / "control.sock", UID)
    try:
        link_protocol.send_line(intruder, {"op": "hello", "cli_version": "2.1.317"})
    except BrokenPipeError:
        pass  # already closed: the refusal can come before the hello
    intruder.settimeout(5)
    try:
        assert intruder.recv(10) == b""  # closed at once
    except ConnectionResetError:
        pass
    assert link.request({"op": "end", "turn": "x"})["ok"] is True  # the first still serves


def test_a_data_connection_needs_the_nonce(pair, shared):
    link, _ = pair
    sock = link_protocol.connect(link_protocol.kit_dir(shared) / "data.sock", UID)
    link_protocol.send_line(sock, {"nonce": "0" * 32})
    sock.settimeout(5)
    assert sock.recv(10) == b""


def test_losing_the_kit_ends_every_turn(pair):
    link, host = pair
    turn = link.lease(SESSION, [])
    root = host.leases[turn].root
    link.close()
    deadline = time.monotonic() + 5
    while (host.leases or root.exists()) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not host.leases and not root.exists()
    with pytest.raises(cli_link.HostUnavailable):
        link.request({"op": "end", "turn": turn})


def test_the_forwarder_reaches_only_the_kits_model_socket(pair, shared):
    link, host = pair
    path = link_protocol.kit_dir(shared) / "model.sock"
    server = link_protocol.listen(path)

    def echo():
        conn, _ = server.accept()
        conn.sendall(conn.recv(100).upper())
        conn.close()

    threading.Thread(target=echo, daemon=True).start()
    client = socket.create_connection(("127.0.0.1", host.forward_port), timeout=5)
    client.sendall(b"ping")
    assert client.recv(100) == b"PING"
    client.close()
    server.close()


def test_the_wrong_hello_never_registers_a_host(shared, monkeypatch):
    monkeypatch.setattr(cli_link, "own_uid", lambda: UID + 1)
    link = cli_link.CliLink(shared, host_uid=UID)
    link.start()
    sock = link_protocol.connect(link_protocol.kit_dir(shared) / "control.sock", UID)
    link_protocol.send_line(sock, {"op": "hello", "cli_version": "9.9.9"})
    sock.settimeout(5)
    assert sock.recv(10) == b""
    assert not link.wait_ready(0.2)
    with pytest.raises(cli_link.HostUnavailable):
        link.lease(SESSION, [])
    link.close()


def test_peer_checks_refuse_another_uid(shared, monkeypatch):
    server = link_protocol.listen(shared / "x.sock")
    with pytest.raises(link_protocol.LinkError):
        link_protocol.connect(shared / "x.sock", UID + 1)
    server.close()


def test_line_reader_limits(shared):
    left, right = socket.socketpair()
    reader = link_protocol.LineReader(right)
    left.sendall(b'{"a": 1}\n[1]\n')
    assert reader.read() == {"a": 1}
    with pytest.raises(link_protocol.LinkError):
        reader.read()

    def flood():
        try:
            left.sendall(b"x" * (link_protocol.MAX_LINE + 70000))
        except OSError:
            pass  # the reader gave up and closed: expected

    sender = threading.Thread(target=flood, daemon=True)
    sender.start()
    with pytest.raises(link_protocol.LinkError):
        reader.read()
    right.close()
    left.close()
    left, right = socket.socketpair()
    left.close()
    with pytest.raises(link_protocol.LinkError):
        link_protocol.read_exact_line(right)
    with pytest.raises(link_protocol.LinkError):
        link_protocol.send_line(right, {"x": "y" * link_protocol.MAX_LINE})


# -- the Anthropic Messages route of the model proxy ------------------------------------------


def answer(*blocks, stop="end_turn", usage=None):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "gemini",
        "content": list(blocks),
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 100, "output_tokens": 20},
    }


TEXT = {"type": "text", "text": "Your order shipped."}
TOOL = {
    "type": "tool_use",
    "id": "call_1",
    "name": "mcp__agentbreeder__orders",
    "input": {"id": 1},
}


class Gateway:
    """A scripted gateway: each call pops the next (status, body, headers)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body, headers = self.answers.pop(0)
        return httpx.Response(status, json=body, headers=headers)

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


def ok(body, cost="0.001"):
    return 200, body, {"x-litellm-response-cost": cost, "x-litellm-model-group": "support-model"}


async def _no_sleep(_):
    return None


class Proxy:
    def __init__(self, shared: Path, gateway: Gateway, max_steps: int = 2):
        self.path = shared / "model.sock"
        self.gateway = gateway
        self.proxy = anthropic_proxy.AnthropicProxy(
            "http://gateway:4000",
            "sk-gateway-secret",
            self.path,
            "support-model",
            peer=UID,
            transport=httpx.MockTransport(gateway),
            sleep=_no_sleep,
        )
        self.rows: list[dict] = []

        async def called(row):
            self.rows.append(row)

        self.state = TurnState(
            turn_id="t",
            max_steps=max_steps,
            aliases=frozenset({"support-model"}),
            system="You are Northwind's support agent.",
            toolset=frozenset({"orders"}),
            on_model_called=called,
        )

    async def __aenter__(self):
        await self.proxy.start()
        self.token = self.proxy.open_turn(self.state)
        self.client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=str(self.path)), base_url="http://cli"
        )
        return self

    async def __aexit__(self, *exc):
        await self.client.aclose()
        await self.proxy.close()

    async def post(self, body=None, path="/v1/messages", headers=None, token=True):
        sent = dict(headers or {})
        if token:
            sent.setdefault("Authorization", f"Bearer {self.token}")
        return await self.client.post(path, json=body or request_body(), headers=sent)


def request_body(**changes):
    body = {
        "model": "claude-opus-4-1",
        "max_tokens": 32000,
        "stream": False,
        "system": [
            {"type": "text", "text": "You are Claude Code."},
            {"type": "text", "text": "You are Northwind's support agent."},
        ],
        "tools": [
            {"name": "mcp__agentbreeder__orders", "input_schema": {"type": "object"}},
            {"name": "DeferredToolPlaceholder", "input_schema": {"type": "object"}},
        ],
        "messages": [{"role": "user", "content": "Where is order 1?"}],
        "metadata": {"user_id": "u"},
        "context_management": {"x": 1},
        "output_config": {"y": 2},
        "guardrails": [],
        "mock_response": "hi",
        "api_base": "http://evil",
        "thinking": {"type": "enabled", "budget_tokens": 31999},
    }
    body.update(changes)
    return body


def run(coroutine):
    return asyncio.run(coroutine)


def test_a_call_is_rebuilt_from_the_allow_list_with_the_alias(shared):
    async def go():
        gateway = Gateway(ok(answer(TEXT)))
        async with Proxy(shared, gateway, max_steps=3) as p:
            response = await p.post(path="/v1/messages?beta=true")
            assert response.status_code == 200 and response.json()["content"] == [TEXT]
            (sent,) = gateway.bodies()
            assert sent["model"] == "support-model"
            assert sent["max_tokens"] == anthropic_proxy.MAX_TOKENS
            assert sent["thinking"] == {
                "type": "enabled",
                "budget_tokens": anthropic_proxy.MAX_TOKENS - 1,
            }
            for dropped in (
                "metadata",
                "context_management",
                "output_config",
                "guardrails",
                "mock_response",
                "api_base",
                "stream",
            ):
                assert dropped not in sent
            assert "tool_choice" not in sent
            request = gateway.requests[0]
            assert request.url.path == "/v1/messages" and not request.url.query
            assert request.headers["authorization"] == "Bearer sk-gateway-secret"
            assert request.headers["anthropic-version"] == anthropic_proxy.ANTHROPIC_VERSION
            assert p.state.calls == 1 and p.state.prompt_tokens == 100
            assert p.rows[0]["main_loop"] is True and p.rows[0]["model"] == "support-model"
            assert errors.turn_cost_usd(p.state.tally) == pytest.approx(0.001)

    run(go())


def test_routes_tokens_and_peers(shared, monkeypatch):
    async def go():
        async with Proxy(shared, Gateway()) as p:
            for path in (
                "/v1/chat/completions",
                "/v1/messages/count_tokens",
                "/v1/../key",
                "/v1/messages?x=1",
            ):
                response = await p.post(path=path)
                assert response.status_code == 404
                assert response.json() == {
                    "type": "error",
                    "error": {"type": "not_found", "message": "NOT_FOUND"},
                }
            assert (await p.post(token=False)).status_code == 401
            response = await p.post(token=False, headers={"x-api-key": p.token})
            assert response.status_code != 401
            p.proxy.close_turn(p.token)
            response = await p.post()
            assert (
                response.status_code == 401
                and response.json()["error"]["message"] == "TURN_UNKNOWN"
            )
            assert "sk-gateway-secret" not in response.text
            monkeypatch.setattr(anthropic_proxy, "peer_uid", lambda sock: UID + 1)
            with pytest.raises(httpx.HTTPError):
                await p.post()

    run(go())


def test_a_streamed_call_gets_the_whole_answer_as_anthropic_events(shared):
    async def go():
        thinking = {"type": "thinking", "thinking": "hm", "signature": "sig"}
        async with Proxy(
            shared, Gateway(ok(answer(thinking, TEXT, TOOL, {"type": "odd"}, stop="tool_use"))), 3
        ) as p:
            response = await p.post(request_body(stream=True))
            assert response.headers["content-type"] == "text/event-stream"
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            kinds = [e["type"] for e in events]
            assert kinds[0] == "message_start" and kinds[-2:] == ["message_delta", "message_stop"]
            assert kinds.count("content_block_start") == 3
            deltas = [e["delta"] for e in events if e["type"] == "content_block_delta"]
            assert {"type": "text_delta", "text": "Your order shipped."} in deltas
            assert {"type": "input_json_delta", "partial_json": '{"id": 1}'} in deltas
            assert {"type": "signature_delta", "signature": "sig"} in deltas
            assert events[-2]["delta"]["stop_reason"] == "tool_use"
            assert events[-2]["usage"] == {"output_tokens": 20}
            assert "stream" not in json.loads(p.gateway.requests[0].content)

    run(go())


def test_the_nth_call_goes_with_tools_off_and_a_tool_calling_answer_is_withheld(shared):
    async def go():
        gateway = Gateway(
            ok(answer(TOOL, stop="tool_use")),
            ok(answer({"type": "text", "text": "let me look"}, TOOL)),
        )
        async with Proxy(shared, gateway, max_steps=2) as p:
            assert (await p.post()).status_code == 200
            response = await p.post()
            assert response.status_code == 429
            assert response.json()["error"]["message"] == "MODEL_STEP_LIMIT"
            assert "let me look" not in response.text
            assert gateway.bodies()[1]["tool_choice"] == {"type": "none"}
            assert p.state.step_limited and p.rows[-1]["step_limited"] is True
            # An (N+1)th call is refused unsent.
            assert (await p.post()).status_code == 429 and len(gateway.requests) == 2
            with pytest.raises(Exception, match="MODEL_STEP_LIMIT"):
                anthropic_proxy.ModelProxy  # noqa: B018
                from kit.proxy import decide

                decide(p.state, "a reply", None)

    run(go())


def test_a_refused_extra_call_keeps_a_main_loop_answer(shared):
    async def go():
        async with Proxy(shared, Gateway(ok(answer(TEXT))), max_steps=1) as p:
            assert (await p.post()).status_code == 200
            assert (await p.post()).status_code == 429
            from kit.proxy import decide

            assert decide(p.state, None, None) == "Your order shipped."

    run(go())


def test_a_hidden_call_is_not_main_loop(shared):
    async def go():
        async with Proxy(shared, Gateway(ok(answer(TEXT)), ok(answer(TEXT))), max_steps=3) as p:
            await p.post(request_body(system="Summarise this conversation.", tools=[]))
            await p.post(request_body(tools=[{"name": "DeferredToolPlaceholder"}]))
            assert [r["main_loop"] for r in p.rows] == [False, False]

    run(go())


def test_a_gateway_failure_is_coded_and_sticks_for_the_turn(shared):
    async def go():
        gateway = Gateway(
            (
                400,
                {"error": {"message": "Budget exceeded for sk-...", "type": "budget_exceeded"}},
                {},
            )
        )
        async with Proxy(shared, gateway) as p:
            response = await p.post()
            assert response.status_code == 400
            assert response.json()["error"]["message"] == "MODEL_BUDGET_EXCEEDED"
            assert "Budget exceeded" not in response.text
            again = await p.post()
            assert again.status_code == 400 and len(gateway.requests) == 1

    run(go())


def test_retries_follow_the_kits_rules(shared):
    async def go():
        gateway = Gateway((503, {}, {}), ok(answer(TEXT)))
        async with Proxy(shared, gateway) as p:
            assert (await p.post()).status_code == 200
            assert len(gateway.requests) == 2 and p.state.calls == 1

    run(go())


@pytest.mark.parametrize(
    "body", [b"not json", json.dumps({"messages": "x"}).encode(), json.dumps([1]).encode()]
)
def test_a_bad_body_is_a_bad_request(shared, body):
    async def go():
        async with Proxy(shared, Gateway()) as p:
            response = await p.client.post(
                "/v1/messages", content=body, headers={"Authorization": f"Bearer {p.token}"}
            )
            assert response.status_code == 400
            assert response.json()["type"] == "error"

    run(go())


def test_an_alias_outside_the_turn_is_denied(shared):
    async def go():
        async with Proxy(shared, Gateway()) as p:
            p.state.aliases = frozenset({"other"})
            response = await p.post()
            assert response.status_code == 403
            assert p.state.failure.code == "MODEL_ACCESS_DENIED"

    run(go())


def test_body_rules_in_isolation():
    body = anthropic_proxy.upstream_body(
        {"max_tokens": 10, "thinking": {"type": "enabled", "budget_tokens": 5}, "tools": [{}]},
        "m",
        final=True,
    )
    assert body == {"max_tokens": 10, "model": "m", "tools": [{}], "tool_choice": {"type": "none"}}
    assert anthropic_proxy.upstream_body({"thinking": {"type": "adaptive", "x": 1}}, "m", False)[
        "thinking"
    ] == {"type": "adaptive"}
    assert anthropic_proxy.upstream_body({"max_tokens": True}, "m", False)["max_tokens"] == (
        anthropic_proxy.MAX_TOKENS
    )
    assert anthropic_proxy.usage(
        {"usage": {"input_tokens": 1, "cache_read_input_tokens": 2, "output_tokens": True}}
    ) == (3, 0)
    assert anthropic_proxy.usage({}) == (0, 0)
    assert anthropic_proxy.has_tool_calls({"content": [{"type": "server_tool_use"}]})
    assert anthropic_proxy.system_text("plain") == "plain"
    assert anthropic_proxy.system_text(None) == ""


# -- the security review's fixes -----------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"tools": [{"type": "web_search_20250305", "name": "web_search"}]},
        {"tools": [{"type": "code_execution_20250522", "name": "mcp__agentbreeder__x"}]},
        {"tools": [{"name": "Bash", "input_schema": {}}]},
        {"tools": "all"},
        {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "image", "source": {"type": "url", "url": "http://x"}}],
                }
            ]
        },
        {"system": [{"type": "document", "source": {"type": "url", "url": "http://x"}}]},
    ],
)
def test_server_tools_and_url_sources_are_refused_unsent(shared, change):
    async def go():
        gateway = Gateway()
        async with Proxy(shared, gateway) as p:
            response = await p.post(request_body(**change))
            assert response.status_code == 400 and gateway.requests == []

    run(go())


def test_only_the_documented_response_fields_reach_the_cli(shared):
    async def go():
        body = {**answer(TEXT), "provider_specific_fields": {"x": 1}, "_hidden_params": {}}
        async with Proxy(shared, Gateway(ok(body)), 3) as p:
            reply = (await p.post()).json()
            assert set(reply) == set(anthropic_proxy.RESPONSE_FIELDS)

    run(go())


def test_an_unknown_or_respelled_flag_is_refused():
    for extra in (["--allowed-tools", "Bash"], ["--ide"], ["positional"], ["--verbose=1"]):
        with pytest.raises(link_protocol.LinkError):
            cli_host.check_argv([*argv(), *extra], SESSION, resume=False)
    with pytest.raises(link_protocol.LinkError):
        cli_host.check_argv([*argv(), "--model"], SESSION, resume=False)


def test_the_kit_refuses_a_socket_directory_it_does_not_own(shared, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (shared / "kit").symlink_to(target)
    with pytest.raises(link_protocol.LinkError):
        link_protocol.own_dir(shared / "kit")


def test_sockets_are_created_world_connectable_without_a_chmod(shared):
    server = link_protocol.listen(shared / "s.sock")
    assert (shared / "s.sock").stat().st_mode & 0o777 == 0o666
    server.close()


def test_a_turn_directory_is_removed_even_after_the_cli_took_its_modes(tmp_path):
    root = tmp_path / "turn"
    (root / "home" / "deep").mkdir(parents=True)
    (root / "home" / "deep" / "transcript").write_text("customer text")
    (root / "home" / "deep").chmod(0)
    (root / "home").chmod(0)
    cli_host.remove_tree(root)
    assert not root.exists()


def test_the_data_socket_caps_waiting_handlers(shared, monkeypatch):
    monkeypatch.setattr(cli_link, "own_uid", lambda: UID + 1)
    monkeypatch.setattr(cli_link, "MAX_DATA_HANDLERS", 2)
    link = cli_link.CliLink(shared, host_uid=UID)
    link.start()
    path = link_protocol.kit_dir(shared) / "data.sock"
    held = [link_protocol.connect(path, UID) for _ in range(2)]  # each waits for its nonce
    time.sleep(0.2)
    extra = link_protocol.connect(path, UID)
    extra.settimeout(5)
    assert extra.recv(10) == b""  # over the cap: closed at once
    for sock in held + [extra]:
        sock.close()
    link.close()


def test_the_host_reaps_only_when_it_is_pid_1(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "waitpid", lambda pid, flags: calls.append(pid) or (0, 0))
    cli_host.reap()
    assert calls == [-1]


def test_sse_indices_count_emitted_blocks_and_keep_redacted_thinking():
    redacted = {"type": "redacted_thinking", "data": "opaque"}
    stream = anthropic_proxy.sse(answer({"type": "odd"}, redacted, TEXT))
    events = [
        json.loads(line[6:]) for line in stream.decode().splitlines() if line.startswith("data: ")
    ]
    starts = [e for e in events if e["type"] == "content_block_start"]
    assert [e["index"] for e in starts] == [0, 1]
    assert starts[0]["content_block"] == redacted


def test_concurrent_large_leases_never_interleave(pair):
    link, host = pair
    long = [
        {"role": "user", "content": "x" * 60000},
        {"role": "assistant", "content": "y" * 60000},
    ]
    with concurrent_pool(4) as pool:
        turns = list(pool.map(lambda _: link.lease(SESSION, long), range(3)))
    assert len(set(turns)) == 3 and all(t in host.leases for t in turns)
    for turn in turns:
        link.end(turn)


def test_an_argument_over_the_kernels_limit_is_refused():
    with pytest.raises(link_protocol.LinkError, match="too long"):
        cli_host.check_argv(argv(**{"--system-prompt": "x" * 130000}), SESSION, resume=False)


def test_removing_a_turn_never_follows_a_link_the_cli_planted(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o500)
    root = tmp_path / "turn"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    cli_host.remove_tree(root)
    assert not root.exists()
    assert outside.stat().st_mode & 0o777 == 0o500
    outside.chmod(0o700)


def test_llm_spans_nest_under_the_turn_that_bound_its_trace(shared):
    """Each CLI request is served in the proxy's own connection task, which does not carry the
    turn's context: without `bind_trace` an LLM span is a trace of its own (seen on Docker)."""
    from kit import telemetry
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        telemetry.setup("kit-test", "", enabled=False)
    exporter = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))

    async def go():
        gateway = Gateway(ok(answer(TEXT)), ok(answer(TEXT)))
        async with Proxy(shared, gateway, max_steps=3) as p:
            unbound = TurnState(turn_id="u", max_steps=3, aliases=frozenset({"support-model"}))
            other = p.proxy.open_turn(unbound)
            with telemetry.span("agent_turn", "AGENT") as root:
                p.proxy.bind_trace(p.state)
                assert (await p.post()).status_code == 200
                headers = {"Authorization": f"Bearer {other}"}
                assert (await p.post(headers=headers, token=False)).status_code == 200
            p.proxy.close_turn(p.token)
            p.proxy.close_turn(other)
            assert p.proxy._parents == {}
            return root.get_span_context()

    try:
        turn = run(go())
        bound, alone = [s for s in exporter.get_finished_spans() if s.name == "llm"]
    finally:
        exporter.shutdown()  # the processor stays on the global provider; it now drops spans
    assert bound.parent is not None and bound.parent.span_id == turn.span_id
    assert bound.context.trace_id == turn.trace_id
    assert alone.parent is None and alone.context.trace_id != turn.trace_id
