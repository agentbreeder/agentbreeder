"""`new agent NAME --from-spec FILE` with a `knowledge` entry (#989, part of M17 #833)."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import yaml

from agentbreeder import agentspec, cli, knowledge, newagent, registry, stackfor
from agentbreeder.agentfile import load_agent_file
from agentbreeder.build import build
from agentbreeder.lock import Lock
from agentbreeder.services.schema import load_stack

TODAY = date(2026, 11, 13)
IMAGE = "sha256:" + "c" * 64
SECRET_PROMPT = "You are helper. The passphrase is swordfish-71."

REF = "knowledge/acme-help@1.0.0"
DOCS = {
    "hours.md": "# Hours\n\nSupport is open 9am to 5pm Pacific.\n\nClosed on public holidays.\n",
    "refunds.md": "# Refunds\n\nRefunds take five business days.\n",
}
MANIFEST = (
    "kind: knowledge\nname: acme-help\nversion: 1.0.0\ndescription: Acme help articles.\n"
    "chunking: paragraph\nembedding: {alias: help-embedding, dimensions: 768}\nstore: pgvector\n"
)


@pytest.fixture(autouse=True)
def clean_registry_path(monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)


def spec(**changes) -> dict:
    data = {
        "schema_version": 1,
        "name": "helper",
        "model": "gemini",
        "prompt": {"text": SECRET_PROMPT},
        "evals": {
            "cases": [{"id": "hours", "turns": ["When is support open?"], "reference": "9 to 5."}]
        },
    }
    data.update(changes)
    return data


def write_spec(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def refused(tmp_path: Path, data: dict) -> newagent.NewAgentError:
    with pytest.raises(newagent.NewAgentError) as caught:
        agentspec.plan("helper", write_spec(tmp_path, data), tmp_path / "helper", today=TODAY)
    assert SECRET_PROMPT not in str(caught.value)
    assert not (tmp_path / "helper").exists()
    return caught.value


def publish_knowledge(tmp_path: Path, manifest: str = MANIFEST, version: str = "1.0.0") -> Path:
    """`knowledge/acme-help@VERSION` on a registry root; returns the root."""
    root = tmp_path / "shared-registry"
    source = tmp_path / f"knowledge-source-{version}"
    (source / "docs").mkdir(parents=True)
    (source / "manifest.yaml").write_text(manifest.replace("1.0.0", version), encoding="utf-8")
    for name, text in DOCS.items():
        (source / "docs" / name).write_text(text, encoding="utf-8")
    registry.publish(source, root)
    return root


@pytest.fixture
def kb_root(tmp_path, monkeypatch) -> Path:
    root = publish_knowledge(tmp_path)
    monkeypatch.setenv(registry.PATH_ENV, str(root))
    return root


def with_knowledge(**entry) -> dict:
    return spec(knowledge=[{"ref": REF, **entry}])


def plan_for(tmp_path: Path, data: dict):
    path = write_spec(tmp_path, data)
    return agentspec.plan("helper", path, tmp_path / "helper", today=TODAY)


# -- the written project ---------------------------------------------------------------------


def test_a_knowledge_project(tmp_path, kb_root):
    plan, extra = plan_for(tmp_path, with_knowledge(top_k=3, extra_searches=0))
    newagent.create(plan)
    project = tmp_path / "helper"

    agent = load_agent_file(project / "agent.yaml")
    assert [(k.ref, k.top_k, k.extra_searches) for k in agent.knowledge] == [(REF, 3, 0)]
    assert not (project / "registry" / "knowledge").exists()  # not copied

    stack = load_stack(project / "stack.yaml")
    assert stack.environment.models == ["chat", "judge", "help-embedding"]
    text = (project / "stack.yaml").read_text()
    assert "ref: service/postgres-pgvector@1.2.0" in text and "@1.1.0" not in text
    config = yaml.safe_load((project / "stack/litellm/config.yaml").read_text())
    assert config["model_list"][-1] == {
        "model_name": "help-embedding",
        "litellm_params": {
            "model": "gemini/gemini-embedding-2",
            "api_key": "os.environ/GEMINI_API_KEY",
            "dimensions": 768,
        },
    }

    lock = Lock.load(project / "agent.lock")
    assert lock.entries[REF]["source"] == os.path.relpath(kb_root, project)

    assert extra["combination"] == {
        "parts": ["model", "prompt", "evals", "knowledge"],
        "verified": True,
    }
    assert extra["knowledge"] == [
        {
            "ref": REF,
            "alias": "help-embedding",
            "dimensions": 768,
            "embedding_model": "gemini/gemini-embedding-2",
            "top_k": 3,
            "extra_searches": 0,
            "documents": 2,
            "chunks": 3,
        }
    ]
    assert extra["refs"][0]["ref"] == REF and extra["refs"][0]["kind"] == "knowledge"
    assert extra["refs"][0]["root_kind"] == "path" and extra["refs"][0]["runs_code"] is False
    assert extra["runs_code"] is False
    assert extra["keys"] == ["GEMINI_API_KEY"]
    assert plan.summary(dry_run=False)["aliases"] == ["chat", "judge", "help-embedding"]


def test_the_counts_match_knowledge_status(tmp_path, kb_root):
    """The preview's counts are what `knowledge status` expects the runtime to index."""
    _, extra = plan_for(tmp_path, with_knowledge())
    hit = registry.resolve(registry.Ref.parse(REF), [kb_root])
    want = knowledge.expected(hit)
    row = extra["knowledge"][0]
    assert (row["documents"], row["chunks"]) == (want.documents, len(want.chunks))
    assert (row["alias"], row["dimensions"]) == (want.alias, want.dimensions)


def test_defaults_reach_agent_yaml(tmp_path, kb_root):
    plan, extra = plan_for(tmp_path, with_knowledge())
    assert (
        f'knowledge:\n  - {{ref: "{REF}", top_k: 5, extra_searches: 1}}\n'
        in (plan.files["agent.yaml"])
    )
    assert (extra["knowledge"][0]["top_k"], extra["knowledge"][0]["extra_searches"]) == (5, 1)


def test_the_preview_is_the_written_text(tmp_path, kb_root):
    plan, extra = plan_for(tmp_path, with_knowledge())
    preview = agentspec.summary(plan, extra, dry_run=True)
    newagent.create(plan)
    for entry in preview["files"]:
        assert (tmp_path / "helper" / entry["path"]).read_text() == entry["content"]


def test_knowledge_changes_the_plan_digest(tmp_path, kb_root):
    _, without = plan_for(tmp_path, spec())
    _, picked = plan_for(tmp_path, with_knowledge())
    _, top = plan_for(tmp_path, with_knowledge(top_k=4))
    assert len({without["plan_sha256"], picked["plan_sha256"], top["plan_sha256"]}) == 3


def test_a_spec_without_knowledge_keeps_the_starter_postgres(tmp_path):
    plan, extra = plan_for(tmp_path, spec())
    assert "ref: service/postgres-pgvector@1.1.0" in plan.files["stack.yaml"]
    assert extra["knowledge"] == []
    assert "dimensions" not in plan.files["stack/litellm/config.yaml"]
    assert plan.summary(dry_run=True)["aliases"] == ["chat", "judge"]


def test_openai_uses_its_embedding_model(tmp_path, kb_root):
    plan, extra = plan_for(tmp_path, with_knowledge() | {"model": "openai"})
    newagent.create(plan)
    config = yaml.safe_load((tmp_path / "helper/stack/litellm/config.yaml").read_text())
    params = config["model_list"][-1]["litellm_params"]
    assert params["model"] == "openai/text-embedding-3-small"
    assert params["api_key"] == "os.environ/OPENAI_API_KEY"
    assert extra["knowledge"][0]["embedding_model"] == "openai/text-embedding-3-small"
    # openai ran end to end with one knowledge version (#1020), as gemini did (#833).
    assert extra["combination"]["verified"] is True


def test_the_verified_combinations():
    """The #833 runs: gemini with one knowledge version, one path tool, or both; never MCP."""
    # Keyed by the runtime's name since #1083 F1.
    # #1058 and #1059 AC added sub-agents (with one tool) and skills, on runtime/adk@1.5.0 only
    # (`PART_RUNTIMES`; tests/test_agent_spec_v2.py).
    assert agentspec.VERIFIED == {
        "adk": frozenset(
            {
                (),
                ("knowledge",),
                ("tools",),
                ("knowledge", "tools"),
                ("subagents",),
                ("subagents", "tools"),
                ("skills",),
            }
        ),
        # The starter and one tool on runtime/langgraph@1.0.0, gemini only
        # (verify-langgraph-adk150.md; tests/test_runtime_profile.py).
        "langgraph": frozenset({(), ("tools",)}),
    }
    assert agentspec.BASE_RUNTIMES == {
        "adk": newagent.RUNTIME,
        "langgraph": "runtime/langgraph@1.0.0",
    }
    assert agentspec.RUNTIME_PROVIDERS == {"langgraph": {"gemini"}}
    assert agentspec.VERIFIED_TOOLS == 1
    # openrouter and openai ran without tools (#1020)
    assert agentspec.TOOL_PROVIDERS == {"gemini"}
    assert not any("mcp" in parts for runs in agentspec.VERIFIED.values() for parts in runs)
    # The registry label is a separate table of runtime versions (owner decision 2026-11-17).
    assert agentspec.RUNTIMES_RUN == {
        "runtime/adk@1.3.0",
        "runtime/adk@1.4.0",
        "runtime/adk@1.5.0",  # verify-langgraph-adk150.md
        "runtime/langgraph@1.0.0",  # verify-langgraph-adk150.md
    }
    assert newagent.RUNTIME in agentspec.RUNTIMES_RUN
    assert agentspec.runtime_verified(newagent.RUNTIME)
    assert agentspec.runtime_verified("runtime/adk@1.3.0")  # Northwind 61/61 (#1095)
    assert not agentspec.runtime_verified("runtime/adk@1.2.0")
    assert agentspec.runtime_verified("runtime/langgraph@1.0.0")
    assert not agentspec.runtime_verified("runtime/deepagents@1.0.0")  # #1083 F5: not verified


def publish_echo(tmp_path: Path, root: Path, *names: str) -> list[str]:
    sources = tmp_path / "tool-sources"
    for name in names:
        registry.publish(registry.scaffold("tool", name, sources), root)
    return [f"tool/{name}@0.1.0" for name in names]


def test_knowledge_with_one_tool_is_verified(tmp_path, kb_root):
    tools = publish_echo(tmp_path, kb_root, "echo")
    _plan, extra = plan_for(tmp_path, with_knowledge() | {"tools": tools})
    assert extra["combination"] == {
        "parts": ["model", "prompt", "evals", "knowledge", "tools"],
        "verified": True,
    }
    assert [row["kind"] for row in extra["refs"]] == ["knowledge", "tool"]
    assert extra["runs_code"] is True


@pytest.mark.parametrize(
    ("changes", "parts"),
    [
        ({"mcp": [{"ref": "mcp/github@1.0.0", "tools": ["get_me"]}]}, {"knowledge", "mcp"}),
        ({"tools": 2}, {"knowledge", "tools"}),  # two tools: every run had one
        ({"model": "openai", "tools": 1}, {"knowledge", "tools"}),  # no tool ran on openai
        (
            {"tools": 1, "mcp": [{"ref": "mcp/github@1.0.0", "tools": ["get_me"]}]},
            {"knowledge", "mcp", "tools"},
        ),
    ],
)
def test_knowledge_beyond_what_was_run_is_unverified(tmp_path, kb_root, changes, parts):
    if "tools" in changes:
        changes = changes | {
            "tools": publish_echo(tmp_path, kb_root, *["echo", "clock"][: changes["tools"]])
        }
    _plan, extra = plan_for(tmp_path, with_knowledge() | changes)
    assert parts <= set(extra["combination"]["parts"])
    assert extra["combination"]["verified"] is False


def test_a_locked_build_carries_the_embedding_alias(tmp_path, kb_root, identity_env):
    plan, _ = plan_for(tmp_path, with_knowledge())
    newagent.create(plan)
    project = tmp_path / "helper"
    identity = {"provider": "static", "ref": "agent-1", "environment": "dev"}
    identity_env.config["gateways"] = {
        "litellm": {
            "type": "litellm",
            "url": "http://127.0.0.1:4000",
            "agent_url": "http://host.docker.internal:4000",
        }
    }
    identity_env.config["environments"]["dev"].update(
        {
            "gateway": "litellm",
            "models": ["chat", "judge", "help-embedding"],
            "ceilings": {"budget_usd": 5, "rpm": 60},
            "bindings": {"DATABASE_URL": "secret://helper-db"},
            "agents": {"helper": {"identity": identity, "candidate_identity": dict(identity)}},
        }
    )

    def builder(context, iidfile):
        assert (context / "artifacts" / "knowledge" / "acme-help" / "docs").is_dir()
        return IMAGE

    result = build(
        project / "agent.yaml", identity_env.load(), "dev", mode="locked", builder=builder
    )
    assert result.image == IMAGE and result.aliases == ["chat", "help-embedding"]


# -- refusals ----------------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["anthropic", "openrouter"])
def test_a_provider_without_an_embedding_model_is_refused(tmp_path, kb_root, provider):
    data = with_knowledge() | {"model": provider}
    if provider == "openrouter":
        data["model_id"] = "openrouter/google/gemini-x"
    error = refused(tmp_path, data)
    assert error.code == "SPEC_UNSUPPORTED" and "embedding model" in str(error)


@pytest.mark.parametrize(
    "knowledge_entries",
    [
        ["x"],
        [1],
        [{"ref": REF}, {"ref": REF}],
        [{"ref": "knowledge/acme-help"}],
        [{"ref": "acme-help"}],
        [{"ref": "tool/echo@0.1.0"}],
        [{"ref": REF, "top_k": 0}],
        [{"ref": REF, "top_k": 21}],
        [{"ref": REF, "top_k": True}],
        [{"ref": REF, "extra_searches": 6}],
        [{"ref": REF, "extra_searches": "1"}],
        [{"ref": REF, "store": "pgvector"}],
        [{"ref": "knowledge/" + "a" * 300 + "@1.0.0"}],
        {"ref": REF},
    ],
)
def test_bad_knowledge_entries_are_refused(tmp_path, kb_root, knowledge_entries):
    assert refused(tmp_path, spec(knowledge=knowledge_entries)).code == "SPEC_INVALID"


def test_an_unknown_version_is_refused_with_the_registry_code(tmp_path, kb_root):
    error = refused(tmp_path, spec(knowledge=[{"ref": "knowledge/acme-help@9.9.9"}]))
    assert error.code == "REF_NOT_FOUND"


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        ("store: pgvector", "store: chroma", "KNOWLEDGE_INVALID"),
        ("chunking: paragraph", "chunking: sentence", "KNOWLEDGE_INVALID"),
        ("dimensions: 768", "dimensions: 0", "KNOWLEDGE_INVALID"),
        ("dimensions: 768", "dimensions: true", "KNOWLEDGE_INVALID"),
        (
            "embedding: {alias: help-embedding, dimensions: 768}",
            "embedding: [x]",
            "KNOWLEDGE_INVALID",
        ),
        ("dimensions: 768", "dimensions: 4000", "SPEC_UNSUPPORTED"),
        ("alias: help-embedding", "alias: chat", "ALIAS_UNSUPPORTED"),
        ("alias: help-embedding", "alias: judge", "ALIAS_UNSUPPORTED"),
        ("alias: help-embedding", "alias: org/embed", "ALIAS_UNSUPPORTED"),
        ("alias: help-embedding", 'alias: ""', "ALIAS_UNSUPPORTED"),
        ("embedding: {alias: help-embedding, dimensions: 768}\n", "", "ALIAS_UNSUPPORTED"),
    ],
)
def test_bad_knowledge_versions_are_refused(tmp_path, monkeypatch, old, new, code):
    root = publish_knowledge(tmp_path, MANIFEST.replace(old, new))
    monkeypatch.setenv(registry.PATH_ENV, str(root))
    error = refused(tmp_path, with_knowledge())
    assert error.code == code
    assert "\n" not in str(error)


def test_openai_dimensions_are_capped(tmp_path, monkeypatch):
    root = publish_knowledge(tmp_path, MANIFEST.replace("768", "2000"))
    monkeypatch.setenv(registry.PATH_ENV, str(root))
    plan_for(tmp_path, with_knowledge())  # gemini: up to 3072
    error = refused(tmp_path, with_knowledge() | {"model": "openai"})
    assert error.code == "SPEC_UNSUPPORTED" and "1536" in str(error)


def test_a_version_without_store_or_chunking_is_accepted(tmp_path, monkeypatch):
    manifest = MANIFEST.replace("chunking: paragraph\n", "").replace("store: pgvector\n", "")
    monkeypatch.setenv(registry.PATH_ENV, str(publish_knowledge(tmp_path, manifest)))
    _, extra = plan_for(tmp_path, with_knowledge())
    assert extra["knowledge"][0]["alias"] == "help-embedding"


def test_a_version_without_dimensions_gets_the_runtime_default(tmp_path, monkeypatch):
    manifest = MANIFEST.replace(", dimensions: 768", "")
    monkeypatch.setenv(registry.PATH_ENV, str(publish_knowledge(tmp_path, manifest)))
    plan, extra = plan_for(tmp_path, with_knowledge())
    assert extra["knowledge"][0]["dimensions"] == knowledge.DEFAULT_DIMENSIONS
    assert "dimensions: 768" in plan.files["stack/litellm/config.yaml"]


def test_too_many_chunks_are_refused(tmp_path, kb_root, monkeypatch):
    monkeypatch.setattr(knowledge, "MAX_ROWS", 2)
    assert refused(tmp_path, with_knowledge()).code == "KNOWLEDGE_TOO_LARGE"


def test_a_knowledge_version_replaced_before_the_lock_is_refused(tmp_path, kb_root):
    plan, _ = plan_for(tmp_path, with_knowledge())
    plan = replace(plan, pinned={REF: "sha256:" + "0" * 64})
    with pytest.raises(newagent.NewAgentError) as caught:
        newagent.create(plan)
    assert caught.value.code == "DIGEST_MISMATCH"
    assert not (tmp_path / "helper").exists()


def test_a_template_dropping_knowledge_is_refused(tmp_path, kb_root, monkeypatch):
    real = agentspec._agent_yaml

    def dropped(data, today, *rest):
        return real(data, today, *rest).replace("knowledge:\n", "unused_knowledge:\n", 1)

    monkeypatch.setattr(agentspec, "_agent_yaml", dropped)
    assert refused(tmp_path, with_knowledge()).code == "SPEC_INVALID"
    monkeypatch.setattr(
        agentspec,
        "_agent_yaml",
        lambda data, today, *rest: real(data, today, *rest).replace("top_k: 5", "top_k: 6"),
    )
    assert refused(tmp_path, with_knowledge()).code == "TEMPLATE_INVALID"


def test_a_gateway_config_without_the_embedding_is_refused(tmp_path, kb_root, monkeypatch):
    real = stackfor.render

    def without(*args, **kwargs):
        files = real(*args, **kwargs)
        head, _, _ = files[stackfor.LITELLM_CONFIG].partition('  - model_name: "help-embedding"')
        files[stackfor.LITELLM_CONFIG] = head + stackfor.LITELLM_TAIL
        return files

    monkeypatch.setattr(stackfor, "render", without)
    assert refused(tmp_path, with_knowledge()).code == "TEMPLATE_INVALID"


@pytest.mark.parametrize(
    "served",
    [
        [("chat", "m"), ("judge", "m")],
        [("chat", "m"), ("judge", "m"), ("embed", "other")],
        [("chat", "m"), ("judge", "e"), ("embed", "e")],
        [("chat", "m"), ("judge", "m"), ("embed", "e"), ("embed", "e")],
    ],
)
def test_check_gateway_wants_exactly_the_embedding(tmp_path, served):
    config = tmp_path / "config.yaml"
    config.write_text(
        "model_list:\n"
        + "".join(
            f"  - model_name: {alias}\n    litellm_params: {{model: gemini/{model}}}\n"
            for alias, model in served
        )
    )
    with pytest.raises(newagent.NewAgentError) as caught:
        newagent.check_gateway(config, {"chat", "judge"}, "gemini/m", ("embed", "gemini/e"))
    assert caught.value.code == "TEMPLATE_INVALID"


def test_check_gateway_accepts_the_embedding(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "model_list:\n"
        "  - model_name: chat\n    litellm_params: {model: gemini/m}\n"
        "  - model_name: judge\n    litellm_params: {model: gemini/m}\n"
        "  - model_name: embed\n    litellm_params: {model: gemini/e, dimensions: 8}\n"
    )
    newagent.check_gateway(config, {"chat", "judge"}, "gemini/m", ("embed", "gemini/e"))


# -- the CLI -----------------------------------------------------------------------------------


def test_cli_dry_run_json(tmp_path, kb_root, capsys):
    path = write_spec(tmp_path, with_knowledge())
    code = cli.main(
        ["new", "agent", "helper", "--from-spec", str(path), "--dir", str(tmp_path / "helper")]
        + ["--dry-run", "--json"]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["dry_run"] is True
    assert out["knowledge"][0]["alias"] == "help-embedding"
    assert out["combination"] == {
        "parts": ["model", "prompt", "evals", "knowledge"],
        "verified": True,
    }
    assert out["aliases"] == ["chat", "judge", "help-embedding"]
    stack = next(f for f in out["files"] if f["path"] == "stack.yaml")
    assert "postgres-pgvector@1.2.0" in stack["content"]
    assert not (tmp_path / "helper").exists()


def test_cli_text_output(tmp_path, kb_root, capsys):
    path = write_spec(tmp_path, with_knowledge())
    code = cli.main(
        ["new", "agent", "helper", "--from-spec", str(path), "--dir", str(tmp_path / "helper")]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert f"Knowledge: {REF} (2 documents, 3 chunks), embedded as help-embedding" in out
    assert "has not been run end to end" not in out  # verified since #833
    assert SECRET_PROMPT not in out


def test_cli_text_output_openai_knowledge_is_verified(tmp_path, kb_root, capsys):
    path = write_spec(tmp_path, with_knowledge() | {"model": "openai"})
    code = cli.main(
        ["new", "agent", "helper", "--from-spec", str(path), "--dir", str(tmp_path / "helper")]
        + ["--dry-run"]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "has not been run end to end" not in out  # #1020
    assert "config-only" not in out


def test_cli_refusal_json(tmp_path, kb_root, capsys):
    path = write_spec(tmp_path, with_knowledge() | {"model": "anthropic"})
    code = cli.main(["new", "agent", "helper", "--from-spec", str(path), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["error"]["code"] == "SPEC_UNSUPPORTED"
    assert not (Path.cwd() / "helper").exists()


def test_a_fifo_manifest_is_refused_without_blocking(tmp_path, kb_root):
    """A FIFO in a shared registry root must not hang the CLI (the safe hash reads first)."""
    manifest = kb_root / "knowledge" / "acme-help" / "1.0.0" / "manifest.yaml"
    manifest.unlink()
    os.mkfifo(manifest)
    error = refused(tmp_path, with_knowledge())
    assert error.code == "MANIFEST_INVALID" and "not a regular file" in str(error)


@pytest.mark.parametrize("kind", ["fifo", "directory", "missing"])
def test_strict_yaml_reads_only_regular_files(tmp_path, kind):
    from agentbreeder.strict_yaml import StrictYamlError, load_file

    path = tmp_path / "manifest.yaml"
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
    with pytest.raises(StrictYamlError):
        load_file(path)


def test_strict_yaml_closes_what_it_refuses(tmp_path):
    from agentbreeder.strict_yaml import StrictYamlError, load_file

    (tmp_path / "dir.yaml").mkdir()
    before = len(os.listdir("/dev/fd"))
    for _ in range(20):
        with pytest.raises(StrictYamlError, match="not a regular file"):
            load_file(tmp_path / "dir.yaml")
    assert len(os.listdir("/dev/fd")) <= before


def test_strict_yaml_still_reads_a_regular_file_and_a_link_to_one(tmp_path):
    from agentbreeder.strict_yaml import load_file

    (tmp_path / "real.yaml").write_text("a: 1\n", encoding="utf-8")
    (tmp_path / "link.yaml").symlink_to(tmp_path / "real.yaml")
    assert load_file(tmp_path / "real.yaml") == load_file(tmp_path / "link.yaml") == {"a": 1}


def test_a_version_changed_while_it_is_read_is_refused(tmp_path, kb_root, monkeypatch):
    real = knowledge.expected

    def changing(hit):
        result = real(hit)
        (hit.path / "docs" / "hours.md").write_text("# Hours\n\nChanged.\n", encoding="utf-8")
        return result

    monkeypatch.setattr(knowledge, "expected", changing)
    error = refused(tmp_path, with_knowledge())
    assert error.code == "DIGEST_MISMATCH" and "changed while it was read" in str(error)


def test_an_empty_store_is_like_a_missing_one(tmp_path, monkeypatch):
    manifest = MANIFEST.replace("store: pgvector", "store: null")
    monkeypatch.setenv(registry.PATH_ENV, str(publish_knowledge(tmp_path, manifest)))
    _, extra = plan_for(tmp_path, with_knowledge())
    assert extra["knowledge"][0]["ref"] == REF
