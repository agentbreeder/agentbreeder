"""`agentbreeder agent set-ref OLD NEW`: move an agent to another registry version (console §8.4).

An exact-string replacement in the project's `agent.yaml`, so comments and formatting are kept.
It refuses unless OLD is one of the refs `build` reads and occurs exactly once in the file as a
whole ref (not inside a longer one), NEW is the same kind and resolves in the project's search
roots, and the rewritten file still validates with every other ref unchanged. `--expect-base`
(S-04) refuses unless the file is still the bytes that were reviewed; the write is an atomic
replace. The record of the change is the file itself and git (S-30), not an audit chain.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import tempfile
from pathlib import Path

from pydantic import ValidationError

from agentbreeder import registry
from agentbreeder.agentfile import MAX_BYTES, AgentFile, detect
from agentbreeder.registry import Ref, RegistryError, read_project_file
from agentbreeder.strict_yaml import StrictYamlError, load_text

AGENT_FILE = "agent.yaml"
# A ref character: a match must not continue one (`prompt/x@1.0.1` inside `prompt/x@1.0.10`).
_REF_CHAR = r"[A-Za-z0-9._/@-]"


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _refs(text: str, where: str) -> list[tuple[str, str]]:
    """(location, ref) for every ref `build` reads, from a valid agent file's text."""
    try:
        data = load_text(text)
    except StrictYamlError as error:
        raise RegistryError("AGENT_INVALID", f"{where}: {error}") from None
    if detect(data) != "agent":
        raise RegistryError("AGENT_INVALID", f"{where} needs `kind: agent`")
    try:
        AgentFile.model_validate(data)
    except ValidationError as error:
        first = error.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "agent"
        raise RegistryError("AGENT_INVALID", f"{where}: {location}: {first['msg']}") from None
    return [(at, value) for at, value in registry._ref_positions("agent", data)]


def _unified(before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        f"a/{AGENT_FILE}",
        f"b/{AGENT_FILE}",
    )
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def set_ref(
    project_dir: Path,
    old: str,
    new: str,
    *,
    expect_base: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Replace OLD with NEW in `<project_dir>/agent.yaml`. Returns {file, old, new, at,
    base_sha256, sha256, diff, dry_run}: `base_sha256` is the file before (what a later call
    passes as `expect_base`), `sha256` the file after, `diff` a unified diff."""
    if expect_base is not None and not registry.DIGEST.fullmatch(expect_base):
        raise RegistryError("DIGEST_INVALID", "--expect-base must be sha256:<64 hex digits>")
    old_ref, new_ref = Ref.parse(old), Ref.parse(new)
    if old_ref.kind != new_ref.kind:
        raise RegistryError(
            "REF_INVALID", f"{old_ref} and {new_ref} are different kinds; a ref moves within one"
        )
    if old_ref == new_ref:
        raise RegistryError("REF_UNCHANGED", f"{AGENT_FILE} would not change: OLD is NEW")
    path = project_dir / AGENT_FILE
    raw = read_project_file(path, MAX_BYTES)
    if raw is None:
        raise RegistryError(
            "AGENT_NOT_FOUND", f"no {AGENT_FILE} in {registry._shown(project_dir)}"
        )
    base = digest(raw)
    if expect_base is not None and base != expect_base:
        raise RegistryError(
            "BASE_MISMATCH",
            f"{AGENT_FILE} is {base}, not the reviewed {expect_base}: it changed after the "
            "preview; review it again",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise RegistryError("AGENT_INVALID", f"{AGENT_FILE} is not UTF-8 text") from None
    refs = _refs(text, AGENT_FILE)
    at = [where for where, value in refs if value == str(old_ref)]
    if len(at) != 1:
        raise RegistryError(
            "REF_NOT_DECLARED", f"{AGENT_FILE} does not declare {old_ref} in a ref field"
        )
    for where, value in refs:
        other = Ref.parse(value) if where != at[0] and value else None
        if other is not None and (other.kind, other.name) == (new_ref.kind, new_ref.name):
            raise RegistryError(
                "REF_DUPLICATE",
                f"{AGENT_FILE} already uses {other} at {where} (one version per name)",
            )
    pattern = re.compile(rf"(?<!{_REF_CHAR}){re.escape(str(old_ref))}(?!{_REF_CHAR})")
    found = pattern.findall(text)
    if not found:
        raise RegistryError(
            "REF_AMBIGUOUS",
            f"{AGENT_FILE} writes {old_ref} in a form set-ref cannot replace exactly (quoted "
            "with escapes or split over lines); edit the file by hand",
        )
    if len(found) != 1:
        raise RegistryError(
            "REF_AMBIGUOUS",
            f"{old_ref} occurs {len(found)} times in {AGENT_FILE} (a comment or another field "
            "too); edit the file by hand",
        )
    registry.resolve(new_ref, registry.roots(project_dir))  # REF_NOT_FOUND / REF_CONFLICT
    updated = pattern.sub(lambda _: str(new_ref), text)
    expected = [(where, str(new_ref) if where == at[0] else value) for where, value in refs]
    if _refs(updated, f"{AGENT_FILE} after the change") != expected:
        raise RegistryError(
            "REF_AMBIGUOUS", f"replacing {old_ref} would change more than {at[0]}; edit by hand"
        )
    data = updated.encode("utf-8")
    result = {
        "file": registry._absolute(path),
        "old": str(old_ref),
        "new": str(new_ref),
        "at": at[0],
        "base_sha256": base,
        "sha256": digest(data),
        "diff": registry.clean(_unified(text, updated))[0],
        "dry_run": dry_run,
    }
    if not dry_run:
        _replace(path, raw, data)
    return result


def _replace(path: Path, base: bytes, data: bytes) -> None:
    """Write `data` over `path` atomically (a temp file in the same directory, then a rename),
    keeping its mode, unless the file is no longer `base`. That re-check narrows, but cannot
    close, the window for a writer that does not take the console's project lock (an editor,
    git). An OS error is WRITE_FAILED and leaves no temp file."""
    temporary = None
    try:
        mode = os.lstat(path).st_mode & 0o777
        descriptor, temporary = tempfile.mkstemp(
            prefix=".agent.yaml-", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        if read_project_file(path, MAX_BYTES) != base:
            raise RegistryError(
                "BASE_MISMATCH", f"{AGENT_FILE} changed while it was being rewritten; try again"
            )
        os.replace(temporary, path)
        temporary = None
    except OSError as error:
        raise RegistryError(
            "WRITE_FAILED", f"cannot write {AGENT_FILE}: {error.strerror or error}"
        ) from None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
