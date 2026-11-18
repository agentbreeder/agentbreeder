"""Write or check a runtime version's generated `runtime/modules.json` (#1083 F1).

The file lists the runtime's own top-level module names (`runtime/*.py` and packages under
`runtime/`) and, for every package in its `requirements.lock`, the top-level names it installs.
`agentbreeder build` and `registry publish` use it to refuse a tool whose module would hide one
of them. A runtime version that declares `protocol` in its manifest must ship the file; versions
published before that key are append-only and use the engine's `LEGACY_ADK` table instead.

    python scripts/runtime_modules.py VERSION_DIR --site-packages DIR   # write it
    python scripts/runtime_modules.py VERSION_DIR --check               # fail if stale
    python scripts/runtime_modules.py --check                           # every built-in version

`--site-packages` is the `site-packages` of an environment the lock was installed into (the
runtime image, or `pip install --require-hashes -r requirements.lock --target DIR`). Without it,
the import names come from the engine's table of already-known packages, and a package the table
does not know is an error.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentbreeder import runtime_profile  # noqa: E402
from agentbreeder.strict_yaml import load_file  # noqa: E402

BUILTIN = ROOT / "src" / "agentbreeder" / "builtin_registry" / "runtime"
PINNED = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==")


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def own_modules(version_dir: Path) -> set[str]:
    """Top-level names Python can import from the runtime's own directory."""
    names = set()
    for path in sorted((version_dir / "runtime").iterdir()):
        if path.name == "__pycache__":
            continue
        if path.is_file() and path.suffix == ".py":
            names.add(path.stem)
        elif path.is_dir() and any(path.rglob("*.py")):
            names.add(path.name)
    return names


def locked_packages(version_dir: Path) -> set[str]:
    lock = version_dir / "runtime" / "requirements.lock"
    return {
        normalise(match[1])
        for line in lock.read_text(encoding="utf-8").splitlines()
        if (match := PINNED.match(line))
    }


def installed_imports(site_packages: Path) -> dict[str, set[str]]:
    """Package -> the top-level names its distribution installed, from each `RECORD`."""
    found: dict[str, set[str]] = {}
    for dist in importlib.metadata.distributions(path=[str(site_packages)]):
        names = set()
        for file in dist.files or []:
            first = file.parts[0]
            if first.endswith((".dist-info", ".egg-info", ".data")) or first in (
                "..",
                "bin",
                "__pycache__",
            ):
                continue
            if len(file.parts) == 1:
                stem = first.split(".")[0]
                if first.endswith((".py", ".so", ".pyd")) and stem:
                    names.add(stem)
            else:
                names.add(first)
        found.setdefault(normalise(dist.metadata["Name"]), set()).update(names)
    return found


def expected(version_dir: Path, site_packages: Path | None = None) -> str:
    packages = locked_packages(version_dir)
    known = (
        installed_imports(site_packages)
        if site_packages is not None
        else {name: set(names) for name, names in runtime_profile.LEGACY_ADK.imports.items()}
    )
    missing = sorted(packages - set(known))
    if missing:
        raise SystemExit(
            f"{version_dir}: no import names for {', '.join(missing)}; pass --site-packages "
            "with the lock installed"
        )
    imports = {package: sorted(known[package]) for package in packages}
    return runtime_profile.modules_document(own_modules(version_dir), imports)


def problems(version_dir: Path, site_packages: Path | None = None) -> list[str]:
    """Why a version's `modules.json` is out of step with its files (empty when it is not)."""
    declares = "protocol" in load_file(version_dir / "manifest.yaml")
    path = version_dir / runtime_profile.MODULES_FILE
    if not declares:
        # Published before F1: append-only, so it has no file and never gains one.
        return [f"{path}: a version without `protocol` must not ship one"] if path.exists() else []
    if not path.is_file():
        return [f"{path} is missing: run scripts/runtime_modules.py {version_dir}"]
    text = path.read_text(encoding="utf-8")
    found = runtime_profile.parse_modules(text.encode())
    issues = []
    if found.modules != own_modules(version_dir):
        issues.append(f"{path}: `modules` does not match runtime/")
    if set(found.imports) != locked_packages(version_dir):
        issues.append(f"{path}: `imports` does not list exactly the lock's packages")
    if site_packages is not None and text != expected(version_dir, site_packages):
        issues.append(f"{path} is stale: run scripts/runtime_modules.py {version_dir}")
    if text != runtime_profile.modules_document(set(found.modules), dict(found.imports)):
        issues.append(f"{path} is not in the canonical form")
    return issues


def builtin_versions() -> list[Path]:
    return sorted(path for path in BUILTIN.glob("*/*") if path.is_dir())


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version_dir", nargs="?", type=Path)
    parser.add_argument("--site-packages", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        versions = [args.version_dir] if args.version_dir else builtin_versions()
        issues = [issue for version in versions for issue in problems(version, args.site_packages)]
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1 if issues else 0
    if args.version_dir is None:
        parser.error("VERSION_DIR is required unless --check")
    path = args.version_dir / runtime_profile.MODULES_FILE
    path.write_text(expected(args.version_dir, args.site_packages), encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
