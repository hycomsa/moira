import puppeteer from "puppeteer-core";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const b = await puppeteer.launch({ executablePath:"/usr/bin/google-chrome", headless:"new",
  args:["--no-sandbox","--force-color-profile=srgb"], defaultViewport:{width:1680,height:900,deviceScaleFactor:2}});
const p = await b.newPage();
try {
  await p.goto("http://localhost:8799/",{waitUntil:"networkidle2",timeout:30000});
  await p.waitForFunction(()=>[...document.querySelectorAll("select")].some(s=>[...s.options].some(o=>o.value==="csl-driver")),{timeout:15000});
  await p.evaluate(()=>{const s=[...document.querySelectorAll("select")].find(s=>[...s.options].some(o=>o.value==="csl-driver"));const set=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,"value").set;set.call(s,"csl-driver");s.dispatchEvent(new Event("change",{bubbles:true}));});
  await sleep(700);
  await p.evaluate(()=>{const n=[...document.querySelectorAll(".nav-item")].find(e=>/traceability/i.test(e.textContent||""));if(n)n.click();});
  await p.waitForSelector(".trace-card",{timeout:10000}); await sleep(500);
  await p.screenshot({path:"/tmp/moira-trace.png"});
  console.log("saved /tmp/moira-trace.png");
} catch(e){ console.error("fail:",e.message); await p.screenshot({path:"/tmp/moira-trace-error.png"}).catch(()=>{}); process.exitCode=1; }
finally { await b.close(); }
