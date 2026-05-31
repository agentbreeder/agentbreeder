import logging

import engine.resolver as resolver
from engine.config_parser import AgentConfig, DeployConfig, ModelConfig, ToolRef


def _cfg(refs: list[str]):
    return AgentConfig(
        name="x", version="1.0.0", team="t", owner="o@e.com", framework="langgraph",
        model=ModelConfig(primary="claude-sonnet-4"), deploy=DeployConfig(cloud="aws"),
        tools=[ToolRef(ref=r) for r in refs],
    )


def test_resolvable_tool_ref_logs_no_warning(tmp_path, caplog, monkeypatch):
    import engine.tool_resolver as tr
    caplog.set_level(logging.WARNING, logger="engine.resolver")
    monkeypatch.setattr(tr, "resolve_tool", lambda ref, project_root=None: (lambda: None))
    resolver.resolve_dependencies(_cfg(["tools/web-search"]), project_root=tmp_path)
    assert "did not resolve" not in caplog.text


def test_unresolvable_tool_ref_warns_not_raises(tmp_path, caplog, monkeypatch):
    import engine.tool_resolver as tr
    caplog.set_level(logging.WARNING, logger="engine.resolver")

    def _boom(ref, project_root=None):
        raise tr.ToolNotFoundError(ref)

    monkeypatch.setattr(tr, "resolve_tool", _boom)
    cfg = resolver.resolve_dependencies(_cfg(["tools/nope"]), project_root=tmp_path)
    assert cfg is not None  # did not raise
    assert "did not resolve" in caplog.text.lower()


def test_unexpected_tool_error_does_not_crash_deploy(tmp_path, caplog, monkeypatch):
    import engine.tool_resolver as tr
    caplog.set_level(logging.WARNING, logger="engine.resolver")

    def _explode(ref, project_root=None):
        raise ImportError("broken tools/x.py")

    monkeypatch.setattr(tr, "resolve_tool", _explode)
    cfg = resolver.resolve_dependencies(_cfg(["tools/broken"]), project_root=tmp_path)
    assert cfg is not None  # deploy did not crash
    assert "unexpected error" in caplog.text.lower()
