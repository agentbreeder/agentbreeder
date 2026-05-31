from engine.runtimes import base


def test_returns_pinned_agentbreeder_when_installed(monkeypatch):
    monkeypatch.delenv("AGENTBREEDER_RUNTIME_REQUIREMENT", raising=False)
    monkeypatch.setattr(base, "version", lambda dist: "9.9.9")
    assert base.runtime_support_requirement() == "agentbreeder==9.9.9"


def test_falls_back_to_unpinned_when_dist_absent(monkeypatch):
    monkeypatch.delenv("AGENTBREEDER_RUNTIME_REQUIREMENT", raising=False)

    def _raise(_dist):
        raise base.PackageNotFoundError

    monkeypatch.setattr(base, "version", _raise)
    assert base.runtime_support_requirement() == "agentbreeder"


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_RUNTIME_REQUIREMENT", "agentbreeder @ file:///wheels/ab.whl")
    assert base.runtime_support_requirement() == "agentbreeder @ file:///wheels/ab.whl"


def test_empty_override_opts_out(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_RUNTIME_REQUIREMENT", "   ")
    assert base.runtime_support_requirement() is None


def test_pinned_version_override_is_returned_verbatim(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_RUNTIME_REQUIREMENT", "agentbreeder==2.5.0")
    assert base.runtime_support_requirement() == "agentbreeder==2.5.0"
