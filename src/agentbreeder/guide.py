"""The guided path: one definition of the steps from an empty directory to a released agent.

This module is the single source for `agentbreeder next`, the console's checklist and the
"Your first agent" docs (guided spec §3, §9.2 #4, §14.2; console epic M4). This file holds the
`next --json` contract: the models below are what `next --json` prints, and
`docs/next.schema.json` is generated from them (a test keeps the two equal). The checks that
fill them are pure reads: they never start, build, deploy or write anything, and never print a
secret value.

Contract rules (`docs/next.md` is the reference):
- `schema_version` changes only on a breaking change. Fields, reason codes and condition codes
  may be added within a version; consumers ignore what they do not know.
- Step ids, states and fix actions are closed within a version.
- The models refuse what the contract forbids, so the producer fails instead of printing it:
  control, bidi and invisible characters, multi-line text, unbounded lists, relative paths, a
  fix on the wrong step, and states that contradict each other.
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from agentbreeder.agentfile import NAME_PATTERN
from agentbreeder.manifest import _IMAGE_PATTERN

SCHEMA_VERSION = 1

StepId = Literal[
    "prerequisites", "project", "keys", "services", "build", "release", "try", "kubernetes"
]
StepState = Literal["done", "next", "blocked", "todo"]
STEP_ORDER: tuple[str, ...] = StepId.__args__

TITLES: dict[str, str] = {
    "prerequisites": "Prerequisites",
    "project": "Project",
    "keys": "Model keys",
    "services": "Services",
    "build": "Build",
    "release": "Release (eval gate)",
    "try": "Try it",
    "kubernetes": "Kubernetes",
}
OPTIONAL_STEPS = frozenset({"kubernetes"})

# What a console button does. Five are console job kinds with the same name (`center/jobs.py`
# KINDS); `new-agent`, `example`, `set-keys` and `invoke` are console actions that are not jobs.
# `None`: the console has no button and shows `command` as an instruction.
FixAction = Literal[
    "new-agent",
    "example",
    "set-keys",
    "services-up",
    "build",
    "release",
    "invoke",
    "cluster-up",
    "promote",
]

# The one code every step can carry: an earlier step must be done before this one can be checked
# (no agent name, no stack, Docker not reachable, state that needs `migrate`). Its `data` is null.
NOT_CHECKED = "NOT_CHECKED"

# Reason codes per step in schema_version 1 (NOT_CHECKED is allowed on every step).
REASONS: dict[str, tuple[str, ...]] = {
    "prerequisites": ("PREREQUISITE_FAILED",),
    "project": ("PROJECT_MISSING", "PROJECT_INVALID", "REF_UNRESOLVED"),
    "keys": ("KEYS_MISSING", "KEYS_CONFIG_INVALID"),
    "services": (
        "SERVICES_UNREADABLE",
        "SERVICES_DOWN",
        "SERVICES_UNHEALTHY",
        "ENVIRONMENT_MISSING",
    ),
    "build": ("REF_UNPINNED", "LOCK_STALE", "NOT_BUILT", "BUILD_STALE"),
    "release": (
        "STATE_NEEDS_MIGRATION",
        "APPROVAL_PENDING",
        "PRODUCTION_DISABLED",
        "RELEASE_GATE_FAILED",
        "NOT_RELEASED",
        "PRODUCTION_STALE",
        "NO_RELEASE_EVIDENCE",
        "RELEASE_THRESHOLD_LOWERED",
        "RELEASE_EVALSET_CHANGED",
        "PRODUCTION_NOT_READY",
    ),
    "try": (),
    "kubernetes": (
        "APPROVAL_PENDING",
        "CLUSTER_DOWN",
        "GRANT_PENDING",
        "NOT_PROMOTED",
        "PROMOTION_STALE",
    ),
}

# Codes whose step is `blocked` (nothing can be run from this step until it is fixed by hand);
# every other code on the first step not done makes it `next`.
BLOCKING: frozenset[str] = frozenset(
    {
        "PREREQUISITE_FAILED",
        "PROJECT_INVALID",
        "REF_UNRESOLVED",
        "KEYS_CONFIG_INVALID",
        "SERVICES_UNREADABLE",
        "STATE_NEEDS_MIGRATION",
        "APPROVAL_PENDING",
        "PRODUCTION_DISABLED",
    }
)

# Codes whose `data` may be null besides NOT_CHECKED: the state behind it could not be read.
NULL_DATA: frozenset[str] = frozenset({NOT_CHECKED, "STATE_NEEDS_MIGRATION"})

# The console actions allowed on each step (the fixed step → job map).
ACTIONS: dict[str, frozenset[str]] = {
    "prerequisites": frozenset(),
    "project": frozenset({"new-agent", "example"}),
    "keys": frozenset({"set-keys"}),
    "services": frozenset({"services-up"}),
    "build": frozenset({"build"}),
    "release": frozenset({"release"}),
    "try": frozenset({"invoke"}),
    "kubernetes": frozenset({"cluster-up", "services-up", "promote"}),
}

# The console action each reason code allows (none for a code not listed). A done `try` allows
# `invoke`. `ACTIONS` is the union per step.
CODE_ACTIONS: dict[str, frozenset[str]] = {
    "PROJECT_MISSING": frozenset({"new-agent", "example"}),
    "KEYS_MISSING": frozenset({"set-keys"}),
    "SERVICES_DOWN": frozenset({"services-up"}),
    "SERVICES_UNHEALTHY": frozenset({"services-up"}),
    "ENVIRONMENT_MISSING": frozenset({"services-up"}),
    "NOT_BUILT": frozenset({"build"}),
    "BUILD_STALE": frozenset({"build"}),
    "RELEASE_GATE_FAILED": frozenset({"release"}),
    "NOT_RELEASED": frozenset({"release"}),
    "PRODUCTION_STALE": frozenset({"release"}),
    "NO_RELEASE_EVIDENCE": frozenset({"release"}),
    "RELEASE_THRESHOLD_LOWERED": frozenset({"release"}),
    "RELEASE_EVALSET_CHANGED": frozenset({"release"}),
    "PRODUCTION_NOT_READY": frozenset({"release"}),
    "CLUSTER_DOWN": frozenset({"cluster-up"}),
    # `cluster up` without `--yes` (the console's job) attaches the cluster but leaves the grant
    # and the operator config unchanged; a reviewed `services up` records it.
    "GRANT_PENDING": frozenset({"services-up"}),
    "NOT_PROMOTED": frozenset({"promote"}),
    "PROMOTION_STALE": frozenset({"promote"}),
}

CONDITION_CODES: tuple[str, ...] = (
    "STEP_BLOCKED",
    "DEPLOYMENT_FAILED",
    "DEPLOYMENT_NONCOMPLIANT",
    "SERVICES_UNHEALTHY",
    "PRODUCTION_UNGATED",
)

_CONDITION_NEEDS = {
    "STEP_BLOCKED": "step",
    "DEPLOYMENT_FAILED": "deployment",
    "DEPLOYMENT_NONCOMPLIANT": "deployment",
    "SERVICES_UNHEALTHY": "service",
    "PRODUCTION_UNGATED": "deployment",
}

# Links come only from this table, never from project, registry or state content.
URLS: tuple[str, ...] = ("https://aistudio.google.com/apikey",)

# Refused in every string, so each one is a single line a terminal shows as written and a shell
# tokenises as it reads: controls (newline and tab included), format characters (bidi,
# zero-width), surrogates, private-use and unassigned code points, line and paragraph separators,
# every space but U+0020, and characters that render as nothing (fillers, variation selectors).
_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp", "Zs"})
_INVISIBLE = re.compile(
    "[\u034f\u115f\u1160\u180e\u2800\u3164\ufe00-\ufe0f\uffa0\ufff9-\ufffc"
    "\U0001d173-\U0001d17a\U000e0100-\U000e01ef]"
)


def _clean(value: str) -> str:
    if _INVISIBLE.search(value) or any(
        char != " " and unicodedata.category(char) in _REFUSED_CATEGORIES for char in value
    ):
        raise ValueError("contains control, bidi, invisible or non-ASCII space characters")
    return value


def _text(limit: int):
    return Annotated[str, Field(min_length=1, max_length=limit), AfterValidator(_clean)]


Message = _text(300)
Command = _text(2000)
CheckName = _text(80)
AbsolutePath = Annotated[
    str, Field(min_length=2, max_length=4096, pattern=r"^/"), AfterValidator(_clean)
]
Code = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{2,39}$")]
AgentName = Annotated[str, Field(pattern=NAME_PATTERN)]
Environment = Annotated[str, Field(pattern=r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")]
ServiceName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")]
ClusterName = Annotated[str, Field(pattern=r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")]
RegistryRef = Annotated[
    str,
    Field(
        max_length=200,
        pattern=r"^[a-z]+/[a-z0-9]([a-z0-9-]*[a-z0-9])?@[0-9]+\.[0-9]+\.[0-9]+"
        r"(-[0-9A-Za-z.-]+)?$",
    ),
]
Version = Annotated[
    str, Field(max_length=64, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$")
]
ImageId = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
# What a deployment runs: a local image ID or `repo[:tag]@sha256:…` (`manifest.py`).
Image = Annotated[
    str,
    Field(max_length=512, pattern=f"(?:{_IMAGE_PATTERN})|(?:^sha256:[a-f0-9]{{64}}$)"),
    AfterValidator(_clean),
]
DeploymentId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
KeyName = Annotated[str, Field(pattern=r"^[A-Z_][A-Z0-9_]{0,127}$")]
Endpoint = Annotated[
    str,
    Field(
        pattern=r"^http://127\.0\.0\.1:([1-9][0-9]{0,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}"
        r"|65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5])$"
    ),
]
Url = Literal[URLS]  # type: ignore[valid-type]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Reason(_Model):
    """Why a step is not done: a code from `REASONS[step]` (or NOT_CHECKED) and one line."""

    code: Code
    message: Message


class Fix(_Model):
    """What fixes the step. `command` is display only: no surface runs it. The console maps
    `action` to its own fixed job; with `action: null` it shows `command` and `note`."""

    action: FixAction | None
    command: Command | None
    note: Message | None
    url: Url | None

    @model_validator(mode="after")
    def _something(self) -> Fix:
        if self.action is None and self.command is None and self.note is None:
            raise ValueError("a fix needs an action, a command or a note")
        return self


class Check(_Model):
    name: CheckName
    passed: bool
    required: bool
    message: Message


class PrerequisitesData(_Model):
    checks: list[Check] = Field(max_length=50)


class NewerVersion(_Model):
    ref: RegistryRef  # the pinned ref
    latest: Version  # the newest published version of that name
    file: AbsolutePath  # the file that pins it
    line: Annotated[int, Field(ge=1)] | None


class ProjectData(_Model):
    agent: AgentName | None
    agent_file: AbsolutePath | None
    stack_file: AbsolutePath | None
    error_code: Code | None
    newer: list[NewerVersion] = Field(max_length=200)


class KeysData(_Model):
    required: list[KeyName] = Field(max_length=50)
    present: list[KeyName] = Field(max_length=50)
    stored: list[KeyName] = Field(max_length=50)
    missing: list[KeyName] = Field(max_length=50)

    @model_validator(mode="after")
    def _sets(self) -> KeysData:
        for names in (self.required, self.present, self.stored, self.missing):
            if len(set(names)) != len(names):
                raise ValueError("key name lists must not repeat a name")
        required = set(self.required)
        if not set(self.present) <= required or not set(self.stored) <= required:
            raise ValueError("present and stored must be required key names")
        if set(self.missing) != required - set(self.present) - set(self.stored):
            raise ValueError("missing must be the required keys neither present nor stored")
        return self


class Service(_Model):
    name: ServiceName
    kind: Literal["service", "mcp-relay"]
    state: Literal[
        "created", "running", "restarting", "paused", "exited", "removing", "dead", "absent"
    ]
    health: Literal["healthy", "unhealthy", "starting", "none"]


class ServicesData(_Model):
    services: list[Service] = Field(max_length=100)
    grant_recorded: bool
    environment_written: bool


class BuildData(_Model):
    image: ImageId | None
    build_manifest: AbsolutePath | None
    environment: Environment | None  # the environment the build manifest was written for
    agent_yaml_current: bool
    lock_current: bool


class Production(_Model):
    id: DeploymentId
    image: Image
    state: Literal["pending", "starting", "ready", "failed", "removing", "disabled"]  # stored
    endpoint: Endpoint | None
    manifest_current: bool  # deployed from the deploy manifest the current build wrote


class ReleaseData(_Model):
    production: Production | None
    # A release/promoted row whose RELEASE_IMAGE and RELEASE_MANIFEST are the ones production
    # runs, whatever its threshold or eval set.
    released: bool
    blocker: bool  # the kill switch blocks production's principal here (`disable`, until `enable`)
    # The latest release row for the current build image, eval set and threshold is eval_failed.
    gate_failed: bool
    failed_tail: bool  # a saved failed-candidate log tail exists (`logs --failed NAME`)


class TryData(_Model):
    deployment: DeploymentId | None
    endpoint: Endpoint | None


class KubernetesData(_Model):
    cluster: ClusterName
    cluster_up: bool
    # The environment's operator config names this stack's cluster (a grant with it was accepted).
    cluster_configured: bool
    deployment: DeploymentId | None  # the active kubernetes deployment
    promoted_image: Image | None  # RELEASE_IMAGE of its promote/promoted audit row
    production_image: Image | None


class _Step(_Model):
    id: StepId
    title: str
    required: bool
    state: StepState
    reason: Reason | None  # null exactly when done
    fix: Fix | None  # required when next or blocked; on a done step only for try

    @model_validator(mode="after")
    def _step_rules(self) -> _Step:
        step = self.id
        if self.title != TITLES[step]:
            raise ValueError(f"{step}: title must be {TITLES[step]!r}")
        if self.required != (step not in OPTIONAL_STEPS):
            raise ValueError(f"{step}: required must be {step not in OPTIONAL_STEPS}")
        if (self.state == "done") != (self.reason is None):
            raise ValueError(f"{step}: a reason is given exactly when it is not done")
        code = self.reason.code if self.reason else None
        if code is not None and code != NOT_CHECKED and code not in REASONS[step]:
            raise ValueError(f"{step}: unknown reason {code}")
        if self.data is None and code not in NULL_DATA:  # type: ignore[attr-defined]
            raise ValueError(f"{step}: data is null only when NOT_CHECKED or unreadable")
        if code == NOT_CHECKED and self.data is not None:  # type: ignore[attr-defined]
            raise ValueError(f"{step}: data is null exactly when NOT_CHECKED")
        if code == NOT_CHECKED and (self.state != "todo" or self.fix is not None):
            raise ValueError(f"{step}: NOT_CHECKED steps are todo with no fix")
        if step == "keys" and self.data is not None:
            if (code == "KEYS_MISSING") != bool(self.data.missing):  # type: ignore[attr-defined]
                raise ValueError("keys: KEYS_MISSING exactly when a key is missing")
            if self.state == "done" and self.data.missing:  # type: ignore[attr-defined]
                raise ValueError("keys: done with a key missing")
        if self.state in ("next", "blocked") and self.fix is None:
            raise ValueError(f"{step}: a {self.state} step needs a fix")
        if self.state == "done" and self.fix is not None and step != "try":
            raise ValueError(f"{step}: only try carries a fix when done")
        if self.fix and self.fix.action:
            allowed = ACTIONS[step] if code is None else CODE_ACTIONS.get(code, frozenset())
            if self.fix.action not in ACTIONS[step] or self.fix.action not in allowed:
                raise ValueError(f"{step}: action {self.fix.action} does not belong here")
        if self.fix and self.fix.action and code in BLOCKING:
            raise ValueError(f"{step}: a blocked step has no console action")
        return self


class PrerequisitesStep(_Step):
    id: Literal["prerequisites"]
    data: PrerequisitesData | None


class ProjectStep(_Step):
    id: Literal["project"]
    data: ProjectData | None


class KeysStep(_Step):
    id: Literal["keys"]
    data: KeysData | None


class ServicesStep(_Step):
    id: Literal["services"]
    data: ServicesData | None


class BuildStep(_Step):
    id: Literal["build"]
    data: BuildData | None


class ReleaseStep(_Step):
    id: Literal["release"]
    data: ReleaseData | None


class TryStep(_Step):
    id: Literal["try"]
    data: TryData | None


class KubernetesStep(_Step):
    id: Literal["kubernetes"]
    data: KubernetesData | None


Step = Annotated[
    PrerequisitesStep
    | ProjectStep
    | KeysStep
    | ServicesStep
    | BuildStep
    | ReleaseStep
    | TryStep
    | KubernetesStep,
    Field(discriminator="id"),
]


class Condition(_Model):
    """A live problem (guided §14.4): computed on every run, never stored, clears itself."""

    code: Code
    message: Message
    step: StepId | None
    deployment: DeploymentId | None
    service: ServiceName | None


class Guide(_Model):
    """What `agentbreeder next --json` prints (exit 0 whenever it was computed)."""

    schema_version: Literal[1]
    project: AbsolutePath
    environment: Environment
    complete: bool  # every required step is done
    next: StepId | None  # the step in state "next"; null when nothing is runnable now
    steps: list[Step] = Field(max_length=len(STEP_ORDER))
    conditions: list[Condition] = Field(max_length=100)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _integer(cls, value):
        if type(value) is not int:  # Literal[1] alone would accept True
            raise ValueError("schema_version must be the integer 1")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Guide:
        """The rules `docs/next.md` states, so the producer cannot print a contradiction."""
        ids = [step.id for step in self.steps]
        if ids != [i for i in STEP_ORDER if i in ids] or set(STEP_ORDER) - set(ids) - set(
            OPTIONAL_STEPS
        ):
            raise ValueError("steps must be every step id once, in order (kubernetes optional)")
        required = [s for s in self.steps if s.required]
        optional = [s for s in self.steps if not s.required]
        if self.complete != all(s.state == "done" for s in required):
            raise ValueError("complete must be true exactly when every required step is done")
        # The first step not done (required ones first) is "next" or "blocked", by its code;
        # every other step not done is "todo".
        pending = [s for s in (*required, *optional) if s.state != "done"]
        for index, step in enumerate(pending):
            if index == 0:
                wanted = "blocked" if step.reason.code in BLOCKING else "next"
            else:
                wanted = "todo"
            if step.state != wanted:
                raise ValueError(f"{step.id}: state {step.state}, expected {wanted}")
        first = pending[0] if pending else None
        if self.next != (first.id if first and first.state == "next" else None):
            raise ValueError("next must name the step in state next, or be null")
        self._check_paths()
        self._check_conditions(first)
        self._check_try()
        return self

    def _check_paths(self) -> None:
        if self.project != os.path.normpath(self.project) or self.project == "/":
            raise ValueError("project must be a normalised directory other than /")
        root = self.project + "/"
        for step in self.steps:
            data = step.data
            paths = []
            if isinstance(data, ProjectData):
                paths = [data.agent_file, data.stack_file, *(n.file for n in data.newer)]
            elif isinstance(data, BuildData):
                paths = [data.build_manifest]
            for path in paths:
                if path is not None and (
                    path != os.path.normpath(path) or not path.startswith(root)
                ):
                    raise ValueError(f"{step.id}: paths must be inside project")

    def _check_conditions(self, first) -> None:
        blocked = [c for c in self.conditions if c.code == "STEP_BLOCKED"]
        for condition in self.conditions:
            if condition.code not in CONDITION_CODES:
                raise ValueError(f"unknown condition {condition.code}")
            needs = _CONDITION_NEEDS.get(condition.code)
            if needs and getattr(condition, needs) is None:
                raise ValueError(f"{condition.code} needs {needs}")
        wanted = [first.id] if first is not None and first.state == "blocked" else []
        if [c.step for c in blocked] != wanted:
            raise ValueError("STEP_BLOCKED is raised exactly for the blocked step")

    def _check_try(self) -> None:
        """Release and build `done` carry their evidence; try names production."""
        steps = {step.id: step for step in self.steps}
        build, release, attempt = steps["build"], steps["release"], steps["try"]
        if build.state == "done" and not (
            build.data.image
            and build.data.build_manifest
            and build.data.environment == self.environment
            and build.data.agent_yaml_current
            and build.data.lock_current
        ):
            raise ValueError("build: done needs an image built for this environment, current")
        production = release.data.production if release.data else None
        if release.state == "done" and not (
            production is not None
            and production.state == "ready"
            and production.manifest_current
            and release.data.released
            and not release.data.gate_failed
            and build.data is not None
            and production.image == build.data.image
        ):
            raise ValueError("release: done needs a ready, current, released production")
        release_code = release.reason.code if release.reason else None
        blocked = bool(release.data and release.data.blocker)
        if blocked and production is None:
            raise ValueError("release: a blocker is reported only for a production")
        if (release_code == "PRODUCTION_DISABLED") != (
            blocked and release_code not in ("STATE_NEEDS_MIGRATION", "APPROVAL_PENDING")
        ):
            raise ValueError("release: PRODUCTION_DISABLED exactly when a blocker exists")
        if release.state == "done" and blocked:
            raise ValueError("release: done needs no blocker")
        if (release.state == "done") != (attempt.state == "done"):
            raise ValueError("try is done exactly when release is done")
        if attempt.data is not None:
            wanted = (production.id, production.endpoint) if production else (None, None)
            if (attempt.data.deployment, attempt.data.endpoint) != wanted:
                raise ValueError("try must name production's deployment and endpoint")
        ungated = [c.deployment for c in self.conditions if c.code == "PRODUCTION_UNGATED"]
        wanted = (
            [production.id]
            if production is not None and release.data and not release.data.released
            else []
        )
        if ungated != wanted:
            raise ValueError("PRODUCTION_UNGATED names production exactly when it is unreleased")


def _open(schema: object) -> object:
    """The published schema leaves objects open (`additionalProperties` dropped), so a consumer
    validating with it accepts the additive fields a later 1.x producer prints. The models
    themselves stay closed, so this producer cannot print a field it did not declare."""
    if isinstance(schema, dict):
        return {k: _open(v) for k, v in schema.items() if k != "additionalProperties"}
    if isinstance(schema, list):
        return [_open(item) for item in schema]
    return schema


def json_schema() -> dict:
    """`docs/next.schema.json`."""
    schema = _open(Guide.model_json_schema())
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "agentbreeder next --json"
    return schema
