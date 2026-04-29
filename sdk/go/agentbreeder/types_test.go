package agentbreeder

import (
	"encoding/json"
	"testing"
)

func TestInvokeInput_String(t *testing.T) {
	t.Parallel()
	in := NewStringInput("hello")
	got, ok := in.AsString()
	if !ok || got != "hello" {
		t.Fatalf("AsString() = %q, %v; want hello, true", got, ok)
	}
	b, err := json.Marshal(in)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if string(b) != `"hello"` {
		t.Fatalf("marshal got %s", b)
	}
}

func TestInvokeInput_Object(t *testing.T) {
	t.Parallel()
	in, err := NewObjectInput(map[string]any{"messages": []any{"hi"}})
	if err != nil {
		t.Fatalf("NewObjectInput: %v", err)
	}
	if _, ok := in.AsString(); ok {
		t.Fatal("AsString should fail on object input")
	}
	var dst struct {
		Messages []string `json:"messages"`
	}
	if err := in.AsObject(&dst); err != nil {
		t.Fatalf("AsObject: %v", err)
	}
	if len(dst.Messages) != 1 || dst.Messages[0] != "hi" {
		t.Fatalf("decoded %+v", dst)
	}
}

func TestInvokeInput_RoundTripUnmarshal(t *testing.T) {
	t.Parallel()
	body := []byte(`{"input":"hello","session_id":"s-1","config":{"k":"v"}}`)
	var req InvokeRequest
	if err := json.Unmarshal(body, &req); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	got, ok := req.Input.AsString()
	if !ok || got != "hello" {
		t.Fatalf("AsString() = %q, %v", got, ok)
	}
	if req.SessionID != "s-1" {
		t.Fatalf("session_id: %q", req.SessionID)
	}
}

func TestInvokeResponse_SetOutput(t *testing.T) {
	t.Parallel()
	var r InvokeResponse
	if err := r.SetOutput(map[string]any{"answer": 42}); err != nil {
		t.Fatalf("SetOutput: %v", err)
	}
	b, err := json.Marshal(r)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	// Output must round-trip as JSON-shaped, not double-encoded.
	got := string(b)
	if !contains(got, `"output":{"answer":42}`) {
		t.Fatalf("unexpected JSON: %s", got)
	}
}

func TestHealthResponse_IsHealthy(t *testing.T) {
	t.Parallel()
	cases := map[HealthStatus]bool{
		StatusHealthy: true,
		StatusOK:      true,
		StatusLoading: false,
	}
	for status, want := range cases {
		got := HealthResponse{Status: status}.IsHealthy()
		if got != want {
			t.Errorf("IsHealthy(%q) = %v, want %v", status, got, want)
		}
	}
}

func contains(haystack, needle string) bool {
	return len(haystack) >= len(needle) && (haystack == needle ||
		(len(haystack) > 0 && (indexOf(haystack, needle) >= 0)))
}

func indexOf(haystack, needle string) int {
	for i := 0; i+len(needle) <= len(haystack); i++ {
		if haystack[i:i+len(needle)] == needle {
			return i
		}
	}
	return -1
}
