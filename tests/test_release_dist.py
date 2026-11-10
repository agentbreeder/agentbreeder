"""The release job's own artifacts are the allowlist (spec 5.2 step 2, plan S8).

tests/test_packaging.py checks builds it makes itself. The release workflow builds with its
hashed build dependencies, so it points this module at that `dist/` directory:

    AGENTBREEDER_RELEASE_DIST=dist python -m pytest tests/test_release_dist.py
"""

import hashlib
import importlib.util
import json
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIST = os.environ.get("AGENTBREEDER_RELEASE_DIST")

pytestmark = pytest.mark.skipif(not DIST, reason="set AGENTBREEDER_RELEASE_DIST to a dist dir")


def _packaging():
    path = ROOT / "tests" / "test_packaging.py"
    spec = importlib.util.spec_from_file_location("_packaging", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


packaging = _packaging()


@pytest.fixture(scope="module")
def dists():
    from agentbreeder import __version__

    directory = Path(DIST).resolve()
    (wheel,) = directory.glob(f"agentbreeder-{__version__}-*.whl")
    (sdist,) = directory.glob(f"agentbreeder-{__version__}.tar.gz")
    return wheel, sdist


def test_release_wheel_is_exactly_the_allowlist(dists):
    wheel, _ = dists
    names = packaging._wheel_files(wheel)
    packaging._assert_clean(names)
    assert names == packaging._expected_wheel()


def test_release_wheel_matches_the_content_manifest(dists):
    wheel, _ = dists
    manifest = json.loads((packaging.PACKAGE / "_content.json").read_text())["files"]
    with zipfile.ZipFile(wheel) as archive:
        for path, entry in manifest.items():
            info = archive.getinfo(f"agentbreeder/{path}")
            assert hashlib.sha256(archive.read(info)).hexdigest() == entry["sha256"], path
            assert bool((info.external_attr >> 16) & 0o111) == entry["exec"], path


def test_release_sdist_ships_tracked_files_only(dists):
    _, sdist = dists
    with tarfile.open(sdist) as archive:
        names = {
            Path(*Path(member.name).parts[1:]).as_posix()
            for member in archive.getmembers()
            if member.isfile()
        }
    packaging._assert_clean(names)
    generated = {"PKG-INFO", "setup.cfg"}
    extra = {
        name
        for name in names - set(packaging._tracked(".")) - generated
        if not name.startswith("src/agentbreeder.egg-info/")
    }
    assert not extra
    assert {f"src/agentbreeder/{p}" for p in packaging._expected_wheel_paths()} <= names


def test_release_metadata_carries_the_version(dists):
    from agentbreeder import __version__

    wheel, _ = dists
    with zipfile.ZipFile(wheel) as archive:
        (metadata,) = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        assert f"Version: {__version__}\n" in archive.read(metadata).decode()
