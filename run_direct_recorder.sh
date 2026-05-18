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
HEARTBEAT_FILE="${BM_RECORDER_HEARTBEAT_FILE:-$STATE_DIR/recorder.heartbeat.json}"
HEARTBEAT_INTERVAL="${BM_RECORDER_HEARTBEAT_INTERVAL:-10}"
MAX_STALE_SECONDS="${BM_RECORDER_MAX_STALE_SECONDS:-180}"
STARTUP_GRACE_SECONDS="${BM_RECORDER_STARTUP_GRACE_SECONDS:-90}"
LOG="$LOG_DIR/direct_recorder.log"

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
    log "direct_recorder stopping pid=$pid reason=$reason"
    kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
      kill -0 "$pid" 2>/dev/null || return 0
      sleep 1
    done
    log "direct_recorder killing stuck pid=$pid reason=$reason"
    kill -KILL "$pid" 2>/dev/null || true
  fi
}

log "direct_recorder supervisor start heartbeat=$HEARTBEAT_FILE max_stale=${MAX_STALE_SECONDS}s"
while true; do
  if [[ "${BM_FETCH_PISTAR_PASSWORD:-0}" == "1" ]]; then
    if [[ -z "${PISTAR_HOST:-}" ]]; then
      log "ERROR: PISTAR_HOST is required when BM_FETCH_PISTAR_PASSWORD=1"
      sleep 60
      continue
    fi
    log "fetching hotspot password from Pi-Star helper (opt-in)"
    export SSHPASS="${PISTAR_LOGIN_PASSWORD:?PISTAR_LOGIN_PASSWORD is required for Pi-Star password helper}"
    BM_HOTSPOT_PASSWORD="$(
      sshpass -e ssh \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/tmp/dmrlogger_pistar_known_hosts \
        -o ConnectTimeout=8 \
        "${PISTAR_USER:-pi-star}@${PISTAR_HOST}" \
        "awk -F= '/^Password=/{gsub(/\"/,\"\",\$2); print \$2; exit}' /etc/mmdvmhost"
    )"
    export BM_HOTSPOT_PASSWORD
  fi

  if [[ -z "${BM_HOTSPOT_PASSWORD:-}" ]]; then
    log "ERROR: BM_HOTSPOT_PASSWORD is required; set it in env/.env or enable the opt-in Pi-Star helper"
    sleep 60
    continue
  fi
  if [[ -z "${BM_RADIO_ID:-}" ]]; then
    log "ERROR: BM_RADIO_ID is required; set it in env/.env"
    sleep 60
    continue
  fi

  rm -f "$HEARTBEAT_FILE"
  log "direct_recorder run start"
  python3 bm_hbp_recorder.py \
    --master "${BM_MASTER:-2503.master.brandmeister.network}" \
    --port "${BM_PORT:-62031}" \
    --radio-id "${BM_RADIO_ID}" \
    --routes-config "$ROUTES_CONFIG" \
    --duration "${BM_RECORDER_DURATION:-3600}" \
    --out-dir "$RECORDINGS_DIR" \
    --min-voice-frames "${BM_MIN_VOICE_FRAMES:-20}" \
    --gap-timeout "${BM_GAP_TIMEOUT:-2.5}" \
    --heartbeat-file "$HEARTBEAT_FILE" \
    --heartbeat-interval "$HEARTBEAT_INTERVAL" \
    >> "$LOG" 2>&1 &
  child=$!
  started_at=$(date +%s)
  rc=0
  while kill -0 "$child" 2>/dev/null; do
    sleep 10
    now=$(date +%s)
    age=$(heartbeat_age)
    if (( now - started_at > STARTUP_GRACE_SECONDS && age > MAX_STALE_SECONDS )); then
      log "ERROR: recorder heartbeat stale age=${age}s max=${MAX_STALE_SECONDS}s; killing child"
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

  if [[ "${BM_FETCH_PISTAR_PASSWORD:-0}" == "1" ]]; then
    unset BM_HOTSPOT_PASSWORD
  fi
  log "direct_recorder exited rc=$rc; restart in 10s"
  sleep 10
done
