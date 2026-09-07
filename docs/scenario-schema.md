# Scenario schema

A scenario is the input to every simulation: the queue's starting state, what
arrives, how long CI takes, and what goes wrong. `glab_api` loads it and serves
it as a GitLab-shaped API; the policies never see the file.

Validate any scenario with `make validate`, which loads every file in
`scenarios/` and reports the first field that fails.

Read alongside `scenarios/large-mixed-queue-advanced.yaml`, the richest shipped
example. It carries every top-level section and every `merge_requests[]` field
below except `cancel_tick`. No shipped scenario uses `cancel_tick` or
`scheduled_target_advances`.

## Top level

```yaml
metadata: {...}            # optional, but calibration and the UI read it
project: {...}             # required
merge_requests: [...]      # required
pipeline_durations: {...}  # optional; per-MR ci_duration overrides it
sha_pools: {...}           # optional; needed for target advances
```

## `project`

| Field | Type | Meaning |
|---|---|---|
| `id` | int | project id the API serves |
| `name`, `path` | string | display only |
| `path_with_namespace` | string | what policies log and group by |
| `default_branch` | string | usually `master` |
| `target_head` | string | the SHA the queue is rebasing onto at t=0 |

## `merge_requests[]`

One entry per MR the scenario will ever contain, including those that arrive
later. An MR is in the queue from `arrival_tick` onward, not from t=0.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id`, `iid` | int | yes | `iid` is what every surface displays |
| `title` | string | yes | free text; conventionally `<domain>/<change>` |
| `state` | string | yes | `opened` for anything the queue will consider |
| `sha` | string | yes | the MR's head |
| `rebased_target_sha` | string | yes | the target it was last rebased onto. Differs from `project.target_head` to start an MR stale |
| `labels` | list | yes | drives priority and conflict grouping — see below |
| `approved_at` | ISO8601 | yes | ties are broken by this, earliest first |
| `pipelines` | list | yes | may be empty; see below |
| `ci_duration` | int (ticks) | no | this MR's CI length. Overrides `pipeline_durations` |
| `arrival_tick` | int | no | when it enters the queue. Absent or 0 means present at t=0 |
| `push_tick` | int | no | author pushes at this tick, invalidating the rebase and cancelling any running pipeline |
| `force_merge_tick` | int | no | merged out of band at this tick, advancing the target head |
| `cancel_tick` | int | no | the author closes the MR at this tick, cancelling any running pipeline |

### Labels carry two meanings

Priority, highest first — the full order, from `MERGE_LABELS_PRIORITY` in
`src/glab_api/state.py`:

```
bot/approved: critical
bot/approved: urgent
bot/approved: high
bot/approved: progressive-delivery
bot/approved: medium
bot/approved: low
bot/approved
bot/automerge
auto-merge
lgtm
```

Conflict grouping: any `tenant-*` label. Two MRs sharing a `tenant-*` label
overlap and cannot be batched together by the optimistic multi-merge policy.
This is what replaced an explicit `tenant_domains` field — the label *is* the
grouping, the way GitLab expresses it.

Hold labels block a merge regardless of priority: `do-not-merge/hold`,
`needs-rebase`, `blocked/bot-access`, `bot/hold`.

### `pipelines[]`

```yaml
pipelines:
  - id: 7001
    status: running        # running | success | failed | pending
    sha: mr1-sha-001       # which commit it ran against
    root_sha: target-001   # the target it was rebased onto
    running_ticks_remaining: 15
    outcome: success       # what it becomes when it finishes
```

`root_sha` is what makes a success stale: when the target head advances past it,
the pass no longer applies to the current target.

## `pipeline_durations`

Applies to MRs without their own `ci_duration`.

```yaml
pipeline_durations:
  distribution: weighted   # weighted | uniform
  min_ticks: 5
  max_ticks: 25
  failure_rate: 0.1        # 0..1, per pipeline
  weights: {"5": 15, "6": 20, ...}   # relative weight per tick length
```

CI length dominates every policy's behaviour more than any other input. A
scenario whose CI durations do not resemble yours will not rank policies the way
your queue does, whatever else it gets right.

## `sha_pools`

```yaml
sha_pools:
  target_advances:
    master: [target-002, target-003, ...]
```

The SHAs the target head advances through. Without enough of them a long run
runs out of targets.

`scheduled_target_advances` is a separate **top-level** key — not part of
`sha_pools` and not under `metadata` — mapping tick to SHA. It moves the head
independently of merges, modelling other repositories pushing to the branch:

```yaml
scheduled_target_advances:
  10: external-push-sha-1
  25: external-push-sha-2
```

## `metadata`

Free-form, but three parts are read:

| Key | Read by | Effect |
|---|---|---|
| `name`, `description` | the UI | shown on the Simulation tab |
| `provenance` | humans | say plainly whether the numbers are synthetic or derived from measurement |
| `calibration_targets` | the tuner and the UI | the measured values this scenario claims to reproduce |

`calibration_targets` is what makes a scenario checkable:

```yaml
metadata:
  calibration_targets:
    performance_dimensions:
      merge_per_hour_24h: 4.3189
      rebase_per_merge_global: 4.2309
      merge_interval_seconds_p95: 2209.0
    arrival_profile:
      scenario_start_hour: 13
      peak_window_hours_utc: ["13", "14", "15", "16", "17", "18", "19", "20"]
```

With it, the Simulation tab shows "calibration target vs this run" and the tuner
can accept or reject. Without it a scenario still simulates — it just cannot
claim to represent anything.

## Writing one by hand

Usually you should not. `scripts/calibrate_from_housekeeping_logs.py` generates
a scenario from measured logs, already carrying `calibration_targets` — see
[docs/logs-to-scenario.md](logs-to-scenario.md) for which fields it derives and
which it fabricates. Hand authoring is for probe scenarios that isolate one behaviour — see
`scenarios/overlap-conflict-phase1.yaml` for a scenario built to make a single
policy difference visible.
