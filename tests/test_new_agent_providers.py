"""`new agent --model PROVIDER [--model-id ID]` (#905): providers through LiteLLM."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest
import yaml

from agentbreeder import cli, gateway_keys, guide_checks, newagent, registry

TODAY = date(2026, 11, 13)
DEFAULTS = {
    "anthropic": "anthropic/claude-opus-5-5",
    "gemini": "gemini/gemini-3.8-flash",
    "openai": "openai/gpt-5.6",
}
ROUTER = "openrouter/meta-llama/llama-4-maverick:free"


@pytest.fixture(autouse=True)
def clean_registry_path(monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)


def config(plan: newagent.Plan) -> dict:
    return yaml.safe_load(plan.files["stack/litellm/config.yaml"])


def _probe(tmp_path, environ=None):
    def doctor():
        return [SimpleNamespace(name="Docker", passed=True, message="ok", required=True)]

    return guide_checks.Probe(
        doctor=doctor,
        containers=lambda state: {},
        clusters=list,
        environ=environ or {},
        stacks_root=tmp_path / "stacks",
    )


# -- the table -----------------------------------------------------------------------------


def test_each_provider_uses_the_key_the_gateway_allows_and_only_gemini_is_verified():
    assert set(newagent.PROVIDERS) == set(gateway_keys.PROVIDER_KEYS)
    for name, provider in newagent.PROVIDERS.items():
        assert provider.key == gateway_keys.PROVIDER_KEYS[name]
        assert provider.model is None or provider.model.startswith(f"{name}/")
    assert [n for n, p in newagent.PROVIDERS.items() if p.verified] == ["gemini"]
    assert set(guide_checks.KEY_URLS) == set(gateway_keys.PROVIDER_KEYS.values())
    assert all(url.startswith("https://") for url in guide_checks.KEY_URLS.values())


# -- plan ----------------------------------------------------------------------------------


@pytest.mark.parametrize("model", sorted(DEFAULTS))
def test_default_model_and_key_fill_both_aliases(model, tmp_path):
    plan = newagent.plan("hello", model, tmp_path / "x", today=TODAY)
    assert plan.model_id == DEFAULTS[model]
    key = newagent.PROVIDERS[model].key
    for entry in config(plan)["model_list"]:
        assert entry["litellm_params"] == {
            "model": DEFAULTS[model],
            "api_key": f"os.environ/{key}",
        }
    assert f"{key}=..." in plan.files["stack.yaml"]  # the comment names the key file entry
    summary = plan.summary(dry_run=True)
    assert (summary["model"], summary["model_id"], summary["key"]) == (model, DEFAULTS[model], key)
    assert summary["verified"] is (model == "gemini")
    path = tmp_path / "config.yaml"
    path.write_text(plan.files["stack/litellm/config.yaml"])
    review = gateway_keys.config_review(path)
    assert review.aliases == {"chat", "judge"}
    assert set(review.providers.values()) == {f"{model} ({key})"}


def test_gemini_without_model_id_is_unchanged_by_905():
    plan = newagent.plan("hello", "gemini", today=TODAY)
    assert plan.files["stack/litellm/config.yaml"] == (
        "model_list:\n"
        "  - model_name: chat\n"
        "    litellm_params: {model: gemini/gemini-3.8-flash, "
        "api_key: os.environ/GEMINI_API_KEY}\n"
        "  - model_name: judge\n"
        "    litellm_params: {model: gemini/gemini-3.8-flash, "
        "api_key: os.environ/GEMINI_API_KEY}\n"
        "general_settings:\n"
        "  master_key: os.environ/LITELLM_MASTER_KEY\n"
        "  database_url: os.environ/DATABASE_URL\n"
    )
    explicit = newagent.plan("hello", "gemini", today=TODAY, model_id="gemini/gemini-3.8-flash")
    assert explicit.files == plan.files


def test_openrouter_needs_a_model_id():
    with pytest.raises(newagent.NewAgentError) as error:
        newagent.plan("hello", "openrouter")
    assert error.value.code == "MODEL_ID_REQUIRED"
    assert "openrouter/<vendor>/<model>" in str(error.value)


def test_a_model_id_with_a_colon_is_quoted_and_reads_back_exactly(tmp_path):
    plan = newagent.plan("hello", "openrouter", tmp_path / "x", today=TODAY, model_id=ROUTER)
    assert f'model: "{ROUTER}"' in plan.files["stack/litellm/config.yaml"]
    assert {e["litellm_params"]["model"] for e in config(plan)["model_list"]} == {ROUTER}
    path = tmp_path / "config.yaml"
    path.write_text(plan.files["stack/litellm/config.yaml"])
    review = gateway_keys.config_review(path)
    assert set(review.providers.values()) == {"openrouter (OPENROUTER_API_KEY)"}


def test_a_model_id_replaces_the_default():
    plan = newagent.plan("hello", "openai", today=TODAY, model_id="openai/gpt-5.4-mini")
    assert {e["litellm_params"]["model"] for e in config(plan)["model_list"]} == {
        "openai/gpt-5.4-mini"
    }


@pytest.mark.parametrize(
    ("model", "model_id"),
    [
        ("anthropic", "openai/gpt-5.6"),  # another provider's prefix
        ("anthropic", "claude-opus-5-5"),  # no prefix
        ("anthropic", "anthropic/"),
        ("anthropic", "anthropic//x"),
        ("anthropic", "anthropic/../x"),
        ("anthropic", "anthropic/./x"),
        ("anthropic", "anthropic/x y"),
        ("anthropic", "anthropic/x\nmodel: evil"),
        ("anthropic", "anthropic/x\x1b[2J"),
        ("anthropic", "anthropic/x#y"),
        ("anthropic", "anthropic/x}"),
        ("anthropic", "anthropic/é"),
        ("anthropic", "anthropic/" + "x" * 119),  # 129 characters
        ("openrouter", "openrouter/acme"),  # needs vendor and model
        ("openrouter", "openrouter/acme/"),
        ("gemini", ""),
    ],
)
def test_bad_model_ids_are_refused_without_echoing_them(model, model_id):
    with pytest.raises(newagent.NewAgentError) as error:
        newagent.plan("hello", model, today=TODAY, model_id=model_id)
    assert error.value.code == "MODEL_ID_INVALID"
    if model_id.split("/", 1)[-1]:  # a bare "PROVIDER/" is the message's own shape
        assert model_id not in str(error.value)
        tail = model_id.split("/", 1)[-1]
        if len(tail) > 3:
            assert tail not in str(error.value)


def test_a_gemini_model_id_overrides_the_default_and_an_equal_one_writes_the_same(tmp_path):
    other = newagent.plan("hello", "gemini", today=TODAY, model_id="gemini/gemini-3.7-flash")
    assert {e["litellm_params"]["model"] for e in config(other)["model_list"]} == {
        "gemini/gemini-3.7-flash"
    }
    default = newagent.create(newagent.plan("hello", "gemini", tmp_path / "a", today=TODAY))
    same = newagent.create(
        newagent.plan(
            "hello", "gemini", tmp_path / "b", today=TODAY, model_id="gemini/gemini-3.8-flash"
        )
    )
    for relative in default.files:
        assert (tmp_path / "a" / relative).read_bytes() == (tmp_path / "b" / relative).read_bytes()
    assert same.published == default.published


def test_dry_run_of_openrouter_without_a_model_id_is_refused(tmp_path, capsys):
    code = cli.main(
        ["new", "agent", "hello", "--model", "openrouter", "--dir", str(tmp_path / "h")]
        + ["--dry-run"]
    )
    assert code == 2
    assert capsys.readouterr().err.startswith("[MODEL_ID_REQUIRED] ")


def test_a_128_character_model_id_is_accepted():
    model_id = "anthropic/" + "x" * 118
    assert len(model_id) == 128
    assert newagent.plan("hello", "anthropic", today=TODAY, model_id=model_id).model_id == model_id


# -- create --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "model_id"),
    [("anthropic", None), ("openai", None), ("openrouter", ROUTER), ("gemini", None)],
)
def test_create_writes_a_project_for_each_provider(tmp_path, model, model_id):
    plan = newagent.create(
        newagent.plan("hello", model, tmp_path / "hello", today=TODAY, model_id=model_id)
    )
    project = tmp_path / "hello"
    assert (project / "agent.lock").is_file()
    assert set(plan.published) == {"prompt/hello-prompt@0.1.0", "eval/hello-evals@0.1.0"}
    review = gateway_keys.config_review(project / "stack/litellm/config.yaml")
    key = newagent.PROVIDERS[model].key
    assert set(review.providers.values()) == {f"{model} ({key})"}
    assert plan.summary(dry_run=False)["model_id"] == plan.model_id


def test_a_config_serving_another_model_is_a_template_bug(tmp_path, monkeypatch):
    monkeypatch.setattr(
        newagent,
        "LITELLM_CONFIG",
        newagent.LITELLM_CONFIG.replace("{model_yaml}, api_key", "openai/other, api_key", 1),
    )
    with pytest.raises(newagent.NewAgentError) as error:
        newagent.create(newagent.plan("hello", "openai", tmp_path / "hello", today=TODAY))
    assert error.value.code == "TEMPLATE_INVALID"
    assert not (tmp_path / "hello").exists()


def test_a_config_the_gateway_refuses_is_a_template_bug(tmp_path, monkeypatch):
    monkeypatch.setattr(
        newagent, "LITELLM_CONFIG", newagent.LITELLM_CONFIG + "callbacks: [custom]\n"
    )
    with pytest.raises(newagent.NewAgentError) as error:
        newagent.create(newagent.plan("hello", "gemini", tmp_path / "hello", today=TODAY))
    assert error.value.code == "TEMPLATE_INVALID"


# -- cli -----------------------------------------------------------------------------------


def test_cli_json_carries_the_model_id_and_verified(tmp_path, capsys):
    code = cli.main(
        ["new", "agent", "hello", "--model", "openrouter", "--model-id", ROUTER]
        + ["--dir", str(tmp_path / "hello"), "--dry-run", "--json"]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["model"] == "openrouter" and summary["model_id"] == ROUTER
    assert summary["key"] == "OPENROUTER_API_KEY" and summary["verified"] is False


@pytest.mark.parametrize(("model", "config_only"), [("openai", True), ("gemini", False)])
def test_cli_text_says_when_a_provider_is_config_only(tmp_path, capsys, model, config_only):
    assert cli.main(["new", "agent", "hello", "--model", model, "--dir", str(tmp_path / "h")]) == 0
    out = capsys.readouterr().out
    assert ("is config-only" in out) is config_only
    assert f"Put {newagent.PROVIDERS[model].key}=<your key>" in out


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["--model", "openrouter"], "MODEL_ID_REQUIRED"),
        (["--model", "anthropic", "--model-id", "openai/gpt-5.6"], "MODEL_ID_INVALID"),
    ],
)
def test_cli_refuses_model_ids_with_a_coded_json_error(tmp_path, capsys, argv, code):
    exit_code = cli.main(
        ["new", "agent", "hello", *argv, "--dir", str(tmp_path / "hello"), "--json"]
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == code
    assert captured.err.startswith(f"[{code}] ")
    assert not (tmp_path / "hello").exists()


# -- next ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "model_id"),
    [("anthropic", None), ("openai", None), ("openrouter", ROUTER), ("gemini", None)],
)
def test_next_reports_the_providers_missing_key_then_done(tmp_path, model, model_id):
    newagent.create(
        newagent.plan("hello", model, tmp_path / "hello", today=TODAY, model_id=model_id)
    )
    key = newagent.PROVIDERS[model].key
    project = tmp_path / "hello"
    keys = next(
        s for s in guide_checks.compute(project, probe=_probe(tmp_path)).steps if s.id == "keys"
    )
    assert keys.state == "next" and keys.reason.code == "KEYS_MISSING"
    assert keys.data.required == [key] and keys.data.missing == [key]
    assert keys.fix.url == guide_checks.KEY_URLS[key]
    assert f"export {key}=<your key>" in keys.fix.note
    marker = "seeded-marker-" + "q" * 20
    done = next(
        s
        for s in guide_checks.compute(project, probe=_probe(tmp_path, {key: marker})).steps
        if s.id == "keys"
    )
    assert done.state == "done" and done.data.present == [key]


# -- --stack-for ---------------------------------------------------------------------------


def _bare_agent(tmp_path, model="gemini", model_id=None):
    """A starter project (any provider) with its stack removed."""
    newagent.create(
        newagent.plan("hello", model, tmp_path / "hello", today=TODAY, model_id=model_id)
    )
    project = tmp_path / "hello"
    original = (
        (project / "stack.yaml").read_text(),
        (project / "stack/litellm/config.yaml").read_text(),
    )
    for relative in ("stack.yaml", "stack/litellm/config.yaml"):
        (project / relative).unlink()
    (project / "stack/litellm").rmdir()
    (project / "stack").rmdir()
    return project, original


@pytest.mark.parametrize(
    ("model", "model_id"),
    [("anthropic", None), ("openai", None), ("openrouter", ROUTER)],
)
def test_stack_for_writes_the_starters_gateway_for_each_provider(tmp_path, model, model_id):
    from agentbreeder import stackfor

    project, (stack_before, config_before) = _bare_agent(tmp_path, model, model_id)
    result = stackfor.create(
        stackfor.plan("hello", model, project / "agent.yaml", today=TODAY, model_id=model_id)
    )
    resolved = model_id or DEFAULTS[model]
    assert result.model_id == resolved
    config = yaml.safe_load((project / "stack/litellm/config.yaml").read_text())
    assert config == yaml.safe_load(config_before)  # the starter's own gateway, this provider
    assert (
        yaml.safe_load((project / "stack.yaml").read_text())["environment"]
        == yaml.safe_load(stack_before)["environment"]
    )
    summary = result.summary(dry_run=False)
    assert (summary["model_id"], summary["verified"]) == (resolved, False)
    assert summary["key"] == newagent.PROVIDERS[model].key


def test_stack_for_refuses_openrouter_without_a_model_id_and_a_foreign_id(tmp_path):
    from agentbreeder import stackfor

    project, _ = _bare_agent(tmp_path)
    with pytest.raises(newagent.NewAgentError) as error:
        stackfor.plan("hello", "openrouter", project / "agent.yaml", today=TODAY)
    assert error.value.code == "MODEL_ID_REQUIRED"
    with pytest.raises(newagent.NewAgentError) as error:
        stackfor.plan("hello", "openai", project / "agent.yaml", model_id="anthropic/x")
    assert error.value.code == "MODEL_ID_INVALID"
    assert not (project / "stack.yaml").exists()


def test_stack_for_cli_json_and_config_only_text(tmp_path, capsys):
    project, _ = _bare_agent(tmp_path)
    argv = ["new", "agent", "hello", "--model", "openrouter", "--model-id", ROUTER]
    argv += ["--stack-for", str(project / "agent.yaml"), "--dry-run"]
    assert cli.main([*argv, "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["model_id"] == ROUTER and summary["verified"] is False
    assert cli.main(argv) == 0
    assert "openrouter is config-only" in capsys.readouterr().out
    assert cli.main([*argv[:4], "gemini", *argv[7:]]) == 0
    assert "config-only" not in capsys.readouterr().out


def test_stack_for_a_gateway_serving_another_model_is_a_template_bug(tmp_path, monkeypatch):
    from agentbreeder import stackfor

    project, _ = _bare_agent(tmp_path)
    monkeypatch.setattr(
        stackfor, "LITELLM_MODEL", stackfor.LITELLM_MODEL.replace("{model},", "gemini/other,")
    )
    with pytest.raises(newagent.NewAgentError) as error:
        stackfor.create(stackfor.plan("hello", "gemini", project / "agent.yaml", today=TODAY))
    assert error.value.code == "TEMPLATE_INVALID"
    assert not (project / "stack.yaml").exists()
