#!/usr/bin/env node
/* Plays a guided tour (#70) in a real headless Chrome and saves one screenshot per step, so you can SEE every step
   after changing the UI or the tour. It presses the presenter's "next" key between steps, so it also proves that
   going through the whole tour works end to end. Needs Node 22+ and Chrome or Edge; nothing to install.

     node tools/tour_screenshots.js --tour 1                       # tour 1 on http://localhost:8000
     node tools/tour_screenshots.js --tour 2 --size 2560x1440      # a projector-sized window
     node tools/tour_screenshots.js --tour 1 --url http://localhost:8768 --out tour-shots --scale 1.3

   Options: --url  --tour 1|2  --size WxH (1600x900)  --out DIR (tour-shots)  --chrome PATH  --scale N (tour size multiplier)
            --motion (keep animations; by default they are reduced so numbers are not caught mid-count)
   Prints one line per step and a summary. Exit code 1 if a step is skipped, has no spotlight, or the card covers it. */
const { spawn } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path");

const arg = (n, d) => { const i = process.argv.indexOf(`--${n}`); return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const flag = (n) => process.argv.includes(`--${n}`);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const URL_ = arg("url", "http://localhost:8000"), TOUR = arg("tour", "1"), OUT = arg("out", "tour-shots");
const [W, H] = arg("size", "1600x900").split("x").map(Number), SCALE = arg("scale", "");

function findChrome() {
  const c = [arg("chrome", ""), process.env.CHROME, "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"];
  return c.find((p) => p && fs.existsSync(p));
}

class CDP {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map(); this.handlers = [];
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.id && this.pending.has(m.id)) { const { ok, bad } = this.pending.get(m.id); this.pending.delete(m.id); m.error ? bad(new Error(m.error.message)) : ok(m.result); }
      else if (m.method) this.handlers.forEach((h) => h(m));
    };
  }
  send(method, params = {}) { const id = ++this.id; this.ws.send(JSON.stringify({ id, method, params })); return new Promise((ok, bad) => this.pending.set(id, { ok, bad })); }
  on(h) { this.handlers.push(h); }
  async eval(expr) { const r = await this.send("Runtime.evaluate", { expression: expr, returnByValue: true }); return r.result.value; }
}

const SAMPLE = `(() => {
  const c = document.querySelector('.tour-card'); if (!c) return null;
  const s = document.querySelector('.tour-spot'), sr = s.getBoundingClientRect(), cr = c.getBoundingClientRect();
  const box = (r) => [r.left, r.top, r.width, r.height].map(Math.round);
  return { count: document.querySelector('.tour-count').textContent, label: document.querySelector('.tour-label').textContent,
           spot: s.hidden ? null : box(sr), card: box(cr) };
})()`;

const overlap = (a, b) => { const w = Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]), h = Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]); return w > 0 && h > 0 ? w * h : 0; };

async function main() {
  const chrome = findChrome();
  if (!chrome) { console.error("No Chrome or Edge found: pass --chrome PATH"); process.exit(2); }
  fs.mkdirSync(OUT, { recursive: true });
  const port = 9300 + Math.floor(Math.random() * 500), profile = fs.mkdtempSync(path.join(os.tmpdir(), "tour-"));
  const proc = spawn(chrome, ["--headless=new", "--disable-gpu", "--hide-scrollbars", `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`, `--window-size=${W},${H}`, "about:blank"], { stdio: "ignore" });
  const quit = () => { try { proc.kill(); } catch (e) { /* gone */ } };
  process.on("exit", quit);
  let target = null;
  for (let i = 0; i < 60 && !target; i++) {
    try { target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find((t) => t.type === "page"); } catch (e) { /* not up yet */ }
    if (!target) await sleep(250);
  }
  if (!target) { console.error("Chrome did not start"); quit(); process.exit(2); }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((ok, bad) => { ws.onopen = ok; ws.onerror = bad; });
  const cdp = new CDP(ws), warnings = [];
  cdp.on((m) => {
    if (m.method === "Runtime.consoleAPICalled" && m.params.type === "warning") {
      const t = m.params.args.map((a) => a.value || a.description || "").join(" ");
      if (t.startsWith("tour:")) warnings.push(t);
    }
  });
  await cdp.send("Runtime.enable"); await cdp.send("Page.enable");
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  if (!flag("motion")) await cdp.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  await cdp.send("Page.navigate", { url: `${URL_}/?tour=${TOUR}${SCALE ? `&scale=${SCALE}` : ""}` });

  const rows = [], problems = [];
  let last = null;
  for (let n = 1; n <= 60; n++) {
    // wait for a new step with its spotlight up and nothing moving for ~0.8 s (a step's actions can take several
    // seconds while the page loads); if no spotlight ever appears the step is reported as having none
    let cur = null, prev = null, stable = 0;
    const t0 = Date.now();
    while (Date.now() - t0 < 25000) {
      cur = await cdp.eval(SAMPLE);
      if (cur && cur.count !== last && cur.spot && JSON.stringify(cur) === JSON.stringify(prev)) { if (++stable >= 3) break; } else stable = 0;
      prev = cur;
      await sleep(300);
    }
    if (!cur || cur.count === last) break;                   // the tour is over
    last = cur.count;
    const file = path.join(OUT, `tour${TOUR}-${String(rows.length + 1).padStart(2, "0")}.png`);
    const shot = await cdp.send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(file, Buffer.from(shot.data, "base64"));
    const issues = [];
    if (!cur.spot) issues.push("no spotlight");
    else if (overlap(cur.spot, cur.card) > 0) issues.push("card covers the spotlight");
    rows.push({ step: cur.count, label: cur.label, spot: cur.spot, card: cur.card, issues });
    console.log(`${cur.count.padEnd(7)} ${issues.length ? "!! " + issues.join(", ") + "  " : "ok  "}${cur.label}`);
    if (issues.length) problems.push(`${cur.count}: ${issues.join(", ")}`);
    const total = parseInt(cur.count.split("/")[1], 10);
    await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key: "PageDown", code: "PageDown", windowsVirtualKeyCode: 34 });
    await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: "PageDown", code: "PageDown", windowsVirtualKeyCode: 34 });
    if (parseInt(cur.count, 10) >= total) { await sleep(2500); break; }
  }
  const skipped = warnings.filter((w) => w.includes("skipping") || w.includes("cannot reach") || w.includes("unknown"));
  skipped.forEach((w) => problems.push(w));
  fs.writeFileSync(path.join(OUT, `tour${TOUR}-report.json`), JSON.stringify({ size: [W, H], rows, warnings }, null, 2));
  console.log(`\n${rows.length} steps captured in ${OUT}/` + (problems.length ? `; ${problems.length} problem(s):\n  ${problems.join("\n  ")}` : "; no problems"));
  quit();
  process.exit(problems.length ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(2); });
