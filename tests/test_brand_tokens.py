"""Brand tokens (#817, console spec §8.5; tokens v2, #1113 §5.5): drift between
website/brand.css and the console's snapshot and glass copy, and WCAG contrast of the palettes.

website/brand.css is the design-system source of truth (#583). The console cannot import it
(no build step), so it ships a snapshot of the `:root` (light) and `.dark` blocks at
src/agentbreeder/center/static/brand.css; this test fails when the two drift. The contrast
checks compute WCAG 2.x ratios from the token values themselves (hex, rgb[a] and oklch, with
alpha composited over the surface below), so a token edit that breaks AA fails here.
"""

import hashlib
import math
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "website" / "brand.css"
SNAPSHOT = ROOT / "src" / "agentbreeder" / "center" / "static" / "brand.css"
CONSOLE_STYLE = ROOT / "src" / "agentbreeder" / "center" / "static" / "style.css"
FONTS = ROOT / "src" / "agentbreeder" / "center" / "static" / "fonts"

# --- parsing ---------------------------------------------------------------------------------


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _block(css: str, selector: str) -> dict[str, str]:
    """The `--name: value` declarations of the top-level block whose selector is `selector`."""
    css = _strip_comments(css)
    match = re.search(rf"(?m)^{re.escape(selector)}\s*\{{", css)
    assert match, f"no {selector} block"
    start = match.end()
    end = css.index("}", start)
    body = css[start:end]
    tokens = {}
    for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body):
        assert name not in tokens, f"{selector} declares {name} twice"
        tokens[name] = " ".join(value.split())
    return tokens


def _palettes(path: Path) -> dict[str, dict[str, str]]:
    css = path.read_text()
    return {"light": _block(css, ":root"), "dark": _block(css, ".dark")}


def _body(css: str, start: int) -> str:
    """The text between the `{` at or after `start` and its matching `}`."""
    open_at = css.index("{", start)
    depth = 0
    for i in range(open_at, len(css)):
        depth += {"{": 1, "}": -1}.get(css[i], 0)
        if depth == 0:
            return css[open_at + 1 : i]
    raise AssertionError("unbalanced braces")


def _rules(body: str) -> list[tuple[str, str]]:
    """The top-level `(prelude, body)` rules of a block body."""
    rules, i = [], 0
    while (open_at := body.find("{", i)) != -1:
        inner = _body(body, open_at)
        rules.append((" ".join(body[i:open_at].split()), inner))
        i = open_at + len(inner) + 2
    return rules


def _theme(css: str) -> dict[str, str]:
    """The `--name: value` declarations of the `@theme inline` block."""
    css = _strip_comments(css)
    body = _body(css, css.index("@theme inline"))
    return {n: " ".join(v.split()) for n, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body)}


def _glass_rules(css: str) -> list[str]:
    """The `.glass` rules and the at-rules holding them inside the first `@layer utilities`,
    normalized (comments dropped, whitespace collapsed)."""
    css = _strip_comments(css)
    layer = _body(css, css.index("@layer utilities"))
    out = []
    for prelude, body in _rules(layer):
        if ".glass" in prelude or ".glass" in body:
            out.append(re.sub(r"\s+", " ", f"{prelude} {{{body}}}").replace("{ ", "{"))
    return out


# --- colour maths (WCAG 2.x relative luminance) ----------------------------------------------

RGBA = tuple[float, float, float, float]  # gamma-encoded sRGB 0..1, alpha 0..1


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_gamma(c: float) -> float:
    c = min(1.0, max(0.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _alpha(text: str | None) -> float:
    if text is None:
        return 1.0
    text = text.strip()
    return float(text[:-1]) / 100 if text.endswith("%") else float(text)


_OKLCH = r"oklch\(\s*([\d.]+%?)\s+([\d.]+)\s+([\d.]+)\s*(?:/\s*([\d.]+%?))?\)"


def parse_color(value: str) -> RGBA:
    value = value.strip().lower()
    if value.startswith("#"):
        hexes = value[1:]
        if len(hexes) in (3, 4):
            hexes = "".join(ch * 2 for ch in hexes)
        channels = [int(hexes[i : i + 2], 16) / 255 for i in range(0, len(hexes), 2)]
        return (*channels[:3], channels[3] if len(channels) == 4 else 1.0)
    if match := re.fullmatch(r"rgba?\(([^)]*)\)", value):
        parts = [p for p in re.split(r"[\s,/]+", match.group(1)) if p]
        r, g, b = (float(p) / 255 for p in parts[:3])
        return (r, g, b, _alpha(parts[3] if len(parts) > 3 else None))
    if match := re.fullmatch(
        r"oklch\(\s*([\d.]+%?)\s+([\d.]+)\s+([\d.]+)\s*(?:/\s*([\d.]+%?))?\)", value
    ):
        lightness, chroma, hue, alpha = match.groups()
        L = float(lightness[:-1]) / 100 if lightness.endswith("%") else float(lightness)
        a = float(chroma) * math.cos(math.radians(float(hue)))
        b = float(chroma) * math.sin(math.radians(float(hue)))
        l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
        m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
        s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
        r = 4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
        g = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
        bl = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_
        return (_to_gamma(r), _to_gamma(g), _to_gamma(bl), _alpha(alpha))
    raise ValueError(f"unsupported colour: {value}")


def _over(top: RGBA, below: RGBA) -> RGBA:
    """Source-over compositing in gamma-encoded sRGB, as browsers do by default."""
    a = top[3]
    return (*(a * t + (1 - a) * u for t, u in zip(top[:3], below[:3], strict=True)), 1.0)


def _luminance(color: RGBA) -> float:
    r, g, b = (_to_linear(c) for c in color[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str, base: str | None = None) -> float:
    """WCAG ratio of `fg` on `bg`; a translucent `bg` is composited over `base` first."""
    below = parse_color(bg)
    if below[3] < 1:
        assert base is not None, f"{bg} is translucent; give the surface below it"
        below = _over(below, parse_color(base))
    top = _over(parse_color(fg), below)
    hi, lo = sorted((_luminance(top), _luminance(below)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_colour_maths_matches_known_values():
    assert contrast("#000", "#fff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)
    assert parse_color("oklch(1 0 0)")[:3] == pytest.approx((1, 1, 1), abs=1e-6)
    assert parse_color("oklch(0 0 0)")[:3] == pytest.approx((0, 0, 0), abs=1e-6)
    # Reference values: sRGB red is oklch(62.796% 0.25768 29.2339) (CSS Color 4) and Tailwind
    # v4 defines zinc-500 (#71717a) as oklch(55.2% 0.016 285.938).
    for value, expected in (
        ("oklch(62.796% 0.25768 29.2339)", (0xFF, 0x00, 0x00)),
        ("oklch(0.552 0.016 285.938)", (0x71, 0x71, 0x7A)),
    ):
        got = [round(c * 255) for c in parse_color(value)[:3]]
        assert all(abs(g - e) <= 2 for g, e in zip(got, expected, strict=True)), (value, got)
    assert parse_color("rgba(255, 255, 255, 0.07)")[3] == pytest.approx(0.07)
    assert parse_color("oklch(1 0 0 / 7%)")[3] == pytest.approx(0.07)
    with pytest.raises(ValueError):
        parse_color("hsl(0 0% 0%)")


# --- drift ------------------------------------------------------------------------------------


def test_console_snapshot_matches_the_website_source():
    source, snapshot = _palettes(SOURCE), _palettes(SNAPSHOT)
    for mode in ("light", "dark"):
        missing = sorted(set(source[mode]) - set(snapshot[mode]))
        extra = sorted(set(snapshot[mode]) - set(source[mode]))
        both = source[mode].keys() & snapshot[mode].keys()
        changed = sorted(n for n in both if source[mode][n] != snapshot[mode][n])
        assert not (missing or extra or changed), (
            f"{mode}: re-copy website/brand.css into center/static/brand.css "
            f"(missing {missing}, extra {extra}, changed {changed})"
        )


def test_console_snapshot_is_self_contained():
    css = _strip_comments(SNAPSHOT.read_text())
    # _block reads the first matching rule only, so the copy must hold exactly :root and .dark.
    assert css.count("{") == 2 and css.count("}") == 2
    assert len(re.findall(r"(?m)^:root\s*\{", css)) == 1
    assert len(re.findall(r"(?m)^\.dark\s*\{", css)) == 1
    assert "@import" not in css and "url(" not in css and "http" not in css
    assert "@theme" not in css and "@layer" not in css  # Tailwind-only syntax stays in the source
    # The glass fallbacks live in style.css: an at-rule here would break the two-block rule.
    assert "@supports" not in css and "@media" not in css


def test_console_glass_matches_source():
    """style.css copies the .glass rules and their three fallback at-rules from the source's
    `@layer utilities` (design v2 §6.2), inside a native `@layer utilities` of its own."""
    source = _glass_rules(SOURCE.read_text())
    assert [r.split(" {")[0] for r in source] == [
        ".glass, .glass-strong",
        ".glass",
        ".glass-strong",
        "@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px)))",
        "@media (prefers-reduced-transparency: reduce)",
        "@media (forced-colors: active)",
    ]
    console = CONSOLE_STYLE.read_text()
    # One layer only: _glass_rules reads the first, so a second copy would go unchecked.
    assert _strip_comments(console).count("@layer utilities") == 1
    assert _glass_rules(console) == source


def test_glass_copy_check_sees_a_change(tmp_path):
    changed = tmp_path / "style.css"
    changed.write_text(CONSOLE_STYLE.read_text().replace("saturate(160%)", "saturate(180%)", 1))
    assert _glass_rules(changed.read_text()) != _glass_rules(SOURCE.read_text())


def test_drift_check_sees_a_changed_value(tmp_path):
    changed = tmp_path / "brand.css"
    changed.write_text(
        SNAPSHOT.read_text().replace("--text-muted: #a3a3a3", "--text-muted: #737373")
    )
    assert _palettes(changed)["dark"] != _palettes(SOURCE)["dark"]


def test_both_palettes_define_the_same_tokens():
    palettes = _palettes(SOURCE)
    assert set(palettes["light"]) == set(palettes["dark"])


# Colour tokens added since #817 (#817 and design v2 §5.3); each has a `--color-*` mapping.
NEW_COLOUR_TOKENS = {
    "--control-border",
    "--success",
    "--success-subtle",
    "--warning",
    "--warning-subtle",
    "--info",
    "--info-subtle",
    "--brand",
    "--border-strong",
    "--destructive-subtle",
    "--tint-green",
    "--tint-blue",
    "--tint-violet",
    "--tint-amber",
    "--glass",
    "--glass-strong",
    "--glass-fallback",
    "--glass-border",
    "--glass-highlight",
    "--scrim",
    "--code-background",
    "--syntax-key",
    "--syntax-string",
    "--syntax-number",
    "--syntax-comment",
}
ELEVATION = {
    "--elev-1": "--shadow-xs",
    "--elev-2": "--shadow-sm",
    "--elev-3": "--shadow-md",
    "--elev-4": "--shadow-lg",
}
NEW_TOKENS = {"--text-muted", "--glass-blur", *NEW_COLOUR_TOKENS, *ELEVATION}


def test_new_tokens_exist_in_both_palettes_and_the_theme():
    palettes = _palettes(SOURCE)
    for mode in ("light", "dark"):
        assert NEW_TOKENS <= set(palettes[mode]), mode
    theme = _theme(SOURCE.read_text())
    for name in NEW_COLOUR_TOKENS:
        assert theme.get(f"--color-{name[2:]}") == f"var({name})", name
    for elev, shadow in ELEVATION.items():
        assert theme.get(shadow) == f"var({elev})", shadow
    assert theme.get("--blur-glass") == "var(--glass-blur)"


def test_no_theme_palette_name_collision():
    """A palette token named like an `@theme` token (`--shadow-sm: var(--shadow-sm)`) is a cycle,
    and Cloud's drift check keys `.dark` and `@theme` by bare name, so one would mask the other."""
    palettes = _palettes(SOURCE)
    theme = _theme(SOURCE.read_text())
    for mode in ("light", "dark"):
        assert not set(palettes[mode]) & set(theme), mode


def test_values_have_no_braces_and_blocks_set_color_scheme():
    css = _strip_comments(SOURCE.read_text())
    for mode, palette in _palettes(SOURCE).items():
        assert not any("{" in v or "}" in v for v in palette.values()), mode
    for selector, scheme in ((":root", "light"), (".dark", "dark")):
        body = _body(css, re.search(rf"(?m)^{re.escape(selector)}\s*\{{", css).start())
        assert f"color-scheme: {scheme};" in body, selector


# --- contrast ---------------------------------------------------------------------------------

AA_TEXT = 4.5
NON_TEXT = 3.0  # WCAG 1.4.11: control boundaries, focus rings

SURFACES = ("--background", "--card", "--secondary")
# --text-muted is for every surface, the sidebar's hover/active one included. Status text
# (--success, --warning, --info) is not for --sidebar-accent (4.21:1 in light).
MUTED_SURFACES = (*SURFACES, "--sidebar-accent")


def _text_pairs(mode: str) -> list[tuple[str, str]]:
    pairs = [
        ("--foreground", "--background"),
        ("--card-foreground", "--card"),
        ("--popover-foreground", "--popover"),
        ("--secondary-foreground", "--secondary"),
        ("--accent-foreground", "--accent"),
        ("--sidebar-foreground", "--sidebar"),
        ("--sidebar-accent-foreground", "--sidebar-accent"),
        ("--primary-foreground", "--primary"),
        ("--sidebar-primary-foreground", "--sidebar-primary"),
    ]
    pairs += [("--text-muted", s) for s in (*MUTED_SURFACES, "--sidebar")]
    pairs += [("--muted-foreground", s) for s in (*SURFACES, "--sidebar")]
    pairs += [("--brand", s) for s in SURFACES]
    return pairs


def _status_pairs() -> list[tuple[str, str]]:
    """Status text (design v2 §5.4): on every opaque surface and its own -subtle fill, never on
    --sidebar-accent (light --success is 4.21:1 there), except --destructive, which is."""
    pairs = [("--destructive", s) for s in (*MUTED_SURFACES, "--destructive-subtle")]
    for status in ("--success", "--warning", "--info"):
        pairs += [(status, s) for s in SURFACES] + [(status, f"{status}-subtle")]
    return pairs


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_text_tokens_pass_aa(mode):
    tokens = _palettes(SOURCE)[mode]
    failures = []
    for fg, bg in _text_pairs(mode):
        ratio = contrast(tokens[fg], tokens[bg], tokens["--background"])
        if ratio < AA_TEXT:
            failures.append(f"{fg} on {bg}: {ratio:.2f}")
    assert not failures, failures


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_status_tokens_pass_aa(mode):
    tokens = _palettes(SOURCE)[mode]
    failures = []
    for fg, bg in _status_pairs():
        ratio = contrast(tokens[fg], tokens[bg])
        if ratio < AA_TEXT:
            failures.append(f"{fg} on {bg}: {ratio:.2f}")
    assert not failures, failures


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_control_border_and_ring_pass_non_text_contrast(mode):
    tokens = _palettes(SOURCE)[mode]
    failures = []
    for fg in ("--control-border", "--ring"):
        for bg in MUTED_SURFACES:
            ratio = contrast(tokens[fg], tokens[bg])
            if ratio < NON_TEXT:
                failures.append(f"{fg} on {bg}: {ratio:.2f}")
    assert not failures, failures


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_syntax_tokens_pass_aa(mode):
    tokens = _palettes(SOURCE)[mode]
    for name in ("--syntax-key", "--syntax-string", "--syntax-number", "--syntax-comment"):
        assert contrast(tokens[name], tokens["--code-background"]) >= AA_TEXT, name


TINTS = ("--tint-green", "--tint-blue", "--tint-violet", "--tint-amber")


def _glass_backdrops(tokens: dict[str, str]) -> dict[str, RGBA]:
    """What can sit behind a glass panel (design v2 §5.5): the opaque surfaces, each tint
    composited over --background, and --foreground (dark content behind the glass)."""
    backdrops = {s: parse_color(tokens[s]) for s in (*SURFACES, "--foreground")}
    for tint in TINTS:
        backdrops[tint] = _over(parse_color(tokens[tint]), parse_color(tokens["--background"]))
    return backdrops


def _hex(color: RGBA) -> str:
    return "#" + "".join(f"{round(c * 255):02x}" for c in color[:3])


def _glass_minimum(tokens: dict[str, str], glass: str, fg: str) -> float:
    return min(
        contrast(tokens[fg], _hex(_over(parse_color(tokens[glass]), below)))
        for below in _glass_backdrops(tokens).values()
    )


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("glass", ["--glass", "--glass-strong"])
def test_glass_text_passes_aa(mode, glass):
    """Text on glass is only --foreground or --text-muted; control edges and the focus ring
    still need 3:1 (design v2 §5.4)."""
    tokens = _palettes(SOURCE)[mode]
    failures = []
    for fg, floor in (
        ("--foreground", AA_TEXT),
        ("--text-muted", AA_TEXT),
        ("--control-border", NON_TEXT),
        ("--ring", NON_TEXT),
    ):
        ratio = _glass_minimum(tokens, glass, fg)
        if ratio < floor:
            failures.append(f"{fg} on {glass}: {ratio:.2f}")
    assert not failures, failures


def test_glass_minimums_match_the_spec():
    """The §5.4 table's glass rows, so a spec number and the tokens cannot drift apart."""
    expected = {
        ("light", "--glass"): (13.89, 5.48, 3.33, 3.63),
        ("dark", "--glass"): (10.17, 4.72, 3.01, 4.68),
        ("light", "--glass-strong"): (16.61, 6.55, 3.98, 4.34),
        ("dark", "--glass-strong"): (14.56, 6.76, 4.32, 6.70),
    }
    palettes = _palettes(SOURCE)
    for (mode, glass), ratios in expected.items():
        got = tuple(
            _glass_minimum(palettes[mode], glass, fg)
            for fg in ("--foreground", "--text-muted", "--control-border", "--ring")
        )
        assert got == pytest.approx(ratios, abs=0.05), (mode, glass, got)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_text_on_tints_passes_aa(mode):
    tokens = _palettes(SOURCE)[mode]
    for tint in TINTS:
        below = _hex(_over(parse_color(tokens[tint]), parse_color(tokens["--background"])))
        for fg in ("--foreground", "--text-muted"):
            assert contrast(tokens[fg], below) >= AA_TEXT, (tint, fg)


# --- fonts ------------------------------------------------------------------------------------

FONT_FILES = {
    "Geist-Variable.woff2": "e24cec106619c03f0b3519e31b9bc55e0d5e926b6a95b8d798cd8cef215b1505",
    "GeistMono-Variable.woff2": "5f687a5dd4c87da13deaff9f6b9503d5e62249ff501265a96b134565f9aa8c87",
}


def test_vendored_fonts_are_pinned_and_licensed():
    for name, digest in FONT_FILES.items():
        data = (FONTS / name).read_bytes()
        assert data[:4] == b"wOF2", name
        assert hashlib.sha256(data).hexdigest() == digest, name
    licence = (FONTS / "OFL.txt").read_text()
    assert "SIL OPEN FONT LICENSE Version 1.1" in licence
    assert "Vercel" in licence
    assert sorted(p.name for p in FONTS.iterdir()) == sorted([*FONT_FILES, "OFL.txt"])
