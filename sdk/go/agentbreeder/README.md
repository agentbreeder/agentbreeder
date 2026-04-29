# AgentBreeder Go SDK

Tier-2 polyglot SDK for the AgentBreeder platform. Implements the
[Runtime Contract v1](../../../engine/schema/runtime-contract-v1.md).

## Install

```bash
go get github.com/agentbreeder/agentbreeder/sdk/go/agentbreeder
```

Requires Go 1.22+.

## Quick start

```go
package main

import (
    "context"
    "log"

    "github.com/agentbreeder/agentbreeder/sdk/go/agentbreeder"
)

func main() {
    invoke := func(ctx context.Context, req agentbreeder.InvokeRequest, resp *agentbreeder.InvokeResponse) error {
        s, _ := req.Input.AsString()
        return resp.SetOutput("you said: " + s)
    }

    srv := agentbreeder.NewServer(invoke,
        agentbreeder.WithName("my-agent"),
        agentbreeder.WithVersion("0.1.0"),
        agentbreeder.WithFramework("custom"),
    )
    if err := srv.ListenAndServe(context.Background(), ":8080"); err != nil {
        log.Fatal(err)
    }
}
```

The SDK auto-wires `/health`, `/invoke`, `/stream`, `/resume`,
`/openapi.json`, and `/.well-known/agent.json`. Bearer-token auth is read
from `AGENT_AUTH_TOKEN` (disabled when empty).

## What you get

- **`NewServer(InvokeFunc, ...Option)`** — contract-conforming `http.Handler`.
- **`WithStream(StreamFunc)`** — opt into SSE streaming on `/stream`.
- **`WithResume(ResumeFunc)`** — opt into HITL resume on `/resume`.
- **`Client`** — typed registry client (agents, models, secrets).
- **`AgentCard`, `HealthResponse`, `InvokeRequest/Response`, `ErrorEnvelope`** —
  contract-faithful types.

## Regenerating types from the OpenAPI spec

The hand-curated public types in `types.go` mirror
[`engine/schema/runtime-contract-v1.openapi.yaml`](../../../engine/schema/runtime-contract-v1.openapi.yaml).
To regenerate the raw `oapi-codegen` package alongside (for users who want it
verbatim):

```bash
go run github.com/oapi-codegen/oapi-codegen/v2/cmd/oapi-codegen@v2.4.1 \
    -generate types,client,server \
    -package gen \
    -o gen/runtime_contract.gen.go \
    ../../../engine/schema/runtime-contract-v1.openapi.yaml
```

The hand-curated types are kept verbatim with the spec.

## Tests

```bash
go test -race -cover ./...
gofmt -l .
go vet ./...
```

Coverage target: ≥85%.
