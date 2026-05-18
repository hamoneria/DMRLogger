# systemd deployment

This is an example production deployment for a Linux host. Adjust paths, user,
and environment file locations for your system.

## Suggested layout

```text
/opt/dmrlogger/        # git checkout
/etc/dmrlogger.env     # secrets/env, chmod 600
/etc/dmrlogger.routes.json
/var/lib/dmrlogger/recordings/
/var/lib/dmrlogger/state/
```

Create a service user:

```bash
sudo useradd --system --home /var/lib/dmrlogger --create-home --shell /usr/sbin/nologin bm-dmr
sudo mkdir -p /opt/dmrlogger /var/lib/dmrlogger/{recordings,state}
sudo chown -R bm-dmr:bm-dmr /var/lib/dmrlogger
```

Install code and build the decoder:

```bash
cd /opt/dmrlogger
sudo -u bm-dmr git clone https://github.com/your-name/dmrlogger.git .
sudo apt-get install -y python3 gcc make ffmpeg libmbe-dev
sudo -u bm-dmr /usr/bin/make
sudo -u bm-dmr uv run python -c "import faster_whisper; print('ok')"
```

Create `/etc/dmrlogger.env`:

```bash
BM_APP_DIR=/opt/dmrlogger
BM_RECORDINGS_DIR=/var/lib/dmrlogger/recordings
BM_STATE_DIR=/var/lib/dmrlogger/state
BM_ROUTES_CONFIG=/etc/dmrlogger.routes.json
BM_AMBE_DECODER=/opt/dmrlogger/dmr_ambe33_to_wav

BM_MASTER=2503.master.brandmeister.network
BM_PORT=62031
BM_RADIO_ID=123456789
BM_HOTSPOT_PASSWORD=replace-me

BM_DIRECT_TELEGRAM_BOT_TOKEN=123456:replace-me
TELEGRAM_CHAT_ID=-1000000000000
```

Protect it:

```bash
sudo chown root:bm-dmr /etc/dmrlogger.env
sudo chmod 640 /etc/dmrlogger.env
```

Create `/etc/dmrlogger.routes.json` from
`configs/bm_direct_routes.example.json` and edit route IDs/topics.

## Units

Copy example units:

```bash
sudo cp deploy/systemd/bm-dmr-recorder.service /etc/systemd/system/
sudo cp deploy/systemd/bm-dmr-poster.service /etc/systemd/system/
sudo cp deploy/systemd/bm-dmr-summary.service /etc/systemd/system/  # optional
sudo cp deploy/systemd/bm-dmr-cleanup.service /etc/systemd/system/
sudo cp deploy/systemd/bm-dmr-cleanup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bm-dmr-recorder.service bm-dmr-poster.service
# Optional daily summary loop:
# sudo systemctl enable --now bm-dmr-summary.service
sudo systemctl enable --now bm-dmr-cleanup.timer
```

Logs:

```bash
journalctl -u bm-dmr-recorder.service -f
journalctl -u bm-dmr-poster.service -f
journalctl -u bm-dmr-summary.service -f  # optional
journalctl -u bm-dmr-cleanup.service -n 100 --no-pager
```

Restart after config changes:

```bash
sudo systemctl restart bm-dmr-recorder.service bm-dmr-poster.service
# Optional:
# sudo systemctl restart bm-dmr-summary.service
```


## Reliability model

The services intentionally use more than one recovery layer:

- The Python recorder/poster write heartbeat JSON files under `BM_STATE_DIR`.
- The shell wrappers monitor those heartbeats and kill/restart stale child processes.
- systemd restarts the wrapper itself if it crashes or exits.
- `ExecStartPre` runs static healthchecks before each start: route JSON, writable data directories, decoder, ffmpeg/imports, and BrandMeister DNS resolution.
- State and metadata JSON writes are atomic (`tmp` file + rename), so an abrupt reboot is less likely to leave partial JSON.

Recommended production values in `/etc/dmrlogger.env`:

```bash
BM_RECORDER_MAX_STALE_SECONDS=180
BM_POSTER_MAX_STALE_SECONDS=900
BM_RECORDER_HEARTBEAT_FILE=/var/lib/dmrlogger/state/recorder.heartbeat.json
BM_POSTER_HEARTBEAT_FILE=/var/lib/dmrlogger/state/poster.heartbeat.json
BM_SUMMARY_HEARTBEAT_FILE=/var/lib/dmrlogger/state/summary.heartbeat.json
```

Healthcheck examples:

```bash
sudo -u bm-dmr python3 /opt/dmrlogger/healthcheck.py --component recorder --no-heartbeat
sudo -u bm-dmr python3 /opt/dmrlogger/healthcheck.py \
  --component poster \
  --heartbeat-file /var/lib/dmrlogger/state/poster.heartbeat.json \
  --max-heartbeat-age 900
```

If a service is alive but stale, inspect:

```bash
journalctl -u bm-dmr-recorder.service -n 200 --no-pager
journalctl -u bm-dmr-poster.service -n 200 --no-pager
sudo cat /var/lib/dmrlogger/state/recorder.heartbeat.json
sudo cat /var/lib/dmrlogger/state/poster.heartbeat.json
```


## Retention cleanup

Enable cleanup in production. It deletes old recording artifact groups from
`BM_RECORDINGS_DIR` but does not remove configs, state, or logs.

Recommended env values for small VPS disks:

```bash
BM_RETENTION_DAYS=7
BM_RETENTION_MAX_BYTES=3G
BM_RETENTION_MIN_FREE_BYTES=2G
BM_RETENTION_DRY_RUN=0
BM_CLEANUP_INTERVAL_SECONDS=3600
```

Dry-run manually before enabling deletion:

```bash
sudo -u bm-dmr BM_RETENTION_DRY_RUN=1 BM_CLEANUP_ONCE=1 \
  /opt/dmrlogger/run_cleanup.sh
```

Run once with deletion:

```bash
sudo -u bm-dmr BM_CLEANUP_ONCE=1 /opt/dmrlogger/run_cleanup.sh
```

## Daily summary scheduling

If using `daily_dmr_summary.py`, prefer a systemd timer or cron job that runs a
small wrapper script. Keep LLM/API keys in the same protected environment file.

## Notes

- Do not use the Pi-Star password helper in production unless you explicitly need
  it. Prefer `BM_HOTSPOT_PASSWORD` in the protected env file.
- Keep recordings and state outside the git checkout.
- Rotate or clean recordings according to your retention policy.
