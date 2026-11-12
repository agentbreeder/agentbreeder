"""`agentbreeder quickstart` (spec §5.5, plan S5)."""

import io
import json
import os
import runpy
import stat
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from agentbreeder import cli, hints, quickstart
from agentbreeder.commands import doctor as doctor_command
from agentbreeder.doctor import Check
from agentbreeder.runtime import Docker
from agentbreeder.store import Store

ENV_KEYS = (quickstart.OPERATOR_ENV, quickstart.CONTEXT_ENV)


@pytest.fixture
def demo(tmp_path, monkeypatch):
    directory = tmp_path / "demo"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(quickstart, "_workdir", lambda: directory)
    monkeypatch.setattr(Docker, "authorize", lambda self: setattr(self, "context", "test-context"))
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return directory


def fake_docker(directory, calls):
    def run(*args, **kwargs):
        calls.append(args)
        if "build" in args:
            context = Path(args[-1])
            calls.append(
                ("context", context, stat.S_IMODE(os.stat(context / "server.py").st_mode))
            )
            (directory / "image-id").write_text("sha256:" + "a" * 64)
        if "ls" in args:
            return "created-image-id"
        return ""

    return run


def fake_cli(directory, seen, deploy_result=None, deploy_error=None, remove_state="removed"):
    def main(argv):
        command = argv[0]
        seen.append((argv, {key: os.environ.get(key) for key in ENV_KEYS}))
        assert argv[argv.index("--state-dir") + 1] == str(directory / "state")
        if command == "deploy":
            manifest = Path(argv[1]).read_text()
            name = next(line.split(": ")[1] for line in manifest.splitlines() if "name:" in line)
            state = Store(directory / "state")
            try:
                if state.active(name) is None:
                    state.create("owned-id", {"name": name}, "digest", "test-context")
            finally:
                state.close()
            if deploy_error:
                raise deploy_error
            print(json.dumps(deploy_result))
            return 0 if deploy_result["state"] == "ready" else 1
        if command == "status":
            print(json.dumps({"events": [1, 2, 3, 4]}))
            return 0
        if command == "remove":
            print(json.dumps({"state": remove_state}))
            return 0
        raise AssertionError(argv)

    return main


READY = {"id": "owned-id", "state": "ready", "endpoint": "http://127.0.0.1:9"}


def test_happy_path_runs_five_steps_in_process(demo, monkeypatch, capsys):
    calls, seen = [], []
    monkeypatch.setattr(quickstart, "run", fake_docker(demo, calls))
    monkeypatch.setattr(quickstart.cli, "main", fake_cli(demo, seen, READY))
    monkeypatch.setattr(quickstart, "invoke", lambda endpoint: {"input": {"message": "hello"}})
    assert quickstart.main() == 0
    out = capsys.readouterr().out
    for step in ("1/5", "2/5", "3/5", "4/5", "5/5"):
        assert step in out
    # The guided path continues with a starter agent (#769), in the launcher's form.
    assert out.strip().splitlines()[-1] == hints.next_line(
        "new", "agent", "hello", "--model", "gemini"
    )
    words = out.strip().splitlines()[-1].split()
    args = cli._parser().parse_args(words[words.index("new") :])
    assert (args.new_command, args.name, args.model) == ("agent", "hello", "gemini")
    # The CLI's JSON is captured, not echoed.
    assert '"endpoint"' not in out
    assert [argv[0] for argv, _ in seen] == ["deploy", "deploy", "status", "remove"]
    for _, env in seen:
        assert env[quickstart.OPERATOR_ENV] == str(demo / "operator.yaml")
        assert env[quickstart.CONTEXT_ENV] == "test-context"
    for key in ENV_KEYS:
        assert key not in os.environ
    for name in ("operator.yaml", "principals.yaml"):
        assert stat.S_IMODE((demo / name).stat().st_mode) == 0o600


def test_image_is_built_from_a_world_readable_copy(demo, monkeypatch):
    calls, seen = [], []
    monkeypatch.setattr(quickstart, "run", fake_docker(demo, calls))
    monkeypatch.setattr(quickstart.cli, "main", fake_cli(demo, seen, READY))
    monkeypatch.setattr(quickstart, "invoke", lambda endpoint: {"input": {"message": "hello"}})
    assert quickstart.main() == 0
    _, context, mode = next(call for call in calls if call[0] == "context")
    assert context.parent == demo
    assert mode == 0o644  # USER 65532 must read it
    packaged = Path(quickstart.__file__).parent / "examples" / "http-agent"
    assert context.resolve() != packaged.resolve()


def test_environment_restored_to_previous_values(demo, monkeypatch):
    monkeypatch.setenv(quickstart.OPERATOR_ENV, "/previous/operator.yaml")
    calls, seen = [], []
    monkeypatch.setattr(quickstart, "run", fake_docker(demo, calls))
    monkeypatch.setattr(quickstart.cli, "main", fake_cli(demo, seen, READY))
    monkeypatch.setattr(quickstart, "invoke", lambda endpoint: {"input": {"message": "hello"}})
    assert quickstart.main() == 0
    assert os.environ[quickstart.OPERATOR_ENV] == "/previous/operator.yaml"
    assert quickstart.CONTEXT_ENV not in os.environ


def test_build_timeout_cleans_unique_tag(demo, monkeypatch):
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        if "build" in args:
            raise subprocess.TimeoutExpired("docker", 180)
        if "ls" in args:
            return "created-image-id"
        return ""

    monkeypatch.setattr(quickstart, "run", run)
    assert quickstart.main() == 1
    tag = calls[0][calls[0].index("--tag") + 1]
    assert tag.startswith("agentbreeder-demo-")
    assert calls[-1] == ("docker", "--context", "test-context", "image", "rm", tag)
    for key in ENV_KEYS:
        assert key not in os.environ


def test_deploy_crash_recovers_durable_id_for_cleanup(demo, monkeypatch, capsys):
    calls, seen = [], []
    monkeypatch.setattr(quickstart, "run", fake_docker(demo, calls))
    monkeypatch.setattr(
        quickstart.cli, "main", fake_cli(demo, seen, deploy_error=KeyError("engine bug"))
    )
    assert quickstart.main() == 1
    assert [argv[0] for argv, _ in seen] == ["deploy", "remove"]
    assert seen[-1][0][1] == "owned-id"
    assert seen[-1][1][quickstart.CONTEXT_ENV] == "test-context"
    assert "5/5 Sample container removed" in capsys.readouterr().out


def test_failed_deploy_keeps_id_and_removes(demo, monkeypatch, capsys):
    calls, seen = [], []
    failed = {"id": "owned-id", "state": "failed", "error": "unhealthy"}
    monkeypatch.setattr(quickstart, "run", fake_docker(demo, calls))
    monkeypatch.setattr(quickstart.cli, "main", fake_cli(demo, seen, failed))
    assert quickstart.main() == 1
    assert seen[-1][0][:2] == ["remove", "owned-id"]
    captured = capsys.readouterr()
    assert "Deployment failed" in captured.err
    assert "Next:" not in captured.out  # no next step after a failed demo


def test_unexpected_response_fails_and_cleans_up(demo, monkeypatch):
    calls, seen = [], []
    monkeypatch.setattr(quickstart, "run", fake_docker(demo, calls))
    monkeypatch.setattr(quickstart.cli, "main", fake_cli(demo, seen, READY))
    monkeypatch.setattr(quickstart, "invoke", lambda endpoint: {"input": "other"})
    assert quickstart.main() == 1
    assert seen[-1][0][0] == "remove"


def test_cleanup_failure_prints_retry_command(demo, monkeypatch, capsys):
    calls, seen = [], []
    monkeypatch.setattr(quickstart, "run", fake_docker(demo, calls))
    monkeypatch.setattr(quickstart.cli, "main", fake_cli(demo, seen, READY, remove_state="ready"))
    monkeypatch.setattr(quickstart, "invoke", lambda endpoint: {"input": {"message": "hello"}})
    assert quickstart.main() == 1
    err = capsys.readouterr().err
    assert "Cleanup incomplete" in err and "agentbreeder remove owned-id --local" in err


def test_cli_output_is_not_leaked_to_the_terminal(demo, monkeypatch, capsys):
    def noisy(argv):
        print("secret-ish detail")
        print("stderr detail", file=__import__("sys").stderr)
        return 2

    monkeypatch.setattr(quickstart.cli, "main", noisy)
    with pytest.raises(RuntimeError, match="deploy failed"):
        quickstart._cli(demo / "state", "deploy", "x")
    captured = capsys.readouterr()
    assert "secret-ish" not in captured.out + captured.err


def test_dry_run_checks_without_docker_changes(demo, monkeypatch, capsys):
    monkeypatch.setattr(
        doctor_command, "check_docker", lambda: [Check("docker", True, "ok", True)]
    )
    monkeypatch.setattr(doctor_command, "check_compose", lambda: [])
    monkeypatch.setattr(
        quickstart, "run", lambda *a, **k: pytest.fail("dry run must not call docker")
    )
    assert quickstart.main(dry_run=True) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out and "1/5" in out and "5/5" in out
    assert "Valid manifest: http-sample" in out
    assert not demo.exists()  # nothing retained


def test_dry_run_reports_doctor_failure(demo, monkeypatch, capsys):
    monkeypatch.setattr(
        doctor_command, "check_docker", lambda: [Check("docker", False, "not running", True)]
    )
    monkeypatch.setattr(doctor_command, "check_compose", lambda: [])
    assert quickstart.main(dry_run=True) == 1
    assert "FAIL docker" in capsys.readouterr().out


def test_cli_subcommand_dispatches(monkeypatch):
    seen = []
    monkeypatch.setattr(quickstart, "main", lambda dry_run=False: seen.append(dry_run) or 0)
    assert cli.main(["quickstart", "--dry-run"]) == 0
    assert cli.main(["quickstart"]) == 0
    assert seen == [True, False]


def test_checkout_wrapper_calls_package(monkeypatch):
    monkeypatch.setattr(quickstart, "main", lambda dry_run=False: 7)
    path = Path(quickstart.__file__).parent / "examples/http-agent/quickstart.py"
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(path), run_name="__main__")
    assert exit_info.value.code == 7


def test_invoke_ignores_proxies(monkeypatch):
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            payload = json.dumps({"input": json.loads(body)}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    try:
        result = quickstart.invoke(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
    assert result == {"input": {"message": "hello"}}


@pytest.mark.skipif(
    os.environ.get("AGENTBREEDER_DOCKER_TEST") != "1", reason="set AGENTBREEDER_DOCKER_TEST=1"
)
def test_real_docker_quickstart():
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert quickstart.main() == 0
    assert "5/5 Sample container removed" in buffer.getvalue()


class StatefulDocker:
    """Containers by deployment ID; lets the real in-process CLI and store run without Docker."""

    def __init__(self):
        self.context = ""
        self.containers = {}
        self.removed = []

    def authorize(self, context=None):
        self.context = "test-context"

    def inspect(self, record):
        return self.containers.get(record["id"])

    def image_present(self, image):
        return True

    def create(self, record, manifest, **kwargs):
        info = {"Id": "c-" + record["id"], "State": {"Running": True}}
        self.containers[record["id"]] = info
        return info

    def start(self, info):
        pass

    def remove(self, info):
        self.removed.append(info["Id"])
        self.containers = {k: v for k, v in self.containers.items() if v["Id"] != info["Id"]}

    def endpoint(self, info, manifest):
        return "http://127.0.0.1:9"

    def healthy(self, endpoint, path, timeout=30):
        return True


def test_real_cli_gate_and_store_in_process(demo, monkeypatch, capsys):
    """Real `cli.main`, identity gate (static adapter) and SQLite store; only Docker is faked."""
    fake, calls = StatefulDocker(), []
    monkeypatch.setattr(cli, "Docker", lambda: fake)
    monkeypatch.setattr(quickstart, "run", fake_docker(demo, calls))
    monkeypatch.setattr(quickstart, "invoke", lambda endpoint: {"input": {"message": "hello"}})
    assert quickstart.main() == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "4/5 Retry reused" in out and "5/5 Sample container removed" in out
    assert len(fake.removed) == 1 and not fake.containers
    store = Store(demo / "state")
    try:
        assert store.active_all() == []
    finally:
        store.close()
    for key in ENV_KEYS:
        assert key not in os.environ


def test_keyboard_interrupt_still_cleans_up(demo, monkeypatch):
    calls, seen = [], []
    monkeypatch.setattr(quickstart, "run", fake_docker(demo, calls))
    monkeypatch.setattr(
        quickstart.cli, "main", fake_cli(demo, seen, deploy_error=KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        quickstart.main()
    assert seen[-1][0][:2] == ["remove", "owned-id"]
    assert calls[-1][:5] == ("docker", "--context", "test-context", "image", "rm")
    for key in ENV_KEYS:
        assert key not in os.environ


def test_unexpected_error_text_is_not_printed(demo, monkeypatch, capsys):
    calls, seen = [], []
    monkeypatch.setattr(quickstart, "run", fake_docker(demo, calls))
    monkeypatch.setattr(
        quickstart.cli, "main", fake_cli(demo, seen, deploy_error=KeyError("/home/me/detail"))
    )
    assert quickstart.main() == 1
    err = capsys.readouterr().err
    assert "Demo stopped: unexpected KeyError" in err and "/home/me" not in err


def test_dry_run_fails_when_sample_is_invalid(demo, monkeypatch, capsys):
    monkeypatch.setattr(doctor_command, "check_docker", lambda: [])
    monkeypatch.setattr(doctor_command, "check_compose", lambda: [])

    def broken(name, destination):
        raise quickstart.example.ExampleError("CONTENT_MISMATCH", "tampered")

    monkeypatch.setattr(quickstart.example, "copy", broken)
    assert quickstart.main(dry_run=True) == 1
    assert "Dry run stopped: [CONTENT_MISMATCH]" in capsys.readouterr().err


def test_dry_run_fails_when_validate_fails(demo, monkeypatch, capsys):
    monkeypatch.setattr(doctor_command, "check_docker", lambda: [])
    monkeypatch.setattr(doctor_command, "check_compose", lambda: [])

    def copy(name, destination):
        destination.mkdir()
        (destination / "agent.yaml").write_text("schema_version: 2\n")
        return destination

    monkeypatch.setattr(quickstart.example, "copy", copy)
    assert quickstart.main(dry_run=True) == 1
    assert "Validation failed" in capsys.readouterr().err


def test_dry_run_reports_workdir_failure(monkeypatch, capsys):
    monkeypatch.setattr(doctor_command, "check_docker", lambda: [])
    monkeypatch.setattr(doctor_command, "check_compose", lambda: [])

    def fail():
        raise OSError("no space")

    monkeypatch.setattr(quickstart, "_workdir", fail)
    assert quickstart.main(dry_run=True) == 1
    assert "Dry run stopped: no space" in capsys.readouterr().err
