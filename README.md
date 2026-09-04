# GitLab Housekeeping Policy Simulator

A policy lab for comparing merge-queue strategies under controlled GitLab-like conditions.

## Purpose

```
Compare merge-queue policies under controlled GitLab-like conditions:
old per-run limit, top-K eligibility, active-cap CI inventory, and Phase 1 optimistic multi-merge.
```

Primary question:

```
What does each policy optimize, and under which queue conditions does it fail?
```

## Architecture

```
scenario YAML
    ↓
stateful fake GitLab API server (FastAPI)
    ↓
gitlab-housekeeping run() with simulator-backed dependencies
    ↓
state mutations: rebase / merge / pipeline / target head
    ↓
metrics NDJSON
    ↓
single-run + policy comparison reports
```

The compatibility harness invokes the upstream `run()` function unchanged and
monkeypatches its GitLab queries and API client to use the simulator. It is a
development harness, not a reverse proxy.

The fake GitLab server is compatible with the real `python-gitlab` path used by `qontract-reconcile`.

## Quick Start

```bash
# Install from the repository root (Python 3.12)
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Validate a scenario
python -m gitlab_hk_sim.cli validate --scenario scenarios/mvp-active-cap.yaml

# Start the fake GitLab server
python -m gitlab_hk_sim.cli serve \
  --scenario scenarios/top-k-poisoned-window.yaml \
  --host 127.0.0.1 \
  --port 8080 \
  --metrics-out reports/active-cap/metrics.ndjson

# In another shell, run the optional qontract-reconcile compatibility harness:
# QONTRACT_RECONCILE_ROOT=/path/to/qontract-reconcile \
# SIM_URL=http://127.0.0.1:8080 \
#   .venv/bin/python run_harness.py

# Generate a report
python -m gitlab_hk_sim.cli report \
  --metrics reports/active-cap/metrics.ndjson \
  --out reports/active-cap/summary.md

# Compare multiple policy runs
python -m gitlab_hk_sim.cli compare \
  --run old=reports/old/metrics.ndjson \
  --run top-k=reports/top-k/metrics.ndjson \
  --run active-cap=reports/active-cap/metrics.ndjson \
  --out reports/comparison.md
```

The server has no authentication. Keep it bound to loopback; non-loopback
binding requires an explicit unsafe override. The harness likewise accepts
only a loopback simulator URL.

## Simulator Control API

While the server is running, advance simulation with:

```bash
# Advance pipeline state by one tick
curl -X POST http://127.0.0.1:8080/__sim/tick

# Get computed metrics
curl http://127.0.0.1:8080/__sim/metrics

# Get current state
curl http://127.0.0.1:8080/__sim/state

# List merge requests merged during this run
curl http://127.0.0.1:8080/__sim/merged_mrs

# Reset to initial scenario
curl -X POST http://127.0.0.1:8080/__sim/reset
```

## Policy Families

| Policy | Controls | Optimizes | Weakness |
|--------|----------|-----------|----------|
| **Old Burst** | Per-run rebase limit | Simple | Active CI can exceed cap over runs |
| **Top-K** | First K eligible | Queue-position purity | Poisoned top window starves CI |
| **Active-Cap** | In-flight CI budget | Steady-state concurrency | No preemption for new high-prio |
| **Phase 1** | Same-root batch merge | MRs per target advance | Requires non-overlapping domains |

## Core Invariant ([qontract-reconcile ADR-019](https://github.com/app-sre/qontract-reconcile/blob/master/docs/adr/ADR-019-merge-queue-acceleration.md))

```
limit controls steady-state CI concurrency, not per-run rebase bursts.
```

Equivalent framing:
- top-K controls eligibility
- active-cap controls useful in-flight CI inventory
- Phase 1 multi-merge consumes same-root green inventory

## Scenarios

| Scenario | Purpose | Expected Winner |
|----------|---------|-----------------|
| `top-k-favorable.yaml` | Clean top window | Top-K ≈ Active-Cap |
| `top-k-poisoned-window.yaml` | Slow/stuck/failed top window | Active-Cap |
| `high-priority-arrival.yaml` | New critical MR arrives | Top-K (preemption) |
| `long-running-ci.yaml` | All slots occupied by slow CI | Tradeoff visible |
| `external-target-advance.yaml` | Target moves externally | Active-Cap limits blast |
| `clean-nonoverlap-phase1.yaml` | Non-overlapping success pool | Phase 1 |
| `overlap-conflict-phase1.yaml` | Shared tenant domains | Phase 1 limited |
| `synthetic-calibration-demo.yaml` | Fabricated calibration-shaped workload | Calibration workflow smoke test |

## Priority Model

MR processing order mirrors production `gitlab-housekeeping`:

```
bot/approved: critical  (highest)
bot/approved: urgent
bot/approved: high
bot/approved: progressive-delivery
bot/approved: medium
bot/approved: low
bot/approved
bot/automerge
auto-merge
lgtm                    (lowest)
```

Ties broken by `approved_at` timestamp (earliest first). All constants live in
`gitlab_hk_sim/state.py` as the single source of truth, imported by both the
server-side Phase 1 module and the driver.

Hold labels (`do-not-merge/hold`, `needs-rebase`, `blocked/bot-access`, `bot/hold`)
prevent merge regardless of priority.

## Scenario Features

### Pipeline Failure Rate

Set `failure_rate` (0.0–1.0) in `pipeline_durations` to model CI flakiness:

```yaml
pipeline_durations:
  min_ticks: 3
  max_ticks: 5
  failure_rate: 0.10   # 10% of pipelines will fail
```

### Merge/Rebase Operation Failure Realism

Set `failure_path_realism` to model API operation failures in merge/rebase
mutations (default is `0.0` for both operations when unset):

```yaml
failure_path_realism:
  merge_failure_rate: 0.001
  rebase_failure_rate: 0.001
```

You can also use a scalar to apply one rate to both:

```yaml
failure_path_realism: 0.001
```

### Deterministic Per-MR Merge Failures

Model repeatable pathological merge failures on specific merge requests:

```yaml
merge_requests:
  - id: 9999
    iid: 753
    merge_failure:
      remaining: always        # or an integer, e.g. 2
      status_code: 405
      detail: 405 Method Not Allowed
```

This is applied before random `failure_path_realism` failures, so deterministic
cases remain reproducible across runs.

### Per-MR CI Duration

Override the global pipeline duration for individual MRs:

```yaml
merge_requests:
  - id: 1
    iid: 1
    ci_duration: 10   # slow test suite, overrides global config
```

### Dynamic MR Arrivals

MRs with `arrival_tick` start hidden and appear mid-simulation:

```yaml
merge_requests:
  - id: 50
    iid: 50
    arrival_tick: 20   # appears at tick 20
    labels: ["bot/approved: critical"]
```

### Tick Length Calibration

By default, one tick is 60 seconds. To align simulator time with production
reconcile cadence, set `tick_seconds` in the scenario:

```yaml
tick_seconds: 82
```

`run_standalone.py` reports both merges/tick and merges/hour using this value.

### Force-Merge (Queue Bypass)

Model a human clicking "Merge" in the GitLab UI — bypasses all queue logic,
pipeline checks, and priority. Immediately merges the MR and advances target:

```yaml
merge_requests:
  - id: 99
    iid: 99
    force_merge_tick: 15   # force-merged at tick 15, invalidates all in-flight CI
```

### MR Cancellation

Model an author closing their MR mid-simulation:

```yaml
merge_requests:
  - id: 42
    iid: 42
    cancel_tick: 30   # closed at tick 30, active pipelines cancelled
```

### External Target Advances

Model external pushes to the target branch (e.g., merges from other projects):

```yaml
scheduled_target_advances:
  10: "external-push-sha-1"   # target advances at tick 10
  25: "external-push-sha-2"   # and again at tick 25
```

## Key Metrics

- **peak_active_pipelines**: Maximum concurrent CI at any tick
- **same_root_success_pool**: Open MRs with valid green pipelines on current target
- **stale_successes**: Pipelines that succeeded on an old target
- **duplicate_rebases**: MRs rebased more than once before merge
- **average_mrs_per_target_advance**: >1 means Phase 1 batching is working

## Repository Compare Semantics

Critical for integration compatibility:

```
GET /api/v4/projects/:id/repository/compare?from=<mr_sha>&to=<target_head>

commits == []  → housekeeping considers MR rebased
commits != []  → housekeeping considers MR not rebased
```

## Queue visualization (NDJSON)

A single-page UI is meant for **walkthroughs in meetings**:
- A **metrics comparison table** (throughput, CI waste, same-root pool, etc.) for every file you load, with the **active** column highlighted
- A **Simulation tab** that surfaces scenario/runtime parameters, embedded calibration targets, arrival profile metadata, and per-policy runtime rollups
- A **kanban board**: one card per merge request, moving through **Waiting → Rebase / CI / Ready → Merged** as you scrub or play
- **Playback** with **Play / Pause / Reset**, **speed (0.25×–4×)**, **Loop** (optional), and a **step scrubber**
- A plain-language **“at this time step”** and **“what housekeeping did”** readout
- **Optional (collapsed) detail** sections for the **stacked bar** chart and **swimlane** (brush and swim range controls only inside that section, so they stay tied to the swimlane)

Durations in the main copy are **time steps** (simulator ticks), not raw log field names.

```bash
make ui
```

In the browser, open or drag-and-drop `metrics.ndjson` (e.g. from `--metrics-out` or `reports/…/metrics.ndjson`). Multiple files open in **tabs** for side-by-side policy comparisons. The brush under the chart only adjusts the **swimlane** window. Chart.js, the nine d3 modules, and js-yaml are vendored in [ui/vendor/](ui/vendor/), so the UI runs offline and makes no third-party requests. The UI sends selected files nowhere; all parsing and rendering happens in the browser.

- **File**: [ui/index.html](ui/index.html) (no build step)
- **Vendored assets**: [ui/vendor/MANIFEST.txt](ui/vendor/MANIFEST.txt) pins every
  third-party file to a version and SHA-384. `make vendor-verify` checks the tracked
  copies offline; `make vendor` re-downloads them and rejects any hash mismatch.

## Standalone Trace Modes

`run_standalone.py` models three runtime modes per policy without a separate
CLI wait flag:

- `regular` (base name): `wait_for_pipeline=False`, `insist=False`
- `-wait`: `wait_for_pipeline=True`, `insist=False`
- `-wait-insist`: `wait_for_pipeline=True`, `insist=True`

Examples:

- `top-k`, `top-k-wait`, `top-k-wait-insist`
- `active-cap`, `active-cap-wait`, `active-cap-wait-insist`
- `old-burst`, `old-burst-wait`, `old-burst-wait-insist`
- `cap+phase1`, `cap+phase1-wait`, `cap+phase1-wait-insist`
- `omm` (configure grouping with `--omm-group-size` and
  `--omm-max-interval`)

Policy presets for comparison/Monte Carlo:

- `phase0`: regular mode only (`top-k`, `active-cap`, `old-burst`)
- `phase1`: `phase0` + `cap+phase1` + `omm` regular
- `all`: every registered trace; OMM currently has regular mode only

`Makefile` comparison targets pass `POLICY_SET` through to `run_standalone.py`
for consistent behavior across quick/advanced/Monte Carlo runs.

## Calibrating From Your Own Logs

The repository ships no operational logs or scenarios derived from them.
`scenarios/synthetic-calibration-demo.yaml` is fully fabricated. Use the
calibration script locally to derive targets from logs you are authorized to
process and generate your own scenario. Calibration
now emits a multidimensional target vector (24h throughput, active-hour
throughput, peak-window throughput, rebase pressure, and merge interval
percentiles) in `metadata.calibration_targets.performance_dimensions`:

```bash
.venv/bin/python scripts/calibrate_from_housekeeping_logs.py \
  --project example-project \
  --logs /path/to/log-a.log /path/to/log-b.log /path/to/log-c.log \
  --emit-scenario scenarios/generated/local-calibrated.yaml

# run old-burst baseline against the calibrated scenario
.venv/bin/python run_standalone.py \
  --compare \
  --policies old-burst \
  --scenario scenarios/generated/local-calibrated.yaml \
  --limit 2 \
  --cycles 480 \
  --ticks-per-cycle 1
```

Tune calibration knobs for any policy (defaults to `old-burst`) and emit
UI-ready calibration CSVs. The tuner now scores candidates against the
multidimensional target vector (not just a single throughput scalar), while
retaining compatibility columns (`rel_error_pct`, `target_mph`) for existing
UI workflows:

```bash
.venv/bin/python scripts/tune_prod_calibration.py \
  --logs /path/to/log-a.log /path/to/log-b.log /path/to/log-c.log \
  --project example-project \
  --policy active-cap \
  --scenario-out scenarios/generated/local-calibrated-active-cap.yaml \
  --grid-out reports/calibration/active-cap-grid.csv \
  --validation-out reports/calibration/active-cap-validation.csv
```

Cycle planning and scoring controls:

- Adaptive cycle planning is on by default (`--cycle-scaling adaptive`) and
  scales tune/validate cycles to log window size with a bounded runtime target.
- Use `--resolution-minutes` (e.g. `60`, `10`, `5`) to control effective
  cycle-planning granularity.
- Candidate ranking can use `--rank-score extended|standard|throughput`
  (`extended` default).
- Final output reports separate gates for throughput tolerance
  (`--tolerance-pct`) and ranking-score tolerance (`--score-tolerance-pct`),
  plus per-dimension error breakdowns.
- Acceptance now includes a max single-dimension guardrail
  (`--max-dimension-error-pct`, default `90`).
- If any acceptance gate fails, calibration is marked **rejected**:
  - `selected-scenario.yaml` is not emitted,
  - `rejected-scenario.yaml` is written for diagnostics,
  - the script exits non-zero so CI/automation cannot treat it as success.
- To force legacy behavior, pass `--cycle-scaling fixed --tune-cycles ... --validate-cycles ...`.

## Discrimination Pass (policy-agnostic)

Run a repeatable stress-variant sweep on top of the tuned calibrated scenario to
force policy separation where possible for any selected policy pair:

```bash
.venv/bin/python scripts/run_discrimination_pass.py \
  --base-scenario scenarios/synthetic-calibration-demo.yaml \
  --policies top-k,active-cap,old-burst \
  --lhs-policy top-k \
  --rhs-policy active-cap \
  --baseline-policy old-burst \
  --cycles 480 \
  --limit 2 \
  --ticks-per-cycle 1
```

`--lhs-policy` and `--rhs-policy` define the delta direction in outputs
(`rhs - lhs`). The optional `--baseline-policy` adds context columns in the
summary.

Outputs are written under `reports/discrimination/<timestamp>/` with:

- `discrimination-summary.csv` (policy-agnostic machine-readable matrix)
- `discrimination-summary.md` (human-readable summary)
- `metadata.json` (timestamp + simulator configuration + optional custom metadata)
- `scenarios/*.yaml` (the generated stress variants)
- `raw/*-compare.txt` (full compare output per variant)

All major run writers emit a sibling `metadata.json` in run output folders.
Generated reports and locally calibrated scenarios are ignored by Git because
they may contain sensitive input-derived values:

- `reports/comparisons/<timestamp>/metadata.json`
- `reports/monte-carlo/<timestamp>/metadata.json`
- `reports/discrimination/<timestamp>/metadata.json`
- `reports/calibration/<timestamp>/metadata.json` (from `tune_prod_calibration.py`)

You can attach custom key/value metadata with repeatable `--metadata KEY=VALUE`
on `run_standalone.py`, `scripts/run_discrimination_pass.py`, and
`scripts/tune_prod_calibration.py`.

### Optional live GitLab enrichment

`scripts/analyze_logs.py plan` can enrich a caller-supplied log export from a
GitLab API. It makes outbound reads only when `GITLAB_TOKEN`, an explicit
`--gitlab-url`, and an explicit `--project-id` are all supplied. Its output
directory then contains an `.mr-cache.json` and reports with merge-request
titles, authors, labels, changed paths, and inferred services. Treat those
files as sensitive; the default `reports/` location is ignored by Git. TLS
verification is enabled by default.

## Running Tests

```bash
PYTHONPATH=. .venv/bin/pytest tests/ -v
```
