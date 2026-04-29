package mcp

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/agentbreeder/agentbreeder/sidecar/internal/config"
)

func TestForwardHTTP(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if !strings.Contains(string(body), `"hello"`) {
			t.Errorf("payload not forwarded: %s", body)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":"1","result":{"ok":true}}`))
	}))
	defer srv.Close()

	c := NewClient(map[string]config.MCPServerSpec{
		"docs": {Transport: "http", URL: srv.URL},
	}, 5*time.Second)

	body, ct, err := c.Forward(context.Background(), "docs", []byte(`{"hello":1}`))
	if err != nil {
		t.Fatal(err)
	}
	if ct != "application/json" {
		t.Errorf("content-type: %q", ct)
	}
	if !strings.Contains(string(body), `"ok":true`) {
		t.Errorf("body not forwarded: %s", body)
	}
}

func TestForwardUnknownServer(t *testing.T) {
	c := NewClient(nil, 0)
	if _, _, err := c.Forward(context.Background(), "nope", nil); err == nil {
		t.Fatal("expected error for unknown server")
	}
}

func TestForwardStdioReturnsClearError(t *testing.T) {
	c := NewClient(map[string]config.MCPServerSpec{
		"local-fs": {Transport: "stdio", Command: "node", Args: []string{"server.js"}},
	}, 0)
	_, _, err := c.Forward(context.Background(), "local-fs", nil)
	if err == nil || !strings.Contains(err.Error(), "stdio") {
		t.Fatalf("expected stdio not-supported error, got: %v", err)
	}
}

func TestForwardUpstream5xx(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
		_, _ = w.Write([]byte("upstream broken"))
	}))
	defer srv.Close()

	c := NewClient(map[string]config.MCPServerSpec{
		"docs": {Transport: "http", URL: srv.URL},
	}, 2*time.Second)
	_, _, err := c.Forward(context.Background(), "docs", []byte("{}"))
	if err == nil {
		t.Fatal("expected error on 502")
	}
}

func TestJSONRPCErrorShape(t *testing.T) {
	raw := JSONRPCError("req-1", -32600, "bad request")
	var got map[string]any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if got["jsonrpc"] != "2.0" {
		t.Errorf("missing jsonrpc field: %v", got)
	}
	e, ok := got["error"].(map[string]any)
	if !ok {
		t.Fatalf("error object missing: %v", got)
	}
	if int(e["code"].(float64)) != -32600 {
		t.Errorf("code: %v", e["code"])
	}
}
