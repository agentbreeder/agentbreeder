"""Read-only access to a stack's agent database for CLI children (M12 spec delta §3.1, §3.5).

Shared by `tool log` (M12) and the other commands that read the agent's database from the host
(`knowledge status`, M14): which database the agent's `DATABASE_URL` binding points at, which
environment's deploy state holds the host-form secrets `services up` writes (`agent-database`,
`console-database`), how to connect read-only, and how a driver error becomes a fixed message.

- `load_stack(project)` and `resolve(stack, project, environment, prefix=...)` give a
  `StackDatabase` (environment, database name, state paths, the two URLs).
- `with read_session(psycopg, url, which, prefix) as db:` connects (session defaults
  `default_transaction_read_only=on`, `search_path=pg_catalog,pg_temp`, 5 s statement and 1 s
  lock timeouts), runs the body in one REPEATABLE READ READ ONLY transaction, rolls it back and
  closes. Every query must schema-qualify its tables. A driver error in the body is mapped.
- `mapped(error, which, psycopg, prefix)` turns a psycopg error into a `DbReadError` with a fixed
  message per SQLSTATE class: never psycopg's text, which can carry the DSN. Command-specific
  codes are `<prefix>_NO_ACCESS`, `<prefix>_NO_TABLES`, `<prefix>_TIMEOUT`, `<prefix>_FAILED`
  and `<prefix>_UNSUPPORTED`.
- `redaction(db)` lists the values to redact from customer data shown with `--content`.

Never fall back from one credential to the other: a login or permission error is a refusal.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from agentbreeder import safe_text, secret_files
from agentbreeder.redaction import redaction_values
from agentbreeder.services.operator import AGENT_SECRET, CONSOLE_SECRET, DATABASE_BINDING
from agentbreeder.services.refs import Reference
from agentbreeder.services.schema import ServiceError
from agentbreeder.services.state import StackState, written_environments

CONNECT_TIMEOUT = 5
# Session defaults, so even a statement outside `read_session`'s transaction is bounded.
OPTIONS = (
    "-c default_transaction_read_only=on -c search_path=pg_catalog,pg_temp "
    "-c statement_timeout=5000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=10000"
)
POSTGRES = "service/postgres-pgvector@"
CONSOLE_ROLE = "agentbreeder_console"
# Connection-time refusals that carry no SQLSTATE (libpq reports them as text only).
_LOGIN_TEXT = ("authentication failed", "pg_hba.conf", "permission denied")


class DbReadError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


@dataclass(frozen=True)
class StackDatabase:
    project: Path
    environment: str
    database: str  # the database the agent's DATABASE_URL binding points at
    directory: Path  # the stack's state directory
    deploy_state: Path  # <directory>/envs/<environment>/state
    # Host-form DSNs with passwords: kept out of repr, so a log line or a test diff of the
    # object never shows them.
    agent_url: str = field(repr=False)  # the agent's own credential
    console_url: str | None = field(repr=False)  # agentbreeder_console, with console_database


# -- the stack -----------------------------------------------------------------------------------


def load_stack(project: Path):
    from agentbreeder.services import engine

    try:
        return engine.load(project)
    except ServiceError as error:
        raise DbReadError(error.code, str(error)) from None


def database_name(stack, prefix: str) -> str:
    """The database the agent's `DATABASE_URL` binding points at: `postgres.databases.X` → X,
    `postgres.roles.R` → the service's `roles_database`; anything else `<prefix>_UNSUPPORTED`."""
    binding = stack.environment.bindings.get(DATABASE_BINDING)
    unsupported = DbReadError(
        f"{prefix}_UNSUPPORTED",
        "This reads the database bound as DATABASE_URL to a service/postgres-pgvector "
        "databases.NAME or roles.NAME output; this stack binds none",
    )
    if binding is None:
        raise unsupported
    try:
        reference = Reference.parse(binding.ref, DATABASE_BINDING)
    except ServiceError:
        raise unsupported from None
    service = stack.services.get(reference.service)
    if service is None or not service.ref.startswith(POSTGRES) or reference.item is None:
        raise unsupported
    if reference.output == "databases":
        return reference.item
    if reference.output == "roles":
        database = service.with_.get("roles_database")
        if isinstance(database, str):
            return database
    raise unsupported


def _environment(directory: Path | None, chosen: str | None, project: Path) -> str:
    written = written_environments(directory) if directory else []
    if not written:
        raise DbReadError(
            "SERVICES_DOWN",
            f"The stack in {project} has no state yet; start it with "
            "`agentbreeder services up --environment ENV`",
        )
    if chosen is None:
        if len(written) > 1:
            raise DbReadError(
                "ENVIRONMENT_AMBIGUOUS", f"pass --environment (one of {', '.join(written)})"
            )
        return written[0]
    if chosen not in written:
        raise DbReadError(
            "SERVICES_DOWN",
            f"The stack has no state for {safe_text.excerpt(chosen, 40)!r} (written: "
            f"{', '.join(written)}); run `agentbreeder services up --environment ENV`",
        )
    return chosen


def _secret(deploy_state: Path, name: str) -> str | None:
    try:
        return secret_files._read(deploy_state / "secrets" / name).decode()
    except secret_files.SecretError as error:
        if error.code == "SECRET_MISSING":
            return None
        raise DbReadError("SERVICES_STALE", f"{name}: {error}") from None
    except UnicodeDecodeError:
        raise DbReadError("SERVICES_STALE", f"{name} is not text") from None


def resolve(stack, project: Path, environment: str | None, *, prefix: str) -> StackDatabase:
    """The environment (the only one written, or `environment`), the database and the two
    host-form URLs. Creates nothing; a stack whose state predates the URLs is SERVICES_STALE."""
    database = database_name(stack, prefix)
    try:
        directory = StackState.locate(stack.name, project)
        chosen = _environment(directory, environment, project)
    except ServiceError as error:
        raise DbReadError(error.code, str(error)) from None
    deploy_state = directory / "envs" / chosen / "state"
    agent_url = _secret(deploy_state, AGENT_SECRET)
    if agent_url is None:
        raise DbReadError(
            "SERVICES_STALE",
            "The stack's state has no host-form agent-database secret; run "
            "`agentbreeder services up`",
        )
    console_url = _secret(deploy_state, CONSOLE_SECRET)
    return StackDatabase(
        project, chosen, database, directory, deploy_state, agent_url, console_url
    )


def redaction(db: StackDatabase) -> list:
    """The stack's secrets plus, explicitly, the two host-form URLs and their bare passwords."""
    values = list(redaction_values(db.deploy_state, db.directory))
    for url in (db.agent_url, db.console_url):
        if url:
            values.append(url)
            try:
                password = urlsplit(url).password
            except ValueError:
                password = None
            if password:
                values.append(unquote(password))
    return values


# -- the connection ------------------------------------------------------------------------------


def psycopg_module(command: str):
    try:
        import psycopg
    except ImportError as error:  # optional dependency: pip install "agentbreeder[eval]"
        raise DbReadError(
            "DEPENDENCY_MISSING", f'{command} needs psycopg: pip install "agentbreeder[eval]"'
        ) from error
    return psycopg


def _connect(psycopg, url: str, which: str, prefix: str):
    """`which` is `agent` or `console`, named in a refusal."""
    try:
        return psycopg.connect(
            url,
            connect_timeout=CONNECT_TIMEOUT,
            options=OPTIONS,
            application_name="agentbreeder-cli",
            autocommit=True,  # transactions are explicit: `_begin` sets their isolation
        )
    except psycopg.Error as error:
        raise mapped(error, which, psycopg, prefix) from None


def _begin(db) -> None:
    db.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    db.execute("SET LOCAL statement_timeout = '5s'")
    db.execute("SET LOCAL lock_timeout = '1s'")
    db.execute("SET LOCAL idle_in_transaction_session_timeout = '10s'")


@contextmanager
def read_session(
    psycopg,
    url: str,
    which: str,
    prefix: str,
    *,
    no_tables: str = "Nothing recorded yet",
    timeout: str = "try again or narrow the request",
) -> Iterator:
    """The only supported way to read: one REPEATABLE READ READ ONLY transaction on a new
    connection, rolled back and closed at the end. Callers must not COMMIT or END it. A psycopg
    error in the body becomes a `DbReadError` (`no_tables` and `timeout` word its message);
    other exceptions propagate unchanged. `which` is `agent` or `console`, named in a refusal."""
    db = _connect(psycopg, url, which, prefix)
    try:
        try:
            _begin(db)
            yield db
            db.execute("ROLLBACK")
        except psycopg.Error as error:
            raise mapped(error, which, psycopg, prefix, no_tables, timeout) from None
    finally:
        db.close()


def mapped(
    error,
    which: str,
    psycopg,
    prefix: str,
    no_tables: str = "Nothing recorded yet",
    timeout: str = "try again or narrow the request",
):
    """A fixed message per SQLSTATE class. `SERVICES_DOWN` only for a connection failure; a
    client-side error (a value psycopg cannot load, for example) is `<prefix>_FAILED`."""
    state = getattr(error, "sqlstate", None) or ""
    text = str(error).lower()
    operational = isinstance(error, psycopg.OperationalError)
    role_missing = text.startswith('role "') or ' role "' in text
    role_missing = role_missing and "does not exist" in text
    login = not state and operational and (role_missing or any(p in text for p in _LOGIN_TEXT))
    if state in ("28P01", "28000") or login:
        return DbReadError(
            "DB_LOGIN_FAILED",
            f"The {which} credential was refused; run `agentbreeder services up` to refresh "
            "the stack's credentials",
        )
    if state == "42501":
        if which == "console":
            return DbReadError(
                "CONSOLE_ROLE_NO_ACCESS",
                f"The database owner's init_sql has not granted {CONSOLE_ROLE} what this reads: "
                "add the grants (see the command's docs) and run "
                "`agentbreeder services up --update`",
            )
        return DbReadError(f"{prefix}_NO_ACCESS", "The agent's credential cannot read this")
    if state == "42P01":
        return DbReadError(f"{prefix}_NO_TABLES", no_tables)
    if state == "57014":
        return DbReadError(f"{prefix}_TIMEOUT", f"The query timed out; {timeout}")
    if state.startswith("08") or (not state and operational):
        return DbReadError(
            "SERVICES_DOWN",
            "The stack's database is not reachable; start it with `agentbreeder services up`",
        )
    if not state:
        return DbReadError(
            f"{prefix}_FAILED",
            f"A value from the database could not be read ({type(error).__name__})",
        )
    return DbReadError(f"{prefix}_FAILED", f"The database refused the query (SQLSTATE {state})")
