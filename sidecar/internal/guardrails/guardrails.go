// Package guardrails implements the egress-payload rule engine.
//
// Each rule has a matcher (regex or keyword set) and an action (block, redact,
// warn). The Evaluator runs all rules in declared order and returns the first
// blocking match (terminal) or a redacted payload (non-terminal).
//
// The default v1 PII rule catches US SSNs and credit-card-like numbers; users
// extend the list via /etc/agentbreeder/sidecar.yaml.
package guardrails

import (
	"errors"
	"fmt"
	"regexp"
	"strings"

	"github.com/agentbreeder/agentbreeder/sidecar/internal/config"
)

// Action is what a rule does on match.
type Action string

const (
	ActionBlock  Action = "block"
	ActionRedact Action = "redact"
	ActionWarn   Action = "warn"
)

// Rule is the runtime representation of a guardrail.
type Rule struct {
	Name     string
	Action   Action
	Replace  string
	matcher  matcher
	original config.GuardrailRule
}

// matcher is implemented by regex / keyword backends.
type matcher interface {
	// Match returns true plus the substring that matched, or false plus "".
	Match(s string) (bool, string)
	// Apply returns the input with all matches replaced by repl.
	Apply(s string, repl string) string
}

// Decision is the outcome of running an evaluator on a payload.
type Decision struct {
	Blocked    bool   // request must be denied
	Modified   bool   // payload was rewritten
	Payload    string // resulting payload (possibly redacted)
	RuleName   string // rule that triggered the decision
	Match      string // matched substring (for logs)
	Violations []Violation
}

// Violation is one rule hit collected for emission to the audit log.
type Violation struct {
	Rule   string
	Action Action
	Match  string
}

// Evaluator owns a set of compiled rules.
type Evaluator struct {
	rules []*Rule
}

// New compiles user rules plus the default PII rules into an Evaluator.
// includeDefaults=true ships the built-in PII detectors at the head of the list.
func New(userRules []config.GuardrailRule, includeDefaults bool) (*Evaluator, error) {
	var compiled []*Rule
	if includeDefaults {
		compiled = append(compiled, defaultPIIRules()...)
	}
	for _, r := range userRules {
		c, err := compileRule(r)
		if err != nil {
			return nil, fmt.Errorf("guardrail %q: %w", r.Name, err)
		}
		compiled = append(compiled, c)
	}
	return &Evaluator{rules: compiled}, nil
}

// Evaluate runs every rule against payload and returns the resulting decision.
// Rules execute in order; the first ActionBlock match short-circuits.
func (e *Evaluator) Evaluate(payload string) Decision {
	d := Decision{Payload: payload}
	for _, r := range e.rules {
		ok, match := r.matcher.Match(d.Payload)
		if !ok {
			continue
		}
		d.Violations = append(d.Violations, Violation{
			Rule:   r.Name,
			Action: r.Action,
			Match:  match,
		})
		switch r.Action {
		case ActionBlock:
			d.Blocked = true
			d.RuleName = r.Name
			d.Match = match
			return d
		case ActionRedact:
			d.Payload = r.matcher.Apply(d.Payload, redactedReplacement(r.Replace))
			d.Modified = true
			d.RuleName = r.Name
			d.Match = match
		case ActionWarn:
			// no payload mutation, just record the violation
			d.RuleName = r.Name
			d.Match = match
		}
	}
	return d
}

// Rules returns a snapshot of compiled rules (for /health introspection).
func (e *Evaluator) Rules() []string {
	out := make([]string, 0, len(e.rules))
	for _, r := range e.rules {
		out = append(out, r.Name)
	}
	return out
}

func compileRule(r config.GuardrailRule) (*Rule, error) {
	if r.Name == "" {
		return nil, errors.New("rule has empty name")
	}
	action := Action(r.Action)
	if action == "" {
		action = ActionRedact
	}
	switch action {
	case ActionBlock, ActionRedact, ActionWarn:
		// ok
	default:
		return nil, fmt.Errorf("unknown action %q", r.Action)
	}

	var m matcher
	switch r.Type {
	case "regex":
		re, err := regexp.Compile(r.Pattern)
		if err != nil {
			return nil, fmt.Errorf("invalid regex: %w", err)
		}
		m = &regexMatcher{re: re}
	case "keyword":
		kws := []string{}
		for _, k := range strings.Split(r.Pattern, ",") {
			k = strings.TrimSpace(k)
			if k != "" {
				kws = append(kws, k)
			}
		}
		if len(kws) == 0 {
			return nil, errors.New("keyword rule has empty pattern")
		}
		m = &keywordMatcher{keywords: kws}
	default:
		return nil, fmt.Errorf("unknown matcher type %q", r.Type)
	}
	return &Rule{
		Name:     r.Name,
		Action:   action,
		Replace:  r.Replace,
		matcher:  m,
		original: r,
	}, nil
}

func redactedReplacement(custom string) string {
	if custom != "" {
		return custom
	}
	return "[REDACTED]"
}

// --- matchers ---------------------------------------------------------------

type regexMatcher struct{ re *regexp.Regexp }

func (m *regexMatcher) Match(s string) (bool, string) {
	loc := m.re.FindStringIndex(s)
	if loc == nil {
		return false, ""
	}
	return true, s[loc[0]:loc[1]]
}

func (m *regexMatcher) Apply(s, repl string) string {
	return m.re.ReplaceAllString(s, repl)
}

type keywordMatcher struct{ keywords []string }

func (m *keywordMatcher) Match(s string) (bool, string) {
	lower := strings.ToLower(s)
	for _, k := range m.keywords {
		if strings.Contains(lower, strings.ToLower(k)) {
			return true, k
		}
	}
	return false, ""
}

func (m *keywordMatcher) Apply(s, repl string) string {
	for _, k := range m.keywords {
		// case-insensitive replace via regex
		re := regexp.MustCompile(`(?i)` + regexp.QuoteMeta(k))
		s = re.ReplaceAllString(s, repl)
	}
	return s
}

// defaultPIIRules ships in every sidecar.
func defaultPIIRules() []*Rule {
	return []*Rule{
		mustCompile(config.GuardrailRule{
			Name: "default-pii-ssn", Type: "regex",
			Pattern: `\b\d{3}-\d{2}-\d{4}\b`,
			Action:  string(ActionRedact), Replace: "[REDACTED-SSN]",
		}),
		mustCompile(config.GuardrailRule{
			Name: "default-pii-credit-card", Type: "regex",
			// Loose: 13-19 digits with optional spaces or dashes between groups.
			Pattern: `\b(?:\d[ -]?){13,19}\b`,
			Action:  string(ActionRedact), Replace: "[REDACTED-CC]",
		}),
		mustCompile(config.GuardrailRule{
			Name: "default-pii-email", Type: "regex",
			Pattern: `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b`,
			Action:  string(ActionRedact), Replace: "[REDACTED-EMAIL]",
		}),
	}
}

func mustCompile(r config.GuardrailRule) *Rule {
	c, err := compileRule(r)
	if err != nil {
		panic(fmt.Sprintf("default rule %q failed to compile: %v", r.Name, err))
	}
	return c
}
