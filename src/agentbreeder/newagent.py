"""`agentbreeder new agent NAME --model gemini`: write a starter project (#769, guided §9.2 #8).

The starter is based on the one walked by hand on 3.0.0a2
(`website/content/docs/your-own-agent.mdx`): a help-desk prompt and the two eval cases that were
run, a stack with Postgres and LiteLLM, and an `agent.yaml` on `runtime/adk@1.0.6`. Its prompt
and evals are published as `prompt/NAME-prompt@0.1.0` and `eval/NAME-evals@0.1.0` into the
project's own `registry/` (release refuses path prompts, and evals have no path form), and
`agent.lock` is written, so `build --locked` passes on the new project. `src/prompt/` and
`src/evals/` stay as the editable sources for the next version.

Only Gemini is offered (§9.1): it is the only provider run end to end, and the eval judge needs
strict `json_schema` output, unverified elsewhere. Templates are string constants here, filled
only with a validated name, a date and fixed provider values, so no user text reaches YAML.

Writes go only under the new directory. It must be absent or an empty real directory (not a
symlink) that this user owns and others cannot write to (group write only for the user's own
group, and never on macOS, where the default group `staff` holds every local user); a new one
stays 0700 until the end.
Everything is written and checked in a hidden staging directory inside it, then moved into
place; on failure what was created is removed. Files are 0644, directories (and any parents it
creates) 0755. A path with control or bidi characters is refused rather than echoed.

Based on the starter walked by hand on 3.0.0a2, with `traces: none` and `agent_events: none`
added (§9.2 #8); this template ran end to end from a checkout on 2026-11-12 (`services up`,
`build --locked`, `release` 2/2).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from agentbreeder import example, registry

RUNTIME = "runtime/adk@1.0.6"
VERSION = "0.1.0"
REVIEW_DAYS = 180
SUMMARY_VERSION = 1  # the `--json` shape (and its `error` form) in docs/commands.md
# The agent name is also the stack name: a stack name starts with a letter and is at most 31
# characters (services.schema), an agent name ends with a letter or digit (agentfile).
NAME_PATTERN = r"^[a-z]([a-z0-9-]{0,29}[a-z0-9])?$"


@dataclass(frozen=True)
class Provider:
    model: str  # the LiteLLM model string
    key: str  # the environment variable LiteLLM reads the key from


PROVIDERS = {"gemini": Provider("gemini/gemini-3.8-flash", "GEMINI_API_KEY")}

AGENT_YAML = """\
# The {name} agent, written by `agentbreeder new agent` on {today}.
#
# Published versions are immutable. To change the prompt (or, the same way, the evals in
# src/evals/), run these from this directory:
#   1. edit src/prompt/prompt.md
#   2. bump `version:` in src/prompt/manifest.yaml, e.g. 0.1.0 -> 0.1.1
#   3. agentbreeder registry publish src/prompt
#   4. change the prompt ref below to prompt/{name}-prompt@0.1.1
#   5. agentbreeder build agent.yaml --stack . --environment dev
#   6. agentbreeder release agent.yaml --stack . --environment dev
# `agentbreeder next` shows where the project is and what to run. With npx or uvx, use the
# command you launched with in place of `agentbreeder`.
kind: agent
schema_version: 1
name: {name}
runtime: {runtime}
model: {{alias: chat, reasoning: low, max_model_calls: 2}}
prompt: {{ref: prompt/{name}-prompt@{version}}}
evals: {{ref: eval/{name}-evals@{version}, threshold: 1.0, retries: 1}}
traces: none        # this stack has no trace collector
agent_events: none  # and no event log for the agent
"""

STACK_YAML = """\
# The {name} agent's local backing services: Postgres and the LiteLLM model gateway.
# `agentbreeder services up --environment dev --env-file FILE` starts them; FILE holds
# {key}=... (mode 0600). See docs/services.md.
kind: stack
schema_version: 1
name: {name}
services:
  postgres:
    ref: service/postgres-pgvector@1.1.0
    with: {{databases: [app, litellm]}}
  litellm:
    ref: service/litellm@1.0.0
    with: {{config: stack/litellm/config.yaml}}
    wire: {{database-url: postgres.databases.litellm}}
environment:
  gateway: litellm
  models: [chat, judge]          # chat for the agent, judge for its evals
  ceilings: {{budget_usd: 5, rpm: 60}}
  bindings:
    DATABASE_URL: {{ref: postgres.databases.app}}
  eval_database: {{ref: postgres.databases.app}}
  agents:
    {name}:
      sponsor: you@example.com   # who answers for this agent
      review_by: "{review_by}"     # the gate refuses deploys after this date
      candidate: true            # release tests a candidate before production
"""

LITELLM_CONFIG = """\
model_list:
  - model_name: chat
    litellm_params: {{model: {model}, api_key: os.environ/{key}}}
  - model_name: judge
    litellm_params: {{model: {model}, api_key: os.environ/{key}}}
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
"""

PROMPT_MANIFEST = """\
kind: prompt
name: {name}-prompt
version: {version}
description: The {name} agent's system prompt.
"""

PROMPT = """\
You are {name}, a concise assistant for the Acme help desk. Answer in one or two sentences.
Acme's support hours are 9am to 5pm Pacific, Monday to Friday. If you don't know something, say so.
"""

EVALS_MANIFEST = """\
kind: eval
name: {name}-evals
version: {version}
description: Starter cases for the {name} agent.
judge: {{alias: judge}}
cases: golden.jsonl
"""

# The two cases run on 3.0.0a2 (2/2 passed); the number shipped is the number verified.
CASES = (
    '{"id": "hours", "turns": ["When is support open?"], "expect": {"must_match": ["9"]}, '
    '"reference": "Support is open 9am to 5pm Pacific, Monday to Friday."}\n'
    '{"id": "unknown", "turns": ["What is Acme\'s stock price today?"], "expect": {}, '
    '"reference": "The agent says it does not know rather than inventing a number."}\n'
)

PROMPT_SOURCE = "src/prompt"
EVALS_SOURCE = "src/evals"


class NewAgentError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


@dataclass(frozen=True)
class Plan:
    name: str
    model: str
    directory: Path
    review_by: str
    files: dict[str, str]  # relative path -> content, in write order
    publishes: tuple[tuple[str, str], ...]  # (source directory, ref)
    published: dict[str, str] = field(default_factory=dict)  # ref -> sha256, after create

    def summary(self, dry_run: bool) -> dict:
        """The `--json` shape: what is (or would be) written, without file content."""
        files = [
            {
                "path": relative,
                "mode": "0644",
                "sha256": "sha256:" + hashlib.sha256(content.encode()).hexdigest(),
            }
            for relative, content in self.files.items()
        ]
        return {
            "schema_version": SUMMARY_VERSION,
            "dry_run": dry_run,
            "name": self.name,
            "directory": str(self.directory),
            "model": self.model,
            "runtime": RUNTIME,
            "aliases": ["chat", "judge"],
            "key": PROVIDERS[self.model].key,
            "review_by": self.review_by,
            "cases": [case["id"] for case in _cases()],
            "files": files,
            "published": [
                {"ref": ref, "source": source, "sha256": self.published.get(ref)}
                for source, ref in self.publishes
            ],
            "lock": "agent.lock",
        }


def _cases() -> list[dict]:
    import json

    return [json.loads(line) for line in CASES.splitlines()]


def valid_name(name: object) -> bool:
    """A name `new agent` accepts: the agent and stack name patterns, and not a word YAML reads
    as something other than a string (`true`, `null`, `no`, …), since names go unquoted into
    the templates."""
    import yaml

    return (
        isinstance(name, str)
        and re.fullmatch(NAME_PATTERN, name) is not None
        and yaml.safe_load(name) == name
    )


def check_inputs(name: str, model: str) -> None:
    """The name and `--model` checks (also used by `--stack-for`)."""
    if not valid_name(name):
        raise NewAgentError(
            "NAME_INVALID",
            "the agent name must be lowercase letters, digits and hyphens, start with a letter, "
            "end with a letter or digit, be at most 31 characters, and not be a YAML word such "
            "as true, false, yes, no, on, off or null",
        )
    if model not in PROVIDERS:
        raise NewAgentError(
            "MODEL_UNSUPPORTED",
            f"--model must be one of: {', '.join(sorted(PROVIDERS))} (the only providers run "
            "end to end)",
        )


def plan(name: str, model: str, directory: Path | None = None, today: date | None = None) -> Plan:
    """Check the inputs and fill the templates; writes nothing."""
    check_inputs(name, model)
    today = today or date.today()
    review_by = (today + timedelta(days=REVIEW_DAYS)).isoformat()
    provider = PROVIDERS[model]
    values = {
        "name": name,
        "today": today.isoformat(),
        "runtime": RUNTIME,
        "version": VERSION,
        "review_by": review_by,
        "model": provider.model,
        "key": provider.key,
    }
    files = {
        "agent.yaml": AGENT_YAML.format(**values),
        "stack.yaml": STACK_YAML.format(**values),
        "stack/litellm/config.yaml": LITELLM_CONFIG.format(**values),
        f"{PROMPT_SOURCE}/manifest.yaml": PROMPT_MANIFEST.format(**values),
        f"{PROMPT_SOURCE}/prompt.md": PROMPT.format(**values),
        f"{EVALS_SOURCE}/manifest.yaml": EVALS_MANIFEST.format(**values),
        f"{EVALS_SOURCE}/golden.jsonl": CASES,
    }
    publishes = (
        (PROMPT_SOURCE, f"prompt/{name}-prompt@{VERSION}"),
        (EVALS_SOURCE, f"eval/{name}-evals@{VERSION}"),
    )
    target = Path(os.path.abspath(directory if directory is not None else Path(name)))
    if registry.clean(str(target), name=True)[0] != str(target):
        # Never echoed: control or bidi characters would reach the terminal (and could forge
        # a `Next:` line).
        raise NewAgentError(
            "DESTINATION_UNSAFE", "the destination path has control or bidi characters"
        )
    return Plan(name, model, target, review_by, files, publishes)


def check(plan: Plan) -> bool:
    """Refuse a destination that is a symlink, not a directory, not empty, or that another
    user could change while it is written (not owned by this user, or writable by others).
    True if it exists (an empty real directory)."""
    try:
        existed = example._check_destination(plan.directory)
    except example.ExampleError as error:
        raise NewAgentError(error.code, _bare(error)) from error
    if existed:
        info = plan.directory.lstat()
        # Group write is fine for a user private group (umask 002 on Linux), not for a shared
        # one: on macOS every local user is in the default group `staff`.
        shared_group = sys.platform == "darwin" or info.st_gid != os.getegid()
        if (
            info.st_uid != os.geteuid()
            or info.st_mode & stat.S_IWOTH
            or (info.st_mode & stat.S_IWGRP and shared_group)
        ):
            raise NewAgentError(
                "DESTINATION_UNSAFE",
                f"{plan.directory} must be owned by you and not writable by other users",
            )
    return existed


def _make_directories(destination: Path, created: list[Path]) -> None:
    """Create `destination` (0700 until the end) and missing parents (0755), recording each in
    `created` as it is made, so a failure part-way removes exactly those."""
    missing = []
    current = destination
    while not os.path.lexists(current):
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        created.append(directory)
        if directory != destination:
            os.chmod(directory, 0o755)


def _bare(error: Exception) -> str:
    """A coded error's message without its `[CODE] ` prefix."""
    return str(error).removeprefix(f"[{getattr(error, 'code', '')}] ")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as writer:
        writer.write(content)
    os.chmod(path, 0o644)


def _lock(project: Path, refs: list[str]) -> None:
    """Write agent.lock as `build` would (plain mode), from the versions just published.

    Every ref must still resolve over the full search path (an `AGENTBREEDER_REGISTRY_PATH`
    root with different content is a conflict `build` would refuse), but the lock is made from
    the project's registry and the built-ins only: `project` is the staging directory, one level
    below the final project, so a source label relative to it would be wrong for any other root.
    """
    from agentbreeder.lock import Lock, materialise

    for text in refs:
        registry.resolve(registry.Ref.parse(text), registry.roots(project))
    lock = Lock()
    with tempfile.TemporaryDirectory(prefix="agentbreeder-new-") as temporary:
        materialise(
            refs=refs,
            paths=[],
            project_dir=project,
            search=[project / "registry", registry.BUILTIN_ROOT],
            lock=lock,
            context=Path(temporary) / "context",
        )
    lock.save(project / "agent.lock")


def _validate(project: Path) -> list[str]:
    """The written files must load as an agent and a stack; returns the agent's refs."""
    from agentbreeder.agentfile import AgentFileError, load_agent_file
    from agentbreeder.services.schema import ServiceError, load_stack

    try:
        agent = load_agent_file(project / "agent.yaml")
        load_stack(project / "stack.yaml")
    except (AgentFileError, ServiceError) as error:  # a template bug: names are checked first
        raise NewAgentError(
            "TEMPLATE_INVALID", f"the starter does not validate: {error}"
        ) from error
    return agent.refs()


def _modes(root: Path) -> None:
    for directory, dirnames, filenames in os.walk(root):
        for name in dirnames:
            os.chmod(Path(directory, name), 0o755)
        for name in filenames:
            os.chmod(Path(directory, name), 0o644)


def create(plan: Plan) -> Plan:
    """Write the starter into `plan.directory`, publish its versions and lock them."""
    existed = check(plan)
    created: list[Path] = []
    moved: list[Path] = []
    staging: Path | None = None
    try:
        if not existed:
            _make_directories(plan.directory, created)
        staging = Path(tempfile.mkdtemp(prefix=".agentbreeder-new-", dir=plan.directory))
        for relative, content in plan.files.items():
            _write(staging / relative, content)
        published = {}
        for source, expected in plan.publishes:
            ref, digest, _ = registry.publish(staging / source, staging / "registry")
            if str(ref) != expected:  # a template bug
                raise NewAgentError("TEMPLATE_INVALID", f"published {ref}, expected {expected}")
            published[str(ref)] = digest
        _lock(staging, _validate(staging))
        _modes(staging)
        example._check_destination(plan.directory, allow=staging)
        for entry in sorted(staging.iterdir()):
            target = plan.directory / entry.name
            if os.path.lexists(target):
                raise NewAgentError("DESTINATION_NOT_EMPTY", f"{target} appeared while writing")
            os.rename(entry, target)
            moved.append(target)
        staging.rmdir()
        if not existed:
            os.chmod(plan.directory, 0o755)
    except BaseException as error:
        for target in reversed(moved):
            example._remove(target)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        for directory in reversed(created):
            try:
                directory.rmdir()
            except OSError:
                pass
        if isinstance(error, example.ExampleError):
            raise NewAgentError(error.code, _bare(error)) from error
        if isinstance(error, registry.RegistryError):
            raise NewAgentError(error.code, _bare(error)) from error
        if isinstance(error, OSError):
            raise NewAgentError(
                "WRITE_FAILED", f"cannot write {plan.directory}: {error.strerror or error}"
            ) from error
        raise
    return Plan(
        plan.name,
        plan.model,
        plan.directory,
        plan.review_by,
        plan.files,
        plan.publishes,
        published,
    )
