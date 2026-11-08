"""`agentbreeder build` (#729): resolve, assemble context, build (faked), write manifests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agentbreeder import build as build_module
from agentbreeder import cli, registry
from agentbreeder.build import BuildError, build
from agentbreeder.manifest import load_manifest
from agentbreeder.registry import RegistryError

IMAGE = "sha256:" + "b" * 64


def version(root: Path, kind: str, name: str, files: dict, extra: str = "") -> None:
    path = root / kind / name / "1.0.0"
    path.mkdir(parents=True)
    (path / "manifest.yaml").write_text(f"kind: {kind}\nname: {name}\nversion: 1.0.0\n{extra}")
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


AGENT = """\
kind: agent
schema_version: 1
name: helper
runtime: runtime/fake@1.0.0
model: {alias: support-model, budget_usd: 10}
prompt: {ref: prompt/helper@1.0.0}
knowledge:
  - {ref: knowledge/help@1.0.0}
tools:
  - {ref: tool/orders@1.0.0}
evals: {ref: eval/golden@1.0.0}
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setattr(registry, "BUILTIN_ROOT", tmp_path / "builtin")
    project = tmp_path / "project"
    root = project / "registry"
    version(
        root,
        "runtime",
        "fake",
        {
            "Dockerfile": "FROM scratch\n",
            "runtime/requirements.lock": "",
            "runtime/server.py": "x=1\n",
        },
        extra="port: 9000\nhealth_path: /healthz\n",  # the manifest must take these from here
    )
    version(root, "prompt", "helper", {"prompt.md": "Be helpful."})
    version(
        root,
        "knowledge",
        "help",
        {"docs/a.md": "Refunds take 5 days."},
        "embedding: {alias: support-embedding, dimensions: 768}\n",
    )
    version(root, "tool", "orders", {"tool.yaml": "functions: []\n", "src/orders.py": ""})
    version(root, "eval", "golden", {"golden.jsonl": "{}\n"})
    (project / "agent.yaml").write_text(AGENT)
    return project


@pytest.fixture
def operator(identity_env):
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
            "bindings": {"DATABASE_URL": "secret://helper-db"},
            "agents": {
                "helper": {
                    "identity": {"provider": "static", "ref": "agent-1", "environment": "dev"},
                    "candidate_identity": {
                        "provider": "static",
                        "ref": "agent-1",
                        "environment": "dev",
                    },
                }
            },
        }
    )
    return identity_env


class FakeBuilder:
    def __init__(self):
        self.contexts = []

    def __call__(self, context: Path, iidfile: Path) -> str:
        listing = sorted(p.relative_to(context).as_posix() for p in context.rglob("*"))
        config = json.loads((context / "agent.config.json").read_text())
        self.contexts.append((listing, config))
        return IMAGE


def test_build_assembles_context_and_writes_manifests(project, operator):
    fake = FakeBuilder()
    result = build(project / "agent.yaml", operator.load(), "dev", builder=fake)
    listing, config = fake.contexts[0]
    assert "Dockerfile" in listing and "runtime/server.py" in listing
    assert "runtime/requirements.lock" in listing and "agent.config.json" in listing
    assert "artifacts/prompt/helper/prompt.md" in listing
    assert "artifacts/knowledge/help/docs/a.md" in listing
    assert "artifacts/tool/orders/src/orders.py" in listing
    assert not any(p.startswith(("artifacts/eval", "artifacts/runtime")) for p in listing)
    assert config["prompt"] == {"dir": "artifacts/prompt/helper"}
    assert config["knowledge"][0]["dir"] == "artifacts/knowledge/help"
    assert config["tools"] == [
        {"name": "orders", "version": "1.0.0", "dir": "artifacts/tool/orders"}
    ]
    assert config["model"] == {"alias": "support-model", "reasoning": "low", "max_model_calls": 4}

    manifest = load_manifest(result.manifest_path)
    assert manifest.image == IMAGE and manifest.name == "helper"
    assert (manifest.port, manifest.health_path) == (9000, "/healthz")
    assert manifest.secrets == {"DATABASE_URL": "secret://helper-db"}
    assert manifest.model_access.aliases == ["support-model", "support-embedding"]
    assert manifest.model_access.budget_usd == 10
    candidate = load_manifest(result.candidate_manifest_path)
    assert candidate.name == "helper-candidate" and candidate.image == IMAGE
    record = json.loads(result.build_record_path.read_text())
    assert record["image"] == IMAGE and len(record["refs"]) == 5
    assert result.lock_changed and (project / "agent.lock").exists()
    assert set(result.as_json()) >= {"image", "manifest_path", "lock", "refs", "aliases"}


def test_build_is_reproducible_and_locked_detects_tamper(project, operator):
    build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())
    again = build(
        project / "agent.yaml", operator.load(), "dev", mode="locked", builder=FakeBuilder()
    )
    assert not again.lock_changed
    (project / "registry/tool/orders/1.0.0/src/orders.py").write_text("evil = 1\n")
    with pytest.raises(RegistryError) as error:
        build(project / "agent.yaml", operator.load(), "dev", mode="locked", builder=FakeBuilder())
    assert error.value.code == "HASH_MISMATCH"


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda c: c["environments"]["dev"].pop("agents"), "IDENTITY_UNBOUND"),
        (lambda c: c["environments"]["dev"].pop("gateway"), "MODEL_ACCESS_UNCONFIGURED"),
    ],
)
def test_build_needs_environment_grants(project, operator, mutation, code):
    mutation(operator.config)
    with pytest.raises(BuildError) as error:
        build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())
    assert error.value.code == code
    with pytest.raises(BuildError) as error:
        build(project / "agent.yaml", operator.load(), "nope", builder=FakeBuilder())
    assert error.value.code == "CONTEXT_ENVIRONMENT_UNKNOWN"


def test_build_rejects_bad_runtime_or_knowledge(project, operator):
    (project / "registry/runtime/fake/1.0.0/Dockerfile").unlink()
    with pytest.raises(BuildError, match="missing Dockerfile"):
        build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())


def test_build_knowledge_needs_embedding_alias(project, operator):
    manifest = project / "registry/knowledge/help/1.0.0/manifest.yaml"
    manifest.write_text("kind: knowledge\nname: help\nversion: 1.0.0\n")
    with pytest.raises(BuildError, match="embedding.alias"):
        build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())


def test_build_refuses_non_image_ids(project, operator):
    with pytest.raises(BuildError) as error:
        build(project / "agent.yaml", operator.load(), "dev", builder=lambda c, i: "oops")
    assert error.value.code == "BUILD_FAILED"


def test_generated_manifest_respects_deploy_contract(project, operator):
    """A budget the manifest contract rejects fails the build, not the deploy."""
    text = (project / "agent.yaml").read_text().replace("budget_usd: 10", "budget_usd: 10, rpm: 5")
    (project / "agent.yaml").write_text(text)
    result = build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())
    assert yaml.safe_load(result.manifest_path.read_text())["model_access"]["rpm"] == 5


def test_path_prompt_is_built(project, operator):
    (project / "prompts").mkdir()
    (project / "prompts/dev.md").write_text("Draft prompt")
    text = (project / "agent.yaml").read_text()
    text = text.replace("prompt: {ref: prompt/helper@1.0.0}", "prompt: {path: prompts/dev.md}")
    (project / "agent.yaml").write_text(text)
    fake = FakeBuilder()
    build(project / "agent.yaml", operator.load(), "dev", builder=fake)
    listing, config = fake.contexts[0]
    assert config["prompt"]["dir"].startswith("artifacts/paths/")
    assert any(p.endswith("dev.md") for p in listing)
    with pytest.raises(RegistryError) as error:
        build(project / "agent.yaml", operator.load(), "dev", mode="locked", builder=fake)
    assert error.value.code == "REF_UNPINNED"


def test_cli_build(project, operator, monkeypatch, capsys):
    monkeypatch.setattr(build_module, "docker_build", lambda c, i: IMAGE)
    monkeypatch.setattr(build_module.build, "__defaults__", None, raising=False)
    original = build_module.build

    def fake_build(*args, **kwargs):
        kwargs["builder"] = lambda c, i: IMAGE
        return original(*args, **kwargs)

    monkeypatch.setattr(build_module, "build", fake_build)
    operator.load()
    assert cli.main(["build", str(project / "agent.yaml"), "--environment", "dev"]) == 0
    out = capsys.readouterr().out
    assert "Built helper" in out and "Next: agentbreeder deploy" in out
    assert cli.main(["build", str(project / "agent.yaml"), "--environment", "dev", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["image"] == IMAGE
    assert cli.main(["build", str(project / "agent.yaml"), "--environment", "missing"]) == 2
    assert "CONTEXT_ENVIRONMENT_UNKNOWN" in capsys.readouterr().err


def test_path_prompt_is_renamed_for_the_runtime(project, operator):
    (project / "prompts").mkdir()
    (project / "prompts/dev.md").write_text("Draft prompt")
    text = (project / "agent.yaml").read_text()
    (project / "agent.yaml").write_text(
        text.replace("prompt: {ref: prompt/helper@1.0.0}", "prompt: {path: prompts/dev.md}")
    )
    fake = FakeBuilder()
    build(project / "agent.yaml", operator.load(), "dev", builder=fake)
    listing, config = fake.contexts[0]
    assert f"{config['prompt']['dir']}/prompt.md" in listing


def test_tool_module_names_must_be_unique(project, operator):
    version(
        project / "registry",
        "tool",
        "dupe",
        {
            "tool.yaml": "functions: [{name: f, module: orders, function: f}]\n",
            "src/orders.py": "",
        },
    )
    (project / "registry/tool/orders/1.0.0/tool.yaml").write_text(
        "functions: [{name: g, module: orders, function: g}]\n"
    )
    text = (project / "agent.yaml").read_text()
    (project / "agent.yaml").write_text(
        text.replace(
            "  - {ref: tool/orders@1.0.0}\n",
            "  - {ref: tool/orders@1.0.0}\n  - {ref: tool/dupe@1.0.0}\n",
        )
    )
    with pytest.raises(BuildError) as error:
        build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())
    assert error.value.code == "TOOL_MODULE_CONFLICT"


def test_build_context_is_readable_under_strict_umask(project, operator):
    import os

    old = os.umask(0o077)
    try:
        fake = FakeBuilder()
        modes = {}

        def builder(context, iidfile):
            for path in [context, *context.rglob("*")]:
                modes[path.relative_to(context).as_posix()] = path.stat().st_mode & 0o777
            return fake(context, iidfile)

        build(project / "agent.yaml", operator.load(), "dev", builder=builder)
    finally:
        os.umask(old)
    assert modes["agent.config.json"] == 0o644 and modes["."] == 0o755
    assert all(m in (0o644, 0o755) for m in modes.values())


@pytest.mark.parametrize("extra", ["port: '8080'\n", "port: 0\n", "health_path: bad\n"])
def test_invalid_runtime_serving_fields_are_build_errors(project, extra):
    from agentbreeder.build import BuildError, _runtime_serving

    runtime = project / "registry/runtime/fake/1.0.0"
    (runtime / "manifest.yaml").write_text("kind: runtime\nname: fake\nversion: 1.0.0\n" + extra)
    with pytest.raises(BuildError, match="invalid port or health_path"):
        _runtime_serving(runtime, "runtime/fake@1.0.0")
