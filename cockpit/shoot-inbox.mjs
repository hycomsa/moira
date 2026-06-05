// Headless screenshot of the Inbox gate-review (evidence + diff + decide).
// Usage: node shoot-inbox.mjs <baseUrl> [outPrefix]
import puppeteer from "puppeteer-core";

const BASE = process.argv[2] || "http://localhost:8799";
const OUT = process.argv[3] || "/tmp/moira-inbox";
const CHROME = "/usr/bin/google-chrome";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
  executablePath: CHROME, headless: "new",
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--force-color-profile=srgb"],
  defaultViewport: { width: 1680, height: 1100, deviceScaleFactor: 2 },
});
const page = await browser.newPage();
try {
  await page.goto(BASE + "/", { waitUntil: "networkidle2", timeout: 30000 });
  await page.waitForFunction(() =>
    [...document.querySelectorAll("select")].some((s) =>
      [...s.options].some((o) => o.value === "csl-driver")), { timeout: 15000 });
  await page.evaluate(() => {
    const sel = [...document.querySelectorAll("select")].find((s) =>
      [...s.options].some((o) => o.value === "csl-driver"));
    const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
    setter.call(sel, "csl-driver");
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await sleep(800);
  await page.evaluate(() => {
    const it = [...document.querySelectorAll(".nav-item")].find((e) => /inbox/i.test(e.textContent || ""));
    if (it) it.click();
  });
  await page.waitForSelector(".inbox-card", { timeout: 15000 });
  await page.waitForSelector(".inbox-card .files", { timeout: 15000 }); // evidence loaded
  await page.evaluate(() => { const d = document.querySelector(".inbox-card details.patch"); if (d) d.open = true; });
  await sleep(600);
  await page.screenshot({ path: `${OUT}.png`, fullPage: true });
  console.log("saved:", `${OUT}.png`);
} catch (e) {
  console.error("shoot failed:", e.message);
  await page.screenshot({ path: `${OUT}-error.png` }).catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
}
