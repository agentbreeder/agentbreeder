"""Unit tests for engine.provisioners — state, requirements, base ABC."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

from engine.provisioners import (
    CloudName,
    InfraProvisioner,
    InfraState,
    get_requirements,
    provisioner_for,
)

# -- InfraState ---------------------------------------------------------------


def test_infra_state_roundtrip(tmp_path: Path) -> None:
    state = InfraState(
        cloud="aws",
        region="us-east-1",
        provisioned_by="agentbreeder@test",
        provisioned_at=datetime(2026, 5, 19, 9, 0, 0),
        mode="validated",
        resources={"cluster": "agentbreeder-default"},
    )
    target = tmp_path / "infra-state.json"
    state.save(target)
    assert target.exists()
    loaded = InfraState.load(target)
    assert loaded == state


def test_infra_state_load_or_none_returns_none_when_missing(tmp_path: Path) -> None:
    assert InfraState.load_or_none(tmp_path / "nope.json") is None


# -- requirements registry ----------------------------------------------------


@pytest.mark.parametrize("cloud", ["aws", "gcp", "azure"])
@pytest.mark.parametrize("mode", ["simple", "full"])
def test_requirements_registry_covers_every_combo(cloud: str, mode: str) -> None:
    req = get_requirements(cast(CloudName, cloud), mode)  # type: ignore[arg-type]
    assert req.cloud == cloud
    assert req.mode == mode
    assert req.required, f"{cloud} {mode} must declare at least one required field"
    # Sensitive fields must not leak via defaults.
    for f in (*req.required, *req.optional):
        if f.sensitive:
            assert f.default is None, f"sensitive field {f.name} must not have a default"
    # Full mode is a strict superset of simple for required fields.
    if mode == "full":
        simple = get_requirements(cast(CloudName, cloud), "simple")
        simple_names = {f.name for f in simple.required}
        full_names = {f.name for f in req.required}
        assert simple_names <= full_names, (
            f"{cloud} full mode must include every simple-mode required field"
        )


def test_requirements_unknown_cloud_raises() -> None:
    with pytest.raises(ValueError, match="No requirements"):
        get_requirements(cast(CloudName, "ibmcloud"), "simple")  # type: ignore[arg-type]


# -- provisioner_for + base ABC behaviour -------------------------------------


_CLOUD_SDK_HINTS = {"aws": "boto3", "gcp": "google.cloud", "azure": "azure.identity"}


def _cloud_sdk_installed(cloud: str) -> bool:
    try:
        __import__(_CLOUD_SDK_HINTS[cloud])
    except ImportError:
        return False
    return True


@pytest.mark.parametrize("cloud", ["aws", "gcp", "azure"])
def test_provisioner_for_returns_subclass(cloud: str) -> None:
    if not _cloud_sdk_installed(cloud):
        pytest.skip(f"{cloud} SDK not installed in this venv")
    p = provisioner_for(cast(CloudName, cloud))  # type: ignore[arg-type]
    assert isinstance(p, InfraProvisioner)


def test_provisioner_for_unknown_cloud_raises() -> None:
    with pytest.raises(ValueError):
        provisioner_for(cast(CloudName, "oracle"))  # type: ignore[arg-type]


def test_provisioner_for_missing_sdk_raises_helpful_import_error(monkeypatch) -> None:
    """Lazy-load surfaces missing cloud SDKs with an extras-install hint."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "boto3":
            raise ImportError("boto3 forcibly missing")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    # Drop cached aws module so reimport actually fires.
    monkeypatch.delitem(__import__("sys").modules, "engine.provisioners.aws", raising=False)

    with pytest.raises(ImportError, match=r"agentbreeder\[aws\]"):
        provisioner_for("aws")
