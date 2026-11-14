"""The eval report viewer's reads (#932, console half): `evals list|show --json` with fixed argv.

Only the run id (checked by the route), `agent` (the agent name rule) and `content=1` come from
the request. The project path and the stack's state directory (for redaction) come from the
registered project, never from the request. Reads are never cached: a report read is always
fresh, and a failed read shows its own coded error, not an older report. The CLI redacts every
string and caps it (`docs/evals.md`); the answer is checked against its shape here and passed on,
never stored.
"""

from __future__ import annotations

import re

# Copies of AGENT and RUN (pinned by tests/test_center_evals.py): the
# console process loads only allowlisted modules, and eval_reports would bring safe_text with it.
AGENT = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,51}[a-z0-9])?")
RUN = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")
TIMEOUT = 60  # `evals show` reads at most 4 MiB and redacts at most 20000 strings
SCHEMA_VERSION = 1


class Invalid(ValueError):
    """A request value outside its rule: `code` is the error code the browser sees."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _agent(query: dict[str, list[str]]) -> list[str]:
    name = query.get("agent", [""])[0]
    if not name:
        return []
    if not AGENT.fullmatch(name):
        raise Invalid("The agent must be an agent name", "AGENT_INVALID")
    return ["--agent", name]


def list_argv(project: dict, state: str, query: dict[str, list[str]]) -> list[str]:
    """`evals list` over the project's default report folder (`DIR/deploy`)."""
    return [
        "evals", "list", "--project", project["path"], "--state-dir", state, *_agent(query),
        "--json",
    ]  # fmt: skip


def show_argv(project: dict, state: str, run: str, query: dict[str, list[str]]) -> list[str]:
    """`evals show RUN`; `content=1` adds the redacted, capped reply and explanation."""
    if not RUN.fullmatch(run):
        raise Invalid("The run id must look like 20261113-213200-1a2b3c", "RUN_INVALID")
    content = query.get("content", [""])[0] == "1"
    return [
        "evals", "show", run, "--project", project["path"], "--state-dir", state,
        *_agent(query), *(["--content"] if content else []), "--json",
    ]  # fmt: skip


def list_answer(data) -> bool:
    """Whether `evals list --json` matches its contract's shape."""
    return (
        isinstance(data, dict)
        and data.get("schema_version") == SCHEMA_VERSION
        and isinstance(data.get("reports"), list)
        and all(isinstance(row, dict) for row in data["reports"])
    )


def show_answer(data, run: str) -> bool:
    """Whether `evals show --json` matches its contract's shape and answers `run`."""
    return (
        isinstance(data, dict)
        and data.get("schema_version") == SCHEMA_VERSION
        and data.get("run_id") == run
        and isinstance(data.get("agent"), str)
        and AGENT.fullmatch(data["agent"]) is not None
        and isinstance(data.get("file"), str)
        and isinstance(data.get("results"), list)
        and all(isinstance(row, dict) for row in data["results"])
        and isinstance(data.get("notes", []), list)
    )
