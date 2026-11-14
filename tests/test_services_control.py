"""`services start|stop|restart [DIR] NAME` and the per-stack write lock (#822, M6; console §4.2,
S-11). Docker is faked; tests/test_services_docker.py covers the real-Docker flow."""

# Fixtures imported from test_services_engine are redefined as parameters on purpose.
# ruff: noqa: F811

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from test_services_engine import FakeDocker, env, fake_docker  # noqa: F401

from agentbreeder import cli
from agentbreeder.services import engine as engine_module
from agentbreeder.services import state as state_module
from agentbreeder.services.compose import DockerCLI
from agentbreeder.services.schema import ServiceError
from agentbreeder.services.state import LOCKS_DIR, services_lock


class ControlDocker(FakeDocker):
    """FakeDocker plus `control`: records the verb and updates the container's state."""

    def control(self, state, verb, service, timeout=180):
        self.events.append((verb, service))
        container = self.running[service]
        container["state"] = "exited" if verb == "stop" else "running"
        container["health"] = "" if verb == "stop" else "starting"


@pytest.fixture
def docker(monkeypatch):
    fake = ControlDocker()
    monkeypatch.setattr(engine_module, "DockerCLI", lambda: fake)
    return fake


@pytest.fixture
def stack(env, docker, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["services", "up", str(env), "--environment", "dev", "--yes"]) == 0
    capsys.readouterr()
    docker.events.clear()
    return env


def stack_id(project: Path) -> str:
    instance = engine_module.Engine(project, docker=ControlDocker())
    try:
        return instance.state.id
    finally:
        instance.close()


def root() -> Path:
    return state_module.stacks_root()


def in_use(monkeypatch, names=("dev/demo-agent",)):
    monkeypatch.setattr(engine_module.Engine, "active_deployments", lambda self: list(names))


# -- the verbs ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verb,state,health,past",
    [
        ("stop", "exited", "", "stopped"),
        ("start", "running", "starting", "started"),
        ("restart", "running", "starting", "restarted"),
    ],
)
def test_each_verb_acts_on_one_service_only(stack, docker, capsys, verb, state, health, past):
    before = {name: dict(info) for name, info in docker.running.items()}
    assert cli.main(["services", verb, str(stack), "echo"]) == 0
    assert docker.events == [(verb, "echo")]  # never `up`: no re-render, no recreate
    assert docker.running["gateway"] == before["gateway"]
    assert docker.running["echo"]["id"] == before["echo"]["id"]
    shown = f", {health}" if health else ""
    assert capsys.readouterr().out == f"echo {past} ({state}{shown})\n"


def test_json_result(stack, docker, capsys):
    assert cli.main(["services", "restart", str(stack), "gateway", "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body == {
        "schema_version": 1,
        "stack": stack_id(stack),
        "service": "gateway",
        "action": "restart",
        "state": "running",
        "health": "starting",
        "in_use": [],
    }


def test_dir_defaults_to_the_current_directory(stack, docker, monkeypatch):
    monkeypatch.chdir(stack)
    assert cli.main(["services", "stop", "echo"]) == 0
    assert docker.events == [("stop", "echo")]


def test_too_many_targets_is_a_usage_error(stack, capsys):
    with pytest.raises(SystemExit) as raised:
        cli.main(["services", "start", str(stack), "echo", "extra"])
    assert raised.value.code == 2
    assert "start takes [DIR] NAME" in capsys.readouterr().err


def test_unknown_service_is_coded(stack, docker, capsys):
    assert cli.main(["services", "restart", str(stack), "nope", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[SERVICE_UNKNOWN]")
    assert json.loads(captured.out)["error"]["code"] == "SERVICE_UNKNOWN"
    assert docker.events == []


def test_a_service_without_a_container_needs_up(stack, docker, capsys):
    del docker.running["echo"]
    assert cli.main(["services", "start", str(stack), "echo"]) == 2
    assert "[SERVICE_NOT_CREATED]" in capsys.readouterr().err
    assert docker.events == []


@pytest.mark.parametrize("verb", ["stop", "restart"])
def test_stop_and_restart_refuse_while_deployments_use_the_stack(
    stack, docker, capsys, monkeypatch, verb
):
    in_use(monkeypatch)
    assert cli.main(["services", verb, str(stack), "gateway", "--json"]) == 2
    captured = capsys.readouterr()
    error = json.loads(captured.out)["error"]
    assert error["code"] == "SERVICE_IN_USE"
    assert "dev/demo-agent" in error["message"] and "--yes" in error["message"]
    assert docker.events == []

    assert cli.main(["services", verb, str(stack), "gateway", "--yes", "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["in_use"] == ["dev/demo-agent"]
    assert f"WARNING: {verb} with active deployments: dev/demo-agent" in captured.err
    assert docker.events == [(verb, "gateway")]


def test_start_never_needs_yes(stack, docker, monkeypatch, capsys):
    in_use(monkeypatch)
    with pytest.raises(SystemExit):
        cli.main(["services", "start", str(stack), "echo", "--yes"])  # no such flag
    capsys.readouterr()
    assert cli.main(["services", "start", str(stack), "echo"]) == 0
    assert "WARNING" not in capsys.readouterr().err


def test_docker_failure_is_coded(stack, docker, capsys, monkeypatch):
    def fail(state, verb, service, timeout=180):
        raise ServiceError("DOCKER_FAILED", f"docker compose {verb} {service} failed")

    monkeypatch.setattr(docker, "control", fail)
    assert cli.main(["services", "stop", str(stack), "echo", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "DOCKER_FAILED"


def test_unreadable_state_is_coded(stack, docker, capsys, monkeypatch):
    def broken(self, *args, **kwargs):
        raise OSError("disk")

    monkeypatch.setattr(engine_module.Engine, "control", broken)
    assert cli.main(["services", "stop", str(stack), "echo", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "STATE_UNREADABLE"


def test_mcp_relays_are_addressable(stack, docker, monkeypatch, capsys):
    from agentbreeder.services import mcp_stack

    relay = mcp_stack.relay_name("search")
    monkeypatch.setattr(
        engine_module.Engine,
        "__init__",
        _with_mcp(engine_module.Engine.__init__, {"search": object()}),
    )
    docker.running[relay] = {"id": "r1", "state": "running", "health": "", "config": ""}
    assert cli.main(["services", "restart", str(stack), relay]) == 0
    assert docker.events == [("restart", relay)]


def _with_mcp(original, mcp):
    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.stack = self.stack.model_copy(update={"mcp": mcp})

    return init


def test_compose_control_argv():
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)

        class Done:
            returncode, stdout, stderr = 0, b"", b""

        return Done()

    docker = DockerCLI(run=run)
    state = type(
        "S", (), {"project": "agentbreeder-p", "directory": Path("/s"), "compose_path": Path("/c")}
    )()
    for verb in ("start", "stop", "restart"):
        docker.control(state, verb, "litellm")
    tails = [argv[argv.index("--env-file") + 2 :] for argv in seen]
    expected = [["start", "litellm"], ["stop", "litellm"], ["restart", "--no-deps", "litellm"]]
    assert tails == expected
    with pytest.raises(ValueError):
        docker.control(state, "rm", "litellm")


# -- the S-11 lock ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["start", "{dir}", "echo"],
        ["stop", "{dir}", "echo"],
        ["restart", "{dir}", "echo"],
        ["up", "{dir}", "--environment", "dev", "--yes"],
        ["down", "{dir}"],
        ["down", "{dir}", "--purge", "--yes"],
        ["plan", "{dir}", "--environment", "dev"],
    ],
)
def test_every_write_fails_fast_while_another_holds_the_stack(stack, docker, capsys, argv):
    with services_lock(root(), stack_id(stack)):
        code = cli.main(["services", *[str(stack) if a == "{dir}" else a for a in argv]])
    assert code == 2
    assert "[SERVICES_BUSY] another services operation is running" in capsys.readouterr().err
    assert docker.events == []


def test_busy_under_json_is_the_error_object(stack, docker, capsys):
    with services_lock(root(), stack_id(stack)):
        assert cli.main(["services", "stop", str(stack), "echo", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "SERVICES_BUSY"


@pytest.mark.parametrize("argv", [["status"], ["env"]])
def test_reads_take_no_lock(stack, docker, capsys, argv):
    with services_lock(root(), stack_id(stack)):
        assert cli.main(["services", argv[0], str(stack), *argv[1:]]) == 0


def test_the_lock_is_released_after_each_write(stack, docker):
    assert cli.main(["services", "stop", str(stack), "echo"]) == 0
    assert cli.main(["services", "start", str(stack), "echo"]) == 0
    with services_lock(root(), stack_id(stack)):
        pass


def test_purge_keeps_the_lock_file(stack, docker):
    identifier = stack_id(stack)
    lock = root() / LOCKS_DIR / f"{identifier}.lock"
    assert lock.is_file()
    inode = lock.stat().st_ino
    assert cli.main(["services", "down", str(stack), "--purge", "--yes"]) == 0
    assert not (root() / identifier).exists()  # the state dir is gone ...
    assert lock.stat().st_ino == inode  # ... the lock is the same file


def test_lock_file_is_private(stack):
    lock = root() / LOCKS_DIR / f"{stack_id(stack)}.lock"
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    assert stat.S_IMODE((root() / LOCKS_DIR).stat().st_mode) == 0o700


def test_list_all_skips_the_locks_directory(stack, tmp_path):
    from agentbreeder import allstacks

    result = allstacks.collect(tmp_path / "no-default-state")
    assert not any(row["code"] == "STACK_NAME_INVALID" for row in result["skipped"])


def test_unsafe_lock_files_are_refused(tmp_path):
    base = tmp_path / "root"
    with services_lock(base, "demo-12345678"):
        pass
    lock = base / LOCKS_DIR / "demo-12345678.lock"
    os.chmod(lock, 0o644)
    with pytest.raises(ServiceError, match="STATE_UNSAFE.*0600"):
        with services_lock(base, "demo-12345678"):
            pass
    lock.unlink()
    lock.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ServiceError, match="STATE_UNSAFE.*cannot open"):
        with services_lock(base, "demo-12345678"):
            pass
    lock.unlink()
    lock.mkdir()
    with pytest.raises(ServiceError, match="STATE_UNSAFE"):
        with services_lock(base, "demo-12345678"):
            pass


@pytest.mark.parametrize("bad", ["../x-12345678", "Demo-12345678", "demo", "demo-1234567g", ""])
def test_invalid_stack_ids_are_refused(tmp_path, bad):
    with pytest.raises(ServiceError, match="invalid stack id"):
        with services_lock(tmp_path, bad):
            pass
    assert not (tmp_path / LOCKS_DIR).exists()


def test_a_symlinked_or_shared_locks_directory_is_refused(tmp_path):
    base = tmp_path / "root"
    base.mkdir(mode=0o700)
    (tmp_path / "elsewhere").mkdir()
    (base / LOCKS_DIR).symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ServiceError, match="STATE_UNSAFE.*not a symlink"):
        with services_lock(base, "demo-12345678"):
            pass
    assert list((tmp_path / "elsewhere").iterdir()) == []  # nothing created through the link
    (base / LOCKS_DIR).unlink()
    (base / LOCKS_DIR).mkdir(mode=0o755)
    with services_lock(base, "demo-12345678"):  # a loose mode is tightened to 0700
        pass
    assert stat.S_IMODE((base / LOCKS_DIR).stat().st_mode) == 0o700


def test_the_lock_comes_before_the_state_is_opened(stack, docker, capsys):
    """A writer that finds the stack busy never recreates a state dir a `down --purge` removed."""
    import shutil

    identifier = stack_id(stack)
    with services_lock(root(), identifier):
        shutil.rmtree(root() / identifier)  # as the holder's purge does
        assert cli.main(["services", "stop", str(stack), "echo"]) == 2
        assert cli.main(["services", "up", str(stack), "--environment", "dev", "--yes"]) == 2
    assert "SERVICES_BUSY" in capsys.readouterr().err
    assert not (root() / identifier).exists()


def test_write_lock_names_the_engine_stack(stack):
    from agentbreeder.services.engine import write_lock

    identifier = stack_id(stack)
    with write_lock(stack):
        with pytest.raises(ServiceError, match="SERVICES_BUSY"):
            with services_lock(root(), identifier):
                pass


def test_the_lock_is_released_when_a_write_fails(stack, docker, monkeypatch, capsys):
    def fail(state, verb, service, timeout=180):
        raise ServiceError("DOCKER_FAILED", "failed")

    monkeypatch.setattr(docker, "control", fail)
    assert cli.main(["services", "restart", str(stack), "echo"]) == 2

    def broken(self, *args, **kwargs):
        raise ServiceError("GRANT_NOT_CONFIRMED", "no")

    monkeypatch.setattr(engine_module.Engine, "up", broken)
    assert cli.main(["services", "up", str(stack), "--environment", "dev"]) == 2
    with services_lock(root(), stack_id(stack)):
        pass


def test_a_lock_that_cannot_be_taken_is_coded(tmp_path, monkeypatch):
    import errno

    def flock(descriptor, operation):
        raise OSError(errno.ENOLCK, "no locks")

    monkeypatch.setattr(state_module.fcntl, "flock", flock)
    with pytest.raises(ServiceError, match="STATE_UNSAFE.*cannot lock"):
        with services_lock(tmp_path, "demo-12345678"):
            pass


def test_deployment_names_are_escaped(stack, docker, capsys, monkeypatch):
    in_use(monkeypatch, ["dev/x\x1b]52;c;eA==\x07"])
    assert cli.main(["services", "stop", str(stack), "echo"]) == 2
    err = capsys.readouterr().err
    assert "\x1b" not in err and "\\x1b]52" in err
    assert cli.main(["services", "stop", str(stack), "echo", "--yes"]) == 0
    err = capsys.readouterr().err
    assert "\x1b" not in err and "\\x1b]52" in err


def test_a_stack_renamed_while_starting_is_refused(stack, docker, monkeypatch, capsys):
    from contextlib import contextmanager

    @contextmanager
    def renamed(project, root=None):
        with services_lock(root or state_module.stacks_root(), "renamed-12345678"):
            yield "renamed-12345678"

    monkeypatch.setattr(engine_module, "write_lock", renamed)
    assert cli.main(["services", "stop", str(stack), "echo", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "STACK_CHANGED"
    assert docker.events == []
