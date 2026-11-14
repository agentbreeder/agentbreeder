import assert from "node:assert/strict";
import { test } from "node:test";

import { DOCKER_EVERY, DOCKER_UNKNOWN, dockerChanged, dockerFrom } from "../../src/agentbreeder/center/static/lib/docker.js";

const up = { data: { reachable: true, message: "Client and engine are reachable." }, error: null };
const down = { data: { reachable: false, message: "Cannot reach the engine" }, error: null };

test("a check every 5 s keeps the blocker within the 15 s acceptance", () => {
  assert.ok(DOCKER_EVERY <= 5000);
});

test("reachable, down and unknown from the envelope", () => {
  assert.deepEqual(dockerFrom(up), { reachable: true, message: "Client and engine are reachable.", problem: null });
  assert.deepEqual(dockerFrom(down), { reachable: false, message: "Cannot reach the engine", problem: null });
  assert.equal(dockerFrom({ data: { reachable: null, message: "" }, error: null }).reachable, null);
  // Only a real boolean counts; a message that is not text is dropped.
  assert.equal(dockerFrom({ data: { reachable: "yes", message: 3 }, error: null }).reachable, null);
  assert.equal(dockerFrom({ data: { reachable: false, message: 3 }, error: null }).message, "");
});

test("a failed check keeps the last answer and records why", () => {
  const known = dockerFrom(down);
  const failed = dockerFrom({ data: null, error: { code: "TIMEOUT", message: "The command timed out" } }, known);
  assert.deepEqual(failed, { reachable: false, message: "Cannot reach the engine", problem: "The command timed out" });
  assert.equal(dockerFrom(null, DOCKER_UNKNOWN).reachable, null);
  assert.equal(dockerFrom({ data: "junk", error: null }, dockerFrom(up)).problem, "The Docker check failed.");
});

test("changed only when the blocker would appear or clear", () => {
  const isUp = dockerFrom(up);
  const isDown = dockerFrom(down);
  assert.equal(dockerChanged(DOCKER_UNKNOWN, isUp), false);
  assert.equal(dockerChanged(DOCKER_UNKNOWN, isDown), true);
  assert.equal(dockerChanged(isUp, isDown), true);
  assert.equal(dockerChanged(isDown, isUp), true);
  assert.equal(dockerChanged(isDown, { ...isDown, message: "other" }), false);
});
