# `agentbreeder invoke`

Send one message to a local deployment and print the reply.

`invoke NAME MESSAGE|- [--candidate] [--customer ID] [--session ID] [--json] [--state-dir DIR | --stack DIR [--environment ENV]]` sends one message to a ready local deployment and prints its reply: only `http://127.0.0.1:<port>` endpoints from local state (Kubernetes rows are refused; use `forward`), after Docker confirms the container is the recorded one and still publishes that port; no proxies, no redirects, one 180 s deadline for the whole exchange, state opened read-only. Controls in the reply are shown as visible escapes; `--json` prints the whole answer. `--candidate` calls `NAME-candidate`; `--customer`/`--session` are not authenticated (#780). A message given as an argument is visible to other local users in `ps`; pass `-` to read it from stdin.

Exit 0 with a reply; 1 when the message was (or may have been) sent but no usable reply came back (a retry is a second turn); 2 when nothing was sent. Errors are `[CODE] message` on stderr, and with `--json` also `{"schema_version": 1, "error": {"code", "message", "sent"}}` on stdout; printed follow-ups carry the same `--stack` or `--state-dir`. A state directory it cannot look into (no search permission, another OS error) is `STATE_UNREADABLE`, exit 2, not a traceback; a missing one is `NO_STATE` (on `main`, not in a published preview).

Flags: `agentbreeder invoke --help`.
