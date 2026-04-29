// Package otelx is a minimal OTel-shaped span exporter.
//
// We deliberately avoid importing go.opentelemetry.io/otel here:
//   - the heavy SDK adds ~5MB to the binary and a dozen indirect deps;
//   - v1 only needs trace export, no metrics, no propagation;
//   - keeping it tiny means we can ship a distroless image under 12MB.
//
// The exporter emits a JSON-encoded "OTLP-lite" payload to OTEL_EXPORTER_OTLP_ENDPOINT.
// The endpoint is expected to accept POST application/json with the OTLP/HTTP
// trace shape (collector accepts this when started with the otlphttp receiver).
//
// If no endpoint is configured the exporter becomes a no-op — callers can
// always emit spans without conditional checks.
package otelx

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"
)

// Span is the runtime representation of a single span the sidecar emits.
type Span struct {
	TraceID    string
	SpanID     string
	ParentID   string
	Name       string
	Kind       string // "internal" | "client" | "server"
	StartTime  time.Time
	EndTime    time.Time
	Attributes map[string]any
	StatusCode string // "OK" | "ERROR" | "UNSET"
	StatusDesc string
}

// Exporter sends spans to an OTLP/HTTP endpoint.
type Exporter struct {
	endpoint   string
	headers    map[string]string
	httpClient *http.Client
	serviceID  string

	mu     sync.Mutex
	closed bool
}

// New builds an exporter. Endpoint is the OTLP/HTTP base URL (e.g.
// http://collector:4318/v1/traces). When empty the exporter no-ops.
func New(endpoint string, headers map[string]string, serviceID string) *Exporter {
	if headers == nil {
		headers = map[string]string{}
	}
	return &Exporter{
		endpoint:   strings.TrimRight(endpoint, "/"),
		headers:    headers,
		httpClient: &http.Client{Timeout: 10 * time.Second},
		serviceID:  serviceID,
	}
}

// Enabled returns true when an endpoint is configured.
func (e *Exporter) Enabled() bool {
	return e.endpoint != ""
}

// StartSpan creates a new Span with a fresh trace + span ID.
// Useful for tests; production code typically reads trace IDs from the agent.
func (e *Exporter) StartSpan(name string) *Span {
	return &Span{
		TraceID:    NewTraceID(),
		SpanID:     NewSpanID(),
		Name:       name,
		Kind:       "internal",
		StartTime:  time.Now().UTC(),
		Attributes: map[string]any{},
		StatusCode: "UNSET",
	}
}

// Export sends one span. Returns nil if the exporter is disabled.
func (e *Exporter) Export(ctx context.Context, s *Span) error {
	if !e.Enabled() {
		return nil
	}
	e.mu.Lock()
	if e.closed {
		e.mu.Unlock()
		return fmt.Errorf("otelx: exporter closed")
	}
	e.mu.Unlock()

	payload := e.encode(s)
	url := e.endpoint
	// Allow callers to pass either the base or the full /v1/traces path.
	if !strings.HasSuffix(url, "/v1/traces") && !strings.Contains(url, "/v1/") {
		url = url + "/v1/traces"
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(payload))
	if err != nil {
		return fmt.Errorf("otelx: build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range e.headers {
		req.Header.Set(k, v)
	}
	resp, err := e.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("otelx: post: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		return fmt.Errorf("otelx: collector returned %d", resp.StatusCode)
	}
	return nil
}

// Close marks the exporter as closed. Subsequent Export calls return an error.
func (e *Exporter) Close() {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.closed = true
}

// encode shapes the span as the OTLP/HTTP JSON resource_spans wrapper.
// We keep this hand-rolled so the package stays dependency-free.
func (e *Exporter) encode(s *Span) []byte {
	attrs := otlpAttrs(s.Attributes)
	body := map[string]any{
		"resourceSpans": []map[string]any{{
			"resource": map[string]any{
				"attributes": otlpAttrs(map[string]any{
					"service.name": e.serviceID,
				}),
			},
			"scopeSpans": []map[string]any{{
				"scope": map[string]any{"name": "agentbreeder.sidecar"},
				"spans": []map[string]any{{
					"traceId":           s.TraceID,
					"spanId":            s.SpanID,
					"parentSpanId":      s.ParentID,
					"name":              s.Name,
					"kind":              spanKindCode(s.Kind),
					"startTimeUnixNano": s.StartTime.UnixNano(),
					"endTimeUnixNano":   s.endNano(),
					"attributes":        attrs,
					"status": map[string]any{
						"code":    statusCodeNum(s.StatusCode),
						"message": s.StatusDesc,
					},
				}},
			}},
		}},
	}
	raw, _ := json.Marshal(body)
	return raw
}

func (s *Span) endNano() int64 {
	if s.EndTime.IsZero() {
		return time.Now().UTC().UnixNano()
	}
	return s.EndTime.UnixNano()
}

func otlpAttrs(in map[string]any) []map[string]any {
	out := make([]map[string]any, 0, len(in))
	for k, v := range in {
		var value map[string]any
		switch x := v.(type) {
		case string:
			value = map[string]any{"stringValue": x}
		case bool:
			value = map[string]any{"boolValue": x}
		case int:
			value = map[string]any{"intValue": x}
		case int64:
			value = map[string]any{"intValue": x}
		case float64:
			value = map[string]any{"doubleValue": x}
		default:
			value = map[string]any{"stringValue": fmt.Sprintf("%v", x)}
		}
		out = append(out, map[string]any{"key": k, "value": value})
	}
	return out
}

func spanKindCode(kind string) int {
	switch kind {
	case "client":
		return 3
	case "server":
		return 2
	case "producer":
		return 4
	case "consumer":
		return 5
	default:
		return 1 // internal
	}
}

func statusCodeNum(code string) int {
	switch code {
	case "OK":
		return 1
	case "ERROR":
		return 2
	default:
		return 0
	}
}

// NewTraceID returns a 32-hex-char trace id.
func NewTraceID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}

// NewSpanID returns a 16-hex-char span id.
func NewSpanID() string {
	var b [8]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}
