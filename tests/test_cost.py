"""`agentbreeder cost` (#906): LLM cost per deployment key and per model from LiteLLM's spend
logs (`/spend/logs/v2`), read-only."""

from __future__ import annotations

import json
import math
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from agentbreeder import cli, gateway_keys, llmcost
from agentbreeder.gateway_keys import GatewayError, LiteLLM

MASTER = "master-for-cost-tests"
DEPLOYED = "a" * 32
REMOVED = "b" * 32


def row(alias, spend=0.001, model="openai/gpt-5.6", group="chat", **extra):
    data = {
        "request_id": extra.pop("request_id", None),
        "metadata": {"user_api_key_alias": alias},
        "spend": spend,
        "model": model,
        "model_group": group,
        "status": "success",
        "prompt_tokens": 10,
        "completion_tokens": 20,
    }
    data.update(extra)
    return data


class FakeSpend:
    """`/spend/logs/v2` with pages; records the queries it was sent."""

    def __init__(self):
        self.rows: list[dict] = []
        self.page_size_cap = 1000
        self.capped = False
        self.queries: list[dict] = []
        self.status = 200
        self.body = None
        self.raw = None
        self.redirect = None
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                query = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                fake.queries.append({"path": urlsplit(self.path).path, **query})
                if self.headers.get("Authorization") != f"Bearer {MASTER}":
                    return self.reply(401, {"error": "auth"})
                if fake.redirect:
                    self.send_response(302)
                    self.send_header("Location", fake.redirect)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
                if fake.raw is not None:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(fake.raw)))
                    self.end_headers()
                    self.wfile.write(fake.raw)
                    return None
                if fake.status != 200:
                    return self.reply(fake.status, {"error": "x"})
                if fake.body is not None:
                    return self.reply(200, fake.body)
                size = min(int(query["page_size"]), fake.page_size_cap)
                page = int(query["page"])
                pages = max(1, math.ceil(len(fake.rows) / size))
                chunk = fake.rows[(page - 1) * size : page * size]
                self.reply(
                    200,
                    {
                        "data": chunk,
                        "total": len(fake.rows),
                        "page": page,
                        "total_pages": pages,
                        "total_is_capped": fake.capped,
                    },
                )

            def reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def spend():
    fake = FakeSpend()
    yield fake
    fake.server.shutdown()


@pytest.fixture
def gateway_env(identity_env, spend, tmp_path):
    master = tmp_path / "master"
    master.write_text(MASTER)
    master.chmod(0o600)
    identity_env.config["gateways"] = {
        "litellm": {
            "type": "litellm",
            "url": spend.url,
            "agent_url": "http://host.docker.internal:4000",
            "master_key": f"file:{master}",
        }
    }
    identity_env.config["environments"]["dev"].update(
        {"gateway": "litellm", "models": ["chat"], "ceilings": {"budget_usd": 5, "rpm": 60}}
    )
    identity_env.write()
    return identity_env


@pytest.fixture
def state_dir(tmp_path):
    from agentbreeder.store import Store

    store = Store(tmp_path / "state")
    store.create(DEPLOYED, {"name": "hello", "image": "i"}, "d", "ctx", environment="dev")
    store.transition(DEPLOYED, "ready")
    store.create(
        REMOVED, {"name": "old", "image": "i", "target": "kubernetes"}, "d", "ctx",
        environment="dev",
    )  # fmt: skip
    store.transition(REMOVED, "removed")
    store.close()
    return tmp_path / "state"


def run_cli(capsys, *argv):
    code = cli.main(["cost", *argv])
    out, err = capsys.readouterr()
    return code, out, err


# --- the window -------------------------------------------------------------------------------


def test_window_defaults_to_the_last_30_days():
    assert llmcost.window(None, None, date(2026, 11, 13)) == (
        date(2026, 10, 15),
        date(2026, 11, 13),
    )
    assert llmcost.window(None, "2026-01-31", date(2026, 11, 13)) == (
        date(2026, 1, 2),
        date(2026, 1, 31),
    )
    assert llmcost.window("2026-11-01", None, date(2026, 11, 13)) == (
        date(2026, 11, 1),
        date(2026, 11, 13),
    )


@pytest.mark.parametrize(
    ("since", "until", "text"),
    [
        ("2026-11-14", "2026-11-13", "after"),
        ("2025-01-01", "2026-11-13", "366"),
        ("20261101", None, "--since"),
        ("2026-13-01", None, "--since"),
        (None, "yesterday", "--until"),
        ("2026-11-1x", None, "--since"),
        ("2026-W46-1", None, "--since"),
        ("２０２６-11-01", None, "--since"),
    ],
)
def test_window_errors(since, until, text):
    with pytest.raises(llmcost.CostError) as error:
        llmcost.window(since, until, date(2026, 11, 13))
    assert error.value.code == "COST_WINDOW"
    assert text in str(error.value)


# --- the summary ------------------------------------------------------------------------------


def records():
    return [
        {
            "id": DEPLOYED,
            "name": "hello",
            "state": "ready",
            "environment": "dev",
            "target": "local",
        },
        {"id": REMOVED, "name": "old", "state": "removed", "environment": "dev", "target": None},
    ]


def test_summarize_groups_by_key_and_model():
    prefix = gateway_keys.PREFIX
    rows = [
        row(prefix + DEPLOYED, 0.002, request_id="r1"),
        row(prefix + DEPLOYED, 0.001, request_id="r2", model="anthropic/claude-opus-5-5",
            group="judge"),
        row(prefix + DEPLOYED, 0.0, request_id="r3", status="failure", completion_tokens=0),
        row(prefix + DEPLOYED, 0.002, request_id="r1"),  # the same call on two pages
        row(prefix + REMOVED, 0.004),
        row(prefix + "eval-20261113-101010-abcdef", 0.0005, group="judge",
            model="anthropic/claude-opus-5-5"),
        row(prefix + "c" * 32, 0.0001),
        row("someone-else", 9.0),
        {"metadata": None, "spend": 1},
        {"spend": 1},
        "not a row",
    ]  # fmt: skip
    result = llmcost.summarize(rows, records())
    keys = {k["key_alias"]: k for k in result["keys"]}
    assert set(keys) == {
        prefix + DEPLOYED,
        prefix + REMOVED,
        prefix + "eval-20261113-101010-abcdef",
        prefix + "c" * 32,
    }
    deployed = keys[prefix + DEPLOYED]
    assert deployed["kind"] == "deployment"
    assert (deployed["name"], deployed["state"], deployed["target"]) == ("hello", "ready", "local")
    assert (deployed["calls"], deployed["failed"]) == (3, 1)
    assert deployed["cost_usd"] == pytest.approx(0.003)
    assert (deployed["prompt_tokens"], deployed["completion_tokens"]) == (30, 40)
    assert [m["model_group"] for m in deployed["models"]] == ["chat", "judge"]
    assert deployed["models"][0]["calls"] == 2
    removed = keys[prefix + REMOVED]
    assert (removed["state"], removed["target"]) == ("removed", "local")
    assert keys[prefix + "eval-20261113-101010-abcdef"]["kind"] == "eval"
    assert keys[prefix + "eval-20261113-101010-abcdef"]["run"] == "20261113-101010-abcdef"
    assert keys[prefix + "c" * 32] == {
        **keys[prefix + "c" * 32],
        "kind": "unknown",
        "id": "c" * 32,
    }
    assert [k["key_alias"] for k in result["keys"]][0] == prefix + REMOVED  # most expensive first
    assert result["total"]["calls"] == 6
    assert result["total"]["cost_usd"] == pytest.approx(0.0076)
    models = {(m["model_group"], m["model"]): m for m in result["models"]}
    assert models[("chat", "openai/gpt-5.6")]["calls"] == 4
    assert models[("judge", "anthropic/claude-opus-5-5")]["calls"] == 2


@pytest.mark.parametrize(
    "bad", [None, -1, float("nan"), float("inf"), True, "0.1", [1], 10**400, 2e12]
)
def test_a_call_without_a_usable_price_is_counted_unpriced(bad):
    rows = [row(gateway_keys.PREFIX + DEPLOYED, bad), row(gateway_keys.PREFIX + DEPLOYED, 0.5)]
    result = llmcost.summarize(rows, [])
    assert result["total"]["calls"] == 2
    assert result["total"]["unpriced"] == 1
    assert result["total"]["cost_usd"] == 0.5
    assert result["keys"][0]["kind"] == "unknown"


def test_bad_fields_are_dropped_not_trusted():
    rows = [
        row(gateway_keys.PREFIX + DEPLOYED, 0.1, prompt_tokens=-5, completion_tokens=True,
            model=7, group="", status=None),
        row(gateway_keys.PREFIX + DEPLOYED, 0.1, status="failure"),
    ]  # fmt: skip
    result = llmcost.summarize(rows, [])
    total = result["total"]
    # Only LiteLLM's "failure" counts as failed: a row without a status (older LiteLLM) is not.
    assert (total["prompt_tokens"], total["completion_tokens"], total["failed"]) == (10, 20, 1)
    unnamed = [m for m in result["models"] if m["model"] is None]
    assert unnamed and unnamed[0]["model_group"] is None
    long_alias = gateway_keys.PREFIX + "x" * 500
    result = llmcost.summarize([row(long_alias)], [])
    assert len(result["keys"][0]["key_alias"]) == len(gateway_keys.PREFIX) + llmcost.TEXT_LIMIT


def test_empty_summary():
    result = llmcost.summarize([], records())
    assert result == {"keys": [], "models": [], "total": result["total"]}
    assert result["total"]["calls"] == 0


# --- reading the gateway ----------------------------------------------------------------------


def test_spend_logs_reads_every_page(spend):
    spend.rows = [row(gateway_keys.PREFIX + DEPLOYED, request_id=str(i)) for i in range(2500)]
    rows, truncated = LiteLLM(spend.url, MASTER).spend_logs("2026-11-01 00:00:00", "x")
    assert len(rows) == 2500 and truncated is False
    assert [q["page"] for q in spend.queries] == ["1", "2", "3"]
    assert spend.queries[0]["path"] == "/spend/logs/v2"
    assert spend.queries[0]["start_date"] == "2026-11-01 00:00:00"
    assert spend.queries[0]["page_size"] == "1000"


def test_spend_logs_stops_at_the_page_limit_and_passes_on_the_cap(spend):
    spend.rows = [row("x") for _ in range(2500)]
    rows, truncated = LiteLLM(spend.url, MASTER).spend_logs("s", "e", pages=2)
    assert (len(rows), truncated) == (2000, True)
    spend.rows, spend.capped = [row("x")], True
    assert LiteLLM(spend.url, MASTER).spend_logs("s", "e")[1] is True


@pytest.mark.parametrize(
    "body",
    [{"data": None}, ["not", "a", "dict"], {"total_pages": 2}],
)
def test_spend_logs_refuses_a_malformed_answer(spend, body):
    spend.body = body
    with pytest.raises(GatewayError) as error:
        LiteLLM(spend.url, MASTER).spend_logs("s", "e")
    assert error.value.code == "GATEWAY_ERROR"


def test_spend_logs_tolerates_a_bad_page_count(spend):
    spend.body = {"data": [row("x"), "junk"], "total_pages": "many"}
    assert LiteLLM(spend.url, MASTER).spend_logs("s", "e") == ([row("x")], False)


def test_spend_logs_keep_only_what_cost_reads(spend):
    big = row("x", request_id="r", proxy_server_request={"messages": ["secret prompt" * 100]})
    big["metadata"]["user_api_key"] = "hashed"
    spend.rows = [big]
    (kept,), _ = LiteLLM(spend.url, MASTER).spend_logs("s", "e")
    assert kept == {
        "request_id": "r",
        "spend": 0.001,
        "model": "openai/gpt-5.6",
        "model_group": "chat",
        "status": "success",
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "metadata": {"user_api_key_alias": "x"},
    }


def test_spend_logs_cut_long_text_as_they_read(spend):
    spend.rows = [row("a" * 5000, model="m" * 5000, request_id=["not", "text"], status={"x": 1})]
    (kept,), _ = LiteLLM(spend.url, MASTER).spend_logs("s", "e")
    assert len(kept["model"]) == 512 and len(kept["metadata"]["user_api_key_alias"]) == 512
    assert kept["request_id"] is None and kept["status"] is None


def test_spend_logs_overflowing_page_count(spend):
    spend.raw = b'{"data": [], "total_pages": Infinity}'
    rows, truncated = LiteLLM(spend.url, MASTER).spend_logs("s", "e")
    assert (rows, truncated) == ([], False)


def test_spend_logs_page_size_is_capped(spend, monkeypatch):
    monkeypatch.setattr(gateway_keys, "SPEND_PAGE_LIMIT", 100)
    spend.rows = [row("x") for _ in range(5)]
    with pytest.raises(GatewayError) as error:
        LiteLLM(spend.url, MASTER).spend_logs("s", "e")
    assert error.value.code == "GATEWAY_ERROR"
    assert "more than 100 bytes" in str(error.value)


@pytest.mark.parametrize("raw", [b"<html>proxy</html>", b"[" * 100000])
def test_a_non_json_answer_is_a_gateway_error(spend, raw):
    spend.raw = raw
    with pytest.raises(GatewayError) as error:
        LiteLLM(spend.url, MASTER).spend_logs("s", "e")
    assert error.value.code == "GATEWAY_ERROR"
    assert "did not return JSON" in str(error.value)


def test_a_redirect_is_not_followed_with_the_master_key(spend):
    spend.redirect = spend.url + "/elsewhere"
    with pytest.raises(GatewayError) as error:
        LiteLLM(spend.url, MASTER).spend_logs("s", "e")
    assert error.value.code == "GATEWAY_ERROR"
    assert [q["path"] for q in spend.queries] == ["/spend/logs/v2"]


def test_spend_logs_404_means_no_spend_logs(spend):
    spend.status = 404
    with pytest.raises(GatewayError) as error:
        LiteLLM(spend.url, MASTER).spend_logs("s", "e")
    assert error.value.code == "GATEWAY_ERROR"


# --- the command ------------------------------------------------------------------------------


def seed(spend):
    prefix = gateway_keys.PREFIX
    spend.rows = [
        row(prefix + DEPLOYED, 0.002, request_id="r1"),
        row(prefix + DEPLOYED, 0.001, request_id="r2", group="judge",
            model="anthropic/claude-opus-5-5"),
        row(prefix + REMOVED, 0.004, request_id="r3"),
        row(prefix + "eval-run1", None, request_id="r4"),
        row("not-ours", 5.0, request_id="r5"),
    ]  # fmt: skip


def test_cost_json(gateway_env, spend, state_dir, capsys):
    seed(spend)
    code, out, err = run_cli(
        capsys, "--state-dir", str(state_dir), "--since", "2026-11-01", "--until", "2026-11-13",
        "--json",
    )  # fmt: skip
    assert code == 0, err
    result = json.loads(out)
    assert result["schema_version"] == 1
    assert (result["since"], result["until"]) == ("2026-11-01", "2026-11-13")
    assert (result["gateway"], result["currency"]) == ("litellm", "USD")
    assert result["source"] == "litellm spend logs"
    assert result["truncated"] is False
    assert result["total"]["calls"] == 4
    assert result["total"]["unpriced"] == 1
    assert result["total"]["cost_usd"] == pytest.approx(0.007)
    assert [k["kind"] for k in result["keys"]] == ["deployment", "deployment", "eval"]
    assert result["keys"][1]["models"][0]["model_group"] == "chat"
    assert spend.queries[0]["start_date"] == "2026-11-01 00:00:00"
    assert spend.queries[0]["end_date"] == "2026-11-13 23:59:59"
    assert MASTER not in out + err


def test_cost_table_by_deployment_and_by_model(gateway_env, spend, state_dir, capsys):
    seed(spend)
    code, out, err = run_cli(capsys, "--state-dir", str(state_dir))
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0].startswith("LLM cost ") and lines[0].endswith("(UTC), gateway litellm")
    assert lines[1].split() == ["DEPLOYMENT", "NAME", "STATE", "CALLS", "FAILED", "TOKENS",
                                "IN/OUT", "USD"]  # fmt: skip
    assert lines[2].split() == [REMOVED, "old", "removed", "1", "0", "10/20", "0.004000"]
    assert lines[3].split() == [DEPLOYED, "hello", "ready", "2", "0", "20/40", "0.003000"]
    assert lines[4].split() == ["eval", "run", "run1", "-", "-", "1", "0", "10/20", "0.000000"]
    assert lines[5].split() == ["Total", "4", "0", "40/80", "0.007000"]
    assert "1 call(s) had no price" in out
    assert "not a bill" in out and "last minute" in out
    code, out, _ = run_cli(capsys, "--state-dir", str(state_dir), "--by", "model")
    assert code == 0
    lines = out.splitlines()
    assert lines[1].split() == ["MODEL", "ALIAS", "MODEL", "CALLS", "FAILED", "TOKENS", "IN/OUT",
                                "USD"]  # fmt: skip
    assert lines[2].split() == ["chat", "openai/gpt-5.6", "3", "0", "30/60", "0.006000"]
    assert lines[3].split() == ["judge", "anthropic/claude-opus-5-5", "1", "0", "10/20",
                                "0.001000"]  # fmt: skip


def test_cost_without_state_reports_unknown_keys(gateway_env, spend, tmp_path, capsys):
    seed(spend)
    code, out, err = run_cli(capsys, "--state-dir", str(tmp_path / "none"), "--json")
    assert code == 0, err
    assert {k["kind"] for k in json.loads(out)["keys"]} == {"unknown", "eval"}
    assert not (tmp_path / "none").exists()  # read-only: nothing created


def test_cost_with_no_calls(gateway_env, spend, state_dir, capsys):
    code, out, _ = run_cli(capsys, "--state-dir", str(state_dir))
    assert code == 0
    assert "No model calls by AgentBreeder keys in this window." in out
    assert "had no price" not in out


def test_cost_warns_when_truncated(gateway_env, spend, state_dir, capsys):
    seed(spend)
    spend.capped = True
    code, out, err = run_cli(capsys, "--state-dir", str(state_dir), "--json")
    assert code == 0
    assert json.loads(out)["truncated"] is True
    assert "partial" in err


def test_cost_control_characters_are_not_printed(gateway_env, spend, state_dir, capsys):
    spend.rows = [row(gateway_keys.PREFIX + "evil\x1b[2J", 0.1, model="m\x1b]0;x\x07",
                      group="g\nfake")]  # fmt: skip
    code, out, _ = run_cli(capsys, "--state-dir", str(state_dir))
    assert code == 0
    assert "\x1b" not in out and "\x07" not in out
    code, out, _ = run_cli(capsys, "--state-dir", str(state_dir), "--by", "model")
    assert "\x1b" not in out and "g fake" in out


def error_of(out):
    return json.loads(out.strip().splitlines()[-1])["error"]


def test_cost_gateway_down(gateway_env, spend, state_dir, capsys):
    spend.server.shutdown()
    spend.server.server_close()
    code, out, err = run_cli(capsys, "--state-dir", str(state_dir), "--json")
    assert code == 2
    assert err.startswith("[GATEWAY_UNREACHABLE] ")
    assert "services status" in err
    assert error_of(out)["code"] == "GATEWAY_UNREACHABLE"


def test_cost_gateway_error(gateway_env, spend, state_dir, capsys):
    spend.status = 500
    code, out, err = run_cli(capsys, "--state-dir", str(state_dir), "--json")
    assert code == 2
    assert error_of(out) == {"code": "GATEWAY_ERROR", "message": err.strip().split("] ", 1)[1]}


def test_cost_bad_window(gateway_env, state_dir, capsys):
    code, out, err = run_cli(capsys, "--state-dir", str(state_dir), "--since", "x", "--json")
    assert code == 2
    assert err.startswith("[COST_WINDOW] --since")
    assert error_of(out)["code"] == "COST_WINDOW"


def test_cost_needs_an_environment_with_a_gateway(identity_env, state_dir, capsys):
    code, _, err = run_cli(capsys, "--state-dir", str(state_dir))
    assert code == 2 and "[COST_NO_GATEWAY] environment dev has no model gateway" in err
    identity_env.config.pop("default_environment")
    identity_env.write()
    code, _, err = run_cli(capsys, "--state-dir", str(state_dir))
    assert code == 2 and "[COST_NO_GATEWAY] no environment selected" in err


def test_cost_needs_a_master_key(identity_env, state_dir, capsys):
    identity_env.config["gateways"] = {
        "g": {"type": "litellm", "url": "http://127.0.0.1:9", "agent_url": "http://x:1"}
    }
    identity_env.config["environments"]["dev"].update(
        {"gateway": "g", "models": ["chat"], "ceilings": {"budget_usd": 5, "rpm": 60}}
    )
    identity_env.write()
    code, out, err = run_cli(capsys, "--state-dir", str(state_dir), "--json")
    assert code == 2
    assert "[COST_NO_GATEWAY] gateway g has no master_key" in err
    assert error_of(out)["code"] == "COST_NO_GATEWAY"


def test_cost_bad_operator_config(identity_env, state_dir, capsys):
    identity_env.path.chmod(0o644)
    code, out, err = run_cli(capsys, "--state-dir", str(state_dir), "--json")
    assert code == 2
    assert err.startswith("[COST_ERROR] Operator config must be owned")
    assert error_of(out)["code"] == "COST_ERROR"


def test_cost_unreadable_state(gateway_env, state_dir, capsys):
    (state_dir / "deployments.sqlite3").chmod(0o644)
    state_dir.chmod(0o755)
    code, out, err = run_cli(capsys, "--state-dir", str(state_dir), "--json")
    assert code == 2
    assert err.startswith("[DEPLOYMENT_ERROR] ")
    assert error_of(out)["code"] == "DEPLOYMENT_ERROR"


def test_cost_state_needing_migration_keeps_its_code(gateway_env, state_dir, capsys, monkeypatch):
    from agentbreeder.store import StateNeedsMigration, Store

    def old(directory):
        raise StateNeedsMigration("run agentbreeder migrate")

    monkeypatch.setattr(Store, "open_readonly", old)
    code, out, err = run_cli(capsys, "--state-dir", str(state_dir), "--json")
    assert code == 2
    assert err.startswith("[STATE_NEEDS_MIGRATION] ")
    assert error_of(out)["code"] == "STATE_NEEDS_MIGRATION"


def test_cost_unreadable_master_key_is_coded(gateway_env, state_dir, tmp_path, capsys):
    (tmp_path / "master").write_bytes(b"\xff\xfe")
    code, out, err = run_cli(capsys, "--state-dir", str(state_dir), "--json")
    assert code == 2
    assert err.startswith("[COST_ERROR] ")
    assert "Traceback" not in err and MASTER not in out + err


def test_cost_environment_needs_stack(gateway_env, state_dir):
    with pytest.raises(SystemExit) as error:
        cli.main(["cost", "--state-dir", str(state_dir), "--environment", "dev"])
    assert error.value.code == 2


def test_cost_with_stack_uses_what_services_up_wrote(
    gateway_env, spend, state_dir, capsys, monkeypatch
):
    from agentbreeder.services import engine

    seen = {}

    def context(project, environment):
        seen["args"] = (project, environment)
        return engine.StackContext(
            stack_id="demo-0123abcd",
            environment="dev",
            operator_config=gateway_env.path,
            state_dir=state_dir,
        )

    monkeypatch.setattr(engine, "context", context)
    monkeypatch.delenv("AGENTBREEDER_OPERATOR_CONFIG")
    seed(spend)
    code, out, err = run_cli(capsys, "--stack", "proj", "--environment", "dev", "--json")
    assert code == 0, err
    assert seen["args"][1] == "dev"
    assert "Using stack demo-0123abcd (dev)" in err
    assert [k.get("name") for k in json.loads(out)["keys"]][:2] == ["old", "hello"]


def test_cost_state_os_error(gateway_env, state_dir, capsys, monkeypatch):
    from agentbreeder.store import Store

    def broken(directory):
        raise OSError("disk")

    monkeypatch.setattr(Store, "open_readonly", broken)
    code, out, err = run_cli(capsys, "--state-dir", str(state_dir), "--json")
    assert code == 2
    assert err.strip() == "[STATE_UNREADABLE] Cannot read local state; check permissions"
    assert error_of(out)["code"] == "STATE_UNREADABLE"
