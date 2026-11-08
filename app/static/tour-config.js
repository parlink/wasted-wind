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
    busySelector: ".tab.loading:not([hidden]), #tm.loading",   // the app's own "still loading" markers (a tab, and the look-back card)
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

  const D = { storm: "2026-10-27", winter: "2025-02-20", battery: "2026-09-02", monthA: "2026-10-01", monthB: "2026-10-31", weekA: "2026-10-26", weekB: "2026-11-01" };

  const TOURS = {
    // Tour 1: forecast and automated decisions
    "1": { steps: [
      { id: "year", do: [["tab", "reveal"], ["click", "range.mode.year"]], show: ["reveal.hero", "reveal.tiles"],
        label: "When Scotland's power lines are full, wind farms are paid to switch off. This is the wind wasted in 2026 so far, and what it cost", hold: 9000 },
      { id: "cables", do: [["tab", "reveal"]], show: ["reveal.boundary", "reveal.binding"],
        label: "Why it happens: two power lines, B4 and B6, carry Scottish wind south. When one is full, farms north of it must stop", hold: 9000 },
      { id: "wind", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm], ["call", "ensureWind", true], ["call", "windPlay"]], show: "wind.panel", dim: 0.1,
        label: "The wind at every farm on 27 Oct 2026, the day Storm Boelo hit", hold: 11000 },
      { id: "week", do: [["call", "ensureWind", false], ["tab", "predict"], ["click", "forecast.week.day1"]], show: "forecast.week",
        label: "Our forecast: how much wind will be wasted tomorrow and on each of the next five days", hold: 8000 },
      { id: "outlook", do: [["tab", "predict"], ["click", "forecast.week.day4"]], show: ["forecast.week", "forecast.hero"],
        label: "Days 4 to 6 are dashed: further ahead, the forecast is less certain", hold: 9000 },
      { id: "lead", do: [["tab", "predict"]], show: "forecast.lead",
        label: "The further ahead we look, the bigger the miss, compared here with two simple guesses", hold: 11000 },
      { id: "accuracy", do: [["tab", "predict"]], show: "forecast.accuracy",
        label: "How accurate it is: our model misses far less than simple guesses, tested on months it had never seen", hold: 10000 },
      { id: "signals", do: [["tab", "predict"]], show: "forecast.shap",
        label: "What the forecast pays most attention to, such as the wind expected at each farm", hold: 8000 },
      { id: "storm", do: [["tab", "predict"], ["set", "tm.date", D.storm]], show: ["tm.tiles", "tm.chart"],
        label: "Storm Boelo: our forecast from the day before, next to what really happened", hold: 12000 },
      { id: "winter", do: [["tab", "predict"], ["set", "tm.date", D.winter]], show: ["tm.tiles", "tm.chart", "tm.note"],
        label: "February 2025: an older model, trained on less history, saw under half of this storm coming", hold: 12000 },
      { id: "headline", do: [["tab", "autopilot"]], show: ["ap.headline", "ap.headline.sub"],
        label: "Batteries can store wind that would be wasted. With our forecast, a battery earns more than without one", hold: 11000 },
      { id: "replay", do: [["tab", "autopilot"], ["set", "ap.battery", "moray_east_bess"], ["set", "ap.date", D.battery], ["wait", 1500], ["call", "apReplay"]], show: ["ap.strip", "ap.soc"],
        label: "One battery's day: every half hour it stores wasted wind, waits, or sells when the lines have room", hold: 18000 },
      { id: "log", do: [["tab", "autopilot"], ["set", "ap.battery", "moray_east_bess"], ["set", "ap.date", D.battery]], show: "ap.log",
        label: "Each decision says what the battery did, at what price, and why", hold: 7000 },
      { id: "full", do: [["tab", "autopilot"], ["set", "ap.battery", "moray_east_bess"], ["set", "ap.date", D.storm]], show: ["ap.strip", "ap.soc"],
        label: "On the storm day this battery was already full, so it couldn't store any more", hold: 10000 },
      { id: "planner", do: [["tab", "autopilot"], ["set", "ap.planner", "lp_band"]], show: ["ap.planner", "ap.headline"],
        label: "A smarter plan: prepare for the high end of the forecast, in case the storm is bigger than expected", hold: 9000 },
      { id: "cable", do: [["tab", "simulate"], ["set", "sim.link.zone", "north_scotland"], ["set", "sim.link.gw", "2"]], show: "sim.link", dim: 0.1,
        label: "What if we built an undersea cable taking power from the north straight to England?", hold: 9000 },
      { id: "cable-effect", do: [["tab", "simulate"], ["wait", 1500]], show: ["sim.value", "sim.delta"],
        label: "How much wind would still be wasted with that cable, compared with what really happened", hold: 8000 },
      { id: "tested", do: [["tab", "method"]], show: "method.validation",
        label: "How we tested it honestly: each month was forecast using only data from before it", hold: 10000 },
      { id: "sources", do: [["tab", "method"]], show: "method.sources",
        label: "All of this comes from free public data: the electricity market, the grid operator and the weather", hold: 7000 },
      { id: "limits", do: [["tab", "method"]], show: "method.limits",
        label: "What it can't do yet: our honest list of limits", hold: 9000 },
    ] },

    // Tour 2: the hidden quantity, how it is found, and what it enables
    "2": { steps: [
      { id: "year", do: [["tab", "reveal"], ["click", "range.mode.year"]], show: ["range.row", "reveal.hero"],
        label: "Wind farms paid to switch off in 2026 so far: clean power that was wasted", hold: 7000 },
      { id: "month", do: [["tab", "reveal"], ["call", "setRange", D.monthA, D.monthB]], show: ["range.row", "reveal.hero"],
        label: "Zoom in to one month: October 2026", hold: 6000 },
      { id: "week", do: [["tab", "reveal"], ["call", "setRange", D.weekA, D.weekB]], show: ["range.row", "reveal.hero"],
        label: "Zoom in again: the week of Storm Boelo", hold: 5000 },
      { id: "day", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm]], show: ["range.row", "reveal.hero"],
        label: "One day: 27 October 2026, when Storm Boelo hit", hold: 6000 },
      { id: "farms", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm]], show: "reveal.farms",
        label: "Where it happened: each farm's share of that day's waste", hold: 8000 },
      { id: "farm", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm], ["call", "ensureTopFarm"]], show: "reveal.farm.chart",
        label: "The biggest farm: the gap between what it planned and what it was allowed to make is the wasted wind", hold: 12000 },
      { id: "methods", do: [["tab", "reveal"], ["call", "setRange", D.monthA, D.monthB], ["call", "ensureTopFarm"]], show: "reveal.farm.ab",
        label: "We check every number two ways: the grid's switch-off orders, and the wind blowing through each turbine", hold: 11000 },
      { id: "why", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm]], show: ["reveal.boundary", "reveal.binding"],
        label: "Why: which of the two power lines was full", hold: 9000 },
      { id: "cost", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm]], show: "reveal.tiles",
        label: "The cost: payments to switch farms off, plus gas burned to replace the power", hold: 8000 },
      { id: "wind", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm], ["call", "ensureWind", true], ["call", "windPlay"]], show: "wind.panel", dim: 0.1,
        label: "The wind that day, farm by farm, hour by hour", hold: 11000 },
      { id: "check", do: [["call", "ensureWind", false], ["tab", "method"]], show: "method.calib",
        label: "Our simulator starts from what really happened, and matches it before you change anything", hold: 10000 },
      { id: "forecast", do: [["tab", "predict"], ["set", "tm.date", D.storm]], show: ["tm.tiles", "tm.chart"],
        label: "Predict it: our forecast, made the day before the storm", hold: 11000 },
      { id: "autopilot", do: [["tab", "autopilot"], ["set", "ap.battery", "moray_east_bess"], ["set", "ap.date", D.battery], ["wait", 1500], ["call", "apReplay"]], show: ["ap.strip", "ap.soc"],
        label: "Act on it: a battery's automatic plan for one day", hold: 18000 },
      { id: "cable", do: [["tab", "simulate"], ["set", "sim.link.zone", "north_scotland"], ["set", "sim.link.gw", "2"]], show: "sim.link", dim: 0.1,
        label: "Test a fix: an undersea cable from the north to England", hold: 8000 },
      { id: "cable-effect", do: [["tab", "simulate"], ["wait", 1500]], show: ["sim.value", "sim.delta"],
        label: "How much less wind would be wasted", hold: 7000 },
      { id: "limits", do: [["tab", "method"]], show: "method.limits",
        label: "What it can't do yet: our honest list of limits", hold: 9000 },
      { id: "sources", do: [["tab", "method"]], show: "method.sources",
        label: "Built entirely from free public data", hold: 7000 },
    ] },
  };

  window.TOUR = { settings: SETTINGS, targets: TARGETS, funcs: FUNCS, tours: TOURS };
})();
