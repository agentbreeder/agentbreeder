# LiteLLM Integration Design

**Status:** In progress — see [#131](https://github.com/agentbreeder/agentbreeder/issues/131)
**Last updated:** April 2026

---

## Why LiteLLM

AgentBreeder's governance story — RBAC, cost attribution, audit trail — requires a centralized gateway that every agent inference call passes through. Building provider routing, fallbacks, retries, rate limiting, and caching from scratch would take 6–12 months and divert the team from the core product.

LiteLLM is an open-source proxy (Apache 2.0, 44k+ GitHub stars) that handles the routing/translation layer. AgentBreeder owns the governance layer on top.

**Division of responsibility:**

```
agent.yaml  ──→  AgentBreeder engine (RBAC check, audit write, cost attribution)
                      │
                      ▼
               LiteLLM proxy (provider translation, fallbacks, retries, caching)
                      │
                      ▼
           OpenAI / Anthropic / Gemini / Ollama / OpenRouter / ...
```

LiteLLM is a **dumb router**. AgentBreeder is the **governance layer**. Never let LiteLLM own RBAC, audit trail, or secrets — those stay in AgentBreeder.

---

## Architecture

### Current State (as of April 2026)

```
┌─────────────────────────────────────────────────────────┐
│  agent.yaml                                             │
│    model.primary: anthropic/claude-sonnet-4             │
│    model.gateway: litellm   ← PARSED, NOT USED          │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
              engine/config_parser.py (reads fields)
                             │
                             ▼
              engine/providers/registry.py
                ├── ProviderType.openai    → OpenAIProvider
                ├── ProviderType.anthropic → AnthropicProvider
                ├── ProviderType.google    → GoogleProvider
                ├── ProviderType.ollama    → OllamaProvider
                └── ProviderType.litellm   ← MISSING
                             │
                             ▼ (direct call today)
                      Anthropic / OpenAI / Google API

LiteLLM proxy (running on :4000, never called for inference)
  └── connectors/litellm/connector.py (model discovery only)

api/services/litellm_key_service.py
  └── get_or_create_agent_key() called at deploy:registering step
      └── key_value returned but DISCARDED — never injected into container
```

### Target State (post-integration)

```
┌─────────────────────────────────────────────────────────┐
│  agent.yaml                                             │
│    model.primary: claude-sonnet-4                       │
│    model.gateway: litellm                               │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
              engine/config_parser.py
                             │
                             ▼
              engine/providers/registry.py
                └── model.gateway == "litellm"
                      └── LiteLLMProvider(base_url, api_key=<agent-sk-key>)
                             │
                             ▼
               LiteLLM proxy (:4000)
                 ├── Virtual key auth (per-agent sk- key)
                 ├── Team budget enforcement
                 ├── Presidio PII guardrail
                 ├── Redis semantic cache
                 ├── Retry + fallback routing
                 └── OTEL span → AgentBreeder tracing
                             │
                             ▼
                  Provider (Anthropic / OpenAI / etc.)
```

---

## Component Breakdown

### 1. LiteLLMProvider (`engine/providers/litellm_provider.py`)

New class extending `ProviderBase`. Uses `httpx.AsyncClient` — no LiteLLM Python SDK dependency to keep engine deps lightweight.

```python
class LiteLLMProvider(ProviderBase):
    """Routes LLM calls through the LiteLLM proxy."""

    @property
    def name(self) -> str:
        return "litellm"

    async def generate(self, messages, model=None, ...) -> GenerateResult:
        # POST {LITELLM_BASE_URL}/v1/chat/completions
        # Bearer token = per-agent virtual key (injected at deploy time)

    async def generate_stream(self, messages, ...) -> AsyncIterator[StreamChunk]:
        # Same endpoint, stream=True, parse SSE

    async def list_models(self) -> list[ModelInfo]:
        # Delegates to LiteLLMConnector.scan()

    async def health_check(self) -> bool:
        # Delegates to LiteLLMConnector.is_available()
```

**Registered in `engine/providers/models.py`:**
```python
class ProviderType(enum.StrEnum):
    ...
    litellm = "litellm"   # add this
```

**Registered in `engine/providers/registry.py`:**
```python
_PROVIDER_CLASSES = {
    ...
    ProviderType.litellm: LiteLLMProvider,
}
```

### 2. Gateway Field Routing (`engine/config_parser.py` + `registry.py`)

When `model.gateway == "litellm"`, `create_provider()` must use `LiteLLMProvider` regardless of the `model.primary` provider prefix.

```python
def resolve_provider(model_config: ModelConfig) -> ProviderBase:
    if model_config.gateway == "litellm":
        return LiteLLMProvider(ProviderConfig(
            provider_type=ProviderType.litellm,
            base_url=settings.LITELLM_BASE_URL,
            api_key=model_config.litellm_key,  # injected at deploy time
            default_model=model_config.primary,
        ))
    # existing logic for direct providers
    ...
```

### 3. Virtual Key Injection (`engine/deployers/`)

The deploy pipeline's `registering` step already calls `get_or_create_agent_key()`. The fix:

1. Return `key_value` from `get_or_create_agent_key()` (currently returns `key_alias` only)
2. Pass `key_value` to the deployer as a secret:
   - `docker_compose.py`: add `LITELLM_API_KEY` and `LITELLM_BASE_URL` to container env
   - `gcp_cloudrun.py`: add as Cloud Run secret env var or Secret Manager reference
   - `aws_ecs.py`: add to ECS task definition environment (via Secrets Manager ARN)
3. The container's agent runtime reads `LITELLM_API_KEY` and uses it as its Bearer token

**Security rule:** `key_value` must be stored via `engine/secrets/` backends. Never put it in a plaintext env var in ECS task definitions or Cloud Run YAML — use secret references.

### 4. Team Budget Registration (`api/routes/teams.py`)

On team creation, register the team in LiteLLM:

```python
# POST /api/v1/teams handler addition
async with httpx.AsyncClient() as client:
    resp = await client.post(
        f"{settings.LITELLM_BASE_URL}/team/new",
        headers={"Authorization": f"Bearer {settings.LITELLM_MASTER_KEY}"},
        json={
            "team_id": str(team.id),
            "max_budget": team.budget_usd,  # new field on teams
            "budget_duration": "30d",
        },
    )
    team.litellm_team_id = resp.json().get("team_id")
```

**DB migration required:** `ALTER TABLE teams ADD COLUMN litellm_team_id VARCHAR NULL;`

### 5. Live Gateway Routes (`api/routes/gateway.py`)

Replace all hardcoded `_GATEWAY_TIERS`, `_GATEWAY_MODELS`, `_GATEWAY_PROVIDERS`, and `_generate_log_entries` with live LiteLLM API calls:

| Current (static) | Target (live) |
|---|---|
| `_GATEWAY_TIERS` | `GET {LITELLM_BASE_URL}/health` + latency measurement |
| `_GATEWAY_MODELS` | `LiteLLMConnector.scan()` |
| `_GATEWAY_PROVIDERS` | Aggregate `health_check()` across all providers |
| `_generate_log_entries()` | `GET {LITELLM_BASE_URL}/global/spend` (requires master key) |

**Graceful degradation:** If LiteLLM is unreachable, return stale cached data (Redis TTL 60s) rather than a 500 error. Status endpoint must always return 200.

### 6. LiteLLM Config (`deploy/litellm_config.yaml`)

**Add to both `litellm_config.yaml` and `litellm_config.quickstart.yaml`:**

```yaml
# Redis caching
litellm_settings:
  cache: true
  cache_params:
    type: redis
    host: redis
    port: 6379
    ttl: 600

# PII guardrail (open source, no license required)
guardrails:
  - guardrail_name: presidio-pii
    litellm_params:
      guardrail: presidio
      mode: pre_call
      output_parse_pii: true

  # Content injection detection
  - guardrail_name: lakera-prompt-injection
    litellm_params:
      guardrail: lakera_guard
      mode: pre_call
      api_key: os.environ/LAKERA_GUARD_API_KEY

# OTEL traces → AgentBreeder tracing API
  success_callback: ["otel"]
  failure_callback: ["otel"]
  otel:
    exporter: otlp_grpc
    endpoint: http://agentbreeder-api:4317
```

### 7. Observability Bridge (`api/routes/tracing.py`)

LiteLLM sets `x-litellm-call-id` on every response. The agent runtime should forward this header to the AgentBreeder tracing API so spans are correlated with audit log entries:

```
LiteLLM call → x-litellm-call-id: abc123
Agent runtime → POST /api/v1/tracing/spans {call_id: "abc123", agent_id: ..., deploy_id: ...}
AgentBreeder  → correlates span with audit log entry for this agent/deploy
```

---

## Features Used (Open Source vs Enterprise)

| Feature | Tier | Phase | Config location |
|---|---|---|---|
| Provider routing (100+ providers) | OSS | 1 | `litellm_config.yaml` |
| Virtual keys (per-agent `sk-` tokens) | OSS | 2 | `litellm_key_service.py` |
| Per-team budgets + `budget_duration` | OSS | 2 | `teams.py` on create |
| Redis exact-match caching | OSS | 1 | `litellm_config.yaml` |
| Fallback chains + retries | OSS | 1 | `litellm_config.yaml` |
| Load balancing (least-busy) | OSS | 1 | `litellm_config.yaml` |
| Health-driven routing | OSS | 1 | `litellm_config.yaml` |
| Presidio PII guardrail | OSS | 3 | `litellm_config.yaml` |
| Lakera prompt injection detection | OSS | 3 | `litellm_config.yaml` |
| OTEL callback | OSS | 3 | `litellm_config.yaml` |
| Prometheus `/metrics` | OSS | 3 | `litellm_config.yaml` |
| Tag-based routing (team pools) | OSS | 4 | `litellm_config.yaml` |
| Semantic caching (Redis vectors) | OSS | 4 | `litellm_config.yaml` |
| Secret manager integrations | **Enterprise** | SKIP | Already in `engine/secrets/` |
| JWT/OIDC auth for proxy | **Enterprise** | SKIP | AgentBreeder owns auth |
| Audit log export to S3/GCS | **Enterprise** | SKIP | AgentBreeder owns audit trail |
| Per-team logging isolation | **Enterprise** | SKIP | Not needed until multi-tenant |

---

## What AgentBreeder Keeps (Does NOT Delegate to LiteLLM)

| Concern | Why |
|---|---|
| **RBAC** | AgentBreeder's RBAC knows about agents, deploys, and teams — LiteLLM's is key/team only |
| **Audit trail** | `api/routes/audit.py` links entries to `agent_id` + `deploy_id`; LiteLLM can't |
| **Secret management** | `engine/secrets/` already supports AWS KMS, GCP Secret Manager, Vault |
| **Prompt registry** | `registry/prompts.py` is the source of truth; don't split it |
| **Agent registry** | LiteLLM has no concept of an "agent" as a registered entity |

---

## Implementation Checklist

### Phase 1 (target: ~2 days)
- [ ] `engine/providers/models.py` — add `ProviderType.litellm`
- [ ] `engine/providers/litellm_provider.py` — new `LiteLLMProvider` class
- [ ] `engine/providers/registry.py` — register `LiteLLMProvider`; read `model.gateway`
- [ ] `engine/config_parser.py` — pass gateway field to provider factory
- [ ] `api/routes/gateway.py` — live LiteLLM calls, graceful degradation
- [ ] `deploy/litellm_config.quickstart.yaml` — add Redis cache block
- [ ] `tests/unit/test_litellm_provider.py` — unit tests

### Phase 2 (target: ~2 days)
- [ ] `engine/deployers/base.py` — inject_litellm_key step
- [ ] `engine/deployers/docker_compose.py` + `gcp_cloudrun.py` + `aws_ecs.py` — key injection
- [ ] `api/services/deploy_service.py` — capture and pass key_value
- [ ] `api/routes/teams.py` — LiteLLM team registration on create/delete
- [ ] `alembic/versions/` — add `litellm_team_id` to teams
- [ ] `tests/integration/test_litellm_key_service.py` — integration tests

### Phase 3 (target: ~3 days)
- [ ] `deploy/litellm_config.yaml` — Presidio guardrail + OTEL callback
- [ ] `api/routes/tracing.py` — bridge `x-litellm-call-id` to audit log
- [ ] `api/routes/costs.py` — live spend from LiteLLM `/global/spend`
- [ ] `api/tasks/provider_health.py` — wire to LiteLLMConnector
- [ ] `tests/integration/test_gateway_routes.py` — live gateway tests

### Phase 4 (target: ~2 days)
- [ ] `deploy/litellm_config.yaml` — tag routing + semantic cache
- [ ] `engine/config_parser.py` — optional `model.routing_strategy` field
- [ ] `tests/e2e/test_litellm_flow.py` — full end-to-end test

---

## References

- LiteLLM docs: https://docs.litellm.ai
- GitHub issue: https://github.com/agentbreeder/agentbreeder/issues/131
- LiteLLM virtual keys: https://docs.litellm.ai/docs/proxy/virtual_keys
- LiteLLM guardrails: https://docs.litellm.ai/docs/proxy/guardrails/quick_start
- LiteLLM OTEL: https://docs.litellm.ai/docs/proxy/logging#opentelemetry
