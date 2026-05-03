/*
 * Split from ui/index.html for maintainability.
 * Client-side only: plain browser JavaScript, no build step.
 */

// Experiments Tab (Calibration + Discrimination)
// =========================================================================
function destroyExperimentCharts() {
  [expDiscDeltaChart, expDiscRebaseChart, expCalErrorChart, expCalScatterChart, expCalDimChart, expCalBenchRuntimeChart, expCalBenchScoreChart].forEach(ch => {
    mqDestroyChartIfPresent(ch);
  });
  expDiscDeltaChart = null;
  expDiscRebaseChart = null;
  expCalErrorChart = null;
  expCalScatterChart = null;
  expCalDimChart = null;
  expCalBenchRuntimeChart = null;
  expCalBenchScoreChart = null;
}

function expMean(values) {
  return mqAverage(values, 0);
}

function expFormatOrDash(value, digits = 2) {
  return mqFormatFixedOrDash(value, digits);
}

function expSingleYChartOptions(yOverrides = {}, overrides = {}) {
  return mqChartOptionsSingleY(yOverrides, overrides);
}

function expModeKind(mode) {
  const m = String(mode || "").toLowerCase();
  if (m.includes("adaptive")) return "adaptive";
  if (m.includes("fixed")) return "fixed";
  return m || "other";
}

function expParseJsonObject(raw, fallback = {}) {
  try {
    const obj = JSON.parse(raw || "{}");
    return obj && typeof obj === "object" ? obj : fallback;
  } catch {
    return fallback;
  }
}

function expPolicyColor(policy, policyList) {
  const idx = Math.max(0, (policyList || []).indexOf(policy));
  return mqPaletteColor(idx);
}

function loadDiscriminationCSVText(text) {
  const { rows } = mqParseCsvRecords(text);
  const parsePoliciesCsv = raw => (raw || "")
    .split(",")
    .map(s => s.trim())
    .filter(Boolean);
  const mapped = rows.map(r => {
    const variant = r.variant || r.title || "unknown";
    const title = r.title || r.variant || "unknown";
    const description = r.description || "";
    const lhsPolicy = (r.lhs_policy || "top-k").trim();
    const rhsPolicy = (r.rhs_policy || "active-cap").trim();
    const baselinePolicy = (r.baseline_policy || "").trim();
    const policyMetrics = {};
    try {
      const parsed = JSON.parse(r.policy_metrics_json || "{}");
      Object.entries(parsed || {}).forEach(([policy, metrics]) => {
        policyMetrics[policy] = {
          throughput_mph: mqParseNumber(metrics?.throughput_mph, 0),
          merged: mqParseNumber(metrics?.merged, 0),
          rebases: mqParseNumber(metrics?.rebases, 0),
          starved: mqParseNumber(metrics?.starved, 0)
        };
      });
    } catch {
      // Legacy files will not have policy_metrics_json.
    }
    if (!policyMetrics["top-k"] && (r.top_k_mph || r.top_k_rebases || r.top_k_starved)) {
      policyMetrics["top-k"] = {
        throughput_mph: mqParseNumber(r.top_k_mph, 0),
        merged: mqParseNumber(r.top_k_merged, 0),
        rebases: mqParseNumber(r.top_k_rebases, 0),
        starved: mqParseNumber(r.top_k_starved, 0)
      };
    }
    if (!policyMetrics["active-cap"] && (r.active_cap_mph || r.active_cap_rebases || r.active_cap_starved)) {
      policyMetrics["active-cap"] = {
        throughput_mph: mqParseNumber(r.active_cap_mph, 0),
        merged: mqParseNumber(r.active_cap_merged, 0),
        rebases: mqParseNumber(r.active_cap_rebases, 0),
        starved: mqParseNumber(r.active_cap_starved, 0)
      };
    }
    if (!policyMetrics["old-burst"] && (r.old_burst_mph || r.old_burst_rebases || r.old_burst_starved)) {
      policyMetrics["old-burst"] = {
        throughput_mph: mqParseNumber(r.old_burst_mph, 0),
        merged: mqParseNumber(r.old_burst_merged, 0),
        rebases: mqParseNumber(r.old_burst_rebases, 0),
        starved: mqParseNumber(r.old_burst_starved, 0)
      };
    }

    const policies = [];
    const addPolicy = p => {
      if (p && !policies.includes(p)) policies.push(p);
    };
    parsePoliciesCsv(r.policies_csv).forEach(addPolicy);
    Object.keys(policyMetrics).forEach(addPolicy);
    addPolicy(lhsPolicy);
    addPolicy(rhsPolicy);
    addPolicy(baselinePolicy);

    const lhsThroughput = mqParseNumber(
      r.lhs_throughput_mph,
      mqParseNumber(policyMetrics[lhsPolicy]?.throughput_mph, 0)
    );
    const rhsThroughput = mqParseNumber(
      r.rhs_throughput_mph,
      mqParseNumber(policyMetrics[rhsPolicy]?.throughput_mph, 0)
    );
    const rhsMinusLhsRaw = mqParseNumberOrNull(r.rhs_minus_lhs_mph);
    const legacyDeltaRaw = mqParseNumberOrNull(r.ac_minus_tk_mph);
    const deltaMph = rhsMinusLhsRaw != null
      ? rhsMinusLhsRaw
      : (legacyDeltaRaw != null ? legacyDeltaRaw : rhsThroughput - lhsThroughput);
    const lhsRebases = mqParseNumber(
      r.lhs_rebases,
      mqParseNumber(policyMetrics[lhsPolicy]?.rebases, mqParseNumber(r.top_k_rebases, 0))
    );
    const rhsRebases = mqParseNumber(
      r.rhs_rebases,
      mqParseNumber(policyMetrics[rhsPolicy]?.rebases, mqParseNumber(r.active_cap_rebases, 0))
    );
    const lhsStarved = mqParseNumber(
      r.lhs_starved,
      mqParseNumber(policyMetrics[lhsPolicy]?.starved, mqParseNumber(r.top_k_starved, 0))
    );
    const rhsStarved = mqParseNumber(
      r.rhs_starved,
      mqParseNumber(policyMetrics[rhsPolicy]?.starved, mqParseNumber(r.active_cap_starved, 0))
    );
    const rebaseSavings = mqParseNumber(
      r.rebase_savings,
      mqParseNumber(r.ac_rebase_savings, lhsRebases - rhsRebases)
    );

    return {
      variant,
      title,
      description,
      lhsPolicy,
      rhsPolicy,
      baselinePolicy,
      policies,
      policyMetrics,
      lhsThroughput,
      rhsThroughput,
      deltaMph,
      lhsRebases,
      rhsRebases,
      lhsStarved,
      rhsStarved,
      rebaseSavings,
      raw_compare: r.raw_compare || ""
    };
  });
  const meta = mapped.length ? {
    lhsPolicy: mapped[0].lhsPolicy,
    rhsPolicy: mapped[0].rhsPolicy,
    baselinePolicy: mapped[0].baselinePolicy || ""
  } : null;
  expData.discrimination = mapped;
  expData.discriminationMeta = meta;
}

function loadCalibrationCSVText(text, fileName = "") {
  const { rows } = mqParseCsvRecords(text);
  const mapped = rows.map((r, i) => ({
    phase: (r.phase || "").toLowerCase(),
    policy: (r.policy || "old-burst").trim(),
    idx: mqParseNumber(r.candidate_idx || r.validation_rank, i + 1),
    tick_seconds: mqParseNumber(r.tick_seconds),
    arrival_skew: mqParseNumber(r.arrival_skew),
    queue_depth_scale: mqParseNumber(r.queue_depth_scale),
    ci_duration_scale: mqParseNumber(r.ci_duration_scale, null),
    throughput_mph: mqParseNumber(r.throughput_mph),
    throughput_active_mph: mqParseNumber(r.throughput_active_mph),
    throughput_peak8_mph: mqParseNumber(r.throughput_peak8_mph),
    throughput_peak8_p90_mph: mqParseNumber(r.throughput_peak8_p90_mph),
    throughput_peak_window_mph: mqParseNumber(r.throughput_peak_window_mph),
    throughput_offpeak_mph: mqParseNumber(r.throughput_offpeak_mph),
    peak_offpeak_ratio: mqParseNumber(r.peak_offpeak_ratio),
    modeled_hours: mqParseNumber(r.modeled_hours),
    total_arrivals: mqParseNumber(r.total_arrivals),
    rebase_per_merge: mqParseNumber(r.rebase_per_merge),
    merge_interval_p50_seconds: mqParseNumber(r.merge_interval_p50_seconds),
    merge_interval_p95_seconds: mqParseNumber(r.merge_interval_p95_seconds),
    merged: mqParseNumber(r.merged),
    rel_error_pct: mqParseNumber(r.rel_error_pct),
    score_pct: mqParseNumber(r.score_pct, mqParseNumber(r.rel_error_pct)),
    throughput_rel_error_pct: mqParseNumber(r.throughput_rel_error_pct, mqParseNumber(r.rel_error_pct)),
    target_mph: mqParseNumber(r.target_mph),
    cycles: mqParseNumber(r.cycles),
    is_best_240: mqParseNumber(r.is_best_240),
    is_best_480: mqParseNumber(r.is_best_480),
    source_240_rel_error_pct: mqParseNumber(r.source_240_rel_error_pct),
    source_240_score_pct: mqParseNumber(r.source_240_score_pct, mqParseNumber(r.source_240_rel_error_pct)),
    throughput_gate_pass: mqParseNumber(r.throughput_gate_pass),
    score_gate_pass: mqParseNumber(r.score_gate_pass),
    max_dimension_gate_pass: mqParseNumber(r.max_dimension_gate_pass),
    accepted_by_gates: mqParseNumber(r.accepted_by_gates),
    dimension_error_pct_json: r.dimension_error_pct_json || "{}",
    target_dimensions_json: r.target_dimensions_json || "{}"
  }));
  let tuneRows = mapped.filter(r => r.phase === "tune");
  let valRows = mapped.filter(r => r.phase === "validate");
  const lower = fileName.toLowerCase();
  if (!tuneRows.length && !valRows.length) {
    if (lower.includes("validation")) valRows = mapped;
    else tuneRows = mapped;
  }
  if (tuneRows.length) expData.calibrationGrid = tuneRows;
  if (valRows.length) expData.calibrationValidation = valRows;
}

function loadCalibrationSummaryCSVText(text, fileName = "") {
  const { rows } = mqParseCsvRecords(text);
  const mapped = rows.map(r => ({
    profile: (r.profile || "").trim(),
    mode: (r.mode || "").trim(),
    wall_seconds: mqParseNumber(r.wall_seconds),
    tune_cycles_used: mqParseNumber(r.tune_cycles_used),
    validate_cycles_used: mqParseNumber(r.validate_cycles_used),
    throughput_mph: mqParseNumber(r.throughput_mph),
    throughput_rel_error_pct: mqParseNumber(r.throughput_rel_error_pct),
    standard_score_pct: mqParseNumber(r.standard_score_pct),
    extended_score_pct: mqParseNumber(r.extended_score_pct, mqParseNumber(r.rank_score_pct)),
    max_dimension_error_pct: mqParseNumber(r.max_dimension_error_pct),
    rank_score_pct: mqParseNumber(r.rank_score_pct, mqParseNumber(r.extended_score_pct)),
    merged: mqParseNumber(r.merged),
    source_file: fileName,
  })).filter(r => r.profile && r.mode);
  const existing = new Map();
  expData.calibrationSummary.forEach(r => {
    existing.set(`${r.profile}::${r.mode}`, r);
  });
  mapped.forEach(r => {
    existing.set(`${r.profile}::${r.mode}`, r);
  });
  expData.calibrationSummary = Array.from(existing.values());
}

function renderExperiments() {
  const hasDisc = Array.isArray(expData.discrimination) && expData.discrimination.length > 0;
  const hasCal = expData.calibrationGrid.length > 0 || expData.calibrationValidation.length > 0 || expData.calibrationSummary.length > 0;
  const hintEl = document.getElementById("expHint");
  const content = document.getElementById("expContent");
  const btnD = document.getElementById("expModeDiscrimination");
  const btnC = document.getElementById("expModeCalibration");

  btnD.disabled = !hasDisc;
  btnC.disabled = !hasCal;
  if (!hasDisc && !hasCal) {
    destroyExperimentCharts();
    content.innerHTML = `<div class="kanban-empty">Load discrimination or calibration CSV files to visualize experiment runs.</div>`;
    hintEl.textContent = "";
    return;
  }
  if (expMode === "discrimination" && !hasDisc) expMode = "calibration";
  if (expMode === "calibration" && !hasCal) expMode = "discrimination";
  btnD.classList.toggle("active", expMode === "discrimination");
  btnC.classList.toggle("active", expMode === "calibration");
  destroyExperimentCharts();

  if (expMode === "discrimination") {
    const lhs = expData.discriminationMeta?.lhsPolicy || "lhs";
    const rhs = expData.discriminationMeta?.rhsPolicy || "rhs";
    hintEl.textContent = `Variant-level performance deltas: ${rhs} minus ${lhs}.`;
    renderDiscriminationExperiments(content);
  } else {
    const hasBenchmarkSummary = expData.calibrationSummary.length > 0;
    hintEl.textContent = hasBenchmarkSummary
      ? "Calibration benchmark summary + candidate-level tuning/validation detail."
      : "Calibration grid search and 480-cycle validation outcome.";
    renderCalibrationExperiments(content);
  }
}

function renderDiscriminationExperiments(content) {
  const rows = [...(expData.discrimination || [])];
  rows.sort((a, b) => a.variant.localeCompare(b.variant));
  if (!rows.length) {
    content.innerHTML = `<div class="kanban-empty">No discrimination rows loaded.</div>`;
    return;
  }
  const lhsPolicy = rows[0].lhsPolicy || expData.discriminationMeta?.lhsPolicy || "lhs";
  const rhsPolicy = rows[0].rhsPolicy || expData.discriminationMeta?.rhsPolicy || "rhs";
  const baselinePolicy = rows[0].baselinePolicy || expData.discriminationMeta?.baselinePolicy || "";
  const allPolicies = [];
  const addPolicy = p => {
    if (p && !allPolicies.includes(p)) allPolicies.push(p);
  };
  addPolicy(lhsPolicy);
  addPolicy(rhsPolicy);
  addPolicy(baselinePolicy);
  rows.forEach(r => (r.policies || []).forEach(addPolicy));
  const deltas = rows.map(r => Number(r.deltaMph) || 0);
  const rebaseSavings = rows.map(r => Number(r.rebaseSavings) || 0);
  const starvedSavings = rows.map(r => (Number(r.lhsStarved) || 0) - (Number(r.rhsStarved) || 0));
  const rhsWins = deltas.filter(v => v > 0).length;
  const ties = deltas.filter(v => v === 0).length;
  const lhsWins = deltas.length - rhsWins - ties;
  const sortedByDelta = [...rows].sort((a, b) => (Number(b.deltaMph) || 0) - (Number(a.deltaMph) || 0));
  const bestRow = sortedByDelta[0];
  const worstRow = sortedByDelta[sortedByDelta.length - 1];
  const calMetaCtx = loadedRunMetadataByCategory?.calibration?.context || {};
  const dimWeightsRaw = (calMetaCtx.dimension_weights && typeof calMetaCtx.dimension_weights === "object")
    ? calMetaCtx.dimension_weights
    : {};
  const targetDimsRaw = (calMetaCtx.target_dimensions && typeof calMetaCtx.target_dimensions === "object")
    ? calMetaCtx.target_dimensions
    : {};
  const dimKeys = Object.keys(dimWeightsRaw).sort((a, b) => a.localeCompare(b));
  const dimRowsHtml = dimKeys.length
    ? dimKeys.map(k => `<tr class="data-row"><td>${mqEscapeHtml(k)}</td><td>${expFormatOrDash(dimWeightsRaw[k], 3)}</td><td>${expFormatOrDash(targetDimsRaw[k], 3)}</td></tr>`).join("")
    : `<tr class="data-row"><td colspan="3">No loaded calibration metadata context. Load a calibration run in Run Browser to show dimension weights/targets.</td></tr>`;
  content.innerHTML = `
    <div class="exp-card" style="overflow-x:auto">
      <h3>Discrimination Scoring Context</h3>
      <div class="sim-kv" style="margin-bottom:0.6rem">
        <span class="k">Primary score</span><span class="v">Direct variant deltas (no weighted objective)</span>
        <span class="k">Delta direction</span><span class="v">${mqEscapeHtml(rhsPolicy)} minus ${mqEscapeHtml(lhsPolicy)} (mph)</span>
        <span class="k">Interpretation</span><span class="v">Positive delta favors ${mqEscapeHtml(rhsPolicy)}; negative favors ${mqEscapeHtml(lhsPolicy)}</span>
        <span class="k">Baseline role</span><span class="v">${baselinePolicy ? mqEscapeHtml(`${baselinePolicy} shown as context only`) : "None loaded"}</span>
      </div>
      <table class="cmp">
        <thead><tr><th>Calibration dimension</th><th>Weight</th><th>Target</th></tr></thead>
        <tbody>${dimRowsHtml}</tbody>
      </table>
    </div>
    <div class="exp-grid">
      <div class="exp-card">
        <h3>Discrimination Summary</h3>
        <div class="sim-kv">
          <span class="k">Variants</span><span class="v">${rows.length}</span>
          <span class="k">${rhsPolicy} wins</span><span class="v">${rhsWins}</span>
          <span class="k">${lhsPolicy} wins</span><span class="v">${lhsWins}</span>
          <span class="k">Ties</span><span class="v">${ties}</span>
          <span class="k">Mean Δ throughput</span><span class="v">${expMean(deltas).toFixed(3)} mph</span>
          <span class="k">Median-like signal</span><span class="v">${deltas.slice().sort((a,b)=>a-b)[Math.floor((deltas.length - 1) / 2)].toFixed(3)} mph</span>
          <span class="k">Mean rebase savings</span><span class="v">${expMean(rebaseSavings).toFixed(2)}</span>
          <span class="k">Mean starved savings</span><span class="v">${expMean(starvedSavings).toFixed(2)}</span>
        </div>
      </div>
      <div class="exp-card">
        <h3>Best / Worst Variant</h3>
        <div class="sim-kv">
          <span class="k">Best variant</span><span class="v">${mqEscapeHtml(bestRow?.variant || "—")}</span>
          <span class="k">Best Δ throughput</span><span class="v">${expFormatOrDash(bestRow?.deltaMph, 3)} mph</span>
          <span class="k">Best rebase savings</span><span class="v">${expFormatOrDash(bestRow?.rebaseSavings, 0)}</span>
          <span class="k">Worst variant</span><span class="v">${mqEscapeHtml(worstRow?.variant || "—")}</span>
          <span class="k">Worst Δ throughput</span><span class="v">${expFormatOrDash(worstRow?.deltaMph, 3)} mph</span>
          <span class="k">Worst rebase savings</span><span class="v">${expFormatOrDash(worstRow?.rebaseSavings, 0)}</span>
        </div>
      </div>
    </div>
    <div class="exp-grid">
      <div class="exp-card">
        <h3>Absolute Throughput by Variant</h3>
        <div class="exp-chart-wrap"><canvas id="expDiscDeltaChart"></canvas></div>
      </div>
      <div class="exp-card">
        <h3>Delta Quality Signals</h3>
        <div class="exp-chart-wrap"><canvas id="expDiscRebaseChart"></canvas></div>
      </div>
    </div>
    <div class="exp-card" style="overflow-x:auto">
      <h3>Variant Summary</h3>
      <table class="cmp" id="expDiscTable"></table>
    </div>
  `;
  const labels = rows.map(r => r.variant);
  expDiscDeltaChart = new Chart(document.getElementById("expDiscDeltaChart").getContext("2d"), {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: `${lhsPolicy} mph`,
          data: rows.map(r => Number(r.lhsThroughput) || 0),
          backgroundColor: "rgba(163,113,247,0.55)",
          borderColor: "#a371f7",
          borderWidth: 1
        },
        {
          label: `${rhsPolicy} mph`,
          data: rows.map(r => Number(r.rhsThroughput) || 0),
          backgroundColor: "rgba(88,166,255,0.55)",
          borderColor: "#58a6ff",
          borderWidth: 1
        },
        ...(baselinePolicy ? [{
          label: `${baselinePolicy} mph`,
          data: rows.map(r => Number(r.policyMetrics?.[baselinePolicy]?.throughput_mph) || 0),
          backgroundColor: "rgba(46,160,67,0.50)",
          borderColor: "#2ea043",
          borderWidth: 1
        }] : [])
      ]
    },
    options: expSingleYChartOptions()
  });
  expDiscRebaseChart = new Chart(document.getElementById("expDiscRebaseChart").getContext("2d"), {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: `Δ throughput (${rhsPolicy}-${lhsPolicy})`, data: rows.map(r => Number(r.deltaMph) || 0), backgroundColor: rows.map(r => (Number(r.deltaMph) || 0) >= 0 ? "rgba(46,160,67,0.55)" : "rgba(248,81,73,0.55)"), borderColor: rows.map(r => (Number(r.deltaMph) || 0) >= 0 ? "#2ea043" : "#f85149"), borderWidth: 1, yAxisID: "y" },
        { label: `Rebase savings (${lhsPolicy}-${rhsPolicy})`, data: rows.map(r => Number(r.rebaseSavings) || 0), backgroundColor: "rgba(210,153,34,0.55)", borderColor: "#d29922", borderWidth: 1, yAxisID: "y1" },
        { label: `Starved savings (${lhsPolicy}-${rhsPolicy})`, data: rows.map(r => (Number(r.lhsStarved) || 0) - (Number(r.rhsStarved) || 0)), backgroundColor: "rgba(121,192,255,0.55)", borderColor: "#79c0ff", borderWidth: 1, yAxisID: "y1" }
      ]
    },
    options: mqChartOptions({
      y: mqThemedScale({ beginAtZero: true, title: { display: true, text: "Δ throughput (mph)" } }),
      y1: mqThemedScale({ beginAtZero: true, position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "Savings (count)" } }),
    })
  });
  const throughputHeaders = allPolicies.map(p => `<th>${mqEscapeHtml(p)} mph</th>`).join("");
  let html =
    `<thead><tr><th>Variant</th>${throughputHeaders}` +
    `<th>${mqEscapeHtml(rhsPolicy)}-${mqEscapeHtml(lhsPolicy)} mph</th><th>Δ %</th>` +
    `<th>${mqEscapeHtml(lhsPolicy)} rebases</th><th>${mqEscapeHtml(rhsPolicy)} rebases</th><th>Rebase savings</th>` +
    `<th>${mqEscapeHtml(lhsPolicy)} starved</th><th>${mqEscapeHtml(rhsPolicy)} starved</th><th>Starved savings</th><th>Raw compare</th></tr></thead><tbody>`;
  rows.forEach(r => {
    const throughputCells = allPolicies.map(policy => {
      const mph = Number(r.policyMetrics?.[policy]?.throughput_mph);
      return `<td>${expFormatOrDash(mph, 3)}</td>`;
    }).join("");
    const lhsMph = Number(r.lhsThroughput) || 0;
    const deltaPct = lhsMph > 0 ? ((Number(r.deltaMph) || 0) * 100 / lhsMph) : 0;
    const starvedDelta = (Number(r.lhsStarved) || 0) - (Number(r.rhsStarved) || 0);
    html +=
      `<tr class="data-row"><td>${mqEscapeHtml(r.variant)}</td>${throughputCells}` +
      `<td>${expFormatOrDash(r.deltaMph, 3)}</td><td>${deltaPct.toFixed(2)}%</td><td>${expFormatOrDash(r.lhsRebases, 0)}</td>` +
      `<td>${expFormatOrDash(r.rhsRebases, 0)}</td><td>${expFormatOrDash(r.rebaseSavings, 0)}</td>` +
      `<td>${expFormatOrDash(r.lhsStarved, 0)}</td><td>${expFormatOrDash(r.rhsStarved, 0)}</td><td>${expFormatOrDash(starvedDelta, 0)}</td>` +
      `<td>${mqEscapeHtml(r.raw_compare || "")}</td></tr>`;
  });
  html += "</tbody>";
  document.getElementById("expDiscTable").innerHTML = html;
}

function renderCalibrationExperiments(content) {
  const grid = [...expData.calibrationGrid];
  const val = [...expData.calibrationValidation];
  const summary = [...expData.calibrationSummary];
  if (!grid.length && !val.length && !summary.length) {
    content.innerHTML = `<div class="kanban-empty">No calibration rows loaded.</div>`;
    return;
  }
  const hasCandidates = grid.length > 0 || val.length > 0;
  summary.sort((a, b) => a.profile.localeCompare(b.profile) || a.mode.localeCompare(b.mode));
  const profiles = [...new Set(summary.map(r => r.profile))];
  const modes = [...new Set(summary.map(r => r.mode))];
  const byMode = new Map();
  summary.forEach(r => {
    const kind = expModeKind(r.mode);
    if (!byMode.has(kind)) byMode.set(kind, []);
    byMode.get(kind).push(r);
  });
  const modeSummaries = Array.from(byMode.entries()).map(([kind, rows]) => ({
    kind,
    rows,
    avg_runtime: mqAverage(rows.map(r => Number(r.wall_seconds))),
    avg_err: mqAverage(rows.map(r => Number(r.throughput_rel_error_pct))),
    avg_ext: mqAverage(rows.map(r => Number(r.extended_score_pct))),
  }));
  modeSummaries.sort((a, b) => a.avg_runtime - b.avg_runtime);
  const fastestMode = modeSummaries[0] || null;
  const bestErrMode = [...modeSummaries].sort((a, b) => a.avg_err - b.avg_err)[0] || null;
  const bestExtMode = [...modeSummaries].sort((a, b) => a.avg_ext - b.avg_ext)[0] || null;
  const rowFor = (profile, kind) => summary.find(r => r.profile === profile && expModeKind(r.mode) === kind) || null;
  const benchmarkRowsHtml = profiles.map(profile => {
    const fx = rowFor(profile, "fixed");
    const ad = rowFor(profile, "adaptive");
    const runtimeDelta = (fx && ad) ? (Number(ad.wall_seconds) - Number(fx.wall_seconds)) : NaN;
    const errDelta = (fx && ad) ? (Number(ad.throughput_rel_error_pct) - Number(fx.throughput_rel_error_pct)) : NaN;
    const extDelta = (fx && ad) ? (Number(ad.extended_score_pct) - Number(fx.extended_score_pct)) : NaN;
    return `<tr class="data-row">
      <td>${mqEscapeHtml(profile)}</td>
      <td>${expFormatOrDash(fx?.wall_seconds, 1)}</td>
      <td>${expFormatOrDash(ad?.wall_seconds, 1)}</td>
      <td>${expFormatOrDash(runtimeDelta, 1)}</td>
      <td>${expFormatOrDash(fx?.throughput_rel_error_pct, 2)}</td>
      <td>${expFormatOrDash(ad?.throughput_rel_error_pct, 2)}</td>
      <td>${expFormatOrDash(errDelta, 2)}</td>
      <td>${expFormatOrDash(fx?.extended_score_pct, 2)}</td>
      <td>${expFormatOrDash(ad?.extended_score_pct, 2)}</td>
      <td>${expFormatOrDash(extDelta, 2)}</td>
    </tr>`;
  }).join("");
  const benchmarkSection = summary.length ? `
    <div class="exp-card">
      <h3>Benchmark run summary</h3>
      <div class="sim-kv">
        <span class="k">Profiles</span><span class="v">${profiles.length}</span>
        <span class="k">Modes</span><span class="v">${modes.length}</span>
        <span class="k">Summary rows</span><span class="v">${summary.length}</span>
        <span class="k">Fastest mode (avg)</span><span class="v">${mqEscapeHtml(fastestMode?.kind || "—")} (${expFormatOrDash(fastestMode?.avg_runtime, 1)}s)</span>
        <span class="k">Lowest throughput err (avg)</span><span class="v">${mqEscapeHtml(bestErrMode?.kind || "—")} (${expFormatOrDash(bestErrMode?.avg_err, 2)}%)</span>
        <span class="k">Lowest extended score (avg)</span><span class="v">${mqEscapeHtml(bestExtMode?.kind || "—")} (${expFormatOrDash(bestExtMode?.avg_ext, 2)}%)</span>
      </div>
    </div>
    <div class="exp-grid">
      <div class="exp-card">
        <h3>Runtime by profile + mode</h3>
        <div class="exp-chart-wrap"><canvas id="expCalBenchRuntimeChart"></canvas></div>
      </div>
      <div class="exp-card">
        <h3>Quality by profile + mode</h3>
        <div class="exp-chart-wrap"><canvas id="expCalBenchScoreChart"></canvas></div>
      </div>
    </div>
    <div class="exp-card" style="overflow-x:auto">
      <h3>Fixed vs adaptive deltas</h3>
      <table class="cmp" id="expCalSummaryTable">
        <thead>
          <tr>
            <th>Profile</th>
            <th>Fixed runtime (s)</th>
            <th>Adaptive runtime (s)</th>
            <th>Runtime Δ (s)</th>
            <th>Fixed thr err %</th>
            <th>Adaptive thr err %</th>
            <th>Err Δ</th>
            <th>Fixed ext %</th>
            <th>Adaptive ext %</th>
            <th>Ext Δ</th>
          </tr>
        </thead>
        <tbody>${benchmarkRowsHtml}</tbody>
      </table>
    </div>
  ` : "";
  if (!hasCandidates) {
    content.innerHTML = benchmarkSection;
  }
  grid.sort((a, b) => a.score_pct - b.score_pct);
  val.sort((a, b) => a.score_pct - b.score_pct);
  const policies = [...new Set([...grid, ...val].map(r => r.policy || "old-burst"))];
  const policyLabel = policies.length === 1 ? policies[0] : `${policies.length} policies`;
  const target = (val[0]?.target_mph || grid[0]?.target_mph || 0).toFixed(3);
  const rankingRows = val.length ? val : grid;
  const best = rankingRows[0] || null;
  const calibrationMeta = loadedRunMetadataByCategory?.calibration || {};
  const decision = (calibrationMeta && typeof calibrationMeta.decision === "object")
    ? calibrationMeta.decision
    : {};
  const decisionStatusRaw = String(decision.status || "").trim().toLowerCase();
  const decisionStatus = decisionStatusRaw || "unknown";
  const decisionLabel = decisionStatus === "accepted"
    ? "ACCEPTED"
    : decisionStatus === "rejected"
      ? "REJECTED"
      : "UNKNOWN";
  const decisionReasons = Array.isArray(decision.reason_codes)
    ? decision.reason_codes.filter(Boolean).join(", ")
    : "";
  const gateInfo = (decision.gates && typeof decision.gates === "object")
    ? decision.gates
    : {};
  const gateSummary = [
    `throughput=${gateInfo.throughput_pass ? "PASS" : "MISS"}`,
    `rank=${gateInfo.score_pass ? "PASS" : "MISS"}`,
    `max-dim=${gateInfo.max_dimension_pass ? "PASS" : "MISS"}`,
  ].join(" | ");
  const decisionClass = decisionStatus === "accepted"
    ? "run-bubble cal-secondary"
    : decisionStatus === "rejected"
      ? "run-bubble cal-warn"
      : "run-bubble meta-primary";
  const targetDims = expParseJsonObject(best?.target_dimensions_json || grid[0]?.target_dimensions_json || "{}", {});
  const bestDimErr = expParseJsonObject(best?.dimension_error_pct_json || "{}", {});
  const dimToRunMetric = {
    merge_per_hour_24h: "throughput_mph",
    merge_per_hour_active_hours: "throughput_active_mph",
    merge_per_hour_peak8: "throughput_peak8_mph",
    merge_per_hour_peak8_p90: "throughput_peak8_p90_mph",
    merge_peak_offpeak_ratio: "peak_offpeak_ratio",
    rebase_per_merge_global: "rebase_per_merge",
    merge_interval_seconds_p50: "merge_interval_p50_seconds",
    merge_interval_seconds_p95: "merge_interval_p95_seconds"
  };
  const dimLabel = k => ({
    merge_per_hour_24h: "24h throughput",
    merge_per_hour_active_hours: "active-hour throughput",
    merge_per_hour_peak8: "peak-8 throughput",
    merge_per_hour_peak8_p90: "peak-8 p90",
    merge_peak_offpeak_ratio: "peak/offpeak ratio",
    rebase_per_merge_global: "rebase/merge",
    merge_interval_seconds_p50: "merge interval p50 (s)",
    merge_interval_seconds_p95: "merge interval p95 (s)"
  }[k] || k);
  if (hasCandidates) {
    content.innerHTML = `
    ${benchmarkSection}
    <div class="exp-note">Target throughput: <b>${target}</b> merges/hour (${mqEscapeHtml(policyLabel)}). Calibration now uses a weighted multidimensional score.</div>
    <div class="exp-note">Decision status: <span class="${mqEscapeHtml(decisionClass)}">${mqEscapeHtml(decisionLabel)}</span>${decisionReasons ? ` · reasons: ${mqEscapeHtml(decisionReasons)}` : ""}${decisionStatus !== "unknown" ? ` · gates: ${mqEscapeHtml(gateSummary)}` : ""}</div>
    <div class="exp-grid">
      <div class="exp-card">
        <h3>Best candidate summary</h3>
        <div class="sim-kv">
          <span class="k">Policy</span><span class="v">${mqEscapeHtml(best?.policy || "—")}</span>
          <span class="k">Weighted score</span><span class="v">${Number(best?.score_pct || 0).toFixed(2)}%</span>
          <span class="k">Throughput error</span><span class="v">${Number(best?.throughput_rel_error_pct || 0).toFixed(2)}%</span>
          <span class="k">Throughput (24h)</span><span class="v">${Number(best?.throughput_mph || 0).toFixed(3)} mph</span>
          <span class="k">Throughput (active)</span><span class="v">${Number(best?.throughput_active_mph || 0).toFixed(3)} mph</span>
          <span class="k">Throughput (peak-8)</span><span class="v">${Number(best?.throughput_peak8_mph || 0).toFixed(3)} mph</span>
          <span class="k">Peak/offpeak ratio</span><span class="v">${Number(best?.peak_offpeak_ratio || 0).toFixed(3)}</span>
          <span class="k">Rebase/Merge</span><span class="v">${Number(best?.rebase_per_merge || 0).toFixed(3)}</span>
          <span class="k">Merge p50 interval</span><span class="v">${Number(best?.merge_interval_p50_seconds || 0).toFixed(1)}s</span>
          <span class="k">Merge p95 interval</span><span class="v">${Number(best?.merge_interval_p95_seconds || 0).toFixed(1)}s</span>
        </div>
      </div>
      <div class="exp-card">
        <h3>Best candidate knobs</h3>
        <div class="sim-kv">
          <span class="k">tick_seconds</span><span class="v">${Number(best?.tick_seconds || 0)}</span>
          <span class="k">arrival_skew</span><span class="v">${Number(best?.arrival_skew || 0).toFixed(2)}</span>
          <span class="k">queue_depth_scale</span><span class="v">${Number(best?.queue_depth_scale || 0).toFixed(2)}</span>
          <span class="k">ci_model</span><span class="v">fixed (5-25 min)</span>
          <span class="k">cycles (row)</span><span class="v">${Number(best?.cycles || 0)}</span>
          <span class="k">merged</span><span class="v">${Math.round(Number(best?.merged || 0))}</span>
        </div>
      </div>
    </div>
    <div class="exp-grid">
      <div class="exp-card">
        <h3>Grid candidates by weighted score (%)</h3>
        <div class="exp-chart-wrap"><canvas id="expCalErrorChart"></canvas></div>
      </div>
      <div class="exp-card">
        <h3>Throughput vs weighted score (tune + validate)</h3>
        <div class="exp-chart-wrap"><canvas id="expCalScatterChart"></canvas></div>
      </div>
    </div>
    <div class="exp-grid">
      <div class="exp-card">
        <h3>Best candidate dimension errors (%)</h3>
        <div class="exp-chart-wrap"><canvas id="expCalDimChart"></canvas></div>
      </div>
      <div class="exp-card" style="overflow-x:auto">
        <h3>Target vs best by dimension</h3>
        <table class="cmp" id="expCalDimTable"></table>
      </div>
    </div>
    <div class="exp-card" style="overflow-x:auto">
      <h3>Validation results (480 cycles)</h3>
      <table class="cmp" id="expCalTable"></table>
    </div>
  `;
  }
  if (summary.length) {
    const labels = profiles;
    const fixedRows = labels.map(p => rowFor(p, "fixed"));
    const adaptiveRows = labels.map(p => rowFor(p, "adaptive"));
    expCalBenchRuntimeChart = new Chart(document.getElementById("expCalBenchRuntimeChart").getContext("2d"), {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "fixed runtime (s)",
            data: fixedRows.map(r => Number(r?.wall_seconds || 0)),
            backgroundColor: "rgba(88,166,255,0.55)",
            borderColor: "#58a6ff",
            borderWidth: 1
          },
          {
            label: "adaptive runtime (s)",
            data: adaptiveRows.map(r => Number(r?.wall_seconds || 0)),
            backgroundColor: "rgba(163,113,247,0.55)",
            borderColor: "#a371f7",
            borderWidth: 1
          }
        ]
      },
      options: expSingleYChartOptions()
    });
    expCalBenchScoreChart = new Chart(document.getElementById("expCalBenchScoreChart").getContext("2d"), {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "fixed throughput err %",
            data: fixedRows.map(r => Number(r?.throughput_rel_error_pct || 0)),
            backgroundColor: "rgba(210,153,34,0.55)",
            borderColor: "#d29922",
            borderWidth: 1
          },
          {
            label: "adaptive throughput err %",
            data: adaptiveRows.map(r => Number(r?.throughput_rel_error_pct || 0)),
            backgroundColor: "rgba(248,81,73,0.50)",
            borderColor: "#f85149",
            borderWidth: 1
          },
          {
            label: "fixed ext score %",
            data: fixedRows.map(r => Number(r?.extended_score_pct || 0)),
            backgroundColor: "rgba(88,166,255,0.30)",
            borderColor: "#58a6ff",
            borderWidth: 1
          },
          {
            label: "adaptive ext score %",
            data: adaptiveRows.map(r => Number(r?.extended_score_pct || 0)),
            backgroundColor: "rgba(163,113,247,0.30)",
            borderColor: "#a371f7",
            borderWidth: 1
          }
        ]
      },
      options: expSingleYChartOptions()
    });
  }
  if (!hasCandidates) return;
  const policyColor = policy => expPolicyColor(policy, policies);

  if (policies.length <= 1) {
    expCalErrorChart = new Chart(document.getElementById("expCalErrorChart").getContext("2d"), {
      type: "line",
      data: {
        labels: grid.map((_, i) => `C${i + 1}`),
        datasets: [{
          label: "weighted score %",
          data: grid.map(r => r.score_pct),
          borderColor: "#58a6ff",
          backgroundColor: "rgba(88,166,255,0.2)",
          tension: 0.15,
          fill: true,
          pointRadius: 3
        }]
      },
      options: expSingleYChartOptions()
    });
  } else {
    const bestErrByPolicy = policies.map(policy => {
      const vals = grid.filter(r => r.policy === policy).map(r => r.score_pct);
      return vals.length ? Math.min(...vals) : 0;
    });
    expCalErrorChart = new Chart(document.getElementById("expCalErrorChart").getContext("2d"), {
      type: "bar",
      data: {
        labels: policies,
        datasets: [{
          label: "best weighted score %",
          data: bestErrByPolicy,
          backgroundColor: policies.map(p => `${policyColor(p)}88`),
          borderColor: policies.map(p => policyColor(p)),
          borderWidth: 1
        }]
      },
      options: expSingleYChartOptions()
    });
  }
  expCalScatterChart = new Chart(document.getElementById("expCalScatterChart").getContext("2d"), {
    type: "scatter",
    data: {
      datasets: policies.flatMap(policy => {
        const tune = grid.filter(r => r.policy === policy);
        const validate = val.filter(r => r.policy === policy);
        const color = policyColor(policy);
        const ds = [];
        if (tune.length) {
          ds.push({
            label: `${policy} tune (240)`,
            data: tune.map(r => ({ x: r.throughput_mph, y: r.score_pct })),
            backgroundColor: `${color}99`
          });
        }
        if (validate.length) {
          ds.push({
            label: `${policy} validate (480)`,
            data: validate.map(r => ({ x: r.throughput_mph, y: r.score_pct })),
            backgroundColor: color
          });
        }
        return ds;
      })
    },
    options: mqChartOptions({
      x: mqThemedScale({ title: { display: true, text: "Throughput (mph)" } }),
      y: mqThemedScale({ title: { display: true, text: "Weighted score %" }, beginAtZero: true }),
    })
  });

  const dimKeys = Object.keys(bestDimErr).sort((a, b) => a.localeCompare(b));
  expCalDimChart = new Chart(document.getElementById("expCalDimChart").getContext("2d"), {
    type: "bar",
    data: {
      labels: dimKeys.map(dimLabel),
      datasets: [{
        label: "error %",
        data: dimKeys.map(k => Number(bestDimErr[k] || 0)),
        backgroundColor: "rgba(248,81,73,0.45)",
        borderColor: "#f85149",
        borderWidth: 1
      }]
    },
    options: expSingleYChartOptions({ title: { display: true, text: "Percent error" } })
  });

  let dimHtml = "<thead><tr><th>Dimension</th><th>Target</th><th>Best actual</th><th>Error %</th></tr></thead><tbody>";
  dimKeys.forEach(k => {
    const targetVal = Number(targetDims[k]);
    const metricName = dimToRunMetric[k];
    let actualVal = metricName && best?.[metricName] !== undefined
      ? Number(best[metricName])
      : NaN;
    if (!Number.isFinite(actualVal) && Number.isFinite(targetVal)) {
      const errFrac = (Number(bestDimErr[k]) || 0) / 100.0;
      actualVal = targetVal * (1 + errFrac);
    }
    const errVal = Number(bestDimErr[k] || 0);
    dimHtml += `<tr class="data-row"><td>${mqEscapeHtml(dimLabel(k))}</td><td>${Number.isFinite(targetVal) ? targetVal.toFixed(4) : "—"}</td><td>${Number.isFinite(actualVal) ? actualVal.toFixed(4) : "—"}</td><td>${errVal.toFixed(2)}</td></tr>`;
  });
  dimHtml += "</tbody>";
  document.getElementById("expCalDimTable").innerHTML = dimHtml;

  let html = "<thead><tr><th>Rank</th><th>Policy</th><th>Tick</th><th>Arrival</th><th>Depth</th><th>mph</th><th>merged</th><th>score %</th><th>throughput err %</th></tr></thead><tbody>";
  (val.length ? val : grid.slice(0, 8)).forEach((r, i) => {
    html += `<tr class="data-row"><td>${i + 1}</td><td>${mqEscapeHtml(r.policy || "old-burst")}</td><td>${r.tick_seconds}</td><td>${r.arrival_skew.toFixed(2)}</td><td>${r.queue_depth_scale.toFixed(2)}</td><td>${r.throughput_mph.toFixed(3)}</td><td>${Math.round(r.merged)}</td><td>${r.score_pct.toFixed(2)}</td><td>${r.throughput_rel_error_pct.toFixed(2)}</td></tr>`;
  });
  html += "</tbody>";
  document.getElementById("expCalTable").innerHTML = html;
}

document.getElementById("expControls").addEventListener("click", e => {
  const btn = e.target.closest("[data-mode]");
  if (!btn || btn.disabled) return;
  expMode = btn.dataset.mode;
  renderExperiments();
});

