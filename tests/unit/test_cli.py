"""Tests for CLI commands — validate, list, describe."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()

VALID_YAML = """\
name: test-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
"""


class TestValidateCommand:
    def test_validate_valid_yaml(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()
        result = runner.invoke(app, ["validate", f.name])
        assert result.exit_code == 0
        assert "Valid" in result.output

    def test_validate_invalid_yaml(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("name: test-agent\nversion: 1.0.0\n")
        f.close()
        result = runner.invoke(app, ["validate", f.name])
        assert result.exit_code == 1

    def test_validate_json_output_valid(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()
        result = runner.invoke(app, ["validate", f.name, "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["valid"] is True

    def test_validate_json_output_invalid(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("name: test-agent\n")
        f.close()
        result = runner.invoke(app, ["validate", f.name, "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output)
        assert output["valid"] is False
        assert len(output["errors"]) > 0

    def test_validate_detects_memory_yaml_by_filename(self) -> None:
        """A file named memory.yaml is validated against the memory schema,
        not the agent schema. Regression for: validate memory.yaml reported
        'framework/runtime required' because it always used the agent schema.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.yaml"
            path.write_text(
                "spec_version: v1\n"
                "name: test-memory\n"
                "version: 1.0.0\n"
                "team: engineering\n"
                "owner: alice@example.com\n"
                "backend: postgresql\n"
                "memory_type: buffer_window\n"
            )
            result = runner.invoke(app, ["validate", str(path), "--json"])
            output = json.loads(result.output)
            # No more "must specify framework or runtime" agent-schema errors
            messages = " ".join(e.get("message", "") for e in output.get("errors", []))
            assert "framework" not in messages.lower()
            assert "runtime" not in messages.lower()

    def test_validate_detects_memory_yaml_by_content(self) -> None:
        """A file whose content has backend + memory_type (no framework/runtime)
        is detected as a memory config even if its filename is unusual."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "my-store.yaml"
            path.write_text(
                "name: test-memory\n"
                "version: 1.0.0\n"
                "team: engineering\n"
                "owner: alice@example.com\n"
                "backend: postgresql\n"
                "memory_type: buffer_window\n"
            )
            result = runner.invoke(app, ["validate", str(path), "--json"])
            output = json.loads(result.output)
            messages = " ".join(e.get("message", "") for e in output.get("errors", []))
            assert "framework" not in messages.lower()


class TestListCommand:
    def test_list_no_agents(self) -> None:
        with patch("cli.commands.list_cmd.REGISTRY_DIR", Path(tempfile.mkdtemp())):
            result = runner.invoke(app, ["list", "agents"])
        assert result.exit_code == 0
        assert "No agents" in result.output or "[]" in result.output

    def test_list_with_agents(self) -> None:
        d = Path(tempfile.mkdtemp())
        registry = {
            "test-agent": {
                "name": "test-agent",
                "version": "1.0.0",
                "team": "eng",
                "framework": "langgraph",
                "status": "running",
                "endpoint_url": "http://localhost:8080",
            }
        }
        (d / "agents.json").write_text(json.dumps(registry))
        with patch("cli.commands.list_cmd.REGISTRY_DIR", d):
            result = runner.invoke(app, ["list", "agents"])
        assert result.exit_code == 0
        assert "test-agent" in result.output

    def test_list_json_output(self) -> None:
        d = Path(tempfile.mkdtemp())
        registry = {
            "my-agent": {
                "name": "my-agent",
                "version": "1.0.0",
                "team": "eng",
                "framework": "langgraph",
                "status": "running",
                "endpoint_url": "http://localhost:8080",
            }
        }
        (d / "agents.json").write_text(json.dumps(registry))
        with patch("cli.commands.list_cmd.REGISTRY_DIR", d):
            result = runner.invoke(app, ["list", "agents", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert len(output) == 1
        assert output[0]["name"] == "my-agent"

    def test_list_filter_by_team(self) -> None:
        d = Path(tempfile.mkdtemp())
        registry = {
            "agent-a": {
                "name": "agent-a",
                "team": "alpha",
                "version": "1.0.0",
                "framework": "langgraph",
                "status": "running",
                "endpoint_url": "http://localhost:8080",
            },
            "agent-b": {
                "name": "agent-b",
                "team": "beta",
                "version": "1.0.0",
                "framework": "langgraph",
                "status": "running",
                "endpoint_url": "http://localhost:8081",
            },
        }
        (d / "agents.json").write_text(json.dumps(registry))
        with patch("cli.commands.list_cmd.REGISTRY_DIR", d):
            result = runner.invoke(app, ["list", "agents", "--team", "alpha", "--json"])
        output = json.loads(result.output)
        assert len(output) == 1
        assert output[0]["name"] == "agent-a"

    def test_list_unsupported_entity(self) -> None:
        result = runner.invoke(app, ["list", "prompts"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output

    def test_list_no_agents_json(self) -> None:
        with patch("cli.commands.list_cmd.REGISTRY_DIR", Path(tempfile.mkdtemp())):
            result = runner.invoke(app, ["list", "agents", "--json"])
        assert result.exit_code == 0
        assert "[]" in result.output


class TestDescribeCommand:
    def test_describe_existing_agent(self) -> None:
        d = Path(tempfile.mkdtemp())
        registry = {
            "my-agent": {
                "name": "my-agent",
                "version": "1.0.0",
                "team": "eng",
                "framework": "langgraph",
                "endpoint_url": "http://localhost:8080",
            }
        }
        (d / "agents.json").write_text(json.dumps(registry))
        with patch("cli.commands.describe.REGISTRY_DIR", d):
            result = runner.invoke(app, ["describe", "my-agent"])
        assert result.exit_code == 0
        assert "my-agent" in result.output
        assert "langgraph" in result.output

    def test_describe_not_found(self) -> None:
        d = Path(tempfile.mkdtemp())
        (d / "agents.json").write_text(json.dumps({"other": {"name": "other"}}))
        with patch("cli.commands.describe.REGISTRY_DIR", d):
            result = runner.invoke(app, ["describe", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_describe_no_registry(self) -> None:
        with patch("cli.commands.describe.REGISTRY_DIR", Path(tempfile.mkdtemp())):
            result = runner.invoke(app, ["describe", "anything"])
        assert result.exit_code == 1

    def test_describe_json_output(self) -> None:
        d = Path(tempfile.mkdtemp())
        registry = {
            "my-agent": {
                "name": "my-agent",
                "version": "1.0.0",
                "team": "eng",
            }
        }
        (d / "agents.json").write_text(json.dumps(registry))
        with patch("cli.commands.describe.REGISTRY_DIR", d):
            result = runner.invoke(app, ["describe", "my-agent", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["name"] == "my-agent"


class TestDeployCommand:
    def test_deploy_invalid_yaml_fails(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("name: test-agent\n")
        f.close()
        result = runner.invoke(app, ["deploy", f.name])
        assert result.exit_code == 1
        assert "failed" in result.output.lower() or "Error" in result.output

    def test_deploy_json_output_on_error(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("name: test-agent\n")
        f.close()
        result = runner.invoke(app, ["deploy", f.name, "--json"])
        assert result.exit_code == 1
        # Find the JSON line in the output
        for line in result.output.strip().splitlines():
            try:
                output = json.loads(line)
                assert "error" in output
                return
            except json.JSONDecodeError:
                continue
        # If we didn't find valid JSON, at least verify error indication
        assert "error" in result.output.lower() or "failed" in result.output.lower()


# ── registry memory / rag push ─────────────────────────────────────────────


class TestRegistryMemoryPush:
    """Covers the new `agentbreeder registry memory push` CLI command (#373).

    These tests pin the YAML → API body translation and verify the HTTP call
    is made with the mapped fields. The wider memory.yaml schema (which has
    `backend` + `config.window_size`) does NOT match the API request shape
    (`backend_type` + `max_messages`); the mapping is centralised in
    `_memory_yaml_to_api_body` so it can be unit-tested without HTTP.
    """

    def test_yaml_to_api_body_maps_buffer_window(self) -> None:
        from cli.commands.registry_cmd import _memory_yaml_to_api_body

        body = _memory_yaml_to_api_body(
            {
                "name": "my-memory",
                "team": "engineering",
                "owner": "alice@example.com",
                "backend": "postgresql",
                "memory_type": "buffer_window",
                "tags": ["postgres", "buffer-window"],
                "description": "test memory",
                "config": {"window_size": 20},
            }
        )
        assert body == {
            "name": "my-memory",
            "team": "engineering",
            "owner": "alice@example.com",
            "backend_type": "postgresql",
            "memory_type": "buffer_window",
            "max_messages": 20,
            "description": "test memory",
            "tags": ["postgres", "buffer-window"],
        }

    def test_yaml_to_api_body_applies_defaults(self) -> None:
        from cli.commands.registry_cmd import _memory_yaml_to_api_body

        body = _memory_yaml_to_api_body({"name": "minimal"})
        assert body["backend_type"] == "postgresql"
        assert body["memory_type"] == "buffer_window"
        assert body["max_messages"] == 100
        assert body["tags"] == []
        assert body["team"] == "default"

    def test_push_missing_file_exits_1(self) -> None:
        result = runner.invoke(app, ["registry", "memory", "push", "/nonexistent/memory.yaml"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_push_missing_name_field_exits_1(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("backend: postgresql\nmemory_type: buffer_window\n")
        f.close()
        with patch.dict("os.environ", {"AGENTBREEDER_API_TOKEN": "dummy"}):
            result = runner.invoke(app, ["registry", "memory", "push", f.name])
        assert result.exit_code == 1
        assert "name" in result.output.lower()

    def test_push_posts_mapped_body(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(
            "name: my-memory\n"
            "team: eng\n"
            "owner: alice@example.com\n"
            "backend: postgresql\n"
            "memory_type: buffer_window\n"
            "tags: [a, b]\n"
            "config:\n"
            "  window_size: 50\n"
        )
        f.close()

        captured: dict = {}

        def fake_post(path: str, body: dict) -> dict:
            captured["path"] = path
            captured["body"] = body
            return {
                "data": {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "name": body["name"],
                    "backend_type": body["backend_type"],
                    "memory_type": body["memory_type"],
                }
            }

        with patch("cli.commands.registry_cmd._post", side_effect=fake_post):
            result = runner.invoke(app, ["registry", "memory", "push", f.name])

        assert result.exit_code == 0, result.output
        assert captured["path"] == "/api/v1/memory/configs"
        assert captured["body"]["backend_type"] == "postgresql"
        assert captured["body"]["max_messages"] == 50
        assert captured["body"]["tags"] == ["a", "b"]


class TestRegistryRagPush:
    """Covers the new `agentbreeder registry rag push` CLI command (#373).

    rag.yaml uses nested ``embedding_model: {provider, name}`` and
    ``chunking: {strategy, chunk_size, chunk_overlap}`` blocks, but
    ``POST /api/v1/rag/indexes`` wants a flat body with a slash-joined
    ``embedding_model`` string and top-level chunk fields.
    """

    def test_yaml_to_api_body_flattens_nested_blocks(self) -> None:
        from cli.commands.registry_cmd import _rag_yaml_to_api_body

        body = _rag_yaml_to_api_body(
            {
                "name": "docs-index",
                "description": "docs RAG",
                "backend": "in_memory",
                "embedding_model": {
                    "provider": "openai",
                    "name": "text-embedding-3-small",
                },
                "chunking": {
                    "strategy": "recursive",
                    "chunk_size": 1024,
                    "chunk_overlap": 128,
                },
                "source": "manual",
                "index_type": "vector",
            }
        )
        assert body["name"] == "docs-index"
        assert body["backend"] == "in_memory"
        assert body["embedding_model"] == "openai/text-embedding-3-small"
        assert body["chunk_strategy"] == "recursive"
        assert body["chunk_size"] == 1024
        assert body["chunk_overlap"] == 128
        assert body["index_type"] == "vector"

    def test_yaml_to_api_body_applies_defaults(self) -> None:
        from cli.commands.registry_cmd import _rag_yaml_to_api_body

        body = _rag_yaml_to_api_body({"name": "minimal"})
        assert body["backend"] == "in_memory"
        assert body["embedding_model"] == "openai/text-embedding-3-small"
        assert body["chunk_strategy"] == "fixed_size"
        assert body["chunk_size"] == 512
        assert body["chunk_overlap"] == 64
        assert body["index_type"] == "vector"

    def test_push_missing_file_exits_1(self) -> None:
        result = runner.invoke(app, ["registry", "rag", "push", "/nonexistent/rag.yaml"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_push_posts_mapped_body(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(
            "name: docs-index\n"
            "version: 1.0.0\n"
            "description: docs RAG\n"
            "backend: in_memory\n"
            "embedding_model:\n"
            "  provider: openai\n"
            "  name: text-embedding-3-small\n"
            "chunking:\n"
            "  strategy: recursive\n"
            "  chunk_size: 1024\n"
            "  chunk_overlap: 128\n"
        )
        f.close()

        captured: dict = {}

        def fake_post(path: str, body: dict) -> dict:
            captured["path"] = path
            captured["body"] = body
            return {
                "data": {
                    "id": "00000000-0000-0000-0000-000000000002",
                    "name": body["name"],
                    "backend": body["backend"],
                    "index_type": body["index_type"],
                }
            }

        with patch("cli.commands.registry_cmd._post", side_effect=fake_post):
            result = runner.invoke(app, ["registry", "rag", "push", f.name])

        assert result.exit_code == 0, result.output
        assert captured["path"] == "/api/v1/rag/indexes"
        assert captured["body"]["embedding_model"] == "openai/text-embedding-3-small"
        assert captured["body"]["chunk_size"] == 1024
        assert captured["body"]["chunk_strategy"] == "recursive"


class TestRegistryRagIngest:
    """Covers `agentbreeder registry rag ingest NAME FILE...`."""

    def test_resolve_id_passthrough_for_uuid(self) -> None:
        from cli.commands.registry_cmd import _resolve_rag_index_id

        uid = "11111111-2222-3333-4444-555555555555"
        assert _resolve_rag_index_id(uid) == uid

    def test_resolve_id_looks_up_name(self) -> None:
        from cli.commands import registry_cmd

        with patch.object(
            registry_cmd,
            "_get",
            return_value={
                "data": [
                    {"id": "aaa", "name": "other"},
                    {"id": "bbb", "name": "docs-index"},
                ]
            },
        ):
            assert registry_cmd._resolve_rag_index_id("docs-index") == "bbb"

    def test_ingest_missing_file_exits_1(self) -> None:
        result = runner.invoke(
            app,
            ["registry", "rag", "ingest", "docs-index", "/nonexistent/file.md"],
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_ingest_unsupported_extension_exits_1(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".exe", delete=False)
        f.write("nope")
        f.close()
        result = runner.invoke(app, ["registry", "rag", "ingest", "docs-index", f.name])
        assert result.exit_code == 1
        assert "unsupported" in result.output.lower()

    def test_ingest_posts_multipart(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
        f.write("# hello world\nSome body.")
        f.close()

        captured: dict = {}

        def fake_multipart(path: str, files: list) -> dict:
            captured["path"] = path
            captured["files"] = files
            return {
                "data": {
                    "id": "job-1234-5678-9abc-def0",
                    "status": "completed",
                    "total_files": 1,
                    "processed_files": 1,
                    "total_chunks": 3,
                    "embedded_chunks": 3,
                }
            }

        from cli.commands import registry_cmd

        with patch.object(registry_cmd, "_resolve_rag_index_id", return_value="abc-id"):
            with patch.object(registry_cmd, "_post_multipart", side_effect=fake_multipart):
                result = runner.invoke(app, ["registry", "rag", "ingest", "docs-index", f.name])

        assert result.exit_code == 0, result.output
        assert captured["path"] == "/api/v1/rag/indexes/abc-id/ingest"
        assert len(captured["files"]) == 1
        field, (filename, content, ctype) = captured["files"][0]
        assert field == "files"
        assert filename == Path(f.name).name
        assert ctype == "text/markdown"
        assert b"hello world" in content
        assert "Ingested 3 chunks" in result.output


class TestRegistryRagSearch:
    """Covers `agentbreeder registry rag search NAME --query ...`."""

    def test_search_posts_body(self) -> None:
        from cli.commands import registry_cmd

        captured: dict = {}

        def fake_post(path: str, body: dict) -> dict:
            captured["path"] = path
            captured["body"] = body
            return {
                "data": {
                    "results": [
                        {
                            "score": 0.91,
                            "source": "docs/intro.md",
                            "text": "AgentBreeder is an OSS agent platform.",
                        }
                    ]
                }
            }

        with patch.object(registry_cmd, "_resolve_rag_index_id", return_value="idx-1"):
            with patch.object(registry_cmd, "_post", side_effect=fake_post):
                result = runner.invoke(
                    app,
                    ["registry", "rag", "search", "docs", "--query", "what is it", "-k", "3"],
                )

        assert result.exit_code == 0, result.output
        assert captured["path"] == "/api/v1/rag/search"
        assert captured["body"] == {"index_id": "idx-1", "query": "what is it", "top_k": 3}
        assert "docs/intro.md" in result.output

    def test_search_json_output(self) -> None:
        from cli.commands import registry_cmd

        with patch.object(registry_cmd, "_resolve_rag_index_id", return_value="idx-1"):
            with patch.object(
                registry_cmd,
                "_post",
                return_value={"data": {"results": []}, "meta": {"total": 0}},
            ):
                result = runner.invoke(
                    app,
                    ["registry", "rag", "search", "docs", "--query", "q", "--json"],
                )
        assert result.exit_code == 0
        # The JSON output should include "results"
        assert '"results"' in result.output
