# #1025 O1 verification: Ollama through `service/litellm@1.2.0`

Status: **verified on macOS with Docker Desktop only** (2026-11-16, branch
`feat/1025-o1-ollama` on `main` 8b77de8b, uncommitted tree as later pushed; not in a published
preview). **Linux was not run** (spec §12.8): on Linux the gateway needs the host mapping this
slice adds and Ollama listening on the docker bridge (`OLLAMA_HOST=0.0.0.0`), and neither has
been run. Ollama on Docker Desktop is reached as `host.docker.internal`, which Docker Desktop
resolves in every container.

Machine: macOS 13.7.8, Intel Core i5-7500 (4 cores, no GPU), 48 GiB, Docker Desktop engine 27.5.1,
Ollama 0.12.3 on the host (`127.0.0.1:11434`), models `llama3.2:3b` (2.0 GB, already present)
and `nomic-embed-text` (274 MB, pulled for this run). LiteLLM 1.106.2 (the pinned
`litellm-database` digest, unchanged).

## How it ran

The CLI was this worktree's editable install, run under `env -i` with a fresh `HOME` and
`TMPDIR` in a scratch directory, `USER=demo`, and a Docker context created in that `HOME`.
Before the first stack the image, volume, network and container lists were saved; teardown
removed only what was missing from that snapshot (the pulled LiteLLM, pgvector and python images
and the agent builds), and the end state matched it (alpine and the two Presidio images, no
volumes, no containers).

| Step | Command | Exit | Time | Result |
|---|---|---|---|---|
| 1 | `new agent ollama-hd --model ollama` | 0 | <1 s | stack on `service/litellm@1.2.0`, both aliases `ollama_chat/llama3.2:3b` with `api_base: http://host.docker.internal:11434`, no key; the text said to `ollama pull llama3.2:3b` |
| 2 | `services up ollama-hd --environment dev --yes` (no `--env-file`) | 0 | 145 s | grant: `chat: ollama (no key → host port 11434)` (and `judge`), "litellm can reach the host …"; gateway healthy under the 1.2.0 launcher |
| 3 | `release` (first) | 5 | 218 s | 0/2: Ollama answered 400 `llama3.2:3b does not support thinking`: the starter's `reasoning: low` became `reasoning_effort`. **Fixed: the Ollama starter writes `reasoning: none`** (the agent then sends no `reasoning_effort`) |
| 4 | `release` (second, after the fix) | 5 | 228 s | 0/2: every chat call hit the runtime's 90 s model timeout. Ollama's log shows the calls finished after 1m47s–3m35s: the model loaded cold (Ollama unloads it after 5 minutes idle) and the parallel eval calls queued on one CPU slot, with the machine's load average between 15 and 33 from other work |
| 5 | one warm-up call to Ollama (2.5 s), then `release` | **0** | 119 s | **2/2 passed**, mean correctness 1.0, p50 latency 31 s. `hours`: "Acme's support hours are from 9:00 AM to 5:00 PM Pacific Standard Time, Monday through Friday." `unknown`: "I'm not aware of the current stock price as that information is not publicly available on our system." The judge's strict `json_schema` verdicts parsed on `llama3.2:3b` |
| 6 | `invoke ollama-hd "When is support open?" --json` | 0 | 43 s | 200, 1 model call, 98 in / 25 out: "Acme's support hours are 9:00 AM to 5:00 PM Pacific Time, Monday through Friday." `cost_usd` null |
| 7 | `invoke ollama-hd "What can you help me with?" --json` | 0 | 21 s | 200, 1 model call, 100 in / 33 out |
| 8 | `cost --by model`, `cost`, `cost --json` | 0 | | every row and the total show `no price (local model)`; `14 call(s) went to a local model (Ollama)`; JSON `total.local` 14, `cost_usd` 0.0, `unpriced` 0. LiteLLM's spend log rows had `custom_llm_provider: ollama_chat` and `spend: 0.0` |
| 9 | `embed` alias `ollama/nomic-embed-text` (`dimensions: 768`) added, `services up --update --yes`, one `/v1/embeddings` call | 0 | | grant line `embed: ollama (no key → host port 11434)`; 768-dimension vector, 10 prompt tokens. Whether `dimensions` other than 768 is honoured was not tried |
| 10 | teardown: `remove --local`, `services down --purge --yes`, `rmi` of new images | 0 | | Docker back to the snapshot |

Model choice: `llama3.2:3b` serves both `chat` and `judge`, so the starter needs no key at all. It
was not compared with other local models; on this CPU it answers in 20–40 s once loaded. **A
cold model plus the eval's parallel calls can exceed the runtime's 90 s model timeout on a slow
machine; the docs say to load the model first (any short call) before `release`.** No
timeout was changed.

## Gateway checks (spec §12.5, §12.6, §4.2)

- **Launcher environment (§4.3).** In the running gateway `/proc/1/environ` held exactly the
  allow-list (`HOME`, `LITELLM_LOG`, `LITELLM_TELEMETRY`, `DISABLE_ADMIN_UI`, `NO_DOCS`,
  `STORE_MODEL_IN_DB`, `AGENTBREEDER_SERVICE`, the image's `PATH`, `SSL_CERT_FILE`,
  `LITELLM_RELEASE_TAG` and four `PRISMA_*` names), the forced `AWS_EC2_METADATA_DISABLED=true`,
  `AWS_CONFIG_FILE` / `AWS_SHARED_CREDENTIALS_FILE=/dev/null`, `NO_GCE_CHECK=true`,
  `GCE_METADATA_HOST` / `_IP` / `_ROOT=metadata.invalid`, and the two secrets. The container's
  own `HOSTNAME` was gone. The gateway reached health and the admin UI stayed off. The image's
  `ENV` names came from `docker image inspect` of the pinned digest. In that image PyYAML is
  6.0.3, google-auth 2.52.0 reads `NO_GCE_CHECK` (value `"true"`), and botocore 1.43.6 reads
  `AWS_EC2_METADATA_DISABLED`.
- **Host mapping.** `extra_hosts: ["host.docker.internal:host-gateway"]` was on the gateway
  only (it makes no difference on Docker Desktop, F8).
- **Metadata services (§12.5),** `tests/test_ollama_gateway_docker.py`, passed twice: fake
  listeners at 169.254.169.254 and 169.254.170.2 on an internal network. With the container's
  raw environment (plus `AWS_CONTAINER_CREDENTIALS_FULL_URI`), boto3 found credentials and
  google-auth's `default()` succeeded, and both listeners were hit. With the launcher's
  environment boto3 found none, google-auth raised `DefaultCredentialsError`, and no listener was hit.
- **Client-side parameters (§4.2).** A virtual key limited to `chat` sent, one at a time in the
  body, `api_base`, `base_url`, `api_key`, `api_version`, `organization`, `extra_headers`,
  `custom_llm_provider`, `user_config`, `litellm_credential_name`, `model_id`,
  `aws_bedrock_runtime_endpoint`, `aws_sts_endpoint`, `vertex_project` and `vertex_location`,
  and the headers `x-litellm-api-base`, `x-litellm-base-url` and `api_base`, every endpoint
  value pointing at a listener on host port 18999. This is the part of the spec's §4.2 list
  that applies to Ollama, not the whole harness (the Vertex, Bedrock and Azure credential
  parameters and the readback endpoints belong to those slices). The listener got nothing (a control request
  from a container and one from the gateway both reached it). LiteLLM refused `api_base`,
  `base_url`, `user_config`, `aws_bedrock_runtime_endpoint` and `aws_sts_endpoint` (401); the
  rest were answered by Ollama as usual (200).
  **Finding: `custom_llm_provider` is honoured per request.** With `"openai"` LiteLLM
  re-routed the Ollama alias to OpenAI and failed (500) because this stack imports no OpenAI
  key; the error came from the client, before any request. On a stack that imports
  `OPENAI_API_KEY`, a caller could send an alias's prompt to OpenAI's default endpoint on the
  operator's key. This applies equally to the four key providers; it is not specific to Ollama.
  Recorded as a known gap for every provider (D14: ship with Ollama verified); the control is
  tracked in #1105.
- **Logs (§12.6).** `services logs` of the gateway (920 lines) and the container's raw log
  were scanned for the master key, the three Postgres role URLs and their passwords, and the
  probe's virtual key: 0 hits.

## Not run

- Linux (§12.8), `kind`, and any other Ollama model.
- `--from-spec` with knowledge for `ollama` (no embedding model is offered for it, so it is
  refused) or with tools (unverified).
- Northwind on Ollama.
