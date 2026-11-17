"""Website on the v2 tokens (design v2 #1113 §8.5, §10.2; step W #1115).

Components take colour from the shared tokens in website/brand.css, never from literals, so a
token change reaches every page and both themes. These checks read the source files only; the
fumadocs mapping check reads the installed package when `website/node_modules` exists and skips
otherwise (the Python CI job does not install the website's packages).
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEBSITE = ROOT / "website"
GLOBALS = WEBSITE / "app" / "globals.css"
BRAND = WEBSITE / "brand.css"
FUMADOCS_CSS = WEBSITE / "node_modules" / "fumadocs-ui" / "dist" / "style.css"

# Files that may hold colour literals (§8.5): the OG image source and the logo. The logo has
# none today; the allow-list keeps the spec's exception for a future raster mark.
ALLOWED = {
    WEBSITE / "components" / "logo.tsx",
    WEBSITE / "app" / "opengraph-image.tsx",
}

LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\boklch\(|\bhsla?\(")
# Tailwind's fixed palette and arbitrary colour values bypass the tokens just like a hex literal.
_COLOURS = (
    r"(?:white|black|(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|"
    r"teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3})"
)
FIXED_PALETTE = re.compile(
    r"\b(?:text|bg|border(?:-[xytrblse])?|divide|fill|stroke|ring|ring-offset|outline|decoration|"
    r"from|via|to|shadow|accent|caret|placeholder)-" + _COLOURS + r"\b"
)
# CSS named colours in SVG attributes or style values (a literal by another name).
NAMED_COLOUR_ATTR = re.compile(
    r"""\b(?:fill|stroke|color|stopColor|floodColor)=["'](?!none\b|currentColor\b|url\()[a-zA-Z]+["']"""
)
# The legacy site palette that v2 removed from globals.css (§10.2).
LEGACY_VARS = re.compile(
    r"var\(--(?:bg|bg-base|bg-surface|bg-elevated|border-hover|text|text-dim|accent-dim|"
    r"accent-border|purple|font-bricolage)\)"
)
# Any colour-bearing key inside an inline style object, wherever it sits in the object.
INLINE_STYLE = re.compile(r"style=\{\{(.*?)\}\}", re.S)
COLOUR_KEYS = re.compile(
    r"\b(?:color|background|backgroundColor|borderColor|fill|stroke|boxShadow|outlineColor)\s*:"
)


def _tsx_files() -> list[Path]:
    files = sorted((WEBSITE / "app").rglob("*.tsx")) + sorted(
        (WEBSITE / "components").rglob("*.tsx")
    )
    assert files, "no website .tsx files found"
    return files


def _code(path: Path) -> str:
    """Source without comments, so prose in a comment never trips a check."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"\{/\*.*?\*/\}", "", text, flags=re.S)
    return re.sub(r"(?m)(^|\s)//.*$", r"\1", text)


@pytest.mark.parametrize("path", _tsx_files(), ids=lambda p: str(p.relative_to(WEBSITE)))
def test_no_colour_literals_in_components(path: Path) -> None:
    if path in ALLOWED:
        return
    code = _code(path)
    assert not LITERAL.findall(code), f"{path.name}: colour literal; use a token class"
    assert not FIXED_PALETTE.findall(code), (
        f"{path.name}: fixed Tailwind colour; use a token class"
    )
    assert not LEGACY_VARS.findall(code), f"{path.name}: legacy site variable; use a v2 token"
    assert not NAMED_COLOUR_ATTR.findall(code), (
        f"{path.name}: named colour attribute; use a token class"
    )
    for body in INLINE_STYLE.findall(code):
        assert not COLOUR_KEYS.search(body), f"{path.name}: colour in an inline style prop"


def test_no_google_fonts() -> None:
    """Bricolage is dropped (DS2); no build-time Google Fonts download remains."""
    hits = [
        p
        for p in list(WEBSITE.glob("*.ts"))
        + list(WEBSITE.glob("*.mjs"))
        + _tsx_files()
        + sorted((WEBSITE / "lib").rglob("*.ts"))
        if "next/font/google" in p.read_text(encoding="utf-8")
    ]
    assert not hits, f"next/font/google imported in {hits}"
    css = GLOBALS.read_text(encoding="utf-8")
    assert "bricolage" not in css.lower()


ALLOWED_HOSTS = {
    "www.agentbreeder.io",
    "agentbreeder.io",
    "github.com",
    "www.linkedin.com",
    "www.w3.org",
    "schema.org",
    "lucide.dev",
    "127.0.0.1",  # loopback, printed in the hero's recorded CLI session
    "vercel.com",  # the hosting provider's privacy policy, linked from /privacy
}


def test_no_third_party_assets() -> None:
    """§11: no new third-party script, stylesheet, font or CDN. Every absolute URL in the site's
    code points at the project, its repository or a namespace; none is loaded as a resource."""
    sources = _tsx_files() + sorted((WEBSITE / "lib").rglob("*.ts")) + [GLOBALS, BRAND]
    sources += [WEBSITE / "next.config.mjs", WEBSITE / "source.config.ts"]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        hosts = set(re.findall(r"https?://([\w.-]+)", text))
        assert hosts <= ALLOWED_HOSTS, (
            f"{path.name}: unexpected hosts {sorted(hosts - ALLOWED_HOSTS)}"
        )
        assert not re.search(r"@import\s+url\(\s*['\"]?https?:", text), (
            f"{path.name}: remote @import"
        )
        assert not re.search(r"<script[^>]+src=", text), f"{path.name}: external <script>"


def test_globals_has_no_legacy_palette_or_forced_theme() -> None:
    css = re.sub(r"/\*.*?\*/", "", GLOBALS.read_text(encoding="utf-8"), flags=re.S)
    for name in (
        "--bg",
        "--bg-surface",
        "--bg-elevated",
        "--text-dim",
        "--accent-dim",
        "--purple",
    ):
        assert not re.search(rf"{re.escape(name)}\s*:", css), f"globals.css still declares {name}"
    # brand.css owns these three; a site redefinition would shadow it (§1).
    for name in ("--border", "--accent", "--text-muted"):
        assert not re.search(rf"(?<![\w-]){re.escape(name)}\s*:", css), (
            f"globals.css shadows {name}"
        )
    # The pinned Tailwind shadows from step T are gone; --shadow-* come from --elev-*.
    assert "--shadow-" not in css
    layout = (WEBSITE / "app" / "layout.tsx").read_text(encoding="utf-8")
    assert "forcedTheme" not in layout
    compact = re.sub(r"\s+", "", layout)
    assert "defaultTheme:'system'" in compact
    assert "enableSystem:true" in compact
    assert "themes:['light','dark']" in compact
    # theme-color comes from lib/theme.ts in the viewport export.
    assert "THEME_COLOR.light" in layout and "THEME_COLOR.dark" in layout
    assert "export const viewport" in layout
    docs = (WEBSITE / "app" / "docs" / "layout.tsx").read_text(encoding="utf-8")
    assert "themeSwitch={{ enabled: false" not in docs
    # The docs mount the site's radiogroup in fumadocs' theme-switch slot (§10.1).
    assert "themeSwitch: DocsThemeSwitch" in docs


def test_every_fumadocs_colour_token_is_mapped() -> None:
    if not FUMADOCS_CSS.exists():
        pytest.skip("website/node_modules not installed")
    declared = set(
        re.findall(r"(--color-fd-[\w-]+)\s*:", FUMADOCS_CSS.read_text(encoding="utf-8"))
    )
    assert declared, "fumadocs declares no --color-fd-* tokens; the parser is stale"
    css = re.sub(r"/\*.*?\*/", "", GLOBALS.read_text(encoding="utf-8"), flags=re.S)
    mapped = set(re.findall(r"(--color-fd-[\w-]+)\s*:\s*var\(--[\w-]+\)", css))
    assert not declared - mapped, f"unmapped fumadocs tokens: {sorted(declared - mapped)}"


def test_fumadocs_mapping_points_at_brand_tokens() -> None:
    brand = BRAND.read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", GLOBALS.read_text(encoding="utf-8"), flags=re.S)
    targets = set(re.findall(r"--color-fd-[\w-]+\s*:\s*var\((--[\w-]+)\)", css))
    assert targets
    missing = [t for t in targets if not re.search(rf"(?m)^\s*{re.escape(t)}\s*:", brand)]
    assert not missing, f"fumadocs mapped to tokens brand.css does not define: {missing}"


def test_theme_color_matches_background() -> None:
    """theme-color (lib/theme.ts) equals --background in each palette."""
    theme = (WEBSITE / "lib" / "theme.ts").read_text(encoding="utf-8")
    light = re.search(r"light:\s*'(#[0-9a-fA-F]{6})'", theme).group(1)
    dark = re.search(r"dark:\s*'(#[0-9a-fA-F]{6})'", theme).group(1)
    brand = re.sub(r"/\*.*?\*/", "", BRAND.read_text(encoding="utf-8"), flags=re.S)
    root = brand[brand.index(":root {") : brand.index("}", brand.index(":root {"))]
    dark_block = brand[brand.index(".dark {") : brand.index("}", brand.index(".dark {"))]
    assert re.search(rf"--background:\s*{light};", root, re.I)
    assert re.search(rf"--background:\s*{dark};", dark_block, re.I)


def test_hero_motion_rules() -> None:
    """§9: no inline <style> in components; the LCP headline never starts hidden; loops pause."""
    hero = (WEBSITE / "components" / "hero.tsx").read_text(encoding="utf-8")
    for path in _tsx_files():
        assert "<style>" not in path.read_text(encoding="utf-8"), f"inline <style> in {path.name}"
    css = GLOBALS.read_text(encoding="utf-8")
    # The headline animates transform only.
    rise = re.search(r"@keyframes ab-rise\s*\{(.*?)\n\}", css, re.S).group(1)
    assert "opacity" not in rise
    assert "hero-title" in hero and 'className="hero-stagger"' in hero
    assert hero.index("hero-title") < hero.index('className="hero-stagger"')
    # Animations exist only when the user has not asked for reduced motion: parse that media
    # block by brace depth and check the selectors are inside it.
    start = css.index("@media (prefers-reduced-motion: no-preference)")
    open_at = css.index("{", start)
    depth, end = 0, open_at
    for end in range(open_at, len(css)):
        depth += {"{": 1, "}": -1}.get(css[end], 0)
        if depth == 0:
            break
    block = css[open_at:end]
    for selector in (".hero-title", ".hero-stagger > *", "[data-paused='false'] .gate-run"):
        assert selector in block, f"{selector} animates outside the no-preference block"
    outside = css[:start] + css[end:]
    assert not re.search(r"animation:\s*ab-(?:rise|reveal|gate-travel|underline)", outside)
    assert "IntersectionObserver" in hero and "visibilitychange" in hero
    assert "[data-paused='true'] .gate-run" in css


def test_waitlist_makes_no_launch_date_claim() -> None:
    """#492: the Cloud waitlist names no quarter or date."""
    form = (WEBSITE / "components" / "waitlist-form.tsx").read_text(encoding="utf-8")
    assert not re.search(r"\bQ[1-4]\s+20\d\d\b", form)
    assert "access opens" not in form
