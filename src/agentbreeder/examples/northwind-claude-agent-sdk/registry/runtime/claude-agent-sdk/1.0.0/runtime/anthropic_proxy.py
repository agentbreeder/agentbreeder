"""The kit's model proxy on the Anthropic Messages route, for the Claude CLI (spec §7.2,
`model_api: anthropic-messages`).

A subclass of kit 1.0.0's `ModelProxy`, kept in this runtime so the kit (vendored byte for byte in
every kit runtime) is unchanged; a later kit version can take it in. Everything the chat route
does holds here, with the Anthropic shapes:

- **Listener:** a Unix socket in the kit's own directory (`kit/model.sock`) instead of a TCP
  port, so the CLI container needs no network: its host forwards the CLI's loopback connections
  here. The kernel must say each peer is the CLI host's uid (two-uid contract, D15).
- **Route:** `POST /v1/messages` only (the CLI's `?beta=true` query is accepted and dropped);
  anything else is 404. The turn token comes as `Authorization: Bearer` (ANTHROPIC_AUTH_TOKEN) or
  `x-api-key`.
- **Body:** an allow-list (MESSAGES_FIELDS). Dropped: `metadata` (a user id), and in v1
  `context_management` and `output_config` (F0 did not run the CLI without them; conformance
  does), `stream`, and every LiteLLM control field. `max_tokens` is capped (MAX_TOKENS);
  `thinking` is kept with its budget below the cap. Upstream calls are never streamed: a
  streamed request gets the whole answer as the six Anthropic SSE event types (D7).
- **Model:** the CLI names models itself (a main and a "small fast" one), so any name is
  rewritten to the agent's alias (§7.2.5); every such call counts toward the cap.
- **Step cap:** the Nth call goes with `tool_choice: {type: none}`; a `tool_use` or
  `server_tool_use` block in the Nth answer is withheld and the turn fails `MODEL_STEP_LIMIT`
  (§7.2.4, #1111); an (N+1)th call is refused unsent.
- **Main-loop calls:** the CLI wraps the agent's prompt in its own system blocks, so a main-loop
  call is one whose system text contains the turn's system prompt and whose tools are exactly
  the agent's (as `mcp__agentbreeder__<name>`, plus the CLI's own `DeferredToolPlaceholder`
  entry, which is no tool). Not run against the real CLI; a misclassified Nth call fails safe.
- **Failures** reach the CLI as a fixed Anthropic error body naming the code, never the
  gateway's text. One LLM span per upstream call (the CLI's instrumentor writes none), with the
  alias and token counts and no message text.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from pathlib import Path

import h11
import httpx
from kit import errors, redaction
from kit.errors import Failure, ModelError
from kit.proxy import (
    BAD_REQUEST,
    INTERNAL,
    NOT_FOUND,
    STEP_LIMIT,
    UNKNOWN_TURN,
    UPSTREAM_TIMEOUT,
    ModelProxy,
    TurnState,
    _TooLarge,
)
from kit.telemetry import span
from link_protocol import CLI_UID, bind_world, own_dir, peer_uid

MESSAGES_PATHS = (b"/v1/messages", b"/v1/messages?beta=true")
UPSTREAM_PATH = "/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 16384
MIN_THINKING = 1024
MCP_PREFIX = "mcp__agentbreeder__"
CLI_PLACEHOLDERS = frozenset({"DeferredToolPlaceholder"})
MESSAGES_FIELDS = (
    "model",
    "messages",
    "system",
    "tools",
    "tool_choice",
    "max_tokens",
    "temperature",
    "top_p",
    "top_k",
    "stop_sequences",
    "thinking",
)
TOOL_BLOCKS = frozenset({"tool_use", "server_tool_use"})


def anthropic_refusal(answer: tuple[int, str, bytes]) -> tuple[int, str, bytes]:
    """The kit's fixed refusal, in the Anthropic error shape: the code and nothing else."""
    status, _, body = answer
    code = json.loads(body)["error"]["code"]
    payload = {"type": "error", "error": {"type": code.lower(), "message": code}}
    return status, "application/json", json.dumps(payload).encode()


def refusal(status: int, code: str) -> tuple[int, str, bytes]:
    payload = {"type": "error", "error": {"type": code.lower(), "message": code}}
    return status, "application/json", json.dumps(payload).encode()


def system_text(system: object) -> str:
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "\n".join(
            block.get("text", "")
            for block in system
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return ""


def tool_names(tools: object) -> list[str]:
    return [
        tool["name"]
        for tool in (tools if isinstance(tools, list) else [])
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    ]


def is_main_loop(body: dict, state: TurnState) -> bool:
    if not state.system or state.system not in system_text(body.get("system")):
        return False
    names = tool_names(body.get("tools"))
    agent = {name[len(MCP_PREFIX) :] for name in names if name.startswith(MCP_PREFIX)}
    others = {name for name in names if not name.startswith(MCP_PREFIX)}
    return agent == state.toolset and others <= CLI_PLACEHOLDERS


class Refused(ValueError):
    """A request that carries what the CLI never needs (see `check_content`)."""


def check_content(body: dict) -> None:
    """Content rules beyond the field allow-list: tools are plain (no `type`, or `custom`) and
    named `mcp__agentbreeder__*` or a CLI placeholder, so no provider server-side tool (web
    search, code execution, fetch) runs on the provider's network for the agent; and no content
    block takes a `url` source the provider would fetch."""
    tools = body.get("tools", [])
    if not isinstance(tools, list):
        raise Refused("tools")
    for tool in tools:
        name = tool.get("name") if isinstance(tool, dict) else None
        if (
            not isinstance(name, str)
            or tool.get("type", "custom") != "custom"
            or not (name.startswith(MCP_PREFIX) or name in CLI_PLACEHOLDERS)
        ):
            raise Refused("tools")

    def walk(value, depth=0):
        if depth > 12:
            raise Refused("nesting")
        if isinstance(value, dict):
            source = value.get("source")
            if isinstance(source, dict) and source.get("type") not in (None, "base64", "text"):
                raise Refused("source")
            for item in value.values():
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)

    walk(body.get("messages"))
    walk(body.get("system"))


RESPONSE_FIELDS = (
    "id",
    "type",
    "role",
    "model",
    "content",
    "stop_reason",
    "stop_sequence",
    "usage",
)


def upstream_body(body: dict, model: str, final: bool) -> dict:
    result = {key: body[key] for key in MESSAGES_FIELDS if key in body}
    result["model"] = model
    requested = result.get("max_tokens")
    if not isinstance(requested, int) or isinstance(requested, bool) or requested < 1:
        requested = MAX_TOKENS
    result["max_tokens"] = min(requested, MAX_TOKENS)
    thinking = result.pop("thinking", None)
    if isinstance(thinking, dict) and thinking.get("type") in ("disabled", "adaptive"):
        result["thinking"] = {"type": thinking["type"]}
    elif isinstance(thinking, dict) and thinking.get("type") == "enabled":
        budget = thinking.get("budget_tokens")
        if isinstance(budget, int) and not isinstance(budget, bool):
            budget = min(budget, result["max_tokens"] - 1)
            if budget >= MIN_THINKING:
                result["thinking"] = {"type": "enabled", "budget_tokens": budget}
    if final and result.get("tools"):
        result["tool_choice"] = {"type": "none"}
    return result


def _blocks(response: dict) -> list[dict]:
    content = response.get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def has_tool_calls(response: dict) -> bool:
    return any(block.get("type") in TOOL_BLOCKS for block in _blocks(response))


def response_text(response: dict) -> str:
    return "".join(
        block["text"]
        for block in _blocks(response)
        if block.get("type") == "text" and isinstance(block.get("text"), str)
    )


def usage(response: dict) -> tuple[int, int]:
    """(prompt, output) tokens: input including cache reads and writes, and output."""
    data = response.get("usage")
    if not isinstance(data, dict):
        return 0, 0

    def number(key: str) -> int:
        value = data.get(key)
        return (
            value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
        )

    prompt = sum(
        number(k)
        for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    return prompt, number("output_tokens")


def sse(response: dict) -> bytes:
    """A whole Messages answer as Anthropic SSE (D7): message_start, then per block
    content_block_start, one delta and content_block_stop, then message_delta and message_stop."""

    def event(kind: str, data: dict) -> str:
        return f"event: {kind}\ndata: {json.dumps({'type': kind, **data})}\n\n"

    prompt_usage = (
        dict(response.get("usage") or {}) if isinstance(response.get("usage"), dict) else {}
    )
    output_tokens = prompt_usage.pop("output_tokens", 0)
    start = {
        "id": response.get("id", ""),
        "type": "message",
        "role": "assistant",
        "model": response.get("model", ""),
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {**prompt_usage, "output_tokens": 0},
    }
    parts = [event("message_start", {"message": start})]
    index = -1  # counts emitted blocks only: a client fills content[index]
    for block in _blocks(response):
        kind = block.get("type")
        if kind == "text":
            opening, delta = (
                {"type": "text", "text": ""},
                {"type": "text_delta", "text": block.get("text", "")},
            )
        elif kind in TOOL_BLOCKS:
            opening = {key: value for key, value in block.items() if key != "input"}
            opening["input"] = {}
            delta = {
                "type": "input_json_delta",
                "partial_json": json.dumps(block.get("input", {})),
            }
        elif kind == "thinking":
            opening = {"type": "thinking", "thinking": "", "signature": ""}
            delta = {"type": "thinking_delta", "thinking": block.get("thinking", "")}
        elif kind == "redacted_thinking":
            opening, delta = dict(block), None  # echoed back unchanged on a continuation
        else:
            continue  # a block type the Messages stream does not carry
        index += 1
        parts.append(event("content_block_start", {"index": index, "content_block": opening}))
        if delta is not None:
            parts.append(event("content_block_delta", {"index": index, "delta": delta}))
        if kind == "thinking" and block.get("signature"):
            parts.append(
                event(
                    "content_block_delta",
                    {
                        "index": index,
                        "delta": {"type": "signature_delta", "signature": block["signature"]},
                    },
                )
            )
        parts.append(event("content_block_stop", {"index": index}))
    parts.append(
        event(
            "message_delta",
            {
                "delta": {
                    "stop_reason": response.get("stop_reason"),
                    "stop_sequence": response.get("stop_sequence"),
                },
                "usage": {"output_tokens": output_tokens},
            },
        )
    )
    parts.append(event("message_stop", {}))
    return "".join(parts).encode()


class AnthropicProxy(ModelProxy):
    def __init__(
        self,
        gateway_url: str,
        gateway_key: str,
        socket_path: Path,
        alias: str,
        *,
        peer: int = CLI_UID,
        **kwargs,
    ):
        super().__init__(gateway_url, gateway_key, **kwargs)
        self.socket_path = socket_path
        self.alias = alias
        self.peer = peer

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=UPSTREAM_TIMEOUT,
            trust_env=False,
            follow_redirects=False,
            transport=self._transport,
        )
        own_dir(self.socket_path.parent)
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        bind_world(listener, self.socket_path)
        self._server = await asyncio.start_unix_server(self._connection, sock=listener)

    @property
    def base_url(self) -> str:
        return f"unix:{self.socket_path}"

    async def _connection(self, reader, writer):
        sock = writer.get_extra_info("socket")
        try:
            allowed = sock is not None and peer_uid(sock) == self.peer
        except OSError:
            allowed = False
        if not allowed:
            writer.close()
            return
        await super()._connection(reader, writer)

    @staticmethod
    def _refuse_headers(request: h11.Request, raw: bytes):
        answer = ModelProxy._refuse_headers(
            h11.Request(
                method=request.method,
                target=b"/v1/chat/completions" if request.target in MESSAGES_PATHS else b"/",
                headers=request.headers,
            ),
            raw,
        )
        return answer  # converted to the Anthropic shape by _send

    def _state(self, request: h11.Request) -> TurnState | None:
        token = None
        for name, value in request.headers:
            if name == b"authorization" and value.startswith(b"Bearer "):
                token = value[7:].decode("ascii", "replace").strip()
            elif name == b"x-api-key" and token is None:
                token = value.decode("ascii", "replace").strip()
        return self._turns.get(token) if token else None

    async def _send(self, connection, writer, answer):
        status, kind, body = answer
        if kind == "application/json" and status >= 400:
            try:
                parsed = json.loads(body)
                if "error" in parsed and "type" not in parsed:  # a kit refusal
                    answer = anthropic_refusal(answer)
            except ValueError:
                pass
        await super()._send(connection, writer, answer)

    async def _answer(self, request: h11.Request, body: bytes, refused):
        if refused is not None:
            return refused
        state = self._state(request)
        if state is None:
            return anthropic_refusal(UNKNOWN_TURN)
        try:
            parsed = json.loads(body)
        except (ValueError, UnicodeDecodeError, RecursionError):
            return anthropic_refusal(BAD_REQUEST)
        try:
            return await self.messages(state, parsed)
        except Exception as error:  # noqa: BLE001 - kept for the turn, never sent on
            if state.internal is None:
                state.internal = error
            return anthropic_refusal(INTERNAL)

    async def chat(self, state: TurnState, body: object):
        return anthropic_refusal(NOT_FOUND)  # this proxy serves the Messages route only

    async def messages(self, state: TurnState, body: object):
        """Serve one Messages call of `state`'s turn: (status, content type, body)."""
        if state.failure is not None:
            return refusal(state.failure_status, state.failure.code)
        if state.step_limited or state.refused_over_cap:
            return refusal(429, STEP_LIMIT)
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
            return anthropic_refusal(BAD_REQUEST)
        try:
            check_content(body)
        except Refused:
            return anthropic_refusal(BAD_REQUEST)
        model = self.alias  # whatever the CLI named (§7.2.5)
        if model not in state.aliases:
            state.failure = ModelError("MODEL_ACCESS_DENIED", status=403)
            state.failure_status = 403
            return refusal(403, "MODEL_ACCESS_DENIED")
        if state.calls >= state.max_steps:
            state.refused_over_cap = True
            return refusal(429, STEP_LIMIT)
        state.calls += 1
        step = state.calls
        final = step >= state.max_steps
        main = is_main_loop(body, state)
        started = time.monotonic()
        with span("llm", "LLM", **{"llm.model_name": model, "llm.provider": "gateway"}) as llm:
            outcome = await self._upstream(state, upstream_body(body, model, final))
            if isinstance(outcome, ModelError):
                llm.set_attribute("agentbreeder.error_code", outcome.code)
            else:
                prompt, output = usage(outcome[1])
                llm.set_attribute("llm.token_count.prompt", prompt)
                llm.set_attribute("llm.token_count.completion", output)
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
            "finish_reason": response.get("stop_reason")
            if isinstance(response.get("stop_reason"), str)
            else "",
            "cost_usd": state.tally["last"],
            "served_by": served_by,
            "fallbacks": fallbacks,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "main_loop": main,
        }
        if limited:
            row["step_limited"] = True
        if state.closed:
            return refusal(429, STEP_LIMIT)
        if state.on_model_called is not None:
            await state.on_model_called(row)
        if limited:
            state.step_limited = True
            return refusal(429, STEP_LIMIT)
        if final:
            state.final_text = response_text(response)
            state.final_main = main
        response = {key: response[key] for key in RESPONSE_FIELDS if key in response}
        if body.get("stream") is True:
            return 200, "text/event-stream", sse(response)
        return 200, "application/json", json.dumps(response).encode()

    async def _upstream(self, state: TurnState, body: dict):
        """One logical call with 1.3.0's retries (as the kit's chat route, on /v1/messages)."""
        if self._client is None:
            raise RuntimeError("start() the proxy first")
        attempt = 1
        while True:
            message = ""
            try:
                async with self._client.stream(
                    "POST",
                    f"{self.gateway_url}{UPSTREAM_PATH}",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self._key}",
                        "anthropic-version": ANTHROPIC_VERSION,
                    },
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
