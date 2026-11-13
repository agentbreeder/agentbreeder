"""Console UI tests (Playwright, #816). Opt-in: AGENTBREEDER_UI_TEST=1 with the `ui` extra
installed and a browser available (`playwright install chromium`, or `--browser-channel chrome`).
CI runs them in one job against the built wheel."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

if os.environ.get("AGENTBREEDER_UI_TEST") != "1":
    collect_ignore_glob = ["test_*.py"]
else:
    pytest.importorskip("playwright")

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"


class Console:
    """A running console server (the installed package) driven by the fake CLI."""

    def __init__(self, process: subprocess.Popen, info: dict, state: Path):
        self.process = process
        self.origin = info["origin"]
        self.login = info["login"]
        self.state = state

    def project(self, name: str = "demo") -> Path:
        """A copy of a fixture project under the test's state directory."""
        target = self.state / "projects" / name
        if not target.exists():
            shutil.copytree(FIXTURES / "projects" / name, target)
        return target


@pytest.fixture
def console(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path),
    }
    process = subprocess.Popen(
        [sys.executable, "-I", str(HERE / "launcher.py"), str(state)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        line = process.stdout.readline()
        if not line:
            raise RuntimeError(f"launcher exited with {process.wait(10)}")
        info = json.loads(line)
        if os.environ.get("AGENTBREEDER_UI_WHEEL") == "1":
            # CI: the server under test is the installed wheel, not the checkout.
            assert "site-packages" in Path(info["package"]).parts, info["package"]
        yield Console(process, info, state)
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)


@pytest.fixture
def violations(context):
    """CSP and Trusted Types violations seen by any page in the context, from the
    `securitypolicyviolation` event (Playwright's init script is not subject to the page's CSP)."""
    context.add_init_script(
        "window.__violations = [];"
        "document.addEventListener('securitypolicyviolation', (e) => window.__violations.push("
        "{directive: e.effectiveDirective, blocked: e.blockedURI, sample: e.sample}));"
    )
    return lambda page: page.evaluate("window.__violations")
