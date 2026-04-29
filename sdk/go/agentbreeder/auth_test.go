package agentbreeder

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestAuthMiddleware_DisabledWhenTokenEmpty(t *testing.T) {
	t.Parallel()
	called := false
	h := authMiddleware("", nil)(http.HandlerFunc(func(_ http.ResponseWriter, _ *http.Request) {
		called = true
	}))

	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, httptest.NewRequest(http.MethodPost, "/invoke", nil))
	if !called {
		t.Fatal("expected next handler to be called when token is empty")
	}
	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}
}

func TestAuthMiddleware_OpenPathBypass(t *testing.T) {
	t.Parallel()
	open := map[string]struct{}{"/health": {}}
	called := false
	h := authMiddleware("secret", open)(http.HandlerFunc(func(_ http.ResponseWriter, _ *http.Request) {
		called = true
	}))

	rr := httptest.NewRecorder()
	// Open path: no auth header — must still pass.
	h.ServeHTTP(rr, httptest.NewRequest(http.MethodGet, "/health", nil))
	if !called || rr.Code != http.StatusOK {
		t.Fatalf("open path was blocked: called=%v status=%d", called, rr.Code)
	}
}

func TestAuthMiddleware_MissingHeaderIs401(t *testing.T) {
	t.Parallel()
	h := authMiddleware("s3cr3t", nil)(http.HandlerFunc(func(_ http.ResponseWriter, _ *http.Request) {
		t.Fatal("next handler must not run on auth failure")
	}))
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, httptest.NewRequest(http.MethodPost, "/invoke", nil))
	if rr.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", rr.Code)
	}
	if got := rr.Header().Get("WWW-Authenticate"); got == "" {
		t.Fatal("expected WWW-Authenticate header on 401")
	}
}

func TestAuthMiddleware_BadTokenIs403(t *testing.T) {
	t.Parallel()
	h := authMiddleware("s3cr3t", nil)(http.HandlerFunc(func(_ http.ResponseWriter, _ *http.Request) {
		t.Fatal("next handler must not run on auth failure")
	}))
	req := httptest.NewRequest(http.MethodPost, "/invoke", nil)
	req.Header.Set("Authorization", "Bearer wrong")
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	if rr.Code != http.StatusForbidden {
		t.Fatalf("expected 403, got %d", rr.Code)
	}
}

func TestAuthMiddleware_GoodTokenAllows(t *testing.T) {
	t.Parallel()
	called := false
	h := authMiddleware("s3cr3t", nil)(http.HandlerFunc(func(_ http.ResponseWriter, _ *http.Request) {
		called = true
	}))
	req := httptest.NewRequest(http.MethodPost, "/invoke", nil)
	req.Header.Set("Authorization", "Bearer s3cr3t")
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	if !called {
		t.Fatalf("good token rejected: status=%d", rr.Code)
	}
}

func TestAuthMiddleware_NotBearerSchemeIs401(t *testing.T) {
	t.Parallel()
	h := authMiddleware("s3cr3t", nil)(http.HandlerFunc(func(_ http.ResponseWriter, _ *http.Request) {
		t.Fatal("must not pass for non-Bearer")
	}))
	req := httptest.NewRequest(http.MethodPost, "/invoke", nil)
	req.Header.Set("Authorization", "Basic abcd")
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	if rr.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", rr.Code)
	}
}
