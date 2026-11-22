"""`agentbreeder cluster up|status|down`: a local kind cluster for one stack (preview), and
`cluster connect token`: check an existing cluster for the `token` provider (#770 K1).

`cluster policy print` and `cluster rbac print` render the admission policies and the Roles a
cluster namespace needs (#770 §7, K2); they read nothing and change nothing."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentbreeder import cli, registry

NAMES = ("cluster",)


def register(commands) -> None:
    cluster = commands.add_parser("cluster", help="A local kind cluster for one stack (preview)")
    cluster_commands = cluster.add_subparsers(dest="cluster_command", required=True)
    for name, text in (
        ("up", "Create the cluster and registry, and add them to the stack's grant"),
        ("status", "Show the cluster, the registry and the tools they need"),
        ("down", "Delete the cluster and registry (refused while something is deployed there)"),
    ):
        item = cluster_commands.add_parser(name, help=text)
        item.add_argument("--stack", type=Path, default=Path("."), metavar="DIR")
        item.add_argument("--json", action="store_true")
        if name == "up":
            item.add_argument("--environment", required=True)
            item.add_argument("--yes", action="store_true", help="Confirm the changed grant")
            item.add_argument("--expect-grant", metavar="SHA256")
            item.add_argument(
                "--restricted",
                action="store_true",
                help="Deploy as restricted service accounts under the admission policies "
                "(preview, local kind only)",
            )
    connect = cluster_commands.add_parser(
        "connect", help="Check an existing cluster for AgentBreeder (preview; records nothing yet)"
    )
    providers = connect.add_subparsers(dest="connect_provider", required=True)
    token = providers.add_parser(
        "token",
        help="Any conformant cluster, through short-lived service account tokens",
        description=(
            "Check a cluster reached with short-lived service account tokens, minted with a "
            "dedicated mint kubeconfig. Until the admission policy check is wired in (#770) "
            "this runs every other check and then refuses, recording nothing."
        ),
    )
    token.add_argument("--server", required=True, metavar="URL", help="https://HOST[:PORT]")
    token.add_argument("--mint-kubeconfig", required=True, type=Path, metavar="FILE")
    token.add_argument("--registry", required=True, metavar="HOST/PATH")
    token.add_argument("--pod-cidr", required=True, action="append", metavar="CIDR")
    token.add_argument("--service-cidr", required=True, metavar="CIDR")
    token.add_argument("--node-cidr", action="append", metavar="CIDR")
    token.add_argument("--namespace", default="agentbreeder")
    token.add_argument("--environment", metavar="ENV")
    token.add_argument("--tls-server-name", metavar="NAME")
    token.add_argument("--token-minutes", type=int, default=30, metavar="10-60")
    token.add_argument("--kubectl", metavar="PATH", help="kubectl to pin (default: from PATH)")
    token.add_argument("--json", action="store_true")
    for name, text in (
        ("policy", "The admission policies a cluster namespace needs (printed, not installed)"),
        ("rbac", "The service accounts and Roles a cluster namespace needs (printed)"),
    ):
        group = cluster_commands.add_parser(name, help=text)
        actions = group.add_subparsers(dest=f"{name}_command", required=True)
        printer = actions.add_parser("print", help=f"Print the {name} objects as YAML")
        printer.add_argument("--namespace", required=True)


def run(args) -> int:
    from agentbreeder import cluster
    from agentbreeder.services import plan as stack_plan
    from agentbreeder.services.schema import ServiceError

    command = args.cluster_command
    if command in ("policy", "rbac"):
        return _print(command, args.namespace)
    log = (lambda message: None) if args.json else (lambda text: print(text, file=sys.stderr))
    if command == "connect":
        return _connect(args, log)
    try:
        if command == "up":

            def confirm(grant: dict) -> bool:
                print(stack_plan.grant_text(grant), file=sys.stderr)
                return True

            result = cluster.up(
                args.stack,
                args.environment,
                confirm=confirm if args.yes else None,
                expect_grant=args.expect_grant,
                restricted=args.restricted,
                log=log,
            )
        elif command == "status":
            result = cluster.status(args.stack)
        else:
            result = cluster.down(args.stack)
    except (ServiceError, registry.RegistryError) as error:
        print(cli._message(error), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


def _connect(args, log) -> int:
    from agentbreeder import cluster_token
    from agentbreeder.store import DeploymentError

    try:
        result = cluster_token.connect(
            server=args.server,
            mint_kubeconfig=args.mint_kubeconfig,
            registry=args.registry,
            pod_cidrs=args.pod_cidr,
            service_cidr=args.service_cidr,
            node_cidrs=args.node_cidr,
            namespace=args.namespace,
            tls_server_name=args.tls_server_name,
            token_minutes=args.token_minutes,
            kubectl_path=args.kubectl,
            log=log,
        )
    except DeploymentError as error:
        print(cli._message(error), file=sys.stderr)
        return 2
    print(json.dumps({"environment": args.environment, **result}, indent=2))
    return 0


def _print(what: str, namespace: str) -> int:
    import yaml

    from agentbreeder import kubernetes_admission as admission

    try:
        objects = admission.policies(namespace) if what == "policy" else admission.roles(namespace)
    except admission.AdmissionError as error:
        print(str(error), file=sys.stderr)
        return 2
    header = [f"# AgentBreeder {what} objects for namespace {namespace} (#770 §7)."]
    if what == "policy":
        header += [
            f"# Digest: {admission.digest(objects)}",
            "# Needs Kubernetes 1.30 or later and the ab-grants ConfigMap in the namespace.",
            "# The policy names are cluster-wide: one AgentBreeder namespace per cluster.",
        ]
    else:
        header.append("# The operator Role gains its Secret names when the chart lock has some.")
    print("\n".join(header))
    print(yaml.safe_dump_all(objects, sort_keys=False, width=100), end="")
    return 0
