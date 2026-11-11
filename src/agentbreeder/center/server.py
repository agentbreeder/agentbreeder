"""`agentbreeder up`: a loopback HTTP server for the command center (spec §6).

Single user. Standard library only, so an install keeps its two dependencies. Binds 127.0.0.1 and
offers no way to bind anything else. Every engine action is a child process of the CLI (`jobs.py`);
nothing here opens the deployment state.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import re
import secrets
import signal
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from agentbreeder import __version__
from agentbreeder.center.jobs import (
    ENVIRONMENT,
    KEY_NAME,
    BadRequest,
    Busy,
    CenterState,
    Runner,
)
from agentbreeder.services.state import private_dir, write_private

DEFAULT_PORT = 8900
TOKEN_TTL = 300
SESSION_ABSOLUTE = 8 * 3600
SESSION_IDLE = 3600
MAX_BODY = 64 * 1024
STATIC = Path(__file__).parent / "static"
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
EXAMPLES = ("northwind",)
OPTIONS = {"services-up": ("digest",), "remove": ("deployment", "target")}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def center_directory() -> Path:
    import os

    value = os.environ.get("AGENTBREEDER_CENTER_DIR")
    return private_dir(Path(value).expanduser() if value else Path.home() / ".agentbreeder/center")


def _hash(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


class Center:
    def __init__(self, directory: Path, environment: str = "dev", prefix=None):
        self.directory = directory
        self.environment = environment
        self.state = CenterState(directory)
        self.runner = Runner(self.state, directory, prefix)
        self.port = 0
        self.token: tuple[bytes, float, bool] | None = None
        self.sessions: dict[bytes, list[float]] = {}
        self.lock = threading.Lock()

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

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

    def redeem(self, token: str) -> str | None:
        with self.lock:
            if self.token is None or not hmac.compare_digest(self.token[0], _hash(token)):
                return None
            digest, expires, used = self.token
            if used:
                print(
                    "WARNING: a used command center login link was presented again.",
                    file=sys.stderr,
                )
                return None
            if time.time() > expires:
                return None
            self.token = (digest, expires, True)
            session = secrets.token_urlsafe(32)
            self.sessions[_hash(session)] = [time.time(), time.time()]
            return session

    def session_valid(self, value: str | None) -> bool:
        if not value:
            return False
        key = _hash(value)
        now = time.time()
        with self.lock:
            entry = self.sessions.get(key)
            if entry is None:
                return False
            if now - entry[0] > SESSION_ABSOLUTE or now - entry[1] > SESSION_IDLE:
                del self.sessions[key]
                return False
            entry[1] = now
            return True

    # -- projects --------------------------------------------------------------------------------

    def register(self, path: Path) -> dict:
        try:
            path = path.expanduser().resolve()
        except (ValueError, OSError, RuntimeError) as error:
            raise BadRequest("Invalid path") from error
        for name in ("agent.yaml", "stack.yaml"):
            candidate = path / name
            if candidate.is_symlink() or not candidate.is_file():
                raise BadRequest(f"That directory needs a regular {name}")
        identifier = hashlib.sha256(str(path).encode()).hexdigest()[:12]
        project = self.state.add_project(identifier, path.name, str(path), self.environment)
        if project["environment"] != self.environment:
            raise BadRequest(f"That project is registered for {project['environment']}")
        return project

    def onboard_example(self, name: str) -> dict:
        if name not in EXAMPLES:
            raise BadRequest("Unknown example")
        destination = Path.home() / "agentbreeder-projects" / name
        if not (destination / "agent.yaml").exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.runner.read_text(["example", name, str(destination)], timeout=60)
        return self.register(destination)

    def need_project(self, identifier: str) -> dict:
        project = self.state.project(identifier)
        if project is None:
            raise BadRequest("Unknown project")
        return project

    # -- reads -----------------------------------------------------------------------------------

    def state_dir(self, project: dict) -> str | None:
        result = self.runner.read_text(
            ["services", "env", project["path"], "--environment", project["environment"]]
        )
        match = re.search(r"AGENTBREEDER_STATE_DIR=(\S+)", result or "")
        return match[1].strip("'\"") if match else None

    def read_view(self, project: dict, view: str) -> dict:
        path, environment = project["path"], project["environment"]
        read = self.runner.read
        if view == "services":
            return read(["services", "status", path, "--json"])
        if view == "registry":
            return {
                "versions": read(["registry", "list", "--project", path, "--json"]),
                "problems": read(["registry", "verify", "--project", path, "--json"]),
            }
        if view == "deployments":
            return read(["list", "--stack", path, "--environment", environment, "--json"])
        if view == "cluster":
            return read(["cluster", "status", "--stack", path, "--json"])
        if view == "audit":
            state = self.state_dir(project)
            if not state:
                return {"error": "Start the services first"}
            return {
                "rows": read(["audit", "list", "--state-dir", state, "--limit", "50", "--json"]),
                "verify": read(["audit", "verify", "--state-dir", state, "--json"]),
            }
        raise BadRequest("Unknown view")

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
        return {**result, "accepted_text": project.get("accepted_grant")}

    def start_job(self, project: dict, body: dict) -> str:
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
        on_success = None
        if kind == "services-up":
            preview = self.plan(project)
            data = preview.get("data") or {}
            if data.get("digest") != options.get("digest"):
                raise BadRequest("The grant changed; review it again")
            if data.get("mcp"):
                raise BadRequest(
                    "This stack has MCP servers; review their tools with "
                    f"`agentbreeder services up {project['path']}`"
                )
            text = data.get("text", "")
            on_success = lambda: self.state.accept_grant(project["id"], text)  # noqa: E731
        return self.runner.start(project, kind, options, keys, on_success)


class Handler(BaseHTTPRequestHandler):
    center: Center
    server_version = "agentbreeder-center"
    sys_version = ""

    def log_message(self, format, *args):  # noqa: A002
        pass  # URLs and bodies stay out of the terminal

    def _send(self, code: int, body: bytes, kind: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for key, value in {**SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, value, extra: dict | None = None) -> None:
        self._send(code, json.dumps(value).encode(), "application/json", extra)

    def _session(self) -> str | None:
        # A header, not a cookie: browsers send cookies to every port on 127.0.0.1, including
        # agent endpoints and stack services.
        return self.headers.get("X-AgentBreeder-Session")

    def _host_ok(self) -> bool:
        return self.headers.get("Host") == f"127.0.0.1:{self.center.port}"

    def _api_ok(self) -> bool:
        if self.headers.get("X-AgentBreeder-Center") != "1":
            return False
        return self.headers.get("Sec-Fetch-Site", "same-origin") == "same-origin"

    def _body(self) -> dict | None:
        length = self.headers.get("Content-Length", "")
        if not re.fullmatch(r"[0-9]{1,9}", length) or int(length) > MAX_BODY:
            return None
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return None
        if self.headers.get("Origin") != self.center.origin:
            return None
        try:
            value = json.loads(self.rfile.read(int(length)) or b"{}")
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._json(403, {"error": "Bad host"})
        parts = urlsplit(self.path)
        if parts.path in FILES:
            name, kind = FILES[parts.path]
            return self._send(200, (STATIC / name).read_bytes(), kind)
        if not parts.path.startswith("/api/"):
            return self._json(404, {"error": "Not found"})
        if not self._api_ok():
            return self._json(403, {"error": "Forbidden"})
        if not self.center.session_valid(self._session()):
            return self._json(401, {"error": "Sign in again"})
        return self._guarded(lambda: self._get(parts))

    def do_POST(self):  # noqa: N802
        if not self._host_ok() or not self._api_ok():
            return self._json(403, {"error": "Forbidden"})
        body = self._body()
        if body is None:
            return self._json(403, {"error": "Forbidden"})
        path = urlsplit(self.path).path
        if path == "/api/session":
            return self._login(body)
        if not self.center.session_valid(self._session()):
            return self._json(401, {"error": "Sign in again"})
        return self._guarded(lambda: self._post(path, body))

    def _guarded(self, action) -> None:
        try:
            action()
        except Busy as error:
            self._json(409, {"error": "Another job is running", "job": error.job})
        except BadRequest as error:
            self._json(400, {"error": str(error)})
        except ValueError:
            self._json(400, {"error": "Invalid request"})
        except OSError:
            self._json(500, {"error": "A local file could not be read or written"})

    def _login(self, body: dict) -> None:
        session = self.center.redeem(str(body.get("token", "")))
        if session is None:
            return self._json(403, {"error": "That link was used or has expired"})
        self._json(200, {"session": session})

    def _get(self, parts) -> None:
        center, path = self.center, parts.path
        if path == "/api/overview":
            return self._json(
                200,
                {
                    "version": __version__,
                    "job": center.runner.current,
                    "doctor": center.runner.read(["doctor", "--json"]),
                },
            )
        if path == "/api/projects":
            return self._json(
                200,
                {
                    "projects": [
                        {**p, "jobs": center.state.latest_jobs(p["id"])}
                        for p in center.state.projects()
                    ]
                },
            )
        match = re.fullmatch(r"/api/projects/([0-9a-f]{12})/([a-z]+)", path)
        if match:
            project = center.need_project(match[1])
            return self._json(200, center.read_view(project, match[2]))
        match = re.fullmatch(r"/api/jobs/([0-9a-f]{32})", path)
        if match:
            after = parse_qs(parts.query).get("after", ["0"])[0]
            job = center.state.job(match[1], int(after) if re.fullmatch(r"[0-9]+", after) else 0)
            return self._json(200 if job else 404, job or {"error": "Unknown job"})
        self._json(404, {"error": "Not found"})

    def _post(self, path: str, body: dict) -> None:
        center = self.center
        if path == "/api/projects":
            if body.get("example"):
                project = center.onboard_example(str(body["example"]))
            elif body.get("path"):
                project = center.register(Path(str(body["path"])))
            else:
                raise BadRequest("Name an example or a path")
            return self._json(200, project)
        match = re.fullmatch(r"/api/projects/([0-9a-f]{12})/plan", path)
        if match:
            return self._json(200, center.plan(center.need_project(match[1])))
        match = re.fullmatch(r"/api/projects/([0-9a-f]{12})/jobs", path)
        if match:
            job = center.start_job(center.need_project(match[1]), body)
            return self._json(200, {"job": job})
        match = re.fullmatch(r"/api/jobs/([0-9a-f]{32})/cancel", path)
        if match:
            return self._json(200, {"cancelled": center.runner.cancel(match[1])})
        self._json(404, {"error": "Not found"})


def make_server(center: Center, port: int | None) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"center": center})
    try:
        server = ThreadingHTTPServer(
            ("127.0.0.1", DEFAULT_PORT if port is None else port), handler
        )
    except OSError as error:
        if port is not None or error.errno != errno.EADDRINUSE:
            raise
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    center.port = server.server_address[1]
    return server


def _watch_terminal(center: Center) -> None:
    """Enter in the terminal mints a new login link."""
    for _ in sys.stdin:
        url, _page = center.mint_login()
        print(f"New one-time link: {url}", flush=True)


def main(args) -> int:
    from agentbreeder.doctor import check_compose, check_docker

    if not ENVIRONMENT.fullmatch(args.environment):
        print("Invalid --environment", file=sys.stderr)
        return 2
    # Started from a backgrounded shell, SIGINT would be ignored here and in every job, and
    # Cancel would do nothing.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    for check in check_docker() + check_compose():
        if not check.passed and check.required:
            print(f"FAIL {check.name}: {check.message}", file=sys.stderr)
    center = Center(center_directory(), args.environment)
    try:
        server = make_server(center, args.port)
    except OSError:
        print(f"Cannot listen on 127.0.0.1:{args.port}", file=sys.stderr)
        return 2
    project = None
    if args.dir is not None:
        try:
            project = center.register(args.dir)
        except BadRequest as error:
            print(str(error), file=sys.stderr)
            return 2
    url, page = center.mint_login()
    print(f"AgentBreeder command center: {center.origin} (this machine only, single user)")
    print(f"One-time link, valid {TOKEN_TTL // 60} minutes: {url}", flush=True)
    if sys.stdin.isatty():
        print("Press Enter here for a new link.", flush=True)
        threading.Thread(target=_watch_terminal, args=(center,), daemon=True).start()
    if not args.no_browser:
        webbrowser.open(page.as_uri())
    if args.yes and project is not None:
        preview = center.plan(project).get("data")
        if preview is None:
            print("Could not read the stack's grant", file=sys.stderr)
        else:
            print(preview["text"], file=sys.stderr)
            try:
                center.start_job(project, {"kind": "services-up", "options": preview})
            except (BadRequest, Busy) as error:
                print(f"Services not started: {error}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        job = center.runner.current
        if job:
            center.runner.cancel(job)
            print("Asked the running job to stop.", file=sys.stderr)
        server.server_close()
    print(
        "Command center stopped. Services and deployments keep running: use "
        "`agentbreeder services down` and `agentbreeder remove`.",
        file=sys.stderr,
    )
    return 0
