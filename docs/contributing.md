# Focused rebuild reference

Read `../CONTRIBUTING.md` and `../CLAUDE.md` for current setup, review, privacy and delivery requirements.

## Changelog entries (#893)

Do not edit `CHANGELOG.md` in a feature or fix PR. Add one file per entry under `changelog.d/`,
named `<issue>.<type>.md` (`added`, `changed`, `fixed` or `internal`), holding one Markdown list
item that says it is on `main`, not in a published preview. See `../changelog.d/README.md`.
`python scripts/changelog.py lint` checks the fragments. The `Changelog fragments` CI workflow
fails a PR that edits `CHANGELOG.md` without the `release` label, or that changes anything
outside `docs/superpowers/`, `changelog.d/`, `CLAUDE.md`, `AGENTS.md`, `.agents/` and
`.gitignore` without a fragment. The release PR runs `python scripts/changelog.py fold`.

## Command docs (#893)

Each top-level command has its own page, `docs/commands/<command>.md`: the title
`` # `agentbreeder <command>` ``, a blank line, the command's `--help` line as a one-line summary,
then the reference text. `docs/commands.md` holds the notes shared by several commands and an
index generated from the pages. After adding a command, or changing its `--help` line, run
`python scripts/commands_index.py --write`; a test fails when the index is stale, a command has
no page, or a page's summary differs from `--help`. Regenerate the index on a merge conflict
rather than merging it by hand.
