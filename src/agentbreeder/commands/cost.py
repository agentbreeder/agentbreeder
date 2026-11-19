"""`agentbreeder cost [--stack DIR [--environment ENV]] [--since DAY] [--until DAY]
[--by deployment|model|subagent] [--json]`: LLM cost per deployment key and per model, and
inside a key per sub-agent (#1058), from the environment's LiteLLM gateway spend logs (#906; see
`llmcost`).

Read-only: the deployment state is opened read-only (a missing state is not an error: every
key is then `unknown`), and the gateway is only read, with its master key from the operator
config. Errors are `[CODE] message` on stderr (with `--json` also the one-line
`{"schema_version": 1, "error": {...}}` on stdout), exit 2.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from agentbreeder import cli, gateway_keys, llmcost
from agentbreeder.operator_config import OperatorConfigError, load_operator_config
from agentbreeder.safe_text import excerpt
from agentbreeder.store import DeploymentError

NAMES = ("cost",)
NOTE = (
    "USD as LiteLLM computed it from its model price list: an estimate, not a bill. "
    "LiteLLM writes spend logs in batches, so calls from about the last minute may be missing."
)


UNTAGGED = "(not tagged)"
TAG_NOTE = (
    "AGENT is the request tag the agent's runtime sent with each model call "
    "(agentbreeder-subagent:<name>); the gateway does not check it, so it splits a key's spend "
    f"and never moves spend between keys. {UNTAGGED}: calls with no such tag."
)


def register(commands) -> None:
    command = cli.stateful_parser(
        commands,
        "cost",
        "Show LLM cost per deployment or per model from the gateway's spend logs (read-only)",
    )
    cli.add_stack(command, environment=True)
    command.add_argument(
        "--since",
        metavar="DAY",
        help="First UTC day, YYYY-MM-DD (default: 29 days before --until)",
    )
    command.add_argument(
        "--until", metavar="DAY", help="Last UTC day, YYYY-MM-DD (default: today)"
    )
    command.add_argument(
        "--by",
        choices=("deployment", "model", "subagent"),
        default="deployment",
        help="Group the table by deployment key (default), by model, or by sub-agent inside "
        "each key (from request tags); --json has all three",
    )


def run(args) -> int:
    try:
        result = _report(args)
    except (
        llmcost.CostError,
        gateway_keys.GatewayError,
        OperatorConfigError,
        DeploymentError,
    ) as error:
        return _fail(args, getattr(error, "code", None) or "COST_ERROR", error)
    except (OSError, sqlite3.Error):
        return _fail(args, "STATE_UNREADABLE", "Cannot read local state; check permissions")
    except (UnicodeError, RecursionError, OverflowError, ValueError):
        # A malformed credential file or gateway answer: a coded error, never a traceback.
        return _fail(args, "COST_ERROR", "the gateway answer or a credential could not be read")
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print(result, args.by)
    if result["truncated"]:
        print(
            "WARNING: LiteLLM returned more spend log rows than are read; the totals are partial. "
            "Use a shorter --since/--until window.",
            file=sys.stderr,
        )
    return 0


def _fail(args, code: str, error) -> int:
    text = str(error)
    message = text.removeprefix(f"[{code}] ")
    if code == "GATEWAY_UNREACHABLE":
        message += "; is the stack running? (`agentbreeder services status`)"
    print(f"[{code}] {message}", file=sys.stderr)
    if args.json:
        print(json.dumps({"schema_version": 1, "error": {"code": code, "message": message}}))
    return 2


def _deployments(state_dir: Path) -> list[dict]:
    from agentbreeder.store import Store

    if not (state_dir / "deployments.sqlite3").exists():
        return []
    store = Store.open_readonly(state_dir)
    try:
        return store.summaries()
    finally:
        store.close()


def _report(args) -> dict:
    start, end = llmcost.window(args.since, args.until)
    operator = load_operator_config()
    name = args.environment or operator.model.default_environment
    environment = operator.model.environments.get(name) if name else None
    if environment is None:
        raise llmcost.CostError(
            "COST_NO_GATEWAY",
            "no environment selected: pass --stack DIR (and --environment), or set "
            "default_environment in the operator config",
        )
    if environment.gateway is None:
        raise llmcost.CostError(
            "COST_NO_GATEWAY", f"environment {excerpt(name, 80)} has no model gateway"
        )
    client = gateway_keys.client(operator, environment.gateway)
    if client is None:
        raise llmcost.CostError(
            "COST_NO_GATEWAY",
            f"gateway {excerpt(environment.gateway, 80)} has no master_key, so its spend logs "
            "can't be read",
        )
    return llmcost.report(client, _deployments(args.state_dir), start, end, environment.gateway)


def _usd(value: float) -> str:
    return f"{value:.6f}"


def _price(bucket: dict) -> str:
    """The USD cell: a bucket of local-model calls only has no price, not $0 (#1025 D11)."""
    if bucket["calls"] and bucket.get("local") == bucket["calls"]:
        return llmcost.LOCAL_LABEL
    return _usd(bucket["cost_usd"])


def _price_or_unknown(bucket: dict) -> str:
    """The USD cell of the sub-agent table: calls without a price are shown as unknown, never
    as $0 (#1058, sub-agents spec §10.2). A row whose other calls are all local has no priced
    call either, so it is `unknown` too; the local count is in the footer and in `--json`."""
    unpriced = bucket["unpriced"]
    if not unpriced:
        return _price(bucket)
    if unpriced + bucket.get("local", 0) >= bucket["calls"]:
        return "unknown"
    return f"{_usd(bucket['cost_usd'])} + {unpriced} unknown"


def _agent(row: dict) -> str:
    if row["role"] == "subagent":
        return row["name"]
    return {"parent": "(parent)", "other": "(other sub-agents)"}.get(row["role"], UNTAGGED)


def _tokens(bucket: dict) -> str:
    return f"{bucket['prompt_tokens']}/{bucket['completion_tokens']}"


def _cached(bucket: dict) -> str:
    """Cached prompt tokens (#1056), a part of the tokens in: as LiteLLM recorded them."""
    return str(bucket.get("cached_prompt_tokens", 0))


def _who(bucket: dict) -> tuple[str, str, str]:
    if bucket["kind"] == "deployment":
        return bucket["id"], bucket["name"] or "-", bucket["state"] or "-"
    if bucket["kind"] == "eval":
        return "eval run " + bucket["run"], "-", "-"
    return bucket["id"], "-", "not in this state"


def _by_subagent(result: dict) -> list[tuple]:
    rows = [
        ("DEPLOYMENT", "NAME", "AGENT", "CALLS", "FAILED", "TOKENS IN/OUT", "CACHED IN", "USD")
    ]
    for key in result["keys"]:
        who = _who(key)[:2]
        # A key with no tagged call is one row: all of it is untagged.
        for bucket in key.get("subagents") or [{**key, "role": "untagged", "name": None}]:
            rows.append(
                (
                    *who,
                    _agent(bucket),
                    str(bucket["calls"]),
                    str(bucket["failed"]),
                    _tokens(bucket),
                    _cached(bucket),
                    _price_or_unknown(bucket),
                )
            )
    return rows


def _print(result: dict, by: str) -> None:
    print(
        f"LLM cost {result['since']} to {result['until']} (UTC), "
        f"gateway {excerpt(result['gateway'], 80)}"
    )
    price = _price_or_unknown if by == "subagent" else _price
    if by == "subagent":
        rows = _by_subagent(result)
    elif by == "model":
        rows = [("MODEL ALIAS", "MODEL", "CALLS", "FAILED", "TOKENS IN/OUT", "CACHED IN", "USD")]
        for bucket in result["models"]:
            rows.append(
                (
                    bucket["model_group"] or "-",
                    bucket["model"] or "-",
                    str(bucket["calls"]),
                    str(bucket["failed"]),
                    _tokens(bucket),
                    _cached(bucket),
                    _price(bucket),
                )
            )
    else:
        rows = [
            ("DEPLOYMENT", "NAME", "STATE", "CALLS", "FAILED", "TOKENS IN/OUT", "CACHED IN", "USD")
        ]
        for bucket in result["keys"]:
            rows.append(
                (
                    *_who(bucket),
                    str(bucket["calls"]),
                    str(bucket["failed"]),
                    _tokens(bucket),
                    _cached(bucket),
                    _price(bucket),
                )
            )
    total = result["total"]
    if len(rows) == 1:
        print("No model calls by AgentBreeder keys in this window.")
    else:
        rows = [tuple(excerpt(cell, 60) for cell in row) for row in rows]
        blank = ("",) * (len(rows[0]) - 6)
        rows.append(
            ("Total", *blank, str(total["calls"]), str(total["failed"]))
            + (_tokens(total), _cached(total), price(total))
        )
        widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
        for row in rows:
            first = len(row) - 5  # calls, failed, tokens, cached and USD are right-aligned
            cells = (
                cell.rjust(width) if index >= first else cell.ljust(width)
                for index, (cell, width) in enumerate(zip(row, widths, strict=True))
            )
            print("  ".join(cells).rstrip())
    if total.get("local"):
        print(
            f"{total['local']} call(s) went to a local model (Ollama): {llmcost.LOCAL_LABEL}, "
            "not in the USD total."
        )
    if total["unpriced"]:
        print(
            f"{total['unpriced']} call(s) had no price from LiteLLM and are not in the USD total."
        )
    if by == "subagent" and any(key.get("subagents") for key in result["keys"]):
        print(TAG_NOTE)  # only when some call is tagged
    print(NOTE)
