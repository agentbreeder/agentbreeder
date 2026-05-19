# AgentBreeder Sidecar

> Cross-cutting concerns layer for every deployed agent. One Go binary, every language, every cloud.

The sidecar is auto-injected next to your agent container by the AgentBreeder deploy pipeline. The agent talks to it over `localhost`; the sidecar handles **all** of the boring-but-mandatory infrastructure work — tracing, cost attribution, guardrails, A2A, MCP, and bearer-token auth — so the language runtime stays thin.

This directory is a self-contained Go module. It does **not** depend on the AgentBreeder Python codebase.

---

## Endpoints

| Listener | Default addr | Purpose | Auth |
|---|---|---|---|
| Inbound | `:8080` | Public ingress; proxies authorised + guardrail-approved traffic to the agent on `:8081` | Bearer (`AGENT_AUTH_TOKEN`) |
| Local | `127.0.0.1:9090` | Helper endpoints for the agent process | none (loopback only) |

### Inbound (`:8080`)

| Path | Method | Description |
|---|---|---|
| `/health` | GET | Liveness probe (does **not** require auth) |
| `/openapi.json` | GET | Self-describing schema (does **not** require auth) |
| `/*` | * | Forwarded to the agent on `AGENTBREEDER_SIDECAR_AGENT_URL` (default `http://127.0.0.1:8081`) after guardrail egress checks |

### Local helpers (`127.0.0.1:9090`)

| Path | Method | Description |
|---|---|---|
| `/a2a/{peer}` | POST | JSON-RPC 2.0 send to a configured A2A peer |
| `/mcp/{server}` | POST | JSON-RPC passthrough to a configured MCP server (HTTP/SSE; stdio TBD) |
| `/cost` | POST | Record a cost/token event in the AgentBreeder API (`costs` + `audit_log`) |
| `/health` | GET | Liveness for the local helpers |

---

## Environment variables

| Var | Required | Default | Notes |
|---|---|---|---|
| `AGENT_NAME` | ✅ | — | Canonical agent identifier emitted in cost / trace events |
| `AGENT_VERSION` |  | — | Optional — populated from `agent.yaml` `version:` |
| `AGENT_AUTH_TOKEN` | ✅ | — | Bearer token validated on inbound `:8080`. Set `AGENTBREEDER_SIDECAR_ALLOW_NO_AUTH=1` for local dev |
| `AGENTBREEDER_SIDECAR` |  | enabled | Set to `disabled` / `off` / `false` / `0` and the binary exits cleanly |
| `AGENTBREEDER_SIDECAR_INBOUND_ADDR` |  | `:8080` | Public listener |
| `AGENTBREEDER_SIDECAR_A2A_ADDR` |  | `127.0.0.1:9090` | Local helpers listener |
| `AGENTBREEDER_SIDECAR_AGENT_URL` |  | `http://127.0.0.1:8081` | Where to forward inbound traffic |
| `OTEL_EXPORTER_OTLP_ENDPOINT` |  | — | OTLP/HTTP base URL (e.g. `http://collector:4318`). When unset, span export is a no-op |
| `OTEL_EXPORTER_OTLP_HEADERS` |  | — | Comma-separated `k=v` pairs |
| `AGENTBREEDER_API_URL` |  | — | API base URL for cost emission. When unset, cost events are dropped silently |
| `AGENTBREEDER_API_TOKEN` |  | — | Bearer token used when calling the AgentBreeder API |

---

## Configuration file

Mount a YAML file at `/etc/agentbreeder/sidecar.yaml` (path overridable with `--config`) to ship guardrail rules, A2A peers, and MCP servers. **Env vars always win** over file values, so deployers can tweak behaviour without rebuilding the image.

```yaml
agent_name: my-agent
agent_version: 1.0.0

guardrails:
  - name: ssn-block
    type: regex
    pattern: '\b\d{3}-\d{2}-\d{4}\b'
    action: block             # block | redact | warn (default redact)
  - name: profanity
    type: keyword
    pattern: "darn,heck"
    action: redact
    replace: "[CENSORED]"

a2a_peers:
  research-agent: https://research.run.app
  # optionally `peer-token@@<url>` to use a per-peer bearer token
  finance-agent: finance-tok@@https://finance.run.app

mcp_servers:
  docs:
    transport: http
    url: https://docs.example.com/mcp
  filesystem:
    transport: stdio          # not supported in v1 — see TODO in internal/mcp
    command: node
    args: ["/srv/fs-server.js"]
```

The default PII rules (SSN, credit card, email) ship in every sidecar — user rules run **after** them and can override / extend.

---

## Build

```bash
# Native
go build -o sidecar ./cmd/sidecar

# Docker (multi-arch)
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag agentbreeder/agentbreeder-sidecar:dev \
  --build-arg VERSION=dev \
  -f Dockerfile .

# Tag matches the AgentBreeder release version in CI:
# agentbreeder/agentbreeder-sidecar:<version>
```

Final image is built `FROM gcr.io/distroless/static-debian12:nonroot` — < 20 MB, no shell, runs as non-root.

---

## Running locally

```bash
docker run --rm \
  -e AGENT_NAME=demo \
  -e AGENT_AUTH_TOKEN=dev-token \
  -e AGENTBREEDER_SIDECAR_AGENT_URL=http://host.docker.internal:8081 \
  -p 8080:8080 \
  agentbreeder/agentbreeder-sidecar:dev

# Then verify:
curl http://localhost:8080/health
curl -H "Authorization: Bearer dev-token" \
     -H "Content-Type: application/json" \
     -d '{"input":"hi"}' \
     http://localhost:8080/invoke
```

For a complete docker-compose example see `sidecar/examples/compose/docker-compose.yml`.

---

## Tests

```bash
go test ./... -cover
go vet ./...
gofmt -l .              # must be empty
```

Coverage targets ≥ 85 % per package.

---

## Auto-injection

Deployers in `engine/deployers/{docker_compose,gcp_cloudrun,aws_ecs,kubernetes}.py` automatically inject the sidecar when `agent.yaml` declares any of:

- `guardrails:`
- `tools:` containing MCP servers
- `a2a:` block

To disable injection (local development), set `AGENTBREEDER_SIDECAR=disabled` in the environment. The Python deployer-side helper `engine.sidecar.should_inject` honours the same env var.

---

## Versioning

The sidecar binary version is independent of the AgentBreeder platform version. CI tags the image as `agentbreeder/agentbreeder-sidecar:<release-version>` whenever the platform releases.
