"""Tests for the POST /api/v1/prompts/test endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


class TestPromptTestEndpoint:
    """Test the prompt test/preview endpoint."""

    def test_basic_prompt_test(self) -> None:
        resp = client.post(
            "/api/v1/prompts/test",
            json={
                "prompt_text": "Hello, you are a helpful assistant.",
                "variables": {},
                "temperature": 0.7,
                "max_tokens": 1024,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "response_text" in data
        assert len(data["response_text"]) > 0
        assert "rendered_prompt" in data
        assert data["rendered_prompt"] == "Hello, you are a helpful assistant."
        assert data["input_tokens"] > 0
        assert data["output_tokens"] > 0
        assert data["total_tokens"] == data["input_tokens"] + data["output_tokens"]
        assert data["latency_ms"] > 0
        assert data["temperature"] == 0.7

    def test_prompt_test_with_variables(self) -> None:
        resp = client.post(
            "/api/v1/prompts/test",
            json={
                "prompt_text": "Hello {{name}}, you work in {{department}}.",
                "variables": {"name": "Alice", "department": "Engineering"},
                "temperature": 0.5,
                "max_tokens": 512,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["rendered_prompt"] == "Hello Alice, you work in Engineering."
        assert "{{name}}" not in data["rendered_prompt"]
        assert "{{department}}" not in data["rendered_prompt"]

    def test_prompt_test_partial_variables(self) -> None:
        """Variables not provided should remain as placeholders."""
        resp = client.post(
            "/api/v1/prompts/test",
            json={
                "prompt_text": "Hello {{name}}, you work in {{department}}.",
                "variables": {"name": "Bob"},
                "temperature": 0.7,
                "max_tokens": 1024,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "Bob" in data["rendered_prompt"]
        assert "{{department}}" in data["rendered_prompt"]

    def test_prompt_test_with_model_name(self) -> None:
        resp = client.post(
            "/api/v1/prompts/test",
            json={
                "prompt_text": "Test prompt",
                "model_name": "GPT-4o",
                "variables": {},
                "temperature": 0.7,
                "max_tokens": 1024,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["model_name"] == "GPT-4o"

    def test_prompt_test_support_context(self) -> None:
        """Prompts with support keywords should get support-themed responses."""
        resp = client.post(
            "/api/v1/prompts/test",
            json={
                "prompt_text": "You are a customer support agent.",
                "variables": {},
                "temperature": 0.7,
                "max_tokens": 1024,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["response_text"]) > 0

    def test_prompt_test_analysis_context(self) -> None:
        """Prompts with analysis keywords get analysis-themed responses."""
        resp = client.post(
            "/api/v1/prompts/test",
            json={
                "prompt_text": "Analyze the following data and produce a report.",
                "variables": {},
                "temperature": 0.7,
                "max_tokens": 1024,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["response_text"]) > 0

    def test_prompt_test_default_values(self) -> None:
        """Temperature and max_tokens should have defaults."""
        resp = client.post(
            "/api/v1/prompts/test",
            json={
                "prompt_text": "Simple test.",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["temperature"] == 0.7

    def test_prompt_test_empty_prompt(self) -> None:
        """Empty prompt should still return a valid response."""
        resp = client.post(
            "/api/v1/prompts/test",
            json={
                "prompt_text": "",
                "variables": {},
                "temperature": 0.7,
                "max_tokens": 1024,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["rendered_prompt"] == ""
