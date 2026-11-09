"""A mock MCP Streamable HTTP server for the opt-in Docker test (tests/test_mcp_relay_docker.py).

It counts tools/call per tool in /tmp/calls.json, records whether each request carried the
relay's credential (never the value), and injects a server-initiated sampling request and a
list_changed notification into every tools/call stream.
"""

import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOLS = [
    {"name": "list_issues", "description": "List issues", "inputSchema": {"type": "object"}},
    {"name": "delete_repo", "description": "Delete a repo", "inputSchema": {"type": "object"}},
]
SESSIONS = set()
LOCK = threading.Lock()
STATE = {"calls": {}, "authorized": 0, "unauthorized": 0}
EXPECTED = (
    open("/run/secrets/token").read().strip() if os.path.exists("/run/secrets/token") else ""
)


def save():
    with open("/tmp/calls.json.tmp", "w") as handle:
        json.dump(STATE, handle)
    os.replace("/tmp/calls.json.tmp", "/tmp/calls.json")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send(self, status, body=b"", content_type="application/json", headers=None):
        self.send_response(status)
        if body:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send(200, b"{}") if self.path == "/healthz" else self.send(405)

    def do_DELETE(self):
        SESSIONS.discard(self.headers.get("Mcp-Session-Id"))
        self.send(200)

    def do_POST(self):
        message = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        with LOCK:
            good = self.headers.get("Authorization") == f"Bearer {EXPECTED}"
            STATE["authorized" if good else "unauthorized"] += 1
            save()
        method = message.get("method")
        if method == "initialize":
            session = secrets.token_hex(8)
            SESSIONS.add(session)
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": True}, "sampling": {}},
                "serverInfo": {"name": "mock", "version": "1"},
            }
            body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result})
            return self.send(200, body.encode(), headers={"Mcp-Session-Id": session})
        if self.headers.get("Mcp-Session-Id") not in SESSIONS:
            return self.send(404, b"{}")
        if method.startswith("notifications/"):
            return self.send(202)
        if method == "tools/list":
            body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"tools": TOOLS}})
            return self.send(200, body.encode())
        name = message["params"]["name"]
        with LOCK:
            STATE["calls"][name] = STATE["calls"].get(name, 0) + 1
            save()
        events = [
            {"jsonrpc": "2.0", "id": "s1", "method": "sampling/createMessage", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"},
            {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}},
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"content": [{"type": "text", "text": f"ran {name}"}]},
            },
        ]
        body = b"".join(
            b"event: message\ndata: " + json.dumps(e).encode() + b"\n\n" for e in events
        )
        self.send(200, body, "text/event-stream")


save()
server = ThreadingHTTPServer(("0.0.0.0", 9000), Handler)
server.daemon_threads = True
server.serve_forever()
