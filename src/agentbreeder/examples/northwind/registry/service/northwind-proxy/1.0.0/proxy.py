"""Key proxy: keeps the Tavily and Phoenix keys out of the agent image.

The agent never holds these keys: it calls this proxy (the stack's `northwind-proxy` service,
reachable from agent containers only) and the proxy adds them, so a compromised agent can use
Tavily but cannot take the key elsewhere. It forwards only POST /tavily/search and POST
/v1/traces (to Phoenix). Model calls do NOT go through here: the agent uses the LiteLLM gateway
with a budget-capped virtual key, so there is no uncapped model route.

Keys come from the files AgentBreeder mounts under /run/secrets (named by <NAME>_FILE).
"""

from __future__ import annotations

import os
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TAVILY = "https://api.tavily.com/search"
MAX_BODY = 8 * 1024 * 1024
# Agents address this proxy by its stack service name, which AgentBreeder sets.
ALLOWED_HOSTS = {"127.0.0.1", "localhost", os.environ.get("AGENTBREEDER_SERVICE", "proxy")}
TLS = ssl.create_default_context()  # the image's CA store


def load_key(wanted: str) -> str:
    try:
        return Path(os.environ[wanted + "_FILE"]).read_text().strip()
    except (KeyError, OSError):
        sys.exit(f"No readable {wanted}_FILE")


TAVILY_KEY = load_key("TAVILY_API_KEY")
# Agent traces (OTLP/HTTP) go to the local Phoenix with its system API key added here.
PHOENIX = os.getenv("PHOENIX_URL", "http://127.0.0.1:6006")
PHOENIX_KEY_FILE = Path(os.getenv("PHOENIX_API_KEY_FILE", "/run/secrets/phoenix-ingest-key"))


def phoenix_key() -> str:
    """Read per request: a new Phoenix database mints a new key, mounted on restart."""
    try:
        return PHOENIX_KEY_FILE.read_text().strip()
    except OSError:
        return ""


class Proxy(BaseHTTPRequestHandler):
    def reply(self, status: int, body: bytes, kind: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        # Browsers can send simple cross-site POSTs to loopback; require JSON (a preflighted
        # content type for cross-origin pages) and a known Host, so a web page cannot
        # spend the operator's Tavily credits through this proxy.
        host = (self.headers.get("Host") or "").split(":")[0]
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if host not in ALLOWED_HOSTS or (
            self.path == "/tavily/search" and content_type != "application/json"
        ):
            self.reply(403, b'{"error":"forbidden"}')
            return
        tavily = self.path == "/tavily/search"
        traces = self.path == "/v1/traces" and bool(phoenix_key())
        if not (tavily or traces):
            self.reply(404, b'{"error":"only /tavily/search and /v1/traces"}')
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = -1
        if not 0 < size <= MAX_BODY:
            self.reply(400, b'{"error":"bad body size"}')
            return
        body = self.rfile.read(size)
        if traces:
            url = PHOENIX + "/v1/traces"
            headers = {
                "content-type": self.headers.get("Content-Type", "application/x-protobuf"),
                "authorization": "Bearer " + phoenix_key(),
            }
        else:
            url = TAVILY
            headers = {"content-type": "application/json", "authorization": "Bearer " + TAVILY_KEY}
        try:
            request = Request(url, data=body, headers=headers)
            with urlopen(request, timeout=300, context=TLS) as response:
                self.reply(response.status, response.read())
        except HTTPError as error:
            self.reply(error.code, error.read())
        except (URLError, TimeoutError) as error:
            self.reply(
                502, ('{"error":"upstream unreachable: %s"}' % type(error).__name__).encode()
            )

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self.reply(200, b'{"status":"ok"}')
        else:
            self.reply(404, b'{"error":"not found"}')

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write("proxy: %s %s\n" % (self.command, args[1] if len(args) > 1 else ""))


if __name__ == "__main__":
    host, port = os.getenv("PROXY_HOST", "127.0.0.1"), int(os.getenv("PROXY_PORT", "8787"))
    print(f"Key proxy on http://{host}:{port} (keys loaded, not printed)", flush=True)
    ThreadingHTTPServer((host, port), Proxy).serve_forever()
