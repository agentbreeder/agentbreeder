// Package server wires the auth, guardrail, A2A, MCP, OTel, and cost components
// into a small HTTP server set:
//
//   - inbound (default :8080) — public ingress, fronts the agent;
//   - localhost A2A (:9090) — /a2a/<peer> JSON-RPC client;
//   - localhost MCP (:9091) — /mcp/<server> JSON-RPC passthrough;
//   - localhost cost (:9092) — /cost POST endpoint for in-process emission.
package server

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/agentbreeder/agentbreeder/sidecar/internal/a2a"
	"github.com/agentbreeder/agentbreeder/sidecar/internal/auth"
	"github.com/agentbreeder/agentbreeder/sidecar/internal/config"
	"github.com/agentbreeder/agentbreeder/sidecar/internal/cost"
	"github.com/agentbreeder/agentbreeder/sidecar/internal/guardrails"
	"github.com/agentbreeder/agentbreeder/sidecar/internal/mcp"
	"github.com/agentbreeder/agentbreeder/sidecar/internal/otelx"
	"github.com/agentbreeder/agentbreeder/sidecar/internal/proxy"
)

// Server is the runtime container for all sidecar listeners.
type Server struct {
	Cfg    *config.Config
	Logger *slog.Logger
	Rules  *guardrails.Evaluator
	OTel   *otelx.Exporter
	Cost   *cost.Client
	A2A    *a2a.Client
	MCP    *mcp.Client
	Proxy  *proxy.Proxy

	servers []*http.Server
	mu      sync.Mutex
}

// New builds all the components from a fully-resolved config.
func New(cfg *config.Config, logger *slog.Logger) (*Server, error) {
	if logger == nil {
		logger = slog.Default()
	}
	rules, err := guardrails.New(extractGuardrails(cfg), true)
	if err != nil {
		return nil, err
	}

	prx, err := proxy.New(cfg.AgentURL, rules)
	if err != nil {
		return nil, err
	}

	a2aClient := a2a.NewClient(buildA2APeers(cfg.A2APeers, cfg.AuthToken), 30*time.Second)
	mcpClient := mcp.NewClient(cfg.MCPServers, 30*time.Second)
	otelExp := otelx.New(cfg.OTLPEndpoint, cfg.OTLPHeaders, cfg.AgentName)
	costClient := cost.NewClient(cfg.APIBaseURL, cfg.APIToken, 5*time.Second)

	return &Server{
		Cfg:    cfg,
		Logger: logger,
		Rules:  rules,
		OTel:   otelExp,
		Cost:   costClient,
		A2A:    a2aClient,
		MCP:    mcpClient,
		Proxy:  prx,
	}, nil
}

// extractGuardrails copies user-supplied rules out of cfg.
func extractGuardrails(cfg *config.Config) []config.GuardrailRule { return cfg.Guardrails }

func buildA2APeers(peers map[string]string, fallbackToken string) map[string]a2a.Peer {
	out := map[string]a2a.Peer{}
	for name, url := range peers {
		// Optional `name@token@url` triple: split on the first '@@' separator.
		parts := strings.SplitN(url, "@@", 2)
		if len(parts) == 2 {
			out[name] = a2a.Peer{URL: parts[1], Token: parts[0]}
		} else {
			out[name] = a2a.Peer{URL: url, Token: fallbackToken}
		}
	}
	return out
}

// InboundRouter builds the chi router for the public ingress port.
func (s *Server) InboundRouter() http.Handler {
	r := chi.NewRouter()
	r.Use(auth.Middleware(s.Cfg.AuthToken))
	r.Get("/health", s.handleHealth)
	r.Get("/openapi.json", s.handleOpenAPI)
	// Anything else — proxy to the agent.
	r.NotFound(s.Proxy.ServeHTTP)
	r.MethodNotAllowed(s.Proxy.ServeHTTP)
	r.HandleFunc("/*", s.Proxy.ServeHTTP)
	return r
}

// LocalRouter builds the chi router for localhost-only helper endpoints.
// Mounts /a2a, /mcp, and /cost on a single port (defaults to 9090) and the
// caller can split per-port if desired by calling InternalA2A/MCP/Cost handlers
// separately.
func (s *Server) LocalRouter() http.Handler {
	r := chi.NewRouter()
	r.Get("/health", s.handleHealth)
	r.Post("/a2a/{peer}", s.handleA2A)
	r.Post("/mcp/{server}", s.handleMCP)
	r.Post("/cost", s.handleCost)
	return r
}

// --- handlers ---------------------------------------------------------------

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	body := map[string]any{
		"status":        "ok",
		"sidecar":       "agentbreeder",
		"agent":         s.Cfg.AgentName,
		"agent_version": s.Cfg.AgentVersion,
		"agent_url":     s.Proxy.Target(),
		"guardrails":    s.Rules.Rules(),
		"otel_enabled":  s.OTel.Enabled(),
		"cost_enabled":  s.Cost.Enabled(),
		"a2a_peers":     keys(s.A2A.Peers),
		"mcp_servers":   keys(s.MCP.Servers),
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(body)
}

func (s *Server) handleOpenAPI(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write([]byte(openAPIDoc))
}

func (s *Server) handleA2A(w http.ResponseWriter, r *http.Request) {
	peer := chi.URLParam(r, "peer")
	body, err := io.ReadAll(r.Body)
	if err != nil {
		s.writeError(w, http.StatusBadRequest, err)
		return
	}
	defer r.Body.Close()

	var req struct {
		Method string         `json:"method"`
		Params map[string]any `json:"params"`
	}
	if err := json.Unmarshal(body, &req); err != nil {
		s.writeError(w, http.StatusBadRequest, err)
		return
	}
	if req.Method == "" {
		req.Method = a2a.MethodSend
	}

	resp, err := s.A2A.Send(r.Context(), peer, req.Method, req.Params)
	if err != nil {
		s.writeError(w, http.StatusBadGateway, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}

func (s *Server) handleMCP(w http.ResponseWriter, r *http.Request) {
	server := chi.URLParam(r, "server")
	body, err := io.ReadAll(r.Body)
	if err != nil {
		s.writeError(w, http.StatusBadRequest, err)
		return
	}
	defer r.Body.Close()

	respBody, ct, err := s.MCP.Forward(r.Context(), server, body)
	if err != nil {
		s.writeError(w, http.StatusBadGateway, err)
		return
	}
	w.Header().Set("Content-Type", ct)
	_, _ = w.Write(respBody)
}

func (s *Server) handleCost(w http.ResponseWriter, r *http.Request) {
	var ev cost.Event
	if err := json.NewDecoder(r.Body).Decode(&ev); err != nil {
		s.writeError(w, http.StatusBadRequest, err)
		return
	}
	if ev.Agent == "" {
		ev.Agent = s.Cfg.AgentName
	}
	if ev.AgentVersion == "" {
		ev.AgentVersion = s.Cfg.AgentVersion
	}
	if err := s.Cost.Send(r.Context(), ev); err != nil {
		s.Logger.Warn("cost emission failed", "err", err.Error())
		s.writeError(w, http.StatusBadGateway, err)
		return
	}
	// Emit a span best-effort so OTel sees cost events alongside latency spans.
	span := s.OTel.StartSpan("agent.cost")
	span.Attributes["model"] = ev.Model
	span.Attributes["input_tokens"] = ev.InputTokens
	span.Attributes["output_tokens"] = ev.OutputTokens
	span.Attributes["cost_usd"] = ev.CostUSD
	span.EndTime = time.Now()
	if err := s.OTel.Export(r.Context(), span); err != nil {
		s.Logger.Debug("otel export skipped", "err", err.Error())
	}
	w.WriteHeader(http.StatusAccepted)
	_, _ = w.Write([]byte(`{"status":"recorded"}`))
}

func (s *Server) writeError(w http.ResponseWriter, code int, err error) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
}

func keys[K comparable, V any](m map[K]V) []K {
	out := make([]K, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

// Run starts the inbound and local routers and blocks until ctx is cancelled.
func (s *Server) Run(ctx context.Context) error {
	s.mu.Lock()
	s.servers = []*http.Server{
		{Addr: s.Cfg.InboundAddr, Handler: s.InboundRouter(), ReadHeaderTimeout: 10 * time.Second},
		{Addr: s.Cfg.A2AAddr, Handler: s.LocalRouter(), ReadHeaderTimeout: 10 * time.Second},
	}
	s.mu.Unlock()

	errs := make(chan error, len(s.servers))
	for _, h := range s.servers {
		go func(h *http.Server) {
			s.Logger.Info("listening", "addr", h.Addr)
			if err := h.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
				errs <- err
			}
		}(h)
	}

	select {
	case <-ctx.Done():
		s.Shutdown()
		return nil
	case err := <-errs:
		s.Shutdown()
		return err
	}
}

// Shutdown gracefully stops every listener within a 5s window.
func (s *Server) Shutdown() {
	s.mu.Lock()
	defer s.mu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	for _, h := range s.servers {
		_ = h.Shutdown(ctx)
	}
	s.OTel.Close()
}

const openAPIDoc = `{
  "openapi": "3.1.0",
  "info": {
    "title": "AgentBreeder Sidecar",
    "version": "0.1.0",
    "description": "Sidecar exposes /health, /openapi.json, and proxies the agent. Localhost helpers: /a2a/{peer}, /mcp/{server}, /cost."
  },
  "paths": {
    "/health": { "get": { "summary": "Liveness probe" } },
    "/openapi.json": { "get": { "summary": "Self-describing schema" } },
    "/a2a/{peer}": { "post": { "summary": "Forward a JSON-RPC tasks/send to a remote A2A peer" } },
    "/mcp/{server}": { "post": { "summary": "Forward a JSON-RPC payload to a configured MCP server" } },
    "/cost": { "post": { "summary": "Record a cost/token event in costs + audit_log" } }
  }
}`
