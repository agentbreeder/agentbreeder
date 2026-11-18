import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { verifyHomepageBuild } from './verify-build.mjs';

const run = promisify(execFile);
const script = fileURLToPath(new URL('./verify-build.mjs', import.meta.url));

const currentCss = '.ab-container{max-width:1200px}.ab-btn-primary{background:var(--primary)}.ab-island{background:var(--code-background)}.glass{background:var(--glass)}.hero-title{animation:ab-rise 500ms}.gate-underline{position:relative}';

async function fixture(t, css, href = '/_next/static/chunks/site.css') {
  const buildDir = await mkdtemp(path.join(tmpdir(), 'agentbreeder-build-test-'));
  t.after(() => rm(buildDir, { recursive: true, force: true }));
  await mkdir(path.join(buildDir, 'server/app'), { recursive: true });
  await mkdir(path.join(buildDir, 'static/chunks'), { recursive: true });
  await writeFile(path.join(buildDir, 'server/app/index.html'), `<html><head><link rel="stylesheet" href="${href}"></head><body><h1 class="hero-title">Know the agent.</h1></body></html>`);
  await writeFile(path.join(buildDir, 'static/chunks/site.css'), css);
  return buildDir;
}

test('accepts the homepage with its current stylesheet', async (t) => {
  await verifyHomepageBuild(await fixture(t, currentCss));
});

test('rejects the deployed failure: new HTML linked to legacy CSS', async (t) => {
  const buildDir = await fixture(t, 'body{background:var(--bg)}.hero-stagger>*{opacity:0}');
  await assert.rejects(verifyHomepageBuild(buildDir), /missing.*ab-container/i);
});

test('rejects legacy overrides even when current rules are also present', async (t) => {
  const buildDir = await fixture(t, `${currentCss}body{background:var(--bg);color:var(--text)}`);
  await assert.rejects(verifyHomepageBuild(buildDir), /legacy.*--bg/i);
});

test('checks the linked CSS rather than an unrelated fresh file', async (t) => {
  const buildDir = await fixture(t, currentCss, '/_next/static/chunks/missing.css');
  await assert.rejects(verifyHomepageBuild(buildDir), /ENOENT/);
});

test('rejects HTML without a stylesheet', async (t) => {
  const buildDir = await fixture(t, currentCss);
  await writeFile(path.join(buildDir, 'server/app/index.html'), '<h1>Know the agent.</h1>');
  await assert.rejects(verifyHomepageBuild(buildDir), /no.*stylesheet/i);
});

test('ignores font preload links and accepts a stylesheet query string', async (t) => {
  const buildDir = await fixture(t, currentCss);
  await writeFile(path.join(buildDir, 'server/app/index.html'), '<link rel="preload" href="/_next/static/media/font.woff2"><link rel="stylesheet" href="/_next/static/chunks/site.css?deployment=preview">');
  await verifyHomepageBuild(buildDir);
});

test('rejects an external stylesheet instead of inspecting an unrelated local file', async (t) => {
  const buildDir = await fixture(t, currentCss, 'https://example.com/site.css');
  await assert.rejects(verifyHomepageBuild(buildDir), /local Next.js asset/);
});

test('rejects a stylesheet link without an href', async (t) => {
  const buildDir = await fixture(t, currentCss);
  await writeFile(path.join(buildDir, 'server/app/index.html'), '<link rel="stylesheet">');
  await assert.rejects(verifyHomepageBuild(buildDir), /local Next.js asset/);
});

test('postbuild CLI succeeds from another working directory', async (t) => {
  const buildDir = await fixture(t, currentCss);
  const { stdout } = await run(process.execPath, [script, buildDir], { cwd: tmpdir() });
  assert.match(stdout, /Homepage build verified/);
});

test('postbuild CLI exits unsuccessfully when the linked stylesheet is stale', async (t) => {
  const buildDir = await fixture(t, 'body{background:var(--bg)}');
  await assert.rejects(run(process.execPath, [script, buildDir]), (error) => {
    assert.equal(error.code, 1);
    assert.match(error.stderr, /missing .ab-container/);
    return true;
  });
});

async function adapterFixture(t, css) {
  // The original tree is valid: checking it must not hide stale emitted assets.
  const buildDir = await fixture(t, currentCss);
  const output = path.join(buildDir, 'output');
  await mkdir(path.join(output, 'functions'), { recursive: true });
  await mkdir(path.join(output, 'static/_next/static/chunks'), { recursive: true });
  await writeFile(path.join(output, 'config.json'), '{"version":3}');
  await writeFile(path.join(output, 'functions/index.prerender-fallback.html'), '<link rel="stylesheet" href="/_next/static/chunks/site.css">');
  await writeFile(path.join(output, 'static/_next/static/chunks/site.css'), css);
  return buildDir;
}

test('accepts the Vercel adapter output after the raw homepage is removed', async (t) => {
  const buildDir = await adapterFixture(t, currentCss);
  await rm(path.join(buildDir, 'server'), { recursive: true });
  await verifyHomepageBuild(buildDir);
});

test('rejects stale emitted CSS even when the raw Next.js stylesheet is fresh', async (t) => {
  const buildDir = await adapterFixture(t, 'body{background:var(--bg)}');
  await assert.rejects(verifyHomepageBuild(buildDir), /missing.*ab-container/i);
});

test('rejects a missing emitted stylesheet instead of using the raw asset', async (t) => {
  const buildDir = await adapterFixture(t, currentCss);
  await rm(path.join(buildDir, 'output/static/_next/static/chunks/site.css'));
  await assert.rejects(verifyHomepageBuild(buildDir), /ENOENT/);
});

test('accepts the adapter static homepage variant', async (t) => {
  const buildDir = await adapterFixture(t, currentCss);
  await rm(path.join(buildDir, 'output/functions'), { recursive: true });
  await writeFile(path.join(buildDir, 'output/static/index.html'), '<link rel="stylesheet" href="/_next/static/chunks/site.css">');
  await verifyHomepageBuild(buildDir);
});

test('rejects missing emitted HTML even when the raw homepage exists', async (t) => {
  const buildDir = await adapterFixture(t, currentCss);
  await rm(path.join(buildDir, 'output/functions'), { recursive: true });
  await assert.rejects(verifyHomepageBuild(buildDir), /ENOENT/);
});
