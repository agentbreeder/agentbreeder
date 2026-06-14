import pytest

from engine.sandbox.base import (
    ExecResult,
    SandboxDisabledError,
    select_sandbox_mode,
)


def test_exec_result_defaults():
    r = ExecResult(stdout="hi", stderr="", exit_code=0)
    assert r.timed_out is False
    assert r.ok is True


def test_exec_result_nonzero_not_ok():
    assert ExecResult(stdout="", stderr="boom", exit_code=1).ok is False


def test_select_sandbox_mode_defaults_local(monkeypatch):
    monkeypatch.delenv("AGENTBREEDER_SANDBOX", raising=False)
    assert select_sandbox_mode() == "local"


def test_select_sandbox_mode_reads_env(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_SANDBOX", "cloud")
    assert select_sandbox_mode() == "cloud"


def test_select_sandbox_mode_rejects_unknown(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_SANDBOX", "bogus")
    with pytest.raises(SandboxDisabledError):
        select_sandbox_mode()
