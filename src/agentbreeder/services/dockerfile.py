"""The Dockerfile subset a built service (`build: Dockerfile`) may use.

Built services are registry versions (trusted code, see the spec's trust model); this check is
defence in depth for one rule: every base image is digest-pinned, and builds get no args,
secrets or SSH. Rather than re-implement BuildKit's parser, and differ from it, it accepts only a
small subset whose meaning is unambiguous, and refuses everything else:

- printable ASCII and tabs only, `\\n` line ends, no BOM; no parser directives, shebangs or
  comments that look like a directive, anywhere in the file;
- one instruction per line: no line continuations, no heredocs;
- instructions: FROM, RUN, COPY, WORKDIR, ENV, USER, EXPOSE, LABEL, CMD, ENTRYPOINT,
  HEALTHCHECK, STOPSIGNAL (no ARG, ADD, ONBUILD, SHELL, VOLUME);
- options: an allowlist per instruction, with no quotes, `$`, backticks or backslashes;
- FROM: `name@sha256:<64 hex>`, an earlier stage, or `scratch`; no FROM options;
- COPY --from: an earlier stage or a digest-pinned image; RUN --mount: bind, cache or tmpfs
  with known keys only.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentbreeder.services.schema import ServiceError

PINNED = re.compile(r"[a-z0-9][a-z0-9._/:-]{0,254}@sha256:[0-9a-f]{64}")
STAGE = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_DIRECTIVE_LIKE = re.compile(r"#[ \t]*[A-Za-z_][A-Za-z0-9_-]*[ \t]*=")
_LINE = re.compile(r"[\x20-\x7e\t]*")
_OPTION = re.compile(r"--([a-z-]+)(=([A-Za-z0-9@:._/,=+-]*))?")
OPTIONS = {
    "FROM": frozenset(),
    "RUN": frozenset({"mount"}),
    "COPY": frozenset({"from", "chown", "chmod", "link"}),
    "WORKDIR": frozenset(),
    "ENV": frozenset(),
    "USER": frozenset(),
    "EXPOSE": frozenset(),
    "LABEL": frozenset(),
    "CMD": frozenset(),
    "ENTRYPOINT": frozenset(),
    "HEALTHCHECK": frozenset({"interval", "timeout", "start-period", "start-interval", "retries"}),
    "STOPSIGNAL": frozenset(),
}
MOUNT_TYPES = frozenset({"bind", "cache", "tmpfs"})
MOUNT_KEYS = frozenset(
    {
        "type", "target", "dst", "destination", "source", "src", "from", "id", "sharing",
        "readonly", "ro", "rw", "readwrite", "mode", "uid", "gid", "size",
    }
)  # fmt: skip


def _refuse(number: int, why: str):
    raise ServiceError("BUILD_INVALID", f"Dockerfile line {number}: {why}")


def _text(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ServiceError("BUILD_INVALID", "a built service needs a regular Dockerfile")
    # Raw bytes: read_text's universal newlines would hide carriage returns from the check.
    try:
        return path.read_bytes().decode("ascii")
    except UnicodeDecodeError:
        raise ServiceError("BUILD_INVALID", "Dockerfile must be plain ASCII") from None


def check(path: Path) -> None:
    names: list[str] = []  # named stages, usable in FROM and --from
    count = 0  # stages so far; --from may also use an index

    def pinned(image: str, index: bool = True) -> bool:
        return bool(
            PINNED.fullmatch(image)
            or image in names
            or (index and image.isdigit() and int(image) < count)
        )

    for number, line in enumerate(_text(path).split("\n"), start=1):
        if not _LINE.fullmatch(line):
            _refuse(number, "only printable ASCII and tabs are allowed")
        stripped = line.strip(" \t")
        if not stripped or stripped.startswith("#"):
            # Anywhere, not just line 1: BuildKit reads directives from the leading comments,
            # and which comments count as leading is exactly where parsers differ.
            if _DIRECTIVE_LIKE.match(stripped) or stripped.startswith("#!"):
                _refuse(number, "parser directives (and comments that look like them) are refused")
            continue
        if stripped.endswith("\\"):
            _refuse(number, "line continuations are refused (one instruction per line)")
        if "<<" in stripped:
            _refuse(number, "heredocs are refused")
        words = stripped.split()
        instruction = words[0].upper()
        if instruction not in OPTIONS:
            _refuse(number, f"{instruction} is refused")
        position = 1
        while position < len(words) and words[position].startswith("--"):
            match = _OPTION.fullmatch(words[position])
            if not match or match.group(1) not in OPTIONS[instruction]:
                _refuse(number, f"unsupported {instruction} option")
            name, value = match.group(1), match.group(3) or ""
            if name == "from" and not pinned(value):
                _refuse(number, "--from must name an earlier stage or a digest-pinned image")
            if name == "mount":
                _check_mount(number, value, pinned)
            position += 1
        if instruction == "FROM":
            rest = words[position:]
            if not rest or not (pinned(rest[0], index=False) or rest[0] == "scratch"):
                _refuse(number, "FROM must be digest-pinned (name@sha256:...)")
            if len(rest) == 3 and rest[1].upper() == "AS" and STAGE.fullmatch(rest[2]):
                names.append(rest[2])
            elif len(rest) != 1:
                _refuse(number, "FROM takes an image and an optional `AS name`")
            count += 1


def _check_mount(number: int, value: str, pinned) -> None:
    fields: dict[str, str] = {}
    for part in value.split(","):
        key, _, item = part.partition("=")
        if key not in MOUNT_KEYS or key in fields:
            _refuse(number, "unsupported --mount option")
        fields[key] = item
    if fields.get("type", "bind") not in MOUNT_TYPES:
        _refuse(number, "build secrets and SSH are refused (mount types: bind, cache, tmpfs)")
    if "from" in fields and not pinned(fields["from"]):
        _refuse(number, "a mount's from must be an earlier stage or pinned")
