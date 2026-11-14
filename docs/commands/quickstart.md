# `agentbreeder quickstart`

Build, gate, deploy, invoke and remove the sample (needs Docker).

Implemented: `quickstart [--dry-run]` (builds the shipped `http-agent` sample from a copy, passes it through a demo static-adapter identity gate, deploys, invokes, redeploys and removes it; history stays in a printed temporary directory; `--dry-run` runs `doctor` and `validate` only).

`quickstart` now ends with `Next: … new agent hello --model gemini` (on `main`, not in a published preview).

Flags: `agentbreeder quickstart --help`.
