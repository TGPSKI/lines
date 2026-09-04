# lines — mqsim + glab_api Makefile
#
# Uses this repository's `.venv`.
# Create it with Python 3.14 and install the project with `pip install -e ".[dev]"`.
#
# Usage:
#   make test                   # run unit tests
#   make lint                   # ruff check
#   make vendor-verify          # check ui/vendor against its manifest
#   make validate               # validate all scenarios
#   make serve SCENARIO=...     # start sim server
#   make run                    # run standalone driver
#   make compare                # compare selected POLICY_SET (default scenario)
#   make compare-advanced       # compare selected POLICY_SET (advanced scenario)
#   make compare-calibrated      # compare against the synthetic calibration demo
#   make discrimination-pass      # run policy-pair stress variants
#   make compare-quick          # quick comparison (~2 min)
#   make calibrate-scenario LOGS="a.log b.log c.log"
#   make tune-calibration LOGS="a.log b.log c.log" CALIBRATION_POLICY=active-cap
#   make monte-carlo-small      # Monte Carlo: 10 trials (~30 min)
#   make monte-carlo-medium     # Monte Carlo: 20 trials (~60 min)
#   make monte-carlo-large      # Monte Carlo: 30 trials (~90 min)
#   make tick                   # advance sim by one tick
#   make metrics                # fetch current metrics
#   make state                  # fetch current state
#   make reset                  # reset sim to initial state
#   make report                 # generate report from latest comparison
#   make full-cycle             # serve + run + ticks + report (automated)

SHELL := /bin/bash
SIM_PORT ?= 8080
SIM_HOST ?= 127.0.0.1
SIM_URL := http://$(SIM_HOST):$(SIM_PORT)
SCENARIO ?= scenarios/mvp-active-cap.yaml
ADV_SCENARIO := scenarios/large-mixed-queue-advanced.yaml
CALIBRATION_SCENARIO ?= scenarios/synthetic-calibration-demo.yaml
GENERATED_CALIBRATION_SCENARIO ?= scenarios/generated/synthetic-calibration.yaml
CALIBRATION_PROJECT ?= queue-lab
# policy sets are defined in run_standalone.py:
#   phase0: regular mode only (wait_for_pipeline=False, insist=False)
#   phase1: phase0 + cap+phase1 + omm regular
#   all: every registered policy trace (OMM currently has regular mode only)
POLICY_SET ?= phase0
METRICS_DIR ?= reports/single
METRICS_FILE = $(METRICS_DIR)/$(POLICY)-metrics.ndjson
REPORT_FILE = $(METRICS_DIR)/$(POLICY)-summary.md
LIMIT ?= 8
TICKS ?= 10
CYCLES ?= 20
TICKS_PER_CYCLE ?= 4
# POLICY can be a regular or mode-explicit trace, e.g.:
#   top-k, top-k-wait, top-k-wait-insist
POLICY ?= active-cap
CALIBRATION_POLICY ?= old-burst
RUN_TS ?= $(shell date +"%m-%d-%y_%I-%M-%p")
CALIBRATION_OUT_DIR ?= reports/calibration/$(RUN_TS)
DISCRIMINATION_POLICIES ?= top-k,active-cap,old-burst
DISCRIMINATION_LHS ?= top-k
DISCRIMINATION_RHS ?= active-cap
DISCRIMINATION_BASELINE ?= old-burst
ALLOW_NON_LOOPBACK ?= false
NETWORK_OVERRIDE := $(if $(filter true 1 yes,$(ALLOW_NON_LOOPBACK)),--allow-non-loopback,)

# Use the venv python directly (avoids uv version mismatch)
VENV_PYTHON := .venv/bin/python
PYTHON := $(VENV_PYTHON)
PYTEST := $(VENV_PYTHON) -m pytest

# Required by the harness targets; set this to a qontract-reconcile checkout.
QONTRACT_RECONCILE_ROOT ?=

.PHONY: test validate serve serve-bg kill-server run check-qontract-root \
        run-harness run-harness-dry compare compare-advanced compare-calibrated \
        compare-quick calibrate-scenario tune-calibration discrimination-pass \
        monte-carlo-small monte-carlo-medium monte-carlo-large \
        tick ticks metrics state reset report full-cycle clean ui help \
        lint vendor vendor-verify \
        analyze-compare analyze-measure analyze-plan

ui: ## Open the queue visualization (load NDJSON in the browser; runs offline)
	@python3 -c "import pathlib, webbrowser; p=pathlib.Path('$(CURDIR)/ui/index.html').resolve(); print(p); webbrowser.open(p.as_uri())"

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-24s\033[0m %s\n", $$1, $$2}'

test: ## Run unit tests
	$(PYTEST) tests/ -v

lint: ## Lint Python sources with ruff
	$(PYTHON) -m ruff check .

vendor: ## Re-download the pinned UI assets in ui/vendor (needs network)
	@python3 scripts/vendor_ui_assets.py --fetch

vendor-verify: ## Check ui/vendor against ui/vendor/MANIFEST.txt (offline)
	@python3 scripts/vendor_ui_assets.py

validate: ## Validate all scenario YAML files
	@for f in scenarios/*.yaml; do \
		echo "--- $$f ---"; \
		$(PYTHON) -c "import sys; sys.path.insert(0, 'src'); from glab_api.cli import cli; cli()" validate --scenario "$$f"; \
		echo ""; \
	done

# ---------------------------------------------------------------------------
# Server management
# ---------------------------------------------------------------------------

serve: ## Start the sim server (use SCENARIO= to pick scenario)
	@mkdir -p $(METRICS_DIR)
	PYTHONPATH=src:. $(PYTHON) -m glab_api.cli serve \
		--scenario $(SCENARIO) \
		--host $(SIM_HOST) \
		--port $(SIM_PORT) $(NETWORK_OVERRIDE) \
		--metrics-out $(METRICS_FILE)

serve-bg: ## Start sim server in background
	@mkdir -p reports/comparisons $(METRICS_DIR)
	@echo "Starting sim server on $(SIM_URL) with scenario $(SCENARIO)..."
	PYTHONPATH=src:. nohup $(PYTHON) -m glab_api.cli serve \
		--scenario $(SCENARIO) \
		--host $(SIM_HOST) \
		--port $(SIM_PORT) $(NETWORK_OVERRIDE) \
		--metrics-out $(METRICS_FILE) \
		> reports/comparisons/server.log 2>&1 &
	@echo $$! > reports/comparisons/server.pid
	@sleep 2
	@if curl -sf $(SIM_URL)/api/v4/user > /dev/null 2>&1; then \
		echo "Server running (PID $$(cat reports/comparisons/server.pid))"; \
	else \
		echo "Server failed to start. Check reports/comparisons/server.log"; \
		exit 1; \
	fi

kill-server: ## Kill background sim server
	@if [ -f reports/comparisons/server.pid ]; then \
		kill $$(cat reports/comparisons/server.pid) 2>/dev/null || true; \
		rm -f reports/comparisons/server.pid; \
		echo "Server stopped"; \
	else \
		echo "No PID file found"; \
	fi

# ---------------------------------------------------------------------------
# Single-policy runs
# ---------------------------------------------------------------------------

run: ## Run standalone driver against sim
	PYTHONPATH=src:. $(PYTHON) run_standalone.py \
		--sim-url $(SIM_URL) \
		--policy $(POLICY) \
		--limit $(LIMIT) \
		--cycles $(CYCLES) \
		--ticks-per-cycle $(TICKS_PER_CYCLE)

# ---------------------------------------------------------------------------
# Comparison runs (single-shot, all policies)
# ---------------------------------------------------------------------------

compare: ## Compare all policies (default scenario)
	PYTHONPATH=src:. $(PYTHON) run_standalone.py \
		--compare \
		--policy-set $(POLICY_SET) \
		--scenario $(SCENARIO) \
		--port $(SIM_PORT) \
		--limit $(LIMIT) \
		--cycles $(CYCLES) \
		--ticks-per-cycle $(TICKS_PER_CYCLE)

compare-advanced: ## Compare all policies against advanced scenario (8h sim)
	PYTHONPATH=src:. $(PYTHON) run_standalone.py \
		--compare \
		--policy-set $(POLICY_SET) \
		--scenario $(ADV_SCENARIO) \
		--port $(SIM_PORT) \
		--limit $(LIMIT) \
		--cycles 480 \
		--ticks-per-cycle 1 \
		--log-level WARNING

compare-calibrated: ## Compare all policies against the synthetic calibration demo
	PYTHONPATH=src:. $(PYTHON) run_standalone.py \
		--compare \
		--policy-set $(POLICY_SET) \
		--scenario $(CALIBRATION_SCENARIO) \
		--port $(SIM_PORT) \
		--limit 2 \
		--cycles 480 \
		--ticks-per-cycle 1 \
		--log-level WARNING

compare-quick: ## Quick comparison (~2 min) - fewer cycles
	PYTHONPATH=src:. $(PYTHON) run_standalone.py \
		--compare \
		--policy-set $(POLICY_SET) \
		--scenario $(SCENARIO) \
		--port $(SIM_PORT) \
		--limit $(LIMIT) \
		--cycles 20 \
		--ticks-per-cycle $(TICKS_PER_CYCLE)

# ---------------------------------------------------------------------------
# Monte Carlo (parallel trials with statistical analysis)
# ---------------------------------------------------------------------------

calibrate-scenario: ## Build a scenario from caller-supplied housekeeping logs
	@if [ -z "$(LOGS)" ]; then \
		echo "Set LOGS to one or more log paths, e.g."; \
		echo '  make calibrate-scenario LOGS="/tmp/a.log /tmp/b.log /tmp/c.log"'; \
		exit 1; \
	fi
	PYTHONPATH=src:. $(PYTHON) scripts/calibrate_from_housekeeping_logs.py \
		--project $(CALIBRATION_PROJECT) \
		--logs $(LOGS) \
		--emit-scenario $(GENERATED_CALIBRATION_SCENARIO)

tune-calibration: ## Tune calibration knobs for selected CALIBRATION_POLICY
	@if [ -z "$(LOGS)" ]; then \
		echo "Set LOGS to one or more log paths, e.g."; \
		echo '  make tune-calibration LOGS="/tmp/a.log /tmp/b.log /tmp/c.log"'; \
		exit 1; \
	fi
	PYTHONPATH=src:. $(PYTHON) scripts/tune_prod_calibration.py \
		--logs $(LOGS) \
		--project $(CALIBRATION_PROJECT) \
		--policy $(CALIBRATION_POLICY) \
		--scenario-out scenarios/generated/synthetic-calibration-$(CALIBRATION_POLICY).yaml \
		--out-dir $(CALIBRATION_OUT_DIR)

discrimination-pass: ## Run repeatable policy-pair discrimination variants
	PYTHONPATH=src:. $(PYTHON) scripts/run_discrimination_pass.py \
		--base-scenario $(CALIBRATION_SCENARIO) \
		--policies $(DISCRIMINATION_POLICIES) \
		--lhs-policy $(DISCRIMINATION_LHS) \
		--rhs-policy $(DISCRIMINATION_RHS) \
		--baseline-policy $(DISCRIMINATION_BASELINE) \
		--cycles 480 \
		--limit 2 \
		--ticks-per-cycle 1

monte-carlo-small: ## Monte Carlo: 10 trials (~30 min)
	PYTHONPATH=src:. $(PYTHON) run_standalone.py \
		--monte-carlo 10 \
		--policy-set $(POLICY_SET) \
		--scenario $(ADV_SCENARIO) \
		--limit $(LIMIT) \
		--cycles 480 \
		--ticks-per-cycle 1 \
		--log-level WARNING

monte-carlo-medium: ## Monte Carlo: 20 trials (~60 min)
	PYTHONPATH=src:. $(PYTHON) run_standalone.py \
		--monte-carlo 20 \
		--policy-set $(POLICY_SET) \
		--scenario $(ADV_SCENARIO) \
		--limit $(LIMIT) \
		--cycles 480 \
		--ticks-per-cycle 1 \
		--log-level WARNING

monte-carlo-large: ## Monte Carlo: 30 trials (~90 min)
	PYTHONPATH=src:. $(PYTHON) run_standalone.py \
		--monte-carlo 30 \
		--policy-set $(POLICY_SET) \
		--scenario $(ADV_SCENARIO) \
		--limit $(LIMIT) \
		--cycles 480 \
		--ticks-per-cycle 1 \
		--log-level WARNING

# ---------------------------------------------------------------------------
# Harness (real gitlab-housekeeping code)
# ---------------------------------------------------------------------------

check-qontract-root:
	@if [ -z "$(QONTRACT_RECONCILE_ROOT)" ]; then \
		echo "Set QONTRACT_RECONCILE_ROOT=/path/to/qontract-reconcile"; \
		exit 1; \
	fi
	@test -d "$(QONTRACT_RECONCILE_ROOT)/reconcile" || { \
		echo "QONTRACT_RECONCILE_ROOT must contain the reconcile package"; \
		exit 1; \
	}

run-harness: check-qontract-root ## Run REAL gitlab-housekeeping (requires qontract-reconcile deps)
	PYTHONPATH=src:. $(PYTHON) run_harness.py \
		--qontract-reconcile-root "$(QONTRACT_RECONCILE_ROOT)" \
		--sim-url $(SIM_URL) $(NETWORK_OVERRIDE) \
		--no-dry-run \
		--limit $(LIMIT)

run-harness-dry: check-qontract-root ## Run REAL gitlab-housekeeping in dry-run mode
	PYTHONPATH=src:. $(PYTHON) run_harness.py \
		--qontract-reconcile-root "$(QONTRACT_RECONCILE_ROOT)" \
		--sim-url $(SIM_URL) $(NETWORK_OVERRIDE) \
		--dry-run \
		--limit $(LIMIT)

# ---------------------------------------------------------------------------
# Sim control
# ---------------------------------------------------------------------------

tick: ## Advance sim by one tick (pipeline state progression)
	@curl -sf -X POST $(SIM_URL)/__sim/tick | python -m json.tool

ticks: ## Advance sim by TICKS ticks (default: 10)
	@for i in $$(seq 1 $(TICKS)); do \
		echo "--- Tick $$i ---"; \
		curl -sf -X POST $(SIM_URL)/__sim/tick | python -m json.tool; \
		echo ""; \
	done

metrics: ## Fetch current metrics summary from sim
	@curl -sf $(SIM_URL)/__sim/metrics | python -m json.tool

state: ## Fetch current simulation state
	@curl -sf $(SIM_URL)/__sim/state | python -m json.tool

reset: ## Reset sim to initial scenario state
	@curl -sf -X POST $(SIM_URL)/__sim/reset | python -m json.tool

# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

report: ## Generate a report for POLICY from its single-run metrics
	@if [ -f $(METRICS_FILE) ]; then \
		PYTHONPATH=src:. $(PYTHON) -m mqsim.cli report \
			--metrics $(METRICS_FILE) \
			--scenario-name "$$(basename $(SCENARIO) .yaml)" \
			--out $(REPORT_FILE); \
		echo "Report: $(REPORT_FILE)"; \
	else \
		echo "No metrics file at $(METRICS_FILE). Run make serve/run or set POLICY/METRICS_FILE."; \
		exit 1; \
	fi

full-cycle: ## Automated: serve + run with ticks + report
	@echo "=== Full Cycle: $(SCENARIO) ==="
	@echo ""
	$(MAKE) serve-bg
	@echo ""
	@echo "--- Running standalone driver ($(CYCLES) cycles, $(TICKS_PER_CYCLE) ticks/cycle) ---"
	$(MAKE) run
	@echo ""
	$(MAKE) kill-server
	@echo ""
	$(MAKE) report
	@echo ""
	@echo "=== Cycle complete ==="

# ---------------------------------------------------------------------------
# Log analysis (caller-supplied JSON logs)
# ---------------------------------------------------------------------------

LOG_FILE ?= $(error Set LOG_FILE=path/to/logs-insights-results.json)
LOG_OUTPUT ?= reports/log-analysis
LOG_ALGORITHM ?= active-cap

analyze-compare: ## Compare algorithms from caller-supplied logs
	$(PYTHON) scripts/analyze_logs.py compare --input "$(LOG_FILE)" --output $(LOG_OUTPUT)

analyze-measure: ## Single-algorithm report from caller-supplied logs
	$(PYTHON) scripts/analyze_logs.py measure --input "$(LOG_FILE)" --algorithm $(LOG_ALGORITHM) --output $(LOG_OUTPUT)

analyze-plan: ## Phase 1 planning; API enrichment needs explicit GitLab settings
	$(PYTHON) scripts/analyze_logs.py plan --input "$(LOG_FILE)" --algorithm $(LOG_ALGORITHM) --output $(LOG_OUTPUT)

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

clean: ## Remove generated reports and caches
	rm -rf reports/comparisons reports/monte-carlo reports/log-analysis reports/single
	rm -rf __pycache__ .pytest_cache
