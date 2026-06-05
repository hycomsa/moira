import puppeteer from "puppeteer-core";
const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));
const b=await puppeteer.launch({executablePath:"/usr/bin/google-chrome",headless:"new",args:["--no-sandbox","--force-color-profile=srgb"],
  defaultViewport:{width:390,height:844,deviceScaleFactor:3,isMobile:true,hasTouch:true}});
const p=await b.newPage();
try{
  await p.goto("http://localhost:8799/m",{waitUntil:"networkidle2",timeout:30000});
  await p.waitForSelector(".card",{timeout:8000}); await sleep(700);
  await p.screenshot({path:"/tmp/moira-mobile.png"});
  console.log("cards:", await p.evaluate(()=>document.querySelectorAll(".card").length));
}catch(e){console.error("fail:",e.message);await p.screenshot({path:"/tmp/moira-mobile-error.png"}).catch(()=>{});process.exitCode=1;}
finally{await b.close();}
