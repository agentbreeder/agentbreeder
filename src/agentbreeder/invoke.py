"""`agentbreeder invoke NAME MESSAGE|-`: send one message to a local deployment (guided §9.2 #7).

Only a ready local deployment recorded in state is called, and only at
`http://127.0.0.1:<port>`: a Kubernetes row is refused (use `forward`). Immediately before
sending, Docker is asked again for the container: its ownership labels must match the record
(`Docker.inspect`) and it must still publish that same loopback port (`Docker.endpoint`), so a
port another process took over after the container stopped is not sent the message. A process
that grabs the port in the milliseconds between that check and the request is not excluded
(the destination is still loopback). The request goes through an opener with no proxies
(`ProxyHandler({})`) and no redirects, and the whole exchange has a 180 s deadline (not per
socket read: a slow-drip answer cannot hold the command longer).

The reply is the agent's (model) text: untrusted. The CLI prints it with controls shown as
visible escapes (`safe_text.visible`); `--json` escapes by construction. Malformed HTTP, deeply
nested or oversized JSON end as `RESPONSE_INVALID`, never a traceback. `--customer` and
`--session` are sent as given; the runtime does not authenticate them (#780). A message given
as an argument is visible to other local users in `ps`; `-` reads it from stdin instead.
"""

from __future__ import annotations

import http.client
import json
import re
import threading
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

TIMEOUT = 180
MAX_MESSAGE = 4000  # the ADK runtime's own limit (characters)
MAX_STDIN = 64 * 1024  # bytes read from `-`
MAX_RESPONSE = 1024 * 1024
ENDPOINT = re.compile(r"http://127\.0\.0\.1:([0-9]{1,5})")
ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # the runtime's session and customer id rule
NOT_AUTHENTICATED = (
    "Note: --customer and --session are not authenticated; anyone who can reach the port can "
    "claim any ID (#780)"
)


class InvokeError(ValueError):
    """`sent` is True when the message may have reached the agent (it may have run the turn):
    the CLI exits 1 for those and 2 only when nothing was sent, so a caller knows a retry is a
    second turn."""

    def __init__(self, code: str, message: str, *, sent: bool = False):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.sent = sent


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise InvokeError(
            "REDIRECT_REFUSED", f"the agent answered with a redirect ({code})", sent=True
        )


_OPENER = build_opener(ProxyHandler({}), _NoRedirect())


@dataclass(frozen=True)
class Answer:
    status: int  # HTTP status
    body: dict  # the agent's JSON object


def _shown(value: object) -> str:
    """A state value (an ID, a state name) for a message: one line, no controls."""
    from agentbreeder import safe_text

    return safe_text.excerpt(value, 80)


def message_from(text: str, stdin) -> str:
    """The message from argv, or from stdin for `-` (UTF-8, at most MAX_STDIN bytes)."""
    if text == "-":
        data = stdin.buffer.read(MAX_STDIN + 1) if hasattr(stdin, "buffer") else stdin.read()
        if isinstance(data, str):
            data = data.encode()
        if len(data) > MAX_STDIN:
            raise InvokeError("MESSAGE_TOO_LONG", f"stdin holds more than {MAX_STDIN} bytes")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise InvokeError("MESSAGE_INVALID", "stdin is not UTF-8") from error
    text = text.strip()
    if not text:
        raise InvokeError("MESSAGE_INVALID", "the message is empty")
    if len(text) > MAX_MESSAGE:
        raise InvokeError("MESSAGE_TOO_LONG", f"messages are limited to {MAX_MESSAGE} characters")
    return text


def check_id(value: str | None, flag: str) -> str | None:
    if value is not None and not ID.fullmatch(value):
        raise InvokeError("ID_INVALID", f"{flag} must be 1-64 of A-Z, a-z, 0-9, _ and -")
    return value


def _next_command(where) -> str:
    """`next` takes the project as DIR (not --stack) and --environment."""
    from agentbreeder import hints

    if where is not None and where.stack is not None:
        return hints.command("next", where.stack, "--environment", where.environment or "dev")
    return hints.command("next")


def target(store, name: str, candidate: bool, where=None) -> dict:
    """The ready local deployment of `name` (or `name-candidate`) recorded in `store`. `where`
    (a `hints.Where`) makes printed follow-ups carry the same --stack or --state-dir."""
    from agentbreeder import hints

    where = where or hints.Where()
    wanted = f"{name}-candidate" if candidate else name
    record = store.active(wanted, "local")
    if record is None:
        cluster = store.active(wanted, "kubernetes")
        if cluster is not None:
            forward = where.command("forward", _shown(cluster["id"]))
            raise InvokeError(
                "KUBERNETES_TARGET",
                f"{wanted} runs on Kubernetes; open a port with `{forward}` and call it there",
            )
        hint = (
            "; a candidate exists only while `release` runs or after a candidate deploy"
            if candidate
            else f"; release it first (`{_next_command(where)}` shows the step)"
        )
        raise InvokeError("NOT_DEPLOYED", f"no local deployment of {wanted} in this state{hint}")
    if record["state"] != "ready":
        raise InvokeError(
            "NOT_READY",
            f"{wanted} ({_shown(record['id'])}) is {_shown(record['state'])}, not ready",
        )
    if not isinstance(record["endpoint"], str) or not _port(record["endpoint"]):
        raise InvokeError("ENDPOINT_REFUSED", "only http://127.0.0.1:<port> endpoints are called")
    return record


def _port(endpoint: str) -> int | None:
    match = ENDPOINT.fullmatch(endpoint)
    port = int(match.group(1)) if match else 0
    return port if 1 <= port <= 65535 else None


def verify(runtime, store, record: dict, where=None) -> None:
    """The container still exists, is the one recorded, and still owns the recorded port."""
    from pydantic import ValidationError

    from agentbreeder import hints
    from agentbreeder.manifest import Manifest
    from agentbreeder.store import DeploymentError

    where = where or hints.Where()
    shown = _shown(record["id"])
    try:
        runtime.authorize(record["context"])
    except DeploymentError as error:  # our own message: DOCKER_HOST, context, engine down
        raise InvokeError("DOCKER_UNAVAILABLE", str(error)) from error
    try:
        info = runtime.inspect(record)
    except DeploymentError as error:  # labels differ, or the inspect answer is unreadable
        raise InvokeError(
            "CONTAINER_MISMATCH", f"the container of {shown} is not the recorded one: {error}"
        ) from error
    if info is None:
        raise InvokeError("CONTAINER_GONE", f"the container of {shown} no longer exists")
    try:
        manifest = Manifest.model_validate(store.manifest(record["id"]))
    except (ValidationError, ValueError) as error:
        raise InvokeError(
            "STATE_INVALID", f"the recorded manifest of {shown} is invalid"
        ) from error
    try:
        live = runtime.endpoint(info, manifest)
    except DeploymentError as error:
        status = where.command("status", shown)
        raise InvokeError(
            "CONTAINER_STOPPED",
            f"the container of {shown} is not running with its loopback port; "
            f"`{status}` shows the recorded state",
        ) from error
    if live != record["endpoint"]:
        raise InvokeError(
            "PORT_CHANGED", f"{shown} now publishes {live}, not the recorded endpoint"
        )


def call(
    endpoint: str,
    message: str,
    customer: str | None = None,
    session: str | None = None,
    timeout: float = TIMEOUT,
) -> Answer:
    """POST `/invoke`, with one deadline for the whole exchange.

    urllib's timeout applies to each socket operation, so the exchange runs in a daemon thread
    joined against `timeout`; on expiry the command reports TIMEOUT and the thread is abandoned
    (it holds only its own socket; the process exits or, in tests, the server closes it). A
    long-lived process calling this would keep one such thread and socket per timeout until the
    peer closes: the CLI and the command center's child processes are short-lived."""
    if not _port(endpoint):
        raise InvokeError("ENDPOINT_REFUSED", "only http://127.0.0.1:<port> endpoints are called")
    body = {"message": message}
    if customer is not None:
        body["customer_id"] = customer
    if session is not None:
        body["session_id"] = session
    request = Request(
        endpoint + "/invoke",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    outcome: dict = {}

    def exchange() -> None:
        try:
            outcome["answer"] = _exchange(request, timeout)
        except BaseException as error:  # noqa: BLE001 - handed to the caller's thread
            outcome["error"] = error

    worker = threading.Thread(target=exchange, name="agentbreeder-invoke", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise InvokeError("TIMEOUT", f"no answer within {_seconds(timeout)} s", sent=True)
    if "error" in outcome:
        raise outcome["error"]
    return outcome["answer"]


def _seconds(timeout: float) -> str:
    return f"{timeout:g}"


def _exchange(request: Request, timeout: float) -> Answer:
    """urllib wraps errors while connecting and sending the request in URLError (nothing usable
    reached the agent); errors while waiting for or reading the answer come through unwrapped,
    after the agent may have run the turn (`sent=True`)."""
    try:
        try:
            # Each socket operation gives up a little before the overall deadline, so a hung
            # connect is reported as such (not sent) rather than racing the deadline.
            response = _OPENER.open(request, timeout=timeout * 0.95)
        except HTTPError as error:  # a 4xx/5xx answer: its body is read below, guarded alike
            response = error
        with response:
            return Answer(response.getcode(), _json(response.read(MAX_RESPONSE + 1)))
    except URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise InvokeError("TIMEOUT", "cannot connect to the agent's port in time") from error
        raise InvokeError("UNREACHABLE", "cannot connect to the agent's port") from error
    except TimeoutError as error:
        raise InvokeError(
            "TIMEOUT", f"no answer within {_seconds(timeout)} s", sent=True
        ) from error
    except http.client.HTTPException as error:  # bad status line, headers or chunking
        raise InvokeError(
            "RESPONSE_INVALID", "the agent's answer is not valid HTTP", sent=True
        ) from error
    except OSError as error:
        raise InvokeError(
            "CONNECTION_LOST", "the connection closed before a full answer", sent=True
        ) from error


def _json(data: bytes) -> dict:
    if len(data) > MAX_RESPONSE:
        raise InvokeError("RESPONSE_INVALID", "the agent's answer is larger than 1 MiB", sent=True)
    try:
        body = json.loads(data)
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        raise InvokeError(
            "RESPONSE_INVALID", "the agent's answer is not JSON", sent=True
        ) from error
    if not isinstance(body, dict):
        raise InvokeError("RESPONSE_INVALID", "the agent's answer is not a JSON object", sent=True)
    return body
