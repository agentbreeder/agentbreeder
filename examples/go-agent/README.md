# go-hello-agent

A minimal Go agent example for AgentBreeder. Calls Anthropic's Claude API
through a tiny `net/http` client; satisfies Runtime Contract v1 via the
Go SDK at [`sdk/go/agentbreeder`](../../sdk/go/agentbreeder).

## Run locally (mock — no API key needed)

```bash
go run .

# In another terminal:
curl -sX POST localhost:8080/invoke \
    -H 'content-type: application/json' \
    -d '{"input":"hello"}'
```

Without `ANTHROPIC_API_KEY`, the agent returns a `[mock] You asked: …`
response so you can validate the contract end-to-end without spending money.

## Run with Claude

```bash
export ANTHROPIC_API_KEY=sk-ant-...
go run .
```

## Deploy

```bash
agentbreeder validate agent.yaml
agentbreeder deploy agent.yaml --target local
```

## Test

```bash
go test -race -cover ./...
```
