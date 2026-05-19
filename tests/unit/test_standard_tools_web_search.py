"""Unit tests for engine.tools.standard.web_search."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from engine.tools.standard.web_search import SCHEMA, web_search

# ---------------------------------------------------------------------------
# SCHEMA shape
# ---------------------------------------------------------------------------


class TestWebSearchSchema:
    def test_schema_is_object_type(self) -> None:
        assert SCHEMA["type"] == "object"

    def test_schema_requires_query(self) -> None:
        assert "query" in SCHEMA["required"]

    def test_schema_has_max_results_with_bounds(self) -> None:
        max_r = SCHEMA["properties"]["max_results"]
        assert max_r["type"] == "integer"
        assert max_r["minimum"] == 1
        assert max_r["maximum"] == 10

    def test_schema_search_depth_is_enum(self) -> None:
        depth = SCHEMA["properties"]["search_depth"]
        assert depth["type"] == "string"
        assert set(depth["enum"]) == {"basic", "advanced"}


# ---------------------------------------------------------------------------
# Missing API key
# ---------------------------------------------------------------------------


class TestWebSearchMissingApiKey:
    def test_raises_runtime_error_when_key_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
            web_search("python type hints")

    def test_raises_when_key_is_empty_string(self, monkeypatch) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "")
        with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
            web_search("python type hints")


# ---------------------------------------------------------------------------
# API timeout
# ---------------------------------------------------------------------------


class TestWebSearchTimeout:
    def test_propagates_timeout_exception(self, monkeypatch) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "fake-key")

        client_cm = MagicMock()
        client = MagicMock()
        client.post.side_effect = httpx.TimeoutException("connect timed out")
        client_cm.__enter__.return_value = client
        client_cm.__exit__.return_value = None

        with patch("engine.tools.standard.web_search.httpx.Client", return_value=client_cm):
            with pytest.raises(httpx.TimeoutException):
                web_search("anything")


# ---------------------------------------------------------------------------
# API error response (non-2xx)
# ---------------------------------------------------------------------------


class TestWebSearchApiError:
    def test_raises_on_http_error(self, monkeypatch) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "fake-key")

        resp = MagicMock()
        resp.status_code = 500
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Server Error",
            request=MagicMock(),
            response=resp,
        )

        client_cm = MagicMock()
        client = MagicMock()
        client.post.return_value = resp
        client_cm.__enter__.return_value = client
        client_cm.__exit__.return_value = None

        with patch("engine.tools.standard.web_search.httpx.Client", return_value=client_cm):
            with pytest.raises(httpx.HTTPStatusError):
                web_search("x")

    def test_raises_on_401_unauthorized(self, monkeypatch) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "wrong-key")

        resp = MagicMock()
        resp.status_code = 401
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized",
            request=MagicMock(),
            response=resp,
        )

        client_cm = MagicMock()
        client = MagicMock()
        client.post.return_value = resp
        client_cm.__enter__.return_value = client
        client_cm.__exit__.return_value = None

        with patch("engine.tools.standard.web_search.httpx.Client", return_value=client_cm):
            with pytest.raises(httpx.HTTPStatusError):
                web_search("x")


# ---------------------------------------------------------------------------
# Malformed response
# ---------------------------------------------------------------------------


class TestWebSearchMalformedResponse:
    def test_handles_missing_results_key_gracefully(self, monkeypatch) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "fake-key")

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"answer": "no results"}  # No "results" key

        client_cm = MagicMock()
        client = MagicMock()
        client.post.return_value = resp
        client_cm.__enter__.return_value = client
        client_cm.__exit__.return_value = None

        with patch("engine.tools.standard.web_search.httpx.Client", return_value=client_cm):
            result = web_search("anything")

        assert result["sources"] == []
        assert result["answer"] == "no results"
        assert result["query"] == "anything"

    def test_handles_empty_json_dict(self, monkeypatch) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "fake-key")

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = {}

        client_cm = MagicMock()
        client = MagicMock()
        client.post.return_value = resp
        client_cm.__enter__.return_value = client
        client_cm.__exit__.return_value = None

        with patch("engine.tools.standard.web_search.httpx.Client", return_value=client_cm):
            result = web_search("anything")

        assert result["sources"] == []
        assert result["answer"] == ""

    def test_handles_individual_result_missing_fields(self, monkeypatch) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "fake-key")

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "answer": "ok",
            "results": [
                {"url": "https://a.com", "title": "A"},  # no content/score
                {},  # entirely empty
            ],
        }

        client_cm = MagicMock()
        client = MagicMock()
        client.post.return_value = resp
        client_cm.__enter__.return_value = client
        client_cm.__exit__.return_value = None

        with patch("engine.tools.standard.web_search.httpx.Client", return_value=client_cm):
            result = web_search("x")

        assert len(result["sources"]) == 2
        assert result["sources"][0]["url"] == "https://a.com"
        assert result["sources"][0]["title"] == "A"
        assert result["sources"][0]["snippet"] == ""
        assert result["sources"][0]["score"] is None
        assert result["sources"][1]["url"] == ""


# ---------------------------------------------------------------------------
# Valid response shape
# ---------------------------------------------------------------------------


class TestWebSearchValidResponse:
    def test_returns_expected_shape(self, monkeypatch) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "fake-key")

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "answer": "Python type hints add static typing.",
            "results": [
                {
                    "url": "https://docs.python.org/3/library/typing.html",
                    "title": "typing — Support for type hints",
                    "content": "This module provides runtime support for type hints.",
                    "score": 0.93,
                },
                {
                    "url": "https://realpython.com/python-type-checking/",
                    "title": "Python Type Checking Guide",
                    "content": "Practical introduction to typing.",
                    "score": 0.87,
                },
            ],
        }

        client_cm = MagicMock()
        client = MagicMock()
        client.post.return_value = resp
        client_cm.__enter__.return_value = client
        client_cm.__exit__.return_value = None

        with patch("engine.tools.standard.web_search.httpx.Client", return_value=client_cm):
            result = web_search("python type hints")

        assert result["query"] == "python type hints"
        assert result["answer"] == "Python type hints add static typing."
        assert len(result["sources"]) == 2
        first = result["sources"][0]
        assert first["url"].startswith("https://")
        assert first["title"]
        assert first["snippet"]
        assert first["score"] == 0.93

    def test_max_results_is_clamped(self, monkeypatch) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "fake-key")

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"answer": "", "results": []}

        client_cm = MagicMock()
        client = MagicMock()
        client.post.return_value = resp
        client_cm.__enter__.return_value = client
        client_cm.__exit__.return_value = None

        with patch("engine.tools.standard.web_search.httpx.Client", return_value=client_cm):
            web_search("x", max_results=999)

        # Payload sent should be clamped to 10
        sent_payload = client.post.call_args.kwargs["json"]
        assert sent_payload["max_results"] == 10

    def test_max_results_clamped_to_minimum(self, monkeypatch) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "fake-key")

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"answer": "", "results": []}

        client_cm = MagicMock()
        client = MagicMock()
        client.post.return_value = resp
        client_cm.__enter__.return_value = client
        client_cm.__exit__.return_value = None

        with patch("engine.tools.standard.web_search.httpx.Client", return_value=client_cm):
            web_search("x", max_results=-5)

        sent_payload = client.post.call_args.kwargs["json"]
        assert sent_payload["max_results"] == 1
