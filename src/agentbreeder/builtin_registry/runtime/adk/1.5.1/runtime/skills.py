"""Skills, kind (a): instruction packs (runtime/adk@1.5.1, #1059).

A skill is reviewed, hash-locked text the model may load during a turn: a name and description
that are always in the system instruction, a `SKILL.md` body returned by `load_skill`, and flat
`references/` files returned by `load_skill_resource`. Skill text steers the model as the prompt
does; it cannot add tools, read session state or secrets, or run code.

`agentbreeder build` places each skill at `artifacts/skill/<name>/` without any `scripts/` and
records the hash of the placed files in agent.config.json. At start this module:

- re-hashes each placed directory (skill_hash.py) and refuses to start on a mismatch
  (SKILL_TAMPERED);
- re-applies the publish-time refusals to what it reads (layout, sizes, front-matter keys), so a
  skill that reached the image another way is refused too (SKILL_INVALID);
- builds ADK `Skill` objects itself, with a front matter of only `name` and `description`, the
  body, and the validated reference files. It never uses ADK's directory loader, and never
  fills `resources.scripts` or `resources.assets`.

The model gets exactly two tools from ADK's `SkillToolset`: `load_skill` and
`load_skill_resource`. The per-turn caps, the "already loaded" answer and the reference path
rule are the runtime's (AgentPlugin), not ADK's.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import config as cfg
import telemetry
import yaml
from google.adk.skills import models
from google.adk.tools.skill_toolset import SkillDiscoveryMode, SkillToolset
from skill_hash import placed_files, placed_hash

LOAD = "load_skill"
LOAD_RESOURCE = "load_skill_resource"
TOOLS = (LOAD, LOAD_RESOURCE)
MAX_SKILLS = 10
MAX_LOADS = 3  # load_skill calls per turn
MAX_RESOURCE_LOADS = 3  # load_skill_resource calls per turn
LOAD_LIMIT = "SKILL_LOAD_LIMIT"
ALREADY_LOADED = (
    "already loaded: its instructions are earlier in this conversation; follow them, and do "
    "not load it again"
)

SKILL_MD = "SKILL.md"
SKILL_MD_LIMIT = 32 * 1024
DESCRIPTION_LIMIT = 1024  # ADK's Frontmatter limit
FIELD_LIMIT = 500
REFERENCES_MAX = 20
REFERENCE_LIMIT = 64 * 1024
MANIFEST_LIMIT = 64 * 1024
NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")  # ADK's kebab-case rule
REFERENCE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\.(md|txt)")
VERSION = re.compile(r"\d+\.\d+\.\d+")
HASH = re.compile(r"sha256:[0-9a-f]{64}")
# Anything else in a front matter is refused: `metadata` carries ADK's `adk_additional_tools`
# and `adk_inject_state`, and `allowed-tools` pre-approves tools.
FRONT_MATTER_KEYS = frozenset({"name", "description", "license", "compatibility"})
_FENCE = re.compile(r"---[ \t]*\r?\n")


class SkillError(cfg.ConfigError):
    """A placed skill the runtime will not load. The message starts with the code."""


def _invalid(ref: str, why: str) -> SkillError:
    return SkillError(f"SKILL_INVALID: {ref}: {why}")


class _Strict(yaml.SafeLoader):
    """Front matter: plain YAML only. No duplicate keys (the later one would win unseen)."""

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=True) for key, _ in node.value]
        if len(keys) != len({repr(key) for key in keys}):
            raise yaml.YAMLError("duplicate key")
        return super().construct_mapping(node, deep)


def _front_matter(text: str) -> tuple[dict, str]:
    """SKILL.md as (front matter, body). Anchors, aliases and tags are refused."""
    opened = _FENCE.match(text)
    if not opened:
        raise ValueError("SKILL.md must start with a front matter")
    closed = re.search(r"^---[ \t]*\r?$\n?", text[opened.end() :], re.MULTILINE)
    if not closed:
        raise ValueError("SKILL.md's front matter is not closed")
    raw = text[opened.end() : opened.end() + closed.start()]
    return _plain_yaml(raw, "SKILL.md's front matter"), text[opened.end() + closed.end() :]


def _plain_yaml(raw: str, what: str) -> dict:
    """A YAML mapping of plain keys and values: no anchors, aliases, tags or duplicate keys.
    A parser error is reported without its text, which quotes the file."""
    try:
        for event in yaml.parse(raw, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                raise ValueError(f"{what} must not use anchors or aliases")
            if getattr(event, "tag", None) not in (None, "tag:yaml.org,2002:str"):
                raise ValueError(f"{what} must not use tags")  # `!!str` is what it already is
        data = yaml.load(raw, Loader=_Strict)  # noqa: S506 - _Strict is a SafeLoader
    except (yaml.YAMLError, RecursionError):
        raise ValueError(f"{what} is not valid YAML") from None
    if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
        raise ValueError(f"{what} must be a mapping")
    return data


# As skill_check's rule for model-visible one-line text: no line break, control, bidi or
# invisible character (the description is in every turn's system instruction). Without the
# host rule's "unassigned" category: which characters are assigned depends on the Python that
# asks, and a description the host's Python accepted must not fail start on the image's.
_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Zl", "Zp"})
_INVISIBLE_MARKS = re.compile(
    "[\u034f\u115f\u1160\u2065\u2800\u3164\ufe00-\ufe0f\uffa0\ufff9-\ufffc\U000e0100-\U000e01ef]"
)
MANIFEST_KEYS = frozenset({"kind", "name", "version", "description"})


def _visible(text: str) -> bool:
    return not _INVISIBLE_MARKS.search(text) and not any(
        unicodedata.category(char) in _REFUSED_CATEGORIES for char in text
    )


def _text(data: bytes, limit: int, what: str) -> str:
    if len(data) > limit:
        raise ValueError(f"{what} is larger than {limit // 1024} KiB")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{what} is not UTF-8") from None


@dataclass(frozen=True)
class Loaded:
    """One skill the runtime verified and will offer."""

    name: str
    version: str
    sha256: str
    skill: models.Skill = field(repr=False, compare=False)

    @property
    def references(self) -> tuple[str, ...]:
        return tuple(self.skill.resources.list_references())


def load_one(entry: dict, resolve_dir) -> Loaded:
    name, version, recorded = entry.get("name"), entry.get("version"), entry.get("sha256")
    if not isinstance(name, str) or not NAME.fullmatch(name) or len(name) > 64:
        raise SkillError("SKILL_INVALID: a skill's name must be lowercase kebab-case")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise SkillError(f"SKILL_INVALID: skill/{name}: invalid version")
    ref = f"skill/{name}@{version}"
    if not isinstance(recorded, str) or not HASH.fullmatch(recorded):
        raise _invalid(ref, "agent.config.json has no sha256 for it")
    if entry.get("scripts"):
        # A script skill needs the script runner, which this runtime does not have.
        raise _invalid(ref, "script skills are not supported by this runtime")
    try:
        files = placed_files(resolve_dir(entry["dir"]))
        found = placed_hash(files)
    except (OSError, ValueError, KeyError) as error:
        raise _invalid(ref, f"cannot read the placed files ({type(error).__name__})") from None
    if found != recorded:
        # The files in the image are not the ones `agentbreeder build` placed and recorded.
        raise SkillError(f"SKILL_TAMPERED: {ref}: its files changed after the build")
    try:
        return Loaded(name, version, recorded, _skill(name, version, files))
    except ValueError as error:  # our own fixed messages only: never a parser's text
        raise _invalid(ref, str(error)[:200]) from None


def _skill(name: str, version: str, files: dict[str, bytes]) -> models.Skill:
    """The ADK Skill for verified placed files, with every publish-time refusal re-applied."""
    references: dict[str, str] = {}
    for relative, data in files.items():
        if relative in ("manifest.yaml", SKILL_MD):
            continue
        folder, _, file_name = relative.partition("/")
        if folder != "references" or not REFERENCE_NAME.fullmatch(file_name):
            # scripts/, assets/, nested or hidden files, anything not listed.
            raise ValueError("only manifest.yaml, SKILL.md and flat references/ are allowed")
        references[file_name] = _text(data, REFERENCE_LIMIT, f"references/{file_name}")
    if len(references) > REFERENCES_MAX:
        raise ValueError(f"more than {REFERENCES_MAX} reference files")
    if SKILL_MD not in files or "manifest.yaml" not in files:
        raise ValueError("manifest.yaml and SKILL.md are required")
    manifest = _plain_yaml(
        _text(files["manifest.yaml"], MANIFEST_LIMIT, "manifest.yaml"), "manifest.yaml"
    )
    if set(manifest) - MANIFEST_KEYS:  # `scripts:` among them: no script skill is loaded
        raise ValueError("manifest.yaml has keys that are not allowed")
    if (manifest.get("kind"), manifest.get("name"), manifest.get("version")) != (
        "skill",
        name,
        version,
    ):
        raise ValueError("manifest.yaml is not this skill version's")
    front, body = _front_matter(_text(files[SKILL_MD], SKILL_MD_LIMIT, SKILL_MD))
    unknown = sorted(str(key) for key in set(front) - FRONT_MATTER_KEYS)
    if unknown:
        raise ValueError("SKILL_FIELD_UNSUPPORTED: front-matter keys that are not allowed")
    description = front.get("description")
    if front.get("name") != name:
        raise ValueError("SKILL.md's name is not the skill's")
    if not isinstance(description, str) or not 1 <= len(description) <= DESCRIPTION_LIMIT:
        raise ValueError("SKILL.md's description must be 1 to 1024 characters")
    if description != manifest.get("description"):
        raise ValueError("SKILL.md's description is not the manifest's")
    if not _visible(description):
        raise ValueError("SKILL.md's description must be one line of plain text")
    for key in ("license", "compatibility"):
        value = front.get(key)
        if value is not None and (
            not isinstance(value, str) or len(value) > FIELD_LIMIT or not _visible(value)
        ):
            raise ValueError(
                f"SKILL.md's {key} must be one line of at most {FIELD_LIMIT} characters"
            )
    return models.Skill(
        # Only name and description: no metadata, no allowed-tools, whatever the file said.
        frontmatter=models.Frontmatter(name=name, description=description),
        instructions=body,
        resources=models.Resources(references=references),
    )


def load(entries: list[dict], resolve_dir) -> list[Loaded]:
    """Every skill of agent.config.json, verified. Raises SkillError (start fails)."""
    if len(entries) > MAX_SKILLS:
        raise SkillError(f"SKILL_INVALID: at most {MAX_SKILLS} skills")
    loaded = [load_one(entry, resolve_dir) for entry in entries]
    if len({skill.name for skill in loaded}) != len(loaded):
        raise SkillError("SKILL_INVALID: a skill is listed twice")
    for skill in loaded:
        # ADK records tool responses and model requests on its own spans: the loaded text is
        # replaced by a label when spans are exported (telemetry.withhold).
        ref = f"skill {skill.name}@{skill.version}"
        telemetry.withhold(skill.skill.instructions, f"{ref} SKILL.md")
        for name, text in skill.skill.resources.references.items():
            telemetry.withhold(text, f"{ref} references/{name}")
    return loaded


def toolset(loaded: list[Loaded]) -> SkillToolset:
    """ADK's toolset over our Skill objects: no code executor (so no script tool), the catalog
    in the system instruction, and only the two load tools offered."""
    return SkillToolset(
        [skill.skill for skill in loaded],
        code_executor=None,
        discovery_mode=SkillDiscoveryMode.EAGER,
        tool_filter=list(TOOLS),
    )


def reference_name(path) -> str | None:
    """The flat reference file a `load_skill_resource` path names, or None when the path is
    not exactly `references/<flat name>` (scripts/, assets/, `..`, nesting)."""
    if not isinstance(path, str) or not path.startswith("references/"):
        return None
    name = path[len("references/") :]
    return name if REFERENCE_NAME.fullmatch(name) else None


# Session state: which hash of each skill this conversation loaded. "Already loaded" is said
# only for the same hash, so a session that loaded an earlier version gets the new text.
LOADED_KEY = "agentbreeder_skills_loaded"


def loaded_hash(state, name: str) -> str | None:
    """The hash of `name` this session last loaded, or None."""
    try:
        recorded = state.get(LOADED_KEY)
    except Exception:  # noqa: BLE001 - state of an unexpected kind
        return None
    value = recorded.get(name) if isinstance(recorded, dict) else None
    return value if isinstance(value, str) else None


def remember_loaded(state, name: str, sha256: str) -> None:
    recorded = state.get(LOADED_KEY)
    state[LOADED_KEY] = {**(recorded if isinstance(recorded, dict) else {}), name: sha256}


def new_usage() -> dict:
    """What one turn records about skills: call counts and what was loaded, in load order."""
    return {"loads": 0, "resource_loads": 0, "loaded": {}}


def reply_entries(usage: dict) -> list[dict]:
    """A turn's `skills[]`: each skill loaded this turn and the reference files read from it."""
    return [
        {"name": name, "version": entry["version"], "resources": list(entry["resources"])}
        for name, entry in usage["loaded"].items()
    ]
