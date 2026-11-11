"""2.x notice (spec §5.6, plan S6): doctor reports 2.x home state; README/PyPI description."""

import fnmatch
import json
import re
import tomllib
from pathlib import Path

import pytest

from agentbreeder import cli, doctor
from agentbreeder.commands import doctor as doctor_command

ROOT = Path(__file__).parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # undo the conftest guard
    monkeypatch.setattr(doctor_command, "check_legacy", doctor.check_legacy)
    monkeypatch.setattr(doctor_command, "check_docker", lambda: [])
    monkeypatch.setattr(doctor_command, "check_compose", lambda: [])
    return tmp_path


def make(base: Path, name: str) -> None:
    path = base / name.rstrip("/")
    if name.endswith("/"):
        path.mkdir(parents=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")


def test_no_home_state_reports_nothing(home):
    assert doctor.check_legacy() == []


def test_three_x_state_alone_is_not_flagged(home):
    for name in ("local/", "stacks/", "operator.yaml", "oidc-token"):
        make(home / ".agentbreeder", name)
    assert doctor.check_legacy() == []


def three_x_home_names() -> set[str]:
    """Every name 3.x puts under ~/.agentbreeder, scanned from the package source.

    Limit: only same-line paths (`Path.home() / ".agentbreeder" / "x"`) are seen; keep 3.x home
    paths in that form, or extend this scan.
    """
    pattern = re.compile(r"\.agentbreeder[\"/ ]+[\"/ ]*\"?([A-Za-z0-9._-]+)")
    found = set()
    for path in (ROOT / "src/agentbreeder").rglob("*.py"):
        if "examples" in path.parts:
            continue
        for line in path.read_text().splitlines():
            if "home()" in line and ".agentbreeder" in line:
                found.update(pattern.findall(line))
    return found


def test_every_three_x_home_path_is_known():
    # A new ~/.agentbreeder path in 3.x shows up here and must be checked against the markers.
    assert three_x_home_names() == {"center", "local", "stacks", "operator.yaml", "oidc-token"}


def test_three_x_paths_do_not_collide_with_markers():
    names = three_x_home_names()
    for marker in doctor.LEGACY_MARKERS:
        assert not any(fnmatch.fnmatch(name, marker.rstrip("/")) for name in names), marker


@pytest.mark.parametrize(
    "marker",
    [
        "state.json",
        "workspace.yaml",
        "context.json",
        "providers.json",
        "providers.local.yaml",
        "quickstart-runtime.json",
        "registry/",
        "evals/",
        "builders/",
        ".env",
        "docker-compose.studio.yml",
        "docker-compose.ui.yml",
    ],
)
def test_each_two_x_marker_is_reported(home, marker):
    make(home / ".agentbreeder", marker)
    [check] = doctor.check_legacy()
    assert check.info and check.passed and not check.required
    assert check.message.startswith("2.x state found in ~/.agentbreeder (")
    expected = "docker-compose*.yml" if marker.startswith("docker-compose") else marker
    assert expected in check.message
    assert check.message.endswith("3.x does not read or change it.")


@pytest.mark.parametrize("name", ["infra-state.json", "layout.json"])
def test_project_local_files_are_not_home_markers(home, name):
    make(home / ".agentbreeder", name)  # 2.x wrote these per project, not in $HOME
    assert doctor.check_legacy() == []


def test_globbed_names_are_not_echoed(home):
    make(home / ".agentbreeder", "docker-compose\x1b[2J.yml")
    [check] = doctor.check_legacy()
    assert "\x1b" not in check.message and "docker-compose*.yml" in check.message


def test_dangling_symlink_counts_and_is_not_followed(home):
    (home / ".agentbreeder").mkdir()
    (home / ".agentbreeder" / "state.json").symlink_to(home / "missing")
    [check] = doctor.check_legacy()
    assert "state.json" in check.message


def test_home_symlinked_state_dir_is_not_followed(home, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    make(elsewhere, "state.json")
    (home / ".agentbreeder").symlink_to(elsewhere)
    assert doctor.check_legacy() == []


def test_unreadable_home_is_skipped(monkeypatch):
    def fail():
        raise RuntimeError("no home")

    monkeypatch.setattr(doctor.Path, "home", staticmethod(fail))
    assert doctor.check_legacy() == []


def test_doctor_prints_info_and_keeps_exit_code(home, capsys):
    make(home / ".agentbreeder", "state.json")
    make(home / ".agentbreeder", "registry/")
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert (
        "INFO 2.x state: 2.x state found in ~/.agentbreeder (registry/, state.json); "
        "3.x does not read or change it." in out
    )


def test_doctor_json_marks_info(home, capsys):
    make(home / ".agentbreeder", "workspace.yaml")
    assert cli.main(["doctor", "--json"]) == 0
    [entry] = json.loads(capsys.readouterr().out)
    assert entry["info"] is True and entry["name"] == "2.x state"


def test_readme_is_the_pypi_description_and_opens_with_the_notice():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["readme"] == "README.md"
    head = (ROOT / "README.md").read_text().split("\n## ", 1)[0]
    assert "not an upgrade" in head
    assert "agentbreeder<3" in head
    assert "2.7.3" in head and "unmaintained" in head and "security" in head
    assert "https://www.agentbreeder.io/docs/legacy-2x" in head


def test_readme_links_work_on_pypi():
    # PyPI renders the README outside the repo, so every link must be absolute.
    links = re.findall(r"\]\(([^)]+)\)", (ROOT / "README.md").read_text())
    assert links
    assert [link for link in links if not link.startswith(("https://", "#"))] == []
    blob = "https://github.com/agentbreeder/agentbreeder/blob/main/"
    missing = [link for link in links if link.startswith(blob)]
    assert [link for link in missing if not (ROOT / link[len(blob) :]).exists()] == []
