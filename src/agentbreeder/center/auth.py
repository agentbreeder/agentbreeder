"""Console sign-in (M2 #818, console spec §4.1, §8.1 #1, §8.2, §10 S-28, S-14, S-24).

Standard library only, so the server process imports no engine module for it. One user,
`admin@localhost`. A password only mints the console's header-bearer session token; it is never a
cookie, never stored, and never logged. The hash is scrypt with its parameters stored per row, so
they can be raised later without breaking rows already written.

What the password protects: the console against other OS users, browser pages and local
containers. What it does not: anything running as your own OS account, which can read the
console's files and run the CLI (spec §8.2, §10.1).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass

EMAIL = "admin@localhost"
MIN_LENGTH = 12
MAX_LENGTH = 1024  # characters; a request body is capped at 64 KiB anyway
# n=2**15, r=8 needs 128 * n * r = 32 MiB; maxmem leaves room above that.
SCRYPT = {"n": 2**15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024}
SALT_BYTES = 16
HASH_BYTES = 32
# A stored row outside these bounds is refused rather than hashed (a damaged row must not make
# one sign-in take minutes or gigabytes).
MAX_N = 2**20
MAX_R = 32
MAX_P = 16
MAX_MAXMEM = 256 * 1024 * 1024
MAX_WORK = 128 * 2**16 * 8 * 2  # bytes of 128*n*r*p: at most four times the default
# Backoff (S-24, S-14): global, not per email, checked before any hashing. Five failures in a
# row, then 30 s, doubling, never more than 5 minutes. The one-time terminal link is not
# subject to it.
FREE_FAILURES = 5
FIRST_WAIT = 30
MAX_WAIT = 300
SESSION_HOURS = (1, 4, 8, 24)  # the choices Settings offers; 8 is the default
DEFAULT_SESSION_HOURS = 8
BACKOFF_TEXT = (
    "Too many attempts. Wait {seconds} s, or press Enter in the terminal running "
    "`agentbreeder up` for a one-time link."
)

# Passwords of 12 or more characters that appear in public breach lists, plus obvious keyboard
# walks and repetitions. Compared case-insensitively. Short on purpose: the length rule does most
# of the work, and this list catches what people type when a form asks for 12 characters.
COMMON = frozenset(
    {
        "123456789012",
        "1234567890123",
        "12345678901234",
        "123456789123",
        "123123123123",
        "111111111111",
        "000000000000",
        "121212121212",
        "098765432109",
        "abcdefghijkl",
        "abcd1234abcd",
        "abc123abc123",
        "aaaaaaaaaaaa",
        "qwertyuiopas",
        "qwertyuiop12",
        "qwertyuiop123",
        "qwerty123456",
        "qwerty12345678",
        "1qaz2wsx3edc",
        "1q2w3e4r5t6y",
        "zaq12wsxcde3",
        "asdfghjkl123",
        "asdfasdfasdf",
        "zxcvbnm12345",
        "password1234",
        "password12345",
        "password123456",
        "password!123",
        "passwordpassword",
        "password1!password1!",
        "p@ssw0rd1234",
        "p@ssword1234",
        "passw0rd1234",
        "mypassword123",
        "changeme1234",
        "changemenow!",
        "letmein12345",
        "letmeinletmein",
        "iloveyou1234",
        "iloveyouiloveyou",
        "welcome12345",
        "welcome123456",
        "welcome@1234",
        "administrator",
        "administrator1",
        "admin1234567",
        "admin@localhost",
        "adminadminadmin",
        "superuser1234",
        "trustno1trustno1",
        "football1234",
        "baseball1234",
        "sunshine1234",
        "princess1234",
        "monkey123456",
        "dragon123456",
        "master123456",
        "starwars1234",
        "whatever1234",
        "correcthorsebatterystaple",
        "agentbreeder",
        "agentbreeder1",
        "agentbreeder123",
        "agentbreeder1234",
        "consolepassword",
    }
)


class PolicyError(ValueError):
    """Safe to show: says what the password must be. Never contains the password."""


def check_policy(password: str) -> None:
    """At least MIN_LENGTH characters, not on the common list, not one repeated character."""
    if not isinstance(password, str) or not password:
        raise PolicyError("Enter a password")
    if len(password) < MIN_LENGTH:
        raise PolicyError(f"Use at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        raise PolicyError(f"Use at most {MAX_LENGTH} characters")
    if password.strip() != password:
        raise PolicyError("The password cannot start or end with a space")
    if any(ord(character) < 32 or ord(character) == 127 for character in password):
        raise PolicyError("The password cannot contain control characters")
    folded = password.casefold()
    if folded in COMMON or len(set(folded)) == 1:
        raise PolicyError("That password is too common; choose another")


def _derive(password: str, salt: bytes, n: int, r: int, p: int, maxmem: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=maxmem, dklen=HASH_BYTES
    )


def hash_password(password: str, parameters: dict | None = None) -> dict:
    """A row for the `users` table: the salt, the hash and the parameters used."""
    chosen = dict(parameters or SCRYPT)
    salt = secrets.token_bytes(SALT_BYTES)
    return {"salt": salt, "hash": _derive(password, salt, **chosen), **chosen}


def _usable(row: dict) -> bool:
    try:
        n, r, p, maxmem = (int(row[key]) for key in ("n", "r", "p", "maxmem"))
    except (KeyError, TypeError, ValueError):
        return False
    return (
        2 <= n <= MAX_N
        and n & (n - 1) == 0
        and 1 <= r <= MAX_R
        and 1 <= p <= MAX_P
        and 0 < maxmem <= MAX_MAXMEM
        and 128 * n * r * p <= MAX_WORK
        and isinstance(row.get("salt"), bytes)
        and isinstance(row.get("hash"), bytes)
        and len(row["hash"]) == HASH_BYTES
    )


_dummy: dict | None = None
_dummy_lock = threading.Lock()


def _dummy_row() -> dict:
    """A hash of a random password with the default parameters, made once, so an unknown email
    (or a damaged row) costs the same scrypt work as a known one."""
    global _dummy
    with _dummy_lock:
        if _dummy is None:
            _dummy = hash_password(secrets.token_urlsafe(24))
        return _dummy


def verify(password: str, row: dict | None) -> bool:
    """Whether `password` matches `row`. Always runs scrypt once (against a dummy hash when the
    row is missing or unusable) and compares in constant time."""
    usable = row is not None and _usable(row)
    target = row if usable else _dummy_row()
    text = password if isinstance(password, str) else ""
    try:
        candidate = _derive(
            text,
            target["salt"],
            int(target["n"]),
            int(target["r"]),
            int(target["p"]),
            int(target["maxmem"]),
        )
    except ValueError:  # a row whose maxmem is too small for its own n, r and p
        dummy = _dummy_row()
        _derive(text, dummy["salt"], **{k: int(dummy[k]) for k in ("n", "r", "p", "maxmem")})
        return False
    return hmac.compare_digest(candidate, target["hash"]) and usable


def warm() -> None:
    """Make the dummy hash now (at start), not inside the first check that needs it."""
    _dummy_row()


def same_email(given: object) -> bool:
    return isinstance(given, str) and given.strip().casefold() == EMAIL


@dataclass
class Backoff:
    """Consecutive failed password checks, console-wide (S-24). A success resets it; a restart
    of the console does too (it lives in memory)."""

    failures: int = 0
    last: float = 0.0

    def wait(self, now: float | None = None) -> int:
        """Seconds until the next check may run; 0 when it may run now."""
        if self.failures < FREE_FAILURES:
            return 0
        delay = min(FIRST_WAIT * 2 ** (self.failures - FREE_FAILURES), MAX_WAIT)
        remaining = self.last + delay - (time.time() if now is None else now)
        return max(0, int(remaining + 0.999))

    def failed(self, now: float | None = None) -> None:
        self.failures += 1
        self.last = time.time() if now is None else now

    def succeeded(self) -> None:
        self.failures = 0
        self.last = 0.0


def session_hours(value: str | None) -> int:
    """The stored session length, or the default when unset or not one of the choices."""
    try:
        hours = int(value or "")
    except ValueError:
        return DEFAULT_SESSION_HOURS
    return hours if hours in SESSION_HOURS else DEFAULT_SESSION_HOURS
