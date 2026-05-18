CC ?= gcc
BASH ?= /usr/bin/bash
CFLAGS ?= -O2 -Wall -Wextra
LDLIBS ?= -lmbe -lm

.PHONY: all clean check-python check-shell test smoke

all: dmr_ambe33_to_wav

dmr_ambe33_to_wav: dmr_ambe33_to_wav.c
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)

check-python:
	python3 -m py_compile *.py

check-shell:
	$(BASH) -n run_direct_recorder.sh
	$(BASH) -n run_direct_poster.sh
	$(BASH) -n run_daily_summary.sh
	$(BASH) -n run_cleanup.sh
	$(BASH) -n scripts/fetch_pistar_password.sh

test:
	uv run --extra dev pytest -q

smoke: check-python check-shell
	python3 -m json.tool configs/bm_direct_routes.example.json >/dev/null
	uv run python healthcheck.py --component all --no-heartbeat --routes-config configs/bm_direct_routes.example.json >/dev/null
	uv run --extra dev pytest -q

clean:
	rm -f dmr_ambe33_to_wav
