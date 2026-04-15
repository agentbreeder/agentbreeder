# Claude Managed Agents Deployer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `cloud: claude-managed` as a deployment target that creates an Anthropic Managed Agent + Environment via the Anthropic API instead of building/pushing a container.

**Architecture:** When `deploy.cloud == "claude-managed"`, the engine skips the container build step entirely. The deployer calls `POST /v1/agents` and `POST /v1/environments`, stores the IDs as `anthropic://agents/{id}?env={env_id}`, and registers that as the endpoint. `agentbreeder chat` detects the `anthropic://` scheme and creates sessions + streams events instead of making HTTP calls.

**Tech Stack:** `anthropic` Python SDK (already a dependency), `pydantic`, Python 3.11+

**Reference:** Anthropic Managed Agents docs: `https://platform.claude.com/docs/en/managed-agents/overview`
Beta header required: `anthropic-beta: managed-agents-2026-04-01`

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Modify | `engine/config_parser.py` | Add `CloudType.claude_managed`, `ClaudeManagedConfig` model |
| Create | `engine/deployers/claude_managed.py` | Full `ClaudeManagedDeployer` implementation |
| Modify | `engine/deployers/__init__.py` | Register `claude-managed` in `DEPLOYERS` |
| Modify | `engine/builder.py` | Skip container build for `cloud: claude-managed` |
| Modify | `cli/commands/chat.py` | Handle `anthropic://` endpoints with sessions |
| Create | `tests/unit/test_deployers_claude_managed.py` | Unit tests (mocked Anthropic SDK) |
| Create | `tests/integration/test_claude_managed_integration.py` | Real Anthropic API test (gated) |

---

## Task 1: Extend `config_parser.py`

**Files:**
- Modify: `engine/config_parser.py`

- [ ] **Step 1: Write the failing test first**

```python
# In tests/unit/test_config_parser.py, add this test class:

class TestClaudeManagedConfig:
    def test_claude_managed_cloud_type_is_valid(self) -> None:
        from engine.config_parser import CloudType
        assert CloudType.claude_managed == "claude-managed"

    def test_agent_config_accepts_claude_managed_cloud(self) -> None:
        from engine.config_parser import (
            AgentConfig, CloudType, DeployConfig, FrameworkType,
            ModelConfig, AccessConfig, ClaudeManagedConfig,
        )
        config = AgentConfig(
            name="my-agent", version="1.0.0", team="eng",
            owner="alice@example.com",
            framework=FrameworkType.claude_sdk,
            model=ModelConfig(primary="claude-sonnet-4-6"),
            deploy=DeployConfig(cloud=CloudType.claude_managed),
            access=AccessConfig(),
            claude_managed=ClaudeManagedConfig(),
        )
        assert config.deploy.cloud == "claude-managed"
        assert config.claude_managed.environment.networking == "unrestricted"

    def test_claude_managed_tools_default_to_full_toolset(self) -> None:
        from engine.config_parser import ClaudeManagedConfig
        cfg = ClaudeManagedConfig()
        assert len(cfg.tools) == 1
        assert cfg.tools[0].type == "agent_toolset_20260401"
```

- [ ] **Step 2: Run to confirm failure**

```bash
python3 -m pytest tests/unit/test_config_parser.py::TestClaudeManagedConfig -x -q 2>&1 | head -10
```

Expected: `AttributeError: 'CloudType' has no 'claude_managed'`

- [ ] **Step 3: Add `CloudType.claude_managed` to the enum**

In `engine/config_parser.py`, find the `CloudType` class (line ~31) and add:

```python
class CloudType(enum.StrEnum):
    aws = "aws"
    azure = "azure"
    gcp = "gcp"
    kubernetes = "kubernetes"
    local = "local"
    claude_managed = "claude-managed"   # NEW
```

- [ ] **Step 4: Add `ClaudeManagedConfig` models**

After the `GoogleADKConfig` class, add:

```python
class ClaudeManagedEnvironmentConfig(BaseModel):
    """Environment container config for Claude Managed Agents."""
    networking: Literal["unrestricted", "restricted"] = "unrestricted"


class ClaudeManagedToolConfig(BaseModel):
    """A single tool entry in the Claude Managed Agent definition."""
    type: str = "agent_toolset_20260401"


class ClaudeManagedConfig(BaseModel):
    """Top-level claude_managed: block in agent.yaml.

    Only read when deploy.cloud == "claude-managed".
    framework field is stored as metadata only — no container is built.
    """
    environment: ClaudeManagedEnvironmentConfig = Field(
        default_factory=ClaudeManagedEnvironmentConfig
    )
    tools: list[ClaudeManagedToolConfig] = Field(
        default_factory=lambda: [ClaudeManagedToolConfig()]
    )
```

You also need `from typing import Literal` at the top if not already imported.

- [ ] **Step 5: Add `claude_managed` field to `AgentConfig`**

In the `AgentConfig` class, after the `google_adk` field, add:

```python
claude_managed: ClaudeManagedConfig | None = None
```

- [ ] **Step 6: Run config parser tests**

```bash
python3 -m pytest tests/unit/test_config_parser.py -x -q 2>&1 | tail -10
```

Expected: All pass including the new `TestClaudeManagedConfig` tests.

- [ ] **Step 7: Commit**

```bash
git add engine/config_parser.py tests/unit/test_config_parser.py
git commit -m "feat(config): add claude-managed CloudType and ClaudeManagedConfig model"
```

---

## Task 2: Write failing tests for `ClaudeManagedDeployer`

**Files:**
- Create: `tests/unit/test_deployers_claude_managed.py`

- [ ] **Step 1: Create test file**

```python
"""Unit tests for the Claude Managed Agents deployer.

All Anthropic API calls are mocked — no real API key or beta access required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.config_parser import (
    AccessConfig,
    AgentConfig,
    ClaudeManagedConfig,
    CloudType,
    DeployConfig,
    FrameworkType,
    ModelConfig,
    PromptsConfig,
    AccessConfig,
)
from engine.deployers.base import DeployResult


def _make_agent_config(
    *,
    name: str = "my-claude-agent",
    version: str = "1.0.0",
) -> AgentConfig:
    return AgentConfig(
        name=name,
        version=version,
        description="Test Claude Managed Agent",
        team="engineering",
        owner="alice@example.com",
        framework=FrameworkType.claude_sdk,
        model=ModelConfig(primary="claude-sonnet-4-6"),
        deploy=DeployConfig(cloud=CloudType.claude_managed),
        access=AccessConfig(),
        prompts=PromptsConfig(
            system="You are a helpful assistant."
        ),
        claude_managed=ClaudeManagedConfig(),
    )


def _make_deployer():
    from engine.deployers.claude_managed import ClaudeManagedDeployer
    return ClaudeManagedDeployer()


class TestClaudeManagedDeployer:
    @pytest.mark.asyncio
    async def test_provision_calls_create_agent_and_environment(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        mock_agent = MagicMock()
        mock_agent.id = "agent_abc123"
        mock_agent.version = 1

        mock_env = MagicMock()
        mock_env.id = "env_xyz789"

        mock_client = MagicMock()
        mock_client.beta.agents.create = AsyncMock(return_value=mock_agent)
        mock_client.beta.environments.create = AsyncMock(return_value=mock_env)

        with patch("engine.deployers.claude_managed._get_anthropic_client", return_value=mock_client):
            result = await deployer.provision(config)

        mock_client.beta.agents.create.assert_awaited_once()
        mock_client.beta.environments.create.assert_awaited_once()

        assert result.endpoint_url == "anthropic://agents/agent_abc123?env=env_xyz789"
        assert result.resource_ids["agent_id"] == "agent_abc123"
        assert result.resource_ids["environment_id"] == "env_xyz789"

    @pytest.mark.asyncio
    async def test_provision_maps_model_and_system_prompt(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        mock_agent = MagicMock(id="agent_abc123", version=1)
        mock_env = MagicMock(id="env_xyz789")
        mock_client = MagicMock()
        mock_client.beta.agents.create = AsyncMock(return_value=mock_agent)
        mock_client.beta.environments.create = AsyncMock(return_value=mock_env)

        with patch("engine.deployers.claude_managed._get_anthropic_client", return_value=mock_client):
            await deployer.provision(config)

        create_kwargs = mock_client.beta.agents.create.call_args.kwargs
        assert create_kwargs["model"] == "claude-sonnet-4-6"
        assert create_kwargs["system"] == "You are a helpful assistant."
        assert create_kwargs["name"] == "my-claude-agent"

    @pytest.mark.asyncio
    async def test_provision_maps_tools_from_claude_managed_config(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        mock_agent = MagicMock(id="agent_abc123", version=1)
        mock_env = MagicMock(id="env_xyz789")
        mock_client = MagicMock()
        mock_client.beta.agents.create = AsyncMock(return_value=mock_agent)
        mock_client.beta.environments.create = AsyncMock(return_value=mock_env)

        with patch("engine.deployers.claude_managed._get_anthropic_client", return_value=mock_client):
            await deployer.provision(config)

        create_kwargs = mock_client.beta.agents.create.call_args.kwargs
        assert len(create_kwargs["tools"]) == 1
        assert create_kwargs["tools"][0]["type"] == "agent_toolset_20260401"

    @pytest.mark.asyncio
    async def test_provision_raises_import_error_without_anthropic_sdk(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        with patch(
            "engine.deployers.claude_managed._get_anthropic_client",
            side_effect=ImportError("anthropic not installed"),
        ):
            with pytest.raises(ImportError, match="pip install anthropic"):
                await deployer.provision(config)

    @pytest.mark.asyncio
    async def test_deploy_returns_anthropic_endpoint(self) -> None:
        """deploy() stores IDs from provision() and returns the anthropic:// endpoint."""
        deployer = _make_deployer()
        config = _make_agent_config()

        deployer._agent_id = "agent_abc123"
        deployer._environment_id = "env_xyz789"

        image = MagicMock()  # should be ignored for claude-managed
        result = await deployer.deploy(config, image)

        assert result.endpoint_url == "anthropic://agents/agent_abc123?env=env_xyz789"
        assert result.status == "running"
        assert result.agent_name == "my-claude-agent"

    @pytest.mark.asyncio
    async def test_deploy_raises_if_provision_not_called(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        image = MagicMock()

        with pytest.raises(RuntimeError, match="provision"):
            await deployer.deploy(config, image)

    @pytest.mark.asyncio
    async def test_teardown_deletes_agent_and_environment(self) -> None:
        deployer = _make_deployer()
        deployer._agent_id = "agent_abc123"
        deployer._environment_id = "env_xyz789"

        mock_client = MagicMock()
        mock_client.beta.agents.delete = AsyncMock()
        mock_client.beta.environments.delete = AsyncMock()

        with patch("engine.deployers.claude_managed._get_anthropic_client", return_value=mock_client):
            await deployer.teardown("my-claude-agent")

        mock_client.beta.agents.delete.assert_awaited_once_with("agent_abc123")
        mock_client.beta.environments.delete.assert_awaited_once_with("env_xyz789")

    @pytest.mark.asyncio
    async def test_health_check_always_returns_healthy(self) -> None:
        """Claude Managed Agents are managed by Anthropic — health is assumed."""
        deployer = _make_deployer()
        result = DeployResult(
            endpoint_url="anthropic://agents/agent_abc123?env=env_xyz789",
            container_id="agent_abc123",
            status="running",
            agent_name="my-claude-agent",
            version="1.0.0",
        )
        health = await deployer.health_check(result)
        assert health.healthy is True
        assert health.checks["managed_by_anthropic"] is True

    @pytest.mark.asyncio
    async def test_get_logs_returns_not_applicable_message(self) -> None:
        """App logs live inside Anthropic sessions — no CloudWatch/Cloud Logging."""
        deployer = _make_deployer()
        deployer._agent_id = "agent_abc123"
        logs = await deployer.get_logs("my-claude-agent")
        assert len(logs) == 1
        assert "session" in logs[0].lower() or "anthropic" in logs[0].lower()


class TestDeployerRegistry:
    def test_claude_managed_routes_to_claude_managed_deployer(self) -> None:
        from engine.deployers import get_deployer
        from engine.deployers.claude_managed import ClaudeManagedDeployer
        deployer = get_deployer(CloudType.claude_managed)
        assert isinstance(deployer, ClaudeManagedDeployer)
```

- [ ] **Step 2: Run to confirm failure**

```bash
python3 -m pytest tests/unit/test_deployers_claude_managed.py -x -q 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'engine.deployers.claude_managed'`

---

## Task 3: Implement `ClaudeManagedDeployer`

**Files:**
- Create: `engine/deployers/claude_managed.py`

- [ ] **Step 1: Create the implementation**

```python
"""Claude Managed Agents deployer.

Instead of building and pushing a container image, this deployer:
1. Creates an Anthropic Managed Agent via POST /v1/agents
2. Creates an Anthropic Environment via POST /v1/environments
3. Returns anthropic://agents/{agent_id}?env={env_id} as the endpoint URL

No container build step occurs — engine/builder.py skips the build phase
when config.deploy.cloud == "claude-managed".

Sessions are created on demand by agentbreeder chat when it detects the
anthropic:// URL scheme.

Beta header: anthropic-beta: managed-agents-2026-04-01
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from engine.config_parser import AgentConfig
from engine.deployers.base import BaseDeployer, DeployResult, HealthStatus, InfraResult
from engine.runtimes.base import ContainerImage

logger = logging.getLogger(__name__)

BETA_HEADER = "managed-agents-2026-04-01"


def _get_anthropic_client() -> Any:
    """Get the Anthropic client with the managed-agents beta header.

    Raises ImportError with install instructions if the SDK is missing.
    """
    try:
        from anthropic import Anthropic
    except ImportError as e:
        msg = (
            "anthropic SDK is not installed. "
            "Run: pip install anthropic"
        )
        raise ImportError(msg) from e

    # The Python SDK sets the beta header automatically when using
    # client.beta.agents / client.beta.environments / client.beta.sessions
    return Anthropic()


def _build_anthropic_endpoint(agent_id: str, environment_id: str) -> str:
    """Build the anthropic:// endpoint URL from agent and environment IDs."""
    return f"anthropic://agents/{agent_id}?env={environment_id}"


class ClaudeManagedDeployer(BaseDeployer):
    """Deploys agents to Anthropic Claude Managed Agents infrastructure.

    No container image is built or pushed. The agent.yaml system prompt,
    model, and tools are mapped directly to the Anthropic Agent API.
    """

    def __init__(self) -> None:
        self._agent_id: str | None = None
        self._environment_id: str | None = None

    def _resolve_system_prompt(self, config: AgentConfig) -> str:
        """Get the system prompt string from the prompts config.

        Supports inline strings only. Registry ref resolution is handled
        by engine/resolver.py before the deployer is called.
        """
        if config.prompts and config.prompts.system:
            return str(config.prompts.system)
        return f"You are {config.name}, an AI agent. {config.description or ''}"

    async def provision(self, config: AgentConfig) -> InfraResult:
        """Create the Anthropic Agent and Environment definitions.

        This is the entire "deployment" for Claude Managed Agents —
        no infrastructure provisioning occurs beyond these API calls.
        """
        try:
            client = _get_anthropic_client()
        except ImportError:
            raise

        cm_config = config.claude_managed
        tools = [{"type": t.type} for t in cm_config.tools] if cm_config else [
            {"type": "agent_toolset_20260401"}
        ]
        networking_type = (
            cm_config.environment.networking if cm_config else "unrestricted"
        )

        system_prompt = self._resolve_system_prompt(config)

        logger.info(
            "Creating Anthropic Managed Agent '%s' with model '%s'",
            config.name, config.model.primary,
        )

        agent = await client.beta.agents.create(
            name=config.name,
            model=config.model.primary,
            system=system_prompt,
            tools=tools,
        )
        self._agent_id = agent.id
        logger.info("Created Anthropic Agent: %s (version %s)", agent.id, agent.version)

        environment = await client.beta.environments.create(
            name=f"{config.name}-env",
            config={
                "type": "cloud",
                "networking": {"type": networking_type},
            },
        )
        self._environment_id = environment.id
        logger.info("Created Anthropic Environment: %s", environment.id)

        endpoint_url = _build_anthropic_endpoint(self._agent_id, self._environment_id)

        return InfraResult(
            endpoint_url=endpoint_url,
            resource_ids={
                "agent_id": self._agent_id,
                "environment_id": self._environment_id,
                "agent_version": str(agent.version),
            },
        )

    async def deploy(self, config: AgentConfig, image: ContainerImage) -> DeployResult:
        """Return the anthropic:// endpoint. Container image is ignored.

        provision() must be called first to create the agent and environment.
        """
        if self._agent_id is None or self._environment_id is None:
            msg = (
                "Cannot deploy: provision() must be called first to create the "
                "Anthropic Agent and Environment."
            )
            raise RuntimeError(msg)

        endpoint_url = _build_anthropic_endpoint(self._agent_id, self._environment_id)

        logger.info(
            "Claude Managed Agent deployed: %s → %s", config.name, endpoint_url
        )

        return DeployResult(
            endpoint_url=endpoint_url,
            container_id=self._agent_id,
            status="running",
            agent_name=config.name,
            version=config.version,
        )

    async def health_check(
        self,
        deploy_result: DeployResult,
        timeout: int = 30,
        interval: int = 5,
    ) -> HealthStatus:
        """Claude Managed Agents are fully managed by Anthropic.

        Health is assumed — if the agent was created successfully, it is available.
        """
        return HealthStatus(
            healthy=True,
            checks={"managed_by_anthropic": True},
        )

    async def teardown(self, agent_name: str) -> None:
        """Delete the Anthropic Agent and Environment definitions."""
        client = _get_anthropic_client()

        if self._agent_id:
            logger.info("Deleting Anthropic Agent: %s", self._agent_id)
            await client.beta.agents.delete(self._agent_id)
            logger.info("Deleted Anthropic Agent: %s", self._agent_id)

        if self._environment_id:
            logger.info("Deleting Anthropic Environment: %s", self._environment_id)
            await client.beta.environments.delete(self._environment_id)
            logger.info("Deleted Anthropic Environment: %s", self._environment_id)

    async def get_logs(self, agent_name: str, since: datetime | None = None) -> list[str]:
        """Logs for Claude Managed Agents live inside individual sessions.

        Use `agentbreeder chat` to start a session and view its event stream.
        There is no persistent log group — Anthropic manages session history
        server-side and it is accessible via the sessions API.
        """
        agent_id = self._agent_id or "(unknown)"
        return [
            f"Logs for Claude Managed Agent '{agent_name}' (ID: {agent_id}) are "
            "available per-session via the Anthropic sessions API. "
            "Use `agentbreeder chat {agent_name}` to start a session and stream events."
        ]

    async def get_url(self, agent_name: str) -> str:
        """Return the anthropic:// endpoint for this agent."""
        if self._agent_id is None or self._environment_id is None:
            msg = "Cannot get URL: agent not yet provisioned."
            raise RuntimeError(msg)
        return _build_anthropic_endpoint(self._agent_id, self._environment_id)

    async def status(self, agent_name: str) -> dict[str, Any]:
        """Get status of the Anthropic Agent definition."""
        if self._agent_id is None:
            return {"name": agent_name, "status": "not_provisioned"}

        try:
            client = _get_anthropic_client()
            agent = await client.beta.agents.retrieve(self._agent_id)
            return {
                "name": agent_name,
                "agent_id": self._agent_id,
                "environment_id": self._environment_id,
                "status": "running",
                "model": agent.model if hasattr(agent, "model") else "unknown",
                "version": agent.version if hasattr(agent, "version") else "unknown",
            }
        except Exception as exc:
            return {"name": agent_name, "status": "error", "error": str(exc)}
```

- [ ] **Step 2: Run unit tests**

```bash
python3 -m pytest tests/unit/test_deployers_claude_managed.py -x -q 2>&1 | tail -15
```

Expected: All tests pass except `TestDeployerRegistry` (needs `__init__.py` update).

- [ ] **Step 3: Commit**

```bash
git add engine/deployers/claude_managed.py tests/unit/test_deployers_claude_managed.py
git commit -m "feat(deployers): add ClaudeManagedDeployer for Anthropic Managed Agents"
```

---

## Task 4: Register `ClaudeManagedDeployer` in the deployer registry

**Files:**
- Modify: `engine/deployers/__init__.py`

- [ ] **Step 1: Add import and registry entry**

```python
# Add import after existing imports:
from engine.deployers.claude_managed import ClaudeManagedDeployer

# Add to DEPLOYERS dict:
    CloudType.claude_managed: ClaudeManagedDeployer,
```

- [ ] **Step 2: Run registry test**

```bash
python3 -m pytest tests/unit/test_deployers_claude_managed.py::TestDeployerRegistry -v
```

Expected: PASS

- [ ] **Step 3: Run all deployer tests**

```bash
python3 -m pytest tests/unit/test_deployers_aws.py tests/unit/test_deployers_aws_app_runner.py tests/unit/test_deployers_azure.py tests/unit/test_deployers_kubernetes.py tests/unit/test_gcp_cloudrun_deployer.py tests/unit/test_deployers_claude_managed.py -q 2>&1 | tail -10
```

Expected: All 180+ tests pass.

- [ ] **Step 4: Commit**

```bash
git add engine/deployers/__init__.py
git commit -m "feat(deployers): register ClaudeManagedDeployer for cloud: claude-managed"
```

---

## Task 5: Skip container build for `cloud: claude-managed`

**Files:**
- Modify: `engine/builder.py`

Find where `builder.build()` is called in `engine/builder.py` (the `build()` or `run()` method that coordinates the pipeline). Add a guard before the build step:

- [ ] **Step 1: Locate the build invocation**

```bash
grep -n "def build\|def run\|cloud\|framework\|ContainerImage" engine/builder.py | head -30
```

- [ ] **Step 2: Add the skip guard**

Find the line where `runtime_builder.build(...)` is called and wrap it:

```python
from engine.config_parser import CloudType

# Before calling build():
if config.deploy.cloud == CloudType.claude_managed:
    # Claude Managed Agents use Anthropic's infrastructure —
    # no container image is built or pushed.
    logger.info(
        "Skipping container build for cloud: claude-managed — "
        "Anthropic manages the runtime"
    )
    return None  # or a sentinel ContainerImage; adjust to match existing return type
```

- [ ] **Step 3: Add a unit test for the skip behaviour**

```python
# In tests/unit/test_deploy_engine.py or tests/unit/test_builder_extended.py, add:

def test_builder_skips_build_for_claude_managed(tmp_path) -> None:
    from engine.config_parser import (
        AgentConfig, CloudType, DeployConfig, FrameworkType,
        ModelConfig, AccessConfig
    )
    from engine.builder import DeployEngine  # adjust import to actual class

    config = AgentConfig(
        name="claude-agent", version="1.0.0", team="eng",
        owner="alice@example.com",
        framework=FrameworkType.claude_sdk,
        model=ModelConfig(primary="claude-sonnet-4-6"),
        deploy=DeployConfig(cloud=CloudType.claude_managed),
        access=AccessConfig(),
    )

    # The builder should return None (no image) for claude-managed
    # without calling any runtime build methods
    # (Adjust this test based on the actual builder API once you read engine/builder.py)
```

- [ ] **Step 4: Run the relevant builder tests**

```bash
python3 -m pytest tests/unit/test_deploy_engine.py tests/unit/test_builder_extended.py -x -q 2>&1 | tail -10
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add engine/builder.py
git commit -m "feat(engine): skip container build when cloud: claude-managed"
```

---

## Task 6: Update `agentbreeder chat` to handle `anthropic://` endpoints

**Files:**
- Modify: `cli/commands/chat.py`

- [ ] **Step 1: Find the current chat implementation**

```bash
grep -n "def chat\|endpoint\|http\|request\|invoke" cli/commands/chat.py | head -20
```

- [ ] **Step 2: Add `anthropic://` branch**

The current `chat.py` likely sends HTTP requests to the agent endpoint. Add a branch that detects `anthropic://` and uses the Anthropic sessions API instead:

```python
def _is_managed_agent_endpoint(endpoint_url: str) -> bool:
    return endpoint_url.startswith("anthropic://agents/")


def _parse_managed_endpoint(endpoint_url: str) -> tuple[str, str]:
    """Parse anthropic://agents/{agent_id}?env={env_id} → (agent_id, env_id)."""
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(endpoint_url)
    agent_id = parsed.path.lstrip("/")
    env_id = parse_qs(parsed.query).get("env", [""])[0]
    return agent_id, env_id


async def _chat_via_managed_agent(
    agent_id: str,
    environment_id: str,
    message: str,
) -> None:
    """Create a session and stream events for a Claude Managed Agent."""
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise RuntimeError(
            "anthropic SDK not installed. Run: pip install anthropic"
        ) from e

    client = Anthropic()

    # Create session
    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=environment_id,
        title="agentbreeder chat session",
    )

    # Stream events
    with client.beta.sessions.events.stream(session.id) as stream:
        client.beta.sessions.events.send(
            session.id,
            events=[{
                "type": "user.message",
                "content": [{"type": "text", "text": message}],
            }],
        )
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if hasattr(block, "text"):
                        print(block.text, end="", flush=True)
            elif event.type == "session.status_idle":
                print()  # newline after streaming
                break
```

Then in the main chat loop, replace the HTTP call with:

```python
if _is_managed_agent_endpoint(endpoint_url):
    agent_id, env_id = _parse_managed_endpoint(endpoint_url)
    await _chat_via_managed_agent(agent_id, env_id, user_message)
else:
    # existing HTTP call to /invoke or /chat
    ...
```

- [ ] **Step 3: Add a unit test for endpoint parsing**

```python
# In tests/unit/test_cli_commands_extended.py or a new file, add:

def test_parse_managed_endpoint_extracts_ids() -> None:
    from cli.commands.chat import _parse_managed_endpoint
    agent_id, env_id = _parse_managed_endpoint(
        "anthropic://agents/agent_abc123?env=env_xyz789"
    )
    assert agent_id == "agent_abc123"
    assert env_id == "env_xyz789"

def test_is_managed_agent_endpoint_true_for_anthropic_scheme() -> None:
    from cli.commands.chat import _is_managed_agent_endpoint
    assert _is_managed_agent_endpoint("anthropic://agents/agent_abc123?env=env_xyz789") is True
    assert _is_managed_agent_endpoint("https://abc.awsapprunner.com") is False
```

- [ ] **Step 4: Run CLI tests**

```bash
python3 -m pytest tests/unit/test_cli.py tests/unit/test_cli_commands_extended.py -x -q 2>&1 | tail -10
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add cli/commands/chat.py
git commit -m "feat(cli): handle anthropic:// endpoints in agentbreeder chat with managed sessions"
```

---

## Task 7: Integration test (gated behind `RUN_CLAUDE_MANAGED_INTEGRATION_TESTS`)

**Files:**
- Create: `tests/integration/test_claude_managed_integration.py`

> **Note:** Requires a valid `ANTHROPIC_API_KEY` with beta access (`managed-agents-2026-04-01`).

- [ ] **Step 1: Create the integration test**

```python
"""Claude Managed Agents integration test.

Requires:
  RUN_CLAUDE_MANAGED_INTEGRATION_TESTS=1
  ANTHROPIC_API_KEY with managed-agents-2026-04-01 beta access

Run: RUN_CLAUDE_MANAGED_INTEGRATION_TESTS=1 pytest tests/integration/test_claude_managed_integration.py -v -s
"""
from __future__ import annotations

import os
import pytest

SKIP_REASON = "Set RUN_CLAUDE_MANAGED_INTEGRATION_TESTS=1 to run Claude Managed Agents tests"
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_CLAUDE_MANAGED_INTEGRATION_TESTS") != "1",
    reason=SKIP_REASON,
)


@pytest.mark.asyncio
async def test_claude_managed_full_deploy_chat_teardown() -> None:
    """Create a Claude Managed Agent, chat with it, then tear it down."""
    from engine.config_parser import (
        AgentConfig, AccessConfig, ClaudeManagedConfig, CloudType,
        DeployConfig, FrameworkType, ModelConfig, PromptsConfig,
    )
    from engine.deployers.claude_managed import ClaudeManagedDeployer

    config = AgentConfig(
        name="agentbreeder-integration-test",
        version="1.0.0",
        description="Integration test agent",
        team="engineering",
        owner="test@agentbreeder.dev",
        framework=FrameworkType.claude_sdk,
        model=ModelConfig(primary="claude-haiku-4-5-20251001"),  # cheapest model for testing
        deploy=DeployConfig(cloud=CloudType.claude_managed),
        access=AccessConfig(),
        prompts=PromptsConfig(system="You are a helpful test assistant. Be very brief."),
        claude_managed=ClaudeManagedConfig(),
    )

    deployer = ClaudeManagedDeployer()

    # 1. Provision (creates Anthropic Agent + Environment)
    infra = await deployer.provision(config)
    assert infra.endpoint_url.startswith("anthropic://agents/")
    print(f"\nProvisioned: {infra.endpoint_url}")

    # 2. Deploy (returns endpoint, no container involved)
    result = await deployer.deploy(config, image=None)  # type: ignore[arg-type]
    assert result.status == "running"

    # 3. Health check
    health = await deployer.health_check(result)
    assert health.healthy is True

    # 4. Send a real message via sessions API and verify response
    from anthropic import Anthropic
    client = Anthropic()
    agent_id, env_id = deployer._agent_id, deployer._environment_id

    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=env_id,
        title="Integration test session",
    )

    response_text = ""
    with client.beta.sessions.events.stream(session.id) as stream:
        client.beta.sessions.events.send(
            session.id,
            events=[{
                "type": "user.message",
                "content": [{"type": "text", "text": "Reply with exactly: INTEGRATION_TEST_OK"}],
            }],
        )
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if hasattr(block, "text"):
                        response_text += block.text
            elif event.type == "session.status_idle":
                break

    print(f"Agent response: {response_text}")
    assert "INTEGRATION_TEST_OK" in response_text, (
        f"Expected 'INTEGRATION_TEST_OK' in response, got: {response_text}"
    )

    # 5. Teardown
    await deployer.teardown(config.name)
    print("Teardown complete.")
```

- [ ] **Step 2: Commit**

```bash
git add tests/integration/test_claude_managed_integration.py
git commit -m "test(integration): add Claude Managed Agents integration test (gated)"
```
