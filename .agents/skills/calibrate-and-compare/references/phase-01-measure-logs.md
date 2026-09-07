---
name: phase-01-measure-logs
description: "Turn gitlab-housekeeping logs into measured throughput and rebase targets"
parent: calibrate-and-compare
---

# Phase 1 — Measure the logs

Everything downstream is judged against the numbers this phase produces. If they
are wrong, a calibration that matches them is worse than no calibration.

## Step 1 — Confirm the log shape

**Inspect**: convert the log the user named, and read the counts.

```bash
make convert-log LOG_FILE=<path> RECORDS_OUT=reports/log-analysis/records.ndjson
```

Every phase reads the same records; an adapter produces them. Two ship, for
the qontract-reconcile JSON export and pod text dialects, and the dialect is
detected from the file. `docs/log-format.md` documents the record and what
writing a third adapter takes.

| Status | Action |
|---|---|
| Records ≈ entries, and `projects` lists the queue | Continue |
| Records far below entries | Stop. The adapter is missing the format; every number downstream would describe the fraction that matched |
| `merge: 0` or `rebase: 0` | Stop. Report which signal is missing against `docs/log-format.md` |
| `cycle: 0` | Continue, and carry it: phase 2's tick length is an estimate, not the measured loop |

## Step 2 — Choose the project filter

**Decide**: `--project` is required and has no default. Ask which project's
merges to measure. One repository's queue is the unit of analysis; mixing
projects produces a throughput number describing nothing.

## Step 3 — Measure

**Generate**:

```bash
make analyze-measure LOG_FILE=<path> LOG_ALGORITHM=<policy>
```

Or directly, when you need flags the target does not pass through:

```bash
PYTHONPATH=src:. python scripts/analyze_logs.py measure \
  --input <path> --algorithm <policy> --output reports/log-analysis
```

Write to: `reports/log-analysis/`

## Step 4 — Read what came back

Report these, quoted from the output rather than recomputed:

- merges per hour over the whole window, and over active hours only
- rebases per merge
- merge interval p50 and p95
- the window the log actually covers

**Decide**: does the window contain enough merges to be worth modelling? A
window with a handful of merges gives percentiles that move under a single
outlier. Say so rather than proceeding quietly.

## Checkpoint

**Artifacts** (local, gitignored):
- `reports/log-analysis/**`

Nothing here is committable. The measured targets carry forward to phase 2 as
the thing the scenario must reproduce.

**Next**: @phase-02-build-scenario.md
