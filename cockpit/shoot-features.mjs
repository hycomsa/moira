// Screenshots: report modal, lineage→artifact modal, files viewer.
import puppeteer from "puppeteer-core";
const BASE = process.argv[2] || "http://localhost:8799";
const OUT = process.argv[3] || "/tmp/moira-feat";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const b = await puppeteer.launch({
  executablePath: "/usr/bin/google-chrome", headless: "new",
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--force-color-profile=srgb"],
  defaultViewport: { width: 1680, height: 1050, deviceScaleFactor: 2 },
});
const page = await b.newPage();
const clickText = (sel, re) => page.evaluate((s, r) => {
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

  // ---- Runs -> select run -> Report modal
  await clickText(".nav-item", /runs/);
  await page.waitForSelector(".run-row", { timeout: 12000 });
  await page.evaluate(() => {
    const r = [...document.querySelectorAll(".run-row")].find((e) => e.textContent.includes("70d8c13ac540"))
            || document.querySelector(".run-row");
    r.click();
  });
  await page.waitForSelector(".plan .plan-node", { timeout: 12000 });
  await sleep(500);
  await clickText("button", /report/);
  await page.waitForSelector(".artifact-text", { timeout: 20000 });
  await sleep(500);
  await page.screenshot({ path: `${OUT}-report.png` });
  await clickText(".modal-foot button, button", /done/);
  await sleep(400);

  // ---- click a node, then a lineage chip -> artifact modal
  await page.evaluate(() => {
    const n = [...document.querySelectorAll(".plan .plan-node")].find((e) => /front|implement/i.test(e.textContent || ""));
    (n || document.querySelector(".plan-node")).click();
  });
  await page.waitForSelector(".lineage-chips .chip-btn", { timeout: 8000 });
  await page.evaluate(() => {
    const c = [...document.querySelectorAll(".chip-btn")].find((e) => /REQ-/.test(e.textContent || ""))
            || document.querySelector(".chip-btn");
    c.click();
  });
  await page.waitForSelector(".artifact-text", { timeout: 8000 });
  await sleep(400);
  await page.screenshot({ path: `${OUT}-lineage.png` });
  await clickText("button", /close/);
  await sleep(400);

  // ---- Files page: navigate to the onboarding screen
  await clickText(".nav-item", /files/);
  await page.waitForSelector(".file-tree .file-row", { timeout: 10000 });
  for (const seg of ["src", "screens", "onboarding"]) {
    const ok = await page.evaluate((name) => {
      const row = [...document.querySelectorAll(".file-row")].find((e) => e.querySelector(".fn")?.textContent === name);
      if (row) row.click(); return !!row;
    }, seg);
    if (!ok) break;
    await sleep(500);
  }
  await page.evaluate(() => {
    const row = [...document.querySelectorAll(".file-row")].find((e) => /OnboardingScreen\.tsx/.test(e.querySelector(".fn")?.textContent || ""));
    if (row) row.click();
  });
  await page.waitForSelector(".file-view", { timeout: 8000 });
  await sleep(400);
  await page.screenshot({ path: `${OUT}-files.png` });
  console.log("saved:", `${OUT}-report.png`, `${OUT}-lineage.png`, `${OUT}-files.png`);
} catch (e) {
  console.error("shoot failed:", e.message);
  await page.screenshot({ path: `${OUT}-error.png` }).catch(() => {});
  process.exitCode = 1;
} finally { await b.close(); }
