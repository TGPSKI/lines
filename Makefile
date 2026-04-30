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
#   make compare                # compare all policies
#   make tick                   # advance sim by one tick
#   make metrics                # fetch current metrics
#   make state                  # fetch current state
#   make reset                  # reset sim to initial state
#   make report                 # generate report from last run
#   make full-cycle             # serve + run + ticks + report (automated)

SHELL := /bin/bash
SIM_PORT ?= 8080
SIM_HOST ?= 127.0.0.1
SIM_URL := http://$(SIM_HOST):$(SIM_PORT)
SCENARIO ?= scenarios/mvp-active-cap.yaml
METRICS_DIR ?= reports/last-run
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
        compare tick ticks metrics state reset report full-cycle clean help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

test: ## Run unit tests
	$(PYTEST) tools/gitlab_housekeeping_perf_sim/tests/ -v

validate: ## Validate all scenario YAML files
	@for f in scenarios/*.yaml; do \
		echo "--- $$f ---"; \
		$(PYTHON) -c "import sys; sys.path.insert(0, '.'); from gitlab_hk_sim.cli import cli; cli()" validate --scenario "$$f"; \
		echo ""; \
	done

serve: ## Start the sim server (use SCENARIO= to pick scenario)
	@mkdir -p $(METRICS_DIR)
	PYTHONPATH=. $(PYTHON) -m gitlab_hk_sim.cli serve \
		--scenario $(SCENARIO) \
		--host $(SIM_HOST) \
		--port $(SIM_PORT) \
		--metrics-out $(METRICS_FILE)

serve-bg: ## Start sim server in background
	@mkdir -p $(METRICS_DIR)
	@echo "Starting sim server on $(SIM_URL) with scenario $(SCENARIO)..."
	PYTHONPATH=. nohup $(PYTHON) -m gitlab_hk_sim.cli serve \
		--scenario $(SCENARIO) \
		--host $(SIM_HOST) \
		--port $(SIM_PORT) \
		--metrics-out $(METRICS_FILE) \
		> $(METRICS_DIR)/server.log 2>&1 &
	@echo $$! > $(METRICS_DIR)/server.pid
	@sleep 2
	@if curl -sf $(SIM_URL)/api/v4/user > /dev/null 2>&1; then \
		echo "Server running (PID $$(cat $(METRICS_DIR)/server.pid))"; \
	else \
		echo "Server failed to start. Check $(METRICS_DIR)/server.log"; \
		exit 1; \
	fi

kill-server: ## Kill background sim server
	@if [ -f $(METRICS_DIR)/server.pid ]; then \
		kill $$(cat $(METRICS_DIR)/server.pid) 2>/dev/null || true; \
		rm -f $(METRICS_DIR)/server.pid; \
		echo "Server stopped"; \
	else \
		echo "No PID file found"; \
	fi

run: ## Run standalone driver against sim
	PYTHONPATH=. $(PYTHON) run_standalone.py \
		--sim-url $(SIM_URL) \
		--policy $(POLICY) \
		--limit $(LIMIT) \
		--cycles $(CYCLES) \
		--ticks-per-cycle $(TICKS_PER_CYCLE)

compare: ## Run all policies and produce comparison table
	PYTHONPATH=. $(PYTHON) run_standalone.py \
		--compare \
		--scenario $(SCENARIO) \
		--port $(SIM_PORT) \
		--limit $(LIMIT) \
		--cycles $(CYCLES) \
		--ticks-per-cycle $(TICKS_PER_CYCLE)

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

report: ## Generate report from last metrics file
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

clean: ## Remove generated reports
	rm -rf $(METRICS_DIR)/*.ndjson $(METRICS_DIR)/*.md $(METRICS_DIR)/*.log $(METRICS_DIR)/*.pid
	rm -rf __pycache__ .pytest_cache
