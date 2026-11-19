"use strict";
const $=selector=>document.querySelector(selector);
const state={
  index:null, selected:null, image:null, bitmap:null, comparisonBitmap:null, rawRole:"base", mapRole:"base", graph:"hist",
  search:"", matched:[], filters:{pno:2,ms:"",chips:[],chipRect:null,roi:null}, history:[], revision:0,
  result:null, resultRevision:-1, resultTime:null, selectionRequest:0, statsRequest:0,
  transform:{scale:1,x:0,y:0}, columns:["PID","PixelX","PixelY","dx","dy","vector_valid"], expand:null,
  rawSort:null, drag:null, visible:[], chipPoints:[], waferCells:[], chipProject:null,
  baseline:null, baselines:new Map(), axes:null, mapColorMax:1, mapScales:{wafer:32,chip:32},
  focusChip:null, chipPan:{x:0,y:0}, entriesById:new Map(), applying:false,
  lower:"stats", ppeGraph:"hist", ppeSelected:null, ppeBin:null, ppeAxes:null,
  contourView:false, contourScope:"image", contourDisplay:"focus", contourFocus:"base",
  contourPid:null, contourData:null, contourKey:null, contourFlight:null, contourCache:new Map(),
  contourLoading:false, contourError:null, contourMessage:"", contourStarted:0, contourTimer:null,
  contourTransform:{zoom:1,x:0,y:0}, contourProjection:null, contourFrame:null,
  contourInteracting:false, contourSettleTimer:null,
  contourTarget:"roi", contourPids:[],
  contourRole:"both", contourSem:false, contourDesign:false, contourSemOpacity:.35,
  areaEnabled:false, areaData:null, areaError:null, areaFlight:null, areaCache:new Map(),
  contourMeasure:"length", areaChannel:"both",
  statsView:"summary",
};
const roles=["base","comparison"], colors={base:"#ff6767",comparison:"#4687ff",design:"#9fee9f"};
const roleLabel=role=>role==="base"?"Base":role==="comparison"?"Comp":String(role);
// Exported files spell the dataset out in full.
const roleName=role=>role==="base"?"Base":role==="comparison"?"Comparison":String(role);
const pidOverlapColor="#c080ff";
const areaKinds=["inward","outward"],areaColors={inward:"#ff9eae",outward:"#55c8ff"};
const areaMode=()=>state.contourMeasure==="area";
const contourGrid=data=>data.scope==="selection"||data.measure==="area"&&data.scope==="pid";
// Base-only session: Comp is not loaded, so pairing, PPE and every Comp control are unavailable.
const single=()=>!!state.index?.metadata?.single;
const shownRoles=()=>single()?["base"]:roles;
const NO_COMP="Comp not loaded";
const labels={dx:"Offset X",dy:"Offset Y",magnitude:"|Offset|"};
const rawLabel=key=>labels[key]?`${labels[key]} [nm]`:key;
const ppeLabels={x:"PPE X",y:"PPE Y",magnitude:"|PPE|"};
const PPE_DEFINITION="PPE = (Center - Layout)_Base - (Center - Layout)_Comparison per pattern; patterns pair by PID inside each matched image pair; both patterns must be valid";
const metric=()=>$("#metric").value;
const layer=name=>$(`[data-layer="${name}"]`).checked;
const vector=name=>$(`[data-vector="${name}"]`).checked;
let backend=null, callNumber=0;
const requestPrefix=Date.now().toString(36)+"-"+Math.random().toString(36).slice(2)+"-";
const pending=new Map();
function fail(error){$("#error").textContent=error.message||String(error);console.error(error);}
function request(command,args={}){
  if(window.testBackend) return window.testBackend(command,args);
  const identity=requestPrefix+String(++callNumber);
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{pending.delete(identity);reject(Error("Operation timed out: "+command));},
      command==="area_difference"?600000:120000);
    pending.set(identity,{resolve,reject,timer});
    backend.request(identity,command,JSON.stringify(args));
  });
}
function saveCsv(rows,name){
  if(!backend){fail(Error("CSV export is available in the Qt application"));return;}
  backend.exportCsv(JSON.stringify(rows),name);
}
function node(tag,text){const value=document.createElement(tag);if(text!==undefined)value.textContent=text;return value;}
const clone=value=>JSON.parse(JSON.stringify(value));
const fmt=value=>value===null||value===undefined?"—":typeof value==="number"?(Number.isInteger(value)?String(value):value.toFixed(4)):String(value);
function rectangle(a,b){return [Math.min(a[0],b[0]),Math.min(a[1],b[1]),Math.max(a[0],b[0]),Math.max(a[1],b[1])];}
function contains(x,y,rect){return !rect||x>=rect[0]&&x<=rect[2]&&y>=rect[1]&&y<=rect[3];}
function datasetEntries(role,filters=state.filters){
  return state.index.images.filter(e=>e.role===role&&e.pno===filters.pno&&(!filters.ms||e.ms===filters.ms));
}
function baseEntries(){return datasetEntries("base");}
// Selection is always a Base image; a Comparison entry is selected through its paired Base id.
function isSelected(entry){
  const id=state.selected?.id;
  return id!=null&&(entry.id===id||(entry.role==="comparison"&&entry.pair_id===id));
}
function msOptions(pno){
  return [...new Set(state.index.images.filter(e=>e.pno===pno).map(e=>e.ms))].sort();
}
function syncFilterControls(){
  const types=msOptions(state.filters.pno);
  if(state.filters.ms&&!types.includes(state.filters.ms))state.filters={...state.filters,ms:""};
  const select=$("#ms");
  select.replaceChildren(new Option("All",""),...types.map(ms=>new Option(ms,ms)));
  $("#pno").value=String(state.filters.pno);
  select.value=state.filters.ms;
}
function passesSpatial(entry,filters){
  return (!filters.chips.length||filters.chips.some(c=>c[0]===entry.chip_x&&c[1]===entry.chip_y))&&
    contains(entry.xrel,entry.yrel,filters.chipRect);
}
function filteredEntries(){
  if(single())return baseEntries().filter(e=>passesSpatial(e,state.filters));
  return baseEntries().filter(e=>{
    const other=state.entriesById.get(e.pair_id);
    return other&&passesSpatial(e,state.filters)&&passesSpatial(other,state.filters);
  });
}
function hasPending(){return !state.result||JSON.stringify(state.filters)!==JSON.stringify(state.result.filters);}
function chipsText(filters){return filters.chips.length?filters.chips.map(c=>`(${c})`).join(" "):"All";}
function filterSummary(filters){
  return `Image type: ${filters.ms||"All"} / Chips: ${filters.chips.length?filters.chips.map(c=>`(${c})`).join(" "):"All"} / Area: ${filters.chipRect?filters.chipRect.map(fmt).join(", "):"All"} / ROI: ${filters.roi?filters.roi.map(fmt).join(", "):"All"} / PNo: ${filters.pno}`;
}
function dirtyStatus(){
  const pending=hasPending();
  $("#applyStatus").textContent=state.applying?"Applying captured selection...":pending?"Pending changes":"Applied";
  document.querySelectorAll(".mapPending").forEach(badge=>badge.hidden=!state.result||!pending);
  $("#statsStatus").textContent=pending?"Previous applied snapshot / pending changes":"Applied snapshot";
  $("#exportStats").disabled=$("#exportOffsetPid").disabled=!state.result||pending||state.applying;
  $("#exportPpeStats").disabled=$("#exportPpeValues").disabled=single()||!state.result||pending||state.applying||state.exportingPpe;
  $("#ppeStatus").textContent=state.exportingPpe?"Preparing PPE values...":$("#statsStatus").textContent;
  $("#snapshot").textContent=state.result&&single()?
    `Applied ${state.resultTime} / ${state.result.images} Base images (Comp not loaded; all selected Base images) / ${filterSummary(state.result.filters)}`:
    state.result?
    `Applied ${state.resultTime} / ${state.result.paired_images} image pairs / Unpaired Base/Comparison: ${state.result.counts.base.unpaired}/${state.result.counts.comparison.unpaired} / Counterpart outside selection Base/Comparison: ${state.result.counts.base.counterpart_outside}/${state.result.counts.comparison.counterpart_outside} / ${filterSummary(state.result.filters)}`:
    "Initial full selection is applied automatically. Edit conditions, then Apply selection.";
}
function conditions(){
  const f=state.filters;
  for(const [id,button,label,value] of [["#chipsText","#clearChips","Chips",f.chips.length?chipsText(f):null],
    ["#areaText","#clearChipRect","Area",f.chipRect&&f.chipRect.map(fmt).join(", ")],
    ["#roiText","#clearRoi","ROI",f.roi&&f.roi.map(fmt).join(", ")]]){
    const text=`${label}: ${value||"All"}`,pill=$(id).parentElement;
    $(id).textContent=text;pill.title=text;pill.classList.toggle("active",!!value);$(button).disabled=!value;
  }
  $("#undo").disabled=!state.history.length;
  dirtyStatus();
}
async function setFilters(patch){
  state.history.push(clone(state.filters));
  if(state.history.length>30) state.history.shift();
  state.filters={...state.filters,...patch};
  state.revision++;
  syncFilterControls();
  conditions();renderList();drawMaps();drawImage();drawGraph();refreshContourView();
}
const ROW_HEIGHT=22;
// One search box: a row is shown when the text matches its name, its chip or its Seq number.
function searchMatch(entry,query){
  const q=query.trim().toLowerCase();
  if(!q)return true;
  if(entry.name.toLowerCase().includes(q))return true;
  const chip=q.replace(/[()\s]/g,"");
  if(/^-?\d+,-?\d+$/.test(chip)&&chip===`${entry.chip_x},${entry.chip_y}`)return true;
  return /^\d+$/.test(q)&&Number(q)===entry.seq;
}
function renderList(){
  state.matched=filteredEntries();
  state.visible=state.matched.filter(e=>searchMatch(e,state.search));
  $("#imageCount").textContent=state.search.trim()?`${state.visible.length} of ${state.matched.length}`:`${state.matched.length} images`;
  $("#images .vspace").style.height=`${state.visible.length*ROW_HEIGHT}px`;
  paintList();
  if(!state.matched.some(e=>e.id===state.selected?.id)){
    choose(state.visible[0]||state.matched[0]||null);
  }
}
// Only the rows in view are created, so long lists stay fast without paging.
function paintList(){
  const list=$("#images"),space=$("#images .vspace");
  const first=Math.max(0,Math.floor(list.scrollTop/ROW_HEIGHT)-5);
  const last=Math.min(state.visible.length,Math.ceil((list.scrollTop+list.clientHeight)/ROW_HEIGHT)+5);
  space.replaceChildren();
  if(!state.visible.length){
    list.removeAttribute("aria-activedescendant");
    const empty=node("div",state.matched.length?"No images match the search. Statistics are not affected by the search.":
      single()?"No Base images in the pending selection.":"No matched image pairs in the pending selection.");
    empty.className="empty";space.append(empty);return;
  }
  // Keep the selected row in the DOM even when scrolled away, so aria-activedescendant stays valid.
  const selectedIndex=state.visible.findIndex(e=>e.id===state.selected?.id);
  const indexes=[...Array(Math.max(0,last-first)).keys()].map(k=>first+k);
  if(selectedIndex>=0&&(selectedIndex<first||selectedIndex>=last))indexes.push(selectedIndex);
  if(selectedIndex>=0)list.setAttribute("aria-activedescendant",`imageRow-${state.visible[selectedIndex].id}`);
  else list.removeAttribute("aria-activedescendant");
  for(const i of indexes){
    const entry=state.visible[i],row=node("div");
    row.className="listRow"+(i%2?" alt":"");row.style.top=`${i*ROW_HEIGHT}px`;
    row.dataset.id=entry.id;row.id=`imageRow-${entry.id}`;row.setAttribute("role","option");
    row.setAttribute("aria-selected",String(state.selected?.id===entry.id));
    row.title=entry.name;
    for(const text of [entry.name,entry.seq,`${entry.chip_x},${entry.chip_y}`,entry.ms])row.append(node("span",text));
    row.onclick=()=>{choose(entry);$("#images").focus();};
    space.append(row);
  }
}
function revealInList(entry){
  const index=state.visible.indexOf(entry);if(index<0)return;
  const list=$("#images"),top=index*ROW_HEIGHT;
  if(top<list.scrollTop)list.scrollTop=top;
  else if(top+ROW_HEIGHT>list.scrollTop+list.clientHeight)list.scrollTop=top+ROW_HEIGHT-list.clientHeight;
}
function stepImage(delta){
  if(!state.visible.length)return;
  const at=state.visible.findIndex(e=>e.id===state.selected?.id);
  const index=at<0?0:Math.max(0,Math.min(state.visible.length-1,at+delta));
  if(state.visible[index]!==state.selected)choose(state.visible[index]);
}
async function choose(entry){
  if(state.selected?.id!==entry?.id){state.contourPids=[];state.contourTarget="roi";}
  state.selected=entry;state.image=null;state.bitmap=null;state.comparisonBitmap=null;
  state.areaData=null;state.areaError=null;syncAreaStatus();
  if(entry)setFocusChip([entry.chip_x,entry.chip_y]);
  if(entry)revealInList(entry);
  paintList();
  const token=++state.selectionRequest;
  setImageStatus(entry?"Loading...":single()?"No Base images in the pending selection.":
    "No matched image pairs in the pending selection. Unpaired images are excluded from analysis.");
  renderRaw();drawImage();drawMaps();refreshContourView();
  if(!entry)return;
  try{
    const payload=await request("image",{id:entry.id});
    if(token!==state.selectionRequest)return;
    const image=new Image();image.src=payload.image;await image.decode();
    if(token!==state.selectionRequest)return;
    state.image=payload;state.bitmap=image;for(const view of imageViews())fit(view);renderRaw();drawMaps();refreshContourView();
    setImageStatus(payload.notice||(single()?`Valid vectors: Base ${payload.vectors.base.length}`:
      `Valid vectors: Base ${payload.vectors.base.length} / Comparison ${payload.vectors.comparison.length}`));
    loadComparisonImage();
    refreshAreaImage();
  }catch(error){if(token===state.selectionRequest)setImageStatus(error.message);fail(error);}
}
function observeSize(canvas,owner,redraw){
  const resize=()=>{
    owner.requestAnimationFrame(()=>{
      const ratio=canvas.id==="contourCanvas"?owner.devicePixelRatio||1:1;
      const width=Math.max(1,Math.floor(Math.floor(canvas.clientWidth)*ratio));
      const height=Math.max(1,Math.floor(Math.floor(canvas.clientHeight)*ratio));
      if(canvas.width===width&&canvas.height===height)return;
      canvas.width=width;canvas.height=height;redraw();
    });
  };
  new owner.ResizeObserver(resize).observe(canvas);
  if(canvas.id==="contourCanvas")owner.addEventListener("resize",resize);
}
function setupCanvas(id){
  observeSize($(id),window,()=>{
    if(id==="#image")fit(mainView);else if(id==="#graph")drawGraph();else if(id==="#ppeGraph")drawPpeGraph();
    else if(id==="#contourCanvas")drawContourView();else drawMaps();
  });
}
// The main panel and the Expand window show the same image state; each keeps its own pan and zoom.
const mainView={get canvas(){return $("#image");},get transform(){return state.transform;},set transform(value){state.transform=value;}};
function imageViews(){
  if(state.expand?.win.closed)state.expand=null;
  return state.expand?[mainView,state.expand]:[mainView];
}
// The image background follows the map switch (Show Base | Comp). The Comparison JPEG is fetched only
// while Comparison is shown, and is drawn shifted by the PID alignment so overlays and the ROI stay in Base coordinates.
function backgroundRole(){
  return state.mapRole==="comparison"&&state.comparisonBitmap?.id===state.selected?.id&&state.comparisonBitmap.bitmap&&state.image?.shift?"comparison":"base";
}
function backgroundNote(){
  if(state.mapRole!=="comparison"||!state.image)return "";
  if(state.comparisonBitmap?.id!==state.selected?.id)return " (loading Comparison image...)";
  if(!state.comparisonBitmap.bitmap)return " (no same-chip Comparison image; showing Base)";
  if(!state.image.shift)return " (no common PID to align the Comparison image; showing Base)";
  return "";
}
async function loadComparisonImage(){
  const entry=state.selected,token=state.selectionRequest;
  if(state.mapRole!=="comparison"||!entry||!state.image||state.comparisonBitmap?.id===entry.id){drawImage();setImageStatus($("#imageStatus").textContent);return;}
  setImageStatus($("#imageStatus").textContent);
  try{
    const payload=await request("comparison_image",{id:entry.id});
    let bitmap=null;
    if(payload.image){bitmap=new Image();bitmap.src=payload.image;await bitmap.decode();}
    if(token!==state.selectionRequest)return;
    state.comparisonBitmap={id:entry.id,bitmap,name:payload.name};
    drawImage();setImageStatus($("#imageStatus").textContent);
  }catch(error){if(token===state.selectionRequest)setImageStatus(error.message);fail(error);}
}
function setImageStatus(text){
  const name=state.selected?state.selected.name:"No image selected";
  const background=!state.selected?"":backgroundRole()==="comparison"
    ?`Background: Comparison (${state.comparisonBitmap.name})`:`Background: Base${backgroundNote()}`;
  $("#imageName").textContent=name;$("#imageBackground").textContent=background;$("#imageStatus").textContent=text;
  if(state.contourView&&state.contourScope==="selection")queueContourDraw();
  if(imageViews().includes(state.expand)){
    state.expand.name.textContent=name;state.expand.background.textContent=background;state.expand.status.textContent=text;
  }
}
function fit(view=mainView){
  if(!state.image){drawImage();return;}
  const c=view.canvas,s=state.image;
  const scale=Math.min(c.width/s.width,c.height/s.height)*.94;
  view.transform={scale,x:(c.width-s.width*scale)/2,y:(c.height-s.height*scale)/2};
  drawImage();
}
function paintRect(ctx,rect,project){
  if(!rect)return;
  const a=project(rect[0],rect[1]),b=project(rect[2],rect[3]);
  ctx.strokeStyle="#ffd166";ctx.lineWidth=2;ctx.strokeRect(a[0],a[1],b[0]-a[0],b[1]-a[1]);
}
function ramp(value,min,max){
  if(value===null||value===undefined)return "#344252";
  const t=max===min?.5:Math.max(0,Math.min(1,(value-min)/(max-min)));
  return `rgb(${Math.round(45+180*t)},${Math.round(150-55*t)},${Math.round(205-100*t)})`;
}
function range(values){
  let min=Infinity,max=-Infinity;
  for(const value of values)if(value!==null&&Number.isFinite(value)){min=Math.min(min,value);max=Math.max(max,value);}
  return min===Infinity?[0,1]:[min,max];
}
function drawMaps(){
  if(!state.index)return;
  updateFocusOptions();
  drawWafer();drawChip();
}
function mapData(){
  return state.result;
}
// Vertical color bar beside each map; the arrow scale and value source are in its tooltip.
function mapLegend(id,scale){
  const element=$(id),bar=node("span");
  bar.className="ramp";
  const source=state.result?(hasPending()?"APPLIED values / pending selection differs":"APPLIED values"):"Computing initial selection...";
  element.replaceChildren(node("span",Number(state.mapColorMax.toPrecision(3)).toString()),bar,node("span","0 nm"));
  element.title=`Color: median |Offset|, 0 to ${fmt(state.mapColorMax)} nm\nArrow: 1 nm = ${scale.toFixed(1)} screen px\n${source}`;
  element.dataset.arrowScale=scale.toFixed(1);
}
function mapArrow(ctx,x,y,entry,scale){
  if(entry?.dx===null||entry?.dy===null||entry?.dx===undefined||entry?.dy===undefined)return;
  const dx=entry.dx*scale,dy=-entry.dy*scale,length=Math.hypot(dx,dy);
  ctx.strokeStyle=ramp(entry.magnitude,0,state.mapColorMax);ctx.lineWidth=2;
  if(length===0){ctx.strokeRect(x-2,y-2,4,4);return;}
  const ex=x+dx,ey=y+dy,angle=Math.atan2(dy,dx),head=Math.min(5,length*.5);
  ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(ex,ey);
  for(const a of [-.5,.5]){ctx.moveTo(ex,ey);ctx.lineTo(ex-head*Math.cos(angle+a),ey-head*Math.sin(angle+a));}
  ctx.stroke();
}
function drawWafer(){
  const c=$("#wafer"),ctx=c.getContext("2d");ctx.clearRect(0,0,c.width,c.height);
  const role=state.mapRole,layout=state.index.metadata.layouts[role];
  const cells=layout.cells.filter(c=>c.code!==0),nx=layout.info.SHOT_ARRAY.X,ny=layout.info.SHOT_ARRAY.Y;
  const pitch=layout.info.CHIP_SIZE_nm;
  const unit=Math.min((c.width-18)/(nx*pitch.X),(c.height-24)/(ny*pitch.Y));
  const w=pitch.X*unit,h=pitch.Y*unit,ox=(c.width-nx*w)/2,oy=(c.height-ny*h)/2;
  const data=mapData()?.maps.filter(e=>e.role===role)||[];
  const measured=new Set(datasetEntries(role,state.result?.filters||state.filters).map(e=>`${e.chip_x},${e.chip_y}`));
  const all=state.baseline?.maps||[];
  const longest=Math.max(1,...all.map(e=>Math.hypot(e.dx||0,e.dy||0)));
  const scale=Math.max(.1,Math.min(32,Math.min(w,h)*.8/longest));state.mapScales.wafer=scale;
  state.waferCells=[];
  for(const cell of cells){
    const x=ox+cell.x*w,y=oy+(ny-1-cell.y)*h,entry=data.find(e=>e.x===cell.x&&e.y===cell.y);
    ctx.fillStyle=measured.has(`${cell.x},${cell.y}`)?"#2c536b":"#344252";ctx.fillRect(x+1,y+1,w-2,h-2);
    ctx.strokeStyle="#61748a";ctx.lineWidth=1;ctx.strokeRect(x,y,w,h);
    if(!measured.has(`${cell.x},${cell.y}`)){
      ctx.strokeStyle="#546172";ctx.beginPath();ctx.moveTo(x,y+h);ctx.lineTo(x+w,y);ctx.stroke();
    }
    if(state.filters.chips.some(p=>p[0]===cell.x&&p[1]===cell.y)){
      ctx.strokeStyle="#ffd166";ctx.lineWidth=3;ctx.strokeRect(x+1,y+1,w-2,h-2);
    }
    if(state.selected?.chip_x===cell.x&&state.selected?.chip_y===cell.y){
      ctx.fillStyle="white";ctx.beginPath();ctx.arc(x+w/2,y+h/2,2,0,Math.PI*2);ctx.fill();
    }
    mapArrow(ctx,x+w/2,y+h/2,entry,scale);
    state.waferCells.push({...cell,measured:measured.has(`${cell.x},${cell.y}`),data:entry,box:[x,y,x+w,y+h]});
  }
  mapLegend("#mapScale",scale);
}
function setFocusChip(chip){
  if(JSON.stringify(chip)!==JSON.stringify(state.focusChip)){
    state.focusChip=chip;state.chipPan={x:0,y:0};
  }
}
function updateFocusOptions(){
  const chips=new Map();
  const add=entry=>chips.set(`${entry.chip_x},${entry.chip_y}`,[entry.chip_x,entry.chip_y]);
  // Keep the applied context available while a different selection is being edited.
  for(const entry of datasetEntries(state.mapRole,state.result?.filters||state.filters))add(entry);
  for(const entry of datasetEntries(state.mapRole))add(entry);
  const keys=[...chips.keys()].sort();
  if(!state.focusChip||!chips.has(state.focusChip.join(","))){
    const selected=state.selected&&`${state.selected.chip_x},${state.selected.chip_y}`;
    setFocusChip(chips.get(selected)||chips.get(keys[0])||null);
  }
  const select=$("#focusChip");select.replaceChildren();
  for(const key of keys)select.add(new Option(`(${key})`,key));
  select.value=state.focusChip?.join(",")||"";select.disabled=!keys.length;
}
function drawChip(){
  const c=$("#chip"),ctx=c.getContext("2d");ctx.clearRect(0,0,c.width,c.height);
  const role=state.mapRole;
  const inFocus=e=>state.focusChip&&e.chip_x===state.focusChip[0]&&e.chip_y===state.focusChip[1];
  const appliedEntries=datasetEntries(role,state.result?.filters||state.filters).filter(inFocus);
  const draftEntries=datasetEntries(role).filter(inFocus);
  const entries=[...new Map([...appliedEntries,...draftEntries].map(e=>[e.id,e])).values()];
  state.chipPoints=[];state.chipProject=null;
  const data=new Map((mapData()?.image_maps||[]).filter(e=>e.role===role).map(e=>[e.id,e]));
  const scale=32;state.mapScales.chip=scale;mapLegend("#chipScale",scale);
  if(!entries.length){ctx.fillStyle="#aec1d9";ctx.fillText("No measured images in selected chips",8,22);return;}
  const [xmin,xmax]=range(entries.map(e=>e.xrel)),[ymin,ymax]=range(entries.map(e=>e.yrel));
  const pad=32,width=Math.max(1,c.width-2*pad),height=Math.max(1,c.height-2*pad),pan=state.chipPan;
  const project=(x,y)=>[pad+(x-xmin)/(xmax-xmin||1)*width+pan.x,c.height-pad-(y-ymin)/(ymax-ymin||1)*height+pan.y];
  state.chipProject={forward:project,inverse:(x,y)=>[
    xmin+(x-pad-pan.x)/width*(xmax-xmin||1),ymin+(c.height-pad-y+pan.y)/height*(ymax-ymin||1)]};
  ctx.strokeStyle="#526982";ctx.strokeRect(pad+pan.x,pad+pan.y,width,height);
  for(const entry of entries){
    const [x,y]=project(entry.xrel,entry.yrel);
    ctx.fillStyle="#536273";
    ctx.beginPath();ctx.arc(x,y,2,0,Math.PI*2);ctx.fill();
    const value=data.get(entry.id);
    mapArrow(ctx,x,y,value,scale);
    if(isSelected(entry)){ctx.strokeStyle="#ffd166";ctx.lineWidth=2;ctx.beginPath();ctx.arc(x,y,6,0,Math.PI*2);ctx.stroke();}
    state.chipPoints.push({entry,x,y,data:value});
  }
  paintRect(ctx,state.filters.chipRect,project);
  if(state.drag?.kind==="chipRoi")paintRect(ctx,rectangle(state.drag.start,state.drag.end),(x,y)=>[x,y]);
}
function drawImage(){
  for(const view of imageViews())paintImage(view);
}
function syncOverlayGroups(){
  document.querySelectorAll("[data-overlay-group]").forEach(button=>{
    const inputs=[...button.parentElement.querySelectorAll("input:not(:disabled)")];
    const checked=inputs.filter(input=>input.checked).length;
    button.disabled=!inputs.length;
    button.setAttribute("aria-pressed",checked===inputs.length?"true":checked?"mixed":"false");
  });
}
// ROI targets come from every pattern with a contour, PID or not; PID filtering applies only to clicked PIDs.
function selectedContourPatterns(){
  if(state.contourTarget==="pids")return (state.image?.labels||[]).filter(label=>state.contourPids.includes(label.pid));
  return (state.image?.contour_patterns||[]).filter(pattern=>contains(pattern.x,pattern.y,state.filters.roi));
}
function contourTargetLabel(label){
  return state.contourTarget==="pids"?state.contourPids.includes(label.pid):
    label.contour_index!=null&&contains(label.x,label.y,state.filters.roi);
}
function selectContourPattern(point,view,additive){
  if(!state.contourView||state.contourScope==="selection")return;
  const [x,y]=point,ctx=view.canvas.getContext("2d");
  const candidates=(state.image.labels||[]).filter(label=>{
    const points=state.image.layers.base[label.contour_index];
    if(points?.length){
      const path=new Path2D();
      points.forEach(([px,py],i)=>{if(i)path.lineTo(px,py);else path.moveTo(px,py);});
      path.closePath();
      if(ctx.isPointInPath(path,x,y))return true;
    }
    return Math.hypot(label.x-x,label.y-y)*view.transform.scale<=10;
  }).sort((a,b)=>Math.hypot(a.x-x,a.y-y)-Math.hypot(b.x-x,b.y-y));
  const hit=candidates[0];
  if(!hit){setImageStatus("No pattern at this position. Click inside a contour or near its PID.");return;}
  if(state.image.labels.filter(label=>label.pid===hit.pid).length!==1){
    fail(Error(`PID ${hit.pid} is not unique in this image`));return;
  }
  if(state.contourScope==="pid")state.contourPid=hit.pid;
  else{
    state.contourTarget="pids";
    state.contourPids=additive?(state.contourPids.includes(hit.pid)?
      state.contourPids.filter(pid=>pid!==hit.pid):[...state.contourPids,hit.pid]):[hit.pid];
  }
  $("#contourPid").value=state.contourPid||"";
  drawImage();refreshContourView();
}
function paintSelectionDots(ctx,labels,scale){
  const radius=4.5/scale;
  ctx.save();
  ctx.fillStyle="#ffd166";ctx.shadowColor="rgba(255,209,102,.9)";ctx.shadowBlur=14;
  for(const label of labels){ctx.beginPath();ctx.arc(label.x,label.y,radius,0,Math.PI*2);ctx.fill();}
  ctx.shadowBlur=0;ctx.strokeStyle="rgba(0,0,0,.85)";ctx.lineWidth=1.5/scale;
  for(const label of labels){ctx.beginPath();ctx.arc(label.x,label.y,radius,0,Math.PI*2);ctx.stroke();}
  ctx.restore();
}
function syncAreaStatus(){
  $("#areaLegend").hidden=$("#areaStatus").hidden=!state.areaEnabled;
  $("#areaRetry").hidden=!state.areaEnabled||!state.areaError;
  const data=state.areaData;
  $("#areaStatus").textContent=state.areaError||(!state.selected?"Select an image.":
    !data?"Computing area difference...":
    `${data.patterns.length} compared Base patterns / ${data.issues.length} excluded. `+
    areaKinds.map(kind=>`${kind}: ${fmt(data.patterns.reduce((sum,p)=>sum+p[kind].area_nm2,0))} nm²`).join(" / "));
  $("#areaStatus").title=data?.issues.map(item=>`PID ${item.pid??"(missing)"}: ${item.reason}`).join("\n")||"";
}
async function refreshAreaImage(){
  if(!state.areaEnabled||single()||!state.image)return;
  const id=state.selected.id;
  if(state.areaData?.id===id){syncAreaStatus();return;}
  if(state.areaCache.has(id)){
    state.areaData=state.areaCache.get(id);state.areaError=null;syncAreaStatus();drawImage();return;
  }
  syncAreaStatus();
  if(state.areaFlight!==null)return;
  state.areaFlight=id;
  try{
    const data=await request("area_difference",{scope:"image",id});
    for(const pattern of data.patterns)for(const kind of areaKinds){
      const path=new Path2D();
      for(const ring of pattern[kind].rings){
        ring.forEach(([x,y],i)=>{if(i)path.lineTo(x,y);else path.moveTo(x,y);});path.closePath();
      }
      pattern[kind].path=path;delete pattern[kind].rings;
    }
    state.areaCache.set(id,data);
    while(state.areaCache.size>4)state.areaCache.delete(state.areaCache.keys().next().value);
    if(state.selected?.id===id){state.areaData=data;state.areaError=null;syncAreaStatus();drawImage();}
  }catch(error){
    if(state.selected?.id===id){state.areaError=error.message;syncAreaStatus();fail(error);}
    else console.error(error);
  }finally{
    state.areaFlight=null;
    if(state.areaEnabled&&state.selected?.id!==id)refreshAreaImage();
  }
}
function paintImage(view){
  const c=view.canvas,ctx=c.getContext("2d");
  ctx.setTransform(1,0,0,1,0,0);ctx.clearRect(0,0,c.width,c.height);
  if(!state.image)return;
  const s=state.image,{scale,x,y}=view.transform;
  const multiplier=Number($("#multiplier").value);
  if(!Number.isFinite(multiplier)||multiplier<0||multiplier>100){setImageStatus("Vector scale must be between 0 and 100");return;}
  ctx.save();ctx.setTransform(scale,0,0,scale,x,y);
  ctx.beginPath();ctx.rect(0,0,s.width,s.height);ctx.clip();
  {
    if(layer("image")&&backgroundRole()==="comparison")ctx.drawImage(state.comparisonBitmap.bitmap,s.shift[0],s.shift[1]);
    else if(layer("image")&&state.bitmap)ctx.drawImage(state.bitmap,0,0);
    if(state.areaEnabled&&state.areaData?.id===state.selected?.id){
      ctx.save();ctx.globalAlpha=.78;
      for(const pattern of state.areaData.patterns)for(const kind of areaKinds){
        ctx.fillStyle=areaColors[kind];ctx.fill(pattern[kind].path,"evenodd");
      }
      ctx.restore();
    }
    for(const name of ["design",...roles]){
      if(!layer(name))continue;
      ctx.strokeStyle=colors[name];ctx.lineWidth=2.5/scale;ctx.beginPath();
      const polys=name==="design"?s.design:s.layers[name];
      for(const points of polys){
        ctx.moveTo(...points[0]);for(const point of points.slice(1))ctx.lineTo(...point);ctx.closePath();
      }ctx.stroke();
    }
    // Selection is a glowing dot at the design position, under the vectors; contour colors stay unchanged.
    if(state.contourView&&state.contourScope!=="selection"){
      const selected=state.contourScope==="pid"?s.labels.filter(label=>label.pid===state.contourPid):selectedContourPatterns();
      paintSelectionDots(ctx,selected,scale);
    }
    for(const role of roles)if(vector(role)){
      ctx.strokeStyle=colors[role];ctx.lineWidth=1.5/scale;
      for(const v of s.vectors[role]){
        const [sx,sy]=v.start,ex=sx+v.delta[0]*multiplier,ey=sy+v.delta[1]*multiplier;
        const angle=Math.atan2(ey-sy,ex-sx);
        ctx.beginPath();ctx.moveTo(sx,sy);ctx.lineTo(ex,ey);
        if(Math.hypot(ex-sx,ey-sy)>0)for(const delta of [-.5,.5]){
          ctx.moveTo(ex,ey);ctx.lineTo(ex-4*Math.cos(angle+delta),ey-4*Math.sin(angle+delta));
        }ctx.stroke();
      }
    }
  }
  ctx.restore();
  const project=(a,b)=>[a*scale+x,b*scale+y];
  if($("#pidLabels").checked){
    ctx.font="bold 12px sans-serif";ctx.textAlign="left";ctx.lineWidth=3;ctx.strokeStyle="rgba(0,0,0,.85)";
    for(const label of s.labels||[]){
      const [lx,ly]=project(label.x,label.y);
      ctx.fillStyle=label.pid===state.ppeSelected||state.contourView&&state.contourScope!=="selection"&&
        (state.contourScope==="pid"?label.pid===state.contourPid:contourTargetLabel(label))?"#ffd166":"#ffffff";
      ctx.strokeText(label.pid,lx+6,ly-6);ctx.fillText(label.pid,lx+6,ly-6);
    }
  }
  paintRect(ctx,state.filters.roi,project);
  if(state.drag?.kind==="roi")paintRect(ctx,rectangle(state.drag.start,state.drag.end),project);
}
const densityPalettes={
  base:[[0,0,0],[70,0,4],[160,20,20],[235,70,60],[255,150,130],[255,225,215]],
  comparison:[[0,0,0],[0,25,70],[0,85,160],[30,160,230],[110,215,255],[215,245,255]],
};
function palette(stops,value){
  const t=Math.max(0,Math.min(1,value))*(stops.length-1),index=Math.min(stops.length-2,Math.floor(t)),fraction=t-index;
  return stops[index].map((channel,i)=>channel+(stops[index+1][i]-channel)*fraction);
}
function densityPixels(data){
  if(data.measure==="area"){
    const pixels=new Uint8ClampedArray(data.bins*data.bins*4),max=data.max_area;
    const [inwardColor,outwardColor]=areaKinds.map(kind=>[1,3,5].map(i=>parseInt(areaColors[kind].slice(i,i+2),16)));
    for(let i=0;i<data.inward.length;i++){
      const inward=state.areaChannel==="outward"?0:data.inward[i];
      const outward=state.areaChannel==="inward"?0:data.outward[i],sum=inward+outward;
      const fraction=sum?inward/sum:0,brightness=sum/max;
      const color=inwardColor.map((channel,k)=>channel*fraction+outwardColor[k]*(1-fraction));
      color.forEach((value,k)=>pixels[i*4+k]=value*brightness);
      pixels[i*4+3]=sum?255:0;
    }
    return pixels;
  }
  const {bins,base,comparison}=data,pixels=new Uint8ClampedArray(bins*bins*4);
  const max=data.max_density||1,totalMax=data.max_total||1;
  for(let i=0;i<bins*bins;i++){
    const b=base[i],c=comparison[i],sum=b+c;let color;
    if(state.contourDisplay==="compare"){
      if(!sum)color=[0,0,0];
      else{
        const ratio=b/sum,stops=ratio<.5
          ?[[85,200,255],[200,200,210]]
          :[[200,200,210],[255,103,103]];
        color=palette(stops,ratio<.5?ratio*2:(ratio-.5)*2).map(channel=>channel*Math.sqrt(Math.min(1,sum/totalMax)));
      }
    }else{
      const focused=state.contourFocus,other=focused==="base"?"comparison":"base";
      const primary=focused==="base"?b:c,secondary=other==="base"?b:c;
      const mainColor=palette(densityPalettes[focused],primary/max);
      const otherColor=palette(densityPalettes[other],secondary/max);
      const alpha=.24*Math.sqrt(Math.min(1,secondary/max));
      color=mainColor.map((channel,index)=>channel*(1-alpha)+otherColor[index]*alpha);
    }
    pixels[i*4]=color[0];pixels[i*4+1]=color[1];pixels[i*4+2]=color[2];pixels[i*4+3]=sum?255:0;
  }
  return pixels;
}
// The main contour panel and the Expand window share the contour data; each keeps its own pan, zoom and raster.
const mainContour={
  get canvas(){return $("#contourCanvas");},slot:"raster",
  get transform(){return state.contourTransform;},set transform(value){state.contourTransform=value;},
  get projection(){return state.contourProjection;},set projection(value){state.contourProjection=value;},
};
function contourViews(){
  const expand=imageViews().includes(state.expand)&&state.expand.contour;
  return expand?[mainContour,expand]:[mainContour];
}
function contourSize(canvas){
  return {width:Math.max(1,Math.floor(canvas.clientWidth)),height:Math.max(1,Math.floor(canvas.clientHeight)),
    ratio:canvas.ownerDocument.defaultView?.devicePixelRatio||1};
}
function contourProjection(view,bounds,{right=20,left=48,top=28,bottom=42,fit=.9}={}){
  const {width,height}=contourSize(view.canvas);
  const area={x:left,y:top,width:Math.max(1,width-left-right),height:Math.max(1,height-top-bottom)};
  const center=[area.x+area.width/2,area.y+area.height/2],t=view.transform;
  const spanX=Math.max(1,bounds[2]-bounds[0]),spanY=Math.max(1,bounds[3]-bounds[1]);
  const scale=Math.min(area.width/spanX,area.height/spanY)*fit*t.zoom;
  const x=center[0]+t.x-(bounds[0]+bounds[2])/2*scale;
  const y=center[1]+t.y+(bounds[1]+bounds[3])/2*scale;
  view.projection={center,scale,x,y};
  return {area,scale,x,y};
}
function contourReference(role=backgroundRole()){
  if(!state.image)return null;
  const frame=state.image.reference_frames[role];
  if(!frame)throw Error(`Missing ${role} reference frame`);
  const bitmap=role==="base"?state.bitmap:state.comparisonBitmap.bitmap;
  const name=role==="base"?state.selected.name:state.comparisonBitmap.name;
  const designs=role==="base"?state.image.design:state.image.comparison_design;
  state.image.referencePaths??={};
  const [anchorX,anchorY]=frame.design_center;
  if(!state.image.referencePaths[role]){
    const path=new Path2D();
    for(const points of designs){
      points.forEach(([px,py],i)=>{
        const x=(px-anchorX)*frame.nm_x,y=(anchorY-py)*frame.nm_y;
        if(i)path.lineTo(x,y);else path.moveTo(x,y);
      });
      path.closePath();
    }
    state.image.referencePaths[role]=path;
  }
  return {role,frame,bitmap,name,path:state.image.referencePaths[role]};
}
function drawDensityCanvas(view,data){
  const canvas=view.canvas,ctx=canvas.getContext("2d"),{width,height}=contourSize(canvas);
  const isArea=data.measure==="area",right=188;
  const extent=data.extent_nm;
  const projection=contourProjection(view,data.scope==="pid"?data.bounds:[-extent,-extent,extent,extent],
    {right,left:58,top:8,bottom:30,fit:.98});
  const {area,scale}=projection,size=2*extent*scale,x=projection.x-extent*scale,y=projection.y-extent*scale;
  const imageKey=isArea?"area:"+state.areaChannel:state.contourDisplay+":"+state.contourFocus;
  if(data.imageKey!==imageKey){
    const image=document.createElement("canvas");image.width=data.bins;image.height=data.bins;
    const imageContext=image.getContext("2d"),imageData=imageContext.createImageData(data.bins,data.bins);
    imageData.data.set(densityPixels(data));imageContext.putImageData(imageData,0,0);
    data.image=image;data.imageKey=imageKey;
  }
  ctx.save();ctx.beginPath();ctx.rect(area.x,area.y,area.width,area.height);ctx.clip();
  ctx.fillStyle="#000";ctx.fillRect(x,y,size,size);
  ctx.imageSmoothingEnabled=!isArea;ctx.imageSmoothingQuality="high";
  const reference=data.scope==="selection"&&(state.contourSem||state.contourDesign)?
    contourReference(isArea?"base":backgroundRole()):null;
  if(reference&&state.contourSem){
    // The density origin is this image's design layout center, not its image center.
    const frame=reference.frame,[anchorX,anchorY]=frame.design_center;
    const w=frame.width*frame.nm_x*scale,h=frame.height*frame.nm_y*scale;
    ctx.save();ctx.globalAlpha=state.contourSemOpacity;
    ctx.drawImage(reference.bitmap,projection.x-anchorX*frame.nm_x*scale,projection.y-anchorY*frame.nm_y*scale,w,h);
    ctx.restore();
  }
  ctx.drawImage(data.image,x,y,size,size);
  if(reference&&state.contourDesign){
    ctx.save();ctx.transform(scale,0,0,-scale,projection.x,projection.y);
    const frame=reference.frame,[anchorX,anchorY]=frame.design_center;
    ctx.beginPath();ctx.rect(-anchorX*frame.nm_x,(anchorY-frame.height)*frame.nm_y,
      frame.width*frame.nm_x,frame.height*frame.nm_y);ctx.clip();
    ctx.strokeStyle=colors.design;ctx.lineWidth=1.5/scale;ctx.stroke(reference.path);ctx.restore();
  }
  ctx.strokeStyle="#3b506b";ctx.strokeRect(x,y,size,size);
  ctx.strokeStyle="rgba(255,255,255,.55)";ctx.beginPath();
  ctx.moveTo(projection.x-7,projection.y);ctx.lineTo(projection.x+7,projection.y);
  ctx.moveTo(projection.x,projection.y-7);ctx.lineTo(projection.x,projection.y+7);ctx.stroke();ctx.restore();
  const step=niceStep(area.width/scale/6);
  ctx.font="10px sans-serif";ctx.fillStyle="#8fa6c2";ctx.textAlign="center";
  const minimum=(area.x-projection.x)/scale,maximum=(area.x+area.width-projection.x)/scale;
  for(let value=Math.ceil(minimum/step)*step;value<=maximum;value+=step)
    ctx.fillText(fmt(value),projection.x+value*scale,height-25);
  ctx.fillText("X [nm]",area.x+area.width/2,height-10);
  ctx.textAlign="right";ctx.textBaseline="middle";
  const top=(projection.y-area.y)/scale,bottom=(projection.y-area.y-area.height)/scale;
  for(let value=Math.ceil(bottom/step)*step;value<=top;value+=step)
    ctx.fillText(fmt(value),area.x-6,projection.y-value*scale);
  ctx.save();ctx.translate(12,area.y+area.height/2);ctx.rotate(-Math.PI/2);ctx.textAlign="center";
  ctx.fillText("Y [nm]",0,0);ctx.restore();ctx.textBaseline="alphabetic";
  ctx.font="11px sans-serif";
  if(isArea){
    const left=width-right+16;
    ctx.textAlign="left";
    for(const [i,kind] of areaKinds.entries()){
      ctx.fillStyle=areaColors[kind];ctx.fillText(kind==="inward"?"Inward: Base \\ Comp":"Outward: Comp \\ Base",left,34+i*18);
    }
    ctx.fillStyle="#aec1d9";
    ["Mean area [nm² / cell]",`0 to ${data.max_area.toPrecision(4)}`,
      "Shared linear scale","Mixed hue = both channels","No cancellation","Hover: both channel values"].forEach((text,i)=>ctx.fillText(text,left,82+i*17));
    for(const [i,kind] of areaKinds.entries()){
      const gradient=ctx.createLinearGradient(left,0,left+140,0);
      gradient.addColorStop(0,"#000");gradient.addColorStop(1,areaColors[kind]);
      ctx.fillStyle=gradient;ctx.fillRect(left,190+i*14,140,9);
    }
  }else if(state.contourDisplay==="compare"){
    const left=width-right+16,kx=left,ky=48,kw=140,kh=56,parts=24;
    ctx.textAlign="left";ctx.fillStyle="#aec1d9";ctx.fillText("Hue = dataset balance",left,30);
    for(let row=0;row<parts;row++)for(let col=0;col<parts;col++){
      const ratio=col/(parts-1),bright=1-row/(parts-1),stops=ratio<.5
        ?[[85,200,255],[200,200,210]]
        :[[200,200,210],[255,103,103]];
      const color=palette(stops,ratio<.5?ratio*2:(ratio-.5)*2).map(channel=>channel*bright);
      ctx.fillStyle=`rgb(${color.map(Math.round).join(",")})`;
      ctx.fillRect(kx+col*kw/parts,ky+row*kh/parts,kw/parts+.5,kh/parts+.5);
    }
    ctx.strokeStyle="#8fa6c2";ctx.strokeRect(kx,ky,kw,kh);
    ctx.fillStyle=colors.comparison;ctx.fillText("Comp",left,121);
    ctx.textAlign="center";ctx.fillStyle="#c8c8d2";ctx.fillText("Balanced",left+70,121);
    ctx.textAlign="right";ctx.fillStyle=colors.base;ctx.fillText("Base",left+140,121);
    ctx.textAlign="left";ctx.fillStyle="#aec1d9";
    ["Mean total [nm / cell]",single()?"per image":"per image pair",
      `0 to ${data.max_total.toPrecision(4)}+`,"Brightness: sqrt(total / cap)"].forEach((text,i)=>ctx.fillText(text,left,143+i*17));
    for(let i=0;i<140;i++){
      const shade=[200,200,210].map(value=>Math.round(value*Math.sqrt(i/139)));
      ctx.fillStyle=`rgb(${shade.join(",")})`;ctx.fillRect(left+i,212,1,9);
    }
    ctx.fillStyle="#aec1d9";ctx.fillText("P99.5 cap; higher saturates",left,238);
  }else{
    const left=width-right+16;
    ctx.textAlign="left";ctx.fillStyle=state.contourFocus==="base"?"#ff9c9c":"#7fd6ff";
    ctx.fillText(single()?"Base":`${roleName(state.contourFocus)} (focused)`,left,34);
    if(!single()){
      ctx.fillStyle=state.contourFocus==="base"?"#7fd6ff":"#ff9c9c";
      ctx.fillText(`${state.contourFocus==="base"?"Comparison":"Base"} (faint)`,left,52);
    }
    ctx.fillStyle="#aec1d9";
    ["Mean length [nm / cell]",single()?"per image":"per image pair",
      `0 to ${data.max_density.toPrecision(4)}+`,single()?"Scale: P99.5 cap":"Shared scale: P99.5 cap"].forEach((text,i)=>ctx.fillText(text,left,78+i*17));
    for(const [i,role] of shownRoles().entries()){
      const gradient=ctx.createLinearGradient(left,0,left+140,0),stops=densityPalettes[role];
      stops.forEach((color,index)=>gradient.addColorStop(index/(stops.length-1),`rgb(${color.join(",")})`));
      ctx.fillStyle=colors[role];ctx.fillText(roleName(role),left,153+i*34);
      ctx.fillStyle=gradient;ctx.fillRect(left,160+i*34,140,9);
    }
    if(!single()){
      ctx.fillStyle="#aec1d9";ctx.fillText("Faint overlay: visual guide",left,222);
    }
  }
}
async function decodeContourBuffer(encoded){
  const text=atob(encoded),bytes=new Uint8Array(text.length);
  for(let i=0;i<text.length;i++)bytes[i]=text.charCodeAt(i);
  return new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"))).arrayBuffer();
}
async function prepareContourData(data){
  if(data.measure==="area"&&data.scope==="image"){
    for(const pattern of data.patterns)for(const kind of areaKinds){
      const path=new Path2D();
      for(const ring of pattern[kind].rings){
        ring.forEach(([x,y],i)=>{if(i)path.lineTo(x,y);else path.moveTo(x,y);});path.closePath();
      }
      pattern[kind].path=path;delete pattern[kind].rings;
    }
    return data;
  }
  if(contourGrid(data)){
    if(data.packed){
      for(const role of data.measure==="area"?areaKinds:roles){
        const buffer=await decodeContourBuffer(data.packed[role]);
        if(buffer.byteLength!==data.bins*data.bins*8)throw Error("Invalid density response size");
        const values=new DataView(buffer),grid=new Float64Array(data.bins*data.bins);
        for(let i=0;i<grid.length;i++)grid[i]=values.getFloat64(i*8,true);
        data[role]=grid;
      }
      delete data.packed;
    }
    return data;
  }
  data.paths={};
  data.counts={};
  for(const role of roles){
    const path=new Path2D();let count=0;
    if(data.scope==="pid"){
      const packed=data.packed[role],buffer=await decodeContourBuffer(packed.points);
      const values=new DataView(buffer);let offset=0;
      for(const length of packed.lengths){
        if(!Number.isInteger(length)||length<0||offset+length*16>values.byteLength)throw Error("Invalid compact contour response");
        for(let i=0;i<length;i++){
          const x=values.getFloat64(offset,true),y=values.getFloat64(offset+8,true);offset+=16;
          if(!Number.isFinite(x)||!Number.isFinite(y))throw Error("Invalid contour coordinates");
          if(i)path.lineTo(x,y);else path.moveTo(x,y);
        }
        if(length){path.closePath();count++;}
      }
      if(offset!==values.byteLength)throw Error("Invalid compact contour response size");
    }else{
      for(const item of data.contours[role]){
        item.points.forEach(([x,y],i)=>{if(i)path.lineTo(x,y);else path.moveTo(x,y);});
        if(item.points.length){path.closePath();count++;}
      }
    }
    data.paths[role]=path;data.counts[role]=count;
  }
  // Keep the compiled paths, not a second copy of every coordinate in JavaScript.
  delete data.packed;delete data.contours;
  return data;
}
function drawContourLines(view,data){
  const canvas=view.canvas,ctx=canvas.getContext("2d"),{width,height,ratio:pixelRatio}=contourSize(canvas);
  const {area,scale,x,y}=contourProjection(view,data.bounds);
  ctx.save();ctx.beginPath();ctx.rect(area.x,area.y,area.width,area.height);ctx.clip();
  const shown=roles.filter(role=>data.scope!=="pid"||state.contourRole==="both"||state.contourRole===role);
  const key=[canvas.width,canvas.height,scale,x,y,shown.join(",")].join(":");
  if(!data[view.slot]||!state.contourInteracting&&data[view.slot].key!==key){
    const image=document.createElement("canvas");image.width=canvas.width;image.height=canvas.height;
    const paint=image.getContext("2d"),layers=[];
    const highlightOverlap=data.scope==="pid"&&shown.length===2;
    for(const role of shown){
      const target=highlightOverlap?document.createElement("canvas"):image;
      if(highlightOverlap){target.width=image.width;target.height=image.height;layers.push(target);}
      const targetPaint=target.getContext("2d");
      targetPaint.setTransform(scale*pixelRatio,0,0,-scale*pixelRatio,x*pixelRatio,y*pixelRatio);
      targetPaint.strokeStyle=colors[role];
      targetPaint.globalAlpha=highlightOverlap?1:data.scope==="pid"?.85:.82;
      targetPaint.lineWidth=1.5/scale;targetPaint.stroke(data.paths[role]);
    }
    if(highlightOverlap){
      paint.globalAlpha=.85;
      for(const layer of layers)paint.drawImage(layer,0,0);
      const overlap=layers[0].getContext("2d");overlap.resetTransform();
      overlap.globalCompositeOperation="destination-in";overlap.drawImage(layers[1],0,0);
      overlap.globalCompositeOperation="source-in";overlap.fillStyle=pidOverlapColor;
      overlap.fillRect(0,0,image.width,image.height);
      paint.globalAlpha=1;paint.drawImage(layers[0],0,0);
    }
    data[view.slot]={image,key,scale,x,y,pixelRatio};
  }
  const raster=data[view.slot],ratio=scale/raster.scale;
  ctx.drawImage(raster.image,x-raster.x*ratio,y-raster.y*ratio,
    raster.image.width/raster.pixelRatio*ratio,raster.image.height/raster.pixelRatio*ratio);
  ctx.restore();
  ctx.strokeStyle="rgba(255,255,255,.5)";ctx.beginPath();ctx.moveTo(x-7,y);ctx.lineTo(x+7,y);
  ctx.moveTo(x,y-7);ctx.lineTo(x,y+7);ctx.stroke();
  ctx.fillStyle="#e7edf7";ctx.font="bold 11px sans-serif";ctx.textAlign="left";
  ctx.fillText(data.scope==="image"?"This image; design centers aligned [nm]":`PID ${data.pid}; image-center coordinates [nm]`,8,15);
  if(data.scope==="pid"){
    const legend=single()?[["Base",colors.base]]:[["Base",colors.base],["Comparison",colors.comparison],["Overlap",pidOverlapColor]];
    let legendX=8;
    for(const [label,color] of legend){
      ctx.fillStyle=color;ctx.fillText(label,legendX,height-10);
      legendX+=ctx.measureText(label).width+18;
    }
  }
}
function drawAreaPatterns(view,data){
  const ctx=view.canvas.getContext("2d"),{width,height}=contourSize(view.canvas);
  const {area,scale,x,y}=contourProjection(view,data.bounds,{right:188});
  ctx.save();ctx.beginPath();ctx.rect(area.x,area.y,area.width,area.height);ctx.clip();
  ctx.transform(scale,0,0,-scale,x,y);ctx.globalAlpha=.7;
  for(const pattern of data.patterns)for(const kind of areaKinds){
    if(state.areaChannel!=="both"&&state.areaChannel!==kind)continue;
    ctx.fillStyle=areaColors[kind];ctx.fill(pattern[kind].path,"evenodd");
  }
  ctx.restore();
  ctx.strokeStyle="#8fa6c2";ctx.beginPath();ctx.moveTo(x-7,y);ctx.lineTo(x+7,y);
  ctx.moveTo(x,y-7);ctx.lineTo(x,y+7);ctx.stroke();
  ctx.font="11px sans-serif";ctx.textAlign="left";
  for(const [i,kind] of areaKinds.entries()){
    ctx.fillStyle=areaColors[kind];ctx.fillText(kind==="inward"?"Inward: Base \\ Comp":"Outward: Comp \\ Base",width-172,34+i*18);
  }
  ctx.fillStyle="#aec1d9";ctx.fillText("Filled regions [nm²]",width-172,84);
  ctx.fillText("Base design centers aligned",width-172,103);
  ctx.fillText("Shared translation only",width-172,122);
  ctx.fillText("Hover: PID areas",width-172,141);
  ctx.fillText("This image; design-centered coordinates [nm]",8,height-10);
}
function drawContourView(){
  if(!state.contourView)return;
  const data=state.contourData;
  const empty=data&&(data.measure==="area"?!data.compared:data.scope==="selection"?!data.pairs||!data.mapped.base&&!data.mapped.comparison:
    data.scope==="pid"&&state.contourRole!=="both"?!data.counts[state.contourRole]:!data.counts.base&&!data.counts.comparison);
  const message=state.contourLoading?
    `Loading ${areaMode()?"Area Diff":state.contourScope==="selection"?"contour density":state.contourScope==="pid"?"PID contours":"image contours"}... ${((performance.now()-state.contourStarted)/1000).toFixed(1)} s`:
    state.contourError||state.contourMessage||(empty?(data.measure==="area"?
      data.scope==="image"?`No usable pattern pairs in this image. ${data.issues.map(item=>`${item.pid??"(missing PID)"}: ${item.reason}`).join("; ")}`:
      `No usable pattern pairs in ${data.selected_pairs} selected image pairs. Excluded Base patterns: ${Object.entries(data.excluded).map(([reason,n])=>`${reason}: ${n}`).join("; ")||"none"}.`:
      data.pairs?"No mapped contours in this selection.":
      single()?"No images in this selection.":"No paired images in this selection."):"");
  $("#contourOverlay").setAttribute("aria-busy",String(state.contourLoading));
  $("#contourMessage").hidden=!message;$("#contourMessageText").textContent=message;
  $("#contourRetry").hidden=!state.contourError;
  if(message)$("#contourStatus").textContent=message;
  if(message||!data){for(const view of contourViews())paintContourView(view,null,message);return;}
  const reference=data.scope==="selection"&&(state.contourSem||state.contourDesign)?contourReference(data.measure==="area"?"base":backgroundRole()):null;
  const referenceText=data.scope==="selection"&&(state.contourSem||state.contourDesign)?
    reference?` Reference: Base / ${reference.name} (current image only; not an average).`:" Reference: loading current Base image...":"";
  $("#contourStatus").textContent=data.measure==="area"&&data.scope==="image"?
    `This image / ${state.contourTarget==="pids"?"clicked PIDs":"current ROI"}: ${data.compared} compared Base patterns / ${data.issues.length} excluded. `+
    areaKinds.map(kind=>`${kind}: ${fmt(data.patterns.reduce((sum,p)=>sum+p[kind].area_nm2,0))} nm²`).join(" / ")+
    ". Both regions use each Base design center; measured contours are not fitted.":
    data.measure==="area"?
    `APPLIED selection${data.scope==="pid"?` / PID ${data.pid}`:""}: ${data.pairs}/${data.selected_pairs} usable image pairs / ${data.compared} compared Base patterns. `+
    `Mean area: inward ${fmt(data.mean_area_nm2.inward)} / outward ${fmt(data.mean_area_nm2.outward)} nm² per usable image pair. `+
    `Excluded Base patterns: ${Object.entries(data.excluded).map(([reason,n])=>`${reason}: ${n}`).join("; ")||"none"}. `+
    `ROI selects Base pattern positions; pairs with no usable patterns are excluded.${hasPending()?" Pending changes are not included.":""}${referenceText}`:
    data.scope==="selection"?
    `APPLIED selection: ${single()?`${data.pairs} Base images / Base ${data.mapped.base.toLocaleString()} mapped contours. Density = contour length [nm] per cell, averaged per image`:
      `${data.pairs} matched image pairs / Base ${data.mapped.base.toLocaleString()} mapped contours / Comparison ${data.mapped.comparison.toLocaleString()} mapped contours. Density = contour length [nm] per cell, averaged per image pair`}${hasPending()?"; pending changes are not included":""}.${state.contourSem||state.contourDesign?
      reference?` Reference: ${reference.role==="base"?"Base":"Comparison"} / ${reference.name} (current image only; not an average).`:" Reference: loading current image...":""}`:
    `${data.scope==="pid"?"APPLIED selection / PID "+data.pid:"This image / "+(state.contourTarget==="pids"?"clicked PIDs":"current ROI")} / Base ${data.counts.base} contours${single()?"":` / Comparison ${data.counts.comparison} contours`}. ${data.scope==="image"?"Each design center is aligned at the origin":`Showing ${state.contourRole==="both"?"Both":state.contourRole==="base"?"Base":"Comparison"}; positions are preserved`}${data.scope==="pid"&&hasPending()?"; pending changes are not included":""}.`;
  for(const view of contourViews())paintContourView(view,data,"");
}
function paintContourView(view,data,message){
  const canvas=view.canvas,ctx=canvas.getContext("2d"),{width,height,ratio}=contourSize(canvas);
  ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);
  if(!data){
    // The main panel shows messages in its DOM overlay; the Expand window draws them on the canvas.
    if(message&&view!==mainContour){
      ctx.fillStyle="#aec1d9";ctx.font="13px sans-serif";ctx.textAlign="center";
      ctx.fillText(message,width/2,height/2);
    }
    return;
  }
  // A hidden canvas (for example, statistics maximized) has no size; observeSize redraws when it returns.
  if(!width||!height||!canvas.clientWidth||!canvas.clientHeight)return;
  // Scope controls can change the panel height. Do not rasterize at the old canvas size first.
  if(canvas.width!==Math.max(1,Math.floor(width*ratio))||
      canvas.height!==Math.max(1,Math.floor(height*ratio))){queueContourDraw();return;}
  if(contourGrid(data))drawDensityCanvas(view,data);
  else if(data.measure==="area")drawAreaPatterns(view,data);
  else drawContourLines(view,data);
}
function fitContour(view){
  clearTimeout(state.contourSettleTimer);state.contourInteracting=false;
  for(const target of view?[view]:contourViews())target.transform={zoom:1,x:0,y:0};
  drawContourView();
}
function zoomContour(view,factor,cx,cy){
  if(!state.contourData||state.contourLoading||!view.projection)return;
  const t=view.transform,center=view.projection.center;
  cx??=center[0];cy??=center[1];
  const zoom=Math.max(.2,Math.min(100,t.zoom*factor)),ratio=zoom/t.zoom;
  view.transform={zoom,x:(cx-center[0])*(1-ratio)+t.x*ratio,y:(cy-center[1])*(1-ratio)+t.y*ratio};
  settleContourInteraction();
  queueContourDraw();
}
function settleContourInteraction(){
  state.contourInteracting=true;clearTimeout(state.contourSettleTimer);
  state.contourSettleTimer=setTimeout(()=>{
    state.contourInteracting=false;queueContourDraw();
  },150);
}
function queueContourDraw(){
  if(state.contourFrame!==null)return;
  state.contourFrame=requestAnimationFrame(()=>{state.contourFrame=null;drawContourView();});
}
function bindContourCanvas(view){
  const canvas=view.canvas;let drag=null;
  canvas.addEventListener("wheel",e=>{
    e.preventDefault();const box=canvas.getBoundingClientRect();
    zoomContour(view,Math.exp(-e.deltaY*.0015),e.clientX-box.left,e.clientY-box.top);
  },{passive:false});
  canvas.onpointerdown=e=>{
    if(e.button!==0||!state.contourData||state.contourLoading)return;
    drag={id:e.pointerId,x:e.clientX,y:e.clientY,transform:{...view.transform}};
    canvas.setPointerCapture(e.pointerId);
  };
  canvas.onpointermove=e=>{
    if(!drag||drag.id!==e.pointerId){
      const data=state.contourData,p=view.projection;
      canvas.title="";
      if(data?.measure==="area"&&!state.contourLoading&&p){
        const box=canvas.getBoundingClientRect(),x=(e.clientX-box.left-p.x)/p.scale,y=(p.y-e.clientY+box.top)/p.scale;
        if(data.scope==="image"){
          const ctx=canvas.getContext("2d");
          ctx.save();ctx.resetTransform();
          const hit=data.patterns.find(item=>areaKinds.some(kind=>ctx.isPointInPath(item[kind].path,x,y,"evenodd")));
          ctx.restore();
          canvas.title=hit?`PID ${hit.pid}\nInward: ${fmt(hit.inward.area_nm2)} nm²\nOutward: ${fmt(hit.outward.area_nm2)} nm²`:"";
          return;
        }
        const col=Math.floor((x+data.extent_nm)/(2*data.extent_nm)*data.bins);
        const row=Math.floor((data.extent_nm-y)/(2*data.extent_nm)*data.bins);
        if(col>=0&&col<data.bins&&row>=0&&row<data.bins){
          const i=row*data.bins+col;
          canvas.title=`Mean area [nm²/cell/image pair]\nInward: ${data.inward[i].toPrecision(6)}\nOutward: ${data.outward[i].toPrecision(6)}`;
        }
      }
      return;
    }
    view.transform={...drag.transform,x:drag.transform.x+e.clientX-drag.x,y:drag.transform.y+e.clientY-drag.y};
    settleContourInteraction();
    queueContourDraw();
  };
  canvas.onpointerup=canvas.onpointercancel=canvas.onlostpointercapture=()=>{drag=null;};
  canvas.ondblclick=()=>fitContour(view);
}
function contourKey(){
  const filters=state.contourScope==="image"?state.filters:(state.result?.filters||state.filters);
  const args={scope:state.contourScope,filters};
  if(areaMode())args.measure="area";
  if(state.contourScope==="image"){
    args.id=state.selected?.id;
    if(state.contourTarget==="pids")args.pids=[...state.contourPids].sort();
  }
  if(state.contourScope==="pid")args.pid=state.contourPid;
  if(state.contourScope==="selection")args.bins=1024;
  return JSON.stringify(args);
}
function syncContourControls(){
  $("#detailPane").classList.toggle("contour-active",state.contourView);
  $("#contourViewButton").setAttribute("aria-pressed",String(state.contourView));
  $("#contourViewButton").textContent=state.contourView?"Hide contours":"Show contours";
  $("#contourPane").hidden=$("#contourSplitter").hidden=!state.contourView;
  $("#contourControls").hidden=!state.contourView;
  $("#contourOverlay").hidden=!state.contourView;
  $("#contourStatus").hidden=!state.contourView;
  if(imageViews().includes(state.expand))state.expand.contour.canvas.hidden=!state.contourView;
  $("#contourPidControl").hidden=state.contourScope!=="pid";
  $("#contourRoleControl").hidden=state.contourScope!=="pid"||areaMode();
  $("#contourReferenceControls").hidden=state.contourScope!=="selection";
  $("#areaChannels").hidden=!areaMode();
  document.querySelectorAll("[data-contour-measure]").forEach(button=>{
    button.setAttribute("aria-pressed",String(button.dataset.contourMeasure===state.contourMeasure));
    button.disabled=single()&&button.dataset.contourMeasure==="area";button.title=button.disabled?"Area Diff requires both Base and Comp":"";
  });
  document.querySelectorAll("[data-area-channel]").forEach(button=>
    button.setAttribute("aria-pressed",String(button.dataset.areaChannel===state.areaChannel)));
  $("#contourHint").hidden=state.contourScope==="selection";
  $("#contourSemOpacity").disabled=!state.contourSem;
  $("#contourTargetControl").hidden=state.contourScope!=="image";
  $("#contourTarget").value=state.contourTarget;
  $("#contourClearPids").hidden=state.contourScope!=="image"||state.contourTarget!=="pids";
  $("#contourSelection").textContent=state.contourScope==="image"?
    (state.contourTarget==="pids"?`Selected PIDs: ${state.contourPids.join(", ")||"none"}`:
      `${state.filters.roi?"ROI":"Whole image"}: ${selectedContourPatterns().length} Base patterns`) :
    state.contourScope==="pid"?`Click a pattern in the source image, or choose PID ${state.contourPid||"—"}.`:
      areaMode()?"1024 × 1024 mean area per cell / Base design layout centers aligned":
        "1024 × 1024 contour length per cell / design layout centers aligned";
  document.querySelectorAll("[data-contour-role]").forEach(button=>{
    button.setAttribute("aria-pressed",String(button.dataset.contourRole===state.contourRole));
    button.disabled=single()&&button.dataset.contourRole!=="base";button.title=button.disabled?NO_COMP:"";
  });
  document.querySelectorAll('[data-contour-display="compare"],[data-contour-focus="comparison"]').forEach(button=>{
    button.disabled=single();button.title=single()?NO_COMP:"";
  });
  $("#contourDisplayLabel").hidden=$("#contourDisplay").hidden=state.contourScope!=="selection"||areaMode();
  $("#contourFocus").hidden=state.contourScope!=="selection"||areaMode()||state.contourDisplay!=="focus";
  document.querySelectorAll("[data-contour-scope]").forEach(button=>
    button.setAttribute("aria-pressed",String(button.dataset.contourScope===state.contourScope)));
  document.querySelectorAll("[data-contour-display]").forEach(button=>
    button.setAttribute("aria-pressed",String(button.dataset.contourDisplay===state.contourDisplay)));
  document.querySelectorAll("[data-contour-focus]").forEach(button=>
    button.setAttribute("aria-pressed",String(button.dataset.contourFocus===state.contourFocus)));
}
function syncContourPids(){
  const pids=(single()?state.result?.offset_by_pid.pids:state.result?.ppe.pids)||[];
  const previous=state.contourPid;
  if(!pids.includes(state.contourPid))state.contourPid=pids[0]||null;
  const select=$("#contourPid");select.replaceChildren(...pids.map(pid=>new Option(pid,pid)));
  select.value=state.contourPid||"";
  if(state.contourView&&state.contourScope==="pid"&&state.contourPid!==previous)drawImage();
}
async function refreshContourView(){
  if(!state.contourView)return;
  syncContourControls();
  let message="";
  if(state.contourScope==="image"&&!state.selected)message=single()?"No images in the current selection.":"No paired images in the current selection.";
  else if(state.contourScope==="image"&&state.contourTarget==="pids"&&!state.contourPids.length)
    message="Click a source pattern to select a PID. Ctrl+click adds or removes PIDs.";
  else if(state.contourScope!=="image"&&!state.result)message="Waiting for the applied selection...";
  else if(state.contourScope==="pid"&&!state.contourPid)message=single()?"No PID in the applied selection.":"No common PID in the applied selection.";
  if(message){
    state.contourKey=null;state.contourLoading=false;state.contourError=null;state.contourMessage=message;
    state.contourData=null;clearInterval(state.contourTimer);state.contourTimer=null;
    drawContourView();return;
  }
  const key=contourKey();
  if(state.contourKey===key&&(state.contourData||state.contourLoading)){drawContourView();return;}
  if(state.contourError&&$("#error").textContent===state.contourError)$("#error").textContent="";
  state.contourKey=key;state.contourMessage="";state.contourError=null;state.contourData=null;
  if(state.contourCache.has(key)){
    const data=state.contourCache.get(key);
    state.contourCache.delete(key);state.contourCache.set(key,data);
    state.contourData=data;state.contourLoading=false;
    clearInterval(state.contourTimer);state.contourTimer=null;fitContour();return;
  }
  state.contourLoading=true;state.contourStarted=performance.now();
  if(!state.contourTimer)state.contourTimer=setInterval(()=>{if(state.contourLoading)drawContourView();},250);
  drawContourView();loadContourRequest();
}
function cacheContourData(key,data){
  if(contourGrid(data)){
    const category=`${data.scope}:${data.measure||"length"}`;
    for(const [cachedKey,cached] of state.contourCache){
      if(contourGrid(cached)&&`${cached.scope}:${cached.measure||"length"}`===category)state.contourCache.delete(cachedKey);
    }
    const grids=[...state.contourCache].filter(([,cached])=>contourGrid(cached));
    while(grids.length>=2)state.contourCache.delete(grids.shift()[0]);
  }
  state.contourCache.set(key,data);
  while(state.contourCache.size>4){
    const oldestLines=[...state.contourCache].find(([,cached])=>!contourGrid(cached));
    state.contourCache.delete(oldestLines?oldestLines[0]:state.contourCache.keys().next().value);
  }
}
async function loadContourRequest(){
  if(state.contourFlight||!state.contourView||!state.contourLoading||!state.contourKey)return;
  const key=state.contourKey;state.contourFlight=key;
  try{
    const args=JSON.parse(key);
    const data=await prepareContourData(await request(args.measure==="area"?"area_difference":"contour_overlay",args));
    cacheContourData(key,data);
    if(state.contourView&&state.contourKey===key){
      state.contourData=data;state.contourLoading=false;state.contourError=null;fitContour();
    }
  }catch(error){
    if(state.contourView&&state.contourKey===key){
      state.contourLoading=false;state.contourError=error.message;drawContourView();fail(error);
    }else console.error(error);
  }finally{
    state.contourFlight=null;
    if(state.contourView&&state.contourLoading)refreshQueuedContour();
    else {clearInterval(state.contourTimer);state.contourTimer=null;}
  }
}
function refreshQueuedContour(){
  // Only the latest requested view follows the one already running; intermediate clicks are discarded.
  if(state.contourCache.has(state.contourKey)){
    state.contourData=state.contourCache.get(state.contourKey);state.contourLoading=false;
    clearInterval(state.contourTimer);state.contourTimer=null;fitContour();
  }else loadContourRequest();
}
function setContourView(enabled){
  state.contourView=enabled;
  if(!enabled){
    state.contourKey=null;state.contourLoading=false;state.contourData=null;
    clearInterval(state.contourTimer);state.contourTimer=null;
  }
  syncContourControls();drawImage();refreshContourView();
}
const EXPAND_STYLE=`*{box-sizing:border-box}body{margin:0;height:100vh;display:flex;flex-direction:column;background:#111923;color:#e7edf7;font:13px "Segoe UI",sans-serif}
.bar{display:flex;align-items:center;gap:10px;padding:6px 10px;background:#24354b}.name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.background{color:#ffd27a;white-space:nowrap}
.warn{color:#ffd166;font-weight:bold}.hint{font-size:11px;color:#aec1d9}.status{font-size:12px;color:#ffd166;padding:4px 10px}
button{font:inherit;color:#edf3ff;background:#263b52;border:1px solid #536a84;border-radius:4px;padding:4px 8px;cursor:pointer}
.views{flex:1;display:flex;gap:4px;min-height:0}
canvas{display:block;flex:1;width:0;height:100%;min-width:0;background:#222;touch-action:none}canvas.contour{background:#111923}canvas[hidden]{display:none}`;
function openExpand(){
  if(imageViews().includes(state.expand)){state.expand.win.focus();return;}
  const win=window.open("","comparisonViewerImage","popup,width=1600,height=1000");
  if(!win){fail(Error("Cannot open the expanded image window"));return;}
  const doc=win.document,style=doc.createElement("style"),bar=doc.createElement("div");
  style.textContent=EXPAND_STYLE;doc.head.replaceChildren(style);doc.title=`${document.title} - Image`;
  const name=doc.createElement("span"),background=doc.createElement("span"),fitButton=doc.createElement("button"),hint=doc.createElement("span");
  const canvas=doc.createElement("canvas"),contourCanvas=doc.createElement("canvas"),views=doc.createElement("div"),status=doc.createElement("div");
  canvas.className="image";contourCanvas.className="contour";contourCanvas.id="contourCanvas";views.className="views";
  contourCanvas.hidden=!state.contourView;contourCanvas.title="Contour overlay. Drag: pan / Wheel: zoom / Double-click: fit";
  bar.className="bar";name.className="name";background.className="background";hint.className="hint";status.className="status";
  fitButton.textContent="Fit";fitButton.title="Fit the image to the window. ROI and analysis results are unchanged.";
  hint.textContent="Follows the main window, including the contour overlay. Drag: pan / Ctrl+drag: shared ROI / Wheel: zoom / Up/Down: previous/next image / Esc: close";
  bar.append(name,background,hint,fitButton);
  if(state.index.metadata.test_chip_override){
    const warn=doc.createElement("span");warn.className="warn";
    warn.textContent=single()?"Synthetic chip coordinates; not suitable for process analysis":
      "Synthetic chip coordinates; same-site comparison not validated";bar.prepend(warn);
  }
  views.append(canvas,contourCanvas);doc.body.replaceChildren(bar,views,status);
  const contour={canvas:contourCanvas,slot:"expandRaster",transform:{zoom:1,x:0,y:0},projection:null};
  const view={win,canvas,name,background,status,contour,transform:{scale:1,x:0,y:0}};
  state.expand=view;
  fitButton.onclick=()=>{fit(view);fitContour(contour);};
  bindImageCanvas(view);bindContourCanvas(contour);
  observeSize(canvas,win,()=>fit(view));
  observeSize(contourCanvas,win,drawContourView);
  win.addEventListener("keydown",e=>{
    if(e.key==="Escape")win.close();
    if(["ArrowDown","ArrowUp"].includes(e.key)){e.preventDefault();stepImage(e.key==="ArrowDown"?1:-1);}
  });
  win.addEventListener("pagehide",()=>{if(state.expand===view){state.expand=null;}});
  setImageStatus($("#imageStatus").textContent);
}
function renderRaw(){
  document.querySelectorAll("[data-raw-role]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.rawRole===state.rawRole)));
  $("#rawCsv").textContent=`Raw measurement data - ${roleLabel(state.rawRole)} (CSV)`;
  $("#rawCsv").disabled=!state.image;
  if($("#rawPane").hidden)return;
  const rows=(state.image?.rows[state.rawRole]||[]).map((r,i)=>({...r,"#":i+1}));
  $("#rawStatus").textContent=rows.length?`${rows.length} source rows for selected image (not aggregated)`:
    state.image?"No same-chip corresponding image. Other chips are not substituted.":"Waiting for image";
  const head=$("#raw thead"),body=$("#raw tbody");head.replaceChildren();body.replaceChildren();
  const keys=[...new Set(["#","PID","IMAGENAME","PNO","SEQUENCENO","XINDEX","YINDEX","PixelX","PixelY","dx","dy","magnitude","vector_valid",...rows.flatMap(r=>Object.keys(r))])].filter(key=>key!=="direction");
  const columns=$("#columns");columns.replaceChildren();
  for(const key of keys){
    const label=node("label"),check=node("input");check.type="checkbox";check.checked=state.columns.includes(key);
    check.onchange=()=>{state.columns=check.checked?[...state.columns,key]:state.columns.filter(k=>k!==key);renderRaw();};
    label.append(check,document.createTextNode(rawLabel(key)));columns.append(label);
  }
  const shown=keys.filter(key=>state.columns.includes(key));
  const title=node("tr");
  for(const key of shown){
    const th=node("th",rawLabel(key));th.tabIndex=0;
    th.onclick=()=>{state.rawSort={key,descending:state.rawSort?.key===key?!state.rawSort.descending:false};renderRaw();};
    th.onkeydown=e=>{if(e.key==="Enter")th.click();};title.append(th);
  }head.append(title);
  const sorted=[...rows];
  if(state.rawSort){
    const {key,descending}=state.rawSort;
    sorted.sort((a,b)=>{
      const aa=a[key],bb=b[key];
      if(aa===null||aa===undefined||aa==="")return bb===null||bb===undefined||bb===""?0:1;
      if(bb===null||bb===undefined||bb==="")return -1;
      const diff=Number.isFinite(Number(aa))&&Number.isFinite(Number(bb))?Number(aa)-Number(bb):String(aa).localeCompare(String(bb));
      return diff*(descending?-1:1);
    });
  }
  sorted.forEach(r=>{
    const tr=node("tr");
    for(const key of shown)tr.append(node("td",r[key]===""||r[key]===undefined?"—":fmt(r[key])));
    body.append(tr);
  });
}
async function updateStatistics(){
  if(state.applying)return;
  const revision=state.revision,filters=clone(state.filters),token=++state.statsRequest;
  state.applying=true;$("#update").disabled=true;dirtyStatus();
  $("#error").textContent="";
  try{
    if(!state.baselines.has(filters.pno)){
      const full=await request("statistics",{pno:filters.pno,ms:"",chips:[],chipRect:null,roi:null});
      state.baselines.set(filters.pno,full);
    }
    const fullSelection=!filters.ms&&!filters.chips.length&&!filters.chipRect&&!filters.roi;
    const result=fullSelection?state.baselines.get(filters.pno):await request("statistics",filters);
    if(token!==state.statsRequest)return;
    state.baseline=state.baselines.get(filters.pno);
    state.axes=graphAxes(state.baseline);
    state.mapColorMax=Math.max(.001,...state.baseline.maps.map(e=>e.magnitude||0),
      ...state.baseline.image_maps.map(e=>e.magnitude||0));
    state.ppeAxes=ppeAxes(state.baseline);
    state.result=result;state.resultRevision=revision;state.resultTime=new Date().toLocaleString();
    if(state.ppeSelected&&!result.ppe.pids.includes(state.ppeSelected))state.ppeSelected=null;
    renderStats();renderPpe();syncContourPids();dirtyStatus();drawMaps();drawGraph();drawPpeGraph();refreshContourView();
  }catch(error){fail(error);}
  finally{
    if(token===state.statsRequest){
      state.applying=false;$("#update").disabled=false;dirtyStatus();
      if($("#error").textContent)$("#applyStatus").textContent="Apply failed / retry";
    }
  }
}
const OFFSET_PID_NOTE="By PID: patterns whose PID is unique in their image; each dataset uses its own pattern positions for the ROI. "+
  "N: valid patterns; Excluded: missing or invalid measurements. SD: population SD.";
function renderStats(){
  const body=$("#stats tbody");body.replaceChildren();
  const shown=shownRoles();
  for(const row of state.result.table.filter(row=>shown.includes(row.role))){
    const tr=node("tr");
    for(const key of ["role","metric","n","missing","excluded","mean","median","sigma","minimum","maximum"]){
      const td=node("td",key==="metric"?`${labels[row.metric]} [nm]`:key==="role"?roleLabel(row.role):fmt(row[key]));
      if(key==="role")td.dataset.datasetRole=row.role;
      tr.append(td);
    }body.append(tr);
  }
  const pidBody=$("#offsetPidTable tbody");pidBody.replaceChildren();
  for(const row of state.result.offset_by_pid.table.filter(row=>shown.includes(row.role))){
    const tr=node("tr");if(!row.n)tr.classList.add("empty");
    for(const key of ["role","pid","n","excluded",...["x","y","magnitude"].flatMap(c=>[`${c}_mean`,`${c}_sd`])]){
      const td=node("td",key==="role"?roleLabel(row.role):fmt(row[key]));
      if(key==="role")td.dataset.datasetRole=row.role;
      tr.append(td);
    }
    pidBody.append(tr);
  }
  $("#statNote").textContent=state.statsView==="pid"?OFFSET_PID_NOTE:state.result.note;
}
function showStatsView(view){
  state.statsView=view;
  document.querySelectorAll("[data-stats-view]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.statsView===view)));
  $("#statsScroll").hidden=view!=="summary";$("#offsetPidScroll").hidden=view!=="pid";
  if(state.result)renderStats();
}
function quantile(values,p){
  const index=(values.length-1)*p,a=Math.floor(index),b=Math.ceil(index);
  return values[a]+(values[b]-values[a])*(index-a);
}
function histogram(values,min,max){
  const counts=Array(30).fill(0);
  for(const value of values)counts[Math.min(29,Math.max(0,Math.floor((value-min)/(max-min)*30)))]++;
  return counts;
}
function graphAxes(baseline){
  const axes={};
  for(const m of Object.keys(labels)){
    const values=shownRoles().flatMap(role=>baseline.graph[role][m]);
    let [min,max]=range(values);min=Math.min(0,min);max=Math.max(0,max);
    if(min===max){min-=.5;max+=.5;}
    const pad=(max-min)*.05;min-=m==="magnitude"?0:pad;max+=pad;
    axes[m]={min,max};
  }
  return axes;
}
function drawGraph(){
  const c=$("#graph"),ctx=c.getContext("2d");ctx.clearRect(0,0,c.width,c.height);
  $("#metric").disabled=state.graph==="scatter";
  $("#metric").title=state.graph==="scatter"?"Scatter always displays Offset X versus Offset Y":"Value displayed in histogram or box plot";
  if(!state.result||!state.axes){$("#graphInfo").textContent="Computing initial statistics...";return;}
  const m=metric(),data=state.result.graph;
  const margin={left:48,right:22,top:18,bottom:32},w=Math.max(1,c.width-margin.left-margin.right),h=Math.max(1,c.height-margin.top-margin.bottom);
  const {min,max}=state.axes[m];
  // X axis and bins stay fixed to the full data; the count axis follows the applied selection
  // so that a small ROI selection is still readable.
  const shown=shownRoles();
  const series=state.graph==="hist"?shown.map(role=>histogram(data[role][m],min,max)):null;
  const peak=series?niceCount(Math.max(1,...series.flat())*1.1):1;
  const xr=state.graph==="scatter"?state.axes.dx:{min,max};
  const yr=state.graph==="scatter"?state.axes.dy:{min:0,max:state.graph==="hist"?peak:2};
  const xp=x=>margin.left+(x-xr.min)/(xr.max-xr.min)*w;
  const yp=y=>margin.top+h-(y-yr.min)/(yr.max-yr.min)*h;
  ctx.font="10px sans-serif";ctx.lineWidth=1;
  const grid=(value,vertical,zero=false)=>{
    ctx.strokeStyle=zero?"#e7edf7":"#40536a";ctx.lineWidth=zero?1.8:1;
    ctx.beginPath();
    if(vertical){ctx.moveTo(xp(value),margin.top);ctx.lineTo(xp(value),margin.top+h);}
    else{ctx.moveTo(margin.left,yp(value));ctx.lineTo(margin.left+w,yp(value));}
    ctx.stroke();
  };
  for(let i=0;i<=4;i++){
    const x=xr.min+(xr.max-xr.min)*i/4,y=yr.min+(yr.max-yr.min)*i/4;
    grid(x,true);if(state.graph!=="box")grid(y,false);
    ctx.fillStyle="#aec1d9";ctx.textAlign="center";ctx.fillText(x.toFixed(2),xp(x),c.height-16);
    if(state.graph!=="box"){
      ctx.textAlign="right";ctx.fillText(state.graph==="hist"?Math.round(y):y.toFixed(2),margin.left-5,yp(y)+3);
    }
  }
  ctx.textAlign="left";
  ctx.save();ctx.beginPath();ctx.rect(margin.left,margin.top,w,h);ctx.clip();
  if(state.graph==="hist"){
    const bins=30;
    shown.forEach((role,j)=>{
      ctx.fillStyle=colors[role];ctx.globalAlpha=.7;
      series[j].forEach((value,i)=>{
        const bw=w/bins/shown.length;ctx.fillRect(margin.left+i*w/bins+j*bw,yp(value),Math.max(1,bw-1),value/peak*h);
      });ctx.globalAlpha=1;
    });
  }else if(state.graph==="box"){
    shown.forEach((role,j)=>{
      const values=[...data[role][m]].sort((a,b)=>a-b);if(!values.length)return;
      const q1=quantile(values,.25),med=quantile(values,.5),q3=quantile(values,.75),iqr=q3-q1;
      const low=values.find(v=>v>=q1-1.5*iqr),high=[...values].reverse().find(v=>v<=q3+1.5*iqr);
      const y=margin.top+h*(j+.5)/shown.length;
      ctx.strokeStyle=colors[role];ctx.lineWidth=2;
      ctx.beginPath();ctx.moveTo(xp(low),y);ctx.lineTo(xp(high),y);ctx.stroke();
      ctx.strokeRect(xp(q1),y-12,Math.max(1,xp(q3)-xp(q1)),24);
      ctx.beginPath();ctx.moveTo(xp(med),y-12);ctx.lineTo(xp(med),y+12);ctx.stroke();
      ctx.fillStyle=colors[role];
      for(const v of values)if(v<low||v>high){ctx.beginPath();ctx.arc(xp(v),y,2,0,Math.PI*2);ctx.fill();}
    });
  }else{
    shown.forEach(role=>{
      ctx.fillStyle=colors[role];ctx.globalAlpha=.45;
      data[role].dx.forEach((x,i)=>{
        const y=data[role].dy[i];ctx.beginPath();
        ctx.arc(xp(x),yp(y),1.8,0,Math.PI*2);ctx.fill();
      });ctx.globalAlpha=1;
    });
  }
  ctx.restore();
  if(xr.min<=0&&xr.max>=0){
    grid(0,true,true);
    ctx.fillStyle="#e7edf7";ctx.textAlign="center";ctx.fillText("0",xp(0),c.height-16);ctx.textAlign="left";
  }
  if(state.graph!=="box"&&yr.min<=0&&yr.max>=0)grid(0,false,true);
  ctx.fillStyle="#aec1d9";ctx.fillText(state.graph==="scatter"?"Offset X [nm]":`${labels[m]} [nm]`,margin.left,c.height-2);
  const description=state.graph==="hist"?"30 shared bins / Count":state.graph==="box"?"Q1/Median/Q3 / 1.5 IQR whiskers / outliers":"Offset Y [nm] / all valid points";
  $("#graphInfo").textContent=`APPLIED snapshot${hasPending()?" / pending selection differs":""}. ${description}. X axis fixed to full data (all image types, applied PNo)${state.graph==="hist"?"; count axis fits the applied selection":state.graph==="scatter"?"; Y axis fixed to full data":""}. ${single()?"Red: Base (Comp not loaded).":"Red: Base / Blue: Comparison."}`;
}
function pidColor(pid){
  const order=state.baseline?.ppe.pids||[],index=Math.max(0,order.indexOf(pid));
  return `hsl(${Math.round(index*137.508)%360},70%,${index%2?58:68}%)`;
}
function niceStep(raw){
  const power=10**Math.floor(Math.log10(raw)),scaled=raw/power;
  return (scaled<=1?1:scaled<=2?2:scaled<=2.5?2.5:scaled<=5?5:10)*power;
}
function ppeAxes(baseline){
  const axes={};
  for(const c of Object.keys(ppeLabels)){
    const values=baseline.ppe.pids.flatMap(pid=>baseline.ppe.values[pid][c]);
    let [min,max]=range(values);min=Math.min(0,min);max=Math.max(0,max);
    if(min===max){min-=.5;max+=.5;}
    const pad=(max-min)*.05;min-=c==="magnitude"?0:pad;max+=pad;
    axes[c]={min,max,bin:niceStep((max-min)/30)};
  }
  return axes;
}
function ppeBinWidth(){return state.ppeBin||state.ppeAxes[$("#ppeComponent").value].bin;}
function ppeBins(values,start,width,count){
  const counts=Array(count).fill(0);
  for(const value of values)counts[Math.min(count-1,Math.max(0,Math.floor((value-start)/width)))]++;
  return counts;
}
// Round a count axis maximum up to 1, 2 or 5 × 10^n so that the four grid steps stay readable.
function niceCount(value){
  const target=Math.max(4,Math.ceil(value)),step=10**Math.floor(Math.log10(target/4));
  for(const f of [1,2,2.5,5,10])if(f*step*4>=target)return Math.max(4,Math.round(f*step*4));
  return target;
}
function renderPpe(){
  const body=$("#ppeTable tbody");body.replaceChildren();
  if(!state.result)return;
  for(const row of state.result.ppe.table){
    const tr=node("tr"),all=row.pid==="All",swatch=node("td"),mark=node("span");
    tr.tabIndex=0;tr.dataset.pid=row.pid;
    tr.setAttribute("aria-current",String(all?!state.ppeSelected:state.ppeSelected===row.pid));
    if(!row.n)tr.classList.add("empty");
    swatch.className="swatch";if(!all)mark.style.background=pidColor(row.pid);swatch.append(mark);tr.append(swatch);
    for(const key of ["pid","n",...["x","y","magnitude"].flatMap(c=>[`${c}_mean`,`${c}_sd`])])tr.append(node("td",fmt(row[key])));
    tr.onclick=()=>{state.ppeSelected=all?null:row.pid;renderPpe();drawPpeGraph();drawImage();};
    tr.onkeydown=e=>{if(["Enter"," "].includes(e.key)){e.preventDefault();tr.click();}};
    body.append(tr);
  }
  const unmatched=state.result.ppe.unmatched;
  $("#ppeNote").textContent=`${PPE_DEFINITION}. SD: population SD. N: valid pattern pairs; `+
    `rows with N = 0 have no valid pair. ROI membership uses the Base pattern position. `+
    `PIDs without a counterpart in the same image pair (Base/Comparison): ${unmatched.base}/${unmatched.comparison}. Click a row to focus one PID.`;
}
function drawPpeGraph(){
  const c=$("#ppeGraph"),ctx=c.getContext("2d");ctx.clearRect(0,0,c.width,c.height);
  if(state.lower!=="ppe")return;
  if(!state.result||!state.ppeAxes){$("#ppeGraphInfo").textContent="Computing initial statistics...";return;}
  const component=$("#ppeComponent").value,axis=state.ppeAxes[component],ppe=state.result.ppe;
  const width=ppeBinWidth();$("#ppeBin").value=String(+width.toPrecision(6));
  $("#ppeBin").disabled=$("#ppeBinAuto").disabled=state.ppeGraph!=="hist";
  const scatter=state.ppeGraph==="scatter";
  $("#ppeComponentControl").hidden=$("#ppeBinControl").hidden=$("#ppeBinAuto").hidden=scatter;$("#ppeStatControl").hidden=!scatter;
  state.ppePoints=[];
  const pids=state.ppeSelected?[state.ppeSelected]:ppe.pids;
  const margin={left:48,right:16,top:14,bottom:32},w=Math.max(1,c.width-margin.left-margin.right),h=Math.max(1,c.height-margin.top-margin.bottom);
  ctx.font="10px sans-serif";ctx.lineWidth=1;
  const scope=`APPLIED snapshot${hasPending()?" / pending selection differs":""}. ${state.ppeSelected?`PID ${state.ppeSelected}`:"All PIDs"}.`;
  if(state.ppeGraph==="hist"){
    const start=Math.floor(axis.min/width)*width,count=Math.ceil((axis.max-start)/width);
    if(!(count>0&&count<=400)){
      $("#ppeGraphInfo").textContent="Bin width is too small for the data range (more than 400 bins). Increase the bin width.";return;
    }
    // X axis stays fixed to the full data; the count axis fits the bars drawn for the applied selection.
    const totals=Array(count).fill(0);
    for(const pid of pids)ppeBins(ppe.values[pid]?.[component]||[],start,width,count).forEach((v,i)=>totals[i]+=v);
    const peak=niceCount(Math.max(1,...totals)*1.1);
    const xp=x=>margin.left+(x-start)/(count*width)*w,yp=y=>margin.top+h-y/peak*h;
    for(let i=0;i<=4;i++){
      const x=start+count*width*i/4,y=peak*i/4;
      ctx.strokeStyle="#40536a";ctx.beginPath();ctx.moveTo(xp(x),margin.top);ctx.lineTo(xp(x),margin.top+h);
      ctx.moveTo(margin.left,yp(y));ctx.lineTo(margin.left+w,yp(y));ctx.stroke();
      ctx.fillStyle="#aec1d9";ctx.textAlign="center";ctx.fillText(x.toFixed(2),xp(x),c.height-16);
      ctx.textAlign="right";ctx.fillText(Math.round(y),margin.left-5,yp(y)+3);
    }
    const stack=Array(count).fill(0);
    for(const pid of pids){
      const bins=ppeBins(ppe.values[pid]?.[component]||[],start,width,count);
      ctx.fillStyle=pidColor(pid);
      bins.forEach((value,i)=>{
        if(!value)return;
        const x=xp(start+i*width),bw=Math.max(1,w/count-1);
        ctx.fillRect(x,yp(stack[i]+value),bw,value/peak*h);stack[i]+=value;
      });
    }
    if(start<=0&&start+count*width>=0){ctx.strokeStyle="#e7edf7";ctx.lineWidth=1.8;ctx.beginPath();ctx.moveTo(xp(0),margin.top);ctx.lineTo(xp(0),margin.top+h);ctx.stroke();}
    ctx.fillStyle="#aec1d9";ctx.textAlign="left";ctx.fillText(`${ppeLabels[component]} [nm]`,margin.left,c.height-2);
    $("#ppeGraphInfo").textContent=`${scope} Count per ${+width.toPrecision(6)} nm bin; ${state.ppeSelected?"":"bars stacked by PID (colors match the table). "}X axis fixed to full data; count axis fits the applied selection.`;
  }else if(state.ppeGraph==="scatter"){
    drawPpeScatter(c,ctx,margin,w,h,scope);
  }else{
    const yp=y=>margin.top+h-(y-axis.min)/(axis.max-axis.min)*h,slot=w/Math.max(1,ppe.pids.length);
    for(let i=0;i<=4;i++){
      const y=axis.min+(axis.max-axis.min)*i/4;
      ctx.strokeStyle="#40536a";ctx.beginPath();ctx.moveTo(margin.left,yp(y));ctx.lineTo(margin.left+w,yp(y));ctx.stroke();
      ctx.fillStyle="#aec1d9";ctx.textAlign="right";ctx.fillText(y.toFixed(2),margin.left-5,yp(y)+3);
    }
    if(axis.min<=0&&axis.max>=0){ctx.strokeStyle="#e7edf7";ctx.lineWidth=1.8;ctx.beginPath();ctx.moveTo(margin.left,yp(0));ctx.lineTo(margin.left+w,yp(0));ctx.stroke();}
    ppe.pids.forEach((pid,i)=>{
      const x=margin.left+slot*(i+.5),values=[...ppe.values[pid][component]].sort((a,b)=>a-b);
      ctx.globalAlpha=!state.ppeSelected||state.ppeSelected===pid?1:.25;
      ctx.fillStyle="#aec1d9";ctx.textAlign="center";ctx.fillText(pid,x,c.height-16);
      if(!values.length){ctx.fillText("—",x,margin.top+h/2);ctx.globalAlpha=1;return;}
      const q1=quantile(values,.25),med=quantile(values,.5),q3=quantile(values,.75),iqr=q3-q1;
      const low=values.find(v=>v>=q1-1.5*iqr),high=[...values].reverse().find(v=>v<=q3+1.5*iqr),half=Math.max(2,Math.min(14,slot*.35));
      ctx.strokeStyle=ctx.fillStyle=pidColor(pid);ctx.lineWidth=1.5;
      ctx.beginPath();ctx.moveTo(x,yp(low));ctx.lineTo(x,yp(high));ctx.stroke();
      ctx.fillStyle="#1c2837";ctx.fillRect(x-half,yp(q3),half*2,Math.max(1,yp(q1)-yp(q3)));
      ctx.strokeRect(x-half,yp(q3),half*2,Math.max(1,yp(q1)-yp(q3)));
      ctx.lineWidth=2.5;ctx.beginPath();ctx.moveTo(x-half,yp(med));ctx.lineTo(x+half,yp(med));ctx.stroke();
      ctx.fillStyle=pidColor(pid);
      for(const v of values)if(v<low||v>high){ctx.beginPath();ctx.arc(x,yp(v),1.6,0,Math.PI*2);ctx.fill();}
      ctx.globalAlpha=1;
    });
    ctx.fillStyle="#aec1d9";ctx.textAlign="left";ctx.fillText(`PID / ${ppeLabels[component]} [nm]`,margin.left,c.height-2);
    $("#ppeGraphInfo").textContent=`${scope} One box per PID: Q1/Median/Q3, 1.5 IQR whiskers, outliers. Value axis fixed to full data.`;
  }
}
function niceStep(value){
  const base=10**Math.floor(Math.log10(value));
  for(const f of [1,2,2.5,5,10])if(f*base>=value)return f*base;
}
// One point per PID: X component on the X axis, Y component on the Y axis, same nm-per-pixel on both axes.
// Mean is centered on 0; SD starts at 0. The axes fit the applied selection because there are few points.
function drawPpeScatter(c,ctx,margin,w,h,scope){
  const stat=$("#ppeStat").value,rows=state.result.ppe.table.filter(r=>r.pid!=="All"&&r.n>0);
  const xs=rows.map(r=>r[`x_${stat}`]),ys=rows.map(r=>r[`y_${stat}`]);
  const reach=Math.max(1e-9,...xs.map(Math.abs),...ys.map(Math.abs))*1.15;
  const step=niceStep(reach/(stat==="mean"?2:4));
  const size=Math.min(w,h),left=margin.left+(w-size)/2,top=margin.top+(h-size)/2;
  const [lo,hi]=stat==="mean"?[-2*step,2*step]:[0,4*step];
  const xp=x=>left+(x-lo)/(hi-lo)*size,yp=y=>top+size-(y-lo)/(hi-lo)*size;
  for(let i=0;i<=4;i++){
    const v=lo+(hi-lo)*i/4,zero=Math.abs(v)<1e-12;
    ctx.strokeStyle=zero?"#e7edf7":"#40536a";ctx.lineWidth=zero?1.8:1;ctx.beginPath();
    ctx.moveTo(xp(v),top);ctx.lineTo(xp(v),top+size);ctx.moveTo(left,yp(v));ctx.lineTo(left+size,yp(v));ctx.stroke();
    ctx.fillStyle="#aec1d9";ctx.textAlign="center";ctx.fillText(+v.toPrecision(3),xp(v),top+size+14);
    ctx.textAlign="right";ctx.fillText(+v.toPrecision(3),left-5,yp(v)+3);
  }
  ctx.font="bold 10px sans-serif";
  rows.forEach((r,i)=>{
    const x=xp(xs[i]),y=yp(ys[i]);
    ctx.globalAlpha=!state.ppeSelected||state.ppeSelected===r.pid?1:.25;
    ctx.fillStyle=pidColor(r.pid);ctx.strokeStyle="#111923";ctx.lineWidth=1.5;
    ctx.beginPath();ctx.arc(x,y,6,0,Math.PI*2);ctx.fill();ctx.stroke();
    ctx.fillStyle="#e7edf7";ctx.textAlign="left";ctx.fillText(r.pid,x+8,y-6);
    state.ppePoints.push({pid:r.pid,x,y});
  });
  ctx.globalAlpha=1;ctx.font="10px sans-serif";
  const name=stat==="mean"?"Mean":"SD";
  ctx.fillStyle="#aec1d9";ctx.textAlign="left";ctx.fillText(`${name} PPE X [nm] (Y axis: ${name} PPE Y [nm])`,margin.left,c.height-2);
  $("#ppeGraphInfo").textContent=`${scope} One point per PID (N > 0): ${name} PPE X vs ${name} PPE Y; same scale on both axes`+
    `${stat==="mean"?", centered on 0":""}. Axes fit the applied selection. Click a point to focus its PID.`;
}
function showLower(name){
  if(name==="ppe"&&single())name="stats";
  state.lower=name;
  document.querySelectorAll("[data-lower]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.lower===name)));
  document.querySelectorAll("[data-lower-pane]").forEach(pane=>pane.hidden=pane.dataset.lowerPane!==name);
  if(name==="ppe")requestAnimationFrame(drawPpeGraph);else requestAnimationFrame(drawGraph);
}
async function exportPpeValues(){
  if(hasPending()||state.applying){fail(Error("Pending selection. Apply selection before exporting."));return;}
  if(!backend){fail(Error("CSV export is available in the Qt application"));return;}
  state.exportingPpe=true;dirtyStatus();
  try{
    const rows=await request("ppe_values",state.result.filters);
    const test=!!state.index.metadata.test_chip_override;
    saveCsv(rows.map(row=>({...row,test_only_chip_override:test})),"ppe_values.csv");
  }catch(error){fail(error);}
  finally{state.exportingPpe=false;dirtyStatus();}
}
function position(event,canvas){const r=canvas.getBoundingClientRect();return [event.clientX-r.left,event.clientY-r.top];}
function imagePoint(point,view=mainView){const t=view.transform;return [(point[0]-t.x)/t.scale,(point[1]-t.y)/t.scale];}
function bindImageCanvas(view){
  const image=view.canvas;
  image.onpointerdown=e=>{
    if(e.button!==0||!state.image)return;
    const point=position(e,image),roi=e.ctrlKey;
    const clamp=p=>[Math.max(0,Math.min(state.image.width,p[0])),Math.max(0,Math.min(state.image.height,p[1]))];
    const start=roi?clamp(imagePoint(point,view)):point;
    state.drag={kind:roi?"roi":"pan",view,start,end:start,pixelStart:imagePoint(point,view)};
    image.setPointerCapture(e.pointerId);
  };
  image.onpointermove=e=>{
    if(!state.image)return;
    const point=position(e,image);
    if(state.drag?.kind==="pan"&&state.drag.view===view){
      view.transform.x+=point[0]-state.drag.end[0];view.transform.y+=point[1]-state.drag.end[1];state.drag.end=point;drawImage();
    }else if(state.drag?.kind==="roi"&&state.drag.view===view){
      const p=imagePoint(point,view);
      state.drag.end=[Math.max(0,Math.min(state.image.width,p[0])),Math.max(0,Math.min(state.image.height,p[1]))];drawImage();
    }else if(!state.drag){
      const p=imagePoint(point,view);
      const hit=roles.flatMap(role=>vector(role)?state.image.vectors[role].map(v=>({...v,role})):[])
        .find(v=>Math.hypot(v.start[0]-p[0],v.start[1]-p[1])*view.transform.scale<10);
      image.title=hit?`${hit.role==="base"?"Base":"Comparison"} ΔX=${fmt(hit.dx)} nm, ΔY=${fmt(hit.dy)} nm`:"";
      if(state.areaEnabled&&state.areaData?.id===state.selected?.id){
        const ctx=image.getContext("2d");
        ctx.save();ctx.resetTransform();
        const pattern=state.areaData.patterns.find(item=>areaKinds.some(kind=>ctx.isPointInPath(item[kind].path,...p,"evenodd")));
        ctx.restore();
        if(pattern)image.title=`PID ${pattern.pid} / Inward: ${fmt(pattern.inward.area_nm2)} nm² / Outward: ${fmt(pattern.outward.area_nm2)} nm²`;
      }
    }
  };
  image.onpointerup=e=>{
    const drag=state.drag;if(drag?.view!==view)return;state.drag=null;
    const distance=Math.hypot(drag.start[0]-drag.end[0],drag.start[1]-drag.end[1]);
    if(drag.kind==="roi"&&distance*view.transform.scale>3){
      state.contourTarget="roi";
      setFilters({roi:rectangle(drag.start,drag.end)});
    }else if(drag.kind==="roi"||distance<=3){
      selectContourPattern(drag.pixelStart,view,e.ctrlKey);
    }
    drawImage();
  };
  image.onpointercancel=()=>{if(state.drag?.view===view)state.drag=null;drawImage();};
  image.onwheel=e=>{
    e.preventDefault();if(!state.image)return;
    const [x,y]=position(e,image),t=view.transform,scale=Math.max(.1,Math.min(50,t.scale*(e.deltaY<0?1.2:1/1.2)));
    view.transform={scale,x:x-(x-t.x)*scale/t.scale,y:y-(y-t.y)*scale/t.scale};drawImage();
  };
}
const LAYOUT_KEY="comparisonViewer.layout.v1";
function loadLayout(){
  try{return JSON.parse(localStorage.getItem(LAYOUT_KEY))||{};}catch{return {};}
}
function saveLayout(patch){
  try{localStorage.setItem(LAYOUT_KEY,JSON.stringify({...loadLayout(),...patch}));}catch{}
}
function setLowerHeight(height){
  const center=$("#center");
  if(height===null){center.style.removeProperty("--lower-height");return null;}
  const max=Math.max(120,center.clientHeight-160),value=Math.round(Math.max(120,Math.min(max,height)));
  center.style.setProperty("--lower-height",`${value}px`);
  return value;
}
function setStatsMode(mode){
  const center=$("#center");
  center.classList.toggle("stats-min",mode==="min");center.classList.toggle("stats-max",mode==="max");
  $("#statsMin").setAttribute("aria-pressed",String(mode==="min"));
  $("#statsMax").setAttribute("aria-pressed",String(mode==="max"));
  $("#statsMin").title=mode==="min"?"Restore the statistics panel":"Minimize the statistics panel";
  $("#statsMax").title=mode==="max"?"Restore the statistics panel":"Maximize the statistics panel";
  // The splitter is inactive while minimized/maximized so the saved normal height is not overwritten.
  $("#splitter").tabIndex=mode==="normal"?0:-1;
  $("#splitter").setAttribute("aria-disabled",String(mode!=="normal"));
  saveLayout({statsMode:mode});
}
const COLLAPSIBLE={waferPane:"wafer map",chipPane:"chip map",imagesPane:"image list"};
function setCollapsed(pane,collapsed){
  pane.classList.toggle("collapsed",collapsed);
  const button=pane.querySelector(".collapse"),name=COLLAPSIBLE[pane.id],sideways=pane.id==="imagesPane";
  if(sideways){$("#upperRow").classList.toggle("list-collapsed",collapsed);$("#listSplitter").tabIndex=collapsed?-1:0;}
  button.textContent=collapsed?"▸":sideways?"◂":"▾";button.setAttribute("aria-expanded",String(!collapsed));
  button.setAttribute("aria-label",`${collapsed?"Expand":"Collapse"} ${name}`);
}
function setListWidth(width){
  const row=$("#upperRow");
  if(width===null){row.style.removeProperty("--list-width");return null;}
  const max=Math.max(220,Math.min(600,row.clientWidth-8-360)),value=Math.round(Math.max(220,Math.min(max,width)));
  row.style.setProperty("--list-width",`${value}px`);
  return value;
}
function bindListSplitter(){
  const splitter=$("#listSplitter"),pane=$("#imagesPane");
  splitter.onpointerdown=e=>{
    if(e.button!==0||pane.classList.contains("collapsed"))return;
    splitter.setPointerCapture(e.pointerId);splitter.classList.add("dragging");
    splitter.drag={x:e.clientX,width:pane.getBoundingClientRect().width};
  };
  splitter.onpointermove=e=>{
    if(splitter.drag)splitter.drag.value=setListWidth(splitter.drag.width+(e.clientX-splitter.drag.x));
  };
  splitter.onpointerup=splitter.onpointercancel=()=>{
    if(!splitter.drag)return;
    const value=splitter.drag.value;splitter.drag=null;splitter.classList.remove("dragging");
    if(Number.isFinite(value))saveLayout({listWidth:value});
  };
  splitter.ondblclick=()=>{setListWidth(null);saveLayout({listWidth:null});};
  splitter.onkeydown=e=>{
    if(!["ArrowLeft","ArrowRight"].includes(e.key)||pane.classList.contains("collapsed"))return;
    e.preventDefault();
    saveLayout({listWidth:setListWidth(pane.getBoundingClientRect().width+(e.key==="ArrowRight"?20:-20))});
  };
}
function setSourceRatio(ratio){
  const total=$("#imageWorkspace").clientWidth;
  const width=Math.max(Math.min(160,total*.35),Math.min(total-188,total*ratio));
  $("#imageWorkspace").style.setProperty("--source-width",`${100*width/Math.max(1,total)}%`);
  $("#contourSplitter").setAttribute("aria-valuenow",String(Math.round(width)));
  return width/Math.max(1,total);
}
function bindContourSplitter(){
  const splitter=$("#contourSplitter");
  const restore=()=>{
    const ratio=loadLayout().sourceRatio;
    setSourceRatio(Number.isFinite(ratio)?ratio:.4);
  };
  restore();new ResizeObserver(restore).observe($("#imageWorkspace"));
  splitter.onpointerdown=e=>{
    if(e.button!==0)return;
    splitter.setPointerCapture(e.pointerId);splitter.classList.add("dragging");
    splitter.drag={x:e.clientX,width:$("#sourceView").getBoundingClientRect().width};
  };
  splitter.onpointermove=e=>{
    if(splitter.drag)splitter.drag.ratio=setSourceRatio(
      (splitter.drag.width+e.clientX-splitter.drag.x)/$("#imageWorkspace").clientWidth);
  };
  splitter.onpointerup=splitter.onpointercancel=()=>{
    if(!splitter.drag)return;
    const ratio=splitter.drag.ratio;splitter.drag=null;splitter.classList.remove("dragging");
    if(Number.isFinite(ratio))saveLayout({sourceRatio:ratio});
  };
  splitter.ondblclick=()=>{setSourceRatio(.4);saveLayout({sourceRatio:.4});};
  splitter.onkeydown=e=>{
    if(!["ArrowLeft","ArrowRight"].includes(e.key))return;
    e.preventDefault();
    saveLayout({sourceRatio:setSourceRatio(($("#sourceView").clientWidth+
      (e.key==="ArrowRight"?20:-20))/$("#imageWorkspace").clientWidth)});
  };
}
// Splitter positions, statistics size and collapsed panels are kept between sessions.
function bindLayout(){
  const layout=loadLayout(),splitter=$("#splitter"),lower=$("#lower");
  if(Number.isFinite(layout.lowerHeight))setLowerHeight(layout.lowerHeight);
  setStatsMode(["min","max"].includes(layout.statsMode)?layout.statsMode:"normal");
  const panes=Object.keys(COLLAPSIBLE).map(id=>document.getElementById(id));
  for(const pane of panes){
    setCollapsed(pane,!!layout.collapsed?.[pane.id]);
    pane.querySelector(".collapse").onclick=()=>{
      setCollapsed(pane,!pane.classList.contains("collapsed"));
      saveLayout({collapsed:Object.fromEntries(panes.map(p=>[p.id,p.classList.contains("collapsed")]))});
    };
  }
  if(Number.isFinite(layout.listWidth))setListWidth(layout.listWidth);
  bindListSplitter();
  bindContourSplitter();
  $("#statsMin").onclick=()=>setStatsMode($("#center").classList.contains("stats-min")?"normal":"min");
  $("#statsMax").onclick=()=>setStatsMode($("#center").classList.contains("stats-max")?"normal":"max");
  splitter.onpointerdown=e=>{
    if(e.button!==0)return;
    splitter.setPointerCapture(e.pointerId);splitter.classList.add("dragging");
    splitter.drag={y:e.clientY,height:lower.getBoundingClientRect().height};
  };
  splitter.onpointermove=e=>{
    if(!splitter.drag)return;
    splitter.drag.value=setLowerHeight(splitter.drag.height-(e.clientY-splitter.drag.y));
  };
  splitter.onpointerup=splitter.onpointercancel=()=>{
    if(!splitter.drag)return;
    const value=splitter.drag.value;splitter.drag=null;splitter.classList.remove("dragging");
    if(Number.isFinite(value))saveLayout({lowerHeight:value});
  };
  splitter.ondblclick=()=>{setLowerHeight(null);saveLayout({lowerHeight:null});};
  splitter.onkeydown=e=>{
    if(!["ArrowUp","ArrowDown"].includes(e.key)||splitter.getAttribute("aria-disabled")==="true")return;
    e.preventDefault();
    saveLayout({lowerHeight:setLowerHeight(lower.getBoundingClientRect().height+(e.key==="ArrowUp"?20:-20))});
  };
}
function bind(){
  $("#areaDifference").onchange=()=>{
    state.areaEnabled=$("#areaDifference").checked;syncAreaStatus();drawImage();refreshAreaImage();
  };
  $("#areaRetry").onclick=()=>{state.areaError=null;refreshAreaImage();};
  document.querySelectorAll("[data-contour-measure]").forEach(button=>button.onclick=()=>{
    state.contourMeasure=button.dataset.contourMeasure;syncContourControls();refreshContourView();
  });
  document.querySelectorAll("[data-area-channel]").forEach(button=>button.onclick=()=>{
    state.areaChannel=button.dataset.areaChannel;syncContourControls();drawContourView();
  });
  $("#contourViewButton").onclick=()=>setContourView(!state.contourView);
  $("#contourTarget").onchange=()=>{
    state.contourTarget=$("#contourTarget").value;drawImage();refreshContourView();
  };
  $("#contourClearPids").onclick=()=>{state.contourPids=[];drawImage();refreshContourView();};
  document.querySelectorAll("[data-contour-role]").forEach(button=>button.onclick=()=>{
    state.contourRole=button.dataset.contourRole;syncContourControls();drawContourView();
  });
  $("#contourSem").onchange=()=>{
    state.contourSem=$("#contourSem").checked;syncContourControls();drawContourView();
  };
  $("#contourDesign").onchange=()=>{
    state.contourDesign=$("#contourDesign").checked;drawContourView();
  };
  $("#contourSemOpacity").oninput=()=>{
    state.contourSemOpacity=Number($("#contourSemOpacity").value)/100;queueContourDraw();
  };
  document.querySelectorAll("[data-contour-scope]").forEach(button=>button.onclick=()=>{
    state.contourScope=button.dataset.contourScope;
    syncContourControls();drawImage();refreshContourView();
  });
  document.querySelectorAll("[data-contour-display]").forEach(button=>button.onclick=()=>{
    state.contourDisplay=button.dataset.contourDisplay;syncContourControls();drawContourView();
  });
  document.querySelectorAll("[data-contour-focus]").forEach(button=>button.onclick=()=>{
    state.contourFocus=button.dataset.contourFocus;syncContourControls();drawContourView();
  });
  $("#contourPid").onchange=()=>{
    state.contourPid=$("#contourPid").value||null;drawImage();refreshContourView();
  };
  $("#contourRetry").onclick=()=>refreshContourView();
  $("#contourZoomIn").onclick=()=>zoomContour(mainContour,1.5);
  $("#contourZoomOut").onclick=()=>zoomContour(mainContour,1/1.5);
  $("#contourFit").onclick=()=>fitContour(mainContour);
  bindContourCanvas(mainContour);
  $("#pno").onchange=()=>setFilters({pno:Number($("#pno").value),chips:[],chipRect:null,roi:null});
  $("#ms").onchange=()=>setFilters({ms:$("#ms").value});
  $("#clearChips").onclick=()=>setFilters({chips:[]});
  $("#clearChipRect").onclick=()=>setFilters({chipRect:null});
  $("#clearRoi").onclick=()=>setFilters({roi:null});
  $("#clearAll").onclick=()=>setFilters({chips:[],chipRect:null,roi:null});
  $("#undo").onclick=()=>{
    if(!state.history.length)return;state.filters=state.history.pop();state.revision++;
    $("#pno").value=state.filters.pno;syncFilterControls();conditions();renderList();drawMaps();drawImage();drawGraph();refreshContourView();
  };
  const list=$("#images");
  list.onscroll=paintList;new ResizeObserver(paintList).observe(list);
  list.onkeydown=$("#image").onkeydown=e=>{
    if(["ArrowDown","ArrowUp"].includes(e.key)){e.preventDefault();stepImage(e.key==="ArrowDown"?1:-1);}
  };
  $("#imageSearch").oninput=()=>{state.search=$("#imageSearch").value;$("#images").scrollTop=0;renderList();};
  $("#update").onclick=updateStatistics;
  // Cohort columns differ by session: a Base-only session has no pairs to count.
  const cohortColumns=()=>single()?{images:state.result.images,cohort:"all selected Base images; Comp not loaded"}:
    {image_pairs:state.result.paired_images,cohort:"matched image pairs; not common-pattern differences",
      unpaired_base:state.result.counts.base.unpaired,unpaired_comparison:state.result.counts.comparison.unpaired};
  $("#exportStats").onclick=()=>{
    if(hasPending()||state.applying){fail(Error("Pending selection. Apply selection before exporting."));return;}
    saveCsv(state.result.table.filter(row=>shownRoles().includes(row.role)).map(({role,...row})=>({dataset:roleName(role),...row,metric:`${labels[row.metric]} [nm]`,computed_at:state.resultTime,filters:JSON.stringify(state.result.filters),
      ...cohortColumns(),test_only_chip_override:!!state.index.metadata.test_chip_override})),"center_offset_statistics.csv");
  };
  $("#exportOffsetPid").onclick=()=>{
    if(hasPending()||state.applying){fail(Error("Pending selection. Apply selection before exporting."));return;}
    const meta=state.index.metadata;
    saveCsv(state.result.offset_by_pid.table.filter(row=>shownRoles().includes(row.role)).map(row=>({
      dataset:roleName(row.role),pid:row.pid,n:row.n,excluded:row.excluded,
      ...Object.fromEntries(["x","y","magnitude"].flatMap(c=>["mean","sd"].map(k=>[`offset_${c}_${k}`,row[`${c}_${k}`]]))),
      unit:"nm",definition:"Center - Layout per pattern",sd:"population",computed_at:state.resultTime,
      filters:JSON.stringify(state.result.filters),...cohortColumns(),
      base_dataset:meta.datasets.base,comparison_dataset:meta.datasets.comparison??"",
      test_only_chip_override:!!meta.test_chip_override})),"center_offset_by_pid.csv");
  };
  document.querySelectorAll("[data-stats-view]").forEach(button=>button.onclick=()=>showStatsView(button.dataset.statsView));
  $("#rawCsv").onclick=()=>saveCsv((state.image?.rows[state.rawRole]||[]).map(r=>
    ({dataset:roleName(state.rawRole),...Object.fromEntries(state.columns.filter(k=>k!=="#").map(k=>[rawLabel(k),r[k]??null])),
      TEST_ONLY_CHIP_OVERRIDE:!!state.index.metadata.test_chip_override})),"measurements.csv");
  document.querySelectorAll("[data-graph]").forEach(button=>button.onclick=()=>{
    state.graph=button.dataset.graph;
    document.querySelectorAll("[data-graph]").forEach(b=>b.setAttribute("aria-pressed",String(b===button)));
    drawGraph();
  });
  $("#metric").onchange=drawGraph;
  document.querySelectorAll("[data-lower]").forEach(button=>button.onclick=()=>showLower(button.dataset.lower));
  document.querySelectorAll("[data-ppe-graph]").forEach(button=>button.onclick=()=>{
    state.ppeGraph=button.dataset.ppeGraph;
    document.querySelectorAll("[data-ppe-graph]").forEach(b=>b.setAttribute("aria-pressed",String(b===button)));
    drawPpeGraph();
  });
  $("#ppeComponent").onchange=$("#ppeStat").onchange=drawPpeGraph;
  $("#ppeGraph").onclick=e=>{
    if(state.ppeGraph!=="scatter")return;
    const c=$("#ppeGraph"),r=c.getBoundingClientRect(),x=(e.clientX-r.left)*c.width/r.width,y=(e.clientY-r.top)*c.height/r.height;
    const hit=(state.ppePoints||[]).map(p=>({...p,d:Math.hypot(p.x-x,p.y-y)})).filter(p=>p.d<=10).sort((a,b)=>a.d-b.d)[0];
    state.ppeSelected=hit?(state.ppeSelected===hit.pid?null:hit.pid):null;renderPpe();drawPpeGraph();drawImage();
  };
  $("#ppeBin").onchange=()=>{
    const value=Number($("#ppeBin").value);
    if(Number.isFinite(value)&&value>0)state.ppeBin=value;else fail(Error("Bin width must be a positive number"));
    drawPpeGraph();
  };
  $("#ppeBinAuto").onclick=()=>{state.ppeBin=null;drawPpeGraph();};
  $("#pidLabels").onchange=drawImage;
  $("#exportPpeStats").onclick=()=>{
    if(hasPending()||state.applying){fail(Error("Pending selection. Apply selection before exporting."));return;}
    const meta=state.index.metadata;
    saveCsv(state.result.ppe.table.map(row=>({...row,definition:PPE_DEFINITION,sd:"population",
      computed_at:state.resultTime,filters:JSON.stringify(state.result.filters),image_pairs:state.result.paired_images,
      base_dataset:meta.datasets.base,comparison_dataset:meta.datasets.comparison,
      test_only_chip_override:!!meta.test_chip_override})),"ppe_statistics.csv");
  };
  $("#exportPpeValues").onclick=exportPpeValues;
  document.querySelectorAll("[data-map-role]").forEach(button=>button.onclick=()=>{
    state.mapRole=button.dataset.mapRole;
    document.querySelectorAll("[data-map-role]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.mapRole===state.mapRole)));
    drawMaps();loadComparisonImage();
  });
  document.querySelectorAll("[data-raw-role]").forEach(button=>button.onclick=()=>{state.rawRole=button.dataset.rawRole;renderRaw();});
  const showRaw=open=>{
    $("#rawPane").hidden=!open;$("#rawToggle").setAttribute("aria-expanded",String(open));
    $("#rawToggle .arrow").textContent=open?"▸":"◂";renderRaw();
  };
  $("#rawToggle").onclick=()=>showRaw($("#rawPane").hidden);
  $("#rawClose").onclick=()=>showRaw(false);
  const menu=$("#exportItems"),showMenu=open=>{menu.hidden=!open;$("#exportButton").setAttribute("aria-expanded",String(open));};
  $("#exportButton").onclick=()=>{showMenu(menu.hidden);if(!menu.hidden)menu.querySelector("button:not(:disabled)")?.focus();};
  menu.addEventListener("click",()=>showMenu(false));
  document.addEventListener("pointerdown",e=>{if(!$("#exportMenu").contains(e.target))showMenu(false);});
  document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!menu.hidden){showMenu(false);$("#exportButton").focus();}});
  document.querySelectorAll("[data-overlay-group]").forEach(button=>button.onclick=()=>{
    const inputs=[...button.parentElement.querySelectorAll("input:not(:disabled)")];
    const checked=!inputs.every(input=>input.checked);
    inputs.forEach(input=>input.checked=checked);syncOverlayGroups();drawImage();
  });
  document.querySelectorAll("[data-layer],[data-vector]").forEach(input=>input.onchange=()=>{
    syncOverlayGroups();drawImage();
  });
  syncOverlayGroups();
  $("#multiplier").oninput=drawImage;$("#fit").onclick=()=>fit(mainView);$("#expand").onclick=openExpand;
  window.addEventListener("pagehide",()=>state.expand?.win.close());
  bindLayout();
  $("#focusChip").onchange=()=>{setFocusChip($("#focusChip").value.split(",").map(Number));drawChip();};
  $("#fitChip").onclick=()=>{state.chipPan={x:0,y:0};drawChip();};
  $("#wafer").onclick=event=>{
    const [x,y]=position(event,$("#wafer")),hit=state.waferCells.find(c=>contains(x,y,c.box));
    if(!hit)return;
    setFocusChip([hit.x,hit.y]);
    const key=[hit.x,hit.y],exists=state.filters.chips.some(v=>v[0]===hit.x&&v[1]===hit.y);
    const chips=event.ctrlKey?(exists?state.filters.chips.filter(v=>v[0]!==hit.x||v[1]!==hit.y):[...state.filters.chips,key]):[key];
    setFilters({chips,chipRect:null});
  };
  const chip=$("#chip");
  const tooltip=entry=>entry?`Offset X=${fmt(entry.dx)} nm / Offset Y=${fmt(entry.dy)} nm / median |Offset|=${fmt(entry.magnitude)} nm / valid N=${entry.n}`:"No calculated valid vectors";
  $("#wafer").onpointermove=e=>{
    const [x,y]=position(e,$("#wafer")),hit=state.waferCells.find(c=>contains(x,y,c.box));
    $("#wafer").title=hit?`Chip (${hit.x},${hit.y}) / ${hit.measured?"Measured":"Unmeasured"} / ${tooltip(hit.data)}`:"";
  };
  chip.onpointerdown=e=>{
    if(e.button!==0||!state.chipProject)return;
    const start=position(e,chip);state.drag={kind:e.ctrlKey?"chipRoi":"chipPan",start,end:start};chip.setPointerCapture(e.pointerId);
  };
  chip.onpointermove=e=>{
    const [x,y]=position(e,chip);
    if(state.drag?.kind==="chipRoi"){state.drag.end=[x,y];drawChip();}
    else if(state.drag?.kind==="chipPan"){
      state.chipPan.x+=x-state.drag.end[0];state.chipPan.y+=y-state.drag.end[1];
      state.drag.end=[x,y];drawChip();
    }
    else{
      const hit=state.chipPoints.find(p=>Math.hypot(p.x-x,p.y-y)<9);
      chip.title=hit?`${hit.entry.name} / Chip (${hit.entry.chip_x},${hit.entry.chip_y}) / ${tooltip(hit.data)}`:"";
    }
  };
  chip.onpointerup=e=>{
    if(!["chipPan","chipRoi"].includes(state.drag?.kind))return;
    const drag=state.drag;state.drag=null;
    const end=position(e,chip);
    const moved=Math.hypot(end[0]-drag.start[0],end[1]-drag.start[1]);
    if(drag.kind==="chipPan"&&moved<5){
      const hits=state.chipPoints.filter(p=>Math.hypot(p.x-end[0],p.y-end[1])<9);
      const at=hits.findIndex(p=>isSelected(p.entry));
      if(hits.length){
        const hit=hits[(at+1)%hits.length].entry;
        const target=hit.role==="base"?hit:baseEntries().find(b=>b.pno===hit.pno&&b.seq===hit.seq&&b.ms===hit.ms&&b.chip_x===hit.chip_x&&b.chip_y===hit.chip_y);
        if(target&&state.matched.some(e=>e.id===target.id))choose(target);
        else setImageStatus("Comparison map position has no corresponding Base image in the current selection.");
      }
    }else if(drag.kind==="chipRoi"&&moved>=5){
      setFilters({chipRect:rectangle(state.chipProject.inverse(...drag.start),state.chipProject.inverse(...end))});
    }drawChip();
  };
  chip.onpointercancel=()=>{state.drag=null;drawChip();};
  bindImageCanvas(mainView);
}
function bindDatasetButtons(){
  const call=name=>()=>{
    if(!backend){fail(Error("File selection is available in the Qt application"));return;}
    backend[name]();
  };
  $("#openBase").onclick=call("openBase");$("#openComparison").onclick=call("openComparison");
  $("#closeComparison").onclick=call("closeComparison");
}
function showDatasets(){
  const meta=state.index?.metadata,loaded=!!state.index;
  for(const role of roles){
    const slot=$(role==="base"?"#baseName":"#comparisonName"),name=meta?.datasets?.[role];
    slot.textContent=name||"(not loaded)";slot.title=meta?.files?.[role]||"";slot.classList.toggle("missing",!name);
  }
  $("#closeComparison").hidden=!loaded||single();
  $("#openComparison").disabled=!loaded;
  $("#openComparison").title=loaded?"Open a Comp dataset to compare with Base":"Open a Base dataset first";
  $("#openComparison").textContent=loaded&&!single()?"Replace Comp...":"Open Comp...";
  $("#openBase").textContent=loaded?"Replace Base...":"Open Base...";
}
// Comp controls stay in place but are disabled, so the layout does not move when Comp is closed.
function applyDatasetMode(){
  if(!single())return;
  $("#areaDifference").disabled=true;$("#areaDifference").closest("label").title="Area difference requires both Base and Comp";
  state.mapRole="base";state.rawRole="base";state.contourRole="base";state.contourDisplay="focus";state.contourFocus="base";
  for(const selector of ['[data-map-role="comparison"]','[data-raw-role="comparison"]','[data-lower="ppe"]']){
    document.querySelectorAll(selector).forEach(button=>{button.disabled=true;button.title=NO_COMP;});
  }
  // A disabled button shows no tooltip in every browser, so the reason is part of the label.
  $('[data-lower="ppe"]').textContent="PPE (Base − Comparison): requires Comp";
  $('[data-lower="ppe"]').title="PPE requires both Base and Comp";
  for(const id of ["#exportPpeStats","#exportPpeValues"])$(id).title="PPE requires both Base and Comp";
  document.querySelectorAll('[data-layer="comparison"],[data-vector="comparison"]').forEach(input=>{
    input.checked=false;input.disabled=true;input.closest("label").title=NO_COMP;
  });
  $("#statsTitle").textContent="Applied statistics / Base only";$("#graphTitle").textContent="Applied distribution / Base only";
}
async function init(){
  bindDatasetButtons();
  state.index=await request("index");
  showDatasets();
  if(state.index===null){
    document.body.classList.add("no-dataset");$("#startup").hidden=false;
    window.viewerReady=true;return;
  }
  state.entriesById=new Map(state.index.images.map(e=>[e.id,e]));
  const pnos=[...new Set(state.index.images.filter(e=>e.role==="base").map(e=>e.pno))].sort((a,b)=>a-b);
  if(!pnos.length)throw Error("No Base images available");
  for(const pno of pnos)$("#pno").add(new Option(pno,pno));
  state.filters.pno=pnos[0];
  $("#pnoControl").hidden=pnos.length===1;
  syncFilterControls();
  applyDatasetMode();
  const testOverride=state.index.metadata.test_chip_override;
  if(testOverride){
    $("#testWarning").textContent=testOverride.warning.replace(/^TEST ONLY:\s*/,"").replace(/^./,c=>c.toUpperCase());
    $("#testWarning").hidden=false;
    document.body.classList.add("test-chip-override");
    $("#multiplier").value="10";
  }
  bind();for(const id of ["#wafer","#chip","#image","#graph","#ppeGraph","#contourCanvas"])setupCanvas(id);
  conditions();renderList();drawMaps();
  if(testOverride){
    if(testOverride.initial_image_id){
      const initial=state.visible.find(entry=>entry.id===testOverride.initial_image_id);
      if(!initial)throw Error("Test overlay initial image is missing");
      await choose(initial);
    }
  }
  window.viewerReady=true;
  await updateStatistics();
}
window.addEventListener("error",event=>fail(event.error||event.message));
window.addEventListener("unhandledrejection",event=>fail(event.reason));
if(window.testBackend){init().catch(fail);}
else if(typeof QWebChannel==="undefined"){fail(Error("Start this Viewer using start-viewer.cmd"));}
else{
  new QWebChannel(qt.webChannelTransport,channel=>{
    backend=channel.objects.backend;
    backend.reply.connect((id,text)=>{
      const job=pending.get(id);if(!job)return;pending.delete(id);clearTimeout(job.timer);
      try{
        const result=JSON.parse(text);
        if(result.ok)job.resolve(result.data);else job.reject(Error(result.error));
      }catch(error){job.reject(error);}
    });
    init().catch(fail);
  });
}
