"""Unit tests for ``engine.prompt_resolver.resolve_prompt``.

Covers:
- Local file resolution (hit / miss / wrong extension)
- Registry API resolution (success / 404 / timeout / malformed JSON / auth failure)
- Inline fallback (when value is not a registry ref)
- Version pinning (``prompts/<name>@1.0.0``)
- ``PromptNotFoundError`` raised with detailed error when all paths fail
- Semver-aware sorting in registry matches (``1.10.0`` > ``1.9.0``)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from engine.prompt_resolver import (
    PromptNotFoundError,
    _semver_key,
    clear_registry_cache,
    is_prompt_ref,
    resolve_prompt,
)


@pytest.fixture(autouse=True)
def _clear_resolver_cache() -> None:
    """Reset the LRU cache between tests so each scenario starts clean."""
    clear_registry_cache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockHTTPResponse:
    """Tiny stand-in for ``httpx.Response`` used by fake transports."""

    def __init__(self, status_code: int, json_body: Any | None = None) -> None:
        self.status_code = status_code
        self._json_body = json_body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "http://fake"),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> Any:
        if isinstance(self._json_body, Exception):
            raise self._json_body
        return self._json_body


class _FakeClient:
    """Drop-in replacement for ``httpx.Client`` that returns a canned response."""

    def __init__(self, response: _MockHTTPResponse | Exception) -> None:
        self._response = response
        self.last_url: str | None = None
        self.last_headers: dict[str, str] | None = None
        self.last_params: dict[str, str] | None = None

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> _MockHTTPResponse:
        self.last_url = url
        self.last_headers = headers
        self.last_params = params
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _patch_httpx_client(response: _MockHTTPResponse | Exception) -> Any:
    """Patch ``httpx.Client`` to return our fake."""

    def _factory(*args: object, **kwargs: object) -> _FakeClient:
        return _FakeClient(response)

    return patch("engine.prompt_resolver.httpx.Client", side_effect=_factory)


# ---------------------------------------------------------------------------
# is_prompt_ref
# ---------------------------------------------------------------------------


class TestIsPromptRef:
    def test_recognises_simple_ref(self) -> None:
        assert is_prompt_ref("prompts/hello") is True

    def test_recognises_versioned_ref(self) -> None:
        assert is_prompt_ref("prompts/hello@1.2.3") is True

    def test_rejects_inline_prompt(self) -> None:
        assert is_prompt_ref("You are a helpful assistant.") is False

    def test_rejects_multiline_value(self) -> None:
        # Multi-line content is treated as inline, never a ref.
        assert is_prompt_ref("prompts/x\nactually inline") is False

    def test_rejects_oversize_value(self) -> None:
        assert is_prompt_ref("prompts/" + "x" * 300) is False


# ---------------------------------------------------------------------------
# Local file resolution
# ---------------------------------------------------------------------------


class TestLocalFileResolution:
    def test_resolves_from_local_file_when_present(self, tmp_path: Path) -> None:
        (tmp_path / "prompts").mkdir()
        prompt_path = tmp_path / "prompts" / "support.md"
        prompt_path.write_text("You are a support agent.", encoding="utf-8")

        result = resolve_prompt("prompts/support", project_root=tmp_path)

        assert result == "You are a support agent."

    def test_missing_file_falls_back_to_registry(self, tmp_path: Path) -> None:
        # No prompts/ dir present.
        with (
            patch.dict(
                "os.environ",
                {"AGENTBREEDER_REGISTRY_URL": "http://registry.local"},
            ),
            _patch_httpx_client(
                _MockHTTPResponse(
                    200,
                    {
                        "data": [
                            {"name": "fallback", "version": "1.0.0", "content": "from-api"},
                        ]
                    },
                )
            ),
        ):
            result = resolve_prompt("prompts/fallback", project_root=tmp_path)
        assert result == "from-api"

    def test_wrong_extension_is_ignored(self, tmp_path: Path) -> None:
        """Unsupported extensions are NOT matched (only .md/.txt/.prompt are)."""
        (tmp_path / "prompts").mkdir()
        # ``.yaml`` is not in _PROMPT_FILE_EXTENSIONS, so this file should be
        # ignored.
        (tmp_path / "prompts" / "foo.yaml").write_text("ignored", encoding="utf-8")

        # No registry configured -> should raise.
        with patch.dict("os.environ", {"AGENTBREEDER_REGISTRY_URL": ""}, clear=False):
            with pytest.raises(PromptNotFoundError):
                resolve_prompt("prompts/foo", project_root=tmp_path)

    def test_resolves_from_txt_file(self, tmp_path: Path) -> None:
        """``.txt`` is an accepted prompt file extension (P9)."""
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "plain.txt").write_text("plain-text prompt body", encoding="utf-8")
        result = resolve_prompt("prompts/plain", project_root=tmp_path)
        assert result == "plain-text prompt body"

    def test_resolves_from_prompt_file(self, tmp_path: Path) -> None:
        """``.prompt`` is an accepted prompt file extension (P9)."""
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "custom.prompt").write_text(
            "custom-format prompt body", encoding="utf-8"
        )
        result = resolve_prompt("prompts/custom", project_root=tmp_path)
        assert result == "custom-format prompt body"

    def test_md_beats_txt_when_both_present(self, tmp_path: Path) -> None:
        """``.md`` is preferred over ``.txt`` when both exist (P9)."""
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "both.md").write_text("md-wins", encoding="utf-8")
        (tmp_path / "prompts" / "both.txt").write_text("txt-loses", encoding="utf-8")
        result = resolve_prompt("prompts/both", project_root=tmp_path)
        assert result == "md-wins"

    def test_txt_beats_prompt_when_both_present(self, tmp_path: Path) -> None:
        """Extension priority order is md > txt > prompt (P9)."""
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "tie.txt").write_text("txt-wins", encoding="utf-8")
        (tmp_path / "prompts" / "tie.prompt").write_text("prompt-loses", encoding="utf-8")
        result = resolve_prompt("prompts/tie", project_root=tmp_path)
        assert result == "txt-wins"


# ---------------------------------------------------------------------------
# Registry API resolution
# ---------------------------------------------------------------------------


class TestRegistryResolution:
    def test_registry_success(self, tmp_path: Path) -> None:
        with (
            patch.dict("os.environ", {"AGENTBREEDER_REGISTRY_URL": "http://registry.local"}),
            _patch_httpx_client(
                _MockHTTPResponse(
                    200,
                    {
                        "data": [
                            {"name": "from-api", "version": "1.0.0", "content": "hi-from-api"},
                        ]
                    },
                )
            ),
        ):
            result = resolve_prompt("prompts/from-api", project_root=tmp_path)
        assert result == "hi-from-api"

    def test_registry_404_raises_not_found(self, tmp_path: Path) -> None:
        with (
            patch.dict("os.environ", {"AGENTBREEDER_REGISTRY_URL": "http://registry.local"}),
            _patch_httpx_client(_MockHTTPResponse(404, {"data": []})),
        ):
            with pytest.raises(PromptNotFoundError) as exc_info:
                resolve_prompt("prompts/missing", project_root=tmp_path)
        # Error should mention both the local path and the registry URL.
        assert "missing.md" in str(exc_info.value)
        assert "http://registry.local" in str(exc_info.value)

    def test_registry_timeout_raises_not_found(self, tmp_path: Path) -> None:
        with (
            patch.dict("os.environ", {"AGENTBREEDER_REGISTRY_URL": "http://registry.local"}),
            _patch_httpx_client(
                httpx.TimeoutException("timed out", request=httpx.Request("GET", "http://x"))
            ),
        ):
            with pytest.raises(PromptNotFoundError):
                resolve_prompt("prompts/slow", project_root=tmp_path)

    def test_registry_malformed_json_raises_not_found(self, tmp_path: Path) -> None:
        with (
            patch.dict("os.environ", {"AGENTBREEDER_REGISTRY_URL": "http://registry.local"}),
            _patch_httpx_client(
                _MockHTTPResponse(200, ValueError("malformed JSON")),
            ),
        ):
            with pytest.raises(PromptNotFoundError):
                resolve_prompt("prompts/malformed", project_root=tmp_path)

    def test_registry_auth_failure_raises_not_found(self, tmp_path: Path) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "AGENTBREEDER_REGISTRY_URL": "http://registry.local",
                    "AGENTBREEDER_REGISTRY_TOKEN": "secret-token",
                },
            ),
            _patch_httpx_client(_MockHTTPResponse(401, {"data": []})),
        ):
            with pytest.raises(PromptNotFoundError):
                resolve_prompt("prompts/forbidden", project_root=tmp_path)

    def test_registry_unconfigured_raises_when_no_local(self, tmp_path: Path) -> None:
        """If registry URL is unset and no local file exists, raise."""
        with patch.dict("os.environ", {"AGENTBREEDER_REGISTRY_URL": ""}, clear=False):
            with pytest.raises(PromptNotFoundError):
                resolve_prompt("prompts/nowhere", project_root=tmp_path)


# ---------------------------------------------------------------------------
# Inline fallback
# ---------------------------------------------------------------------------


class TestInlineFallback:
    def test_inline_literal_returned_unchanged(self, tmp_path: Path) -> None:
        text = "You are a helpful assistant."
        assert resolve_prompt(text, project_root=tmp_path) == text

    def test_inline_multiline_returned_unchanged(self, tmp_path: Path) -> None:
        text = "Line 1\nLine 2\nLine 3"
        assert resolve_prompt(text, project_root=tmp_path) == text


# ---------------------------------------------------------------------------
# Version pinning
# ---------------------------------------------------------------------------


class TestVersionPinning:
    def test_version_pin_filters_to_exact_match(self, tmp_path: Path) -> None:
        captured: dict[str, Any] = {}

        class _Capturing(_FakeClient):
            def get(  # type: ignore[override]
                self,
                url: str,
                headers: dict[str, str] | None = None,
                params: dict[str, str] | None = None,
            ) -> _MockHTTPResponse:
                captured["params"] = params or {}
                return _MockHTTPResponse(
                    200,
                    {
                        "data": [
                            {"name": "foo", "version": "1.0.0", "content": "v1"},
                            {"name": "foo", "version": "2.0.0", "content": "v2"},
                        ]
                    },
                )

        def _factory(*_a: object, **_kw: object) -> _Capturing:
            return _Capturing(_MockHTTPResponse(200, None))

        with (
            patch.dict("os.environ", {"AGENTBREEDER_REGISTRY_URL": "http://registry.local"}),
            patch("engine.prompt_resolver.httpx.Client", side_effect=_factory),
        ):
            result = resolve_prompt("prompts/foo@1.0.0", project_root=tmp_path)

        assert result == "v1"
        assert captured["params"].get("version") == "1.0.0"
        assert captured["params"].get("name") == "foo"

    def test_no_version_pin_picks_semver_latest(self, tmp_path: Path) -> None:
        """When unpinned, semver sort must pick 1.10.0 over 1.9.0 (not lexicographic)."""
        with (
            patch.dict("os.environ", {"AGENTBREEDER_REGISTRY_URL": "http://registry.local"}),
            _patch_httpx_client(
                _MockHTTPResponse(
                    200,
                    {
                        "data": [
                            {"name": "foo", "version": "1.9.0", "content": "v1-9"},
                            {"name": "foo", "version": "1.10.0", "content": "v1-10"},
                            {"name": "foo", "version": "1.2.0", "content": "v1-2"},
                        ]
                    },
                )
            ),
        ):
            result = resolve_prompt("prompts/foo", project_root=tmp_path)
        assert result == "v1-10"

    def test_version_pin_not_found_raises(self, tmp_path: Path) -> None:
        with (
            patch.dict("os.environ", {"AGENTBREEDER_REGISTRY_URL": "http://registry.local"}),
            _patch_httpx_client(
                _MockHTTPResponse(
                    200,
                    {
                        "data": [
                            {"name": "foo", "version": "2.0.0", "content": "v2"},
                        ]
                    },
                )
            ),
        ):
            with pytest.raises(PromptNotFoundError):
                resolve_prompt("prompts/foo@1.0.0", project_root=tmp_path)


# ---------------------------------------------------------------------------
# Resolution order — local file beats registry
# ---------------------------------------------------------------------------


class TestResolutionOrder:
    def test_local_file_wins_over_registry(self, tmp_path: Path) -> None:
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "winner.md").write_text("local-win", encoding="utf-8")

        with (
            patch.dict("os.environ", {"AGENTBREEDER_REGISTRY_URL": "http://registry.local"}),
            _patch_httpx_client(
                _MockHTTPResponse(
                    200,
                    {"data": [{"name": "winner", "version": "1.0.0", "content": "registry-win"}]},
                )
            ),
        ):
            assert resolve_prompt("prompts/winner", project_root=tmp_path) == "local-win"


# ---------------------------------------------------------------------------
# Semver key helper (unit-level)
# ---------------------------------------------------------------------------


class TestSemverKey:
    def test_semver_orders_10_above_9(self) -> None:
        assert _semver_key("1.10.0") > _semver_key("1.9.0")

    def test_invalid_versions_rank_after_valid(self) -> None:
        # Valid semver ranks lower numerically (0,...) than invalid (1,...)
        assert _semver_key("1.0.0") < _semver_key("not-a-version")

    def test_invalid_versions_lexicographic_among_themselves(self) -> None:
        assert _semver_key("zzz") > _semver_key("aaa")


# ---------------------------------------------------------------------------
# Registry resolution cache (P7)
# ---------------------------------------------------------------------------


class TestRegistryCache:
    """LRU cache on the registry resolution path."""

    def test_repeat_resolution_hits_registry_only_once(self, tmp_path: Path) -> None:
        """The second call for the same (name, version) is served from cache."""
        call_count = {"n": 0}

        class _Counting(_FakeClient):
            def get(  # type: ignore[override]
                self,
                url: str,
                headers: dict[str, str] | None = None,
                params: dict[str, str] | None = None,
            ) -> _MockHTTPResponse:
                call_count["n"] += 1
                return _MockHTTPResponse(
                    200,
                    {
                        "data": [
                            {
                                "name": "cached",
                                "version": "1.0.0",
                                "content": "cached-body",
                            }
                        ]
                    },
                )

        def _factory(*_a: object, **_kw: object) -> _Counting:
            return _Counting(_MockHTTPResponse(200, None))

        with (
            patch.dict(
                "os.environ",
                {"AGENTBREEDER_REGISTRY_URL": "http://registry.local"},
            ),
            patch("engine.prompt_resolver.httpx.Client", side_effect=_factory),
        ):
            r1 = resolve_prompt("prompts/cached", project_root=tmp_path)
            r2 = resolve_prompt("prompts/cached", project_root=tmp_path)
            r3 = resolve_prompt("prompts/cached", project_root=tmp_path)

        assert r1 == r2 == r3 == "cached-body"
        # Only the first call should have hit the registry; the next two
        # come from the LRU cache.
        assert call_count["n"] == 1

    def test_cache_distinguishes_by_version(self, tmp_path: Path) -> None:
        """Different ``version`` pins should not share cache entries."""
        seen_versions: list[str | None] = []

        class _Recording(_FakeClient):
            def get(  # type: ignore[override]
                self,
                url: str,
                headers: dict[str, str] | None = None,
                params: dict[str, str] | None = None,
            ) -> _MockHTTPResponse:
                seen_versions.append((params or {}).get("version"))
                return _MockHTTPResponse(
                    200,
                    {
                        "data": [
                            {"name": "vfoo", "version": "1.0.0", "content": "v1"},
                            {"name": "vfoo", "version": "2.0.0", "content": "v2"},
                        ]
                    },
                )

        def _factory(*_a: object, **_kw: object) -> _Recording:
            return _Recording(_MockHTTPResponse(200, None))

        with (
            patch.dict(
                "os.environ",
                {"AGENTBREEDER_REGISTRY_URL": "http://registry.local"},
            ),
            patch("engine.prompt_resolver.httpx.Client", side_effect=_factory),
        ):
            r1 = resolve_prompt("prompts/vfoo@1.0.0", project_root=tmp_path)
            r2 = resolve_prompt("prompts/vfoo@2.0.0", project_root=tmp_path)
            # Re-resolve the first — should be a cache hit.
            r1_again = resolve_prompt("prompts/vfoo@1.0.0", project_root=tmp_path)

        assert r1 == r1_again == "v1"
        assert r2 == "v2"
        # Two distinct cache keys -> two HTTP calls (the third call is cached).
        assert len(seen_versions) == 2
        assert "1.0.0" in seen_versions
        assert "2.0.0" in seen_versions

    def test_clear_registry_cache_forces_refetch(self, tmp_path: Path) -> None:
        """``clear_registry_cache()`` evicts entries and forces a re-fetch."""
        call_count = {"n": 0}

        class _Counting(_FakeClient):
            def get(  # type: ignore[override]
                self,
                url: str,
                headers: dict[str, str] | None = None,
                params: dict[str, str] | None = None,
            ) -> _MockHTTPResponse:
                call_count["n"] += 1
                return _MockHTTPResponse(
                    200,
                    {
                        "data": [
                            {
                                "name": "evictme",
                                "version": "1.0.0",
                                "content": "body",
                            }
                        ]
                    },
                )

        def _factory(*_a: object, **_kw: object) -> _Counting:
            return _Counting(_MockHTTPResponse(200, None))

        with (
            patch.dict(
                "os.environ",
                {"AGENTBREEDER_REGISTRY_URL": "http://registry.local"},
            ),
            patch("engine.prompt_resolver.httpx.Client", side_effect=_factory),
        ):
            resolve_prompt("prompts/evictme", project_root=tmp_path)
            clear_registry_cache()
            resolve_prompt("prompts/evictme", project_root=tmp_path)

        assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# Differentiated registry error logging (P10)
# ---------------------------------------------------------------------------


class TestRegistryErrorLogging:
    """Verify that different registry failure modes log at distinct levels
    with distinct event names — see resolver docstring for the taxonomy."""

    def test_401_logs_registry_auth_failed_at_error_level(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "AGENTBREEDER_REGISTRY_URL": "http://registry.local",
                    "AGENTBREEDER_REGISTRY_TOKEN": "expired",
                },
            ),
            _patch_httpx_client(_MockHTTPResponse(401, {"data": []})),
            caplog.at_level("DEBUG", logger="engine.prompt_resolver"),
        ):
            with pytest.raises(PromptNotFoundError):
                resolve_prompt("prompts/secret", project_root=tmp_path)

        auth_records = [r for r in caplog.records if r.message == "registry_auth_failed"]
        assert len(auth_records) == 1
        rec = auth_records[0]
        assert rec.levelname == "ERROR"
        assert getattr(rec, "status_code", None) == 401
        assert getattr(rec, "prompt_name", None) == "secret"

    def test_403_logs_registry_auth_failed_at_error_level(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "AGENTBREEDER_REGISTRY_URL": "http://registry.local",
                    "AGENTBREEDER_REGISTRY_TOKEN": "wrong-scope",
                },
            ),
            _patch_httpx_client(_MockHTTPResponse(403, {"data": []})),
            caplog.at_level("DEBUG", logger="engine.prompt_resolver"),
        ):
            with pytest.raises(PromptNotFoundError):
                resolve_prompt("prompts/forbidden", project_root=tmp_path)

        auth_records = [r for r in caplog.records if r.message == "registry_auth_failed"]
        assert len(auth_records) == 1
        assert auth_records[0].levelname == "ERROR"
        assert getattr(auth_records[0], "status_code", None) == 403

    def test_404_logs_registry_not_found_at_warning_level(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with (
            patch.dict(
                "os.environ",
                {"AGENTBREEDER_REGISTRY_URL": "http://registry.local"},
            ),
            _patch_httpx_client(_MockHTTPResponse(404, {"data": []})),
            caplog.at_level("DEBUG", logger="engine.prompt_resolver"),
        ):
            with pytest.raises(PromptNotFoundError):
                resolve_prompt("prompts/missing-404", project_root=tmp_path)

        nf_records = [r for r in caplog.records if r.message == "registry_not_found"]
        assert len(nf_records) == 1
        assert nf_records[0].levelname == "WARNING"
        assert getattr(nf_records[0], "status_code", None) == 404

    def test_timeout_logs_registry_timeout_at_warning_level(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with (
            patch.dict(
                "os.environ",
                {"AGENTBREEDER_REGISTRY_URL": "http://registry.local"},
            ),
            _patch_httpx_client(
                httpx.TimeoutException("timed out", request=httpx.Request("GET", "http://x"))
            ),
            caplog.at_level("DEBUG", logger="engine.prompt_resolver"),
        ):
            with pytest.raises(PromptNotFoundError):
                resolve_prompt("prompts/slow-timeout", project_root=tmp_path)

        timeout_records = [r for r in caplog.records if r.message == "registry_timeout"]
        assert len(timeout_records) == 1
        assert timeout_records[0].levelname == "WARNING"
        assert getattr(timeout_records[0], "prompt_name", None) == "slow-timeout"

    def test_500_logs_generic_registry_error_at_warning_level(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with (
            patch.dict(
                "os.environ",
                {"AGENTBREEDER_REGISTRY_URL": "http://registry.local"},
            ),
            _patch_httpx_client(_MockHTTPResponse(500, {"data": []})),
            caplog.at_level("DEBUG", logger="engine.prompt_resolver"),
        ):
            with pytest.raises(PromptNotFoundError):
                resolve_prompt("prompts/server-error", project_root=tmp_path)

        err_records = [r for r in caplog.records if r.message == "registry_error"]
        assert len(err_records) >= 1
        assert err_records[0].levelname == "WARNING"
        assert getattr(err_records[0], "status_code", None) == 500
