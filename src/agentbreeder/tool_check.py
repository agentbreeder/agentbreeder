"""Tool versions: the checks `registry publish|import|fork|edit` run before a `tool/` version is
written (`TOOL_INVALID`), and the `functions` view `registry show` adds (M12 delta §5).

Nothing here imports tool code. Module files are read as bytes (at most 256 KiB, opened without
following symlinks), decoded as strict UTF-8 and parsed with `ast.parse`; `SyntaxError`,
`ValueError`, `RecursionError` and `MemoryError` are a refusal (or a `signature_error`), never a
crash. These checks fail early; `runtime/adk@1.2.0` makes the authoritative shadowing check at load
time with `find_spec` on the image's real `sys.path`, and `build` keeps its own checks.

They do not make tool code safe to run: the boundaries are review, the eval gate (a quality gate)
and, later, #909's policy.
"""

from __future__ import annotations

import ast
import copy
import os
import re
import stat
import sys
from collections import OrderedDict
from pathlib import Path

from agentbreeder import registry, runtime_profile, strict_yaml
from agentbreeder.runtime_profile import LEGACY_ADK

CODE = "TOOL_INVALID"
CHECKER_VERSION = 1  # part of the `show` cache key: raise it when the view changes
SOURCE_LIMIT = 256 * 1024
SIGNATURE_LIMIT = 1000
SPEC_LIMIT = 64 * 1024  # tool.yaml, as strict_yaml.load_file

# As runtime/adk's tools.py: what the model calls and the module under src/.
FUNCTION_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
MODULE_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
PART = re.compile(r"[a-z0-9_]+")  # each directory under src/, and each module file's stem
DUNDER = re.compile(r"__\w*__")
# Python imports these by name at startup, and pytest by name, from anywhere on sys.path.
HOOK_NAMES = frozenset({"sitecustomize", "usercustomize", "conftest", "pytest", "_pytest"})
MAX_FUNCTIONS = 64  # per tool.yaml; bounds the work `show` does in the console process
SIGNATURE_NODES = 2000  # AST nodes in a def's parameters and return annotation that are unparsed
ALLOWED_NOTE = "a tool holds only manifest.yaml, tool.yaml, README* and src/**/*.py"

# The module names of the runtimes published before #1083 F1 (`runtime_profile.LEGACY_ADK`):
# the runtime's own modules and its lock's import names. A later runtime version ships them in
# its own `runtime/modules.json`, which `runtime_names` reads from the built-in registry.
RUNTIME_MODULES = LEGACY_ADK.modules
LOCKED_IMPORTS = LEGACY_ADK.imports
# Standard-library modules removed in Python 3.12 and 3.13: absent from a newer host's
# `sys.stdlib_module_names`, but the runtime image may still have them.
REMOVED_STDLIB = frozenset(
    {
        "aifc", "asynchat", "asyncore", "audioop", "cgi", "cgitb", "chunk", "crypt", "distutils",
        "imghdr", "imp", "lib2to3", "mailcap", "msilib", "nis", "nntplib", "ossaudiodev",
        "pipes", "smtpd", "sndhdr", "spwd", "sunau", "telnetlib", "uu", "xdrlib",
    }
)  # fmt: skip
# In the runtime image but outside the lock (pip and its build tooling).
IMAGE_EXTRAS = frozenset({"pip", "setuptools", "pkg_resources", "_distutils_hack", "wheel"})


def runtime_names(root: Path | None = None) -> tuple[frozenset[str], frozenset[str]]:
    """(runtime module names, locked import names) a tool's src/ must not shadow: `LEGACY_ADK`'s
    plus every built-in runtime version's `runtime/modules.json`. A tool is checked when it is
    published, before any agent pins a runtime, so the names of every built-in runtime count.
    Cached per registry root; the built-in files never change in an installed package."""
    root = registry.BUILTIN_ROOT if root is None else root
    cached = _RUNTIME_NAMES.get(root)
    if cached is None:
        modules, imports = set(RUNTIME_MODULES), set()
        for names in LOCKED_IMPORTS.values():
            imports.update(names)
        for path in sorted(root.glob(f"runtime/*/*/{runtime_profile.MODULES_FILE}")):
            try:
                found = runtime_profile.parse_modules(_read_modules(path))
            except runtime_profile.ProfileError as error:
                shown = path.relative_to(root).as_posix()
                raise registry.RegistryError(error.code, f"{shown}: {error.message}") from None
            modules |= found.modules
            for names in found.imports.values():
                imports.update(names)
        cached = _RUNTIME_NAMES[root] = (frozenset(modules), frozenset(imports))
    return cached


_RUNTIME_NAMES: dict[Path, tuple[frozenset[str], frozenset[str]]] = {}


def _read_modules(path: Path) -> bytes:
    if path.is_symlink():
        raise runtime_profile.ProfileError("ARTIFACT_INVALID", "must not be a symlink")
    try:
        with path.open("rb") as handle:
            return handle.read(runtime_profile.MODULES_LIMIT + 1)
    except OSError:
        raise runtime_profile.ProfileError("ARTIFACT_INVALID", "unreadable") from None


def _reserved() -> frozenset[str]:
    modules, imports = runtime_names()
    names = set(modules) | set(imports) | REMOVED_STDLIB | IMAGE_EXTRAS
    names |= set(sys.stdlib_module_names) | set(sys.builtin_module_names)
    return frozenset(names)


def shadows(name: str) -> str | None:
    """Why a top-level name in a tool's src/ would hide a module the runtime needs, or None."""
    if DUNDER.fullmatch(name):
        return "is a reserved Python name"
    if name in HOOK_NAMES:
        return "is a name Python or pytest imports by itself"
    modules, imports = runtime_names()
    if name in modules:
        return "shadows a runtime module"
    if (
        name in sys.stdlib_module_names
        or name in REMOVED_STDLIB
        or name in sys.builtin_module_names
    ):
        return "shadows a standard-library module"
    if name in imports or name in IMAGE_EXTRAS:
        return "shadows a package the runtime installs"
    return None


class ToolCheckError(registry.RegistryError):
    def __init__(self, message: str):
        super().__init__(CODE, message)
        self.message = message


def _shown(text: str) -> str:
    return registry.clean(text, name=True)[0]


# -- The allowed files and top-level names ------------------------------------------------------


def _check_path(relative: str, is_dir: bool) -> None:
    """One entry of a tool version against the allowed files (not a denylist)."""
    parts = relative.split("/")
    if not is_dir and len(parts) == 1:
        if relative in ("manifest.yaml", "tool.yaml") or relative.startswith("README"):
            return
    elif parts[0] == "src":
        inner = parts[1:]
        if is_dir:
            if all(PART.fullmatch(part) for part in inner):
                return
        elif inner and relative.endswith(".py"):
            names = [*inner[:-1], inner[-1].removesuffix(".py")]
            if all(PART.fullmatch(part) for part in names):
                return
    hint = ""
    if not is_dir and Path(relative).name.startswith(("requirements", "pyproject", "setup.")):
        hint = "; tools have no dependencies of their own, they use the runtime's locked packages"
    kind = "directory" if is_dir else "file"
    raise ToolCheckError(
        f"{kind} {_shown(relative)} is not allowed: {ALLOWED_NOTE} "
        f"(lower-case letters, digits and _ in src/ names){hint}"
    )


def top_level_names(paths: list[tuple[str, bool]]) -> list[str]:
    """What `import NAME` could find in src/: module stems and package directories."""
    names = set()
    for relative, is_dir in paths:
        parts = relative.split("/")
        if len(parts) == 2 and parts[0] == "src":
            names.add(parts[1] if is_dir else parts[1].removesuffix(".py"))
    return sorted(names)


def _check_names(paths: list[tuple[str, bool]]) -> None:
    for relative, is_dir in paths:
        _check_path(relative, is_dir)
    files = {relative.removesuffix(".py") for relative, is_dir in paths if not is_dir}
    for relative, is_dir in paths:
        if is_dir and relative in files:  # import picks the package; one name, one module
            raise ToolCheckError(f"both {_shown(relative)}.py and {_shown(relative)}/ exist")
    for name in top_level_names(paths):
        reason = shadows(name)
        if reason:
            raise ToolCheckError(
                f"src/{_shown(name)} {reason}; tool code goes first on sys.path, so rename it"
            )


# -- tool.yaml ------------------------------------------------------------------------------------


def _functions(spec: dict) -> list[dict]:
    """The `functions` entries, checked as the runtime reads them."""
    functions = spec.get("functions")
    if functions is None:
        functions = []
    if not isinstance(functions, list):
        raise ToolCheckError("tool.yaml: `functions` must be a list")
    if len(functions) > MAX_FUNCTIONS:
        raise ToolCheckError(f"tool.yaml: at most {MAX_FUNCTIONS} functions")
    result, seen = [], set()
    for index, item in enumerate(functions):
        where = f"tool.yaml functions[{index}]"
        if not isinstance(item, dict):
            raise ToolCheckError(f"{where} must be a mapping")
        name, module = item.get("name"), item.get("module")
        function = item.get("function") or name  # as the runtime: empty means `name`
        for key, value, pattern in (
            ("name", name, FUNCTION_NAME),
            ("module", module, MODULE_NAME),
            ("function", function, FUNCTION_NAME),
        ):
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise ToolCheckError(f"{where}: `{key}` must match {pattern.pattern}")
        if name in seen:
            raise ToolCheckError(f"{where}: function {name} is declared twice")
        seen.add(name)
        # As the runtime reads them: str(description) (null shows as "None", as the model sees
        # it), and empty parameters take the default.
        description = str(item.get("description", ""))
        parameters = item.get("parameters") or {"type": "object", "properties": {}}
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ToolCheckError(f"{where}: `parameters` must be a JSON Schema object")
        result.append(
            {
                "name": name,
                "module": module,
                "function": function,
                "description": description.strip(),
                "parameters": parameters,
            }
        )
    return result


def module_path(module: str, files: set[str]) -> str | None:
    """The file a module name imports from src/: the package `src/M/` first, as `import` does,
    else `src/M.py` (`check` refuses a version with both)."""
    for candidate in (f"src/{module}/__init__.py", f"src/{module}.py"):
        if candidate in files:
            return candidate
    return None


# -- Module source --------------------------------------------------------------------------------


class _Unparsable(Exception):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code  # too_large, syntax_error or not_found
        self.reason = reason


def _parse(data: bytes, relative: str) -> ast.Module:
    if len(data) > SOURCE_LIMIT:
        raise _Unparsable("too_large", f"{relative} exceeds {SOURCE_LIMIT // 1024} KiB")
    try:
        text = data.decode("utf-8", errors="strict")
        return ast.parse(text, filename=relative)
    except UnicodeDecodeError:
        raise _Unparsable("syntax_error", f"{relative} is not UTF-8 text") from None
    except SyntaxError as error:
        line = f" (line {error.lineno})" if isinstance(error.lineno, int) else ""
        raise _Unparsable("syntax_error", f"{relative} does not parse{line}") from None
    except (ValueError, RecursionError, MemoryError):
        raise _Unparsable(
            "syntax_error", f"{relative} does not parse (too deeply nested)"
        ) from None


def _definition(tree: ast.Module, function: str, relative: str):
    """The top-level `def`/`async def` the runtime would call (the last one wins, as in Python)."""
    found = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            found = node
    if found is None:
        raise _Unparsable("not_found", f"{relative} has no top-level def or async def {function}")
    return found


def _signature(node) -> str:
    """`name(params) -> returns` from the source, without the first parameter (the runtime
    passes its ToolRuntime there; the model never sees it), capped at SIGNATURE_LIMIT."""
    parts = [node.args] if node.returns is None else [node.args, node.returns]
    if sum(1 for part in parts for _ in ast.walk(part)) > SIGNATURE_NODES:
        raise _Unparsable("too_large", "the parameters are too large to show")
    arguments = copy.copy(node.args)
    if arguments.posonlyargs:
        arguments.posonlyargs = arguments.posonlyargs[1:]
    elif arguments.args:
        arguments.args = arguments.args[1:]
    # Defaults belong to the last positional parameters: keep at most as many as remain.
    kept = min(len(arguments.defaults), len(arguments.posonlyargs) + len(arguments.args))
    arguments.defaults = arguments.defaults[len(arguments.defaults) - kept :]
    text = f"{node.name}({ast.unparse(arguments)})"
    if node.returns is not None:
        text += f" -> {ast.unparse(node.returns)}"
    text = registry.clean(text, name=True)[0]
    if len(text) > SIGNATURE_LIMIT:
        text = text[: SIGNATURE_LIMIT - 1] + "…"
    return text


# -- Before a write (`place`) ---------------------------------------------------------------------


def _read_source(staged: Path, relative: str) -> bytes:
    """A module file of the private staged copy: a regular file opened without following
    symlinks, at most SOURCE_LIMIT + 1 bytes read."""
    path = staged / relative
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise ToolCheckError(f"cannot read {_shown(relative)}") from None
    # Checked on the raw descriptor: fdopen refuses a directory with IsADirectoryError.
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ToolCheckError(f"{_shown(relative)} must be a regular file")
    with os.fdopen(descriptor, "rb") as reader:
        return reader.read(SOURCE_LIMIT + 1)


def check(staged: Path, ref) -> None:
    """Refuse (`TOOL_INVALID`) a staged tool version that holds files other than the allowed
    ones, shadows a module the runtime needs, or whose tool.yaml names a function that does not
    resolve to a parsable top-level def. Error text names paths inside the version only."""
    entries = registry._entries(staged)
    paths = [(relative, stat.S_ISDIR(info.st_mode)) for relative, _, info in entries]
    try:
        _check_names(paths)
        files = {relative for relative, is_dir in paths if not is_dir}
        if "tool.yaml" not in files:
            raise ToolCheckError("tool.yaml is missing")
        try:
            spec = strict_yaml.load_file(staged / "tool.yaml", SPEC_LIMIT)
        except strict_yaml.StrictYamlError as error:
            message = str(error).replace(str(staged), "<version>")
            raise ToolCheckError(f"tool.yaml does not load: {message}") from None
        for function in _functions(spec):
            relative = module_path(function["module"], files)
            if relative is None:
                raise ToolCheckError(
                    f"function {function['name']}: module {function['module']} is not in src/ "
                    f"(src/{function['module']}.py or src/{function['module']}/__init__.py)"
                )
            try:
                tree = _parse(_read_source(staged, relative), relative)
                _definition(tree, function["function"], relative)
            except _Unparsable as error:
                raise ToolCheckError(f"function {function['name']}: {error.reason}") from None
    except ToolCheckError as error:
        raise ToolCheckError(f"{ref}: {error.message}") from None


# -- `registry show` ------------------------------------------------------------------------------

_CACHE: OrderedDict[tuple, dict] = OrderedDict()
_CACHE_SIZE = 256


def view(digest: str, items: list) -> dict:
    """`functions` (and `functions_error`) for `registry show` of a tool version, from the bytes
    `show` read (`registry._File` items). Versions are immutable, so the result is cached by
    the version digest and CHECKER_VERSION."""
    key = (digest, CHECKER_VERSION)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return copy.deepcopy(_CACHE[key])
    result = _view(items)
    _CACHE[key] = result
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return copy.deepcopy(result)


def _view(items: list) -> dict:
    by_path = {item.relative: item for item in items if item.type == "file"}
    spec_item = by_path.get("tool.yaml")
    try:
        if spec_item is None:
            raise ToolCheckError("tool.yaml is missing")
        if spec_item.head is None or spec_item.size > SPEC_LIMIT:
            raise ToolCheckError("tool.yaml is too large to show")
        try:
            spec = strict_yaml.load_text(spec_item.head.decode("utf-8"))
        except UnicodeDecodeError:
            raise ToolCheckError("tool.yaml is not UTF-8 text") from None
        except strict_yaml.StrictYamlError as error:
            raise ToolCheckError(f"tool.yaml does not load: {error}") from None
        functions = _functions(spec)
    except ToolCheckError as error:
        return {"functions": [], "functions_error": str(error)}
    trees: dict[str, ast.Module | _Unparsable] = {}
    signatures: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    shown = []
    for function in functions:
        relative = module_path(function["module"], set(by_path))
        signature, problem = None, None
        try:
            if relative is None:
                raise _Unparsable("not_found", "no such module")
            if relative not in trees:
                item = by_path[relative]
                try:
                    if item.head is None or item.truncated or item.size > SOURCE_LIMIT:
                        raise _Unparsable("too_large", "too large")
                    trees[relative] = _parse(item.head, relative)
                except _Unparsable as error:
                    trees[relative] = error
            key = (relative, function["function"])
            if key not in signatures:  # several entries may name one def: unparse it once
                signatures[key] = _signature_of(trees[relative], function["function"], relative)
            signature, problem = signatures[key]
        except _Unparsable as error:
            problem = error.code
        shown.append(
            {
                **{key: _visible(function[key]) for key in function},
                "signature": signature,
                "signature_error": problem,
            }
        )
    return {"functions": shown, "functions_error": None}


def _signature_of(tree, function: str, relative: str) -> tuple[str | None, str | None]:
    try:
        if isinstance(tree, _Unparsable):
            raise tree
        return _signature(_definition(tree, function, relative)), None
    except _Unparsable as error:
        return None, error.code
    except (RecursionError, MemoryError, ValueError):  # ast.unparse of a pathological default
        return None, "syntax_error"


# Invisible format characters and the zero-width joiners: invisible in a page, so a description a
# reviewer reads could carry text only the model sees. Removed on top of registry.clean.
_INVISIBLE = re.compile(f"[{registry._INVISIBLE}]")


def _visible(value):
    if isinstance(value, str):
        return _INVISIBLE.sub("", registry.clean(value)[0])
    if isinstance(value, dict):
        return {registry.clean(str(k), name=True)[0]: _visible(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_visible(v) for v in value]
    return registry._clean_value(value)


RESERVED = _reserved()
