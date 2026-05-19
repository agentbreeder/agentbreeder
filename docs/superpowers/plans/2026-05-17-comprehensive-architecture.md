# AgentBreeder — Comprehensive Architecture, Design & Implementation Plan
**Date:** 2026-05-17  
**Scope:** End-to-end: CLI + SDK + Dashboard → Registry → Test (Ollama/cloud providers) → Deploy (GCP/AWS/Azure) with auto-infra provisioning

---

## Executive Summary

AgentBreeder is **~85% application-complete** but has one critical production gap: cloud deployers assume pre-existing infrastructure. On fresh accounts, AWS and Azure deployments fail immediately; GCP gets further but still requires manual VPC Connector and Cloud SQL setup.

This plan addresses four parallel tracks:
1. **Infra Auto-Provisioning** — "bring your own" OR "fresh account" deployment paths
2. **CLI Resource Management** — create prompts/tools/RAG from terminal, end-to-end local test flow
3. **Auth & RBAC Hardening** — OAuth/SSO, refresh tokens, resource-level ACLs
4. **Frontend Design Uplift** — deployment wizard, enhanced chat sandbox, design system audit

---

## Current State Snapshot

### What works today
| Surface | Component | Status |
|---------|-----------|--------|
| **CLI** | init, validate, deploy (existing infra), chat (basic) | ✅ |
| **CLI** | prompt/tool/rag create | ❌ Missing |
| **SDK** | Python, TypeScript, Go | ✅ |
| **Dashboard** | 45+ pages, visual builders, playground, RAG, evals | ✅ |
| **Dashboard** | Deployment wizard (guided infra setup) | ❌ Missing |
| **Auth** | JWT email/password | ✅ |
| **Auth** | OAuth2/SSO (Google, GitHub, Okta, Azure AD) | ❌ Missing |
| **RBAC** | 3-tier team-scoped | ✅ |
| **RBAC** | Resource-level ACLs, org hierarchy | ❌ Missing |
| **LLM Gateway** | LiteLLM proxy, 100+ models, fallback chains | ✅ |
| **LLM Gateway** | Request caching, circuit breaker | ❌ Missing |
| **Deployers** | Local (Docker Compose), GCP Cloud Run | ✅ |
| **Deployers** | AWS ECS (existing infra only), Azure (existing infra only) | ⚠️ Partial |
| **Infra Provisioning** | GCP: auto-creates AR repo + SA | ⚠️ Partial |
| **Infra Provisioning** | AWS: auto-creates ECR only; fails on VPC/cluster | ❌ Critical gap |
| **Infra Provisioning** | Azure: creates nothing; fails on RG/ACA env | ❌ Critical gap |
| **Website/Docs** | 44 pages, mostly current | ⚠️ Stale AWS/Azure claims |

---

## The Two Deployment Scenarios

### Scenario A — Bring Your Own Infrastructure
*User has existing VPC, subnets, security groups, container registry, IAM roles, Postgres DB*

```
User provides env vars:
  AWS_VPC_SUBNETS=subnet-xxx,subnet-yyy
  AWS_SECURITY_GROUPS=sg-xxx
  AWS_ECS_CLUSTER=my-cluster
  AWS_EXECUTION_ROLE_ARN=arn:aws:iam::123:role/ecsTaskExecution

agentbreeder deploy --target aws
# → Validates infra exists (pre-flight checks)
# → Builds image, pushes to ECR
# → Creates/updates ECS service
# → Returns endpoint URL
```

**GCP/AWS/Azure all work today** for Scenario A (AWS/Azure hard-fail if any env var missing, but work if all are set).

### Scenario B — Fresh Account (Zero to Production)
*User has cloud credentials only — no pre-existing infrastructure*

```
User provides:
  AWS credentials (access key or IAM role)
  No VPC, no subnets, no cluster

agentbreeder deploy --target aws --provision
# AgentBreeder provisions in order:
#   1. VPC (10.0.0.0/16) + public/private subnets
#   2. Internet Gateway + NAT Gateway
#   3. Security Groups (agent-sg: 8080 inbound from ALB, outbound all)
#   4. ECS Cluster
#   5. IAM Execution Role (AmazonECSTaskExecutionRolePolicy)
#   6. ECR Repository
#   7. RDS PostgreSQL (if memory: declared in agent.yaml)
#   8. Builds + pushes image
#   9. Creates ECS Fargate service
#   10. Creates ALB + target group (if access.visibility: public)
# → Returns endpoint URL + outputs infra IDs to .agentbreeder/infra-state.json
```

---

## Three Deployment Surfaces

### Surface 1: CLI
```bash
# Resource creation
agentbreeder prompt create --name support-system --file prompts/support.md
agentbreeder tool create --name zendesk-lookup --schema tools/zendesk.json  
agentbreeder rag create --name product-docs --type vector --backend pgvector
agentbreeder rag ingest product-docs --dir ./docs/

# Local test with Ollama
agentbreeder chat --model ollama/llama3.2 --stream

# Cloud test with provider
agentbreeder chat --model claude-sonnet-4 --stream --rag product-docs --memory my-session

# Deploy (Scenario A: existing infra)
agentbreeder deploy --target gcp   # assumes GCP_PROJECT_ID set, VPC optional
agentbreeder deploy --target aws   # assumes AWS_VPC_SUBNETS etc set
agentbreeder deploy --target azure # assumes AZURE_RESOURCE_GROUP etc set

# Deploy (Scenario B: fresh account)
agentbreeder deploy --target gcp --provision
agentbreeder deploy --target aws --provision
agentbreeder deploy --target azure --provision

# Inspect & verify
agentbreeder describe my-agent
agentbreeder chat --agent https://my-agent-xxx.run.app --stream
```

### Surface 2: Python SDK
```python
from agenthub import Agent, deploy, InfraConfig

# Create + register resources
agent = Agent(
    name="support-agent",
    framework="langgraph",
    model="claude-sonnet-4",
    tools=["tools/zendesk-lookup"],
    rag=["kb/product-docs"],
    memory={"type": "buffer_window", "backend": "postgres"},
)

# Test locally  
response = agent.chat("What is your return policy?", model="ollama/llama3.2")

# Deploy — Scenario A (pass existing infra)
result = deploy(
    agent,
    target="aws",
    infra=InfraConfig(
        vpc_subnets=["subnet-xxx"],
        security_groups=["sg-xxx"],
        ecs_cluster="my-cluster",
        execution_role_arn="arn:aws:iam::123:role/ecsTaskExecution",
    )
)

# Deploy — Scenario B (auto-provision)
result = deploy(
    agent,
    target="aws",
    provision=True,   # creates all infra
)

print(result.endpoint_url)  # https://support-agent-xxx.us-east-1.elb.amazonaws.com
print(result.registry_url)  # agents/support-agent@1.0.0
```

### Surface 3: AgentBreeder Dashboard
```
Dashboard Deployment Wizard:
  Step 1: Select agent from registry (or create new)
  Step 2: Choose cloud target (GCP / AWS / Azure)
  Step 3: Choose infra mode:
    ● Bring your own infrastructure
      └ Fill: VPC ID, Subnets, Security Groups, Cluster, Execution Role
      └ Validate button → checks all infra exists
    ○ Provision for me (beta)
      └ Shows preview: list of resources to be created + estimated cost
      └ Require confirmation checkbox
  Step 4: Configure environment
    └ Environment variables (key-value pairs)
    └ Secret references (links to registered secrets)
    └ Scaling (min/max instances, CPU threshold)
  Step 5: Deploy
    └ Live progress stream (SSE)
    └ Phase indicators: Provision → Build → Push → Deploy → Health Check → Register
    └ Show endpoint URL + registry entry when done
```

---

## Full System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          DEVELOPER SURFACES                              │
├─────────────────┬───────────────────┬───────────────────────────────────┤
│   agentbreeder  │  agentbreeder-sdk │       AgentBreeder Dashboard       │
│      CLI        │   (Python / TS)   │  (React 19 + Tailwind + shadcn)   │
└────────┬────────┴────────┬──────────┴──────────────┬────────────────────┘
         │                 │                          │
         └─────────────────┴──────────────────────────┘
                           │  REST API + SSE
                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         FastAPI Backend  (api/)                          │
├──────────────┬────────────────┬───────────────┬─────────────────────────┤
│   Auth/RBAC  │ Registry CRUD  │  Deploy Jobs  │   Infra Provisioner     │
│  JWT + OAuth │ agents/tools/  │  (SSE jobs)   │  (NEW: GCP/AWS/Azure)   │
│  SAML/SSO    │ prompts/rag/   │               │                         │
│  Refresh tok │ models/MCP     │               │                         │
├──────────────┴────────────────┴───────────────┴─────────────────────────┤
│   LLM Gateway       │   RAG Service      │   Memory Service             │
│   (LiteLLM proxy)   │   (vector+graph)   │   (Postgres pgvector)        │
│   + cache + circuit │   pgvector+Neo4j   │   + session management       │
│   breaker           │                    │                              │
└─────────────────────┴────────────────────┴──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      Deploy Engine (engine/)                             │
├──────────────────────────────────────────────────────────────────────────┤
│  Config Parser → RBAC Check → Registry Resolver → Runtime Builder       │
│         ↓                                                               │
│  ┌──────────────────────────────────────────────────────────────┐       │
│  │               Infrastructure Provisioner (NEW)               │       │
│  │  detect mode: existing | provision                           │       │
│  │                                                              │       │
│  │  Scenario A: validate_existing_infra()                       │       │
│  │    └─ pre-flight: check VPC, subnets, SGs, registry, IAM    │       │
│  │                                                              │       │
│  │  Scenario B: provision_fresh_infra()                         │       │
│  │    GCP: AR repo + SA + VPC Connector + Cloud SQL (optional)  │       │
│  │    AWS: VPC + subnets + SGs + ECS cluster + IAM + ECR + RDS  │       │
│  │    Azure: RG + ACA env + Log Analytics + ACR + Azure PG      │       │
│  │    State: written to .agentbreeder/infra-state.json          │       │
│  └──────────────────────────────────────────────────────────────┘       │
│         ↓                                                               │
│  Container Build → Push to Registry → Deploy to Cloud → Health Check   │
│         ↓                                                               │
│  Sidecar Injection → Auto-Register in Registry → Return Endpoint        │
└─────────────────────────────────────────────────────────────────────────┘
                           │
         ┌─────────────────┼──────────────────┐
         ▼                 ▼                  ▼
    ┌─────────┐      ┌───────────┐      ┌───────────┐
    │   GCP   │      │    AWS    │      │   Azure   │
    │Cloud Run│      │ECS Fargate│      │Container  │
    │  + AR   │      │+ ECR +VPC │      │  Apps     │
    │  + SA   │      │+ RDS (opt)│      │ + ACR     │
    └────┬────┘      └─────┬─────┘      └─────┬─────┘
         │                 │                   │
         └─────────────────┴───────────────────┘
                    Deployed Agent
              + AgentBreeder Sidecar
                    │
    ┌───────────────┼────────────────┐
    ▼               ▼                ▼
  /invoke        /stream          /health
  (REST)         (SSE)            (probe)
```

---

## Component Architecture

### Backend Components

#### 1. Infra Provisioner (NEW — engine/provisioners/)
```
engine/provisioners/
├── base.py                    # InfraProvisioner ABC
│   ├── validate_existing()    # Pre-flight: check all infra exists
│   ├── provision_network()    # VPC, subnets, SGs, connectivity
│   ├── provision_registry()   # Container image registry
│   ├── provision_iam()        # Service accounts, roles, policies
│   ├── provision_database()   # Postgres instance (if memory declared)
│   ├── provision_compute_env()# ECS cluster, ACA environment, etc.
│   └── destroy()              # Teardown all provisioned infra
├── gcp.py                     # GCP-specific (~200 LOC)
│   ├── VPC Connector (if needed)
│   ├── Service Account + IAM bindings
│   ├── Artifact Registry repo (already exists, expand)
│   └── Cloud SQL PostgreSQL (if memory: in agent.yaml)
├── aws.py                     # AWS-specific (~500 LOC)
│   ├── VPC (10.0.0.0/16) + public/private subnets
│   ├── Internet Gateway + Route Tables + NAT Gateway
│   ├── Security Groups (agent, ALB, database)
│   ├── ECS Cluster
│   ├── IAM Execution Role + Task Role
│   ├── ECR Repository (already exists, keep)
│   ├── RDS PostgreSQL t3.micro (if memory declared)
│   └── ALB + Target Group + Listener (if public access)
├── azure.py                   # Azure-specific (~450 LOC)
│   ├── Resource Group
│   ├── Log Analytics Workspace (required for ACA)
│   ├── Container Apps Environment
│   ├── Azure Container Registry
│   ├── Managed Identity (per-agent)
│   └── Azure Database for PostgreSQL flexible server
└── state.py                   # InfraState: read/write .agentbreeder/infra-state.json
```

**InfraState format** (`.agentbreeder/infra-state.json`):
```json
{
  "cloud": "aws",
  "region": "us-east-1",
  "provisioned_by": "agentbreeder@1.8.0",
  "provisioned_at": "2026-05-17T10:00:00Z",
  "resources": {
    "vpc_id": "vpc-xxx",
    "public_subnets": ["subnet-xxx", "subnet-yyy"],
    "private_subnets": ["subnet-zzz"],
    "security_groups": {"agent": "sg-xxx", "alb": "sg-yyy", "db": "sg-zzz"},
    "ecs_cluster": "agentbreeder-my-agent",
    "execution_role_arn": "arn:aws:iam::123:role/agentbreeder-execution",
    "ecr_repository": "123.dkr.ecr.us-east-1.amazonaws.com/my-agent",
    "rds_endpoint": "my-agent.xxx.us-east-1.rds.amazonaws.com:5432",
    "alb_dns": "my-agent-xxx.us-east-1.elb.amazonaws.com"
  }
}
```

#### 2. Auth Service Enhancements (api/auth.py + api/services/auth.py)
```
New features:
├── OAuth2 providers
│   ├── Google (google-auth library, OIDC)
│   ├── GitHub (OAuth2 code flow)
│   └── Microsoft (MSAL, covers Azure AD + personal)
├── SAML 2.0 (python3-saml)
│   ├── Okta, Azure AD, Google Workspace IdP configs
│   └── SP metadata endpoint + ACS callback
├── Refresh tokens
│   ├── Short-lived access tokens (15m)
│   ├── Long-lived refresh tokens (30d, rotated on use)
│   └── Token rotation: issue new refresh token on every refresh
└── Per-resource ACLs
    ├── ResourcePermission model (resource_type, resource_id, user_id, actions[])
    ├── Actions: read | write | deploy | review | approve | admin
    └── Fallback to team-level RBAC if no per-resource ACL set
```

#### 3. LLM Gateway Enhancements
```
New features:
├── Request-level caching
│   ├── Redis TTL cache keyed on (model, prompt_hash, temperature)
│   ├── Configurable TTL per model tier (high=0, standard=60s, lite=300s)
│   └── Cache hit rate metric
├── Circuit breaker
│   ├── Per-provider: open after 5 consecutive failures
│   ├── Half-open probe: single request after 30s
│   └── Alert webhook on circuit open
├── Real-time cost alerts
│   ├── Budget threshold: 80%/90%/100% of monthly limit
│   └── Webhook + dashboard notification
└── Model routing policies (agent.yaml)
    ├── strategy: cost_optimized | latency_optimized | quality_optimized
    └── Auto-selects from provider catalog based on strategy
```

#### 4. CLI Resource Commands (cli/commands/)
```
New commands:
├── prompt.py
│   ├── agentbreeder prompt create --name <n> --file <path> [--version 1.0.0]
│   ├── agentbreeder prompt list [--team <t>]
│   ├── agentbreeder prompt test <name> --input "..." [--model ollama/llama3.2]
│   └── agentbreeder prompt get <name>
├── tool.py
│   ├── agentbreeder tool create --name <n> --schema <json_file>
│   ├── agentbreeder tool list
│   └── agentbreeder tool sandbox <name> --input '{"key": "val"}'
└── rag.py
    ├── agentbreeder rag create --name <n> --type vector|graph --backend pgvector|neo4j|memory
    ├── agentbreeder rag ingest <name> --dir <path> [--type graph]
    ├── agentbreeder rag search <name> --query "..." [--limit 5]
    └── agentbreeder rag list
```

Chat enhancements:
```
agentbreeder chat
  --model        Model to use (ollama/llama3.2, claude-sonnet-4, etc.)
  --stream       Real-time streaming output (SSE)
  --rag          RAG index name to inject context from
  --memory       Memory config name (persists to Postgres)
  --session-id   Resume existing conversation
  --agent        Deployed agent endpoint URL
  New: token counter display [↑ 1,234 tokens | $0.003]
  New: model health indicator [● ollama connected]
```

### Frontend Architecture (Dashboard Enhancements)

#### Design System (Current + Upgrades)
```
Current:
  - React 19.2.5 + Vite
  - Tailwind CSS 4.2 + shadcn/ui + Base UI
  - Dark-first: zinc-950 background + green-500 accent
  - @xyflow/react for visual builders
  - Geist Variable font

Upgrades needed:
  - Deployment Wizard (5-step guided flow)
  - Enhanced Chat Sandbox (streaming, token display, model comparison)
  - Infra Preview Component (tree of resources to be created with cost estimate)
  - OAuth login buttons (Google, GitHub)
  - Real-time progress SSE components (deploy + provision)
  - Notification system (WebSocket for cost alerts, health events)
```

#### Deployment Wizard (New Component)
```
Page: /deploy-wizard (or modal from agent detail page)

Step 1: Agent Selection
  └ Pick from registry (existing agents) OR "create first"

Step 2: Cloud Target
  └ GCP Cloud Run | AWS ECS Fargate | Azure Container Apps
  └ Region picker with latency/price indication

Step 3: Infrastructure Mode
  ● Bring Your Own (default)
    └ Per-cloud form: VPC ID, Subnets, SGs, Cluster, Execution Role
    └ [Validate Infrastructure] button → calls POST /api/v1/deployments/validate-infra
    └ Checklist: ✓ VPC found | ✓ Subnets valid | ✗ Security group missing
  ○ Provision for Me (beta badge)
    └ Preview panel: tree of resources to create
    └ Cost estimate: "$X/month estimated"
    └ "This will create N resources in your cloud account" confirmation checkbox

Step 4: Configuration
  └ Environment variables (key-value editor)
  └ Secrets (dropdown from registered secrets)
  └ Scaling (min/max instances slider, CPU target)
  └ Memory (if memory: declared, shows DB tier picker)

Step 5: Deploy
  └ Live SSE progress log:
    [✓] Validating config (0.2s)
    [✓] Checking RBAC permissions (0.1s)
    [⟳] Provisioning infrastructure... (AWS: VPC → Subnets → SGs → Cluster → IAM → ECR)
        └ VPC created: vpc-xxx
        └ Subnets: subnet-aaa, subnet-bbb
        └ Security group: sg-xxx
        └ ECS cluster: agentbreeder-my-agent
        └ Execution role: arn:aws:iam::123:role/...
        └ ECR: 123.dkr.ecr.us-east-1.amazonaws.com/my-agent
    [⟳] Building container image...
    [✓] Pushed to ECR (45s)
    [⟳] Deploying to ECS Fargate...
    [✓] Health check passed
    [✓] Registered in registry: agents/my-agent@1.0.0

  └ Result:
    Endpoint: https://my-agent-xxx.us-east-1.elb.amazonaws.com ↗
    [Copy URL] [Test in Playground] [View in Registry]
```

#### Chat Sandbox (Enhanced)
```
Current state: 1,326-line PlaygroundPage with streaming, tool-call viz
Enhancements:
  - Real-time token counter (↑ 1,234 in | ↓ 456 out | $0.003)
  - Model health indicator (● gpt-4o | ◌ claude offline)
  - Side-by-side model comparison (split pane, same prompt to 2+ models)
  - RAG context panel (show retrieved chunks alongside response)
  - Memory timeline (see conversation history, reset session)
  - Export conversation (JSON, Markdown)
  - Keyboard shortcuts (Cmd+K = new chat, Cmd+M = switch model)
```

### API Layer (New Endpoints)

```
# Infrastructure
POST   /api/v1/deployments/validate-infra    Validate existing infra (Scenario A)
POST   /api/v1/deployments/provision         Start infra provisioning job (Scenario B)
GET    /api/v1/deployments/provision/{id}    SSE stream of provisioning progress
DELETE /api/v1/deployments/provision/{id}    Rollback / destroy provisioned infra
GET    /api/v1/deployments/cloud-requirements/{cloud}  What env vars are needed

# Auth (new)
GET    /auth/oauth/{provider}               Initiate OAuth flow (google, github, microsoft)
GET    /auth/oauth/{provider}/callback      OAuth callback handler
POST   /auth/token/refresh                  Exchange refresh token for new access token
POST   /auth/saml/metadata                  SP metadata for SAML IdP config
POST   /auth/saml/callback                  SAML ACS callback

# RBAC (new resource-level)
GET    /api/v1/rbac/permissions/{resource_type}/{resource_id}   Get per-resource ACL
POST   /api/v1/rbac/permissions/{resource_type}/{resource_id}   Set per-resource ACL
DELETE /api/v1/rbac/permissions/{resource_type}/{resource_id}/{user_id}

# Prompts (CLI-accessible)
POST   /api/v1/prompts                       (exists, confirm CLI wraps this)
POST   /api/v1/prompts/test                  (exists, confirm CLI wraps this)

# Tools (CLI-accessible)
POST   /api/v1/tools                         (exists)
POST   /api/v1/tools/sandbox/execute         (exists)

# RAG (CLI-accessible)  
POST   /api/v1/rag                           (exists)
POST   /api/v1/rag/{id}/ingest               (exists)
POST   /api/v1/rag/{id}/search               (exists)

# LLM Gateway (new)
POST   /api/v1/gateway/cache/invalidate      Invalidate cached responses
GET    /api/v1/gateway/circuit-status        Circuit breaker status per provider
POST   /api/v1/gateway/alerts/budget         Set budget alert threshold
```

---

## Security Architecture

### Cloud Security Controls (per skill assessment)

| Control | GCP | AWS | Azure |
|---------|-----|-----|-------|
| Container registry | Artifact Registry (private) | ECR (private) | ACR (private) |
| IAM | Workload Identity Federation | Instance profiles + IRSA | Managed Identity |
| Secrets | Secret Manager | Secrets Manager | Key Vault |
| Network | VPC + Private Service Connect | VPC + PrivateLink | VNet + Private Endpoints |
| Postgres | Cloud SQL (Private IP, SSL) | RDS (private subnet, SSL) | Azure PG (VNet injection, SSL) |
| Audit | Cloud Audit Logs | CloudTrail | Azure Monitor |
| Image signing | Binary Authorization | ECR image scanning | Defender for Containers |

### Auth Security Controls
- Passwords: bcrypt (cost 12)
- JWT: HS256 → upgrade to RS256 (asymmetric, rotatable without invalidating all tokens)
- OAuth tokens: never stored, only used for identity assertion
- Refresh tokens: hashed in DB, rotated on every use (token rotation), 30-day expiry
- PKCE: required for all OAuth flows
- Rate limiting: 10 login attempts / 5 min / IP
- MFA: TOTP (Google Authenticator) for admin accounts

### Container Security (per Dockerfiles)
- Non-root USER directive required on all Dockerfiles
- Base images pinned (no :latest tags)
- No secrets in ENV/ARG
- Image scanning on every push (Trivy or cloud-native scanner)
- Sidecar: distroless base image

---

## Design System Specification

### Visual Identity (existing, document for consistency)
```
Colors (OKLCH):
  Background:  oklch(0.085 0 0)      — zinc-950 #09090b
  Surface:     oklch(0.110 0 0)      — zinc-900 #111113
  Border:      oklch(1 0 0 / 7%)     — subtle white 7%
  Accent:      oklch(0.723 0.219 142.5) — green-500 #22c55e
  Accent dim:  oklch(0.600 0.180 142.5) — green-600 #16a34a
  Text:        oklch(0.985 0 0)      — white-ish #fafafa
  Text muted:  oklch(0.640 0 0)      — zinc-400 #a1a1aa
  Error:       oklch(0.637 0.258 29.2)  — red-500 #ef4444
  Warning:     oklch(0.769 0.188 70.1)  — amber-500 #f59e0b
  
Font: Geist Variable (existing)
Radius: 0.5rem
Spacing: 4px/8px grid

New components to design:
  1. InfraProgressCard — live provisioning step list with spinners
  2. InfraPreviewTree — resource tree with cost annotations
  3. DeployWizard — 5-step multi-stage form with step indicators
  4. TokenCounter — inline model usage counter (↑/↓ tokens, $cost)
  5. ModelHealthBadge — ● green / ◌ gray / ✗ red per provider
  6. OAuthButtons — Google, GitHub, Microsoft login buttons
  7. CircuitBreakerStatus — gateway health panel
  8. BudgetAlertBanner — persistent warning at 80%+ budget usage
```

### UX Principles (from ui-ux-pro-max audit)
- Touch targets: ≥44×44px on all interactive elements
- Loading states: skeleton screens for >300ms operations; progress bars for provisioning
- Error recovery: every error message includes a recovery action
- Animations: 150–300ms ease-out; respect prefers-reduced-motion
- Forms: visible labels (not placeholder-only), inline validation on blur, error summary for multi-field
- Wizard: step indicator, allow back navigation, auto-save draft
- Charts: accessible colors, legend, tooltip, keyboard-navigable

---

## Implementation Plan (Sequenced)

### Phase 1 — Infra Provisioning (4 weeks) [HIGHEST PRIORITY]
Unlocks AWS and Azure for all users.

**Week 1:** Foundation
- `engine/provisioners/base.py` — InfraProvisioner ABC, InfraState model
- `engine/provisioners/state.py` — read/write infra-state.json
- API: `POST /api/v1/deployments/validate-infra` + `GET /api/v1/deployments/cloud-requirements/{cloud}`
- GCP provisioner: VPC Connector + Cloud SQL creation (expand existing)

**Week 2:** AWS Provisioner
- `engine/provisioners/aws.py` — VPC + subnets + SGs + ECS cluster + IAM + RDS
- Update `engine/deployers/aws_ecs.py` to call provisioner before deploy
- CLI: `agentbreeder deploy --target aws --provision` flag
- SSE: progress stream for provisioning jobs

**Week 3:** Azure Provisioner  
- `engine/provisioners/azure.py` — RG + Log Analytics + ACA env + ACR + Managed Identity + Azure PG
- Update `engine/deployers/azure_container_apps.py`
- CLI: `agentbreeder deploy --target azure --provision` flag

**Week 4:** Dashboard + Docs
- Deployment Wizard UI (5-step wizard component)
- Infra preview tree component
- SSE progress log component
- Docs: Update quickstart.mdx + deployment.mdx (both scenarios)
- Fix stale AWS/Azure claims in 10+ doc pages

### Phase 2 — CLI Resource Management (2 weeks)
Completes the local create-register-test loop.

**Week 5:**
- `cli/commands/prompt.py` — create/list/test
- `cli/commands/tool.py` — create/list/sandbox
- Register in `cli/main.py`
- Enhance `agentbreeder chat`: streaming SSE, token counter, `--rag`, `--memory`, `--session-id`

**Week 6:**
- `cli/commands/rag.py` — create/ingest/search/list
- Expand `cli/commands/search.py` (full cross-entity search)
- Update docs: cli-reference.mdx with new commands
- Integration test: full create-register-chat-deploy flow

### Phase 3 — Auth & RBAC Hardening (3 weeks)

**Week 7:** OAuth2
- OAuth2 providers: Google, GitHub, Microsoft (api/services/oauth.py)
- New routes: GET /auth/oauth/{provider} + /callback
- Dashboard: OAuth login buttons on login page
- Docs: authentication.mdx — add OAuth section

**Week 8:** Tokens + SAML
- JWT: RS256 asymmetric signing (key pair management)
- Refresh tokens: rotation, 30-day expiry, revocation
- SAML 2.0: Okta + Azure AD IdP configs
- Rate limiting on auth endpoints (slowapi)

**Week 9:** Resource ACLs + Org Hierarchy
- ResourcePermission model + migrations
- Per-resource ACL API endpoints
- Optional: Org hierarchy (org → team, not just flat teams)
- Dashboard: Permission editor on agent/prompt/tool detail pages

### Phase 4 — LLM Gateway Hardening (2 weeks)

**Week 10:**
- Redis TTL cache for LLM responses
- Circuit breaker per provider (circuitbreaker library or custom)
- Real-time cost alerts with budget threshold webhooks

**Week 11:**
- Model routing policies (cost/latency/quality strategies in agent.yaml)
- Dashboard: Circuit breaker status panel, budget alert config
- Docs: gateways.mdx — add cache + circuit breaker sections

### Phase 5 — MCP Sidecar + Search (1 week)

**Week 12:**
- `engine/deployers/mcp_sidecar.py` — full deploy logic (multi-container on all clouds)
- `cli/commands/search.py` — full cross-entity search
- Dashboard: Search improvements

---

## GitHub Issue Structure

### Epic Labels
- `epic:infra-provisioning` — Phase 1
- `epic:cli-resources` — Phase 2
- `epic:auth-rbac` — Phase 3
- `epic:llm-gateway` — Phase 4
- `epic:mcp-sidecar` — Phase 5
- `epic:docs` — Documentation updates

### Tracking Issues (to create)

#### [EPIC] Infrastructure Auto-Provisioning
- [x] #xxx: GCP provisioner — VPC Connector, Cloud SQL, SA expansion
- [ ] #xxx: AWS provisioner — VPC, subnets, SGs, ECS cluster, IAM, RDS, ALB
- [ ] #xxx: Azure provisioner — RG, Log Analytics, ACA env, ACR, Managed Identity, Azure PG
- [ ] #xxx: InfraState model — read/write .agentbreeder/infra-state.json
- [ ] #xxx: validate-infra API endpoint (Scenario A pre-flight)
- [ ] #xxx: cloud-requirements API endpoint (per-cloud env var spec)
- [ ] #xxx: SSE progress stream for provisioning jobs
- [ ] #xxx: CLI --provision flag (all three deployers)
- [ ] #xxx: Teardown provisioned infra (agentbreeder teardown --destroy-infra)
- [ ] #xxx: Dashboard deployment wizard UI (5-step)
- [ ] #xxx: Infra preview tree component + cost estimate
- [ ] #xxx: Docs — Scenario A guide (existing infra)
- [ ] #xxx: Docs — Scenario B guide (fresh account)
- [ ] #xxx: Fix stale AWS/Azure claims in 10+ doc pages

#### [EPIC] CLI Resource Management
- [ ] #xxx: agentbreeder prompt create/list/test
- [ ] #xxx: agentbreeder tool create/list/sandbox
- [ ] #xxx: agentbreeder rag create/ingest/search/list
- [ ] #xxx: agentbreeder chat — streaming, token counter, --rag, --memory flags
- [ ] #xxx: agentbreeder search — full cross-entity search
- [ ] #xxx: Docs — cli-reference.mdx new commands

#### [EPIC] Auth & RBAC
- [ ] #xxx: OAuth2 — Google provider
- [ ] #xxx: OAuth2 — GitHub provider
- [ ] #xxx: OAuth2 — Microsoft/Azure AD provider
- [ ] #xxx: SAML 2.0 — Okta + Azure AD IdP
- [ ] #xxx: JWT RS256 asymmetric signing
- [ ] #xxx: Refresh token rotation (30-day, revocation)
- [ ] #xxx: Rate limiting on auth endpoints
- [ ] #xxx: Resource-level ACLs (ResourcePermission model + API)
- [ ] #xxx: Dashboard — OAuth login buttons
- [ ] #xxx: Dashboard — Permission editor on resource detail pages
- [ ] #xxx: Docs — OAuth/SSO setup guide

#### [EPIC] LLM Gateway Hardening
- [ ] #xxx: Redis TTL cache for LLM responses
- [ ] #xxx: Circuit breaker per provider
- [ ] #xxx: Budget threshold alerts + webhooks
- [ ] #xxx: Model routing policies (cost/latency/quality strategies)
- [ ] #xxx: Dashboard — circuit breaker status panel
- [ ] #xxx: Docs — gateway caching + circuit breaker

#### [EPIC] MCP Sidecar + Search
- [ ] #xxx: MCP sidecar deployer — full implementation
- [ ] #xxx: Multi-container on GCP (expand existing)
- [ ] #xxx: Multi-container on AWS (ECS task definition)
- [ ] #xxx: Multi-container on Azure (ACA multi-container)
- [ ] #xxx: agentbreeder search — full implementation
