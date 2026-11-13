"""Log levels for `logs ID --level` and `services logs --level` (guided spec §10, §14.4).

A heuristic filter and highlight, never a notification source. Levels are read per known
format, keyed by built-in ref: Postgres (`service/postgres-pgvector`), the Python servers
(`service/litellm`, `service/phoenix`) and the agent runtime (`runtime/adk`). Project services,
MCP relays and images not built from a known runtime stay unclassified (`None`).

A record is a header line and the lines after it; those continuation lines take the header's
level (a traceback stays with its `ERROR` line, Postgres `DETAIL`/`STATEMENT` lines with their
`ERROR`, a multi-line SQL statement with its `INFO` line). A blank line ends the record unless
the next line clearly continues it (indented, or a chained traceback's "During handling …").
Lines arrive one at a time (`--follow`), so a line's level is decided when it is read.
"""

from __future__ import annotations

import re

LEVELS = ("debug", "info", "warning", "error")
CHOICES = ("warning", "error")  # what --level accepts: show this level and above

# The format for each built-in ref name (`registry.Ref.parse(ref).kind`/`.name`).
FORMATS = {
    ("service", "postgres-pgvector"): "postgres",
    ("service", "litellm"): "python",
    ("service", "phoenix"): "python",
    ("runtime", "adk"): "adk",
}

# Docker's `--timestamps` prefix, which every line read by `logs` and `services logs` carries.
_DOCKER_TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z ")

_PYTHON_WORDS = {
    "DEBUG": "debug",
    "INFO": "info",
    "WARNING": "warning",
    "WARN": "warning",
    "ERROR": "error",
    "CRITICAL": "error",
    "FATAL": "error",
}
_WORD = r"(?P<word>DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)"
_PYTHON_HEADERS = (
    # LiteLLM: `02:24:14 - LiteLLM Proxy:ERROR: file.py:5109 - …`
    re.compile(rf"^\d\d:\d\d:\d\d - [^:\n]{{1,60}}:{_WORD}: "),
    # Uvicorn: `INFO:     Started server process [1]`; logging's default `ERROR:root:…`
    re.compile(rf"^{_WORD}:(?:\s|[\w.]+:)"),
    # logging with asctime: `2026-11-13 02:23:54,573 INFO sqlalchemy.engine.Engine …`
    re.compile(rf"^\d{{4}}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:[,.]\d+)?\s+(?:- )?\[?{_WORD}\b"),
)
# `warnings.warn` output: `/path/x.py:2666: PydanticJsonSchemaWarning: …`, `<sys>:0: SAWarning: …`
_PY_WARNING = re.compile(r"^\S+:\d+: [A-Z]\w*Warning: ")
_TRACEBACK = "Traceback (most recent call last):"
# Lines that continue a Python record even after a blank line (a chained traceback).
_PY_CONTINUES = (
    "During handling of the above exception",
    "The above exception was the direct cause",
    _TRACEBACK,
)
# The ADK runtime's own messages (`server.py`, `agent.py`): plain prints, each a failure.
_ADK_FAILURE = re.compile(r"^(?:runtime warm-up|turn record|closing MCP sessions) failed: ")

# Postgres: `2026-11-13 02:22:53.379 UTC [131] ERROR:  …` at the start of a line, or right
# after the image's init-script progress dots (`waiting for server to start....2026-…`), never
# elsewhere: a logged statement's own text cannot start a record.
_PG_HEADER = re.compile(
    r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)? [A-Za-z0-9+-]{1,10} \[\d+\] (?P<word>[A-Z0-9]+):  "
)
_PG_PROGRESS = re.compile(r"^[a-z][a-z ]{0,60}\.{2,}")
_PG_WORDS = {
    "LOG": "info",
    "INFO": "info",
    "NOTICE": "info",
    "WARNING": "warning",
    "ERROR": "error",
    "FATAL": "error",
    "PANIC": "error",
}
_PG_CONTINUES = frozenset({"DETAIL", "HINT", "STATEMENT", "CONTEXT", "QUERY", "LOCATION"})
# The image's init tools: `initdb: warning: …`, `initdb: hint: …`
_PG_TOOL = re.compile(r"^(?:initdb|pg_ctl|postgres|psql): (?P<word>[a-z]+): ")
_PG_TOOL_WORDS = {"warning": "warning", "error": "error", "fatal": "error"}

_CONTINUE = object()  # a header that continues the current record (Postgres DETAIL, …)


def _python(text: str, adk: bool):
    for pattern in _PYTHON_HEADERS:
        match = pattern.match(text)
        if match:
            return _PYTHON_WORDS[match["word"]]
    if _PY_WARNING.match(text):
        return "warning"
    if text.startswith(_TRACEBACK):
        return "error"
    if adk and _ADK_FAILURE.match(text):
        return "error"
    return None


def _postgres(text: str):
    progress = _PG_PROGRESS.match(text)
    match = _PG_HEADER.match(text, progress.end() if progress else 0)
    if match:
        word = match["word"]
        if word in _PG_CONTINUES:
            return _CONTINUE
        if word.startswith("DEBUG"):
            return "debug"
        return _PG_WORDS.get(word, "info")
    match = _PG_TOOL.match(text)
    if match:
        word = match["word"]
        return _PG_TOOL_WORDS[word] if word in _PG_TOOL_WORDS else _CONTINUE
    return None


class Classifier:
    """Feed lines in order; each returns the level of the record the line belongs to."""

    def __init__(self, fmt: str | None):
        if fmt not in (None, "postgres", "python", "adk"):
            raise ValueError(f"unknown log format {fmt!r}")
        self.format = fmt
        self.level: str | None = None  # the open record's level; None when no record is open
        self.blank = False  # a blank line since the last line of the open record

    def feed(self, line: str) -> str | None:
        if self.format is None:
            return None
        text = _DOCKER_TIME.sub("", line, count=1)
        if not text.strip():
            self.blank = self.level is not None
            return self.level
        if self.format == "postgres":
            header = _postgres(text)
        else:
            header = _python(text, adk=self.format == "adk")
        if header is _CONTINUE:
            self.blank = False
            return self.level
        if header is not None:
            self.level, self.blank = header, False
            return header
        if self.level is not None and (
            not self.blank or text[0] in " \t" or text.startswith(_PY_CONTINUES)
        ):
            self.blank = False
            return self.level
        self.level, self.blank = None, False  # a plain line after a blank: the record ended
        return None


def classify(fmt: str | None, lines: list[str]) -> list[str | None]:
    classifier = Classifier(fmt)
    return [classifier.feed(line) for line in lines]


def shown(level: str | None, threshold: str | None) -> bool:
    """Whether `--level threshold` keeps a line of `level` (no threshold keeps every line)."""
    if threshold is None:
        return True
    return level is not None and LEVELS.index(level) >= LEVELS.index(threshold)


def format_for(ref: str | None, builtin: bool) -> str | None:
    """The format of a built-in ref (`service/litellm@1.0.0`, `runtime/adk@1.0.6`), else None."""
    from agentbreeder import registry

    if not ref or not builtin:
        return None
    try:
        parsed = registry.Ref.parse(ref)
    except registry.RegistryError:
        return None
    return FORMATS.get((parsed.kind, parsed.name))
