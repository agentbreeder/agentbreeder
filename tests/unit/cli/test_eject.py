"""Unit tests for ``agentbreeder eject`` (issue #560, bug #8).

Covers the four flows the bug-fix promised:

* ``--to yaml`` writes a clean agent.yaml fetched from the registry
* ``--to code`` also scaffolds agent.py, requirements.txt, README.md
* Agent not found in the registry -> clear error, exit 1
* Output dir already populated -> exit 1 unless --force is passed
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import yaml
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


# ── Fakes ──────────────────────────────────────────────────────────────────


def _agent_record(name: str = "my-agent", framework: str = "langgraph") -> dict[str, Any]:
    """Build an API response row that includes a realistic config_snapshot."""
    return {
        "id": "00000000-0000-0000-0000-000000000042",
        "name": name,
        "version": "1.2.3",
        "description": "An agent ejected for testing.",
        "team": "engineering",
        "owner": "alice@example.com",
        "framework": framework,
        "model_primary": "gpt-4o",
        "model_fallback": None,
        "endpoint_url": "https://will-be-stripped.example.com",
        "status": "running",
        "tags": ["test", "ejectable"],
        "config_snapshot": {
            "name": name,
            "version": "1.2.3",
            "description": "An agent ejected for testing.",
            "team": "engineering",
            "owner": "alice@example.com",
            "tags": ["test", "ejectable"],
            "framework": framework,
            "model": {"primary": "gpt-4o"},
            "deploy": {"cloud": "local"},
        },
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
    }


def _ok(agents: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": agents,
            "meta": {"page": 1, "per_page": 100, "total": len(agents)},
        },
    )


def _patched_httpx(response: httpx.Response):
    """Patch ``cli.commands.eject.httpx.Client`` to yield a stub client."""

    class _Stub:
        def __enter__(self) -> _Stub:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def get(self, *_a: Any, **_kw: Any) -> httpx.Response:
            return response

    return patch("cli.commands.eject.httpx.Client", lambda *_a, **_kw: _Stub())


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Drop user tokens / keychain access; force CWD into tmp_path so default
    output dirs don't pollute the workspace."""
    monkeypatch.setenv("AGENTBREEDER_API_TOKEN", "test-token")
    monkeypatch.delenv("AGENTBREEDER_URL", raising=False)
    monkeypatch.delenv("AGENTBREEDER_API_URL", raising=False)
    monkeypatch.chdir(tmp_path)


# ── Tests ──────────────────────────────────────────────────────────────────


class TestEjectToYaml:
    def test_writes_agent_yaml(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "my-agent-yaml"
        with _patched_httpx(_ok([_agent_record()])):
            result = runner.invoke(
                app,
                ["eject", "my-agent", "--to", "yaml", "--output-dir", str(out_dir)],
            )
        assert result.exit_code == 0, result.stdout + result.stderr
        agent_yaml = out_dir / "agent.yaml"
        assert agent_yaml.exists(), "agent.yaml was not written"

        parsed = yaml.safe_load(agent_yaml.read_text())
        assert parsed["name"] == "my-agent"
        assert parsed["framework"] == "langgraph"
        assert parsed["model"]["primary"] == "gpt-4o"
        assert parsed["deploy"]["cloud"] == "local"
        # Ephemeral / server-managed fields must be stripped:
        assert "id" not in parsed
        assert "endpoint_url" not in parsed
        assert "status" not in parsed
        assert "created_at" not in parsed
        # No code scaffolding should appear in --to yaml mode.
        assert not (out_dir / "agent.py").exists()
        assert not (out_dir / "requirements.txt").exists()


class TestEjectToCode:
    def test_writes_full_scaffold_for_langgraph(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "my-agent-code"
        with _patched_httpx(_ok([_agent_record(framework="langgraph")])):
            result = runner.invoke(
                app,
                ["eject", "my-agent", "--to", "code", "--output-dir", str(out_dir)],
            )
        assert result.exit_code == 0, result.stdout + result.stderr
        for filename in ("agent.yaml", "agent.py", "requirements.txt", "README.md"):
            assert (out_dir / filename).exists(), f"missing scaffold file: {filename}"
        # The agent.py template must reference the langgraph imports we expect.
        agent_py = (out_dir / "agent.py").read_text()
        assert "langgraph" in agent_py.lower()
        # Requirements should include the framework package.
        reqs = (out_dir / "requirements.txt").read_text()
        assert "langgraph" in reqs


class TestEjectAgentNotFound:
    def test_exits_1_with_clear_message(self) -> None:
        with _patched_httpx(_ok([_agent_record(name="other-agent")])):
            result = runner.invoke(app, ["eject", "missing-agent", "--to", "yaml"])
        assert result.exit_code == 1
        # Either stdout or stderr; rich routes errors through stdout by default.
        combined = result.stdout + (result.stderr or "")
        assert "missing-agent" in combined
        assert "not found" in combined.lower()


class TestEjectDirExists:
    def test_exits_1_when_output_dir_populated(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "occupied"
        out_dir.mkdir()
        (out_dir / "preexisting.txt").write_text("don't clobber me")

        with _patched_httpx(_ok([_agent_record()])):
            result = runner.invoke(
                app,
                ["eject", "my-agent", "--to", "yaml", "--output-dir", str(out_dir)],
            )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "already exists" in combined.lower() or "--force" in combined

        # The pre-existing file is intact and no agent.yaml was written.
        assert (out_dir / "preexisting.txt").read_text() == "don't clobber me"
        assert not (out_dir / "agent.yaml").exists()

    def test_force_overwrites(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "occupied-force"
        out_dir.mkdir()
        (out_dir / "stale.txt").write_text("ok")

        with _patched_httpx(_ok([_agent_record()])):
            result = runner.invoke(
                app,
                [
                    "eject",
                    "my-agent",
                    "--to",
                    "yaml",
                    "--output-dir",
                    str(out_dir),
                    "--force",
                ],
            )
        assert result.exit_code == 0, result.stdout + result.stderr
        assert (out_dir / "agent.yaml").exists()
