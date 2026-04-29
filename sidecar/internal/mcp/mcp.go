// Package mcp provides minimal MCP-server passthrough.
//
// v1 supports HTTP/SSE upstream MCP servers. The agent calls
//
//	POST localhost:9091/mcp/<server>      { "method": "...", "params": {...} }
//
// and the sidecar forwards the JSON-RPC request to the configured upstream URL.
//
// Stdio MCP servers are intentionally deferred — they require process
// management and a persistent JSON-RPC session, which is more invasive than
// fits in the v1 sidecar. See TODO in StdioPassthrough.
package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/agentbreeder/agentbreeder/sidecar/internal/config"
)

// Client routes /mcp/<server> requests to upstream MCP servers.
type Client struct {
	HTTP    *http.Client
	Servers map[string]config.MCPServerSpec
}

// NewClient builds a client given the server map from config.
func NewClient(servers map[string]config.MCPServerSpec, timeout time.Duration) *Client {
	if timeout == 0 {
		timeout = 30 * time.Second
	}
	if servers == nil {
		servers = map[string]config.MCPServerSpec{}
	}
	return &Client{
		HTTP:    &http.Client{Timeout: timeout},
		Servers: servers,
	}
}

// Forward proxies the JSON-RPC payload to the upstream server.
// Returns the upstream response body and content-type.
func (c *Client) Forward(ctx context.Context, server string, payload []byte) ([]byte, string, error) {
	spec, ok := c.Servers[server]
	if !ok {
		return nil, "", fmt.Errorf("mcp server %q not configured", server)
	}
	switch spec.Transport {
	case "http", "sse":
		return c.forwardHTTP(ctx, spec, payload)
	case "stdio":
		// TODO(track-j): implement stdio MCP passthrough. v1 returns a clear error.
		return nil, "", errors.New("mcp stdio transport not yet supported by sidecar v1")
	default:
		return nil, "", fmt.Errorf("mcp server %q has unknown transport %q", server, spec.Transport)
	}
}

func (c *Client) forwardHTTP(ctx context.Context, spec config.MCPServerSpec, payload []byte) ([]byte, string, error) {
	url := strings.TrimRight(spec.URL, "/")
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(payload))
	if err != nil {
		return nil, "", fmt.Errorf("mcp: build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")

	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, "", fmt.Errorf("mcp: forward: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, "", fmt.Errorf("mcp: read response: %w", err)
	}
	if resp.StatusCode >= 500 {
		return nil, "", fmt.Errorf("mcp: upstream returned %d: %s", resp.StatusCode, string(body))
	}
	ct := resp.Header.Get("Content-Type")
	if ct == "" {
		ct = "application/json"
	}
	return body, ct, nil
}

// JSONRPCError encodes a JSON-RPC 2.0 error response that the sidecar can hand
// back to the agent when forwarding fails.
func JSONRPCError(id any, code int, message string) []byte {
	b, _ := json.Marshal(map[string]any{
		"jsonrpc": "2.0",
		"id":      id,
		"error":   map[string]any{"code": code, "message": message},
	})
	return b
}
