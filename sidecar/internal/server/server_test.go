package server

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/agentbreeder/agentbreeder/sidecar/internal/config"
)

// freePort returns a port that is available at the moment of the call.
// There's a small TOCTOU window before the next listener binds it; tests below
// retry once on failure to keep them deterministic.
func freePort(t *testing.T) string {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := l.Addr().(*net.TCPAddr).Port
	_ = l.Close()
	return strings.Split((&net.TCPAddr{Port: port}).String(), ":")[1]
}

func waitForHealth(url string, attempts int) error {
	c := &http.Client{Timeout: 200 * time.Millisecond}
	for i := 0; i < attempts; i++ {
		resp, err := c.Get(url)
		if err == nil {
			_ = resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				return nil
			}
		}
		time.Sleep(50 * time.Millisecond)
	}
	return context.DeadlineExceeded
}

func contextWithCancel() (context.Context, context.CancelFunc) {
	return context.WithCancel(context.Background())
}

func deadlineCh(seconds int) <-chan time.Time {
	return time.After(time.Duration(seconds) * time.Second)
}

func newTestServer(t *testing.T, agentURL string) *Server {
	t.Helper()
	cfg := &config.Config{
		AgentName: "demo",
		AuthToken: "tok",
		AgentURL:  agentURL,
	}
	cfg.Validate()
	s, err := New(cfg, nil)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func TestHealthHandler(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()

	s := newTestServer(t, upstream.URL)
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	s.InboundRouter().ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("got %d", w.Code)
	}
	var body map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &body)
	if body["sidecar"] != "agentbreeder" {
		t.Errorf("missing fields: %v", body)
	}
	if body["agent"] != "demo" {
		t.Errorf("agent name not surfaced: %v", body)
	}
}

func TestInboundProxyRequiresAuth(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()

	s := newTestServer(t, upstream.URL)
	req := httptest.NewRequest(http.MethodPost, "/invoke", strings.NewReader("{}"))
	w := httptest.NewRecorder()
	s.InboundRouter().ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("expected 401 without auth, got %d", w.Code)
	}
}

func TestInboundProxyForwardsAuthenticated(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"data":"ok"}`))
	}))
	defer upstream.Close()

	s := newTestServer(t, upstream.URL)
	req := httptest.NewRequest(http.MethodPost, "/invoke", strings.NewReader("{}"))
	req.Header.Set("Authorization", "Bearer tok")
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	s.InboundRouter().ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("got %d, body=%s", w.Code, w.Body.String())
	}
}

func TestA2AHandlerForwards(t *testing.T) {
	peer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":"1","result":{"echo":` + string(body) + `}}`))
	}))
	defer peer.Close()

	cfg := &config.Config{
		AgentName: "demo", AuthToken: "tok", AgentURL: "http://127.0.0.1:1",
		A2APeers: map[string]string{"buddy": peer.URL},
	}
	cfg.Validate()
	s, err := New(cfg, nil)
	if err != nil {
		t.Fatal(err)
	}

	req := httptest.NewRequest(http.MethodPost, "/a2a/buddy",
		bytes.NewReader([]byte(`{"method":"tasks/send","params":{"message":"hi"}}`)))
	w := httptest.NewRecorder()
	s.LocalRouter().ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("got %d, body=%s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), `"jsonrpc"`) {
		t.Errorf("response not JSON-RPC: %s", w.Body.String())
	}
}

func TestA2AHandlerUnknownPeer(t *testing.T) {
	s := newTestServer(t, "http://127.0.0.1:1")
	req := httptest.NewRequest(http.MethodPost, "/a2a/who",
		strings.NewReader(`{"method":"tasks/send"}`))
	w := httptest.NewRecorder()
	s.LocalRouter().ServeHTTP(w, req)
	if w.Code != http.StatusBadGateway {
		t.Errorf("expected 502, got %d", w.Code)
	}
}

func TestMCPHandlerForwards(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"got":` + string(body) + `}`))
	}))
	defer upstream.Close()

	cfg := &config.Config{
		AgentName: "demo", AuthToken: "tok", AgentURL: "http://127.0.0.1:1",
		MCPServers: map[string]config.MCPServerSpec{
			"docs": {Transport: "http", URL: upstream.URL},
		},
	}
	cfg.Validate()
	s, _ := New(cfg, nil)

	req := httptest.NewRequest(http.MethodPost, "/mcp/docs", strings.NewReader(`{"hi":1}`))
	w := httptest.NewRecorder()
	s.LocalRouter().ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("got %d, body=%s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), `"got"`) {
		t.Errorf("body not forwarded: %s", w.Body.String())
	}
}

func TestCostHandlerWritesEvent(t *testing.T) {
	hits := 0
	api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hits++
		w.WriteHeader(http.StatusOK)
	}))
	defer api.Close()

	cfg := &config.Config{
		AgentName: "demo", AuthToken: "tok", AgentURL: "http://127.0.0.1:1",
		APIBaseURL: api.URL,
	}
	cfg.Validate()
	s, _ := New(cfg, nil)

	req := httptest.NewRequest(http.MethodPost, "/cost",
		strings.NewReader(`{"model":"gpt","input_tokens":5,"output_tokens":3,"cost_usd":0.001}`))
	w := httptest.NewRecorder()
	s.LocalRouter().ServeHTTP(w, req)
	if w.Code != http.StatusAccepted {
		t.Errorf("expected 202, got %d, body=%s", w.Code, w.Body.String())
	}
	if hits != 2 {
		t.Errorf("expected 2 API calls (costs+audit), got %d", hits)
	}
}

func TestCostHandlerRejectsBadJSON(t *testing.T) {
	s := newTestServer(t, "http://127.0.0.1:1")
	req := httptest.NewRequest(http.MethodPost, "/cost", strings.NewReader("bad"))
	w := httptest.NewRecorder()
	s.LocalRouter().ServeHTTP(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestOpenAPIHandler(t *testing.T) {
	s := newTestServer(t, "http://127.0.0.1:1")
	req := httptest.NewRequest(http.MethodGet, "/openapi.json", nil)
	w := httptest.NewRecorder()
	s.InboundRouter().ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "openapi") {
		t.Errorf("body missing openapi field: %s", w.Body.String())
	}
}

func TestRunStartsAndShutsDownCleanly(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()

	port1 := freePort(t)
	port2 := freePort(t)
	cfg := &config.Config{
		AgentName:   "demo",
		AuthToken:   "tok",
		AgentURL:    upstream.URL,
		InboundAddr: "127.0.0.1:" + port1,
		A2AAddr:     "127.0.0.1:" + port2,
	}
	cfg.Validate()
	s, err := New(cfg, nil)
	if err != nil {
		t.Fatal(err)
	}

	ctx, cancel := contextWithCancel()
	errCh := make(chan error, 1)
	go func() { errCh <- s.Run(ctx) }()

	// Poll the inbound /health endpoint to confirm the listener is up.
	if err := waitForHealth("http://127.0.0.1:"+port1+"/health", 50); err != nil {
		t.Fatal(err)
	}

	cancel()
	if err := <-errCh; err != nil {
		t.Errorf("Run returned error: %v", err)
	}
}

func TestServerNewWithBadAgentURL(t *testing.T) {
	cfg := &config.Config{AgentName: "demo", AuthToken: "tok", AgentURL: "://bad"}
	if _, err := New(cfg, nil); err == nil {
		t.Fatal("expected proxy build error")
	}
}

func TestServerNewWithBadGuardrail(t *testing.T) {
	cfg := &config.Config{
		AgentName: "demo", AuthToken: "tok", AgentURL: "http://127.0.0.1:1",
		Guardrails: []config.GuardrailRule{{Name: "x", Type: "regex", Pattern: "[("}},
	}
	if _, err := New(cfg, nil); err == nil {
		t.Fatal("expected guardrail compile error")
	}
}

func TestRunReturnsListenerError(t *testing.T) {
	// Use the same port twice — the second listener will fail to bind.
	port := freePort(t)
	cfg := &config.Config{
		AgentName:   "demo",
		AuthToken:   "tok",
		AgentURL:    "http://127.0.0.1:1",
		InboundAddr: "127.0.0.1:" + port,
		A2AAddr:     "127.0.0.1:" + port,
	}
	cfg.Validate()
	s, err := New(cfg, nil)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := contextWithCancel()
	defer cancel()
	errCh := make(chan error, 1)
	go func() { errCh <- s.Run(ctx) }()

	select {
	case err := <-errCh:
		if err == nil {
			t.Errorf("expected bind error")
		}
	case <-deadlineCh(2):
		t.Errorf("Run did not surface bind error in time")
	}
}

func TestBuildA2APeers(t *testing.T) {
	out := buildA2APeers(map[string]string{
		"plain":    "https://a.example",
		"with-tok": "secret-token@@https://b.example",
	}, "fallback")
	if out["plain"].URL != "https://a.example" || out["plain"].Token != "fallback" {
		t.Errorf("plain peer: %+v", out["plain"])
	}
	if out["with-tok"].URL != "https://b.example" || out["with-tok"].Token != "secret-token" {
		t.Errorf("with-tok peer: %+v", out["with-tok"])
	}
}
