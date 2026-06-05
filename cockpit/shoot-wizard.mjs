// Screenshot the guided-run wizard at each step.
import puppeteer from "puppeteer-core";
const BASE = process.argv[2] || "http://localhost:8799";
const OUT = process.argv[3] || "/tmp/moira-wizard";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
  executablePath: "/usr/bin/google-chrome", headless: "new",
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--force-color-profile=srgb"],
  defaultViewport: { width: 1680, height: 1050, deviceScaleFactor: 2 },
});
const page = await browser.newPage();
const clickByText = (sel, re) => page.evaluate((s, r) => {
  const el = [...document.querySelectorAll(s)].find((e) => new RegExp(r, "i").test(e.textContent || ""));
  if (el) el.click(); return !!el;
}, sel, re.source);

try {
  await page.goto(BASE + "/", { waitUntil: "networkidle2", timeout: 30000 });
  await page.waitForFunction(() =>
    [...document.querySelectorAll("select")].some((s) => [...s.options].some((o) => o.value === "csl-driver")), { timeout: 15000 });
  await page.evaluate(() => {
    const sel = [...document.querySelectorAll("select")].find((s) => [...s.options].some((o) => o.value === "csl-driver"));
    const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
    setter.call(sel, "csl-driver"); sel.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await sleep(700);
  await clickByText(".nav-item", /runs/);
  await sleep(600);
  await clickByText("button", /guided run/);
  await page.waitForSelector(".wiz-steps", { timeout: 10000 });
  await page.waitForSelector(".opt-card.wide", { timeout: 10000 });

  // step 1: pick func
  await page.evaluate(() => document.querySelector(".opt-card.wide")?.click());
  await sleep(300);
  await page.screenshot({ path: `${OUT}-1-spec.png` });

  // -> step 2 pipeline
  await clickByText(".modal button, button", /^next$/);
  await page.waitForSelector(".opt-card.wide", { timeout: 8000 });
  await page.evaluate(() => {
    const c = [...document.querySelectorAll(".opt-card.wide")].find((e) => /react native/i.test(e.textContent || ""))
            || document.querySelector(".opt-card.wide");
    c?.click();
  });
  await page.waitForSelector(".plan-preview", { timeout: 8000 });
  await sleep(300);
  await page.screenshot({ path: `${OUT}-2-pipeline.png` });

  // -> step 3 backend -> step 4 review
  await clickByText("button", /^next$/); await sleep(300);
  await clickByText("button", /^next$/);
  await page.waitForSelector(".rev-row", { timeout: 8000 });
  await sleep(300);
  await page.screenshot({ path: `${OUT}-3-review.png` });
  console.log("saved 3 wizard screenshots:", OUT);
} catch (e) {
  console.error("shoot failed:", e.message);
  await page.screenshot({ path: `${OUT}-error.png` }).catch(() => {});
  process.exitCode = 1;
} finally { await browser.close(); }
