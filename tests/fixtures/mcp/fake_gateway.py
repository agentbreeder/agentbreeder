"""A fake OpenAI-compatible LLM gateway for the opt-in MCP Docker tests (tests/
test_adk_mcp_docker.py, tests/test_mcp_builtins_live.py). Standard library only.

It follows a script of tool calls: `/files/script.json` when present (a list of
`{"tool": ..., "arguments": {...}}`), else one `mock_list_issues` call. Each chat completion asks
for the next scripted tool once the request carries as many tool results as calls already made;
after the last one it answers with every tool result's text. Every request's offered tool names
are appended to /tmp/requests.jsonl, so a test can check what the agent declared.

It also answers the LiteLLM key routes `deploy` uses (/key/generate, /key/delete, /key/list), so
a real `agentbreeder deploy` can mint and revoke its per-deployment key against it.
"""

import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT = "/files/script.json"
DEFAULT = [{"tool": "mock_list_issues", "arguments": {"q": "open"}}]


def script():
    if os.path.exists(SCRIPT):
        with open(SCRIPT) as handle:
            return json.load(handle)
    return DEFAULT


def completion(model, message, finish):
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def text_of(message):
    content = message.get("content")
    return content if isinstance(content, str) else json.dumps(content)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send(self, status, body: bytes, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/index.html":
            return self.send(200, b"gw\n", "text/html")
        if self.path.startswith("/key/list"):
            return self.send(200, json.dumps({"keys": [], "total_pages": 1}).encode())
        self.send(404, b"")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        if self.path == "/key/generate":
            return self.send(200, json.dumps({"key": "sk-" + secrets.token_hex(16)}).encode())
        if self.path == "/key/delete":
            return self.send(200, b"{}")
        if not self.path.endswith("/chat/completions"):
            return self.send(404, b"{}")
        offered = [t.get("function", {}).get("name") for t in body.get("tools") or []]
        results = [m for m in body.get("messages", []) if m.get("role") == "tool"]
        with open("/tmp/requests.jsonl", "a") as handle:
            handle.write(json.dumps({"tools": offered, "tool_results": len(results)}) + "\n")
        model = body.get("model", "m1")
        steps = script()
        step = len(results)
        if step < len(steps) and steps[step]["tool"] in offered:
            call = {
                "id": f"call_{step + 1}",
                "type": "function",
                "function": {
                    "name": steps[step]["tool"],
                    "arguments": json.dumps(steps[step].get("arguments", {})),
                },
            }
            message = {"role": "assistant", "content": None, "tool_calls": [call]}
            reply = completion(model, message, "tool_calls")
        elif results:
            text = "\n".join(text_of(m) for m in results)
            reply = completion(model, {"role": "assistant", "content": f"Result: {text}"}, "stop")
        else:
            reply = completion(model, {"role": "assistant", "content": "No tools."}, "stop")
        self.send(200, json.dumps(reply).encode())


server = ThreadingHTTPServer(("0.0.0.0", 4000), Handler)
server.daemon_threads = True
server.serve_forever()
