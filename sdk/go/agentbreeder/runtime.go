package agentbreeder

import (
	"context"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"sync/atomic"
	"time"

	"github.com/go-chi/chi/v5"
)

// InvokeFunc is the agent author's synchronous handler. The SDK calls it from
// POST /invoke (see spec §4.2). Implementations should:
//
//   - Honor ctx (request-scoped — the HTTP server cancels it on disconnect).
//   - Read InvokeRequest.Input via .AsString or .AsObject.
//   - Populate the response via SetOutput, optionally setting SessionID and
//     Metadata.
//
// Returning a non-nil error produces a 500 with an ErrorEnvelope. The agent
// MUST NOT populate the trace_id/tokens/cost_usd/model/latency_ms fields —
// those are sidecar-owned (Track J).
type InvokeFunc func(ctx context.Context, req InvokeRequest, resp *InvokeResponse) error

// StreamEvent is one SSE event emitted by a StreamFunc.
//
//   - When Name is empty, the event is rendered as an implicit `data: {...}`
//     (the conventional shape for token-by-token text streaming — typically
//     a SseTextDelta).
//   - When Name is set, the event is rendered as
//     `event: <name>\ndata: {...}\n\n`.
type StreamEvent struct {
	Name string
	Data any
}

// StreamFunc is the agent author's streaming handler. The SDK calls it from
// POST /stream (see spec §4.3). Send events on the supplied channel; the SDK
// handles SSE framing and the mandatory `data: [DONE]\n\n` terminator. Close
// the channel when the stream ends.
//
// The channel buffers up to 32 events; producers should not assume back-
// pressure semantics. If the function returns an error, an `event: error` is
// emitted before the terminator.
type StreamFunc func(ctx context.Context, req InvokeRequest, out chan<- StreamEvent) error

// ResumeFunc is the optional handler for POST /resume. Most runtimes do not
// implement HITL resumption; in that case, leave it nil and the SDK serves
// 501 NOT_IMPLEMENTED.
type ResumeFunc func(ctx context.Context, req ResumeRequest, resp *InvokeResponse) error

// Server is the contract-conforming HTTP handler scaffolding. Construct one
// with NewServer; mount it on any [http.Server].
type Server struct {
	opts options

	logger *slog.Logger
	loaded atomic.Bool

	invoke InvokeFunc
	stream StreamFunc
	resume ResumeFunc

	openapiBytes []byte
	cardBytes    []byte
}

type options struct {
	agentName       string
	agentVersion    string
	framework       string
	authToken       string
	openapiOverride []byte
	agentCard       *AgentCard
	logger          *slog.Logger
	streamBuffer    int
	streamHeartbeat time.Duration
	startedLoaded   bool
	stream          StreamFunc
	resume          ResumeFunc
}

// Option configures a Server.
type Option func(*options)

// WithName sets the agent name advertised in /health, /openapi.json, and the
// agent card. Defaults to the AGENT_NAME env var or "agent".
func WithName(name string) Option { return func(o *options) { o.agentName = name } }

// WithVersion sets the agent SemVer advertised on /health and the agent card.
// Defaults to AGENT_VERSION env var or "0.1.0".
func WithVersion(version string) Option { return func(o *options) { o.agentVersion = version } }

// WithFramework records an optional framework hint exposed on /health and
// the agent card (e.g. "anthropic-go", "custom").
func WithFramework(framework string) Option {
	return func(o *options) { o.framework = framework }
}

// WithAuthToken overrides the bearer token. Defaults to the AGENT_AUTH_TOKEN
// env var. Pass "" to disable auth (local dev).
func WithAuthToken(token string) Option {
	return func(o *options) { o.authToken = token }
}

// WithStream registers a streaming handler. Without it, POST /stream returns
// 501 NOT_IMPLEMENTED.
func WithStream(fn StreamFunc) Option {
	return func(o *options) { o.stream = fn }
}

// WithResume registers a /resume handler. Without it, POST /resume returns
// 501 NOT_IMPLEMENTED.
func WithResume(fn ResumeFunc) Option {
	return func(o *options) { o.resume = fn }
}

// WithOpenAPI overrides the served /openapi.json document.
func WithOpenAPI(doc []byte) Option {
	return func(o *options) { o.openapiOverride = doc }
}

// WithAgentCard sets the body served at /.well-known/agent.json.
func WithAgentCard(card AgentCard) Option {
	return func(o *options) { o.agentCard = &card }
}

// WithLogger swaps the default slog.Default() logger.
func WithLogger(logger *slog.Logger) Option {
	return func(o *options) { o.logger = logger }
}

// WithStreamBuffer sizes the per-request event channel. Defaults to 32.
func WithStreamBuffer(n int) Option {
	return func(o *options) { o.streamBuffer = n }
}

// WithLoadedAtStart marks the agent as ready immediately. Without it, the
// agent reports `status: "loading"` until [Server.MarkLoaded] is called —
// useful for agents that perform heavy startup work in a goroutine.
func WithLoadedAtStart() Option {
	return func(o *options) { o.startedLoaded = true }
}

//go:embed runtime-contract-v1.openapi.json
var defaultOpenAPI embed.FS

// NewServer wires up the runtime-contract endpoints around the supplied
// invoke handler. Pass nil for invoke during early scaffolding, but a nil
// handler will return 501 from /invoke.
//
// Stream and resume handlers are registered via [WithStream] and
// [WithResume].
func NewServer(invoke InvokeFunc, opts ...Option) *Server {
	o := options{
		agentName:     envOr("AGENT_NAME", "agent"),
		agentVersion:  envOr("AGENT_VERSION", "0.1.0"),
		framework:     os.Getenv("AGENT_FRAMEWORK"),
		authToken:     os.Getenv("AGENT_AUTH_TOKEN"),
		streamBuffer:  32,
		startedLoaded: true,
	}
	for _, opt := range opts {
		opt(&o)
	}
	if o.logger == nil {
		o.logger = slog.Default()
	}
	if o.streamBuffer <= 0 {
		o.streamBuffer = 32
	}

	s := &Server{
		opts:   o,
		logger: o.logger,
		invoke: invoke,
		stream: o.stream,
		resume: o.resume,
	}
	s.loaded.Store(o.startedLoaded)

	// Materialize the embedded OpenAPI spec.
	if len(o.openapiOverride) > 0 {
		s.openapiBytes = o.openapiOverride
	} else {
		b, err := defaultOpenAPI.ReadFile("runtime-contract-v1.openapi.json")
		if err == nil {
			s.openapiBytes = b
		} else {
			// Fallback to a minimal stub so /openapi.json never 500s.
			s.openapiBytes = []byte(`{"openapi":"3.1.0","info":{"title":"AgentBreeder Agent","version":"1","x-agentbreeder-runtime-contract":"1"},"paths":{}}`)
		}
	}

	// Default agent card if none supplied.
	if o.agentCard == nil {
		o.agentCard = &AgentCard{
			Name:      o.agentName,
			Version:   o.agentVersion,
			Framework: o.framework,
			Protocol:  "a2a-v1",
			Endpoints: map[string]string{
				"invoke": "/invoke",
				"stream": "/stream",
				"health": "/health",
			},
		}
	}
	cardBytes, _ := json.Marshal(o.agentCard)
	s.cardBytes = cardBytes
	s.opts.agentCard = o.agentCard

	return s
}

// MarkLoaded transitions the agent from "loading" to "healthy". Idempotent.
func (s *Server) MarkLoaded() { s.loaded.Store(true) }

// MarkUnloaded transitions the agent back to "loading". Useful for agents
// that need to reload state.
func (s *Server) MarkUnloaded() { s.loaded.Store(false) }

// Loaded reports the agent's readiness state.
func (s *Server) Loaded() bool { return s.loaded.Load() }

// Handler returns an [http.Handler] mounting the contract endpoints. The
// returned handler is suitable for use directly with [http.ListenAndServe]
// or for embedding inside a parent chi router.
func (s *Server) Handler() http.Handler {
	r := chi.NewRouter()

	openPaths := map[string]struct{}{
		"/health":                 {},
		"/openapi.json":           {},
		"/.well-known/agent.json": {},
	}
	r.Use(authMiddleware(s.opts.authToken, openPaths))
	r.Use(versionHeaderMiddleware)

	r.Get("/health", s.handleHealth)
	r.Get("/openapi.json", s.handleOpenAPI)
	r.Get("/.well-known/agent.json", s.handleAgentCard)
	r.Post("/invoke", s.handleInvoke)
	r.Post("/stream", s.handleStream)
	r.Post("/resume", s.handleResume)

	return r
}

// ListenAndServe is a convenience that binds Handler() to addr and serves
// until ctx is cancelled. addr defaults to ":8080" when empty.
func (s *Server) ListenAndServe(ctx context.Context, addr string) error {
	if addr == "" {
		addr = ":8080"
	}
	srv := &http.Server{
		Addr:              addr,
		Handler:           s.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
	}
	errCh := make(chan error, 1)
	go func() {
		s.logger.Info("agentbreeder runtime listening", "addr", addr, "agent", s.opts.agentName)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- err
		}
		close(errCh)
	}()

	select {
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdownCtx)
		return nil
	case err, ok := <-errCh:
		if !ok {
			return nil
		}
		return err
	}
}

// versionHeaderMiddleware stamps the contract-version response header on
// every reply. Required by spec §5.5.
func versionHeaderMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set(HeaderRuntimeContractVersion, ContractVersion)
		next.ServeHTTP(w, r)
	})
}

// --- handlers ---------------------------------------------------------------

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	body := HealthResponse{
		AgentName: s.opts.agentName,
		Version:   s.opts.agentVersion,
		Framework: s.opts.framework,
	}
	if s.loaded.Load() {
		body.Status = StatusHealthy
	} else {
		body.Status = StatusLoading
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(body)
}

func (s *Server) handleOpenAPI(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write(s.openapiBytes)
}

func (s *Server) handleAgentCard(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write(s.cardBytes)
}

func (s *Server) handleInvoke(w http.ResponseWriter, r *http.Request) {
	if s.invoke == nil {
		writeError(w, http.StatusNotImplemented, CodeNotImplemented, "/invoke not implemented")
		return
	}
	if !s.loaded.Load() {
		w.Header().Set("Retry-After", "5")
		writeError(w, http.StatusServiceUnavailable, CodeAgentNotLoaded, "Agent not loaded yet")
		return
	}

	req, err := decodeInvokeRequest(r)
	if err != nil {
		writeError(w, http.StatusBadRequest, CodeInvalidInput, err.Error())
		return
	}

	resp := InvokeResponse{}
	if err := s.invoke(r.Context(), req, &resp); err != nil {
		s.logger.Error("invoke failed", "err", err.Error(), "agent", s.opts.agentName)
		writeError(w, http.StatusInternalServerError, CodeInternalError, err.Error())
		return
	}
	if len(resp.Output) == 0 {
		// Default to JSON null so the response is shape-valid.
		resp.Output = json.RawMessage("null")
	}
	if resp.SessionID == "" {
		resp.SessionID = req.SessionID
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}

func (s *Server) handleStream(w http.ResponseWriter, r *http.Request) {
	if s.stream == nil {
		writeError(w, http.StatusNotImplemented, CodeNotImplemented, "/stream not implemented")
		return
	}
	if !s.loaded.Load() {
		w.Header().Set("Retry-After", "5")
		writeError(w, http.StatusServiceUnavailable, CodeAgentNotLoaded, "Agent not loaded yet")
		return
	}

	req, err := decodeInvokeRequest(r)
	if err != nil {
		writeError(w, http.StatusBadRequest, CodeInvalidInput, err.Error())
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, CodeInternalError, "streaming unsupported by transport")
		return
	}

	// SSE response headers per spec §4.3.
	h := w.Header()
	h.Set("Content-Type", "text/event-stream")
	h.Set("Cache-Control", "no-cache")
	h.Set("Connection", "keep-alive")
	h.Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	out := make(chan StreamEvent, s.opts.streamBuffer)
	errCh := make(chan error, 1)
	go func() {
		defer close(out)
		errCh <- s.stream(r.Context(), req, out)
	}()

	for evt := range out {
		if err := writeSseEvent(w, evt); err != nil {
			s.logger.Warn("sse write failed", "err", err.Error())
			return
		}
		flusher.Flush()
	}

	if err := <-errCh; err != nil {
		_ = writeSseEvent(w, StreamEvent{Name: SseEventError, Data: SseErrorEvent{Error: err.Error()}})
		flusher.Flush()
	}

	// Stream terminator. Required by spec §4.3.
	_, _ = io.WriteString(w, "data: [DONE]\n\n")
	flusher.Flush()
}

func (s *Server) handleResume(w http.ResponseWriter, r *http.Request) {
	if s.resume == nil {
		writeError(w, http.StatusNotImplemented, CodeNotImplemented, "/resume not supported by this runtime")
		return
	}
	if !s.loaded.Load() {
		w.Header().Set("Retry-After", "5")
		writeError(w, http.StatusServiceUnavailable, CodeAgentNotLoaded, "Agent not loaded yet")
		return
	}

	var req ResumeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, CodeInvalidInput, err.Error())
		return
	}
	if req.ThreadID == "" {
		writeError(w, http.StatusUnprocessableEntity, CodeInvalidInput, "thread_id is required")
		return
	}

	resp := InvokeResponse{}
	if err := s.resume(r.Context(), req, &resp); err != nil {
		s.logger.Error("resume failed", "err", err.Error())
		writeError(w, http.StatusInternalServerError, CodeInternalError, err.Error())
		return
	}
	if len(resp.Output) == 0 {
		resp.Output = json.RawMessage("null")
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}

// --- helpers ---------------------------------------------------------------

func decodeInvokeRequest(r *http.Request) (InvokeRequest, error) {
	var req InvokeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		return InvokeRequest{}, fmt.Errorf("malformed JSON: %w", err)
	}
	if len(req.Input.Raw()) == 0 {
		return InvokeRequest{}, errors.New("`input` is required")
	}
	// Honor X-Session-Id header when body field is unset.
	if req.SessionID == "" {
		req.SessionID = r.Header.Get("X-Session-Id")
	}
	return req, nil
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(ErrorEnvelope{
		Error: ErrorBody{Code: code, Message: message},
	})
}

func writeSseEvent(w io.Writer, evt StreamEvent) error {
	payload, err := json.Marshal(evt.Data)
	if err != nil {
		return err
	}
	if evt.Name == "" {
		_, err = fmt.Fprintf(w, "data: %s\n\n", payload)
		return err
	}
	_, err = fmt.Fprintf(w, "event: %s\ndata: %s\n\n", evt.Name, payload)
	return err
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
