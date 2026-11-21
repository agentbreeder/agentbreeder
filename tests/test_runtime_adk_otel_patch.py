"""The built-in `runtime/adk@1.4.1` and `runtime/adk@1.5.1`: 1.4.0 and 1.5.0 with a dependency
update (opentelemetry 1.45.2, which needs google-adk 2.14.0) and ADK's legacy span schema pinned,
so their spans stay 1.4.0's and 1.5.0's.

Neither version has run end to end (no Docker run yet; `test_runtime_adk_otel_patch_docker.py`
is the opt-in rerun), so neither is in `RUNTIMES_RUN` or `VERIFIED`, and the starter, Northwind
and the console's form stay where they were. Stub builder only: no image is built.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from test_runtime_adk_150 import _build, _without_the_runtime

from agentbreeder import agentspec, newagent, registry, runtime_profile

ADK = registry.BUILTIN_ROOT / "runtime" / "adk"
PATCHES = {"1.4.1": "1.4.0", "1.5.1": "1.5.0"}
# Every pin the update moved, and nothing else (both platforms resolve the same).
MOVED = {
    "google-adk": ("2.12.0", "2.14.0"),
    "opentelemetry-api": ("1.42.1", "1.45.2"),
    "opentelemetry-exporter-http-transport": (None, "0.66b2"),
    "opentelemetry-exporter-otlp-common": (None, "0.66b2"),
    "opentelemetry-exporter-otlp-proto-common": ("1.42.1", "1.45.2"),
    "opentelemetry-exporter-otlp-proto-http": ("1.42.1", "1.45.2"),
    "opentelemetry-proto": ("1.42.1", "1.45.2"),
    "opentelemetry-sdk": ("1.42.1", "1.45.2"),
    "opentelemetry-semantic-conventions": ("0.63b1", "0.66b2"),
}
# The files that differ beyond the version string, and why.
CHANGED = {
    "manifest.yaml",  # google-adk 2.14.0 and one paragraph on the update
    "runtime/requirements.in",  # the update and its recorded exceptions
    "runtime/requirements.lock",  # MOVED
    "runtime/modules.json",  # 1.5.1 only: two new packages, a stray `__pycache__` dropped
    "runtime/telemetry.py",  # pin_adk_schema
    "runtime_tests/test_runtime.py",  # its test
}
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)", re.M)


def _pins(lock: Path) -> dict[str, str]:
    return dict(PIN.findall(lock.read_text()))


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


@pytest.mark.parametrize("new", sorted(PATCHES))
def test_the_new_version_is_listed_and_loads(new):
    refs = {row["ref"] for row in registry.listing([registry.BUILTIN_ROOT])}
    assert f"runtime/adk@{new}" in refs and f"runtime/adk@{PATCHES[new]}" in refs
    profile = runtime_profile.load(ADK / new)
    old = runtime_profile.load(ADK / PATCHES[new])
    assert profile.legacy == old.legacy
    assert profile.capabilities == old.capabilities and profile.limits == old.limits
    assert profile.reserved_tool_names == old.reserved_tool_names


@pytest.mark.parametrize("new", sorted(PATCHES))
def test_the_lock_moves_only_the_opentelemetry_pins_and_google_adk(new):
    old = _pins(ADK / PATCHES[new] / "runtime" / "requirements.lock")
    pins = _pins(ADK / new / "runtime" / "requirements.lock")
    moved = {name: (old.get(name), pins.get(name)) for name in old | pins}
    moved = {name: pair for name, pair in moved.items() if pair[0] != pair[1]}
    assert moved == MOVED
    assert pins["opentelemetry-api"] == "1.45.2"  # fixes both advisories pip-audit reports


def test_both_new_versions_share_one_lock():
    locks = {(ADK / new / "runtime" / "requirements.lock").read_bytes() for new in PATCHES}
    assert len(locks) == 1


@pytest.mark.parametrize("new", sorted(PATCHES))
def test_every_other_file_is_the_old_version_but_for_its_version(new):
    old_files = _files(ADK / PATCHES[new])
    new_files = _files(ADK / new)
    assert set(new_files) == set(old_files)
    old_version, new_version = PATCHES[new].encode(), new.encode()
    differ = {
        name
        for name, data in new_files.items()
        if data.replace(new_version, old_version) != old_files[name]
    }
    assert differ == {name for name in CHANGED if name in old_files}


@pytest.mark.parametrize("new", sorted(PATCHES))
def test_the_runtime_pins_adks_legacy_span_schema(new):
    text = (ADK / new / "runtime" / "telemetry.py").read_text()
    assert 'os.environ["ADK_TELEMETRY_SCHEMA_VERSION_OPT_IN"] = "1"' in text
    assert "\npin_adk_schema()\n" in text  # at import, before any ADK call


def test_the_old_versions_are_untouched():
    recorded = json.loads((Path(__file__).parent / "fixtures" / "builtin_hashes.json").read_text())
    for old in PATCHES.values():
        path = ADK / old
        assert registry.copy_and_hash(path) == recorded[f"runtime/adk@{old}"]
        assert _pins(path / "runtime" / "requirements.lock")["opentelemetry-api"] == "1.42.1"


def test_not_run_end_to_end_so_nothing_moves_to_them():
    for new in PATCHES:
        ref = f"runtime/adk@{new}"
        assert ref not in agentspec.RUNTIMES_RUN
        assert not agentspec.runtime_verified(ref)
    assert newagent.RUNTIME == "runtime/adk@1.4.0"
    northwind = (
        registry.BUILTIN_ROOT.parent / "examples" / "northwind" / "agent.yaml"
    ).read_text()
    assert "runtime: runtime/adk@1.5.0\n" in northwind
    builder = (
        registry.BUILTIN_ROOT.parent / "center" / "static" / "lib" / "builder.js"
    ).read_text()
    assert 'STARTER_RUNTIME = "runtime/adk@1.4.0"' in builder


@pytest.mark.parametrize(("new", "kind"), [("1.4.1", "starter"), ("1.5.1", "northwind")])
def test_an_agent_builds_as_on_the_old_version(tmp_path, identity_env, monkeypatch, new, kind):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    old = _build(tmp_path, identity_env, kind, PATCHES[new])
    built = _build(tmp_path, identity_env, kind, new)
    assert built["config"] == old["config"]
    assert built["template"] == old["template"]
    for part in ("manifest", "candidate"):
        # _without_the_runtime maps 1.5.0 to 1.4.0; map the patch to its own base first.
        data = built[part].replace(new.encode(), PATCHES[new].encode())
        assert _without_the_runtime(data) == _without_the_runtime(old[part]), part


@pytest.mark.skipif(
    os.getenv("AGENTBREEDER_AUDIT_TEST") != "1" or not shutil.which("pip-audit"),
    reason="Opt-in: needs pip-audit and the network (AGENTBREEDER_AUDIT_TEST=1)",
)
@pytest.mark.parametrize("new", sorted(PATCHES))
def test_pip_audit_finds_nothing_in_the_new_lock(new):
    lock = ADK / new / "runtime" / "requirements.lock"
    result = subprocess.run(
        ["pip-audit", "--no-deps", "--disable-pip", "-r", str(lock)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No known vulnerabilities found" in result.stdout + result.stderr
