package agentbreeder

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// helper: build a server with the supplied invoke and options.
func newTestServer(t *testing.T, invoke InvokeFunc, opts ...Option) *httptest.Server {
	t.Helper()
	srv := NewServer(invoke, opts...)
	return httptest.NewServer(srv.Handler())
}

// post is a JSON POST helper that fails the test on transport error and
// returns the *http.Response. The caller is responsible for closing the body.
func post(t *testing.T, url, body string) *http.Response {
	t.Helper()
	//nolint:noctx // tests use the default client; ctx is implied by t.
	resp, err := http.Post(url, "application/json", strings.NewReader(body))
	if err != nil {
		t.Fatalf("POST %s: %v", url, err)
	}
	return resp
}

func TestServer_Health_EmitsContractVersionAndShape(t *testing.T) {
	t.Parallel()
	ts := newTestServer(t, nil, WithName("foo"), WithVersion("1.2.3"), WithFramework("custom"))
	defer ts.Close()

	resp, err := http.Get(ts.URL + "/health")
	if err != nil {
		t.Fatalf("GET /health: %v", err)
	}
	defer resp.Body.Close()

	if got := resp.Header.Get(HeaderRuntimeContractVersion); got != "1" {
		t.Fatalf("X-Runtime-Contract-Version = %q, want 1", got)
	}
	var body HealthResponse
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if body.AgentName != "foo" || body.Version != "1.2.3" || body.Framework != "custom" {
		t.Fatalf("body=%+v", body)
	}
	if !body.IsHealthy() {
		t.Fatalf("expected healthy by default; got %q", body.Status)
	}
}

func TestServer_Health_OpenWithoutAuth(t *testing.T) {
	t.Parallel()
	ts := newTestServer(t, nil, WithAuthToken("s3cr3t"))
	defer ts.Close()
	resp, err := http.Get(ts.URL + "/health")
	if err != nil {
		t.Fatalf("GET: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status %d", resp.StatusCode)
	}
}

func TestServer_Health_LoadingState(t *testing.T) {
	t.Parallel()
	srv := NewServer(nil)
	srv.MarkUnloaded()
	ts := httptest.NewServer(srv.Handler())
	defer ts.Close()
	resp, err := http.Get(ts.URL + "/health")
	if err != nil {
		t.Fatalf("GET: %v", err)
	}
	defer resp.Body.Close()
	var body HealthResponse
	_ = json.NewDecoder(resp.Body).Decode(&body)
	if body.Status != StatusLoading {
		t.Fatalf("expected loading, got %q", body.Status)
	}
}

func TestServer_OpenAPI_AdvertisesContractVersion(t *testing.T) {
	t.Parallel()
	ts := newTestServer(t, nil)
	defer ts.Close()
	resp, err := http.Get(ts.URL + "/openapi.json")
	if err != nil {
		t.Fatalf("GET: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	var doc map[string]any
	if err := json.Unmarshal(body, &doc); err != nil {
		t.Fatalf("decode: %v: %s", err, body)
	}
	info, _ := doc["info"].(map[string]any)
	if got := info["x-agentbreeder-runtime-contract"]; got != "1" {
		t.Fatalf("info.x-agentbreeder-runtime-contract = %v, want 1", got)
	}
}

func TestServer_AgentCard_DefaultShape(t *testing.T) {
	t.Parallel()
	ts := newTestServer(t, nil, WithName("bot"), WithVersion("0.9.0"))
	defer ts.Close()
	resp, err := http.Get(ts.URL + "/.well-known/agent.json")
	if err != nil {
		t.Fatalf("GET: %v", err)
	}
	defer resp.Body.Close()
	var card AgentCard
	if err := json.NewDecoder(resp.Body).Decode(&card); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if card.Name != "bot" || card.Version != "0.9.0" {
		t.Fatalf("card=%+v", card)
	}
	if card.Endpoints["invoke"] != "/invoke" {
		t.Fatalf("missing invoke endpoint: %v", card.Endpoints)
	}
}

func TestServer_Invoke_HappyPath(t *testing.T) {
	t.Parallel()
	echo := func(_ context.Context, req InvokeRequest, resp *InvokeResponse) error {
		s, _ := req.Input.AsString()
		return resp.SetOutput(fmt.Sprintf("you said: %s", s))
	}
	ts := newTestServer(t, echo)
	defer ts.Close()

	body := strings.NewReader(`{"input":"hi","session_id":"s-1"}`)
	resp, err := http.Post(ts.URL+"/invoke", "application/json", body)
	if err != nil {
		t.Fatalf("POST: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		got, _ := io.ReadAll(resp.Body)
		t.Fatalf("status=%d body=%s", resp.StatusCode, got)
	}
	var out InvokeResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatalf("decode: %v", err)
	}
	var got string
	if err := json.Unmarshal(out.Output, &got); err != nil {
		t.Fatalf("output decode: %v", err)
	}
	if got != "you said: hi" {
		t.Fatalf("output=%q", got)
	}
	if out.SessionID != "s-1" {
		t.Fatalf("session_id echo broken: %q", out.SessionID)
	}
}

func TestServer_Invoke_AuthEnforced(t *testing.T) {
	t.Parallel()
	called := false
	echo := func(_ context.Context, _ InvokeRequest, _ *InvokeResponse) error {
		called = true
		return nil
	}
	ts := newTestServer(t, echo, WithAuthToken("s3cr3t"))
	defer ts.Close()

	// No header → 401
	resp, err := http.Post(ts.URL+"/invoke", "application/json", strings.NewReader(`{"input":"x"}`))
	if err != nil {
		t.Fatalf("POST: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", resp.StatusCode)
	}

	// Bad token → 403
	req, _ := http.NewRequestWithContext(context.Background(), http.MethodPost, ts.URL+"/invoke", strings.NewReader(`{"input":"x"}`))
	req.Header.Set("Authorization", "Bearer wrong")
	req.Header.Set("Content-Type", "application/json")
	resp2, _ := http.DefaultClient.Do(req)
	resp2.Body.Close()
	if resp2.StatusCode != http.StatusForbidden {
		t.Fatalf("expected 403, got %d", resp2.StatusCode)
	}

	// Good token → 200
	req3, _ := http.NewRequestWithContext(context.Background(), http.MethodPost, ts.URL+"/invoke", strings.NewReader(`{"input":"x"}`))
	req3.Header.Set("Authorization", "Bearer s3cr3t")
	req3.Header.Set("Content-Type", "application/json")
	resp3, _ := http.DefaultClient.Do(req3)
	resp3.Body.Close()
	if resp3.StatusCode != http.StatusOK {
		t.Fatalf("expected 200 with good token, got %d", resp3.StatusCode)
	}
	if !called {
		t.Fatal("invoke handler never reached")
	}
}

func TestServer_Invoke_MalformedJSONIs400(t *testing.T) {
	t.Parallel()
	ts := newTestServer(t, func(_ context.Context, _ InvokeRequest, _ *InvokeResponse) error { return nil })
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/invoke", "application/json", strings.NewReader(`not-json`))
	if err != nil {
		t.Fatalf("POST: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", resp.StatusCode)
	}
	var env ErrorEnvelope
	if err := json.NewDecoder(resp.Body).Decode(&env); err != nil {
		t.Fatalf("decode envelope: %v", err)
	}
	if env.Error.Code != CodeInvalidInput {
		t.Fatalf("expected code %s, got %s", CodeInvalidInput, env.Error.Code)
	}
}

func TestServer_Invoke_MissingInputIs400(t *testing.T) {
	t.Parallel()
	ts := newTestServer(t, func(_ context.Context, _ InvokeRequest, _ *InvokeResponse) error { return nil })
	defer ts.Close()
	resp := post(t, ts.URL+"/invoke", `{}`)
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", resp.StatusCode)
	}
}

func TestServer_Invoke_HandlerError_Is500WithEnvelope(t *testing.T) {
	t.Parallel()
	ts := newTestServer(t, func(_ context.Context, _ InvokeRequest, _ *InvokeResponse) error {
		return errors.New("boom")
	})
	defer ts.Close()
	resp := post(t, ts.URL+"/invoke", `{"input":"x"}`)
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", resp.StatusCode)
	}
	var env ErrorEnvelope
	_ = json.NewDecoder(resp.Body).Decode(&env)
	if env.Error.Code != CodeInternalError || env.Error.Message != "boom" {
		t.Fatalf("envelope=%+v", env)
	}
}

func TestServer_Invoke_NotLoadedIs503(t *testing.T) {
	t.Parallel()
	srv := NewServer(func(_ context.Context, _ InvokeRequest, _ *InvokeResponse) error { return nil })
	srv.MarkUnloaded()
	ts := httptest.NewServer(srv.Handler())
	defer ts.Close()

	resp := post(t, ts.URL+"/invoke", `{"input":"x"}`)
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d", resp.StatusCode)
	}
	if resp.Header.Get("Retry-After") == "" {
		t.Fatal("503 must set Retry-After")
	}
}

func TestServer_Invoke_NotImplementedWhenInvokeNil(t *testing.T) {
	t.Parallel()
	ts := newTestServer(t, nil)
	defer ts.Close()
	resp := post(t, ts.URL+"/invoke", `{"input":"x"}`)
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotImplemented {
		t.Fatalf("expected 501, got %d", resp.StatusCode)
	}
}

// -- streaming ---------------------------------------------------------------

func TestServer_Stream_EmitsEventsAndDoneTerminator(t *testing.T) {
	t.Parallel()
	stream := func(_ context.Context, _ InvokeRequest, out chan<- StreamEvent) error {
		out <- StreamEvent{Data: SseTextDelta{Delta: "hello "}}
		out <- StreamEvent{Data: SseTextDelta{Delta: "world"}}
		out <- StreamEvent{Name: SseEventStep, Data: SseStepEvent{Description: "done"}}
		return nil
	}
	ts := newTestServer(t, nil, WithStream(stream))
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/stream", "application/json", strings.NewReader(`{"input":"x"}`))
	if err != nil {
		t.Fatalf("POST: %v", err)
	}
	defer resp.Body.Close()

	if resp.Header.Get("Content-Type") != "text/event-stream" {
		t.Fatalf("Content-Type=%q", resp.Header.Get("Content-Type"))
	}
	if resp.Header.Get("Cache-Control") != "no-cache" {
		t.Fatalf("Cache-Control=%q", resp.Header.Get("Cache-Control"))
	}
	if resp.Header.Get("X-Accel-Buffering") != "no" {
		t.Fatalf("X-Accel-Buffering=%q", resp.Header.Get("X-Accel-Buffering"))
	}

	body, _ := io.ReadAll(resp.Body)
	if !bytes.Contains(body, []byte(`data: {"delta":"hello "}`)) {
		t.Fatalf("missing first delta: %s", body)
	}
	if !bytes.Contains(body, []byte(`event: step`)) {
		t.Fatalf("missing step event: %s", body)
	}
	if !bytes.HasSuffix(bytes.TrimRight(body, "\n"), []byte("data: [DONE]")) {
		t.Fatalf("missing [DONE] terminator: %q", body)
	}
}

func TestServer_Stream_ErrorEmitsErrorEventThenDone(t *testing.T) {
	t.Parallel()
	stream := func(_ context.Context, _ InvokeRequest, out chan<- StreamEvent) error {
		out <- StreamEvent{Data: SseTextDelta{Delta: "partial"}}
		return errors.New("rate limited")
	}
	ts := newTestServer(t, nil, WithStream(stream))
	defer ts.Close()

	resp := post(t, ts.URL+"/stream", `{"input":"x"}`)
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if !bytes.Contains(body, []byte(`event: error`)) {
		t.Fatalf("missing error event: %s", body)
	}
	if !bytes.Contains(body, []byte("rate limited")) {
		t.Fatalf("error message missing: %s", body)
	}
	if !bytes.Contains(body, []byte("data: [DONE]")) {
		t.Fatalf("terminator missing on error: %s", body)
	}
}

func TestServer_Stream_NotImplementedWhenNoHandler(t *testing.T) {
	t.Parallel()
	ts := newTestServer(t, nil)
	defer ts.Close()
	resp := post(t, ts.URL+"/stream", `{"input":"x"}`)
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotImplemented {
		t.Fatalf("expected 501, got %d", resp.StatusCode)
	}
}

// -- resume ------------------------------------------------------------------

func TestServer_Resume_NotImplementedByDefault(t *testing.T) {
	t.Parallel()
	ts := newTestServer(t, nil)
	defer ts.Close()
	resp := post(t, ts.URL+"/resume", `{"thread_id":"t","human_input":"y"}`)
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotImplemented {
		t.Fatalf("expected 501, got %d", resp.StatusCode)
	}
}

func TestServer_Resume_HappyPath(t *testing.T) {
	t.Parallel()
	resume := func(_ context.Context, req ResumeRequest, resp *InvokeResponse) error {
		return resp.SetOutput("resumed:" + req.ThreadID)
	}
	ts := newTestServer(t, nil, WithResume(resume))
	defer ts.Close()
	resp := post(t, ts.URL+"/resume", `{"thread_id":"t-99","human_input":"go"}`)
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		t.Fatalf("status=%d body=%s", resp.StatusCode, body)
	}
}

func TestServer_Resume_MissingThreadIs422(t *testing.T) {
	t.Parallel()
	ts := newTestServer(t, nil, WithResume(func(_ context.Context, _ ResumeRequest, _ *InvokeResponse) error { return nil }))
	defer ts.Close()
	resp := post(t, ts.URL+"/resume", `{}`)
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422, got %d", resp.StatusCode)
	}
}

// -- listen-and-serve ---------------------------------------------------------

func TestServer_ListenAndServe_GracefulShutdown(t *testing.T) {
	t.Parallel()
	srv := NewServer(func(_ context.Context, _ InvokeRequest, _ *InvokeResponse) error { return nil })
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	// Bind on a random port
	if err := srv.ListenAndServe(ctx, "127.0.0.1:0"); err != nil {
		t.Fatalf("ListenAndServe: %v", err)
	}
}

// helper for SSE parsing if needed
func _scanSse(body io.Reader) []string {
	scanner := bufio.NewScanner(body)
	var lines []string
	for scanner.Scan() {
		lines = append(lines, scanner.Text())
	}
	return lines
}
