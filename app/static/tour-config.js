/* Guided tour (#70): EVERYTHING that is specific to this app lives in this file.
   tour.js is a generic engine that only reads window.TOUR. If the UI changes:
     - an element moved or was renamed  -> edit its selector in TARGETS (one line)
     - an app function was renamed      -> edit FUNCS (a few small helpers)
     - a different date, order or wording -> edit the steps below
   Keep each TARGETS entry and each action on ONE line: tests/test_tour.py reads this file to check that
   every selector still exists in index.html and every step refers to a name defined here.

   Step:  { id, do: [actions], show: "target" | ["target", ...], label, hold }
     do     actions run in order, then the page settles (no tab is loading):
              ["tab", "reveal"]            switch tab (reveal | predict | simulate | autopilot | method)
              ["click", "target"]          click an element (a ring shows where)
              ["set", "target", "value"]   set an input/select and fire input + change
              ["call", "funcName", ...]    one of FUNCS below
              ["wait", ms]                 extra pause for a slow load
     show   the element(s) to spotlight (their union); a step whose target never appears is skipped
     label  a short, factual caption of what is on screen (no speaker script: that is docs/PITCH.md)
     hold   ms to stay on the step in auto mode (default SETTINGS.holdMs)
     dim    optional 0-1: how dark the page goes around the spotlight (default: tour.css); use ~0.12 when the point is on the map
   Every step sets the state it needs (tab, dates), so going back or jumping to a step always works. */
(() => {
  const SETTINGS = {
    card: "bottom-left",          // preferred corner of the caption card; it moves aside if it would cover the spotlight
    holdMs: 8000,                 // auto mode: default time per step
    targetWaitMs: 4000,           // how long to wait for a step's element before skipping the step
    settleMinMs: 400,             // pause after every action
    settleMaxMs: 6000,            // give up waiting for a load after this
    busySelector: ".tab.loading:not([hidden])",   // the app's own "still loading" marker
    scrollContainer: ".panel",    // what scrolls when a target is below the fold
    restore: "reload",            // "reload": leaving the tour reloads the page, so nothing is left changed
  };

  const TARGETS = {
    "map": "#map",
    "wind.panel": "#wind-ctl",
    "range.row": "#tab-reveal .date-row",
    "range.mode.year": "#tab-reveal .seg button[data-mode=\"year\"]",
    "range.start": "#range-start",
    "reveal.hero": "#tab-reveal .hero",
    "reveal.tiles": "#tab-reveal .tiles",
    "reveal.boundary": "#boundary-bar",
    "reveal.binding": "#binding-note",
    "reveal.farms": "#farm-bars",
    "reveal.farm.chart": "#farm-chart",
    "reveal.farm.ab": "#ab-chart",
    "forecast.week": "#week-strip",
    "forecast.week.day1": "#week-strip .week-day:nth-child(1)",
    "forecast.week.day4": "#week-strip .week-day:nth-child(4)",
    "forecast.hero": "#tab-predict .hero",
    "forecast.lead": "#lead-block",
    "forecast.accuracy": "#mae-bars",
    "forecast.shap": "#shap-bars",
    "tm.date": "#tm-date",
    "tm.tiles": "#tm .tiles",
    "tm.chart": "#tm-chart",
    "tm.note": "#tm-note",
    "ap.headline": "#ap-headline",
    "ap.headline.sub": "#ap-headline-sub",
    "ap.date": "#ap-date",
    "ap.battery": "#ap-battery",
    "ap.log": "#ap-log",
    "ap.planner": "#ap-planner",
    "ap.strip": "#ap-strip",
    "ap.soc": "#ap-soc-chart",
    "sim.link": "#link-lever",
    "sim.link.zone": "#link-zone",
    "sim.link.gw": "#link-gw",
    "sim.value": "#sim-value",
    "sim.delta": "#sim-delta",
    "method.validation": "#method-validation",
    "method.calib": "#method-calib",
    "method.limits": "#tab-method > .block:nth-last-of-type(2)",
    "method.sources": "#tab-method > .block:last-of-type",
  };

  const FUNCS = {
    // ----- the engine needs these four -----
    tab: (t) => setTab(t),
    isRealData: () => $("fake-banner").hidden,                      // the tour's numbers exist only in the released data
    isReady: () => !!(state.farms && state.farms.length) && !document.querySelector(".tab.loading:not([hidden])"),
    beforeStep: () => {                                             // stop any replay left running by the previous step
      if (state.ap && state.ap.timer) stopApReplay();
      if (state.wind && state.wind.timer) $("wind-play").click();
    },
    // ----- app helpers used by steps below -----
    setRange: (a, b) => {                                           // History range: from a to b (inclusive ISO dates)
      $("range-start").value = a;
      $("range-end").hidden = false;                                // the app hides it for a single day, then ignores it
      $("range-end").value = b;
      $("range-end").dispatchEvent(new Event("change", { bubbles: true }));
    },
    ensureTopFarm: () => { if (!state.farm) { const bar = document.querySelector("#farm-bars .hbar"); if (bar) bar.click(); } },
    ensureWind: (on) => { if (!!state.wind.on !== on) $("wind-toggle").click(); },
    windPlay: () => { if (!state.wind.timer) $("wind-play").click(); },
    apReplay: () => { if (!state.ap.timer) toggleApReplay(); },
  };

  const D = { storm: "2026-10-27", winter: "2025-02-20", battery: "2026-09-02", monthA: "2026-10-01", monthB: "2026-10-31", weekA: "2026-10-25", weekB: "2026-10-31" };

  const TOURS = {
    // Tour 1: forecast and automated decisions
    "1": { steps: [
      { id: "year", do: [["tab", "reveal"], ["click", "range.mode.year"]], show: ["reveal.hero", "reveal.tiles"],
        label: "Wind switched off so far in 2026, and what it cost", hold: 9000 },
      { id: "cables", do: [["tab", "reveal"]], show: ["reveal.boundary", "reveal.binding"],
        label: "Which cable was full: B4, from north to south Scotland, or B6, from Scotland to England", hold: 9000 },
      { id: "wind", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm], ["call", "ensureWind", true], ["call", "windPlay"]], show: "wind.panel", dim: 0.1,
        label: "Wind speed and direction at each farm on 27 Oct 2026, hour by hour", hold: 11000 },
      { id: "week", do: [["call", "ensureWind", false], ["tab", "predict"], ["click", "forecast.week.day1"]], show: "forecast.week",
        label: "Tomorrow and the five days after it, refreshed every hour", hold: 8000 },
      { id: "outlook", do: [["tab", "predict"], ["click", "forecast.week.day4"]], show: ["forecast.week", "forecast.hero"],
        label: "Dashed days are an outlook: less reliable than the first three", hold: 9000 },
      { id: "lead", do: [["tab", "predict"]], show: "forecast.lead",
        label: "How the average miss grows with days ahead, against two simple alternatives", hold: 11000 },
      { id: "accuracy", do: [["tab", "predict"]], show: "forecast.accuracy",
        label: "Average miss per farm-hour against three simple baselines, on months the model never trained on", hold: 10000 },
      { id: "signals", do: [["tab", "predict"]], show: "forecast.shap",
        label: "The inputs the forecast leans on most", hold: 8000 },
      { id: "storm", do: [["tab", "predict"], ["set", "tm.date", D.storm]], show: ["tm.tiles", "tm.chart"],
        label: "27 Oct 2026: the forecast as made at 10:00 the day before, next to what happened", hold: 12000 },
      { id: "winter", do: [["tab", "predict"], ["set", "tm.date", D.winter]], show: ["tm.tiles", "tm.chart", "tm.note"],
        label: "20 Feb 2025: an earlier model with less history forecast under half of this storm", hold: 12000 },
      { id: "headline", do: [["tab", "autopilot"]], show: ["ap.headline", "ap.headline.sub"],
        label: "Share of the best possible battery earnings with our forecast and without one, on three months held out from tuning", hold: 11000 },
      { id: "replay", do: [["tab", "autopilot"], ["set", "ap.battery", "moray_east_bess"], ["set", "ap.date", D.battery], ["wait", 1500], ["call", "apReplay"]], show: ["ap.strip", "ap.soc"],
        label: "One battery on 2 Sep 2026: charge, hold or discharge each half hour", hold: 18000 },
      { id: "log", do: [["tab", "autopilot"], ["set", "ap.battery", "moray_east_bess"], ["set", "ap.date", D.battery]], show: "ap.log",
        label: "Every decision is logged with its reason", hold: 7000 },
      { id: "full", do: [["tab", "autopilot"], ["set", "ap.battery", "moray_east_bess"], ["set", "ap.date", D.storm]], show: ["ap.strip", "ap.soc"],
        label: "27 Oct 2026, the biggest storm: the battery was already full all day, so none of that waste could be stored", hold: 10000 },
      { id: "planner", do: [["tab", "autopilot"], ["set", "ap.planner", "lp_band"]], show: ["ap.planner", "ap.headline"],
        label: "Planning for the high end of the forecast's range instead of its middle", hold: 9000 },
      { id: "cable", do: [["tab", "simulate"], ["set", "sim.link.zone", "north_scotland"], ["set", "sim.link.gw", "2"]], show: "sim.link", dim: 0.1,
        label: "What if a 2 GW undersea cable took power from the north straight to England?", hold: 9000 },
      { id: "cable-effect", do: [["tab", "simulate"], ["wait", 1500]], show: ["sim.value", "sim.delta"],
        label: "Wasted wind with the cable, against what really happened", hold: 8000 },
      { id: "tested", do: [["tab", "method"]], show: "method.validation",
        label: "How the forecast was tested: each month forecast using only data from before it", hold: 10000 },
      { id: "sources", do: [["tab", "method"]], show: "method.sources",
        label: "The public data behind it", hold: 7000 },
      { id: "limits", do: [["tab", "method"]], show: "method.limits",
        label: "What this does not do yet", hold: 9000 },
    ] },

    // Tour 2: the hidden quantity, how it is found, and what it enables
    "2": { steps: [
      { id: "year", do: [["tab", "reveal"], ["click", "range.mode.year"]], show: ["range.row", "reveal.hero"],
        label: "Wind switched off so far in 2026", hold: 7000 },
      { id: "month", do: [["tab", "reveal"], ["call", "setRange", D.monthA, D.monthB]], show: ["range.row", "reveal.hero"],
        label: "Zooming in: October 2026", hold: 6000 },
      { id: "week", do: [["tab", "reveal"], ["call", "setRange", D.weekA, D.weekB]], show: ["range.row", "reveal.hero"],
        label: "Zooming in again: one week", hold: 5000 },
      { id: "day", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm]], show: ["range.row", "reveal.hero"],
        label: "One day: 27 Oct 2026", hold: 6000 },
      { id: "farms", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm]], show: "reveal.farms",
        label: "Where: each farm's share of that day's waste", hold: 8000 },
      { id: "farm", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm], ["call", "ensureTopFarm"]], show: "reveal.farm.chart",
        label: "The biggest farm: planned and delivered output, half hour by half hour; the gap is the wasted wind", hold: 12000 },
      { id: "methods", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm], ["call", "ensureTopFarm"]], show: "reveal.farm.ab",
        label: "Checked two independent ways: the grid's switch-off instructions, and wind speed through the farm's power curve", hold: 11000 },
      { id: "why", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm]], show: ["reveal.boundary", "reveal.binding"],
        label: "Why: which of the two cables was full", hold: 9000 },
      { id: "cost", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm]], show: "reveal.tiles",
        label: "What the switch-offs cost: payments to farms, and the gas burned instead", hold: 8000 },
      { id: "wind", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm], ["call", "ensureWind", true], ["call", "windPlay"]], show: "wind.panel", dim: 0.1,
        label: "Wind speed and direction through the day, farm by farm", hold: 11000 },
      { id: "check", do: [["call", "ensureWind", false], ["tab", "method"]], show: "method.calib",
        label: "Check: with every setting at its historical value, the simulator reproduces the measured waste", hold: 10000 },
      { id: "forecast", do: [["tab", "predict"], ["set", "tm.date", D.storm]], show: ["tm.tiles", "tm.chart"],
        label: "Forecast it: the prediction made the day before, 27 Oct 2026", hold: 11000 },
      { id: "autopilot", do: [["tab", "autopilot"], ["set", "ap.battery", "moray_east_bess"], ["set", "ap.date", D.battery], ["wait", 1500], ["call", "apReplay"]], show: ["ap.strip", "ap.soc"],
        label: "Act on it: an automatic battery plan for one day, 2 Sep 2026", hold: 18000 },
      { id: "cable", do: [["tab", "simulate"], ["set", "sim.link.zone", "north_scotland"], ["set", "sim.link.gw", "2"]], show: "sim.link", dim: 0.1,
        label: "Test a fix: a 2 GW undersea cable from the north", hold: 8000 },
      { id: "cable-effect", do: [["tab", "simulate"], ["wait", 1500]], show: ["sim.value", "sim.delta"],
        label: "The effect on wasted wind", hold: 7000 },
      { id: "limits", do: [["tab", "method"]], show: "method.limits",
        label: "What this does not do yet", hold: 9000 },
      { id: "sources", do: [["tab", "method"]], show: "method.sources",
        label: "Public data only", hold: 7000 },
    ] },
  };

  window.TOUR = { settings: SETTINGS, targets: TARGETS, funcs: FUNCS, tours: TOURS };
})();
