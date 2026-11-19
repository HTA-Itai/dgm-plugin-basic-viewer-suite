// Base-only session: Comp controls stay visible but disabled, PPE is unavailable, statistics cover Base only.
const assert=require("node:assert/strict");
const {spawnSync}=require("node:child_process");
const {pathToFileURL}=require("node:url");
const path=require("node:path");
const fs=require("node:fs");
const {chromium}=require("playwright");
const database=process.argv[2],output=process.argv[3]||path.dirname(path.dirname(database));
const appDirectory=process.argv[4]||__dirname;
const synthetic=process.argv[5]==="synthetic";
if(!database)throw Error("Supply a Base database, optional output directory and staged app directory");
function command(operation,args){
  const source="import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from engine import Store;s=Store(Path(sys.argv[2]),None);op=sys.argv[3];args=json.loads(sys.argv[4]);v=s.index() if op=='index' else s.image(args['id']) if op=='image' else s.contour_overlay(args) if op=='contour_overlay' else s.statistics(args);print(json.dumps(v,allow_nan=False))";
  const result=spawnSync("python3",["-c",source,appDirectory,database,operation,JSON.stringify(args)],{maxBuffer:20*1024*1024});
  if(result.status!==0)throw Error(result.stderr.toString());
  return JSON.parse(result.stdout.toString());
}
(async()=>{
  const browser=await chromium.launch({executablePath:"/usr/bin/google-chrome",headless:true,args:["--no-sandbox"]});
  try{
    const page=await browser.newPage({viewport:{width:1600,height:950}});
    const errors=[],calls=[];
    page.on("pageerror",error=>errors.push(error.message));
    await page.route(/^https?:/,route=>route.abort());
    await page.exposeFunction("engineRequest",async(operation,args)=>{calls.push(operation);return command(operation,args);});
    await page.addInitScript(()=>{window.testBackend=(op,args)=>window.engineRequest(op,args);});
    await page.goto(pathToFileURL(path.join(appDirectory,"web.html")).href);
    await page.waitForFunction(()=>window.viewerReady);
    await page.waitForFunction(()=>state.result&&!state.applying&&state.image);
    assert.equal(await page.evaluate(()=>single()),true);
    assert.equal(await page.evaluate(()=>!!state.index.metadata.test_chip_override),synthetic);
    assert.equal(await page.locator("#testWarning").isVisible(),synthetic);
    if(synthetic)assert.match(await page.locator("#testWarning").innerText(),/synthetic chip coordinates/i);

    assert.equal(await page.locator("#comparisonName").innerText(),"(not loaded)");
    assert.equal(await page.locator("#comparisonName").evaluate(e=>e.classList.contains("missing")),true);
    assert.equal(await page.locator("#datasets .base .slotClose").count(),0);
    assert.equal(await page.locator("#closeComparison").isVisible(),false);
    assert.equal(await page.locator("#openComparison").isEnabled(),true);
    assert.equal(await page.locator("#openComparison").innerText(),"Open Comp...");

    for(const selector of ['#waferPane [data-map-role="comparison"]','#chipPane [data-map-role="comparison"]',
      '[data-raw-role="comparison"]','[data-lower="ppe"]','[data-layer="comparison"]','[data-vector="comparison"]'])
      assert.equal(await page.locator(selector).isDisabled(),true,selector);
    assert.equal(await page.locator('[data-layer="comparison"]').isChecked(),false);
    for(const [group,attribute] of [["contour","layer"],["vectors","vector"]]){
      const button=page.locator(`[data-overlay-group="${group}"]`);
      assert.equal(await button.getAttribute("aria-pressed"),"true");
      for(const checked of [false,true]){
        await button.click();
        assert.equal(await page.locator(`[data-${attribute}="base"]`).isChecked(),checked);
        assert.equal(await page.locator(`[data-${attribute}="comparison"]`).isChecked(),false);
        assert.equal(await page.locator(`[data-${attribute}="comparison"]`).isDisabled(),true);
        assert.equal(await button.getAttribute("aria-pressed"),String(checked));
      }
    }
    assert.match(await page.locator('[data-lower="ppe"]').innerText(),/requires Comp/);
    assert.equal(await page.locator("#exportPpeStats").isDisabled(),true);
    assert.equal(await page.locator("#exportStats").isDisabled(),false);
    assert.equal(await page.locator("#exportOffsetPid").isDisabled(),false);
    assert.equal(await page.locator("#statsTitle").innerText(),"Applied statistics / Base only");

    assert.deepEqual([...new Set(await page.locator("#stats tbody tr td:first-child").allTextContents())],["Base"]);
    const counts=await page.evaluate(()=>({n:state.result.table.find(r=>r.role==="base"&&r.metric==="dx").n,
      graph:state.result.graph.base.dx.length,images:state.result.images,pairs:state.result.paired_images}));
    assert.equal(counts.n,counts.graph);assert.equal(counts.pairs,0);assert.ok(counts.images>0);
    assert.match(await page.locator("#statNote").innerText(),/Base only/);
    assert.match(await page.locator("#graphInfo").innerText(),/Comp not loaded/);

    await page.locator('[data-stats-view="pid"]').click();
    assert.equal(await page.locator("#statsScroll").isVisible(),false);
    assert.equal(await page.locator("#offsetPidScroll").isVisible(),true);
    const pidRows=await page.locator("#offsetPidTable tbody tr").count();
    assert.equal(pidRows,await page.evaluate(()=>state.result.offset_by_pid.table.filter(r=>r.role==="base").length));
    assert.ok(pidRows>0);
    assert.deepEqual([...new Set(await page.locator("#offsetPidTable tbody tr td:first-child").allTextContents())],["Base"]);
    assert.match(await page.locator("#statNote").innerText(),/By PID/);
    const saved=await page.evaluate(()=>{
      const saved=[];backend={exportCsv:(text,name)=>saved.push([name,JSON.parse(text)])};
      document.querySelector("#exportOffsetPid").click();document.querySelector("#exportStats").click();
      document.querySelector("#rawCsv").click();backend=null;return saved;
    });
    assert.deepEqual(saved.map(([name])=>name),["center_offset_by_pid.csv","center_offset_statistics.csv","measurements.csv"]);
    const [,pidCsv]=saved[0],[,statCsv]=saved[1],[,rawCsv]=saved[2];
    assert.equal(pidCsv.length,pidRows);
    assert.deepEqual(Object.keys(pidCsv[0]).slice(0,5),["dataset","pid","n","excluded","offset_x_mean"]);
    assert.ok(pidCsv.every(row=>row.dataset==="Base"&&row.cohort==="all selected Base images; Comp not loaded"&&row.comparison_dataset===""));
    assert.ok(statCsv.every(row=>row.dataset==="Base"&&row.images===counts.images&&!("image_pairs" in row)&&!("unpaired_base" in row)));
    assert.ok(rawCsv.length>0);
    assert.ok(pidCsv.every(row=>row.test_only_chip_override===synthetic));
    assert.ok(statCsv.every(row=>row.test_only_chip_override===synthetic));
    assert.ok(rawCsv.every(row=>row.TEST_ONLY_CHIP_OVERRIDE===synthetic));
    if(synthetic){
      const [popup]=await Promise.all([page.waitForEvent("popup"),page.locator("#expand").click()]);
      assert.match(await popup.locator(".warn").innerText(),/synthetic chip coordinates/i);
      await popup.close();
    }
    await page.locator('[data-stats-view="summary"]').click();
    assert.equal(await page.locator("#statsScroll").isVisible(),true);

    await page.locator("#contourViewButton").click();
    await page.waitForFunction(()=>state.contourData?.scope==="image"&&!state.contourLoading);
    assert.doesNotMatch(await page.locator("#contourStatus").innerText(),/Comparison/);
    for(const selector of ['[data-contour-role="comparison"]','[data-contour-role="both"]','[data-contour-display="compare"]'])
      assert.equal(await page.locator(selector).isDisabled(),true,selector);
    await page.locator('[data-contour-scope="selection"]').click();
    await page.waitForFunction(()=>state.contourData?.scope==="selection"&&!state.contourLoading);
    assert.ok(await page.evaluate(()=>state.contourData.pairs>0));

    await page.screenshot({path:path.join(output,"single-mode.png")});
    assert.equal(calls.includes("ppe_values"),false);
    assert.deepEqual(errors,[]);
    console.log(`single-mode browser OK: ${counts.images} Base images, ${pidRows} PID rows`);
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
