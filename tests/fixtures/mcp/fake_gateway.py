"""A fake OpenAI-compatible LLM gateway for the opt-in ADK-through-relay Docker test
(tests/test_adk_mcp_docker.py). Standard library only.

The first chat completion of a conversation asks for the `mock_list_issues` tool; once the request
carries a tool result, it answers with that result's text. Every request's offered tool names
are appended to /tmp/requests.jsonl, so the test can check what the agent declared.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOL = "mock_list_issues"


def completion(model, message, finish):
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


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
        self.send(200, b"gw\n", "text/html") if self.path == "/index.html" else self.send(404, b"")

    def do_POST(self):
        if not self.path.endswith("/chat/completions"):
            return self.send(404, b"{}")
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        offered = [t.get("function", {}).get("name") for t in body.get("tools") or []]
        results = [m for m in body.get("messages", []) if m.get("role") == "tool"]
        with open("/tmp/requests.jsonl", "a") as handle:
            handle.write(json.dumps({"tools": offered, "tool_results": len(results)}) + "\n")
        model = body.get("model", "m1")
        if results:
            content = results[-1].get("content")
            text = content if isinstance(content, str) else json.dumps(content)
            reply = completion(model, {"role": "assistant", "content": f"Result: {text}"}, "stop")
        elif TOOL in offered:
            call = {
                "id": "call_1",
                "type": "function",
                "function": {"name": TOOL, "arguments": json.dumps({"q": "open"})},
            }
            message = {"role": "assistant", "content": None, "tool_calls": [call]}
            reply = completion(model, message, "tool_calls")
        else:
            reply = completion(model, {"role": "assistant", "content": "No tools."}, "stop")
        self.send(200, json.dumps(reply).encode())


server = ThreadingHTTPServer(("0.0.0.0", 4000), Handler)
server.daemon_threads = True
server.serve_forever()
