"""Remote PDPs: OPA (localhost or HTTPS REST) and generic AuthZEN 1.0 HTTP.

The operator configures the trusted endpoint and the expected policy revision. The adapter
obtains the evaluated revision from the PDP response and rejects missing or mismatched revisions;
a client-side digest is never treated as evidence of what the server evaluated.
"""

from __future__ import annotations

from agentbreeder.identity.transport import JsonTransport, Transport, TransportError, check_url
from agentbreeder.operator_config import OperatorConfigError, PolicySource, resolve_credential
from agentbreeder.policy import PolicyError, PolicyRequest, SourceResult


class _Remote:
    def __init__(self, source: PolicySource, transport: Transport | None = None, base=None):
        if not source.url or not source.revision:
            raise PolicyError(
                "POLICY_ERROR", "Remote PDP sources need a URL and expected revision"
            )
        try:
            check_url(source.url, allow_loopback_http=True)
            token = resolve_credential(source.token, base)
        except (TransportError, OperatorConfigError) as error:
            raise PolicyError("POLICY_ERROR", "Invalid remote PDP configuration") from error
        self.source = source
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.transport = transport or JsonTransport(allow_loopback_http=True)

    def _post(self, url: str, body: dict) -> dict:
        try:
            status, response = self.transport.request(
                "POST", url, headers=self.headers, json_body=body
            )
        except TransportError as error:
            raise PolicyError("POLICY_ERROR", "Policy decision point unavailable") from error
        if status != 200 or not isinstance(response, dict):
            raise PolicyError("POLICY_ERROR", f"Policy decision point returned HTTP {status}")
        return response

    def _check_revision(self, revision) -> str:
        if revision != self.source.revision:
            raise PolicyError(
                "POLICY_REVISION_MISMATCH", "PDP did not evaluate the expected policy revision"
            )
        return f"revision:{revision}"


class OpaEvaluator(_Remote):
    """POST /v1/data/<path>?provenance=true; result must be {allow: bool, obligations: [...]}."""

    def evaluate(self, request: PolicyRequest) -> SourceResult:
        if not self.source.path:
            raise PolicyError("POLICY_ERROR", "OPA sources need a decision path")
        url = f"{self.source.url.rstrip('/')}/v1/data/{self.source.path}?provenance=true"
        response = self._post(url, {"input": request.to_authzen()})
        provenance = response.get("provenance") or {}
        if self.source.bundle:
            revision = ((provenance.get("bundles") or {}).get(self.source.bundle) or {}).get(
                "revision"
            )
        else:
            revision = provenance.get("revision")
        evidence = self._check_revision(revision)
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("allow"), bool):
            raise PolicyError("POLICY_ERROR", "OPA result must contain a boolean allow")
        return SourceResult(result["allow"], _obligations(result.get("obligations")), evidence)


class AuthZenEvaluator(_Remote):
    """POST /access/v1/evaluation; revision is read from `context.policy_revision`."""

    def evaluate(self, request: PolicyRequest) -> SourceResult:
        url = f"{self.source.url.rstrip('/')}/access/v1/evaluation"
        response = self._post(url, request.to_authzen())
        context = response.get("context") or {}
        if not isinstance(context, dict):
            raise PolicyError("POLICY_ERROR", "AuthZEN context must be an object")
        evidence = self._check_revision(context.get("policy_revision"))
        if not isinstance(response.get("decision"), bool):
            raise PolicyError("POLICY_ERROR", "AuthZEN response must contain a boolean decision")
        return SourceResult(
            response["decision"], _obligations(context.get("obligations")), evidence
        )


def _obligations(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise PolicyError("OBLIGATION_UNSUPPORTED", "Obligations must be a list")
    names = []
    for item in value:
        name = item.get("name") if isinstance(item, dict) else item
        if not isinstance(name, str):
            raise PolicyError("OBLIGATION_UNSUPPORTED", "Malformed obligation")
        names.append(name)
    return tuple(names)
