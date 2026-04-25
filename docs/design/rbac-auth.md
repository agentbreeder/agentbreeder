# RBAC & Authentication Design

**Status:** Phase 1–3 implemented (commit `fde2105`, `3703385`) — Phase 4 (SSO) planned
**GitHub issue:** [#128](https://github.com/agentbreeder/agentbreeder/issues/128)
**Last updated:** April 2026

---

## Problem Statement

AgentBreeder's original codebase had a fully-designed RBAC system that was never wired in: `api/middleware/rbac.py` existed but was never called, ~150 API routes were unauthenticated, `TeamService` stored membership in-memory (lost on restart), and the approval workflow had zero persistence. This document captures the design decisions that drove the implementation.

---

## Identity Model

```
Org (AgentBreeder instance)
 └── Teams  (engineering, data-science, ops)
      ├── Users              email + password OR SSO provider
      ├── Service Principals CI/CD bots, automation agents
      └── Groups             named sets of users ("ml-leads", "senior-engineers")
```

Every principal that joins a team is automatically issued a scoped LiteLLM virtual key. Keys are revoked automatically when the principal leaves the team or is deactivated.

## Platform Roles

| Role | Deploy | Approve | Manage Teams | Billing |
|---|:---:|:---:|:---:|:---:|
| `admin` | yes | yes | yes | yes |
| `deployer` | yes | no | no | no |
| `contributor` | submit only | no | no | no |
| `viewer` | no | no | no | no |

**Role hierarchy** (`api/services/team_service.py::ROLE_HIERARCHY`): `viewer < contributor < deployer < admin`. `require_role("deployer")` accepts admins and deployers.

## Per-Asset ACL

Eight asset types have fine-grained ACLs stored in `resource_permissions`: `agent`, `prompt`, `tool`, `memory`, `rag`, `knowledge_base`, `model`, `mcp_server`.

| Principal | read | use | write | deploy | publish | admin |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Owner (creator) | ✓ | ✓ | ✓ | requires approval | ✓ | ✓ |
| Owner's team | ✓ | ✓ | — | — | — | — |
| Other teams | ✓ | — | — | — | — | — |
| Unauthenticated | — | — | — | — | — | — |

Default ACLs are granted automatically at asset creation time (`rbac_service.py::grant_default_permissions()`).

**Why this granularity:** `use` (call the agent/tool) is separated from `write` (edit config) and `deploy` (push to cloud). A data-science team can use an engineering team's tools without being able to modify or redeploy them.

## Approval Workflow

```
contributor submits asset
       │
       ▼
asset_approval_requests (status: pending)  →  admin group notified
       │
       ├── Admin approves  →  status: approved  →  deploy may proceed
       └── Admin rejects   →  status: rejected  →  contributor sees reason
```

The approval workflow in `asset_approval_requests` is for **asset deployment gates** — separate from the HITL tool approval in `api/routes/approvals.py` (which is for human-in-the-loop pauses during agent execution).

**Deploy gate** (`engine/governance.py::check_deploy_approved()`): Runs between Step 2 (RBAC check) and Step 5 (provision). If `agent.yaml::access.require_approval: true` and no approved request exists, the deploy fails before any cloud resources are touched or LiteLLM keys minted. Admins bypass.

## Credential Lifecycle

Four scoped credential types, all stored in `litellm_key_refs`:

| `scope_type` | Issued to | Minted when | Revoked when |
|---|---|---|---|
| `user` | Human team member | Joins team | Leaves team / deactivated |
| `service_principal` | CI/CD bot | SP created | SP deleted |
| `agent` | Deployed agent (via APS sidecar) | Agent deployed | Agent torn down |
| `aps_sidecar` | APS sidecar container | Agent deployed | Agent torn down |

**Key design decision:** The agent container never holds LiteLLM credentials directly. It holds only `APS_URL` and `APS_TOKEN`. The APS sidecar holds `LITELLM_API_KEY`. This means rotating a LiteLLM key requires updating only the sidecar, with zero agent downtime.

**`aps_sidecar` token:** A JWT-signed credential carrying `{agent_name, team_id, deploy_id}`. The APS verifies this payload on every request — prevents one agent calling another agent's sidecar. Stored with `is_aps_token=true` flag in `litellm_key_refs`.

## LiteLLM Budget Attribution

Teams register with LiteLLM on creation (`POST /team/new`). The returned `litellm_team_id` is stored on the `teams` table. All four key types carry `team_id`, so every LLM call is attributed to the correct team's budget:

```
teams.litellm_team_id  (registered at team creation)
        │
        ├── user keys         (auto-minted on membership insert)
        ├── service principal keys (minted on SP creation)
        └── agent keys        (minted at deploy, held by APS sidecar)
```

Budget caps enforced at the LiteLLM proxy level — once a team's budget is exhausted, all its keys are rate-limited until reset.

## Database Schema

```sql
-- Phase 1: asset ownership (migration 014)
ALTER TABLE agents      ADD COLUMN created_by UUID REFERENCES users(id);
ALTER TABLE agents      ADD COLUMN team_id    UUID REFERENCES teams(id);
-- (same for prompts, tools, models, mcp_servers)

-- Phase 2: ACL and approvals (migration 015)
CREATE TABLE resource_permissions (
  id             UUID PRIMARY KEY,
  resource_type  TEXT NOT NULL,          -- agent | prompt | tool | ...
  resource_id    UUID NOT NULL,
  principal_type TEXT NOT NULL,          -- user | team | service_principal | group
  principal_id   TEXT NOT NULL,
  actions        JSONB NOT NULL,         -- ["read", "use", "write", ...]
  granted_by     UUID REFERENCES users(id),
  granted_at     TIMESTAMPTZ DEFAULT now(),
  UNIQUE(resource_type, resource_id, principal_type, principal_id)
);

CREATE TABLE asset_approval_requests (
  id            UUID PRIMARY KEY,
  asset_type    TEXT NOT NULL,
  asset_id      UUID NOT NULL,
  asset_version TEXT NOT NULL,
  submitter_id  UUID REFERENCES users(id),
  status        TEXT NOT NULL DEFAULT 'pending',
  approver_id   UUID REFERENCES users(id),
  reason        TEXT,
  message       TEXT,
  created_at    TIMESTAMPTZ DEFAULT now(),
  decided_at    TIMESTAMPTZ
);

-- Phase 3: service principals (migration 015)
CREATE TABLE service_principals (
  id             UUID PRIMARY KEY,
  name           TEXT UNIQUE NOT NULL,
  team_id        UUID REFERENCES teams(id),
  role           TEXT NOT NULL DEFAULT 'deployer',
  allowed_assets JSONB,
  created_by     UUID REFERENCES users(id),
  last_used_at   TIMESTAMPTZ,
  is_active      BOOL NOT NULL DEFAULT true,
  created_at     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE principal_groups (
  id         UUID PRIMARY KEY,
  name       TEXT NOT NULL,
  team_id    UUID REFERENCES teams(id),
  member_ids JSONB NOT NULL DEFAULT '[]',
  UNIQUE(team_id, name)
);

-- LiteLLM team attribution (pending migration)
ALTER TABLE teams ADD COLUMN litellm_team_id TEXT NULL;
-- litellm_key_refs additions (pending)
ALTER TABLE litellm_key_refs ADD COLUMN is_aps_token BOOL NOT NULL DEFAULT false;
```

## API Endpoints

```
# ACL management
GET    /api/v1/rbac/permissions                          list permissions (filterable)
POST   /api/v1/rbac/permissions                          grant permission
DELETE /api/v1/rbac/permissions/{id}                     revoke permission
POST   /api/v1/rbac/permissions/check                    check permission

# Asset approval queue
GET    /api/v1/rbac/approvals        ?status=pending     list approval requests
POST   /api/v1/rbac/approvals                            submit asset for approval
GET    /api/v1/rbac/approvals/{id}                       get request detail
POST   /api/v1/rbac/approvals/{id}/approve               admin only
POST   /api/v1/rbac/approvals/{id}/reject                admin only

# Service principals
GET    /api/v1/rbac/service-principals
POST   /api/v1/rbac/service-principals
DELETE /api/v1/rbac/service-principals/{id}
POST   /api/v1/rbac/service-principals/{id}/rotate-key

# Groups
GET    /api/v1/rbac/groups
POST   /api/v1/rbac/groups
POST   /api/v1/rbac/groups/{id}/members
DELETE /api/v1/rbac/groups/{id}/members/{user_id}

# Virtual keys
GET    /api/v1/rbac/keys
POST   /api/v1/rbac/keys
DELETE /api/v1/rbac/keys/{alias}

# HITL tool approval (separate from asset approvals)
POST   /api/v1/approvals/            agent submits tool call for human approval
GET    /api/v1/approvals/            operator polls pending approvals
POST   /api/v1/approvals/{id}/approve
POST   /api/v1/approvals/{id}/reject
```

## Pending Work

- [ ] **Deploy approval gate** — `engine/governance.py::check_deploy_approved()` not yet called in deploy pipeline
- [ ] **ACL enforcement on non-agent routes** — `check_permission()` only in `agents.py`; missing from `prompts.py`, `tools.py`, `rag.py`, `mcp_servers.py`, `models.py`
- [ ] **`litellm_team_id` migration** — teams table missing this column; budget enforcement at proxy blocked
- [ ] **`aps_sidecar` scope type** — `KeyScopeType.aps_sidecar` not yet in enum; `is_aps_token` column not yet added
- [ ] **`approvals.py` HITL persistence** — still in-memory dict; needs DB or Redis backend
- [ ] **Phase 4: Enterprise SSO** — `okta`, `azure_ad`, `aws_iam`, `google`, `saml` (separate issue)
