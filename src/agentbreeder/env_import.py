"""Read a secret value from the environment or a dotenv file, for `secret set --from-env`.

The env file is parsed, never sourced or expanded: only `KEY=value` and `export KEY=value` lines,
optionally quoted, plus comments and blank lines. Errors give the line number, never the line,
since a malformed line may hold a secret. Well-known cloud, VCS and CI credentials can't be
imported: `services up` imports the names a repo's service entries declare, and a repo must not
be able to pull, say, an AWS key out of the operator's environment.
"""

from __future__ import annotations

import fnmatch
import os
import re
import stat
from pathlib import Path

MAX_BYTES = 64 * 1024
VARIABLE = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
_LINE = re.compile(r"^(?:export\s+)?([A-Za-z0-9_-]+)\s*=\s*(.*)$")
_EXPANSION = re.compile(r"\$\(|\$\{|`")

DENYLIST = (
    "AWS_*",
    "AZURE_*",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GCLOUD_*",
    "CLOUDSDK_*",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITLAB_TOKEN",
    "CI_JOB_TOKEN",
    "NPM_TOKEN",
    "NODE_AUTH_TOKEN",
    "PYPI_*",
    "TWINE_*",
    "DOCKER_*PASSWORD*",
    "DOCKER_*TOKEN*",
    "*_SSH_*",
    "SSH_*",
    "GIT_SSH*",
    "KUBECONFIG",
    "VAULT_TOKEN",
    "HOMEBREW_GITHUB_API_TOKEN",
)


class EnvImportError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def denied(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in DENYLIST)


def _read(path: Path) -> str:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise EnvImportError(
            "ENV_FILE_UNSAFE", f"Cannot open env file {path}: {error.strerror}"
        ) from None
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        os.close(descriptor)
        raise EnvImportError("ENV_FILE_UNSAFE", f"{path} must be a regular file owned by you")
    if info.st_mode & 0o022:
        os.close(descriptor)
        raise EnvImportError("ENV_FILE_UNSAFE", f"{path} must not be group or world writable")
    with os.fdopen(descriptor, "rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise EnvImportError("ENV_FILE_UNSAFE", f"{path} is larger than {MAX_BYTES} bytes")
    try:
        return data.decode()
    except UnicodeDecodeError:
        raise EnvImportError("ENV_FILE_INVALID", f"{path} is not UTF-8") from None


def _value(raw: str, number: int) -> str:
    def invalid(reason: str) -> EnvImportError:
        return EnvImportError("ENV_FILE_INVALID", f"env file line {number}: {reason}")

    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        quote = raw[0]
        end = raw.find(quote, 1)
        if end < 0:
            raise invalid("unclosed quote")
        rest = raw[end + 1 :].strip()
        if rest and not rest.startswith("#"):
            raise invalid("text after the closing quote")
        value = raw[1:end]
        if quote == '"' and _EXPANSION.search(value):
            raise invalid("shell expansion is not supported")
        return value
    value = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
    if _EXPANSION.search(value):
        raise invalid("shell expansion is not supported")
    return value


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, line in enumerate(_read(Path(path)).splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _LINE.match(stripped)
        if not match or not VARIABLE.fullmatch(match.group(1)):
            raise EnvImportError("ENV_FILE_INVALID", f"env file line {number}: expected KEY=value")
        values[match.group(1)] = _value(match.group(2), number)
    return values


def lookup(name: str, env_file: Path | None = None) -> str | None:
    """The value of `name` from the process environment, else from `env_file`; None if unset."""
    if not VARIABLE.fullmatch(name):
        raise EnvImportError("IMPORT_NAME", f"Variable names must match {VARIABLE.pattern}")
    if denied(name):
        raise EnvImportError(
            "IMPORT_DENIED",
            f"{name} looks like a cloud, VCS or CI credential and can't be imported",
        )
    if name in os.environ:
        return os.environ[name]
    if env_file is not None:
        return parse_env_file(env_file).get(name)
    return None
