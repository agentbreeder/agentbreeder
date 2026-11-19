"""Parity: the kit's model-call rules against `runtime/adk@1.3.0`'s own functions (#1083 F2b,
multi-framework spec §7.1).

1.3.0 reads LiteLLM and OpenAI objects (`agent.py`); the kit reads raw HTTP answers
(`runtime_kit/errors.py`). These tests take 1.3.0's functions from its published `agent.py`
source (by AST, so google-adk need not be installed), feed them a LiteLLM-shaped failure or
response, feed the kit the same status, error type and headers, and compare the answers.
"""

from __future__ import annotations

import ast
import time
import types
from pathlib import Path

import pytest

from agentbreeder.runtime_kit import errors as kit

AGENT = (
    Path(__file__).resolve().parents[1]
    / "src/agentbreeder/builtin_registry/runtime/adk/1.3.0/runtime/agent.py"
)
FUNCTIONS = {
    "new_tally",
    "_headers",
    "response_cost",
    "response_fallback",
    "upstream_status",
    "upstream_kind",
    "retry_after",
    "failure_headers",
    "retry_delay",
    "turn_fallbacks",
    "root_attributes",
    "turn_cost_usd",
    "classify",
}
CONSTANTS = {
    "TURN_TIMEOUT",
    "COST_HEADER",
    "FALLBACKS_HEADER",
    "GROUP_HEADER",
    "ALIAS",
    "MAX_FALLBACKS",
    "MAX_ATTEMPTS",
    "RETRY_STATUSES",
    "TRANSIENT_4XX",
    "RETRY_BACKOFF",
    "RATE_LIMIT_WAIT",
    "MAX_RETRY_AFTER",
    "RETRY_DEADLINE",
}


class _GenaiAPIError(Exception):
    code = None


def _adk():
    """1.3.0's functions and constants, compiled from its source with stand-ins for the two
    library names they use (`genai_errors`, and `ModelError`, taken from the same file)."""
    tree = ast.parse(AGENT.read_text(encoding="utf-8"))
    keep = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            keep.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "ModelError":
            keep.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in CONSTANTS for t in node.targets
        ):
            keep.append(node)
    module = ast.Module(body=keep, type_ignores=[])
    namespace = {
        "__name__": "adk_1_3_0_agent",
        "math": __import__("math"),
        "re": __import__("re"),
        "time": time,
        "genai_errors": types.SimpleNamespace(APIError=_GenaiAPIError),
    }
    exec(compile(module, str(AGENT), "exec"), namespace)  # noqa: S102 - our own published file
    missing = (FUNCTIONS | CONSTANTS | {"ModelError"}) - set(namespace)
    assert not missing, f"1.3.0's agent.py no longer has {sorted(missing)}"
    return types.SimpleNamespace(**namespace)


ADK = _adk()


def _litellm_failure(status: int, kind: str | None, headers: dict | None, nested: bool = True):
    """A failure shaped as LiteLLM raises it to ADK 1.3.0."""
    body = {"type": kind} if kind is not None else {}
    error_type = type(
        "ProviderError",
        (Exception,),
        {
            "__module__": "litellm.exceptions",
            "status_code": status,
            "body": {"error": body} if nested else body,
            "litellm_response_headers": headers,
        },
    )
    return error_type("provider text")


def _litellm_response(headers: dict):
    return types.SimpleNamespace(
        _hidden_params={"additional_headers": {f"llm_provider-{k}": v for k, v in headers.items()}}
    )


def test_constants_match_1_3_0():
    for name in sorted(CONSTANTS - {"COST_HEADER", "FALLBACKS_HEADER", "GROUP_HEADER", "ALIAS"}):
        assert getattr(kit, name) == getattr(ADK, name), name
    assert kit.ALIAS.pattern == ADK.ALIAS.pattern
    # 1.3.0 reads LiteLLM's prefixed copies of the same response headers.
    for name in ("COST_HEADER", "FALLBACKS_HEADER", "GROUP_HEADER"):
        assert getattr(ADK, name) == "llm_provider-" + getattr(kit, name)


STATUSES = [400, 401, 402, 403, 404, 408, 409, 413, 422, 429, 500, 502, 503, 504, 529]
KINDS = [None, "budget_exceeded", "throttling_error", "auth_error", "invalid_request_error"]
RETRY_AFTERS = [None, "30", "0", "-1", " 5 ", "7.5", "abc", "99999999", "1" * 13, "٣"]


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("after", RETRY_AFTERS)
def test_classify_matches_1_3_0(status, kind, after):
    headers = {"retry-after": after} if after is not None else None
    expected = ADK.classify(_litellm_failure(status, kind, headers))
    got = kit.classify(kit.Failure(status, kind, headers))
    assert (got.code, got.retry_after) == (expected.code, expected.retry_after)


@pytest.mark.parametrize("nested", [True, False])
def test_error_type_nested_or_flat_matches(nested):
    for kind in KINDS:
        failure = _litellm_failure(400, kind, None, nested=nested)
        body = {"error": {"type": kind}} if nested else {"type": kind}
        assert kit.upstream_kind(body) == ADK.upstream_kind(failure)


def _tally_pair(deadline_in: float, waited: float):
    pair = []
    for module in (ADK, kit):
        tally = module.new_tally()
        tally["deadline"] = time.monotonic() + deadline_in
        tally["waited"] = waited
        pair.append(tally)
    return pair


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("after", [None, "1", "4", "30"])
@pytest.mark.parametrize("attempt", [1, 2, 3])
@pytest.mark.parametrize("deadline_in,waited", [(1000.0, 0.0), (1000.0, 8.0), (0.1, 0.0)])
def test_retry_delay_matches_1_3_0(status, kind, after, attempt, deadline_in, waited):
    headers = {"retry-after": after} if after is not None else None
    adk_tally, kit_tally = _tally_pair(deadline_in, waited)
    expected = ADK.retry_delay(_litellm_failure(status, kind, headers), attempt, adk_tally)
    got = kit.retry_delay(kit.Failure(status, kind, headers), attempt, kit_tally)
    assert got == expected
    assert kit_tally["waited"] == adk_tally["waited"]


def test_retry_delay_without_a_tally_matches():
    for status in STATUSES:
        for kind in KINDS:
            expected = ADK.retry_delay(_litellm_failure(status, kind, None), 1, None)
            assert kit.retry_delay(kit.Failure(status, kind, None), 1, None) == expected


def test_connection_failures_count_as_1_3_0s_litellm_statuses():
    # LiteLLM raised APIConnectionError (status 500) and Timeout (status 408) to 1.3.0.
    for status in (kit.CONNECTION_STATUS, kit.TIMEOUT_STATUS):
        adk_tally, kit_tally = _tally_pair(1000.0, 0.0)
        assert kit.retry_delay(kit.Failure(status), 1, kit_tally) == ADK.retry_delay(
            _litellm_failure(status, None, None), 1, adk_tally
        )
        assert (
            kit.classify(kit.Failure(status)).code
            == ADK.classify(_litellm_failure(status, None, None)).code
        )


COSTS = [None, "0.0125", "0", "-1", "nan", "inf", "abc", "", True, 3, 0.5]
FALLBACKS = [None, "0", "2", "100", "101", "1000", "-1", "x", 3, True, 101]
GROUPS = [None, "support-model", "a" * 64, "a" * 65, "http://x", "", "../x", 7]


@pytest.mark.parametrize("cost", COSTS)
def test_response_cost_matches_1_3_0(cost):
    headers = {} if cost is None else {"x-litellm-response-cost": cost}
    assert kit.response_cost(headers) == ADK.response_cost(_litellm_response(headers))


@pytest.mark.parametrize("fallbacks", FALLBACKS)
@pytest.mark.parametrize("group", GROUPS)
def test_response_fallback_matches_1_3_0(fallbacks, group):
    headers = {}
    if fallbacks is not None:
        headers["x-litellm-attempted-fallbacks"] = fallbacks
    if group is not None:
        headers["x-litellm-model-group"] = group
    assert kit.response_fallback(headers) == ADK.response_fallback(_litellm_response(headers))


def test_a_turn_tally_matches_1_3_0s_cost_client():
    """The same three responses through 1.3.0's CostClient bookkeeping and the kit's."""
    responses = [
        {"x-litellm-response-cost": "0.01", "x-litellm-model-group": "support-model"},
        {
            "x-litellm-response-cost": "0.02",
            "x-litellm-attempted-fallbacks": "1",
            "x-litellm-model-group": "fallback-model",
        },
        {"x-litellm-model-group": "support-model"},  # no cost: the turn's cost is unknown
    ]
    adk_tally, kit_tally = ADK.new_tally(), kit.new_tally()
    for headers in responses:
        # CostClient.acompletion's bookkeeping, written out as in 1.3.0.
        response = _litellm_response(headers)
        adk_tally["calls"] += 1
        cost = ADK.response_cost(response)
        adk_tally["last"] = cost
        if cost is None:
            adk_tally["unknown"] += 1
        else:
            adk_tally["usd"] += cost
        count, group = ADK.response_fallback(response)
        adk_tally["route"] = (count, group)
        adk_tally["fallbacks"] += count
        if group is not None and group not in adk_tally["served_by"]:
            adk_tally["served_by"].append(group)
        kit.record_response(kit_tally, headers)
        assert kit.turn_cost_usd(kit_tally) == ADK.turn_cost_usd(adk_tally)
        assert kit.turn_fallbacks(kit_tally) == ADK.turn_fallbacks(adk_tally)
        assert kit.root_attributes(kit_tally) == ADK.root_attributes(adk_tally)
    for key in ("calls", "unknown", "usd", "last", "fallbacks", "served_by", "route"):
        assert kit_tally[key] == adk_tally[key], key


def test_an_empty_turn_matches_1_3_0():
    assert kit.turn_cost_usd(kit.new_tally()) == ADK.turn_cost_usd(ADK.new_tally())
    assert kit.turn_fallbacks(kit.new_tally()) == ADK.turn_fallbacks(ADK.new_tally())
    assert kit.root_attributes(kit.new_tally()) == ADK.root_attributes(ADK.new_tally())


def test_error_messages_match_1_3_0s_server():
    """Every 1.3.0 code has 1.3.0's status and text; the kit adds only MODEL_STEP_LIMIT."""
    tree = ast.parse(AGENT.with_name("server.py").read_text(encoding="utf-8"))
    (node,) = [
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", "") == "MODEL_ERRORS" for t in n.targets)
    ]
    adk_errors = ast.literal_eval(node.value)
    assert set(kit.MODEL_ERRORS) - set(adk_errors) == {"MODEL_STEP_LIMIT"}
    for code, value in adk_errors.items():
        assert kit.MODEL_ERRORS[code] == value, code
