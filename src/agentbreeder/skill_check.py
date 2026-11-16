"""Skill versions (`skill/<name>@X.Y.Z`, #1059 K1, skills spec §5): the checks `registry publish|
import|fork|edit` and `verify` run, the `skill` view `registry show` adds, and `runs_code`, which
decides from a version's files whether it is code.

A skill is an instruction pack (kind (a): `manifest.yaml`, `SKILL.md`, optional `references/`) or a
script skill (kind (b): the same plus `scripts/`, every file declared in the manifest). Nothing
here runs, imports or parses a script, and no runtime loads skills yet: the checks fail early, so a
name or description that passes publish never fails at runtime start.

`SKILL.md`'s front matter is the model's always-on text (L1: name and description, in every turn
of every agent that pins the skill), so it is parsed with the strict YAML loader and limited to
`name`, `description`, `license` and `compatibility`; ADK's own `Frontmatter` accepts extra keys
(`metadata.adk_additional_tools` would add tools), so this check is ours.
"""

from __future__ import annotations

import os
import re
import stat
import unicodedata
from pathlib import Path

from agentbreeder import registry, strict_yaml

INVALID = "SKILL_INVALID"
FIELD_UNSUPPORTED = "SKILL_FIELD_UNSUPPORTED"
UNDECLARED_SCRIPT = "SKILL_UNDECLARED_SCRIPT"
CODES = (INVALID, FIELD_UNSUPPORTED, UNDECLARED_SCRIPT)

SKILL_MD = "SKILL.md"
SKILL_MD_LIMIT = 32 * 1024
DESCRIPTION_LIMIT = 1024  # ADK's Frontmatter limit
REFERENCES_MAX = 20
REFERENCE_LIMIT = 64 * 1024
SCRIPTS_MAX = 10
SCRIPT_LIMIT = 64 * 1024
# The largest allowed skill (64 KiB manifest, 32 KiB SKILL.md, 20 references and 10 scripts of
# 64 KiB) is under registry.SHOW_TOTAL_LIMIT, so `parse` reads every byte; a test pins it.
SCRIPT_SKILL_NAME_MAX = 24  # so `skill_<name>__<script>` fits a 64-character function name

KEBAB = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")  # ADK's skill name rule (skills/models.py)
REFERENCE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\.(md|txt)")
SCRIPT_NAME = re.compile(r"[a-z][a-z0-9_]{0,23}")
SCRIPT_PATH = re.compile(r"scripts/(?P<module>[a-z][a-z0-9_]{0,63})\.py")
TOP_LEVEL = {"manifest.yaml": "file", SKILL_MD: "file", "references": "dir", "scripts": "dir"}
MANIFEST_KEYS = frozenset({"kind", "name", "version", "description", "scripts"})
FRONT_MATTER_KEYS = frozenset({"name", "description", "license", "compatibility"})
SCRIPT_KEYS = frozenset({"name", "path", "description", "parameters"})
FIELD_LIMIT = 500  # license, compatibility (the Agent Skills format's limit)
# Model-visible text (descriptions, parameter schema strings, license, compatibility) is one line
# with no control, format, surrogate, private-use, unassigned or line/paragraph separator
# character, and none of the invisible marks below, so a reviewer sees everything the model sees.
_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
_INVISIBLE_MARKS = re.compile(
    "[\u034f\u115f\u1160\u2065\u2800\u3164\ufe00-\ufe0f\uffa0\ufff9-\ufffc\U000e0100-\U000e01ef]"
)
PARAMETER_KEYS = frozenset({"type", "properties", "required", "description", "enum", "items"})
PARAMETER_DEPTH = 8
PARAMETER_NODES = 500
PROPERTY_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
SHOWN_KEYS = 5  # unknown keys named in an error message
_FENCE = re.compile(r"---[ \t]*\r?\n")


class SkillCheckError(registry.RegistryError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message)
        self.message = message


def _shown(text: str) -> str:
    return registry.clean(text, name=True)[0]


def _keys(keys: list) -> str:
    """At most SHOWN_KEYS unknown keys, so a file of many keys cannot make a huge error."""
    shown = ", ".join(_shown(str(k))[:64] for k in keys[:SHOWN_KEYS])
    return shown + (f" and {len(keys) - SHOWN_KEYS} more" if len(keys) > SHOWN_KEYS else "")


def _visible(text: str) -> bool:
    return not _INVISIBLE_MARKS.search(text) and not any(
        unicodedata.category(char) in _REFUSED_CATEGORIES for char in text
    )


def tool_name(skill: str, script: str) -> str:
    """The function name the model will call for a script (skills spec §9.4)."""
    return f"skill_{skill.replace('-', '_')}__{script}"


# -- runs_code -----------------------------------------------------------------------------------


def runs_code(version_dir: Path) -> bool:
    """Whether a skill version is code (skills spec §5.2), fail closed: true when it has a
    `scripts` entry of any kind, when its manifest declares `scripts:`, or when the directory or
    the manifest cannot be read as a plain file holding a YAML mapping."""
    try:
        for entry in os.listdir(version_dir):
            # `Scripts/` on a case-sensitive file system, or any entry a published skill may not
            # hold: never published, so never vouched for as text.
            folded = unicodedata.normalize("NFKC", entry).casefold()
            if folded == "scripts" or entry not in TOP_LEVEL:
                return True
        info = (version_dir / "manifest.yaml").lstat()
        if not stat.S_ISREG(info.st_mode):
            return True
        data = strict_yaml.load_file(version_dir / "manifest.yaml", registry.MANIFEST_LIMIT)
    except (OSError, ValueError, RecursionError):  # StrictYamlError is a ValueError
        return True
    return "scripts" in data


# -- The checks ----------------------------------------------------------------------------------


def _text(item, where: str, limit: int) -> str:
    """A file's UTF-8 text from the bytes `registry._read_version` read, refused when too large,
    not kept (the version's show budget ran out) or not UTF-8."""
    if item.size > limit:
        raise SkillCheckError(INVALID, f"{where} exceeds the {limit // 1024} KiB limit")
    if item.head is None or item.truncated:
        raise SkillCheckError(INVALID, f"{where} is too large to read")
    if b"\x00" in item.head:
        raise SkillCheckError(INVALID, f"{where} is not UTF-8 text")
    try:
        return item.head.decode("utf-8")
    except UnicodeDecodeError:
        raise SkillCheckError(INVALID, f"{where} is not UTF-8 text") from None


def _plain(value, where: str, field: str, limit: int, minimum: int = 1) -> str:
    """One line of model-visible text of `minimum` to `limit` characters (see _visible)."""
    if not isinstance(value, str) or not minimum <= len(value) <= limit:
        raise SkillCheckError(
            INVALID, f"{where}: `{field}` must be text of {minimum} to {limit} characters"
        )
    if not _visible(value):
        raise SkillCheckError(
            INVALID,
            f"{where}: `{field}` holds a line break, control, bidi or invisible character; "
            "use one line of plain text",
        )
    return value


def _description(value, where: str) -> str:
    return _plain(value, where, "description", DESCRIPTION_LIMIT)


def _parameters(value, where: str) -> dict:
    """A script's JSON Schema, the subset the model sees: an object at the top, only
    PARAMETER_KEYS, bounded depth and size, and every string plain text (it is in the model's
    tool list on every turn)."""
    nodes = 0

    def walk(node, depth: int, at: str) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > PARAMETER_NODES or depth > PARAMETER_DEPTH:
            raise SkillCheckError(INVALID, f"{where}: `parameters` is too large or too deep")
        if not isinstance(node, dict):
            raise SkillCheckError(INVALID, f"{where}: `parameters{at}` must be a mapping")
        unknown = sorted(set(node) - PARAMETER_KEYS)
        if unknown:
            raise SkillCheckError(
                INVALID,
                f"{where}: `parameters{at}`: {_keys(unknown)} is not allowed "
                f"({', '.join(sorted(PARAMETER_KEYS))})",
            )
        for key, item in node.items():
            if key == "properties":
                if not isinstance(item, dict):
                    raise SkillCheckError(
                        INVALID, f"{where}: `parameters{at}.properties` is not a mapping"
                    )
                for name, schema in item.items():
                    if not PROPERTY_NAME.fullmatch(name):
                        raise SkillCheckError(
                            INVALID, f"{where}: property names match {PROPERTY_NAME.pattern}"
                        )
                    walk(schema, depth + 1, f"{at}.{name}")
            elif key == "items":
                walk(item, depth + 1, f"{at}.items")
            elif key in ("required", "enum"):
                if not isinstance(item, list) or len(item) > 100:
                    raise SkillCheckError(
                        INVALID, f"{where}: `parameters{at}.{key}` must be a list"
                    )
                for value in item:
                    nodes += 1
                    if key == "required" and not isinstance(value, str):
                        raise SkillCheckError(
                            INVALID, f"{where}: `parameters{at}.required` lists property names"
                        )
                    if isinstance(value, str):
                        _plain(value, where, f"parameters{at}.{key}", 200, minimum=0)
                    elif not (value is None or isinstance(value, (bool, int, float))):
                        raise SkillCheckError(
                            INVALID, f"{where}: `parameters{at}.{key}` holds plain values only"
                        )
            else:  # type, description
                _plain(item, where, f"parameters{at}.{key}", DESCRIPTION_LIMIT)

    walk(value, 0, "")
    if value.get("type") != "object":
        raise SkillCheckError(INVALID, f"{where}: `parameters` must be a JSON Schema object")
    return value


def _front_matter(text: str) -> tuple[dict, str]:
    """(front matter, body) of SKILL.md: a `---` line, strict YAML, a closing `---` line."""
    opening = _FENCE.match(text)
    if opening is None:
        raise SkillCheckError(INVALID, f"{SKILL_MD} must start with a `---` front matter block")
    closing = re.compile(r"^---[ \t]*(\r?\n|\Z)", re.MULTILINE).search(text, opening.end())
    if closing is None:
        raise SkillCheckError(INVALID, f"{SKILL_MD}: the front matter has no closing `---` line")
    try:
        data = strict_yaml.load_text(text[opening.end() : closing.start()])
    except strict_yaml.StrictYamlError as error:
        raise SkillCheckError(INVALID, f"{SKILL_MD} front matter: {error}") from None
    return data, text[closing.end() :]


def _scripts(manifest: dict, name: str) -> list[dict]:
    declared = manifest["scripts"]
    if not isinstance(declared, list) or not 1 <= len(declared) <= SCRIPTS_MAX:
        raise SkillCheckError(
            INVALID, f"manifest.yaml: `scripts` must be a list of 1 to {SCRIPTS_MAX} entries"
        )
    if len(name) > SCRIPT_SKILL_NAME_MAX:
        raise SkillCheckError(
            INVALID,
            f"a skill with scripts has a name of at most {SCRIPT_SKILL_NAME_MAX} characters, so "
            "its tool names fit",
        )
    result, names, paths = [], set(), set()
    for index, entry in enumerate(declared):
        where = f"manifest.yaml scripts[{index}]"
        if not isinstance(entry, dict):
            raise SkillCheckError(INVALID, f"{where} must be a mapping")
        unknown = sorted(set(entry) - SCRIPT_KEYS)
        if unknown:
            raise SkillCheckError(
                INVALID,
                f"{where}: {_keys(unknown)} is not allowed ({', '.join(sorted(SCRIPT_KEYS))})",
            )
        script, path = entry.get("name"), entry.get("path")
        if not isinstance(script, str) or not SCRIPT_NAME.fullmatch(script):
            raise SkillCheckError(INVALID, f"{where}: `name` must match {SCRIPT_NAME.pattern}")
        if not isinstance(path, str) or not SCRIPT_PATH.fullmatch(path):
            raise SkillCheckError(
                INVALID, f"{where}: `path` must be scripts/<module>.py ({SCRIPT_PATH.pattern})"
            )
        if script in names or path in paths:
            raise SkillCheckError(
                INVALID, f"{where}: script {script} or its path is declared twice"
            )
        names.add(script)
        paths.add(path)
        parameters = _parameters(
            entry.get("parameters", {"type": "object", "properties": {}}), where
        )
        result.append(
            {
                "name": script,
                "path": path,
                "tool": tool_name(name, script),
                "description": _description(entry.get("description"), where),
                "parameters": parameters,
            }
        )
    return result


def parse(items: list, ref) -> dict:
    """Check a skill version from the entries `registry._read_version` read, and return what
    it holds: {name, description, license, compatibility, body, references, scripts, runs_code}.
    Raises SkillCheckError (SKILL_INVALID, SKILL_FIELD_UNSUPPORTED, SKILL_UNDECLARED_SCRIPT);
    messages name paths inside the version only."""
    by_path = {item.relative: item for item in items}
    for item in items:
        top = item.relative.split("/")[0]
        if top not in TOP_LEVEL:
            raise SkillCheckError(
                INVALID,
                f"{_shown(top)} is not allowed: a skill holds only manifest.yaml, {SKILL_MD}, "
                "references/ and scripts/",
            )
        if item.relative == top and item.type != TOP_LEVEL[top]:
            kind = "a directory" if TOP_LEVEL[top] == "dir" else "a file"
            raise SkillCheckError(INVALID, f"{top} must be {kind}")
    manifest_item = by_path.get("manifest.yaml")
    if manifest_item is None:
        raise SkillCheckError(INVALID, "manifest.yaml is missing")
    try:
        manifest = strict_yaml.load_text(
            _text(manifest_item, "manifest.yaml", registry.MANIFEST_LIMIT)
        )
    except strict_yaml.StrictYamlError as error:
        raise SkillCheckError(INVALID, f"manifest.yaml: {error}") from None
    unknown = sorted(set(manifest) - MANIFEST_KEYS)
    if unknown:
        raise SkillCheckError(
            INVALID,
            f"manifest.yaml: {_keys(unknown)} is not allowed in a skill "
            f"manifest ({', '.join(sorted(MANIFEST_KEYS))})",
        )
    name = ref.name
    if not KEBAB.fullmatch(name):
        raise SkillCheckError(INVALID, f"the skill name must be kebab-case ({KEBAB.pattern})")
    description = _description(manifest.get("description"), "manifest.yaml")

    skill_item = by_path.get(SKILL_MD)
    if skill_item is None:
        raise SkillCheckError(INVALID, f"{SKILL_MD} is missing")
    front, body = _front_matter(_text(skill_item, SKILL_MD, SKILL_MD_LIMIT))
    unsupported = sorted(set(front) - FRONT_MATTER_KEYS)
    if unsupported:
        raise SkillCheckError(
            FIELD_UNSUPPORTED,
            f"{SKILL_MD} front matter: {_keys(unsupported)} is not "
            f"supported (only {', '.join(sorted(FRONT_MATTER_KEYS))}); metadata and "
            "allowed-tools are refused because they would change what the model can do",
        )
    if front.get("name") != name:
        raise SkillCheckError(INVALID, f"{SKILL_MD} front matter: `name` must be {name}")
    if front.get("description") != description:
        raise SkillCheckError(
            INVALID, f"{SKILL_MD} front matter: `description` must equal the manifest's"
        )
    for key in ("license", "compatibility"):
        if key in front:
            _plain(front[key], f"{SKILL_MD} front matter", key, FIELD_LIMIT)

    references = []
    for item in items:
        parts = item.relative.split("/")
        if parts[0] != "references" or len(parts) == 1:
            continue
        where = _shown(item.relative)
        if len(parts) > 2 or item.type != "file" or not REFERENCE_NAME.fullmatch(parts[1]):
            raise SkillCheckError(
                INVALID,
                f"{where}: references/ holds flat .md or .txt files named "
                f"{REFERENCE_NAME.pattern}",
            )
        _text(item, where, REFERENCE_LIMIT)
        references.append(item.relative)
    if len(references) > REFERENCES_MAX:
        raise SkillCheckError(INVALID, f"references/ holds at most {REFERENCES_MAX} files")

    found = {item.relative: item for item in items if item.relative.startswith("scripts/")}
    scripts = _scripts(manifest, name) if "scripts" in manifest else []
    declared = {script["path"] for script in scripts}
    for relative in sorted(found):
        if relative not in declared:
            raise SkillCheckError(
                UNDECLARED_SCRIPT,
                f"{_shown(relative)} is not declared in manifest.yaml `scripts:`; every file in "
                "scripts/ is declared",
            )
    if "scripts" in by_path and "scripts" not in manifest:
        raise SkillCheckError(
            UNDECLARED_SCRIPT, "scripts/ exists but manifest.yaml declares no `scripts:`"
        )
    for script in scripts:
        item = found.get(script["path"])
        if item is None or item.type != "file":
            raise SkillCheckError(
                UNDECLARED_SCRIPT, f"{script['path']} is declared but is not a file in scripts/"
            )
        _text(item, script["path"], SCRIPT_LIMIT)
    return {
        "name": name,
        "description": description,
        "license": front.get("license"),
        "compatibility": front.get("compatibility"),
        "body": body,
        "references": references,
        "scripts": scripts,
        "runs_code": bool(scripts) or "scripts" in by_path or "scripts" in manifest,
    }


def check(version_dir: Path, ref) -> None:
    """Refuse a skill version that breaks the rules above (`registry.place` and `verify`)."""
    items, _ = registry._read_version(version_dir)
    try:
        parse(items, ref)
    except SkillCheckError as error:
        raise SkillCheckError(error.code, f"{ref}: {error.message}") from None


# -- `registry show` ------------------------------------------------------------------------------


def view(items: list, ref) -> dict:
    """`skill` (cleaned fields, the body, reference paths, each script with its tool name) and
    `skill_error` for `registry show`, from the bytes `show` read. A version that breaks the
    rules is shown with `skill: null` and the reason, not refused."""
    try:
        parsed = parse(items, ref)
    except SkillCheckError as error:
        return {"skill": None, "skill_error": str(error)}
    parsed["body"] = registry.clean(parsed["body"])[0]
    return {"skill": registry._clean_value(parsed), "skill_error": None}
