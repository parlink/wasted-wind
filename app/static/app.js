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
const fakeParts = new Set();   // which datasets on screen are still fixtures

function noteSource(part, meta) {
  if (!meta) return;
  if (meta.source === "fixtures") fakeParts.add(part); else fakeParts.delete(part);
  const banner = $("fake-banner"), tag = $("source-tag");
  banner.hidden = fakeParts.size === 0;
  banner.textContent = fakeParts.size
    ? `Fake data (app/fixtures) in: ${[...fakeParts].join(", ")}. Real numbers arrive with Person A's data releases.` : "";
  tag.textContent = fakeParts.size ? (fakeParts.has("wind farm waste") ? "fake data" : "partly fake data") : `data ${meta.data_version}`;
  tag.className = "tag" + (fakeParts.size ? " fake" : "");
}
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
const HOME_MWH_PER_YEAR = 2.7;   // average GB home (CONTRACT §7)
const homesFor = (twh) => (twh == null ? null : (twh * 1e6) / HOME_MWH_PER_YEAR);
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
                      callback: (v) => nf(Math.abs(v) >= 10 ? 0 : 2).format(v) } },
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
  map.on("click", (e) => { if (onMapClickPlace(e)) e.preventDefault(); });
  map.on("click", "farms-hit", (e) => { if (!state.placing) selectFarm(e.features[0].properties.farm_id); });
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
    const where = name === "B4" ? "pinch point inside Scotland" : "Scotland–England border";
    const text = share[name] == null ? `${name} · ${where} · approximate line`
      : `${name} · ${where} · full ${fmtPct(share[name])} of the time`;
    const label = el("div", { class: "boundary-label" }, text);
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
  const simulate = state.tab === "simulate" && state.simResult;
  const byFarm = {};
  if (simulate) {
    for (const f of state.simResult.farms) byFarm[f.farm_id] = { value: f.curtailed_mwh, base: f.baseline_curtailed_mwh };
  } else if (predict) {
    for (const f of (state.tomorrow && state.tomorrow.farms) || [])
      byFarm[f.farm_id] = { value: f.hours.reduce((s, h) => s + (h.pred_mwh || 0), 0) };
  } else {
    for (const f of (state.reveal && state.reveal.farms) || []) byFarm[f.farm_id] = { value: f.curtailed_mwh, share: f.share_wasted };
  }
  const maxV = Math.max(predict ? 50 : 1e-9, ...Object.values(byFarm).map((v) => v.value || 0));   // 50 MWh floor: tiny forecasts stay small
  const R = 26;   // radius of the largest circle, px; area is proportional to the value
  const features = state.farms.map((f) => {
    const v = byFarm[f.farm_id] || { value: 0, share: 0 };
    const r = 3 + R * Math.sqrt((v.value || 0) / maxV);
    const tip = simulate
      ? `Your scenario: ${fmtEnergy(v.value)} wasted (really: ${fmtEnergy(v.base)}) · rough`
      : predict
      ? `Expected to be wasted tomorrow: ${fmtEnergy(v.value)}`
      : `Wasted: ${fmtEnergy(v.value)} (${fmtPct(v.share)} of what it could have made)`;
    return { type: "Feature", geometry: { type: "Point", coordinates: [f.lon, f.lat] },
      properties: { farm_id: f.farm_id, name: f.farm_name, value: v.value || 0, share: v.share || 0, r, tip } };
  });
  features.sort((a, b) => b.properties.r - a.properties.r);   // small circles drawn on top
  map.getSource("farms").setData({ type: "FeatureCollection", features });
  map.setPaintProperty("farms", "circle-radius", ["get", "r"]);
  map.setPaintProperty("farms", "circle-color", predict || simulate ? C.waste
    : ["interpolate", ["linear"], ["get", "share"], 0, C.rampZero, 0.1, "#f2a57a", 0.25, C.waste, 0.5, "#b8461a"]);
  map.setPaintProperty("farms", "circle-stroke-color",
    ["case", ["==", ["get", "farm_id"], state.farm || ""], C.ink, C.surface]);
  map.setPaintProperty("farms", "circle-stroke-width",
    ["case", ["==", ["get", "farm_id"], state.farm || ""], 2.5, 2]);
  drawMapLegend(predict || simulate, maxV, simulate);
}

function drawMapLegend(predict, maxV, simulate = false) {
  const node = clear($("map-legend"));
  node.append(el("div", {}, simulate ? "Circle size: wind wasted in your scenario"
    : predict ? "Circle size: wind expected to be wasted tomorrow" : "Circle size: wind power wasted"));
  if (!(maxV > 0.5)) {
    node.append(el("div", { class: "sub" }, predict && !simulate ? "No waste forecast for any farm" : "No wind wasted in this period"));
  } else {
  const sizes = el("div", { class: "sizes" });
  for (const frac of [0.1, 0.5, 1]) {
    const d = 2 * (3 + 26 * Math.sqrt(frac));
    sizes.append(el("span", {}, el("span", { class: "dot", style: { width: `${d}px`, height: `${d}px` } }), fmtEnergy(maxV * frac)));
  }
  node.append(sizes);
  }
  if (!predict) {
    node.append(el("div", { style: { marginTop: "8px" } }, "Colour: share of the farm's possible output wasted"),
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
    noteSource("wind farm waste", reveal.meta);
    renderReveal();
    updateMap();
    if (state.farm) await loadFarmDetail();
  } catch (e) { showError(e); } finally { tab.classList.remove("loading"); }
}

function renderReveal() {
  const t = state.reveal.totals;
  $("window-label").textContent = windowText();
  $("hero-value").textContent = fmtEnergy(t.curtailed_twh * 1e6);
  // planned = what the farms told the grid they'd generate; switched off = the part the grid refused
  const planned = state.reveal.farms.reduce((s, f) => s + (f.potential_mwh || 0), 0);
  const cut = t.curtailed_twh * 1e6;
  $("hero-sub").textContent = `${windowText()} · ${state.farms.length} farms tracked · ` +
    (planned <= 0 ? "no output expected"
      : cut === 0 ? `none of the ${fmtEnergy(planned)} the farms could make was wasted: the cables had room`
      : `${fmtPct(cut / planned)} of the ${fmtEnergy(planned)} the farms could have made`);
  $("t-payments").textContent = fmtCompact(t.payments_gbp, "£");
  $("t-gas").textContent = fmtCompact(t.replacement_gas_gbp, "£");
  $("t-co2").textContent = `${fmtCompact(t.co2_t)} t`;
  $("t-homes").textContent = fmtCompact(homesFor(t.curtailed_twh));

  // B4 vs B6: tall segmented bar, legend names the parts
  const bar = clear($("boundary-bar"));
  if (!t.by_boundary_twh) {   // real curtailment but no real residual yet: don't guess
    clear($("boundary-legend"));
    $("binding-note").textContent = "The split between the two cables isn't available for this data yet.";
    hbars($("farm-bars"), state.reveal.farms.map((f) => ({ id: f.farm_id, label: farmName(f.farm_id), value: f.curtailed_mwh })),
      { color: C.waste, selected: state.farm, onClick: selectFarm, fmt: fmtEnergy });
    return;
  }
  const b4 = t.by_boundary_twh.B4 || 0, b6 = t.by_boundary_twh.B6 || 0, tot = b4 + b6;
  const parts = [["B4", "northern → southern Scotland", b4, C.planned], ["B6", "Scotland → England", b6, C.waste]];
  bar.setAttribute("aria-label", parts.map(([n, , v]) => `${n} ${fmtPct(tot ? v / tot : 0)}`).join(", "));
  for (const [name, desc, v, color] of parts) {
    if (!tot || v <= 0) continue;
    const share = v / tot;
    bar.append(el("div", { style: { flex: `${share} 1 0`, background: color },
      title: `${name} (${desc}): ${fmtEnergy(v * 1e6)}, ${fmtPct(share)}` }, share > 0.12 ? fmtPct(share) : ""));
  }
  legend($("boundary-legend"), parts.map(([n, d, v, c]) => ({ label: `${n} ${d} · ${fmtEnergy(v * 1e6)}`, color: c })));
  const sh = Object.fromEntries((state.boundaries.boundaries || []).map((b) => [b.boundary, b.share_periods_binding]));
  $("binding-note").textContent = `B4 was full ${fmtPct(sh.B4)} of the time, B6 ${fmtPct(sh.B6)}. ` +
    "Waste north of B4 while B4 was full is counted as B4; the rest as B6.";

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
    `${fmtEnergy(wasted)} wasted (${fmtPct(planned ? wasted / planned : 0)} of what it could have made) · ${windowText()}`;
  legend($("farm-legend"), [
    { label: "Could have made (its plan)", color: C.planned, kind: "line" },
    { label: "Allowed to make", color: C.neutral, kind: "line" },
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
    { label: "From grid switch-off orders", color: C.planned, kind: "line" },
    ...(hasB ? [{ label: "From weather (wind speed vs metered output)", color: C.waste, kind: "line" }] : []),
  ]);
  lineChart("ab-chart", dl.map((k) => dayFmt.format(new Date(k))), [
    { label: "Method A", data: a, borderColor: C.planned, pointRadius: dl.length < 40 ? 3 : 0, pointBackgroundColor: C.planned },
    ...(hasB ? [{ label: "Method B", data: b, borderColor: C.waste, pointRadius: dl.length < 40 ? 3 : 0, pointBackgroundColor: C.waste }] : []),
  ], { yLabel: "MWh per day" });
  const r = hasB ? pearson(a, b) : null;
  $("ab-note").textContent = !hasB ? "The weather cross-check isn't available for this farm."
    : r == null ? "Not enough days to compare the two methods."
    : `The two estimates move together day to day: correlation ${r.toFixed(2)} (1 = perfectly, above 0.6 is good).`;
}

function selectFarm(id) {
  state.farm = state.farm === id ? null : id;
  updateMap();
  if (state.tab === "reveal") {
    renderReveal();
    loadFarmDetail().catch(showError);
    if (state.farm) $("farm-detail").scrollIntoView({ behavior: "smooth", block: "start" });
  } else if (state.tab === "predict" && state.tomorrow) {
    renderPredict();
  }
}

// ---------- window controls ----------

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll("#tab-reveal .seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
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
    noteSource("forecast", state.tomorrow.meta);
    $("pred-fake").hidden = state.tomorrow.meta.source !== "fixtures";
    renderPredict();
    updateMap();
  } catch (e) { showError(e); } finally { tab.classList.remove("loading"); }
}

function renderPredict() {
  const tm = state.tomorrow;
  const farms = tm.farms || [];
  const sel = state.farm && farms.find((f) => f.farm_id === state.farm);
  const scope = sel ? [sel] : farms;
  $("pred-scope").textContent = sel ? `Wind expected to be wasted tomorrow at ${farmName(sel.farm_id)}` : "Wind expected to be wasted tomorrow, all farms";

  // hourly totals across the scope (summing farm ranges overstates the joint range: labelled)
  const hours = (scope[0] && scope[0].hours.map((h) => h.hour_start)) || [];
  const sum = (key) => hours.map((_, i) => scope.reduce((s, f) => s + ((f.hours[i] && f.hours[i][key]) || 0), 0));
  const pred = sum("pred_mwh"), p10 = sum("p10_mwh"), p90 = sum("p90_mwh");
  const hasRange = scope.some((f) => f.hours.some((h) => h.p90_mwh != null));
  const total = pred.reduce((a, b) => a + b, 0);
  $("pred-value").textContent = farms.length ? (total < 0.5 ? "≈ 0 MWh" : fmtEnergy(total)) : "No forecast";
  const issued = tm.issued_at ? new Date(tm.issued_at) : null;
  const stale = issued && Date.now() - issued.getTime() > 36 * 3600 * 1000;
  const sub = clear($("pred-sub"));
  if (hours.length) sub.append(`For ${dayFmt.format(new Date(hours[0]))} · issued ${shortFmt.format(issued)} UTC `);
  if (stale) sub.append(el("span", { class: "tag stale" }, "stale: older than 36 h"));
  if (farms.length && total < 0.5) sub.append(el("div", {}, "The model expects the cables to have room for this wind. " +
    "The range shows how much could still be wasted if it's wrong."));
  if (hasRange && hours.length) sub.append(el("div", {},
    `Likely range ${fmtEnergy(p10.reduce((a, b) => a + b, 0))} – ${fmtEnergy(p90.reduce((a, b) => a + b, 0))}` +
    (sel ? " (likely range, 80% of the time)" : " (adding up each farm's likely range, so approximate)")));

  legend($("pred-legend"), [{ label: "Expected waste", color: C.waste, kind: "line" },
    ...(hasRange ? [{ label: "Likely range (80% of the time)", color: C.wasteWash }] : [])]);
  lineChart("tomorrow-chart", hours.map((h) => hourFmt.format(new Date(h))), [
    ...(hasRange ? [
      { label: "90th percentile", data: p90, borderColor: "transparent", pointRadius: 0, hideInTooltip: false },
      { label: "10th percentile", data: p10, borderColor: "transparent", fill: "-1", backgroundColor: C.wasteWash, pointRadius: 0 },
    ] : []),
    { label: "forecast", data: pred, borderColor: C.waste },
  ], { yLabel: "MWh per hour" });

  // per-farm table (also the accessible view of the map in this tab)
  const tbl = clear($("pred-table"));
  tbl.append(el("thead", {}, el("tr", {}, el("th", {}, "Farm"), el("th", { class: "num" }, "Expected"), el("th", { class: "num" }, "Likely range"))));
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
  const names = { lightgbm: "Our model", persistence: "Same as 2 days ago", climatology: "Usual for that hour", wind_rule: "Simple windy-day rule" };
  const order = ["lightgbm", "persistence", "climatology", "wind_rule"].filter((k) => m.models[k]);
  hbars($("mae-bars"), order.map((k) => ({ id: k, label: names[k], value: m.models[k].mae_mwh,
    color: k === "lightgbm" ? C.waste : C.neutral })), { fmt: (v) => `${nf(1).format(v)} MWh` });
  const mt = clear($("metrics-table"));
  mt.append(el("thead", {}, el("tr", {}, el("th", {}, "Forecast"), el("th", { class: "num" }, "Avg miss"),
    el("th", { class: "num" }, "Warnings right"), el("th", { class: "num" }, "Events caught"), el("th", { class: "num" }, "Month total off by"))));
  const mb = el("tbody");
  for (const k of order) {
    const x = m.models[k];
    mb.append(el("tr", {}, el("td", {}, names[k]), el("td", { class: "num" }, `${nf(1).format(x.mae_mwh)} MWh`),
      el("td", { class: "num" }, fmtPct(x.precision)), el("td", { class: "num" }, fmtPct(x.recall)),
      el("td", { class: "num" }, `${nf(1).format(x.monthly_total_error_pct)}%`)));
  }
  mt.append(mb);
  $("metrics-note").textContent = `An "event" is an hour where a farm wastes over ${m.event_threshold_mwh} MWh. "Warnings right" = how often a predicted event really happened; ` +
    `"events caught" = how many real events it saw coming. Tested ${m.validation}. Model ${m.model_version}.`;

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
  legend($("bt-legend"), [{ label: "Really wasted", color: C.planned, kind: "line" },
    { label: "Our forecast, made the day before", color: C.waste, kind: "line" }]);
  lineChart("backtest-chart", s.map((p) => shortFmt.format(new Date(p.hour_start))), [
    { label: "actual", data: s.map((p) => p.actual_mwh), borderColor: C.planned },
    { label: "forecast", data: s.map((p) => p.pred_mwh), borderColor: C.waste },
  ], { yLabel: "MWh per hour" });
}

// ---------- Simulate ----------

const SIM_DEFAULT = () => ({
  b4: { mode: "historical", mw: 0 }, b6: { mode: "historical", mw: 0 },
  batteries: [], wind: 100, quality: "ours", preset: "all", start: "", end: "",
});
state.sim = SIM_DEFAULT();
state.simResult = null;
state.simPart1 = null;
state.placing = false;
let simTimer = null, simSeq = 0, simInit = false, batteryN = 0;
const batteryMarkers = [];
const RANGES = { offset: { min: -3000, max: 10000, step: 100, val: 0 }, fixed: { min: 0, max: 15000, step: 100, val: 3000 } };

function simWindow() {
  const s = state.sim;
  if (s.preset === "y2025") return { start: "2025-01-01", end: "2026-01-01" };
  if (s.preset === "last30" && state.days.length) {
    const end = new Date(state.days[state.days.length - 1].getTime() + 86400000);
    return { start: isoDay(new Date(end.getTime() - 30 * 86400000)), end: isoDay(end) };
  }
  if (s.preset === "custom") return { start: s.start || null, end: s.end || null };
  return { start: null, end: null };
}

function simRequest() {
  const s = state.sim;
  return {
    b4: { mode: s.b4.mode, mw: s.b4.mw }, b6: { mode: s.b6.mode, mw: s.b6.mw },
    batteries: s.batteries.map(({ battery_id, lat, lon, zone, power_mw, energy_mwh, efficiency }) =>
      ({ battery_id, lat, lon, zone, power_mw, energy_mwh, efficiency, dispatcher: "rule" })),
    weather: { wind_scale: s.wind / 100 }, window: simWindow(), forecast_quality: s.quality,
  };
}

function scheduleSim() {   // sliders wait 300 ms after the last move
  clearTimeout(simTimer);
  simTimer = setTimeout(runSim, 300);
}

async function runSim() {
  const seq = ++simSeq;
  const tab = $("tab-simulate");
  tab.classList.add("loading");
  try {
    const body = simRequest();
    const w = body.window;
    const q = w.start || w.end ? `?${new URLSearchParams(Object.entries(w).filter(([, v]) => v))}` : "";
    const [r, part1] = await Promise.all([
      fetch("/api/scenario", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        .then((x) => (x.ok ? x.json() : x.text().then((t) => { throw new Error(`/api/scenario: ${t.slice(0, 200)}`); }))),
      api(`/api/reveal/summary${q}`),
    ]);
    if (seq !== simSeq) return;          // a newer request is on its way
    state.simResult = r; state.simPart1 = part1;
    noteSource("simulator", r.meta);
    renderSim();
    updateMap();
  } catch (e) { showError(e); } finally { if (seq === simSeq) tab.classList.remove("loading"); }
}

function deltaText(node, d, base, fmt, lowerIsGood = true) {
  node.className = "delta";
  if (d == null || Math.abs(d) < 1e-12) { node.textContent = "same as reality"; return; }
  const pct = base ? ` (${d > 0 ? "+" : "−"}${nf(0).format(Math.abs(d / base) * 100)}%)` : "";
  node.textContent = `${d > 0 ? "+" : "−"}${fmt(Math.abs(d))} vs reality${pct}`;
  node.classList.add((d < 0) === lowerIsGood ? "good" : "bad");
}

function renderSim() {
  const r = state.simResult, s = r.scenario, b = r.baseline, d = r.delta;
  $("sim-value").textContent = fmtEnergy(s.curtailed_twh * 1e6);
  deltaText($("sim-delta"), d.curtailed_twh * 1e6, b.curtailed_twh * 1e6, fmtEnergy);
  const p1 = state.simPart1 && state.simPart1.totals.curtailed_twh;
  const sameData = state.simPart1 && state.simPart1.meta.source === r.meta.source;
  const win = r.window.start ? `${dayFmt.format(new Date(r.window.start))} – ${dayFmt.format(new Date(new Date(r.window.end) - 1))}` : "";
  $("sim-history").textContent = `${win} · What really happened: ${fmtEnergy(b.curtailed_twh * 1e6)} wasted` +
    (sameData && p1 ? ` (the Reveal tab says ${fmtEnergy(p1 * 1e6)}).` : ".");

  const tiles = [["payments", "payments_gbp", (v) => fmtCompact(v, "£")], ["gas", "replacement_gas_gbp", (v) => fmtCompact(v, "£")],
    ["co2", "co2_t", (v) => `${fmtCompact(v)} t`], ["homes", "homes_equiv", (v) => fmtCompact(v)]];
  for (const [id, key, fmt] of tiles) {
    if (key === "homes_equiv") {   // same energy-based count as Reveal
      $(`s-${id}`).textContent = fmt(homesFor(s.curtailed_twh));
      deltaText($(`s-${id}-d`), homesFor(d.curtailed_twh), homesFor(b.curtailed_twh), fmt);
      continue;
    }
    $(`s-${id}`).textContent = fmt(s[key]);
    deltaText($(`s-${id}-d`), d[key], b[key], fmt);
  }

  // B4 / B6: history bar vs scenario bar per boundary
  const bnode = clear($("sim-boundaries"));
  const max = Math.max(1e-12, ...["B4", "B6"].flatMap((k) => [s.by_boundary_twh[k], b.by_boundary_twh[k]]));
  bnode.append(el("div", { class: "legend" },
    el("span", { class: "key" }, el("span", { class: "swatch", style: { background: C.neutral } }), "What really happened"),
    el("span", { class: "key" }, el("span", { class: "swatch", style: { background: C.waste } }), "Your scenario")));
  for (const k of ["B4", "B6"]) {
    bnode.append(el("div", { class: "cmp-row", title: `${k}: history ${fmtEnergy(b.by_boundary_twh[k] * 1e6)}, scenario ${fmtEnergy(s.by_boundary_twh[k] * 1e6)}` },
      el("strong", {}, k),
      el("span", { class: "cmp-bars" },
        el("span", { style: { width: `${(b.by_boundary_twh[k] / max) * 100}%`, background: C.neutral } }),
        el("span", { style: { width: `${(s.by_boundary_twh[k] / max) * 100}%`, background: C.waste } })),
      el("span", { class: "val" }, fmtEnergy(s.by_boundary_twh[k] * 1e6))));
  }

  // batteries
  const bo = clear($("sim-batteries-out"));
  if (r.batteries.length) {
    const t = el("table", { class: "table" }, el("thead", {}, el("tr", {}, el("th", {}, "Battery"),
      el("th", { class: "num" }, "Wasted wind stored"), el("th", { class: "num" }, "Earned"), el("th", { class: "num" }, "Full cycles"))));
    const tb = el("tbody");
    for (const x of r.batteries) {
      tb.append(el("tr", {}, el("td", {}, x.battery_id), el("td", { class: "num" }, fmtEnergy(x.captured_curtailment_mwh)),
        el("td", { class: "num" }, fmtCompact(x.revenue_gbp, "£")), el("td", { class: "num" }, nf(1).format(x.cycles))));
    }
    t.append(tb);
    bo.append(el("div", { class: "table-wrap" }, t));
    const fb = Math.max(...r.batteries.map((x) => x.forecast_fallback_periods || 0));
    if (state.sim.quality === "ours" && fb) bo.append(el("p", { class: "note" },
      `${nf(0).format(fb)} half hours had no forecast (outside the backtest months), so the plan there had no waste information.`));
  }

  // farms (indicative)
  const ft = clear($("sim-farms"));
  ft.append(el("thead", {}, el("tr", {}, el("th", {}, "Farm"), el("th", { class: "num" }, "Really"),
    el("th", { class: "num" }, "Your scenario"), el("th", { class: "num" }, "Change"))));
  const fb = el("tbody");
  for (const f of r.farms.slice(0, 12)) {
    const ch = f.curtailed_mwh - f.baseline_curtailed_mwh;
    fb.append(el("tr", {}, el("td", {}, farmName(f.farm_id)), el("td", { class: "num" }, fmtEnergy(f.baseline_curtailed_mwh)),
      el("td", { class: "num" }, fmtEnergy(f.curtailed_mwh)),
      el("td", { class: "num" }, Math.abs(ch) < 0.5 ? "–" : `${ch > 0 ? "+" : "−"}${fmtEnergy(Math.abs(ch))}`)));
  }
  ft.append(fb);

  const notes = [`${r.cached ? "cached" : `computed in ${nf(0).format(r.elapsed_ms)} ms`}`];
  if (r.levers_not_yet_applied && r.levers_not_yet_applied.length) notes.push(`not applied yet: ${r.levers_not_yet_applied.join(", ")}`);
  $("sim-status").textContent = notes.join(" · ");
}

// --- lever controls ---

function syncBoundaryControl(name) {
  const box = document.querySelector(`.lever[data-boundary="${name}"]`);
  const lv = state.sim[name];
  box.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === lv.mode)));
  const input = box.querySelector("input"), out = box.querySelector("output");
  if (lv.mode === "historical") { input.disabled = true; out.textContent = "as it was"; return; }
  const r = RANGES[lv.mode];
  Object.assign(input, { disabled: false, min: r.min, max: r.max, step: r.step });
  input.value = lv.mw;
  out.textContent = lv.mode === "offset" ? `${lv.mw >= 0 ? "+" : "−"}${nf(0).format(Math.abs(lv.mw))} MW` : `${nf(0).format(lv.mw)} MW`;
}

function syncControls() {
  syncBoundaryControl("b4"); syncBoundaryControl("b6");
  $("wind-scale").value = state.sim.wind; $("wind-scale-out").textContent = `${state.sim.wind}%`;
  $("forecast-quality").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.q === state.sim.quality)));
  $("window-preset").value = state.sim.preset;
  $("window-custom").hidden = state.sim.preset !== "custom";
  renderBatteryList();
  drawBatteryMarkers();
}

function initSimControls() {
  for (const name of ["b4", "b6"]) {
    const box = document.querySelector(`.lever[data-boundary="${name}"]`);
    box.querySelectorAll(".seg button").forEach((btn) => btn.addEventListener("click", () => {
      const mode = btn.dataset.mode;
      state.sim[name] = { mode, mw: mode === "historical" ? 0 : RANGES[mode].val };
      syncBoundaryControl(name); scheduleSim();
    }));
    box.querySelector("input").addEventListener("input", (e) => {
      state.sim[name].mw = Number(e.target.value); syncBoundaryControl(name); scheduleSim();
    });
  }
  $("wind-scale").addEventListener("input", (e) => {
    state.sim.wind = Number(e.target.value); $("wind-scale-out").textContent = `${state.sim.wind}%`; scheduleSim();
  });
  $("forecast-quality").querySelectorAll("button").forEach((btn) => btn.addEventListener("click", () => {
    state.sim.quality = btn.dataset.q; syncControls(); scheduleSim();
  }));
  $("window-preset").addEventListener("change", (e) => {
    state.sim.preset = e.target.value; $("window-custom").hidden = state.sim.preset !== "custom";
    if (state.sim.preset === "custom" && state.days.length) {
      $("window-start").min = $("window-end").min = isoDay(state.days[0]);
      $("window-start").max = $("window-end").max = isoDay(new Date(state.days[state.days.length - 1].getTime() + 86400000));
    }
    scheduleSim();
  });
  for (const id of ["window-start", "window-end"]) $(id).addEventListener("change", () => {
    state.sim.start = $("window-start").value; state.sim.end = $("window-end").value; scheduleSim();
  });
  $("sim-reset").addEventListener("click", () => { state.sim = SIM_DEFAULT(); setPlacing(false); syncControls(); scheduleSim(); });
  $("place-battery").addEventListener("click", () => setPlacing(!state.placing));
}

// --- batteries on the map ---

function lineLatAt(coords, lon) {   // latitude of a boundary polyline at a longitude (clamped to its ends)
  if (lon <= coords[0][0]) return coords[0][1];
  for (let i = 1; i < coords.length; i++) {
    const [x0, y0] = coords[i - 1], [x1, y1] = coords[i];
    if (lon <= x1) return y0 + ((lon - x0) / (x1 - x0)) * (y1 - y0);
  }
  return coords[coords.length - 1][1];
}
function zoneAt(lon, lat) {
  if (lat > lineLatAt(BOUNDARIES.B4, lon)) return "north_scotland";
  if (lat > lineLatAt(BOUNDARIES.B6, lon)) return "south_scotland";
  return null;
}

function setPlacing(on) {
  state.placing = on;
  $("place-battery").setAttribute("aria-pressed", String(on));
  $("place-battery").textContent = on ? "Click the map… (click here to stop)" : "Place a battery on the map";
  $("map").classList.toggle("placing", on);
}

function onMapClickPlace(e) {
  if (!state.placing || state.tab !== "simulate") return false;
  const { lng, lat } = e.lngLat;
  const zone = zoneAt(lng, lat);
  if (!zone) { $("place-hint").textContent = "That's in England, outside the model. Click in Scotland."; return true; }
  batteryN += 1;
  state.sim.batteries.push({ battery_id: `battery_${batteryN}`, lat: +lat.toFixed(4), lon: +lng.toFixed(4), zone,
    power_mw: 200, energy_mwh: 400, efficiency: 0.88 });
  $("place-hint").textContent = `Added a battery ${zone === "north_scotland" ? "north of B4" : "between B4 and B6"}. Edit its size below.`;
  renderBatteryList(); drawBatteryMarkers(); scheduleSim();
  return true;
}

function renderBatteryList() {
  const list = clear($("battery-list"));
  for (const b of state.sim.batteries) {
    const field = (label, key, min, max, step) => el("label", {}, `${label} `,
      el("input", { type: "number", min, max, step, value: key === "efficiency" ? Math.round(b[key] * 100) : b[key],
        oninput: (e) => {
          const v = Number(e.target.value);
          if (!Number.isFinite(v) || v < min || v > max) return;
          b[key] = key === "efficiency" ? v / 100 : v; scheduleSim();
        } }));
    list.append(el("div", { class: "battery" },
      el("div", {}, el("strong", {}, b.battery_id), ` · ${b.zone === "north_scotland" ? "north of B4" : "between B4 and B6"}`),
      el("button", { type: "button", class: "remove", "aria-label": `Remove ${b.battery_id}`, onclick: () => {
        state.sim.batteries = state.sim.batteries.filter((x) => x !== b); renderBatteryList(); drawBatteryMarkers(); scheduleSim();
      } }, "×"),
      el("div", { class: "fields" }, field("MW", "power_mw", 1, 5000, 10), field("MWh", "energy_mwh", 1, 20000, 10),
        field("efficiency %", "efficiency", 80, 95, 1))));
  }
}

function drawBatteryMarkers() {
  batteryMarkers.forEach((m) => m.remove());
  batteryMarkers.length = 0;
  if (!map || state.tab !== "simulate") return;
  for (const b of state.sim.batteries) {
    const m = new maplibregl.Marker({ element: el("div", { class: "battery-marker", title: `${b.battery_id}: ${b.power_mw} MW / ${b.energy_mwh} MWh` }) })
      .setLngLat([b.lon, b.lat]).addTo(map);
    batteryMarkers.push(m);
  }
}

async function loadSimulate() {
  if (!simInit) { initSimControls(); simInit = true; syncControls(); }
  drawBatteryMarkers();
  if (!state.simResult) await runSim(); else { renderSim(); updateMap(); }
}

// ---------- Autopilot ----------

const AP_COLORS = { charge: C.planned, discharge: C.waste, hold: C.grid };
const STRATEGY_NAMES = { do_nothing: "No battery", reactive: "No forecast", forecast: "Our forecast", perfect: "Knows the future" };
state.ap = { scorecard: null, day: null, date: null, strategy: "forecast", planner: "rule", battery: null, step: -1, timer: null };
let apInit = false;

function apHasLpPlanner() {   // #13: the LP also plans reactive/forecast batteries
  return state.ap.scorecard.results.some((r) => r.strategy === "forecast" && r.dispatcher === "lp");
}

function apResults() {
  const sc = state.ap.scorecard;
  // each strategy under the chosen planner (rule rows where that planner has none, e.g. do nothing);
  // "perfect" is always the optimiser's true ceiling once it exists
  const pick = (strategy) => sc.results.find((r) => r.strategy === strategy && r.dispatcher === state.ap.planner)
    || sc.results.find((r) => r.strategy === strategy && r.dispatcher === "rule");
  const out = {};
  for (const k of ["do_nothing", "reactive", "forecast"]) { const r = pick(k); if (r) out[k] = r; }
  out.perfect = sc.results.find((r) => r.strategy === "perfect" && r.dispatcher === "lp")
    || sc.results.find((r) => r.strategy === "perfect");
  if (!out.perfect) delete out.perfect;
  return out;
}

function holdoutText(sc) {
  const h = sc.holdout && sc.holdout.capture_ratio;
  const p = state.ap.planner, f = h && h[`forecast/${p}`], r = h && h[`reactive/${p}`];
  if (f == null || r == null) return "";
  return `On months the planning rules were not tuned on (${sc.holdout.months.join(", ")}): ` +
    `${fmtPct(f)} vs ${fmtPct(r)}.`;
}

function apLpCeiling() {
  return state.ap.scorecard.results.some((r) => r.strategy === "perfect" && r.dispatcher === "lp");
}

function renderScorecard() {
  const sc = state.ap.scorecard, res = apResults();
  const f = res.forecast, r = res.reactive;
  $("ap-headline").textContent = f && r ? `${fmtPct(f.capture_ratio)} vs ${fmtPct(r.capture_ratio)}` : "–";
  const period = sc.period ? `${dayFmt.format(new Date(sc.period.start))} – ${dayFmt.format(new Date(new Date(sc.period.end) - 1))}` : "";
  $("ap-headline-sub").textContent = `Share of the best possible earnings a battery makes with our forecast vs with no forecast. ` +
    `${sc.batteries.length} batteries, ${period}. ` +
    (apLpCeiling() ? `"Best possible" = a battery that knows the future.`
      : `"Best possible" = the same rules, knowing what will happen that day.`) +
    (holdoutText(sc) ? ` ${holdoutText(sc)}` : "");
  const order = ["perfect", "forecast", "reactive", "do_nothing"].filter((k) => res[k]);
  hbars($("ap-ratio-bars"), order.map((k) => ({ id: k, label: STRATEGY_NAMES[k], value: res[k].capture_ratio,
    color: k === "forecast" ? C.planned : C.neutral })), { fmt: fmtPct });
  const t = clear($("ap-scorecard"));
  t.append(el("thead", {}, el("tr", {}, el("th", {}, "Battery plans with"), el("th", { class: "num" }, "Wasted wind stored"),
    el("th", { class: "num" }, "Earned"), el("th", { class: "num" }, "Switch-off payments saved"))));
  const tb = el("tbody");
  for (const k of order) {
    const x = res[k];
    tb.append(el("tr", {}, el("td", {}, STRATEGY_NAMES[k]), el("td", { class: "num" }, fmtEnergy(x.captured_curtailment_mwh)),
      el("td", { class: "num" }, fmtCompact(x.revenue_gbp, "£")), el("td", { class: "num" }, fmtCompact(x.payments_avoided_gbp, "£"))));
  }
  t.append(tb);
  $("ap-score-note").textContent = "Each battery can only store wind that was really wasted, and only sell when the cables south have room. Batteries tested: " +
    sc.batteries.map((b) => `${b.battery_id} (${nf(0).format(b.power_mw)} MW / ${nf(0).format(b.energy_mwh)} MWh)`).join(", ") + ".";
}

async function loadApDay() {
  stopApReplay();
  const tab = $("tab-autopilot");
  tab.classList.add("loading");
  try {
    const q = new URLSearchParams({ strategy: state.ap.strategy });
    if (state.ap.date) q.set("date", state.ap.date);
    if (state.ap.battery) q.set("battery_id", state.ap.battery);
    if (state.ap.strategy !== "perfect") q.set("dispatcher", state.ap.planner);
    const day = await api(`/api/autopilot/schedule?${q}`);
    state.ap.day = day;
    noteSource("battery schedules", day.meta);
    if (day.available) {
      $("ap-date").min = day.available.first; $("ap-date").max = day.available.last;
    }
    if (day.date) { state.ap.date = day.date; $("ap-date").value = day.date; }
    renderApDay();
  } catch (e) { showError(e); } finally { tab.classList.remove("loading"); }
}

function renderApDay() {
  const day = state.ap.day;
  const bat = day.batteries[0];
  const badge = $("ap-badge");
  const issued = day.plan_issued_at ? new Date(day.plan_issued_at) : null;
  const stale = day.live && issued && Date.now() - issued.getTime() > 36 * 3600 * 1000;
  badge.textContent = day.live ? (stale ? "live · stale" : "live") : "replay of the backtest";
  badge.className = "tag" + (day.live && !stale ? " live" : stale ? " stale" : "");
  $("ap-issued").textContent = issued ? `Plan made ${shortFmt.format(issued)} UTC, using only what was known at that time.` : "";
  legend($("ap-legend"), [{ label: "Storing wasted wind", color: AP_COLORS.charge },
    { label: "Selling (cables have room)", color: AP_COLORS.discharge }, { label: "Waiting", color: AP_COLORS.hold }]);

  const strip = clear($("ap-strip")), log = clear($("ap-log"));
  if (!bat) {
    $("ap-day-sum").textContent = "No schedule for this day.";
    if (charts["ap-soc-chart"]) { charts["ap-soc-chart"].destroy(); delete charts["ap-soc-chart"]; }
    return;
  }
  const steps = bat.steps;
  const caught = steps.reduce((s, x) => s + (x.captured_curtailment_mwh || 0), 0);
  const rev = steps.some((x) => x.revenue_gbp != null) ? steps.reduce((s, x) => s + (x.revenue_gbp || 0), 0) : null;
  const n = (a) => steps.filter((x) => x.action === a).length;
  $("ap-day-sum").textContent = `${bat.battery_id}: stored ${fmtEnergy(caught)} of wasted wind` + (rev == null ? "" : `, earned ${fmtCompact(rev, "£")}`) +
    ` · ${n("charge")} half hours storing, ${n("discharge")} selling, ${n("hold")} waiting`;
  strip.setAttribute("aria-label", `Actions through the day for ${bat.battery_id}`);
  steps.forEach((x, i) => {
    const time = hourFmt.format(new Date(x.period_start));
    strip.append(el("span", { style: { background: AP_COLORS[x.action] || C.grid }, title: `${time} · ${x.action}: ${x.reason}`,
      onmouseenter: () => highlightApStep(i, false) }));
    log.append(el("div", { class: "row", "data-i": i },
      el("span", { class: "t" }, time),
      el("span", { class: "a" }, el("i", { style: { background: AP_COLORS[x.action] || C.grid } }), x.action),
      el("span", {}, x.reason + (x.power_mw ? ` (${nf(0).format(x.power_mw)} MW)` : ""))));
  });
  const cap = (state.ap.scorecard.batteries.find((b) => b.battery_id === bat.battery_id) || {}).energy_mwh;
  lineChart("ap-soc-chart", steps.map((x) => hourFmt.format(new Date(x.period_start))), [
    { label: "stored", data: steps.map((x) => x.soc_mwh), borderColor: C.planned, fill: "origin", backgroundColor: "rgba(42,120,214,0.10)" },
  ], { yLabel: "MWh stored" });
  if (cap) { charts["ap-soc-chart"].options.scales.y.max = cap; charts["ap-soc-chart"].update(); }
}

function highlightApStep(i, scroll = true) {
  state.ap.step = i;
  document.querySelectorAll("#ap-strip span").forEach((s, j) => s.classList.toggle("now", j === i));
  document.querySelectorAll("#ap-log .row").forEach((r) => r.classList.toggle("now", Number(r.dataset.i) === i));
  const row = document.querySelector(`#ap-log .row[data-i="${i}"]`);
  if (row && scroll) { const log = $("ap-log"); log.scrollTop = row.offsetTop - log.offsetTop - 40; }
  const ch = charts["ap-soc-chart"];
  if (ch && i >= 0) {
    ch.setActiveElements([{ datasetIndex: 0, index: i }]);
    ch.tooltip.setActiveElements([{ datasetIndex: 0, index: i }], { x: 0, y: 0 });
    ch.update("none");
  }
}

function stopApReplay() {
  if (state.ap.timer) clearInterval(state.ap.timer);
  state.ap.timer = null;
  $("ap-play").textContent = "▶ Replay the day";
}

function toggleApReplay() {
  if (state.ap.timer) return stopApReplay();
  const steps = (state.ap.day && state.ap.day.batteries[0] && state.ap.day.batteries[0].steps) || [];
  if (!steps.length) return;
  let i = state.ap.step >= steps.length - 1 ? -1 : state.ap.step;
  $("ap-play").textContent = "❚❚ Pause";
  state.ap.timer = setInterval(() => {
    i += 1;
    if (i >= steps.length) return stopApReplay();
    highlightApStep(i);
  }, 350);
}

function shiftApDay(delta) {
  if (!state.ap.date) return;
  const d = new Date(`${state.ap.date}T00:00:00Z`);
  const next = isoDay(new Date(d.getTime() + delta * 86400000));
  const av = state.ap.day && state.ap.day.available;
  if (av && (next < av.first || next > av.last)) return;
  state.ap.date = next; loadApDay();
}

async function loadAutopilot() {
  try {
    if (!state.ap.scorecard) {
      state.ap.scorecard = await api("/api/autopilot/scorecard");
      noteSource("battery schedules", state.ap.scorecard.meta);
    }
    if (!apInit) {
      apInit = true;
      const sel = clear($("ap-battery"));
      for (const b of state.ap.scorecard.batteries) sel.append(el("option", { value: b.battery_id },
        `${b.battery_id} · ${b.zone === "north_scotland" ? "north of B4" : "between B4 and B6"} · ${nf(0).format(b.power_mw)} MW / ${nf(0).format(b.energy_mwh)} MWh`));
      state.ap.battery = (state.ap.scorecard.batteries[0] || {}).battery_id || null;
      sel.addEventListener("change", (e) => { state.ap.battery = e.target.value; loadApDay(); });
      $("ap-strategy").addEventListener("change", (e) => { state.ap.strategy = e.target.value; loadApDay(); });
      const planner = $("ap-planner");
      planner.hidden = !apHasLpPlanner();
      if (apHasLpPlanner()) state.ap.planner = "lp";
      planner.value = state.ap.planner;
      planner.addEventListener("change", (e) => { state.ap.planner = e.target.value; renderScorecard(); loadApDay(); });
      $("ap-date").addEventListener("change", (e) => { if (e.target.value) { state.ap.date = e.target.value; loadApDay(); } });
      $("ap-prev").addEventListener("click", () => shiftApDay(-1));
      $("ap-next").addEventListener("click", () => shiftApDay(1));
      $("ap-play").addEventListener("click", toggleApReplay);
    }
    renderScorecard();
    if (!state.ap.day) await loadApDay(); else renderApDay();
  } catch (e) { showError(e); }
}

// ---------- Method ----------

const MODEL_NAMES = { lightgbm: "Our model (LightGBM)", wind_rule: "Wind rule", climatology: "Climatology", persistence: "Persistence" };
let methodLoaded = false;

async function loadMethod() {
  if (methodLoaded) return;
  const tab = $("tab-method");
  tab.classList.add("loading");
  // each part is independent: one missing file must not blank the others
  const [fm, cal, sc] = await Promise.allSettled([api("/api/forecast/metrics"), api("/api/method/calibration"),
    api("/api/autopilot/scorecard")]);
  try {
    const ok = [fm, cal, sc].find((r) => r.status === "fulfilled");
    if (ok) $("method-version").textContent = `Data: ${ok.value.meta.data_version}.`;
    if (fm.status === "fulfilled") renderMethodForecast(fm.value);
    else $("method-validation").textContent = "Forecast metrics not available.";
    if (cal.status === "fulfilled") renderMethodCalibration(cal.value);
    else $("method-calib").textContent = "Simulator check not available.";
    if (sc.status === "fulfilled") renderMethodBatteries(sc.value);
    else $("method-basis").textContent = "Battery scorecard not available.";
    methodLoaded = true;
  } catch (e) { showError(e); } finally { tab.classList.remove("loading"); }
}

function renderMethodForecast(m) {
  noteSource("forecast metrics", m.meta);
  $("method-validation").textContent = `Tested ${m.validation || "walk-forward"}: each month is predicted by a model ` +
    "trained only on earlier months, using only what was known by 10:00 the day before.";
  $("method-event").textContent = fmtEnergy(m.event_threshold_mwh);
  const rows = Object.entries(m.models || {});
  const abl = m.ablations && m.ablations.lightgbm_without_neso_limits;
  if (abl) rows.splice(1, 0, ["ablation", abl]);
  const t = clear($("method-models"));
  t.append(el("thead", {}, el("tr", {}, el("th", {}, "Forecast"), el("th", { class: "num" }, "Error"),
    el("th", { class: "num" }, "Caught"), el("th", { class: "num" }, "Precision"), el("th", { class: "num" }, "Monthly total"))));
  const tb = el("tbody");
  for (const [k, v] of rows) {
    tb.append(el("tr", { class: k === "lightgbm" ? "ours" : "" },
      el("td", {}, k === "ablation" ? "Our model without NESO limits" : MODEL_NAMES[k] || k),
      el("td", { class: "num" }, fmtEnergy(v.mae_mwh)), el("td", { class: "num" }, fmtPct(v.recall)),
      el("td", { class: "num" }, fmtPct(v.precision)),
      el("td", { class: "num" }, v.monthly_total_error_pct == null ? "–" : `±${nf(0).format(v.monthly_total_error_pct)}%`)));
  }
  t.append(tb);
  const notes = clear($("method-forecast-notes"));
  const iv = m.interval;
  if (iv && iv.coverage_on_waste_hours != null) notes.append(el("li", {}, "Range (10th–90th percentile): should contain the " +
    `real value ${fmtPct(iv.nominal)} of the time. Overall it does ${fmtPct(iv.coverage_p10_p90)}, but on hours with real ` +
    `waste only ${fmtPct(iv.coverage_on_waste_hours)}; the misses are storms above the range.`));
  for (const n of m.notes || []) notes.append(el("li", {}, n));
}

function renderMethodCalibration(c) {
  noteSource("simulator", c.meta);
  if (c.simulated_twh == null) { $("method-calib").textContent = "Simulator not available."; return; }
  const errs = c.by_month.filter((r) => r.historical_mwh > 0)
    .map((r) => Math.abs(r.simulated_mwh - r.historical_mwh) / r.historical_mwh);
  $("method-calib").textContent = `With every lever at today's grid: ${fmtEnergy(c.simulated_twh * 1e6)} simulated vs ` +
    `${fmtEnergy(c.historical_twh * 1e6)} rebuilt from history, over ${c.by_month.length} months` +
    (errs.length ? `; the worst month is off by ${Math.max(...errs) < 1e-4 ? "less than 0.01" : nf(2).format(Math.max(...errs) * 100)}%.` : ".");
}

function renderMethodBatteries(sc) {
  noteSource("battery schedules", sc.meta);
  const lp = (sc.results || []).some((r) => r.strategy === "perfect" && r.dispatcher === "lp");
  $("method-basis").textContent = "Each battery is replayed half hour by half hour against what really happened: it can " +
    "only catch real waste and only sell when the cable south has room. Capture ratio = its revenue ÷ the revenue of " +
    (lp ? "the best schedule possible with perfect hindsight (a linear programme, a true ceiling)."
      : "the same rules given perfect knowledge of the day.") +
    (sc.holdout ? ` The planning rules were tuned on other months; ${sc.holdout.months.join(", ")} are held out.` : "");
  const ul = clear($("method-caveats"));
  for (const c of sc.caveats || []) ul.append(el("li", {}, c));
}

// ---------- tabs, intro, boot ----------

function setTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
  document.querySelectorAll(".tab").forEach((s) => (s.hidden = s.id !== `tab-${tab}`));
  if (tab !== "reveal") stopPlay();
  if (tab !== "simulate") setPlacing(false);
  if (tab !== "autopilot" && state.ap) stopApReplay();
  drawBatteryMarkers();
  if (tab === "predict") loadPredict(); else if (tab === "reveal") { renderReveal(); updateMap(); loadFarmDetail().catch(showError); }
  else if (tab === "simulate") loadSimulate();
  else if (tab === "autopilot") { loadAutopilot(); updateMap(); }
  else { if (tab === "method") loadMethod().catch(showError); updateMap(); }
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
  document.querySelectorAll("#tab-reveal .seg button").forEach((b) => b.addEventListener("click", () => { stopPlay(); setMode(b.dataset.mode); }));
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
    noteSource("wind farm waste", health.meta);
    $("source-tag").title = `Generated ${health.meta.generated_at}`;
    // The panel must never wait on map tiles (venue Wi-Fi): load both, draw the map when ready.
    initMap().then(updateMap).catch((e) => console.warn("map failed to load", e));
    await loadReveal();
  } catch (e) { showError(e); }
}

boot();
