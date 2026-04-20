# AgentBreeder Seed Agents

This directory contains production-grade `agent.yaml` files that seed the AgentBreeder registry with real, well-documented examples across common enterprise domains.

Seeds are not toy demos. Each file is:
- **Valid** — passes `agentbreeder validate` against the current schema
- **Realistic** — covers a real use case with correct tool refs, guardrails, and scaling config
- **Annotated** — every non-obvious field is commented
- **Diverse** — different frameworks, models, cloud targets, and governance patterns

## Included Seeds

| File | Domain | Framework | Model | Cloud |
|------|--------|-----------|-------|-------|
| `customer-support-agent.yaml` | Customer Support | claude_sdk | claude-sonnet-4 | AWS ECS Fargate |
| `data-pipeline-agent.yaml` | Data Engineering | langgraph | gpt-4o | GCP Cloud Run |
| `devops-agent.yaml` | DevOps / SRE | claude_sdk | claude-sonnet-4 | GCP Cloud Run |
| `finance-analyst-agent.yaml` | Finance | openai_agents | gpt-4o | AWS ECS Fargate |
| `content-moderator-agent.yaml` | Trust & Safety | claude_sdk | claude-haiku-4-5 | Kubernetes |
| `code-reviewer-agent.yaml` | Developer Productivity | claude_sdk | claude-sonnet-4 | GCP Cloud Run |

## Loading Seeds into Your Local Registry

### Option 1: CLI (recommended)

Validate and register one agent:

```bash
agentbreeder validate seeds/customer-support-agent.yaml
agentbreeder register seeds/customer-support-agent.yaml
```

Register all seeds at once:

```bash
for f in seeds/*.yaml; do
  agentbreeder validate "$f" && agentbreeder register "$f"
done
```

### Option 2: API

```bash
curl -X POST http://localhost:8000/api/v1/agents \
  -H "Content-Type: application/yaml" \
  -H "Authorization: Bearer $AGENTBREEDER_TOKEN" \
  --data-binary @seeds/customer-support-agent.yaml
```

### Option 3: Docker Compose (fresh dev environment)

If you are bringing up a fresh local stack, the `docker-compose.yml` in `deploy/` can auto-seed on first boot. Set `AGENTBREEDER_SEED_ON_INIT=true` in your `.env` and the seeds in this directory will be registered automatically.

## Adapting a Seed for Your Own Agent

Seeds are starting points, not templates to fill in. The recommended workflow:

1. Copy the seed closest to your use case
2. Run `agentbreeder validate` as you edit to catch schema errors early
3. Replace `ref:` values with real registry refs for your org's tools and prompts
4. Update `team` and `owner` to match your org structure
5. When ready: `agentbreeder deploy --target local` to test end-to-end

## Contributing a New Seed

See the **Contributing to the Registry** section in [CONTRIBUTING.md](../CONTRIBUTING.md) for the full quality checklist and submission process.

The short version:
1. The agent must solve a real, concrete use case
2. All required fields must be present (`name`, `version`, `team`, `owner`, `framework`, `model.primary`, `deploy.cloud`)
3. Include at least one guardrail where appropriate
4. Pass `agentbreeder validate` before opening a PR
5. Add a row to the table above in this README
