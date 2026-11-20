"""Registry ▸ Skills (#1059 UI-R, composition console spec §9) over a copy of Northwind with skill
versions published into its registry: the list's Code column, a version's detail (the "sent on
every model call" block, the Formatted / Source toggle, references, scripts), the diff's
description line, the safe renderer in a real DOM (the XSS corpus: allowed elements and
attributes only, no request leaves the console) and the Import review for an instruction pack and
a script skill. The fake CLI runs the real `registry list`, `show`, `diff` and `import`."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from playwright.sync_api import expect

from agentbreeder import registry

from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_registry import open_northwind, row

pytestmark = pytest.mark.ui

DESCRIPTION = "How to decide and explain refunds."
SECURE = "https" + "://"
SCRIPTS_YAML = """scripts:
  - name: prorate
    path: scripts/prorate.py
    description: "Pro-rated refund."
    parameters:
      type: object
      properties: {price: {type: number}}
      required: [price]
"""
BODY = """# Refunds

Be **fair** and *brief*. See the [policy](https://example.com/policy "Refund policy").

- Check the order
- Explain the amount

&amp; stays as written.
"""
# The renderer's corpus (tests/js/markdown.test.mjs has the full one): every shape must come out
# as text inside allowed elements.
CORPUS = f"""# Corpus

<script>window.__xss = 1</script>

<img src=x onerror="window.__xss = 2">

<svg/onload="window.__xss = 3">

<a href="javascript:window.__xss = 4">raw anchor</a>

[js](javascript:window.__xss=5) [JS](JaVaScRiPt:alert(1)) [data](data:text/html,hi)
[plain](http://plain.example/) [relative](/x) [proto](//evil.example/)

![pixel]({SECURE}evil.example/p.png)

[europa.eu]({SECURE}europa.eu@evil.example/) [apple.com]({SECURE}аpple.com/)
[\u202emoc.elgoog\u202c]({SECURE}evil.example/) [{SECURE}bank.example]({SECURE}evil.example/)

[click][1]

[1]: {SECURE}evil.example/

<javascript:alert(1)> <{SECURE}ok.example/auto> <mailto:help@ok.example>

| a | b |
|---|---|
| 1 | 2 |
"""

ALLOWED_TAGS = {
    "H4", "H5", "H6", "P", "EM", "STRONG", "CODE", "PRE", "UL", "OL", "LI", "BLOCKQUOTE", "HR",
    "BR", "A", "SPAN",
}  # fmt: skip
# href, rel, target and class; an ordered list's start; <Text>'s own fixed title on an escape.
ALLOWED_ATTRIBUTES = {"href", "rel", "target", "class", "start", "title"}


def skill_dir(
    where: Path,
    name: str,
    version: str,
    *,
    description: str = DESCRIPTION,
    body: str = BODY,
    scripts: bool = False,
    files: dict[str, str] | None = None,
    extra: str = "",
) -> Path:
    where.mkdir(parents=True)
    manifest = f'kind: skill\nname: {name}\nversion: {version}\ndescription: "{description}"\n'
    (where / "manifest.yaml").write_text(manifest + (SCRIPTS_YAML if scripts else ""))
    (where / "SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "{description}"\n{extra}---\n{body}'
    )
    contents = dict(files or {})
    if scripts:
        contents["scripts/prorate.py"] = "print('prorate')\n"
    for relative, text in contents.items():
        (where / relative).parent.mkdir(parents=True, exist_ok=True)
        (where / relative).write_text(text)
    return where


def publish(console, project: Path, name: str, version: str, **kwargs) -> None:
    source = console.state.parent / "skill-src" / f"{name}-{version}"
    if source.exists():
        shutil.rmtree(source)
    registry.publish(skill_dir(source, name, version, **kwargs), project / "registry")


def with_skills(page, console) -> Path:
    project = open_northwind(page, console)
    if not (project / "registry" / "skill").exists():
        publish(
            console,
            project,
            "refund-policy",
            "1.0.0",
            extra="license: MIT\n",
            files={
                "references/eu-rules.md": "## EU\n\n*Fourteen* days.\n",
                "references/a.txt": "*plain*\n",
            },
        )
        publish(console, project, "refund-policy", "1.1.0", description="Refund rules, v2.")
        publish(console, project, "calc", "1.0.0", scripts=True)
        publish(console, project, "mixed", "1.0.0")
        publish(console, project, "mixed", "1.1.0", scripts=True)
        publish(console, project, "corpus", "1.0.0", body=CORPUS)
        publish(console, project, "odd", "1.0.0")
        # An entry a skill may not hold: listed, treated as code, verify fails.
        (project / "registry" / "skill" / "odd" / "1.0.0" / "notes.md").write_text("x")
    return project


def open_skill(page, console, name: str):
    with_skills(page, console)
    page.goto(f"{console.origin}/#/registry/skills/{name}")
    expect(page.locator("main h1")).to_contain_text(f"Skill {name}")
    card = page.get_by_role("region", name=re.compile(r"^Version \d"))
    expect(card.get_by_role("heading", name="Files")).to_be_visible()
    return card


def test_the_skills_list_has_the_code_column(page, console):
    with_skills(page, console)
    nav = page.get_by_role("navigation", name="Console")
    nav.get_by_role("link", name="Skills", exact=True).click()
    expect(page.locator("main h1")).to_contain_text("Skills")
    table = page.get_by_role("table", name="Skills")
    expect(table.get_by_role("columnheader", name="Code")).to_be_visible()
    for name, code in {
        "refund-policy": "Instructions",
        "calc": "Has scripts (code)",
        "mixed": "Mixed",
        "odd": "Unknown, treated as code",
    }.items():
        expect(row(page, "Skills", name).get_by_role("cell").last).to_have_text(code)


def test_no_skills_says_how_to_write_one(page, console):
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/skills")
    expect(page.get_by_text("No skill versions yet.")).to_be_visible()
    expect(page.locator("main")).to_contain_text("agentbreeder registry new skill NAME")


def test_a_skill_version_shows_its_description_first_then_formatted_instructions(page, console):
    open_skill(page, console, "refund-policy")
    page.get_by_role("button", name="Show 1.0.0").click()
    card = page.get_by_role("region", name="Version 1.0.0")
    callout = card.locator("section.callout")
    expect(callout.get_by_role("heading", name="Sent on every model call")).to_be_visible()
    expect(callout).to_contain_text("Description, 34 characters")
    expect(callout.locator("blockquote.quoted")).to_have_text(DESCRIPTION)
    facts = card.locator("dl.facts").nth(1)
    expect(facts).to_contain_text("Instructions")
    expect(facts).to_contain_text("MIT")
    # Formatted by default: SKILL.md's `#` is an h4 under the section's h3, the link shows its
    # destination and opens in a new tab, entities stay as written.
    toggle = card.get_by_role("group", name="Show SKILL.md as")
    expect(toggle.get_by_role("radio", name="Formatted")).to_be_checked()
    md = card.get_by_role("group", name="SKILL.md", exact=True)
    expect(md.locator("h4")).to_have_text("Refunds")
    link = md.get_by_role("link", name="policy (opens in a new tab)")
    expect(link).to_have_attribute("href", f"{SECURE}example.com/policy")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    expect(link).to_have_attribute("target", "_blank")
    expect(md).to_contain_text(f"({SECURE}example.com/policy)")
    expect(md).to_contain_text("(title: Refund policy)")
    expect(md).to_contain_text("&amp; stays as written.")
    expect(card.get_by_text("The model reads the source.").first).to_be_visible()
    # Source: what the model reads, and focus stays on the radio.
    source = toggle.get_by_role("radio", name="Source (what the model reads)")
    source.click()
    expect(source).to_be_focused()
    expect(card.get_by_role("group", name="SKILL.md", exact=True)).to_have_count(0)
    expect(card.locator("pre.text").filter(has_text="# Refunds").first).to_be_visible()
    # References follow the page's toggle; a .txt file is shown as text.
    eu = card.locator("details").filter(has=page.get_by_text("eu-rules.md", exact=True))
    eu.get_by_text("eu-rules.md", exact=True).click()
    expect(eu.locator("pre.text")).to_contain_text("## EU")
    plain = card.locator("details").filter(has=page.get_by_text("a.txt", exact=True))
    plain.get_by_text("a.txt", exact=True).click()
    expect(plain.locator("pre.text")).to_have_text("*plain*\n")
    # Formatted again: the .md reference renders, the .txt stays text.
    toggle.get_by_role("radio", name="Formatted").click()
    expect(eu.locator("div.md h5")).to_have_text("EU")
    expect(plain.locator("pre.text")).to_have_text("*plain*\n")
    source.click()
    # One toggle for the page: another version keeps the choice.
    page.get_by_role("button", name="Show 1.1.0").click()
    card = page.get_by_role("region", name="Version 1.1.0")
    expect(card.get_by_role("radio", name="Source (what the model reads)")).to_be_checked()


def test_a_script_skill_lists_its_scripts_as_code(page, console):
    card = open_skill(page, console, "calc")
    expect(card.locator("dl.facts").nth(1)).to_contain_text("Has scripts (code)")
    expect(card.get_by_text("These scripts are code.")).to_be_visible()
    scripts = card.locator("ul.skill-scripts")
    expect(scripts.get_by_role("heading", name="skill_calc__prorate")).to_be_visible()
    expect(scripts).to_contain_text("Pro-rated refund.")
    expect(scripts).to_contain_text('"required"')
    scripts.get_by_text("Source", exact=True).click()
    expect(scripts.locator("pre.text").filter(has_text="print('prorate')").first).to_be_visible()


def test_a_broken_skill_says_why_and_points_at_its_files(page, console):
    card = open_skill(page, console, "odd")
    expect(card.get_by_text("This version breaks the skill rules")).to_be_visible()
    expect(card.locator("dl.facts").nth(1)).to_contain_text("Unknown, treated as code")
    expect(card.get_by_text("The source is in Files below.")).to_be_visible()


def test_the_diff_says_when_the_description_changed(page, console):
    open_skill(page, console, "refund-policy")
    compare = page.get_by_role("region", name="Compare versions")
    expect(compare.get_by_text("The description changed.")).to_be_visible()
    expect(compare.get_by_text("The scripts changed")).to_have_count(0)


def test_the_corpus_renders_only_allowed_elements_and_fetches_nothing(page, console):
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    card = open_skill(page, console, "corpus")
    md = card.get_by_role("group", name="SKILL.md", exact=True)
    expect(md.locator("h4")).to_have_text("Corpus")
    found = md.evaluate(
        """(root) => [...root.querySelectorAll("*")].map((el) => ({
            tag: el.tagName, attrs: [...el.attributes].map((a) => [a.name, a.value]) }))"""
    )
    for element in found:
        assert element["tag"] in ALLOWED_TAGS, element
        for name, value in element["attrs"]:
            assert name in ALLOWED_ATTRIBUTES, element
            if name == "href":
                assert value.startswith((SECURE, "mailto:")), element
                assert "@evil" not in value, element
            if name == "title":
                assert value == "A control or invisible character, shown escaped", element
    hrefs = md.evaluate("(root) => [...root.querySelectorAll('a')].map((a) => a.href)")
    # The look-alike host and the link text naming another host are real links, each followed by
    # its true destination (the host in punycode).
    assert hrefs == [
        f"{SECURE}xn--pple-43d.com/",
        f"{SECURE}evil.example/",
        f"{SECURE}evil.example/",
        f"{SECURE}ok.example/auto",
        "mailto:help@ok.example",
    ], hrefs
    expect(md).to_contain_text(f"apple.com (opens in a new tab) ({SECURE}xn--pple-43d.com/)")
    expect(md).to_contain_text(
        f"{SECURE}bank.example (opens in a new tab) ({SECURE}evil.example/)"
    )
    # The raw markup and the refused links are visible text; a look-alike host shows in punycode
    # and the bidi override is a visible escape.
    for text in (
        "<script>window.__xss = 1</script>",
        "<img src=x onerror=",
        "[js](javascript:window.__xss=5)",
        "[image: pixel, not shown]",
        f"[europa.eu]({SECURE}europa.eu@evil.example/)",
        "[1]: ",
        "| a | b |",
    ):
        expect(md).to_contain_text(text)
    assert page.evaluate("window.__xss") is None
    assert md.locator("img, script, svg, iframe, style").count() == 0
    origin = console.origin
    assert [url for url in requests if not url.startswith(origin)] == []


def test_the_import_review_of_a_pack_and_of_a_script_skill(page, console):
    project = open_northwind(page, console)
    pack = skill_dir(console.state.parent / "import-src" / "pack", "pack-skill", "1.0.0")
    scripted = skill_dir(
        console.state.parent / "import-src" / "scripted", "script-skill", "1.0.0", scripts=True
    )
    page.goto(f"{console.origin}/#/registry/import")
    card = page.get_by_role("region", name="Import a version")
    card.get_by_label("Version directory (absolute path)").fill(str(pack))
    card.get_by_role("button", name="Review import").click()
    review = card.get_by_role("region", name="Review")
    expect(review.locator("section.callout blockquote.quoted")).to_have_text(DESCRIPTION)
    expect(review).to_contain_text("Instructions")
    expect(review).to_contain_text("its instructions can steer the agent to use any tool it has")
    expect(review.get_by_role("group", name="This code runs in agents")).to_have_count(0)
    review.get_by_role("button", name="Import as skill/pack-skill@1.0.0").click()
    expect(
        card.get_by_role("status").filter(has_text="Imported skill/pack-skill@1.0.0")
    ).to_be_visible()
    assert (project / "registry" / "skill" / "pack-skill" / "1.0.0" / "SKILL.md").is_file()

    card.get_by_label("Version directory (absolute path)").fill(str(scripted))
    card.get_by_role("button", name="Review import").click()
    review = card.get_by_role("region", name="Review")
    expect(review).to_contain_text("Has scripts (code)")
    confirm = review.get_by_role("group", name="This code runs in agents")
    expect(confirm).to_contain_text("Script skills are designed to run in a separate container")
    button = review.get_by_role("button", name="Import as skill/script-skill@1.0.0")
    expect(button).to_be_disabled()
    confirm.get_by_role("checkbox").check()
    button.click()
    expect(
        card.get_by_role("status").filter(has_text="Imported skill/script-skill@1.0.0")
    ).to_be_visible()


@pytest.mark.parametrize("width", [320, 375])
def test_the_skill_detail_fits_narrow_screens(page, console, width):
    page.set_viewport_size({"width": width, "height": 800})
    open_skill(page, console, "corpus")
    page.wait_for_timeout(300)
    assert page.evaluate("document.documentElement.scrollWidth") <= width


def test_skills_have_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    with_skills(scheme_page, console)
    found = []
    for route in ("/registry/skills", "/registry/skills/refund-policy", "/registry/skills/calc"):
        scheme_page.goto(f"{console.origin}/#{route}")
        scheme_page.locator("main h1").wait_for()
        scheme_page.wait_for_timeout(600)
        found += blocking(scheme_page, route)
    scheme_page.get_by_role("radio", name="Source (what the model reads)").check()
    found += blocking(scheme_page, "calc, Source")
    assert found == []
