---
name: findings-readout
description: "Turn a policy comparison into a findings document: size the investigation from the deterministic summary, dispatch one analyst per distinct unexplained phenomenon, assemble their packages. Use when a comparison has run and someone needs to know what it means rather than what it measured."
metadata:
  author: lines
  version: "1.0"
compatibility: "A comparison under reports/comparisons/ whose runs carry a run_summary"
---

# Findings readout

A comparison table reports what happened. This produces what it means, and
labels how much of that is measured rather than argued.

You are the meta pass. **You investigate nothing.** You read the deterministic
layer, decide how much investigation this comparison deserves, and write one
seed per phenomenon. Each seed becomes one analyst run
(@references/analyst.md); their packages assemble into `findings.md`.

The count is your judgement, sized to the evidence. A comparison where every
policy ties on every row deserves one seed asking why the scenario does not
discriminate. A comparison with three unexplained divergences deserves three.

**Distinct** means different evidence would settle them. Two observations one
cause would explain are one seed. Two angles the same metric would settle are
one seed.

## Turn one — gather

Read only these. Sizing needs no derivations.

```bash
R=reports/comparisons/<run>
python -c "import json;print(json.load(open('$R/metadata.json'))['context'])"
```

Then each policy's summary:

```bash
PYTHONPATH=src python -c "
import json,sys,glob; sys.path.insert(0,'src')
from mqsim.summary import read_run_summary
for f in sorted(glob.glob('$R/*-metrics.ndjson')):
    m=read_run_summary([json.loads(l) for l in open(f) if l.strip()])
    print(f.split('/')[-1].replace('-metrics.ndjson',''), {k:m[k] for k in
      ('mrs_merged','throughput_merges_per_hour','rebase_calls','duplicate_rebase_total',
       'wait_p95','starved_mrs','queue_drain_pct','merge_cycles','avg_mrs_per_merge_cycle')})"
```

`src/mqsim/metrics.json` gives each metric's direction, so "which policy wins
this row" is mechanical, not a judgement call.

If a sweep, Monte Carlo or discrimination run exists for the same scenario, note
that it exists. Do not read it — that is an analyst's job.

## Turn two — the candidate list

Write one line per phenomenon worth an analyst: what it is, and the evidence
that flags it. Nothing else.

What qualifies as a phenomenon:

| Shape | Why it earns a seed |
|---|---|
| Policies tie on the headline metric but differ elsewhere | The tradeoff is real and unstated |
| A metric where one policy is an outlier, not just a winner | Outliers usually have a mechanism |
| A policy that should win by design and does not | Either the scenario lacks the condition it exploits, or the model is wrong |
| Every row ties | The scenario does not discriminate. One seed, not zero |
| A metric that contradicts another | e.g. more merge rounds but the same merges |

What does not:

- A row where the difference is smaller than the run-to-run variance you have no
  Monte Carlo to bound. That is a seed about variance, not about the metric.
- Two rows that move together by construction — `rebase_calls` and
  `pipelines_created` differ only by skip-CI rebases.
- Anything the previous `findings.md` for this scenario already settled.

An empty candidate list is a legitimate result: say so in one line and record
seeds: 0. A saturated comparison investigated zero times is honest.

## Turn three — dispatch and record

For each candidate, write a seed:

```
SEED <n>
phenomenon: <what is unexplained, in one sentence>
evidence:   <the metric and values that flag it>
owns:       <which policies and which metrics this angle covers>
```

No two seeds own the same metric on the same policies; an overlap means they
were one phenomenon.

Dispatch one analyst per seed with @references/analyst.md. Collect their
packages verbatim — you do not rewrite findings, and you do not add any.

Assemble into `reports/findings/<run>-findings.md`:

```markdown
# Findings — <scenario>, limit=<n>, <date>

<one paragraph: what the comparison was, how many angles it earned, and why>

<every analyst package, verbatim, in seed order>

## Not investigated
<phenomena you saw and chose not to seed, with the reason>
```

That last section is not filler. It is what stops the next reader re-opening a
question you already judged not worth the run.
