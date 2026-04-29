package guardrails

import (
	"strings"
	"testing"

	"github.com/agentbreeder/agentbreeder/sidecar/internal/config"
)

func TestDefaultRulesRedactSSN(t *testing.T) {
	e, err := New(nil, true)
	if err != nil {
		t.Fatal(err)
	}
	d := e.Evaluate("My SSN is 123-45-6789 thanks")
	if !d.Modified {
		t.Fatal("expected SSN to be redacted")
	}
	if strings.Contains(d.Payload, "123-45-6789") {
		t.Errorf("payload still leaks SSN: %q", d.Payload)
	}
	if !strings.Contains(d.Payload, "[REDACTED-SSN]") {
		t.Errorf("payload missing redaction marker: %q", d.Payload)
	}
}

func TestDefaultRulesRedactEmail(t *testing.T) {
	e, _ := New(nil, true)
	d := e.Evaluate("Contact: alice@example.com please")
	if !d.Modified {
		t.Fatal("expected email redaction")
	}
	if strings.Contains(d.Payload, "alice@example.com") {
		t.Errorf("payload still has email: %q", d.Payload)
	}
}

func TestNoMatchPassesThrough(t *testing.T) {
	e, _ := New(nil, true)
	d := e.Evaluate("just a sentence")
	if d.Modified || d.Blocked {
		t.Errorf("clean text should pass: %+v", d)
	}
	if d.Payload != "just a sentence" {
		t.Errorf("payload corrupted: %q", d.Payload)
	}
}

func TestUserBlockRule(t *testing.T) {
	e, err := New([]config.GuardrailRule{{
		Name: "blocked-word", Type: "keyword",
		Pattern: "forbidden", Action: "block",
	}}, false)
	if err != nil {
		t.Fatal(err)
	}
	d := e.Evaluate("This is a forbidden message")
	if !d.Blocked {
		t.Fatal("expected block")
	}
	if d.RuleName != "blocked-word" {
		t.Errorf("rule name not propagated: %q", d.RuleName)
	}
}

func TestUserRegexRedact(t *testing.T) {
	e, err := New([]config.GuardrailRule{{
		Name: "phone", Type: "regex",
		Pattern: `\d{3}-\d{4}`, Action: "redact", Replace: "<phone>",
	}}, false)
	if err != nil {
		t.Fatal(err)
	}
	d := e.Evaluate("Call me at 555-1212 ok?")
	if !d.Modified {
		t.Fatal("expected modification")
	}
	if !strings.Contains(d.Payload, "<phone>") {
		t.Errorf("missing custom replacement: %q", d.Payload)
	}
}

func TestKeywordCaseInsensitive(t *testing.T) {
	e, _ := New([]config.GuardrailRule{{
		Name: "secret-kw", Type: "keyword",
		Pattern: "secret", Action: "redact", Replace: "***",
	}}, false)
	d := e.Evaluate("This is SECRET data")
	if !strings.Contains(d.Payload, "***") {
		t.Errorf("case-insensitive keyword failed: %q", d.Payload)
	}
}

func TestInvalidRegexErrors(t *testing.T) {
	_, err := New([]config.GuardrailRule{{
		Name: "bad", Type: "regex", Pattern: "[(",
	}}, false)
	if err == nil {
		t.Fatal("invalid regex should error")
	}
}

func TestUnknownActionErrors(t *testing.T) {
	_, err := New([]config.GuardrailRule{{
		Name: "bad", Type: "keyword", Pattern: "x", Action: "explode",
	}}, false)
	if err == nil {
		t.Fatal("unknown action should error")
	}
}

func TestBlockShortCircuits(t *testing.T) {
	e, err := New([]config.GuardrailRule{
		{Name: "first", Type: "keyword", Pattern: "stop", Action: "block"},
		{Name: "second", Type: "keyword", Pattern: "stop", Action: "redact", Replace: "<x>"},
	}, false)
	if err != nil {
		t.Fatal(err)
	}
	d := e.Evaluate("please stop here")
	if !d.Blocked {
		t.Fatal("expected block")
	}
	if len(d.Violations) != 1 {
		t.Errorf("block should not run subsequent rules, got %d violations", len(d.Violations))
	}
}

func TestMultipleViolationsRecorded(t *testing.T) {
	e, _ := New([]config.GuardrailRule{
		{Name: "r1", Type: "keyword", Pattern: "alpha", Action: "redact", Replace: "X"},
		{Name: "r2", Type: "keyword", Pattern: "beta", Action: "redact", Replace: "Y"},
	}, false)
	d := e.Evaluate("alpha and beta together")
	if len(d.Violations) != 2 {
		t.Errorf("expected 2 violations, got %d", len(d.Violations))
	}
}

func TestRulesIntrospection(t *testing.T) {
	e, _ := New(nil, true)
	rules := e.Rules()
	if len(rules) < 3 {
		t.Errorf("expected default rules, got %v", rules)
	}
}
