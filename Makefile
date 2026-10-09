SHELL := /bin/bash
.PHONY: help setup up down seed bench bench1 bench2 ceiling gil collect clean smoke

# Run as root where there is no sudo (containers), otherwise via sudo.
SUDO := $(shell [ "$$(id -u)" = 0 ] && echo "" || echo "sudo -E")

help:
	@echo "make setup    provision the host natively (installs pg17/pgbouncer/redis/oha/python)"
	@echo "make up       start postgres + pgbouncer + redis, pinned to APP_CORES"
	@echo "make seed     create and fill the exams table (200k rows)"
	@echo "make smoke    fast sanity pass: 1 rep, short windows, C0 + C4. RUN THIS FIRST."
	@echo "make bench    EVERYTHING: Exp 1 (9 configs) + Exp 2 (Flask) + ceiling + Exp 3 + collect"
	@echo "make bench1   Experiment 1 only (9 configs x 3 reps)"
	@echo "make bench2   Experiment 2 only (Flask)"
	@echo "make ceiling  harness ceiling (no-work endpoint) — proves the generator is not the limit"
	@echo "make gil      Experiment 3 (GIL / free-threading microbenchmarks)"
	@echo "make collect  re-aggregate results/raw into results/tables.md"
	@echo "make down     stop the services"
	@echo ""
	@echo "Takes ~45 min end to end. Override the topology for other hardware:"
	@echo "  APP_CORES=0-3 GEN_CORES=4-5 make bench"

setup:
	$(SUDO) bash native/setup.sh

up:
	$(SUDO) bash native/services.sh start

down:
	$(SUDO) bash native/services.sh stop

seed:
	.venv/bin/python bench/seed.py | tee results/dataset.json

# The brief asks for one target that reproduces everything. This is it.
bench:
	bash bench/run_all.sh
	bash bench/ceiling.sh
	bash gil/run_gil.sh
	.venv/bin/python bench/collect.py
	@echo ""
	@echo "Done. results/tables.md, results/ceiling.json, results/gil/ ."
	@echo "Environment record: results/environment.txt"

ceiling:
	bash bench/ceiling.sh

bench1:
	CONFIGS="C0 C1 C2 C3 C4 B1 B2 B3 B4" SKIP_FLASK=1 bash bench/run_all.sh

bench2:
	bash bench/run_flask.sh

gil:
	bash gil/run_gil.sh

collect:
	.venv/bin/python bench/collect.py

smoke:
	REPS=1 WARMUP_S=3 MEASURE_S=5 CONFIGS="C0 C4" SKIP_FLASK=1 bash bench/run_all.sh

clean:
	rm -rf results/raw/* results/logs/*
