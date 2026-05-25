"""Pure agent-stack recommendation heuristics.

This module is intentionally side-effect-free: no I/O, no database, no LLM
calls, no network. It encodes the deterministic decision rules from the
``/agent-build`` advisory skill (Step G) so that both the CLI skill and the
Studio wizard share a single source of truth.

The two genuinely-fuzzy signals (framework disambiguation from free-text use
case, cross-session memory inference from the business goal) are implemented
as conservative keyword heuristics; the Studio wizard lets users override
every field anyway.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Keyword lists (module constants — easy to extend, never duplicated)
# ---------------------------------------------------------------------------

# Framework disambiguation keywords (checked against technical_use_case, lowercased)
_GCP_KEYWORDS = frozenset({"vertex", "google workspace", "google cloud", "gcp"})
_CREW_KEYWORDS = frozenset({"crew", "multiple agents", "specialized agents"})
_CLAUDE_KEYWORDS = frozenset({"claude", "tool use", "adaptive thinking"})

# Model complexity keywords (checked against technical_use_case, lowercased)
_COMPLEX_KEYWORDS = frozenset({"complex planning", "research", "analysis"})

# Cross-session memory keywords (checked against business_goal, lowercased)
_CROSS_SESSION_KEYWORDS = frozenset({"user", "preference", "history", "remember"})

# MCP / A2A signal keywords (checked against technical_use_case, lowercased)
_MCP_KEYWORDS = frozenset({"api", "integration", "webhook"})
_A2A_KEYWORDS = frozenset({"delegate", "sub-agent", "hand off"})


def _contains_any(text: str, keywords: frozenset[str]) -> bool:
    """Return True if *text* (lowercased) contains any keyword from the set."""
    lowered = text.lower()
    return any(kw in lowered for kw in keywords)


# ---------------------------------------------------------------------------
# Eval dimension lookup table (business_goal keyword → dimension list)
# ---------------------------------------------------------------------------

_EVAL_DIMENSION_TABLE: list[tuple[frozenset[str], list[str]]] = [
    (
        frozenset({"support"}),
        ["deflection_rate", "escalation_accuracy", "csat_proxy", "pii_non_leakage"],
    ),
    (
        frozenset({"financial", "report"}),
        ["numerical_accuracy", "schema_correctness", "completeness", "hallucination_rate"],
    ),
    (
        frozenset({"code", "review"}),
        ["correctness", "security", "format_compliance", "test_pass_rate"],
    ),
    (
        frozenset({"research", "analysis"}),
        ["citation_accuracy", "hallucination_rate", "completeness", "source_relevance"],
    ),
    (
        frozenset({"pipeline", "data", "etl"}),
        ["schema_validation", "row_completeness", "latency", "error_rate"],
    ),
    (
        frozenset({"sales", "crm", "lead"}),
        ["lead_scoring_accuracy", "email_tone", "compliance"],
    ),
]

_DEFAULT_EVAL_DIMENSIONS: list[str] = [
    "correctness",
    "latency",
    "tool_call_accuracy",
    "hallucination_rate",
]

# ---------------------------------------------------------------------------
# Input / output models
# ---------------------------------------------------------------------------


class RecommendInput(BaseModel):
    """Inputs for the recommendation engine.

    All fields have safe defaults so callers can pass a partial object and
    get a fully-specified recommendation.
    """

    business_goal: str = ""
    technical_use_case: str = ""
    # subset of {"a","b","c","d","e"}: loops, checkpoints, HITL, parallel, none
    state_flags: list[str] = Field(default_factory=list)
    # aws | gcp | azure | kubernetes | local
    cloud_preference: str = "local"
    # python | typescript | none
    language_preference: str = "none"
    # subset of {"a","b","c","d","e"}: unstructured, sql, graph, live-apis, none
    data_flags: list[str] = Field(default_factory=list)
    # realtime | batch | event_driven | low_volume
    scale_profile: str = "low_volume"


class Recommendation(BaseModel):
    """Full agent-stack recommendation produced by ``recommend()``."""

    framework: str  # langgraph | crewai | claude_sdk | openai_agents | google_adk
    code_tier: str  # full_code | low_code
    model_primary: str
    rag: str  # vector | graph | hybrid | sql_tool | none
    memory: str  # redis | postgresql | redis+postgresql | none
    mcp_a2a: str  # mcp | a2a | mcp+a2a | none
    deploy_target: str  # ecs_fargate | cloud_run | azure_container_apps | docker_compose
    eval_dimensions: list[str]
    reasoning: dict[str, str]  # field → one-sentence why


# ---------------------------------------------------------------------------
# Pure recommendation function
# ---------------------------------------------------------------------------


def recommend(inp: RecommendInput) -> Recommendation:
    """Return a deterministic agent-stack recommendation for the given inputs.

    All decision rules are ported verbatim from ``.claude/commands/agent-build.md``
    Step G. Order of framework checks matters — typescript → gcp/adk → crewai
    → claude_sdk → langgraph.
    """
    state_set = set(inp.state_flags)
    use_case = inp.technical_use_case
    goal = inp.business_goal

    # ------------------------------------------------------------------
    # code_tier
    # ------------------------------------------------------------------
    strong_state_flags = state_set & {"a", "b", "c", "d"}
    if len(strong_state_flags) >= 2:
        code_tier = "full_code"
        code_tier_reason = (
            f"{len(strong_state_flags)} complex state flags ({', '.join(sorted(strong_state_flags))}) "
            "require full programmatic control"
        )
    else:
        code_tier = "low_code"
        code_tier_reason = "fewer than 2 complex state flags — YAML config is sufficient"

    # ------------------------------------------------------------------
    # framework  (typescript → gcp/adk → crewai → claude_sdk → langgraph)
    # ------------------------------------------------------------------
    if inp.language_preference == "typescript":
        framework = "openai_agents"
        fw_reason = "TypeScript preference requires OpenAI Agents (only TS-native framework)"
    elif inp.cloud_preference == "gcp" or _contains_any(use_case, _GCP_KEYWORDS):
        framework = "google_adk"
        fw_reason = "GCP cloud preference or Google/Vertex keyword detected → Google ADK for best platform integration"
    elif _contains_any(use_case, _CREW_KEYWORDS):
        framework = "crewai"
        fw_reason = "multi-agent crew keyword detected → CrewAI for role-based agent collaboration"
    elif _contains_any(use_case, _CLAUDE_KEYWORDS) and not ({"b", "c"} <= state_set):
        framework = "claude_sdk"
        fw_reason = "Claude/tool-use keyword detected and no strong state requirements → Claude SDK (simplest path)"
    else:
        framework = "langgraph"
        fw_reason = "complex state flags or no discriminating keyword → LangGraph as the safe, robust default"

    # ------------------------------------------------------------------
    # model_primary
    # ------------------------------------------------------------------
    if framework == "google_adk":
        model_primary = "gemini-2.5-flash"
        model_reason = "Google ADK agents use Gemini by default for best GCP integration"
    elif framework == "openai_agents":
        model_primary = "gpt-4o"
        model_reason = "OpenAI Agents framework pairs naturally with GPT-4o"
    elif _contains_any(use_case, _COMPLEX_KEYWORDS):
        model_primary = "claude-opus-4"
        model_reason = "complex planning / research / analysis keyword detected → Claude Opus 4 for maximum capability"
    elif inp.scale_profile in {"batch", "low_volume"}:
        model_primary = "claude-haiku-4-5"
        model_reason = (
            f"scale_profile={inp.scale_profile} prioritises cost efficiency → Claude Haiku 4.5"
        )
    else:
        model_primary = "claude-sonnet-4-6"
        model_reason = f"scale_profile={inp.scale_profile} with no complexity override → Claude Sonnet 4.6 (balanced)"

    # ------------------------------------------------------------------
    # rag
    # ------------------------------------------------------------------
    data_set = set(inp.data_flags)
    if {"a", "c"} <= data_set:
        rag = "hybrid"
        rag_reason = "both unstructured (a) and graph (c) data flags → hybrid vector + graph RAG"
    elif "c" in data_set:
        rag = "graph"
        rag_reason = "graph data flag (c) → graph RAG via Neo4j"
    elif "a" in data_set:
        rag = "vector"
        rag_reason = "unstructured data flag (a) → pgvector vector search"
    elif data_set == {"b"}:
        rag = "sql_tool"
        rag_reason = "only SQL/structured data flag (b) → SQL tool instead of vector RAG"
    else:
        rag = "none"
        rag_reason = "no data flags requiring retrieval augmentation"

    # ------------------------------------------------------------------
    # memory
    # ------------------------------------------------------------------
    is_realtime = inp.scale_profile == "realtime"
    cross_session = _contains_any(goal, _CROSS_SESSION_KEYWORDS)

    if is_realtime and cross_session:
        memory = "redis+postgresql"
        mem_reason = "realtime scale + cross-session goal keyword → Redis for speed + PostgreSQL for persistence"
    elif is_realtime:
        memory = "redis"
        mem_reason = "realtime scale requires fast in-memory session storage → Redis"
    elif cross_session:
        memory = "postgresql"
        mem_reason = (
            "cross-session goal keyword detected → PostgreSQL for durable long-term memory"
        )
    else:
        memory = "none"
        mem_reason = "no realtime requirement or cross-session signals → stateless is fine"

    # ------------------------------------------------------------------
    # mcp_a2a
    # ------------------------------------------------------------------
    has_mcp = "d" in data_set or _contains_any(use_case, _MCP_KEYWORDS)
    has_a2a = _contains_any(use_case, _A2A_KEYWORDS)

    if has_mcp and has_a2a:
        mcp_a2a = "mcp+a2a"
        mcp_reason = "external tool integration (MCP) + inter-agent delegation (A2A) both detected"
    elif has_mcp:
        mcp_a2a = "mcp"
        mcp_reason = "external API / integration keyword or live-API data flag → MCP sidecar"
    elif has_a2a:
        mcp_a2a = "a2a"
        mcp_reason = "delegate / sub-agent / hand-off keyword → A2A protocol for inter-agent calls"
    else:
        mcp_a2a = "none"
        mcp_reason = "no external tool or inter-agent signals detected"

    # ------------------------------------------------------------------
    # deploy_target
    # ------------------------------------------------------------------
    if inp.cloud_preference == "aws":
        deploy_target = "ecs_fargate"
        deploy_reason = (
            "AWS cloud preference → ECS Fargate (default; App Runner/Lambda are planned)"
        )
    elif inp.cloud_preference == "gcp":
        deploy_target = "cloud_run"
        deploy_reason = "GCP cloud preference → Cloud Run (serverless, auto-scaling)"
    elif inp.cloud_preference == "azure":
        deploy_target = "azure_container_apps"
        deploy_reason = "Azure cloud preference → Azure Container Apps"
    else:
        deploy_target = "docker_compose"
        deploy_reason = f"cloud_preference={inp.cloud_preference} → Docker Compose for local / low-volume / k8s"

    # ------------------------------------------------------------------
    # eval_dimensions
    # ------------------------------------------------------------------
    goal_lower = goal.lower()
    eval_dimensions = _DEFAULT_EVAL_DIMENSIONS
    for keywords, dimensions in _EVAL_DIMENSION_TABLE:
        if any(kw in goal_lower for kw in keywords):
            eval_dimensions = dimensions
            break

    # ------------------------------------------------------------------
    # Assemble reasoning dict
    # ------------------------------------------------------------------
    reasoning: dict[str, str] = {
        "code_tier": code_tier_reason,
        "framework": fw_reason,
        "model_primary": model_reason,
        "rag": rag_reason,
        "memory": mem_reason,
        "mcp_a2a": mcp_reason,
        "deploy_target": deploy_reason,
    }

    return Recommendation(
        framework=framework,
        code_tier=code_tier,
        model_primary=model_primary,
        rag=rag,
        memory=memory,
        mcp_a2a=mcp_a2a,
        deploy_target=deploy_target,
        eval_dimensions=eval_dimensions,
        reasoning=reasoning,
    )
