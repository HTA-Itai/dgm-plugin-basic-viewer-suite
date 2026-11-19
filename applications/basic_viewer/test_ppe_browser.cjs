// PPE tab check with real datasets: node test_ppe_browser.cjs BASE.sqlite COMPARISON.sqlite OUTPUT_DIR
const assert=require("node:assert/strict");
const {spawnSync}=require("node:child_process");
const {pathToFileURL}=require("node:url");
const path=require("node:path");
const fs=require("node:fs");
const {chromium}=require("playwright");
const [base,comparison,output]=process.argv.slice(2);
if(!output)throw Error("Supply BASE.sqlite COMPARISON.sqlite OUTPUT_DIR");
function command(operation,args){
  const source="import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from engine import Store;"+
    "s=Store(Path(sys.argv[2]),Path(sys.argv[3]));op=sys.argv[4];a=json.loads(sys.argv[5]);"+
    "v=s.index() if op=='index' else s.image(a['id']) if op=='image' else s.comparison_image(a['id']) if op=='comparison_image' else s.ppe_values(a) if op=='ppe_values' else s.statistics(a);"+
    "print(json.dumps(v,allow_nan=False))";
  const result=spawnSync("python3",["-c",source,__dirname,base,comparison,operation,JSON.stringify(args)],{maxBuffer:200*1024*1024});
  if(result.status!==0)throw Error(result.stderr.toString());
  return JSON.parse(result.stdout.toString());
}
(async()=>{
  fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({executablePath:"/usr/bin/google-chrome",headless:true,args:["--no-sandbox"]});
  try{
    const page=await browser.newPage({viewport:{width:1920,height:1150}});
    const errors=[];
    page.on("pageerror",error=>errors.push(error.message));
    await page.exposeFunction("engineRequest",async(operation,args)=>command(operation,args));
    await page.addInitScript(()=>{window.testBackend=(op,args)=>window.engineRequest(op,args);});
    await page.goto(pathToFileURL(path.join(__dirname,"web.html")).href);
    await page.waitForFunction(()=>window.viewerReady&&state.result&&!state.applying&&state.image,null,{timeout:600000});
    assert.equal(await page.locator("#ppeStatsPane").isVisible(),false);
    await page.click('[data-lower="ppe"]');
    assert.equal(await page.locator("#statsPane").isVisible(),false);
    assert.equal(await page.locator("#ppeStatsPane").isVisible(),true);
    assert.doesNotMatch(await page.locator("body").innerText(),/[\u3040-\u30ff\u3400-\u9fff]/);
    const pids=await page.evaluate(()=>state.result.ppe.pids);
    assert.equal(await page.locator("#ppeTable tbody tr").count(),pids.length+1);
    assert.equal(await page.locator("#ppeTable thead th").count(),9);
    assert.doesNotMatch(await page.locator("#ppeTable thead").innerText(),/3σ/);
    // Stacked histogram: one color per PID that has data; selecting a row keeps only that PID.
    const colorsDrawn=()=>page.evaluate(()=>{
      const ctx=document.querySelector("#ppeGraph").getContext("2d"),original=ctx.fillRect,seen=new Set();
      ctx.fillRect=function(...args){if(String(this.fillStyle).startsWith("#")&&this.fillStyle!=="#1c2837")seen.add(this.fillStyle);return original.apply(this,args);};
      drawPpeGraph();ctx.fillRect=original;return seen.size;
    });
    const withData=await page.evaluate(()=>state.result.ppe.table.filter(r=>r.pid!=="All"&&r.n>0).length);
    assert.equal(await colorsDrawn(),withData);
    await page.screenshot({path:path.join(output,"ppe-histogram-all.png")});
    const focus=await page.evaluate(()=>state.result.ppe.table.find(r=>r.pid!=="All"&&r.n>0).pid);
    await page.click(`#ppeTable tr[data-pid="${focus}"]`);
    assert.equal(await colorsDrawn(),1);
    assert.match(await page.locator("#ppeGraphInfo").innerText(),new RegExp(`PID ${focus}`));
    await page.screenshot({path:path.join(output,"ppe-histogram-pid.png")});
    await page.fill("#ppeBin","0.00001");await page.locator("#ppeBin").dispatchEvent("change");
    assert.match(await page.locator("#ppeGraphInfo").innerText(),/too small/);
    await page.click("#ppeBinAuto");
    assert.match(await page.locator("#ppeGraphInfo").innerText(),/Count per/);
    await page.click('[data-ppe-graph="box"]');
    await page.selectOption("#ppeComponent","y");
    assert.match(await page.locator("#ppeGraphInfo").innerText(),/One box per PID/);
    await page.screenshot({path:path.join(output,"ppe-boxplot.png")});
    // Scatter: one point per PID with data; Mean is centered on 0; clicking a point focuses its PID.
    await page.click('[data-ppe-graph="scatter"]');
    assert.equal(await page.locator("#ppeComponent").isVisible(),false);
    assert.equal(await page.locator("#ppeBin").isVisible(),false);
    assert.equal(await page.locator("#ppeStatControl").isVisible(),true);
    assert.match(await page.locator("#ppeGraphInfo").innerText(),/One point per PID.*centered on 0/);
    // Let ResizeObserver redraws settle so the point positions match the canvas that receives the click.
    await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
    const points=await page.evaluate(()=>state.ppePoints);
    assert.equal(points.length,withData);
    const other=points.find(p=>p.pid!==focus)||points[0];
    const box=await page.locator("#ppeGraph").boundingBox(),size=await page.evaluate(()=>{const c=document.querySelector("#ppeGraph");return [c.width,c.height];});
    await page.mouse.click(box.x+other.x*box.width/size[0],box.y+other.y*box.height/size[1]);
    assert.equal(await page.evaluate(()=>state.ppeSelected),other.pid);
    await page.screenshot({path:path.join(output,"ppe-scatter-mean.png")});
    await page.selectOption("#ppeStat","sd");
    assert.doesNotMatch(await page.locator("#ppeGraphInfo").innerText(),/centered on 0/);
    assert.ok(await page.evaluate(()=>state.ppePoints.every(p=>p.y<=document.querySelector("#ppeGraph").height)));
    await page.screenshot({path:path.join(output,"ppe-scatter-sd.png")});
    await page.click('[data-ppe-graph="hist"]');
    assert.equal(await page.locator("#ppeStatControl").isVisible(),false);
    // PID labels on the image overlay follow the checkbox.
    const labels=()=>page.evaluate(()=>{
      const ctx=document.querySelector("#image").getContext("2d"),original=ctx.fillText;let n=0;
      ctx.fillText=function(...args){n++;return original.apply(this,args);};drawImage();ctx.fillText=original;return n;
    });
    const expected=await page.evaluate(()=>state.image.labels.length);
    assert.ok(expected>0);
    assert.equal(await labels(),expected);
    await page.locator("#detailPane").screenshot({path:path.join(output,"ppe-pid-labels.png")});
    await page.uncheck("#pidLabels");
    assert.equal(await labels(),0);
    // Same cohort: PPE value rows agree with the table N; stale exports are blocked.
    const filters=await page.evaluate(()=>state.result.filters);
    const rows=command("ppe_values",filters);
    const all=await page.evaluate(()=>state.result.ppe.table[0].n);
    assert.equal(rows.filter(r=>r.ppe_x!==null).length,all);
    await page.evaluate(()=>setFilters({chips:[[state.focusChip[0],state.focusChip[1]]]}));
    assert.equal(await page.locator("#exportPpeValues").isDisabled(),true);
    assert.equal(await page.locator("#exportPpeStats").isDisabled(),true);
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,"ppe-validation.json"),JSON.stringify({pids:pids.length,valid_pairs:all,value_rows:rows.length},null,1));
    console.log(`PASS: PPE tab, ${pids.length} PIDs, ${all} valid pairs, ${rows.length} value rows; per-PID histogram colors, PID focus, bin width guard, box plot, scatter, PID labels, export guard`);
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
