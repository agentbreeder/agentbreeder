"""What CrewAI writes outside the turn, confined (spec §8.5, §13.7, §13.8): its files under
`HOME` and its console output. No customer text outside `turns`, none in the logs.

F0 found CrewAI 1.15.34 writing, on every run and with `memory=False`:
- `<data dir>/<CREWAI_STORAGE_DIR>/latest_kickoff_task_outputs.db`, a SQLite file holding every
  task's inputs and output (the customer's message and the reply), shared by every session in
  the container. Its writer is the `Crew`'s `TaskOutputStorageHandler`; there is no switch.
- `<data dir>/crewai/credentials/secret.key`, a random Fernet key CrewAI's own platform login
  uses to encrypt its access token (`tokens.enc`). It holds no AgentBreeder secret and no
  customer text; it is made when CrewAI's trace listener asks whether the user is logged in to
  the CrewAI platform.

What this runtime does about them:
1. The adapter never builds a `Crew` (it runs `Agent.kickoff_async`), so the task-output writer
   is never constructed.
2. `confine()` also replaces the writer's behaviour, for any `Crew` this process might build:
   `TaskOutputStorageHandler` and `KickoffTaskOutputsSQLiteStorage` keep nothing and open no
   file. `check()` proves it at start-up; if CrewAI changed underneath, the runtime does not
   start.
3. `sweep()` deletes the task-output database if it exists, after every turn, and at start-up
   (a Kubernetes emptyDir survives a container restart) also the credentials directory and any
   `/tmp/crewai-tool-results-*` left by CrewAI's "better compaction" (off in the adapter). With
   (1) and (2) neither is made on this runtime's path; a runtime test runs turns with a fresh
   `HOME` and finds no file holding the turn's text, no database and no key.
4. The entry point turns CrewAI's telemetry, tracking and trace upload off before import, and
   fixes `CREWAI_STORAGE_DIR`, so the paths do not depend on the working directory.

The console: CrewAI's global event listener builds its console formatter with `verbose=True`
whatever the agent says, so every agent run printed panels to stdout (the container's log) with
the backstory (the customer's notes), each tool's arguments and output, and the answer.
CrewAI also prints through module-level `rich` consoles (an event-pairing warning, context
summary panels). `quiet_console()` makes every `ConsoleFormatter` non-verbose and silent,
CrewAI's shared `Printer` print nothing, and `rich` console output go nowhere in this process
(the kit itself never uses `rich`; it prints its own one-line, redacted log lines). CrewAI's
`logging` loggers stay, quieted by the kit (`logs.quiet`).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

TASK_OUTPUTS = "latest_kickoff_task_outputs.db"
# CrewAI's deferred tool results ("better compaction", off in the adapter), hard-coded under /tmp.
TOOL_RESULTS = "crewai-tool-results-*"
STORAGE_DIR = "agentbreeder"


def _data_dir() -> Path:
    """Where CrewAI's storage lives, computed as `crewai_core.paths` does, without creating it."""
    import appdirs

    return Path(appdirs.user_data_dir(os.environ.get("CREWAI_STORAGE_DIR", STORAGE_DIR), "CrewAI"))


def credentials_dir() -> Path:
    """`crewai_core.token_manager.TokenManager._get_secure_storage_path()`, without creating it
    (a runtime test checks the two agree)."""
    if sys.platform == "win32":  # pragma: no cover - the image is Linux
        base = os.environ.get("LOCALAPPDATA", "")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.path.expanduser("~/.local/share")
    return Path(base) / "crewai" / "credentials"


def task_outputs_path() -> Path:
    return _data_dir() / TASK_OUTPUTS


class _NoTaskOutputs:
    """What `TaskOutputStorageHandler` becomes: it keeps nothing."""

    def __init__(self, *args, **kwargs) -> None:
        self.storage = None

    def update(self, task_index, log) -> None:
        return None

    def add(self, *args, **kwargs) -> None:
        return None

    def reset(self) -> None:
        return None

    def load(self) -> list:
        return []


class _NoSQLite:
    """What `KickoffTaskOutputsSQLiteStorage` becomes: no file, nothing kept."""

    def __init__(self, db_path=None) -> None:
        self.db_path = None

    def add(self, *args, **kwargs) -> None:
        return None

    def update(self, *args, **kwargs) -> None:
        return None

    def load(self) -> list:
        return []

    def delete_all(self) -> None:
        return None


def _replace(target: type, source: type) -> None:
    for name in ("__init__", "update", "add", "reset", "load", "delete_all"):
        if name in vars(source):
            setattr(target, name, vars(source)[name])


def quiet_console() -> None:
    """No CrewAI console output: no panels, no printed messages."""
    from crewai.events.utils.console_formatter import ConsoleFormatter
    from crewai_core.printer import Printer
    from rich.console import Console

    def silent(self, *args, **kwargs) -> None:
        return None

    # A property, so the formatter's own `self.verbose = True` (and any later assignment) is a
    # no-op; every handler returns early when it is False.
    ConsoleFormatter.verbose = property(lambda self: False, lambda self, value: None)
    ConsoleFormatter.print = silent
    Printer.print = silent
    for name in ("print", "log", "rule", "print_json", "out"):
        setattr(Console, name, silent)


def confine() -> None:
    """Make CrewAI's task-output store keep nothing and its console silent, then sweep what an
    earlier run left."""
    quiet_console()
    from crewai.memory.storage import kickoff_task_outputs_storage as sqlite_storage
    from crewai.utilities import task_output_storage_handler as handler

    _replace(handler.TaskOutputStorageHandler, _NoTaskOutputs)
    _replace(sqlite_storage.KickoffTaskOutputsSQLiteStorage, _NoSQLite)
    check()
    sweep(credentials=True)


def check() -> None:
    """Fail start-up unless the store is off and the console is quiet."""
    from crewai.crew import Crew
    from crewai.events.event_listener import EventListener
    from crewai.execution import _tracing_disabled
    from crewai_core.printer import PRINTER
    from crewai_core.telemetry import Telemetry

    if not _tracing_disabled(None) or not Telemetry._is_telemetry_disabled():
        raise RuntimeError("CrewAI's tracing or telemetry is still on")
    if EventListener().formatter.verbose is not False:
        raise RuntimeError("CrewAI's console formatter is still verbose")
    from rich.console import Console

    if type(PRINTER).print.__name__ != "silent" or Console.print.__name__ != "silent":
        raise RuntimeError("CrewAI's printer or rich's console still prints")
    from crewai.memory.storage.kickoff_task_outputs_storage import (
        KickoffTaskOutputsSQLiteStorage,
    )

    factory = Crew.__private_attributes__["_task_output_handler"].default_factory
    handler = factory()
    if handler.storage is not None or handler.load() != []:
        raise RuntimeError("CrewAI's task-output store is still on")
    if KickoffTaskOutputsSQLiteStorage().db_path is not None:
        raise RuntimeError("CrewAI's task-output SQLite storage is still on")


def sweep(credentials: bool = False) -> list[str]:
    """Delete CrewAI's task-output database (and, at start-up, its credentials directory); the
    names of what was removed (for tests; never logged with any content)."""
    removed = []
    database = task_outputs_path()
    for path in (
        database,
        *(database.with_name(database.name + s) for s in ("-journal", "-wal", "-shm")),
    ):
        if path.exists():
            path.unlink()
            removed.append(path.name)
    directory = credentials_dir()
    if credentials and directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
        removed.append("credentials")
    if credentials:
        for leftover in Path("/tmp").glob(TOOL_RESULTS):  # noqa: S108 - CrewAI's fixed path
            shutil.rmtree(leftover, ignore_errors=True)
            removed.append("tool-results")
    return removed
