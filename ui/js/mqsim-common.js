/*
 * Shared primitives for Merge Queue Sim UI modules.
 * Client-side only: plain browser JavaScript, no build step.
 */

const MQSIM_LEGEND_FONT_SIZE = 14;
const MQSIM_CHART_FONT_SIZE = 13;
const MQSIM_POLICY_COLOR_PALETTE = ["#58a6ff", "#a371f7", "#2ea043", "#d29922", "#f85149", "#79c0ff", "#d2a8ff", "#56d364"];
const MQSIM_THEME = Object.freeze({
  ticks: "#8b949e",
  grid: "#21262d",
  legendText: "#8b949e",
});

// Chart.js defaults to 12px for ticks and tooltips, the smallest text on a
// chart-heavy screen. Lift it toward the body size; zoom scales this too, so
// the point is the ratio, not the absolute.
if (typeof Chart !== "undefined" && Chart.defaults) {
  Chart.defaults.font.size = MQSIM_CHART_FONT_SIZE;
}

// Expose constants inspected by the CSP-safe runtime self-check.
globalThis.MQSIM_LEGEND_FONT_SIZE = MQSIM_LEGEND_FONT_SIZE;
globalThis.MQSIM_CHART_FONT_SIZE = MQSIM_CHART_FONT_SIZE;
globalThis.MQSIM_POLICY_COLOR_PALETTE = MQSIM_POLICY_COLOR_PALETTE;

function mqParseNumber(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function mqParseNumberOrNull(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function mqAverage(values, fallback = NaN) {
  if (!Array.isArray(values) || !values.length) return fallback;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function mqFormatFixedOrDash(value, digits = 2, dash = "—") {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : dash;
}

function mqPaletteColor(index, palette = MQSIM_POLICY_COLOR_PALETTE, fallback = "#8b949e") {
  if (!Array.isArray(palette) || !palette.length) return fallback;
  const i = Number(index);
  if (!Number.isFinite(i)) return fallback;
  return palette[Math.abs(Math.trunc(i)) % palette.length] || fallback;
}

function mqEscapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function mqParseCsvRecords(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    const nxt = text[i + 1];
    if (ch === "\"") {
      if (inQuotes && nxt === "\"") {
        cell += "\"";
        i++;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }
    if (ch === "," && !inQuotes) {
      row.push(cell);
      cell = "";
      continue;
    }
    if ((ch === "\n" || ch === "\r") && !inQuotes) {
      if (ch === "\r" && nxt === "\n") i++;
      row.push(cell);
      cell = "";
      if (row.some(c => c !== "")) rows.push(row);
      row = [];
      continue;
    }
    cell += ch;
  }
  if (cell.length || row.length) {
    row.push(cell);
    if (row.some(c => c !== "")) rows.push(row);
  }
  if (!rows.length) return { headers: [], rows: [] };
  const headers = rows[0].map(h => h.trim());
  const out = rows.slice(1).map(cols => {
    const rec = {};
    headers.forEach((h, i) => {
      rec[h] = (cols[i] ?? "").trim();
    });
    return rec;
  });
  return { headers, rows: out };
}

function mqDetectCsvType(text) {
  const { headers } = mqParseCsvRecords(text);
  const h = new Set(headers);
  // run_standalone.py writes "trial" as the first column; "run" is accepted
  // for older files. loadMcCSVText itself is positional, so either works.
  if ((h.has("trial") || h.has("run")) && h.has("policy")) return "monteCarlo";
  if (
    h.has("variant")
    && (
      h.has("ac_minus_tk_mph")
      || h.has("rhs_minus_lhs_mph")
      || h.has("lhs_policy")
      || h.has("policy_metrics_json")
    )
  ) return "discrimination";
  if (
    h.has("profile")
    && h.has("mode")
    && h.has("wall_seconds")
    && (h.has("throughput_rel_error_pct") || h.has("extended_score_pct"))
  ) return "calibrationSummary";
  if (h.has("phase") && h.has("tick_seconds") && h.has("rel_error_pct")) return "calibration";
  return "unknown";
}

function mqThemedScale(overrides = {}) {
  const base = {
    ticks: { color: MQSIM_THEME.ticks },
    grid: { color: MQSIM_THEME.grid },
  };
  const ticks = { ...base.ticks, ...(overrides.ticks || {}) };
  const grid = { ...base.grid, ...(overrides.grid || {}) };
  return { ...base, ...overrides, ticks, grid };
}

function mqThemedLegend(overrides = {}) {
  const base = {
    labels: {
      color: MQSIM_THEME.legendText,
      font: { size: MQSIM_LEGEND_FONT_SIZE },
      boxWidth: 10,
    },
  };
  const labels = {
    ...base.labels,
    ...(overrides.labels || {}),
    font: {
      ...base.labels.font,
      ...((overrides.labels || {}).font || {}),
    },
  };
  return { ...base, ...overrides, labels };
}

function mqChartOptions(scales, overrides = {}) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    scales: scales || {},
    ...overrides,
  };
}

function mqChartOptionsSingleY(yOverrides = {}, overrides = {}) {
  return mqChartOptions({
    y: mqThemedScale({ beginAtZero: true, ...yOverrides }),
  }, overrides);
}

function mqDestroyChartIfPresent(chartRef) {
  if (chartRef && typeof chartRef.destroy === "function") chartRef.destroy();
}
