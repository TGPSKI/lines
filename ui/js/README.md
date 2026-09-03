# Merge Queue Sim UI JS Split

This UI is intentionally **client-side only**:

- no npm/yarn
- no bundler/transpiler
- plain `<script src="...">` loading order

## File layout

- `mqsim-core.js`: shared state, parsing, load pipeline, stats, simulation, run browser, kanban, charts, swimlane, playback.
- `mqsim-common.js`: cross-file primitives (formatting, CSV parsing, chart theme/options, safe chart destroy helpers).
- `mqsim-experiments.js`: discrimination + calibration experiment parsing/rendering.
- `mqsim-monte-carlo.js`: monte-carlo parsing/rendering/statistics.
- `mqsim-sweep.js`: parameter-sweep parsing and rendering.
- `mqsim-multi-merge.js`: multi-merge experiment parsing and rendering.
- `mqsim-selfcheck.js`: runtime self-check + smoke tests for split-file integrity.

## Load order contract

`index.html` loads scripts in this exact order:

1. `mqsim-common.js`
2. `mqsim-core.js`
3. `mqsim-experiments.js`
4. `mqsim-monte-carlo.js`
5. `mqsim-sweep.js`
6. `mqsim-multi-merge.js`
7. `mqsim-selfcheck.js`

Do not reorder unless you also update cross-file references.

## Functional-equivalence guardrails

### 1) Runtime self-check

Use the **Run UI Self-Check** button in the top bar.

It verifies:

- critical DOM anchors exist (including control-row hosts)
- key API functions are defined across all split JS files
- parser/type-detection smoke tests for CSV + hourly timeline behavior
- optional end-to-end lifecycle test (`loadFiles` -> render controls -> `unloadAllData`) when no user data is loaded

If user data is already loaded, lifecycle checks are skipped safely and reported as warnings.

Results are shown in the UI status line and browser console.

### 2) Static syntax check (no toolchain required)

Run from the repository root:

```bash
node --check ui/js/mqsim-common.js
node --check ui/js/mqsim-core.js
node --check ui/js/mqsim-experiments.js
node --check ui/js/mqsim-monte-carlo.js
node --check ui/js/mqsim-sweep.js
node --check ui/js/mqsim-multi-merge.js
node --check ui/js/mqsim-selfcheck.js
```

### 3) Manual parity spot-check

Load one representative run folder and confirm:

- Run Browser still loads/selects rows and metadata
- Statistics + Simulation tabs render with no console errors
- Experiments/Monte Carlo tabs render when corresponding CSV files are loaded
