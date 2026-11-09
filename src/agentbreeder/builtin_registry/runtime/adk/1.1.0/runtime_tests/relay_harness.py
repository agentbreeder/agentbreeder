"""The real built-in MCP relay (service/mcp-relay@1.0.1, relay.py) in process, in front of a
mock MCP upstream, for runtime/adk@1.1.0's MCP tests. Standard library only.

The relay is loaded by path from the built-in registry next to this runtime version, so these
tests exercise the ADK client against the relay agents actually talk to. The mock upstream and
grant/pins writers are a trimmed copy of tests/test_mcp_relay.py's (the repo's tests/ isn't
importable here); keep them in step.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RELAY_PY = (
    Path(__file__).resolve().parents[4] / "service" / "mcp-relay" / "1.0.1" / "relay" / "relay.py"
)
CANARY = "ghp_CANARY_relay_harness_credential_0001"  # the upstream credential the relay holds
TOKEN = "a" * 64  # the agent's per-deployment token
DEPLOYMENT = "1" * 32


def load_relay():
    sys.dont_write_bytecode = True  # nothing written into the built-in version
    spec = importlib.util.spec_from_file_location("mcp_relay_for_runtime_tests", RELAY_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


relay_mod = load_relay()


def tool(name: str, description: str = "") -> dict:
    return {
        "name": name,
        "description": description or f"{name} tool",
        "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
    }


class Upstream:
    """A Streamable HTTP MCP server that counts what reaches it."""

    def __init__(self, tools=("list_issues", "create_issue", "delete_repo")):
        self.tools = [tool(name) for name in tools]
        self.calls: dict[str, list] = {}
        self.authorized: list[bool] = []
        self.sessions: set[str] = set()
        self.deleted: list[str] = []
        self.counter = 0
        self.lock = threading.Lock()
        self.is_error = False  # answer tools/call with an isError result
        server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        server.daemon_threads = True
        self.server = server
        self.url = f"http://127.0.0.1:{server.server_address[1]}/mcp"
        threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, status, body=b"", headers=None):
                self.send_response(status)
                if body:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self):
                self._send(405)

            def do_DELETE(self):
                session = self.headers.get("Mcp-Session-Id")
                with upstream.lock:
                    upstream.deleted.append(session)
                    upstream.sessions.discard(session)
                self._send(200)

            def do_POST(self):
                message = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                with upstream.lock:
                    upstream.authorized.append(
                        self.headers.get("Authorization") == f"Bearer {CANARY}"
                    )
                method = message.get("method")
                if method == "initialize":
                    with upstream.lock:
                        upstream.counter += 1
                        session = f"upstream-{upstream.counter}"
                        upstream.sessions.add(session)
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "mock", "version": "1"},
                    }
                    body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result})
                    return self._send(200, body.encode(), {"Mcp-Session-Id": session})
                if self.headers.get("Mcp-Session-Id") not in upstream.sessions:
                    return self._send(404, b"{}")
                if method.startswith("notifications/"):
                    return self._send(202)
                if method == "tools/list":
                    result = {"tools": upstream.tools}
                else:
                    name = message["params"]["name"]
                    with upstream.lock:
                        upstream.calls.setdefault(name, []).append(
                            message["params"].get("arguments")
                        )
                    result = {
                        "content": [{"type": "text", "text": f"ran {name}"}],
                        "isError": upstream.is_error,
                    }
                body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result})
                self._send(200, body.encode())

        return Handler


class Relay:
    """The relay as `services up` runs it: config, a 0400 credential, pins and a grant file
    for the agent's token, with the per-agent stack grant."""

    def __init__(
        self, directory: Path, upstream: Upstream, *, server="github", agent="test-agent",
        granted=("list_issues", "create_issue"), declared=("list_issues", "create_issue",
        "delete_repo"),
    ):  # fmt: skip
        self.directory = directory
        self.server = server
        self.agent = agent
        self.upstream = upstream
        self.grants = directory / "grants"
        secrets_dir = directory / "secrets"
        secrets_dir.mkdir(parents=True)
        (secrets_dir / "token").write_text(CANARY + "\n")
        os.chmod(secrets_dir / "token", 0o400)
        self.pins = directory / "pins.json"
        pins = {t["name"]: {"sha256": relay_mod.tool_hash(t), "definition": t}
                for t in upstream.tools if t["name"] in declared}  # fmt: skip
        self.pins.write_text(json.dumps({"server": server, "tools": pins}))
        self.grant(list(granted))
        self.config = {
            "server": server,
            "upstream": {"kind": "service", "url": upstream.url},
            "auth": {"type": "bearer", "secret": "token"},
            "tools": {name: "read" for name in declared},
            "agents": {agent: list(granted)},
            "allowed_ports": [443],
            "admin_token_sha256": hashlib.sha256(b"c" * 64).hexdigest(),
        }
        self.paths = {"grants": str(self.grants), "secrets": str(secrets_dir),
                      "pins": str(self.pins)}  # fmt: skip
        self.log = io.StringIO()
        self.port = 0
        self.start()

    def grant(self, tools: list[str]) -> None:
        """Write (or narrow) the grant file for TOKEN, as deploy does."""
        directory = self.grants / "dev" / self.server
        directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(TOKEN.encode()).hexdigest()
        (directory / f"{digest}.json").write_text(json.dumps({
            "deployment": DEPLOYMENT, "agent": self.agent, "environment": "dev",
            "server": self.server, "tools": tools,
        }))  # fmt: skip

    def start(self) -> None:
        self.relay = relay_mod.Relay(self.config, self.paths)
        self.relay.log_stream = self.log
        self.agents, self.admin = relay_mod.serve(self.relay, self.port, 0)
        self.port = self.agents.server_address[1]  # a restart reuses it: the agent's URL is fixed
        for server in (self.agents, self.admin):
            threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()

    def stop(self) -> None:
        for server in (self.agents, self.admin):
            server.shutdown()
            server.server_close()

    def restart(self) -> None:
        """A new relay process: its sessions are gone, so old session ids get 404."""
        self.stop()
        self.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"
