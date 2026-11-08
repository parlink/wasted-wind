/* Windfall dashboard: map + Reveal and Predict tabs. Talks only to the API in docs/CONTRACT.md §7.
 * Data-derived text is inserted with textContent, never innerHTML. */
"use strict";

// The palette has ONE meaning per colour, on every tab:
//   raspberry (waste)  wind that was actually wasted: the map's circles on History, farm bars, the gap in a farm's chart,
//                      "Really wasted". Its lighter tone (wasteLight) is the second item of a pair of waste splits
//                      (B6 next to B4, Method B next to Method A).
//   teal (ours)        what WE produce: our forecast (incl. "Expected waste" on Forecast), our model, a Simulate scenario,
//                      our battery's actions (storing; selling in the darker oursDeep), the cable and batteries you place.
//   grey               baselines and what was planned or delivered (simple windy-day rule, "what really happened",
//                      a farm's plan), and waiting.
// So wasted wind is raspberry, and anything we predict or decide is teal. Change a colour here, not at its uses.
const C = {
  ours: "#0e9fbf", oursDeep: "#064a5c", oursWash: "rgba(14,159,191,0.16)",
  waste: "#c81e6a", wasteLight: "#e97aa8", wasteWash: "rgba(200,30,106,0.18)", neutral: "#898781",
  wind: "#4a5a6e",   // weather (the wind arrows): not ours, not waste
  grid: "#e1e0d9", axis: "#c3c2b7", ink: "#0b0b0b", ink2: "#52514e", muted: "#898781", critical: "#d03b3b",
  surface: "#fcfcfb", rampZero: "#b8b6ae",
};
const BASEMAP = "https://tiles.openfreemap.org/styles/positron";
const MAP_VIEWS = {
  light: BASEMAP,
  detailed: "https://tiles.openfreemap.org/styles/liberty",
  dark: "https://tiles.openfreemap.org/styles/dark",
  satellite: {   // Esri World Imagery; glyphs from OpenFreeMap so the wind arrows' labels still draw
    version: 8, glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
    sources: { sat: { type: "raster", tileSize: 256, maxzoom: 18,
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
      attribution: "Imagery © Esri, Maxar, Earthstar Geographics" } },
    layers: [{ id: "sat", type: "raster", source: "sat" }],
  },
};
// Approximate boundary lines (labelled "approximate" on the map)
const BOUNDARIES = {
  B4: [[-6.2, 56.05], [-5.2, 56.12], [-4.4, 56.15], [-3.6, 56.28], [-2.9, 56.42], [-2.3, 56.5], [-1.4, 56.5]],
  B6: [[-3.6, 54.92], [-3.05, 55.0], [-2.65, 55.15], [-2.25, 55.4], [-2.05, 55.62], [-1.6, 55.8]],
};

const state = {
  tab: "reveal", farm: null, mode: "all", range: null, days: [], playTimer: null,
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
  const upTo = state.days.length ? ` · to ${dayFmt.format(state.days[state.days.length - 1])}` : "";
  tag.textContent = fakeParts.size ? (fakeParts.has("wind farm waste") ? "fake data" : "partly fake data") : `Real grid data${upTo}`;
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
// Headline numbers count up from their old value to the new one (skipped for reduced motion).
const REDUCED_MOTION = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
function countUp(node, text) {
  const m = /-?\d[\d,]*(\.\d+)?/.exec(text);
  const prev = node._countTo;
  if (node._countRaf) cancelAnimationFrame(node._countRaf);
  if (!m || REDUCED_MOTION) { node.textContent = text; node._countTo = m ? parseFloat(m[0].replace(/,/g, "")) : null; return; }
  const to = parseFloat(m[0].replace(/,/g, "")), dec = m[1] ? m[1].length - 1 : 0;
  const from = prev != null && isFinite(prev) ? prev : 0;
  const unitChanged = node._countUnit !== text.slice(m.index + m[0].length).trim();
  node._countTo = to; node._countUnit = text.slice(m.index + m[0].length).trim();
  const start = unitChanged ? 0 : from, t0 = performance.now(), ms = 650;
  const fmt = new Intl.NumberFormat("en-GB", { minimumFractionDigits: dec, maximumFractionDigits: dec });
  const step = (t) => {
    const k = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - k, 3);
    node.textContent = text.slice(0, m.index) + fmt.format(start + (to - start) * e) + text.slice(m.index + m[0].length);
    if (k < 1) node._countRaf = requestAnimationFrame(step); else node.textContent = text;
  };
  node._countRaf = requestAnimationFrame(step);
}

function fmtEnergy(mwh) {
  if (mwh == null) return "–";
  const a = Math.abs(mwh);
  if (a >= 1e6) return `${nf(a >= 1e7 ? 1 : 2).format(mwh / 1e6)} TWh`;
  if (a < 0.05) return "0 MWh";
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
const pct1 = (x) => (x == null ? "–" : `${nf(1).format(x * 100)}%`);   // where a 1-point difference matters
const dayFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const shortFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
const hourFmt = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
const isoDay = (d) => d.toISOString().slice(0, 10);
const farmName = (id) => (state.farms.find((f) => f.farm_id === id) || {}).farm_name || id;

const monthFmt = new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
const DAY_MS = 86400000;

// Reveal's time window: null = everything, else [start, end) in whole UTC days.
// The All/Year/Month/Week/Day buttons give a default range; the date boxes can set any range.
// ["2026-08","2026-09","2026-10"] -> "Aug–Oct 2026" (or "Dec 2025 – Feb 2026" across a year)
function monthSpan(ms) {
  if (!ms || !ms.length) return "";
  const d = (m) => new Date(`${m}-01T00:00:00Z`), f = (m, y) => d(m).toLocaleString("en-GB", { month: "short", ...(y ? { year: "numeric" } : {}), timeZone: "UTC" });
  const a = ms[0], b = ms[ms.length - 1];
  if (a === b) return f(a, true);
  return a.slice(0, 4) === b.slice(0, 4) ? `${f(a)}–${f(b, true)}` : `${f(a, true)} – ${f(b, true)}`;
}
function dataStart() { return state.days[0]; }
function dataEnd() { return new Date(state.days[state.days.length - 1].getTime() + DAY_MS); }
function clampRange(start, end) {
  const s = new Date(Math.max(start.getTime(), dataStart().getTime()));
  const e = new Date(Math.min(end.getTime(), dataEnd().getTime()));
  return e > s ? { start: s, end: e } : null;
}
function defaultRange(mode, anchor) {
  if (mode === "all" || !state.days.length) return null;
  const a = new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth(), anchor.getUTCDate()));
  if (mode === "day") return clampRange(a, new Date(a.getTime() + DAY_MS));
  if (mode === "week") {   // 7-day blocks from the first day of data (a Monday)
    const k = Math.floor((a - dataStart()) / (7 * DAY_MS));
    const s = new Date(dataStart().getTime() + k * 7 * DAY_MS);
    return clampRange(s, new Date(s.getTime() + 7 * DAY_MS));
  }
  if (mode === "month") return clampRange(new Date(Date.UTC(a.getUTCFullYear(), a.getUTCMonth(), 1)),
    new Date(Date.UTC(a.getUTCFullYear(), a.getUTCMonth() + 1, 1)));
  return clampRange(new Date(Date.UTC(a.getUTCFullYear(), 0, 1)), new Date(Date.UTC(a.getUTCFullYear() + 1, 0, 1)));
}
// History's pickers: All (or a hand-typed range) shows From - To; Day one date picker; Month and Week the browser's
// month / week picker (a calendar of months; a calendar where clicking any day picks its week). Browsers without those
// (Firefox, Safari) get the date picker instead, and the day picked snaps to its month or week. Year: a short list.
// The app's weeks are 7-day blocks from the first day of data, 1 Jan 2024, a Monday that starts ISO week 1, so they
// are exactly the ISO weeks the week picker uses.
const NATIVE = (() => {
  const has = (type) => { const i = document.createElement("input"); i.setAttribute("type", type); return i.type === type; };
  return { month: has("month"), week: has("week") };
})();
function isoWeekOf(d) {   // a UTC date -> "2026-W44" (ISO 8601: weeks start on Monday; week 1 holds the first Thursday)
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  t.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7) + 3);   // the Thursday of its week decides the year
  const week = Math.floor((t - Date.UTC(t.getUTCFullYear(), 0, 1)) / DAY_MS / 7) + 1;
  return `${t.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}
function mondayOfIsoWeek(v) {   // "2026-W44" -> the Monday that starts it (UTC), or null
  const m = /^(\d{4})-W(\d{2})$/.exec(v || "");
  if (!m) return null;
  const jan4 = new Date(Date.UTC(+m[1], 0, 4));
  return new Date(jan4.getTime() - ((jan4.getUTCDay() + 6) % 7) * DAY_MS + (+m[2] - 1) * 7 * DAY_MS);
}
function periodList(mode, first, end) {   // the Year list: first = data start, end = data end (exclusive); newest first
  const out = [];
  for (let y = first.getUTCFullYear(); y <= new Date(end.getTime() - DAY_MS).getUTCFullYear(); y++) {
    out.push({ value: isoDay(new Date(Math.max(Date.UTC(y, 0, 1), first.getTime()))),
               label: `${y}${Date.UTC(y + 1, 0, 1) > end.getTime() ? " (so far)" : ""}` });
  }
  return out.reverse();
}
function inferMode(r) {   // a typed range that is exactly one day / week / month / year switches to that picker
  for (const m of ["day", "week", "month", "year"]) {
    const d = defaultRange(m, r.start);
    if (d && d.start.getTime() === r.start.getTime() && d.end.getTime() === r.end.getTime()) return m;
  }
  return "custom";
}
function shiftedRange(dir) {   // the next/previous period of the same length
  const r = state.range;
  if (!r) return null;
  if (state.mode === "month" || state.mode === "year") {
    const a = new Date(r.start);
    if (state.mode === "month") a.setUTCMonth(a.getUTCMonth() + dir, 1); else a.setUTCFullYear(a.getUTCFullYear() + dir, 0, 1);
    return a >= dataEnd() ? null : defaultRange(state.mode, new Date(Math.max(a.getTime(), dataStart().getTime())));
  }
  const len = state.mode === "day" ? DAY_MS : state.mode === "week" ? 7 * DAY_MS : r.end - r.start;
  const s = new Date(r.start.getTime() + dir * len);
  if (s >= dataEnd() || new Date(s.getTime() + len) <= dataStart()) return null;
  return clampRange(s, new Date(s.getTime() + len));
}
function windowParams() {
  const r = state.range;
  return r ? `?start=${isoDay(r.start)}&end=${isoDay(r.end)}` : "";
}
function windowText() {
  if (!state.days.length) return "";
  const r = state.range || { start: dataStart(), end: dataEnd() };
  const last = new Date(r.end.getTime() - DAY_MS);
  const whole = (unit) => r.start.getUTCDate() === 1 && r.end.getUTCDate() === 1 &&
    (unit === "month" ? r.start.getUTCMonth() !== r.end.getUTCMonth() || r.end - r.start < 32 * DAY_MS : r.start.getUTCMonth() === 0 && r.end.getUTCMonth() === 0);
  if (r.end - r.start === DAY_MS) return dayFmt.format(r.start);
  if (state.mode === "month" && whole("month") && r.end - r.start < 32 * DAY_MS) return monthFmt.format(r.start);
  if (state.mode === "year" && whole("year")) return String(r.start.getUTCFullYear());
  if (state.mode === "year" && r.end >= dataEnd() && r.start.getUTCMonth() === 0 && r.start.getUTCDate() === 1) {
    return `${r.start.getUTCFullYear()} so far (to ${dayFmt.format(last)})`;
  }
  return `${dayFmt.format(r.start)} – ${dayFmt.format(last)}`;
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

// Decimals for an axis whose largest label is maxAbs, so small values don't all read "0" (0.004 MWh -> 4 decimals)
function axisDecimals(maxAbs) {
  if (!(maxAbs > 0) || maxAbs >= 10) return 0;
  return Math.min(4, Math.max(1, Math.ceil(-Math.log10(maxAbs)) + 1));
}

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
                      callback: (v, i, ticks) => nf(axisDecimals(Math.max(...ticks.map((t) => Math.abs(t.value))))).format(v) } },
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
    if (r.ok) { document.body.dataset.mapView = "satellite"; return MAP_VIEWS.satellite; }   // opens on satellite
  } catch (_) { /* fall through */ }
  return { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#eef1f3" } }] };
}

function basemapIds(style) {
  return { sources: new Set(Object.keys(style.sources || {})), layers: new Set((style.layers || []).map((l) => l.id)) };
}
function setMapView(view) {
  const base = map._baseIds || { sources: new Set(), layers: new Set() };
  document.body.dataset.mapView = view;
  map.setStyle(MAP_VIEWS[view] || BASEMAP, {
    transformStyle: (prev, next) => {
      if (!prev) return next;
      const sources = Object.fromEntries(Object.entries(prev.sources).filter(([k]) => !base.sources.has(k)));
      const layers = prev.layers.filter((l) => !base.layers.has(l.id));
      map._baseIds = basemapIds(next);
      return { ...next, glyphs: next.glyphs || prev.glyphs, sources: { ...next.sources, ...sources }, layers: [...next.layers, ...layers] };
    },
  });
  // images (the wind arrow) go with the old style: add it back before the wind layer needs it
  map.once("styledata", () => { if (!map.hasImage("wind-arrow")) map.addImage("wind-arrow", windArrowImage(), { pixelRatio: 2 }); });
}

async function initMap() {
  map = new maplibregl.Map({
    container: "map", style: await basemapStyle(), center: [-4.0, 57.2], zoom: 5.6,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
  // remember which layers belong to the basemap, so a view change keeps our own (farms, cables, wind)
  map.once("style.load", () => { map._baseIds = basemapIds(map.getStyle()); });
  $("map-view").addEventListener("change", (e) => setMapView(e.target.value));
  // the panel floats over the right of the map: keep Scotland centred in the visible part
  const padMap = () => map.setPadding({ top: 0, bottom: 0, left: 0, right: window.innerWidth > 820 ? $("panel").offsetWidth + 24 : 0 });
  padMap(); window.addEventListener("resize", padMap);
  map.on("zoom", syncMarkerScale); syncMarkerScale();
  initWind();
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
    paint: { "circle-radius": zoomScaled(["+", 8, ["get", "r"]]), "circle-opacity": 0 } });

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

// ---------- turbines: each farm is a turbine on its circle; on Reveal the blades turn at the share of
// output the farm was allowed to make, so a farm that was mostly switched off barely moves ----------
// Two stacked SVGs of the same box: the static tower, and the rotor (blades + black hub) that spins. The rotor is
// its own element so the browser redraws it at its real on-screen size every time the marker grows; a spinning
// <g> inside one SVG was drawn at the starting size and then stretched, which made it blurry when zoomed in.
const BLADE = "M12 10 C10.2 7 10.3 3.2 12 0.3 C13.8 3.2 13.9 7 12 10 Z";
const TURBINE_SVG =
  '<svg class="tower-svg" viewBox="0 0 24 34" aria-hidden="true"><path class="tower" d="M10.7 10 L13.3 10 L15.4 32.6 L8.6 32.6 Z"/><rect class="tower" x="7" y="32" width="10" height="2" rx="1"/></svg>' +
  `<svg class="rotor" viewBox="0 0 24 34" aria-hidden="true"><g class="blades-fill"><path d="${BLADE}"/><path d="${BLADE}" transform="rotate(120 12 10)"/><path d="${BLADE}" transform="rotate(240 12 10)"/></g><circle class="hub" cx="12" cy="10" r="2.2"/></svg>`;
const turbineMarkers = new Map();
const MIN_CIRCLE_R = 2, MAX_CIRCLE_R = 10.8;   // px at the default zoom; the biggest circle is no taller than a turbine (16 x 22.4 px, style.css)

// Marker size as the map zooms. Zooming out shrinks them with the map. Zooming in from the default view they grow
// more slowly than the land does (so they get a little smaller relative to it): at SLOW_RATE (a bit under half the
// land's rate) until zoom 9, then at MID_RATE (three quarters of it) until they reach MAX_SCALE, after which they
// stop growing so they never swamp the screen. Raising MAX_SCALE or lowering MID_RATE keeps them growing for longer.
const BASE_ZOOM = 5.6, SLOW_UNTIL = 9, SLOW_RATE = 0.45, MID_RATE = 0.75, MAX_SCALE = 15;
const CAP_ZOOM = SLOW_UNTIL + (Math.log2(MAX_SCALE) - SLOW_RATE * (SLOW_UNTIL - BASE_ZOOM)) / MID_RATE;   // where growth stops
function markerScale(z) {
  if (z <= BASE_ZOOM) return Math.max(0.45, 2 ** (z - BASE_ZOOM));
  const grown = SLOW_RATE * (Math.min(z, SLOW_UNTIL) - BASE_ZOOM) + MID_RATE * Math.max(0, z - SLOW_UNTIL);
  return Math.min(MAX_SCALE, 2 ** grown);
}
function zoomScaled(radius) {   // MapLibre: zoom may only drive a top-level interpolate, so sample markerScale at stops
  const stops = [3, 4.5, BASE_ZOOM, 7, 8, SLOW_UNTIL, 10, 11, 12, CAP_ZOOM, 22]
    .sort((a, b) => a - b).flatMap((z) => [z, ["*", radius, markerScale(z)]]);
  return ["interpolate", ["linear"], ["zoom"], ...stops];
}
function syncMarkerScale() { if (map) map.getContainer().style.setProperty("--tscale", markerScale(map.getZoom()).toFixed(3)); }
function drawTurbines(byFarm, reveal) {
  for (const f of state.farms) {
    let m = turbineMarkers.get(f.farm_id);
    if (!m) {
      const node = el("div", { class: "turbine" });
      node.innerHTML = TURBINE_SVG;
      m = new maplibregl.Marker({ element: node, anchor: "bottom", offset: [0, 2] }).setLngLat([f.lon, f.lat]).addTo(map);
      turbineMarkers.set(f.farm_id, m);
    }
    const node = m.getElement();
    const running = reveal ? 1 - Math.min(1, Math.max(0, (byFarm[f.farm_id] || {}).share || 0)) : 0.6;
    node.classList.toggle("still", running < 0.05);
    node.style.setProperty("--spin", `${(1.4 / Math.max(running, 0.05)).toFixed(2)}s`);   // full output: one turn in 1.4 s
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
  const R = MAX_CIRCLE_R - MIN_CIRCLE_R;   // area is proportional to the value
  const features = state.farms.map((f) => {
    const v = byFarm[f.farm_id] || { value: 0, share: 0 };
    const r = MIN_CIRCLE_R + R * Math.sqrt((v.value || 0) / maxV);
    const tip = simulate
      ? `Your scenario: ${fmtEnergy(v.value)} wasted (really: ${fmtEnergy(v.base)}) · rough`
      : predict
      ? `Expected to be wasted tomorrow: ${fmtEnergy(v.value)}`
      : `Wasted: ${fmtEnergy(v.value)} (${fmtPct(v.share)} of what it could have made)`;
    return { type: "Feature", geometry: { type: "Point", coordinates: [f.lon, f.lat] },
      properties: { farm_id: f.farm_id, name: f.farm_name, value: v.value || 0, share: v.share || 0, r, tip } };
  });
  features.sort((a, b) => b.properties.r - a.properties.r);   // small circles drawn on top
  drawTurbines(byFarm, !predict && !simulate);
  map.getSource("farms").setData({ type: "FeatureCollection", features });
  map.setPaintProperty("farms", "circle-radius", zoomScaled(["get", "r"]));
  map.setPaintProperty("farms", "circle-color", predict || simulate ? C.ours
    : ["interpolate", ["linear"], ["get", "share"], 0, C.rampZero, 0.1, "#e58ab0", 0.25, C.waste, 0.5, "#8a0f45"]);
  map.setPaintProperty("farms", "circle-stroke-color",
    ["case", ["==", ["get", "farm_id"], state.farm || ""], C.ink, C.surface]);
  map.setPaintProperty("farms", "circle-stroke-width",
    ["case", ["==", ["get", "farm_id"], state.farm || ""], 2.5, 2]);
  drawMapLegend(predict || simulate, maxV, simulate);
}

function drawMapLegend(predict, maxV, simulate = false) {
  const node = clear($("map-legend"));
  node.classList.toggle("ours", !!predict);   // forecast and scenario circles are teal, History's are raspberry
  node.append(el("div", {}, simulate ? "Circle size: wind wasted in your scenario"
    : predict ? "Circle size: wind expected to be wasted tomorrow" : "Circle size: wind power wasted"),
    el("div", { class: "note", style: { margin: "0" } }, "Example sizes at the starting zoom"));
  if (!(maxV > 0.5)) {
    node.append(el("div", { class: "sub" }, predict && !simulate ? "No waste forecast for any farm" : "No wind wasted in this period"));
  } else {
  const sizes = el("div", { class: "sizes" });
  for (const frac of [0.1, 0.5, 1]) {
    const d = 2 * (MIN_CIRCLE_R + (MAX_CIRCLE_R - MIN_CIRCLE_R) * Math.sqrt(frac));
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
    if (!q) { state.revealAll = reveal; fillAbout(); }
    noteSource("wind farm waste", reveal.meta);
    renderReveal();
    updateMap();
    updateWind();
    if (state.farm) await loadFarmDetail();
  } catch (e) { showError(e); } finally { tab.classList.remove("loading"); }
}

function renderReveal() {
  const t = state.reveal.totals;
  countUp($("hero-value"), fmtEnergy(t.curtailed_twh * 1e6));
  // planned = what the farms told the grid they'd generate; switched off = the part the grid refused
  const planned = state.reveal.farms.reduce((s, f) => s + (f.potential_mwh || 0), 0);
  const cut = t.curtailed_twh * 1e6;
  $("hero-sub").textContent = `${windowText()} · ${state.farms.length} farms tracked · ` +
    (planned <= 0 ? "no output expected"
      : cut === 0 ? `none of the ${fmtEnergy(planned)} the farms could make was wasted: the cables had room`
      : `${fmtPct(cut / planned)} of the ${fmtEnergy(planned)} the farms could have made`);
  // farms on government price contracts can bid to PAY to switch off when power prices go negative,
  // so a window's net payment can be below zero: flip the label, never show "£-37k"
  const farmsPaid = t.payments_gbp < 0;
  $("t-payments").previousElementSibling.textContent = farmsPaid ? "Farms paid to switch off (net)" : "Paid to farms to switch off";
  addInfo("t-payments");
  countUp($("t-payments"), fmtCompact(Math.abs(t.payments_gbp), "£"));
  countUp($("t-gas"), fmtCompact(t.replacement_gas_gbp, "£"));
  countUp($("t-co2"), `${fmtCompact(t.co2_t)} t`);
  countUp($("t-homes"), fmtCompact(homesFor(t.curtailed_twh)));

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
  const parts = [["B4", "northern → southern Scotland", b4, C.waste], ["B6", "Scotland → England", b6, C.wasteLight, C.ink]];   // dark text on the light segment
  bar.setAttribute("aria-label", parts.map(([n, , v]) => `${n} ${fmtPct(tot ? v / tot : 0)}`).join(", "));
  for (const [name, desc, v, color, text] of parts) {
    if (!tot || v <= 0) continue;
    const share = v / tot;
    bar.append(el("div", { style: { flex: `${share} 1 0`, background: color, ...(text ? { color: text } : {}) },
      title: `${name} (${desc}): ${fmtEnergy(v * 1e6)}, ${fmtPct(share)}` }, share > 0.12 ? fmtPct(share) : ""));
  }
  legend($("boundary-legend"), parts.map(([n, d, v, c]) => ({ label: `${n} ${d} · ${fmtEnergy(v * 1e6)}`, color: c })));
  const sh = Object.fromEntries((state.boundaries.boundaries || []).map((b) => [b.boundary, b.share_periods_binding]));
  $("binding-note").textContent = `B4 was full ${fmtPct(sh.B4)} of the time, B6 ${fmtPct(sh.B6)}. ` +
    "Waste north of B4 while B4 was full is counted as B4; the rest as B6.";

  hbars($("farm-bars"), state.reveal.farms.map((f) => ({ id: f.farm_id, label: farmName(f.farm_id), value: f.curtailed_mwh })),
    { color: C.waste, selected: state.farm, onClick: selectFarm, fmt: fmtEnergy });
}

function monthlySums(daily, keys) {
  const out = new Map();
  for (const [day, row] of daily) {
    const m = day.slice(0, 7);
    if (!out.has(m)) out.set(m, Object.fromEntries(keys.map((k) => [k, null])));
    const b = out.get(m);
    for (const k of keys) if (row[k] != null) b[k] = (b[k] || 0) + row[k];
  }
  return out;
}

function weeklySums(daily, keys) {   // 7-day buckets from the first day
  const out = new Map();
  let i = 0, bucket = null;
  for (const [day, row] of daily) {
    if (i % 7 === 0) { bucket = day; out.set(bucket, Object.fromEntries(keys.map((k) => [k, null]))); }
    const b = out.get(bucket);
    for (const k of keys) if (row[k] != null) b[k] = (b[k] || 0) + row[k];
    i += 1;
  }
  return out;
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
  if (s.length > 48 * 400) {    // over ~13 months: monthly totals
    const d = monthlySums(dailySums(s, keys), keys);
    labels = [...d.keys()].map((k) => monthFmt.format(new Date(`${k}-01T00:00:00Z`)));
    s = [...d.values()]; unit = "MWh per month";
  } else if (s.length > 48 * 100) {    // over ~100 days: weekly totals (a year is 52 points)
    const d = weeklySums(dailySums(s, keys), keys);
    labels = [...d.keys()].map((k) => `w/c ${dayFmt.format(new Date(k))}`);
    s = [...d.values()]; unit = "MWh per week";
  } else if (s.length > 48 * 10) {   // over ~10 days: daily totals
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
    { label: "Could have made (its plan)", color: C.ink2, kind: "line" },
    { label: "Allowed to make", color: C.neutral, kind: "line" },
    { label: "Wasted: the gap", color: C.wasteWash },
  ]);
  lineChart("farm-chart", labels, [
    { label: "planned", data: s.map((p) => p.planned_mwh), borderColor: C.ink2 },
    { label: "delivered", data: s.map((p) => p.accepted_mwh), borderColor: C.neutral,
      fill: { target: 0 }, backgroundColor: C.wasteWash },
  ], { yLabel: unit });

  // Method A vs B, daily
  let daily = dailySums(res.series, ["curtailed_mwh_a", "curtailed_mwh_b"]);
  // long windows: coarser points, or the two lines turn into a smear
  const step = daily.size > 400 ? "month" : daily.size > 120 ? "week" : "day";
  if (step === "month") daily = monthlySums(daily, ["curtailed_mwh_a", "curtailed_mwh_b"]);
  if (step === "week") daily = weeklySums(daily, ["curtailed_mwh_a", "curtailed_mwh_b"]);
  const weekly = step !== "day";
  const dl = [...daily.keys()];
  const a = dl.map((k) => daily.get(k).curtailed_mwh_a), b = dl.map((k) => daily.get(k).curtailed_mwh_b);
  const hasB = b.some((v) => v != null);
  legend($("ab-legend"), [
    { label: "From grid switch-off orders", color: C.waste, kind: "line" },
    ...(hasB ? [{ label: "From weather (wind speed vs metered output)", color: C.wasteLight, kind: "line" }] : []),
  ]);
  lineChart("ab-chart", dl.map((k) => step === "month" ? monthFmt.format(new Date(`${k}-01T00:00:00Z`)) : dayFmt.format(new Date(k))), [
    { label: "Method A", data: a, borderColor: C.waste, pointRadius: dl.length < 40 ? 3 : 0, pointBackgroundColor: C.waste },
    ...(hasB ? [{ label: "Method B", data: b, borderColor: C.wasteLight, borderDash: [5, 4], pointRadius: dl.length < 40 ? 3 : 0, pointBackgroundColor: C.wasteLight }] : []),
  ], { yLabel: `MWh per ${step}` });
  const r = hasB ? pearson(a, b) : null;
  $("ab-note").textContent = !hasB ? "The weather cross-check isn't available for this farm."
    : r == null ? "Not enough days to compare the two methods."
    : `The two estimates move together ${step} to ${step}: correlation ${r.toFixed(2)} (1 = perfectly` +
      (step === "day" ? ", above 0.6 is good)." : "; weekly and monthly totals correlate more easily, so pick a month or less for the daily test).");
}

function selectFarm(id) {
  state.farm = state.farm === id ? null : id;
  updateMap();
  if (state.tab === "reveal") {
    renderReveal();
    loadFarmDetail().catch(showError);
    if (state.farm) $("farm-detail").scrollIntoView({ behavior: "smooth", block: "start" });
  } else if (state.tab === "predict" && state.tomorrow) {
    renderPredict(); if (tmInit && state.tm.date) renderTimeMachine();
    if ((state.farm || null) !== state.weekFarm) loadWeek().catch(showError);   // the strip follows the farm
  }
}

// ---------- window controls ----------

function syncDateInputs() {
  const r = state.range || (state.days.length ? { start: dataStart(), end: dataEnd() } : null);
  if (!r) return;
  for (const id of ["range-start", "range-end"]) {
    $(id).min = isoDay(dataStart()); $(id).max = isoDay(new Date(dataEnd().getTime() - DAY_MS));
  }
  $("range-start").value = isoDay(r.start);
  $("range-end").value = isoDay(new Date(r.end.getTime() - DAY_MS));   // shown inclusive
  document.querySelectorAll("#tab-reveal .seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === state.mode)));
  const fixed = state.mode !== "all";
  // Year: a short list. Month / Week: the browser's month / week picker, else the date picker snapping to the period.
  // Day: one date picker. All or a typed range: From - To.
  const m = state.mode, last = new Date(dataEnd().getTime() - DAY_MS);
  const show = m === "year" ? "year" : (m === "month" || m === "week") ? (NATIVE[m] ? m : "snap") : m === "day" ? "day" : "range";
  const sel = $("range-period");
  sel.hidden = show !== "year";
  if (show === "year") {
    if (!sel.options.length) for (const o of periodList("year", dataStart(), dataEnd())) sel.append(el("option", { value: o.value }, o.label));
    sel.value = isoDay(r.start);
  }
  const mon = $("range-month"), wk = $("range-week");
  mon.hidden = show !== "month"; wk.hidden = show !== "week";
  if (show === "month") { mon.min = isoDay(dataStart()).slice(0, 7); mon.max = isoDay(last).slice(0, 7); mon.value = isoDay(r.start).slice(0, 7); }
  if (show === "week") { wk.min = isoWeekOf(dataStart()); wk.max = isoWeekOf(last); wk.value = isoWeekOf(r.start); }
  $("range-start").hidden = !(show === "day" || show === "snap" || show === "range");
  $("range-start").setAttribute("aria-label", show === "snap" ? `Any day in the ${m}` : show === "range" ? "From" : "Day");
  const oneDay = r.end - r.start === DAY_MS;   // one day needs one date box, not "30 Dec to 30 Dec"
  $("range-end").hidden = show !== "range" || oneDay;
  document.querySelector("#tab-reveal .date-sep").hidden = show !== "range" || oneDay;
  $("range-prev").disabled = !fixed || !shiftedRange(-1);
  $("range-next").disabled = !fixed || !shiftedRange(1);
  $("play").disabled = !fixed;
}
function setMode(mode) {
  // keep looking at the same moment: the new range is the one containing the old one's LAST day, so
  // Year -> Month -> Week -> Day narrows onto the most recent part of what you were looking at
  const anchor = state.range ? new Date(state.range.end.getTime() - DAY_MS)
    : (state.days.length ? state.days[state.days.length - 1] : null);
  state.mode = mode;
  state.range = anchor ? defaultRange(mode, anchor) : null;
  // the data's last week / month is only a few days long: start on the latest complete one instead.
  // The current year is kept (most of it is in), shown as "so far".
  const full = { week: 7, month: 28 }[mode];
  if (full && state.range && state.range.end >= dataEnd() && (state.range.end - state.range.start) / DAY_MS < full) {
    state.range = shiftedRange(-1) || state.range;
  }
  syncDateInputs();
  loadReveal();
}
function setRangeFromInputs() {
  const a = $("range-start").value, b = $("range-end").hidden ? a : $("range-end").value;   // day: one box
  if (!a || !b) return;
  let s = new Date(`${a}T00:00:00Z`), e = new Date(`${b}T00:00:00Z`);
  if (e < s) [s, e] = [e, s];
  const r = clampRange(s, new Date(e.getTime() + DAY_MS));
  if (!r) return;
  state.range = r;
  state.mode = inferMode(r);   // exactly a day / week / month / year: that picker; anything else: ‹ › step by its length
  syncDateInputs();
  loadReveal();
}
function stepRange(dir) {
  const r = shiftedRange(dir);
  if (!r) return false;
  state.range = r; syncDateInputs(); loadReveal();
  return true;
}
function stopPlay() {
  if (state.playTimer) clearInterval(state.playTimer);
  state.playTimer = null;
  $("play").textContent = "▶"; $("play").setAttribute("aria-label", "Play through");
}
function togglePlay() {
  if (state.playTimer) return stopPlay();
  if (state.mode === "all") return;
  if (!shiftedRange(1)) {   // at the end: play from the start
    state.range = defaultRange(state.mode === "custom" ? "day" : state.mode, dataStart());
    if (state.mode === "custom") state.mode = "day";
    syncDateInputs(); loadReveal();
  }
  $("play").textContent = "❚❚"; $("play").setAttribute("aria-label", "Pause");
  state.playTimer = setInterval(() => { if (!stepRange(1)) stopPlay(); }, 1400);
}

// ---------- Predict ----------

// #49: forecast error by days ahead (pipeline.lead_skill); hidden until the file is built
function renderLeadSkill(rows) {
  $("lead-block").hidden = !(rows && rows.length);
  if (!rows || !rows.length) return;
  const days = (k) => `${k} day${k === 1 ? "" : "s"}`;
  legend($("lead-legend"), [{ label: "Our model", color: C.ours, kind: "line" },
    { label: "Simple windy-day rule", color: C.neutral, kind: "line" },
    { label: "Usual for that hour", color: C.rampZero, kind: "line" }]);
  lineChart("lead-chart", rows.map((r) => `${days(r.lead_days)} ahead`), [
    { label: "Our model", data: rows.map((r) => r.mae_mwh), borderColor: C.ours, pointRadius: 3, pointBackgroundColor: C.ours },
    { label: "Simple windy-day rule", data: rows.map((r) => r.wind_rule_mae_mwh), borderColor: C.neutral, borderDash: [5, 4], pointRadius: 3, pointBackgroundColor: C.neutral },
    { label: "Usual for that hour", data: rows.map((r) => r.baseline_mae_mwh), borderColor: C.rampZero, borderDash: [2, 3], pointRadius: 0 },
  ], { yLabel: "Avg miss, MWh per farm-hour", tooltipLabel: (c) => ` ${nf(1).format(c.parsed.y)} MWh  ${c.dataset.label}` });
  const first = rows[0], last = rows[rows.length - 1];
  const worse = rows.find((r) => r.wind_rule_mae_mwh < r.mae_mwh);
  $("lead-note").textContent = `The day before, the model misses ${nf(1).format(first.mae_mwh)} MWh per farm-hour on average and ` +
    `catches ${fmtPct(first.recall)} of waste events; ${days(last.lead_days)} ahead, ${nf(1).format(last.mae_mwh)} MWh and ${fmtPct(last.recall)}. ` +
    (worse ? `From ${days(worse.lead_days)} ahead the simple rule misses less hour by hour, though the model still catches more of the ` +
      `big events (${fmtPct(worse.recall)}), so treat those days as an outlook. ` : "") +
    `Beyond the day before there are no grid limits yet (published the day before), and ${days(last.lead_days)} is as far as ` +
    `the weather archive keeps old forecasts.`;
}

// The strip shows the same scope as the big number below it: all farms, or the farm selected on the map / list.
async function loadWeek() {
  const farm = state.farm || null;
  state.week = await api(`/api/forecast/week${farm ? `?farm_id=${encodeURIComponent(farm)}` : ""}`).catch(() => null);
  state.weekFarm = farm;
  renderWeek();
}

// #49: the next 6 days. Day 1 (tomorrow) is the day-ahead forecast every other number on the tab is about.
function renderWeek() {
  const w = state.week, strip = clear($("week-strip")), scope = clear($("week-scope"));
  const days = (w && w.days) || [];
  strip.hidden = $("week-note").hidden = scope.hidden = days.length < 2;
  if (days.length < 2) return;
  if (w.farm_id) scope.append(`Next 6 days at ${farmName(w.farm_id)}`,
    el("button", { type: "button", class: "link-btn", onclick: () => selectFarm(w.farm_id) }, "Show all farms"));
  else scope.append("Next 6 days, all farms");
  const lead = (state.tomorrow && state.tomorrow.lead_days) || 1;
  const max = Math.max(...days.map((d) => d.pred_mwh), 1e-9);
  const wd = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", timeZone: "UTC" });   // "Tue 10": one line
  for (const d of days) {
    const sel = d.lead_days === lead;
    strip.append(el("button", { type: "button", role: "tab", class: `week-day ${d.kind}`, "aria-selected": String(sel),
      title: `${dayFmt.format(new Date(d.date))}. ${d.kind === "outlook" ? "Outlook" : "Forecast"} made ${d.lead_days} day${d.lead_days > 1 ? "s" : ""} ahead.` +
        (d.p90_mwh != null ? ` Likely range (farm ranges added up): ${fmtEnergy(d.p10_mwh)} – ${fmtEnergy(d.p90_mwh)}` : " No range: only the day-ahead's range is calibrated."),
      onclick: () => selectLead(d.lead_days) },
      el("span", { class: "wd-date" }, d.lead_days === 1 ? `Tomorrow · ${wd.format(new Date(d.date))}` : wd.format(new Date(d.date))),
      el("span", { class: "wd-val" }, d.pred_mwh < 0.5 ? "≈ 0 MWh" : fmtEnergy(d.pred_mwh)),
      el("span", { class: "wd-bar", style: { width: `${Math.max(2, d.pred_mwh / max * 100)}%` } })));
  }
  const ol = w.outlook_from_lead;
  $("week-note").textContent = ol ? `Days ${ol}–${days.length} (dashed) are an outlook: in the backtest, from ${ol} days ahead a ` +
    `simple windy-day rule missed less hour by hour (see "How far ahead can we see?" below). Grid limits are only published ` +
    `the day before, so days 2+ forecast without them.` : "Grid limits are only published the day before, so days 2+ forecast without them.";
}

async function selectLead(k) {
  try {
    state.tomorrow = await api(`/api/forecast/tomorrow?lead_days=${k}`);
    renderWeek(); renderPredict(); updateMap();
  } catch (e) { showError(e); }
}

async function loadPredict() {
  const tab = $("tab-predict");
  tab.classList.add("loading");
  try {
    if (!state.tomorrow) [state.tomorrow, state.metrics] = await Promise.all([api("/api/forecast/tomorrow"), api("/api/forecast/metrics")]);
    await loadWeek();
    noteSource("forecast", state.tomorrow.meta);
    $("pred-fake").hidden = state.tomorrow.meta.source !== "fixtures";
    renderPredict();
    updateMap();
    updateWind();
    loadTimeMachine().catch(showError);
  } catch (e) { showError(e); } finally { tab.classList.remove("loading"); }
}

function renderPredict() {
  const tm = state.tomorrow;
  const farms = tm.farms || [];
  const sel = state.farm && farms.find((f) => f.farm_id === state.farm);
  const scope = sel ? [sel] : farms;
  const lead = tm.lead_days || 1;
  const when = lead === 1 ? "tomorrow" : `on ${dayFmt.format(new Date((farms[0] && farms[0].hours[0] && farms[0].hours[0].hour_start) || Date.now()))}`;
  $("pred-scope").textContent = sel ? `Wind expected to be wasted ${when} at ${farmName(sel.farm_id)}` : `Wind expected to be wasted ${when}, all farms`;
  $("pred-table-title").textContent = `${when === "tomorrow" ? "Tomorrow" : when.replace(/^on /, "")}, farm by farm`;   // the table is for the chosen day

  // hourly totals across the scope (summing farm ranges overstates the joint range: labelled)
  const hours = (scope[0] && scope[0].hours.map((h) => h.hour_start)) || [];
  const sum = (key) => hours.map((_, i) => scope.reduce((s, f) => s + ((f.hours[i] && f.hours[i][key]) || 0), 0));
  const pred = sum("pred_mwh"), p10 = sum("p10_mwh"), p90 = sum("p90_mwh");
  const hasRange = scope.some((f) => f.hours.some((h) => h.p90_mwh != null));
  const total = pred.reduce((a, b) => a + b, 0);
  countUp($("pred-value"), farms.length ? (total < 0.5 ? "≈ 0 MWh" : fmtEnergy(total)) : "No forecast");
  const issued = tm.issued_at ? new Date(tm.issued_at) : null;
  const stale = issued && Date.now() - issued.getTime() > 36 * 3600 * 1000;
  const sub = clear($("pred-sub"));
  if (hours.length) sub.append(`For ${dayFmt.format(new Date(hours[0]))} · issued ${shortFmt.format(issued)} UTC `);
  if (stale) sub.append(el("span", { class: "tag stale" }, "stale: older than 36 h"));
  if (farms.length && total < 0.5) sub.append(el("div", {}, "The model expects the cables to have room for this wind. " +
    "The range shows how much could still be wasted if it's wrong. ",
    el("button", { type: "button", class: "link-btn", onclick: () => {
      $("tm").scrollIntoView({ behavior: "smooth", block: "start" }); $("tm-storm").click();
    } }, "See how it did on the biggest storm →")));
  if (hasRange && hours.length) sub.append(el("div", {},
    `Likely range ${fmtEnergy(p10.reduce((a, b) => a + b, 0))} – ${fmtEnergy(p90.reduce((a, b) => a + b, 0))}` +
    (sel ? " (likely range, 80% of the time)" : " (adding up each farm's likely range, so approximate)")));

  legend($("pred-legend"), [{ label: "Expected waste", color: C.ours, kind: "line" },
    ...(hasRange ? [{ label: "Likely range (80% of the time)", color: C.oursWash }] : [])]);
  lineChart("tomorrow-chart", hours.map((h) => hourFmt.format(new Date(h))), [
    ...(hasRange ? [
      { label: "90th percentile", data: p90, borderColor: "transparent", pointRadius: 0, hideInTooltip: false },
      { label: "10th percentile", data: p10, borderColor: "transparent", fill: "-1", backgroundColor: C.oursWash, pointRadius: 0 },
    ] : []),
    { label: "forecast", data: pred, borderColor: C.ours },
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
    color: k === "lightgbm" ? C.ours : C.neutral })), { fmt: (v) => `${nf(1).format(v)} MWh` });
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

  renderLeadSkill(m.skill_by_lead);

  hbars($("shap-bars"), (m.shap_top_signals || []).map((s) => ({ id: s.signal, label: SIGNAL_NAMES[s.signal] || s.signal.replace(/_/g, " "), value: s.mean_abs_shap })),
    { color: C.ours, fmt: (v) => nf(2).format(v) });

  loadBacktest().catch(showError);
}

async function loadBacktest() {
  const id = state.farm;
  const q = id ? `?farm_id=${encodeURIComponent(id)}` : "";
  const bt = await api(`/api/forecast/backtest${q}`);
  if (state.farm !== id) return;
  // six months of hours is a smear: show daily totals (dailySums keys on period_start)
  const daily = dailySums(bt.series.map((p) => ({ period_start: p.hour_start, actual_mwh: p.actual_mwh, pred_mwh: p.pred_mwh })),
    ["actual_mwh", "pred_mwh"]);
  const days = [...daily.keys()], s = [...daily.values()];
  legend($("bt-legend"), [{ label: "Really wasted", color: C.waste, kind: "line" },
    { label: "Our forecast, made the day before", color: C.ours, kind: "line" }]);
  const dm = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  lineChart("backtest-chart", days.map((k) => dm.format(new Date(k))), [
    { label: "actual", data: s.map((p) => p.actual_mwh), borderColor: C.waste },
    { label: "forecast", data: s.map((p) => p.pred_mwh), borderColor: C.ours },
  ], { yLabel: "MWh per day" });
}

// ---------- Look back: the forecast as issued, what happened, and the batteries, for one past day ----------

state.tm = { date: null, range: null, storm: null };
let tmInit = false;

async function loadTimeMachine() {
  if (!tmInit) {
    tmInit = true;
    $("tm-date").addEventListener("change", (e) => { if (e.target.value) { state.tm.date = e.target.value; renderTimeMachine(); } });
    $("tm-prev").addEventListener("click", () => shiftTm(-1));
    $("tm-next").addEventListener("click", () => shiftTm(1));
    $("tm-storm").addEventListener("click", () => { if (state.tm.storm) { state.tm.date = state.tm.storm; renderTimeMachine(); } });
    // the days with an honest day-ahead forecast = the backtest; find them and the biggest day once
    $("tm").classList.add("loading");   // marks the card busy while the first (slow) load runs
    let bt;
    try { bt = await api("/api/forecast/backtest"); } finally { $("tm").classList.remove("loading"); }
    const daily = dailySums(bt.series.map((p) => ({ period_start: p.hour_start, actual_mwh: p.actual_mwh })), ["actual_mwh"]);
    const days = [...daily.keys()].sort();
    if (!days.length) { $("tm-note").textContent = "No backtest forecasts in this data."; return; }
    state.tm.range = { first: days[0], last: days[days.length - 1] };
    state.tm.storm = days.reduce((a, d) => ((daily.get(d).actual_mwh || 0) > (daily.get(a).actual_mwh || 0) ? d : a), days[0]);
    // a date picked while this was loading (the tour sets one straight away) wins over the storm
    const picked = state.tm.date && state.tm.date >= state.tm.range.first && state.tm.date <= state.tm.range.last;
    if (!picked) state.tm.date = state.tm.storm;
    $("tm-date").min = state.tm.range.first; $("tm-date").max = state.tm.range.last;
  }
  if (state.tm.date) await renderTimeMachine();
}

function shiftTm(delta) {
  if (!state.tm.date || !state.tm.range) return;
  const d = isoDay(new Date(new Date(`${state.tm.date}T00:00:00Z`).getTime() + delta * DAY_MS));
  if (d < state.tm.range.first || d > state.tm.range.last) return;
  state.tm.date = d; renderTimeMachine();
}

async function renderTimeMachine() {
  if (!state.tm.range || !state.tm.date) return;   // still loading the range: loadTimeMachine renders when ready
  const day = state.tm.date, next = isoDay(new Date(new Date(`${day}T00:00:00Z`).getTime() + DAY_MS));
  $("tm-date").value = day;
  $("tm-prev").disabled = day <= state.tm.range.first; $("tm-next").disabled = day >= state.tm.range.last;
  const box = $("tm"); box.classList.add("loading");
  try {
    const farm = state.farm ? `&farm_id=${encodeURIComponent(state.farm)}` : "";
    const [bt, ...plans] = await Promise.all([
      api(`/api/forecast/backtest?start=${day}&end=${next}${farm}`),
      ...["forecast", "reactive", "perfect"].map((s) => api(`/api/autopilot/schedule?date=${day}&strategy=${s}`).catch(() => null)),
    ]);
    if (state.tm.date !== day) return;   // the user moved on meanwhile
    const s = bt.series;
    const pred = s.reduce((a, p) => a + (p.pred_mwh || 0), 0), act = s.reduce((a, p) => a + (p.actual_mwh || 0), 0);
    $("tm-pred").textContent = fmtEnergy(pred);
    $("tm-actual").textContent = fmtEnergy(act);
    $("tm-err").textContent = act > 0.5 ? `forecast was ${fmtPct(Math.abs(pred - act) / act)} ${pred >= act ? "high" : "low"}` : "";
    legend($("tm-legend"), [{ label: "Really wasted", color: C.waste, kind: "line" },
      { label: "Our forecast, made the day before", color: C.ours, kind: "line" },
      { label: "Simple windy-day rule", color: C.neutral, kind: "line" }]);
    lineChart("tm-chart", s.map((p) => hourFmt.format(new Date(p.hour_start))), [
      { label: "actual", data: s.map((p) => p.actual_mwh), borderColor: C.waste },
      { label: "forecast", data: s.map((p) => p.pred_mwh), borderColor: C.ours },
      { label: "windy-day rule", data: s.map((p) => p.wind_rule_mwh), borderColor: C.neutral, borderDash: [4, 3] },
    ], { yLabel: "MWh per hour" });

    const t = clear($("tm-batteries"));
    t.append(el("thead", {}, el("tr", {}, el("th", {}, "Batteries planned with"), el("th", { class: "num" }, "Wasted wind stored"),
      el("th", { class: "num" }, "Earned"))));
    const tb = el("tbody");
    const names = { forecast: "Our forecast", reactive: "No forecast", perfect: "Knowing the future" };
    ["forecast", "reactive", "perfect"].forEach((k, i) => {
      const p = plans[i];
      if (!p || !p.batteries.length) return;
      const steps = p.batteries.flatMap((b) => b.steps);
      const stored = steps.reduce((a, x) => a + (x.captured_curtailment_mwh || 0), 0);
      const earned = steps.reduce((a, x) => a + (x.revenue_gbp || 0), 0);
      tb.append(el("tr", { class: k === "forecast" ? "ours" : "" }, el("td", {}, names[k]),
        el("td", { class: "num" }, fmtEnergy(stored)), el("td", { class: "num" }, fmtCompact(earned, "£"))));
    });
    t.append(tb);
    // why: the dispatcher's most common reason that day (numbers stripped), in plain words when it's the usual storm story
    const ours = plans[0] && plans[0].batteries.flatMap((b) => b.steps);
    let why = "";
    if (ours && ours.length) {
      const count = {};
      for (const x of ours) { const r = String(x.reason || "").split(",")[0].trim(); count[r] = (count[r] || 0) + 1; }
      const [top, n] = Object.entries(count).sort((a, b) => b[1] - a[1])[0];
      why = /holding: full/.test(top)
        ? `Most of the day (${fmtPct(n / ours.length)} of half hours) the batteries were already full and the pinch points had no room to sell into: on a storm day the cable, not the battery, is the limit. `
        : `Most common decision that day: "${top}" (${fmtPct(n / ours.length)} of half hours). `;
    }
    $("tm-note").textContent = why + (state.farm ? `Forecast for ${farmName(state.farm)} only (click "All farms" on History to see every farm). ` : "Forecast for all 33 farms. ")
      + `Batteries: the ${plans[0] && plans[0].batteries.length ? plans[0].batteries.length : 4} in the Batteries tab, all together. `
      + "One day can go either way (batteries store on one day and sell on another, and the one that knows the future plans "
      + "across the whole period); the fair comparison is the period total on the Batteries tab. "
      + `Only ${dayFmt.format(new Date(`${state.tm.range.first}T00:00:00Z`))} – ${dayFmt.format(new Date(`${state.tm.range.last}T00:00:00Z`))} can be looked back at: `
      + "earlier days were used to train the model, so a forecast for them would be cheating.";
  } catch (e) { showError(e); } finally { box.classList.remove("loading"); }
}

// ---------- Simulate ----------

const SIM_DEFAULT = () => ({
  b4: { mode: "historical", mw: 0 }, b6: { mode: "historical", mw: 0 },
  batteries: [], wind: 100, quality: "ours", preset: "all", start: "", end: "",
  link: { zone: "", gw: 2 },
  demand: { zone: "north_scotland", hp: 0, ev: 0, dc: 0 },
  eco: { gas: 80, carbon: 50, intensity: 0.37, switch: 1 },
});
// landing points for the drawn undersea cable (indicative), and the slider formats
const LINK_ROUTES = { north_scotland: [[-1.78, 57.50], [-1.15, 53.75]], south_scotland: [[-2.40, 55.97], [-1.35, 54.80]] };
const SIM_SLIDERS = [
  ["dem-hp", "demand", "hp", (v) => `${nf(1).format(v)} GW`], ["dem-ev", "demand", "ev", (v) => `${nf(1).format(v)} GW`],
  ["dem-dc", "demand", "dc", (v) => `${nf(1).format(v)} GW`],
  ["eco-gas", "eco", "gas", (v) => `${nf(0).format(v)}p/therm`], ["eco-carbon", "eco", "carbon", (v) => `£${nf(0).format(v)}/t`],
  ["eco-intensity", "eco", "intensity", (v) => `${nf(2).format(v)} t`], ["eco-switch", "eco", "switch", (v) => `${nf(1).format(v)}×`],
  ["link-gw", "link", "gw", (v) => `${nf(1).format(v)} GW`],
];
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
    batteries: s.batteries.map(({ battery_id, lat, lon, zone, power_mw, energy_mwh, efficiency, site, dispatcher }) =>
      ({ battery_id, lat, lon, zone, power_mw, energy_mwh, efficiency, site, dispatcher, radius_km: 20 })),
    weather: { wind_scale: s.wind / 100 }, window: simWindow(), forecast_quality: s.quality,
    subsea_link: s.link.zone ? { zone: s.link.zone, gw: s.link.gw, availability: 0.95 } : null,
    demand_growth: { [s.demand.zone]: { heat_pumps_gw: s.demand.hp, evs_gw: s.demand.ev, data_centres_gw: s.demand.dc } },
    gas_price_p_therm: s.eco.gas, carbon_price_gbp_t: s.eco.carbon, carbon_intensity_t_mwh: s.eco.intensity,
    switch_off_price: { mode: "multiplier", value: s.eco.switch },
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
  countUp($("sim-value"), fmtEnergy(s.curtailed_twh * 1e6));
  deltaText($("sim-delta"), d.curtailed_twh * 1e6, b.curtailed_twh * 1e6, fmtEnergy);
  const p1 = state.simPart1 && state.simPart1.totals.curtailed_twh;
  const sameData = state.simPart1 && state.simPart1.meta.source === r.meta.source;
  const win = r.window.start ? `${dayFmt.format(new Date(r.window.start))} – ${dayFmt.format(new Date(new Date(r.window.end) - 1))}` : "";
  $("sim-history").textContent = `${win} · What really happened: ${fmtEnergy(b.curtailed_twh * 1e6)} wasted` +
    (sameData && p1 ? ` (the History tab says ${fmtEnergy(p1 * 1e6)}).` : ".");

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
    el("span", { class: "key" }, el("span", { class: "swatch", style: { background: C.ours } }), "Your scenario")));
  for (const k of ["B4", "B6"]) {
    bnode.append(el("div", { class: "cmp-row", title: `${k}: history ${fmtEnergy(b.by_boundary_twh[k] * 1e6)}, scenario ${fmtEnergy(s.by_boundary_twh[k] * 1e6)}` },
      el("strong", {}, k),
      el("span", { class: "cmp-bars" },
        el("span", { style: { width: `${(b.by_boundary_twh[k] / max) * 100}%`, background: C.neutral } }),
        el("span", { style: { width: `${(s.by_boundary_twh[k] / max) * 100}%`, background: C.ours } })),
      el("span", { class: "val" }, fmtEnergy(s.by_boundary_twh[k] * 1e6))));
  }

  // batteries
  if (state.sim.batteries.length) {   // name the farms the server tied each battery to, without rebuilding the inputs
    document.querySelectorAll(".battery-note").forEach((n) => {
      const b = state.sim.batteries.find((x) => x.battery_id === n.dataset.id); if (b) n.textContent = batteryNote(b);
    });
    drawBatteryLinks();
  }
  const bo = clear($("sim-batteries-out"));
  if (r.batteries.length) {
    const t = el("table", { class: "table" }, el("thead", {}, el("tr", {}, el("th", {}, "Battery"),
      el("th", { class: "num" }, "Wasted wind stored"), el("th", { class: "num" }, "Earned"), el("th", { class: "num" }, "Full cycles"))));
    const tb = el("tbody");
    for (const x of r.batteries) {
      tb.append(el("tr", {}, el("td", { title: x.site === "farm" ? `At ${(x.farm_ids || []).map(farmName).join(", ")}` : "On the grid" },
        x.battery_id.replace(/^battery_(\d+)$/, "Battery $1"), el("div", { class: "note", style: { margin: "0" } }, x.site === "farm" ? `at ${(x.farm_ids || []).map(farmName).join(", ")}` : "on the grid")),
        el("td", { class: "num" }, fmtEnergy(x.captured_curtailment_mwh)),
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
  for (const [id, group, key, fmt] of SIM_SLIDERS) { $(id).value = state.sim[group][key]; $(`${id}-out`).textContent = fmt(state.sim[group][key]); }
  $("link-zone").value = state.sim.link.zone; $("link-size-row").hidden = !state.sim.link.zone;
  $("demand-zone").value = state.sim.demand.zone;
  renderBatteryList();
  drawBatteryMarkers();
}

function drawSubsea() {   // the new cable as a curve out at sea (indicative route)
  if (!map || !map.getSource) return;
  const z = state.tab === "simulate" && state.sim.link.zone;
  let features = [];
  if (z) {
    const [a, b] = LINK_ROUTES[z];
    const ctrl = [Math.max(a[0], b[0]) + 1.6, (a[1] + b[1]) / 2];   // bow it east, offshore
    const pts = [];
    for (let t = 0; t <= 1.0001; t += 0.05) {
      pts.push([(1 - t) ** 2 * a[0] + 2 * (1 - t) * t * ctrl[0] + t ** 2 * b[0], (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * ctrl[1] + t ** 2 * b[1]]);
    }
    features = [{ type: "Feature", geometry: { type: "LineString", coordinates: pts }, properties: {} }];
  }
  const data = { type: "FeatureCollection", features };
  if (map.getSource("subsea")) { map.getSource("subsea").setData(data); return; }
  if (!map.isStyleLoaded()) { map.once("idle", drawSubsea); return; }   // never silently drop the line
  map.addSource("subsea", { type: "geojson", data });
  map.addLayer({ id: "subsea", type: "line", source: "subsea",
    paint: { "line-color": C.ours, "line-width": 3, "line-dasharray": [1, 1.2] } });
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
  for (const [id, group, key, fmt] of SIM_SLIDERS) $(id).addEventListener("input", (e) => {
    state.sim[group][key] = Number(e.target.value); $(`${id}-out`).textContent = fmt(state.sim[group][key]); scheduleSim();
  });
  $("link-zone").addEventListener("change", (e) => {
    state.sim.link.zone = e.target.value; $("link-size-row").hidden = !e.target.value; drawSubsea(); scheduleSim();
  });
  $("demand-zone").addEventListener("change", (e) => { state.sim.demand.zone = e.target.value; scheduleSim(); });
  $("sim-reset").addEventListener("click", () => { state.sim = SIM_DEFAULT(); setPlacing(false); syncControls(); drawSubsea(); scheduleSim(); });
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
    power_mw: 200, energy_mwh: 400, efficiency: 0.88, site: "farm", dispatcher: "rule" });
  $("place-hint").textContent = `Added a battery ${zone === "north_scotland" ? "north of B4" : "between B4 and B6"}. Edit its size below.`;
  renderBatteryList(); drawBatteryMarkers(); scheduleSim();
  return true;
}

function renderBatteryList() {
  const list = clear($("battery-list"));
  for (const b of state.sim.batteries) {
    const choice = (label, key, opts) => el("label", {}, el("span", {}, label),
      el("select", { onchange: (e) => { b[key] = e.target.value; renderBatteryList(); scheduleSim(); } },
        ...opts.map(([v, t]) => el("option", { value: v, selected: b[key] === v ? "" : undefined }, t))));
    const field = (label, key, min, max, step) => el("label", {}, el("span", {}, label),
      el("input", { type: "number", min, max, step, value: key === "efficiency" ? Math.round(b[key] * 100) : b[key],
        oninput: (e) => {
          const v = Number(e.target.value);
          if (!Number.isFinite(v) || v < min || v > max) return;
          b[key] = key === "efficiency" ? v / 100 : v; scheduleSim();
        } }));
    const name = b.battery_id.replace(/^battery_(\d+)$/, "Battery $1");
    list.append(el("div", { class: "battery" },
      el("div", { class: "bhead" },
        el("span", {}, el("strong", {}, name), ` · ${b.zone === "north_scotland" ? "north of B4" : "between B4 and B6"}`),
        el("button", { type: "button", class: "remove", "aria-label": `Remove ${name}`, onclick: () => {
          state.sim.batteries = state.sim.batteries.filter((x) => x !== b); renderBatteryList(); drawBatteryMarkers(); scheduleSim();
        } }, "×")),
      el("div", { class: "sizes" }, field("Power (MW)", "power_mw", 1, 5000, 10), field("Storage (MWh)", "energy_mwh", 1, 20000, 10),
        field("Efficiency (%)", "efficiency", 80, 95, 1)),
      el("div", { class: "opts" },
        choice("Connected", "site", [["farm", "At the nearest wind farm"], ["grid", "To the grid (whole zone)"]]),
        choice("Planned by", "dispatcher", [["rule", "Simple rules"], ["lp", "Optimiser"], ["lp_band", "Optimiser, plans for the high end"]])),
      el("div", { class: "note battery-note", "data-id": b.battery_id, style: { margin: "0" } }, batteryNote(b))));
  }
}

function batteryNote(b) {
  const res = state.simResult && state.simResult.batteries.find((x) => x.battery_id === b.battery_id);
  const names = res && res.farm_ids && res.farm_ids.length ? res.farm_ids.map(farmName).join(", ") : null;
  const lp = b.dispatcher === "lp" ? " The optimiser takes a few seconds per battery." : "";
  if (b.site === "farm") {
    return (names ? `Shares a connection with ${names}: it can only store wind those farms would have wasted.`
      : "Shares a connection with the wind farm(s) within 20 km (or the nearest one): it can only store their waste.") + lp;
  }
  return `Connected to the grid, so it can store wind wasted anywhere ${b.zone === "north_scotland" ? "north of B4 (at either pinch point)" : "between B4 and B6 (at B6)"}.` + lp;
}

function drawBatteryLinks() {   // thin lines from each farm-sited battery to the farms it is tied to
  if (!map || !map.getSource || !map.loaded()) return;
  const feats = [];
  if (state.tab === "simulate" && state.simResult) {
    for (const b of state.sim.batteries) {
      const res = state.simResult.batteries.find((x) => x.battery_id === b.battery_id);
      for (const fid of (res && b.site === "farm" && res.farm_ids) || []) {
        const f = state.farms.find((x) => x.farm_id === fid);
        if (f) feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: [[b.lon, b.lat], [f.lon, f.lat]] }, properties: {} });
      }
    }
  }
  const data = { type: "FeatureCollection", features: feats };
  if (map.getSource("battery-links")) { map.getSource("battery-links").setData(data); return; }
  map.addSource("battery-links", { type: "geojson", data });
  map.addLayer({ id: "battery-links", type: "line", source: "battery-links",
    paint: { "line-color": C.ours, "line-width": 2, "line-opacity": 0.8 } });
}

function drawBatteryMarkers() {
  batteryMarkers.forEach((m) => m.remove());
  batteryMarkers.length = 0;
  drawBatteryLinks();
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
  drawSubsea();
  if (!state.simResult) await runSim(); else { renderSim(); updateMap(); }
}

// ---------- Autopilot ----------

const AP_COLORS = { charge: C.ours, discharge: C.oursDeep, hold: C.grid };
const STRATEGY_NAMES = { do_nothing: "No battery", reactive: "Without a forecast", forecast: "With our forecast", perfect: "Knows the future" };
state.ap = { scorecard: null, day: null, date: null, strategy: "forecast", planner: "rule", battery: null, step: -1, timer: null };
let apInit = false;

function apHasLpPlanner() {   // #13: the LP also plans reactive/forecast batteries
  return state.ap.scorecard.results.some((r) => r.strategy === "forecast" && r.dispatcher === "lp");
}
function apHasBandPlanner() {   // #52: the LP planning towards the high end of the forecast band
  return state.ap.scorecard.results.some((r) => r.strategy === "forecast" && r.dispatcher === "lp_band");
}
// the no-forecast battery has no band to plan for, so it is compared under the plain optimiser
const apPlannerFor = (strategy) => (strategy === "reactive" && state.ap.planner === "lp_band" ? "lp" : state.ap.planner);

function apResults() {
  const sc = state.ap.scorecard;
  // each strategy under the chosen planner (rule rows where that planner has none, e.g. do nothing);
  // "perfect" is always the optimiser's true ceiling once it exists
  const pick = (strategy) => sc.results.find((r) => r.strategy === strategy && r.dispatcher === apPlannerFor(strategy))
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
  const f = h && h[`forecast/${apPlannerFor("forecast")}`], r = h && h[`reactive/${apPlannerFor("reactive")}`];
  if (f == null || r == null) return "";
  return `On months the planning rules were not tuned on (${monthSpan(sc.holdout.months)}): ` +
    `${fmtPct(f)} vs ${fmtPct(r)}.`;
}

// "seagreen_bess" -> "Seagreen battery"
function batteryName(b) {
  const f = b.farm_id && state.farms.find((x) => x.farm_id === b.farm_id);
  return f ? `${farmName(b.farm_id)} battery` : b.battery_id;
}

/** The battery result in plain words: { main: one sentence, note: how it was tested and how sure we are }.
 *  The range is the day-block bootstrap 95% interval from scorecard.json (#69), as a gain over the no-forecast battery. */
function batteryGain(sc, planner) {
  const h = sc && sc.holdout, cr = h && h.capture_ratio;
  const f = cr && cr[`forecast/${planner}`], r = cr && cr[`reactive/${planner === "lp_band" ? "lp" : planner}`];
  if (f == null || !r) return null;
  const more = f / r - 1, months = h.months || [];
  const ci0 = h.forecast_minus_reactive_ci95 && h.forecast_minus_reactive_ci95[planner];
  const lo = ci0 && ci0.length === 2 ? Math.max(0, ci0[0] / r) : null, hi = ci0 && ci0.length === 2 ? ci0[1] / r : null;
  const when = months.length ? `Tested on ${monthSpan(months)}, months we kept aside while building it.` : "";
  if (more <= 0) return { more, lo, hi, months, main: "On the months we tested, our forecast did not help a battery earn more.", note: when };
  const ci = h.forecast_minus_reactive_ci95 && h.forecast_minus_reactive_ci95[planner];
  const range = ci && ci.length === 2
    ? ` With only ${months.length} months to test on, the real gain could be anywhere from ${Math.max(0, Math.round((ci[0] / r) * 100))}% to ${Math.round((ci[1] / r) * 100)}%.`
    : "";
  return { more, lo, hi, months, note: when + range,
           main: `A battery using our forecast earned about ${Math.round(more * 100)}% more than the same battery without one.` };
}

function apLpCeiling() {
  return state.ap.scorecard.results.some((r) => r.strategy === "perfect" && r.dispatcher === "lp");
}

function renderScorecard() {
  const sc = state.ap.scorecard, res = apResults();
  const h = sc.holdout && sc.holdout.capture_ratio;
  const pf = apPlannerFor("forecast"), pr = apPlannerFor("reactive");
  const hf = h && h[`forecast/${pf}`], hr = h && h[`reactive/${pr}`];
  const held = hf != null && hr != null;   // the fair result: months the planning rules never saw
  const gain = held ? batteryGain(sc, pf) : null;
  const span = held ? monthSpan(sc.holdout.months) : "";
  // one headline: how much more a battery earns with our forecast, and how sure we are
  if (gain && gain.more > 0) {
    countUp($("ap-headline"), `+${Math.round(gain.more * 100)}%`);
    $("ap-headline-sub").textContent = `Tested on ${span}, months we kept aside while building it.` +
      (gain.hi != null ? ` With only ${gain.months.length} months, the real gain could be anywhere from ${Math.round(gain.lo * 100)}% to ${Math.round(gain.hi * 100)}%.` : "");
  } else if (gain) {
    $("ap-headline").textContent = "No gain";
    $("ap-headline-sub").textContent = `On ${span} our forecast did not help a battery earn more.`;
  } else {   // older data without held-out months: the whole period
    const f = res.forecast, r = res.reactive;
    countUp($("ap-headline"), f && r && r.capture_ratio > 0 ? `+${Math.round((f.capture_ratio / r.capture_ratio - 1) * 100)}%` : "–");
    $("ap-headline-sub").textContent = "";
  }
  // bars: share of the most a battery could have earned, on the same months as the headline
  const rows = held
    ? [["perfect", (h["perfect/lp"] != null ? h["perfect/lp"] : 1)], ["forecast", hf], ["reactive", hr]]
    : ["perfect", "forecast", "reactive"].filter((k) => res[k]).map((k) => [k, res[k].capture_ratio]);
  hbars($("ap-ratio-bars"), rows.map(([k, v]) => ({ id: k, label: STRATEGY_NAMES[k], value: v,
    color: k === "forecast" ? C.ours : C.neutral })), { fmt: fmtPct });
  $("ap-bars-note").textContent = `Share of the most a battery could have earned${held ? `, ${span}` : ""}.`;
  // the full numbers, behind "Show the numbers"
  const order = ["perfect", "forecast", "reactive", "do_nothing"].filter((k) => res[k]);
  const t = clear($("ap-scorecard"));
  t.append(el("thead", {}, el("tr", {}, el("th", {}, "Battery"), el("th", { class: "num" }, "Wasted wind stored"),
    el("th", { class: "num" }, "Earned"), el("th", { class: "num" }, "Switch-off payments saved"))));
  const tb = el("tbody");
  for (const k of order) {
    const x = res[k];
    tb.append(el("tr", {}, el("td", {}, STRATEGY_NAMES[k]), el("td", { class: "num" }, fmtEnergy(x.captured_curtailment_mwh)),
      el("td", { class: "num" }, fmtCompact(x.revenue_gbp, "£")), el("td", { class: "num" }, fmtCompact(x.payments_avoided_gbp, "£"))));
  }
  t.append(tb);
  const period = sc.period ? `${dayFmt.format(new Date(sc.period.start))} – ${dayFmt.format(new Date(new Date(sc.period.end) - 1))}` : "";
  $("ap-score-note").textContent = `${period}, ${sc.batteries.length} batteries together: ` +
    sc.batteries.map((b) => `${batteryName(b)} (${nf(0).format(b.power_mw)} MW)`).join(", ") +
    ". A battery only stores wind that was really wasted, and only sells when the cables have room.";
}

async function loadApDay() {
  stopApReplay();
  const tab = $("tab-autopilot");
  tab.classList.add("loading");
  try {
    const q = new URLSearchParams({ strategy: state.ap.strategy });
    if (state.ap.date) q.set("date", state.ap.date);
    if (state.ap.battery) q.set("battery_id", state.ap.battery);
    if (state.ap.strategy !== "perfect") q.set("dispatcher", apPlannerFor(state.ap.strategy));
    let day = await api(`/api/autopilot/schedule?${q}`);
    // switching strategy or planner can leave the chosen day outside the new one's range (e.g. the live
    // day exists for the plain optimiser only): then show that planner's latest day instead of an empty one
    if (!day.batteries.length && day.available && state.ap.date &&
        (state.ap.date < day.available.first || state.ap.date > day.available.last)) {
      q.set("date", day.available.last);
      day = await api(`/api/autopilot/schedule?${q}`);
    }
    state.ap.day = day;
    noteSource("battery schedules", day.meta);
    if (day.available) {
      $("ap-date").min = day.available.first; $("ap-date").max = day.available.last;
    }
    if (day.date) { state.ap.date = day.date; $("ap-date").value = day.date; }
    renderApDay();
  } catch (e) { showError(e); } finally { tab.classList.remove("loading"); }
}

function bat0Done(day) {
  const b = day.batteries && day.batteries[0];
  return b ? b.steps.filter((x) => x.executed).length : 0;
}

function renderApDay() {
  const day = state.ap.day;
  const bat = day.batteries[0];
  const badge = $("ap-badge");
  const issued = day.plan_issued_at ? new Date(day.plan_issued_at) : null;
  const stale = day.live && issued && Date.now() - issued.getTime() > 36 * 3600 * 1000;
  badge.textContent = day.live ? (stale ? "live · stale" : "live") : "replay of the backtest";
  badge.className = "tag" + (day.live && !stale ? " live" : stale ? " stale" : "");
  const nDone = bat0Done(day);
  $("ap-issued").textContent = (day.live && issued ? `Live plan, made ${shortFmt.format(issued)} UTC.`
    : "Planned the day before, using only what was known then.") +
    (nDone ? ` The first ${nDone} half hours are done; the rest is the plan.` : "");
  legend($("ap-legend"), [{ label: "Storing wasted wind", color: AP_COLORS.charge },
    { label: "Selling", color: AP_COLORS.discharge }, { label: "Waiting", color: AP_COLORS.hold }]);

  const strip = clear($("ap-strip")), log = clear($("ap-log"));
  if (!bat) {
    $("ap-day-sum").textContent = "No schedule for this day.";
    if (charts["ap-soc-chart"]) { charts["ap-soc-chart"].destroy(); delete charts["ap-soc-chart"]; }
    return;
  }
  const steps = bat.steps;
  const caught = steps.reduce((s, x) => s + (x.captured_curtailment_mwh || 0), 0);
  const rev = steps.some((x) => x.revenue_gbp != null) ? steps.reduce((s, x) => s + (x.revenue_gbp || 0), 0) : null;
  $("ap-day-sum").textContent = `Stored ${fmtEnergy(caught)} of wasted wind` + (rev == null ? "" : ` · earned ${fmtCompact(rev, "£")}`);
  strip.setAttribute("aria-label", `Actions through the day for ${bat.battery_id}`);
  const mixed = day.live || bat0Done(day) > 0;   // part done, part still a plan
  steps.forEach((x, i) => {
    const time = hourFmt.format(new Date(x.period_start));
    strip.append(el("span", { class: mixed && !x.executed ? "planned" : "",
      style: { background: AP_COLORS[x.action] || C.grid }, title: `${time} · ${x.executed ? "done" : mixed ? "planned" : x.action}: ${x.action}, ${x.reason}`,
      onmouseenter: () => highlightApStep(i, false) }));
  });
  // the log: one row per run of identical half hours ("00:00–16:00 Waiting"), not 48 near-identical rows
  const rows = steps.map((x) => ({ x, what: logWhat(x, mixed && !x.executed), why: logWhy(x.reason),
    tag: x.executed ? "done" : mixed ? "plan" : "" }));
  for (const g of logGroups(rows.map((r) => `${r.x.action}|${r.what}|${r.why}|${r.tag}`))) {
    const r = rows[g.from], end = new Date(new Date(steps[g.to].period_start).getTime() + 30 * 60000);
    const time = hourFmt.format(new Date(steps[g.from].period_start)) + (g.to > g.from ? `–${hourFmt.format(end)}` : "");
    log.append(el("div", { class: "row", "data-from": g.from, "data-to": g.to },
      el("span", { class: "t" }, time),
      el("span", { class: "a" }, el("i", { style: { background: AP_COLORS[r.x.action] || C.grid } }), ({ charge: "store", discharge: "sell", hold: "wait" })[r.x.action] || r.x.action),
      el("span", {}, el("span", { class: "what" }, r.what),
        r.tag === "done" ? el("span", { class: "tag live-done" }, "done") : r.tag ? el("span", { class: "tag" }, "plan") : "",
        el("span", { class: "why" }, r.why))));
  }
  const cap = (state.ap.scorecard.batteries.find((b) => b.battery_id === bat.battery_id) || {}).energy_mwh;
  lineChart("ap-soc-chart", steps.map((x) => hourFmt.format(new Date(x.period_start))), [
    { label: "stored", data: steps.map((x) => x.soc_mwh), borderColor: C.ours, fill: "origin", backgroundColor: "rgba(14,159,191,0.12)" },
  ], { yLabel: "MWh stored" });
  if (cap) { charts["ap-soc-chart"].options.scales.y.max = cap; charts["ap-soc-chart"].update(); }
}

/** Runs of equal keys -> [{ from, to }] (inclusive indexes): consecutive identical log rows become one. */
function logGroups(keys) {
  const out = [];
  keys.forEach((k, i) => { if (i && k === keys[i - 1]) out[out.length - 1].to = i; else out.push({ from: i, to: i }); });
  return out;
}

/** Decision log, line 1: what the battery did, in the same words every time, from the executed numbers. */
function logWhat(x, planned) {
  const mwh = (x.power_mw || 0) * 0.5;   // a half hour
  const amt = (v) => (v >= 10 ? nf(0) : nf(1)).format(v);
  if (x.action === "discharge") {
    if (planned) return mwh < 0.05 ? "Ready to sell here if it has charge" : `Plans to sell up to ${amt(mwh)} MWh`;
    const per = mwh > 1e-6 && x.revenue_gbp != null ? x.revenue_gbp / mwh : null;
    return mwh < 0.05 ? "Sold almost nothing: the battery was nearly empty"
      : `Sold ${amt(mwh)} MWh at £${nf(0).format(per)}/MWh = ${fmtCompact(x.revenue_gbp, "£")}`;
  }
  if (x.action === "charge") {
    const got = x.captured_curtailment_mwh != null ? x.captured_curtailment_mwh : mwh;
    return planned ? `Plans to store up to ${amt(mwh)} MWh of wasted wind` : `Stored ${amt(got)} MWh of wind that was being switched off`;
  }
  return "Waiting";
}
/** Line 2: the planner's reason, in plain words. Its prices are what the plan EXPECTED the day before. */
function logWhy(reason) {
  return (reason || "")
    .replace(/^optimiser: |^holding: |^charging: |^discharging: /, "")
    .replace(/^selling at (£\d+\/MWh)/, "planned: expected $1")
    .replace(/^sell slot if charged \((£\d+\/MWh)/, "planned: sell here if charged (expected $1")
    .replace(/^top-price half hour \((£\d+\/MWh)\)/, "planned: one of the day's top prices (expected $1)")
    .replace(/MW room on (B\d)/, "MW of room on the $1 line")
    .replace(/(\d+) MWh being curtailed now \(not in the plan\)/, "$1 MWh was being switched off right now (the forecast hadn't expected it)")
    .replace(/^storing ([\d.]+) MWh of forecast waste behind (B\d)/, "planned: store $1 MWh of forecast waste behind $2")
    .replace(/^holding, /, "")
    .replace(/^planned sale not possible \(no room, no charge or price <= 0\)$/, "planned to sell, but couldn't: battery empty, no room on the line, or price below zero")
    .replace(/^full, ([\d.]+) MWh waste forecast behind (B\d)$/, "full: $1 MWh of waste forecast behind $2")
    .replace(/^forecast waste did not happen$/, "the waste the forecast expected didn't happen")
    .replace(/^better prices later$/, "better prices expected later")
    .replace(/^no room on (B\d)( to export)?$/, "no room on the $1 line to sell");
}

function highlightApStep(i, scroll = true) {
  state.ap.step = i;
  document.querySelectorAll("#ap-strip span").forEach((s, j) => s.classList.toggle("now", j === i));
  let row = null;   // log rows cover runs of half hours: light the one that contains step i
  document.querySelectorAll("#ap-log .row").forEach((r) => {
    const on = Number(r.dataset.from) <= i && i <= Number(r.dataset.to);
    r.classList.toggle("now", on); if (on) row = r;
  });
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
        `${batteryName(b)} · ${nf(0).format(b.power_mw)} MW`));
      state.ap.battery = (state.ap.scorecard.batteries[0] || {}).battery_id || null;
      sel.addEventListener("change", (e) => { state.ap.battery = e.target.value; loadApDay(); });
      $("ap-strategy").addEventListener("change", (e) => { state.ap.strategy = e.target.value; loadApDay(); });
      const planner = $("ap-planner");
      planner.hidden = !apHasLpPlanner();
      planner.querySelector('option[value="lp_band"]').hidden = !apHasBandPlanner();
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

const SIGNAL_NAMES = {
  pot_farm_mw: "This farm's expected output", pot_north_mw: "Expected wind output, north of B4",
  pot_south_mw: "Expected wind output, south of B4", farm_code: "Which farm it is", capacity_mw: "Farm size",
  wind_fc_ms: "Forecast wind speed", b4_limit_mw: "B4 capacity tomorrow", b6_limit_mw: "B6 capacity tomorrow",
  b4_excess_potential_mw: "Expected overflow at B4", b6_excess_potential_mw: "Expected overflow at B6",
  gb_demand_fc_mw: "Britain's forecast demand", lag48_mwh: "Waste at this hour 2 days ago",
  trailing_waste_rate: "How often it was switched off lately", bid_median_gbp_mwh: "Its price to switch off",
  hour: "Hour of day", weekday: "Day of week", month: "Month",
};

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
    (sc.holdout ? ` The planning rules were tuned on other months; ${monthSpan(sc.holdout.months)} are held out.` : "");
  const bw = sc.band_weight, hold = sc.holdout && sc.holdout.capture_ratio;
  if (bw && hold && hold["forecast/lp_band"] != null && hold["forecast/lp"] != null) {
    $("method-basis").textContent += ` A third planner plans for waste between the likely value and the high end of the ` +
      `forecast's range (weight ${bw.chosen}, chosen on ${monthSpan(bw.selected_on)}), because the forecast's misses are storms ` +
      `above its range. On the held-out months it earns ${pct1(hold["forecast/lp_band"])} of the maximum, vs ` +
      `${pct1(hold["forecast/lp"])} for the plain optimiser.`;
  }
  const ul = clear($("method-caveats"));
  for (const c of sc.caveats || []) ul.append(el("li", {}, c));
}

// ---------- wind arrows (map layer): 100 m wind at each farm for one day, from Open-Meteo via /api/wind ----------

state.wind = { on: false, date: null, data: null, hour: 12, timer: null, error: null };

function windDate() {   // which day the arrows are for: one picked day on History, or tomorrow on Forecast
  if (state.tab === "predict" && state.tomorrow && state.tomorrow.farms && state.tomorrow.farms[0])
    return state.tomorrow.farms[0].hours[0].hour_start.slice(0, 10);
  if (state.tab === "reveal" && state.range && state.range.end - state.range.start === DAY_MS) return isoDay(state.range.start);
  return null;
}

function windArrowImage() {   // a slate arrow pointing up (north), drawn once
  const s = 64, c = document.createElement("canvas"); c.width = c.height = s;
  const g = c.getContext("2d");
  g.translate(s / 2, s / 2);
  g.beginPath(); g.moveTo(0, -26); g.lineTo(13, -4); g.lineTo(5, -6); g.lineTo(5, 24); g.lineTo(-5, 24); g.lineTo(-5, -6);
  g.lineTo(-13, -4); g.closePath();
  g.fillStyle = C.wind; g.strokeStyle = "#ffffff"; g.lineWidth = 3; g.lineJoin = "round";
  g.stroke(); g.fill();
  return g.getImageData(0, 0, s, s);
}

function drawWind() {
  if (!map) return;
  const feats = [];
  const d = state.wind.on && state.wind.data;
  if (d) {
    for (const w of d.farms) {
      const f = state.farms.find((x) => x.farm_id === w.farm_id);
      const sp = w.speed_ms[state.wind.hour], dir = w.direction_deg[state.wind.hour];
      if (!f || sp == null || dir == null) continue;
      // beside the turbine, pointing where the wind blows TO (direction is where it comes from)
      feats.push({ type: "Feature", geometry: { type: "Point", coordinates: [f.lon + 0.16, f.lat] },
        properties: { rot: (dir + 180) % 360, speed: sp, label: `${nf(0).format(sp)}` } });
    }
  }
  const data = { type: "FeatureCollection", features: feats };
  if (map.getSource("wind")) { map.getSource("wind").setData(data); return; }
  // the first draw adds an image and a layer, which needs the style ready. If it's busy (tiles loading after
  // a date change), draw again once it's idle: an early return here used to leave the arrows blank (found
  // checking the #58 merge)
  if (!map.isStyleLoaded()) { map.once("idle", drawWind); return; }
  if (!map.hasImage("wind-arrow")) map.addImage("wind-arrow", windArrowImage(), { pixelRatio: 2 });
  map.addSource("wind", { type: "geojson", data });
  map.addLayer({ id: "wind", type: "symbol", source: "wind", layout: {
    "icon-image": "wind-arrow", "icon-rotate": ["get", "rot"], "icon-rotation-alignment": "map", "icon-allow-overlap": true,
    "icon-size": ["interpolate", ["linear"], ["get", "speed"], 0, 0.35, 25, 1.05],
    "text-field": ["get", "label"], "text-size": 10, "text-offset": [0, 1.6], "text-allow-overlap": true,
    "text-font": ["Noto Sans Regular"] },
    paint: { "text-color": C.ink2, "text-halo-color": "#ffffff", "text-halo-width": 1.2 } });
}

function renderWindLabel() {
  const d = state.wind.data, label = $("wind-label");
  if (!state.wind.on) return;
  if (state.wind.error) { label.textContent = state.wind.error; return; }
  if (!d) { label.textContent = windDate() ? "Loading wind…" : "Pick one day on History (Day), or open Forecast, to see its wind."; return; }
  const sp = d.farms.map((w) => w.speed_ms[state.wind.hour]).filter((x) => x != null);
  const avg = sp.length ? sp.reduce((a, b) => a + b, 0) / sp.length : null;
  label.textContent = `${dayFmt.format(new Date(`${d.date}T00:00:00Z`))}, ${String(state.wind.hour).padStart(2, "0")}:00 UTC` +
    (avg != null ? ` · average ${nf(1).format(avg)} m/s at 100 m` : "") + ` · arrows point where the wind blows; numbers are m/s · ${d.source}`;
}

async function updateWind() {
  const day = state.wind.on ? windDate() : null;
  $("wind-hour").disabled = $("wind-play").disabled = !day;
  if (!day) { state.wind.data = null; state.wind.error = null; stopWindPlay(); drawWind(); renderWindLabel(); return; }
  if (!state.wind.data || state.wind.data.date !== day) {
    state.wind.data = null; state.wind.error = null; renderWindLabel(); drawWind();
    try {
      const d = await api(`/api/wind?date=${day}`);
      if (windDate() !== day) return;   // moved on meanwhile
      state.wind.data = d;
    } catch (e) {
      state.wind.error = "Wind isn't available right now (no connection to the weather service).";
    }
  }
  drawWind(); renderWindLabel();
}

function stopWindPlay() {
  if (state.wind.timer) clearInterval(state.wind.timer);
  state.wind.timer = null; $("wind-play").textContent = "▶";
}

function initWind() {
  $("wind-toggle").addEventListener("click", () => {
    state.wind.on = !state.wind.on;
    $("wind-toggle").setAttribute("aria-pressed", String(state.wind.on));
    $("wind-body").hidden = !state.wind.on;
    if (!state.wind.on) stopWindPlay();
    updateWind();
  });
  $("wind-hour").addEventListener("input", (e) => { stopWindPlay(); state.wind.hour = Number(e.target.value); drawWind(); renderWindLabel(); });
  $("wind-play").addEventListener("click", () => {
    if (state.wind.timer) return stopWindPlay();
    $("wind-play").textContent = "❚❚";
    state.wind.timer = setInterval(() => {
      state.wind.hour = (state.wind.hour + 1) % 24; $("wind-hour").value = state.wind.hour; drawWind(); renderWindLabel();
    }, 700);
  });
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
  drawSubsea();
  updateWind();
  if (tab === "predict") loadPredict(); else if (tab === "reveal") { renderReveal(); updateMap(); loadFarmDetail().catch(showError); }
  else if (tab === "simulate") loadSimulate();
  else if (tab === "autopilot") { loadAutopilot(); updateMap(); }
  else { if (tab === "method") loadMethod().catch(showError); updateMap(); }
}

// ---------- "How it works": the plain-English story and the data, shown on the first visit ----------
const STORM_DAY = "2026-10-27";   // the biggest day in the data, 12 days before judging: the "why now" of the intro
let aboutPage = 1;
function showAbout(open) {
  $("about").hidden = !open;
  if (open) { aboutGo(1); fillStorm(); }
  else try { localStorage.setItem("ww-about-seen", "1"); } catch (_) { /* private mode */ }
}
function aboutGo(n) {
  aboutPage = n;
  document.querySelectorAll(".about-page").forEach((s) => (s.hidden = Number(s.dataset.page) !== n));
  document.querySelectorAll(".about-dots span").forEach((d, i) => d.classList.toggle("on", i === n - 1));
  $("about-back").hidden = n === 1;
  $("about-next").hidden = n === 3;
  $("about-tour").hidden = n !== 3;
  $("about-explore").textContent = n === 3 ? "Explore myself" : "Skip";
  $("about").querySelector(".about-card").scrollTop = 0;
}
function fillAbout() {   // the headline figures, from the History totals over all the data
  const t = state.revealAll && state.revealAll.totals;
  if (!t) return;
  $("about-twh").textContent = fmtEnergy(t.curtailed_twh * 1e6);
  $("about-paid").textContent = fmtCompact(Math.abs(t.payments_gbp), "£");
  $("about-gas").textContent = fmtCompact(t.replacement_gas_gbp, "£");
}
let stormFilled = false;
async function fillStorm() {   // the storm day's figures, live from the same API the History tab uses
  if (stormFilled) return;
  stormFilled = true;
  const end = isoDay(new Date(new Date(`${STORM_DAY}T00:00:00Z`).getTime() + DAY_MS));
  try {
    const [r, bt, sc] = await Promise.all([api(`/api/reveal/summary?start=${STORM_DAY}&end=${end}`),
      api(`/api/forecast/backtest?start=${STORM_DAY}&end=${end}`).catch(() => null),
      api("/api/autopilot/scorecard").catch(() => null)]);
    const t = r.totals, cut = t.curtailed_twh * 1e6, pot = r.farms.reduce((a, f) => a + (f.potential_mwh || 0), 0);
    $("storm-twh").textContent = fmtEnergy(cut);
    $("storm-paid").textContent = fmtCompact(Math.abs(t.payments_gbp), "£");
    $("storm-gas").textContent = fmtCompact(t.replacement_gas_gbp, "£");
    if (pot > 0) $("storm-share").textContent = fmtPct(cut / pot);
    if (bt && bt.series.length) {
      const pred = bt.series.reduce((a, x) => a + (x.pred_mwh || 0), 0), act = bt.series.reduce((a, x) => a + (x.actual_mwh || 0), 0);
      $("about-proof").innerHTML = "";
      $("about-proof").append(el("b", {}, "Did we see 27 October coming? "),
        `Yes: the day before, we forecast ${fmtEnergy(pred)}. ${fmtEnergy(act)} was wasted.`);
      const g = batteryGain(sc, "lp");
      const big = document.querySelectorAll('.about-page[data-page="2"] .about-big b')[2];
      if (g && big && g.more > 0) big.textContent = `+${Math.round(g.more * 100)}%`;
    }
  } catch (_) { stormFilled = false; }
}
function initAbout() {
  $("about-open").addEventListener("click", () => showAbout(true));
  $("about-close").addEventListener("click", () => showAbout(false));
  $("about-explore").addEventListener("click", () => showAbout(false));
  $("about-next").addEventListener("click", () => aboutGo(Math.min(3, aboutPage + 1)));
  $("about-back").addEventListener("click", () => aboutGo(Math.max(1, aboutPage - 1)));
  $("about").addEventListener("click", (e) => { if (e.target === $("about")) showAbout(false); });
  document.addEventListener("keydown", (e) => {
    if ($("about").hidden) return;
    if (e.key === "Escape") showAbout(false);
    else if (e.key === "ArrowRight") aboutGo(Math.min(3, aboutPage + 1));
    else if (e.key === "ArrowLeft") aboutGo(Math.max(1, aboutPage - 1));
  });
  $("about").querySelectorAll("[data-go]").forEach((n) => n.addEventListener("click", (e) => {
    e.preventDefault(); showAbout(false); setTab(n.dataset.go);
  }));
  $("about-tour").addEventListener("click", () => {
    showAbout(false);
    const b = document.querySelector('[data-tour-start="1"]');
    if (b) b.click();
  });
  let seen = false;
  try { seen = localStorage.getItem("ww-about-seen") === "1"; } catch (_) { /* show it */ }
  if (!seen && !/[?&](notour\b|tour=)/.test(location.search)) showAbout(true);   // never over a tour started from the URL
}


// ---------- "where does this number come from?": a grey ⓘ beside each number's label ----------
const GAS_NOTE = "gas at 80p/therm burned at 50% efficiency (about £55/MWh) plus carbon at £50/t × 0.37 t per MWh (about £18.50), so about £73 per MWh";
const SIM_NOTE = " In Simulate, the same sum is done on your scenario: the real 2024–2026 history replayed with your changes. The line underneath is your scenario minus what really happened.";
const INFO = {
  "hero-value": "What each farm planned to generate (Elexon physical notifications) minus what the grid let it generate after its switch-off orders (bid-offer acceptances), added up over the 33 farms we track and every half hour in the period. Cross-checked against wind speed through each farm's power curve.",
  "t-payments": "Each wasted MWh × the price that farm itself bid to be paid to switch off (Elexon bid-offer data), added up. It's a net figure: a few farms on government price contracts pay to switch off when power prices go negative.",
  "t-gas": `Each wasted MWh × the cost of making it in a gas plant instead: ${GAS_NOTE}.`,
  "t-co2": "Each wasted MWh × 0.37 tonnes of CO₂, typical for electricity from a gas plant.",
  "t-homes": "Wasted MWh ÷ 2.7 MWh, a typical British home's electricity use in a year.",
  "pred-value": "Our machine-learning forecast (LightGBM), added up over the farms and hours shown. It reads 17 signals: weather forecasts at each farm, the power-line limits, the demand forecast, switch-off prices and recent waste. The likely range comes from two extra models for the low (10%) and high (90%) ends.",
  "tm-pred": "The forecast exactly as it was saved at 10:00 the day before, made only with data available at that time.",
  "tm-actual": "What was really wasted that day, measured afterwards from Elexon records: planned output minus what the grid allowed.",
  "sim-value": "Wasted wind in your scenario: planned minus allowed, recomputed with your changes to power lines, cables, batteries, demand and weather." + SIM_NOTE,
  "s-payments": "Each wasted MWh in your scenario × the farm's own switch-off price (scaled if you changed the switch-off price)." + SIM_NOTE,
  "s-gas": `Each wasted MWh in your scenario × the cost of gas power instead: ${GAS_NOTE}, or your own gas and carbon prices.` + SIM_NOTE,
  "s-co2": "Each wasted MWh in your scenario × 0.37 t of CO₂ per MWh of gas power (or your own value)." + SIM_NOTE,
  "s-homes": "Wasted MWh in your scenario ÷ 2.7 MWh, a typical home's electricity use in a year." + SIM_NOTE,
  "ap-headline": "The same 4 batteries, replayed against what really happened on Aug–Oct 2026 (months never used to tune the planner). With our forecast they earned 35% of the most possible (a battery that knows the future); without a forecast, 27%. 35 ÷ 27 − 1 ≈ 27% more. Revenue = MWh sold × the real market price for that half hour (Elexon).",
};
function addInfo(id) {
  const value = $(id);
  if (!value || !INFO[id]) return;
  const box = value.closest(".tile, .hero");
  const label = box && box.querySelector(".label");
  if (!label || label.querySelector(".info")) return;
  label.append(el("span", { class: "info", tabindex: "0", role: "button", "aria-label": `How this is worked out: ${INFO[id]}`, "data-tip": INFO[id] }, "i"));
}
function initInfo() {
  Object.keys(INFO).forEach(addInfo);
  const tip = el("div", { id: "info-tip", role: "tooltip", hidden: "" });
  document.body.append(tip);
  const show = (e) => {
    const t = e.target.closest && e.target.closest(".info");
    if (!t) return;
    tip.textContent = t.dataset.tip; tip.hidden = false;
    const r = t.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    const left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2));
    const top = r.bottom + 8 + h < window.innerHeight ? r.bottom + 8 : r.top - h - 8;
    tip.style.left = `${left}px`; tip.style.top = `${top}px`;
  };
  const hide = (e) => { if (e.target.closest && e.target.closest(".info")) tip.hidden = true; };
  document.addEventListener("mouseover", show); document.addEventListener("mouseout", hide);
  document.addEventListener("focusin", show); document.addEventListener("focusout", hide);
}

async function boot() {
  initInfo();
  initAbout();
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
  document.querySelectorAll("#tab-reveal .seg button").forEach((b) => b.addEventListener("click", () => { stopPlay(); setMode(b.dataset.mode); }));
  $("play").addEventListener("click", togglePlay);
  const showPeriod = (mode, day) => {   // load the whole year / month / week that contains `day`
    if (!day || isNaN(day)) return;
    stopPlay(); state.mode = mode; state.range = defaultRange(mode, day); syncDateInputs(); loadReveal();
  };
  $("range-start").addEventListener("change", (e) => {
    // standing in for a month / week picker (Firefox, Safari): any day picked selects its month or week
    if (state.mode === "month" || state.mode === "week") return showPeriod(state.mode, new Date(`${e.target.value}T00:00:00Z`));
    stopPlay(); setRangeFromInputs();
  });
  $("range-period").addEventListener("change", (e) => showPeriod("year", new Date(`${e.target.value}T00:00:00Z`)));
  $("range-month").addEventListener("change", (e) => e.target.value && showPeriod("month", new Date(`${e.target.value}-01T00:00:00Z`)));
  $("range-week").addEventListener("change", (e) => showPeriod("week", mondayOfIsoWeek(e.target.value)));
  $("range-end").addEventListener("change", () => { stopPlay(); setRangeFromInputs(); });
  $("range-prev").addEventListener("click", () => { stopPlay(); stepRange(-1); });
  $("range-next").addEventListener("click", () => { stopPlay(); stepRange(1); });
  $("farm-clear").addEventListener("click", () => selectFarm(state.farm));
  try {
    const [health, farms] = await Promise.all([api("/api/health"), api("/api/farms")]);
    state.farms = farms.farms;
    const start = new Date(health.range.start), end = new Date(health.range.end);
    for (let d = new Date(start); d < end; d = new Date(d.getTime() + 86400000)) state.days.push(d);
    syncDateInputs();
    noteSource("wind farm waste", health.meta);
    $("source-tag").title = `Data release ${health.meta.data_version}, generated ${health.meta.generated_at}`;
    // The panel must never wait on map tiles (venue Wi-Fi): load both, draw the map when ready.
    initMap().then(updateMap).catch((e) => console.warn("map failed to load", e));
    await loadReveal();
  } catch (e) { showError(e); }
}

boot();
