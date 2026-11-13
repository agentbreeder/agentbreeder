"""The console's static files: vendored Preact + htm, the source guard and the CSP (#816, M0b).

Console spec §10.1 I1: a console session is code execution as the owner, so the browser-side
guards (CSP with Trusted Types, the source guard, no HTML sinks) are a primary control (S-23).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from agentbreeder.center import server

STATIC = server.STATIC
VENDOR = STATIC / "vendor"

# htm 3.1.1's `preact/standalone.module.js`: htm plus the Preact 10 build and hooks it bundles,
# one ES module with no imports. Source: https://registry.npmjs.org/htm/-/htm-3.1.1.tgz
# (its npm integrity is HTM_TARBALL_SHA512), file `package/preact/standalone.module.js`, copied
# unchanged.
HTM_TARBALL_SHA512 = (
    "sha512-983Vyg8NwUE7JkZ6NmOqpCZ+sh1bKv2iYTlUkzlWmA5JD2acKoxd4KVxbMmxX/"
    "85mtfdnDmTFoNKcg5DGAvxNQ=="
)
VENDORED = {
    "htm-3.1.1-preact-standalone.module.js": (
        "72284e8e9079c87817145df1110f74e8a2aa040b2fc384922e18dfcb46fc1fd7"
    ),
}
LICENCES = {"LICENSE-htm.txt": "Apache License", "LICENSE-preact.txt": "Jason Miller"}

# Sinks that turn a string into markup or code. Each is a pattern over the raw source; `eval` and
# `Function` are matched as a call or a property name, since "the eval gate" is UI text.
FORBIDDEN = {
    name: re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
    for name in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "dangerouslySetInnerHTML",
        "srcdoc",
        "createContextualFragment",
        "DOMParser",
        "setHTMLUnsafe",
        "parseHTMLUnsafe",
    )
} | {
    "document.write": re.compile(r"\bdocument\s*\.\s*write(?:ln)?\b"),
    "eval": re.compile(r"(?<![\w$.])eval\s*\(|\.\s*eval\b|[\"'`]eval[\"'`]"),
    "Function": re.compile(r"(?<![\w$])Function\s*\(|[\"'`]Function[\"'`]"),
}
STRING_TIMER = re.compile(r"\bset(?:Timeout|Interval)\s*\(\s*[\"'`]")
CODE_SUFFIXES = {".js", ".css", ".html", ".svg"}
# Served but never parsed as code: fonts and PNG are binary, .txt is served as text/plain.
INERT_SUFFIXES = {".woff2", ".png", ".txt"}
EXTERNAL = re.compile(r"(?i)(?:\b(?:https?|wss?|ftp):|(?:url\(|[\"'`=\s])\s*//[^/\s])")
# The SVG namespace is a name, not a fetch.
NAMESPACES = {"http://www.w3.org/2000/svg", "http://www.w3.org/1998/Math/MathML"}


def _own_sources() -> list[Path]:
    return [
        path
        for path in sorted(STATIC.rglob("*"))
        if path.is_file() and path.suffix in CODE_SUFFIXES and VENDOR not in path.parents
    ]


def test_vendored_module_is_the_pinned_upstream_file():
    for name, digest in VENDORED.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, name


def test_vendored_licences_ship_beside_it():
    for name, marker in LICENCES.items():
        assert marker in (VENDOR / name).read_text(), name
    assert "/vendor/LICENSE-htm.txt" in server.static_files()
    assert "/vendor/LICENSE-preact.txt" in server.static_files()


def test_vendor_holds_only_the_pinned_files():
    names = {path.name for path in VENDOR.iterdir()}
    assert names == set(VENDORED) | set(LICENCES)


def test_own_sources_exist():
    names = {path.relative_to(STATIC).as_posix() for path in _own_sources()}
    assert {"index.html", "app.js", "lib/text.js", "lib/router.js", "lib/api.js"} <= names


@pytest.mark.parametrize("path", _own_sources(), ids=lambda p: p.relative_to(STATIC).as_posix())
def test_source_guard(path):
    # Raw source, comments included: stripping comments could hide a sink after a `//` in a string.
    code = path.read_text()
    for name, pattern in FORBIDDEN.items():
        assert not pattern.search(code), f"{name} in {path.name}"
    assert not STRING_TIMER.search(code), f"string timer in {path.name}"


def test_source_guard_catches_each_sink():
    # The guard's own patterns, checked against a line that uses each sink.
    uses = {
        "document.write": "document.write(x)",
        "eval": "eval(x)",
        "Function": "new Function('return 1')",
    }
    for name, pattern in FORBIDDEN.items():
        assert pattern.search(uses.get(name, f"node.{name} = x")), name
    for evasion in (
        "window.eval(x)",
        "self['eval'](x)",
        "Function ('x')",
        "document . writeln(x)",
    ):
        assert any(p.search(evasion) for p in FORBIDDEN.values()), evasion
    for prose in ("the eval gate", "evals 61/61", "evaluate(x)", "myFunction(x)", "innerHTMLish"):
        assert not any(p.search(prose) for p in FORBIDDEN.values()), prose
    assert STRING_TIMER.search('setTimeout("alert(1)", 0)')
    assert not STRING_TIMER.search("setTimeout(() => go(), 0)")


# Bidi controls and invisible format characters ("Trojan Source"): code that reads differently
# from what runs. Tests write them as escapes (\u202e), never as raw characters.
INVISIBLE = re.compile(
    "[\u00ad\u061c\u180e\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u206f\ufeff"
    "\U000e0000-\U000e007f]"
)


@pytest.mark.parametrize("path", _own_sources(), ids=lambda p: p.relative_to(STATIC).as_posix())
def test_no_raw_bidi_or_invisible_characters(path):
    for number, line in enumerate(path.read_text().splitlines(), 1):
        assert not INVISIBLE.search(line), f"{path.name}:{number}"


def test_console_tests_write_invisible_characters_as_escapes():
    root = Path(__file__).parent
    for path in [*root.glob("ui/*.py"), *root.glob("js/*.mjs"), Path(__file__)]:
        for number, line in enumerate(path.read_text().splitlines(), 1):
            assert not INVISIBLE.search(line), f"{path.name}:{number}"


def test_html_has_no_inline_script_style_or_handlers():
    for path in STATIC.rglob("*.html"):
        source = path.read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", source), path.name
        assert "<style" not in source and " style=" not in source, path.name
        assert not re.search(r"\son[a-z]+\s*=", source), path.name


def test_no_static_file_references_an_external_origin():
    checked = 0
    for path in sorted(STATIC.rglob("*")):
        if not path.is_file() or path.suffix not in CODE_SUFFIXES:
            continue  # fonts are binary; .txt licence texts name URLs but are never loaded
        source = path.read_text()
        for name in NAMESPACES:
            source = source.replace(name, "")
        match = EXTERNAL.search(source)
        assert match is None, (
            f"{path.relative_to(STATIC)}: {source[match.start() : match.start() + 60]!r}"
        )
        checked += 1
    assert checked >= 6


def test_external_origin_pattern():
    for bad in (
        'src="https://cdn.x/y.js"',
        "url(//fonts.example/a.woff2)",
        "import x from '//evil/x.js'",
        "fetch('wss://x')",
    ):
        assert EXTERNAL.search(bad), bad
    for good in ('src="/app.js"', "url(/fonts/a.woff2)", "// a comment line", "a // b"):
        assert not EXTERNAL.search(good), good


def test_csp_requires_trusted_types_and_allows_no_inline_code():
    policy = dict(
        part.strip().split(" ", 1)
        for part in server.SECURITY_HEADERS["Content-Security-Policy"].split(";")
    )
    assert policy["default-src"] == "'none'"
    assert policy["script-src"] == "'self'"
    assert policy["style-src"] == "'self'"
    assert policy["connect-src"] == "'self'"
    assert policy["img-src"] == "'self' data:"
    assert policy["font-src"] == "'self'"
    assert policy["frame-ancestors"] == "'none'"
    assert policy["trusted-types"] == "'none'"
    assert policy["require-trusted-types-for"] == "'script'"
    assert policy["base-uri"] == "'none'" and policy["form-action"] == "'none'"
    assert not any("unsafe" in value for value in policy.values())


def test_every_served_type_is_either_guarded_or_inert():
    # A new served type (say .mjs or .json) must join the guard or be argued inert here.
    assert set(server.STATIC_TYPES) == CODE_SUFFIXES | INERT_SUFFIXES


def test_every_static_file_is_served_with_its_type():
    files = server.static_files()
    for path in STATIC.rglob("*"):
        if path.is_file() and path.suffix in server.STATIC_TYPES:
            key = "/" + path.relative_to(STATIC).as_posix()
            assert key in files, key
    assert files["/vendor/htm-3.1.1-preact-standalone.module.js"][1].startswith("text/javascript")
    # package.json tells `node --test` the modules are ES modules; it is never served.
    assert "/package.json" not in files
