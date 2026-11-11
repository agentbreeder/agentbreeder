"""Wheel and sdist contents are an exact allowlist (spec 5.1, plan S2).

Builds the sdist, a wheel from that sdist (what PyPI users get) and a wheel straight from the
tree, then compares file lists with the expected set derived from tracked files and
`_content.json`. Two builds with the same SOURCE_DATE_EPOCH must give identical sha256s.
Needs `build` (dev extra) and network access for the isolated build environment.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "agentbreeder"
SHIPPED_TREES = ("builtin_registry", "examples")
FORBIDDEN_PARTS = {"__pycache__", ".agentbreeder", ".secrets", "deploy", ".DS_Store"}

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENTBREEDER_PACKAGING_TEST") != "1",
    reason="Opt-in packaging build test (AGENTBREEDER_PACKAGING_TEST=1; CI runs it)",
)


def _tracked(*paths: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *paths], cwd=ROOT, check=True, capture_output=True
    ).stdout.decode()
    return [path for path in out.split("\0") if path]


# Copied with ignored local files kept (secrets, bytecode) so the dirty-tree case is tested,
# but without build/ (setuptools reuses a stale build/lib), VCS data and unrelated large trees.
_COPY_SKIP = shutil.ignore_patterns(
    ".git", ".venv", "build", "*.egg-info", "website", "node_modules", ".remember", "dist"
)


# Local state an example run leaves behind. Planted in the copy so the leak check means
# something on a clean CI runner too, not only in a checkout that happens to have state.
SENTINELS = (
    "examples/northwind/stack/.secrets/zz-sentinel",
    "examples/northwind/.agentbreeder/zz-sentinel.yaml",
    "examples/northwind/deploy/zz-sentinel.json",
    "examples/northwind/stack/zz-sentinel.log",
    "examples/northwind/registry/prompt/__pycache__/x.cpython-312.pyc",
    "examples/http-agent/__pycache__/server.cpython-312.pyc",
    "builtin_registry/.DS_Store",
)


def _source_copy(tmp: Path) -> Path:
    source = tmp / "source"
    shutil.copytree(ROOT, source, ignore=_COPY_SKIP, symlinks=True)
    for relative in SENTINELS:
        sentinel = source / "src" / "agentbreeder" / relative
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("sentinel: must never be packaged\n")
    return source


def _build(source: Path, outdir: Path, *flags: str) -> None:
    env = {**os.environ, "SOURCE_DATE_EPOCH": "1762646400"}
    subprocess.run(
        [sys.executable, "-m", "build", *flags, "--outdir", str(outdir), str(source)],
        cwd=source,
        env=env,
        check=True,
        capture_output=True,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expected_wheel() -> set[str]:
    manifest = json.loads((PACKAGE / "_content.json").read_text())["files"]
    expected = {f"agentbreeder/{path}" for path in manifest}
    expected |= {"agentbreeder/_content.json", "agentbreeder/policy/cedar_schema.json"}
    expected |= {f"agentbreeder/center/static/{n}" for n in ("index.html", "app.js", "style.css")}
    for path in _tracked("src/agentbreeder"):
        relative = Path(path).relative_to("src/agentbreeder")
        if relative.parts[0] in SHIPPED_TREES or relative.suffix != ".py":
            continue
        expected.add(f"agentbreeder/{relative.as_posix()}")
    return expected


def _wheel_files(wheel: Path) -> set[str]:
    names = zipfile.ZipFile(wheel).namelist()
    return {name for name in names if ".dist-info/" not in name}


def _assert_clean(names: set[str]) -> None:
    for name in names:
        parts = set(Path(name).parts)
        assert not parts & FORBIDDEN_PARTS, name
        assert not name.endswith((".pyc", ".pyo")), name


@pytest.fixture(scope="module")
def builds(tmp_path_factory):
    source = _source_copy(tmp_path_factory.mktemp("copy"))
    first = tmp_path_factory.mktemp("first")
    second = tmp_path_factory.mktemp("second")
    tree = tmp_path_factory.mktemp("tree")
    _build(source, first)  # sdist, then the wheel built from that sdist
    _build(source, second)
    _build(source, tree, "--wheel")  # wheel straight from the (dirty) source tree
    return first, second, tree


def test_wheel_from_sdist_is_exactly_the_allowlist(builds):
    first, _, _ = builds
    (wheel,) = first.glob("*.whl")
    names = _wheel_files(wheel)
    _assert_clean(names)
    assert names == _expected_wheel()


def test_no_planted_local_state_ships(builds):
    first, _, tree = builds
    (sdist,) = first.glob("*.tar.gz")
    with tarfile.open(sdist) as archive:
        packed = {Path(*Path(name).parts[1:]).as_posix() for name in archive.getnames()}
    for wheel in (*first.glob("*.whl"), *tree.glob("*.whl")):
        names = _wheel_files(wheel)
        for relative in SENTINELS:
            assert f"agentbreeder/{relative}" not in names, (wheel.name, relative)
    for relative in SENTINELS:
        assert f"src/agentbreeder/{relative}" not in packed, relative


def test_built_metadata_carries_the_package_version(builds):
    from agentbreeder import __version__

    first, _, _ = builds
    (wheel,) = first.glob("*.whl")
    assert wheel.name.startswith(f"agentbreeder-{__version__}-")
    with zipfile.ZipFile(wheel) as archive:
        (metadata,) = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        assert f"Version: {__version__}\n" in archive.read(metadata).decode()


def test_wheel_from_tree_matches_wheel_from_sdist(builds):
    first, _, tree = builds
    (from_sdist,) = first.glob("*.whl")
    (from_tree,) = tree.glob("*.whl")
    assert _wheel_files(from_tree) == _wheel_files(from_sdist)


def test_shipped_files_match_the_content_manifest(builds):
    first, _, _ = builds
    (wheel,) = first.glob("*.whl")
    manifest = json.loads((PACKAGE / "_content.json").read_text())["files"]
    with zipfile.ZipFile(wheel) as archive:
        for path, entry in manifest.items():
            info = archive.getinfo(f"agentbreeder/{path}")
            assert hashlib.sha256(archive.read(info)).hexdigest() == entry["sha256"], path
            executable = bool((info.external_attr >> 16) & 0o111)
            assert executable == entry["exec"], path


def test_sdist_ships_tracked_files_only(builds):
    first, _, _ = builds
    (sdist,) = first.glob("*.tar.gz")
    with tarfile.open(sdist) as archive:
        names = {
            Path(*Path(member.name).parts[1:]).as_posix()
            for member in archive.getmembers()
            if member.isfile()
        }
    _assert_clean(names)
    tracked = set(_tracked(".")) | set(_untracked_not_ignored())
    generated = {"PKG-INFO", "setup.cfg"}
    untracked = {
        name
        for name in names - tracked - generated
        if not name.startswith("src/agentbreeder.egg-info/")
    }
    assert not untracked
    shipped = {f"src/agentbreeder/{path}" for path in _expected_wheel_paths()}
    assert shipped <= names


def _untracked_not_ignored() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode()
    return [path for path in out.split("\0") if path]


def _expected_wheel_paths() -> set[str]:
    return {name.removeprefix("agentbreeder/") for name in _expected_wheel()}


def test_northwind_dotfile_and_nested_stack_files_ship(builds):
    first, _, _ = builds
    (wheel,) = first.glob("*.whl")
    names = _wheel_files(wheel)
    base = "agentbreeder/examples/northwind/stack/"
    assert {
        base + ".dockerignore",
        base + "db/commerce.sql",
        base + "litellm/config.yaml",
    } <= names


def _sdist_contents(sdist: Path) -> dict[str, str]:
    with tarfile.open(sdist) as archive:
        return {
            member.name: hashlib.sha256(archive.extractfile(member).read()).hexdigest()
            for member in archive.getmembers()
            if member.isfile()
        }


def test_two_builds_are_identical(builds):
    """Wheels are byte-identical. setuptools does not apply SOURCE_DATE_EPOCH to sdist tar
    entries, so sdists are compared by content (same files, same bytes), not archive bytes."""
    first, second, _ = builds
    (wheel,) = first.glob("*.whl")
    assert _sha256(wheel) == _sha256(second / wheel.name)
    (sdist,) = first.glob("*.tar.gz")
    assert _sdist_contents(sdist) == _sdist_contents(second / sdist.name)
