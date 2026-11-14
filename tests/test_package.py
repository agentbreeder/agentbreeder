"""Agent package format (M13 #763, spec §5, §12): schema, assemble, load, lock redaction."""

from __future__ import annotations

import getpass
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from agentbreeder import lock as lock_module
from agentbreeder import package
from agentbreeder.pack import pack
from agentbreeder.package import EXTERNAL, Package, PackageError
from agentbreeder.registry import copy_and_hash

AGENT_YAML = b"name: support-bot\nruntime: runtime/adk@1.1.0\n"


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def lock_bytes(entries: dict | None = None) -> bytes:
    entries = entries or {
        "runtime/adk@1.1.0": {"sha256": "sha256:" + "1" * 64, "source": "builtin"},
        "prompt/support@1.0.0": {"sha256": "sha256:" + "2" * 64, "source": "registry"},
        "path:prompts/extra.md": {"sha256": "sha256:" + "3" * 64, "source": "prompts/extra.md"},
        "tool/shared@2.0.0": {"sha256": "sha256:" + "4" * 64, "source": "../../shared/registry"},
        "tool/abs@1.0.0": {"sha256": "sha256:" + "5" * 64, "source": "/Users/someone/reg"},
    }
    return (json.dumps({"lock_version": 1, "entries": entries}, indent=2) + "\n").encode()


def record_data(lock: bytes | None = None, **changes) -> dict:
    data = {
        "package_version": 1,
        "agent": {"name": "support-bot", "version": "1.4.0", "runtime": "runtime/adk@1.1.0"},
        "image": {"id": "sha256:" + "a" * 64, "platform": "linux/arm64"},
        "inputs": {
            "agent_yaml_sha256": sha(AGENT_YAML),
            "lock_sha256": sha(lock if lock is not None else lock_bytes()),
            "lock_version": 1,
            "sources": {},
        },
        "evals": {
            "ref": "eval/support-smoke@1.0.0",
            "set_sha256": "sha256:" + "b" * 64,
            "threshold": 1.0,
            "retries": 1,
            "run_id": "20261120-180411-abc123",
            "passed": 12,
            "cases": 12,
            "environment": "dev",
        },
        "deploy": {
            "port": 8080,
            "health_path": "/health",
            "aliases": ["chat-default"],
            "budget_usd": None,
            "rpm": None,
            "mcp_access": [],
        },
        "released": {"at": "2026-11-20T18:04:11Z", "agentbreeder": "3.0.0a3.dev0"},
    }
    for path, value in changes.items():
        *parents, last = path.split(".")
        target = data
        for part in parents:
            target = target[part]
        if value is _DELETE:
            del target[last]
        else:
            target[last] = value
    return data


_DELETE = object()


def record(**changes) -> Package:
    return package.parse(json.dumps(record_data(**changes)).encode())


# -- schema --------------------------------------------------------------------------------------


def test_parse_round_trips_canonically():
    parsed = record()
    assert parsed.ref == "agent/support-bot@1.4.0"
    assert package.parse(parsed.to_bytes()) == parsed
    assert parsed.to_bytes() == record().to_bytes()
    assert list(json.loads(parsed.to_bytes())) == [
        "package_version",
        "agent",
        "image",
        "inputs",
        "evals",
        "deploy",
        "released",
    ]


@pytest.mark.parametrize(
    "path,value",
    [
        ("extra", 1),
        ("deploy.identity", {"provider": "static", "ref": "x", "environment": "dev"}),
        ("deploy.secrets", {"A": "b"}),
        ("deploy.policy", {}),
        ("deploy.capabilities", {}),
        ("deploy.requires", {}),
        ("deploy.target", "local"),
        ("deploy.image", "x@sha256:" + "a" * 64),
        ("deploy.gateway", "litellm"),
        ("image.registry", "127.0.0.1:5001/x"),
        ("inputs.sources", {"x": {}}),
        ("inputs.lock_version", 2),
        ("image.id", "sha256:short"),
        ("agent.name", "Bad_Name"),
        ("agent.version", "1.4"),
        ("agent.version", "01.4.0"),
        ("evals.passed", 13),
        ("evals.threshold", 1.5),
        ("evals.environment", "Prod!"),
        ("deploy.aliases", ["a", "a"]),
        ("deploy.aliases", []),
        ("deploy.health_path", "health"),
        ("deploy.budget_usd", _DELETE),
        ("released.at", "2026-11-20 18:04:11"),
        ("agent.version", 140),
    ],
)
def test_forged_or_malformed_fields_are_refused(path, value):
    with pytest.raises(PackageError) as raised:
        record(**{path: value})
    assert raised.value.code == "PACKAGE_INVALID"


def test_mcp_access_uses_the_deploy_rules():
    entry = {"ref": "mcp/github@1.0.0", "tools": ["search"]}
    assert record(**{"deploy.mcp_access": [entry]}).deploy.mcp_access[0].ref == "mcp/github@1.0.0"
    with pytest.raises(PackageError):
        record(**{"deploy.mcp_access": [entry, {**entry, "ref": "mcp/github@1.0.1"}]})
    with pytest.raises(PackageError):
        record(**{"deploy.mcp_access": [{"ref": "tool/x@1.0.0", "tools": ["a"]}]})


def test_newer_package_version_says_upgrade():
    with pytest.raises(PackageError, match="newer AgentBreeder"):
        record(package_version=2)


@pytest.mark.parametrize(
    "data",
    [
        b"[]",
        b"not json",
        b"\xff",
        b'{"package_version": 1, "package_version": 1}',
        b" " * (package.PACKAGE_LIMIT + 1),
    ],
)
def test_parse_refuses_bad_documents(data):
    with pytest.raises(PackageError) as raised:
        package.parse(data)
    assert raised.value.code == "PACKAGE_INVALID"


def test_error_messages_do_not_echo_values():
    secret = "sk_live_" + "z" * 20
    with pytest.raises(PackageError) as raised:
        record(**{"evals.run_id": secret})
    assert secret not in str(raised.value)
    assert "evals.run_id" in str(raised.value)
    forged = record_data()
    forged["deploy"]["ignore previous instructions"] = 1
    with pytest.raises(PackageError) as raised:
        package.parse(json.dumps(forged).encode())
    assert "ignore" not in str(raised.value)
    assert "deploy.?" in str(raised.value)


# -- lock redaction ------------------------------------------------------------------------------


def test_redaction_keeps_safe_labels_and_hashes():
    redacted = json.loads(package.redact_lock(lock_bytes()))
    entries = redacted["entries"]
    assert redacted["lock_version"] == 1
    assert list(entries) == sorted(entries)
    assert entries["runtime/adk@1.1.0"]["source"] == "builtin"
    assert entries["prompt/support@1.0.0"]["source"] == "registry"
    assert entries["path:prompts/extra.md"]["source"] == "prompts/extra.md"
    assert entries["tool/shared@2.0.0"] == {"sha256": "sha256:" + "4" * 64, "source": EXTERNAL}
    assert entries["tool/abs@1.0.0"]["source"] == EXTERNAL


@pytest.mark.parametrize(
    "label,safe",
    [
        ("builtin", True),
        ("registry", True),
        ("remote:acme", True),
        (".", True),
        ("vendor/registry", True),
        ("remote:../x", False),
        ("remote:", False),
        ("../registry", False),
        ("vendor/../../x", False),
        ("/abs", False),
        ("~/reg", False),
        ("C:\\reg", False),
        ("a\\b", False),
        (".hidden", False),
        ("", False),
        (None, False),
    ],
)
def test_safe_label(label, safe):
    assert package.safe_label(label) is safe


@pytest.mark.parametrize(
    "body",
    [
        {"lock_version": 2, "entries": {}},
        {"lock_version": 1},
        {"lock_version": 1, "entries": [], "extra": 1},
        {"lock_version": 1, "entries": []},
        {
            "lock_version": 1,
            "entries": {"runtime/adk@1.1.0": {"sha256": "x", "source": "builtin"}},
        },
        {"lock_version": 1, "entries": {"runtime/adk@1.1.0": {"sha256": "sha256:" + "1" * 64}}},
        {
            "lock_version": 1,
            "entries": {"../x": {"sha256": "sha256:" + "1" * 64, "source": "builtin"}},
        },
        {
            "lock_version": 1,
            "entries": {"path:../x": {"sha256": "sha256:" + "1" * 64, "source": "x"}},
        },
    ],
)
def test_redaction_refuses_unexpected_locks(body):
    with pytest.raises(PackageError) as raised:
        package.redact_lock(json.dumps(body).encode())
    assert raised.value.code == "PACKAGE_INVALID"


# -- assemble and load ---------------------------------------------------------------------------


def assembled(tmp_path: Path) -> tuple[Path, str]:
    staging = tmp_path / "stage" / "1.4.0"
    staging.parent.mkdir(parents=True)
    digest = package.assemble(staging, record(), AGENT_YAML, lock_bytes(), "Support bot")
    return staging, digest


def test_assemble_writes_the_four_files(tmp_path):
    staging, digest = assembled(tmp_path)
    assert sorted(p.name for p in staging.iterdir()) == sorted(package.FILES)
    assert staging.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o644 for p in staging.iterdir())
    assert (staging / "agent.yaml").read_bytes() == AGENT_YAML
    assert (staging / "package.json").read_bytes() == record().to_bytes()
    assert (staging / "agent.lock").read_bytes() == package.redact_lock(lock_bytes())
    assert digest == copy_and_hash(staging)
    loaded = package.load(staging, "support-bot", "1.4.0")
    assert loaded == record()


def test_assemble_is_deterministic(tmp_path):
    _, first = assembled(tmp_path / "a")
    _, second = assembled(tmp_path / "b")
    assert first == second


def test_assemble_refuses_inputs_that_differ_from_the_build(tmp_path):
    with pytest.raises(PackageError) as raised:
        package.assemble(tmp_path / "x", record(), AGENT_YAML + b"#", lock_bytes())
    assert raised.value.code == "PACKAGE_INPUTS_CHANGED"
    with pytest.raises(PackageError) as raised:
        package.assemble(tmp_path / "x", record(), AGENT_YAML, lock_bytes() + b" ")
    assert raised.value.code == "PACKAGE_INPUTS_CHANGED"
    assert not (tmp_path / "x").exists()
    with pytest.raises(PackageError):
        package.assemble(tmp_path / "x", record(), AGENT_YAML, lock_bytes(), "d" * 501)


def test_assemble_refuses_an_existing_staging_dir(tmp_path):
    (tmp_path / "x").mkdir()
    with pytest.raises(FileExistsError):
        package.assemble(tmp_path / "x", record(), AGENT_YAML, lock_bytes())


def test_load_refuses_mismatches_and_extra_files(tmp_path):
    staging, _ = assembled(tmp_path)
    with pytest.raises(PackageError, match="manifest.yaml"):
        package.load(staging, "support-bot", "1.4.1")
    with pytest.raises(PackageError, match="manifest.yaml"):
        package.load(staging, "other", "1.4.0")
    (staging / "notes.txt").write_text("x")
    with pytest.raises(PackageError, match="exactly"):
        package.load(staging, "support-bot", "1.4.0")
    (staging / "notes.txt").unlink()
    (staging / "manifest.yaml").write_bytes(package.manifest_bytes("support-bot", "1.4.1", ""))
    with pytest.raises(PackageError, match="package.json"):
        package.load(staging, "support-bot", "1.4.1")
    (staging / "manifest.yaml").write_text("kind: agent\nkind: tool\n")
    with pytest.raises(PackageError, match="not valid"):
        package.load(staging, "support-bot", "1.4.0")


def test_load_refuses_symlinked_files_and_dirs(tmp_path):
    staging, _ = assembled(tmp_path)
    real = tmp_path / "real.json"
    real.write_bytes((staging / "package.json").read_bytes())
    (staging / "package.json").unlink()
    (staging / "package.json").symlink_to(real)
    with pytest.raises(PackageError, match="package.json"):
        package.load(staging, "support-bot", "1.4.0")
    link = tmp_path / "link"
    link.symlink_to(staging)
    with pytest.raises(PackageError, match="real directory"):
        package.load(link, "support-bot", "1.4.0")
    with pytest.raises(PackageError, match="real directory"):
        package.load(tmp_path / "missing", "support-bot", "1.4.0")


def test_packaged_lock_is_never_a_lock_input(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("Lock.load must not read a package")

    monkeypatch.setattr(lock_module.Lock, "load", classmethod(refuse))
    staging, _ = assembled(tmp_path)
    package.load(staging, "support-bot", "1.4.0")


def test_no_path_or_user_leaks_in_the_package_or_its_tar(tmp_path):
    staging, digest = assembled(tmp_path)
    archive = pack(staging, "agent", "support-bot", "1.4.0")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        blobs = [tar.extractfile(m).read() for m in tar.getmembers() if m.isreg()]
        names = " ".join(m.name for m in tar.getmembers())
    blobs.append(names.encode())
    forbidden = [b"/Users/", b"/home/", b"..", b"~", os.path.expanduser("~").encode()]
    user = getpass.getuser().encode()
    for blob in blobs:
        for needle in forbidden:
            assert needle not in blob, needle
        if len(user) >= 4:
            assert user not in blob


def test_size_caps(tmp_path, monkeypatch):
    with pytest.raises(PackageError, match="1 MiB"):
        package.redact_lock(b" " * (package.LOCK_LIMIT + 1))
    staging, _ = assembled(tmp_path)
    (staging / "manifest.yaml").write_bytes(b"#" * (package.PACKAGE_LIMIT + 1))
    with pytest.raises(PackageError, match="too large"):
        package.load(staging, "support-bot", "1.4.0")
    monkeypatch.setattr(package, "PACKAGE_LIMIT", 10)
    with pytest.raises(PackageError, match="64 KiB"):
        package.assemble(tmp_path / "y", record(), AGENT_YAML, lock_bytes())
    assert not (tmp_path / "y").exists()


def test_load_refuses_a_directory_named_like_a_file(tmp_path):
    staging, _ = assembled(tmp_path)
    (staging / "agent.yaml").unlink()
    (staging / "agent.yaml").mkdir()
    (staging / "package.json").unlink()
    (staging / "package.json").mkdir()
    with pytest.raises(PackageError, match="package.json"):
        package.load(staging, "support-bot", "1.4.0")


@pytest.mark.parametrize("value", [True, 1.0])
def test_format_versions_must_be_integers(value):
    with pytest.raises(PackageError, match="newer AgentBreeder"):
        record(package_version=value)
    with pytest.raises(PackageError, match="inputs.lock_version"):
        record(**{"inputs.lock_version": value})
    with pytest.raises(PackageError, match="lock_version 1"):
        package.redact_lock(json.dumps({"lock_version": value, "entries": {}}).encode())


def test_oversized_numbers_are_refused():
    with pytest.raises(PackageError, match="oversized number"):
        package.parse(b'{"package_version": 1' + b"0" * 5000 + b"}")


@pytest.mark.parametrize("path", ["prompts/my prompt.md", ".prompts/x.md", "prompts/é.md"])
def test_any_project_path_key_is_kept(path):
    body = lock_bytes({f"path:{path}": {"sha256": "sha256:" + "6" * 64, "source": path}})
    entries = json.loads(package.redact_lock(body))["entries"]
    assert entries == {f"path:{path}": {"sha256": "sha256:" + "6" * 64, "source": path}}


@pytest.mark.parametrize(
    "key",
    [
        "path:",
        "path:/abs",
        "path:~/x",
        "path:a\\b",
        "path:a/../../b",
        "path:a\nb",
        "agent/x@1.0.0",
    ],
)
def test_bad_lock_keys_are_refused(key):
    body = lock_bytes({key: {"sha256": "sha256:" + "6" * 64, "source": "registry"}})
    with pytest.raises(PackageError, match="not a ref"):
        package.redact_lock(body)


def test_a_path_key_does_not_launder_another_source():
    body = lock_bytes({"path:x.md": {"sha256": "sha256:" + "6" * 64, "source": "/Users/a/x.md"}})
    assert json.loads(package.redact_lock(body))["entries"]["path:x.md"]["source"] == EXTERNAL


def test_a_failed_run_is_not_a_package():
    with pytest.raises(PackageError, match="evals"):
        record(**{"evals.passed": 11})
    assert record(**{"evals.passed": 11, "evals.threshold": 0.9}).evals.passed == 11


def test_the_record_cannot_change_after_parsing():
    parsed = record()
    with pytest.raises((TypeError, AttributeError, ValueError)):
        parsed.deploy.aliases.append("x")


def test_load_checks_its_arguments_and_the_agent_yaml_hash(tmp_path):
    staging, _ = assembled(tmp_path)
    with pytest.raises(PackageError, match="agent/<name>@X.Y.Z"):
        package.load(staging, "support-bot", "1.4")
    with pytest.raises(PackageError, match="agent/<name>@X.Y.Z"):
        package.load(staging, "../x", "1.4.0")
    (staging / "agent.yaml").write_bytes(AGENT_YAML + b"# edited\n")
    with pytest.raises(PackageError, match="agent.yaml differs"):
        package.load(staging, "support-bot", "1.4.0")


def test_a_record_validates_from_python_dicts():
    built = Package.model_validate(record_data())
    assert built == record()
    assert built.deploy.aliases == ("chat-default",)
