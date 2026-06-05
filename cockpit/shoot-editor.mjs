import puppeteer from "puppeteer-core";
const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));
const b=await puppeteer.launch({executablePath:"/usr/bin/google-chrome",headless:"new",args:["--no-sandbox","--force-color-profile=srgb"],defaultViewport:{width:1680,height:1000,deviceScaleFactor:2}});
const p=await b.newPage();
try{
  await p.goto("http://localhost:8799/",{waitUntil:"networkidle2",timeout:30000});
  await p.waitForFunction(()=>[...document.querySelectorAll("select")].some(s=>[...s.options].some(o=>o.value==="csl-driver")),{timeout:15000});
  await p.evaluate(()=>{const s=[...document.querySelectorAll("select")].find(s=>[...s.options].some(o=>o.value==="csl-driver"));const set=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,"value").set;set.call(s,"csl-driver");s.dispatchEvent(new Event("change",{bubbles:true}));});
  await sleep(700);
  await p.evaluate(()=>{const n=[...document.querySelectorAll(".nav-item")].find(e=>/pipelines/i.test(e.textContent||""));if(n)n.click();});
  // pick sdlc-rn-mobile in the builder select
  await p.waitForSelector(".bld-select",{timeout:10000});
  await p.evaluate(()=>{const s=document.querySelector(".bld-select");const o=[...s.options].find(o=>/rn-mobile/.test(o.value));if(o){const set=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,"value").set;set.call(s,o.value);s.dispatchEvent(new Event("change",{bubbles:true}));}});
  await p.waitForSelector(".rf-node2",{timeout:10000}); await sleep(1400);
  // select the implement (producer) node
  await p.evaluate(()=>{const n=[...document.querySelectorAll(".rf-node2 .rfn-title")].find(e=>/front|implement|developer/i.test(e.textContent||""));if(n)n.closest(".rf-node2").click();});
  await sleep(700);
  await p.screenshot({path:"/tmp/moira-editor-producer.png"});
  // select a gate node
  await p.evaluate(()=>{const n=[...document.querySelectorAll(".rf-node2")].find(e=>/gate/i.test(e.querySelector(".rfn-badge")?.textContent||""));if(n)n.click();});
  await sleep(700);
  await p.screenshot({path:"/tmp/moira-editor-gate.png"});
  console.log("saved editor screenshots");
}catch(e){console.error("fail:",e.message);await p.screenshot({path:"/tmp/moira-editor-error.png"}).catch(()=>{});process.exitCode=1;}
finally{await b.close();}
