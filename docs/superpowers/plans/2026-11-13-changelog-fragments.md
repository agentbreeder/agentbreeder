# Plan: changelog fragments and per-command docs (#893)

Spec: `docs/superpowers/specs/2026-11-13-changelog-fragments-design.md`. One PR, branch
`feat/893-changelog-fragments`.

1. `scripts/changelog.py` (`lint`, `check`, `fold`) and `tests/test_changelog_fragments.py`
   (name and content rules, `check` against fake diffs and labels, `fold` on a copy of today's
   `CHANGELOG.md`, a two-PR merge in a temporary git repository).
2. `changelog.d/README.md` and this PR's fragment `changelog.d/893.internal.md`.
3. `.github/workflows/changelog.yml`; actionlint and zizmor clean.
4. Split `docs/commands.md` into `docs/commands/<command>.md`; `scripts/commands_index.py`;
   `tests/test_commands_docs.py` (index current, one file per parser command, links resolve, and
   a one-off check that every sentence of the old page survives).
5. Fix references: `src/agentbreeder/newagent.py` comment, `docs/events.md`.
6. `docs/contributing.md`: the fragment and command-doc rules.
7. Run the touched tests and `tests/test_cli_help_golden.py` on 3.11, 3.12, 3.13; ruff; gate;
   Sonnet review loop; PR with the review table.
