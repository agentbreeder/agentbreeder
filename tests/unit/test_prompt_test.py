"""Tests for the POST /api/v1/prompts/test endpoint."""

from __future__ import annotations

import pytest
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


class TestPromptTestValidation:
    """Validation tests for ``PromptTestRequest`` Field bounds (W4-15)."""

    def test_temperature_below_zero_rejected(self) -> None:
        resp = client.post(
            "/api/v1/prompts/test",
            json={
                "prompt_text": "x",
                "variables": {},
                "temperature": -0.1,
                "max_tokens": 1024,
            },
        )
        assert resp.status_code == 422

    def test_temperature_above_two_rejected(self) -> None:
        resp = client.post(
            "/api/v1/prompts/test",
            json={
                "prompt_text": "x",
                "variables": {},
                "temperature": 2.1,
                "max_tokens": 1024,
            },
        )
        assert resp.status_code == 422

    def test_max_tokens_zero_rejected(self) -> None:
        resp = client.post(
            "/api/v1/prompts/test",
            json={
                "prompt_text": "x",
                "variables": {},
                "temperature": 0.7,
                "max_tokens": 0,
            },
        )
        assert resp.status_code == 422

    def test_max_tokens_too_large_rejected(self) -> None:
        resp = client.post(
            "/api/v1/prompts/test",
            json={
                "prompt_text": "x",
                "variables": {},
                "temperature": 0.7,
                "max_tokens": 1_000_000,
            },
        )
        assert resp.status_code == 422


@pytest.mark.no_auto_auth
class TestPromptTestAuth:
    """Authentication tests for the prompt-test endpoint (W4-16).

    The global ``_auto_auth`` fixture in ``tests/unit/conftest.py`` patches
    JWT auth for all API tests. The ``@no_auto_auth`` marker opts this class
    out so we can verify that the endpoint actually returns 401 when no
    ``Authorization`` header is present.
    """

    def test_returns_401_when_no_auth_header(self) -> None:
        resp = client.post(
            "/api/v1/prompts/test",
            json={
                "prompt_text": "Hello.",
                "variables": {},
                "temperature": 0.7,
                "max_tokens": 1024,
            },
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body.get("detail") == "Not authenticated"


class TestPromptACLWiring:
    """Regression tests for ACL wiring (W4-18).

    ``_enforce_acl`` is currently orphaned in ``api/routes/prompts.py`` because
    the full CRUD endpoints (PUT/DELETE /{id}) are not yet implemented. This
    test guards the helper's existence + signature so that when CRUD lands,
    the wiring contract is enforced.
    """

    def test_enforce_acl_is_importable(self) -> None:
        from api.routes.prompts import _enforce_acl

        assert callable(_enforce_acl)

    def test_enforce_acl_signature(self) -> None:
        import inspect

        from api.routes.prompts import _enforce_acl

        sig = inspect.signature(_enforce_acl)
        params = list(sig.parameters.keys())
        # Contract: (db, user_email, resource_id, action)
        assert params == ["db", "user_email", "resource_id", "action"]

    def test_no_unprotected_mutating_routes_registered(self) -> None:
        """Any future PUT/DELETE /api/v1/prompts/{id} route must reference _enforce_acl.

        This regression test imports the router and inspects all registered
        mutating routes. If a CRUD endpoint is added without a corresponding
        call to ``_enforce_acl`` in its handler source, this test fails fast.
        """
        import inspect

        from api.routes.prompts import router

        mutating_methods = {"PUT", "DELETE", "PATCH"}
        for route in router.routes:
            methods = getattr(route, "methods", None) or set()
            path = getattr(route, "path", "")
            # We only audit per-resource mutating routes (those with a path param).
            if not (methods & mutating_methods):
                continue
            if "{" not in path:
                continue
            endpoint = getattr(route, "endpoint", None)
            assert endpoint is not None, f"Route {path} has no endpoint"
            src = inspect.getsource(endpoint)
            assert "_enforce_acl" in src, (
                f"Mutating prompt route {path} (methods={methods}) does not call "
                "_enforce_acl(); add it before merging."
            )
