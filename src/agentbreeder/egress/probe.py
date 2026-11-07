"""Active boundary probes, run in a short-lived container that joins the agent's network namespace.

Prints one JSON object. The host decides pass/fail; this script only reports what it observed.
Standard library only.
"""

from __future__ import annotations

import json
import socket
import sys
import time
import urllib.request


def probe(plan: dict, timeout: float = 3.0) -> dict:
    tcp_open = []
    for host, port in plan.get("tcp", []):
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as connection:
                connection.settimeout(timeout)
                connection.connect((host, int(port)))
                tcp_open.append([host, port])
        except OSError:
            pass
    resolved = []
    for name in plan.get("dns_must_fail", []):
        try:
            socket.getaddrinfo(name, 443)
            resolved.append(name)
        except OSError:
            pass
    gateway = None
    if plan.get("gateway"):
        # A just-(re)started sidecar may need a moment to listen; only the gateway is retried.
        deadline = time.monotonic() + float(plan.get("gateway_wait", 15))
        while True:
            try:
                with urllib.request.urlopen(plan["gateway"], timeout=10) as response:  # noqa: S310
                    body = json.loads(response.read(65536))
                    gateway = {"status": response.status, "body": body}
                    break
            except Exception as error:  # report, never raise: the host fails closed on absence
                gateway = {"status": None, "error": type(error).__name__}
            if time.monotonic() >= deadline:
                break
            time.sleep(0.5)
    return {"tcp_open": tcp_open, "dns_resolved": resolved, "gateway": gateway}


def main() -> None:  # pragma: no cover - container entry point
    print(json.dumps(probe(json.loads(sys.argv[1]))))


if __name__ == "__main__":  # pragma: no cover
    main()
