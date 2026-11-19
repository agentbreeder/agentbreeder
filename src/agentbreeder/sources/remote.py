"""The declared sources of one build (#829 spec §4, §8.1): `lock.materialise`'s `RemoteSet`.

Each source is fetched at most once per build, into a fresh private repository (`sources.git`)
deleted when the build ends. When are the network and the cache used:

- a ref already locked from a source at the pin declared now may come from `trees/` in the
  cache; the copy's hash must still equal the lock (`lock._reconcile`), and a cached tree whose
  hash differs is deleted (CACHE_CORRUPT) and fetched again;
- a first lock of a ref (or a ref after a pin change) is always re-derived from the pinned
  commit, never from `trees/`, so a planted tree cannot become a first lock;
- whether a source holds a ref comes from its listing at the pin, cached in `listings/` (a
  plain build needs the network once per source per pin, to check for conflicts).

`offline=True` never uses the network: anything not in the cache is SOURCE_OFFLINE.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from agentbreeder import hints, registry
from agentbreeder.registry import Ref, RegistryError, _shown
from agentbreeder.sources import short_pin
from agentbreeder.sources.cache import Cache
from agentbreeder.sources.git import Repo


class Remotes:
    """A `sources.RemoteSet` over git sources. Use as a context manager."""

    def __init__(
        self, table: dict[str, dict], *, offline: bool = False, cache: Cache | None = None
    ) -> None:
        self._table = dict(table)
        self.offline = offline
        self.cache = cache if cache is not None else Cache()
        self._repos: dict[str, Repo] = {}
        self._listings: dict[str, dict[str, str | None]] = {}
        self._versions: dict[tuple[str, str], Path] = {}
        self._staging = Path(tempfile.mkdtemp(prefix="agentbreeder-sources-"))
        os.chmod(self._staging, 0o700)

    def __enter__(self) -> Remotes:
        self.cache.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        for repo in self._repos.values():
            repo.close()
        self._repos.clear()
        shutil.rmtree(self._staging, ignore_errors=True)
        self.cache.__exit__(None, None, None)

    def table(self) -> dict[str, dict]:
        return self._table

    def describe(self, name: str) -> str:
        record = self._table[name]
        return f"source '{_shown(name)}' ({record['type']} {record['url']} @{short_pin(record)})"

    def _fetched(self, name: str, why: str) -> Repo:
        if name in self._repos:
            return self._repos[name]
        if self.offline:
            raise RegistryError(
                "SOURCE_OFFLINE",
                f"source '{_shown(name)}' is not in the cache ({why}); "
                f"run {hints.command('build')} without --offline once",
            )
        record = self._table[name]
        repo = Repo(name)
        try:
            repo.fetch(record["url"], record["pin"]["commit"])
            names = repo.listing(record.get("path", "."))
        except BaseException:
            repo.close()
            raise
        self._repos[name] = repo
        if not names:
            print(
                f"[SOURCE_EMPTY] source '{_shown(name)}' has no registry versions under path "
                f"'{_shown(record.get('path', '.'))}' at {record['pin']['commit'][:7]}; check "
                "its path: in agent.yaml",
                file=sys.stderr,
            )
        known = self.cache.listing(record) or {}
        self._listings[name] = {ref: known.get(ref) for ref in names}
        self.cache.store_listing(record, self._listings[name])
        return repo

    def _listing(self, name: str) -> dict[str, str | None]:
        if name not in self._listings:
            cached = self.cache.listing(self._table[name])
            if cached is not None:
                self._listings[name] = cached
            else:
                self._fetched(name, "its listing is needed once per pin to check for conflicts")
        return self._listings[name]

    def provides(self, name: str, ref: Ref, locked: dict | None) -> bool:
        return locked is not None or str(ref) in self._listing(name)

    def version(self, name: str, ref: Ref, locked: dict | None) -> Path:
        key = (name, str(ref))
        if key in self._versions:
            return self._versions[key]
        if locked is not None:
            tree = self.cache.tree(locked["sha256"])
            if tree is not None:
                try:
                    digest = registry.copy_and_hash(tree)
                except RegistryError:
                    digest = None
                if digest == locked["sha256"]:
                    self._versions[key] = tree
                    return tree
                self.cache.discard(tree)
                print(
                    f"[CACHE_CORRUPT] the cached copy of {ref} did not match agent.lock; it "
                    "was deleted and is fetched again",
                    file=sys.stderr,
                )
            why = f"{ref} is locked but not cached"
        else:
            if self.offline:
                raise RegistryError(
                    "SOURCE_OFFLINE",
                    f"{ref} from source '{_shown(name)}' is not locked yet; a first lock needs "
                    f"the network: run {hints.command('build')} without --offline",
                )
            why = f"{ref} is not locked yet"
        repo = self._fetched(name, why)
        path = repo.extract(self._table[name].get("path", "."), ref, self._staging / name)
        self._versions[key] = path
        return path

    def remember(self, name: str, ref: Ref, digest: str, copy: Path) -> None:
        self.cache.store_tree(digest, copy)
        listing = self._listings.get(name)
        if listing is not None and listing.get(str(ref)) != digest:
            listing[str(ref)] = digest
            self.cache.store_listing(self._table[name], listing)
