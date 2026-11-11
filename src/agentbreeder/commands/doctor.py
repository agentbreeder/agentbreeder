"""`agentbreeder doctor`: check local Docker prerequisites without changing anything."""

from __future__ import annotations

import json
from dataclasses import asdict

from agentbreeder.doctor import check_compose, check_docker, check_legacy

NAMES = ("doctor",)


def register(commands) -> None:
    doctor = commands.add_parser("doctor", help="Check local Docker prerequisites without changes")
    doctor.add_argument("--json", action="store_true")


def run(args) -> int:
    checks = check_docker() + check_compose() + check_legacy()
    if args.json:
        print(json.dumps([asdict(check) for check in checks], indent=2))
    else:
        for check in checks:
            mark = (
                "INFO"
                if check.info
                else "PASS"
                if check.passed
                else "FAIL"
                if check.required
                else "WARN"
            )
            print(f"{mark} {check.name}: {check.message}")
    return 0 if all(check.passed or not check.required for check in checks) else 1
