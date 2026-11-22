# `agentbreeder cluster`

A local kind cluster for one stack (preview).

`cluster up` and `cluster down` hold a per-user lock (`~/.agentbreeder/locks/cluster.lock`, independent of `AGENTBREEDER_STACKS_DIR`); a second one at the same time fails with `CLUSTER_BUSY` (on `main`, not in a published preview).

`cluster up --restricted` (local `kind`, preview, not yet run) installs the service accounts, Roles and admission policies of #770 §7 and records a kubeconfig for the `ab-deployer` service account. `cluster policy print --namespace N` and `cluster rbac print --namespace N` print those objects for a cluster namespace; they read and change nothing. See [the Kubernetes target](../command-center.md#restricted-accounts-and-admission-policies-770-k2-preview-not-yet-run) (on `main`, not in a published preview).

## `cluster connect token` (preview, records nothing yet)

Checks an existing cluster for the `token` provider (#770 §4.2): any conformant cluster reached with short-lived service account tokens, minted with a dedicated mint kubeconfig (one cluster, one context, one user, data fields only; an `exec` plugin must sit where your user cannot write it). It checks the mint file's CA against the chain the API server presents and refuses a server whose certificate chains to a public CA (`KUBERNETES_CA_PUBLIC`) (and refuses to decide when Python has no system CA store, `KUBERNETES_TRUST_STORE_EMPTY`: a python.org Python on macOS has none until you run its `Install Certificates.command`, or set `SSL_CERT_FILE` to a CA bundle such as `python -m certifi` prints), pins `kubectl` by absolute path and hash, then with a deployer token reads the `kube-system` UID, the namespace labels, the Kubernetes version (1.30 or later) and the namespace's `ResourceQuota` (it must cap `count/secrets` and `count/deployments.apps`), and with an installer token computes `blocked_cidrs` from `--pod-cidr`, `--service-cidr` and `--node-cidr` (cross-checked against nodes, pods and Services; IPv4 only; at most 32).

The admission policy check it needs next is not wired in yet (K2 built the policies and `cluster up --restricted`, not this check), so the command then refuses with `KUBERNETES_POLICY_UNCHECKED` and records nothing; deploys and `promote` to a `token` environment are refused with the same code. Run once against a local `kind` cluster (Kubernetes 1.37) reached on a LAN address (macOS, Docker Desktop, kubectl 1.31.4, 2026-11-21; `tests/test_kubernetes_token_kind.py`): the CA, mint, UID, namespace-label, version, quota and `--pod-cidr`/`--service-cidr` checks passed, and the command then refused with `KUBERNETES_POLICY_UNCHECKED`. With a stub policy check in the test, the block was returned, and a deployer token session authorized, listed quotas and was refused Secrets. `--node-cidr` was not passed, no other `connect` refusal (for example `KUBERNETES_CA_PUBLIC`) was exercised, and no other cluster or kubectl version has been run. On `main`, not in a published preview.

If minting fails, `KUBERNETES_MINT_FAILED` shows one line of kubectl's stderr with the mint's credentials, tokens, CA data and home paths replaced; when the mint uses an `exec` plugin none of it is shown, since a plugin may print a credential in no recognisable shape.

Token sessions live in `~/.agentbreeder/kube-sessions/` for one command; `doctor` reports any a killed command left behind.

Flags: `agentbreeder cluster --help`.
