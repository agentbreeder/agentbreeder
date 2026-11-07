import importlib.util
import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest


@pytest.fixture
def server():
    path = Path(__file__).parents[1] / "examples/http-agent/server.py"
    spec = importlib.util.spec_from_file_location("sample", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    service = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    worker = threading.Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{service.server_port}"
    finally:
        service.shutdown()
        service.server_close()
        worker.join(timeout=5)


def test_health(server):
    with urllib.request.urlopen(server + "/health", timeout=3) as response:
        assert json.load(response) == {"status": "ok", "sample": True}


def test_real_echo(server):
    request = urllib.request.Request(server + "/invoke", data=b'{"message":"hello"}')
    with urllib.request.urlopen(request, timeout=3) as response:
        body = json.load(response)
        assert body["input"] == {"message": "hello"}
        assert body["sample"] is True
        assert "no model" in body["description"]


@pytest.mark.parametrize(
    "data,code",
    [(b"invalid", 400), (b"x" * 65537, 413), (b"[" * 2000 + b"]" * 2000, 400)],
    ids=["invalid-json", "oversized", "excessive-nesting"],
)
def test_reject_bad_payload(server, data, code):
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(urllib.request.Request(server + "/invoke", data=data), timeout=3)
    assert error.value.code == code


def test_missing_endpoint(server):
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(server + "/missing", timeout=3)
    assert error.value.code == 404


def test_truncated_body(server):
    address = urlparse(server)
    with socket.create_connection((address.hostname, address.port), timeout=3) as client:
        client.sendall(b"POST /invoke HTTP/1.0\r\nContent-Length: 10\r\n\r\n{}")
        client.shutdown(socket.SHUT_WR)
        response = client.recv(4096)
    assert response.startswith(b"HTTP/1.0 400 ")


def test_runtime_health_uses_real_http(server):
    from agentbreeder.runtime import Docker

    assert Docker().healthy(server, "/health", timeout=1)
    assert not Docker().healthy(server, "/missing", timeout=0.1)


def test_health_trickled_headers_cannot_exceed_deadline():
    import time

    from agentbreeder.runtime import Docker

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)

        def trickle():
            connection, _ = listener.accept()
            with connection:
                connection.recv(2048)
                try:
                    for byte in b"HTTP/1.1 200 OK\r\nX-Slow: too-slow\r\n\r\n":
                        connection.sendall(bytes([byte]))
                        time.sleep(0.02)
                except OSError:
                    pass

        worker = threading.Thread(target=trickle, daemon=True)
        worker.start()
        start = time.monotonic()
        assert not Docker().healthy(
            f"http://127.0.0.1:{listener.getsockname()[1]}", "/", timeout=0.15
        )
        assert time.monotonic() - start < 0.6
        worker.join(timeout=2)


def test_health_redirect_is_not_followed():
    from http.server import BaseHTTPRequestHandler

    from agentbreeder.runtime import Docker

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/health")
            self.end_headers()

        def log_message(self, *args):
            pass

    service = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    worker = threading.Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        assert not Docker().healthy(f"http://127.0.0.1:{service.server_port}", "/", timeout=0.1)
    finally:
        service.shutdown()
        service.server_close()
        worker.join(timeout=2)
