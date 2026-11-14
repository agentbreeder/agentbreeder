"""scripts/changelog.py: changelog fragments, the PR check and the release fold (#893)."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("changelog", ROOT / "scripts" / "changelog.py")
changelog = importlib.util.module_from_spec(_spec)
sys.modules["changelog"] = changelog  # dataclasses look the module up while the class is built
_spec.loader.exec_module(changelog)

ENTRY = "- A thing (#901), on `main`, not in a\n  published preview.\n"
SMALL = """# Changelog

## [Unreleased]

On main, not in a published preview.

### Added

- Old added entry, on `main`, not in a published preview.

### Internal

- Old internal entry.

## [3.0.0a2] — 2026-11-11

### Added

- Released entry.
"""


def _fragment(root: Path, name: str, text: str = ENTRY) -> Path:
    directory = root / "changelog.d"
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def test_lint_command(tmp_path):
    assert _main("--root", str(tmp_path), "lint").returncode == 0
    _fragment(tmp_path, "901.added.md", "- no marking\n")
    result = _main("--root", str(tmp_path), "lint")
    assert result.returncode == 1 and "901.added.md" in result.stderr


def test_unreadable_fragment(tmp_path):
    path = _fragment(tmp_path, "901.added.md")
    path.write_bytes(b"- \xff\xfe not utf-8")
    assert "unreadable" in changelog.lint_one(path)[1][0]


def test_repository_fragments_lint_clean():
    fragments, errors = changelog.lint(ROOT)
    assert errors == []
    assert all(f.path.parent.name == "changelog.d" for f in fragments)


@pytest.mark.parametrize(
    "name", ["901.added.md", "901.fixed.md", "901.two-words.changed.md", "12.internal.md"]
)
def test_valid_names(tmp_path, name):
    fragment, errors = changelog.lint_one(_fragment(tmp_path, name))
    assert errors == [] and fragment is not None


@pytest.mark.parametrize(
    "name",
    [
        "901.md",
        "901.feature.md",
        "0901.added.md",
        "x.added.md",
        "901.Bad.added.md",
        "901.added.txt",
    ],
)
def test_invalid_names(tmp_path, name):
    fragment, errors = changelog.lint_one(_fragment(tmp_path, name))
    assert fragment is None and "name must be" in errors[0]


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("A thing, on `main`, not in a published preview.", "list item"),
        ("- A thing.", "published preview"),
        ("- A thing, on `main`, not in a published preview.\n\n### Added", "headings"),
        ("- One, not in a published preview.\n- Two, not in a published preview.", "one entry"),
        ("- One, not in a published preview.\n* Two.", "one entry"),
        ("- One, not in a published preview.\n1. Two.", "one entry"),
        ("- One, not in a published preview.\n\nA paragraph outside the item.", "one entry"),
    ],
)
def test_invalid_content(tmp_path, text, problem):
    fragment, errors = changelog.lint_one(_fragment(tmp_path, "901.added.md", text))
    assert fragment is None and any(problem in e for e in errors)


@pytest.mark.parametrize(
    "text",
    [
        "- A thing, on `main`, not in a published preview, for epic\n  #812:\n  - a sub item\n",
        "- A count, on `main`, not in a published preview, and the\n  # of tests.\n",
        "- Run it, on `main`, not in a published preview:\n\n  ```sh\n# a comment\n- x\n  ```\n",
    ],
)
def test_content_allows_indented_lines_sub_items_and_fences(tmp_path, text):
    assert changelog.lint_one(_fragment(tmp_path, "901.added.md", text))[1] == []


def test_readme_and_symlinks(tmp_path):
    _fragment(tmp_path, "README.md", "# not a fragment")
    target = _fragment(tmp_path, "901.added.md")
    (tmp_path / "changelog.d" / "902.added.md").symlink_to(target)
    fragments, errors = changelog.lint(tmp_path)
    assert [f.path.name for f in fragments] == ["901.added.md"]
    assert errors == ["changelog.d/902.added.md: not a regular file"]


# --- check -------------------------------------------------------------------------------


def test_code_change_needs_a_fragment():
    errors = changelog.check_changes([("M", "src/agentbreeder/cli.py")], [], "someone")
    assert len(errors) == 1 and "no changelog fragment" in errors[0]


def test_code_change_with_fragment_passes():
    changes = [("M", "src/agentbreeder/cli.py"), ("A", "changelog.d/901.fixed.md")]
    assert changelog.check_changes(changes, [], "someone") == []


def test_edited_fragment_counts_but_deleted_or_readme_does_not():
    assert (
        changelog.check_changes([("M", "src/x.py"), ("M", "changelog.d/901.fixed.md")], [], "")
        == []
    )
    for other in [("D", "changelog.d/901.fixed.md"), ("M", "changelog.d/README.md")]:
        assert changelog.check_changes([("M", "src/x.py"), other], [], "")


def test_direct_changelog_edit_fails_even_with_fragment():
    changes = [("M", "CHANGELOG.md"), ("A", "changelog.d/901.fixed.md"), ("M", "src/x.py")]
    errors = changelog.check_changes(changes, [], "someone")
    assert len(errors) == 1 and "edited directly" in errors[0]


def test_release_pr_may_edit_changelog_and_delete_fragments():
    changes = [("M", "CHANGELOG.md"), ("D", "changelog.d/901.fixed.md"), ("M", "src/x.py")]
    assert changelog.check_changes(changes, ["release", "cli"], "someone") == []


def test_trivial_paths_need_no_fragment():
    changes = [
        ("A", "docs/superpowers/specs/x.md"),
        ("M", "docs/superpowers/verification/M11.md"),
        ("M", "CLAUDE.md"),
        ("M", "AGENTS.md"),
        ("M", ".gitignore"),
        ("M", ".agents/skills/x/SKILL.md"),
    ]
    assert changelog.check_changes(changes, [], "someone") == []


def test_dependabot_needs_no_fragment_but_cannot_edit_changelog():
    assert changelog.check_changes([("M", "pyproject.toml")], [], "dependabot[bot]") == []
    assert changelog.check_changes([("M", "CHANGELOG.md")], [], "dependabot[bot]")


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "CHANGELOG.md").write_text(SMALL, encoding="utf-8")
    (repo / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _main(*args: str) -> subprocess.CompletedProcess:
    """Run the script's main() in-process (so coverage sees it), shaped like subprocess.run."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = changelog.main(list(args))
        except SystemExit as stop:  # argparse errors and pr_changes' refusal
            code = stop.code if isinstance(stop.code, int) else 1
            if isinstance(stop.code, str):
                print(stop.code, file=sys.stderr)
    return subprocess.CompletedProcess(args, code, out.getvalue(), err.getvalue())


def _run_check(repo: Path, labels=(), author="someone"):
    return _main(
        "--root", str(repo), "check", "--labels-json", json.dumps(list(labels)), "--author", author
    )


def test_check_reads_the_test_merge_commit(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "pr")
    (repo / "code.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "pr change")
    _git(repo, "checkout", "-q", "main")
    (repo / "other.py").write_text("y = 1\n", encoding="utf-8")  # the base moved on
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base moves")
    _git(repo, "merge", "-q", "--no-ff", "-m", "test merge", "pr")

    result = _run_check(repo)
    assert result.returncode == 1 and "this PR changes code.py." in result.stderr

    _git(repo, "reset", "-q", "--hard", "HEAD^1")
    _git(repo, "checkout", "-q", "pr")
    _fragment(repo, "901.fixed.md")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fragment")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "test merge", "pr")
    assert _run_check(repo).returncode == 0


def test_check_refuses_a_non_merge_head(tmp_path):
    result = _run_check(_repo(tmp_path))
    assert result.returncode == 1 and "not a merge commit" in result.stderr


def test_check_rejects_bad_labels_json(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(ROOT / "scripts" / "changelog.py"),
            "--root",
            str(tmp_path),
            "check",
            "--labels-json",
            '{"a": 1}',
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2 and "JSON list of strings" in result.stderr


# --- fold --------------------------------------------------------------------------------


def _fold(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return _main("--root", str(root), "fold", *extra)


def test_fold_inserts_at_top_of_headings_and_creates_missing_ones(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(SMALL, encoding="utf-8")
    _fragment(tmp_path, "901.added.md", "- New 901, on `main`, not in a published preview.\n")
    _fragment(tmp_path, "905.added.md", "- New 905, on `main`, not in a published preview.\n")
    _fragment(tmp_path, "903.fixed.md", "- Fix 903, on `main`, not in a published preview.\n")
    _fragment(tmp_path, "904.internal.md", "- Int 904, on `main`, not in a published preview.\n")
    _fragment(tmp_path, "906.changed.md", "- Chg 906, on `main`, not in a published\n  preview.\n")

    result = _fold(tmp_path)
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in (tmp_path / "changelog.d").iterdir()) == []
    assert (
        (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
        == """# Changelog

## [Unreleased]

On main, not in a published preview.

### Added

- New 905, on `main`, not in a published preview.

- New 901, on `main`, not in a published preview.

- Old added entry, on `main`, not in a published preview.

### Changed

- Chg 906, on `main`, not in a published
  preview.

### Internal

- Int 904, on `main`, not in a published preview.

- Old internal entry.

### Fixed

- Fix 903, on `main`, not in a published preview.

## [3.0.0a2] — 2026-11-11

### Added

- Released entry.
"""
    )


def test_fold_appends_a_missing_heading_at_the_end_of_unreleased(tmp_path):
    text = "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- Old.\n\n## [1.0.0]\n\n- R.\n"
    (tmp_path / "CHANGELOG.md").write_text(text, encoding="utf-8")
    _fragment(tmp_path, "9.internal.md", "- I, not in a published preview.\n")
    assert _fold(tmp_path).returncode == 0
    assert (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8") == (
        "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- Old.\n\n### Internal\n\n"
        "- I, not in a published preview.\n\n## [1.0.0]\n\n- R.\n"
    )


def test_fold_keeps_a_rule_before_the_next_release(tmp_path):
    text = "# Changelog\n\n## [Unreleased]\n\nNothing yet.\n\n---\n\n## [1.0.0]\n\n- R.\n"
    (tmp_path / "CHANGELOG.md").write_text(text, encoding="utf-8")
    _fragment(tmp_path, "9.added.md", "- A, not in a published preview.\n")
    _fragment(tmp_path, "9.fixed.md", "- F, not in a published preview.\n")
    assert _fold(tmp_path).returncode == 0
    assert (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8") == (
        "# Changelog\n\n## [Unreleased]\n\nNothing yet.\n\n### Added\n\n"
        "- A, not in a published preview.\n\n### Fixed\n\n- F, not in a published preview.\n\n"
        "---\n\n## [1.0.0]\n\n- R.\n"
    )


def test_fold_without_unreleased_fails_cleanly(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.0.0]\n", encoding="utf-8")
    _fragment(tmp_path, "9.added.md", "- A, not in a published preview.\n")
    result = _fold(tmp_path)
    assert result.returncode == 1 and "no '## [Unreleased]' heading" in result.stderr
    assert "Traceback" not in result.stderr


def test_fold_dry_run_writes_nothing(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(SMALL, encoding="utf-8")
    fragment = _fragment(tmp_path, "901.fixed.md")
    result = _fold(tmp_path, "--dry-run")
    assert result.returncode == 0 and "### Fixed\n\n- A thing (#901)" in result.stdout
    assert fragment.exists() and (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8") == SMALL


def test_fold_refuses_invalid_fragments(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(SMALL, encoding="utf-8")
    _fragment(tmp_path, "901.fixed.md", "- no marking\n")
    result = _fold(tmp_path)
    assert result.returncode == 1 and "published preview" in result.stderr
    assert (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8") == SMALL


def test_fold_on_the_real_changelog_only_adds_the_fragments(tmp_path):
    shutil.copy(ROOT / "CHANGELOG.md", tmp_path / "CHANGELOG.md")
    shutil.copytree(ROOT / "changelog.d", tmp_path / "changelog.d")
    fragments, _ = changelog.lint(tmp_path)
    assert _fold(tmp_path).returncode == 0
    before = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    after = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    added = list(after)
    for line in before:  # every old line survives, in order
        added.remove(line)
    expected = [line for f in fragments for line in f.text.splitlines()]
    assert sorted(line for line in added if line) == sorted(expected)


# --- the acceptance: two PRs with fragments and command-doc edits merge cleanly ----------------


def test_two_prs_with_fragments_and_command_doc_edits_merge_without_conflict(tmp_path):
    repo = _repo(tmp_path)
    shutil.copytree(ROOT / "docs", repo / "docs", ignore=shutil.ignore_patterns("superpowers"))
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "docs")
    for branch, issue, page in [("a", 901, "logs"), ("b", 902, "watch")]:
        _git(repo, "checkout", "-q", "-b", branch, "main")
        _fragment(
            repo, f"{issue}.added.md", f"- Thing {issue}, on `main`, not in a published preview.\n"
        )
        doc = repo / "docs" / "commands" / f"{page}.md"
        lines = doc.read_text(encoding="utf-8").splitlines()
        lines.insert(-1, f"New paragraph from {issue}.\n")
        doc.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", branch)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "a", "a")
    _git(repo, "merge", "-q", "--no-ff", "-m", "b", "b")  # raises on a conflict
    assert sorted(p.name for p in (repo / "changelog.d").iterdir()) == [
        "901.added.md",
        "902.added.md",
    ]
