"""The registry-source cache (#829 spec §8.1).

`AGENTBREEDER_CACHE_DIR` (default `~/.agentbreeder/cache`), sources under `registries/v1/`:

- `trees/<tree hex>/` one extracted version, by its tree hash (`registry.copy_and_hash`);
- `listings/<sha256 of type, url, pin, path>.json` the ref names a source holds at a pin, plus a
  tree hash per ref once that ref was materialised.

The directory must be owned by the user and mode 0700 on every use (CACHE_UNSAFE). Every write
goes to a temporary sibling and is renamed into place, under a shared `flock` on
`registries/v1/.lock`, so concurrent builds never see a partial entry.

Nothing read from the cache is trusted on its own: a tree is used only for a ref already in
`agent.lock`, and the copy's hash must equal the lock (the hash comes from the committed lock,
not from the cache); a first lock never reads `trees/`. A listing can at worst hide or invent a
conflict; it never supplies built bytes.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

from agentbreeder import registry
from agentbreeder.registry import DIGEST, REF, RegistryError, _shown

ENV = "AGENTBREEDER_CACHE_DIR"
LISTING_VERSION = 1
LISTING_LIMIT = 4 * 1024 * 1024
LISTING_REFS = 100_000


def base() -> Path:
    value = os.environ.get(ENV)
    return Path(value).expanduser() if value else Path.home() / ".agentbreeder" / "cache"


def _private_dir(path: Path) -> None:
    """Create `path` (0700) or check an existing one: a real directory, ours, 0700."""
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise RegistryError("CACHE_UNSAFE", f"{_shown(path)} must be a directory, not a link")
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RegistryError("CACHE_UNSAFE", f"{_shown(path)} must be owned by you with mode 0700")


class Cache:
    def __init__(self, root: Path | None = None) -> None:
        top = root if root is not None else base()
        top.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _private_dir(top)
        self.dir = top / "registries"
        _private_dir(self.dir)
        self.dir = self.dir / "v1"
        _private_dir(self.dir)
        for name in ("trees", "listings"):
            _private_dir(self.dir / name)
        self._lock = None

    def __enter__(self) -> Cache:
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
        self._lock = os.open(self.dir / ".lock", flags, 0o600)
        fcntl.flock(self._lock, fcntl.LOCK_SH)
        return self

    def __exit__(self, *exc) -> None:
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None

    # trees

    def tree(self, digest: str) -> Path | None:
        """The cached tree for `digest`, if present and a real directory (not yet verified)."""
        if not DIGEST.fullmatch(digest):
            return None
        path = self.dir / "trees" / digest.removeprefix("sha256:")
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return None
        return path if stat.S_ISDIR(info.st_mode) else None

    def discard(self, path: Path) -> None:
        if path.parent == self.dir / "trees":
            shutil.rmtree(path, ignore_errors=True)

    def store_tree(self, digest: str, source: Path) -> None:
        """Copy `source` (a hashed copy in the build context) into `trees/<hex>`, atomically;
        the copy's hash must equal `digest`, else nothing is stored."""
        if not DIGEST.fullmatch(digest) or self.tree(digest) is not None:
            return
        staging = Path(tempfile.mkdtemp(prefix=".tmp-", dir=self.dir / "trees"))
        try:
            if registry.copy_and_hash(source, staging / "t") != digest:
                return
            os.rename(staging / "t", self.dir / "trees" / digest.removeprefix("sha256:"))
        except OSError:
            pass  # another build stored it first; the cache is an optimisation
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    # listings

    @staticmethod
    def listing_key(record: dict) -> str:
        """sha256 of (type, url, pin, path): a pin is immutable, so the listing only grows."""
        parts = [
            record["type"],
            record["url"],
            json.dumps(record["pin"], sort_keys=True),
            record.get("path", "."),
        ]
        return hashlib.sha256("\0".join(parts).encode()).hexdigest()

    def listing(self, record: dict) -> dict[str, str | None] | None:
        """ref → tree hash (or None), parsed strictly; any problem discards the file."""
        path = self.dir / "listings" / f"{self.listing_key(record)}.json"
        try:
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_size > LISTING_LIMIT:
                raise ValueError
            data = json.loads(path.read_bytes())
            if (
                not isinstance(data, dict)
                or set(data) != {"listing_version", "refs"}
                or data["listing_version"] != LISTING_VERSION
                or not isinstance(data["refs"], dict)
                or len(data["refs"]) > LISTING_REFS
            ):
                raise ValueError
            for ref, digest in data["refs"].items():
                if not REF.fullmatch(ref) or not (
                    digest is None or (isinstance(digest, str) and DIGEST.fullmatch(digest))
                ):
                    raise ValueError
            return data["refs"]
        except FileNotFoundError:
            return None
        except (OSError, ValueError, RecursionError):
            try:
                os.unlink(path)
            except OSError:
                pass
            return None

    def store_listing(self, record: dict, refs: dict[str, str | None]) -> None:
        path = self.dir / "listings" / f"{self.listing_key(record)}.json"
        body = json.dumps(
            {"listing_version": LISTING_VERSION, "refs": dict(sorted(refs.items()))}
        ).encode()
        descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(body)
            os.replace(temporary, path)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass
