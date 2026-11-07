"""Manifest schema_version 2 and operator configuration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import v2_manifest_data, write_private

from agentbreeder.manifest import Manifest, ManifestError, load_manifest
from agentbreeder.operator_config import (
    OperatorConfigError,
    load_operator_config,
    resolve_credential,
)

ROOT = Path(__file__).parents[1]


def test_v2_round_trip_and_schema_doc_in_sync():
    manifest = Manifest.model_validate(v2_manifest_data())
    assert manifest.identity.ref == "agent-1"
    schema = json.loads((ROOT / "docs/manifest.schema.json").read_text())
    assert schema == Manifest.model_json_schema()


def test_example_manifest_validates():
    assert load_manifest(ROOT / "examples/http-agent/agent.yaml").schema_version == 2


@pytest.mark.parametrize(
    "change",
    [
        {"identity": None},
        {"schema_version": 3},
        {"policy": {"restrictions": ["/etc/policy.cedar"]}},
        {"policy": {"restrictions": ["ghcr.io/acme/policy:latest"]}},
        {"policy": {"restrictions": ["a", "a"]}},
        {"requires": {"attributes": {"risk_tier": "low"}}},
        {"requires": {"roles": [{"authority": "t", "role_id": "r"}]}},
        {"identity": {"provider": "static", "ref": "agent-1"}},
        {"identity": {"provider": "static", "ref": "agent-1", "environment": "Prod!"}},
        {
            "capabilities": {
                "tools": [{"mcp": "http://insecure.example.com", "actions": ["a"]}],
            }
        },
        {
            "capabilities": {
                "tools": [{"mcp": "https://mcp.example.com", "actions": ["a"]}],
                "approval_required": ["b"],
            }
        },
        {"identity": {"provider": "static", "ref": "x", "environment": "dev", "extra": 1}},
    ],
)
def test_invalid_v2_inputs_rejected(change):
    data = v2_manifest_data()
    data.update(change)
    if data.get("identity") is None:
        data.pop("identity")
    with pytest.raises(ValueError):
        Manifest.model_validate(data)


def test_v1_cannot_carry_v2_blocks():
    with pytest.raises(ValueError):
        Manifest.model_validate(
            {
                "schema_version": 1,
                "name": "x",
                "image": "sha256:" + "a" * 64,
                "identity": {"provider": "static", "ref": "a", "environment": "dev"},
            }
        )


def test_load_manifest_reports_nested_field_without_values(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text(
        "schema_version: 2\nname: x\nimage: sha256:" + "a" * 64 + "\n"
        "identity: {provider: static, ref: 'secret-value!!', environment: dev}\n"
    )
    with pytest.raises(ManifestError) as error:
        load_manifest(path)
    assert "identity" in str(error.value) and "secret-value" not in str(error.value)


def test_missing_operator_config_fails_closed(tmp_path, monkeypatch):
    from agentbreeder import operator_config

    monkeypatch.delenv(operator_config.CONFIG_ENV)
    monkeypatch.setattr(operator_config, "DEFAULT_PATH", tmp_path / "missing.yaml")
    config = load_operator_config()
    assert config.enforcement == "enforce" and config.path is None
    with pytest.raises(ValueError) as error:
        config.context("dev", "local")
    assert error.value.code == "CONTEXT_ENVIRONMENT_UNKNOWN"


def test_explicit_missing_config_is_an_error(tmp_path):
    with pytest.raises(OperatorConfigError):
        load_operator_config(tmp_path / "missing.yaml")


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o602])
def test_operator_config_must_be_private(identity_env, mode):
    identity_env.write()
    identity_env.path.chmod(mode)
    with pytest.raises(OperatorConfigError, match="0600"):
        load_operator_config(identity_env.path)


def test_operator_config_symlink_rejected(identity_env, tmp_path):
    identity_env.write()
    link = tmp_path / "link.yaml"
    link.symlink_to(identity_env.path)
    with pytest.raises(OperatorConfigError, match="symlink"):
        load_operator_config(link)


@pytest.mark.parametrize(
    "config",
    [
        {"enforcement": "off"},
        {"environments": {"Bad Name": {"targets": ["local"]}}},
        {"environments": {"dev": {"targets": []}}},
        {"approval_ttl_seconds": 5},
        {"unknown": True},
    ],
)
def test_invalid_operator_config(identity_env, config):
    identity_env.config = config
    with pytest.raises(OperatorConfigError, match="Invalid operator config"):
        identity_env.load()


def test_operator_digest_changes_with_content(identity_env):
    first = identity_env.load().digest
    identity_env.config["approval_ttl_seconds"] = 600
    assert identity_env.load().digest != first


def test_credential_references(tmp_path, monkeypatch):
    monkeypatch.setenv("IDP_SECRET", "s3cret")
    assert resolve_credential("env:IDP_SECRET", None) == "s3cret"
    write_private(tmp_path / "cred", "file-secret\n")
    assert resolve_credential("file:cred", tmp_path) == "file-secret"
    assert resolve_credential(None, None) is None
    with pytest.raises(OperatorConfigError, match="references"):
        resolve_credential("literal-secret", None)
    monkeypatch.delenv("IDP_SECRET")
    with pytest.raises(OperatorConfigError, match="unset"):
        resolve_credential("env:IDP_SECRET", None)
