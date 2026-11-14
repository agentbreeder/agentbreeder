"""Recent agent traces per deployment, read from a stack's Phoenix (#907).

Where they come from: a stack's `service/phoenix@*` service (`services up`), on its loopback host
port, read with the stack's stored `ingest-key` (the system API key Phoenix's bootstrap mints).
That key is admin-equivalent in Phoenix (it can also write): it is only sent to that loopback
URL, only with GET, never printed, and Phoenix's redirects are not followed. A VIEWER key is a
planned follow-up (phoenix@1.1.0).

Which deployment a trace belongs to: runtime/adk names the Phoenix project after the agent
(`openinference.project.name` = the agent name, so `X-candidate` deployments share project `X`).
A runtime that is given `AGENTBREEDER_DEPLOYMENT_ID` (set by deploy) and records it as the
`agentbreeder.deployment_id` attribute of its root span is attributed exactly. Otherwise a
deployment's traces are those of its project between its creation and its removal (or now):
`attribution: "window"`, and every other deployment of the same project whose lifetime overlaps
is listed, because their traces can't be told apart. A deployment's runtime never changes, so
either all of its traces carry the attribute or none do.

What is shown: the span tree (name, kind, status, latency), models, token counts and tool calls.
Message content (`input.value`, `output.value`, retrieved documents) is never read into the
output: it holds customer messages. Every string Phoenix returns is untrusted: it is redacted
with the CLI's stored-secret values (as `logs`), flattened to one line without control
characters and cut (`safe_text.excerpt`).
"""

from __future__ import annotations

import http.client
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from time import monotonic
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from agentbreeder import safe_text

SCHEMA_VERSION = 1
ATTRIBUTE = "agentbreeder.deployment_id"
CANDIDATE_SUFFIX = "-candidate"
PHOENIX_REF = "service/phoenix@"
INGEST_KEY = "ingest-key"
PORT = "http"
DEFAULT_LAST = 20
MAX_LAST = 200
MAX_SPANS = 500  # per trace; more are dropped and the trace is marked
MAX_PAGES = 20  # per listing; more and the result is marked truncated
PAGE = 1000  # Phoenix's maximum page size
BATCH = 20  # trace ids per spans request (the query string stays short)
WINDOW_FACTOR = 3  # window mode reads this many times --last, as others' traces are dropped
BODY_LIMIT = 16 * 1024 * 1024
TIMEOUT = 10
MAX_CALLS = 400  # HTTP requests per command: a bound on time (each has TIMEOUT) and load
CURSOR_LIMIT = 512
DEADLINE = 120  # seconds for all of a command's requests (checked before each one)
TEXT = 200
ERROR_TEXT = 300
HEX32 = re.compile(r"[0-9a-f]{32}")
HEX16 = re.compile(r"[0-9a-f]{16}")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_PROJECT = re.compile(r"[^/?#\x00-\x1f\x7f\ud800-\udfff]{1,200}")


class TracesError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # never resend the bearer elsewhere
        return None


# No proxy, whatever HTTP_PROXY says: the bearer goes to the loopback URL and nowhere else.
_OPENER = build_opener(ProxyHandler({}), _NoRedirect)


@dataclass
class Phoenix:
    url: str
    key: str = field(repr=False)
    calls: int = 0
    tagged: dict = field(default_factory=dict, repr=False)  # (project, deployment) -> bool
    started: float = field(default_factory=monotonic, repr=False)

    def get(self, path: str, query: list[tuple[str, str]] | None = None) -> dict | None:
        """GET a JSON object; None on 404 (an unknown project)."""
        target = self.url.rstrip("/") + path + ("?" + urlencode(query) if query else "")
        request = Request(target, headers={"authorization": f"Bearer {self.key}"}, method="GET")
        if monotonic() - self.started > DEADLINE:
            raise TracesError(
                "TRACES_LIMIT",
                f"Phoenix took more than {DEADLINE} s in all; pass a deployment ID, a smaller "
                "--last or a later --since",
            )
        if self.calls >= MAX_CALLS:
            raise TracesError(
                "TRACES_LIMIT",
                f"more than {MAX_CALLS} Phoenix requests would be needed; pass a deployment ID, "
                "a smaller --last or a later --since",
            )
        self.calls += 1
        try:
            with _OPENER.open(request, timeout=TIMEOUT) as response:
                body = response.read(BODY_LIMIT + 1)
                status = response.status
        except HTTPError as error:
            if error.code == 404:
                return None
            if error.code in (401, 403):
                raise TracesError(
                    "TRACES_AUTH", f"Phoenix refused the stack's key (HTTP {error.code})"
                ) from None
            raise TracesError(
                "TRACES_PHOENIX_ERROR", f"Phoenix answered HTTP {error.code}"
            ) from None
        except (URLError, OSError, ValueError, http.client.HTTPException):
            raise TracesError(
                "TRACES_UNREACHABLE", "Phoenix did not answer; is the stack running?"
            ) from None
        if status != 200:  # a redirect that was not followed
            raise TracesError("TRACES_PHOENIX_ERROR", f"Phoenix answered HTTP {status}")
        if len(body) > BODY_LIMIT:
            raise TracesError("TRACES_PHOENIX_ERROR", "Phoenix's answer is too large")
        try:
            data = json.loads(body)
        except (ValueError, RecursionError):
            raise TracesError("TRACES_PHOENIX_ERROR", "Phoenix's answer is not JSON") from None
        if not isinstance(data, dict):
            raise TracesError("TRACES_PHOENIX_ERROR", "Phoenix's answer is not an object")
        return data

    def _pages(self, path: str, query: list[tuple[str, str]], limit: int) -> tuple[list, bool]:
        """Rows of a cursor-paginated listing, at most `limit` (and MAX_PAGES pages)."""
        rows: list = []
        cursor = None
        for _ in range(MAX_PAGES):
            page = [*query, ("limit", str(min(PAGE, limit - len(rows))))]
            data = self.get(path, page + ([("cursor", cursor)] if cursor else []))
            if data is None:
                return rows, False
            batch = data.get("data")
            if not isinstance(batch, list):
                raise TracesError("TRACES_PHOENIX_ERROR", "Phoenix's answer has no data list")
            rows += batch
            cursor = data.get("next_cursor")
            if isinstance(cursor, str) and len(cursor) > CURSOR_LIMIT:
                raise TracesError("TRACES_PHOENIX_ERROR", "Phoenix's page cursor is too long")
            more = isinstance(cursor, str) and bool(cursor)
            if len(rows) >= limit or not more:
                return rows[:limit], len(rows) > limit or (len(rows) >= limit and more)
        return rows, True

    def projects(self) -> set[str]:
        rows, _ = self._pages("/v1/projects", [], PAGE * MAX_PAGES)
        return {row["name"] for row in rows if isinstance(row, dict) and _text(row.get("name"))}

    def _project_path(self, project: str, tail: str) -> str:
        return f"/v1/projects/{quote(project, safe='')}/{tail}"

    def trace_ids(self, project: str, start: datetime, end: datetime | None, last: int):
        """Newest-first trace ids of `project` started in [start, end)."""
        query = [("start_time", start.isoformat()), ("order", "desc"), ("sort", "start_time")]
        if end is not None:
            query.append(("end_time", end.isoformat()))
        rows, _ = self._pages(self._project_path(project, "traces"), query, last)
        return [r["trace_id"] for r in rows if isinstance(r, dict) and _hex32(r.get("trace_id"))]

    def attributed_roots(
        self, project: str, deployment: str, start: datetime, end: datetime | None, limit: int
    ) -> list[dict]:
        """Root spans that carry this deployment's id, newest first."""
        query = [
            ("parent_id", "null"),
            ("attribute", f'{ATTRIBUTE}:"{deployment}"'),
            ("sort", "start_time"),
            ("order", "desc"),
            ("start_time", start.isoformat()),
        ]
        if end is not None:
            query.append(("end_time", end.isoformat()))
        rows, _ = self._pages(self._project_path(project, "spans"), query, limit)
        return [r for r in rows if isinstance(r, dict)]

    def spans(self, project: str, trace_ids: list[str]) -> tuple[dict[str, list[dict]], set]:
        """Spans of these traces by trace id, and the ids whose spans may be partial (their
        batch hit MAX_SPANS per trace or MAX_PAGES)."""
        result: dict[str, list[dict]] = {t: [] for t in trace_ids}
        partial: set[str] = set()
        for at in range(0, len(trace_ids), BATCH):
            batch = trace_ids[at : at + BATCH]
            query = [("trace_id", t) for t in batch]
            rows, more = self._pages(
                self._project_path(project, "spans"), query, MAX_SPANS * len(batch)
            )
            if more:
                partial.update(batch)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                context = row.get("context")
                trace = context.get("trace_id") if isinstance(context, dict) else None
                if _hex32(trace) and trace in result:
                    result[trace].append(row)
        return result, partial


def locate(project_dir, state_dir) -> Phoenix:
    """The stack's Phoenix: its loopback URL and stored ingest key (never printed)."""
    from agentbreeder import redaction
    from agentbreeder.services import engine
    from agentbreeder.services.schema import ServiceError
    from agentbreeder.services.state import StackState

    stack_dir = redaction.stack_directory_for(state_dir) if state_dir is not None else None
    if project_dir is None or stack_dir is None:
        raise TracesError(
            "TRACES_NO_PHOENIX", "traces are read from a stack's Phoenix: pass --stack DIR"
        )
    try:
        stack = engine.load(project_dir.resolve())
        names = sorted(
            name for name, entry in stack.services.items() if entry.ref.startswith(PHOENIX_REF)
        )
        if len(names) != 1:
            raise TracesError(
                "TRACES_NO_PHOENIX",
                "the stack has no service/phoenix service"
                if not names
                else "the stack has more than one service/phoenix service",
            )
        state = StackState.open_directory(stack_dir)
        if state is None:
            raise TracesError("TRACES_NO_PHOENIX", "the stack was never brought up")
        try:
            port = state.ports().get(names[0], {}).get(PORT)
            key = state.read_secret(names[0], INGEST_KEY)
        finally:
            state.close()
    except ServiceError as error:
        raise TracesError(error.code, safe_text.excerpt(str(error), 400)) from None
    if port is None:
        raise TracesError("TRACES_NO_PHOENIX", f"Phoenix ({names[0]}) has no host port yet")
    if not key:
        raise TracesError(
            "TRACES_NO_PHOENIX",
            f"Phoenix ({names[0]}) has no ingest key yet; run `services up` again",
        )
    try:
        token = key.decode("ascii").strip()
    except UnicodeError:
        raise TracesError("TRACES_NO_PHOENIX", "the stack's Phoenix key is malformed") from None
    if not token or any(c.isspace() for c in token):
        raise TracesError("TRACES_NO_PHOENIX", "the stack's Phoenix key is malformed")
    return Phoenix(f"http://127.0.0.1:{int(port)}", token)


# -- parsing ------------------------------------------------------------------------------------


def _text(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _hex32(value) -> bool:
    return isinstance(value, str) and bool(HEX32.fullmatch(value))


def _count(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = int(value)
    except (OverflowError, ValueError):
        return None
    return number if 0 <= number < 10**12 else None


def _when(value) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max(0, round((end - start).total_seconds() * 1000))


def since(value: str | None) -> datetime | None:
    """`--since`: a UTC day (YYYY-MM-DD) or an ISO date-time (UTC when it has no offset)."""
    if value is None:
        return None
    try:
        if _DAY.fullmatch(value):
            return datetime.combine(date.fromisoformat(value), time(), tzinfo=UTC)
        moment = datetime.fromisoformat(value) if len(value) <= 64 else None
    except ValueError:
        moment = None
    if moment is None:
        raise TracesError(
            "TRACES_SINCE", "--since must be a day (2026-11-13) or an ISO time (2026-11-13T09:00Z)"
        )
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class Shown:
    """Strings from Phoenix as they may be shown: redacted, one line, cut."""

    def __init__(self, redactor):
        self.redactor = redactor

    def __call__(self, value, limit: int = TEXT) -> str | None:
        text = _text(value)
        return None if text is None else safe_text.excerpt(self.redactor(text), limit)


def _span(row: dict, show: Shown) -> dict | None:
    context = row.get("context")
    span_id = context.get("span_id") if isinstance(context, dict) else None
    if not isinstance(span_id, str) or not HEX16.fullmatch(span_id):
        return None
    parent = row.get("parent_id")
    attributes = row.get("attributes")
    attributes = attributes if isinstance(attributes, dict) else {}
    start, end = _when(row.get("start_time")), _when(row.get("end_time"))
    status = row.get("status_code")
    status = status.lower() if status in ("OK", "ERROR", "UNSET") else "unset"
    kind = row.get("span_kind")
    kind = (
        kind.upper()[:20]
        if isinstance(kind, str) and kind.isascii() and kind.isalpha()
        else "UNKNOWN"
    )
    model = show(attributes.get("llm.model_name")) or show(attributes.get("gen_ai.request.model"))
    prompt = _count(attributes.get("llm.token_count.prompt"))
    completion = _count(attributes.get("llm.token_count.completion"))
    error = None
    if status == "error":
        error = show(row.get("status_message"), ERROR_TEXT) or show(
            attributes.get("tool.error"), ERROR_TEXT
        )
    elif _text(attributes.get("tool.error")):
        error = show(attributes.get("tool.error"), ERROR_TEXT)
    return {
        "span_id": span_id,
        "parent_id": parent if isinstance(parent, str) and HEX16.fullmatch(parent) else None,
        "name": show(row.get("name")) or "?",
        "kind": kind,
        "status": "error" if error is not None else status,
        "start_time": start.isoformat() if start else None,
        "latency_ms": _ms(start, end),
        "model": model,
        "tokens": None
        if prompt is None and completion is None
        else {"prompt": prompt or 0, "completion": completion or 0},
        "tool": show(attributes.get("tool.name")) if kind == "TOOL" else None,
        "error": error,
        "_end": end,
        "_start": start,
        "_attributes": attributes,
    }


def summarize(
    trace_id: str, project: str, rows: list[dict], show: Shown, partial: bool = False
) -> dict | None:
    """One trace of the --json shape, from its spans; None when no span could be read."""
    parsed, seen = [], set()
    for row in rows[:MAX_SPANS]:
        item = _span(row, show)
        if item is not None and item["span_id"] not in seen:  # a repeated id: the first wins
            seen.add(item["span_id"])
            parsed.append(item)
    if not parsed:
        return None
    epoch = datetime.min.replace(tzinfo=UTC)
    parsed.sort(key=lambda s: (s["_start"] or epoch, s["span_id"]))
    ids = {s["span_id"] for s in parsed}
    roots = [s for s in parsed if s["parent_id"] is None or s["parent_id"] not in ids]
    root = next((s for s in roots if s["parent_id"] is None), roots[0] if roots else parsed[0])
    attributes = root["_attributes"]
    starts = [s["_start"] for s in parsed if s["_start"]]
    ends = [s["_end"] for s in parsed if s["_end"]]
    start, end = (min(starts) if starts else None), (max(ends) if ends else None)
    marked = attributes.get(ATTRIBUTE)
    prompt = sum(s["tokens"]["prompt"] for s in parsed if s["tokens"])
    completion = sum(s["tokens"]["completion"] for s in parsed if s["tokens"])
    models: list[str] = []
    for s in parsed:
        if s["model"] and s["model"] not in models:
            models.append(s["model"])
    spans = [{k: v for k, v in s.items() if not k.startswith("_")} for s in parsed]
    return {
        "trace_id": trace_id,
        "project": show(project),
        "deployment_id": marked if _hex32(marked) else None,
        "attribution": "exact" if _hex32(marked) else None,
        "start_time": start.isoformat() if start else None,
        "end_time": end.isoformat() if end else None,
        "latency_ms": _ms(start, end),
        "status": "error" if any(s["status"] == "error" for s in spans) else "ok",
        "session_id": show(attributes.get("session.id")),
        "turn_id": show(attributes.get("agent.turn_id")),
        "models": models,
        "tokens": {"prompt": prompt, "completion": completion, "total": prompt + completion},
        "tool_calls": [
            {
                "name": s["tool"] or s["name"],
                "status": s["status"],
                "latency_ms": s["latency_ms"],
                "span_id": s["span_id"],
            }
            for s in spans
            if s["kind"] == "TOOL"
        ],
        "spans": spans,
        "spans_truncated": partial or len(rows) > MAX_SPANS,
    }


# -- deployments ------------------------------------------------------------------------------


def _project_for(name: str, known: set[str]) -> str | None:
    if name in known:
        return name
    base = name.removesuffix(CANDIDATE_SUFFIX)
    return base if base != name and base in known else None


# States whose container no longer runs: the lifetime ended at the last update.
ENDED = ("failed", "disabled")
# Deployments `traces` (no ID) lists: not removed, and a container ran.
LISTED = ("starting", "ready", "removing", "disabled")


def _lifetime(row: dict) -> tuple[datetime, datetime | None]:
    """[created, end): end is the removal, or the last update of a failed or disabled
    deployment; None while it runs. A deployment awaiting approval never ran (empty)."""
    start = _when(row.get("created")) or datetime.min.replace(tzinfo=UTC)
    state = row.get("state")
    if state == "pending_approval":
        return start, start
    end = None
    if state == "removed":
        end = _when(row.get("removed")) or _when(row.get("updated"))
    elif state in ENDED:
        end = _when(row.get("updated"))
    return start, end


def _overlaps(a: tuple[datetime, datetime | None], b: tuple[datetime, datetime | None]) -> bool:
    if a[0] == a[1] or b[0] == b[1]:
        return False  # one of them never ran
    far = datetime.max.replace(tzinfo=UTC)
    return a[0] < (b[1] or far) and b[0] < (a[1] or far)


def _alive_at(lifetime: tuple[datetime, datetime | None], moment: datetime) -> bool:
    return lifetime[0] <= moment and (lifetime[1] is None or moment < lifetime[1])


@dataclass
class Report:
    phoenix: str
    traces: list[dict] = field(default_factory=list)
    overlapping: list[str] = field(default_factory=list)
    truncated: bool = False
    notes: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "phoenix": self.phoenix,
            "traces": self.traces,
            "overlapping": self.overlapping,
            "truncated": self.truncated,
            "notes": self.notes,
        }


def _tags(phoenix: Phoenix, project: str, deployment: dict) -> bool:
    """Whether the deployment's runtime records its ID (one of its root spans carries it)."""
    key = (project, deployment["id"])
    if key not in phoenix.tagged:
        start, end = _lifetime(deployment)
        phoenix.tagged[key] = start != end and bool(
            phoenix.attributed_roots(project, deployment["id"], start, end, 1)
        )
    return phoenix.tagged[key]


def for_deployment(
    phoenix: Phoenix,
    deployment: dict,
    others: list[dict],
    known: set[str],
    show: Shown,
    last: int,
    after: datetime | None,
    report: Report,
) -> None:
    """Add the deployment's newest traces (at most `last`) to `report`."""
    project = _project_for(deployment["name"], known)
    if project is None:
        report.notes.append(
            f"{deployment['id']}: no Phoenix project {show(deployment['name'], 80)} "
            "(no traces yet, or `traces: none`)"
        )
        return
    lifetime = _lifetime(deployment)
    start = max(lifetime[0], after) if after else lifetime[0]
    end = lifetime[1]
    if end is not None and start >= end:
        report.notes.append(
            f"{deployment['id']}: never ran (awaiting approval)"
            if deployment.get("state") == "pending_approval"
            else f"{deployment['id']}: stopped before --since"
        )
        return
    key = (project, deployment["id"])
    whole = start == lifetime[0]  # no --since cut: a miss means the runtime doesn't tag
    roots = (
        []
        if whole and phoenix.tagged.get(key) is False
        else phoenix.attributed_roots(project, deployment["id"], start, end, last)
    )
    ids = [
        r["context"]["trace_id"]
        for r in roots
        if isinstance(r.get("context"), dict) and _hex32(r["context"].get("trace_id"))
    ]
    if ids or whole:
        phoenix.tagged[key] = bool(ids)
    if not ids and _tags(phoenix, project, deployment):
        return  # its runtime tags every trace: none of its traces are in this --since window
    if not ids:
        # Over-fetch: traces another deployment tagged are dropped below. More than asked
        # for is not truncation.
        ids = phoenix.trace_ids(project, start, end, min(last * WINDOW_FACTOR, PAGE))
    spans, partial = phoenix.spans(project, ids)
    window = False
    added = 0
    for trace_id in ids:
        if added == last:
            break
        trace = summarize(trace_id, project, spans.get(trace_id, []), show, trace_id in partial)
        if trace is None:
            continue
        marked = trace["deployment_id"]
        if marked is not None and marked != deployment["id"]:
            continue  # another deployment's trace, in the same project and window
        trace["deployment_id"] = deployment["id"]
        trace["attribution"] = "exact" if marked else "window"
        window |= not marked
        report.truncated |= trace["spans_truncated"]
        report.traces.append(trace)
        added += 1
    if not window:
        return
    for other in others:
        if (
            other["id"] != deployment["id"]
            and _project_for(other["name"], known) == project
            and _overlaps(lifetime, _lifetime(other))
            and other["id"] not in report.overlapping
            and not _tags(phoenix, project, other)
        ):
            report.overlapping.append(other["id"])


def one_trace(
    phoenix: Phoenix,
    trace_id: str,
    projects: list[str],
    deployments: list[dict],
    show: Shown,
    report: Report,
) -> None:
    """Find `trace_id` in the first of `projects` that has it."""
    for project in projects:
        spans, partial = phoenix.spans(project, [trace_id])
        trace = summarize(trace_id, project, spans.get(trace_id, []), show, bool(partial))
        if trace is None:
            continue
        report.truncated |= trace["spans_truncated"]
        if trace["deployment_id"] is None:
            moment = _when(trace["start_time"])
            matches = [
                d["id"]
                for d in deployments
                if moment is not None
                and _project_for(d["name"], {project}) == project
                and _alive_at(_lifetime(d), moment)
                and not _tags(phoenix, project, d)  # its traces carry its ID; this one doesn't
            ]
            if len(matches) == 1:
                trace["deployment_id"], trace["attribution"] = matches[0], "window"
            elif matches:
                report.overlapping += [m for m in matches if m not in report.overlapping]
        report.traces.append(trace)
        return
    raise TracesError("TRACES_NOT_FOUND", f"trace {trace_id} is not in Phoenix (yet)")


def valid_project(name: str) -> bool:
    return bool(_PROJECT.fullmatch(name))
