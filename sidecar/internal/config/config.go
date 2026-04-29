// Package config loads sidecar configuration from environment variables and
// an optional YAML file mounted at /etc/agentbreeder/sidecar.yaml.
//
// Environment variables always win over file values so deployers can tweak
// behaviour without rebuilding the image. The YAML file is the place to ship
// guardrail rules and MCP/A2A peer maps that don't fit cleanly in env vars.
package config

import (
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"

	"gopkg.in/yaml.v3"
)

// DefaultConfigPath is where the deployer mounts the operator-supplied YAML.
const DefaultConfigPath = "/etc/agentbreeder/sidecar.yaml"

// Config is the fully-resolved sidecar runtime configuration.
type Config struct {
	// AgentName is the canonical agent identifier emitted in cost / trace events.
	AgentName string `yaml:"agent_name"`
	// AgentVersion is the agent semantic version. Optional.
	AgentVersion string `yaml:"agent_version"`

	// Listeners.
	InboundAddr string `yaml:"inbound_addr"` // public ingress, default :8080
	A2AAddr     string `yaml:"a2a_addr"`     // localhost-only, default 127.0.0.1:9090
	MCPAddr     string `yaml:"mcp_addr"`     // localhost-only, default 127.0.0.1:9091
	CostAddr    string `yaml:"cost_addr"`    // localhost-only, default 127.0.0.1:9092

	// Upstream agent target — sidecar proxies inbound requests here.
	AgentURL string `yaml:"agent_url"` // default http://127.0.0.1:8081

	// Auth.
	AuthToken string `yaml:"-"` // never serialised, sourced from env only

	// OpenTelemetry.
	OTLPEndpoint string            `yaml:"otlp_endpoint"`
	OTLPHeaders  map[string]string `yaml:"otlp_headers"`

	// Cost / audit emission target (the AgentBreeder API).
	APIBaseURL string `yaml:"api_base_url"`
	APIToken   string `yaml:"-"` // sourced from env only

	// Guardrails: ordered list of rules to apply on egress payloads.
	Guardrails []GuardrailRule `yaml:"guardrails"`

	// A2A peer map: peer name → URL.
	A2APeers map[string]string `yaml:"a2a_peers"`

	// MCP server map: server name → command (stdio) or URL (sse/http).
	MCPServers map[string]MCPServerSpec `yaml:"mcp_servers"`
}

// GuardrailRule is one regex-or-keyword rule loaded from the YAML file.
type GuardrailRule struct {
	Name    string `yaml:"name"`
	Type    string `yaml:"type"`    // "regex" | "keyword"
	Pattern string `yaml:"pattern"` // regex source, or comma-separated keywords
	Action  string `yaml:"action"`  // "block" | "redact" | "warn", default "redact"
	Replace string `yaml:"replace"` // replacement string when action == "redact"
}

// MCPServerSpec describes how to reach one upstream MCP server.
type MCPServerSpec struct {
	Transport string   `yaml:"transport"` // "stdio" | "http" | "sse"
	URL       string   `yaml:"url"`       // for http / sse
	Command   string   `yaml:"command"`   // for stdio
	Args      []string `yaml:"args"`
}

// Defaults are applied to any zero-value field after env + file are read.
func (c *Config) applyDefaults() {
	if c.InboundAddr == "" {
		c.InboundAddr = ":8080"
	}
	if c.A2AAddr == "" {
		c.A2AAddr = "127.0.0.1:9090"
	}
	if c.MCPAddr == "" {
		c.MCPAddr = "127.0.0.1:9091"
	}
	if c.CostAddr == "" {
		c.CostAddr = "127.0.0.1:9092"
	}
	if c.AgentURL == "" {
		c.AgentURL = "http://127.0.0.1:8081"
	}
	if c.A2APeers == nil {
		c.A2APeers = map[string]string{}
	}
	if c.MCPServers == nil {
		c.MCPServers = map[string]MCPServerSpec{}
	}
	if c.OTLPHeaders == nil {
		c.OTLPHeaders = map[string]string{}
	}
}

// Validate returns an error for any inconsistency the runtime cannot recover from.
func (c *Config) Validate() error {
	if c.AgentName == "" {
		return errors.New("agent_name is required (set AGENT_NAME)")
	}
	if c.AuthToken == "" && os.Getenv("AGENTBREEDER_SIDECAR_ALLOW_NO_AUTH") != "1" {
		return errors.New("AGENT_AUTH_TOKEN is required (set AGENTBREEDER_SIDECAR_ALLOW_NO_AUTH=1 to bypass for local dev)")
	}
	for i, rule := range c.Guardrails {
		if rule.Name == "" {
			return fmt.Errorf("guardrail rule #%d missing name", i)
		}
		if rule.Type != "regex" && rule.Type != "keyword" {
			return fmt.Errorf("guardrail %q has unknown type %q (regex|keyword)", rule.Name, rule.Type)
		}
	}
	for name, spec := range c.MCPServers {
		switch spec.Transport {
		case "stdio":
			if spec.Command == "" {
				return fmt.Errorf("mcp server %q has stdio transport but no command", name)
			}
		case "http", "sse":
			if spec.URL == "" {
				return fmt.Errorf("mcp server %q has %s transport but no url", name, spec.Transport)
			}
		case "":
			return fmt.Errorf("mcp server %q missing transport (stdio|http|sse)", name)
		default:
			return fmt.Errorf("mcp server %q has unknown transport %q", name, spec.Transport)
		}
	}
	return nil
}

// Load reads YAML at path (if it exists) and overlays env vars.
// Env vars override file values so a deployer can tweak the image without rebuilding.
func Load(path string) (*Config, error) {
	cfg := &Config{}
	if path != "" {
		raw, err := os.ReadFile(path) //nolint:gosec // path is operator-supplied
		switch {
		case err == nil:
			if err := yaml.Unmarshal(raw, cfg); err != nil {
				return nil, fmt.Errorf("parse %s: %w", path, err)
			}
		case os.IsNotExist(err):
			// optional, fall through with empty cfg
		default:
			return nil, fmt.Errorf("read %s: %w", path, err)
		}
	}
	cfg.overlayEnv()
	cfg.applyDefaults()
	return cfg, cfg.Validate()
}

// overlayEnv sets fields from env vars, overriding any file value.
func (c *Config) overlayEnv() {
	setIfEnv(&c.AgentName, "AGENT_NAME")
	setIfEnv(&c.AgentVersion, "AGENT_VERSION")
	setIfEnv(&c.InboundAddr, "AGENTBREEDER_SIDECAR_INBOUND_ADDR")
	setIfEnv(&c.A2AAddr, "AGENTBREEDER_SIDECAR_A2A_ADDR")
	setIfEnv(&c.MCPAddr, "AGENTBREEDER_SIDECAR_MCP_ADDR")
	setIfEnv(&c.CostAddr, "AGENTBREEDER_SIDECAR_COST_ADDR")
	setIfEnv(&c.AgentURL, "AGENTBREEDER_SIDECAR_AGENT_URL")
	setIfEnv(&c.OTLPEndpoint, "OTEL_EXPORTER_OTLP_ENDPOINT")
	setIfEnv(&c.APIBaseURL, "AGENTBREEDER_API_URL")

	// Secrets always come from env, never the YAML on disk.
	c.AuthToken = os.Getenv("AGENT_AUTH_TOKEN")
	c.APIToken = os.Getenv("AGENTBREEDER_API_TOKEN")

	// OTLP headers list: "k1=v1,k2=v2"
	if h := os.Getenv("OTEL_EXPORTER_OTLP_HEADERS"); h != "" {
		if c.OTLPHeaders == nil {
			c.OTLPHeaders = map[string]string{}
		}
		for _, kv := range strings.Split(h, ",") {
			parts := strings.SplitN(kv, "=", 2)
			if len(parts) == 2 {
				c.OTLPHeaders[strings.TrimSpace(parts[0])] = strings.TrimSpace(parts[1])
			}
		}
	}
}

func setIfEnv(dst *string, name string) {
	if v, ok := os.LookupEnv(name); ok && v != "" {
		*dst = v
	}
}

// IsDisabled returns true when AGENTBREEDER_SIDECAR=disabled is set; the
// sidecar process exits cleanly in that case so local dev runs the agent alone.
func IsDisabled() bool {
	v := strings.ToLower(os.Getenv("AGENTBREEDER_SIDECAR"))
	return v == "disabled" || v == "off" || v == "0" || v == "false"
}

// MustParseInt is a small helper used by tests — never panics in prod paths.
func mustParseInt(s string, fallback int) int {
	if s == "" {
		return fallback
	}
	n, err := strconv.Atoi(s)
	if err != nil {
		return fallback
	}
	return n
}

var _ = mustParseInt // silence unused-warning when re-organising
