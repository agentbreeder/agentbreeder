// Logs (#821, M5): one snapshot of a stack service's or a deployment's logs, read by `services logs
// --json` or `logs ID --json` (redacted by the CLI, controls escaped). The lines are filtered,
// searched, wrapped and copied here; nothing is stored. Follow comes with the pumped children
// (M5 PR2).

import { emptyReason, LEVEL_CHOICES, linePieces, logNotes, logsPath, searchHits, searchStatus, shownRows, SINCE, stepHit, TAILS, windowOf } from "../lib/logs.js";
import { href } from "../lib/router.js";
import { visible } from "../lib/text.js";
import { Action, Card, CopyButton, nextId, Notice, Text, useConsole } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { html, useEffect, useLayoutEffect, useMemo, useRef, useState } from "../ui/preact.js";

const ROW = 20; // px; `.logview.nowrap .logline` has this height, so rows can be windowed
const KINDS = ["service", "deployment"];
const LEVEL_WORDS = { warning: "Warnings and errors", error: "Errors only" };

export function Logs({ project, params }) {
  const { navigate } = useConsole();
  const base = `/api/projects/${project.id}`;
  const [services] = useView(`${base}/services`, project.id);
  const [deployments] = useView(`${base}/deployments`, project.id);
  const named = Boolean(params && params.kind);
  const candidate = named && KINDS.includes(params.kind) && params.name ? { kind: params.kind, name: params.name } : null;
  // The router lets any plain segment through; only a service name or a deployment ID is read.
  const source = candidate && logsPath(project.id, candidate) ? candidate : null;
  const serviceNames = Object.keys((services.data && services.data.services) || {});
  const rows = Array.isArray(deployments.data) ? deployments.data : [];
  const deployment = source && source.kind === "deployment" ? rows.find((row) => row.id === source.name) : null;
  const choose = (event) => {
    const [kind, name] = event.currentTarget.value.split(":");
    navigate(kind ? `/logs/${kind}/${name}` : "/logs");
  };
  const value = source ? `${source.kind}:${source.name}` : "";
  const known = !source || (source.kind === "service" ? serviceNames.includes(source.name) : Boolean(deployment));
  return html`
    <${Card} title="Source">
      <div class="row">
        <label class="field">
          <span>Read the logs of</span>
          <select value=${value} onChange=${choose}>
            <option value="">Choose a service or deployment</option>
            ${serviceNames.length ? html`<optgroup label="Stack services">${serviceNames.map((name) => html`<option value=${`service:${name}`}>${name}</option>`)}</optgroup>` : null}
            ${rows.length ? html`<optgroup label="Deployments">${rows.map((row) => html`<option value=${`deployment:${row.id}`}>${visible(`${row.name} (${row.target}, ${String(row.id).slice(0, 8)})`)}</option>`)}</optgroup>` : null}
            ${source && !known ? html`<option value=${value}>${visible(source.name)}</option>` : null}
          </select>
        </label>
      </div>
      ${!serviceNames.length && !rows.length && !services.loading && !deployments.loading ? html`<p class="muted">Nothing to read yet: start the services or deploy an agent first.</p>` : null}
    <//>
    ${named && !source ? html`<${Notice} notice=${{ tone: "error", text: "This address names no service or deployment: choose one above." }} />` : null}
    ${source ? html`<${Reader} key=${value} project=${project} source=${source} kubernetes=${Boolean(deployment && deployment.target === "kubernetes")} />` : null}
  `;
}

/** A row's link to its logs; nothing when the name could not be a route parameter. */
export function LogsLink({ kind, name, label }) {
  let target;
  try {
    target = href("/logs/:kind/:name", { kind, name });
  } catch {
    return null;
  }
  return html`<a class="action ghost small" href=${target} aria-label=${label}>Logs</a>`;
}

function useLogs(path) {
  const { client } = useConsole();
  const [state, setState] = useState({ loading: false, data: null, error: null });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return undefined;
    let live = true;
    setState((previous) => ({ ...previous, loading: true }));
    client
      .request(path)
      .then((reply) => live && setState({ loading: false, data: reply.data, error: reply.error }))
      .catch((error) => {
        if (!live || error.kind === "session") return;
        setState({ loading: false, data: null, error: { code: error.code, message: error.message } });
      });
    return () => {
      live = false;
    };
  }, [path, tick]);
  return [state, () => setTick((n) => n + 1)];
}

function Reader({ project, source, kubernetes }) {
  // The read's options are a small form: applied with Read, so typing never starts a read.
  const [form, setForm] = useState({ tail: 200, since: "", previous: false });
  const [applied, setApplied] = useState(form);
  const [read, again] = useLogs(logsPath(project.id, source, applied));
  const [filter, setFilter] = useState("");
  const [level, setLevel] = useState("");
  const [wrap, setWrap] = useState(false);
  const [query, setQuery] = useState("");
  // The chosen match is kept with the hits it was chosen from, so a new filter or search drops it
  // in the same render; `jump` scrolls to it on every Next or Previous, even with one match.
  const [pick, setPick] = useState({ hits: null, at: -1, jump: 0 });
  const data = read.data;
  const lines = (data && data.lines) || [];
  const levels = (data && data.levels) || [];
  const shown = useMemo(() => shownRows(lines, levels, { level: data && data.format ? level || null : null, filter }), [data, level, filter]);
  const hits = useMemo(() => searchHits(shown, query), [shown, query]);
  const hit = pick.hits === hits ? pick.at : -1;
  const sinceBad = Boolean(form.since) && !SINCE.test(form.since);
  const sinceWhy = useRef(nextId("why")).current;
  const apply = (event) => {
    event.preventDefault();
    if (sinceBad || read.loading) return; // the button stays the same element, so focus stays
    const next = { ...form, previous: kubernetes && form.previous };
    if (JSON.stringify(next) === JSON.stringify(applied)) again();
    else setApplied(next);
  };
  const empty = emptyReason(read.error, data);
  const failed = read.error && !empty;
  const levelOff = !data ? "Read the logs first." : !data.format ? "This log's format is not known, so its lines have no level." : false;
  const go = (backwards) => setPick((previous) => ({ hits, at: stepHit(hits, hit, backwards), jump: previous.jump + 1 }));
  const name = source.kind === "service" ? source.name : `deployment ${source.name.slice(0, 8)}`;
  return html`
    <${Card} title=${`Logs: ${name}`} busy=${read.loading}>
      <form class="row log-options" onSubmit=${apply}>
        <label class="field">
          <span>Lines</span>
          <select value=${String(form.tail)} onChange=${(e) => setForm({ ...form, tail: Number(e.currentTarget.value) })}>
            ${TAILS.map((n) => html`<option value=${String(n)}>Last ${n}</option>`)}
          </select>
        </label>
        <div class="field">
          <label class="field">
            <span>Since (optional)</span>
            <input type="text" value=${form.since} placeholder="10m" spellcheck="false" autocomplete="off" aria-invalid=${sinceBad ? "true" : "false"} aria-describedby=${sinceBad ? sinceWhy : undefined} onInput=${(e) => setForm({ ...form, since: e.currentTarget.value.trim() })} />
          </label>
          ${sinceBad ? html`<span class="reason" id=${sinceWhy}>Since must look like 10m, 2h or 2026-11-11T09:00:00Z.</span>` : null}
        </div>
        ${kubernetes ? html`<label class="check"><input type="checkbox" checked=${form.previous} onChange=${(e) => setForm({ ...form, previous: e.currentTarget.checked })} /> Previous container (after a restart)</label>` : null}
        <${Action} type="submit" tone="secondary" disabled=${sinceBad ? "Fix Since first." : false}>${read.loading ? "Reading…" : data || read.error ? "Read again" : "Read"}<//>
      </form>
      ${failed ? html`<${Notice} notice=${{ tone: "error", text: `${read.error.message || "Could not read the logs."}${read.error.code ? ` (${read.error.code})` : ""}` }} />` : null}
      ${data
        ? html`<div class="row log-tools" role="group" aria-label="Show lines">
            <label class="field">
              <span>Filter</span>
              <input type="search" value=${filter} spellcheck="false" autocomplete="off" onInput=${(e) => setFilter(e.currentTarget.value)} />
            </label>
            <${LevelPick} level=${level} setLevel=${setLevel} off=${levelOff} />
            <label class="field">
              <span>Search</span>
              <input type="search" value=${query} spellcheck="false" autocomplete="off" onInput=${(e) => setQuery(e.currentTarget.value)} onKeyDown=${(e) => { if (e.key === "Enter") { e.preventDefault(); go(e.shiftKey); } }} />
            </label>
            <div class="search-step">
              <button type="button" class="action ghost small" disabled=${!hits.length} onClick=${() => go(true)}>Previous match</button>
              <button type="button" class="action ghost small" disabled=${!hits.length} onClick=${() => go(false)}>Next match</button>
              <span class="muted small" role="status">${searchStatus(hits, hit, query)}</span>
            </div>
            <label class="check"><input type="checkbox" checked=${wrap} onChange=${(e) => setWrap(e.currentTarget.checked)} /> Wrap lines</label>
            <${CopyButton} value=${shown.map((row) => row.text).join("\n")} label=${`the ${shown.length} shown lines`} />
          </div>`
        : null}
      <div role="status" class="log-status">${empty ? html`<p class="log-empty"><${EmptyText} empty=${empty} /></p>` : null}</div>
      ${data && shown.length !== lines.length && shown.length ? html`<p class="muted small">Showing ${shown.length} of ${lines.length} lines.</p>` : null}
      ${data && shown.length ? html`<${LogLines} rows=${shown} wrap=${wrap} query=${query} current=${hit >= 0 ? hits[hit] : -1} jump=${pick.jump} />` : null}
      ${data && lines.length && !shown.length ? html`<p class="muted">No line matches the filter.</p>` : null}
      ${data ? html`<ul class="log-notes muted small">${logNotes(data, { tail: applied.tail }).map((note) => html`<li>${note}</li>`)}</ul>` : null}
    <//>
  `;
}

function EmptyText({ empty }) {
  return html`<${Text} value=${empty.text} />${empty.code !== "EMPTY" ? html` <span class="muted small">(${empty.code})</span>` : null}`;
}

function LevelPick({ level, setLevel, off }) {
  const id = useRef(nextId("why")).current;
  return html`<div class="field">
    <label class="field">
      <span>Level</span>
      <select value=${off ? "" : level} disabled=${Boolean(off)} aria-describedby=${off ? id : undefined} onChange=${(e) => setLevel(e.currentTarget.value)}>
        <option value="">All lines</option>
        ${LEVEL_CHOICES.map((choice) => html`<option value=${choice}>${LEVEL_WORDS[choice]}</option>`)}
      </select>
    </label>
    ${off ? html`<span class="reason" id=${id}>${off}</span>` : null}
  </div>`;
}

/**
 * The lines. Unwrapped, every row is ROW px high, so only the rows in view (plus a margin) are in
 * the page: 10,000 lines stay as fast as 200. Wrapped rows vary in height, so all are in the page
 * and the browser skips laying out the ones out of view (`content-visibility: auto`).
 */
function LogLines({ rows, wrap, query, current, jump }) {
  const box = useRef(null);
  const [view, setView] = useState({ top: 0, height: 480 });
  const onScroll = () => box.current && setView({ top: box.current.scrollTop, height: box.current.clientHeight });
  useLayoutEffect(onScroll, [rows, wrap]);
  useLayoutEffect(() => {
    if (current < 0 || !box.current) return;
    if (wrap) {
      box.current.querySelector(`[data-pos="${current}"]`)?.scrollIntoView({ block: "center" });
    } else {
      box.current.scrollTop = Math.max(0, current * ROW - box.current.clientHeight / 2);
    }
    onScroll();
  }, [jump, wrap]);
  const [first, last] = wrap ? [0, rows.length] : windowOf(view.top, view.height, ROW, rows.length);
  return html`<div class=${`logview ${wrap ? "wrap" : "nowrap"}`} ref=${box} onScroll=${onScroll} role="region" aria-label="Log lines" tabindex="0">
    ${wrap ? null : html`<div class="spacer" style=${{ height: `${first * ROW}px` }}></div>`}
    ${rows.slice(first, last).map((row, offset) => {
      const position = first + offset;
      return html`<div key=${row.index} data-pos=${position} class=${`logline${row.level ? ` level-${row.level}` : ""}${position === current ? " current" : ""}`}>
        <span class="ln" aria-hidden="true">${row.index + 1}</span><span class="lt">${linePieces(row.line, query).map((piece) => {
          const text = piece.escaped ? html`<span class="esc" title="A control or invisible character, shown escaped">${piece.text}</span>` : piece.text;
          return piece.hit ? html`<mark>${text}</mark>` : text;
        })}</span>
      </div>`;
    })}
    ${wrap ? null : html`<div class="spacer" style=${{ height: `${(rows.length - last) * ROW}px` }}></div>`}
  </div>`;
}
