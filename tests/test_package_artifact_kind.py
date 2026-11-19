"""The agent artifact against kind's real `registry:2` (M13 #763, spec §8.2 and the slice-0
question: does the pinned registry accept `artifactType` and `subject`, and serve referrers?).

Opt-in, and it needs the local cluster's registry at 127.0.0.1:5001:

    agentbreeder cluster up
    AGENTBREEDER_DOCKER_TEST=1 python -m pytest tests/test_package_artifact_kind.py -q -s

It runs no Docker command itself: it talks HTTP to the registry. It writes only under the
repository path `m13-artifact-test/`. `-s` shows what the registry did with `subject`.
"""

from __future__ import annotations

import io
import json
import os
import tarfile

import pytest
from test_package_artifact import NAME, VERSION, make_package, sha

from agentbreeder import package_artifact
from agentbreeder.cluster import REGISTRY_PORT
from agentbreeder.package_artifact import MANIFEST_TYPE, build, describe, push
from agentbreeder.registry import copy_and_hash

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in: needs the kind registry"
)

REGISTRY = f"127.0.0.1:{REGISTRY_PORT}/m13-artifact-test"
BASE = f"http://127.0.0.1:{REGISTRY_PORT}/v2/m13-artifact-test/{NAME}"


def get(url: str, accept: str = MANIFEST_TYPE) -> tuple[int, dict, bytes]:
    return package_artifact._call("GET", url, headers={"Accept": accept})


def test_the_artifact_is_stored_and_found_by_tag(tmp_path):
    version = make_package(tmp_path)
    artifact = build(version, NAME, VERSION)
    pushed = push(REGISTRY, artifact)
    assert push(REGISTRY, artifact) == pushed  # a second push gives the same reference
    status, _, manifest = get(f"{BASE}/manifests/{artifact.tag}")
    assert status == 200 and sha(manifest) == pushed.digest
    layer = json.loads(manifest)["layers"][0]
    status, _, blob = get(f"{BASE}/blobs/{layer['digest']}", "*/*")
    assert status == 200 and sha(blob) == layer["digest"]
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(tmp_path / "out", filter="data")
        else:  # Python 3.11 before 3.11.4
            tar.extractall(tmp_path / "out")
    assert copy_and_hash(tmp_path / "out" / "agent" / NAME / VERSION) == copy_and_hash(version)


def test_subject_and_referrers(tmp_path, capsys):
    """Records what the registry does; either answer is allowed by §8.2 (found by tag)."""
    plain = push(REGISTRY, build(make_package(tmp_path / "a"), NAME, VERSION))
    subject = describe(plain.reference)  # any manifest the registry holds can be a subject
    pushed = push(REGISTRY, build(make_package(tmp_path / "b"), NAME, VERSION, subject))
    status, headers, body = get(
        f"{BASE}/referrers/{subject['digest']}", "application/vnd.oci.image.index.v1+json"
    )
    listed = status == 200 and pushed.digest in body.decode("utf-8", "replace")
    with capsys.disabled():
        print(f"\nsubject kept by the registry: {pushed.subject}")
        print(f"referrers API: HTTP {status}, lists the artifact: {listed}")
    status, _, manifest = get(f"{BASE}/manifests/v{VERSION}.agent")
    assert status == 200 and sha(manifest) == pushed.digest
