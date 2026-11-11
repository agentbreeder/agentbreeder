"""Version source (#752): one value, read by setuptools, the CLI and the npm launcher."""

import tomllib
from pathlib import Path

import pytest

from agentbreeder import __version__
from agentbreeder._version import npm_version
from agentbreeder.cli import main

ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_the_preview_version_sorts_above_2x():
    from packaging.version import Version

    assert __version__ == "3.0.0a2"
    assert Version(__version__) > Version("2.7.3")
    assert Version(__version__).is_prerelease


def test_pyproject_reads_the_version_from_the_package():
    project = _pyproject()["project"]
    assert "version" not in project
    assert "version" in project["dynamic"]
    dynamic = _pyproject()["tool"]["setuptools"]["dynamic"]["version"]
    assert dynamic == {"attr": "agentbreeder.__version__"}


def test_installed_metadata_matches():
    from importlib.metadata import version

    assert version("agentbreeder") == __version__


def test_cli_version_matches(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["--version"])
    assert raised.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


@pytest.mark.parametrize(
    ("pypi", "npm"),
    [
        ("3.0.0a1", "3.0.0-alpha.1"),
        ("3.0.0b2", "3.0.0-beta.2"),
        ("3.0.0rc1", "3.0.0-rc.1"),
        ("3.0.0", "3.0.0"),
        ("3.1.12a10", "3.1.12-alpha.10"),
    ],
)
def test_npm_version_mapping(pypi, npm):
    assert npm_version(pypi) == npm


@pytest.mark.parametrize(
    "bad", ["3.0", "3.0.0.dev0", "3.0.0a1.post1", "3.0.0+local", "v3.0.0", "", "3.0.0a1\n"]
)
def test_npm_version_refuses_what_it_cannot_map(bad):
    with pytest.raises(ValueError):
        npm_version(bad)


def test_metadata_marks_the_alpha():
    project = _pyproject()["project"]
    assert "Development Status :: 3 - Alpha" in project["classifiers"]
    assert "Development" not in project["description"].split()[0]
    assert {"Homepage", "Documentation", "Source", "Legacy 2.x"} <= set(project["urls"])
