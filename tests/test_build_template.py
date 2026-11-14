"""`build._manifest` split into `template()` and `bind()` (#763, M13 slice 1c, spec §5.4).

The pinned bytes below are what `build` wrote before the split (Northwind, and the test agent with
MCP tools); the split must not change one byte of a generated deploy manifest.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from test_build import FakeBuilder, version
from test_build import operator as operator  # noqa: F401  (fixture)
from test_build import project as project  # noqa: F401  (fixture)
from test_mcp import GITHUB

from agentbreeder import build as build_module
from agentbreeder import registry
from agentbreeder.agentfile import load_agent_file
from agentbreeder.build import BuildError, bind, build, template
from agentbreeder.manifest import IdentityReference
from agentbreeder.package import DeployTemplate

IMAGE = "sha256:" + "c" * 64
NORTHWIND = Path(registry.__file__).parent / "examples" / "northwind"

NORTHWIND_MANIFEST = """\
schema_version: 2
name: northwind-support
image: sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
target: local
port: 8080
health_path: /health
identity:
  provider: static
  ref: agent-1
  environment: dev
secrets:
  DATABASE_URL: secret://northwind-db
model_access:
  gateway: litellm-local
  aliases:
  - support-model
  - support-embedding
  budget_usd: 25.0
  rpm: 120
"""

NORTHWIND_CANDIDATE = """\
schema_version: 2
name: northwind-support-candidate
image: sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
target: local
port: 8080
health_path: /health
identity:
  provider: static
  ref: agent-2
  environment: dev
secrets:
  DATABASE_URL: secret://northwind-db
model_access:
  gateway: litellm-local
  aliases:
  - support-model
  - support-embedding
  budget_usd: 25.0
  rpm: 120
"""

HELPER_MANIFEST = """\
schema_version: 2
name: helper
image: sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
target: local
port: 9000
health_path: /healthz
identity:
  provider: static
  ref: agent-1
  environment: dev
secrets:
  DATABASE_URL: secret://helper-db
model_access:
  gateway: litellm-local
  aliases:
  - support-model
  - support-embedding
  budget_usd: 10.0
mcp_access:
- ref: mcp/github@1.0.0
  tools:
  - list_issues
  - create_issue
"""


@pytest.fixture
def northwind(tmp_path, monkeypatch, identity_env):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    project = tmp_path / "northwind"
    shutil.copytree(NORTHWIND, project, ignore=shutil.ignore_patterns("deploy", ".agentbreeder"))
    identity_env.config["gateways"] = {
        "litellm-local": {
            "type": "litellm",
            "url": "http://127.0.0.1:4000",
            "agent_url": "http://host.docker.internal:4000",
        }
    }
    identity_env.config["environments"]["dev"].update(
        {
            "gateway": "litellm-local",
            "models": ["support-model", "support-embedding"],
            "ceilings": {"budget_usd": 50, "rpm": 300},
            "bindings": {"DATABASE_URL": "secret://northwind-db"},
            "agents": {
                "northwind-support": {
                    "identity": {"provider": "static", "ref": "agent-1", "environment": "dev"},
                    "candidate_identity": {
                        "provider": "static",
                        "ref": "agent-2",
                        "environment": "dev",
                    },
                }
            },
        }
    )
    return project, identity_env


def test_northwind_manifests_are_byte_identical(northwind):
    project, operator = northwind
    result = build(
        project / "agent.yaml", operator.load(), "dev", mode="locked", builder=lambda c, i: IMAGE
    )
    assert result.manifest_path.read_text() == NORTHWIND_MANIFEST
    assert result.candidate_manifest_path.read_text() == NORTHWIND_CANDIDATE


def test_helper_with_mcp_manifest_is_byte_identical(project, operator):  # noqa: F811
    version(project / "registry", "mcp", "github", {"mcp.yaml": yaml.safe_dump(GITHUB)})
    agent = yaml.safe_load((project / "agent.yaml").read_text())
    agent["mcp"] = [{"ref": "mcp/github@1.0.0", "tools": ["list_issues", "create_issue"]}]
    (project / "agent.yaml").write_text(yaml.safe_dump(agent))
    result = build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())
    assert result.manifest_path.read_text() == HELPER_MANIFEST


def _northwind_template(project: Path) -> DeployTemplate:
    agent = load_agent_file(project / "agent.yaml")
    return template(agent, {"port": 8080, "health_path": "/health"}, list(ALIASES))


ALIASES = ("support-model", "support-embedding")
IDENTITY = IdentityReference(provider="static", ref="agent-1", environment="dev")


def test_template_holds_only_agent_and_runtime_fields(northwind):
    project, _ = northwind
    deploy = _northwind_template(project)
    assert deploy.model_dump(mode="json") == {
        "port": 8080,
        "health_path": "/health",
        "aliases": list(ALIASES),
        "budget_usd": 25.0,
        "rpm": 120,
        "mcp_access": [],
    }


def test_bind_of_a_template_read_back_from_package_json_gives_the_same_bytes(northwind):
    """Slice 3 binds `package.json`'s `deploy`, not build's object: the bytes must match."""
    project, operator = northwind
    stored = json.loads(json.dumps(_northwind_template(project).model_dump(mode="json")))
    manifest = bind(
        DeployTemplate.model_validate(stored),
        IMAGE,
        operator.load(),
        "dev",
        IDENTITY,
        "northwind-support",
    )
    assert yaml.safe_dump(manifest, sort_keys=False) == NORTHWIND_MANIFEST


def test_bind_takes_identity_secrets_and_gateway_from_the_operator_only(northwind):
    project, operator = northwind
    config = operator.config["environments"]["dev"]
    config["gateway"] = "other-gateway"
    operator.config["gateways"]["other-gateway"] = dict(
        operator.config["gateways"]["litellm-local"]
    )
    config["bindings"] = {"API_KEY": "secret://other"}
    other = IdentityReference(provider="static", ref="agent-9", environment="dev")
    image = "sha256:" + "d" * 64
    manifest = bind(_northwind_template(project), image, operator.load(), "dev", other, "nw")
    assert manifest["identity"] == {"provider": "static", "ref": "agent-9", "environment": "dev"}
    assert manifest["secrets"] == {"API_KEY": "secret://other"}
    assert manifest["model_access"]["gateway"] == "other-gateway"
    assert (manifest["image"], manifest["target"], manifest["name"]) == (image, "local", "nw")


@pytest.mark.parametrize(
    "key",
    ["identity", "secrets", "policy", "capabilities", "requires", "target", "image", "gateway"],
)
def test_the_template_has_no_field_for_what_bind_owns(northwind, key):
    project, _ = northwind
    forged = {**_northwind_template(project).model_dump(mode="json"), key: "x"}
    with pytest.raises(ValidationError):
        DeployTemplate.model_validate(forged)


def test_unset_budget_rpm_and_no_mcp_are_omitted(northwind):
    project, operator = northwind
    deploy = _northwind_template(project).model_copy(update={"budget_usd": None, "rpm": None})
    manifest = bind(deploy, IMAGE, operator.load(), "dev", IDENTITY, "nw")
    assert manifest["model_access"] == {"gateway": "litellm-local", "aliases": list(ALIASES)}
    assert "mcp_access" not in manifest


def test_no_bindings_means_no_secrets_key(northwind):
    project, operator = northwind
    operator.config["environments"]["dev"]["bindings"] = {}
    manifest = bind(_northwind_template(project), IMAGE, operator.load(), "dev", IDENTITY, "nw")
    assert "secrets" not in manifest


def test_an_invalid_template_is_a_build_error(northwind):
    project, _ = northwind
    aliases = [f"alias-{n}" for n in range(21)]  # over the 20-alias limit
    with pytest.raises(BuildError, match="MANIFEST_INVALID.*template.*aliases"):
        template(
            load_agent_file(project / "agent.yaml"), {"port": 8080, "health_path": "/"}, aliases
        )


def test_the_template_is_checked_before_docker_build(northwind, monkeypatch):
    project, operator = northwind
    calls = []

    def refuse(*args):
        raise BuildError("MANIFEST_INVALID", "generated deploy template is invalid: aliases")

    monkeypatch.setattr(build_module, "template", refuse)
    with pytest.raises(BuildError, match="MANIFEST_INVALID"):
        build(project / "agent.yaml", operator.load(), "dev", builder=lambda c, i: calls.append(1))
    assert calls == []


def test_bind_refuses_a_manifest_the_deploy_contract_rejects(northwind):
    project, operator = northwind
    with pytest.raises(BuildError, match="MANIFEST_INVALID.*manifest.*name"):
        bind(_northwind_template(project), IMAGE, operator.load(), "dev", IDENTITY, "Bad Name")


def test_an_integer_budget_in_package_json_binds_as_before(northwind):
    """A hand-written `package.json` may say `25`; the manifest still says `25.0`."""
    project, operator = northwind
    stored = {**_northwind_template(project).model_dump(mode="json"), "budget_usd": 25}
    manifest = bind(
        DeployTemplate.model_validate(stored),
        IMAGE,
        operator.load(),
        "dev",
        IDENTITY,
        "northwind-support",
    )
    assert yaml.safe_dump(manifest, sort_keys=False) == NORTHWIND_MANIFEST
