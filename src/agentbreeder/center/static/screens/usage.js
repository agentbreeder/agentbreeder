// Service usage on screen (#822, M6 console half, part 4): Home's table of every registered
// project's services (status chip, host ports, CPU, memory, network and block IO with
// sparklines) and a service's Usage tab (one tile per figure, and the disk reading). The figures
// come from the project's usage ring (`GET /api/projects/ID/usage`), asked for every few seconds
// only while a service of that project is up and the page is visible.

import { portsText, serviceUp } from "../lib/format.js";
import { href } from "../lib/router.js";
import {
  WINDOW, bytesText, diskAsOf, lastValue, latestRow, memoryText, pairText, percentText, rateSeries, rateText, seriesFor, serviceDisk,
  statusMismatch, sumSeries, totalsText, usageGap, usageProblem, valueSeries,
} from "../lib/usage.js";
import { Card, Table, Text, useConsole } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { Spark, StatusChip, useUsage } from "../ui/usage.js";
import { html, useEffect, useRef } from "../ui/preact.js";

export const VM_NOTE = "CPU and memory are of the Docker VM (Docker Desktop runs containers in one): CPU is a share of one of its cores, so a busy container can pass 100%, and memory is out of what the VM lets the container use.";
const IO_NOTE = "Network and block IO are per second, averaged between readings, with the totals since the container started below.";
const INTERNAL = "Reached by agents on the stack network, not from this machine";
// Status re-reads for a status/usage disagreement: each is a session-refreshing read, so they
// are spaced and capped per page visit (a crash-looping container must not keep a page alive).
export const REREAD_GAP = 30000;
export const MAX_REREADS = 3;
const COLUMNS = ["Service", "Project", "Status", "Host ports", "CPU (of the Docker VM)", "Memory (of the Docker VM)", "Network", "Block IO"];

/** Home's services table across every registered project. */
export function HomeServices({ projects }) {
  return html`<${Card} title="Services">
    <${Table} label="Services across projects" columns=${COLUMNS}>
      ${projects.map((project) => html`<${ProjectRows} key=${project.id} project=${project} />`)}
    <//>
    <p class="muted small">${VM_NOTE} ${IO_NOTE} The sparklines show the last five minutes, kept in this console's memory only.</p>
  <//>`;
}

/**
 * One project's rows. Its status is read as other screens read it (again when a job of the project
 * ends); usage is polled while one of its services is up. When the newest sample and the status
 * disagree (a container stopped or started outside the console while another runs), status is
 * read once more for that disagreement, at most MAX_REREADS times per visit and REREAD_GAP apart:
 * status reads are not polls, so a page left open must not keep the session alive.
 */
function ProjectRows({ project }) {
  const [services, reload] = useView(`/api/projects/${project.id}/services`, project.id);
  const all = (services.data && services.data.services) || {};
  const names = Object.keys(all);
  const anyUp = names.some((name) => serviceUp(all[name]));
  const usage = useUsage(project.id, anyUp);
  const samples = (usage.data && usage.data.samples) || [];
  const window = (usage.data && usage.data.window) || WINDOW;
  const mismatch = statusMismatch(services.data && services.data.services, samples, serviceUp);
  const reread = useRef({ last: "", at: 0, count: 0 });
  useEffect(() => {
    const seen = reread.current;
    if (!mismatch || mismatch === seen.last || services.loading) return;
    if (seen.count >= MAX_REREADS || Date.now() - seen.at < REREAD_GAP) return;
    reread.current = { last: mismatch, at: Date.now(), count: seen.count + 1 };
    reload();
  }, [mismatch, services.loading]);
  const note = (text, tone = "muted") => html`<tr><td colspan=${COLUMNS.length} class=${tone}><strong><${Text} value=${project.name} />:</strong> <${Text} value=${text} /></td></tr>`;

  if (!services.data) {
    if (services.error) return note(services.error.message || "Could not read its services.");
    return note("Reading its services…");
  }
  if (!names.length) return html`<tr><td colspan=${COLUMNS.length} class="muted"><strong><${Text} value=${project.name} />:</strong> no services started yet. <${ScopedLink} project=${project} to="/services">Open its Services<//></td></tr>`;
  const problem = anyUp ? usageProblem(usage) : null;
  return html`
    ${problem ? note(problem, "notice-row") : null}
    ${names.map((name) => html`<${ServiceRow} key=${name} project=${project} name=${name} info=${all[name]} usage=${usage} samples=${samples} window=${window} />`)}
  `;
}

/** A link into a scoped screen of `project`: it sets the scope, then opens the screen. */
function ScopedLink({ project, to, children, label }) {
  const { setScope } = useConsole();
  return html`<a href=${`#${to}`} aria-label=${label} onClick=${(event) => {
    event.preventDefault();
    setScope(project.id, to);
  }}>${children}</a>`;
}

function serviceHref(name) {
  try {
    return href("/services/:name", { name }).slice(1);
  } catch {
    return null; // a name that cannot be a route parameter gets no link
  }
}

function ServiceRow({ project, name, info, usage, samples, window }) {
  const to = serviceHref(name);
  const up = serviceUp(info);
  const series = seriesFor(samples, "service", name);
  const row = up ? latestRow(series) : null;
  const gap = up ? usageGap(usage, row) : null;
  return html`<tr>
    <th scope="row">${to ? html`<${ScopedLink} project=${project} to=${to}><${Text} value=${name} /><//>` : html`<${Text} value=${name} />`}</th>
    <td><${Text} value=${project.name} /></td>
    <td><${StatusChip} info=${info} /></td>
    <td>${portsText(info) === "internal only" ? html`<span class="muted">internal only</span> <span class="muted small">(${INTERNAL.toLowerCase()})</span>` : html`<${Text} value=${portsText(info)} />`}</td>
    ${!up
      ? html`<td colspan="4" class="muted">Not running: no usage.</td>`
      : gap
        ? html`<td colspan="4" class="muted"><${Text} value=${gap} /></td>`
        : html`<${UsageCells} name=${name} series=${series} row=${row} window=${window} />`}
  </tr>`;
}

function UsageCells({ name, series, row, window }) {
  const cpu = valueSeries(series, "cpu_percent");
  const memory = valueSeries(series, "memory_used");
  const rx = rateSeries(series, "network_rx");
  const tx = rateSeries(series, "network_tx");
  const read = rateSeries(series, "block_read");
  const write = rateSeries(series, "block_write");
  return html`
    <td class="usage-cell"><span class="usage-value">${percentText(row.cpu_percent)}</span><${Spark} what=${`${name} CPU`} points=${cpu} format=${percentText} window=${window} floor=${5} /></td>
    <td class="usage-cell"><span class="usage-value">${memoryText(row)}</span><${Spark} what=${`${name} memory`} points=${memory} format=${bytesText} window=${window} /></td>
    <td class="usage-cell"><span class="usage-value">${pairText(lastValue(rx), lastValue(tx), ["in", "out"])}</span><${Spark} what=${`${name} network, in and out`} points=${sumSeries(rx, tx)} format=${rateText} window=${window} floor=${1024} /><span class="muted small usage-total">${totalsText(row, ["network_rx", "network_tx"], ["in", "out"])}</span></td>
    <td class="usage-cell"><span class="usage-value">${pairText(lastValue(read), lastValue(write), ["read", "written"])}</span><${Spark} what=${`${name} block IO, read and written`} points=${sumSeries(read, write)} format=${rateText} window=${window} floor=${1024} /><span class="muted small usage-total">${totalsText(row, ["block_read", "block_write"], ["read", "written"])}</span></td>
  `;
}

/**
 * A service's Usage tab: one tile per figure with its sparkline, and the disk reading (the
 * container's writable layer and the stack's volumes, read every 60 s by the CLI).
 */
export function UsageTab({ project, name, info }) {
  const up = serviceUp(info);
  const usage = useUsage(project.id, up);
  const samples = (usage.data && usage.data.samples) || [];
  const window = (usage.data && usage.data.window) || WINDOW;
  const series = seriesFor(samples, "service", name);
  const row = up ? latestRow(series) : null;
  const problem = up ? usageProblem(usage) : null;
  const disk = usage.data && usage.data.disk;
  const layer = serviceDisk(disk, name);
  const volumes = (disk && disk.volumes) || [];
  const rate = (key) => rateSeries(series, key);
  const tiles = row
    ? [
        ["CPU", percentText(row.cpu_percent), "of the Docker VM", valueSeries(series, "cpu_percent"), percentText, 5],
        ["Memory", memoryText(row), `of the Docker VM${typeof row.memory_percent === "number" ? `, ${percentText(row.memory_percent)}` : ""}`, valueSeries(series, "memory_used"), bytesText, 0],
        ["Network in", rateText(lastValue(rate("network_rx"))), `${bytesText(row.network_rx)} since it started`, rate("network_rx"), rateText, 1024],
        ["Network out", rateText(lastValue(rate("network_tx"))), `${bytesText(row.network_tx)} since it started`, rate("network_tx"), rateText, 1024],
        ["Block read", rateText(lastValue(rate("block_read"))), `${bytesText(row.block_read)} since it started`, rate("block_read"), rateText, 1024],
        ["Block written", rateText(lastValue(rate("block_write"))), `${bytesText(row.block_write)} since it started`, rate("block_write"), rateText, 1024],
        ["Processes", typeof row.pids === "number" ? String(row.pids) : "no value", "inside the container", valueSeries(series, "pids"), (n) => String(Math.round(n)), 1],
      ]
    : [];
  return html`<${Card} title="Usage">
    ${problem ? html`<p class="notice warn" role="status"><${Text} value=${problem} /></p>` : null}
    ${!up
      ? html`<p class="muted">Not running, so there is no usage to show. Start it under Overview.</p>`
      : row
        ? html`<ul class="tiles" aria-label=${`Usage of ${name}`}>
            ${tiles.map(([label, value, sub, points, format, floor]) => html`<li class="tile" key=${label}>
              <span class="tile-label">${label}</span>
              <span class="tile-value"><${Text} value=${value} /></span>
              <span class="muted small"><${Text} value=${sub} /></span>
              <${Spark} what=${`${name} ${label === "CPU" ? label : label.toLowerCase()}`} points=${points} format=${format} window=${window} floor=${floor} />
            </li>`)}
          </ul>`
        : html`<p class="muted" role="status"><${Text} value=${usageGap(usage, row)} /></p>`}
    <h3>Disk</h3>
    <dl class="facts">
      <dt>Writable layer</dt><dd>${layer === null ? html`<span class="muted">no reading</span>` : html`<${Text} value=${bytesText(layer)} />`}</dd>
      <dt>Stack volumes</dt><dd>${volumes.length ? html`<ul class="plain">${volumes.map((volume) => html`<li key=${volume.name}><code><${Text} value=${volume.name} /></code> <${Text} value=${bytesText(volume.size)} /></li>`)}</ul>` : html`<span class="muted">none read</span>`}</dd>
      <dt>Read</dt><dd><${Text} value=${diskAsOf(disk)} /></dd>
    </dl>
    <p class="muted small">${VM_NOTE} The figures update every few seconds while this tab is open; the last five minutes are kept in this console's memory only. Usage is best effort: containers are matched by their labels.</p>
  <//>`;
}
