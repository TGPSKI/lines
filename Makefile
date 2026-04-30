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
#   make compare                # compare all policies (default scenario)
#   make compare-advanced       # compare all policies (advanced scenario)
#   make compare-quick          # quick comparison (~2 min)
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
POLICY_SET ?= phase0
METRICS_DIR := reports/comparisons/latest
METRICS_FILE := $(METRICS_DIR)/metrics.ndjson
REPORT_FILE := $(METRICS_DIR)/summary.md
LIMIT ?= 8
TICKS ?= 10
CYCLES ?= 3
TICKS_PER_CYCLE ?= 4
POLICY ?= active-cap

# Use uv run from the repo root to get the full dependency set
UV_RUN := cd ../.. && uv run --directory tools/gitlab_housekeeping_perf_sim

# For direct python invocations within this directory
PYTHON := cd ../.. && uv run python
PYTEST := cd ../.. && uv run pytest

# Path to qontract-reconcile root (two levels up from this tool)
QR_ROOT := $(shell cd ../.. && pwd)

.PHONY: test validate serve serve-bg kill-server run run-harness run-harness-dry \
        compare compare-advanced compare-quick \
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
		--scenario $(SCENARIO) \
		--port $(SIM_PORT) \
		--limit $(LIMIT) \
		--cycles $(CYCLES) \
		--ticks-per-cycle $(TICKS_PER_CYCLE)

compare-advanced: ## Compare all policies against advanced scenario (8h sim)
	PYTHONPATH=. $(PYTHON) run_standalone.py \
		--compare \
		--scenario $(ADV_SCENARIO) \
		--port $(SIM_PORT) \
		--limit $(LIMIT) \
		--cycles $(CYCLES) \
		--ticks-per-cycle $(TICKS_PER_CYCLE)

compare-quick: ## Quick comparison (~2 min) - fewer cycles
	PYTHONPATH=. $(PYTHON) run_standalone.py \
		--compare \
		--scenario $(SCENARIO) \
		--port $(SIM_PORT) \
		--limit $(LIMIT) \
		--cycles 20 \
		--ticks-per-cycle $(TICKS_PER_CYCLE)

# ---------------------------------------------------------------------------
# Monte Carlo (parallel trials with statistical analysis)
# ---------------------------------------------------------------------------

monte-carlo-small: ## Monte Carlo: 10 trials (~10 min)
	PYTHONPATH=. $(PYTHON) run_standalone.py \
		--monte-carlo 10 \
		--policy-set $(POLICY_SET) \
		--scenario $(ADV_SCENARIO) \
		--limit $(LIMIT) \
		--cycles $(CYCLES) \
		--ticks-per-cycle $(TICKS_PER_CYCLE)

monte-carlo-medium: ## Monte Carlo: 20 trials (~20 min)
	PYTHONPATH=. $(PYTHON) run_standalone.py \
		--monte-carlo 20 \
		--policy-set $(POLICY_SET) \
		--scenario $(ADV_SCENARIO) \
		--limit $(LIMIT) \
		--cycles $(CYCLES) \
		--ticks-per-cycle $(TICKS_PER_CYCLE)

monte-carlo-large: ## Monte Carlo: 30 trials (~30 min)
	PYTHONPATH=. $(PYTHON) run_standalone.py \
		--monte-carlo 30 \
		--policy-set $(POLICY_SET) \
		--scenario $(ADV_SCENARIO) \
		--limit $(LIMIT) \
		--cycles $(CYCLES) \
		--ticks-per-cycle $(TICKS_PER_CYCLE)

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
