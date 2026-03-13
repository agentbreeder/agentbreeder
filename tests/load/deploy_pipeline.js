/**
 * k6 load test — Deploy Pipeline (critical path)
 *
 * Tests the deploy endpoint under load:
 *   POST /api/v1/deploys        — trigger a deployment
 *   GET  /api/v1/deploys/{id}   — poll deploy status
 *
 * The deploy pipeline is the core product. This test verifies it:
 *   - accepts concurrent deploy requests without errors
 *   - responds to status polls quickly
 *   - degrades gracefully under spike load
 *
 * Run:
 *   k6 run tests/load/deploy_pipeline.js
 *   k6 run --vus 20 --duration 60s tests/load/deploy_pipeline.js
 *
 * Thresholds:
 *   p95 deploy submission < 2000ms (deploy kick-off, not completion)
 *   p95 status poll < 200ms
 *   error rate < 0.5%
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend, Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const AUTH_TOKEN = __ENV.AUTH_TOKEN || "test-token";

export const options = {
  stages: [
    { duration: "20s", target: 5 },    // ramp up slowly (deploys are expensive)
    { duration: "60s", target: 20 },   // sustain
    { duration: "20s", target: 40 },   // spike
    { duration: "20s", target: 0 },    // ramp down
  ],
  thresholds: {
    http_req_failed: ["rate<0.005"],                                  // < 0.5% errors
    "http_req_duration{endpoint:deploy}": ["p(95)<2000"],             // deploy kick-off < 2s
    "http_req_duration{endpoint:deploy_status}": ["p(95)<200"],       // status poll < 200ms
  },
};

const errorRate = new Rate("errors");
const deployDuration = new Trend("deploy_submit_time");
const statusDuration = new Trend("deploy_status_time");
const deploysStarted = new Counter("deploys_started");
const deploysPolled = new Counter("deploys_polled");

const headers = {
  Authorization: `Bearer ${AUTH_TOKEN}`,
  "Content-Type": "application/json",
};

const AGENT_YAML_FIXTURE = JSON.stringify({
  yaml_content: `name: load-test-agent
version: "1.0.0"
team: loadtest
owner: k6@test.local
framework: custom
model:
  primary: claude-haiku-4-5
deploy:
  cloud: local
`,
  target: "local",
});

function submitDeploy() {
  const res = http.post(`${BASE_URL}/api/v1/deploys`, AGENT_YAML_FIXTURE, {
    headers,
    tags: { endpoint: "deploy" },
  });

  const ok = check(res, {
    "deploy submit: status 200 or 202": (r) => r.status === 200 || r.status === 202,
    "deploy submit: has job_id": (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.data?.job_id !== undefined || body.job_id !== undefined;
      } catch {
        return false;
      }
    },
    "deploy submit: response time < 2s": (r) => r.timings.duration < 2000,
  });

  errorRate.add(!ok);
  deployDuration.add(res.timings.duration);
  deploysStarted.add(1);

  if (ok && res.status === 200) {
    try {
      const body = JSON.parse(res.body);
      return body.data?.job_id || body.job_id;
    } catch {
      return null;
    }
  }
  return null;
}

function pollDeployStatus(jobId) {
  if (!jobId) return;

  const res = http.get(`${BASE_URL}/api/v1/deploys/${jobId}`, {
    headers,
    tags: { endpoint: "deploy_status" },
  });

  check(res, {
    "deploy status: status 200 or 404": (r) => r.status === 200 || r.status === 404,
    "deploy status: response time < 200ms": (r) => r.timings.duration < 200,
    "deploy status: has status field": (r) => {
      try {
        const body = JSON.parse(r.body);
        return (
          body.data?.status !== undefined ||
          body.status !== undefined
        );
      } catch {
        return false;
      }
    },
  });

  statusDuration.add(res.timings.duration);
  deploysPolled.add(1);
}

export default function () {
  const rand = Math.random();

  if (rand < 0.3) {
    // Submit a new deploy, then poll status once
    const jobId = submitDeploy();
    if (jobId) {
      sleep(0.5);
      pollDeployStatus(jobId);
    }
  } else {
    // Poll an existing deploy (simulates monitoring ongoing deploys)
    const fakeJobId = `job-${Math.floor(Math.random() * 100)}`;
    pollDeployStatus(fakeJobId);
  }

  sleep(1 + Math.random() * 2);
}

export function handleSummary(data) {
  return {
    "tests/load/results/deploy_pipeline_summary.json": JSON.stringify(data, null, 2),
  };
}
