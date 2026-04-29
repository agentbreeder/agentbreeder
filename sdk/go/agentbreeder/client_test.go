package agentbreeder

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestClient_ListAgents_HappyPath(t *testing.T) {
	t.Parallel()
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/agents", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer test-key" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data": []AgentSummary{{ID: "a1", Name: "support", Version: "1.0.0", Team: "eng"}},
			"meta": map[string]any{"page": 1, "total": 1},
		})
	})
	ts := httptest.NewServer(mux)
	defer ts.Close()

	c := NewClient(ts.URL, "test-key")
	got, err := c.ListAgents(context.Background())
	if err != nil {
		t.Fatalf("ListAgents: %v", err)
	}
	if len(got) != 1 || got[0].Name != "support" {
		t.Fatalf("got %+v", got)
	}
}

func TestClient_GetAgent_NotFoundIsAPIError(t *testing.T) {
	t.Parallel()
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/agents/missing", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"errors": []ErrorBody{{Code: "AGENT_NOT_FOUND", Message: "not here"}},
		})
	})
	ts := httptest.NewServer(mux)
	defer ts.Close()

	c := NewClient(ts.URL, "k")
	_, err := c.GetAgent(context.Background(), "missing")
	if err == nil {
		t.Fatal("expected error")
	}
	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("want *APIError, got %T", err)
	}
	if apiErr.Status != http.StatusNotFound || len(apiErr.Errors) != 1 || apiErr.Errors[0].Code != "AGENT_NOT_FOUND" {
		t.Fatalf("apiErr=%+v", apiErr)
	}
}

func TestClient_RegisterAgent_PostsRequestBody(t *testing.T) {
	t.Parallel()
	var seen string
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/agents", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		buf := make([]byte, 1024)
		n, _ := r.Body.Read(buf)
		seen = string(buf[:n])
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data": AgentSummary{ID: "id-1", Name: "demo", Version: "0.1.0", Team: "eng"},
		})
	})
	ts := httptest.NewServer(mux)
	defer ts.Close()

	c := NewClient(ts.URL, "k")
	out, err := c.RegisterAgent(context.Background(), AgentRegistration{
		Name: "demo", Version: "0.1.0", Team: "eng", Owner: "a@b.c", Language: "go", Framework: "custom",
	})
	if err != nil {
		t.Fatalf("RegisterAgent: %v", err)
	}
	if out.ID != "id-1" {
		t.Fatalf("out=%+v", out)
	}
	if !strings.Contains(seen, `"language":"go"`) {
		t.Fatalf("body missing language=go: %s", seen)
	}
}

func TestClient_ListModels_DecodeShape(t *testing.T) {
	t.Parallel()
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/models", func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data": []ModelSummary{{ID: "m1", Name: "claude-sonnet-4", Provider: "anthropic"}},
		})
	})
	ts := httptest.NewServer(mux)
	defer ts.Close()

	c := NewClient(ts.URL, "")
	got, err := c.ListModels(context.Background())
	if err != nil {
		t.Fatalf("ListModels: %v", err)
	}
	if len(got) != 1 || got[0].Provider != "anthropic" {
		t.Fatalf("got=%+v", got)
	}
}

func TestClient_PutSecret_RoundTrip(t *testing.T) {
	t.Parallel()
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/secrets/", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPut {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data": Secret{Name: "OPENAI_API_KEY", Backend: "aws"},
		})
	})
	ts := httptest.NewServer(mux)
	defer ts.Close()

	c := NewClient(ts.URL, "")
	got, err := c.PutSecret(context.Background(), "OPENAI_API_KEY", "aws", nil)
	if err != nil {
		t.Fatalf("PutSecret: %v", err)
	}
	if got.Backend != "aws" {
		t.Fatalf("got=%+v", got)
	}
}

func TestClient_EmptyBaseURLErrors(t *testing.T) {
	t.Parallel()
	c := NewClient("", "k")
	_, err := c.ListAgents(context.Background())
	if err == nil {
		t.Fatal("expected error from empty base url")
	}
}

func TestClient_DeleteAgent_NoBody(t *testing.T) {
	t.Parallel()
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/agents/foo", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodDelete {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})
	ts := httptest.NewServer(mux)
	defer ts.Close()
	c := NewClient(ts.URL, "")
	if err := c.DeleteAgent(context.Background(), "foo"); err != nil {
		t.Fatalf("DeleteAgent: %v", err)
	}
}
