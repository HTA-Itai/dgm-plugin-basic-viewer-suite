const assert=require("node:assert/strict");
const {spawnSync}=require("node:child_process");
const {pathToFileURL}=require("node:url");
const path=require("node:path");
const fs=require("node:fs");
const {chromium}=require("playwright");
const database=process.argv[2],output=process.argv[3]||path.dirname(path.dirname(database));
const appDirectory=process.argv[4]||__dirname;
const comparison=process.env.VIEWER_COMPARISON||"";
if(!database)throw Error("Supply database, optional output directory and staged app directory");
function command(operation,args){
  const source="import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from engine import Store;s=Store(Path(sys.argv[2]),Path(sys.argv[5]) if sys.argv[5] else None);op=sys.argv[3];args=json.loads(sys.argv[4]);v=s.index() if op=='index' else s.image(args['id']) if op=='image' else s.comparison_image(args['id']) if op=='comparison_image' else s.contour_overlay(args) if op=='contour_overlay' else s.statistics(args);print(json.dumps(v,allow_nan=False))";
  const result=spawnSync("python3",["-c",source,appDirectory,database,operation,JSON.stringify(args),comparison],{maxBuffer:20*1024*1024});
  if(result.status!==0)throw Error(result.stderr.toString());
  return JSON.parse(result.stdout.toString());
}
(async()=>{
  const browser=await chromium.launch({executablePath:"/usr/bin/google-chrome",headless:true,args:["--no-sandbox"]});
  try{
    const page=await browser.newPage({viewport:{width:2200,height:1400},deviceScaleFactor:2});
    const errors=[],network=[],calls=[];
    let gate=null,release=null,rejectNext=false,contourGate=null,releaseContour=null,rejectContour=false;
    const blockContour=()=>{contourGate=new Promise(resolve=>{releaseContour=resolve;});};
    const block=()=>{gate=new Promise(resolve=>{release=resolve;});};
    block();
    page.on("pageerror",error=>errors.push(error.message));
    await page.route(/^https?:/,route=>{network.push(route.request().url());return route.abort();});
    await page.exposeFunction("engineRequest",async(operation,args)=>{
      calls.push({operation,args});
      if(operation==="statistics"){
        if(gate){const waiting=gate;gate=null;await waiting;}
        if(rejectNext){rejectNext=false;throw Error("Injected apply failure");}
      }
      if(operation==="contour_overlay"){
        if(contourGate){const waiting=contourGate;contourGate=null;await waiting;}
        if(rejectContour){rejectContour=false;throw Error("Injected contour failure");}
      }
      return command(operation,args);
    });
    await page.addInitScript(()=>{window.testBackend=(op,args)=>window.engineRequest(op,args);});
    await page.goto(pathToFileURL(path.join(appDirectory,"web.html")).href);
    await page.waitForFunction(()=>window.viewerReady);
    const synthetic=await page.evaluate(()=>!!state.index.metadata.test_chip_override);
    assert.equal(await page.locator("#imageMode,#imageDisplay").count(),0);
    // Workbench: Selection block above the maps in the left rail; map dataset switch in each map heading.
    assert.equal(await page.locator("#rail #scopePane #update").innerText(),"Apply selection");
    assert.equal(await page.locator("#scopePane label",{hasText:"Image type"}).locator("#ms").count(),1);
    assert.equal(await page.locator("#pnoControl").isVisible(),false);
    const rail=await page.locator("#rail").boundingBox();
    const order=[];
    for(const id of ["scopePane","waferPane","chipPane"])order.push((await page.locator("#"+id).boundingBox()).y);
    assert.deepEqual([...order].sort((x,y)=>x-y),order);
    assert.ok((await page.locator("#center").boundingBox()).x>rail.x+rail.width-1);
    // Image list sits left of the Image panel, with the same height.
    const listBox=await page.locator("#imagesPane").boundingBox(),detailBox=await page.locator("#detailPane").boundingBox();
    assert.ok(listBox.x>rail.x+rail.width-1&&listBox.x+listBox.width<detailBox.x);
    assert.ok(Math.abs(listBox.y-detailBox.y)<1&&Math.abs(listBox.height-detailBox.height)<1);
    assert.deepEqual(await page.locator("#waferPane [data-map-role]").allInnerTexts(),["Base","Comp"]);
    assert.deepEqual(await page.locator("#chipPane [data-map-role]").allInnerTexts(),["Base","Comp"]);
    assert.deepEqual(await page.locator("#layers .chip").allInnerTexts(),
      ["Image","Design","PID","Base","Comp","Base","Comp","Area difference"]);
    assert.deepEqual(await page.locator("#layers .chipLabel").allInnerTexts(),["Contour","Vectors"]);
    const chipStyle=sel=>page.locator(`#layers .chip:has(${sel}) span`).evaluate(e=>getComputedStyle(e).backgroundColor);
    assert.equal(await chipStyle('[data-vector="comparison"]'),"rgb(70, 135, 255)");
    await page.locator('#layers .chip:has([data-vector="comparison"])').click();
    assert.equal(await page.locator('[data-vector="comparison"]').isChecked(),false);
    assert.equal(await page.locator('[data-vector="base"]').isChecked(),true);
    assert.equal(await chipStyle('[data-vector="comparison"]'),"rgb(29, 46, 67)");
    await page.locator('#layers .chip:has([data-vector="comparison"])').click();
    assert.equal(await page.locator('[data-vector="comparison"]').isChecked(),true);
    for(const [group,attribute] of [["contour","layer"],["vectors","vector"]]){
      const button=page.locator(`[data-overlay-group="${group}"]`);
      const input=role=>page.locator(`[data-${attribute}="${role}"]`);
      assert.equal(await button.getAttribute("aria-pressed"),"true");
      await button.click();
      assert.equal(await input("base").isChecked(),false);
      assert.equal(await input("comparison").isChecked(),false);
      assert.equal(await button.getAttribute("aria-pressed"),"false");
      await button.click();
      await page.locator(`#layers .chip:has([data-${attribute}="base"])`).click();
      assert.equal(await button.getAttribute("aria-pressed"),"mixed");
      await button.click();
      assert.equal(await input("base").isChecked(),true);
      assert.equal(await input("comparison").isChecked(),true);
      assert.equal(await button.getAttribute("aria-pressed"),"true");
    }
    assert.equal(await page.locator('[data-layer="design"]').isChecked(),true);
    assert.equal(await page.locator('[data-layer="image"]').isChecked(),true);
    assert.equal(await page.locator("#areaDifference").isChecked(),false);
    const [baseChip,compChip]=await Promise.all(['[data-layer="base"]','[data-layer="comparison"]'].map(sel=>page.locator(`#layers .chip:has(${sel})`).boundingBox()));
    assert.ok(compChip.x-(baseChip.x+baseChip.width)>=1.5,"Base/Comp chips must be separated");
    assert.equal(await page.locator("#rawPane").isVisible(),false);
    for(const [pane,button] of [["chipPane","fitChip"],["detailPane","fit"]]){
      const heading=page.locator(`#${pane} > .panelHeading`);
      assert.equal(await heading.locator(`#${button}`).count(),1);
      assert.match(await heading.locator(`#${button}`).getAttribute("title"),/unchanged/);
    }
    assert.equal(await page.locator("#statsPane #update").count(),0);
    const pre=await page.evaluate(()=>{
      const ctx=document.querySelector("#wafer").getContext("2d"),original=ctx.fillRect,seen=[];
      ctx.fillRect=function(...args){seen.push(this.fillStyle);return original.apply(this,args);};
      drawWafer();ctx.fillRect=original;
      return {calculated:!!state.result,colors:seen.filter(v=>v==="#2c536b").length};
    });
    assert.deepEqual(pre,{calculated:false,colors:5});
    release();
    const ready=async()=>page.waitForFunction(()=>state.result&&!state.applying&&!document.querySelector("#update").disabled);
    await ready();
    assert.equal(calls.filter(c=>c.operation==="statistics").length,1);
    assert.equal(await page.locator("html").getAttribute("lang"),"en");
    assert.doesNotMatch(await page.locator("body").innerText(),/[\u3040-\u30ff\u3400-\u9fff]/);
    assert.equal(await page.locator("header select").count(),0);
    assert.equal(await page.locator("#datasets > div").count(),2);
    const a=await page.locator("#datasets > div").nth(0).boundingBox(),b=await page.locator("#datasets > div").nth(1).boundingBox();
    assert.ok(b.y>=a.y+a.height-1);
    assert.equal(await page.locator("#multiplier").inputValue(),"10");
    assert.deepEqual(await page.locator("#metric option").evaluateAll(options=>options.map(o=>o.value)),["dx","dy","magnitude"]);
    assert.deepEqual(await page.locator("#metric option").allTextContents(),["Offset X [nm]","Offset Y [nm]","|Offset| [nm]"]);
    assert.equal(await page.locator('[data-lower="stats"]').textContent(),"Center offset (Center − Layout)");
    assert.equal(await page.locator("#stats tbody tr").count(),6);
    assert.deepEqual(await page.locator("#stats tbody tr td:first-child").allTextContents(),["Base","Base","Base","Comp","Comp","Comp"]);
    assert.deepEqual(await page.locator("[data-raw-role]").allTextContents(),["Base","Comp"]);
    assert.equal(await page.locator(".colorBar .state").count(),0);
    const verifyCohort=async()=>assert.equal(await page.evaluate(()=>{
      return ["base","comparison"].every(role=>{
        const n=state.result.table.find(r=>r.role===role&&r.metric==="dx").n;
        return n===state.result.graph[role].dx.length&&
          n===state.result.maps.filter(r=>r.role===role).reduce((a,r)=>a+r.n,0)&&
          n===state.result.image_maps.filter(r=>r.role===role).reduce((a,r)=>a+r.n,0);
      });
    }),true);
    await verifyCohort();
    if(await page.evaluate(()=>state.result.paired_images>0)){
      await page.setViewportSize({width:1600,height:950});
      const contourCalls=()=>calls.filter(c=>c.operation==="contour_overlay").length;
      const sourcePoint=async pid=>{
        await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
        return page.evaluate(pid=>{
          const label=state.image.labels.find(label=>label.pid===pid),box=$("#image").getBoundingClientRect(),t=mainView.transform;
          return {x:box.x+label.x*t.scale+t.x,y:box.y+label.y*t.scale+t.y};
        },pid);
      };
      const pids=await page.evaluate(()=>state.image.labels.slice(0,2).map(label=>label.pid));
      blockContour();
      await page.locator("#contourViewButton").click();
      await page.waitForFunction(()=>state.contourLoading);
      assert.equal(await page.locator("#contourOverlay").getAttribute("aria-busy"),"true");
      assert.match(await page.locator("#contourMessage").innerText(),/Loading/);
      assert.doesNotMatch(await page.locator("#contourMessage").innerText(),/No paired/);
      await page.evaluate(()=>{refreshContourView();refreshContourView();});
      assert.equal(contourCalls(),1);
      releaseContour();
      await page.waitForFunction(()=>state.contourData?.scope==="image"&&!state.contourLoading);
      assert.equal(await page.locator("#imageStage").isVisible(),true);
      assert.equal(await page.locator("#contourOverlay").isVisible(),true);
      assert.equal(await page.locator("#contourInset").count(),0);
      await page.locator("#contourZoomIn").click();
      await page.waitForFunction(()=>state.contourTransform.zoom>1);
      await page.locator("#contourFit").click();
      assert.equal(await page.evaluate(()=>state.contourTransform.zoom),1);
      assert.equal(contourCalls(),1);
      blockContour();
      await page.locator('[data-contour-scope="pid"]').click();
      await page.waitForFunction(()=>state.contourLoading);
      await page.locator('[data-contour-scope="selection"]').click();
      await page.locator('[data-contour-scope="pid"]').click();
      assert.equal(contourCalls(),2);
      releaseContour();
      await page.waitForFunction(()=>state.contourData?.scope==="pid"&&!state.contourLoading);
      assert.equal(contourCalls(),2,"Superseded scopes must not queue additional work");
      assert.ok(await page.evaluate(()=>state.contourData.counts.base>0&&state.contourData.counts.comparison>0));
      const pidPoint=await sourcePoint(pids[1]);
      await page.mouse.click(pidPoint.x,pidPoint.y);
      await page.waitForFunction(pid=>state.contourData?.pid===pid&&!state.contourLoading,pids[1]);
      assert.deepEqual(await page.evaluate(()=>{
        const original=paintSelectionDots,seen=[];
        paintSelectionDots=function(ctx,labels,scale){seen.push(...labels.map(label=>label.pid));return original(ctx,labels,scale);};
        try{drawImage();return seen;}finally{paintSelectionDots=original;}
      }),[pids[1]],"Clicking a source pattern must move the glowing PID center");
      const beforeVisibility=contourCalls();
      for(const role of ["base","comparison"]){
        await page.locator(`[data-contour-role="${role}"]`).click();
        await page.waitForFunction(role=>state.contourData.raster.key.endsWith(":"+role),role);
        const brightness=await page.evaluate(()=>{
          const c=state.contourData.raster.image,pixels=c.getContext("2d").getImageData(0,0,c.width,c.height).data;
          let red=0,blue=0;
          for(let i=0;i<pixels.length;i+=4){
            red=Math.max(red,pixels[i]*pixels[i+3]/255);
            blue=Math.max(blue,pixels[i+2]*pixels[i+3]/255);
          }
          return {red,blue};
        });
        if(role==="base")assert.ok(brightness.red>200&&brightness.blue<120,JSON.stringify(brightness));
        else assert.ok(brightness.blue>200&&brightness.red<120,JSON.stringify(brightness));
      }
      await page.locator('[data-contour-role="both"]').click();
      assert.equal(contourCalls(),beforeVisibility,"Dataset visibility must reuse the loaded PID paths");
      const overlapColors=await page.evaluate(()=>{
        const canvas=document.createElement("canvas");
        canvas.style.cssText="position:fixed;left:-2000px;width:640px;height:200px";
        document.body.append(canvas);canvas.width=640*devicePixelRatio;canvas.height=200*devicePixelRatio;
        const view={canvas,slot:"raster",transform:{zoom:1,x:0,y:0},projection:null};
        try{
          const data={scope:"pid",pid:"test",bounds:[-50,-20,50,20],
            paths:{base:new Path2D("M -40 -8 L 10 -8"),comparison:new Path2D("M -10 -8 L 40 -8")}};
          drawContourLines(view,data);
          const ctx=data.raster.image.getContext("2d"),p=view.projection;
          return [-30,0,30].map(x=>{
            const px=Math.round((p.x+x*p.scale)*devicePixelRatio),py=Math.round((p.y+8*p.scale)*devicePixelRatio);
            const pixels=ctx.getImageData(px-1,py-1,3,3).data;
            let best=[0,0,0,0];
            for(let i=0;i<pixels.length;i+=4)if(pixels[i+3]>best[3])best=Array.from(pixels.slice(i,i+4));
            return best.slice(0,3).map(v=>v*best[3]/255);
          });
        }finally{
          canvas.remove();
        }
      });
      assert.ok(overlapColors[0][0]>200&&overlapColors[0][2]<120,JSON.stringify(overlapColors));
      assert.ok(overlapColors[2][2]>200&&overlapColors[2][1]<150&&overlapColors[2][0]<100,
        "Comparison-only strokes must keep the shared blue color: "+JSON.stringify(overlapColors));
      assert.ok(overlapColors[1][0]>170&&overlapColors[1][2]>230&&overlapColors[1][1]<150,
        "Coincident PID lines must be highlighted purple, not one dataset's color: "+JSON.stringify(overlapColors));
      assert.equal(await page.locator("#contourCanvas").evaluate(c=>
        c.width===Math.floor(Math.floor(c.clientWidth)*devicePixelRatio)&&c.height===Math.floor(Math.floor(c.clientHeight)*devicePixelRatio)),true);
      const canvas=await page.locator("#contourCanvas").boundingBox(),beforeZoom=contourCalls();
      await page.mouse.move(canvas.x+canvas.width/2,canvas.y+canvas.height/2);
      await page.mouse.wheel(0,-300);
      await page.waitForFunction(()=>state.contourTransform.zoom>1);
      await page.locator("#contourFit").click();
      assert.equal(contourCalls(),beforeZoom,"Zoom and Fit must reuse compiled paths");
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      await page.evaluate(()=>{window.previousPidRaster=state.contourData.raster;});
      await page.locator('[data-contour-scope="selection"]').click();
      await page.waitForFunction(()=>state.contourData?.scope==="selection"&&!state.contourLoading);
      const density=await page.evaluate(()=>({
        pairs:state.contourData.pairs,expected:state.result.paired_images,
        base:state.contourData.mapped.base,comparison:state.contourData.mapped.comparison,
        max:state.contourData.max_total,
      }));
      assert.equal(density.pairs,density.expected);
      assert.ok(density.base>0&&density.comparison>0&&density.max>0,JSON.stringify(density));
      assert.equal(await page.evaluate(()=>state.contourData.bins),1024);
      assert.equal(await page.locator("#contourResolution").count(),0,"Density quality is fixed at 1024");
      assert.deepEqual(await page.evaluate(()=>{
        const saved=state.contourCache;state.contourCache=new Map();
        try{
          cacheContourData("old-density",{scope:"selection",bins:1024});
          cacheContourData("pid",{scope:"pid"});
          cacheContourData("new-density",{scope:"selection",bins:1024});
          return [...state.contourCache.keys()];
        }finally{state.contourCache=saved;}
      }),["pid","new-density"],"Retain only one large density result");
      await page.locator('[data-contour-display="compare"]').click();
      assert.equal(await page.locator('[data-contour-display="compare"]').getAttribute("aria-pressed"),"true");
      const beforeCached=contourCalls();
      await page.locator('[data-contour-scope="pid"]').click();
      await page.waitForFunction(()=>state.contourData?.scope==="pid");
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      assert.equal(contourCalls(),beforeCached,"Revisiting a cached scope must not read SQLite again");
      assert.equal(await page.evaluate(()=>state.contourData.raster===window.previousPidRaster),true,
        "Changing scope and status text must not rasterize a cached PID at a temporary panel size");
      rejectContour=true;
      await page.evaluate(()=>{state.contourCache.clear();state.contourData=null;refreshContourView();});
      await page.waitForFunction(()=>!!state.contourError);
      assert.match(await page.locator("#contourMessage").innerText(),/Injected contour failure/);
      assert.equal(await page.locator("#contourOverlay").getAttribute("aria-busy"),"false");
      await page.locator("#contourRetry").click();
      await page.waitForFunction(()=>state.contourData?.scope==="pid"&&!state.contourLoading);
      assert.equal(await page.locator("#error").innerText(),"");
      await page.locator('[data-contour-scope="image"]').click();
      await page.waitForFunction(()=>state.contourData?.scope==="image"&&!state.contourLoading);
      const source=await page.locator("#sourceView").boundingBox(),overlay=await page.locator("#contourPane").boundingBox();
      assert.ok(source.x+source.width<overlay.x,"The source image must remain left of the contour");
      const divider=await page.locator("#contourSplitter").boundingBox();
      await page.mouse.move(divider.x+divider.width/2,divider.y+divider.height/2);
      await page.mouse.down();await page.mouse.move(divider.x+55,divider.y+divider.height/2,{steps:5});await page.mouse.up();
      assert.ok((await page.locator("#sourceView").boundingBox()).width>source.width+30);
      await page.locator("#contourSplitter").dblclick();
      const filtersBefore=await page.evaluate(()=>JSON.stringify(state.filters));
      let point=await sourcePoint(pids[0]);await page.mouse.click(point.x,point.y);
      await page.waitForFunction(pid=>state.contourTarget==="pids"&&state.contourData?.counts.base===1&&
        state.contourPids[0]===pid&&!state.contourLoading,pids[0]);
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      const selectionMark=await page.evaluate(pid=>{
        const c=$("#image"),ctx=c.getContext("2d"),t=mainView.transform,k=c.width/c.getBoundingClientRect().width;
        const pixel=(x,y)=>[...ctx.getImageData(Math.round((x*t.scale+t.x)*k),Math.round((y*t.scale+t.y)*k),1,1).data.slice(0,3)];
        const label=state.image.labels.find(label=>label.pid===pid);
        // The yellow PID label sits up-right of the pattern; sample only the rest of the contour.
        const contour=state.image.layers.base[label.contour_index].filter(([x,y])=>x<label.x||y>label.y).map(([x,y])=>pixel(x,y));
        return {base:$("#layers [data-layer=base]").checked,dot:pixel(label.x,label.y),
          red:contour.filter(([r,g,b])=>r>230&&g<140&&b<140).length,
          yellow:contour.filter(([r,g,b])=>r>230&&g>180&&b<150).length,total:contour.length};
      },pids[0]);
      assert.ok(selectionMark.dot[0]>230&&selectionMark.dot[1]>180&&selectionMark.dot[2]<150,
        "Selected pattern needs a yellow dot at its design position: "+JSON.stringify(selectionMark));
      assert.ok(selectionMark.base&&selectionMark.red>selectionMark.total/3&&selectionMark.yellow===0,
        "Selection must not recolor the Base contour: "+JSON.stringify(selectionMark));
      point=await sourcePoint(pids[1]);await page.keyboard.down("Control");await page.mouse.click(point.x,point.y);await page.keyboard.up("Control");
      await page.waitForFunction(()=>state.contourData?.counts.base===2&&!state.contourLoading);
      point=await sourcePoint(pids[0]);await page.keyboard.down("Control");await page.mouse.click(point.x,point.y);await page.keyboard.up("Control");
      await page.waitForFunction(pid=>state.contourData?.counts.base===1&&state.contourPids[0]===pid&&!state.contourLoading,pids[1]);
      assert.equal(await page.evaluate(()=>JSON.stringify(state.filters)),filtersBefore,"Clicked PIDs must not change analysis filters");
      await page.locator("#contourClearPids").click();
      await page.waitForFunction(()=>!state.contourLoading&&state.contourPids.length===0);
      assert.match(await page.locator("#contourMessage").innerText(),/Click a source pattern/);
      point=await sourcePoint(pids[0]);
      await page.keyboard.down("Control");await page.mouse.move(point.x-8,point.y-8);await page.mouse.down();
      await page.mouse.move(point.x+8,point.y+8,{steps:5});await page.mouse.up();await page.keyboard.up("Control");
      await page.waitForFunction(()=>state.contourTarget==="roi"&&state.filters.roi&&state.contourData?.counts.base>0&&!state.contourLoading);
      await page.locator("#clearRoi").click();
      await page.waitForFunction(()=>!state.filters.roi&&!state.contourLoading);
      await page.locator("#contourViewButton").click();
    }else{
      await page.locator("#contourViewButton").click();
      assert.match(await page.locator("#contourMessage").innerText(),/No paired images/);
      assert.equal(await page.locator("#contourOverlay").getAttribute("aria-busy"),"false");
      await page.locator("#contourViewButton").click();
    }
    // Give the graphs more room where tables fit, without increasing table overflow on smaller layouts.
    for(const width of [1920,1600,1270]){
      await page.setViewportSize({width,height:1080});
      for(const drawer of [false,true]){
        if(drawer)await page.locator("#rawToggle").click();
        for(const mode of ["stats","ppe"]){
          await page.locator(`[data-lower="${mode}"]`).click();
          const layout=await page.evaluate(mode=>{
            const pane=document.querySelector(mode==="stats"?"#statsPane":"#ppeStatsPane");
            const graph=document.querySelector(mode==="stats"?"#graphPane":"#ppeGraphPane");
            const scroll=pane.querySelector(".scroll");
            const measure=()=>({graph:graph.getBoundingClientRect().width,overflow:scroll.scrollWidth-scroll.clientWidth});
            const current=measure();
            pane.style.width="56%";
            const previous=measure();
            pane.style.removeProperty("width");
            return {current,previous};
          },mode);
          assert.ok(layout.current.overflow<=layout.previous.overflow,JSON.stringify({width,drawer,mode,layout}));
          if(width===1920&&(!drawer||mode==="ppe")){
            assert.ok(layout.current.graph>=layout.previous.graph+50,JSON.stringify({width,drawer,mode,layout}));
            assert.equal(layout.current.overflow,0,JSON.stringify({width,drawer,mode,layout}));
          }
          if(width===1920&&!drawer){
            const pane=page.locator(mode==="stats"?"#statsPane":"#ppeStatsPane");
            const before=await pane.boundingBox();
            await page.mouse.move(before.x+before.width-3,before.y+before.height-3);
            await page.mouse.down();
            await page.mouse.move(before.x+before.width-53,before.y+before.height-3,{steps:5});
            await page.mouse.up();
            assert.ok((await pane.boundingBox()).width<before.width-20,`Cannot resize ${mode} table`);
            await pane.evaluate(element=>element.style.removeProperty("width"));
          }
        }
        if(drawer)await page.locator("#rawToggle").click();
      }
    }
    await page.locator('[data-lower="stats"]').click();
    await page.setViewportSize({width:2200,height:1400});
    assert.doesNotMatch(await page.locator("body").innerText(),/TEST ONLY/);
    assert.doesNotMatch(await page.title(),/TEST ONLY/);
    if(!synthetic){
      assert.equal(await page.evaluate(()=>state.result.paired_images),0);
      assert.equal(await page.evaluate(()=>state.visible.length),0);
      assert.equal(await page.evaluate(()=>state.result.counts.base.unpaired),1290);
      assert.equal(await page.evaluate(()=>state.result.counts.comparison.unpaired),1290);
      assert.ok(await page.evaluate(()=>state.result.table.every(r=>r.n===0)));
      assert.equal(await page.locator("#testWarning").isVisible(),false);
      assert.equal(await page.locator("#error").innerText(),"");
      assert.deepEqual(errors,[]);assert.deepEqual(network,[]);
      console.log("PASS: original unmatched chips remain excluded from maps, tables, graphs and matched-image list; explicit unpaired counts.");
      return;
    }
    assert.equal(await page.locator("#testWarning").isVisible(),true);
    assert.match(await page.locator("#testWarning").innerText(),/synthetic chip coordinates.*Not a validated same-site comparison/i);
    assert.equal(await page.evaluate(()=>state.result.paired_images),1288);
    assert.equal(await page.evaluate(()=>state.result.table[0].n),22214);
    assert.equal(await page.evaluate(()=>state.visible.length),1288);
    const baselineAxes=await page.evaluate(()=>JSON.stringify(state.axes));
    const statsCalls=()=>calls.filter(c=>c.operation==="statistics").length;
    // Either map heading switches both maps; the overlay and the measurements drawer are independent.
    const datasetSwitch=async(pane,role)=>{
      const rawRole=await page.evaluate(()=>state.rawRole);
      await page.locator(`#${pane} [data-map-role="${role}"]`).click();
      assert.deepEqual(await page.locator('[data-map-role][aria-pressed="true"]').evaluateAll(b=>b.map(x=>x.dataset.mapRole)),[role,role]);
      assert.ok(await page.evaluate(()=>state.chipPoints.every(p=>p.entry.role===state.mapRole&&p.entry.chip_x===state.focusChip[0]&&p.entry.chip_y===state.focusChip[1])));
      assert.equal(await page.evaluate(()=>state.rawRole),rawRole);
    };
    // The image background follows the map switch; the Comparison JPEG is fetched on demand and cached.
    const pixels=()=>page.locator("#image").evaluate(c=>c.toDataURL());
    const imageCalls=()=>calls.filter(c=>c.operation==="comparison_image").length;
    const imageName=await page.locator("#imageName").innerText();
    assert.equal(imageCalls(),0);
    assert.equal(await page.locator("#imageBackground").innerText(),"Background: Base");
    const imageLayer=page.locator('[data-layer="image"]');
    await imageLayer.uncheck();
    const bareBase=await pixels();
    await imageLayer.check();
    const initialPixels=await pixels();
    await datasetSwitch("chipPane","comparison");
    await page.waitForFunction(()=>/^Background: Comparison \(.+\)$/.test(document.querySelector("#imageBackground").textContent));
    assert.equal(await page.locator("#imageName").innerText(),imageName);
    assert.notEqual(await pixels(),initialPixels);
    await imageLayer.uncheck();
    assert.equal(await pixels(),bareBase);
    await imageLayer.check();
    await datasetSwitch("waferPane","base");
    assert.equal(await page.locator("#imageBackground").innerText(),"Background: Base");
    assert.equal(await pixels(),initialPixels);
    await datasetSwitch("chipPane","comparison");
    await page.waitForFunction(()=>/^Background: Comparison/.test(document.querySelector("#imageBackground").textContent));
    assert.equal(imageCalls(),1);
    await page.locator("#contourViewButton").click();
    await page.locator('[data-contour-scope="selection"]').click();
    await page.waitForFunction(()=>state.contourData?.scope==="selection"&&!state.contourLoading);
    const beforeReference=calls.filter(c=>c.operation==="contour_overlay").length;
    await page.locator("#contourSem").check();
    await page.waitForFunction(()=>document.querySelector("#contourStatus").textContent.includes("Reference: Comparison"));
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))));
    const referenceDraw=await page.evaluate(()=>{
      const c=$("#contourCanvas"),ctx=c.getContext("2d"),original=ctx.drawImage,draws=[];
      ctx.drawImage=function(image,...args){
        if(image===state.comparisonBitmap.bitmap)draws.push(args);
        return original.call(this,image,...args);
      };
      try{drawContourView();}finally{ctx.drawImage=original;}
      const p=state.contourProjection,f=state.image.reference_frames.comparison,[ax,ay]=f.design_center;
      return {draw:draws.at(-1),expected:[p.x-ax*f.nm_x*p.scale,p.y-ay*f.nm_y*p.scale,
        f.width*f.nm_x*p.scale,f.height*f.nm_y*p.scale],center:f.design_center,frame:[f.width,f.height]};
    });
    assert.deepEqual(referenceDraw.draw,referenceDraw.expected,"SEM must be placed on its design layout center like the density");
    assert.notDeepEqual(referenceDraw.center,referenceDraw.frame.map(v=>v/2),"Real data must exercise a non-image-center origin");
    const contourPixels=()=>page.locator("#contourCanvas").evaluate(c=>c.toDataURL());
    const opacity=async value=>{
      await page.locator("#contourSemOpacity").evaluate((input,value)=>{
        input.value=value;input.dispatchEvent(new Event("input",{bubbles:true}));
      },value);
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    };
    await opacity("0");const invisibleReference=await contourPixels();
    await opacity("100");assert.notEqual(await contourPixels(),invisibleReference,"SEM opacity must affect actual contour-panel pixels");
    await page.locator("#contourDesign").check();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const withDesign=await contourPixels();
    const designPixel=await page.evaluate(()=>{
      const c=$("#contourCanvas"),f=state.image.reference_frames.comparison,[ax,ay]=f.design_center,p=state.contourProjection;
      // Use the design pattern nearest the origin so the sampled vertex is inside the visible panel.
      const distance=points=>Math.min(...points.map(([x,y])=>Math.hypot(x-ax,y-ay)));
      const polygon=state.image.comparison_design.filter(points=>{
        const xs=points.map(v=>v[0]),ys=points.map(v=>v[1]);
        return !(Math.min(...xs)<=0&&Math.min(...ys)<=0&&Math.max(...xs)>=f.width&&Math.max(...ys)>=f.height);
      }).sort((a,b)=>distance(a)-distance(b))[0];
      const [px,py]=polygon[0],ratio=devicePixelRatio||1;
      const x=Math.round((p.x+(px-ax)*f.nm_x*p.scale)*ratio),y=Math.round((p.y-(ay-py)*f.nm_y*p.scale)*ratio);
      const data=c.getContext("2d").getImageData(x-1,y-1,3,3).data;
      let best=[0,0,0,0];
      for(let i=0;i<data.length;i+=4)if(data[i+1]-data[i]>best[1]-best[0])best=[...data.slice(i,i+4)];
      return best;
    });
    assert.ok(designPixel[1]>200&&designPixel[1]>designPixel[0]&&designPixel[1]>designPixel[2],
      "Design vertices must be drawn at the density origin: "+JSON.stringify(designPixel));
    await page.locator("#contourDesign").uncheck();
    assert.notEqual(await contourPixels(),withDesign,"Design toggle must affect rendered pixels");
    await datasetSwitch("waferPane","base");
    await page.waitForFunction(()=>document.querySelector("#contourStatus").textContent.includes("Reference: Base"));
    assert.match(await page.locator("#contourStatus").innerText(),/current image only; not an average/);
    await datasetSwitch("chipPane","comparison");
    await page.waitForFunction(()=>document.querySelector("#contourStatus").textContent.includes("Reference: Comparison"));
    assert.equal(calls.filter(c=>c.operation==="contour_overlay").length,beforeReference,"Reference layers must not recalculate density");
    await page.locator("#contourSem").uncheck();
    await page.locator("#contourViewButton").click();
    // Edge tab: right edge of the window, near the top of the Image panel; it moves with the drawer.
    const tabBox=await page.locator("#rawToggle").boundingBox(),viewport=page.viewportSize();
    const imageTop=(await page.locator("#detailPane").boundingBox()).y;
    assert.ok(viewport.width-(tabBox.x+tabBox.width)<12 && Math.abs(tabBox.y-imageTop)<80,JSON.stringify({tabBox,imageTop}));
    assert.equal(await page.locator("#rawToggle").innerText().then(t=>/Raw measurement data/.test(t.replace(/\s+/g," "))),true);
    await page.locator("#rawToggle").click();
    assert.equal(await page.locator("#rawPane").isVisible(),true);
    assert.equal(await page.locator("#rawToggle").getAttribute("aria-expanded"),"true");
    const rawBox=await page.locator("#rawPane").boundingBox(),openTab=await page.locator("#rawToggle").boundingBox();
    assert.ok(Math.abs(openTab.x+openTab.width-rawBox.x)<4,"tab sits on the drawer's left edge");
    await page.locator('[data-raw-role="comparison"]').click();
    assert.equal(await page.locator("#raw tbody tr").count(),20);
    assert.equal(await page.locator("#raw thead th").first().innerText(),"PID");
    await page.locator("#rawPane details summary").click();
    await page.locator("#columns label",{hasText:/^#$/}).locator("input").check();
    assert.equal(await page.locator("#raw thead th").first().innerText(),"#");
    assert.equal(await page.locator("#raw tbody tr").first().locator("td").first().innerText(),"1");
    await page.locator("#columns label",{hasText:/^#$/}).locator("input").uncheck();
    // A narrow window scrolls horizontally, so the open drawer and its close button stay reachable.
    const wideSize=page.viewportSize();
    await page.setViewportSize({width:1270,height:wideSize.height});
    await page.waitForTimeout(200);
    const scroll=await page.evaluate(()=>({scroll:document.body.scrollWidth,client:document.body.clientWidth,overflow:getComputedStyle(document.body).overflowX}));
    assert.ok(scroll.scroll>scroll.client&&scroll.overflow==="auto",JSON.stringify(scroll));
    await page.evaluate(()=>{document.body.scrollLeft=document.body.scrollWidth;});
    const closeBox=await page.locator("#rawClose").boundingBox();
    assert.ok(closeBox.x>=0&&closeBox.x+closeBox.width<=1270,JSON.stringify(closeBox));
    await page.evaluate(()=>{document.body.scrollLeft=0;});
    await page.setViewportSize(wideSize);
    await page.locator("#rawClose").click();
    assert.equal(await page.locator("#rawPane").isVisible(),false);
    await datasetSwitch("waferPane","base");
    assert.equal(statsCalls(),1);
    // View-only focus never changes selection or causes a recalculation.
    const initialFilters=await page.evaluate(()=>JSON.stringify(state.filters));
    const applied=await page.evaluate(()=>JSON.stringify(state.result));
    await page.selectOption("#focusChip","6,5");
    assert.ok(await page.evaluate(()=>state.chipPoints.every(p=>p.entry.chip_x===6&&p.entry.chip_y===5)));
    assert.equal(await page.evaluate(()=>JSON.stringify(state.filters)),initialFilters);
    assert.equal(await page.evaluate(()=>JSON.stringify(state.result)),applied);
    await page.selectOption("#focusChip","8,7");
    const drag=async(selector,start,end,ctrl=false)=>{
      const box=await page.locator(selector).boundingBox();
      if(ctrl)await page.keyboard.down("Control");
      await page.mouse.move(box.x+start[0],box.y+start[1]);await page.mouse.down();
      await page.mouse.move(box.x+end[0],box.y+end[1],{steps:7});await page.mouse.up();
      if(ctrl)await page.keyboard.up("Control");
    };
    const chipBounds=await page.locator("#chip").boundingBox();
    await drag("#chip",[80,80],[112,104]);
    assert.deepEqual(await page.evaluate(()=>state.chipPan),{x:32,y:24});
    assert.equal(await page.evaluate(()=>state.filters.chipRect),null);
    assert.equal(await page.evaluate(()=>JSON.stringify(state.filters)),initialFilters);
    await page.locator("#fitChip").click();
    // Cancelled modifier-drag must not commit an ROI.
    await page.keyboard.down("Control");
    await page.mouse.move(chipBounds.x+50,chipBounds.y+50);await page.mouse.down();
    await page.mouse.move(chipBounds.x+100,chipBounds.y+100,{steps:2});
    await page.locator("#chip").dispatchEvent("pointercancel");
    await page.mouse.up();await page.keyboard.up("Control");
    assert.equal(await page.evaluate(()=>state.filters.chipRect),null);
    await drag("#chip",[35,35],[chipBounds.width*.65,chipBounds.height*.65],true);
    assert.ok(await page.evaluate(()=>state.filters.chipRect!==null));
    assert.equal(await page.evaluate(()=>JSON.stringify(state.result)),applied);
    assert.equal(statsCalls(),1);
    assert.match(await page.locator("#applyStatus").innerText(),/Pending/);
    assert.deepEqual(await page.locator(".mapPending").evaluateAll(b=>b.map(x=>x.hidden)),[false,false]);
    assert.equal(await page.locator("#exportStats").isDisabled(),true);
    await page.locator("#clearChipRect").click();
    // Image normal drag pans only; Ctrl+drag places shared source-pixel ROI.
    await page.waitForFunction(()=>state.image&&state.bitmap);
    const t=await page.evaluate(()=>({...state.transform}));
    await drag("#image",[80,80],[108,103]);
    assert.ok(await page.evaluate(t=>Math.abs(state.transform.x-t.x-28)<1&&Math.abs(state.transform.y-t.y-23)<1,t));
    assert.equal(await page.evaluate(()=>state.filters.roi),null);
    await page.locator("#fit").click();
    const tr=await page.evaluate(()=>({...state.transform}));
    await drag("#image",[tr.x+tr.scale*50,tr.y+tr.scale*50],[tr.x+tr.scale*280,tr.y+tr.scale*280],true);
    const roi=await page.evaluate(()=>state.filters.roi);
    for(let i=0;i<4;i++)assert.ok(Math.abs(roi[i]-[50,50,280,280][i])<2,JSON.stringify(roi));
    assert.equal(statsCalls(),1);
    // Apply captures one snapshot; edits made while computing stay pending.
    block();
    await page.locator("#update").click();
    await page.waitForFunction(()=>state.applying);
    await page.selectOption("#ms","01MS");
    release();await ready();
    assert.equal(await page.evaluate(()=>state.result.filters.ms),"");
    assert.equal(await page.evaluate(()=>state.filters.ms),"01MS");
    assert.equal(await page.evaluate(()=>hasPending()),true);
    assert.equal(await page.locator("#exportStats").isDisabled(),true);
    await page.locator("#clearRoi").click();
    await page.locator("#update").click();await ready();
    assert.equal(await page.evaluate(()=>state.result.paired_images),644);
    assert.equal(await page.evaluate(()=>state.result.table[0].n),11114);
    assert.equal(await page.evaluate(()=>state.visible.length),644);
    assert.equal(await page.evaluate(()=>JSON.stringify(state.axes)),baselineAxes);
    await verifyCohort();
    assert.equal(await page.evaluate(()=>hasPending()),false);
    assert.deepEqual(await page.locator(".mapPending").evaluateAll(b=>b.map(x=>x.hidden)),[true,true]);
    assert.equal(await page.locator("#exportStats").isEnabled(),true);
    // Wafer selection immediately edits the draft, never the existing result.
    const oldSnapshot=await page.evaluate(()=>JSON.stringify(state.result));
    const wafer=await page.locator("#wafer").boundingBox();
    const cell=await page.evaluate(()=>state.waferCells.find(c=>c.x===8&&c.y===7));
    await page.mouse.click(wafer.x+(cell.box[0]+cell.box[2])/2,wafer.y+(cell.box[1]+cell.box[3])/2);
    assert.deepEqual(await page.evaluate(()=>state.filters.chips),[[8,7]]);
    assert.equal(await page.evaluate(()=>JSON.stringify(state.result)),oldSnapshot);
    await page.locator("#update").click();await ready();
    assert.equal(await page.evaluate(()=>state.result.paired_images),128);
    assert.equal(await page.evaluate(()=>state.result.table[0].n),2216);
    assert.equal(await page.evaluate(()=>state.result.counts.base.unpaired),1);
    await verifyCohort();
    await page.evaluate(()=>choose(state.visible.find(e=>e.seq===23)));
    await page.waitForFunction(()=>state.image&&state.bitmap);
    const rendering=await page.evaluate(()=>{
      const result={};
      for(const id of ["image","wafer","chip"]){
        const ctx=document.getElementById(id).getContext("2d"),orig=ctx.stroke,strokes=[];
        ctx.stroke=function(){strokes.push({color:this.strokeStyle,width:this.lineWidth*(id==="image"?state.transform.scale:1)});return orig.call(this);};
        if(id==="image")drawImage();else if(id==="wafer")drawWafer();else drawChip();
        ctx.stroke=orig;result[id]=strokes;
      }
      return result;
    });
    for(const color of ["#ff6767","#4687ff","#9fee9f"]){
      assert.ok(rendering.image.some(s=>s.color===color&&Math.abs(s.width-2.5)<.001),color);
    }
    assert.ok(rendering.wafer.length>5&&rendering.chip.length>100);
    // A selected vector with negative source dy must point down on screen.
    const vectorDirection=await page.evaluate(()=>{
      const value=state.chipPoints.find(p=>p.data?.dy<0).data;
      const ctx=document.querySelector("#chip").getContext("2d"),original=ctx.lineTo,ends=[];
      ctx.lineTo=function(x,y){ends.push([x,y]);return original.call(this,x,y);};
      mapArrow(ctx,100,100,value,32);ctx.lineTo=original;
      return {dy:value.dy,end:ends[0]};
    });
    assert.ok(vectorDirection.dy<0&&vectorDirection.end[1]>100,JSON.stringify(vectorDirection));
    assert.match(await page.locator("#mapScale").getAttribute("title"),/Arrow: 1 nm = [\d.]+ screen px/);
    assert.match(await page.locator("#chipScale").getAttribute("title"),/32.0 screen px/);
    // Color bars sit to the right of each map.
    for(const [map,bar] of [["#wafer","#mapScale"],["#chip","#chipScale"]]){
      const m=await page.locator(map).boundingBox(),c=await page.locator(bar).boundingBox();
      assert.ok(c.x>=m.x+m.width-1&&c.height>m.height*.8,bar);
    }
    await page.screenshot({path:path.join(output,"preview-workflow-a.png")});
    await page.locator("#detailPane").screenshot({path:path.join(output,"preview-overlay-detail.png")});
    for(const graph of ["hist","box"]){
      await page.locator(`[data-graph="${graph}"]`).click();
      for(const metric of ["dx","dy","magnitude"]){
        await page.selectOption("#metric",metric);
        assert.equal(await page.evaluate(()=>JSON.stringify(state.axes)),baselineAxes);
      }
    }
    await page.screenshot({path:path.join(output,"preview-box.png")});
    await page.locator('[data-graph="scatter"]').click();
    assert.equal(await page.locator("#metric").isDisabled(),true);
    const zero=await page.evaluate(()=>{
      const c=document.querySelector("#graph").getContext("2d"),orig=c.stroke,colors=[];
      c.stroke=function(){colors.push(this.strokeStyle);return orig.call(this);};drawGraph();c.stroke=orig;
      return colors.filter(c=>c==="#e7edf7").length;
    });assert.equal(zero,2);
    // Rejected Apply keeps the last snapshot; the failure is visible and retry succeeds.
    await page.selectOption("#ms","02MS");
    const beforeFailure=await page.evaluate(()=>JSON.stringify(state.result));
    rejectNext=true;await page.locator("#update").click();await ready();
    assert.equal(await page.evaluate(()=>JSON.stringify(state.result)),beforeFailure);
    assert.match(await page.locator("#error").innerText(),/Injected apply failure/);
    assert.match(await page.locator("#applyStatus").innerText(),/failed/);
    await page.locator("#update").click();await ready();
    assert.equal(await page.locator("#error").innerText(),"");
    assert.equal(await page.evaluate(()=>hasPending()),false);
    // Empty selection / undo / reset preserves image type and fixed axes.
    await page.evaluate(()=>setFilters({chips:[[0,0]]}));
    await page.locator("#update").click();await ready();
    assert.equal(await page.evaluate(()=>state.result.paired_images),0);
    assert.equal(await page.evaluate(()=>state.visible.length),0);
    await page.locator("#undo").click();
    await page.locator("#clearAll").click();
    assert.equal(await page.locator("#ms").inputValue(),"02MS");
    await page.selectOption("#ms","");
    await page.locator("#update").click();await ready();
    // Virtual list: only rows in view exist; scrolling reaches the last image without paging.
    const listed=await page.evaluate(()=>state.visible.length);
    assert.equal(listed,1288);
    assert.ok(await page.locator("#images .listRow").count()<80);
    await page.locator("#images").evaluate(list=>{list.scrollTop=list.scrollHeight;});
    await page.waitForFunction(()=>document.querySelector(`#images .listRow[data-id="${state.visible.at(-1).id}"]`));
    await page.locator(`#images .listRow[data-id="${await page.evaluate(()=>state.visible.at(-1).id)}"]`).click();
    await page.waitForFunction(()=>state.selected?.id===state.visible.at(-1).id&&state.image);
    await page.keyboard.press("ArrowUp");
    assert.equal(await page.evaluate(()=>state.selected.id===state.visible.at(-2).id),true);
    // Screen readers: the listbox points at the selected option, which stays in the DOM when scrolled away.
    const activeRow=()=>page.evaluate(()=>{
      const id=document.querySelector("#images").getAttribute("aria-activedescendant"),row=id&&document.getElementById(id);
      return {id,exists:!!row,selected:row?.getAttribute("aria-selected"),matches:row?.dataset.id===String(state.selected?.id)};
    });
    assert.deepEqual(await activeRow(),{id:`imageRow-${await page.evaluate(()=>state.selected.id)}`,exists:true,selected:"true",matches:true});
    await page.locator("#images").evaluate(list=>{list.scrollTop=0;list.dispatchEvent(new Event("scroll"));});
    await page.waitForFunction(()=>document.querySelector(`#images .listRow[data-id="${state.visible[0].id}"]`));
    assert.equal((await activeRow()).exists,true);
    // Search filters only the list; statistics and the analysis selection stay unchanged.
    const beforeSearch=await page.evaluate(()=>JSON.stringify([state.result,state.filters]));
    const callsBeforeSearch=statsCalls();
    const search=async text=>{await page.fill("#imageSearch",text);return page.evaluate(()=>state.visible.map(e=>[e.name,e.seq,e.chip_x,e.chip_y]));};
    const chipRows=await search("(8, 7)");
    assert.ok(chipRows.length>0&&chipRows.every(r=>r[2]===8&&r[3]===7),String(chipRows.length));
    const seqRows=await search("23");
    assert.ok(seqRows.some(r=>r[1]===23)&&seqRows.every(r=>r[1]===23||r[0].includes("23")));
    const nameRows=await search("m000005-02ms");
    assert.deepEqual(nameRows.map(r=>r[0]),["S21_M000005-02MS.png"]);
    assert.match(await page.locator("#imageCount").innerText(),/^1 of 1288$/);
    await page.locator("#images .listRow").first().click();await page.waitForFunction(()=>state.image&&state.selected.name==="S21_M000005-02MS.png");
    assert.deepEqual(await search("no such image"),[]);
    assert.equal(await page.locator("#images").getAttribute("aria-activedescendant"),null);
    assert.match(await page.locator("#images .empty").innerText(),/No images match/);
    assert.equal(await page.evaluate(()=>state.selected.name),"S21_M000005-02MS.png");
    await search("");
    assert.equal(await page.evaluate(()=>JSON.stringify([state.result,state.filters])),beforeSearch);
    assert.equal(statsCalls(),callsBeforeSearch);
    // Splitter: drag resizes and is remembered; double-click restores the default.
    const lowerHeight=async()=>(await page.locator("#lower").boundingBox()).height;
    const defaultHeight=await lowerHeight();
    await drag("#splitter",[300,4],[300,-196]);
    assert.ok(Math.abs(await lowerHeight()-defaultHeight-200)<3);
    assert.ok(Math.abs(await page.evaluate(()=>JSON.parse(localStorage.getItem(LAYOUT_KEY)).lowerHeight)-defaultHeight-200)<3);
    await page.locator("#statsMax").click();
    assert.equal(await page.locator("#image").isVisible(),false);
    // The splitter is inactive while maximized; keys must not overwrite the remembered height.
    const saved=await page.evaluate(()=>JSON.parse(localStorage.getItem(LAYOUT_KEY)).lowerHeight);
    assert.equal(await page.locator("#splitter").getAttribute("tabindex"),"-1");
    await page.locator("#splitter").dispatchEvent("keydown",{key:"ArrowUp"});
    assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem(LAYOUT_KEY)).lowerHeight),saved);
    assert.ok(await lowerHeight()>defaultHeight+300);
    await page.locator("#statsMin").click();
    assert.equal(await page.locator("#statsPane").isVisible(),false);
    assert.equal(await page.locator("#image").isVisible(),true);
    await page.locator("#statsMin").click();
    assert.ok(Math.abs(await lowerHeight()-defaultHeight-200)<3);
    // A remembered tall results area is capped when the window gets shorter, so it never leaves #center.
    await drag("#splitter",[300,4],[300,-396]);
    const fullSize=page.viewportSize();
    await page.setViewportSize({width:fullSize.width,height:620});
    await page.waitForTimeout(200);
    const centerBox=await page.locator("#center").boundingBox(),lowerBox=await page.locator("#lower").boundingBox();
    assert.ok(lowerBox.y+lowerBox.height<=centerBox.y+centerBox.height+1&&lowerBox.y>centerBox.y+150,JSON.stringify({centerBox,lowerBox}));
    await page.setViewportSize(fullSize);
    await page.locator("#splitter").dblclick();
    assert.ok(Math.abs(await lowerHeight()-defaultHeight)<3);
    await page.locator("#waferPane .collapse").click();
    assert.equal(await page.locator("#wafer").isVisible(),false);
    assert.deepEqual(await page.evaluate(()=>JSON.parse(localStorage.getItem(LAYOUT_KEY)).collapsed),{waferPane:true,chipPane:false,imagesPane:false});
    await page.locator("#waferPane .collapse").click();
    // Image list width: drag is remembered, double-click resets; collapsing keeps Up/Down on the image.
    const listWidth=async()=>(await page.locator("#imagesPane").boundingBox()).width;
    const defaultListWidth=await listWidth();
    await drag("#listSplitter",[4,300],[84,300]);
    assert.ok(Math.abs(await listWidth()-defaultListWidth-80)<3);
    assert.ok(Math.abs(await page.evaluate(()=>JSON.parse(localStorage.getItem(LAYOUT_KEY)).listWidth)-defaultListWidth-80)<3);
    await page.locator("#listSplitter").dblclick();
    assert.ok(Math.abs(await listWidth()-defaultListWidth)<3);
    const imageWidth=(await page.locator("#image").boundingBox()).width;
    await page.locator("#imagesPane .collapse").click();
    assert.equal(await page.locator("#images").isVisible(),false);
    assert.ok(await listWidth()<40);
    assert.ok((await page.locator("#image").boundingBox()).width>imageWidth+250);
    assert.equal(JSON.parse(await page.evaluate(()=>localStorage.getItem(LAYOUT_KEY))).collapsed.imagesPane,true);
    const beforeStep=await page.evaluate(()=>state.visible.findIndex(e=>e.id===state.selected.id));
    await page.locator("#image").focus();await page.keyboard.press("ArrowDown");
    assert.equal(await page.evaluate(()=>state.visible.findIndex(e=>e.id===state.selected.id)),beforeStep+1);
    await page.keyboard.press("ArrowUp");
    await page.locator("#imagesPane .collapse").click();
    assert.equal(await page.locator("#images").isVisible(),true);
    // Export menu collects every CSV export.
    await page.locator("#exportButton").click();
    assert.deepEqual(await page.locator("#exportItems button").allInnerTexts(),
      ["Center offset statistics (CSV)","Center offset statistics by PID (CSV)","PPE statistics by PID (CSV)","PPE values per pattern (CSV)","Raw measurement data - Comp (CSV)"]);
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#exportItems").isVisible(),false);
    // Exported CSV values spell the dataset out as Base / Comparison.
    assert.deepEqual(await page.evaluate(()=>{
      const saved=[],previous=backend;
      backend={exportCsv:(text,name)=>saved.push([name,JSON.parse(text)])};
      try{$("#exportStats").click();$("#rawCsv").click();}finally{backend=previous;}
      return saved.map(([name,rows])=>[name,[...new Set(rows.map(r=>r.dataset))],rows.some(r=>"role" in r)]);
    }),[["center_offset_statistics.csv",["Base","Comparison"],false],["measurements.csv",["Comparison"],false]]);
    // Expand: a second window follows the selection and shares the ROI, with its own pan and zoom.
    const [popup]=await Promise.all([page.waitForEvent("popup"),page.locator("#expand").click()]);
    await page.waitForFunction(()=>state.expand&&state.expand.canvas.width>200);
    await page.waitForFunction(()=>{
      const c=state.expand.canvas,d=c.getContext("2d").getImageData(c.width/2,c.height/2,1,1).data;return d[3]>0;
    });
    assert.equal(await popup.locator(".name").innerText(),await page.evaluate(()=>state.selected.name));
    assert.equal(await popup.locator(".background").innerText(),await page.locator("#imageBackground").innerText());
    assert.equal(await popup.locator(".warn").innerText(),"Synthetic chip coordinates; same-site comparison not validated");
    assert.doesNotMatch(await popup.locator("body").innerText(),/TEST ONLY/);
    assert.equal(await popup.title(),`${await page.title()} - Image`);
    const mainTransform=await page.evaluate(()=>JSON.stringify(state.transform));
    const ex=await page.evaluate(()=>({...state.expand.transform}));
    const popupBox=await popup.locator("canvas.image").boundingBox();
    await popup.keyboard.down("Control");
    await popup.mouse.move(popupBox.x+ex.x+ex.scale*20,popupBox.y+ex.y+ex.scale*30);await popup.mouse.down();
    await popup.mouse.move(popupBox.x+ex.x+ex.scale*120,popupBox.y+ex.y+ex.scale*90,{steps:5});await popup.mouse.up();
    await popup.keyboard.up("Control");
    const expandRoi=await page.evaluate(()=>state.filters.roi);
    for(let i=0;i<4;i++)assert.ok(Math.abs(expandRoi[i]-[20,30,120,90][i])<2,JSON.stringify(expandRoi));
    assert.equal(await page.evaluate(()=>JSON.stringify(state.transform)),mainTransform);
    await popup.mouse.move(popupBox.x+popupBox.width/2,popupBox.y+popupBox.height/2);
    await popup.mouse.wheel(0,-200);
    assert.equal(await page.evaluate(()=>JSON.stringify(state.transform)),mainTransform);
    const nextName=await page.evaluate(()=>state.visible[state.visible.indexOf(state.selected)+1].name);
    await popup.keyboard.press("ArrowDown");
    await page.waitForFunction(name=>state.selected.name===name&&state.image,nextName);
    assert.equal(await popup.locator(".name").innerText(),nextName);
    // Expand also shows the contour overlay, with its own pan and zoom.
    assert.equal(await popup.locator("canvas.contour").isVisible(),false);
    await page.locator("#contourViewButton").click();
    await page.waitForFunction(()=>state.contourData&&!state.contourLoading);
    await popup.locator("canvas.contour").waitFor({state:"visible"});
    await page.waitForFunction(()=>{
      const c=state.expand.contour.canvas;if(c.width<100)return false;
      const d=c.getContext("2d").getImageData(0,0,c.width,c.height).data;
      for(let i=3;i<d.length;i+=4)if(d[i]>0)return true;return false;
    });
    const contourBox=await popup.locator("canvas.contour").boundingBox(),imageBox=await popup.locator("canvas.image").boundingBox();
    assert.ok(contourBox.width>200&&imageBox.width>200&&contourBox.x>=imageBox.x+imageBox.width-1,JSON.stringify([imageBox,contourBox]));
    const mainContourTransform=await page.evaluate(()=>JSON.stringify(state.contourTransform));
    await popup.mouse.move(contourBox.x+contourBox.width/2,contourBox.y+contourBox.height/2);
    await popup.mouse.wheel(0,-200);
    await page.waitForFunction(()=>state.expand.contour.transform.zoom>1);
    assert.equal(await page.evaluate(()=>JSON.stringify(state.contourTransform)),mainContourTransform);
    await popup.screenshot({path:path.join(output,"preview-expand.png")});
    // A hidden contour canvas (statistics maximized) must not keep scheduling redraws.
    await page.locator("#statsMax").click();
    await page.waitForFunction(()=>document.querySelector("#contourCanvas").clientHeight===0);
    const hiddenFrames=await page.evaluate(async()=>{
      const frame=()=>new Promise(r=>requestAnimationFrame(r));
      for(let i=0;i<5;i++)await frame();
      let queued=0;
      for(let i=0;i<10;i++){await frame();if(state.contourFrame!==null)queued++;}
      return queued;
    });
    assert.equal(hiddenFrames,0,"Hidden contour canvas keeps scheduling redraws");
    await page.locator("#statsMax").click();
    await page.locator("#contourViewButton").click();
    assert.equal(await popup.locator("canvas.contour").isVisible(),false);
    await page.locator("#clearRoi").click();
    await Promise.all([popup.waitForEvent("close"),popup.keyboard.press("Escape").catch(()=>{})]);
    await page.waitForFunction(()=>imageViews().length===1&&state.expand===null);
    assert.deepEqual(errors,[]);assert.deepEqual(network,[]);
    const report={
      passed:true,windows_runtime_tested:false,workflow:"A",
      verified:["Ctrl+drag places ROI on both canvases; normal drags pan only; cancelled ROI ignored",
        "Removed interaction and pattern-display selectors",
        "Stats and raw-data dataset labels are Base / Comp (CSV values Base / Comparison); map 'Not applied' badge sits in the map heading",
        "Expand window shows the contour overlay beside the image with its own pan and zoom; hidden contour canvas stays idle",
        "Map dataset switch controls both maps and the image background (Comparison JPEG on demand, cached, PID-aligned); overlays remain BOTH",
        "One focus chip; focus and pan do not change analysis selection",
        "Apply captures one selection; pending edits during worker request remain pending",
        "Apply failure surfaced; previous snapshot retained; retry succeeds",
        "Map/table/graph sample counts agree on shared image-pair cohort",
        "Counterpart outside selection excluded; unpaired counts explicit",
        "Startup full calculation once; English UI; two-line dataset header; precompute measured-chip colors",
        "Fixed axes/bins; histogram/box metrics; scatter zero axes; 2.5px contours; 10x vectors",
        "Image type filters; stale export guard; empty selection; undo/reset; offline behavior",
        "Workbench rail order; Image list beside the Image panel (resizable, collapsible, Up/Down on the image); linked Show Base | Comp map switch; independent Base/Comp layer chips; selection shown as a center dot without recoloring contours; vertical color bars",
        "Raw measurement data drawer closed by default (edge tab), independent dataset tabs, PID first and optional # column",
        "Virtual image list reaches the last row; aria-activedescendant follows the selection; search by name, chip or Seq filters only the list",
        "Splitter drag remembered, capped after window shrink, double-click reset; horizontal scroll keeps the drawer reachable; statistics maximize/minimize; collapsible maps",
        "Export menu; Expand window follows selection, shares ROI, keeps its own pan and zoom"],
      initial_pairs:1288,initial_base_valid:22214,chip_8_7_01MS_pairs:128
    };
    fs.writeFileSync(path.join(output,"workflow-validation.json"),JSON.stringify(report,null,2)+"\n");
    console.log("PASS: approved workflow A; "+report.verified.join("; "));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
