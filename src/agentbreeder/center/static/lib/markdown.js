// A skill's Markdown as a token tree (#1059 UI-R, composition console spec §9.3, CC1). A skill's
// SKILL.md and references are text from a publisher that the model reads, so they are untrusted:
// this parser never builds HTML. It turns the source into plain objects of a fixed set of types,
// and `ui/markdown.js` renders each type as a fixed element with htm, so publisher text only
// ever becomes text nodes. Nothing here touches the DOM, fetches or decodes anything.
//
// The subset: headings (`#` to h4, `##` to h5, deeper to h6), paragraphs, emphasis, strong, inline
// code, fenced and indented code blocks, bullet and numbered lists, block quotes, horizontal rules
// and hard line breaks. Everything else stays visible as its own characters: raw HTML, entities
// (never decoded, so the formatted view shows what the model reads), images (as "[image: ALT, not
// shown]"), link titles, reference-style definitions, tables (their source, CC7) and anything
// outside the subset. Only secure web links and mail links become links; any other scheme, a
// relative address or an address with a user name is text.
//
// Bounds: lists and block quotes nest at most 4 deep and emphasis and links at most 8 (deeper
// markers stay literal text); past MAX_TOKENS the caller shows the source with the reason. Every
// scan moves forward or jumps through tables built in one pass, and there are no regular
// expressions that can backtrack on nested input, so the work grows linearly with the input
// (`steps` counts it; tests/js/markdown.test.mjs checks the growth).

export const MAX_TOKENS = 5000;
export const MAX_BLOCK_DEPTH = 4;
export const MAX_INLINE_DEPTH = 8;
export const MAX_SHOWN_ADDRESS = 80;

/** Every block and inline token type the renderer knows. Anything else is a bug. */
export const BLOCK_TYPES = ["heading", "paragraph", "code", "list", "quote", "hr", "literal"];
export const INLINE_TYPES = ["text", "em", "strong", "code", "br", "link"];

const SECURE = "https" + ":"; // spelled apart: the static guard flags a literal web address
const MAIL = "mailto:";

class Budget {
  constructor() {
    this.tokens = 0;
    this.steps = 0;
    this.over = false;
    this.noted = false; // "full address in Source" is shown once per document
  }

  token(node) {
    this.tokens += 1;
    if (this.tokens > MAX_TOKENS) this.over = true;
    return node;
  }
}

// -- lines ------------------------------------------------------------------------------------

const isSpace = (ch) => ch === " " || ch === "\t";

/** The number of leading spaces (a tab counts as 4) and where the text after them starts. */
function indentOf(line) {
  let width = 0;
  let index = 0;
  while (index < line.length && isSpace(line[index])) {
    width += line[index] === "\t" ? 4 - (width % 4) : 1;
    index += 1;
  }
  return { width, index };
}

const blank = (line) => indentOf(line).index === line.length;

/** Remove up to `count` columns of leading indent. */
function dedent(line, count) {
  let width = 0;
  let index = 0;
  while (index < line.length && width < count && isSpace(line[index])) {
    width += line[index] === "\t" ? 4 - (width % 4) : 1;
    index += 1;
  }
  return line.slice(index);
}

function fenceOf(line) {
  const { width, index } = indentOf(line);
  if (width > 3) return null;
  const ch = line[index];
  if (ch !== "`" && ch !== "~") return null;
  let end = index;
  while (line[end] === ch) end += 1;
  if (end - index < 3) return null;
  if (ch === "`" && line.indexOf("`", end) !== -1) return null;
  return { ch, length: end - index, indent: width };
}

function closesFence(line, fence) {
  const { width, index } = indentOf(line);
  if (width > 3) return false;
  let end = index;
  while (line[end] === fence.ch) end += 1;
  return end - index >= fence.length && blank(line.slice(end));
}

function headingOf(line) {
  const { width, index } = indentOf(line);
  if (width > 3) return null;
  let end = index;
  while (line[end] === "#" && end - index < 7) end += 1;
  const level = end - index;
  if (level < 1 || level > 6) return null;
  if (end < line.length && !isSpace(line[end])) return null;
  let text = line.slice(end).trim();
  // A closing run of #s preceded by a space is not part of the text.
  let cut = text.length;
  while (cut > 0 && text[cut - 1] === "#") cut -= 1;
  if (cut === 0) text = "";
  else if (cut < text.length && isSpace(text[cut - 1])) text = text.slice(0, cut).trimEnd();
  return { level, text };
}

function isRule(line) {
  const { width, index } = indentOf(line);
  if (width > 3) return false;
  const ch = line[index];
  if (ch !== "-" && ch !== "*" && ch !== "_") return false;
  let count = 0;
  for (let i = index; i < line.length; i += 1) {
    if (line[i] === ch) count += 1;
    else if (!isSpace(line[i])) return false;
  }
  return count >= 3;
}

function quoteOf(line) {
  const { width, index } = indentOf(line);
  if (width > 3 || line[index] !== ">") return null;
  let rest = line.slice(index + 1);
  if (rest[0] === " " || rest[0] === "\t") rest = rest.slice(1);
  return rest;
}

/** A list marker: {ordered, start, content (column of the item's text), rest, bullet}. */
function markerOf(line) {
  const { width, index } = indentOf(line);
  if (width > 3) return null;
  const ch = line[index];
  let end = index;
  let ordered = false;
  let start = 1;
  if (ch === "-" || ch === "*" || ch === "+") {
    end = index + 1;
  } else {
    while (end < line.length && end - index < 9 && line[end] >= "0" && line[end] <= "9") end += 1;
    if (end === index || (line[end] !== "." && line[end] !== ")")) return null;
    ordered = true;
    start = Number(line.slice(index, end));
    end += 1;
  }
  if (end < line.length && !isSpace(line[end])) return null;
  const after = indentOf(line.slice(end));
  const gap = end === line.length || after.index === line.length - end ? 1 : Math.min(after.width, 4) || 1;
  const rest = after.width > 4 ? line.slice(end + 1) : line.slice(end + after.index);
  return { ordered, start, bullet: ordered ? line[end - 1] : ch, content: width + (end - index) + gap, rest };
}

/** A table: a row with `|`, then a delimiter row (`|---|:--:|`). Shown as source (CC7). */
function isDelimiterRow(line) {
  const text = line.trim();
  if (!text.includes("-")) return false;
  let pipes = 0;
  for (const ch of text) {
    if (ch === "|") pipes += 1;
    else if (ch !== "-" && ch !== ":" && !isSpace(ch)) return false;
  }
  return pipes > 0;
}

function isDefinition(line) {
  const { width, index } = indentOf(line);
  if (width > 3 || line[index] !== "[") return false;
  const close = line.indexOf("]", index + 1);
  return close > index + 1 && line[close + 1] === ":";
}

// -- blocks -----------------------------------------------------------------------------------

/** Whether `line` starts a block other than a paragraph continuation, at this depth. */
function interrupts(line, depth) {
  if (blank(line) || fenceOf(line) || headingOf(line) || isRule(line)) return true;
  if (depth < MAX_BLOCK_DEPTH && (quoteOf(line) !== null || markerOf(line))) return true;
  return false;
}

function parseBlocks(lines, depth, budget) {
  const blocks = [];
  let i = 0;
  while (i < lines.length && !budget.over) {
    budget.steps += 1;
    const line = lines[i];
    if (blank(line)) {
      i += 1;
      continue;
    }
    const fence = fenceOf(line);
    if (fence) {
      const body = [];
      i += 1;
      while (i < lines.length && !closesFence(lines[i], fence)) {
        budget.steps += 1;
        body.push(dedent(lines[i], fence.indent));
        i += 1;
      }
      i += 1; // the closing fence, or past the end
      blocks.push(budget.token({ type: "code", text: body.join("\n") }));
      continue;
    }
    if (indentOf(line).width >= 4) {
      const body = [];
      while (i < lines.length && (indentOf(lines[i]).width >= 4 || blank(lines[i]))) {
        budget.steps += 1;
        body.push(dedent(lines[i], 4));
        i += 1;
      }
      while (body.length && blank(body[body.length - 1])) body.pop();
      blocks.push(budget.token({ type: "code", text: body.join("\n") }));
      continue;
    }
    const heading = headingOf(line);
    if (heading) {
      blocks.push(budget.token({ type: "heading", level: Math.min(heading.level + 3, 6), children: parseInline(heading.text, budget) }));
      i += 1;
      continue;
    }
    if (isRule(line)) {
      blocks.push(budget.token({ type: "hr" }));
      i += 1;
      continue;
    }
    if (depth < MAX_BLOCK_DEPTH && quoteOf(line) !== null) {
      const inner = [];
      while (i < lines.length) {
        budget.steps += 1;
        const rest = quoteOf(lines[i]);
        if (rest !== null) inner.push(rest);
        else if (!blank(lines[i]) && inner.length && !blank(inner[inner.length - 1]) && !interrupts(lines[i], depth)) inner.push(lines[i]); // lazy continuation
        else break;
        i += 1;
      }
      blocks.push(budget.token({ type: "quote", children: parseBlocks(inner, depth + 1, budget) }));
      continue;
    }
    const marker = depth < MAX_BLOCK_DEPTH ? markerOf(line) : null;
    if (marker) {
      const list = { type: "list", ordered: marker.ordered, start: marker.start, items: [] };
      let current = marker;
      while (current) {
        const item = [current.rest];
        i += 1;
        let gap = false;
        while (i < lines.length) {
          budget.steps += 1;
          const next = lines[i];
          if (blank(next)) {
            gap = true;
            item.push("");
            i += 1;
            continue;
          }
          if (indentOf(next).width >= current.content) {
            item.push(dedent(next, current.content));
            gap = false;
            i += 1;
            continue;
          }
          if (!gap && !interrupts(next, depth)) {
            item.push(next); // lazy continuation of the item's paragraph
            i += 1;
            continue;
          }
          break;
        }
        while (item.length && blank(item[item.length - 1])) item.pop();
        list.items.push(parseBlocks(item, depth + 1, budget));
        budget.token(list);
        const following = i < lines.length ? markerOf(lines[i]) : null;
        current = following && following.ordered === list.ordered && following.bullet === current.bullet ? following : null;
      }
      blocks.push(list);
      continue;
    }
    if (isDefinition(line)) {
      // A reference-style definition: its own visible line, never resolved.
      blocks.push(budget.token({ type: "literal", text: line.trim(), block: false }));
      i += 1;
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && isDelimiterRow(lines[i + 1])) {
      const rows = [];
      while (i < lines.length && !blank(lines[i]) && lines[i].includes("|")) {
        budget.steps += 1;
        rows.push(lines[i]);
        i += 1;
      }
      blocks.push(budget.token({ type: "literal", text: rows.join("\n"), block: true }));
      continue;
    }
    const paragraph = [line];
    i += 1;
    while (i < lines.length && !interrupts(lines[i], depth) && !isDefinition(lines[i])) {
      budget.steps += 1;
      paragraph.push(lines[i]);
      i += 1;
    }
    blocks.push(budget.token({ type: "paragraph", children: parseInline(paragraph.map((l) => l.replace(/^[ \t]+/, "")).join("\n"), budget) }));
  }
  return blocks;
}

// -- inline -----------------------------------------------------------------------------------

const PUNCTUATION = new Set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~");
const isWhite = (ch) => ch === undefined || ch === " " || ch === "\t" || ch === "\n";
const isPunct = (ch) => ch !== undefined && PUNCTUATION.has(ch);

/**
 * For each index, the index of the next character in `stops` at or after it (or the length):
 * one backward pass, so every later "scan to the next X" is a jump.
 */
function nextOf(text, test, budget) {
  budget.steps += text.length;
  const next = new Int32Array(text.length + 1);
  next[text.length] = text.length;
  for (let i = text.length - 1; i >= 0; i -= 1) next[i] = test(text[i]) ? i : next[i + 1];
  return next;
}

/** A link's address as {href, shown, scheme} when it may be a link, else null. */
export function linkTarget(address) {
  let url;
  try {
    url = new URL(address);
  } catch {
    return null;
  }
  if (url.protocol === MAIL) {
    // A query (cc, bcc, body) or fragment would be hidden behind the shown address: text.
    if (url.search || url.hash) return null;
    const to = decodeSafe(url.pathname);
    return to ? { href: url.href, shown: to, scheme: "mailto" } : null;
  }
  if (url.protocol !== SECURE || url.username || url.password || !url.hostname) return null;
  // The address as the browser will use it: the host in ASCII (punycode), so a look-alike
  // host shows as xn--… here.
  const full = url.href;
  const cut = full.length > MAX_SHOWN_ADDRESS;
  return { href: full, shown: cut ? `${full.slice(0, MAX_SHOWN_ADDRESS)}…` : full, cut, scheme: "https" };
}

function decodeSafe(text) {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

/** Plain text of inline tokens (an image's alt, a link's accessible name). */
export function plainText(nodes) {
  let out = "";
  for (const node of nodes || []) {
    if (node.type === "text" || node.type === "code") out += node.text;
    else if (node.type === "br") out += " ";
    else if (node.children) out += plainText(node.children);
  }
  return out;
}

function parseInline(text, budget) {
  const out = [];
  const delims = []; // open emphasis runs: {ch, count, index in out}
  const brackets = []; // open `[` or `![`: {index in out, image, delims, dead}
  let liveBrackets = 0; // the ones not past the nesting cap
  // The text since the last token, as pieces joined once at `flush` (appending to one string
  // and reading its end on every line would flatten it each time: quadratic). The tail state a
  // line break needs is kept beside it: trailing spaces, and whether the last character is a
  // backslash typed as such (an escaped `\\` is not a hard break).
  let pieces = [];
  let tailSpaces = 0;
  let tailSlash = false;
  const add = (piece, literal = true) => {
    if (!piece) return;
    pieces.push(piece);
    let k = piece.length;
    while (k > 0 && piece[k - 1] === " ") k -= 1;
    tailSpaces = k === 0 ? tailSpaces + piece.length : piece.length - k;
    tailSlash = k === piece.length && literal && piece[k - 1] === "\\";
  };
  /** Drop `count` characters from the end of the pieces. */
  const dropTail = (count) => {
    while (count > 0 && pieces.length) {
      const last = pieces[pieces.length - 1];
      if (last.length <= count) {
        count -= last.length;
        pieces.pop();
      } else {
        pieces[pieces.length - 1] = last.slice(0, last.length - count);
        count = 0;
      }
    }
    tailSpaces = 0;
    tailSlash = false;
  };
  const flush = () => {
    if (pieces.length) out.push(budget.token({ type: "text", text: pieces.join("") }));
    pieces = [];
    tailSpaces = 0;
    tailSlash = false;
  };
  // Code spans: the next backtick run of the same length, found through per-length queues.
  const runs = new Map(); // length -> starts, in order
  for (let i = 0; i < text.length; ) {
    budget.steps += 1;
    if (text[i] !== "`") {
      i += 1;
      continue;
    }
    let end = i;
    while (text[end] === "`") end += 1;
    const length = end - i;
    if (!runs.has(length)) runs.set(length, []);
    runs.get(length).push(i);
    i = end;
  }
  const cursor = new Map(); // length -> next index into runs.get(length)
  const nextRun = (length, after) => {
    const list = runs.get(length) || [];
    let at = cursor.get(length) || 0;
    while (at < list.length && list[at] <= after) {
      at += 1;
      budget.steps += 1;
    }
    cursor.set(length, at);
    return at < list.length ? list[at] : -1;
  };
  const stopAddress = nextOf(text, (ch) => isWhite(ch) || ch === ")" || ch === "<", budget);
  const stopAngle = nextOf(text, (ch) => ch === ">" || ch === "<" || isWhite(ch), budget);
  const nextQuote = { '"': nextOf(text, (ch) => ch === '"' || ch === "\n", budget), "'": nextOf(text, (ch) => ch === "'" || ch === "\n", budget) };

  /** `(address "title")` at `open` (the index of `(`): {address, title, end} or null. */
  const destination = (open) => {
    let i = open + 1;
    while (text[i] === " " || text[i] === "\t") i += 1;
    const start = i;
    let end = stopAddress[i];
    if (end === start || end >= text.length) return null;
    // Balanced parentheses in the address (a few levels, each a jump): `…/Foo_(bar)`.
    let parens = 0;
    for (let k = start; k < end; k += 1) if (text[k] === "(") parens += 1;
    for (let extend = 0; parens > 0 && extend < 4 && text[end] === ")"; extend += 1) {
      parens -= 1;
      const next = stopAddress[end + 1];
      for (let k = end + 1; k < next; k += 1) if (text[k] === "(") parens += 1;
      budget.steps += next - end;
      end = next;
      if (end >= text.length) return null;
    }
    const address = text.slice(start, end);
    i = end;
    let title = null;
    while (text[i] === " " || text[i] === "\t") i += 1;
    if ((text[i] === '"' || text[i] === "'") && i > end) {
      const close = nextQuote[text[i]][i + 1];
      if (close >= text.length || text[close] !== text[i]) return null;
      title = text.slice(i + 1, close);
      i = close + 1;
      while (text[i] === " " || text[i] === "\t") i += 1;
    }
    if (text[i] !== ")") return null;
    return { address, title, end: i + 1 };
  };

  let noted = budget.noted;
  let i = 0;
  while (i < text.length) {
    budget.steps += 1;
    budget.noted = noted;
    if (budget.over) return out;
    const ch = text[i];
    if (ch === "\\" && isPunct(text[i + 1])) {
      add(text[i + 1], false);
      i += 2;
      continue;
    }
    if (ch === "\n") {
      // Two trailing spaces or a backslash make a hard break; otherwise a soft one (a space).
      const hard = tailSpaces >= 2 || tailSlash;
      dropTail(tailSlash ? 1 : tailSpaces);
      if (hard) {
        flush();
        out.push(budget.token({ type: "br" }));
      } else add(" ");
      i += 1;
      continue;
    }
    if (ch === "`") {
      let end = i;
      while (text[end] === "`") end += 1;
      const close = nextRun(end - i, i);
      if (close === -1) {
        add(text.slice(i, end));
        i = end;
        continue;
      }
      flush();
      let code = text.slice(end, close).replace(/\n/g, " ");
      if (code.length > 2 && code[0] === " " && code[code.length - 1] === " " && code.trim()) code = code.slice(1, -1);
      out.push(budget.token({ type: "code", text: code }));
      i = close + (end - i);
      continue;
    }
    // An autolink, outside link text only (links do not nest). Its text is the address as the
    // browser will use it (`shown`: the host in punycode), never the characters as typed.
    if (ch === "<" && !liveBrackets) {
      const end = stopAngle[i + 1];
      if (end < text.length && text[end] === ">" && end > i + 1) {
        const target = linkTarget(text.slice(i + 1, end));
        if (target) {
          flush();
          if (target.cut) target.note = !noted;
          noted = noted || Boolean(target.cut);
          out.push(budget.token({ type: "link", ...target, children: [budget.token({ type: "text", text: target.shown })], autolink: true }));
          i = end + 1;
          continue;
        }
      }
      add(ch); // raw HTML and other autolinks stay as their characters
      i += 1;
      continue;
    }
    if (ch === "[" || (ch === "!" && text[i + 1] === "[")) {
      const image = ch === "!";
      flush();
      out.push(budget.token({ type: "text", text: image ? "![" : "[" }));
      // Past the nesting cap a `[` stays text, but is still matched by its `]` (dead), so a
      // later `]` never closes an outer opener.
      const dead = liveBrackets + delims.length >= MAX_INLINE_DEPTH;
      if (!dead) liveBrackets += 1;
      brackets.push({ index: out.length - 1, image, delims: delims.length, dead });
      i += image ? 2 : 1;
      continue;
    }
    if (ch === "]" && brackets.length) {
      const opener = brackets.pop();
      if (!opener.dead) liveBrackets -= 1;
      const target = !opener.dead && text[i + 1] === "(" ? destination(i + 1) : null;
      if (!target) {
        add("]"); // not an inline link (reference-style links are never resolved)
        i += 1;
        continue;
      }
      flush();
      delims.length = Math.min(delims.length, opener.delims); // emphasis cannot cross a link
      const children = out.splice(opener.index + 1);
      out.pop(); // the `[` text
      const link = linkTarget(target.address);
      if (opener.image) {
        out.push(budget.token({ type: "text", text: `[image: ${plainText(children)}, not shown]` }));
      } else if (link) {
        if (link.cut) link.note = !noted;
        noted = noted || Boolean(link.cut);
        out.push(budget.token({ type: "link", ...link, title: target.title, children }));
        // CommonMark: links do not nest; earlier `[` stay text.
        brackets.length = 0;
        liveBrackets = 0;
      } else {
        // Any other scheme, a relative address, a user name in it: the text and the address.
        out.push(budget.token({ type: "text", text: "[" }));
        out.push(...children);
        out.push(budget.token({ type: "text", text: `](${target.address}${target.title !== null ? ` "${target.title}"` : ""})` }));
      }
      if (link && target.title !== null && !opener.image) out.push(budget.token({ type: "text", text: ` (title: ${target.title})` }));
      i = target.end;
      continue;
    }
    if (ch === "*" || ch === "_") {
      let end = i;
      while (text[end] === ch) end += 1;
      const count = end - i;
      const before = i === 0 ? undefined : text[i - 1];
      const after = text[end];
      const leftFlanking = !isWhite(after) && (!isPunct(after) || isWhite(before) || isPunct(before));
      const rightFlanking = !isWhite(before) && (!isPunct(before) || isWhite(after) || isPunct(after));
      const canOpen = ch === "*" ? leftFlanking : leftFlanking && (!rightFlanking || isPunct(before));
      const canClose = ch === "*" ? rightFlanking : rightFlanking && (!leftFlanking || isPunct(after));
      let matched = -1;
      if (canClose && count <= 3) {
        // Only the top few open runs are searched (at most MAX_INLINE_DEPTH).
        for (let d = delims.length - 1; d >= 0; d -= 1) {
          budget.steps += 1;
          if (delims[d].ch === ch && delims[d].count === count) {
            matched = d;
            break;
          }
        }
      }
      const floor = brackets.length ? brackets[brackets.length - 1].delims : 0;
      if (matched >= floor) {
        flush();
        const opener = delims[matched];
        delims.length = matched;
        const children = out.splice(opener.index + 1);
        out.pop(); // the opener's text
        let node = { type: count === 1 ? "em" : "strong", children };
        if (count === 3) node = { type: "em", children: [budget.token({ type: "strong", children })] };
        out.push(budget.token(node));
        i = end;
        continue;
      }
      if (canOpen && count <= 3 && delims.length + liveBrackets < MAX_INLINE_DEPTH) {
        flush();
        out.push(budget.token({ type: "text", text: text.slice(i, end) }));
        delims.push({ ch, count, index: out.length - 1 });
        i = end;
        continue;
      }
      add(text.slice(i, end));
      i = end;
      continue;
    }
    add(ch);
    i += 1;
  }
  flush();
  budget.noted = noted;
  return mergeText(out);
}

/** Adjacent text tokens joined (unmatched openers leave split runs). */
function mergeText(nodes) {
  const merged = [];
  for (const node of nodes) {
    const last = merged[merged.length - 1];
    if (node.type === "text" && last && last.type === "text") last.text += node.text;
    else merged.push(node);
  }
  return merged;
}

// -- the document -----------------------------------------------------------------------------

/**
 * `source` as {blocks, fallback, steps}. `fallback` is null, or the reason the whole document
 * is shown as source instead (over MAX_TOKENS).
 */
export function parse(source) {
  const budget = new Budget();
  const lines = String(source ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = parseBlocks(lines, 0, budget);
  if (budget.over) {
    return { blocks: [], fallback: `Shown as source: over ${MAX_TOKENS.toLocaleString("en-US")} formatting elements.`, steps: budget.steps };
  }
  return { blocks, fallback: null, steps: budget.steps };
}

/** Every token in a parsed document, depth first: for tests and the renderer's checks. */
export function* walk(blocks) {
  for (const block of blocks || []) {
    yield block;
    if (block.children) yield* walk(block.children);
    if (block.items) for (const item of block.items) yield* walk(item);
  }
}
