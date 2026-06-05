import puppeteer from "puppeteer-core";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const b = await puppeteer.launch({ executablePath:"/usr/bin/google-chrome", headless:"new",
  args:["--no-sandbox","--force-color-profile=srgb"], defaultViewport:{width:1500,height:950,deviceScaleFactor:2}});
const p = await b.newPage();
const clickRow = (name) => p.evaluate((n)=>{const r=[...document.querySelectorAll(".file-row")].find(e=>e.querySelector(".fn")?.textContent===n); if(r){r.click();return true;} return false;}, name);
try {
  await p.goto("http://localhost:8799/",{waitUntil:"networkidle2",timeout:30000});
  await p.waitForFunction(()=>[...document.querySelectorAll("select")].some(s=>[...s.options].some(o=>o.value==="csl-driver")),{timeout:15000});
  await p.evaluate(()=>{const s=[...document.querySelectorAll("select")].find(s=>[...s.options].some(o=>o.value==="csl-driver"));const set=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,"value").set;set.call(s,"csl-driver");s.dispatchEvent(new Event("change",{bubbles:true}));});
  await sleep(700);
  await p.evaluate(()=>{const n=[...document.querySelectorAll(".nav-item")].find(e=>/files/i.test(e.textContent||""));if(n)n.click();});
  await p.waitForSelector(".file-tree .file-row",{timeout:10000});
  await clickRow("src"); await sleep(500);
  await clickRow("screens"); await sleep(500);
  await clickRow("i18n"); await sleep(500);        // second sibling expanded — both should stay open
  await clickRow("onboarding"); await sleep(500);
  await clickRow("OnboardingScreen.tsx"); await sleep(600);
  await p.screenshot({path:"/tmp/moira-tree.png", fullPage:false});
  // report how many dir rows are open (▾)
  const open = await p.evaluate(()=>[...document.querySelectorAll(".file-row.dir .fi")].filter(e=>e.textContent==="▾").length);
  console.log("open dirs:", open, "-> saved /tmp/moira-tree.png");
} catch(e){ console.error("fail:",e.message); await p.screenshot({path:"/tmp/moira-tree-error.png"}).catch(()=>{}); process.exitCode=1; }
finally { await b.close(); }
