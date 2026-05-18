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
RECORDINGS_DIR="${BM_RECORDINGS_DIR:-$APP_DIR/recordings}"
CLEANUP_INTERVAL_SECONDS="${BM_CLEANUP_INTERVAL_SECONDS:-3600}"
CLEANUP_ONCE="${BM_CLEANUP_ONCE:-0}"
CLEANUP_STATE_FILE="${BM_CLEANUP_STATE_FILE:-$STATE_DIR/cleanup_state.json}"
LOG="$LOG_DIR/cleanup.log"

mkdir -p "$STATE_DIR" "$LOG_DIR" "$RECORDINGS_DIR"

log() {
  echo "[$(date --iso-8601=seconds)] $*" | tee -a "$LOG"
}

run_once() {
  local args=(
    --recordings-dir "$RECORDINGS_DIR"
    --state-file "$CLEANUP_STATE_FILE"
    --keep-days "${BM_RETENTION_DAYS:-14}"
    --max-bytes "${BM_RETENTION_MAX_BYTES:-5G}"
    --min-free-bytes "${BM_RETENTION_MIN_FREE_BYTES:-1G}"
  )
  if [[ "${BM_RETENTION_DRY_RUN:-0}" == "1" ]]; then
    args+=(--dry-run)
  else
    args+=(--apply)
  fi
  python3 cleanup_recordings.py "${args[@]}"
}

log "cleanup loop start interval=${CLEANUP_INTERVAL_SECONDS}s retention_days=${BM_RETENTION_DAYS:-14} max=${BM_RETENTION_MAX_BYTES:-5G} min_free=${BM_RETENTION_MIN_FREE_BYTES:-1G} dry_run=${BM_RETENTION_DRY_RUN:-0}"
while true; do
  log "cleanup cycle start"
  if run_once >> "$LOG" 2>&1; then
    log "cleanup cycle ok"
  else
    rc=$?
    log "ERROR: cleanup cycle failed rc=$rc"
  fi
  if [[ "$CLEANUP_ONCE" == "1" ]]; then
    exit 0
  fi
  sleep "$CLEANUP_INTERVAL_SECONDS"
done
