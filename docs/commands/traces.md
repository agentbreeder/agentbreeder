# `agentbreeder traces`

Show recent agent traces per deployment from the stack's Phoenix (read-only).

`traces [ID | --trace TRACE_ID] --stack DIR [--environment ENV] [--last N] [--since D] [--project NAME] [--json]` lists the newest traces of one deployment (`ID`, from `list`), of every deployment that is not removed (no `ID`), or one trace as a span tree (`--trace`, the `trace_id` a turn returns). Each trace shows its start, latency, status, token counts, models and tool calls. Traces are read from the stack's `service/phoenix` on loopback with the stack's stored ingest key, which is never printed. That key is admin-equivalent in Phoenix; `traces` only reads with it. A trace is attributed to a deployment exactly when its root span carries `agentbreeder.deployment_id`; otherwise by the deployment's lifetime window, with any overlapping deployments of the same agent listed. Message content is never shown, and every string is redacted as in `logs`. Details, attribution and the `--json` shape: [traces.md](../traces.md) (on `main`, not in a published preview).

Exit 0 when Phoenix was read (also with no traces); 2 with `[CODE] message` on stderr (and with `--json` also `{"schema_version": 1, "error": {"code", "message"}}` on stdout) when nothing was read.

Flags: `agentbreeder traces --help`.
