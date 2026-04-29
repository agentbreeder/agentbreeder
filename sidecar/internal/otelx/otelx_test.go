package otelx

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestDisabledExporterIsNoOp(t *testing.T) {
	e := New("", nil, "demo-agent")
	if e.Enabled() {
		t.Fatal("empty endpoint should disable exporter")
	}
	span := e.StartSpan("demo")
	span.EndTime = time.Now()
	if err := e.Export(context.Background(), span); err != nil {
		t.Errorf("disabled exporter must not error: %v", err)
	}
}

func TestExportShape(t *testing.T) {
	var captured map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(body, &captured)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	e := New(srv.URL, map[string]string{"x-team": "eng"}, "my-agent")
	span := e.StartSpan("agent.invoke")
	span.Attributes["model"] = "claude-sonnet-4"
	span.Attributes["tokens"] = 100
	span.StatusCode = "OK"
	span.EndTime = span.StartTime.Add(50 * time.Millisecond)

	if err := e.Export(context.Background(), span); err != nil {
		t.Fatal(err)
	}

	rs, ok := captured["resourceSpans"].([]any)
	if !ok || len(rs) != 1 {
		t.Fatalf("missing resourceSpans: %v", captured)
	}
	first := rs[0].(map[string]any)
	scope := first["scopeSpans"].([]any)[0].(map[string]any)
	span0 := scope["spans"].([]any)[0].(map[string]any)
	if span0["name"] != "agent.invoke" {
		t.Errorf("name: %v", span0["name"])
	}
	if span0["traceId"] == "" || span0["spanId"] == "" {
		t.Errorf("missing trace/span ids")
	}
	if int(span0["kind"].(float64)) != 1 {
		t.Errorf("kind should be internal=1: %v", span0["kind"])
	}
}

func TestExportRespectsHeaders(t *testing.T) {
	var got string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Get("x-team")
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	e := New(srv.URL, map[string]string{"x-team": "platform"}, "agent")
	if err := e.Export(context.Background(), e.StartSpan("foo")); err != nil {
		t.Fatal(err)
	}
	if got != "platform" {
		t.Errorf("custom header lost: %q", got)
	}
}

func TestExportCollectorError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
	}))
	defer srv.Close()

	e := New(srv.URL, nil, "agent")
	err := e.Export(context.Background(), e.StartSpan("foo"))
	if err == nil || !strings.Contains(err.Error(), "400") {
		t.Errorf("expected 400 error, got %v", err)
	}
}

func TestCloseRefusesFurtherExport(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	e := New(srv.URL, nil, "agent")
	e.Close()
	err := e.Export(context.Background(), e.StartSpan("foo"))
	if err == nil {
		t.Fatal("expected error after Close()")
	}
}

func TestSpanIDsUniqueAndShape(t *testing.T) {
	if len(NewTraceID()) != 32 {
		t.Errorf("trace id should be 32 hex chars")
	}
	if len(NewSpanID()) != 16 {
		t.Errorf("span id should be 16 hex chars")
	}
	a, b := NewTraceID(), NewTraceID()
	if a == b {
		t.Errorf("trace ids should be unique")
	}
}

func TestEndpointPathNormalisation(t *testing.T) {
	hits := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/traces" {
			t.Errorf("expected /v1/traces, got %s", r.URL.Path)
		}
		hits++
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	for _, ep := range []string{srv.URL, srv.URL + "/", srv.URL + "/v1/traces"} {
		e := New(ep, nil, "agent")
		if err := e.Export(context.Background(), e.StartSpan("foo")); err != nil {
			t.Fatalf("ep %s: %v", ep, err)
		}
	}
	if hits != 3 {
		t.Errorf("expected 3 calls, got %d", hits)
	}
}

func TestAttrTypes(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if !strings.Contains(string(body), "stringValue") {
			t.Errorf("missing string attr: %s", body)
		}
		if !strings.Contains(string(body), "intValue") {
			t.Errorf("missing int attr: %s", body)
		}
		if !strings.Contains(string(body), "boolValue") {
			t.Errorf("missing bool attr: %s", body)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	e := New(srv.URL, nil, "agent")
	s := e.StartSpan("attrs")
	s.Attributes["s"] = "hi"
	s.Attributes["i"] = 42
	s.Attributes["b"] = true
	s.Attributes["f"] = 1.5
	if err := e.Export(context.Background(), s); err != nil {
		t.Fatal(err)
	}
}
