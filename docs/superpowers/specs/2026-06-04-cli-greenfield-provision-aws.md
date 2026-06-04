# Spec: CLI greenfield provisioning for AWS (`agentbreeder deploy --provision`)

> Issue: #537 · Refs #383 · Scope: **AWS only** (GCP/Azure follow the same shape later)
> Status: approved-to-implement (user pre-authorized in the c→a→b→implement flow)

## Summary

Let a user with a **fresh AWS account** (no VPC, subnets, ECS cluster, or IAM role)
run one command and get a fully-provisioned, serving agent:

```bash
agentbreeder deploy agent.yaml --target ecs-fargate --provision
```

The greenfield provisioner (`AWSProvisioner.provision()`) already creates the full
footprint and returns an `InfraState`. The missing glue is: a CLI flag, a branch in
the deploy pipeline that calls `provision()` **before** the deployer, a **mapper** that
turns `InfraState.resources` into the `deploy.env_vars` the ECS deployer already reads,
state persistence, and full-footprint teardown. After the mapper injects the IDs, the
existing BYO deploy path runs unchanged.

## Inputs

- **CLI flag** `--provision` / `-p` on `agentbreeder deploy` — `bool`, default `False`.
  Local mode only for this scope (remote/Studio tracked separately).
- **`agent.yaml`** — unchanged. Greenfield reads `deploy.region`, `deploy.env_vars`
  (creds + optional `AWS_MULTI_AZ_NAT`, `AWS_ACM_CERTIFICATE_ARN`), and derives:
  - `AWS_AGENT_NAME` ← `config.name`, `AWS_AGENT_VERSION` ← `config.version`
  - `AWS_HAS_MEMORY` ← agent declares `memory:`/KB (so RDS is provisioned)
  - `AWS_ACCESS_VISIBILITY` ← `access.visibility` (so ALB is provisioned when `public`)
- **Credentials** — standard boto3 chain (env / `~/.aws/credentials` / instance profile).

## Outputs / side effects

- Calls `provisioner_for("aws").provision(InfraValidationInput(cloud="aws", region, mode="simple", fields=...), progress=<console>)`.
- Injects mapped IDs into `config.deploy.env_vars` (see Mapper below).
- Auto-provisions the data tier (RDS/Redis) into the **new** VPC (existing behavior, now fed greenfield network).
- Persists the merged footprint to `.agentbreeder/infra-state.json` (mode `provisioned`).
- Deploys + health-checks + registers via the existing pipeline; returns the endpoint URL.
- `agentbreeder teardown <agent>` removes the **entire** greenfield footprint (tag-gated).

## The mapper (the only genuinely new logic)

New module `engine/deployers/_greenfield.py`:

```python
def infra_state_to_env(cloud: str, state: InfraState) -> dict[str, str]:
    """Map a greenfield InfraState into the deploy.env_vars the deployer reads."""
```

AWS mapping (verified against `aws_ecs.py::_extract_ecs_config` and `aws.py::provision`):

| env var (deployer reads) | source in `state.resources` |
|---|---|
| `AWS_ECS_CLUSTER` | `["ecs_cluster"]["name"]` |
| `AWS_EXECUTION_ROLE_ARN` | `["iam_execution_role"]["arn"]` |
| `AWS_VPC_SUBNETS` | `,`.join(`["network"]["public_subnet_ids"]`) — agent task ENI gets a public IP |
| `AWS_SECURITY_GROUPS` | `["security_groups"]["agent_sg_id"]` |
| `AWS_VPC_ID` | `["vpc"]["vpc_id"]` — so data-backend auto-provision lands in the new VPC |
| `AWS_REGION` | `state.region` |
| `AWS_DB_SUBNETS` (new, optional) | `,`.join(`["network"]["private_subnet_ids"]`) — RDS/Redis prefer private |

Only set keys the user didn't already supply (`env.setdefault(...)`), so an explicit
BYO field always wins.

## Pipeline ordering (engine/builder.py Step 5)

```
if provision and cloud in {aws} and BYO infra absent:
    state = await provisioner_for(cloud).provision(payload, progress)   # NEW
    env.update(infra_state_to_env(cloud, state) minus user-set keys)    # NEW (mapper)
    merge state into .agentbreeder/infra-state.json                     # NEW
await deployer.provision(config)              # existing — now finds populated env_vars
await self._auto_provision_data_backends(...) # existing — lands in the new VPC
```

`DeployEngine.deploy(...)` gains `provision: bool = False`; `cli/commands/deploy.py`
threads the flag through `_deploy_local`.

## Acceptance criteria

- [ ] `deploy --target ecs-fargate --provision` on an account with **no** VPC/cluster provisions the footprint, deploys, health-checks green, registers — **no BYO env_vars** required.
- [ ] `infra_state_to_env("aws", state)` returns the exact 6–7 keys above (unit-tested against a representative `InfraState`).
- [ ] Step 5 ordering verified with a fake provisioner: greenfield → mapper → data-backend → deploy; `.agentbreeder/infra-state.json` holds the greenfield + data footprint.
- [ ] Explicit BYO `env_vars` are never overwritten by the mapper (`setdefault` semantics).
- [ ] `--provision` against a cloud whose `provision()` is still `NotImplementedError` (gcp/azure) fails fast with a clear "use Studio wizard / not yet on CLI" message — never a raw traceback.
- [ ] `agentbreeder teardown <agent>` calls `provisioner.destroy(state)` for the greenfield footprint and leaves **zero** orphans (tag-gated; refuses untagged).
- [ ] Re-running `deploy --provision` with an existing `infra-state.json` **reuses** infra (no duplicate VPC).
- [ ] Provisioner `progress` messages stream to the Rich console during Step 5.
- [ ] Gates green; docs flipped roadmap→shipped.

## Edge cases & error handling

- **Partial provision then deploy fails** → infra is already persisted; print the exact `agentbreeder teardown <agent>` command to clean up. (Auto-rollback is a nice-to-have, not required for v1.)
- **`--provision` + BYO env_vars both present** → BYO wins; skip greenfield provisioning, log that existing infra was detected.
- **Missing AWS creds** → fail in Step 5 with the boto3 credential error surfaced clearly (no silent fallback).
- **Cloud != aws with `--provision`** → fast, friendly error (AWS-only this release).
- **`provision()` idempotency** → the provisioner is create-if-not-exists (tag-filtered), but we still short-circuit on an existing state file to avoid slow re-describes.

## Out of scope

- GCP / Azure CLI greenfield (same mapper shape; follow-up).
- Remote (`--remote`) / Studio orchestrator greenfield serve hand-off (the `deployer_for()` gap — separate, larger work noted in #537).
- App Runner greenfield (the provisioner builds ECS Fargate only).
- Multi-region / failover / custom-domain wiring beyond the existing ALB+ACM path.

## Open questions (resolved with defaults unless overridden)

1. Agent in public vs private subnets? → **Default: public subnets + `assignPublicIp=ENABLED`** (matches the verified BYO path; no NAT dependency for the task). Data tier → private subnets.
2. Auto-rollback on deploy failure? → **Default: no**; print the teardown command. Revisit if users hit it.
