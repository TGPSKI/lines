# lines

[UI tour](docs/ui-tour.md) | [methodology](docs/methodology.md) | [scenario schema](docs/scenario-schema.md) | [logs to scenario](docs/logs-to-scenario.md) | [ADR-019](https://github.com/app-sre/qontract-reconcile/blob/master/docs/adr/ADR-019-merge-queue-acceleration.md) | [pate.sh](https://pate.sh)

**A merge-queue policy lab - design a strategy, calibrate it against your own
logs, and watch it run.**

Compare merge-queue policies under controlled GitLab-like conditions: per-run
rebase limit, top-K eligibility, active-cap CI inventory, and optimistic
multi-merge. Promote your production logs into a scenario, prove the scenario
reproduces what you measured, then run every policy against it and read the
result as a tenant feels it - time to merge and the p95 tail, not throughput.

`lines` was built to develop optimistic multi-merge
([ADR-019](https://github.com/app-sre/qontract-reconcile/blob/master/docs/adr/ADR-019-merge-queue-acceleration.md))
for `gitlab_housekeeping`, the merge queue in
[qontract-reconcile](https://github.com/app-sre/qontract-reconcile) that serves
app-interface - Red Hat AppSRE's GitOps monorepo.

![Three merge-queue policies replayed on one playhead: kanban boards for
active-cap, old-burst and top-k, each card a merge request moving through
Queue, Rebasing, CI, Ready, Stale and Merged as the step counter advances.
old-burst holds visibly more merge requests in CI than the other
two.](docs/media/replay-hero.gif)

`lines` provides two packages:

* **`mqsim`** is the policy simulator: it drives
policies, records metrics, and renders them in a browser UI called **Merge
Queue Sim**.
* **`glab_api`** is the fake GitLab API server it drives them
against — a scenario-loaded FastAPI service that speaks the same shapes as the
real thing.

## Purpose

**What does each policy optimize, and under which queue conditions does it
fail?**

You cannot A/B a merge queue.

One queue serves every tenant, a policy change is
global, and the counterfactual - *what the other policy would have done with the
same arrivals* - is never observable in production.

A calibrated simulator is
how you get that counterfactual, and calibration is what makes the answer worth
anything. [docs/methodology.md](docs/methodology.md) provides a deep dive.

## Architecture

```
scenario YAML
    ↓
glab_api: stateful fake GitLab API server (FastAPI)
    ↓
gitlab-housekeeping run() with simulator-backed dependencies
    ↓
state mutations: rebase / merge / pipeline / target head
    ↓
metrics NDJSON
    ↓
mqsim: single-run + policy comparison reports
```

The compatibility harness invokes the upstream `run()` function unchanged and
monkeypatches its GitLab queries and API client to use the simulator. The fake
GitLab server is compatible with the real `python-gitlab` path
`qontract-reconcile` uses — see [Harness mode (provenance)](#harness-mode-provenance).

## Documentation

| Document | What it answers |
|---|---|
| [docs/log-format.md](docs/log-format.md) | The record every stage reads, the two shipped log adapters, and what writing your own takes |
| [docs/methodology.md](docs/methodology.md) | Why the pipeline is shaped this way: the counterfactual production cannot provide, why CI duration is not a knob, and what a passing calibration does and does not prove |
| [docs/logs-to-scenario.md](docs/logs-to-scenario.md) | The four stages from a production log to a proofed scenario: what each derives, what it fabricates, and the traps between them |
| [docs/scenario-schema.md](docs/scenario-schema.md) | Every field a scenario YAML can carry, and the three in `metadata` that are read rather than stored |
| [docs/golden-scenarios.md](docs/golden-scenarios.md) | What makes a scenario trustworthy, how to author a probe, and why the proof must travel with it |

Three agent-executable patterns live in `.agents/skills/`:

| Pattern | Use it when |
|---|---|
| `calibrate-and-compare` | walking the arc from your own logs to a policy recommendation |
| `queue-behaviour-triage` | a run's numbers contradict what you expected |
| `findings-readout` | a comparison has run and you need what it means, not what it measured |

## Running it

`make` targets wrap each of these; use whichever you prefer.

```bash
# Install from the repository root (Python 3.14)
python3.14 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Validate a scenario
python -m glab_api.cli validate --scenario scenarios/mvp-active-cap.yaml

# Start the fake GitLab server
python -m glab_api.cli serve \
  --scenario scenarios/top-k-poisoned-window.yaml \
  --host 127.0.0.1 \
  --port 8080 \
  --metrics-out reports/active-cap/metrics.ndjson

# Generate a report
python -m mqsim.cli report \
  --metrics reports/active-cap/metrics.ndjson \
  --out reports/active-cap/summary.md

# Compare multiple policy runs
python -m mqsim.cli compare \
  --run old=reports/old/metrics.ndjson \
  --run top-k=reports/top-k/metrics.ndjson \
  --run active-cap=reports/active-cap/metrics.ndjson \
  --out reports/comparison.md
```

The server has no authentication. Keep it bound to loopback; non-loopback
binding requires an explicit unsafe override. The harness likewise accepts
only a loopback simulator URL.

## The pipeline

Six stages, in the order you run them. Each links the section that documents it.

1. [Scrape your logs into a scenario](#scrape-your-logs-into-a-scenario) — derive
   targets from logs you are authorised to process
2. [What a scenario can express](#scenarios) — arrivals, priorities, CI duration,
   failure modes
3. [Calibrate and gate it](#calibrate-and-gate-it) — tune knobs until the scenario
   reproduces what you measured, and reject it when it does not
4. [Prove it on a golden scenario](docs/golden-scenarios.md) — the proof travels
   with the scenario
5. [Test the policies](#policy-families) — every policy against the same arrivals
6. [Compare across axes](#key-metrics) — throughput, CI cost, wait time, the p95
   tail, and the tradeoffs between them

`.agents/skills/calibrate-and-compare` walks this end to end.

## Scrape your logs into a scenario

The repository ships no operational logs or scenarios derived from them.
`scenarios/synthetic-calibration-demo.yaml` is fully fabricated. Use the
calibration script locally to derive targets from logs you are authorized to
process and generate your own scenario. It writes a multidimensional target
vector — 24h throughput, active-hour throughput, peak-window throughput, rebase
pressure and merge-interval percentiles — into
`metadata.calibration_targets.performance_dimensions`:

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
| `mvp-active-cap.yaml` | 5 MRs; the smallest thing that runs | Smoke test, and what the harness drives |
| `large-mixed-queue.yaml` | 100 MRs, mixed priorities | Load, not a specific policy |
| `large-mixed-queue-advanced.yaml` | 120 MRs over 480 ticks with staggered arrivals, variable CI, failures, pushes and force-merges | Every scenario field at once |
| `showcase-10h.yaml` | 108 MRs over a 24h horizon, 23 tenants, peak-centred arrivals | The comparison this README quotes |

All twelve are fabricated; none derives from operational logs.

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
`src/glab_api/state.py` as the single source of truth, imported by both the
Phase 1 module and the driver.

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

## Calibrate and gate it

Tune calibration knobs for any policy (defaults to `old-burst`) and emit
UI-ready calibration CSVs. The tuner scores candidates against the whole target
vector rather than throughput alone, and the CSVs carry `rel_error_pct` and
`target_mph` columns for the UI:

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
- Acceptance includes a max single-dimension guardrail
  (`--max-dimension-error-pct`, default `90`).
- If any acceptance gate fails, calibration is marked **rejected**:
  - `selected-scenario.yaml` is not emitted,
  - `rejected-scenario.yaml` is written for diagnostics,
  - the script exits non-zero so CI/automation cannot treat it as success.
- To force legacy behavior, pass `--cycle-scaling fixed --tune-cycles ... --validate-cycles ...`.

## Policy Families

| Policy | Controls | Optimizes | Weakness |
|--------|----------|-----------|----------|
| **Old Burst** | Per-run rebase limit | Simple | Active CI can exceed cap over runs |
| **Top-K** | First K eligible | Queue-position purity | Poisoned top window starves CI |
| **Active-Cap** | In-flight CI budget | Steady-state concurrency | No preemption for new high-prio |
| **Phase 1 / OMM** | Group lead + pending, `skip_ci` rebase, non-overlapping tenants | MRs per target advance without re-running CI | Group lost to an external merge, lead failure, or window expiry |

**Phase 1 and OMM are the same feature** — optimistic multi-merge — and it is
what production runs today. Two traces sit at different points in its history:
`cap+phase1` modelled the concept well before OMM shipped, an upper-bound
estimate that batches non-overlapping same-root successes within a single
cycle. `omm` models the protocol that concept became in production — a merged
lead, `omm-pending` labels, skip-CI rebase at group formation, dynamic
expansion each cycle, and invalidation only on an external merge. Neither is a
replica: the simulator has never tracked production 1:1, and does not try to.

`old-burst` is the pre-OMM per-run-limit behavior, kept as a comparison point
rather than as a description of production.

The default policy set is `phase0` — `old-burst`, `top-k`, `active-cap` — so
`make compare` and the Monte Carlo targets do not exercise it. Use
`POLICY_SET=phase1` or `all` to include it.

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
- `omm` (group window: `--omm-max-interval`; group size is bounded only by
  tenant non-overlap, as upstream bounds it)

Policy presets for comparison/Monte Carlo:

- `phase0`: regular mode only (`top-k`, `active-cap`, `old-burst`)
- `phase1`: `phase0` + `cap+phase1` + `omm` regular
- `all`: every registered trace; OMM currently has regular mode only

`Makefile` comparison targets pass `POLICY_SET` through to `run_standalone.py`
for consistent behavior across quick/advanced/Monte Carlo runs.

## Key Metrics

Keys as a run's `run_summary` event carries them:

- **`peak_active_pipelines`**: maximum concurrent CI at any tick
- **`same_root_success_pool_p95`** / **`_max`**: open MRs with a green pipeline
  on the current target
- **`stale_success_max`**: pipelines that succeeded on a target since replaced
- **`duplicate_rebase_total`**: rebases beyond the first for one MR
- **`avg_mrs_per_merge_cycle`**: above 1 means multi-merge batched

`src/mqsim/metrics.json` carries the label, unit and `higher_is_better`
direction for the subset the UI displays; `stale_success_max` is summary-only.

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

## Queue visualization (NDJSON)

A single-page UI is meant for **walkthroughs in meetings**. Every view is
captured in the [UI tour](docs/ui-tour.md).

![The Statistics tab's verdict band, comparing omm against old-burst. Tenant
column: median time to merge 9m vs 14m, p95 tail 15m vs 1h21m, MRs waiting over
100 ticks 1 vs 10. Platform column: rebases per merge 1.70 vs 3.15, wasted
rebases 77 vs 230. Below it a metrics table shows throughput of 4.502
merges/hour for three of five policies.](docs/media/ui/stats-verdict.png)

Statistics opens on this. On a 24h run the throughput table is near-flat —
4.502 merges/hour for three of five policies, 108 MRs merged for four of five —
while the median MR merges in 9m instead of 14m and the p95 tail falls from
1h21m to 15m. Throughput is what the fleet accounts for; wait time is what a
tenant feels.

- A **metrics comparison table** (throughput, CI waste, same-root pool, etc.) for every file you load, with the **active** column highlighted
- A **Simulation tab** that surfaces scenario/runtime parameters, embedded calibration targets, arrival profile metadata, and per-policy runtime rollups
- A **kanban board**: one card per merge request, moving through **Queue → Rebasing → CI → Ready → Stale → Merged** as you scrub or play
- **Playback** with **Play / Step / Reset**, **speed (0.25×–4×)**, **Loop** (optional), and a **step scrubber**
- A plain-language **“at this time step”** and **“what housekeeping did”** readout
- Separate **Trace**, **All Policies** and **Swimlane** tabs: the metric chart with
  per-MR swimlanes inline beneath it, the stacked-bar composition, and the full-run
  swimlane. These read a finished run, so they do not follow the playhead — only the
  kanban board and its readout do
- A **window control** on those three tabs. A 24h run at 30s ticks is 2880
  columns, where one CI pipeline is under half a pixel. Drag the overview strip
  or take a Peak / Off-peak / quarter preset; the window is shared across the
  three tabs, and the All Policies stat lines and y axis are clipped to it

Durations in the main copy are **time steps** (simulator ticks), not raw log field names.

```bash
make ui
```

In the browser, open or drag-and-drop `metrics.ndjson` (e.g. from `--metrics-out` or `reports/…/metrics.ndjson`). Multiple files open in **tabs** for side-by-side policy comparisons. Chart.js, the nine d3 modules, and js-yaml are vendored in [ui/vendor/](ui/vendor/), so the UI runs offline and makes no third-party requests. The UI sends selected files nowhere; all parsing and rendering happens in the browser.

- **File**: [ui/index.html](ui/index.html) (no build step)
- **Vendored assets**: [ui/vendor/MANIFEST.txt](ui/vendor/MANIFEST.txt) pins every
  third-party file to a version and SHA-384. `make vendor-verify` checks the tracked
  copies offline; `make vendor` re-downloads them and rejects any hash mismatch.

## Harness mode (provenance)

Not a stage in the pipeline above. It is the evidence the arc rests on — that
the policies compared here are the ones the real integration runs — and
evidence has to be checkable once, not re-executed by every reader. Everything
else in this repository runs without it.

`pip install -e ".[harness]"` pulls qontract-reconcile at commit
[`5d6cf91`](https://github.com/app-sre/qontract-reconcile/commit/5d6cf917ab73472068ff746196f13c5e52662508)
into the same interpreter — the integration is imported into this process, so
one environment has to satisfy both. It is not on PyPI and has no usable tag:
the newest, 0.10.1 (2024-12-10), predates `gitlab_housekeeping.py`. Two of its
dependencies are uv workspace members of that repo, pinned here by git
subdirectory because pip would otherwise take unrelated packages of those
names from PyPI. `--qontract-reconcile-root /path/to/checkout` remains the
alternative; without either, the harness exits saying so.

Validated 2026-09-04 on Python 3.14.6: qontract-reconcile 0.10.2.dev859 at
that commit rebased three merge requests against
`scenarios/mvp-active-cap.yaml`, then merged MR 1 and advanced the target head
after four ticks. Everything else in this repository runs without it.

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

## Repository Compare Semantics

How `gitlab_housekeeping` decides whether an MR is rebased:

```
GET /api/v4/projects/:id/repository/compare?from=<mr_sha>&to=<target_head>

commits == []  → housekeeping considers MR rebased
commits != []  → housekeeping considers MR not rebased
```

## Running Tests

```bash
make test          # or: PYTHONPATH=. .venv/bin/pytest tests/ -v
```

## History

| Date | Event |
|---|---|
| 2026-03-05 | [ADR-019](https://github.com/app-sre/qontract-reconcile/pull/5439) opened, proposing optimistic non-overlapping multi-merge. Merged 2026-04-15 |
| 2026-04-23 | First OMM implementation PR ([#5508](https://github.com/app-sre/qontract-reconcile/pull/5508)) opened. Merged 2026-05-06 |
| 2026-04-29 | First simulator commit, `2138aec` in this history |
| 2026-05-01 | Simulator public on the [`tgpski-gitlab-housekeeping-performance-simulator`](https://github.com/TGPSKI/qontract-reconcile/tree/tgpski-gitlab-housekeeping-performance-simulator/tools/gitlab_housekeeping_perf_sim) branch of the qontract-reconcile fork |
| 2026-09-04 | Harness validated against qontract-reconcile `5d6cf91` |
| 2026-09-07 | Published as `TGPSKI/lines` |

Speculative multi-merge is older than this repository. Zuul's dependent
pipelines have tested changes against the assumed success of the changes ahead
of them since 2012, and Uber's SubmitQueue design, published at EuroSys 2019
as [Keeping Master Green at Scale](https://dl.acm.org/doi/10.1145/3302424.3303970),
prunes the speculation tree by a build-target conflict graph. What this
repository adds is the harness that runs the production integration unchanged
against the fake server, and the calibration gate that rejects a scenario
before any policy comparison is read from it.

## Acknowledgments

Built by Tyler Pate ([@TGPSKI](https://github.com/TGPSKI)), who wrote
[ADR-019](https://github.com/app-sre/qontract-reconcile/blob/master/docs/adr/ADR-019-merge-queue-acceleration.md),
and Ryan Hur ([@rhur-pixel](https://github.com/rhur-pixel)), who implemented
optimistic multi-merge as his first project on the AppSRE team.

Reviewed and refined in design and code review by
Di Wang ([@hemslo](https://github.com/hemslo)), Christian Assing ([@chassing](https://github.com/chassing)),
Karl Fischer ([@fishi0x01](https://github.com/fishi0x01)), Esron Silva ([@esron](https://github.com/esron)),
Feng Huang ([@BumbleFeng](https://github.com/BumbleFeng)), and Suzana Nesic ([@suzana-nesic](https://github.com/suzana-nesic)).

Built on the foundation of `gitlab_housekeeping` and the wider
`qontract-reconcile` project — thanks to Jaime Melis
([@jmelis](https://github.com/jmelis)), Maor Friedman
([@maorfr](https://github.com/maorfr)), and everyone who has contributed to the
AppSRE codebase.
