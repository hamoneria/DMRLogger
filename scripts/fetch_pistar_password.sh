#!/usr/bin/env bash
set -Eeuo pipefail

# Optional helper for local/lab setups: read BrandMeister hotspot password
# from a Pi-Star/MMDVMHost device. Main recorder startup does NOT use this
# unless BM_FETCH_PISTAR_PASSWORD=1 is explicitly set.

if [[ -z "${PISTAR_HOST:-}" ]]; then
  echo "PISTAR_HOST is required" >&2
  exit 2
fi

export SSHPASS="${PISTAR_LOGIN_PASSWORD:?PISTAR_LOGIN_PASSWORD is required for Pi-Star password helper}"
sshpass -e ssh \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/tmp/hermes_pistar_known_hosts \
  -o ConnectTimeout="${PISTAR_CONNECT_TIMEOUT:-8}" \
  "${PISTAR_USER:-pi-star}@${PISTAR_HOST}" \
  "awk -F= '/^Password=/{gsub(/\"/,\"\",\$2); print \$2; exit}' /etc/mmdvmhost"
