// One-off PWA icon generator: renders the SafeRide bus mark with headless
// Chromium (Playwright) and writes the PNG set under public/icons/.
// Run from frontend/: node scripts/generate-icons.mjs
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.join(here, "..", "public", "icons");
mkdirSync(outDir, { recursive: true });

// Lucide "bus-front" outline, drawn in white on forest green (#206F4A).
const busGlyph = `
  <path d="M4 6 2 7" /><path d="M10 6h4" /><path d="m22 7-2-1" />
  <rect width="16" height="16" x="4" y="3" rx="2" />
  <path d="M4 11h16" /><path d="M8 15h.01" /><path d="M16 15h.01" />
  <path d="M6 19v2" /><path d="M18 21v-2" />
`;

function iconHtml({ size, glyphScale, rounded }) {
  const glyphSize = Math.round(size * glyphScale);
  const radius = rounded ? Math.round(size * 0.22) : 0;
  return `<!doctype html><html><body style="margin:0">
    <div id="icon" style="width:${size}px;height:${size}px;background:#206F4A;
        border-radius:${radius}px;display:flex;align-items:center;justify-content:center">
      <svg xmlns="http://www.w3.org/2000/svg" width="${glyphSize}" height="${glyphSize}"
           viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="1.7"
           stroke-linecap="round" stroke-linejoin="round">${busGlyph}</svg>
    </div></body></html>`;
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 600, height: 600 } });

async function shoot(file, opts) {
  await page.setContent(iconHtml(opts));
  await page.locator("#icon").screenshot({
    path: path.join(outDir, file),
    omitBackground: true,
  });
  console.log("wrote", file);
}

// "any" icons keep transparent rounded corners; the maskable icon fills the
// full square (the platform applies its own mask) with a smaller glyph.
await shoot("icon-512.png", { size: 512, glyphScale: 0.62, rounded: true });
await shoot("icon-192.png", { size: 192, glyphScale: 0.62, rounded: true });
await shoot("icon-maskable-512.png", { size: 512, glyphScale: 0.48, rounded: false });
await shoot("apple-touch-icon.png", { size: 180, glyphScale: 0.62, rounded: false });
await shoot("favicon-32.png", { size: 32, glyphScale: 0.7, rounded: false });

await browser.close();
