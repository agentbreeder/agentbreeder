"""The two-uid deploy contract and the Anthropic Messages route (#1089, owner decisions D15, D17).

Scripted Docker and kubectl only. A manifest whose `runtime` says `isolation: two-uid` gets a
second container from the agent's image (its own uid, no secret, no network on local Docker, no
environment but its role) sharing one small volume with the agent; one whose runtime says
`model_api: anthropic-messages` gets `/v1/messages` on its gateway key and is refused wherever
guardrails apply. Every other manifest is unchanged (the goldens in test_kit_egress.py).
`tests/test_northwind_claude_agent_sdk_docker.py` is the opt-in real-engine run (written, not run).
"""

from __future__ import annotations

import json

import pytest
from conftest import v2_manifest_data
from test_kit_egress import (
    ACCESS,
    HOST_GATEWAY,
    INTERNAL,
    KIT,
    NAMES,
    RECORD,
    FakeDocker,
    _labelled,
    _scripted,
    _world,
    kit_manifest,
)

from agentbreeder import build, gateway_keys, kubernetes, runtime_profile
from agentbreeder.egress import kit as kit_egress
from agentbreeder.egress.boundary import BoundaryUnverified
from agentbreeder.egress.kit import KitBoundary
from agentbreeder.gate import GateDenied
from agentbreeder.manifest import Manifest

CLAUDE = {
    "ref": "runtime/claude-agent-sdk@1.0.0",
    "profile": "kit",
    "model_api": "anthropic-messages",
    "isolation": "two-uid",
}
SHARED = ("volume", NAMES["shared"], kit_egress.SHARED_DIR)


def claude_manifest(**overrides) -> Manifest:
    data = {"model_access": dict(ACCESS), "runtime": dict(CLAUDE), **overrides}
    return Manifest.model_validate(v2_manifest_data(**data))


# -- the manifest field ----------------------------------------------------------------------


def test_runtime_fields_are_written_only_when_declared():
    plain = kit_manifest()
    assert plain.model_dump(mode="json")["runtime"] == KIT  # no null keys: digests unchanged
    assert '"model_api"' not in plain.model_dump_json()
    claude = claude_manifest()
    assert claude.model_dump(mode="json")["runtime"] == CLAUDE
    assert claude.digest() != plain.digest()


@pytest.mark.parametrize(
    "change", [{"model_api": "openai-chat"}, {"isolation": "root"}, {"isolation": None, "x": 1}]
)
def test_runtime_fields_take_only_their_one_value(change):
    with pytest.raises(ValueError):
        claude_manifest(runtime={**CLAUDE, **change})


def test_build_writes_the_fields_from_the_runtime_profile():
    profile = runtime_profile.from_manifest(
        {
            "kind": "runtime",
            "protocol": 1,
            "profile": "kit",
            "kit": "1.0.0",
            "framework": "claude-agent-sdk",
            "display_name": "Claude Agent SDK",
            "model_api": "anthropic-messages",
            "isolation": "two-uid",
        }
    )
    assert build._runtime_info(CLAUDE["ref"], profile) == CLAUDE
    chat = runtime_profile.from_manifest(
        {
            "kind": "runtime",
            "protocol": 1,
            "profile": "kit",
            "kit": "1.0.0",
            "framework": "langgraph",
            "display_name": "LangGraph",
            "model_api": "openai-chat",
        }
    )
    assert build._runtime_info(KIT["ref"], chat) == KIT


# -- gateway keys (D17) ------------------------------------------------------------------------


def test_only_the_anthropic_route_adds_v1_messages():
    assert gateway_keys.key_routes() == list(gateway_keys.GUARDED_ROUTES)
    assert gateway_keys.key_routes(None, None, "openai-chat") == list(gateway_keys.GUARDED_ROUTES)
    routes = gateway_keys.key_routes(None, None, "anthropic-messages")
    assert routes == [*gateway_keys.GUARDED_ROUTES, "/v1/messages"]
    body = gateway_keys.mint_body(
        "agentbreeder-x", ["support-model"], 1.0, 10, {"agentbreeder": True}, routes=routes
    )
    assert body["allowed_routes"] == routes


def test_no_other_route_can_be_minted():
    with pytest.raises(gateway_keys.GatewayError) as error:
        gateway_keys.mint_body(
            "agentbreeder-x", ["m"], 1.0, 10, {"agentbreeder": True}, routes=["/v1/responses"]
        )
    assert error.value.code == "GATEWAY_KEY_ROUTES"


# -- the gate refuses the Anthropic route wherever guardrails apply ----------------------------


def _deploy(identity_env, state, runtime, manifest):
    from agentbreeder.deployment import DeploymentService

    return DeploymentService(state, runtime, identity_env.load()).deploy(manifest, local=True)


def test_a_required_floor_refuses_the_anthropic_route(identity_env, state, runtime):
    identity_env.config["environments"]["dev"]["guardrails"] = {
        "required": True,
        "approved_sha256": "a" * 64,
    }
    with pytest.raises(GateDenied) as error:
        _deploy(identity_env, state, runtime, claude_manifest())
    assert error.value.code == "RUNTIME_GUARDRAILS_UNSUPPORTED"
    assert runtime.mock_calls == []


def test_agent_guardrails_refuse_the_anthropic_route(identity_env, state, runtime):
    with pytest.raises(GateDenied) as error:
        _deploy(identity_env, state, runtime, claude_manifest(guardrails=["pii-mask"]))
    assert error.value.code == "RUNTIME_GUARDRAILS_UNSUPPORTED"


def test_a_chat_kit_runtime_is_not_refused_for_it(identity_env, state, runtime):
    identity_env.config["environments"]["dev"]["guardrails"] = {
        "required": True,
        "approved_sha256": "a" * 64,
    }
    with pytest.raises(GateDenied) as error:
        _deploy(identity_env, state, runtime, kit_manifest())
    assert error.value.code == "GUARDRAILS_UNVERIFIED"  # the floor's own check, as before


# -- local Docker: the CLI container -----------------------------------------------------------


def _create(manifest):
    docker = FakeDocker({("image", "inspect"): _labelled(), ("network", "inspect"): INTERNAL})
    boundary = KitBoundary(docker)
    boundary.create(
        RECORD,
        manifest,
        environment={"AGENTBREEDER_GATEWAY_URL": HOST_GATEWAY},
        mounts=[],
        agents_network="stack-agents",
    )
    return boundary, docker


def test_two_uid_creates_a_secretless_offline_cli_container_and_a_shared_volume():
    _, docker = _create(claude_manifest())
    volume = next(c for c in docker.calls if c[:2] == ("volume", "create"))
    assert volume[-1] == NAMES["shared"]
    assert "io.agentbreeder.deployment=" + RECORD["id"] in volume
    assert "o=size=1m,uid=65532,gid=65532,mode=0755,noexec,nosuid,nodev" in volume
    creates = [c for c in docker.calls if c[:2] == ("container", "create")]
    cli = next(c for c in creates if c[c.index("--name") + 1] == NAMES["cli"])
    assert cli[cli.index("--network") + 1] == "none"
    assert cli[cli.index("--user") + 1] == "65533:65533"
    assert cli[cli.index("--cap-drop") + 1] == "ALL"
    for flag in ("--read-only", "no-new-privileges", "io.agentbreeder.role=cli"):
        assert flag in cli
    assert [cli[i + 1] for i, a in enumerate(cli) if a == "--env"] == [
        "AGENTBREEDER_ROLE=cli-host"
    ]
    assert [cli[i + 1] for i, a in enumerate(cli) if a == "--mount"] == [
        kit_egress.shared_mount(NAMES["shared"], readonly=True)
    ]
    assert kit_egress.shared_mount(NAMES["shared"], readonly=True).endswith(",readonly")
    assert "volume-nocopy" in kit_egress.shared_mount(NAMES["shared"])
    assert cli[-1] == claude_manifest().image
    assert not any("_FILE=" in a or "secret" in a.lower() for a in cli)
    # The agent mounts the same volume; its other arguments are the kit path's.
    assert docker.create.call_args.kwargs["volumes"] == (kit_egress.shared_mount(NAMES["shared"]),)


def test_a_kit_runtime_without_isolation_gets_no_cli_container():
    _, docker = _create(kit_manifest())
    assert not any(c[:2] == ("volume", "create") for c in docker.calls)
    assert "volumes" not in docker.create.call_args.kwargs
    names = [c[c.index("--name") + 1] for c in docker.calls if c[:2] == ("container", "create")]
    assert NAMES["cli"] not in names


def test_start_sidecar_starts_the_cli_container_for_two_uid_only():
    docker = FakeDocker()
    KitBoundary(docker).start_sidecar(RECORD, claude_manifest())
    KitBoundary(docker).start_sidecar(RECORD, kit_manifest())
    KitBoundary(docker).start_sidecar(RECORD)
    assert docker.calls == [
        ("container", "start", NAMES["sidecar"], NAMES["cli"]),
        ("container", "start", NAMES["sidecar"]),
        ("container", "start", NAMES["sidecar"]),
    ]


def _cli_world(**changes):
    world = _world()
    labels = {"io.agentbreeder.deployment": RECORD["id"]}
    world["agent"]["Image"] = "sha256:agent"
    world["agent"]["Mounts"] = [
        {
            "Type": "volume",
            "Name": NAMES["shared"],
            "Destination": kit_egress.SHARED_DIR,
            "RW": True,
        }
    ]
    world["cli"] = {
        "Id": "cli-id",
        "Image": "sha256:agent",
        "Config": {
            "Labels": {**labels, "io.agentbreeder.role": "cli"},
            "User": "65533:65533",
            "Env": ["AGENTBREEDER_ROLE=cli-host", "PATH=/usr/bin"],
        },
        "State": {"Running": True},
        "HostConfig": {
            "NetworkMode": "none",
            "CapDrop": ["ALL"],
            "ReadonlyRootfs": True,
            "SecurityOpt": ["no-new-privileges"],
        },
        "Mounts": [
            {
                "Type": "volume",
                "Name": NAMES["shared"],
                "Destination": kit_egress.SHARED_DIR,
                "RW": False,
            }
        ],
    }
    world["volume"] = {
        "Labels": dict(labels),
        "Options": {"type": "tmpfs", "device": "tmpfs", "o": kit_egress.VOLUME_OPTIONS},
    }
    for path, value in changes.items():
        target = world
        *parents, leaf = path.split(".")
        for key in parents:
            target = target[key]
        target[leaf] = value
    return world


def _verify_cli(world):
    docker = _scripted(world)
    original = docker.responses[("container", "inspect")]

    def inspect(args):
        if args[-1] == NAMES["cli"]:
            return json.dumps([world["cli"]])
        return original(args)

    docker.responses = {
        ("volume", "inspect"): lambda args: json.dumps([world["volume"]]),
        ("container", "inspect"): inspect,
        **docker.responses,
    }
    docker.responses[("container", "inspect")] = inspect
    return KitBoundary(docker).verify(
        RECORD, claude_manifest(), gateway_url=HOST_GATEWAY, agents_network="stack-agents"
    )


def test_verify_accepts_the_cli_container_it_created():
    assert _verify_cli(_cli_world())["tcp_open"] == []


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"cli.Config.User": "65532:65532"}, "own uid"),
        ({"cli.HostConfig.NetworkMode": NAMES["internal"]}, "own uid"),
        ({"cli.HostConfig.CapAdd": ["SETUID"]}, "capabilities"),
        ({"cli.HostConfig.Privileged": True}, "capabilities"),
        ({"cli.HostConfig.ReadonlyRootfs": False}, "writable root"),
        ({"cli.HostConfig.Binds": ["/x:/y"]}, "host path"),
        ({"cli.Image": "sha256:other"}, "agent's image"),
        ({"cli.State.Running": False}, "not running"),
        ({"cli.Config.Env": ["AGENTBREEDER_ROLE=cli-host", "LLM_GATEWAY_KEY_FILE=/k"]}, "env"),
        ({"cli.Config.Env": []}, "environment"),
        ({"cli.Config.Labels": {"io.agentbreeder.deployment": "x"}}, "ownership"),
        (
            {
                "cli.Mounts": [
                    {
                        "Type": "volume",
                        "Name": NAMES["shared"],
                        "Destination": "/run/agentbreeder-cli",
                    },
                    {"Type": "bind", "Name": "", "Destination": "/run/secrets"},
                ]
            },
            "more than",
        ),
        ({"agent.Mounts": []}, "more than"),
        ({"volume.Labels": {"io.agentbreeder.deployment": "x"}}, "volume ownership"),
        (
            {
                "cli.Mounts": [
                    {
                        "Type": "volume",
                        "Name": NAMES["shared"],
                        "Destination": "/run/agentbreeder-cli",
                        "RW": True,
                    }
                ]
            },
            "read-only",
        ),
        ({"cli.HostConfig.SecurityOpt": []}, "no-new-privileges"),
        ({"volume.Options": {"type": "tmpfs", "device": "tmpfs", "o": "size=1m"}}, "options"),
    ],
)
def test_verify_fails_closed_on_any_other_cli_container(changes, message):
    with pytest.raises(BoundaryUnverified) as error:
        _verify_cli(_cli_world(**changes))
    assert message in str(error.value)


def test_teardown_removes_the_shared_volume_and_verifies_it_is_gone():
    left = {"n": 0}

    def volumes(args):
        left["n"] += 1
        return NAMES["shared"] + "\n" if left["n"] == 1 else ""

    docker = FakeDocker({("volume", "ls"): volumes})
    KitBoundary(docker).teardown(RECORD)
    assert ("volume", "rm", "--force", NAMES["shared"]) in docker.calls

    stuck = FakeDocker({("volume", "ls"): NAMES["shared"] + "\n"})
    with pytest.raises(Exception, match="could not be verified"):
        KitBoundary(stuck).teardown(RECORD)


# -- kind: the CLI container in the pod ---------------------------------------------------------


def _pod(manifest):
    objects = kubernetes._objects(
        {"id": "d" * 32, "container": "agentbreeder-x", "digest": "spec"},
        manifest.model_copy(update={"target": "kubernetes"}),
        "agents",
        {"A": "1"},
        [],
        ("10.0.0.0/8",),
    )
    return next(o for o in objects if o["kind"] == "Deployment")["spec"]["template"]["spec"]


def test_kind_runs_the_cli_container_as_its_own_uid_with_no_secret():
    pod = _pod(claude_manifest())
    agent, cli = pod["containers"]
    assert cli["name"] == "cli" and cli["image"] == agent["image"]
    assert cli["env"] == [{"name": "AGENTBREEDER_ROLE", "value": "cli-host"}]
    assert cli["securityContext"] == {
        "runAsUser": 65533,
        "runAsGroup": 65533,
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert {m["name"] for m in cli["volumeMounts"]} == {"cli-tmp", "cli-shared"}
    shared = next(m for m in cli["volumeMounts"] if m["name"] == "cli-shared")
    assert shared["readOnly"] is True  # the emptyDir is world-writable
    assert {"name": "cli-shared", "mountPath": "/run/agentbreeder-cli"} in agent["volumeMounts"]
    assert pod["securityContext"]["runAsNonRoot"] is True  # PSS restricted still holds
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert volumes["cli-shared"]["emptyDir"] == {"medium": "Memory", "sizeLimit": "1Mi"}


def test_kind_without_isolation_has_one_container():
    assert [c["name"] for c in _pod(kit_manifest())["containers"]] == ["agent"]


def test_kind_never_mounts_secrets_into_the_cli_container(tmp_path):
    """The pod's fsGroup gives the CLI container gid 65532 too; only the missing mount keeps
    the secret volume (mode 0440, group 65532) from it, so that is pinned here."""
    from agentbreeder.secret_files import MOUNT_ROOT, SecretMount

    source = tmp_path / "key"
    source.write_text("k")
    mount = SecretMount(("LLM_GATEWAY_KEY",), "key", source, f"{MOUNT_ROOT}/key")
    objects = kubernetes._objects(
        {"id": "d" * 32, "container": "agentbreeder-x", "digest": "spec"},
        claude_manifest().model_copy(update={"target": "kubernetes"}),
        "agents",
        {"A": "1"},
        [mount],
        ("10.0.0.0/8",),
    )
    pod = next(o for o in objects if o["kind"] == "Deployment")["spec"]["template"]["spec"]
    agent, cli = pod["containers"]
    assert any(m["name"] == "secrets" for m in agent["volumeMounts"])
    assert not any(m["name"] == "secrets" for m in cli["volumeMounts"])
    assert not any(e["name"].endswith("_FILE") for e in cli["env"])
