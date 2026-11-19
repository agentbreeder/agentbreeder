"""The conformance report: its shape, where it is written and how it is read back.

A report is always written under the **default** state directory,
`~/.agentbreeder/conformance/<runtime>/<version>/<image digest>.json`, whatever `--stack` or
`--state-dir` a command was given, so the console's Build screen and the runtime detail read one
place (multi-framework spec §10.3, console spec §8). Directories are 0700 and files 0600.

`result` is `pass` only when every check that applies to the image's profile passed. A check
that did not run makes it `incomplete`, which is not a pass: a runtime version is never called
conformant from a report with a `not_run` row.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from agentbreeder import __version__
from agentbreeder.conformance import checks, protocol
from agentbreeder.registry import Ref, RegistryError
from agentbreeder.safe_text import excerpt

SCHEMA_VERSION = 1
REPORT_LIMIT = 1024 * 1024
MAX_REPORTS = 500  # report files of one runtime version read by `newest`
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
RESULTS = (checks.PASS, checks.FAIL, "incomplete")


def root() -> Path:
    """`~/.agentbreeder/conformance`: never a stack's state directory."""
    return Path.home() / ".agentbreeder" / "conformance"


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build(
    *, image: str, runtime: str, profile: str, agent: str, rows: list[dict], started: str
) -> dict:
    """The report for one run. `runtime` is the ref, `image` the image ID."""
    counts = {
        status: sum(1 for row in rows if row["status"] == status)
        for status in (checks.PASS, checks.FAIL, checks.NOT_RUN, checks.NOT_APPLICABLE)
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol": protocol.VERSION,
        "profile": profile,
        "runtime": runtime,
        "mode": "declared",
        "agent": agent,
        "image": image,
        "result": checks.result(rows),
        "counts": counts,
        "started": started,
        "finished": now(),
        "agentbreeder": __version__,
        "checks": rows,
    }


def directory(runtime: str, base: Path | None = None) -> Path:
    """Where a runtime version's reports are. `runtime` must be a `runtime/` ref."""
    ref = Ref.parse(runtime)
    if ref.kind != "runtime":
        raise RegistryError("REF_INVALID", f"{runtime!r} is not a runtime ref")
    return (base or root()) / ref.name / ref.version


def private_directory(path: Path) -> None:
    """Create `path` and its missing parents 0700; refuse one that is a link or not yours."""
    missing = []
    current = path
    while not os.path.lexists(current):
        missing.append(current)
        current = current.parent
    for part in reversed(missing):
        part.mkdir(mode=0o700, exist_ok=True)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise OSError(f"{path} must be a directory you own, not a link")
    if info.st_mode & 0o077:
        path.chmod(0o700)


def write(report: dict, base: Path | None = None) -> Path:
    """Write the report (0600, replaced atomically) and return its path."""
    if not IMAGE.fullmatch(str(report.get("image"))):
        raise ValueError("a report needs the image ID (sha256:…)")
    target = directory(report["runtime"], base)
    for part in (target.parent.parent, target.parent, target):
        private_directory(part)
    path = target / (report["image"].removeprefix("sha256:") + ".json")
    data = (json.dumps(report, indent=2) + "\n").encode()
    descriptor, temporary = tempfile.mkstemp(dir=target, prefix=".report-")
    try:
        with os.fdopen(descriptor, "wb") as writer:
            writer.write(data)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def _read(path: Path) -> dict | None:
    """One report file, or None when it is not a private regular file holding a report."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        with os.fdopen(descriptor, "rb") as reader:
            info = os.fstat(reader.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o022
                or info.st_size > REPORT_LIMIT
            ):
                return None
            data = json.loads(reader.read(REPORT_LIMIT + 1))
    except (OSError, ValueError, RecursionError):
        return None
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != SCHEMA_VERSION
        or data.get("result") not in RESULTS
        or not IMAGE.fullmatch(str(data.get("image")))
        or not _STAMP.fullmatch(str(data.get("finished")))
        or path.name != data["image"].removeprefix("sha256:") + ".json"
    ):
        return None
    return data


def newest(runtime: str, base: Path | None = None) -> dict | None:
    """The newest report for an image built on this runtime version, as the summary
    `registry runtimes --json` shows: {passed, result, digest, date}. None when there is none
    (or the directory is not a private one you own)."""
    try:
        target = directory(runtime, base)
        info = os.lstat(target)
    except (OSError, RegistryError):
        return None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        return None
    try:
        with os.scandir(target) as entries:
            stamped = [
                (entry.stat(follow_symlinks=False).st_mtime, entry.name)
                for entry in entries
                if entry.name.endswith(".json")
            ]
    except OSError:
        return None
    names = [name for _, name in sorted(stamped, reverse=True)]  # the most recently written
    best = None
    for name in names[:MAX_REPORTS]:
        data = _read(target / name)
        if data is None or data.get("runtime") != runtime:
            continue
        if best is None or data["finished"] > best["finished"]:
            best = data
    if best is None:
        return None
    return {
        "passed": best["result"] == checks.PASS,
        "result": best["result"],
        "digest": best["image"],
        "date": best["finished"],
    }


def text(report: dict) -> str:
    """The report for a terminal. Details hold text the agent produced, so each is one capped
    line."""
    lines = [
        f"{report['runtime']}  protocol {report['protocol']} ({report['profile']})  "
        f"{report['image']}"
    ]
    group = None
    for row in report["checks"]:
        if row["group"] != group:
            group = row["group"]
            lines.append(f"{group}:")
        detail = f": {excerpt(row['detail'], 300)}" if row["detail"] else ""
        lines.append(f"  {row['status']:<14} {row['id']}{detail}")
    counts = report["counts"]
    lines.append(
        f"Result: {report['result']} ({counts['pass']} passed, {counts['fail']} failed, "
        f"{counts['not_run']} not run, {counts['not_applicable']} not applicable)"
    )
    if report["result"] == "incomplete":
        lines.append("Not a pass: every check that applies must run and pass.")
    return "\n".join(lines)
