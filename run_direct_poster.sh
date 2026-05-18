#!/usr/bin/env bash
set -Eeuo pipefail

export PATH="/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

APP_DIR="${BM_APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
cd "$APP_DIR"

# Optional local config. This file is intentionally gitignored.
if [[ -f "$APP_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$APP_DIR/.env"
  set +a
fi

RECORDINGS_DIR="${BM_RECORDINGS_DIR:-$APP_DIR/recordings}"
STATE_DIR="${BM_STATE_DIR:-$APP_DIR/state}"
LOG_DIR="${BM_LOG_DIR:-$APP_DIR/logs}"
ROUTES_CONFIG="${BM_ROUTES_CONFIG:-$APP_DIR/configs/bm_direct_routes.json}"
STATE_FILE="${BM_POSTER_STATE_FILE:-$STATE_DIR/bm_direct_dmrlogs_state.json}"
DECODER="${BM_AMBE_DECODER:-$APP_DIR/dmr_ambe33_to_wav}"
HEARTBEAT_FILE="${BM_POSTER_HEARTBEAT_FILE:-$STATE_DIR/poster.heartbeat.json}"
MAX_STALE_SECONDS="${BM_POSTER_MAX_STALE_SECONDS:-900}"
STARTUP_GRACE_SECONDS="${BM_POSTER_STARTUP_GRACE_SECONDS:-120}"
LOG="$LOG_DIR/direct_poster.log"

mkdir -p "$LOG_DIR" "$STATE_DIR" "$RECORDINGS_DIR"
export PYTHONUNBUFFERED=1

log() {
  echo "[$(date --iso-8601=seconds)] $*" >> "$LOG"
}

heartbeat_age() {
  python3 - "$HEARTBEAT_FILE" <<'PY'
import json, sys, time
from pathlib import Path
p = Path(sys.argv[1])
try:
    data = json.loads(p.read_text(encoding='utf-8'))
    print(max(0, int(time.time() - float(data.get('time', 0)))))
except Exception:
    print(999999)
PY
}

stop_child() {
  local pid="$1"
  local reason="$2"
  if kill -0 "$pid" 2>/dev/null; then
    log "direct_poster stopping pid=$pid reason=$reason"
    kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || return 0
      sleep 1
    done
    log "direct_poster killing stuck pid=$pid reason=$reason"
    kill -KILL "$pid" 2>/dev/null || true
  fi
}

log "direct_poster supervisor start heartbeat=$HEARTBEAT_FILE max_stale=${MAX_STALE_SECONDS}s"
while true; do
  rm -f "$HEARTBEAT_FILE"
  log "direct_poster run start"
  uv run --with faster-whisper post_direct_recordings_telegram.py \
    --recordings-dir "$RECORDINGS_DIR" \
    --routes-config "$ROUTES_CONFIG" \
    --state-file "$STATE_FILE" \
    --decoder "$DECODER" \
    --poll "${BM_POSTER_POLL:-5}" \
    --min-duration "${BM_MIN_DURATION:-3}" \
    --max-per-loop "${BM_POSTER_MAX_PER_LOOP:-2}" \
    --heartbeat-file "$HEARTBEAT_FILE" \
    >> "$LOG" 2>&1 &
  child=$!
  started_at=$(date +%s)
  rc=0
  while kill -0 "$child" 2>/dev/null; do
    sleep 10
    now=$(date +%s)
    age=$(heartbeat_age)
    if (( now - started_at > STARTUP_GRACE_SECONDS && age > MAX_STALE_SECONDS )); then
      log "ERROR: poster heartbeat stale age=${age}s max=${MAX_STALE_SECONDS}s; killing child"
      stop_child "$child" "stale-heartbeat"
      rc=124
      break
    fi
  done
  if [[ "$rc" == "0" ]]; then
    wait "$child" || rc=$?
  else
    wait "$child" 2>/dev/null || true
  fi
  log "direct_poster exited rc=$rc; restart in 10s"
  sleep 10
done
