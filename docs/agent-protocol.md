# Agent protocol v1

The HTTP contract an agent image built by `agentbreeder build` serves, written down so that
`deploy`, `invoke`, `release`, the console and a second framework's runtime can rely on one
definition (#758, multi-framework spec §10).

**Status:** on `main`, not in a published preview. Protocol v1 is what the built-in
`runtime/adk@1.3.0` serves: this page describes that runtime's behaviour from its source
(`src/agentbreeder/builtin_registry/runtime/adk/1.3.0/runtime/server.py` and `agent.py`). No
other runtime serves it yet. The conformance checks for it
([`agentbreeder conformance`](commands/conformance.md)) have not been run against a real
container; see the [ADK baseline record](superpowers/verification/F2a-adk-baseline.md).

A runtime version says which protocol it serves in its `manifest.yaml` (`protocol: 1` and a
`profile`, see [Runtime versions](registry.md#runtime-versions-on-main-not-in-a-published-preview)).
A `runtime/adk` version published before those keys existed is read as protocol 1, profile `v1`.

## What v1 does not have

- **No authentication.** Any caller that can reach the port can invoke the agent and can name
  any `customer_id`. `deploy` publishes the port on host loopback only. Bearer authentication is
  reserved for a later version (#780).
- **No streaming.** One request, one JSON reply.
- **No cancellation, resume or idempotency key.** A client that stops waiting does not stop the
  turn. Sending a request again runs a second turn; tools with side effects run again.

## Transport

HTTP/1.1 on the port in the runtime manifest (`port`, 8080 for `runtime/adk`). Every `/health`
and `/invoke` reply is JSON (`Content-Type: application/json`) with `Cache-Control: no-store`.
(`runtime/adk` also serves a chat page at `GET /`; it is not part of the protocol.)

The agent serves a request only when its `Host` header, without the port, is `localhost`,
`127.0.0.1` or `agent` (the agent's alias on a deployment network). `POST /invoke` with any
other `Host` is answered 404. The check stops a web page from driving a local agent through DNS
rebinding. `GET /health` is answered for any `Host`.

## `GET /health`

200 means the agent is serving requests. `deploy` waits up to 30 seconds for it.

`runtime/adk@1.3.0` answers 200 with `{"status": "ok", "model": "<alias>"}` as soon as it
listens, including while it is still starting and after a start that failed (the start is tried
again on the first request, which then reports the failure). A client must not read more than
"the server is up" from a v1 200. The kit profile below is stricter.

## `POST /invoke`

Runs one turn.

### Request

`Content-Type: application/json` and a `Content-Length` body of at most 64 KiB:

```json
{"message": "Where is order 1042?", "session_id": "s-1", "customer_id": "c-17"}
```

| Field | Type | Meaning |
|---|---|---|
| `message` | string, required | The customer's message: not empty after trimming, at most 4,000 characters |
| `session_id` | string, optional | The conversation. 1 to 64 of `A-Z a-z 0-9 _ -`. When absent the agent makes one and returns it |
| `customer_id` | string, optional | Who is asking, same characters. When absent the customer is the shared `guest` |

History and saved notes belong to a customer: the same `session_id` under another `customer_id`
is a different, empty conversation. The customer is set by the server from the request and is
never a value the model chooses.

### Reply (200)

| Field | Type | Meaning |
|---|---|---|
| `reply` | string | The agent's answer |
| `session_id` | string | The request's, or the one the agent made |
| `customer_id` | string | The request's, or `guest` |
| `model` | string | The gateway alias the agent calls |
| `framework` | string | The runtime's framework, as its manifest names it (`google-adk`) |
| `runtime` | string | The runtime version, `runtime/<name>@X.Y.Z` |
| `turn_id` | string | This turn's id; the `correlation_id` of its errors and log lines |
| `trace_id` | string or null | The turn's trace, 32 hex digits |
| `cost_usd` | number or null | The sum of the gateway's cost for the turn's model calls; null when any call had none |
| `fallbacks` | integer or null | How many gateway fallbacks served the turn; null when no model call was made |
| `served_by` | list of strings | The aliases that answered, when the gateway named them |
| `rate_limit_wait_s` | number | Seconds the turn waited on gateway rate limits |
| `sources` | list of strings | Titles of the knowledge passages used |
| `tools` | list | One entry per tool call |
| `web` | list | What a web search tool recorded |
| `tickets` | list | What a ticket tool recorded |
| `memory_saved` | list | Notes saved this turn |
| `kb_searches` | integer | Extra knowledge searches the model asked for |
| `model_calls` | integer | Model calls in the turn |
| `retrieval` | string | How knowledge was searched (`none` when the agent has none) |
| `prompt_tokens` | integer | Input tokens over the turn |
| `output_tokens` | integer | Output tokens over the turn |
| `mcp_unavailable` | list of strings | `mcp:` servers whose relay could not be reached this turn |

A tool may add its own list to the reply; it cannot replace a field above.

### Statuses

| Status | When |
|---|---|
| 200 | The turn's reply |
| 400 | The body is not the JSON object above, an id is not valid, or the body is empty, over 64 KiB or sent with `Transfer-Encoding` |
| 402 | `MODEL_BUDGET_EXCEEDED` |
| 404 | Another path, or a `Host` that is not allowed |
| 409 | A turn for this session is still running (`runtime/adk@1.3.0`: only while a turn that timed out is still stopping; see below) |
| 413 | The message is over 4,000 characters |
| 415 | The `Content-Type` is not `application/json` |
| 429 | `MODEL_RATE_LIMITED` |
| 500 | The turn failed for another reason (a fixed message) |
| 502 | `MODEL_ACCESS_DENIED`, `MODEL_REQUEST_REFUSED` or `MODEL_UNAVAILABLE` |
| 503 | The agent's database is unavailable, or the agent is misconfigured |
| 504 | The turn took longer than the runtime's deadline (170 seconds for `runtime/adk@1.3.0`) |

A request refused with 400, 404, 413 or 415 reaches no model and no tool. Every error body has
`error`, a message for a person.

**One turn per session at a time.** `runtime/adk@1.3.0` makes a second request for a session
wait for the first; it answers 409 only when an earlier turn timed out (504) and is still
stopping. The conformance check asks for more: a request that arrives while a turn for the
session is running is refused with 409. That is a known difference, listed in the ADK baseline
record.

### Model errors

A model call that fails reaches the caller as a fixed code, never as the gateway's or the
provider's text:

```json
{"error": "…", "code": "MODEL_RATE_LIMITED", "correlation_id": "<turn_id>",
 "trace_id": "<trace id>", "retry_after_s": 30}
```

| Code | Status | Cause | Retried by the agent |
|---|---|---|---|
| `MODEL_BUDGET_EXCEEDED` | 402 | The gateway says the key's budget is spent | No |
| `MODEL_RATE_LIMITED` | 429 | The gateway's rate limit | Only while the turn's waits stay within 10 seconds, and at most 3 tries of a call |
| `MODEL_ACCESS_DENIED` | 502 | The gateway answered 401 or 403 | No |
| `MODEL_REQUEST_REFUSED` | 502 | Another 4xx, except 408 and 409 | No |
| `MODEL_UNAVAILABLE` | 502 | A 5xx, 408 or 409 from the gateway | Up to 3 tries of a call for 500, 502, 503 and 409; a timeout (408, 504) is not retried |

`retry_after_s` and the `Retry-After` header carry the gateway's `Retry-After` for
`MODEL_RATE_LIMITED`, and are null and absent otherwise. `runtime/adk@1.3.0` writes the gateway's
text, redacted, to its own log under the `correlation_id`. The conformance check asks that the
text is in neither the log nor a span; see the ADK baseline record.

## State

- **History** is kept per customer and session by the agent, in its own database. A client
  sends only the new message.
- **Notes** (when `agent.yaml` has `memory.notes`) are kept per customer across sessions.
- The agent holds no state for a client between requests beyond these.

## Tools

Tools run inside the turn. The customer a tool acts for is the request's `customer_id`, passed
by the runtime, not an argument the model fills in. A tool that raises is reported to the model
as a failed call and recorded; it does not fail the turn. Each call is one entry in `tools` and
one audit row. An `mcp:` server that cannot be reached is named in `mcp_unavailable` and the
turn continues without it.

## Traces

When the deployment has a trace collector, each turn writes one root span named `agent_turn`
(`openinference.span.kind` `AGENT`) with `session.id`, `user.id`, `agent.turn_id`,
`agent.framework`, `agent.name` and, when the deployment sets it, `agentbreeder.deployment_id`;
the resource has `openinference.project.name`, the agent name. Model and tool calls are child
spans. Spans carry message text and go only to the collector the deployment names. See
[traces](traces.md).

## Profiles

A runtime manifest's `profile` says which additions the runtime serves on top of v1.

- **`v1`:** this page, as `runtime/adk@1.3.0` serves it. `runtime/adk@1.4.0` also answers
  `MODEL_BLOCKED` (422), which this page does not list and no check covers yet.
- **`kit`** (multi-framework spec §10.2; no runtime has it yet): `GET /health` is 503 until the
  agent is built and after a failed start, and 200 only when it is ready; on SIGTERM the turn in
  flight finishes or times out within the stop grace; a turn that reaches its model-call limit
  with the model still asking for tools fails with `MODEL_STEP_LIMIT`. A `kit` runtime still
  passes every v1 check.

## Reserved for a later version

Streaming (`Accept: text/event-stream` with typed events), bearer authentication (#780), and an
interrupted turn that can be resumed by the authenticated caller.

## Versioning

The protocol version is an integer. A change that a v1 client or `deploy` could not ignore
(a removed or retyped field, a changed status, a new required request field) is a new version;
adding an optional request field or a reply field is not. `build` refuses a runtime that declares
a protocol the engine does not know (`PROTOCOL_UNSUPPORTED`).
