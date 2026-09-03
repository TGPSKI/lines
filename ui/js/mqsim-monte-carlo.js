/*
 * Split from ui/index.html for maintainability.
 * Client-side only: plain browser JavaScript, no build step.
 */

/* global mqEscapeHtml, mqParseCsvRecords, mqPaletteColor,
   mqDestroyChartIfPresent, mqChartOptions, mqThemedScale, mqThemedLegend,
   MQSIM_POLICY_COLOR_PALETTE, Chart */

// =========================================================================
// Monte Carlo Tab
// =========================================================================
const MC_COLORS = MQSIM_POLICY_COLOR_PALETTE;
const MC_METRIC_LABELS = {
  total_time_ticks: "Total Time (ticks)", mrs_merged: "MRs Merged",
  throughput_merges_per_tick: "Throughput (merges/tick)", time_to_first_merge: "Time to First Merge",
  throughput_merges_per_hour: "Throughput (merges/hour)", tick_seconds: "Tick Length (seconds)",
  time_to_merge_10: "Time to Merge 10 MRs", avg_merge_interval_ticks: "Avg Merge Interval",
  queue_drain_pct: "Queue Drain %", rebase_calls: "Rebases",
  pipelines_created: "Pipelines Created", peak_active_pipelines: "Peak Active Pipelines",
  duplicate_rebase_total: "Duplicate Rebases", wait_p50: "Wait Time p50 (ticks)",
  wait_p95: "Wait Time p95 (ticks)", wait_max: "Max Wait (ticks)",
  starved_mrs: "Starved MRs (>100 ticks)", merge_errors: "Merge Errors",
  rebase_errors: "Rebase Errors", pipeline_cancels: "Pipeline Cancels"
};
const MC_LOWER_BETTER = new Set([
  "total_time_ticks", "time_to_first_merge", "time_to_merge_10",
  "avg_merge_interval_ticks", "rebase_calls", "pipelines_created",
  "peak_active_pipelines", "duplicate_rebase_total",
  "wait_p50", "wait_p95", "wait_max", "starved_mrs",
  "merge_errors", "rebase_errors", "pipeline_cancels"
]);
const MC_SKIP_METRICS = new Set(["total_time_ticks", "tick_seconds"]);

function loadMcCSVText(text) {
  const { headers, rows } = mqParseCsvRecords(text);
  if (!headers.length || !rows.length || headers.length < 3) return;
  const policyKey = headers[1];
  const metricCols = headers.slice(2);
  const policies = new Set();
  const trials = {};

  rows.forEach(row => {
    const policy = String(row[policyKey] || "").trim();
    if (!policy) return;
    policies.add(policy);
    if (!trials[policy]) trials[policy] = {};
    metricCols.forEach(m => {
      if (!trials[policy][m]) trials[policy][m] = [];
      const v = Number(row[m]);
      if (Number.isFinite(v)) trials[policy][m].push(v);
    });
  });

  mcData = { policies: [...policies], metrics: metricCols, trials };
  mcVisiblePolicies = new Set(mcData.policies);
  initMcControls();
  document.getElementById("mcContent").style.display = "block";
}

function mcPolicyColor(policy) {
  return mqPaletteColor(mcData?.policies?.indexOf(policy) ?? -1, MC_COLORS);
}

function mcVisiblePolicyList() {
  return (mcData?.policies || []).filter(p => mcVisiblePolicies.has(p));
}

function mcStatsByPolicy(metric, policies) {
  const out = {};
  (policies || []).forEach(policy => {
    out[policy] = mcStats(mcData?.trials?.[policy]?.[metric] || []);
  });
  return out;
}

function initMcControls() {
  const sel = document.getElementById("mcMetricSelect");
  sel.innerHTML = mcData.metrics.map(m =>
    `<option value="${mqEscapeHtml(m)}">${mqEscapeHtml(MC_METRIC_LABELS[m] || m)}</option>`
  ).join("");
  sel.addEventListener("change", renderMonteCarlo);

  const toggles = document.getElementById("mcPolicyToggles");
  toggles.innerHTML = mcData.policies.map((p, i) =>
    `<button class="mc-toggle active" data-policy="${mqEscapeHtml(p)}" style="border-left:3px solid ${mqPaletteColor(i, MC_COLORS)}">${mqEscapeHtml(p)}</button>`
  ).join("");
  toggles.addEventListener("click", e => {
    const btn = e.target.closest(".mc-toggle");
    if (!btn) return;
    btn.classList.toggle("active");
    mcVisiblePolicies = new Set([...toggles.querySelectorAll(".mc-toggle.active")].map(b => b.dataset.policy));
    renderMonteCarlo();
  });

  const compA = document.getElementById("mcCompareA");
  const compB = document.getElementById("mcCompareB");
  const opts = mcData.policies.map(p => `<option value="${mqEscapeHtml(p)}">${mqEscapeHtml(p)}</option>`).join("");
  compA.innerHTML = opts;
  compB.innerHTML = opts;
  if (mcData.policies.length > 1) compB.selectedIndex = 1;
  compA.addEventListener("change", renderMcPairwise);
  compB.addEventListener("change", renderMcPairwise);
}

function mcStats(values) {
  if (!values || !values.length) return { mean: 0, stddev: 0, ci95: 0, min: 0, max: 0, q1: 0, median: 0, q3: 0 };
  const sorted = [...values].sort((a, b) => a - b);
  const n = sorted.length;
  const mean = sorted.reduce((a, b) => a + b, 0) / n;
  const variance = n > 1 ? sorted.reduce((s, v) => s + (v - mean) ** 2, 0) / (n - 1) : 0;
  const stddev = Math.sqrt(variance);
  const ci95 = n > 1 ? 1.96 * stddev / Math.sqrt(n) : 0;
  const q = p => { const i = p * (n - 1); const lo = Math.floor(i); return lo === i ? sorted[lo] : sorted[lo] + (sorted[lo + 1] - sorted[lo]) * (i - lo); };
  return { mean, stddev, ci95, min: sorted[0], max: sorted[n - 1], q1: q(0.25), median: q(0.5), q3: q(0.75) };
}

function mcHasCiOverlap(a, b) {
  return (a.mean - a.ci95) <= (b.mean + b.ci95) && (b.mean - b.ci95) <= (a.mean + a.ci95);
}

function mcPolicyABeatsPolicyB(a, b, lowerBetter) {
  return lowerBetter ? a.mean < b.mean : a.mean > b.mean;
}

function renderMonteCarlo() {
  if (!mcData) return;
  renderMcBoxChart();
  renderMcCIChart();
  renderMcStatsTable();
  renderMcPairwise();
  renderMcRank();
  renderMcHeatmap();
}

function renderMcBoxChart() {
  const metric = document.getElementById("mcMetricSelect").value;
  const policies = mcVisiblePolicyList();
  const ctx = document.getElementById("mcBoxChart").getContext("2d");

  mqDestroyChartIfPresent(mcBoxChart);

  const allValues = policies.flatMap(p => mcData.trials[p]?.[metric] || []);
  const yMin = allValues.length ? Math.min(...allValues) * 0.9 : 0;
  const yMax = allValues.length ? Math.max(...allValues) * 1.1 : 100;

  mcBoxChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: policies,
      datasets: [
        ...policies.map((policy, i) => {
          const values = mcData.trials[policy]?.[metric] || [];
          const s = mcStats(values);
          const color = mcPolicyColor(policy);
          return {
            label: policy + " IQR",
            data: [{ x: policy, y: s.q3 - s.q1 }],
            base: policies.map((p, j) => j === i ? mcStats(mcData.trials[p]?.[metric] || []).q1 : null),
            backgroundColor: color + "44",
            borderColor: color,
            borderWidth: 2,
            barPercentage: 0.5,
          };
        }),
        {
          type: "scatter",
          label: "Trials",
          data: policies.flatMap((policy, i) => {
            const values = mcData.trials[policy]?.[metric] || [];
            return values.map(v => ({ x: i, y: v }));
          }),
          backgroundColor: policies.flatMap((policy) => {
            const values = mcData.trials[policy]?.[metric] || [];
            const color = mcPolicyColor(policy);
            return values.map(() => color + "88");
          }),
          pointRadius: 3,
          pointHoverRadius: 5,
        }
      ]
    },
    options: mqChartOptions({
      x: mqThemedScale(),
      y: mqThemedScale({ min: yMin, max: yMax })
    }, {
      plugins: { legend: { display: false }, tooltip: { enabled: true } },
    })
  });
}

function renderMcCIChart() {
  const metric = document.getElementById("mcMetricSelect").value;
  const policies = mcVisiblePolicyList();
  const ctx = document.getElementById("mcCIChart").getContext("2d");

  mqDestroyChartIfPresent(mcCIChart);

  const means = [];
  const ciLow = [];
  const ciHigh = [];
  const colors = [];

  policies.forEach(policy => {
    const values = mcData.trials[policy]?.[metric] || [];
    const s = mcStats(values);
    means.push(s.mean);
    ciLow.push(s.mean - s.ci95);
    ciHigh.push(s.mean + s.ci95);
    colors.push(mcPolicyColor(policy));
  });

  mcCIChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: policies,
      datasets: [{
        label: "Mean",
        data: means,
        backgroundColor: colors.map(c => c + "66"),
        borderColor: colors,
        borderWidth: 2,
      }]
    },
    options: mqChartOptions({
      x: mqThemedScale(),
      y: mqThemedScale()
    }, {
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            afterLabel: (ctx) => {
              const i = ctx.dataIndex;
              return `CI95: [${ciLow[i].toFixed(2)}, ${ciHigh[i].toFixed(2)}]`;
            }
          }
        }
      }
    }),
    plugins: [{
      id: "errorBars",
      afterDraw(chart) {
        const { ctx, scales: { x, y } } = chart;
        ctx.save();
        ctx.lineWidth = 2;
        policies.forEach((_, i) => {
          const xPos = x.getPixelForValue(i);
          const yLo = y.getPixelForValue(ciLow[i]);
          const yHi = y.getPixelForValue(ciHigh[i]);
          ctx.strokeStyle = colors[i];
          ctx.beginPath();
          ctx.moveTo(xPos, yLo); ctx.lineTo(xPos, yHi);
          ctx.moveTo(xPos - 4, yLo); ctx.lineTo(xPos + 4, yLo);
          ctx.moveTo(xPos - 4, yHi); ctx.lineTo(xPos + 4, yHi);
          ctx.stroke();
        });
        ctx.restore();
      }
    }]
  });
}

function renderMcStatsTable() {
  const policies = mcVisiblePolicyList();
  const table = document.getElementById("mcStatsTable");
  let html = "<thead><tr><th>Metric</th>";
  policies.forEach(p => { html += `<th>${mqEscapeHtml(p)}</th>`; });
  html += "</tr></thead><tbody>";

  mcData.metrics.forEach(metric => {
    if (MC_SKIP_METRICS.has(metric)) return;
    const label = MC_METRIC_LABELS[metric] || metric;
    html += `<tr><td class="metric-name">${mqEscapeHtml(label)}</td>`;
    const lowerBetter = MC_LOWER_BETTER.has(metric);
    const statsMap = mcStatsByPolicy(metric, policies);
    const statsArr = policies.map(p => statsMap[p]);
    const bestVal = statsArr.reduce((best, s) => (
      best === null ? s.mean : (lowerBetter ? Math.min(best, s.mean) : Math.max(best, s.mean))
    ), null);

    statsArr.forEach(s => {
      const isBest = Math.abs(s.mean - bestVal) < 0.001;
      const cls = isBest ? " class=\"best\"" : "";
      html += `<td${cls}>${s.mean.toFixed(2)} <span style="color:var(--muted);font-size:0.68rem">+/-${s.ci95.toFixed(2)}</span></td>`;
    });
    html += "</tr>";
  });
  html += "</tbody>";
  table.innerHTML = html;
}

function renderMcPairwise() {
  if (!mcData) return;
  const pA = document.getElementById("mcCompareA").value;
  const pB = document.getElementById("mcCompareB").value;
  const container = document.getElementById("mcPairwise");

  if (!pA || !pB || pA === pB) {
    container.innerHTML = "<p style='color:var(--muted)'>Select two different policies to compare.</p>";
    return;
  }

  let html = "<table style='width:100%;font-size:0.75rem;border-collapse:collapse'>";
  html += `<tr style="border-bottom:1px solid var(--border)"><th style="text-align:left;padding:0.3rem">Metric</th><th>Delta</th><th>Sig?</th></tr>`;

  mcData.metrics.forEach(metric => {
    if (MC_SKIP_METRICS.has(metric)) return;
    const sA = mcStats(mcData.trials[pA]?.[metric] || []);
    const sB = mcStats(mcData.trials[pB]?.[metric] || []);
    const delta = sA.mean - sB.mean;
    const lower = MC_LOWER_BETTER.has(metric);
    const ciOverlap = mcHasCiOverlap(sA, sB);
    const significant = !ciOverlap;
    const aWins = mcPolicyABeatsPolicyB(sA, sB, lower);
    const label = MC_METRIC_LABELS[metric] || metric;

    let sigClass = "mc-sig-none";
    let sigLabel = "~";
    if (significant) {
      sigClass = aWins ? "mc-sig-pos" : "mc-sig-neg";
      sigLabel = aWins ? `${pA} wins` : `${pB} wins`;
    }

    html += `<tr style="border-bottom:1px solid var(--border)">`;
    html += `<td style="padding:0.25rem">${mqEscapeHtml(label)}</td>`;
    html += `<td style="text-align:center;padding:0.25rem">${delta >= 0 ? "+" : ""}${delta.toFixed(2)}</td>`;
    html += `<td style="text-align:center;padding:0.25rem" class="${sigClass}">${mqEscapeHtml(sigLabel)}</td>`;
    html += `</tr>`;
  });
  html += "</table>";
  container.innerHTML = html;
}

// --- Rank Summary Bar ---
let mcRankChart = null;

function mcIsLowerBetter(m) {
  return MC_LOWER_BETTER.has(m);
}

function renderMcRank() {
  if (!mcData) return;
  const policies = mcVisiblePolicyList();
  const ctx = document.getElementById("mcRankChart").getContext("2d");
  mqDestroyChartIfPresent(mcRankChart);

  const wins = {}, ties = {}, losses = {};
  policies.forEach(p => { wins[p] = 0; ties[p] = 0; losses[p] = 0; });

  mcData.metrics.forEach(metric => {
    if (MC_SKIP_METRICS.has(metric)) return;
    const statsMap = mcStatsByPolicy(metric, policies);
    const lower = mcIsLowerBetter(metric);

    policies.forEach(p => {
      let isWinner = true, isTied = false;
      policies.forEach(other => {
        if (other === p) return;
        const sP = statsMap[p], sO = statsMap[other];
        const pBetter = mcPolicyABeatsPolicyB(sP, sO, lower);
        const overlap = mcHasCiOverlap(sP, sO);
        if (overlap) isTied = true;
        if (!pBetter && !overlap) isWinner = false;
      });
      if (isWinner && !isTied) wins[p]++;
      else if (isTied) ties[p]++;
      else losses[p]++;
    });
  });

  mcRankChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: policies,
      datasets: [
        { label: "Wins", data: policies.map(p => wins[p]), backgroundColor: "#2ea04399", borderColor: "#2ea043", borderWidth: 1 },
        { label: "Ties", data: policies.map(p => ties[p]), backgroundColor: "#d2992244", borderColor: "#d29922", borderWidth: 1 },
        { label: "Losses", data: policies.map(p => losses[p]), backgroundColor: "#f8514944", borderColor: "#f85149", borderWidth: 1 },
      ]
    },
    options: mqChartOptions({
      x: mqThemedScale({ stacked: true }),
      y: mqThemedScale({ stacked: true })
    }, {
      indexAxis: "y",
      plugins: {
        legend: { position: "bottom", ...mqThemedLegend() }
      }
    })
  });
}

// --- Winner Heatmap ---
function renderMcHeatmap() {
  if (!mcData) return;
  const policies = mcVisiblePolicyList();
  const container = document.getElementById("mcHeatmap");
  if (policies.length < 2) { container.innerHTML = ""; return; }

  const results = {};
  policies.forEach(pA => {
    results[pA] = {};
    policies.forEach(pB => { results[pA][pB] = { wins: 0, losses: 0, ties: 0 }; });
  });

  mcData.metrics.forEach(metric => {
    if (MC_SKIP_METRICS.has(metric)) return;
    const lower = mcIsLowerBetter(metric);
    policies.forEach(pA => {
      policies.forEach(pB => {
        if (pA === pB) return;
        const sA = mcStats(mcData.trials[pA]?.[metric] || []);
        const sB = mcStats(mcData.trials[pB]?.[metric] || []);
        const overlap = mcHasCiOverlap(sA, sB);
        if (overlap) { results[pA][pB].ties++; return; }
        const aWins = mcPolicyABeatsPolicyB(sA, sB, lower);
        if (aWins) results[pA][pB].wins++;
        else results[pA][pB].losses++;
      });
    });
  });

  let html = "<table style='width:100%;font-size:0.72rem;border-collapse:collapse;text-align:center'>";
  html += "<tr><th style='padding:0.3rem;border:1px solid var(--border)'></th>";
  policies.forEach(p => { html += `<th style="padding:0.3rem;border:1px solid var(--border);color:var(--text)">${mqEscapeHtml(p)}</th>`; });
  html += "</tr>";

  policies.forEach(pA => {
    html += `<tr><td style="padding:0.3rem;border:1px solid var(--border);color:var(--text);font-weight:600;text-align:left">${mqEscapeHtml(pA)}</td>`;
    policies.forEach(pB => {
      if (pA === pB) {
        html += `<td style="padding:0.3rem;border:1px solid var(--border);background:#161b22">—</td>`;
        return;
      }
      const r = results[pA][pB];
      const total = r.wins + r.losses + r.ties;
      const ratio = total > 0 ? r.wins / total : 0;
      let bg, color;
      if (ratio > 0.6) { bg = `rgba(46,160,67,${0.15 + ratio * 0.3})`; color = "#3fb950"; }
      else if (ratio < 0.4) { bg = `rgba(248,81,73,${0.15 + (1 - ratio) * 0.2})`; color = "#f85149"; }
      else { bg = "rgba(210,153,34,0.1)"; color = "#d29922"; }
      const title = `${pA} vs ${pB}: ${r.wins}W/${r.ties}T/${r.losses}L`;
      html += `<td style="padding:0.3rem;border:1px solid var(--border);background:${bg};color:${color};font-weight:600" title="${mqEscapeHtml(title)}">${r.wins}/${r.ties}/${r.losses}</td>`;
    });
    html += "</tr>";
  });
  html += "</table>";
  html += `<div style="font-size:0.65rem;color:var(--muted);margin-top:0.3rem">Cells: Wins/Ties/Losses (row vs column). Green = row dominates.</div>`;
  container.innerHTML = html;
}
