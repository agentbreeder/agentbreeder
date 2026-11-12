"""scripts/release_check.py and scripts/install_smoke.py (#752, plan S8)."""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_check = _load("release_check")
install_smoke = _load("install_smoke")

from agentbreeder import __version__  # noqa: E402

RELEASE = {
    "pypi_version": __version__,
    "exclude_newer": "2026-11-10T17:00:00Z",
    "window_end": "2026-12-10T17:00:00Z",
    "min_uv": "0.11.6",
}


def test_tag_must_equal_v_plus_version():
    release_check.check_tag(f"v{__version__}")
    for bad in (__version__, f"v{__version__}.post1", "v2.7.3", f"V{__version__}"):
        with pytest.raises(release_check.CheckFailed):
            release_check.check_tag(bad)


def test_main_reports_a_bad_tag(capsys):
    assert release_check.main(["tag", "v0.0.1"]) == 1
    assert "does not match" in capsys.readouterr().err
    assert release_check.main(["tag", f"v{__version__}"]) == 0


@pytest.mark.parametrize(
    "tag_time",
    ["2026-11-10T17:00:00Z", "2026-11-12T09:00:00-08:00", "2026-11-17T16:59:59+00:00"],
)
def test_npm_window_accepts_a_fresh_window(tag_time):
    release_check.check_npm_window(tag_time, RELEASE)


@pytest.mark.parametrize(
    ("tag_time", "reason"),
    [
        ("2026-11-10T16:59:59Z", "after the tag"),
        ("2026-11-17T17:00:01Z", "more than 7 days"),
        ("2026-11-12T10:00:00", "no UTC offset"),
        ("soon", "not an ISO 8601"),
    ],
)
def test_npm_window_refuses(tag_time, reason):
    with pytest.raises(release_check.CheckFailed, match=reason):
        release_check.check_npm_window(tag_time, RELEASE)


def test_npm_window_refuses_a_window_ending_before_the_tag():
    short = {**RELEASE, "window_end": "2026-11-11T00:00:00Z"}
    with pytest.raises(release_check.CheckFailed, match="window_end"):
        release_check.check_npm_window("2026-11-12T00:00:00Z", short)


def test_npm_window_reads_the_committed_release_json():
    committed = json.loads((ROOT / "npm" / "release.json").read_text())
    assert release_check.main(["npm-window", "--tag-time", committed["exclude_newer"]]) == 0


def _sdist(path: Path, members: dict[str, bytes], mtime: int = 0, symlink: str | None = None):
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(data), mtime
            archive.addfile(info, io.BytesIO(data))
        if symlink:
            info = tarfile.TarInfo(symlink)
            info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
            archive.addfile(info)
    return path


def test_sdists_compare_by_content_not_archive_bytes(tmp_path):
    a = _sdist(tmp_path / "a.tar.gz", {"p/x": b"1", "p/y": b"2"}, mtime=1)
    b = _sdist(tmp_path / "b.tar.gz", {"p/y": b"2", "p/x": b"1"}, mtime=2)
    assert a.read_bytes() != b.read_bytes()
    assert release_check.main(["compare-sdists", str(a), str(b)]) == 0


@pytest.mark.parametrize(
    ("second", "reason"),
    [({"p/x": b"1"}, "members"), ({"p/x": b"1", "p/y": b"3"}, "content")],
)
def test_sdists_that_differ_are_refused(tmp_path, second, reason):
    a = _sdist(tmp_path / "a.tar.gz", {"p/x": b"1", "p/y": b"2"})
    b = _sdist(tmp_path / "b.tar.gz", second)
    with pytest.raises(release_check.CheckFailed, match=reason):
        release_check.compare_sdists(a, b)


def test_sdist_with_a_link_is_refused(tmp_path):
    a = _sdist(tmp_path / "a.tar.gz", {"p/x": b"1"}, symlink="p/link")
    with pytest.raises(release_check.CheckFailed, match="not a file"):
        release_check.compare_sdists(a, a)


def _hashed(constraints: str) -> str:
    """The committed constraints as `uv pip compile --generate-hashes` would print them."""
    lines = []
    for line in constraints.splitlines():
        if line and not line.lstrip().startswith("#"):
            lines.append(f"{line} \\\n    --hash=sha256:{'a' * 64}")
    return "\n".join(lines) + "\n"


HASHED = _hashed((ROOT / "npm" / "constraints.txt").read_text())


def _artifacts(tmp_path):
    wheel = tmp_path / f"agentbreeder-{__version__}-py3-none-any.whl"
    sdist = tmp_path / f"agentbreeder-{__version__}.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    return wheel, sdist


def test_requirements_pin_agentbreeder_to_the_built_files(tmp_path, monkeypatch):
    wheel, sdist = _artifacts(tmp_path)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=HASHED)

    monkeypatch.setattr(release_check.shutil, "which", lambda name: "/stub/uv")
    out = tmp_path / "requirements.txt"
    release_check.write_requirements(wheel, sdist, out, run=fake_run)
    text = out.read_text()
    import hashlib

    assert f"agentbreeder[eval]=={__version__} \\\n" in text
    assert f"--hash=sha256:{hashlib.sha256(b'wheel').hexdigest()}" in text
    assert f"--hash=sha256:{hashlib.sha256(b'sdist').hexdigest()}" in text
    assert text.endswith(HASHED)
    exclude_newer = json.loads((ROOT / "npm" / "release.json").read_text())["exclude_newer"]
    assert {"--generate-hashes", "--universal", exclude_newer} <= set(calls[0])


def test_requirements_refuse_other_files_and_a_resolved_self_pin(tmp_path, monkeypatch):
    wheel, sdist = _artifacts(tmp_path)
    monkeypatch.setattr(release_check.shutil, "which", lambda name: "/stub/uv")
    near = tmp_path / f"agentbreeder-{__version__}0.tar.gz"
    for args in ((sdist, wheel), (wheel, near)):
        with pytest.raises(release_check.CheckFailed, match="are not the agentbreeder"):
            release_check.write_requirements(*args, tmp_path / "r.txt")

    def pins_self(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="agentbreeder==2.7.3\n")

    with pytest.raises(release_check.CheckFailed, match="pins agentbreeder"):
        release_check.write_requirements(wheel, sdist, tmp_path / "r.txt", run=pins_self)


def test_requirements_must_match_the_npm_constraints(tmp_path, monkeypatch):
    wheel, sdist = _artifacts(tmp_path)
    monkeypatch.setattr(release_check.shutil, "which", lambda name: "/stub/uv")

    def drifted(argv, **kwargs):
        stdout = HASHED.replace("pyyaml==6.0.3", "pyyaml==6.0.2")
        return subprocess.CompletedProcess(argv, 0, stdout=stdout)

    with pytest.raises(release_check.CheckFailed, match="npm/constraints.txt"):
        release_check.write_requirements(wheel, sdist, tmp_path / "r.txt", run=drifted)


def test_requirements_show_uv_errors(tmp_path, monkeypatch):
    wheel, sdist = _artifacts(tmp_path)
    monkeypatch.setattr(release_check.shutil, "which", lambda name: "/stub/uv")

    def broken(argv, **kwargs):
        raise subprocess.CalledProcessError(2, argv, stderr="No solution found")

    with pytest.raises(release_check.CheckFailed, match="No solution found"):
        release_check.write_requirements(wheel, sdist, tmp_path / "r.txt", run=broken)


def test_requirements_need_uv(tmp_path, monkeypatch):
    wheel, sdist = _artifacts(tmp_path)
    monkeypatch.setattr(release_check.shutil, "which", lambda name: None)
    args = ["requirements", "--wheel", str(wheel), "--sdist", str(sdist), "--out", "x"]
    assert release_check.main(args) == 1


# install_smoke: the parts that do not need an installed wheel.


def test_install_smoke_refuses_the_checkout():
    with pytest.raises(SystemExit, match="imported the checkout"):
        install_smoke.check_installed(__version__)


def test_install_smoke_hardlink_check(tmp_path):
    tmp_path = tmp_path / "builtin"
    tmp_path.mkdir()
    (tmp_path / "a").write_text("x")
    with pytest.raises(SystemExit, match="1 of 1 built-in files are not hardlinked"):
        install_smoke.check_hardlinked(tmp_path)
    os.link(tmp_path / "a", tmp_path / "b")
    install_smoke.check_hardlinked(tmp_path)
    (tmp_path / "c").write_text("copied, not linked")
    with pytest.raises(SystemExit, match="1 of 3"):
        install_smoke.check_hardlinked(tmp_path)
    (tmp_path / "c").unlink()
    with pytest.raises(SystemExit, match="no built-in files"):
        install_smoke.check_hardlinked(tmp_path / "c")


def test_install_smoke_locked_build_on_a_copied_northwind(tmp_path):
    from agentbreeder import example

    project = example.copy("northwind", tmp_path / "nw")
    config = install_smoke.operator_config(tmp_path / "smoke-operator")
    assert oct(config.stat().st_mode & 0o777) == "0o600"
    install_smoke.locked_build(project, config)


def test_install_smoke_next_and_new_agent_checks(tmp_path, capsys):
    from agentbreeder import cli, example

    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "linked").symlink_to(real)  # as macOS temp dirs: /var -> /private/var
    tmp_path = tmp_path / "linked"
    project = example.copy("northwind", tmp_path / "nw")
    assert cli.main(["next", str(project), "--json"]) == 0
    install_smoke.check_next(capsys.readouterr().out, project)
    with pytest.raises(SystemExit, match="did not describe"):
        install_smoke.check_next(json.dumps({"project": "elsewhere"}), project)
    with pytest.raises(SystemExit, match="project step"):
        install_smoke.check_next(
            json.dumps({"project": str(project.absolute()), "complete": False, "steps": []}),
            project,
        )
    assert "the checklist parses" in capsys.readouterr().out
    target = tmp_path / "hello"
    argv = ["new", "agent", "hello", "--model", "gemini", "--dir", str(target), "--dry-run"]
    assert cli.main([*argv, "--json"]) == 0
    output = capsys.readouterr().out
    install_smoke.check_new(output, target)
    with pytest.raises(SystemExit, match="did not list"):
        install_smoke.check_new(json.dumps({"dry_run": True, "files": []}), target)
    target.mkdir()
    with pytest.raises(SystemExit, match="wrote to its destination"):
        install_smoke.check_new(output, target)


def test_install_smoke_refuses_to_run_from_the_checkout_interpreter():
    """The full script runs only in CI (install-smoke) and the release build job, against an
    installed wheel; from this interpreter it must fail, never pass."""
    result = subprocess.run(
        [sys.executable, "-I", "scripts/install_smoke.py", "--version", __version__],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:  # an editable install resolves to the checkout and must fail
        pytest.fail("install_smoke passed against the checkout")
    assert "install_smoke:" in result.stderr or "ModuleNotFoundError" in result.stderr


def test_sdists_differing_only_in_exec_bit_are_refused(tmp_path):
    def sdist(path, mode):
        with tarfile.open(path, "w:gz") as archive:
            info = tarfile.TarInfo("p/run.sh")
            info.size, info.mode = 2, mode
            archive.addfile(info, io.BytesIO(b"hi"))
        return path

    a, b = sdist(tmp_path / "a.tar.gz", 0o644), sdist(tmp_path / "b.tar.gz", 0o755)
    with pytest.raises(release_check.CheckFailed, match="exec bit"):
        release_check.compare_sdists(a, b)


def test_sdist_with_duplicate_members_is_refused(tmp_path):
    path = tmp_path / "dup.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for data in (b"1", b"2"):
            info = tarfile.TarInfo("p/x")
            info.size = 1
            archive.addfile(info, io.BytesIO(data))
    with pytest.raises(release_check.CheckFailed, match="appears twice"):
        release_check.compare_sdists(path, path)


def test_workflows_call_release_check_with_real_subcommands():
    import re

    import yaml

    commands = {"tag", "npm-window", "compare-sdists", "requirements"}
    seen = set()
    for name in ("release.yml", "ci.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text()
        yaml.safe_load(text)  # parses
        seen |= set(re.findall(r"scripts/release_check\.py ([a-z-]+)", text))
    assert seen and seen <= commands, seen - commands
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert commands <= seen
    assert "scripts/install_smoke.py" in release and "scripts/upgrade_guard.py" in release
