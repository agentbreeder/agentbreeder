"""MCP servers through a relay, slice 4a (#744; spec §7): the `mcp` kind, contracts, and the
per-deployment grant lifecycle against a fake relay (a grants directory nobody serves)."""

from __future__ import annotations

import copy
import json
import stat

import pytest
import yaml
from conftest import v2_manifest_data
from pydantic import ValidationError
from test_build import IMAGE, FakeBuilder, version
from test_build import operator as operator  # noqa: F401  (fixture)
from test_build import project as project  # noqa: F401  (fixture)

from agentbreeder import mcp, registry
from agentbreeder.agentfile import AgentFile
from agentbreeder.build import BuildError, build
from agentbreeder.deployment import DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.manifest import Manifest, load_manifest
from agentbreeder.operator_config import OperatorConfigError
from agentbreeder.services.schema import ServiceError

GITHUB = {
    "upstream": {"remote": {"url": "https://api.githubcopilot.com/mcp/"}},
    "auth": {"type": "bearer", "secret": "token"},
    "secrets": {"token": {"import": "GITHUB_MCP_TOKEN"}},
    "tools": {"list_issues": {"effect": "read"}, "create_issue": {"effect": "write"}},
}
TOKEN = "ab" * 32


# -- mcp.yaml -----------------------------------------------------------------------------------


def test_mcp_spec_valid_shapes():
    spec = mcp.McpSpec.model_validate(GITHUB)
    assert spec.auth.secret_names() == {"token"} and set(spec.tools) == set(GITHUB["tools"])
    header = copy.deepcopy(GITHUB)
    header["auth"] = {"type": "header", "header": {"name": "X-Api-Key", "secret": "token"}}
    assert mcp.McpSpec.model_validate(header).auth.secret_names() == {"token"}
    oauth = copy.deepcopy(GITHUB)
    oauth["auth"] = {
        "type": "oauth_refresh",
        "oauth_refresh": {
            "token_url": "https://oauth2.example.com/token",
            "client_id": "id",
            "client_secret": "cs",
            "refresh_token": "rt",
            "scopes": ["a.readonly"],
        },
    }
    oauth["secrets"] = {n: {"import": f"G_{n.upper()}"} for n in ("id", "cs", "rt")}
    assert mcp.McpSpec.model_validate(oauth).auth.secret_names() == {"id", "cs", "rt"}
    service = {
        "upstream": {"service": {"ref": "service/playwright-mcp@1.0.0", "port": "http"}},
        "auth": {"type": "none"},
        "tools": {"browser_navigate": {"effect": "write"}},
    }
    spec = mcp.McpSpec.model_validate(service)
    assert spec.upstream.service.path == "/mcp" and spec.auth.secret_names() == set()


@pytest.mark.parametrize(
    "change,message",
    [
        (lambda d: d["upstream"]["remote"].update(url="http://x.example.com/mcp"), "https"),
        (lambda d: d["upstream"]["remote"].update(url="https://u:p@x.example.com/"), "https"),
        (lambda d: d["upstream"].update(service={"ref": "service/x@1.0.0", "port": "h"}), "one"),
        (lambda d: d.update(upstream={}), "exactly one"),
        (lambda d: d["auth"].update(secret="nope"), "undeclared"),
        (lambda d: d["auth"].update(type="none"), "only its own field"),
        (lambda d: d["auth"].update(header={"name": "X-K", "secret": "token"}), "own field"),
        (lambda d: d["secrets"].update(token={"import": "GITHUB_TOKEN"}), "can't be imported"),
        (lambda d: d["secrets"].update(t2={"generate": "token", "per": "x"}), "for services"),
        (lambda d: d["secrets"].update({"Bad": {"generate": "token"}}), "invalid"),
        (lambda d: d.update(tools={}), "tools"),
        (lambda d: d["tools"].update({"bad name": {"effect": "read"}}), "tool names"),
        (lambda d: d["tools"].update(t={"effect": "delete"}), "tools"),
        (lambda d: d.update(extra=1), "extra"),
    ],
)
def test_mcp_spec_refusals(change, message):
    data = copy.deepcopy(GITHUB)
    change(data)
    with pytest.raises(ValidationError, match=message):
        mcp.McpSpec.model_validate(data)


@pytest.mark.parametrize(
    "auth,message",
    [
        ({"type": "header", "header": {"name": "Authorization", "secret": "token"}}, "controls"),
        (
            {
                "type": "oauth_refresh",
                "oauth_refresh": {
                    "token_url": "https://t.example.com/token",
                    "client_id": "token",
                    "client_secret": "token",
                    "refresh_token": "token",
                    "scopes": ["has space"],
                },
            },
            "printable",
        ),
    ],
)
def test_mcp_auth_refusals(auth, message):
    data = copy.deepcopy(GITHUB)
    data["auth"] = auth
    with pytest.raises(ValidationError, match=message):
        mcp.McpSpec.model_validate(data)


def test_oauth_refresh_needs_a_remote_upstream():
    data = {
        "upstream": {"service": {"ref": "service/x@1.0.0", "port": "http"}},
        "auth": {
            "type": "oauth_refresh",
            "oauth_refresh": {
                "token_url": "https://t.example.com/token",
                "client_id": "a",
                "client_secret": "a",
                "refresh_token": "a",
            },
        },
        "secrets": {"a": {"import": "A_SECRET"}},
        "tools": {"t": {"effect": "read"}},
    }
    with pytest.raises(ValidationError, match="remote upstreams"):
        mcp.McpSpec.model_validate(data)


def test_load_mcp(tmp_path):
    (tmp_path / "mcp.yaml").write_text(yaml.safe_dump(GITHUB))
    assert "list_issues" in mcp.load_mcp(tmp_path).tools
    (tmp_path / "mcp.yaml").write_text("tools: {}\n")
    with pytest.raises(ServiceError, match="MCP_INVALID"):
        mcp.load_mcp(tmp_path)
    (tmp_path / "mcp.yaml").write_text("a: &x 1\nb: *x\n")
    with pytest.raises(ServiceError, match="MCP_INVALID"):
        mcp.load_mcp(tmp_path)


def test_registry_kind_and_scaffold(tmp_path):
    assert registry.Ref.parse("mcp/github@1.0.0").kind == "mcp"
    target = registry.scaffold("mcp", "github", tmp_path)
    assert mcp.load_mcp(target).auth.type == "bearer"


# -- tokens and grant files ---------------------------------------------------------------------


def test_tokens_and_names():
    token = mcp.mint_token()
    assert len(token) == 64 and mcp.TOKEN.fullmatch(token)
    assert mcp.token_hash(token) != mcp.token_hash(mcp.mint_token())
    for bad in ("", "AB" * 32, "ab" * 33, "zz" * 32):
        with pytest.raises(mcp.McpError, match="MCP_TOKEN_INVALID"):
            mcp.token_hash(bad)
    assert mcp.env_name("google-workspace") == "GOOGLE_WORKSPACE"


def grant(deployment="d" * 32, server="github"):
    return {
        "deployment": deployment,
        "agent": "helper",
        "environment": "dev",
        "server": server,
        "tools": ["list_issues"],
    }


def test_grant_files_are_hash_named_read_only_and_hold_no_token(tmp_path):
    directory = tmp_path / "grants" / "dev" / "github"
    path = mcp.write_grant(directory, TOKEN, grant())
    assert path.name == f"{mcp.token_hash(TOKEN)}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o444
    assert stat.S_IMODE(directory.stat().st_mode) == 0o755
    assert TOKEN not in path.read_text()
    assert json.loads(path.read_text()) == grant()
    mcp.write_grant(directory, TOKEN, grant())  # rewriting is atomic, not EEXIST
    (directory / "notes.txt").write_text("ignored")
    (directory / ("0" * 64 + ".json")).write_text("not json")
    found = mcp.grants(directory)
    assert found[mcp.token_hash(TOKEN)] == grant() and found["0" * 64] == {}
    assert mcp.grants(tmp_path / "missing") == {}

    with pytest.raises(mcp.McpError, match="MCP_GRANT_INVALID"):
        mcp.write_grant(directory, TOKEN, {**grant(), "token": TOKEN})
    with pytest.raises(mcp.McpError, match="MCP_GRANTS_DIR"):
        mcp.write_grant(tmp_path / ".." / "relative-not", TOKEN, grant()) if False else (
            mcp.write_grant(type(directory)("relative"), TOKEN, grant())
        )


def test_revoke_by_deployment_and_keep(tmp_path):
    other = "cd" * 32
    mcp.write_grant(tmp_path, TOKEN, grant())
    mcp.write_grant(tmp_path, other, grant("e" * 32))
    assert mcp.revoke(tmp_path, deployment="f" * 32) == 0
    assert mcp.revoke(tmp_path, deployment="d" * 32) == 1
    assert list(mcp.grants(tmp_path)) == [mcp.token_hash(other)]
    assert mcp.revoke(tmp_path, keep={mcp.token_hash(other)}) == 0
    assert mcp.revoke(tmp_path) == 1 and mcp.grants(tmp_path) == {}


# -- agent.yaml, manifest, operator config ----------------------------------------------------


AGENT = {
    "kind": "agent",
    "schema_version": 1,
    "name": "helper",
    "runtime": "runtime/fake@1.0.0",
    "model": {"alias": "support-model"},
    "prompt": {"ref": "prompt/helper@1.0.0"},
}


def test_agent_file_mcp_entries():
    agent = AgentFile.model_validate(
        {**AGENT, "mcp": [{"ref": "mcp/github@1.0.0", "tools": ["list_issues"]}]}
    )
    assert "mcp/github@1.0.0" in agent.refs()
    for entry, message in (
        ({"ref": "tool/github@1.0.0", "tools": ["a"]}, "must be a mcp"),
        ({"ref": "mcp/github@1.0.0", "tools": []}, "at least 1"),
        ({"ref": "mcp/github@1.0.0", "tools": ["a", "a"]}, "duplicate tool"),
        ({"ref": "mcp/github@1.0.0", "tools": ["a"], "server": "github"}, "server"),
    ):
        with pytest.raises(ValidationError, match=message):
            AgentFile.model_validate({**AGENT, "mcp": [entry]})
    with pytest.raises(ValidationError, match="duplicates"):
        AgentFile.model_validate(
            {
                **AGENT,
                "mcp": [
                    {"ref": "mcp/github@1.0.0", "tools": ["a"]},
                    {"ref": "mcp/github@1.0.1", "tools": ["a"]},
                ],
            }
        )


MODEL = {"gateway": "litellm-local", "aliases": ["support-model"]}


def test_manifest_mcp_access_contract():
    data = v2_manifest_data(
        model_access=MODEL, mcp_access=[{"ref": "mcp/github@1.0.0", "tools": ["list_issues"]}]
    )
    manifest = Manifest.model_validate(data)
    assert (
        manifest.digest() != Manifest.model_validate(v2_manifest_data(model_access=MODEL)).digest()
    )
    for access in (
        [{"ref": "mcp/github@1.0.0", "tools": []}],
        [{"ref": "mcp/github@1.0.0", "tools": ["a", "a"]}],
        [{"ref": "tool/github@1.0.0", "tools": ["a"]}],
        [{"ref": "mcp/github@1.0.0", "tools": ["a"]}, {"ref": "mcp/github@1.0.1", "tools": ["a"]}],
        [],
    ):
        with pytest.raises(ValidationError):
            Manifest.model_validate(v2_manifest_data(model_access=MODEL, mcp_access=access))
    with pytest.raises(ValidationError, match="needs model_access"):
        Manifest.model_validate(
            v2_manifest_data(mcp_access=[{"ref": "mcp/github@1.0.0", "tools": ["a"]}])
        )


def relay_env(identity_env, tmp_path, tools=("list_issues",), relays=None):
    identity_env.config["gateways"] = {
        "litellm-local": {
            "type": "litellm",
            "url": "http://127.0.0.1:4000",
            "agent_url": "http://litellm:4000",
        }
    }
    identity = {"provider": "static", "ref": "agent-1", "environment": "dev"}
    identity_env.config["environments"]["dev"].update(
        {
            "gateway": "litellm-local",
            "models": ["support-model"],
            "ceilings": {"budget_usd": 50, "rpm": 300},
            "mcp_relays": relays
            if relays is not None
            else {
                "github": {
                    "ref": "mcp/github@1.0.0",
                    "url": "http://mcp-github:8080/mcp",
                    "grants_dir": str(tmp_path / "grants" / "dev" / "github"),
                }
            },
            "agents": {
                "helper": {
                    "identity": identity,
                    "mcp": {"github": list(tools)} if tools else {},
                }
            },
        }
    )
    return tmp_path / "grants" / "dev" / "github"


@pytest.mark.parametrize(
    "change,message",
    [
        (lambda e: e["mcp_relays"]["github"].update(grants_dir="relative"), "absolute"),
        (lambda e: e["mcp_relays"]["github"].update(url="ftp://x"), "url"),
        (lambda e: e["mcp_relays"]["github"].update(ref="tool/x@1.0.0"), "ref"),
        (lambda e: e["mcp_relays"].update({"Bad": e["mcp_relays"]["github"]}), "relay name"),
        (
            lambda e: e["mcp_relays"].update(
                other={**e["mcp_relays"]["github"], "ref": "mcp/other@1.0.0"}
            ),
            "own grants_dir",
        ),
        (lambda e: e["agents"]["helper"].update(mcp={"slack": ["a"]}), "lacks"),
        (lambda e: e["agents"]["helper"].update(mcp={"github": []}), "1-100"),
        (lambda e: e["agents"]["helper"].update(mcp={"github": ["a", "a"]}), "unique"),
    ],
)
def test_operator_config_mcp_refusals(identity_env, tmp_path, change, message):
    relay_env(identity_env, tmp_path)
    change(identity_env.config["environments"]["dev"])
    with pytest.raises(OperatorConfigError):
        identity_env.load()


# -- build ------------------------------------------------------------------------------------


def test_build_checks_tools_and_writes_config_and_manifest(project, operator):  # noqa: F811
    version(project / "registry", "mcp", "github", {"mcp.yaml": yaml.safe_dump(GITHUB)})
    agent = yaml.safe_load((project / "agent.yaml").read_text())
    agent["mcp"] = [{"ref": "mcp/github@1.0.0", "tools": ["list_issues"]}]
    (project / "agent.yaml").write_text(yaml.safe_dump(agent))
    fake = FakeBuilder()
    result = build(project / "agent.yaml", operator.load(), "dev", builder=fake)
    listing, config = fake.contexts[0]
    assert config["mcp"] == [{"name": "github", "version": "1.0.0", "tools": ["list_issues"]}]
    assert not any(p.startswith("artifacts/mcp") for p in listing)  # never in the image
    manifest = load_manifest(result.manifest_path)
    assert manifest.image == IMAGE
    assert [a.model_dump() for a in manifest.mcp_access] == [
        {"ref": "mcp/github@1.0.0", "tools": ["list_issues"]}
    ]
    assert load_manifest(result.candidate_manifest_path).mcp_access == manifest.mcp_access

    agent["mcp"] = [{"ref": "mcp/github@1.0.0", "tools": ["delete_repo"]}]
    (project / "agent.yaml").write_text(yaml.safe_dump(agent))
    with pytest.raises(BuildError, match="MCP_TOOL_UNKNOWN"):
        build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())


def test_build_refuses_an_invalid_mcp_version(project, operator):  # noqa: F811
    version(project / "registry", "mcp", "github", {"mcp.yaml": "tools: {}\n"})
    agent = yaml.safe_load((project / "agent.yaml").read_text())
    agent["mcp"] = [{"ref": "mcp/github@1.0.0", "tools": ["list_issues"]}]
    (project / "agent.yaml").write_text(yaml.safe_dump(agent))
    with pytest.raises(BuildError, match="ARTIFACT_INVALID"):
        build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())


def test_manifest_without_mcp_has_no_mcp_access(project, operator):  # noqa: F811
    result = build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())
    assert load_manifest(result.manifest_path).mcp_access is None
    assert "mcp_access" not in yaml.safe_load(result.manifest_path.read_text())


# -- deploy lifecycle against a fake relay ---------------------------------------------------


def mcp_manifest(tools=("list_issues",), ref="mcp/github@1.0.0"):
    return Manifest.model_validate(
        v2_manifest_data(
            name="helper",
            model_access=MODEL,
            mcp_access=[{"ref": ref, "tools": list(tools)}],
        )
    )


RUNNING = {"Id": "c", "State": {"Running": True}}


def test_deploy_mints_token_writes_grant_and_cleans_up(identity_env, state, runtime, tmp_path):
    grants_dir = relay_env(identity_env, tmp_path)
    runtime.inspect.side_effect = [None, RUNNING, RUNNING, RUNNING]
    service = DeploymentService(state, runtime, identity_env.load())
    result = service.deploy(mcp_manifest(), local=True, environment="dev")
    assert result["state"] == "ready"

    kwargs = runtime.create.call_args.kwargs
    assert kwargs["environment"]["AGENTBREEDER_MCP_GITHUB_URL"] == "http://mcp-github:8080/mcp"
    mount = next(m for m in kwargs["mounts"] if m.target == "/run/secrets/mcp-github-token")
    token = mount.source.read_text()
    assert mount.envs == ("AGENTBREEDER_MCP_GITHUB_TOKEN",)
    assert token not in json.dumps(kwargs["environment"])
    files = mcp.grants(grants_dir)
    assert list(files) == [mcp.token_hash(token)]
    assert files[mcp.token_hash(token)] == {
        "deployment": result["id"],
        "agent": "helper",
        "environment": "dev",
        "server": "github",
        "tools": ["list_issues"],
    }

    # reconcile keeps the current grant and deletes a stray one
    mcp.write_grant(grants_dir, "cd" * 32, {**files[mcp.token_hash(token)]})
    mcp.write_grant(grants_dir, "ef" * 32, {**files[mcp.token_hash(token)], "server": "x"})
    unknown = {**files[mcp.token_hash(token)], "deployment": "a" * 32}
    mcp.write_grant(grants_dir, "12" * 32, unknown)
    pruned = [r for r in service.reconcile() if r["compliance"] == "mcp_grant_revoked"]
    assert len(pruned) == 3 and list(mcp.grants(grants_dir)) == [mcp.token_hash(token)]

    # a deployment whose token file is gone keeps no grant either
    mount.source.chmod(0o600)
    mount.source.unlink()
    assert [r for r in service.reconcile() if r["compliance"] == "mcp_grant_revoked"]
    assert mcp.grants(grants_dir) == {}

    runtime.inspect.side_effect = None
    runtime.inspect.return_value = None
    removed = service.remove(result["id"], local=True)
    assert removed["state"] == "removed" and mcp.grants(grants_dir) == {}


def test_redeploy_replaces_the_earlier_token(identity_env, state, runtime, tmp_path):
    grants_dir = relay_env(identity_env, tmp_path)
    runtime.inspect.side_effect = [None, RUNNING, RUNNING, RUNNING]
    service = DeploymentService(state, runtime, identity_env.load())
    first = service.deploy(mcp_manifest(), local=True, environment="dev")
    before = set(mcp.grants(grants_dir))
    # the container is gone: the next deploy recreates it with a fresh token
    runtime.inspect.side_effect = [None, RUNNING, RUNNING, RUNNING]
    second = service.deploy(mcp_manifest(), local=True, environment="dev")
    assert second["id"] == first["id"], second
    assert second["state"] == "ready", second.get("error")
    after = set(mcp.grants(grants_dir))
    assert len(after) == 1 and after != before


def test_failed_deploy_leaves_no_grant(identity_env, state, runtime, tmp_path):
    grants_dir = relay_env(identity_env, tmp_path)
    runtime.inspect.side_effect = [None, RUNNING, RUNNING, RUNNING]
    runtime.healthy.return_value = False
    service = DeploymentService(state, runtime, identity_env.load())
    result = service.deploy(mcp_manifest(), local=True, environment="dev")
    assert result["state"] == "failed" and mcp.grants(grants_dir) == {}


@pytest.mark.parametrize(
    "setup,manifest,code",
    [
        ({"relays": {}, "tools": ()}, mcp_manifest(), "MCP_SERVER_UNAVAILABLE"),
        ({"tools": ()}, mcp_manifest(), "MCP_TOOL_NOT_GRANTED"),
        ({}, mcp_manifest(tools=("list_issues", "create_issue")), "MCP_TOOL_NOT_GRANTED"),
        ({}, mcp_manifest(ref="mcp/github@2.0.0"), "MCP_SERVER_UNAVAILABLE"),
    ],
)
def test_deploy_refusals(identity_env, state, runtime, tmp_path, setup, manifest, code):
    relay_env(identity_env, tmp_path, **setup)
    service = DeploymentService(state, runtime, identity_env.load())
    with pytest.raises(GateDenied) as error:
        service.deploy(manifest, local=True, environment="dev")
    assert error.value.code == code and runtime.create.call_count == 0
    # preflight (release, release --rollback) sees the same deny
    decision = service.preflight(manifest, "dev", service_deployer())
    assert decision.code == code


def service_deployer():
    from agentbreeder.deployer import os_deployer

    return os_deployer()


def test_two_relays_for_one_ref_are_ambiguous(identity_env, state, runtime, tmp_path):
    relay = {"ref": "mcp/github@1.0.0", "url": "http://a:8080/mcp"}
    relays = {
        "github": {**relay, "grants_dir": str(tmp_path / "g1")},
        "github2": {**relay, "grants_dir": str(tmp_path / "g2")},
    }
    relay_env(identity_env, tmp_path, relays=relays)
    service = DeploymentService(state, runtime, identity_env.load())
    with pytest.raises(GateDenied) as error:
        service.deploy(mcp_manifest(), local=True, environment="dev")
    assert error.value.code == "MCP_SERVER_AMBIGUOUS"


def test_runtime_enforcement_refuses_mcp_access(identity_env, state, runtime, tmp_path):
    relay_env(identity_env, tmp_path)
    service = DeploymentService(state, runtime, identity_env.load())
    service.enforcing = True
    assert service._deploy_requirements(mcp_manifest(), "dev")[0] == "MCP_RELAY_UNSUPPORTED"


def test_grant_write_failure_fails_the_deploy(identity_env, state, runtime, tmp_path):
    grants_dir = relay_env(identity_env, tmp_path)
    grants_dir.parent.mkdir(parents=True)
    grants_dir.symlink_to(tmp_path)  # refused: grants_dir must be a real directory
    runtime.inspect.side_effect = [None, RUNNING, RUNNING, RUNNING]
    service = DeploymentService(state, runtime, identity_env.load())
    result = service.deploy(mcp_manifest(), local=True, environment="dev")
    assert result["state"] == "failed" and "MCP_GRANTS_DIR" in result["error"]
    assert runtime.create.call_count == 0


def test_inactive_deployment_grant_is_pruned(identity_env, state, runtime, tmp_path):
    grants_dir = relay_env(identity_env, tmp_path)
    runtime.inspect.side_effect = [None, RUNNING, RUNNING, RUNNING]
    runtime.healthy.return_value = False
    service = DeploymentService(state, runtime, identity_env.load())
    failed = service.deploy(mcp_manifest(), local=True, environment="dev")
    grant_data = {
        "deployment": failed["id"],
        "agent": "helper",
        "environment": "dev",
        "server": "github",
        "tools": ["list_issues"],
    }
    mcp.write_grant(grants_dir, TOKEN, grant_data)  # e.g. left by a crash
    runtime.inspect.side_effect = None
    service.reconcile()
    assert mcp.grants(grants_dir) == {}


def test_remove_fails_when_grants_cannot_be_deleted(
    identity_env, state, runtime, tmp_path, monkeypatch
):
    relay_env(identity_env, tmp_path)
    runtime.inspect.side_effect = [None, RUNNING, RUNNING, RUNNING]
    service = DeploymentService(state, runtime, identity_env.load())
    result = service.deploy(mcp_manifest(), local=True, environment="dev")

    def broken(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(mcp, "revoke", broken)
    runtime.inspect.side_effect = None
    runtime.inspect.return_value = None
    removed = service.remove(result["id"], local=True)
    assert removed["state"] == "failed" and "MCP_GRANT_CLEANUP" in removed["error"]


# -- stack.yaml ------------------------------------------------------------------------------


def stack_with_mcp():
    from test_services_engine import STACK

    data = copy.deepcopy(STACK)
    data["mcp"] = {"github": {"ref": "mcp/github@1.0.0"}}
    agent = next(iter(data["environment"]["agents"]))
    data["environment"]["agents"][agent]["mcp"] = {"github": ["list_issues"]}
    return data, agent


def test_stack_mcp_entries_and_grants_validate():
    from agentbreeder.services.schema import Stack

    data, _ = stack_with_mcp()
    assert Stack.model_validate(data).mcp["github"].ref == "mcp/github@1.0.0"


@pytest.mark.parametrize(
    "change,message",
    [
        (lambda d, a: d["mcp"].update({"Bad": {"ref": "mcp/x@1.0.0"}}), "invalid"),
        (lambda d, a: d["mcp"]["github"].update(ref="service/x@1.0.0"), "must be an mcp"),
        (lambda d, a: d["mcp"]["github"].update(ref="mcp/x"), "not an mcp"),
        (lambda d, a: d["mcp"].update(gh2={"ref": "mcp/github@1.0.1"}), "appears once"),
        (lambda d, a: d["environment"]["agents"][a].update(mcp={"slack": ["x"]}), "unknown"),
        (lambda d, a: d["environment"]["agents"][a].update(mcp={"github": []}), "1-100"),
        (lambda d, a: d["environment"]["agents"][a].update(mcp={"github": ["a b"]}), "tool"),
    ],
)
def test_stack_mcp_refusals(change, message):
    from agentbreeder.services.schema import Stack

    data, agent = stack_with_mcp()
    change(data, agent)
    with pytest.raises(ValidationError, match=message):
        Stack.model_validate(data)


def test_services_up_refuses_mcp_until_the_relay_exists(tmp_path, monkeypatch):
    from test_services_engine import FakeDocker, write_project

    from agentbreeder.services.engine import Engine
    from agentbreeder.services.state import ROOT_ENV

    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    data, _ = stack_with_mcp()
    project = write_project(tmp_path / "project", stack=data)
    engine = Engine(project, docker=FakeDocker())
    try:
        with pytest.raises(ServiceError, match="MCP_RELAY_UNAVAILABLE"):
            engine.up("dev", confirm=lambda grant: True)
    finally:
        engine.close()
