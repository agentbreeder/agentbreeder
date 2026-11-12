"""The launcher form in printed commands, and the one `Next:` helper (#819, guided §9.2 #9)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentbreeder import hints, launcher

PYTHON = "/opt/py/bin/python"


def script(path: Path, interpreter: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{interpreter}\nimport agentbreeder\n")
    path.chmod(0o755)
    return str(path)


def prefix(env=None, *, which=None, sys_prefix="/opt/py", executable=PYTHON):
    return launcher.prefix(
        env or {},
        executable=executable,
        sys_prefix=sys_prefix,
        which=which or (lambda name: None),
        version="3.0.0a3",
    )


@pytest.mark.parametrize("version", ["3.0.0", "3.0.0-alpha.2", "10.20.30-rc.1", "0.1.0-beta.0"])
def test_npx_form_needs_the_enum_and_a_strict_semver(version):
    env = {"AGENTBREEDER_LAUNCHER": "npx", "AGENTBREEDER_LAUNCHER_VERSION": version}
    assert prefix(env) == f"npx agentbreeder@{version}"


@pytest.mark.parametrize(
    "launcher_value, version",
    [
        ("npx", "3.0.0; rm -rf ~"),
        ("npx", "$(id)"),
        ("npx", "3.0.0-alpha"),
        ("npx", "03.0.0"),
        ("npx", "3.0.0a2"),
        ("npx", "3.0.0-alpha.2 "),
        ("npx", "3.0.0\n"),
        ("npx", ""),
        ("NPX", "3.0.0"),
        ("npx ", "3.0.0"),
        ("npm", "3.0.0"),
        ("", "3.0.0"),
    ],
)
def test_injected_launcher_env_is_ignored(launcher_value, version):
    env = {"AGENTBREEDER_LAUNCHER": launcher_value, "AGENTBREEDER_LAUNCHER_VERSION": version}
    form = prefix(env)
    assert form == f"{PYTHON} -m agentbreeder"
    assert version.strip() not in form or not version.strip()


def test_uvx_form_when_the_interpreter_is_in_the_uv_cache(tmp_path):
    cache = tmp_path / "uv-cache"
    env_prefix = cache / "archive-v0" / "abc123"
    env_prefix.mkdir(parents=True)
    form = prefix({"UV_CACHE_DIR": str(cache)}, sys_prefix=str(env_prefix))
    assert form == "uvx --from 'agentbreeder[eval]==3.0.0a3' agentbreeder"
    xdg = tmp_path / "xdg"
    (xdg / "uv" / "archive-v0" / "x").mkdir(parents=True)
    form = prefix({"XDG_CACHE_HOME": str(xdg)}, sys_prefix=str(xdg / "uv" / "archive-v0" / "x"))
    assert form.startswith("uvx --from ")
    # Outside the cache: not uvx.
    assert not prefix({"UV_CACHE_DIR": str(cache)}, sys_prefix=str(tmp_path)).startswith("uvx")


def test_npx_wins_over_uvx():
    # npx runs uvx: the npx form is what the user typed.
    env = {
        "AGENTBREEDER_LAUNCHER": "npx",
        "AGENTBREEDER_LAUNCHER_VERSION": "3.0.0-alpha.3",
        "UV_CACHE_DIR": "/opt",
    }
    assert prefix(env) == "npx agentbreeder@3.0.0-alpha.3"


def test_bare_form_when_path_resolves_to_this_interpreter(tmp_path):
    venv = tmp_path / "venv" / "bin"
    executable = str(venv / "python")
    found = script(venv / "agentbreeder", executable)
    assert prefix(
        which=lambda n: found if n == "agentbreeder" else None, executable=executable
    ) == ("agentbreeder")
    # `uv tool install`: a symlink on PATH into the tool's own bin directory.
    link = tmp_path / "local-bin" / "agentbreeder"
    link.parent.mkdir()
    link.symlink_to(found)
    assert prefix(which=lambda n: str(link), executable=executable) == "agentbreeder"
    # The shebang names this interpreter although the script lives elsewhere.
    other = script(tmp_path / "elsewhere" / "agentbreeder", executable)
    assert prefix(which=lambda n: other, executable=executable) == "agentbreeder"


def test_a_stale_agentbreeder_on_path_is_not_used(tmp_path):
    stale = script(tmp_path / "old" / "bin" / "agentbreeder", "/usr/bin/python3")
    executable = str(tmp_path / "new" / "bin" / "python")
    form = prefix(
        which=lambda name: stale if name == "agentbreeder" else None, executable=executable
    )
    assert form == f"{executable} -m agentbreeder"


def test_python_m_form(tmp_path):
    assert prefix(which=lambda name: PYTHON if name == "python" else None) == (
        "python -m agentbreeder"
    )
    spaced = str(tmp_path / "my env" / "python")
    assert prefix(executable=spaced) == f"'{spaced}' -m agentbreeder"


def test_next_line_quotes_and_adds_stack_flags(monkeypatch):
    monkeypatch.setattr(hints, "_prefix", lambda: "npx agentbreeder@3.0.0-alpha.3")
    line = hints.next_line(
        "release", Path("/home/a b/agent.yaml"), stack=Path("/home/a b"), environment="dev"
    )
    assert line == (
        "Next: npx agentbreeder@3.0.0-alpha.3 release '/home/a b/agent.yaml' "
        "--stack '/home/a b' --environment dev"
    )
    assert hints.next_line("release", "agent.yaml") == (
        "Next: npx agentbreeder@3.0.0-alpha.3 release agent.yaml"
    )
    assert hints.next_note("read README.md") == "Next: read README.md"


def _printed(env: dict) -> str:
    code = "from agentbreeder import hints; print(hints.command('release', 'agent.yaml'))"
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def test_launcher_end_to_end_in_a_real_interpreter(tmp_path):
    src = str(Path(__file__).resolve().parents[1] / "src")
    base = {"HOME": str(tmp_path), "PYTHONPATH": src, "UV_CACHE_DIR": str(tmp_path / "cache")}
    npx = {
        **base,
        "PATH": "/usr/bin:/bin",
        "AGENTBREEDER_LAUNCHER": "npx",
        "AGENTBREEDER_LAUNCHER_VERSION": "3.0.0-alpha.3",
    }
    assert _printed(npx) == "npx agentbreeder@3.0.0-alpha.3 release agent.yaml"
    assert _printed({**npx, "AGENTBREEDER_LAUNCHER_VERSION": "1.0.0 && id"}).endswith(
        "-m agentbreeder release agent.yaml"
    )
    bindir = Path(sys.executable).parent
    if (bindir / "agentbreeder").exists():  # an installed console script beside this python
        on_path = {**base, "PATH": f"{bindir}{os.pathsep}/usr/bin:/bin"}
        assert _printed(on_path) == "agentbreeder release agent.yaml"
