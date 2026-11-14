"""Read back the eval reports `release` saves as `<dir>/<agent>.eval-<run>.json` (`evals`).

The report files are local, but their strings are untrusted: case ids, check names, tool names
and the judge's reason come from the eval set or from model output. Every string shown is
redacted with the values the caller passes (`redaction.collect`), made one line and capped, and
the raw reply and full judge explanation are only read when asked for (`content=True`), redacted
and capped too. Only plain report names are opened: a regular file (never a symlink, opened with
O_NOFOLLOW and checked with fstat), at most MAX_BYTES, strict JSON (no NaN or Infinity).

The file format is unchanged and owned by `release` (`EvalReport.summary()` plus `results`). A
malformed case is skipped with a note; a malformed header makes the report invalid.
"""

from __future__ import annotations

import errno
import json
import math
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agentbreeder import redaction, safe_text

SCHEMA_VERSION = 1
MAX_BYTES = 4 * 1024 * 1024
MAX_LIST = 100  # rows `evals list` reads and returns, newest first
LIST_BYTES = 32 * 1024 * 1024  # report bytes `evals list` parses in all
MAX_REDACTIONS = 20_000  # distinct strings redacted per report (bounded time)
MAX_CASES = 5000
MAX_CHECKS = 100
MAX_TOOLS = 100
CONTENT_LIMIT = 4096  # characters of a reply or a full explanation with --content
# Characters redacted past what is shown, so redaction never scans a whole 4 MiB string: a
# secret form (literal, base64, URL- or JSON-quoted) that starts in the shown part and is at most
# half this long is found; the window grows to twice the longest form when that is longer.
REDACT_WINDOW = 32 * 1024
TOO_MUCH_REDACTED = "[not shown: mostly redacted]"
OVER_BUDGET = "[not shown: too many strings to redact]"
AGENT = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,51}[a-z0-9])?")  # agentfile.NAME_PATTERN
RUN = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")  # evals.run: %Y%m%d-%H%M%S-<6 hex>
FILE = re.compile(
    r"([a-z0-9](?:[a-z0-9-]{0,51}[a-z0-9])?)\.eval-([0-9]{8}-[0-9]{6}-[0-9a-f]{6})\.json"
)
TRACE = re.compile(r"[0-9a-f]{32}")

Redact = Callable[[str], str]


class ReportError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Entry:
    agent: str
    run_id: str
    path: Path

    @property
    def file(self) -> str:
        return self.path.name


@dataclass
class Report:
    entry: Entry
    header: dict
    results: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_json(self, content: bool) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "agent": self.entry.agent,
            "file": self.entry.file,
            **self.header,
            "content": content,
            "results": self.results,
            "notes": self.notes,
        }


def directory(project: Path | None, output: Path | None) -> Path:
    """`--output DIR` (as `release --output`), else `<project>/deploy` (release's default)."""
    return output if output is not None else (project or Path(".")) / "deploy"


def _check_dir(path: Path) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise ReportError(
            "EVAL_REPORT_NOT_FOUND",
            f"No eval reports: {_shown(path)} does not exist (release saves them there)",
        ) from None
    except OSError as error:
        raise ReportError(
            "EVAL_REPORT_UNREADABLE", f"Cannot read {_shown(path)}: {_why(error)}"
        ) from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ReportError(
            "EVAL_REPORT_INVALID", f"{_shown(path)} must be a directory, not a symlink"
        )


def _shown(path: Path) -> str:
    return safe_text.excerpt(str(path), 300)


def _why(error: OSError) -> str:
    return error.strerror or type(error).__name__


def entries(folder: Path, agent: str | None = None) -> list[Entry]:
    """Report files in `folder` by name (newest run first); nothing is opened."""
    if agent is not None and not AGENT.fullmatch(agent):
        raise ReportError("EVAL_REPORT_INVALID", "--agent must be an agent name (agent.yaml name)")
    _check_dir(folder)
    found: list[Entry] = []
    try:
        with os.scandir(folder) as scan:
            for item in scan:
                match = FILE.fullmatch(item.name)
                if match and (agent is None or match.group(1) == agent):
                    found.append(Entry(match.group(1), match.group(2), folder / item.name))
    except OSError as error:
        raise ReportError(
            "EVAL_REPORT_UNREADABLE", f"Cannot list {_shown(folder)}: {_why(error)}"
        ) from None
    found.sort(key=lambda e: (e.run_id, e.agent), reverse=True)
    return found


def find(folder: Path, run: str | None, agent: str | None) -> Entry:
    """The report for `run` (or the newest), refusing a run saved under two agents."""
    if run is not None and not RUN.fullmatch(run):
        raise ReportError(
            "EVAL_REPORT_INVALID", "RUN must be a run id like 20261113-213200-1a2b3c"
        )
    listed = entries(folder, agent)
    if run is not None:
        listed = [e for e in listed if e.run_id == run]
    else:
        listed = [e for e in listed if e.run_id == listed[0].run_id] if listed else []
    if not listed:
        what = f"run {run}" if run else "any run"
        who = f" of agent {agent}" if agent else ""
        raise ReportError(
            "EVAL_REPORT_NOT_FOUND", f"No eval report for {what}{who} in {_shown(folder)}"
        )
    if len(listed) > 1:
        raise ReportError(
            "EVAL_REPORT_AMBIGUOUS",
            f"Run {listed[0].run_id} has reports for agents "
            + ", ".join(e.agent for e in listed)
            + "; pass --agent NAME",
        )
    return listed[0]


def _constant(name: str):
    raise ValueError(f"{name} is not allowed")


def _pairs(pairs: list) -> dict:
    """A JSON object with no repeated key (a repeated check name could hide a failure)."""
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate key")
    return result


class Budget:
    """`redact`, memoized per distinct string, for at most MAX_REDACTIONS distinct strings;
    after that every new string is OVER_BUDGET and `exhausted` is set. Strings shorter than
    redaction.MIN_LITERAL hold neither a value it matches nor a URL password, and are returned
    as they are."""

    def __init__(self, redact: Redact):
        self.redact = redact
        self.cache: dict[str, str] = {}
        self.exhausted = False
        # The longest form `redact` replaces (a redaction.Redactor's patterns are longest first).
        forms = getattr(redact, "patterns", None) or [""]
        self.margin = max(REDACT_WINDOW // 2, len(forms[0]))

    def __call__(self, text: str) -> str:
        if len(text) < redaction.MIN_LITERAL:
            return text
        found = self.cache.get(text)
        if found is not None:
            return found
        if len(self.cache) >= MAX_REDACTIONS:
            self.exhausted = True
            return OVER_BUDGET
        found = self.cache[text] = self.redact(text)
        return found


def read_raw(entry: Entry) -> dict:
    """The parsed report file: a regular file (not a symlink), ≤ MAX_BYTES, strict JSON."""
    try:
        descriptor = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise ReportError(
            "EVAL_REPORT_NOT_FOUND", f"{entry.file} is gone (removed while listing?)"
        ) from None
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.EMLINK):  # O_NOFOLLOW on a symlink (EMLINK: BSD)
            raise ReportError(
                "EVAL_REPORT_INVALID", f"{entry.file} is a symlink; reports must be regular files"
            ) from None
        raise ReportError(
            "EVAL_REPORT_UNREADABLE", f"Cannot open {entry.file}: {_why(error)}"
        ) from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ReportError("EVAL_REPORT_INVALID", f"{entry.file} is not a regular file")
        if info.st_size > MAX_BYTES:
            raise ReportError(
                "EVAL_REPORT_TOO_LARGE", f"{entry.file} is over {MAX_BYTES // (1024 * 1024)} MiB"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(MAX_BYTES + 1)
    except OSError as error:
        raise ReportError(
            "EVAL_REPORT_UNREADABLE", f"Cannot read {entry.file}: {_why(error)}"
        ) from None
    finally:
        os.close(descriptor)
    if len(data) > MAX_BYTES:
        raise ReportError(
            "EVAL_REPORT_TOO_LARGE", f"{entry.file} is over {MAX_BYTES // (1024 * 1024)} MiB"
        )
    try:
        parsed = json.loads(
            data.decode("utf-8"), parse_constant=_constant, object_pairs_hook=_pairs
        )
    except (ValueError, RecursionError):  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        raise ReportError("EVAL_REPORT_INVALID", f"{entry.file} is not valid JSON") from None
    if not isinstance(parsed, dict):
        raise ReportError("EVAL_REPORT_INVALID", f"{entry.file} is not a report object")
    return parsed


def _int(value, minimum: int = 0) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value if value < 2**53 else None


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _window(redact: Redact, value, limit: int) -> str:
    """At most `limit` + 1 characters of `value`, redacted, in time bounded by the window.

    `margin` is the longest form `redact` replaces, at least REDACT_WINDOW // 2 (a `Budget`
    knows it). Only the first `limit` + 2 * `margin` characters are redacted, and the result is
    cut to `limit` + 1 before anything else sees it. When the value is longer than that window,
    a secret cut at the window's end is not found. What is kept stays clear of it: the kept text
    is surviving raw characters and marks, so its last character comes from at most `limit` + 1
    + `removed` raw characters in, where `removed` counts every raw character a mark replaced
    (marks that grew the text do not offset it). Unless `removed` is at most `margin` nothing is
    shown, so every secret form starting in the kept part ends inside the window."""
    margin = getattr(redact, "margin", REDACT_WINDOW // 2)
    raw = str(value)
    window = raw[: limit + 2 * margin]
    text = redact(window)
    if text == OVER_BUDGET:
        return text
    if len(raw) > len(window):
        survivors = len(text) - len(redaction.MARK) * text.count(redaction.MARK)
        if len(window) - survivors > margin:
            return TOO_MUCH_REDACTED
    return text[: limit + 1]


def _text(redact: Redact, value, limit: int) -> str:
    return safe_text.excerpt(_window(redact, value, limit), limit)


def _ids(redact: Redact, value, shown: bool) -> list[str] | None:
    if not isinstance(value, list):
        return None
    if not shown:
        return []
    return [_text(redact, item, 80) for item in value[:MAX_CASES] if isinstance(item, str)]


def header(raw: dict, entry: Entry, redact: Redact, ids: bool = True) -> dict:
    """The summary fields `EvalReport.summary()` wrote, type-checked; EVAL_REPORT_INVALID if
    one is missing or the wrong type. With `ids=False` (`evals list`), `flaky` and `failed` are
    type-checked but not redacted, and are empty."""
    cases, passed = _int(raw.get("cases")), _int(raw.get("passed"))
    rate = _number(raw.get("pass_rate"))
    texts = {k: raw.get(k) for k in ("run_id", "eval_set", "judge", "endpoint")}
    flaky, failed = _ids(redact, raw.get("flaky"), ids), _ids(redact, raw.get("failed"), ids)
    mean = _number(raw.get("mean_correctness"))
    p50 = _int(raw.get("p50_latency_ms"))
    if (
        cases is None
        or passed is None
        or passed > cases
        or rate is None
        or not all(isinstance(v, str) for v in texts.values())
        or flaky is None
        or failed is None
        or mean is None
        or p50 is None
    ):
        raise ReportError(
            "EVAL_REPORT_INVALID", f"{entry.file} is missing or has malformed summary fields"
        )
    if texts["run_id"] != entry.run_id:
        raise ReportError(
            "EVAL_REPORT_INVALID", f"{entry.file} holds another run id than its name says"
        )
    return {
        "run_id": entry.run_id,
        "eval_set": _text(redact, texts["eval_set"], 200),
        "judge": _text(redact, texts["judge"], 200),
        "endpoint": _text(redact, texts["endpoint"], 300),
        "cases": cases,
        "passed": passed,
        "pass_rate": rate,
        "flaky": flaky,
        "failed": failed,
        "mean_correctness": mean,
        "p50_latency_ms": p50,
    }


def _scalar(value) -> str | None:
    """A category or tool name as text: a string, or a number shown as text; else None."""
    if isinstance(value, str):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return None


def _content(redact: Redact, value) -> str:
    text = _window(redact, value, CONTENT_LIMIT)
    return text if len(text) <= CONTENT_LIMIT else text[: CONTENT_LIMIT - 1] + "…"


def _checks(redact: Redact, checks: dict) -> dict[str, bool]:
    """Check names shown (one line, 80 characters); names that become equal once shortened or
    redacted get a `#2`, `#3` suffix, so a failed check is never hidden by a passed one."""
    shown: dict[str, bool] = {}
    for name, ok in checks.items():
        base = key = _text(redact, name, 80)
        number = 2
        while key in shown:
            key, number = f"{base}#{number}", number + 1
        shown[key] = ok
    return shown


def case(raw, redact: Redact, content: bool) -> dict | None:
    """One result row, type-checked and redacted; None if malformed."""
    if not isinstance(raw, dict):
        return None
    checks = raw.get("checks")
    ident, passed = raw.get("case_id"), raw.get("passed")
    attempts, latency = _int(raw.get("attempts"), 1), _int(raw.get("latency_ms"))
    correctness = _number(raw.get("correctness"))
    category, trace = raw.get("category"), raw.get("trace_id")
    tools, explanation = raw.get("tools", []), raw.get("explanation") or ""
    if (
        not isinstance(ident, str)
        or not isinstance(passed, bool)
        or not isinstance(checks, dict)
        or len(checks) > MAX_CHECKS
        or not all(isinstance(v, bool) for v in checks.values())
        or attempts is None
        or latency is None
        or correctness is None
        or not isinstance(tools, list)
        or not isinstance(explanation, str)
    ):
        return None
    shown_checks = _checks(redact, checks)
    row = {
        "id": _text(redact, ident, 80),
        "category": None if _scalar(category) is None else _text(redact, _scalar(category), 80),
        "passed": passed,
        "attempts": attempts,
        "checks": shown_checks,
        "failed_checks": [name for name, ok in shown_checks.items() if not ok],
        "correctness": correctness,
        # As release's failed_cases: the judge's reason, redacted, one line, capped.
        "reason": _text(redact, explanation, 200),
        "latency_ms": latency,
        "trace_id": trace if isinstance(trace, str) and TRACE.fullmatch(trace) else None,
        "tools": [
            _text(redact, name, 80) for name in map(_scalar, tools[:MAX_TOOLS]) if name is not None
        ],
    }
    if content:
        reply = raw.get("reply")
        row["reply"] = _content(redact, reply) if isinstance(reply, str) else ""
        row["explanation"] = _content(redact, explanation)
    return row


def load(entry: Entry, redact: Redact, content: bool = False) -> Report:
    raw = read_raw(entry)
    redact = budget = Budget(redact)
    report = Report(entry, header(raw, entry, redact))
    results = raw.get("results")
    if not isinstance(results, list):
        raise ReportError("EVAL_REPORT_INVALID", f"{entry.file} has no results list")
    if len(results) > MAX_CASES:
        report.notes.append(f"only the first {MAX_CASES} of {len(results)} cases are shown")
    skipped = 0
    for item in results[:MAX_CASES]:
        row = case(item, redact, content)
        if row is None:
            skipped += 1
        else:
            report.results.append(row)
    if skipped:
        report.notes.append(f"{skipped} malformed case(s) skipped")
    if budget.exhausted:
        report.notes.append(
            f"the report has more than {MAX_REDACTIONS} distinct strings; the rest show "
            f"{OVER_BUDGET}"
        )
    passed = sum(1 for item in results if isinstance(item, dict) and item.get("passed") is True)
    if (report.header["cases"], report.header["passed"]) != (len(results), passed):
        report.notes.append(
            f"the summary says {report.header['passed']}/{report.header['cases']} passed, but "
            f"the results list has {passed}/{len(results)}"
        )
    return report


def summaries(folder: Path, agent: str | None, redact: Redact) -> tuple[list[dict], int]:
    """(rows for `evals list`, newest first and at most MAX_LIST; the number of reports found).
    A report that cannot be read is a row with its `error` code instead of the summary; so is
    one past LIST_BYTES of reports parsed in all (EVAL_REPORT_TOO_LARGE)."""
    listed = entries(folder, agent)
    rows: list[dict] = []
    parsed = 0
    for entry in listed[:MAX_LIST]:
        row: dict = {"agent": entry.agent, "run_id": entry.run_id, "file": entry.file}
        try:
            info = os.lstat(entry.path)
            row["modified"] = int(info.st_mtime)
            if info.st_size <= MAX_BYTES:  # a larger one is refused unread, and not counted
                if parsed + info.st_size > LIST_BYTES:
                    raise ReportError(
                        "EVAL_REPORT_TOO_LARGE",
                        f"not read: evals list parses at most {LIST_BYTES // (1024 * 1024)} MiB "
                        f"of reports in all; use evals show {entry.run_id}",
                    )
                parsed += info.st_size
            top = header(read_raw(entry), entry, Budget(redact), ids=False)
        except ReportError as error:
            row["error"] = {"code": error.code, "message": str(error)}
        except OSError as error:
            row["error"] = {"code": "EVAL_REPORT_UNREADABLE", "message": _why(error)}
        else:
            row |= {k: top[k] for k in ("eval_set", "judge", "cases", "passed", "pass_rate")}
        rows.append(row)
    return rows, len(listed)
