"""The Compose/Docker touchpoint: argv hygiene, redaction, one-shot caps (#738)."""

from __future__ import annotations

import io
import json
import stat
import subprocess
from types import SimpleNamespace

import pytest

from agentbreeder.services import compose as compose_module
from agentbreeder.services.compose import DockerCLI, redact, sanitised_environment
from agentbreeder.services.schema import ServiceError
from agentbreeder.services.state import StackState

CANARY = "canary-7f3a9b1c2d"


@pytest.fixture
def state(tmp_path):
    (tmp_path / "p").mkdir()
    value = StackState("demo", tmp_path / "p", tmp_path / "stacks")
    yield value
    value.close()


class Recorder:
    def __init__(self, *results):
        self.calls = []
        self.results = list(results)

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        result = self.results.pop(0) if self.results else (0, "", "")
        if isinstance(result, BaseException):
            raise result
        code, out, err = result
        return SimpleNamespace(returncode=code, stdout=out.encode(), stderr=err.encode())


def test_environment_is_sanitised(monkeypatch):
    monkeypatch.setenv("COMPOSE_FILE", "/evil.yaml")
    monkeypatch.setenv("DOCKER_HOST", "tcp://evil:2375")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = sanitised_environment()
    assert "COMPOSE_FILE" not in env and "DOCKER_HOST" not in env
    assert env["PATH"] == "/usr/bin"


def test_redact_longest_first():
    assert redact("a=abcdef b=abc", [b"abcdef", "abc", ""]) == "a=[redacted] b=abc"


def test_run_uses_context_and_redacts_errors():
    run = Recorder((1, "", f"line1\nError: password {CANARY} rejected\n"))
    docker = DockerCLI(run=run)
    docker.context = "desktop-linux"
    docker.redactions.append(CANARY.encode())
    with pytest.raises(ServiceError) as error:
        docker.run("volume", "ls")
    assert CANARY not in str(error.value) and "[redacted]" in str(error.value)
    assert run.calls[0][0][:3] == ["docker", "--context", "desktop-linux"]
    assert run.calls[0][1]["env"] == sanitised_environment()


def test_run_timeout_and_success():
    docker = DockerCLI(run=Recorder(subprocess.TimeoutExpired("docker", 1), (0, " ok \n", "")))
    with pytest.raises(ServiceError, match="failed or timed out"):
        docker.run("ps", what="listing")
    assert docker.run("ps") == "ok"
    with pytest.raises(ServiceError, match="docker ps failed$"):
        DockerCLI(run=Recorder((1, "", ""))).run("ps")


def test_compose_argv_is_explicit(state):
    run = Recorder()
    docker = DockerCLI(run=run)
    docker.write_compose(state, {"name": state.project, "services": {}})
    assert stat.S_IMODE(state.compose_path.stat().st_mode) == 0o600
    assert (state.directory / "compose.env").read_bytes() == b""
    docker.up(state, "echo", timeout=30)
    argv = run.calls[0][0]
    assert argv[:10] == [
        "docker",
        "compose",
        "-p",
        state.project,
        "-f",
        str(state.compose_path),
        "--project-directory",
        str(state.directory),
        "--env-file",
        str(state.directory / "compose.env"),
    ]
    assert argv[10:] == [
        "up", "--detach", "--wait", "--wait-timeout", "30", "--no-deps", "--pull", "missing",
        "echo",
    ]  # fmt: skip
    docker.write_compose(state, {"name": state.project, "services": {"a": {}}})
    docker.down(state)
    docker.logs(state, "echo", 5)
    assert run.calls[1][0][10:] == ["down", "--remove-orphans"]
    assert run.calls[2][0][10:] == ["logs", "--no-color", "--tail", "5", "echo"]


def test_down_and_containers_without_compose_file(state):
    run = Recorder()
    docker = DockerCLI(run=run)
    docker.down(state)
    assert docker.containers(state) == {}
    assert run.calls == []


def row(state, service, cid, config="c1", stack=None):
    labels = f"io.agentbreeder.stack={stack or state.id},io.agentbreeder.config={config},x="
    return {"Service": service, "ID": cid, "State": "running", "Health": "", "Labels": labels}


def test_containers_parses_lines_and_arrays(state):
    state.compose_path.write_text("x")
    lines = "\n".join(
        json.dumps(r) for r in (row(state, "a", "1"), row(state, "b", "2", stack="other"))
    )
    docker = DockerCLI(run=Recorder((0, lines, ""), (0, json.dumps([row(state, "c", "3")]), "")))
    assert docker.containers(state) == {
        "a": {"id": "1", "state": "running", "health": "", "config": "c1"}
    }
    assert list(docker.containers(state)) == ["c"]
    item = {"Service": "d", "ID": "4", "Labels": {"io.agentbreeder.stack": state.id}}
    docker = DockerCLI(run=Recorder((0, json.dumps([item]), "")))
    assert docker.containers(state)["d"]["id"] == "4"


def test_volumes_images_and_builds(state, tmp_path):
    run = Recorder(
        (0, "2026-11-08T00:00:00Z\n", ""),
        (1, "", "no such volume"),
        (0, f"{state.project}-echo-data\nother-volume\n", ""),
        (0, "", ""),
        (0, "", ""),
        (1, "", "missing"),
        (0, "", ""),
        (0, "sha256:x", ""),
    )
    docker = DockerCLI(run=run)
    assert docker.volume_identity("v") == "v@2026-11-08T00:00:00Z"
    assert docker.volume_identity("v") is None
    assert docker.stack_volumes(state) == [f"{state.project}-echo-data"]
    assert run.calls[2][0][-2:] == ["--filter", f"label=io.agentbreeder.stack={state.id}"][-2:]
    docker.remove_volume(f"{state.project}-echo-data")
    docker.build(tmp_path, "agentbreeder-svc/x:1")
    assert "--pull=false" in run.calls[4][0] and "--build-arg" not in run.calls[4][0]
    docker.pull("img@sha256:" + "a" * 64)  # missing -> pulled
    assert run.calls[6][0][1:3] == ["image", "pull"]
    docker.pull("img")  # present -> nothing
    assert len(run.calls) == 8


class FakeProcess:
    def __init__(self, stdout=b"", code=0, hang=False):
        self.stdout = io.BytesIO(stdout)
        self.code = code
        self.hang = hang
        self.killed = False

    def wait(self, timeout=None):
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired("docker", timeout)
        return self.code

    def kill(self):
        self.killed = True


def oneshot(state, process, **options):
    seen = {}

    def popen(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        if isinstance(process, BaseException):
            raise process
        return process

    ticks = iter(range(100000))
    seen["run"] = Recorder()
    docker = DockerCLI(popen=popen, run=seen["run"], clock=lambda: next(ticks))
    result = docker.oneshot(
        state,
        label="echo.bootstrap",
        network="net",
        image="img",
        command=["python3", "-c", "print(1)"],
        **options,
    )
    return result, seen


def test_oneshot_argv_is_hardened(state, tmp_path):
    result, seen = oneshot(
        state,
        FakeProcess(b"  minted-value\n"),
        capture=True,
        user="1:1",
        mounts=[(tmp_path, "/run/secrets")],
        env={"B": "2", "A": "1"},
    )
    assert result == b"minted-value"
    argv = seen["argv"]
    for flag in ("--rm", "--read-only", "--cap-drop", "--security-opt", "--pids-limit"):
        assert flag in argv
    assert argv[argv.index("--log-driver") + 1] == "none"
    assert argv[argv.index("--pull") + 1] == "never"
    assert f"type=bind,src={tmp_path},dst=/run/secrets,readonly" in argv
    assert argv.index("--env=A=1") < argv.index("--env=B=2")
    # The command is the whole argv: the image ENTRYPOINT never wraps it.
    assert argv[-3:] == ["img", "-c", "print(1)"]
    assert argv[argv.index("--entrypoint") + 1] == "python3"
    assert argv.index("--entrypoint") < argv.index("img")
    assert argv[argv.index("--name") + 1].startswith(f"{state.project}-step-")
    assert seen["kwargs"]["stderr"] == subprocess.DEVNULL
    _, seen = oneshot(state, FakeProcess())
    assert seen["kwargs"]["stdout"] == subprocess.DEVNULL and "--user" not in seen["argv"]


@pytest.mark.parametrize(
    "process,capture,code",
    [
        (FakeProcess(b"x" * (8 * 1024 + 1)), True, "ONESHOT_OUTPUT"),
        (FakeProcess(b"  \n"), True, "ONESHOT_OUTPUT"),
        (FakeProcess(b"ok", code=3), True, "ONESHOT_FAILED"),
        (FakeProcess(hang=True), False, "ONESHOT_FAILED"),
        (OSError("no docker"), False, "ONESHOT_FAILED"),
    ],
)
def test_oneshot_fails_closed(state, process, capture, code):
    with pytest.raises(ServiceError) as error:
        oneshot(state, process, capture=capture)
    assert error.value.code == code
    assert "x" * 20 not in str(error.value)


def test_probe_retries_until_deadline(state):
    attempts = []

    class Probing(DockerCLI):
        def oneshot(self, state, **kwargs):
            attempts.append(kwargs["command"][-1])
            if len(attempts) < 3:
                raise ServiceError("ONESHOT_FAILED", "x")
            return b""

    clock = iter(range(100))
    docker = Probing()
    assert docker.probe(state, "net", "http://echo:8080/", sleep=lambda s: None, clock=lambda: 0)
    assert attempts == ["http://echo:8080/"] * 3

    class Failing(DockerCLI):
        def oneshot(self, state, **kwargs):
            raise ServiceError("ONESHOT_FAILED", "x")

    assert not Failing().probe(
        state, "net", "http://x/", timeout=2, sleep=lambda s: None, clock=lambda: next(clock)
    )
    assert compose_module.PROBE_IMAGE.startswith("python:3.12-slim@sha256:")


def test_authorize_maps_docker_errors(monkeypatch):
    from agentbreeder import runtime
    from agentbreeder.store import DeploymentError

    def refuse(self, context=None):
        raise DeploymentError("Unset DOCKER_HOST")

    monkeypatch.setattr(runtime.Docker, "authorize", refuse)
    with pytest.raises(ServiceError, match="DOCKER_UNAVAILABLE"):
        DockerCLI().authorize()

    def accept(self, context=None):
        self.context = "local"

    monkeypatch.setattr(runtime.Docker, "authorize", accept)
    docker = DockerCLI()
    docker.authorize()
    assert docker.context == "local"


@pytest.mark.parametrize(
    "process,code",
    [(FakeProcess(hang=True), "ONESHOT_FAILED"), (None, "ONESHOT_OUTPUT")],
)
def test_oneshot_removes_the_container_when_aborted(state, process, code):
    """A step that hangs or floods stdout is removed by name, not just its docker client."""
    import threading as threading_module

    if process is None:  # floods stdout and keeps running until killed
        release = threading_module.Event()

        class Flooding(FakeProcess):
            def wait(self, timeout=None):
                if not self.killed:
                    release.wait(timeout or 0)
                    raise subprocess.TimeoutExpired("docker", timeout)
                return -9

        process = Flooding(b"x" * (8 * 1024 + 1))
    with pytest.raises(ServiceError) as error:
        _, seen = oneshot(state, process, capture=True, timeout=50)
    assert error.value.code == code
    assert process.killed


def test_oneshot_abort_tolerates_cleanup_failures(state):
    class Stubborn(FakeProcess):
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("docker", timeout)

    seen = {}

    def popen(argv, **kwargs):
        seen["argv"] = argv
        return Stubborn()

    ticks = iter(range(1000))
    run = Recorder((1, "", "no such container"))
    docker = DockerCLI(popen=popen, run=run, clock=lambda: next(ticks))
    with pytest.raises(ServiceError, match="timed out"):
        docker.oneshot(state, label="x", network="n", image="i", command=["true"], timeout=3)
    name = seen["argv"][seen["argv"].index("--name") + 1]
    assert run.calls[0][0][-3:] == ["rm", "--force", name]
