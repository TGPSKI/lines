# Golden scenarios

A policy comparison is only as good as the scenario it ran on. "cap+phase1 uses
41% fewer rebases" is a fact about a YAML file until someone shows that the YAML
file behaves like a real queue.

A **golden scenario** is one that has been shown to reproduce measured behaviour
within stated tolerances, and that carries the evidence with it.

## Two kinds of scenario, two different jobs

| Kind | Built by | Answers |
|---|---|---|
| **Probe** | hand-authored | "does policy X do the thing it claims under condition Y?" |
| **Golden** | calibrated from logs, then proofed | "which policy should I run on a queue that behaves like mine?" |

Both ship. `scenarios/overlap-conflict-phase1.yaml` is a probe: a small queue
constructed so that multi-merge either batches or does not, with nothing else
happening to obscure it. A golden scenario is the opposite — messy on purpose,
because the queue it models is messy.

Do not use a probe to rank policies, and do not use a golden scenario to
demonstrate a mechanism. A probe has no claim to realism; a golden scenario has
too much going on to isolate one behaviour.

## Authoring a probe scenario

A probe is a hypothesis written as YAML. Build it backwards from the behaviour
you want to make visible.

1. **State the behaviour in one sentence.** "Optimistic multi-merge should batch
   two MRs whose tenant labels do not overlap, and refuse when they do."
2. **Construct the minimum queue that exercises it.** Two MRs, one shared label
   in one variant and none in the other. Every additional MR is noise unless it
   is doing work in the hypothesis.
3. **Remove every source of variance you are not testing.** Set `ci_duration`
   per MR rather than a distribution. Set `failure_rate: 0` unless failure *is*
   the hypothesis. A probe with random CI answers a different question on every
   run.
4. **Say in `metadata.description` what it is supposed to show,** so the next
   reader can tell whether it still does.
5. **Prove it discriminates.** Run it against at least two policies. If they
   score the same, the scenario does not test what you think it tests — that is
   a scenario bug, not a policy finding.

See [docs/scenario-schema.md](scenario-schema.md) for the fields.

## Getting to a golden scenario

The path is measure → generate → tune → gate → promote. The first four are the
[calibrate-and-compare workflow](../.agents/skills/calibrate-and-compare/SKILL.md),
phases 1–3, and [docs/logs-to-scenario.md](logs-to-scenario.md) documents what
each stage derives, what it fabricates, and the traps in between. This section is
about the fifth.

### What the gates decide

`scripts/tune_prod_calibration.py` grid-searches knobs, validates the best
candidates over a longer run, and then accepts or rejects against three gates:

| Gate | Asks |
|---|---|
| `throughput_pass` | is the headline merge rate within tolerance of measured? |
| `score_pass` | is the weighted multidimensional score within tolerance? |
| `max_dimension_pass` | is the *worst single* dimension within its limit? |

The third exists because the first two can both pass while one dimension is
badly wrong.

On acceptance the tuner writes `selected-scenario.yaml`. On rejection it writes
`rejected-scenario.yaml` and exits non-zero, so automation cannot mistake a
failure for a pass.

### A rejection is a result

A rejected calibration says: *the model cannot reproduce this queue with these
knobs*. That is information about your queue, and it is usually more interesting
than a pass. Read `context.best_*.dimension_error_pct` and see which dimension
resisted — it is normally arrival shape or CI duration, rarely throughput.

The three honest responses are to widen the knob grid, to proceed with a
scenario you label as uncalibrated, or to conclude the queue's behaviour is
outside what this model expresses. Loosening a threshold until it passes is not
among them, because the threshold is the claim.

### Promote it, with the proof attached

The tuner's output lands in a gitignored report directory. Copying the YAML by
hand gives you a file that claims to represent a queue while the evidence stays
behind.

```bash
python scripts/promote_scenario.py reports/calibration/<run> --name <scenario-name>
```

This writes `scenarios/<name>.yaml` with a `metadata.proof` block:

```yaml
metadata:
  proof:
    proofed_from: golden-proof-1d-fixed-ci-v13
    calibrated_policy: old-burst
    validated_over_cycles: '480'
    gates: {accepted: true, max_dimension_pass: true, score_pass: true, throughput_pass: true}
    thresholds: {max_dimension_error_pct: 90.0, rank_score_pct: 30.0, throughput_rel_error_pct: 5.0}
    worst_dimension: merge_interval_seconds_p50
    worst_dimension_error_pct: 52.41
```

It refuses to promote a rejected run unless you pass `--force-rejected`, which
records the failure in the proof rather than hiding it.

## The honesty principle

Look at that example again. It passed every gate — and its worst dimension is
**52% off**, because `max_dimension_error_pct` was set to 90 for this run.

That is a legitimate scenario. Merge-interval p50 is a hard dimension to
reproduce, it carries 1% of the weighted score, and a scenario that matches
throughput, peak shape and rebase pressure while missing p50 is still useful for
ranking policies on CI cost.

It stops being legitimate the moment someone quotes a result from it without
that number. So:

- **The proof travels with the scenario.** A promoted scenario names its worst
  dimension in its own metadata. Anyone can read it without finding the run.
- **Quote the worst dimension when you quote the result.** "On a scenario
  calibrated to within 5% on throughput but 52% on merge-interval p50" is a
  longer sentence and a defensible one.
- **A relaxed threshold is a decision, not a default.** If you raise
  `--max-dimension-error-pct`, say why in `metadata.notes`. The next reader
  cannot distinguish a considered relaxation from a convenient one.
- **Never edit a proofed scenario in place.** Re-run the calibration and promote
  again. A scenario whose YAML has drifted from the run that proofed it makes a
  claim nothing supports.

### CI duration is estimated, not calibrated

The logs carry merge and rebase timing, never pipeline duration:
`gitlab_housekeeping` reads the GitLab API, not the CI system. So
`calibrate_from_housekeeping_logs.py` writes an authored 5–25 minute
distribution for every input and records that fact as
`ci_model: fixed-minutes-distribution`.

This is a deliberate estimate. The spread is close enough to rank policies, and
the scenario declares it in its own metadata rather than presenting it as
measured. It matters because `docs/scenario-schema.md` is right that CI length
moves policy behaviour more than any other input: a result about CI cost rests
on that distribution, not on your queue. Say so when you quote one, the same way
you quote the worst dimension.

Supply real durations with per-MR `ci_duration` if you have them. Nothing in the
pipeline will do it for you.

## Checking a scenario's claim before trusting it

```bash
python -c "
import yaml,json
d=yaml.safe_load(open('scenarios/<name>.yaml'))
print(json.dumps(d.get('metadata',{}).get('proof','NO PROOF — uncalibrated'), indent=1))"
```

`NO PROOF` is the expected answer for every probe scenario and every synthetic
one shipped with the repo. It is only a problem when the scenario is being used
to rank policies for a real queue.
