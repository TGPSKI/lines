#!/usr/bin/env python3
"""Analyze user-supplied gitlab_housekeeping logs.

Three subcommands:

    compare   Auto-detect algorithm windows and produce A/B/(N) comparison tables.
    measure   Single-algorithm performance report with hourly breakdowns.
    plan      Phase 1 multi-merge overlap analysis with GitLab API enrichment.

Input: any log an adapter in `mqsim.adapters` reads — the JSON export
shape [{@timestamp, message}, ...], the pod text dialect, or neutral
NDJSON records. See docs/log-format.md.
Output: Markdown reports written to --output directory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mqsim.adapters import DIALECTS, read_log  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRIORITY_LABELS = [
    "bot/approved: critical",
    "bot/approved: high",
    "bot/approved: medium",
    "bot/approved: low",
    "bot/approved",
    "lgtm",
]

TENANT_LABEL_PREFIX = "tenant-"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class LogEvent:
    ts: datetime
    kind: str  # "merge" | "rebase"
    algorithm: str  # the policy that emitted it, or "unknown"
    iid: str


@dataclass
class AlgorithmWindow:
    name: str
    start: datetime
    end: datetime

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


@dataclass
class ParsedLog:
    path: Path
    raw_entries: int
    events: list[LogEvent]
    windows: list[AlgorithmWindow]
    time_start: datetime
    time_end: datetime
    dialect: str = ""

    @property
    def hours(self) -> float:
        return (self.time_end - self.time_start).total_seconds() / 3600


@dataclass
class WindowMetrics:
    """Computed metrics for one algorithm window."""

    name: str
    hours: float
    start: datetime
    end: datetime

    total_merges: int = 0
    total_rebases: int = 0
    merges_hr: float = 0.0
    weekday_merges: int = 0
    weekday_merge_hr: float = 0.0
    weekend_merges: int = 0
    peak_merges_hr: int = 0

    clean_rebases: int = 0
    clean_rebases_per_merge: float = 0.0
    waste_ratio: float = 0.0
    wasted_rebases: int = 0
    mrs_needing_1_rebase: int = 0
    max_rebases_single_merged: int = 0

    stuck_mrs: int = 0
    stuck_rebases: int = 0
    top_stuck: list[tuple[str, int]] = field(default_factory=list)

    merge_iids: set[str] = field(default_factory=set)
    rebase_counts: Counter = field(default_factory=Counter)
    hourly_merges: dict[str, set[str]] = field(default_factory=dict)
    hourly_rebases: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


def ingest(path: Path, dialect: str = "auto") -> ParsedLog:
    """Read a log through an adapter and shape its records for the reports."""
    source = read_log(path, dialect)

    # A merge carries no policy of its own; it is attributed below to the
    # rebase window containing it.
    events = [
        LogEvent(
            r.ts,
            r.event,
            "" if r.event == "merge" else (r.policy or "unknown"),
            str(r.iid),
        )
        for r in source.records
        if r.event in {"merge", "rebase"} and r.iid is not None
    ]
    events.sort(key=lambda e: e.ts)

    windows = _detect_windows(events)
    _assign_merge_algorithms(events, windows)

    fallback = source.first_ts or datetime.min
    return ParsedLog(
        path=path,
        raw_entries=source.entries,
        events=events,
        windows=windows,
        time_start=min((e.ts for e in events), default=fallback),
        time_end=max((e.ts for e in events), default=source.last_ts or fallback),
        dialect=source.dialect,
    )


def _detect_windows(events: list[LogEvent]) -> list[AlgorithmWindow]:
    """Detect algorithm windows from rebase policy transitions."""
    rebase_events = [e for e in events if e.kind == "rebase"]
    if not rebase_events:
        return []

    segments: list[tuple[str, datetime, datetime]] = []
    current_algo = rebase_events[0].algorithm
    seg_start = rebase_events[0].ts

    for ev in rebase_events[1:]:
        if ev.algorithm != current_algo:
            segments.append((current_algo, seg_start, ev.ts))
            current_algo = ev.algorithm
            seg_start = ev.ts
    segments.append((current_algo, seg_start, rebase_events[-1].ts))

    # Merge adjacent segments with the same algorithm (handles brief overlap)
    merged: list[AlgorithmWindow] = []
    for name, start, end in segments:
        if merged and merged[-1].name == name:
            merged[-1].end = end
        else:
            merged.append(AlgorithmWindow(name, start, end))

    return merged


def _assign_merge_algorithms(
    events: list[LogEvent], windows: list[AlgorithmWindow]
) -> None:
    """Tag merge events with the algorithm active at that timestamp."""
    for ev in events:
        if ev.kind != "merge":
            continue
        for w in windows:
            if w.start <= ev.ts <= w.end:
                ev.algorithm = w.name
                break
        else:
            # Before first window or after last — use nearest
            if windows:
                if ev.ts < windows[0].start:
                    ev.algorithm = windows[0].name
                else:
                    ev.algorithm = windows[-1].name


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------


def compute_metrics(events: list[LogEvent], window: AlgorithmWindow) -> WindowMetrics:
    """Compute all metrics for events within an algorithm window."""
    merges = [
        e
        for e in events
        if e.kind == "merge"
        and e.algorithm == window.name
        and window.start <= e.ts <= window.end
    ]
    rebases = [
        e
        for e in events
        if e.kind == "rebase"
        and e.algorithm == window.name
        and window.start <= e.ts <= window.end
    ]

    wm = WindowMetrics(
        name=window.name,
        hours=window.hours,
        start=window.start,
        end=window.end,
    )

    merge_iids = {m.iid for m in merges}
    rebase_counts = Counter(r.iid for r in rebases)
    stuck_iids = set(rebase_counts.keys()) - merge_iids
    stuck_rebases = sum(rebase_counts[iid] for iid in stuck_iids)
    clean_rebases = len(rebases) - stuck_rebases
    clean_counts = Counter(r.iid for r in rebases if r.iid in merge_iids)

    wm.total_merges = len(merge_iids)
    wm.total_rebases = len(rebases)
    wm.merges_hr = len(merge_iids) / max(window.hours, 0.01)
    wm.merge_iids = merge_iids
    wm.rebase_counts = rebase_counts

    # Weekday / weekend / peak
    hourly: dict[str, set[str]] = defaultdict(set)
    hourly_r: dict[str, int] = defaultdict(int)
    for m in merges:
        hourly[m.ts.strftime("%Y-%m-%d %H")].add(m.iid)
    for r in rebases:
        hourly_r[r.ts.strftime("%Y-%m-%d %H")] += 1

    wm.hourly_merges = dict(hourly)
    wm.hourly_rebases = dict(hourly_r)

    wd_hours = {
        k: v
        for k, v in hourly.items()
        if datetime.strptime(k, "%Y-%m-%d %H").weekday() < 5
    }
    we_hours = {
        k: v
        for k, v in hourly.items()
        if datetime.strptime(k, "%Y-%m-%d %H").weekday() >= 5
    }

    wd_total = sum(len(v) for v in wd_hours.values())
    wm.weekday_merges = wd_total
    wm.weekday_merge_hr = wd_total / max(len(wd_hours), 1)
    wm.weekend_merges = sum(len(v) for v in we_hours.values())
    wm.peak_merges_hr = max((len(v) for v in hourly.values()), default=0)

    # Rebase efficiency
    wm.clean_rebases = clean_rebases
    wm.clean_rebases_per_merge = clean_rebases / max(len(merge_iids), 1)
    wm.wasted_rebases = max(0, clean_rebases - len(merge_iids))
    wm.waste_ratio = wm.wasted_rebases / max(clean_rebases, 1) * 100
    wm.mrs_needing_1_rebase = sum(1 for v in clean_counts.values() if v == 1)
    wm.max_rebases_single_merged = max(clean_counts.values()) if clean_counts else 0

    # Stuck MR pathology
    wm.stuck_mrs = len(stuck_iids)
    wm.stuck_rebases = stuck_rebases
    wm.top_stuck = sorted(
        [(iid, rebase_counts[iid]) for iid in stuck_iids],
        key=lambda x: -x[1],
    )[:5]

    return wm


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _pct(new: float, old: float) -> str:
    if old == 0:
        return "---"
    return f"{(new - old) / old * 100:+.0f}%"


def _pp(new: float, old: float) -> str:
    return f"{new - old:+.0f}pp"


def _fmt_stuck(top: list[tuple[str, int]], limit: int = 3) -> str:
    return ", ".join(f"!{iid}({cnt})" for iid, cnt in top[:limit])


# ---------------------------------------------------------------------------
# Subcommand: compare
# ---------------------------------------------------------------------------


def cmd_compare(parsed: ParsedLog, output_dir: Path) -> Path:
    """Produce A/B/(N) comparison markdown."""
    windows = parsed.windows
    if not windows:
        print("ERROR: No algorithm windows detected in log data.", file=sys.stderr)
        sys.exit(1)

    metrics = [compute_metrics(parsed.events, w) for w in windows]

    date_tag = (
        parsed.time_start.strftime("%Y%m%d") + "-" + parsed.time_end.strftime("%Y%m%d")
    )
    out_path = output_dir / f"comparison-{date_tag}.md"

    lines: list[str] = []
    _a = lines.append

    _a(f"# A/B Log Comparison — {' vs '.join(w.name for w in windows)}")
    _a("")
    t0 = f"{parsed.time_start:%Y-%m-%d %H:%M}"
    t1 = f"{parsed.time_end:%Y-%m-%d %H:%M}"
    _a(f"**Period**: {t0} → {t1} UTC")
    src = parsed.path.name
    _a(f"**Source**: `{src}`  ({parsed.raw_entries:,} log entries)")
    _a("")

    for w in windows:
        ws = f"{w.start:%Y-%m-%d %H:%M}"
        we = f"{w.end:%Y-%m-%d %H:%M}"
        _a(f"- **{w.name}**: {ws} → {we} ({w.hours:.0f}h)")
    _a("")

    # Build column headers
    names = [m.name for m in metrics]
    has_delta = len(metrics) >= 2

    hdr = f"| {'Metric':<38} |"
    sep = f"|{'-' * 40}|"
    for n in names:
        hdr += f" {n:>12} |"
        sep += f"{'-' * 14}|"
    if has_delta:
        hdr += f" {'Delta':>12} |"
        sep += f"{'-' * 14}|"
    _a(hdr)
    _a(sep)

    def _row(label: str, vals: list[str], delta: str = "") -> str:
        r = f"| {label:<38} |"
        for v in vals:
            r += f" {v:>12} |"
        if has_delta:
            r += f" {delta:>12} |"
        return r

    # Throughput
    _a(
        f"| **THROUGHPUT** {'':>23} |"
        + " " * 14 * len(names)
        + "|"
        + (" " * 14 + "|" if has_delta else "")
    )
    _a(
        _row(
            "Total merges",
            [str(m.total_merges) for m in metrics],
            _pct(metrics[-1].total_merges, metrics[0].total_merges)
            if has_delta
            else "",
        )
    )
    _a(
        _row(
            "Merges/hr (overall)",
            [f"{m.merges_hr:.1f}" for m in metrics],
            _pct(metrics[-1].merges_hr, metrics[0].merges_hr) if has_delta else "",
        )
    )
    _a(
        _row(
            "Merges/hr (weekday active)",
            [f"{m.weekday_merge_hr:.1f}" for m in metrics],
            _pct(metrics[-1].weekday_merge_hr, metrics[0].weekday_merge_hr)
            if has_delta
            else "",
        )
    )
    _a(
        _row(
            "Peak merges/hr",
            [str(m.peak_merges_hr) for m in metrics],
            _pct(metrics[-1].peak_merges_hr, metrics[0].peak_merges_hr)
            if has_delta
            else "",
        )
    )
    _a(_row("", [""] * len(metrics)))

    # Rebase efficiency
    _a(
        f"| **REBASE EFFICIENCY** (excl stuck) {'':>2} |"
        + " " * 14 * len(names)
        + "|"
        + (" " * 14 + "|" if has_delta else "")
    )
    _a(
        _row(
            "Total rebase calls",
            [str(m.total_rebases) for m in metrics],
        )
    )
    _a(
        _row(
            "Clean rebase calls",
            [str(m.clean_rebases) for m in metrics],
        )
    )
    _a(
        _row(
            "Rebase calls/merge (clean)",
            [f"{m.clean_rebases_per_merge:.2f}x" for m in metrics],
            _pct(
                metrics[-1].clean_rebases_per_merge, metrics[0].clean_rebases_per_merge
            )
            if has_delta
            else "",
        )
    )
    _a(
        _row(
            "MRs needing 1 rebase",
            [str(m.mrs_needing_1_rebase) for m in metrics],
        )
    )
    _a(
        _row(
            "Max rebases (single merged MR)",
            [str(m.max_rebases_single_merged) for m in metrics],
        )
    )
    _a(_row("", [""] * len(metrics)))

    # CI waste
    _a(
        f"| **CI WASTE** {'':>25} |"
        + " " * 14 * len(names)
        + "|"
        + (" " * 14 + "|" if has_delta else "")
    )
    _a(
        _row(
            "Wasted rebases",
            [str(m.wasted_rebases) for m in metrics],
        )
    )
    _a(
        _row(
            "Waste ratio",
            [f"{m.waste_ratio:.0f}%" for m in metrics],
            _pp(metrics[-1].waste_ratio, metrics[0].waste_ratio) if has_delta else "",
        )
    )
    if has_delta and metrics[0].clean_rebases_per_merge > 0:
        ci_eff = (
            metrics[0].clean_rebases_per_merge / metrics[-1].clean_rebases_per_merge
        )
        _a(
            _row(
                "CI efficiency gain",
                ["1.0x"] + [""] * (len(metrics) - 2) + [f"{ci_eff:.2f}x"],
            )
        )
    _a(_row("", [""] * len(metrics)))

    # Stuck MR pathology
    _a(
        f"| **STUCK MR PATHOLOGY** {'':>15} |"
        + " " * 14 * len(names)
        + "|"
        + (" " * 14 + "|" if has_delta else "")
    )
    _a(
        _row(
            "Stuck MRs (rebased, never merged)",
            [str(m.stuck_mrs) for m in metrics],
        )
    )
    _a(
        _row(
            "Rebases burned on stuck MRs",
            [str(m.stuck_rebases) for m in metrics],
        )
    )
    for m in metrics:
        if m.top_stuck:
            _a(f"| Top stuck ({m.name}): {_fmt_stuck(m.top_stuck):<60} |")
    _a("")

    # Sim validation block (only if exactly 2 windows)
    if has_delta:
        ci_eff = metrics[0].clean_rebases_per_merge / max(
            metrics[-1].clean_rebases_per_merge, 0.01
        )
        _a("## Simulation Validation")
        _a("")
        _a("| Metric | Predicted | Actual |")
        _a("|--------|-----------|--------|")
        beat = "**BEATS SIM**" if ci_eff > 1.83 else ""
        _a(f"| CI efficiency gain | 1.83x | {ci_eff:.2f}x {beat} |")
        wd_a = metrics[-1].weekday_merge_hr
        wd_b = metrics[0].weekday_merge_hr
        _a(f"| Throughput delta | 0% | {_pct(wd_a, wd_b)} |")
        _a("")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")
    return out_path


# ---------------------------------------------------------------------------
# Subcommand: measure
# ---------------------------------------------------------------------------


def _adopt_unknown_window(parsed: ParsedLog, algorithm: str) -> bool:
    """Label a single policy-less window from --algorithm.

    Records carry the emitting policy only when the log dialect names it. A
    log without it yields one window called "unknown"; naming it is the
    caller asserting which policy ran, and nothing in the log confirms it.
    """
    if len(parsed.windows) != 1 or parsed.windows[0].name != "unknown":
        return False
    parsed.windows[0].name = algorithm
    for ev in parsed.events:
        ev.algorithm = algorithm
    print(f"  Records name no policy; window labelled '{algorithm}' as told.")
    return True


def _select_window(
    parsed: ParsedLog, algorithm: str | None, *, default_last: bool = False
) -> AlgorithmWindow:
    """Pick the window to report on, or exit with what was available."""
    if algorithm:
        matching = [w for w in parsed.windows if w.name == algorithm]
        if not matching and _adopt_unknown_window(parsed, algorithm):
            matching = parsed.windows
        if not matching:
            print(
                f"ERROR: Algorithm '{algorithm}' not found. "
                f"Available: {[w.name for w in parsed.windows]}",
                file=sys.stderr,
            )
            sys.exit(1)
        return matching[0]
    if len(parsed.windows) == 1:
        return parsed.windows[0]
    if default_last:
        if parsed.windows:
            return parsed.windows[-1]
        return AlgorithmWindow("unknown", parsed.time_start, parsed.time_end)
    print(
        "ERROR: Multiple algorithms detected. Specify --algorithm. "
        f"Available: {[w.name for w in parsed.windows]}",
        file=sys.stderr,
    )
    sys.exit(1)


def cmd_measure(parsed: ParsedLog, algorithm: str | None, output_dir: Path) -> Path:
    """Single-algorithm performance report with hourly breakdown."""
    window = _select_window(parsed, algorithm)

    wm = compute_metrics(parsed.events, window)
    date_tag = window.start.strftime("%Y%m%d") + "-" + window.end.strftime("%Y%m%d")
    out_path = output_dir / f"measure-{window.name}-{date_tag}.md"

    lines: list[str] = []
    _a = lines.append

    _a(f"# {window.name.upper()} Performance Report")
    _a("")
    ws = f"{window.start:%Y-%m-%d %H:%M}"
    we = f"{window.end:%Y-%m-%d %H:%M}"
    _a(f"**Window**: {ws} → {we} UTC ({window.hours:.0f}h)")
    _a(f"**Source**: `{parsed.path.name}`")
    _a("")

    _a("## Summary")
    _a("")
    _a("| Metric | Value |")
    _a("|--------|-------|")
    _a(f"| Total merges | {wm.total_merges} |")
    _a(f"| Merges/hr (overall) | {wm.merges_hr:.1f} |")
    _a(f"| Merges/hr (weekday active) | {wm.weekday_merge_hr:.1f} |")
    _a(f"| Weekend merges | {wm.weekend_merges} |")
    _a(f"| Peak merges/hr | {wm.peak_merges_hr} |")
    _a(f"| Total rebase calls | {wm.total_rebases} |")
    _a(f"| Clean rebase calls | {wm.clean_rebases} |")
    _a(f"| Rebase calls/merge | {wm.clean_rebases_per_merge:.2f}x |")
    _a(f"| Waste ratio | {wm.waste_ratio:.0f}% |")
    _a(f"| MRs needing 1 rebase | {wm.mrs_needing_1_rebase} |")
    _a(f"| Max rebases (single merged MR) | {wm.max_rebases_single_merged} |")
    _a(f"| Stuck MRs | {wm.stuck_mrs} (burned {wm.stuck_rebases} rebases) |")
    _a("")

    if wm.top_stuck:
        _a("### Top Stuck MRs")
        _a("")
        _a("| MR | Rebases |")
        _a("|----|---------|")
        for iid, cnt in wm.top_stuck:
            _a(f"| !{iid} | {cnt} |")
        _a("")

    # Hourly breakdown
    _a("## Hourly Breakdown")
    _a("")
    _a("| Hour (UTC) | Merges | Rebases | Ratio |")
    _a("|------------|--------|---------|-------|")

    all_hours = sorted(
        set(list(wm.hourly_merges.keys()) + list(wm.hourly_rebases.keys()))
    )
    for hour in all_hours:
        m_count = len(wm.hourly_merges.get(hour, set()))
        r_count = wm.hourly_rebases.get(hour, 0)
        ratio = f"{r_count / m_count:.1f}x" if m_count > 0 else "---"
        _a(f"| {hour} | {m_count} | {r_count} | {ratio} |")
    _a("")

    # Weekday vs weekend summary
    _a("## Weekday vs Weekend")
    _a("")
    wd_hours = {
        k: v
        for k, v in wm.hourly_merges.items()
        if datetime.strptime(k, "%Y-%m-%d %H").weekday() < 5
    }
    we_hours = {
        k: v
        for k, v in wm.hourly_merges.items()
        if datetime.strptime(k, "%Y-%m-%d %H").weekday() >= 5
    }
    wd_rebases = sum(
        v
        for k, v in wm.hourly_rebases.items()
        if datetime.strptime(k, "%Y-%m-%d %H").weekday() < 5
    )
    we_rebases = sum(
        v
        for k, v in wm.hourly_rebases.items()
        if datetime.strptime(k, "%Y-%m-%d %H").weekday() >= 5
    )

    _a("| | Weekday | Weekend |")
    _a("|--|---------|---------|")
    _a(f"| Active hours | {len(wd_hours)} | {len(we_hours)} |")
    _a(f"| Merges | {wm.weekday_merges} | {wm.weekend_merges} |")
    we_rate = wm.weekend_merges / max(len(we_hours), 1)
    _a(f"| Merges/hr | {wm.weekday_merge_hr:.1f} | {we_rate:.1f} |")
    _a(f"| Rebases | {wd_rebases} | {we_rebases} |")
    _a("")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")
    return out_path


# ---------------------------------------------------------------------------
# Subcommand: plan
# ---------------------------------------------------------------------------

SERVICE_PATH_RE = re.compile(r"^data/services/([^/]+)/")


def _extract_service(filepath: str) -> str | None:
    m = SERVICE_PATH_RE.match(filepath)
    return m.group(1) if m else None


def _load_mr_cache(cache_path: Path) -> dict[str, dict]:
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)
    return {}


def _save_mr_cache(cache_path: Path, cache: dict[str, dict]) -> None:
    with open(cache_path, "w") as f:
        json.dump(cache, f, indent=2, default=str)


def _fetch_mr_data(
    iids: list[str],
    gitlab_url: str,
    token: str,
    project_id: int,
    cache_path: Path,
    api_delay: float,
    *,
    ssl_verify: bool = True,
) -> dict[str, dict]:
    """Fetch MR labels and changed files from GitLab API with caching."""
    import gitlab as gl_lib

    cache = _load_mr_cache(cache_path)
    to_fetch = [iid for iid in iids if iid not in cache]

    if not to_fetch:
        print(f"  All {len(iids)} MRs found in cache.")
        return {iid: cache[iid] for iid in iids if iid in cache}

    print(f"  Fetching {len(to_fetch)} MRs from GitLab API ({len(cache)} cached)...")

    gl = gl_lib.Gitlab(gitlab_url, private_token=token, ssl_verify=ssl_verify)
    project = gl.projects.get(project_id)

    for i, iid in enumerate(to_fetch):
        try:
            mr = project.mergerequests.get(iid)
            changes = mr.changes()
            changed_files = [c["new_path"] for c in changes.get("changes", [])]
            services = list(
                {s for fp in changed_files if (s := _extract_service(fp)) is not None}
            )

            cache[iid] = {
                "iid": iid,
                "title": mr.title,
                "labels": mr.labels,
                "changed_files": changed_files,
                "services": services,
                "author": mr.author["username"] if mr.author else "unknown",
                "state": mr.state,
            }
        except Exception as exc:
            print(f"  WARN: Failed to fetch MR !{iid}: {exc}", file=sys.stderr)
            cache[iid] = {
                "iid": iid,
                "labels": [],
                "changed_files": [],
                "services": [],
                "error": str(exc),
            }

        if (i + 1) % 25 == 0:
            print(f"  ... {i + 1}/{len(to_fetch)}")
            _save_mr_cache(cache_path, cache)

        time.sleep(api_delay)

    _save_mr_cache(cache_path, cache)
    return {iid: cache[iid] for iid in iids if iid in cache}


def _files_overlap(files_a: list[str], files_b: list[str]) -> bool:
    return bool(set(files_a) & set(files_b))


def _services_overlap(svcs_a: list[str], svcs_b: list[str]) -> bool:
    return bool(set(svcs_a) & set(svcs_b))


def cmd_plan(
    parsed: ParsedLog,
    algorithm: str | None,
    output_dir: Path,
    gitlab_url: str | None,
    gitlab_token: str | None,
    project_id: int | None,
    api_delay: float,
    *,
    ssl_verify: bool = True,
) -> list[Path]:
    """Phase 1 multi-merge overlap analysis."""
    window = _select_window(parsed, algorithm, default_last=True)

    # Get merge IIDs in chronological order
    merges = sorted(
        [
            e
            for e in parsed.events
            if e.kind == "merge"
            and e.algorithm == window.name
            and window.start <= e.ts <= window.end
        ],
        key=lambda e: e.ts,
    )
    # Deduplicate: keep first occurrence of each IID
    seen: set[str] = set()
    unique_merges: list[LogEvent] = []
    for m in merges:
        if m.iid not in seen:
            seen.add(m.iid)
            unique_merges.append(m)

    iids = [m.iid for m in unique_merges]
    print(f"Plan: {len(iids)} merged MRs in {window.name} window")

    output_dir.mkdir(parents=True, exist_ok=True)
    cache_path = output_dir / ".mr-cache.json"
    date_tag = window.start.strftime("%Y%m%d") + "-" + window.end.strftime("%Y%m%d")

    # Fetch MR data
    if gitlab_token:
        if not gitlab_url or project_id is None:
            raise ValueError(
                "GitLab enrichment requires explicit gitlab_url and project_id"
            )
        mr_data = _fetch_mr_data(
            iids,
            gitlab_url,
            gitlab_token,
            project_id,
            cache_path,
            api_delay,
            ssl_verify=ssl_verify,
        )
    else:
        print(
            "  WARN: No GitLab token provided (set GITLAB_TOKEN or --gitlab-token). "
            "Skipping API enrichment.",
            file=sys.stderr,
        )
        mr_data = {}

    # --- Output 1: Raw data ---
    raw_path = output_dir / f"plan-raw-{date_tag}.json"
    raw_records = []
    for m in unique_merges:
        d = mr_data.get(m.iid, {})
        raw_records.append(
            {
                "iid": m.iid,
                "merged_at": m.ts.isoformat(),
                "labels": d.get("labels", []),
                "changed_files": d.get("changed_files", []),
                "services": d.get("services", []),
                "title": d.get("title", ""),
                "author": d.get("author", ""),
            }
        )
    with open(raw_path, "w") as f:
        json.dump(raw_records, f, indent=2)

    # --- Pairwise analysis ---
    pairs_total = max(len(unique_merges) - 1, 1)
    file_overlaps = 0
    service_overlaps = 0
    multi_merge_runs: list[int] = []
    current_run = 1

    for i in range(len(unique_merges) - 1):
        a = mr_data.get(unique_merges[i].iid, {})
        b = mr_data.get(unique_merges[i + 1].iid, {})

        f_overlap = _files_overlap(
            a.get("changed_files", []), b.get("changed_files", [])
        )
        s_overlap = _services_overlap(a.get("services", []), b.get("services", []))

        if f_overlap:
            file_overlaps += 1
        if s_overlap:
            service_overlaps += 1

        if not f_overlap:
            current_run += 1
        else:
            multi_merge_runs.append(current_run)
            current_run = 1

    multi_merge_runs.append(current_run)

    # Label distribution
    priority_dist: Counter[str] = Counter()
    tenant_dist: Counter[str] = Counter()
    for iid in iids:
        d = mr_data.get(iid, {})
        for label in d.get("labels", []):
            if label in PRIORITY_LABELS:
                priority_dist[label] += 1
            if label.startswith(TENANT_LABEL_PREFIX):
                tenant_dist[label] += 1

    file_indep_pct = (pairs_total - file_overlaps) / pairs_total * 100
    svc_indep_pct = (pairs_total - service_overlaps) / pairs_total * 100
    avg_run = sum(multi_merge_runs) / max(len(multi_merge_runs), 1)
    max_run = max(multi_merge_runs) if multi_merge_runs else 0
    runs_ge2 = sum(1 for r in multi_merge_runs if r >= 2)
    runs_ge3 = sum(1 for r in multi_merge_runs if r >= 3)

    # --- Output 2: Summary ---
    summary_path = output_dir / f"plan-summary-{date_tag}.md"
    lines: list[str] = []
    _a = lines.append

    _a(f"# Phase 1 Planning Summary — {window.name}")
    _a("")
    ws = f"{window.start:%Y-%m-%d %H:%M}"
    we = f"{window.end:%Y-%m-%d %H:%M}"
    _a(f"**Window**: {ws} → {we} UTC ({window.hours:.0f}h)")
    _a(f"**Merged MRs analyzed**: {len(iids)}")
    api_note = "+ GitLab API" if gitlab_token else "(no API enrichment)"
    _a(f"**Data source**: `{parsed.path.name}` {api_note}")
    _a("")

    _a("## Pairwise Overlap Analysis")
    _a("")
    _a("| Metric | Value |")
    _a("|--------|-------|")
    _a(f"| Consecutive MR pairs | {pairs_total} |")
    fo_pct = file_overlaps / pairs_total * 100
    _a(f"| File-level overlaps | {file_overlaps} ({fo_pct:.1f}%) |")
    fi = pairs_total - file_overlaps
    _a(f"| File-level independent | {fi} ({file_indep_pct:.1f}%) |")
    so_pct = service_overlaps / pairs_total * 100
    _a(f"| Service-level overlaps | {service_overlaps} ({so_pct:.1f}%) |")
    si = pairs_total - service_overlaps
    _a(f"| Service-level independent | {si} ({svc_indep_pct:.1f}%) |")
    _a("")

    _a("## Multi-Merge Opportunity Windows")
    _a("")
    _a("Consecutive non-overlapping MR sequences (multi-merge candidates):")
    _a("")
    _a("| Metric | Value |")
    _a("|--------|-------|")
    _a(f"| Total runs | {len(multi_merge_runs)} |")
    _a(f"| Average run length | {avg_run:.1f} |")
    _a(f"| Max run length | {max_run} |")
    n_runs = max(len(multi_merge_runs), 1)
    ge2_pct = runs_ge2 / n_runs * 100
    ge3_pct = runs_ge3 / n_runs * 100
    _a(f"| Runs >= 2 (multi-merge possible) | {runs_ge2} ({ge2_pct:.0f}%) |")
    _a(f"| Runs >= 3 | {runs_ge3} ({ge3_pct:.0f}%) |")
    _a("")

    run_dist = Counter(multi_merge_runs)
    _a("### Run Length Distribution")
    _a("")
    _a("| Length | Count | % |")
    _a("|--------|-------|---|")
    for length in sorted(run_dist.keys()):
        count = run_dist[length]
        _a(f"| {length} | {count} | {count / len(multi_merge_runs) * 100:.1f}% |")
    _a("")

    if priority_dist:
        _a("## Priority Label Distribution")
        _a("")
        _a("| Label | Count | % of MRs |")
        _a("|-------|-------|----------|")
        for label, count in priority_dist.most_common():
            _a(f"| {label} | {count} | {count / len(iids) * 100:.1f}% |")
        _a("")

    if tenant_dist:
        _a("## Tenant Label Distribution")
        _a("")
        _a("| Label | Count | % of MRs |")
        _a("|-------|-------|----------|")
        for label, count in tenant_dist.most_common(20):
            _a(f"| {label} | {count} | {count / len(iids) * 100:.1f}% |")
        _a("")

    summary_path.write_text("\n".join(lines) + "\n")

    # --- Output 3: Phase 1 impact ---
    impact_path = output_dir / f"plan-phase1-impact-{date_tag}.md"
    lines = []
    _a = lines.append

    _a("# Phase 1 Multi-Merge Impact Prediction")
    _a("")
    _a(f"**Based on**: {len(iids)} merged MRs over {window.hours:.0f}h ({window.name})")
    _a("")

    wm = compute_metrics(parsed.events, window)
    baseline_merges_hr = wm.weekday_merge_hr

    _a("## Throughput Predictions")
    _a("")
    _a("Assuming optimistic multi-merge for non-overlapping consecutive MRs:")
    _a("")
    _a("| Scenario | Merges/hr | Improvement |")
    _a("|----------|-----------|-------------|")
    _a(f"| Baseline (serialized) | {baseline_merges_hr:.1f} | --- |")

    if avg_run > 1:
        predicted_2x = baseline_merges_hr * min(avg_run, 2.0)
        predicted_avg = baseline_merges_hr * avg_run
        predicted_max = baseline_merges_hr * min(max_run, 5.0)
        base = max(baseline_merges_hr, 0.01)
        imp_2x = f"{predicted_2x / base:.1f}x"
        imp_avg = f"{predicted_avg / base:.1f}x"
        imp_max = f"{predicted_max / base:.1f}x"
        _a(f"| Conservative (2-merge) | {predicted_2x:.1f} | {imp_2x} |")
        _a(f"| Avg batch ({avg_run:.1f}) | {predicted_avg:.1f} | {imp_avg} |")
        _a(f"| Peak opportunity (cap 5) | {predicted_max:.1f} | {imp_max} |")
    _a("")

    _a("## Key Findings")
    _a("")
    _a(f"- **{file_indep_pct:.0f}%** of consecutive pairs: zero file overlap")
    _a(f"- **{svc_indep_pct:.0f}%** of consecutive pairs: different services")
    _a(f"- Average non-overlapping run: **{avg_run:.1f}** MRs")
    _a(f"- Longest non-overlapping run: **{max_run}** MRs")
    _a("")

    _a("## Risks and Caveats")
    _a("")
    _a(
        "- File overlap != semantic conflict. "
        "Non-overlapping changes may still conflict."
    )
    _a("- Predictions assume CI time is the bottleneck; verify this for your queue.")
    _a("- Actual throughput depends on arrival rate during peak hours.")
    _a("- Multi-merge requires rollback capability for failed batches.")
    _a("")

    impact_path.write_text("\n".join(lines) + "\n")

    return [raw_path, summary_path, impact_path]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- compare --
    p_cmp = sub.add_parser("compare", help="A/B/(N) algorithm comparison")
    p_cmp.add_argument("--input", "-i", required=True, help="Log file")
    p_cmp.add_argument(
        "--log-dialect",
        default="auto",
        choices=["auto", *DIALECTS],
        help="Log dialect to read. Default: detect from the file",
    )
    p_cmp.add_argument(
        "--output", "-o", default="reports/log-analysis", help="Output directory"
    )
    p_cmp.add_argument(
        "--switch-ts", help="Override switch timestamp (YYYY-MM-DD HH:MM)"
    )

    # -- measure --
    p_msr = sub.add_parser("measure", help="Single-algorithm performance report")
    p_msr.add_argument("--input", "-i", required=True, help="Log file")
    p_msr.add_argument(
        "--log-dialect",
        default="auto",
        choices=["auto", *DIALECTS],
        help="Log dialect to read. Default: detect from the file",
    )
    p_msr.add_argument(
        "--algorithm",
        "-a",
        help="Algorithm name (auto-detected if omitted and unambiguous)",
    )
    p_msr.add_argument(
        "--output", "-o", default="reports/log-analysis", help="Output directory"
    )

    # -- plan --
    p_pln = sub.add_parser("plan", help="Phase 1 multi-merge planning")
    p_pln.add_argument("--input", "-i", required=True, help="Log file")
    p_pln.add_argument(
        "--log-dialect",
        default="auto",
        choices=["auto", *DIALECTS],
        help="Log dialect to read. Default: detect from the file",
    )
    p_pln.add_argument(
        "--algorithm", "-a", help="Algorithm window to analyze (default: last detected)"
    )
    p_pln.add_argument(
        "--output", "-o", default="reports/log-analysis", help="Output directory"
    )
    p_pln.add_argument(
        "--gitlab-url",
        default=None,
        help="GitLab instance URL (required for live enrichment)",
    )
    p_pln.add_argument(
        "--gitlab-token",
        default=os.environ.get("GITLAB_TOKEN"),
        help="GitLab private token (default: $GITLAB_TOKEN)",
    )
    p_pln.add_argument(
        "--project-id",
        type=int,
        default=None,
        help="GitLab project ID (required for live enrichment)",
    )
    p_pln.add_argument(
        "--api-delay", type=float, default=0.5, help="Seconds between API calls"
    )
    p_pln.add_argument(
        "--no-ssl-verify",
        action="store_true",
        default=False,
        help="Disable SSL certificate verification (for self-signed CAs)",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "plan" and args.gitlab_token:
        missing = []
        if not args.gitlab_url:
            missing.append("--gitlab-url")
        if args.project_id is None:
            missing.append("--project-id")
        if missing:
            parser.error(
                f"{', '.join(missing)} required when GitLab enrichment is enabled"
            )

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output)
    parsed = ingest(input_path, args.log_dialect)
    print(f"Loaded {input_path.name} as {parsed.dialect}")
    print(
        f"  {parsed.raw_entries:,} entries, {len(parsed.events)} relevant events, "
        f"{len(parsed.windows)} algorithm window(s) detected"
    )
    for w in parsed.windows:
        ws = f"{w.start:%Y-%m-%d %H:%M}"
        we = f"{w.end:%Y-%m-%d %H:%M}"
        print(f"  - {w.name}: {ws} → {we} ({w.hours:.0f}h)")

    # Handle --switch-ts override for compare
    if args.command == "compare" and getattr(args, "switch_ts", None):
        switch = datetime.strptime(args.switch_ts, "%Y-%m-%d %H:%M")
        parsed.windows = _detect_windows_with_override(parsed.events, switch)
        _assign_merge_algorithms(parsed.events, parsed.windows)
        print(f"  (override) Switch timestamp: {switch}")

    if args.command == "compare":
        out = cmd_compare(parsed, output_dir)
        print(f"\nWrote: {out}")

    elif args.command == "measure":
        out = cmd_measure(parsed, getattr(args, "algorithm", None), output_dir)
        print(f"\nWrote: {out}")

    elif args.command == "plan":
        outputs = cmd_plan(
            parsed,
            getattr(args, "algorithm", None),
            output_dir,
            args.gitlab_url,
            args.gitlab_token,
            args.project_id,
            args.api_delay,
            ssl_verify=not args.no_ssl_verify,
        )
        print(f"\nWrote {len(outputs)} files:")
        for p in outputs:
            print(f"  {p}")


def _detect_windows_with_override(
    events: list[LogEvent], switch_ts: datetime
) -> list[AlgorithmWindow]:
    """Force a two-window split at the given timestamp."""
    rebase_events = [e for e in events if e.kind == "rebase"]
    if not rebase_events:
        return []

    before = [e for e in rebase_events if e.ts < switch_ts]
    after = [e for e in rebase_events if e.ts >= switch_ts]

    windows = []
    if before:
        algo_before = Counter(e.algorithm for e in before).most_common(1)[0][0]
        windows.append(AlgorithmWindow(algo_before, before[0].ts, before[-1].ts))
    if after:
        algo_after = Counter(e.algorithm for e in after).most_common(1)[0][0]
        windows.append(AlgorithmWindow(algo_after, after[0].ts, after[-1].ts))
    return windows


if __name__ == "__main__":
    main()
