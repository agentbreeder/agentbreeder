# M17 CLI: `agentbreeder new agent NAME --from-spec FILE` (#833)

Status: built on `feat/833-agent-builder` (2026-11-13). Plan of record: the console epic plan, M17.
Design: console spec §4.5 ("Build brand-new") and §8.5, guided-path spec §14.7. This note covers the
CLI half. The console's builder form is lane B's.

## Problem

The console's builder needs a server-side way to turn a form (name, model, prompt, evals, verified
tools and MCP servers) into a project. The browser must never build YAML (§14.7), and the prompt
must not travel through argv.

## Scope (coordinator, 2026-11-13)

- `new agent NAME --from-spec FILE [--dir DIR] [--dry-run] [--json]`; the spec is JSON in a private
  temp file. `--dry-run --json` is the preview: it carries the full text of every file.
- The project is the starter's: prompt and evals published as in `new agent`, the stack from the
  `--stack-for` template (`stackfor.render`, extracted; `--stack-for` output is byte-identical,
  golden-tested), MCP grants added with `mcpgrant.change`, then `agent.lock`.
- `stackfor._agent_entry` stays the single #909 identity seam.
- Knowledge is refused (`SPEC_UNSUPPORTED`) until a knowledge combination has been run (after M14).
- Unverified combinations are reported (`combination.verified: false`), not refused; the console
  enables only verified ones. Today only gemini + prompt + evals is verified (`agentspec.VERIFIED`).
- Picked versions are not re-published into the new registry; they must resolve over the roots the
  new project resolves, and `agent.lock` (written after the move when a ref comes from
  `AGENTBREEDER_REGISTRY_PATH`) records a source relative to the project.
- Out of scope: the console form, knowledge, memory, the live acceptance (batched M17 verification).

## Design

- **Reading the spec.** `os.open(O_NOFOLLOW|O_NONBLOCK)`, then `fstat` on the descriptor: a regular
  file owned by the effective user with no group or other bits. At most 256 KiB, UTF-8, strict JSON
  (duplicate keys and NaN/Infinity refused). A pydantic model with `extra="forbid"` and `strict=True`
  checks the shape; errors name the field, never the value.
- **Text.** Prompt text, turns, references and patterns refuse control and bidi characters
  (`registry.clean`; tab and newline allowed). The prompt goes into `prompt.md` as is; the cases go
  into `golden.jsonl` with `json.dumps`. Refs and MCP tool names are pattern-checked and JSON-quoted
  in YAML. The rendered `agent.yaml` is parsed back and must equal the spec's name, tools, MCP
  entries, threshold, retries and model calls (`TEMPLATE_INVALID` otherwise).
- **MCP.** Each server is named after its ref (`mcp/github@1.0.0` → `github`), which must be a valid
  stack name and not `postgres` or `litellm`; its tools must be declared in its `mcp.yaml`. The
  grants are made by `mcpgrant.change` on a private temp copy of the rendered `stack.yaml`, so the
  preview shows the file that is written. `keys` adds each server's imported keys.
- **Writing.** `newagent.create` with the spec's files: the same destination checks, staging,
  publish, validation (`load_agent_file`, `load_stack`, the gateway review) and rollback. When a ref
  resolves from a path root, `agent.lock` is written in the final directory after the move (its
  source labels must be relative to the project, not the staging directory); a failure removes
  everything that was moved.

## S-34 security delta (`new agent --from-spec`)

| # | Concern | Finding | Disposition |
|---|---|---|---|
| 1 | Prompt in argv or logs | The prompt travels only in the spec file. Errors name fields, never values (a test checks a prompt never appears in output); an unknown key is the sender's text and is cut to 80 characters, and `MCP_TOOL_UNDECLARED` names pattern-checked tool names. | Mitigated. |
| 2 | Another user planting or reading the spec | A link, FIFO, another user's file or a group/other-accessible file is refused, checked on the open descriptor. | Mitigated. |
| 3 | YAML injection | No user text is placed in YAML except checked names, refs and tool names, JSON-quoted; the prompt and cases go to `prompt.md` and JSON lines. The agent file is parsed back and compared; the stack is validated and the grant edit is re-parsed by `mcpgrant`. | Mitigated. |
| 4 | Code that runs in agents | A picked tool or MCP server from `AGENTBREEDER_REGISTRY_PATH` is code. `refs[].runs_code`, `root_kind` and `runs_code` are in the preview, and the text output names it, so the console can show its "this code runs in agents" confirm before writing (as in onboarding, S-01). | Mitigated; the confirm is the console's. |
| 5 | Widening MCP grants without consent | The grants are written to a new `stack.yaml`; nothing runs until `services up`, whose consent shows each agent's tools and effects. Only declared tools are granted. | No new path past consent. |
| 6 | Preview/write drift | The preview carries `plan_sha256` over every file's text, each picked version's content digest (`refs[].sha256`) and the destination; the write with `--expect-digest` refuses (`DIGEST_MISMATCH`) if the spec, a picked version or the date changed. The dry run also validates the stack and gateway config, so a preview that passes is one the write accepts. | Mitigated (the console must pass the digest). |
| 7 | Writing outside the destination | The starter's destination rules apply (absent or an empty directory you own, never a symlink, staged then moved). | Mitigated (unchanged). |
| 8 | Terminal escapes | Output goes through `safe_text.visible`; spec values are never echoed, and key names only cleaned and cut short. | Mitigated. |
| 9 | Secrets | The spec holds no keys; `keys` lists names only. | n/a |
| 10 | ReDoS through stored patterns | `must_match` and `must_not_match` patterns run later in `release` against model replies (`re.search`, no timeout). Refused, from a `re._parser` parse (present on 3.11–3.13): nested repeats, repeated alternatives, backreferences, more than two variable repeats and alternations together (a fixed repeat counts its body that many times), and patterns over 200 bytes. What remains is polynomial: two adjacent repeats such as `a*a*b` cost O(n³): about 2 s on a 1,000-character reply and 15 s on 2,000, so a hostile pattern can stall `release` for seconds to minutes. The patterns are the local user's own, `release` can be interrupted, and hand-written eval sets have the same exposure. | Partly mitigated; an eval-time match timeout in `evals.py` is the complete fix (follow-up). |

## Acceptance

- Offline (tests/test_agent_spec.py): gemini + prompt + evals writes a project that validates, with
  a lock; the preview text equals the written files (tool + MCP); MCP grants land in the stack; a
  path-root tool locks with a relative source and passes a faked `build --locked`; refusals for
  every input class; rollback on a failed lock.
- Live (batched M17 verification): from the console form, an agent with gemini, a prompt, evals and
  one tool is created, brought up and released.

## Review log

Different-model review (Sonnet, read-only subagents; coder Opus).

| Round | Lens | Verdict | Findings and disposition |
|---|---|---|---|
| 1 | Security | No High; changes requested | (M) Exponential-backtracking patterns stored for `release`: refused (S-34 #10). (L) The write was not bound to the preview: `plan_sha256` with the version digests, and `--expect-digest` with `--from-spec`. (L) Unknown keys echoed at any length: cut to 80. (Nit) A bidi ref name: already refused by `Ref.parse`. Checked sound: the spec read, YAML injection, grants, lock-after-move rollback, output escaping. |
| 1 | Correctness, tests, docs | Approved with notes | (L) A dry run skipped stack validation: `_preflight` loads the stack and reviews the gateway config. (L) Two knowledge entries or a non-object gave `SPEC_INVALID`: any entry is now `SPEC_UNSUPPORTED`. (L) CRLF refused: documented. Test gaps (bool as int, another provider) added. Checked: cases load in `evals`, the stack validates, the lock works with `build --locked`, console callers still pass `--model`. |
| 2 | Security and correctness | Changes requested | (M) Chains of optionals (`a?a?…aaa`, `(a?){18}a{18}`) and adjacent repeats (`a*a*a*b`) passed the pattern check: at most two variable repeats now, a fixed repeat counting its body (S-34 #10 records the polynomial residual). (L) A version replaced between the plan and the lock was locked: `agent.lock` must now hold each previewed `refs[].sha256` (`DIGEST_MISMATCH`). Checked sound: the digest coverage, truncation, the dry-run preflight, knowledge, `--stack-for` unchanged. |
| 3 | Security and correctness | Changes requested | (H) Sequential alternations (`(?:a|aa)` 25 times, `(?:a|ab){25}c`) passed and are exponential: each alternation now counts toward the limit of two. `Plan.pinned` rollback checked sound in both lock branches. |
| 4 | Security and docs | Approved | No bypass of the pattern check found (nested, optional, alternation, lookaround, atomic, bounded and lazy forms tried). The adjacent-repeat residual measured: `a*a*b` on 2,000 characters takes about 15 s (S-34 #10 updated). (L) Doc nits: rows reordered, `must_not_match` named, the cost stated. |
