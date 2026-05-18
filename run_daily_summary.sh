#!/usr/bin/env bash
set -Eeuo pipefail
export PATH="/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

APP_DIR="${BM_APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
cd "$APP_DIR"

if [[ -f "$APP_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$APP_DIR/.env"
  set +a
fi

STATE_DIR="${BM_STATE_DIR:-$APP_DIR/state}"
LOG_DIR="${BM_LOG_DIR:-$APP_DIR/logs}"
SUMMARY_INTERVAL_SECONDS="${BM_SUMMARY_INTERVAL_SECONDS:-86400}"
SUMMARY_ROUTE_KEYS="${BM_SUMMARY_ROUTE_KEYS:-}"
SUMMARY_ONCE="${BM_SUMMARY_ONCE:-0}"
SUMMARY_OUTPUT_DIR="${BM_SUMMARY_OUTPUT_DIR:-$STATE_DIR/summaries}"
HEARTBEAT_FILE="${BM_SUMMARY_HEARTBEAT_FILE:-$STATE_DIR/summary.heartbeat.json}"
LOG="$LOG_DIR/daily_summary.log"

mkdir -p "$STATE_DIR" "$LOG_DIR" "$SUMMARY_OUTPUT_DIR"

log() {
  echo "[$(date --iso-8601=seconds)] $*" | tee -a "$LOG"
}

write_heartbeat() {
  python3 - "$HEARTBEAT_FILE" "$1" "${2:-}" <<'PY'
import json, sys, time
from pathlib import Path
path = Path(sys.argv[1])
status = sys.argv[2]
message = sys.argv[3]
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_name(f'.{path.name}.tmp')
tmp.write_text(json.dumps({'component': 'summary', 'time': time.time(), 'status': status, 'message': message}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
tmp.replace(path)
PY
}

trim_key() {
  python3 - "$1" <<'PY'
import sys
print(sys.argv[1].strip())
PY
}

run_once() {
  mkdir -p "$SUMMARY_OUTPUT_DIR"
  local base_args=(
    --routes-config "${BM_ROUTES_CONFIG:-$APP_DIR/configs/bm_direct_routes.json}"
    --recordings-dir "${BM_RECORDINGS_DIR:-$APP_DIR/recordings}"
    --poster-state "${BM_POSTER_STATE_FILE:-$STATE_DIR/bm_direct_dmrlogs_state.json}"
    --hours "${BM_SUMMARY_HOURS:-24}"
    --output-dir "$SUMMARY_OUTPUT_DIR"
  )
  base_args+=(--summary-provider "${BM_SUMMARY_PROVIDER:-gemini}")
  base_args+=(--gemini-model "${GEMINI_MODEL:-gemini-2.5-flash}")
  base_args+=(--openrouter-model "${OPENROUTER_MODEL:-${BM_SUMMARY_MODEL:-google/gemini-2.5-flash}}")
  if [[ "${BM_SUMMARY_USE_GEMINI:-1}" == "0" ]]; then
    base_args+=(--no-gemini)
  fi
  if [[ -n "$SUMMARY_ROUTE_KEYS" ]]; then
    IFS=',' read -ra keys <<< "$SUMMARY_ROUTE_KEYS"
    for raw_key in "${keys[@]}"; do
      key="$(trim_key "$raw_key")"
      [[ -z "$key" ]] && continue
      uv run daily_dmr_summary.py summarize "${base_args[@]}" --route-key "$key"
      if [[ "${BM_SUMMARY_POST:-0}" == "1" ]]; then
        local safe="${key/:/_}"
        uv run daily_dmr_summary.py post \
          --routes-config "${BM_ROUTES_CONFIG:-$APP_DIR/configs/bm_direct_routes.json}" \
          --state-file "${BM_DAILY_SUMMARY_STATE:-$STATE_DIR/bm_daily_summary_state.json}" \
          --route-key "$key" \
          --message-file "$SUMMARY_OUTPUT_DIR/summary_${safe}.txt"
      fi
    done
  else
    uv run daily_dmr_summary.py summarize "${base_args[@]}"
    if [[ "${BM_SUMMARY_POST:-0}" == "1" ]]; then
      for file in "$SUMMARY_OUTPUT_DIR"/summary_*.txt; do
        [[ -e "$file" ]] || continue
        local name="$(basename "$file" .txt)"
        local key="${name#summary_}"
        key="${key/_/:}"
        uv run daily_dmr_summary.py post \
          --routes-config "${BM_ROUTES_CONFIG:-$APP_DIR/configs/bm_direct_routes.json}" \
          --state-file "${BM_DAILY_SUMMARY_STATE:-$STATE_DIR/bm_daily_summary_state.json}" \
          --route-key "$key" \
          --message-file "$file"
      done
    fi
  fi
}

write_heartbeat startup
while true; do
  log "running daily summary"
  if run_once; then
    write_heartbeat ok "summary cycle completed"
  else
    rc=$?
    write_heartbeat error "summary cycle failed rc=$rc"
    log "ERROR: summary cycle failed rc=$rc"
  fi
  if [[ "$SUMMARY_ONCE" == "1" ]]; then
    exit 0
  fi
  sleep "$SUMMARY_INTERVAL_SECONDS"
done
