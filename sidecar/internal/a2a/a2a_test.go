package a2a

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestSendIssuesValidJSONRPC(t *testing.T) {
	var got Request
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/a2a" {
			t.Errorf("expected /a2a path, got %s", r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer peer-tok" {
			t.Errorf("missing or wrong bearer token: %q", r.Header.Get("Authorization"))
		}
		_ = json.NewDecoder(r.Body).Decode(&got)
		_ = json.NewEncoder(w).Encode(Response{
			JSONRPC: "2.0", ID: got.ID,
			Result: map[string]any{"task_id": "t-1", "status": "completed", "output": "hi"},
		})
	}))
	defer srv.Close()

	c := NewClient(map[string]Peer{"buddy": {URL: srv.URL, Token: "peer-tok"}}, 5*time.Second)
	resp, err := c.Send(context.Background(), "buddy", MethodSend, map[string]any{"message": "hello"})
	if err != nil {
		t.Fatal(err)
	}
	if resp.Error != nil {
		t.Fatalf("unexpected error: %+v", resp.Error)
	}
	if got.JSONRPC != "2.0" {
		t.Errorf("jsonrpc field missing: %q", got.JSONRPC)
	}
	if got.Method != MethodSend {
		t.Errorf("wrong method: %q", got.Method)
	}
	if got.Params["message"] != "hello" {
		t.Errorf("params.message wrong: %v", got.Params["message"])
	}
	if got.ID == "" {
		t.Errorf("missing id")
	}
}

func TestSendUnknownPeerErrors(t *testing.T) {
	c := NewClient(nil, 0)
	_, err := c.Send(context.Background(), "nope", MethodSend, nil)
	if err == nil {
		t.Fatal("expected error for unknown peer")
	}
}

func TestSendPeerErrorStatus(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte("boom"))
	}))
	defer srv.Close()

	c := NewClient(map[string]Peer{"x": {URL: srv.URL}}, 2*time.Second)
	_, err := c.Send(context.Background(), "x", MethodSend, nil)
	if err == nil {
		t.Fatal("expected error on 500 response")
	}
}

func TestSendDecodesError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(Response{
			JSONRPC: "2.0", ID: "1",
			Error: &Error{Code: -32601, Message: "method not found"},
		})
	}))
	defer srv.Close()

	c := NewClient(map[string]Peer{"x": {URL: srv.URL}}, 2*time.Second)
	resp, err := c.Send(context.Background(), "x", "bogus/method", nil)
	if err != nil {
		t.Fatal(err)
	}
	if resp.Error == nil {
		t.Fatal("expected error in response")
	}
	if resp.Error.Code != -32601 {
		t.Errorf("error code wrong: %d", resp.Error.Code)
	}
	if resp.Error.Error() == "" {
		t.Errorf("Error() should produce a string")
	}
}

func TestNewRequestIDIsUnique(t *testing.T) {
	seen := map[string]struct{}{}
	for i := 0; i < 1000; i++ {
		id := newRequestID()
		if id == "" {
			t.Fatal("empty id")
		}
		if _, dup := seen[id]; dup {
			t.Fatalf("duplicate id: %s", id)
		}
		seen[id] = struct{}{}
	}
}

func TestSendEmptyMethod(t *testing.T) {
	c := NewClient(map[string]Peer{"x": {URL: "http://example"}}, 0)
	_, err := c.sendTo(context.Background(), c.Peers["x"], "", nil)
	if err == nil {
		t.Fatal("empty method should error")
	}
}
