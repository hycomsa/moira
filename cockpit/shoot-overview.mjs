import puppeteer from "puppeteer-core";
const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));
const b=await puppeteer.launch({executablePath:"/usr/bin/google-chrome",headless:"new",args:["--no-sandbox","--force-color-profile=srgb"],defaultViewport:{width:1680,height:1080,deviceScaleFactor:2}});
const p=await b.newPage();
const setWs=async(id)=>{await p.evaluate((v)=>{const s=[...document.querySelectorAll("select")].find(s=>[...s.options].some(o=>o.value===v));const set=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,"value").set;set.call(s,v);s.dispatchEvent(new Event("change",{bubbles:true}));},id);};
try{
  await p.goto("http://localhost:8799/",{waitUntil:"networkidle2",timeout:30000});
  await p.waitForFunction(()=>[...document.querySelectorAll("select")].some(s=>[...s.options].some(o=>o.value==="csl-driver")),{timeout:15000});
  await setWs("csl-driver"); await sleep(1200);
  await p.evaluate(()=>{const n=[...document.querySelectorAll(".nav-item")].find(e=>/overview/i.test(e.textContent||""));if(n)n.click();});
  await p.waitForSelector(".hero",{timeout:8000}); await sleep(900);
  await p.screenshot({path:"/tmp/moira-overview.png"});
  console.log("saved /tmp/moira-overview.png");
}catch(e){console.error("fail:",e.message);await p.screenshot({path:"/tmp/moira-overview-error.png"}).catch(()=>{});process.exitCode=1;}
finally{await b.close();}
