# `agentbreeder cluster`

A local kind cluster for one stack (preview).

`cluster up` and `cluster down` hold a per-user lock (`~/.agentbreeder/locks/cluster.lock`, independent of `AGENTBREEDER_STACKS_DIR`); a second one at the same time fails with `CLUSTER_BUSY` (on `main`, not in a published preview).

`cluster up --restricted` (local `kind`, preview, not yet run) installs the service accounts, Roles and admission policies of #770 §7 and records a kubeconfig for the `ab-deployer` service account. `cluster policy print --namespace N` and `cluster rbac print --namespace N` print those objects for a cluster namespace; they read and change nothing. See [the Kubernetes target](../command-center.md#restricted-accounts-and-admission-policies-770-k2-preview-not-yet-run) (on `main`, not in a published preview).

## `cluster connect token` (preview, records nothing yet)

Checks an existing cluster for the `token` provider (#770 §4.2): any conformant cluster reached with short-lived service account tokens, minted with a dedicated mint kubeconfig (one cluster, one context, one user, data fields only; an `exec` plugin must sit where your user cannot write it). It checks the mint file's CA against the chain the API server presents and refuses a server whose certificate chains to a public CA (`KUBERNETES_CA_PUBLIC`) (and refuses to decide when Python has no system CA store, `KUBERNETES_TRUST_STORE_EMPTY`), pins `kubectl` by absolute path and hash, then with a deployer token reads the `kube-system` UID, the namespace labels, the Kubernetes version (1.30 or later) and the namespace's `ResourceQuota` (it must cap `count/secrets` and `count/deployments.apps`), and with an installer token computes `blocked_cidrs` from `--pod-cidr`, `--service-cidr` and `--node-cidr` (cross-checked against nodes, pods and Services; IPv4 only; at most 32).

The admission policy check it needs next is not wired in yet (K2 built the policies and `cluster up --restricted`, not this check), so the command then refuses with `KUBERNETES_POLICY_UNCHECKED` and records nothing; deploys and `promote` to a `token` environment are refused with the same code. Nothing here has run against a real cluster. On `main`, not in a published preview.

Token sessions live in `~/.agentbreeder/kube-sessions/` for one command; `doctor` reports any a killed command left behind.

```text
agentbreeder cluster connect token --server https://HOST[:PORT] --mint-kubeconfig FILE \
    --registry HOST/PATH --pod-cidr CIDR --service-cidr CIDR [--node-cidr CIDR] \
    [--namespace NAMESPACE] [--environment ENV] [--tls-server-name NAME] \
    [--token-minutes 10-60] [--kubectl PATH] [--json]
```

Flags: `agentbreeder cluster --help`.
