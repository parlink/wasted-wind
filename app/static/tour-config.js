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
    "reveal.farm.title": "#farm-title",
    "reveal.farm.sub": "#farm-sub",
    "forecast.scope": "#week-scope",
    "forecast.backtest": "#backtest-chart",
    "ap.bars": "#ap-ratio-bars",
    "sim.place": "#place-battery",
    "sim.batteries": "#battery-list",
    "method.history": "#tab-method > .block:first-of-type",
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
    openMore: () => { const d = document.getElementById("ap-more"); if (d) d.open = true; },   // Batteries: "More options"
    setFarm: (id) => { if ((state.farm || null) !== (id || null)) selectFarm(id || state.farm); },   // select a farm (null: all farms)
    placeBatteryNear: (farmId) => {                                 // Simulate: a battery at a wind farm, as if clicked there (once)
      const f = state.farms.find((x) => x.farm_id === farmId);
      if (!f || state.sim.batteries.some((b) => Math.abs(b.lat - f.lat) < 1e-3 && Math.abs(b.lon - f.lon) < 1e-3)) return;
      setPlacing(true); onMapClickPlace({ lngLat: { lng: f.lon, lat: f.lat } }); setPlacing(false);
    },
  };

  const D = { storm: "2026-10-27", winter: "2025-02-20", battery: "2026-09-02", monthA: "2026-10-01", monthB: "2026-10-31", weekA: "2026-10-26", weekB: "2026-11-01" };

  const F = "seagreen";   // the farm both tours follow: the biggest waste on the biggest day (25.2 GWh, switched off all day)
  const TOURS = {
    // Tour 1 (HRT, "Best Use of Data for Predictions & Decision Making"): market + weather data -> a prediction ->
    // an automated decision, scored against reality.
    "1": { steps: [
      { id: "year", do: [["tab", "reveal"], ["click", "range.mode.year"]], show: ["reveal.hero", "reveal.tiles"],
        label: "In 2026 so far, Scotland's wind farms were paid to switch off this much clean power. This is what it cost", hold: 10000 },
      { id: "farm", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm], ["call", "setFarm", F]], show: ["reveal.farm.title", "reveal.farm.sub"],
        label: "Pick a day and a farm: on 27 October, the biggest day in our data, Seagreen was paid to switch off all day", hold: 10000 },
      { id: "farm-chart", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm], ["call", "setFarm", F]], show: "reveal.farm.chart",
        label: "The line is what Seagreen planned to make. The shaded gap is the power it was told to throw away", hold: 11000 },
      { id: "week", do: [["tab", "predict"], ["call", "setFarm", F], ["click", "forecast.week.day1"]], show: ["forecast.scope", "forecast.week", "forecast.hero"],
        label: "Our model forecasts this waste for every farm, up to six days ahead, and updates every hour from the latest weather", hold: 11000 },
      { id: "signals", do: [["tab", "predict"]], show: "forecast.shap",
        label: "What it learns from: weather forecasts, power-line limits, electricity demand and market prices, combined", hold: 9000 },
      { id: "storm", do: [["tab", "predict"], ["call", "setFarm", F], ["set", "tm.date", D.storm]], show: ["tm.tiles", "tm.chart"],
        label: "Tested on the past: for 27 October our forecast for Seagreen, made the day before, was close to what happened", hold: 12000 },
      { id: "accuracy", do: [["tab", "predict"]], show: "forecast.accuracy",
        label: "On months it had never seen, our model misses less than simple rules of thumb", hold: 9000 },
      { id: "gain", do: [["tab", "autopilot"]], show: ["ap.headline", "ap.headline.sub"],
        label: "From prediction to decision: a battery that follows our forecast earned about 27% more than one without it", hold: 11000 },
      { id: "compare", do: [["tab", "autopilot"]], show: "ap.bars",
        label: "Out of the most a battery could have earned, ours took a bigger share than one with no forecast", hold: 9000 },
      { id: "replay", do: [["tab", "autopilot"], ["set", "ap.battery", "moray_east_bess"], ["set", "ap.date", D.battery], ["wait", 1500], ["call", "apReplay"]], show: ["ap.strip", "ap.soc"],
        label: "Fully automatic: every half hour the computer decides to store wind, wait, or sell when the lines have room", hold: 18000 },
      { id: "log", do: [["tab", "autopilot"], ["set", "ap.battery", "moray_east_bess"], ["set", "ap.date", D.battery]], show: "ap.log",
        label: "Every decision is written down with its reason and the real price it sold at", hold: 8000 },
      { id: "batteries", do: [["tab", "simulate"], ["call", "placeBatteryNear", "seagreen"], ["call", "placeBatteryNear", "moray_east"]], show: ["sim.place", "sim.batteries"], dim: 0.1,
        label: "What if? We place two batteries next to wind farms, and the model replays history with them", hold: 10000 },
      { id: "cable", do: [["tab", "simulate"], ["call", "placeBatteryNear", "seagreen"], ["call", "placeBatteryNear", "moray_east"], ["set", "sim.link.zone", "north_scotland"], ["set", "sim.link.gw", "2"]], show: "sim.link", dim: 0.1,
        label: "Or build an undersea cable from the north of Scotland straight to England", hold: 9000 },
      { id: "result", do: [["tab", "simulate"], ["call", "placeBatteryNear", "seagreen"], ["call", "placeBatteryNear", "moray_east"], ["set", "sim.link.zone", "north_scotland"], ["set", "sim.link.gw", "2"], ["wait", 1500]], show: ["sim.value", "sim.delta"],
        label: "The answer in seconds: this much less wind would have been wasted, compared with what really happened", hold: 9000 },
      { id: "fair", do: [["tab", "method"]], show: "method.validation",
        label: "How we kept it fair: each month was predicted using only data from before it, then checked against reality", hold: 10000 },
    ] },

    // Tour 2 (G-Research, "Make the invisible visible"): a hidden quantity, how we found it, what we can do with it.
    "2": { steps: [
      { id: "year", do: [["tab", "reveal"], ["click", "range.mode.year"]], show: ["range.row", "reveal.hero"],
        label: "Scotland pays wind farms to switch off when its power lines are full. The yearly total is known; when, where and why are not", hold: 10000 },
      { id: "farm", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm], ["call", "setFarm", F]], show: ["reveal.farm.title", "reveal.farm.sub"],
        label: "We rebuilt it from millions of public grid records: on 27 October, Seagreen was switched off all day", hold: 10000 },
      { id: "farm-chart", do: [["tab", "reveal"], ["call", "setRange", D.storm, D.storm], ["call", "setFarm", F]], show: "reveal.farm.chart",
        label: "Now you can see it: the gap between what the farm planned to make and what it was allowed to make", hold: 11000 },
      { id: "week", do: [["tab", "predict"], ["call", "setFarm", F], ["click", "forecast.week.day1"]], show: ["forecast.scope", "forecast.week", "forecast.hero"],
        label: "Once it is visible, it can be predicted: the waste expected at Seagreen over the next six days", hold: 10000 },
      { id: "storm", do: [["tab", "predict"], ["call", "setFarm", F], ["set", "tm.date", D.storm]], show: ["tm.tiles", "tm.chart"],
        label: "Did we see the biggest day coming? Our forecast for Seagreen, made the day before, next to what really happened", hold: 12000 },
      { id: "history", do: [["tab", "predict"], ["call", "setFarm", F]], show: "forecast.backtest",
        label: "More than two years of forecasts for Seagreen, each made only with what was known the day before", hold: 10000 },
      { id: "accuracy", do: [["tab", "predict"]], show: "forecast.accuracy",
        label: "Compared with simple rules of thumb, our model misses less, on months it had never seen", hold: 9000 },
      { id: "gain", do: [["tab", "autopilot"]], show: ["ap.headline", "ap.headline.sub"],
        label: "What we can do with it: a battery that follows our forecast earned about 27% more than one without", hold: 11000 },
      { id: "replay", do: [["tab", "autopilot"], ["set", "ap.battery", "moray_east_bess"], ["set", "ap.date", D.battery], ["wait", 1500], ["call", "apReplay"]], show: ["ap.strip", "ap.soc"],
        label: "The battery runs itself: it stores wind that would be wasted and sells when the lines have room", hold: 18000 },
      { id: "full", do: [["tab", "autopilot"], ["set", "ap.battery", "moray_east_bess"], ["set", "ap.date", D.storm]], show: ["ap.strip", "ap.soc"],
        label: "A hidden limit: on the biggest day the battery was already full, so more storage would have saved more", hold: 10000 },
      { id: "batteries", do: [["tab", "simulate"], ["call", "placeBatteryNear", "seagreen"], ["call", "placeBatteryNear", "moray_east"]], show: ["sim.place", "sim.batteries"], dim: 0.1,
        label: "So we test more storage: two batteries placed next to wind farms", hold: 9000 },
      { id: "cable", do: [["tab", "simulate"], ["call", "placeBatteryNear", "seagreen"], ["call", "placeBatteryNear", "moray_east"], ["set", "sim.link.zone", "north_scotland"], ["set", "sim.link.gw", "2"]], show: "sim.link", dim: 0.1,
        label: "And a new undersea cable that skips both bottlenecks", hold: 8000 },
      { id: "result", do: [["tab", "simulate"], ["call", "placeBatteryNear", "seagreen"], ["call", "placeBatteryNear", "moray_east"], ["set", "sim.link.zone", "north_scotland"], ["set", "sim.link.gw", "2"], ["wait", 1500]], show: ["sim.value", "sim.delta"],
        label: "Replayed over real history: this much less wind would have been wasted", hold: 9000 },
      { id: "found", do: [["tab", "method"]], show: "method.history",
        label: "How we found it: the grid's switch-off orders, cross-checked against the wind at each turbine. The two agree", hold: 11000 },
      { id: "sources", do: [["tab", "method"]], show: "method.sources",
        label: "All from free public data: the electricity market, the grid operator and weather records. Anyone can rebuild it", hold: 9000 },
    ] },
  };

  window.TOUR = { settings: SETTINGS, targets: TARGETS, funcs: FUNCS, tours: TOURS };
})();
