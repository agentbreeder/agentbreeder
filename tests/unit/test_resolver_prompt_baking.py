from engine.config_parser import AgentConfig, DeployConfig, ModelConfig, PromptsConfig
from engine.resolver import resolve_dependencies


def _cfg(system: str | None):
    return AgentConfig(
        name="x",
        version="1.0.0",
        team="t",
        owner="o@e.com",
        framework="langgraph",
        model=ModelConfig(primary="claude-sonnet-4"),
        deploy=DeployConfig(cloud="aws"),
        prompts=PromptsConfig(system=system),
    )


def test_prompt_ref_is_baked_from_local_file(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "support.md").write_text("You are a support agent.")
    cfg = resolve_dependencies(_cfg("prompts/support"), project_root=tmp_path)
    assert cfg.prompts.system == "You are a support agent."


def test_inline_prompt_is_left_untouched(tmp_path):
    cfg = resolve_dependencies(_cfg("You are literally inline."), project_root=tmp_path)
    assert cfg.prompts.system == "You are literally inline."


def test_unresolvable_ref_is_left_for_runtime(tmp_path):
    cfg = resolve_dependencies(_cfg("prompts/missing"), project_root=tmp_path)
    assert cfg.prompts.system == "prompts/missing"  # warned, not raised


def test_prompt_ref_baked_from_registry_when_no_local_file(tmp_path, monkeypatch):
    """Verify _bake_prompt_ref falls through to registry when no local file exists."""
    import engine.prompt_resolver as pr

    pr.clear_registry_cache()
    monkeypatch.setattr(pr, "_resolve_from_registry", lambda name, version: "REGISTRY PROMPT")
    cfg = resolve_dependencies(_cfg("prompts/remote"), project_root=tmp_path)
    assert cfg.prompts.system == "REGISTRY PROMPT"
