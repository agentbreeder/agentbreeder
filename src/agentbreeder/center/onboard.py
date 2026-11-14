"""Onboard an `agent.yaml` alone (#823, M7; console spec §4.5 "Onboard", §8.5, §10 S-01).

The console runs `new agent NAME --model PROVIDER [--model-id=ID] --stack-for=PATH` twice:
first with `--dry-run --json` for the preview (the files it would write, the gateway aliases and
what the project brings that runs in agents), then, once the owner confirms, with
`--expect-digest` set to the previewed `agent_sha256`, so a changed `agent.yaml` is refused rather
than written for. The three-tier confirm is: (1) the server-produced preview, (2) a "this code runs
in agents" checkbox when the project brings code from outside the built-in registry (`runs_code`),
and (3) the confirm button. The server re-checks (2) on its own dry run before writing; the confirm
is a UX control, not a boundary (spec §10.1 I1). The project is then registered, which re-applies
the S-01 ownership check.

Pure request and answer checks: no engine module is loaded, so the server process keeps its
allowlist (`tests/test_center_reads.py`). Text from the request reaches argv only as `--flag=VALUE`
after these checks, never as a separate word that could read as a flag.
"""

from __future__ import annotations

import re

from agentbreeder.center.files import BadRequest

# `newagent.PROVIDERS` and `newagent.NAME_PATTERN` (tests keep them equal).
PROVIDERS = ("anthropic", "gemini", "openai", "openrouter")
NAME = re.compile(r"^[a-z]([a-z0-9-]{0,29}[a-z0-9])?$")
MODEL_ID = re.compile(r"[A-Za-z0-9._:/-]{1,128}")  # `newagent.MODEL_ID_PATTERN`
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
AGENT_FILE = "agent.yaml"
MAX_PATH = 4096
CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
ROOT_KINDS = ("project", "path", "builtin")
TIMEOUT = 60  # resolving the agent's refs and an eval set; writing two small files


class BadOutput(ValueError):
    pass


def request(body: object) -> tuple[str, str, list[str]]:
    """The checked `(name, agent_path, argv)` for a preview or write request body. `argv` has no
    `--dry-run`, `--expect-digest` or `--json`; the caller adds them."""
    if not isinstance(body, dict):
        raise BadRequest("Send the path, the agent's name and a model provider")
    name = body.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise BadRequest(
            "The agent's name starts with a lowercase letter, then lowercase letters, digits and "
            "dashes, at most 31 characters, not ending in a dash",
            "NAME_INVALID",
        )
    model = body.get("model")
    if model not in PROVIDERS:
        raise BadRequest(f"Pick a model provider: {', '.join(PROVIDERS)}", "MODEL_INVALID")
    path = body.get("path")
    if not isinstance(path, str) or not path.startswith("/") or len(path) > MAX_PATH:
        raise BadRequest(
            "Type the absolute path of the agent.yaml (or its directory)", "PATH_INVALID"
        )
    if CONTROLS.search(path):
        raise BadRequest("That path has control characters", "PATH_INVALID")
    if path.rstrip("/").rsplit("/", 1)[-1] != AGENT_FILE:
        path = path.rstrip("/") + "/" + AGENT_FILE
    argv = ["new", "agent", name, "--model", model]
    model_id = body.get("model_id")
    if model_id not in (None, ""):
        # The CLI checks the provider prefix and segments; here only the alphabet, so the value
        # is one argv word that is never a flag (`--model-id=` keeps it one word anyway).
        if not isinstance(model_id, str) or not MODEL_ID.fullmatch(model_id):
            raise BadRequest(
                "A model id is letters, digits and . _ : / -, at most 128 characters",
                "MODEL_ID_INVALID",
            )
        argv.append(f"--model-id={model_id}")
    argv.append(f"--stack-for={path}")
    return name, path, argv


def write_request(body: object) -> tuple[str, bool]:
    """The previewed digest and the "this code runs in agents" answer from a write request."""
    digest = body.get("digest") if isinstance(body, dict) else None
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise BadRequest("Preview the agent.yaml first", "PREVIEW_REQUIRED")
    return digest, body.get("code_confirmed") is True


def _strings(value: object, limit: int = 50) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise BadOutput
    if not all(isinstance(item, str) for item in value):
        raise BadOutput
    return list(value)


def summary(data: object, name: str, dry_run: bool, model: str | None = None) -> dict:
    """`new agent --stack-for --json`'s answer, checked against its schema 1 shape and reduced to
    what the preview shows; `model`, when given, is the provider that was asked for. Raises
    BadOutput for anything else."""
    try:
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise BadOutput
        if data.get("dry_run") is not dry_run or data.get("name") != name:
            raise BadOutput
        text = {key: data.get(key) for key in ("directory", "model", "model_id", "key")}
        text["review_by"] = data.get("review_by")
        if not all(isinstance(value, str) for value in text.values()):
            raise BadOutput
        if data.get("model") not in PROVIDERS or not isinstance(data.get("verified"), bool):
            raise BadOutput
        if model is not None and data.get("model") != model:
            raise BadOutput
        digest = data.get("agent_sha256")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise BadOutput
        files = data.get("files")
        if not isinstance(files, list) or not 0 < len(files) <= 10:
            raise BadOutput
        shown_files = []
        for entry in files:
            if not isinstance(entry, dict):
                raise BadOutput
            path, sha = entry.get("path"), entry.get("sha256")
            if not isinstance(path, str) or not isinstance(sha, str) or not DIGEST.fullmatch(sha):
                raise BadOutput
            shown_files.append({"path": path, "sha256": sha})
        refs = data.get("refs")
        if not isinstance(refs, list) or len(refs) > 100:
            raise BadOutput
        shown_refs = []
        for entry in refs:
            if not isinstance(entry, dict):
                raise BadOutput
            ref, kind = entry.get("ref"), entry.get("kind")
            root_kind, runs = entry.get("root_kind"), entry.get("runs_code")
            if not isinstance(ref, str) or not isinstance(kind, str):
                raise BadOutput
            if root_kind not in ROOT_KINDS or not isinstance(runs, bool):
                raise BadOutput
            shown_refs.append(
                {"ref": ref, "kind": kind, "root_kind": root_kind, "runs_code": runs}
            )
        runs_code = data.get("runs_code")
        # Recomputed, so a preview can never hide code its own rows show.
        expected = any(row["runs_code"] and row["root_kind"] != "builtin" for row in shown_refs)
        if runs_code is not expected:
            raise BadOutput
        return {
            "name": name,
            **text,
            "verified": data["verified"],
            "aliases": _strings(data.get("aliases"), 10),
            "warnings": _strings(data.get("warnings"), 20),
            "files": shown_files,
            "refs": shown_refs,
            "runs_code": runs_code,
            "agent_sha256": digest,
        }
    except BadOutput:
        raise BadOutput("`new agent --stack-for --json` printed an unexpected answer") from None
