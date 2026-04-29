package agentbreeder

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Client talks to an AgentBreeder API instance — the central registry, not a
// deployed agent. Use [Server] for the agent side.
//
// The client is intentionally narrow: it covers the slice of the API that a
// Go-based agent (or a CI pipeline) needs — listing/registering agents,
// listing models, reading and writing secret references. Wider coverage is
// reserved for follow-up work; the canonical surface is the Python SDK.
type Client struct {
	BaseURL    string
	APIKey     string
	HTTPClient *http.Client
}

// ClientOption configures a Client.
type ClientOption func(*Client)

// WithHTTPClient swaps the default http.Client (10s timeout). Use this to
// inject test doubles or to attach an auth-rotating transport.
func WithHTTPClient(hc *http.Client) ClientOption {
	return func(c *Client) {
		if hc != nil {
			c.HTTPClient = hc
		}
	}
}

// NewClient constructs a registry client. baseURL is e.g.
// "https://api.agentbreeder.io" or "http://localhost:8000". apiKey is the
// caller's bearer token (issued by the central API).
func NewClient(baseURL, apiKey string, opts ...ClientOption) *Client {
	c := &Client{
		BaseURL:    strings.TrimRight(baseURL, "/"),
		APIKey:     apiKey,
		HTTPClient: &http.Client{Timeout: 10 * time.Second},
	}
	for _, opt := range opts {
		opt(c)
	}
	return c
}

// APIError is returned by every Client method when the API responds non-2xx.
// It exposes the HTTP status, the parsed error envelope (when present), and
// the raw body for debugging.
type APIError struct {
	Status int
	Body   []byte
	// Errors carries the platform error envelope when the response shape
	// matches { "errors": [...] } — see CLAUDE.md §API Conventions.
	Errors []ErrorBody
}

func (e *APIError) Error() string {
	if len(e.Errors) > 0 {
		return fmt.Sprintf("agentbreeder: api %d: %s — %s", e.Status, e.Errors[0].Code, e.Errors[0].Message)
	}
	return fmt.Sprintf("agentbreeder: api %d: %s", e.Status, string(e.Body))
}

// platformResponse is the top-level wrapper for the central API
// (data/meta/errors). See CLAUDE.md §API Conventions.
type platformResponse[T any] struct {
	Data   T              `json:"data"`
	Meta   map[string]any `json:"meta,omitempty"`
	Errors []ErrorBody    `json:"errors,omitempty"`
}

// AgentSummary is the registry-side projection of an agent record. It is
// deliberately partial; full schemas live in the Python SDK.
type AgentSummary struct {
	ID          string    `json:"id"`
	Name        string    `json:"name"`
	Version     string    `json:"version"`
	Team        string    `json:"team"`
	Framework   string    `json:"framework,omitempty"`
	Language    string    `json:"language,omitempty"`
	Description string    `json:"description,omitempty"`
	CreatedAt   time.Time `json:"created_at,omitempty"`
	UpdatedAt   time.Time `json:"updated_at,omitempty"`
}

// AgentRegistration is the body of POST /api/v1/agents.
type AgentRegistration struct {
	Name        string `json:"name"`
	Version     string `json:"version"`
	Team        string `json:"team"`
	Owner       string `json:"owner"`
	Framework   string `json:"framework,omitempty"`
	Language    string `json:"language,omitempty"`
	Description string `json:"description,omitempty"`
}

// ModelSummary is the projection of a registry model record.
type ModelSummary struct {
	ID         string  `json:"id"`
	Name       string  `json:"name"`
	Provider   string  `json:"provider"`
	InputCost  float64 `json:"input_cost_per_million,omitempty"`
	OutputCost float64 `json:"output_cost_per_million,omitempty"`
}

// Secret is the registry projection of a secret-manager reference. The
// SDK never sees secret material — only the reference name + backend.
type Secret struct {
	Name    string            `json:"name"`
	Backend string            `json:"backend"`
	Tags    map[string]string `json:"tags,omitempty"`
}

// ListAgents lists agents the caller can see.
func (c *Client) ListAgents(ctx context.Context) ([]AgentSummary, error) {
	var out platformResponse[[]AgentSummary]
	if err := c.do(ctx, http.MethodGet, "/api/v1/agents", nil, &out); err != nil {
		return nil, err
	}
	return out.Data, nil
}

// GetAgent fetches a single agent by name.
func (c *Client) GetAgent(ctx context.Context, name string) (*AgentSummary, error) {
	var out platformResponse[AgentSummary]
	path := fmt.Sprintf("/api/v1/agents/%s", url.PathEscape(name))
	if err := c.do(ctx, http.MethodGet, path, nil, &out); err != nil {
		return nil, err
	}
	return &out.Data, nil
}

// RegisterAgent creates or updates an agent in the registry. Conventionally
// invoked by `agentbreeder deploy` after a successful build.
func (c *Client) RegisterAgent(ctx context.Context, reg AgentRegistration) (*AgentSummary, error) {
	var out platformResponse[AgentSummary]
	if err := c.do(ctx, http.MethodPost, "/api/v1/agents", reg, &out); err != nil {
		return nil, err
	}
	return &out.Data, nil
}

// DeleteAgent soft-deletes (archives) an agent.
func (c *Client) DeleteAgent(ctx context.Context, name string) error {
	path := fmt.Sprintf("/api/v1/agents/%s", url.PathEscape(name))
	return c.do(ctx, http.MethodDelete, path, nil, nil)
}

// ListModels lists models registered with the central registry.
func (c *Client) ListModels(ctx context.Context) ([]ModelSummary, error) {
	var out platformResponse[[]ModelSummary]
	if err := c.do(ctx, http.MethodGet, "/api/v1/models", nil, &out); err != nil {
		return nil, err
	}
	return out.Data, nil
}

// ListSecrets lists registered secret references for the caller's team.
func (c *Client) ListSecrets(ctx context.Context) ([]Secret, error) {
	var out platformResponse[[]Secret]
	if err := c.do(ctx, http.MethodGet, "/api/v1/secrets", nil, &out); err != nil {
		return nil, err
	}
	return out.Data, nil
}

// GetSecret fetches metadata about a single secret by name. Material is
// never returned; the deployer fetches it directly from the secret backend.
func (c *Client) GetSecret(ctx context.Context, name string) (*Secret, error) {
	var out platformResponse[Secret]
	path := fmt.Sprintf("/api/v1/secrets/%s", url.PathEscape(name))
	if err := c.do(ctx, http.MethodGet, path, nil, &out); err != nil {
		return nil, err
	}
	return &out.Data, nil
}

// PutSecret registers or updates a secret reference. Backend is one of
// "env", "aws", "gcp", "vault" — see engine/secrets/.
func (c *Client) PutSecret(ctx context.Context, name, backend string, tags map[string]string) (*Secret, error) {
	body := Secret{Name: name, Backend: backend, Tags: tags}
	var out platformResponse[Secret]
	if err := c.do(ctx, http.MethodPut, "/api/v1/secrets/"+url.PathEscape(name), body, &out); err != nil {
		return nil, err
	}
	return &out.Data, nil
}

// do issues a request, JSON-encodes the optional body, and decodes the
// platform-envelope response into out (when non-nil). Non-2xx responses are
// returned as *APIError.
func (c *Client) do(ctx context.Context, method, path string, body, out any) error {
	if c.BaseURL == "" {
		return errors.New("agentbreeder: client base URL is empty")
	}

	var rdr io.Reader
	if body != nil {
		buf, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("agentbreeder: marshal request: %w", err)
		}
		rdr = bytes.NewReader(buf)
	}

	req, err := http.NewRequestWithContext(ctx, method, c.BaseURL+path, rdr)
	if err != nil {
		return fmt.Errorf("agentbreeder: build request: %w", err)
	}
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if c.APIKey != "" {
		req.Header.Set("Authorization", "Bearer "+c.APIKey)
	}
	req.Header.Set("User-Agent", "agentbreeder-go-sdk/"+Version)

	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return fmt.Errorf("agentbreeder: http: %w", err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)

	if resp.StatusCode >= 400 {
		apiErr := &APIError{Status: resp.StatusCode, Body: respBody}
		var env platformResponse[json.RawMessage]
		if jsonErr := json.Unmarshal(respBody, &env); jsonErr == nil {
			apiErr.Errors = env.Errors
		}
		return apiErr
	}

	if out == nil || len(respBody) == 0 {
		return nil
	}
	if err := json.Unmarshal(respBody, out); err != nil {
		return fmt.Errorf("agentbreeder: decode response: %w (body=%s)", err, string(respBody))
	}
	return nil
}
