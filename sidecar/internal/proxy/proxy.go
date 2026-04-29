// Package proxy fronts the agent on :8080 and forwards authorised, guardrail-
// approved traffic to the agent on :8081 (configurable).
//
// Egress payload is run through the guardrails evaluator. A hard "block"
// returns 403 to the caller; a "redact" rewrites the request body before
// forwarding. /health is always proxied unmodified so deploy probes work.
package proxy

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"

	"github.com/agentbreeder/agentbreeder/sidecar/internal/guardrails"
)

// Proxy wraps a reverse-proxy with guardrail egress checks.
type Proxy struct {
	target *url.URL
	rp     *httputil.ReverseProxy
	rules  *guardrails.Evaluator
}

// New builds a proxy targeting the given upstream URL.
func New(targetURL string, rules *guardrails.Evaluator) (*Proxy, error) {
	u, err := url.Parse(targetURL)
	if err != nil {
		return nil, fmt.Errorf("proxy: parse target: %w", err)
	}
	rp := httputil.NewSingleHostReverseProxy(u)
	rp.ErrorHandler = func(w http.ResponseWriter, _ *http.Request, err error) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadGateway)
		_, _ = w.Write([]byte(fmt.Sprintf(`{"error":"upstream agent unreachable: %s"}`, err.Error())))
	}
	return &Proxy{target: u, rp: rp, rules: rules}, nil
}

// ServeHTTP runs the request through guardrails (POST/PUT/PATCH only) then
// forwards to the agent.
func (p *Proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if p.rules != nil && hasBody(r.Method) {
		body, _ := io.ReadAll(r.Body)
		_ = r.Body.Close()
		if len(body) > 0 {
			payload := extractStrings(body)
			d := p.rules.Evaluate(payload)
			if d.Blocked {
				writeBlocked(w, d.RuleName, d.Match)
				return
			}
			if d.Modified {
				body = rewriteBody(body, payload, d.Payload)
			}
		}
		r.Body = io.NopCloser(bytes.NewReader(body))
		r.ContentLength = int64(len(body))
		r.Header.Set("Content-Length", fmt.Sprint(len(body)))
	}
	p.rp.ServeHTTP(w, r)
}

// Target returns the upstream URL (useful for /health introspection).
func (p *Proxy) Target() string { return p.target.String() }

func hasBody(method string) bool {
	switch strings.ToUpper(method) {
	case http.MethodPost, http.MethodPut, http.MethodPatch:
		return true
	default:
		return false
	}
}

// extractStrings pulls scannable text from a request body so guardrails can
// run against JSON payloads as easily as plain text.
//
// JSON: extract every string leaf (concatenated with newlines).
// Anything else: treat the body as one string.
func extractStrings(body []byte) string {
	trimmed := bytes.TrimSpace(body)
	if len(trimmed) == 0 {
		return ""
	}
	if trimmed[0] != '{' && trimmed[0] != '[' {
		return string(body)
	}
	var generic any
	if err := json.Unmarshal(trimmed, &generic); err != nil {
		return string(body)
	}
	var sb strings.Builder
	collectStrings(generic, &sb)
	return sb.String()
}

func collectStrings(v any, sb *strings.Builder) {
	switch x := v.(type) {
	case string:
		sb.WriteString(x)
		sb.WriteString("\n")
	case map[string]any:
		for _, val := range x {
			collectStrings(val, sb)
		}
	case []any:
		for _, val := range x {
			collectStrings(val, sb)
		}
	}
}

// rewriteBody attempts to substitute the redacted payload back into the body.
// For JSON we re-walk leaves and replace each redacted value; for plain text
// we just swap the body.
func rewriteBody(body []byte, original, redacted string) []byte {
	trimmed := bytes.TrimSpace(body)
	if len(trimmed) > 0 && (trimmed[0] == '{' || trimmed[0] == '[') {
		var generic any
		if err := json.Unmarshal(trimmed, &generic); err == nil {
			generic = redactStrings(generic, original, redacted)
			out, err := json.Marshal(generic)
			if err == nil {
				return out
			}
		}
	}
	return []byte(redacted)
}

// redactStrings walks generic JSON and replaces each string with its redacted
// counterpart. Because guardrails return a single redacted blob, we use a
// per-string find-and-replace map keyed off the line-broken original.
func redactStrings(v any, original, redacted string) any {
	origLines := strings.Split(strings.TrimRight(original, "\n"), "\n")
	redactedLines := strings.Split(strings.TrimRight(redacted, "\n"), "\n")
	mapping := map[string]string{}
	for i := 0; i < len(origLines) && i < len(redactedLines); i++ {
		if origLines[i] != redactedLines[i] {
			mapping[origLines[i]] = redactedLines[i]
		}
	}
	return walkReplace(v, mapping)
}

func walkReplace(v any, mapping map[string]string) any {
	switch x := v.(type) {
	case string:
		if rep, ok := mapping[x]; ok {
			return rep
		}
		return x
	case map[string]any:
		for k, val := range x {
			x[k] = walkReplace(val, mapping)
		}
		return x
	case []any:
		for i, val := range x {
			x[i] = walkReplace(val, mapping)
		}
		return x
	default:
		return v
	}
}

func writeBlocked(w http.ResponseWriter, rule, match string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusForbidden)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"error":      "request blocked by guardrail",
		"rule":       rule,
		"match":      match,
		"sidecar":    "agentbreeder",
		"action_url": "https://agentbreeder.io/docs/sidecar",
	})
}

// Suppress unused-import warning for context — kept available for future
// guardrail engines that need a deadline.
var _ = context.Background
