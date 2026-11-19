"""The runtime kit's loopback model proxy (#1083 F2b, multi-framework spec §7.2).

The proxy runs on a real loopback socket; the gateway is an httpx MockTransport that records
every upstream request and answers from a script. Malformed requests go over a raw socket.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agentbreeder.runtime_kit import proxy as proxy_module
from agentbreeder.runtime_kit import redaction
from agentbreeder.runtime_kit.errors import ModelError
from agentbreeder.runtime_kit.proxy import ModelProxy, StepLimit, TurnState, decide

KEY = "gateway-key-0123456789abcdef"
ALIAS = "support-model"
SYSTEM = "You are Northwind's support agent."
TOOLS = [{"type": "function", "function": {"name": "get_order", "parameters": {}}}]


def answer(text="Done.", tool_call=False, cost="0.001", headers=None, usage=(7, 3)):
    message = {"role": "assistant", "content": None if tool_call else text}
    if tool_call:
        message["tool_calls"] = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_order", "arguments": "{}"},
            }
        ]
    body = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "gemini",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_call else "stop",
            }
        ],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
    }
    head = {"x-litellm-model-group": ALIAS, "x-litellm-attempted-fallbacks": "0"}
    if cost is not None:
        head["x-litellm-response-cost"] = cost
    head.update(headers or {})
    return httpx.Response(200, json=body, headers=head)


class Gateway:
    """A scripted upstream: each call takes the next answer (the last one repeats)."""

    def __init__(self, *answers):
        self.answers = list(answers) or [answer()]
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        item = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def bodies(self):
        return [json.loads(r.content) for r in self.requests]


def error(status, kind="api_error", message="provider said SECRET-TEXT", headers=None):
    return httpx.Response(
        status, json={"error": {"type": kind, "message": message}}, headers=headers or {}
    )


async def _no_sleep(seconds):
    return None


def run(gateway, steps, *, max_steps=4, state_kwargs=None):
    """Start a proxy, open one turn, run `steps(client, state, proxy)`, close everything."""

    async def main():
        proxy = ModelProxy(
            "http://gateway:4000", KEY, httpx.MockTransport(gateway), sleep=_no_sleep
        )
        await proxy.start()
        rows: list[dict] = []

        async def model_called(row):
            rows.append(row)

        fields = {
            "turn_id": "t1",
            "max_steps": max_steps,
            "aliases": frozenset({ALIAS}),
            "reasoning": "low",
            "system": SYSTEM,
            "toolset": frozenset({"get_order"}),
            "on_model_called": model_called,
        }
        state = TurnState(**{**fields, **(state_kwargs or {})})
        token = proxy.open_turn(state)
        try:
            async with httpx.AsyncClient(base_url=proxy.base_url, trust_env=False) as client:
                client.headers["Authorization"] = f"Bearer {token}"
                result = await steps(client, state, proxy)
        finally:
            proxy.close_turn(token)
            await proxy.close()
        return result, state, rows, token

    return asyncio.run(main())


def chat(**extra):
    body = {
        "model": ALIAS,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "hi"}],
        "tools": TOOLS,
    }
    body.update(extra)
    return body


async def post(client, body=None, **kwargs):
    return await client.post(
        "/chat/completions", json=body if body is not None else chat(), **kwargs
    )


# -- requests are built from scratch -------------------------------------------------------


def test_listens_on_loopback_and_relays_an_answer():
    gateway = Gateway(answer("Order 1042 has shipped."))

    async def steps(client, state, proxy):
        assert proxy.base_url.startswith("http://127.0.0.1:")
        return await post(client)

    response, state, rows, _ = run(gateway, steps)
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Order 1042 has shipped."
    (request,) = gateway.requests
    assert str(request.url) == "http://gateway:4000/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert state.calls == 1 and state.prompt_tokens == 7 and state.output_tokens == 3
    assert rows == [
        {
            "step": 1,
            "model": ALIAS,
            "prompt_tokens": 7,
            "output_tokens": 3,
            "finish_reason": "stop",
            "cost_usd": 0.001,
            "served_by": ALIAS,
            "fallbacks": 0,
            "latency_ms": rows[0]["latency_ms"],
            "main_loop": True,
        }
    ]


def test_only_allow_listed_fields_reach_the_gateway():
    gateway = Gateway()
    smuggled = {
        "api_base": "http://evil",
        "api_key": "x",
        "metadata": {"guardrails": []},
        "user": "u",
        "mock_response": "hi",
        "guardrails": [],
        "fallbacks": ["other"],
        "caching": True,
        "extra_body": {"custom_llm_provider": "openai"},
        "disable_global_guardrails": True,
        "litellm_disabled_callbacks": ["presidio"],
        "parallel_tool_calls": True,
        "reasoning_effort": "high",
        "temperature": 0.2,
        "max_tokens": 50,
    }

    async def steps(client, state, proxy):
        return await post(
            client,
            chat(**smuggled),
            headers={
                "x-litellm-disable-guardrails": "true",
                "x-litellm-guardrails": "[]",
                "X-Forwarded-For": "1.2.3.4",
            },
        )

    run(gateway, steps)
    (body,) = gateway.bodies
    assert set(body) == {
        "model",
        "messages",
        "tools",
        "temperature",
        "max_tokens",
        "reasoning_effort",
    }
    assert body["reasoning_effort"] == "low"  # the agent's, not the framework's
    sent = {name.lower() for name in gateway.requests[0].headers}
    assert not sent & {"x-litellm-disable-guardrails", "x-litellm-guardrails", "x-forwarded-for"}


def test_reasoning_none_sends_no_reasoning_effort():
    gateway = Gateway()
    run(gateway, lambda c, s, p: post(c), state_kwargs={"reasoning": None})
    assert "reasoning_effort" not in gateway.bodies[0]


def test_another_model_is_refused_and_fails_the_turn():
    gateway = Gateway()

    async def steps(client, state, proxy):
        first = await post(client, chat(model="expensive-model"))
        second = await post(client)
        return first, second

    (first, second), state, _, _ = run(gateway, steps)
    assert first.status_code == 403 and first.json()["error"]["code"] == "MODEL_ACCESS_DENIED"
    assert second.status_code == 403  # the terminal answer repeats
    assert gateway.requests == []
    with pytest.raises(ModelError) as raised:
        decide(state, "text", None)
    assert raised.value.code == "MODEL_ACCESS_DENIED"


def test_unknown_and_finished_tokens_get_a_fixed_401():
    gateway = Gateway()

    async def steps(client, state, proxy):
        anonymous = await client.post(
            "/chat/completions", json=chat(), headers={"Authorization": ""}
        )
        wrong = await client.post(
            "/chat/completions", json=chat(), headers={"Authorization": "Bearer nope"}
        )
        return anonymous, wrong

    (anonymous, wrong), _, _, token = run(gateway, steps)
    assert anonymous.status_code == wrong.status_code == 401
    assert wrong.json() == {
        "error": {"message": "TURN_UNKNOWN", "type": "turn_unknown", "code": "TURN_UNKNOWN"}
    }
    assert token not in redaction.values()  # forgotten when the turn closed

    async def after_close():
        proxy = ModelProxy("http://gateway:4000", KEY, httpx.MockTransport(gateway))
        await proxy.start()
        state = TurnState(turn_id="t", max_steps=4, aliases=frozenset({ALIAS}))
        old = proxy.open_turn(state)
        proxy.close_turn(old)
        async with httpx.AsyncClient(base_url=proxy.base_url, trust_env=False) as client:
            response = await client.post(
                "/chat/completions", json=chat(), headers={"Authorization": f"Bearer {old}"}
            )
        await proxy.close()
        return response

    assert asyncio.run(after_close()).status_code == 401
    assert gateway.requests == []


def test_a_bad_body_is_400_and_not_counted():
    gateway = Gateway()

    async def steps(client, state, proxy):
        not_json = await client.post(
            "/chat/completions", content=b"{nope", headers={"Content-Type": "application/json"}
        )
        no_messages = await post(client, {"model": ALIAS})
        return not_json, no_messages

    (not_json, no_messages), state, _, _ = run(gateway, steps)
    assert not_json.status_code == no_messages.status_code == 400
    assert state.calls == 0 and state.failure is None


# -- the HTTP listener ---------------------------------------------------------------------


def _raw(request: bytes) -> bytes:
    async def main():
        proxy = ModelProxy("http://gateway:4000", KEY, httpx.MockTransport(Gateway()))
        await proxy.start()
        state = TurnState(turn_id="t", max_steps=4, aliases=frozenset({ALIAS}))
        token = proxy.open_turn(state)
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(request.replace(b"TOKEN", token.encode()))
        await writer.drain()
        data = await asyncio.wait_for(reader.read(65536), 5)
        writer.close()
        await proxy.close()
        return data

    return asyncio.run(main())


BODY = json.dumps(chat()).encode()


def _request(target=b"/v1/chat/completions", method=b"POST", extra=b"", body=BODY, length=True):
    head = (
        method + b" " + target + b" HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer TOKEN\r\n"
    )
    if length:
        head += b"Content-Length: " + str(len(body)).encode() + b"\r\n"
    return head + extra + b"\r\n" + body


@pytest.mark.parametrize(
    "target",
    [
        b"/v1/../key/generate",
        b"/v1/chat/completions?x=1",
        b"/v1/embeddings",
        b"/v1/messages",
        b"/key/generate",
        b"http://127.0.0.1/v1/chat/completions",
        b"/v1/chat/completions/",
        b"/v1/./chat/completions",
    ],
)
def test_other_paths_are_404(target):
    assert _raw(_request(target=target)).startswith(b"HTTP/1.1 404")


def test_other_methods_are_404():
    assert _raw(_request(method=b"GET", body=b"", length=False)).startswith(b"HTTP/1.1 404")


def test_transfer_encoding_is_refused():
    chunked = b"%x\r\n%s\r\n0\r\n\r\n" % (len(BODY), BODY)
    request = _request(extra=b"Transfer-Encoding: chunked\r\n", body=chunked, length=False)
    assert _raw(request).startswith(b"HTTP/1.1 400")


def test_two_content_lengths_are_refused():
    request = _request(extra=b"Content-Length: " + str(len(BODY)).encode() + b"\r\n")
    assert _raw(request).startswith(b"HTTP/1.1 400")


def test_a_body_over_the_cap_is_413(monkeypatch):
    monkeypatch.setattr(proxy_module, "MAX_BODY", 100)
    assert _raw(_request()).startswith(b"HTTP/1.1 413")


def test_garbage_is_400():
    assert _raw(b"\x00\x01 not http\r\n\r\n").startswith(b"HTTP/1.1 400")


def test_connections_over_the_cap_get_503(monkeypatch):
    monkeypatch.setattr(proxy_module, "MAX_CONNECTIONS", 0)
    assert _raw(_request()).startswith(b"HTTP/1.1 503")


def test_keep_alive_serves_several_calls_on_one_connection():
    gateway = Gateway()

    async def steps(client, state, proxy):
        return [(await post(client)).status_code for _ in range(3)]

    statuses, state, _, _ = run(gateway, steps)
    assert statuses == [200, 200, 200] and state.calls == 3


# -- the step cap --------------------------------------------------------------------------


def test_the_nth_call_has_function_calling_off():
    gateway = Gateway()

    async def steps(client, state, proxy):
        return [await post(client) for _ in range(2)]

    run(gateway, steps, max_steps=2)
    first, second = gateway.bodies
    assert "tool_choice" not in first
    assert second["tool_choice"] == "none" and second["tools"] == TOOLS  # tools kept


def test_a_tool_calling_nth_answer_is_withheld():
    """The provider ignores tool_choice none (Gemini, #1111): nothing of the answer is relayed,
    and the turn fails MODEL_STEP_LIMIT whatever the framework does next."""
    gateway = Gateway(answer(tool_call=True))

    async def steps(client, state, proxy):
        first = await post(client)
        last = await post(client)
        after = await post(client)
        return first, last, after

    (first, last, after), state, rows, _ = run(gateway, steps, max_steps=2)
    assert first.status_code == 200 and first.json()["choices"][0]["message"]["tool_calls"]
    assert last.status_code == 429
    assert last.json()["error"]["code"] == "MODEL_STEP_LIMIT"
    assert b"call_1" not in last.content
    assert after.status_code == 429
    assert len(gateway.requests) == 2  # the (N+1)th never went upstream
    assert rows[-1]["step_limited"] is True and "step_limited" not in rows[0]
    assert state.calls == 2 and state.tally["calls"] == 2  # the withheld call was billed
    with pytest.raises(StepLimit):
        decide(state, "Let me look that up.", None)


def test_the_cap_holds_with_a_legacy_function_call():
    legacy = answer()
    body = legacy.json()
    body["choices"][0]["message"]["function_call"] = {"name": "get_order", "arguments": "{}"}
    gateway = Gateway(httpx.Response(200, json=body))

    _, state, _, _ = run(gateway, lambda c, s, p: post(c), max_steps=1)
    assert state.step_limited


def test_a_refused_extra_call_after_a_main_loop_answer_keeps_its_text():
    gateway = Gateway(answer("Here is your answer."))

    async def steps(client, state, proxy):
        await post(client)
        return await post(client)

    refused, state, _, _ = run(gateway, steps, max_steps=1)
    assert refused.status_code == 429
    assert state.refused_over_cap and state.final_main
    assert decide(state, None, RuntimeError("framework gave up")) == "Here is your answer."


def test_a_refused_extra_call_after_a_hidden_nth_call_fails_the_turn():
    gateway = Gateway(answer("A summary nobody asked for."))

    async def steps(client, state, proxy):
        hidden = chat(messages=[{"role": "system", "content": "Summarise."}])
        await post(client, hidden)
        return await post(client)

    _, state, rows, _ = run(gateway, steps, max_steps=1)
    assert rows[0]["main_loop"] is False
    with pytest.raises(StepLimit):
        decide(state, "anything", None)


@pytest.mark.parametrize(
    "change",
    [
        {"messages": [{"role": "user", "content": "no system prompt"}]},
        {"tools": []},
        {"tools": TOOLS + [{"type": "function", "function": {"name": "task"}}]},
        {"messages": [{"role": "system", "content": [{"type": "text", "text": "Other."}]}]},
        {"messages": []},
    ],
)
def test_main_loop_needs_the_system_prompt_and_exactly_the_tool_set(change):
    state = TurnState(
        turn_id="t",
        max_steps=4,
        aliases=frozenset({ALIAS}),
        system=SYSTEM,
        toolset=frozenset({"get_order"}),
    )
    assert proxy_module.is_main_loop(chat(), state)
    assert not proxy_module.is_main_loop(chat(**change), state)


def test_main_loop_reads_a_content_list_system_prompt():
    state = TurnState(
        turn_id="t",
        max_steps=4,
        aliases=frozenset({ALIAS}),
        system=SYSTEM,
        toolset=frozenset({"get_order"}),
    )
    parts = [{"type": "text", "text": SYSTEM[:10]}, {"type": "text", "text": SYSTEM[10:]}]
    assert proxy_module.is_main_loop(chat(messages=[{"role": "system", "content": parts}]), state)
    no_system = TurnState(turn_id="t", max_steps=4, aliases=frozenset({ALIAS}))
    assert not proxy_module.is_main_loop(chat(), no_system)


# -- failures ------------------------------------------------------------------------------


def test_a_failing_gateway_is_tried_three_times_then_fails_the_turn_with_a_fixed_body():
    gateway = Gateway(error(500))

    async def steps(client, state, proxy):
        first = await post(client)
        again = await post(client)
        return first, again

    (first, again), state, rows, _ = run(gateway, steps)
    assert len(gateway.requests) == 3  # 1.3.0's MAX_ATTEMPTS, then the stored answer
    assert first.status_code == again.status_code == 500
    assert b"SECRET-TEXT" not in first.content
    assert first.json()["error"]["code"] == "MODEL_UNAVAILABLE"
    assert "SECRET-TEXT" in state.detail  # for the turn record only
    assert rows == []
    with pytest.raises(ModelError) as raised:
        decide(state, "the framework swallowed the error", None)
    assert raised.value.code == "MODEL_UNAVAILABLE"


@pytest.mark.parametrize(
    "response,code,attempts",
    [
        (error(400, "budget_exceeded"), "MODEL_BUDGET_EXCEEDED", 1),
        (error(401, "auth_error"), "MODEL_ACCESS_DENIED", 1),
        (error(400, "invalid_request_error"), "MODEL_REQUEST_REFUSED", 1),
        (error(504, "timeout_error"), "MODEL_UNAVAILABLE", 1),
        (error(503), "MODEL_UNAVAILABLE", 3),
        (error(429, "throttling_error", headers={"Retry-After": "30"}), "MODEL_RATE_LIMITED", 1),
        (error(429, "throttling_error", headers={"Retry-After": "2"}), "MODEL_RATE_LIMITED", 3),
    ],
)
def test_gateway_failures_are_coded_as_1_3_0(response, code, attempts):
    gateway = Gateway(response)
    _, state, _, _ = run(gateway, lambda c, s, p: post(c))
    assert state.failure.code == code
    assert len(gateway.requests) == attempts
    if code == "MODEL_RATE_LIMITED" and attempts == 1:
        assert state.failure.retry_after == 30


def test_a_transient_failure_then_success_counts_one_call():
    gateway = Gateway(error(503), answer("ok"))
    response, state, rows, _ = run(gateway, lambda c, s, p: post(c))
    assert response.status_code == 200 and len(gateway.requests) == 2
    assert state.calls == 1 and len(rows) == 1


def test_connection_errors_and_timeouts():
    connect = Gateway(httpx.ConnectError("refused"))
    _, state, _, _ = run(connect, lambda c, s, p: post(c))
    assert state.failure.code == "MODEL_UNAVAILABLE" and len(connect.requests) == 3
    slow = Gateway(httpx.ReadTimeout("slow"))
    _, state, _, _ = run(slow, lambda c, s, p: post(c))
    assert state.failure.code == "MODEL_UNAVAILABLE" and len(slow.requests) == 1


def test_a_non_json_answer_or_one_over_the_cap_is_unavailable(monkeypatch):
    gateway = Gateway(httpx.Response(200, content=b"<html>"))
    _, state, _, _ = run(gateway, lambda c, s, p: post(c))
    assert state.failure.code == "MODEL_UNAVAILABLE"
    monkeypatch.setattr(proxy_module, "MAX_RESPONSE", 10)
    gateway = Gateway(answer("a long answer"))
    _, state, _, _ = run(gateway, lambda c, s, p: post(c))
    assert state.failure.code == "MODEL_UNAVAILABLE" and len(gateway.requests) == 1


def test_a_failure_writing_the_audit_row_is_kept_for_the_turn():
    gateway = Gateway()

    async def broken(row):
        raise RuntimeError("database down")

    response, state, _, _ = run(
        gateway, lambda c, s, p: post(c), state_kwargs={"on_model_called": broken}
    )
    assert response.status_code == 500 and b"database" not in response.content
    with pytest.raises(RuntimeError):
        decide(state, "text", None)


# -- streaming and cost --------------------------------------------------------------------


def test_a_streamed_request_goes_upstream_unstreamed_and_comes_back_as_sse():
    gateway = Gateway(answer("Hello there."))

    async def steps(client, state, proxy):
        return await post(client, chat(stream=True, stream_options={"include_usage": True}))

    response, state, _, _ = run(gateway, steps)
    (body,) = gateway.bodies
    assert "stream" not in body and "stream_options" not in body
    assert response.headers["content-type"] == "text/event-stream"
    events = [line[6:] for line in response.text.split("\n\n") if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    chunks = [json.loads(e) for e in events[:-1]]
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": "Hello there."}
    assert chunks[1]["choices"][0]["finish_reason"] == "stop"
    assert chunks[2]["usage"] == {"prompt_tokens": 7, "completion_tokens": 3}
    assert state.tally["usd"] == 0.001  # the cost header was read


def test_sse_carries_tool_calls_and_leaves_usage_out_unless_asked():
    data = proxy_module.sse(answer(tool_call=True).json(), include_usage=False)
    chunks = [json.loads(e[6:]) for e in data.decode().split("\n\n") if e.startswith("data: {")]
    assert chunks[0]["choices"][0]["delta"]["tool_calls"][0]["index"] == 0
    assert not any("usage" in c for c in chunks)


def test_cost_unknown_when_a_call_has_no_header():
    gateway = Gateway(answer(cost=None))
    _, state, rows, _ = run(gateway, lambda c, s, p: post(c))
    assert state.tally["unknown"] == 1 and rows[0]["cost_usd"] is None


def test_usage_ignores_values_that_are_not_counts():
    assert proxy_module.usage({"usage": {"prompt_tokens": True, "completion_tokens": -1}}) == (
        0,
        0,
    )
    assert proxy_module.usage({}) == (0, 0)
    assert proxy_module.finish_reason({"choices": "x"}) == ""
    assert proxy_module.response_text({"choices": [{"message": "x"}]}) == ""


# -- the framework's client ----------------------------------------------------------------


def test_turn_auth_sends_the_running_turns_token():
    import contextvars

    var = contextvars.ContextVar("token", default=None)
    auth = proxy_module.TurnAuth(var)
    request = httpx.Request("POST", "http://x", headers={"Authorization": "Bearer placeholder"})
    assert "authorization" not in next(auth.auth_flow(request)).headers
    var.set("abc")
    assert next(auth.auth_flow(request)).headers["authorization"] == "Bearer abc"


def test_decide_lets_the_frameworks_own_outcome_through():
    state = TurnState(turn_id="t", max_steps=4, aliases=frozenset({ALIAS}))
    assert decide(state, "the reply", None) == "the reply"
    assert decide(state, None, None) == ""
    with pytest.raises(KeyError):
        decide(state, None, KeyError("framework bug"))


# -- review round 1 (security) -------------------------------------------------------------


def test_duplicate_content_length_with_bare_lf_is_refused():
    request = _request(extra=b"Content-Length: " + str(len(BODY)).encode() + b"\r\n")
    assert _raw(request.replace(b"\r\n", b"\n")).startswith(b"HTTP/1.1 400")


def test_a_content_length_list_is_refused():
    request = _request(length=False, extra=b"Content-Length: %d, %d\r\n" % (len(BODY), len(BODY)))
    assert _raw(request).startswith(b"HTTP/1.1 400")


def test_a_deeply_nested_body_is_400():
    body = b"[" * 200_000
    assert _raw(_request(body=body)).startswith(b"HTTP/1.1 400")


def test_an_unknown_token_is_refused_before_the_body_is_read():
    head = (
        b"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Authorization: Bearer nope\r\nContent-Length: 4000000\r\n\r\n"
    )
    assert _raw(head).startswith(b"HTTP/1.1 401")  # answered with no body sent


def test_a_request_that_trickles_past_the_deadline_is_dropped(monkeypatch):
    monkeypatch.setattr(proxy_module, "REQUEST_TIMEOUT", 0.2)

    async def main():
        proxy = ModelProxy("http://gateway:4000", KEY, httpx.MockTransport(Gateway()))
        await proxy.start()
        token = proxy.open_turn(TurnState(turn_id="t", max_steps=4, aliases=frozenset({ALIAS})))
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        head = _request().replace(b"TOKEN", token.encode()).split(b"\r\n\r\n")[0] + b"\r\n\r\n"
        writer.write(head)
        await writer.drain()
        await asyncio.sleep(0.4)
        try:
            writer.write(b"{")
            await writer.drain()
        except ConnectionError:
            pass
        data = await asyncio.wait_for(reader.read(65536), 5)
        writer.close()
        await proxy.close()
        return data

    assert asyncio.run(main()).startswith(b"HTTP/1.1 400")


def test_a_call_that_outlives_its_turn_records_nothing():
    holder: dict = {}

    def gateway(request):
        holder["proxy"].close_turn(holder["token"])  # the turn ends while the call is out
        return answer("late")

    async def main():
        proxy = ModelProxy("http://gateway:4000", KEY, httpx.MockTransport(gateway))
        await proxy.start()
        rows: list = []

        async def called(row):
            rows.append(row)

        state = TurnState(
            turn_id="t", max_steps=4, aliases=frozenset({ALIAS}), on_model_called=called
        )
        token = proxy.open_turn(state)
        holder.update(proxy=proxy, token=token)
        async with httpx.AsyncClient(base_url=proxy.base_url, trust_env=False) as client:
            response = await post(client, headers={"Authorization": f"Bearer {token}"})
        await proxy.close()
        return response, rows, state

    response, rows, state = asyncio.run(main())
    assert response.status_code == 429 and rows == [] and state.closed
