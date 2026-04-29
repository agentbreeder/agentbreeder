# syntax=docker/dockerfile:1.7
#
# Multi-stage build for the AgentBreeder Go example agent.
# Builder produces a static binary; final image is distroless/static for a
# tiny attack surface (~15 MB).
#
# IMPORTANT: build this image with the repo root as the context so the
# `replace` directive in examples/go-agent/go.mod can find sdk/go/agentbreeder:
#
#     docker build -f examples/go-agent/Dockerfile -t agentbreeder/go-agent-example .
FROM golang:1.22-alpine AS builder
WORKDIR /src
RUN apk add --no-cache git ca-certificates

# The replace directive in examples/go-agent/go.mod points at
# ../../sdk/go/agentbreeder; preserve that relative path inside the image.
COPY sdk/go/agentbreeder /src/sdk/go/agentbreeder
COPY examples/go-agent /src/examples/go-agent

WORKDIR /src/examples/go-agent
RUN go mod tidy && go mod download
ENV CGO_ENABLED=0 GOOS=linux
RUN go build -trimpath -ldflags='-s -w' -o /out/agent ./...

FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /app
COPY --from=builder /out/agent /app/agent
EXPOSE 8080
USER nonroot:nonroot

ENV AGENT_NAME="go-hello-agent" \
    AGENT_VERSION="0.1.0" \
    AGENT_FRAMEWORK="custom"

ENTRYPOINT ["/app/agent"]
