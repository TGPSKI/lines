# UI tour

Every reachable view, sub-view and interaction state of [ui/index.html](../ui/index.html),
captured from one 24h run of `scenarios/showcase-10h.yaml` across five policies.

The captures are automated and deterministic: fixed viewport and device scale,
Chart.js animation disabled before first render, the playhead parked on one
tick, and every wait a predicate poll rather than a sleep. Re-running the
capture on unchanged data reproduces the same bytes.

Open the UI on this data yourself with `make ui`.

## Load

![The Load tab: a drop zone for NDJSON traces, and a run browser listing report
directories grouped by category.](media/ui/tab-load.png)

Drag NDJSON in, or point the run browser at a `reports/` directory. Parsing and
rendering happen in the browser; nothing is uploaded.

## Statistics — the verdict

![The Statistics tab. A verdict band compares omm against old-burst in two
columns: tenant (median time to merge 9m vs 14m, p95 tail 15m vs 1h21m, worst
MR 59m vs 3h54m, MRs waiting over 100 ticks 1 vs 10) and platform (rebases per
merge 1.70 vs 3.15, wasted rebases 77 vs 230, CI failures 2 vs 8, peak CI
concurrency 8 vs 11). Below it, a metrics table shows throughput of 4.502
merges/hour for three of five policies.](media/ui/stats-verdict.png)

Throughput is 4.502
merges/hour for three of five policies and 108 MRs merged for four of five —
read that alone and nothing happened. The same run moves the median MR from
14m to 9m and the p95 tail from 1h21m to 15m.

Both sides are selectable; the baseline defaults to `old-burst`, the pre-OMM
production policy.

![The standard metrics table across five policies, grouped into throughput,
CI, pool and batch sections, with the best value per row
highlighted.](media/ui/stats-metrics.png)

![Extended mode, adding peak-window and off-peak throughput, arrival rates per
window, and merge-interval percentiles.](media/ui/stats-metrics-extended.png)

Extended mode separates peak from off-peak. A policy that looks equivalent over
24h can differ inside the peak window.

![Median time-to-merge and p95 tail latency broken out by priority
label.](media/ui/stats-perlabel.png)

Per-priority, because an average hides whether the tail belongs to
low-priority work or to everyone.

## Kanban — the replay

![The Kanban tab: one board per policy, cards moving through Queue, Rebasing,
CI, Ready, Stale and Merged as the step counter advances.](media/ui/tab-kanban.png)

One card per merge request, one board per policy, one playhead across all of
them. This is the only surface that follows the playhead — everything else
reads a finished run.

![A kanban card tip showing that MR's state, labels and
timings.](media/ui/state-kanban-card-tip.png)

## Trace, All Policies, Swimlane

![The Trace tab: a stacked composition chart with per-MR swimlanes inline
beneath it.](media/ui/tab-composition.png)

![The All Policies tab: one stacked-bar composition panel per policy, on a
shared y axis.](media/ui/tab-bars.png)

Shared y axis, so panel heights are comparable rather than each self-scaled.

![The Swimlane tab: one lane per merge request across the full run, segments
coloured by state.](media/ui/tab-swimlane.png)

### Windowing a long run

![The swimlane at full range: a window control above it showing the whole
2879-tick run, with an overview strip and Full, Peak, Off-peak, First quarter
and Last quarter presets.](media/ui/state-window-full.png)

A 24h run at 30s ticks is 2880 columns. Everything renders, but one CI pipeline
is under half a pixel wide.

![The same swimlane windowed to the peak: the overview strip shows the selected
span, and the lane count drops to those with activity inside
it.](media/ui/state-window-peak.png)

Drag the overview strip or take a preset. The window is global, so the Trace,
All Policies and Swimlane tabs stay comparable. A windowed swimlane drops lanes
with nothing inside the window, and the All Policies stat lines and y axis are
clipped to it — a mean drawn across a peak-only view but computed over all 24
hours would state something false.

### Interaction states

![Hovering a swimlane segment: a tooltip with the MR, its state, priority,
service labels and tick range.](media/ui/state-swimlane-hover.png)

![Clicking a swimlane segment locks a detail panel with a tick scrubber bounded
to that MR's own event range, its counters, and its recent transition
history.](media/ui/state-swimlane-lock.png)

The scrubber is bounded to the MR's own first and last event, so scrubbing
cannot leave the lane.

![A tooltip on the Trace composition chart.](media/ui/state-composition-tooltip.png)

![A tooltip on an All Policies panel.](media/ui/state-bars-tooltip.png)

## Simulation

![The Simulation tab, scenario view: MR count, tick length, CI duration
distribution, failure rates and run duration.](media/ui/simulation-scenario.png)

![The calibration view: the scenario's embedded calibration targets beside what
this run produced.](media/ui/simulation-calibration.png)

A scenario promoted from real logs carries its targets. This view is where a
run says whether it reproduced them.

![The metadata view: provenance, seed and generation
parameters.](media/ui/simulation-metadata.png)

## Experiments

![The discrimination view: variant scenarios scored on whether they separate
two policies.](media/ui/experiments-discrimination.png)

Which scenarios actually tell two policies apart, so a comparison is not run on
a scenario where every policy scores the same.

![The calibration view: a parameter grid with validation
results.](media/ui/experiments-calibration.png)

## Monte Carlo

![The Monte Carlo tab: per-trial distributions, pairwise comparison, rank
summary and a winner heatmap across metrics.](media/ui/tab-monteCarlo.png)

Repeated trials, because a single run's winner can be noise.

## Sweep

![The Sweep tab on the limit axis, with an axis picker, a noise-floor line, and
grouped bar and convergence charts.](media/ui/sweep-axis-limit.png)

![The Sweep tab on the OMM window axis.](media/ui/sweep-axis-omm-max-interval.png)

Each document varies one parameter and pins the other. The noise-floor line
names the policies that **cannot** read the swept flag — their spread across the
axis is this scenario's run-to-run noise, and any claimed effect has to clear
it. On the OMM window axis every policy but `omm` is a control.

## Multi-Merge

![The Multi-Merge statistics view: how often consecutive merges touch
non-overlapping services.](media/ui/multiMerge-statistics.png)

![The simulator view: group formation under the non-overlap
rule.](media/ui/multiMerge-simulator.png)

![The explorer view: per-MR service labels and overlap
detail.](media/ui/multiMerge-explorer.png)

Optimistic multi-merge only helps when consecutive merges touch different
services. These views measure that independence rate on the loaded data.
