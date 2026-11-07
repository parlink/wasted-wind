/* Wasted Wind dashboard: map + Reveal and Predict tabs. Talks only to the API in docs/CONTRACT.md §7.
 * Data-derived text is inserted with textContent, never innerHTML. */
"use strict";

const C = {
  planned: "#2a78d6", waste: "#eb6834", wasteWash: "rgba(235,104,52,0.22)", neutral: "#898781",
  grid: "#e1e0d9", axis: "#c3c2b7", ink: "#0b0b0b", ink2: "#52514e", muted: "#898781", critical: "#d03b3b",
  surface: "#fcfcfb", rampZero: "#b8b6ae",
};
const BASEMAP = "https://tiles.openfreemap.org/styles/positron";
// Approximate boundary lines (labelled "approximate" on the map)
const BOUNDARIES = {
  B4: [[-6.2, 56.05], [-5.2, 56.12], [-4.4, 56.15], [-3.6, 56.28], [-2.9, 56.42], [-2.3, 56.5], [-1.4, 56.5]],
  B6: [[-3.6, 54.92], [-3.05, 55.0], [-2.65, 55.15], [-2.25, 55.4], [-2.05, 55.62], [-1.6, 55.8]],
};

const state = {
  tab: "reveal", farm: null, mode: "all", day: 0, days: [], playTimer: null,
  farms: [], reveal: null, boundaries: null, tomorrow: null, metrics: null,
};
const charts = {};
const $ = (id) => document.getElementById(id);

// ---------- small helpers ----------

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}
function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k === "style") Object.assign(e.style, v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) e.setAttribute(k, v);
  }
  for (const kid of kids.flat()) e.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return e;
}
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }
function showError(err) { const e = $("error"); e.textContent = `Couldn't load data: ${err.message}`; e.hidden = false; }

const nf = (d) => new Intl.NumberFormat("en-GB", { maximumFractionDigits: d });
function fmtEnergy(mwh) {
  if (mwh == null) return "–";
  const a = Math.abs(mwh);
  if (a >= 1e6) return `${nf(2).format(mwh / 1e6)} TWh`;
  if (a >= 1e3) return `${nf(a >= 1e5 ? 0 : 1).format(mwh / 1e3)} GWh`;
  return `${nf(a >= 100 ? 0 : 1).format(mwh)} MWh`;
}
function fmtCompact(x, prefix = "") {
  if (x == null) return "–";
  const a = Math.abs(x);
  const [v, s] = a >= 1e9 ? [x / 1e9, "bn"] : a >= 1e6 ? [x / 1e6, "M"] : a >= 1e3 ? [x / 1e3, "k"] : [x, ""];
  return `${prefix}${nf(Math.abs(v) >= 100 ? 0 : 1).format(v)}${s}`;
}
const fmtPct = (x) => (x == null ? "–" : `${nf(0).format(x * 100)}%`);
const dayFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const shortFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
const hourFmt = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
const isoDay = (d) => d.toISOString().slice(0, 10);
const farmName = (id) => (state.farms.find((f) => f.farm_id === id) || {}).farm_name || id;

function windowParams() {
  if (state.mode !== "day" || !state.days.length) return "";
  const d = state.days[state.day];
  const next = new Date(d.getTime() + 86400000);
  return `?start=${isoDay(d)}&end=${isoDay(next)}`;
}
function windowText() {
  if (state.mode !== "day" || !state.days.length) {
    if (!state.days.length) return "";
    return `${dayFmt.format(state.days[0])} – ${dayFmt.format(state.days[state.days.length - 1])}`;
  }
  return dayFmt.format(state.days[state.day]);
}

function legend(node, items) {
  clear(node);
  for (const it of items) {
    node.append(el("span", { class: "key" },
      el("span", { class: it.kind === "line" ? "line" : "swatch", style: { background: it.color } }), it.label));
  }
}

function hbars(node, rows, { color, selected, onClick, fmt }) {
  clear(node);
  const max = Math.max(...rows.map((r) => r.value || 0), 1e-9);
  for (const r of rows) {
    const tag = onClick ? "button" : "div";
    const row = el(tag, {
      class: "hbar" + (selected && selected === r.id ? " selected" : ""),
      type: onClick ? "button" : undefined, title: `${r.label}: ${fmt(r.value)}`,
      onclick: onClick ? () => onClick(r.id) : undefined,
    },
    el("span", { class: "name" }, r.label),
    el("span", { class: "track" }, el("span", { class: "fill", style: {
      display: "block", width: `${Math.max(0, (r.value || 0) / max) * 100}%`, background: r.color || color } })),
    el("span", { class: "val" }, fmt(r.value)));
    node.append(row);
  }
}

// Vertical crosshair that snaps to the hovered index (Chart.js plugin)
const crosshair = {
  id: "crosshair",
  afterDatasetsDraw(chart) {
    const a = chart.tooltip && chart.tooltip.getActiveElements();
    if (!a || !a.length) return;
    const x = a[0].element.x, { top, bottom } = chart.chartArea, ctx = chart.ctx;
    ctx.save(); ctx.strokeStyle = C.axis; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke(); ctx.restore();
  },
};

function lineChart(id, labels, datasets, { yLabel, tooltipLabel } = {}) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart($(id), {
    type: "line",
    data: { labels, datasets },
    plugins: [crosshair],
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      elements: { point: { radius: 0, hoverRadius: 4, hoverBorderWidth: 2, hoverBorderColor: C.surface },
                  line: { borderWidth: 2, borderCapStyle: "round", borderJoinStyle: "round", tension: 0 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: C.surface, titleColor: C.ink2, bodyColor: C.ink, borderColor: "rgba(11,11,11,0.1)",
          borderWidth: 1, padding: 8, boxWidth: 12, boxHeight: 2, usePointStyle: false,
          filter: (item) => !item.dataset.hideInTooltip,
          callbacks: { label: tooltipLabel || ((c) => ` ${fmtEnergy(c.parsed.y)}  ${c.dataset.label}`) },
        },
      },
      scales: {
        x: { grid: { display: false }, border: { color: C.axis },
             ticks: { color: C.muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 6, font: { size: 11 } } },
        y: { beginAtZero: true, grid: { color: C.grid }, border: { display: false },
             title: { display: !!yLabel, text: yLabel, color: C.muted, font: { size: 11 } },
             ticks: { color: C.muted, font: { size: 11 }, maxTicksLimit: 5,
                      callback: (v) => nf(0).format(v) } },
      },
    },
  });
}

// ---------- map ----------

let map;
const boundaryMarkers = [];

async function basemapStyle() {
  // Venue Wi-Fi fallback: if the tile style doesn't load in 5 s, draw farms on a plain background.
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 5000);
    const r = await fetch(BASEMAP, { signal: ctl.signal });
    clearTimeout(t);
    if (r.ok) return BASEMAP;
  } catch (_) { /* fall through */ }
  return { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#eef1f3" } }] };
}

async function initMap() {
  map = new maplibregl.Map({
    container: "map", style: await basemapStyle(), center: [-4.0, 57.2], zoom: 5.6,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  await new Promise((res) => (map.loaded() ? res() : map.once("load", res)));

  map.addSource("boundaries", { type: "geojson", data: boundaryGeoJSON() });
  map.addLayer({ id: "boundaries", type: "line", source: "boundaries",
    paint: { "line-color": ["interpolate", ["linear"], ["get", "share"], 0, C.axis, 0.5, C.critical],
             "line-width": ["interpolate", ["linear"], ["get", "share"], 0, 1.5, 0.5, 5],
             "line-dasharray": [2, 1.5] } });
  map.addSource("farms", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({ id: "farms", type: "circle", source: "farms",
    paint: { "circle-radius": 6, "circle-color": C.waste, "circle-opacity": 0.85,
             "circle-stroke-width": ["case", ["==", ["get", "farm_id"], ""], 3, 2],
             "circle-stroke-color": C.surface } });
  map.addLayer({ id: "farms-hit", type: "circle", source: "farms",   // bigger invisible hit target
    paint: { "circle-radius": ["+", 8, ["get", "r"]], "circle-opacity": 0 } });

  const tip = $("map-tip");
  map.on("mousemove", "farms-hit", (e) => {
    map.getCanvas().style.cursor = "pointer";
    const p = e.features[0].properties;
    clear(tip).append(el("strong", {}, p.name), el("span", { class: "sub" }, p.tip));
    tip.hidden = false;
    tip.style.left = `${e.originalEvent.clientX + 12}px`;
    tip.style.top = `${e.originalEvent.clientY + 12}px`;
  });
  map.on("mouseleave", "farms-hit", () => { map.getCanvas().style.cursor = ""; tip.hidden = true; });
  map.on("click", "farms-hit", (e) => selectFarm(e.features[0].properties.farm_id));
}

function boundaryGeoJSON() {
  const share = {};
  for (const b of (state.boundaries && state.boundaries.boundaries) || []) share[b.boundary] = b.share_periods_binding;
  return { type: "FeatureCollection", features: Object.entries(BOUNDARIES).map(([name, coords]) => ({
    type: "Feature", properties: { name, share: share[name] || 0 }, geometry: { type: "LineString", coordinates: coords } })) };
}

function drawBoundaryLabels() {
  boundaryMarkers.forEach((m) => m.remove());
  boundaryMarkers.length = 0;
  const share = {};
  for (const b of (state.boundaries && state.boundaries.boundaries) || []) share[b.boundary] = b.share_periods_binding;
  for (const [name, coords] of Object.entries(BOUNDARIES)) {
    const label = el("div", { class: "boundary-label" }, `${name} · full ${fmtPct(share[name])} of the time · approximate`);
    // label at the line's eastern end, out at sea, so it doesn't cover cities
    boundaryMarkers.push(new maplibregl.Marker({ element: label, anchor: "left", offset: [6, 0] })
      .setLngLat(coords[coords.length - 1]).addTo(map));
  }
}

function updateMap() {
  if (!map || !map.getSource("farms")) return;
  map.getSource("boundaries").setData(boundaryGeoJSON());
  drawBoundaryLabels();
  const predict = state.tab === "predict";
  const byFarm = {};
  if (predict) {
    for (const f of (state.tomorrow && state.tomorrow.farms) || [])
      byFarm[f.farm_id] = { value: f.hours.reduce((s, h) => s + (h.pred_mwh || 0), 0) };
  } else {
    for (const f of (state.reveal && state.reveal.farms) || []) byFarm[f.farm_id] = { value: f.curtailed_mwh, share: f.share_wasted };
  }
  const maxV = Math.max(1e-9, ...Object.values(byFarm).map((v) => v.value || 0));
  const R = 26;   // radius of the largest circle, px; area is proportional to the value
  const features = state.farms.map((f) => {
    const v = byFarm[f.farm_id] || { value: 0, share: 0 };
    const r = 3 + R * Math.sqrt((v.value || 0) / maxV);
    const tip = predict
      ? `Forecast tomorrow: ${fmtEnergy(v.value)}`
      : `Switched off: ${fmtEnergy(v.value)} (${fmtPct(v.share)} of planned output)`;
    return { type: "Feature", geometry: { type: "Point", coordinates: [f.lon, f.lat] },
      properties: { farm_id: f.farm_id, name: f.farm_name, value: v.value || 0, share: v.share || 0, r, tip } };
  });
  features.sort((a, b) => b.properties.r - a.properties.r);   // small circles drawn on top
  map.getSource("farms").setData({ type: "FeatureCollection", features });
  map.setPaintProperty("farms", "circle-radius", ["get", "r"]);
  map.setPaintProperty("farms", "circle-color", predict ? C.waste
    : ["interpolate", ["linear"], ["get", "share"], 0, C.rampZero, 0.1, "#f2a57a", 0.25, C.waste, 0.5, "#b8461a"]);
  map.setPaintProperty("farms", "circle-stroke-color",
    ["case", ["==", ["get", "farm_id"], state.farm || ""], C.ink, C.surface]);
  map.setPaintProperty("farms", "circle-stroke-width",
    ["case", ["==", ["get", "farm_id"], state.farm || ""], 2.5, 2]);
  drawMapLegend(predict, maxV);
}

function drawMapLegend(predict, maxV) {
  const node = clear($("map-legend"));
  node.append(el("div", {}, predict ? "Circle area: forecast waste tomorrow" : "Circle area: energy switched off"));
  const sizes = el("div", { class: "sizes" });
  for (const frac of [0.1, 0.5, 1]) {
    const d = 2 * (3 + 26 * Math.sqrt(frac));
    sizes.append(el("span", {}, el("span", { class: "dot", style: { width: `${d}px`, height: `${d}px` } }), fmtEnergy(maxV * frac)));
  }
  node.append(sizes);
  if (!predict) {
    node.append(el("div", { style: { marginTop: "8px" } }, "Colour: share of planned output switched off"),
      el("div", { class: "ramp" }), el("div", { class: "ramp-labels" }, el("span", {}, "0%"), el("span", {}, "25%"), el("span", {}, "50%+")));
  }
}

// ---------- Reveal ----------

async function loadReveal() {
  const tab = $("tab-reveal");
  tab.classList.add("loading");
  try {
    const q = windowParams();
    const [reveal, boundaries] = await Promise.all([api(`/api/reveal/summary${q}`), api(`/api/reveal/boundaries${q}`)]);
    state.reveal = reveal; state.boundaries = boundaries;
    renderReveal();
    updateMap();
    if (state.farm) await loadFarmDetail();
  } catch (e) { showError(e); } finally { tab.classList.remove("loading"); }
}

function renderReveal() {
  const t = state.reveal.totals;
  $("window-label").textContent = windowText();
  $("hero-value").textContent = fmtEnergy(t.curtailed_twh * 1e6);
  $("hero-sub").textContent = `${windowText()} · ${state.farms.length} farms tracked`;
  $("t-payments").textContent = fmtCompact(t.payments_gbp, "£");
  $("t-gas").textContent = fmtCompact(t.replacement_gas_gbp, "£");
  $("t-co2").textContent = `${fmtCompact(t.co2_t)} t`;
  $("t-homes").textContent = fmtCompact(t.homes_equiv);

  // B4 vs B6: tall segmented bar, legend names the parts
  const b4 = t.by_boundary_twh.B4 || 0, b6 = t.by_boundary_twh.B6 || 0, tot = b4 + b6;
  const bar = clear($("boundary-bar"));
  const parts = [["B4", "North–South Scotland", b4, C.planned], ["B6", "Scotland–England", b6, C.waste]];
  bar.setAttribute("aria-label", parts.map(([n, , v]) => `${n} ${fmtPct(tot ? v / tot : 0)}`).join(", "));
  for (const [name, desc, v, color] of parts) {
    if (!tot || v <= 0) continue;
    const share = v / tot;
    bar.append(el("div", { style: { flex: `${share} 1 0`, background: color },
      title: `${name} (${desc}): ${fmtEnergy(v * 1e6)}, ${fmtPct(share)}` }, share > 0.12 ? fmtPct(share) : ""));
  }
  legend($("boundary-legend"), parts.map(([n, d, v, c]) => ({ label: `${n} ${d} · ${fmtEnergy(v * 1e6)}`, color: c })));
  const sh = Object.fromEntries((state.boundaries.boundaries || []).map((b) => [b.boundary, b.share_periods_binding]));
  $("binding-note").textContent = `B4 was full in ${fmtPct(sh.B4)} of half hours, B6 in ${fmtPct(sh.B6)}. ` +
    "Waste in North Scotland while B4 was full counts as B4; everything else as B6.";

  hbars($("farm-bars"), state.reveal.farms.map((f) => ({ id: f.farm_id, label: farmName(f.farm_id), value: f.curtailed_mwh })),
    { color: C.waste, selected: state.farm, onClick: selectFarm, fmt: fmtEnergy });
}

function dailySums(series, keys) {
  const out = new Map();
  for (const p of series) {
    const d = p.period_start.slice(0, 10);
    if (!out.has(d)) out.set(d, Object.fromEntries(keys.map((k) => [k, null])));
    const row = out.get(d);
    for (const k of keys) if (p[k] != null) row[k] = (row[k] || 0) + p[k];
  }
  return out;
}

function pearson(xs, ys) {
  const pairs = xs.map((x, i) => [x, ys[i]]).filter(([x, y]) => x != null && y != null);
  if (pairs.length < 3) return null;
  const n = pairs.length, mx = pairs.reduce((s, p) => s + p[0], 0) / n, my = pairs.reduce((s, p) => s + p[1], 0) / n;
  let sxy = 0, sxx = 0, syy = 0;
  for (const [x, y] of pairs) { sxy += (x - mx) * (y - my); sxx += (x - mx) ** 2; syy += (y - my) ** 2; }
  return sxx && syy ? sxy / Math.sqrt(sxx * syy) : null;
}

async function loadFarmDetail() {
  const id = state.farm;
  const box = $("farm-detail");
  if (!id) { box.hidden = true; return; }
  box.hidden = false;
  $("farm-title").textContent = farmName(id);
  const res = await api(`/api/reveal/farm/${encodeURIComponent(id)}${windowParams()}`);
  if (state.farm !== id) return;   // user clicked elsewhere meanwhile
  let s = res.series;
  const keys = ["planned_mwh", "accepted_mwh", "metered_mwh", "curtailed_mwh_a", "curtailed_mwh_b"];
  let labels, unit;
  if (s.length > 1500) {        // long windows: daily totals keep the chart readable
    const d = dailySums(s, keys);
    labels = [...d.keys()].map((k) => dayFmt.format(new Date(k)));
    s = [...d.values()]; unit = "MWh per day";
  } else {
    labels = s.map((p) => shortFmt.format(new Date(p.period_start))); unit = "MWh per half hour";
  }
  const farm = state.farms.find((f) => f.farm_id === id) || {};
  const wasted = s.reduce((a, p) => a + (p.curtailed_mwh_a || 0), 0);
  const planned = s.reduce((a, p) => a + (p.planned_mwh || 0), 0);
  $("farm-sub").textContent = `${nf(0).format(farm.capacity_mw || 0)} MW · ${farm.site_type || ""} · ` +
    `${fmtEnergy(wasted)} switched off (${fmtPct(planned ? wasted / planned : 0)} of planned) · ${windowText()}`;
  legend($("farm-legend"), [
    { label: "Planned (what the farm told the grid)", color: C.planned, kind: "line" },
    { label: "Delivered (after the grid said stop)", color: C.neutral, kind: "line" },
    { label: "Wasted: the gap", color: C.wasteWash },
  ]);
  lineChart("farm-chart", labels, [
    { label: "planned", data: s.map((p) => p.planned_mwh), borderColor: C.planned },
    { label: "delivered", data: s.map((p) => p.accepted_mwh), borderColor: C.neutral,
      fill: { target: 0 }, backgroundColor: C.wasteWash },
  ], { yLabel: unit });

  // Method A vs B, daily
  const daily = dailySums(res.series, ["curtailed_mwh_a", "curtailed_mwh_b"]);
  const dl = [...daily.keys()];
  const a = dl.map((k) => daily.get(k).curtailed_mwh_a), b = dl.map((k) => daily.get(k).curtailed_mwh_b);
  const hasB = b.some((v) => v != null);
  legend($("ab-legend"), [
    { label: "Method A: market data (planned − accepted)", color: C.planned, kind: "line" },
    ...(hasB ? [{ label: "Method B: physics (wind × power curve − metered)", color: C.waste, kind: "line" }] : []),
  ]);
  lineChart("ab-chart", dl.map((k) => dayFmt.format(new Date(k))), [
    { label: "Method A", data: a, borderColor: C.planned, pointRadius: dl.length < 40 ? 3 : 0, pointBackgroundColor: C.planned },
    ...(hasB ? [{ label: "Method B", data: b, borderColor: C.waste, pointRadius: dl.length < 40 ? 3 : 0, pointBackgroundColor: C.waste }] : []),
  ], { yLabel: "MWh per day" });
  const r = hasB ? pearson(a, b) : null;
  $("ab-note").textContent = !hasB ? "Method B (the physics cross-check) isn't run for this farm."
    : r == null ? "Not enough days to compare the two methods."
    : `Daily correlation between the two methods: r = ${r.toFixed(2)} (we aim for above 0.6).`;
}

function selectFarm(id) {
  state.farm = state.farm === id ? null : id;
  updateMap();
  if (state.tab === "reveal") {
    renderReveal();
    loadFarmDetail().catch(showError);
    if (state.farm) $("farm-detail").scrollIntoView({ behavior: "smooth", block: "start" });
  } else {
    renderPredict();
  }
}

// ---------- window controls ----------

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  $("day-slider").disabled = mode !== "day";
  loadReveal();
}
function stopPlay() {
  if (state.playTimer) clearInterval(state.playTimer);
  state.playTimer = null;
  $("play").textContent = "▶"; $("play").setAttribute("aria-label", "Play through days");
}
function togglePlay() {
  if (state.playTimer) return stopPlay();
  if (state.mode !== "day") setMode("day");
  $("play").textContent = "❚❚"; $("play").setAttribute("aria-label", "Pause");
  state.playTimer = setInterval(() => {
    if (state.day >= state.days.length - 1) return stopPlay();
    state.day += 1; $("day-slider").value = state.day; loadReveal();
  }, 1400);
}

// ---------- Predict ----------

async function loadPredict() {
  const tab = $("tab-predict");
  tab.classList.add("loading");
  try {
    if (!state.tomorrow) [state.tomorrow, state.metrics] = await Promise.all([api("/api/forecast/tomorrow"), api("/api/forecast/metrics")]);
    renderPredict();
    updateMap();
  } catch (e) { showError(e); } finally { tab.classList.remove("loading"); }
}

function renderPredict() {
  const tm = state.tomorrow;
  const farms = tm.farms || [];
  const sel = state.farm && farms.find((f) => f.farm_id === state.farm);
  const scope = sel ? [sel] : farms;
  $("pred-scope").textContent = sel ? `Tomorrow, ${farmName(sel.farm_id)}` : "Tomorrow, all farms";

  // hourly totals across the scope (summing farm ranges overstates the joint range: labelled)
  const hours = (scope[0] && scope[0].hours.map((h) => h.hour_start)) || [];
  const sum = (key) => hours.map((_, i) => scope.reduce((s, f) => s + ((f.hours[i] && f.hours[i][key]) || 0), 0));
  const pred = sum("pred_mwh"), p10 = sum("p10_mwh"), p90 = sum("p90_mwh");
  const hasRange = scope.some((f) => f.hours.some((h) => h.p90_mwh != null));
  const total = pred.reduce((a, b) => a + b, 0);
  $("pred-value").textContent = farms.length ? fmtEnergy(total) : "No forecast";
  const issued = tm.issued_at ? new Date(tm.issued_at) : null;
  const stale = issued && Date.now() - issued.getTime() > 36 * 3600 * 1000;
  const sub = clear($("pred-sub"));
  if (hours.length) sub.append(`For ${dayFmt.format(new Date(hours[0]))} · issued ${shortFmt.format(issued)} UTC `);
  if (stale) sub.append(el("span", { class: "tag stale" }, "stale: older than 36 h"));
  if (hasRange && hours.length) sub.append(el("div", {},
    `Range ${fmtEnergy(p10.reduce((a, b) => a + b, 0))} – ${fmtEnergy(p90.reduce((a, b) => a + b, 0))}` +
    (sel ? " (10th–90th percentile)" : " (sum of each farm's 10th–90th percentile; approximate)")));

  legend($("pred-legend"), [{ label: "Forecast waste", color: C.waste, kind: "line" },
    ...(hasRange ? [{ label: "10–90% range", color: C.wasteWash }] : [])]);
  lineChart("tomorrow-chart", hours.map((h) => hourFmt.format(new Date(h))), [
    ...(hasRange ? [
      { label: "90th percentile", data: p90, borderColor: "transparent", pointRadius: 0, hideInTooltip: false },
      { label: "10th percentile", data: p10, borderColor: "transparent", fill: "-1", backgroundColor: C.wasteWash, pointRadius: 0 },
    ] : []),
    { label: "forecast", data: pred, borderColor: C.waste },
  ], { yLabel: "MWh per hour" });

  // per-farm table (also the accessible view of the map in this tab)
  const tbl = clear($("pred-table"));
  tbl.append(el("thead", {}, el("tr", {}, el("th", {}, "Farm"), el("th", { class: "num" }, "Forecast"), el("th", { class: "num" }, "Range"))));
  const body = el("tbody");
  const rows = farms.map((f) => ({ f, p: f.hours.reduce((s, h) => s + (h.pred_mwh || 0), 0),
    lo: f.hours.reduce((s, h) => s + (h.p10_mwh || 0), 0), hi: f.hours.reduce((s, h) => s + (h.p90_mwh || 0), 0) }))
    .sort((a, b) => b.p - a.p);
  for (const r of rows) {
    body.append(el("tr", { class: r.f.farm_id === state.farm ? "selected" : "", style: { cursor: "pointer" },
      onclick: () => selectFarm(r.f.farm_id) },
      el("td", {}, farmName(r.f.farm_id)), el("td", { class: "num" }, fmtEnergy(r.p)),
      el("td", { class: "num" }, hasRange ? `${fmtEnergy(r.lo)} – ${fmtEnergy(r.hi)}` : "–")));
  }
  tbl.append(body);

  // model vs baselines: ours highlighted, baselines neutral
  const m = state.metrics;
  const names = { lightgbm: "Our model", persistence: "Same hour, 2 days ago", climatology: "Usual for that hour", wind_rule: "Wind threshold rule" };
  const order = ["lightgbm", "persistence", "climatology", "wind_rule"].filter((k) => m.models[k]);
  hbars($("mae-bars"), order.map((k) => ({ id: k, label: names[k], value: m.models[k].mae_mwh,
    color: k === "lightgbm" ? C.waste : C.neutral })), { fmt: (v) => `${nf(1).format(v)} MWh` });
  const mt = clear($("metrics-table"));
  mt.append(el("thead", {}, el("tr", {}, el("th", {}, "Model"), el("th", { class: "num" }, "Error"),
    el("th", { class: "num" }, "Precision"), el("th", { class: "num" }, "Recall"), el("th", { class: "num" }, "Monthly total off by"))));
  const mb = el("tbody");
  for (const k of order) {
    const x = m.models[k];
    mb.append(el("tr", {}, el("td", {}, names[k]), el("td", { class: "num" }, `${nf(1).format(x.mae_mwh)} MWh`),
      el("td", { class: "num" }, fmtPct(x.precision)), el("td", { class: "num" }, fmtPct(x.recall)),
      el("td", { class: "num" }, `${nf(1).format(x.monthly_total_error_pct)}%`)));
  }
  mt.append(mb);
  $("metrics-note").textContent = `Precision and recall count "waste events" above ${m.event_threshold_mwh} MWh in a farm-hour. ` +
    `Validation: ${m.validation}. Model ${m.model_version}.`;

  hbars($("shap-bars"), (m.shap_top_signals || []).map((s) => ({ id: s.signal, label: s.signal.replace(/_/g, " "), value: s.mean_abs_shap })),
    { color: C.planned, fmt: (v) => nf(2).format(v) });

  loadBacktest().catch(showError);
}

async function loadBacktest() {
  const id = state.farm;
  const q = id ? `?farm_id=${encodeURIComponent(id)}` : "";
  const bt = await api(`/api/forecast/backtest${q}`);
  if (state.farm !== id) return;
  const s = bt.series;
  legend($("bt-legend"), [{ label: "What happened", color: C.planned, kind: "line" },
    { label: "Our forecast (made the day before)", color: C.waste, kind: "line" }]);
  lineChart("backtest-chart", s.map((p) => shortFmt.format(new Date(p.hour_start))), [
    { label: "actual", data: s.map((p) => p.actual_mwh), borderColor: C.planned },
    { label: "forecast", data: s.map((p) => p.pred_mwh), borderColor: C.waste },
  ], { yLabel: "MWh per hour" });
}

// ---------- tabs, intro, boot ----------

function setTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
  document.querySelectorAll(".tab").forEach((s) => (s.hidden = s.id !== `tab-${tab}`));
  if (tab !== "reveal") stopPlay();
  if (tab === "predict") loadPredict(); else if (tab === "reveal") { renderReveal(); updateMap(); loadFarmDetail().catch(showError); }
  else updateMap();
}

function initIntro() {
  const intro = $("intro");
  const steps = [...intro.querySelectorAll(".intro-step")];
  let i = 0;
  const close = () => { intro.hidden = true; document.removeEventListener("keydown", onKey); if (map) map.resize(); };
  const next = () => { if (i >= steps.length - 1) return close(); steps[i].hidden = true; i += 1; steps[i].hidden = false; };
  const onKey = (e) => { if (e.key === "Escape") close(); else if (e.key === "Enter" || e.key === " " || e.key === "ArrowRight") { e.preventDefault(); next(); } };
  document.addEventListener("keydown", onKey);
  intro.addEventListener("click", (e) => (e.target.id === "enter-map" ? close() : next()));
  if (new URLSearchParams(location.search).has("nointro")) close();
}

async function boot() {
  initIntro();
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => { stopPlay(); setMode(b.dataset.mode); }));
  $("play").addEventListener("click", togglePlay);
  $("day-slider").addEventListener("input", (e) => { stopPlay(); state.day = Number(e.target.value); if (state.mode !== "day") setMode("day"); else loadReveal(); });
  $("farm-clear").addEventListener("click", () => selectFarm(state.farm));
  $("day-slider").disabled = true;
  try {
    const [health, farms] = await Promise.all([api("/api/health"), api("/api/farms")]);
    state.farms = farms.farms;
    const start = new Date(health.range.start), end = new Date(health.range.end);
    for (let d = new Date(start); d < end; d = new Date(d.getTime() + 86400000)) state.days.push(d);
    $("day-slider").max = Math.max(0, state.days.length - 1);
    const fake = health.meta.source === "fixtures";
    $("fake-banner").hidden = !fake;
    const tag = $("source-tag");
    tag.textContent = fake ? "fake data" : `data ${health.meta.data_version}`;
    tag.className = "tag" + (fake ? " fake" : "");
    tag.title = `Generated ${health.meta.generated_at}`;
    // The panel must never wait on map tiles (venue Wi-Fi): load both, draw the map when ready.
    initMap().then(updateMap).catch((e) => console.warn("map failed to load", e));
    await loadReveal();
  } catch (e) { showError(e); }
}

boot();
