import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// Check the CSS the generated homepage actually links to, not just the source
// files: a restored build cache can leave these out of step with one another.
export async function verifyHomepageBuild(buildDir) {
  const html = await readFile(path.join(buildDir, 'server/app/index.html'), 'utf8');
  const links = html.match(/<link\b[^>]*>/gi) ?? [];
  const stylesheets = links.flatMap((link) => {
    if (!/\brel=["']stylesheet["']/i.test(link)) return [];
    const href = link.match(/\bhref=["']([^"']+)["']/i)?.[1];
    assert(href?.startsWith('/_next/static/'), 'Homepage stylesheet must be a local Next.js asset');
    return [href.split(/[?#]/)[0].replace('/_next/', '')];
  });
  assert(stylesheets.length > 0, 'Homepage has no linked stylesheet');

  const css = (await Promise.all(stylesheets.map((file) => readFile(path.join(buildDir, file), 'utf8')))).join('\n');
  for (const name of ['ab-container', 'ab-btn-primary', 'ab-island', 'glass', 'hero-title', 'gate-underline']) {
    assert(new RegExp(`\\.${name}(?=[\\s,{:])`).test(css), `Homepage stylesheet is missing .${name}; refusing a stale CSS build`);
  }
  for (const name of ['bg', 'bg-surface', 'bg-elevated', 'text', 'text-dim']) {
    assert(!new RegExp(`var\\(\\s*--${name}\\s*\\)`).test(css), `Homepage stylesheet contains legacy --${name} overrides`);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const buildDir = process.argv[2] ?? fileURLToPath(new URL('../.next', import.meta.url));
  await verifyHomepageBuild(buildDir);
  console.log('Homepage build verified: current layout, buttons, themes and code panels.');
}
