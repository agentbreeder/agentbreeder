import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ACTIONS, actionReason, cancelledText, doneText, inUseConfirm, inUseNames,
} from "../../src/agentbreeder/center/static/lib/services.js";

test("the three actions in button order", () => {
  assert.deepEqual(ACTIONS.map(([action]) => action), ["start", "stop", "restart"]);
});

test("which actions apply in each state", () => {
  const running = { state: "running", health: "healthy" };
  assert.equal(actionReason(running, "start"), "It is already running.");
  assert.equal(actionReason(running, "stop"), null);
  assert.equal(actionReason(running, "restart"), null);
  const exited = { state: "exited", health: "" };
  assert.equal(actionReason(exited, "start"), null);
  assert.equal(actionReason(exited, "stop"), "It is not running.");
  assert.equal(actionReason(exited, "restart"), "It is not running.");
  for (const info of [null, {}, { state: "absent" }]) {
    for (const [action] of ACTIONS) assert.match(actionReason(info, action), /no container yet/);
  }
  // paused and restarting keep their bindings: they count as up.
  assert.equal(actionReason({ state: "restarting" }, "stop"), null);
});

test("the deployments named by SERVICE_IN_USE", () => {
  const message = "these deployments use the stack: local/northwind-support, cluster/orders; add --yes to stop litellm anyway";
  assert.deepEqual(inUseNames(message), ["local/northwind-support", "cluster/orders"]);
  assert.deepEqual(inUseNames("these deployments use the stack: local/a; add --yes to restart x anyway"), ["local/a"]);
  assert.deepEqual(inUseNames("something else"), []);
  assert.deepEqual(inUseNames(undefined), []);
});

test("the in-use confirm names the deployments and what they lose", () => {
  const one = inUseConfirm("stop", "litellm", "these deployments use the stack: local/support; add --yes to stop litellm anyway");
  assert.deepEqual(one, {
    title: "Stop litellm anyway?",
    text: "This deployment uses the stack: local/support. They cannot reach litellm until it is started again.",
    confirm: "Stop litellm",
  });
  const two = inUseConfirm("restart", "postgres", "these deployments use the stack: local/a, local/b; add --yes to restart postgres anyway");
  assert.equal(two.title, "Restart postgres anyway?");
  assert.match(two.text, /^These deployments use the stack: local\/a, local\/b\. They cannot reach postgres while it restarts\.$/);
  // An unexpected or truncated wording: no CLI advice (`--yes`) in the dialog.
  for (const message of ["in use by y", "these deployments use the stack: local/a, local/b; add --y", undefined]) {
    assert.equal(inUseConfirm("stop", "x", message).text, "Deployments use this stack. They cannot reach x until it is started again.");
  }
});

test("the sentence after an action", () => {
  assert.equal(doneText({ service: "litellm", action: "restart", state: "running", health: "starting" }), "litellm restarted: now running (starting). Its health check has not passed yet.");
  assert.equal(doneText({ service: "litellm", action: "stop", state: "exited", health: "" }), "litellm stopped: now exited.");
  assert.equal(doneText(null), "");
  assert.equal(cancelledText("stop", "litellm"), "Nothing was done: litellm was not stopped.");
});
