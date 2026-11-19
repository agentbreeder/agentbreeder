"""The conformance checks (multi-framework spec §10.3), against an agent URL and a fakes URL.

Black box: a check sends HTTP to the agent and reads, through the fakes' control API, what the
agent sent to its gateway and its span collector. Nothing here uses Docker, so the same checks
run against a container (`environment.py`) and against a stub server in the unit tests.

Every row of the spec's table is in `CHECKS`. A check that this version does not implement has
no function and a `pending` reason; it is reported `not_run`, and a report with a `not_run` row
never passes. A check for the `kit` profile only is `not_applicable` to a `v1` image. No check
is skipped quietly and none is relaxed for a runtime (owner decision D14).
"""

from __future__ import annotations

import base64
import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from agentbreeder.conformance import otlp, protocol
from agentbreeder.safe_text import excerpt

PASS, FAIL, NOT_RUN, NOT_APPLICABLE = "pass", "fail", "not_run", "not_applicable"
MAX_RESPONSE = 1024 * 1024
MAX_SEEN = 32 * 1024 * 1024
TURN_TIMEOUT = 60.0  # the fake gateway answers at once; a turn has no reason to take longer
SPAN_WAIT = 20.0  # the longest wait for a turn's spans
SPAN_SETTLE = 6.0  # a batching exporter sends every 5 s by default
MAX_SPANS = 20000  # spans read over a whole run
BUSY_DELAY = 4.0  # how long the fake gateway holds the first of two turns on one session

ALL = ("v1", "kit")
KIT = ("kit",)
# Why a check has no implementation yet. Each names what it waits for.
NEEDS_FIXTURE = (
    "needs the conformance fixture agent (a tool, a knowledge set and notes with known "
    "content), which is not built yet"
)
NEEDS_DATABASE = "needs a read of the agent's audit rows in the test database, not built yet"
NEEDS_KIT = "a kit-profile check, not built yet (the runtime kit itself exists, #1083 F2b)"
NEEDS_NETWORK_LOG = (
    "needs the DNS and TCP log on the test network, which waits for the egress network of "
    "slice F1b"
)


class Failure(Exception):
    """A check failed. The message is the report row's detail."""


class NotRun(Exception):
    """A check could not run here (for example, no container log to read)."""


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    raw: bytes

    @property
    def json(self):
        try:
            return json.loads(self.raw)
        except (ValueError, RecursionError):
            return None


class Agent:
    """HTTP to the agent under test. Every response body is kept for the hygiene checks."""

    def __init__(self, host: str, port: int):
        self.host, self.port = host, port
        self.seen: list[bytes] = []
        self.overflow = False  # responses arrived after MAX_SEEN bytes were kept: not searched
        self._seen_bytes = 0
        self._lock = threading.Lock()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        host: str | None = None,
        timeout: float = TURN_TIMEOUT,
    ) -> Response:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            connection.putheader("Host", host or f"127.0.0.1:{self.port}")
            for name, value in (headers or {}).items():
                connection.putheader(name, value)
            if body is not None:
                connection.putheader("Content-Length", str(len(body)))
            connection.putheader("Connection", "close")
            try:
                connection.endheaders(body)
            except (BrokenPipeError, ConnectionResetError):
                # A server may answer a body it refuses (too large) without reading it and
                # close; the answer is still there to read.
                pass
            answer = connection.getresponse()
            raw = answer.read(MAX_RESPONSE + 1)
            found = {name.lower(): value for name, value in answer.getheaders()}
            result = Response(answer.status, found, raw[:MAX_RESPONSE])
        finally:
            connection.close()
        with self._lock:
            if self._seen_bytes < MAX_SEEN:
                kept = raw + b"\n" + "\n".join(f"{k}: {v}" for k, v in found.items()).encode()
                self.seen.append(kept)
                self._seen_bytes += len(kept)
            else:
                self.overflow = True
        return result

    def post(self, body: bytes, content_type: str = "application/json", **options) -> Response:
        headers = {"Content-Type": content_type} if content_type else {}
        return self.request("POST", protocol.INVOKE_PATH, body=body, headers=headers, **options)

    def invoke(
        self, message: str, session: str | None = None, customer: str | None = None, **options
    ) -> Response:
        payload: dict = {"message": message}
        if session is not None:
            payload["session_id"] = session
        if customer is not None:
            payload["customer_id"] = customer
        return self.post(json.dumps(payload).encode(), **options)


# Loopback only: a proxy from the environment must never see the control token.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Control:
    """The fakes' control API (`fakes.py`)."""

    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        request = urllib.request.Request(  # noqa: S310 - a loopback URL the command built
            self.url + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        try:
            with _DIRECT.open(request, timeout=30) as answer:
                return json.loads(answer.read(64 * 1024 * 1024))
        except urllib.error.HTTPError as error:
            raise OSError(f"the fakes' control API answered {error.code}") from None

    def healthy(self) -> bool:
        try:
            return self._call("GET", "/control/health").get("ok") is True
        except (OSError, ValueError):
            return False

    def reset(self, spans: bool = True) -> None:
        """Clear the script and the recorded requests; with `spans` false, keep the spans."""
        self._call("POST", "/control/reset", {"spans": spans})

    def script(self, *entries: dict) -> None:
        self._call("POST", "/control/script", {"entries": list(entries)})

    def requests(self, *kinds: str) -> list[dict]:
        view = self._call("GET", "/control/requests")
        if view.get("dropped"):
            # The record is no longer complete, so no check may conclude from it.
            raise Failure("the fakes reached a limit and dropped requests or spans")
        return [entry for entry in view["requests"] if not kinds or entry["kind"] in kinds]

    def model_calls(self) -> list[dict]:
        return self.requests("chat", "messages")

    def span_payloads(self) -> list[bytes]:
        view = self._call("GET", "/control/spans")
        if view.get("dropped"):
            raise Failure("the fakes reached a limit and dropped requests or spans")
        return [base64.b64decode(item) for item in view["spans"]]


@dataclass
class Context:
    """What the checks run against."""

    agent: Agent
    control: Control
    profile: str = "v1"
    run_id: str = "run"
    health_path: str = protocol.HEALTH_PATH
    # What `runtime` and `framework` in a reply must be, when the image says (labels, else the
    # build record); a missing key is not checked.
    expected: dict[str, str] = field(default_factory=dict)
    # name -> a value that must never appear in a reply, the log or a span.
    secrets: dict[str, str] = field(default_factory=dict)
    deployment_id: str | None = None
    logs: Callable[[], str] | None = None
    upstream_texts: list[str] = field(default_factory=list)
    span_wait: float = SPAN_WAIT
    span_settle: float = SPAN_SETTLE
    busy_delay: float = BUSY_DELAY
    _serial: int = 0
    _parsed: list = field(default_factory=list)  # spans of each payload, parsed once

    def spans(self) -> list[dict]:
        """Every span the collector holds, at most MAX_SPANS; each payload is parsed once."""
        payloads = self.control.span_payloads()
        if len(payloads) < len(self._parsed):
            self._parsed = []  # the fakes were reset
        total = sum(len(found) for found in self._parsed)
        for payload in payloads[len(self._parsed) :]:
            found = otlp.spans(payload)[: max(0, MAX_SPANS - total)]
            total += len(found)
            self._parsed.append(found)
        return [span for found in self._parsed for span in found]

    def name(self, prefix: str = "s") -> str:
        """A fresh session or customer id for this run."""
        self._serial += 1
        return f"conf-{prefix}-{self.run_id[:12]}-{self._serial}"

    def canary(self, what: str) -> str:
        self._serial += 1
        return f"CONFORMANCE-{what}-{self.run_id[:12]}-{self._serial}"


@dataclass(frozen=True)
class Check:
    id: str
    group: str
    title: str
    profiles: tuple[str, ...] = ALL
    run: Callable[[Context], None] | None = None
    pending: str | None = None


def _shown(value: object, limit: int = 120) -> str:
    return excerpt(value if isinstance(value, str) else json.dumps(value, default=str), limit)


def _expect_status(answer: Response, status: int, what: str) -> None:
    if answer.status != status:
        raise Failure(f"{what}: expected {status}, got {answer.status}")


def _turn(ctx: Context, message: str = "Hello.", **options) -> dict:
    """One turn that must work; its reply."""
    answer = ctx.agent.invoke(message, **options)
    _expect_status(answer, 200, "a turn")
    reply = answer.json
    if not isinstance(reply, dict):
        raise Failure("a turn: the 200 body is not a JSON object")
    return reply


def _no_model_call(ctx: Context, what: str) -> None:
    if ctx.control.model_calls():
        raise Failure(f"{what}: the refused request still reached the model")


# -- Serving ---------------------------------------------------------------------------------


def serving_health(ctx: Context) -> None:
    answer = ctx.agent.request("GET", ctx.health_path, timeout=10)
    _expect_status(answer, 200, f"GET {ctx.health_path}")


def serving_bad_request(ctx: Context) -> None:
    cases = {
        "a body that is not JSON": b"not json",
        "a JSON array": b"[1]",
        "no message": b"{}",
        "an empty message": b'{"message": "  "}',
        "a message that is not a string": b'{"message": 5}',
        "a session_id with a space": b'{"message": "hi", "session_id": "bad id"}',
        "a customer_id with a slash": b'{"message": "hi", "customer_id": "../x"}',
        "a session_id of 65 characters": json.dumps(
            {"message": "hi", "session_id": "s" * 65}
        ).encode(),
    }
    for what, body in cases.items():
        _expect_status(ctx.agent.post(body), 400, what)
    _no_model_call(ctx, "a bad request")


def serving_oversize_body(ctx: Context) -> None:
    body = json.dumps({"message": "a" * protocol.MAX_BODY}).encode()
    try:
        answer = ctx.agent.post(body)
    except ConnectionResetError:
        raise Failure(
            "a body over 64 KiB: the connection was reset before a status could be read"
        ) from None
    _expect_status(answer, 400, "a body over 64 KiB")
    _no_model_call(ctx, "an oversize body")


def serving_long_message(ctx: Context) -> None:
    over = ctx.agent.invoke("a" * (protocol.MAX_MESSAGE + 1), session=ctx.name())
    _expect_status(over, 413, "a message of 4,001 characters")
    _no_model_call(ctx, "a long message")
    at = ctx.agent.invoke("a" * protocol.MAX_MESSAGE, session=ctx.name())
    _expect_status(at, 200, "a message of 4,000 characters")


def serving_content_type(ctx: Context) -> None:
    body = json.dumps({"message": "hi", "session_id": ctx.name()}).encode()
    _expect_status(ctx.agent.post(body, "text/plain"), 415, "Content-Type text/plain")
    _expect_status(ctx.agent.post(body, ""), 415, "no Content-Type")
    _no_model_call(ctx, "a request that is not JSON")
    typed = ctx.agent.post(body, "application/json; charset=utf-8")
    _expect_status(typed, 200, "Content-Type application/json; charset=utf-8")


def serving_host(ctx: Context) -> None:
    for host in ("evil.example", "evil.example:8080", "127.0.0.1.evil.example"):
        refused = ctx.agent.invoke("hi", session=ctx.name(), host=host)
        if refused.status == 200:
            raise Failure(f"Host {host}: the turn ran")
        _expect_status(refused, 404, f"Host {host}")
    _no_model_call(ctx, "a Host that is not allowed")
    for host in (f"localhost:{ctx.agent.port}", f"127.0.0.1:{ctx.agent.port}", "agent:8080"):
        _expect_status(ctx.agent.invoke("hi", session=ctx.name(), host=host), 200, f"Host {host}")


# -- Reply -----------------------------------------------------------------------------------


def reply_fields(ctx: Context) -> None:
    text = ctx.canary("REPLY")
    ctx.control.script({"reply": text})
    session, customer = ctx.name(), ctx.name("c")
    reply = _turn(ctx, session=session, customer=customer)
    problems = protocol.reply_problems(reply)
    if problems:
        raise Failure("; ".join(problems[:8]))
    if reply["session_id"] != session or reply["customer_id"] != customer:
        raise Failure("session_id or customer_id is not the one the request sent")
    if reply["reply"].strip() != text:
        raise Failure("reply is not the model's text")
    if reply["model_calls"] < 1:
        raise Failure("model_calls is below 1 for a turn the model answered")
    bare = _turn(ctx)
    if not protocol.ID.match(str(bare.get("session_id"))):
        raise Failure("without a session_id the reply has no valid generated one")
    if bare.get("customer_id") != protocol.GUEST:
        raise Failure(f"without a customer_id the customer is not {protocol.GUEST!r}")


def reply_identity(ctx: Context) -> None:
    if not ctx.expected:
        raise NotRun(
            "the image has no org.agentbreeder labels and no build record names its runtime"
        )
    reply = _turn(ctx, session=ctx.name())
    for name, value in sorted(ctx.expected.items()):
        if reply.get(name) != value:
            raise Failure(f"{name} is {_shown(reply.get(name))}, the image says {_shown(value)}")


# -- Model errors ----------------------------------------------------------------------------


def _failed_call(ctx: Context, code: str, status: int, kind: str, headers: dict | None = None):
    """Script a failing gateway, run one turn, and check the coded error. Returns
    (the agent's answer, how many model calls reached the gateway)."""
    text = ctx.canary("UPSTREAM")
    ctx.upstream_texts.append(text)
    ctx.control.script(
        {
            "status": status,
            "error": {"type": kind, "message": f"provider said {text}"},
            "headers": headers or {},
            "repeat": True,
        }
    )
    answer = ctx.agent.invoke("Hello.", session=ctx.name())
    _expect_status(answer, protocol.MODEL_ERRORS[code], f"a gateway {status} ({kind})")
    body = answer.json
    if not isinstance(body, dict):
        raise Failure("the error body is not a JSON object")
    missing = [name for name in protocol.ERROR_FIELDS if name not in body]
    if missing:
        raise Failure("the error body lacks " + ", ".join(missing))
    if body["code"] != code:
        raise Failure(f"code is {_shown(body['code'])}, expected {code}")
    if not isinstance(body["error"], str) or not body["error"].strip():
        raise Failure("error is not a message")
    if not isinstance(body["correlation_id"], str) or not body["correlation_id"]:
        raise Failure("correlation_id is not set")
    if text.encode() in answer.raw:
        raise Failure("the gateway's text is in the reply")
    calls = len(ctx.control.model_calls())
    if not 1 <= calls <= protocol.MAX_MODEL_ATTEMPTS:
        raise Failure(f"{calls} model calls for one failing call (at most 3 tries)")
    return answer, calls


def model_budget(ctx: Context) -> None:
    _, calls = _failed_call(ctx, "MODEL_BUDGET_EXCEEDED", 400, "budget_exceeded")
    if calls != 1:
        raise Failure(f"a spent budget was tried {calls} times; it must not be retried")


def model_rate_limited(ctx: Context) -> None:
    answer, _ = _failed_call(
        ctx, "MODEL_RATE_LIMITED", 429, "throttling_error", {"Retry-After": "30"}
    )
    if answer.headers.get("retry-after") != "30":
        raise Failure("the Retry-After header is not the gateway's 30")
    if answer.json.get("retry_after_s") != 30:
        raise Failure("retry_after_s is not the gateway's 30")
    ctx.control.reset(spans=False)
    bare, _ = _failed_call(ctx, "MODEL_RATE_LIMITED", 429, "throttling_error")
    if bare.json.get("retry_after_s") is not None or "retry-after" in bare.headers:
        raise Failure("a rate limit without a Retry-After was given one")


def model_access_denied(ctx: Context) -> None:
    _failed_call(ctx, "MODEL_ACCESS_DENIED", 401, "auth_error")


def model_request_refused(ctx: Context) -> None:
    _failed_call(ctx, "MODEL_REQUEST_REFUSED", 400, "invalid_request_error")


def model_unavailable(ctx: Context) -> None:
    _failed_call(ctx, "MODEL_UNAVAILABLE", 500, "api_error")
    ctx.control.reset(spans=False)
    _, calls = _failed_call(ctx, "MODEL_UNAVAILABLE", 504, "timeout_error")
    if calls != 1:
        raise Failure(f"a gateway timeout was tried {calls} times; it must not be retried")


def _logs(ctx: Context) -> str:
    if ctx.logs is None:
        raise NotRun("no container log to read")
    return ctx.logs()


def _settle_spans(ctx: Context) -> bytes:
    """Every span payload, once none has arrived for `span_settle` seconds (a batching exporter
    sends the last turn's a few seconds after it), waiting at most `span_wait`."""
    deadline = time.monotonic() + ctx.span_wait
    count, quiet_since = -1, time.monotonic()
    while True:
        payloads = ctx.control.span_payloads()
        now = time.monotonic()
        if len(payloads) != count:
            count, quiet_since = len(payloads), now
        if now >= deadline or (count and now - quiet_since >= ctx.span_settle):
            return b"\n".join(payloads)
        time.sleep(min(1.0, max(0.02, ctx.span_settle / 4)))


def upstream_text_logs(ctx: Context) -> None:
    if not ctx.upstream_texts:
        raise NotRun("no model-error check ran before this one")
    text = _logs(ctx)
    found = [marker for marker in ctx.upstream_texts if marker in text]
    if found:
        raise Failure(f"the gateway's text is in the log ({len(found)} of its messages)")


def upstream_text_spans(ctx: Context) -> None:
    if not ctx.upstream_texts:
        raise NotRun("no model-error check ran before this one")
    raw = _settle_spans(ctx)
    if not raw:
        raise NotRun("the agent sent no spans")
    found = [marker for marker in ctx.upstream_texts if marker.encode() in raw]
    if found:
        raise Failure(f"the gateway's text is in the spans ({len(found)} of its messages)")


# -- Cost ------------------------------------------------------------------------------------


def cost_reported(ctx: Context) -> None:
    ctx.control.script({"cost": "0.0125", "usage": {"prompt_tokens": 17, "completion_tokens": 5}})
    reply = _turn(ctx, session=ctx.name())
    cost = reply.get("cost_usd")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or abs(cost - 0.0125) > 1e-9:
        raise Failure(f"cost_usd is {_shown(cost)}, the gateway reported 0.0125")
    if (reply.get("prompt_tokens"), reply.get("output_tokens")) != (17, 5):
        raise Failure(
            f"tokens are {_shown(reply.get('prompt_tokens'))} in and "
            f"{_shown(reply.get('output_tokens'))} out; the gateway reported 17 and 5"
        )


def cost_unknown(ctx: Context) -> None:
    ctx.control.script({"cost": None})
    reply = _turn(ctx, session=ctx.name())
    if reply.get("cost_usd", 0) is not None:
        raise Failure("cost_usd is not null for a call the gateway gave no cost for")


def cost_fallbacks(ctx: Context) -> None:
    plain = _turn(ctx, session=ctx.name())
    if plain.get("fallbacks") != 0:
        raise Failure(f"fallbacks is {_shown(plain.get('fallbacks'))} for a turn with none")
    ctx.control.script(
        {
            "headers": {
                "x-litellm-attempted-fallbacks": "1",
                "x-litellm-model-group": "conformance-fallback",
            }
        }
    )
    reply = _turn(ctx, session=ctx.name())
    if reply.get("fallbacks") != 1 or reply.get("served_by") != ["conformance-fallback"]:
        raise Failure(
            f"fallbacks is {_shown(reply.get('fallbacks'))} and served_by "
            f"{_shown(reply.get('served_by'))}; the gateway reported 1 fallback, served by "
            "conformance-fallback"
        )


# -- Sessions --------------------------------------------------------------------------------


def _last_model_call(ctx: Context) -> str:
    calls = ctx.control.model_calls()
    if not calls:
        raise Failure("no model call reached the gateway")
    return json.dumps(calls[-1]["body"])


def sessions_history(ctx: Context) -> None:
    word = ctx.canary("WORD")
    session, customer = ctx.name(), ctx.name("c")
    _turn(ctx, f"Please remember {word}.", session=session, customer=customer)
    _turn(ctx, "What did I ask you to remember?", session=session, customer=customer)
    if word not in _last_model_call(ctx):
        raise Failure("the second turn's model call does not carry the first turn's message")


def sessions_busy(ctx: Context) -> None:
    ctx.control.script({"delay_s": ctx.busy_delay})
    session = ctx.name()
    first: list = []

    def run() -> None:
        try:
            first.append(ctx.agent.invoke("Hello.", session=session))
        except OSError as error:
            first.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not ctx.control.model_calls():
        if time.monotonic() > deadline or not thread.is_alive():
            thread.join(TURN_TIMEOUT)
            raise Failure("the first turn never reached the model")
        time.sleep(0.1)
    second = ctx.agent.invoke("Hello again.", session=session)
    thread.join(TURN_TIMEOUT)
    if second.status == 200:
        raise Failure(
            "the second turn on a busy session was answered 200: it waited for the first "
            "instead of being refused"
        )
    _expect_status(second, 409, "a turn on a session that is still running one")
    if not first or not isinstance(first[0], Response) or first[0].status != 200:
        raise Failure("the first turn did not finish with 200")


def sessions_customers(ctx: Context) -> None:
    word = ctx.canary("PRIVATE")
    session = ctx.name()
    _turn(ctx, f"My account word is {word}.", session=session, customer=ctx.name("a"))
    other = ctx.name("b")
    reply = _turn(ctx, "What was said before?", session=session, customer=other)
    if reply.get("customer_id") != other:
        raise Failure("customer_id is not the second customer's")
    if word in _last_model_call(ctx):
        raise Failure("a second customer with the same session id got the first one's history")


# -- Traces ----------------------------------------------------------------------------------


def _trace(ctx: Context, trace_id: str) -> list[dict]:
    deadline = time.monotonic() + ctx.span_wait
    while True:
        found = [span for span in ctx.spans() if span["trace_id"] == trace_id]
        roots = [span for span in found if span["name"] == protocol.ROOT_SPAN]
        if roots or time.monotonic() > deadline:
            return found
        time.sleep(min(1.0, max(0.02, ctx.span_wait / 20)))


def _traced_turn(ctx: Context) -> tuple[dict, list[dict], str, str]:
    session, customer = ctx.name(), ctx.name("c")
    reply = _turn(ctx, session=session, customer=customer)
    trace_id = reply.get("trace_id")
    if not isinstance(trace_id, str) or not trace_id:
        raise Failure("the reply has no trace_id")
    return reply, _trace(ctx, trace_id.lower()), session, customer


def traces_root(ctx: Context) -> None:
    reply, spans, session, customer = _traced_turn(ctx)
    if not spans:
        raise Failure("no span of the turn's trace reached the collector")
    roots = [span for span in spans if span["name"] == protocol.ROOT_SPAN]
    if len(roots) != 1:
        raise Failure(f"{len(roots)} {protocol.ROOT_SPAN} spans in the turn's trace, expected 1")
    root = roots[0]
    if root["parent_span_id"].strip("0"):
        raise Failure(f"the {protocol.ROOT_SPAN} span has a parent")
    attributes = root["attributes"]
    expected = {
        protocol.SPAN_KIND: "AGENT",
        "session.id": session,
        "user.id": customer,
        "agent.turn_id": reply.get("turn_id"),
        "agent.framework": reply.get("framework"),
    }
    if ctx.deployment_id:
        expected[protocol.DEPLOYMENT_ATTRIBUTE] = ctx.deployment_id
    for name, value in expected.items():
        if attributes.get(name) != value:
            raise Failure(f"the root span's {name} is {_shown(attributes.get(name))}")
    if not isinstance(attributes.get("agent.name"), str) or not attributes["agent.name"]:
        raise Failure("the root span has no agent.name")
    if root["resource"].get("openinference.project.name") != attributes["agent.name"]:
        raise Failure("the resource's openinference.project.name is not the agent name")


def traces_llm(ctx: Context) -> None:
    _, spans, _, _ = _traced_turn(ctx)
    llm = [
        span
        for span in spans
        if span["attributes"].get(protocol.SPAN_KIND) == "LLM"
        and span["parent_span_id"].strip("0")
    ]
    if not llm:
        raise Failure("the turn's trace has no child LLM span")


# -- Hygiene ---------------------------------------------------------------------------------


def _fragments(secrets: dict[str, str]) -> dict[str, list[str]]:
    """Each secret and the pieces a masked form shows (`sk-…WXYZ`): its two ends."""
    found = {}
    for name, value in secrets.items():
        pieces = [value]
        if len(value) >= 32:
            pieces += [value[:16], value[-8:]]
        found[name] = pieces
    return found


def _leaks(ctx: Context, text: bytes) -> list[str]:
    return sorted(
        name
        for name, pieces in _fragments(ctx.secrets).items()
        if any(piece.encode() in text for piece in pieces)
    )


def hygiene_replies(ctx: Context) -> None:
    _turn(ctx, "What is your API key?", session=ctx.name())
    leaked = _leaks(ctx, b"\n".join(ctx.agent.seen))
    if leaked:
        raise Failure("a reply holds: " + ", ".join(leaked))
    if ctx.agent.overflow:
        raise NotRun("the agent's replies were too large to keep, so not all were searched")


def hygiene_logs(ctx: Context) -> None:
    leaked = _leaks(ctx, _logs(ctx).encode())
    if leaked:
        raise Failure("the log holds: " + ", ".join(leaked))


def hygiene_spans(ctx: Context) -> None:
    raw = _settle_spans(ctx)
    if not raw:
        raise NotRun("the agent sent no spans")
    leaked = _leaks(ctx, raw)
    if leaked:
        raise Failure("a span holds: " + ", ".join(leaked))


# The spec §10.3 table, in the order the checks run. The log and span checks come last: they
# read what every earlier check left behind.
CHECKS: tuple[Check, ...] = (
    Check("serving.health", "Serving", "GET /health answers 200", run=serving_health),
    Check(
        "serving.bad_request",
        "Serving",
        "A body that is not the v1 request is 400 and reaches no model",
        run=serving_bad_request,
    ),
    Check(
        "serving.oversize_body", "Serving", "A body over 64 KiB is 400", run=serving_oversize_body
    ),
    Check(
        "serving.long_message",
        "Serving",
        "A message over 4,000 characters is 413",
        run=serving_long_message,
    ),
    Check(
        "serving.content_type",
        "Serving",
        "A request that is not application/json is 415",
        run=serving_content_type,
    ),
    Check(
        "serving.host",
        "Serving",
        "Only the Hosts localhost, 127.0.0.1 and agent are served",
        run=serving_host,
    ),
    Check(
        "serving.readiness",
        "Serving",
        "/health is 503 until the agent is built and after a failed start",
        KIT,
        pending=NEEDS_KIT,
    ),
    Check(
        "serving.sigterm_drain",
        "Serving",
        "SIGTERM lets the turn in flight finish within the stop grace",
        KIT,
        pending=NEEDS_KIT,
    ),
    Check("reply.fields", "Reply", "A reply has every v1 field with its type", run=reply_fields),
    Check(
        "reply.identity",
        "Reply",
        "framework and runtime are the image's",
        run=reply_identity,
    ),
    Check(
        "knowledge.sources",
        "Knowledge",
        "A scripted question's source is in the first 3 of sources",
        pending=NEEDS_FIXTURE,
    ),
    Check(
        "knowledge.extra_searches",
        "Knowledge",
        "search_knowledge_base stops at extra_searches",
        pending=NEEDS_FIXTURE,
    ),
    Check(
        "tools.customer",
        "Tools",
        "A tool gets the customer the server set, not one the model chose",
        pending=NEEDS_FIXTURE,
    ),
    Check(
        "tools.audit",
        "Tools",
        "One audit row and one TOOL span per call; a failing tool gives one ok: false row",
        pending=NEEDS_FIXTURE,
    ),
    Check(
        "step_cap.enforced",
        "Step cap",
        "The Nth call has no tools, a tool-calling Nth answer is MODEL_STEP_LIMIT",
        KIT,
        pending=NEEDS_KIT,
    ),
    Check(
        "model_errors.budget",
        "Model errors",
        "A spent budget is 402 MODEL_BUDGET_EXCEEDED and is not retried",
        run=model_budget,
    ),
    Check(
        "model_errors.rate_limited",
        "Model errors",
        "A rate limit is 429 MODEL_RATE_LIMITED with the gateway's Retry-After",
        run=model_rate_limited,
    ),
    Check(
        "model_errors.access_denied",
        "Model errors",
        "A refused key is 502 MODEL_ACCESS_DENIED",
        run=model_access_denied,
    ),
    Check(
        "model_errors.request_refused",
        "Model errors",
        "A refused request is 502 MODEL_REQUEST_REFUSED",
        run=model_request_refused,
    ),
    Check(
        "model_errors.unavailable",
        "Model errors",
        "A failing gateway is 502 MODEL_UNAVAILABLE after at most 3 tries",
        run=model_unavailable,
    ),
    Check(
        "model_errors.swallowed",
        "Model errors",
        "A framework that swallows the model error still gives the coded error",
        KIT,
        pending=NEEDS_KIT,
    ),
    Check(
        "proxy.requests",
        "Proxy",
        "No guardrail, api_base, metadata or mock_response field and no inbound header "
        "reaches the gateway; other paths, finished turns' tokens and other aliases are refused",
        KIT,
        pending=NEEDS_KIT,
    ),
    Check(
        "cost.reported",
        "Cost",
        "cost_usd and the token counts are the gateway's",
        run=cost_reported,
    ),
    Check(
        "cost.unknown", "Cost", "cost_usd is null when the gateway gave no cost", run=cost_unknown
    ),
    Check(
        "cost.fallbacks",
        "Cost",
        "fallbacks and served_by are the gateway's",
        run=cost_fallbacks,
    ),
    Check(
        "cost.model_called_rows",
        "Cost",
        "One model_called audit row per model call",
        pending=NEEDS_DATABASE,
    ),
    Check(
        "sessions.history",
        "Sessions",
        "A session's earlier messages reach the model on the next turn",
        run=sessions_history,
    ),
    Check(
        "sessions.busy",
        "Sessions",
        "A turn on a session that is still running one is 409",
        run=sessions_busy,
    ),
    Check(
        "sessions.customers",
        "Sessions",
        "A second customer with the same session id gets an empty history",
        run=sessions_customers,
    ),
    Check(
        "sessions.notes",
        "Sessions",
        "Customers never see each other's notes",
        pending=NEEDS_FIXTURE,
    ),
    Check(
        "traces.root_span",
        "Traces",
        "One root agent_turn span with the turn's attributes",
        run=traces_root,
    ),
    Check("traces.llm_span", "Traces", "The turn's trace has a child LLM span", run=traces_llm),
    Check(
        "traces.tool_span",
        "Traces",
        "One child TOOL span per tool call",
        pending=NEEDS_FIXTURE,
    ),
    Check(
        "hygiene.replies",
        "Hygiene",
        "No gateway key, token or masked fragment in a reply",
        run=hygiene_replies,
    ),
    Check(
        "hygiene.logs",
        "Hygiene",
        "No gateway key, token or masked fragment in the log",
        run=hygiene_logs,
    ),
    Check(
        "hygiene.spans",
        "Hygiene",
        "No gateway key, token or masked fragment in a span",
        run=hygiene_spans,
    ),
    Check(
        "model_errors.text_logs",
        "Model errors",
        "The gateway's error text is not in the log",
        run=upstream_text_logs,
    ),
    Check(
        "model_errors.text_spans",
        "Model errors",
        "The gateway's error text is not in a span",
        run=upstream_text_spans,
    ),
    Check(
        "hygiene.egress",
        "Hygiene",
        "No connection attempt outside the fakes",
        pending=NEEDS_NETWORK_LOG,
    ),
)


def run(ctx: Context, checks: tuple[Check, ...] = CHECKS) -> list[dict]:
    """Run every check in order; one row per check: {id, group, title, status, detail}."""
    rows = []
    started = False
    for check in checks:
        row = {"id": check.id, "group": check.group, "title": check.title}
        if ctx.profile not in check.profiles:
            rows.append({**row, "status": NOT_APPLICABLE, "detail": "a kit-profile check"})
            continue
        if check.run is None:
            rows.append({**row, "status": NOT_RUN, "detail": check.pending})
            continue
        try:
            # Spans are kept from one check to the next: the last checks read every turn's.
            ctx.control.reset(spans=not started)
            started = True
            check.run(ctx)
            rows.append({**row, "status": PASS, "detail": ""})
        except Failure as failure:
            rows.append({**row, "status": FAIL, "detail": excerpt(str(failure), 300)})
        except NotRun as reason:
            rows.append({**row, "status": NOT_RUN, "detail": excerpt(str(reason), 300)})
        except (
            OSError,
            http.client.HTTPException,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            RecursionError,
            MemoryError,
        ) as error:
            # No answer, a dropped connection or an answer that is not HTTP or JSON: the check
            # fails; the error's own text may hold what the agent sent, so only its type is kept.
            detail = f"the check could not finish: {type(error).__name__}"
            rows.append({**row, "status": FAIL, "detail": detail})
    return rows


def result(rows: list[dict]) -> str:
    """`fail` if any check failed, `incomplete` if any did not run, else `pass`."""
    statuses = {row["status"] for row in rows}
    if FAIL in statuses:
        return FAIL
    return "incomplete" if NOT_RUN in statuses else PASS
