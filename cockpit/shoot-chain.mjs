import puppeteer from "puppeteer-core";
const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));
const RID="run-395a2af3011a";
const b=await puppeteer.launch({executablePath:"/usr/bin/google-chrome",headless:"new",args:["--no-sandbox","--force-color-profile=srgb"],defaultViewport:{width:1680,height:1000,deviceScaleFactor:2}});
const p=await b.newPage();
try{
  await p.goto("http://localhost:8799/",{waitUntil:"networkidle2",timeout:30000});
  await p.waitForFunction(()=>[...document.querySelectorAll("select")].some(s=>[...s.options].some(o=>o.value==="csl-driver")),{timeout:15000});
  await p.evaluate(()=>{const s=[...document.querySelectorAll("select")].find(s=>[...s.options].some(o=>o.value==="csl-driver"));const set=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,"value").set;set.call(s,"csl-driver");s.dispatchEvent(new Event("change",{bubbles:true}));});
  await sleep(700);
  await p.evaluate(()=>{const n=[...document.querySelectorAll(".nav-item")].find(e=>/runs/i.test(e.textContent||""));if(n)n.click();});
  await p.waitForSelector(".run-row",{timeout:12000});
  await p.evaluate((rid)=>{const r=[...document.querySelectorAll(".run-row")].find(e=>e.textContent.includes(rid.replace("run-","")));if(r)r.click();},RID);
  await p.waitForSelector(".chain-badge",{timeout:8000}); await sleep(800);
  await p.evaluate(()=>{const n=[...document.querySelectorAll(".plan .plan-node")].find(e=>/front|implement/i.test(e.textContent||""));if(n)n.click();});
  await sleep(500);
  await p.screenshot({path:"/tmp/moira-chain.png"});
  console.log("badge:", await p.evaluate(()=>document.querySelector(".chain-badge")?.textContent));
}catch(e){console.error("fail:",e.message);await p.screenshot({path:"/tmp/moira-chain-error.png"}).catch(()=>{});process.exitCode=1;}
finally{await b.close();}
