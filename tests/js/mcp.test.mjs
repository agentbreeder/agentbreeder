import assert from "node:assert/strict";
import { test } from "node:test";

import { RELAY_STATES, TOOL_STATES, grantChoices, grantProblem, grantRows, grantWriteProblem, grantSteps, grantedTools, repinCommand, serverRows, shellWord, shortHash } from "../../src/agentbreeder/center/static/lib/mcp.js";

const PIN = `sha256:${"a".repeat(64)}`;
const OTHER = `sha256:${"b".repeat(64)}`;
const FILES = {
  stack: "gh-reader",
  servers: [
    {
      name: "github",
      ref: "mcp/github@1.0.0",
      relay: "mcp-github",
      locked: { ref: "mcp/github@1.0.0", origin: "built-in", upstream: "https://api.githubcopilot.com:443", token_url: null },
      lock_current: true,
      pins: { list_issues: OTHER, get_me: PIN },
    },
  ],
  agents: [
    { name: "gh-reader", grants: { github: ["get_me", "list_issues", "issue_read"] } },
    { name: "other", grants: {} },
  ],
};
const STATUS = {
  stack: "gh-reader-0123",
  services: { "mcp-github": { state: "running", health: "", host_ports: {} } },
  mcp: { github: { state: "ok", pinned: 2, changed: [], missing: [], unpinned: ["search_repositories"] } },
};

test("a pin is shown as 16 hex digits, like the CLI's grant text", () => {
  assert.equal(shortHash(PIN), "a".repeat(16));
  assert.equal(shortHash("md5:abc"), "");
  assert.equal(shortHash(null), "");
});

test("the repin command names the project and environment, quoted for a shell", () => {
  assert.equal(repinCommand("/p/gh", "dev"), "agentbreeder services up /p/gh --environment dev --repin");
  assert.equal(repinCommand("", ""), "agentbreeder services up DIR --environment ENV --repin");
  assert.equal(repinCommand("/p/my gh", "dev"), "agentbreeder services up '/p/my gh' --environment dev --repin");
  assert.equal(shellWord("/p/it's; rm -rf ~"), "'/p/it'\\''s; rm -rf ~'");
  assert.equal(shellWord("$(x)"), "'$(x)'");
});

test("servers join the lock's pins with the relay's status", () => {
  const [row] = serverRows(FILES, STATUS);
  assert.equal(row.name, "github");
  assert.equal(row.origin, "built-in");
  assert.equal(row.upstream, "https://api.githubcopilot.com:443");
  assert.equal(row.relayState, "running");
  assert.equal(row.state, "ok");
  assert.equal(row.stateText, RELAY_STATES.ok);
  assert.deepEqual(row.unpinned, ["search_repositories"]);
  assert.deepEqual(row.tools.map((tool) => [tool.name, tool.state]), [["get_me", "pinned"], ["list_issues", "pinned"]]);
  assert.equal(row.tools[0].hash, "a".repeat(16));
});

test("drift marks the changed and missing tools", () => {
  const status = { ...STATUS, mcp: { github: { state: "drift", changed: ["get_me"], missing: ["list_issues"], unpinned: [] } } };
  const [row] = serverRows(FILES, status);
  assert.equal(row.state, "drift");
  assert.deepEqual(row.tools.map((tool) => tool.stateText), [TOOL_STATES.changed, TOOL_STATES.missing]);
});

test("without an upstream check the pins are not claimed to match", () => {
  for (const status of [null, { services: {}, mcp: {} }, { ...STATUS, mcp: { github: { state: "unverified", error: "MCP_PROBE_FAILED" } } }]) {
    const [row] = serverRows(FILES, status);
    assert.ok(row.tools.every((tool) => tool.state === "unknown"));
  }
  const [row] = serverRows(FILES, { ...STATUS, mcp: { github: { state: "unverified", error: "MCP_PROBE_FAILED" } } });
  assert.equal(row.error, "MCP_PROBE_FAILED");
  assert.equal(row.stateText, RELAY_STATES.unverified);
});

test("without an mcp entry a running relay is not called absent", () => {
  const [running] = serverRows(FILES, { services: { "mcp-github": { state: "running" } }, mcp: {} });
  assert.equal(running.state, "");
  const [absent] = serverRows(FILES, { services: { "mcp-github": { state: "absent" } }, mcp: {} });
  assert.equal(absent.state, "absent");
});

test("untrusted keys never read Object.prototype", () => {
  const files = { servers: [{ name: "__proto__", relay: "constructor", pins: {} }], agents: [] };
  const [row] = serverRows(files, { services: {}, mcp: {} });
  assert.equal(row.relayState, "");
  assert.equal(row.state, "");
});

test("a relay that is not running", () => {
  const [row] = serverRows(FILES, { services: { "mcp-github": { state: "absent" } }, mcp: { github: { state: "absent" } } });
  assert.equal(row.relayState, "absent");
  assert.equal(row.stateText, RELAY_STATES.absent);
});

test("an unknown state from the CLI is not shown as a known one", () => {
  const [row] = serverRows(FILES, { services: {}, mcp: { github: { state: "__proto__" } } });
  assert.equal(row.state, "");
  assert.equal(row.stateText, "");
});

test("bad shapes give no rows rather than throwing", () => {
  assert.deepEqual(serverRows(null, null), []);
  assert.deepEqual(serverRows({ servers: [null, 3, "x"] }, null), []);
  assert.deepEqual(grantRows({ agents: [null], servers: "x" }), []);
  const [row] = serverRows({ servers: [{ name: "g", pins: [1, 2] }] }, { mcp: { g: { changed: "x" } } });
  assert.deepEqual(row.tools, []);
});

test("grants list each agent and mark tools not pinned yet", () => {
  assert.deepEqual(grantRows(FILES), [
    {
      agent: "gh-reader",
      server: "github",
      tools: [
        { name: "get_me", pinned: true },
        { name: "list_issues", pinned: true },
        { name: "issue_read", pinned: false },
      ],
    },
    { agent: "other", server: "", tools: [] },
  ]);
});

test("a relay name the log route can't take has no Logs link", () => {
  const long = `mcp-${"a".repeat(28)}`;
  const [short] = serverRows(FILES, STATUS);
  assert.equal(short.relayLogs, true);
  const [row] = serverRows({ servers: [{ ...FILES.servers[0], relay: long }] }, null);
  assert.equal(row.relayLogs, false);
  assert.equal(serverRows({ servers: [{ ...FILES.servers[0], relay: `mcp-${"a".repeat(27)}` }] }, null)[0].relayLogs, true);
});

// -- grant and revoke (#831 part 2) --

const DECLARED = [
  { name: "get_me", effect: "read" },
  { name: "issue_read", effect: "read" },
  { name: "list_issues", effect: "read" },
];

test("a grant offers the declared tools not granted yet", () => {
  assert.deepEqual(grantChoices("grant", DECLARED, ["get_me"]).map((tool) => tool.name), ["issue_read", "list_issues"]);
  assert.deepEqual(grantChoices("grant", null, []), []);
  assert.deepEqual(grantChoices("grant", [null, { name: 3 }], []), []);
});

test("a revoke offers the granted tools, also one no longer declared", () => {
  assert.deepEqual(grantChoices("revoke", DECLARED, ["get_me", "gone"]), [
    { name: "get_me", effect: "read" },
    { name: "gone", effect: "" },
  ]);
});

test("an agent's current grant on one server", () => {
  assert.deepEqual(grantedTools(FILES, "gh-reader", "github"), ["get_me", "list_issues", "issue_read"]);
  assert.deepEqual(grantedTools(FILES, "other", "github"), []);
  assert.deepEqual(grantedTools(FILES, "nobody", "github"), []);
  assert.deepEqual(grantedTools(FILES, "gh-reader", "__proto__"), []);
});

test("refusals a person can act on have fixed words", () => {
  for (const code of ["MCP_AGENT_UNKNOWN", "MCP_UNKNOWN", "MCP_TOOL_UNDECLARED", "MCP_TOOL_INVALID", "STACK_EDIT_AMBIGUOUS", "STACK_TOO_LARGE", "BASE_MISMATCH"]) {
    const shown = grantProblem({ code, message: "cli text" });
    assert.notEqual(shown.text, "cli text", code);
    assert.equal(shown.review, code === "BASE_MISMATCH");
  }
  assert.equal(grantProblem({ code: "WRITE_BUSY", message: "Held by a job" }).text, "Held by a job");
  assert.equal(grantProblem({ code: "toString", message: "m" }).text, "m");
  assert.equal(grantProblem(null), null);
});

test("next steps: remove a deployment holding a revoked tool, reconcile an unreadable file, then services up", () => {
  const summary = {
    in_use: [
      { deployment: "d".repeat(32), environment: "dev", tools: ["get_me"] },
      { deployment: null, environment: "dev", tools: ["get_me"] },
    ],
  };
  const steps = grantSteps(summary, "/p/my gh", "dev");
  assert.deepEqual(steps.map((step) => step.command), [
    `agentbreeder remove ${"d".repeat(32)} --local --stack '/p/my gh'`,
    "agentbreeder reconcile --stack '/p/my gh' --environment dev",
    "agentbreeder services up '/p/my gh' --environment dev",
  ]);
  assert.match(steps[0].text, /MCP_GRANT_IN_USE/);
  assert.deepEqual(grantSteps({ in_use: [] }, "/p", "dev").map((step) => step.command), ["agentbreeder services up /p --environment dev"]);
});

test("a write problem other than a refusal before writing may have changed the file", () => {
  for (const code of ["MCP_GRANT_INVALID", "TIMEOUT", "COMMAND_FAILED", "BAD_OUTPUT", undefined]) {
    const shown = grantWriteProblem({ code, message: "m" });
    assert.equal(shown.uncertain, true, String(code));
    assert.match(shown.text, /may have happened/);
  }
  for (const code of ["BASE_MISMATCH", "WRITE_BUSY", "MCP_TOOL_UNDECLARED", "STACK_INVALID", "ENTRY_UNSAFE"]) {
    assert.equal(grantWriteProblem({ code, message: "m" }).uncertain, false, code);
  }
  assert.equal(grantWriteProblem(null), null);
});
