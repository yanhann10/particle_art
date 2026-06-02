import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { execSync } from "node:child_process";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const pieceDir = path.resolve(__dirname, "..");
const outDir = path.resolve(pieceDir, "recordings");
const framesDir = path.join(outDir, "frames");
fs.mkdirSync(framesDir, { recursive: true });

const W = 1280;
const H = 720;
const FPS = 30;
const LOOP_DURATION = 5; // seconds
const TOTAL_FRAMES = LOOP_DURATION * FPS;

async function main() {
  console.log("Launching browser...");
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: W, height: H },
    deviceScaleFactor: 1,
  });

  const baseUrl = "http://localhost:8000/"; // Assumes the server is serving index.html here
  console.log(`Navigating to ${baseUrl}...`);
  await page.goto(baseUrl, { waitUntil: "networkidle" });

  console.log("Setting up seamless loop...");
  await page.evaluate((duration) => {
    if (window.uniforms && window.uniforms.uLoop) {
      window.uniforms.uLoop.value = duration;
    }
    // Stop the normal requestAnimationFrame loop to control it manually
    window._manualControl = true;
    const oldLoop = window.loop;
    // Overwrite the loop function if possible, or just inject uTime
  }, LOOP_DURATION);

  console.log(`Capturing ${TOTAL_FRAMES} frames...`);
  const start = Date.now();

  for (let i = 0; i < TOTAL_FRAMES; i++) {
    const time = i / FPS;
    await page.evaluate((t) => {
      if (window.uniforms && window.uniforms.uTime) {
        window.uniforms.uTime.value = t;
      }
      // Re-render
      if (window.renderer && window.scene && window.camera) {
        window.renderer.render(window.scene, window.camera);
      }
    }, time);

    const framePath = path.join(framesDir, `frame_${String(i).padStart(4, "0")}.png`);
    await page.locator("canvas").first().screenshot({ path: framePath });

    if (i % 30 === 0) {
      console.log(`  Captured frame ${i}/${TOTAL_FRAMES}...`);
    }
  }

  console.log("Done capturing frames. Closing browser.");
  await browser.close();

  console.log("Stitching frames with ffmpeg...");
  const outputVideo = path.join(outDir, "loop.mp4");
  const ffmpegCmd = `ffmpeg -y -framerate ${FPS} -i "${framesDir}/frame_%04d.png" -c:v libx264 -pix_fmt yuv420p -crf 18 "${outputVideo}"`;
  
  try {
    execSync(ffmpegCmd);
    console.log(`Successfully created loop video: ${outputVideo}`);
    
    // Also create a GIF if possible
    const outputGif = path.join(outDir, "loop.gif");
    const gifCmd = `ffmpeg -y -i "${outputVideo}" -vf "fps=${FPS},scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" "${outputGif}"`;
    execSync(gifCmd);
    console.log(`Successfully created loop GIF: ${outputGif}`);

  } catch (err) {
    console.error("Error during ffmpeg processing:", err.message);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
