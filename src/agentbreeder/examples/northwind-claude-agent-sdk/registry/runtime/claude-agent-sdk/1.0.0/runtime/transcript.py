"""History for the Claude CLI: a transcript written per turn from `turns` (spec §8.4 (a)).

The CLI takes history only by resuming its own transcript, so the CLI host writes the
conversation's user and assistant text (the kit's history, §7.5: at most 20 turns, 64 KiB) into
the turn's own config directory as the pinned CLI's JSONL, the CLI resumes it, and the directory
is deleted when the turn ends. Nothing outlives the turn, and `turns` stays the only history.

The JSONL format is the CLI's own and undocumented; the entry shape below is the one the pinned
SDK's own transcript tools write (`session_mutations.py`). Each entry is built as a dict and
serialised with `json.dumps`, never templated, so customer text cannot change its structure.
Whether CLI 2.1.317 resumes it as a faithful history was not run (§16.1); the conformance kit
checks it on every CLI version.

Stdlib only.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

SESSION = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MAX_ITEMS = 40  # 20 turns of user and assistant text
MAX_TEXT = 64 * 1024
TIMESTAMP = "2026-01-01T00:00:00.000Z"  # fixed: a written entry's time joins to nothing
_UNSAFE = re.compile(r"[^a-zA-Z0-9]")


def project_key(cwd: str) -> str:
    """The CLI's directory name for a working directory (`sessions._sanitize_path`): every
    non-alphanumeric character becomes '-'. Working directories here are short and fixed."""
    key = _UNSAFE.sub("-", cwd)
    if len(key) > 200:
        raise ValueError("working directory too long")
    return key


def valid_history(history: object) -> list[dict]:
    """The history as the kit gives it: user/assistant pairs of strings, oldest first."""
    if not isinstance(history, list) or len(history) > MAX_ITEMS or len(history) % 2:
        raise ValueError("history must be user/assistant pairs")
    total = 0
    for position, item in enumerate(history):
        role = "user" if position % 2 == 0 else "assistant"
        if (
            not isinstance(item, dict)
            or set(item) != {"role", "content"}
            or item["role"] != role
            or not isinstance(item["content"], str)
        ):
            raise ValueError("history must be user/assistant pairs")
        total += len(item["content"])
    if total > MAX_TEXT * 2:
        raise ValueError("history too long")
    return history


def entries(history: list[dict], session_id: str, cwd: str) -> list[dict]:
    """The transcript entries: a chain of user and assistant messages."""
    if not SESSION.fullmatch(session_id):
        raise ValueError("session id must be a lower-case UUID")
    result = []
    parent = None
    for item in valid_history(history):
        identifier = str(uuid.uuid4())
        message = (
            {"role": "user", "content": item["content"]}
            if item["role"] == "user"
            else {"role": "assistant", "content": [{"type": "text", "text": item["content"]}]}
        )
        result.append(
            {
                "type": item["role"],
                "uuid": identifier,
                "parentUuid": parent,
                "sessionId": session_id,
                "timestamp": TIMESTAMP,
                "isSidechain": False,
                "userType": "external",
                "cwd": cwd,
                "message": message,
            }
        )
        parent = identifier
    return result


def write(config_dir: Path, cwd: str, session_id: str, history: list[dict]) -> Path:
    """Write the transcript where the CLI looks for it; returns its path."""
    path = config_dir / "projects" / project_key(cwd) / f"{session_id}.jsonl"
    path.parent.mkdir(mode=0o700, parents=True)
    lines = [
        json.dumps(e, separators=(",", ":"), ensure_ascii=True)
        for e in entries(history, session_id, cwd)
    ]
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    path.chmod(0o600)
    return path
