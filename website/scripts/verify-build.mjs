import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// Check the CSS the generated homepage actually links to, not just the source
// files: a restored build cache can leave these out of step with one another.
export async function verifyHomepageBuild(buildDir) {
  let htmlPath = path.join(buildDir, 'server/app/index.html');
  let assetRoot = buildDir;
  let assetPrefix = '/_next/';
  // Vercel's onBuildComplete adapter emits deployment artifacts before npm
  // postbuild runs. Validate that output as a unit, never the original assets.
  const output = path.join(buildDir, 'output');
  if (existsSync(path.join(output, 'config.json'))) {
    const prerender = path.join(output, 'functions/index.prerender-fallback.html');
    htmlPath = existsSync(prerender) ? prerender : path.join(output, 'static/index.html');
    assetRoot = path.join(output, 'static');
    assetPrefix = '/';
  }
  const html = await readFile(htmlPath, 'utf8');
  const links = html.match(/<link\b[^>]*>/gi) ?? [];
  const stylesheets = links.flatMap((link) => {
    if (!/\brel=["']stylesheet["']/i.test(link)) return [];
    const href = link.match(/\bhref=["']([^"']+)["']/i)?.[1];
    assert(href?.startsWith('/_next/static/'), 'Homepage stylesheet must be a local Next.js asset');
    return [href.split(/[?#]/)[0].slice(assetPrefix.length)];
  });
  assert(stylesheets.length > 0, 'Homepage has no linked stylesheet');

  const css = (await Promise.all(stylesheets.map((file) => readFile(path.join(assetRoot, file), 'utf8')))).join('\n');
  for (const name of ['ab-container', 'ab-btn-primary', 'ab-island', 'glass', 'hero-title', 'gate-underline']) {
    assert(new RegExp(`\\.${name}(?=[\\s,{:])`).test(css), `Homepage stylesheet is missing .${name}; refusing a stale CSS build`);
  }
  for (const name of ['bg', 'bg-surface', 'bg-elevated', 'text', 'text-dim']) {
    assert(!new RegExp(`var\\(\\s*--${name}\\s*\\)`).test(css), `Homepage stylesheet contains legacy --${name} overrides`);
  }
}

// A link inside a link is invalid HTML: the parser closes the outer one early, so the server HTML no
// longer matches what React renders and every affected page throws hydration error #418 (#1129:
// fumadocs wraps the docs nav title in its own home link). Check every prerendered page.
export function findNestedAnchor(html) {
  const body = html.replace(/<(script|style|template)\b[\s\S]*?<\/\1\s*>/gi, '');
  let depth = 0;
  for (const [tag] of body.matchAll(/<\/?a(?=[\s>\/])[^>]*>/gi)) {
    if (tag.startsWith('</')) depth = Math.max(0, depth - 1);
    else if (++depth > 1) return tag;
  }
  return null;
}

async function htmlFiles(dir) {
  if (!existsSync(dir)) return [];
  const entries = await readdir(dir, { withFileTypes: true, recursive: true });
  return entries
    .filter((entry) => entry.isFile() && entry.name.endsWith('.html'))
    .map((entry) => path.join(entry.parentPath, entry.name));
}

export async function verifyNoNestedLinks(buildDir) {
  const files = [...(await htmlFiles(path.join(buildDir, 'server/app'))), ...(await htmlFiles(path.join(buildDir, 'output')))];
  assert(files.length > 0, 'No prerendered HTML found to check for nested links');
  for (const file of files) {
    const nested = findNestedAnchor(await readFile(file, 'utf8'));
    assert(!nested, `${path.relative(buildDir, file)} nests a link inside a link (${nested}); this breaks hydration (#1129)`);
  }
  return files.length;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const buildDir = process.argv[2] ?? fileURLToPath(new URL('../.next', import.meta.url));
  await verifyHomepageBuild(buildDir);
  console.log('Homepage build verified: current layout, buttons, themes and code panels.');
  const pages = await verifyNoNestedLinks(buildDir);
  console.log(`No nested links in ${pages} prerendered pages.`);
}
