# Console: onboarding and building agents for each framework (#1085): design

- Status: **r4, reviews converged** (round 4 APPROVED). The r1–r3 `frontend-design`, `ui-ux-pro-max` and `/architect` (API) reviews
  (Fable, read-only) are folded in (§12). Owner decisions C1–C8 are open (§10). No code in this PR.
- Issue #1085, part of epic #1083. Builds on the architecture spec
  `2026-11-16-multi-framework-agents-design.md` (#1084, called "the architecture spec" below),
  which defines runtimes, the kit, protocol v1, conformance and code mode.
- Builds on (on `main` at 38cb7336) the console as documented in `docs/command-center.md`:
  - **Build an agent** (#833): `center/builder.py`, `screens/builder.js`, `lib/builder.js`
    (`keepOffered`, `unverifiedText`), and `agentspec.py` (`VERIFIED`);
  - **Registry ▸ Import** (#825): `center/registryimport.py`, `screens/import.js`;
  - **Onboard an agent.yaml alone** (#823);
  - **Agents** and the agent detail (#823), **Try it** (`screens/try.js`, `center/tryit.py`),
    **Traces** (#907), **LLM cost** (#906), the eval report viewer (#932) and the **Audit** view
    (#824).
- Rule carried over: **the browser never builds YAML and never runs anything itself.** Every write
  is a CLI child with a dry run, a digest and `--expect-digest` (three-tier confirm), as today.

## 1. Problem

The console builds and onboards agents for one framework. Once `runtime/<framework>` versions
exist (architecture spec §6), a user needs to:
- pick a framework when building;
- bring an existing LangGraph, OpenAI Agents, Deep Agents, CrewAI or Claude Agent SDK project in;
- see, everywhere an agent appears, which framework it runs and whether that has been run end to
  end.

This has to work without separate screens per framework.

## 2. Goal

Framework is **one more attribute of an agent**: chosen in one place, and shown everywhere else as
one neutral badge. Every existing screen works unchanged for every framework, because protocol v1
gives them the same fields (architecture spec §10).

There are three new surfaces:
- the framework choice on Build;
- a code-mode onboarding form;
- the runtime detail in the registry browser, with its conformance report.

**No per-framework text lives in the console.** Display names, notes and links come from the
runtime manifest (§8).

## 3. Scope and non-goals

In scope: Build (§4), onboarding a framework project (§5), the framework across the agent list,
detail, registry, Try it, Traces, cost, evals and audit (§6), states and accessibility (§7), the
API (§8) and tests (§9).

Not in scope:
- per-framework settings (none in v1, architecture spec D3);
- a graph or crew visualiser (C6);
- streaming in Try it (protocol v1.1);
- image import (#757).

## 4. Build an agent: choosing a framework

### 4.1 The field

A **Framework** fieldset in the existing `.choices` pattern (#1043), as the first field after the
name. Its data comes from `GET /api/builder/parts` → `runtimes` (§8).

```
Framework
(•) Google ADK
    runtime/adk@1.3.0 · Run end to end (Northwind)
( ) LangGraph
    runtime/langgraph@1.0.0 · Conformance passed; not run end to end
( ) OpenAI Agents SDK
    runtime/openai-agents@1.0.0 · Not run end to end
  …
Version  [1.0.0 ▾]        (static text when the framework has one version)

The console creates only combinations that have been run end to end. Preview shows the files
for any of them, and `agentbreeder new agent --from-spec` writes them anyway.

Cannot be used: runtime/example@0.1.0: protocol 3 is not supported by this AgentBreeder (PROTOCOL_UNSUPPORTED)
```

- **The radio label is the framework name only** (`display_name`, the product's own name, C8).
- **A second line, visible at every width**, carries the ref and the status. It is tied to its radio
  with `aria-describedby`, so the radios' accessible names stay short.
- **One Version select** under the group, for the chosen framework. It is a labelled `<select>`
  ("Version of LangGraph") that becomes static text when there is one version. Each framework starts
  on its newest version. Status and the second line follow the selected version. A change is
  announced once through `#announce` ("LangGraph 1.0.0: Not run end to end").
- **Status uses the console's existing vocabulary** (`unverifiedText`, the provider select). There
  are four values, as words, with any glyph `aria-hidden`:
  - "Run end to end (Northwind)": `VERIFIED` has this runtime;
  - "Conformance passed; not run end to end": the newest conformance report for a **declared-mode**
    image of this runtime version passed;
  - "Conformance failed; not run end to end": that report failed. The words link to it (§6.3);
  - "Not run end to end": otherwise.

  Runtime-level reports live in the **default state directory** (`~/.agentbreeder`), whatever
  project ran them (§8), because Build has no project yet. Code-mode images never set a runtime's
  status: their reports are keyed by their `code/` ref and shown on their project (§6.3).
- **Where the list comes from.** `parts.runtimes` is the engine's **resolved view** of each version,
  not the raw manifest. For a version without a `protocol` key (every existing `runtime/adk`
  version), the engine's `LEGACY_ADK` profile (architecture spec §5.3) supplies `display_name`
  ("Google ADK"), protocol 1, profile `v1`, `limits` and `reserved_tool_names`. So the console holds
  no framework text, and existing ADK versions are usable.
- **A runtime the CLI cannot use** (an unknown `protocol` value, an invalid manifest) is not a disabled
  radio, which keyboard users would skip along with its reason. It is listed as text under the
  group: "Cannot be used: REF: reason (CODE)".
- **A runtime's own note** (manifest `notes`, for example that it needs an Anthropic-shaped gateway
  route) is a third line, shown through `Text` and capped (§8).
- **Default:** Google ADK (C1).
- **Order:** runtimes that have been run end to end first, then by name.

### 4.2 What follows from the chosen framework

The rest of the form is unchanged: prompt, model provider, tools, MCP servers, eval cases, model
calls, threshold and retries are the same for every runtime (architecture spec §5.2). Three things
follow from the chosen runtime version's manifest:
- **Capabilities.** Without `mcp`, the MCP picker says "LangGraph 1.0.0 has no MCP client". The name
  and version come from the manifest; there is no per-framework string.
- **Limits.** The tools picker caps at `limits.tools`.
- **Reserved names.** A tool whose function name is in `reserved_tool_names` cannot be picked.

**When a switch makes picks invalid.** Picks that no longer fit the new framework or version (an MCP
server, a tool over the limit, a reserved name):
- **stay on the form, each with a reason line and a Remove control**, so they are never dropped
  silently. They are still removable when the picker itself is not offered;
- are reported in one message through `#announce`, not one per pick;
- set Preview's disabled reason (the `Action` string) to "2 picks do not work with LangGraph 1.0.0.
  Remove them or choose another framework.";
- clear their marks if the user switches back.

This replaces `keepOffered`'s silent drop for framework switches only. `keepOffered` keeps its
behaviour for registry changes. A framework or version change marks the preview out of date
(`changed()`), like any other edit.

### 4.3 Preview and create

- **The three-tier confirm is unchanged.** The spec JSON gains `runtime` (the ref), which `new agent
  --from-spec` writes into `agent.yaml`. The preview shows the `runtime:` line.
- **The code confirm** lists the runtime when it comes from outside the built-in registry
  (`runs_code("runtime")`), worded as for tools.
- **Create** is enabled only for a combination run end to end, now keyed by `(runtime name,
  parts)`. So a framework marked "Run end to end (Northwind)" with an MCP server can still have
  Create off. The general line under the group is static; the specific reason is Create's own
  `Action` disabled reason ("LangGraph with MCP servers has not been run end to end"), as today. The preview names what has not been run
  ("LangGraph with MCP servers has not been run end to end").
- **`agentspec.VERIFIED`** becomes `{runtime_name: frozenset(combinations)}`, changed only by a
  verification record (architecture spec §15).

## 5. Onboarding a framework project (code mode)

Home's **Add a project** card gains a fifth form, **A project written for a framework (code
mode)**.
- **Until code mode (architecture spec §9.2, F8) is on `main`**, the card says "Framework code cannot
  be onboarded here yet." (C2), in the style of "Knowledge is not on this form yet".
- **The framework choice** lists only runtimes whose manifest has the `code_mode` capability.

### 5.1 Fields

- **Project directory:** an absolute path, with Registry ▸ Import's rules (no control, bidi or
  invisible characters; passed after `--`; never read by the console server).
- **Framework:** the §4.1 group, filtered to `code_mode`.
- **Entry:** filled in by the first review (§5.2), never guessed by the browser.
- **Agent name, model provider and optional model id:** as in the agent.yaml-alone form.

### 5.2 Two reviews, then create

1. **Find** runs `agentbreeder new agent NAME --from-code=DIR --runtime=REF --model=… --dry-run
   --json` with no entry. It reads files only (static `ast` discovery; nothing is imported, resolved
   or sent to a package index). Its answer starts with **What the CLI refused**, before anything
   else. When that list is non-empty it is the whole answer. It can hold:
   - a vendor key name in `.env` or declared secrets (`PROVIDER_KEY_RESERVED`; the value is never
     shown);
   - a requirement the resolver will never accept: an index URL, a VCS, URL or path requirement
     (`REQUIREMENT_REFUSED`);
   - no candidate factory (`ENTRY_NOT_FOUND`), with the module's top-level names.

   Otherwise it lists the **candidate entries** the CLI found (for example `agent:build`,
   `app:make_graph`) as a radio group. It also lists **the framework packages the requirements
   name**, with a warning when they do not match the chosen framework ("requirements name crewai;
   you chose LangGraph"), because a wrong pick otherwise fails only at start-up
   (`ENTRY_TYPE_MISMATCH`).
2. **Review** (the same command with `--entry=module:factory`) shows:
   - the `code/<name>@0.1.0` version it would write: files, size and hash, as Import's review does,
     with dotfiles and `.env` listed as left out;
   - `code.yaml`;
   - the extra requirements **as declared**, not yet resolved or pinned (resolving sends names to
     the package index, so it waits for the confirm);
   - `agent.yaml` and `stack.yaml`, with their digests.

   **Any field edit drops the review** (the onboard form's rule), and the user must review again.
3. **The code confirm**, always shown for code mode. It names the directory and the number of
   extra packages:

   > Code in DIR and N extra packages run inside the agent, with the agent's privileges. They can
   > read its database, its MCP tokens and its gateway key (limited to the granted models, budget
   > and rate), call the gateway directly, and so go around the runtime's step cap, cost count and
   > audit. Building and releasing also run them as you. Resolving the packages contacts the
   > package index. The eval gate is a quality gate, not a security review.
   >
   > ☐ I trust this code to run in agents

   A "What the runtime switches off" link to the docs comes from the manifest's `docs` field, and
   is shown for every runtime that has one.
4. **Create** sends the reviewed digest (over the files and the declared requirements). The server
   repeats the review with `--expect-digest`. Then the CLI resolves the requirements into the
   hash-locked lock (wheels only), writes the files and registers the project. A resolve failure is
   `LOCK_FAILED`, and nothing is written. `DIGEST_MISMATCH` means review again. If the files
   were written but the project was not added, the error says so and points to **An existing
   project**, as the agent.yaml-alone flow does.

### 5.3 After onboarding

- **The guided path (#820)** gains a **Conformance** step after Build, for code-mode projects only
  (C3). The step comes from `agentbreeder next --json`, as every guided-path step does (the console
  adds none of its own); architecture spec §9.2 adds it to `next` in F8. Code mode is where the
  factory contract can fail at start-up. A failed step shows the report's failing groups and links
  to the report on the project's agent detail (§6.1, §6.3).
- **Agents built from `agent.yaml`** get conformance only on request.

## 6. The framework everywhere else

There is one component, **`FrameworkBadge`**, built on the neutral `.badge` style
(`style.css:196`), not on `.chip`, which is the status pill (`style.css:415`).
- It shows the display name. Where the ref matters, the ref is visible text beside it, never hover
  only.
- There are no logos (C4).

### 6.1 Agents list and detail

- **Agents list:** the badge sits **under the agent name** at every width, where the candidate
  badge sits, rather than in a ninth column.
  - Its source is the deployment's `runtime` from `list --all --json`, which the CLI takes from the
    manifest's `runtime` field, else the image label, else the build record or release package
    matched by digest (architecture spec §11). So existing ADK agents and `kind` deployments have one.
  - The ref becomes a name through `GET /api/runtimes` (the `parts.runtimes` view, §8). A ref that
    is not in the registry shows the ref itself.
  - `null` shows "Not recorded".
- **Framework filter.** A select ("All frameworks", each framework present, "Not recorded").
  - It is kept in the URL query, which is a **new feature of `lib/router.js`** (it reads no query
    today) and part of slice S2.
  - The status line counts what is shown ("3 of 9 agents").
  - The empty state reads "No agents on CrewAI." with a **Show all frameworks** button.
  - The filter's value and focus survive the 3-second refresh (#1073).
- **Agent detail, Overview:** keeps the **Runtime** row (the ref) and adds a **Framework** row (the
  badge), and **Protocol** and **Capabilities** rows, all from the same resolved view (§8). A legacy
  ADK version shows "1 (ADK, before the kit)". A code-mode agent also has a **Conformance** row:
  the newest report for its `code/` ref (§6.3), with failing groups expanded.

### 6.2 Try it, Traces, cost, evals, audit

These need no per-framework change, which is what protocol v1 is for.

- **Try it.** Turn details (`screens/try.js:219-226`) shows Tools, Sources, Latency, Tokens, Cost,
  Trace and Model alias. It gains **Runtime** and **Framework** rows. `center/tryit.py` already
  passes `runtime` on, and adds `framework` (at most 100 characters).
  - The Cost row's "Not measured" text (`try.js:224`) names `runtime/adk` versions. It becomes
    neutral: "Not measured: the runtime did not report it for this turn."
  - The new code `MODEL_STEP_LIMIT` gets a plain-language line in the coded-failure table.
- **Traces.** A checkbox **Show framework spans (29)** sits above the span tree. Framework spans are
  those that are neither the root, an LLM span nor a TOOL span: graph nodes, chains, crew tasks.
  - **When hidden:** LLM and TOOL rows move under their nearest shown ancestor. A span with an
    error is always shown. The tree's status line says "41 spans, 29 framework spans hidden"; it is a
    `role="status"` region, so a toggle is announced, and focus stays on the checkbox.
  - **Default:** shown when the trace has at most 20 spans, hidden above that. It is computed when a
    tree is first opened and then kept for that tree while the tab is open; a "Read again" keeps it.
  - **No checkbox** when there are no framework spans, so ADK traces look as they do today.
  - It is a display filter only.
- **Cost, Usage, the eval report viewer, Releases and Audit** are unchanged apart from the badge
  where an agent is named.

### 6.3 Registry ▸ Runtimes, the runtime detail and conformance

- **Registry ▸ Runtimes** lists every `runtime/` version with its framework, protocol, kit version
  and status (§4.1's words).
- **The runtime detail** shows:
  - the manifest's fields as a definition list;
  - the pinned packages from `requirements.in` (names and versions);
  - **Conformance**: the newest report for an image built from this runtime version (from the
    state directory's records, §8), with its image digest and date. It is marked "not the current
    build" when a project's newest image of that runtime differs. Failing check groups are
    expanded, and each check's message is shown as text.
  - **Run conformance**, a job on one of the current project's **declared-mode** built images of this
    runtime version (code-mode images are checked by the guided path's Conformance step, §5.3;
    their report is shown on their agent's detail), picked with a select of those images (agent
    name, built time, short digest). Its disabled reasons are: "No declared-mode image of this
    runtime version in this project. Build an agent from `agent.yaml` on it first.", the
    Docker-down notice, and "A conformance run is in progress."
  - Code-mode reports are shown on the project's own agent detail, keyed by the `code/` ref, never
    as the runtime's status.
- **Registry ▸ Code** lists `code/` versions, with Fork and Import as for tools.

## 7. States, accessibility, themes

| Surface | Loading | Empty | Error |
|---|---|---|---|
| Framework group | the builder's `role="status"` line, "Reading the registries…", not an `aria-hidden` skeleton | cannot happen with the built-ins, so it is shown as an error with the CLI's code | the read's code and **Try again**; Preview disabled until a runtime is chosen |
| Version select | part of the group's loading | static text for one version | n/a |
| Code-mode Find and Review | a job row with its log | "No candidate entries" is the `ENTRY_NOT_FOUND` refusal | the CLI's code and message, never values; an unreadable directory is the CLI's `PATH_UNREADABLE`; Find needs no Docker |
| Agents filter | the list's own | "No agents on NAME." + **Show all frameworks** | the list's own |
| Registry ▸ Runtimes, ▸ Code | `role="status"` line | "No runtimes" cannot happen; "No code versions yet. Onboard a framework project to add one." | the read's code and **Try again** |
| Runtime detail, Conformance | the job row, "Running conformance" | "Not run for this runtime version." + Run conformance | the job's coded failure (#1042) |
| Guided path, Conformance step | the job | n/a | the failing groups and a link to the report on the agent detail |

**Accessibility**
- Each radio's status and note are linked with `aria-describedby`. Glyphs are `aria-hidden`, and
  the words carry the meaning.
- Unusable runtimes are text, not disabled controls (§4.1).
- Invalid picks are announced once, and each one's reason is linked to its Remove control (§4.2).
- The span checkbox is a native checkbox with its count in the label.
- Nothing is conveyed by colour, icon or hover alone. Focus is kept on filter changes and refreshes.
- axe runs in light and dark on every new or changed screen. Build is checked at 320 px reflow and
  200% zoom.

**Themes:** only `brand.css` tokens; the badge uses `.badge`'s `--control-border` and `--text-muted`
in both themes.

**Layout:** the Build form keeps its grid (`.builder`, which stacks under 900 px). The second line
under each radio means no row has to fit a long ref on one line.

## 8. API the console needs

Every route here reads CLI output, or runs a CLI child, as today.

| Route | Change |
|---|---|
| `GET /api/builder/parts` | gains `runtimes: [{ref, name, version, display_name, framework, protocol, profile, kit?, capabilities, limits, reserved_tool_names, notes?, docs?, verified, conformance?: {passed, digest, date}, usable, reason?}]`: the engine's resolved view (manifest plus `LEGACY_ADK`, §4.1), computed by a CLI read (`agentbreeder registry runtimes --json`, architecture F1) so the console holds no profile logic. `display_name` and `notes` are capped at 100 and 300 characters, `docs` must be a relative docs path, and all are shown as text |
| `POST /api/projects/builder` (+`/create`) | the spec gains `runtime`; the server checks it is a listed, usable ref (`SPEC_UNSUPPORTED`) |
| `POST /api/projects/fromcode` (+`/find`, `/create`) | new, slice S5 (§5), with the builder's body cap and argv rules |
| `GET /api/runtimes` | the same `runtimes` list on its own, for the Agents list and Overview |
| `list --all --json` | each deployment gains `runtime` (manifest field, else label, else build record or package by digest, else `null`; architecture spec §11, F2a); additive, so `schema_version` stays 1 |
| `GET /api/registry/runtime/NAME/VERSION` | the existing registry show, plus the newest conformance record |
| `POST /api/projects/ID/conformance` | a new job: `agentbreeder conformance IMAGE --json`, where the image is one of the project's built images (never a free string). The CLI always writes the report to the **default** state directory, `~/.agentbreeder/conformance/<runtime>/<version>/<digest>.json` (declared mode) or `…/code/<name>/<version>/<digest>.json` (code mode), never a stack's, so Build and the runtime detail read one place. The agent detail reads a code-mode agent's newest `code/<name>/<version>` record through the project's agent read |

Answers are allow-listed (`BAD_OUTPUT` otherwise), as today.

## 9. Tests

Playwright with the fake CLI, and axe in light and dark, for each screen. Each case below is a named
test.

**Build**
- the framework list and the four status values;
- `runtime/adk@1.2.0` and `1.3.0` listed as usable "Google ADK" through the legacy profile;
- the version select changes the status;
- a runtime's note;
- an unusable runtime listed as text;
- keyboard selection;
- a switch marks an MCP pick, an over-limit tool and a reserved name invalid, each removable, with
  one announcement, and switching back clears them;
- a switch marks the preview out of date;
- the preview shows `runtime:`;
- Create off for a runtime not run end to end, and for a run runtime with an unrun combination;
- the code confirm for a path runtime;
- 320 px reflow and 200% zoom.

**Code mode**
- the card before F8;
- Find with candidate entries, then Review, then Create;
- each refusal code at Find (`PROVIDER_KEY_RESERVED`, `REQUIREMENT_REFUSED`, `ENTRY_NOT_FOUND`) and
  `LOCK_FAILED` at Create, with nothing written;
- no package index contacted before the confirm (the fake CLI records it);
- the framework-package mismatch warning;
- the confirm required;
- an edit drops the review;
- a digest mismatch;
- files written but the project not added.

**Agents**
- the badge under the name;
- "Not recorded";
- the filter, its empty state and count;
- the filter surviving a reload and the 3-second refresh.

**Overview and Try it**
- the Runtime and Framework rows;
- the `MODEL_STEP_LIMIT` line;
- the neutral "Not measured" text.

**Traces**
- the checkbox on a fixture LangGraph trace, by keyboard;
- re-parenting;
- an error span stays visible;
- an ADK trace shows no checkbox.

**Runtimes and the guided path**
- the list and detail;
- no report, a stale report, a failing report expanded;
- Run conformance with no declared-mode image of the runtime (a code-mode image is not offered),
  the image picker, and as a job;
- a code-mode report never changes the runtime's status;
- the guided-path Conformance step passing and failing.

Server tests (pytest) cover each new route's argv, body rules and answer allow-list.

**Acceptance** (slice S6), recorded with the Playwright video method:
1. build LangGraph Northwind from the console;
2. release it;
3. Try it, then open its trace;
4. show it in the Agents list beside the ADK agent.

This needs architecture slice F3.

## 10. Owner decisions (open)

| # | Decision | Recommendation |
|---|---|---|
| C1 | Default framework on Build | **Google ADK**, fixed. No hidden "last used" state |
| C2 | The code-mode card before F8 | **Shown, saying "Framework code cannot be onboarded here yet."**; no roadmap wording |
| C3 | Automatic conformance | **Only as the guided-path step for code-mode projects**; otherwise on request |
| C4 | Framework logos | **No**; a neutral text badge |
| C5 | Per-framework Try it extras (for example a Deep Agents todo list) | **No** in v1; protocol v1 has no such fields |
| C6 | A graph or crew visualiser | **No** in this epic; the span tree and its framework-spans checkbox cover it |
| C7 | Where the framework filter lives | **The Agents list only**; Usage and Cost can group by it later |
| C8 | Names | **Each framework's own product name**, with the ref beside it |

## 11. Slices (lane B)

| Slice | Contents | Needs |
|---|---|---|
| S1 | `parts.runtimes`; the Framework group, version select, notes, invalid-pick rules; `VERIFIED` by runtime | architecture F1 |
| S2 | `FrameworkBadge`; Agents badge and filter (router query support); Overview rows; Try it rows and codes | F2a |
| S3 | Registry ▸ Runtimes and ▸ Code; the runtime detail, conformance records and job; the guided-path step | F2a (records), F8 (the guided-path step) |
| S4 | The Traces framework-spans checkbox | F3 (a real LangGraph trace as fixture) |
| S5 | Code-mode onboarding | F8 |
| S6 | Acceptance recording (§9) | F3 |

## 12. Review log

| Round | Reviewer | Result |
|---|---|---|
| r1 | `frontend-design` + `ui-ux-pro-max` (Fable, read-only) | CHANGES REQUESTED, 5 major. Folded in: radio rows reduced to the name, with ref and status on a described second line and one Version select (§4.1); one status vocabulary taken from the console's own words, with conformance records in the state directory because Build has no project (§4.1, §8); invalid picks removable, one announcement, the `Action` reason, `keepOffered` scoped (§4.2); the code confirm no longer reassures, and its copy states what code can read (§5.2); code mode split into Find and Review, refusals first, a framework-package mismatch warning, the "written but not added" state (§5.2). Minors: `.badge`, not `.chip` (§6); the badge under the name, not a column, and router query support named (§6.1); the span checkbox named by its effect, with re-parenting and error spans (§6.2); Runtime row kept, Try it rows corrected against `try.js:219-226` (§6.2); missing states (§7); per-framework text moved to manifest fields (§8); C1 fixed default, C2 copy; slice ids S1–S6 |
| r2 | `frontend-design` + `ui-ux-pro-max` + `/architect` for §8 (Fable, read-only) | CHANGES REQUESTED, 1 blocker, 5 major; every r1 item confirmed in the body. Folded in: package resolution moved after the confirm, to Create (§5.2); `parts.runtimes` is the engine's resolved view, with `LEGACY_ADK` supplying ADK's name and limits (§4.1, §8); the deployment's runtime from architecture §11's three sources, with a ref-to-name lookup (§6.1, §8); conformance records in the default state directory, code-mode reports kept off runtime status, and a "Conformance failed" status (§4.1, §6.3, §8); code mode's CLI surface and the `next` step added to architecture §9.2; `SESSION_CUSTOMER_MISMATCH` dropped (§6.2, §9). Minors: confirm copy no longer reassures (§5.2); Overview sources and legacy wording (§6.1); Create's reason is its `Action` (§4.3); Version select label and announcement (§4.1); trace status region and default kept per tree (§6.2); image picker and its empty state (§6.3); missing states (§7); `version`, `profile` and `kit` fields (§8) |
| r3 | All four lenses over both specs (Fable, read-only) | Cross-spec fixes made in the architecture spec (report location, manifest `runtime` field). Here: four status values (§4.1), provider-key names in `.env` and secrets (§5.2), the manifest field as the first runtime source (§6.1, §8) |
| r4 | Narrow check of the r3 fixes (Fable, read-only) | **APPROVED** (no blocker or major). Minors folded in: a failed guided-path Conformance step links to the agent detail (§5.3, §7); the runtime detail's Run conformance picks declared-mode images only (§6.3) |
| r4 delta | Fable, read-only, over the text folded in after r4 (head `097b292a`) | CHANGES REQUESTED (1 major, 4 minor), folded in: the code-mode run path is the guided-path step, not the agent detail (§6.3); declared-mode disabled reason and §9 case; a Conformance row on the agent detail and its read in §8; re-wrapped lines |
