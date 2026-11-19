"""`agentbreeder up`: a loopback HTTP server for the console (spec §6, §8.1).

Single user. Standard library only, so an install keeps its two dependencies. Binds 127.0.0.1 and
offers no way to bind anything else. Every engine action is a child process of the CLI (`jobs.py`);
nothing here opens the deployment state.

Requests go through one route table (`ROUTES`). Before any handler runs, `Handler._dispatch`
applies the same guards to every method (S-03): the `Host`, the API header and
`Sec-Fetch-Site`, then for every non-GET method `Origin` and the content type, then the session,
then the route's body cap. Unknown methods get 405. Every API reply is an envelope
`{"data": ..., "error": null | {"code", "message"}, "stale": bool}`.
"""

from __future__ import annotations

import errno
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import socket
import sqlite3
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from agentbreeder import __version__, guide, registry
from agentbreeder.center import agents as agent_rows
from agentbreeder.center import (
    auth,
    builder,
    edits,
    knowledgestatus,
    logfollow,
    mcpgrants,
    onboard,
    pinsreview,
    registryimport,
    toollog,
    tryit,
)
from agentbreeder.center import cost as cost_reads
from agentbreeder.center import evals as eval_reads
from agentbreeder.center import traces as trace_lists
from agentbreeder.center import usage as usage_feeds
from agentbreeder.center import watch as watch_feeds
from agentbreeder.center.files import (
    BadRequest,
    check_ancestors,
    check_project,
    private_dir,
    write_private,
)
from agentbreeder.center.instance import AlreadyRunning, SingleInstance, remove_leftovers
from agentbreeder.center.jobs import (
    DIGEST,
    ENVIRONMENT,
    KEY_NAME,
    SCHEMA_VERSION,
    Busy,
    CenterState,
    ChildRecords,
    Runner,
    SchemaTooNew,
    WriteBusy,
    cli_error,
    error,
)
from agentbreeder.center.reads import Reads

PORTS = tuple(range(8080, 8100))  # 3.0.0a2 used 8900
TOKEN_TTL = 300
SESSION_IDLE = 3600
MAX_BODY = 64 * 1024  # JSON requests
MAX_EDITOR_BODY = 1024 * 1024  # editor payloads (prompts, evals, tools; later milestones)
MAX_DISCARD = 16 * 1024 * 1024  # an over-cap body read and dropped so the 413 arrives
DISCARD_SECONDS = 5
STATIC = Path(__file__).parent / "static"
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}
EXAMPLES = ("northwind",)
# `new agent`'s own name rule (`newagent.NAME_PATTERN`; a test keeps them equal). Checked here
# first so a refused name never reaches a child process or creates the projects folder.
RELEASE_ROWS = 1000  # audit rows read for an agent's releases: `audit list`'s maximum
STARTER_NAME = re.compile(r"^[a-z]([a-z0-9-]{0,29}[a-z0-9])?$")
GUIDE_TIMEOUT = 60  # `next` runs doctor's checks and one `compose ps`
# `build`'s `lock: "update"` drops --locked after "Move agent to this version" (#826).
OPTIONS = {
    "services-up": ("digest", "allow_project_mcp", "pins_digest"),
    "remove": ("deployment", "target"),
    "build": ("lock",),
}
# A console session is code execution as the owner (console spec §10.1 I1), so the browser-side
# guards are a primary control: scripts and styles from this origin only, no inline code, and
# Trusted Types required with no policy allowed, so every HTML sink (innerHTML and the like)
# throws.
# `default-src 'none'` is stricter than the plan's `'self'`: each kind the console uses is named.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; font-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'; trusted-types 'none'; require-trusted-types-for 'script'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
PROJECT_ID = r"(?P<project>[0-9a-f]{12})"
JOB_ID = r"(?P<job>[0-9a-f]{32})"
# Log reads (#821, M5). A service name is `services.schema.SERVICE_NAME` and `--since` is
# `runtime.SINCE` (tests keep both equal; neither module is loaded by the server process).
SERVICE = r"(?P<service>[a-z][a-z0-9-]{0,30})"
DEPLOYMENT = r"(?P<deployment>[0-9a-f]{32})"
FOLLOW_ID = r"(?P<follow>[0-9a-f]{32})"
LOG_SINCE = re.compile(
    r"[1-9][0-9]{0,5}[smh]|[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z"
)
LOG_TAIL = 200  # the CLI's default
MAX_LOG_TAIL = 10000  # `runtime.MAX_TAIL`
LOG_TIMEOUT = 60  # one `docker logs` or `kubectl logs`, after the CLI's own checks
# Try it (#823, M7): turns in flight across the console. Each holds a child for up to
# `tryit.TIMEOUT`; the screen sends one at a time per tab.
MAX_TURNS_IN_FLIGHT = 4
# SERVICES_BUSY (#822 S-11): another services write holds the stack; the console says so plainly.
SERVICES_BUSY_TEXT = "Another operation is running on this stack; retry when it finishes"
# Service controls (#822, M6): one container, through `services start|stop|restart NAME --json`.
SERVICE_ACTIONS = ("start", "stop", "restart")
SERVICE_CONTROL = "service-control"  # the short-write kind; it also holds the project
# A stack service (`SERVICE`) or an MCP relay, `mcp-` + a server name of up to 32 characters
# (`services.schema.MCP_SERVER`, `_` written as `-`): the names `services start|stop|restart` take.
SERVICE_OR_RELAY = r"(?P<service>[a-z][a-z0-9-]{0,35})"
MAX_TRACES_IN_FLIGHT = 4  # Try it trace reads (#907), each a `traces` child of up to 120 s
MAX_TOOL_LOGS_IN_FLIGHT = 4  # tool execution log reads (#828), each a `tool log` child
DOCKER_REUSE = 3.0  # seconds one Docker check answers every tab (M6 #822)
DOCKER_TIMEOUT = 15  # `doctor --only docker`: one engine check with its own 10 s timeout
MAX_KNOWLEDGE_IN_FLIGHT = 4  # knowledge status reads (#830), each a `knowledge status` child
MAX_COST_IN_FLIGHT = 2  # LLM cost reads (#906), each a `cost` child of up to 120 s
AUDIT_PAGE = 50  # rows per Audit page; one more is read to know whether an older page exists
AUDIT_BEFORE = re.compile(r"[1-9][0-9]{0,17}")  # `audit list --before SEQ`
AGENT = r"(?P<agent>[a-z0-9][a-z0-9-]{0,52})"
EVAL_RUN = r"(?P<run>[0-9]{8}-[0-9]{6}-[0-9a-f]{6})"  # eval_reports.RUN
TRACE = r"(?P<trace>[0-9a-f]{32})"  # a Try it turn's trace (#907)


def center_directory() -> Path:
    import os

    value = os.environ.get("AGENTBREEDER_CENTER_DIR")
    return private_dir(Path(value).expanduser() if value else Path.home() / ".agentbreeder/center")


def static_files(root: Path = STATIC) -> dict[str, tuple[bytes, str]]:
    """The allow-list of static paths, built once at start from the package tree: regular files
    with a known type, no dotfiles, no symlinks. Anything else is a 404."""
    files: dict[str, tuple[bytes, str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts) or path.is_symlink():
            continue
        kind = STATIC_TYPES.get(path.suffix)
        if kind is None or not path.is_file():
            continue
        files["/" + relative.as_posix()] = (path.read_bytes(), kind)
    files["/"] = files["/index.html"]
    return files


def _hash(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


@dataclass
class Session:
    """One tab's sign-in, in memory only (a restart signs everyone out)."""

    created: float
    last: float
    kind: str  # "link" (the one-time terminal link) or "password"
    version: int  # the credential version it was minted under


def _policy(password: object) -> None:
    try:
        auth.check_policy(password)  # type: ignore[arg-type]
    except auth.PolicyError as problem:
        raise BadRequest(str(problem), "PASSWORD_POLICY") from problem


class SignInRefused(Exception):
    def __init__(self, status: int, code: str, message: str, data: dict | None):
        super().__init__(message)
        self.status, self.code, self.data = status, code, data


class Center:
    def __init__(self, directory: Path, environment: str = "dev", prefix=None):
        self.directory = directory
        self.environment = environment
        self.state = CenterState(directory)
        self.runner = Runner(self.state, directory, prefix)
        self.reads = Reads(run_cli=self.runner.read)
        self.runner.on_invalidate.append(self.reads.invalidate)
        self.static = static_files()
        self.port = 0
        self.token: tuple[bytes, float, bool] | None = None
        self.sessions: dict[bytes, Session] = {}
        self.lock = threading.Lock()
        self.hashing = threading.Lock()  # one password check at a time (S-24)
        self.backoff = auth.Backoff()
        self.backoff_logged = 0  # the failure count whose wait was last recorded
        self.backoff_version = self.state.credential_version()
        self.turns = threading.BoundedSemaphore(MAX_TURNS_IN_FLIGHT)
        self.traces = threading.BoundedSemaphore(MAX_TRACES_IN_FLIGHT)
        self.tool_logs = threading.BoundedSemaphore(MAX_TOOL_LOGS_IN_FLIGHT)
        self.docker_lock = threading.Lock()
        self.docker_seen: tuple[float, dict] | None = None
        self.knowledge_reads = threading.BoundedSemaphore(MAX_KNOWLEDGE_IN_FLIGHT)
        self.cost_reads = threading.BoundedSemaphore(MAX_COST_IN_FLIGHT)
        self.usage = usage_feeds.Usage(self.runner)  # one `services usage --follow` per project
        self.log_follows = logfollow.LogFollows(self.runner)  # M5: a tab's `logs --follow`
        self.watch = watch_feeds.Watch(self.runner)  # one `watch --record` per project (#821)
        # The last verified repin review per project (#964): only its digest, in memory, under
        # `self.lock`. Consumed by the start that uses it; a restart loses it.
        self.pins_reviews: dict[str, str] = {}

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def docker(self, fresh: bool = False) -> dict:
        """Whether Docker is reachable, for the page blocker (console §8.5, M6 #822):
        `doctor --json --only docker`, the engine check alone, so a hung engine is reported
        after that check's 10 s timeout (the full doctor would run its compose and PATH checks
        after it). Read with exit 1 allowed (doctor's answer when the check fails, which is the
        answer wanted here), never from the cache, so a stopped engine shows and a started one
        clears, and tracked, so shutdown stops a check in flight. One check runs at a time and
        its answer serves every tab for DOCKER_REUSE seconds after the check started. `fresh`
        (Retry) takes only an answer from a check that STARTED after the request arrived (one
        already running may have seen Docker before it came back), so a burst of Retries costs
        the check in flight plus one more, not one each."""
        arrived = time.monotonic()
        with self.docker_lock:
            seen = self.docker_seen  # (when the check started, its answer)
            if seen and (
                seen[0] >= arrived if fresh else time.monotonic() - seen[0] < DOCKER_REUSE
            ):
                return seen[1]
            started = time.monotonic()
            answer = self.runner.read(
                ["doctor", "--json", "--only", "docker"],
                timeout=DOCKER_TIMEOUT,
                ok_codes=(0, 1),
                cache=False,
                track=True,
            )
            result = docker_state(answer)
            self.docker_seen = (started, result)
            return result

    # -- login -----------------------------------------------------------------------------------

    def mint_login(self) -> tuple[str, Path]:
        """A new one-time link. The browser opens a private file that redirects, so the token
        is never in a process argument list, which every local user can read."""
        token = secrets.token_urlsafe(32)
        url = f"{self.origin}/#t={token}"
        with self.lock:
            self.token = (_hash(token), time.time() + TOKEN_TTL, False)
        page = self.directory / "login.html"
        write_private(
            page,
            f'<!doctype html><meta http-equiv="refresh" content="0;url={url}">'.encode(),
        )
        return url, page

    def redeem(self, token: str) -> dict | None:
        """A one-time terminal link: a new session of kind "link". Not subject to the password
        backoff (S-14: the terminal link always works)."""
        with self.lock:
            if self.token is None or not hmac.compare_digest(self.token[0], _hash(token)):
                return None
            digest, expires, used = self.token
            if used:
                print(
                    "WARNING: a used console login link was presented again.",
                    file=sys.stderr,
                )
                return None
            if time.time() > expires:
                return None
            self.token = (digest, expires, True)
        self.state.record_event("link", "ok", "terminal-link")
        return self._new_session("link")

    def _new_session(self, kind: str, version: int | None = None) -> dict:
        """A new token (never reused, S-24), recorded under `version`: for a password sign-in the
        credential version read before the check, so a reset that commits during the check
        leaves a session that is already ended."""
        session = secrets.token_urlsafe(32)
        if version is None:
            version = self.state.credential_version()
        now = time.time()
        with self.lock:
            self.sessions[_hash(session)] = Session(now, now, kind, version)
        return {
            "session": session,
            "kind": kind,
            "setup": not self.state.has_password(),
            "email": auth.EMAIL,
        }

    def session(self, value: str | None, refresh: bool = True) -> Session | None:
        """The live session for a token, or None. `refresh=False` for polls (guided §14.9 R7): a
        tab left polling a job log does not keep an idle session alive. The credential version
        and the session length are re-read from center.sqlite3 on every call, so a password
        reset in another process (`up --reset-password`) or "sign out everywhere" ends every
        session at once."""
        if not value:
            return None
        key = _hash(value)
        version = self.state.credential_version()
        limit = auth.session_hours(self.state.setting("session_hours")) * 3600
        now = time.time()
        with self.lock:
            entry = self.sessions.get(key)
            if entry is None:
                return None
            if (
                entry.version != version
                or now - entry.created > limit
                or now - entry.last > SESSION_IDLE
            ):
                del self.sessions[key]
                return None
            if refresh:
                entry.last = now
            return entry

    def session_valid(self, value: str | None, refresh: bool = True) -> bool:
        return self.session(value, refresh) is not None

    def end_session(self, value: str | None) -> None:
        if value:
            with self.lock:
                self.sessions.pop(_hash(value), None)

    def _checked(self, check: Callable[[], bool], event: str) -> bool:
        """Run one password check under the console-wide backoff (S-24): the wait is checked
        before any hashing, one check runs at a time (single-flight), and the outcome is recorded
        (S-30). Raises SignInRefused for the wait or a check already running."""
        if not self.hashing.acquire(blocking=False):
            raise SignInRefused(
                429, "SIGN_IN_BUSY", "Another sign-in is being checked; try again", None
            )
        try:
            # A new password (a reset from the terminal, a change) starts a fresh count: the
            # wait guarded the old one.
            version = self.state.credential_version()
            if version != self.backoff_version:
                self.backoff_version = version
                self.backoff.succeeded()
                self.backoff_logged = 0
            wait = self.backoff.wait()
            if wait:
                # One row per wait, not per refused request: a flood must not push the other
                # events out of the kept 1000 (S-30).
                if self.backoff_logged != self.backoff.failures:
                    self.backoff_logged = self.backoff.failures
                    self.state.record_event(event, "backoff", "browser")
                raise SignInRefused(
                    429, "BACKOFF", auth.BACKOFF_TEXT.format(seconds=wait), {"retry_after": wait}
                )
            good = check()
            if good:
                self.backoff.succeeded()
                self.backoff_logged = 0  # a later wait at the same count is recorded too
            else:
                self.backoff.failed()
            self.state.record_event(event, "ok" if good else "failed", "browser")
            return good
        finally:
            self.hashing.release()

    def sign_in(self, email: object, password: object) -> dict:
        """`POST /api/session {email, password}`: a new session of kind "password". A wrong
        email and a wrong password get the same answer after the same scrypt work."""
        if not isinstance(password, str) or not isinstance(email, str):
            raise BadRequest("Send an email and a password")
        if len(password) > auth.MAX_LENGTH or len(email) > 320:
            raise BadRequest("Wrong email or password", "BAD_CREDENTIALS")
        version = self.state.credential_version()
        row = self.state.user(auth.EMAIL) if auth.same_email(email) else None
        if not self._checked(lambda: auth.verify(password, row), "sign-in"):
            # 403, not 401: the page has no session to end, it shows the form's error.
            raise SignInRefused(403, "BAD_CREDENTIALS", "Wrong email or password", None)
        return self._new_session("password", version)

    def set_first_password(self, current: Session, password: object) -> dict:
        """Setup (S-28): only a session minted from the one-time terminal link, and only while
        no password exists. Every session ends (the credential version moves on), so the browser
        signs in with the new password next."""
        if current.kind != "link":
            raise SignInRefused(
                401, "LINK_REQUIRED", "Open the one-time link from the terminal to set up", None
            )
        if self.state.has_password():
            raise SignInRefused(409, "PASSWORD_SET", "A password is already set; sign in", None)
        _policy(password)
        record = auth.hash_password(password)  # type: ignore[arg-type]
        if not self.state.set_password(
            auth.EMAIL, record, "password-set", "browser", only_if_none=True
        ):
            raise SignInRefused(409, "PASSWORD_SET", "A password is already set; sign in", None)
        return {"email": auth.EMAIL}

    def change_password(self, current: object, password: object) -> dict:
        """Settings: needs the current password (S-28), checked under the same backoff as
        sign-in. Every other session ends; this tab gets a new one."""
        if not isinstance(current, str) or not current:
            raise BadRequest("Enter your current password", "CURRENT_REQUIRED")
        if len(current) > auth.MAX_LENGTH:
            raise SignInRefused(403, "WRONG_PASSWORD", "The current password is wrong", None)
        row = self.state.user(auth.EMAIL)
        if row is None:
            raise SignInRefused(409, "NO_PASSWORD", "No password is set yet", None)
        _policy(password)  # cheap; the new hash is made only after the current one checks out
        if not self._checked(lambda: auth.verify(current, row), "password-change-check"):
            raise SignInRefused(403, "WRONG_PASSWORD", "The current password is wrong", None)
        record = auth.hash_password(password)  # type: ignore[arg-type]
        self.state.set_password(auth.EMAIL, record, "password-change", "browser")
        return self._new_session("password")

    def settings(self) -> dict:
        return {
            "email": auth.EMAIL,
            "password_set": self.state.has_password(),
            "session_hours": auth.session_hours(self.state.setting("session_hours")),
            "session_choices": list(auth.SESSION_HOURS),
            "idle_minutes": SESSION_IDLE // 60,
            "events": self.state.security_events(),
        }

    def save_settings(self, body: dict) -> dict:
        hours = body.get("session_hours")
        if type(hours) is not int or hours not in auth.SESSION_HOURS:  # not 1.0, not True
            raise BadRequest("Choose a session length the console offers", "SESSION_HOURS")
        self.state.set_setting("session_hours", str(hours))
        self.state.record_event("session-length", "ok", "browser")
        return self.settings()

    # -- projects --------------------------------------------------------------------------------

    def register(self, path: Path) -> dict:
        try:
            path = path.expanduser().resolve()
        except (ValueError, OSError, RuntimeError) as problem:
            raise BadRequest("Invalid path") from problem
        check_project(path)
        identifier = hashlib.sha256(str(path).encode()).hexdigest()[:12]
        project = self.state.add_project(identifier, path.name, str(path), self.environment)
        if project["environment"] != self.environment:
            raise BadRequest(f"That project is registered for {project['environment']}")
        return project

    @staticmethod
    def projects_folder() -> Path:
        """`~/agentbreeder-projects`, checked (S-01) before anything is created, then made."""
        folder = Path.home() / "agentbreeder-projects"
        if folder.is_symlink() and not folder.exists():
            raise BadRequest(
                f"{folder} is a broken link; remove it or point it at a directory you own",
                "PROJECT_UNSAFE",
            )
        existing = folder
        while not existing.exists():
            existing = existing.parent
        check_ancestors(existing.resolve())
        folder.mkdir(mode=0o755, parents=True, exist_ok=True)
        check_ancestors(folder.resolve())
        return folder

    def onboard_example(self, name: str) -> dict:
        if name not in EXAMPLES:
            raise BadRequest("Unknown example")
        destination = Path.home() / "agentbreeder-projects" / name
        if not (destination / "agent.yaml").exists():
            self.projects_folder()
            self.runner.read_text(["example", name, str(destination)], timeout=60)
        return self.register(destination)

    def create_starter(self, name: object) -> dict:
        """Create starter (#820): `new agent NAME --model gemini` into the fixed folder, then
        registered. Only the name comes from the request; `new agent` refuses a destination that
        is not empty and writes its own coded error, which is shown as it is (guided §14.2)."""
        if not isinstance(name, str) or not STARTER_NAME.fullmatch(name):
            raise BadRequest(
                "A name starts with a lowercase letter, then lowercase letters, digits and "
                "dashes, at most 31 characters, not ending in a dash",
                "NAME_INVALID",
            )
        destination = self.projects_folder() / name
        self._new_agent(
            ["new", "agent", name, "--model", "gemini", "--dir", str(destination), "--json"]
        )
        return self.register(destination)

    def _new_agent(self, argv: list[str]) -> object:
        """Run `new agent ... --json` (a refusal prints its JSON error and exits 2); its coded
        error goes through the same filter as a stderr line: a fixed code shape, no controls."""
        result = self.runner.read(argv, timeout=onboard.TIMEOUT, ok_codes=(0, 2), cache=False)
        refused = result["data"].get("error") if isinstance(result["data"], dict) else None
        if isinstance(refused, dict):
            line = f"[{refused.get('code')}] {refused.get('message')}".replace("\n", " ")
            problem = cli_error(line, 2)
            raise BadRequest(problem["message"], problem["code"])
        if result["error"]:
            raise BadRequest(result["error"]["message"], result["error"]["code"])
        return result["data"]

    def _stack_for(self, body: object, digest: str | None = None) -> tuple[dict, dict]:
        """`new agent --stack-for` as a dry run (the preview); with `digest`, refused unless the
        agent file is still the previewed one. Returns (checked request, checked summary)."""
        name, path, argv = onboard.request(body)
        expect = [] if digest is None else [f"--expect-digest={digest}"]
        data = self._new_agent([*argv, *expect, "--dry-run", "--json"])
        try:
            shown = onboard.summary(data, name, dry_run=True, model=argv[4])
        except onboard.BadOutput as problem:
            raise BadRequest(str(problem), "BAD_OUTPUT") from problem
        return {"name": name, "path": path, "argv": argv, "model": argv[4]}, shown

    def preview_stack_for(self, body: object) -> dict:
        """Onboard an agent.yaml alone, step 1 (#823): what `--stack-for` would write and what
        the project brings that runs in agents. Writes nothing."""
        return self._stack_for(body)[1]

    def onboard_agent_yaml(self, body: object) -> dict:
        """Onboard an agent.yaml alone, step 3: re-check the previewed file and the code confirm
        on a fresh dry run, write `stack.yaml` beside it, then register its directory (S-01
        again, `check_project`)."""
        digest, code_confirmed = onboard.write_request(body)
        checked, shown = self._stack_for(body, digest)
        if shown["runs_code"] and not code_confirmed:
            raise BadRequest(
                "This project brings code that runs in agents; confirm that before it is added",
                "CODE_CONFIRM_REQUIRED",
            )
        data = self._new_agent([*checked["argv"], f"--expect-digest={digest}", "--json"])
        # The directory of the path that was checked, not one named by the child's output.
        directory = Path(checked["path"]).parent
        try:
            onboard.summary(data, checked["name"], dry_run=False, model=checked["model"])
            return self.register(directory)
        except (onboard.BadOutput, BadRequest) as problem:
            # The files are on disk now, so a retry would only meet STACK_EXISTS: say so.
            code = problem.code if isinstance(problem, BadRequest) else "BAD_OUTPUT"
            raise BadRequest(
                f"{problem} stack.yaml was written but the project was not added; fix that, "
                f"then add {directory} under An existing project",
                code,
            ) from problem

    def _from_spec(self, body: object, digest: str | None = None) -> tuple[str, Path, dict]:
        """`new agent --from-spec` as a dry run (the preview, #833), into the fixed projects
        folder; with `digest`, refused unless the plan is still the previewed one. Returns
        (name, destination, checked summary)."""
        name, spec = builder.request(body)
        if spec.get("runtime") is not None:  # a listed, usable ref, before any write (#1085)
            builder.check_runtime(spec, self._runtimes())
        destination = self.projects_folder() / name
        expect = [] if digest is None else [f"--expect-digest={digest}"]
        with builder.spec_file(spec) as path:
            data = self._new_agent(
                ["new", "agent", name, f"--from-spec={path}", f"--dir={destination}", *expect]
                + ["--dry-run", "--json"]
            )
        try:
            shown = builder.summary(
                data,
                name,
                True,
                spec["model"],
                str(destination),
                spec["schema_version"],
                spec.get("runtime"),
            )
        except builder.BadOutput as problem:
            raise BadRequest(str(problem), "BAD_OUTPUT") from problem
        return name, destination, shown

    def _runtimes(self) -> list[dict]:
        """The engine's resolved view of every `runtime/` version (#1085 S1, console spec §8):
        `registry runtimes --json` as a CLI child, so this process holds no profile logic, over
        the roots a new project resolves (AGENTBREEDER_REGISTRY_PATH, which the child inherits,
        and the built-ins; the child runs in the console's own directory, which has no
        registry). Never the last good answer: a spec is checked against what is there now."""
        result = self.runner.read(
            list(builder.RUNTIMES_ARGV),
            timeout=builder.RUNTIMES_TIMEOUT,
            cache=False,
            track=True,  # stopped at shutdown, and its output is capped as it is read
            max_bytes=builder.MAX_RUNTIMES_BYTES,
        )
        if result["error"]:
            raise BadRequest(result["error"]["message"], result["error"]["code"])
        try:
            return builder.runtimes(result["data"])
        except builder.BadOutput as problem:
            raise BadRequest(str(problem), "BAD_OUTPUT") from problem

    def builder_parts(self) -> dict:
        """The builder's pickers (#833): the `tool/` and `mcp/` versions a new project resolves
        (AGENTBREEDER_REGISTRY_PATH, which CLI children inherit, and the built-ins), and the
        Framework group's `runtimes` (#1085 S1). A failed runtimes read fails the whole answer
        with the read's code: the form cannot be previewed without a runtime."""
        try:
            parts = builder.parts(registry.roots(None))
        except (registry.RegistryError, OSError):
            message = "The registries could not be read; run `agentbreeder registry verify`"
            return envelope(None, {"code": "REGISTRY_UNREADABLE", "message": message})
        try:
            parts["runtimes"] = self._runtimes()
        except BadRequest as problem:
            return envelope(None, {"code": problem.code, "message": str(problem)})
        return envelope(parts)

    def preview_builder(self, body: object) -> dict:
        """Build a new agent, step 1 (#833): every file `new agent --from-spec` would write,
        with its text, and whether the combination was run end to end. Writes nothing."""
        return self._from_spec(body)[2]

    def create_builder(self, body: object) -> dict:
        """Build a new agent, step 3: re-check the verified combination and the code confirm on
        a fresh dry run pinned to the previewed plan, write the project, then register it."""
        digest, code_confirmed = builder.write_request(body)
        name, destination, shown = self._from_spec(body, digest)
        if not shown["combination"]["verified"]:
            raise BadRequest(
                "This combination has not been run end to end, so the console does not create "
                "it; `agentbreeder new agent --from-spec` can",
                "COMBINATION_UNVERIFIED",
            )
        if shown["runs_code"] and not code_confirmed:
            raise BadRequest(
                "This agent uses code that runs in agents; confirm that before it is created",
                "CODE_CONFIRM_REQUIRED",
            )
        _, spec = builder.request(body)
        with builder.spec_file(spec) as path:
            data = self._new_agent(
                ["new", "agent", name, f"--from-spec={path}", f"--dir={destination}"]
                + [f"--expect-digest={digest}", "--json"]
            )
        try:
            builder.summary(
                data,
                name,
                False,
                spec["model"],
                str(destination),
                spec["schema_version"],
                spec.get("runtime"),
            )
            return self.register(destination)
        except (builder.BadOutput, BadRequest) as problem:
            # The files are on disk now, so a retry would only meet the destination check.
            code = problem.code if isinstance(problem, BadRequest) else "BAD_OUTPUT"
            raise BadRequest(
                f"{problem} The agent was written but not added; fix that, then add "
                f"{destination} under An existing project",
                code,
            ) from problem

    def need_project(self, identifier: str) -> dict:
        project = self.state.project(identifier)
        if project is None:
            raise BadRequest("Unknown project", "UNKNOWN_PROJECT")
        return project

    # -- reads -----------------------------------------------------------------------------------

    def state_dir(self, project: dict) -> str | None:
        result = self.runner.read_text(
            ["services", "env", project["path"], "--environment", project["environment"]]
        )
        match = re.search(r"AGENTBREEDER_STATE_DIR=(\S+)", result or "")
        return match[1].strip("'\"") if match else None

    def guide(self, project: dict) -> dict:
        """`next --json` with fixed arguments (never `--output` or a path from the request),
        checked against its own models and the registered path before the browser sees it
        (`docs/next.md`, "What the console must not do with it"). On a failed run the last good
        answer comes back marked stale."""
        path = project["path"]
        result = self.runner.read(
            ["next", path, "--environment", project["environment"], "--json"],
            timeout=GUIDE_TIMEOUT,
        )
        if result["data"] is None:
            return result
        try:
            checked = guide.Guide.model_validate(result["data"])
        except (ValidationError, ValueError, TypeError):
            checked = None
        if checked is None or checked.project != path:
            return envelope(
                None,
                error(
                    "GUIDE_INVALID",
                    "The guide's answer does not match its contract; run "
                    "`agentbreeder next` in a terminal",
                ),
            )
        return {**result, "data": checked.model_dump(mode="json")}

    def read_view(
        self, project: dict, view: str, query: dict[str, list[str]] | None = None
    ) -> dict:
        """The envelope for one view: a single CLI read is its own envelope; a view made of
        several reads carries each read's envelope in `data`. Only `audit` (`before`, a seq: the
        page of rows older than it) and `evals` (`agent`, an agent name) read the query."""
        path, environment = project["path"], project["environment"]
        read = self.runner.read
        if view == "services":
            return read(["services", "status", path, "--json"])
        if view == "guide":
            return self.guide(project)
        if view == "registry":  # in process: the same functions as `registry list/verify`
            return envelope(
                {
                    "versions": self.reads.registry_versions(path),
                    "problems": self.reads.registry_problems(path),
                    "roots": self.reads.roots(path),
                    "in_use": self.reads.in_use(path),
                }
            )
        if view == "agent":
            return self.reads.agent(path)
        if view == "lock":
            return self.reads.lock(path)
        if view == "deployments":
            return read(["list", "--stack", path, "--environment", environment, "--json"])
        if view == "cluster":
            return read(["cluster", "status", "--stack", path, "--json"])
        if view == "audit":
            state = self.state_dir(project)
            if not state:
                return envelope(None, error("STACK_NOT_UP", "Start the services first"))
            before = (query or {}).get("before", [""])[0]
            if before and not AUDIT_BEFORE.fullmatch(before):
                raise BadRequest(
                    "Before must be a row's seq (a positive number)", "BEFORE_INVALID"
                )
            argv = ["audit", "list", "--state-dir", state, "--limit", str(AUDIT_PAGE + 1)]
            rows = read([*argv, *(["--before", before] if before else []), "--json"])
            older = None
            if isinstance(rows["data"], list) and len(rows["data"]) > AUDIT_PAGE:
                rows = {**rows, "data": rows["data"][:AUDIT_PAGE]}
                last = rows["data"][-1]
                older = last.get("seq") if isinstance(last, dict) else None
            return envelope(
                {
                    "rows": rows,
                    "before": int(before) if before else None,  # the page this answers
                    "older": older if type(older) is int else None,
                    "verify": read(["audit", "verify", "--state-dir", state, "--json"]),
                    # Production endpoints, and the agent of a `release` row (by its image).
                    "deployments": read(
                        ["list", "--stack", path, "--environment", environment, "--json"]
                    ),
                }
            )
        if view == "mcp":  # MCP servers, pins and grants from stack.yaml and stack.lock (#831)
            return self.reads.stack_mcp(path)
        if view == "identity":
            return self.reads.stack_agents(path)
        if view == "evals":  # the eval reports `release` saved in the project's deploy/ (#932)
            return self.eval_reports(project, None, query or {})
        if view == "releases":  # the project's release history, from its audit chain (#823)
            state = self.state_dir(project)
            if not state:
                return envelope(None, error("STACK_NOT_UP", "Start the services first"))
            rows = read(
                ["audit", "list", "--state-dir", state, "--limit", str(RELEASE_ROWS), "--json"]
            )
            if rows["data"] is None:
                return rows
            agent = self.reads.agent(path)["data"]
            return envelope(
                {
                    # Release rows name no agent: they are this project's agent.yaml's runs.
                    "agent": agent.get("name") if isinstance(agent, dict) else None,
                    "releases": agent_rows.releases(rows["data"]),
                    # Only the newest RELEASE_ROWS audit rows are read (the CLI's maximum).
                    "truncated": isinstance(rows["data"], list)
                    and len(rows["data"]) >= RELEASE_ROWS,
                },
                rows["error"],
                rows["stale"],
            )
        raise BadRequest("Unknown view", "UNKNOWN_VIEW")

    def agents(self) -> dict:
        """Every deployment of every stack (`list --all --json`, read-only), each marked with the
        registered project it belongs to (#823)."""
        result = self.runner.read(["list", "--all", "--json"])
        if result["data"] is None:
            return result
        try:
            linked = agent_rows.link_projects(result["data"], self.state.projects())
        except agent_rows.BadListing as problem:
            return envelope(None, error("BAD_OUTPUT", str(problem)))
        return envelope(linked, result["error"], result["stale"])

    def logs(self, project: dict, source: str, name: str, query: dict[str, list[str]]) -> dict:
        """One snapshot of a service's or a deployment's logs (#821): `services logs DIR NAME`
        or `logs ID --stack DIR`, with `--json` and fixed flags; only the name, the line count,
        `--since` and (for a deployment) `--previous` come from the request, each checked
        first. Never cached: a log read is always fresh, and a failed read shows its own coded
        error, not older lines. The CLI redacts every line (failing closed) and escapes
        controls; the answer is checked against its shape and passed on, never stored."""
        tail_text = query.get("tail", [str(LOG_TAIL)])[0]
        since = query.get("since", [""])[0]
        previous = query.get("previous", [""])[0] == "1"
        argv = _log_argv(project, source, name, tail_text, since, previous)
        result = self.runner.read(argv, timeout=LOG_TIMEOUT, cache=False, track=True)
        if result["data"] is None:
            return result
        checked = _log_answer(result["data"], source, name)
        if checked is None:
            return envelope(
                None,
                error(
                    "LOGS_INVALID",
                    "The log answer does not match its contract; run the command in a terminal",
                ),
            )
        return envelope(checked)

    def follow_logs(
        self, project: dict, source: str, name: str, body: dict, session: bytes
    ) -> dict:
        """Start following a service's or a deployment's logs for this session (#821, M5 PR 2):
        the snapshot read's argv with `--follow` (no `--previous`: a stopped container has no
        more lines). Only `tail` and `since` come from the request, checked as for a read; the
        lines then come from `GET /api/follows/ID` (`logfollow`)."""
        tail = body.get("tail", LOG_TAIL)
        since = body.get("since", "")
        if type(tail) is not int or not isinstance(since, str):
            raise BadRequest("Tail must be a number and since a string", "BAD_REQUEST")
        argv = _log_argv(project, source, name, str(tail), since, False)
        return self.log_follows.start(session, source, name, [*argv, "--follow"])

    def failed_tail(self, project: dict, agent: str) -> dict:
        """The log tail `release` saved from the agent's last failed candidate (#826, the
        gate-failed view): `logs --failed NAME --stack DIR --environment ENV --json`, fixed argv,
        never cached. The CLI read the file `release` wrote (0600, already redacted) and escapes
        nothing in JSON; the browser shows every line through Text. Exit 1 (no tail: none failed
        yet, or a later release passed) is `{saved: false}`. The file's path is not passed on:
        it names the state directory, which holds the stack's secrets."""
        if not eval_reads.AGENT.fullmatch(agent):
            raise BadRequest("The agent must be an agent name", "AGENT_INVALID")
        argv = ["logs", "--failed", agent, "--stack", project["path"]]
        argv += ["--environment", project["environment"], "--json"]
        result = self.runner.read(
            argv, timeout=LOG_TIMEOUT, cache=False, track=True, empty_codes=(1,)
        )
        if result["error"] is not None:
            return result
        if result["data"] is None:
            return envelope({"agent": agent, "saved": False, "lines": []})
        data = result["data"]
        lines = data.get("lines") if isinstance(data, dict) else None
        if (
            not isinstance(data, dict)
            or data.get("agent") != agent
            or not isinstance(lines, list)
            or not all(isinstance(line, str) for line in lines)
        ):
            return envelope(
                None,
                error(
                    "LOGS_INVALID",
                    "The log answer does not match its contract; run the command in a terminal",
                ),
            )
        return envelope({"agent": agent, "saved": True, "lines": lines})

    def eval_reports(self, project: dict, run: str | None, query: dict[str, list[str]]) -> dict:
        """The saved eval reports (`evals list`), or one run's report (`evals show RUN`), from the
        project's default `deploy/` folder (#932): fixed argv from `center/evals.py`, redacted
        with the stack's state, never cached, the answer checked against its shape."""
        try:
            state = self.state_dir(project)
            if not state:
                return envelope(None, error("STACK_NOT_UP", "Start the services first"))
            argv = (
                eval_reads.list_argv(project, state, query)
                if run is None
                else eval_reads.show_argv(project, state, run, query)
            )
        except eval_reads.Invalid as problem:
            raise BadRequest(str(problem), problem.code) from None
        result = self.runner.read(argv, timeout=eval_reads.TIMEOUT, cache=False, track=True)
        if result["data"] is None:
            return result
        ok = (
            eval_reads.list_answer(result["data"])
            if run is None
            else eval_reads.show_answer(result["data"], run)
        )
        if not ok:
            return envelope(
                None,
                error(
                    "EVALS_INVALID",
                    "The eval report answer does not match its contract; run the command in a "
                    "terminal",
                ),
            )
        return envelope(result["data"])

    def try_turn(self, project: dict, name: str, body: dict) -> dict:
        """One Try it turn (#823): `invoke NAME - --json` with the message on stdin (S-12a), as
        an uncached, tracked read with no stale answer and `invoke`'s own deadline. Nothing is
        stored: no job and no job lines. The answer is checked by `tryit.turn_answer`."""
        request = tryit.turn_request(name, body)
        if not self.turns.acquire(blocking=False):
            raise BadRequest(
                f"{MAX_TURNS_IN_FLIGHT} Try it messages are already waiting for a reply; try "
                "again when one ends",
                "TRY_BUSY",
            )
        try:
            started = time.monotonic()
            result = self.runner.read(
                tryit.turn_argv(project, name, request),
                timeout=tryit.TIMEOUT,
                ok_codes=(0, 1, 2),  # invoke prints its JSON error object and exits 1 or 2
                cache=False,
                track=True,
                input=request["message"],
            )
            latency = round((time.monotonic() - started) * 1000)
        finally:
            self.turns.release()
        if result["data"] is None:  # no JSON: a timeout, a crash, a usage error, no child
            problem = result["error"]
            return envelope({"sent": tryit.maybe_sent(problem), "latency_ms": latency}, problem)
        turn, problem = tryit.turn_answer(result["data"], latency)
        return envelope(turn, problem)

    def turn_trace(self, project: dict, trace_id: str) -> dict:
        """A Try it turn's trace (#907): `traces --trace ID ... --json` as an uncached, tracked
        read (Phoenix may not have the trace yet, so an answer is never kept). Its answer is
        checked by `tryit.trace_answer`; no message content is in it."""
        argv = tryit.trace_argv(project, trace_id)
        if not self.traces.acquire(blocking=False):
            raise BadRequest(
                f"{MAX_TRACES_IN_FLIGHT} trace reads are already running; try again when one ends",
                "TRACE_BUSY",
            )
        try:
            result = self.runner.read(
                argv,
                timeout=tryit.TRACE_TIMEOUT,
                ok_codes=(0, 2),  # traces prints its JSON error object and exits 2
                cache=False,
                track=True,
            )
        finally:
            self.traces.release()
        if result["data"] is None:
            return result
        trace, problem = tryit.trace_answer(result["data"], trace_id)
        return envelope(trace, problem)

    def deployment_traces(
        self, project: dict, deployment_id: str, query: dict[str, list[str]]
    ) -> dict:
        """A deployment's recent traces, for an agent's Traces tab (#907): `traces ID ... --last N
        --json` as an uncached, tracked read sharing the trace-read bound. Its answer is checked
        by `traces.list_answer`: a summary per trace, no spans and no message content."""
        last = trace_lists.list_request(query)
        argv = trace_lists.list_argv(project, deployment_id, last)
        if not self.traces.acquire(blocking=False):
            raise BadRequest(
                f"{MAX_TRACES_IN_FLIGHT} trace reads are already running; try again when one ends",
                "TRACE_BUSY",
            )
        try:
            result = self.runner.read(
                argv,
                timeout=trace_lists.TIMEOUT,
                ok_codes=(0, 2),  # traces prints its JSON error object and exits 2
                cache=False,
                track=True,
            )
        finally:
            self.traces.release()
        if result["data"] is None:
            return result
        answer, problem = trace_lists.list_answer(result["data"], deployment_id)
        return envelope(answer, problem)

    def tool_log(self, project: dict, query: dict[str, list[str]]) -> dict:
        """A tool's execution log (#828): `tool log REF ... --json` as an uncached, tracked read
        (a log is always fresh, and a failed read shows its own coded error, never older rows).
        Arguments are read only with `content=1` and are never cached, stored or logged. The
        answer is checked by `toollog.log_answer`."""
        request = toollog.log_request(query)
        if not self.tool_logs.acquire(blocking=False):
            raise BadRequest(
                f"{MAX_TOOL_LOGS_IN_FLIGHT} tool log reads are already running; try again when "
                "one ends",
                "TOOL_LOG_BUSY",
            )
        try:
            result = self.runner.read(
                toollog.log_argv(project, request),
                timeout=toollog.TIMEOUT,
                ok_codes=(0, 2),  # `tool log` prints its JSON error object and exits 2
                cache=False,
                track=True,
            )
        finally:
            self.tool_logs.release()
        if result["data"] is None:
            return result
        log, problem = toollog.log_answer(result["data"], request)
        return envelope(log, problem)

    def knowledge_status(self, project: dict, query: dict[str, list[str]]) -> dict:
        """A knowledge version's index status (#830): `knowledge status REF ... --json` as an
        uncached, tracked read (an index status is always fresh, and a failed read shows its own
        coded error, never an older "fresh"). The answer is checked by
        `knowledgestatus.status_answer`."""
        request = knowledgestatus.status_request(query)
        if not self.knowledge_reads.acquire(blocking=False):
            raise BadRequest(
                f"{MAX_KNOWLEDGE_IN_FLIGHT} knowledge status reads are already running; try "
                "again when one ends",
                "KNOWLEDGE_STATUS_BUSY",
            )
        try:
            result = self.runner.read(
                knowledgestatus.status_argv(project, request),
                timeout=knowledgestatus.TIMEOUT,
                ok_codes=(0, 2),  # `knowledge status` prints its JSON error object and exits 2
                cache=False,
                track=True,
            )
        finally:
            self.knowledge_reads.release()
        if result["data"] is None:
            return result
        status, problem = knowledgestatus.status_answer(result["data"], request)
        return envelope(status, problem)

    def llm_cost(self, project: dict, query: dict[str, list[str]]) -> dict:
        """The project's LLM cost (#906): `cost --stack DIR ... --json` as an uncached, tracked
        read (spend is always read fresh, and a failed read shows its own coded error, never older
        numbers). The answer is checked by `cost.cost_answer`, which adds when it was read."""
        request = cost_reads.cost_request(query)
        if not self.cost_reads.acquire(blocking=False):
            raise BadRequest(
                f"{MAX_COST_IN_FLIGHT} cost reads are already running; try again when one ends",
                "COST_BUSY",
            )
        try:
            result = self.runner.read(
                cost_reads.cost_argv(project, request),
                timeout=cost_reads.TIMEOUT,
                ok_codes=(0, 2),  # `cost` prints its JSON error object and exits 2
                cache=False,
                track=True,
            )
        finally:
            self.cost_reads.release()
        if result["data"] is None:
            return result
        cost, problem = cost_reads.cost_answer(result["data"], request)
        return envelope(cost, problem)

    def plan(self, project: dict) -> dict:
        result = self.runner.read(
            [
                "services",
                "plan",
                project["path"],
                "--environment",
                project["environment"],
                "--json",
            ],
            timeout=120,
        )
        problem = result.get("error") or {}
        if problem.get("code") == "SERVICES_BUSY":
            # `plan` is a services write (it records ports and copies): it fails fast while
            # another services write runs on the stack (S-11). No cached grant is shown then:
            # it could not be started until that write ends.
            busy = error("SERVICES_BUSY", SERVICES_BUSY_TEXT)
            result = {"data": None, "error": busy, "stale": False}
        return {**result, "accepted_text": project.get("accepted_grant")}

    def recheck(self, project: dict) -> None:
        """S-01, re-checked before every job and write: the path must still resolve to itself
        (no component swapped for a link since registration) and pass the ownership checks."""
        stored = Path(project["path"])
        try:
            moved = stored.resolve(strict=True) != stored
        except (OSError, RuntimeError):
            moved = True
        if moved:
            raise BadRequest(
                f"{stored} was moved or replaced by a link; add the project again",
                "PROJECT_UNSAFE",
            )
        check_project(stored)

    def registry_edit(self, project: dict, body: object) -> dict:
        """The prompt editor's review and save (#826): `registry edit` with the text on stdin
        (or `--remove PATH`, knowledge documents, #830, with nothing on stdin).
        Without `digest` a dry run (diff and digest, nothing written, uncached); with it the
        write, bound to the reviewed bytes, as a short write locked per name."""
        request = edits.edit_request(body)
        self.recheck(project)
        argv = edits.edit_argv(project, request)
        if request["digest"] is None:
            return self.runner.read(argv, timeout=60, cache=False, input=request["text"])
        ref = request["ref"]
        target = f"{project['id']}:{ref.kind}/{ref.name}"
        return self.runner.short_write(edits.EDIT_KIND, target, argv, input=request["text"])

    def registry_fork(self, project: dict, body: object) -> dict:
        """Fork (#828 "Fork to edit"; #825, M9a: any kind but a package, `--as NAME`):
        `registry fork REF [--as NAME] [--version X.Y.Z]`. Without `digest` a dry run (the
        copy's files and digest, nothing written, uncached). With it, a fresh dry run first, as
        an import: a code-bearing copy (tool, service, eval, mcp, runtime) needs
        `code_confirmed` (three-tier confirm); then the copy, bound to the reviewed digest, as a
        short write locked per name like an edit. Both answers are checked as an import's (the
        same object, docs/registry.md)."""
        request = edits.fork_request(body)
        self.recheck(project)
        review = self._fork_child(project, request, dry_run=True)
        if request["digest"] is None or review["data"] is None:
            return review
        data = review["data"]
        if data["sha256"] != request["digest"]:
            message = "The version changed after your review; review it again"
            return {"data": None, "error": error("DIGEST_MISMATCH", message), "stale": False}
        if data["runs_code"] and not request["code_confirmed"]:
            raise BadRequest(
                "Confirm that you reviewed this code: it runs in agents", "CODE_NOT_CONFIRMED"
            )
        return self._fork_child(project, request, dry_run=False, target=data["ref"])

    def _fork_child(
        self, project: dict, request: dict, *, dry_run: bool, target: str | None = None
    ) -> dict:
        argv = edits.fork_argv(project, request, dry_run=dry_run)
        if dry_run:
            result = self.runner.read(argv, timeout=60, cache=False)
        else:
            name = str(target).split("@", 1)[0]
            result = self.runner.short_write(edits.EDIT_KIND, f"{project['id']}:{name}", argv)
        return self._checked_copy(result, dry_run, "fork")

    def registry_import(self, project: dict, body: object) -> dict:
        """Import by path (#825, M9a): `registry import --json -- PATH`. Without `digest` a dry
        run (the staged copy's files and digest, nothing written, uncached). With it, a fresh
        dry run first: the kind comes only from the CLI, so a code-bearing version (tool,
        service, eval, mcp, runtime) is refused here without `code_confirmed` (three-tier
        confirm, `registryimport`); then the write, bound to the reviewed digest, as a short
        write locked per name like an edit or a fork. Both answers are checked by
        `registryimport.import_answer`."""
        request = registryimport.import_request(body)
        self.recheck(project)
        review = self._import_child(project, request, dry_run=True)
        if request["digest"] is None or review["data"] is None:
            return review
        data = review["data"]
        if data["sha256"] != request["digest"]:
            # The CLI would refuse the write the same way; say so before taking the lock.
            message = "The version changed after your review; review it again"
            return {"data": None, "error": error("DIGEST_MISMATCH", message), "stale": False}
        if data["runs_code"] and not request["code_confirmed"]:
            raise BadRequest(
                "Confirm that you reviewed this code: it runs in agents", "CODE_NOT_CONFIRMED"
            )
        return self._import_child(project, request, dry_run=False, target=data["ref"])

    def _import_child(
        self, project: dict, request: dict, *, dry_run: bool, target: str | None = None
    ) -> dict:
        argv = registryimport.import_argv(project, request, dry_run=dry_run)
        if dry_run:
            result = self.runner.read(argv, timeout=registryimport.TIMEOUT, cache=False)
        else:
            name = str(target).split("@", 1)[0]
            result = self.runner.short_write(edits.EDIT_KIND, f"{project['id']}:{name}", argv)
        return self._checked_copy(result, dry_run, "import")

    @staticmethod
    def _checked_copy(result: dict, dry_run: bool, command: str) -> dict:
        """A `registry import` or `registry fork` answer through `registryimport.import_answer`
        (the same object); one off the contract is BAD_OUTPUT."""
        if result["data"] is None:
            return result
        try:
            data = registryimport.import_answer(result["data"], dry_run=dry_run)
        except registryimport.BadOutput as problem:
            message = f"The {command} answer does not match its contract ({problem}); "
            message += (
                f"run `agentbreeder registry {command}` in a terminal"
                if dry_run
                else "the version may have been written: check the registry before you retry"
            )
            return {"data": None, "error": error("BAD_OUTPUT", message), "stale": False}
        return {**result, "data": data}

    def set_ref(self, project: dict, body: object) -> dict:
        """ "Move agent to this version" (#826): `agent set-ref`, a dry run (the diff and the
        digest of the agent.yaml it read), then the write bound to that digest. The write also
        holds the project, so it never lands while a build or release reads agent.yaml."""
        request = edits.set_ref_request(body)
        self.recheck(project)
        argv = edits.set_ref_argv(project, request)
        if request["base"] is None:
            return self.runner.read(argv, timeout=60, cache=False)
        return self.runner.short_write(edits.SET_REF_KIND, project["id"], argv, project=project)

    def mcp_grant(self, project: dict, body: object) -> dict:
        """Grant or revoke (#831): `mcp grant|revoke --json`. Without `base` a dry run (the diff
        and the digest of stack.yaml, nothing written, uncached); with it the write, bound to the
        reviewed file, as a short write that also holds the project (no job reads a half-changed
        stack). The answer is checked by `mcpgrants.grant_answer`."""
        request = mcpgrants.grant_request(body)
        self.recheck(project)
        argv = mcpgrants.grant_argv(project, request)
        if request["base"] is None:
            result = self.runner.read(argv, timeout=60, cache=False)
        else:
            result = self.runner.short_write(mcpgrants.KIND, project["id"], argv, project=project)
        if result["data"] is None:
            return result
        summary, problem = mcpgrants.grant_answer(result["data"], request)
        return envelope(summary, problem)

    def mcp_pins(self, project: dict, body: object) -> dict:
        """The repin review (#964): `services pins DIR --json`, uncached and tracked (a timeout
        or shutdown stops the whole group, including the `docker compose exec` that holds the
        CLI's write lock), its stdout capped. The answer is checked and rendered by
        `pinsreview.verify`; only then is its digest kept, replacing any earlier one. A failed
        read keeps none: the earlier review is gone too."""
        if body not in (None, {}):
            raise BadRequest("Send an empty JSON object", "PINS_REVIEW_INVALID")
        self.recheck(project)
        with self.lock:
            self.pins_reviews.pop(project["id"], None)
        files = self.reads.stack_mcp(project["path"])
        if files.get("data") is None:
            return files
        relays = len(files["data"].get("servers") or [])
        result = self.runner.read(
            pinsreview.pins_argv(project),
            timeout=pinsreview.timeout(relays),
            cache=False,
            track=True,
            max_bytes=pinsreview.MAX_BYTES,
            loads=pinsreview.loads,
        )
        problem = result.get("error") or {}
        if problem.get("code") == "SERVICES_BUSY":
            return envelope(None, error("SERVICES_BUSY", SERVICES_BUSY_TEXT))
        if problem.get("code") == "OUTPUT_TOO_LARGE":
            message = (
                "The review is larger than the console shows (2 MiB); review it in a terminal: "
                + pinsreview.terminal_command(project)
            )
            return envelope(None, error("PINS_REVIEW_TOO_LARGE", message))
        if result["data"] is None or result.get("stale"):
            return envelope(None, result.get("error") or pinsreview.INVALID)
        summary, invalid = pinsreview.verify(result["data"])
        if summary is None:
            return envelope(None, invalid)
        with self.lock:
            self.pins_reviews[project["id"]] = summary["digest"]
        return envelope(summary)

    def _take_pins_review(self, project: dict, digest: object) -> str:
        """The start's half of the repin (#964): pop the stored review and compare, as one step,
        so two starts can't both use it and a page can't post a digest the server never showed."""
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise BadRequest("Review the repin first", "PINS_EXPECT_INVALID")
        with self.lock:
            stored = self.pins_reviews.pop(project["id"], None)
        if stored != digest:
            raise BadRequest(pinsreview.STALE, "PINS_REVIEW_STALE")
        return stored

    def _restore_pins_review(self, project: dict, digest: str) -> None:
        """A refusal before the job launched: put the review back, unless a newer one is there."""
        with self.lock:
            self.pins_reviews.setdefault(project["id"], digest)

    def mcp_tools(self, project: dict, ref: registry.Ref) -> dict:
        """The tools an `mcp/` version declares (#831), from `registry show --json` (cached as
        the registry browser's reads are), parsed by `mcpgrants.declared_tools`."""
        if ref.kind != "mcp" or ref.version is None:
            raise BadRequest("ref must be mcp/NAME@X.Y.Z", "REF_INVALID")
        shown = self.reads.show(project["path"], ref)
        if shown["data"] is None:
            return shown
        rows, problem = mcpgrants.declared_tools(shown["data"])
        return envelope(rows, problem, shown["stale"])

    def service_action(self, project: dict, name: str, action: str, body: object) -> dict:
        """Start, stop or restart one stack service (#822): `services ACTION DIR NAME --json`
        as a short write per service that also holds the project, so it never overlaps a
        console job on the stack. The CLI's per-stack lock stays authoritative (SERVICES_BUSY,
        said plainly). `stop` and `restart` refuse with SERVICE_IN_USE, naming the deployments,
        until the request is sent again with `{"yes": true}`; every other refusal (PORT_BUSY,
        SERVICE_NOT_CREATED, DOCKER_FAILED) is the CLI's coded message, never Docker's text."""
        if action not in SERVICE_ACTIONS:
            raise BadRequest("Unknown service action")
        if not isinstance(body, dict) or not set(body) <= {"yes"}:
            raise BadRequest("Invalid request")
        yes = body.get("yes", False)
        if not isinstance(yes, bool) or (yes and action == "start"):
            raise BadRequest("Invalid request")
        self.recheck(project)
        argv = ["services", action, project["path"], name, "--json"]
        if yes:
            argv.append("--yes")
        result = self.runner.short_write(
            SERVICE_CONTROL, f"{project['id']}:{name}", argv, project=project
        )
        problem = result.get("error")
        if problem and problem.get("code") == "SERVICES_BUSY":
            return envelope(None, error("SERVICES_BUSY", SERVICES_BUSY_TEXT))
        if result["data"] is None:
            return result
        return envelope(*_service_answer(result["data"], name, action))

    def start_job(self, project: dict, body: dict) -> str:
        self.recheck(project)
        kind = str(body.get("kind", ""))
        options = body.get("options") or {}
        if isinstance(options, dict):
            options = {k: v for k, v in options.items() if k in OPTIONS.get(kind, ())}
        keys = body.get("keys") or None
        if not isinstance(options, dict) or (keys is not None and not isinstance(keys, dict)):
            raise BadRequest("Invalid job options")
        if keys is not None and kind != "services-up":
            raise BadRequest("Keys are only accepted when starting services")
        if keys is not None:
            keys = {str(k): str(v) for k, v in keys.items() if v}
            if len(keys) > 16 or not all(KEY_NAME.fullmatch(name) for name in keys):
                raise BadRequest("Invalid key name")
        pins = None
        if kind == "services-up" and "pins_digest" in options:
            pins = self._take_pins_review(project, options["pins_digest"])
        try:
            return self._start_checked(project, kind, options, keys)
        except BaseException:
            if pins is not None:
                self._restore_pins_review(project, pins)
            raise

    def _start_checked(self, project: dict, kind: str, options: dict, keys) -> str:
        """`start_job` after the repin review was taken: the pre-launch checks, then the job."""
        on_success = None
        if kind == "services-up":
            preview = self.plan(project)
            if (preview.get("error") or {}).get("code") == "SERVICES_BUSY":
                raise BadRequest(SERVICES_BUSY_TEXT, "SERVICES_BUSY")
            if preview.get("error"):  # never start from a cached (stale) grant
                raise BadRequest("Could not read the grant; review it again", "GRANT_UNAVAILABLE")
            data = preview.get("data") or {}
            if data.get("digest") != options.get("digest"):
                raise BadRequest("The grant changed; review it again", "GRANT_CHANGED")
            # MCP servers that are not built in need the person's consent (#831): checked
            # against this fresh plan, then passed as `--allow-project-mcp NAME`.
            options["allow_project_mcp"] = mcpgrants.consent_names(
                options.get("allow_project_mcp"), data
            )
            text = data.get("text", "")
            on_success = lambda: self.state.accept_grant(project["id"], text)  # noqa: E731
        return self.runner.start(project, kind, options, keys, on_success)


def _service_answer(data: Any, name: str, action: str) -> tuple[dict | None, dict | None]:
    """`services start|stop|restart --json` checked: the service and action asked for, a state,
    and `in_use` as a list of deployment names. Only those fields reach the browser."""
    ok = (
        isinstance(data, dict)
        and data.get("service") == name
        and data.get("action") == action
        and isinstance(data.get("state"), str)
        and isinstance(data.get("health", ""), str)
        and isinstance(data.get("in_use", []), list)
        and all(isinstance(item, str) for item in data.get("in_use", []))
    )
    if not ok:
        return None, error("BAD_OUTPUT", "The services command answered in an unexpected shape")
    return {
        "service": name,
        "action": action,
        "state": data["state"],
        "health": data.get("health", ""),
        "in_use": data.get("in_use", []),
    }, None


def _log_argv(
    project: dict, source: str, name: str, tail_text: str, since: str, previous: bool
) -> list[str]:
    """`services logs DIR NAME` or `logs ID --stack DIR --environment ENV` with fixed flags and
    `--json`; the line count, `--since` and `--previous` checked first (BadRequest)."""
    if not re.fullmatch(r"[1-9][0-9]{0,4}", tail_text) or int(tail_text) > MAX_LOG_TAIL:
        raise BadRequest(f"Lines must be 1 to {MAX_LOG_TAIL}", "TAIL_INVALID")
    if since and not LOG_SINCE.fullmatch(since):
        raise BadRequest("Since must look like 10m, 2h or 2026-11-11T09:00:00Z", "SINCE_INVALID")
    path = project["path"]
    if source == "service":
        if previous:
            raise BadRequest("Only a deployment has a previous container", "PREVIOUS_INVALID")
        argv = ["services", "logs", path, name]
    else:
        argv = ["logs", name, "--stack", path, "--environment", project["environment"]]
        argv += ["--previous"] if previous else []
    return [*argv, "--tail", tail_text, *(["--since", since] if since else []), "--json"]


def _log_answer(data: Any, source: str, name: str) -> dict | None:
    """`logs --json` / `services logs --json` checked (docs/logs.md): the source and name asked
    for, lines as strings, one level (or null) per line. Only the known keys go on."""
    if not isinstance(data, dict) or data.get("source") != (
        "agent" if source == "deployment" else "service"
    ):
        return None
    if data.get("deployment" if source == "deployment" else "name") != name:
        return None
    lines, levels = data.get("lines"), data.get("levels")
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        return None
    if not isinstance(levels, list) or len(levels) != len(lines):
        return None
    if not all(level in (None, "debug", "info", "warning", "error") for level in levels):
        return None
    texts = ("target", "name", "format", "pod_reason")
    if any(data.get(key) is not None and not isinstance(data[key], str) for key in texts):
        return None
    return {
        "source": source,
        "name": data.get("name"),
        "target": data.get("target"),
        "lines": lines,
        "levels": levels,
        "format": data.get("format"),
        "truncated": data.get("truncated") is True,
        "previous": data.get("previous") is True,
        "pod_reason": data.get("pod_reason"),
    }


# -- the route table ------------------------------------------------------------------------------


def envelope(data: Any = None, problem: dict | None = None, stale: bool = False) -> dict:
    return {"data": data, "error": problem, "stale": stale}


def docker_state(answer: dict) -> dict:
    """`{reachable, message}` from a `doctor --json` read: reachable is None when the answer
    has no Docker check (so no blocker shows), and the read's error is passed on."""
    if answer["error"] or not isinstance(answer["data"], list):
        problem = answer["error"] or error("BAD_OUTPUT", "The command returned unreadable output")
        return envelope(None, problem)
    for check in answer["data"]:
        if isinstance(check, dict) and check.get("name") == "Docker":
            return envelope(
                {
                    "reachable": check.get("passed") is True,
                    "message": str(check.get("message", "")),
                }
            )
    return envelope({"reachable": None, "message": ""})


@dataclass(frozen=True)
class Request:
    center: Center
    match: re.Match
    body: dict | None
    query: dict[str, list[str]]
    session: bytes = b""  # the session token's hash (owner of log follows, #821)
    token: str | None = None  # the session token itself (sign out of this tab)
    current: Session | None = None  # the live session; None only on `session=False` routes

    def project(self) -> dict:
        return self.center.need_project(self.match["project"])


@dataclass(frozen=True)
class Route:
    """One API route. `sample` is a path that matches `pattern`; the guard tests use it."""

    method: str
    pattern: str
    handler: Callable[[Request], dict]
    sample: str
    session: bool = True  # only sign-in itself runs without one
    poll: bool = False  # polls do not refresh the idle timer (R7)
    limit: int = MAX_BODY  # body cap for non-GET methods (MAX_EDITOR_BODY for editors)
    regex: re.Pattern = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "regex", re.compile(self.pattern))


def _overview(request: Request) -> dict:
    runner = request.center.runner
    # Exit 1 is doctor's answer when a required check fails (Docker down): keep it, or the
    # last passing answer would be shown instead.
    doctor = runner.read(["doctor", "--json"], ok_codes=(0, 1))
    return envelope({"version": __version__, "jobs": runner.running(), "doctor": doctor})


def _activity(request: Request) -> dict:
    """Running jobs, and the latest jobs of every project, newest first."""
    center = request.center
    return envelope({"running": center.runner.running(), "recent": center.state.recent_jobs()})


def _usage(request: Request) -> dict:
    """The project's usage ring (M6 #822): starts its `services usage --follow --json` child on
    the first poll; the child stops IDLE_AFTER seconds after the last one."""
    return request.center.usage.read(request.project())


def _watch(request: Request) -> dict:
    """The project's container events and latest status (M5 #821): starts its `watch --stack DIR
    --json --record` child on the first poll; the child stops IDLE_AFTER seconds after the last
    one. `--record` stores `died` and `unhealthy` for `agentbreeder events`."""
    return request.center.watch.read(request.project())


def _docker(request: Request) -> dict:
    return request.center.docker(fresh=request.query.get("fresh") == ["1"])


def _projects(request: Request) -> dict:
    state = request.center.state
    return envelope(
        {"projects": [{**p, "jobs": state.latest_jobs(p["id"])} for p in state.projects()]}
    )


def _view(request: Request) -> dict:
    return request.center.read_view(request.project(), request.match["view"], request.query)


def _used_by(request: Request) -> dict:
    """Which files of the project declare and lock REF (in process), and which deployments run
    it (`registry used-by --json`, a CLI child: it reads deploy state)."""
    ref = request.query.get("ref", [""])[0]
    project = request.project()
    files = request.center.reads.used_by(project["path"], ref)
    if files["error"]:
        return files
    # Canonical ref in argv (it parsed above). `used-by` exits 2 when a project file has an
    # error but still prints its JSON, so that answer is kept.
    canonical = str(registry.Ref.parse(ref))
    deployed = request.center.runner.read(
        ["registry", "used-by", canonical, "--project", project["path"], "--json"],
        ok_codes=(0, 2),
    )
    return envelope({"files": files["data"], "deployments": deployed})


def _query_ref(request: Request, name: str) -> registry.Ref:
    try:
        return registry.Ref.parse(request.query.get(name, [""])[0])
    except registry.RegistryError as problem:
        raise BadRequest(str(problem).removeprefix(f"[{problem.code}] "), problem.code) from None


def _registry_show(request: Request) -> dict:
    """One version (`registry show --json`): a CLI child, cached until the registries change."""
    project = request.project()
    return request.center.reads.show(project["path"], _query_ref(request, "ref"))


def _registry_diff(request: Request) -> dict:
    """What changed between two versions of one name (`registry diff --json`)."""
    project = request.project()
    first, second = _query_ref(request, "from"), _query_ref(request, "to")
    return request.center.reads.diff(project["path"], first, second)


def _registry_used_by(request: Request) -> dict:
    """Which registered projects (of this environment) declare, lock and run REF: one
    `registry used-by --json` child over all of them (console spec §8.4, "among registered
    projects"). It exits 2 when a project file has an error but still prints its answer."""
    project = request.project()
    ref = _query_ref(request, "ref")
    paths = [project["path"]] + sorted(
        p["path"]
        for p in request.center.state.projects()
        if p["environment"] == project["environment"] and p["id"] != project["id"]
    )
    argv = ["registry", "used-by", str(ref)]
    for path in paths:
        argv += ["--project", path]
    return request.center.runner.read([*argv, "--json"], ok_codes=(0, 2))


def _service_logs(request: Request) -> dict:
    return request.center.logs(
        request.project(), "service", request.match["service"], request.query
    )


def _deployment_logs(request: Request) -> dict:
    return request.center.logs(
        request.project(), "deployment", request.match["deployment"], request.query
    )


def _follow_service_logs(request: Request) -> dict:
    return request.center.follow_logs(
        request.project(), "service", request.match["service"], request.body, request.session
    )


def _follow_deployment_logs(request: Request) -> dict:
    return request.center.follow_logs(
        request.project(),
        "deployment",
        request.match["deployment"],
        request.body,
        request.session,
    )


def _follow_read(request: Request) -> dict:
    """A follow's lines after `after` (a poll: it does not keep the session alive)."""
    after = request.query.get("after", ["0"])[0]
    if not re.fullmatch(r"0|[1-9][0-9]{0,15}", after):
        raise BadRequest("After must be a line number", "AFTER_INVALID")
    try:
        return request.center.log_follows.read(
            request.session, request.match["follow"], int(after)
        )
    except logfollow.FollowNotFound as missing:
        raise NotFound("No such log follow") from missing


def _follow_stop(request: Request) -> dict:
    try:
        return request.center.log_follows.stop(request.session, request.match["follow"])
    except logfollow.FollowNotFound as missing:
        raise NotFound("No such log follow") from missing


def _failed_tail(request: Request) -> dict:
    return request.center.failed_tail(request.project(), request.match["agent"])


def _eval_report(request: Request) -> dict:
    return request.center.eval_reports(request.project(), request.match["run"], request.query)


def _tool_log(request: Request) -> dict:
    return request.center.tool_log(request.project(), request.query)


def _knowledge_status(request: Request) -> dict:
    return request.center.knowledge_status(request.project(), request.query)


def _llm_cost(request: Request) -> dict:
    return request.center.llm_cost(request.project(), request.query)


def _agents(request: Request) -> dict:
    return request.center.agents()


def _try_turn(request: Request) -> dict:
    return request.center.try_turn(request.project(), request.match["agent"], request.body)


def _registry_edit(request: Request) -> dict:
    return request.center.registry_edit(request.project(), request.body)


def _registry_fork(request: Request) -> dict:
    return request.center.registry_fork(request.project(), request.body)


def _registry_import(request: Request) -> dict:
    return request.center.registry_import(request.project(), request.body)


def _mcp_grant(request: Request) -> dict:
    return request.center.mcp_grant(request.project(), request.body)


def _mcp_pins(request: Request) -> dict:
    return request.center.mcp_pins(request.project(), request.body)


def _mcp_tools(request: Request) -> dict:
    return request.center.mcp_tools(request.project(), _query_ref(request, "ref"))


def _deployment_traces(request: Request) -> dict:
    return request.center.deployment_traces(
        request.project(), request.match["deployment"], request.query
    )


def _service_action(request: Request) -> dict:
    return request.center.service_action(
        request.project(), request.match["service"], request.match["action"], request.body
    )


def _set_ref(request: Request) -> dict:
    return request.center.set_ref(request.project(), request.body)


def _turn_trace(request: Request) -> dict:
    return request.center.turn_trace(request.project(), request.match["trace"])


def _job(request: Request) -> dict:
    after = request.query.get("after", ["0"])[0]
    job = request.center.state.job(
        request.match["job"], int(after) if re.fullmatch(r"[0-9]{1,12}", after) else 0
    )
    if job is None:
        raise NotFound("Unknown job")
    live = request.center.runner.job_status(request.match["job"])
    return envelope({**job, "cancelling": False, "can_stop": False, **(live or {})})


def _session(request: Request) -> dict:
    """Sign in: `{token}` (the one-time terminal link) or `{email, password}`."""
    body = request.body
    if "token" in body:
        session = request.center.redeem(str(body.get("token", "")))
        if session is None:
            raise SignInRefused(
                403, "LINK_USED", "This link was already used or has expired", None
            )
        return envelope(session)
    return envelope(request.center.sign_in(body.get("email"), body.get("password")))


def _auth_state(request: Request) -> dict:
    """Before sign-in: whether a password exists, so the page shows the form or says to open the
    terminal's link. Nothing else."""
    return envelope({"password_set": request.center.state.has_password(), "email": auth.EMAIL})


def _setup(request: Request) -> dict:
    return envelope(
        request.center.set_first_password(request.current, request.body.get("password"))
    )


def _change_password(request: Request) -> dict:
    return envelope(
        request.center.change_password(request.body.get("current"), request.body.get("password"))
    )


def _sign_out(request: Request) -> dict:
    request.center.end_session(request.token)
    request.center.state.record_event("sign-out", "ok", "browser")
    return envelope({"signed_out": True})


def _sign_out_everywhere(request: Request) -> dict:
    request.center.state.end_all_sessions("browser")
    return envelope({"signed_out": True})


def _settings(request: Request) -> dict:
    return envelope(request.center.settings())


def _save_settings(request: Request) -> dict:
    return envelope(request.center.save_settings(request.body))


def _add_project(request: Request) -> dict:
    body, center = request.body, request.center
    if body.get("example"):
        return envelope(center.onboard_example(str(body["example"])))
    if "starter" in body:
        return envelope(center.create_starter(body["starter"]))
    if "stack_for" in body:
        return envelope(center.onboard_agent_yaml(body["stack_for"]))
    if body.get("path"):
        return envelope(center.register(Path(str(body["path"]))))
    raise BadRequest("Name an example or a path")


def _preview_stack_for(request: Request) -> dict:
    return envelope(request.center.preview_stack_for(request.body))


def _builder_parts(request: Request) -> dict:
    return request.center.builder_parts()


def _preview_builder(request: Request) -> dict:
    return envelope(request.center.preview_builder(request.body))


def _create_builder(request: Request) -> dict:
    return envelope(request.center.create_builder(request.body))


def _plan(request: Request) -> dict:
    result = request.center.plan(request.project())
    return envelope(
        {"grant": result["data"], "accepted_text": result["accepted_text"]},
        result["error"],
        result["stale"],
    )


def _start_job(request: Request) -> dict:
    return envelope({"job": request.center.start_job(request.project(), request.body)})


def _cancel(request: Request) -> dict:
    return envelope({"cancelled": request.center.runner.cancel(request.match["job"])})


def _stop_job(request: Request) -> dict:
    """The confirmed escalation after a Cancel has waited: stops the job's process group."""
    return envelope({"stopped": request.center.runner.stop(request.match["job"])})


ROUTES: tuple[Route, ...] = (
    Route("GET", r"/api/overview", _overview, "/api/overview"),
    Route("GET", r"/api/activity", _activity, "/api/activity", poll=True),
    Route("GET", r"/api/projects", _projects, "/api/projects"),
    Route("GET", r"/api/agents", _agents, "/api/agents"),
    Route("GET", r"/api/builder/parts", _builder_parts, "/api/builder/parts"),
    # Before the generic view route, which would match it too. A poll: it does not keep a
    # session alive.
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/usage",
        _usage,
        "/api/projects/0123456789ab/usage",
        poll=True,
    ),
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/watch",
        _watch,
        "/api/projects/0123456789ab/watch",
        poll=True,
    ),
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/(?P<view>[a-z]+)",
        _view,
        "/api/projects/0123456789ab/services",
    ),
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/used-by",
        _used_by,
        "/api/projects/0123456789ab/used-by",
    ),
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/registry/show",
        _registry_show,
        "/api/projects/0123456789ab/registry/show",
    ),
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/registry/diff",
        _registry_diff,
        "/api/projects/0123456789ab/registry/diff",
    ),
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/registry/used-by",
        _registry_used_by,
        "/api/projects/0123456789ab/registry/used-by",
    ),
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/mcp/tools",
        _mcp_tools,
        "/api/projects/0123456789ab/mcp/tools",
    ),
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/tools/log",
        _tool_log,
        "/api/projects/0123456789ab/tools/log",
    ),
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/knowledge/status",
        _knowledge_status,
        "/api/projects/0123456789ab/knowledge/status",
    ),
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/logs/service/{SERVICE}",
        _service_logs,
        "/api/projects/0123456789ab/logs/service/litellm",
    ),
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/logs/deployment/{DEPLOYMENT}",
        _deployment_logs,
        "/api/projects/0123456789ab/logs/deployment/" + "0" * 32,
    ),
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/logs/failed/{AGENT}",
        _failed_tail,
        "/api/projects/0123456789ab/logs/failed/demo",
    ),
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/evals/{EVAL_RUN}",
        _eval_report,
        "/api/projects/0123456789ab/evals/20261113-213200-1a2b3c",
    ),
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/traces/{TRACE}",
        _turn_trace,
        "/api/projects/0123456789ab/traces/" + "0" * 32,
    ),
    Route("GET", rf"/api/jobs/{JOB_ID}", _job, "/api/jobs/" + "0" * 32, poll=True),
    Route("GET", r"/api/auth", _auth_state, "/api/auth", session=False),
    Route("GET", r"/api/settings", _settings, "/api/settings"),
    Route("POST", r"/api/session", _session, "/api/session", session=False),
    Route("POST", r"/api/setup", _setup, "/api/setup"),
    Route("POST", r"/api/password", _change_password, "/api/password"),
    Route("POST", r"/api/signout", _sign_out, "/api/signout"),
    Route("POST", r"/api/signout-everywhere", _sign_out_everywhere, "/api/signout-everywhere"),
    Route("POST", r"/api/settings", _save_settings, "/api/settings"),
    Route("POST", r"/api/projects", _add_project, "/api/projects"),
    Route("POST", r"/api/projects/stack-for", _preview_stack_for, "/api/projects/stack-for"),
    Route(
        "POST",
        r"/api/projects/builder",
        _preview_builder,
        "/api/projects/builder",
        limit=MAX_EDITOR_BODY,  # the spec: up to 256 KiB of prompt and cases
    ),
    Route(
        "POST",
        r"/api/projects/builder/create",
        _create_builder,
        "/api/projects/builder/create",
        limit=MAX_EDITOR_BODY,
    ),
    Route("POST", rf"/api/projects/{PROJECT_ID}/plan", _plan, "/api/projects/0123456789ab/plan"),
    Route(
        "POST", rf"/api/projects/{PROJECT_ID}/jobs", _start_job, "/api/projects/0123456789ab/jobs"
    ),
    Route("POST", rf"/api/jobs/{JOB_ID}/cancel", _cancel, "/api/jobs/" + "0" * 32 + "/cancel"),
    Route("POST", rf"/api/jobs/{JOB_ID}/stop", _stop_job, "/api/jobs/" + "0" * 32 + "/stop"),
    Route(
        "POST",
        rf"/api/projects/{PROJECT_ID}/agents/{AGENT}/try",
        _try_turn,
        "/api/projects/0123456789ab/agents/support/try",
    ),
    Route(
        "POST",
        rf"/api/projects/{PROJECT_ID}/registry/edit",
        _registry_edit,
        "/api/projects/0123456789ab/registry/edit",
        limit=MAX_EDITOR_BODY,
    ),
    Route(
        "POST",
        rf"/api/projects/{PROJECT_ID}/registry/fork",
        _registry_fork,
        "/api/projects/0123456789ab/registry/fork",
    ),
    Route(
        "POST",
        rf"/api/projects/{PROJECT_ID}/registry/import",
        _registry_import,
        "/api/projects/0123456789ab/registry/import",
    ),
    Route(
        "POST",
        rf"/api/projects/{PROJECT_ID}/mcp/grant",
        _mcp_grant,
        "/api/projects/0123456789ab/mcp/grant",
    ),
    Route(
        "POST",
        rf"/api/projects/{PROJECT_ID}/mcp/pins",
        _mcp_pins,
        "/api/projects/0123456789ab/mcp/pins",
    ),
    Route(
        "POST",
        rf"/api/projects/{PROJECT_ID}/agent/set-ref",
        _set_ref,
        "/api/projects/0123456789ab/agent/set-ref",
    ),
    Route("GET", r"/api/docker", _docker, "/api/docker", poll=True),
    Route(
        "POST",
        rf"/api/projects/{PROJECT_ID}/services/{SERVICE_OR_RELAY}/(?P<action>start|stop|restart)",
        _service_action,
        "/api/projects/0123456789ab/services/litellm/restart",
    ),
    # Log follows (#821, M5 PR 2): start one, poll its lines, stop it.
    Route(
        "POST",
        rf"/api/projects/{PROJECT_ID}/logs/service/{SERVICE}/follow",
        _follow_service_logs,
        "/api/projects/0123456789ab/logs/service/litellm/follow",
    ),
    Route(
        "POST",
        rf"/api/projects/{PROJECT_ID}/logs/deployment/{DEPLOYMENT}/follow",
        _follow_deployment_logs,
        "/api/projects/0123456789ab/logs/deployment/" + "0" * 32 + "/follow",
    ),
    Route(
        "GET", rf"/api/follows/{FOLLOW_ID}", _follow_read, "/api/follows/" + "0" * 32, poll=True
    ),
    Route(
        "POST",
        rf"/api/follows/{FOLLOW_ID}/stop",
        _follow_stop,
        "/api/follows/" + "0" * 32 + "/stop",
    ),
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/deployments/{DEPLOYMENT}/traces",
        _deployment_traces,
        "/api/projects/0123456789ab/deployments/" + "0" * 32 + "/traces",
    ),
    # A dash, so the generic view route (`[a-z]+`) never matches it (#906).
    Route(
        "GET",
        rf"/api/projects/{PROJECT_ID}/llm-cost",
        _llm_cost,
        "/api/projects/0123456789ab/llm-cost",
    ),
)


def find_route(method: str, path: str, routes=ROUTES) -> tuple[Route | None, re.Match | None, set]:
    """The route for `method` and `path`, and the methods `path` allows (for a 405)."""
    allowed: set[str] = set()
    for route in routes:
        match = route.regex.fullmatch(path)
        if match is None:
            continue
        if route.method == method:
            return route, match, allowed
        allowed.add(route.method)
    return None, None, allowed


class NotFound(Exception):
    pass


class Refused(Exception):
    pass


class HttpError(Exception):
    def __init__(
        self, status: int, code: str, message: str, extra: dict | None = None, data: Any = None
    ):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra
        self.data = data


class Handler(BaseHTTPRequestHandler):
    center: Center
    routes: tuple[Route, ...] = ROUTES
    server_version = "agentbreeder-console"
    sys_version = ""
    timeout = 30  # a client that claims a body and sends nothing does not hold a thread
    _body_pending = False  # a body was announced and not read

    def log_message(self, format, *args):  # noqa: A002
        pass  # URLs and bodies stay out of the terminal

    def handle(self):
        # A client that resets while its request line or headers are still being read fails in
        # the base class, before `_dispatch`; it needs no reply and no traceback.
        try:
            super().handle()
        except ConnectionError:
            self.close_connection = True

    def __getattr__(self, name: str):
        # Every method, including ones no route uses, reaches the same guarded dispatch; the
        # base class would otherwise answer an unknown method with 501 before any check.
        if name.startswith("do_"):
            return lambda: self._dispatch(name[3:])
        raise AttributeError(name)

    def send_error(self, code, message=None, explain=None):
        """`http.server`'s own errors (a bad request line, header or version) get the envelope
        and the security headers too, never its HTML page."""
        self.close_connection = True
        self.request_version = "HTTP/1.0"  # an unparsed request line leaves 0.9: no headers
        try:
            self._json(code, envelope(None, error("HTTP", "Bad request")))
        except OSError:
            pass

    def _send(self, code: int, body: bytes, kind: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for key, value in {**SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, value: dict, extra: dict | None = None) -> None:
        self._send(code, json.dumps(value).encode(), "application/json", extra)

    def _fail(self, failure: HttpError) -> None:
        try:
            self._json(
                failure.status,
                envelope(failure.data, error(failure.code, failure.message)),
                failure.extra,
            )
        except OSError:  # the client went away; nothing to tell it
            self.close_connection = True
            return
        self._discard_body()

    def _discard_body(self) -> None:
        """After an early reply, read and drop an unread body (up to MAX_DISCARD), so closing
        the socket does not reset the connection and lose the reply the client is waiting
        for. The reply is sent first and our side of the connection shut, so the client sees
        it even if it is still sending."""
        if not self._body_pending:
            return
        self._body_pending = False
        length = self.headers.get("Content-Length", "")
        if not re.fullmatch(r"[0-9]{1,12}", length):
            return
        remaining = min(int(length), MAX_DISCARD)
        deadline = time.monotonic() + DISCARD_SECONDS  # a trickling client cannot hold a thread
        try:
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_WR)
            self.connection.settimeout(DISCARD_SECONDS)
            while remaining > 0 and time.monotonic() < deadline:
                chunk = self.rfile.read1(min(remaining, 65536))  # whatever has arrived
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass

    # -- guards ----------------------------------------------------------------------------------

    def _localhost_redirect(self, method: str) -> bool:
        """`localhost:<port>` gets a redirect to `127.0.0.1:<port>`, the one host form the console
        accepts (spec §8.1 #1), on GET or HEAD of a static path only; the API never redirects."""
        if method not in ("GET", "HEAD"):
            return False
        if self.headers.get("Host") != f"localhost:{self.center.port}":
            return False
        try:
            path = urlsplit(self.path).path
        except ValueError:
            return False
        if path not in self.center.static:
            return False
        self._send(302, b"", "text/plain; charset=utf-8", {"Location": self.center.origin + path})
        return True

    def _guard_host(self) -> None:
        if self.headers.get("Host") != f"127.0.0.1:{self.center.port}":
            raise HttpError(403, "FORBIDDEN", "Bad host")

    def _guard_api(self) -> None:
        # The custom header forces a CORS preflight that is never approved; Sec-Fetch-Site
        # refuses same-site pages, such as other 127.0.0.1 ports (S-25).
        if self.headers.get("X-AgentBreeder-Center") != "1":
            raise HttpError(403, "FORBIDDEN", "Forbidden")
        if self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin":
            raise HttpError(403, "FORBIDDEN", "Forbidden")

    def _guard_write(self) -> None:
        if self.headers.get("Origin") != self.center.origin:
            raise HttpError(403, "FORBIDDEN", "Forbidden")
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise HttpError(403, "FORBIDDEN", "Forbidden")

    def _guard_session(self, route: Route | None) -> Session | None:
        # A header, not a cookie: browsers send cookies to every port on 127.0.0.1, including
        # agent endpoints and stack services. Only a route marked `session=False` (sign-in)
        # skips it; an unknown path or method needs a session before it learns 404 or 405.
        if route is not None and not route.session:
            return None
        token = self.headers.get("X-AgentBreeder-Session")
        refresh = route is None or not route.poll
        current = self.center.session(token, refresh=refresh)
        if current is None:
            raise HttpError(401, "SIGN_IN", "Sign in again")
        return current

    def _read_body(self, route: Route) -> dict:
        length = self.headers.get("Content-Length")
        if length is None:
            raise HttpError(411, "LENGTH_REQUIRED", "Content-Length is required")
        if not re.fullmatch(r"[0-9]{1,12}", length):
            raise HttpError(400, "BAD_REQUEST", "Invalid Content-Length")
        if int(length) > route.limit:
            raise HttpError(413, "TOO_LARGE", f"The request is over {route.limit // 1024} KiB")
        self._body_pending = False
        try:
            raw = self.rfile.read(int(length))
        except TimeoutError as problem:
            raise HttpError(408, "TIMEOUT", "The request body did not arrive in time") from problem
        if len(raw) != int(length):
            raise HttpError(400, "BAD_REQUEST", "The request body is shorter than its length")
        try:
            value = json.loads(raw or b"{}")
        except (ValueError, RecursionError) as problem:
            raise HttpError(400, "BAD_REQUEST", "The request is not JSON") from problem
        if not isinstance(value, dict):
            raise HttpError(400, "BAD_REQUEST", "The request must be a JSON object")
        return value

    # -- dispatch --------------------------------------------------------------------------------

    def _dispatch(self, method: str) -> None:
        """Guards, in order, for every method: host; then for /api/ the API header and
        Sec-Fetch-Site, for non-GET methods Origin and content type, then the session; only
        then 404/405, the body cap and the handler."""
        self._body_pending = self.headers.get("Content-Length", "0") not in ("", "0")
        try:
            if self._localhost_redirect(method):
                return
            self._guard_host()
            try:
                parts = urlsplit(self.path)
            except ValueError as problem:
                raise HttpError(400, "BAD_REQUEST", "Invalid request path") from problem
            if not parts.path.startswith("/api/"):
                return self._static(method, parts.path)
            self._guard_api()
            route, match, allowed = find_route(method, parts.path, self.routes)
            if method != "GET":
                self._guard_write()
            current = self._guard_session(route)
            if route is None:
                if allowed:
                    raise HttpError(
                        405, "METHOD", "Method not allowed", {"Allow": ", ".join(sorted(allowed))}
                    )
                raise HttpError(404, "NOT_FOUND", "Not found")
            body = self._read_body(route) if method != "GET" else None
            token = self.headers.get("X-AgentBreeder-Session")
            session = _hash(token) if token else b""
            request = Request(
                self.center, match, body, parse_qs(parts.query), session, token, current
            )
            self._json(200, self._handle(route, request))
        except HttpError as failure:
            self._fail(failure)
        except ConnectionError:  # the client closed the tab or reloaded mid-request
            self.close_connection = True
        except Exception as problem:  # never a traceback or a dropped connection
            print(f"console: request failed: {type(problem).__name__}", file=sys.stderr)
            self._fail(HttpError(500, "INTERNAL", "The console hit an unexpected error"))

    def _static(self, method: str, path: str) -> None:
        if method not in ("GET", "HEAD"):
            raise HttpError(405, "METHOD", "Method not allowed", {"Allow": "GET, HEAD"})
        entry = self.center.static.get(path)
        if entry is None:
            raise HttpError(404, "NOT_FOUND", "Not found")
        self._send(200, entry[0], entry[1])

    def _handle(self, route: Route, request: Request) -> dict:
        try:
            return route.handler(request)
        except WriteBusy as busy:
            message = (
                "A running job needs this; try again when it ends"
                if busy.reason == "job"
                else "Another save is in progress; try again when it ends"
            )
            raise HttpError(409, "WRITE_BUSY", message) from busy
        except Busy as busy:
            raise HttpError(
                409, "BUSY", "Another job is running", data={"job": busy.job}
            ) from busy
        except BadRequest as problem:
            raise HttpError(400, problem.code, str(problem)) from problem
        except NotFound as problem:
            raise HttpError(404, "NOT_FOUND", str(problem)) from problem
        except Refused as problem:
            raise HttpError(403, "REFUSED", str(problem)) from problem
        except SignInRefused as problem:
            raise HttpError(
                problem.status, problem.code, str(problem), data=problem.data
            ) from problem
        except ValueError as problem:
            raise HttpError(400, "BAD_REQUEST", "Invalid request") from problem
        except OSError as problem:
            raise HttpError(
                500, "LOCAL_FILE", "A local file could not be read or written"
            ) from problem
        except Exception as problem:  # never a traceback or a dropped connection
            print(
                f"console: {route.method} {route.pattern} failed: {type(problem).__name__}",
                file=sys.stderr,
            )
            raise HttpError(500, "INTERNAL", "The console hit an unexpected error") from problem


class PortBusy(OSError):
    def __init__(self, port: int, scanned: bool):
        super().__init__(port)
        self.port, self.scanned = port, scanned


def port_candidates(center: Center, port: int | None) -> tuple[list[int], bool]:
    """The ports to try, and whether that is a scan. An explicit `--port` is tried alone. In
    first-run state (no password yet) the preferred port comes first, then the rest of
    8080-8099. Once a password exists only the preferred port is tried (S-29): another user
    squatting it must not move the console, because a stale tab or bookmark would then show the
    squatter's sign-in form."""
    if port is not None:
        return [port], False
    remembered = center.state.setting("port") or ""
    preferred = (
        int(remembered) if remembered.isdigit() and 0 < int(remembered) < 65536 else PORTS[0]
    )
    if center.state.has_password():
        return [preferred], False
    return [preferred, *(p for p in PORTS if p != preferred)], True


def in_use(port: int) -> bool:
    """Something already answers on 127.0.0.1:`port`. On macOS a bind to 127.0.0.1 succeeds
    beside a listener on 0.0.0.0 or [::] (Docker-published ports, most dev servers) and takes
    its loopback traffic, so a successful bind alone does not mean the port is free."""
    if port == 0:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except ConnectionRefusedError:
        return False
    except OSError:
        return True  # a timeout (a full accept queue) or a reset: something holds it


def make_server(center: Center, port: int | None) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"center": center})
    candidates, scanned = port_candidates(center, port)
    for candidate in candidates:
        if in_use(candidate):
            continue
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            break
        except OSError as problem:
            if problem.errno != errno.EADDRINUSE:
                raise
    else:
        raise PortBusy(candidates[0], scanned)
    server.daemon_threads = True
    center.port = server.server_address[1]
    if port is None:
        try:
            center.state.set_setting("port", str(center.port))
        except sqlite3.Error:
            pass  # remembering the port is a convenience
    return server


def _watch_terminal(center: Center) -> None:
    """Enter in the terminal mints a new login link."""
    for _ in sys.stdin:
        url, page = center.mint_login()
        _show_link(url, page, "New one-time link")


def _show_link(url: str, page: Path, label: str) -> None:
    """S-15: a one-time link is a bearer credential. It is printed only to a terminal; when
    stdout is redirected (a log file) only the path of the 0600 `login.html` is printed."""
    if sys.stdout.isatty():
        print(f"{label}, valid {TOKEN_TTL // 60} minutes: {url}", flush=True)
    else:
        print(
            f"{label} written to {page} (open that file; valid {TOKEN_TTL // 60} minutes, "
            "once). Links are printed only to a terminal.",
            flush=True,
        )


STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class _Flag:
    """A flag a signal handler may set. Not a threading.Event: its set() takes a lock, and a
    second signal handled while the first handler holds that lock would deadlock the main thread
    (seen on Linux CI with SIGTERM then SIGHUP back to back). Plain attribute writes take none."""

    def __init__(self):
        self.value = False

    def is_set(self) -> bool:
        return self.value

    def set(self) -> None:
        self.value = True

    def clear(self) -> None:
        self.value = False


_stopping = _Flag()


def _stop(signum, frame):
    """SIGTERM/SIGHUP: stop once, like Ctrl-C. Later ones (a closing terminal can send more than
    one, and a supervisor may repeat SIGTERM) are ignored here, so they never cut the jobs'
    SIGINT grace short; only a second Ctrl-C does. A Python handler, unlike SIG_IGN, is not
    inherited by children."""
    if _stopping.is_set():
        return
    _stopping.set()
    raise KeyboardInterrupt


def _say(text: str) -> None:
    """A status line on stderr. A closed terminal (SIGHUP) makes writes fail; stopping the jobs
    must go on regardless, and the interpreter's last flush must not fail either."""
    try:
        print(text, file=sys.stderr, flush=True)
    except (OSError, ValueError):  # ValueError: stderr already closed
        devnull = open(os.devnull, "w")  # noqa: SIM115 (lives until exit)
        sys.stdout = sys.stderr = devnull


def _shut_down(center: Center) -> None:
    """Stop every job. A signal landing here (a Ctrl-C, or a stop signal racing the first)
    must not skip it: an interrupted attempt is retried (it sends SIGINT again, which is
    harmless), and if every attempt is interrupted, each job's group still gets SIGTERM."""
    for _attempt in range(3):
        try:
            running = center.runner.running()
            if running:
                _say(f"Stopping {len(running)} running job(s); Ctrl-C again to skip the wait.")
            stopped = center.runner.shutdown()
            if stopped:
                _say(f"Stopped {len(stopped)} running job(s).")
            return
        except KeyboardInterrupt:
            continue
    center.runner.terminate_all()


def main(args) -> int:
    """Run the console; the signal handlers it installs are restored when it returns, so an
    ignored SIGTERM/SIGHUP is never inherited by a process started afterwards."""
    previous = {number: signal.getsignal(number) for number in STOP_SIGNALS}
    _stopping.clear()
    try:
        return _main(args)
    finally:
        for number, handler in previous.items():
            if handler is not None:  # None: installed outside Python, cannot be restored
                signal.signal(number, handler)


def serve_process(args) -> int:
    """The console as a process (`python -I -m agentbreeder.center`): `main`, then ignore any
    later SIGTERM/SIGHUP. A closing terminal or a supervisor can send one after the console has
    stopped cleanly; the process is about to exit and must not die of it."""
    code = main(args)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    return code


def _main(args) -> int:
    """The console server process (`python -I -m agentbreeder.center`, started by `up`)."""
    if not ENVIRONMENT.fullmatch(args.environment):
        print("Invalid --environment", file=sys.stderr)
        return 2
    # Started from a backgrounded shell, SIGINT would be ignored here and in every job, and
    # Cancel would do nothing. SIGTERM and SIGHUP (terminal closed) stop it the same way, so
    # its children are stopped too.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGHUP, _stop)
    # `up --set-password` / `--reset-password` (M2) branch here, before the lock: they run
    # beside a live console and must not take it, sweep its jobs or migrate under it.
    try:
        if getattr(args, "set_password", False) or getattr(args, "reset_password", False):
            return password_command(args)
        return _locked(args)
    except KeyboardInterrupt:  # stopped before serving (taking the lock, a slow port scan)
        _stopping.set()  # a second stop signal while unwinding is a no-op
        return 130


def _read_new_password() -> str:
    """S-15: `getpass` (no echo) twice on a terminal, else exactly one line of stdin. Never argv,
    never printed."""
    if sys.stdin.isatty():
        first = getpass.getpass(f"New console password for {auth.EMAIL}: ")
        auth.check_policy(first)
        if getpass.getpass("Type it again: ") != first:
            raise auth.PolicyError("The two passwords differ")
        return first
    line = sys.stdin.readline(auth.MAX_LENGTH + 3)
    if line.endswith("\r\n"):
        line = line[:-2]
    elif line.endswith("\n"):
        line = line[:-1]
    auth.check_policy(line)
    return line


def _password_state(directory: Path) -> CenterState:
    """center.sqlite3 for a password command. If its schema is behind this version it must be
    migrated, which is only safe while no console runs (a running one of another version would
    have the schema change under it)."""
    path = directory / "center.sqlite3"
    version = 0
    if path.exists():
        probe = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = probe.execute("SELECT version FROM schema_version").fetchone()
            version = row[0] if row else 0
        except sqlite3.Error:
            version = 0
        finally:
            probe.close()
    if version >= SCHEMA_VERSION:
        return CenterState(directory)  # SchemaTooNew when it is newer
    instance = SingleInstance(directory)
    instance.acquire()  # AlreadyRunning: a console of another version is serving
    try:
        return CenterState(directory)
    finally:
        instance.release_quietly()


def password_command(args) -> int:
    """`up --set-password` (the first password) and `up --reset-password` (a forgotten one; it
    ends every open session, even in a running console). Terminal-only by design (S-28)."""
    if args.dir is not None or args.yes or getattr(args, "link", False):
        print(
            "--set-password and --reset-password take no project, --yes or --link",
            file=sys.stderr,
        )
        return 2
    reset = bool(getattr(args, "reset_password", False))
    try:
        state = _password_state(center_directory())
    except AlreadyRunning:
        print(
            "A console of another AgentBreeder version is running; stop it (Ctrl-C in its "
            "terminal), then try again.",
            file=sys.stderr,
        )
        return 2
    except SchemaTooNew as problem:
        print(str(problem), file=sys.stderr)
        return 2
    except (sqlite3.Error, OSError) as problem:
        print(f"Cannot open the console database (center.sqlite3): {problem}", file=sys.stderr)
        return 2
    if not reset and state.has_password():
        print(
            "A console password is already set. Change it in the console's Settings, or use "
            "`agentbreeder up --reset-password` if you forgot it.",
            file=sys.stderr,
        )
        return 2
    try:
        password = _read_new_password()
    except auth.PolicyError as problem:
        print(f"Password not set: {problem}", file=sys.stderr)
        return 2
    except EOFError:
        print("Password not set: no input", file=sys.stderr)
        return 2
    record = auth.hash_password(password)
    if reset:
        state.set_password(auth.EMAIL, record, "password-reset", "terminal")
        print(
            f"Console password reset for {auth.EMAIL}. Every open console session has ended; "
            "sign in again."
        )
        return 0
    if not state.set_password(auth.EMAIL, record, "password-set", "terminal", only_if_none=True):
        print("A console password was set meanwhile; nothing changed.", file=sys.stderr)
        return 2
    print(f"Console password set for {auth.EMAIL}. Sign in at the URL `agentbreeder up` prints.")
    return 0


def _locked(args) -> int:
    try:
        directory = center_directory()
        instance = SingleInstance(directory)
        instance.acquire()
    except AlreadyRunning as running:
        return _already_running(args, running.url)
    except OSError as problem:
        print(f"Cannot use the console directory: {problem}", file=sys.stderr)
        return 2
    try:
        return _serve(args, directory, instance)
    finally:
        instance.release()


def _already_running(args, url: str | None) -> int:
    where = url or "on this machine (it is starting or stopping)"
    print(f"The AgentBreeder console is already running: {where}")
    print("Use that one, or stop it first (Ctrl-C in its terminal).")
    if getattr(args, "link", False):
        print("For a one-time link, press Enter in the terminal running it.")
    if args.dir is not None or args.yes:
        print(
            "Nothing else was done: add the project and start its services in that console.",
            file=sys.stderr,
        )
        return 1
    return 0


def _run(args, center: Center, server: ThreadingHTTPServer, instance: SingleInstance) -> int:
    """From the bind to the end of serving: register DIR, print the link, `--yes`, serve."""
    project = None
    if args.dir is not None:
        try:
            project = center.register(args.dir)
        except BadRequest as problem:
            print(str(problem), file=sys.stderr)
            return 2
    instance.publish(center.origin)  # only once there is a console to answer at it
    auth.warm()  # the dummy hash, so the first unknown-email check costs what every other does
    print(f"AgentBreeder console: {center.origin} (this machine only, single user)")
    # First run (or a2 state with no password): the link mints the session that may set the
    # password (S-28). Once a password exists, the browser opens the sign-in page and a link is
    # made only on demand: `up --link`, or Enter here.
    if not center.state.has_password() or getattr(args, "link", False):
        url, page = center.mint_login()
        label = "One-time link" if center.state.has_password() else "One-time setup link"
        _show_link(url, page, label)
        opens = page.as_uri()
    else:
        print(f"Sign in as {auth.EMAIL} at {center.origin}/", flush=True)
        opens = f"{center.origin}/"
    if sys.stdin.isatty():
        print("Press Enter here for a new one-time link.", flush=True)
        threading.Thread(target=_watch_terminal, args=(center,), daemon=True).start()
    if not args.no_browser:
        webbrowser.open(opens)
    if args.yes and project is not None:
        answer = center.plan(project)
        preview = answer.get("data")
        if (answer.get("error") or {}).get("code") == "SERVICES_BUSY":
            print(f"Services not started: {SERVICES_BUSY_TEXT}", file=sys.stderr)
        elif preview is None:
            print("Could not read the stack's grant", file=sys.stderr)
        else:
            print(preview["text"], file=sys.stderr)
            try:
                center.start_job(project, {"kind": "services-up", "options": preview})
            except (BadRequest, Busy, WriteBusy) as problem:
                print(f"Services not started: {problem}", file=sys.stderr)
    server.serve_forever()
    return 0


def _serve(args, directory: Path, instance: SingleInstance) -> int:
    removed = remove_leftovers(directory)  # S-16: only once the lock is held
    if removed:
        print(f"Removed {len(removed)} file(s) left by a console that crashed.", file=sys.stderr)
    stopped = ChildRecords(directory).sweep()  # S-10: follows a killed console left running
    if stopped:
        print(f"Stopped {stopped} process(es) left by a console that crashed.", file=sys.stderr)
    try:
        center = Center(directory, args.environment)
        center.state.interrupt_running()  # the lock is held: no other console owns these
    except SchemaTooNew as problem:
        print(str(problem), file=sys.stderr)
        return 2
    except (sqlite3.Error, OSError) as problem:
        print(f"Cannot open the console database (center.sqlite3): {problem}", file=sys.stderr)
        return 2
    try:
        server = make_server(center, args.port)
    except PortBusy as busy:
        if busy.scanned:
            print(
                f"No free port on 127.0.0.1 between {PORTS[0]} and {PORTS[-1]}; "
                "start with --port N",
                file=sys.stderr,
            )
        else:
            print(
                f"127.0.0.1:{busy.port} is in use by another program. Open only the URL that "
                "`up` prints. Find what holds the port, or start with --port N.",
                file=sys.stderr,
            )
        return 2
    except OSError as problem:
        print(f"Cannot listen on 127.0.0.1: {problem.strerror or problem}", file=sys.stderr)
        return 2
    try:
        # Everything after the bind is inside the stop handling: a Ctrl-C or stop signal at any
        # point from here (registering DIR, `--yes` starting services) still stops every job.
        code = _run(args, center, server, instance)
    except KeyboardInterrupt:
        code = 0
    finally:
        _stopping.set()  # from here on a SIGTERM/SIGHUP is a no-op
        try:
            server.server_close()
            instance.withdraw()  # a second `up` no longer prints a URL nobody answers
        except KeyboardInterrupt:
            pass
        _shut_down(center)
    if code:
        return code
    _say(
        "Console stopped. Services and deployments keep running: use "
        "`agentbreeder services down` and `agentbreeder remove`."
    )
    return 0
