"""`agentbreeder eval`: run an agent's eval set against a running endpoint."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentbreeder import agentfile, cli, registry, secret_files
from agentbreeder.operator_config import OperatorConfigError, load_operator_config

NAMES = ("eval",)


def register(commands) -> None:
    ev = commands.add_parser("eval", help="Run an agent's eval set against a running endpoint")
    ev.add_argument("agent")
    ev.add_argument("--endpoint", required=True)
    ev.add_argument("--environment")
    ev.add_argument("--only", help="Comma-separated case IDs")
    ev.add_argument("--retries", type=int, default=None)
    ev.add_argument("--state-dir", type=Path, default=cli.DEFAULT_STATE)
    ev.add_argument("--json", action="store_true")
    cli.add_stack(ev)


def run(args) -> int:
    from agentbreeder import evals
    from agentbreeder.release import ReleaseError, _eval_set

    try:
        operator = load_operator_config()
        agent = agentfile.load_agent_file(Path(args.agent))
        environment = args.environment or operator.model.default_environment
        eval_set = _eval_set(agent, Path(args.agent).resolve().parent)
        report = evals.run(
            eval_set,
            args.endpoint,
            operator,
            environment,
            args.state_dir,
            retries=agent.evals.retries if args.retries is None else args.retries,
            only=set(args.only.split(",")) if args.only else None,
        )
    except (
        evals.EvalError,
        ReleaseError,
        agentfile.AgentFileError,
        registry.RegistryError,
        OperatorConfigError,
        secret_files.SecretError,
    ) as error:
        print(cli._message(error, args=args, near=Path(args.agent)), file=sys.stderr)
        return 2
    summary = report.summary()
    if args.json:
        print(json.dumps({**summary, "results": report.results}, indent=2))
    else:
        for result in report.results:
            mark = (
                "PASS"
                if result["passed"] and result["attempts"] == 1
                else ("FLKY" if result["passed"] else "FAIL")
            )
            print(f"{mark} {result['case_id']} ({result['correctness']:.2f})")
        print(
            f"{summary['passed']}/{summary['cases']} passed, mean correctness "
            f"{summary['mean_correctness']}, p50 {summary['p50_latency_ms']} ms"
        )
    return 0 if report.pass_rate >= agent.evals.threshold else 5
