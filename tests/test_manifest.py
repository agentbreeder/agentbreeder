from pathlib import Path

import pytest

from agentbreeder.manifest import ManifestError, load_manifest

VALID = (
    "schema_version: 1\nname: support-agent\nimage: registry.example.com/support@sha256:"
    + "a" * 64
    + "\n"
)


def write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "agent.yaml"
    p.write_text(content)
    return p


def test_minimal_manifest(tmp_path):
    m = load_manifest(write(tmp_path, VALID))
    assert m.name == "support-agent"
    assert m.target == "local"
    assert m.port == 8080
    assert m.health_path == "/health"


@pytest.mark.parametrize(
    "extra",
    [
        "name: duplicate\n",
        "unknown: true\n",
        'port: "8080"\n',
        "port: 0\n",
        "target: aws\n",
        "secrets:\n  KEY: plaintext\n",
        "secrets:\n  BAD-NAME: secret://model-key\n",
        "health_path: https://example.com\n",
        "health_path: /health?token=secret\n",
        "schema_version: true\n",
    ],
)
def test_reject_invalid_manifest(tmp_path, extra):
    with pytest.raises(ManifestError):
        load_manifest(write(tmp_path, VALID + extra))


def test_secret_reference(tmp_path):
    m = load_manifest(write(tmp_path, VALID + "secrets:\n  API_KEY: secret://model-key\n"))
    assert m.secrets == {"API_KEY": "secret://model-key"}


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        "null",
        "name: [",
        "---\n{}\n---\n{}",
        "!!python/object/apply:os.system [echo invalid]",
    ],
)
def test_reject_invalid_yaml(tmp_path, content):
    with pytest.raises(ManifestError):
        load_manifest(write(tmp_path, content))


def test_reject_mutable_image(tmp_path):
    with pytest.raises(ManifestError):
        load_manifest(write(tmp_path, VALID.replace("@sha256:" + "a" * 64, ":latest")))


@pytest.mark.parametrize(
    "repository",
    [
        "https://registry.example.com/support",
        "registry.example.com//support",
        "repo/../name",
        "host:99999/repo",
    ],
)
def test_reject_invalid_image_name(tmp_path, repository):
    with pytest.raises(ManifestError):
        load_manifest(write(tmp_path, VALID.replace("registry.example.com/support", repository)))


@pytest.mark.parametrize("repository", ["localhost:5000/team/agent", "agent", "team/agent:v1"])
def test_valid_image_names(tmp_path, repository):
    assert load_manifest(
        write(tmp_path, VALID.replace("registry.example.com/support", repository))
    )


def test_invalid_yaml_timestamp_is_safe(tmp_path):
    with pytest.raises(ManifestError):
        load_manifest(write(tmp_path, VALID + "unknown: 2026-99-99\n"))


def test_reject_large_file(tmp_path):
    with pytest.raises(ManifestError, match="64 KiB"):
        load_manifest(write(tmp_path, "a" * 65537))


def test_missing_file(tmp_path):
    with pytest.raises(ManifestError):
        load_manifest(tmp_path / "missing.yaml")


@pytest.mark.parametrize("extra", ["secrets: &secret {}\n", "secrets: *secret\n"])
def test_reject_aliases_and_anchors(tmp_path, extra):
    with pytest.raises(ManifestError):
        load_manifest(write(tmp_path, VALID + extra))


def test_reject_invalid_utf8(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_bytes(b"\xff\xfe")
    with pytest.raises(ManifestError):
        load_manifest(p)


def test_error_does_not_echo_secret(tmp_path):
    with pytest.raises(ManifestError) as error:
        load_manifest(write(tmp_path, VALID + "secrets:\n  API_KEY: very-sensitive-value\n"))
    assert "very-sensitive-value" not in str(error.value)


def test_digest_ignores_unset_model_access():
    """Manifests deployed before model_access existed keep their digest (no NAME_CONFLICT)."""
    import hashlib
    import json

    from agentbreeder.manifest import Manifest

    data = {"schema_version": 1, "name": "x", "image": "sha256:" + "a" * 64}
    manifest = Manifest.model_validate(data)
    old = {k: v for k, v in json.loads(manifest.model_dump_json()).items() if k != "model_access"}
    legacy = hashlib.sha256(json.dumps(old, separators=(",", ":")).encode()).hexdigest()
    assert manifest.digest() == legacy
