import assert from "node:assert/strict";
import { test } from "node:test";

import {
  bytesText, diskAsOf, lastValue, latestRow, memoryText, pairText, percentText, rateSeries, rateText, serviceDisk, seriesFor,
  sparkLabel, sparkLines, statusChip, statusMismatch, sumSeries, totalsText, usageGap, usageProblem, valueSeries,
} from "../../src/agentbreeder/center/static/lib/usage.js";

const row = (name, extra = {}) => ({
  source: "service", name, deployment: null,
  cpu_percent: 1, memory_used: 1024, memory_limit: 4096, memory_percent: 25,
  network_rx: 0, network_tx: 0, block_read: 0, block_write: 0, pids: 3, ...extra,
});
const sample = (time, containers) => ({ time, containers });

test("bytes, rates and percentages in words", () => {
  assert.equal(bytesText(0), "0 B");
  assert.equal(bytesText(1023), "1023 B");
  assert.equal(bytesText(1536), "1.5 KiB");
  assert.equal(bytesText(512 * 1024 * 1024), "512 MiB");
  assert.equal(bytesText(1.5 * 1024 ** 3), "1.5 GiB");
  assert.equal(bytesText(null), "no value");
  assert.equal(bytesText(-1), "no value");
  assert.equal(bytesText(Number.NaN), "no value");
  assert.equal(rateText(2048), "2.0 KiB/s");
  assert.equal(rateText(null), "no value");
  assert.equal(percentText(2.345), "2.3%");
  assert.equal(percentText(150), "150.0%");
  assert.equal(percentText(undefined), "no value");
});

test("a service's series has a gap where it was not listed, and agents match by role", () => {
  const samples = [
    sample("2026-11-14T10:00:00Z", [row("litellm")]),
    sample("2026-11-14T10:00:02Z", [row("postgres")]),
    sample("not a time", [row("litellm")]),
    sample("2026-11-14T10:00:04Z", [row("litellm", { cpu_percent: 7 }), { ...row("x"), source: "agent", name: "litellm", deployment: "a".repeat(32) }]),
  ];
  const series = seriesFor(samples, "service", "litellm");
  assert.equal(series.length, 3);
  assert.equal(series[1].row, null);
  assert.equal(latestRow(series).cpu_percent, 7);
  assert.equal(latestRow(seriesFor(samples, "service", "postgres")), null);
  const agent = { source: "agent", name: "demo", deployment: "b".repeat(32), role: "egress", cpu_percent: 2 };
  const main = { source: "agent", name: "demo", deployment: "b".repeat(32), cpu_percent: 3 };
  const both = [sample("2026-11-14T10:00:00Z", [agent, main])];
  assert.equal(latestRow(seriesFor(both, "agent", "demo")).cpu_percent, 3);
  assert.equal(latestRow(seriesFor(both, "agent", "demo", "egress")).cpu_percent, 2);
  assert.deepEqual(seriesFor(null, "service", "x"), []);
  assert.equal(latestRow([]), null);
});

test("rates per second between samples; a counter that went down (a restart) is a gap", () => {
  const series = seriesFor([
    sample("2026-11-14T10:00:00Z", [row("a", { network_rx: 1000 })]),
    sample("2026-11-14T10:00:02Z", [row("a", { network_rx: 5000 })]),
    sample("2026-11-14T10:00:04Z", [row("a", { network_rx: 100 })]),
    sample("2026-11-14T10:00:06Z", []),
    sample("2026-11-14T10:00:08Z", [row("a", { network_rx: null })]),
  ], "service", "a");
  assert.deepEqual(rateSeries(series, "network_rx").map((p) => p.value), [null, 2000, null, null, null]);
  assert.deepEqual(valueSeries(series, "network_rx").map((p) => p.value), [1000, 5000, 100, null, null]);
  const a = [{ t: 1, value: 1 }, { t: 2, value: null }, { t: 3, value: 2 }];
  const b = [{ t: 1, value: 2 }, { t: 2, value: 5 }, { t: 3, value: 3 }];
  assert.deepEqual(sumSeries(a, b).map((p) => p.value), [3, null, 5]);
  assert.equal(lastValue(a), 2);
  assert.equal(lastValue([]), null);
});

test("sparkline points span the window, scale to the top, and split at gaps", () => {
  const end = Date.parse("2026-11-14T10:05:00Z");
  const points = [
    { t: end - 300000, value: 0 },
    { t: end - 150000, value: 10 },
    { t: end - 100000, value: null },
    { t: end, value: 5 },
  ];
  const lines = sparkLines(points, { window: 300, width: 100, height: 22 });
  // A single point after a gap draws as a short dash.
  assert.deepEqual(lines, ["0,21 50,1", "100,11 101.5,11"]);
  // An idle series stays at the bottom when the floor is above it.
  assert.deepEqual(sparkLines([{ t: 0, value: 0.1 }, { t: 1000, value: 0.1 }], { window: 1, width: 10, height: 22, floor: 10 }), ["0,20.8 10,20.8"]);
  // Points older than the window are left out.
  assert.deepEqual(sparkLines([{ t: 0, value: 1 }, { t: 400000, value: 1 }, { t: 700000, value: 1 }], { window: 300, width: 100, height: 22 }), ["0,1 100,1"]);
  assert.deepEqual(sparkLines([]), []);
});

test("a sparkline's words name its low, high and newest value", () => {
  const points = [{ t: 0, value: 1 }, { t: 1, value: null }, { t: 2, value: 4 }, { t: 3, value: 2 }];
  assert.equal(sparkLabel("litellm CPU", points, percentText), "litellm CPU, last 5 minutes: low 1.0%, high 4.0%, now 2.0%");
  assert.equal(sparkLabel("x", [{ t: 0, value: 1 }, { t: 1, value: null }], percentText, 60), "x, last 1 minute: low 1.0%, high 1.0%, now no value");
  assert.equal(sparkLabel("x", [], percentText), "x, last 5 minutes: no readings yet");
});

test("memory and IO in words", () => {
  assert.equal(memoryText(row("a", { memory_used: 120 * 1024 * 1024, memory_limit: 8 * 1024 ** 3 })), "120 MiB of 8.0 GiB");
  assert.equal(memoryText(row("a", { memory_limit: null })), "1.0 KiB");
  assert.equal(memoryText(row("a", { memory_used: null })), "no value");
  assert.equal(memoryText(null), "no value");
  assert.equal(pairText(1536, null, ["in", "out"]), "1.5 KiB/s in, no value out");
  assert.equal(totalsText(row("a", { network_rx: 2048, network_tx: 0 }), ["network_rx", "network_tx"], ["in", "out"]), "2.0 KiB in, 0 B out since it started");
  assert.equal(totalsText(null, ["a", "b"], ["x", "y"]), "");
});

test("status chips: a word for every state, with an icon and a tone", () => {
  const word = (state, health = "") => statusChip({ state, health });
  assert.deepEqual(word("running", "healthy"), { tone: "ok", icon: "✓", word: "Healthy" });
  assert.deepEqual(word("running"), { tone: "ok", icon: "✓", word: "Running" });
  assert.equal(word("running", "starting").word, "Starting");
  assert.equal(word("running", "unhealthy").tone, "error");
  assert.equal(word("restarting").word, "Restarting");
  assert.equal(word("paused").tone, "warn");
  assert.equal(word("removing").word, "Removing");
  assert.equal(word("dead").tone, "error");
  assert.equal(word("exited").word, "Stopped");
  assert.equal(word("created").word, "Created");
  assert.equal(word("absent").word, "Not created");
  assert.equal(statusChip(null).word, "Not created");
  assert.equal(word("odd\u001b").word, "odd\\x1b");
});

test("why a running service shows no figures", () => {
  assert.equal(usageGap(null, null), "Reading usage…");
  assert.equal(usageGap({ data: null, error: null }, null), "Reading usage…");
  assert.equal(usageGap({ data: { samples: [] }, error: null }, null), "Waiting for the first reading…");
  assert.equal(usageGap({ data: { samples: [sample("t", [])] }, error: null }, null), "Not in the latest reading.");
  assert.equal(usageGap({ data: null, error: { message: "x" } }, null), "No reading: see the note above.");
  assert.equal(usageGap({ data: null, error: null }, row("a")), null);
});

test("a failed feed's note says whether the figures are old", () => {
  assert.equal(usageProblem(null), null);
  assert.equal(usageProblem({ data: null, error: null }), null);
  assert.equal(usageProblem({ data: { samples: [] }, error: { message: "Docker went away." } }), "Docker went away. It is tried again in a few seconds.");
  assert.equal(usageProblem({ data: { samples: [sample("t", [])] }, error: {} }), "The usage command failed. The figures are from the last reading; it is tried again in a few seconds.");
});

test("status and the newest sample disagree: a signature per disagreement", () => {
  const up = (info) => info.state === "running";
  const services = { litellm: { state: "running" }, postgres: { state: "exited" } };
  assert.equal(statusMismatch(services, [], up), "");
  assert.equal(statusMismatch(null, [sample("t", [])], up), "");
  assert.equal(statusMismatch(services, [sample("t", [row("litellm")])], up), "");
  assert.equal(statusMismatch(services, [sample("t", [])], up), "litellm");
  assert.equal(statusMismatch(services, [sample("t", [row("litellm"), row("postgres"), row("new")])], up), "new,postgres");
  // Agent rows are not services.
  assert.equal(statusMismatch(services, [sample("t", [row("litellm"), { ...row("postgres"), source: "agent" }])], up), "");
});

test("the disk reading: a service's writable layer and when it was read", () => {
  const disk = { as_of: "2026-11-14T10:00:00Z", stale: false, volumes: [], containers: [{ source: "service", name: "postgres", deployment: null, size: 4096 }, { source: "agent", name: "litellm", deployment: "a".repeat(32), size: 1 }] };
  assert.equal(serviceDisk(disk, "postgres"), 4096);
  assert.equal(serviceDisk(disk, "litellm"), null);
  assert.equal(serviceDisk(null, "x"), null);
  assert.match(diskAsOf(disk, "en-GB"), /^as of \d\d:\d\d:\d\d$/);
  assert.match(diskAsOf({ ...disk, stale: true }, "en-GB"), /\(the last reading failed\)$/);
  assert.equal(diskAsOf({ ...disk, as_of: "garbage" }), "as of garbage");
  assert.equal(diskAsOf(null), "not read yet");
  assert.equal(diskAsOf({ as_of: null }), "not read yet");
});
