"""The loopback model proxy (spec §7.2, D1).

Every framework is pointed at this proxy, in the agent's own process, never at the gateway. The
proxy holds the gateway key and implements once what `runtime/adk@1.3.0` built into ADK's
LiteLLM client: the turn's cost, fallbacks and token tally, retries, coded model errors, the
step cap, the model allow-set and one `model_called` audit row per call.

- **Listener (§7.2.1):** asyncio on 127.0.0.1 only, a random port, h11 as the HTTP/1.1 parser.
  `Transfer-Encoding` on a request, more than one `Content-Length`, an absolute-form target, a
  query string and any path but `POST /v1/chat/completions` are refused. At most
  MAX_CONNECTIONS connections, a MAX_BODY request, a MAX_RESPONSE upstream answer, an
  IDLE_TIMEOUT idle connection, REQUEST_TIMEOUT to receive a request. The turn token is checked
  before the body is read.
- **Turn binding (§7.2.2):** each turn has a random 128-bit token, sent by the framework as its
  API key (`Authorization: Bearer`). An unknown or finished token gets a fixed 401. The token is
  remembered by redaction while the turn runs.
- **Requests are built from scratch (§7.2.3):** an allow-list of body fields; no inbound header
  is forwarded; `model` must be in the turn's allow-set (§7.2.5) and `reasoning_effort` is the
  agent's. Upstream calls are never streamed: a `stream: true` request gets the whole answer as
  synthesised SSE (D7), so the cost header is always read.
- **Step cap (§7.2.4):** the Nth call goes with `tool_choice: "none"`, but the proxy never relies
  on the provider honouring it (Gemini does not, #1111): a tool-calling Nth answer is withheld
  and the turn fails `MODEL_STEP_LIMIT`; an (N+1)th call is refused without going upstream.
- **Failures (§7.2.7):** the framework gets a fixed body naming the code, never the gateway's
  text; once a turn has a terminal failure, every later call of the turn gets the same answer at
  once, so a framework's own retry loop ends.

The proxy writes no log line and never keeps a body beyond the call.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import h11
import httpx

from . import errors, redaction
from .errors import Failure, ModelError

LISTEN_HOST = "127.0.0.1"  # never 0.0.0.0: sidecars share the network namespace
MAX_CONNECTIONS = 64
MAX_BODY = 4 * 1024 * 1024
MAX_RESPONSE = 8 * 1024 * 1024
MAX_HEADERS = 16 * 1024
IDLE_TIMEOUT = 120.0
REQUEST_TIMEOUT = 30.0  # to receive one whole request, head and body, however slowly it trickles
UPSTREAM_TIMEOUT = 90.0  # per upstream call, as 1.3.0
CHAT_PATH = b"/v1/chat/completions"
# Chat Completions fields a request may carry upstream. Everything else is dropped:
# api_base, api_key, metadata, user, mock_response, guardrails, fallbacks, caching, extra_body
# and every other LiteLLM control field, so the agent cannot switch off the gateway's
# guardrails or redirect a call (spec §7.2.3, T7).
CHAT_FIELDS = (
    "model",
    "messages",
    "tools",
    "tool_choice",
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "stop",
    "response_format",
)
REASONING = {"none": None, "low": "low", "medium": "medium", "high": "high"}
STEP_LIMIT = "MODEL_STEP_LIMIT"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


ModelCalled = Callable[[dict], Awaitable[None]]


@dataclass
class TurnState:
    """One turn, as the proxy sees it."""

    turn_id: str
    max_steps: int
    aliases: frozenset[str]
    reasoning: str | None = None
    system: str | None = None  # the turn's system prompt; its hash marks a main-loop call
    toolset: frozenset[str] = frozenset()  # the agent's tool names, likewise
    on_model_called: ModelCalled | None = None  # writes the call's `model_called` row
    tally: dict = field(default_factory=errors.new_tally)
    calls: int = 0  # logical calls the framework made that went upstream
    prompt_tokens: int = 0
    output_tokens: int = 0
    final_text: str | None = None  # the Nth call's text, when the Nth call was answered
    final_main: bool = False  # whether the Nth call was a main-loop call
    refused_over_cap: bool = False  # an (N+1)th call was refused
    step_limited: bool = False  # the Nth answer called tools and was withheld
    failure: ModelError | None = None  # the turn's terminal model failure
    failure_status: int = 502  # what the framework is answered while `failure` holds
    detail: str = ""  # the gateway's text for the failure, redacted (turn record only)
    internal: Exception | None = None  # the proxy itself failed (e.g. the audit row's database)
    closed: bool = False  # the turn ended; a call still in flight records nothing more

    @property
    def system_hash(self) -> str | None:
        return None if self.system is None else _hash(self.system)


class StepLimit(ModelError):
    def __init__(self):
        super().__init__(STEP_LIMIT)


def decide(state: TurnState, reply: str | None, raised: Exception | None) -> str:
    """The turn's result (spec §7.2.4, §7.2.7), whatever the framework did with the proxy's
    answers: a withheld tool-calling Nth answer first, then a terminal model failure, then a
    refused (N+1)th call, then the framework's own outcome."""
    if state.step_limited:
        raise StepLimit()
    if state.failure is not None:
        coded = ModelError(state.failure.code, state.failure.retry_after, state.failure.status)
        raise coded
    if state.internal is not None:
        raise state.internal
    if state.refused_over_cap:
        if state.final_main and state.final_text:
            return state.final_text
        raise StepLimit()
    if raised is not None:
        raise raised
    return reply or ""


# -- request and response rules (pure) ------------------------------------------------------


def content_text(content: object) -> str:
    """A message's text: a string, or the text parts of a content list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def tool_names(tools: object) -> frozenset[str]:
    names = set()
    for tool in tools if isinstance(tools, list) else []:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str):
            names.add(name)
    return frozenset(names)


def is_main_loop(body: dict, state: TurnState) -> bool:
    """Whether a call is the agent's own reasoning step: it carries the turn's system prompt
    and exactly the agent's tool set (spec §7.2.4). Anything else is a hidden call."""
    expected = state.system_hash
    messages = body.get("messages")
    if expected is None or not isinstance(messages, list) or not messages:
        return False
    first = messages[0]
    if not isinstance(first, dict) or first.get("role") not in ("system", "developer"):
        return False
    if _hash(content_text(first.get("content"))) != expected:
        return False
    return tool_names(body.get("tools")) == state.toolset


def upstream_body(body: dict, model: str, reasoning: str | None, final: bool) -> dict:
    """The upstream request: the allow-listed fields only, the alias, the agent's reasoning
    effort, never streamed, and function calling off on the Nth call (tools kept: removing
    them while the history holds tool calls is refused by some backends)."""
    result = {key: body[key] for key in CHAT_FIELDS if key in body}
    result["model"] = model
    if reasoning:
        result["reasoning_effort"] = reasoning
    if final and result.get("tools"):
        result["tool_choice"] = "none"
    return result


def _choices(response: dict) -> list[dict]:
    choices = response.get("choices")
    return [c for c in choices if isinstance(c, dict)] if isinstance(choices, list) else []


def has_tool_calls(response: dict) -> bool:
    """Whether any choice asks for a tool: `tool_calls`, or a legacy `function_call`."""
    for choice in _choices(response):
        message = choice.get("message")
        if isinstance(message, dict) and (
            message.get("tool_calls") or message.get("function_call")
        ):
            return True
    return False


def response_text(response: dict) -> str:
    choices = _choices(response)
    message = choices[0].get("message") if choices else None
    return content_text(message.get("content")) if isinstance(message, dict) else ""


def finish_reason(response: dict) -> str:
    choices = _choices(response)
    reason = choices[0].get("finish_reason") if choices else None
    return reason if isinstance(reason, str) else ""


def usage(response: dict) -> tuple[int, int]:
    """(prompt, output) tokens as the gateway reported them; output includes reasoning."""
    data = response.get("usage")
    if not isinstance(data, dict):
        return 0, 0

    def number(key: str) -> int:
        value = data.get(key)
        return (
            value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
        )

    return number("prompt_tokens"), number("completion_tokens")


def sse(response: dict, include_usage: bool) -> bytes:
    """A whole chat answer as Chat Completions SSE chunks (D7): one delta with the role,
    content and tool calls per choice, one with its finish reason, the usage chunk when the
    framework asked for it, then `[DONE]`."""
    base = {
        "id": response.get("id", ""),
        "object": "chat.completion.chunk",
        "created": response.get("created", 0),
        "model": response.get("model", ""),
    }
    events: list[dict] = []
    for position, choice in enumerate(_choices(response)):
        index = choice.get("index", position)
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        delta: dict = {"role": "assistant"}
        if message.get("content") is not None:
            delta["content"] = content_text(message.get("content"))
        calls = message.get("tool_calls")
        if isinstance(calls, list) and calls:
            delta["tool_calls"] = [
                {**call, "index": k} for k, call in enumerate(calls) if isinstance(call, dict)
            ]
        events.append({**base, "choices": [{"index": index, "delta": delta}]})
        events.append(
            {
                **base,
                "choices": [
                    {"index": index, "delta": {}, "finish_reason": choice.get("finish_reason")}
                ],
            }
        )
    if include_usage and isinstance(response.get("usage"), dict):
        events.append({**base, "choices": [], "usage": response["usage"]})
    text = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    return (text + "data: [DONE]\n\n").encode()


def refusal(status: int, code: str) -> tuple[int, str, bytes]:
    """The fixed answer to a call the proxy did not serve: the code, nothing else."""
    body = {"error": {"message": code, "type": code.lower(), "code": code}}
    return status, "application/json", json.dumps(body).encode()


UNKNOWN_TURN = refusal(401, "TURN_UNKNOWN")
NOT_FOUND = refusal(404, "NOT_FOUND")
BAD_REQUEST = refusal(400, "BAD_REQUEST")
TOO_LARGE = refusal(413, "BODY_TOO_LARGE")
BUSY = refusal(503, "PROXY_BUSY")
INTERNAL = refusal(500, "PROXY_ERROR")


class _TooLarge(Exception):
    pass


class ModelProxy:
    """The proxy for one process. `start()` it on the event loop the turns run on."""

    def __init__(
        self,
        gateway_url: str,
        gateway_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.gateway_url = gateway_url.rstrip("/")
        self._key = gateway_key
        redaction.remember(gateway_key)
        self._transport = transport
        self._sleep = sleep
        self._client: httpx.AsyncClient | None = None
        self._server: asyncio.base_events.Server | None = None
        self._turns: dict[str, TurnState] = {}
        self._active = 0
        self._writers: set[asyncio.StreamWriter] = set()
        self.port = 0

    # -- lifecycle ---------------------------------------------------------------------------

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=UPSTREAM_TIMEOUT,
            trust_env=False,  # never an HTTP(S)_PROXY from the environment
            follow_redirects=False,
            transport=self._transport,
        )
        self._server = await asyncio.start_server(self._connection, LISTEN_HOST, 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            # Keep-alive connections from the framework's client would hold wait_closed().
            for writer in list(self._writers):
                writer.close()
            await self._server.wait_closed()
        if self._client is not None:
            await self._client.aclose()

    @property
    def base_url(self) -> str:
        return f"http://{LISTEN_HOST}:{self.port}/v1"

    def open_turn(self, state: TurnState) -> str:
        token = secrets.token_hex(16)
        redaction.remember(token)
        self._turns[token] = state
        return token

    def close_turn(self, token: str) -> None:
        state = self._turns.pop(token, None)
        if state is not None:
            state.closed = True
        redaction.forget(token)

    # -- HTTP --------------------------------------------------------------------------------

    async def _connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        if self._active >= MAX_CONNECTIONS:
            await self._reject(writer, BUSY)
            return
        self._active += 1
        self._writers.add(writer)
        connection = h11.Connection(h11.SERVER, max_incomplete_event_size=MAX_HEADERS)
        raw = bytearray()  # this request's bytes as received, for the head checks h11 smooths
        try:
            while True:
                request = await self._read(connection, reader, raw)
                if request is None:
                    break
                answer = await self._answer(*request)
                await self._send(connection, writer, answer)
                if connection.our_state is not h11.DONE or connection.their_state is not h11.DONE:
                    break
                connection.start_next_cycle()
                raw = bytearray(connection.trailing_data[0])
        except (h11.ProtocolError, _TooLarge, TimeoutError, ConnectionError):
            if connection.our_state in (h11.IDLE, h11.SEND_RESPONSE):
                try:
                    await self._send(connection, writer, BAD_REQUEST)
                except (h11.ProtocolError, ConnectionError):
                    pass
        finally:
            self._active -= 1
            self._writers.discard(writer)
            writer.close()

    async def _reject(self, writer: asyncio.StreamWriter, answer) -> None:
        connection = h11.Connection(h11.SERVER)
        try:
            await self._send(connection, writer, answer)
        except (h11.ProtocolError, ConnectionError):
            pass
        finally:
            writer.close()

    async def _read(
        self, connection: h11.Connection, reader: asyncio.StreamReader, raw: bytearray
    ):
        """One request: (h11.Request, body, refused answer or None); None at a clean close."""
        request = None
        body = bytearray()
        refused = None
        deadline = None  # set once the request's first bytes arrive
        while True:
            event = connection.next_event()
            if event is h11.NEED_DATA:
                loop = asyncio.get_running_loop()
                wait = IDLE_TIMEOUT if deadline is None else deadline - loop.time()
                if wait <= 0:
                    raise TimeoutError
                data = await asyncio.wait_for(reader.read(65536), wait)
                if deadline is None and data:
                    deadline = loop.time() + REQUEST_TIMEOUT
                if len(raw) <= MAX_HEADERS:
                    raw.extend(data)
                connection.receive_data(data)
                continue
            if isinstance(event, h11.ConnectionClosed):
                return None
            if isinstance(event, h11.Request):
                request = event
                refused = self._refuse_headers(event, bytes(raw))
                if refused is None and self._state(event) is None:
                    refused = UNKNOWN_TURN  # before the body: an unknown peer buffers nothing
                if refused is not None:
                    # No body is read for a refused request; the connection closes after it.
                    return request, b"", refused
            elif isinstance(event, h11.Data):
                body.extend(event.data)
                if len(body) > MAX_BODY:
                    return request, b"", TOO_LARGE
            elif isinstance(event, h11.EndOfMessage):
                return request, bytes(body), refused

    @staticmethod
    def _refuse_headers(request: h11.Request, raw: bytes):
        # h11 merges repeated identical Content-Length headers (and accepts a bare LF and a
        # "27, 27" list), so the raw head is checked: one Content-Length, digits only.
        head = re.split(rb"\r?\n\r?\n", raw, maxsplit=1)[0]
        lines = re.split(rb"\r?\n", head)[1:]
        lengths = [
            line.split(b":", 1)[1].strip()
            for line in lines
            if b":" in line and line.split(b":", 1)[0].strip().lower() == b"content-length"
        ]
        names = [name for name, _ in request.headers]
        if (
            b"transfer-encoding" in names
            or len(lengths) > 1
            or any(not value.isdigit() for value in lengths)
        ):
            return BAD_REQUEST
        if request.method != b"POST" or request.target != CHAT_PATH:
            # Also absolute-form targets, a query string and dot segments (`/v1/../x`).
            return NOT_FOUND
        for name, value in request.headers:
            if name == b"content-length" and int(value) > MAX_BODY:
                return TOO_LARGE
        return None

    async def _send(self, connection: h11.Connection, writer: asyncio.StreamWriter, answer):
        status, kind, body = answer
        headers = [
            (b"content-type", kind.encode()),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ]
        writer.write(connection.send(h11.Response(status_code=status, headers=headers)))
        writer.write(connection.send(h11.Data(data=body)))
        writer.write(connection.send(h11.EndOfMessage()))
        await writer.drain()

    def _state(self, request: h11.Request) -> TurnState | None:
        token = None
        for name, value in request.headers:
            if name == b"authorization" and value.startswith(b"Bearer "):
                token = value[7:].decode("ascii", "replace").strip()
        return self._turns.get(token) if token else None

    async def _answer(self, request: h11.Request, body: bytes, refused):
        if refused is not None:
            return refused
        state = self._state(request)
        if state is None:  # the turn ended while its body was read
            return UNKNOWN_TURN
        try:
            parsed = json.loads(body)
        except (ValueError, UnicodeDecodeError, RecursionError):
            return BAD_REQUEST
        try:
            return await self.chat(state, parsed)
        except Exception as error:  # noqa: BLE001 - kept for the turn, never sent on
            if state.internal is None:
                state.internal = error
            return INTERNAL

    # -- one call ----------------------------------------------------------------------------

    async def chat(self, state: TurnState, body: object):
        """Serve one chat call of `state`'s turn: (status, content type, body)."""
        if state.failure is not None:
            return refusal(state.failure_status, state.failure.code)
        if state.step_limited or state.refused_over_cap:
            return refusal(429, STEP_LIMIT)
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
            return BAD_REQUEST
        model = body.get("model")
        if not isinstance(model, str) or model not in state.aliases:
            # A model the agent does not declare: the turn fails MODEL_ACCESS_DENIED.
            state.failure = ModelError("MODEL_ACCESS_DENIED", status=403)
            state.failure_status = 403
            state.detail = "the framework asked for a model alias the agent does not declare"
            return refusal(403, "MODEL_ACCESS_DENIED")
        if state.calls >= state.max_steps:
            state.refused_over_cap = True
            return refusal(429, STEP_LIMIT)
        state.calls += 1
        step = state.calls
        final = step >= state.max_steps
        main = is_main_loop(body, state)
        started = time.monotonic()
        outcome = await self._upstream(state, upstream_body(body, model, state.reasoning, final))
        if isinstance(outcome, ModelError):
            state.failure = outcome
            state.failure_status = outcome.status if 400 <= (outcome.status or 0) < 600 else 502
            return refusal(state.failure_status, outcome.code)
        headers, response = outcome
        errors.record_response(state.tally, headers)
        prompt, output = usage(response)
        state.prompt_tokens += prompt
        state.output_tokens += output
        limited = final and has_tool_calls(response)
        fallbacks, served_by = state.tally["route"] or (None, None)
        row = {
            "step": step,
            "model": model,
            "prompt_tokens": prompt,
            "output_tokens": output,
            "finish_reason": finish_reason(response),
            "cost_usd": state.tally["last"],
            "served_by": served_by,
            "fallbacks": fallbacks,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "main_loop": main,
        }
        if limited:
            row["step_limited"] = True
        if state.closed:  # the turn timed out or ended meanwhile: record nothing more
            return refusal(429, STEP_LIMIT)
        if state.on_model_called is not None:
            await state.on_model_called(row)
        if limited:
            # The provider ignored `tool_choice: none`: relay none of the answer (§7.2.4).
            state.step_limited = True
            return refusal(429, STEP_LIMIT)
        if final:
            state.final_text = response_text(response)
            state.final_main = main
        if body.get("stream") is True:
            options = body.get("stream_options")
            include = isinstance(options, dict) and options.get("include_usage") is True
            return 200, "text/event-stream", sse(response, include)
        return 200, "application/json", json.dumps(response).encode()

    async def _upstream(self, state: TurnState, body: dict):
        """One logical call with 1.3.0's retries: (headers, response) or a ModelError."""
        if self._client is None:
            raise RuntimeError("start() the proxy first")
        attempt = 1
        while True:
            message = ""
            try:
                async with self._client.stream(
                    "POST",
                    f"{self.gateway_url}/v1/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {self._key}"},
                ) as response:
                    data = await self._read_capped(response)
                    status, headers = response.status_code, response.headers
            except httpx.TimeoutException:
                failure = Failure(errors.TIMEOUT_STATUS)
            except _TooLarge:
                state.detail = "the gateway's answer was over the proxy's response cap"
                return ModelError("MODEL_UNAVAILABLE", status=502)
            except httpx.HTTPError as error:
                failure = Failure(errors.CONNECTION_STATUS)
                message = type(error).__name__
            else:
                try:
                    parsed = json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    parsed = None
                if 200 <= status < 300:
                    if isinstance(parsed, dict):
                        return headers, parsed
                    state.detail = "the gateway's answer was not a JSON object"
                    return ModelError("MODEL_UNAVAILABLE", status=502)
                failure = Failure(status, errors.upstream_kind(parsed), headers)
                message = errors.upstream_message(parsed)
            delay = errors.retry_delay(failure, attempt, state.tally)
            if delay is None:
                state.detail = redaction.upstream(
                    f"status {failure.status}: {message}"
                    if message
                    else f"status {failure.status}"
                )[:500]
                return errors.classify(failure)
            await self._sleep(delay)
            attempt += 1

    @staticmethod
    async def _read_capped(response: httpx.Response) -> bytes:
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > MAX_RESPONSE:
                raise _TooLarge()
        return bytes(data)


class TurnAuth(httpx.Auth):
    """A framework's model client sends the running turn's token as its API key (§7.2.2)."""

    def __init__(self, token_var):
        self.token_var = token_var

    def auth_flow(self, request: httpx.Request):
        token = self.token_var.get()
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        else:
            request.headers.pop("Authorization", None)
        yield request
