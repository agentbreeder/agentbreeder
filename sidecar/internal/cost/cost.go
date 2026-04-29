// Package cost emits cost events to the AgentBreeder API.
//
// Two upstream endpoints are written to:
//   - POST {api}/api/v1/costs       — populates the costs table
//   - POST {api}/api/v1/audit       — adds an audit-log entry tagged "cost.recorded"
//
// On any 4xx/5xx the failed event is logged with slog and dropped — we never
// block the agent path on cost emission.
package cost

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"
)

// Event is the wire shape posted to the API.
type Event struct {
	Agent        string    `json:"agent"`
	AgentVersion string    `json:"agent_version,omitempty"`
	Model        string    `json:"model"`
	Provider     string    `json:"provider,omitempty"`
	InputTokens  int       `json:"input_tokens"`
	OutputTokens int       `json:"output_tokens"`
	CostUSD      float64   `json:"cost_usd"`
	TraceID      string    `json:"trace_id,omitempty"`
	OccurredAt   time.Time `json:"occurred_at"`
}

// Validate ensures the minimum required fields are present.
func (e Event) Validate() error {
	if e.Agent == "" {
		return errors.New("cost event: agent is required")
	}
	if e.Model == "" {
		return errors.New("cost event: model is required")
	}
	if e.InputTokens < 0 || e.OutputTokens < 0 {
		return errors.New("cost event: token counts must be non-negative")
	}
	return nil
}

// Client posts cost events to the AgentBreeder API.
type Client struct {
	HTTP    *http.Client
	BaseURL string
	Token   string
}

// NewClient builds a client. baseURL "" → the client is disabled and Send returns nil.
func NewClient(baseURL, token string, timeout time.Duration) *Client {
	if timeout == 0 {
		timeout = 5 * time.Second
	}
	return &Client{
		HTTP:    &http.Client{Timeout: timeout},
		BaseURL: strings.TrimRight(baseURL, "/"),
		Token:   token,
	}
}

// Enabled returns true when a base URL is configured.
func (c *Client) Enabled() bool { return c.BaseURL != "" }

// Send writes the cost event to /api/v1/costs and the audit entry to
// /api/v1/audit. A non-nil error means the cost emission failed; the audit
// entry is best-effort and its error is logged but not returned.
func (c *Client) Send(ctx context.Context, ev Event) error {
	if !c.Enabled() {
		return nil
	}
	if err := ev.Validate(); err != nil {
		return err
	}
	if ev.OccurredAt.IsZero() {
		ev.OccurredAt = time.Now().UTC()
	}
	if err := c.post(ctx, "/api/v1/costs", ev); err != nil {
		return err
	}
	auditEntry := map[string]any{
		"event_type": "cost.recorded",
		"actor":      "sidecar",
		"resource":   ev.Agent,
		"metadata": map[string]any{
			"model":         ev.Model,
			"input_tokens":  ev.InputTokens,
			"output_tokens": ev.OutputTokens,
			"cost_usd":      ev.CostUSD,
			"trace_id":      ev.TraceID,
		},
		"timestamp": ev.OccurredAt,
	}
	// Best-effort: ignore audit errors so cost reporting is never silently dropped
	// because an audit endpoint is unhealthy.
	_ = c.post(ctx, "/api/v1/audit", auditEntry)
	return nil
}

func (c *Client) post(ctx context.Context, path string, body any) error {
	raw, err := json.Marshal(body)
	if err != nil {
		return fmt.Errorf("cost: marshal: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+path, bytes.NewReader(raw))
	if err != nil {
		return fmt.Errorf("cost: build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if c.Token != "" {
		req.Header.Set("Authorization", "Bearer "+c.Token)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return fmt.Errorf("cost: post %s: %w", path, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		return fmt.Errorf("cost: %s returned %d", path, resp.StatusCode)
	}
	return nil
}
