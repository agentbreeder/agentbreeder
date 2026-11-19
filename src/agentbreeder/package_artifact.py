"""The OCI artifact of one agent package, and its push to a loopback registry (M13 #763, spec
§8.1, §8.2, §12.5).

`build` turns a package directory into one OCI image manifest
(`artifactType: application/vnd.agentbreeder.agent.v1`) with the OCI empty config and exactly one
layer: the deterministic `tar+gzip` of the package at `agent/<name>/<X.Y.Z>/…` (`pack.pack`). The
layer is checked after it is packed, so what is checked is what is pushed: it holds the four
package files and nothing else, and none of them carries a home directory, a `/Users/` or `/home/`
path, a `~/` path or a `..` path segment (`PACKAGE_LEAK`).

`push` sends it to `<registry>/<name>` with tag `v<X.Y.Z>.agent`, over plain HTTP to the IP
literal `127.0.0.1` only (`REGISTRY_NOT_LOCAL` otherwise), with no proxy, no credentials and no
redirects, then reads the tag back and compares the digest (`REGISTRY_MISMATCH`). A manifest with
a `subject` that the registry refuses is pushed again without it; `Pushed.subject` says which one
the registry holds. Tags can be moved by anyone who can reach the registry, so a reader compares
the tree hash of the extracted layer, never the tag.

Nothing here runs Docker, and no command calls this module yet: `deploy agent/… --cluster` is a
later slice.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import re
import tarfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from agentbreeder import package
from agentbreeder.agentfile import NAME_PATTERN
from agentbreeder.pack import pack
from agentbreeder.package import PackageError
from agentbreeder.registry import MAX_BYTES, PACKAGE_KIND, RegistryError

ARTIFACT_TYPE = "application/vnd.agentbreeder.agent.v1"
LAYER_TYPE = "application/vnd.agentbreeder.agent.layer.v1.tar+gzip"
MANIFEST_TYPE = "application/vnd.oci.image.manifest.v1+json"
EMPTY_TYPE = "application/vnd.oci.empty.v1+json"
EMPTY = b"{}"
SUBJECT_TYPES = (
    MANIFEST_TYPE,
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
# Statuses that can mean "this registry does not take a manifest with a subject". An auth or
# rate-limit answer (401, 403, 429) is a failure, not a reason to drop the subject.
SUBJECT_REFUSALS = (400, 404, 405, 415, 422)
RESPONSE_LIMIT = 4 * 1024 * 1024  # the largest manifest or error body read from the registry

_VERSION = re.compile(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_COMPONENT = r"[a-z0-9]+(?:(?:\.|__?|-+)[a-z0-9]+)*"  # the OCI repository grammar
_REGISTRY = re.compile(rf"(127\.0\.0\.1)(?::([0-9]{{1,5}}))?((?:/{_COMPONENT})+)")
_LEAKS = (
    ("a /Users/ path", re.compile(rb"(?:^|[^A-Za-z0-9._-])/Users/")),
    ("a /home/ path", re.compile(rb"(?:^|[^A-Za-z0-9._-])/home/")),
    ("a ~/ path", re.compile(rb"(?:^|[^A-Za-z0-9])~/")),
    ("a .. path segment", re.compile(rb"(?:^|[^A-Za-z0-9.])\.\./|/\.\.(?:[^A-Za-z0-9.]|$)")),
)


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def loopback_registry(value: object) -> tuple[str, str]:
    """`(host[:port], path)` of an `environments.<env>.kubernetes.registry` value. The host must
    be exactly the IP literal `127.0.0.1` (spec §8.1): not `localhost`, not another loopback
    address, and not a name that merely starts with it."""
    match = _REGISTRY.fullmatch(value) if isinstance(value, str) else None
    if match is None or (match[2] is not None and not 0 < int(match[2]) < 65536):
        raise PackageError(
            "REGISTRY_NOT_LOCAL",
            "an agent package is pushed only to a registry at 127.0.0.1[:port]/path "
            "(environments.<env>.kubernetes.registry)",
        )
    host = match[1] if match[2] is None else f"{match[1]}:{int(match[2])}"
    return host, match[3].strip("/")


def _checked_ref(name: str, version: str) -> None:
    if not (
        isinstance(name, str)
        and isinstance(version, str)
        and re.fullmatch(NAME_PATTERN, name)
        and _VERSION.fullmatch(version)
    ):
        raise PackageError("PACKAGE_INVALID", "agent packages are agent/<name>@X.Y.Z")


def _checked_subject(subject: object) -> dict:
    if (
        not isinstance(subject, dict)
        or set(subject) != {"mediaType", "digest", "size"}
        or subject["mediaType"] not in SUBJECT_TYPES
        or not isinstance(subject["digest"], str)
        or not _DIGEST.fullmatch(subject["digest"])
        or type(subject["size"]) is not int
        or subject["size"] <= 0
    ):
        raise PackageError(
            "PACKAGE_INVALID", "the artifact's subject must be an image manifest descriptor"
        )
    return {key: subject[key] for key in ("mediaType", "digest", "size")}


def scan(layer: bytes, name: str, version: str) -> None:
    """Refuse a packed layer that is not exactly one agent package, or whose files or entry names
    carry a home directory or a path that climbs out of the project (spec §12.3, §12.5). The
    message names the file and the kind of text, never the text."""
    prefix = f"{PACKAGE_KIND}/{name}/{version}"
    expected = {PACKAGE_KIND, f"{PACKAGE_KIND}/{name}", prefix}
    expected |= {f"{prefix}/{file}" for file in package.FILES}
    home = os.path.expanduser("~").encode()
    leaks = list(_LEAKS)
    if len(home) > 1 and home != b"~":
        bounded = (
            rb"(?:^|[^A-Za-z0-9._-])" + re.escape(home.rstrip(b"/")) + rb"(?:/|[^A-Za-z0-9._-]|$)"
        )
        leaks.insert(0, ("the home directory", re.compile(bounded)))
    if len(layer) > MAX_BYTES:
        raise PackageError("PACKAGE_INVALID", "the packed package is too large")
    try:
        with tarfile.open(fileobj=io.BytesIO(layer), mode="r:gz") as archive:
            members = archive.getmembers()
            if {member.name for member in members} != expected or len(members) != len(expected):
                raise PackageError(
                    "PACKAGE_INVALID", f"a package holds exactly {', '.join(package.FILES)}"
                )
            if sum(member.size for member in members) > MAX_BYTES:
                raise PackageError("PACKAGE_INVALID", "the packed package is too large")
            for member in members:
                if member.name.count("/") < 3:  # agent, agent/<name>, agent/<name>/<version>
                    if not member.isdir():
                        raise PackageError("PACKAGE_INVALID", "the packed package is unreadable")
                    continue
                if not member.isreg():  # a symlink or hardlink carries a path the scan cannot see
                    raise PackageError(
                        "PACKAGE_INVALID", "a package holds regular files only, no links"
                    )
                data = archive.extractfile(member).read()
                for what, pattern in leaks:
                    if pattern.search(data):
                        raise PackageError(
                            "PACKAGE_LEAK",
                            f"{member.name.rsplit('/', 1)[-1]} of agent/{name}@{version} holds "
                            f"{what}; it is not pushed. Release a new version without it",
                        )
    except (tarfile.TarError, OSError, EOFError, zlib.error):
        raise PackageError("PACKAGE_INVALID", "the packed package is unreadable") from None


@dataclass(frozen=True)
class Artifact:
    """One agent package as an OCI artifact: `manifest` (canonical JSON bytes) names `layer`."""

    name: str
    version: str
    layer: bytes
    manifest: bytes
    subject: bool

    @property
    def digest(self) -> str:
        return _sha256(self.manifest)

    @property
    def tag(self) -> str:
        return f"v{self.version}.agent"


@dataclass(frozen=True)
class Pushed:
    """`reference` is `host/path/name@sha256:…` of the artifact manifest the registry holds;
    `subject` is whether that manifest names the image as its subject."""

    reference: str
    digest: str
    subject: bool


def _manifest(layer: bytes, subject: dict | None) -> bytes:
    document = {
        "schemaVersion": 2,
        "mediaType": MANIFEST_TYPE,
        "artifactType": ARTIFACT_TYPE,
        "config": {"mediaType": EMPTY_TYPE, "digest": _sha256(EMPTY), "size": len(EMPTY)},
        "layers": [{"mediaType": LAYER_TYPE, "digest": _sha256(layer), "size": len(layer)}],
    }
    if subject is not None:
        document["subject"] = subject
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def build(version_dir: Path, name: str, version: str, subject: dict | None = None) -> Artifact:
    """The artifact for the package at `version_dir`, which the caller has already copied to a
    private directory and checked against its release row (spec §7.2 steps 2–3). `subject` is the
    pushed image manifest's descriptor (`describe`), or None."""
    _checked_ref(name, version)
    descriptor = None if subject is None else _checked_subject(subject)
    package.load(version_dir, name, version)
    try:
        layer = pack(version_dir, PACKAGE_KIND, name, version)
    except RegistryError:
        # Never the RegistryError text: it names the directory.
        raise PackageError("PACKAGE_INVALID", "the package could not be packed") from None
    scan(layer, name, version)
    return Artifact(name, version, layer, _manifest(layer, descriptor), descriptor is not None)


def without_subject(artifact: Artifact) -> Artifact:
    """The same layer under a manifest with no `subject`, for a registry that refuses one."""
    return Artifact(
        artifact.name, artifact.version, artifact.layer, _manifest(artifact.layer, None), False
    )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a 3xx is returned to the caller as a status, never followed


def _call(
    method: str,
    url: str,
    data: bytes | None = None,
    headers: dict | None = None,
    timeout: int = 30,
) -> tuple[int, dict, bytes]:
    """One request to the loopback registry: no proxy, no credentials, no redirects. Returns the
    status, the headers (lower-case names) and at most `RESPONSE_LIMIT` + 1 bytes of the body."""
    request = Request(url, data=data, headers=headers or {}, method=method)
    try:
        try:
            reply = build_opener(ProxyHandler({}), _NoRedirect).open(request, timeout=timeout)
        except HTTPError as error:
            reply = error  # a 3xx, 4xx or 5xx answer: read like any other, below
        with reply:
            return reply.code, _lower(reply.headers), reply.read(RESPONSE_LIMIT + 1)
    except (URLError, OSError, ValueError, http.client.HTTPException):
        raise PackageError("REGISTRY_UNREADABLE", "Cannot reach the registry") from None


def _lower(headers) -> dict:
    return {key.lower(): value for key, value in (headers.items() if headers else ())}


def _refused(what: str, status: int) -> PackageError:
    return PackageError("REGISTRY_UNREADABLE", f"The registry refused {what} (HTTP {status})")


def _push_blob(host: str, base: str, data: bytes, timeout: int) -> None:
    digest = _sha256(data)
    if _call("HEAD", f"{base}/blobs/{digest}", timeout=timeout)[0] == 200:
        return
    status, headers, _ = _call("POST", f"{base}/blobs/uploads/", data=b"", timeout=timeout)
    if status != 202 or not headers.get("location"):
        raise _refused("an upload", status)
    location = urlsplit(urljoin(f"{base}/blobs/uploads/", headers["location"]))
    if location.scheme != "http" or location.netloc != host:
        raise PackageError(
            "REGISTRY_NOT_LOCAL", "The registry sent an upload location on another host"
        )
    query = [pair for pair in parse_qsl(location.query, keep_blank_values=True)]
    query = [pair for pair in query if pair[0] != "digest"] + [("digest", digest)]
    target = f"http://{host}{location.path}?{urlencode(query)}"
    status, _, _ = _call(
        "PUT",
        target,
        data=data,
        headers={"Content-Type": "application/octet-stream"},
        timeout=timeout,
    )
    if status != 201:
        raise _refused("a blob", status)


def push(registry: str, artifact: Artifact, *, timeout: int = 30) -> Pushed:
    """Push `artifact` to `<registry>/<name>`, tag `v<X.Y.Z>.agent`, and read the tag back. A
    manifest with a subject that the registry refuses (`SUBJECT_REFUSALS`) is pushed again
    without one."""
    host, path = loopback_registry(registry)
    _checked_ref(artifact.name, artifact.version)
    if not isinstance(artifact.layer, bytes) or not isinstance(artifact.manifest, bytes):
        raise PackageError("PACKAGE_INVALID", "the artifact was not made by build")
    scan(artifact.layer, artifact.name, artifact.version)  # an Artifact can be built by hand
    try:
        subject = json.loads(artifact.manifest).get("subject")
    except (ValueError, AttributeError):
        subject = None
    checked = None if subject is None else _checked_subject(subject)
    if artifact.manifest != _manifest(artifact.layer, checked) or artifact.subject != (
        checked is not None
    ):
        raise PackageError("PACKAGE_INVALID", "the artifact was not made by build")
    repository = f"{path}/{artifact.name}"
    base = f"http://{host}/v2/{repository}"
    for blob in (EMPTY, artifact.layer):
        _push_blob(host, base, blob, timeout)
    headers = {"Content-Type": MANIFEST_TYPE}
    url = f"{base}/manifests/{artifact.tag}"
    status, _, _ = _call("PUT", url, data=artifact.manifest, headers=headers, timeout=timeout)
    if artifact.subject and status in SUBJECT_REFUSALS:
        artifact = without_subject(artifact)
        status, _, _ = _call("PUT", url, data=artifact.manifest, headers=headers, timeout=timeout)
    if status != 201:
        raise _refused("the agent artifact", status)
    status, _, held = _call("GET", url, headers={"Accept": MANIFEST_TYPE}, timeout=timeout)
    if status != 200:
        raise PackageError("REGISTRY_UNREADABLE", "Cannot read the agent artifact back")
    if held != artifact.manifest:
        raise PackageError("REGISTRY_MISMATCH", "The registry holds a different agent artifact")
    return Pushed(f"{host}/{repository}@{artifact.digest}", artifact.digest, artifact.subject)


def describe(reference: str, *, timeout: int = 30) -> dict:
    """The OCI descriptor (`mediaType`, `digest`, `size`) of the manifest at
    `127.0.0.1[:port]/path@sha256:…`, read from the registry and checked against its digest. It
    is the `subject` of the agent artifact pushed next to that image."""
    name, _, digest = reference.partition("@") if isinstance(reference, str) else ("", "", "")
    if not _DIGEST.fullmatch(digest):
        raise PackageError("REGISTRY_UNREADABLE", "The image reference has no sha256 digest")
    host, path = loopback_registry(name)
    status, headers, body = _call(
        "GET",
        f"http://{host}/v2/{path}/manifests/{digest}",
        headers={"Accept": ", ".join(SUBJECT_TYPES)},
        timeout=timeout,
    )
    if status != 200:
        raise PackageError("REGISTRY_UNREADABLE", "Cannot read the image from the registry")
    if len(body) > RESPONSE_LIMIT or _sha256(body) != digest:
        raise PackageError("REGISTRY_MISMATCH", "The registry holds a different image")
    media_type = headers.get("content-type", "").partition(";")[0].strip()
    return _checked_subject({"mediaType": media_type, "digest": digest, "size": len(body)})
