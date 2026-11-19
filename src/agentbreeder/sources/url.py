"""Canonical URLs for remote registry sources (#829 spec §5).

Every source URL is reduced to one canonical string. Trust matching, `agent.lock`'s `sources`,
messages and the argv of `git`/`urllib` use that string, never the raw text, so two spellings of
one repository cannot be trusted, locked or fetched differently. Pure functions: no network,
no filesystem.

Rules (spec §5):
- git: `https://host[:port]/path` or `ssh://[user@]host[:port]/path`; scp-style `user@host:path`
  becomes `ssh://user@host/path`. Every other transport (`file://`, `http://`, `ext::`, a local
  path) is refused.
- http index: `https://host[:port]/path`.
- oci: `host[:port]/repository` (no scheme, no tag or digest: the pin is separate).
- No credentials: userinfo in an https URL, a password in an ssh URL, and a query or fragment in
  any URL are refused (they would be copied into the committed lock).
- Scheme and host lower-cased; the host is ASCII (no IDN), without a trailing dot; the default
  port (443, 22) is dropped; path segments are non-empty, never `.`/`..`, and carry no `%`, `~`
  or backslash; no host or segment starts with `-` (`-oProxyCommand=…`).
"""

from __future__ import annotations

import ipaddress
import re

TYPES = ("git", "oci", "http")
MAX_URL = 2048

_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_USER = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._-]{0,63}")
_SEGMENT = re.compile(r"[A-Za-z0-9._@+=,-]+")
_OCI_SEGMENT = re.compile(r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*")
_SCP = re.compile(r"(?P<user>[^@/:]+)@(?P<host>[^/:]+):(?P<path>[^/].*)")
_PORT = re.compile(r"[1-9][0-9]{0,4}")  # ASCII only: str.isdigit accepts '²'
_DEFAULT_PORTS = {"https": 443, "ssh": 22}


class SourceUrlError(ValueError):
    """A source URL that does not canonicalise; the message names the rule."""


def _host(text: str) -> str:
    """A lower-cased ASCII host name or IP literal (IPv6 in brackets)."""
    if not text:
        raise SourceUrlError("the URL has no host")
    if text.startswith("["):
        if not text.endswith("]") or "%" in text:
            raise SourceUrlError("malformed IPv6 host (no zone ids)")
        try:
            address = ipaddress.IPv6Address(text[1:-1])
        except ValueError as error:
            raise SourceUrlError("malformed IPv6 host") from error
        return f"[{address.compressed}]"
    if not text.isascii():
        raise SourceUrlError("non-ASCII host names are not supported")
    host = text.lower()
    if host.endswith("."):
        host = host[:-1]
    if host.startswith("-"):
        raise SourceUrlError("a host must not start with '-'")
    try:
        return str(ipaddress.IPv4Address(host))
    except ValueError:
        pass
    labels = host.split(".")
    if labels[-1].isdigit() or labels[-1].startswith("0x"):
        # Only a strict dotted quad is an IPv4 host: `2130706433`, `0x7f.1`, `127.1` or
        # `010.1.1.1` would resolve to an address under a second spelling (WHATWG rule).
        raise SourceUrlError(f"invalid host {host!r}: write an IPv4 address as a dotted quad")
    if len(host) > 253 or not all(_LABEL.fullmatch(label) for label in labels):
        raise SourceUrlError(f"invalid host {host!r}")
    return host


def _port(text: str | None, scheme: str | None) -> str:
    if text is None:
        return ""
    if not _PORT.fullmatch(text) or int(text) > 65535:
        raise SourceUrlError(f"invalid port {text!r}")
    if scheme is not None and int(text) == _DEFAULT_PORTS.get(scheme):
        return ""
    return f":{int(text)}"


def _authority(text: str, scheme: str | None) -> tuple[str, str]:
    """(user, host[:port]) of `[user@]host[:port]`; user is '' when absent."""
    user = ""
    if "@" in text:
        user, text = text.rsplit("@", 1)
        if scheme != "ssh":
            raise SourceUrlError("credentials in the URL are not allowed; use git credentials")
        if ":" in user:
            raise SourceUrlError("a password in the URL is not allowed")
        if not _USER.fullmatch(user):
            raise SourceUrlError(f"invalid ssh user {user!r}")
    port = None
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            raise SourceUrlError("malformed IPv6 host")
        host, rest = text[: end + 1], text[end + 1 :]
        if rest:
            if not rest.startswith(":"):
                raise SourceUrlError("malformed host")
            port = rest[1:]
    elif ":" in text:
        host, port = text.rsplit(":", 1)
    else:
        host = text
    return user, _host(host) + _port(port, scheme)


def _segments(path: str, *, oci: bool = False) -> list[str]:
    if not path:
        raise SourceUrlError("the URL needs a path")
    parts = path.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise SourceUrlError("path segments must be non-empty and not '.' or '..'")
        if part.startswith("-"):
            raise SourceUrlError("a path segment must not start with '-'")
        if "%" in part or "~" in part or "\\" in part:
            raise SourceUrlError("'%', '~' and '\\' are not allowed in the path")
        if not (_OCI_SEGMENT if oci else _SEGMENT).fullmatch(part):
            raise SourceUrlError(f"invalid path segment {part!r}")
    return parts


def canonical(kind: str, text: object) -> str:
    """The canonical form of a `kind` (git, oci, http) source URL; SourceUrlError otherwise."""
    if kind not in TYPES:
        raise SourceUrlError(f"unknown source type {kind!r}")
    if not isinstance(text, str) or not text:
        raise SourceUrlError("the URL must be a non-empty string")
    if len(text) > MAX_URL or not text.isprintable() or any(c.isspace() for c in text):
        raise SourceUrlError("the URL is too long or contains spaces or control characters")
    if "?" in text or "#" in text:
        raise SourceUrlError("a query or fragment in the URL is not allowed")
    if kind == "oci":
        if "://" in text:
            raise SourceUrlError("an OCI reference has no scheme: host/repository")
        if "@" in text:
            raise SourceUrlError("the OCI digest goes in the pin, not the URL")
        host, _, path = text.partition("/")
        _, authority = _authority(host, None)
        if not any(mark in authority for mark in (".", ":", "[")):
            # `a/b` is docker.io/a/b to Docker: name the registry host explicitly.
            raise SourceUrlError("an OCI reference needs a registry host (with a dot or port)")
        if ":" in path:
            raise SourceUrlError("an OCI tag is not a pin; use a digest")
        return authority + "/" + "/".join(_segments(path, oci=True))
    scheme, sep, rest = text.partition("://")
    if not sep:
        match = _SCP.fullmatch(text) if kind == "git" else None
        if match is None:
            raise SourceUrlError("the URL needs https:// (or ssh:// for git)")
        if match["path"].startswith("~"):
            raise SourceUrlError("'~' paths are not allowed")
        scheme, rest = "ssh", f"{match['user']}@{match['host']}/{match['path']}"
    scheme = scheme.lower()
    allowed = ("https", "ssh") if kind == "git" else ("https",)
    if scheme not in allowed:
        raise SourceUrlError(f"the {scheme} transport is not allowed; use {' or '.join(allowed)}")
    authority, _, path = rest.partition("/")
    user, host = _authority(authority, scheme)
    prefix = f"{user}@" if user else ""
    return f"{scheme}://{prefix}{host}/" + "/".join(_segments(path))


def subpath(text: object) -> str:
    """A git source's `path:` (the subdirectory holding kind/name/version): relative, no `..`,
    no leading `/` or `:` (git pathspec magic). '.' is the repository root."""
    if text == ".":
        return "."
    if not isinstance(text, str) or not text or len(text) > 512:
        raise SourceUrlError("path must be a non-empty relative path")
    if text.startswith(("/", ":")):
        raise SourceUrlError("path must be relative and must not start with ':'")
    try:
        return "/".join(_segments(text))
    except SourceUrlError as error:
        raise SourceUrlError(f"path: {error}") from None
