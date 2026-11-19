"""The forwarding sidecar of a kit deployment (#1083 F1b). Standard library only.

A deployment whose manifest says `runtime.profile: kit` runs on internal networks only, so it
has no route off the host and cannot publish a port. This sidecar gives it the two paths it
still needs, and nothing else:

* ingress: a port published on host loopback, forwarded to the agent's HTTP port;
* the model gateway, when the operator's gateway is on the host (`host.docker.internal`),
  which an internal network cannot reach.

Each forward is a fixed listen port to one fixed `host:port`, set at deploy time from the
operator config. Bytes are copied unchanged in both directions: there is no proxy protocol, no
`CONNECT` and no destination the agent can choose. `GET /healthz` on the health port reports the
configuration, which deploy compares with what it created.

Configuration arrives in `AGENTBREEDER_FORWARDER_CONFIG` (JSON). It holds no secret.
"""

from __future__ import annotations

import json
import os
import select
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONNECT_TIMEOUT = 10
# A model turn can leave a connection silent for minutes; an abandoned one must still end.
IDLE_TIMEOUT = 900
MAX_CONNECTIONS = 48  # per forward; the sidecar runs under a 128-pid limit
CHUNK = 65536
HEALTH_TIMEOUT = 5
HEALTH_CONNECTIONS = 8


class HealthServer(ThreadingHTTPServer):
    """The health endpoint, capped: it must never use the threads the forwards need."""

    daemon_threads = True

    def __init__(self, address, handler, limit: int = HEALTH_CONNECTIONS):
        super().__init__(address, handler)
        self.slots = threading.BoundedSemaphore(limit)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except RuntimeError:
            self.slots.release()
            self.shutdown_request(request)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def pump(client: socket.socket, upstream: socket.socket, idle: float = IDLE_TIMEOUT) -> None:
    """Copy bytes both ways until both sides have closed, an error, or `idle` seconds of
    silence. A half-close is passed on, so a request whose sender shuts down still gets its
    reply."""
    peer = {client: upstream, upstream: client}
    reading = {client, upstream}
    try:
        for connection in peer:
            connection.settimeout(idle)  # bounds a send to a peer that stopped reading
    except OSError:
        return
    while reading:
        try:
            readable, _, _ = select.select(list(reading), [], [], idle)
        except (OSError, ValueError):
            return
        if not readable:
            return
        for source in readable:
            try:
                data = source.recv(CHUNK)
            except OSError:
                return
            if not data:
                reading.discard(source)
                try:
                    peer[source].shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                continue
            try:
                peer[source].sendall(data)
            except OSError:
                return


class Forward:
    """One listening port copied to one fixed destination."""

    def __init__(
        self,
        listen: int,
        host: str,
        port: int,
        *,
        bind: str = "0.0.0.0",  # noqa: S104 - the sidecar's own namespace; see the module text
        idle: float = IDLE_TIMEOUT,
        limit: int = MAX_CONNECTIONS,
    ):
        self.host, self.port, self.idle = host, int(port), idle
        self.slots = threading.BoundedSemaphore(limit)
        self.server = socket.create_server((bind, int(listen)), backlog=64)
        self.listen = self.server.getsockname()[1]
        self.closed = False

    def start(self) -> Forward:
        threading.Thread(target=self.serve, daemon=True).start()
        return self

    def serve(self) -> None:
        while not self.closed:
            try:
                client, _ = self.server.accept()
            except OSError:
                if self.closed:
                    return
                time.sleep(0.05)  # a transient failure (aborted connection, no descriptors)
                continue
            if not self.slots.acquire(blocking=False):
                client.close()  # over the cap: refuse, never queue without bound
                continue
            try:
                threading.Thread(target=self.handle, args=(client,), daemon=True).start()
            except RuntimeError:  # no thread left under the pid limit: refuse this one
                self.slots.release()
                client.close()

    def handle(self, client: socket.socket) -> None:
        upstream = None
        try:
            upstream = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
            pump(client, upstream, self.idle)
        except OSError:
            pass  # the destination is down or gone: the caller sees a closed connection
        finally:
            for connection in (client, upstream):
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass
            self.slots.release()

    def close(self) -> None:
        self.closed = True
        try:
            self.server.close()
        except OSError:
            pass


def parse_forwards(config: dict) -> list[tuple[int, str, int]]:
    """The configured forwards, validated: a malformed entry stops the sidecar at start."""
    result = []
    for entry in config.get("forwards") or []:
        listen, host, port = entry["listen"], entry["host"], entry["port"]
        if not (isinstance(host, str) and host and len(host) <= 253):
            raise ValueError("forward host")
        for number in (listen, port):
            if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 65535:
                raise ValueError("forward port")
        result.append((listen, host, port))
    if len({listen for listen, _, _ in result}) != len(result):
        raise ValueError("duplicate listen port")
    return result


def health(config: dict) -> dict:
    return {
        "deployment": config.get("deployment"),
        "code_digest": config.get("code_digest"),
        "forwards": [list(forward) for forward in parse_forwards(config)],
    }


def health_handler(config: dict):
    body = json.dumps(health(config)).encode()

    class Health(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "agentbreeder-forwarder"
        sys_version = ""
        timeout = HEALTH_TIMEOUT  # an idle connection cannot hold a thread

        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            sys.stderr.write(f"health {self.command}\n")

        def do_GET(self):  # noqa: N802 - stdlib naming
            found = self.path == "/healthz"
            payload = body if found else b'{"error":"NOT_FOUND"}'
            self.send_response(200 if found else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

    return Health


def serve(config: dict) -> None:  # pragma: no cover - exercised in Docker
    for listen, host, port in parse_forwards(config):
        Forward(listen, host, port).start()
    server = HealthServer(("0.0.0.0", int(config["health_port"])), health_handler(config))  # noqa: S104
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        time.sleep(3600)


def main() -> None:  # pragma: no cover - container entry point
    serve(json.loads(os.environ["AGENTBREEDER_FORWARDER_CONFIG"]))


if __name__ == "__main__":  # pragma: no cover
    main()
