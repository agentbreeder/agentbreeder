// The safe Markdown parser for a skill's SKILL.md and references (#1059 UI-R, composition console
// spec §9.3): the XSS corpus (only allowed token types, only secure web and mail links), the
// growth test, the sink check, links, entities, titles and definitions, heading levels, the
// nesting caps and the token-cap fallback. tests/ui/test_skills.py renders the same corpus in a
// browser and checks the DOM.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  BLOCK_TYPES, INLINE_TYPES, MAX_BLOCK_DEPTH, MAX_INLINE_DEPTH, MAX_TOKENS, linkTarget, parse, plainText, walk,
} from "../../src/agentbreeder/center/static/lib/markdown.js";

const STATIC = new URL("../../src/agentbreeder/center/static/", import.meta.url);
const SECURE = "https" + "://";

export const CORPUS = [
  // raw HTML and the OWASP filter-evasion shapes: never markup, always their characters
  "<script>alert(1)</script>",
  "<img src=x onerror=alert(1)>",
  "<svg/onload=alert(1)>",
  "<iframe src=javascript:alert(1)></iframe>",
  '<a href="javascript:alert(1)">x</a>',
  "<IMG SRC=JaVaScRiPt:alert('XSS')>",
  "<IMG SRC=&#106;&#97;&#118;&#97;&#115;&#99;&#114;&#105;&#112;&#116;&#58;alert(1)>",
  "<style>@import 'x';</style>",
  "<!-- comment --><b onmouseover=alert(1)>hi</b>",
  "<div style=\"background:url(javascript:alert(1))\">x</div>",
  // links with schemes that are never links
  "[x](javascript:alert(1))",
  "[x](JaVaScRiPt:alert(1))",
  "[x](java\tscript:alert(1))",
  "[x](&#106;avascript:alert(1))",
  "[x](javascript&#58;alert(1))",
  "[x](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
  "[x](vbscript:msgbox(1))",
  "[x](file:///etc/passwd)",
  "[x](http://plain.example/)",
  "[x](//evil.example/)",
  "[x](/relative/path)",
  "<javascript:alert(1)>",
  "<data:text/html,<script>alert(1)</script>>",
  // images: text, never fetched
  `![a tracking pixel](${SECURE}evil.example/p.png)`,
  `![x](javascript:alert(1))`,
  // reference-style links and definitions: visible, never resolved
  `[click][1]\n\n[1]: ${SECURE}evil.example/ "title"`,
  // bidi-spoofed link text and a userinfo address
  `[\u202emoc.elgoog\u202c](${SECURE}evil.example/)`,
  `[europa.eu](${SECURE}europa.eu@evil.example/)`,
  // an IDN homograph host and link text that names another host
  `[apple.com](${SECURE}аpple.com/)`,
  `[${SECURE}bank.example](${SECURE}evil.example/)`,
  // entities and titles
  `[x](${SECURE}ok.example/ "a title") &lt;b&gt; &amp;`,
];

const ALLOWED_SCHEMES = new Set(["https", "mailto"]);

function check(source) {
  const { blocks, fallback } = parse(source);
  assert.equal(fallback, null, source);
  for (const token of walk(blocks)) {
    assert.ok(BLOCK_TYPES.includes(token.type) || INLINE_TYPES.includes(token.type), `${token.type} in ${source}`);
    if (token.type === "link") {
      assert.ok(ALLOWED_SCHEMES.has(token.scheme), source);
      const url = new URL(token.href);
      assert.ok(url.protocol === "https:" || url.protocol === "mailto:", token.href);
      assert.equal(url.username + url.password, "", token.href);
    }
  }
  return blocks;
}

test("the XSS corpus yields only allowed tokens and secure web or mail links", () => {
  for (const source of CORPUS) check(source);
});

test("raw HTML and entities stay as their characters", () => {
  const [block] = check("<script>alert(1)</script> &amp; &lt;b&gt; &#106;");
  assert.equal(plainText(block.children), "<script>alert(1)</script> &amp; &lt;b&gt; &#106;");
});

test("a scheme that is not a secure web or mail link is text with its address", () => {
  for (const source of ["[x](javascript:alert(1))", "[x](http://plain.example/)", "[x](/relative)", "[x](data:text/html,hi)"]) {
    const [block] = check(source);
    assert.ok(block.children.every((node) => node.type === "text"), source);
    assert.ok(plainText(block.children).includes("]("), source);
  }
});

test("an image is a visible note, never a link or a fetch", () => {
  const [block] = check(`![a tracking pixel](${SECURE}evil.example/p.png)`);
  assert.deepEqual(block.children, [{ type: "text", text: "[image: a tracking pixel, not shown]" }]);
});

test("titles and reference definitions are visible", () => {
  const [para] = check(`[x](${SECURE}ok.example/ "a title")`);
  assert.equal(para.children[0].type, "link");
  assert.equal(plainText(para.children.slice(1)), " (title: a title)");
  const blocks = check(`[click][1]\n\n[1]: ${SECURE}evil.example/`);
  assert.equal(plainText(blocks[0].children), "[click][1]");
  assert.deepEqual(blocks[1], { type: "literal", text: `[1]: ${SECURE}evil.example/`, block: false });
});

test("link targets: the full destination, punycode, userinfo, cut, mailto", () => {
  assert.equal(linkTarget(`${SECURE}europa.eu@evil.example/`), null);
  assert.equal(linkTarget(`${SECURE}:pw@evil.example/`), null);
  assert.equal(linkTarget("http://plain.example/"), null);
  assert.equal(linkTarget("relative"), null);
  const idn = linkTarget(`${SECURE}аpple.com/`);
  assert.equal(idn.shown, `${SECURE}xn--pple-43d.com/`);
  const long = linkTarget(`${SECURE}ok.example/${"a".repeat(200)}`);
  assert.equal(long.cut, true);
  assert.equal(long.shown.length, 81);
  assert.ok(long.shown.endsWith("…"));
  assert.equal(long.href.length, `${SECURE}ok.example/`.length + 200);
  const mail = linkTarget("mailto:help@ok.example");
  assert.deepEqual([mail.scheme, mail.shown], ["mailto", "help@ok.example"]);
});

test("autolinks follow the same rules", () => {
  const [block] = check(`<${SECURE}ok.example/a> <mailto:a@ok.example> <javascript:alert(1)> <b>`);
  const links = block.children.filter((node) => node.type === "link");
  assert.deepEqual(links.map((l) => l.scheme), ["https", "mailto"]);
  assert.ok(plainText(block.children).includes("<javascript:alert(1)>"));
  assert.ok(plainText(block.children).includes("<b>"));
});

test("the supported subset", () => {
  const blocks = check("# A\n## B\n### C\n#### D\n\nP *em* **strong** ***both*** `code`  \nline\n\n- a\n- b\n\n3. c\n4. d\n\n> q\n\n---\n\n```\nfenced\n```\n\n    indented\n\n| a | b |\n|---|---|\n| 1 | 2 |");
  assert.deepEqual(blocks.map((b) => b.type), ["heading", "heading", "heading", "heading", "paragraph", "list", "list", "quote", "hr", "code", "code", "literal"]);
  assert.deepEqual(blocks.slice(0, 4).map((b) => b.level), [4, 5, 6, 6]);
  const para = blocks[4].children.map((n) => n.type);
  assert.deepEqual(para, ["text", "em", "text", "strong", "text", "em", "text", "code", "br", "text"]);
  assert.equal(blocks[6].start, 3);
  assert.equal(blocks[11].block, true); // a table: its source, as CC7 says
});

test("lists and quotes nest at most 4 deep; deeper markers stay text", () => {
  const quotes = parse("> ".repeat(MAX_BLOCK_DEPTH + 3) + "deep").blocks;
  let depth = 0;
  let node = quotes[0];
  while (node && node.type === "quote") {
    depth += 1;
    node = node.children[0];
  }
  assert.equal(depth, MAX_BLOCK_DEPTH);
  assert.equal(plainText(node.children), "> > > deep");
});

test("emphasis and links nest at most 8 deep", () => {
  const source = "*a ".repeat(MAX_INLINE_DEPTH + 4) + "x" + " a*".repeat(MAX_INLINE_DEPTH + 4);
  const { blocks } = parse(source);
  let deepest = 0;
  const depth = (nodes, d) => {
    for (const n of nodes) {
      deepest = Math.max(deepest, d);
      if (n.children) depth(n.children, d + 1);
    }
  };
  depth(blocks[0].children, 0);
  assert.ok(deepest <= MAX_INLINE_DEPTH, String(deepest));
  assert.ok(plainText(blocks[0].children).includes("x"));
});

test("past the token cap the whole document falls back to source", () => {
  const { blocks, fallback } = parse("*a* ".repeat(MAX_TOKENS));
  assert.deepEqual(blocks, []);
  assert.match(fallback, /over 5,000 formatting elements/);
  assert.equal(parse("*a* ".repeat(100)).fallback, null);
});

test("adversarial input: doubling it at most about doubles the work", () => {
  const shapes = {
    brackets: (n) => "[".repeat(n) + "](".repeat(n),
    emphasis: (n) => "*_".repeat(n) + "a",
    quotes: (n) => "> ".repeat(n) + "x",
    backticks: (n) => Array.from({ length: n }, (_, i) => "`".repeat((i % 50) + 1) + "a").join(""),
    spaces: (n) => "x" + " ".repeat(n) + "y  \nz",
    links: (n) => `[a](${SECURE}x.example/`.repeat(n),
    angles: (n) => "<".repeat(n) + "a",
    lists: (n) => "- ".repeat(n) + "x\n".repeat(n),
  };
  for (const [name, make] of Object.entries(shapes)) {
    const small = parse(make(2000)).steps;
    const large = parse(make(4000)).steps;
    assert.ok(large <= small * 2.3, `${name}: ${small} -> ${large}`);
  }
});

test("the renderer and the skill screens use no HTML sink", () => {
  const SINKS = /innerHTML|outerHTML|dangerouslySetInnerHTML|DOMParser|insertAdjacentHTML|document\.write|createContextualFragment|srcdoc/;
  for (const file of ["lib/markdown.js", "ui/markdown.js", "screens/skill.js"]) {
    assert.doesNotMatch(readFileSync(new URL(file, STATIC), "utf8"), SINKS, file);
  }
});


test("an autolink shows its destination as the browser uses it, the host in punycode", () => {
  const [block] = check(`<${SECURE}аpple.com/x>`);
  const [link] = block.children;
  assert.equal(link.href, `${SECURE}xn--pple-43d.com/x`);
  assert.equal(plainText(link.children), `${SECURE}xn--pple-43d.com/x`);
  const [encoded] = check(`<${SECURE}a.example/%E2%80%AE>`);
  assert.equal(plainText(encoded.children), `${SECURE}a.example/%E2%80%AE`);
});

test("links do not nest: an autolink inside link text stays text", () => {
  const [block] = check(`[<${SECURE}a.example>](${SECURE}b.example)`);
  const links = [...walk([block])].filter((t) => t.type === "link");
  assert.equal(links.length, 1);
  assert.equal(links[0].href, `${SECURE}b.example/`);
  assert.equal(plainText(links[0].children), `<${SECURE}a.example>`);
});

test("an escaped backslash at a line end is not a hard break; a typed one is", () => {
  const [escaped] = check("a\\\\\nb");
  assert.deepEqual(escaped.children, [{ type: "text", text: "a\\ b" }]);
  const [typed] = check("a\\\nb");
  assert.deepEqual(typed.children.map((n) => n.type), ["text", "br", "text"]);
  const [spaces] = check("a   \nb");
  assert.deepEqual(spaces.children.map((n) => n.type), ["text", "br", "text"]);
});

test("a mail link with a query or fragment is text", () => {
  assert.equal(linkTarget("mailto:x@ok.example?bcc=z@evil.example"), null);
  assert.equal(linkTarget("mailto:x@ok.example#x"), null);
  const [block] = check("[a](mailto:x@ok.example?body=hi)");
  assert.ok(block.children.every((n) => n.type === "text"));
});

test("past the nesting cap a `[` still pairs with its own `]`", () => {
  const [block] = check("[".repeat(MAX_INLINE_DEPTH + 1) + `x](${SECURE}a.example)` + "]".repeat(MAX_INLINE_DEPTH));
  const links = [...walk([block])].filter((t) => t.type === "link");
  assert.equal(links.length, 0);
  assert.ok(plainText(block.children).startsWith("[".repeat(MAX_INLINE_DEPTH + 1) + "x]("));
});

test("balanced parentheses stay in the address", () => {
  const [block] = check(`[a](${SECURE}w.example/Foo_(bar))`);
  assert.equal(block.children[0].href, `${SECURE}w.example/Foo_(bar)`);
  assert.equal(block.children.length, 1);
});

test("a cut destination says 'full address in Source' once per file", () => {
  const long = `${SECURE}ok.example/${"a".repeat(100)}`;
  const [block] = check(`[a](${long}) [b](${long}) <${long}>`);
  const links = block.children.filter((n) => n.type === "link");
  assert.deepEqual(links.map((l) => Boolean(l.note)), [true, false, false]);
});

test("many short lines in one paragraph: doubling the input about doubles the time", () => {
  const time = (n) => {
    const source = "a\n".repeat(n);
    const start = performance.now();
    for (let k = 0; k < 3; k += 1) parse(source);
    return performance.now() - start;
  };
  time(4000); // warm up
  const small = time(16000);
  const large = time(64000);
  assert.ok(large < small * 4 * 3, `${small.toFixed(1)} ms -> ${large.toFixed(1)} ms for 4x the input`);
});
