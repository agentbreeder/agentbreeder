"""One redactor for every log and child stream the CLI prints (guided §14.3 #2, §14.9 R2; S-09).

`redact(text, values)` is pure and shared by the CLI and the console. Each value is replaced in
these forms: literal; base64 (standard and URL-safe, with or without padding, of the value and
of the value plus a newline, and the part of it that survives when the value sits inside a
larger base64 string at any of the three byte alignments, e.g. `Basic base64(user:secret)`);
URL-quoted (`quote` and `quote_plus`, upper- and lower-case hex); and JSON-escaped. Each line of
a multi-line value (a PEM key) of at least 8 characters is also replaced on its own. Matches of
every pattern are merged into spans and replaced in one pass, so overlapping values leave no
suffix behind. Independently of the values, the password of any `scheme://user:password@host`
(the user may be empty) is replaced. All of it runs in time linear in the text for a given set
of values.

It is best effort: a value transformed another way (hashed, split across lines, compressed,
re-encoded twice) is not found. Known limits of the URL rule: only `scheme://` URLs (not
`user:password@host` without a scheme, nor DSNs like `host=... password=...`); the authority
ends at whitespace or a quote, so a password containing those is cut there; when no `@` comes
before the first `/`, `?` or `#`, the last `@` in the authority ends the userinfo unless the text
before the first `/` is `host[:port]` and a `?` or `#` follows it (a query's `@`), so text such as
`http://svc:4000/x@y` is redacted (`4000/x`) as the ambiguous case; an IPv6 literal host
(`[::1]`) is only handled when an `@` before the first `/` marks the userinfo.

`collect(state_dir, ...)` / `redaction_values(...)` are CLI-only: they gather values from local
state (the stack's stored secrets, every non-removed deployment's 0444 secret copies in every
environment of the stack, and the `<state>/secrets` values) and report what they could not read.
The console never calls them: it holds no secret and opens no deployment state.
"""

from __future__ import annotations

import base64
import bisect
import json
import os
import re
import sqlite3
import stat
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import quote, quote_plus

MARK = "[redacted]"
MIN_LITERAL = 4  # as `services logs` redacted before; shorter values match too much
MIN_LINE = 8  # a line of a multi-line value
MIN_CORE = 8  # an unaligned base64 fragment shorter than this matches too much
URL_WINDOW = 2048  # characters after `://` searched for `user:password@`
_URL_STOP = re.compile(r"[\s\"'<>`]")
_SCHEME_TAIL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{0,31}$")
_PERCENT = re.compile(r"%[0-9A-F]{2}")
_SCHEME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+.-")
_AT = re.compile("@")
_COLON = re.compile(":")
_DELIMITER = re.compile(r"[/?#]")
_QUERY = re.compile(r"[?#]")
_SLASH = re.compile("/")
_HOSTISH = re.compile(r"[A-Za-z0-9\[]")
_HOST_PORT = re.compile(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?")  # ASCII digits only
_HOST_MAX = 260
_IPV6_HOST = re.compile(r"\[[0-9A-Fa-f:.]+\](?::[0-9]{1,5})?")


def _base64_forms(raw: bytes) -> set[str]:
    found: set[str] = set()
    for data in (raw, raw + b"\n"):
        encoded = base64.b64encode(data).decode()
        found |= {encoded, encoded.rstrip("=")}
    # Inside a longer base64 string the value starts at byte offset 0, 1 or 2 of a 3-byte
    # group: only the characters made purely of its bits are stable.
    for offset in (0, 1, 2):
        encoded = base64.b64encode(b"\0" * offset + raw).decode()
        start = (8 * offset + 5) // 6
        end = (8 * (offset + len(raw))) // 6
        core = encoded[start:end]
        if len(core) >= MIN_CORE:
            found.add(core)
    return found | {form.translate(str.maketrans("+/", "-_")) for form in found}


def _forms(value: str) -> set[str]:
    raw = value.encode("utf-8", "surrogateescape")
    quoted = {quote(value, safe=""), quote_plus(value)}
    quoted |= {_PERCENT.sub(lambda m: m.group().lower(), form) for form in quoted}
    return (
        {value, json.dumps(value)[1:-1], json.dumps(value, ensure_ascii=False)[1:-1]}
        | quoted
        | _base64_forms(raw)
    )


def patterns(values: Iterable[bytes | str]) -> list[str]:
    """Every string `redact` replaces for these values, longest first."""
    found: set[str] = set()
    for item in values:
        text = item.decode("utf-8", "replace") if isinstance(item, bytes) else str(item)
        text = text.strip()
        if len(text) < MIN_LITERAL:
            continue
        found |= _forms(text)
        lines = [line.strip() for line in text.splitlines()]
        if len(lines) > 1:
            for line in lines:
                if len(line) >= MIN_LINE:
                    found |= _forms(line)
    return sorted((p for p in found if len(p) >= MIN_LITERAL), key=len, reverse=True)


def _positions(pattern: re.Pattern, text: str) -> list[int]:
    return [match.start() for match in pattern.finditer(text)]


def _first(positions: list[int], low: int, high: int) -> int | None:
    """The first position in [low, high), or None."""
    index = bisect.bisect_left(positions, low)
    return positions[index] if index < len(positions) and positions[index] < high else None


def _last(positions: list[int], low: int, high: int) -> int | None:
    """The last position in [low, high), or None."""
    index = bisect.bisect_left(positions, high) - 1
    return positions[index] if index >= 0 and positions[index] >= low else None


def _url_password_spans(text: str) -> list[tuple[int, int]]:
    """Password spans of `scheme://[user]:password@host` (everything after the userinfo's
    first `:` up to the `@` that ends it).

    The authority runs from `://` to the first whitespace or quote, at most URL_WINDOW
    characters. Normally the userinfo ends at the last `@` before the first `/`, `?` or `#`
    (so `user:p@ss@host` is covered whole). With no `@` there, the last `@` in the authority
    ends the userinfo (a password may contain `/`, `?` or `#`), except when:
    - the text before the first delimiter is exactly a bracketed IPv6 literal with an optional
      port (`[::1]:8080/x@y`), or
    - what follows that `@` cannot start a host (`[A-Za-z0-9[]`), or
    - the text before the first `/` is exactly an ASCII `host[:port]` and a `?` or `#` follows
      that `/` (`http://svc:4000/v1?user=a@b.com`: the `@` is in the query).
    Ambiguous text such as `http://svc:4000/x@y` is redacted. Linear: positions of `@ : / ? #`
    are listed once and looked up with bisect, the bracket and host checks are bounded, the next
    stop is found incrementally, and the search resumes after each match."""
    if "://" not in text:
        return []
    ats = _positions(_AT, text)
    colons = _positions(_COLON, text)
    delimiters = _positions(_DELIMITER, text)
    queries = _positions(_QUERY, text)
    slashes = _positions(_SLASH, text)
    spans: list[tuple[int, int]] = []
    position, stop = 0, -1
    while True:
        marker = text.find("://", position)
        if marker < 0:
            return spans
        start = marker + 3
        position = start
        if marker == 0 or text[marker - 1] not in _SCHEME_CHARS:
            continue
        if not _SCHEME_TAIL.search(text, max(0, marker - 32), marker):
            continue
        if stop < start:
            found = _URL_STOP.search(text, start)
            stop = found.start() if found else len(text)
        end = min(stop, start + URL_WINDOW)
        delimiter = _first(delimiters, start, end)
        at = _last(ats, start, delimiter) if delimiter is not None else None
        if at is None:
            # No `@` before the first delimiter: a password may contain `/`, `?` or `#`.
            at = _last(ats, start, end)
            if at is None:
                continue
            authority_end = delimiter if delimiter is not None else end
            if authority_end - start <= _HOST_MAX and _IPV6_HOST.fullmatch(
                text, start, authority_end
            ):
                continue  # `[::1]:8080/x@y`: an IPv6 literal host, then a path
            if not _HOSTISH.match(text, at + 1):
                continue  # what follows the `@` cannot be a host
            slash = _first(slashes, start, at)
            if slash is not None and _first(queries, slash, at) is not None:
                # `host[:port]/path?query@...`: the `@` is in the query, unless what comes
                # before the first `/` cannot be a host (then it is userinfo: redact).
                if slash - start <= _HOST_MAX and _HOST_PORT.fullmatch(text, start, slash):
                    continue
        colon = _first(colons, start, at)
        if colon is not None and colon + 1 < at:
            spans.append((colon + 1, at))
        position = at + 1


def redact(text: str, values: Iterable[bytes | str]) -> str:
    return Redactor(values)(text)


class Redactor:
    """`redact` with the patterns computed once, for a stream redacted line by line
    (`logs --follow`). Calling it is the same as `redact(text, values)`."""

    def __init__(self, values: Iterable[bytes | str]):
        self.patterns = patterns(values)

    def __call__(self, text: str) -> str:
        return _apply(text, self.patterns)


def _apply(text: str, compiled: list[str]) -> str:
    spans = _url_password_spans(text)
    for pattern in compiled:
        start = text.find(pattern)
        while start >= 0:
            spans.append((start, start + len(pattern)))
            start = text.find(pattern, start + 1)
    if not spans:
        return text
    merged: list[list[int]] = []
    for start, stop in sorted(spans):
        if merged and start <= merged[-1][1]:  # overlaps or touches: one span
            merged[-1][1] = max(merged[-1][1], stop)
        else:
            merged.append([start, stop])
    out, cursor = [], 0
    for start, stop in merged:
        out += [text[cursor:start], MARK]
        cursor = stop
    out.append(text[cursor:])
    return "".join(out)


# -- CLI-only: collecting values ---------------------------------------------------------------

READ_LIMIT = 64 * 1024  # the largest secret `agentbreeder secret set` accepts


def read_copy(path: Path) -> bytes | None:
    """A deployment's secret copy (mode 0444 so the agent's uid can read the bind mount):
    opened with O_NOFOLLOW, a regular file you own inside a 0700 directory you own. None when
    it is missing or anything about it is unexpected. A file over READ_LIMIT keeps its whole
    lines only (a value cut mid-line would be redacted as a fragment)."""
    try:
        parent = path.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or parent.st_mode & 0o077
        ):
            return None
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            return None
        data = handle.read(READ_LIMIT + 1)
    if len(data) > READ_LIMIT:
        data = data[:READ_LIMIT]
        data = data[: data.rfind(b"\n") + 1] if b"\n" in data else b""
    return data or None


class Collected:
    """Values to redact, and what could not be read (redaction is then incomplete)."""

    def __init__(self) -> None:
        self.values: list[bytes] = []
        self.problems: list[str] = []

    @property
    def complete(self) -> bool:
        return not self.problems


def _files(directory: Path, found: Collected, what: str) -> None:
    try:
        info = directory.lstat()
    except FileNotFoundError:
        return
    except OSError:
        found.problems.append(f"cannot read {what}")
        return
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        found.problems.append(f"{what} is not a directory you own")
        return
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        found.problems.append(f"cannot list {what}")
        return
    for name in names:
        if name.startswith("."):
            continue
        data = read_copy(directory / name)
        if data:
            found.values.append(data)
            try:
                if (directory / name).lstat().st_size > READ_LIMIT:  # only whole lines were kept
                    found.problems.append(f"{what}/{name} is larger than {READ_LIMIT} bytes")
            except OSError:
                found.problems.append(f"cannot read {what}/{name}")
        else:
            found.problems.append(f"cannot read {what}/{name}")


def deployment_copies(state_dir: Path, identifier: str, found: Collected) -> None:
    if re.fullmatch(r"[0-9a-f]{32}", identifier):
        _files(state_dir / "deployments" / identifier / "secrets", found, "a deployment's secrets")


def _deployment_values(state_dir: Path, found: Collected, store=None) -> None:
    """Copies of every non-removed deployment, plus `<state>/secrets` (the values those
    copies are made from). `store` is an already-open Store for this state directory."""
    from agentbreeder.store import DeploymentError, Store

    _files(state_dir / "secrets", found, "local secrets")
    if store is None and not (state_dir / "deployments.sqlite3").exists():
        return
    opened = None
    try:
        if store is None:
            store = opened = Store.open_readonly(state_dir)
        identifiers = [row["id"] for row in store.active_all()]
    except (DeploymentError, OSError, sqlite3.Error) as error:
        code = getattr(error, "code", "error")
        if code == "STATE_NEEDS_MIGRATION":
            from agentbreeder import hints

            migrate = hints.command("migrate", state_dir=state_dir)
            found.problems.append(f"the state at {state_dir} needs an upgrade: run `{migrate}`")
        else:
            found.problems.append(f"cannot list deployments in {state_dir} ({code})")
        return
    finally:
        if opened is not None:
            opened.close()
    for identifier in identifiers:
        deployment_copies(state_dir, identifier, found)


def stack_directory_for(state_dir: Path) -> Path | None:
    """The stack directory when `state_dir` is a stack's `envs/<env>/state` (`--stack`)."""
    candidate = state_dir.absolute()
    if candidate.name == "state" and candidate.parent.parent.name == "envs":
        stack = candidate.parent.parent.parent
        if (stack / "stack.db").is_file():
            return stack
    return None


def collect(state_dir: Path | None, stack: Path | None = None, *, store=None) -> Collected:
    """Values to redact from output about local deployments. `stack` is a stack's state
    directory (`~/.agentbreeder/stacks/<id>`); its stored secrets and the deployments of every
    environment it wrote are included. `store`, an open Store for `state_dir`, is used instead
    of opening it again. Never raises: what it cannot read is listed in `problems`."""
    from agentbreeder.services.schema import ServiceError
    from agentbreeder.services.state import StackState, written_environments

    found = Collected()
    states: list[Path] = [state_dir] if state_dir is not None else []
    if stack is None and state_dir is not None:
        stack = stack_directory_for(state_dir)
    if stack is not None:
        try:
            opened = StackState.open_directory(stack)
            if opened is not None:
                try:
                    found.values += opened.known_secrets(found.problems)
                finally:
                    opened.close()
            states += [stack / "envs" / env / "state" for env in written_environments(stack)]
        except (ServiceError, OSError, sqlite3.Error):
            found.problems.append("cannot read the stack's stored secrets")
    seen: set[Path] = set()
    for directory in states:
        resolved = directory.absolute()
        if resolved in seen:
            continue
        seen.add(resolved)
        own = store if state_dir is not None and resolved == state_dir.absolute() else None
        _deployment_values(resolved, found, own)
    return found


def redaction_values(state_dir: Path | None, stack: Path | None = None) -> list[bytes]:
    """`collect(...).values`: best effort, for output that is shown, not stored."""
    return collect(state_dir, stack).values
