"""Opt-in real Docker checks for #1025 V1 (Vertex AI through `service/litellm@1.4.0`), on the
pinned LiteLLM image. NOT RUN when this slice was written: no Docker slot was available, so
every expectation below comes from reading LiteLLM 1.106.2, not from a run. They are the spec's
ship gates for Vertex (D14, D15, §12.5); run them before the slice merges:

    AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_vertex_gateway_docker.py --no-cov -s

None of them needs a Google account.

1. Redirects (D15, §4.1) and what LiteLLM prints (part of §12.6). In the pinned image, on an
   internal network with no route out, LiteLLM calls Vertex with a throwaway service-account
   key whose token endpoint and API base are a scripted server. The server answers the model
   call with 301, 302, 303, 307 and 308 to a second host. Pass: the second host never receives
   the access token (in a header, the path or the body), for the sync and the async chat
   client, each of which must have been served every redirect code with the token, and one
   plain call each must succeed, or the harness proves nothing. The embedding client is
   reported, not asserted: whether it honours `api_base` is not known. A failed token exchange
   is then provoked, and nothing the process prints may hold the private key or the token.
   This test passes its own `api_base` and `token_uri`, which the gateway's config review and
   launcher refuse: it measures the HTTP client, not the review.
2. A running gateway (D14 §4.2): `services up` on the real 1.4.0 with a reviewed Vertex config
   and a throwaway key. The gateway's process holds the canonical key, not the file's extra
   fields; a key reaches its aliases (a control); every client-side parameter of §4.2 sent for
   a Vertex alias is refused by the request filter or LiteLLM's own body check before any
   provider call; no model-info or config endpoint returns the key to a virtual key, with the
   endpoint shown live by the master key; the gateway's logs hold no key material.
   Limits, stated plainly: this gateway has ordinary network egress, so if a request were NOT
   refused, LiteLLM would try a token exchange at the real oauth2.googleapis.com with the
   throwaway key's signed assertion (and the test fails). And because every request here is
   refused before a provider call, the log check does not cover what the proxy logs when a
   Vertex call fails: that needs the real-credential run (§12.7).
3. Metadata services (§12.5): the O1 check, on the 1.4.0 launcher.

The request filter's own harness also runs on 1.4.0 (tests/test_gateway_request_filter_docker.py
takes it as a third version).
"""

from __future__ import annotations

import json
import os
import uuid

import pytest
import yaml
from test_gateway_request_filter_docker import FIELDS, PROVIDER, PROVIDERS, _provider_service
from test_guardrails_docker import _post, docker

from agentbreeder import gateway_keys, gateway_providers, registry
from agentbreeder.services.engine import Engine
from agentbreeder.services.state import ROOT_ENV

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)

V14 = registry.BUILTIN_ROOT / "service/litellm/1.4.0"
IMAGE = yaml.safe_load((V14 / "service.yaml").read_text())["image"]
KEY_NAME = gateway_providers.VERTEX_KEY_NAME
# A name no real project should have; nothing here is meant to reach Google.
PROJECT = "ab-test-not-a-project"
KEY_ID = "markerkeyid0123456789abcdef"

# --- 1. redirects ------------------------------------------------------------------------------

CODES = (301, 302, 303, 307, 308)
TOKEN = "ya29.marker-access-token-" + "0123456789abcdef" * 2

SERVER = r"""
import json, os, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROLE = os.environ["ROLE"]
TOKEN = os.environ["FAKE_TOKEN"]
OTHER = os.environ.get("OTHER_URL", "")
REPLY = {"candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]},
                         "finishReason": "STOP", "index": 0}],
         "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1,
                           "totalTokenCount": 2},
         "predictions": [{"embeddings": {"values": [0.1, 0.2, 0.3],
                                         "statistics": {"token_count": 1}}}]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _any(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        seen = " ".join(f"{k}: {v}" for k, v in self.headers.items())
        seen += " " + self.path + " " + body.decode("utf-8", "replace")
        token = "yes" if TOKEN in seen else "no"
        auth = "yes" if self.headers.get("Authorization") else "no"
        if ROLE == "other":
            print(f"OTHER {self.command} token={token} auth={auth} body={len(body)}", flush=True)
            return self._reply(REPLY)
        if self.path.startswith("/badtoken"):
            print("GOOGLE bad-token-request", flush=True)
            data = json.dumps({"error": "invalid_grant", "error_description": "bad"}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.startswith("/token"):
            print("GOOGLE token-request", flush=True)
            return self._reply({"access_token": TOKEN, "expires_in": 3600,
                                "token_type": "Bearer"})
        # /<kind>/r<code>... or /<kind>/ok...: kind is the client that called (sync, async,
        # embed), so each client's redirects are counted on their own.
        match = re.match(r"/([a-z]+)/(?:r(\d{3})|ok)", self.path)
        kind = match.group(1) if match else "unknown"
        if match and match.group(2):
            print(f"GOOGLE redirect {kind} {match.group(2)} token={token}", flush=True)
            self.send_response(int(match.group(2)))
            self.send_header("Location", OTHER + "/landed")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        print(f"GOOGLE served {kind} token={token}", flush=True)
        self._reply(REPLY)

    do_GET = do_POST = do_PUT = _any


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
"""

PROBE = r"""
import asyncio, json, os, sys
import litellm

PROJECT, CODES = sys.argv[1], sys.argv[2].split(",")
PEM = os.environ["PROBE_PEM"]


def key(token_path):
    return json.dumps({
        "type": "service_account", "project_id": PROJECT, "private_key_id": "k1",
        "private_key": PEM, "client_email": f"probe@{PROJECT}.iam.gserviceaccount.com",
        "client_id": "1", "token_uri": "http://fake-google:8080/" + token_path})


common = dict(vertex_credentials=key("token"), vertex_project=PROJECT,
              vertex_location="us-central1", num_retries=0, timeout=20)
say = [{"role": "user", "content": "hello"}]
paths = ["ok"] + ["r" + code for code in CODES]


def base(kind, path):
    return f"http://fake-google:8080/{kind}/{path}"


def report(label, error=None):
    print("PROBE", label, "ok" if error is None else "error " + type(error).__name__,
          flush=True)


for path in paths:
    try:
        litellm.completion(model="vertex_ai/gemini-2.5-flash", messages=say,
                           api_base=base("sync", path), **common)
        report("sync " + path)
    except Exception as error:
        report("sync " + path, error)


async def main():
    for path in paths:
        try:
            await litellm.acompletion(model="vertex_ai/gemini-2.5-flash", messages=say,
                                      api_base=base("async", path), **common)
            report("async " + path)
        except Exception as error:
            report("async " + path, error)
        try:
            await litellm.aembedding(model="vertex_ai/text-embedding-005", input=["x"],
                                     api_base=base("embed", path), **common)
            report("embed " + path)
        except Exception as error:
            report("embed " + path, error)


asyncio.run(main())

# A failed token exchange: whatever LiteLLM and google-auth print or raise goes to the test.
try:
    litellm.completion(model="vertex_ai/gemini-2.5-flash", messages=say,
                       api_base=base("sync", "ok"),
                       **{**common, "vertex_credentials": key("badtoken")})
    report("badtoken")
except Exception as error:
    report("badtoken", error)
    print("PROBE-ERROR-TEXT", repr(error)[:4000], flush=True)
"""


def _lines(name: str, prefix: str) -> list[str]:
    return [line for line in docker("logs", name).stdout.splitlines() if line.startswith(prefix)]


def _pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def test_a_vertex_redirect_never_carries_the_access_token():
    tag = uuid.uuid4().hex[:8]
    network = f"ab-v1-redirect-{tag}"
    google, other = f"ab-v1-google-{tag}", f"ab-v1-other-{tag}"
    python = ["--entrypoint", "/app/.venv/bin/python3", IMAGE]
    pem = _pem()
    docker("pull", "-q", IMAGE)
    docker("network", "create", "--internal", network)
    try:
        for name, alias, role in (
            (google, "fake-google", "google"),
            (other, "fake-other", "other"),
        ):
            docker(
                "run", "-d", "--name", name, "--network", network, "--network-alias", alias,
                "-e", f"ROLE={role}", "-e", f"FAKE_TOKEN={TOKEN}",
                "-e", "OTHER_URL=http://fake-other:8080",
                *python, "-c", SERVER,
            )  # fmt: skip
        probe = docker(
            "run", "--rm", "--network", network, "--read-only", "--tmpfs", "/tmp",
            "--user", "65532:65532", "-e", "HOME=/tmp",
            "-e", "LITELLM_LOCAL_MODEL_COST_MAP=True", "-e", "LITELLM_LOG=WARNING",
            "-e", f"PROBE_PEM={pem}",
            *python, "-c", PROBE, PROJECT, ",".join(str(code) for code in CODES),
            check=False,
        )  # fmt: skip
        results = [line for line in probe.stdout.splitlines() if line.startswith("PROBE ")]
        at_google, at_other = _lines(google, "GOOGLE"), _lines(other, "OTHER")
        print("\nprobe:", json.dumps(results))
        print("at the configured endpoint:", json.dumps(sorted(set(at_google))))
        print("at the redirect target:", json.dumps(at_other))
        # The harness, per chat client: a plain call succeeded, and the configured endpoint
        # saw the token on it and on every redirect code (so each redirect was really served).
        assert "GOOGLE token-request" in at_google, probe.stderr[-2000:]
        for kind in ("sync", "async"):
            assert f"PROBE {kind} ok ok" in results, (kind, results, probe.stderr[-2000:])
            assert f"GOOGLE served {kind} token=yes" in at_google, (kind, at_google)
            for code in CODES:
                assert f"GOOGLE redirect {kind} {code} token=yes" in at_google, (kind, code)
        embed = sorted({line for line in at_google if " embed " in line})
        print("embedding client at the configured endpoint (reported, not asserted):", embed)
        if not embed:
            print("NOTE: the embedding client never reached the scripted endpoint; its "
                  "redirect behaviour is NOT covered by this run")  # fmt: skip
        # D15: whatever a client followed, the token did not travel with it.
        leaked = [line for line in at_other if "token=yes" in line or "auth=yes" in line]
        assert not leaked, leaked
        followed = sorted({line.split()[1] for line in at_other})
        print("followed without the token (methods at the redirect target):", followed)
        # §12.6, the part reachable without Google: a failed token exchange prints no secret.
        assert "GOOGLE bad-token-request" in at_google
        assert any(line.startswith("PROBE badtoken error") for line in results), results
        printed = probe.stdout + probe.stderr
        body = [line for line in pem.splitlines() if line and not line.startswith("-----")]
        for secret in (TOKEN, *body):
            assert secret not in printed
        print("printed by the probe process:", len(printed.splitlines()), "lines")
    finally:
        docker("rm", "-f", google, other, check=False)
        docker("network", "rm", network, check=False)


# --- 2. a running gateway ----------------------------------------------------------------------

VERTEX = {
    "vertex_credentials": f"os.environ/{KEY_NAME}",
    "vertex_project": PROJECT,
    "vertex_location": "us-central1",
}
CONFIG = {
    "model_list": [
        {"model_name": "v", "litellm_params": {"model": "vertex_ai/gemini-2.5-flash", **VERTEX}},
        {
            "model_name": "v-embed",
            "litellm_params": {"model": "vertex_ai/text-embedding-005", **VERTEX},
        },
    ],
    "litellm_settings": {"turn_off_message_logging": True, "set_verbose": False},
    "general_settings": {
        "master_key": "os.environ/LITELLM_MASTER_KEY",
        "database_url": "os.environ/DATABASE_URL",
        "store_prompts_in_spend_logs": False,
    },
}
STACK = {
    "kind": "stack",
    "schema_version": 1,
    "name": "vertexgw",
    "services": {
        "postgres": {
            "ref": "service/postgres-pgvector@1.2.0",
            "with": {"databases": ["litellm", "agent"]},
        },
        # The listener every client-side endpoint parameter points at (FIELDS' B_URL).
        "provider-b": {"ref": "service/provider-b@1.0.0"},
        "litellm": {
            "ref": "service/litellm@1.4.0",
            "with": {"config": "litellm.yaml"},
            "wire": {"database-url": "postgres.databases.litellm"},
        },
    },
    "environment": {
        "gateway": "litellm",
        "models": ["v", "v-embed"],
        "ceilings": {"budget_usd": 5, "rpm": 600},
        "agents": {"vertex-agent": {"sponsor": "lead@example.com", "review_by": "2027-06-30"}},
        "bindings": {"DATABASE_URL": {"ref": "postgres.databases.agent"}},
    },
}
# Endpoints that describe deployments or the config (§4.2 readback). `/health` is left out on
# purpose: it calls every model, which here would be a token exchange with Google.
READBACK = (
    "/model/info",
    "/v1/model/info",
    "/v2/model/info",
    "/models",
    "/v1/models",
    "/model/list",
    "/model_group/info",
    "/v1/model_group/info",
    "/model/metrics",
    "/get/config/callbacks",
    "/config/yaml",
    "/get/config",
    "/credentials",
    "/router/settings",
)
# The request filter's refusal, or LiteLLM's own check of api_base/base_url/user_config in a
# body. Not the generic "not allowed": a key without access to the model says that too.
REFUSAL_MARKS = ("GATEWAY_PARAM_REFUSED", "not allowed in request body")


def _throwaway_key() -> tuple[str, list[str]]:
    """A service-account key as Google issues one (extra fields included), on one line, and
    the strings that must never be read back or logged."""
    pem = _pem()
    key = {
        "type": "service_account",
        "project_id": PROJECT,
        "private_key_id": KEY_ID,
        "private_key": pem,
        "client_email": f"gateway@{PROJECT}.iam.gserviceaccount.com",
        "client_id": "100000000000000000001",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "universe_domain": "googleapis.com",
    }
    body = pem.splitlines()
    return json.dumps(key), [KEY_ID, body[2], body[5], "PRIVATE KEY", "private_key"]


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    text, secrets = _throwaway_key()
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    monkeypatch.setenv(KEY_NAME, text)
    root = tmp_path / "project"
    version = root / "registry/service/provider-b/1.0.0"
    (version / "files").mkdir(parents=True)
    (version / "manifest.yaml").write_text("kind: service\nname: provider-b\nversion: 1.0.0\n")
    (version / "service.yaml").write_text(yaml.safe_dump(_provider_service("B")))
    (version / "files/provider.py").write_text(PROVIDER)
    (root / "litellm.yaml").write_text(yaml.safe_dump(CONFIG, sort_keys=False))
    (root / "stack.yaml").write_text(yaml.safe_dump(STACK, sort_keys=False))
    instance = Engine(root)
    yield instance, text, secrets
    try:
        instance.active_deployments = lambda: []
        instance.down(volumes=True)
    finally:
        instance.close()


def _get(base: str, key: str, path: str) -> tuple[int, str]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def _chat(**extra) -> dict:
    return {"model": "v", "messages": [{"role": "user", "content": "hello"}], **extra}


def test_a_vertex_gateway_keeps_its_key_and_its_route(gateway):
    instance, key_text, secrets = gateway
    grants = []
    instance.up("dev", confirm=lambda grant: grants.append(grant) or True)
    label = f"vertex ({KEY_NAME}, project {PROJECT}, us-central1)"
    assert grants[0]["gateway_config"]["providers"] == {"v": label, "v-embed": label}
    container = f"{instance.state.project}-litellm-1"
    master = (instance.state.directory / "service-secrets/litellm/master-key").read_text()
    base = f"http://127.0.0.1:{instance.status()['services']['litellm']['host_ports']['http']}"

    # 1. LiteLLM's own process holds the canonical key: the seven fields, nothing else.
    read = "import sys; sys.stdout.buffer.write(open('/proc/1/environ', 'rb').read())"
    environ = docker("exec", container, "/app/.venv/bin/python3", "-c", read).stdout.split("\0")
    held = [item.partition("=")[2] for item in environ if item.startswith(KEY_NAME + "=")]
    assert len(held) == 1
    assert list(json.loads(held[0])) == list(gateway_providers.VERTEX_KEY_FIELDS)
    assert held[0] == gateway_providers.vertex_canonical(
        gateway_providers.vertex_key(key_text.encode())
    )
    names = {item.partition("=")[0] for item in environ if item}
    assert not {n for n in names if n.startswith(("GOOGLE_", "CLOUDSDK_", "AWS_SECRET"))}
    print("\nvariables in the gateway process:", sorted(names))

    client = gateway_keys.LiteLLM(base, master)
    keys = {}
    for label_, routes in (("d", gateway_keys.key_routes(None)), ("o", None)):
        alias = gateway_keys.alias(label_ * 32)
        keys[alias] = client.mint(
            alias, ["v", "v-embed"], 1.0, 600,
            {"agentbreeder": True, "deployment_id": label_ * 32}, duration="1h", routes=routes,
        )  # fmt: skip

    # 2. Credential readback: no endpoint returns the key to a virtual key.
    readback = {}
    for alias, key in keys.items():
        for path in READBACK:
            status, text = _get(base, key, path)
            readback[f"{alias[-4:]} {path}"] = status
            for secret in secrets:
                assert secret not in text, (path, status, "returned key material")
    print("readback statuses (virtual keys):", json.dumps(readback))
    operator = {}
    for path in READBACK:  # key material here is reported, not asserted: the operator's key
        status, text = _get(base, master, path)
        operator[path] = [status, any(secret in text for secret in secrets[:3])]
    print("readback with the master key [status, key material present]:", json.dumps(operator))
    # The check above is not vacuous: the endpoint is live and describes the Vertex aliases.
    status, text = _get(base, master, "/model/info")
    assert status == 200 and '"v-embed"' in text and "vertex_ai/" in text, (status, text[:300])
    # Control: the no-limit key does reach its aliases, so a refusal below is not "no access".
    open_key = keys[gateway_keys.alias("o" * 32)]
    status, text = _get(base, open_key, "/v1/models")
    assert status == 200 and '"v"' in text.replace(" ", ""), (status, text[:300])

    # 3. Client-side parameters: refused before any provider call, for both keys.
    attempts = {f"body {field}": _chat(**{field: value}) for field, value in FIELDS.items()}
    attempts |= {
        f"custom_llm_provider={name}": _chat(custom_llm_provider=name) for name in PROVIDERS
    }
    attempts["extra_body vertex_project"] = _chat(extra_body={"vertex_project": "other-project"})
    results = {}
    for alias, key in keys.items():
        for name, body in attempts.items():
            results[f"{alias[-4:]} {name}"] = _post(base, key, "/v1/chat/completions", body)
        embed = {"model": "v-embed", "input": ["x"], "vertex_location": "europe-west4"}
        results[f"{alias[-4:]} embed vertex_location"] = _post(base, key, "/v1/embeddings", embed)
    print("override statuses:", json.dumps({name: r[0] for name, r in results.items()}))
    for name, (status, text) in results.items():
        assert 400 <= status < 500, (name, status, text[:300])
        if name.startswith("o"):  # the deploy-shaped key may be stopped earlier, by its routes
            assert any(mark in text for mark in REFUSAL_MARKS), (name, status, text[:300])
    # The filter itself, by name, for the provider-routing and Vertex parameters.
    filtered = [f"custom_llm_provider={name}" for name in PROVIDERS]
    filtered += [f"body {field}" for field in FIELDS if field.startswith("vertex_")]
    filtered += ["extra_body vertex_project", "embed vertex_location"]
    for name in filtered:
        status, text = results[f"{gateway_keys.alias('o' * 32)[-4:]} {name}"]
        assert status == 400 and "GATEWAY_PARAM_REFUSED" in text, (name, status, text[:300])
    listener = docker("logs", f"{instance.state.project}-provider-b-1").stdout
    assert "PROVIDER-" not in listener, listener

    # 4. The logs hold no key material (LITELLM_LOG=WARNING). Only refused requests were sent,
    # so this does not cover a failing Vertex call (module docstring). The two generic markers
    # ("PRIVATE KEY", "private_key") are left out: a log line may name the field.
    logs = docker("logs", container)
    text = logs.stdout + logs.stderr
    for secret in (*secrets[:3], master, *keys.values()):
        assert secret not in text
    assert "refused GATEWAY_PARAM_REFUSED" in text
    print("gateway log lines:", len(text.splitlines()))
    for alias in keys:
        client.revoke(alias)


# --- 3. metadata services, on the 1.4.0 launcher -----------------------------------------------


def test_the_1_4_0_launcher_environment_sends_nothing_to_metadata_services(tmp_path, monkeypatch):
    import test_ollama_gateway_docker as metadata

    monkeypatch.setattr(metadata, "V12", V14)  # the same harness, this version's launcher
    assert metadata.IMAGE == IMAGE
    metadata.test_the_launcher_environment_sends_nothing_to_metadata_services(tmp_path)
