"""`agentbreeder forward`: open a loopback port to a Kubernetes deployment."""

from __future__ import annotations

import time

from agentbreeder import cli
from agentbreeder.deployment import DeploymentService
from agentbreeder.store import DeploymentError

NAMES = ("forward",)


def register(commands) -> None:
    forward = cli.stateful_parser(
        commands, "forward", "Open a loopback port to a Kubernetes deployment", "id"
    )
    forward.add_argument("--port", type=int, default=0, help="Local port (default: any free)")
    cli.add_stack(forward, environment=True)


def run(args) -> int:
    return cli.run_stateful(args, lambda service, operator: _forward(service, args), existing=True)


def _forward(service: DeploymentService, args) -> int:
    record = service.store.get(args.id)
    if service.store.manifest(args.id).get("target") != "kubernetes" or record["state"] != "ready":
        raise DeploymentError("forward needs a ready Kubernetes deployment")
    service.kubernetes.authorize(record["context"])
    with service.kubernetes.forward(record["endpoint"], args.port) as port:
        print(f"http://127.0.0.1:{port}  (unauthenticated; Ctrl-C to stop)", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
