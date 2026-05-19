# End-to-End Deploy Pipeline — Status & Plan
**Date:** 2026-05-17  
**Goal:** Full CLI-to-cloud workflow: create prompt → tools → RAG → agent → test (Ollama/providers) → deploy GCP/AWS/Azure with auto-infra provisioning

---

## Current State Summary

### What works today (no changes needed)
| Component | Status |
|-----------|--------|
| `agentbreeder init` | ✅ Full — scaffolds any framework, any runtime |
| `agentbreeder validate` | ✅ Full — validates agent.yaml |
| `agentbreeder deploy --target local` | ✅ Full — Docker Compose, Ollama sidecar |
| `agentbreeder deploy --target gcp` | ✅ App layer — builds image, creates Cloud Run service |
| `agentbreeder deploy --target aws` | ✅ App layer — builds image, creates ECS Fargate service |
| `agentbreeder deploy --target azure` | ✅ App layer — builds image, creates Container Apps service |
| Registry CRUD (agents/prompts/tools/models) | ✅ Full |
| Providers (Ollama, Claude, OpenAI, Gemini) | ✅ Full |
| RAG (Vector pgvector + Graph Neo4j) | ✅ API layer — no CLI |
| Memory (Postgres, buffer_window) | ✅ API layer |
| Secrets (env, AWS SM, GCP SM, Vault) | ✅ Full |

### What's missing or partial
| Gap | Impact | Priority |
|-----|--------|----------|
| **No infrastructure provisioning** — cloud deployers assume VPC, subnets, security groups, container registry, DB all exist | **CRITICAL** — cloud deploy fails on fresh accounts | P0 |
| **No `agentbreeder rag` CLI command** | Can't create/manage RAG indexes from terminal | P1 |
| **No `agentbreeder prompt create` / `tool create` CLI** | Creating registry resources requires dashboard or raw API | P1 |
| **`agentbreeder chat` missing streaming + token display** | Poor local test experience | P2 |
| **MCP sidecar deployer is a stub** | MCP tools don't deploy with agents | P2 |
| **`agentbreeder search` is 69-line stub** | Registry search unusable | P3 |

---

## Target Workflow (what we're building toward)

```
# Step 1: Create registry resources
agentbreeder prompt create --name support-system --file prompts/support.md
agentbreeder tool create --name zendesk-lookup --schema tools/zendesk.json
agentbreeder rag create --name product-docs --type vector --backend pgvector
agentbreeder rag ingest product-docs --dir ./docs/
agentbreeder rag create --name knowledge-graph --type graph --backend neo4j
agentbreeder rag ingest knowledge-graph --dir ./docs/ --type graph

# Step 2: Scaffold agent referencing those resources
agentbreeder init  # picks up registered prompts/tools/RAG

# Step 3: Test locally with Ollama
agentbreeder chat --model ollama/llama3 --stream
agentbreeder chat --model claude-sonnet-4 --stream

# Step 4: Deploy to cloud (auto-provisions missing infra)
agentbreeder deploy --target gcp    # creates VPC, AR, SA, DB, Cloud Run
agentbreeder deploy --target aws    # creates VPC, ECR, IAM, RDS, ECS Fargate
agentbreeder deploy --target azure  # creates RG, ACR, VNet, ACA environment

# Step 5: Verify — endpoint returned, agent in registry
agentbreeder describe my-agent
agentbreeder chat --agent https://<cloud-run-url>
```

---

## Implementation Plan

### P0 — Infrastructure Auto-Provisioning (cloud deployers)

This is the biggest gap. Each cloud deployer needs a "bootstrap infra" step that runs before the image push.

#### GCP Auto-Provisioning (`engine/deployers/gcp_cloudrun.py`)
Add `_ensure_gcp_infra(self, config)` that idempotently creates:
- [ ] **Artifact Registry** repository (if not exists)
- [ ] **Service Account** for the agent (or reuse via identity.py)
- [ ] **IAM bindings** — Cloud Run invoker, SA token creator
- [ ] **VPC Connector** (if agent needs private network access)
- [ ] **Cloud SQL instance** — PostgreSQL for memory (if `memory:` declared in agent.yaml)
- [ ] **Secret Manager secrets** — mirror from agent.yaml `secrets:` list
- Uses: `google-cloud-artifactregistry`, `google-cloud-iam`, `google-cloud-sql-connector`

#### AWS Auto-Provisioning (`engine/deployers/aws_ecs.py`)
Add `_ensure_aws_infra(self, config)`:
- [ ] **ECR repository** (if not exists)
- [ ] **VPC + subnets + security groups** (if `AWS_VPC_ID` not set)
- [ ] **ECS Cluster** (if not exists)
- [ ] **IAM roles** — task role + execution role (via identity.py expansion)
- [ ] **RDS PostgreSQL** — for memory (if `memory:` declared)
- [ ] **ALB + Target Group** — if `access.visibility: public`
- [ ] **Secrets Manager** — mirror secrets from agent.yaml
- Uses: boto3 (ec2, ecs, rds, elbv2, iam, ecr, secretsmanager)

#### Azure Auto-Provisioning (`engine/deployers/azure_container_apps.py`)
Add `_ensure_azure_infra(self, config)`:
- [ ] **Resource Group** (if not exists)
- [ ] **Azure Container Registry** (if not exists)
- [ ] **Container Apps Environment** (if not exists)
- [ ] **Azure Database for PostgreSQL** — for memory (if `memory:` declared)
- [ ] **Key Vault** — mirror secrets
- [ ] **Managed Identity** — per-agent identity
- Uses: azure-mgmt-containerregistry, azure-mgmt-containerinstance, azure-mgmt-rdbms

**Implementation approach:** Each deployer gets a `_ensure_infra()` method called before `_build_and_push()`. Methods are idempotent — check-then-create. Failures surface as `InfraProvisionError` (rollback-safe). New env var: `AGENTBREEDER_PROVISION_INFRA=true` (default true for cloud targets, false for local).

---

### P1 — CLI Resource Management Commands

#### `agentbreeder prompt` subcommand (`cli/commands/prompt.py`)
- [ ] `agentbreeder prompt create --name <n> --file <path> [--version 1.0.0]`
- [ ] `agentbreeder prompt list [--team <t>]`
- [ ] `agentbreeder prompt get <name>`
- [ ] `agentbreeder prompt test <name> --input "..." [--model ollama/llama3]`
- Calls: `POST /api/v1/prompts`, `GET /api/v1/prompts`, `POST /api/v1/prompts/test`

#### `agentbreeder tool` subcommand (`cli/commands/tool.py`)
- [ ] `agentbreeder tool create --name <n> --schema <json_file>`
- [ ] `agentbreeder tool list`
- [ ] `agentbreeder tool sandbox --name <n> --input '{"key": "val"}'`
- Calls: `POST /api/v1/tools`, `POST /api/v1/tools/sandbox/execute`

#### `agentbreeder rag` subcommand (`cli/commands/rag.py`)
- [ ] `agentbreeder rag create --name <n> --type vector|graph --backend pgvector|neo4j|memory`
- [ ] `agentbreeder rag ingest <name> --dir <path> [--type graph]`
- [ ] `agentbreeder rag search <name> --query "..." [--limit 5]`
- [ ] `agentbreeder rag list`
- Calls: `POST /api/v1/rag`, `POST /api/v1/rag/{id}/ingest`, `POST /api/v1/rag/{id}/search`

---

### P2 — Chat Command Improvements (`cli/commands/chat.py`)

- [ ] Real-time streaming (SSE/WebSocket from `/stream` endpoint)
- [ ] Token counting display (`[123 tokens | $0.002]` in status bar)
- [ ] `--rag <index-name>` flag — inject RAG context into conversation
- [ ] `--memory <config-name>` flag — persist conversation to postgres
- [ ] Session resumption (`--session-id <id>`)
- [ ] Multi-turn with history display

---

### P2 — MCP Sidecar Deployer (`engine/deployers/mcp_sidecar.py`)

Currently 83 lines, no deploy logic. Needs:
- [ ] Build sidecar image alongside agent image
- [ ] Inject sidecar into ECS task definition (multi-container task)
- [ ] Inject sidecar into GCP Cloud Run service (multi-container service, GA since 2024)
- [ ] Inject sidecar into Azure Container Apps (multi-container support)
- [ ] Wire `AGENT_AUTH_TOKEN` env var across containers

---

### P3 — Search Command (`cli/commands/search.py`)

Currently 69 lines. Expand to:
- [ ] `agentbreeder search <query>` — cross-entity fuzzy search
- [ ] `--type agent|prompt|tool|model` filter
- [ ] `--team <t>` filter
- [ ] Rich table output with clickable links

---

## File Changes Required

| File | Change Type | Description |
|------|-------------|-------------|
| `engine/deployers/gcp_cloudrun.py` | MODIFY | Add `_ensure_gcp_infra()` with AR, SA, VPC Connector, Cloud SQL |
| `engine/deployers/aws_ecs.py` | MODIFY | Add `_ensure_aws_infra()` with ECR, VPC, ECS cluster, RDS, IAM |
| `engine/deployers/azure_container_apps.py` | MODIFY | Add `_ensure_azure_infra()` with RG, ACR, ACA env, PostgreSQL |
| `engine/deployers/identity.py` | MODIFY | Expand to handle execution roles + RDS/Cloud SQL access policies |
| `cli/commands/prompt.py` | CREATE | Prompt CRUD + test CLI |
| `cli/commands/tool.py` | CREATE | Tool CRUD + sandbox CLI |
| `cli/commands/rag.py` | CREATE | RAG create/ingest/search CLI |
| `cli/main.py` | MODIFY | Register new prompt/tool/rag subcommands |
| `cli/commands/chat.py` | MODIFY | Streaming, token counts, RAG/memory flags |
| `engine/deployers/mcp_sidecar.py` | MODIFY | Full sidecar deployment logic |
| `cli/commands/search.py` | MODIFY | Full cross-entity search |
| `website/content/docs/cli-reference.mdx` | MODIFY | Document new prompt/tool/rag commands |
| `website/content/docs/quickstart.mdx` | MODIFY | Update quickstart flow |

---

## Sequence to Implement

1. **P0 first** — infra provisioning unlocks cloud deploys on fresh accounts (biggest blocker)
   - GCP → AWS → Azure (in parallel, separate subtasks)
2. **P1 next** — CLI resource commands complete the create-register-test loop
   - prompt → tool → rag (sequential, each builds on registry pattern)
3. **P2** — chat streaming + MCP sidecar (parallel)
4. **P3** — search command (low impact, quick win)

---

## Success Criteria

```bash
# Fresh GCP account, no pre-existing infra
export GOOGLE_APPLICATION_CREDENTIALS=~/sa.json
export GOOGLE_CLOUD_PROJECT=my-project

agentbreeder prompt create --name support-system --file prompts/support.md
agentbreeder tool create --name lookup --schema tools/lookup.json
agentbreeder rag create --name docs --type vector --backend pgvector
agentbreeder rag ingest docs --dir ./knowledge/
agentbreeder init  # creates agent.yaml referencing above
agentbreeder chat --model ollama/llama3 --stream  # local test passes
agentbreeder chat --model claude-sonnet-4 --stream  # cloud provider test passes
agentbreeder deploy --target gcp  # auto-provisions all infra, returns endpoint
# → https://my-agent-xxxx-uc.a.run.app  [HEALTHY]
# → Registered in registry: agents/my-agent@1.0.0
agentbreeder chat --agent https://my-agent-xxxx-uc.a.run.app  # cloud chat works
```
