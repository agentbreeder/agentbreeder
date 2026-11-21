"""The user's trusted registry sources (#829 spec §8.3, D1).

A fetch reaches only sources the user has trusted, in every mode. The list lives in the user's
own `~/.agentbreeder/sources.yaml` (owner-only, checked like the operator config), never in the
project, plus `AGENTBREEDER_REGISTRY_TRUST` (comma-separated prefix entries) that whoever runs CI
sets. No code path derives either from project files.

    trusted:
      - https://github.com/acme              # scheme + [user@]host[:port] + path prefix
      - url: https://git.corp.example/acme   # a host on a private address needs private: true
        private: true
      - url: https://github.com              # a bare host trusts every repository there
        whole_host: true

Entries are canonicalised exactly like source URLs (`sources.url.canonical`) and match the
canonical source URL on whole path segments; the ssh user must match exactly. Two spellings of
one address are two different strings, so an alias fails closed: a trust entry for one is not a
trust entry for the other. IPv4-mapped IPv6 hosts (`[::ffff:127.0.0.1]`) and decimal or hex IPv4
spellings are refused outright by `sources.url.canonical`.

`check` also refuses a host that resolves to a loopback, private, link-local, unique-local or
otherwise non-global address unless its entry says `private: true` (an IPv4 address mapped or
embedded in IPv6 is checked as IPv4). It returns the checked addresses so git can be pinned to
them (`http.curloptResolve`). Over ssh, and behind a proxy, the check is best effort: ssh resolves
the host itself and a proxy resolves the target.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from agentbreeder import hints
from agentbreeder.registry import RegistryError, _shown
from agentbreeder.sources.url import SourceUrlError, canonical
from agentbreeder.strict_yaml import StrictYamlError, load_file

ENV = "AGENTBREEDER_REGISTRY_TRUST"
FILE_LIMIT = 64 * 1024
MAX_ENTRIES = 200
_SENTINEL = "_"
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")
# Not global, whatever `is_global` says: IPv4-compatible (deprecated) and site-local (deprecated).
_REFUSED_V6 = (ipaddress.IPv6Network("::/96"), ipaddress.IPv6Network("fec0::/10"))


def sources_file() -> Path:
    """`~/.agentbreeder/sources.yaml`, resolved at call time (HOME may change in tests)."""
    return Path.home() / ".agentbreeder" / "sources.yaml"


@dataclass(frozen=True)
class Entry:
    prefix: str  # canonical, without a trailing slash
    private: bool = False
    whole_host: bool = False
    origin: str = "file"  # "file" or "env"


def prefix(text: object, *, whole_host: bool = False) -> tuple[str, bool]:
    """(canonical prefix, is a bare host) for a trust entry; SourceUrlError otherwise."""
    if not isinstance(text, str):
        raise SourceUrlError("a trust entry must be a URL string")
    stripped = text.rstrip("/")
    scheme, sep, rest = stripped.partition("://")
    bare = bool(sep) and "/" not in rest
    if bare:
        value = canonical("git", f"{stripped}/{_SENTINEL}")[: -len(_SENTINEL) - 1]
    else:
        value = canonical("git", stripped)
    if bare and not whole_host:
        raise SourceUrlError(
            f"{value} is a whole host: it trusts every repository there, including an "
            "attacker's; trust a path prefix such as an organization, or pass --whole-host"
        )
    return value, bare


def matches(entry: str, url: str) -> bool:
    """Whether canonical `url` is under canonical prefix `entry`, on whole path segments."""
    return url == entry or url.startswith(entry + "/")


def _check_file(path: Path) -> None:
    """Owned by this user, mode 0600-style, a regular file (not a link): as the operator config."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise RegistryError("SOURCE_TRUST_INVALID", f"{_shown(path)} must be a regular file")
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RegistryError(
            "SOURCE_TRUST_INVALID", f"{_shown(path)} must be owned by you with mode 0600"
        )


def _file_entries(path: Path) -> list[Entry]:
    if not os.path.lexists(path):
        return []
    _check_file(path)
    try:
        data = load_file(path, limit=FILE_LIMIT)
    except StrictYamlError as error:
        raise RegistryError("SOURCE_TRUST_INVALID", f"{_shown(path)}: {error}") from error
    if not isinstance(data, dict) or set(data) - {"trusted"}:
        raise RegistryError("SOURCE_TRUST_INVALID", f"{_shown(path)}: only `trusted:` is allowed")
    items = data.get("trusted") or []
    if not isinstance(items, list) or len(items) > MAX_ENTRIES:
        raise RegistryError(
            "SOURCE_TRUST_INVALID", f"{_shown(path)}: trusted must be a list (≤ {MAX_ENTRIES})"
        )
    result = []
    for index, item in enumerate(items):
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict) or set(item) - {"url", "private", "whole_host"}:
            raise RegistryError(
                "SOURCE_TRUST_INVALID", f"{_shown(path)}: trusted[{index}] has unknown keys"
            )
        private, whole = item.get("private", False), item.get("whole_host", False)
        if type(private) is not bool or type(whole) is not bool:
            raise RegistryError(
                "SOURCE_TRUST_INVALID",
                f"{_shown(path)}: trusted[{index}] private and whole_host are true or false",
            )
        try:
            value, _ = prefix(item.get("url"), whole_host=whole)
        except SourceUrlError as error:
            raise RegistryError(
                "SOURCE_TRUST_INVALID", f"{_shown(path)}: trusted[{index}]: {error}"
            ) from None
        result.append(Entry(value, private, whole, "file"))
    return result


def _env_entries() -> list[Entry]:
    """`AGENTBREEDER_REGISTRY_TRUST`: prefix entries only; a bare host or a private host needs
    the file (`--whole-host`, `--private`), so a CI variable cannot widen trust that far."""
    result = []
    for text in os.environ.get(ENV, "").split(","):
        if not text.strip():
            continue
        try:
            value, _ = prefix(text.strip())
        except SourceUrlError as error:
            raise RegistryError("SOURCE_TRUST_INVALID", f"{ENV}: {error}") from None
        result.append(Entry(value, origin="env"))
    return result


def entries() -> list[Entry]:
    return _file_entries(sources_file()) + _env_entries()


def _interactive() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (OSError, ValueError):
        return False


def _hint_prefix(url: str) -> str:
    """The first path segment (an organization), or the whole path when it has one segment."""
    scheme, _, rest = url.partition("://")
    authority, _, path = rest.partition("/")
    first = path.split("/")[0]
    return f"{scheme}://{authority}/{first}"


def _host_port(url: str) -> tuple[str, int]:
    scheme, _, rest = url.partition("://")
    authority = rest.partition("/")[0].rpartition("@")[2]
    default = 22 if scheme == "ssh" else 443
    if authority.startswith("["):
        host, _, port = authority[1:].partition("]")
        return host, int(port[1:]) if port else default
    host, _, port = authority.partition(":")
    return host, int(port) if port else default


def _global(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is None and ip in _NAT64:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            return embedded.is_global and not embedded.is_multicast
        if any(ip in network for network in _REFUSED_V6):
            return False
    return ip.is_global and not ip.is_multicast


def resolve(host: str, port: int) -> list[str]:
    """Every address `host` resolves to (an IP literal resolves to itself, without DNS)."""
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


@dataclass(frozen=True)
class Checked:
    """A trusted source URL and every address its host was checked at."""

    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


def check(name: str, url: str, *, kind: str = "git") -> Checked:
    """Refuse a fetch from an untrusted URL or a private address (spec §8.3). Called inside the
    fetcher before every network call, in every mode."""
    found = [entry for entry in entries() if matches(entry.prefix, url)]
    host, port = _host_port(url)
    if not found:
        suggestion = _hint_prefix(url)
        where = "" if _interactive() else f"; in CI set {ENV}={suggestion}"
        raise RegistryError(
            "SOURCE_HOST_UNTRUSTED",
            f"source '{_shown(name)}' fetches from {url}, which you have not trusted{where}. "
            f"If you trust it: {hints.command('registry', 'trust', suggestion)}",
        )
    try:
        addresses = resolve(host, port)
    except OSError as error:
        raise RegistryError(
            "SOURCE_UNREACHABLE",
            f"source '{_shown(name)}' ({kind}, {host}): could not resolve the host "
            f"({type(error).__name__}). Already fetched? Re-run with --offline. Otherwise check "
            "the network or VPN. No longer used? Remove it from agent.yaml.",
        ) from None
    if not addresses:
        raise RegistryError(
            "SOURCE_UNREACHABLE", f"source '{_shown(name)}': {host} has no address"
        )
    private = [address for address in addresses if not _global(address)]
    if private and not any(entry.private for entry in found):
        raise RegistryError(
            "SOURCE_HOST_PRIVATE",
            f"source '{_shown(name)}': {host} resolves to a private address ({private[0]}). If "
            f"this is your internal host: "
            f"{hints.command('registry', 'trust', _hint_prefix(url), '--private')}",
        )
    return Checked(url, host, port, tuple(addresses))


def write(path: Path, items: list[dict]) -> None:
    """Rewrite the user's file atomically, mode 0600 in a 0700 directory."""
    import yaml

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    body = []
    for item in items:
        flags = {k: True for k in ("private", "whole_host") if item.get(k)}
        body.append({"url": item["url"], **flags} if flags else item["url"])
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(yaml.safe_dump({"trusted": body}, sort_keys=False))
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def file_items(path: Path) -> list[dict]:
    """The file's entries as {url, private, whole_host} for `registry trust` to edit."""
    return [
        {"url": e.prefix, "private": e.private, "whole_host": e.whole_host}
        for e in _file_entries(path)
    ]
