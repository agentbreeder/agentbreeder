# More LiteLLM providers: Ollama, Vertex AI, Bedrock, Azure OpenAI (#1025)

Status: spec r4 (2026-11-15), for owner decisions D1–D15 (§13). Nothing here is implemented.
Parent: #1017 (model layer epic), phase 3. Related: #1031 (unknown providers pass the review,
slice P0 here), #1023 (direct provider endpoints, PR #1029), #1024 (guardrails, PR #1029).
Everything in this spec stays on the gateway route.

## 1. Problem

A stack's model gateway is `service/litellm@1.0.0` (LiteLLM 1.106.2, digest-pinned). Its config
comes from the repo, so `gateway_keys.config_review` reviews it before any key reaches it (#739):
allow-listed keys per section, plain scalar values, and `os.environ/` only for the master key, the
database URL and each model's own provider key (`PROVIDER_KEYS`: Gemini, OpenRouter, OpenAI,
Anthropic). The grant shows each alias with its provider and key name, and the config's sha256 is
part of the grant digest.

That review makes four common providers impossible to configure:

| Provider | What LiteLLM needs | Why `config_review` refuses it today |
|---|---|---|
| Ollama (local) | `api_base` (a host URL); no key | `api_base` is not in `_PARAMS` |
| Vertex AI | `vertex_project`, `vertex_location`, a service-account JSON (`vertex_credentials`) | none of the three keys is allowed; no key name for the JSON |
| Bedrock | `aws_access_key_id`, `aws_secret_access_key`, `aws_region_name` | none is allowed; `AWS_*` imports are refused by `env_import.DENYLIST` |
| Azure OpenAI | `api_base` (`https://<resource>.openai.azure.com`), `api_key`, `api_version` | `api_base`/`api_version` not allowed; `AZURE_*` imports are refused |

### 1.1 Facts from `main` (67f9634) that shape the design

Each fact below was read in the code; F1's first sentence was also run. LiteLLM, boto3 and
google-auth behaviour is marked *(expected, to record in P0)* wherever it has not been run.

- **F1 (#1031).** An entry with an unknown provider and no key passes the review today, labelled
  `unknown (no key)` (run: `vertex_ai/…` and `bedrock/…` entries with no credentials both pass). So
  does a model string with no `/` (`provider = None`, gateway_keys.py ~392), which LiteLLM routes
  by name. *(Expected, to record in P0:)* LiteLLM then falls back to Google Application Default
  Credentials or the boto3 default chain, which on a cloud VM end at the instance metadata
  server, i.e. the VM's own identity. A laptop, kind or Docker Desktop has no metadata server.
- **F2.** `env_import.DENYLIST` refuses `AWS_*`, `AZURE_*`, `GOOGLE_APPLICATION_CREDENTIALS`,
  `GCLOUD_*` and `CLOUDSDK_*` in `lookup()`, which `services up` (`engine._secret`) and
  `secret set --from-env` use; `mcp.py` calls `denied()` for MCP server imports. A repo can never
  import a cloud credential from the operator's environment.
- **F3.** The `service/litellm` launcher (`files/start.py`) starts from `dict(os.environ)` and
  adds a fixed allow-list of secret files. Each file must be one line, at most 16 KiB, no NUL. A
  service-account JSON fits only when minified to one line.
- **F4.** An optional import that is unset is absent in the container; `_secret` returns an
  earlier stored value when the environment no longer has one. *(Expected:)* an `os.environ/X`
  reference to an unset X reaches LiteLLM as "not set", which for Vertex and Bedrock means the
  default credential chain (F1), not an error.
- **F5.** `config_review(path)` has four callers: `services/plan.py` (grant), `services/lock.py`
  via `config_aliases`, `newagent.py` and `guide_checks.py`. None passes the gateway's version.
  `guide_checks._gateway` accepts any `service/litellm@*` ref without resolving it.
- **F6.** The grant line per alias is `"<provider> (<KEY NAME>)"`; changing it for an existing
  provider changes every existing grant digest. `guide_checks._keys` parses the key name out of
  that label with a regex, and `newagent.Provider.key` / `stackfor.py` assume one key per provider.
- **F7.** Knowledge chunks are hashed as `sha256("gateway:<alias>:<dimensions>\n<title>\n<text>")`
  (`knowledge.chunk_hash`), with no provider or model in it.
- **F8.** Service containers get no `host.docker.internal` mapping on Linux; only agent containers
  get `--add-host host.docker.internal:host-gateway` (`runtime.py` ~490). **Docker Desktop on
  macOS resolves the name in every container**, so on macOS the gateway can already reach host
  ports today.
- **F9.** The gateway service has `egress: true`: no network restriction on where LiteLLM
  connects. The config review is the only control on endpoints.
- **F10.** Built-in origin is known only after lock resolution (`lock.py` `origins`, compared with
  `BUILTIN_LABEL`); `services/schema.py` has no origin. `plan.py` already uses `host_ports` for
  published ports in the grant. MCP relays are always recorded as `BUILTIN_LABEL` (`lock.py`
  ~227) although their secrets come from the project's MCP version. A stack entry for a service
  sets only `with` (params) and `wire` (`StackService`): it cannot change a built-in service's
  secrets, env, command or ports.
- **F11.** `config_review` accepts any scalar `model_info` key and `litellm_settings.set_verbose`
  / `json_logs`. No shipped config (starters, Northwind) uses `model_info` or `set_verbose`.
- **F12.** `newagent.PROVIDERS` carries a `verified` flag (only `gemini` is true); docs say
  "config-only" for the others. A provider becomes `verified` only after §12's recorded run.

## 2. Goal and scope

Goal: an operator can serve a model alias from Ollama, Vertex AI, Bedrock or Azure OpenAI through
the stack's gateway, with the guarantee the key-only providers are meant to have: the repo's
config cannot send a credential, or a prompt, to an endpoint the grant did not show, and cannot
make the gateway use an identity the operator did not import.

In scope: model-string and endpoint review per provider, credential shapes and import names, new
`service/litellm` versions, `config_review` and grant changes, embeddings per provider, docs and
`new agent --model` choices, and one real run per provider before it is called `verified`.

Out of scope: direct (gateway-less) routes to these providers (§9.1); workload identity / keyless
federation (§3.6); Azure AI Foundry, Vertex Model Garden partner models, Bedrock agents, imported
models and knowledge bases; an in-stack Ollama service (D5); caching; a network egress block for
the gateway container (residual R3, follow-up issue); the knowledge embedding-route check (§7.2,
its own issue).

## 3. Providers and credential shapes

Parameter names and behaviours below are LiteLLM 1.106.2's as documented; each provider's slice
records them against the pinned image before it ships.

### 3.1 Ollama (local)

- **Model string:** `ollama_chat/<model>` for chat, `ollama/<model>` for embeddings (§5.1 pattern).
- **Credential:** none. Ollama on the operator's host listens on 11434 without auth.
- **Endpoint:** `api_base` must equal exactly `http://host.docker.internal:11434` (D4).
- **Reach:** on macOS the name already resolves (F8). On Linux the gateway needs
  `host.docker.internal:host-gateway` (§6.2), and Ollama must listen on the docker bridge address,
  which in practice means `OLLAMA_HOST=0.0.0.0` and exposes Ollama to the LAN unless the host
  firewall blocks 11434. The docs say this; Linux is not claimed until §12.8 runs.
- **Model availability:** the operator pulls the model (`ollama pull …`); `services up` does not.
- **Tool calls:** depend on the model; the starter's default model is picked by O1's recorded run.

### 3.2 Vertex AI

- **Model string:** `vertex_ai/<model>` from an allow-listed family (§5.1: `gemini-`,
  `gemini-embedding-`, `text-embedding-`, `text-multilingual-embedding-`), so no
  `vertex_ai/openai/…` routes, numeric endpoint ids or partner-publisher (`…@…`) models.
- **Credential:** a service-account key JSON, minified to one line, imported as
  `VERTEX_SERVICE_ACCOUNT_JSON` (§3.5). The launcher (§6.3) parses it once, refuses anything but
  `"type": "service_account"`, refuses `authorized_user` (a gcloud user's refresh token carries the
  user's whole scope) and `external_account` (its `credential_source` names a URL or file the
  token exchange reads; D6), requires `token_uri == https://oauth2.googleapis.com/token` and
  `universe_domain` absent or `googleapis.com`, and **re-serialises a canonical object** with only
  `type`, `project_id`, `private_key_id`, `private_key`, `client_email`, `client_id`, `token_uri`.
  LiteLLM receives that canonical string, so the launcher and LiteLLM cannot disagree on
  duplicate keys or extra fields.
- **Config:** `vertex_credentials: os.environ/VERTEX_SERVICE_ACCOUNT_JSON` exactly (never a path),
  `vertex_project` (`^[a-z][a-z0-9-]{4,28}[a-z0-9]\Z`), `vertex_location` (`global` or
  `^[a-z]+-[a-z]+[0-9]{1,2}\Z`), all **required** on every `vertex_ai/` entry.
- **Project binding:** `services up` (on the final value `_secret` returns, stored or new) and the
  launcher (on the file it reads) both refuse (`VERTEX_PROJECT_MISMATCH`) when `vertex_project`
  differs from the key's `project_id`, so a repo cannot point the operator's service
  account at another project it can reach. The grant shows the project and location.
- **Endpoint:** derived by LiteLLM from the location; `api_base` and every `vertex_ai_*` endpoint
  key are refused.
- **Docs:** a dedicated service account with `roles/aiplatform.user` only.

### 3.3 Bedrock

- **Model string:** `bedrock/<model id>` or an inference-profile id, by §5.1's pattern: no
  further `/` (so no `bedrock/converse/`, `bedrock/invoke/`, `bedrock/imported/` routes) and no
  `arn:` (an ARN overrides region and account) (D7).
- **Credential:** an IAM access key pair, imported as `BEDROCK_ACCESS_KEY_ID` and
  `BEDROCK_SECRET_ACCESS_KEY`. Refused: `aws_session_token`, `aws_role_name`, `aws_session_name`,
  `aws_web_identity_token`, `aws_sts_endpoint`, `aws_profile_name`, `aws_bedrock_runtime_endpoint`.
- **Bedrock API keys** (a bearer token scoped to Bedrock) are narrower. In LiteLLM they are read
  from the `AWS_BEARER_TOKEN_BEDROCK` environment variable, not a `litellm_params` key. Slice B0
  records whether 1.106.2 supports it; under D8 the launcher would map an import
  `BEDROCK_API_KEY` to that variable, and the config would carry no credential param, so the
  "explicit credentials" rule needs a config marker for that form (B0 must define one, e.g. a
  required `api_key: os.environ/BEDROCK_API_KEY` if LiteLLM accepts it as a param, before
  B1; otherwise the IAM pair stays).
- **Config:** `aws_access_key_id: os.environ/BEDROCK_ACCESS_KEY_ID`, `aws_secret_access_key:
  os.environ/BEDROCK_SECRET_ACCESS_KEY` and `aws_region_name` (`^[a-z]{2}(-gov)?-[a-z]+-[0-9]\Z`)
  are **required** on every `bedrock/` entry.
- **Docs:** an IAM user allowed only `bedrock:InvokeModel` and
  `bedrock:InvokeModelWithResponseStream` on the chosen model ARNs.

### 3.4 Azure OpenAI

- **Model string:** `azure/<deployment>` (`^azure/[A-Za-z0-9._-]{1,64}\Z`). `azure_text/`,
  `azure_ai/` and every other prefix are separate providers and are refused.
- **Credential and endpoint from the operator.** The repo must not choose the host that receives
  the key: an `*.openai.azure.com` host can be an attacker's own Azure resource, and the key goes
  in its `api-key` header. So both come from the operator: `AZURE_OPENAI_API_KEY` (secret) and
  `AZURE_OPENAI_ENDPOINT` (not secret), both gateway-only imports (§3.5). The config must say
  `api_key: os.environ/AZURE_OPENAI_API_KEY` and `api_base: os.environ/AZURE_OPENAI_ENDPOINT`
  exactly. `services up` and the launcher both check the endpoint against
  `^https://(?!xn--)[a-z0-9](?:[a-z0-9]|-(?!-)){1,62}[a-z0-9]\.openai\.azure\.com/?\Z` and the
  grant prints the resource name taken only from the validated match. Validation runs on the
  final value (`_secret` may return a stored one) and before anything is printed; an invalid
  endpoint is never echoed in an error, the grant or logs. `up` confirms the grant before it resolves secrets
  (`engine.up`), so the endpoint joins the grant itself: when the config has an Azure entry,
  `plan` resolves `AZURE_OPENAI_ENDPOINT` the way `_secret` would (environment, env file, else the
  stored value), validates it, and puts its sha256 and resource name in the grant. A changed
  endpoint changes the grant digest, so the existing confirm flow asks again; there is no second
  consent step. `plan`/`status` with no value available show `endpoint not resolved`, and `up`
  refuses until it is. `_secret` then re-checks that the value it returns hashes to the confirmed
  one (`AZURE_ENDPOINT_CHANGED` if it does not). The lookup runs in `engine` and is passed to
  `plan.grant()`, which stays pure (no state or env access). The grant gains the Azure field only
  when the config has an Azure entry, so other stacks keep their digest (golden test). The stored
  value counts as available, so a confirmed stack does not look unconfirmed without an env file.
- **Config:** `api_version` (`^[0-9]{4}-[0-9]{2}-[0-9]{2}(-preview)?\Z`) and
  `model_info.base_model` (`^azure/[A-Za-z0-9._-]{1,64}\Z`) are **required**; without
  `base_model` LiteLLM cannot price a deployment name. The repo chooses `base_model`, so it could
  name a cheaper model and under-price the deployment against budgets: the grant line prints it
  per alias, and the docs say budgets on Azure aliases trust that line.
- Entra ID auth (`azure_ad_token`, `tenant_id`, `client_id`, `client_secret`, `azure_username`,
  `azure_password`) is refused (D9).

### 3.5 Import names and who may import them

New names must not reopen F2: `BEDROCK_SECRET_ACCESS_KEY` is an AWS credential whatever it is
called. The design (D3):

- `env_import.GATEWAY_ONLY` = {`VERTEX_SERVICE_ACCOUNT_JSON`, `BEDROCK_ACCESS_KEY_ID`,
  `BEDROCK_SECRET_ACCESS_KEY`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`} (+ `BEDROCK_API_KEY`
  if D8). **`denied()` refuses every `GATEWAY_ONLY` name** (in addition to `DENYLIST`), so every
  existing caller, `mcp.py`'s MCP-version validator and `secret set --from-env` included, refuses
  them with no further change. `lookup(name, env_file, allow_gateway_only=True)` is an exact-name pass for the
  `GATEWAY_ONLY` names only: for those five (six) names it bypasses both the `GATEWAY_ONLY` rule
  and `DENYLIST` (whose `AZURE_*` pattern would otherwise refuse `AZURE_OPENAI_*`); every other
  name goes through `denied()` unchanged, so `AZURE_FOO` and `AWS_SECRET_ACCESS_KEY` stay refused
  with the flag set. A table test covers each `GATEWAY_ONLY` name with and without the flag, and
  those two. Implementation greps every `denied()`/`lookup()` caller and adds a test for each.
- One shared resolver helper in `engine` is the only code that passes `allow_gateway_only=True`;
  `_secret` uses it, and the plan-time Azure lookup (§3.4) uses it for `AZURE_OPENAI_ENDPOINT`
  only, never reading or printing a secret-shaped `GATEWAY_ONLY` value. It passes the flag only when the service's locked
  origin is `BUILTIN_LABEL` (the packaged registry), it is not an MCP relay, and its locked spec
  `provides: gateway/litellm`. Origin is never inferred from `provides` or the name. Relays are
  recorded as built-in (F10), so the relay exclusion is explicit and tested.
- `services/lock.py` also refuses (`IMPORT_GATEWAY_ONLY`) any service other than the built-in
  gateway that declares a `GATEWAY_ONLY` import, checking the MCP version's imports as well as the
  relay spec's, next to where `origins` is computed. A project registry entry named
  `service/litellm` is project origin, so it is refused too. A stack cannot override the built-in
  gateway's secrets or env (F10), so the allowance cannot move a credential into another variable.
- Tests: project service, project-shadowed `service/litellm`, MCP version (at load), relay,
  built-in non-gateway services (phoenix, postgres), `secret set --from-env` each refused;
  a `plan` for a project-shadowed `service/litellm` does not resolve the Azure endpoint;
  built-in litellm accepted; an agent container and another service cannot read the
  gateway's secret files.

- **Rotation:** `_secret` keeps a stored value when the variable is later unset, so a revoked key
  stays in the gateway until replaced. The docs say to re-import the new value; `services down --volumes`
  already removes stack state. A narrower clear command is a follow-up issue (R7), not in a slice.

### 3.6 Keyless identity (deferred)

Workload identity is the right production answer for Vertex and Bedrock, but it needs a cloud
VM or cluster, and its chains read the metadata services that §4.3 blocks. It belongs with the
production Kubernetes work (#770). Until then long-lived keys with narrow IAM are the only shape,
and the docs say so.

## 4. Endpoint review (SSRF)

Threats: a credential sent to a host that is not its provider; a prompt sent to a host the grant
did not show; the gateway used as a proxy into the host, the stack network or a metadata service
(its responses reach the agent as model output); and an agent, through its virtual key, steering
any of these per request.

### 4.1 Rules for every provider

- Endpoint-shaped keys (`api_base`, `base_url`, `api_host`, `aws_bedrock_runtime_endpoint`,
  `aws_sts_endpoint`, `vertex_ai_*`, `extra_headers`, …) are refused, except §3.1's exact string
  and §3.4's operator-supplied `os.environ/AZURE_OPENAI_ENDPOINT`. `_PARAMS` stays an allow-list,
  now per provider (§5.1), so an unknown key is refused by default.
- **Literal checks only.** The review never resolves DNS. Every pattern is applied with
  `re.fullmatch` and ends in `\Z` (a `$` would accept a trailing newline). IP literals,
  `localhost`, userinfo, other ports, punycode (`xn--`) or non-ASCII labels, `--`, a trailing dot,
  query, fragment and paths other than empty or `/` are refused.
- **Every printed value has a pattern.** For the printed fields (project, location, region, API
  version, endpoint, `base_model`, `keep_alive`) the pattern applies to `str` values only and any
  other scalar is refused, except `keep_alive`, which may be the int `-1`. The common numeric and
  boolean `_PARAMS` (`timeout`, `rpm`, `temperature`, `drop_params`, …) keep today's scalar rule.
  Project, location, region, API version, resource name, `base_model` and `keep_alive` each have
  their own `fullmatch` pattern, so no control character
  or newline reaches the grant; a table test covers ESC, CR, LF and non-ASCII per field.
- **DNS rebinding.** Review-time resolution would prove nothing (LiteLLM resolves per request).
  Accepted hosts are in provider-owned zones or Docker's fixed `host.docker.internal`.
- **Redirects.** *(Expected:)* botocore does not follow; google-auth (`requests`) does but its
  `token_uri` is pinned; the OpenAI SDK used for Azure follows by default, and httpx keeps custom
  headers such as `api-key` across a cross-origin redirect. P0 records this for each provider
  against a local fake endpoint. LiteLLM exposes no switch to turn redirects off, so a provider
  whose client follows a redirect carrying the credential is blocked, unless the owner accepts
  that residual explicitly for that provider (D15) (Azure's operator-supplied endpoint and Ollama's
  fixed host narrow it, not close it).

### 4.2 Client-side parameters (per request)

Agents hold virtual keys and send request bodies and headers to the gateway. LiteLLM has guards
for client-supplied `api_base`/`api_key` and settings (`allow_client_side_credentials`,
`configurable_clientside_auth_params`) that loosen them; whether the guard covers the new
providers' parameters is **not known**. So:

- Those two settings stay outside the allow-list (they are refused today) and a test says so.
- Slice P0 runs, against the pinned image, a virtual key sending each of `api_base`, `base_url`,
  `aws_bedrock_runtime_endpoint`, `aws_sts_endpoint`, `aws_region_name`, `aws_role_name`,
  `aws_web_identity_token`, `vertex_credentials`, `vertex_project`, `vertex_location`,
  `vertex_ai_project`, `vertex_ai_location`, `vertex_ai_credentials`, `azure_ad_token`,
  `azure_ad_token_provider`, `api_version`, `api_key`, `organization`, `aws_session_name`,
  `litellm_credential_name`, `model_id`, `extra_headers`, `custom_llm_provider` and `user_config`,
  in the body and as `x-litellm-*` headers, for an alias of each provider. Pass: the call is refused or
  the parameter ignored, and a fake listener receives nothing.
- **Credential readback.** The same harness calls `/model/info`, `/v1/model/info`, `/models`,
  `/model/list`, `/v2/model/info`, `/model_group/info`, `/model/metrics` (those that exist on
  1.106.2) and `/get/config/callbacks`, plus any other endpoint P0 finds that returns deployment
  params, with an agent's virtual key for an alias of each
  provider. Pass: no credential value appears and no `vertex_credentials` or `aws_*` field is
  returned unmasked. LiteLLM masks `api_key`-like fields; whether it masks the new ones is not
  known.
- If a parameter gets through, or a credential is read back, that provider does not ship until a fix exists (a LiteLLM setting,
  or a request filter in front of the gateway, which would be its own spec). D14.
- **As built (#1105):** LiteLLM 1.106.2 has no setting for `custom_llm_provider`, so
  `service/litellm@1.3.0` loads a request filter as LiteLLM's pre-call callback. It refuses the
  parameters above and the other routing fields (`GATEWAY_PARAM_REFUSED`) and every call type
  except chat completions and embeddings (`GATEWAY_ROUTE_REFUSED`). LiteLLM's file and batch
  GET/DELETE routes pick a provider from a query parameter or header and never reach a pre-call
  hook, so every deployment and eval key is also minted with `allowed_routes` (chat and
  embeddings), not only under a guardrails floor. The pinned-image check is
  `tests/test_gateway_request_filter_docker.py`, with 1.2.0 as its control.

### 4.3 Metadata services and ambient credentials

Every Vertex, Bedrock and Azure entry names its credentials explicitly; unknown providers and
bare model names are refused (P0). As further layers, `start.py` in each new version:

1. Builds LiteLLM's environment from an **allow-list** instead of copying `os.environ`: the env
   names declared in that version's `service.yaml` (`DISABLE_ADMIN_UI`, `NO_DOCS`,
   `STORE_MODEL_IN_DB`, …) plus `AGENTBREEDER_SERVICE`, listed statically in the launcher and
   checked equal to the `service.yaml` by a test, the image's own
   `ENV` names enumerated from `docker inspect` of the pinned digest in O1 and listed by name in
   the launcher (no `PYTHON*` wildcard), and the mapped secrets. An AC checks that `up` still
   reaches health and the admin UI stays off. That drops ambient `AWS_*` (container credentials URIs, web identity, `AWS_ENDPOINT_URL*`,
   `AWS_PROFILE`, `AWS_BEARER_TOKEN_BEDROCK`), `GOOGLE_*`/`CLOUDSDK_*`, Azure identity endpoints
   (`IDENTITY_ENDPOINT`, `IMDS_ENDPOINT`, `MSI_ENDPOINT`, `AZURE_FEDERATED_TOKEN_FILE`) and
   `HTTP(S)_PROXY`/`NO_PROXY`.
2. Sets `AWS_EC2_METADATA_DISABLED=true`, `AWS_CONFIG_FILE=/dev/null`,
   `AWS_SHARED_CREDENTIALS_FILE=/dev/null`, `NO_GCE_CHECK` (P0 records which casing the pinned
   google-auth reads), `GCE_METADATA_HOST=metadata.invalid`, `GCE_METADATA_IP=metadata.invalid` (it is
   formatted into a URL host, so a name that never resolves sends no packet) and
   `GCE_METADATA_ROOT=metadata.invalid`.
3. Reads the reviewed config itself (`/run/params/config`, with PyYAML, which LiteLLM depends on;
   O1 confirms it in the image), scans every string recursively for `os.environ/`, fails closed
   on any parse error, and exits before `exec` if a referenced `GATEWAY_ONLY` X is unset. The
   review (`strict_yaml`) has already refused duplicate keys and anchors, so the two parsers see
   the same document. So a secret
   file removed after `up` cannot fall through to a default chain.

P0 runs before any new version exists, so its harness sets these variables itself on a 1.0.0
gateway; each provider slice re-runs it against its own version. It checks each variable with a
fake listener on 169.254.169.254 and 169.254.170.2 in a test
network. The real control would be a network block on 169.254.0.0/16 and `fd00:ec2::254` from the
gateway container; it is residual R3 (a follow-up issue). In the local preview these variables
only matter on a cloud VM or inside ECS/EKS, which the docs say.

### 4.4 Credentials present at `up`

`services up` refuses with `MODEL_CREDENTIAL_MISSING` when the reviewed config's `required_env`
(§5.3) names a `GATEWAY_ONLY` variable that has no value, resolved now or stored from an earlier
run (`_secret`'s result), naming the alias and the variable, never the value. Key-only providers
keep today's behaviour.

## 5. `config_review` changes

### 5.1 Per-provider table

`PROVIDER_KEYS` becomes a table of providers. The provider is chosen by an **exact,
case-sensitive prefix match on `litellm_params.model`**, then the whole string must `fullmatch`
the provider's model pattern. A model with no `/`, or an unlisted prefix, is refused (#1031).

| Provider | Model pattern (`fullmatch`) | Credential params → import | Required plain params | Extra allowed |
|---|---|---|---|---|
| gemini, openrouter, openai, anthropic | prefix + `[!-~]{1,256}` (printable ASCII) | `api_key` → as today | — | — |
| ollama | `ollama(_chat)?/[A-Za-z0-9._:-]{1,128}` | none | `api_base` (exact §3.1) | `keep_alive` (`^[0-9]{1,4}[smh]\Z` or `-1`) |
| vertex | `vertex_ai/(gemini-\|gemini-embedding-\|text-embedding-\|text-multilingual-embedding-)[a-z0-9.-]{1,96}` | `vertex_credentials` → `VERTEX_SERVICE_ACCOUNT_JSON` | `vertex_project`, `vertex_location` | — |
| bedrock | `bedrock/(?!arn:)[a-z0-9][a-z0-9._:-]{0,127}` | `aws_access_key_id` → `BEDROCK_ACCESS_KEY_ID`, `aws_secret_access_key` → `BEDROCK_SECRET_ACCESS_KEY` | `aws_region_name` | — |
| azure | `azure/[A-Za-z0-9._-]{1,64}` | `api_key` → `AZURE_OPENAI_API_KEY`, `api_base` → `AZURE_OPENAI_ENDPOINT` | `api_version`, `model_info.base_model` | — |

The common `_PARAMS` (timeout, rpm, tpm, dimensions, …) stay allowed for every provider. A
credential param must be exactly `os.environ/<its import>`. For the four existing providers the
model pattern only refuses non-printable and non-ASCII characters; the golden test (§12.1)
confirms every shipped config still passes.

`model_info` gets an allow-list: `mode` (`chat|embedding`) for every provider, and `base_model`
for Azure only (refused elsewhere). Price keys (`input_cost_per_token`, …), `id`, `access_groups` and every other
key are refused: a repo must not set the price that budgets are enforced against. No shipped
config uses `model_info` (F11), but a user's config with price keys stops passing: the slice's
changelog calls this out as a change, and the golden test includes a project fixture with
`model_info` to pin the new error.

`litellm_settings.set_verbose: true` is refused when any entry uses a `GATEWAY_ONLY` credential
(verbose mode can print provider params; D13). `services logs` redaction gains JWT, bearer and
PEM-body shapes, since derived secrets (access tokens, signed assertions) are not exact imported
values.

### 5.2 Which gateway runs the config

`config_review(path, caps: GatewayCaps)`, with `caps` **required** (no default, so an omitted
argument fails closed). `GatewayCaps(imports: frozenset[str], reaches_host: frozenset[int])` is
derived from the resolved `ServiceSpec` of the gateway. A provider whose credential imports are
not all in `caps.imports`, or an Ollama entry without 11434 in `caps.reaches_host`, is refused
(`GATEWAY_PROVIDER_UNSUPPORTED`, naming the provider and the gateway ref). Call sites:

- `services/plan.py` and `services/lock.py` (`config_aliases`): from `self.locked.specs` / `specs`.
- `newagent.py`: from the built-in version it writes into the stack.
- `guide_checks.py`: resolves the ref against the built-in registry; for a non-built-in or
  unresolvable ref it reports `not checked` instead of guessing.

### 5.3 Review result and grant lines

`ConfigReview` gains `required_env: dict[alias, tuple[str, ...]]` (the variables each alias
needs) and `routes: dict[alias, dict]` (provider, project/location/region, and for Azure "from
`AZURE_OPENAI_ENDPOINT`"). `guide_checks._keys`, `MODEL_CREDENTIAL_MISSING` and `stackfor.py` use
`required_env` instead of parsing labels; `newagent.Provider.key` becomes `keys: tuple[str, ...]`.

The printed labels for the four existing providers stay byte-identical, and the grant adds new
keys only when present (as `grant["mcp"]` does), so a stack that uses no new provider and stays on
litellm 1.0.0 keeps its grant digest (F6). New labels:

- `ollama (no key → host port 11434)`
- `vertex (VERTEX_SERVICE_ACCOUNT_JSON, project <p>, <location>)`
- `bedrock (BEDROCK_ACCESS_KEY_ID + BEDROCK_SECRET_ACCESS_KEY, <region>)`
- `azure (AZURE_OPENAI_API_KEY → <resource>.openai.azure.com from AZURE_OPENAI_ENDPOINT,
  <api_version>, priced as <base_model>)`, with the resource resolved at plan time (§3.4).

The grant digest already covers the gateway's ref and image; it now also covers `routes`.

## 6. New `service/litellm` versions

Built-in versions are append-only once merged (`1.0.0/**` is never touched). **Each provider
slice cuts its own minor** (D12): 1.1.0 for O1 (Ollama), then 1.2.0 Vertex, 1.3.0 Bedrock,
1.4.0 Azure, each **cumulative** (it carries everything the earlier versions need, e.g. 1.2.0 has
`reaches_host: [11434]` and the Vertex imports) and adding only the provider its slice verified.
So no version ships an import or launcher check that has not run, and `GatewayCaps` is read per
version. The launcher changes of §4.3 and §6.3 first ship in 1.1.0 (slice O1), not in P1. Same image digest unless a slice shows the pinned LiteLLM lacks a
behaviour (then a new digest goes through the two-week rule and the image review).

**As built.** The minors moved, because 1.1.0 went to #1024 G1b, 1.3.0 to #1105 and 1.4.0 to #1089
(the Anthropic Messages route): Ollama is 1.2.0 (O1) and Vertex is 1.5.0 (V1), which carries
1.4.0's request filter. 1.5.0 ships config-only: no Vertex credential was
available, so §12.7 has not run for Vertex and its starter stays `verified: false`; the §12.5
checks for Vertex (`tests/test_vertex_gateway_docker.py`) ran on Docker Desktop (macOS,
2026-11-19, as 1.4.0): 3 passed, after one expectation was corrected (LiteLLM's own credential
check refuses `vertex_credentials` and `vertex_ai_credentials` with a 401 before the filter), and
again on 1.5.0 (2026-11-20): 3 passed, with #1089's Messages-route Docker test also passing on
1.5.0. Bedrock and Azure take the next minors.

### 6.1 `service.yaml`

- `secrets:` adds that version's gateway-only imports, each `{import: <NAME>, optional: true}`.
- `env:` adds the §4.3 variables.
- 1.1.0 adds `reaches_host: [11434]` (§6.2).

### 6.2 Reaching host Ollama

A new optional `service.yaml` field `reaches_host: [int]` (built-in services only, at most one
port, checked in `lock.py` by origin) makes `compose_service` add
`extra_hosts: ["host.docker.internal:host-gateway"]` to that service, **only when** the reviewed
config has an Ollama entry: `compose_service` takes the config review as an input, and the compose
label already hashes the service's compose entry, so the added line changes it.

What this does and does not limit:
- On Linux, a stack without Ollama gets no host mapping. With it, the gateway can open **any**
  host port bound to the bridge or 0.0.0.0; Docker cannot restrict the mapping to 11434.
- On macOS Docker Desktop the name resolves in every container already (F8): the field changes
  nothing there, and the gateway can reach host ports in every stack, today included.
- So the exact `api_base` string is the control on both, and P0 tests that no accepted config,
  and no client-side parameter (§4.2), yields a request to a host port other than 11434. The grant
  states "litellm can reach the host" (residual R2).
- Kubernetes: `kubernetes.py`/`cluster.py` hold no service or gateway code (stack services are
  Compose-only), so nothing changes there. How kind agents reach an Ollama-backed gateway is not
  verified and not claimed.

### 6.3 Launcher (`files/start.py`)

- `NAMES` adds that version's secrets; the environment is built from the §4.3 allow-list.
- Vertex: parse once, check, re-serialise canonically (§3.2); a bad value exits with `secret
  vertex-service-account-json is not a service-account key` (never the value).
- Azure: check `AZURE_OPENAI_ENDPOINT` against §3.4's pattern before `exec`. Vertex: compare
  `project_id` with every `vertex_project` in the config. The endpoint pattern and the Vertex
  rules are one shared constant in `gateway_keys.py`; a test checks the launcher's copy is
  identical.
- Required-variable check from the config (§4.3.3); its "no provider key imported" warning counts
  an Ollama entry as a provider, from the same parse.
- The one-line rule stays; docs show how to minify a key file (`jq -c`).

## 7. Embeddings

### 7.1 Per provider (to verify in each slice; nothing below has run)

| Provider | Candidate model (LiteLLM string) | Native dimensions | 768 (Northwind's column)? |
|---|---|---|---|
| Ollama | `ollama/nomic-embed-text` | 768 | natively; whether `dimensions` is accepted is recorded in O1 |
| Vertex | `vertex_ai/text-embedding-005` | 768 | yes |
| Vertex | `vertex_ai/gemini-embedding-001` | 3072 | via `dimensions`, to verify |
| Bedrock | `bedrock/amazon.titan-embed-text-v2:0` | 1024 | no: 256, 512 or 1024 only |
| Bedrock | `bedrock/cohere.embed-english-v3` | 1024 | no |
| Azure | `azure/<text-embedding-3-small deployment>` | 1536 | via `dimensions`, to verify |

A knowledge version declares its own `embedding.dimensions`, so a Bedrock embedding alias works
with a 1024 (or 512) knowledge version, not with Northwind's 768 table.

### 7.2 Re-index on a provider change (F7, separate issue)

Moving an embedding alias to another model with the same dimensions leaves every chunk hash
valid, so `knowledge status` reports a current index built by another model. This exists today
(Gemini → OpenAI at 768) and does not gate this spec or any `verified` flag. Proposed for its own
issue (D10): record the embedding route per index (a `kb_meta` row from a new runtime version) and
report `STALE_EMBEDDING_ROUTE`, rather than change `chunk_hash`, which would re-embed every index.

## 8. Cost, logs and tracing

- **Cost:** LiteLLM prices Vertex and Bedrock by model id, Azure by `model_info.base_model`
  (required). Ollama has no price: `agentbreeder cost` labels those aliases
  `no price (local model)` instead of `$0.00` (D11). A zero-cost alias never reaches a budget;
  the docs say so.
- **Logs:** `turn_off_message_logging` is provider-independent; §5.1 adds the `set_verbose` rule
  and redaction shapes. Provider errors can include project, region or resource names (not
  secrets).
- **Tracing:** the trace proxy sits in front of the gateway; no change.

## 9. Interaction with #1023 and #1024

### 9.1 Direct route (#1023)

These four providers are **gateway-only** (D11 here; #1023's D8 covers only Gemini, OpenAI and
Anthropic). Vertex and Bedrock credentials are cloud identities and Azure's is bound to an
operator endpoint; none goes into an agent container. #1023's preflight refuses them on a direct
environment (`DIRECT_PROVIDER_UNSUPPORTED`). Ollama has no key, but a direct agent route to a host
port contradicts the egress boundary, so it is gateway-only too.

### 9.2 Guardrails (#1024)

#1024's Presidio guardrails run in the gateway and apply to every alias whatever its provider. A
guardrails-required environment with an Ollama alias still masks before the call, though the
model is local. #1024's `running_sha256` binds the same config file, so adding a provider
re-approves guardrails as well as the grant.

## 10. Security summary

| # | Threat | Control |
|---|---|---|
| T1 | Repo config sends a key or prompt to an attacker host | per-provider model patterns and param allow-list; Ollama exact `api_base`; Azure endpoint and key from the operator; Vertex project bound to the key |
| T2 | Gateway used to reach metadata or the host | no endpoint keys; literal patterns; §4.3 env allow-list and variables; P0 fake-listener tests |
| T3 | Default credential chain picks the VM's identity (#1031) | unknown providers and bare names refused; explicit credentials required; `MODEL_CREDENTIAL_MISSING`; launcher required-variable check |
| T4 | Repo pulls a cloud credential from the operator's env | `GATEWAY_ONLY` via `lookup(allow_gateway_only=)`, built-in origin only, checked in `lock.py` |
| T5 | Over-broad credential | service_account only, canonicalised; no session tokens, roles or Entra; least-privilege docs |
| T6 | Agent steers endpoints, or reads credentials back, per request | §4.2 P0 tests per parameter and per info endpoint; ship blocked on a failure |
| T7 | Redirect carries a key elsewhere | P0 records per client; a following client blocks the provider unless the owner accepts it (D15) |
| T8 | Secrets in logs | `set_verbose` refused with gateway-only creds; JWT/bearer/PEM redaction |
| T9 | Terminal injection via printed values | `fullmatch` + `\Z` per printed field; control-character tests |

Residuals, in docs: **R1** a long-lived cloud key sits in the gateway container's environment
(as provider keys do today); **R2** the gateway can reach host ports (macOS always, Linux with
Ollama); **R3** no network block on metadata ranges or general egress from the gateway
(follow-up issue); **R4** Linux Ollama on `0.0.0.0` is reachable from the LAN unless firewalled;
**R5** LiteLLM's own request handling (§4.2) is tested for the listed parameters and endpoints
only; **R6** Azure `base_model` and the Vertex family list are repo-chosen within the patterns,
so pricing trusts the printed grant line; **R7** a revoked key stays stored until re-imported
or cleared.

## 11. Change list and cross-repo impact

- `gateway_keys.py`: provider table, model patterns, per-provider params, `model_info`
  allow-list, `set_verbose` rule, `GatewayCaps`, `required_env`/`routes`, error codes
  (`GATEWAY_PROVIDER_UNKNOWN`, `GATEWAY_PROVIDER_UNSUPPORTED`).
- `env_import.py`: `GATEWAY_ONLY`, `lookup(allow_gateway_only=)`. `services/lock.py`:
  `IMPORT_GATEWAY_ONLY`, `reaches_host` origin check, `GatewayCaps` for `config_aliases`.
  `services/schema.py`: `reaches_host`. `services/plan.py`: `compose_service` takes the review;
  grant `routes`. `services/engine.py`: `allow_gateway_only`, `MODEL_CREDENTIAL_MISSING`,
  `VERTEX_PROJECT_MISMATCH`, Azure endpoint check. `guide_checks.py`, `newagent.py`, `stackfor.py`:
  `required_env`, `Provider.keys`. Redaction shapes (lane A's `redaction.py`; coordinate).
- `builtin_registry/service/litellm/1.1.0/**` … `1.4.0/**` (new, one per slice); `cost` label.
- Docs: `docs/services.md`, `docs/commands/new.md`, `docs/commands/cost.md`, a provider page
  (credential shapes, IAM guidance, residuals, Linux Ollama warning); `changelog.d/` per slice.
- No `agent.yaml`, JSON Schema or `agent.lock` change. Grant digests change only for stacks that
  use a new provider or move to a new litellm version.
- **Cloud:** no code change (no OSS version pin); a `CLAUDE.md` note when the first provider
  ships. **Website:** nothing until release R; then only `verified` rows.

## 12. Acceptance criteria

1. P0 (#1031): an unknown prefix, a bare model name and a credential-less `vertex_ai/` or
   `bedrock/` entry are refused; every config shipped in the repo (starters, Northwind, test
   fixtures) keeps its `config_review` result and grant digest byte for byte (golden test).
2. Table tests: every §4.1 malformed form, per-provider model-pattern bypasses (`bedrock/arn:…`,
   `bedrock/converse/…`, `vertex_ai/openai/…`, `azure_ai/…`, case variants), refused `model_info`
   keys, and control characters per printed field. No config value is echoed in an error.
3. `GATEWAY_ONLY`: refused for a project service, a project `service/litellm`, an MCP version (at
   load), a relay, a built-in non-gateway service and `secret set --from-env`; accepted for the
   built-in gateway.
4. `MODEL_CREDENTIAL_MISSING` and `VERTEX_PROJECT_MISMATCH` at `up`; the launcher exits on an
   unset required variable, a non-`service_account` or non-canonical-able JSON, a bad
   `universe_domain`/`token_uri`, a multi-line file, a file over 16 KiB and a bad Azure endpoint.
5. Pinned-image checks (P0, real Docker, `AGENTBREEDER_DOCKER_TEST=1`, run locally like the
   existing Docker tests, recorded in the PR): metadata variables (each one, fake listeners on
   169.254.169.254 and 169.254.170.2); redirect behaviour per client; §4.2 client-side parameters and credential readback;
   no request to a non-11434 host port from any accepted config.
6. Log check: at `LITELLM_LOG=WARNING` no credential of any type appears in `services logs`.
7. **Per provider, before `verified: true` and before any docs claim:** a real run on macOS +
   Docker Desktop with a real credential: `services up` with the grant shown, a `new agent
   --model <provider>` chat turn through the gateway, one embedding call, `agentbreeder cost`
   showing the alias (or `no price` for Ollama), recorded in a verification record with the
   redaction OCR check. Northwind 61/61 is not required per provider; where run, the score is
   stated.
8. Ollama on Linux: a run on a Linux host (with the documented `OLLAMA_HOST` setup) before Linux
   is claimed.

## 13. Owner decisions

- **D1** Order: **Ollama first** (no account or spend; runnable on the owner's Mac today), then
  Vertex, Bedrock, Azure. *Rec: yes.* Vertex second because a GCP project exists for #908.
- **D2** Refuse unknown providers and bare model names in `config_review` (#1031), shipped ahead
  of the new providers. *Rec: yes.*
- **D3** Gateway-only import names (§3.5), importable only by the built-in `gateway/litellm`
  service via `lookup(allow_gateway_only=)`, checked by origin in `lock.py`. *Rec: yes.*
- **D4** Ollama `api_base` is exactly `http://host.docker.internal:11434`. *Rec: yes.*
- **D5** No built-in `service/ollama` in this epic. *Rec: yes.*
- **D6** Vertex: `service_account` keys only, canonicalised, project bound to the key's
  `project_id`. *Rec: yes.*
- **D7** Bedrock: model ids and inference-profile ids only (no ARNs, no `/` routes); no session
  tokens, roles, profiles or custom endpoints. *Rec: yes.*
- **D8** If B0 shows LiteLLM 1.106.2 supports `AWS_BEARER_TOKEN_BEDROCK`, use a Bedrock API key
  (import `BEDROCK_API_KEY`) instead of the IAM pair. *Rec: yes, narrower scope.*
- **D9** Azure: endpoint and key both from the operator; `*.openai.azure.com` only; Entra ID
  later. *Rec: yes.*
- **D10** File the embedding-route staleness (§7.2) as its own issue; it does not gate this spec.
  *Rec: yes.*
- **D11** All four providers are gateway-only; `cost` shows `no price (local model)` for Ollama.
  *Rec: yes.*
- **D12** One new `service/litellm` minor per provider slice, never declaring an unverified
  provider. *Rec: yes.*
- **D13** Refuse `model_info` keys other than `base_model`/`mode` for every config, and
  `set_verbose: true` when a gateway-only credential is used. *Rec: yes.*
- **D14** A provider whose §4.2 client-side test lets a parameter through does not ship until a
  fix exists (a LiteLLM setting or a separately specced request filter). *Rec: yes.*
- **D15** A provider whose client follows a 3xx carrying the credential (P0, §4.1) is blocked
  unless the owner accepts that residual for that provider by name; the acceptance goes in the
  verification record and the docs. Azure (OpenAI SDK) is the likely case. *Rec: block by
  default; decide per provider with P0's evidence.*

## 14. Slices (after D1–D15)

| Slice | Content | Acceptance |
|---|---|---|
| P0 | #1031: unknown prefixes and bare names refused; golden digests. Pinned-image harness: metadata variables, redirects, §4.2 client-side parameters (key-only providers now; each provider slice adds its own) | §12.1, §12.5 for existing providers |
| P1 | provider table, model patterns, `model_info` allow-list, `GatewayCaps` at all four callers, `required_env`/`routes`, `GATEWAY_ONLY` in `denied()`/`lookup()` + lock check (no new litellm version yet; tests use a fixture built-in gateway spec) | §12.2, §12.3, the `up`-side half of §12.4, against the fixture; re-run on the real built-in in V1, B1 and A1 |
| O1 | litellm 1.1.0: launcher env allow-list and required-variable check, §4.3 variables, Ollama, `reaches_host`, `new agent --model ollama`, cost label, docs | launcher half of §12.4, §12.5, §12.6, §12.7 for Ollama (macOS); §12.8 before Linux |
| V1 | litellm 1.2.0: Vertex (canonical JSON, project binding), starter, docs + IAM guide | §12.5 + §12.7 for Vertex |
| B0/B1 | B0: Bedrock API key and redirect checks against the pinned image (before B1's names are fixed); B1: litellm 1.3.0 Bedrock | §12.5 + §12.7 for Bedrock |
| A1 | litellm 1.4.0: Azure with operator endpoint, `base_model`, starter, docs | §12.5 + §12.7 for Azure |

Each slice is its own PR with a different-model review and the full gate. A provider whose real
run has not happened keeps `verified: false` and is called config-only in docs.

## 15. Review log

| Round | Reviewer (Sonnet, read-only) | Result | Findings folded in |
|---|---|---|---|
| r1 | /architect | CHANGES REQUESTED (4 high, 5 medium, 3 low) | import carve-out moved from schema load to `lookup(allow_gateway_only=)` + `lock.py` origin check; `host_ports` renamed `reaches_host`, macOS already resolves `host.docker.internal`, Linux `OLLAMA_HOST` LAN note, Kubernetes unaffected; four `config_review` callers, `GatewayCaps`, guide_checks `not checked` for non-built-in refs; structured `required_env` (guide_checks parsed labels); one litellm minor per slice, B0 before names are fixed; bare model names and a model-pattern column; `model_info` allow-list; "missing" = not resolved or stored, launcher marker replaced by a config parse; unrun claims marked; K1 split out; harness location and conditional grant keys |
| r1 | /security | CHANGES REQUESTED (2 high, 7 medium, 4 low) | Azure endpoint and key from the operator, not the repo; client-side parameter tests (§4.2, D14); launcher environment from an allow-list, container-credential/web-identity/endpoint/proxy variables dropped, `GCE_METADATA_IP`/`ROOT` added, casing recorded; Vertex canonical re-serialise, `universe_domain`, project bound to the key; per-provider model regexes (no ARN, no `/` routes, exact prefixes); `fullmatch` + `\Z`, no punycode, a pattern per printed field; built-in by origin, shadowing refused; Linux host-gateway reaches all host ports; `set_verbose` and redaction shapes; `model_info` price keys; launcher required-variable check; `caps` required; F1's LiteLLM half marked expected |
| r2 | /architect | CHANGES REQUESTED (3 medium, 6 low); all r1 findings resolved | MCP versions checked directly (relays are recorded built-in); launcher env allow-list built from `plan.environment()` + the image's enumerated `ENV`, health AC; litellm minors cumulative, launcher work moved to O1, P0 harness sets the variables on 1.0.0; relay and built-in non-gateway tests; Vertex project check in the launcher too, shared constants; Azure endpoint change needs reconfirmation; `model_info` behaviour change in changelog and golden test; PyYAML to confirm in O1; log check in O1 |
| r2 | /security | CHANGES REQUESTED (2 medium, 9 low); r1 findings resolved or recorded | `denied()` refuses every `GATEWAY_ONLY` name, `lookup(allow_gateway_only=)` skips only that rule; stack entries cannot override a built-in's secrets/env (F10, verified); credential readback via `/model/info` etc. and more client-side params in §4.2; Vertex family allow-list (no numeric or `@` models); printable-ASCII pattern for existing providers; `base_model` Azure-only and printed (R6); string-only patterns, `keep_alive` int `-1`; Azure validation on the final value before printing; `GCE_METADATA_IP=metadata.invalid`, no `PYTHON*` wildcard, Bedrock API key needs a config marker; no redirect switch exists, so block or accept per provider; launcher parse fails closed, recursive scan; rotation and stored secrets (R7) |
| r3 | /architect | CHANGES REQUESTED (2 medium, 4 low); r2 findings resolved | `lookup` allowance bypasses `DENYLIST` for the exact `GATEWAY_ONLY` names only; Azure endpoint resolved at plan time into the grant (one consent point; `engine.up` confirms before secrets), re-checked in `_secret`; §6 1.1.0 belongs to O1; launcher env names listed statically, tested equal to `service.yaml`; P1 tests on a fixture gateway, re-run in V1/B1/A1; clear-secrets command moved to a follow-up |
| r3 | /security | APPROVED (4 low) | allowance wording and `AZURE_FOO`/`AWS_SECRET_ACCESS_KEY` tests; T7 reworded and D15 added for redirect acceptance; string-only rule scoped to printed fields; more readback endpoints |
| r4 | /architect | APPROVED (3 low notes) | endpoint lookup in `engine`, passed to a pure `plan.grant()`; Azure grant field only when present (golden test); stored value counts as available |
| r4 | /security | APPROVED (1 low) | one shared resolver helper is the only caller passing the flag; plan-time lookup reads `AZURE_OPENAI_ENDPOINT` only; project-shadowed gateway test |
