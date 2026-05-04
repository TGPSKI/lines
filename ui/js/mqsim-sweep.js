/* mqsim-sweep.js — Limit Sweep visualization tab */

const SWEEP_POLICY_COLORS = {
  "cap+phase1": "#a371f7",
  "active-cap": "#58a6ff",
  "top-k": "#3fb950",
  "old-burst": "#f0883e",
};

const SWEEP_METRIC_GROUPS = [
  { label: "── Throughput ──", metrics: [
    "MRs Merged", "Throughput (merges/hour)", "Throughput Peak8 (merges/hour)",
    "Throughput Offpeak (merges/hour)", "Throughput Active (merges/hour)",
  ]},
  { label: "── CI Efficiency ──", metrics: [
    "Rebases", "Duplicate Rebases", "Rebase/Merge Ratio",
    "Peak Active Pipelines", "Pipelines Created",
  ]},
  { label: "── Priority & Fairness ──", metrics: [
    "Wait Time p95 (ticks)", "Starved MRs (>100 ticks)",
    "Time to Merge 10 MRs", "Avg Merge Interval",
    "Merge Interval p50 (seconds)", "Merge Interval p95 (seconds)",
  ]},
  { label: "── Phase 1 ──", metrics: [
    "Merge Cycles", "Avg MRs / Merge Cycle", "Same-Root Pool Max",
  ]},
  { label: "── Other ──", metrics: [
    "Queue Drain %", "Peak/Offpeak Throughput Ratio",
  ]},
];

let sweepVisiblePolicies = new Set();
let sweepCurrentMetric = "Rebases";

function loadSweepData(data) {
  sweepData = data;
  sweepVisiblePolicies = new Set(data.policies);
  const navTab = document.querySelector('[data-tab="sweep"]');
  if (navTab) navTab.classList.remove("disabled");
}

function renderSweepTab() {
  if (!sweepData) return;
  renderSweepHeader();
  renderSweepControls();
  renderSweepCharts();
  renderSweepTable();
}

function renderSweepHeader() {
  const el = document.getElementById("sweepHeader");
  const s = sweepData.setup || {};
  el.innerHTML = `<h2>${mqEscapeHtml(sweepData.title || "Limit Sweep")}</h2>`
    + `<p>${mqEscapeHtml(sweepData.description || "")}</p>`
    + `<div class="sweep-setup">`
    + Object.entries(s).map(([k, v]) => `<span><b>${mqEscapeHtml(k)}:</b> ${mqEscapeHtml(String(v))}</span>`).join("")
    + `</div>`;
}

let _sweepControlsInit = false;
function renderSweepControls() {
  const sel = document.getElementById("sweepMetricSelect");
  if (sel.options.length === 0) {
    SWEEP_METRIC_GROUPS.forEach(g => {
      const og = document.createElement("optgroup");
      og.label = g.label;
      g.metrics.forEach(m => {
        const opt = document.createElement("option");
        opt.value = m;
        opt.textContent = m;
        if (m === sweepCurrentMetric) opt.selected = true;
        og.appendChild(opt);
      });
      sel.appendChild(og);
    });
    sel.addEventListener("change", () => {
      sweepCurrentMetric = sel.value;
      renderSweepCharts();
      renderSweepTable();
    });
  }

  const toggles = document.getElementById("sweepPolicyToggles");
  toggles.innerHTML = `<span class="sweep-control-label">Policies:</span>` +
    sweepData.policies.map(p => {
      const color = SWEEP_POLICY_COLORS[p] || "#8b949e";
      const active = sweepVisiblePolicies.has(p) ? " active" : "";
      return `<button class="sweep-policy-btn${active}" style="--policy-color:${color}" data-sweep-policy="${mqEscapeHtml(p)}">${mqEscapeHtml(p)}</button>`;
    }).join("");

  if (!_sweepControlsInit) {
    _sweepControlsInit = true;
    toggles.addEventListener("click", e => {
      const btn = e.target.closest("[data-sweep-policy]");
      if (!btn) return;
      const p = btn.dataset.sweepPolicy;
      if (sweepVisiblePolicies.has(p)) sweepVisiblePolicies.delete(p);
      else sweepVisiblePolicies.add(p);
      btn.classList.toggle("active", sweepVisiblePolicies.has(p));
      renderSweepCharts();
      renderSweepTable();
    });
  }
}

function getSweepSeriesForMetric(metric) {
  return sweepData.policies.filter(p => sweepVisiblePolicies.has(p)).map(p => ({
    policy: p,
    color: SWEEP_POLICY_COLORS[p] || "#8b949e",
    values: sweepData.data[p].map(d => d[metric] ?? null),
  }));
}

function renderSweepCharts() {
  renderSweepLineChart();
  renderSweepBarChart();
  renderSweepConvergeChart();
}

function renderSweepLineChart() {
  const ctx = document.getElementById("sweepLineCanvas");
  const series = getSweepSeriesForMetric(sweepCurrentMetric);
  const labels = sweepData.limits.map(l => `limit=${l}`);

  document.getElementById("sweepLineTitle").textContent = `${sweepCurrentMetric} vs Limit`;

  const datasets = series.map(s => ({
    label: s.policy,
    data: s.values,
    borderColor: s.color,
    backgroundColor: s.color + "33",
    tension: 0.3,
    pointRadius: 5,
    pointHoverRadius: 7,
    borderWidth: 2,
  }));

  if (sweepLineChart) {
    sweepLineChart.data.labels = labels;
    sweepLineChart.data.datasets = datasets;
    sweepLineChart.options.plugins.title.text = sweepCurrentMetric;
    sweepLineChart.update();
  } else {
    sweepLineChart = new Chart(ctx, {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          title: { display: false },
          legend: { position: "bottom", labels: { color: "#c9d1d9", boxWidth: 12, font: { size: 11 } } },
          tooltip: { mode: "index", intersect: false },
        },
        scales: {
          x: { ticks: { color: "#8b949e" }, grid: { color: "rgba(255,255,255,0.04)" } },
          y: { beginAtZero: true, ticks: { color: "#8b949e" }, grid: { color: "rgba(255,255,255,0.06)" } },
        },
        interaction: { mode: "index", intersect: false },
      },
    });
  }
}

function renderSweepBarChart() {
  const ctx = document.getElementById("sweepBarCanvas");
  const series = getSweepSeriesForMetric(sweepCurrentMetric);
  const labels = sweepData.limits.map(l => `limit=${l}`);

  document.getElementById("sweepBarTitle").textContent = `${sweepCurrentMetric} (grouped bars)`;

  const datasets = series.map(s => ({
    label: s.policy,
    data: s.values,
    backgroundColor: s.color + "99",
    borderColor: s.color,
    borderWidth: 1,
  }));

  if (sweepBarChart) {
    sweepBarChart.data.labels = labels;
    sweepBarChart.data.datasets = datasets;
    sweepBarChart.update();
  } else {
    sweepBarChart = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom", labels: { color: "#c9d1d9", boxWidth: 12, font: { size: 11 } } },
          tooltip: { mode: "index", intersect: false },
        },
        scales: {
          x: { ticks: { color: "#8b949e" }, grid: { color: "rgba(255,255,255,0.04)" } },
          y: { beginAtZero: true, ticks: { color: "#8b949e" }, grid: { color: "rgba(255,255,255,0.06)" } },
        },
      },
    });
  }
}

let _sweepConvergeInit = false;
let sweepConvergeA = "active-cap";
let sweepConvergeB = "old-burst";

function initSweepConvergeSelects() {
  if (_sweepConvergeInit) return;
  _sweepConvergeInit = true;
  const selA = document.getElementById("sweepConvergeA");
  const selB = document.getElementById("sweepConvergeB");
  if (!selA || !selB) return;
  const fill = (sel, defaultVal) => {
    sel.innerHTML = "";
    sweepData.policies.forEach(p => {
      const opt = document.createElement("option");
      opt.value = p;
      opt.textContent = p;
      if (p === defaultVal) opt.selected = true;
      sel.appendChild(opt);
    });
  };
  fill(selA, sweepConvergeA);
  fill(selB, sweepConvergeB);
  selA.addEventListener("change", () => { sweepConvergeA = selA.value; renderSweepConvergeChart(); });
  selB.addEventListener("change", () => { sweepConvergeB = selB.value; renderSweepConvergeChart(); });
}

function renderSweepConvergeChart() {
  initSweepConvergeSelects();
  const ctx = document.getElementById("sweepConvergeCanvas");
  const aData = sweepData.data[sweepConvergeA];
  const bData = sweepData.data[sweepConvergeB];
  if (!aData || !bData) return;

  const metric = sweepCurrentMetric;
  const labels = sweepData.limits.map(l => `limit=${l}`);
  const deltaMetric = aData.map((d, i) => (d[metric] ?? 0) - (bData[i][metric] ?? 0));

  const metricLabel = document.getElementById("sweepConvergeMetricLabel");
  if (metricLabel) metricLabel.textContent = `(Δ ${metric})`;

  const colorA = SWEEP_POLICY_COLORS[sweepConvergeA] || "#58a6ff";

  const datasets = [
    {
      label: `Δ ${metric} (${sweepConvergeA} − ${sweepConvergeB})`,
      data: deltaMetric,
      borderColor: colorA,
      backgroundColor: colorA + "33",
      tension: 0.3, pointRadius: 5, borderWidth: 2.5,
      fill: { target: "origin", above: colorA + "11", below: colorA + "11" },
    },
  ];

  if (sweepConvergeChart) {
    sweepConvergeChart.data.labels = labels;
    sweepConvergeChart.data.datasets = datasets;
    sweepConvergeChart.options.scales.y.title.text = `Δ ${metric}`;
    sweepConvergeChart.update();
  } else {
    sweepConvergeChart = new Chart(ctx, {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom", labels: { color: "#c9d1d9", boxWidth: 12, font: { size: 11 } } },
          tooltip: { mode: "index", intersect: false },
          annotation: {
            annotations: {
              zeroLine: { type: "line", yMin: 0, yMax: 0, borderColor: "rgba(255,255,255,0.2)", borderWidth: 1, borderDash: [4, 3] },
            },
          },
        },
        scales: {
          x: { ticks: { color: "#8b949e" }, grid: { color: "rgba(255,255,255,0.04)" } },
          y: { title: { display: true, text: `Δ ${metric}`, color: "#8b949e" }, ticks: { color: "#8b949e" }, grid: { color: "rgba(255,255,255,0.06)" } },
        },
        interaction: { mode: "index", intersect: false },
      },
    });
  }
}

function renderSweepTable() {
  const table = document.getElementById("sweepDataTable");
  const metric = sweepCurrentMetric;
  document.getElementById("sweepTableTitle").textContent = `${metric} — All Limits`;

  const policies = sweepData.policies.filter(p => sweepVisiblePolicies.has(p));
  const limits = sweepData.limits;

  let html = `<thead><tr><th>Limit</th>` + policies.map(p =>
    `<th style="color:${SWEEP_POLICY_COLORS[p] || "#c9d1d9"}">${mqEscapeHtml(p)}</th>`
  ).join("") + `</tr></thead><tbody>`;

  const lowerBetter = metric.includes("Rebase") || metric.includes("Wait") || metric.includes("Starved")
    || metric.includes("Peak Active") || metric.includes("Time to Merge") || metric.includes("Duplicate")
    || metric.includes("Interval");

  limits.forEach((lim, li) => {
    const vals = policies.map(p => sweepData.data[p][li][metric] ?? null);
    const best = lowerBetter
      ? Math.min(...vals.filter(v => v !== null))
      : Math.max(...vals.filter(v => v !== null));

    html += `<tr><td><b>${lim}</b></td>` + vals.map(v => {
      const cls = v === best ? ' class="best"' : "";
      return `<td${cls}>${v !== null ? v.toLocaleString() : "—"}</td>`;
    }).join("") + `</tr>`;
  });

  html += `</tbody>`;
  table.innerHTML = html;
}
