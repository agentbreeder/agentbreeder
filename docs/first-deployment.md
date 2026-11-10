# Deploy your own local image

Requires macOS or Linux, a running local Docker context, and a trusted stateless image. The service uses your local Docker permissions; it does not provide enterprise RBAC. Containers bind only to loopback and run as UID 65532 with a read-only filesystem. Image-declared volumes and secret injection are rejected.

Every deploy passes the identity gate (`identity-gate.md`), so you need an operator config and an onboarded agent principal first. Without them the deploy fails closed (`CONTEXT_ENVIRONMENT_UNKNOWN`). For a local trial, use the static adapter as `src/agentbreeder/examples/http-agent/quickstart.py` does; real operators point a provider at their IdP instead. Both files must be private (mode 0600).

```sh
mkdir -p ~/agentbreeder-trial && cd ~/agentbreeder-trial
umask 077
cat > principals.yaml <<'YAML'
# Trial only: real operators verify agents in their identity provider.
principals:
  my-agent:
    kind: agent
    enabled: true
    environment: dev
    review_by: "2099-01-01"
    sponsors: [{id: me, name: me@example.com, active: true}]
YAML
cat > operator.yaml <<'YAML'
enforcement: enforce
default_environment: dev
environments:
  dev: {targets: [local]}
providers:
  trial: {type: static, authority: trial, file: principals.yaml}
YAML
export AGENTBREEDER_OPERATOR_CONFIG="$PWD/operator.yaml"
```

Build your image (here, the repository's HTTP sample), then write a `schema_version: 2` manifest that names the principal:

```sh
docker build --iidfile image-id PATH/TO/agentbreeder/src/agentbreeder/examples/http-agent
cat > agent.yaml <<YAML
schema_version: 2
name: http-sample
image: $(cat image-id)
target: local
identity: {provider: trial, ref: my-agent, environment: dev}
YAML
PATH/TO/.venv/bin/agentbreeder deploy agent.yaml --local --environment dev
```

Copy the deployment ID and loopback endpoint from the output, then invoke it:

```sh
curl -s -X POST ENDPOINT/invoke -d '{"message":"hello"}'
```

Repeating deploy with the same manifest resumes the same deployment. A changed manifest requires removing the earlier deployment first; updates and rollback are later work. Keep `AGENTBREEDER_OPERATOR_CONFIG` set for later commands, so status can re-verify the principal:

```sh
PATH/TO/.venv/bin/agentbreeder status DEPLOYMENT_ID --json
PATH/TO/.venv/bin/agentbreeder remove DEPLOYMENT_ID --local
```

State and event history persist in `~/.agentbreeder/local` (override with `--state-dir`). Status is the last recorded observation, not a live health check. Failed workloads remain available for retry/removal; success is returned only after container and HTTP health verification. Errors never manufacture an endpoint. Keep the state directory while resources exist; deleting it loses ownership/recovery records. Docker administrators can bypass these local controls.

To run the real Docker lifecycle test (builds and removes a test container/image):

```sh
AGENTBREEDER_DOCKER_TEST=1 .venv/bin/python -m pytest tests/test_local_integration.py -q
```
