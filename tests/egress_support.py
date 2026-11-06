"""Shared helpers for the phase-4 egress tests: a TLS MCP upstream and operator wiring."""

from __future__ import annotations

import datetime
import ipaddress
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from conftest import enable_cedar, write_private

TOOL_TOKEN = "tool-secret-value"
CALL_POLICY = """
permit(principal, action == AgentBreeder::Action::"call_tool", resource)
when { resource.name == "lookup" || resource.name == "refund" };
permit(principal, action == AgentBreeder::Action::"RequireCallApproval", resource)
when { resource.name == "refund" };
"""


def make_certificate(directory: Path, names=("localhost", "host.docker.internal")):
    """A self-signed server certificate; returns (cert_path, key_path, pem_text)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
    now = datetime.datetime.now(datetime.UTC)
    alternative = [x509.DNSName(n) for n in names] + [
        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))
    ]
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName(alternative), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = certificate.public_bytes(serialization.Encoding.PEM)
    cert_path = write_private(directory / "upstream.pem", pem.decode())
    key_path = directory / "upstream.key"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return cert_path, key_path, pem.decode()


class Upstream:
    """An HTTPS MCP server stand-in that records what it received."""

    def __init__(self, directory: Path, host: str = "127.0.0.1", redirect: bool = False):
        self.requests: list[dict] = []
        cert, key, self.pem = make_certificate(directory)
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                upstream.requests.append({"headers": dict(self.headers), "body": body})
                if redirect:
                    self.send_response(302)
                    self.send_header("Location", "https://evil.example.com/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                params = body.get("params") or {}
                result = {
                    "jsonrpc": "2.0",
                    "id": body.get("id"),
                    "result": {
                        "authorized": self.headers.get("Authorization") == f"Bearer {TOOL_TOKEN}",
                        "tool": params.get("name"),
                    },
                }
                data = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Mcp-Session-Id", "session-1")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer((host, 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def enable_boundary(env, url: str, pem: str, monkeypatch, extra_policy: str = CALL_POLICY):
    """Cedar PDP with deploy + call_tool policy and one approved destination."""
    from conftest import BASELINE_POLICY

    enable_cedar(env, BASELINE_POLICY + extra_policy)
    write_private(env.directory / "ca.pem", pem)
    monkeypatch.setenv("AB_TEST_TOOL_TOKEN", TOOL_TOKEN)
    env.config["runtime_enforcement"] = {
        "mode": "mcp_http",
        "destinations": {
            "tools": {
                "url": url,
                "credential": "env:AB_TEST_TOOL_TOKEN",
                "allow_private": True,
                "ca_file": "ca.pem",
            }
        },
    }


def tool_capabilities(url: str) -> dict:
    return {"tools": [{"mcp": url, "actions": ["lookup", "refund", "delete"]}]}
