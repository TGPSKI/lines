/*
 * Client-side runtime validation for split JS files.
 * No build tooling required.
 */
(function () {
  const SELF_CHECK_VERSION = "2.0";
  const WAIT_STEP_MS = 50;

  function setStatus(msg, kind) {
    const el = document.getElementById("uiSelfCheckStatus");
    if (!el) return;
    const color = kind === "ok" ? "#3fb950" : (kind === "warn" ? "#d29922" : "#f85149");
    el.innerHTML = `Self-check: <span style="color:${color}">${msg}</span>`;
  }

  function addResult(results, suite, check, ok, detail, severity) {
    results.push({
      suite,
      check,
      ok: !!ok,
      detail: detail || (ok ? "ok" : "failed"),
      severity: severity || (ok ? "info" : "error"),
    });
  }

  function resolveGlobalSymbol(name) {
    if (!name) return undefined;
    if (name in globalThis) return globalThis[name];
    try {
      return Function(`return (typeof ${name} !== "undefined") ? ${name} : undefined;`)();
    } catch {
      return undefined;
    }
  }

  function requireFn(name, results, suite) {
    const ok = typeof resolveGlobalSymbol(name) === "function";
    addResult(results, suite, `fn:${name}`, ok, ok ? "ok" : "missing function");
    return ok;
  }

  function requireEl(id, results, suite) {
    const ok = !!document.getElementById(id);
    addResult(results, suite, `dom:#${id}`, ok, ok ? "ok" : "missing element");
    return ok;
  }

  function requireValue(name, predicate, results, suite) {
    const value = resolveGlobalSymbol(name);
    let ok = false;
    try {
      ok = typeof predicate === "function" ? !!predicate(value) : !!value;
    } catch {
      ok = false;
    }
    addResult(results, suite, `value:${name}`, ok, ok ? "ok" : "missing or invalid value");
    return ok;
  }

  function waitFor(predicate, timeoutMs) {
    const timeout = Number(timeoutMs) || 2000;
    const start = Date.now();
    return new Promise(resolve => {
      function tick() {
        let ok = false;
        try {
          ok = !!predicate();
        } catch {
          ok = false;
        }
        if (ok) {
          resolve(true);
          return;
        }
        if (Date.now() - start >= timeout) {
          resolve(false);
          return;
        }
        setTimeout(tick, WAIT_STEP_MS);
      }
      tick();
    });
  }

  function createFile(name, text, type) {
    return new File([String(text || "")], name, { type: type || "text/plain" });
  }

  function fixtureNdjson() {
    const rows = [
      {
        event: "scenario_meta",
        tick_seconds: 3600,
        total_time_ticks: 4,
        arrivals: [{ iid: 1, tick: 0 }, { iid: 2, tick: 1 }, { iid: 3, tick: 2 }],
        scenario_metadata: {
          window_ticks: 4,
          window_hours: 4,
          calibration_targets: {
            arrival_profile: {
              scenario_start_hour: 16,
              peak_window_hours_utc: [16, 17],
            },
          },
        },
      },
      { event: "tick", tick: 0, open_mrs: 2, arrivals: [1, 2], transitions: [] },
      { event: "rebase", tick: 0, mr_iid: 1, pipeline_id: 100, pipeline_outcome: "success" },
      { event: "tick", tick: 1, open_mrs: 2, arrivals: [3], transitions: [{ mr_iid: 1, from: "pending", to: "running", pipeline_id: 100 }] },
      { event: "merge", tick: 1, mr_iid: 1 },
      { event: "tick", tick: 2, open_mrs: 2, arrivals: [], transitions: [{ mr_iid: 2, from: "pending", to: "stale", pipeline_id: 101 }] },
      { event: "merge", tick: 3, mr_iid: 2 },
      { event: "tick", tick: 3, open_mrs: 1, arrivals: [], transitions: [] },
    ];
    return `${rows.map(r => JSON.stringify(r)).join("\n")}\n`;
  }

  function fixtureCalibrationSummaryCsv() {
    return [
      "profile,mode,wall_seconds,throughput_rel_error_pct,extended_score_pct",
      "24h,fixed-240-480,10.0,2.1,3.3",
      "24h,adaptive,6.5,2.4,3.0",
    ].join("\n");
  }

  function fixtureMonteCarloCsv() {
    return [
      "run,policy,total_time_ticks,mrs_merged,throughput_merges_per_hour,rebase_calls",
      "1,top-k,100,20,4.2,15",
      "2,top-k,95,21,4.5,14",
      "1,active-cap,110,18,3.7,9",
      "2,active-cap,108,19,3.9,8",
    ].join("\n");
  }

  function runParserSmoke(results) {
    const suite = "parsers";
    if (typeof window.mqParseCsvRecords === "function") {
      const parsed = window.mqParseCsvRecords("a,b\n1,2\n\"3,4\",5\n");
      const ok = parsed && parsed.rows && parsed.rows.length === 2 && parsed.rows[1].a === "3,4";
      addResult(results, suite, "mqParseCsvRecords.quoted-comma", ok, ok ? "ok" : "unexpected CSV parse output");
    }

    if (typeof window.mqDetectCsvType === "function") {
      const kinds = [
        { label: "calibrationSummary", text: fixtureCalibrationSummaryCsv(), want: "calibrationSummary" },
        { label: "discrimination", text: "variant,rhs_minus_lhs_mph,lhs_policy\nx,1.2,top-k\n", want: "discrimination" },
        { label: "monteCarlo", text: fixtureMonteCarloCsv(), want: "monteCarlo" },
      ];
      kinds.forEach(k => {
        const got = window.mqDetectCsvType(k.text);
        addResult(results, suite, `mqDetectCsvType.${k.label}`, got === k.want, `expected ${k.want}, got ${got}`);
      });
    }

    if (typeof window.computeHourlyTimeline === "function") {
      const timeline = window.computeHourlyTimeline({
        events: [
          { event: "scenario_meta", tick_seconds: 3600, scenario_metadata: { window_ticks: 4, calibration_targets: { arrival_profile: { scenario_start_hour: 16, peak_window_hours_utc: [16, 17] } } } },
          { event: "tick", tick: 0, arrivals: [1, 2], transitions: [] },
          { event: "merge", tick: 0, mr_iid: 1 },
          { event: "tick", tick: 1, arrivals: [3], transitions: [] },
          { event: "merge", tick: 1, mr_iid: 2 },
        ],
        scenarioMeta: { tick_seconds: 3600, scenario_metadata: { window_ticks: 4 } },
        arrivalProfile: { scenario_start_hour: 16, peak_window_hours_utc: [16, 17] },
        tickSeconds: 3600,
        totalTicks: 4,
      });
      const okTimeline = timeline && Array.isArray(timeline.rows) && timeline.rows.length >= 4;
      addResult(results, suite, "computeHourlyTimeline.basic", !!okTimeline, okTimeline ? "ok" : "unexpected timeline output");
    }
  }

  async function runLifecycleSmoke(results) {
    const suite = "lifecycle";
    if (typeof window.hasLoadedData !== "function" || typeof window.loadFiles !== "function" || typeof window.unloadAllData !== "function") {
      addResult(results, suite, "preconditions", false, "required lifecycle functions missing");
      return;
    }
    if (window.hasLoadedData()) {
      addResult(results, suite, "fixture.load", true, "skipped: existing loaded data present", "warn");
      return;
    }

    const files = [
      createFile("selfcheck-fixture.ndjson", fixtureNdjson(), "application/x-ndjson"),
      createFile("selfcheck-calibration-summary.csv", fixtureCalibrationSummaryCsv(), "text/csv"),
      createFile("selfcheck-monte-carlo-summary.csv", fixtureMonteCarloCsv(), "text/csv"),
      createFile("selfcheck-scenario.yaml", "scenario_metadata:\n  window_hours: 4\n", "text/yaml"),
    ];

    window.loadFiles(files, { replaceByCategory: true });
    const loaded = await waitFor(() => window.hasLoadedData(), 5000);
    addResult(results, suite, "fixture.load", loaded, loaded ? "loaded fixture files" : "timed out waiting for load");
    if (!loaded) return;

    if (typeof window.switchTab === "function") {
      window.switchTab("bars");
      await waitFor(() => {
        const host = document.getElementById("barsLegendSeriesControls");
        return !!(host && host.querySelector("button"));
      }, 2000);
      const barsLegendOk = !!document.querySelector("#barsLegendSeriesControls button");
      addResult(results, suite, "bars.controls.render", barsLegendOk, barsLegendOk ? "ok" : "bars controls not populated");

      window.switchTab("stats");
      const statsOk = !!document.querySelector("#metricPicker button");
      addResult(results, suite, "stats.controls.render", statsOk, statsOk ? "ok" : "stats metric chips missing");
    }

    window.unloadAllData();
    const unloaded = await waitFor(() => !window.hasLoadedData(), 2000);
    addResult(results, suite, "fixture.unload", unloaded, unloaded ? "unload successful" : "unload did not clear loaded data");
  }

  function logResults(results, elapsedMs) {
    const failed = results.filter(r => !r.ok && r.severity !== "warn");
    const warned = results.filter(r => r.severity === "warn");
    const title = failed.length ? "[mqsim self-check] FAILED" : "[mqsim self-check] PASSED";
    console.group(`${title} v${SELF_CHECK_VERSION} (${elapsedMs}ms)`);
    results.forEach(r => {
      const status = r.ok ? "OK   " : (r.severity === "warn" ? "WARN " : "FAIL ");
      console.log(`${status}[${r.suite}] ${r.check} - ${r.detail}`);
    });
    console.groupEnd();
    return { failed, warned };
  }

  async function run() {
    const started = Date.now();
    setStatus(`running self-check v${SELF_CHECK_VERSION}...`, "warn");
    const results = [];

    [
      "dropZone",
      "runChipList",
      "simScenarioView",
      "simCalibrationView",
      "simMetadataView",
      "cmpTable",
      "expContent",
      "mcContent",
      "barsLegendControls",
      "kanbanControlRow",
      "swimlaneControlRow",
      "statsControlRow",
    ].forEach(id => {
      requireEl(id, results, "dom");
    });

    [
      "loadFiles",
      "unloadAllData",
      "renderStats",
      "renderSimulationTab",
      "renderExperiments",
      "renderMonteCarlo",
      "renderBarsTab",
      "renderKanbanTab",
      "renderSwimlaneTab",
      "mqParseCsvRecords",
      "mqDetectCsvType",
      "computeHourlyTimeline",
      "mqParseNumber",
      "mqParseNumberOrNull",
      "mqPaletteColor",
      "mqAverage",
      "mqFormatFixedOrDash",
      "mqThemedScale",
      "mqThemedLegend",
      "mqChartOptions",
      "mqChartOptionsSingleY",
    ].forEach(name => {
      requireFn(name, results, "api");
    });

    requireValue("MQSIM_POLICY_COLOR_PALETTE", function (v) { return Array.isArray(v) && v.length >= 6; }, results, "api");
    requireValue("MQSIM_LEGEND_FONT_SIZE", function (v) { return Number.isFinite(Number(v)) && Number(v) > 0; }, results, "api");

    runParserSmoke(results);
    await runLifecycleSmoke(results);

    const elapsed = Date.now() - started;
    const summary = logResults(results, elapsed);
    if (summary.failed.length) {
      setStatus(`${summary.failed.length} failure(s), ${summary.warned.length} warning(s). See console.`, "fail");
      return { ok: false, failed: summary.failed, warned: summary.warned, results: results, elapsedMs: elapsed };
    }
    setStatus(`${results.length} checks passed (${summary.warned.length} warning(s)) in ${elapsed}ms.`, summary.warned.length ? "warn" : "ok");
    return { ok: true, failed: [], warned: summary.warned, results: results, elapsedMs: elapsed };
  }

  window.runMqSimSelfCheck = run;

  document.addEventListener("DOMContentLoaded", function () {
    const btn = document.getElementById("btnRunUiSelfCheck");
    if (!btn) return;
    btn.addEventListener("click", function () { run(); });
  });
})();
