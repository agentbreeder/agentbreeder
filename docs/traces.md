# Agent traces from Phoenix

`agentbreeder traces` lists recent agent traces per deployment: the span tree, models, token
counts, tool calls and latency. It reads them from the stack's Phoenix (`service/phoenix`,
see [services.md](services.md)) (#907). It is on `main`, not in a published preview.

```bash
agentbreeder traces --stack DIR                       # every deployment whose container ran
agentbreeder traces ID --stack DIR --last 50          # one deployment (ID from `list`)
agentbreeder traces --trace TRACE_ID --stack DIR      # one trace, as a tree
agentbreeder traces ID --stack DIR --since 2026-11-13 --json
```

`--environment ENV` picks the stack environment when there is more than one. With no `ID`, the
deployments listed are those that are starting, ready, removing or disabled (not removed, failed or
awaiting approval). `--last N` (1–200, default 20) applies per deployment. `--since` takes a UTC day or an ISO time. `--project NAME` (with
`--trace`) names the Phoenix project when the trace's deployment isn't in this state.

## Where the traces come from

- **Phoenix:** the stack's single `service/phoenix@*` service, on its loopback host port
  (`http://127.0.0.1:<port>`, from the stack state). `--stack DIR` is required: without a stack
  there is no Phoenix (`TRACES_NO_PHOENIX`).
- **Key:** the stack's stored `ingest-key`, the system API key Phoenix's bootstrap mints. In
  Phoenix it is **admin-equivalent**: it could also write or delete. `traces` only sends it with
  `GET` to that loopback URL. It never prints it, never follows a redirect, and never uses a
  proxy (`HTTP_PROXY` is ignored). A read-only VIEWER key (phoenix@1.1.0) is a planned
  follow-up.
- **Known limit:** the port is the one `services up` recorded. Only the host is fixed, not the
  listener. If Phoenix is stopped and another local process takes that port, the key goes to it.
  This is the same exposure as the other stack keys the CLI uses on loopback (the gateway master
  key).
- **API (phoenix 21.0.0):**
  - `GET /v1/projects`
  - `GET /v1/projects/{project}/traces?start_time&end_time&sort=start_time&order=desc`
  - `GET /v1/projects/{project}/spans?trace_id=…` and `?parent_id=null&attribute=agentbreeder.deployment_id:"<id>"`

  Pages are at most 1000 rows, with at most 20 pages per listing, 500 spans per trace, 16 MiB
  per answer and a 512-character page cursor. Each request has a 10 s timeout, and one command
  makes at most 400 requests within 120 s (`TRACES_LIMIT`).

## Which deployment a trace belongs to

The runtime names its Phoenix project after the agent, so `X` and `X-candidate` share project
`X`. A deployment ID is attributed in one of two ways:

| `attribution` | How |
|---|---|
| `exact` | The trace's root span carries `agentbreeder.deployment_id`. Deploy gives every model-access container `AGENTBREEDER_DEPLOYMENT_ID`, and a runtime that records it as that root-span attribute (`runtime/adk@1.2.0` does, on `agent_turn`) is attributed exactly. |
| `window` | The runtime doesn't record it (`runtime/adk` ≤ 1.1.0), so the trace started in the project during the deployment's lifetime: from its creation to its removal, or to its last update when it failed or was disabled, or until now. A deployment awaiting approval never ran and has no traces. |

A deployment's runtime never changes, so either all of its traces are `exact` or none are.
`exact` means only that the agent's runtime reported this ID; it is not authenticated. An
agent's own code could put another deployment's ID on its spans. Only a 32-hex ID counts; any
other value is treated as absent.
- **Others' traces:** in window mode, traces that carry another deployment's ID are left out.
  Three times `--last` are read for that, so when a busy tagged deployment shares the project,
  fewer than `--last` may still be shown.
- **`overlapping`:** when a trace is window-attributed, any other deployment of the same project
  whose lifetime overlaps and whose traces carry no ID is listed in `overlapping` (and warned
  about on stderr), because its traces can't be told apart.
`--trace` reports the deployment only when exactly one candidate fits.

## What is shown, and what never is

Shown: span names, kinds (OpenInference), status, latency, model names, token counts, tool
names and error messages. Token counts come from each span's `llm.token_count.*`, and a trace's
total is their sum over its spans, as Phoenix computes it. Phoenix also maps `gen_ai.*` usage
onto those attributes. Never read out: message content
(`input.value`, `output.value`, tool arguments and results, retrieved documents), because it
holds customer messages. Every string from Phoenix is redacted with the same stored-secret values
as `logs`. If one of them can't be read, nothing is printed (`REDACTION_INCOMPLETE`). Each
string is then flattened to one line without control characters and cut (200 characters, 300
for errors).

## `--json` (schema_version 1)

The console's Try it uses `traces --trace TRACE_ID --stack DIR --environment ENV --json` for a
turn's **Trace spans** panel (see [command-center.md](command-center.md)).

```json
{
  "schema_version": 1,
  "phoenix": "http://127.0.0.1:57340",
  "traces": [
    {
      "trace_id": "40534d25d484451c76d0f23168d19418",
      "project": "northwind-support",
      "deployment_id": "18bc2abed83946b6a52c045e973d1844",
      "attribution": "window",
      "start_time": "2026-11-14T03:10:33.358423+00:00",
      "end_time": "2026-11-14T03:10:33.399734+00:00",
      "latency_ms": 41,
      "status": "error",
      "session_id": "s-1",
      "turn_id": "turn-1",
      "models": ["gemini-2.5-flash"],
      "tokens": {"prompt": 321, "completion": 70, "total": 391},
      "tool_calls": [
        {"name": "lookup_order", "status": "error", "latency_ms": 11, "span_id": "b1eaa89e532f0dc9"}
      ],
      "spans": [
        {"span_id": "a623a1e273310546", "parent_id": null, "name": "agent_turn", "kind": "AGENT",
         "status": "unset", "start_time": "…", "latency_ms": 41, "model": null, "tokens": null,
         "tool": null, "error": null}
      ],
      "spans_truncated": false
    }
  ],
  "overlapping": [],
  "truncated": false,
  "notes": []
}
```

- `spans` is flat, in start order. The tree comes from `parent_id`; a span whose parent isn't
  in the list is a root.
- `status` is `ok|error|unset` per span (a span with `tool.error` is `error`), and `ok|error`
  per trace.
- `notes` explain empty results: a deployment whose agent has no Phoenix project yet, one that
  never ran (awaiting approval), or one that stopped before `--since`.
- With `--since`, a deployment whose runtime tags its traces shows only its own traces in that
  window (possibly none). It never falls back to the window.
- `deployment_id` and `attribution` are null when `--trace` can't attribute the trace.
- `truncated` (and `spans_truncated` per trace) means a limit cut the answer. Reaching `--last`
  doesn't set it.
- Errors: `[CODE] message` on stderr, and with `--json` also `{"schema_version": 1, "error":
  {"code", "message"}}` on stdout, exit 2. Codes: `TRACES_NO_PHOENIX`, `TRACES_UNREACHABLE`,
  `TRACES_AUTH`, `TRACES_PHOENIX_ERROR`, `TRACES_LIMIT`, `TRACES_NOT_FOUND`, `TRACES_SINCE`, `ID_INVALID`,
  `TRACE_ID_INVALID`, `PROJECT_INVALID`, `NOT_FOUND`, `USAGE`, `REDACTION_INCOMPLETE`, and the
  state codes (`STATE_*`).

## Not yet

- Traces of eval runs linked from `release`.
- The console's Traces tab.
- Message content (`--content`).
- A read-only Phoenix key.
