"""Write or check the npm launcher's release files (spec 5.3, plan S7).

    python scripts/sync_npm.py --write [--exclude-newer 2026-11-10T00:00:00Z]
        set npm/package.json's version and npm/release.json from agentbreeder.__version__, and
        re-resolve npm/constraints.txt with `uv pip compile --universal` (needs the network)
    python scripts/sync_npm.py --check
        fail if those files disagree with the package; never touches the network

`exclude_newer` is the time the constraints were resolved at (release prep); `window_end` is 30
days later and bounds only files added later to the pinned agentbreeder version.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
NPM = ROOT / "npm"
PACKAGE_JSON = NPM / "package.json"
RELEASE_JSON = NPM / "release.json"
CONSTRAINTS = NPM / "constraints.txt"
MIN_UV = "0.11.6"
WINDOW = timedelta(days=30)
FILES = ["bin/agentbreeder.js", "release.json", "constraints.txt", "README.md", "LICENSE"]
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_PIN = re.compile(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==([^\s;]+)")

sys.path.insert(0, str(ROOT / "src"))
from agentbreeder import __version__  # noqa: E402
from agentbreeder._version import npm_version  # noqa: E402


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(stamp: str) -> datetime:
    if not _TIMESTAMP.fullmatch(stamp or ""):
        raise ValueError(f"expected YYYY-MM-DDTHH:MM:SSZ, got {stamp!r}")
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def expected_package() -> dict:
    """package.json exactly as it must be: any other key (scripts, dependencies) is an error."""
    return {
        "name": "agentbreeder",
        "version": npm_version(__version__),
        "description": (
            "npx launcher for AgentBreeder (preview): runs the pinned PyPI release with uv's uvx."
        ),
        "license": "Apache-2.0",
        "homepage": "https://www.agentbreeder.io",
        "repository": {
            "type": "git",
            "url": "git+https://github.com/agentbreeder/agentbreeder.git",
            "directory": "npm",
        },
        "bin": {"agentbreeder": "bin/agentbreeder.js"},
        "files": FILES,
        "engines": {"node": ">=18"},
    }


def expected_release(exclude_newer: str) -> dict:
    return {
        "pypi_version": __version__,
        "exclude_newer": exclude_newer,
        "window_end": _stamp(_parse(exclude_newer) + WINDOW),
        "min_uv": MIN_UV,
    }


def _project_requirements() -> list[Requirement]:
    """The runtime dependencies and the [eval] extra, which the launcher installs."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    requirements = project["dependencies"] + project["optional-dependencies"]["eval"]
    return [Requirement(text) for text in requirements]


def _constraint_pins(text: str) -> dict[str, list[str]]:
    pins: dict[str, list[str]] = {}
    for line in text.splitlines():
        match = _PIN.match(line.strip())
        if match:
            pins.setdefault(_normalise(match.group(1)), []).append(match.group(2))
    return pins


def check() -> list[str]:
    errors = []
    try:
        package = json.loads(PACKAGE_JSON.read_text())
        release = json.loads(RELEASE_JSON.read_text())
        constraints = CONSTRAINTS.read_text()
        readme = (NPM / "README.md").read_text()
        same_license = (NPM / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    except (OSError, ValueError) as exc:
        return [f"cannot read the npm release files: {exc}"]

    if package != expected_package():
        errors.append("npm/package.json differs from what sync_npm.py --write produces")
    try:
        want = expected_release(release.get("exclude_newer", ""))
    except ValueError as exc:
        errors.append(f"npm/release.json exclude_newer: {exc}")
    else:
        if release != want:
            errors.append(f"npm/release.json is {release}, expected {want}")
        if f"--exclude-newer {release['exclude_newer']}" not in constraints:
            errors.append("npm/constraints.txt was not resolved at release.json's exclude_newer")
    if "--universal" not in constraints:
        errors.append("npm/constraints.txt was not resolved with --universal")

    resolved = _constraint_pins(constraints)
    if _normalise("agentbreeder") in resolved:
        errors.append("npm/constraints.txt must not pin agentbreeder itself")
    for requirement in _project_requirements():
        name = _normalise(requirement.name)
        found = resolved.get(name)
        if not found or len(found) != 1 or found[0] not in requirement.specifier:
            errors.append(f"npm/constraints.txt pins {name} {found}, wants {requirement}")
    if not same_license:
        errors.append("npm/LICENSE differs from LICENSE")
    for text in (f"agentbreeder@{npm_version(__version__)}", f"uv {MIN_UV}"):
        if text not in readme:
            errors.append(f"npm/README.md does not mention {text}")
    return errors


def write(exclude_newer: str) -> None:
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv is required to resolve npm/constraints.txt")
    release = expected_release(exclude_newer)
    PACKAGE_JSON.write_text(json.dumps(expected_package(), indent=2) + "\n")
    RELEASE_JSON.write_text(json.dumps(release, indent=2) + "\n")
    shutil.copyfile(ROOT / "LICENSE", NPM / "LICENSE")
    subprocess.run(
        [
            uv,
            "pip",
            "compile",
            "pyproject.toml",
            "--extra",
            "eval",
            "--universal",
            "--only-binary",
            ":all:",
            "--exclude-newer",
            exclude_newer,
            "--no-header",
            "--quiet",
            "-o",
            str(CONSTRAINTS.relative_to(ROOT)),
        ],
        cwd=ROOT,
        check=True,
    )
    body = CONSTRAINTS.read_text()
    header = (
        "# Resolved for the npm launcher by scripts/sync_npm.py:\n"
        "#   uv pip compile pyproject.toml --extra eval --universal --only-binary :all:"
        f" --exclude-newer {exclude_newer}\n"
    )
    CONSTRAINTS.write_text(header + body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--exclude-newer", help="UTC timestamp; default: now")
    args = parser.parse_args(argv)
    if args.check:
        errors = check()
        for error in errors:
            print(f"sync_npm: {error}", file=sys.stderr)
        return 1 if errors else 0
    stamp = args.exclude_newer or _stamp(datetime.now(UTC))
    _parse(stamp)
    write(stamp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
