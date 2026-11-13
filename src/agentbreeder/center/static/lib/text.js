// Untrusted text for display (S-35, console spec §10). Agent replies, log lines, notes and file
// contents can carry terminal escapes (ESC [ ... m), other C0/C1 controls and bidi overrides that
// make a line read differently from its bytes. Each such character becomes a visible escape:
// `\x1b` for a C0/C1 control or DEL, `U+202E` for a bidi or line-separator control. Line feeds and
// tabs stay as they are; a CR directly before a LF is dropped (CRLF line endings); a lone CR is
// shown as `\r`. The copy form is the same escaped text, so Copy never puts a raw ESC on the
// clipboard for a terminal to interpret.

// Characters that change how a line reads without being seen: bidi embeddings, overrides and
// isolates, the implicit marks, the Arabic letter mark, the line and paragraph separators, and the
// invisible format characters (soft hyphen, Mongolian vowel separator, zero-width space, word
// joiner and invisible operators, the deprecated format controls, the BOM, and the tag block that
// can smuggle hidden ASCII in model output). Zero-width joiner and non-joiner (U+200C/U+200D) are
// kept as themselves: emoji sequences and Persian and Indic text need them.
const FORMAT = new Set([0x00ad, 0x061c, 0x180e, 0x200b, 0x200e, 0x200f, 0x2028, 0x2029, 0xfeff]);
const FORMAT_RANGES = [
  [0x202a, 0x202e],
  [0x2060, 0x2064],
  [0x2066, 0x206f],
  [0xe0000, 0xe007f],
];
const isFormat = (code) => FORMAT.has(code) || FORMAT_RANGES.some(([low, high]) => code >= low && code <= high);

const hex = (code, width) => code.toString(16).toUpperCase().padStart(width, "0");

/** The visible escape for one code point, or null when it is shown as itself. */
export function escapeFor(code) {
  if (code === 0x0a || code === 0x09) return null;
  if (code < 0x20 || (code >= 0x7f && code <= 0x9f)) {
    if (code === 0x0d) return "\\r";
    return `\\x${hex(code, 2).toLowerCase()}`;
  }
  if (isFormat(code)) return `U+${hex(code, 4)}`;
  return null;
}

/**
 * Split text into runs: {text, escaped: false} for plain text and {text, escaped: true} for each
 * visible escape, so a view can style escapes apart from the text around them.
 */
export function segments(value) {
  const text = String(value ?? "").replace(/\r\n/g, "\n");
  const runs = [];
  let plain = "";
  for (const char of text) {
    const escape = escapeFor(char.codePointAt(0));
    if (escape === null) {
      plain += char;
      continue;
    }
    if (plain) runs.push({ text: plain, escaped: false });
    plain = "";
    runs.push({ text: escape, escaped: true });
  }
  if (plain) runs.push({ text: plain, escaped: false });
  return runs;
}

/** The escaped form as one string: what the view shows and what Copy copies. */
export function visible(value) {
  return segments(value)
    .map((run) => run.text)
    .join("");
}
