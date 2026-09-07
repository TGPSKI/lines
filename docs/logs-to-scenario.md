# From production logs to a calibrated scenario

`docs/log-format.md` describes the input. `docs/golden-scenarios.md` describes
the proof at the end. This is the machinery between them: what each stage reads,
what it derives, what it fabricates, and where it will mislead you.

Every number below was produced by running the stage against
`docs/sample-housekeeping-log.json` — 268 entries spanning 5h27m of one queue.

## The four stages

| Stage | Script | Reads | Writes |
|---|---|---|---|
| 1. Measure | `analyze_logs.py` | any log an adapter reads | a markdown report under `reports/log-analysis/` |
| 2. Generate | `calibrate_from_housekeeping_logs.py` | the same | `scenarios/generated/*.yaml` with `calibration_targets` |
| 3. Tune and gate | `tune_prod_calibration.py` | the same logs | `selected-scenario.yaml` or `rejected-scenario.yaml` |
| 4. Promote | `promote_scenario.py` | a gated run directory | `scenarios/<name>.yaml` with `metadata.proof` |

Stages 1 and 2 read the same thing: records, produced by an adapter from
whatever your logs are. See [One record, two adapters](#one-record-two-adapters).

---

## Stage 1 — Measure

```bash
make analyze-measure LOG_FILE=docs/sample-housekeeping-log.json LOG_ALGORITHM=old-burst
```

Produces the targets everything downstream is judged against. On the sample:

| Metric | Value |
|---|---|
| Window | 2026-05-06 13:01 → 18:26 UTC |
| Total merges | 59 |
| Merges/hr overall | 10.9 |
| Rebase calls | 208 |
| Rebase calls/merge | 3.47x |
| Waste ratio | 71% |

### The window is bounded by rebase lines, not by the file

The sample contains 60 merge lines. The report says 59. The last merge is at
18:28:48; the last rebase is at 18:26:03, and the policy window ends there.
Merges outside a detected window are dropped, because policy attribution is
positional — a merge is assigned to the rebase window containing it.

A log truncated mid-cycle silently loses its trailing merges. Compare the
report's merge count against `grep -c merge_merge_requests` before trusting the
rate.

---

## Stage 2 — Generate

```bash
make calibrate-scenario LOGS=docs/sample-housekeeping-log.json \
  CALIBRATION_PROJECT=example/queue-lab
```

### What it derives from the log

Six aggregates per log, weighted across logs by window hours:

| Derived | From |
|---|---|
| `window_hours` | first to last timestamp the adapter saw, including lines that produced no record |
| `avg_cycle_seconds` | intervals between `cycle` records, capped at 600s. Falls back to the modal gap between events when there are none |
| merge successes and failures | `merge` records, minus a later `merge_failure` for the same iid |
| rebases | `rebase` records |
| hourly and weekday histograms | timestamps of `merge`, `rebase`, `label_change` and `close` records |
| percentile dimensions | merge-to-merge intervals |

### How those become a scenario

**Tick length is the reconcile cycle.** `tick_seconds = max(30, round(avg_cycle_seconds))`.
The floor at 30 exists because a sub-30s tick makes CI durations round to noise.

The sample carries no `cycle` records — its dialect has no cycle marker — so
the cadence is inferred from the modal gap between events: 21.95s, under the
floor, giving `tick_seconds: 30`. That is the estimate, not the loop. A log
with cycle markers measures the real interval; without them, pass
`--tick-seconds-override` with the interval you run.

**The scenario window is a choice, not the log's window.** `--window-ticks`
defaults to 480, so 480 × 30s = 4h — from a 5.5h log. Only *rates* carry over.
`calibration_targets.window_hours` reports the scenario's window; the log's
window is not recorded in the YAML.

**Queue size follows from the rate and the window.**

```
expected_merges = max(30, round(merges_per_hour * window_hours))
```

then a horizon-dependent shaping, because a short scenario with a deep starting
queue reaches artificial steady state and stops responding to arrival shape:

| Scenario window | Seeded at t=0 | Total MRs | Floor at t=0 | Extra floor |
|---|---|---|---|---|
| ≤ 36h | 6% of `expected_merges` | 1.10x | 4 | 10 |
| ≤ 192h | 12% | 1.25x | 8 | 14 |
| > 192h | 18% | 1.35x | 13 | 16 |

`--queue-depth-scale` multiplies both, with hard floors of 10 seeded and
seeded + 8 total. The sample produced **48 MRs, 10 present at t=0**.

**Arrivals are sampled against the observed shape.** Each candidate tick gets a
weight of `hourly_weight × weekday_weight × skew`, where the first two are the
normalised histograms from the log and `skew` is a power curve set by
`--arrival-skew` (default 0.85, clamped to 0.4–2.5; >1 pulls arrivals earlier).
Scenario start hour is the measured peak-8 window start for windows ≥22h, and a
weighted draw otherwise; start weekday is the busiest weekday for windows <120h.

**Initial queue composition scales with rebase pressure.**

```
rebase_pressure = rebases_per_hour / merges_per_hour
stale_share     = clamp(0.04 + 0.008 * rebase_pressure, 0.08, 0.14)
```

`ready` and `running` shares are set by horizon (0.24/0.16 at ≤36h up to
0.40/0.24 above 192h); `no_pipeline` takes the remainder, clamped to 0.22–0.45.
The sample got `ready 0.24, running 0.16, stale_success 0.08, no_pipeline 0.45`.

### What it fabricates

The generator copies no identities out of the logs. These are authored constants
in `calibrate_from_housekeeping_logs.py`, identical for every input:

| Fabricated | Value |
|---|---|
| Priority labels | `bot/approved: critical` 5, `high` 15, `medium` 30, `low` 30, `bot/approved` 15, `lgtm` 5 |
| Tenant labels | `tenant-alpha` … `tenant-zeta`, one each, plus a second with p=0.15 |
| CI durations | 5min 5, 8min 25, 12min 35, 18min 25, 25min 10, converted to ticks |
| Pipeline failure rate | 0.05 |
| API failure rate | 0.01, on both merge and rebase |
| MR titles, SHAs, `approved_at` | `synthetic/calibrated-change-NNN`, `mrN-sha-001`, 3 minutes apart from 2024-01-01T09:00Z |
| Target advance pool | `target-002` … `target-419`, 418 SHAs regardless of window |

**CI duration is the significant one.** `docs/scenario-schema.md` states that CI
length dominates policy behaviour more than any other input, and these logs
cannot supply it: `gitlab_housekeeping` reads the GitLab API, not the CI
system. `ci_model: fixed-minutes-distribution` in the emitted metadata
records that the distribution is authored. The 5–25 minute spread is close
enough for policy ranking, and the scenario says so in its own metadata. Quote
it when the result depends on CI cost.

**At least one merge failure is always injected.**
`max(1, round(expected_merges * merge_failure_rate))`. The sample observed a
failure rate of 0.0 and still got one failing MR (iid 87), drawn from the back
55% of the queue. `observed_merge_failure_rate: 0.0` in the metadata is the
honest record; the scenario is one failure less optimistic than the log.

### Verified output

The sample, read directly — stage 2 takes the same file stage 1 measured:

```
window_hours=5.458
merge_success=60/60 (10.993 per hour)
rebases=208 (38.109 per hour) rebase_limit_hits=0
avg_cycle_seconds=21.95
dimensions: merge24h=10.993 active=10.000 peak8=10.000 peak8_p90=12.000
             rebase/merge=3.467 peak_ratio=3.467 offpeak_ratio=0.000 interval_p95_s=517.0
```

`3.467` rebases per merge matches stage 1's `3.47x` independently. Stage 2
counts 60 merges where stage 1 reported 59: stage 1 drops the merge that falls
outside the last rebase window, and stage 2 has no windows to drop it from.

`offpeak_ratio=0.000` is the trap. A 5.5h log has no off-peak hours, so the
dimension is measured as zero and written into `calibration_targets` as a target.
Under the default `hourly-extended` profile, `merge_peak_offpeak_ratio` carries
17% of the tuner's score — the second-heaviest weight — against a target the log
could not measure. **A log shorter than a day should be tuned with
`--dimension-profile legacy`**, which does not include that dimension.

---

## Stage 3 — Tune and gate

```bash
make tune-calibration LOGS=... CALIBRATION_PROJECT=... CALIBRATION_POLICY=old-burst
```

Grid-searches `--tick-seconds-candidates` (default: derived from the logs),
`--arrival-skew-candidates` (`0.9,1.05`) and `--queue-depth-candidates`
(`0.95,1.05`) at `--tune-cycles`, then re-runs the best `--validate-top-n`
(3) candidates at `--validate-cycles`. `--ci-duration-scale-candidates` is
accepted and ignored.

### Scoring

Two dimension-weight profiles:

| Dimension | `hourly-extended` (default) | `legacy` |
|---|---|---|
| `merge_per_hour_24h` | 0.10 | 0.30 |
| `merge_per_hour_active_hours` | 0.20 | 0.20 |
| `merge_per_hour_peak8` | 0.20 | 0.20 |
| `merge_per_hour_peak8_p90` | 0.15 | 0.15 |
| `merge_peak_offpeak_ratio` | 0.17 | — |
| `rebase_per_merge_global` | 0.12 | 0.10 |
| `merge_interval_seconds_p50` | 0.01 | — |
| `merge_interval_seconds_p95` | 0.05 | 0.05 |

`--rank-score extended` (default) ranks on
`0.70 × standard_score + 0.20 × max_dimension_error + 0.10 × throughput_rel_error`.
`standard` uses the weighted score alone; `throughput` uses relative throughput
error alone.

### The gates

| Gate | Passes when | Default |
|---|---|---|
| `throughput_pass` | throughput relative error ≤ `--tolerance-pct` | 5.0 |
| `score_pass` | ranking score ≤ `--score-tolerance-pct` | 30.0 |
| `max_dimension_pass` | worst single dimension ≤ `--max-dimension-error-pct` | 90.0 |

All three must pass for `accepted`. Failures are recorded as
`decision_reason_codes`: `throughput_gate_failed`, `rank_score_gate_failed`,
`max_dimension_gate_failed`.

The default `max_dimension_error_pct` of 90 is loose on purpose — merge-interval
percentiles are hard to reproduce and carry little weight. It is also why a
passing calibration can still be 52% off on one dimension, which is the case
`docs/golden-scenarios.md` works through.

On acceptance the run writes `selected-scenario.yaml`. On rejection it writes
`rejected-scenario.yaml` and exits non-zero.

---

## Stage 4 — Promote

```bash
python scripts/promote_scenario.py reports/calibration/<run> --name <scenario-name>
```

Copies the scenario into `scenarios/` with `metadata.proof` carrying the gates,
the thresholds, every dimension's error, and the worst one by name. Refuses a
rejected run unless `--force-rejected`, which records the failure in the proof.

See `docs/golden-scenarios.md` for what to do with a rejection and how to quote
a result from a scenario that passed on a relaxed gate.

---

## One record, two adapters

Both entry points read records. An adapter turns a log into them, and the two
that ship read the two qontract-reconcile dialects — the JSON export and the
pod text log. `docs/log-format.md` documents the record and both adapters.

The dialect is detected from the file, so either stage takes either log:

```bash
make convert-log LOG_FILE=docs/sample-housekeeping-log.json \
  RECORDS_OUT=reports/log-analysis/records.ndjson
```

Converting is optional — the stages call the same adapter themselves — but the
counts it prints are the check that the adapter matched your format at all.

For a log that is neither dialect, write one adapter and both stages read it.
What each dialect cannot carry is the thing to check first: the JSON export has
no cycle marker, no label change and no merge failure, so from it the tick
length is an estimate and the observed failure rate is 0 by construction.

---

## Traps, collected

| Trap | Symptom | Check |
|---|---|---|
| Adapter missed the format | few records for many entries; `no parseable log lines` | `make convert-log` and read the counts |
| No cycle records | `tick_seconds: 30` and a window shorter than intended | `avg_reconcile_cycle_seconds` in the emitted metadata against the loop you run |
| Window bounded by rebase lines | stage 1 merge count below `grep -c` | last rebase timestamp vs last merge timestamp |
| Scenario window is a choice | `calibration_targets.window_hours` unlike the log | `window_ticks × tick_seconds / 3600` |
| Short log, zero off-peak | `offpeak_ratio=0.000`, tuner scores against it | use `--dimension-profile legacy` under 24h |
| Forced merge failure | one failing MR with `observed_merge_failure_rate: 0.0` | `merge_failure` keys in the emitted YAML |
| Target pool is fixed at 418 | a long run stops advancing the target head | `sha_pools.target_advances.master` length vs expected merges |
| CI is authored | policy ranking on CI cost rests on a synthetic distribution | `ci_model` and `ci_minutes_range` in the metadata |
