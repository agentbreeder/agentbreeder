"""Pluggable execution backends for CloudSandbox.

CloudSandbox is a thin Sandbox-Protocol client; the backend is the substrate
that actually runs code (e2b microVM in prod, FakeBackend in tests). Selected by
AGENTBREEDER_SANDBOX_BACKEND (default 'e2b').
"""

from __future__ import annotations

import os

from engine.sandbox.backends.base import SandboxBackend


def make_backend_from_env() -> SandboxBackend:
    name = os.environ.get("AGENTBREEDER_SANDBOX_BACKEND", "e2b").strip().lower()
    if name == "e2b":
        from engine.sandbox.backends.e2b import E2BBackend

        return E2BBackend()
    if name == "fake":
        from engine.sandbox.backends.fake import FakeBackend

        return FakeBackend()
    raise ValueError(f"unknown AGENTBREEDER_SANDBOX_BACKEND={name!r}")
