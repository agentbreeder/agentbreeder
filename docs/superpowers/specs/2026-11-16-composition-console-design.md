# Console: sub-agents and skills (#1058 §11, #1059 §12): design

- Status: **r4, reviews converged** (round 2 APPROVED by all three lenses, rounds 3 and 4 APPROVED). The r1 and r2
  `frontend-design`, `ui-ux-pro-max` and `/architect` (API) reviews (Claude Sonnet, read-only) are
  folded in (§17). Owner decisions CC1–CC10 are open (§15). No code in this
  PR.
- Part of #1058 and #1059 (epic #1057). This is the console half of the sub-agents spec
  (`2026-11-16-sub-agents-design.md`, "the sub-agents spec", §11) and the skills spec
  (`2026-11-16-skills-design.md`, "the skills spec", §12). It follows the plan in PR #1098
  (`docs/superpowers/plans/2026-11-16-sub-agents-and-skills.md`, "the plan", §10: UI-R and UI-B).
  The plan needs a few changes for this design, listed in §14. The owner accepted every decision in
  both specs on 2026-11-16 (sub-agents D1–D10, skills D1–D12).
- Builds on the console as documented in `docs/command-center.md` (on `main` at 4a3c9ee7):
  - **Build an agent** (#833): `screens/builder.js`, `lib/builder.js` (`SPEC_CHECKS`, `ToolPicker`,
    `keepOffered`, `unverifiedText`, `createProblem`), `center/builder.py`, `agentspec.py`
    (`SPEC_VERSION` 1, `VERIFIED`, `combination`) and `newagent.py` (`RUNTIME`, today
    `runtime/adk@1.3.0`);
  - the **registry browser** (#824) and **Import** (#825): `screens/registry.js`,
    `lib/registry.js` (`KINDS`), `screens/import.js`, `center/registryimport.py`;
  - **Agents** and the agent detail (#823): `screens/agents.js` (`Overview`, the `agent.yaml`
    card) and the agent read `center/reads.py:222`;
  - **Try it** (`screens/try.js` `Inspector`, `center/tryit.py` `turn_answer`), **Traces** (#907,
    `screens/traces.js`, `center/traces.py`, `phoenix_traces.py`) and **LLM cost** (#906,
    `screens/cost.js` `AgentCost`, `center/cost.py`, `llmcost.py`).
- Sibling design: the multi-framework console (`2026-11-16-multi-framework-console-design.md`,
  "#1085"). Its `parts.runtimes` and spec `runtime` field are reused here (§4.1, §11), so the two
  share one spec version bump (§4.5).
- **Rules carried over:**
  - The browser never builds YAML, never parses YAML and never runs anything itself.
  - Every write is a CLI child with a dry run, a digest and `--expect-digest` (the three-tier
    confirm).
  - Every answer the browser gets is allow-listed on the server. The one existing exception, the
    agent read, is named in §11.
  - All publisher and agent text goes through `<Text>`.
- **Nothing in this document has been run.** The console enables only what the acceptance runs
  (plan §8, AC) have verified.

## 1. Problem

Once the authoring and runtime slices land (plan AY and RT), an `agent.yaml` can declare
`subagents:`, `limits:` and `skills:`. The console today cannot show any of it:
- The agent detail lists the model, prompt, tools, knowledge and MCP only.
- Try it lists tools, sources and cost per turn. It does not say which sub-agent called a tool,
  which skill was loaded or what each sub-agent cost.
- A trace shows `ask_billing` as an ordinary tool span and `load_skill` with no skill name.
- Registry has no Skills page. A skill's `SKILL.md` is Markdown from a publisher, and the console
  has no safe way to render Markdown.
- Build cannot add a sub-agent or pick a skill.

## 2. Goal

- **See it.** Every place an agent is shown says which sub-agents and skills it has.
- **Follow a turn.** Every turn says which sub-agent did what, at what cost, and which skills were
  loaded.
- **Review a skill before pinning it.** That includes its description, which is sent on every model
  call. The skill's Markdown is shown in a way that cannot run, load or pose as console text, and
  its source, which the model reads, is one click away.
- **Build with them.** Build can add up to five sub-agents and pick skills. It can preview any
  combination. Create stays off for anything not run end to end.

## 3. Scope and non-goals

In scope:
- Build (§4);
- agent detail (§5);
- Try it (§6);
- Traces (§7);
- cost (§8);
- Registry ▸ Skills, the renderer and Import (§9);
- states, accessibility, themes and CSS (§10);
- the API (§11), tests (§12), slices (§13) and plan changes (§14).

Not in scope:
- **Editing a skill in the console.** Fork works as for every kind; editing is CC3.
- **Running a script skill.** Script skills (kind b) are shown in Registry. Build cannot pick them
  until skills K6 is verified (§4.3).
- **Calling a sub-agent directly.** The sub-agents spec §9 refuses it.
- **Sub-agent knowledge on Build.** Build has no knowledge yet. A file with `knowledge: true` is
  shown on the detail.
- **Skills on sub-agents** (skills D10, later).
- **A call graph or a sub-agent filter on Traces** (CC6).

## 4. Build an agent

### 4.1 Which runtime, and when the sections offer anything

- **The runtime list.** The builder reads `parts.runtimes` (#1085 §8: each version's `ref`,
  `capabilities`, `usable`, …).
  - When #1085's CLI read (its F1, `registry runtimes --json`) is not on `main` yet,
    `center/builder.parts` fills the same list in-process from the built-in `runtime/adk`
    manifests (`manifest.yaml` `capabilities`), as it reads `mcp.yaml` for MCP servers today.
  - Either way, the console holds no capability logic of its own.
- **Which runtime the spec pins.** A spec v2 carries `runtime` (the field #1085 adds, §4.5).
  - Without #1085's Framework group, the builder sends the newest usable built-in `runtime/adk`
    whose capabilities include every capability the form needs:
    - `subagents` for a sub-agent;
    - `skills` for a skill.
  - A form that uses neither sends no `runtime`. The CLI then keeps `newagent.RUNTIME`, so v1 output
    is unchanged.
  - The choice is visible before Preview: each section the form uses (Sub-agents, Skills) shows a muted
    line, "Uses runtime/adk@1.5.0 (the newest with sub-agents and skills)."
  - The server checks that a sent `runtime` is a listed, usable ref (`SPEC_UNSUPPORTED`), as #1085 §8
    does, also when only the fallback list exists.
  - The fallback reads `capabilities` through the same validated parser `build` uses
    (`build._runtime_capabilities`, which SP moves to a public shared helper), never a second
    parser. A runtime whose manifest fails that parser is listed as unusable with its reason, as
    #1085 §4.1 does, never dropped silently.
- **When no runtime has the capability**, the section shows one muted line, not a disabled control.
  The line is built from the answer: "No runtime in the registry has sub-agents yet." When #1085
  is on and the chosen version lacks it, the line reads "runtime/adk@1.3.0 has no sub-agents. Newest
  with sub-agents: runtime/adk@1.5.0." Both refs come from `parts.runtimes`.
- **Picks the runtime cannot take after a framework or version switch** follow #1085 §4.2:
  - each stays on the form with a reason and a **Remove** control;
  - one `#announce` message reports them;
  - Preview's `Action` reason names them.

### 4.2 Sub-agents section

It sits after **MCP servers** and before **Skills**, because a sub-agent picks from the same tools
and from the agent's MCP grants. The layout is single-column, like the rest of the form: label above
control, with no side-by-side fields and no new breakpoint.

```
Sub-agents
Specialists the agent can ask during a turn. The agent always writes the reply; a
sub-agent sees only the request the agent writes, never the conversation.
A sub-agent has its own tools but runs in the same process as the agent, so it can
reach whatever the agent can, including its secrets. It is not a security boundary.
Not run end to end: sub-agents.                              (muted small)

┌ Sub-agent 1: billing ────────────────────────────────────────────────┐
│ ask_billing · gemini · 1 tool · needs attention: the name is taken    │
│                                          [Remove sub-agent billing]   │
│ ▾ Details                                                             │
│   Name                                                                │
│   [billing                    ]                                       │
│   Lowercase letters, digits and _. The agent calls it as ask_billing. │
│   When to ask it (the agent's model reads this)                       │
│   [Refunds, invoices and payment questions…                     ]     │
│   120 of 500 characters                                               │
│   Model provider                                                      │
│   [gemini ▾]                                                          │
│   Model id (optional; replaces the provider's default)               │
│   [                            ]                                      │
│   Prompt (the sub-agent's instructions)                               │
│   [You are the billing specialist…                              ]     │
│   ▸ Tools (1 picked)                                                  │
│   ▸ MCP tools (0 of 2 the agent was granted)                          │
│   ▸ Limits (model calls 4, times per turn 2)                          │
└───────────────────────────────────────────────────────────────────────┘
[Add a sub-agent]  1 of 5
```

**The fieldset.** Each sub-agent is a `<fieldset tabindex="-1">`:
- **Legend.** "Sub-agent N: NAME", or "Sub-agent N" before it has a name.
- **Summary row**, a `div.sub-summary` whose text is a `span.muted.small`: the tool name, the provider, the number of tools, and the words
  "needs attention: …" with the first problem when there is one.
- **Remove** is at the end of the summary row, never inside the legend.
- **Body.** A `<details>` named **Details**. It is open for a new sub-agent and for any sub-agent with
  a problem; the others start closed, so five sub-agents stay short. Opening and closing changes
  only the display. The fields are always in the form and always validated. A problem also opens the
  nested `<details>` (Tools, MCP tools, Limits) that holds the field with it, so an `aria-invalid`
  field is never inside a closed section.

**Fields:**
- **Name.**
  - Uses `maxlength="32"` and the agent name's rule-text style. The rule note gives the tool name
    (`ask_billing`) as text.
  - The live check covers only the pattern and a duplicate within the form. Problems set
    `aria-invalid` and are linked with `aria-describedby` (WCAG 3.3.1).
  - Clashes with tool, MCP or built-in function names are left to the CLI (`SUBAGENT_NAME_CLASH`),
    which the preview reports, because the parts answer does not list function names.
- **When to ask it.** A textarea with `maxlength="500"`.
  - The visible "120 of 500 characters" is `aria-hidden` and is not a live region.
  - A separate visually hidden polite status speaks only at 450 and at 500.
- **Model provider and Model id.** As for the agent, with the same "(not run end to end)" suffix.
  The default is the agent's provider and id (CC10). The CLI decides whether the sub-agent shares
  the agent's gateway alias or gets its own, and the preview's aliases line shows which (CC2).
- **Prompt.** A textarea with the 32 KiB cap. The CLI writes it as a prompt version and names it.
  The preview shows the ref, and the console never derives it.
- **Tools** (a `<details>`, "Tools (N picked)"). This is `ToolPicker` over the same `parts.tools`,
  generalised to take:
  - an **id prefix** (`build-sub-2-tool-NAME`), so ids are unique across the agent and five
    sub-agents;
  - **a state slice** (the sub-agent's own `tools`).

  An agent file has one version per name across the agent and every sub-agent. For a tool name the
  agent or another sub-agent already picked, only that version is enabled. The other versions are
  disabled options, and a visible line beside the select says why: "The agent uses 1.2.0, and an
  agent file has one version per name" (or "Sub-agent returns uses 1.2.0…"). If the first picker
  later changes its version, the other picks are marked and removable (§4.1's rule). At most 20 tools.
- **MCP tools** (a `<details>`, "MCP tools (N of M the agent was granted)"). Checkboxes for the tools
  granted to the agent in the MCP section only (sub-agents spec §8.2).
  - With no grants, it shows: "Grant MCP tools to the agent first. A sub-agent can use only tools
    the agent was granted."
  - A pick whose grant the agent loses stays, marked and removable.
- **Limits** (a `<details>`). Model calls (1–20, default the agent's) and times per turn (1–3,
  default 2), with the spec's rule text.

**Add a sub-agent** appends a fieldset with its Details open and moves focus to its Name.
- "N of 5" sits beside the button.
- At five, `Action`'s reason "An agent has at most 5 sub-agents." is shown and linked. The button is
  natively disabled and leaves the tab order. Focus is never stranded on it, because Add moved focus
  into the new fieldset.

**Remove**
- **Accessible name:** "Remove sub-agent billing", or "Remove sub-agent 2" when the sub-agent has no
  name.
- **Confirm (`useConfirm`)** when the description or prompt is not empty: "Remove sub-agent billing
  and its prompt? This cannot be undone." An empty sub-agent is removed at once. On Cancel, focus
  returns to that Remove button.
- **Focus after removal:**
  - It goes to the next fieldset, else the previous one, else **Add a sub-agent**.
  - Legends renumber, so `#announce` names both what went and what got focus: "Sub-agent billing
    removed. Focus is on sub-agent 1, returns."
  - When eval cases name the removed sub-agent, it adds "2 eval cases still check it."
- Removing marks the preview out of date, like any edit (CC9).

**Order and turn cap**
- **Order.** File order is the order shown, and there is no reordering in v1.
- **Turn cap.** With at least one sub-agent, **Model calls and the eval gate** gains "Model calls
  per turn, all agents together (1 to 40)".
  - Its placeholder and note give the default: "Empty uses the default, 12 (the agent's calls plus 6,
    at most 40)."
  - Without sub-agents the field is hidden and the spec omits `limits`.

### 4.3 Skills picker

It sits after Sub-agents. It is one select per skill name, like `ToolPicker`, over `parts.skills`
(§11), newest first.

```
Skills
Instructions the agent loads only when a question needs them. Each skill's name
and description are sent on every model call; the rest only when it is loaded.

Skill refund-policy
[1.2.0 (project) ▾]
"How to decide and explain refunds, including EU cooling-off rules."
2 references · Review in Registry (opens in a new tab)
Skill complaint-tone
[Not used ▾]
Newest, 1.0.0: "Tone guide for complaints. Use when the customer…"  [Show full description]
Skill prorate-kit
[Not used ▾]
1.1.0 has scripts and cannot be picked here yet; 1.0.0 has none.

Picked 1 of 10 · names and descriptions add 92 characters to every model call,
plus a fixed instruction
```

- **Descriptions.**
  - A picked skill's description is shown in full, linked to its select with `aria-describedby`,
    because that text reaches every model call (skills spec §13.1).
  - An unpicked skill's description is clamped to two lines with CSS. **Show full description** is a
    native button with `aria-expanded`. Screen readers read the full text either way.
- **Review in Registry** (picked skills only) opens the Registry version in a new tab, with the
  accessible name "Review refund-policy 1.2.0 in Registry (opens in a new tab)". The form stays as
  it is in this tab.
- **Version change.** When the picked version's description differs from the version picked before
  on this form, a `.notice.warn` under the select says: "The description changed from 1.1.0. It is
  sent on every model call." It also links to the diff, which opens in a new tab.
- **Script skills.** A version is a script skill when `code` is `"code"` or `"unknown"` (§11, from
  the files, fail closed).
  - While no usable runtime has `skill_scripts` or `VERIFIED` lacks `("skill-scripts",)`, **only
    those versions** are disabled options.
  - A visible line under the select says which versions and why. A name whose versions are all
    script skills reads "has scripts: cannot be picked here yet".
- **Running total.** "Picked N of 10 · names and descriptions add C characters to every model call,
  plus a fixed instruction" is a polite status region. C is the sum of the picked names' and
  descriptions' lengths. `build` prints the full L1 size.
- **Empty.** The text reads: "No `skill/` versions were found. None ship with AgentBreeder. Write one
  with `agentbreeder registry scaffold skill/NAME`, or import one from Registry ▸ Skills, then
  Read again." **Read again** re-reads the parts and keeps the form.
- `keepOffered` gains `skills`: a pick that a re-read no longer offers is dropped, as for tools.

### 4.4 Preview, verification and Create

- **Eval cases.**
  - `SPEC_CHECKS` gains `subagents_include`, `subagents_exclude`, `subagent_tools_include`,
    `skills_loaded` and `skills_not_loaded`, and a case may carry `tags`.
  - The editor offers the sub-agent checks only when the form has a sub-agent, and the skill checks
    only when a skill is picked.
  - A case that names a sub-agent or skill not on the form is a case problem: "case refund-1 checks
    sub-agent returns, which is not on the form".
- **The preview** is the server's dry run, as today.
  - Its files show the `runtime:`, `subagents:`, `limits:` and `skills:` lines and blocks, and the
    sub-agents' prompt versions.
  - Its summary lists every gateway alias and the provider keys the stack needs, including those of
    the sub-agents.
  - A CLI refusal such as `SUBAGENT_NAME_CLASH` is shown as the preview's error with the CLI's
    location text (`subagents[1].name`).
- **The parts table** (Ref, From, Code) gains each sub-agent's prompt and tools, and each skill.
  For an instruction pack, Code reads "Text; name and description on every model call".
- **Verified.**
  - `combination.parts` gains `subagents` and `skills`. `unverifiedText` words them "sub-agents"
    and "skills": "Not run end to end yet: a new agent with sub-agents and skills."
  - Each section's status line is `p.muted.small`, with no tone or icon, worded by the same
    function, so the user learns it before Preview.
  - The single `.notice.warn` stays in the preview, as today.
  - **Create is off** until `combination.verified` is true. `VERIFIED` changes only in plan slice
    AC.
- **The code confirm** is unchanged. It covers tools from outside the built-in registry, including
  a sub-agent's. Instruction packs are text and add no checkbox, and script skills cannot be picked.

### 4.5 Spec v1 and v2

`agentspec.SPEC_VERSION` becomes 2 once, shared with #1085's `runtime` field (§14).
- **When the builder sends 2.** It sends `schema_version: 2` only when the form uses a v2 field:
  - `runtime`, `subagents`, `skills` or `limits`;
  - a case's `tags`, or a new check.

  Otherwise it sends 1, so a form without them previews byte-identically to today (a golden test).
- **v1 rules.**
  - A v1 body carrying a v2 field is `SPEC_INVALID`, on the server (`center/builder.request`'s
    `FIELDS` gains the v2 keys only for version 2) and in `agentspec`.
  - The answer's `spec_version` must equal the version sent (`center/builder.summary` checks it),
    so a v1 form still gets 1.
- **Caps for v2.** The body cap for v2 goes from 256 KiB to 512 KiB, in both `agentspec` and the
  server, and v1 keeps 256 KiB (two named caps, SP), since six prompts of 32 KiB is
  192 KiB before cases. `summary`'s `MAX_FILES` is checked against five sub-agents' prompt versions
  and raised if needed (a test with the maximum form). `aliases` (≤ 10) and `combination.parts`
  (≤ 10) already fit.

## 5. Agent detail

### 5.1 Overview, `agent.yaml` card

The card keeps its rows and adds two:
- **Sub-agents:** "billing, returns (2)", or "none". Each name is a button that scrolls the
  sub-agent's `h3` into view and focuses it (`tabindex="-1"`). It is not a `#fragment` link, which
  `lib/router.js` would treat as a route.
- **Skills:** "refund-policy 1.2.0, complaint-tone 1.0.0", each linking to its Registry version, or
  "none".

The data is the agent read. Once AY lands it returns `subagents`, `limits` and `skills`. The screen
reads them with defaults, so an older CLI's answer shows "none".

### 5.2 Sub-agents card

The card is shown only when the file has sub-agents. It is a list, not a table, because seven
columns would not fit at 320 px. Each sub-agent is an `<h3 tabindex="-1">` ("billing · ask_billing")
followed by a `dl.facts`:

| Row | Content |
|---|---|
| When to ask it | the description, in full |
| Model | alias, reasoning, model calls per turn |
| Times per turn | `max_calls_per_turn` |
| Prompt | ref (linked to Registry) or path |
| Tools | refs, or "none" |
| MCP tools | `server: tool`, or "none" |
| Knowledge | "Searches the agent's knowledge" or "No" |

Under the list:
- "Model calls per turn, all agents together: 12 (default)";
- the Build section's isolation sentence (§4.2), worded the same.

### 5.3 Skills card

The card is shown only when the file has skills. Each skill is an `<h3>` (the ref, linked to Registry)
and a `dl.facts`:

| Row | Content |
|---|---|
| Kind | "Instructions", "Instructions and scripts", or "Unknown, treated as code" |
| References | a count |
| Description | in full |

The data is `GET /api/projects/ID/skills` (§11). When it fails, the Kind row reads "Could not be
read" with the code, and the rest of the card still shows from the agent read. A sentence under the
list says: "Each name and description is sent on every model call. A skill's instructions are sent
only when the agent loads them."

## 6. Try it

**Turn details** (`try.js` `Inspector`) gains two rows from the reply fields of the sub-agents spec
§7.6 and the skills spec §8.5, passed through `turn_answer`'s allow-list (§11).

**Sub-agents** is a `ul.plain` in the `dd`, one item per sub-agent that ran, in first-call order:
- "billing: asked 2 times, 3 model calls, tools refund_request, $0.0021";
- "billing: … at least $0.0012; 1 call not measured", when some of its calls had no cost;
- "billing: … cost not measured", when none did.

**Skills** is a `ul.plain`, for example "refund-policy 1.2.0 loaded (references: eu-rules.md)".

**When each row is shown**
- A row with entries is always shown.
- An empty list shows "None asked" or "None loaded", but only when the agent read says the file has
  sub-agents or skills.
- Otherwise the row is absent, so other agents' turns and older runtimes read as today.

**Tools** stays unqualified (sub-agents D5). An `ask_NAME` entry is shown as "ask_billing
(sub-agent)" when NAME is in the turn's `subagents`.

**Cost** stays the turn total and short:
- "$0.0052" when every call was measured;
- "at least $0.0052", followed by "some calls not measured", when any sub-agent's `cost_unknown` is
  above 0.

The per-sub-agent split is in the Sub-agents row only.

**No new turn failure codes**
- "Already consulted", `SKILL_LOAD_LIMIT` and "already loaded" are tool results to the model, and
  `TurnUnknown` gives the runtime's fixed reply.
- The build and start codes get plain-language lines in the coded-failure table for job rows
  (#1042): `RUNTIME_NO_SUBAGENTS`, `RUNTIME_NO_SKILLS`, `SUBAGENT_*`, `SKILL_FIELD_UNSUPPORTED`,
  `SKILL_UNDECLARED_SCRIPT`, `SKILL_NAME_CLASH` and `SKILL_TAMPERED`.

## 7. Traces

### 7.1 The span tree

This tree is used by Try it's trace panel and by the Traces tab.

**The boundary span** is `ask_NAME`, a TOOL span whose `subagent` field is set.
- It starts with the neutral `.badge` reading "sub-agent", followed by the name as text: "billing".
- Only the word is in the badge, so `white-space: nowrap` cannot overflow at 320 px.
- It is the same neutral style as #1085's `FrameworkBadge`.

**Spans under it** (the child's AGENT, LLM and TOOL spans):
- Indented by the tree as today, with no visible badge.
- The first level under the boundary span carries a visually hidden prefix "in sub-agent billing,",
  once per item at that level, so a screen-reader user inside the sub-agent's branch knows where they
  are without hearing it on every deeper row.
- Their `subagent` field is set by the runtime (sub-agents spec §10.1). TR tests that it is set on
  every descendant.

**Skill load spans** are named by outcome:
- "Loaded skill refund-policy 1.2.0", or "Loaded refund-policy 1.2.0: references/eu-rules.md", when
  the status is ok.
- "Tried to load skill refund-policy" with the span's error line, otherwise. This covers a limit, an
  unknown skill or a refused path.
- The facts add the `sha256`, shortened to 12 characters, with a copy button for the full value.

**Error spans and span content**
- A span with an error keeps its error line, wherever it sits.
- The CLI never reads a span's input or output, so skill bodies never reach the console. The runtime
  also replaces them with `{"chars": n}` (skills spec §11).

### 7.2 The Traces tab list

Each trace item gains two `div.small` lines. Each is absent when empty.
- "Sub-agents: billing (2 model calls, 1,240 tokens), returns (1 model call, 310 tokens)". The
  CLI sums these from the spans with the same count-once rule as the trace total
  (`_count_tokens_once`); the browser does not.
- "Skills loaded: refund-policy 1.2.0".

## 8. Cost

The Overview's **LLM cost** card (`AgentCost`) gains a `Table` labelled **Spend per sub-agent**,
under "Spend per deployment", when the `cost` answer has `by_subagent` rows for this agent's
deployments (§11).
- Columns: Agent, then the card's existing `COUNT_COLUMNS` (calls, spend, unpriced), right-aligned
  with `td.num` as in the other tables.
- The agent's own row is named "NAME (the agent itself)".
- A row with unpriced calls shows its spend as "at least $X", and with none priced, "not measured",
  never $0. A row with zero calls is not listed.
- A `p.muted.small` under the table says: "Grouped by the request tag the runtime sends with each
  model call." The card's existing estimate and delay notes apply.

**Precondition: the S0 probe's verdict is "tags"** (plan §3).
- Only tags reach LiteLLM's spend logs. There is no stored mapping from a request to a sub-agent that
  a later `cost` run could use.
- If S0 finds that tags do not arrive, the table is not built. The card then says: "Spend per
  sub-agent is not available from the gateway. A turn's Try it details split its cost by sub-agent."

Home's project cost card is unchanged.

## 9. Registry ▸ Skills

### 9.1 List

`lib/registry.js` `KINDS` gains `skill: {path: "skills", nav: "Skills", one: "Skill"}` after
Tools. This adds a **Skills** link to the sidebar.

The list gains a **Code** column, its seventh. It takes the latest version's `code`:
- "Instructions" for `text`;
- "Has scripts (code)" for `code`;
- "Unknown, treated as code" for `unknown` (unreadable files, K1 fails closed);
- "Mixed" when versions differ.

The versions table on the detail gets the same column per version. The values come from
`registry list`'s rows, never from the kind. The words carry the meaning, and any icon is
`aria-hidden`.

### 9.2 Version detail

The detail comes from `registry show --json`'s `skill` object (§11). The browser never parses front
matter or YAML. The detail shows, in this order:

1. **Facts**, a `dl.facts`: name, version, root, verify status, hash, `license` and `compatibility`
   when present, and the source label for a remote version (M9b).
2. **"Sent on every model call"**, a new `.callout` block (§10.3).
   - Its first line is plain text: "Every agent that pins this version sends this name and
     description on every model call, before it chooses to load anything."
   - Then a labelled quote, "Description, 120 characters", in `blockquote.quoted`. It holds the
     description as text.
   - Publisher text never sits inside a system-styled notice. This block comes first because the
     description is the stronger injection position (skills spec §13.1).
3. **Instructions (`SKILL.md`)**, with a **Rendered / Source** toggle.
   - The toggle is a native radio pair in a `fieldset.choices` whose legend is "Show SKILL.md as".
     The options are "Formatted" and "Source (what the model reads)".
   - Formatted is the default. Changing it does not move focus and announces only the radio's own
     state.
   - There is **one toggle for the page**, and it applies to `SKILL.md` and every Markdown
     reference. The choice is kept while the tab is open, not stored.
   - Formatted is the safe renderer (§9.3) inside `div.md` (§10.3). The frame is labelled "Text from
     the skill's publisher, shown formatted. The model reads the source."
   - Source is a `pre.text` through `<Text>`. When the CLI's `stripped` flag is set, it is followed
     by "control or invisible characters removed", as `FileView` does today.
4. **References.** One `<details>` per file, with its name and size.
   - A `.md` file follows the page's toggle, and a `.txt` file is shown in a `pre.text`.
   - A file over the cap shows "Not shown: over the 64 KiB cap" with its size.
5. **Scripts** (kind b only). One `<h4>` per script, with its tool name
   (`skill_refund_policy__prorate`, the name evals and replies use), followed by a `dl.facts`:
   - its description;
   - its parameters, as the JSON Schema in a `pre.text`;
   - its source, in a `<details>` as text, as tool code is shown today.

   A list is used here, not a table, for 320 px. A `.notice.warn` sits above it: "These scripts are
   code. Script skills are designed to run each script in a separate container with no network; that
   is not shipped or verified, and agents built in this console cannot use them."

The version timeline, diff, used-by, Fork and verify keep their behaviour for `skill/`. The diff
gains one `.notice.warn` above the hunks when the answer's `skill.description_changed` is true: "The
description changed. It is sent on every model call of every agent that pins this skill." There is
also a line when `scripts_changed` is true (CC5).

### 9.3 The safe Markdown renderer (`lib/markdown.js`)

**Our own small subset renderer to Preact nodes, with no HTML strings (CC1).** It is a bounded,
iterative block-then-inline parser into a token tree. Each token is rendered with `html```, so
publisher text only ever becomes text nodes inside a fixed set of elements.

**Sinks.** The renderer never uses `innerHTML`, `dangerouslySetInnerHTML`, `DOMParser`,
`insertAdjacentHTML` or `document.write`. A test fails if `lib/markdown.js` or the skill screens
contain any of them.

**Supported:**
- headings: `#` → `h4`, `##` → `h5`, `###` and deeper → `h6`, under the page's `h1` (page),
  `h2` (Card) and `h3` (section);
- paragraphs, emphasis, strong and inline code;
- fenced and indented code blocks, as `pre` with no highlighting;
- bullet and numbered lists;
- block quotes, horizontal rules and hard line breaks.

**Shown visibly as text, never hidden:**
- raw HTML, as its literal characters;
- **HTML entities, not decoded** (`&amp;` stays `&amp;`), so the formatted view shows what the
  model reads;
- images, as "[image: ALT, not shown]";
- link titles, as "(title: …)" after the link;
- reference-style link definitions, as their own visible line;
- tables, as their source in a `pre` (CC7);
- anything outside the subset.

**Links**
- Each URL is parsed with `new URL`. Only `https:` and `mailto:` become `<a>`.
- **`https:`** links get `rel="noopener noreferrer"` and `target="_blank"`. After the link text they
  show their **full destination** as text, ASCII (punycode) host included, cut at 80 characters with
  "…". A cut destination adds "(full address in Source)" once, to its text and accessible name.
  - The accessible name adds "(opens in a new tab)".
  - A URL with userinfo (`https://europa.eu@evil.example/`) is never a link. It is shown as text.
- **`mailto:`** links show the address and get no `target`.
- **Any other scheme** (`http:`, `javascript:`, `data:`, `file:`, relative) is shown as text with
  its URL. Autolinks follow the same rules.
- Link text and destinations go through `<Text>` like everything else.

**Characters**
- The CLI strips control, bidi and invisible format characters (`registry.clean`, reported by the
  boolean `stripped`).
- The renderer strips nothing itself. Every text node goes through `<Text>` (`lib/text.js`
  `segments`), which shows any leftover as a visible escape, so bidi-spoofed link text is
  neutralised.

**Bounds**
- **Input size.** `SKILL.md` is at most 32 KiB and a reference at most 64 KiB, the registry's caps.
- **Nesting.**
  - Lists and block quotes nest at most 4 deep, and emphasis and links at most 8.
  - Deeper markers are kept as literal text.
- **Fallback to source.** Past 5,000 tokens the whole document is shown as Source, with the
  reason, so rendering depth and size are bounded.
- **No backtracking.** There are no regular expressions that can backtrack on nested input.

**Tests**
- **XSS corpus.** Every output has:
  - no element outside the allow-list;
  - no attribute outside `href`, `rel`, `target` and `class`;
  - no `href` whose scheme is not `https:` or `mailto:`.

  The corpus covers:
  - OWASP filter-evasion cases;
  - `javascript:` with entities and mixed case;
  - `data:` URIs;
  - raw `<script>`, `<img onerror>` and `<svg>`;
  - reference-style links;
  - bidi-spoofed link text;
  - a userinfo URL;
  - an IDN homograph host;
  - link text that looks like a different host.
- **Growth test, not a wall-clock test.** Doubling an adversarial input (nested brackets,
  emphasis markers, `>` runs) at most about doubles the parser's step count.
- **Heading levels and the source fallback** each have a test.

### 9.4 Import

The Import review (`screens/import.js`, `center/registryimport.py`) for a `skill/` version:
- shows the §9.2 description block before the files;
- decides the code question from the fetched or staged files (K1's `code`), not from the kind. The
  cross-check that today raises `BadOutput` on a mismatch compares the CLI's file-based answer;
- for a **script skill** (`code` or `unknown`), uses the existing "This code runs in agents"
  fieldset and checkbox, with the addition: "Script skills are designed to run in a separate
  container with no network; that is not shipped or verified.";
- for an **instruction pack**, extends the existing "steers the agent" line to: "A skill's name and
  description are sent on every model call of every agent that pins it, and its instructions can
  steer the agent to use any tool it has. Read the description above and the source before you
  import."

## 10. States, accessibility, themes and CSS

### 10.1 States

| Surface | Loading | Empty | Error |
|---|---|---|---|
| Build: Sub-agents | part of the parts read ("Reading the registries…") | "No sub-agents. Add one to let the agent ask a specialist." with **Add a sub-agent** | the parts read's code and **Try again** |
| Build: a sub-agent fieldset | n/a | n/a | "needs attention: …" in its summary row, Details opened, the field `aria-invalid` with its reason |
| Build: Skills | as above | the §4.3 empty text with **Read again** | as above |
| Build: no runtime has the capability | n/a | the §4.1 muted line | n/a |
| Build: preview after an edit | n/a | n/a | the existing "Preview (out of date)" heading and warn line |
| Detail: Sub-agents, Skills cards | the `agent.yaml` card's `busy` | card absent; the `agent.yaml` rows say "none" | the agent read's `ViewState`; for Skills, the Kind row "Could not be read" (§5.3) |
| Try it rows | the turn's pending bubble | "None asked" or "None loaded" (§6) | the turn's existing failure |
| Try it, partly measured cost | n/a | n/a | "at least $X; N calls not measured" |
| Traces lines | the list's status line | lines absent | the list's existing error |
| Cost per sub-agent | the card's `busy` | table absent; the S0 "not available" line when that applies | the card's existing error |
| Registry ▸ Skills list | the registry's `role="status"` line | "No skill versions yet. Write one with `agentbreeder registry scaffold skill/NAME` or import one." | the read's code and **Try again** |
| Skill version detail | "Reading this version…" | n/a | the CLI's code (`SKILL_FIELD_UNSUPPORTED`, `SKILL_UNDECLARED_SCRIPT`, `VERIFY_FAILED`, …) as text; Source still offered when a body was returned |
| Formatted view | n/a | "This `SKILL.md` has no body." A body of only unsupported blocks shows them as text (§9.3) | over the token cap: the Source view with the reason |
| A reference over the cap | n/a | n/a | "Not shown: over the 64 KiB cap" |

### 10.2 Accessibility

- **Sub-agent fieldsets**
  - Each has a legend naming it, and focus can be sent to the fieldset (`tabindex="-1"`).
  - Remove's accessible name includes the sub-agent's name or number.
  - Add and Remove move focus as §4.2 says, and each says so once through `#announce`, naming the
    sub-agent that gets focus.
- **Counts.** Visible character counts are `aria-hidden`. Hidden polite status regions speak at
  thresholds only. "N of 5" and "Picked N of 10" are polite status regions.
- **Descriptions and reasons.** Descriptions and reason lines are linked with `aria-describedby`.
  The reason for a disabled option is visible text next to its select, never only in the option.
- **Words, not colour.** "needs attention", "sub-agent", "at least" and the skill load span names
  carry the meaning in words. No state is shown by colour, icon or hover alone.
- **Span tree.** The first level of spans inside a sub-agent carries a hidden "in sub-agent NAME,"
  prefix (§7.1).
- **Formatted / Source.** The toggle is a native radio group whose option label says the model reads
  the source. Formatted headings start at `h4` under the section's `h3`.
- **Links.** External links say "(opens in a new tab)" in their accessible name, and `mailto:`
  links do not.
- **In-page jumps** on the Overview are buttons that move focus (§5.1).
- **axe** runs in light and dark on:
  - Build with both sections filled and one sub-agent invalid;
  - the agent detail with both cards;
  - a Try it turn with both rows;
  - a trace tree with a sub-agent and a skill load;
  - a skill version in Formatted and Source.
- **Playwright at 320 px and 375 px, and at 200% zoom**, on:
  - Build;
  - the agent detail cards;
  - the Try it `Inspector` with long sub-agent lines;
  - the trace tree;
  - the skill detail.

  Each asserts no horizontal page scroll.

### 10.3 Themes and the new CSS

Only `brand.css` tokens are used, and there are no new colours. The new rules are listed here so
the slice adds exactly these and nothing else:

- `.callout`: `display: block`, `border: 1px solid var(--control-border)`, `border-radius: 6px`,
  padding as `.notice`, `background: var(--background)`. It is a neutral frame, not the blue info
  notice.
- `blockquote.quoted`, inside `.callout` and for quoted descriptions:
  - `border-left: 3px solid var(--control-border)`;
  - `color: inherit`;
  - `margin: 8px 0`;
  - `padding-left: 12px`;
  - `white-space: pre-wrap`.
- `div.md`, the frame of formatted publisher text: `border: 1px solid var(--control-border)`,
  padding, a `max-height` with `overflow: auto` matching the Source `pre`, and a `p.muted.small`
  label above it.
- Inside `.md`:
  - `h4`, `h5` and `h6` at 13px, weight 600, with `margin: 12px 0 4px` (below the section `h3`'s
    14px, so the outline does not look flat);
  - `ul` and `ol` with `padding-left: 20px` per level;
  - `blockquote` as `blockquote.quoted` with `color: var(--text-muted)`;
  - `pre` as `pre.text`;
  - `hr` as `border-top: 1px solid var(--control-border)` (`--border` is faint in dark).
- `.clamp-2`: `display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden`. It is only for unpicked skill descriptions, with the Show button. The full text
  stays in the DOM and in the select's `aria-describedby`, so it is read in full either way.
- `.sub-summary`: a `div` with Remove at the end, `display: flex; flex-wrap: wrap; gap: 8px`; its
  text `span` has `flex: 1 1 12rem; min-width: 0`, so long problem text wraps and the button wraps
  below it at 320 px.
- `fieldset[tabindex="-1"]:focus` and `h3[tabindex="-1"]:focus`: `outline: 2px solid var(--ring);
  outline-offset: 2px`. The global `:focus-visible` rule may not match a programmatic `.focus()` after
  a mouse click, so these use `:focus`; a Playwright test checks the outline after a mouse-click
  Remove and a jump.

Badges use `.badge` (`--control-border`, `--text-muted`). Notices keep their tones. Every new or
changed surface is checked in both themes. Dark mode's `--border` is faint, so frames use
`--control-border`.

## 11. API the console needs

Every route reads CLI output or runs a CLI child, as today. Each new field is allow-listed with the
caps stated. A bad list item is dropped, and a bad value the screen depends on makes the answer
`*_INVALID`; nothing is shown raw. **One exception exists today:** the agent read
(`GET /api/projects/ID/agent`) passes `agentfile`'s `model_dump` through without an allow-list.
That is safe because the CLI's own strict model validated it. This design adds no new pass-through.

| Route or answer | Change | Producer |
|---|---|---|
| `GET /api/builder/parts` | `runtimes` as #1085 §8, or the in-process fallback of §4.1 (`ref`, `capabilities`, `usable`). `skills: [{ref, name, version, root_kind, code, description, references, scripts, unusable?}]`: `code` ∈ `text`/`code`/`unknown`; `description` ≤ 1024, a separate cap from tools' 300; `references`, `scripts` counts; `unusable` text for a script skill that cannot be picked | K1 (`registry list` code and counts), #1085 F1 or the fallback |
| `POST /api/projects/builder` (+`/create`) | spec v2 (§4.5): `runtime`, `subagents: [{name, description, model, model_id, max_model_calls, max_calls_per_turn, prompt: {text}, tools: [ref], mcp: [{ref, tools}]}]` (≤ 5), `skills: [ref]` (≤ 10), `limits: {turn_model_calls}`, case `tags` and checks. Body cap 512 KiB for v2. A v1 body with v2 fields is `SPEC_INVALID` | SP (§13) |
| `GET /api/projects/ID/agent` | no route change; `subagents`, `limits`, `skills` appear once AY lands | AY |
| `GET /api/projects/ID/skills` (new) | an in-process read `reads.skills(project)`. It resolves each pinned skill ref through `registry.resolve` and checks its hash against `agent.lock`. It returns `[{ref, code, references, description, locked: bool}]`, allow-listed. Only the resolved refs, counts and descriptions are cached, keyed on the registry signature plus `agent.yaml` and `agent.lock`; `locked` is recomputed on every request by hashing the files, because the signature covers roots and version names only and a file edited under an unchanged version (the `SKILL_TAMPERED` case) must show | K1 (`runs_code`/`code` helper and counts) |
| Try it turn (`tryit.turn_answer`) | `subagents: [{name, calls, model_calls, tools, cost_usd, cost_unknown, served_by}]` (≤ 5; `name` by the sub-agent pattern; `cost_unknown` an integer count ≥ 0; `tools`, `served_by` through `_names`). `skills: [{name, version, resources}]` (≤ 10; kebab name, semver, `resources` ≤ 3 names by the references pattern). Absent when the runtime sent none | RT |
| Trace spans (`traces --trace`, `tryit._span`) | each span: `subagent` (pattern-checked or null), `skill: {name, version, sha256, resource}` (each checked, or null) | TR |
| Trace list (`traces ID --last N`, `traces._trace`) | each trace: `subagents: [{name, model_calls, tokens}]` (≤ 5, tokens counted once), `skills: [ref]` (≤ 10) | TR |
| Cost (`cost --json`, `center/cost.py`) | `by_subagent: [{key_alias, subagent ("-" for the agent itself), ...the existing bucket fields: calls, cost_usd, unpriced, …}]`, parsed by the existing `_bucket`. Present only when S0's verdict is "tags" | EV, after S0 |
| Registry list rows | `code` per version (`text`, `code`, `unknown`) | K1 |
| `registry show --json` for `skill/` | `skill: {description, license?, compatibility?, body, stripped, references: [{name, size, text?, truncated}], scripts: [{name, tool, description, parameters, path}]}`, front matter split off by the CLI's strict loader | K1 |
| `registry diff --json` for `skill/` | `skill: {description_changed, scripts_changed}` | K1 |
| Import review | `code` from files and the skill `description` | K1 |

## 12. Tests

Playwright with the fake CLI, and axe in light and dark, per screen. Each case below is a named test.

**Build**
- Sub-agents:
  - add, name, and see the tool name;
  - pattern and duplicate problems, with `aria-invalid`;
  - a CLI clash shown in the preview;
  - the hidden count status at 450 and 500;
  - provider defaults;
  - unique ids with two sub-agents and the agent (axe);
  - the agent's tool version enforced, and marked after the agent changes it;
  - MCP tools limited to the agent's grants, and a lost grant marked and removable;
  - limits;
  - five, then `Action`'s reason;
  - Remove with and without the confirm, its focus and announcement, and the eval-case count;
  - Details open for new and invalid sub-agents.
- The turn cap: shown with a sub-agent, with its default.
- Runtime: the in-process fallback list; the runtime the v2 spec pins; no capable runtime (the
  muted line); a #1085 switch that marks picks.
- Skills:
  - the picker and the full description for picked skills;
  - the clamp and Show button for unpicked ones;
  - Review in Registry;
  - the description-changed warning on a version switch;
  - a partly scripted name (only its scripted versions disabled);
  - the running total;
  - the empty state with Read again;
  - `keepOffered`.
- Eval checks offered only when relevant, and a case naming an absent sub-agent.
- The preview's blocks, `runtime:`, aliases and keys; the parts table; unverified wording;
  Create off.
- A v1 form previews byte-identically and sends `schema_version: 1` (golden); a v1 body with
  `subagents` is `SPEC_INVALID`; the maximum v2 form fits the caps.
- Keyboard-only add and remove; 320 px, 375 px and 200% zoom.

**Agent detail**
- The rows and both cards with a fixture `agent.yaml`.
- "none" with an older answer.
- The jump buttons focus the `h3`.
- The Skills Kind values and the "Could not be read" state.

**Try it**
- Both rows from a fixture reply.
- Absent for a 1.3.0 reply, and "None asked" only for an agent with sub-agents.
- Partly and fully unmeasured cost wording.
- The `ask_billing (sub-agent)` label.

**Traces**
- The badge on the boundary span only, and the hidden prefix on first-level spans only.
- Skill load span names by outcome.
- Error spans and the list lines.
- An ADK 1.3.0 trace unchanged.

**Cost**
- The per-sub-agent table with priced, unpriced and mixed rows.
- The S0 "not available" line.
- Absent without sub-agents.

**Registry and renderer**
- The Skills list with the four Code values, and Code in the versions table.
- The detail sections: the callout, the toggle (one per page, focus kept), a reference rendered, a
  `.txt` shown, a reference over the cap, the scripts list and notice, the description-changed
  diff line.
- The import confirm for a pack and for a script skill, and the file-based cross-check.
- Renderer:
  - the XSS corpus;
  - the growth test;
  - the sink grep test;
  - link destinations, userinfo, punycode and `mailto:`;
  - entities kept;
  - titles and definitions visible;
  - heading levels;
  - nesting caps and the token-cap fallback.

**Server tests** (pytest) cover each changed route's allow-list:
- over-long or malformed `subagents`, `skills`, span fields, `by_subagent` rows and `skill` show
  fields are dropped or refused;
- the v1 and v2 body rules;
- `reads.skills`: cache invalidation on a registry change, and a hash mismatch with the lock.

**Acceptance** (UI-B3), recorded with the Playwright video method (`tests/ui`) on the AC fixture
agent:
1. build an agent with two sub-agents and two skills; Create is on once AC has marked it verified;
2. release it;
3. Try it, with one question routed to a sub-agent and one that loads a skill, then open the trace;
4. the detail cards and the cost split.

## 13. Slices

| Slice | Lane | Contents | Needs |
|---|---|---|---|
| UI-R1 | B | `lib/markdown.js`, `.md` CSS, the renderer tests | nothing (can start now) |
| UI-R2 | B | Registry ▸ Skills list and Code column, version detail, scripts, import confirm, diff line | K1 (with the §11 shapes) |
| TR | A (or with RT) | `phoenix_traces._span` reads `agentbreeder.subagent` and `agentbreeder.skill.*` (including the resource attribute named in RT), `summarize` adds `subagents` (count-once) and `skills`; `docs/traces.md`; tests with fixture spans, including `subagent` on every descendant | the RT attribute contract (fixtures can start before RT lands) |
| SP | A (coordinator agrees `agentspec.py`, `newagent.py`) | `agentspec` v2: `Literal[1, 2]`, v2 fields refused under v1, `runtime` (one bump with #1085), generating `subagents:`, `limits:`, `skills:`, the sub-agents' prompt versions, their aliases and provider keys in the stack (CC2), case `tags` and the new checks; `center/builder.request`/`summary` version and cap changes; the caps as `MAX_SPEC_BYTES_V1` (256 KiB) and `MAX_SPEC_BYTES_V2` (512 KiB) in `agentspec` and mirrored in `center/builder` (the file read uses the larger cap, then a v1 spec over 256 KiB is refused); the shared capabilities helper; `docs/commands/new.md` | AY, K1, EV's rule names |
| UI-B1 | B | Agent detail rows and cards, `reads.skills`, Try it rows, Traces badge, names and lines, cost table | AY, K1, RT, TR, EV, S0 |
| UI-B2 | B | Build: runtime choice, the Sub-agents section, the Skills picker, eval checks, verified wording | SP, AC (`VERIFIED`); #1085 S1 if it lands first |
| UI-B3 | B | Acceptance recording (§12) | AC, UI-B1, UI-B2 |

UI-B1 can land before AC, because it only shows what a file or reply already has. UI-B2 waits for
AC, so Create is never on for something not run.

## 14. Changes to the plan (PR #1098) and the specs

The changes below belong in the plan, in #1098 if it is still open or in a follow-up. They are
recorded here so they are not lost:

- **New slices TR and SP** (§13). The plan has neither; it assigns span reading to no slice and the
  agentspec CLI work to UI-B.
- **UI-B is split** into UI-B1 (before AC), UI-B2 and UI-B3 (after AC). UI-R is split into UI-R1 (no
  dependency) and UI-R2 (after K1).
- **K1 gains** the exact `registry show`/`diff` `skill` shapes, the tri-state `code` on list rows,
  and a counts helper (§11), with tests.
- **RT names the resource attribute.** The skills spec §11 says "and the resource name"; RT names
  it `agentbreeder.skill.resource`.
- **RT and the sub-agents spec §7.6 fix `cost_unknown`** as an integer count of unmeasured calls
  (the runtime tally is a count).
- **EV's `by_subagent`** reuses the existing bucket fields and exists only after S0's "tags"
  verdict.
- **`VERIFIED` shape.** AC adds flat tuples, but #1085 makes `VERIFIED` per runtime
  (`{runtime_name: frozenset}`). Whichever lands second rebases onto the other.

## 15. Owner decisions (open)

| # | Decision | Recommendation |
|---|---|---|
| CC1 | Markdown renderer | **Our own bounded subset renderer to Preact nodes** (§9.3), not a vendored library with `html: false`. The console has no build step and vendors only Preact and htm, and rendering to nodes removes the HTML sink entirely. The skills spec names markdown-it only as an example, and this meets its rules. |
| CC2 | A sub-agent's gateway alias on Build | **The CLI decides.** The same provider and model id as the agent reuses its alias; otherwise the sub-agent gets a new alias, which the preview shows. |
| CC3 | Edit skills in the console | **Not in v1.** Fork into the project and edit the files. |
| CC4 | Show script skills on Build before K6 | **Yes, disabled with the reason** (§4.3). |
| CC5 | "Description changed" on the skill diff | **Yes**, from `registry diff --json` (K1), never by the browser reading hunks. |
| CC6 | A sub-agent filter on Traces, or a call graph | **No in v1.** The per-trace lines and the badge cover it. |
| CC7 | Markdown tables | **Shown as source in v1.** |
| CC8 | Where the Skills card's Kind comes from | **A separate in-process read, `reads.skills`** (§11), cached on the registry and lock, not a field on the uncached-by-registry agent read. |
| CC9 | Removing a sub-agent | **Confirm when it has a description or prompt; no undo.** |
| CC10 | Default provider of a new sub-agent | **The agent's**, so the default adds no provider key. |

## 16. Cross-repo

- **JSON Schema:** none. Spec v2 is documented in `docs/commands/new.md` by SP.
- **Cloud:** no change. It does not embed the local console.
- **Website:** no change until release R (#836). `command-center.mdx` then lists only what UI-B3
  recorded.

## 17. Review log

| Round | Reviewer | Result |
|---|---|---|
| r1 | `frontend-design` (Claude Sonnet, read-only) | CHANGES REQUESTED, 5 major. Folded in: no neutral `.notice` exists, so a `.callout` and `blockquote.quoted` are added and listed (§9.2, §10.3); the pickers take an id prefix and a state slice (§4.2); the mock is redrawn single-column with no new breakpoint (§4.2); collapsible Details per sub-agent and Remove placed in a summary row (§4.2); `.md` CSS and the heading outline (§9.3, §10.3). Minors: Skills and Scripts as `dl` lists, not tables (§5.3, §9.2); Code grain and a sidebar link (§9.1); section status lines muted (§4.4); clamped unpicked descriptions (§4.3); Try it cost kept short with the split in its own row as a list (§6); badge holds only the word (§7.1); cost table label, `td.num` and note (§8); confirm on Remove and invalid state (§4.2, §10.1); more states and widths (§10). |
| r1 | `ui-ux-pro-max` (Claude Sonnet, read-only) | CHANGES REQUESTED, 5 major. Folded in: confirm on Remove (§4.2, CC9); Review in Registry and the description-changed warning on Build (§4.3); the formatted view hides nothing the model reads (entities, titles, definitions, full destinations), its frame and the Source label (§9.2, §9.3); nesting and token caps and a growth test (§9.3); the CLI strips and `<Text>` escapes, with `stripped` a boolean (§9.3). Minors: `Action`'s disabled button (§4.2); fieldset focus and renumbering (§4.2); the count's live regions and `aria-invalid` (§4.2); the isolation sentence on Build (§4.2); copy for unshipped script skills (§9.2, §9.4); names in the L1 total (§4.3); the runtime line from data (§4.1); jump buttons (§5.1); span names by outcome and a hidden sub-agent prefix (§7.1); "at least" and when "None" rows show (§6); link rules for userinfo, punycode and `mailto:` (§9.3); one toggle per page (§9.2); the eval-case count on Remove, partly scripted names, Read again, the agent's tool version enforced (§4.2, §4.3). |
| r1 | `/architect` (Claude Sonnet, read-only) | CHANGES REQUESTED, 6 major. Folded in: `parts.runtimes` with an in-process fallback, and a v2 spec that pins the capable runtime while v1 stays on `newagent.RUNTIME` (§4.1, §4.5); a CLI slice SP for agentspec v2, one bump with #1085 (§4.5, §13, §14); `reads.skills` as its own cached, allow-listed read, and the §11 intro now names the agent read's pass-through (§5.3, §11, CC8); cost reuses the bucket fields and needs S0's "tags" verdict (§8, §11); slice TR (§13, §14); K1's exact shapes (§11, §14). Minors: dependencies and the plan deviation (§13, §14); tri-state `code` (§9.1, §11); the live clash check limited to what the parts answer has (§4.2); v1/v2 rules and caps, and the skills description cap (§4.5, §11); `cost_unknown` as a count (§11, §14); the TR descendant test (§7.1, §13). |
| r2 | all three lenses (Claude Sonnet, read-only) | **APPROVED** (no blocker or major). Minors folded in: one version per tool name across all sub-agents (§4.2); a problem opens its nested `<details>` (§4.2); focus on Cancel (§4.2); the pinned runtime shown before Preview, the server's `SPEC_UNSUPPORTED` check and the shared capabilities parser (§4.1, §13); the hidden sub-agent prefix once per first level (§7.1); "(full address in Source)" (§9.3); `.md` heading size, `hr` token, `.sub-summary` as a `div`, `.clamp-2` note, focus outlines (§10.3); the v2 cap in both places (§4.5, §13); why the `reads.skills` hash check stays (§11). |
| r3 | narrow check of the r2 folds, all three lenses (Claude Sonnet, read-only) | **APPROVED** (no blocker or major). Minors folded in: the §12 prefix test matches §7.1; the runtime line shown per section used (§4.1); SP makes the capabilities parser public and an invalid runtime is listed unusable (§4.1); focus outlines use `:focus` with a test (§10.3); the full-address note once (§9.3); `reads.skills` caches only refs, counts and descriptions and recomputes `locked` (§11); two named spec caps (§4.5, §13). |
| r4 | narrow check of the r3 folds (Claude Sonnet, read-only) | **APPROVED**, no findings beyond optional wording; the #1085 §4.1 citation checked (unusable runtimes listed as text). |
