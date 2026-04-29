package proxy

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/agentbreeder/agentbreeder/sidecar/internal/config"
	"github.com/agentbreeder/agentbreeder/sidecar/internal/guardrails"
)

func newProxy(t *testing.T, target string, rules []config.GuardrailRule) *Proxy {
	t.Helper()
	e, err := guardrails.New(rules, false)
	if err != nil {
		t.Fatal(err)
	}
	p, err := New(target, e)
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func TestProxyForwardsGET(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/health" {
			t.Errorf("path: %s", r.URL.Path)
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	}))
	defer upstream.Close()

	p := newProxy(t, upstream.URL, nil)
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	p.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("got %d", w.Code)
	}
}

func TestProxyBlocksOnGuardrail(t *testing.T) {
	upstreamHits := 0
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		upstreamHits++
	}))
	defer upstream.Close()

	p := newProxy(t, upstream.URL, []config.GuardrailRule{{
		Name: "no-secrets", Type: "keyword", Pattern: "FORBIDDEN", Action: "block",
	}})
	req := httptest.NewRequest(http.MethodPost, "/invoke",
		strings.NewReader(`{"input":"this contains FORBIDDEN data"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	p.ServeHTTP(w, req)

	if w.Code != http.StatusForbidden {
		t.Errorf("expected 403, got %d", w.Code)
	}
	if upstreamHits != 0 {
		t.Errorf("upstream should not be reached on block")
	}
	var body map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &body)
	if body["rule"] != "no-secrets" {
		t.Errorf("missing rule name: %v", body)
	}
}

func TestProxyRedactsBody(t *testing.T) {
	var got []byte
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got, _ = io.ReadAll(r.Body)
		w.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()

	p := newProxy(t, upstream.URL, []config.GuardrailRule{{
		Name: "ssn", Type: "regex", Pattern: `\d{3}-\d{2}-\d{4}`,
		Action: "redact", Replace: "[SSN]",
	}})
	req := httptest.NewRequest(http.MethodPost, "/invoke",
		strings.NewReader(`{"input":"my SSN is 123-45-6789 ok"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	p.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("got %d, body=%s", w.Code, w.Body.String())
	}
	if strings.Contains(string(got), "123-45-6789") {
		t.Errorf("upstream still got SSN: %s", got)
	}
	if !strings.Contains(string(got), "[SSN]") {
		t.Errorf("upstream missing redaction: %s", got)
	}
}

func TestProxyBadGatewayWhenUpstreamDown(t *testing.T) {
	p := newProxy(t, "http://127.0.0.1:1", nil)
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	p.ServeHTTP(w, req)
	if w.Code != http.StatusBadGateway {
		t.Errorf("expected 502, got %d", w.Code)
	}
}

func TestProxyForwardsPlainBody(t *testing.T) {
	var got []byte
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got, _ = io.ReadAll(r.Body)
		w.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()

	p := newProxy(t, upstream.URL, nil)
	req := httptest.NewRequest(http.MethodPost, "/invoke", strings.NewReader("hello"))
	w := httptest.NewRecorder()
	p.ServeHTTP(w, req)
	if string(got) != "hello" {
		t.Errorf("body modified: %q", got)
	}
}

func TestNewBadURLErrors(t *testing.T) {
	if _, err := New("://bad", nil); err == nil {
		t.Fatal("expected parse error")
	}
}

func TestProxyTarget(t *testing.T) {
	p := newProxy(t, "http://127.0.0.1:9999", nil)
	if !strings.HasPrefix(p.Target(), "http://127.0.0.1:9999") {
		t.Errorf("target wrong: %s", p.Target())
	}
}
