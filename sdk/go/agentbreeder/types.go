// Code mirrors engine/schema/runtime-contract-v1.openapi.yaml.
//
// In production this file is regenerated via:
//
//	go run github.com/oapi-codegen/oapi-codegen/v2/cmd/oapi-codegen@v2.4.1 \
//	    -generate types,client,server -package gen \
//	    -o gen/runtime_contract.gen.go \
//	    ../../../engine/schema/runtime-contract-v1.openapi.yaml
//
// The hand-rolled definitions here are the public, hand-curated surface re-
// exported from the gen package. They are kept verbatim with the spec so
// that swapping in a generated file is a drop-in replacement. See README for
// the regeneration command.
//
// Why hand-curated and generated coexist: oapi-codegen produces names that
// are not idiomatic Go (e.g. InvokeRequestInput0 for oneOf branches). We
// expose Go-natural types here and rely on encoding/json for the wire
// shape. The generated package is included as a vendoring target so users
// who want raw oapi-codegen output can opt in.

package agentbreeder

import (
	"encoding/json"
	"fmt"
)

// HealthStatus is the canonical status value emitted by /health.
type HealthStatus string

const (
	StatusHealthy HealthStatus = "healthy"
	StatusLoading HealthStatus = "loading"
	// StatusOK is accepted as a backward-compat alias for StatusHealthy.
	// New servers MUST emit StatusHealthy. SDK clients MUST tolerate both.
	StatusOK HealthStatus = "ok"
)

// HealthResponse is the body of GET /health. See spec §4.1.
type HealthResponse struct {
	Status    HealthStatus `json:"status"`
	AgentName string       `json:"agent_name"`
	Version   string       `json:"version"`
	Framework string       `json:"framework,omitempty"`
}

// IsHealthy returns true if the status is either "healthy" or the legacy "ok".
func (h HealthResponse) IsHealthy() bool {
	return h.Status == StatusHealthy || h.Status == StatusOK
}

// AgentCard is the body of GET /.well-known/agent.json — the A2A discovery
// metadata. See spec §4.5.
type AgentCard struct {
	Name      string            `json:"name"`
	Version   string            `json:"version"`
	Framework string            `json:"framework,omitempty"`
	Protocol  string            `json:"protocol,omitempty"`
	Endpoints map[string]string `json:"endpoints"`
}

// InvokeInput is the polymorphic input field on InvokeRequest. The contract
// allows a string, an object, or an array. We model it as json.RawMessage to
// avoid forcing a representation choice on the agent author; helper accessors
// disambiguate at use-time.
type InvokeInput struct {
	raw json.RawMessage
}

// NewStringInput wraps a string for /invoke or /stream.
func NewStringInput(s string) InvokeInput {
	b, _ := json.Marshal(s)
	return InvokeInput{raw: b}
}

// NewObjectInput wraps an arbitrary map/struct.
func NewObjectInput(v any) (InvokeInput, error) {
	b, err := json.Marshal(v)
	if err != nil {
		return InvokeInput{}, fmt.Errorf("agentbreeder: marshal object input: %w", err)
	}
	return InvokeInput{raw: b}, nil
}

// Raw returns the underlying JSON bytes.
func (i InvokeInput) Raw() json.RawMessage { return i.raw }

// AsString attempts to decode the input as a string. Returns ok=false if the
// payload is not a JSON string.
func (i InvokeInput) AsString() (string, bool) {
	if len(i.raw) == 0 {
		return "", false
	}
	var s string
	if err := json.Unmarshal(i.raw, &s); err != nil {
		return "", false
	}
	return s, true
}

// AsObject decodes the input into the supplied destination. The destination
// must be a pointer.
func (i InvokeInput) AsObject(dst any) error {
	if len(i.raw) == 0 {
		return fmt.Errorf("agentbreeder: empty invoke input")
	}
	return json.Unmarshal(i.raw, dst)
}

// MarshalJSON implements json.Marshaler.
func (i InvokeInput) MarshalJSON() ([]byte, error) {
	if len(i.raw) == 0 {
		return []byte("null"), nil
	}
	return i.raw, nil
}

// UnmarshalJSON implements json.Unmarshaler.
func (i *InvokeInput) UnmarshalJSON(b []byte) error {
	i.raw = append(i.raw[:0], b...)
	return nil
}

// InvokeRequest is the body of POST /invoke and POST /stream. See spec §4.2.
type InvokeRequest struct {
	Input     InvokeInput    `json:"input"`
	SessionID string         `json:"session_id,omitempty"`
	Config    map[string]any `json:"config,omitempty"`
	Metadata  map[string]any `json:"metadata,omitempty"`
}

// InvokeResponse is the body of a 200 from POST /invoke. See spec §4.2.
//
// The trace_id, tokens, cost_usd, model, and latency_ms fields are reserved
// for the platform sidecar (Track J). Agents MUST NOT populate them in v1;
// the sidecar overwrites them in-flight.
type InvokeResponse struct {
	Output    json.RawMessage `json:"output"`
	SessionID string          `json:"session_id,omitempty"`
	Metadata  map[string]any  `json:"metadata,omitempty"`

	// Reserved — sidecar-injected (Track J). See spec §4.2.
	TraceID   string  `json:"trace_id,omitempty"`
	Tokens    *Tokens `json:"tokens,omitempty"`
	CostUSD   float64 `json:"cost_usd,omitempty"`
	Model     string  `json:"model,omitempty"`
	LatencyMs int64   `json:"latency_ms,omitempty"`
}

// Tokens is the optional `tokens` object on InvokeResponse.
type Tokens struct {
	Input  int `json:"input"`
	Output int `json:"output"`
	Total  int `json:"total"`
}

// SetOutput is a convenience for agent code that wants to return a Go value
// without building json.RawMessage by hand.
func (r *InvokeResponse) SetOutput(v any) error {
	b, err := json.Marshal(v)
	if err != nil {
		return fmt.Errorf("agentbreeder: marshal output: %w", err)
	}
	r.Output = b
	return nil
}

// ResumeRequest is the body of POST /resume. Optional in v1 — frameworks
// without checkpointing return 404 or 501.
type ResumeRequest struct {
	ThreadID   string          `json:"thread_id"`
	HumanInput json.RawMessage `json:"human_input"`
}

// ErrorEnvelope is the structured error body returned on 4xx/5xx. See §4.6.
type ErrorEnvelope struct {
	Error   ErrorBody `json:"error"`
	TraceID string    `json:"trace_id,omitempty"`
}

// ErrorBody is the inner object of ErrorEnvelope.
type ErrorBody struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	Details any    `json:"details,omitempty"`
}

// Standard error codes used by the SDK's auto-wired endpoints. Codes are
// SCREAMING_SNAKE_CASE per the spec.
const (
	CodeAgentNotLoaded = "AGENT_NOT_LOADED"
	CodeInvalidInput   = "INVALID_INPUT"
	CodeUnauthorized   = "UNAUTHORIZED"
	CodeForbidden      = "FORBIDDEN"
	CodeInternalError  = "INTERNAL_ERROR"
	CodeNotImplemented = "NOT_IMPLEMENTED"
	CodeUnspecified    = "UNSPECIFIED"
)

// SSE event names defined in the spec §4.3.
const (
	SseEventStep     = "step"
	SseEventToolCall = "tool_call"
	SseEventError    = "error"
	SseEventResult   = "result"
)

// SseTextDelta is the implicit `data:` event — token-by-token text streaming.
// New runtimes SHOULD emit Delta; SDK clients MUST accept either Delta or Text.
type SseTextDelta struct {
	Delta string `json:"delta,omitempty"`
	Text  string `json:"text,omitempty"`
}

// SseStepEvent is the named `event: step` payload — intermediate framework
// step.
type SseStepEvent struct {
	Description string `json:"description"`
	Result      any    `json:"result,omitempty"`
}

// SseToolCallEvent is the named `event: tool_call` payload — tool invocation
// delta.
type SseToolCallEvent struct {
	Name string         `json:"name"`
	Args map[string]any `json:"args,omitempty"`
}

// SseErrorEvent is the named `event: error` payload — emitted before stream
// terminates if the run fails.
type SseErrorEvent struct {
	Error string `json:"error"`
}

// SseResultEvent is the named `event: result` payload — final aggregated
// payload (CrewAI today).
type SseResultEvent struct {
	Output any `json:"output"`
}

// HeaderRuntimeContractVersion is the response header every agent emits.
const HeaderRuntimeContractVersion = "X-Runtime-Contract-Version"
