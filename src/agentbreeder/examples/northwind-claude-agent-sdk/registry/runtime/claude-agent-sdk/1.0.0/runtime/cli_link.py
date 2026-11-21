"""The kit's side of the two-uid contract (owner decision D15): it listens for the CLI host and
drives one CLI per turn through it. See `link_protocol.py` for the contract and `cli_host.py`
for the other side.

- **Control** (`kit/control.sock`): the host connects, the kernel must say it is CLI_UID (never
  the kit's own uid), and it says hello with the CLI version, which must be the pinned one. One
  host at a time: while a host is connected, another connection is refused, so a CLI cannot take
  the host's place. Requests carry an id; the host answers each.
- **Data** (`kit/data.sock`): for each `run`, the kit makes a one-time nonce and sends it over the
  control connection only; the host connects with it and that connection becomes the CLI's
  stdin and stdout. A connection with any other nonce, or from any other uid, is closed.

The adapter's `HostTransport` is the SDK's transport over a data connection.

Stdlib only.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import socket
import threading
from pathlib import Path

from link_protocol import (
    CLI_UID,
    CONTROL,
    DATA,
    PINNED_CLI,
    SHARED_DIR,
    LineReader,
    LinkError,
    kit_dir,
    listen,
    own_dir,
    peer_uid,
    read_exact_line,
    send_line,
)

REQUEST_TIMEOUT = 15.0
MAX_DATA_HANDLERS = 8  # data connections waiting for their nonce at once


def own_uid() -> int:
    return os.getuid()


class HostUnavailable(RuntimeError):
    """No CLI host is connected, or it did not answer."""


class CliLink:
    def __init__(self, shared: Path = SHARED_DIR, *, host_uid: int = CLI_UID):
        self.directory = kit_dir(shared)
        self.host_uid = host_uid
        self.guard = threading.Lock()
        self.connected = threading.Condition(self.guard)
        self.host: socket.socket | None = None
        self.host_version: str | None = None
        self.pending: dict[str, dict] = {}  # request id -> {"event", "reply"}
        self.waiting: dict[str, dict] = {}  # nonce -> {"event", "sock"}
        self.servers: list[socket.socket] = []
        self.closed = False
        self.handlers = threading.BoundedSemaphore(MAX_DATA_HANDLERS)
        self.send_guard = threading.Lock()  # concurrent turns' requests never interleave

    # -- lifecycle ------------------------------------------------------------------------------

    def start(self) -> None:
        if self.host_uid == own_uid():
            raise LinkError("the CLI host must run under a uid other than the kit's")
        own_dir(self.directory)
        for name, loop in ((CONTROL, self._control_loop), (DATA, self._data_loop)):
            server = listen(self.directory / name)
            self.servers.append(server)
            threading.Thread(target=loop, args=(server,), name=f"link-{name}", daemon=True).start()

    def wait_ready(self, timeout: float) -> bool:
        with self.connected:
            return self.connected.wait_for(lambda: self.host is not None, timeout)

    def close(self) -> None:
        self.closed = True
        for server in self.servers:
            with contextlib.suppress(OSError):
                server.close()
        with self.guard:
            host = self.host
        if host is not None:
            with contextlib.suppress(OSError):
                host.shutdown(socket.SHUT_RDWR)
                host.close()

    # -- control ----------------------------------------------------------------------------

    def _control_loop(self, server: socket.socket) -> None:
        while not self.closed:
            try:
                sock, _ = server.accept()
            except OSError:
                return
            with self.guard:
                busy = self.host is not None
            try:
                refused = busy or peer_uid(sock) != self.host_uid
            except OSError:
                refused = True
            if refused:
                sock.close()
                continue
            threading.Thread(target=self._serve_host, args=(sock,), daemon=True).start()

    def _serve_host(self, sock: socket.socket) -> None:
        reader = LineReader(sock)
        try:
            sock.settimeout(10.0)
            hello = reader.read()
            if not hello or hello.get("op") != "hello" or hello.get("cli_version") != PINNED_CLI:
                raise LinkError("the host's hello is wrong")
            sock.settimeout(None)
            with self.connected:
                if self.host is not None:
                    raise LinkError("a host is already connected")
                self.host, self.host_version = sock, hello["cli_version"]
                self.connected.notify_all()
            while (message := reader.read()) is not None:
                request = message.get("id")
                with self.guard:
                    entry = self.pending.get(request) if isinstance(request, str) else None
                if entry is not None:
                    entry["reply"] = message
                    entry["event"].set()
        except (OSError, LinkError, TimeoutError):
            pass
        finally:
            with self.guard:
                if self.host is sock:
                    self.host = None
                for entry in self.pending.values():
                    entry["event"].set()  # no reply: the waiter raises
            with contextlib.suppress(OSError):
                sock.close()

    def request(self, message: dict, timeout: float = REQUEST_TIMEOUT) -> dict:
        """Send one request to the host and wait for its answer (from a worker thread)."""
        request = secrets.token_hex(8)
        entry = {"event": threading.Event(), "reply": None}
        with self.guard:
            host = self.host
            if host is None:
                raise HostUnavailable("no CLI host is connected")
            self.pending[request] = entry
        try:
            with self.send_guard:
                send_line(host, {**message, "id": request})
            if not entry["event"].wait(timeout) or entry["reply"] is None:
                raise HostUnavailable("the CLI host did not answer")
            reply = entry["reply"]
            if reply.get("ok") is not True:
                raise LinkError(str(reply.get("error", "refused"))[:200])
            return reply
        except OSError as error:
            raise HostUnavailable("the CLI host connection failed") from error
        finally:
            with self.guard:
                self.pending.pop(request, None)

    # -- turns ------------------------------------------------------------------------------

    def lease(self, session_id: str, history: list[dict]) -> str:
        """A per-turn directory with the transcript written; returns the turn handle."""
        reply = self.request({"op": "lease", "session_id": session_id, "history": history})
        turn = reply.get("turn")
        if not isinstance(turn, str):
            raise LinkError("the host's lease answer is wrong")
        return turn

    def end(self, turn: str) -> None:
        """Kill the turn's CLI and remove its directory (idempotent)."""
        with contextlib.suppress(HostUnavailable, LinkError):
            self.request({"op": "end", "turn": turn})

    def run(self, turn: str, argv: list[str], env: dict, timeout: float = 30.0) -> socket.socket:
        """Start the turn's CLI; returns the connection that is its stdin and stdout."""
        nonce = secrets.token_hex(16)
        slot = {"event": threading.Event(), "sock": None}
        with self.guard:
            self.waiting[nonce] = slot
        try:
            self.request(
                {"op": "run", "turn": turn, "nonce": nonce, "argv": argv, "env": env}, timeout
            )
            if not slot["event"].wait(timeout) or slot["sock"] is None:
                raise HostUnavailable("the CLI's connection did not arrive")
            slot["taken"] = True
            return slot["sock"]
        finally:
            with self.guard:
                self.waiting.pop(nonce, None)
                # The connection may have arrived just after the wait gave up: never leak it.
                late = slot["sock"] if not slot.get("taken") else None
            if late is not None:
                late.close()

    def _data_loop(self, server: socket.socket) -> None:
        while not self.closed:
            try:
                sock, _ = server.accept()
            except OSError:
                return
            try:
                allowed = peer_uid(sock) == self.host_uid
            except OSError:
                allowed = False
            # A cap on handlers: a CLI (the host's uid) could otherwise open connections until
            # the agent container runs out of threads.
            if not allowed or not self.handlers.acquire(blocking=False):
                sock.close()
                continue
            threading.Thread(target=self._data, args=(sock,), daemon=True).start()

    def _data(self, sock: socket.socket) -> None:
        try:
            self._nonce(sock)
        finally:
            self.handlers.release()

    def _nonce(self, sock: socket.socket) -> None:
        try:
            sock.settimeout(10.0)
            nonce = read_exact_line(sock).get("nonce")
            send_ok = False
            with self.guard:
                slot = self.waiting.pop(nonce, None) if isinstance(nonce, str) else None
                if slot is not None:
                    slot["sock"] = sock  # under the guard: `run` sees it or closes it
                    send_ok = True
            if not send_ok:
                raise LinkError("unknown nonce")
            send_line(sock, {"ok": True})
            sock.settimeout(None)
            slot["event"].set()
        except (OSError, LinkError, TimeoutError):
            sock.close()
