import assert from "node:assert/strict";
import { test } from "node:test";

import { RELAY_STATES, TOOL_STATES, grantRows, repinCommand, serverRows, shellWord, shortHash } from "../../src/agentbreeder/center/static/lib/mcp.js";

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
