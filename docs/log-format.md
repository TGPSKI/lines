# Log format

Every stage of the pipeline reads one shape: **records**. A record is a
timestamp, one signal, and the merge request it happened to. `mqsim.adapters`
turns a log into records, and nothing downstream knows which log it came from.

Two adapters ship, both reading qontract-reconcile housekeeping logs in the
dialects this project grew up on. They are examples. An external log needs one
adapter — a module that produces the records below — not a change to either of
them.

## The record

NDJSON: one JSON object per line, in time order.

```json
{"ts":"2026-05-06T13:01:07Z","event":"merge","project":"example/queue-lab","iid":1200}
{"ts":"2026-05-06T13:01:19Z","event":"rebase","project":"example/queue-lab","iid":1200,"policy":"old-burst"}
```

| Field | Required | Meaning |
|---|---|---|
| `ts` | yes | ISO-8601. An offset is honoured; no offset is read as UTC |
| `event` | yes | one of the seven signals below |
| `project` | on `merge`, `rebase`, `label_change`, `close` | what calibration's `--project` filter matches |
| `iid` | on `merge`, `merge_failure`, `rebase` | the merge request number |
| `policy` | no, `rebase` only | which policy issued the rebase |

| Event | What it means | What reads it |
|---|---|---|
| `cycle` | a reconcile loop started | tick length: the spacing between cycles |
| `merge` | a merge was attempted | throughput, hourly and weekday shape |
| `merge_failure` | the last `merge` for that iid did not land | observed failure rate |
| `rebase` | a rebase was requested | rebase pressure, per-MR waste |
| `rebase_limit` | the loop hit its per-cycle rebase cap | reported, not modelled |
| `label_change` | a priority label was added or removed | arrival timing |
| `close` | the MR was closed without merging | activity shape |

A record that cannot be used is rejected by line, naming the file and line
number. Nothing is skipped silently at this layer — silence belongs to the
adapter, which is where a log line that matches nothing is dropped.

### The two optional fields cost you something

**No `policy` on rebases.** Stage 1 detects algorithm windows from the policy
that emitted each rebase. Records without it produce a single window named
`unknown`; `--algorithm old-burst` then labels that window from the flag, and
the report says so. You lose A/B window detection, not the measurement.

**No `cycle` records.** Tick length falls back to the modal gap between
events, which is shorter than the reconcile loop — the shipped sample lands at
21.95s against a 30s floor. If your log has no cycle marker, pass
`--tick-seconds-override` with the interval you actually run, or the scenario
window is wrong by the ratio.

## Producing records

**Convert with a tool you already have.** Records are four fields; awk or jq
will do it. Feed the NDJSON straight to both stages.

**Write an adapter.** A module under `src/mqsim/adapters/` with
`read(path) -> LogSource`, registered in that package's `ADAPTERS`. Then
`--log-dialect <name>` reads your logs everywhere, and
`src/mqsim/adapters/housekeeping_text.py` is the 104-line example to copy.

## Check the adapter before trusting a calibration

```bash
make convert-log LOG_FILE=docs/sample-housekeeping-log.json \
  RECORDS_OUT=reports/log-analysis/records.ndjson
```

```
docs/sample-housekeeping-log.json read as housekeeping-json
  268 timestamped entries, 268 records
  2026-05-06 13:01 → 2026-05-06 18:28 (5.46h)
  merge: 60
  rebase: 208
  cycle: 0 — see docs/log-format.md
  projects: example/queue-lab
```

`timestamped entries` is what the file held; `records` is what matched. A
large gap between them means the adapter is missing your format, and every
number downstream describes only the fraction that happened to match. A
`projects` list that does not contain the name you plan to pass as
`--project` means calibration will measure nothing.

## The shipped example adapters

### `housekeeping-json`

A JSON **array** of `{"@timestamp", "message"}` objects — the shape a
CloudWatch Logs Insights export produces. Not JSONL, not an object with a
`results` key.

```json
[
  {
    "@timestamp": "2026-05-06 13:01:07.000",
    "message": "2026-05-06T13:01:07Z 1 INFO [gitlab_housekeeping.py:rebase_merge_requests:733] processing [example/queue-lab, 1200]"
  }
]
```

Two patterns carry everything: `gitlab_housekeeping\.py:(?P<func>\w+):(\d+)\]`
names the emitting function, and `\[(?P<project>[^\[\]]+?),\s*(?P<iid>\d+)\]$`
takes the project and merge request from the end of the line. The iid pattern
requires the message to **end** with `[<project>, <number>]`, which is how
"rebase limit reached" — naming no MR — stays out of the per-MR counts.

| Function in the message | Record |
|---|---|
| `merge_merge_requests` | `merge` |
| `rebase_merge_requests` | `rebase`, policy `old-burst` |
| `_try_rebase` | `rebase`, policy `active-cap` |

This dialect carries no cycle boundary, no label change, and no merge failure.
See the cost of the missing `cycle` above.

### `housekeeping-text`

Plain text, one line each, as `kubectl logs` writes it:

```
[2026-05-06 13:01:07] [INFO] reconcile.gitlab_housekeeping - dry-run: ['merge', 'example/queue-lab', 1200]
```

| Line | Record |
|---|---|
| `using gql endpoint` | `cycle` |
| `['merge', '<project>', <iid>]` | `merge` |
| `unable to merge <iid>:` | `merge_failure` |
| `['rebase', '<project>', <iid>]` | `rebase` |
| `rebase limit reached for this reconcile loop` | `rebase_limit` |
| `['add_label'` or `['remove_label'` | `label_change` |
| `['close_item'` | `close` |

It names no policy, so stage 1 reads one `unknown` window from it.

## What the sample measures

`docs/sample-housekeeping-log.json` is a working example in the JSON dialect —
268 entries, five hours of one queue. Stage 1 on it:

```bash
PYTHONPATH=src:. python scripts/analyze_logs.py measure \
  --input docs/sample-housekeeping-log.json \
  --algorithm old-burst \
  --output reports/log-analysis
```

gives 59 merges over 5 hours, 208 rebase calls, 3.47 rebases per merge, and
one stuck MR. Those are the numbers a scenario built from it must reproduce —
see [the calibrate-and-compare workflow](../.agents/skills/calibrate-and-compare/SKILL.md)
and [docs/logs-to-scenario.md](logs-to-scenario.md) for the stages between.
