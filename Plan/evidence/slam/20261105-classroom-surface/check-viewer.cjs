const { chromium } = require('D:/Code/radar/web/frontend/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:"msedge",args:['--enable-webgl','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('file:///D:/Code/radar/Plan/evidence/slam/20261105-classroom-surface/index.html');
 await page.waitForFunction(()=>{let d=document.querySelector('.js-plotly-plot');return d&&d._fullLayout&&d._fullData.length===6},{timeout:30000});
 await page.screenshot({path:'D:/Code/radar/Plan/evidence/slam/20261105-classroom-surface/browser-preview.png'});
 const data=await page.evaluate(()=>{let d=document.querySelector('.js-plotly-plot');return {traces:d.data.length,types:d.data.map(x=>x.type),buttons:d.layout.updatemenus[0].buttons.length,webgl:!!document.querySelector('canvas')}});
 if(errors.length||data.traces!==6||data.buttons!==3||!data.webgl)throw new Error(JSON.stringify({data,errors}));
 const fs=require('fs');fs.writeFileSync('D:/Code/radar/Plan/evidence/slam/20261105-classroom-surface/browser-check.json',JSON.stringify({data,errors},null,2));await browser.close();
})();
