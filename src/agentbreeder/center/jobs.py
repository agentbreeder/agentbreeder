"""Center state and the job runner.

A job is the CLI in a child process: `python -I -m agentbreeder <fixed argv>`, so the UI cannot
drift from it or skip the gate. `-I` and a working directory outside the project keep a project's
`yaml.py` from being imported on the host. Argv is built here from a registered project and values
checked against fixed patterns, never from free text. Engine jobs run one at a time per project;
the jobs that touch the local cluster also run one at a time on the machine (`lock_keys`).
"""

from __future__ import annotations

import base64
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from agentbreeder.center.files import BadRequest

__all__ = [
    "BadRequest",
    "Busy",
    "WriteBusy",
    "CenterState",
    "Runner",
    "argv_for",
    "cli_error",
    "redact",
]

MAX_LINE = 4000
MAX_LINES = 20000
KEEP_JOBS = 100
ENVIRONMENT = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")
DEPLOYMENT = re.compile(r"^[0-9a-f]{32}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
KEY_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
CODED = re.compile(r"^\[([A-Z][A-Z0-9_]{2,39})\] (.+)$")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]")
MAX_MESSAGE = 300
EXIT_APPROVAL = 3
KINDS = frozenset(
    {
        "services-up",
        "services-down",
        "build",
        "release",
        "rollback",
        "cluster-up",
        "cluster-down",
        "promote",
        "remove",
        "reconcile",
    }
)
_ALLOWED_ENV = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "TMPDIR",
    "TERM",
    "SHELL",
    "SSL_CERT_FILE",
}
_ALLOWED_PREFIXES = ("LC_", "AGENTBREEDER_")
_ALLOWED_EXACT_DOCKER = {"DOCKER_CONTEXT"}


class Busy(Exception):
    def __init__(self, job: str):
        super().__init__(job)
        self.job = job


def child_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key in _ALLOWED_ENV or key in _ALLOWED_EXACT_DOCKER or key.startswith(_ALLOWED_PREFIXES)
    }


def error(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def cli_error(stderr: str, returncode: int) -> dict:
    """S-17: only the CLI's coded `[CODE] message` line reaches the browser, never free stderr
    (which can carry paths, tracebacks or a third party's text)."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    match = CODED.match(lines[-1]) if lines else None  # the CLI prints its error last
    if match:
        return error(match[1], CONTROL.sub("", match[2])[:MAX_MESSAGE])
    return error("COMMAND_FAILED", f"The command failed (exit {returncode}); run it in a terminal")


def _signal(process: subprocess.Popen, number: int) -> None:
    try:
        process.send_signal(number)
    except (ProcessLookupError, OSError):
        pass


def _signal_group(process: subprocess.Popen, number: int) -> None:
    """The child's whole process group: jobs start with `start_new_session=True`, so the group
    id is the child's pid and holds its docker/kubectl grandchildren."""
    try:
        os.killpg(process.pid, number)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _wait_all(processes: Sequence[subprocess.Popen], deadline: float) -> None:
    """Wait for every process until one shared deadline."""
    for process in processes:
        try:
            process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass


def redact(text: str, values: Sequence[str]) -> str:
    """Best effort: the value and its base64 and URL-encoded forms. Echoes in other encodings
    or truncated are not caught; the docs say so."""
    for value in values:
        for form in (
            value,
            base64.b64encode(value.encode()).decode(),
            quote(value, safe=""),
        ):
            if form:
                text = text.replace(form, "***")
    return text


def _dotenv_quote(value: str) -> str:
    """Quote for env_import's parser, which takes one plain pair of quotes."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    raise BadRequest("A key value cannot contain both quote characters")


class CenterState:
    """Projects, jobs and job logs. Never holds a secret."""

    def __init__(self, directory: Path):
        self.db = sqlite3.connect(
            directory / "center.sqlite3", check_same_thread=False, isolation_level=None
        )
        self.lock = threading.Lock()
        (directory / "center.sqlite3").chmod(0o600)
        with self.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL,
                    environment TEXT NOT NULL, accepted_grant TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL,
                    state TEXT NOT NULL, exit_code INTEGER, created REAL NOT NULL,
                    finished REAL);
                CREATE TABLE IF NOT EXISTS lines (
                    job TEXT NOT NULL, seq INTEGER NOT NULL, text TEXT NOT NULL,
                    PRIMARY KEY (job, seq));
                """
            )
            self.db.execute(
                "UPDATE jobs SET state='interrupted', finished=? WHERE state='running'",
                (time.time(),),
            )

    def _rows(self, sql: str, *parameters) -> list[dict]:
        with self.lock:
            cursor = self.db.execute(sql, parameters)
            names = [column[0] for column in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def _write(self, sql: str, *parameters) -> None:
        with self.lock:
            self.db.execute(sql, parameters)

    def add_project(self, identifier, name, path, environment) -> dict:
        self._write(
            "INSERT OR IGNORE INTO projects (id,name,path,environment,created) VALUES (?,?,?,?,?)",
            identifier,
            name,
            path,
            environment,
            time.time(),
        )
        return self.project(identifier)

    def project(self, identifier: str) -> dict | None:
        rows = self._rows("SELECT * FROM projects WHERE id=?", identifier)
        return rows[0] if rows else None

    def projects(self) -> list[dict]:
        return self._rows("SELECT * FROM projects ORDER BY created")

    def accept_grant(self, identifier: str, text: str) -> None:
        self._write("UPDATE projects SET accepted_grant=? WHERE id=?", text, identifier)

    def create_job(self, identifier: str, project: str, kind: str) -> None:
        self._write(
            "INSERT INTO jobs (id,project,kind,state,created) VALUES (?,?,?,'running',?)",
            identifier,
            project,
            kind,
            time.time(),
        )
        stale = self._rows(
            "SELECT id FROM jobs WHERE state!='running' ORDER BY created DESC LIMIT -1 OFFSET ?",
            KEEP_JOBS,
        )
        for row in stale:
            self._write("DELETE FROM lines WHERE job=?", row["id"])
            self._write("DELETE FROM jobs WHERE id=?", row["id"])

    def finish_job(self, identifier: str, state: str, code: int | None) -> None:
        self._write(
            "UPDATE jobs SET state=?, exit_code=?, finished=? WHERE id=?",
            state,
            code,
            time.time(),
            identifier,
        )

    def append(self, job: str, seq: int, text: str) -> None:
        self._write("INSERT INTO lines (job,seq,text) VALUES (?,?,?)", job, seq, text[:MAX_LINE])

    def job(self, identifier: str, after: int = 0) -> dict | None:
        rows = self._rows("SELECT * FROM jobs WHERE id=?", identifier)
        if not rows:
            return None
        lines = self._rows(
            "SELECT seq,text FROM lines WHERE job=? AND seq>? ORDER BY seq", identifier, after
        )
        return {**rows[0], "lines": lines}

    def recent_jobs(self, limit: int = 20) -> list[dict]:
        return self._rows("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", limit)

    def latest_jobs(self, project: str) -> dict[str, dict]:
        """The most recent job of each kind for one project."""
        latest: dict[str, dict] = {}
        for row in self._rows("SELECT * FROM jobs WHERE project=? ORDER BY created", project):
            latest[row["kind"]] = row
        return latest


def argv_for(kind: str, project: dict, options: dict) -> list[str]:
    path, environment = project["path"], project["environment"]
    stack = ["--stack", path, "--environment", environment]
    agent = str(Path(path) / "agent.yaml")
    if kind == "services-up":
        digest = options.get("digest", "")
        if not DIGEST.fullmatch(str(digest)):
            raise BadRequest("Review the grant first")
        argv = [
            "services",
            "up",
            path,
            "--environment",
            environment,
            "--yes",
            "--expect-grant",
            digest,
        ]
        return [*argv, "--env-file", options["env_file"]] if options.get("env_file") else argv
    if kind == "services-down":
        return ["services", "down", path]
    if kind == "build":
        return ["build", agent, *stack, "--locked"]
    if kind == "release":
        return ["release", agent, *stack]
    if kind == "rollback":
        return ["release", agent, *stack, "--rollback"]
    if kind == "cluster-up":
        # No --yes: the grant change goes through the reviewed services-up path.
        return ["cluster", "up", "--stack", path, "--environment", environment]
    if kind == "cluster-down":
        return ["cluster", "down", "--stack", path]
    if kind == "promote":
        return ["promote", agent, *stack, "--to", "kubernetes"]
    if kind == "remove":
        identifier = str(options.get("deployment", ""))
        where = options.get("target")
        if not DEPLOYMENT.fullmatch(identifier) or where not in ("local", "kubernetes"):
            raise BadRequest("Unknown deployment")
        return ["remove", identifier, "--local" if where == "local" else "--cluster", *stack]
    if kind == "reconcile":
        return ["reconcile", "--local", *stack]
    raise BadRequest("Unknown job kind")


MACHINE = "machine"
POLICY = "policy-save"
# Engine jobs that touch the one local kind cluster serialise machine-wide; every engine job also
# holds its project. `remove` of a cluster deployment holds only its project: the CLI refuses
# `cluster down` while anything is deployed there. Console locks only order the console's own
# jobs: a terminal command does not see them, and correctness comes from the CLI's own file locks
# (spec §10, S-11).
MACHINE_KINDS = frozenset({"cluster-up", "cluster-down", "promote"})
# Short writes (later milestones: registry publish/import, `agent set-ref`, memory edits, policy
# save): a synchronous CLI child with a per-target lock. Registry and memory writes are never
# blocked by engine jobs; `agent set-ref` also holds its project (it rewrites agent.yaml), and a
# policy save excludes every engine job in both directions.
SHORT_TIMEOUT = 60
SHORT_TIMEOUTS = {"tool-publish": 300}  # publishing a tool re-locks its dependencies
SHUTDOWN_GRACE = 5.0


class WriteBusy(Exception):
    """A short write, or a job during a policy save, was refused: no job to follow.
    `reason` is "job" (an engine job holds what it needs) or "saving" (a save is running)."""

    def __init__(self, key: str, reason: str):
        super().__init__(key)
        self.key, self.reason = key, reason


def _stop_process(process: subprocess.Popen, grace: float | None = None) -> None:
    """Stop a child and its process group without waiting forever: SIGTERM, then SIGKILL."""
    grace = SHUTDOWN_GRACE if grace is None else grace
    _signal_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        _signal_group(process, signal.SIGKILL)
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass


def _stop_gracefully(process: subprocess.Popen, grace: float | None = None) -> None:
    """The Cancel path first (one SIGINT, so a replace step restores), then `_stop_process`."""
    grace = SHUTDOWN_GRACE if grace is None else grace
    _signal(process, signal.SIGINT)
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    _stop_process(process, grace)


def lock_keys(kind: str, project: dict) -> tuple[str, ...]:
    project_key = f"project:{project['id']}"
    return (MACHINE, project_key) if kind in MACHINE_KINDS else (project_key,)


@dataclass
class Running:
    id: str
    project: str
    kind: str
    process: subprocess.Popen
    keys: tuple[str, ...]
    pump: threading.Thread | None = None


class Runner:
    def __init__(self, state: CenterState, directory: Path, prefix: Sequence[str] | None = None):
        self.state = state
        self.directory = directory
        self.prefix = list(prefix or [sys.executable, "-I", "-m", "agentbreeder"])
        self.mutex = threading.Lock()
        self.jobs: dict[str, Running] = {}
        self.held: dict[str, str] = {}  # lock key -> the job or short write holding it
        self.children: set[subprocess.Popen] = set()  # long-lived children (follows, watch, usage)
        self.cache: dict[str, dict] = {}
        self.stopping = False

    # -- engine jobs -----------------------------------------------------------------------------

    def running(self) -> list[dict]:
        """Running engine jobs, oldest first."""
        with self.mutex:
            return [
                {"id": job.id, "project": job.project, "kind": job.kind}
                for job in self.jobs.values()
            ]

    def _claim(self, keys: Sequence[str], holder: str, write: bool = False) -> None:
        """Take every key or none (call with the mutex held). Busy names the job holding the
        first taken key; a short write gets WriteBusy, which names none."""
        for key in keys:
            owner = self.held.get(key)
            if owner is not None:
                if owner.startswith("short:"):
                    raise WriteBusy(key, "saving")
                if write:
                    raise WriteBusy(key, "job")
                raise Busy(owner)
        for key in keys:
            self.held[key] = holder

    def _free(self, holder: str) -> None:
        for key in [key for key, value in self.held.items() if value == holder]:
            del self.held[key]

    def _policy_saving(self) -> bool:
        return any(key.startswith(f"{POLICY}:") for key in self.held)

    def start(
        self,
        project: dict,
        kind: str,
        options: dict,
        keys: dict[str, str] | None = None,
        on_success: Callable[[], None] | None = None,
    ) -> str:
        if kind not in KINDS:
            raise BadRequest("Unknown job kind")
        options = dict(options)
        locks = lock_keys(kind, project)
        with self.mutex:
            if self.stopping:
                raise BadRequest("The console is stopping", "STOPPING")
            if self._policy_saving():
                raise WriteBusy(POLICY, "saving")
            identifier = uuid.uuid4().hex
            self._claim(locks, identifier)
            env_file = process = failure = None
            try:
                env_file = self._env_file(identifier, keys) if keys else None
                if env_file:
                    options["env_file"] = str(env_file)
                argv = argv_for(kind, project, options)
                process = subprocess.Popen(
                    [*self.prefix, *argv],
                    cwd=self.directory,
                    env=child_environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                    start_new_session=True,  # a terminal Ctrl-C must not reach the job
                )
                self.state.create_job(identifier, project["id"], kind)
            except BaseException as problem:
                self._free(identifier)
                failure = problem
            else:
                self.jobs[identifier] = Running(identifier, project["id"], kind, process, locks)
        if failure is not None:
            # Outside the mutex: a child that was started but not recorded is stopped, never
            # orphaned, and a slow stop does not block every other request.
            if process is not None:
                if process.stdout:
                    process.stdout.close()
                _stop_process(process)
            if env_file:
                env_file.unlink(missing_ok=True)
            raise failure
        pump = threading.Thread(
            target=self._pump,
            args=(identifier, process, env_file, list((keys or {}).values()), on_success),
            daemon=True,
        )
        with self.mutex:
            if identifier in self.jobs:
                self.jobs[identifier].pump = pump
        pump.start()
        return identifier

    def _env_file(self, identifier: str, keys: dict[str, str]) -> Path:
        for name, value in keys.items():
            if not KEY_NAME.fullmatch(name) or not value or re.search(r"[\r\n]", value):
                raise BadRequest("Invalid key name or value")
        path = self.directory / f"env-{identifier}"
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.writelines(f"{name}={_dotenv_quote(value)}\n" for name, value in keys.items())
        return path

    def _pump(self, identifier, process, env_file, values, on_success) -> None:
        seq = 0
        code = None
        unrecorded = False
        try:
            for line in process.stdout:  # always drained, so the child never blocks on a pipe
                seq += 1
                if unrecorded or seq > MAX_LINES:
                    continue
                try:
                    self.state.append(identifier, seq, redact(line.rstrip("\n"), values))
                except Exception:
                    # The log cannot be recorded (disk full, database error): a job nobody can
                    # see must not run on, but it stops the graceful way, as Cancel does.
                    unrecorded = True
                    threading.Thread(target=_stop_gracefully, args=(process,), daemon=True).start()
            code = process.wait()
        except Exception:
            _stop_process(process)
            code = None
        finally:
            if env_file:
                try:
                    env_file.unlink(missing_ok=True)
                except OSError:
                    pass  # removed at the next start (S-16); the locks must still be freed
            self.invalidate()
            with self.mutex:
                self.jobs.pop(identifier, None)
                self._free(identifier)
        # The exit code decides: a job that finished (0) despite its log failing did its work
        # (services up, a release); one stopped because of it exits non-zero and is failed.
        state = (
            "succeeded" if code == 0 else "waiting_approval" if code == EXIT_APPROVAL else "failed"
        )
        for _attempt in range(2):
            try:
                self.state.finish_job(identifier, state, code)
                break
            except Exception:
                time.sleep(0.2)  # a second failure leaves it to the next start (interrupted)
        if state == "succeeded" and on_success:
            on_success()

    def cancel(self, identifier: str) -> bool:
        """One SIGINT to the CLI child only: `release` and `promote` finish any replace step and
        restore on it (S-18 keeps this graceful path)."""
        with self.mutex:
            job = self.jobs.get(identifier)
            if job is None:
                return False
            job.process.send_signal(signal.SIGINT)
            return True

    def any_engine_job(self) -> bool:
        with self.mutex:
            return bool(self.jobs)

    # -- short writes ----------------------------------------------------------------------------

    def short_write(
        self,
        kind: str,
        target: str,
        argv: Sequence[str],
        timeout: int | None = None,
        project: dict | None = None,
    ) -> dict:
        """A short write as a synchronous CLI child, locked per target (`kind:target`), plus
        its project for writes that change a project's files (`agent set-ref`). A policy save
        is refused while any engine job runs, and no engine job starts while one is saving, so
        a job never reads a half-changed policy. Contention raises WriteBusy."""
        keys = [f"{kind}:{target}"]
        if project is not None:
            keys.append(f"project:{project['id']}")
        holder = f"short:{uuid.uuid4().hex}"
        with self.mutex:
            if self.stopping:
                raise BadRequest("The console is stopping", "STOPPING")
            if kind == POLICY and self.jobs:
                raise WriteBusy(POLICY, "job")
            self._claim(keys, holder, write=True)
        try:
            return self.read(
                argv,
                timeout=timeout or SHORT_TIMEOUTS.get(kind, SHORT_TIMEOUT),
                key=f"{keys[0]} {uuid.uuid4().hex}",  # writes are never served from the cache
            )
        finally:
            self.invalidate()
            with self.mutex:
                self._free(holder)

    # -- long-lived children and shutdown ---------------------------------------------------------

    def adopt(self, process: subprocess.Popen) -> None:
        """Track a long-lived child (log follow, watch, usage) so shutdown stops it too. It must
        be started with `start_new_session=True`, so it leads its own process group."""
        with self.mutex:
            self.children = {child for child in self.children if child.poll() is None}
            self.children.add(process)

    def forget(self, process: subprocess.Popen) -> None:
        with self.mutex:
            self.children.discard(process)

    def shutdown(self, grace: float = SHUTDOWN_GRACE) -> list[str]:
        """Stop every engine job and long-lived child (S-18). First SIGINT to each CLI child
        (the same graceful step as Cancel); after `grace` seconds, SIGTERM to every child's
        process group, whether or not the child itself has exited, so the docker, kubectl or
        kind processes it started stop too. A second Ctrl-C skips the wait. Reads and short
        writes are not stopped: they end or time out on their own (at most 300 s). Returns the
        ids of the jobs that were running."""
        with self.mutex:
            self.stopping = True
            jobs = list(self.jobs.values())
            # Only live adopted children: a long-ended one's pid may belong to someone else now.
            # Jobs keep the group signal even after their CLI exits (their pump holds them).
            live = [child for child in self.children if child.poll() is None]
            processes = [job.process for job in jobs] + live
        for process in processes:
            _signal(process, signal.SIGINT)
        try:
            _wait_all(processes, time.monotonic() + grace)
        except KeyboardInterrupt:
            pass  # asked twice: go straight to SIGTERM
        for process in processes:
            _signal_group(process, signal.SIGTERM)
        try:
            # One deadline for the SIGTERM wait and for each pump to record how its job ended:
            # shutdown takes at most two grace periods.
            deadline = time.monotonic() + grace
            _wait_all(processes, deadline)
            for job in jobs:
                if job.pump is not None:
                    job.pump.join(timeout=max(0.0, deadline - time.monotonic()))
        except KeyboardInterrupt:
            pass
        return [job.id for job in jobs]

    def terminate_all(self) -> None:
        """The last resort when shutdown itself keeps being interrupted: SIGTERM to every job's
        and live child's process group, no wait."""
        with self.mutex:
            self.stopping = True
            processes = [job.process for job in self.jobs.values()]
            processes += [child for child in self.children if child.poll() is None]
        for process in processes:
            _signal_group(process, signal.SIGTERM)

    def invalidate(self) -> None:
        """A job or write ended: cached reads are no longer the last good answer."""
        with self.mutex:
            self.cache.clear()

    def read(self, argv: Sequence[str], timeout: int = 30, key: str | None = None) -> dict:
        """Run a read-only command that prints JSON; on failure, keep the last good answer."""
        cache_key = key or " ".join(argv)
        try:
            result = subprocess.run(
                [*self.prefix, *argv],
                cwd=self.directory,
                env=child_environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return self._stale(cache_key, error("TIMEOUT", "The command timed out"))
        except OSError:
            return self._stale(cache_key, error("COMMAND_FAILED", "The command could not start"))
        if result.returncode:
            return self._stale(cache_key, cli_error(result.stderr, result.returncode))
        try:
            answer = {"data": json.loads(result.stdout), "error": None, "stale": False}
        except ValueError:
            return self._stale(
                cache_key, error("BAD_OUTPUT", "The command returned unreadable output")
            )
        self.cache[cache_key] = answer
        return answer

    def read_text(self, argv: Sequence[str], timeout: int = 30) -> str | None:
        """Run a read-only command that prints text; None on failure."""
        try:
            result = subprocess.run(
                [*self.prefix, *argv],
                cwd=self.directory,
                env=child_environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout if result.returncode == 0 else None

    def _stale(self, key: str, problem: dict) -> dict:
        """The `{data, error, stale}` envelope: the last good data, marked stale, if any."""
        previous = self.cache.get(key)
        if previous:
            return {"data": previous["data"], "error": problem, "stale": True}
        return {"data": None, "error": problem, "stale": False}
