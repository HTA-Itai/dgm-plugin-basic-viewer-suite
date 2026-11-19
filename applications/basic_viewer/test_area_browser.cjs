const assert=require("node:assert/strict");
const {spawnSync}=require("node:child_process");
const {pathToFileURL}=require("node:url");
const path=require("node:path"),fs=require("node:fs");
const {chromium}=require("playwright");
const [base,comparison,output,app=__dirname]=process.argv.slice(2);
if(!base||!comparison||!output)throw Error("Supply Base, Comp, output directory and optional app directory");
const source=`import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from engine import Store
s=Store(Path(sys.argv[2]),Path(sys.argv[3]) if sys.argv[3] else None)
op,args=sys.argv[4],json.loads(sys.argv[5])
v=s.index() if op=='index' else s.image(args['id']) if op=='image' else s.comparison_image(args['id']) if op=='comparison_image' else s.area_difference(args) if op=='area_difference' else s.contour_overlay(args) if op=='contour_overlay' else s.statistics(args)
print(json.dumps(v,allow_nan=False))`;
function command(op,args,single=false){
  const result=spawnSync("python3",["-c",source,app,base,single?"":comparison,op,JSON.stringify(args)],{maxBuffer:24*1024*1024});
  if(result.status!==0)throw Error(result.stderr.toString());
  return JSON.parse(result.stdout.toString());
}
(async()=>{
  fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({executablePath:"/usr/bin/google-chrome",headless:true,args:["--no-sandbox"]});
  try{
    const page=await browser.newPage({viewport:{width:2200,height:1400}});
    const errors=[],calls=[];
    const selectionDots=()=>page.evaluate(()=>{
      const original=paintSelectionDots,seen=[];
      paintSelectionDots=function(ctx,labels,scale){
        seen.push(labels.map(label=>({pid:label.pid,x:label.x,y:label.y})));
        return original(ctx,labels,scale);
      };
      try{drawImage();return seen;}finally{paintSelectionDots=original;}
    });
    let gate=null,release=null,rejectSelection=false,zero=false;
    page.on("pageerror",error=>errors.push(error.message));
    await page.exposeFunction("engineRequest",async(op,args)=>{
      calls.push({op,args});
      if(op==="area_difference"&&args.scope==="image"&&gate){const waiting=gate;gate=null;await waiting;}
      if(op==="area_difference"&&args.scope==="selection"&&rejectSelection){
        rejectSelection=false;throw Error("Injected area failure");
      }
      const data=command(op,args);
      if(op==="area_difference"&&args.scope==="selection"&&zero){
        delete data.packed;data.inward=Array(1024**2).fill(0);data.outward=Array(1024**2).fill(0);
        data.mean_area_nm2={inward:0,outward:0};data.max_area=1;
      }
      return data;
    });
    await page.addInitScript(()=>{window.testBackend=(op,args)=>window.engineRequest(op,args);});
    await page.goto(pathToFileURL(path.join(app,"web.html")).href);
    await page.waitForFunction(()=>state.result&&state.image&&!state.applying);
    assert.equal(calls.filter(c=>c.op==="area_difference").length,0,"Area calculations are lazy");
    // Keep aggregation small, while retaining actual Builder geometry and Image alignment.
    await page.evaluate(async()=>{
      const e=state.selected,c=state.entriesById.get(e.pair_id);
      await setFilters({chips:[[e.chip_x,e.chip_y]],ms:e.ms,
        chipRect:[Math.min(e.xrel,c.xrel),Math.min(e.yrel,c.yrel),Math.max(e.xrel,c.xrel),Math.max(e.yrel,c.yrel)]});
      await updateStatistics();
    });
    await page.waitForFunction(()=>state.image&&state.selected);
    gate=new Promise(resolve=>{release=resolve;});
    await page.locator("#layers .chip:has(#areaDifference)").click();
    await page.waitForFunction(()=>state.areaFlight!==null);
    const first=await page.evaluate(()=>state.selected.id);
    // Old image responses must not be painted on a newly selected image.
    await page.evaluate(async()=>{
      const e=state.index.images.find(e=>e.role==="base"&&e.pair_id&&e.id!==state.selected.id);
      await choose(e);
    });
    assert.equal(await page.evaluate(()=>state.areaData),null);
    release();
    await page.waitForFunction(()=>state.areaData?.id===state.selected.id);
    assert.notEqual(await page.evaluate(()=>state.areaData.id),first);
    assert.match(await page.locator("#areaStatus").innerText(),/20 compared Base patterns/);
    const paint=await page.evaluate(()=>{
      const ctx=$("#image").getContext("2d"),originalFill=ctx.fill,originalStroke=ctx.stroke;
      const fills=[],strokes=[];
      ctx.fill=function(...args){if(args[1]==="evenodd")fills.push(`${this.fillStyle}:${this.globalAlpha.toFixed(2)}`);return originalFill.apply(this,args);};
      ctx.stroke=function(...args){strokes.push(this.strokeStyle);return originalStroke.apply(this,args);};
      drawImage();ctx.fill=originalFill;ctx.stroke=originalStroke;
      return {fills:[...new Set(fills)],strokes:[...new Set(strokes)]};
    });
    assert.deepEqual(paint.fills,["#ff9eae:0.78","#55c8ff:0.78"]);
    assert.ok(paint.strokes.includes("#ff6767")&&paint.strokes.includes("#4687ff"),"Contour colors remain distinct from filled areas");
    const popupEvent=page.waitForEvent("popup");
    await page.locator("#expand").click();
    const popup=await popupEvent;
    await popup.waitForSelector("canvas.image");
    assert.equal(await page.evaluate(()=>imageViews().length),2);
    await page.screenshot({path:path.join(output,"area-image.png")});
    await popup.close();
    await page.locator("#layers .chip:has(#areaDifference)").click();
    assert.equal(await page.evaluate(()=>state.areaEnabled),false);
    await page.locator("#contourViewButton").click();
    await page.locator('[data-contour-scope="selection"]').click();
    await page.waitForFunction(()=>state.contourData?.scope==="selection"&&!state.contourLoading);
    assert.deepEqual(await selectionDots(),[],"Population view must not mark an unrelated PID");
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const densityCalls=calls.filter(c=>c.op==="contour_overlay").length;
    const legend=await page.evaluate(()=>{
      const ctx=$("#contourCanvas").getContext("2d"),original=ctx.fillText,texts=[];
      ctx.fillText=function(text,...args){texts.push(text);return original.call(this,text,...args);};
      drawContourView();ctx.fillText=original;
      const {width,ratio}=contourSize($("#contourCanvas")),x=(width-188+16+70)*ratio;
      return {texts,cap:state.contourData.max_density.toPrecision(4),
        bars:[164,198].map(y=>Array.from(ctx.getImageData(x,y*ratio,1,1).data).slice(0,3)),
        expected:roles.map(role=>palette(densityPalettes[role],.5))};
    });
    assert.ok(legend.texts.includes("Mean length [nm / cell]")&&legend.texts.includes("per image pair"));
    assert.ok(legend.texts.includes(`0 to ${legend.cap}+`)&&legend.texts.includes("Shared scale: P99.5 cap"));
    legend.bars.forEach((color,i)=>color.forEach((v,k)=>assert.ok(Math.abs(v-legend.expected[i][k])<4)));
    await page.screenshot({path:path.join(output,"density-focus-legend.png")});
    await page.locator('[data-contour-display="compare"]').click();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const comparisonLegend=await page.evaluate(()=>{
      const ctx=$("#contourCanvas").getContext("2d"),original=ctx.fillText,texts=[];
      ctx.fillText=function(text,...args){texts.push(text);return original.call(this,text,...args);};
      drawContourView();ctx.fillText=original;
      return {texts,cap:state.contourData.max_total.toPrecision(4)};
    });
    assert.ok(comparisonLegend.texts.includes("Mean total [nm / cell]"));
    assert.ok(comparisonLegend.texts.includes(`0 to ${comparisonLegend.cap}+`));
    assert.ok(comparisonLegend.texts.includes("Brightness: sqrt(total / cap)"));
    assert.ok(comparisonLegend.texts.includes("P99.5 cap; higher saturates"));
    assert.equal(calls.filter(c=>c.op==="contour_overlay").length,densityCalls,"Legend switches do not recalculate density");
    await page.screenshot({path:path.join(output,"density-comparison-legend.png")});
    await page.locator('[data-contour-display="focus"]').click();
    await page.locator('[data-contour-measure="area"]').click();
    const ready=()=>page.waitForFunction(()=>state.contourData?.measure==="area"&&!state.contourLoading);
    await ready();
    const count=calls.filter(c=>c.op==="area_difference"&&c.args.scope==="selection").length;
    const totals=await page.evaluate(()=>({
      actual:areaKinds.map(k=>state.contourData[k].reduce((a,b)=>a+b,0)),
      expected:areaKinds.map(k=>state.contourData.mean_area_nm2[k]),
    }));
    totals.actual.forEach((v,i)=>assert.ok(Math.abs(v-totals.expected[i])<1e-6));
    assert.equal(await page.locator("#contourDisplay").isVisible(),false);
    assert.equal(await page.locator("#areaChannels").isVisible(),true);
    for(const kind of ["inward","outward","both"])await page.locator(`[data-area-channel="${kind}"]`).click();
    assert.equal(calls.filter(c=>c.op==="area_difference"&&c.args.scope==="selection").length,count);
    await page.locator('[data-area-channel="inward"]').click();
    assert.deepEqual(await page.locator('[data-area-channel="inward"]').evaluate(button=>{
      const style=getComputedStyle(button);return [style.backgroundColor,style.color];
    }),["rgb(255, 158, 174)","rgb(8, 19, 31)"]);
    await page.screenshot({path:path.join(output,"area-inward-selected.png")});
    await page.locator('[data-area-channel="both"]').click();
    assert.notEqual(await page.locator('[data-area-channel="inward"]').evaluate(button=>getComputedStyle(button).backgroundColor),
      "rgb(255, 158, 174)");
    const areaPalette=await page.evaluate(()=>{
      const rgb=kind=>[1,3,5].map(i=>parseInt(areaColors[kind].slice(i,i+2),16)),b=rgb("inward"),c=rgb("outward");
      return {actual:Array.from(densityPixels({measure:"area",bins:2,max_area:1,
        inward:[1,0,.5,0],outward:[0,1,.5,0]})),
        expected:Array.from(new Uint8ClampedArray([...b,255,...c,255,...b.map((v,k)=>(v+c[k])/2),255,0,0,0,0]))};
    });
    assert.deepEqual(areaPalette.actual,areaPalette.expected,"Area grids must share the overlay/legend palette, including mixed cells");
    // Reference always uses Base, even if Show Comp is selected.
    await page.evaluate(()=>{state.mapRole="comparison";state.contourSem=true;state.contourDesign=true;syncContourControls();drawContourView();});
    assert.match(await page.locator("#contourStatus").innerText(),/Reference: Base/);
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const hover=await page.evaluate(()=>{
      const d=state.contourData,i=d.inward.findIndex((v,i)=>v+d.outward[i]>0),row=Math.floor(i/d.bins),col=i%d.bins;
      const x=-d.extent_nm+(col+.5)*2*d.extent_nm/d.bins,y=d.extent_nm-(row+.5)*2*d.extent_nm/d.bins;
      state.contourTransform.zoom=20;drawContourView();
      const before=mainContour.projection;
      state.contourTransform.x+=before.center[0]-(before.x+x*before.scale);
      state.contourTransform.y+=before.center[1]-(before.y-y*before.scale);
      drawContourView();
      const p=mainContour.projection,box=$("#contourCanvas").getBoundingClientRect();
      return {x:box.x+p.x+x*p.scale,y:box.y+p.y-y*p.scale,inward:d.inward[i].toPrecision(6),outward:d.outward[i].toPrecision(6)};
    });
    await page.locator("#contourCanvas").dispatchEvent("pointermove",{clientX:hover.x,clientY:hover.y,pointerId:1});
    const title=await page.locator("#contourCanvas").getAttribute("title");
    assert.ok(title.includes(`Inward: ${hover.inward}`)&&title.includes(`Outward: ${hover.outward}`),title);
    await page.evaluate(()=>fitContour());
    await page.screenshot({path:path.join(output,"area-selection.png")});
    // Measure is a major mode: it stays selected across every scope.
    await page.locator('[data-contour-scope="image"]').click();
    await page.waitForFunction(()=>state.contourData?.measure==="area"&&state.contourData.scope==="image"&&!state.contourLoading);
    assert.equal(await page.locator('[data-contour-measure="area"]').getAttribute("aria-pressed"),"true");
    assert.equal(await page.locator("#contourTargetControl").isVisible(),true);
    assert.equal(await page.locator("#areaChannels").isVisible(),true);
    assert.ok(await page.evaluate(()=>state.contourData.patterns.every(item=>areaKinds.every(k=>item[k].path instanceof Path2D))));
    await page.locator("#contourTarget").selectOption("pids");
    await page.waitForFunction(()=>state.contourMessage.includes("Click a source pattern"));
    await page.evaluate(()=>{state.contourPids=[state.image.labels[0].pid];refreshContourView();});
    await page.waitForFunction(()=>state.contourData?.scope==="image"&&state.contourData.compared===1&&!state.contourLoading);
    await page.screenshot({path:path.join(output,"area-this-image.png")});
    const areaPopupEvent=page.waitForEvent("popup");await page.locator("#expand").click();
    const areaPopup=await areaPopupEvent;
    await areaPopup.waitForSelector("canvas.contour");
    assert.ok(await areaPopup.locator("canvas.contour").isVisible());
    await areaPopup.close();
    await page.locator('[data-contour-scope="pid"]').click();
    await page.waitForFunction(()=>state.contourData?.measure==="area"&&state.contourData.scope==="pid"&&!state.contourLoading);
    assert.equal(await page.locator("#contourPidControl").isVisible(),true);
    assert.equal(await page.locator("#contourRoleControl").isVisible(),false);
    assert.doesNotMatch(await page.locator("#contourStatus").innerText(),/Reference:/,
      "Hidden All selected reference settings must not affect image-center PID coordinates");
    const pidTotals=await page.evaluate(()=>areaKinds.map(k=>[state.contourData[k].reduce((a,b)=>a+b,0),state.contourData.mean_area_nm2[k]]));
    pidTotals.forEach(([actual,expected])=>assert.ok(Math.abs(actual-expected)<1e-6));
    const pids=await page.evaluate(()=>Array.from($("#contourPid").options,option=>option.value)
      .filter(pid=>state.image.labels.some(label=>label.pid===pid)).slice(0,2));
    assert.equal(pids.length,2);
    const expectedDot=()=>page.evaluate(()=>{
      const label=state.image.labels.find(label=>label.pid===state.contourPid);
      return {pid:label.pid,x:label.x,y:label.y};
    });
    for(const pid of pids){
      await page.locator("#contourPid").selectOption(pid);
      await page.waitForFunction(pid=>state.contourData?.pid===pid&&!state.contourLoading,pid);
      assert.deepEqual(await selectionDots(),[[await expectedDot()]],"PID selector must move the glowing design-center mark");
    }
    await page.locator("#pidLabels").uncheck();
    await page.locator('[data-layer="base"]').uncheck();
    await page.locator('[data-layer="comparison"]').uncheck();
    assert.deepEqual(await selectionDots(),[[await expectedDot()]],"PID glow is independent of labels and contour visibility");
    await page.locator("#pidLabels").check();
    await page.locator('[data-layer="base"]').check();
    await page.locator('[data-layer="comparison"]').check();
    const pidPopupEvent=page.waitForEvent("popup");await page.locator("#expand").click();
    const pidPopup=await pidPopupEvent;await pidPopup.waitForSelector("canvas.image");
    assert.deepEqual(await selectionDots(),[[await expectedDot()],[await expectedDot()]],"Expand must show the same selected center");
    await pidPopup.close();
    await page.screenshot({path:path.join(output,"area-same-pid.png")});
    await page.locator('[data-contour-measure="length"]').click();
    await page.waitForFunction(()=>state.contourData?.scope==="pid"&&state.contourData.measure!=="area"&&!state.contourLoading);
    assert.deepEqual(await selectionDots(),[[await expectedDot()]],"Density and Area Diff share PID highlighting");
    const fallback=await page.evaluate(()=>{
      const pids=state.result.ppe.pids,original=paintSelectionDots,seen=[];
      paintSelectionDots=function(ctx,labels,scale){seen.push(...labels.map(label=>label.pid));return original(ctx,labels,scale);};
      try{
        state.result.ppe.pids=[state.image.labels.find(label=>label.pid!==state.contourPid).pid];
        syncContourPids();
        return {pid:state.contourPid,seen};
      }finally{paintSelectionDots=original;state.result.ppe.pids=pids;syncContourPids();}
    });
    assert.deepEqual(fallback.seen,[fallback.pid],"Applied-selection PID fallback must repaint the source mark immediately");
    const roleStyles=await page.evaluate(()=>roles.map(role=>{
      const button=$(`[data-contour-role="${role}"]`),ctx=$("#image").getContext("2d");
      ctx.fillStyle=colors[role];
      return {role,color:getComputedStyle(button).color,contour:ctx.fillStyle};
    }));
    assert.deepEqual(roleStyles,[{role:"base",color:"rgb(255, 103, 103)",contour:"#ff6767"},
      {role:"comparison",color:"rgb(70, 135, 255)",contour:"#4687ff"}]);
    assert.equal(await page.locator("#contourRoleControl").isVisible(),true);
    await page.locator('[data-contour-measure="area"]').click();
    await page.waitForFunction(()=>state.contourData?.measure==="area"&&!state.contourLoading);
    await page.locator('[data-contour-scope="selection"]').click();
    await ready();
    assert.equal(calls.filter(c=>c.op==="area_difference"&&c.args.scope==="selection").length,count,
      "Returning from other scopes must reuse the expensive full-selection grid");
    rejectSelection=true;
    await page.evaluate(()=>{state.contourCache.clear();state.contourKey=null;refreshContourView();});
    await page.waitForFunction(()=>!!state.contourError);
    assert.equal(await page.locator("#contourRetry").isVisible(),true);
    await page.locator("#contourRetry").click();await ready();
    zero=true;
    await page.evaluate(()=>{state.contourCache.clear();state.contourKey=null;refreshContourView();});
    await ready();
    assert.equal(await page.locator("#contourMessage").isVisible(),false,"Identical contours are a valid zero map");
    assert.match(await page.locator("#contourStatus").innerText(),/inward 0 \/ outward 0/);
    assert.deepEqual(errors,[]);
    await page.close();
    const singlePage=await browser.newPage();
    await singlePage.exposeFunction("engineRequest",(op,args)=>command(op,args,true));
    await singlePage.addInitScript(()=>{window.testBackend=(op,args)=>window.engineRequest(op,args);});
    await singlePage.goto(pathToFileURL(path.join(app,"web.html")).href);
    await singlePage.waitForFunction(()=>window.viewerReady&&state.result&&!state.applying&&state.image);
    assert.equal(await singlePage.locator("#areaDifference").isDisabled(),true);
    await singlePage.locator("#contourViewButton").click();
    await singlePage.locator('[data-contour-scope="selection"]').click();
    assert.equal(await singlePage.locator('[data-contour-measure="area"]').isDisabled(),true);
    await singlePage.waitForFunction(()=>state.contourData?.scope==="selection"&&!state.contourLoading);
    await singlePage.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const singleLegend=await singlePage.evaluate(()=>{
      const ctx=$("#contourCanvas").getContext("2d"),original=ctx.fillText,texts=[];
      ctx.fillText=function(text,...args){texts.push(text);return original.call(this,text,...args);};
      drawContourView();ctx.fillText=original;return texts;
    });
    assert.ok(singleLegend.includes("per image")&&!singleLegend.includes("Comparison"));
    console.log("Area and density passed: exact legends/caps, Focus/Comparison/Base-only, lazy/stale image, approved colors/opacity, PID-center glow, Expand, exact totals, channels, reference, hover, retry and zero.");
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
