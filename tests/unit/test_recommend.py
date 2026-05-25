"""Unit tests for engine/recommend.py — pure recommendation heuristics.

Tests cover all major decision paths per the Phase 5a plan test matrix.
"""

from __future__ import annotations

from engine.recommend import Recommendation, RecommendInput, recommend

# ---------------------------------------------------------------------------
# Framework selection
# ---------------------------------------------------------------------------


def test_typescript_picks_openai_agents() -> None:
    r = recommend(RecommendInput(language_preference="typescript"))
    assert r.framework == "openai_agents"
    assert r.model_primary == "gpt-4o"


def test_gcp_picks_google_adk_and_gemini() -> None:
    r = recommend(RecommendInput(cloud_preference="gcp"))
    assert r.framework == "google_adk"
    assert r.model_primary == "gemini-2.5-flash"
    assert r.deploy_target == "cloud_run"


def test_crew_keyword_picks_crewai() -> None:
    r = recommend(RecommendInput(technical_use_case="multiple agents working together in a crew"))
    assert r.framework == "crewai"


def test_specialized_agents_keyword_picks_crewai() -> None:
    r = recommend(RecommendInput(technical_use_case="specialized agents for data processing"))
    assert r.framework == "crewai"


def test_claude_keyword_picks_claude_sdk_without_state_flags() -> None:
    r = recommend(
        RecommendInput(technical_use_case="use claude with adaptive thinking and tool use")
    )
    assert r.framework == "claude_sdk"


def test_claude_sdk_not_chosen_when_bc_state_flags_present() -> None:
    """When both b and c are in state_flags, langgraph wins over claude_sdk."""
    r = recommend(
        RecommendInput(
            technical_use_case="claude tool use for research",
            state_flags=["b", "c"],
        )
    )
    assert r.framework == "langgraph"


# ---------------------------------------------------------------------------
# code_tier
# ---------------------------------------------------------------------------


def test_full_code_when_two_state_flags() -> None:
    r = recommend(RecommendInput(state_flags=["b", "c"]))
    assert r.code_tier == "full_code"
    assert r.framework == "langgraph"


def test_full_code_when_ad_state_flags() -> None:
    r = recommend(RecommendInput(state_flags=["a", "d"]))
    assert r.code_tier == "full_code"


def test_low_code_when_stateless() -> None:
    assert recommend(RecommendInput(state_flags=["e"])).code_tier == "low_code"


def test_low_code_when_one_state_flag() -> None:
    assert recommend(RecommendInput(state_flags=["a"])).code_tier == "low_code"


# ---------------------------------------------------------------------------
# model_primary
# ---------------------------------------------------------------------------


def test_batch_uses_haiku() -> None:
    assert recommend(RecommendInput(scale_profile="batch")).model_primary == "claude-haiku-4-5"


def test_low_volume_uses_haiku() -> None:
    assert (
        recommend(RecommendInput(scale_profile="low_volume")).model_primary == "claude-haiku-4-5"
    )


def test_complex_planning_uses_opus() -> None:
    r = recommend(RecommendInput(technical_use_case="complex planning and research analysis"))
    assert r.model_primary == "claude-opus-4"


def test_default_uses_sonnet() -> None:
    assert recommend(RecommendInput(scale_profile="realtime")).model_primary == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# rag
# ---------------------------------------------------------------------------


def test_hybrid_rag_when_unstructured_and_graph() -> None:
    assert recommend(RecommendInput(data_flags=["a", "c"])).rag == "hybrid"


def test_graph_rag_when_only_graph() -> None:
    assert recommend(RecommendInput(data_flags=["c"])).rag == "graph"


def test_vector_rag_when_unstructured() -> None:
    assert recommend(RecommendInput(data_flags=["a"])).rag == "vector"


def test_sql_tool_when_only_db() -> None:
    assert recommend(RecommendInput(data_flags=["b"])).rag == "sql_tool"


def test_no_rag_when_live_api() -> None:
    assert recommend(RecommendInput(data_flags=["d"])).rag == "none"


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------


def test_redis_plus_pg_when_realtime_and_cross_session() -> None:
    r = recommend(
        RecommendInput(
            scale_profile="realtime",
            business_goal="remember user preferences across sessions",
        )
    )
    assert r.memory == "redis+postgresql"


def test_redis_when_realtime_no_cross_session() -> None:
    r = recommend(RecommendInput(scale_profile="realtime", business_goal="process requests fast"))
    assert r.memory == "redis"


def test_postgresql_when_cross_session_non_realtime() -> None:
    r = recommend(
        RecommendInput(
            scale_profile="batch",
            business_goal="track user history across conversations",
        )
    )
    assert r.memory == "postgresql"


# ---------------------------------------------------------------------------
# mcp_a2a
# ---------------------------------------------------------------------------


def test_mcp_when_data_flag_d() -> None:
    assert recommend(RecommendInput(data_flags=["d"])).mcp_a2a == "mcp"


def test_a2a_when_delegate_keyword() -> None:
    assert (
        recommend(RecommendInput(technical_use_case="delegate tasks to sub-agents")).mcp_a2a
        == "a2a"
    )


def test_mcp_a2a_combined() -> None:
    r = recommend(
        RecommendInput(
            data_flags=["d"],
            technical_use_case="hand off to specialized sub-agent via webhook api integration",
        )
    )
    assert r.mcp_a2a == "mcp+a2a"


# ---------------------------------------------------------------------------
# deploy_target
# ---------------------------------------------------------------------------


def test_aws_realtime_deploys_ecs() -> None:
    assert (
        recommend(RecommendInput(cloud_preference="aws", scale_profile="realtime")).deploy_target
        == "ecs_fargate"
    )


def test_aws_batch_still_deploys_ecs() -> None:
    assert (
        recommend(RecommendInput(cloud_preference="aws", scale_profile="batch")).deploy_target
        == "ecs_fargate"
    )


def test_azure_deploys_container_apps() -> None:
    assert (
        recommend(RecommendInput(cloud_preference="azure")).deploy_target == "azure_container_apps"
    )


def test_local_deploys_docker_compose() -> None:
    assert recommend(RecommendInput(cloud_preference="local")).deploy_target == "docker_compose"


def test_kubernetes_deploys_docker_compose() -> None:
    assert (
        recommend(RecommendInput(cloud_preference="kubernetes")).deploy_target == "docker_compose"
    )


# ---------------------------------------------------------------------------
# eval_dimensions
# ---------------------------------------------------------------------------


def test_support_goal_eval_dimensions() -> None:
    r = recommend(RecommendInput(business_goal="reduce tier-1 support tickets"))
    assert "deflection_rate" in r.eval_dimensions


def test_financial_goal_eval_dimensions() -> None:
    r = recommend(RecommendInput(business_goal="generate financial reports"))
    assert "numerical_accuracy" in r.eval_dimensions


def test_code_goal_eval_dimensions() -> None:
    r = recommend(RecommendInput(business_goal="automate code review"))
    assert "correctness" in r.eval_dimensions
    assert "security" in r.eval_dimensions


def test_research_goal_eval_dimensions() -> None:
    r = recommend(RecommendInput(business_goal="research and analysis of documents"))
    assert "citation_accuracy" in r.eval_dimensions


def test_pipeline_goal_eval_dimensions() -> None:
    r = recommend(RecommendInput(business_goal="etl data pipeline processing"))
    assert "schema_validation" in r.eval_dimensions


def test_sales_goal_eval_dimensions() -> None:
    r = recommend(RecommendInput(business_goal="crm lead scoring"))
    assert "lead_scoring_accuracy" in r.eval_dimensions


def test_helpdesk_keyword_hits_support_row() -> None:
    """Non-literal 'helpdesk' maps to the support eval row (broadened keywords)."""
    r = recommend(RecommendInput(business_goal="automate helpdesk"))
    assert "deflection_rate" in r.eval_dimensions


def test_customer_tickets_keyword_hits_support_row() -> None:
    """'customer tickets' phrase (two separate keywords) maps to support eval row."""
    r = recommend(RecommendInput(business_goal="resolve customer tickets faster"))
    assert "deflection_rate" in r.eval_dimensions


def test_eval_dimensions_mutation_does_not_corrupt_defaults() -> None:
    """Mutating result.eval_dimensions must not corrupt the module-level default list."""
    r = recommend(RecommendInput())  # hits _DEFAULT_EVAL_DIMENSIONS path
    original_len = len(r.eval_dimensions)
    r.eval_dimensions.append("__sentinel__")
    # A second call must return a clean copy, unaffected by the mutation above
    r2 = recommend(RecommendInput())
    assert "__sentinel__" not in r2.eval_dimensions
    assert len(r2.eval_dimensions) == original_len


# ---------------------------------------------------------------------------
# Default / happy-path
# ---------------------------------------------------------------------------


def test_default_is_haiku_langgraph_docker() -> None:
    r = recommend(RecommendInput())
    assert r.framework == "langgraph"
    assert r.model_primary == "claude-haiku-4-5"  # low_volume default → haiku
    assert r.deploy_target == "docker_compose"
    assert r.rag == "none"
    assert r.memory == "none"
    assert r.mcp_a2a == "none"


def test_reasoning_present_for_each_field() -> None:
    r = recommend(RecommendInput())
    for k in ("framework", "model_primary", "rag", "memory", "mcp_a2a", "deploy_target"):
        assert k in r.reasoning, f"missing reasoning key: {k}"
        assert r.reasoning[k], f"empty reasoning for: {k}"


def test_recommendation_is_recommendation_instance() -> None:
    r = recommend(RecommendInput())
    assert isinstance(r, Recommendation)


def test_code_tier_in_reasoning() -> None:
    r = recommend(RecommendInput(state_flags=["b", "c"]))
    assert "code_tier" in r.reasoning
