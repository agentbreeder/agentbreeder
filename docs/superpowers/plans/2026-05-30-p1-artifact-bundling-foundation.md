# P1 — Artifact Bundling Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a deployed agent container able to (a) import the AgentBreeder runtime (`engine.*`, `api.services.*`), (b) receive its `prompts/<name>` prompt baked in at deploy time, and (c) reach artifact backends through an explicit URL contract instead of values scraped off the deploy host — so prompts, first-party tools, RAG and memory stop silently breaking on AWS/GCP/Azure.

**Architecture:** Three seams, all inside the existing sacred pipeline (no order change). (1) Each Python runtime's `get_requirements()` adds a pinned `agentbreeder` requirement so `pip install -r requirements.txt` brings the engine into the image. (2) `resolve_dependencies(config, project_root)` resolves `prompts/<name>` into `config.prompts.system` (→ `AGENT_SYSTEM_PROMPT`) and validates first-party/local tool refs, failing soft with warnings. (3) The resolver's backend wiring reads explicit `agent.yaml` `backend_url` fields and only falls back to the deploy host's local `REDIS_URL`/`DATABASE_URL`/`NEO4J_URL` when `AGENTBREEDER_ALLOW_LOCAL_BACKENDS=1`; it exposes `KB_PGVECTOR_DSN` as the seam P2 fills.

**Tech Stack:** Python 3.11, pydantic v2, pytest, importlib.metadata, JSON Schema (`engine/schema/agent.schema.json`).

**Branch:** `feat/cloud-agnostic-p1-bundling`

---

### Task 1: `runtime_support_requirement()` — the bundling helper

**Files:**
- Modify: `engine/runtimes/base.py` (add helper near `_get_litellm_requirements`, ~line 41)
- Test: `tests/unit/runtimes/test_runtime_support_requirement.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/runtimes/test_runtime_support_requirement.py
import importlib
import pytest
from engine.runtimes import base


def test_returns_pinned_agentbreeder_when_installed(monkeypatch):
    monkeypatch.delenv("AGENTBREEDER_RUNTIME_REQUIREMENT", raising=False)
    monkeypatch.setattr(base, "version", lambda dist: "9.9.9")
    assert base.runtime_support_requirement() == "agentbreeder==9.9.9"


def test_falls_back_to_unpinned_when_dist_absent(monkeypatch):
    monkeypatch.delenv("AGENTBREEDER_RUNTIME_REQUIREMENT", raising=False)

    def _raise(_dist):
        raise base.PackageNotFoundError

    monkeypatch.setattr(base, "version", _raise)
    assert base.runtime_support_requirement() == "agentbreeder"


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_RUNTIME_REQUIREMENT", "agentbreeder @ file:///wheels/ab.whl")
    assert base.runtime_support_requirement() == "agentbreeder @ file:///wheels/ab.whl"


def test_empty_override_opts_out(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_RUNTIME_REQUIREMENT", "   ")
    assert base.runtime_support_requirement() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/runtimes/test_runtime_support_requirement.py -v`
Expected: FAIL — `AttributeError: module 'engine.runtimes.base' has no attribute 'runtime_support_requirement'`

- [ ] **Step 3: Write minimal implementation**

Add near the top of `engine/runtimes/base.py` (after the existing `from __future__` / imports block — the module currently imports `from engine.config_parser import AgentConfig`):

```python
import os
from importlib.metadata import PackageNotFoundError, version
```

Then add the helper just below `_get_litellm_requirements` (~line 41):

```python
def runtime_support_requirement() -> str | None:
    """Return the pip requirement that bundles the AgentBreeder runtime into an
    agent image, or ``None`` when bundling is disabled.

    A deployed agent's ``server.py`` template and any resolved first-party tools
    import from ``engine.*`` / ``api.services.*``. Those modules ship in the
    ``agentbreeder`` distribution, so adding it to the image's ``requirements.txt``
    is what makes those imports resolve in the container. Without it they
    ``ImportError`` at runtime on every non-local target.

    Override with ``AGENTBREEDER_RUNTIME_REQUIREMENT`` (a pinned version, a VCS
    URL, or a local wheel path). Set it to an empty/whitespace string to opt out
    — useful for fully self-contained agents that import nothing from the engine.
    """
    override = os.getenv("AGENTBREEDER_RUNTIME_REQUIREMENT")
    if override is not None:
        override = override.strip()
        return override or None
    try:
        return f"agentbreeder=={version('agentbreeder')}"
    except PackageNotFoundError:
        # Source checkout without the dist installed (e.g. CI unit tests):
        # fall back to an unpinned requirement so images still build.
        return "agentbreeder"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/runtimes/test_runtime_support_requirement.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add engine/runtimes/base.py tests/unit/runtimes/test_runtime_support_requirement.py
git commit -m "feat(runtimes): add runtime_support_requirement() to bundle engine into agent images"
```

---

### Task 2: Wire the requirement into every Python runtime's `get_requirements()`

**Files:**
- Modify: `engine/runtimes/langgraph.py:153-181` (`get_requirements`)
- Modify: `engine/runtimes/claude_sdk.py`, `engine/runtimes/openai_agents.py`, `engine/runtimes/crewai.py`, `engine/runtimes/google_adk.py` (their `get_requirements` returns)
- Test: `tests/unit/runtimes/test_get_requirements_bundles_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/runtimes/test_get_requirements_bundles_engine.py
import pytest
from engine.config_parser import AgentConfig, ModelConfig, DeployConfig
from engine.runtimes.langgraph import LangGraphRuntime
from engine.runtimes.claude_sdk import ClaudeSDKRuntime
from engine.runtimes.openai_agents import OpenAIAgentsRuntime
from engine.runtimes.crewai import CrewAIRuntime
from engine.runtimes.google_adk import GoogleADKRuntime


def _cfg(model="claude-sonnet-4"):
    return AgentConfig(
        name="x", version="1.0.0", team="t", owner="o@e.com", framework="langgraph",
        model=ModelConfig(primary=model), deploy=DeployConfig(cloud="aws"),
    )


@pytest.mark.parametrize("runtime", [
    LangGraphRuntime(), ClaudeSDKRuntime(), OpenAIAgentsRuntime(),
    CrewAIRuntime(), GoogleADKRuntime(),
])
def test_get_requirements_includes_agentbreeder(runtime, monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_RUNTIME_REQUIREMENT", "agentbreeder==1.2.3")
    deps = runtime.get_requirements(_cfg())
    assert any(d.startswith("agentbreeder==1.2.3") for d in deps), deps


def test_opt_out_excludes_agentbreeder(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_RUNTIME_REQUIREMENT", "")
    deps = LangGraphRuntime().get_requirements(_cfg())
    assert not any(d.startswith("agentbreeder") for d in deps), deps
```

> If a runtime constructor or `AgentConfig` field name differs from the above, adjust the
> fixture to the real signature found in `engine/config_parser.py` — do not change the assertion.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/runtimes/test_get_requirements_bundles_engine.py -v`
Expected: FAIL — assertion error, `agentbreeder==1.2.3` not in deps.

- [ ] **Step 3: Write minimal implementation**

In `engine/runtimes/langgraph.py`, import the helper (extend the existing
`from engine.runtimes.base import (...)` block) by adding `runtime_support_requirement`,
then append at the end of `get_requirements`, just before `return deps`:

```python
        support = runtime_support_requirement()
        if support:
            deps.append(support)
        return deps
```

Apply the identical two changes (import + append-before-return) to the `get_requirements`
of `claude_sdk.py`, `openai_agents.py`, `crewai.py`, and `google_adk.py`. Each already
builds a `deps`/list — append `support` the same way. (CrewAI's may be named differently;
append to whatever list it returns.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/runtimes/test_get_requirements_bundles_engine.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Run the full runtimes suite (no regressions)**

Run: `pytest tests/unit/runtimes/ -q`
Expected: PASS (existing litellm/requirements tests still green)

- [ ] **Step 6: Commit**

```bash
git add engine/runtimes/*.py tests/unit/runtimes/test_get_requirements_bundles_engine.py
git commit -m "feat(runtimes): bundle agentbreeder runtime in all Python runtime images"
```

---

### Task 3: Add backend-URL fields to `agent.yaml` (config + schema + docs)

**Files:**
- Modify: `engine/config_parser.py:123-152` (`KnowledgeBaseRef`, `MemoryConfig`)
- Modify: `engine/schema/agent.schema.json` (knowledge_bases items, memory object)
- Modify: `website/content/docs/agent-yaml.mdx` (same commit — D4)
- Test: `tests/unit/test_config_parser_backend_fields.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_config_parser_backend_fields.py
from engine.config_parser import KnowledgeBaseRef, MemoryConfig


def test_kb_ref_accepts_backend_url():
    kb = KnowledgeBaseRef(ref="kb/product-docs", backend_url="postgresql://h/db")
    assert kb.backend_url == "postgresql://h/db"


def test_kb_ref_backend_url_optional():
    assert KnowledgeBaseRef(ref="kb/x").backend_url is None


def test_memory_accepts_backend_and_url():
    m = MemoryConfig(stores=["mem/sessions"], backend="redis", backend_url="redis://h:6379")
    assert m.backend == "redis"
    assert m.backend_url == "redis://h:6379"


def test_memory_backend_fields_optional():
    m = MemoryConfig(stores=["mem/x"])
    assert m.backend is None and m.backend_url is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config_parser_backend_fields.py -v`
Expected: FAIL — `ValidationError: unexpected keyword argument 'backend_url'` (pydantic forbids extra) or `AttributeError`.

- [ ] **Step 3: Write minimal implementation**

In `engine/config_parser.py`, add the fields. `KnowledgeBaseRef` (currently at `:123`):

```python
class KnowledgeBaseRef(BaseModel):
    ref: str
    backend_url: str | None = None  # explicit vector-store DSN (pgvector) or graph URL; cloud-reachable
```

`MemoryConfig` (currently at `:148`):

```python
class MemoryConfig(BaseModel):
    stores: list[str] = Field(default_factory=list)
    backend: str | None = None       # "redis" | "postgresql" | None (None → resolve from registry)
    backend_url: str | None = None   # explicit, cloud-reachable connection string
```

> Preserve any existing fields/validators on these models — only add the new optional fields.

- [ ] **Step 4: Update the JSON Schema (same commit)**

In `engine/schema/agent.schema.json`, under the `knowledge_bases` array item schema add:

```json
"backend_url": { "type": "string", "description": "Explicit cloud-reachable vector-store DSN or graph URL for this knowledge base." }
```

Under the `memory` object schema add:

```json
"backend": { "type": "string", "enum": ["redis", "postgresql"], "description": "Memory backend type. Omit to resolve from the registry." },
"backend_url": { "type": "string", "description": "Explicit cloud-reachable connection string for the memory backend." }
```

- [ ] **Step 5: Update docs (same commit — D4)**

In `website/content/docs/agent-yaml.mdx`, in the `knowledge_bases` and `memory` sections, document
`backend_url` (and `memory.backend`) with a one-line note: *"In cloud deployments, set `backend_url`
to a reachable managed backend, or let `agentbreeder deploy` provision one (see RAG/Memory docs)."*

- [ ] **Step 6: Run test + schema validation**

Run: `pytest tests/unit/test_config_parser_backend_fields.py -v && python -c "import json; json.load(open('engine/schema/agent.schema.json'))"`
Expected: PASS (4 passed) and schema parses.

- [ ] **Step 7: Commit**

```bash
git add engine/config_parser.py engine/schema/agent.schema.json website/content/docs/agent-yaml.mdx tests/unit/test_config_parser_backend_fields.py
git commit -m "feat(schema): add backend_url to knowledge_bases + memory.backend/backend_url"
```

---

### Task 4: Bake `prompts/<name>` refs into `AGENT_SYSTEM_PROMPT` at deploy time

**Files:**
- Modify: `engine/resolver.py` (add `_bake_prompt_ref`; thread `project_root` through `resolve_dependencies`)
- Test: `tests/unit/test_resolver_prompt_baking.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_resolver_prompt_baking.py
from pathlib import Path
from engine.config_parser import AgentConfig, ModelConfig, DeployConfig, PromptsConfig
from engine.resolver import resolve_dependencies


def _cfg(system):
    return AgentConfig(
        name="x", version="1.0.0", team="t", owner="o@e.com", framework="langgraph",
        model=ModelConfig(primary="claude-sonnet-4"), deploy=DeployConfig(cloud="aws"),
        prompts=PromptsConfig(system=system),
    )


def test_prompt_ref_is_baked_from_local_file(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "support.md").write_text("You are a support agent.")
    cfg = resolve_dependencies(_cfg("prompts/support"), project_root=tmp_path)
    assert cfg.prompts.system == "You are a support agent."


def test_inline_prompt_is_left_untouched(tmp_path):
    cfg = resolve_dependencies(_cfg("You are literally inline."), project_root=tmp_path)
    assert cfg.prompts.system == "You are literally inline."


def test_unresolvable_ref_is_left_for_runtime(tmp_path):
    cfg = resolve_dependencies(_cfg("prompts/missing"), project_root=tmp_path)
    assert cfg.prompts.system == "prompts/missing"  # warned, not raised
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_resolver_prompt_baking.py -v`
Expected: FAIL — `resolve_dependencies() got an unexpected keyword argument 'project_root'`.

- [ ] **Step 3: Write minimal implementation**

In `engine/resolver.py` add `from pathlib import Path` to imports, then add the helper above
`resolve_dependencies`:

```python
def _bake_prompt_ref(config: AgentConfig, project_root: Path | None) -> None:
    """Resolve a ``prompts/<name>`` system-prompt ref into a literal string at
    deploy time, so the container receives it via ``AGENT_SYSTEM_PROMPT`` instead
    of resolving over the network at runtime. Unresolvable refs are left as-is
    (the runtime can still try) with a warning."""
    from engine.prompt_resolver import (  # local import avoids import cycles
        PromptNotFoundError,
        is_prompt_ref,
        resolve_prompt,
    )

    system = config.prompts.system
    if not system or not is_prompt_ref(system):
        return
    try:
        resolved = resolve_prompt(system, project_root)
    except PromptNotFoundError:
        logger.warning(
            "Prompt ref %r could not be resolved at deploy time; the container "
            "will attempt runtime resolution.",
            system,
        )
        return
    if resolved and resolved != system:
        config.prompts.system = resolved
        logger.info("Baked prompt ref %r into AGENT_SYSTEM_PROMPT at deploy", system)
```

Change the signature and call it first thing inside `resolve_dependencies`:

```python
def resolve_dependencies(config: AgentConfig, project_root: Path | None = None) -> AgentConfig:
    _bake_prompt_ref(config, project_root)
    refs = []
    ...  # rest unchanged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_resolver_prompt_baking.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add engine/resolver.py tests/unit/test_resolver_prompt_baking.py
git commit -m "feat(resolver): resolve prompts/<name> refs into AGENT_SYSTEM_PROMPT at deploy"
```

---

### Task 5: Validate first-party / local tool refs at deploy (warn-soft)

**Files:**
- Modify: `engine/resolver.py` (add `_check_tool_refs`, call from `resolve_dependencies`)
- Test: `tests/unit/test_resolver_tool_check.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_resolver_tool_check.py
from pathlib import Path
import engine.resolver as resolver
from engine.config_parser import AgentConfig, ModelConfig, DeployConfig, ToolRef


def _cfg(refs):
    return AgentConfig(
        name="x", version="1.0.0", team="t", owner="o@e.com", framework="langgraph",
        model=ModelConfig(primary="claude-sonnet-4"), deploy=DeployConfig(cloud="aws"),
        tools=[ToolRef(ref=r) for r in refs],
    )


def test_resolvable_tool_ref_logs_no_warning(tmp_path, caplog):
    import engine.tool_resolver as tr
    monkey = lambda ref, project_root=None: (lambda: None)
    orig = tr.resolve_tool
    tr.resolve_tool = monkey
    try:
        resolver.resolve_dependencies(_cfg(["tools/web-search"]), project_root=tmp_path)
    finally:
        tr.resolve_tool = orig
    assert "did not resolve" not in caplog.text


def test_unresolvable_tool_ref_warns_not_raises(tmp_path, caplog):
    import engine.tool_resolver as tr
    def _boom(ref, project_root=None):
        raise tr.ToolNotFoundError(ref)
    orig = tr.resolve_tool
    tr.resolve_tool = _boom
    try:
        cfg = resolver.resolve_dependencies(_cfg(["tools/nope"]), project_root=tmp_path)
    finally:
        tr.resolve_tool = orig
    assert cfg is not None  # did not raise
    assert "did not resolve" in caplog.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_resolver_tool_check.py -v`
Expected: FAIL — no warning emitted because the check does not exist yet.

- [ ] **Step 3: Write minimal implementation**

In `engine/resolver.py` add the helper and call it inside `resolve_dependencies` (after
`_bake_prompt_ref`):

```python
def _check_tool_refs(config: AgentConfig, project_root: Path | None) -> None:
    """Best-effort deploy-time check that ``ref: tools/<name>`` entries resolve to a
    local file or a first-party ``engine.tools.standard`` tool (now bundled in the
    image). Missing tools warn rather than raise — registry/network tools may only
    resolve at runtime."""
    from engine.tool_resolver import ToolNotFoundError, is_tool_ref, resolve_tool

    for tool in config.tools:
        ref = getattr(tool, "ref", None)
        if not ref or not is_tool_ref(ref):
            continue
        try:
            resolve_tool(ref, project_root=project_root)
        except ToolNotFoundError:
            logger.warning(
                "Tool ref %r did not resolve to a local or first-party tool at "
                "deploy; relying on runtime/registry resolution.",
                ref,
            )
```

Call site inside `resolve_dependencies`:

```python
    _bake_prompt_ref(config, project_root)
    _check_tool_refs(config, project_root)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_resolver_tool_check.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add engine/resolver.py tests/unit/test_resolver_tool_check.py
git commit -m "feat(resolver): warn at deploy when tool refs don't resolve locally/first-party"
```

---

### Task 6: Backend-URL contract — explicit URLs win; local env behind a flag; expose `KB_PGVECTOR_DSN`

**Files:**
- Modify: `engine/resolver.py` (memory block `:140-160`, KB block `:162-181`)
- Test: `tests/unit/test_resolver_backend_contract.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_resolver_backend_contract.py
from pathlib import Path
from engine.config_parser import (
    AgentConfig, ModelConfig, DeployConfig, MemoryConfig, KnowledgeBaseRef,
)
from engine.resolver import resolve_dependencies


def _cfg(memory=None, kbs=None):
    return AgentConfig(
        name="x", version="1.0.0", team="t", owner="o@e.com", framework="langgraph",
        model=ModelConfig(primary="claude-sonnet-4"), deploy=DeployConfig(cloud="aws"),
        memory=memory, knowledge_bases=kbs or [],
    )


def test_explicit_memory_backend_url_is_injected(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTBREEDER_ALLOW_LOCAL_BACKENDS", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")  # local host value
    cfg = resolve_dependencies(
        _cfg(memory=MemoryConfig(stores=["mem/s"], backend="redis",
                                 backend_url="redis://prod-cache:6379")),
        project_root=tmp_path,
    )
    assert cfg.deploy.env_vars["REDIS_URL"] == "redis://prod-cache:6379"


def test_local_redis_is_NOT_scraped_without_flag(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTBREEDER_ALLOW_LOCAL_BACKENDS", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    cfg = resolve_dependencies(
        _cfg(memory=MemoryConfig(stores=["mem/s"], backend="redis")),
        project_root=tmp_path,
    )
    assert "REDIS_URL" not in cfg.deploy.env_vars


def test_local_redis_scraped_when_flag_set(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBREEDER_ALLOW_LOCAL_BACKENDS", "1")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    cfg = resolve_dependencies(
        _cfg(memory=MemoryConfig(stores=["mem/s"], backend="redis")),
        project_root=tmp_path,
    )
    assert cfg.deploy.env_vars["REDIS_URL"] == "redis://localhost:6379"


def test_kb_backend_url_exposed_as_pgvector_dsn(tmp_path):
    cfg = resolve_dependencies(
        _cfg(kbs=[KnowledgeBaseRef(ref="kb/docs", backend_url="postgresql://pg/db")]),
        project_root=tmp_path,
    )
    assert cfg.deploy.env_vars["KB_PGVECTOR_DSN"] == "postgresql://pg/db"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_resolver_backend_contract.py -v`
Expected: FAIL — current code scrapes local `REDIS_URL` unconditionally and never sets `KB_PGVECTOR_DSN`.

- [ ] **Step 3: Write minimal implementation**

Replace the **memory** block of `resolve_dependencies` (`engine/resolver.py:140-160`) with:

```python
    # Memory store refs — resolve backend + TTL into agent env vars.
    if config.memory:
        if config.deploy.env_vars is None:
            config.deploy.env_vars = {}

        backend, ttl_seconds = _resolve_memory_config(config.memory.stores)
        backend = config.memory.backend or backend  # explicit agent.yaml wins
        if backend:
            config.deploy.env_vars.setdefault("MEMORY_BACKEND", backend)
        if ttl_seconds and ttl_seconds > 0:
            config.deploy.env_vars.setdefault("MEMORY_TTL_SECONDS", str(ttl_seconds))

        # D2 contract: explicit backend_url wins; local host env only behind a flag.
        allow_local = os.environ.get("AGENTBREEDER_ALLOW_LOCAL_BACKENDS") == "1"
        explicit = config.memory.backend_url
        if backend == "redis":
            url = explicit or (os.environ.get("REDIS_URL") if allow_local else None)
            if url:
                config.deploy.env_vars.setdefault("REDIS_URL", url)
        elif backend == "postgresql":
            url = explicit or (os.environ.get("DATABASE_URL") if allow_local else None)
            if url:
                config.deploy.env_vars.setdefault("DATABASE_URL", url)

        for store_ref in config.memory.stores:
            refs.append(f"memory:{store_ref}")
        logger.debug("Resolved memory stores: backend=%s ttl=%s", backend, ttl_seconds)
```

Replace the **knowledge_bases** block (`engine/resolver.py:162-181`) with:

```python
    # Resolve knowledge base refs → RAG index IDs + backend DSN for invoke-time search.
    if config.knowledge_bases:
        if config.deploy.env_vars is None:
            config.deploy.env_vars = {}

        kb_index_ids = _resolve_kb_index_ids(config.knowledge_bases)
        if kb_index_ids:
            config.deploy.env_vars["KB_INDEX_IDS"] = ",".join(kb_index_ids)
            logger.info(
                "Resolved %d knowledge base(s) → KB_INDEX_IDS=%s",
                len(kb_index_ids),
                config.deploy.env_vars["KB_INDEX_IDS"],
            )

        # D2 contract: explicit per-KB backend_url becomes the vector-store DSN seam
        # that P2 (managed provisioning) fills when no explicit URL is given.
        dsns = [kb.backend_url for kb in config.knowledge_bases if kb.backend_url]
        if dsns:
            config.deploy.env_vars.setdefault("KB_PGVECTOR_DSN", dsns[0])

        allow_local = os.environ.get("AGENTBREEDER_ALLOW_LOCAL_BACKENDS") == "1"
        neo4j_url = os.environ.get("NEO4J_URL")
        if neo4j_url and allow_local and "NEO4J_URL" not in config.deploy.env_vars:
            config.deploy.env_vars["NEO4J_URL"] = neo4j_url
            logger.debug("Injected local NEO4J_URL (AGENTBREEDER_ALLOW_LOCAL_BACKENDS=1)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_resolver_backend_contract.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Run the full resolver suite + check for callers relying on old scraping**

Run: `pytest tests/unit/ -k resolver -q && grep -rn "AGENTBREEDER_ALLOW_LOCAL_BACKENDS" deploy/ docker-compose* 2>/dev/null`
Expected: resolver tests PASS. If local docker-compose relied on env scraping, set
`AGENTBREEDER_ALLOW_LOCAL_BACKENDS=1` in `deploy/docker-compose.yml` (the API/agent build env) in this step.

- [ ] **Step 6: Preserve local-dev behavior in compose (if needed)**

If Step 5 showed local agents lose their backend, add to the relevant service env in
`deploy/docker-compose.yml`:

```yaml
      AGENTBREEDER_ALLOW_LOCAL_BACKENDS: "1"
```

- [ ] **Step 7: Commit**

```bash
git add engine/resolver.py tests/unit/test_resolver_backend_contract.py deploy/docker-compose.yml
git commit -m "feat(resolver): explicit backend_url contract; gate local env scraping behind flag"
```

---

### Task 7: Thread `project_root` from the deploy pipeline + integration assertion on the built image

**Files:**
- Modify: `engine/builder.py:149` (call site of `resolve_dependencies`)
- Test: `tests/integration/test_build_bundles_engine.py`

- [ ] **Step 1: Write the failing integration test**

```python
# tests/integration/test_build_bundles_engine.py
from pathlib import Path
from engine.config_parser import AgentConfig, ModelConfig, DeployConfig, PromptsConfig
from engine.resolver import resolve_dependencies
from engine.runtimes.langgraph import LangGraphRuntime


def _agent_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agent"
    (d / "prompts").mkdir(parents=True)
    (d / "prompts" / "sys.md").write_text("You are a baked agent.")
    (d / "agent.py").write_text("graph = None\n")
    (d / "requirements.txt").write_text("langgraph>=0.2.0\n")
    return d


def test_built_image_installs_engine_and_bakes_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_RUNTIME_REQUIREMENT", "agentbreeder==1.2.3")
    agent_dir = _agent_dir(tmp_path)
    cfg = AgentConfig(
        name="baked", version="1.0.0", team="t", owner="o@e.com", framework="langgraph",
        model=ModelConfig(primary="claude-sonnet-4"), deploy=DeployConfig(cloud="aws"),
        prompts=PromptsConfig(system="prompts/sys"),
    )
    cfg = resolve_dependencies(cfg, project_root=agent_dir)
    image = LangGraphRuntime().build(agent_dir, cfg)

    reqs = (image.context_dir / "requirements.txt").read_text()
    assert "agentbreeder==1.2.3" in reqs                       # engine bundled
    assert 'AGENT_SYSTEM_PROMPT="You are a baked agent."' in image.dockerfile_content  # prompt baked
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_build_bundles_engine.py -v`
Expected: PASS already? No — `requirements.txt` will contain `agentbreeder==1.2.3` (Task 2) and
the prompt is baked (Task 4) **only because** the test calls `resolve_dependencies` with
`project_root` directly. This test guards the *contract*; it should pass once Tasks 2 & 4 are in.
If it fails, the failure pinpoints which task regressed. Treat a failure here as a real bug to fix.

- [ ] **Step 3: Make the pipeline pass `project_root`**

In `engine/builder.py`, change the resolver call (currently `config = resolve_dependencies(config)`
at `:149`) to pass the agent directory, which is already available as `config_path.parent`
(used later at `:171/:174`):

```python
            config = resolve_dependencies(config, config_path.parent)
```

- [ ] **Step 4: Run test + builder integration suite**

Run: `pytest tests/integration/test_build_bundles_engine.py tests/integration -k build -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine/builder.py tests/integration/test_build_bundles_engine.py
git commit -m "feat(builder): pass agent project_root into resolve_dependencies for deploy-time resolution"
```

---

### Task 8: Docs, changelog, and cross-repo sync (D4)

**Files:**
- Modify: `website/content/docs/agent-yaml.mdx` (verify Task 3 note landed; add a "Cloud backends" callout)
- Modify: `website/content/docs/how-to.mdx` (note `AGENTBREEDER_ALLOW_LOCAL_BACKENDS` for local dev)
- Modify: `CHANGELOG.md` (Unreleased → "Artifact bundling foundation")
- Check: `agentbreeder-cloud` for the new `backend_url` / env-contract assumptions

- [ ] **Step 1: Add a "Cloud backends" callout to docs**

In `website/content/docs/agent-yaml.mdx`, add a short callout under memory/knowledge_bases:

> **Cloud backends.** A deployed agent never inherits your laptop's `REDIS_URL`/`DATABASE_URL`.
> Either set `backend_url` explicitly, or let `agentbreeder deploy` provision a managed backend.
> For local Docker Compose, set `AGENTBREEDER_ALLOW_LOCAL_BACKENDS=1` to reuse the compose services.

- [ ] **Step 2: Changelog entry**

Add under `## [Unreleased]` in `CHANGELOG.md`:

```markdown
### Added
- Agent images now bundle the AgentBreeder runtime (`agentbreeder` dist), so registry-ref
  prompts, first-party tools, RAG and memory work on AWS/GCP/Azure — not just self-contained agents.
- `agent.yaml`: `knowledge_bases[].backend_url`, `memory.backend`, `memory.backend_url`.
### Changed
- Deploy no longer forwards the deploy host's local `REDIS_URL`/`DATABASE_URL`/`NEO4J_URL` to cloud
  agents. Set `backend_url`, or `AGENTBREEDER_ALLOW_LOCAL_BACKENDS=1` for local dev.
```

- [ ] **Step 3: Cross-repo grep (standing rule)**

Run: `grep -rn "REDIS_URL\|DATABASE_URL\|backend_url\|knowledge_bases" /Users/rajit/personal-github/agentbreeder-cloud --include=*.py --include=*.ts -l 2>/dev/null | head`
Expected: review hits; if Cloud builds `agent.yaml` programmatically or assumed local env forwarding,
file a companion change/issue. Record findings in the commit message.

- [ ] **Step 4: Commit**

```bash
git add website/content/docs/agent-yaml.mdx website/content/docs/how-to.mdx CHANGELOG.md
git commit -m "docs(cloud): document engine bundling + backend_url contract; changelog"
```

---

### Task 9: Full-suite gate before PR

- [ ] **Step 1: Lint + type + unit + integration**

Run: `ruff check . && ruff format --check . && mypy engine/ && pytest tests/unit tests/integration -q`
Expected: all green. Fix anything red before proceeding (no `--no-verify`).

- [ ] **Step 2: Coverage on changed files ≥ 80%**

Run: `pytest --cov=engine --cov-report=term-missing tests/unit tests/integration -q`
Expected: `engine/resolver.py`, `engine/runtimes/base.py` changed lines ≥ 80% covered.

- [ ] **Step 3: Open the PR (only now — D5)**

```bash
git push -u origin feat/cloud-agnostic-p1-bundling
```
Then open a PR titled `feat: cloud-agnostic deployment P1 — artifact bundling foundation`, body
linking this plan and the epic index. Do **not** squash (preserve task-by-task history).

---

## Self-Review

**Spec coverage** (against epic L2 root causes 1 & 2, and the D1/D2 cross-cutting decisions):
- Root cause 1 (engine never bundled) → Tasks 1, 2, 7. ✅
- Root cause 2 (local-env scraping / no backend seam) → Tasks 3, 6. ✅
- D1 (bundling via `agentbreeder` dist, override env, opt-out) → Task 1. ✅
- D2 (explicit `backend_url`, flag-gated local scraping, `KB_PGVECTOR_DSN` seam) → Tasks 3, 6. ✅
- Deploy-time prompt resolution (epic L2 "prompts only work baked-in") → Task 4. ✅
- First-party tool refs (epic L2 "tools ImportError") → bundling (Task 2) + deploy-time check (Task 5). ✅
- D4 doc sync → Tasks 3, 8. D5 branch/PR discipline → Task 9. ✅
- **Out of scope for P1 (correctly deferred):** RAG client embed-on-query swap + vector-store provisioning → **P2**; memory backend provisioning + env namespacing → **P3**; MCP → **P4**. The `langgraph_server._inject_kb_context` `from api.services.rag_service import get_rag_store` line keeps working once the engine is bundled (import resolves; in-memory store stays empty until P2 wires `KB_PGVECTOR_DSN`). ✅

**Placeholder scan:** No TBD/"add error handling"/"similar to Task N". Every code step shows full code. ✅
*Caveat flagged for the executor:* the exact constructor kwargs of `AgentConfig`/runtime classes and `resolve_tool`'s signature are taken from `config_parser.py`/`tool_resolver.py`; if a name differs, adapt the **fixture/call**, never the assertion.

**Type/name consistency:** `runtime_support_requirement` (Tasks 1,2,7), `resolve_dependencies(config, project_root)` (Tasks 4,5,6,7), `KnowledgeBaseRef.backend_url` / `MemoryConfig.backend`/`backend_url` (Tasks 3,6), env vars `KB_PGVECTOR_DSN`/`AGENTBREEDER_ALLOW_LOCAL_BACKENDS`/`AGENTBREEDER_RUNTIME_REQUIREMENT` — all spelled identically across tasks. ✅
