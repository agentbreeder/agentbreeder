"""Tests for the deploy service — deploy job management and API routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from api.models.enums import DeployJobStatus
from api.models.schemas import (
    DeployJobDetailResponse,
    DeployJobResponse,
    DeployLogEntry,
    DeployRequest,
)
from api.services.deploy_service import (
    PIPELINE_STEPS,
    _append_log,
    _job_logs,
    _parse_yaml_fields,
    get_job_logs,
)


class TestParseYamlFields:
    """Tests for the YAML field parser used by create_agent_and_deploy."""

    def test_parse_basic_fields(self) -> None:
        yaml = """name: test-agent
version: "1.0.0"
description: "A test agent"
team: engineering
owner: alice@example.com
framework: langgraph

model:
  primary: claude-sonnet-4
  temperature: 0.7
"""
        result = _parse_yaml_fields(yaml)
        assert result["name"] == "test-agent"
        assert result["version"] == "1.0.0"
        assert result["description"] == "A test agent"
        assert result["team"] == "engineering"
        assert result["owner"] == "alice@example.com"
        assert result["framework"] == "langgraph"
        assert result["model_primary"] == "claude-sonnet-4"

    def test_parse_model_with_fallback(self) -> None:
        yaml = """name: my-agent
version: "0.1.0"
team: default
owner: bob@example.com
framework: crewai

model:
  primary: gpt-4o
  fallback: claude-sonnet-4
"""
        result = _parse_yaml_fields(yaml)
        assert result["model_primary"] == "gpt-4o"
        assert result["model_fallback"] == "claude-sonnet-4"

    def test_parse_missing_fields_uses_defaults(self) -> None:
        yaml = """name: minimal-agent"""
        result = _parse_yaml_fields(yaml)
        assert result["name"] == "minimal-agent"
        assert result["version"] == "0.1.0"
        assert result["team"] == "default"
        assert result["framework"] == "langgraph"
        assert result["model_primary"] == "claude-sonnet-4"
        assert result["model_fallback"] is None

    def test_parse_tags(self) -> None:
        yaml = """name: tagged-agent
version: "1.0.0"
team: ops
owner: charlie@example.com
framework: custom
tags: [production, customer-support, tier-1]
"""
        result = _parse_yaml_fields(yaml)
        assert result["tags"] == ["production", "customer-support", "tier-1"]

    def test_parse_empty_yaml(self) -> None:
        result = _parse_yaml_fields("")
        assert result["name"] == "untitled"
        assert result["version"] == "0.1.0"

    def test_parse_comments_ignored(self) -> None:
        yaml = """# This is a comment
name: real-agent
# Another comment
version: "2.0.0"
"""
        result = _parse_yaml_fields(yaml)
        assert result["name"] == "real-agent"
        assert result["version"] == "2.0.0"


class TestJobLogs:
    """Tests for log management functions."""

    def setup_method(self) -> None:
        _job_logs.clear()

    def test_append_and_get_logs(self) -> None:
        job_id = uuid.uuid4()
        _append_log(job_id, "info", "Starting deploy")
        _append_log(job_id, "warn", "Low memory")

        logs = get_job_logs(job_id)
        assert len(logs) == 2
        assert logs[0]["level"] == "info"
        assert logs[0]["message"] == "Starting deploy"
        assert logs[1]["level"] == "warn"

    def test_get_logs_empty(self) -> None:
        logs = get_job_logs(uuid.uuid4())
        assert logs == []

    def test_append_log_with_step(self) -> None:
        job_id = uuid.uuid4()
        _append_log(job_id, "info", "Building image", step="building")
        logs = get_job_logs(job_id)
        assert logs[0]["step"] == "building"

    def test_log_has_timestamp(self) -> None:
        job_id = uuid.uuid4()
        _append_log(job_id, "info", "test")
        logs = get_job_logs(job_id)
        assert "timestamp" in logs[0]
        # Should be a valid ISO timestamp
        datetime.fromisoformat(logs[0]["timestamp"])


class TestPipelineSteps:
    """Tests for pipeline step configuration."""

    def test_pipeline_has_8_steps(self) -> None:
        assert len(PIPELINE_STEPS) == 8

    def test_pipeline_step_keys_unique(self) -> None:
        keys = [s["key"] for s in PIPELINE_STEPS]
        assert len(keys) == len(set(keys))

    def test_pipeline_steps_have_required_fields(self) -> None:
        for step in PIPELINE_STEPS:
            assert "key" in step
            assert "label" in step
            assert "duration" in step
            assert isinstance(step["duration"], (int, float))
            assert step["duration"] > 0


class TestDeployRequest:
    """Tests for the DeployRequest schema."""

    def test_deploy_request_with_agent_id(self) -> None:
        aid = uuid.uuid4()
        req = DeployRequest(agent_id=aid, target="local")
        assert req.agent_id == aid
        assert req.target == "local"
        assert req.config_yaml is None

    def test_deploy_request_with_yaml(self) -> None:
        req = DeployRequest(config_yaml="name: test\n", target="gcp")
        assert req.config_yaml == "name: test\n"
        assert req.target == "gcp"
        assert req.agent_id is None

    def test_deploy_request_defaults(self) -> None:
        req = DeployRequest()
        assert req.target == "local"
        assert req.agent_id is None
        assert req.config_yaml is None
        assert req.config_path is None


class TestDeploySchemas:
    """Tests for deploy-related Pydantic schemas."""

    def test_deploy_job_response(self) -> None:
        resp = DeployJobResponse(
            id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            status=DeployJobStatus.building,
            target="local",
            error_message=None,
            started_at=datetime.now(UTC),
            completed_at=None,
        )
        assert resp.status == DeployJobStatus.building
        assert resp.agent_name is None

    def test_deploy_log_entry(self) -> None:
        entry = DeployLogEntry(
            timestamp="2026-03-12T10:00:00Z",
            level="info",
            message="Building container",
            step="building",
        )
        assert entry.level == "info"
        assert entry.step == "building"

    def test_deploy_job_detail_response(self) -> None:
        resp = DeployJobDetailResponse(
            id=str(uuid.uuid4()),
            agent_id=str(uuid.uuid4()),
            status="building",
            target="local",
            logs=[
                DeployLogEntry(
                    timestamp="2026-03-12T10:00:00Z",
                    level="info",
                    message="Starting",
                ),
            ],
        )
        assert len(resp.logs) == 1
        assert resp.logs[0].message == "Starting"

    def test_deploy_job_detail_empty_logs(self) -> None:
        resp = DeployJobDetailResponse(
            id=str(uuid.uuid4()),
            agent_id=str(uuid.uuid4()),
            status="pending",
            target="local",
        )
        assert resp.logs == []
