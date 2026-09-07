/*
 * Split from ui/index.html for maintainability.
 * Client-side only: plain browser JavaScript, no build step.
 */

const C0 = { other: "rgba(110,118,129,0.9)", active: "rgba(163,113,247,0.9)", pool: "rgba(46,160,67,0.9)", stale: "rgba(210,153,34,0.9)", pending: "#6e7681", running: "#a371f7", success: "#2ea043", merged: "rgba(31,111,235,0.55)", failed: "#f85149", force_merge: "#f85149" };
const BASE_MS = 500, HIGHLIGHT_TICKS = 4;
const MERGE_LABELS_PRIORITY = [
  "bot/approved: critical",
  "bot/approved: urgent",
  "bot/approved: high",
  "bot/approved: progressive-delivery",
  "bot/approved: medium",
  "bot/approved: low",
  "bot/approved",
  "bot/automerge",
  "auto-merge",
  "lgtm"
];
let playhead = 0, playTimer = null, xRange = null;
let filesData = [], activeIndex = 0;

// Time display mode: "ticks" | "relative" | "absolute"
let timeDisplayMode = "ticks";

function getTimeContext(fileIdx) {
  const d = filesData[fileIdx || activeIndex];
  if (!d) return { tickSeconds: 60, startHour: 0, startWeekday: "", peakHoursUtc: new Set() };
  const meta = (d.events || []).find(e => e.event === "scenario_meta") || {};
  const tickSeconds = Math.max(1, Number(meta.tick_seconds) || 60);
  const scenMeta = meta.scenario_metadata || {};
  const ap = scenMeta.arrival_profile || scenMeta.calibration_targets?.arrival_profile || {};
  const startHour = Number(ap.scenario_start_hour) || 0;
  const startWeekday = ap.scenario_start_weekday || "";
  const peakHoursUtc = new Set();
  (Array.isArray(ap.peak_window_hours_utc) ? ap.peak_window_hours_utc : []).forEach(h => {
    const n = Number(h);
    if (Number.isFinite(n)) peakHoursUtc.add(((n % 24) + 24) % 24);
  });
  return { tickSeconds, startHour, startWeekday, peakHoursUtc };
}

function fmtTickAsTime(tick, ctx) {
  if (timeDisplayMode === "ticks") return String(tick);
  const totalSeconds = tick * ctx.tickSeconds;
  if (timeDisplayMode === "relative") {
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    const s = totalSeconds % 60;
    if (h > 0) return s > 0 ? `${h}h${String(m).padStart(2, "0")}m${String(s).padStart(2, "0")}s` : m > 0 ? `${h}h${String(m).padStart(2, "0")}m` : `${h}h`;
    if (m > 0) return s > 0 ? `${m}m${String(s).padStart(2, "0")}s` : `${m}m`;
    return `${s}s`;
  }
  // absolute: offset from scenario_start_hour
  const baseSeconds = ctx.startHour * 3600;
  const absSeconds = (baseSeconds + totalSeconds) % 86400;
  const hh = Math.floor(absSeconds / 3600);
  const mm = Math.floor((absSeconds % 3600) / 60);
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}

function fmtTickLabel(tick, ctx) {
  if (timeDisplayMode === "ticks") return `Step ${tick}`;
  return fmtTickAsTime(tick, ctx);
}

function fmtTickRange(start, end, ctx) {
  if (timeDisplayMode === "ticks") {
    const dur = end - start;
    return `Ticks ${start}\u2192${end} (${dur} tick${dur > 1 ? "s" : ""})`;
  }
  const durSec = (end - start) * ctx.tickSeconds;
  const durLabel = durSec >= 3600 ? `${(durSec / 3600).toFixed(1)}h` : durSec >= 60 ? `${Math.round(durSec / 60)}m` : `${durSec}s`;
  return `${fmtTickAsTime(start, ctx)}\u2192${fmtTickAsTime(end, ctx)} (${durLabel})`;
}

function timeAxisTitle() {
  if (timeDisplayMode === "ticks") return "Time step";
  if (timeDisplayMode === "relative") return "Elapsed time";
  return "Time (UTC)";
}

// Peak window highlight state
let showPeakHighlight = false;

function getPeakTickRanges(ctx, maxTick) {
  if (!ctx.peakHoursUtc || ctx.peakHoursUtc.size === 0) return [];
  const secPerTick = ctx.tickSeconds;
  const ranges = [];
  let inPeak = false, rangeStart = 0;
  for (let t = 0; t <= maxTick; t++) {
    const totalSec = t * secPerTick;
    const utcHour = Math.floor(((ctx.startHour * 3600 + totalSec) % 86400) / 3600);
    const isPeak = ctx.peakHoursUtc.has(utcHour);
    if (isPeak && !inPeak) { rangeStart = t; inPeak = true; }
    else if (!isPeak && inPeak) { ranges.push([rangeStart, t]); inPeak = false; }
  }
  if (inPeak) ranges.push([rangeStart, maxTick]);
  return ranges;
}

function isTickInPeak(tick, ctx) {
  if (!ctx.peakHoursUtc || ctx.peakHoursUtc.size === 0) return false;
  const totalSec = tick * ctx.tickSeconds;
  const utcHour = Math.floor(((ctx.startHour * 3600 + totalSec) % 86400) / 3600);
  return ctx.peakHoursUtc.has(utcHour);
}

const PEAK_BG_COLOR = "rgba(251,191,36,0.07)";
const PEAK_BORDER_COLOR = "rgba(251,191,36,0.22)";

let expMode = "discrimination";
let expData = {
  discrimination: null,
  discriminationMeta: null,
  calibrationGrid: [],
  calibrationValidation: [],
  calibrationSummary: [],
};
let expDiscDeltaChart = null, expDiscRebaseChart = null, expCalErrorChart = null, expCalScatterChart = null, expCalDimChart = null, expCalBenchRuntimeChart = null, expCalBenchScoreChart = null;
let kanbanVisibleIdxs = new Set();
let barVisibleIdxs = new Set();
let swimIdx = 0;
let loadedRunMetadataByCategory = {};
let loadedScenarioDocs = [];

// Monte Carlo shared state (declared here for cross-file availability).
let mcData = null; // { policies: [...], metrics: [...], trials: { policy: { metric: [values] } } }
let mcBoxChart = null, mcCIChart = null;
let mcVisiblePolicies = new Set();

// Sweep shared state
let sweepLoaded = false;
let sweepData = null;
let sweepDocs = [];   // one per loaded sweep; each varies a different axis
let multiMergeLoaded = false;
let multiMergeFileName = "";
let sweepLineChart = null;
let sweepBarChart = null;
let sweepConvergeChart = null;

// Per-label merge time data
let perLabelData = null;
let perLabelChart = null;
let perLabelP95Chart = null;
let statsSubtab = "metrics"; // "metrics" | "perlabel"
let simSubtab = "scenario"; // "scenario" | "calibration" | "metadata"

function _isPerLabelData(obj) {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return false;
  const keys = Object.keys(obj);
  if (keys.length === 0) return false;
  const first = obj[keys[0]];
  if (!first || typeof first !== "object") return false;
  const subKeys = Object.keys(first);
  if (subKeys.length === 0) return false;
  const sample = first[subKeys[0]];
  return sample && typeof sample === "object" && "count" in sample && "mean_seconds" in sample;
}

// --- Parsing & Analysis ---
function parseNdjson(text) {
  const lines = String(text || "").split("\n");
  const out = [];
  lines.forEach(rawLine => {
    const line = rawLine.trim();
    if (!line) return;
    try {
      const parsed = JSON.parse(line);
      if (parsed && typeof parsed === "object") out.push(parsed);
    } catch {
      // Ignore malformed NDJSON lines, matching historical behavior.
    }
  });
  return out;
}
function parseYamlDoc(text) {
  if (!(window.jsyaml && typeof window.jsyaml.load === "function")) return null;
  try {
    return window.jsyaml.load(text);
  } catch {
    return null;
  }
}
function normalizeNameStem(name) {
  return String(name || "")
    .toLowerCase()
    .replace(/\.(ndjson|jsonl|json|yaml|yml)$/i, "")
    .replace(/-metrics$/i, "")
    .trim();
}
function getPriorityLabel(labels) {
  const arr = Array.isArray(labels) ? labels.map(String) : [];
  return MERGE_LABELS_PRIORITY.find(lbl => arr.includes(lbl)) || "";
}
function buildCatalogEntryFromLabels(labels) {
  const list = Array.isArray(labels) ? labels.map(String).filter(Boolean) : [];
  return {
    labels: list,
    priority_label: getPriorityLabel(list),
    service_labels: list.filter(lbl => lbl.startsWith("tenant-")).sort((a, b) => a.localeCompare(b)),
  };
}
function buildScenarioCatalog(rawScenario) {
  const out = {};
  const mrs = Array.isArray(rawScenario?.merge_requests) ? rawScenario.merge_requests : [];
  mrs.forEach(mr => {
    const iid = Number(mr?.iid);
    if (!Number.isFinite(iid) || iid <= 0) return;
    out[String(iid)] = buildCatalogEntryFromLabels(mr?.labels);
  });
  return out;
}
function scenarioMetaFromYaml(rawScenario, sourceFileName = "") {
  const mrs = Array.isArray(rawScenario?.merge_requests) ? rawScenario.merge_requests : [];
  const arrivals = [];
  const cancellations = [];
  const forceMerges = [];
  const pushes = [];
  mrs.forEach(mr => {
    const iid = Number(mr?.iid);
    if (!Number.isFinite(iid) || iid <= 0) return;
    const arrivalTick = Number(mr?.arrival_tick);
    const cancelTick = Number(mr?.cancel_tick);
    const forceMergeTick = Number(mr?.force_merge_tick);
    const pushTick = Number(mr?.push_tick);
    if (arrivalTick > 0) arrivals.push({ iid, tick: arrivalTick });
    if (cancelTick > 0) cancellations.push({ iid, tick: cancelTick });
    if (forceMergeTick > 0) forceMerges.push({ iid, tick: forceMergeTick });
    if (pushTick > 0) pushes.push({ iid, tick: pushTick });
  });
  return {
    event: "scenario_meta",
    tick: 0,
    total_mrs: mrs.length,
    failure_rate: Number(rawScenario?.pipeline_durations?.failure_rate) || 0,
    tick_seconds: Number(rawScenario?.tick_seconds) || 60,
    operation_failure_rate: {
      merge: Number(rawScenario?.failure_path_realism?.merge_failure_rate) || 0,
      rebase: Number(rawScenario?.failure_path_realism?.rebase_failure_rate) || 0,
    },
    pipeline_duration: {
      min: Number(rawScenario?.pipeline_durations?.min_ticks) || 0,
      max: Number(rawScenario?.pipeline_durations?.max_ticks) || 0,
    },
    force_merges: forceMerges,
    cancellations,
    arrivals,
    pushes,
    scheduled_target_advances: rawScenario?.scheduled_target_advances || {},
    scenario_metadata: rawScenario?.metadata || {},
    mr_catalog: buildScenarioCatalog(rawScenario),
    scenario_source_file: sourceFileName,
  };
}
function mergeCatalogEntries(fallbackEntry, existingEntry) {
  const fb = fallbackEntry && typeof fallbackEntry === "object" ? fallbackEntry : {};
  const ex = existingEntry && typeof existingEntry === "object" ? existingEntry : {};
  const labelsEx = Array.isArray(ex.labels) ? ex.labels : [];
  const labelsFb = Array.isArray(fb.labels) ? fb.labels : [];
  const labels = labelsEx.length ? labelsEx : labelsFb;
  const serviceEx = Array.isArray(ex.service_labels) ? ex.service_labels : [];
  const serviceFb = Array.isArray(fb.service_labels) ? fb.service_labels : [];
  return {
    labels,
    priority_label: ex.priority_label || fb.priority_label || getPriorityLabel(labels) || "",
    service_labels: serviceEx.length ? serviceEx : (serviceFb.length ? serviceFb : labels.filter(lbl => String(lbl).startsWith("tenant-")).sort((a, b) => String(a).localeCompare(String(b)))),
  };
}
function mergeCatalogs(fallbackCatalog, existingCatalog) {
  const fb = fallbackCatalog && typeof fallbackCatalog === "object" ? fallbackCatalog : {};
  const ex = existingCatalog && typeof existingCatalog === "object" ? existingCatalog : {};
  const merged = {};
  const keys = new Set([...Object.keys(fb), ...Object.keys(ex)]);
  keys.forEach(key => {
    merged[key] = mergeCatalogEntries(fb[key], ex[key]);
  });
  return merged;
}
function withScenarioMeta(events, fallbackMeta) {
  const arr = Array.isArray(events) ? events : [];
  if (!fallbackMeta || typeof fallbackMeta !== "object") return arr;
  const idx = arr.findIndex(e => e && e.event === "scenario_meta");
  if (idx < 0) return [fallbackMeta, ...arr];
  const current = arr[idx] || {};
  const next = { ...fallbackMeta, ...current };
  next.arrivals = (Array.isArray(current.arrivals) && current.arrivals.length) ? current.arrivals : fallbackMeta.arrivals;
  next.cancellations = (Array.isArray(current.cancellations) && current.cancellations.length) ? current.cancellations : fallbackMeta.cancellations;
  next.force_merges = (Array.isArray(current.force_merges) && current.force_merges.length) ? current.force_merges : fallbackMeta.force_merges;
  next.pushes = (Array.isArray(current.pushes) && current.pushes.length) ? current.pushes : fallbackMeta.pushes;
  next.pipeline_duration = (current.pipeline_duration && typeof current.pipeline_duration === "object" && Object.keys(current.pipeline_duration).length)
    ? current.pipeline_duration
    : fallbackMeta.pipeline_duration;
  next.operation_failure_rate = (current.operation_failure_rate && typeof current.operation_failure_rate === "object" && Object.keys(current.operation_failure_rate).length)
    ? current.operation_failure_rate
    : fallbackMeta.operation_failure_rate;
  next.scheduled_target_advances = (current.scheduled_target_advances && typeof current.scheduled_target_advances === "object" && Object.keys(current.scheduled_target_advances).length)
    ? current.scheduled_target_advances
    : fallbackMeta.scheduled_target_advances;
  next.scenario_metadata = (current.scenario_metadata && typeof current.scenario_metadata === "object" && Object.keys(current.scenario_metadata).length)
    ? current.scenario_metadata
    : fallbackMeta.scenario_metadata;
  next.mr_catalog = mergeCatalogs(fallbackMeta.mr_catalog, current.mr_catalog);
  if (current.total_mrs == null) next.total_mrs = fallbackMeta.total_mrs;
  if (current.tick_seconds == null) next.tick_seconds = fallbackMeta.tick_seconds;
  if (current.failure_rate == null) next.failure_rate = fallbackMeta.failure_rate;
  const before = JSON.stringify(current);
  const after = JSON.stringify(next);
  if (before === after) return arr;
  const out = arr.slice();
  out[idx] = next;
  return out;
}
function resolveScenarioDocForTrace(events, traceName = "") {
  if (!loadedScenarioDocs.length) return null;
  if (loadedScenarioDocs.length === 1) return loadedScenarioDocs[0];
  const stem = normalizeNameStem(traceName);
  const meta = (Array.isArray(events) ? events : []).find(e => e && e.event === "scenario_meta") || {};
  const scenarioName = String(meta?.scenario_metadata?.name || "").trim().toLowerCase();
  if (scenarioName) {
    const byName = loadedScenarioDocs.find(s => String(s?.doc?.metadata?.name || "").trim().toLowerCase() === scenarioName);
    if (byName) return byName;
  }
  const ctxScenario = String(loadedRunMetadataByCategory?.comparisons?.context?.scenario || "").toLowerCase();
  if (ctxScenario) {
    const byCtx = loadedScenarioDocs.find(s => ctxScenario.includes(normalizeNameStem(s.fileName)));
    if (byCtx) return byCtx;
  }
  if (stem) {
    const byStem = loadedScenarioDocs.find(s => normalizeNameStem(s.fileName).includes(stem) || stem.includes(normalizeNameStem(s.fileName)));
    if (byStem) return byStem;
  }
  return loadedScenarioDocs[0];
}
function applyLoadedScenariosToFilesData() {
  if (!filesData.length || !loadedScenarioDocs.length) return false;
  let changed = false;
  filesData = filesData.map(d => {
    const docEntry = resolveScenarioDocForTrace(d.events, d.name);
    if (!docEntry) return d;
    const fallbackMeta = scenarioMetaFromYaml(docEntry.doc, docEntry.fileName);
    const mergedEvents = withScenarioMeta(d.events || [], fallbackMeta);
    if (mergedEvents === d.events) return d;
    changed = true;
    return { ...d, events: mergedEvents };
  });
  if (changed) {
    filesData.forEach(d => {
      const M = computeFileMetrics(d.events);
      d.metrics = M;
      d.packed = M.packed;
    });
  }
  return changed;
}

function isShiftPressed(evt) {
  return !!(evt?.native?.shiftKey || evt?.shiftKey || window.event?.shiftKey);
}

function isTypingTarget(target) {
  const el = target instanceof HTMLElement ? target : null;
  if (!el) return false;
  if (el.isContentEditable) return true;
  const field = el.closest("input, textarea, select");
  if (!field) return false;
  return !field.classList.contains("lock-scrub");
}

function handleLegendToggle(evt, item, legend) {
  const chart = legend?.chart;
  const idx = item?.datasetIndex;
  if (!chart || idx == null) return;
  if (isShiftPressed(evt)) {
    const hasOtherVisible = chart.data.datasets.some((_, i) => (
      i !== idx && chart.isDatasetVisible(i)
    ));
    chart.data.datasets.forEach((_, i) => {
      chart.setDatasetVisibility(i, hasOtherVisible ? i === idx : true);
    });
  } else {
    chart.setDatasetVisibility(idx, !chart.isDatasetVisible(idx));
  }
  chart.update();
}
function normalizeEventsForAnalysis(arr) {
  const base = (Array.isArray(arr) ? arr : []).filter(e => e && e.event !== "api_call");
  const extra = [];
  base.forEach(e => { if (e.event === "tick" && Array.isArray(e.force_merges)) e.force_merges.forEach(fm => extra.push(fm)); });
  return base.concat(extra);
}
function percentile(values, percentileValue) {
  if (!values.length) return 0;
  const sorted = [...values].sort((u, v) => u - v);
  const idx = Math.min(sorted.length - 1, Math.floor((sorted.length * percentileValue) / 100));
  return sorted[idx];
}

function buildSwimlaneSegs(evs, maxT) {
  const mrId = v => String(v);
  const segs = [], pipe = new Map();
  const horizon = maxT + 1; // exclusive upper bound so last frame remains representable
  evs.forEach(e => { if (e.event === "rebase" && e.pipeline_id != null) pipe.set(e.pipeline_id, { mr: mrId(e.mr_iid), rebaseTick: e.tick, outcome: e.pipeline_outcome || null }); });
  evs.forEach(e => {
    if (e.event !== "tick" || !e.transitions) return;
    e.transitions.forEach(tr => {
      const m = (tr.from || "").toLowerCase(), n = (tr.to || "").toLowerCase();
      if (tr.mr_iid == null) return;
      const mid = mrId(tr.mr_iid);
      const pid = tr.pipeline_id;
      let p = pid == null ? null : pipe.get(pid);
      // Some pipelines exist at scenario start and transition without a matching
      // rebase event in the stream. Synthesize minimal tracking so they can render.
      if (!p && pid != null) {
        p = {
          mr: mid,
          rebaseTick: Math.max(0, e.tick - 1),
          outcome: null,
        };
        pipe.set(pid, p);
      }
      if (p && p.mr !== mid) p.mr = mid;
      if (m === "pending" && n === "running" && p) {
        const start = Math.max(0, p.rebaseTick ?? (e.tick - 1));
        if (e.tick > start) segs.push({ mr: mid, start, end: e.tick, kind: "pending" });
        p.runAt = e.tick;
      } else if (m === "running" && n === "success" && p) {
        const runAt = Math.max(0, p.runAt ?? (e.tick - 1));
        if (e.tick > runAt) segs.push({ mr: mid, start: runAt, end: e.tick, kind: "running" });
        p.succAt = e.tick;
      } else if (m === "running" && n === "failed" && p) {
        const runAt = Math.max(0, p.runAt ?? (e.tick - 1));
        if (e.tick > runAt) segs.push({ mr: mid, start: runAt, end: e.tick, kind: "failed" });
        p.failedAt = e.tick;
      }
    });
  });
  const allMergeEvents = evs
    .filter(e => e.event === "merge" && e.mr_iid != null)
    .map(e => ({ tick: e.tick, mr: mrId(e.mr_iid) }))
    .sort((a, b) => a.tick - b.tick);
  pipe.forEach((p, pid) => {
    if (p.succAt == null) return;
    let nextMerge = null, nextRebase = null;
    evs.forEach(e => {
      if (mrId(e.mr_iid) !== p.mr || e.tick < p.succAt) return;
      if (e.event === "merge" && e.tick >= p.succAt && (!nextMerge || e.tick < nextMerge)) nextMerge = e.tick;
      if (e.event === "rebase" && e.tick > p.succAt && (!nextRebase || e.tick < nextRebase)) nextRebase = e.tick;
    });
    const t1 = Math.min(nextMerge ?? horizon, nextRebase ?? horizon, horizon);
    if (t1 <= p.succAt) return;
    // A success becomes stale as soon as *another* MR merges, even if this MR
    // itself eventually merges later (e.g., force-merge). Keep that stale split.
    const firstOtherMerge = allMergeEvents.find(
      me => me.tick > p.succAt && me.mr !== p.mr
    )?.tick ?? null;
    if (firstOtherMerge != null && firstOtherMerge < t1) {
      segs.push({ mr: p.mr, start: p.succAt, end: firstOtherMerge, kind: "success" });
      segs.push({ mr: p.mr, start: firstOtherMerge, end: t1, kind: "stale" });
    } else if (nextMerge != null && (nextRebase == null || nextMerge <= nextRebase)) {
      segs.push({ mr: p.mr, start: p.succAt, end: t1, kind: "success" });
    } else if (nextRebase != null) {
      segs.push({ mr: p.mr, start: p.succAt, end: t1, kind: "stale" });
    } else {
      segs.push({ mr: p.mr, start: p.succAt, end: t1, kind: "success" });
    }
  });
  const mergeTick = new Map(), forceMergeTicks = [];
  evs.forEach(e => {
    if (e.event === "merge" && e.mr_iid != null) {
      const mid = mrId(e.mr_iid);
      const prev = mergeTick.get(mid);
      if (prev == null || e.tick < prev) mergeTick.set(mid, e.tick);
      const kind = e.force_merge ? "force_merge" : "merged";
      segs.push({ mr: mid, start: e.tick, end: e.tick + Math.max(1, maxT * 0.006), kind });
      if (e.force_merge) forceMergeTicks.push(e.tick);
    }
  });

  const closeTick = new Map();
  evs.forEach(e => {
    if (e.event !== "tick" || !Array.isArray(e.cancellations) || e.tick == null) return;
    e.cancellations.forEach(mrid => {
      const mid = mrId(mrid);
      const prev = closeTick.get(mid);
      if (prev == null || e.tick < prev) closeTick.set(mid, e.tick);
    });
  });

  segs.forEach(s => {
    if (s.kind === "merged" || s.kind === "force_merge") return;
    const mid = mrId(s.mr);
    const mt = mergeTick.get(mid);
    const ct = closeTick.get(mid);
    const cut = Math.min(mt ?? Infinity, ct ?? Infinity);
    if (cut !== Infinity && s.end > cut) s.end = cut;
  });
  segs.forEach(s => { s.end = Math.max(s.end, s.start); });
  const result = segs.filter(s => s.end > s.start).sort((a, b) => {
    const an = Number(a.mr), bn = Number(b.mr);
    if (Number.isFinite(an) && Number.isFinite(bn) && an !== bn) return an - bn;
    if (String(a.mr) !== String(b.mr)) return String(a.mr).localeCompare(String(b.mr));
    return a.start - b.start;
  });
  result._forceMergeTicks = forceMergeTicks;
  return result;
}

function buildPackedSeriesFromEvents(raw) {
  const evs = normalizeEventsForAnalysis([...raw]);
  let maxTick = 0;
  evs.forEach(e => { if (e.tick != null) maxTick = Math.max(maxTick, e.tick); });
  const segs = buildSwimlaneSegs(evs, maxTick);
  const byT = new Map();
  evs.forEach(e => { if (e.event === "tick" && e.tick != null) byT.set(e.tick, { ...e }); else if (e.event === "snapshot" && e.tick != null && !byT.has(e.tick)) byT.set(e.tick, { ...e }); });
  evs.forEach(e => { if (e.event === "snapshot" && e.tick != null) { const r = byT.get(e.tick); if (r) { r.open_mrs = r.open_mrs ?? e.open_mrs; } } });
  const series = { labels: [], pool: [], active: [], stale: [], rest: [], open: [] };
  for (let t = 0; t <= maxTick; t++) {
    const w = byT.get(t) || {}, o = w.open_mrs ?? 0;
    const ciMrs = new Set(), readyMrs = new Set(), staleMrs = new Set();
    for (const s of segs) {
      if (s.start > t || s.end <= t) continue;
      if (s.kind === "running") ciMrs.add(s.mr);
      else if (s.kind === "success") readyMrs.add(s.mr);
      else if (s.kind === "stale") staleMrs.add(s.mr);
    }
    readyMrs.forEach(mr => { ciMrs.delete(mr); staleMrs.delete(mr); });
    ciMrs.forEach(mr => staleMrs.delete(mr));
    const ci = ciMrs.size, ready = readyMrs.size, stale = staleMrs.size;
    series.labels.push(t); series.pool.push(ready); series.active.push(ci); series.stale.push(stale); series.rest.push(Math.max(0, o - ready - ci - stale)); series.open.push(o);
  }
  return { maxTick, merges: evs.filter(e => e.event === "merge").map(e => ({ tick: e.tick, mr: e.mr_iid })), series, segs };
}

function rebaseSetAtTick(evs, t) { const s = new Set(); evs.forEach(e => { if (e.event === "rebase" && e.tick === t && e.mr_iid != null) s.add(String(e.mr_iid)); }); return s; }
function mergeTickByMr(evs) { const m = new Map(); evs.forEach(e => { if (e.event === "merge" && e.mr_iid != null) { const k = String(e.mr_iid); const x = m.get(k); if (x == null || e.tick < x) m.set(k, e.tick); } }); return m; }
function arrivalTickByMr(evs) {
  const m = new Map();
  const meta = evs.find(e => e.event === "scenario_meta") || {};
  const arr = Array.isArray(meta.arrivals) ? meta.arrivals : [];
  arr.forEach(a => {
    if (!a || a.iid == null) return;
    const k = String(a.iid);
    const t = Number(a.tick) || 0;
    const prev = m.get(k);
    if (prev == null || t < prev) m.set(k, t);
  });
  evs.forEach(e => {
    if (e.event !== "tick" || !Array.isArray(e.arrivals) || e.tick == null) return;
    e.arrivals.forEach(iid => {
      if (iid == null) return;
      const k = String(iid);
      const prev = m.get(k);
      if (prev == null || e.tick < prev) m.set(k, e.tick);
    });
  });
  return m;
}
function cancellationTickByMr(evs) {
  const m = new Map();
  evs.forEach(e => {
    if (e.event !== "tick" || !Array.isArray(e.cancellations) || e.tick == null) return;
    e.cancellations.forEach(iid => {
      if (iid == null) return;
      const k = String(iid);
      const prev = m.get(k);
      if (prev == null || e.tick < prev) m.set(k, e.tick);
    });
  });
  return m;
}
function collectAllIids(evs) {
  const s = new Set();
  const meta = evs.find(e => e.event === "scenario_meta") || {};
  if (meta.mr_catalog && typeof meta.mr_catalog === "object") {
    Object.keys(meta.mr_catalog).forEach(k => s.add(String(k)));
  }
  const arr = Array.isArray(meta.arrivals) ? meta.arrivals : [];
  arr.forEach(a => { if (a && a.iid != null) s.add(String(a.iid)); });
  evs.forEach(e => {
    if (e.mr_iid != null) s.add(String(e.mr_iid));
    (e.transitions || []).forEach(tr => { if (tr.mr_iid != null) s.add(String(tr.mr_iid)); });
  });
  if (!s.size && Number.isFinite(Number(meta.total_mrs)) && Number(meta.total_mrs) > 0) {
    const n = Math.floor(Number(meta.total_mrs));
    for (let i = 1; i <= n; i++) s.add(String(i));
  }
  return s;
}
function classifyMrAtTick(segs, t, m, mergeM, rebaseS) {
  const mid = String(m);
  if (t >= (mergeM.get(mid) ?? 1e9)) return "merged";
  if (rebaseS.has(mid)) return "rebase";
  let hasReady = false, hasStale = false, hasCi = false;
  for (const s of segs) {
    if (String(s.mr) !== mid) continue;
    if (s.start > t || s.end <= t) continue;
    if (s.kind === "success") hasReady = true;
    else if (s.kind === "stale") hasStale = true;
    else if (s.kind === "pending" || s.kind === "running") hasCi = true;
  }
  if (hasCi) return "ci";
  if (hasReady) return "ready";
  if (hasStale) return "stale";
  return "wait";
}
function buildKanbanState(packed, evs, t) {
  const f = normalizeEventsForAnalysis(evs);
  const mergeM = mergeTickByMr(f);
  const rebaseS = rebaseSetAtTick(f, t);
  const arrivalM = arrivalTickByMr(f);
  const cancelM = cancellationTickByMr(f);
  const iids = Array.from(collectAllIids(f));
  const w = { queue: [], wait: [], rebase: [], ci: [], ready: [], stale: [], merged: [] };
  for (const m of iids) {
    const mid = String(m);
    const mergedAt = mergeM.get(mid);
    const canceledAt = cancelM.get(mid);
    const arrivalAt = arrivalM.get(mid) ?? 0;
    const isMerged = mergedAt != null && mergedAt <= t;
    if (isMerged) {
      w.merged.push(mid);
      continue;
    }
    if (canceledAt != null && canceledAt <= t) continue;
    if (arrivalAt > t) continue;
    w.queue.push(mid);
    const c = classifyMrAtTick(packed.segs, t, mid, mergeM, rebaseS);
    w[c === "rebase" ? "rebase" : c].push(mid);
  }
  Object.values(w).forEach(a => a.sort((a, b) => Number(a) - Number(b)));
  return w;
}
function buildNarrativeAtTick(evs, t) {
  const a = [];
  evs.forEach(e => {
    if (e.tick === t) {
      if (e.event === "rebase") a.push("Rebased !" + e.mr_iid);
      if (e.event === "merge" && e.force_merge) a.push("FORCE-MERGED !" + e.mr_iid + " (bypassed queue)");
      else if (e.event === "merge") a.push("Merged !" + e.mr_iid);
    }
  });
  evs.forEach(e => {
    if (e.event === "tick" && e.tick === t) (e.transitions || []).forEach(tr => {
      const to = (tr.to || "").toLowerCase();
      if (to === "failed") a.push("!" + tr.mr_iid + ": CI failed");
      else a.push("!" + tr.mr_iid + ": " + tr.from + " → " + tr.to);
    });
  });
  return a.length ? a : ["Clock advanced."];
}

/**
 * The run's own summary, keyed the way this UI's renderers expect.
 *
 * src/mqsim/metrics.json carries each metric's short UI alias, so the mapping
 * between the two naming schemes is stated once rather than implied by two
 * codebases agreeing.
 */
function mqsimSummaryForUi(events) {
  const summaryEvent = (events || []).slice().reverse()
    .find(e => e && e.event === "run_summary");
  const summary = summaryEvent && summaryEvent.metrics;
  if (!summary || typeof summary !== "object") return {};
  const out = { ...summary };
  Object.entries(MQSIM_METRIC_SPEC).forEach(([canon, entry]) => {
    if (!(canon in summary)) return;
    (entry.ui_aliases || []).forEach(alias => { out[alias] = summary[canon]; });
  });
  return out;
}

function computeFileMetrics(evs) {
  const f = normalizeEventsForAnalysis(evs);
  const packed = buildPackedSeriesFromEvents(f);
  const rebaseE = f.filter(e => e.event === "rebase"), mergeE = f.filter(e => e.event === "merge"), tickE = f.filter(e => e.event === "tick");
  const rebaseErrE = f.filter(e => e.event === "rebase_error");
  const mergeErrE = f.filter(e => e.event === "merge_error");
  const pipelineCancelE = f.filter(e => e.event === "pipeline_cancel");
  const meta = f.find(e => e.event === "scenario_meta") || {};
  const tickSeconds = Math.max(1, Number(meta.tick_seconds) || 60);
  const srpL = tickE.map(e => e.same_root_success_pool || 0), stL = tickE.map(e => e.stale_successes || 0);
  const peak = tickE.reduce((M, e) => Math.max(M, e.active_pipelines || 0), 0);
  const mrc = {}; rebaseE.forEach(e => { mrc[e.mr_iid] = (mrc[e.mr_iid] || 0) + 1; });
  const dup = Object.values(mrc).filter(n => n > 1).reduce((a, n) => a + n - 1, 0);
  const totalT = packed.maxTick;
  const totalHours = (totalT * tickSeconds) / 3600;
  const mTicks = mergeE.map(e => e.tick).sort((a, b) => a - b);
  const mergeRounds = new Set(mergeE.map(e => e.tick)).size;
  const o0 = packed.series.open.find(v => v > 0) ?? 0;
  const ciTimes = packed.segs.filter(s => s.kind === "running").map(s => s.end - s.start);
  const ciMin = ciTimes.length ? Math.min(...ciTimes) : 0;
  const ciMax = ciTimes.length ? Math.max(...ciTimes) : 0;
  const ciAvg = ciTimes.length ? ciTimes.reduce((a, b) => a + b, 0) / ciTimes.length : 0;
  const forceMergeCount = mergeE.filter(e => e.force_merge).length;
  let ciFailures = 0;
  tickE.forEach(e => { (e.transitions || []).forEach(tr => { if ((tr.to || "").toLowerCase() === "failed") ciFailures++; }); });
  const embeddedScenarioMetadata = meta.scenario_metadata && typeof meta.scenario_metadata === "object"
    ? meta.scenario_metadata
    : {};
  const calTargets = embeddedScenarioMetadata.calibration_targets && typeof embeddedScenarioMetadata.calibration_targets === "object"
    ? embeddedScenarioMetadata.calibration_targets
    : {};
  const arrivalProfile = calTargets.arrival_profile && typeof calTargets.arrival_profile === "object"
    ? calTargets.arrival_profile
    : {};
  const timelineTicks = Number(meta.total_time_ticks)
    || Number(embeddedScenarioMetadata.window_ticks)
    || totalT;
  const timeline = computeHourlyTimeline({
    events: f,
    scenarioMeta: meta,
    arrivalProfile,
    tickSeconds,
    totalTicks: timelineTicks,
  });
  // Denominator is the whole scenario population — starting queue plus every
  // scheduled arrival — so drain cannot exceed 100% once MRs arrive after t=0.
  const queueDrainBase = Number(meta?.total_mrs) || o0;
  const peakOffRatio = timeline.throughput_offpeak_window_mph > 0
    ? timeline.throughput_peak_window_mph / timeline.throughput_offpeak_window_mph
    : 0;
  return {
    packed,
    // Segment-derived, so the UI is the only implementation and there is
    // nothing to drift against.
    ci_min: ciMin, ci_max: ciMax, ci_avg: ciAvg,
    // Everything else comes from the run's own run_summary event, written by
    // mqsim/summary.py. The UI used to recompute all of it from the same
    // events, which is how queue_drain ended up with two denominators.
    ...mqsimSummaryForUi(f),
};
}

function globalMaxTick() { let mx = 0; filesData.forEach(d => { if (d.packed) mx = Math.max(mx, d.packed.maxTick); }); return mx; }

// --- Tab Navigation ---
function updatePlaybackVisibility() {
  const active = document.querySelector(".tab-panel.active");
  const show = !!(active && active.id === "panelKanban" && filesData.length);
  if (!show && playTimer) stop();
  const bar = document.getElementById("playbackBar");
  if (bar) bar.classList.toggle("visible", show);
}

function swimIdsAllowedForTab(name) {
  if (name === "composition") return new Set(["swimAreaL", "swimAreaR"]);
  if (name === "swimlane") return new Set(["swimAreaSingle"]);
  return new Set();
}

function updateSwimTooltipVisibilityForTab(name) {
  const active = getActiveSwimLock();
  const tip = getGlobalSwimTipEl();
  if (!active) {
    tip.style("display", "none").style("pointer-events", "none").classed("locked", false);
    return;
  }
  const allowed = swimIdsAllowedForTab(name);
  if (!allowed.has(active.swimId)) {
    tip.style("display", "none").style("pointer-events", "none").classed("locked", false);
    return;
  }
  renderLockedSwimTip(active.swimId);
}

function getActiveSwimLockForTab(name) {
  const allowed = swimIdsAllowedForTab(name);
  if (!allowed.size) return null;
  const tipSwimId = getGlobalSwimTipEl().attr("data-swim-id") || null;
  if (tipSwimId && allowed.has(tipSwimId)) {
    const exact = getActiveSwimLock(tipSwimId);
    if (exact) return exact;
  }
  for (const swimId of allowed) {
    const lock = getActiveSwimLock(swimId);
    if (lock) return lock;
  }
  return null;
}

function getActiveTabName() {
  return document.querySelector(".nav-tab.active")?.dataset.tab || "load";
}

function renderTabContent(name) {
  if (name === "kanban") renderKanbanTab();
  if (name === "composition") { renderChartPolicyTabs(); refreshCharts(); renderWindowBar("windowBarComposition"); }
  if (name === "bars") { renderBarsTab(); renderWindowBar("windowBarBars"); }
  if (name === "swimlane") { renderSwimlaneTab(); renderWindowBar("windowBarSwimlane"); }
  if (name === "experiments") renderExperiments();
  if (name === "stats") renderStats();
  if (name === "simulation") renderSimulationTab();
  if (name === "monteCarlo") renderMonteCarlo();
  if (name === "sweep") renderSweepTab();
}

function switchTab(name) {
  document.querySelectorAll(".nav-tab").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.toggle("active", p.id === "panel" + name.charAt(0).toUpperCase() + name.slice(1)));
  renderTabContent(name);
  updatePlaybackVisibility();
  updateSwimTooltipVisibilityForTab(name);
}
document.getElementById("navTabs").addEventListener("click", e => { if (e.target.dataset.tab && !e.target.classList.contains("disabled")) switchTab(e.target.dataset.tab); });

function hasLoadedData() {
  return !!(
    filesData.length
    || loadedScenarioDocs.length
    || mcData
    || sweepLoaded
    || perLabelData
    || expData.discrimination
    || expData.calibrationGrid.length
    || expData.calibrationValidation.length
    || expData.calibrationSummary.length
  );
}

function updateRefreshUiButtonState() {
  const btn = document.getElementById("btnRedrawUi");
  if (!btn) return;
  btn.disabled = !hasLoadedData();
}

function refreshUiWithoutReloadingFiles() {
  if (!hasLoadedData()) return;
  renderFileList();
  const activeTab = getActiveTabName();
  renderTabContent(activeTab);
  updatePlaybackVisibility();
  updateSwimTooltipVisibilityForTab(activeTab);
}

document.getElementById("btnRedrawUi").addEventListener("click", refreshUiWithoutReloadingFiles);

// --- Load Tab ---
const dropZone = document.getElementById("dropZone"), fileInput = document.getElementById("fileInput");
const runDirInput = document.getElementById("runDirInput");
const btnBrowseFiles = document.getElementById("btnBrowseFiles");
const btnBrowseRunFolder = document.getElementById("btnBrowseRunFolder");
const btnUnload = document.getElementById("btnUnload");
const btnLoadSelectedRun = document.getElementById("btnLoadSelectedRun");
const btnRefreshRuns = document.getElementById("btnRefreshRuns");
const runSearchInput = document.getElementById("runSearchInput");
const runSearchSuggestions = document.getElementById("runSearchSuggestions");
const loadedFileCount = document.getElementById("loadedFileCount");
const runCategorySelect = document.getElementById("runCategorySelect");
const runMetaKeySelect = document.getElementById("runMetaKeySelect");
const runMetaValueSelect = document.getElementById("runMetaValueSelect");
const runNameFilterSelect = document.getElementById("runNameFilterSelect");
const runChipList = document.getElementById("runChipList");
const runMetadataView = document.getElementById("runMetadataView");
const runOutputFiles = document.getElementById("runOutputFiles");
const runBrowserStatus = document.getElementById("runBrowserStatus");
let runCatalog = [];
let lastRunDirFiles = null;
let runSearchActiveSuggestion = -1;
const selectedRunKeys = new Set();
let activeRunKey = null;
let runSortBy = "timestamp";
let runSortDir = "desc";
const RUN_SORTABLE_COLUMNS = new Set(["category", "timestamp", "name"]);
const DATA_TABS = ["stats", "simulation", "kanban", "composition", "bars", "swimlane", "experiments", "monteCarlo", "sweep", "multiMerge"];

function readFileText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = reject;
    reader.readAsText(file);
  });
}

function flattenMetadata(obj, prefix = "", out = {}) {
  if (!obj || typeof obj !== "object") return out;
  Object.entries(obj).forEach(([k, v]) => {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v == null) return;
    if (typeof v === "object" && !Array.isArray(v)) flattenMetadata(v, key, out);
    else out[key] = String(v);
  });
  return out;
}

const RUN_CATEGORIES = new Set(["comparisons", "discrimination", "monte-carlo", "calibration", "sweep", "multi-merge"]);

function inferRunCategory(normalizedPath) {
  const lower = String(normalizedPath || "").toLowerCase();
  if (/(?:^|\/)discrimination-summary\.csv$/i.test(lower)) return "discrimination";
  if (/(?:^|\/)monte-carlo-summary\.csv$/i.test(lower)) return "monte-carlo";
  if (/(?:^|\/)plan-raw.*\.json$/i.test(lower)) return "multi-merge";
  if (
    /(?:^|\/)calibration-(?:grid|validation)\.csv$/i.test(lower)
    || /(?:^|\/)calibration-.*\.csv$/i.test(lower)
    || /(?:^|\/)selected-scenario\.ya?ml$/i.test(lower)
    || /(?:^|\/)scenario.*\.ya?ml$/i.test(lower)
    || /(?:^|\/)adaptive-vs-fixed-summary\.csv$/i.test(lower)
    || /(?:^|\/)metadata\.json$/i.test(lower)
  ) return "calibration";
  if (/\.(ndjson|jsonl|json)$/i.test(lower)) return "comparisons";
  return null;
}

function parseRunFileRelPath(relPath) {
  const normalized = (relPath || "").replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  if (!parts.length) return null;

  // Preferred format: reports/<category>/<run>/...
  const categoryIdx = parts.findIndex(p => RUN_CATEGORIES.has(p));
  if (categoryIdx >= 0 && parts.length >= categoryIdx + 3) {
    return {
      category: parts[categoryIdx],
      run: parts[categoryIdx + 1],
      subpath: parts.slice(categoryIdx + 2).join("/"),
    };
  }

  // Fallback when user selects a category/run folder directly.
  if (parts.length >= 2) {
    const inferred = inferRunCategory(normalized);
    if (!inferred) return null;
    return {
      category: inferred,
      run: parts[0],
      subpath: parts.slice(1).join("/"),
    };
  }
  return null;
}

function createRunCatalogEntry({ category, run, subRun = "", files = [] }) {
  const runName = subRun ? `${run}/${subRun}` : run;
  return {
    key: `${category}::${runName}`,
    category,
    run: runName,
    files,
    metadata: {},
    metadataFlat: {},
    metadataSubpath: subRun ? `${subRun}/metadata.json` : "metadata.json",
  };
}

function expandCalibrationLeafRuns(rawEntry) {
  const nestedRoots = new Set();
  rawEntry.files.forEach(({ subpath }) => {
    const m = String(subpath || "").match(/^(.*)\/metadata\.json$/i);
    if (!m) return;
    const root = m[1];
    if (!root || /^scenarios(?:\/|$)/i.test(root)) return;
    nestedRoots.add(root);
  });
  const nested = [...nestedRoots].sort((a, b) => a.localeCompare(b));
  if (!nested.length) {
    return [createRunCatalogEntry({
      category: rawEntry.category,
      run: rawEntry.run,
      files: rawEntry.files,
    })];
  }
  const expanded = nested.map(root => {
    const prefix = `${root}/`;
    const files = rawEntry.files.filter(f => f.subpath.startsWith(prefix));
    return createRunCatalogEntry({
      category: rawEntry.category,
      run: rawEntry.run,
      subRun: root,
      files,
    });
  });
  const rootFiles = rawEntry.files.filter(f => !nested.some(root => f.subpath.startsWith(`${root}/`)));
  const hasRootSummaryArtifacts = rootFiles.some(f => (
    /adaptive-vs-fixed-summary\.csv$/i.test(f.subpath)
    || /summary\.csv$/i.test(f.subpath)
    || /metadata\.json$/i.test(f.subpath)
  ));
  if (hasRootSummaryArtifacts) {
    expanded.unshift(createRunCatalogEntry({
      category: rawEntry.category,
      run: `${rawEntry.run}/benchmark`,
      files: rootFiles,
    }));
  }
  return expanded;
}

async function indexRunDirectory(filesLike) {
  const files = Array.from(filesLike || []);
  if (files.length) lastRunDirFiles = filesLike;
  const groupedRuns = new Map();
  files.forEach(file => {
    const rel = file.webkitRelativePath || file.name;
    const parsed = parseRunFileRelPath(rel);
    if (!parsed) return;
    const key = `${parsed.category}::${parsed.run}`;
    if (!groupedRuns.has(key)) {
      groupedRuns.set(key, {
        category: parsed.category,
        run: parsed.run,
        files: []
      });
    }
    groupedRuns.get(key).files.push({ file, subpath: parsed.subpath, relPath: rel });
  });

  const expandedRuns = [];
  groupedRuns.forEach(rawEntry => {
    if (rawEntry.category === "calibration") {
      expandedRuns.push(...expandCalibrationLeafRuns(rawEntry));
      return;
    }
    expandedRuns.push(createRunCatalogEntry({
      category: rawEntry.category,
      run: rawEntry.run,
      files: rawEntry.files,
    }));
  });

  await Promise.all(expandedRuns.map(async entry => {
    const metaFile = entry.files.find(x => x.subpath === entry.metadataSubpath);
    if (!metaFile) return;
    try {
      const text = await readFileText(metaFile.file);
      entry.metadata = JSON.parse(text);
      entry.metadataFlat = flattenMetadata(entry.metadata);
    } catch {
      entry.metadata = { parse_error: "Unable to parse metadata.json" };
      entry.metadataFlat = flattenMetadata(entry.metadata);
    }
  }));

  runCatalog = expandedRuns.sort((a, b) => {
    if (a.category !== b.category) return a.category.localeCompare(b.category);
    return b.run.localeCompare(a.run);
  });
  const validKeys = new Set(runCatalog.map(r => r.key));
  Array.from(selectedRunKeys).forEach(key => { if (!validKeys.has(key)) selectedRunKeys.delete(key); });
  if (activeRunKey && !validKeys.has(activeRunKey)) activeRunKey = null;
  renderRunBrowserControls();
}

function parseSearchTerms(query) {
  const terms = [];
  const parts = query.split(/\s+/).filter(Boolean);
  for (const part of parts) {
    const eqIdx = part.indexOf("=");
    if (eqIdx > 0) {
      terms.push({ key: part.slice(0, eqIdx).toLowerCase(), value: part.slice(eqIdx + 1).toLowerCase() });
    } else {
      terms.push({ key: null, value: part.toLowerCase() });
    }
  }
  return terms;
}

function matchRunToTerms(run, terms) {
  if (!terms.length) return true;
  const blob = [run.category, run.run, ...Object.entries(run.metadataFlat).map(([k, v]) => `${k}=${v}`)].join(" ").toLowerCase();
  return terms.every(term => {
    if (term.key === "category") return run.category.toLowerCase().includes(term.value);
    if (term.key) {
      return Object.entries(run.metadataFlat).some(([k, v]) =>
        k.toLowerCase().includes(term.key) && String(v).toLowerCase().includes(term.value)
      );
    }
    return blob.includes(term.value);
  });
}

function getFilteredRuns() {
  const query = runSearchInput ? runSearchInput.value.trim() : "";
  const terms = parseSearchTerms(query);
  const filtered = runCatalog.filter(run => matchRunToTerms(run, terms));
  return filtered.sort(compareRunsBySort);
}

function compareMaybeNumericText(a, b) {
  return String(a || "").localeCompare(String(b || ""), undefined, { numeric: true, sensitivity: "base" });
}

function compareRunTimestamps(a, b) {
  const tsA = getRunTimestampMs(a);
  const tsB = getRunTimestampMs(b);
  if (tsA === tsB) return 0;
  return tsA < tsB ? -1 : 1;
}

function compareRunsBySort(a, b) {
  const direction = runSortDir === "asc" ? 1 : -1;
  let primary = 0;
  if (runSortBy === "category") primary = compareMaybeNumericText(a.category, b.category);
  else if (runSortBy === "name") primary = compareMaybeNumericText(a.run, b.run);
  else primary = compareRunTimestamps(a, b);
  if (primary !== 0) return primary * direction;

  const tsFallback = compareRunTimestamps(b, a); // Keep fallback recency first.
  if (tsFallback !== 0) return tsFallback;
  const categoryFallback = compareMaybeNumericText(a.category, b.category);
  if (categoryFallback !== 0) return categoryFallback;
  return compareMaybeNumericText(a.run, b.run);
}

function getRunTimestampMs(run) {
  const candidates = [
    run?.metadata?.generated_at_iso,
    run?.metadata?.generated_at,
    run?.metadataFlat?.generated_at_iso,
    run?.metadataFlat?.generated_at,
    run?.metadataFlat?.["context.generated_at_iso"],
    run?.metadataFlat?.["context.generated_at"],
  ];
  for (const candidate of candidates) {
    if (!candidate) continue;
    const ms = Date.parse(String(candidate));
    if (Number.isFinite(ms)) return ms;
  }
  return Number.NEGATIVE_INFINITY;
}

function formatRunTimestamp(run) {
  const ms = getRunTimestampMs(run);
  if (!Number.isFinite(ms) || ms === Number.NEGATIVE_INFINITY) return "no timestamp";
  return new Date(ms).toLocaleString(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function getRunSortIndicator(column) {
  if (runSortBy !== column) return "-";
  return runSortDir === "asc" ? "^" : "v";
}

function describeRunSort() {
  const label = runSortBy === "name" ? "name" : runSortBy;
  return `${label} ${runSortDir}`;
}

function firstMetaValue(flat, keys) {
  for (const key of keys) {
    const value = flat[key];
    if (value != null && String(value).trim() !== "") return value;
  }
  return "";
}

function countListish(value) {
  if (value == null) return 0;
  if (Array.isArray(value)) return value.filter(Boolean).length;
  const str = String(value).trim();
  if (!str) return 0;
  return str.split(/\s*,\s*/).filter(Boolean).length;
}

function previewListish(value, maxItems = 2) {
  if (value == null) return "";
  const items = Array.isArray(value)
    ? value.map(x => String(x).trim()).filter(Boolean)
    : String(value).split(/\s*,\s*/).map(x => x.trim()).filter(Boolean);
  if (!items.length) return "";
  if (items.length <= maxItems) return items.join(", ");
  return `${items.slice(0, maxItems).join(", ")} +${items.length - maxItems}`;
}

function tailPath(value) {
  if (value == null) return "";
  const normalized = String(value).replace(/\\/g, "/").trim();
  if (!normalized) return "";
  const parts = normalized.split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : normalized;
}


function summarizeRunBubbles(run) {
  const flat = run.metadataFlat || {};
  const bubbles = [];
  const MAX_ROW_BUBBLES = 5;
  const push = (label, rawValue, tone = "") => {
    if (rawValue == null || rawValue === "") return;
    const value = typeof rawValue === "object" ? JSON.stringify(rawValue) : String(rawValue);
    const compact = value.length > 24 ? `${value.slice(0, 21)}...` : value;
    bubbles.push({ label, value: compact, tone });
  };
  if (run.category === "calibration") {
    push("mode", firstMetaValue(flat, ["mode", "context.mode"]), "cal-primary");
    push("profile", firstMetaValue(flat, ["profile", "context.profile"]), "cal-secondary");
    push("score", firstMetaValue(flat, ["rank_score", "context.rank_score"]), "cal-warn");
    push("cycles", flat["context.cycle_scaling"]);
    push("tune", flat["context.tune_cycles"]);
    push("validate", flat["context.validate_cycles"]);
    push("window", firstMetaValue(flat, ["context.window_hours", "context.target_window_hours"]));
    push("events", firstMetaValue(flat, ["context.events", "context.total_events"]));
  } else if (run.category === "comparisons") {
    const policies = firstMetaValue(flat, ["context.policies"]);
    const policyCount = countListish(policies);
    if (policyCount <= 1) push("policy", previewListish(policies, 1), "cmp-primary");
    else push("policies", `${policyCount} (${previewListish(policies, 2)})`, "cmp-primary");
    const scenarioPath = firstMetaValue(flat, ["context.scenario", "context.base_scenario"]) || "";
    const scenarioTail = tailPath(scenarioPath);
    push("scenario", scenarioTail, "cmp-secondary");
    push("cycles", flat["context.cycles"]);
    push("limit", flat["context.limit"]);
    push("ticks", flat["context.ticks_per_cycle"]);
    const metricsFiles = firstMetaValue(flat, ["context.policy_metrics_files"]);
    const metricsCount = countListish(metricsFiles);
    push("metrics", metricsCount ? `${metricsCount} file(s)` : "");
    push("comparison", firstMetaValue(flat, ["context.comparison_file"]));
  } else if (run.category === "discrimination") {
    const lhs = firstMetaValue(flat, ["context.lhs_policy"]);
    const rhs = firstMetaValue(flat, ["context.rhs_policy"]);
    if (lhs || rhs) push("duel", `${lhs || "?"} vs ${rhs || "?"}`, "dis-primary");
    push("baseline", firstMetaValue(flat, ["context.baseline_policy"]), "dis-secondary");
    const policies = firstMetaValue(flat, ["context.policies"]);
    const variants = firstMetaValue(flat, ["context.variants"]);
    const variantCount = countListish(variants);
    const policyCount = countListish(policies);
    push("policies", policyCount ? `${policyCount} (${previewListish(policies, 2)})` : "");
    push("variants", variantCount ? `${variantCount} (${previewListish(variants, 2)})` : "");
    push("scenario", tailPath(firstMetaValue(flat, ["context.base_scenario", "context.scenario"])));
    push("cycles", flat["context.cycles"]);
    push("limit", flat["context.limit"]);
  } else if (run.category === "multi-merge") {
    push("window", firstMetaValue(flat, ["context.window", "context.period"]), "mm-primary");
    push("project", firstMetaValue(flat, ["context.project"]), "mm-secondary");
    push("mrs", flat["context.total_mrs"] || flat["context.mr_count"]);
  } else if (run.category === "monte-carlo") {
    const policySet = firstMetaValue(flat, ["context.policy_set"]);
    const policies = firstMetaValue(flat, ["context.policies"]);
    const policyCount = countListish(policies);
    push("set", policySet || "custom", "mc-primary");
    push("scenario", tailPath(firstMetaValue(flat, ["context.scenario", "context.base_scenario"])), "mc-secondary");
    push("policies", policyCount ? `${policyCount} (${previewListish(policies, 2)})` : "");
    push("trials", firstMetaValue(flat, ["context.trials"]));
    push("cycles", flat["context.cycles"]);
    push("limit", flat["context.limit"]);
    push("ticks", flat["context.ticks_per_cycle"]);
  } else if (run.category === "sweep") {
    const limits = firstMetaValue(flat, ["context.limits"]);
    const policies = firstMetaValue(flat, ["context.policies"]);
    push("limits", Array.isArray(limits) ? limits.join(",") : (limits || ""), "cmp-primary");
    push("policies", countListish(policies) ? `${countListish(policies)} (${previewListish(policies, 2)})` : "", "cmp-secondary");
    push("scenario", tailPath(firstMetaValue(flat, ["context.scenario", "custom_metadata.scenario"])));
    push("window", firstMetaValue(flat, ["context.window"]));
  } else {
    push("script", tailPath(firstMetaValue(flat, ["script"])), "meta-primary");
    push("host", firstMetaValue(flat, ["hostname"]));
  }
  if (!bubbles.length && run.subRun) push("leaf", run.subRun);
  push("files", pickLoadFilesForRun(run).length);
  if (bubbles.length <= MAX_ROW_BUBBLES) return bubbles;
  const extra = bubbles.length - MAX_ROW_BUBBLES;
  return [
    ...bubbles.slice(0, MAX_ROW_BUBBLES),
    { label: "more", value: `+${extra}`, tone: "meta-primary" },
  ];
}

function renderRunBrowserControls() {
  const categories = [...new Set(runCatalog.map(r => r.category))];
  const prevCategory = runCategorySelect.value;
  runCategorySelect.innerHTML = `<option value="">all</option>` + categories.map(c => `<option value="${mqEscapeHtml(c)}">${mqEscapeHtml(c)}</option>`).join("");
  if (categories.includes(prevCategory)) runCategorySelect.value = prevCategory;

  const keyMap = new Map();
  runCatalog.forEach(run => {
    Object.keys(run.metadataFlat).forEach(key => {
      if (!keyMap.has(key)) keyMap.set(key, new Set());
      keyMap.get(key).add(run.metadataFlat[key]);
    });
  });
  const prevKey = runMetaKeySelect.value;
  const keys = Array.from(keyMap.keys()).sort((a, b) => a.localeCompare(b));
  runMetaKeySelect.innerHTML = `<option value="">any</option>` + keys.map(k => `<option value="${mqEscapeHtml(k)}">${mqEscapeHtml(k)}</option>`).join("");
  if (keys.includes(prevKey)) runMetaKeySelect.value = prevKey;

  const names = [...new Set(runCatalog.map(r => r.run))].sort((a, b) => b.localeCompare(a));
  const prevRunName = runNameFilterSelect.value;
  runNameFilterSelect.innerHTML = `<option value="">any</option>` + names.map(n => `<option value="${mqEscapeHtml(n)}">${mqEscapeHtml(n)}</option>`).join("");
  if (names.includes(prevRunName)) runNameFilterSelect.value = prevRunName;

  renderMetaValueOptions(keyMap);
  renderRunChips();
}

function renderMetaValueOptions(keyMap = null) {
  const activeKey = runMetaKeySelect.value;
  const map = keyMap || (() => {
    const m = new Map();
    runCatalog.forEach(run => {
      if (!run.metadataFlat[activeKey]) return;
      if (!m.has(activeKey)) m.set(activeKey, new Set());
      m.get(activeKey).add(run.metadataFlat[activeKey]);
    });
    return m;
  })();
  const values = activeKey && map.has(activeKey)
    ? Array.from(map.get(activeKey)).sort((a, b) => a.localeCompare(b))
    : [];
  const prev = runMetaValueSelect.value;
  runMetaValueSelect.innerHTML = `<option value="">any</option>` + values.map(v => `<option value="${mqEscapeHtml(v)}">${mqEscapeHtml(v)}</option>`).join("");
  if (values.includes(prev)) runMetaValueSelect.value = prev;
}

function getRunByKey(key) {
  if (!key) return null;
  return runCatalog.find(r => r.key === key) || null;
}

function pruneEmpty(value) {
  if (Array.isArray(value)) {
    const items = value.map(pruneEmpty).filter(v => v != null);
    return items.length ? items : undefined;
  }
  if (value && typeof value === "object") {
    const out = {};
    Object.entries(value).forEach(([k, v]) => {
      const pruned = pruneEmpty(v);
      if (pruned != null && pruned !== "") out[k] = pruned;
    });
    return Object.keys(out).length ? out : undefined;
  }
  if (value == null || value === "") return undefined;
  return value;
}

function buildRunMetadataSummary(run, loadableCount) {
  const flat = run?.metadataFlat || {};
  const policies = firstMetaValue(flat, ["context.policies"]);
  const variants = firstMetaValue(flat, ["context.variants"]);
  const summary = {
    category: run?.category,
    run: run?.run,
    timestamp: formatRunTimestamp(run),
    sub_run: run?.subRun || "",
    highlights: {
      mode: firstMetaValue(flat, ["mode", "context.mode"]),
      profile: firstMetaValue(flat, ["profile", "context.profile"]),
      policy_set: firstMetaValue(flat, ["context.policy_set"]),
      policy_count: countListish(policies) || "",
      variant_count: countListish(variants) || "",
      scenario: tailPath(firstMetaValue(flat, ["context.scenario", "context.base_scenario"])),
      cycles: firstMetaValue(flat, ["context.cycles", "context.tune_cycles"]),
      validate_cycles: firstMetaValue(flat, ["context.validate_cycles"]),
      limit: firstMetaValue(flat, ["context.limit"]),
      ticks_per_cycle: firstMetaValue(flat, ["context.ticks_per_cycle"]),
      trials: firstMetaValue(flat, ["context.trials"]),
      score: firstMetaValue(flat, ["rank_score", "context.rank_score"]),
    },
    outputs: {
      indexed_files: Array.isArray(run?.files) ? run.files.length : 0,
      loadable_files: loadableCount,
    },
    metadata: flat,
  };
  return pruneEmpty(summary) || {};
}

function updateRunDetailPane() {
  const selected = getSelectedRunEntries();
  const run = getRunByKey(activeRunKey) || selected[0] || null;
  const contentEl = document.getElementById("runMetadataContent");
  if (!run) {
    if (contentEl) contentEl.innerHTML = "";
    runMetadataView.textContent = "{}";
    runOutputFiles.textContent = "";
    return;
  }
  const loadable = pickLoadFilesForRun(run);
  const compactSummary = buildRunMetadataSummary(run, loadable.length);
  runMetadataView.textContent = JSON.stringify(compactSummary, null, 2);

  if (contentEl) contentEl.innerHTML = _renderStructuredMetadata(run, loadable);

  const preview = loadable
    .slice(0, 8)
    .map(f => `<code>${mqEscapeHtml(f.webkitRelativePath || f.name)}</code>`)
    .join(", ");
  runOutputFiles.innerHTML = loadable.length
    ? `Loadable outputs (${loadable.length}): ${preview}${loadable.length > 8 ? ", ..." : ""}`
    : "No loadable outputs for this run.";
}

function _renderStructuredMetadata(run, loadable) {
  const flat = run.metadataFlat || {};
  const html = [];

  const decision = flat["context.decision_status"] || flat["decision.status"] || "";
  if (decision) {
    const cls = decision === "accepted" ? "accepted" : "rejected";
    html.push(`<div class="meta-decision ${cls}">${decision.toUpperCase()}</div>`);
  }

  const groups = {};
  for (const [key, val] of Object.entries(flat)) {
    if (val == null || val === "") continue;
    const dotIdx = key.indexOf(".");
    const group = dotIdx > 0 ? key.slice(0, dotIdx) : "info";
    const subKey = dotIdx > 0 ? key.slice(dotIdx + 1) : key;
    if (!groups[group]) groups[group] = [];
    groups[group].push([subKey, val]);
  }

  const groupOrder = ["decision", "context", "custom_metadata", "info"];
  const sortedGroups = Object.keys(groups).sort((a, b) => {
    const ai = groupOrder.indexOf(a), bi = groupOrder.indexOf(b);
    return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
  });

  for (const group of sortedGroups) {
    const entries = groups[group];
    html.push(`<div class="meta-section"><div class="meta-section-title">${mqEscapeHtml(group)}</div>`);
    for (const [key, val] of entries) {
      let valClass = "";
      const sv = String(val).toLowerCase();
      if (sv === "true" || sv === "accepted") valClass = "good";
      else if (sv === "false" || sv === "rejected") valClass = "error";
      else if (sv.includes("error") || sv.includes("fail")) valClass = "warn";
      const displayVal = String(val).length > 60 ? String(val).slice(0, 57) + "..." : String(val);
      const fullKey = group === "info" ? key : `${group}.${key}`;
      html.push(`<div class="meta-row meta-clickable" data-meta-key="${mqEscapeHtml(fullKey)}" data-meta-val="${mqEscapeHtml(String(val))}"><span class="meta-key" title="${mqEscapeHtml(key)}">${mqEscapeHtml(key)}</span><span class="meta-val ${valClass}" title="${mqEscapeHtml(String(val))}">${mqEscapeHtml(displayVal)}</span></div>`);
    }
    html.push("</div>");
  }

  return html.join("");
}

(function initMetaToggle() {
  const toggle = document.getElementById("metaToggleRaw");
  const pre = document.getElementById("runMetadataView");
  const content = document.getElementById("runMetadataContent");
  if (!toggle || !pre || !content) return;
  let showRaw = false;
  toggle.addEventListener("click", () => {
    showRaw = !showRaw;
    pre.classList.toggle("visible", showRaw);
    content.style.display = showRaw ? "none" : "";
    toggle.textContent = showRaw ? "Structured" : "JSON";
  });
  content.addEventListener("click", e => {
    const row = e.target.closest(".meta-clickable");
    if (!row) return;
    const key = row.dataset.metaKey || "";
    const val = row.dataset.metaVal || "";
    if (!key || !runSearchInput) return;
    runSearchInput.value = `${key}=${val}`;
    runSearchInput.dispatchEvent(new Event("input"));
    runSearchInput.focus();
  });
})();

function renderRunChips() {
  const filtered = getFilteredRuns();
  const filteredKeys = new Set(filtered.map(r => r.key));
  if (activeRunKey && !filteredKeys.has(activeRunKey)) activeRunKey = null;
  if (!activeRunKey && filtered.length) activeRunKey = filtered[0].key;
  if (!filtered.length) {
    runChipList.innerHTML = `<div class="run-list-empty">${runCatalog.length ? "No runs match the current filters." : "No run folder indexed yet."}</div>`;
  } else {
    const rows = filtered.map(run => {
      const selected = selectedRunKeys.has(run.key);
      const active = run.key === activeRunKey;
      const rowClass = `run-row${selected ? " selected" : ""}${active ? " active" : ""}`;
      const bubbles = summarizeRunBubbles(run);
      const bubblesHtml = bubbles.map(b => `<span class="run-bubble${b.tone ? ` ${mqEscapeHtml(b.tone)}` : ""}"><b>${mqEscapeHtml(b.label)}</b>: ${mqEscapeHtml(b.value)}</span>`).join("");
      const sub = run.subRun ? `<div class="run-subpath">${mqEscapeHtml(run.subRun)}</div>` : "";
      return `<tr class="${rowClass}" data-run-key="${mqEscapeHtml(run.key)}" title="${mqEscapeHtml(run.category)} / ${mqEscapeHtml(run.run)}">
        <td class="col-category">${mqEscapeHtml(run.category)}</td>
        <td class="col-timestamp">${mqEscapeHtml(formatRunTimestamp(run))}</td>
        <td class="col-name"><div class="run-name">${mqEscapeHtml(run.run)}</div>${sub}</td>
        <td class="col-bubbles"><div class="run-bubbles">${bubblesHtml || '<span class="run-bubble">no metadata bubbles</span>'}</div></td>
      </tr>`;
    }).join("");
    runChipList.innerHTML = `<table class="run-list-table">
      <thead>
        <tr>
          <th class="col-category run-sortable${runSortBy === "category" ? " sorted" : ""}" data-sort-col="category">Category <span class="sort-ind">${getRunSortIndicator("category")}</span></th>
          <th class="col-timestamp run-sortable${runSortBy === "timestamp" ? " sorted" : ""}" data-sort-col="timestamp">Timestamp <span class="sort-ind">${getRunSortIndicator("timestamp")}</span></th>
          <th class="col-name run-sortable${runSortBy === "name" ? " sorted" : ""}" data-sort-col="name">Name <span class="sort-ind">${getRunSortIndicator("name")}</span></th>
          <th class="col-bubbles">Bubbles</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
  }

  const selectedVisible = filtered.filter(r => selectedRunKeys.has(r.key)).length;
  const selectedTotal = selectedRunKeys.size;
  runBrowserStatus.textContent = filtered.length
    ? `${filtered.length} run(s) shown (sorted by ${describeRunSort()}) · ${selectedVisible} selected (total selected: ${selectedTotal}). Max one selected per non-calibration category. Press Enter to load selected.`
    : (runCatalog.length ? "No runs match the current filters." : "No run folder indexed yet.");
  updateRunDetailPane();
}

function getSelectedRunEntries() {
  if (!selectedRunKeys.size) return [];
  return runCatalog.filter(r => selectedRunKeys.has(r.key));
}

function pickLoadFilesForRun(run) {
  if (!run) return [];
  const byExt = run.files.filter(x => /\.(ndjson|jsonl|json|csv|ya?ml)$/i.test(x.file.name));
  if (run.category === "sweep") return byExt.filter(x => /\.json$/i.test(x.file.name) && !/metadata\.json$/i.test(x.file.name)).map(x => x.file);
  if (run.category === "comparisons") return byExt.filter(x => /\.ndjson$/i.test(x.file.name) || /\.ya?ml$/i.test(x.file.name) || /per-label-merge-times\.json$/i.test(x.file.name)).map(x => x.file);
  if (run.category === "discrimination") return byExt.filter(x => /discrimination-summary\.csv$/i.test(x.file.name)).map(x => x.file);
  if (run.category === "monte-carlo") return byExt.filter(x => /monte-carlo-summary\.csv$/i.test(x.file.name)).map(x => x.file);
  if (run.category === "multi-merge") return byExt.filter(x => /plan-raw.*\.json$/i.test(x.file.name)).map(x => x.file);
  if (run.category === "calibration") return byExt.filter(x => /calibration-.*\.csv$/i.test(x.file.name) || /-grid\.csv$/i.test(x.file.name) || /-validation\.csv$/i.test(x.file.name) || /adaptive-vs-fixed-summary\.csv$/i.test(x.file.name) || /selected-scenario\.ya?ml$/i.test(x.file.name) || /scenario.*\.ya?ml$/i.test(x.file.name)).map(x => x.file);
  return byExt.map(x => x.file);
}

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("dragover", e => e.preventDefault());
dropZone.addEventListener("drop", e => { e.preventDefault(); if (e.dataTransfer.files) loadFiles(e.dataTransfer.files); });
fileInput.addEventListener("change", () => { if (fileInput.files) loadFiles(fileInput.files); });
runDirInput.addEventListener("change", () => { if (runDirInput.files) indexRunDirectory(runDirInput.files); });
btnBrowseFiles.addEventListener("click", () => fileInput.click());
btnBrowseRunFolder.addEventListener("click", () => runDirInput.click());
if (btnUnload) btnUnload.addEventListener("click", unloadAllData);
if (btnRefreshRuns) btnRefreshRuns.addEventListener("click", () => {
  if (lastRunDirFiles) { indexRunDirectory(lastRunDirFiles); runBrowserStatus.textContent = "Refreshed folder index."; }
  else { runBrowserStatus.textContent = "No folder loaded to refresh. Import a folder first."; }
});

function buildSearchSuggestions(query) {
  const suggestions = [];
  if (!runCatalog.length) return suggestions;
  const lower = query.toLowerCase();
  const eqIdx = lower.indexOf("=");
  if (eqIdx < 0) {
    const allKeys = new Set(["category"]);
    runCatalog.forEach(r => Object.keys(r.metadataFlat).forEach(k => allKeys.add(k)));
    const cats = [...new Set(runCatalog.map(r => r.category))];
    cats.filter(c => c.toLowerCase().includes(lower)).forEach(c => {
      suggestions.push({ display: `category=${c}`, insert: `category=${c} `, keyPart: "category", valPart: c });
    });
    [...allKeys].sort().filter(k => k.toLowerCase().includes(lower)).slice(0, 12).forEach(k => {
      suggestions.push({ display: `${k}=`, insert: `${k}=`, keyPart: k, valPart: "" });
    });
  } else {
    const keyPart = lower.slice(0, eqIdx);
    const valPart = lower.slice(eqIdx + 1);
    const valuesSet = new Set();
    runCatalog.forEach(run => {
      if (keyPart === "category") {
        if (run.category.toLowerCase().includes(valPart)) valuesSet.add(run.category);
      } else {
        Object.entries(run.metadataFlat).forEach(([k, v]) => {
          if (k.toLowerCase().includes(keyPart) && String(v).toLowerCase().includes(valPart)) valuesSet.add(`${k}=${v}`);
        });
      }
    });
    [...valuesSet].sort().slice(0, 15).forEach(v => {
      const fullInsert = keyPart === "category" ? `category=${v} ` : `${v} `;
      suggestions.push({ display: keyPart === "category" ? `category=${v}` : v, insert: fullInsert, keyPart, valPart: v });
    });
  }
  return suggestions.slice(0, 15);
}

function renderSearchSuggestions() {
  if (!runSearchInput || !runSearchSuggestions) return;
  const raw = runSearchInput.value;
  const lastSpaceIdx = raw.lastIndexOf(" ");
  const currentToken = lastSpaceIdx >= 0 ? raw.slice(lastSpaceIdx + 1) : raw;
  if (!currentToken) { runSearchSuggestions.classList.remove("visible"); return; }
  const suggestions = buildSearchSuggestions(currentToken);
  if (!suggestions.length) { runSearchSuggestions.classList.remove("visible"); return; }
  runSearchActiveSuggestion = -1;
  runSearchSuggestions.innerHTML = suggestions.map((s, i) => {
    const eqIdx = s.display.indexOf("=");
    const keyHtml = eqIdx >= 0 ? `<span class="sg-key">${mqEscapeHtml(s.display.slice(0, eqIdx + 1))}</span><span class="sg-val">${mqEscapeHtml(s.display.slice(eqIdx + 1))}</span>` : `<span class="sg-key">${mqEscapeHtml(s.display)}</span>`;
    return `<div class="run-search-suggestion" data-idx="${i}" data-insert="${mqEscapeHtml(s.insert)}">${keyHtml}</div>`;
  }).join("");
  runSearchSuggestions.classList.add("visible");
}

function applySearchSuggestion(insertText) {
  if (!runSearchInput) return;
  const raw = runSearchInput.value;
  const lastSpaceIdx = raw.lastIndexOf(" ");
  const prefix = lastSpaceIdx >= 0 ? raw.slice(0, lastSpaceIdx + 1) : "";
  runSearchInput.value = prefix + insertText;
  runSearchSuggestions.classList.remove("visible");
  renderRunChips();
  runSearchInput.focus();
}

if (runSearchInput) {
  runSearchInput.addEventListener("input", () => { renderSearchSuggestions(); renderRunChips(); });
  runSearchInput.addEventListener("focus", () => renderSearchSuggestions());
  runSearchInput.addEventListener("blur", () => setTimeout(() => runSearchSuggestions.classList.remove("visible"), 180));
  runSearchInput.addEventListener("keydown", e => {
    const items = runSearchSuggestions.querySelectorAll(".run-search-suggestion");
    if (e.key === "ArrowDown") { e.preventDefault(); runSearchActiveSuggestion = Math.min(runSearchActiveSuggestion + 1, items.length - 1); items.forEach((el, i) => el.classList.toggle("active", i === runSearchActiveSuggestion)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); runSearchActiveSuggestion = Math.max(runSearchActiveSuggestion - 1, 0); items.forEach((el, i) => el.classList.toggle("active", i === runSearchActiveSuggestion)); }
    else if ((e.key === "Tab" || e.key === "Enter") && runSearchActiveSuggestion >= 0 && items[runSearchActiveSuggestion]) {
      e.preventDefault();
      applySearchSuggestion(items[runSearchActiveSuggestion].getAttribute("data-insert") || "");
    }
  });
}
if (runSearchSuggestions) {
  runSearchSuggestions.addEventListener("mousedown", e => {
    const el = e.target.closest(".run-search-suggestion");
    if (el) { e.preventDefault(); applySearchSuggestion(el.getAttribute("data-insert") || ""); }
  });
}

runChipList.addEventListener("click", e => {
  const sortHeader = e.target.closest(".run-sortable[data-sort-col]");
  if (sortHeader) {
    const column = sortHeader.getAttribute("data-sort-col") || "";
    if (!RUN_SORTABLE_COLUMNS.has(column)) return;
    if (runSortBy === column) {
      runSortDir = runSortDir === "asc" ? "desc" : "asc";
    } else {
      runSortBy = column;
      runSortDir = column === "timestamp" ? "desc" : "asc";
    }
    renderRunChips();
    return;
  }
  const row = e.target.closest(".run-row");
  if (!row) return;
  const key = row.getAttribute("data-run-key");
  if (!key) return;
  const clicked = getRunByKey(key);
  if (!clicked) return;
  activeRunKey = key;
  if (selectedRunKeys.has(key)) {
    selectedRunKeys.delete(key);
  } else {
    // Enforce one selected run per non-calibration category.
    if (clicked.category !== "calibration") {
      Array.from(selectedRunKeys).forEach(existingKey => {
        const existing = getRunByKey(existingKey);
        if (existing?.category === clicked.category) selectedRunKeys.delete(existingKey);
      });
    }
    selectedRunKeys.add(key);
  }
  renderRunChips();
});

function loadSelectedRunsFromBrowser() {
  const selectedRuns = getSelectedRunEntries();
  if (!selectedRuns.length) {
    runBrowserStatus.textContent = "Select one or more run rows first.";
    return;
  }
  const uniq = new Map();
  selectedRuns.forEach(run => {
    pickLoadFilesForRun(run).forEach(file => {
      const rel = file.webkitRelativePath || file.name;
      const key = `${rel}|${file.size}|${file.lastModified}`;
      if (!uniq.has(key)) uniq.set(key, file);
    });
  });
  const files = Array.from(uniq.values());
  if (!files.length) {
    runBrowserStatus.textContent = "Selected runs have no loadable NDJSON/CSV/YAML outputs.";
    return;
  }
  const nextMeta = { ...loadedRunMetadataByCategory };
  selectedRuns.forEach(run => { nextMeta[run.category] = run.metadata || {}; });
  loadedRunMetadataByCategory = nextMeta;
  loadFiles(files, { replaceByCategory: true });
  const categories = [...new Set(selectedRuns.map(r => r.category))].join(", ");
  runBrowserStatus.textContent = `Loaded ${files.length} file(s) from ${selectedRuns.length} selected run(s). Replaced loaded data for: ${categories}.`;
}

btnLoadSelectedRun.addEventListener("click", () => {
  loadSelectedRunsFromBrowser();
});
document.addEventListener("keydown", e => {
  if (!document.getElementById("panelLoad").classList.contains("active")) return;
  if (isTypingTarget(e.target)) return;
  if (e.key === "Enter") {
    if (!selectedRunKeys.size) return;
    e.preventDefault();
    loadSelectedRunsFromBrowser();
    return;
  }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const filtered = getFilteredRuns();
    if (!filtered.length) return;
    const curIdx = filtered.findIndex(r => r.key === activeRunKey);
    let nextIdx;
    if (e.key === "ArrowDown") nextIdx = curIdx < filtered.length - 1 ? curIdx + 1 : 0;
    else nextIdx = curIdx > 0 ? curIdx - 1 : filtered.length - 1;
    activeRunKey = filtered[nextIdx].key;
    renderRunChips();
    const activeRow = runChipList.querySelector(`.run-row[data-run-key="${CSS.escape(activeRunKey)}"]`);
    if (activeRow) activeRow.scrollIntoView({ block: "nearest" });
    return;
  }
  if (e.key === " ") {
    e.preventDefault();
    if (!activeRunKey) return;
    const run = getRunByKey(activeRunKey);
    if (!run) return;
    if (selectedRunKeys.has(activeRunKey)) {
      selectedRunKeys.delete(activeRunKey);
    } else {
      if (run.category !== "calibration") {
        Array.from(selectedRunKeys).forEach(k => {
          const ex = getRunByKey(k);
          if (ex?.category === run.category) selectedRunKeys.delete(k);
        });
      }
      selectedRunKeys.add(activeRunKey);
    }
    renderRunChips();
    return;
  }
});

function enableTabs(tabNames) {
  tabNames.forEach(name => {
    const btn = document.querySelector(`.nav-tab[data-tab="${name}"]`);
    if (btn) btn.classList.remove("disabled");
  });
}

function disableTabs(tabNames) {
  tabNames.forEach(name => {
    const btn = document.querySelector(`.nav-tab[data-tab="${name}"]`);
    if (btn) btn.classList.add("disabled");
  });
}

function unloadAllData() {
  stop();
  clearAllSwimLocks();

  mqDestroyChartIfPresent(chartInstL);
  chartInstL = null;
  mqDestroyChartIfPresent(chartInstR);
  chartInstR = null;
  barCharts.forEach(ch => mqDestroyChartIfPresent(ch));
  barCharts.clear();

  if (typeof destroyExperimentCharts === "function") destroyExperimentCharts();
  if (typeof _destroySimCharts === "function") _destroySimCharts();
  mqDestroyChartIfPresent(mcBoxChart);
  mcBoxChart = null;
  mqDestroyChartIfPresent(mcCIChart);
  mcCIChart = null;

  filesData = [];
  loadedScenarioDocs = [];
  loadedRunMetadataByCategory = {};
  expData = {
    discrimination: null,
    discriminationMeta: null,
    calibrationGrid: [],
    calibrationValidation: [],
    calibrationSummary: [],
  };
  mcData = null;
  sweepLoaded = false;
  sweepData = null;
  sweepDocs = [];
  mqDestroyChartIfPresent(sweepLineChart); sweepLineChart = null;
  mqDestroyChartIfPresent(sweepBarChart); sweepBarChart = null;
  mqDestroyChartIfPresent(sweepConvergeChart); sweepConvergeChart = null;
  perLabelData = null;
  mqDestroyChartIfPresent(perLabelChart); perLabelChart = null;
  mqDestroyChartIfPresent(perLabelP95Chart); perLabelP95Chart = null;
  if (typeof unloadMultiMergeData === "function") unloadMultiMergeData();
  multiMergeLoaded = false;
  multiMergeFileName = "";
  statsSubtab = "metrics";
  simSubtab = "scenario";

  selectedSeries = null;
  statsExtendedMode = false;
  activeIndex = 0;
  chartIdxL = 0;
  chartIdxR = 1;
  swimIdx = 0;
  playhead = 0;
  xRange = null;
  kanbanVisibleIdxs = new Set();
  barVisibleIdxs = new Set();
  mcVisiblePolicies = new Set();

  BARS_LEGEND_KEYS.forEach(key => { barsLegendVisibility[key] = true; });
  barsStatsMode = "max";
  barsStatsWindow = "all";
  barsStatsSeriesKey = "Idle";
  barsStatsEnabled = true;

  const scrub = document.getElementById("scrub");
  if (scrub) {
    scrub.max = 0;
    scrub.value = 0;
  }
  const playNow = document.getElementById("playNow");
  const playTotal = document.getElementById("playTotal");
  if (playNow) { playNow.textContent = "Step 0"; playNow.style.minWidth = ""; }
  if (playTotal) playTotal.textContent = " / 0";
  _playLabelLockedWidth = 0;

  const mcContent = document.getElementById("mcContent");
  if (mcContent) mcContent.style.display = "none";

  if (fileInput) fileInput.value = "";
  if (runDirInput) runDirInput.value = "";

  disableTabs(DATA_TABS);
  renderFileList();
  updateLoadedCount();
  updateRefreshUiButtonState();
  switchTab("load");
  runBrowserStatus.textContent = "Unloaded all loaded files and metrics. Run browser index is still available.";
}

function loadFiles(fl, opts = {}) {
  const replaceByCategory = opts.replaceByCategory !== false;
  const a = Array.from(fl); if (!a.length) return;
  const ndjsonFiles = a.filter(f => /\.(ndjson|jsonl|json)$/i.test(f.name));
  const csvFiles = a.filter(f => /\.csv$/i.test(f.name));
  const yamlFiles = a.filter(f => /\.ya?ml$/i.test(f.name));

  let ndjsonLoaded = false;
  let csvMonteLoaded = false;
  let csvExpLoaded = false;
  let yamlLoaded = false;
  let sweepJsonLoaded = false;
  const tasks = [];
  if (ndjsonFiles.length) {
    tasks.push(Promise.all(ndjsonFiles.map(F => new Promise((R, N) => { const r = new FileReader(); r.onload = () => R({ n: F.name.replace(/(-metrics)?\.(ndjson|jsonl|json)$/i, ""), t: r.result, fileName: F.name }); r.onerror = N; r.readAsText(F); }))).then(A => {
      const sweepFiles = [];
      const perLabelFiles = [];
      const metricsFiles = [];
      A.forEach(x => {
        if (/^\./.test(x.fileName)) return;
        if (/\.json$/i.test(x.fileName)) {
          try {
            const parsed = JSON.parse(x.t);
            if (parsed && Array.isArray(parsed.limits) && parsed.data) { sweepFiles.push(parsed); return; }
            if (parsed && _isPerLabelData(parsed)) { perLabelFiles.push(parsed); return; }
            if (Array.isArray(parsed) && parsed.length && parsed[0].iid && parsed[0].changed_files) {
              if (typeof loadMultiMergeData === "function") loadMultiMergeData(parsed);
              multiMergeLoaded = true;
              multiMergeFileName = x.fileName || "plan-raw";
              return;
            }
          } catch {}
        }
        metricsFiles.push(x);
      });
      if (sweepFiles.length) {
        // Three caps, three sweeps. Keeping only the last one meant a run
        // directory holding all three showed one of them.
        loadSweepData(sweepFiles);
        sweepLoaded = true;
        sweepJsonLoaded = true;
      }
      if (perLabelFiles.length) {
        perLabelData = perLabelFiles[perLabelFiles.length - 1];
        enableTabs(["stats"]);
      }
      if (metricsFiles.length) {
        filesData = metricsFiles.map(x => ({ name: x.n, events: parseNdjson(x.t) }));
        applyLoadedScenariosToFilesData();
        activeIndex = 0; chartIdxL = 0; chartIdxR = Math.min(1, metricsFiles.length - 1); swimIdx = 0; stop();
        filesData.forEach(d => { if (!d.metrics) { const M = computeFileMetrics(d.events); d.metrics = M; d.packed = M.packed; } });
        kanbanVisibleIdxs = new Set(filesData.map((_, i) => i));
        barVisibleIdxs = new Set(filesData.map((_, i) => i));
        const mx = globalMaxTick(); playhead = 0; xRange = null;
        updatePlayLabel();
        document.getElementById("scrub").max = mx;
        document.getElementById("scrub").value = 0;
        enableTabs(["stats", "simulation", "kanban", "composition", "bars", "swimlane"]);
        ndjsonLoaded = true;
      }
    }));
  }
  if (yamlFiles.length) {
    tasks.push(Promise.all(yamlFiles.map(F => new Promise((R, N) => {
      const r = new FileReader();
      r.onload = () => R({ fileName: F.name, text: String(r.result || "") });
      r.onerror = N;
      r.readAsText(F);
    }))).then(items => {
      const parsed = items
        .map(item => ({ ...item, doc: parseYamlDoc(item.text) }))
        .filter(item => item.doc && typeof item.doc === "object");
      if (parsed.length) {
        loadedScenarioDocs = parsed.map(item => ({ fileName: item.fileName, doc: item.doc }));
        yamlLoaded = true;
        applyLoadedScenariosToFilesData();
        enableTabs(["simulation"]);
      }
    }));
  }
  if (csvFiles.length) {
    tasks.push(Promise.all(csvFiles.map(F => new Promise((R, N) => {
      const r = new FileReader();
      r.onload = () => R({ name: F.name, text: r.result });
      r.onerror = N;
      r.readAsText(F);
    }))).then(items => {
      const parsedItems = items.map(({ name, text }) => ({
        name,
        text: String(text),
        kind: mqDetectCsvType(String(text)),
      }));
      if (replaceByCategory) {
        if (parsedItems.some(x => x.kind === "monteCarlo")) {
          mcData = null;
        }
        if (parsedItems.some(x => x.kind === "discrimination")) {
          expData.discrimination = null;
          expData.discriminationMeta = null;
        }
        if (parsedItems.some(x => x.kind === "calibration" || x.kind === "calibrationSummary")) {
          expData.calibrationGrid = [];
          expData.calibrationValidation = [];
          expData.calibrationSummary = [];
        }
      }
      parsedItems.forEach(({ name, text, kind }) => {
        if (kind === "monteCarlo") {
          loadMcCSVText(text);
          csvMonteLoaded = true;
        } else if (kind === "discrimination") {
          loadDiscriminationCSVText(text);
          csvExpLoaded = true;
        } else if (kind === "calibration") {
          loadCalibrationCSVText(text, name);
          csvExpLoaded = true;
        } else if (kind === "calibrationSummary") {
          loadCalibrationSummaryCSVText(text, name);
          csvExpLoaded = true;
        }
      });
      if (csvMonteLoaded) enableTabs(["monteCarlo"]);
      if (csvExpLoaded) enableTabs(["experiments"]);
    }));
  }

  Promise.all(tasks).then(() => {
    renderFileList();
    updateLoadedCount();
    updateRefreshUiButtonState();
    if (ndjsonLoaded || yamlLoaded) {
      if (filesData.length) switchTab("simulation");
      else switchTab("simulation");
    }
    else if (multiMergeLoaded) switchTab("multiMerge");
    else if (sweepJsonLoaded) switchTab("sweep");
    else if (csvExpLoaded) switchTab("experiments");
    else if (csvMonteLoaded) switchTab("monteCarlo");
  });
}
function updateLoadedCount() {
  if (!loadedFileCount) return;
  const count = filesData.length + loadedScenarioDocs.length + (mcData ? 1 : 0) + (sweepLoaded ? 1 : 0) + (perLabelData ? 1 : 0) + (expData.discrimination ? 1 : 0) + (expData.calibrationGrid.length ? 1 : 0) + (multiMergeLoaded ? 1 : 0);
  loadedFileCount.textContent = count ? `${count} file${count !== 1 ? "s" : ""}` : "0 files";
}
function renderFileList() {
  const el = document.getElementById("fileList");
  el.innerHTML = "";
  const chips = [];
  filesData.forEach((d, i) => {
    chips.push(`<span class="file-chip fc-ndjson" data-unload="ndjson" data-idx="${i}" title="Click to unload">${mqEscapeHtml(d.name)}.ndjson<span class="fc-x">\u00d7</span></span>`);
  });
  loadedScenarioDocs.forEach((s, i) => {
    chips.push(`<span class="file-chip fc-scenario" data-unload="scenario" data-idx="${i}" title="Click to unload">${mqEscapeHtml(s.fileName)}<span class="fc-x">\u00d7</span></span>`);
  });
  if (mcData) {
    chips.push(`<span class="file-chip fc-csv" data-unload="mc" title="Click to unload">monte-carlo (${mcData.policies.length}p/${mcData.metrics.length}m)<span class="fc-x">\u00d7</span></span>`);
  }
  if (expData.discrimination) {
    chips.push(`<span class="file-chip fc-csv" data-unload="discrimination" title="Click to unload">discrimination (${expData.discrimination.length})<span class="fc-x">\u00d7</span></span>`);
  }
  if (expData.calibrationGrid.length || expData.calibrationValidation.length || expData.calibrationSummary.length) {
    chips.push(`<span class="file-chip fc-csv" data-unload="calibration" title="Click to unload">calibration (${expData.calibrationGrid.length}t/${expData.calibrationValidation.length}v)<span class="fc-x">\u00d7</span></span>`);
  }
  if (sweepLoaded && sweepData) {
    chips.push(`<span class="file-chip fc-ndjson" data-unload="sweep" title="Click to unload">sweep (${sweepDocs.length} ${sweepDocs.length === 1 ? 'axis' : 'axes'} × ${sweepData.policies.length} policies)<span class="fc-x">\u00d7</span></span>`);
  }
  if (perLabelData) {
    const pCount = Object.keys(perLabelData).length;
    chips.push(`<span class="file-chip fc-csv" data-unload="perlabel" title="Click to unload">per-label (${pCount} policies)<span class="fc-x">\u00d7</span></span>`);
  }
  if (multiMergeLoaded) {
    const mrCount = (typeof mmRawData !== "undefined" && mmRawData) ? mmRawData.length : "?";
    chips.push(`<span class="file-chip fc-ndjson" data-unload="multimerge" title="Click to unload">multi-merge (${mrCount} MRs)<span class="fc-x">\u00d7</span></span>`);
  }
  el.innerHTML = chips.join("");
}

(function initFileChipUnload() {
  const el = document.getElementById("fileList");
  if (!el) return;
  el.addEventListener("click", e => {
    const chip = e.target.closest(".file-chip");
    if (!chip) return;
    const kind = chip.dataset.unload;
    const idx = parseInt(chip.dataset.idx || "0", 10);
    if (kind === "ndjson" && idx >= 0 && idx < filesData.length) {
      filesData.splice(idx, 1);
      if (activeIndex >= filesData.length) activeIndex = Math.max(0, filesData.length - 1);
    } else if (kind === "scenario" && idx >= 0 && idx < loadedScenarioDocs.length) {
      loadedScenarioDocs.splice(idx, 1);
    } else if (kind === "mc") {
      mcData = null;
    } else if (kind === "discrimination") {
      expData.discrimination = null;
    } else if (kind === "calibration") {
      expData.calibrationGrid = [];
      expData.calibrationValidation = [];
      expData.calibrationSummary = [];
    } else if (kind === "sweep") {
      sweepLoaded = false;
      sweepData = null;
      sweepDocs = [];
      mqDestroyChartIfPresent(sweepLineChart); sweepLineChart = null;
      mqDestroyChartIfPresent(sweepBarChart); sweepBarChart = null;
      mqDestroyChartIfPresent(sweepConvergeChart); sweepConvergeChart = null;
      disableTabs(["sweep"]);
    } else if (kind === "perlabel") {
      perLabelData = null;
      mqDestroyChartIfPresent(perLabelChart); perLabelChart = null;
      mqDestroyChartIfPresent(perLabelP95Chart); perLabelP95Chart = null;
      statsSubtab = "metrics";
    }
    renderFileList();
    updateLoadedCount();
    updateRefreshUiButtonState();
    const tab = getActiveTabName();
    renderTabContent(tab);
  });
})();

// --- Statistics Tab ---
let selectedSeries = null; // null = all selected
let statsExtendedMode = false; // false=standard, true=extended

// Both tables are derived from src/mqsim/metrics.json via ui/metrics-spec.js.
// They used to be maintained here by hand, which is how the Discrimination
// panel ended up reading a weights key the producer never wrote.
const MQSIM_METRIC_SPEC = window.MQSIM_METRIC_SPEC || {};
const METRIC_TOOLTIPS = {};
const METRIC_HIGHER_IS_BETTER = {};
Object.entries(MQSIM_METRIC_SPEC).forEach(([key, entry]) => {
  const names = [key].concat(entry.ui_aliases || []);
  names.forEach(name => {
    METRIC_TOOLTIPS[name] = entry.tooltip;
    METRIC_HIGHER_IS_BETTER[name] = entry.higher_is_better;
  });
});


function getSelectedIndices() {
  if (!selectedSeries || selectedSeries.length === 0) return filesData.map((_, i) => i);
  return selectedSeries;
}
function getSelectedIndexSet() {
  return new Set(getSelectedIndices());
}
function toggleSeriesSelection(idx) {
  const i = Number(idx);
  if (!Number.isInteger(i) || i < 0 || i >= filesData.length) return;
  const set = getSelectedIndexSet();
  if (set.has(i)) set.delete(i);
  else set.add(i);
  if (set.size === 0 || set.size === filesData.length) {
    selectedSeries = null;
    return;
  }
  selectedSeries = Array.from(set).sort((a, b) => a - b);
}
function normalizeSelectedSeries() {
  if (!Array.isArray(selectedSeries)) return;
  const max = filesData.length - 1;
  const next = selectedSeries
    .map(n => Number(n))
    .filter(n => Number.isInteger(n) && n >= 0 && n <= max)
    .sort((a, b) => a - b);
  if (!next.length || next.length === filesData.length) selectedSeries = null;
  else selectedSeries = [...new Set(next)];
}

const ALL_HERO_METRICS = [
  { key: "mrs_merged", label: "MRs Merged", higher: true, fmt: v => Math.round(v) },
  { key: "throughput", label: "Throughput", higher: true, fmt: v => v.toFixed(3) },
  { key: "throughput_hour", label: "Throughput (hour)", higher: true, fmt: v => v.toFixed(2) },
  { key: "throughput_24h_mph", label: "Throughput (24h mph)", higher: true, fmt: v => v.toFixed(2) },
  { key: "throughput_active_mph", label: "Throughput (active mph)", higher: true, fmt: v => v.toFixed(2) },
  { key: "throughput_peak8_mph", label: "Peak8 throughput", higher: true, fmt: v => v.toFixed(2) },
  { key: "throughput_peak_window_mph", label: "Peak-window mph", higher: true, fmt: v => v.toFixed(2) },
  { key: "throughput_offpeak_window_mph", label: "Off-peak mph", higher: true, fmt: v => v.toFixed(2) },
  { key: "peak_offpeak_throughput_ratio", label: "Peak/Off-peak ratio", higher: true, fmt: v => v.toFixed(2) + "x" },
  { key: "queue_drain", label: "Queue Drain %", higher: true, fmt: v => Math.round(v) + "%" },
  { key: "ci_avg", label: "Avg CI Time", higher: false, fmt: v => v.toFixed(1) },
  { key: "peak", label: "Peak Pipelines", higher: false, fmt: v => v },
  { key: "dup", label: "Wasted Rebases", higher: false, fmt: v => v },
  { key: "time_to_first", label: "Time to 1st Merge", higher: false, fmt: v => Math.round(v) },
  { key: "time_to_10", label: "Time to 10 Merges", higher: false, fmt: v => Math.round(v) },
  { key: "avg_int", label: "Avg Merge Interval", higher: false, fmt: v => v.toFixed(1) },
  { key: "rebase_calls", label: "Total Rebases", higher: false, fmt: v => Math.round(v) },
  { key: "merge_errors", label: "Merge Errors", higher: false, fmt: v => Math.round(v) },
  { key: "rebase_errors", label: "Rebase Errors", higher: false, fmt: v => Math.round(v) },
  { key: "merge_interval_p95_seconds", label: "Merge p95 (s)", higher: false, fmt: v => v.toFixed(1) },
  { key: "srp_p95", label: "Same-root p95", higher: false, fmt: v => v },
  { key: "srp_max", label: "Same-root max", higher: false, fmt: v => v },
  { key: "merge_rounds", label: "Merge Rounds", higher: true, fmt: v => Math.round(v) },
  { key: "avg_mrs_per_round", label: "MRs/Round", higher: true, fmt: v => v.toFixed(3) },
];
const EXTENDED_ONLY_METRIC_KEYS = new Set([
  "throughput_24h_mph",
  "throughput_active_mph",
  "throughput_peak8_mph",
  "throughput_peak_window_mph",
  "throughput_offpeak_window_mph",
  "peak_offpeak_throughput_ratio",
  "merge_interval_p95_seconds",
]);
const STANDARD_DEFAULT_HERO_METRIC_KEYS = ["mrs_merged", "throughput_hour", "queue_drain", "dup", "time_to_10", "rebase_calls"];
const EXTENDED_DEFAULT_HERO_METRIC_KEYS = ["mrs_merged", "throughput_peak8_mph", "throughput_peak_window_mph", "throughput_offpeak_window_mph", "peak_offpeak_throughput_ratio", "merge_interval_p95_seconds"];
let heroMetricKeysStandard = [...STANDARD_DEFAULT_HERO_METRIC_KEYS];
let heroMetricKeysExtended = [...EXTENDED_DEFAULT_HERO_METRIC_KEYS];
function getActiveHeroMetricKeys() {
  return statsExtendedMode ? heroMetricKeysExtended : heroMetricKeysStandard;
}
function setActiveHeroMetricKeys(nextKeys) {
  if (statsExtendedMode) heroMetricKeysExtended = nextKeys;
  else heroMetricKeysStandard = nextKeys;
}
function getVisibleHeroMetrics() {
  if (statsExtendedMode) return ALL_HERO_METRICS;
  return ALL_HERO_METRICS.filter(m => !EXTENDED_ONLY_METRIC_KEYS.has(m.key));
}

function renderScenarioInfoCard() {
  const el = document.getElementById("scenarioInfoCard");
  if (!el) return;
  normalizeSelectedSeries();
  const meta = filesData.map(d => (d.events || []).find(e => e.event === "scenario_meta")).filter(Boolean);
  const m = meta[0] || {};
  const selectedSet = getSelectedIndexSet();
  const chips = filesData.map((d, i) => (
    `<button class="si-policy-chip${selectedSet.has(i) ? " active" : ""}" data-series-idx="${i}" title="${mqEscapeHtml(d.name)}">${mqEscapeHtml(d.name)}</button>`
  )).join("");
  const mergePct = (Number(m?.operation_failure_rate?.merge || 0) * 100).toFixed(2);
  const rebasePct = (Number(m?.operation_failure_rate?.rebase || 0) * 100).toFixed(2);
  const hasOpsFail = Number(mergePct) > 0 || Number(rebasePct) > 0;
  const modeOffCls = statsExtendedMode ? "" : " active";
  const modeOnCls = statsExtendedMode ? " active" : "";
  const modeSwitchCls = statsExtendedMode ? " on" : "";

  let mainItems = `<span class="si-title">Scenario</span>`;
  if (Number.isFinite(Number(m.total_mrs))) {
    const scheduled = Array.isArray(m.arrivals) ? m.arrivals.length : 0;
    const starting = Math.max(0, Number(m.total_mrs) - scheduled);
    const split = scheduled ? ` (${starting} starting + ${scheduled} arriving)` : "";
    mainItems += `<span class="si-item"><span class="si-label">MRs:</span><span class="si-val">${m.total_mrs}${split}</span></span>`;
  }
  mainItems += `<span class="si-item"><span class="si-label">Tick:</span><span class="si-val">${m.tick_seconds || 60}s</span></span>`;
  if (m.pipeline_duration) mainItems += `<span class="si-item"><span class="si-label">CI Duration:</span><span class="si-val">${m.pipeline_duration.min}–${m.pipeline_duration.max} ticks</span></span>`;
  if (m.failure_rate != null) mainItems += `<span class="si-item"><span class="si-label">Failure Rate:</span><span class="si-val${m.failure_rate > 0 ? " danger" : ""}">${(Number(m.failure_rate) * 100).toFixed(1)}%</span></span>`;
  if (m.operation_failure_rate) {
    mainItems += `<span class="si-item"><span class="si-label">Op failures:</span><span class="si-val${hasOpsFail ? " danger" : ""}">merge ${mergePct}% / rebase ${rebasePct}%</span></span>`;
  }
  const modeledHours = filesData.length ? Number(filesData[0]?.metrics?.modeled_hours) : NaN;
  if (Number.isFinite(modeledHours) && modeledHours > 0) {
    mainItems += `<span class="si-item"><span class="si-label">Duration:</span><span class="si-val">${modeledHours.toFixed(1)}h</span></span>`;
  }

  el.innerHTML = `
    <div class="scenario-info">
      <div class="si-main">${mainItems}</div>
      <div class="si-controls">
        <div class="si-policy-row">
          <span class="si-controls-label">Select Policies</span>
          <div class="si-policy-chips">${chips}</div>
        </div>
        <div class="si-mode-toggle">
          <span class="si-mode-label${modeOffCls}">standard</span>
          <button id="statsViewModeToggle" class="si-mode-switch${modeSwitchCls}" type="button" role="switch" aria-checked="${statsExtendedMode ? "true" : "false"}" title="Toggle extended metrics view">
            <span class="si-mode-knob"></span>
          </button>
          <span class="si-mode-label${modeOnCls}">extended</span>
        </div>
      </div>
    </div>
  `;

  const chipsEl = el.querySelector(".si-policy-chips");
  if (chipsEl) {
    chipsEl.addEventListener("click", evt => {
      const btn = evt.target.closest("button[data-series-idx]");
      if (!btn) return;
      toggleSeriesSelection(btn.dataset.seriesIdx);
      renderStats();
    });
  }
  const modeToggleEl = document.getElementById("statsViewModeToggle");
  if (modeToggleEl) {
    modeToggleEl.addEventListener("click", () => {
      statsExtendedMode = !statsExtendedMode;
      renderStats();
    });
  }
}

function fmtSim(v, digits = 3) {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v ?? "");
  return n.toFixed(digits);
}

function fmtHoursLabel(hours) {
  const h = Number(hours);
  if (!Number.isFinite(h) || h < 0) return "—";
  const days = h / 24;
  if (days >= 2) return `${h.toFixed(1)}h (${days.toFixed(2)}d)`;
  return `${h.toFixed(1)}h`;
}

function classForDeltaPct(deltaPctAbs) {
  if (!Number.isFinite(deltaPctAbs)) return "";
  if (deltaPctAbs <= 10) return "sim-delta-good";
  if (deltaPctAbs <= 25) return "sim-delta-warn";
  return "sim-delta-bad";
}

function formatDeltaCell(measured, target, digits = 1) {
  const m = Number(measured);
  const t = Number(target);
  if (!Number.isFinite(m) || !Number.isFinite(t) || t === 0) {
    return `<span class="sim-delta-warn">—</span>`;
  }
  const deltaPct = ((m - t) / t) * 100;
  const cls = classForDeltaPct(Math.abs(deltaPct));
  const sign = deltaPct >= 0 ? "+" : "";
  return `<span class="${cls}">${sign}${deltaPct.toFixed(digits)}%</span>`;
}

function parsePeakWindowHours(arrivalProfile) {
  const out = new Set();
  const list = Array.isArray(arrivalProfile?.peak_window_hours_utc)
    ? arrivalProfile.peak_window_hours_utc
    : [];
  list.forEach(h => {
    const n = Number(h);
    if (Number.isFinite(n)) out.add(((n % 24) + 24) % 24);
  });
  return out;
}

function collectArrivalTicks(events, scenarioMeta) {
  // An arrival that fires appears both in scenarioMeta.arrivals (the schedule)
  // and in the tick event that emitted it. Counting both double-counts every
  // arrival and adds the scheduled ones that never happened, so prefer the
  // observed ticks and fall back to the schedule only for runs that emitted none.
  const observed = [];
  events.forEach(e => {
    if (e.event !== "tick" || !Array.isArray(e.arrivals)) return;
    const tick = Number(e.tick);
    if (!Number.isFinite(tick) || tick < 0) return;
    e.arrivals.forEach(() => observed.push(Math.floor(tick)));
  });
  if (observed.length) return observed;
  const fromMeta = Array.isArray(scenarioMeta?.arrivals) ? scenarioMeta.arrivals : [];
  const scheduled = [];
  fromMeta.forEach(a => {
    const t = Number(a?.tick);
    if (Number.isFinite(t) && t >= 0) scheduled.push(Math.floor(t));
  });
  return scheduled;
}

// Ticks as MM:SS, minutes allowed past 59 so the unit never changes mid-column.
function fmtTicksAsDuration(ticks, tickSeconds) {
  const secs = Math.round(Number(ticks) * (Number(tickSeconds) || 60));
  if (!Number.isFinite(secs)) return "—";
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function percentileFromSorted(sortedValues, percentileValue) {
  if (!sortedValues.length) return 0;
  const idx = Math.min(sortedValues.length - 1, Math.floor((sortedValues.length * percentileValue) / 100));
  return sortedValues[idx];
}

function computeHourlyTimeline({ events, scenarioMeta, arrivalProfile, tickSeconds, totalTicks }) {
  const ts = Math.max(1, Number(tickSeconds) || 60);
  const maxTickSeen = (events || []).reduce((mx, e) => {
    const t = Number(e?.tick);
    return Number.isFinite(t) ? Math.max(mx, t) : mx;
  }, 0);
  const baseTicks = Math.max(0, Number(totalTicks) || 0, maxTickSeen + 1);
  const tickDerivedHours = (baseTicks * ts) / 3600;
  const hintHours = Number(scenarioMeta?.scenario_metadata?.window_hours);
  const modeledHours = Number.isFinite(hintHours) && hintHours > 0
    ? hintHours
    : tickDerivedHours;
  // Bucket count is rounded to avoid +1 visual inflation from tick-zero indexing.
  const modeledHourBuckets = Math.max(1, Math.round(modeledHours));
  const mergesByHour = Array.from({ length: modeledHourBuckets }, () => 0);
  const arrivalsByHour = Array.from({ length: modeledHourBuckets }, () => 0);
  const mergeTicks = [];

  (events || []).forEach(e => {
    if (e.event !== "merge") return;
    const tick = Number(e.tick);
    if (!Number.isFinite(tick) || tick < 0) return;
    mergeTicks.push(Math.floor(tick));
    const hour = Math.min(
      modeledHourBuckets - 1,
      Math.max(0, Math.floor((tick * ts) / 3600)),
    );
    mergesByHour[hour] += 1;
  });

  collectArrivalTicks(events || [], scenarioMeta).forEach(tick => {
    const hour = Math.min(
      modeledHourBuckets - 1,
      Math.max(0, Math.floor((tick * ts) / 3600)),
    );
    arrivalsByHour[hour] += 1;
  });

  const startHour = Number(arrivalProfile?.scenario_start_hour);
  const baseHour = Number.isFinite(startHour) ? ((startHour % 24) + 24) % 24 : 0;
  const peakHours = parsePeakWindowHours(arrivalProfile);

  const rows = mergesByHour.map((m, i) => {
    const utcHour = (baseHour + i) % 24;
    return {
      hour_index: i,
      utc_hour: utcHour,
      merges: m,
      arrivals: arrivalsByHour[i] || 0,
      throughput_mph: m,
      is_peak: peakHours.has(utcHour),
    };
  });

  const activeRows = rows.filter(r => r.merges > 0);
  const peakRows = rows.filter(r => r.is_peak);
  const offPeakRows = rows.filter(r => !r.is_peak);

  const peakWindow = Math.min(8, rows.length);
  let bestPeak8Avg = 0;
  let bestPeak8Slice = rows.slice(0, peakWindow).map(r => r.merges);
  for (let i = 0; i <= rows.length - peakWindow; i++) {
    const slice = rows.slice(i, i + peakWindow).map(r => r.merges);
    const avg = slice.reduce((a, b) => a + b, 0) / Math.max(1, peakWindow);
    if (avg > bestPeak8Avg) {
      bestPeak8Avg = avg;
      bestPeak8Slice = slice;
    }
  }

  const sortedIntervals = mergeTicks
    .sort((a, b) => a - b)
    .slice(1)
    .map((tick, i) => (tick - mergeTicks[i]) * ts)
    .filter(v => Number.isFinite(v) && v > 0)
    .sort((a, b) => a - b);

  const totalMerges = rows.reduce((a, r) => a + r.merges, 0);
  const totalArrivals = rows.reduce((a, r) => a + r.arrivals, 0);

  return {
    modeled_hours: modeledHours,
    modeled_hour_buckets: modeledHourBuckets,
    modeled_ticks: baseTicks,
    total_merges: totalMerges,
    total_arrivals: totalArrivals,
    throughput_24h_mph: totalMerges / Math.max(1, modeledHours),
    throughput_active_mph: activeRows.length
      ? totalMerges / activeRows.length
      : 0,
    throughput_peak8_mph: bestPeak8Avg,
    throughput_peak8_p90_mph: percentileFromSorted(
      [...bestPeak8Slice].sort((a, b) => a - b),
      90,
    ),
    throughput_peak_window_mph: peakRows.length
      ? peakRows.reduce((a, r) => a + r.merges, 0) / peakRows.length
      : 0,
    throughput_offpeak_window_mph: offPeakRows.length
      ? offPeakRows.reduce((a, r) => a + r.merges, 0) / offPeakRows.length
      : 0,
    arrivals_peak_window_per_hour: peakRows.length
      ? peakRows.reduce((a, r) => a + r.arrivals, 0) / peakRows.length
      : 0,
    arrivals_offpeak_window_per_hour: offPeakRows.length
      ? offPeakRows.reduce((a, r) => a + r.arrivals, 0) / offPeakRows.length
      : 0,
    merge_interval_p50_seconds: percentileFromSorted(sortedIntervals, 50),
    merge_interval_p95_seconds: percentileFromSorted(sortedIntervals, 95),
    rows,
    peak_hours: peakHours,
  };
}

function buildPolicyExtended(fileData, scenarioMeta, arrivalProfile) {
  const m = fileData?.metrics || {};
  const tickSeconds = Number(m.tick_seconds) || Number(scenarioMeta.tick_seconds) || 60;
  const totalTicks = Number(m.total_time_ticks)
    || Number(scenarioMeta.total_time_ticks)
    || Number(scenarioMeta.scenario_metadata?.window_ticks)
    || 0;
  const timeline = computeHourlyTimeline({
    events: fileData?.events || [],
    scenarioMeta,
    arrivalProfile,
    tickSeconds,
    totalTicks,
  });
  const merges = Number(m.mrs_merged) || 0;
  const rebases = Number(m.rebase_calls) || 0;
  return {
    policy: fileData?.name || "unknown",
    tick_seconds: tickSeconds,
    total_ticks: Number(m.total_time_ticks) || timeline.modeled_ticks,
    modeled_hours: timeline.modeled_hours,
    merged: merges,
    rebases,
    rebase_per_merge: merges > 0 ? rebases / merges : 0,
    peak_pipelines: Number(m.peak) || Number(m.peak_active_pipelines) || 0,
    avg_ci_ticks: Number(m.ci_avg) || 0,
    throughput_24h_mph: timeline.throughput_24h_mph,
    throughput_active_mph: timeline.throughput_active_mph,
    throughput_peak8_mph: timeline.throughput_peak8_mph,
    throughput_peak8_p90_mph: timeline.throughput_peak8_p90_mph,
    throughput_peak_window_mph: timeline.throughput_peak_window_mph,
    throughput_offpeak_window_mph: timeline.throughput_offpeak_window_mph,
    arrivals_peak_window_per_hour: timeline.arrivals_peak_window_per_hour,
    arrivals_offpeak_window_per_hour: timeline.arrivals_offpeak_window_per_hour,
    merge_interval_p50_seconds: timeline.merge_interval_p50_seconds,
    merge_interval_p95_seconds: timeline.merge_interval_p95_seconds,
    total_arrivals: timeline.total_arrivals,
    hourly: timeline.rows,
  };
}

function renderSimulationTab() {
  const scenarioView = document.getElementById("simScenarioView");
  const calibrationView = document.getElementById("simCalibrationView");
  const metadataView = document.getElementById("simMetadataView");
  if (!scenarioView) return;

  const noData = !filesData.length && !loadedScenarioDocs.length && !Object.keys(loadedRunMetadataByCategory).length;
  if (noData) {
    scenarioView.innerHTML = `<div class="sim-card full sim-empty">Load NDJSON traces or a scenario YAML to view simulation parameters.</div>`;
    if (calibrationView) calibrationView.innerHTML = "";
    if (metadataView) metadataView.innerHTML = "";
    return;
  }

  // Resolve sub-tab visibility
  const subtabBar = document.getElementById("simSubtabBar");
  if (subtabBar) subtabBar.querySelectorAll(".stats-subtab").forEach(b => b.classList.toggle("active", b.dataset.simtab === simSubtab));
  if (scenarioView) scenarioView.style.display = simSubtab === "scenario" ? "" : "none";
  if (calibrationView) calibrationView.style.display = simSubtab === "calibration" ? "" : "none";
  if (metadataView) metadataView.style.display = simSubtab === "metadata" ? "" : "none";

  const simCtx = _getSimContext();

  if (simSubtab === "scenario") _renderSimScenario(scenarioView, simCtx);
  else {
    _destroySimCharts();
    if (simSubtab === "calibration") _renderSimCalibration(calibrationView, simCtx);
    else if (simSubtab === "metadata") _renderSimMetadata(metadataView, simCtx);
  }
}

function _getSimContext() {
  const ownerFile = filesData[0] || null;
  const scenarioDocEntry = ownerFile
    ? resolveScenarioDocForTrace(ownerFile.events || [], ownerFile.name)
    : (loadedScenarioDocs[0] || null);
  const scenarioMeta = ownerFile
    ? ((ownerFile.events || []).find(e => e.event === "scenario_meta") || {})
    : (scenarioDocEntry ? scenarioMetaFromYaml(scenarioDocEntry.doc, scenarioDocEntry.fileName) : {});

  const rawScenario = scenarioDocEntry?.doc || {};
  const embeddedScenarioMetadata = scenarioMeta.scenario_metadata || {};
  const cal = embeddedScenarioMetadata.calibration_targets || {};
  const perfDims = cal.performance_dimensions || {};
  const arrivalProfile = cal.arrival_profile || {};
  const opFail = scenarioMeta.operation_failure_rate || {};
  const tickSeconds = Number(scenarioMeta.tick_seconds) || Number(embeddedScenarioMetadata.tick_seconds) || 60;
  const modeledWindowTicks = Number(embeddedScenarioMetadata.window_ticks) || Number(scenarioMeta.total_time_ticks) || Number(ownerFile?.metrics?.total_time_ticks) || 0;
  const mrCatalog = scenarioMeta.mr_catalog && typeof scenarioMeta.mr_catalog === "object" ? scenarioMeta.mr_catalog : {};
  const catalogEntries = Object.values(mrCatalog).filter(x => x && typeof x === "object");
  const rawMrs = Array.isArray(rawScenario.merge_requests) ? rawScenario.merge_requests : [];
  const initialOpenCount = rawMrs.filter(mr => Number(mr?.arrival_tick) <= 0).length;
  const arrivalCount = Array.isArray(scenarioMeta.arrivals) ? scenarioMeta.arrivals.length : 0;
  const limit = Number(loadedRunMetadataByCategory?.comparisons?.context?.limit
    || loadedRunMetadataByCategory?.calibration?.context?.limit
    || embeddedScenarioMetadata.limit) || null;

  const timelineBase = computeHourlyTimeline({
    events: ownerFile?.events || [],
    scenarioMeta,
    arrivalProfile,
    tickSeconds,
    totalTicks: modeledWindowTicks,
  });

  return {
    ownerFile, scenarioDocEntry, scenarioMeta, rawScenario,
    embeddedScenarioMetadata, cal, perfDims, arrivalProfile, opFail,
    tickSeconds, modeledWindowTicks, mrCatalog, catalogEntries,
    rawMrs, initialOpenCount, arrivalCount, limit, timelineBase,
  };
}

function _simFmtDuration(ticks, tickSeconds) {
  if (timeDisplayMode === "ticks") return Number.isInteger(ticks) ? `${ticks} ticks` : `${ticks.toFixed(1)} ticks`;
  const secs = ticks * tickSeconds;
  if (secs < 120) return `${secs.toFixed(0)}s`;
  return `${(secs / 60).toFixed(1)}m`;
}

let _simCiChart = null, _simPriorityChart = null, _simTenantChart = null, _simTimelineChart = null;

function _destroySimCharts() {
  mqDestroyChartIfPresent(_simCiChart); _simCiChart = null;
  mqDestroyChartIfPresent(_simPriorityChart); _simPriorityChart = null;
  mqDestroyChartIfPresent(_simTenantChart); _simTenantChart = null;
  mqDestroyChartIfPresent(_simTimelineChart); _simTimelineChart = null;
}

function _renderSimScenario(el, ctx) {
  _destroySimCharts();
  const { scenarioDocEntry, scenarioMeta, embeddedScenarioMetadata, arrivalProfile, opFail, tickSeconds, modeledWindowTicks, catalogEntries, initialOpenCount, arrivalCount, limit, timelineBase, ownerFile } = ctx;

  const scenarioPeakHours = Array.isArray(arrivalProfile.peak_window_hours_utc) ? arrivalProfile.peak_window_hours_utc : [];
  const scenarioFile = scenarioDocEntry?.fileName || scenarioMeta.scenario_source_file || "embedded only";
  const shortFile = scenarioFile.split("/").pop();
  const calibrationName = embeddedScenarioMetadata.name
    || loadedRunMetadataByCategory?.comparisons?.custom_metadata?.calibration
    || loadedRunMetadataByCategory?.calibration?.custom_metadata?.purpose
    || "—";

  const forceCount = Array.isArray(scenarioMeta.force_merges) ? scenarioMeta.force_merges.length : 0;
  const cancelCount = Array.isArray(scenarioMeta.cancellations) ? scenarioMeta.cancellations.length : 0;

  const pdMin = scenarioMeta.pipeline_duration?.min;
  const pdMax = scenarioMeta.pipeline_duration?.max;
  const pdFmt = pdMin != null ? `${_simFmtDuration(pdMin, tickSeconds)} – ${_simFmtDuration(pdMax, tickSeconds)}` : "—";

  const failRate = Number(scenarioMeta.failure_rate) || 0;
  const mergeFailRate = Number(opFail.merge) || 0;
  const rebaseFailRate = Number(opFail.rebase) || 0;

  const sections = [];

  // Section 1: Scenario Details
  sections.push(`<div class="sim-section">
    <div class="sim-section-title">Scenario Details</div>
    <div class="sim-card full">
      <div class="sim-kv">
        <span class="k">Scenario file</span><span class="v">${mqEscapeHtml(shortFile)}</span>
        <span class="k">Calibration</span><span class="v">${mqEscapeHtml(calibrationName)}</span>
        <span class="k">Total MRs</span><span class="v">${mqEscapeHtml(String(scenarioMeta.total_mrs ?? "—"))}</span>
        <span class="k">Initial open MRs</span><span class="v">${initialOpenCount || "—"}</span>
        <span class="k">Dynamic arrivals</span><span class="v">${arrivalCount}</span>
        ${limit ? `<span class="k">Limit (concurrency cap)</span><span class="v">${limit}</span>` : ""}
        <span class="k">Scenario window</span><span class="v">${mqEscapeHtml(fmtHoursLabel(timelineBase.modeled_hours))} (${modeledWindowTicks} ticks × ${tickSeconds}s)</span>
        <span class="k">Pipeline duration</span><span class="v">${mqEscapeHtml(pdFmt)}</span>
        <span class="k">Pipeline failure rate</span><span class="v">${(failRate * 100).toFixed(2)}%</span>
        <span class="k">Operation failure rate</span><span class="v">merge ${(mergeFailRate * 100).toFixed(3)}% / rebase ${(rebaseFailRate * 100).toFixed(3)}%</span>
        <span class="k">Force-merges / cancellations</span><span class="v">${forceCount} / ${cancelCount}</span>
        <span class="k">Scenario start (UTC)</span><span class="v">${mqEscapeHtml(String(arrivalProfile.scenario_start_hour ?? "0"))}:00 ${mqEscapeHtml(arrivalProfile.scenario_start_weekday || "")}</span>
        <span class="k">Peak window (UTC)</span><span class="v">${scenarioPeakHours.length ? scenarioPeakHours.join(", ") : "—"}</span>
      </div>
    </div>
  </div>`);

  // Section 2: CI Pipeline
  const ciData = _getCiDistributionData(ownerFile, tickSeconds);
  if (ciData) {
    const bucketRows = ciData.buckets.map(b =>
      `<tr><td>${b.label}</td><td>${b.count}</td><td>${b.pct}%</td></tr>`
    ).join("");
    sections.push(`<div class="sim-section">
      <div class="sim-section-title">CI Pipeline${ownerFile ? ` <span style="color:var(--muted);font-weight:400">(observed in ${mqEscapeHtml(ownerFile.name)}${filesData.length > 1 ? `, 1 of ${filesData.length} loaded runs` : ""})</span>` : ""}</div>
      <div class="sim-card full">
        <div class="sim-split">
          <div class="sim-kv">
            <span class="k">Pipelines observed</span><span class="v">${ciData.total}</span>
            <span class="k">Min</span><span class="v">${ciData.fmtMin}</span>
            <span class="k">Median</span><span class="v">${ciData.fmtMedian}</span>
            <span class="k">Mean</span><span class="v">${ciData.fmtMean}</span>
            <span class="k">P95</span><span class="v">${ciData.fmtP95}</span>
            <span class="k">Max</span><span class="v">${ciData.fmtMax}</span>
          </div>
          <div class="sim-table-wrap"><table class="sim-table dense">
            <thead><tr><th>Duration</th><th>Count</th><th>%</th></tr></thead>
            <tbody>${bucketRows}</tbody>
          </table></div>
        </div>
        <div style="height:180px;margin-top:0.6rem"><canvas id="simCiChart"></canvas></div>
      </div>
    </div>`);
  }

  // Section 3: MR Labels
  const priorityCounts = {};
  const serviceCounts = {};
  catalogEntries.forEach(entry => {
    const p = String(entry.priority_label || "").trim() || "none";
    priorityCounts[p] = (priorityCounts[p] || 0) + 1;
    (Array.isArray(entry.service_labels) ? entry.service_labels : []).forEach(lbl => {
      const key = String(lbl).trim();
      if (key) serviceCounts[key] = (serviceCounts[key] || 0) + 1;
    });
  });

  if (Object.keys(priorityCounts).length || Object.keys(serviceCounts).length) {
    const prioritySorted = Object.entries(priorityCounts).sort((a, b) => b[1] - a[1]);
    const serviceSorted = Object.entries(serviceCounts).sort((a, b) => b[1] - a[1]);
    const priorityRows = prioritySorted.map(([k, v]) => `<tr><td>${mqEscapeHtml(k)}</td><td>${v}</td></tr>`).join("");
    const serviceRows = serviceSorted.map(([k, v]) => `<tr><td>${mqEscapeHtml(k)}</td><td>${v}</td></tr>`).join("");
    sections.push(`<div class="sim-section">
      <div class="sim-section-title">MR Labels</div>
      <div class="sim-card full">
        <div class="sim-split">
          <div class="sim-table-wrap"><table class="sim-table dense"><thead><tr><th>Priority</th><th>Count</th></tr></thead><tbody>${priorityRows}</tbody></table></div>
          <div class="sim-table-wrap"><table class="sim-table dense"><thead><tr><th>Tenant</th><th>Count</th></tr></thead><tbody>${serviceRows}</tbody></table></div>
        </div>
        <div class="sim-split" style="margin-top:0.6rem">
          <div style="height:160px"><canvas id="simPriorityChart"></canvas></div>
          <div>
            <div style="height:160px"><canvas id="simTenantChart"></canvas></div>
            ${serviceSorted.length > 12 ? `<div class="sim-subtle" style="text-align:center">chart shows the top 12 of ${serviceSorted.length}; the table lists all</div>` : ""}
          </div>
        </div>
      </div>
    </div>`);
  }

  // Section 4: Hourly Merge Queue Timeline
  const hourlyRows = timelineBase.rows.map(r => `<tr${r.is_peak ? ' style="background:rgba(88,166,255,0.04)"' : ""}>
    <td>h${r.hour_index}</td><td>${String(r.utc_hour).padStart(2, "0")}:00</td>
    <td>${r.is_peak ? "peak" : "off-peak"}</td><td>${r.arrivals}</td><td>${r.merges}</td><td>${fmtSim(r.throughput_mph, 3)}</td>
  </tr>`).join("");
  if (hourlyRows) {
    sections.push(`<div class="sim-section">
      <div class="sim-section-title">Hourly Merge Queue Timeline</div>
      <div class="sim-card full">
        <div style="height:200px;margin-bottom:0.6rem"><canvas id="simTimelineChart"></canvas></div>
        <div class="sim-table-wrap no-max"><table class="sim-table dense">
          <thead><tr><th>Hour</th><th>UTC</th><th>Window</th><th>Arrivals</th><th>Merges</th><th>Throughput/h</th></tr></thead>
          <tbody>${hourlyRows}</tbody>
        </table></div>
      </div>
    </div>`);
  }

  el.innerHTML = sections.join("");

  // Render charts after DOM insertion
  _renderSimScenarioCharts(ctx, ciData);
}

function _getCiDistributionData(ownerFile, tickSeconds) {
  if (!ownerFile || !ownerFile.packed || !ownerFile.packed.segs) return null;
  const ciTicks = ownerFile.packed.segs.filter(s => s.kind === "running").map(s => s.end - s.start);
  if (!ciTicks.length) return null;

  ciTicks.sort((a, b) => a - b);
  const total = ciTicks.length;
  const minT = ciTicks[0], maxT = ciTicks[total - 1];
  const mean = ciTicks.reduce((a, b) => a + b, 0) / total;
  const mid = Math.floor(total / 2);
  const median = total % 2 ? ciTicks[mid] : (ciTicks[mid - 1] + ciTicks[mid]) / 2;
  const p95 = ciTicks[Math.floor(total * 0.95)];

  const bucketSize = Math.max(1, Math.ceil((maxT - minT + 1) / 8));
  const buckets = [];
  for (let start = minT; start <= maxT; start += bucketSize) {
    const end = Math.min(start + bucketSize - 1, maxT);
    const count = ciTicks.filter(t => t >= start && t <= end).length;
    const pct = (count / total * 100).toFixed(1);
    const label = `${_simFmtDuration(start, tickSeconds)} – ${_simFmtDuration(end, tickSeconds)}`;
    buckets.push({ start, end, count, pct, label });
  }

  return {
    total, minT, maxT, mean, median, p95, buckets, tickSeconds,
    fmtMin: _simFmtDuration(minT, tickSeconds),
    fmtMedian: _simFmtDuration(median, tickSeconds),
    fmtMean: _simFmtDuration(mean, tickSeconds),
    fmtP95: _simFmtDuration(p95, tickSeconds),
    fmtMax: _simFmtDuration(maxT, tickSeconds),
  };
}

function _renderSimScenarioCharts(ctx, ciData) {
  const chartDefaults = {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { color: "#8b949e", font: { size: 10 } }, grid: { color: "#21262d" } },
      y: { ticks: { color: "#8b949e", font: { size: 10 } }, grid: { color: "#21262d" }, beginAtZero: true },
    },
  };

  // CI Pipeline bar chart
  if (ciData) {
    const canvas = document.getElementById("simCiChart");
    if (canvas) {
      _simCiChart = new Chart(canvas, {
        type: "bar",
        data: {
          labels: ciData.buckets.map(b => b.label),
          datasets: [{ data: ciData.buckets.map(b => b.count), backgroundColor: "rgba(88,166,255,0.6)", borderColor: "rgba(88,166,255,0.9)", borderWidth: 1 }],
        },
        options: { ...chartDefaults, plugins: { ...chartDefaults.plugins, tooltip: { callbacks: { label: tip => `${tip.raw} pipelines (${(tip.raw / ciData.total * 100).toFixed(1)}%)` } } } },
      });
    }
  }

  // Priority label chart
  const { catalogEntries } = ctx;
  const priorityCounts = {};
  const serviceCounts = {};
  catalogEntries.forEach(entry => {
    const p = String(entry.priority_label || "").trim() || "none";
    priorityCounts[p] = (priorityCounts[p] || 0) + 1;
    (Array.isArray(entry.service_labels) ? entry.service_labels : []).forEach(lbl => {
      const key = String(lbl).trim();
      if (key) serviceCounts[key] = (serviceCounts[key] || 0) + 1;
    });
  });

  const prioritySorted = Object.entries(priorityCounts).sort((a, b) => b[1] - a[1]);
  const serviceAll = Object.entries(serviceCounts).sort((a, b) => b[1] - a[1]);
  const SERVICE_CHART_LIMIT = 12;
  const serviceSorted = serviceAll.slice(0, SERVICE_CHART_LIMIT);
  const serviceTruncated = serviceAll.length > SERVICE_CHART_LIMIT;

  const prioCanvas = document.getElementById("simPriorityChart");
  if (prioCanvas && prioritySorted.length) {
    _simPriorityChart = new Chart(prioCanvas, {
      type: "bar",
      data: {
        labels: prioritySorted.map(([k]) => k.replace("bot/approved: ", "").replace("bot/approved", "default")),
        datasets: [{ data: prioritySorted.map(([, v]) => v), backgroundColor: "rgba(163,113,247,0.6)", borderColor: "rgba(163,113,247,0.9)", borderWidth: 1 }],
      },
      options: chartDefaults,
    });
  }

  const tenantCanvas = document.getElementById("simTenantChart");
  if (tenantCanvas && serviceSorted.length) {
    _simTenantChart = new Chart(tenantCanvas, {
      type: "bar",
      data: {
        labels: serviceSorted.map(([k]) => k.replace("tenant-", "")),
        datasets: [{ data: serviceSorted.map(([, v]) => v), backgroundColor: "rgba(210,153,34,0.6)", borderColor: "rgba(210,153,34,0.9)", borderWidth: 1 }],
      },
      options: chartDefaults,
    });
  }

  // Hourly timeline chart
  const { timelineBase } = ctx;
  const timelineCanvas = document.getElementById("simTimelineChart");
  if (timelineCanvas && timelineBase.rows.length) {
    const rows = timelineBase.rows;
    const peakHours = new Set(Array.isArray(ctx.arrivalProfile.peak_window_hours_utc) ? ctx.arrivalProfile.peak_window_hours_utc : []);
    _simTimelineChart = new Chart(timelineCanvas, {
      type: "bar",
      data: {
        labels: rows.map(r => `${String(r.utc_hour).padStart(2, "0")}:00`),
        datasets: [
          {
            label: "Arrivals",
            data: rows.map(r => r.arrivals),
            backgroundColor: rows.map(r => r.is_peak ? "rgba(88,166,255,0.5)" : "rgba(88,166,255,0.25)"),
            borderColor: "rgba(88,166,255,0.8)",
            borderWidth: 1,
          },
          {
            label: "Merges",
            data: rows.map(r => r.merges),
            backgroundColor: rows.map(r => r.is_peak ? "rgba(46,160,67,0.6)" : "rgba(46,160,67,0.3)"),
            borderColor: "rgba(46,160,67,0.8)",
            borderWidth: 1,
          },
        ],
      },
      options: {
        ...chartDefaults,
        plugins: { legend: { display: true, position: "top", labels: { color: "#e6edf3", font: { size: 10 }, boxWidth: 10, padding: 8 } } },
      },
    });
  }
}

function _renderSimCalibration(el, ctx) {
  const { scenarioMeta, embeddedScenarioMetadata, perfDims, arrivalProfile } = ctx;
  const sections = [];

  const calibrationName = embeddedScenarioMetadata.name
    || loadedRunMetadataByCategory?.comparisons?.custom_metadata?.calibration
    || loadedRunMetadataByCategory?.calibration?.custom_metadata?.purpose
    || "—";
  const calibratedPolicy = embeddedScenarioMetadata.calibration_targets?.calibrated_policy
    || loadedRunMetadataByCategory?.calibration?.context?.policy
    || "old-burst";

  // Determine which policy to use as primary for calibration comparison
  const policyExtended = filesData.map(d => buildPolicyExtended(d, scenarioMeta, arrivalProfile));
  const calibratedPolicyData = policyExtended.find(p => p.policy === calibratedPolicy) || policyExtended[0];

  // Calibration overview
  const calMeta = loadedRunMetadataByCategory?.calibration || {};
  const calCtx = calMeta.context || {};
  const calCm = calMeta.custom_metadata || {};
  sections.push(`<div class="sim-section">
    <div class="sim-section-title calibration">Calibration Overview</div>
    <div class="sim-card full">
      <div class="sim-kv">
        <span class="k">Calibration name</span><span class="v">${mqEscapeHtml(calibrationName)}</span>
        <span class="k">Calibrated policy</span><span class="v">${mqEscapeHtml(calibratedPolicy)}</span>
        ${calCtx.target_anchor ? `<span class="k">Target anchor</span><span class="v">${mqEscapeHtml(calCtx.target_anchor)}</span>` : ""}
        ${calCtx.dimension_profile ? `<span class="k">Dimension profile</span><span class="v">${mqEscapeHtml(calCtx.dimension_profile)}</span>` : ""}
        ${calCtx.rank_score ? `<span class="k">Scoring method</span><span class="v">${mqEscapeHtml(calCtx.rank_score)}</span>` : ""}
        ${calCm.ci_model ? `<span class="k">CI model</span><span class="v">${mqEscapeHtml(calCm.ci_model)}</span>` : ""}
        ${calCtx.cycle_scaling ? `<span class="k">Cycle scaling</span><span class="v">${mqEscapeHtml(calCtx.cycle_scaling)}</span>` : ""}
        ${calCtx.tune_cycles ? `<span class="k">Tune cycles</span><span class="v">${calCtx.tune_cycles}</span>` : ""}
        ${calCtx.validate_cycles ? `<span class="k">Validate cycles</span><span class="v">${calCtx.validate_cycles}</span>` : ""}
      </div>
    </div>
  </div>`);

  // Target vs Measured
  if (Object.keys(perfDims).length && calibratedPolicyData) {
    const targetRows = [
      ["merge_per_hour_24h", calibratedPolicyData.throughput_24h_mph],
      ["merge_per_hour_active_hours", calibratedPolicyData.throughput_active_mph],
      ["merge_per_hour_peak8", calibratedPolicyData.throughput_peak8_mph],
      ["merge_per_hour_peak8_p90", calibratedPolicyData.throughput_peak8_p90_mph],
      ["rebase_per_merge_global", calibratedPolicyData.rebase_per_merge],
      ["merge_interval_seconds_p50", calibratedPolicyData.merge_interval_p50_seconds],
      ["merge_interval_seconds_p95", calibratedPolicyData.merge_interval_p95_seconds],
      ["merge_peak_offpeak_ratio", calibratedPolicyData.throughput_peak_window_mph && calibratedPolicyData.throughput_offpeak_window_mph
        ? calibratedPolicyData.throughput_peak_window_mph / calibratedPolicyData.throughput_offpeak_window_mph : null],
    ].filter(([k, v]) => Number.isFinite(Number(perfDims[k])) && v != null)
     .map(([key, measured]) => {
      const target = Number(perfDims[key]);
      return `<tr><td class="metric-name">${mqEscapeHtml(key)}</td><td>${fmtSim(target, 4)}</td><td>${fmtSim(measured, 4)}</td><td>${formatDeltaCell(measured, target, 1)}</td></tr>`;
    }).join("");
    if (targetRows) {
      sections.push(`<div class="sim-section">
        <div class="sim-section-title calibration">Calibration target vs this run</div>
        <div class="sim-card full">
          <div class="sim-subtle">Calibrated policy: <strong>${mqEscapeHtml(calibratedPolicy)}</strong> — comparing runtime measurements against calibration targets</div>
          <div class="sim-table-wrap"><table class="sim-table dense">
            <thead><tr><th>Dimension</th><th>Calibration target</th><th>This run</th><th>Delta</th></tr></thead>
            <tbody>${targetRows}</tbody>
          </table></div>
        </div>
      </div>`);
    }
  }

  // Per-Policy Runtime Metrics
  if (policyExtended.length) {
    const peakTarget = Number(perfDims.merge_per_hour_peak8);
    const offPeakTarget = Number(perfDims.merge_per_hour_24h);
    const runtimeRows = policyExtended.map(p => `<tr>
      <td class="sticky-col">${mqEscapeHtml(p.policy)}</td>
      <td>${mqEscapeHtml(fmtHoursLabel(p.modeled_hours))}</td>
      <td>${fmtSim(p.throughput_24h_mph, 3)}</td>
      <td>${fmtSim(p.throughput_active_mph, 3)}</td>
      <td>${fmtSim(p.throughput_peak8_mph, 3)}</td>
      <td>${fmtSim(p.throughput_peak_window_mph, 3)} ${Number.isFinite(peakTarget) ? `<span class="sim-badge peak">${formatDeltaCell(p.throughput_peak_window_mph, peakTarget, 1)}</span>` : ""}</td>
      <td>${fmtSim(p.throughput_offpeak_window_mph, 3)} ${Number.isFinite(offPeakTarget) ? `<span class="sim-badge offpeak">${formatDeltaCell(p.throughput_offpeak_window_mph, offPeakTarget, 1)}</span>` : ""}</td>
      <td>${fmtSim(p.rebases, 0)}</td>
      <td>${fmtSim(p.rebase_per_merge, 3)}</td>
      <td>${fmtSim(p.merge_interval_p95_seconds, 1)}</td>
      <td>${fmtSim(p.merged, 0)}</td>
      <td>${fmtSim(p.total_arrivals, 0)}</td>
    </tr>`).join("");
    sections.push(`<div class="sim-section">
      <div class="sim-section-title runtime">Per-Policy Runtime</div>
      <div class="sim-card full"><div class="sim-table-wrap"><table class="sim-table dense">
        <thead><tr>
          <th class="sticky-col">Policy</th><th>Run window</th><th>24h mph</th><th>Active mph</th><th>Peak8 mph</th>
          <th>Peak avg</th><th>Off-peak avg</th><th>Rebases</th><th>Rebase/Merge</th><th>Merge p95 (s)</th><th>Merged</th><th>Arrivals</th>
        </tr></thead>
        <tbody>${runtimeRows}</tbody>
      </table></div></div>
    </div>`);
  }

  // Dimension weights (if available from calibration metadata)
  const dimWeights = calCtx.standard_dimension_weights;
  if (dimWeights && typeof dimWeights === "object") {
    const weightRows = Object.entries(dimWeights)
      .sort((a, b) => Number(b[1]) - Number(a[1]))
      .map(([k, v]) => `<tr><td class="metric-name">${mqEscapeHtml(k)}</td><td>${(Number(v) * 100).toFixed(0)}%</td></tr>`)
      .join("");
    sections.push(`<div class="sim-section">
      <div class="sim-section-title calibration">Dimension Weights</div>
      <div class="sim-card full"><div class="sim-table-wrap"><table class="sim-table dense">
        <thead><tr><th>Dimension</th><th>Weight</th></tr></thead>
        <tbody>${weightRows}</tbody>
      </table></div></div>
    </div>`);
  }

  // Decision gates (if available)
  const gates = calCtx.decision_gates || calMeta.decision?.gates;
  const thresholds = calCtx.decision_thresholds;
  if (gates || thresholds) {
    let gateHtml = `<div class="sim-kv">`;
    if (gates) {
      Object.entries(gates).forEach(([k, v]) => {
        const passed = v === "true" || v === true;
        gateHtml += `<span class="k">${mqEscapeHtml(k)}</span><span class="v" style="color:${passed ? "var(--green)" : "var(--red)"}">${passed ? "✓ pass" : "✗ fail"}</span>`;
      });
    }
    gateHtml += `</div>`;
    if (thresholds) {
      // These are the configured limits the gates were judged against, not
      // results. Rendered in the same list they read as more outcomes.
      gateHtml += `<div class="sim-subtle" style="margin-top:0.5rem">Thresholds these were judged against</div><div class="sim-kv">`;
      Object.entries(thresholds).forEach(([k, v]) => {
        gateHtml += `<span class="k">${mqEscapeHtml(k)}</span><span class="v">${mqEscapeHtml(String(v))}</span>`;
      });
      gateHtml += `</div>`;
    }
    sections.push(`<div class="sim-section">
      <div class="sim-section-title calibration">Decision Gates</div>
      <div class="sim-card full">${gateHtml}</div>
    </div>`);
  }

  if (!sections.length) {
    el.innerHTML = `<div class="sim-card full sim-empty">No calibration data available. Load a calibration run or scenario with embedded calibration targets.</div>`;
    return;
  }
  el.innerHTML = sections.join("");
}

function _renderSimMetadata(el, ctx) {
  const categories = Object.keys(loadedRunMetadataByCategory);
  if (!categories.length) {
    el.innerHTML = `<div class="sim-card full sim-empty">No run metadata loaded. Load a run from the browser to see metadata.</div>`;
    return;
  }

  let html = "";
  categories.sort().forEach(cat => {
    const meta = loadedRunMetadataByCategory[cat] || {};
    const flat = flattenMetadata(meta);
    const entries = Object.entries(flat).sort((a, b) => a[0].localeCompare(b[0]));

    html += `<div class="sim-section">
      <div class="sim-section-title">${mqEscapeHtml(cat)}</div>
      <div class="sim-card full">
        <div class="sim-table-wrap no-max"><table class="sim-table dense">
          <thead><tr><th style="width:40%">Key</th><th>Value</th></tr></thead>
          <tbody>${entries.map(([k, v]) => `<tr class="sim-meta-row" data-key="${mqEscapeHtml(k)}" data-value="${mqEscapeHtml(String(v))}">
            <td class="metric-name">${mqEscapeHtml(k)}</td>
            <td>${mqEscapeHtml(String(v))}</td>
          </tr>`).join("")}</tbody>
        </table></div>
      </div>
    </div>`;
  });

  el.innerHTML = html;

  el.querySelectorAll(".sim-meta-row").forEach(row => {
    row.addEventListener("click", () => {
      const key = row.dataset.key;
      const value = row.dataset.value;
      const searchBox = document.getElementById("runSearchBox");
      if (searchBox) {
        searchBox.value = `${key}=${value}`;
        searchBox.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });
  });
}

// Sim subtab click handler
(function initSimSubtabs() {
  const bar = document.getElementById("simSubtabBar");
  if (!bar) return;
  bar.addEventListener("click", e => {
    const btn = e.target.closest(".stats-subtab");
    if (!btn || !btn.dataset.simtab) return;
    simSubtab = btn.dataset.simtab;
    renderSimulationTab();
  });
})();


function renderStats() {
  if (!filesData.length && !perLabelData) return;
  if (!filesData.length && perLabelData) statsSubtab = "perlabel";

  const subtabBar = document.getElementById("statsSubtabBar");
  if (subtabBar) {
    subtabBar.style.display = perLabelData ? "flex" : "none";
    subtabBar.querySelectorAll(".stats-subtab").forEach(b => b.classList.toggle("active", b.dataset.subtab === statsSubtab));
  }

  const metricsView = document.getElementById("statsMetricsView");
  const perLabelView = document.getElementById("statsPerLabelView");
  if (metricsView) metricsView.style.display = statsSubtab === "metrics" ? "" : "none";
  if (perLabelView) perLabelView.style.display = statsSubtab === "perlabel" ? "" : "none";

  if (statsSubtab === "metrics" && filesData.length) {
    renderScenarioInfoCard();
    renderVerdictBand();
    renderMetricPicker();
    const metrics = filesData.map(d => d.metrics);
    renderHeroCards(metrics);
    renderStatsExtended(metrics);
    renderCmpTable();
  } else if (statsSubtab === "perlabel") {
    if (filesData.length) renderScenarioInfoCard();
    renderPerLabelView();
  }
}

(function initStatsSubtabs() {
  const bar = document.getElementById("statsSubtabBar");
  if (!bar) return;
  bar.addEventListener("click", e => {
    const btn = e.target.closest(".stats-subtab");
    if (!btn) return;
    statsSubtab = btn.dataset.subtab;
    renderStats();
  });
})();

function renderPerLabelView() {
  if (!perLabelData) return;

  const selectedNames = _getVisiblePerLabelPolicies();
  const policies = Object.keys(perLabelData).filter(p => selectedNames.has(p));
  if (!policies.length) {
    const table = document.getElementById("perLabelTable");
    if (table) table.innerHTML = "<tr><td style='color:var(--muted);padding:1rem'>No policies selected</td></tr>";
    return;
  }

  const LABEL_ORDER = ["critical", "high", "medium", "low", "none", "lgtm"];
  const allLabels = new Set();
  policies.forEach(p => Object.keys(perLabelData[p]).forEach(l => allLabels.add(l)));
  const labels = LABEL_ORDER.filter(l => allLabels.has(l));

  const COLORS = { "active-cap": "#58a6ff", "cap+phase1": "#a371f7", "top-k": "#d29922", "old-burst": "#f85149" };
  const fallbackColors = ["#2ea043", "#8b949e", "#da3633", "#79c0ff"];

  const ctx0 = getTimeContext(0);
  const tickSec = ctx0.tickSeconds || 60;  // match every other fallback in this file
  const isTicks = timeDisplayMode === "ticks";
  const yLabel = isTicks ? "Ticks" : "Minutes";
  const yFmt = v => isTicks ? Math.round(v / tickSec) + "" : (v / 60).toFixed(0) + "m";
  const tipFmt = v => isTicks ? Math.round(v / tickSec) + " ticks" : (v / 60).toFixed(1) + "m";

  const chartOpts = {
    responsive: true, maintainAspectRatio: false,
    plugins: {
      legend: { position: "top", labels: { color: "#e6edf3", font: { size: 11 }, boxWidth: 12, padding: 10 } },
      tooltip: { callbacks: { label: tip => `${tip.dataset.label}: ${tipFmt(tip.raw)}` } },
    },
    scales: {
      x: { ticks: { color: "#8b949e" }, grid: { color: "#21262d" } },
      y: { ticks: { color: "#8b949e", callback: yFmt }, grid: { color: "#21262d" }, title: { display: true, text: yLabel, color: "#8b949e" } },
    },
  };

  const medianSets = policies.map((p, pi) => ({
    label: p,
    data: labels.map(l => (perLabelData[p][l] || {}).median_seconds || 0),
    backgroundColor: COLORS[p] || fallbackColors[pi % fallbackColors.length],
    borderColor: COLORS[p] || fallbackColors[pi % fallbackColors.length],
    borderWidth: 1,
  }));

  mqDestroyChartIfPresent(perLabelChart);
  const ctx = document.getElementById("perLabelCanvas");
  if (ctx) {
    perLabelChart = new Chart(ctx, {
      type: "bar",
      data: { labels: labels.map(l => l.charAt(0).toUpperCase() + l.slice(1)), datasets: medianSets },
      options: chartOpts,
    });
  }

  const p95Sets = policies.map((p, pi) => ({
    label: p,
    data: labels.map(l => (perLabelData[p][l] || {}).p95_seconds || 0),
    backgroundColor: COLORS[p] || fallbackColors[pi % fallbackColors.length],
    borderColor: COLORS[p] || fallbackColors[pi % fallbackColors.length],
    borderWidth: 1,
  }));

  mqDestroyChartIfPresent(perLabelP95Chart);
  const p95Ctx = document.getElementById("perLabelP95Canvas");
  if (p95Ctx) {
    perLabelP95Chart = new Chart(p95Ctx, {
      type: "bar",
      data: { labels: labels.map(l => l.charAt(0).toUpperCase() + l.slice(1)), datasets: p95Sets },
      options: chartOpts,
    });
  }

  const ciMedianSeconds = _getSimCiMedianSeconds();
  const ciLabel = isTicks ? Math.round(ciMedianSeconds / tickSec) + " ticks" : (ciMedianSeconds / 60).toFixed(0) + "m";
  const fmtMin = v => v == null ? "—" : isTicks ? Math.round(v / tickSec) + " ticks" : (v / 60).toFixed(1) + "m";

  const table = document.getElementById("perLabelTable");
  if (!table) return;

  let html = `<thead><tr><th>Priority</th><th>Count</th>`;
  policies.forEach(p => { html += `<th>${mqEscapeHtml(p)}</th>`; });
  html += `<th>Δ spread</th><th>Δ / CI median (${ciLabel})</th></tr></thead><tbody>`;

  labels.forEach(label => {
    const medians = policies.map(p => (perLabelData[p][label] || {}).median_seconds ?? null);
    const p95s = policies.map(p => (perLabelData[p][label] || {}).p95_seconds ?? null);
    const validMedians = medians.filter(v => v != null);
    const validP95 = p95s.filter(v => v != null);
    const minMedian = validMedians.length ? Math.min(...validMedians) : 0;
    const maxMedian = validMedians.length ? Math.max(...validMedians) : 0;
    const minP95 = validP95.length ? Math.min(...validP95) : 0;
    const spread = maxMedian - minMedian;
    const ciPct = ciMedianSeconds > 0 ? spread / ciMedianSeconds : 0;
    const count = Math.round(policies.reduce((n, p) => n + ((perLabelData[p][label] || {}).count || 0), 0) / policies.length);

    html += `<tr>`;
    html += `<td class="metric-name">${label} <span style="color:var(--muted);font-weight:400;font-size:0.78rem">median</span></td>`;
    html += `<td>${count}</td>`;
    medians.forEach(v => {
      if (v == null) { html += `<td>—</td>`; return; }
      const cls = (v === minMedian && validMedians.length > 1) ? " best" : "";
      html += `<td class="${cls}">${fmtMin(v)}</td>`;
    });
    html += `<td>${spread === 0 ? "—" : fmtMin(spread)}</td>`;
    html += `<td${ciPct >= 1 ? ' class="best"' : ""}>${spread === 0 ? "—" : (ciPct * 100).toFixed(0) + "%"}</td>`;
    html += `</tr>`;

    html += `<tr>`;
    html += `<td class="metric-name" style="color:var(--muted);font-weight:400"><span style="padding-left:0.6rem">p95</span></td>`;
    html += `<td></td>`;
    p95s.forEach(v => {
      if (v == null) { html += `<td>—</td>`; return; }
      const cls = (v === minP95 && validP95.length > 1) ? " best" : "";
      html += `<td class="${cls}" style="color:var(--muted)">${fmtMin(v)}</td>`;
    });
    const p95Spread = validP95.length ? Math.max(...validP95) - minP95 : 0;
    const p95CiPct = ciMedianSeconds > 0 ? p95Spread / ciMedianSeconds : 0;
    html += `<td style="color:var(--muted)">${p95Spread === 0 ? "—" : fmtMin(p95Spread)}</td>`;
    html += `<td style="color:var(--muted)">${p95Spread === 0 ? "—" : (p95CiPct * 100).toFixed(0) + "%"}</td>`;
    html += `</tr>`;
  });

  html += `</tbody>`;
  table.innerHTML = html;
}

function _getVisiblePerLabelPolicies() {
  if (!filesData.length) return new Set(Object.keys(perLabelData || {}));
  const sel = getSelectedIndices();
  const names = new Set(sel.map(i => filesData[i]?.name).filter(Boolean));
  if (!names.size) return new Set(Object.keys(perLabelData || {}));
  return names;
}

function _getSimCiMedianSeconds() {
  if (!filesData.length) return 480;
  const allCiTicks = [];
  const sel = getSelectedIndices();
  let tickSec = 30;
  for (const i of sel) {
    const d = filesData[i];
    if (!d || !d.packed || !d.packed.segs) continue;
    const m = d.metrics;
    if (m && m.tick_seconds) tickSec = Number(m.tick_seconds) || 60;
    d.packed.segs.forEach(s => { if (s.kind === "running") allCiTicks.push(s.end - s.start); });
  }
  if (allCiTicks.length) {
    allCiTicks.sort((a, b) => a - b);
    const mid = Math.floor(allCiTicks.length / 2);
    const medianTicks = allCiTicks.length % 2 ? allCiTicks[mid] : (allCiTicks[mid - 1] + allCiTicks[mid]) / 2;
    return medianTicks * tickSec;
  }
  const meta = filesData.map(d => (d.events || []).find(e => e.event === "scenario_meta")).filter(Boolean)[0];
  if (meta) {
    const ts = Number(meta.tick_seconds) || 60;
    const pd = meta.pipeline_duration;
    if (pd && pd.min != null && pd.max != null) return ((pd.min + pd.max) / 2) * ts;
  }
  return 480;
}
function renderStatsExtended(metrics) {
  const el = document.getElementById("statsExtended");
  if (!el) return;
  if (!statsExtendedMode) {
    el.innerHTML = "";
    return;
  }
  const sel = getSelectedIndices();
  if (!sel.length) { el.innerHTML = ""; return; }
  const rows = sel.map(i => ({ name: filesData[i].name, m: metrics[i] || {} }));
  const bestBy = (key, higher = true) => {
    let best = null;
    rows.forEach(r => {
      const value = Number(r.m[key]);
      if (!Number.isFinite(value)) return;
      if (!best) {
        best = { ...r, value };
        return;
      }
      if (higher ? value > best.value : value < best.value) best = { ...r, value };
    });
    return best;
  };
  const bestPeak = bestBy("throughput_peak_window_mph", true);
  const bestOffPeak = bestBy("throughput_offpeak_window_mph", true);
  const bestRatio = bestBy("peak_offpeak_throughput_ratio", true);
  const bestInterval = bestBy("merge_interval_p95_seconds", false);
  const bestActive = bestBy("throughput_active_mph", true);
  const longestModeled = bestBy("modeled_hours", true);
  const mean = key => {
    const vals = rows.map(r => Number(r.m[key])).filter(Number.isFinite);
    if (!vals.length) return 0;
    return vals.reduce((a, b) => a + b, 0) / vals.length;
  };
  el.innerHTML = `
    <div class="stats-extended-card">
      <h3>Extended Throughput Profile</h3>
      <div class="stats-extended-kv">
        <span class="k">Peak window best</span><span class="v">${bestPeak ? `${fmtSim(bestPeak.value, 3)} mph (${mqEscapeHtml(bestPeak.name)})` : "—"}</span>
        <span class="k">Off-peak best</span><span class="v">${bestOffPeak ? `${fmtSim(bestOffPeak.value, 3)} mph (${mqEscapeHtml(bestOffPeak.name)})` : "—"}</span>
        <span class="k">Active-hour best</span><span class="v">${bestActive ? `${fmtSim(bestActive.value, 3)} mph (${mqEscapeHtml(bestActive.name)})` : "—"}</span>
        <span class="k">Peak/off-peak ratio best</span><span class="v">${bestRatio ? `${fmtSim(bestRatio.value, 3)}x (${mqEscapeHtml(bestRatio.name)})` : "—"}</span>
        <span class="k">Mean peak-window mph</span><span class="v">${fmtSim(mean("throughput_peak_window_mph"), 3)}</span>
        <span class="k">Mean off-peak mph</span><span class="v">${fmtSim(mean("throughput_offpeak_window_mph"), 3)}</span>
      </div>
    </div>
    <div class="stats-extended-card">
      <h3>Modeled Window & Arrivals</h3>
      <div class="stats-extended-kv">
        <span class="k">Longest modeled window</span><span class="v">${longestModeled ? `${fmtHoursLabel(longestModeled.value)} (${mqEscapeHtml(longestModeled.name)})` : "—"}</span>
        <span class="k">Mean modeled window</span><span class="v">${fmtHoursLabel(mean("modeled_hours"))}</span>
        <span class="k">Mean arrivals</span><span class="v">${fmtSim(mean("total_arrivals"), 0)}</span>
        <span class="k">Peak arrivals/h (mean)</span><span class="v">${fmtSim(mean("arrivals_peak_window_per_hour"), 2)}</span>
        <span class="k">Off-peak arrivals/h (mean)</span><span class="v">${fmtSim(mean("arrivals_offpeak_window_per_hour"), 2)}</span>
        <span class="k">24h throughput (mean)</span><span class="v">${fmtSim(mean("throughput_24h_mph"), 3)} mph</span>
      </div>
    </div>
    <div class="stats-extended-card">
      <h3>Merge Interval & Reliability</h3>
      <div class="stats-extended-kv">
        <span class="k">Best p95 merge interval</span><span class="v">${bestInterval ? `${fmtSim(bestInterval.value, 1)}s (${mqEscapeHtml(bestInterval.name)})` : "—"}</span>
        <span class="k">Mean p50 merge interval</span><span class="v">${fmtSim(mean("merge_interval_p50_seconds"), 1)}s</span>
        <span class="k">Mean p95 merge interval</span><span class="v">${fmtSim(mean("merge_interval_p95_seconds"), 1)}s</span>
        <span class="k">Mean rebases</span><span class="v">${fmtSim(mean("rebase_calls"), 0)}</span>
        <span class="k">Mean CI failures</span><span class="v">${fmtSim(mean("ci_failures"), 1)}</span>
        <span class="k">Mean queue drain</span><span class="v">${fmtSim(mean("queue_drain"), 0)}%</span>
      </div>
      <div class="stats-extended-note">Extended metrics are computed from per-hour timeline slices + event intervals.</div>
    </div>
  `;
}
function renderMetricPicker() {
  const el = document.getElementById("metricPicker"); el.innerHTML = "";
  el.innerHTML = `<span class="mp-label">Hero cards:</span>`;
  const activeKeys = getActiveHeroMetricKeys();
  getVisibleHeroMetrics().forEach(m => {
    const active = activeKeys.includes(m.key) ? " active" : "";
    el.innerHTML += `<button data-key="${m.key}" class="${active}" title="${mqEscapeHtml(METRIC_TOOLTIPS[m.key] || m.label)}">${m.label}</button>`;
  });
}
document.getElementById("metricPicker").addEventListener("click", e => {
  const btn = e.target.closest("button[data-key]");
  if (!btn) return;
  const k = btn.dataset.key;
  const current = [...getActiveHeroMetricKeys()];
  if (current.includes(k)) setActiveHeroMetricKeys(current.filter(x => x !== k));
  else setActiveHeroMetricKeys([...current, k]);
  renderStats();
});
// --- Verdict band -----------------------------------------------------------
// Throughput per hour is a platform accounting number and it read as a five-way
// tie on this data. A tenant experiences wait time and the bad tail. Both are
// in the same run summary; this states them against a baseline.
let verdictBaseIdx = null, verdictCandIdx = null;

const VERDICT_TENANT_ROWS = [
  { key: "wait_p50", label: "Median time to merge", fmt: "dur" },
  { key: "wait_p95", label: "p95 tail", fmt: "dur" },
  { key: "wait_max", label: "Worst MR", fmt: "dur" },
  { key: "starved_mrs", label: "MRs waiting > 100 ticks", fmt: "int" },
];

const VERDICT_PLATFORM_ROWS = [
  { key: "_rebases_per_merge", label: "Rebases per merge", fmt: "d2" },
  { key: "dup", label: "Wasted rebases", fmt: "int" },
  { key: "ci_failures", label: "CI failures", fmt: "int" },
  { key: "peak", label: "Peak CI concurrency", fmt: "int" },
];

function verdictValue(m, key) {
  if (!m) return null;
  if (key === "_rebases_per_merge") {
    const merged = Number(m.mrs_merged);
    if (!merged) return null;
    return Number(m.rebase_calls) / merged;
  }
  const v = Number(m[key]);
  return Number.isFinite(v) ? v : null;
}

// A tenant reads "6h22m", not the cmp table's "382:30".
function verdictDuration(ticks, tickSeconds) {
  const secs = Math.round(Number(ticks) * (Number(tickSeconds) || 60));
  if (!Number.isFinite(secs)) return "—";
  const h = Math.floor(secs / 3600), m = Math.round((secs % 3600) / 60);
  if (h > 0) return m > 0 ? `${h}h${String(m).padStart(2, "0")}m` : `${h}h`;
  return `${m}m`;
}

function verdictFmt(v, kind, tickSeconds) {
  if (v == null) return "—";
  if (kind === "dur") return verdictDuration(v, tickSeconds);
  if (kind === "d2") return v.toFixed(2);
  return String(Math.round(v));
}

// Both panels are "lower is better", so one rule covers every row.
function verdictDelta(base, cand) {
  if (base == null || cand == null || base === 0) return null;
  return (cand - base) / base;
}

function pickVerdictPair() {
  const sel = getSelectedIndices().filter(i => filesData[i]?.metrics);
  if (sel.length < 2) return null;
  const named = n => sel.find(i => filesData[i].name.includes(n));
  const byWait = [...sel].sort(
    (a, b) => (verdictValue(filesData[a].metrics, "wait_p50") ?? Infinity)
      - (verdictValue(filesData[b].metrics, "wait_p50") ?? Infinity));
  // old-burst is the pre-OMM production policy, so it is the default baseline
  // whenever it is loaded; otherwise the slowest trace stands in for it.
  let base = verdictBaseIdx != null && sel.includes(verdictBaseIdx)
    ? verdictBaseIdx
    : (named("old-burst") ?? byWait[byWait.length - 1]);
  let cand = verdictCandIdx != null && sel.includes(verdictCandIdx)
    ? verdictCandIdx
    : byWait.find(i => i !== base);
  if (cand == null || cand === base) return null;
  return { base, cand };
}

function verdictHeadline(bm, cm) {
  const p50b = verdictValue(bm, "wait_p50"), p50c = verdictValue(cm, "wait_p50");
  const p95b = verdictValue(bm, "wait_p95"), p95c = verdictValue(cm, "wait_p95");
  const parts = [];
  if (p50b && p50c != null) {
    const r = p50c / p50b;
    parts.push(r < 1
      ? `an MR merges in ${r.toFixed(2)}× the time`
      : `an MR merges in ${r.toFixed(2)}× the time (slower)`);
  }
  if (p95b && p95c != null) {
    const d = (p95b - p95c) / p95b;
    parts.push(d > 0
      ? `the p95 tail shrinks by ${Math.round(d * 100)}%`
      : `the p95 tail grows by ${Math.round(-d * 100)}%`);
  }
  return parts.length ? parts.join(", and ") + "." : "";
}

function verdictRowsHtml(rows, bm, cm, tickSeconds) {
  return rows.map(r => {
    const b = verdictValue(bm, r.key), c = verdictValue(cm, r.key);
    const d = verdictDelta(b, c);
    const cls = d == null ? "" : d < -0.005 ? " better" : d > 0.005 ? " worse" : "";
    const pct = d == null ? "—"
      : `${d > 0 ? "+" : ""}${Math.round(d * 100)}%`;
    return `<tr><th>${mqEscapeHtml(r.label)}</th>`
      + `<td>${verdictFmt(c, r.fmt, tickSeconds)}</td>`
      + `<td class="vd-was">${verdictFmt(b, r.fmt, tickSeconds)}</td>`
      + `<td class="vd-delta${cls}">${pct}</td></tr>`;
  }).join("");
}

function renderVerdictBand() {
  const el = document.getElementById("verdictBand");
  if (!el) return;
  const pair = pickVerdictPair();
  if (!pair) {
    el.innerHTML = `<div class="vd-empty">Load two or more policy traces to compare.</div>`;
    return;
  }
  const { base, cand } = pair;
  const bm = filesData[base].metrics, cm = filesData[cand].metrics;
  const tickSeconds = cm.tick_seconds || bm.tick_seconds;
  const opts = idx => getSelectedIndices().filter(i => filesData[i]?.metrics).map(i =>
    `<option value="${i}"${i === idx ? " selected" : ""}>${mqEscapeHtml(filesData[i].name)}</option>`
  ).join("");

  el.innerHTML =
    `<div class="vd-top">`
    + `<select id="verdictCand" class="vd-pick">${opts(cand)}</select>`
    + `<span class="vd-vs">vs</span>`
    + `<select id="verdictBase" class="vd-pick">${opts(base)}</select>`
    + `<p class="vd-headline">${mqEscapeHtml(verdictHeadline(bm, cm))}</p>`
    + `</div>`
    + `<div class="vd-cols">`
    + `<div class="vd-col"><h4>Tenant — what an MR author waits for</h4>`
    + `<table class="vd-table"><thead><tr><th></th><th>${mqEscapeHtml(filesData[cand].name)}</th>`
    + `<th>${mqEscapeHtml(filesData[base].name)}</th><th>Δ</th></tr></thead>`
    + `<tbody>${verdictRowsHtml(VERDICT_TENANT_ROWS, bm, cm, tickSeconds)}</tbody></table></div>`
    + `<div class="vd-col"><h4>Platform — what the CI fleet pays</h4>`
    + `<table class="vd-table"><thead><tr><th></th><th>${mqEscapeHtml(filesData[cand].name)}</th>`
    + `<th>${mqEscapeHtml(filesData[base].name)}</th><th>Δ</th></tr></thead>`
    + `<tbody>${verdictRowsHtml(VERDICT_PLATFORM_ROWS, bm, cm, tickSeconds)}</tbody></table></div>`
    + `</div>`;

  el.querySelector("#verdictCand").onchange = e => {
    verdictCandIdx = Number(e.target.value); renderVerdictBand();
  };
  el.querySelector("#verdictBase").onchange = e => {
    verdictBaseIdx = Number(e.target.value); renderVerdictBand();
  };
}

function renderHeroCards(metrics) {
  const activeKeys = getActiveHeroMetricKeys();
  const cards = getVisibleHeroMetrics().filter(m => activeKeys.includes(m.key));
  const sel = getSelectedIndices();
  const el = document.getElementById("heroCards"); el.innerHTML = "";
  cards.forEach(c => {
    const numericRows = sel
      .map(i => ({ idx: i, val: Number(metrics[i][c.key]) }))
      .filter(r => Number.isFinite(r.val));
    if (!numericRows.length) {
      el.innerHTML += `<div class="hero-card"><div class="label">${c.label}</div><div class="value">—</div><div class="winner">no data</div><div class="sub">load NDJSON traces with this metric</div></div>`;
      return;
    }
    let best = numericRows[0];
    numericRows.forEach(r => { if (c.higher ? r.val > best.val : r.val < best.val) best = r; });
    const worstVal = numericRows.reduce((w, r) => c.higher ? Math.min(w, r.val) : Math.max(w, r.val), best.val);
    // A card that names a winner when every policy scored the same reads as a
    // result. Say "all tied" and drop the redundant worst line instead.
    const tied = numericRows.every(r => Math.abs(r.val - best.val) < 1e-9);
    const winner = tied
      ? `all ${numericRows.length} tied`
      : mqEscapeHtml(filesData[best.idx].name);
    const sub = tied ? "" : `<div class="sub">${c.higher ? "worst" : "max"}: ${c.fmt(worstVal)}</div>`;
    el.innerHTML += `<div class="hero-card"><div class="label">${c.label}</div><div class="value">${c.fmt(best.val)}</div><div class="winner">${winner}</div>${sub}</div>`;
  });
}
function renderCmpTable() {
  const sel = getSelectedIndices();
  const heads = sel.map(i => filesData[i].name);
  const metrics = sel.map(i => filesData[i].metrics);
  const fmt = (x, f, M) => (f === "dur" ? fmtTicksAsDuration(x, M && M.tick_seconds) : f === "d3" ? (typeof x === "number" ? x.toFixed(3) : x) : f === "d1" ? (typeof x === "number" ? x.toFixed(1) : x) : f === "d0" ? String(Math.round(x)) : x);
  const rows = [
    ["s", "── Throughput & Time ──"], ["mrs_merged", "MRs merged", "d0"], ["throughput_hour", "Throughput (merges/hour)", "d3"],
    ["time_to_first", "Time to first merge", "dur"], ["time_to_10", "Time to merge 10", "dur"], ["avg_int", "Avg merge interval", "dur"], ["queue_drain", "Queue drain %", "d0"],
    ["s", "── CI ──"], ["rebase_calls", "Rebases", "d0"], ["peak", "Peak active", "d0"], ["dup", "Wasted rebases", "d0"], ["ci_min", "CI min", "dur"], ["ci_avg", "CI avg", "dur"], ["ci_max", "CI max", "dur"], ["ci_failures", "CI Failures", "d0"],
    ["s", "── Pool ──"], ["srp_p95", "Same-root p95", "d0"], ["srp_max", "Same-root max", "d0"],
    ["s", "── Batch ──"], ["merge_rounds", "Merge rounds", "d0"], ["avg_mrs_per_round", "Avg MRs/round", "d3"]
  ];
  if (statsExtendedMode) {
    const extendedRows = [
      ["s", "── Extended Throughput ──"], ["throughput_24h_mph", "Throughput/h (24h window)", "d3"], ["throughput_active_mph", "Throughput/h (active merge hours)", "d3"], ["throughput_peak8_mph", "Throughput/h (peak8 avg)", "d3"], ["throughput_peak8_p90_mph", "Throughput/h (peak8 p90)", "d3"], ["throughput_peak_window_mph", "Throughput/h (peak window)", "d3"], ["throughput_offpeak_window_mph", "Throughput/h (off-peak window)", "d3"], ["peak_offpeak_throughput_ratio", "Peak/off-peak throughput ratio", "d3"], ["arrivals_peak_window_per_hour", "Arrivals/h (peak window)", "d3"], ["arrivals_offpeak_window_per_hour", "Arrivals/h (off-peak window)", "d3"], ["merge_interval_p50_seconds", "Merge interval p50 (s)", "d1"], ["merge_interval_p95_seconds", "Merge interval p95 (s)", "d1"],
    ];
    const ciIdx = rows.findIndex(r => r[0] === "s" && r[1] === "── CI ──");
    if (ciIdx >= 0) rows.splice(ciIdx, 0, ...extendedRows);
    else rows.push(...extendedRows);
  }
  let h = "<thead><tr><th>Metric</th>" + heads.map(n => `<th>${mqEscapeHtml(n)}</th>`).join("") + "</tr></thead><tbody>";
  rows.forEach(r => {
    if (r[0] === "s") { h += `<tr class="section"><td colspan="${heads.length + 1}">${r[1]}</td></tr>`; return; }
    const key = r[0], tip = METRIC_TOOLTIPS[key] || "", hib = METRIC_HIGHER_IS_BETTER[key];
    const vals = metrics.map(M => M[key]);
    const bestSet = new Set();
    if (hib !== null && vals.some(v => typeof v === "number")) {
      let bv = hib ? -Infinity : Infinity;
      vals.forEach((v) => { if (typeof v !== "number") return; if (hib ? v > bv : v < bv) bv = v; });
      vals.forEach((v, i) => { if (v === bv) bestSet.add(i); });
      if (bestSet.size === vals.length) bestSet.clear();
    }
    h += `<tr class="data-row"><td class="metric-name" data-tip="${mqEscapeHtml(tip)}">${r[1]}</td>` + metrics.map((M, i) => {
      const v = M[key], cls = bestSet.has(i) ? ' class="best"' : "";
      return `<td${cls}>${r[2] && v != null && typeof v === "number" ? fmt(v, r[2], M) : (v == null ? "—" : v)}</td>`;
    }).join("") + "</tr>";
  });
  h += "</tbody>";
  document.getElementById("cmpTable").innerHTML = h;
}

// --- Kanban Tab ---
function renderKanbanPolicyTabs() {
  const el = document.getElementById("kanbanPolicyTabs");
  el.innerHTML = "";
  filesData.forEach((d, i) => {
    const b = document.createElement("button");
    b.className = "cp-tab" + (kanbanVisibleIdxs.has(i) ? " active" : "");
    b.textContent = d.name;
    b.onclick = () => {
      if (kanbanVisibleIdxs.has(i)) kanbanVisibleIdxs.delete(i);
      else kanbanVisibleIdxs.add(i);
      renderKanbanPolicyTabs();
      renderKanbanTab();
    };
    el.appendChild(b);
  });
}
function renderKanbanTab() {
  if (!filesData.length) return;
  renderKanbanPolicyTabs();
  lockPlayLabelWidth();
  renderSnapshot();
  renderTraces();
}
function ensureSnapshotSkeleton() {
  const el = document.getElementById("kSnap");
  if (el.querySelector("[data-snap=step]")) return;
  el.innerHTML =
    `<span data-snap="step" style="font-weight:600"></span>` +
    ` <span data-snap="peak" class="peak-badge hidden"></span>` +
    ` <span data-snap="queue"><b>Queue:</b></span>` +
    ` <span data-snap="counts"></span>` +
    ` <span data-snap="story" class="story-tip"></span>`;
}
function renderSnapshot() {
  const el = document.getElementById("kSnap");
  ensureSnapshotSkeleton();
  const t = playhead;
  const ctx = getTimeContext(activeIndex);
  const stepEl = el.querySelector("[data-snap=step]");
  const peakEl = el.querySelector("[data-snap=peak]");
  const queueEl = el.querySelector("[data-snap=queue]");
  const countsEl = el.querySelector("[data-snap=counts]");
  const storyEl = el.querySelector("[data-snap=story]");

  stepEl.textContent = fmtTickLabel(t, ctx);

  const peakNow = isTickInPeak(t, ctx);
  peakEl.textContent = peakNow ? "PEAK" : "off-peak";
  peakEl.className = showPeakHighlight ? (peakNow ? "peak-badge peak" : "peak-badge offpeak") : "peak-badge hidden";

  const scrubEl = document.getElementById("scrub");
  if (scrubEl) scrubEl.classList.toggle("in-peak", showPeakHighlight && peakNow);

  const visible = filesData.filter((_, i) => kanbanVisibleIdxs.has(i));
  if (!visible.length) {
    queueEl.style.display = "none";
    countsEl.textContent = "";
    storyEl.textContent = "Select one or more policies to render Kanban boards.";
    storyEl.title = "";
    return;
  }
  queueEl.style.display = "";
  const queueCounts = visible.map(d => {
    if (!d.packed) return "–";
    const f = normalizeEventsForAnalysis(d.events);
    return String(buildKanbanState(d.packed, f, t).queue.length);
  }).join("  ");
  countsEl.textContent = queueCounts;

  const f = normalizeEventsForAnalysis(filesData[0]?.events || []);
  const story = buildNarrativeAtTick(f, t);
  storyEl.textContent = story[0] || "";
  storyEl.title = story.join("; ");
}
function renderTraces() {
  _closeKanbanTip();
  const container = document.getElementById("tracesContainer");
  container.innerHTML = "";
  if (!kanbanVisibleIdxs.size) {
    container.innerHTML = `<div class="kanban-empty">No policies selected. Use the bubbles above to choose what to render.</div>`;
    return;
  }
  filesData.forEach((d, i) => {
    if (!kanbanVisibleIdxs.has(i)) return;
    if (!d.packed) return;
    const f = normalizeEventsForAnalysis(d.events);
    const t = playhead;
    const w = buildKanbanState(d.packed, f, t);
    const trace = document.createElement("div");
    trace.className = "trace";

    const hdr = document.createElement("div");
    hdr.className = "trace-header";
    hdr.innerHTML = `<span class="tname">${mqEscapeHtml(d.name)}</span><span class="tcounts">Q:${w.queue.length} R:${w.rebase.length} CI:${w.ci.length} Rdy:${w.ready.length} St:${w.stale.length} M:${w.merged.length}</span>`;
    trace.appendChild(hdr);

    const body = document.createElement("div");
    body.className = "trace-body";
    const colHdr = document.createElement("div");
    colHdr.className = "trace-cols";
    ["Queue", "Rebasing", "CI", "Ready", "Stale", "Merged"].forEach(n => { colHdr.innerHTML += `<div class="col-hd">${n}</div>`; });
    body.appendChild(colHdr);

    const rebasedRecent = new Set(), mergedRecent = new Set(), forceMergedRecent = new Set(), ciFailedRecent = new Set();
    const justReadyRecent = new Set(), justStaleRecent = new Set();
    const visitedFirstTick = new Map();
    const markVisited = (mr, at) => {
      if (mr == null || at == null || at > t) return;
      const mid = String(mr);
      const prev = visitedFirstTick.get(mid);
      if (prev == null || at < prev) visitedFirstTick.set(mid, at);
    };
    f.forEach(e => {
      if (e.tick == null || e.tick > t) return;
      if (e.event === "rebase" || e.event === "merge") markVisited(e.mr_iid, e.tick);
      if (e.event === "tick") {
        (e.transitions || []).forEach(tr => markVisited(tr.mr_iid, e.tick));
      }
    });
    for (let dt = 0; dt < HIGHLIGHT_TICKS; dt++) { const tt = t - dt; f.forEach(e => {
      if (e.tick === tt && e.mr_iid != null) {
        const mid = String(e.mr_iid);
        if (e.event === "rebase") rebasedRecent.add(mid);
        if (e.event === "merge" && e.force_merge) forceMergedRecent.add(mid);
        else if (e.event === "merge") mergedRecent.add(mid);
      }
      if (e.event === "tick" && e.tick === tt) {
        (e.transitions || []).forEach(tr => {
          const to = (tr.to || "").toLowerCase();
          if (to === "failed" && tr.mr_iid != null) {
            ciFailedRecent.add(String(tr.mr_iid));
          }
          if (to === "success" && tr.mr_iid != null) {
            justReadyRecent.add(String(tr.mr_iid));
          }
        });
      }
    }); }
    // Detect recently-staled MRs from packed segments
    if (d.packed && d.packed.segs) {
      for (const s of d.packed.segs) {
        if (s.kind === "stale" && s.start <= t && s.start > (t - HIGHLIGHT_TICKS)) {
          justStaleRecent.add(String(s.mr));
        }
      }
    }

    const cards = document.createElement("div");
    cards.className = "trace-cards";
    const colData = [w.queue, w.rebase, w.ci, w.ready, w.stale, w.merged];
    const mTickMap = mergeTickByMr(f);
    const kCtx = getTimeContext(i);
    colData.forEach((arr, ci) => {
      const col = document.createElement("div");
      col.className = "tcol";
      if (ci === 5) {
        const byMT = new Map();
        arr.forEach(m => { const mt = mTickMap.get(m) ?? 0; if (!byMT.has(mt)) byMT.set(mt, []); byMT.get(mt).push(m); });
        Array.from(byMT.keys()).sort((a, b) => b - a).forEach(mt => {
          const mrs = byMT.get(mt), isRecent = (t - mt) < HIGHLIGHT_TICKS;
          const mtLabel = timeDisplayMode === "ticks" ? `t=${mt}` : fmtTickAsTime(mt, kCtx);
          if (mrs.length > 1) {
            const grp = document.createElement("div");
            grp.className = "merge-group" + (isRecent ? " recent" : "");
            grp.innerHTML = `<span class="mg-label">${mqEscapeHtml(mtLabel)} (${mrs.length})</span>`;
            mrs.forEach(m => { const c = document.createElement("div"); c.className = "kcard" + (forceMergedRecent.has(m) ? " force-merged" : isRecent ? " just-merged" : " muted"); c.textContent = "!" + m; grp.appendChild(c); });
            col.appendChild(grp);
          } else {
            mrs.forEach(m => { const c = document.createElement("div"); c.className = "kcard" + (forceMergedRecent.has(m) ? " force-merged" : isRecent ? " just-merged" : " muted"); c.textContent = "!" + m; col.appendChild(c); });
          }
        });
      } else if (ci === 0) {
        arr.forEach(m => {
          const c = document.createElement("div");
          const firstVisited = visitedFirstTick.get(String(m));
          let cls = "kcard";
          if (firstVisited == null) cls += " queue-unvisited";
          else if ((t - firstVisited) < HIGHLIGHT_TICKS) cls += " queue-visited-new";
          else cls += " queue-visited";
          c.className = cls;
          c.textContent = "!" + m;
          col.appendChild(c);
        });
      } else {
        arr.forEach(m => {
          const c = document.createElement("div");
          let cls = "kcard";
          if (forceMergedRecent.has(m)) cls += " force-merged";
          else if (mergedRecent.has(m)) cls += " just-merged";
          else if (ciFailedRecent.has(m)) cls += " ci-failed";
          else if (justStaleRecent.has(m) && ci === 4) cls += " just-stale";
          else if (justReadyRecent.has(m) && (ci === 3 || ci === 4)) cls += " just-ready";
          else if (rebasedRecent.has(m)) cls += " just-rebased";
          c.className = cls; c.textContent = "!" + m; col.appendChild(c);
        });
      }
      cards.appendChild(col);
    });
    body.appendChild(cards);
    trace.appendChild(body);
    container.appendChild(trace);
  });
  container.addEventListener("click", _handleKanbanCardClick);
}

let _kanbanTipMr = null;
let _kanbanTipIdx = null;
function _handleKanbanCardClick(event) {
  const card = event.target.closest(".kcard");
  if (!card) return;
  const mrText = card.textContent.replace(/^!/, "");
  const mrId = parseInt(mrText, 10);
  if (isNaN(mrId)) return;
  const trace = card.closest(".trace");
  if (!trace) return;
  const traceIdx = Array.from(document.getElementById("tracesContainer").children).indexOf(trace);
  const dataIdx = _kanbanTraceDataIndex(traceIdx);
  if (dataIdx < 0) return;
  if (_kanbanTipMr === mrId && _kanbanTipIdx === dataIdx) {
    _closeKanbanTip();
    return;
  }
  _kanbanTipMr = mrId;
  _kanbanTipIdx = dataIdx;
  _showKanbanTip(mrId, dataIdx, card);
}
function _kanbanTraceDataIndex(traceIdx) {
  let count = 0;
  for (let i = 0; i < filesData.length; i++) {
    if (!kanbanVisibleIdxs.has(i)) continue;
    if (!filesData[i].packed) continue;
    if (count === traceIdx) return i;
    count++;
  }
  return -1;
}
function _closeKanbanTip() {
  _kanbanTipMr = null;
  _kanbanTipIdx = null;
  const tip = document.getElementById("kanbanTip");
  if (tip) tip.style.display = "none";
}
function _showKanbanTip(mr, dataIdx, cardEl) {
  const tip = document.getElementById("kanbanTip");
  if (!tip) return;
  const d = filesData[dataIdx];
  if (!d || !d.packed) return;
  const evs = normalizeEventsForAnalysis(d.events || []);
  const t = playhead;
  const seg = segmentAtTick(d.packed, mr, t);
  const hist = buildMrHistory(evs, mr, t);
  const labels = getMrScenarioLabels(evs, mr, d.name || "");
  const ctx = getTimeContext(dataIdx);
  const KANBAN_COL_LABELS = { queue: "Queue", rebase: "Rebasing", running: "CI", success: "Ready", stale: "Stale", merged: "Merged", force_merge: "Merged (force)" };
  const stateLabel = seg ? (KANBAN_COL_LABELS[seg.kind] || seg.kind) : "Unknown";
  const segRange = seg ? (timeDisplayMode === "ticks" ? `${seg.start}→${seg.end}` : fmtTickRange(seg.start, seg.end, ctx)) : "n/a";
  const tickLabel = timeDisplayMode === "ticks" ? `t${t}` : fmtTickAsTime(t, ctx);

  let html = `<button class="kt-close" type="button" aria-label="Close">&times;</button>`;
  html += `<div class="kt-head"><span class="kt-mr">MR !${mr}</span><span class="kt-state">${mqEscapeHtml(stateLabel)} @ ${mqEscapeHtml(tickLabel)}</span></div>`;
  html += `<div class="kt-grid">`;
  html += `<div class="k">Policy</div><div class="v">${mqEscapeHtml(d.name || "—")}</div>`;
  html += `<div class="k">Segment</div><div class="v">${mqEscapeHtml(segRange)}</div>`;
  html += `<div class="k">Priority</div><div class="v">${mqEscapeHtml(labels.priorityLabel)}</div>`;
  html += `<div class="k">Service</div><div class="v">${mqEscapeHtml(labels.serviceLabels)}</div>`;
  html += `<div class="k">Rebases</div><div class="v">${hist.rebases}</div>`;
  html += `<div class="k">Merges</div><div class="v">${hist.merges}</div>`;
  html += `<div class="k">CI passed</div><div class="v">${hist.transOk}</div>`;
  html += `<div class="k">CI failed</div><div class="v">${hist.transFail}</div>`;
  html += `</div>`;
  if (hist.recent.length) {
    const fmtTick = h => timeDisplayMode === "ticks" ? `t${h.tick}` : fmtTickAsTime(h.tick, ctx);
    html += `<div class="kt-history"><ul>`;
    html += hist.recent.map(h => `<li><strong>${fmtTick(h)}</strong> ${mqEscapeHtml(h.msg)}</li>`).join("");
    html += `</ul></div>`;
  }
  tip.innerHTML = html;
  tip.querySelector(".kt-close")?.addEventListener("click", _closeKanbanTip);
  tip.style.display = "block";

  const rect = cardEl.getBoundingClientRect();
  const tipW = 440, tipH = tip.offsetHeight || 300;
  let left = rect.right + 8;
  let top = rect.top;
  if (left + tipW > window.innerWidth - 12) left = rect.left - tipW - 8;
  if (left < 8) left = 8;
  if (top + tipH > window.innerHeight - 12) top = window.innerHeight - tipH - 12;
  if (top < 8) top = 8;
  tip.style.left = left + "px";
  tip.style.top = top + "px";
}
(function initKanbanTipDismiss() {
  const panel = document.getElementById("panelKanban");
  if (!panel) return;
  panel.addEventListener("click", function(e) {
    if (_kanbanTipMr == null) return;
    const tip = document.getElementById("kanbanTip");
    if (tip && tip.contains(e.target)) return;
    if (e.target.closest(".kcard")) return;
    _closeKanbanTip();
  });
})();

// --- Composition Tab ---
let chartIdxL = 0, chartIdxR = 1, chartInstL = null, chartInstR = null;
let chartViewMode = "single";
const barCharts = new Map();
const BARS_LEGEND_KEYS = ["Idle", "Open (total)", "Stale", "CI", "Ready"];
const barsLegendVisibility = Object.fromEntries(BARS_LEGEND_KEYS.map(k => [k, true]));
const BARS_LEGEND_COLORS = {
  "Idle": C0.other,
  "Open (total)": "rgba(180,186,194,0.9)",
  "Stale": C0.stale,
  "CI": C0.active,
  "Ready": C0.pool,
};
const BARS_STATS_MODES = ["max", "avg"];
const BARS_STATS_WINDOWS = ["all", "peak", "off-peak"];
let barsStatsMode = "max";
let barsStatsWindow = "all";
let barsStatsSeriesKey = "Idle";
let barsStatsEnabled = true;

document.getElementById("chartViewToggle").addEventListener("click", e => {
  const btn = e.target.closest("button"); if (!btn) return;
  chartViewMode = btn.dataset.mode;
  document.querySelectorAll("#chartViewToggle button").forEach(b => b.classList.toggle("active", b === btn));
  const pair = document.getElementById("chartPair");
  pair.classList.toggle("single-view", chartViewMode === "single");
  document.getElementById("pickerR").style.display = chartViewMode === "single" ? "none" : "";
  document.getElementById("pickerL").querySelector(".cp-label").textContent = chartViewMode === "single" ? "Policy:" : "Left:";
  setTimeout(refreshCharts, 30);
});

function renderChartPolicyTabs() {
  [["chartPolicyTabsL", chartIdxL, "L"], ["chartPolicyTabsR", chartIdxR, "R"]].forEach(([elId, sel, side]) => {
    const el = document.getElementById(elId); el.innerHTML = "";
    filesData.forEach((d, i) => {
      const b = document.createElement("button");
      b.className = "cp-tab" + (i === sel ? " active" : "");
      b.textContent = d.name;
      b.onclick = () => { if (side === "L") chartIdxL = i; else chartIdxR = i; renderChartPolicyTabs(); refreshCharts(); };
      el.appendChild(b);
    });
  });
  document.getElementById("pickerR").style.display = chartViewMode === "single" ? "none" : "";
}
function refreshCharts() {
  const pL = filesData[chartIdxL]?.packed, pR = filesData[chartIdxR]?.packed;
  document.getElementById("chartTitleL").textContent = filesData[chartIdxL]?.name || "";
  document.getElementById("chartTitleR").textContent = filesData[chartIdxR]?.name || "";
  const yMax = getSharedYMax();
  if (pL) {
    doChart(pL, "chartCanvasL", "L", yMax, chartIdxL);
    dSwim(pL, "swimAreaL");
  }
  if (chartViewMode === "compare" && pR) {
    doChart(pR, "chartCanvasR", "R", yMax, chartIdxR);
    dSwim(pR, "swimAreaR");
  }
}

function destroyBarCharts() {
  barCharts.forEach(ch => mqDestroyChartIfPresent(ch));
  barCharts.clear();
}

function barsLegendKeyFromDataset(ds) {
  return String(ds?.seriesKey || ds?.label || "");
}

function getBarsSeriesValues(packed, key, windowFilter, fileIdx) {
  const s = packed?.series;
  if (!s) return [];
  const src = (
    key === "Idle" ? s.rest
      : key === "Open (total)" ? s.open
        : key === "Stale" ? s.stale
          : key === "CI" ? s.active
            : key === "Ready" ? s.pool
              : []
  );
  if (!Array.isArray(src)) return [];
  // Clipped to the visible window first: a mean drawn across a peak-only view
  // but computed over all 24 hours states something false.
  const [lo, hi] = getXWindow(packed.maxTick);
  const inWindow = src.slice(lo, Math.min(hi, src.length - 1) + 1);
  const ctx = getTimeContext(fileIdx);
  if (!windowFilter || windowFilter === "all" || !ctx.peakHoursUtc.size) {
    return inWindow.map(v => Number(v)).filter(v => Number.isFinite(v));
  }
  const wantPeak = windowFilter === "peak";
  return inWindow.map((v, i) => {
    const isPeak = isTickInPeak(lo + i, ctx);
    return (isPeak === wantPeak) ? Number(v) : NaN;
  }).filter(v => Number.isFinite(v));
}

function computeBarsStatValue(packed, key, mode, windowFilter, fileIdx) {
  const values = getBarsSeriesValues(packed, key, windowFilter, fileIdx);
  if (!values.length) return null;
  if (mode === "avg") {
    return values.reduce((sum, v) => sum + v, 0) / values.length;
  }
  return Math.max(...values);
}

function formatBarsStatLabelValue(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "n/a";
  if (Math.abs(n) >= 100) return n.toFixed(1);
  if (Math.abs(n) >= 10) return n.toFixed(2);
  return n.toFixed(3);
}

function _barsStatWindowLabel(win) {
  if (win === "peak") return "PEAK ";
  if (win === "off-peak") return "OFF-PEAK ";
  return "";
}

function plBarsStatLine(packed, fileIdx) {
  return {
    id: "barsStatLine",
    afterDatasetsDraw(chart) {
      if (!barsStatsEnabled) return;
      const key = barsStatsSeriesKey;
      const mode = barsStatsMode;
      if (!key || !BARS_LEGEND_KEYS.includes(key) || !BARS_STATS_MODES.includes(mode)) return;
      if (barsLegendVisibility[key] === false) return;

      const value = computeBarsStatValue(packed, key, mode, barsStatsWindow, fileIdx);
      if (!Number.isFinite(value)) return;

      const yScale = chart?.scales?.y;
      const area = chart?.chartArea;
      if (!yScale || !area) return;

      const y = yScale.getPixelForValue(value);
      if (!Number.isFinite(y) || y < area.top || y > area.bottom) return;

      const color = BARS_LEGEND_COLORS[key] || "#8b949e";
      const winLabel = _barsStatWindowLabel(barsStatsWindow);
      const label = `${key} ${winLabel}${mode.toUpperCase()}: ${formatBarsStatLabelValue(value)}`
        + (isXWindowed() ? " · in window" : "");

      const ctx = chart.ctx;
      ctx.save();
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.75;
      ctx.beginPath();
      ctx.moveTo(area.left, y);
      ctx.lineTo(area.right, y);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.font = "12px ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif";
      const textW = ctx.measureText(label).width;
      const padX = 6;
      const boxH = 18;
      const boxW = Math.ceil(textW + (padX * 2));
      const boxX = Math.max(area.left + 4, area.right - boxW - 4);
      const boxY = Math.min(Math.max(area.top + 4, y - boxH - 4), area.bottom - boxH - 2);

      ctx.fillStyle = "rgba(13,17,23,0.86)";
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.rect(boxX, boxY, boxW, boxH);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = "#e6edf3";
      ctx.textBaseline = "middle";
      ctx.fillText(label, boxX + padX, boxY + (boxH / 2));
      ctx.restore();
    }
  };
}

function renderBarsLegendControls() {
  const traceHost = document.getElementById("barsTraceControls");
  const statsHost = document.getElementById("barsStatsControls");
  const legendHost = document.getElementById("barsLegendSeriesControls");
  if (!traceHost || !statsHost || !legendHost) return;

  traceHost.innerHTML = `<span class="bars-legend-title">Traces</span>` + filesData.map((d, i) => (
    `<button class="bars-trace-btn${barVisibleIdxs.has(i) ? " active" : ""}" data-bars-trace-idx="${i}">${mqEscapeHtml(d.name)}</button>`
  )).join("");

  const statsBodyCls = barsStatsEnabled ? "bars-stat-body" : "bars-stat-body off";
  statsHost.innerHTML = `<button class="bars-stat-power-btn${barsStatsEnabled ? " active" : ""}" data-bars-stat-enabled="toggle">Stats</button>`
    + `<span class="${statsBodyCls}">`
    + `<span class="bars-legend-subtitle">Mode</span>`
    + BARS_STATS_MODES.map(mode => (
      `<button class="bars-stat-mode-btn${barsStatsMode === mode ? " active" : ""}" data-bars-stat-mode="${mode}">${mode.toUpperCase()}</button>`
    )).join("")
    + `<span class="bars-legend-subtitle">Window</span>`
    + BARS_STATS_WINDOWS.map(win => (
      `<button class="bars-stat-mode-btn${barsStatsWindow === win ? " active" : ""}" data-bars-stat-window="${win}">${win === "all" ? "All" : win === "peak" ? "Peak" : "Off-peak"}</button>`
    )).join("")
    + `<span class="bars-legend-subtitle">Trace</span>`
    + BARS_LEGEND_KEYS.map(key => {
      const color = BARS_LEGEND_COLORS[key] || "#8b949e";
      const swatchCls = key === "Open (total)" ? "bars-legend-swatch line" : "bars-legend-swatch";
      const activeCls = barsStatsSeriesKey === key ? " active" : "";
      return `<button class="bars-stat-trace-btn${activeCls}" style="--series-color:${color}" data-bars-stat-trace="${mqEscapeHtml(key)}"><span class="${swatchCls}" aria-hidden="true"></span>${mqEscapeHtml(key)}</button>`;
    }).join("")
    + `</span>`;

  legendHost.innerHTML = `<span class="bars-legend-title">Legend</span>` + BARS_LEGEND_KEYS.map(key => {
    const color = BARS_LEGEND_COLORS[key] || "#8b949e";
    const swatchCls = key === "Open (total)" ? "bars-legend-swatch line" : "bars-legend-swatch";
    const activeCls = barsLegendVisibility[key] !== false ? " active" : "";
    return `<button class="bars-legend-btn${activeCls}" style="--series-color:${color}" data-bars-legend-key="${mqEscapeHtml(key)}"><span class="${swatchCls}" aria-hidden="true"></span>${mqEscapeHtml(key)}</button>`;
  }).join("");
}

function applyBarsLegendVisibility(updateMode = "none") {
  barCharts.forEach(ch => {
    ch.data.datasets.forEach((ds, idx) => {
      const key = barsLegendKeyFromDataset(ds);
      const visible = barsLegendVisibility[key] !== false;
      ch.setDatasetVisibility(idx, visible);
    });
    ch.update(updateMode);
  });
}

function renderBarsTab() {
  if (!filesData.length) return;
  renderBarsLegendControls();
  const host = document.getElementById("barsPanels");
  host.innerHTML = "";
  destroyBarCharts();
  const selected = Array.from(barVisibleIdxs).sort((a, b) => a - b);
  if (!selected.length) {
    host.innerHTML = `<div class="kanban-empty">No policies selected. Use the bubbles above to choose policies.</div>`;
    return;
  }
  const single = selected.length === 1;
  const sharedYMax = (() => {
    if (!selected.length) return null;
    let maxAcross = 0;
    selected.forEach(idx => {
      const packed = filesData[idx]?.packed;
      if (!packed?.series) return;
      const s = packed.series;
      // Windowed: a y axis sized by the peak flattens an off-peak window to
      // nothing. Still shared across policies, so the comparison holds.
      const [lo, hi] = getXWindow(packed.maxTick);
      for (let i = lo; i <= Math.min(hi, s.labels.length - 1); i++) {
        const stack = (Number(s.pool[i]) || 0) + (Number(s.active[i]) || 0)
          + (Number(s.stale[i]) || 0) + (Number(s.rest[i]) || 0);
        maxAcross = Math.max(maxAcross, stack, Number(s.open[i]) || 0);
      }
    });
    // Keep a little headroom so line/legend never clips.
    return maxAcross > 0 ? Math.ceil(maxAcross * 1.05) : 1;
  })();
  selected.forEach((idx, n) => {
    const d = filesData[idx];
    if (!d?.packed) return;
    const panel = document.createElement("div");
    panel.className = "bars-panel" + (single ? " single" : "");
    panel.innerHTML = `<h3>${mqEscapeHtml(d.name)}</h3><canvas id="barsCanvas${idx}-${n}"></canvas>`;
    host.appendChild(panel);
    const ctx = panel.querySelector("canvas").getContext("2d");
    const ch = buildCompositionChart(ctx, d.packed, {
      fileIdx: idx,
      sharedYMax,
      tooltipScope: "bars",
      animation: false,
      extraPlugins: [plBarsStatLine(d.packed, idx)],
    });
    ch.data.datasets.forEach((ds, datasetIdx) => {
      const key = barsLegendKeyFromDataset(ds);
      ch.setDatasetVisibility(datasetIdx, barsLegendVisibility[key] !== false);
    });
    ch.update("none");
    barCharts.set(`bars-${idx}-${n}`, ch);
  });
}

function renderSwimPolicyTabs() {
  const el = document.getElementById("swimPolicyTabs");
  el.innerHTML = "";
  filesData.forEach((d, i) => {
    const b = document.createElement("button");
    b.className = "cp-tab" + (i === swimIdx ? " active" : "");
    b.textContent = d.name;
    b.onclick = () => {
      swimIdx = i;
      renderSwimlaneTab();
    };
    el.appendChild(b);
  });
}

function renderSwimlaneTab() {
  if (!filesData.length) return;
  if (swimIdx >= filesData.length) swimIdx = 0;
  renderSwimPolicyTabs();
  const d = filesData[swimIdx];
  document.getElementById("swimSingleTitle").textContent = d?.name ? `${d.name} swimlane` : "";
  const area = document.getElementById("swimAreaSingle");
  if (!d?.packed) {
    area.innerHTML = `<div class="kanban-empty">No data for selected policy.</div>`;
    return;
  }
  dSwim(d.packed, "swimAreaSingle");
}

const segmentColorForKind = kind => (
  kind === "pending" ? C0.pending
    : kind === "running" ? C0.running
      : kind === "success" ? C0.success
        : kind === "stale" ? C0.stale
          : kind === "merged" ? C0.merged
            : kind === "failed" ? C0.failed
              : kind === "force_merge" ? C0.force_merge
                : C0.other
);

let showMergeLines = true;
function syncMergeLineControls() {
  [document.getElementById("btnMergeLinesComposition"), document.getElementById("btnMergeLinesBars"), document.getElementById("btnMergeLinesSwim")].forEach(el => {
    if (!el) return;
    el.classList.toggle("active", showMergeLines);
    el.setAttribute("aria-pressed", showMergeLines ? "true" : "false");
  });
}
function applyMergeLinesSetting() {
  if (chartInstL) chartInstL.update("none");
  if (chartInstR) chartInstR.update("none");
  barCharts.forEach(ch => ch.update("none"));
  if (document.getElementById("panelSwimlane")?.classList.contains("active")) renderSwimlaneTab();
}
function onMergeLineControlChange(checked) {
  showMergeLines = !!checked;
  syncMergeLineControls();
  applyMergeLinesSetting();
}
["btnMergeLinesComposition", "btnMergeLinesBars", "btnMergeLinesSwim"].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("click", () => onMergeLineControlChange(!showMergeLines));
});
syncMergeLineControls();
const barsLegendControlsEl = document.getElementById("barsLegendControls");
if (barsLegendControlsEl) {
  barsLegendControlsEl.addEventListener("click", e => {
    const traceBtn = e.target.closest("button[data-bars-trace-idx]");
    if (traceBtn) {
      const idx = Number(traceBtn.dataset.barsTraceIdx);
      if (!Number.isFinite(idx) || idx < 0 || idx >= filesData.length) return;
      if (barVisibleIdxs.has(idx)) barVisibleIdxs.delete(idx);
      else barVisibleIdxs.add(idx);
      renderBarsTab();
      return;
    }

    const statsEnabledBtn = e.target.closest("button[data-bars-stat-enabled]");
    if (statsEnabledBtn) {
      barsStatsEnabled = !barsStatsEnabled;
      renderBarsLegendControls();
      barCharts.forEach(ch => ch.update("none"));
      return;
    }

    const statsModeBtn = e.target.closest("button[data-bars-stat-mode]");
    if (statsModeBtn) {
      const mode = String(statsModeBtn.dataset.barsStatMode || "").toLowerCase();
      if (!BARS_STATS_MODES.includes(mode)) return;
      barsStatsMode = mode;
      renderBarsLegendControls();
      barCharts.forEach(ch => ch.update("none"));
      return;
    }

    const statsWindowBtn = e.target.closest("button[data-bars-stat-window]");
    if (statsWindowBtn) {
      const win = String(statsWindowBtn.dataset.barsStatWindow || "");
      if (!BARS_STATS_WINDOWS.includes(win)) return;
      barsStatsWindow = win;
      renderBarsLegendControls();
      barCharts.forEach(ch => ch.update("none"));
      return;
    }

    const statsTraceBtn = e.target.closest("button[data-bars-stat-trace]");
    if (statsTraceBtn) {
      const key = String(statsTraceBtn.dataset.barsStatTrace || "");
      if (!BARS_LEGEND_KEYS.includes(key)) return;
      barsStatsSeriesKey = key;
      if (isShiftPressed(e)) {
        // Shift-click on STATS trace means "force isolate" this legend series.
        BARS_LEGEND_KEYS.forEach(k => {
          barsLegendVisibility[k] = (k === key);
        });
        applyBarsLegendVisibility("none");
      }
      renderBarsLegendControls();
      barCharts.forEach(ch => ch.update("none"));
      return;
    }

    const btn = e.target.closest("button[data-bars-legend-key]");
    if (!btn) return;
    const key = String(btn.dataset.barsLegendKey || "");
    if (!BARS_LEGEND_KEYS.includes(key)) return;
    if (isShiftPressed(e)) {
      const visibleKeys = BARS_LEGEND_KEYS.filter(k => barsLegendVisibility[k] !== false);
      const onlyTargetVisible = visibleKeys.length === 1 && visibleKeys[0] === key;
      BARS_LEGEND_KEYS.forEach(k => {
        barsLegendVisibility[k] = onlyTargetVisible ? true : (k === key);
      });
    } else {
      const visible = barsLegendVisibility[key] !== false;
      barsLegendVisibility[key] = !visible;
    }
    renderBarsLegendControls();
    applyBarsLegendVisibility("none");
  });
}

function plMergePlay(p) {
  return { id: "mp", afterDatasetsDraw(chart) {
    const anyVisible = chart.data.datasets.some((ds, i) => chart.isDatasetVisible(i));
    if (!anyVisible) return;
    const s = chart.scales.x, ctx = chart.ctx, g = chart.chartArea; if (!s) return; ctx.save();
    if (showMergeLines) {
      (p.merges || []).forEach(ev => { const px = s.getPixelForValue(ev.tick); if (g && px >= g.left && px <= g.right) { ctx.strokeStyle = "rgba(255,255,255,0.12)"; ctx.beginPath(); ctx.moveTo(px, g.top); ctx.lineTo(px, g.bottom); ctx.stroke(); } });
    }
    ctx.restore();
  }};
}
const tooltipPrefs = { composition: true, bars: true, swimlane: true };
const swimTipStates = Object.create(null);
const SWIM_COL_LABELS = { wait: "Idle", rebase: "Rebasing", ci: "CI", ready: "Ready", stale: "Stale", merged: "Merged" };

function getTooltipScopeForSwim(swimId) {
  return swimId === "swimAreaSingle" ? "swimlane" : "composition";
}

function isTooltipEnabled(scope) {
  return tooltipPrefs[scope] !== false;
}

function applyCompositionTooltipPref() {
  const enabled = isTooltipEnabled("composition");
  if (chartInstL) { chartInstL.options.plugins.tooltip.enabled = enabled; chartInstL.update("none"); }
  if (chartInstR) { chartInstR.options.plugins.tooltip.enabled = enabled; chartInstR.update("none"); }
}

function applyBarsTooltipPref() {
  const enabled = isTooltipEnabled("bars");
  barCharts.forEach(ch => {
    ch.options.plugins.tooltip.enabled = enabled;
    ch.update("none");
  });
}

function toggleTooltipScope(scope) {
  tooltipPrefs[scope] = !isTooltipEnabled(scope);
  if (scope === "composition") applyCompositionTooltipPref();
  else if (scope === "bars") applyBarsTooltipPref();
}

function getSwimTipState(swimId) {
  if (!swimTipStates[swimId]) {
    swimTipStates[swimId] = {
      hover: null, locked: false, expanded: false, fileKey: null,
      lockMr: null, lockTick: null, lockSeg: null, lockKind: null,
      eventTicks: [], mrEventTicks: [], maxTick: 0,
      lockMinTick: 0, lockMaxTick: 0,
      lockX: null, lockY: null, lockPlacement: "tr",
      tooltipX: null, tooltipY: null,
      lockLaneCenterY: null, lockLaneRowPx: null
    };
  }
  return swimTipStates[swimId];
}

function getActiveSwimLock(preferredSwimId = null) {
  if (preferredSwimId && swimTipStates[preferredSwimId]?.locked) {
    return { swimId: preferredSwimId, state: swimTipStates[preferredSwimId] };
  }
  for (const [swimId, st] of Object.entries(swimTipStates)) {
    if (st.locked) return { swimId, state: st };
  }
  return null;
}

function getSwimOwner(swimId) {
  if (swimId === "swimAreaL") return filesData[chartIdxL] || null;
  if (swimId === "swimAreaR") return filesData[chartIdxR] || null;
  if (swimId === "swimAreaSingle") return filesData[swimIdx] || null;
  return null;
}

function getPackedForSwim(swimId) {
  const owner = getSwimOwner(swimId);
  if (owner?.packed) return owner.packed;
  return null;
}

function getEventsForSwim(swimId) {
  const owner = getSwimOwner(swimId);
  return normalizeEventsForAnalysis(owner?.events || []);
}

function getPageScroll() {
  return { x: window.scrollX || window.pageXOffset || 0, y: window.scrollY || window.pageYOffset || 0 };
}

function getPanelScroll(swimId) {
  const swimEl = document.getElementById(swimId);
  const pane = swimEl?.closest(".chart-pair");
  if (!pane) return null;
  return { el: pane, left: pane.scrollLeft, top: pane.scrollTop };
}

function restorePanelScroll(pos) {
  if (!pos || !pos.el) return;
  pos.el.scrollLeft = pos.left;
  pos.el.scrollTop = pos.top;
}

function getSwimViewportRect(swimId) {
  const swimEl = document.getElementById(swimId);
  if (!swimEl) return null;
  const r = swimEl.getBoundingClientRect();
  const inset = 8;
  const left = Math.max(inset, r.left);
  const right = Math.min(window.innerWidth - inset, r.right);
  const top = Math.max(inset, r.top);
  const bottom = Math.min(window.innerHeight - inset, r.bottom);
  if (right <= left || bottom <= top) return null;
  return { left, right, top, bottom };
}

function restorePageScroll(pos) {
  if (!pos) return;
  const dx = Math.abs((window.scrollX || 0) - pos.x);
  const dy = Math.abs((window.scrollY || 0) - pos.y);
  if (dx > 1 || dy > 1) window.scrollTo(pos.x, pos.y);
}

function positionLockedTip(tip, swimId, st, lockWidth) {
  const node = tip.node();
  if (!node) return;
  const margin = 12;
  const vw = window.innerWidth, vh = window.innerHeight;
  const local = getSwimViewportRect(swimId);
  let left = margin, top = margin;
  if (st.tooltipX != null && st.tooltipY != null) {
    left = st.tooltipX;
    top = st.tooltipY;
  } else {
    const w0 = node.offsetWidth || lockWidth;
    const h0 = node.offsetHeight || 420;
    const baseX = st.lockX ?? (local ? (local.left + margin) : margin);
    const baseY = st.lockY ?? (local ? (local.top + margin) : margin);
    const candidates = [
      { left: baseX + 18, top: baseY + 18 },
      { left: baseX + 18, top: baseY - h0 - 18 },
      { left: baseX - w0 - 18, top: baseY + 18 },
      { left: baseX - w0 - 18, top: baseY - h0 - 18 },
    ];
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(v, hi));
    const laneCenter = st.lockLaneCenterY;
    const laneHalfBand = Math.max(16, (st.lockLaneRowPx || 14) * 4);
    let best = null;
    candidates.forEach(c => {
      const cLeft = clamp(c.left, margin, vw - w0 - margin);
      const cTop = clamp(c.top, margin, vh - h0 - margin);
      const cRight = cLeft + w0;
      const cBottom = cTop + h0;
      const overlapSwimX = local
        ? (cRight > local.left && cLeft < local.right)
        : true;
      const avoidTop = laneCenter != null ? (laneCenter - laneHalfBand) : null;
      const avoidBottom = laneCenter != null ? (laneCenter + laneHalfBand) : null;
      const overlapLaneBand = (
        avoidTop != null &&
        avoidBottom != null &&
        cBottom > avoidTop &&
        cTop < avoidBottom
      );
      const forbidden = overlapSwimX && overlapLaneBand;
      const distPenalty = Math.abs(cLeft - baseX) + Math.abs(cTop - baseY);
      const laneOverlapPx = overlapLaneBand
        ? Math.max(0, Math.min(cBottom, avoidBottom) - Math.max(cTop, avoidTop))
        : 0;
      const score = distPenalty + (forbidden ? (100000 + laneOverlapPx * 50) : 0);
      if (!best || score < best.score) best = { left: cLeft, top: cTop, score };
    });
    left = best?.left ?? clamp(baseX + 18, margin, vw - w0 - margin);
    top = best?.top ?? clamp(baseY + 18, margin, vh - h0 - margin);
  }
  const w = node.offsetWidth || lockWidth;
  const h = node.offsetHeight || 320;
  left = Math.max(margin, Math.min(left, vw - w - margin));
  top = Math.max(margin, Math.min(top, vh - h - margin));
  tip.style("left", left + "px").style("top", top + "px");
}

function clearSwimLock(swimId) {
  const st = swimTipStates[swimId];
  if (!st) return;
  st.locked = false; st.expanded = false; st.lockMr = null; st.lockTick = null; st.lockSeg = null; st.lockKind = null;
  st.mrEventTicks = []; st.lockMinTick = 0; st.lockMaxTick = 0;
  st.lockPlacement = "tr";
  st.tooltipX = null; st.tooltipY = null;
  st.lockX = null; st.lockY = null;
  st.lockLaneCenterY = null; st.lockLaneRowPx = null;
}

function unlockActiveSwimLock(preferredSwimId = null) {
  const active = getActiveSwimLock(preferredSwimId);
  if (!active) return false;
  const unlockedSwimId = active.swimId;
  clearSwimLock(unlockedSwimId);
  swimTipDrag.active = false;
  swimTipDrag.swimId = null;
  const tip = getGlobalSwimTipEl();
  if (!getActiveSwimLock()) {
    tip.style("display", "none").style("pointer-events", "none").classed("locked", false);
  }
  refreshLockedSwimOnly(unlockedSwimId, { skipTipRender: true });
  return true;
}

function clearAllSwimLocks() {
  swimTipDrag.active = false;
  swimTipDrag.swimId = null;
  Object.keys(swimTipStates).forEach(clearSwimLock);
  getGlobalSwimTipEl().style("display", "none").style("pointer-events", "none").classed("locked", false);
}

function shortSha(sha) {
  if (!sha) return "n/a";
  const s = String(sha);
  return s.length > 12 ? s.slice(0, 10) + "…" : s;
}

function mrLaneAtTick(packed, evs, tick, mr) {
  const w = buildKanbanState(packed, evs, tick);
  const isMr = v => String(v) === String(mr);
  for (const k of ["wait", "rebase", "ci", "ready", "stale", "merged"]) {
    if ((w[k] || []).some(isMr)) return k;
  }
  return "wait";
}

function getSwimEventTicks(evs, maxTick) {
  const ticks = new Set([0, maxTick]);
  evs.forEach(e => {
    if (e.tick == null) return;
    if (e.event === "rebase" || e.event === "merge") { ticks.add(e.tick); return; }
    if (e.event === "tick") {
      if ((e.transitions && e.transitions.length) ||
          (e.arrivals && e.arrivals.length) ||
          (e.pushes && e.pushes.length) ||
          (e.cancellations && e.cancellations.length) ||
          (e.force_merges && e.force_merges.length)) ticks.add(e.tick);
    }
  });
  return Array.from(ticks).sort((a, b) => a - b);
}

function getMrEventTicks(evs, mr, maxTick, packed = null) {
  const isMr = v => String(v) === String(mr);
  const ticks = new Set();
  let mergeTick = null;
  evs.forEach(e => {
    if (e.tick == null) return;
    if (e.event === "merge" && isMr(e.mr_iid)) {
      if (mergeTick == null || e.tick < mergeTick) mergeTick = e.tick;
      ticks.add(e.tick);
      return;
    }
    if (e.event === "rebase" && isMr(e.mr_iid)) {
      ticks.add(e.tick);
      return;
    }
    if (e.event !== "tick") return;
    if ((e.transitions || []).some(tr => isMr(tr.mr_iid))) ticks.add(e.tick);
    if (Array.isArray(e.arrivals) && e.arrivals.some(isMr)) ticks.add(e.tick);
    if (Array.isArray(e.pushes) && e.pushes.some(isMr)) ticks.add(e.tick);
    if (Array.isArray(e.cancellations) && e.cancellations.some(isMr)) ticks.add(e.tick);
    if (Array.isArray(e.force_merges) && e.force_merges.some(isMr)) ticks.add(e.tick);
  });
  if (packed && Array.isArray(packed.segs)) {
    packed.segs.forEach(s => {
      if (!isMr(s.mr)) return;
      if (mergeTick != null && s.start != null && Number(s.start) > mergeTick) return;
      if (s.start != null) ticks.add(Math.max(0, Math.min(maxTick, Number(s.start) || 0)));
    });
  }
  let out = Array.from(ticks).sort((a, b) => a - b);
  if (mergeTick != null) {
    out = out.filter(t => t <= mergeTick);
    if (!out.includes(mergeTick)) out.push(mergeTick);
    out.sort((a, b) => a - b);
  }
  if (out.length) return out;
  return [0, maxTick];
}

function segmentAtTick(packed, mr, tick) {
  if (!packed) return null;
  const isMr = v => String(v) === String(mr);
  const rank = { success: 0, running: 1, pending: 2, stale: 3, failed: 4, merged: 5, force_merge: 6 };
  const cand = packed.segs
    .filter(s => isMr(s.mr) && s.kind !== "to_running" && s.start <= tick && s.end > tick)
    .sort((a, b) => (rank[a.kind] ?? 99) - (rank[b.kind] ?? 99));
  return cand[0] || null;
}

function buildMrHistory(evs, mr, tick) {
  const isMr = v => String(v) === String(mr);
  const hist = [];
  let rebases = 0, merges = 0, transOk = 0, transFail = 0;
  const add = (at, msg) => hist.push({ tick: at, msg });
  evs.forEach(e => {
    if (e.tick == null || e.tick > tick) return;
    if (e.event === "rebase" && isMr(e.mr_iid)) {
      rebases++;
      add(e.tick, `Rebase → ${shortSha(e.new_sha)} (pipeline ${e.pipeline_outcome || "n/a"}${e.pipeline_id != null ? ", id " + e.pipeline_id : ""})`);
    } else if (e.event === "merge" && isMr(e.mr_iid)) {
      merges++;
      add(e.tick, `${e.force_merge ? "Force-merge" : "Merge"} → target ${e.new_target_head || "n/a"}`);
    } else if (e.event === "tick") {
      (e.transitions || []).forEach(tr => {
        if (!isMr(tr.mr_iid)) return;
        const from = (tr.from || "unknown").toLowerCase();
        const to = (tr.to || "unknown").toLowerCase();
        if (to === "success") transOk++;
        if (to === "failed") transFail++;
        add(e.tick, `Transition ${from} → ${to}${tr.pipeline_id != null ? " (pipeline " + tr.pipeline_id + ")" : ""}`);
      });
      if (Array.isArray(e.arrivals) && e.arrivals.some(isMr)) add(e.tick, "Arrived in queue");
      if (Array.isArray(e.pushes) && e.pushes.some(isMr)) add(e.tick, "Source branch push (SHA changed)");
      if (Array.isArray(e.cancellations) && e.cancellations.some(isMr)) add(e.tick, "Cancelled");
    }
  });
  // Show newest events first in the tooltip history.
  hist.sort((a, b) => b.tick - a.tick);
  return { rebases, merges, transOk, transFail, recent: hist.slice(0, 14) };
}

function getMrScenarioLabels(evs, mr, traceName = "") {
  const meta = (evs || []).find(e => e.event === "scenario_meta") || {};
  let catalog = meta.mr_catalog;
  if ((!catalog || typeof catalog !== "object") && loadedScenarioDocs.length) {
    const docEntry = resolveScenarioDocForTrace(evs, traceName);
    if (docEntry) catalog = buildScenarioCatalog(docEntry.doc);
  }
  if (!catalog || typeof catalog !== "object") return { priorityLabel: "n/a", serviceLabels: "n/a" };
  const entry = catalog[String(mr)] || catalog[Number(mr)];
  if (!entry || typeof entry !== "object") {
    return { priorityLabel: "n/a", serviceLabels: "n/a" };
  }
  const priorityLabel = entry.priority_label || "n/a";
  const serviceLabelsArr = Array.isArray(entry.service_labels)
    ? entry.service_labels
    : [];
  const serviceLabels = serviceLabelsArr.length
    ? serviceLabelsArr.join(", ")
    : "n/a";
  return { priorityLabel, serviceLabels };
}

const swimTipDrag = { active: false, swimId: null, dx: 0, dy: 0 };

function getGlobalSwimTipEl() {
  const tip = d3.select("body").selectAll(".swim-tip.swim-tip-global").data([0]).join("div")
    .classed("swim-tip", true)
    .classed("swim-tip-global", true);
  if (!tip.attr("data-bound")) {
    tip.attr("data-bound", "1")
      .on("click", event => {
        const active = getActiveSwimLock(tip.attr("data-swim-id") || null);
        if (!active) return;
        event.stopPropagation();
        if (event.shiftKey) { unlockActiveSwimLock(active.swimId); return; }
      })
      .on("input", event => {
        const active = getActiveSwimLock(tip.attr("data-swim-id") || null);
        if (!active) return;
        if (!(event.target && event.target.classList && event.target.classList.contains("lock-scrub"))) return;
        const st = active.state;
        const lo = st.lockMinTick ?? 0;
        const hi = st.lockMaxTick ?? (st.maxTick || 0);
        const v = Math.max(lo, Math.min(hi, Number(event.target.value) || 0));
        st.lockTick = v;
        const packed = getPackedForSwim(active.swimId);
        st.lockSeg = segmentAtTick(packed, st.lockMr, v);
        refreshLockedSwimOnly(active.swimId, { skipTipRender: true });
        const tickVal = tip.node()?.querySelector(".tick-val");
        if (tickVal) tickVal.textContent = `t${v}`;
      })
      .on("change", event => {
        const active = getActiveSwimLock(tip.attr("data-swim-id") || null);
        if (!active) return;
        if (!(event.target && event.target.classList && event.target.classList.contains("lock-scrub"))) return;
        const pagePos = getPageScroll();
        const panelPos = getPanelScroll(active.swimId);
        const swimEl = document.getElementById(active.swimId);
        const swimLeft = swimEl ? swimEl.scrollLeft : 0;
        const swimTop = swimEl ? swimEl.scrollTop : 0;
        renderLockedSwimTip(active.swimId);
        if (swimEl) {
          swimEl.scrollLeft = swimLeft;
          swimEl.scrollTop = swimTop;
        }
        restorePanelScroll(panelPos);
        restorePageScroll(pagePos);
      })
      .on("mousedown", event => {
        const active = getActiveSwimLock(tip.attr("data-swim-id") || null);
        if (!active) return;
        if (!(event.target && event.target.closest && event.target.closest(".head"))) return;
        const rect = tip.node()?.getBoundingClientRect();
        if (!rect) return;
        swimTipDrag.active = true;
        swimTipDrag.swimId = active.swimId;
        swimTipDrag.dx = event.clientX - rect.left;
        swimTipDrag.dy = event.clientY - rect.top;
        active.state.tooltipX = rect.left;
        active.state.tooltipY = rect.top;
        event.preventDefault();
        event.stopPropagation();
      });
    window.addEventListener("mousemove", event => {
      if (!swimTipDrag.active) return;
      const active = getActiveSwimLock(swimTipDrag.swimId || null);
      if (!active || active.swimId !== swimTipDrag.swimId) {
        swimTipDrag.active = false;
        return;
      }
      active.state.tooltipX = event.clientX - swimTipDrag.dx;
      active.state.tooltipY = event.clientY - swimTipDrag.dy;
      positionLockedTip(tip, active.swimId, active.state, 680);
    });
    window.addEventListener("mouseup", () => {
      swimTipDrag.active = false;
    });
  }
  return tip;
}

function renderLockedSwimTip(preferredSwimId = null) {
  const pagePos = getPageScroll();
  const active = getActiveSwimLock(preferredSwimId);
  const tip = getGlobalSwimTipEl();
  if (!active) {
    tip.style("display", "none").style("pointer-events", "none").classed("locked", false);
    restorePageScroll(pagePos);
    return;
  }
  const { swimId, state: st } = active;
  const panelPos = getPanelScroll(swimId);
  const swimEl = document.getElementById(swimId);
  const swimLeft = swimEl ? swimEl.scrollLeft : 0;
  const swimTop = swimEl ? swimEl.scrollTop : 0;
  const packed = getPackedForSwim(swimId);
  const evs = getEventsForSwim(swimId);
  if (!packed || st.lockMr == null || st.lockTick == null) {
    clearSwimLock(swimId);
    if (!getActiveSwimLock()) {
      tip.style("display", "none").style("pointer-events", "none").classed("locked", false);
    }
    restorePageScroll(pagePos);
    return;
  }
  st.lockSeg = segmentAtTick(packed, st.lockMr, st.lockTick);
  const lane = mrLaneAtTick(packed, evs, st.lockTick, st.lockMr);
  const hist = buildMrHistory(evs, st.lockMr, st.lockTick);
  const ownerName = getSwimOwner(swimId)?.name || "policy";
  const mrLabels = getMrScenarioLabels(evs, st.lockMr, ownerName);
  const tickE = evs.find(e => e.event === "tick" && e.tick === st.lockTick);
  const snapE = evs.find(e => e.event === "snapshot" && e.tick === st.lockTick);
  const seg = st.lockSeg;
  const lockOwner = getSwimOwner(swimId);
  const lockOwnerIdx = lockOwner ? filesData.indexOf(lockOwner) : activeIndex;
  const lockTCtx = getTimeContext(lockOwnerIdx);
  const segTxt = seg ? (timeDisplayMode === "ticks" ? `${seg.kind} ${seg.start}→${seg.end}` : `${seg.kind} ${fmtTickRange(seg.start, seg.end, lockTCtx)}`) : "n/a";
  const mrTicks = st.mrEventTicks && st.mrEventTicks.length ? st.mrEventTicks : [st.lockMinTick, st.lockMaxTick];
  const eventIdx = mrTicks.indexOf(st.lockTick);
  const lo = st.lockMinTick ?? 0;
  const hi = st.lockMaxTick ?? (st.maxTick || packed.maxTick);
  const scrubLabel = timeDisplayMode === "ticks"
    ? `Lock tick scrubber (${lo}→${hi}${eventIdx >= 0 ? `, MR event ${eventIdx + 1}/${mrTicks.length}` : ""})`
    : `Lock scrubber (${fmtTickAsTime(lo, lockTCtx)}→${fmtTickAsTime(hi, lockTCtx)}${eventIdx >= 0 ? `, MR event ${eventIdx + 1}/${mrTicks.length}` : ""})`;
  const lockTickLabel = timeDisplayMode === "ticks" ? `t${st.lockTick}` : fmtTickAsTime(st.lockTick, lockTCtx);
  let html = `<div class="head">` +
    `<span class="head-title">MR !${st.lockMr}</span>` +
    `<span class="head-sub">${ownerName} lane</span></div>`;
  html += `<div class="sum">Lane: <strong>${SWIM_COL_LABELS[lane] || lane}</strong><br>Segment: ${mqEscapeHtml(segTxt)}</div>`;
  html += `<div class="scrub"><div class="k">${mqEscapeHtml(scrubLabel)}</div>` +
    `<div class="scrub-row"><input class="lock-scrub" type="range" min="${lo}" max="${hi}" value="${st.lockTick}" step="1" />` +
    `<span class="tick-val">${mqEscapeHtml(lockTickLabel)}</span></div></div>`;
  html += `<div class="details"><div class="grid">` +
    `<div class="k">Target head</div><div class="v">${mqEscapeHtml(snapE?.target_head || "n/a")}</div>` +
    `<div class="k">Open MRs</div><div class="v">${snapE?.open_mrs ?? tickE?.open_mrs ?? "n/a"}</div>` +
    `<div class="k">Active pipelines</div><div class="v">${tickE?.active_pipelines ?? snapE?.active_pipelines ?? "n/a"}</div>` +
    `<div class="k">Same-root pool</div><div class="v">${tickE?.same_root_success_pool ?? "n/a"}</div>` +
    `<div class="k">Priority label</div><div class="v">${mqEscapeHtml(mrLabels.priorityLabel)}</div>` +
    `<div class="k">Service label(s)</div><div class="v">${mqEscapeHtml(mrLabels.serviceLabels)}</div>` +
    `<div class="k">Rebases (<=t)</div><div class="v">${hist.rebases}</div>` +
    `<div class="k">Merges (<=t)</div><div class="v">${hist.merges}</div>` +
    `<div class="k">Transitions success</div><div class="v">${hist.transOk}</div>` +
    `<div class="k">Transitions failed</div><div class="v">${hist.transFail}</div>` +
    `</div>`;
  html += `<div class="k">Recent PR history / transitions</div>`;
  const fmtHistTick = h => timeDisplayMode === "ticks" ? `t${h.tick}` : fmtTickAsTime(h.tick, lockTCtx);
  html += `<div class="history-scroll"><ul>${hist.recent.length ? hist.recent.map(h => `<li><strong>${fmtHistTick(h)}</strong> ${mqEscapeHtml(h.msg)}</li>`).join("") : "<li>No events up to this tick.</li>"}</ul></div>`;
  html += `</div>`;
  html += `<div class="hint">Drag the header to move. Arrow Left/Right jumps event ticks. Arrow Up/Down switches lane. Shift+click or Esc unlocks.</div>`;
  const lockWidth = 860;
  tip.html(html)
    .style("display", "block")
    .style("pointer-events", "auto")
    .attr("data-swim-id", swimId)
    .classed("locked", true);
  positionLockedTip(tip, swimId, st, lockWidth);
  if (swimEl) {
    swimEl.scrollLeft = swimLeft;
    swimEl.scrollTop = swimTop;
  }
  restorePanelScroll(panelPos);
  restorePageScroll(pagePos);
}

function refreshLockedSwimOnly(swimId, opts = {}) {
  const skipTipRender = !!opts.skipTipRender;
  const pagePos = getPageScroll();
  const panelPos = getPanelScroll(swimId);
  if (swimId === "swimAreaL") {
    const p = filesData[chartIdxL]?.packed;
    if (p) dSwim(p, "swimAreaL", { skipTipRender });
  } else if (swimId === "swimAreaR") {
    const p = filesData[chartIdxR]?.packed;
    if (chartViewMode === "compare" && p) dSwim(p, "swimAreaR", { skipTipRender });
  } else if (swimId === "swimAreaSingle") {
    const p = filesData[swimIdx]?.packed;
    if (p) dSwim(p, "swimAreaSingle", { skipTipRender });
  }
  restorePanelScroll(panelPos);
  restorePageScroll(pagePos);
}

function stepLockedTick(direction, preferredSwimId = null) {
  const active = getActiveSwimLock(preferredSwimId);
  if (!active || (direction !== -1 && direction !== 1)) return false;
  const st = active.state;
  const lo = st.lockMinTick ?? 0;
  const hi = st.lockMaxTick ?? (st.maxTick || 0);
  const ticks = (st.mrEventTicks && st.mrEventTicks.length ? st.mrEventTicks : [lo, hi]).filter(t => t >= lo && t <= hi);
  const cur = st.lockTick ?? 0;
  let nxt = cur;
  if (direction > 0) {
    const f = ticks.find(t => t > cur);
    nxt = f == null ? cur : f;
  } else {
    for (let i = ticks.length - 1; i >= 0; i--) {
      if (ticks[i] < cur) { nxt = ticks[i]; break; }
    }
  }
  if (nxt === cur) return true;
  st.lockTick = nxt;
  const packed = getPackedForSwim(active.swimId);
  st.lockSeg = segmentAtTick(packed, st.lockMr, nxt);
  refreshLockedSwimOnly(active.swimId);
  renderLockedSwimTip(active.swimId);
  return true;
}

function stepLockedLane(direction, preferredSwimId = null) {
  const active = getActiveSwimLock(preferredSwimId);
  if (!active || (direction !== -1 && direction !== 1)) return false;
  const st = active.state;
  const packed = getPackedForSwim(active.swimId);
  const evs = getEventsForSwim(active.swimId);
  if (!packed || st.lockMr == null) return false;
  const mrList = Array.from(new Set((packed.segs || []).map(s => s.mr))).sort((a, b) => a - b);
  if (!mrList.length) return false;

  const curIdx = mrList.findIndex(m => String(m) === String(st.lockMr));
  const baseIdx = curIdx >= 0 ? curIdx : 0;
  const nextIdx = Math.max(0, Math.min(mrList.length - 1, baseIdx + direction));
  if (nextIdx === baseIdx) return true;

  const nextMr = mrList[nextIdx];
  const oldTick = st.lockTick ?? 0;
  const mrTicks = getMrEventTicks(evs, nextMr, packed.maxTick, packed);
  const lo = mrTicks[0];
  const hi = mrTicks[mrTicks.length - 1];
  const nextTick = Math.max(lo, Math.min(hi, oldTick));

  st.lockMr = nextMr;
  st.mrEventTicks = mrTicks;
  st.lockMinTick = lo;
  st.lockMaxTick = hi;
  st.lockTick = nextTick;
  st.lockSeg = segmentAtTick(packed, nextMr, nextTick);
  st.lockKind = st.lockSeg?.kind || null;
  if (st.lockLaneCenterY != null && st.lockLaneRowPx != null) {
    st.lockLaneCenterY += direction * st.lockLaneRowPx;
  }
  refreshLockedSwimOnly(active.swimId);
  return true;
}

function getSharedYMax() {
  if (chartViewMode !== "compare") return undefined;
  const pL = filesData[chartIdxL]?.packed, pR = filesData[chartIdxR]?.packed;
  if (!pL || !pR) return undefined;
  let mx = 0;
  for (const p of [pL, pR]) {
    const [lo, hi] = getXWindow(p.maxTick);
    for (let t = Math.max(0, Math.floor(lo)); t <= Math.min(p.maxTick, Math.ceil(hi)); t++) {
      const stack = (p.series.pool[t] || 0) + (p.series.active[t] || 0) + (p.series.stale[t] || 0) + (p.series.rest[t] || 0);
      const line = p.series.open[t] || 0;
      mx = Math.max(mx, stack, line);
    }
  }
  return mx > 0 ? mx : undefined;
}

// --- Time window ------------------------------------------------------------
// A 24h run at 30s ticks is 2880 columns wide. Every surface still renders, but
// one CI pipeline is under half a pixel and the swimlane reads as noise. The
// window narrows the tick domain that every composition chart, All-Policies
// panel and swimlane share.
// xRange === null is the whole run.
const WINDOW_MIN_SPAN = 20;

function getXWindow(maxTick) {
  const mx = Math.max(0, maxTick || 0);
  if (!Array.isArray(xRange)) return [0, mx];
  let a = Math.round(xRange[0]), b = Math.round(xRange[1]);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return [0, mx];
  if (b < a) [a, b] = [b, a];
  a = Math.max(0, Math.min(mx, a));
  b = Math.max(0, Math.min(mx, b));
  const minSpan = Math.min(WINDOW_MIN_SPAN, mx);
  if (b - a < minSpan) {
    b = Math.min(mx, a + minSpan);
    a = Math.max(0, b - minSpan);
  }
  return [a, b];
}

function isXWindowed() { return Array.isArray(xRange); }

function setXWindow(range) {
  xRange = range ? [range[0], range[1]] : null;
  const name = getActiveTabName();
  renderTabContent(name);
  updateSwimTooltipVisibilityForTab(name);
}

// The window is global, but its overview strip is drawn from whichever trace
// the active tab is showing, so the strip and the surface below it agree.
function windowReferenceIdx() {
  const name = getActiveTabName();
  if (name === "swimlane") return swimIdx;
  if (name === "bars") return [...barVisibleIdxs][0] ?? 0;
  return chartIdxL;
}

function windowSpanText(span, tCtx) {
  const secs = span * (tCtx?.tickSeconds || 60);
  if (secs < 5400) return `${(secs / 60).toFixed(0)} min`;
  return `${(secs / 3600).toFixed(1)} h`;
}

function windowPresets(mx, tCtx) {
  const peak = getPeakTickRanges(tCtx, mx);
  const out = [{ key: "full", label: "Full", range: null }];
  if (peak.length) {
    out.push({ key: "peak", label: "Peak", range: [peak[0][0], peak[peak.length - 1][1]] });
    const off = peak[peak.length - 1][1];
    if (mx - off > WINDOW_MIN_SPAN) out.push({ key: "offpeak", label: "Off-peak", range: [off, mx] });
  }
  out.push({ key: "first", label: "First quarter", range: [0, Math.round(mx / 4)] });
  out.push({ key: "last", label: "Last quarter", range: [Math.round(mx * 0.75), mx] });
  return out;
}

function renderWindowBar(hostId) {
  const host = document.getElementById(hostId);
  if (!host) return;
  const idx = windowReferenceIdx();
  const p = filesData[idx]?.packed || filesData[0]?.packed;
  const mx = globalMaxTick();
  if (!p || !mx) { host.innerHTML = ""; host.hidden = true; return; }
  host.hidden = false;
  const tCtx = getTimeContext(idx);
  const [w0, w1] = getXWindow(mx);
  const presets = windowPresets(mx, tCtx);
  const activeKey = !isXWindowed()
    ? "full"
    : (presets.find(o => o.range && o.range[0] === w0 && o.range[1] === w1)?.key || "custom");

  host.innerHTML =
    `<div class="win-head"><span class="tab-control-title">Window</span>`
    + presets.map(o =>
      `<button class="win-preset${o.key === activeKey ? " active" : ""}" data-win-preset="${o.key}">${mqEscapeHtml(o.label)}</button>`
    ).join("")
    + `<button class="win-preset" data-win-nudge="out" title="Double the window">Zoom out</button>`
    + `<button class="win-preset" data-win-nudge="prev" title="Pan one window earlier">&#9664;</button>`
    + `<button class="win-preset" data-win-nudge="next" title="Pan one window later">&#9654;</button>`
    + `<span class="win-readout">${isXWindowed()
      ? `ticks ${w0}–${w1} · ${windowSpanText(w1 - w0, tCtx)} · ${Math.round(100 * (w1 - w0) / mx)}% of run`
      : `ticks 0–${mx} · ${windowSpanText(mx, tCtx)} · full run`}</span>`
    + `</div><div class="win-brush"></div>`;

  host.querySelectorAll("[data-win-preset]").forEach(b => {
    b.onclick = () => {
      const o = presets.find(x => x.key === b.dataset.winPreset);
      if (o) setXWindow(o.range);
    };
  });
  host.querySelectorAll("[data-win-nudge]").forEach(b => {
    b.onclick = () => {
      const span = w1 - w0;
      if (b.dataset.winNudge === "out") {
        const grow = Math.round(span / 2);
        const a = w0 - grow, z = w1 + grow;
        setXWindow(a <= 0 && z >= mx ? null : [Math.max(0, a), Math.min(mx, z)]);
        return;
      }
      const dir = b.dataset.winNudge === "next" ? 1 : -1;
      let a = w0 + dir * span, z = w1 + dir * span;
      if (a < 0) { a = 0; z = span; }
      if (z > mx) { z = mx; a = mx - span; }
      setXWindow([a, z]);
    };
  });
  drawWindowBrush(host.querySelector(".win-brush"), p, mx, tCtx);
}

function drawWindowBrush(container, p, mx, tCtx) {
  // Hand-rolled rather than d3.brushX: the vendored d3 is scale/selection/axis
  // only, and a drag-select over one rect does not justify pulling in
  // d3-brush, d3-drag, d3-transition and d3-shape under a self-only CSP.
  if (!container) return;
  const w = Math.max(240, container.clientWidth || container.parentElement?.clientWidth || 600);
  const h = 46, axisH = 14, plot = h - axisH;
  const sel = d3.select(container).html("");
  const svg = sel.append("svg").attr("width", w).attr("height", h);
  const x = d3.scaleLinear().domain([0, mx]).range([0, w]);

  getPeakTickRanges(tCtx, mx).forEach(([t0, t1]) => {
    svg.append("rect").attr("x", x(t0)).attr("y", 0)
      .attr("width", Math.max(1, x(t1) - x(t0))).attr("height", plot)
      .attr("fill", "rgba(210,153,34,0.13)");
  });

  // One sample per pixel column: 2880 path points would render identically.
  const open = p.series?.open || [];
  if (open.length) {
    const openMax = d3.max(open) || 1;
    const y = d3.scaleLinear().domain([0, openMax]).range([plot - 1, 1]);
    const cols = Math.min(open.length, Math.round(w));
    const pts = [];
    for (let i = 0; i < cols; i++) {
      const lo = Math.floor(i * open.length / cols);
      const hi = Math.max(lo + 1, Math.floor((i + 1) * open.length / cols));
      let peak = 0;
      for (let k = lo; k < hi; k++) peak = Math.max(peak, open[k]);
      pts.push(`${(i * w / cols).toFixed(1)},${y(peak).toFixed(1)}`);
    }
    svg.append("path")
      .attr("d", `M0,${plot} L${pts.join(" L")} L${w},${plot} Z`)
      .attr("fill", "rgba(88,166,255,0.22)").attr("stroke", "rgba(88,166,255,0.75)")
      .attr("stroke-width", 1);
  }

  const ax = d3.axisBottom(x).ticks(8);
  if (timeDisplayMode !== "ticks") ax.tickFormat(tick => fmtTickAsTime(tick, tCtx));
  svg.append("g").attr("class", "win-axis").attr("transform", `translate(0,${plot})`).call(ax);

  const selRect = svg.append("rect").attr("class", "win-sel").attr("y", 0).attr("height", plot)
    .attr("pointer-events", "none").style("display", "none");
  const paint = (px0, px1) => {
    selRect.attr("x", Math.min(px0, px1)).attr("width", Math.abs(px1 - px0))
      .style("display", null);
  };
  if (isXWindowed()) {
    const [w0, w1] = getXWindow(mx);
    paint(x(w0), x(w1));
  }

  // Drag to select; a click with no drag clears the window.
  const hit = svg.append("rect").attr("class", "win-hit")
    .attr("x", 0).attr("y", 0).attr("width", w).attr("height", plot)
    .attr("fill", "transparent").style("cursor", "crosshair");
  let anchor = null;
  hit.on("pointerdown", function(event) {
    anchor = d3.pointer(event, this)[0];
    this.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  }).on("pointermove", function(event) {
    if (anchor == null) return;
    paint(anchor, d3.pointer(event, this)[0]);
  }).on("pointerup", function(event) {
    if (anchor == null) return;
    const px = d3.pointer(event, this)[0];
    const a = anchor;
    anchor = null;
    setXWindow(Math.abs(px - a) < 4 ? null : [x.invert(Math.min(a, px)), x.invert(Math.max(a, px))]);
  }).on("pointercancel", () => { anchor = null; });
}

function buildCompositionChart(canvas, packed, opts = {}) {
  // Pure factory. Constructs and returns a Chart and nothing else: it owns no
  // registry and assigns no module state, so chartInstL/chartInstR and the
  // barCharts map stay with their callers and the two tabs keep independent
  // lifecycles. Everything it reads — C0, timeDisplayMode, the plugin
  // factories — is shared config both tabs already read identically.
  const { fileIdx = 0, sharedYMax = null, tooltipScope = "composition" } = opts;
  const tCtx = getTimeContext(fileIdx);
  const [winX0, winX1] = getXWindow(packed.maxTick);
  const labels = timeDisplayMode === "ticks"
    ? packed.series.labels
    : packed.series.labels.map(t => fmtTickAsTime(t, tCtx));
  const yOpts = { stacked: true, beginAtZero: true };
  if (sharedYMax != null) yOpts.max = sharedYMax;
  const options = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: opts.legend || { display: false },
      tooltip: { enabled: isTooltipEnabled(tooltipScope) },
    },
    scales: {
      x: {
        min: winX0,
        max: winX1,
        title: { display: true, text: timeAxisTitle() },
        ticks: { maxTicksLimit: 12 },
      },
      y: yOpts,
    },
  };
  if (opts.animation !== undefined) options.animation = opts.animation;
  return new Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "Ready", seriesKey: "Ready", data: packed.series.pool, backgroundColor: C0.pool, stack: "a", order: 3, borderWidth: 0 },
        { label: "CI", seriesKey: "CI", data: packed.series.active, backgroundColor: C0.active, stack: "a", order: 2, borderWidth: 0 },
        { label: "Stale", seriesKey: "Stale", data: packed.series.stale, backgroundColor: C0.stale, stack: "a", order: 1, borderWidth: 0 },
        { label: "Idle", seriesKey: "Idle", data: packed.series.rest, backgroundColor: C0.other, stack: "a", order: 0, borderWidth: 0 },
        { label: "Open (total)", seriesKey: "Open (total)", data: packed.series.open, type: "line", borderColor: "rgba(180,186,194,0.9)", borderWidth: 2, pointRadius: 0, borderDash: [5, 4] },
      ],
    },
    options,
    plugins: [plPeakBand(fileIdx), plMergePlay(packed), plCrosshair(), ...(opts.extraPlugins || [])],
  });
}

function doChart(p, canvasId, side, sharedYMax, fileIdx) {
  const cvs = document.getElementById(canvasId); if (!cvs) return;
  if (typeof Chart.getChart === "function") { const o = Chart.getChart(cvs); if (o) o.destroy(); }
  const inst = buildCompositionChart(cvs, p, {
    fileIdx,
    sharedYMax,
    tooltipScope: "composition",
    legend: { position: "bottom", labels: { boxWidth: 10, font: { size: MQSIM_LEGEND_FONT_SIZE } }, onClick: handleLegendToggle },
  });
  if (side === "L") chartInstL = inst; else chartInstR = inst;
}

function plCrosshair() {
  return { id: "crosshair",
    afterEvent(chart, args) {
      const prev = chart._crosshairX;
      chart._crosshairX = (args.event.type === "mousemove") ? args.event.x : null;
      if (args.event.type === "mouseout") chart._crosshairX = null;
      if (prev !== chart._crosshairX) chart.draw();
    },
    afterDatasetsDraw(chart) {
      if (chart._crosshairX == null) return;
      const g = chart.chartArea; if (!g) return;
      const ctx = chart.ctx; ctx.save();
      ctx.fillStyle = "rgba(255,255,255,0.12)";
      ctx.fillRect(chart._crosshairX - 1, g.top, 2, g.bottom - g.top);
      ctx.restore();
    }
  };
}
function syncWindow() {
  const pL = filesData[chartIdxL]?.packed, pR = filesData[chartIdxR]?.packed;
  const yMax = getSharedYMax();
  if (pL) {
    doChart(pL, "chartCanvasL", "L", yMax, chartIdxL);
    dSwim(pL, "swimAreaL");
  }
  if (chartViewMode === "compare" && pR) {
    doChart(pR, "chartCanvasR", "R", yMax, chartIdxR);
    dSwim(pR, "swimAreaR");
  }
  const pS = filesData[swimIdx]?.packed;
  if (pS) dSwim(pS, "swimAreaSingle");
}

function dSwim(p, swimId, opts = {}) {
  const skipTipRender = !!opts.skipTipRender;
  const [x0, x1] = getXWindow(p.maxTick);
  const swimEl = document.getElementById(swimId);
  const prevScrollLeft = swimEl ? swimEl.scrollLeft : 0;
  const prevScrollTop = swimEl ? swimEl.scrollTop : 0;
  const panelPos = getPanelScroll(swimId);
  const pagePos = getPageScroll();
  const sw = d3.select("#" + swimId).html(""); const t = p.maxTick;
  // Only lanes with something inside the window; 108 mostly-empty rows
  // otherwise.
  const list = new Set();
  p.segs.forEach(s => { if (s.end >= x0 && s.start <= x1) list.add(s.mr); });
  const mrL = Array.from(list).sort((a, b) => a - b);
  if (!mrL.length) { sw.append("p").text("No segments."); return; }
  const owner = filesData.find(d => d.packed === p);
  const evs = normalizeEventsForAnalysis(owner?.events || []);
  const st = getSwimTipState(swimId);
  const tip = getGlobalSwimTipEl();
  const tooltipScope = getTooltipScopeForSwim(swimId);
  const kindLabel = k => k === "running" ? "CI Running" : k === "success" ? "Ready" : k === "pending" ? "CI Queued" : k === "stale" ? "Stale" : k === "failed" ? "CI Failed" : k === "merged" ? "Merged" : k === "force_merge" ? "Force-merged" : k;
  st.fileKey = owner?.name || st.fileKey;
  st.maxTick = p.maxTick;
  st.eventTicks = getSwimEventTicks(evs, p.maxTick);
  function showHoverTip(event, s) {
    st.hover = { seg: s, clientX: event.clientX, clientY: event.clientY };
    if (!isTooltipEnabled(tooltipScope)) return;
    if (getActiveSwimLock(swimId)) return;
    const dur = s.end - s.start;
    const labels = getMrScenarioLabels(evs, s.mr, owner?.name || "");
    tip.html(
      `<div class="sum"><strong>MR !${s.mr}</strong><br>` +
      `State: ${kindLabel(s.kind)}<br>` +
      `Priority: ${mqEscapeHtml(labels.priorityLabel)}<br>` +
      `Service: ${mqEscapeHtml(labels.serviceLabels)}<br>` +
      `${fmtTickRange(s.start, s.end, swimTCtx)}` +
      `</div>`
    )
      .style("display", "block")
      .style("left", (event.clientX + 12) + "px")
      .style("top", (event.clientY - 10) + "px")
      .style("pointer-events", "none")
      .classed("locked", false);
  }
  function moveHoverTip(event) {
    if (!st.hover || !isTooltipEnabled(tooltipScope) || getActiveSwimLock(swimId)) return;
    st.hover.clientX = event.clientX; st.hover.clientY = event.clientY;
    tip.style("left", (event.clientX + 12) + "px").style("top", (event.clientY - 10) + "px");
  }
  function clearHoverTip() {
    st.hover = null;
    if (!getActiveSwimLock()) tip.style("display", "none");
  }

  const chartCol = document.getElementById(swimId).closest(".chart-col");
  const chartWrap = chartCol ? chartCol.querySelector(".chart-wrap") : null;
  const isStandalone = swimId === "swimAreaSingle";
  const pad = { t: 4, l: 36, b: 18, r: 4 };
  const w = Math.max(300, (chartWrap?.clientWidth || document.getElementById(swimId).clientWidth || 400) - 8);
  const targetHeight = isStandalone ? Math.max(280, Math.min(w, window.innerHeight * 0.78)) : 0;
  const rowH = isStandalone
    ? Math.max(16, Math.min(52, Math.floor(targetHeight / Math.max(1, mrL.length))))
    : 14;
  const H = pad.t + rowH * mrL.length + pad.b, y = d3.scaleBand().domain(mrL.map(String)).range([pad.t, pad.t + rowH * mrL.length]).padding(0.12);
  const ownerIdx = owner ? filesData.indexOf(owner) : activeIndex;
  const swimTCtx = getTimeContext(ownerIdx);
  const x = d3.scaleLinear().domain([x0, x1]).range([pad.l, w - pad.r]);
  const svg = sw.append("svg").attr("width", w).attr("height", H).style("outline", "none");
  const xAxis = d3.axisBottom(x).ticks(5);
  if (timeDisplayMode !== "ticks") {
    xAxis.tickFormat(tick => fmtTickAsTime(tick, swimTCtx));
  }
  svg.append("g").attr("transform", `translate(0,${H - pad.b})`).call(xAxis);
  if (showPeakHighlight) {
    const peakRanges = getPeakTickRanges(swimTCtx, p.maxTick);
    peakRanges.forEach(([t0, t1]) => {
      const px0 = x(t0), px1 = x(t1);
      svg.append("rect").attr("x", px0).attr("y", pad.t).attr("width", px1 - px0).attr("height", H - pad.t - pad.b)
        .attr("fill", PEAK_BG_COLOR).attr("stroke", PEAK_BORDER_COLOR).attr("stroke-width", 0.5).attr("pointer-events", "none");
    });
  }
  if (st.locked && st.lockMr != null) {
    const activeY = y(String(st.lockMr));
    const activeBw = y.bandwidth();
    if (activeY != null) {
      svg.append("rect")
        .attr("x", pad.l)
        .attr("y", Math.max(pad.t, activeY - 1))
        .attr("width", Math.max(0, (w - pad.r) - pad.l))
        .attr("height", Math.max(2, activeBw + 2))
        .attr("fill", "rgba(88,166,255,0.12)")
        .attr("stroke", "rgba(88,166,255,0.4)")
        .attr("stroke-width", 1)
        .attr("rx", 3);
    }
  }
  if (st.locked && st.lockTick != null && st.lockTick >= x0 && st.lockTick <= x1) {
    const lx = x(st.lockTick);
    const bandY = y(String(st.lockMr));
    const cy = bandY != null ? (bandY + rowH / 2) : ((pad.t + H - pad.b) / 2);
    const half = rowH * 2.5; // cap indicator to ~5 swimlane heights
    const y1Lock = Math.max(pad.t, cy - half);
    const y2Lock = Math.min(H - pad.b, cy + half);
    svg.append("line").attr("x1", lx).attr("x2", lx).attr("y1", y1Lock).attr("y2", y2Lock)
      .attr("stroke", "rgba(255,208,102,0.45)").attr("stroke-width", 6);
    svg.append("line").attr("x1", lx).attr("x2", lx).attr("y1", y1Lock).attr("y2", y2Lock)
      .attr("stroke", "#ffd166").attr("stroke-width", 2);
  }
  const visible = p.segs.filter(s => s.end > x0 && s.start < x1 && s.kind !== "to_running")
    .map(s => { if (s.start > t) return null; if (s.kind === "merged" || s.kind === "force_merge") return s.start <= t ? s : null; if (s.end <= t) return s; if (s.start < t && s.end > t) return { ...s, end: t, _p: 1 }; return null; })
    .filter(s => s && s.end > s.start);
  const bw = y.bandwidth(), barH = bw * 0.75, barOff = (bw - barH) / 2;

  visible.filter(s => s.kind !== "merged" && s.kind !== "force_merge").forEach(s => {
    const sx = x(Math.max(s.start, x0)), segW = Math.max(0, x(Math.min(s.end, x1)) - sx);
    const sy = y(String(s.mr));
    svg.append("rect").attr("x", sx).attr("y", sy + barOff).attr("height", barH)
      .attr("width", segW).attr("fill", segmentColorForKind(s.kind)).attr("opacity", s._p ? 0.5 : 0.85).attr("rx", 2);
    svg.append("rect").attr("x", sx).attr("y", sy).attr("height", bw).attr("width", segW)
      .attr("fill", "transparent").style("cursor", "default")
      .on("mouseover", event => showHoverTip(event, s))
      .on("mousemove", moveHoverTip)
      .on("mouseout", clearHoverTip);
  });
  // Merged markers — full row height line + circle
  if (showMergeLines) visible.filter(s => s.kind === "merged").forEach(s => {
    const mx = x(Math.max(s.start, x0)), yt = y(String(s.mr));
    svg.append("line").attr("x1", mx).attr("x2", mx).attr("y1", yt - 1).attr("y2", yt + bw + 1)
      .attr("stroke", "#58a6ff").attr("stroke-width", 3).attr("opacity", 1);
    svg.append("circle").attr("cx", mx).attr("cy", yt + bw / 2).attr("r", Math.max(3, bw * 0.5))
      .attr("fill", "#58a6ff").attr("stroke", "#0d1117").attr("stroke-width", 1)
      .style("cursor", "default")
      .on("mouseover", event => showHoverTip(event, s))
      .on("mousemove", moveHoverTip)
      .on("mouseout", clearHoverTip);
  });
  // Force-merge markers — full row height diamond (no spanning dashed lines)
  if (showMergeLines) visible.filter(s => s.kind === "force_merge").forEach(s => {
    const mx = x(Math.max(s.start, x0)), yt = y(String(s.mr)), cy = yt + bw / 2;
    const r = Math.max(5, bw * 0.6);
    svg.append("line").attr("x1", mx).attr("x2", mx).attr("y1", yt - 1).attr("y2", yt + bw + 1)
      .attr("stroke", C0.force_merge).attr("stroke-width", 2).attr("opacity", 0.9);
    svg.append("polygon")
      .attr("points", `${mx},${cy - r} ${mx + r},${cy} ${mx},${cy + r} ${mx - r},${cy}`)
      .attr("fill", C0.force_merge).attr("stroke", "#0d1117").attr("stroke-width", 1)
      .style("cursor", "default")
      .on("mouseover", event => showHoverTip(event, s))
      .on("mousemove", moveHoverTip)
      .on("mouseout", clearHoverTip);
  });
  // Crosshair on hover
  const hLine = svg.append("line").attr("y1", pad.t).attr("y2", H - pad.b)
    .attr("stroke", "rgba(255,255,255,0.15)").attr("stroke-width", 1).style("display", "none");
  svg.on("mousemove", function(event) {
    const [mx] = d3.pointer(event);
    hLine.attr("x1", mx).attr("x2", mx).style("display", null);
  }).on("mouseleave", () => hLine.style("display", "none"))
  .on("click", event => {
    if (getActiveSwimLock(swimId)) { unlockActiveSwimLock(swimId); return; }
    const [mx, my] = d3.pointer(event, svg.node());
    const inRows = mx >= pad.l && mx <= (w - pad.r) && my >= pad.t && my <= (pad.t + rowH * mrL.length);
    if (inRows) {
      const rowIdx = Math.max(0, Math.min(mrL.length - 1, Math.floor((my - pad.t) / rowH)));
      const mr = mrL[rowIdx];
      const rawTick = Math.max(0, Math.min(p.maxTick, Math.round(x.invert(mx))));
      const mrTicks = getMrEventTicks(evs, mr, p.maxTick, p);
      const lockMin = mrTicks[0];
      const lockMax = mrTicks[mrTicks.length - 1];
      const tick = Math.max(lockMin, Math.min(lockMax, rawTick));
      st.locked = true;
      st.expanded = true;
      st.fileKey = owner?.name || null;
      st.lockMr = mr;
      st.lockTick = tick;
      st.lockSeg = segmentAtTick(p, mr, tick);
      st.lockKind = st.lockSeg?.kind || null;
      st.lockX = event.clientX + 12;
      st.lockY = event.clientY - 10;
      st.lockLaneCenterY = event.clientY;
      st.lockLaneRowPx = rowH;
      st.lockPlacement = mx >= ((pad.l + (w - pad.r)) / 2) ? "tr" : "bl";
      st.tooltipX = null;
      st.tooltipY = null;
      st.eventTicks = getSwimEventTicks(evs, p.maxTick);
      st.mrEventTicks = mrTicks;
      st.maxTick = p.maxTick;
      st.lockMinTick = lockMin;
      st.lockMaxTick = lockMax;
      renderLockedSwimTip(swimId);
      dSwim(p, swimId, { skipTipRender: true }); // repaint indicator at locked tick
      restorePageScroll(pagePos);
      return;
    }
    toggleTooltipScope(tooltipScope);
    if (!isTooltipEnabled(tooltipScope) && !getActiveSwimLock(swimId)) {
      tip.style("display", "none");
    }
  });

  if (swimEl) {
    swimEl.scrollLeft = prevScrollLeft;
    swimEl.scrollTop = prevScrollTop;
  }
  restorePanelScroll(panelPos);
  restorePageScroll(pagePos);
  if (st.locked && !skipTipRender) renderLockedSwimTip(swimId);
  else if (!getActiveSwimLock()) tip.style("display", "none");
}

// --- Playback ---
function stop() { if (playTimer) { clearInterval(playTimer); playTimer = null; } document.getElementById("btnPlay").textContent = "Play"; }
let _playLabelLockedWidth = 0;
function lockPlayLabelWidth() {
  const el = document.getElementById("playNow");
  if (!el) return;
  const mx = globalMaxTick();
  if (!mx) return;
  const ctx = getTimeContext(activeIndex);
  // The last tick is not the widest string: fmtTickAsTime drops zero
  // components, so a run ending at "10h" passes through "9h59m30s" on the way.
  // Sample across the run and take the widest actually rendered.
  const prev = el.textContent;
  el.style.minWidth = "";
  let measured = 0;
  for (let i = 0; i <= 24; i++) {
    const t = Math.round(mx * i / 24);
    el.textContent = timeDisplayMode === "ticks"
      ? "Step " + t : fmtTickAsTime(t, ctx);
    measured = Math.max(measured, el.offsetWidth);
  }
  el.textContent = prev;
  if (measured > 0 && measured + 2 > _playLabelLockedWidth) {
    _playLabelLockedWidth = measured + 2;
  }
  el.style.minWidth = _playLabelLockedWidth + "px";
}
function updatePlayLabel() {
  const now = document.getElementById("playNow");
  const total = document.getElementById("playTotal");
  if (!now || !total) return;
  const mx = globalMaxTick();
  const ctx = getTimeContext(activeIndex);
  if (timeDisplayMode === "ticks") {
    now.textContent = "Step " + playhead;
    total.textContent = " / " + mx;
  } else {
    now.textContent = fmtTickAsTime(playhead, ctx);
    total.textContent = " / " + fmtTickAsTime(mx, ctx);
  }
}
function tick() { const mx = globalMaxTick(); if (!mx) { stop(); return; } playhead++;
  if (playhead > mx) { if (document.getElementById("chkLoop").checked) playhead = 0; else { playhead = mx; stop(); return; } }
  document.getElementById("scrub").value = playhead;
  updatePlayLabel(); onPlayheadChange(); }
function play() { if (playTimer) { stop(); return; } document.getElementById("btnPlay").textContent = "Pause";
  const s = +document.getElementById("selSpeed").value || 1; playTimer = setInterval(tick, BASE_MS / s); }
function stepOnce() {
  stop();
  const mx = globalMaxTick();
  if (!mx) return;
  if (playhead >= mx) playhead = document.getElementById("chkLoop").checked ? 0 : mx;
  else playhead++;
  document.getElementById("scrub").value = playhead;
  updatePlayLabel();
  onPlayheadChange();
}
function resetP() { stop(); playhead = 0; document.getElementById("scrub").value = 0; updatePlayLabel(); onPlayheadChange(); }

function onPlayheadChange() {
  const active = document.querySelector(".tab-panel.active");
  if (!active) return;
  if (active.id === "panelKanban") { renderSnapshot(); renderTraces(); }
}

document.getElementById("btnPlay").addEventListener("click", play);
document.getElementById("btnStep").addEventListener("click", stepOnce);
document.getElementById("btnReset").addEventListener("click", resetP);
document.getElementById("scrub").addEventListener("input", e => {
  stop();
  playhead = +e.target.value;
  updatePlayLabel();
  onPlayheadChange();
});
document.getElementById("selSpeed").addEventListener("change", () => { if (!playTimer) return; const s = +document.getElementById("selSpeed").value || 1; clearInterval(playTimer); playTimer = setInterval(tick, BASE_MS / s); });
document.addEventListener("keydown", e => {
  if (isTypingTarget(e.target)) return;
  let tabName = null;
  const active = document.querySelector(".tab-panel.active");
  if (active?.id === "panelKanban") tabName = "kanban";
  else if (active?.id === "panelComposition") tabName = "composition";
  else if (active?.id === "panelBars") tabName = "compositionCharts";
  else if (active?.id === "panelSwimlane") tabName = "swimlane";
  if (!tabName) return;
  if (tabName === "kanban") {
    if (e.key === "Escape" && _kanbanTipMr != null) {
      _closeKanbanTip();
      e.preventDefault();
    }
    return;
  }
  const lock = getActiveSwimLockForTab(tabName);
  if (!lock) return;
  if (e.key === "Escape") {
    unlockActiveSwimLock(lock.swimId);
    e.preventDefault();
    return;
  }
  if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && !e.shiftKey) {
    const dir = e.key === "ArrowRight" ? 1 : -1;
    if (stepLockedTick(dir, lock.swimId)) {
      e.preventDefault();
      return;
    }
  }
  if ((e.key === "ArrowUp" || e.key === "ArrowDown") && !e.shiftKey) {
    const dir = e.key === "ArrowDown" ? 1 : -1;
    if (stepLockedLane(dir, lock.swimId)) {
      e.preventDefault();
      return;
    }
  }
});

// =========================================================================
// Time toggle: wire up all [data-time-toggle] groups
(function initTimeToggles() {
  document.querySelectorAll(".time-toggle[data-time-toggle]").forEach(group => {
    group.addEventListener("click", e => {
      const btn = e.target.closest("button[data-tmode]");
      if (!btn) return;
      const mode = btn.dataset.tmode;
      timeDisplayMode = mode;
      document.querySelectorAll(".time-toggle[data-time-toggle] button").forEach(b =>
        b.classList.toggle("active", b.dataset.tmode === mode)
      );
      refreshAllTimeViews();
    });
  });
})();

function refreshAllTimeViews() {
  lockPlayLabelWidth();
  if (typeof renderSnapshot === "function") renderSnapshot();
  if (typeof renderTraces === "function") renderTraces();
  if (typeof updatePlayLabel === "function") updatePlayLabel();
  if (typeof refreshCharts === "function") refreshCharts();
  if (typeof renderBarsTab === "function") renderBarsTab();
  const pS = filesData[swimIdx]?.packed;
  if (pS && typeof dSwim === "function") {
    dSwim(pS, "swimAreaSingle");
  }
  if (typeof renderLockedSwimTip === "function") renderLockedSwimTip();
  if (typeof renderSimulationTab === "function") renderSimulationTab();
  if (typeof renderPerLabelView === "function" && perLabelData) renderPerLabelView();
}

// =========================================================================
// Peak highlight toggle: wire up all [data-peak-toggle] buttons
(function initPeakToggles() {
  document.querySelectorAll("[data-peak-toggle]").forEach(btn => {
    btn.addEventListener("click", () => {
      showPeakHighlight = !showPeakHighlight;
      document.querySelectorAll("[data-peak-toggle]").forEach(b =>
        b.classList.toggle("active", showPeakHighlight)
      );
      refreshAllTimeViews();
    });
  });
})();

// Chart.js plugin: draw peak window background bands
function plPeakBand(fileIdx) {
  return {
    id: "peakBand",
    beforeDatasetsDraw(chart) {
      if (!showPeakHighlight) return;
      const ctx = getTimeContext(fileIdx);
      const area = chart.chartArea;
      if (!area) return;
      const xScale = chart.scales.x;
      if (!xScale) return;
      const maxTick = xScale.max || 0;
      const ranges = getPeakTickRanges(ctx, maxTick);
      if (!ranges.length) return;
      const g = chart.ctx;
      g.save();
      ranges.forEach(([t0, t1]) => {
        const x0 = xScale.getPixelForValue(t0);
        const x1 = xScale.getPixelForValue(t1);
        g.fillStyle = PEAK_BG_COLOR;
        g.fillRect(x0, area.top, x1 - x0, area.bottom - area.top);
        g.strokeStyle = PEAK_BORDER_COLOR;
        g.lineWidth = 1;
        g.beginPath();
        g.moveTo(x0, area.top); g.lineTo(x0, area.bottom);
        g.moveTo(x1, area.top); g.lineTo(x1, area.bottom);
        g.stroke();
      });
      g.restore();
    }
  };
}

// =========================================================================
// Run Browser splitter: drag to resize list vs detail pane
(function initRunBrowserSplitter() {
  const splitter = document.getElementById("runBrowserSplitter");
  const detail = document.getElementById("runDetailPane");
  if (!splitter || !detail) return;
  let dragging = false, startX = 0, startW = 0;
  splitter.addEventListener("mousedown", e => {
    e.preventDefault();
    dragging = true;
    startX = e.clientX;
    startW = detail.offsetWidth;
    splitter.classList.add("dragging");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  });
  document.addEventListener("mousemove", e => {
    if (!dragging) return;
    const dx = startX - e.clientX;
    const container = detail.parentElement;
    const maxW = container ? container.offsetWidth - 100 : window.innerWidth - 200;
    detail.style.width = Math.max(80, Math.min(maxW, startW + dx)) + "px";
  });
  document.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    splitter.classList.remove("dragging");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  });
})();
