"""Real deployment-test service. Not an AI agent or an authenticated production service."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 64 * 1024


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def respond(self, status: int, body: dict) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.respond(200, {"status": "ok", "sample": True})
        else:
            self.respond(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/invoke":
            self.respond(404, {"error": "Not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.respond(400, {"error": "Invalid Content-Length"})
            return
        if not 0 < size <= MAX_BODY:
            self.respond(413, {"error": "Body must contain 1–65536 bytes"})
            return
        if self.headers.get("Transfer-Encoding"):
            self.respond(400, {"error": "Transfer-Encoding is not supported"})
            return
        try:
            raw = self.rfile.read(size)
            if len(raw) != size:
                self.respond(400, {"error": "A complete JSON body is required"})
                return
            payload = json.loads(raw)
            pending = [(payload, 0)]
            while pending:
                value, depth = pending.pop()
                if depth > 64:
                    raise ValueError("JSON nesting exceeds the sample limit")
                if isinstance(value, dict):
                    pending.extend((child, depth + 1) for child in value.values())
                elif isinstance(value, list):
                    pending.extend((child, depth + 1) for child in value)
        except (ValueError, UnicodeError, TimeoutError, RecursionError):
            self.respond(400, {"error": "A complete JSON body is required"})
            return
        self.respond(
            200,
            {
                "sample": True,
                "description": "Deployment demonstration; no model called",
                "input": payload,
            },
        )

    def log_message(self, format: str, *args) -> None:
        # No request URLs/bodies are logged by this minimal sample.
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(
        (os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "8080"))), Handler
    )
    server.serve_forever()
