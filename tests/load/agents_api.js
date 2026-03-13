/**
 * k6 load test — Agent Registry API (critical path)
 *
 * Tests the most heavily used read paths:
 *   GET /api/v1/agents          — list agents (paginated)
 *   GET /api/v1/agents/{id}     — get agent detail
 *   GET /api/v1/registry/search — cross-entity search
 *
 * Run:
 *   k6 run tests/load/agents_api.js
 *   k6 run --vus 50 --duration 60s tests/load/agents_api.js
 *   k6 run --env BASE_URL=https://staging.agentgarden.io tests/load/agents_api.js
 *
 * Thresholds (SLOs):
 *   p95 response time < 500ms
 *   error rate < 1%
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const AUTH_TOKEN = __ENV.AUTH_TOKEN || "test-token";

export const options = {
  stages: [
    { duration: "30s", target: 10 },   // ramp up
    { duration: "60s", target: 50 },   // sustain
    { duration: "30s", target: 100 },  // spike
    { duration: "30s", target: 0 },    // ramp down
  ],
  thresholds: {
    http_req_failed: ["rate<0.01"],          // < 1% errors
    http_req_duration: ["p(95)<500"],        // p95 < 500ms
    "http_req_duration{endpoint:list}": ["p(95)<300"],
    "http_req_duration{endpoint:detail}": ["p(95)<200"],
    "http_req_duration{endpoint:search}": ["p(95)<500"],
  },
};

const errorRate = new Rate("errors");
const listDuration = new Trend("list_response_time");
const detailDuration = new Trend("detail_response_time");
const searchDuration = new Trend("search_response_time");

const headers = {
  Authorization: `Bearer ${AUTH_TOKEN}`,
  "Content-Type": "application/json",
};

// ---------------------------------------------------------------------------
// Scenario helpers
// ---------------------------------------------------------------------------

function testListAgents() {
  const res = http.get(`${BASE_URL}/api/v1/agents?page=1&page_size=25`, {
    headers,
    tags: { endpoint: "list" },
  });

  const ok = check(res, {
    "list agents: status 200": (r) => r.status === 200,
    "list agents: has data field": (r) => {
      try {
        return JSON.parse(r.body).data !== undefined;
      } catch {
        return false;
      }
    },
    "list agents: response time < 500ms": (r) => r.timings.duration < 500,
  });

  errorRate.add(!ok);
  listDuration.add(res.timings.duration);
  return res;
}

function testGetAgentDetail(agentId) {
  const res = http.get(`${BASE_URL}/api/v1/agents/${agentId}`, {
    headers,
    tags: { endpoint: "detail" },
  });

  const ok = check(res, {
    "agent detail: status 200 or 404": (r) => r.status === 200 || r.status === 404,
    "agent detail: response time < 200ms": (r) => r.timings.duration < 200,
  });

  errorRate.add(!ok);
  detailDuration.add(res.timings.duration);
  return res;
}

function testRegistrySearch(query) {
  const res = http.get(
    `${BASE_URL}/api/v1/registry/search?q=${encodeURIComponent(query)}&page_size=10`,
    {
      headers,
      tags: { endpoint: "search" },
    },
  );

  const ok = check(res, {
    "registry search: status 200": (r) => r.status === 200,
    "registry search: response time < 500ms": (r) => r.timings.duration < 500,
  });

  errorRate.add(!ok);
  searchDuration.add(res.timings.duration);
  return res;
}

// ---------------------------------------------------------------------------
// Main scenario
// ---------------------------------------------------------------------------

const AGENT_IDS = ["agent-001", "agent-002", "agent-003", "demo-agent"];
const SEARCH_QUERIES = ["support", "billing", "claude", "langraph", "research"];

export default function () {
  // Weighted scenario mix:
  // 60% list, 30% detail, 10% search
  const rand = Math.random();

  if (rand < 0.6) {
    testListAgents();
  } else if (rand < 0.9) {
    const agentId = AGENT_IDS[Math.floor(Math.random() * AGENT_IDS.length)];
    testGetAgentDetail(agentId);
  } else {
    const query = SEARCH_QUERIES[Math.floor(Math.random() * SEARCH_QUERIES.length)];
    testRegistrySearch(query);
  }

  sleep(0.5 + Math.random());
}

export function handleSummary(data) {
  return {
    "tests/load/results/agents_api_summary.json": JSON.stringify(data, null, 2),
  };
}
