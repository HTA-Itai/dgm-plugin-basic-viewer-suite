// Synthetic browser regression for Comparison-side MS cycling and PNO-dependent Image type options.
// Usage: NODE_PATH=<playwright node_modules> node test_selection_browser.cjs
const assert=require("node:assert/strict");
const {spawnSync}=require("node:child_process");
const {pathToFileURL}=require("node:url");
const path=require("node:path");
const fs=require("node:fs");
const os=require("node:os");
const {chromium}=require("playwright");

const python=process.env.VIEWER_PYTHON||"python3";
const work=fs.mkdtempSync(path.join(os.tmpdir(),"viewer-selection-"));
const fixture=`
import json,shutil,sqlite3,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import test_two_files
case=test_two_files.TwoFileTests();case.setUp()
out=Path(sys.argv[2])
for path in case.paths:
    target=out/path.name;shutil.copy(path,target)
    with sqlite3.connect(target) as db:
        # Same site as image 1 with another MS, plus one PNO 3 image with its own MS.
        for source_id,changes in ((1,dict(ms="02MS")),(1,dict(pno=3,ms="03MS"))):
            columns=[r[1] for r in db.execute("PRAGMA table_info(images)") if r[1]!="id"]
            row=dict(zip(columns,db.execute(f"SELECT {','.join(columns)} FROM images WHERE id=?",(source_id,)).fetchone()))
            row.update(changes,name=f"{changes['ms']}-{row['name']}")
            new=db.execute(f"INSERT INTO images({','.join(row)}) VALUES({','.join('?'*len(row))})",list(row.values())).lastrowid
            db.execute("INSERT INTO patterns SELECT ?,ordinal,layout_x,layout_y,px,py,dx,dy,magnitude,direction,available,valid,raw FROM patterns WHERE image_id=?",(new,source_id))
        manifest=json.loads(db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
        layout=dict(info=dict(SHOT_ARRAY=dict(X=10,Y=10),CHIP_SIZE_nm=dict(X=1000,Y=1000)),
                    cells=[dict(x=x,y=y,code=1) for y in range(10) for x in range(10)])
        db.execute("UPDATE metadata SET value=? WHERE key='layout'",(json.dumps(layout),))
        manifest.update(images=db.execute("SELECT COUNT(*) FROM images").fetchone()[0],patterns=db.execute("SELECT COUNT(*) FROM patterns").fetchone()[0])
        db.execute("UPDATE metadata SET value=? WHERE key='manifest'",(json.dumps(manifest),))
case.tearDown()
`;
const made=spawnSync(python,["-c",fixture,__dirname,work]);
if(made.status!==0)throw Error(made.stderr.toString());
const files=[path.join(work,"base.sqlite"),path.join(work,"comparison.sqlite")];
function command(operation,args){
  const source="import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from engine import Store;s=Store(Path(sys.argv[2]),Path(sys.argv[3]));op=sys.argv[4];args=json.loads(sys.argv[5]);v=s.index() if op=='index' else s.image(args['id']) if op=='image' else s.comparison_image(args['id']) if op=='comparison_image' else s.statistics(args);print(json.dumps(v,allow_nan=False))";
  const result=spawnSync(python,["-c",source,__dirname,...files,operation,JSON.stringify(args)],{maxBuffer:20*1024*1024});
  if(result.status!==0)throw Error(result.stderr.toString());
  return JSON.parse(result.stdout.toString());
}
(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROME||"/usr/bin/google-chrome",headless:true,args:["--no-sandbox"]});
  try{
    const page=await browser.newPage({viewport:{width:2200,height:1400}});
    const errors=[];
    page.on("pageerror",error=>errors.push(error.message));
    await page.route(/^https?:/,route=>route.abort());
    await page.exposeFunction("engineRequest",async(operation,args)=>command(operation,args));
    await page.addInitScript(()=>{window.testBackend=(op,args)=>window.engineRequest(op,args);});
    await page.goto(pathToFileURL(path.join(__dirname,"web.html")).href);
    await page.waitForFunction(()=>window.viewerReady&&state.result&&!state.applying);
    const options=async()=>page.$$eval("#ms option",nodes=>nodes.map(n=>n.value));

    // Image type options follow the selected PNO, and an unavailable type falls back to All.
    assert.deepEqual(await options(),["","01MS","02MS"]);
    // Two PNO values: the PNo selector is shown next to Image type.
    assert.equal(await page.locator("#pnoControl").isVisible(),true);
    assert.match(await page.locator("#pnoControl").innerText(),/^PNo/);
    await page.selectOption("#ms","02MS");
    await page.selectOption("#pno","3");
    assert.deepEqual(await options(),["","03MS"]);
    assert.equal(await page.evaluate(()=>state.filters.ms),"");
    assert.equal(await page.inputValue("#ms"),"");
    await page.click("#update");
    await page.waitForFunction(()=>state.result?.filters.pno===3&&!state.applying);
    assert.equal(await page.locator("#applyStatus").innerText().then(t=>/error|fail|unknown/i.test(t)),false);
    await page.click("#undo");
    assert.equal(await page.evaluate(()=>state.filters.pno),2);
    assert.equal(await page.inputValue("#ms"),"02MS");
    await page.selectOption("#ms","");
    await page.click("#update");
    await page.waitForFunction(()=>state.result?.filters.pno===2&&!state.applying);
    // Repeated clicks on one Chip map position cycle MS images in both dataset views.
    const pick=async()=>{
      const box=await page.locator("#chip").boundingBox();
      const point=await page.evaluate(()=>{const p=state.chipPoints[0];return [p.x,p.y];});
      await page.mouse.click(box.x+point[0],box.y+point[1]);
      await page.waitForTimeout(150);
      return page.evaluate(()=>state.selected.ms);
    };
    for(const role of ["base","comparison"]){
      await page.locator(`#chipPane [data-map-role="${role}"]`).click();
      await page.evaluate(()=>{state.focusChip=[8,7];state.chipPan={x:0,y:0};drawChip();});
      const points=await page.evaluate(()=>state.chipPoints.map(p=>[p.entry.role,p.entry.ms]));
      assert.deepEqual(points.map(p=>p[0]),[role,role]);
      const seen=[await pick(),await pick(),await pick()];
      assert.deepEqual(new Set(seen.slice(0,2)),new Set(["01MS","02MS"]),`${role}: ${seen}`);
      assert.equal(seen[2],seen[0],`${role}: ${seen}`);
      const ring=await page.evaluate(()=>state.chipPoints.filter(p=>isSelected(p.entry)).length);
      assert.equal(ring,1,`${role}: selected ring`);
    }
    assert.deepEqual(errors,[]);
    console.log("PASS: PNO-dependent Image type and Chip map MS cycling for Base and Comparison");
  }finally{
    await browser.close();
    fs.rmSync(work,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
