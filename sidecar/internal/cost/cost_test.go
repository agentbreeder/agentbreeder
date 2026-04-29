package cost

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

func TestSendPostsCostsAndAudit(t *testing.T) {
	type call struct {
		path, auth string
		body       []byte
	}
	var mu sync.Mutex
	var calls []call

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		mu.Lock()
		calls = append(calls, call{r.URL.Path, r.Header.Get("Authorization"), body})
		mu.Unlock()
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := NewClient(srv.URL, "tok", 2*time.Second)
	err := c.Send(context.Background(), Event{
		Agent: "demo", Model: "claude-sonnet-4",
		InputTokens: 100, OutputTokens: 50, CostUSD: 0.0034,
		TraceID: "trace-1",
	})
	if err != nil {
		t.Fatal(err)
	}

	if len(calls) != 2 {
		t.Fatalf("expected 2 calls (costs + audit), got %d", len(calls))
	}

	paths := map[string]bool{calls[0].path: true, calls[1].path: true}
	if !paths["/api/v1/costs"] || !paths["/api/v1/audit"] {
		t.Errorf("missing expected paths, got %v", paths)
	}

	for _, c := range calls {
		if c.auth != "Bearer tok" {
			t.Errorf("missing bearer token on %s: %q", c.path, c.auth)
		}
	}
}

func TestSendValidates(t *testing.T) {
	c := NewClient("http://example", "", 0)
	tests := []Event{
		{Model: "x"}, // missing agent
		{Agent: "x"}, // missing model
		{Agent: "x", Model: "y", InputTokens: -1}, // negative tokens
	}
	for i, ev := range tests {
		if err := c.Send(context.Background(), ev); err == nil {
			t.Errorf("test %d: expected validation error", i)
		}
	}
}

func TestDisabledClientNoOp(t *testing.T) {
	c := NewClient("", "", 0)
	if err := c.Send(context.Background(), Event{Agent: "a", Model: "b"}); err != nil {
		t.Errorf("disabled client should silently skip: %v", err)
	}
}

func TestAuditFailureDoesNotMaskSuccess(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/audit" {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := NewClient(srv.URL, "", 2*time.Second)
	err := c.Send(context.Background(), Event{Agent: "a", Model: "m"})
	if err != nil {
		t.Errorf("audit failure must be best-effort, got: %v", err)
	}
}

func TestCostsFailureSurfaces(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/costs" {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := NewClient(srv.URL, "", 2*time.Second)
	err := c.Send(context.Background(), Event{Agent: "a", Model: "m"})
	if err == nil {
		t.Fatal("costs 4xx should surface")
	}
}

func TestAuditMetadataPopulated(t *testing.T) {
	var auditBody []byte
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/audit" {
			auditBody, _ = io.ReadAll(r.Body)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := NewClient(srv.URL, "", 2*time.Second)
	_ = c.Send(context.Background(), Event{
		Agent: "demo", Model: "gpt-4o",
		InputTokens: 10, OutputTokens: 5, CostUSD: 0.0001,
		TraceID: "tr-9",
	})
	if len(auditBody) == 0 {
		t.Fatal("expected audit body")
	}
	var got map[string]any
	if err := json.Unmarshal(auditBody, &got); err != nil {
		t.Fatal(err)
	}
	if got["event_type"] != "cost.recorded" {
		t.Errorf("event_type wrong: %v", got["event_type"])
	}
	if got["actor"] != "sidecar" {
		t.Errorf("actor wrong: %v", got["actor"])
	}
}
