"""MCP relay tool calls in the hash-chained audit log (#1068).

`service/mcp-relay@1.0.2` appends one JSON line per `tools/call` from an authenticated
deployment, forwarded or refused, to `<stack>/relay-audit/<env>/<server>.jsonl` (bind-mounted
at `/run/audit`). This module imports those lines into the deploy state of that environment
(`<stack>/envs/<env>/state`) through `audit.append`, as `mcp_tool_call` rows. `audit list` and
`audit verify` import before they read, so a call shows up the next time the log is read.

What a row holds: the correlation id (also in the relay's own log line), the deployment, agent,
server and tool, the relay's decision (`allow`, or a refusal code such as `TOOL_NOT_GRANTED`) and
the time taken. Never arguments, results or credentials; an ungranted tool's name appears only as
the relay's 16-character sha256 prefix (`tool` is null).

Trust: the file is written by the relay container (the operator's uid), not by agents, but every
line is still validated: a line that is not exactly the relay's record shape is not imported, and
one `mcp_relay_record` row counts the lines refused. A refusal before the relay reads the request
(a bad token, too many concurrent requests, a malformed body) has no deployment and is only in the
relay's log.

Import is at-least-once read and exactly-once write: a cursor file (`relay-audit-cursor.json` in
the deploy state directory) remembers how far each file was read, and inside the same
IMMEDIATE transaction a record whose id is already in the chain is skipped.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agentbreeder import registry
from agentbreeder.audit import AuditEvent
from agentbreeder.audit import append as audit_append

DIRECTORY = "relay-audit"
CURSOR_FILE = "relay-audit-cursor.json"
ACTION = "mcp_tool_call"
INVALID_ACTION = "mcp_relay_record"
LINE_MAX = 4096  # the relay's AUDIT_LINE_MAX
READ_MAX = 4 * 1024 * 1024  # bytes per file per import; the rest waits for the next read
FILES_MAX = 64
KEYS = frozenset(
    {
        "v",
        "id",
        "ts",
        "relay",
        "server",
        "environment",
        "deployment",
        "agent",
        "tool",
        "tool_sha256",
        "decision",
        "ms",
    }
)
HEX32 = re.compile(r"[0-9a-f]{32}")
HEX16 = re.compile(r"[0-9a-f]{16}")
VERSION = re.compile(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}")
SERVER = re.compile(r"[a-z][a-z0-9_]{0,31}")  # stack.yaml MCP_SERVER
ENVIRONMENT = re.compile(r"[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?")
TOOL = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")  # mcp.TOOL
CODE = re.compile(r"[A-Z][A-Z0-9_]{2,63}")
MESSAGES = {
    "TOOL_NOT_GRANTED": "The tool is not granted to this deployment",
    "TOOL_DEFINITION_CHANGED": "The tool's definition changed upstream since it was pinned",
    "TOOL_MISSING_UPSTREAM": "The tool is missing upstream",
    "TOOL_NOT_PINNED": "The tool's definition isn't pinned",
    "RATE_LIMITED": "Too many calls in the last minute",
    "AUDIT_UNAVAILABLE": "The relay couldn't open its audit file, so the call wasn't forwarded",
    "UPSTREAM_ERROR": "The upstream MCP server was unavailable",
    "UPSTREAM_INVALID": "The upstream MCP server sent no valid response",
}


@dataclass
class ImportReport:
    imported: int = 0
    invalid: int = 0
    files: list[str] = field(default_factory=list)


def location(state_dir: Path) -> tuple[Path, str] | None:
    """(the environment's relay-audit directory, the environment) for a stack's deploy state
    (`<stack>/envs/<env>/state`); None for any other state directory."""
    state_dir = Path(state_dir)
    environment = state_dir.parent.name
    if state_dir.name != "state" or state_dir.parent.parent.name != "envs":
        return None
    if not ENVIRONMENT.fullmatch(environment):
        return None
    return state_dir.parent.parent.parent / DIRECTORY / environment, environment


def _owned_dir(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()


def _files(directory: Path) -> list[tuple[str, Path]]:
    if not _owned_dir(directory):
        return []
    found = []
    for entry in sorted(os.listdir(directory))[: FILES_MAX * 4]:
        server, dot, suffix = entry.rpartition(".")
        if not dot or suffix != "jsonl" or not SERVER.fullmatch(server):
            continue
        path = directory / entry
        try:
            info = path.lstat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid():
            found.append((server, path))
    return found[:FILES_MAX]


def _read_cursor(state_dir: Path) -> dict:
    try:
        data = json.loads((state_dir / CURSOR_FILE).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cursor(state_dir: Path, cursor: dict) -> None:
    target = state_dir / CURSOR_FILE
    scratch = state_dir / (CURSOR_FILE + ".tmp")
    fd = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(cursor, handle, sort_keys=True)
    os.replace(scratch, target)


def _chunk(path: Path, position: dict) -> tuple[bytes, int, int]:
    """New bytes from the recorded offset: (data, start offset, inode). A replaced or shorter
    file starts again from 0 (ids already in the chain are skipped)."""
    # O_NONBLOCK: a FIFO swapped in after the listing can't hang the read.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("not a regular file")
        offset = position.get("offset") if isinstance(position, dict) else None
        if (
            type(offset) is not int
            or offset < 0
            or position.get("inode") != info.st_ino
            or offset > info.st_size
        ):
            offset = 0
        os.lseek(fd, offset, os.SEEK_SET)
        data = os.read(fd, READ_MAX)
        return data, offset, info.st_ino
    finally:
        os.close(fd)


def parse(line: bytes, server: str, environment: str) -> dict | None:
    """One relay record, or None when it is not exactly the relay's shape."""
    if len(line) > LINE_MAX:
        return None
    try:
        record = json.loads(line.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        return None
    if not isinstance(record, dict) or set(record) != KEYS:
        return None
    ts, ms, tool, digest = record["ts"], record["ms"], record["tool"], record["tool_sha256"]
    checks = (
        record["v"] == 1 and type(record["v"]) is int,
        _matches(HEX32, record["id"]),
        type(ts) in (int, float) and math.isfinite(ts) and 0 < ts < 32503680000,
        _matches(VERSION, record["relay"]),
        record["server"] == server,
        record["environment"] == environment,
        _matches(HEX32, record["deployment"]),
        _matches(registry.NAME, record["agent"]),
        record["decision"] == "allow" or _matches(CODE, record["decision"]),
        type(ms) is int and 0 <= ms <= 10_000_000,
    )
    if not all(checks):
        return None
    if tool is None:  # an ungranted name: only its hash, and never allowed
        if not _matches(HEX16, digest) or record["decision"] == "allow":
            return None
    elif not (_matches(TOOL, tool) and digest is None):
        return None
    return record


def _matches(pattern: re.Pattern, value) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def event(record: dict) -> AuditEvent:
    decision = record["decision"]
    return AuditEvent(
        attempt_id=f"mcp-{record['id']}",
        action=ACTION,
        decision="allow" if decision == "allow" else "deny",
        # The relay authenticated the deployment by its token; no person or IdP is involved.
        actor=record["agent"],
        actor_verified=False,
        enforcement_mode="enforce",
        deployment_id=record["deployment"],
        reasons=(
            []
            if decision == "allow"
            else [{"code": decision, "message": MESSAGES.get(decision, "Refused by the relay")}]
        ),
        context={
            "correlation_id": record["id"],
            "server": record["server"],
            "tool": record["tool"],
            "tool_sha256": record["tool_sha256"],
            "environment": record["environment"],
            "relay": f"service/mcp-relay@{record['relay']}",
            "relay_at": datetime.fromtimestamp(record["ts"], UTC).isoformat(),
            "ms": record["ms"],
        },
    )


def import_records(store) -> ImportReport:
    """Import new relay records for this deploy state. A no-op outside a stack's environment.
    Raises sqlite3.Error/OSError only for the store; unreadable relay files are skipped."""
    report = ImportReport()
    found = location(store.directory)
    if found is None:
        return report
    directory, environment = found
    files = _files(directory)
    if not files:
        return report
    with store.transaction():
        cursor = _read_cursor(store.directory)
        known = {
            row[0]
            for row in store.db.execute(
                "SELECT attempt_id FROM audit WHERE action IN (?, ?)", (ACTION, INVALID_ACTION)
            )
        }
        for server, path in files:
            try:
                data, start, inode = _chunk(path, cursor.get(server, {}))
            except OSError:
                continue
            end = data.rfind(b"\n") + 1  # only whole lines; a partial last line waits
            lines = data[:end].split(b"\n")[:-1] if end else []
            if not end and len(data) >= READ_MAX:
                end, lines = len(data), [data]  # one line longer than READ_MAX: refuse it
            invalid: list[bytes] = []
            for line in lines:
                record = parse(line, server, environment)
                if record is None:
                    invalid.append(line)
                    continue
                attempt = f"mcp-{record['id']}"
                if attempt in known:
                    continue
                audit_append(store.db, event(record))
                known.add(attempt)
                report.imported += 1
            if invalid:
                invalid_event = _invalid_event(server, environment, invalid, start)
                if invalid_event.attempt_id not in known:  # the same lines read again
                    report.invalid += len(invalid)
                    audit_append(store.db, invalid_event)
                    known.add(invalid_event.attempt_id)
            if end:
                report.files.append(server)
            cursor[server] = {"inode": inode, "offset": start + end}
    try:
        _write_cursor(store.directory, cursor)
    except OSError:
        pass  # the next import re-reads; ids already imported are skipped
    return report


def _invalid_event(server: str, environment: str, lines: list[bytes], offset: int) -> AuditEvent:
    """One row for the refused lines; its id is their content's hash, so the same lines read
    again (a lost cursor) are recorded once. The lines themselves are never stored."""
    count = len(lines)
    digest = hashlib.sha256(server.encode() + b"\0" + b"\n".join(lines)).hexdigest()[:32]
    return AuditEvent(
        attempt_id=f"mcp-invalid-{digest}",
        action=INVALID_ACTION,
        decision="rejected",
        actor="agentbreeder",
        actor_verified=False,
        enforcement_mode="enforce",
        reasons=[
            {
                "code": "RELAY_RECORD_INVALID",
                "message": f"{count} line(s) of {server}.jsonl were not relay records",
            }
        ],
        context={"server": server, "environment": environment, "offset": offset},
    )


def safe_import(store) -> tuple[ImportReport | None, str | None]:
    """`import_records`, with a store failure turned into a warning: reading the log must
    still work when an import can't be written (a busy or read-only state)."""
    try:
        return import_records(store), None
    except (sqlite3.Error, OSError):
        return None, "MCP relay records could not be imported now; they will be on the next read"
