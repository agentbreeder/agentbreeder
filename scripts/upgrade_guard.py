"""The README's claim: until 3.x has a final release, pip installs 2.7.3 (spec r9, plan S8).

    python scripts/upgrade_guard.py --wheel dist/agentbreeder-3.0.0a1-py3-none-any.whl
        before publishing: a local index holding 2.7.3 (downloaded from PyPI) and the built 3.x
        pre-release; a bare install and an upgrade from 2.7.3 must both resolve to 2.7.3, and an
        exact pin must resolve to the pre-release (so the index really offers it)
    python scripts/upgrade_guard.py
        after publishing: the same two checks against PyPI itself

Every install is --no-deps: only agentbreeder's own resolution is in question, and 2.x's
dependency tree is not installed.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

LEGACY = "2.7.3"


class GuardFailed(Exception):
    pass


def _pip(env: Path, *args: str) -> None:
    command = [str(env / "bin" / "python"), "-m", "pip", "--disable-pip-version-check", *args]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise GuardFailed(f"pip {' '.join(args)} failed:\n{result.stdout}{result.stderr}")


def _installed(env: Path) -> str:
    code = "from importlib.metadata import version; print(version('agentbreeder'))"
    return subprocess.run(
        [str(env / "bin" / "python"), "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()


def _fresh(path: Path) -> Path:
    venv.EnvBuilder(with_pip=True, clear=True).create(path)
    return path


def run(work: Path, index_args: list[str], prerelease: str | None) -> list[str]:
    report = []
    bare = _fresh(work / "bare")
    _pip(bare, "install", "--no-deps", *index_args, "agentbreeder")
    if (found := _installed(bare)) != LEGACY:
        raise GuardFailed(f"a bare `pip install agentbreeder` installed {found}, not {LEGACY}")
    report.append(f"bare install: {LEGACY}")

    upgrade = _fresh(work / "upgrade")
    _pip(upgrade, "install", "--no-deps", *index_args, f"agentbreeder=={LEGACY}")
    _pip(upgrade, "install", "--no-deps", "--upgrade", *index_args, "agentbreeder")
    if (found := _installed(upgrade)) != LEGACY:
        raise GuardFailed(f"`pip install -U agentbreeder` from {LEGACY} installed {found}")
    report.append(f"upgrade from {LEGACY}: {LEGACY}")

    if prerelease:
        pinned = _fresh(work / "pinned")
        _pip(pinned, "install", "--no-deps", *index_args, f"agentbreeder=={prerelease}")
        if (found := _installed(pinned)) != prerelease:
            raise GuardFailed(f"the exact pin installed {found}, not {prerelease}")
        report.append(f"exact pin: {prerelease} (the index offers the pre-release)")
    return report


def local_index(work: Path, wheel: Path) -> list[str]:
    index = work / "index"
    index.mkdir()
    _download = [sys.executable, "-m", "pip", "download", "--disable-pip-version-check"]
    result = subprocess.run(
        [
            *_download,
            "--no-deps",
            "--only-binary=:all:",
            "-d",
            str(index),
            f"agentbreeder=={LEGACY}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GuardFailed(f"downloading {LEGACY} failed:\n{result.stdout}{result.stderr}")
    shutil.copy2(wheel, index / wheel.name)
    return ["--no-index", "--find-links", str(index)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wheel", type=Path, help="the built pre-release wheel (local index)")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="upgrade-guard-") as scratch:
        work = Path(scratch)
        try:
            if args.wheel:
                version = args.wheel.name.split("-")[1]
                report = run(work, local_index(work, args.wheel), version)
            else:
                report = run(work, [], None)
        except GuardFailed as failure:
            print(f"upgrade_guard: {failure}", file=sys.stderr)
            return 1
    print("\n".join(f"upgrade_guard: {line}" for line in report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
