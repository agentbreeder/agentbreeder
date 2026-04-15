"""Claude Managed Agents integration test.

Requires a valid ANTHROPIC_API_KEY with managed-agents-2026-04-01 beta access.

Run:
    RUN_CLAUDE_MANAGED_INTEGRATION_TESTS=1 \
        pytest tests/integration/test_claude_managed_integration.py -v -s
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
    """Create a Claude Managed Agent, send a message, verify response, then tear down."""
    from engine.config_parser import (
        AccessConfig,
        AgentConfig,
        ClaudeManagedConfig,
        CloudType,
        DeployConfig,
        FrameworkType,
        ModelConfig,
        PromptsConfig,
    )
    from engine.deployers.claude_managed import ClaudeManagedDeployer

    config = AgentConfig(
        name="agentbreeder-integration-test",
        version="1.0.0",
        description="Integration test agent",
        team="engineering",
        owner="test@agentbreeder.dev",
        framework=FrameworkType.claude_sdk,
        # Use cheapest model for testing
        model=ModelConfig(primary="claude-haiku-4-5-20251001"),
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
    print(f"Agent ID:    {infra.resource_ids['agent_id']}")
    print(f"Env ID:      {infra.resource_ids['environment_id']}")

    # 2. Deploy (returns endpoint, no container involved)
    result = await deployer.deploy(config, image=None)  # type: ignore[arg-type]
    assert result.status == "running"
    assert result.endpoint_url == infra.endpoint_url
    print(f"Deploy result: {result.endpoint_url}")

    # 3. Health check
    health = await deployer.health_check(result)
    assert health.healthy is True
    print("Health check: passed (managed by Anthropic)")

    # 4. Send a real message via sessions API and verify response
    try:
        from anthropic import Anthropic
    except ImportError:
        pytest.skip("anthropic SDK not installed")

    client = Anthropic()
    agent_id = deployer._agent_id
    env_id = deployer._environment_id

    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=env_id,
        title="Integration test session",
    )
    print(f"Session ID: {session.id}")

    response_text = ""
    with client.beta.sessions.events.stream(session.id) as stream:
        client.beta.sessions.events.send(
            session.id,
            events=[
                {
                    "type": "user.message",
                    "content": [
                        {
                            "type": "text",
                            "text": "Reply with exactly: INTEGRATION_TEST_OK",
                        }
                    ],
                }
            ],
        )
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if hasattr(block, "text"):
                        response_text += block.text
            elif event.type == "session.status_idle":
                break

    print(f"Agent response: {response_text!r}")
    assert "INTEGRATION_TEST_OK" in response_text, (
        f"Expected 'INTEGRATION_TEST_OK' in response, got: {response_text!r}"
    )

    # 5. Logs (informational — returns session guidance)
    logs = await deployer.get_logs(config.name)
    print(f"Logs: {logs[0]}")

    # 6. Teardown
    await deployer.teardown(config.name)
    print("Teardown complete.")
