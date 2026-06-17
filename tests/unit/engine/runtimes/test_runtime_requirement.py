"""Tests for ``runtime_support_requirement`` dev/local version handling.

The generated agent image's ``requirements.txt`` pins ``agentbreeder==<version>``
based on the locally-installed distribution. PEP-440 dev releases (``2.7.1.dev3``)
and local-segment versions (``2.7.1+gHASH``) do not exist on PyPI, so blindly
pinning to them breaks ``pip install`` inside the built container. These tests
lock in the fallback-to-base-version behavior so deploys from a dev checkout
continue to produce a buildable image.
"""

from __future__ import annotations

from engine.runtimes import base


def test_normal_release_version_is_pinned_verbatim(monkeypatch):
    """A clean PEP-440 release version flows through unchanged."""
    monkeypatch.delenv("AGENTBREEDER_RUNTIME_REQUIREMENT", raising=False)
    monkeypatch.setattr(base, "version", lambda dist: "2.7.0")
    assert base.runtime_support_requirement() == "agentbreeder==2.7.0"


def test_dev_release_falls_back_to_base_version(monkeypatch):
    """``2.7.1.dev3`` -> ``agentbreeder==2.7.1`` (the release it's heading toward)."""
    monkeypatch.delenv("AGENTBREEDER_RUNTIME_REQUIREMENT", raising=False)
    monkeypatch.setattr(base, "version", lambda dist: "2.7.1.dev3")
    assert base.runtime_support_requirement() == "agentbreeder==2.7.1"


def test_local_version_segment_falls_back_to_base_version(monkeypatch):
    """``2.7.1+g5a41d99`` (hatch-vcs dirty-checkout local segment) -> ``2.7.1``."""
    monkeypatch.delenv("AGENTBREEDER_RUNTIME_REQUIREMENT", raising=False)
    monkeypatch.setattr(base, "version", lambda dist: "2.7.1+g5a41d996c")
    assert base.runtime_support_requirement() == "agentbreeder==2.7.1"


def test_dev_with_local_segment_falls_back_to_base_version(monkeypatch):
    """The full setuptools_scm shape ``2.7.1.dev3+g5a41d996c`` -> ``2.7.1``."""
    monkeypatch.delenv("AGENTBREEDER_RUNTIME_REQUIREMENT", raising=False)
    monkeypatch.setattr(base, "version", lambda dist: "2.7.1.dev3+g5a41d996c")
    assert base.runtime_support_requirement() == "agentbreeder==2.7.1"


def test_env_override_wins_even_against_dev_version(monkeypatch):
    """``AGENTBREEDER_RUNTIME_REQUIREMENT`` always takes precedence."""

    def _should_not_be_called(_dist):  # pragma: no cover - defensive
        raise AssertionError("version() must not be consulted when override is set")

    monkeypatch.setattr(base, "version", _should_not_be_called)
    monkeypatch.setenv(
        "AGENTBREEDER_RUNTIME_REQUIREMENT",
        "agentbreeder @ file:///wheels/ab.whl",
    )
    assert (
        base.runtime_support_requirement()
        == "agentbreeder @ file:///wheels/ab.whl"
    )


def test_package_not_found_returns_unpinned(monkeypatch):
    """Source checkout with no installed dist still produces a buildable spec."""
    monkeypatch.delenv("AGENTBREEDER_RUNTIME_REQUIREMENT", raising=False)

    def _raise(_dist):
        raise base.PackageNotFoundError

    monkeypatch.setattr(base, "version", _raise)
    assert base.runtime_support_requirement() == "agentbreeder"
