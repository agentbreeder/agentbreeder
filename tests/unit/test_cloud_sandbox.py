"""Unit tests for CloudSandbox + the SandboxBackend abstraction (no network)."""

from __future__ import annotations

from engine.sandbox.backends.base import SandboxBackend
from engine.sandbox.backends.fake import FakeBackend


def test_fake_backend_satisfies_protocol():
    assert isinstance(FakeBackend(), SandboxBackend)
