/**
 * k6 load test — Orchestration Execute (critical path)
 *
 * Tests the orchestration execution endpoint:
 *   POST /api/v1/orchestrations/{id}/execute  — run a multi-agent pipeline
 *   GET  /api/v1/orchestrations               — list orchestrations
 *
 * Run:
 *   k6 run tests/load/orchestration_execute.js
 *   k6 run --vus 30 --duration 60s tests/load/orchestration_execute.js
 *
 * Thresholds:
 *   p95 execute < 5000ms (agent calls are slow)
 *   p95 list < 300ms
 *   error rate < 1%
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const AUTH_TOKEN = __ENV.AUTH_TOKEN || "test-token";
const ORCHESTRATION_ID = __ENV.ORCHESTRATION_ID || "customer-support-pipeline";

export const options = {
  stages: [
    { duration: "20s", target: 5 },
    { duration: "60s", target: 30 },
    { duration: "20s", target: 0 },
  ],
  thresholds: {
    http_req_failed: ["rate<0.01"],
    "http_req_duration{endpoint:execute}": ["p(95)<5000"],
    "http_req_duration{endpoint:list_orchestrations}": ["p(95)<300"],
  },
};

const errorRate = new Rate("errors");
const executeDuration = new Trend("orchestration_execute_time");
const listDuration = new Trend("orchestration_list_time");

const headers = {
  Authorization: `Bearer ${AUTH_TOKEN}`,
  "Content-Type": "application/json",
};

const TEST_MESSAGES = [
  "I need help with my billing statement",
  "My account is locked and I can't log in",
  "Can you help me upgrade my plan?",
  "I want to cancel my subscription",
  "How do I reset my password?",
  "I was charged twice for the same order",
];

function executeOrchestration(orchId, message) {
  const payload = JSON.stringify({
    input_message: message,
    context: { user_id: `u-${Math.floor(Math.random() * 1000)}` },
  });

  const res = http.post(
    `${BASE_URL}/api/v1/orchestrations/${orchId}/execute`,
    payload,
    { headers, tags: { endpoint: "execute" }, timeout: "10s" },
  );

  const ok = check(res, {
    "execute: status 200": (r) => r.status === 200,
    "execute: has output": (r) => {
      try {
        const body = JSON.parse(r.body);
        return (
          body.data?.output !== undefined ||
          body.output !== undefined
        );
      } catch {
        return false;
      }
    },
  });

  errorRate.add(!ok);
  executeDuration.add(res.timings.duration);
}

function listOrchestrations() {
  const res = http.get(`${BASE_URL}/api/v1/orchestrations`, {
    headers,
    tags: { endpoint: "list_orchestrations" },
  });

  const ok = check(res, {
    "list orchestrations: status 200": (r) => r.status === 200,
    "list orchestrations: response time < 300ms": (r) => r.timings.duration < 300,
  });

  errorRate.add(!ok);
  listDuration.add(res.timings.duration);
}

export default function () {
  const rand = Math.random();

  if (rand < 0.7) {
    // Execute orchestration with a random message
    const message = TEST_MESSAGES[Math.floor(Math.random() * TEST_MESSAGES.length)];
    executeOrchestration(ORCHESTRATION_ID, message);
  } else {
    // List orchestrations
    listOrchestrations();
  }

  sleep(1 + Math.random() * 2);
}

export function handleSummary(data) {
  return {
    "tests/load/results/orchestration_execute_summary.json": JSON.stringify(data, null, 2),
  };
}
