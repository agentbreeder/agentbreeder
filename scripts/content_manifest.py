"""Write or check src/agentbreeder/_content.json (spec 5.1, plan S2).

The manifest lists every shipped non-module file under the built-in registry and the shipped
examples, with its sha256 and executable bit, taken from tracked files only (`git ls-files`).
The installed package checks itself against it: a corruption check, not tamper-evidence.

    python scripts/content_manifest.py           # rewrite the manifest
    python scripts/content_manifest.py --check   # fail if stale or if untracked files exist
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "agentbreeder"
TREES = ("builtin_registry", "examples")
MANIFEST = PACKAGE / "_content.json"


def _git(*args: str) -> list[str]:
    out = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    return [line for line in out.split("\0") if line]


def build() -> dict:
    files = {}
    trees = [f"src/agentbreeder/{tree}" for tree in TREES]
    # `-s` gives "<mode> <object> <stage>\t<path>"; the index mode is the exec bit we ship.
    for entry in _git("ls-files", "-s", "-z", "--", *trees):
        meta, path = entry.split("\t", 1)
        mode = meta.split()[0]
        if mode not in {"100644", "100755"}:
            raise SystemExit(f"only regular files may ship: {path} (mode {mode})")
        data = (ROOT / path).read_bytes()
        relative = Path(path).relative_to("src/agentbreeder").as_posix()
        files[relative] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "exec": mode == "100755",
        }
    return {"version": 1, "files": dict(sorted(files.items()))}


def render(manifest: dict) -> str:
    return json.dumps(manifest, indent=1, sort_keys=True) + "\n"


def untracked() -> list[str]:
    trees = [f"src/agentbreeder/{tree}" for tree in TREES]
    found = _git("ls-files", "--others", "--exclude-standard", "-z", "--", *trees)
    return [path for path in found if "__pycache__" not in Path(path).parts]


def main(argv: list[str]) -> int:
    text = render(build())
    if "--check" in argv:
        problems = []
        if not MANIFEST.exists() or MANIFEST.read_text() != text:
            stale = MANIFEST.relative_to(ROOT)
            problems.append(f"{stale} is stale: run scripts/content_manifest.py")
        problems += [f"untracked file under a shipped tree: {path}" for path in untracked()]
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    MANIFEST.write_text(text)
    print(f"wrote {MANIFEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
