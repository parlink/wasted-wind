/* Guided tour engine (#70). Generic: it knows nothing about this app. Element names, steps, dates and the few
   app functions it calls are all in tour-config.js (window.TOUR).

   Screen-size independence: nothing is positioned in fixed pixels. The spotlight follows each target's live
   bounding box every animation frame (so layout shifts, charts finishing, window resizes and projector
   scaling are all followed), the card is sized in em from a viewport-relative font size (tour.css), and the
   card moves to another corner whenever it would cover the spotlight. */
(() => {
  "use strict";
  const T = window.TOUR;
  if (!T) return;
  const S = T.settings;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---------- pure geometry (tested under node in tests/test_tour.py) ----------

  function overlapArea(a, b) {
    const w = Math.min(a.left + a.width, b.left + b.width) - Math.max(a.left, b.left);
    const h = Math.min(a.top + a.height, b.top + b.height) - Math.max(a.top, b.top);
    return w > 0 && h > 0 ? w * h : 0;
  }
  function unionRect(rects) {
    const l = Math.min(...rects.map((r) => r.left)), t = Math.min(...rects.map((r) => r.top));
    const r = Math.max(...rects.map((x) => x.left + x.width)), b = Math.max(...rects.map((x) => x.top + x.height));
    return { left: l, top: t, width: r - l, height: b - t };
  }
  function inflate(r, d) { return { left: r.left - d, top: r.top - d, width: r.width + 2 * d, height: r.height + 2 * d }; }
  function clampRect(r, vp) {
    const l = Math.max(0, r.left), t = Math.max(0, r.top);
    return { left: l, top: t, width: Math.max(0, Math.min(vp.w, r.left + r.width) - l), height: Math.max(0, Math.min(vp.h, r.top + r.height) - t) };
  }
  /** Corners to try: the preferred one first, then its mirror images. */
  function cornerOrder(pref) {
    const [v, h] = pref.split("-"), flip = { top: "bottom", bottom: "top", left: "right", right: "left" };
    return [pref, `${v}-${flip[h]}`, `${flip[v]}-${h}`, `${flip[v]}-${flip[h]}`];
  }
  function cardRectAt(corner, card, vp, gap) {
    const [v, h] = corner.split("-");
    return { left: h === "left" ? gap : vp.w - gap - card.w, top: v === "top" ? gap : vp.h - gap - card.h, width: card.w, height: card.h };
  }
  /** First corner whose card does not touch the target; else the one covering the least of it. */
  function chooseCorner(pref, card, vp, gap, target) {
    let best = null;
    for (const corner of cornerOrder(pref)) {
      const rect = cardRectAt(corner, card, vp, gap), overlap = target ? overlapArea(rect, target) : 0;
      if (overlap === 0) return { corner, rect };
      if (!best || overlap < best.overlap) best = { corner, rect, overlap };
    }
    return best;
  }

  // ---------- page helpers ----------

  let run = null;

  function resolve(name) {
    const sel = T.targets[name];
    if (!sel) { console.warn("tour: unknown target", name); return null; }
    try { return document.querySelector(sel); } catch (e) { console.warn("tour: bad selector", sel); return null; }
  }
  function isVisible(el) {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
  }
  const toList = (show) => (show == null ? [] : Array.isArray(show) ? show : [show]);

  async function waitAll(names) {
    const t0 = Date.now();
    for (;;) {
      const els = names.map(resolve);
      if (els.every(isVisible)) return els;
      if (Date.now() - t0 > S.targetWaitMs) return null;
      await sleep(100);
    }
  }
  async function settle() {
    await sleep(S.settleMinMs);
    const t0 = Date.now();
    while (document.querySelector(S.busySelector) && Date.now() - t0 < S.settleMaxMs) await sleep(100);
  }
  function scrollBox() { return document.querySelector(S.scrollContainer); }
  /** Scroll the panel so the rect sits in the middle of the screen (or its top if it is taller than the screen). */
  function scrollToRect(rect, smooth) {
    const box = scrollBox();
    if (!box) return;
    const vh = window.innerHeight;
    const delta = rect.height > vh * 0.8 ? rect.top - vh * 0.08 : rect.top + rect.height / 2 - vh / 2;
    if (Math.abs(delta) > 4) box.scrollBy({ top: delta, behavior: smooth ? "smooth" : "auto" });
  }
  function ripple(el) {
    const r = el.getBoundingClientRect();
    const d = document.createElement("div");
    d.className = "tour-ripple";
    d.style.left = `${r.left + r.width / 2}px`; d.style.top = `${r.top + r.height / 2}px`;
    run.root.append(d);
    setTimeout(() => d.remove(), 900);
  }

  // ---------- actions and steps ----------

  async function perform(action) {
    const [kind, ...args] = action;
    if (kind === "tab") await T.funcs.tab(args[0]);
    else if (kind === "click" || kind === "set") {
      const [el] = (await waitAll([args[0]])) || [];
      if (!el) { console.warn("tour: cannot reach", args[0]); return; }
      const r = el.getBoundingClientRect();
      if (r.top < 0 || r.bottom > window.innerHeight) { scrollToRect(r, false); await sleep(150); }
      ripple(el);
      await sleep(350);                               // the ring shows first, then the click lands
      if (kind === "click") el.click();
      else {
        el.value = args[1];
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }
    } else if (kind === "call") {
      const fn = T.funcs[args[0]];
      if (!fn) console.warn("tour: unknown function", args[0]); else await fn(...args.slice(1));
    } else if (kind === "wait") await sleep(args[0]);
    else console.warn("tour: unknown action", kind);
    await settle();
  }

  function go(n, dir) {
    if (!run) return;
    if (n >= run.steps.length) return exit();
    if (n < 0) { n = 0; dir = 1; }
    run.dir = dir;
    show(n);
  }
  const next = () => go(run.i + 1, 1);
  const prev = () => go(run.i - 1, -1);

  async function show(n) {
    const me = run, tok = ++me.token, step = me.steps[n];
    clearTimeout(me.timer);
    me.i = n; me.shown = false; me.names = [];
    // step.dim: how dark the page goes (0-1); lower it when the story is on the map behind the spotlight
    if (step.dim != null) me.root.style.setProperty("--tour-dim", `rgba(14, 18, 24, ${step.dim})`);
    else me.root.style.removeProperty("--tour-dim");
    renderCard();
    hideSpot();
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    if (T.funcs.beforeStep) await T.funcs.beforeStep();
    for (const a of step.do || []) {
      me.dirty = true;
      await perform(a);
      if (run !== me || tok !== me.token) return;
    }
    const names = toList(step.show);
    if (names.length) {
      const els = await waitAll(names);
      if (run !== me || tok !== me.token) return;
      if (!els) { console.warn("tour: skipping", step.id, "(target not visible)"); return go(n + (me.dir > 0 ? 1 : -1), me.dir); }
      scrollToRect(unionRect(els.map((e) => e.getBoundingClientRect())), true);
      await sleep(450);                               // let the smooth scroll finish before spotlighting
      if (run !== me || tok !== me.token) return;
    }
    me.names = names; me.shown = true;
    me.placed = "";                                    // force a fresh placement
    if (me.mode === "auto" && !me.paused) arm();
  }

  function arm() {
    clearTimeout(run.timer);
    const step = run.steps[run.i];
    const me = run;
    run.timer = setTimeout(() => { if (run === me && me.mode === "auto" && !me.paused) next(); }, step.hold || S.holdMs);
  }

  // ---------- overlay ----------

  function h(tag, attrs = {}, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") e.className = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
    }
    e.append(...kids);
    return e;
  }
  function build(opts) {
    const c = {};
    c.count = h("span", { class: "tour-count" });
    c.presenter = h("button", { type: "button", class: "tour-mode-btn", onclick: () => setMode("presenter") }, "Presenter");
    c.auto = h("button", { type: "button", class: "tour-mode-btn", onclick: () => setMode("auto") }, "Auto");
    c.close = h("button", { type: "button", class: "tour-x", "aria-label": "Close tour", onclick: () => exit() }, "✕");
    c.label = h("p", { class: "tour-label", "aria-live": "polite" });
    c.bar = h("span", { class: "tour-bar-fill" });
    c.prev = h("button", { type: "button", class: "tour-nav-btn", "aria-label": "Previous step", onclick: () => prev() }, "‹");
    c.pause = h("button", { type: "button", class: "tour-nav-btn", "aria-label": "Pause", onclick: () => togglePause() }, "⏸");
    c.next = h("button", { type: "button", class: "tour-nav-btn", "aria-label": "Next step", onclick: () => next() }, "›");
    c.card = h("div", { class: "tour-card", role: "dialog", "aria-label": "Guided tour" },
      h("div", { class: "tour-top" }, c.count, h("span", { class: "tour-modes" }, c.presenter, c.auto), c.close),
      c.label, h("div", { class: "tour-bar" }, c.bar), h("div", { class: "tour-nav" }, c.prev, c.pause, c.next));
    c.spot = h("div", { class: "tour-spot", hidden: "" });
    c.root = h("div", { class: "tour-root" }, c.spot, c.card);
    if (opts.scale) c.root.style.setProperty("--tour-scale", String(opts.scale));
    document.body.append(c.root);
    return c;
  }
  function renderCard() {
    const step = run.steps[run.i];
    run.label.textContent = step.label || "";
    run.count.textContent = `${run.i + 1} / ${run.steps.length}`;
    run.bar.style.width = `${((run.i + 1) / run.steps.length) * 100}%`;
    run.prev.disabled = run.i === 0;
    run.next.textContent = run.i === run.steps.length - 1 ? "✓" : "›";
    run.next.setAttribute("aria-label", run.i === run.steps.length - 1 ? "Finish tour" : "Next step");
    run.presenter.setAttribute("aria-pressed", String(run.mode === "presenter"));
    run.auto.setAttribute("aria-pressed", String(run.mode === "auto"));
    run.pause.hidden = run.mode !== "auto";
    run.pause.textContent = run.paused ? "▶" : "⏸";
    run.pause.setAttribute("aria-label", run.paused ? "Resume" : "Pause");
  }
  function setMode(mode) {
    run.mode = mode; run.paused = false;
    try { localStorage.setItem("tourMode", mode); } catch (e) { /* private mode */ }
    renderCard();
    if (mode === "auto" && run.shown) arm(); else clearTimeout(run.timer);
  }
  function togglePause() {
    run.paused = !run.paused;
    renderCard();
    if (run.paused) clearTimeout(run.timer); else if (run.shown) arm();
  }
  function hideSpot() { run.spot.hidden = true; run.placed = ""; }

  /** Called every animation frame: follow the targets' live position. */
  function frame() {
    if (!run) return;
    if (run.shown) {
      const rects = run.names.map(resolve).filter(isVisible).map((e) => e.getBoundingClientRect());
      place(rects.length ? unionRect(rects) : null);
    }
    run.raf = requestAnimationFrame(frame);
  }
  function place(target) {
    const vp = { w: window.innerWidth, h: window.innerHeight };
    const card = { w: run.card.offsetWidth, h: run.card.offsetHeight };
    const key = [target && [target.left, target.top, target.width, target.height].map(Math.round).join(","), vp.w, vp.h, card.w, card.h].join("|");
    if (key === run.placed) return;
    run.placed = key;
    const em = parseFloat(getComputedStyle(run.root).fontSize) || 16;
    let box = null;
    if (target) {
      box = clampRect(inflate(target, 0.45 * em), vp);
      Object.assign(run.spot.style, { left: `${box.left}px`, top: `${box.top}px`, width: `${box.width}px`, height: `${box.height}px` });
    }
    run.spot.hidden = !target;
    const pick = chooseCorner(S.card, card, vp, 1.1 * em, box && inflate(box, 0.6 * em));
    run.card.style.left = `${pick.rect.left}px`;
    run.card.style.top = `${pick.rect.top}px`;
  }

  // ---------- keys ----------

  function onKey(e) {
    if (!run) return;
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test((e.target && e.target.tagName) || "");
    let handled = true;
    if (e.key === "Escape") exit();
    else if (e.key === "PageDown" || (!typing && (e.key === "ArrowRight" || e.key === " "))) next();
    else if (e.key === "PageUp" || (!typing && e.key === "ArrowLeft")) prev();
    else if (!typing && (e.key === "a" || e.key === "A")) setMode(run.mode === "auto" ? "presenter" : "auto");
    else handled = false;
    if (handled) { e.preventDefault(); e.stopPropagation(); }
  }

  // ---------- start / exit ----------

  function start(id, opts = {}) {
    const tour = T.tours[id];
    if (!tour) return console.warn("tour: no tour", id);
    if (run) teardown();
    run = Object.assign(build(opts), { id, steps: tour.steps, mode: opts.mode === "auto" ? "auto" : "presenter",
      i: 0, token: 0, dir: 1, dirty: false, paused: false, shown: false, names: [], placed: "", timer: null, raf: 0 });
    document.addEventListener("keydown", onKey, true);
    run.raf = requestAnimationFrame(frame);
    const first = Math.min(Math.max((opts.step || 1) - 1, 0), tour.steps.length - 1);
    go(first, 1);
  }
  function teardown() {
    const r = run; run = null;
    cancelAnimationFrame(r.raf); clearTimeout(r.timer);
    r.root.remove();
    document.removeEventListener("keydown", onKey, true);
    return r;
  }
  function exit() {
    if (!run) return;
    if (T.funcs.beforeStep) T.funcs.beforeStep();
    const r = teardown();
    if (r.dirty && S.restore === "reload") {          // put the app back exactly as it was: reload without the tour parameters
      const u = new URL(location.href);
      ["tour", "mode", "step", "scale"].forEach((k) => u.searchParams.delete(k));
      location.replace(u.toString());
    }
  }

  // ---------- the button and its menu ----------

  function savedMode() { try { return localStorage.getItem("tourMode") === "auto" ? "auto" : "presenter"; } catch (e) { return "presenter"; } }
  function wireLauncher() {
    const btn = document.getElementById("tour-btn"), menu = document.getElementById("tour-menu");
    if (!btn || !menu) return;
    const note = document.getElementById("tour-note"), modes = [...menu.querySelectorAll("[data-tour-mode]")];
    let mode = savedMode();
    const paintModes = () => modes.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.tourMode === mode)));
    const close = () => { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); };
    paintModes();
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
      btn.setAttribute("aria-expanded", String(!menu.hidden));
    });
    modes.forEach((b) => b.addEventListener("click", () => { mode = b.dataset.tourMode; paintModes(); }));
    menu.querySelectorAll("[data-tour-start]").forEach((b) => b.addEventListener("click", () => {
      if (!T.funcs.isRealData()) { note.hidden = false; return; }
      note.hidden = true; close();
      start(b.dataset.tourStart, { mode });
    }));
    document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) close(); });
  }

  // ?tour=1|2 [&mode=auto] [&step=N] [&scale=1.3] starts a tour when the page has loaded
  async function autostart() {
    const q = new URLSearchParams(location.search), id = q.get("tour");
    if (!id) return;
    const t0 = Date.now();
    while (!T.funcs.isReady() && Date.now() - t0 < 60000) await sleep(200);
    await sleep(500);
    start(id, { mode: q.get("mode"), step: parseInt(q.get("step") || "1", 10), scale: parseFloat(q.get("scale")) || 0 });
  }

  wireLauncher();
  autostart();
  window.TourEngine = { start, exit, _geometry: { overlapArea, unionRect, inflate, clampRect, cornerOrder, cardRectAt, chooseCorner } };
})();
