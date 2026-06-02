import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const pieceDir = path.resolve(__dirname, "..");
const outDir = path.resolve(pieceDir, "renders");
fs.mkdirSync(outDir, { recursive: true });

const W = Number(process.env.W || 4096);
const H = Number(process.env.H || 4096);

function nowSlug() {
  const d = new Date();
  const z = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${z(d.getMonth() + 1)}${z(d.getDate())}_${z(d.getHours())}${z(d.getMinutes())}${z(d.getSeconds())}`;
}

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: Math.min(W, 4096), height: Math.min(H, 4096) },
    deviceScaleFactor: 1,
  });

  // Serve via file:// (module imports from unpkg still work in chromium)
  const baseUrl = `file://${path.resolve(pieceDir, "index.html")}`;

  // First load: ask the page to compute a triptych, then read it.
  await page.goto(`${baseUrl}?triptych=1`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(400);

  const ids = await page.evaluate(() => window.__triptych || []);
  if (!Array.isArray(ids) || ids.length < 3) {
    throw new Error(`Could not compute triptych ids (got ${JSON.stringify(ids)})`);
  }

  const run = nowSlug();
  const out = [];

  for (let i = 0; i < 3; i++) {
    const id = ids[i];
    await page.goto(`${baseUrl}?sig=${encodeURIComponent(id)}`, { waitUntil: "domcontentloaded" });

    // Let the GPU settle and a couple frames render.
    await page.waitForTimeout(650);

    // Resize render buffer to target W/H for crisp export.
    await page.evaluate(
      ([w, h]) => {
        const c = document.querySelector("canvas");
        if (!c) return;
        // If three.js respects renderer.setSize(innerWidth, innerHeight),
        // we can force a resize by temporarily overriding window dimensions.
        // Here we use CSS sizing + manual canvas size as a best-effort.
        c.style.width = w + "px";
        c.style.height = h + "px";
      },
      [W, H]
    );

    await page.waitForTimeout(350);

    const filename = `${run}__${i + 1}__${id}__${W}x${H}.png`;
    const filePath = path.join(outDir, filename);

    const canvas = await page.locator("canvas").first();
    await canvas.screenshot({ path: filePath });
    out.push(filePath);
    console.log(`Wrote ${filePath}`);
  }

  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

