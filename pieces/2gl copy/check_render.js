import { chromium } from 'playwright';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  
  page.on('console', msg => console.log('BROWSER LOG:', msg.text()));
  page.on('pageerror', err => console.log('BROWSER ERROR:', err.message));

  try {
    console.log('Navigating to http://localhost:8000/ ...');
    await page.goto('http://localhost:8000/', { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000); // Wait for initial render
    await page.screenshot({ path: 'debug_render.png' });
    console.log('Screenshot saved to debug_render.png');
  } catch (e) {
    console.error('Failed to capture screenshot:', e);
  } finally {
    await browser.close();
  }
})();
