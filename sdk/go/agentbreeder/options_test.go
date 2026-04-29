package agentbreeder

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestNewClient_WithHTTPClient(t *testing.T) {
	t.Parallel()
	custom := &http.Client{}
	c := NewClient("http://x", "k", WithHTTPClient(custom))
	if c.HTTPClient != custom {
		t.Fatal("WithHTTPClient was not applied")
	}
	// nil should be ignored; default kept.
	c2 := NewClient("http://x", "", WithHTTPClient(nil))
	if c2.HTTPClient == nil {
		t.Fatal("nil http client must not erase default")
	}
}

func TestAPIError_Error(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name string
		e    *APIError
		want string
	}{
		{"with envelope", &APIError{Status: 404, Errors: []ErrorBody{{Code: "X", Message: "y"}}}, "X — y"},
		{"raw body", &APIError{Status: 500, Body: []byte("oops")}, "oops"},
	}
	for _, c := range cases {
		c := c
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			if !strings.Contains(c.e.Error(), c.want) {
				t.Fatalf("Error() = %q; want substring %q", c.e.Error(), c.want)
			}
		})
	}
}

func TestServer_WithOpenAPI_OverridesEmbedded(t *testing.T) {
	t.Parallel()
	override := []byte(`{"openapi":"3.1.0","info":{"title":"custom","version":"1"}}`)
	srv := NewServer(nil, WithOpenAPI(override))
	ts := httptest.NewServer(srv.Handler())
	defer ts.Close()
	resp, err := http.Get(ts.URL + "/openapi.json")
	if err != nil {
		t.Fatalf("GET: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if !strings.Contains(string(body), `"title":"custom"`) {
		t.Fatalf("override not served: %s", body)
	}
}

func TestServer_WithAgentCard_OverridesDefault(t *testing.T) {
	t.Parallel()
	srv := NewServer(nil, WithAgentCard(AgentCard{
		Name: "alt", Version: "9.9.9",
		Endpoints: map[string]string{"invoke": "/x"},
	}))
	ts := httptest.NewServer(srv.Handler())
	defer ts.Close()
	resp, err := http.Get(ts.URL + "/.well-known/agent.json")
	if err != nil {
		t.Fatalf("GET: %v", err)
	}
	defer resp.Body.Close()
	var card AgentCard
	_ = json.NewDecoder(resp.Body).Decode(&card)
	if card.Name != "alt" || card.Endpoints["invoke"] != "/x" {
		t.Fatalf("override broken: %+v", card)
	}
}

func TestServer_WithLogger_StreamBufferAndStartedLoaded(t *testing.T) {
	t.Parallel()
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	srv := NewServer(
		func(_ context.Context, _ InvokeRequest, _ *InvokeResponse) error { return nil },
		WithLogger(logger),
		WithStreamBuffer(4),
		WithLoadedAtStart(),
	)
	if !srv.Loaded() {
		t.Fatal("WithLoadedAtStart should mark loaded")
	}
	srv.MarkLoaded()
	if !srv.Loaded() {
		t.Fatal("MarkLoaded idempotent")
	}
}

func TestNewObjectInput_FailsOnUnmarshalable(t *testing.T) {
	t.Parallel()
	type bad struct {
		Ch chan int `json:"ch"`
	}
	_, err := NewObjectInput(bad{Ch: make(chan int)})
	if err == nil {
		t.Fatal("expected marshal error")
	}
}

func TestInvokeInput_AsString_OnObjectFails(t *testing.T) {
	t.Parallel()
	in, _ := NewObjectInput(map[string]int{"x": 1})
	if _, ok := in.AsString(); ok {
		t.Fatal("AsString should fail on object")
	}
}

func TestInvokeInput_AsObject_EmptyErrors(t *testing.T) {
	t.Parallel()
	var in InvokeInput
	var dst map[string]any
	if err := in.AsObject(&dst); err == nil {
		t.Fatal("expected error on empty input")
	}
}

func TestInvokeInput_MarshalJSON_NullWhenEmpty(t *testing.T) {
	t.Parallel()
	var in InvokeInput
	b, err := in.MarshalJSON()
	if err != nil || string(b) != "null" {
		t.Fatalf("got %s, %v", b, err)
	}
}

func TestSetOutput_FailsOnUnmarshalable(t *testing.T) {
	t.Parallel()
	var r InvokeResponse
	if err := r.SetOutput(map[string]any{"ch": make(chan int)}); err == nil {
		t.Fatal("expected marshal error")
	}
}

func TestClient_DoSurfaces500AsAPIError(t *testing.T) {
	t.Parallel()
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/agents", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte("not json"))
	})
	ts := httptest.NewServer(mux)
	defer ts.Close()
	c := NewClient(ts.URL, "")
	_, err := c.ListAgents(context.Background())
	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("want *APIError; got %T", err)
	}
	if apiErr.Status != 500 {
		t.Fatalf("status=%d", apiErr.Status)
	}
}

func TestClient_ListSecrets_AndGetSecret(t *testing.T) {
	t.Parallel()
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/secrets", func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data": []Secret{{Name: "OPENAI_API_KEY", Backend: "aws"}},
		})
	})
	mux.HandleFunc("/api/v1/secrets/OPENAI_API_KEY", func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data": Secret{Name: "OPENAI_API_KEY", Backend: "aws"},
		})
	})
	ts := httptest.NewServer(mux)
	defer ts.Close()
	c := NewClient(ts.URL, "")
	got, err := c.ListSecrets(context.Background())
	if err != nil || len(got) != 1 {
		t.Fatalf("ListSecrets: %v %+v", err, got)
	}
	one, err := c.GetSecret(context.Background(), "OPENAI_API_KEY")
	if err != nil || one.Backend != "aws" {
		t.Fatalf("GetSecret: %v %+v", err, one)
	}
}
