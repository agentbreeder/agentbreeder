"""The console on design v2 (#1116, spec docs/superpowers/specs/2026-11-16-design-system-v2.md):
theme.js before first paint, glass only on the header, menus and dialogs, control boundaries on
--control-border (§8.1), and --primary never meaning "healthy" (§3, DS1)."""

from __future__ import annotations

import re

import pytest

from agentbreeder.center import server

STATIC = server.STATIC
STYLE = STATIC / "style.css"
THEME = STATIC / "theme.js"
APP = STATIC / "app.js"
INDEX = STATIC / "index.html"


def _rules(css: str) -> list[tuple[str, str]]:
    """(selector, declarations) for every innermost rule, with comments removed and at-rule
    preludes dropped (a rule inside @media keeps its own selector)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = []
    stack: list[str] = []
    start = 0
    for index, char in enumerate(css):
        if char == "{":
            stack.append(css[start:index].strip().split(";")[-1].strip())
            start = index + 1
        elif char == "}":
            body = css[start:index].strip()
            selector = stack.pop() if stack else ""
            if body and not selector.startswith("@") and ":" in body:
                rules.append((" ".join(selector.split()), body))
            start = index + 1
    return rules


def _declarations(body: str) -> dict[str, str]:
    out = {}
    for part in body.split(";"):
        if ":" in part:
            name, value = part.split(":", 1)
            out[name.strip()] = value.strip()
    return out


# -- theme.js --


def test_theme_script_runs_before_the_stylesheets_as_a_classic_script():
    html = INDEX.read_text()
    tag = re.search(r"<script[^>]*src=\"/theme\.js\"[^>]*>", html)
    assert tag, "index.html must load /theme.js"
    assert "type=" not in tag.group(0)  # a classic script: modules are deferred
    head = html.split("</head>", 1)[0]
    assert tag.start() < head.index('href="/brand.css"') < head.index('href="/style.css"')


def test_theme_script_guards_storage_and_follows_the_system():
    source = THEME.read_text()
    assert "try" in source and "catch" in source
    assert "matchMedia" in source and "prefers-color-scheme: dark" in source
    assert "colorScheme" in source
    for sink in ("innerHTML", "eval", "document.write", "className ="):
        assert sink not in source, sink


def test_theme_script_and_app_accept_the_same_three_values():
    theme = THEME.read_text()
    accepted = set(re.findall(r'stored === "([a-z]+)"', theme))
    assert accepted == {"system", "light", "dark"}
    match = re.search(r"const THEMES = \[([^\]]*)\]", APP.read_text())
    assert match, "app.js must declare THEMES"
    assert set(re.findall(r'"([a-z]+)"', match.group(1))) == accepted


# -- glass --

GLASS = {
    "header.top": "ui/shell.js",
    ".menu-body": "ui/shell.js",
    "dialog.confirm": "ui/components.js",
}


@pytest.mark.parametrize("selector", sorted(GLASS))
def test_glass_surfaces_carry_the_class_and_leave_its_paint_to_the_layer(selector):
    """A layered .glass loses to any unlayered rule, so the element's own rules must not set a
    background or a border colour (spec §13, Layers)."""
    tag, _, cls = selector.partition(".")
    markup = (STATIC / GLASS[selector]).read_text()
    assert re.search(rf'class="[^"]*\b{re.escape(cls)}\b[^"]*\bglass-strong\b', markup), selector
    assert any(sel == selector for sel, _ in _rules(STYLE.read_text())), selector
    assert glass_paint_problems(STYLE.read_text(), selector) == []


PAINT = ("background", "background-color", "background-image", "border", "border-color")


def glass_paint_problems(css: str, selector: str) -> list[str]:
    """Unlayered declarations that would beat the layered glass paint on `selector`, in any rule
    whose selector list names it (alone, grouped, or with a state such as :hover)."""
    problems = []
    for selectors, body in _rules(css):
        parts = [part.strip() for part in selectors.split(",")]
        if not any(re.fullmatch(rf"{re.escape(selector)}(?:[:\[].*)?", part) for part in parts):
            continue
        problems += [f"{selectors} sets {name}" for name in _declarations(body) if name in PAINT]
    return problems


def test_glass_paint_check_sees_grouped_and_state_rules():
    assert glass_paint_problems("header.top, .x { background: red; }", "header.top")
    assert glass_paint_problems(".menu-body:hover { border-color: red; }", ".menu-body")
    assert not glass_paint_problems("header.top .brand { background: red; }", "header.top")
    assert not glass_paint_problems("header.top { border-width: 0 0 1px; }", "header.top")


def test_nothing_else_is_glass():
    """Glass is chrome only (§6.1): cards, tables, logs and forms stay opaque."""
    users = sorted(
        path.name
        for path in STATIC.rglob("*.js")
        if "vendor" not in path.parts and re.search(r"\bglass(?:-strong)?\b", path.read_text())
    )
    assert users == ["components.js", "shell.js", "signin.js"]  # signin.js: the re-auth dialog


def test_style_has_no_colour_literals():
    css = re.sub(r"/\*.*?\*/", "", STYLE.read_text(), flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch)\(", css)


# -- §8.1 control boundaries --

CONTROL = re.compile(
    r"(?<![\w-])(?:input|select|textarea|button)(?![\w-])|\.pill\b|\.menu summary|\.file-tab\b"
    r"|\.theme-toggle\b|\[role=\"radio\"\]|\.editor\b|\.build-link\b"
)
DECORATIVE = re.compile(r"var\(--(?:border|input)\)")


def control_border_problems(css: str) -> list[str]:
    problems = []
    for selector, body in _rules(css):
        if not CONTROL.search(selector):
            continue
        for name, value in _declarations(body).items():
            if name.startswith("border") and DECORATIVE.search(value):
                problems.append(f"{selector} {{ {name}: {value} }}")
    return problems


def test_control_boundaries_use_the_control_border_token():
    assert control_border_problems(STYLE.read_text()) == []


def test_control_border_check_sees_a_decorative_border():
    bad = "input.x { border: 1px solid var(--border); }\n.pill { border-color: var(--input); }"
    assert len(control_border_problems(bad)) == 2
    assert control_border_problems(".card { border: 1px solid var(--border); }") == []


# -- --primary never means healthy --

HEALTHY = re.compile(r"\.(?:passed|ok|done|healthy|success|fresh|compliant)\b")


def primary_as_healthy(css: str) -> list[str]:
    return [
        f"{selector} {{ {body} }}"
        for selector, body in _rules(css)
        if HEALTHY.search(selector) and "var(--primary" in body
    ]


def test_no_rule_uses_primary_to_mean_healthy():
    """--primary is monochrome (DS1): "passed" and "healthy" are the gate line and --success."""
    assert primary_as_healthy(STYLE.read_text()) == []


def test_primary_check_sees_a_healthy_rule():
    assert primary_as_healthy(".chip.ok { color: var(--primary); }")
    assert primary_as_healthy(
        "@media (x) { .gate-step.passed { border-color: var(--primary-foreground); } }"
    )
    assert primary_as_healthy(".verify.passed, .identity.passed { color: var(--primary); }")
    assert primary_as_healthy(".step.done .step-state { color: var(--primary); }")
    assert not primary_as_healthy("button.action { background: var(--primary); }")


def test_links_are_text_coloured_and_underlined():
    rules = dict(_rules(STYLE.read_text()))
    names = _declarations(rules["a"])
    assert names["color"] == "var(--foreground)"
    assert names["text-decoration"] == "underline"
