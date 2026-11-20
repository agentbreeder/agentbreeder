# `agentbreeder up`

Start the local console on 127.0.0.1 (single user, preview).

See [`command-center.md`](../command-center.md).

Sign-in flags (on `main`, not in a published preview; see "Sign-in" in `command-center.md`):

- `--set-password`: set the console's first password from the terminal, then exit. On a terminal
  it asks twice without echo; otherwise it reads exactly one line of stdin. Never pass a password
  as an argument.
- `--reset-password`: replace a forgotten password the same way, then exit. Every open console
  session ends, also in a console that is running.
- `--link`: print a one-time sign-in link at start even when a password exists (Enter in the
  terminal running `up` makes another).

Flags: `agentbreeder up --help`.
