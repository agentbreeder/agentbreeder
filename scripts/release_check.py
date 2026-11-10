"""Checks the release workflow runs (spec 5.2, plan S8). Each prints why it failed and exits 1.

python scripts/release_check.py tag v3.0.0a1
    the tag is exactly "v" + agentbreeder.__version__
python scripts/release_check.py npm-window --tag-time 2026-11-12T10:00:00-08:00
    npm/release.json's exclude_newer is at most 7 days before the tag commit (re-run
    `sync_npm.py --write` at release prep) and its window_end is after the tag commit
python scripts/release_check.py compare-sdists A.tar.gz B.tar.gz
    two sdists hold the same members with the same bytes (setuptools does not apply
    SOURCE_DATE_EPOCH to tar entries, so the archive bytes may differ)
python scripts/release_check.py requirements --wheel W --sdist S --out FILE
    the hashed requirements file attached to the GitHub release: the [eval] dependency set
    resolved at release.json's exclude_newer with every file hash, plus agentbreeder itself
    pinned to the built wheel and sdist
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_JSON = ROOT / "npm" / "release.json"
CONSTRAINTS = ROOT / "npm" / "constraints.txt"
_PIN = re.compile(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==([^\s;\\]+)\s*(?:;\s*([^\\]+?))?\s*\\?$")
FRESHNESS = timedelta(days=7)

sys.path.insert(0, str(ROOT / "src"))
from agentbreeder import __version__  # noqa: E402


class CheckFailed(Exception):
    pass


def check_tag(tag: str) -> None:
    if tag != f"v{__version__}":
        raise CheckFailed(f"tag {tag!r} does not match the package version v{__version__}")


def _utc(stamp: str) -> datetime:
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        raise CheckFailed(f"not an ISO 8601 time: {stamp!r}") from None
    if moment.tzinfo is None:
        raise CheckFailed(f"time has no UTC offset: {stamp!r}")
    return moment.astimezone(UTC)


def check_npm_window(tag_time: str, release: dict | None = None) -> None:
    release = release or json.loads(RELEASE_JSON.read_text())
    tagged = _utc(tag_time)
    exclude_newer = _utc(release["exclude_newer"])
    window_end = _utc(release["window_end"])
    if exclude_newer > tagged:
        raise CheckFailed(f"exclude_newer {exclude_newer} is after the tag commit {tagged}")
    if tagged - exclude_newer > FRESHNESS:
        raise CheckFailed(
            f"exclude_newer {exclude_newer} is more than 7 days before the tag commit {tagged}; "
            "run scripts/sync_npm.py --write and commit before tagging"
        )
    if window_end <= tagged:
        raise CheckFailed(f"window_end {window_end} is not after the tag commit {tagged}")


def _members(path: Path) -> dict[str, tuple[bool, bytes]]:
    """name -> (executable, bytes); duplicate names and anything but files/dirs are refused."""
    members: dict[str, tuple[bool, bytes]] = {}
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            if not member.isfile():
                raise CheckFailed(f"{path.name}: {member.name} is not a file or directory")
            if member.name in members:
                raise CheckFailed(f"{path.name}: {member.name} appears twice")
            data = archive.extractfile(member).read()
            members[member.name] = (bool(member.mode & 0o111), data)
    return members


def compare_sdists(first: Path, second: Path) -> None:
    a, b = _members(first), _members(second)
    if a.keys() != b.keys():
        raise CheckFailed(f"sdists differ in members: {sorted(a.keys() ^ b.keys())}")
    changed = sorted(name for name in a if a[name] != b[name])
    if changed:
        raise CheckFailed(f"sdists differ in content or exec bit: {changed}")


def _pins(text: str) -> set[tuple[str, str, str]]:
    """(name, version, marker) for each pinned requirement line, ignoring hashes and comments."""
    pins = set()
    for line in text.splitlines():
        match = _PIN.match(line.strip())
        if match:
            name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
            pins.add((name, match.group(2), (match.group(3) or "").strip()))
    return pins


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_requirements(wheel: Path, sdist: Path, out: Path, run=subprocess.run) -> None:
    wheel_ok = wheel.name.startswith(f"agentbreeder-{__version__}-") and wheel.suffix == ".whl"
    if not wheel_ok or sdist.name != f"agentbreeder-{__version__}.tar.gz":
        names = f"{wheel.name}, {sdist.name}"
        raise CheckFailed(f"{names} are not the agentbreeder {__version__} dists")
    uv = shutil.which("uv")
    if not uv:
        raise CheckFailed("uv is required to resolve the release requirements")
    exclude_newer = json.loads(RELEASE_JSON.read_text())["exclude_newer"]
    command = [
        uv,
        "pip",
        "compile",
        "pyproject.toml",
        "--extra",
        "eval",
        "--universal",
        "--generate-hashes",
        "--only-binary",
        ":all:",
        "--exclude-newer",
        exclude_newer,
        "--no-header",
        "--quiet",
    ]
    try:
        resolved = run(command, cwd=ROOT, check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as error:
        raise CheckFailed(f"uv pip compile failed:\n{error.stderr}") from None
    if "agentbreeder==" in resolved:
        raise CheckFailed("the resolved dependency set unexpectedly pins agentbreeder")
    if _pins(resolved) != _pins(CONSTRAINTS.read_text()):
        raise CheckFailed(
            "the release requirements and npm/constraints.txt resolve different versions; "
            "run scripts/sync_npm.py --write with the same exclude_newer"
        )
    header = (
        f"# agentbreeder {__version__}: dependencies resolved at {exclude_newer} for the [eval]\n"
        "# extra, with every file hash. Install with:\n"
        f"#   uv pip install --require-hashes -r requirements-{__version__}.txt\n"
    )
    pin = (
        f"agentbreeder[eval]=={__version__} \\\n"
        f"    --hash=sha256:{_sha256(wheel)} \\\n"
        f"    --hash=sha256:{_sha256(sdist)}\n"
    )
    out.write_text(header + pin + resolved)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    tag = commands.add_parser("tag")
    tag.add_argument("tag")
    window = commands.add_parser("npm-window")
    window.add_argument("--tag-time", required=True)
    sdists = commands.add_parser("compare-sdists")
    sdists.add_argument("first", type=Path)
    sdists.add_argument("second", type=Path)
    requirements = commands.add_parser("requirements")
    requirements.add_argument("--wheel", type=Path, required=True)
    requirements.add_argument("--sdist", type=Path, required=True)
    requirements.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "tag":
            check_tag(args.tag)
        elif args.command == "npm-window":
            check_npm_window(args.tag_time)
        elif args.command == "compare-sdists":
            compare_sdists(args.first, args.second)
        else:
            write_requirements(args.wheel, args.sdist, args.out)
    except CheckFailed as failure:
        print(f"release_check: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
