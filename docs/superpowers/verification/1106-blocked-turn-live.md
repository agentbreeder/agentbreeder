# #1106 verification: a guardrail-blocked turn keeps no matched text (live, Presidio + Phoenix)

Status: **verified from `main`** (dabd828c, after #1149 merged, 2026-11-19 UTC, macOS 13.7.8
x86_64, Docker Desktop engine 27.5.1). On `runtime/adk@1.5.0`, behind `service/litellm@1.3.0`
with a required Presidio floor and Phoenix, a turn the gateway blocked for a US SSN left the
value in no `turns` row, audit row, Phoenix span or container log. The same turn on
`runtime/adk@1.4.0` (the control) kept it in `turns.message` and in the `agent_turn` and
`retrieve_knowledge` span inputs. The ADK session still holds the message on both, as documented.
No container, volume or network of the stack was left; image cleanup against the snapshot is
not verified here.

One finding outside #1106: the sample SSN first tried, `078-05-1120`, is **not detected** by
Presidio's US_SSN recognizer (the recognizer lists it as a known sample number and drops it), so
that turn was not blocked and reached Gemini. The blocked turns used `536-22-8174`,
the synthetic SSN in `tests/test_guardrails_docker.py`.

This is on `main`, not in a published preview. It is a record only: no product code changed.

## What changed (1 PR)

| PR | Merged | Content |
|---|---|---|
| #1149 | on `main` before dabd828c | `runtime/adk@1.5.0`: a blocked turn's `turns` row gets `[blocked by a gateway guardrail]`, empty `sources` and `evidence`, and an error of `MODEL_BLOCKED: ENTITY by guardrail NAME`; its spans are exported bare (ids, times, kinds, counts, labels) |

The fix was unit-tested in #1149 (scripted model, fake gateway, in-memory span exporter); its
review rows and suite results are in that PR. The suite was not re-run for this record, which is
the live acceptance run the issue asks for.

## Wheel from main (clean sandbox)

`main` at dabd828c, no tracked changes, exported with `git archive` and built with
`python -m build --wheel` (`agentbreeder-3.0.0a3-py3-none-any.whl`, sha256
`b253c1bb7a3c2eb65f1c1b7e87e2b0225e2419e8f7e2034d91ed3fead4252e8f`), installed with `[eval]`
into a fresh Python 3.12.8 venv. Every command ran under `env -i` with a fresh `HOME` and
`TMPDIR` inside the sandbox (`$SB`, under `/tmp`), `PIP_NO_CACHE_DIR=1` and `USER=demo`. A Docker
context was created inside the sandbox `HOME`. `GEMINI_API_KEY`, `TAVILY_API_KEY` and
`OPENROUTER_API_KEY` were copied from `~/.env` into one 0600 file given only to `services up
--env-file`; it was deleted at the end. A keepalive container (`alpine:3.20`) ran throughout so
Docker Desktop's Resource Saver could not stop the VM.

### The project

A copy of the wheel's Northwind example (`examples/northwind`), changed by hand:
- `agent.yaml`: `runtime: runtime/adk@1.5.0` (then `1.4.0` for the control).
- `stack.yaml`: `service/presidio-analyzer@1.0.0` and `service/presidio-anonymizer@1.0.0` added
  and wired into the gateway, the gateway moved to `service/litellm@1.3.0`, and
  `environment.guardrails: {required: true, approved_sha256: 670fb270…}`.
- `stack/litellm/config.yaml`: `disable_error_logs: true`, and one guardrail:

```yaml
guardrails:
  - guardrail_name: pii-block
    litellm_params:
      guardrail: presidio
      mode: pre_call
      default_on: true
      pii_entities_config: {US_SSN: BLOCK, CREDIT_CARD: BLOCK}
      presidio_language: en
      timeout: 30
```

Northwind keeps its knowledge base (`knowledge/northwind-help@1.0.0`, so every turn has a
`retrieve_knowledge` span), `traces: otlp` through its proxy to `service/phoenix@1.0.0`, and
`agent_events: postgres`. The agent was built and deployed with `build` and `deploy --local`, not
`release`, so Northwind's evals did not run.

No scripts: each step was one `agentbreeder` command or one `psql`, `pg_dump` or `docker logs`
call, run by hand from 16:04 (snapshot) to about 16:26 UTC (teardown). Times below are the
client's clock when the command started; the stored `turns.created` of the blocked 1.5.0 turn is
16:22:16.3.

| Check (in the order run, UTC) | Result |
|---|---|
| `services up` with `required: true` and no sha | refused: `[STACK_INVALID] … a required guardrails floor needs approved_sha256` (as documented) |
| `services up --update` with `required: false` | exit 0; printed `guardrails sha256 670fb2701c77c176…` (`pii-block (pre_call): CREDIT_CARD BLOCK, US_SSN BLOCK`) |
| `services up --update` with `required: true, approved_sha256: 670fb270…` (16:20) | exit 0; `guardrails floor: required; approved sha256 … (matches the running config)`; operator config `effective: [presidio:pre_call]` |
| `build` + `deploy --local` on `runtime/adk@1.5.0` (16:21) | built, deployment `b94266b7…` `ready` |
| control turn "What are your support hours?" (16:21:52) | 200, 2 model calls, `retrieval: hybrid` |
| turn with `078-05-1120` (16:22:03) | **200, not blocked**: Gemini answered (1 model call). See "Notes" |
| turn with `536-22-8174` (16:22:15) | **422 `MODEL_BLOCKED`**, trace `baf6f81c…`, turn `a3a1e29c…` |
| `remove`, then `build` + `deploy --local` on `runtime/adk@1.4.0` (16:23) | deployment `2ecde7f3…` `ready` (the same name with another spec needs `remove` first: `DEPLOYMENT_ERROR`) |
| turn with `536-22-8174` on 1.4.0 (16:23:50) | **422 `MODEL_BLOCKED`**, trace `fb713689…`, turn `3d319eb7…` |

## What each store held for the blocked turn

How each column was checked:
- **1.5.0, saved.** `pg_dump` of the `support`, `phoenix` and `litellm` databases and `docker
  logs` of every container, all taken after the 1.5.0 turns and before the 1.4.0 deploy, searched
  for `536-22-8174`, `536228174` and `536 22 8174`; plus `psql` reads of the turn's row, audit
  rows (saved) and spans.
- **1.4.0, not saved.** `psql` queries against the live stack as the database owner
  (`turns::text`, `audit_events::text`, Phoenix `spans.attributes` and `spans.events` with
  `like '%536-22-8174%'`, the turn's row and the trace's spans with the SSN masked on output), and
  `docker logs` of the agent (saved) and the gateway, run after the 1.4.0 turn. Their output was
  read in the session but not written to files, and the stack was then torn down, so the 1.4.0
  column cannot be re-checked from saved files. Only the agent log and the invoke reply (422,
  trace `fb713689…`) were saved.

| Store | `runtime/adk@1.5.0` | `runtime/adk@1.4.0` (control) |
|---|---|---|
| `turns.message` | `[blocked by a gateway guardrail]` | the full message, SSN included |
| `turns.error` | `MODEL_BLOCKED: US_SSN by guardrail pii-block` | `BadRequestError: litellm.BadRequestError: OpenAIException - Blocked entity detected: US_SSN by Guardrail: pii-block. …` (no value) |
| `turns.sources`, `evidence` | `[]`, `[]` | 3 source titles and their chunks |
| `audit_events` (4 rows for the 1.5.0 turn) | `message_received` (`chars: 48`), `knowledge_retrieved` (chunk ids), `turn_failed`, `guardrail_blocked` (`entity: US_SSN`, `guardrail: pii-block`): no value | no value |
| Phoenix `agent_turn` span | no `input.value`; `agentbreeder.guardrail.{name,action,entity}`, `agentbreeder.error_code: MODEL_BLOCKED` | `input.value` = the message, SSN included |
| Phoenix `retrieve_knowledge` span | `retrieval.mode` only | `input.value` = the message; the retrieved documents' text |
| Phoenix ADK spans (`invocation`, `invoke_agent`, `call_llm`, `generate_content`) | kind and model labels only; names cut to the first word; span events keep `exception.type` (`litellm.exceptions.BadRequestError`) with an empty message | no SSN (checked: 2 of the trace's 6 spans held it, `agent_turn` and `retrieve_knowledge`) |
| Phoenix, all spans of every trace | 0 spans with the SSN in the 1.5.0 dump | 2 spans in all after the control, both in the 1.4.0 trace |
| Agent container log | 0 lines with the value; 1 line, `model call failed: … code=MODEL_BLOCKED status=400 detail=MODEL_BLOCKED: US_SSN by guardrail pii-block`, which holds only the entity and guardrail names | 0 lines with the value; the line holds the gateway's text (entity and guardrail names, no value) |
| Gateway, Presidio, Phoenix, Postgres, proxy, ops logs | 0 lines | 0 lines (gateway checked) |
| LiteLLM database (`pg_dump`) | 0 | not dumped again |
| `adk_sessions.events` | **holds the message** (1 row) | holds the message (1 row); 2 rows in all |

The `adk_sessions` result is expected: #1149 and `docs/agent-yaml.md` say the ADK session still
holds the message and sends it with the session's next turn.

In both versions retrieval ran as `keyword-only (vector search unavailable)`: the gateway
blocked the turn's embedding call before the model call was blocked (as in the 2026-11-16 run
that found #1106). For 1.5.0 the saved audit row (`keyword-only`) and the gateway log's errors at 16:22:16 are
consistent with that, not proof that the embedding call was the one blocked; for 1.4.0 it was
read from the `retrieve_knowledge` span, not saved.

## Acceptance (from the issue)

| Check | Result |
|---|---|
| A blocked turn leaves no matched value in `turns`, Phoenix spans or logs (a real-Docker check with Presidio) | **PASS on `runtime/adk@1.5.0`**: 0 hits in `turns`, `audit_events`, Phoenix spans (attributes and events) and every container log. The control on `1.4.0` kept it in `turns.message` and 2 spans |
| the docs state exactly what is stored | `docs/agent-yaml.md` (#1106 bullet, "Still stored: the conversation in the ADK session") matches what was observed; this PR adds the run to it, `docs/traces.md` and `docs/services.md` |

Flows that need a model key: Gemini (`gemini/gemini-3.8-flash`, `gemini-embedding-2`) through the
gateway. The blocked turns sent nothing to a provider; the control turn and the `078-05-1120`
turn did (3 chat calls and their embeddings). Spend was not tallied.

## Browser click-through

Not run: the check reads Phoenix's database tables directly. Neither the Phoenix UI nor
`agentbreeder traces --trace` was used.

## Leak scan and teardown

- **Leak scan.** Every saved output (invoke JSON, audit rows, build, deploy and `services`
  outputs, container logs, database dumps: 28 files), this record and the PR's diff were scanned
  for the three key values in the sandbox env file (each over 12 characters), plain, base64 and
  URL-encoded, and for a home-directory path: 0 key hits; the only home-path hit was this
  sentence's own search term before it was reworded. Saved outputs held the sandbox path only.
  The SSNs in this record are synthetic.
- **Teardown.** `remove` of both deployments and `services down --purge --yes` exited 0
  (`Removed volume agentbreeder-northwind-1f883b69-postgres-data`, the two project service
  images and the stack state). No container, volume or network of the stack was left.
- **Exit codes.** Read from the shell, not saved to files.
- **Sandbox.** The key file was the only key file under the sandbox; it was deleted with the
  sandbox. No `agentbreeder` process or port of the stack was left after `services down`.
- **Images and snapshot.** Not verified in this record: the same session ran other Docker tasks
  after this one, and its images are removed against the 16:04 UTC snapshot at the end of the
  session (the lane report states that result).

## Notes

- **`078-05-1120` is not blocked.** Presidio's US_SSN recognizer rejects a few well-known
  sample numbers: `invalidate_result` in
  `presidio_analyzer/predefined_recognizers/country_specific/us/us_ssn_recognizer.py` (read in
  the pinned analyzer image) lists `000`, `666`, `123456789`, `98765432` and `078051120`. So the
  gateway passed it to Gemini and the agent stored and traced it like any turn. A real SSN-shaped
  value is detected; a test or demo that uses `078-05-1120` to show a block will see none.
- **Span names.** On 1.5.0 the blocked turn's ADK spans are exported as `invoke_agent` and
  `generate_content` (first word only), as `docs/traces.md` describes.
- **Not run:** Linux, `kind`, another provider, `traces --trace` and the Phoenix UI, retrieval
  with a working embedding call on a blocked turn, a block on the model's reply (post-call), a
  block after a tool call (so the audit rows of earlier tool calls, which 1.5.0 keeps, were not
  exercised), and `MASK` entities.
