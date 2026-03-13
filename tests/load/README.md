# Load Tests — Agent Garden

k6 load test scripts for the critical paths in Agent Garden. These enforce production SLOs:

| Script | Endpoint(s) | p95 SLO | Error SLO |
|--------|------------|---------|-----------|
| `agents_api.js` | GET /agents, /agents/{id}, /registry/search | 500ms | < 1% |
| `deploy_pipeline.js` | POST /deploys, GET /deploys/{id} | 2000ms / 200ms | < 0.5% |
| `orchestration_execute.js` | POST /orchestrations/{id}/execute | 5000ms | < 1% |

## Prerequisites

```bash
# Install k6 (macOS)
brew install k6

# Install k6 (Linux)
sudo apt-get install k6
```

## Running Tests

```bash
# Quick smoke test (5 VUs, 30s)
k6 run --vus 5 --duration 30s tests/load/agents_api.js

# Full load test with default stages
k6 run tests/load/agents_api.js
k6 run tests/load/deploy_pipeline.js
k6 run tests/load/orchestration_execute.js

# Against staging
k6 run --env BASE_URL=https://staging.agentgarden.io \
        --env AUTH_TOKEN=$STAGING_TOKEN \
        tests/load/agents_api.js

# All scripts sequentially
for f in tests/load/*.js; do k6 run "$f"; done
```

## Configuration

All scripts accept environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `BASE_URL` | `http://localhost:8000` | API base URL |
| `AUTH_TOKEN` | `test-token` | JWT bearer token |
| `ORCHESTRATION_ID` | `customer-support-pipeline` | Orchestration to test |

## Results

Summary JSON files are written to `tests/load/results/` after each run.

## CI Integration

Add to GitHub Actions (after integration tests pass):

```yaml
- name: Load test (smoke)
  run: k6 run --vus 5 --duration 30s tests/load/agents_api.js
  env:
    BASE_URL: http://localhost:8000
    AUTH_TOKEN: ${{ secrets.CI_TOKEN }}
```

## Thresholds

Each script enforces k6 thresholds that fail the run if SLOs are breached.
See the `options.thresholds` block in each script for the exact values.
