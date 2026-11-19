"""Local fixtures for git registry sources (#829 slice 1b): no third-party network, no Docker.

- `BareRepo`: a bare repository whose commits are written with git plumbing (`hash-object`,
  `update-index`, `write-tree`, `commit-tree`) and fixed dates, so commit ids are stable.
- `GitServer`: git's own `git http-backend` behind a loopback HTTPS server with a test
  certificate, passed to git through `GIT_SSL_CAINFO` (spec §11.1 slice 1b). Options make it
  speak protocol v0 only (so fetch-by-SHA of a non-tip commit is refused), redirect every
  request, or record what it was asked.
"""

from __future__ import annotations

import os
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.egress_support import make_certificate

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git is not installed")

FIXED = {
    "GIT_AUTHOR_NAME": "fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    # macOS git precomposes Unicode file names by default; fixtures store names as given.
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.precomposeUnicode",
    "GIT_CONFIG_VALUE_0": "false",
}

FILE, EXEC, LINK, MODULE = "100644", "100755", "120000", "160000"


class BareRepo:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._git("init", "--quiet", "--bare", "-b", "main", str(path), git_dir=False)
        self.files: dict[str, tuple[str, bytes]] = {}

    def _git(self, *args, input: bytes | None = None, extra=None, git_dir=True) -> str:
        env = {"PATH": os.environ["PATH"], "HOME": str(self.path.parent), **FIXED, **(extra or {})}
        argv = [GIT, *([f"--git-dir={self.path}"] if git_dir else []), *args]
        result = subprocess.run(argv, env=env, input=input, capture_output=True, check=True)
        return result.stdout.decode().strip()

    def put(self, path: str, data: bytes | str, mode: str = FILE) -> None:
        self.files[path] = (mode, data.encode() if isinstance(data, str) else data)

    def remove(self, path: str) -> None:
        del self.files[path]

    def version(self, kind: str, name: str, version: str, files: dict, prefix="registry") -> None:
        base = f"{prefix}/{kind}/{name}/{version}" if prefix != "." else f"{kind}/{name}/{version}"
        self.put(f"{base}/manifest.yaml", f"kind: {kind}\nname: {name}\nversion: {version}\n")
        for relative, content in files.items():
            mode = EXEC if relative.endswith(".sh") else FILE
            self.put(f"{base}/{relative}", content, mode)

    def commit(self, message: str = "c", *, parent: str | None = "HEAD", ref="main") -> str:
        index = self.path / "fixture-index"
        index.unlink(missing_ok=True)
        extra = {"GIT_INDEX_FILE": str(index)}
        for path, (mode, data) in sorted(self.files.items()):
            if mode == MODULE:
                sha = data.decode()
            else:
                sha = self._git("hash-object", "-w", "--stdin", input=data, extra=extra)
            self._git("update-index", "--add", "--cacheinfo", f"{mode},{sha},{path}", extra=extra)
        tree = self._git("write-tree", extra=extra)
        index.unlink()
        args = ["commit-tree", tree, "-m", message]
        if parent == "HEAD":
            try:
                parent = self._git("rev-parse", "--verify", "-q", f"refs/heads/{ref}")
            except subprocess.CalledProcessError:
                parent = None
        if parent:
            args += ["-p", parent]
        sha = self._git(*args)
        self._git("update-ref", f"refs/heads/{ref}", sha)
        return sha

    def force(self, sha: str, ref="main") -> None:
        """Point `ref` at `sha` (a force-push), then drop unreachable objects."""
        self._git("update-ref", f"refs/heads/{ref}", sha)

    def prune(self) -> None:
        self._git("reflog", "expire", "--expire=now", "--all")
        self._git("gc", "--prune=now", "--quiet")


class GitServer:
    """`git http-backend` over loopback HTTPS. `url(name)` is https://127.0.0.1:PORT/name."""

    def __init__(
        self,
        root: Path,
        certs: Path,
        *,
        v0: bool = False,
        redirect: bool = False,
        status: int | None = None,
        names: tuple[str, ...] = ("localhost",),
    ):
        self.root = root
        self.v0 = v0
        self.redirect = redirect
        self.status = status
        self.requests: list[tuple[str, str]] = []
        backend = Path(subprocess.check_output([GIT, "--exec-path"], text=True).strip())
        self.backend = backend / "git-http-backend"
        certs.mkdir(parents=True, exist_ok=True)
        cert, key, _ = make_certificate(certs, names=names)
        self.ca = str(cert)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _cgi(self):
                parts = urlsplit(self.path)
                server.requests.append((self.command, parts.path))
                if server.status is not None:
                    self.send_response(server.status)
                    self.send_header("WWW-Authenticate", 'Basic realm="fixture"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if server.redirect:
                    self.send_response(302)
                    self.send_header("Location", f"https://127.0.0.1:1{self.path}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                env = {
                    "PATH": os.environ["PATH"],
                    "GIT_PROJECT_ROOT": str(server.root),
                    "GIT_HTTP_EXPORT_ALL": "1",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": os.devnull,
                    "PATH_INFO": parts.path,
                    "QUERY_STRING": parts.query,
                    "REQUEST_METHOD": self.command,
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "CONTENT_LENGTH": str(len(body)),
                    "REMOTE_ADDR": "127.0.0.1",
                }
                if self.headers.get("Git-Protocol") and not server.v0:
                    env["GIT_PROTOCOL"] = self.headers["Git-Protocol"]
                if self.headers.get("Content-Encoding"):
                    env["HTTP_CONTENT_ENCODING"] = self.headers["Content-Encoding"]
                out = subprocess.run(
                    [str(server.backend)], input=body, env=env, capture_output=True
                ).stdout
                head, _, rest = out.partition(b"\r\n\r\n")
                status, headers = 200, []
                for line in head.decode("latin-1").split("\r\n"):
                    key, _, value = line.partition(": ")
                    if key.lower() == "status":
                        status = int(value.split()[0])
                    elif key:
                        headers.append((key, value))
                self.send_response(status)
                for key, value in headers:
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(rest)))
                self.end_headers()
                self.wfile.write(rest)

            do_GET = do_POST = _cgi

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert), str(key))
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self, name: str) -> str:
        return f"https://127.0.0.1:{self.port}/{name}"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def trust_file(home: Path, *entries) -> Path:
    """Write ~/.agentbreeder/sources.yaml (0600) under `home` with `entries`."""
    import yaml

    path = home / ".agentbreeder" / "sources.yaml"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(yaml.safe_dump({"trusted": list(entries)}))
    path.chmod(0o600)
    return path
