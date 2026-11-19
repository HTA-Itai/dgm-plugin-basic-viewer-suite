const $ = id => document.getElementById(id);
let bridge = null;
let state = null;
let currentImageId = null;
let activeMetric = null;
let activeStats = null;
let ruleNames = [];

function callBridge(command, params = {}) {
  return new Promise((resolve, reject) => {
    if (!bridge) {
      resolve(null);
      return;
    }
    const id = Math.random().toString(36).slice(2);
    const handler = (identity, text) => {
      if (identity !== id) return;
      bridge.reply.disconnect(handler);
      const response = JSON.parse(text);
      response.ok ? resolve(response.data) : reject(new Error(response.error));
    };
    bridge.reply.connect(handler);
    bridge.request(id, command, JSON.stringify(params));
  });
}

function drawCanvas(id, pattern = false) {
  const canvas = $(id);
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const dpr = devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.fillStyle = "#101720";
  ctx.fillRect(0, 0, rect.width, rect.height);
  if (pattern) {
    ctx.strokeStyle = "#536a84";
    ctx.fillStyle = "#64748b";
    for (let y = 0; y < 24; y++) {
      for (let x = 0; x < 24; x++) {
        if ((x + y) % 2) continue;
        const cx = 28 + x * (rect.width - 56) / 23;
        const cy = 12 + y * (rect.height - 24) / 23;
        ctx.fillRect(cx - 2, cy - 2, 4, 4);
        ctx.strokeRect(cx - 5, cy - 5, 10, 10);
      }
    }
  } else {
    const palette = activeMetric === "Area"
      ? ["#263b52", "#55c8ff", "#ffd166", "#ff6767"]
      : activeMetric === "SizeX"
      ? ["#263b52", "#9fee9f", "#55c8ff", "#b29aff"]
      : ["#263b52", "#82d7d7", "#ffd166", "#ff9fd7"];
    for (let y = 0; y < 8; y++) {
      for (let x = 0; x < 12; x++) {
        ctx.fillStyle = palette[(x + y) % palette.length];
        ctx.fillRect(18 + x * 22, 18 + y * 19, 14, 12);
        ctx.strokeStyle = "#536a84";
        ctx.strokeRect(18 + x * 22, 18 + y * 19, 14, 12);
      }
    }
  }
}

function drawPatternMetric(canvas, image) {
  drawCanvas("sourceImage", false);
  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const patterns = image.patterns || [];
  if (!patterns.length) return;
  const xs = patterns.map(p => p.layout_x).filter(v => v !== null);
  const ys = patterns.map(p => p.layout_y).filter(v => v !== null);
  const xmin = Math.min(...xs), xmax = Math.max(...xs);
  const ymin = Math.min(...ys), ymax = Math.max(...ys);
  const sx = x => 28 + (x - xmin) / Math.max(1, xmax - xmin) * (rect.width - 56);
  const sy = y => 12 + (y - ymin) / Math.max(1, ymax - ymin) * (rect.height - 24);
  const values = patterns.map(p => {
    const raw = p.raw || {};
    const value = raw[activeMetric];
    return typeof value === "number" ? value : null;
  }).filter(v => v !== null);
  const lo = values.length ? Math.min(...values) : 0;
  const hi = values.length ? Math.max(...values) : 1;
  patterns.forEach((p, i) => {
    if (p.layout_x === null || p.layout_y === null) return;
    const value = (p.raw || {})[activeMetric];
    const ratio = typeof value === "number" && hi !== lo ? (value - lo) / (hi - lo) : 0.5;
    const hue = 210 - ratio * 210;
    ctx.fillStyle = `hsl(${hue} 75% 58%)`;
    ctx.beginPath();
    ctx.arc(sx(p.layout_x), sy(p.layout_y), p.edge_cut ? 2 : 5, 0, Math.PI * 2);
    ctx.fill();
  });
  $("imageStatus").textContent = `${image.name} - ${activeMetric}: ${values.length} valid values`;
}

function renderImageList(filter = "") {
  if (!state) return;
  const needle = filter.trim().toLowerCase();
  const images = state.images.filter(image =>
    !needle || image.name.toLowerCase().includes(needle)
    || `${image.chip_x},${image.chip_y}`.includes(needle)
  );
  $("imageCount").textContent = `${images.length} images`;
  const host = $("images");
  host.innerHTML = "";
  images.forEach((image, i) => {
    const row = document.createElement("div");
    row.className = `listRow${i % 2 ? " alt" : ""}${i === 0 ? " selected" : ""}`;
    row.innerHTML = `<span>${image.name}</span><span>${image.seq}</span><span>${image.chip_x},${image.chip_y}</span><span>${image.ms}</span>`;
    row.onclick = () => selectImage(image.id);
    host.appendChild(row);
  });
  if (images.length) selectImage(images[0].id);
}

async function selectImage(id) {
  currentImageId = id;
  document.querySelectorAll(".listRow").forEach(row => row.classList.remove("selected"));
  const host = $("images");
  [...host.children].forEach((row, i) => {
    if (state.images[i] && state.images[i].id === id) row.classList.add("selected");
  });
  try {
    const image = await callBridge("image", {id});
    $("imageName").textContent = image.name;
    drawPatternMetric($("sourceImage"), image);
  } catch (error) {
    $("error").textContent = error.message;
  }
}

function renderStats(stats) {
  activeStats = stats;
  const box = $("boxPlot");
  const histogram = $("histogram");
  box.innerHTML = "";
  histogram.innerHTML = "";
  const names = Object.keys(stats.groups);
  if (!names.length) {
    box.textContent = "No group statistics";
    return;
  }
  names.forEach((name, groupIndex) => {
    const summary = stats.groups[name];
    const item = document.createElement("div");
    item.className = "box";
    item.dataset.group = name;
    item.innerHTML =
      `<div class="whisker"></div>` +
      `<div class="boxRect" style="border-color:hsl(${groupIndex * 55} 75% 58%)"></div>` +
      `<div class="median"></div>` +
      `<div class="boxLabel">${name}<br>N=${summary.n}</div>`;
    for (let i = 0; i < 7; i++) {
      const point = document.createElement("button");
      point.className = "point";
      point.style.background = `hsl(${groupIndex * 55} 75% 58%)`;
      point.style.left = `${25 + (i * 13) % 55}%`;
      point.style.top = `${22 + (i * 11) % 54}%`;
      point.onclick = () => selectBoxPoint(name, i, point);
      item.appendChild(point);
    }
    box.appendChild(item);
  });
  renderHistogram(names[0]);
  $("statsStatus").textContent = `${names.length} groups`;
}

function selectBoxPoint(groupName, pointIndex, point) {
  document.querySelectorAll(".point").forEach(p => p.classList.remove("selected"));
  point.classList.add("selected");
  const image = state.images[(ruleNames.indexOf(groupName) + pointIndex) % state.images.length];
  if (image) {
    const row = [...$("images").children].find((_, i) => state.images[i] && state.images[i].id === image.id);
    [...$("images").children].forEach(r => r.classList.remove("selected"));
    if (row) row.classList.add("selected");
    selectImage(image.id);
  }
  renderHistogram(groupName);
  $("statsStatus").textContent = `${groupName}: point ${pointIndex + 1} → ${image ? image.name : ""}`;
}

function renderHistogram(groupName) {
  const histogram = $("histogram");
  histogram.innerHTML = "";
  $("histTitle").textContent = `${groupName} histogram`;
  [12,18,29,42,36,27,20,14,9,5].forEach(v => {
    const bar = document.createElement("div");
    bar.style.height = `${v / 42 * 100}%`;
    histogram.appendChild(bar);
  });
}

async function refreshStats() {
  if (!state || !activeMetric) return;
  try {
    activeStats = await callBridge("statistics", {metric: activeMetric});
    renderStats(activeStats);
  } catch (error) {
    $("error").textContent = error.message;
  }
}

async function loadIndex() {
  try {
    state = await callBridge("index");
    if (!state) return;
    document.body.classList.add("loaded");
    $("startup").hidden = true;
    $("metricStrip").hidden = false;
    $("datasetName").textContent = state.manifest.dataset_id;
    $("editGroups").disabled = false;
    $("applyGroupRule").disabled = false;
    $("metricSelect").innerHTML = "";
    state.metrics.forEach(metric => {
      const option = document.createElement("option");
      option.value = metric.name;
      option.textContent = metric.name;
      $("metricSelect").appendChild(option);
    });
    ruleNames = state.group_names || [];
    if (!activeMetric && state.metrics.length) activeMetric = state.metrics.find(m => m.name === "Area")?.name || state.metrics[0].name;
    if (activeMetric) $("metricSelect").value = activeMetric;
    renderImageList();
    drawCanvas("wafer");
    drawCanvas("chip");
    refreshStats();
  } catch (error) {
    $("error").textContent = error.message;
  }
}

function setup() {
  $("openDataset").onclick = () => bridge && bridge.openDataset();
  $("editGroups").onclick = () => bridge && bridge.openGroupingEditor();
  $("applyGroupRule").onclick = () => bridge && bridge.applyGroupingRule();
  $("metricSelect").onchange = () => {
    activeMetric = $("metricSelect").value;
    $("waferMetric").textContent = activeMetric;
    if (state) {
      refreshStats();
      if (currentImageId) selectImage(currentImageId);
      drawCanvas("wafer");
      drawCanvas("chip");
    }
  };
  $("imageSearch").oninput = e => renderImageList(e.target.value);
  $("resetSelection").onclick = () => {
    $("selectionStatus").textContent = "No selection";
  };
  window.addEventListener("resize", () => {
    drawCanvas("wafer");
    drawCanvas("chip");
    if (currentImageId) selectImage(currentImageId);
  });
  loadIndex();
}

if (typeof qt !== "undefined") {
  new QWebChannel(qt.webChannelTransport, channel => {
    bridge = channel.objects.backend;
    bridge.datasetChanged.connect(loadIndex);
    bridge.ruleApplied.connect(text => {
      try {
        const result = JSON.parse(text);
        ruleNames = result.group_names || [];
        loadIndex();
      } catch (_) {}
    });
    setup();
  });
} else {
  setup();
}
