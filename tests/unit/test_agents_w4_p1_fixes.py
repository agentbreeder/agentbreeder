"""Tests for Wave 4 Agents P1 fixes (W4-01 .. W4-06).

Covers:
* W4-01 — structured ``RuntimeValidationResult`` with path hints
  (``RuntimeValidationError``, ``add_error``, ``from_items``).
* W4-02 — tempfile cleanup on build() failure across all Python + Node + Go
  runtimes (no leaked ``/tmp/agentbreeder-*`` directories on exception).
* W4-03 — ``Idempotency-Key`` header is set on every invoke proxy POST.
* W4-04 — transient ``httpx.TimeoutException`` triggers retry via
  ``async_retry`` in the invoke proxy.
* W4-05 — sibling runtime test files already exist; smoke-check that they
  cover ``get_runtime`` for openai_agents / node / go.
* W4-06 — ``ClaudeSDKRuntime._build_env_block`` now delegates to the shared
  ``build_env_block`` and still emits every previously-emitted env var.
* W4-08 — ``validate_config_yaml`` surfaces framework-specific config
  errors (claude_sdk + non-Claude model) at parse time.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.agents import router
from engine.config_parser import AgentConfig, FrameworkType
from engine.runtimes.base import (
    RuntimeBuilder,
    RuntimeValidationError,
    RuntimeValidationResult,
    build_env_block,
)
from engine.runtimes.claude_sdk import ClaudeSDKRuntime
from engine.runtimes.crewai import CrewAIRuntime
from engine.runtimes.custom import CustomRuntime
from engine.runtimes.go.builder import GoRuntimeFamily
from engine.runtimes.google_adk import GoogleADKRuntime
from engine.runtimes.langgraph import LangGraphRuntime
from engine.runtimes.node import NodeRuntimeFamily
from engine.runtimes.openai_agents import OpenAIAgentsRuntime
from registry.agents import validate_config_yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(framework: FrameworkType = FrameworkType.langgraph, **overrides) -> AgentConfig:
    base = {
        "name": "test-agent",
        "version": "1.0.0",
        "team": "test",
        "owner": "test@example.com",
        "framework": framework,
        "model": {"primary": "gpt-4o"},
        "deploy": {"cloud": "local"},
    }
    base.update(overrides)
    return AgentConfig(**base)


def _make_agent_dir(files: dict[str, str] | None = None) -> Path:
    d = Path(tempfile.mkdtemp(prefix="test-w4-"))
    if files:
        for name, content in files.items():
            (d / name).write_text(content)
    return d


# ---------------------------------------------------------------------------
# W4-01 — RuntimeValidationResult with structured path hints
# ---------------------------------------------------------------------------


class TestRuntimeValidationResultStructured:
    def test_add_error_keeps_plain_list_and_structured_list_in_sync(self) -> None:
        result = RuntimeValidationResult(valid=True)
        result.add_error("Model is required", path="model.primary", suggestion="Add one")
        assert result.valid is False
        assert result.errors == ["Model is required"]
        assert len(result.error_items) == 1
        assert result.error_items[0].path == "model.primary"
        assert result.error_items[0].suggestion == "Add one"
        assert result.error_items[0].message == "Model is required"

    def test_from_items_derives_legacy_errors_list(self) -> None:
        items = [
            RuntimeValidationError(message="bad name", path="name"),
            RuntimeValidationError(message="bad cloud", path="deploy.cloud"),
        ]
        result = RuntimeValidationResult.from_items(items)
        assert result.valid is False
        assert result.errors == ["bad name", "bad cloud"]
        assert result.error_items == items

    def test_from_items_empty_is_valid(self) -> None:
        result = RuntimeValidationResult.from_items([])
        assert result.valid is True
        assert result.errors == []
        assert result.error_items == []

    def test_legacy_constructor_still_works(self) -> None:
        # Existing call sites that pass only `errors=[...]` must keep working.
        result = RuntimeValidationResult(valid=False, errors=["old-style"])
        assert result.errors == ["old-style"]
        assert result.error_items == []

    def test_claude_sdk_validate_emits_structured_items(self) -> None:
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir()  # no agent.py, no requirements
        config = _make_config(framework=FrameworkType.claude_sdk, model={"primary": "claude-3"})
        result = runtime.validate(agent_dir, config)
        assert result.valid is False
        # Both views populated
        assert len(result.errors) >= 2
        assert len(result.error_items) == len(result.errors)
        # Path hints present
        paths = {item.path for item in result.error_items}
        assert "agent.py" in paths
        assert "requirements.txt" in paths


# ---------------------------------------------------------------------------
# W4-02 — Tempfile cleanup on build() exception
# ---------------------------------------------------------------------------


class TestBuildTempfileCleanup:
    """For each runtime, force build() to raise and assert no leaked tmpdir.

    We snapshot ``/tmp`` (the gettempdir() root) for ``agentbreeder-*``
    directories before + after the failing build, and assert the set is
    unchanged.
    """

    @staticmethod
    def _snapshot_tempdirs() -> set[str]:
        root = Path(tempfile.gettempdir())
        return {p.name for p in root.iterdir() if p.name.startswith("agentbreeder-")}

    def _assert_no_leak(self, runtime: RuntimeBuilder, config: AgentConfig) -> None:
        # agent_dir doesn't exist → iterdir() raises → build() must clean up.
        bogus_dir = Path("/nonexistent/agentbreeder-test-dir-does-not-exist")
        before = self._snapshot_tempdirs()
        with pytest.raises(FileNotFoundError):
            runtime.build(bogus_dir, config)
        after = self._snapshot_tempdirs()
        leaked = after - before
        assert not leaked, f"Leaked tempdirs: {leaked}"

    def test_langgraph_build_cleans_up(self) -> None:
        self._assert_no_leak(LangGraphRuntime(), _make_config())

    def test_crewai_build_cleans_up(self) -> None:
        self._assert_no_leak(CrewAIRuntime(), _make_config(framework=FrameworkType.crewai))

    def test_claude_sdk_build_cleans_up(self) -> None:
        self._assert_no_leak(
            ClaudeSDKRuntime(),
            _make_config(framework=FrameworkType.claude_sdk, model={"primary": "claude-sonnet-4"}),
        )

    def test_openai_agents_build_cleans_up(self) -> None:
        self._assert_no_leak(
            OpenAIAgentsRuntime(),
            _make_config(framework=FrameworkType.openai_agents),
        )

    def test_google_adk_build_cleans_up(self) -> None:
        self._assert_no_leak(
            GoogleADKRuntime(),
            _make_config(
                framework=FrameworkType.google_adk, model={"primary": "gemini-2.5-flash"}
            ),
        )

    def test_custom_build_cleans_up(self) -> None:
        self._assert_no_leak(CustomRuntime(), _make_config(framework=FrameworkType.custom))

    def test_node_build_cleans_up(self) -> None:
        # Node uses copytree(str(agent_dir), ...) which raises FileNotFoundError.
        runtime = NodeRuntimeFamily()
        # Build a config that will route into the 'custom' Node template.
        config = AgentConfig(
            name="node-agent",
            version="1.0.0",
            team="t",
            owner="a@b.com",
            framework=None,
            runtime={"language": "node", "framework": "custom"},
            model={"primary": "gpt-4o"},
            deploy={"cloud": "local"},
        )
        before = self._snapshot_tempdirs()
        with pytest.raises(FileNotFoundError):
            runtime.build(Path("/nonexistent/agentbreeder-node-test"), config)
        after = self._snapshot_tempdirs()
        leaked = after - before
        assert not leaked, f"Leaked tempdirs: {leaked}"

    def test_go_build_cleans_up(self) -> None:
        self._assert_no_leak(
            GoRuntimeFamily(),
            AgentConfig(
                name="go-agent",
                version="1.0.0",
                team="t",
                owner="a@b.com",
                framework=None,
                runtime={"language": "go", "framework": "custom"},
                model={"primary": "gpt-4o"},
                deploy={"cloud": "local"},
            ),
        )


# ---------------------------------------------------------------------------
# W4-03 — Idempotency-Key header on invoke
# W4-04 — Retry on transient httpx errors
# ---------------------------------------------------------------------------


def _make_invoke_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    # Bypass auth for these tests
    from api.auth import get_current_user
    from api.database import get_db
    from api.models.database import User

    fake_user = User(id="00000000-0000-0000-0000-000000000000", email="t@t.com")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = lambda: MagicMock()
    return app


def _patch_agent_lookup(agent_name: str = "test-agent") -> AsyncMock:
    agent = MagicMock()
    agent.name = agent_name
    agent.endpoint_url = "http://runtime.local"
    return AsyncMock(return_value=agent)


class TestInvokeIdempotencyKey:
    def test_idempotency_key_header_present_on_successful_invoke(self) -> None:
        app = _make_invoke_app()
        client = TestClient(app)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"output": "hi", "history": []}
        mock_response.text = "ok"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "api.routes.agents.AgentRegistry.get_by_id",
                _patch_agent_lookup(),
            ),
            patch("api.routes.agents._resolve_agent_auth_token", AsyncMock(return_value=None)),
            patch("api.routes.agents.httpx.AsyncClient") as mock_cls,
        ):
            mock_cls.return_value = mock_client
            resp = client.post(
                "/api/v1/agents/11111111-1111-1111-1111-111111111111/invoke",
                json={"input": "hello"},
            )

        assert resp.status_code == 200
        # Inspect the headers passed to the runtime's httpx call
        call_kwargs = mock_client.post.await_args.kwargs
        sent_headers = call_kwargs["headers"]
        assert "Idempotency-Key" in sent_headers
        key = sent_headers["Idempotency-Key"]
        # UUID4 format
        assert len(key) == 36
        assert key.count("-") == 4

    def test_idempotency_key_is_unique_per_call(self) -> None:
        app = _make_invoke_app()
        client = TestClient(app)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"output": "x", "history": []}
        mock_response.text = "ok"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        keys: list[str] = []
        with (
            patch(
                "api.routes.agents.AgentRegistry.get_by_id",
                _patch_agent_lookup(),
            ),
            patch("api.routes.agents._resolve_agent_auth_token", AsyncMock(return_value=None)),
            patch("api.routes.agents.httpx.AsyncClient") as mock_cls,
        ):
            mock_cls.return_value = mock_client
            for _ in range(2):
                client.post(
                    "/api/v1/agents/22222222-2222-2222-2222-222222222222/invoke",
                    json={"input": "hello"},
                )
                keys.append(mock_client.post.await_args.kwargs["headers"]["Idempotency-Key"])

        assert keys[0] != keys[1]


class TestInvokeRetryOnTransientErrors:
    def test_timeout_then_success_recovers(self) -> None:
        """First call raises TimeoutException, second returns 200 — retry wins."""
        app = _make_invoke_app()
        client = TestClient(app)

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json.return_value = {"output": "recovered", "history": []}
        ok_response.text = "ok"

        mock_client = AsyncMock()
        # First call: timeout. Second: success.
        mock_client.post = AsyncMock(
            side_effect=[httpx.TimeoutException("simulated timeout"), ok_response]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "api.routes.agents.AgentRegistry.get_by_id",
                _patch_agent_lookup(),
            ),
            patch("api.routes.agents._resolve_agent_auth_token", AsyncMock(return_value=None)),
            patch("api.routes.agents.httpx.AsyncClient") as mock_cls,
        ):
            mock_cls.return_value = mock_client
            resp = client.post(
                "/api/v1/agents/33333333-3333-3333-3333-333333333333/invoke",
                json={"input": "hello"},
            )

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["output"] == "recovered"
        assert mock_client.post.await_count == 2

    def test_persistent_timeout_returns_error_response(self) -> None:
        """All retries time out — proxy surfaces the error in the response."""
        app = _make_invoke_app()
        client = TestClient(app)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("persistent timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "api.routes.agents.AgentRegistry.get_by_id",
                _patch_agent_lookup(),
            ),
            patch("api.routes.agents._resolve_agent_auth_token", AsyncMock(return_value=None)),
            patch("api.routes.agents.httpx.AsyncClient") as mock_cls,
        ):
            mock_cls.return_value = mock_client
            resp = client.post(
                "/api/v1/agents/44444444-4444-4444-4444-444444444444/invoke",
                json={"input": "hello"},
            )

        assert resp.status_code == 200  # proxy never 500s on transport error
        body = resp.json()["data"]
        assert body["status_code"] == 0
        assert "TimeoutException" in body["error"]
        # 3 attempts max
        assert mock_client.post.await_count == 3


# ---------------------------------------------------------------------------
# W4-06 — claude_sdk env block consolidation
# ---------------------------------------------------------------------------


class TestClaudeSDKEnvBlockConsolidation:
    def test_delegates_to_shared_build_env_block(self) -> None:
        """The claude_sdk env block must include every env var the shared
        helper would emit (audit finding A6 — previously a bespoke
        implementation could drop env_vars from deploy.env_vars)."""
        config = _make_config(
            framework=FrameworkType.claude_sdk,
            model={
                "primary": "claude-sonnet-4",
                "temperature": 0.5,
                "max_tokens": 4096,
            },
            deploy={
                "cloud": "local",
                "env_vars": {"FOO": "bar", "BAZ": "qux"},
            },
            prompts={"system": "be helpful"},
        )

        runtime = ClaudeSDKRuntime()
        env_block = runtime._build_env_block(config)

        # Shared helper output must be a prefix (or substring) of the
        # consolidated block — every line it produced still appears.
        shared = build_env_block(config, "claude_sdk")
        for line in shared.splitlines():
            assert line in env_block, f"Shared line dropped: {line!r}"

        # Claude-SDK-specific extras are still emitted.
        assert "AGENT_THINKING_ENABLED" in env_block
        assert "AGENT_THINKING_EFFORT" in env_block
        assert "AGENT_PROMPT_CACHING" in env_block
        assert "AGENT_ROUTING_PROVIDER" in env_block

    def test_deploy_env_vars_not_dropped(self) -> None:
        """Regression guard for the env-var-dropping bug the audit flagged."""
        config = _make_config(
            framework=FrameworkType.claude_sdk,
            model={"primary": "claude-sonnet-4"},
            deploy={"cloud": "local", "env_vars": {"CUSTOM_X": "value-x"}},
        )
        env_block = ClaudeSDKRuntime()._build_env_block(config)
        assert 'ENV CUSTOM_X="value-x"' in env_block


# ---------------------------------------------------------------------------
# W4-08 — YAML validator calls framework-specific validate_config
# ---------------------------------------------------------------------------


class TestYamlFrameworkValidatorWiring:
    CLAUDE_SDK_BAD_MODEL = """\
name: test-agent
version: 1.0.0
team: eng
owner: alice@example.com
framework: claude_sdk
model:
  primary: openai/gpt-4o
deploy:
  cloud: local
"""

    LANGGRAPH_OK = """\
name: ok-agent
version: 1.0.0
team: eng
owner: bob@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
"""

    def test_claude_sdk_yaml_with_non_claude_model_fails_at_parse(self) -> None:
        result = validate_config_yaml(self.CLAUDE_SDK_BAD_MODEL)
        assert result.valid is False
        # The framework-level error is in the result
        messages = " ".join(e.message for e in result.errors)
        assert "Claude" in messages or "claude" in messages.lower()
        # Path hint points at the model field
        paths = {e.path for e in result.errors}
        assert "model.primary" in paths

    def test_langgraph_yaml_with_any_model_passes(self) -> None:
        result = validate_config_yaml(self.LANGGRAPH_OK)
        # No framework-specific failures for langgraph
        assert result.valid is True

    def test_default_validate_config_returns_valid(self) -> None:
        """Runtimes that don't override validate_config must accept any config."""
        runtime = LangGraphRuntime()
        config = _make_config()
        result = runtime.validate_config(config)
        assert result.valid is True
        assert result.errors == []


# ---------------------------------------------------------------------------
# W4-05 — runtime test files exist for openai_agents / node / go
# ---------------------------------------------------------------------------


class TestSiblingRuntimeTestsExist:
    """Sanity check — the test files the audit flagged as missing exist."""

    def test_openai_agents_runtime_test_file_present(self) -> None:
        from tests.unit import test_openai_agents_runtime  # noqa: F401

    def test_node_runtime_test_file_present(self) -> None:
        from tests.unit import test_node_runtime  # noqa: F401

    def test_go_runtime_test_file_present(self) -> None:
        from tests.unit import test_runtime_go  # noqa: F401


# ---------------------------------------------------------------------------
# Cleanup any tmp dirs that may have leaked from existing build paths.
# Best-effort — keeps the test environment tidy without affecting assertions.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _cleanup_leaked_tempdirs():
    yield
    root = Path(tempfile.gettempdir())
    for p in root.iterdir():
        if p.name.startswith("agentbreeder-") and p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
