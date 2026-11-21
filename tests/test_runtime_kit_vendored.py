"""The runtime kit is vendored, byte for byte, into every kit runtime (#1083 F2b, D2).

`src/agentbreeder/runtime_kit/` is the source of truth. The newest version of each built-in
runtime with `profile: kit` must carry the kit version its manifest names in `runtime/kit/`,
identical to the source. (Older versions are frozen by tests/test_builtin_append_only.py.) A
kit change therefore ships as a new version of each runtime that takes it.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentbreeder import registry, runtime_profile
from agentbreeder.runtime_kit import KIT_VERSION
from agentbreeder.strict_yaml import load_file

KIT = Path(__file__).resolve().parents[1] / "src/agentbreeder/runtime_kit"
RUNTIMES = Path(registry.BUILTIN_ROOT) / "runtime"


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def kit_runtimes() -> dict[str, Path]:
    """Each kit runtime's newest version directory."""
    newest: dict[str, Path] = {}
    for manifest in sorted(RUNTIMES.glob("*/*/manifest.yaml")):
        meta = load_file(manifest)
        if meta.get("profile") != "kit":
            continue
        directory = manifest.parent
        name = directory.parent.name
        if name not in newest or _version(directory.name) > _version(newest[name].name):
            newest[name] = directory
    return newest


def _files(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def test_there_is_a_kit_runtime():
    assert {"crewai", "langgraph", "openai-agents"} <= set(kit_runtimes())


def test_each_kit_runtime_vendors_the_kit_byte_for_byte():
    source = _files(KIT)
    for name, directory in kit_runtimes().items():
        profile = runtime_profile.load(directory)
        assert profile.kit == KIT_VERSION, (
            f"runtime/{name}@{directory.name} names kit {profile.kit}; the source is "
            f"{KIT_VERSION}: publish a new runtime version with the new kit"
        )
        vendored = _files(directory / "runtime" / "kit")
        assert sorted(vendored) == sorted(source), f"runtime/{name}: kit files differ"
        changed = [file for file in source if vendored[file] != source[file]]
        assert not changed, (
            f"runtime/{name}@{directory.name}/runtime/kit differs from runtime_kit in "
            f"{changed}: copy src/agentbreeder/runtime_kit/*.py into a new runtime version"
        )


def test_the_kit_imports_nothing_from_agentbreeder_and_only_itself_relatively():
    kit_modules = {path.stem for path in KIT.glob("*.py")}
    for path in sorted(KIT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative: a kit module
                    continue
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                assert name != "agentbreeder", f"{path.name} imports agentbreeder"
                assert name not in kit_modules, f"{path.name} imports {name} absolutely"


def test_the_kit_runtimes_list_the_kit_as_their_own_module():
    for name, directory in kit_runtimes().items():
        modules = runtime_profile.load_modules(directory, runtime_profile.load(directory))
        assert "kit" in modules.modules, name
