"""The conformance checks (#759) against a stub agent that serves protocol v1, and against the
same stub with one behaviour broken at a time."""

from __future__ import annotations

import pytest
from conformance_support import FRAMEWORK, RUNTIME, StubAgent, otlp_request, otlp_span

from agentbreeder.conformance import checks, fakes, otlp, protocol

TOKEN = "t" * 32
KEY = "sk-conf-" + "9" * 48
IMPLEMENTED = [check.id for check in checks.CHECKS if check.run is not None]


class World:
    def __init__(self, flaws=(), profile="v1", logs=True, expected=True):
        self.harness = fakes.Harness(TOKEN)
        gateway = f"http://127.0.0.1:{self.harness.gateway_port}"
        self.stub = StubAgent(gateway, KEY, flaws=frozenset(flaws), deployment="dep-1")
        self.context = checks.Context(
            agent=checks.Agent("127.0.0.1", self.stub.port),
            control=checks.Control(f"http://127.0.0.1:{self.harness.control_port}", TOKEN),
            profile=profile,
            run_id="abcdef012345",
            expected={"runtime": RUNTIME, "framework": FRAMEWORK} if expected else {},
            secrets={"the gateway key": KEY},
            deployment_id="dep-1",
            logs=self.stub.logs if logs else None,
            span_wait=0.6,
            span_settle=0.1,
            busy_delay=0.6,
        )

    def run(self, *ids):
        selected = tuple(c for c in checks.CHECKS if not ids or c.id in ids)
        return {row["id"]: row for row in checks.run(self.context, selected)}

    def close(self):
        self.stub.close()
        self.harness.close()


@pytest.fixture
def world():
    made = []

    def make(**options):
        made.append(World(**options))
        return made[-1]

    yield make
    for one in made:
        one.close()


def test_a_conformant_agent_passes_every_implemented_check(world):
    rows = world().run()
    assert [row for row in rows.values() if row["status"] == checks.FAIL] == []
    assert {name for name, row in rows.items() if row["status"] == checks.PASS} == set(IMPLEMENTED)
    assert all(rows[name]["detail"] == "" for name in IMPLEMENTED)
    # Unbuilt checks are reported, never dropped, and a v1 image is not held to the kit's.
    pending = {c.id for c in checks.CHECKS if c.run is None and "v1" in c.profiles}
    assert {n for n, row in rows.items() if row["status"] == checks.NOT_RUN} == pending
    kit_only = {c.id for c in checks.CHECKS if c.profiles == ("kit",)}
    assert {n for n, row in rows.items() if row["status"] == checks.NOT_APPLICABLE} == kit_only
    assert checks.result(list(rows.values())) == "incomplete"


def test_a_kit_image_is_held_to_the_kit_checks(world):
    rows = world(profile="kit").run("serving.readiness", "proxy.requests", "serving.health")
    assert rows["serving.readiness"]["status"] == checks.NOT_RUN
    assert "F2b" in rows["proxy.requests"]["detail"]
    assert rows["serving.health"]["status"] == checks.PASS


# One broken behaviour -> the check that must catch it.
FLAWS = {
    "unhealthy": "serving.health",
    "any_id": "serving.bad_request",
    "any_size": "serving.oversize_body",
    "any_length": "serving.long_message",
    "any_type": "serving.content_type",
    "any_host": "serving.host",
    "missing_field": "reply.fields",
    "wrong_type": "reply.fields",
    "wrong_framework": "reply.identity",
    "retry_budget": "model_errors.budget",
    "no_retry_after": "model_errors.rate_limited",
    "wrong_code": "model_errors.access_denied",
    "uncoded": "model_errors.request_refused",
    "many_retries": "model_errors.unavailable",
    "leak_upstream": "model_errors.unavailable",
    "no_correlation": "model_errors.budget",
    "wrong_cost": "cost.reported",
    "no_fallbacks": "cost.fallbacks",
    "no_history": "sessions.history",
    "serial_sessions": "sessions.busy",
    "shared_history": "sessions.customers",
    "no_trace_id": "traces.root_span",
    "two_roots": "traces.root_span",
    "wrong_user": "traces.root_span",
    "no_llm_span": "traces.llm_span",
    "leak_key": "hygiene.replies",
}


@pytest.mark.parametrize("flaw", sorted(FLAWS))
def test_a_broken_behaviour_fails_its_check(world, flaw):
    row = world(flaws=[flaw]).run(FLAWS[flaw])[FLAWS[flaw]]
    assert row["status"] == checks.FAIL, row
    assert row["detail"] and KEY not in row["detail"]


def test_every_flaw_names_an_implemented_check():
    assert set(FLAWS.values()) <= set(IMPLEMENTED)


@pytest.mark.parametrize(
    ("flaw", "check"),
    [
        ("log_upstream", "model_errors.text_logs"),
        ("span_upstream", "model_errors.text_spans"),
        ("log_key", "hygiene.logs"),
        ("span_key", "hygiene.spans"),
    ],
)
def test_text_left_in_logs_and_spans_is_found_at_the_end(world, flaw, check):
    rows = world(flaws=[flaw]).run("model_errors.unavailable", "model_errors.budget", check)
    assert rows["model_errors.unavailable"]["status"] == checks.PASS
    assert rows[check]["status"] == checks.FAIL
    clean = world().run("model_errors.unavailable", check)
    assert clean[check]["status"] == checks.PASS


def test_checks_that_have_nothing_to_read_say_so(world):
    blind = world(logs=False, flaws=["no_spans"], expected=False)
    rows = blind.run(
        "model_errors.budget",
        "hygiene.logs",
        "hygiene.spans",
        "model_errors.text_logs",
        "model_errors.text_spans",
        "reply.identity",
    )
    for name in ("hygiene.logs", "hygiene.spans", "model_errors.text_logs", "reply.identity"):
        assert rows[name]["status"] == checks.NOT_RUN and rows[name]["detail"], name
    assert rows["model_errors.text_spans"]["status"] == checks.NOT_RUN
    alone = world().run("model_errors.text_logs", "model_errors.text_spans")
    assert {row["status"] for row in alone.values()} == {checks.NOT_RUN}


def test_an_agent_that_does_not_answer_fails_without_its_error_text(world):
    one = world()
    one.stub.close()
    rows = one.run("serving.health", "reply.fields")
    for row in rows.values():
        assert row["status"] == checks.FAIL
        assert row["detail"].startswith("the check could not finish: ")


def test_a_record_the_fakes_cut_short_fails_the_check(world, monkeypatch):
    one = world()
    monkeypatch.setattr(fakes, "MAX_REQUESTS", 1)
    rows = one.run("sessions.history")
    assert rows["sessions.history"]["status"] == checks.FAIL
    assert "dropped" in rows["sessions.history"]["detail"]
    monkeypatch.setattr(fakes, "MAX_REQUESTS", 2000)
    monkeypatch.setattr(fakes, "MAX_SPAN_PAYLOADS", 0)
    assert one.run("traces.root_span")["traces.root_span"]["status"] == checks.FAIL


def test_replies_too_large_to_keep_are_not_called_clean(world, monkeypatch):
    monkeypatch.setattr(checks, "MAX_SEEN", 1)
    one = world()
    one.context.agent.request("GET", "/health")
    row = one.run("serving.health", "hygiene.replies")["hygiene.replies"]
    assert row["status"] == checks.NOT_RUN and "too large" in row["detail"]


def test_spans_are_parsed_once_and_capped(world, monkeypatch):
    one = world()
    one.run("traces.root_span")
    first = one.context.spans()
    assert first and one.context.spans() == first
    parsed = one.context._parsed
    assert one.context.spans() == first and one.context._parsed is parsed
    monkeypatch.setattr(checks, "MAX_SPANS", 1)
    one.context._parsed = []
    assert len(one.context.spans()) == 1
    one.context.control.reset()
    assert one.context.spans() == []


def test_no_spans_fails_the_trace_checks(world):
    rows = world(flaws=["no_spans"]).run("traces.root_span", "traces.llm_span")
    assert rows["traces.root_span"]["status"] == checks.FAIL
    assert "no span" in rows["traces.root_span"]["detail"]
    assert rows["traces.llm_span"]["status"] == checks.FAIL


def test_the_result_of_a_run():
    def rows(*statuses):
        return [{"status": status} for status in statuses]

    assert checks.result(rows("pass", "not_applicable")) == "pass"
    assert checks.result(rows("pass", "not_run")) == "incomplete"
    assert checks.result(rows("pass", "not_run", "fail")) == "fail"


def test_the_catalogue_covers_the_spec_table():
    ids = [check.id for check in checks.CHECKS]
    assert len(ids) == len(set(ids))
    assert {check.group for check in checks.CHECKS} == {
        "Serving",
        "Reply",
        "Knowledge",
        "Tools",
        "Step cap",
        "Model errors",
        "Proxy",
        "Cost",
        "Sessions",
        "Hygiene",
        "Traces",
    }
    for check in checks.CHECKS:
        assert (check.run is None) == bool(check.pending), check.id
        assert set(check.profiles) <= set(protocol.PROFILES) and "kit" in check.profiles
        assert check.id.split(".")[0] == check.group.lower().replace(" ", "_")
    # The checks that read what the others left run after every check that talks to the agent.
    last_turn = max(i for i, c in enumerate(checks.CHECKS) if c.id == "hygiene.replies")
    for name in ("hygiene.logs", "hygiene.spans", "model_errors.text_logs"):
        assert ids.index(name) > last_turn


def test_reply_problems():
    assert protocol.reply_problems([]) == ["the reply is not a JSON object"]
    good = {
        name: (None if nullable else kinds[0]())
        for name, (kinds, nullable) in protocol.REPLY_FIELDS.items()
    }
    assert protocol.reply_problems(good) == []
    bad = {**good, "reply": None, "kb_searches": True, "served_by": [1], "cost_usd": "1"}
    del bad["turn_id"]
    assert protocol.reply_problems(bad) == [
        "reply is null",
        "turn_id is missing",
        "cost_usd has type str",
        "kb_searches has type bool",
        "served_by must hold strings",
    ]


def test_secret_fragments():
    found = checks._fragments({"key": "sk-conf-" + "ab" * 24, "short": "abc"})
    assert found["short"] == ["abc"]
    assert found["key"][1:] == ["sk-conf-abababab", "abababab"]


# -- the OTLP reader -------------------------------------------------------------------------


def test_spans_are_read_from_an_otlp_payload():
    trace, root, child = "ab" * 16, "01" * 8, "02" * 8
    attributes = {"s": "text", "n": 7, "neg": -2, "f": 1.5, "b": True, "l": ["x", "y"]}
    payload = otlp_request(
        {"openinference.project.name": "agent"},
        [
            otlp_span(trace, root, "", "agent_turn", attributes),
            otlp_span(trace, child, root, "call_llm", {}),
        ],
    )
    first, second = otlp.spans(payload)
    assert first == {
        "trace_id": trace,
        "span_id": root,
        "parent_span_id": "",
        "name": "agent_turn",
        "attributes": attributes,
        "resource": {"openinference.project.name": "agent"},
    }
    assert second["parent_span_id"] == root and second["attributes"] == {}


@pytest.mark.parametrize(
    "payload",
    [b"", b"\xff\xff\xff", b"not protobuf at all", b"\x0a\xff\x01", b"\x0f", b"\x80" * 12],
)
def test_a_payload_that_is_not_otlp_gives_no_spans(payload):
    assert otlp.spans(payload) == []


def test_unknown_fields_are_skipped():
    span = otlp_span("ab" * 16, "01" * 8, "", "agent_turn", {"k": "v"})
    extra = b"\x38\x05" + b"\x45\x00\x00\x00\x00" + b"\x49" + b"\x00" * 8  # varint, fixed32/64
    payload = otlp_request({}, [span + extra]) + b"\x10\x01"
    (found,) = otlp.spans(payload)
    assert found["name"] == "agent_turn" and found["attributes"] == {"k": "v"}


# -- the docs say what the code checks ---------------------------------------------------------


def _doc(name: str) -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parents[1] / "docs" / name).read_text(encoding="utf-8")


def test_the_protocol_page_has_every_field_status_and_code():
    page = _doc("agent-protocol.md")
    for name in protocol.REPLY_FIELDS:
        assert f"| `{name}` |" in page, name
    for status in protocol.STATUSES:
        assert f"| {status} |" in page, status
    for code, status in protocol.MODEL_ERRORS.items():
        assert f"| `{code}` | {status} |" in page, code
    for name in protocol.ERROR_FIELDS:
        assert f'"{name}"' in page, name
    for host in protocol.ALLOWED_HOSTS:
        assert f"`{host}`" in page
    assert "64 KiB" in page and "4,000 characters" in page and f"`{protocol.GUEST}`" in page
    for attribute in (
        *protocol.ROOT_ATTRIBUTES,
        protocol.DEPLOYMENT_ATTRIBUTE,
        protocol.ROOT_SPAN,
    ):
        assert f"`{attribute}`" in page, attribute


def test_the_command_page_lists_every_check_and_whether_it_is_built():
    page = _doc("commands/conformance.md")
    for check in checks.CHECKS:
        (line,) = [row for row in page.splitlines() if row.startswith(f"| `{check.id}` |")]
        assert check.title in line
        assert line.endswith("| built |") == (check.run is not None), check.id
        assert ("| kit |" in line) == (check.profiles == ("kit",)), check.id
    built = sum(1 for check in checks.CHECKS if check.run)
    assert f"{len(checks.CHECKS)} checks, {built} built." in page
    record = _doc("superpowers/verification/F2a-adk-baseline.md")
    assert "Status: **not run.**" in record  # a run replaces this line and this assertion
    assert f"The {built} built checks" in record
    assert f"{len(checks.CHECKS) - built} of the {len(checks.CHECKS)} checks" in record


def test_the_control_api_never_goes_through_a_proxy(world, monkeypatch):
    # A proxy from the environment would get the control token and break the run.
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    assert world().context.control.healthy()


def test_a_reset_on_an_oversize_body_is_a_failure_that_says_so(world, monkeypatch):
    one = world()

    def reset(*args, **options):
        raise ConnectionResetError

    monkeypatch.setattr(one.context.agent, "post", reset)
    row = one.run("serving.oversize_body")["serving.oversize_body"]
    assert row["status"] == checks.FAIL
    assert "reset before a status could be read" in row["detail"]


def test_an_all_zero_parent_is_not_a_child_span(monkeypatch):
    root = {"span_id": "a" * 16, "parent_span_id": "", "attributes": {}}
    zero = {
        "span_id": "b" * 16,
        "parent_span_id": "0" * 16,
        "attributes": {protocol.SPAN_KIND: "LLM"},
    }
    monkeypatch.setattr(checks, "_traced_turn", lambda ctx: (None, [root, zero], None, None))
    with pytest.raises(checks.Failure, match="no child LLM span"):
        checks.traces_llm(None)
