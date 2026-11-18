"""#1083 F1 golden test: making the engine data-driven changes nothing for `runtime/adk`.

Northwind and the `new agent` starter build byte-identically on `runtime/adk` 1.2.0, 1.3.0 and
1.4.0: the whole build context (every file's path, mode and bytes), the deploy manifest, the
candidate manifest and the deploy template. The digests in `fixtures/runtime_golden.json` were
written from `main` before F1 (commit 732d6f17) and must never be regenerated to make a change
pass: a difference means an existing ADK build changed.

The refusals those runtimes gave before are pinned too (`MCP_TOOL_RESERVED`,
`MCP_RUNTIME_UNSUPPORTED` and the knowledge count).

What the digests depend on besides the engine: the Northwind example's files, the starter's
templates (`newagent`) and the three runtime versions. If one of those inputs is changed on
purpose (a new Northwind tool, a starter template edit), the case fails for that reason and not
because of the engine: check that the same commit changed that input, that `git stash`-ing only
the engine change still fails, and then remove that case's entry from the fixture and rewrite it
with the command below, saying so in the pull request. Not hashed here: `agent.lock`,
`build.json` and a released package's bytes.

    AGENTBREEDER_WRITE_GOLDEN=1 pytest tests/test_runtime_golden.py   # only for a NEW case
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import date
from pathlib import Path

import pytest

from agentbreeder import newagent, registry
from agentbreeder.build import BuildError, build

FIXTURE = Path(__file__).parent / "fixtures" / "runtime_golden.json"
NORTHWIND = Path(registry.__file__).parent / "examples" / "northwind"
VERSIONS = ("1.2.0", "1.3.0", "1.4.0")
IMAGE = "sha256:" + "c" * 64
TODAY = date(2026, 11, 17)


def _digest_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            digest.update(f"d {relative} {path.stat().st_mode & 0o777:o}\0".encode())
        else:
            digest.update(f"f {relative} {path.stat().st_mode & 0o777:o}\0".encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


class Recorder:
    """Stands in for `docker build`: hashes the context it is handed."""

    def __init__(self):
        self.context = None
        self.config = None

    def __call__(self, context: Path, iidfile: Path) -> str:
        self.context = _digest_tree(context)
        self.config = hashlib.sha256((context / "agent.config.json").read_bytes()).hexdigest()
        return IMAGE


def _operator(identity_env, name: str, aliases: list[str]):
    identity_env.config["gateways"] = {
        "litellm-local": {
            "type": "litellm",
            "url": "http://127.0.0.1:4000",
            "agent_url": "http://host.docker.internal:4000",
        }
    }
    identity = {"provider": "static", "ref": "agent-1", "environment": "dev"}
    identity_env.config["environments"]["dev"].update(
        {
            "gateway": "litellm-local",
            "models": aliases,
            "ceilings": {"budget_usd": 50, "rpm": 300},
            "bindings": {"DATABASE_URL": "secret://agent-db"},
            "agents": {name: {"identity": identity, "candidate_identity": dict(identity)}},
        }
    )
    return identity_env.load()


def _pin(project: Path, version: str) -> None:
    path = project / "agent.yaml"
    text, count = re.subn(
        r"^runtime: runtime/adk@[0-9.]+$",
        f"runtime: runtime/adk@{version}",
        path.read_text(),
        count=1,
        flags=re.M,
    )
    assert count == 1
    path.write_text(text)


def _northwind(tmp_path: Path, version: str) -> Path:
    project = tmp_path / "northwind"
    shutil.copytree(NORTHWIND, project, ignore=shutil.ignore_patterns("__pycache__"))
    _pin(project, version)
    return project


def _starter(tmp_path: Path, version: str) -> Path:
    plan = newagent.plan("golden-starter", "gemini", tmp_path / "golden-starter", today=TODAY)
    newagent.create(plan)
    _pin(plan.directory, version)
    return plan.directory


def _built(project: Path, operator) -> dict:
    recorder = Recorder()
    result = build(project / "agent.yaml", operator, "dev", builder=recorder)

    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    return {
        "context": recorder.context,
        "agent_config": recorder.config,
        "manifest": sha(result.manifest_path),
        "candidate_manifest": sha(result.candidate_manifest_path),
        # As pinned before F1, when the template had no `runtime` field: package.json leaves
        # an unset one out (`package.Package.to_bytes`), so these bytes are what is written.
        "deploy_template": hashlib.sha256(
            json.dumps(
                {
                    key: value
                    for key, value in result.deploy.model_dump(mode="json").items()
                    if not (key == "runtime" and value is None)
                },
                sort_keys=True,
            ).encode()
        ).hexdigest(),
        "aliases": result.aliases,
    }


CASES = [(kind, version) for kind in ("northwind", "starter") for version in VERSIONS]


@pytest.mark.parametrize("kind,version", CASES)
def test_adk_builds_are_byte_identical(tmp_path, identity_env, monkeypatch, kind, version):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    if kind == "northwind":
        project = _northwind(tmp_path, version)
        operator = _operator(
            identity_env, "northwind-support", ["support-model", "support-embedding"]
        )
    else:
        project = _starter(tmp_path, version)
        operator = _operator(identity_env, "golden-starter", ["chat", "judge"])
    built = _built(project, operator)
    key = f"{kind}@{version}"
    golden = json.loads(FIXTURE.read_text()) if FIXTURE.exists() else {}
    if os.environ.get("AGENTBREEDER_WRITE_GOLDEN") and key not in golden:
        golden[key] = built
        FIXTURE.write_text(json.dumps(dict(sorted(golden.items())), indent=1) + "\n")
    assert built == golden[key], f"an existing ADK build changed: {key}"


def _refused(project: Path, operator) -> BuildError:
    with pytest.raises(BuildError) as caught:
        build(project / "agent.yaml", operator, "dev", builder=Recorder())
    return caught.value


def _add_mcp(project: Path, tools: str) -> None:
    root = project / "registry" / "mcp" / "docs" / "1.0.0"
    root.mkdir(parents=True)
    (root / "manifest.yaml").write_text("kind: mcp\nname: docs\nversion: 1.0.0\n")
    (root / "mcp.yaml").write_text(
        'upstream:\n  remote: {url: "https://mcp.example.com/mcp"}\n'
        "auth:\n  type: bearer\n  secret: token\n"
        "secrets:\n  token: {import: DOCS_MCP_TOKEN}\n"
        "tools:\n  search: {effect: read}\n  transfer_to_agent: {effect: read}\n"
    )
    with (project / "agent.yaml").open("a") as handle:
        handle.write(f"\nmcp:\n  - {{ref: mcp/docs@1.0.0, tools: [{tools}]}}\n")


@pytest.mark.parametrize("version", VERSIONS)
def test_adk_still_refuses_a_reserved_mcp_tool_name(tmp_path, identity_env, monkeypatch, version):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    project = _starter(tmp_path, version)
    _add_mcp(project, "transfer_to_agent")
    error = _refused(project, _operator(identity_env, "golden-starter", ["chat", "judge"]))
    assert error.code == "MCP_TOOL_RESERVED"
    assert "mcp/docs@1.0.0: transfer_to_agent is reserved by" in str(error)


@pytest.mark.parametrize("version", ("1.0.4", "1.0.5", "1.0.6"))
def test_adk_before_1_1_0_still_refuses_mcp(tmp_path, identity_env, monkeypatch, version):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    project = _starter(tmp_path, version)
    _add_mcp(project, "search")
    error = _refused(project, _operator(identity_env, "golden-starter", ["chat", "judge"]))
    assert error.code == "MCP_RUNTIME_UNSUPPORTED"
    assert f"runtime/adk@{version}" in str(error)


@pytest.mark.parametrize("version", VERSIONS)
def test_adk_still_refuses_two_knowledge_sets(tmp_path, identity_env, monkeypatch, version):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    project = _northwind(tmp_path, version)
    first = project / "registry" / "knowledge" / "northwind-help" / "1.0.0"
    second = project / "registry" / "knowledge" / "more-help" / "1.0.0"
    shutil.copytree(first, second)
    manifest = second / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("name: northwind-help", "name: more-help"))
    path = project / "agent.yaml"
    path.write_text(
        path.read_text().replace(
            "knowledge:\n", "knowledge:\n  - {ref: knowledge/more-help@1.0.0, top_k: 3}\n", 1
        )
    )
    operator = _operator(identity_env, "northwind-support", ["support-model", "support-embedding"])
    # Before F1 the agent file itself refused a second set (AGENT_INVALID, "Invalid
    # knowledge"); the count is now the runtime's own limit, refused by build.
    error = _refused(project, operator)
    assert error.code == "RUNTIME_LIMIT"
    assert f"runtime/adk@{version} takes at most 1 knowledge: entry" in str(error)
    assert "the agent has 2" in str(error)
