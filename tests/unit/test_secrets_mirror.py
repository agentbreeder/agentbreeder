"""Unit tests for engine.secrets.auto_mirror."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from engine.secrets.auto_mirror import (
    CloudSecretRef,
    MirrorResult,
    deterministic_name,
    mirror_secrets_to_cloud,
)
from engine.secrets.base import SecretEntry, SecretsBackend


def _run(coro):
    return asyncio.run(coro)


# ─── fakes ──────────────────────────────────────────────────────────────────


class _FakeWorkspace(SecretsBackend):
    backend_name = "fake-workspace"  # type: ignore[assignment]

    def __init__(self, values: dict[str, str]) -> None:
        self._values = dict(values)

    async def get(self, name: str) -> str | None:
        return self._values.get(name)

    async def set(self, name, value, *, tags=None):
        self._values[name] = value

    async def delete(self, name: str) -> None:
        self._values.pop(name, None)

    async def list(self) -> list[SecretEntry]:
        return [
            SecretEntry(name=k, masked_value="••••", backend=self.backend_name)
            for k in self._values
        ]


class _FakeCloud(SecretsBackend):
    """Captures writes for assertion."""

    def __init__(self, name: str = "aws") -> None:
        self._name = name
        self.writes: list[tuple[str, str, dict[str, str] | None]] = []
        self.fail: set[str] = set()

    @property
    def backend_name(self) -> str:
        return self._name

    async def get(self, name: str) -> str | None:
        return None

    async def set(self, name, value, *, tags=None):
        if name in self.fail:
            raise RuntimeError("induced failure")
        self.writes.append((name, value, dict(tags) if tags else None))

    async def delete(self, name: str) -> None: ...

    async def list(self):
        return []


@pytest.fixture
def patch_target(monkeypatch):
    """Replace ``_build_target_backend`` with a controllable factory."""
    cloud = _FakeCloud()

    def factory(cloud_name: str, options: dict[str, Any]) -> SecretsBackend:
        cloud._name = cloud_name
        return cloud

    monkeypatch.setattr("engine.secrets.auto_mirror._build_target_backend", factory)
    return cloud


# ─── deterministic_name ─────────────────────────────────────────────────────


def test_deterministic_name_aws():
    assert (
        deterministic_name("customer-support", "OPENAI_API_KEY", cloud="aws")
        == "agentbreeder/customer-support/OPENAI_API_KEY"
    )


def test_deterministic_name_gcp_substitutes_slash():
    assert (
        deterministic_name("orders", "DB_PASSWORD", cloud="gcp")
        == "agentbreeder_orders_DB_PASSWORD"
    )


# ─── mirror_secrets_to_cloud ────────────────────────────────────────────────


class TestMirrorSecretsToCloud:
    def test_empty_secret_list_short_circuits(self, patch_target):
        result = _run(
            mirror_secrets_to_cloud(
                "agent",
                [],
                target_cloud="aws",
                workspace_backend=_FakeWorkspace({}),
            )
        )
        assert result.refs == []
        assert patch_target.writes == []

    def test_mirrors_each_value(self, patch_target):
        ws = _FakeWorkspace({"OPENAI_API_KEY": "sk-abc1234", "ZENDESK_TOKEN": "zd-xyz"})
        result = _run(
            mirror_secrets_to_cloud(
                "support-agent",
                ["OPENAI_API_KEY", "ZENDESK_TOKEN"],
                target_cloud="aws",
                workspace_backend=ws,
            )
        )
        assert len(result.refs) == 2
        assert {ref.cloud_name for ref in result.refs} == {
            "agentbreeder/support-agent/OPENAI_API_KEY",
            "agentbreeder/support-agent/ZENDESK_TOKEN",
        }
        # All writes carry the AgentBreeder tag set.
        for _, _, tags in patch_target.writes:
            assert tags is not None
            assert tags.get("managed-by") == "agentbreeder"
            assert tags.get("agent") == "support-agent"

    def test_missing_secret_skipped_not_failed(self, patch_target):
        ws = _FakeWorkspace({"PRESENT": "yes-1234567"})
        result = _run(
            mirror_secrets_to_cloud(
                "a",
                ["PRESENT", "MISSING"],
                target_cloud="aws",
                workspace_backend=ws,
            )
        )
        assert "MISSING" in result.skipped
        assert len(result.refs) == 1
        assert result.refs[0].logical_name == "PRESENT"

    def test_target_failure_recorded_not_raised(self, patch_target):
        ws = _FakeWorkspace({"OK": "v1234567", "BAD": "v7654321"})
        patch_target.fail.add("agentbreeder/agent/BAD")
        result = _run(
            mirror_secrets_to_cloud(
                "agent",
                ["OK", "BAD"],
                target_cloud="aws",
                workspace_backend=ws,
            )
        )
        assert any(ref.logical_name == "OK" for ref in result.refs)
        assert "BAD" in result.errors

    def test_unknown_target_raises(self):
        with pytest.raises(ValueError):
            _run(
                mirror_secrets_to_cloud(
                    "agent",
                    ["X"],
                    target_cloud="azure",  # type: ignore[arg-type]
                    workspace_backend=_FakeWorkspace({"X": "v"}),
                )
            )

    def test_gcp_target_uses_underscored_name(self, patch_target):
        ws = _FakeWorkspace({"OPENAI": "k-1234567"})
        result = _run(
            mirror_secrets_to_cloud(
                "agent",
                ["OPENAI"],
                target_cloud="gcp",
                workspace_backend=ws,
            )
        )
        assert result.refs[0].cloud_name == "agentbreeder_agent_OPENAI"

    def test_runtime_sa_grant_called(self, monkeypatch, patch_target):
        ws = _FakeWorkspace({"K": "v1234567"})
        called: list[tuple[str, str, str]] = []

        async def fake_grant(*, target_cloud, cloud_name, principal, target_options):
            called.append((target_cloud, cloud_name, principal))

        monkeypatch.setattr("engine.secrets.auto_mirror._grant_secret_accessor", fake_grant)
        _run(
            mirror_secrets_to_cloud(
                "agent",
                ["K"],
                target_cloud="aws",
                runtime_service_account="arn:aws:iam::123:role/r",
                workspace_backend=ws,
            )
        )
        assert called
        assert called[0][2] == "arn:aws:iam::123:role/r"

    def test_grant_failure_does_not_abort(self, monkeypatch, patch_target):
        ws = _FakeWorkspace({"K": "v1234567"})

        async def boom(**kw):
            raise RuntimeError("iam denied")

        monkeypatch.setattr("engine.secrets.auto_mirror._grant_secret_accessor", boom)
        result = _run(
            mirror_secrets_to_cloud(
                "agent",
                ["K"],
                target_cloud="aws",
                runtime_service_account="arn:aws:iam::123:role/r",
                workspace_backend=ws,
            )
        )
        assert len(result.refs) == 1


# ─── MirrorResult dataclass smoke test ───────────────────────────────────────


def test_mirror_result_default_collections_independent():
    a = MirrorResult()
    b = MirrorResult()
    a.skipped.append("X")
    assert b.skipped == []  # defaults are not shared mutables


def test_cloud_secret_ref_is_frozen():
    from dataclasses import FrozenInstanceError

    ref = CloudSecretRef(logical_name="A", cloud_name="agentbreeder/x/A", cloud="aws")
    with pytest.raises(FrozenInstanceError):
        ref.cloud = "gcp"  # type: ignore[misc]


# ─── _grant_secret_accessor dispatcher ──────────────────────────────────────


class TestGrantDispatcher:
    def test_dispatch_aws(self, monkeypatch):
        from engine.secrets import auto_mirror

        called: list[Any] = []

        def fake_aws(secret_name, principal, options):
            called.append(("aws", secret_name, principal))

        monkeypatch.setattr(auto_mirror, "_aws_grant", fake_aws)
        _run(
            auto_mirror._grant_secret_accessor(
                target_cloud="aws",
                cloud_name="agentbreeder/a/K",
                principal="arn:aws:iam::123:role/r",
                target_options={},
            )
        )
        assert called[0][0] == "aws"

    def test_dispatch_gcp(self, monkeypatch):
        from engine.secrets import auto_mirror

        called: list[Any] = []

        def fake_gcp(secret_name, principal, options):
            called.append(("gcp", secret_name, principal))

        monkeypatch.setattr(auto_mirror, "_gcp_grant", fake_gcp)
        _run(
            auto_mirror._grant_secret_accessor(
                target_cloud="gcp",
                cloud_name="agentbreeder_a_K",
                principal="sa@proj.iam.gserviceaccount.com",
                target_options={"project_id": "proj"},
            )
        )
        assert called[0][0] == "gcp"

    def test_dispatch_vault_is_noop(self):
        from engine.secrets import auto_mirror

        # Should return None without raising.
        result = _run(
            auto_mirror._grant_secret_accessor(
                target_cloud="vault",
                cloud_name="any",
                principal="any",
                target_options={},
            )
        )
        assert result is None


# ─── _build_target_backend ──────────────────────────────────────────────────


def test_build_target_backend_passes_prefix_empty(monkeypatch):
    """Ensures auto_mirror disables backend-side prefixing in favour of the
    deterministic name format."""
    from engine.secrets import auto_mirror

    captured: dict[str, Any] = {}

    def fake_get_backend(name: str, **kwargs: Any):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return _FakeCloud(name)

    monkeypatch.setattr("engine.secrets.factory.get_backend", fake_get_backend)
    backend = auto_mirror._build_target_backend("aws", {"region": "us-east-1"})
    assert backend.backend_name == "aws"
    assert captured["kwargs"]["prefix"] == ""
    assert captured["kwargs"]["region"] == "us-east-1"


# ─── _emit_mirror_audit fallback path ───────────────────────────────────────


def test_emit_mirror_audit_falls_back_when_api_unavailable(monkeypatch, caplog):
    """If the api package isn't importable the audit emit must not raise."""
    import builtins

    from engine.secrets import auto_mirror

    real_import = builtins.__import__

    def block_api(name, *args, **kwargs):
        if name.startswith("api."):
            raise ImportError("api unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_api)
    with caplog.at_level("INFO"):
        _run(
            auto_mirror._emit_mirror_audit(
                agent_name="a",
                logical="K",
                cloud_name="agentbreeder/a/K",
                target_cloud="aws",
                workspace="default",
            )
        )
