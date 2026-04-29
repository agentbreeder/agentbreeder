// Package main is a minimal example Go agent for AgentBreeder.
//
// It satisfies the Runtime Contract v1 by calling
// agentbreeder.NewServer, and answers /invoke by querying Anthropic's
// Claude over HTTP. To stay framework-light and not pull a heavy SDK, the
// example talks to api.anthropic.com directly via net/http; replace with
// github.com/anthropics/anthropic-sdk-go in production.
//
// Configuration (env vars, all injected by the deployer):
//
//   - AGENT_NAME, AGENT_VERSION, AGENT_FRAMEWORK — set by `agentbreeder deploy`
//   - AGENT_MODEL                                  — model id (e.g. claude-sonnet-4-20250514)
//   - AGENT_AUTH_TOKEN                             — bearer token for /invoke
//   - ANTHROPIC_API_KEY                            — Claude credential
//   - PORT                                         — defaults to 8080
//
// Usage:
//
//	go run .
//	curl -sX POST localhost:8080/invoke \
//	    -H 'content-type: application/json' \
//	    -d '{"input":"What is the capital of France?"}'
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/agentbreeder/agentbreeder/sdk/go/agentbreeder"
)

const (
	defaultModel    = "claude-sonnet-4-20250514"
	anthropicAPIURL = "https://api.anthropic.com/v1/messages"
	anthropicAPIVer = "2023-06-01"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	slog.SetDefault(logger)

	client := newAnthropicClient(envOr("ANTHROPIC_API_KEY", ""), envOr("AGENT_MODEL", defaultModel))

	srv := agentbreeder.NewServer(
		client.Invoke,
		agentbreeder.WithFramework("custom"),
		agentbreeder.WithLogger(logger),
	)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	addr := ":" + envOr("PORT", "8080")
	logger.Info("starting go-hello-agent", "addr", addr, "model", client.Model)
	if err := srv.ListenAndServe(ctx, addr); err != nil {
		logger.Error("server failed", "err", err.Error())
		os.Exit(1)
	}
}

// anthropicClient is the smallest possible Claude client that satisfies the
// example. It exposes Invoke as an [agentbreeder.InvokeFunc].
type anthropicClient struct {
	APIKey string
	Model  string
	HTTP   *http.Client
}

func newAnthropicClient(apiKey, model string) *anthropicClient {
	if model == "" {
		model = defaultModel
	}
	return &anthropicClient{
		APIKey: apiKey,
		Model:  model,
		HTTP:   &http.Client{Timeout: 60 * time.Second},
	}
}

// Invoke is the agent's contract handler. It accepts either a plain string
// or {"prompt": "..."}.
func (c *anthropicClient) Invoke(ctx context.Context, req agentbreeder.InvokeRequest, resp *agentbreeder.InvokeResponse) error {
	prompt, err := extractPrompt(req)
	if err != nil {
		return err
	}
	if c.APIKey == "" {
		// Mock path — keeps the example runnable without credentials.
		return resp.SetOutput(fmt.Sprintf("[mock] You asked: %s", prompt))
	}

	answer, err := c.complete(ctx, prompt)
	if err != nil {
		return err
	}
	return resp.SetOutput(answer)
}

func extractPrompt(req agentbreeder.InvokeRequest) (string, error) {
	if s, ok := req.Input.AsString(); ok {
		return s, nil
	}
	var body struct {
		Prompt string `json:"prompt"`
	}
	if err := req.Input.AsObject(&body); err == nil && body.Prompt != "" {
		return body.Prompt, nil
	}
	return "", errors.New("input must be a string or {\"prompt\": \"...\"}")
}

type messagesRequest struct {
	Model     string    `json:"model"`
	MaxTokens int       `json:"max_tokens"`
	Messages  []message `json:"messages"`
}

type message struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type messagesResponse struct {
	Content []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	} `json:"content"`
}

func (c *anthropicClient) complete(ctx context.Context, prompt string) (string, error) {
	body, err := json.Marshal(messagesRequest{
		Model:     c.Model,
		MaxTokens: 1024,
		Messages:  []message{{Role: "user", Content: prompt}},
	})
	if err != nil {
		return "", fmt.Errorf("marshal: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, anthropicAPIURL, bytes.NewReader(body))
	if err != nil {
		return "", fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-api-key", c.APIKey)
	req.Header.Set("anthropic-version", anthropicAPIVer)

	httpResp, err := c.HTTP.Do(req)
	if err != nil {
		return "", fmt.Errorf("anthropic http: %w", err)
	}
	defer httpResp.Body.Close()

	respBody, _ := io.ReadAll(httpResp.Body)
	if httpResp.StatusCode >= 400 {
		return "", fmt.Errorf("anthropic api %d: %s", httpResp.StatusCode, respBody)
	}

	var out messagesResponse
	if err := json.Unmarshal(respBody, &out); err != nil {
		return "", fmt.Errorf("decode: %w", err)
	}
	if len(out.Content) == 0 {
		return "", errors.New("no content in response")
	}
	return out.Content[0].Text, nil
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
