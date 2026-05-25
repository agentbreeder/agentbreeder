# Studio Phase 5a — Recommendation Engine + Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Extract the `/agent-build` "recommend my stack" heuristics into a pure, tested `engine/recommend.py` and expose them at `POST /api/v1/builders/recommend`, so the upcoming Studio wizard (Phase 5b) and the CLI skill share one source of truth instead of forking the logic.

**Architecture:** `engine/recommend.py` is a pure function `recommend(RecommendInput) -> Recommendation` (no I/O, no LLM, no DB) implementing the skill's deterministic rules; the two genuinely-fuzzy signals (framework disambiguation from use-case text, long-term-memory inference from the goal) use conservative keyword heuristics that default to the safe choice — the wizard lets the user override every field anyway. The endpoint is a thin wrapper returning `ApiResponse[Recommendation]`. A typed `api.builders.recommend()` client method is added for Phase 5b.

**Tech Stack:** Python 3.11+, Pydantic, FastAPI, pytest. Frontend: TS api client only.

**Branch:** `feat/studio-ux-simplification` (commit per task; no PR until the whole epic passes locally).

---

## File Structure

- `engine/recommend.py` — NEW: `RecommendInput`, `Recommendation` (Pydantic) + pure `recommend()`.
- `tests/unit/test_recommend.py` — NEW: rule-matrix tests.
- `api/routes/builders.py` — add `POST /recommend` route returning `ApiResponse[Recommendation]`.
- `tests/unit/test_builders_recommend.py` — NEW: endpoint test.
- `dashboard/src/lib/api.ts` — add `builders.recommend(input)` typed method.

---

### Task 1: `engine/recommend.py` — input/output models + pure function

**Files:** Create `engine/recommend.py`; Test `tests/unit/test_recommend.py`

**Models** (Pydantic `BaseModel`, mirror `engine/config_parser.py` style):

```python
class RecommendInput(BaseModel):
    business_goal: str = ""
    technical_use_case: str = ""
    state_flags: list[str] = []        # subset of {"a","b","c","d","e"}: loops, checkpoints, HITL, parallel, none
    cloud_preference: str = "local"    # aws | gcp | azure | kubernetes | local
    language_preference: str = "none"  # python | typescript | none
    data_flags: list[str] = []         # subset of {"a","b","c","d","e"}: unstructured, sql, graph, live-apis, none
    scale_profile: str = "low_volume"  # realtime | batch | event_driven | low_volume

class Recommendation(BaseModel):
    framework: str          # langgraph | crewai | claude_sdk | openai_agents | google_adk
    code_tier: str          # full_code | low_code
    model_primary: str
    rag: str                # vector | graph | hybrid | sql_tool | none
    memory: str             # redis | postgresql | redis+postgresql | none
    mcp_a2a: str            # mcp | a2a | mcp+a2a | none
    deploy_target: str      # ecs_fargate | cloud_run | azure_container_apps | docker_compose
    eval_dimensions: list[str]
    reasoning: dict[str, str]   # field -> one-sentence why
```

**Decision rules** (port verbatim from `.claude/commands/agent-build.md` Step G — these are the spec):

- **code_tier:** `full_code` if `len(set(state_flags) & {"a","b","c","d"}) >= 2` else `low_code`.
- **framework:**
  - `language_preference == "typescript"` → `openai_agents`
  - elif `cloud_preference == "gcp"` OR use_case mentions vertex/google workspace (keyword) → `google_adk`
  - elif use_case mentions "crew"/"multiple agents"/"specialized agents" (keyword) → `crewai`
  - elif use_case mentions "claude"/"tool use"/"adaptive thinking" (keyword) AND no strong state (`{"b","c"} ⊄ state_flags`) → `claude_sdk`
  - elif `{"b","c"} & set(state_flags)` → `langgraph`
  - else → `langgraph` (safe default)
- **model_primary:**
  - if framework == `google_adk`: `gemini-2.5-flash`
  - elif framework == `openai_agents`: `gpt-4o`
  - elif use_case mentions complex planning/research/analysis (keyword): `claude-opus-4`
  - elif scale_profile in {`batch`,`low_volume`}: `claude-haiku-4-5`
  - else: `claude-sonnet-4-6`
- **rag:** `{"a","c"} ⊆ data_flags` → `hybrid`; elif `"c" in` → `graph`; elif `"a" in` → `vector`; elif `data_flags == ["b"]` → `sql_tool`; else `none`.
- **memory:** realtime per-session (scale_profile == `realtime`) AND goal implies cross-session (keyword: "user"/"preference"/"history"/"remember") → `redis+postgresql`; elif scale_profile == `realtime` → `redis`; elif goal implies cross-session (keyword) → `postgresql`; else `none`.
- **mcp_a2a:** has_mcp = `"d" in data_flags` OR use_case names external tools/APIs (keyword: "api"/"integration"/"webhook"); has_a2a = use_case mentions "delegate"/"sub-agent"/"hand off". Combine: both→`mcp+a2a`, mcp→`mcp`, a2a→`a2a`, else `none`.
- **deploy_target:** aws+realtime→`ecs_fargate`; aws (other)→`ecs_fargate` (App Runner/Lambda are planned — default ECS); gcp→`cloud_run`; azure→`azure_container_apps`; local/kubernetes/low_volume→`docker_compose`.
- **eval_dimensions:** keyword-match on `business_goal` (lowercase) per the skill's table — support→`["deflection_rate","escalation_accuracy","csat_proxy","pii_non_leakage"]`; financial/report→`["numerical_accuracy","schema_correctness","completeness","hallucination_rate"]`; code/review→`["correctness","security","format_compliance","test_pass_rate"]`; research/analysis→`["citation_accuracy","hallucination_rate","completeness","source_relevance"]`; pipeline/data/etl→`["schema_validation","row_completeness","latency","error_rate"]`; sales/crm/lead→`["lead_scoring_accuracy","email_tone","compliance"]`; else→`["correctness","latency","tool_call_accuracy","hallucination_rate"]`.
- **reasoning:** one short sentence per decided field stating which input drove it.

- [ ] **Step 1: Write the failing test** (`tests/unit/test_recommend.py`) — a matrix of cases asserting each rule. Examples:

```python
from engine.recommend import recommend, RecommendInput

def test_typescript_picks_openai_agents():
    r = recommend(RecommendInput(language_preference="typescript"))
    assert r.framework == "openai_agents"
    assert r.model_primary == "gpt-4o"

def test_gcp_picks_google_adk_and_gemini():
    r = recommend(RecommendInput(cloud_preference="gcp"))
    assert r.framework == "google_adk"
    assert r.model_primary == "gemini-2.5-flash"
    assert r.deploy_target == "cloud_run"

def test_full_code_when_two_state_flags():
    r = recommend(RecommendInput(state_flags=["b", "c"]))
    assert r.code_tier == "full_code"
    assert r.framework == "langgraph"

def test_low_code_when_stateless():
    assert recommend(RecommendInput(state_flags=["e"])).code_tier == "low_code"

def test_hybrid_rag_when_unstructured_and_graph():
    assert recommend(RecommendInput(data_flags=["a", "c"])).rag == "hybrid"

def test_sql_tool_when_only_db():
    assert recommend(RecommendInput(data_flags=["b"])).rag == "sql_tool"

def test_batch_uses_haiku():
    assert recommend(RecommendInput(scale_profile="batch")).model_primary == "claude-haiku-4-5"

def test_support_goal_eval_dimensions():
    r = recommend(RecommendInput(business_goal="reduce tier-1 support tickets"))
    assert "deflection_rate" in r.eval_dimensions

def test_aws_realtime_deploys_ecs():
    assert recommend(RecommendInput(cloud_preference="aws", scale_profile="realtime")).deploy_target == "ecs_fargate"

def test_default_is_sonnet_langgraph_docker():
    r = recommend(RecommendInput())
    assert r.framework == "langgraph"
    assert r.model_primary == "claude-sonnet-4-6"
    assert r.deploy_target == "docker_compose"
    assert r.rag == "none" and r.memory == "none" and r.mcp_a2a == "none"

def test_reasoning_present_for_each_field():
    r = recommend(RecommendInput())
    for k in ("framework", "model_primary", "rag", "memory", "mcp_a2a", "deploy_target"):
        assert k in r.reasoning and r.reasoning[k]
```

- [ ] **Step 2** — Run: `venv/bin/python -m pytest tests/unit/test_recommend.py -v` → FAIL (module missing).
- [ ] **Step 3** — Implement `engine/recommend.py` per the rules above. Pure function; keyword lists as module constants; type hints throughout; module logger only if needed (no prints). Order the framework checks exactly as listed (typescript → gcp/adk → crewai → claude_sdk → langgraph).
- [ ] **Step 4** — Run the test → all PASS. Run `ruff check engine/recommend.py tests/unit/test_recommend.py` and `ruff format engine/recommend.py tests/unit/test_recommend.py` and `mypy engine/recommend.py --ignore-missing-imports` → clean.
- [ ] **Step 5** — Commit: `git commit -m "feat(engine): recommend.py — pure agent-stack recommendation heuristics"`

---

### Task 2: `POST /api/v1/builders/recommend` endpoint

**Files:** Modify `api/routes/builders.py`; Test `tests/unit/test_builders_recommend.py`

- [ ] **Step 1** — Read `builders.py` imports + how its routes return `ApiResponse[...]` and the router prefix. Read `api/models/schemas.py` for the `ApiResponse[T]` generic usage pattern.
- [ ] **Step 2: Write the failing test** (`tests/unit/test_builders_recommend.py`) — mirror a sibling builders/api test's async client fixture:

```python
async def test_recommend_endpoint_returns_stack(client):
    resp = await client.post("/api/v1/builders/recommend", json={
        "business_goal": "reduce tier-1 support tickets",
        "technical_use_case": "search KB then look up order then escalate",
        "state_flags": ["b", "c"],
        "cloud_preference": "aws",
        "language_preference": "python",
        "data_flags": ["a"],
        "scale_profile": "realtime",
    })
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["framework"] == "langgraph"
    assert data["rag"] == "vector"
    assert data["deploy_target"] == "ecs_fargate"
    assert "deflection_rate" in data["eval_dimensions"]

async def test_recommend_endpoint_defaults(client):
    resp = await client.post("/api/v1/builders/recommend", json={})
    assert resp.status_code == 200
    assert resp.json()["data"]["framework"] == "langgraph"
```

- [ ] **Step 3** — Run → FAIL (404/route missing).
- [ ] **Step 4** — Implement: import `RecommendInput, Recommendation, recommend` from `engine.recommend`; add `@router.post("/recommend")` taking `RecommendInput` as the body, calling `recommend(...)`, returning `ApiResponse[Recommendation](data=result)`. Pure, no auth-beyond-default, no DB. (Confirm whether builders routes require auth deps — match the sibling routes.)
- [ ] **Step 5** — Run → PASS. `pytest tests/unit -k "builders or recommend" -v` green; `ruff check api/routes/builders.py`; `mypy api/routes/builders.py --ignore-missing-imports` clean.
- [ ] **Step 6** — Commit: `git commit -m "feat(api): POST /builders/recommend — shared stack recommendation"`

---

### Task 3: Frontend API client method

**Files:** Modify `dashboard/src/lib/api.ts`

- [ ] **Step 1** — Read the `builders` object in `api.ts` (or add one if absent) + the `request()` POST pattern. Add TS interfaces `RecommendInput` and `Recommendation` mirroring the Pydantic models, and:

```ts
recommend: (input: RecommendInput) =>
  request<Recommendation>("/builders/recommend", { method: "POST", body: JSON.stringify(input) }),
```

No `any`. Place under a `builders` namespace consistent with the file.
- [ ] **Step 2** — `cd dashboard && npx tsc --noEmit` → clean.
- [ ] **Step 3** — Commit: `git commit -m "feat(studio): builders.recommend API client method"`

---

### Task 4: Verify

- [ ] **Step 1** — `venv/bin/python -m pytest tests/unit/test_recommend.py tests/unit/test_builders_recommend.py -v` (green), `ruff check engine/recommend.py api/routes/builders.py`, `ruff format --check engine/recommend.py`, `mypy engine/recommend.py api/routes/builders.py --ignore-missing-imports`, `cd dashboard && npx tsc --noEmit`.

---

## Self-Review

**Spec coverage (§C architecture decision):** heuristics extracted to a single pure module (Task 1) + endpoint (Task 2) so the wizard and skill share one source ✓; deterministic + conservative keyword defaults, no hidden LLM ✓; client method ready for 5b (Task 3). Persisting the generated agent uses the existing `POST /agents/from-yaml` — that's Phase 5b, not here.

**Placeholder scan:** the decision rules are stated concretely (ported verbatim from the skill); tests give explicit assertions; the "match the sibling test fixture" instructions are concrete verification steps, not placeholders.

**Type/name consistency:** `RecommendInput`/`Recommendation` field names are identical across the Python module (Task 1), the endpoint (Task 2), and the TS interfaces (Task 3). The endpoint reuses the engine models directly (no divergent schema).
