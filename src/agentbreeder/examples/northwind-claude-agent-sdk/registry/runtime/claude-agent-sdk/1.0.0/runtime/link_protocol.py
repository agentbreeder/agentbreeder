"""What the agent container and the CLI container share: the two-uid contract (owner decision
D15) and the line protocol between them (`cli_link.py` in the agent, `cli_host.py` in the CLI
container).

The contract, as the deploy engine sets it up (`runtime.isolation: two-uid`):
- The agent container runs the kit as KIT_UID, with the secret files mounted.
- A second container from the same image runs the CLI host as CLI_UID, with no secret mount and
  (on local Docker) no network. The CLI it starts for each turn is a child of that host, so it
  runs as CLI_UID too, and cannot read a file only the agent container has.
- The two share one directory, SHARED_DIR. Only the kit listens there (`kit/`), and every peer
  is checked by its kernel credentials: the kit accepts CLI_UID only, the host accepts KIT_UID
  only. A CLI (also CLI_UID) cannot pose as the kit; it can try to pose as the host, which the
  kit refuses (one host connection at a time, made before any CLI exists, and a one-time nonce
  per data connection).

Messages are one JSON object per line, at most MAX_LINE bytes. Stdlib only.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
from pathlib import Path

KIT_UID = 65532
CLI_UID = 65533
SHARED_DIR = Path("/run/agentbreeder-cli")
KIT_DIR_NAME = "kit"
CONTROL = "control.sock"
DATA = "data.sock"
MODEL = "model.sock"
MAX_LINE = 1024 * 1024  # a lease carries the conversation's history (64 KiB of text, escaped)
# The Claude Code CLI version this runtime was checked against (F0: 2.1.317 in SDK 0.2.177). The
# host refuses to start on any other: the controls are a list each CLI version passes again.
PINNED_CLI = "2.1.317"
PINNED_SDK = "0.2.177"
SETTINGS = Path(__file__).resolve().parent / "settings.json"
ROLE_ENV = "AGENTBREEDER_ROLE"
HOST_ROLE = "cli-host"


class LinkError(Exception):
    """The other side broke the contract or the protocol; the connection is dropped."""


def kit_dir(shared: Path = SHARED_DIR) -> Path:
    return shared / KIT_DIR_NAME


def peer_uid(sock: socket.socket) -> int:
    """The uid of the process at the other end of a connected Unix socket, from the kernel."""
    if sys.platform.startswith("linux"):
        data = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        return struct.unpack("3i", data)[1]
    # macOS/BSD (development and tests only): LOCAL_PEERCRED gives a struct xucred.
    local_peercred = 0x001
    data = sock.getsockopt(0, local_peercred, 256)
    return struct.unpack_from("Ii", data)[1]


def send_line(sock: socket.socket, message: dict) -> None:
    data = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
    if len(data) > MAX_LINE:
        raise LinkError("message too large")
    sock.sendall(data)


class LineReader:
    """Reads one JSON object per line from a socket, never more than MAX_LINE per line."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buffer = bytearray()

    def read(self) -> dict | None:
        """The next message; None at a clean end of stream."""
        while b"\n" not in self.buffer:
            if len(self.buffer) > MAX_LINE:
                raise LinkError("line too long")
            chunk = self.sock.recv(65536)
            if not chunk:
                if self.buffer:
                    raise LinkError("truncated line")
                return None
            self.buffer.extend(chunk)
        line, _, rest = bytes(self.buffer).partition(b"\n")
        self.buffer = bytearray(rest)
        return parse(line)


def read_exact_line(sock: socket.socket, limit: int = 4096) -> dict:
    """One line, read a byte at a time so nothing after it is consumed: on a data connection
    the bytes that follow belong to the CLI."""
    data = bytearray()
    while True:
        byte = sock.recv(1)
        if not byte:
            raise LinkError("connection closed")
        if byte == b"\n":
            return parse(bytes(data))
        data.extend(byte)
        if len(data) > limit:
            raise LinkError("line too long")


def parse(line: bytes) -> dict:
    try:
        message = json.loads(line)
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        raise LinkError("not JSON") from error
    if not isinstance(message, dict):
        raise LinkError("not an object")
    return message


def listen(path: Path) -> socket.socket:
    """A Unix listener at `path` that any local uid may connect to; callers check the peer."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bind_world(server, path)
    server.listen(64)
    return server


def bind_world(server: socket.socket, path: Path) -> None:
    """Bind with the socket created mode 0666 (umask 0 for the call), so no chmod follows a path
    another process could have swapped."""
    previous = os.umask(0o111)
    try:
        server.bind(str(path))
    finally:
        os.umask(previous)


def own_dir(directory: Path) -> None:
    """Create the kit's socket directory, or refuse one this uid does not own or that is a
    link: on `kind` the shared emptyDir is world-writable and survives a container restart."""
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    info = directory.lstat()
    import stat as _stat

    if not _stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise LinkError("the kit's socket directory is not the kit's own")
    directory.chmod(0o755)


def connect(path: Path, expected_uid: int, timeout: float = 5.0) -> socket.socket:
    """Connect to `path` and refuse the peer unless the kernel says it is `expected_uid`."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
        if peer_uid(sock) != expected_uid:
            raise LinkError("unexpected peer")
    except BaseException:
        sock.close()
        raise
    sock.settimeout(None)
    return sock


def load_settings(path: Path = SETTINGS) -> dict:
    """`settings.json`: integers within fixed bounds, nothing else."""
    data = json.loads(path.read_text(encoding="utf-8"))
    bounds = {"max_concurrent_turns": (1, 16), "max_leases": (1, 32), "lease_seconds": (30, 600)}
    if not isinstance(data, dict) or set(data) != set(bounds):
        raise ValueError("settings.json must hold exactly " + ", ".join(sorted(bounds)))
    for key, (low, high) in bounds.items():
        value = data[key]
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise ValueError(f"settings.json: {key} must be an integer in [{low}, {high}]")
    if data["max_concurrent_turns"] > data["max_leases"]:
        raise ValueError("settings.json: max_concurrent_turns must not exceed max_leases")
    return data
