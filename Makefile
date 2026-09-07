.PHONY: up down clean smoke baseline current stress spike soak leak-test check compare monitoring dashboard-png

# Number of Locust worker containers — scale load generation horizontally,
# e.g. `make up WORKERS=8`
WORKERS ?= 2

COMPOSE = docker compose
# --exit-code-on-error 0: pass/fail is decided by the SLA gate scripts, not by
# locust's default "any failed request = exit 1" (the demo API injects ~1%
# payment failures by design, so that default would abort every recipe here
# before its gate line ever ran).
LOCUST_RUN = $(COMPOSE) run --rm --no-deps locust-master \
	-f /mnt/locust/ecommerce.py --host http://target-api:8000 --headless --only-summary \
	--exit-code-on-error 0

## Start the target API + Locust web UI (http://localhost:8089)
up:
	$(COMPOSE) up -d --build --scale locust-worker=$(WORKERS) target-api locust-master locust-worker

## Start everything including Prometheus + Grafana (http://localhost:3000)
monitoring:
	$(COMPOSE) --profile monitoring up -d --build --scale locust-worker=$(WORKERS)

down:
	$(COMPOSE) --profile monitoring --profile report down

clean: down
	rm -rf reports/*.csv reports/*.html

## Quick sanity run — 10 users, 1 minute
smoke:
	$(COMPOSE) up -d --build --wait target-api
	$(LOCUST_RUN) -u 10 -r 5 -t 1m \
		--csv /mnt/reports/smoke --html /mnt/reports/smoke.html
	python3 scripts/check_slas.py reports/smoke_stats.csv

## Baseline load — 50 users, 5 minutes
baseline:
	$(COMPOSE) up -d --build --wait target-api
	$(LOCUST_RUN) -u 50 -r 5 -t 5m \
		--csv /mnt/reports/baseline --html /mnt/reports/baseline.html
	python3 scripts/check_slas.py reports/baseline_stats.csv

## Same profile as baseline, recorded separately — the "after" run for
## `make compare` (regressions are only meaningful between identical profiles)
current:
	$(COMPOSE) up -d --build --wait target-api
	$(LOCUST_RUN) -u 50 -r 5 -t 5m \
		--csv /mnt/reports/current --html /mnt/reports/current.html
	python3 scripts/check_slas.py reports/current_stats.csv

## Stepped stress ramp 10 -> 150 users (8 minutes)
stress:
	$(COMPOSE) up -d --build --wait target-api
	LOAD_SHAPE=stages $(LOCUST_RUN) \
		--csv /mnt/reports/stress --html /mnt/reports/stress.html

## Sudden 10x traffic spike (6 minutes)
spike:
	$(COMPOSE) up -d --build --wait target-api
	LOAD_SHAPE=spike $(LOCUST_RUN) \
		--csv /mnt/reports/spike --html /mnt/reports/spike.html

## Constant load for SOAK_MINUTES (default 30)
soak:
	$(COMPOSE) up -d --build --wait target-api
	LOAD_SHAPE=soak $(LOCUST_RUN) \
		--csv /mnt/reports/soak --html /mnt/reports/soak.html

## Memory-leak hunt: sample the target's memory under constant load, then
## analyze the trend. Demo the failing gate with:
##   SIMULATE_MEMORY_LEAK=true make leak-test LEAK_MINUTES=3
LEAK_MINUTES ?= 10
leak-test:
	$(COMPOSE) up -d --build --wait target-api
	CID=$$($(COMPOSE) ps -q target-api); \
	[ -n "$$CID" ] || { echo "target-api container not found"; exit 1; }; \
	python3 scripts/memory_monitor.py \
		--container $$CID --interval 5 --output reports/memory.csv & \
	MONITOR_PID=$$!; \
	$(LOCUST_RUN) -u 30 -r 10 -t $(LEAK_MINUTES)m \
		--csv /mnt/reports/leak --html /mnt/reports/leak.html; \
	LOCUST_EXIT=$$?; \
	kill $$MONITOR_PID 2>/dev/null; \
	[ $$LOCUST_EXIT -eq 0 ] || { echo "load test itself failed (exit $$LOCUST_EXIT)"; exit $$LOCUST_EXIT; }; \
	python3 scripts/check_memory_leak.py reports/memory.csv

## Re-run the SLA gate against the last baseline report
check:
	python3 scripts/check_slas.py reports/baseline_stats.csv

## Snapshot the live Grafana dashboard to a PNG (needs `make monitoring` up).
## Grafana renders it server-side, so it works headless — in CI, or over SSH.
DASHBOARD_PNG ?= reports/dashboard.png
DASHBOARD_MINUTES ?= 15
dashboard-png:
	$(COMPOSE) --profile report up -d grafana-renderer
	@echo "waiting for the renderer..."
	@until curl -sf http://localhost:3000/api/health >/dev/null; do sleep 2; done
	curl -sf --max-time 90 -o $(DASHBOARD_PNG) \
		"http://localhost:3000/render/d/locust-perf-lab/locust-load-test?kiosk&from=now-$(DASHBOARD_MINUTES)m&to=now&width=1600&height=1250&timeout=60"
	@echo "wrote $(DASHBOARD_PNG)"

## Detect regressions between two runs OF THE SAME PROFILE — comparing e.g.
## a 50-user baseline against a 10-user smoke is meaningless by construction.
## Typical flow: make baseline; <change something>; make current; make compare
BASELINE ?= reports/baseline_stats.csv
CURRENT ?= reports/current_stats.csv
compare:
	python3 scripts/compare_runs.py $(BASELINE) $(CURRENT)
