package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadDefaults(t *testing.T) {
	t.Setenv("AGENT_NAME", "demo")
	t.Setenv("AGENT_AUTH_TOKEN", "secret")
	cfg, err := Load("")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.InboundAddr != ":8080" {
		t.Errorf("default inbound addr: got %q", cfg.InboundAddr)
	}
	if cfg.AgentURL != "http://127.0.0.1:8081" {
		t.Errorf("default agent URL: got %q", cfg.AgentURL)
	}
	if cfg.A2AAddr != "127.0.0.1:9090" {
		t.Errorf("default a2a addr: got %q", cfg.A2AAddr)
	}
	if cfg.MCPAddr != "127.0.0.1:9091" {
		t.Errorf("default mcp addr: got %q", cfg.MCPAddr)
	}
	if cfg.CostAddr != "127.0.0.1:9092" {
		t.Errorf("default cost addr: got %q", cfg.CostAddr)
	}
}

func TestLoadEnvOverridesFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "sidecar.yaml")
	yaml := `
agent_name: file-agent
agent_url: http://from-file:1234
guardrails:
  - name: pii-ssn
    type: regex
    pattern: '\d{3}-\d{2}-\d{4}'
    action: redact
    replace: "[REDACTED]"
`
	if err := os.WriteFile(path, []byte(yaml), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AGENT_NAME", "env-agent")
	t.Setenv("AGENTBREEDER_SIDECAR_AGENT_URL", "http://from-env:5678")
	t.Setenv("AGENT_AUTH_TOKEN", "tok")

	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.AgentName != "env-agent" {
		t.Errorf("env should override file name: got %q", cfg.AgentName)
	}
	if cfg.AgentURL != "http://from-env:5678" {
		t.Errorf("env should override file url: got %q", cfg.AgentURL)
	}
	if len(cfg.Guardrails) != 1 || cfg.Guardrails[0].Name != "pii-ssn" {
		t.Errorf("guardrails not loaded from file: %+v", cfg.Guardrails)
	}
}

func TestLoadMissingFileIsOK(t *testing.T) {
	t.Setenv("AGENT_NAME", "demo")
	t.Setenv("AGENT_AUTH_TOKEN", "tok")
	cfg, err := Load("/this/does/not/exist.yaml")
	if err != nil {
		t.Fatalf("missing file should be ok, got: %v", err)
	}
	if cfg.AgentName != "demo" {
		t.Errorf("env value lost: %q", cfg.AgentName)
	}
}

func TestValidateRequiresAgentName(t *testing.T) {
	t.Setenv("AGENT_NAME", "")
	t.Setenv("AGENT_AUTH_TOKEN", "x")
	_, err := Load("")
	if err == nil {
		t.Fatal("expected validation error for empty agent name")
	}
}

func TestValidateRequiresAuthToken(t *testing.T) {
	t.Setenv("AGENT_NAME", "demo")
	os.Unsetenv("AGENT_AUTH_TOKEN")
	os.Unsetenv("AGENTBREEDER_SIDECAR_ALLOW_NO_AUTH")
	_, err := Load("")
	if err == nil {
		t.Fatal("expected validation error for missing auth token")
	}
}

func TestValidateAuthBypass(t *testing.T) {
	t.Setenv("AGENT_NAME", "demo")
	os.Unsetenv("AGENT_AUTH_TOKEN")
	t.Setenv("AGENTBREEDER_SIDECAR_ALLOW_NO_AUTH", "1")
	_, err := Load("")
	if err != nil {
		t.Fatalf("bypass should allow empty token, got: %v", err)
	}
}

func TestValidateGuardrailType(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "sidecar.yaml")
	if err := os.WriteFile(path, []byte("guardrails:\n  - name: foo\n    type: bogus\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AGENT_NAME", "demo")
	t.Setenv("AGENT_AUTH_TOKEN", "tok")
	if _, err := Load(path); err == nil {
		t.Fatal("expected error for bogus guardrail type")
	}
}

func TestValidateMCPServer(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "sidecar.yaml")
	if err := os.WriteFile(path, []byte("mcp_servers:\n  fs:\n    transport: stdio\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AGENT_NAME", "demo")
	t.Setenv("AGENT_AUTH_TOKEN", "tok")
	if _, err := Load(path); err == nil {
		t.Fatal("expected error: stdio mcp server with no command")
	}
}

func TestOTLPHeadersFromEnv(t *testing.T) {
	t.Setenv("AGENT_NAME", "demo")
	t.Setenv("AGENT_AUTH_TOKEN", "tok")
	t.Setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-team=eng, x-region=us")
	cfg, err := Load("")
	if err != nil {
		t.Fatal(err)
	}
	if cfg.OTLPHeaders["x-team"] != "eng" || cfg.OTLPHeaders["x-region"] != "us" {
		t.Errorf("OTLP headers not parsed: %+v", cfg.OTLPHeaders)
	}
}

func TestLoadInvalidYAMLErrors(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "sidecar.yaml")
	// Indentation mismatch produces a parse error.
	if err := os.WriteFile(path, []byte("guardrails:\n  - name: a\n type: regex\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AGENT_NAME", "demo")
	t.Setenv("AGENT_AUTH_TOKEN", "tok")
	if _, err := Load(path); err == nil {
		t.Fatal("expected parse error")
	}
}

func TestValidateMCPHTTPNeedsURL(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "sidecar.yaml")
	if err := os.WriteFile(path, []byte("mcp_servers:\n  docs:\n    transport: http\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AGENT_NAME", "demo")
	t.Setenv("AGENT_AUTH_TOKEN", "tok")
	if _, err := Load(path); err == nil {
		t.Fatal("expected error: http transport with no url")
	}
}

func TestValidateMCPMissingTransport(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "sidecar.yaml")
	if err := os.WriteFile(path, []byte("mcp_servers:\n  docs:\n    url: http://x\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AGENT_NAME", "demo")
	t.Setenv("AGENT_AUTH_TOKEN", "tok")
	if _, err := Load(path); err == nil {
		t.Fatal("expected error: missing transport")
	}
}

func TestMustParseIntFallback(t *testing.T) {
	if mustParseInt("", 7) != 7 {
		t.Errorf("empty should yield fallback")
	}
	if mustParseInt("not-a-number", 9) != 9 {
		t.Errorf("invalid should yield fallback")
	}
	if mustParseInt("42", 0) != 42 {
		t.Errorf("valid value parsed wrong")
	}
}

func TestIsDisabled(t *testing.T) {
	cases := map[string]bool{
		"":         false,
		"enabled":  false,
		"disabled": true,
		"off":      true,
		"0":        true,
		"false":    true,
		"DISABLED": true,
	}
	for v, want := range cases {
		t.Setenv("AGENTBREEDER_SIDECAR", v)
		if got := IsDisabled(); got != want {
			t.Errorf("IsDisabled(%q) = %v, want %v", v, got, want)
		}
	}
}
