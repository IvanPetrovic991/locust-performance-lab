.PHONY: up down clean smoke baseline stress spike soak check compare monitoring

# Number of Locust worker containers — scale load generation horizontally,
# e.g. `make up WORKERS=8`
WORKERS ?= 2

COMPOSE = docker compose
LOCUST_RUN = $(COMPOSE) run --rm --no-deps -v ./reports:/mnt/reports locust-master \
	-f /mnt/locust/ecommerce.py --host http://target-api:8000 --headless --only-summary

## Start the target API + Locust web UI (http://localhost:8089)
up:
	$(COMPOSE) up -d --build --scale locust-worker=$(WORKERS) target-api locust-master locust-worker

## Start everything including Prometheus + Grafana (http://localhost:3000)
monitoring:
	$(COMPOSE) --profile monitoring up -d --build --scale locust-worker=$(WORKERS)

down:
	$(COMPOSE) --profile monitoring down

clean: down
	rm -rf reports/*.csv reports/*.html

## Quick sanity run — 10 users, 1 minute
smoke:
	$(COMPOSE) up -d --build target-api
	$(LOCUST_RUN) -u 10 -r 5 -t 1m \
		--csv /mnt/reports/smoke --html /mnt/reports/smoke.html
	python3 scripts/check_slas.py reports/smoke_stats.csv

## Baseline load — 50 users, 5 minutes
baseline:
	$(COMPOSE) up -d --build target-api
	$(LOCUST_RUN) -u 50 -r 5 -t 5m \
		--csv /mnt/reports/baseline --html /mnt/reports/baseline.html
	python3 scripts/check_slas.py reports/baseline_stats.csv

## Stepped stress ramp 10 -> 150 users (8 minutes)
stress:
	$(COMPOSE) up -d --build target-api
	LOAD_SHAPE=stages $(LOCUST_RUN) \
		--csv /mnt/reports/stress --html /mnt/reports/stress.html

## Sudden 10x traffic spike (6 minutes)
spike:
	$(COMPOSE) up -d --build target-api
	LOAD_SHAPE=spike $(LOCUST_RUN) \
		--csv /mnt/reports/spike --html /mnt/reports/spike.html

## Constant load for SOAK_MINUTES (default 30)
soak:
	$(COMPOSE) up -d --build target-api
	LOAD_SHAPE=soak $(LOCUST_RUN) \
		--csv /mnt/reports/soak --html /mnt/reports/soak.html

## Memory-leak hunt: sample the target's memory under constant load, then
## analyze the trend. Demo the failing gate with:
##   SIMULATE_MEMORY_LEAK=true make leak-test LEAK_MINUTES=3
LEAK_MINUTES ?= 10
leak-test:
	$(COMPOSE) up -d --build target-api
	python3 scripts/memory_monitor.py \
		--container $$($(COMPOSE) ps -q target-api) \
		--interval 5 --output reports/memory.csv & \
	MONITOR_PID=$$!; \
	$(LOCUST_RUN) -u 30 -r 10 -t $(LEAK_MINUTES)m \
		--csv /mnt/reports/leak --html /mnt/reports/leak.html; \
	kill $$MONITOR_PID; \
	python3 scripts/check_memory_leak.py reports/memory.csv

## Re-run the SLA gate against the last baseline report
check:
	python3 scripts/check_slas.py reports/baseline_stats.csv

## Detect regressions between two runs:
## make compare BASELINE=reports/baseline_stats.csv CURRENT=reports/smoke_stats.csv
BASELINE ?= reports/baseline_stats.csv
CURRENT ?= reports/smoke_stats.csv
compare:
	python3 scripts/compare_runs.py $(BASELINE) $(CURRENT)
