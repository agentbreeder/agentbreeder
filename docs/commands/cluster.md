# `agentbreeder cluster`

A local kind cluster for one stack (preview).

`cluster up` and `cluster down` hold a per-user lock (`~/.agentbreeder/locks/cluster.lock`, independent of `AGENTBREEDER_STACKS_DIR`); a second one at the same time fails with `CLUSTER_BUSY` (on `main`, not in a published preview).

`cluster up --restricted` (local `kind`, preview, not yet run) installs the service accounts, Roles and admission policies of #770 §7 and records a kubeconfig for the `ab-deployer` service account. `cluster policy print --namespace N` and `cluster rbac print --namespace N` print those objects for a cluster namespace; they read and change nothing. See [the Kubernetes target](../command-center.md#restricted-accounts-and-admission-policies-770-k2-preview-not-yet-run) (on `main`, not in a published preview).

Flags: `agentbreeder cluster --help`.
