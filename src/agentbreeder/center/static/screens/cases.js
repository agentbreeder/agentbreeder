// The eval-set case editor (#826, M10 PR 2): the body of the editor (screens/edit.js) for an eval
// set's cases file. Each case is a collapsible row (its id, first turn and problems); opened, it
// edits the id, the turns, the checks and the reference answer. The editor's text is the file
// rebuilt from the cases (lib/cases.js), so the review and save are the prompt editor's: the
// server runs `registry edit … --dry-run` and the CLI loads the edited set as `release` does
// (EVAL_INVALID, shown at its case).

import { CHECKS, MAX_CASES, applyForm, caseForm, caseProblems, newCase, parseCases, pyJson, serializeCases } from "../lib/cases.js";
import { Action, Actions, Text, nextId } from "../ui/components.js";
import { html, useEffect, useRef, useState } from "../ui/preact.js";

const preview = (data) => {
  const turn = Array.isArray(data.turns) ? data.turns.find((t) => typeof t === "string" || (t && typeof t.message === "string")) : null;
  const text = typeof turn === "string" ? turn : turn ? turn.message : "";
  return text.length > 80 ? `${text.slice(0, 79)}…` : text;
};

/**
 * One case: a summary row, and its form while it is open. `initial` is the line as the file
 * had it ({raw, data}, or null for a new case): an edit that puts every field back writes that
 * line again, so "No changes" comes back.
 */
function CaseRow({ number, data, initial, problems, invalid, open, onToggle, onChange, onRemove }) {
  const [form, setForm] = useState(() => caseForm(data));
  const start = useRef(initial).current; // the line at mount: later edits change `raw`
  const ids = useRef({ id: nextId("case-id"), reference: nextId("case-ref") }).current;
  const set = (patch) => {
    const next = { ...form, ...patch };
    setForm(next);
    const written = applyForm(start ? start.data : data, next);
    onChange(written, start && pyJson(written) === pyJson(start.data) ? start.raw : null);
  };
  const bad = (field) => (problems.some((p) => p.field === field) ? "true" : undefined);
  const setTurn = (index, text) => set({ turns: form.turns.map((turn, i) => (i === index ? { ...turn, text } : turn)) });
  const id = typeof data.id === "string" && data.id ? data.id : "(no id)";
  const flagged = problems.length || invalid;
  return html`<details class=${`case${flagged ? " invalid" : ""}`} open=${open} onToggle=${(event) => onToggle(event.currentTarget.open)}>
    <summary>
      <span class="case-number">${number}.</span> <code><${Text} value=${id} /></code>
      <span class="muted"> <${Text} value=${preview(data)} /></span>
      ${problems.length ? html`<span class="case-problem"> ! <${Text} value=${problems.map((p) => p.text).join("; ")} /></span>` : null}
    </summary>
    ${open
      ? html`<div class="case-form">
          ${invalid ? html`<p class="notice error" role="alert"><${Text} value=${invalid} /></p>` : null}
          <label for=${ids.id}>Case id</label>
          <input id=${ids.id} value=${form.id} aria-invalid=${bad("id")} onInput=${(event) => set({ id: event.currentTarget.value })} />
          <fieldset>
            <legend>Turns (what the user says, in order)</legend>
            ${form.turns.map((turn, index) => turn.text === null
              ? html`<p class="muted small" key=${index}>Turn ${index + 1} is kept as it is (not text the console edits).</p>`
              : html`<div class="turn" key=${index}>
                  <label for=${`${ids.id}-turn-${index}`}>Turn ${index + 1}${turn.data && turn.data.new_session ? " (a new session)" : ""}</label>
                  <textarea id=${`${ids.id}-turn-${index}`} rows="2" value=${turn.text} onInput=${(event) => setTurn(index, event.currentTarget.value)}></textarea>
                  ${form.turns.length > 1 ? html`<${Action} small tone="secondary" onClick=${() => set({ turns: form.turns.filter((_, i) => i !== index) })}>Remove turn ${index + 1}<//>` : null}
                </div>`)}
            <${Action} small tone="secondary" onClick=${() => set({ turns: [...form.turns, { text: "" }] })}>Add a turn<//>
          </fieldset>
          <fieldset>
            <legend>Checks (each must pass; a judge model also grades the reply against the reference)</legend>
            ${CHECKS.map(({ key, label }) => html`<div class="check-field" key=${key}>
              <label for=${`${ids.id}-${key}`}>${label}</label>
              <textarea id=${`${ids.id}-${key}`} rows="2" spellcheck="false" value=${form[key]} onInput=${(event) => set({ [key]: event.currentTarget.value })}></textarea>
            </div>`)}
          </fieldset>
          <label for=${ids.reference}>Reference answer (what a correct reply says)</label>
          <textarea id=${ids.reference} rows="3" value=${form.reference} aria-invalid=${bad("reference")} onInput=${(event) => set({ reference: event.currentTarget.value })}></textarea>
          <${Actions}><${Action} tone="secondary" onClick=${onRemove}>Remove case ${id}<//><//>
        </div>`
      : null}
  </details>`;
}

/**
 * The cases (`parsed`, the editor's `parseCases` of its text) as rows. `onText(next)` gives the
 * rebuilt file. `original` is the version's text: a row whose fields are all put back writes its
 * original line. `invalid` is the CLI's EVAL_INVALID ({index, message}) from the last review,
 * shown at its case (which opens); any edit clears it. A file that is not JSON lines is shown with
 * why, and `fallback` (the plain text editor) instead.
 */
export function CasesBody({ parsed, original, onText, invalid, fallback }) {
  const [open, setOpen] = useState(() => new Set());
  const [generation, setGeneration] = useState(0); // re-made forms after an add or remove
  const initial = useRef(null);
  if (initial.current === null) {
    const first = parseCases(original);
    initial.current = new Map(first.entries.filter((entry) => entry.data).map((entry) => [entry.raw, entry]));
  }
  const lineOf = (index) => {
    let seen = -1;
    return parsed.entries.findIndex((entry) => entry.data && (seen += 1) === index);
  };
  useEffect(() => {
    if (!invalid || parsed.error) return;
    const line = lineOf(invalid.index);
    if (line >= 0) setOpen((now) => new Set(now).add(line));
  }, [invalid]);
  if (parsed.error) {
    return html`<p class="notice warn">This file is not one JSON case per line (<${Text} value=${parsed.error} />), so it is shown as text.</p>${fallback}`;
  }
  const { entries, ending } = parsed;
  const cases = entries.map((entry, line) => ({ entry, line })).filter(({ entry }) => entry.data);
  const problems = caseProblems(entries);
  const write = (next) => onText(serializeCases(next, ending));
  const change = (line, data, raw) => write(entries.map((entry, i) => (i === line ? { raw, data } : entry)));
  const toggle = (line, isOpen) => setOpen((now) => {
    const next = new Set(now);
    if (isOpen) next.add(line);
    else next.delete(line);
    return next;
  });
  const add = () => {
    const line = entries.length;
    write([...entries, { raw: null, data: newCase(entries) }]);
    setOpen((now) => new Set(now).add(line));
    setGeneration((n) => n + 1);
  };
  const remove = (line) => {
    write(entries.filter((_, i) => i !== line));
    setOpen(new Set([...open].filter((l) => l !== line).map((l) => (l > line ? l - 1 : l))));
    setGeneration((n) => n + 1);
  };
  return html`<div class="cases">
    <p class="muted small" role="status">${cases.length} ${cases.length === 1 ? "case" : "cases"}${problems.count ? `, ${problems.count} to fix before review` : ""}.</p>
    ${problems.set ? html`<p class="notice error"><${Text} value=${problems.set} /></p>` : null}
    ${cases.map(({ entry, line }, index) => html`<${CaseRow}
      key=${`${generation}:${line}`}
      number=${index + 1}
      data=${entry.data}
      initial=${initial.current.get(entry.raw) || null}
      problems=${problems.cases[index]}
      invalid=${invalid && invalid.index === index ? invalid.message : null}
      open=${open.has(line)}
      onToggle=${(isOpen) => toggle(line, isOpen)}
      onChange=${(data, raw) => change(line, data, raw)}
      onRemove=${() => remove(line)} />`)}
    <${Actions}><${Action} tone="secondary" onClick=${add} disabled=${cases.length >= MAX_CASES ? `An eval set has at most ${MAX_CASES} cases.` : false}>Add a case<//><//>
  </div>`;
}
