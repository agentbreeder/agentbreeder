// Package a2a implements the JSON-RPC client that the agent calls at
// localhost:9090/a2a/<peer> to send tasks to remote agents.
//
// Wire format mirrors engine/a2a/protocol.py exactly so Python and Go peers
// interoperate without a shim:
//
//	{
//	  "jsonrpc": "2.0",
//	  "id": "<uuid>",
//	  "method": "tasks/send",
//	  "params": { "message": "...", "context": {...}, "task_id": "..." }
//	}
package a2a

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// Methods supported by the protocol (matches engine/a2a/protocol.py constants).
const (
	MethodSend      = "tasks/send"
	MethodGet       = "tasks/get"
	MethodCancel    = "tasks/cancel"
	MethodSubscribe = "tasks/sendSubscribe"
)

// Request is the JSON-RPC 2.0 request envelope.
type Request struct {
	JSONRPC string         `json:"jsonrpc"`
	ID      string         `json:"id"`
	Method  string         `json:"method"`
	Params  map[string]any `json:"params"`
}

// Error is the JSON-RPC 2.0 error object.
type Error struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

func (e *Error) Error() string {
	return fmt.Sprintf("a2a rpc error %d: %s", e.Code, e.Message)
}

// Response is the JSON-RPC 2.0 response envelope.
type Response struct {
	JSONRPC string `json:"jsonrpc"`
	ID      string `json:"id"`
	Result  any    `json:"result,omitempty"`
	Error   *Error `json:"error,omitempty"`
}

// Peer is the address of one remote A2A-enabled agent.
type Peer struct {
	URL   string // e.g. https://other-agent.run.app
	Token string // optional bearer token for the remote agent
}

// Client is a thin JSON-RPC client over HTTP.
type Client struct {
	HTTP    *http.Client
	Peers   map[string]Peer
	Timeout time.Duration
}

// NewClient builds a client with sane defaults.
func NewClient(peers map[string]Peer, timeout time.Duration) *Client {
	if timeout == 0 {
		timeout = 30 * time.Second
	}
	if peers == nil {
		peers = map[string]Peer{}
	}
	return &Client{
		HTTP:    &http.Client{Timeout: timeout},
		Peers:   peers,
		Timeout: timeout,
	}
}

// Send issues a JSON-RPC request to the named peer and returns the response.
// `peer` must exist in the client's peer map; otherwise the call returns an error
// without making any network attempt.
func (c *Client) Send(ctx context.Context, peer string, method string, params map[string]any) (*Response, error) {
	p, ok := c.Peers[peer]
	if !ok {
		return nil, fmt.Errorf("a2a peer %q not configured", peer)
	}
	return c.sendTo(ctx, p, method, params)
}

func (c *Client) sendTo(ctx context.Context, p Peer, method string, params map[string]any) (*Response, error) {
	if method == "" {
		return nil, errors.New("a2a: method is required")
	}
	if params == nil {
		params = map[string]any{}
	}

	body, err := json.Marshal(Request{
		JSONRPC: "2.0",
		ID:      newRequestID(),
		Method:  method,
		Params:  params,
	})
	if err != nil {
		return nil, fmt.Errorf("a2a: marshal: %w", err)
	}

	url := strings.TrimRight(p.URL, "/") + "/a2a"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("a2a: build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	if p.Token != "" {
		req.Header.Set("Authorization", "Bearer "+p.Token)
	}

	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, fmt.Errorf("a2a: post: %w", err)
	}
	defer resp.Body.Close()

	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("a2a: read response: %w", err)
	}
	if resp.StatusCode >= 500 {
		return nil, fmt.Errorf("a2a: peer returned %d: %s", resp.StatusCode, string(raw))
	}
	var out Response
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, fmt.Errorf("a2a: decode: %w (body=%s)", err, string(raw))
	}
	return &out, nil
}

// newRequestID returns a 32-hex-char id (matches uuid4 length / format-class).
func newRequestID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	// Force RFC4122 v4 markers so Python uuid.UUID() can parse it if needed.
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return hex.EncodeToString(b[:])
}
