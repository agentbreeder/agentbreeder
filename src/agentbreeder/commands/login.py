"""`agentbreeder login`: sign in with the operator's OIDC device flow."""

from __future__ import annotations

import sys

from agentbreeder import cli
from agentbreeder.deployer import DeployerError, device_login
from agentbreeder.operator_config import OperatorConfigError, load_operator_config

NAMES = ("login",)


def register(commands) -> None:
    login = commands.add_parser("login", help="Sign in with the operator's OIDC device flow")
    login.add_argument("--issuer")


def run(args) -> int:
    try:
        deployer = device_login(load_operator_config(), issuer=args.issuer)
    except (DeployerError, OperatorConfigError) as error:
        print(cli._message(error), file=sys.stderr)
        return 2
    print(f"Signed in as {deployer.label}")
    return 0
