# GitLab Housekeeping Policy Simulator - Makefile
#
# Uses the root qontract-reconcile venv via `uv run`.
# Run `uv sync` at the repo root first.
#
# Usage:
#   make test                   # run unit tests
#   make validate               # validate all scenarios
#   make serve SCENARIO=...     # start sim server
#   make run                    # run standalone driver
#   make compare                # compare selected POLICY_SET (default scenario)
#   make compare-advanced       # compare selected POLICY_SET (advanced scenario)
#   make compare-prod-calibrated # compare against app-interface calibrated scenario
#   make discrimination-pass      # run policy-pair stress variants
#   make compare-quick          # quick comparison (~2 min)
#   make calibrate-prod-scenario LOGS="a.log b.log c.log"
#   make tune-prod-calibration LOGS="a.log b.log c.log" CALIBRATION_POLICY=active-cap
#   make monte-carlo-small      # Monte Carlo: 10 trials (~10 min)
#   make monte-carlo-medium     # Monte Carlo: 20 trials (~20 min)
#   make monte-carlo-large      # Monte Carlo: 30 trials (~30 min)
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
PROD_SCENARIO := scenarios/app-interface-prod-calibrated.yaml
# policy sets are defined in run_standalone.py:
#   phase0: regular mode only (wait_for_pipeline=False, insist=False)
#   phase1: phase0 + cap+phase1 regular
#   all: regular + wait + wait-insist traces for all policies
POLICY_SET ?= phase0
METRICS_DIR := reports/comparisons/latest
METRICS_FILE := $(METRICS_DIR)/metrics.ndjson
REPORT_FILE := $(METRICS_DIR)/summary.md
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

# Use the venv python directly (avoids uv version mismatch)
VENV_PYTHON := .venv/bin/python
PYTHON := $(VENV_PYTHON)
PYTEST := $(VENV_PYTHON) -m pytest

# Path to qontract-reconcile root (two levels up from this tool)
QR_ROOT := $(shell cd ../.. && pwd)

.PHONY: test validate serve serve-bg kill-server run run-harness run-harness-dry \
        compare compare-advanced compare-prod-calibrated compare-quick \
        calibrate-prod-scenario tune-prod-calibration discrimination-pass \
        monte-carlo-small monte-carlo-medium monte-carlo-large \
        tick ticks metrics state reset report full-cycle clean ui help

ui: ## Open the queue visualization (load NDJSON in the browser; requires network for CDN)
	@python3 -c "import pathlib, webbrowser; p=pathlib.Path('$(CURDIR)/ui/index.html').resolve(); print(p); webbrowser.open(p.as_uri())"

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-24s\033[0m %s\n", $$1, $$2}'

test: ## Run unit tests
	$(PYTEST) tools/gitlab_housekeeping_perf_sim/tests/ -v

validate: ## Validate all scenario YAML files
	@for f in scenarios/*.yaml; do \
		echo "--- $$f ---"; \
		$(PYTHON) -c "import sys; sys.path.insert(0, '.'); from gitlab_hk_sim.cli import cli; cli()" validate --scenario "$$f"; \
		echo ""; \
	done

# ---------------------------------------------------------------------------
# Server management
# ---------------------------------------------------------------------------

serve: ## Start the sim server (use SCENARIO= to pick scenario)
	@mkdir -p reports/comparisons
	PYTHONPATH=. $(PYTHON) -m gitlab_hk_sim.cli serve \
		--scenario $(SCENARIO) \
		--host $(SIM_HOST) \
		--port $(SIM_PORT) \
		--metrics-out $(METRICS_FILE)

serve-bg: ## Start sim server in background
	@mkdir -p reports/comparisons
	@echo "Starting sim server on $(SIM_URL) with scenario $(SCENARIO)..."
	PYTHONPATH=. nohup $(PYTHON) -m gitlab_hk_sim.cli serve \
		--scenario $(SCENARIO) \
		--host $(SIM_HOST) \
		--port $(SIM_PORT) \
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
	PYTHONPATH=. $(PYTHON) run_standalone.py \
		--sim-url $(SIM_URL) \
		--policy $(POLICY) \
		--limit $(LIMIT) \
		--cycles $(CYCLES) \
		--ticks-per-cycle $(TICKS_PER_CYCLE)

# ---------------------------------------------------------------------------
# Comparison runs (single-shot, all policies)
# ---------------------------------------------------------------------------

compare: ## Compare all policies (default scenario)
	PYTHONPATH=. $(PYTHON) run_standalone.py \
		--compare \
		--policy-set $(POLICY_SET) \
		--scenario $(SCENARIO) \
		--port $(SIM_PORT) \
		--limit $(LIMIT) \
		--cycles $(CYCLES) \
		--ticks-per-cycle $(TICKS_PER_CYCLE)

compare-advanced: ## Compare all policies against advanced scenario (8h sim)
	PYTHONPATH=. $(PYTHON) run_standalone.py \
		--compare \
		--policy-set $(POLICY_SET) \
		--scenario $(ADV_SCENARIO) \
		--port $(SIM_PORT) \
		--limit $(LIMIT) \
		--cycles 480 \
		--ticks-per-cycle 1 \
		--log-level WARNING

compare-prod-calibrated: ## Compare all policies against app-interface calibrated scenario
	PYTHONPATH=. $(PYTHON) run_standalone.py \
		--compare \
		--policy-set $(POLICY_SET) \
		--scenario $(PROD_SCENARIO) \
		--port $(SIM_PORT) \
		--limit 2 \
		--cycles 480 \
		--ticks-per-cycle 1 \
		--log-level WARNING

compare-quick: ## Quick comparison (~2 min) - fewer cycles
	PYTHONPATH=. $(PYTHON) run_standalone.py \
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

calibrate-prod-scenario: ## Build app-interface calibrated scenario from housekeeping logs
	@if [ -z "$(LOGS)" ]; then \
		echo "Set LOGS to one or more log paths, e.g."; \
		echo '  make calibrate-prod-scenario LOGS="/tmp/a.log /tmp/b.log /tmp/c.log"'; \
		exit 1; \
	fi
	PYTHONPATH=. $(PYTHON) scripts/calibrate_from_housekeeping_logs.py \
		--project app-interface \
		--logs $(LOGS) \
		--emit-scenario $(PROD_SCENARIO)

tune-prod-calibration: ## Tune calibration knobs for selected CALIBRATION_POLICY
	@if [ -z "$(LOGS)" ]; then \
		echo "Set LOGS to one or more log paths, e.g."; \
		echo '  make tune-prod-calibration LOGS="/tmp/a.log /tmp/b.log /tmp/c.log"'; \
		exit 1; \
	fi
	PYTHONPATH=. $(PYTHON) scripts/tune_prod_calibration.py \
		--logs $(LOGS) \
		--project app-interface \
		--policy $(CALIBRATION_POLICY) \
		--scenario-out scenarios/app-interface-prod-calibrated-$(CALIBRATION_POLICY).yaml \
		--out-dir $(CALIBRATION_OUT_DIR)

discrimination-pass: ## Run repeatable policy-pair discrimination variants
	PYTHONPATH=. $(PYTHON) scripts/run_discrimination_pass.py \
		--base-scenario $(PROD_SCENARIO) \
		--policies $(DISCRIMINATION_POLICIES) \
		--lhs-policy $(DISCRIMINATION_LHS) \
		--rhs-policy $(DISCRIMINATION_RHS) \
		--baseline-policy $(DISCRIMINATION_BASELINE) \
		--cycles 480 \
		--limit 2 \
		--ticks-per-cycle 1

monte-carlo-small: ## Monte Carlo: 10 trials (~30 min)
	PYTHONPATH=. $(PYTHON) run_standalone.py \
		--monte-carlo 10 \
		--policy-set $(POLICY_SET) \
		--scenario $(ADV_SCENARIO) \
		--limit $(LIMIT) \
		--cycles 480 \
		--ticks-per-cycle 1 \
		--log-level WARNING

monte-carlo-medium: ## Monte Carlo: 20 trials (~60 min)
	PYTHONPATH=. $(PYTHON) run_standalone.py \
		--monte-carlo 20 \
		--policy-set $(POLICY_SET) \
		--scenario $(ADV_SCENARIO) \
		--limit $(LIMIT) \
		--cycles 480 \
		--ticks-per-cycle 1 \
		--log-level WARNING

monte-carlo-large: ## Monte Carlo: 30 trials (~90 min)
	PYTHONPATH=. $(PYTHON) run_standalone.py \
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

run-harness: ## Run REAL gitlab-housekeeping (requires qontract-reconcile deps)
	PYTHONPATH=.:$(QR_ROOT) $(PYTHON) run_harness.py \
		--sim-url $(SIM_URL) \
		--no-dry-run \
		--limit $(LIMIT)

run-harness-dry: ## Run REAL gitlab-housekeeping in dry-run mode
	PYTHONPATH=.:$(QR_ROOT) $(PYTHON) run_harness.py \
		--sim-url $(SIM_URL) \
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

report: ## Generate report from latest comparison metrics
	@if [ -f $(METRICS_FILE) ]; then \
		PYTHONPATH=. $(PYTHON) -m gitlab_hk_sim.cli report \
			--metrics $(METRICS_FILE) \
			--scenario-name "$$(basename $(SCENARIO) .yaml)" \
			--out $(REPORT_FILE); \
		echo "Report: $(REPORT_FILE)"; \
	else \
		echo "No metrics file at $(METRICS_FILE). Run a simulation first."; \
		exit 1; \
	fi

full-cycle: ## Automated: serve + run (3 cycles with ticks) + report
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
# Cleanup
# ---------------------------------------------------------------------------

clean: ## Remove generated reports and caches
	rm -rf reports/comparisons reports/monte-carlo
	rm -rf __pycache__ .pytest_cache
