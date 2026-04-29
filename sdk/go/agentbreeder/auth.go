package agentbreeder

import (
	"crypto/subtle"
	"encoding/json"
	"net/http"
	"strings"
)

// authMiddleware enforces bearer-token auth on protected endpoints. See
// runtime-contract-v1.md §3 for the wire behavior.
//
// Behavior:
//   - If token is empty, all requests pass (local-dev mode).
//   - If the request path is in openPaths, the middleware no-ops.
//   - Otherwise, the Authorization header MUST be "Bearer <token>" with a
//     constant-time match. 401 if missing/malformed, 403 if mismatched.
//
// Mirrors sidecar/internal/auth/auth.go for behavioral consistency.
func authMiddleware(token string, openPaths map[string]struct{}) func(http.Handler) http.Handler {
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
				writeAuthError(w, http.StatusUnauthorized, CodeUnauthorized, "Missing bearer token")
				return
			}
			supplied := strings.TrimPrefix(header, prefix)
			if subtle.ConstantTimeCompare([]byte(supplied), []byte(token)) != 1 {
				writeAuthError(w, http.StatusForbidden, CodeForbidden, "Invalid bearer token")
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

func writeAuthError(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set(HeaderRuntimeContractVersion, ContractVersion)
	if status == http.StatusUnauthorized {
		w.Header().Set("WWW-Authenticate", `Bearer realm="agentbreeder-agent"`)
	}
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(ErrorEnvelope{
		Error: ErrorBody{Code: code, Message: message},
	})
}
