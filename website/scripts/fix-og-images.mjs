/**
 * Regenerates the 4 branded PNGs, replacing:
 *   agent-breeder.com  →  agentbreeder.io
 *   github.com/rajitsaha/agentbreeder  →  github.com/agentbreeder/agentbreeder
 *
 * Uses sharp to composite an SVG text patch over each image.
 * Run: node scripts/fix-og-images.mjs
 */

import sharp from 'sharp';
import { readFileSync, existsSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dir = dirname(fileURLToPath(import.meta.url));
const pub = resolve(__dir, '../public');

/**
 * Build an SVG overlay that paints a filled rectangle (to erase old text)
 * then renders new text on top.
 *
 * @param {object[]} patches - array of { x, y, w, h, text, fontSize, color, bg, align }
 * @param {number} imgW - image width
 * @param {number} imgH - image height
 */
function makeSvgOverlay(patches, imgW, imgH) {
  const rects = patches.map(({ x, y, w, h, bg = '#09090b' }) =>
    `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${bg}" />`
  ).join('\n');

  const texts = patches.map(({ x, y, h, text, fontSize = 13, color = '#52525b', align = 'left', w }) => {
    const tx = align === 'right' ? x + w : align === 'center' ? x + w / 2 : x + 6;
    const anchor = align === 'right' ? 'end' : align === 'center' ? 'middle' : 'start';
    return `<text x="${tx}" y="${y + h / 2 + fontSize * 0.35}" font-family="ui-monospace, 'SF Mono', monospace" font-size="${fontSize}" fill="${color}" text-anchor="${anchor}">${text}</text>`;
  }).join('\n');

  return Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${imgW}" height="${imgH}">
${rects}
${texts}
</svg>`);
}

async function patchImage(srcPath, patches) {
  if (!existsSync(srcPath)) {
    console.warn(`  ⚠  Not found: ${srcPath}`);
    return;
  }
  const img = sharp(srcPath);
  const { width: w, height: h } = await img.metadata();
  const overlay = makeSvgOverlay(patches, w, h);
  await img
    .composite([{ input: overlay, top: 0, left: 0 }])
    .toFile(srcPath + '.tmp');

  // Atomic replace
  const { renameSync } = await import('fs');
  renameSync(srcPath + '.tmp', srcPath);
  console.log(`  ✓  Patched ${srcPath.replace(pub + '/', 'public/')}`);
}

async function main() {
  console.log('Patching branded PNGs…\n');

  // ── og.png & social-preview.png ──────────────────────────────────────────
  // Both have the same layout: bottom-right domain + bottom-left github URL
  // They're 1200×630. Patch two areas.
  for (const name of ['og.png', 'social-preview.png']) {
    await patchImage(resolve(pub, name), [
      // Bottom-right: old domain  (approx x=1010, y=593, w=175, h=22)
      { x: 990, y: 590, w: 200, h: 28, text: 'agentbreeder.io', fontSize: 12, color: '#52525b', align: 'right' },
      // Bottom-left: old github URL (approx x=15, y=593, w=320, h=22)
      { x: 12, y: 590, w: 340, h: 28, text: 'github.com/agentbreeder/agentbreeder', fontSize: 12, color: '#52525b', align: 'left' },
    ]);
  }

  // ── blog hero images ──────────────────────────────────────────────────────
  // Both 1200×630 (or similar). Bottom-right domain only.
  for (const slug of ['agentbreeder-vs-competitors', 'why-i-built-agentbreeder']) {
    await patchImage(resolve(pub, `blog/${slug}/hero.png`), [
      { x: 990, y: 590, w: 200, h: 28, text: 'agentbreeder.io', fontSize: 12, color: '#52525b', align: 'right' },
    ]);
  }

  console.log('\nDone. Verify images look correct before committing.');
}

main().catch(err => { console.error(err); process.exit(1); });
