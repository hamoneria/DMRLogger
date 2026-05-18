#!/usr/bin/env bash
set -Eeuo pipefail

python3 -m py_compile *.py legacy/hoseline/*.py
/usr/bin/bash -n run_direct_recorder.sh
/usr/bin/bash -n run_direct_poster.sh
/usr/bin/bash -n run_daily_summary.sh
/usr/bin/bash -n run_cleanup.sh
/usr/bin/bash -n scripts/fetch_pistar_password.sh
python3 -m json.tool configs/bm_direct_routes.example.json >/dev/null
uv run python healthcheck.py --component all --no-heartbeat --routes-config configs/bm_direct_routes.example.json >/dev/null
uv run --extra dev pytest -q
