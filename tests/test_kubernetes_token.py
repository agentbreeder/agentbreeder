"""The `token` cluster provider (#770 §4.2, slice K1): the operator config block, the dedicated
mint kubeconfig, per-operation sessions, the sweep, `connect token` and its `blocked_cidrs`
(§6.4), against a fake kubectl. No cluster or Docker is used; the opt-in `kind` check is
tests/test_kubernetes_token_kind.py.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import signal
import ssl
import stat
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from conftest import v2_manifest_data, write_private

from agentbreeder import cli, cluster_token, kubernetes_providers, kubernetes_token
from agentbreeder.deployment import DeploymentService
from agentbreeder.kubernetes import Kubernetes
from agentbreeder.kubernetes_providers import PROVIDERS, Binding
from agentbreeder.kubernetes_token import TOKEN, TokenProvider
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import KubernetesTarget, api_server_refusal
from agentbreeder.store import DeploymentError, Store

UID = "11111111-2222-3333-4444-555555555555"
SERVER = "https://203.0.113.20:6443"
# Any base64 body passes the PEM parser; the real-TLS test below builds real certificates.
CA_PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    + base64.b64encode(b"a test certificate authority, DER stand-in").decode()
    + "\n-----END CERTIFICATE-----\n"
)
OTHER_PEM = CA_PEM.replace(
    base64.b64encode(b"a test certificate authority, DER stand-in").decode(),
    base64.b64encode(b"another certificate authority").decode(),
)


def bound_token(account: str = "ab-deployer", minutes: int = 30) -> str:
    claims = {"iat": 1000, "exp": 1000 + minutes * 60, "sub": account}
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{body}.c2lnbmF0dXJl"


MINTED = bound_token()
OPERATOR_SECRET = "operator-static-credential"
HOSTILE = {
    "KUBECONFIG": "/evil/config",
    "KUBECACHEDIR": "/evil/cache",
    "KUBECTL_EXTERNAL_DIFF": "/evil/diff",
    "HTTPS_PROXY": "http://evil:3128",
    "https_proxy": "http://evil:3128",
    "HTTP_PROXY": "http://evil:3128",
    "NO_PROXY": "*",
    "SSL_CERT_FILE": "/evil/ca.pem",
    "SSL_CERT_DIR": "/evil/certs",
    "HELM_PLUGINS": "/evil/plugins",
    "PATH": "/evil/bin:/usr/bin",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mint_config(server=SERVER, ca=CA_PEM, user=None, cluster_extra=None) -> dict:
    cluster = {
        "server": server,
        "certificate-authority-data": base64.b64encode(ca.encode()).decode(),
    }
    cluster.update(cluster_extra or {})
    return {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": "c", "cluster": cluster}],
        "contexts": [{"name": "operator@c", "context": {"cluster": "c", "user": "u"}}],
        "users": [{"name": "u", "user": user or {"token": OPERATOR_SECRET}}],
        "current-context": "operator@c",
    }


def _skip_flags(argv: list[str]) -> list[str]:
    """The verb part of a kubectl argv: global flags with values are skipped."""
    rest, index = argv[1:], 0
    while index < len(rest) and rest[index].startswith("--") and "=" not in rest[index]:
        index += 2
    return rest[index:]


class FakeKubectl:
    """`subprocess.run` for the pinned kubectl: mints tokens and answers reads."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.envs: list[dict] = []
        self.kwargs: list[dict] = []
        self.minted: list[str] = []
        self.configs: list[str] = []
        self.mint_fail = False
        self.lifetime = 30
        self.mint_output: str | None = None  # None: a token naming the account
        self.uid = UID
        self.version = {"major": "1", "minor": "31+"}
        self.labels = {
            "app.kubernetes.io/managed-by": "agentbreeder",
            "pod-security.kubernetes.io/enforce": "restricted",
        }
        self.quota = {"count/secrets": "50", "count/deployments.apps": "20"}
        self.nodes = [
            {
                "spec": {"podCIDRs": ["10.244.0.0/24"]},
                "status": {
                    "addresses": [
                        {"type": "InternalIP", "address": "10.0.0.5"},
                        {"type": "ExternalIP", "address": "198.51.100.7"},
                        {"type": "Hostname", "address": "node-1"},
                    ]
                },
            }
        ]
        self.pods = [{"status": {"podIP": "10.244.0.9", "podIPs": [{"ip": "10.244.0.9"}]}}]
        self.services = [
            {"spec": {"clusterIP": "10.96.0.10", "clusterIPs": ["10.96.0.10"]}},
            {"spec": {"clusterIP": "None"}},
        ]

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        self.envs.append(dict(kwargs.get("env") or {}))
        self.kwargs.append(kwargs)
        verb = _skip_flags(list(argv))
        output, code = "", 0
        if verb[:2] == ["create", "token"]:
            config = argv[argv.index("--kubeconfig") + 1]
            self.configs.append(Path(config).read_text())
            account = verb[-1]
            self.minted.append(account)
            code = 1 if self.mint_fail else 0
            output = (
                self.mint_output
                if self.mint_output is not None
                else bound_token(account, self.lifetime)
            )
        else:
            config = argv[argv.index("--kubeconfig") + 1]
            self.configs.append(Path(config).read_text())
            if verb[:3] == ["get", "namespace", "kube-system"]:
                output = self.uid
            elif verb[:2] == ["get", "namespace"]:
                output = json.dumps({"metadata": {"labels": self.labels}})
            elif verb[:1] == ["version"]:
                output = json.dumps({"serverVersion": self.version})
            elif verb[:2] == ["get", "resourcequotas"]:
                output = json.dumps({"items": [{"spec": {"hard": self.quota}}]})
            elif verb[:2] == ["get", "nodes"]:
                output = json.dumps({"items": self.nodes})
            elif verb[:2] == ["get", "pods"]:
                output = json.dumps({"items": self.pods})
            elif verb[:2] == ["get", "services"]:
                output = json.dumps({"items": self.services})
        return SimpleNamespace(returncode=code, stdout=output.encode(), stderr=b"secret stderr")


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    root = tmp_path / "home" / ".agentbreeder" / "kube-sessions"
    monkeypatch.setattr(kubernetes_token, "sessions_root", lambda: root)
    kubernetes_token._verified.clear()
    return root


@pytest.fixture
def tool(tmp_path):
    path = tmp_path / "bin" / "kubectl"
    path.parent.mkdir()
    path.write_bytes(b"#!/bin/sh\n# a stand-in kubectl\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def mint(tmp_path):
    return write_private(tmp_path / "mint.yaml", mint_config())


def token_block(mint: Path, **overrides) -> dict:
    block = {
        "server": SERVER,
        "tls_server_name": "kubernetes.default",
        "ca_pem": CA_PEM,
        "ca_sha256": sha(CA_PEM.encode()),
        "mint_kubeconfig": str(mint),
        "mint_kubeconfig_sha256": sha(mint.read_bytes()),
        "token_minutes": 30,
    }
    block.update(overrides)
    return block


def target_data(mint: Path, tool: Path, **token) -> dict:
    return {
        "provider": "token",
        "context": "cluster",
        "cluster_uid": UID,
        "namespace": "agentbreeder",
        "registry": "registry.example.com/agentbreeder",
        "blocked_cidrs": ["10.0.0.0/8"],
        "token": token_block(mint, **token),
        "tools": {"kubectl": str(tool), "kubectl_sha256": sha(tool.read_bytes())},
    }


def provider_for(mint, tool, run, **token) -> TokenProvider:
    target = KubernetesTarget.model_validate(target_data(mint, tool, **token))
    binding = Binding(TOKEN, UID, "agentbreeder", TokenProvider.document(target))
    return TokenProvider(binding, run=run)


# -- the operator config --------------------------------------------------------------------------


def test_the_token_block_validates(mint, tool):
    target = KubernetesTarget.model_validate(target_data(mint, tool))
    assert target.provider == "token" and target.kubeconfig is None
    assert target.token.service_accounts.deployer == "ab-deployer"
    assert PROVIDERS[TOKEN] is TokenProvider


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda d: d.update(kubeconfig="/k"), "KUBERNETES_PROVIDER_FIELDS"),
        (lambda d: d.pop("token"), "KUBERNETES_PROVIDER_FIELDS"),
        (lambda d: d.pop("tools"), "KUBERNETES_PROVIDER_FIELDS"),
        (lambda d: d.update(context="kind-x"), "KUBERNETES_PROVIDER_FIELDS"),
        (
            lambda d: d["token"].update(server="https://127.0.0.1:6443"),
            "KUBERNETES_SERVER_REFUSED",
        ),
        (lambda d: d["token"].update(ca_sha256="0" * 64), "KUBERNETES_CA_CHANGED"),
        (lambda d: d["token"].update(ca_pem="not a pem"), "KUBERNETES_CA_INVALID"),
        (lambda d: d["token"].update(token_minutes=61), "token_minutes"),
        (lambda d: d["token"].update(token_minutes=9), "token_minutes"),
        (lambda d: d["token"].update(registry_credential="secret://x"), "registry_credential"),
        (
            lambda d: d["token"].update(service_accounts={"deployer": "ab", "installer": "ab"}),
            "KUBERNETES_TOKEN_INVALID",
        ),
        (lambda d: d["token"].update(mint_kubeconfig="relative.yaml"), "mint_kubeconfig"),
    ],
)
def test_the_token_block_is_strict(mint, tool, change, code):
    data = target_data(mint, tool)
    change(data)
    with pytest.raises(ValueError, match=code):
        KubernetesTarget.model_validate(data)


def test_a_kind_target_cannot_carry_token_fields(mint, tool):
    data = target_data(mint, tool) | {"provider": "kind", "kubeconfig": "/k", "context": "kind-x"}
    with pytest.raises(ValueError, match="KUBERNETES_PROVIDER_FIELDS"):
        KubernetesTarget.model_validate(data)
    data.pop("token"), data.pop("tools")
    assert KubernetesTarget.model_validate(data).provider == "kind"
    with pytest.raises(ValueError, match="KUBERNETES_PROVIDER_FIELDS"):
        KubernetesTarget.model_validate({**data, "kubeconfig": None})


@pytest.mark.parametrize(
    "server",
    [
        "https://127.0.0.1:6443",
        "https://localhost:6443",
        "https://api.localhost",
        "https://0.0.0.0:6443",
        "https://169.254.169.254",
        "https://169.254.10.1:6443",
        "https://224.0.0.1",
        "https://[::1]:6443",
        "http://203.0.113.20:6443",
        "https://203.0.113.20:6443/path",
        "https://203.0.113.20:0",
        "https://203.0.113.20:70000",
        "https://-bad-.example.com",
        "https://300.1.1.1",
        "https://user@203.0.113.20",
    ],
)
def test_api_servers_that_are_refused(server):
    assert api_server_refusal(server)


@pytest.mark.parametrize(
    "server", ["https://203.0.113.20:6443", "https://api.cluster.example.com", "https://10.0.0.1"]
)
def test_api_servers_that_are_accepted(server):
    assert api_server_refusal(server) is None


# -- the mint kubeconfig --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "code"),
    [
        (mint_config(user={"tokenFile": "/t"}), "MINT_KUBECONFIG_INVALID"),
        (
            mint_config(user={"client-certificate": "/c", "client-key": "/k"}),
            "MINT_KUBECONFIG_INVALID",
        ),
        (mint_config(user={"auth-provider": {"name": "gcp"}}), "MINT_KUBECONFIG_INVALID"),
        (mint_config(user={"username": "a", "password": "b"}), "MINT_KUBECONFIG_INVALID"),
        (mint_config(cluster_extra={"certificate-authority": "/ca"}), "MINT_KUBECONFIG_INVALID"),
        (mint_config(cluster_extra={"insecure-skip-tls-verify": True}), "MINT_KUBECONFIG_INVALID"),
        (mint_config(user={"exec": {"command": "gcloud"}}), "MINT_EXEC_RELATIVE"),
        (mint_config(user={"exec": {"command": "/x", "shell": "y"}}), "MINT_KUBECONFIG_INVALID"),
        (mint_config() | {"extensions": []}, "MINT_KUBECONFIG_INVALID"),
        (mint_config() | {"preferences": {"colors": True}}, "MINT_KUBECONFIG_INVALID"),
        (
            mint_config() | {"clusters": mint_config()["clusters"] * 2},
            "MINT_KUBECONFIG_INVALID",
        ),
        (
            mint_config()
            | {"contexts": [{"name": "-x", "context": {"cluster": "c", "user": "u"}}]},
            "MINT_KUBECONFIG_INVALID",
        ),
        (
            mint_config()
            | {"contexts": [{"name": "x", "context": {"cluster": "z", "user": "u"}}]},
            "MINT_KUBECONFIG_INVALID",
        ),
    ],
)
def test_the_mint_file_holds_data_fields_only(config, code):
    with pytest.raises(DeploymentError, match=code):
        kubernetes_token.parse_mint(yaml.safe_dump(config).encode())


def test_the_mint_file_must_reach_the_bindings_server_and_ca(mint, tool):
    token = token_block(mint)
    parsed = kubernetes_token.parse_mint(yaml.safe_dump(mint_config()).encode())
    kubernetes_token.check_mint(parsed, token)
    for config in (mint_config(server="https://198.51.100.1:6443"), mint_config(ca=OTHER_PEM)):
        other = kubernetes_token.parse_mint(yaml.safe_dump(config).encode())
        with pytest.raises(DeploymentError, match="KUBERNETES_MINT_MISMATCH"):
            kubernetes_token.check_mint(other, token)


def test_an_exec_plugin_must_be_unwritable_and_pinned(tmp_path, mint, monkeypatch):
    plugin = tmp_path / "plugin"
    plugin.write_bytes(b"#!/bin/sh\n")
    parsed = kubernetes_token.parse_mint(
        yaml.safe_dump(mint_config(user={"exec": {"command": str(plugin)}})).encode()
    )
    token = token_block(mint)
    with pytest.raises(DeploymentError, match="MINT_EXEC_WRITABLE"):  # tmp_path is ours
        kubernetes_token.check_mint(parsed, token)
    monkeypatch.setattr(kubernetes_token, "writable_by_user", lambda path: False)
    with pytest.raises(DeploymentError, match="MINT_EXEC_UNPINNED"):
        kubernetes_token.check_mint(parsed, token)
    kubernetes_token.check_mint(parsed, token | {"mint_exec_sha256": sha(plugin.read_bytes())})
    with pytest.raises(DeploymentError, match="KUBERNETES_TOOL_CHANGED"):
        kubernetes_token.check_mint(parsed, token | {"mint_exec_sha256": "0" * 64})
    plain = kubernetes_token.parse_mint(yaml.safe_dump(mint_config()).encode())
    with pytest.raises(DeploymentError, match="KUBERNETES_MINT_MISMATCH"):
        kubernetes_token.check_mint(plain, token | {"mint_exec_sha256": "0" * 64})


def test_writable_by_user_sees_parents_and_symlink_targets(tmp_path):
    assert kubernetes_token.writable_by_user(str(tmp_path / "plugin"))
    assert not kubernetes_token.writable_by_user("/bin/sh") or os.getuid() == 0


def test_environments_are_allowlists():
    hostile = HOSTILE | {"HOME": "/home/op", "LANG": "C.UTF-8", "TMPDIR": "/tmp/x"}
    minted = kubernetes_token.mint_environment(["/opt/kube", "/usr/local/bin"], hostile)
    no_kuberc = {"KUBECTL_KUBERC": "false", "KUBERC": "off"}
    assert (
        minted
        == {
            "PATH": "/opt/kube:/usr/local/bin",
            "HOME": "/home/op",
            "LANG": "C.UTF-8",
            "TMPDIR": "/tmp/x",
        }
        | no_kuberc
    )
    used = kubernetes_token.use_environment("/opt/kube", "/s/home", hostile | {"KUBERC": "/x"})
    assert used == {
        "PATH": "/opt/kube", "HOME": "/s/home", "LANG": "C.UTF-8", "TMPDIR": "/tmp/x",
    } | no_kuberc  # fmt: skip


# -- sessions -------------------------------------------------------------------------------------


def test_a_session_mints_on_a_copy_and_keeps_the_token_off_argv(mint, tool, sessions, monkeypatch):
    for key, value in HOSTILE.items():
        monkeypatch.setenv(key, value)
    run = FakeKubectl()
    provider = provider_for(mint, tool, run)
    with provider.session() as session:
        directory = Path(session.env["HOME"]).parent
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        config = directory / "config"
        assert stat.S_IMODE(config.stat().st_mode) == 0o600
        assert json.loads(config.read_text())["users"][0]["user"]["token"] == MINTED
        assert not (directory / "mint").exists()  # the copy is gone once the token is read
        assert session.binary == str(tool)
        assert session.argv == (
            "--kubeconfig", str(config), "--context", "cluster", "--server", SERVER,
            "--certificate-authority", str(directory / "ca.crt"),
            "--cache-dir", str(directory / "cache"), "--tls-server-name", "kubernetes.default",
        )  # fmt: skip
        assert (directory / "ca.crt").read_text() == CA_PEM
        assert MINTED not in " ".join(session.argv)
        assert set(session.env) <= {"PATH", "HOME", "LANG", "TMPDIR", "KUBECTL_KUBERC", "KUBERC"}
        assert session.env["PATH"] == str(tool.parent)
        assert session.expires == pytest.approx(time.monotonic() + 25 * 60, abs=5)
        session.verify()
    assert not directory.exists()
    mint_argv = run.calls[0]
    assert mint_argv[0] == str(tool)
    assert _skip_flags(mint_argv) == ["create", "token", "--duration", "30m", "--", "ab-deployer"]
    copy = mint_argv[mint_argv.index("--kubeconfig") + 1]
    assert copy == str(directory / "mint") and copy != str(mint)
    assert mint_argv[mint_argv.index("--context") + 1] == "operator@c"
    assert mint_argv[mint_argv.index("--namespace") + 1] == "agentbreeder"
    assert yaml.safe_load(run.configs[0]) == mint_config()  # the pinned file's exact bytes
    assert run.envs[0] == {"PATH": str(tool.parent), "HOME": os.environ["HOME"]} | {
        key: os.environ[key] for key in ("LANG", "TMPDIR") if key in os.environ
    } | {"KUBECTL_KUBERC": "false", "KUBERC": "off"}
    assert run.kwargs[0]["stdin"] is not None and run.kwargs[0]["capture_output"]
    assert provider.observed_minutes == 30


def test_each_identity_mints_its_own_account(mint, tool, sessions):
    run = FakeKubectl()
    provider = provider_for(mint, tool, run)
    for identity in kubernetes_token.IDENTITIES:
        with provider.session_as(identity):
            pass
    assert run.minted == ["ab-deployer", "ab-installer", "ab-operator", "ab-canary"]
    with pytest.raises(DeploymentError, match="KUBERNETES_UNCONFIGURED"):
        with provider.session_as("admin"):
            pass


@pytest.mark.parametrize(
    ("break_it", "code"),
    [
        (
            lambda m, t, r: m.write_text(yaml.safe_dump(mint_config()) + "\n"),
            "MINT_KUBECONFIG_CHANGED",
        ),
        (lambda m, t, r: t.write_bytes(b"#!/bin/sh\n# swapped\n"), "KUBERNETES_TOOL_CHANGED"),
        (lambda m, t, r: setattr(r, "mint_fail", True), "KUBERNETES_MINT_FAILED"),
        (lambda m, t, r: setattr(r, "mint_output", "not a token"), "KUBERNETES_MINT_FAILED"),
        (lambda m, t, r: m.chmod(0o644), "MINT_KUBECONFIG_INVALID"),
    ],
)
def test_a_failed_mint_leaves_nothing(mint, tool, sessions, break_it, code):
    run = FakeKubectl()
    provider = provider_for(mint, tool, run)
    break_it(mint, tool, run)
    with pytest.raises(DeploymentError, match=code) as raised:
        with provider.session():
            pass
    assert "secret stderr" not in str(raised.value) and OPERATOR_SECRET not in str(raised.value)
    assert list(sessions.iterdir()) == []
    assert run.minted == ([] if code != "KUBERNETES_MINT_FAILED" else ["ab-deployer"])


def test_the_session_is_removed_when_the_operation_fails(mint, tool, sessions):
    provider = provider_for(mint, tool, FakeKubectl())
    with pytest.raises(RuntimeError):
        with provider.session():
            raise RuntimeError("the operation failed")
    assert list(sessions.iterdir()) == []


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGHUP])
def test_sigterm_and_sighup_remove_the_session(mint, tool, sessions, number):
    provider = provider_for(mint, tool, FakeKubectl())
    before = signal.getsignal(number)
    with pytest.raises(SystemExit) as raised:
        with provider.session():
            os.kill(os.getpid(), number)
            time.sleep(1)
    assert raised.value.code == 128 + number
    assert list(sessions.iterdir()) == []
    assert signal.getsignal(number) is before


def test_sigint_removes_the_session(mint, tool, sessions):
    provider = provider_for(mint, tool, FakeKubectl())
    with pytest.raises(KeyboardInterrupt):
        with provider.session():
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(1)
    assert list(sessions.iterdir()) == []


def test_a_shorter_observed_lifetime_sets_the_renewal(mint, tool, sessions):
    run = FakeKubectl()
    run.lifetime = 15  # the API server capped the 30 requested minutes
    with provider_for(mint, tool, run).session() as session:
        assert session.expires == pytest.approx(time.monotonic() + 10 * 60, abs=5)
    run.lifetime = 6
    with pytest.raises(DeploymentError, match="KUBERNETES_TOKEN_TOO_SHORT"):
        with provider_for(mint, tool, run).session():
            pass
    assert list(sessions.iterdir()) == []


def test_an_undeletable_session_never_masks_the_error(mint, tool, sessions):
    provider = provider_for(mint, tool, FakeKubectl())
    with pytest.raises(RuntimeError, match="the operation"):
        with provider.session() as session:
            cache = Path(session.env["HOME"]).parent / "cache"
            (cache / "entry").mkdir()
            cache.chmod(0o500)
            raise RuntimeError("the operation failed")
    assert list(sessions.iterdir()) == []


@pytest.mark.parametrize(
    "tamper",
    [
        lambda d: d["token"]["service_accounts"].update(deployer="--as=system:admin"),
        lambda d: d["token"].update(token_minutes="30m --as=x"),
        lambda d: d["token"].update(mint_kubeconfig="--kubeconfig=/evil"),
        lambda d: d["token"].update(server="https://127.0.0.1:6443"),
        lambda d: d["tools"].update(kubectl="kubectl"),
        lambda d: d.update(context="--context=evil"),
        lambda d: d.update(extra=1),
    ],
)
def test_a_tampered_binding_row_is_revalidated(mint, tool, tamper):
    """A row edited along with its digest and audit (a consistency check, not tamper-evidence)
    still cannot put a flag on kubectl's argv."""
    import copy

    target = KubernetesTarget.model_validate(target_data(mint, tool))
    document = copy.deepcopy(TokenProvider.document(target))
    tamper(document)
    with pytest.raises(DeploymentError, match="cluster binding"):
        TokenProvider(Binding(TOKEN, UID, "agentbreeder", document))


def test_a_symlinked_session_root_is_refused(mint, tool, sessions, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    sessions.parent.mkdir(parents=True)
    sessions.symlink_to(elsewhere)
    with pytest.raises(DeploymentError, match="STATE_UNSAFE"):
        with provider_for(mint, tool, FakeKubectl()).session():
            pass
    assert kubernetes_token.leftovers() == (0, 0)


def test_the_sweep_removes_only_old_session_directories(sessions, tmp_path):
    sessions.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    old, fresh, other = sessions / "s-old", sessions / "s-fresh", sessions / "notes"
    for directory in (old, fresh, other):
        directory.mkdir()
    (old / "cache").mkdir()
    (old / "config").write_text("token")
    (old / "link").symlink_to(outside)
    (old / "dirlink").symlink_to(tmp_path)
    ancient = time.time() - kubernetes_token.SESSION_MAX_AGE - 60
    os.utime(old, (ancient, ancient))
    os.utime(other, (ancient, ancient))
    assert kubernetes_token.leftovers() == (2, 1)
    assert kubernetes_token.sweep() == 1
    assert not old.exists() and fresh.exists() and other.exists()
    assert outside.read_text() == "keep" and tmp_path.exists()
    assert kubernetes_token.leftovers() == (1, 0)


def test_token_lifetime_reads_the_claims_without_trusting_them():
    assert kubernetes_token.token_lifetime(MINTED) == 30
    assert kubernetes_token.token_lifetime("a.b.c") is None


# -- the runtime ----------------------------------------------------------------------------------


@pytest.fixture
def token_env(identity_env, mint, tool):
    identity_env.config["environments"]["dev"] = {
        "targets": ["local", "kubernetes"],
        "kubernetes": target_data(mint, tool),
    }
    identity_env.config["environments"]["prod"] = {
        "targets": ["local", "kubernetes"],
        "kubernetes": target_data(mint, tool) | {"namespace": "agentbreeder-prod"},
    }
    return identity_env


@pytest.fixture
def fake(monkeypatch):
    from agentbreeder import kubernetes as kubernetes_module

    run = FakeKubectl()
    monkeypatch.setattr(kubernetes_module.subprocess, "run", run)
    monkeypatch.setattr(kubernetes_token.subprocess, "run", run)
    return run


def test_the_runtime_runs_the_pinned_kubectl_in_a_session(token_env, tmp_path, sessions, fake):
    store = Store(tmp_path / "state")
    kube = Kubernetes(token_env.load(), bindings=store)
    kube.select("dev")
    assert kube.context.startswith("token|")
    assert TokenProvider.inline is False
    kube.authorize()
    calls = [call for call in fake.calls if "create" not in call]
    assert {call[0] for call in calls} == {str(Path(kube.provider.tools["kubectl"]))}
    assert all("--server" in call and "--certificate-authority" in call for call in calls)
    assert fake.minted == ["ab-deployer"]  # one session for the whole operation
    kube.close()
    assert list(sessions.iterdir()) == []
    store.close()


def test_an_expiring_session_is_reopened(token_env, tmp_path, sessions, fake, monkeypatch):
    from agentbreeder import kubernetes as kubernetes_module

    store = Store(tmp_path / "state")
    kube = Kubernetes(token_env.load(), bindings=store)
    kube.select("dev")
    kube.authorize()
    now = time.monotonic()
    monkeypatch.setattr(kubernetes_module.time, "monotonic", lambda: now + 26 * 60)
    kube.authorize()
    assert fake.minted == ["ab-deployer", "ab-deployer"]
    assert len(list(sessions.iterdir())) == 1
    kube.close()
    store.close()


def test_a_cluster_uid_mismatch_is_refused_after_the_first_call(
    token_env, tmp_path, sessions, fake
):
    fake.uid = "99999999-2222-3333-4444-555555555555"
    store = Store(tmp_path / "state")
    kube = Kubernetes(token_env.load(), bindings=store)
    kube.select("dev")
    with pytest.raises(DeploymentError, match="KUBERNETES_CLUSTER_MISMATCH"):
        kube.authorize()
    kube.close()
    store.close()


def test_a_changed_kubectl_is_refused_before_any_call(token_env, tmp_path, sessions, fake, tool):
    store = Store(tmp_path / "state")
    kube = Kubernetes(token_env.load(), bindings=store)
    kube.select("dev")
    kube.authorize()
    calls = len(fake.calls)
    tool.write_bytes(b"#!/bin/sh\n# swapped after connect\n")
    with pytest.raises(DeploymentError, match="KUBERNETES_TOOL_CHANGED"):
        kube.command("get", "namespace", "kube-system")
    assert len(fake.calls) == calls
    kube.close()
    store.close()


def test_a_binding_row_from_another_environment_is_refused(token_env, tmp_path, sessions, fake):
    """K0's K1 blocker: a record swapped to another environment's binding ID is refused."""
    store = Store(tmp_path / "state")
    config = token_env.load()
    dev, prod = Kubernetes(config, bindings=store), Kubernetes(config, bindings=store)
    dev.select("dev")
    prod.select("prod")
    assert dev.context != prod.context
    fresh = Kubernetes(config, bindings=_Bindings(store, dev, prod))
    fresh.authorize(dev.context, "dev")
    assert fake.minted == ["ab-deployer"]
    with pytest.raises(DeploymentError, match="KUBERNETES_BINDING_CHANGED"):
        Kubernetes(config, bindings=_Bindings(store, dev, prod)).authorize(prod.context, "dev")
    with pytest.raises(DeploymentError, match="KUBERNETES_BINDING_CHANGED"):
        Kubernetes(config, bindings=_Bindings(store, dev, prod)).authorize(prod.context, None)
    fresh.close()
    store.close()


class _Bindings:
    """The store's binding rows, with the audit digests each deployment_created event holds."""

    def __init__(self, store, *runtimes):
        self.store = store
        self.digests = {kube.binding.id: [kube.binding.digest] for kube in runtimes}

    def cluster_binding(self, identifier):
        return self.store.cluster_binding(identifier)

    def cluster_binding_audit(self, identifier):
        return self.digests.get(identifier, [])


def test_deploys_to_a_token_cluster_are_refused_until_the_policy_check(token_env, tmp_path):
    from unittest.mock import Mock

    store = Store(tmp_path / "state")
    service = DeploymentService(store, Mock(), token_env.load())
    manifest = Manifest.model_validate(
        v2_manifest_data(target="kubernetes", image="registry.example.com/x@sha256:" + "a" * 64)
    )
    assert service._kubernetes_requirements(manifest, "dev") == (
        "KUBERNETES_POLICY_UNCHECKED",
        "Deploys to a token cluster need the admission policy check, which is not wired in "
        "yet (#770; K2 built the policies, not this check)",
    )
    store.close()


# -- connect --------------------------------------------------------------------------------------


def connect(mint, tool, run, **overrides):
    options = {
        "server": SERVER,
        "mint_kubeconfig": mint,
        "registry": "registry.example.com/agentbreeder",
        "pod_cidrs": ["10.244.0.0/16"],
        "service_cidr": "10.96.0.0/12",
        "tls_server_name": "kubernetes.default",
        "kubectl_path": str(tool),
        "run": run,
        "tls": lambda server, ca, name: None,
        "resolve": lambda host: ["203.0.113.20"],
        "policy": lambda session, namespace, run: "sha256:" + "b" * 64,
    }
    options.update(overrides)
    return cluster_token.connect(**options)


def test_connect_records_a_checked_block_and_a_review(mint, tool, sessions):
    run = FakeKubectl()
    checked = []
    result = connect(mint, tool, run, tls=lambda *args: checked.append(args))
    block = result["kubernetes"]
    target = KubernetesTarget.model_validate(block)
    assert target.provider == "token" and target.cluster_uid == UID
    assert target.token.ca_pem == CA_PEM and target.token.ca_sha256 == sha(CA_PEM.encode())
    assert target.token.mint_kubeconfig_sha256 == sha(mint.read_bytes())
    assert target.tools.kubectl == str(tool) and target.tools.kubectl_sha256 == sha(
        tool.read_bytes()
    )
    assert block["blocked_cidrs"] == [
        "10.244.0.0/16", "10.96.0.0/12", "203.0.113.20/32", "10.0.0.5/32", "198.51.100.7/32",
        "169.254.169.254/32", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "100.64.0.0/10", "169.254.0.0/16",
    ]  # fmt: skip
    assert checked == [(SERVER, CA_PEM, "kubernetes.default")]
    assert run.minted == ["ab-deployer", "ab-installer"]
    review = result["review"]
    assert review["policy_digest"] == "sha256:" + "b" * 64
    assert review["kubernetes_version"] == "1.31" and review["token_observed_minutes"] == 30
    assert review["ca_fingerprints_sha256"] == cluster_token.ca_fingerprints(CA_PEM)
    assert OPERATOR_SECRET not in json.dumps(result) and MINTED not in json.dumps(result)
    assert list(sessions.iterdir()) == []


def test_connect_reads_nodes_pods_and_services_as_the_installer(mint, tool, sessions):
    run = FakeKubectl()
    connect(mint, tool, run)
    by_token = {}
    for call, config in zip(run.calls, run.configs, strict=True):
        if "create" in call:
            continue
        user = json.loads(config)["users"][0]["user"]["token"]
        by_token.setdefault(user, []).append(_skip_flags(call)[:2])
    assert by_token == {
        bound_token("ab-deployer"): [
            ["get", "namespace"], ["version", "-o"], ["get", "namespace"],
            ["get", "resourcequotas"],
        ],
        bound_token("ab-installer"): [["get", "nodes"], ["get", "pods"], ["get", "services"]],
    }  # fmt: skip


def test_connect_with_the_shipped_policy_check_records_nothing(mint, tool, sessions):
    with pytest.raises(DeploymentError, match="KUBERNETES_POLICY_UNCHECKED"):
        connect(mint, tool, FakeKubectl(), policy=None)
    assert list(sessions.iterdir()) == []


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda r: setattr(r, "version", {"major": "1", "minor": "29"}), "KUBERNETES_VERSION"),
        (lambda r: r.labels.pop("app.kubernetes.io/managed-by"), "NAMESPACE_UNMANAGED"),
        (
            lambda r: r.labels.update({"pod-security.kubernetes.io/enforce": "baseline"}),
            "UNRESTRICTED",
        ),
        (lambda r: r.quota.pop("count/secrets"), "KUBERNETES_QUOTA_MISSING"),
        (lambda r: r.nodes[0]["spec"].update(podCIDRs=["10.245.0.0/24"]), "RANGES_INCOMPLETE"),
        (lambda r: r.pods[0]["status"].update(podIP="10.0.0.5", podIPs=[]), "RANGES_INCOMPLETE"),
        (lambda r: r.services[0]["spec"].update(clusterIPs=["10.200.0.1"]), "RANGES_INCOMPLETE"),
        (lambda r: r.nodes[0]["spec"].update(podCIDRs=["fd00::/64"]), "DUAL_STACK"),
        (
            lambda r: r.nodes[0]["status"]["addresses"].append(
                {"type": "InternalIP", "address": "fd00::5"}
            ),
            "DUAL_STACK",
        ),
        (
            lambda r: r.nodes.extend(
                {
                    "spec": {},
                    "status": {
                        "addresses": [{"type": "ExternalIP", "address": f"198.51.100.{n}"}]
                    },
                }
                for n in range(30)
            ),
            "KUBERNETES_RANGES_TOO_MANY",
        ),
        (lambda r: setattr(r, "uid", "not json at all {"), "KUBERNETES_TOKEN_INVALID"),
    ],
)
def test_connect_refuses(mint, tool, sessions, change, code):
    run = FakeKubectl()
    change(run)
    with pytest.raises(DeploymentError, match=code):
        connect(mint, tool, run)
    assert list(sessions.iterdir()) == []


def test_node_ranges_replace_node_addresses(mint, tool, sessions):
    run = FakeKubectl()
    run.nodes.extend(
        {
            "spec": {},
            "status": {"addresses": [{"type": "ExternalIP", "address": f"198.51.100.{n}"}]},
        }
        for n in range(30)
    )
    block = connect(mint, tool, run, node_cidrs=["198.51.100.0/24", "10.0.0.0/24"])["kubernetes"]
    assert "198.51.100.0/24" in block["blocked_cidrs"] and len(block["blocked_cidrs"]) == 11
    assert "198.51.100.7/32" not in block["blocked_cidrs"]
    with pytest.raises(DeploymentError, match="KUBERNETES_RANGES_INCOMPLETE"):
        connect(mint, tool, FakeKubectl(), node_cidrs=["198.51.100.0/24"])  # 10.0.0.5 outside


@pytest.mark.parametrize(
    ("options", "code"),
    [
        ({"server": "https://127.0.0.1:6443"}, "KUBERNETES_SERVER_REFUSED"),
        ({"server": "https://198.51.100.1:6443"}, "KUBERNETES_MINT_MISMATCH"),
        ({"pod_cidrs": []}, "KUBERNETES_RANGES_INVALID"),
        ({"pod_cidrs": ["10.244.0.1/16"]}, "KUBERNETES_RANGES_INVALID"),
        ({"service_cidr": "fd00::/108"}, "KUBERNETES_DUAL_STACK_UNSUPPORTED"),
        ({"registry": "Registry/With/Caps"}, "KUBERNETES_TOKEN_INVALID"),
        ({"token_minutes": 5}, "KUBERNETES_TOKEN_INVALID"),
        ({"kubectl_path": "/nonexistent/kubectl"}, "CLUSTER_TOOL_MISSING"),
        ({"resolve": lambda host: []}, "KUBERNETES_UNREACHABLE"),
    ],
)
def test_connect_refuses_its_inputs(mint, tool, sessions, options, code):
    with pytest.raises(DeploymentError, match=code):
        connect(mint, tool, FakeKubectl(), **options)


def test_connect_refuses_a_writable_exec_plugin(tmp_path, tool, sessions):
    plugin = tmp_path / "plugin"
    plugin.write_bytes(b"#!/bin/sh\n")
    mint = write_private(
        tmp_path / "exec-mint.yaml", mint_config(user={"exec": {"command": str(plugin)}})
    )
    with pytest.raises(DeploymentError, match="MINT_EXEC_WRITABLE"):
        connect(mint, tool, FakeKubectl())


def test_connect_pins_an_exec_plugin(tmp_path, tool, sessions, monkeypatch):
    plugin = tmp_path / "plugin"
    plugin.write_bytes(b"#!/bin/sh\n")
    mint = write_private(
        tmp_path / "exec-mint.yaml", mint_config(user={"exec": {"command": str(plugin)}})
    )
    monkeypatch.setattr(kubernetes_token, "writable_by_user", lambda path: False)
    run = FakeKubectl()
    block = connect(mint, tool, run)["kubernetes"]
    assert block["token"]["mint_exec_sha256"] == sha(plugin.read_bytes())
    mint_env = run.envs[0]
    assert mint_env["PATH"] == f"{tool.parent}{os.pathsep}{tmp_path}"


# -- the TLS cross-check, against a real local TLS server -----------------------------------------


@pytest.fixture
def tls_server(tmp_path):
    import datetime
    import socket
    import threading

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.UTC)

    def certificate(subject, issuer, key, signer, *, ca, san=None):
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        )
        if san:
            builder = builder.add_extension(x509.SubjectAlternativeName(san), critical=False)
        return builder.sign(signer, hashes.SHA256())

    import ipaddress

    ca_key, other_key, leaf_key = (ec.generate_private_key(ec.SECP256R1()) for _ in range(3))
    ca = certificate("test-ca", "test-ca", ca_key, ca_key, ca=True)
    other = certificate("other-ca", "other-ca", other_key, other_key, ca=True)
    leaf = certificate(
        "kube-apiserver", "test-ca", leaf_key, ca_key, ca=False,
        san=[
            x509.DNSName("kubernetes.default"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ],
    )  # fmt: skip
    pem = serialization.Encoding.PEM
    (tmp_path / "leaf.pem").write_bytes(leaf.public_bytes(pem))
    (tmp_path / "leaf.key").write_bytes(
        leaf_key.private_bytes(
            pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "leaf.pem", tmp_path / "leaf.key")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    stop = threading.Event()

    def serve():
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            try:
                with context.wrap_socket(connection, server_side=True):
                    pass
            except (ssl.SSLError, OSError):
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield SimpleNamespace(
        url=f"https://127.0.0.1:{listener.getsockname()[1]}",
        ca=ca.public_bytes(pem).decode(),
        other=other.public_bytes(pem).decode(),
    )
    stop.set()
    thread.join(timeout=2)
    listener.close()


def test_tls_check_against_a_real_server(tls_server, monkeypatch):
    monkeypatch.setattr(cluster_token, "_has_trust_store", lambda: True)  # tested below
    cluster_token.tls_check(tls_server.url, tls_server.ca, None)  # IP SAN
    cluster_token.tls_check(tls_server.url, tls_server.ca, "kubernetes.default")
    with pytest.raises(DeploymentError, match="KUBERNETES_CA_MISMATCH"):
        cluster_token.tls_check(tls_server.url, tls_server.other, None)
    with pytest.raises(DeploymentError, match="KUBERNETES_CA_MISMATCH"):
        cluster_token.tls_check(tls_server.url, tls_server.ca, "wrong.example.com")
    real = ssl.create_default_context

    def trusting():  # as if the cluster's CA were a public root in the system store
        context = real()
        # 3.13 adds VERIFY_X509_STRICT, which this minimal test CA (no key usage) fails
        context.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
        context.load_verify_locations(cadata=tls_server.ca)
        return context

    monkeypatch.setattr(cluster_token.ssl, "create_default_context", trusting)
    with pytest.raises(DeploymentError, match="KUBERNETES_CA_PUBLIC"):
        cluster_token.tls_check(tls_server.url, tls_server.ca, None)


def test_tls_check_reports_an_unreachable_server_and_a_bad_ca(tls_server):
    with pytest.raises(DeploymentError, match="KUBERNETES_UNREACHABLE"):
        cluster_token.tls_check("https://127.0.0.1:9", tls_server.ca, None)
    with pytest.raises(DeploymentError, match="KUBERNETES_CA_INVALID"):
        cluster_token.tls_check(tls_server.url, CA_PEM, None)


def test_ca_fingerprints_match_openssls_format(tls_server):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    expected = x509.load_pem_x509_certificate(tls_server.ca.encode()).fingerprint(hashes.SHA256())
    assert cluster_token.ca_fingerprints(tls_server.ca) == [
        ":".join(f"{byte:02X}" for byte in expected)
    ]


# -- the command and doctor -----------------------------------------------------------------------


def test_cluster_connect_token_command(monkeypatch, mint, capsys):
    seen = {}

    def fake_connect(**options):
        seen.update(options)
        return {"kubernetes": {"provider": "token"}, "review": {}}

    monkeypatch.setattr(cluster_token, "connect", fake_connect)
    code = cli.main(
        [
            "cluster", "connect", "token", "--server", SERVER, "--mint-kubeconfig", str(mint),
            "--registry", "registry.example.com/agentbreeder", "--pod-cidr", "10.244.0.0/16",
            "--pod-cidr", "10.245.0.0/16", "--service-cidr", "10.96.0.0/12",
            "--node-cidr", "198.51.100.0/24", "--environment", "prod", "--json",
        ]
    )  # fmt: skip
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"environment": "prod", "kubernetes": {"provider": "token"}, "review": {}}
    assert seen["pod_cidrs"] == ["10.244.0.0/16", "10.245.0.0/16"]
    assert seen["node_cidrs"] == ["198.51.100.0/24"] and seen["namespace"] == "agentbreeder"
    assert seen["token_minutes"] == 30 and seen["kubectl_path"] is None


def test_cluster_connect_token_reports_a_refusal(monkeypatch, mint, capsys):
    def refuse(**options):
        raise DeploymentError("[KUBERNETES_POLICY_UNCHECKED] not built yet; nothing was recorded")

    monkeypatch.setattr(cluster_token, "connect", refuse)
    code = cli.main(
        [
            "cluster", "connect", "token", "--server", SERVER, "--mint-kubeconfig", str(mint),
            "--registry", "registry.example.com/agentbreeder", "--pod-cidr", "10.244.0.0/16",
            "--service-cidr", "10.96.0.0/12",
        ]
    )  # fmt: skip
    assert code == 2
    assert "KUBERNETES_POLICY_UNCHECKED" in capsys.readouterr().err


def test_doctor_reports_leftover_sessions(sessions):
    from agentbreeder import doctor

    assert doctor.check_kube_sessions() == []
    sessions.mkdir(parents=True)
    (sessions / "s-left").mkdir()
    old = sessions / "s-old"
    old.mkdir()
    ancient = time.time() - kubernetes_token.SESSION_MAX_AGE - 60
    os.utime(old, (ancient, ancient))
    (check,) = doctor.check_kube_sessions()
    assert check.required is False and check.passed is False
    assert "2 cluster session directories" in check.message and "1 older" in check.message
    assert kubernetes_providers.PROVIDERS[TOKEN] is TokenProvider


def test_the_public_check_sends_the_server_name(tls_server, monkeypatch):
    monkeypatch.setattr(cluster_token, "_has_trust_store", lambda: True)
    names = []
    real = ssl.SSLContext.wrap_socket

    def spy(self, sock, *args, server_hostname=None, **kwargs):
        if not kwargs.get("server_side"):  # the test server's own handshakes run here too
            names.append(server_hostname)
        return real(self, sock, *args, server_hostname=server_hostname, **kwargs)

    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", spy)
    cluster_token.tls_check(tls_server.url, tls_server.ca, "kubernetes.default")
    assert names == ["kubernetes.default", "kubernetes.default"]


def test_an_empty_trust_store_is_refused_not_read_as_private(tls_server, monkeypatch):
    monkeypatch.setattr(cluster_token, "_has_trust_store", lambda: False)
    with pytest.raises(DeploymentError, match="KUBERNETES_TRUST_STORE_EMPTY"):
        cluster_token.tls_check(tls_server.url, tls_server.ca, None)


def test_has_trust_store_reads_the_default_paths(tmp_path, monkeypatch):
    def paths(cafile=None, capath=None):
        return SimpleNamespace(cafile=cafile, capath=capath)

    bundle = tmp_path / "cert.pem"
    bundle.write_text("x")
    folder = tmp_path / "certs"
    folder.mkdir()
    cases = [(paths(), False), (paths(str(bundle)), True), (paths(None, str(folder)), False)]
    for found, expected in cases:
        monkeypatch.setattr(cluster_token.ssl, "get_default_verify_paths", lambda f=found: f)
        assert cluster_token._has_trust_store() is expected
    (folder / "abcd.0").write_text("x")
    assert cluster_token._has_trust_store() is True
