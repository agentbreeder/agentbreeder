import assert from "node:assert/strict";
import { test } from "node:test";

import { escapeFor, segments, visible } from "../../src/agentbreeder/center/static/lib/text.js";

test("plain text, line feeds and tabs pass through", () => {
  assert.equal(visible("hello\tworld\nnext"), "hello\tworld\nnext");
  assert.deepEqual(segments("abc"), [{ text: "abc", escaped: false }]);
});

test("a terminal escape becomes visible and the copy form has no raw ESC", () => {
  const line = "\x1b[31mred\x1b[0m";
  assert.equal(visible(line), "\\x1b[31mred\\x1b[0m");
  assert.ok(!visible(line).includes("\x1b"));
  assert.deepEqual(segments(line).filter((run) => run.escaped).map((run) => run.text), ["\\x1b", "\\x1b"]);
});

test("every C0 and C1 control and DEL is escaped", () => {
  for (let code = 0; code < 0x20; code += 1) {
    if (code === 0x0a || code === 0x09) continue;
    assert.notEqual(escapeFor(code), null, code.toString(16));
  }
  for (let code = 0x7f; code <= 0x9f; code += 1) assert.notEqual(escapeFor(code), null, code.toString(16));
  assert.equal(escapeFor(0x9b), "\\x9b"); // CSI
  assert.equal(escapeFor(0x00), "\\x00");
});

test("bidi controls show as code points", () => {
  // "abc" RLO "txt.exe" PDF: reads as "abcexe.txt" when rendered raw.
  assert.equal(visible("abc‮txt.exe‬"), "abcU+202Etxt.exeU+202C");
  for (const code of [0x061c, 0x200e, 0x200f, 0x2028, 0x2029, 0x2066, 0x2067, 0x2068, 0x2069]) {
    assert.match(escapeFor(code), /^U\+[0-9A-F]{4}$/);
  }
});

test("invisible format characters and tag characters show as code points", () => {
  for (const code of [0x00ad, 0x180e, 0x200b, 0x2060, 0x2064, 0x206a, 0x206f, 0xfeff]) {
    assert.match(escapeFor(code), /^U\+[0-9A-F]{4}$/, code.toString(16));
  }
  // "hi" smuggled in the tag block reads as nothing at all when rendered raw.
  assert.equal(visible("ok\u{E0068}\u{E0069}"), "okU+E0068U+E0069");
  assert.equal(escapeFor(0xe007f), "U+E007F");
});

test("joiners stay, so emoji sequences and Persian text render", () => {
  assert.equal(escapeFor(0x200c), null);
  assert.equal(escapeFor(0x200d), null);
  assert.equal(visible("👩‍💻"), "👩‍💻");
});

test("CRLF line endings are kept as lines; a lone CR is visible", () => {
  assert.equal(visible("a\r\nb"), "a\nb");
  assert.equal(visible("progress 10%\rprogress 90%"), "progress 10%\\rprogress 90%");
});

test("non-ASCII text that is not a control stays", () => {
  assert.equal(visible("café ✓ 東京 😀"), "café ✓ 東京 😀");
});

test("null and numbers render as text", () => {
  assert.equal(visible(null), "");
  assert.equal(visible(42), "42");
});
