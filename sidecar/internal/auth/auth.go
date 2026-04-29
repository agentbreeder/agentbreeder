// Package auth implements bearer-token validation for inbound requests.
//
// The sidecar fronts the agent and refuses any request that does not present
// `Authorization: Bearer <AGENT_AUTH_TOKEN>`. The /health and /openapi.json
// endpoints are exempted because deploy-time probes do not have the token.
package auth

import (
	"crypto/subtle"
	"net/http"
	"strings"
)

// Open paths bypass bearer-token validation. Keep this list short.
var openPaths = map[string]struct{}{
	"/health":       {},
	"/openapi.json": {},
}

// Middleware returns an HTTP middleware that enforces Bearer token auth.
// If the configured token is empty (test/local-only mode), all requests pass.
func Middleware(token string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if _, open := openPaths[r.URL.Path]; open {
				next.ServeHTTP(w, r)
				return
			}
			if token == "" {
				next.ServeHTTP(w, r)
				return
			}

			header := r.Header.Get("Authorization")
			const prefix = "Bearer "
			if !strings.HasPrefix(header, prefix) {
				writeUnauthorized(w)
				return
			}
			supplied := strings.TrimPrefix(header, prefix)
			// Constant-time comparison to deny timing attacks.
			if subtle.ConstantTimeCompare([]byte(supplied), []byte(token)) != 1 {
				writeUnauthorized(w)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

func writeUnauthorized(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("WWW-Authenticate", `Bearer realm="agentbreeder-sidecar"`)
	w.WriteHeader(http.StatusUnauthorized)
	_, _ = w.Write([]byte(`{"error":"unauthorized"}`))
}
