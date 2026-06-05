// Headless screenshot of the run drill-down showing the per-step file diff.
// Usage: node shoot-diff.mjs <baseUrl> <runId|latest> [outPrefix]
import puppeteer from "puppeteer-core";

const BASE = process.argv[2] || "http://localhost:8799";
const RUN = process.argv[3] || "latest";
const OUT = process.argv[4] || "/tmp/moira-diff";
const CHROME = "/usr/bin/google-chrome";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
  executablePath: CHROME, headless: "new",
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--force-color-profile=srgb"],
  defaultViewport: { width: 1680, height: 1020, deviceScaleFactor: 2 },
});
const page = await browser.newPage();
page.on("console", (m) => { if (m.type() === "error") console.log("[page error]", m.text()); });

try {
  await page.goto(BASE + "/", { waitUntil: "networkidle2", timeout: 30000 });

  // 1) switch workspace to CSL Driver. The theme + workspace selects share a
  //    class, so locate the workspace one by its csl-driver option, then drive
  //    React's controlled <select> via the native value setter + bubbling change.
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
  await page.waitForFunction(() => {
    const sel = [...document.querySelectorAll("select")].find((s) =>
      [...s.options].some((o) => o.value === "csl-driver"));
    return sel && sel.value === "csl-driver";
  }, { timeout: 10000 });

  // 2) go to Runs
  await page.evaluate(() => {
    const it = [...document.querySelectorAll(".nav-item")].find((e) => /runs/i.test(e.textContent || ""));
    if (it) it.click();
  });
  await page.waitForSelector(".run-row", { timeout: 20000 });

  // 3) select the target run (or the top/most-recent one)
  await page.evaluate((run) => {
    const rows = [...document.querySelectorAll(".run-row")];
    const want = run !== "latest" ? rows.find((r) => (r.textContent || "").includes(run.replace("run-", ""))) : null;
    (want || rows[0])?.click();
  }, RUN);
  await page.waitForSelector(".plan .plan-node", { timeout: 15000 });
  await sleep(800);

  // 4) click the plan node that actually has a file diff (try each, keep the one with ul.files)
  const picked = await page.evaluate(async () => {
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const nodes = [...document.querySelectorAll(".plan .plan-node")];
    // prefer an implementation/frontend node first
    nodes.sort((a, b) => (/(front|implement|generat|dev)/i.test(b.textContent || "") ? 1 : 0)
                       - (/(front|implement|generat|dev)/i.test(a.textContent || "") ? 1 : 0));
    for (const n of nodes) {
      n.click();
      await sleep(450);
      if (document.querySelector(".audit ul.files")) return (n.textContent || "").trim();
    }
    return null;
  });
  console.log("node with diff:", picked);

  // 5) expand the diff <details>
  await page.evaluate(() => {
    const d = document.querySelector("details.patch");
    if (d) d.open = true;
  });
  await sleep(500);

  // 6) screenshots: full page + just the audit pane
  await page.screenshot({ path: `${OUT}-full.png`, fullPage: false });
  const aside = await page.$(".c-right");
  if (aside) await aside.screenshot({ path: `${OUT}-audit.png` });
  console.log("saved:", `${OUT}-full.png`, aside ? `${OUT}-audit.png` : "(no audit pane)");
} catch (e) {
  console.error("shoot failed:", e.message);
  await page.screenshot({ path: `${OUT}-error.png` }).catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
}
