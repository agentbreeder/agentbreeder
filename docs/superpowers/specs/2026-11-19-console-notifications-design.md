# Console notifications (#827 M11, console half): design

- Status: **r2**, the r1 lens findings folded in (§5). Part of #827 (epic #1054). The CLI half is on
  `main`: stored events (`events.py`, `agentbreeder events`, `docs/events.md`) and conditions
  (`next --json`'s `conditions`, `guide_checks.compute`). This adds the console's bell, panel and
  toasts only.
- Specs: guided-path spec §11, §14.4, §14.9 R1, R5, R7, R8 (`2026-11-11-guided-path-design.md`).
- Nothing here has been run on a real stack.

## 1. What the console shows

- **Bell** in the top bar, between the Jobs pill and the theme toggle, as a pill of the same
  height. It shows **one number**: the conditions of the project in scope plus its unread stored
  events; under All projects, the unread events of every registered project (no conditions are
  read there: they are per project and `next` is not cheap). Zero shows no number.
  - **Conditions** are exactly `next --json`'s `conditions`, from the read the console already
    makes once per scope change, job end and window focus (`useGuide`). No new child: the bell,
    the Next pill and Home's checklist share it, so they never disagree.
  - The accessible name says the split in words: "Notifications: 2 need attention, 1 unread
    event", "Notifications: nothing needs attention, no unread events". A missing or failed `next`
    says "what needs attention is not known yet", a stale one adds "(last known)", and a failed
    events read says "events could not be read": never a silent zero.
- **Panel**: the bell (`aria-expanded`, `aria-controls`) opens a section in the page flow under the
  top bar, so it never covers the sticky header. It is not modal and not a dialog. Opening moves
  focus to its heading; Escape or Close closes it and returns focus to the bell. Width about 32rem
  at the right, the full width less 16 px gutters on narrow screens, scrolling inside.
  - **Needs attention (N)**: each condition's code and message, and "Open Home" (the checklist
    says what to do). Empty: "Nothing needs attention." Under All projects: "Choose a project in
    the Project scope menu to see what needs attention." A failed `next`: its message; a stale one:
    "Last known".
  - **Recent events (N new)**: the newest 20 events (of the project in scope, or of every
    registered project, each prefixed with the project's name), newest first: a title, the time
    (`<time datetime>`), the CLI's fixed-template message, and a "new" badge and a left rule for
    unread ones (never colour alone). **Mark all read** marks the shown unread ones; it stays in
    place as "All read" (`aria-disabled`), so focus is not lost, and a hidden status line says
    "Marked N events as read." Empty: "No events stored." A failed read: its message and code in
    the panel, never a toast. A footer line always says what events are and that the newest 500
    are kept on this machine.
  - A **gate failure** shows the report path as text. When the server parsed the path as the
    project's `deploy/<agent>.eval-<RUN>.json`, it offers **Show summary of failed cases**
    (`aria-expanded`; the summary appears in place in a status region) through the eval report
    viewer's existing route (`evals show RUN --agent NAME`, fixed argv, redacted with the stack's
    state; it needs the services up), and **Open the report**. The browser sends only the run and
    agent the server parsed, never the path. Any other path shows the command to run in a
    terminal.
- **Failure toasts**: a permanent region ("Failure notifications") at the bottom left, clear of the
  jobs dock at the bottom right. A toast is added, with `role="alert"` on the toast itself, for an
  unread event stored after the page's first read (the highest id seen then is the baseline).
  Each event is toasted once per tab; a dismissed one does not come back; a highest id below the
  baseline (the store was deleted) resets the baseline with no toast; a scope change resets it
  too. At most 3 on screen and the rest counted ("N more failures in Notifications"). Toasts never
  take focus and never time out. Each has **Open notifications** and **Dismiss** (named for its
  title), 44 px targets under `pointer: coarse`.
- Tokens only (`--card`, `--control-border`, `--border`, `--destructive`, `--elev-2`, `--elev-3`),
  no new colour; long text wraps (`overflow-wrap: anywhere`); no horizontal scroll at 320 px.

## 2. Server

- `GET /api/events[?project=ID]`: `events --limit 200 --json` (fixed argv, no `--project`, so
  `next`'s checks do not run again). The CLI answer is shared by every request within 2 seconds (one
  child for tabs polling together). The allow-list is applied on every request against the
  projects registered at that moment (so a project added a second ago is not missed): only events
  whose `project` is a registered project's resolved path pass, with that project's `id`, and only
  `id`, `time`, `kind`, `source`, `name`, `exit_code`, `code`, `read` and `message` (no control,
  bidi or invisible character, at most 300 characters), each checked against the CLI's own
  patterns (tied by a test); for `gate_failed`, `gate` with `passed` ≤ `cases`, `failed` (at most
  50 ids of at most 80 clean characters), `more`, `report` (absolute, clean) and `report_agent`
  and `report_run` when the path is that project's `deploy/<agent>.eval-<RUN>.json`. A bad event is
  dropped and counted; a bad envelope is `EVENTS_INVALID`. With `?project=ID` (a registered id, else
  `PROJECT_NOT_FOUND`) only that project's events are returned. It is a poll: it does not refresh
  the session idle timer (§14.9 R7).
- `POST /api/events/read[?project=ID]` `{ids: [int, …]}` (1 to 50 positive ids, nothing else):
  only ids among the events this console lists for its registered projects are sent (`--mark-read`
  does not check a project, so any other id is `EVENTS_READ_INVALID`), as `events --mark-read ID …
  --limit 1 --json`, each id its own word. Session, CSRF header and JSON body as every POST. It
  answers as the GET.
- New module `center/notifications.py` (pure: `re` and `center.files`), on the server's import
  allowlist.

## 3. Browser

- One events read in the shell, shared by the bell, the panel and the toasts: on mount, every 20 s
  while the page is visible, on window focus, when the page becomes visible and when a job ends.
  One read in flight per path; a scope change is never skipped. A 401 ends the session as every
  read does (no toast).

## 4. Not in scope

Desktop notifications (later, §14.4), log-level notifications (a filter, not a source, §14.4), the
review-date warning (dropped until `review_by` is recorded), and the acceptance run (stop a service,
see the condition appear and clear; a clean release stores zero events), which needs Docker.

## 5. Review log

| Round | Reviewer | Result |
|---|---|---|
| r1 | `frontend-design`, `ui-ux-pro-max`, `/security` (Claude Sonnet, read-only) | CHANGES REQUESTED by all three. Folded in: one badge number with the split in words, zero, stale and unknown cases (§1); bell placement and the in-flow panel, so nothing covers the header (§1); toasts at the bottom left clear of the dock, a permanent region with `role="alert"` per toast, no focus taken, no timeout, dismissed toasts not returning, the reset baseline, read events not toasted (§1); the focus model (heading on open, Escape to the bell, Mark all read kept in place) and a status line (§1); mark-read limited to listed ids (§2, the security blocker); server-side scoping by project (§2); the summary read from the parsed run and agent only (§1); coalesced reads (§2); panel error states with no toast (§1); named tokens and narrow widths (§1). Not taken: click-outside closing (Close and Escape suffice), a CSS variable for the dock height (toasts sit on the other side), a reduced-motion rule (no animation is added). |
