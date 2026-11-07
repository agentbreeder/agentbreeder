# Deploy your own local image

Requires macOS or Linux, a running local Docker context, and a trusted stateless image. The service uses your local Docker permissions; it does not provide enterprise RBAC. Containers bind only to loopback and run as UID 65532 with a read-only filesystem. Image-declared volumes and secret injection are rejected.

```sh
docker build --iidfile /tmp/agentbreeder-sample-image examples/http-agent
.venv/bin/python - <<'PYTHON'
from pathlib import Path
image = Path('/tmp/agentbreeder-sample-image').read_text().strip()
Path('/tmp/agentbreeder-sample.yaml').write_text(
    f'schema_version: 1\nname: http-sample\nimage: {image}\ntarget: local\n'
)
PYTHON
.venv/bin/agentbreeder deploy /tmp/agentbreeder-sample.yaml --local
```

Copy the deployment ID and loopback endpoint from the output. Invoke `ENDPOINT/invoke` with the same JSON shown above. Repeating deploy with the same manifest resumes the same deployment. A changed manifest requires removing the earlier deployment first; updates and rollback are later work.

```sh
.venv/bin/agentbreeder status DEPLOYMENT_ID --json
.venv/bin/agentbreeder remove DEPLOYMENT_ID --local
```

State and event history persist in `~/.agentbreeder/local` (override with `--state-dir`). Status is the last recorded observation, not a live health check. Failed workloads remain available for retry/removal; success is returned only after container and HTTP health verification. Errors never manufacture an endpoint. Keep the state directory while resources exist; deleting it loses ownership/recovery records. Docker administrators can bypass these local controls.

To run the real Docker lifecycle test (builds and removes a test container/image):

```sh
AGENTBREEDER_DOCKER_TEST=1 .venv/bin/python -m pytest tests/test_local_integration.py -q
```

