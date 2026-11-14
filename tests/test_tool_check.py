"""Tool checks before a write (`TOOL_INVALID`) and `registry show` functions (M12 §5, #828)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from agentbreeder import cli, registry, tool_check
from agentbreeder.registry import Ref, RegistryError

BUILTIN = registry.BUILTIN_ROOT
NORTHWIND = Path(registry.__file__).parent / "examples" / "northwind"
ORDERS = Ref.parse("tool/orders@1.0.0")

TOOL_YAML = """functions:
  - name: lookup
    module: lookup
    function: lookup
    description: Look something up.
    parameters:
      type: object
      properties:
        query: {type: string}
"""
SOURCE = 'def lookup(rt, query: str = "") -> str:\n    return query\n'


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setattr(registry, "BUILTIN_ROOT", tmp_path / "builtin")
    tool_check._CACHE.clear()


def make_tool(directory: Path, files: dict, name: str = "lookup", version: str = "1.0.0") -> Path:
    directory.mkdir(parents=True)
    (directory / "manifest.yaml").write_text(f"kind: tool\nname: {name}\nversion: {version}\n")
    for relative, content in files.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content)
    return directory


def tool(tmp_path: Path, extra: dict | None = None, **replace) -> Path:
    files = {"tool.yaml": TOOL_YAML, "src/lookup.py": SOURCE, **(extra or {})}
    for key, value in replace.items():
        files[key.replace("__", "/").replace("_dot_", ".")] = value
    return make_tool(tmp_path / "source", {k: v for k, v in files.items() if v is not None})


def refused(source: Path, root: Path, pattern: str) -> RegistryError:
    with pytest.raises(RegistryError) as error:
        registry.place(source, root)
    assert error.value.code == "TOOL_INVALID", error.value
    assert re.search(pattern, str(error.value)), error.value
    assert not (root / "tool").exists()  # nothing written
    assert str(source) not in str(error.value)  # never the staging directory either
    assert "agentbreeder-import-" not in str(error.value) and ".import-" not in str(error.value)
    return error.value


# -- allowed files -----------------------------------------------------------------------------


def test_a_valid_tool_is_published(tmp_path):
    result = registry.place(tool(tmp_path), tmp_path / "registry")
    assert result["ref"] == "tool/lookup@1.0.0"


@pytest.mark.parametrize(
    "path",
    [
        "src/native.so",
        "src/hook.pth",
        "src/lookup_helper.pyc_",
        "requirements.txt",
        "requirements-dev.txt",
        "pyproject.toml",
        "setup.py",
        "src/Upper.py",
        "src/my-module.py",
        "src/pkg-name/__init__.py",
        "data/rows.json",
        "notes.txt",
        "src/wheel.whl",
        "src/data.json",
    ],
)
def test_files_outside_the_allowlist_are_refused(tmp_path, path):
    error = refused(tool(tmp_path, {path: "x\n"}), tmp_path / "registry", "is not allowed")
    if path.startswith(("requirements", "pyproject", "setup")):
        assert "no dependencies of their own" in str(error)


@pytest.mark.parametrize(
    "name", ["sitecustomize", "usercustomize", "conftest", "pytest", "_pytest"]
)
@pytest.mark.parametrize("form", ["{}.py", "{}/__init__.py"])
def test_startup_hook_modules_are_refused_by_name(tmp_path, name, form):
    source = tool(tmp_path, {"src/" + form.format(name): ""})
    refused(source, tmp_path / "registry", "imports by itself")


def test_a_module_that_is_both_a_file_and_a_package_is_refused(tmp_path):
    source = tool(tmp_path, {"src/lookup/__init__.py": ""})
    refused(source, tmp_path / "registry", "both src/lookup.py and src/lookup/ exist")


def test_readmes_and_nested_packages_are_allowed(tmp_path):
    extra = {
        "README.md": "# x\n",
        "README": "x\n",
        "src/lookup_pkg/__init__.py": "",
        "src/lookup_pkg/sub_1/helpers.py": "y = 2\n",
        "src/lookup_pkg/sitecustomize.py": "",  # only src/ top level is imported at startup
    }
    registry.place(tool(tmp_path, extra), tmp_path / "registry")


def test_a_symlinked_module_is_refused_and_nothing_is_written(tmp_path):
    source = tool(tmp_path, **{"src__lookup_dot_py": None})
    outside = tmp_path / "outside.py"
    outside.write_text(SOURCE)
    (source / "src").mkdir()
    os.symlink(outside, source / "src/lookup.py")
    with pytest.raises(RegistryError, match="symlinks are not allowed"):
        registry.place(source, tmp_path / "registry")
    assert not (tmp_path / "registry/tool").exists()


# -- shadowing ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,reason",
    [
        ("src/json.py", "standard-library"),
        ("src/asyncio/__init__.py", "standard-library"),
        ("src/cgi.py", "standard-library"),  # removed in 3.13, still in a 3.12 image
        ("src/sys.py", "standard-library"),
        ("src/agent.py", "runtime module"),
        ("src/memory.py", "runtime module"),
        ("src/yaml.py", "package the runtime installs"),
        ("src/psycopg/__init__.py", "package the runtime installs"),
        ("src/google/__init__.py", "package the runtime installs"),
        ("src/pydantic.py", "package the runtime installs"),
        ("src/httpx.py", "package the runtime installs"),
        ("src/opentelemetry/__init__.py", "package the runtime installs"),
        ("src/pip.py", "package the runtime installs"),
        ("src/pkg_resources.py", "package the runtime installs"),
        ("src/__init__.py", "reserved Python name"),
        ("src/__main__.py", "reserved Python name"),
    ],
)
def test_top_level_names_that_shadow_are_refused(tmp_path, path, reason):
    refused(tool(tmp_path, {path: ""}), tmp_path / "registry", reason)


def test_a_declared_module_that_shadows_is_refused(tmp_path):
    spec = TOOL_YAML.replace("module: lookup", "module: json")
    source = tool(tmp_path, {"src/json.py": SOURCE}, tool_dot_yaml=spec)
    refused(source, tmp_path / "registry", r"src/json shadows a standard-library module")


def test_every_locked_runtime_package_has_its_import_names():
    """A lock that gains a package must add its import names to tool_check.LOCKED_IMPORTS."""
    locks = sorted(
        (Path(registry.__file__).parent / "builtin_registry").glob(
            "runtime/adk/*/runtime/requirements.lock"
        )
    )
    assert locks
    packages = set()
    for lock in locks:
        for line in lock.read_text().splitlines():
            match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)==", line)
            if match:
                packages.add(re.sub(r"[-_.]+", "-", match[1]).lower())
    missing = sorted(packages - set(tool_check.LOCKED_IMPORTS))
    assert not missing, f"add the import names of {missing} to tool_check.LOCKED_IMPORTS"
    for name in ("psycopg", "yaml", "pydantic", "google", "httpx", "opentelemetry", "litellm"):
        assert name in tool_check.RESERVED


def test_runtime_modules_match_every_builtin_runtime():
    runtimes = Path(registry.__file__).parent / "builtin_registry/runtime/adk"
    stems = {path.stem for path in runtimes.glob("*/runtime/*.py")}
    assert stems == set(tool_check.RUNTIME_MODULES)


# -- code checks (S-06) --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,pattern",
    [
        ("functions: {}\n", "must be a list"),
        ("functions: [x]\n", "must be a mapping"),
        ("functions: [{name: Bad, module: lookup}]\n", "`name` must match"),
        ("functions: [{name: lookup, module: Lookup}]\n", "`module` must match"),
        ("functions: [{name: lookup, module: lookup, function: 2}]\n", "`function` must match"),
        ("functions: [{name: lookup, module: lookup}, {name: lookup, module: lookup}]\n",
         "declared twice"),
        ("functions: [" + "{name: f, module: lookup}, " * 65 + "]\n", "at most 64 functions"),
        ("functions: [{name: lookup, module: lookup, parameters: {type: string}}]\n",
         "JSON Schema object"),
        ("functions: [{name: lookup, module: missing}]\n", "module missing is not in src/"),
        ("functions: [{name: lookup, module: lookup, function: other}]\n",
         "no top-level def or async def other"),
        ("functions: [\n", "does not load"),
        ("- 1\n", "does not load"),
    ],
)  # fmt: skip
def test_tool_yaml_problems_are_refused(tmp_path, spec, pattern):
    refused(tool(tmp_path, tool_dot_yaml=spec), tmp_path / "registry", pattern)


@pytest.mark.parametrize(
    "entry",
    [
        "{name: lookup, module: lookup, parameters: {}}",
        "{name: lookup, module: lookup, parameters: null}",
        "{name: lookup, module: lookup, function: null}",
        "{name: lookup, module: lookup, function: ''}",
        "{name: lookup, module: lookup, description: null}",
        "{name: lookup, module: lookup, description: 42}",
    ],
)
def test_what_the_runtime_accepts_is_accepted(tmp_path, entry):
    """Empty fields are read as the runtime reads them (`or` defaults, str(description))."""
    registry.place(tool(tmp_path, tool_dot_yaml=f"functions: [{entry}]\n"), tmp_path / "registry")


def test_a_missing_tool_yaml_is_refused(tmp_path):
    refused(tool(tmp_path, tool_dot_yaml=None), tmp_path / "registry", "tool.yaml is missing")


@pytest.mark.parametrize(
    "content,pattern",
    [
        (b"def lookup(rt):\n    return '\xff'\n", "not UTF-8 text"),
        ("def lookup(rt:\n", r"does not parse \(line 1\)"),
        ("x = " + "(" * 5000 + ")" * 5000 + "\n", "does not parse"),
        ("x = " + "-" * 200_000 + "1\n", "does not parse|exceeds 256 KiB"),
        ("# " + "x" * (256 * 1024) + "\ndef lookup(rt): pass\n", "exceeds 256 KiB"),
        ("lookup = 1\n", "no top-level def"),
        ("class C:\n    def lookup(self): pass\n", "no top-level def"),
    ],
)
def test_module_problems_are_refused(tmp_path, content, pattern):
    source = tool(tmp_path, src__lookup_dot_py=content)
    refused(source, tmp_path / "registry", pattern)


def test_a_package_module_and_async_def_resolve(tmp_path):
    spec = TOOL_YAML.replace("module: lookup", "module: lookup_pkg")
    source = tool(
        tmp_path,
        {"src/lookup_pkg/__init__.py": "async def lookup(rt, query=''):\n    return query\n"},
        tool_dot_yaml=spec,
        src__lookup_dot_py=None,
    )
    registry.place(source, tmp_path / "registry")


def test_tool_code_is_never_imported(tmp_path):
    """S-06: a module whose top level raises (or would write a file) is only parsed."""
    marker = tmp_path / "imported"
    body = f"open({str(marker)!r}, 'w').write('x')\nraise SystemExit(9)\n" + SOURCE
    source = tool(tmp_path, src__lookup_dot_py=body)
    registry.place(source, tmp_path / "registry")
    shown = registry.show(Ref.parse("tool/lookup@1.0.0"), [tmp_path / "registry"])
    assert shown["functions"][0]["signature"] == "lookup(query: str='') -> str"
    assert not marker.exists() and "lookup" not in sys.modules


def test_other_kinds_are_not_tool_checked(tmp_path):
    source = make_tool(tmp_path / "p", {"anything.bin": b"\x00"})
    (source / "manifest.yaml").write_text("kind: prompt\nname: p\nversion: 1.0.0\n")
    registry.place(source, tmp_path / "registry")


def test_the_scaffold_passes_and_shows_its_function(tmp_path):
    source = registry.scaffold("tool", "9-notes", tmp_path)
    registry.place(source, tmp_path / "registry")
    shown = registry.show(Ref.parse("tool/9-notes@0.1.0"), [tmp_path / "registry"])
    assert shown["functions_error"] is None
    (function,) = shown["functions"]
    assert function["module"] == "tool_9_notes"
    assert function["signature"] == "echo_9_notes(text: str) -> str"


def test_every_shipped_tool_passes(monkeypatch):
    """Compatibility: every built-in and example tool fits the rules (M12 §5)."""
    versions = sorted(
        path.parent
        for base in (Path(registry.__file__).parent / "builtin_registry", NORTHWIND.parent)
        for path in base.glob("**/tool/*/*/manifest.yaml")
    )
    assert any("orders" in str(v) for v in versions)
    for version in versions:
        ref = Ref.parse(f"tool/{version.parent.name}@{version.name}")
        tool_check.check(version, ref)


# -- through fork and edit ---------------------------------------------------------------------


@pytest.fixture
def northwind(tmp_path) -> Path:
    project = tmp_path / "northwind"
    shutil.copytree(NORTHWIND, project, ignore=shutil.ignore_patterns("__pycache__"))
    return project


def test_editing_orders_is_checked_before_anything_is_written(northwind):
    target = northwind / "registry/tool/orders/1.0.1"
    for sets, pattern in (
        ([("src/orders.py", b"def lookup_orders(rt:\n")], "does not parse"),
        ([("src/json.py", b"")], "shadows a standard-library module"),
        ([("requirements.txt", b"requests\n")], "no dependencies of their own"),
    ):
        with pytest.raises(RegistryError, match=pattern):
            registry.edit_version(ORDERS, northwind, sets=sets)
        assert not target.exists()
    good = (NORTHWIND / "registry/tool/orders/1.0.0/src/orders.py").read_bytes() + b"\n# v2\n"
    result = registry.edit_version(ORDERS, northwind, sets=[("src/orders.py", good)])
    assert result["ref"] == "tool/orders@1.0.1" and target.is_dir()


def test_forking_is_checked(tmp_path):
    shared = tmp_path / "shared"
    make_tool(shared / "tool/lookup/1.0.0", {"tool.yaml": TOOL_YAML, "src/lookup.py": SOURCE})
    bad = make_tool(shared / "tool/bad/1.0.0", {"tool.yaml": TOOL_YAML, "src/json.py": ""}, "bad")
    assert bad.is_dir()
    project = tmp_path / "project"
    project.mkdir()
    os.environ[registry.PATH_ENV] = str(shared)
    try:
        registry.fork_version(Ref.parse("tool/lookup@1.0.0"), project, version="1.0.1")
        with pytest.raises(RegistryError, match="TOOL_INVALID"):
            registry.fork_version(Ref.parse("tool/bad@1.0.0"), project, version="1.0.1")
    finally:
        del os.environ[registry.PATH_ENV]
    assert (project / "registry/tool/lookup/1.0.1").is_dir()
    assert not (project / "registry/tool/bad").exists()


# -- `registry show` functions -------------------------------------------------------------------


def test_show_northwind_orders_functions():
    shown = registry.show(ORDERS, [NORTHWIND / "registry"])
    assert shown["functions_error"] is None
    names = [(f["name"], f["module"], f["function"]) for f in shown["functions"]]
    assert names == [
        ("lookup_orders", "orders", "lookup_orders"),
        ("get_product_catalog", "orders", "get_product_catalog"),
    ]
    first = shown["functions"][0]
    assert first["signature"] == "lookup_orders(order_id: str='', status: str='') -> str"
    assert first["signature_error"] is None
    assert first["parameters"]["type"] == "object"
    assert set(first["parameters"]["properties"]) == {"order_id", "status"}
    assert first["description"].startswith("Look up the current customer's Northwind orders")


def _shown(tmp_path: Path, **replace) -> dict:
    """Write a version directly (bypassing the write checks) and show it."""
    root = tmp_path / "registry"
    files = {"tool.yaml": TOOL_YAML, "src/lookup.py": SOURCE}
    for key, value in replace.items():
        files[key.replace("__", "/").replace("_dot_", ".")] = value
    make_tool(root / "tool/lookup/1.0.0", {k: v for k, v in files.items() if v is not None})
    return registry.show(Ref.parse("tool/lookup@1.0.0"), [root])


@pytest.mark.parametrize(
    "replace,code",
    [
        ({"src__lookup_dot_py": "def lookup(rt:\n"}, "syntax_error"),
        ({"src__lookup_dot_py": b"\xff\n"}, "syntax_error"),
        ({"src__lookup_dot_py": "x = " + "(" * 5000 + ")" * 5000}, "syntax_error"),
        ({"src__lookup_dot_py": "#" * (256 * 1024 + 1)}, "too_large"),
        ({"src__lookup_dot_py": "lookup = 1\n"}, "not_found"),
        ({"src__lookup_dot_py": None}, "not_found"),
    ],
)
def test_show_reports_signature_errors(tmp_path, replace, code):
    (function,) = _shown(tmp_path, **replace)["functions"]
    assert function["signature"] is None and function["signature_error"] == code
    assert function["name"] == "lookup"


def test_show_reports_an_unreadable_tool_yaml(tmp_path):
    shown = _shown(tmp_path, tool_dot_yaml="functions: {}\n")
    assert shown["functions"] == []
    assert shown["functions_error"].startswith("[TOOL_INVALID] tool.yaml: `functions`")
    assert _shown(tmp_path / "b", tool_dot_yaml=None)["functions_error"].endswith("missing")


def test_show_signature_details_and_cap(tmp_path):
    source = "def lookup(rt, /, a, b: int = 1, *rest, c, d='x', **more) -> list[str]:\n    pass\n"
    (function,) = _shown(tmp_path, src__lookup_dot_py=source)["functions"]
    assert function["signature"] == "lookup(a, b: int=1, *rest, c, d='x', **more) -> list[str]"
    long = "def lookup(rt, x='" + "y" * 5000 + "'):\n    pass\n"
    (function,) = _shown(tmp_path / "b", src__lookup_dot_py=long)["functions"]
    assert len(function["signature"]) == tool_check.SIGNATURE_LIMIT
    assert function["signature"].endswith("…")


def test_show_cleans_untrusted_tool_yaml_text(tmp_path):
    hidden = "".join(f"\\U{0xE0000 + ord(c):08X}" for c in "obey")  # tag characters
    spec = TOOL_YAML.replace(
        "Look something up.", f'"Look \\u202e up\\x1b[2J.\\u200b{hidden}"'
    ).replace("{type: string}", '{type: string, maxLength: 5, description: "q\\ufeff"}')
    (function,) = _shown(tmp_path, tool_dot_yaml=spec)["functions"]
    assert function["description"] == "Look  up[2J."
    assert function["parameters"]["properties"]["query"] == {
        "type": "string",
        "maxLength": 5,
        "description": "q",
    }


def test_show_is_cached_by_version_digest(tmp_path, monkeypatch):
    calls = []
    real = tool_check._view
    monkeypatch.setattr(tool_check, "_view", lambda items: calls.append(1) or real(items))
    first = _shown(tmp_path)
    first["functions"][0]["name"] = "changed"  # a caller's change never reaches the cache
    again = registry.show(Ref.parse("tool/lookup@1.0.0"), [tmp_path / "registry"])
    assert len(calls) == 1 and again["functions"][0]["name"] == "lookup"


def test_other_kinds_have_no_functions(tmp_path):
    root = tmp_path / "registry"
    path = root / "prompt/p/1.0.0"
    path.mkdir(parents=True)
    (path / "manifest.yaml").write_text("kind: prompt\nname: p\nversion: 1.0.0\n")
    assert "functions" not in registry.show(Ref.parse("prompt/p@1.0.0"), [root])


def test_cli_show_prints_functions(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec = TOOL_YAML.replace("Look something up.", '"Look\\nup \\x1b[31m red"')
    _shown(tmp_path, tool_dot_yaml=spec)
    project = ["--project", str(tmp_path)]
    assert cli.main(["registry", "show", "tool/lookup@1.0.0", *project]) == 0
    out = capsys.readouterr().out
    assert "Functions:\n  lookup  lookup: lookup(query: str='') -> str\n    Look up" in out
    assert "\x1b" not in out
    assert cli.main(["registry", "show", "tool/lookup@1.0.0", *project, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["functions"][0]["signature_error"] is None


def test_a_tool_without_functions_is_allowed_and_shows_none(tmp_path):
    registry.place(tool(tmp_path, tool_dot_yaml="# none yet\nother: 1\n"), tmp_path / "registry")
    shown = registry.show(Ref.parse("tool/lookup@1.0.0"), [tmp_path / "registry"])
    assert shown["functions"] == [] and shown["functions_error"] is None


@pytest.mark.parametrize(
    "spec,message",
    [
        (b"functions: \xff\n", "not UTF-8 text"),
        (b"functions: [\n", "does not load"),
        (b"# " + b"x" * (64 * 1024) + b"\n", "too large to show"),
    ],
)
def test_show_reports_tool_yaml_that_does_not_read(tmp_path, spec, message):
    assert message in _shown(tmp_path, tool_dot_yaml=spec)["functions_error"]


def test_show_turns_a_failing_unparse_into_syntax_error(tmp_path, monkeypatch):
    def fail(node):
        raise RecursionError

    monkeypatch.setattr(tool_check, "_signature", fail)
    (function,) = _shown(tmp_path)["functions"]
    assert function["signature_error"] == "syntax_error"


def test_the_show_cache_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_check, "_CACHE_SIZE", 1)
    _shown(tmp_path / "a")
    _shown(tmp_path / "b", src__lookup_dot_py=SOURCE + "# b\n")
    assert len(tool_check._CACHE) == 1


def test_reading_a_module_refuses_what_is_not_a_regular_file(tmp_path):
    (tmp_path / "src/pkg").mkdir(parents=True)
    with pytest.raises(RegistryError, match="cannot read src/missing.py"):
        tool_check._read_source(tmp_path, "src/missing.py")
    with pytest.raises(RegistryError, match="src/pkg must be a regular file"):
        tool_check._read_source(tmp_path, "src/pkg")


def test_show_unparses_each_def_once_and_bounds_it(tmp_path, monkeypatch):
    entries = "".join(f"  - {{name: f{i}, module: lookup, function: lookup}}\n" for i in range(64))
    calls = []
    real = tool_check._signature
    monkeypatch.setattr(tool_check, "_signature", lambda node: calls.append(1) or real(node))
    shown = _shown(tmp_path, tool_dot_yaml="functions:\n" + entries)
    assert len(shown["functions"]) == 64 and len(calls) == 1
    assert {f["signature"] for f in shown["functions"]} == {"lookup(query: str='') -> str"}
    big = "def lookup(rt, a=[" + ", ".join(["1"] * 3000) + "]):\n    pass\n"
    (function,) = _shown(tmp_path / "b", src__lookup_dot_py=big)["functions"]
    assert function["signature_error"] == "too_large"


def test_show_prefers_the_package_as_import_does(tmp_path):
    """Written directly (publish refuses both): `import lookup` finds the package first."""
    root = tmp_path / "registry"
    package = "def lookup(rt, from_package: int):\n    pass\n"
    files = {"tool.yaml": TOOL_YAML, "src/lookup.py": SOURCE, "src/lookup/__init__.py": package}
    make_tool(root / "tool/lookup/1.0.0", files)
    (function,) = registry.show(Ref.parse("tool/lookup@1.0.0"), [root])["functions"]
    assert function["signature"] == "lookup(from_package: int)"


@pytest.mark.parametrize("name", ["0", "a" * 63, "a-", "9-x", "x" + "-" * 62, "tool-" + "b" * 58])
def test_the_scaffold_passes_for_every_name_shape(tmp_path, name):
    source = registry.scaffold("tool", name, tmp_path)
    registry.place(source, tmp_path / "registry")


def test_deep_yaml_flow_nesting_is_refused_quickly():
    from agentbreeder import strict_yaml

    with pytest.raises(strict_yaml.StrictYamlError, match="more than 64 levels"):
        strict_yaml.load_text("a: " + "[" * 20000 + "]" * 20000)
    assert strict_yaml.load_text("a: " + "[" * 64 + "]" * 64)["a"]


def test_a_null_description_shows_as_the_model_sees_it(tmp_path):
    spec = "functions: [{name: lookup, module: lookup, description: null}]\n"
    (function,) = _shown(tmp_path, tool_dot_yaml=spec)["functions"]
    assert function["description"] == "None"  # runtime: str(item.get("description", ""))
