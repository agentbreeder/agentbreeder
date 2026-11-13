"""`agentbreeder invoke NAME MESSAGE|- [--candidate] [--customer ID] [--session ID] [--json]
[--state-dir DIR | --stack DIR [--environment ENV]]`: send one message to a local deployment
(see `agentbreeder.invoke`).

Exit 0 with the agent's reply. Exit 1 when the message was (or may have been) sent but no usable
reply came back: the agent answered with an error, the answer was invalid, or it timed out; a
retry is a second turn. Exit 2 when nothing was sent. Errors print `[CODE] message` on stderr,
and with `--json` also `{"schema_version": 1, "error": {"code", "message", "sent"}}` on stdout
(argparse usage errors stay plain text). Under `--json` a consumer branches on `error`: without
it, the answer came back and `status` says whether the agent answered 200 with a reply.
"""

from __future__ import annotations

import json
import re
import sys

from agentbreeder import cli, hints, safe_text
from agentbreeder.agentfile import NAME_PATTERN

NAMES = ("invoke",)
SCHEMA_VERSION = 1


def register(commands) -> None:
    command = cli.stateful_parser(
        commands, "invoke", "Send one message to a local deployment and print the reply"
    )
    command.add_argument("name", metavar="NAME", help="Agent name from agent.yaml")
    command.add_argument("message", metavar="MESSAGE", help="The message, or - to read stdin")
    command.add_argument(
        "--candidate",
        action="store_true",
        help="Call NAME-candidate (the release candidate) instead of production",
    )
    command.add_argument(
        "--customer", metavar="ID", help="customer_id to send (not authenticated)"
    )
    command.add_argument(
        "--session", metavar="ID", help="session_id to continue (not authenticated)"
    )
    cli.add_stack(command, environment=True)


def _where(args) -> hints.Where:
    """The --stack (and its environment) or non-default --state-dir this ran with, so printed
    follow-ups (`status`, `forward`, `next`) work as printed."""
    if args.stack is not None:
        return hints.Where(stack=args.stack, environment=args.environment)
    custom = args.state_dir if args.state_dir != cli.DEFAULT_STATE else None
    return hints.Where(state_dir=custom)


def _fail(args, error) -> int:
    print(str(error), file=sys.stderr)
    sent = bool(getattr(error, "sent", False))
    if args.json:
        message = str(error).removeprefix(f"[{error.code}] ")
        body = {"code": error.code, "message": message, "sent": sent}
        print(json.dumps({"schema_version": SCHEMA_VERSION, "error": body}, indent=2))
    return 1 if sent else 2


def run(args) -> int:
    from agentbreeder import invoke

    try:
        if not re.fullmatch(NAME_PATTERN, args.name):
            raise invoke.InvokeError("AGENT_NAME", "NAME must be an agent name from agent.yaml")
        invoke.check_id(args.customer, "--customer")
        invoke.check_id(args.session, "--session")
        message = invoke.message_from(args.message, sys.stdin)
        _check_state(args.state_dir)
    except invoke.InvokeError as error:
        return _fail(args, error)
    if args.customer is not None or args.session is not None:
        print(invoke.NOT_AUTHENTICATED, file=sys.stderr)
    try:
        store = _open(args.state_dir)
    except invoke.InvokeError as error:
        return _fail(args, error)
    try:
        return _invoke(store, args, message)
    finally:
        store.close()


def _check_state(state_dir) -> None:
    """NO_STATE when there is no state file, STATE_UNREADABLE when it cannot be looked at (a
    directory without search permission, another OS error): `Path.is_file` raises
    PermissionError on 3.11–3.13, which was a traceback. What `is_file` treats as "not a file"
    (the errnos `pathlib` ignores, a NUL in the path) stays NO_STATE. The reason is the errno's
    symbolic name, never the OS's message."""
    import errno
    import os
    import stat

    from agentbreeder import invoke

    shown = safe_text.excerpt(state_dir, 300)
    try:
        mode = os.stat(state_dir / "deployments.sqlite3").st_mode
    except ValueError:  # an embedded NUL, from a --stack config: as `is_file`, not a file
        mode = None
    except OSError as error:
        if error.errno not in (errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP):
            if isinstance(error, PermissionError):
                reason = "permission denied"
            else:
                reason = errno.errorcode.get(error.errno, "OS error")
            raise invoke.InvokeError(
                "STATE_UNREADABLE", f"cannot read the AgentBreeder state at {shown} ({reason})"
            ) from error
        mode = None
    if mode is None or not stat.S_ISREG(mode):
        raise invoke.InvokeError(
            "NO_STATE", f"no AgentBreeder state at {shown}; check --state-dir or --stack"
        )


def _open(state_dir):
    """The state, read-only, with every failure as a coded InvokeError (so `--json` callers get
    the error object, unlike `cli.run_stateful`'s text)."""
    import sqlite3

    from agentbreeder import invoke
    from agentbreeder.store import DeploymentError, StateBusy, StateNeedsMigration, Store

    try:
        return Store.open_readonly(state_dir)
    except StateNeedsMigration as error:
        code, message = "STATE_NEEDS_MIGRATION", str(error)
    except StateBusy as error:
        code, message = "STATE_BUSY", str(error)
    except DeploymentError as error:
        code, message = "STATE_UNREADABLE", str(error)
    except (OSError, sqlite3.Error):
        code, message = "STATE_UNREADABLE", "cannot read the local state"
    raise invoke.InvokeError(code, safe_text.excerpt(message, 400))


def _runtime():
    from agentbreeder.runtime import Docker

    return Docker()


def _invoke(store, args, message: str) -> int:
    import sqlite3

    from agentbreeder import invoke

    where = _where(args)
    try:
        try:  # both read the state (target: the row; verify: the recorded manifest)
            record = invoke.target(store, args.name, args.candidate, where)
            invoke.verify(_runtime(), store, record, where)
        except sqlite3.Error as error:
            raise invoke.InvokeError("STATE_UNREADABLE", "cannot read the local state") from error
        answer = invoke.call(record["endpoint"], message, args.customer, args.session)
    except invoke.InvokeError as error:
        return _fail(args, error)
    ok = answer.status == 200 and isinstance(answer.body.get("reply"), str)
    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "deployment": record["id"],
                    "name": record["name"],
                    "endpoint": record["endpoint"],
                    "status": answer.status,
                    "response": answer.body,
                },
                indent=2,
            )
        )
        return 0 if ok else 1
    if not ok:
        detail = answer.body.get("error", "no reply in the answer")
        print(
            f"[AGENT_ERROR] {record['name']} answered {answer.status}: "
            f"{safe_text.excerpt(detail, 300)}",
            file=sys.stderr,
        )
        return 1
    reply = answer.body["reply"].strip()
    print(safe_text.visible(reply), flush=True)  # before the stderr note
    session = answer.body.get("session_id")
    if isinstance(session, str) and invoke.ID.fullmatch(session):
        print(f"Session: {session} (continue with --session {session})", file=sys.stderr)
    return 0
