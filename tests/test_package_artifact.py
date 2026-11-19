"""The agent package's OCI artifact and its loopback push (M13 #763, spec §8.1, §8.2, §12.5).

The registry here is a small in-process HTTP server on 127.0.0.1; no Docker runs. The same push
against kind's real `registry:2` is in `tests/test_package_artifact_kind.py` (opt-in).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from test_package import AGENT_YAML, lock_bytes, record_data

from agentbreeder import package, package_artifact
from agentbreeder.agentfile import NAME_PATTERN
from agentbreeder.package import PackageError
from agentbreeder.package_artifact import (
    ARTIFACT_TYPE,
    EMPTY_TYPE,
    LAYER_TYPE,
    MANIFEST_TYPE,
    build,
    describe,
    loopback_registry,
    push,
    scan,
    without_subject,
)
from agentbreeder.registry import copy_and_hash

NAME, VERSION = "support-bot", "1.4.0"
SUBJECT = {"mediaType": MANIFEST_TYPE, "digest": "sha256:" + "a" * 64, "size": 512}


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def make_package(root: Path, agent_yaml: bytes = AGENT_YAML, description: str = "Bot") -> Path:
    staging = root / "agent" / NAME / VERSION
    staging.parent.mkdir(parents=True)
    data = record_data()
    data["inputs"]["agent_yaml_sha256"] = sha(agent_yaml)
    parsed = package.parse(json.dumps(data).encode())
    package.assemble(staging, parsed, agent_yaml, lock_bytes(), description)
    return staging


def code(raised) -> str:
    return raised.value.code


# -- the registry host (§8.1) --------------------------------------------------------------------


def test_loopback_registry_accepts_the_ip_literal_only():
    assert loopback_registry("127.0.0.1:5001/agents") == ("127.0.0.1:5001", "agents")
    assert loopback_registry("127.0.0.1/a/b.c_d-e") == ("127.0.0.1", "a/b.c_d-e")


@pytest.mark.parametrize(
    "value",
    [
        "127.0.0.1.evil.com/x",
        "localhost:5001/x",
        "127.0.0.2:5001/x",
        "127.0.0.1:5001",
        "127.0.0.1:5001/",
        "127.0.0.1:0/x",
        "127.0.0.1:65536/x",
        "127.0.0.1:5001/../x",
        "127.0.0.1:5001/x/",
        "127.0.0.1:5001/x-",
        "127.0.0.1:5001/a..b",
        "127.0.0.1:5001/a.",
        "127.0.0.1:5001/X",
        "127.0.0.1:5001/x\n",
        "user@127.0.0.1:5001/x",
        "http://127.0.0.1:5001/x",
        "[::1]:5001/x",
        "0x7f.0.0.1:5001/x",
        "",
        None,
        5001,
    ],
)
def test_loopback_registry_refuses_everything_else(value):
    with pytest.raises(PackageError) as raised:
        loopback_registry(value)
    assert code(raised) == "REGISTRY_NOT_LOCAL"


def test_agent_names_fit_the_oci_repository_grammar():
    """§12.4: no agent name needs escaping in `/v2/<path>/<name>/…`."""
    assert NAME_PATTERN == r"^[a-z0-9]([a-z0-9-]{0,51}[a-z0-9])?$"


# -- build (§8.2) --------------------------------------------------------------------------------


def test_build_gives_one_manifest_with_one_layer(tmp_path):
    version = make_package(tmp_path)
    artifact = build(version, NAME, VERSION)
    document = json.loads(artifact.manifest)
    assert document == {
        "schemaVersion": 2,
        "mediaType": MANIFEST_TYPE,
        "artifactType": ARTIFACT_TYPE,
        "config": {"mediaType": EMPTY_TYPE, "digest": sha(b"{}"), "size": 2},
        "layers": [
            {"mediaType": LAYER_TYPE, "digest": sha(artifact.layer), "size": len(artifact.layer)}
        ],
    }
    assert sha(b"{}") == (
        "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    )
    assert artifact.tag == "v1.4.0.agent"
    assert artifact.digest == sha(artifact.manifest)
    assert artifact.subject is False
    assert ARTIFACT_TYPE != "application/vnd.agentbreeder.registry.v1"  # M9b's registry source


def test_the_layer_extracts_to_the_package_tree_hash(tmp_path):
    version = make_package(tmp_path)
    artifact = build(version, NAME, VERSION)
    out = tmp_path / "out"
    with tarfile.open(fileobj=io.BytesIO(artifact.layer), mode="r:gz") as tar:
        tar.extractall(out, filter="data") if hasattr(tarfile, "data_filter") else tar.extractall(
            out
        )
    assert copy_and_hash(out / "agent" / NAME / VERSION) == copy_and_hash(version)


def test_build_is_deterministic(tmp_path):
    first = build(make_package(tmp_path / "a"), NAME, VERSION, SUBJECT)
    second = build(make_package(tmp_path / "b"), NAME, VERSION, dict(reversed(SUBJECT.items())))
    assert first == second
    assert first.manifest == second.manifest


def test_subject_is_recorded_and_can_be_dropped(tmp_path):
    artifact = build(make_package(tmp_path), NAME, VERSION, SUBJECT)
    assert json.loads(artifact.manifest)["subject"] == SUBJECT
    assert artifact.subject is True
    bare = without_subject(artifact)
    assert "subject" not in json.loads(bare.manifest)
    assert (bare.layer, bare.subject) == (artifact.layer, False)
    assert bare == build(make_package(tmp_path / "again"), NAME, VERSION)


@pytest.mark.parametrize(
    "subject",
    [
        {},
        "sha256:" + "a" * 64,
        {**SUBJECT, "urls": ["http://example.com"]},
        {**SUBJECT, "mediaType": "text/html"},
        {**SUBJECT, "digest": "sha512:" + "a" * 128},
        {**SUBJECT, "digest": 5},
        {**SUBJECT, "size": 0},
        {**SUBJECT, "size": True},
        {**SUBJECT, "size": "512"},
    ],
)
def test_build_refuses_a_bad_subject(tmp_path, subject):
    with pytest.raises(PackageError) as raised:
        build(make_package(tmp_path), NAME, VERSION, subject)
    assert code(raised) == "PACKAGE_INVALID"


@pytest.mark.parametrize("name,version", [("Bad", VERSION), (NAME, "1.4"), (None, VERSION)])
def test_build_refuses_a_bad_ref(tmp_path, name, version):
    with pytest.raises(PackageError) as raised:
        build(make_package(tmp_path), name, version)
    assert code(raised) == "PACKAGE_INVALID"


def test_build_refuses_what_load_refuses(tmp_path):
    version = make_package(tmp_path)
    (version / "extra.txt").write_text("x")
    with pytest.raises(PackageError) as raised:
        build(version, NAME, VERSION)
    assert code(raised) == "PACKAGE_INVALID"


def test_build_does_not_show_the_packer_error(tmp_path, monkeypatch):
    from agentbreeder.registry import RegistryError

    def fail(*args, **kwargs):
        raise RegistryError("ENTRY_UNSAFE", f"changed while packing: {tmp_path}")

    monkeypatch.setattr(package_artifact, "pack", fail)
    with pytest.raises(PackageError) as raised:
        build(make_package(tmp_path), NAME, VERSION)
    assert code(raised) == "PACKAGE_INVALID"
    assert str(tmp_path) not in raised.value.message


# -- leak scan (§12.3, §12.5) --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,what",
    [
        (b"# built at /Users/someone/project\n", "/Users/"),
        (b"# built at /home/someone/project\n", "/home/"),
        (b"# see ~/notes\n", "~/"),
        (b"# see ../../shared/registry\n", ".."),
        (b"# see registry/..\n", ".."),
    ],
)
def test_a_leaking_file_is_refused_without_showing_the_text(tmp_path, text, what):
    version = make_package(tmp_path, AGENT_YAML + text)
    with pytest.raises(PackageError) as raised:
        build(version, NAME, VERSION)
    assert code(raised) == "PACKAGE_LEAK"
    assert "agent.yaml" in raised.value.message and what in raised.value.message
    assert "someone" not in raised.value.message and "notes" not in raised.value.message


def test_the_home_directory_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/srv/people/jo")
    version = make_package(tmp_path, AGENT_YAML + b"# /srv/people/jo/x\n")
    with pytest.raises(PackageError) as raised:
        build(version, NAME, VERSION)
    assert code(raised) == "PACKAGE_LEAK"
    assert "home directory" in raised.value.message
    assert "/srv/people" not in raised.value.message


@pytest.mark.parametrize(
    "text",
    [
        b"description: Answers questions... and more\n",
        b"health_path: /health\n",
        b"note: about ~5 items, a..b, v1..2\n",
    ],
)
def test_ordinary_text_is_not_a_leak(tmp_path, text):
    build(make_package(tmp_path, AGENT_YAML + text), NAME, VERSION)


def test_a_released_package_has_no_leak_after_lock_redaction(tmp_path):
    """`lock_bytes()` names `../../shared/registry` and `/Users/someone/reg`; the package's lock
    copy holds `external:` instead, so the scan passes."""
    artifact = build(make_package(tmp_path), NAME, VERSION)
    assert b"external:" in gzip.decompress(artifact.layer)


def tarball(entries: dict[str, bytes | None]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return gzip.compress(raw.getvalue())


def test_scan_refuses_a_layer_that_is_not_exactly_one_package(tmp_path):
    prefix = f"agent/{NAME}/{VERSION}"
    good = {"agent": None, f"agent/{NAME}": None, prefix: None}
    good |= {f"{prefix}/{name}": b"x" for name in package.FILES}
    scan(tarball(good), NAME, VERSION)
    for bad in (
        {**good, f"{prefix}/extra": b"x"},
        {key: value for key, value in good.items() if not key.endswith("agent.lock")},
        {**good, "agent/other": None},
    ):
        with pytest.raises(PackageError) as raised:
            scan(tarball(bad), NAME, VERSION)
        assert code(raised) == "PACKAGE_INVALID"
    with pytest.raises(PackageError) as raised:
        scan(b"not a gzip", NAME, VERSION)
    assert code(raised) == "PACKAGE_INVALID"
    with pytest.raises(PackageError) as raised:
        scan(gzip.compress(b"not a tar" * 100), NAME, VERSION)
    assert code(raised) == "PACKAGE_INVALID"


# -- a fake loopback registry --------------------------------------------------------------------


class Registry:
    """OCI distribution, as far as a push needs: blob HEAD, upload POST then PUT, manifest PUT
    and GET. Flags make it misbehave."""

    def __init__(self):
        self.blobs: dict[str, bytes] = {}
        self.manifests: dict[str, tuple[str, bytes]] = {}
        self.requests: list[tuple[str, str, dict]] = []
        self.refuse_subject = False
        self.location = None  # an upload Location to send instead of the usual one
        self.tamper = False
        self.fail: dict[tuple[str, str], int] = {}  # (method, path part) -> status
        registry = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body=b"", headers=None):
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def handle_any(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                registry.requests.append((self.command, self.path, dict(self.headers)))
                for (method, part), status in registry.fail.items():
                    if method == self.command and part in self.path:
                        elsewhere = {"Location": "http://example.com/"} if status < 400 else {}
                        return self.reply(status, headers=elsewhere)
                path, _, query = self.path.partition("?")
                repository, _, rest = path.removeprefix("/v2/").rpartition("/blobs/")
                if repository and self.command == "HEAD":
                    return self.reply(200 if rest in registry.blobs else 404)
                if repository and self.command == "POST" and rest == "uploads/":
                    location = registry.location or f"/v2/{repository}/blobs/uploads/u1?state=s"
                    return self.reply(202, headers={"Location": location})
                if repository and self.command == "PUT":
                    digest = query.rpartition("digest=")[2].replace("%3A", ":")
                    if "state=s" not in query or sha(body) != digest:
                        return self.reply(400)
                    registry.blobs[digest] = body
                    return self.reply(201)
                repository, _, reference = path.removeprefix("/v2/").rpartition("/manifests/")
                if repository and self.command == "PUT":
                    document = json.loads(body)
                    if registry.refuse_subject and "subject" in document:
                        return self.reply(400, b'{"errors":[{"code":"MANIFEST_INVALID"}]}')
                    for item in [document["config"], *document["layers"]]:
                        if item["digest"] not in registry.blobs:
                            return self.reply(400)
                    held = (self.headers["Content-Type"], body)
                    registry.manifests[f"{repository}:{reference}"] = held
                    registry.manifests[f"{repository}@{sha(body)}"] = held
                    return self.reply(201)
                if repository and self.command == "GET":
                    key = f"{repository}{'@' if reference.startswith('sha256:') else ':'}"
                    found = registry.manifests.get(key + reference)
                    if found is None:
                        return self.reply(404)
                    body = found[1] + b" " if registry.tamper else found[1]
                    return self.reply(200, body, {"Content-Type": found[0] + "; charset=utf-8"})
                return self.reply(404)

            do_GET = do_HEAD = do_POST = do_PUT = handle_any

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"127.0.0.1:{self.server.server_address[1]}"
        self.url = f"{self.host}/agents"
        threading.Thread(target=self.server.serve_forever, args=(0.01,), daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def registry():
    server = Registry()
    yield server
    server.close()


@pytest.fixture
def artifact(tmp_path):
    return build(make_package(tmp_path), NAME, VERSION)


# -- push (§8.2, §12.5) --------------------------------------------------------------------------


def test_push_stores_the_layer_and_the_manifest_under_the_agent_tag(registry, artifact):
    pushed = push(registry.url, artifact)
    assert pushed.reference == f"{registry.host}/agents/{NAME}@{artifact.digest}"
    assert pushed.digest == artifact.digest and pushed.subject is False
    assert registry.manifests[f"agents/{NAME}:v1.4.0.agent"] == (MANIFEST_TYPE, artifact.manifest)
    assert registry.blobs[sha(artifact.layer)] == artifact.layer
    assert registry.blobs[sha(b"{}")] == b"{}"


def test_push_sends_no_credentials_and_only_talks_to_the_registry(registry, artifact):
    push(registry.url, artifact)
    for _, path, headers in registry.requests:
        assert path.startswith(f"/v2/agents/{NAME}/")
        assert not {"authorization", "cookie", "proxy-authorization"} & {
            k.lower() for k in headers
        }
        assert headers["Host"] == registry.host


def test_a_second_push_uploads_no_blob_and_gives_the_same_reference(registry, artifact):
    first = push(registry.url, artifact)
    registry.requests.clear()
    assert push(registry.url, artifact) == first
    assert not [r for r in registry.requests if r[0] == "POST"]


def test_push_ignores_proxy_settings(registry, artifact, monkeypatch):
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    assert push(registry.url, artifact).digest == artifact.digest


def test_a_refused_subject_is_pushed_without_it(registry, tmp_path):
    with_subject = build(make_package(tmp_path), NAME, VERSION, SUBJECT)
    registry.refuse_subject = True
    pushed = push(registry.url, with_subject)
    bare = without_subject(with_subject)
    assert pushed.subject is False and pushed.digest == bare.digest
    assert registry.manifests[f"agents/{NAME}:v1.4.0.agent"][1] == bare.manifest


def test_an_accepted_subject_is_kept(registry, tmp_path):
    with_subject = build(make_package(tmp_path), NAME, VERSION, SUBJECT)
    pushed = push(registry.url, with_subject)
    assert pushed.subject is True and pushed.digest == with_subject.digest
    assert json.loads(registry.manifests[f"agents/{NAME}:v1.4.0.agent"][1])["subject"] == SUBJECT


def test_push_refuses_a_registry_that_is_not_loopback(artifact):
    with pytest.raises(PackageError) as raised:
        push("localhost:5001/agents", artifact)
    assert code(raised) == "REGISTRY_NOT_LOCAL"


@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.2:5001/v2/agents/support-bot/blobs/uploads/u1",
        "http://example.com/v2/x/blobs/uploads/u1",
        "https://{host}/v2/x/blobs/uploads/u1",
        "//example.com/upload",
    ],
)
def test_an_upload_location_on_another_host_is_refused(registry, artifact, location):
    registry.location = location.format(host=registry.host)
    with pytest.raises(PackageError) as raised:
        push(registry.url, artifact)
    assert code(raised) == "REGISTRY_NOT_LOCAL"
    assert not registry.blobs


def test_an_absolute_upload_location_on_the_registry_is_used(registry, artifact):
    registry.location = f"http://{registry.host}/v2/agents/{NAME}/blobs/uploads/u9?state=s"
    assert push(registry.url, artifact).digest == artifact.digest


@pytest.mark.parametrize(
    "method,part,status",
    [
        ("POST", "/blobs/uploads/", 500),
        ("POST", "/blobs/uploads/", 307),
        ("PUT", "/blobs/uploads/", 500),
        ("PUT", "/manifests/", 500),
        ("PUT", "/manifests/", 404),
        ("GET", "/manifests/", 404),
    ],
)
def test_a_registry_failure_is_reported_as_unreadable(registry, artifact, method, part, status):
    registry.fail[(method, part)] = status
    with pytest.raises(PackageError) as raised:
        push(registry.url, artifact)
    assert code(raised) == "REGISTRY_UNREADABLE"


def test_a_redirect_is_never_followed(registry, artifact):
    registry.fail[("HEAD", "/blobs/")] = 302
    registry.fail[("POST", "/blobs/uploads/")] = 302
    with pytest.raises(PackageError) as raised:
        push(registry.url, artifact)
    assert code(raised) == "REGISTRY_UNREADABLE"
    assert [r[0] for r in registry.requests] == ["HEAD", "POST"]


def test_a_different_manifest_read_back_is_a_mismatch(registry, artifact):
    registry.tamper = True
    with pytest.raises(PackageError) as raised:
        push(registry.url, artifact)
    assert code(raised) == "REGISTRY_MISMATCH"


def test_an_unreachable_registry_is_unreadable(artifact):
    server = Registry()
    server.close()
    with pytest.raises(PackageError) as raised:
        push(server.url, artifact, timeout=2)
    assert code(raised) == "REGISTRY_UNREADABLE"


def test_a_missing_upload_location_is_refused(registry, artifact, monkeypatch):
    real = package_artifact._call

    def call(method, url, **kwargs):
        status, headers, body = real(method, url, **kwargs)
        return status, {} if method == "POST" else headers, body

    monkeypatch.setattr(package_artifact, "_call", call)
    with pytest.raises(PackageError) as raised:
        push(registry.url, artifact)
    assert code(raised) == "REGISTRY_UNREADABLE"


# -- describe: the image's descriptor, the artifact's subject ------------------------------------


def put_image(registry: Registry, media_type: str = MANIFEST_TYPE) -> tuple[str, bytes]:
    body = json.dumps({"schemaVersion": 2, "mediaType": media_type}).encode()
    registry.manifests[f"agents/{NAME}@{sha(body)}"] = (media_type, body)
    return f"{registry.url}/{NAME}@{sha(body)}", body


def test_describe_reads_the_image_manifest_descriptor(registry, tmp_path):
    reference, body = put_image(registry)
    subject = describe(reference)
    assert subject == {"mediaType": MANIFEST_TYPE, "digest": sha(body), "size": len(body)}
    pushed = push(registry.url, build(make_package(tmp_path), NAME, VERSION, subject))
    assert pushed.subject is True


def test_describe_refuses_bad_references_and_answers(registry):
    reference, _ = put_image(registry)
    for bad, expected in (
        (f"localhost:5001/agents/{NAME}@sha256:" + "a" * 64, "REGISTRY_NOT_LOCAL"),
        (f"{registry.url}/{NAME}:v1.4.0", "REGISTRY_UNREADABLE"),
        (f"{registry.url}/{NAME}@sha256:" + "b" * 64, "REGISTRY_UNREADABLE"),
        (None, "REGISTRY_UNREADABLE"),
    ):
        with pytest.raises(PackageError) as raised:
            describe(bad)
        assert code(raised) == expected
    registry.tamper = True
    with pytest.raises(PackageError) as raised:
        describe(reference)
    assert code(raised) == "REGISTRY_MISMATCH"
    registry.tamper = False
    other, _ = put_image(registry, "text/html")
    with pytest.raises(PackageError) as raised:
        describe(other)
    assert code(raised) == "PACKAGE_INVALID"


# -- review round 1 (security) -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        b"docs: https://example.com/home/page\n",
        b"scim: https://idp.example.com/api/Users/12\n",
    ],
)
def test_a_url_path_is_not_a_home_path(tmp_path, text):
    build(make_package(tmp_path, AGENT_YAML + text), NAME, VERSION)


def test_a_file_url_home_path_is_a_leak(tmp_path):
    with pytest.raises(PackageError) as raised:
        build(make_package(tmp_path, AGENT_YAML + b"# file:///home/jo/x\n"), NAME, VERSION)
    assert code(raised) == "PACKAGE_LEAK"


def test_a_short_home_does_not_match_longer_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/app")
    build(make_package(tmp_path / "a", AGENT_YAML + b"# /application /apps/x\n"), NAME, VERSION)
    with pytest.raises(PackageError) as raised:
        build(make_package(tmp_path / "b", AGENT_YAML + b"# /app/x\n"), NAME, VERSION)
    assert code(raised) == "PACKAGE_LEAK"


def test_scan_refuses_an_oversized_layer(monkeypatch):
    prefix = f"agent/{NAME}/{VERSION}"
    entries = {"agent": None, f"agent/{NAME}": None, prefix: None}
    entries |= {f"{prefix}/{name}": b"x" * 100 for name in package.FILES}
    layer = tarball(entries)
    monkeypatch.setattr(package_artifact, "MAX_BYTES", 399)
    with pytest.raises(PackageError) as raised:
        scan(layer, NAME, VERSION)  # 400 bytes of members
    assert code(raised) == "PACKAGE_INVALID"
    monkeypatch.setattr(package_artifact, "MAX_BYTES", len(layer) - 1)
    with pytest.raises(PackageError) as raised:
        scan(layer, NAME, VERSION)
    assert code(raised) == "PACKAGE_INVALID"


def test_a_malformed_reply_is_unreadable(artifact):
    import socket

    listener = socket.create_server(("127.0.0.1", 0))

    def answer():
        connection, _ = listener.accept()
        connection.recv(65536)
        connection.sendall(b"garbage\r\n\r\n")
        connection.close()

    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    try:
        with pytest.raises(PackageError) as raised:
            push(f"127.0.0.1:{listener.getsockname()[1]}/agents", artifact, timeout=5)
        assert code(raised) == "REGISTRY_UNREADABLE"
    finally:
        thread.join(5)
        listener.close()


def test_push_checks_an_artifact_made_by_hand(registry, tmp_path, artifact):
    leaking = make_package(tmp_path / "leak", AGENT_YAML + b"# /Users/someone/x\n")
    from agentbreeder.pack import pack

    layer = pack(leaking, "agent", NAME, VERSION)
    forged = package_artifact.Artifact(
        NAME, VERSION, layer, package_artifact._manifest(layer, None), False
    )
    with pytest.raises(PackageError) as raised:
        push(registry.url, forged)
    assert code(raised) == "PACKAGE_LEAK"
    for manifest, subject in (
        (b"{}", False),
        (b"not json", False),
        (b"[]", False),
        (artifact.manifest, True),
        (package_artifact._manifest(b"other", None), False),
        ("text", False),
    ):
        forged = package_artifact.Artifact(NAME, VERSION, artifact.layer, manifest, subject)
        with pytest.raises(PackageError) as raised:
            push(registry.url, forged)
        assert code(raised) == "PACKAGE_INVALID"
    assert not registry.requests


@pytest.mark.parametrize("status", [401, 403, 429])
def test_an_auth_or_rate_answer_does_not_drop_the_subject(registry, tmp_path, status):
    registry.fail[("PUT", "/manifests/")] = status
    with pytest.raises(PackageError) as raised:
        push(registry.url, build(make_package(tmp_path), NAME, VERSION, SUBJECT))
    assert code(raised) == "REGISTRY_UNREADABLE"
    assert len([r for r in registry.requests if r[0] == "PUT" and "/manifests/" in r[1]]) == 1


def test_a_digest_in_the_upload_location_is_replaced(registry, artifact):
    registry.location = f"/v2/agents/{NAME}/blobs/uploads/u1?digest=sha256%3A{'0' * 64}&state=s"
    push(registry.url, artifact)
    puts = [r[1] for r in registry.requests if r[0] == "PUT" and "/blobs/" in r[1]]
    assert puts and all(path.count("digest=") == 1 and "0" * 64 not in path for path in puts)


# -- review round 2 ------------------------------------------------------------------------------


def test_scan_refuses_links_and_files_where_directories_belong():
    prefix = f"agent/{NAME}/{VERSION}"
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for name in ("agent", f"agent/{NAME}", prefix):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
        for name in package.FILES:
            info = tarfile.TarInfo(f"{prefix}/{name}")
            info.type = tarfile.SYMTYPE
            info.linkname = "/Users/x/secret"
            archive.addfile(info)
    with pytest.raises(PackageError) as raised:
        scan(gzip.compress(raw.getvalue()), NAME, VERSION)
    assert code(raised) == "PACKAGE_INVALID" and "links" in raised.value.message
    entries = {"agent": b"", f"agent/{NAME}": None, prefix: None}
    entries |= {f"{prefix}/{name}": b"x" for name in package.FILES}
    with pytest.raises(PackageError) as raised:
        scan(tarball(entries), NAME, VERSION)
    assert code(raised) == "PACKAGE_INVALID"


def test_a_zero_padded_port_is_normalised():
    """Else the registry's own `Location` host would not equal ours."""
    assert loopback_registry("127.0.0.1:0080/agents") == ("127.0.0.1:80", "agents")


def test_a_failure_while_reading_an_error_body_is_unreadable(artifact, monkeypatch):
    from urllib.error import HTTPError

    class Broken(io.BytesIO):
        def read(self, *args):
            raise TimeoutError("timed out")

    class Opener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 500, "boom", {}, Broken())

    monkeypatch.setattr(package_artifact, "build_opener", lambda *handlers: Opener())
    with pytest.raises(PackageError) as raised:
        push("127.0.0.1:5001/agents", artifact)
    assert code(raised) == "REGISTRY_UNREADABLE"
