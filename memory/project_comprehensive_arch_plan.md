---
name: project-comprehensive-arch-plan
description: Comprehensive architecture review May 2026 — 5 epics, 25 GitHub issues, two deployment scenarios (existing infra vs fresh account)
metadata:
  type: project
---

Full architecture + implementation plan created 2026-05-17.

**Why:** User wants end-to-end CLI → register → test → cloud deploy across GCP/AWS/Azure with auto-infra provisioning when cloud environment doesn't exist yet.

**How to apply:** Reference this when working on any of the 5 epics. Check GitHub issues #377–#401 for specs.

## The Two Deployment Scenarios
- **Scenario A** (existing infra): pass VPC IDs / subnets / SGs via env vars → AgentBreeder validates + deploys app layer only. Works TODAY for all 3 clouds.
- **Scenario B** (fresh account): `--provision` flag → AgentBreeder creates all infra idempotently, writes `.agentbreeder/infra-state.json`, then deploys.

## GitHub Epic Issues
- #377 Infra Auto-Provisioning (P0) — sub-issues: #382–#389
- #378 CLI Resource Management (P1) — sub-issues: #390–#393
- #379 Auth & RBAC Hardening (P2) — sub-issues: #394–#397
- #380 LLM Gateway Hardening (P3) — sub-issues: #398–#399
- #381 MCP Sidecar + Chat Sandbox (P4) — sub-issues: #400–#401

## Implementation order
Phase 1 (wks 1-4): #377 infra provisioning — biggest blocker
Phase 2 (wks 5-6): #378 CLI commands
Phase 3 (wks 7-9): #379 auth/RBAC
Phase 4 (wks 10-11): #380 gateway
Phase 5 (wk 12): #381 MCP + search

## Key technical facts
- AWS provisioner needs ~500 LOC: VPC + 4 subnets + 3 SGs + ECS cluster + IAM + RDS + ALB
- Azure provisioner needs ~450 LOC: RG + Log Analytics + ACA env + ACR + Managed Identity + Azure PG
- GCP provisioner needs ~200 LOC: VPC Connector + Cloud SQL + SA (AR repo already auto-creates)
- Auth: currently email/password JWT HS256 only, no OAuth/SSO, no refresh tokens
- Dashboard: 45+ pages, React 19 + Tailwind 4.2 + shadcn/ui, dark zinc+green design system
- Website: 10+ pages have stale AWS/Azure/K8s claims (only Local + GCP shipped in v1.7.1)
