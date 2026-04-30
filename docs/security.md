# AgentBreeder Security & Encryption-at-Rest Runbook

This document is the auditable artifact cited by the
``encryption_at_rest_documented`` compliance control (see
``engine/compliance/controls.py`` and issue #208). It describes how
encryption-at-rest is configured for every data store AgentBreeder touches.

## Storage layers and encryption requirements

| Layer | Data | Encryption requirement | How AgentBreeder enforces it |
|-------|------|------------------------|------------------------------|
| PostgreSQL (registry / audit / cost / incidents / compliance_scans) | All persisted state | AES-256 at rest | Use AWS RDS / GCP Cloud SQL / Azure Database with default encryption ON; or self-managed Postgres on encrypted EBS / PD / Azure Disks. |
| Redis (cache, rate limit) | Ephemeral | At rest if replicated to disk; otherwise ephemeral | ElastiCache encryption, Memorystore CMEK, or Azure Cache for Redis SSE. |
| Container images | Code, runtimes | Encrypted registries | ECR, Artifact Registry, ACR — all encrypt at rest by default. |
| Secrets | API keys, DB credentials | Provider-managed envelope encryption | AWS Secrets Manager / GCP Secret Manager / HashiCorp Vault — never the ``env`` backend in production. The ``secrets_backend_not_env`` control enforces this. |
| Object storage (RAG, evidence reports) | User content | SSE-S3 / GCS-managed / Azure SSE | Set in the bucket policy at deploy time. |

## Required configuration

1. Set ``DATABASE_URL`` with ``?sslmode=require`` (or ``verify-full`` in
   regulated environments). The ``db_ssl_enabled`` control verifies this at
   runtime via ``ssl_is_used()``.
2. Set the workspace secrets backend to ``aws``, ``gcp``, ``vault``, or
   ``keychain``. The ``secrets_backend_not_env`` control verifies this.
3. Enable encryption at rest on the underlying database engine (RDS,
   Cloud SQL, Azure DB) before pointing AgentBreeder at it. There is no
   in-process option to encrypt-at-rest after the fact.
4. Enable bucket-level SSE on any object store used for RAG indices or
   evidence-report exports.

## Audit retention

Audit events live in the ``audit_events`` table and are retained for at
least 365 days. The ``audit_log_retention`` control verifies this against
the live row history.

## Incident response

Operational incidents are persisted in the ``incidents`` table (#207) and
surfaced on the AgentOps dashboard. Compliance scans are persisted in the
``compliance_scans`` table (#208). Both are covered by the same encryption
guarantees as the rest of the registry — they live in PostgreSQL and inherit
its at-rest configuration.
