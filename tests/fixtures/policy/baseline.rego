# Rego equivalent of tests/conftest.py BASELINE_POLICY (deploy + RequireApproval).
# Run against a real OPA with: opa run --server --bundle tests/fixtures/policy
#   AGENTBREEDER_OPA_URL=http://127.0.0.1:8181 pytest tests/test_policy.py -k real_opa
package agentbreeder.deploy

import rego.v1

default allow := false

allow if {
	input.subject.properties.verified == true
	input.resource.properties.principal.kind == "agent"
}

obligations contains "require_approval" if input.resource.properties.risk_tier == "high"

decision := {"allow": allow, "obligations": obligations}
