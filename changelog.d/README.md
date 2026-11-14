# Changelog fragments

Each pull request adds its `CHANGELOG.md` entry here as one file instead of editing
`CHANGELOG.md`, so parallel PRs do not conflict (#893). At release the fragments are folded into
`CHANGELOG.md`'s `[Unreleased]` section and deleted.

## Name

`<issue>.<type>.md`, or `<issue>.<slug>.<type>.md` when one issue needs more than one entry of a
type (`<slug>` is lower-case letters, digits and `-`).

| `<type>` | `CHANGELOG.md` heading |
|---|---|
| `added` | `### Added` |
| `changed` | `### Changed` |
| `internal` | `### Internal` |
| `fixed` | `### Fixed` |

## Content

One Markdown list item, exactly as it will appear, starting with `- ` (indented sub-items are
fine; no headings). It says it is on `main`, not in a published preview:

```markdown
- `agentbreeder events --mark-read ID` warns about an unknown ID (#897), on `main`, not in a
  published preview.
```

## Checks

- `python scripts/changelog.py lint` checks every fragment (a test runs it).
- In CI, `changelog.yml` fails a pull request that edits `CHANGELOG.md` without the `release`
  label, or that changes anything outside `docs/superpowers/`, `changelog.d/`, `CLAUDE.md`,
  `AGENTS.md`, `.agents/` and `.gitignore` without adding or editing a fragment. Dependabot PRs
  and release PRs need no fragment. The `release` label is for the release PR only; it lifts both
  rules, so reviewers check it is not set on anything else.

## At release

The release PR (label `release`) runs `python scripts/changelog.py fold`, which adds each
fragment at the top of its heading in `[Unreleased]` (highest issue number first, then by file name; a missing
heading is created) and deletes the fragments, then renames `[Unreleased]` and edits the entries
as before (`--dry-run` prints the result without writing).

Cut-over: the entries in `[Unreleased]` on 2026-11-13 stay where they are; later entries are
fragments until the next release folds them in.
