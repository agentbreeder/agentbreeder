"""Shared identity-gate fixtures. Tests use the static adapter rather than skipping the gate."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentbreeder import deployer as deployer_module
from agentbreeder import operator_config

AUTHORITY = "tenant-a"
IMAGE = "sha256:" + "a" * 64


@pytest.fixture(autouse=True)
def _bare_launcher(monkeypatch):
    """`Next:` lines name the launcher; pin the bare form so output doesn't depend on how the
    test interpreter was installed (tests/test_launcher.py covers each form)."""
    from agentbreeder import hints

    monkeypatch.setattr(hints, "_prefix", lambda: "agentbreeder")


@pytest.fixture(autouse=True)
def _no_real_legacy_home(monkeypatch):
    """`doctor` must not see the developer's real ~/.agentbreeder (2.x state adds an INFO line)."""
    from agentbreeder.commands import doctor

    monkeypatch.setattr(doctor, "check_legacy", lambda: [])


def principal_entry(**overrides) -> dict:
    entry = {
        "kind": "agent",
        "enabled": True,
        "environment": "dev",
        "review_by": "2099-01-01",
        "sponsors": [{"id": "alice-id", "name": "alice@example.com", "active": True}],
        "owners": [{"id": "bob-id", "name": "bob@example.com", "active": True}],
        "roles": [{"resource": "payments-api", "role_id": "refunds-role", "name": "Refunds"}],
        "groups": [{"group_id": "finance-group", "name": "Finance"}],
        "attributes": {"acme/data_classification": "confidential", "acme/risk_tier": "low"},
    }
    entry.update(overrides)
    return entry


def write_private(path: Path, data) -> Path:
    path.write_text(data if isinstance(data, str) else yaml.safe_dump(data))
    path.chmod(0o600)
    return path


class IdentityEnv:
    """An operator config directory with a static provider; edit and call `load()`."""

    def __init__(self, directory: Path):
        directory.mkdir(mode=0o700, exist_ok=True)
        self.directory = directory
        self.principals = {"agent-1": principal_entry()}
        self.config: dict = {
            "enforcement": "enforce",
            "default_environment": "dev",
            "environments": {"dev": {"targets": ["local"]}, "prod": {"targets": ["local"]}},
            "providers": {"static": {"authority": AUTHORITY, "file": "principals.yaml"}},
        }
        self.write()

    @property
    def path(self) -> Path:
        return self.directory / "operator.yaml"

    def write(self) -> None:
        write_private(self.directory / "principals.yaml", {"principals": self.principals})
        write_private(self.path, self.config)

    def load(self):
        self.write()
        return operator_config.load_operator_config(self.path)


@pytest.fixture(autouse=True)
def identity_env(tmp_path, monkeypatch) -> IdentityEnv:
    env = IdentityEnv(tmp_path / "operator")
    monkeypatch.setenv(operator_config.CONFIG_ENV, str(env.path))
    for name in (deployer_module.TOKEN_ENV, deployer_module.TOKEN_FILE_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(deployer_module, "CACHE_PATH", tmp_path / "no-token-cache")
    return env


def v2_manifest_data(**overrides) -> dict:
    data = {
        "schema_version": 2,
        "name": "sample",
        "image": IMAGE,
        "identity": {"provider": "static", "ref": "agent-1", "environment": "dev"},
        "requires": {
            "roles": [
                {"authority": AUTHORITY, "resource": "payments-api", "role_id": "refunds-role"}
            ],
            "groups": [{"authority": AUTHORITY, "group_id": "finance-group"}],
            "attributes": {"acme/data_classification": "confidential"},
        },
    }
    data.update(overrides)
    return data


@pytest.fixture
def v2_manifest():
    from agentbreeder.manifest import Manifest

    return Manifest.model_validate(v2_manifest_data())


BASELINE_POLICY = """
permit(principal, action == AgentBreeder::Action::"deploy", resource)
when { principal.verified && resource.principal_kind == "agent" };
permit(principal, action == AgentBreeder::Action::"RequireApproval", resource)
when { resource.risk_tier == "high" };
permit(principal, action == AgentBreeder::Action::"approve", resource)
when { principal.verified && principal.subject == "carol" };
permit(principal, action == AgentBreeder::Action::"enable", resource)
when { principal.verified && principal.subject == "admin" };
"""


def write_policy(directory: Path, name: str, text: str) -> str:
    import hashlib

    path = write_private(directory / name, text)
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def enable_cedar(env: IdentityEnv, baseline: str = BASELINE_POLICY, restrictions=None) -> None:
    digest = write_policy(env.directory, "baseline.cedar", baseline)
    approved = {}
    for name, text in (restrictions or {}).items():
        approved[name] = {
            "source": f"{name}.cedar",
            "digest": write_policy(env.directory, f"{name}.cedar", text),
        }
    env.config["pdp"] = {
        "engine": "cedar",
        "baseline": {"source": "baseline.cedar", "digest": digest},
        "approved_restrictions": approved,
    }
    env.config["risk_attribute"] = "acme/risk_tier"


def actor(subject: str, verified: bool = True, **aliases):
    from agentbreeder.deployer import Deployer

    return Deployer(
        subject,
        verified,
        "https://issuer.example.com" if verified else None,
        "oidc" if verified else "os",
        tuple(aliases.values()),
        human=verified,
    )


@pytest.fixture
def runtime():
    from unittest.mock import Mock

    runtime = Mock(context="local-test")
    runtime.inspect.return_value = {"Id": "container-id", "State": {"Running": True}}
    runtime.endpoint.return_value = "http://127.0.0.1:1234"
    runtime.healthy.return_value = True
    return runtime


@pytest.fixture
def state(tmp_path):
    from agentbreeder.store import Store

    store = Store(tmp_path / "state")
    yield store
    store.close()
