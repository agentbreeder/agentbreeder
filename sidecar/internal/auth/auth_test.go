package auth

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func newOK() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
}

func TestMiddlewareAcceptsCorrectToken(t *testing.T) {
	mw := Middleware("secret")(newOK())
	req := httptest.NewRequest(http.MethodPost, "/invoke", nil)
	req.Header.Set("Authorization", "Bearer secret")
	w := httptest.NewRecorder()
	mw.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("got %d, want 200", w.Code)
	}
}

func TestMiddlewareRejectsWrongToken(t *testing.T) {
	mw := Middleware("secret")(newOK())
	req := httptest.NewRequest(http.MethodPost, "/invoke", nil)
	req.Header.Set("Authorization", "Bearer wrong")
	w := httptest.NewRecorder()
	mw.ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("got %d, want 401", w.Code)
	}
}

func TestMiddlewareRejectsMissingHeader(t *testing.T) {
	mw := Middleware("secret")(newOK())
	req := httptest.NewRequest(http.MethodPost, "/invoke", nil)
	w := httptest.NewRecorder()
	mw.ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("got %d, want 401", w.Code)
	}
	if w.Header().Get("WWW-Authenticate") == "" {
		t.Errorf("expected WWW-Authenticate header on 401")
	}
}

func TestMiddlewareSkipsHealth(t *testing.T) {
	mw := Middleware("secret")(newOK())
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	mw.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("got %d, want 200", w.Code)
	}
}

func TestMiddlewareSkipsOpenAPI(t *testing.T) {
	mw := Middleware("secret")(newOK())
	req := httptest.NewRequest(http.MethodGet, "/openapi.json", nil)
	w := httptest.NewRecorder()
	mw.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("got %d, want 200", w.Code)
	}
}

func TestMiddlewareEmptyTokenIsPermissive(t *testing.T) {
	mw := Middleware("")(newOK())
	req := httptest.NewRequest(http.MethodPost, "/invoke", nil)
	w := httptest.NewRecorder()
	mw.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("empty token should accept request, got %d", w.Code)
	}
}

func TestMiddlewareWrongScheme(t *testing.T) {
	mw := Middleware("secret")(newOK())
	req := httptest.NewRequest(http.MethodPost, "/invoke", nil)
	req.Header.Set("Authorization", "Basic c2VjcmV0")
	w := httptest.NewRecorder()
	mw.ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("non-bearer scheme should be rejected, got %d", w.Code)
	}
}
