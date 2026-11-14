# `agentbreeder cluster`

A local kind cluster for one stack (preview).

`cluster up` and `cluster down` hold a per-user lock (`~/.agentbreeder/locks/cluster.lock`, independent of `AGENTBREEDER_STACKS_DIR`); a second one at the same time fails with `CLUSTER_BUSY` (on `main`, not in a published preview).

Flags: `agentbreeder cluster --help`.
