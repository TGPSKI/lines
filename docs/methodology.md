# Methodology

Why this pipeline exists, and what a result from it is worth.

[docs/logs-to-scenario.md](logs-to-scenario.md) is the mechanics of each stage.
[docs/golden-scenarios.md](golden-scenarios.md) is the proof at the end. This is
the argument connecting them.

## The problem: a merge queue admits no A/B test

A reconciler runs one policy at a time against one queue. Policy windows are
sequential, so a comparison drawn from production logs is confounded by
everything else that moved between them — queue depth, arrival shape, CI load,
the day of the week. Tuesday under old-burst and Thursday under active-cap are
two different experiments with one variable each, both unmeasured.

You cannot re-run Tuesday with the other policy. That counterfactual is gone the
moment the reconciler acts, and no amount of statistics recovers it.

A simulator manufactures it. Same scenario, same seed, same arrivals, same CI
durations — only the policy differs. Every difference in the output is then
attributable to the policy, because nothing else was allowed to vary.

That holds only if the scenario resembles the system it claims to model,
which is what the first loop is for.

## Two loops

**Calibration** earns the right to run **analysis**:

```
logs ──→ targets ──→ scenario ──→ simulate ──→ score ──→ accept?
                         ↑                                 │
                         └────────────── reject ───────────┘

golden scenario ──→ policy A ─┐
                ──→ policy B ─┼──→ compare
                ──→ policy C ─┘
```

## The five steps

| Step | What it does | Artifact |
|---|---|---|
| 1. Ingest | measured rates, temporal shape, failure counts | `scripts/analyze_logs.py`, `mqsim.adapters` |
| 2. Generate | a scenario carrying those rates as `calibration_targets` | `scripts/calibrate_from_housekeeping_logs.py` |
| 3. Score | weighted error across every target dimension | `scripts/tune_prod_calibration.py` |
| 4. Gate | accept, or reject with the failing dimension named | same, `decision_reason_codes` |
| 5. Compare | one scenario, one seed, many policies | `run_standalone.py --compare` |

### What is observed and what is searched

Not every input is in the logs. The split decides which knobs the tuner is
allowed to turn:

| Parameter | In the logs? | How it is set |
|---|---|---|
| Merge and rebase rates | yes | measured directly |
| Hourly and weekday shape | yes | measured directly |
| Merge failures | yes | measured directly |
| Cycle interval | sometimes | measured from cycle markers, else estimated |
| Arrival burstiness | no | grid search (`--arrival-skew-candidates`) |
| Queue depth | no | grid search (`--queue-depth-candidates`) |
| CI duration | no | authored — see below |

A parameter that is neither measured nor searched is a constant, and
`docs/logs-to-scenario.md` lists every one of them.

## CI duration is not a tunable parameter

It is a physical property of the build system, and treating it as a knob breaks
the method.

An early calibration compressed CI durations to close the gap on throughput. The
throughput numbers matched. The policy comparison became meaningless, because
policies that differ in how they spend CI time were no longer under realistic
time pressure — the thing they are being compared on had been tuned away. That
is why `--ci-duration-scale-candidates` is still accepted by the tuner and
deliberately ignored.

The reconciler's own logs cannot supply the distribution: `gitlab_housekeeping`
reads the GitLab API, not the CI system. So the generator authors one and
declares it as `ci_model: fixed-minutes-distribution` in the scenario's own
metadata. A result that turns on CI cost rests on that distribution — quote it
when you quote the result.

## Gates, and why they are explicit

A scenario becomes a golden scenario only by passing three:

| Gate | Default | Why |
|---|---|---|
| Throughput error | ≤ 5% | the headline metric has to be right |
| Ranking score | ≤ 30% | weighted quality across every dimension |
| Worst single dimension | ≤ 90% | no dimension may be catastrophically wrong |

A rejected run writes `rejected-scenario.yaml` and exits non-zero, naming the
gate that failed. The thresholds ship as defaults rather than as a house style,
so a reader can disagree with one and re-run.

## The honest-mismatch principle

Some dimensions cannot match, for reasons the model can name.

Merge-interval p50 is the standing example. Realistic CI duration puts a floor
under how fast a merge cycle can complete; a production queue that beats that
floor is doing something the model does not represent — cached artifacts,
partial pipeline reuse, concurrent merges. The gap is not noise and it is not a
tuning failure.

So the scenario reports it. `docs/golden-scenarios.md` works through a run that
passes with `worst_dimension_error_pct: 52.41`, and the proof carried in
`metadata.proof` names the worst dimension rather than averaging it away.

"Calibrated to within 5% on throughput and 52% on merge-interval p50" is a
stronger claim than "calibrated," because a reader can check where it fails.

## What the model does not capture

| Not modelled | Consequence | What is done instead |
|---|---|---|
| CI internals — caching, shared runners, contention | merge intervals have a floor production may beat | CI as a declared fixed distribution |
| Human action — force-merges, cancels, pushes | only automated reconciler behaviour appears | scenarios model the reconciler's world |
| Infrastructure — rate limits, latency, scheduling | per-cycle overhead is aggregate, not causal | cycle interval measured from logs |
| Load drift over weeks | a scenario ages with the queue it came from | recalibrate from fresh logs |
| Rare events | a single run will not contain them | Monte Carlo over many seeds |

## When not to use this

- **Stateless systems.** No queue, no ordering, no reconciliation loop, nothing
  for a policy to be wrong about.
- **No structured logs.** No observability, no targets, no calibration — only a
  scenario someone invented.
- **Capacity-bound problems.** If the bottleneck is raw throughput rather than
  scheduling, the policy is not the variable and simulation adds nothing.
- **Systems changing faster than you can recalibrate.** The model is stale
  before it is proved.

## Where the harness fits

`run_harness.py` drives the unmodified upstream `gitlab_housekeeping` against
the fake GitLab server. It is not a stage in this pipeline and most users will
never run it. It is the evidence for the claim the pipeline rests on — that the
policies compared here are the ones the real integration runs — pinned to a
commit and dated, so it can be checked once rather than re-executed by everyone.
See the README's harness section.
