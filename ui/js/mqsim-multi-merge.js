/*
 * Multi-Merge Replay & Analysis module for Merge Queue Sim.
 * Loads plan-raw JSON (from analyze_logs.py plan), computes overlap metrics,
 * renders interactive explorer and statistics sub-tabs.
 */

/* global enableTabs, mqThemedScale, mqThemedLegend,
   mqDestroyChartIfPresent, MQSIM_POLICY_COLOR_PALETTE */

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const MM_PRIORITY_LABELS = [
  "critical", "urgent", "high", "progressive-delivery", "medium", "low",
  "bot/approved", "bot/automerge", "lgtm",
];

const MM_GAP_OPTIONS = [
  { label: "All", maxSeconds: Infinity },
  { label: "≤ 2m", maxSeconds: 120 },
  { label: "≤ 5m", maxSeconds: 300 },
  { label: "≤ 10m", maxSeconds: 600 },
  { label: "≤ 30m", maxSeconds: 1800 },
  { label: "≤ 1h", maxSeconds: 3600 },
  { label: "≤ 4h", maxSeconds: 14400 },
];

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let mmRawData = null;
let mmMetrics = null;
let mmCharts = {};
let mmPlayTimer = null;
let mmPlayIndex = 0;
let mmSortCol = "idx";
let mmSortAsc = true;
let mmHighlightedPair = -1;
let mmSubtab = "statistics";
let mmFilteredPairs = [];
let mmOverlapMode = "service"; // "service" | "file" | "ast"

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

function loadMultiMergeData(jsonArray) {
  mmRawData = jsonArray.sort(
    (a, b) => new Date(a.merged_at) - new Date(b.merged_at)
  );
  mmMetrics = computeMultiMergeMetrics(mmRawData);
  enableTabs(["multiMerge"]);
  mmInitOverlapMode();
  mmInitSubtabs();
  populateFilters(mmRawData);
  renderTimeContext(mmRawData, mmMetrics);
  renderOverlapTable(mmRawData, mmMetrics);
  renderMultiMergeStats(mmMetrics);
  renderStatsTable(mmRawData, mmMetrics);
  renderAllCharts(mmMetrics);
  renderBatchSimulator(mmRawData, mmMetrics);
  mmShowDetailHint();
  mmSetupKeyboard();
}

function unloadMultiMergeData() {
  mmStopPlayback();
  Object.values(mmCharts).forEach(c => mqDestroyChartIfPresent(c));
  mmCharts = {};
  mmRawData = null;
  mmMetrics = null;
  mmPlayIndex = 0;
  mmHighlightedPair = -1;
  mmFilteredPairs = [];
  const body = document.getElementById("mmOverlapBody");
  if (body) body.innerHTML = "";
  const cards = document.getElementById("mmHeroCards");
  if (cards) cards.innerHTML = "";
  const detail = document.getElementById("mmMRDetail");
  if (detail) detail.innerHTML = "";
  document.removeEventListener("keydown", mmKeyHandler);
}

// ---------------------------------------------------------------------------
// Sub-tab management
// ---------------------------------------------------------------------------

function mmInitSubtabs() {
  const bar = document.getElementById("mmSubtabBar");
  if (!bar) return;
  bar.querySelectorAll(".stats-subtab").forEach(btn => {
    btn.onclick = () => {
      mmSubtab = btn.dataset.mmtab;
      bar.querySelectorAll(".stats-subtab").forEach(b =>
        b.classList.toggle("active", b.dataset.mmtab === mmSubtab)
      );
      const views = { statistics: "mmViewStatistics", simulator: "mmViewSimulator", explorer: "mmViewExplorer" };
      Object.entries(views).forEach(([tab, id]) => {
        const el = document.getElementById(id);
        if (!el) return;
        if (tab === mmSubtab) {
          el.style.display = id === "mmViewExplorer" ? "flex" : "";
        } else {
          el.style.display = "none";
        }
      });
      const explorerFilters = document.getElementById("mmExplorerFilters");
      if (explorerFilters) explorerFilters.style.display = mmSubtab === "explorer" ? "" : "none";
    };
  });
}

// ---------------------------------------------------------------------------
// Overlap mode control
// ---------------------------------------------------------------------------

function mmInitOverlapMode() {
  const row = document.getElementById("mmOverlapControlRow");
  if (!row) return;
  row.querySelectorAll(".tab-toggle-chip[data-overlap]").forEach(chip => {
    chip.onclick = () => {
      if (chip.disabled) return;
      mmOverlapMode = chip.dataset.overlap;
      row.querySelectorAll(".tab-toggle-chip[data-overlap]").forEach(c =>
        c.classList.toggle("active", c.dataset.overlap === mmOverlapMode)
      );
      mmRefreshAll();
    };
  });
}

function mmRefreshAll() {
  if (!mmRawData) return;
  mmMetrics = computeMultiMergeMetrics(mmRawData);
  renderTimeContext(mmRawData, mmMetrics);
  renderOverlapTable(mmRawData, mmMetrics);
  renderMultiMergeStats(mmMetrics);
  renderStatsTable(mmRawData, mmMetrics);
  renderAllCharts(mmMetrics);
  renderBatchSimulator(mmRawData, mmMetrics);
  renderExecSummary(mmRawData, mmMetrics);
}

// ---------------------------------------------------------------------------
// Keyboard navigation
// ---------------------------------------------------------------------------

function mmSetupKeyboard() {
  document.removeEventListener("keydown", mmKeyHandler);
  document.addEventListener("keydown", mmKeyHandler);
}

function mmKeyHandler(e) {
  if (!mmRawData || !mmMetrics) return;
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;

  if (mmSubtab === "simulator" && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
    e.preventDefault();
    const slider = document.getElementById("mmLimitSlider");
    if (!slider) return;
    const val = +slider.value + (e.key === "ArrowRight" ? 1 : -1);
    if (val >= 1 && val <= 10) {
      slider.value = val;
      slider.dispatchEvent(new Event("input"));
    }
    return;
  }

  if (mmSubtab !== "explorer") return;

  if (e.key === "Escape") {
    mmHighlightedPair = -1;
    mmShowDetailHint();
    renderOverlapTable(mmRawData, mmMetrics);
    e.preventDefault();
    return;
  }

  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (!mmFilteredPairs.length) return;
    const currentFilterIdx = mmFilteredPairs.findIndex(p => p.idx === mmHighlightedPair);
    let nextFilterIdx;
    if (e.key === "ArrowDown") {
      nextFilterIdx = currentFilterIdx < 0 ? 0 : Math.min(currentFilterIdx + 1, mmFilteredPairs.length - 1);
    } else {
      nextFilterIdx = currentFilterIdx <= 0 ? 0 : currentFilterIdx - 1;
    }
    const pair = mmFilteredPairs[nextFilterIdx];
    mmHighlightedPair = pair.idx;
    mmShowPairInDrawer(pair.idx, mmRawData, mmMetrics);
    renderOverlapTable(mmRawData, mmMetrics);
    const highlighted = document.querySelector("#mmOverlapBody tr.highlighted");
    if (highlighted) highlighted.scrollIntoView({ block: "nearest" });
  }
}

// ---------------------------------------------------------------------------
// Metrics computation
// ---------------------------------------------------------------------------

function computeMultiMergeMetrics(data) {
  const pairs = [];
  for (let i = 0; i < data.length - 1; i++) {
    const a = data[i];
    const b = data[i + 1];
    const filesA = new Set(a.changed_files || []);
    const filesB = new Set(b.changed_files || []);
    const overlappingFiles = [...filesA].filter(f => filesB.has(f));
    const svcsA = new Set(a.services || []);
    const svcsB = new Set(b.services || []);
    const overlappingServices = [...svcsA].filter(s => svcsB.has(s));
    const gap = (new Date(b.merged_at) - new Date(a.merged_at)) / 1000;

    let hasOverlap, overlapItems;
    if (mmOverlapMode === "service") {
      hasOverlap = overlappingServices.length > 0;
      overlapItems = overlappingServices;
    } else {
      hasOverlap = overlappingFiles.length > 0;
      overlapItems = overlappingFiles;
    }

    pairs.push({
      idx: i, iidA: a.iid, iidB: b.iid,
      fileOverlap: overlappingFiles.length > 0, overlappingFiles,
      svcOverlap: overlappingServices.length > 0, overlappingServices,
      overlap: hasOverlap, overlapItems,
      gapSeconds: gap,
    });
  }

  const fileIndependent = pairs.filter(p => !p.fileOverlap).length;
  const svcIndependent = pairs.filter(p => !p.svcOverlap).length;
  const independent = pairs.filter(p => !p.overlap).length;
  const total = pairs.length || 1;

  const batches = computeBatchRuns(pairs, data, 10, Infinity);
  const batchSizes = batches.map(b => b.length);
  const avgBatch = batchSizes.reduce((s, v) => s + v, 0) / (batchSizes.length || 1);

  const svcOverlapCounts = {};
  pairs.forEach(p => {
    p.overlappingServices.forEach(s => {
      svcOverlapCounts[s] = (svcOverlapCounts[s] || 0) + 1;
    });
  });

  const labelCounts = {};
  const authorCounts = {};
  const serviceCounts = {};
  data.forEach(mr => {
    (mr.labels || []).forEach(l => { labelCounts[l] = (labelCounts[l] || 0) + 1; });
    if (mr.author) authorCounts[mr.author] = (authorCounts[mr.author] || 0) + 1;
    (mr.services || []).forEach(s => { serviceCounts[s] = (serviceCounts[s] || 0) + 1; });
  });

  const tenantLabels = {};
  Object.entries(labelCounts).forEach(([l, c]) => {
    if (l.startsWith("tenant-")) tenantLabels[l.replace("tenant-", "")] = c;
  });

  const priorityLabels = {};
  Object.entries(labelCounts).forEach(([l, c]) => {
    const lower = l.toLowerCase();
    if (MM_PRIORITY_LABELS.includes(lower)) {
      priorityLabels[lower] = (priorityLabels[lower] || 0) + c;
      return;
    }
    const botMatch = lower.match(/^bot\/approved:\s*(.+)$/);
    if (botMatch && MM_PRIORITY_LABELS.includes(botMatch[1])) {
      priorityLabels[botMatch[1]] = (priorityLabels[botMatch[1]] || 0) + c;
    }
  });

  // Uncapped non-overlapping run lengths (using active overlap mode)
  const runLengths = [];
  let currentRun = 1;
  for (let i = 0; i < pairs.length; i++) {
    if (!pairs[i].overlap) {
      currentRun++;
    } else {
      runLengths.push(currentRun);
      currentRun = 1;
    }
  }
  runLengths.push(currentRun);
  const avgRunLength = runLengths.reduce((s, v) => s + v, 0) / (runLengths.length || 1);
  const maxRunLength = Math.max(...runLengths, 0);

  // Bot vs human classification
  const BOT_AUTHORS = ["devtools-bot", "app-sre-bot", "openshift-bot", "cluster-update-bot"];
  let botCount = 0;
  let humanCount = 0;
  data.forEach(mr => {
    if (!mr.author || BOT_AUTHORS.includes(mr.author) || mr.author.endsWith("-bot")) botCount++;
    else humanCount++;
  });

  // File overlap by service (conflict hotspots)
  const fileOverlapByService = {};
  pairs.filter(p => p.fileOverlap).forEach(p => {
    const svcsA = data[p.idx].services || [];
    const svcsB = data[p.idx + 1].services || [];
    [...new Set([...svcsA, ...svcsB])].forEach(s => {
      fileOverlapByService[s] = (fileOverlapByService[s] || 0) + 1;
    });
  });

  const spanHours = data.length >= 2
    ? (new Date(data[data.length - 1].merged_at) - new Date(data[0].merged_at)) / 3600000
    : 1;
  const baseMph = data.length / spanHours;

  return {
    pairs, fileIndependencePct: (fileIndependent / total) * 100,
    svcIndependencePct: (svcIndependent / total) * 100,
    independencePct: (independent / total) * 100,
    overlapMode: mmOverlapMode,
    avgBatchSize: avgBatch, batches, batchSizes, svcOverlapCounts,
    tenantLabels, priorityLabels, labelCounts, authorCounts, serviceCounts,
    baseMph, totalMRs: data.length, spanHours,
    runLengths, avgRunLength, maxRunLength,
    botCount, humanCount, fileOverlapByService,
  };
}

function computeBatchRuns(pairs, data, limit, maxGapSeconds) {
  if (!data || !data.length) return [];
  const maxGap = maxGapSeconds == null ? Infinity : maxGapSeconds;
  const batches = [];
  let current = [0];
  let batchSet = mmOverlapMode === "service"
    ? new Set(data[0].services || [])
    : new Set(data[0].changed_files || []);
  for (let i = 0; i < pairs.length; i++) {
    const nextMrIdx = i + 1;
    const nextItems = mmOverlapMode === "service"
      ? new Set(data[nextMrIdx].services || [])
      : new Set(data[nextMrIdx].changed_files || []);
    const hasOverlap = [...nextItems].some(f => batchSet.has(f));
    const gapOk = pairs[i].gapSeconds <= maxGap;
    if (!hasOverlap && gapOk && current.length < limit) {
      current.push(nextMrIdx);
      nextItems.forEach(f => batchSet.add(f));
    } else {
      batches.push(current);
      current = [nextMrIdx];
      batchSet = new Set(nextItems);
    }
  }
  if (current.length) batches.push(current);
  return batches;
}

function computeRealisticThroughput(batches, data) {
  if (!batches.length || !data.length) return { mph: 0, effectiveHours: 0 };
  const firstMerge = new Date(data[0].merged_at).getTime();
  const lastMerge = new Date(data[data.length - 1].merged_at).getTime();
  const spanHours = (lastMerge - firstMerge) / 3600000 || 1;
  const totalMRs = data.length;
  const mph = totalMRs / (spanHours * (batches.length / totalMRs));
  return { mph, effectiveHours: spanHours, batchCount: batches.length, totalMRs };
}

// ---------------------------------------------------------------------------
// Hero cards
// ---------------------------------------------------------------------------

function renderMultiMergeStats(metrics) {
  const container = document.getElementById("mmHeroCards");
  if (!container) return;
  const overlaps = metrics.pairs.filter(p => p.overlap).length;
  const gaps = metrics.pairs.map(p => p.gapSeconds).sort((a, b) => a - b);
  const medianGap = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 0;
  const p95Gap = gaps.length ? gaps[Math.floor(gaps.length * 0.95)] : 0;
  const pairsUnder10m = metrics.pairs.filter(p => p.gapSeconds <= 600).length;
  const leafMRs = mmRawData ? mmRawData.filter(mr => (mr.changed_files || []).length <= 2).length : 0;
  const leafPct = metrics.totalMRs ? ((leafMRs / metrics.totalMRs) * 100).toFixed(0) : 0;

  const batches5 = computeBatchRuns(metrics.pairs, mmRawData, 5, 600);
  const tput5 = computeRealisticThroughput(batches5, mmRawData);
  const gainX = (tput5.mph / metrics.baseMph).toFixed(1);

  const modeLabel = mmOverlapMode === "service" ? "Service" : "File";

  container.innerHTML = `
    <div class="hero-card"><div class="label">${modeLabel} Independence</div><div class="value">${metrics.independencePct.toFixed(1)}%</div><div class="sub">${overlaps} overlaps / ${metrics.pairs.length} pairs</div></div>
    <div class="hero-card"><div class="label">Avg Run Length</div><div class="value">${metrics.avgRunLength.toFixed(1)}</div><div class="sub">max: ${metrics.maxRunLength} · consecutive non-overlapping</div></div>
    <div class="hero-card"><div class="label">Median Gap</div><div class="value">${formatGap(medianGap)}</div><div class="sub">p95: ${formatGap(p95Gap)} · ${pairsUnder10m} pairs ≤10m</div></div>
    <div class="hero-card"><div class="label">Predicted (limit=5, ≤10m)</div><div class="value">${tput5.mph.toFixed(1)}/hr</div><div class="sub">${gainX}x gain (base: ${metrics.baseMph.toFixed(1)}/hr)</div></div>
    <div class="hero-card"><div class="label">Leaf MRs (≤2 files)</div><div class="value">${leafPct}%</div><div class="sub">${leafMRs}/${metrics.totalMRs} — best batch candidates</div></div>
    <div class="hero-card"><div class="label">Bot Authored</div><div class="value">${metrics.totalMRs ? ((metrics.botCount / metrics.totalMRs) * 100).toFixed(0) : 0}%</div><div class="sub">${metrics.botCount} bot · ${metrics.humanCount} human</div></div>
  `;
}

// ---------------------------------------------------------------------------
// Stats table
// ---------------------------------------------------------------------------

function renderStatsTable(data, metrics) {
  const el = document.getElementById("mmStatsTable");
  if (!el || !data.length) return;

  const gaps = metrics.pairs.map(p => p.gapSeconds).sort((a, b) => a - b);
  const medianGap = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 0;
  const p95Gap = gaps.length ? gaps[Math.floor(gaps.length * 0.95)] : 0;
  const avgGap = gaps.length ? gaps.reduce((s, v) => s + v, 0) / gaps.length : 0;
  const pairsUnder10m = metrics.pairs.filter(p => p.gapSeconds <= 600).length;
  const overlaps = metrics.pairs.filter(p => p.overlap).length;
  const leafMRs = data.filter(mr => (mr.changed_files || []).length <= 2).length;
  const batches5 = computeBatchRuns(metrics.pairs, data, 5, 600);
  const tput5 = computeRealisticThroughput(batches5, data);
  const topAuthor = Object.entries(metrics.authorCounts).sort((a, b) => b[1] - a[1])[0];
  const topService = Object.entries(metrics.serviceCounts || {}).sort((a, b) => b[1] - a[1])[0];
  const modeLabel = mmOverlapMode === "service" ? "Service" : "File";

  const start = new Date(data[0].merged_at);
  const end = new Date(data[data.length - 1].merged_at);
  const fmt = { month: "short", day: "numeric", year: "numeric" };

  const rows = [
    ["Period", `${start.toLocaleDateString("en-US", fmt)} — ${end.toLocaleDateString("en-US", fmt)}`],
    ["Total MRs", data.length],
    ["Overlap Mode", modeLabel],
    ["Span", `${metrics.spanHours.toFixed(1)} hours`],
    ["Baseline Throughput", `${metrics.baseMph.toFixed(1)} merges/hr`],
    ["Predicted Throughput (limit=5, ≤10m)", `${tput5.mph.toFixed(1)} merges/hr (${(tput5.mph / metrics.baseMph).toFixed(1)}x)`],
    [`${modeLabel} Independence`, `${metrics.independencePct.toFixed(1)}% (${overlaps} overlaps / ${metrics.pairs.length} pairs)`],
    ["Avg Run Length", `${metrics.avgRunLength.toFixed(1)} (max: ${metrics.maxRunLength})`],
    ["Median Gap", formatGap(medianGap)],
    ["Avg Gap", formatGap(avgGap)],
    ["p95 Gap", formatGap(p95Gap)],
    ["Pairs ≤ 10m", `${pairsUnder10m} / ${metrics.pairs.length} (${(pairsUnder10m / metrics.pairs.length * 100).toFixed(0)}%)`],
    ["Leaf MRs (≤2 files)", `${leafMRs} / ${data.length} (${(leafMRs / data.length * 100).toFixed(0)}%)`],
    ["Bot Authored", `${metrics.botCount} (${(metrics.botCount / data.length * 100).toFixed(0)}%)`],
    ["Human Authored", `${metrics.humanCount} (${(metrics.humanCount / data.length * 100).toFixed(0)}%)`],
    ["Top Author", topAuthor ? `${topAuthor[0]} (${topAuthor[1]} MRs)` : "—"],
    ["Top Service", topService ? `${topService[0]} (${topService[1]} MRs)` : "—"],
  ];

  el.innerHTML = `<table class="mm-stats-tbl"><tbody>${rows.map(([k, v]) =>
    `<tr><td class="mm-stats-key">${k}</td><td class="mm-stats-val">${v}</td></tr>`
  ).join("")}</tbody></table>`;
}

// ---------------------------------------------------------------------------
// Time context
// ---------------------------------------------------------------------------

function renderTimeContext(data, metrics) {
  const container = document.getElementById("mmDensityBar");
  if (!container || data.length < 2) return;
  const start = new Date(data[0].merged_at);
  const end = new Date(data[data.length - 1].merged_at);
  const fmt = { weekday: "short", month: "short", day: "numeric" };
  const overlaps = metrics.pairs.filter(p => p.overlap).length;
  const modeLabel = mmOverlapMode === "service" ? "service" : "file";
  container.innerHTML = `<span style="font-size:0.74rem;color:var(--muted)">` +
    `${start.toLocaleDateString("en-US", fmt)} — ${end.toLocaleDateString("en-US", fmt)}` +
    ` · ${data.length} MRs` +
    ` · <span style="color:#f85149">${overlaps} ${modeLabel} overlaps</span>` +
    ` · ${metrics.independencePct.toFixed(0)}% independent` +
    `</span>`;
}

// ---------------------------------------------------------------------------
// Charts
// ---------------------------------------------------------------------------

function renderAllCharts(metrics) {
  const gapSelect = document.getElementById("mmBatchGapSelect");
  const maxGap = gapSelect && gapSelect.value ? +gapSelect.value : Infinity;
  renderBatchDistChart(metrics, maxGap);
  renderGapDistChart(metrics);
  renderRunLengthChart(metrics);
  renderBotHumanChart(metrics);
  renderConflictChart(metrics);
  renderTenantChart(metrics);
  renderPriorityChart(metrics);
  renderServiceOverlapChart(metrics);
  renderDailyRateChart(metrics);
  renderHourlyChart(metrics);
  renderFileCountChart(metrics);
  renderExecSummary(metrics);
}

function renderBatchDistChart(metrics, maxGap) {
  const canvas = document.getElementById("mmBatchDistCanvas");
  if (!canvas) return;
  mqDestroyChartIfPresent(mmCharts.batchDist);

  const gap = maxGap == null ? Infinity : maxGap;
  const labels = [];
  const batchedMRs = [];
  const savedCycles = [];
  for (let cap = 1; cap <= 10; cap++) {
    const batches = computeBatchRuns(metrics.pairs, mmRawData, cap, gap);
    const multiBatches = batches.filter(b => b.length > 1);
    const mrsInMulti = multiBatches.reduce((s, b) => s + b.length, 0);
    const cyclesSaved = mrsInMulti - multiBatches.length;
    labels.push(String(cap));
    batchedMRs.push(mrsInMulti);
    savedCycles.push(cyclesSaved);
  }

  mmCharts.batchDist = new Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "MRs in multi-batches", data: batchedMRs, backgroundColor: "rgba(88,166,255,0.6)", borderColor: "#58a6ff", borderWidth: 1 },
        { label: "CI cycles saved", data: savedCycles, backgroundColor: "rgba(63,185,80,0.5)", borderColor: "#3fb950", borderWidth: 1 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: mqThemedLegend() },
      scales: {
        x: mqThemedScale({ title: { display: true, text: "Merge Limit" } }),
        y: mqThemedScale({ beginAtZero: true, title: { display: true, text: "Count" } }),
      },
    },
  });
}

function renderTenantChart(metrics) {
  const canvas = document.getElementById("mmTenantCanvas");
  if (!canvas) return;
  mqDestroyChartIfPresent(mmCharts.tenant);
  const sorted = Object.entries(metrics.tenantLabels).sort((a, b) => b[1] - a[1]).slice(0, 15);
  mmCharts.tenant = new Chart(canvas, {
    type: "bar",
    data: { labels: sorted.map(([l]) => l), datasets: [{ data: sorted.map(([, v]) => v), backgroundColor: "rgba(163,113,247,0.6)", borderColor: "#a371f7", borderWidth: 1 }] },
    options: { indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: mqThemedScale({ beginAtZero: true }), y: mqThemedScale() } },
  });
}

function renderPriorityChart(metrics) {
  const canvas = document.getElementById("mmPriorityCanvas");
  if (!canvas) return;
  mqDestroyChartIfPresent(mmCharts.priority);
  const entries = Object.entries(metrics.priorityLabels).sort((a, b) => MM_PRIORITY_LABELS.indexOf(a[0].toLowerCase()) - MM_PRIORITY_LABELS.indexOf(b[0].toLowerCase()));
  if (!entries.length) return;
  mmCharts.priority = new Chart(canvas, {
    type: "doughnut",
    data: { labels: entries.map(([l]) => l), datasets: [{ data: entries.map(([, v]) => v), backgroundColor: MQSIM_POLICY_COLOR_PALETTE.slice(0, entries.length) }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: mqThemedLegend({ position: "right" }) } },
  });
}

function renderServiceOverlapChart(metrics) {
  const canvas = document.getElementById("mmServiceOverlapCanvas");
  if (!canvas) return;
  mqDestroyChartIfPresent(mmCharts.svcOverlap);
  const sorted = Object.entries(metrics.svcOverlapCounts).sort((a, b) => b[1] - a[1]).slice(0, 10);
  if (!sorted.length) return;
  mmCharts.svcOverlap = new Chart(canvas, {
    type: "bar",
    data: { labels: sorted.map(([l]) => l), datasets: [{ data: sorted.map(([, v]) => v), backgroundColor: "rgba(248,81,73,0.5)", borderColor: "#f85149", borderWidth: 1 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: mqThemedScale(), y: mqThemedScale({ beginAtZero: true, title: { display: true, text: "Overlaps" } }) } },
  });
}

function renderDailyRateChart() {
  const canvas = document.getElementById("mmDailyRateCanvas");
  if (!canvas || !mmRawData) return;
  mqDestroyChartIfPresent(mmCharts.dailyRate);

  const days = {};
  mmRawData.forEach(mr => {
    const d = new Date(mr.merged_at);
    const key = d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
    days[key] = (days[key] || 0) + 1;
  });
  const labels = Object.keys(days);
  const values = Object.values(days);

  mmCharts.dailyRate = new Chart(canvas, {
    type: "bar",
    data: { labels, datasets: [{ label: "Merges", data: values, backgroundColor: "rgba(46,160,67,0.5)", borderColor: "#2ea043", borderWidth: 1 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: mqThemedScale(), y: mqThemedScale({ beginAtZero: true }) } },
  });
}

function renderHourlyChart() {
  const canvas = document.getElementById("mmHourlyCanvas");
  if (!canvas || !mmRawData) return;
  mqDestroyChartIfPresent(mmCharts.hourly);

  const hours = new Array(24).fill(0);
  mmRawData.forEach(mr => {
    const h = new Date(mr.merged_at).getHours();
    hours[h]++;
  });

  mmCharts.hourly = new Chart(canvas, {
    type: "bar",
    data: { labels: hours.map((_, i) => i + ":00"), datasets: [{ data: hours, backgroundColor: "rgba(210,153,34,0.5)", borderColor: "#d29922", borderWidth: 1 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: mqThemedScale(), y: mqThemedScale({ beginAtZero: true, title: { display: true, text: "Merges" } }) } },
  });
}

function renderFileCountChart() {
  const canvas = document.getElementById("mmFileCountCanvas");
  if (!canvas || !mmRawData) return;
  mqDestroyChartIfPresent(mmCharts.fileCount);

  const buckets = { "1": 0, "2": 0, "3-5": 0, "6-10": 0, "11-20": 0, "20+": 0 };
  mmRawData.forEach(mr => {
    const n = (mr.changed_files || []).length;
    if (n <= 1) buckets["1"]++;
    else if (n === 2) buckets["2"]++;
    else if (n <= 5) buckets["3-5"]++;
    else if (n <= 10) buckets["6-10"]++;
    else if (n <= 20) buckets["11-20"]++;
    else buckets["20+"]++;
  });

  mmCharts.fileCount = new Chart(canvas, {
    type: "bar",
    data: { labels: Object.keys(buckets), datasets: [{ data: Object.values(buckets), backgroundColor: "rgba(121,192,255,0.5)", borderColor: "#79c0ff", borderWidth: 1 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: mqThemedScale({ title: { display: true, text: "Files changed" } }), y: mqThemedScale({ beginAtZero: true, title: { display: true, text: "MRs" } }) } },
  });
}

// ---------------------------------------------------------------------------
// Independence Profile charts
// ---------------------------------------------------------------------------

function renderGapDistChart(metrics) {
  const canvas = document.getElementById("mmGapDistCanvas");
  if (!canvas) return;
  mqDestroyChartIfPresent(mmCharts.gapDist);

  const buckets = { "0-2m": 0, "2-5m": 0, "5-10m": 0, "10-30m": 0, "30m-1h": 0, "1-4h": 0, "4h+": 0 };
  metrics.pairs.forEach(p => {
    const g = p.gapSeconds;
    if (g <= 120) buckets["0-2m"]++;
    else if (g <= 300) buckets["2-5m"]++;
    else if (g <= 600) buckets["5-10m"]++;
    else if (g <= 1800) buckets["10-30m"]++;
    else if (g <= 3600) buckets["30m-1h"]++;
    else if (g <= 14400) buckets["1-4h"]++;
    else buckets["4h+"]++;
  });

  const colors = Object.keys(buckets).map((_, i) => i < 3 ? "rgba(63,185,80,0.6)" : (i < 5 ? "rgba(210,153,34,0.5)" : "rgba(248,81,73,0.4)"));
  mmCharts.gapDist = new Chart(canvas, {
    type: "bar",
    data: { labels: Object.keys(buckets), datasets: [{ data: Object.values(buckets), backgroundColor: colors, borderWidth: 1, borderColor: colors.map(c => c.replace("0.6", "1").replace("0.5", "1").replace("0.4", "1")) }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: mqThemedScale({ title: { display: true, text: "Gap between merges" } }), y: mqThemedScale({ beginAtZero: true, title: { display: true, text: "Pairs" } }) } },
  });
}

function renderRunLengthChart(metrics) {
  const canvas = document.getElementById("mmRunLengthCanvas");
  if (!canvas) return;
  mqDestroyChartIfPresent(mmCharts.runLength);

  const buckets = { "1": 0, "2-3": 0, "4-5": 0, "6-10": 0, "11-20": 0, "21-50": 0, "50+": 0 };
  metrics.runLengths.forEach(r => {
    if (r <= 1) buckets["1"]++;
    else if (r <= 3) buckets["2-3"]++;
    else if (r <= 5) buckets["4-5"]++;
    else if (r <= 10) buckets["6-10"]++;
    else if (r <= 20) buckets["11-20"]++;
    else if (r <= 50) buckets["21-50"]++;
    else buckets["50+"]++;
  });

  mmCharts.runLength = new Chart(canvas, {
    type: "bar",
    data: { labels: Object.keys(buckets), datasets: [{ data: Object.values(buckets), backgroundColor: "rgba(88,166,255,0.6)", borderColor: "#58a6ff", borderWidth: 1 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: mqThemedScale({ title: { display: true, text: "Consecutive non-overlapping MRs" } }), y: mqThemedScale({ beginAtZero: true, title: { display: true, text: "Runs" } }) } },
  });
}

function renderBotHumanChart(metrics) {
  const canvas = document.getElementById("mmBotHumanCanvas");
  if (!canvas) return;
  mqDestroyChartIfPresent(mmCharts.botHuman);

  mmCharts.botHuman = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: ["Bot", "Human"],
      datasets: [{ data: [metrics.botCount, metrics.humanCount], backgroundColor: ["rgba(163,113,247,0.7)", "rgba(88,166,255,0.7)"] }],
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: mqThemedLegend({ position: "right" }) } },
  });
}

function renderConflictChart(metrics) {
  const canvas = document.getElementById("mmConflictCanvas");
  if (!canvas) return;
  mqDestroyChartIfPresent(mmCharts.conflict);

  const sorted = Object.entries(metrics.fileOverlapByService).sort((a, b) => b[1] - a[1]).slice(0, 10);
  if (!sorted.length) return;

  mmCharts.conflict = new Chart(canvas, {
    type: "bar",
    data: { labels: sorted.map(([l]) => l), datasets: [{ data: sorted.map(([, v]) => v), backgroundColor: "rgba(248,81,73,0.6)", borderColor: "#f85149", borderWidth: 1 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: mqThemedScale(), y: mqThemedScale({ beginAtZero: true, title: { display: true, text: "File overlaps" } }) } },
  });
}

function renderExecSummary(metrics) {
  const el = document.getElementById("mmExecSummary");
  if (!el) return;

  const batches5 = computeBatchRuns(metrics.pairs, mmRawData, 5, 600);
  const tput5 = computeRealisticThroughput(batches5, mmRawData);
  const gainX = (tput5.mph / metrics.baseMph).toFixed(1);
  const pairsUnder10m = metrics.pairs.filter(p => p.gapSeconds <= 600).length;
  const pctUnder10m = ((pairsUnder10m / (metrics.pairs.length || 1)) * 100).toFixed(0);
  const modeLabel = mmOverlapMode === "service" ? "service" : "file";

  el.innerHTML = `<div style="font-size:0.82rem;line-height:1.6">
    <strong style="font-size:0.9rem">Multi-Merge Feasibility</strong> <span style="color:var(--muted);font-size:0.74rem">(overlap: ${modeLabel})</span><br>
    <span style="color:#3fb950;font-weight:600">${metrics.independencePct.toFixed(0)}%</span> of consecutive MR pairs have zero ${modeLabel} overlap.
    Average non-overlapping run: <strong>${metrics.avgRunLength.toFixed(1)} MRs</strong> (max: ${metrics.maxRunLength}).
    <span style="color:var(--muted)">${pctUnder10m}% of pairs arrive within 10 minutes.</span><br>
    At <code>Merge Limit = 5</code> with ≤10m gap: <strong>${tput5.mph.toFixed(1)} merges/hr</strong> (${gainX}x vs ${metrics.baseMph.toFixed(1)}/hr baseline).
  </div>`;
}

// ---------------------------------------------------------------------------
// Detail drawer
// ---------------------------------------------------------------------------

function mmShowDetailHint() {
  const detail = document.getElementById("mmMRDetail");
  if (!detail) return;
  detail.innerHTML = `<span class="mm-detail-hint">Select a row to see pair details · Arrow keys to navigate · Escape to deselect</span>`;
}

function mmShowPairInDrawer(pairIdx, data, metrics) {
  const detail = document.getElementById("mmMRDetail");
  if (!detail) return;
  const pair = metrics.pairs[pairIdx];
  if (!pair) return;
  const mrA = data[pairIdx];
  const mrB = data[pairIdx + 1];
  if (!mrA || !mrB) return;

  const overlapParts = [];
  if (pair.svcOverlap) {
    overlapParts.push(`<div style="grid-column:1/-1;padding:0.5rem;border:1px solid rgba(210,153,34,0.3);border-radius:6px;background:rgba(210,153,34,0.05)">
      <span style="color:#d29922;font-weight:600;font-size:0.74rem">Service overlap:</span>
      <span style="font-size:0.72rem"> ${pair.overlappingServices.join(", ")}</span></div>`);
  }
  if (pair.fileOverlap) {
    overlapParts.push(`<div style="grid-column:1/-1;padding:0.5rem;border:1px solid rgba(248,81,73,0.3);border-radius:6px;background:rgba(248,81,73,0.05)">
      <span style="color:#f85149;font-weight:600;font-size:0.74rem">File overlap:</span>
      <span style="font-size:0.72rem"> ${pair.overlappingFiles.join(", ")}</span></div>`);
  }
  if (!overlapParts.length) {
    overlapParts.push(`<div style="grid-column:1/-1;padding:0.4rem;border:1px solid rgba(63,185,80,0.3);border-radius:6px;background:rgba(63,185,80,0.05)">
      <span style="color:#3fb950;font-size:0.74rem">No overlap — batchable (gap: ${formatGap(pair.gapSeconds)})</span></div>`);
  }
  const overlapSection = overlapParts.join("");

  detail.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.75rem;font-size:0.76rem">
      ${mmRenderMRCard(mrA, pairIdx, data.length)}
      ${mmRenderMRCard(mrB, pairIdx + 1, data.length)}
      ${overlapSection}
    </div>`;
}

function mmShowMRInDrawer(idx, data, metrics) {
  const detail = document.getElementById("mmMRDetail");
  if (!detail || !data[idx]) return;
  const mr = data[idx];
  detail.innerHTML = `<div style="display:grid;grid-template-columns:1fr;gap:0.5rem;font-size:0.76rem">${mmRenderMRCard(mr, idx, data.length)}</div>`;
}

function mmRenderMRCard(mr, idx, total) {
  const ts = new Date(mr.merged_at);
  const timeStr = ts.toLocaleString("en-US", { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const files = (mr.changed_files || []);
  return `<div style="padding:0.6rem;border:1px solid var(--border);border-radius:8px;background:var(--panel)">
    <div style="font-weight:600;margin-bottom:0.3rem">!${mr.iid} <span style="color:var(--muted);font-weight:400;font-size:0.72rem">#${idx + 1}/${total}</span></div>
    <div style="font-size:0.72rem;color:var(--muted);margin-bottom:0.4rem">${escHtml(mr.title || "")}</div>
    <div style="display:grid;grid-template-columns:auto 1fr;gap:0.2rem 0.6rem;font-size:0.72rem">
      <span style="color:var(--muted)">Merged</span><span>${timeStr}</span>
      <span style="color:var(--muted)">Author</span><span>${escHtml(mr.author || "—")}</span>
      <span style="color:var(--muted)">Services</span><span>${(mr.services || []).join(", ") || "—"}</span>
      <span style="color:var(--muted)">Labels</span><span style="word-break:break-word">${(mr.labels || []).join(", ") || "—"}</span>
      <span style="color:var(--muted)">Files (${files.length})</span><span style="word-break:break-all">${files.join("<br>") || "—"}</span>
    </div>
  </div>`;
}

function escHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ---------------------------------------------------------------------------
// Legacy compatibility
// ---------------------------------------------------------------------------

function mmStopPlayback() {
  if (mmPlayTimer) { clearInterval(mmPlayTimer); mmPlayTimer = null; }
}

function mmUpdateScrubber(idx, data, metrics) {
  mmShowMRInDrawer(idx, data, metrics);
}

// ---------------------------------------------------------------------------
// Overlap Explorer Table
// ---------------------------------------------------------------------------

function renderOverlapTable(data, metrics) {
  const body = document.getElementById("mmOverlapBody");
  if (!body) return;

  const filtered = getFilteredPairs(metrics.pairs, data);
  mmFilteredPairs = filtered;
  const sorted = sortPairs(filtered);

  body.innerHTML = sorted.map(p => {
    const cls = p.overlap ? "overlap" : "";
    const hl = p.idx === mmHighlightedPair ? " highlighted" : "";
    const gap = formatGap(p.gapSeconds);

    const overlapTypes = [];
    if (p.svcOverlap) overlapTypes.push("Service");
    if (p.fileOverlap) overlapTypes.push("File");
    const overlapCell = overlapTypes.length ? overlapTypes.join(", ") : "—";

    let items = "—";
    if (p.fileOverlap || p.svcOverlap) {
      const parts = [];
      if (mmOverlapMode === "service") {
        if (p.svcOverlap) parts.push(p.overlappingServices.join(", "));
        if (p.fileOverlap) {
          const fileStr = p.overlappingFiles.map(f => f.split("/").pop()).join(", ");
          parts.push(`<span style="color:var(--muted)">· ${fileStr}</span>`);
        }
      } else {
        if (p.fileOverlap) parts.push(p.overlappingFiles.map(f => f.split("/").pop()).join(", "));
        if (p.svcOverlap) {
          parts.push(`<span style="color:var(--muted)">· ${p.overlappingServices.join(", ")}</span>`);
        }
      }
      items = parts.join(" ");
    }
    return `<tr class="${cls}${hl}" data-idx="${p.idx}">
      <td>${p.idx + 1}</td>
      <td><a class="mm-iid-link" data-mr="${p.idx}">!${p.iidA}</a></td>
      <td><a class="mm-iid-link" data-mr="${p.idx + 1}">!${p.iidB}</a></td>
      <td>${overlapCell}</td>
      <td>${items}</td>
      <td>${gap}</td>
    </tr>`;
  }).join("");

  body.querySelectorAll(".mm-iid-link").forEach(link => {
    link.onclick = (e) => {
      e.stopPropagation();
      mmShowMRInDrawer(+link.dataset.mr, data, metrics);
    };
  });

  body.querySelectorAll("tr").forEach(tr => {
    tr.onclick = () => {
      const idx = +tr.dataset.idx;
      mmHighlightedPair = idx;
      mmShowPairInDrawer(idx, data, metrics);
      renderOverlapTable(data, metrics);
    };
  });

  const ths = document.querySelectorAll("#mmOverlapTable th");
  ths.forEach(th => {
    th.onclick = () => {
      const col = th.dataset.col;
      if (mmSortCol === col) mmSortAsc = !mmSortAsc;
      else { mmSortCol = col; mmSortAsc = true; }
      renderOverlapTable(data, metrics);
    };
  });
}

function getFilteredPairs(pairs, data) {
  const overlapFilter = document.getElementById("mmFilterOverlap");
  const gapFilter = document.getElementById("mmFilterGap");
  let result = [...pairs];
  if (overlapFilter) {
    const v = overlapFilter.value;
    if (v === "overlap") result = result.filter(p => p.overlap);
    else if (v === "independent") result = result.filter(p => !p.overlap);
  }
  if (gapFilter && gapFilter.value !== "") {
    const maxSec = +gapFilter.value;
    if (Number.isFinite(maxSec)) result = result.filter(p => p.gapSeconds <= maxSec);
  }
  return result;
}

function sortPairs(pairs) {
  const col = mmSortCol;
  const dir = mmSortAsc ? 1 : -1;
  return [...pairs].sort((a, b) => {
    let va, vb;
    switch (col) {
      case "idx": va = a.idx; vb = b.idx; break;
      case "iidA": va = +a.iidA; vb = +b.iidA; break;
      case "iidB": va = +a.iidB; vb = +b.iidB; break;
      case "overlap": va = a.overlap ? 1 : 0; vb = b.overlap ? 1 : 0; break;
      case "gap": va = a.gapSeconds; vb = b.gapSeconds; break;
      default: va = a.idx; vb = b.idx;
    }
    return (va - vb) * dir;
  });
}

function formatGap(seconds) {
  if (seconds < 60) return seconds.toFixed(0) + "s";
  if (seconds < 3600) return (seconds / 60).toFixed(1) + "m";
  return (seconds / 3600).toFixed(1) + "h";
}

// ---------------------------------------------------------------------------
// Batch Simulator
// ---------------------------------------------------------------------------

function renderBatchSimulator(data, metrics) {
  const slider = document.getElementById("mmLimitSlider");
  const valLabel = document.getElementById("mmLimitVal");
  const gapSelect = document.getElementById("mmBatchGapSelect");

  function refresh() {
    const limit = slider ? +slider.value : 5;
    const maxGap = gapSelect && gapSelect.value ? +gapSelect.value : Infinity;
    if (valLabel) valLabel.textContent = limit;
    updateBatchSummary(limit, maxGap, data, metrics);
    renderThroughputChart(data, metrics, limit, maxGap);
    renderBatchDistChart(metrics, maxGap);
  }

  if (slider) slider.oninput = refresh;
  if (gapSelect) gapSelect.onchange = refresh;
  refresh();
}

function updateBatchSummary(limit, maxGap, data, metrics) {
  const el = document.getElementById("mmBatchSummary");
  if (!el) return;
  const batches = computeBatchRuns(metrics.pairs, data, limit, maxGap);
  const tput = computeRealisticThroughput(batches, data);
  const avgSize = data.length / (batches.length || 1);
  el.textContent = `${batches.length} batches · avg ${avgSize.toFixed(1)} MRs/batch → ${tput.mph.toFixed(1)} merges/hr (base: ${metrics.baseMph.toFixed(1)})`;
}

function renderThroughputChart(data, metrics, activeLimit, maxGap) {
  const canvas = document.getElementById("mmThroughputCanvas");
  if (!canvas) return;
  mqDestroyChartIfPresent(mmCharts.throughput);

  const limits = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
  const values = limits.map(l => {
    const batches = computeBatchRuns(metrics.pairs, data, l, maxGap);
    return computeRealisticThroughput(batches, data).mph;
  });
  const pointRadii = limits.map(l => l === activeLimit ? 8 : 4);
  const pointColors = limits.map(l => l === activeLimit ? "#fff" : "#58a6ff");

  mmCharts.throughput = new Chart(canvas, {
    type: "line",
    data: {
      labels: limits.map(String),
      datasets: [{
        label: "Predicted merges/hr",
        data: values,
        borderColor: "#58a6ff",
        backgroundColor: "rgba(88,166,255,0.1)",
        fill: true, tension: 0.3,
        pointRadius: pointRadii,
        pointBackgroundColor: pointColors,
        pointBorderColor: pointColors,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: mqThemedLegend() },
      scales: {
        x: mqThemedScale({ title: { display: true, text: "Merge Limit" } }),
        y: mqThemedScale({ beginAtZero: true, title: { display: true, text: "Merges/hr" } }),
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Filter population
// ---------------------------------------------------------------------------

function populateFilters(data) {
  const gapSelect = document.getElementById("mmFilterGap");
  if (gapSelect) {
    gapSelect.innerHTML = MM_GAP_OPTIONS.map(opt => {
      const val = opt.maxSeconds === Infinity ? "" : opt.maxSeconds;
      return `<option value="${val}">${opt.label}</option>`;
    }).join("");
    gapSelect.value = "600";
    gapSelect.onchange = () => renderOverlapTable(mmRawData, mmMetrics);
  }
  const overlapFilter = document.getElementById("mmFilterOverlap");
  if (overlapFilter) overlapFilter.onchange = () => renderOverlapTable(mmRawData, mmMetrics);
}
