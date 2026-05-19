# DMRLogger

[Русская версия README](README.ru.md)

Self-hosted BrandMeister DMR recorder/transcriber that connects directly to a
BrandMeister master via the HomeBrew/MMDVM HBP protocol, records configured
Talkgroups, decodes DMR voice, transcribes speech, enriches metadata, and posts
results to Telegram topics.

> Status: experimental but working. Treat this as radio/network automation for
> technically comfortable operators, not a polished appliance.

## What it does

- Connects to a BrandMeister HBP master as a listen-only hotspot/peer.
- Records configured `(Talkgroup, Timeslot)` routes.
- Stores raw DMRD packets, extracted AMBE payloads, metadata, decoded audio, MP3,
  and transcripts.
- Converts AMBE voice frames to WAV via `mbelib`.
- Converts WAV to Telegram-friendly MP3 via `ffmpeg`.
- Transcribes audio with `faster-whisper`.
- Looks up public DMR user metadata via RadioID.net and caches it locally.
- Posts audio and transcript messages to Telegram group topics.
- Can collect/post daily statistics and summaries.
- Supports Russian or English public Telegram captions/transcripts/summaries via configuration.

## Architecture

```text
BrandMeister HBP master
  -> bm_hbp_recorder.py
  -> recordings/*.dmrd + *.ambe33 + *.json
  -> post_direct_recordings_telegram.py
  -> dmr_ambe33_to_wav + ffmpeg + faster-whisper
  -> RadioID.net metadata cache
  -> Telegram Bot API / forum topics
  -> daily_dmr_summary.py
```

Main files:

- `bm_hbp_recorder.py` — HBP recorder; receives DMRD frames and writes per-call files.
- `bm_hbp_listen.py` — HBP protocol helpers and minimal listener/debug CLI.
- `post_direct_recordings_telegram.py` — decodes, transcribes, enriches, and posts recordings.
- `daily_dmr_summary.py` — collects 24h route stats and posts/pins summaries.
- `dmr_ambe33_to_wav.c` — AMBE33 to WAV decoder wrapper using `mbelib`.
- `run_direct_recorder.sh` — simple restart loop for the recorder.
- `run_direct_poster.sh` — simple restart loop for the poster/transcriber.
- `scripts/fetch_pistar_password.sh` — optional lab helper for Pi-Star setups.

## Requirements

System packages:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv gcc make ffmpeg sshpass
```

AMBE decoding requires `mbelib` headers and library. On distros that package it:

```bash
sudo apt-get install -y libmbe-dev
```

If `libmbe-dev` is not available, install/build `mbelib` from source, then run
`make` again. The decoder build expects `mbelib.h` and links with `-lmbe -lm`.

Python environment:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv run python -c "import faster_whisper; print('ok')"
```

## Bare-metal installation (without Docker)

This mode runs directly on the host. You install system packages, build the small
AMBE decoder binary, and run the recorder/poster shell wrappers from the git
checkout.

```bash
git clone https://github.com/hamoneria/DMRLogger.git
cd DMRLogger
cp .env.example .env
cp configs/bm_direct_routes.example.json configs/bm_direct_routes.json
make
```

Edit `.env` and `configs/bm_direct_routes.json` before running. At minimum, set
`BM_RADIO_ID`, `BM_HOTSPOT_PASSWORD`, `BM_DIRECT_TELEGRAM_BOT_TOKEN`, and the
Telegram destination/chat/topic values used by your route config.

## Configuration


### Config compatibility and future providers

DMRLogger treats route config as a compatibility contract. Existing configs that
do not contain new fields must continue to work after updates. New features are
added as optional fields with safe defaults.

Current configs without `config_version` are treated as v1. A future config may
include this optional marker:

```json
{
  "config_version": 1
}
```

Reserved future provider sections may be added later for OpenRouter summaries,
cloud transcription, Discord, Matrix, and generic webhooks. They must not be
required for existing Telegram/faster-whisper deployments. Destination objects
without an explicit `provider` are interpreted as Telegram, preserving current
behavior.

See `docs/roadmap.md` for the provider roadmap and compatibility rules.

### Telegram destination modes

Each DMR route is identified by `tg` + `slot` and can publish to one of several
Telegram layouts via `destination` in `configs/bm_direct_routes.json`:

1. **Group topics** — one Telegram group, one topic per DMR talkgroup:

```json
"destination": {"type": "topic", "chat_id_env": "TELEGRAM_CHAT_ID", "message_thread_id": 2}
```

2. **One channel per DMR talkgroup** — each route has its own `chat_id_env`:

```json
"destination": {"type": "chat", "chat_id_env": "TELEGRAM_CHAT_ID_TG2501"}
```

3. **Single channel for everything** — routes share the default chat and use
`posting.add_hashtags=true` plus per-route `hashtag` values:

```json
"posting": {"enabled": true, "default_mode": "single_chat", "add_hashtags": true, "language": "ru"}
```

### Public post language

DMRLogger can publish Telegram captions, transcript headers, Whisper language
hints, and daily summary prompts/fallback text in Russian or English.

Set the global language in route config:

```json
"posting": {"enabled": true, "language": "ru"},
"summary": {"enabled": true, "language": "ru"}
```

Supported values are `ru` and `en`. Defaults remain `ru` for compatibility.
A route may override the global value with `"language": "en"` (or legacy alias
`"post_language": "en"`). Environment defaults are also supported:

```bash
BM_POST_LANGUAGE=ru
BM_SUMMARY_LANGUAGE=ru
```

Use `posting.language` for per-recording audio captions/transcript posts and
`summary.language` for daily LLM prompts and stats-only fallback summaries.

4. **Local-only / disabled posting**:

```json
"destination": {"type": "none"}
```

Example configs are provided for the common layouts:

- `configs/bm_direct_routes.example.json` — group + topics
- `configs/bm_direct_routes.channels.example.json` — one channel per route
- `configs/bm_direct_routes.single_channel.example.json` — one channel + hashtags

### Summary posting

Summaries are controlled by the top-level `summary` block and optional per-route
`summary` overrides:

```json
"summary": {
  "enabled": true,
  "mode": "per_route",
  "interval": "24h",
  "at": "09:00",
  "timezone": "Europe/Moscow",
  "pin": true,
  "unpin_previous": true,
  "use_llm": true,
  "fallback": true,
  "language": "ru"
}
```

Docker includes an optional `summary` profile. Generate summaries only:

```bash
BM_SUMMARY_POST=0 docker compose --profile summary -f docker-compose.example.yml up -d summary
```

Generate and post them to Telegram:

```bash
BM_SUMMARY_POST=1 docker compose --profile summary -f docker-compose.example.yml up -d summary
```

`BM_SUMMARY_INTERVAL_SECONDS` controls the loop interval; default is 86400.


### Environment

`.env` is local and must not be committed.

Minimum useful values:

```bash
BM_MASTER=2503.master.brandmeister.network
BM_PORT=62031
BM_RADIO_ID=123456789
BM_HOTSPOT_PASSWORD=change-me
BM_DIRECT_TELEGRAM_BOT_TOKEN=123456:replace-me
TELEGRAM_CHAT_ID=-1000000000000
```

Optional paths; defaults are project-local:

```bash
BM_RECORDINGS_DIR=./recordings
BM_STATE_DIR=./state
BM_LOG_DIR=./logs
BM_ROUTES_CONFIG=./configs/bm_direct_routes.json
BM_AMBE_DECODER=./dmr_ambe33_to_wav
```

### Routes

Example `configs/bm_direct_routes.json`:

```json
{
  "telegram": {
    "bot_token_env": "BM_DIRECT_TELEGRAM_BOT_TOKEN",
    "chat_id_env": "TELEGRAM_CHAT_ID",
    "public_url": "https://t.me/your_channel_or_group"
  },
  "peers": [
    {
      "radio_id": 123456789,
      "groups": [
        {
          "tg": 2501,
          "slot": 1,
          "label": "TG2501",
          "message_thread_id": 2
        }
      ]
    }
  ]
}
```

`message_thread_id` is the Telegram forum topic ID. Omit it for ordinary chats
or channels.


## Docker

Docker is the recommended release path for normal users. The published image is
intended to be self-contained: it includes Python, `uv`, `ffmpeg`, `mbelib`, the
compiled `dmr_ambe33_to_wav` decoder, Python dependencies, and the default
Faster-Whisper model (`base`) baked into the image.

That means a user of the published image only needs Docker, configuration files,
secrets, and persistent volumes. They do **not** need to install Python, build
`mbelib`, install `ffmpeg`, or wait for the Whisper model to download at first
startup.

### Production quick start with a published image

Create a small runtime directory; it does not need to be a git checkout:

```bash
mkdir dmrlogger
cd dmrlogger
```

Download or copy these files from the repository:

```bash
curl -O https://raw.githubusercontent.com/hamoneria/DMRLogger/main/.env.example
curl -O https://raw.githubusercontent.com/hamoneria/DMRLogger/main/docker-compose.example.yml
mkdir -p configs
curl -o configs/bm_direct_routes.example.json   https://raw.githubusercontent.com/hamoneria/DMRLogger/main/configs/bm_direct_routes.example.json
```

Prepare local config files:

```bash
cp .env.example .env
cp configs/bm_direct_routes.example.json configs/bm_direct_routes.json
nano .env
nano configs/bm_direct_routes.json
```

Set the image name in `.env` if needed:

```bash
DMRLOGGER_IMAGE=ghcr.io/hamoneria/dmrlogger:latest
```

Start recorder and poster:

```bash
docker compose -f docker-compose.example.yml up -d
```

Watch logs:

```bash
docker compose -f docker-compose.example.yml logs -f
```

Persistent runtime data is stored outside the image in:

```text
./data/recordings
./data/state
./data/logs
```

### Development workflow

For development, build or pull an image once, then bind-mount the working tree
into `/app` so Python/shell changes are picked up without rebuilding heavy
layers:

```bash
docker build -t dmrlogger:with-model .
cp .env.example .env
cp configs/bm_direct_routes.example.json configs/bm_direct_routes.json
nano .env
nano configs/bm_direct_routes.json
docker compose -f docker-compose.dev.yml up -d
```

The Dockerfile keeps the virtualenv in `/opt/venv`, not `/app/.venv`, so the
`.:/app` bind mount used by `docker-compose.dev.yml` does not hide installed
Python dependencies.

After editing scripts, just restart the relevant service:

```bash
docker compose -f docker-compose.dev.yml restart poster
```

### Building release images locally

Build with the default baked-in model (`base`):

```bash
docker build -t dmrlogger:latest .
```


### Whisper model selection

The published image bakes in `base` as the recommended default so first startup is fast and does not require downloading a model. This is not a hard limit.

Runtime override in `.env`:

```bash
WHISPER_MODEL=small
```

Common choices:

- `tiny` — fastest, noticeably lower quality.
- `base` — recommended default for small VPS and DMR audio.
- `small` — better quality, slower; good upgrade on stronger CPU.
- `medium` — heavier; use only if the server can keep up.
- `large-v3` — best quality, usually too slow on small CPU-only VPS.

If `WHISPER_MODEL` differs from the model baked into the image, the container may download it on first use. For production, users with powerful servers can build an image with their preferred model preloaded:

```bash
docker build --build-arg WHISPER_MODEL=small -t dmrlogger:small .
```

Then set:

```bash
DMRLOGGER_IMAGE=dmrlogger:small
WHISPER_MODEL=small
```

Build with another baked-in Whisper model:

```bash
docker build --build-arg WHISPER_MODEL=small -t dmrlogger:small .
```

At runtime you can still set `WHISPER_MODEL`, but if it differs from the baked
model the container may need to download that model on first use.

Do not bake secrets into the image. Pass tokens/passwords via `.env` at runtime
and mount route config as a read-only file.

## Running

Recorder:

```bash
./run_direct_recorder.sh
```

Poster/transcriber:

```bash
./run_direct_poster.sh
```

For one-shot/manual runs:

```bash
uv run python bm_hbp_recorder.py \
  --master "$BM_MASTER" \
  --radio-id "$BM_RADIO_ID" \
  --routes-config configs/bm_direct_routes.json \
  --out-dir recordings \
  --duration 300

uv run python post_direct_recordings_telegram.py \
  --recordings-dir recordings \
  --routes-config configs/bm_direct_routes.json \
  --state-file state/bm_direct_dmrlogs_state.json \
  --once
```

## Data layout

A typical recording directory looks like:

```text
recordings/
└─ peer123456789/
   └─ TG2501_tg2501_ts1/
      └─ 2026-05-17/
         ├─ 20260517T083041Z_tg2501_ts1_src2500001_stream123.dmrd
         ├─ 20260517T083041Z_tg2501_ts1_src2500001_stream123.ambe33
         ├─ 20260517T083041Z_tg2501_ts1_src2500001_stream123.json
         ├─ 20260517T083041Z_tg2501_ts1_src2500001_stream123.wav
         ├─ 20260517T083041Z_tg2501_ts1_src2500001_stream123.mp3
         └─ 20260517T083041Z_tg2501_ts1_src2500001_stream123.txt
```

These runtime artifacts are ignored by git.


## Reliability and watchdogs

The production path is designed as a layered watchdog setup:

1. **Application heartbeat**
   - `bm_hbp_recorder.py` writes `recorder.heartbeat.json` while connected and looping.
   - `post_direct_recordings_telegram.py` writes `poster.heartbeat.json` every poll loop.
   - `run_daily_summary.sh` writes `summary.heartbeat.json` after each summary cycle.

2. **Supervisor shell wrappers**
   - `run_direct_recorder.sh` and `run_direct_poster.sh` run the Python worker as a child process.
   - If the heartbeat becomes stale, the wrapper sends `SIGTERM`, then `SIGKILL` if needed, and starts a fresh worker.
   - This handles the important failure mode where a process is still alive but stuck.

3. **Container/systemd restart policy**
   - Docker Compose uses `restart: unless-stopped` plus per-service healthchecks.
   - systemd units use `Restart=always`, `StartLimitIntervalSec=0`, and preflight checks.

4. **Atomic state writes**
   - Runtime JSON state/metadata/heartbeat files are written via temporary file + rename to reduce corruption risk after power loss or container kill.

Useful tuning variables:

```env
BM_RECORDER_MAX_STALE_SECONDS=180
BM_POSTER_MAX_STALE_SECONDS=900
BM_RECORDER_HEARTBEAT_INTERVAL=10
BM_RECORDER_STARTUP_GRACE_SECONDS=90
BM_POSTER_STARTUP_GRACE_SECONDS=120
```

Manual healthcheck:

```bash
python3 healthcheck.py --component recorder --no-heartbeat
python3 healthcheck.py --component poster --heartbeat-file state/poster.heartbeat.json --max-heartbeat-age 900
```

In Docker:

```bash
docker compose -f docker-compose.example.yml ps
docker compose -f docker-compose.example.yml logs -f recorder poster
```

Note: Docker healthchecks mark containers unhealthy, while the internal supervisor is what actively kills and restarts stale child processes. systemd/Docker restart policies then cover full wrapper/container crashes.


## Retention and disk cleanup

Long-running recorders must have retention. The project includes
`cleanup_recordings.py` and `run_cleanup.sh` to remove old recording artifacts and
keep disk usage bounded.

Default policy:

```env
BM_RETENTION_DAYS=14
BM_RETENTION_MAX_BYTES=5G
BM_RETENTION_MIN_FREE_BYTES=1G
BM_RETENTION_DRY_RUN=0
BM_CLEANUP_INTERVAL_SECONDS=3600
```

The cleanup removes complete recording artifact groups by basename, e.g.
`.dmrd`, `.ambe33`, `.json`, `.wav`, `.mp3`, `.txt`, then prunes empty
recording directories. State files, logs, configs, and route definitions are not
removed.

Dry-run first:

```bash
python3 cleanup_recordings.py \
  --recordings-dir recordings \
  --keep-days 14 \
  --max-bytes 5G \
  --min-free-bytes 1G \
  --dry-run
```

Apply:

```bash
python3 cleanup_recordings.py \
  --recordings-dir recordings \
  --keep-days 14 \
  --max-bytes 5G \
  --min-free-bytes 1G \
  --apply
```

Docker profile:

```bash
docker compose --profile cleanup -f docker-compose.example.yml up -d cleanup
```

For small VPS disks, use a tighter cap, for example:

```env
BM_RETENTION_DAYS=7
BM_RETENTION_MAX_BYTES=3G
BM_RETENTION_MIN_FREE_BYTES=2G
```

## Daily summaries

`daily_dmr_summary.py` can collect recent route activity and post a summary to a
Telegram topic:

```bash
uv run daily_dmr_summary.py collect \
  --routes-config configs/bm_direct_routes.json \
  --recordings-dir recordings \
  --hours 24

uv run daily_dmr_summary.py summarize \
  --routes-config configs/bm_direct_routes.json \
  --recordings-dir recordings \
  --hours 24 \
  --output-dir state/summaries

uv run daily_dmr_summary.py post \
  --routes-config configs/bm_direct_routes.json \
  --route-key 2501:1 \
  --message-file summary.txt
```

`summarize` works without an LLM using a stats/transcript fallback. If
`GEMINI_API_KEY` is set, it can ask Gemini for a short natural-language summary;
if Gemini is unavailable, it falls back to the local stats-only summary.

Schedule the collect/summarize/post flow with cron or a systemd timer.

## Optional Pi-Star helper

The normal startup path does **not** depend on Pi-Star. Set `BM_HOTSPOT_PASSWORD`
directly in `.env` for normal deployments.

For local/lab setups, `scripts/fetch_pistar_password.sh` can read `Password=`
from a Pi-Star/MMDVMHost device over SSH:

```bash
PISTAR_HOST=192.0.2.10 PISTAR_USER=pi-star ./scripts/fetch_pistar_password.sh
```

The recorder will only use this helper if explicitly enabled:

```bash
BM_FETCH_PISTAR_PASSWORD=1
PISTAR_HOST=192.0.2.10
```

Do not use this as the default deployment path.

## Legacy HoseLine prototype

Older HoseLine/WebSocket prototype scripts may exist in this repository for
reference. The main supported architecture is the direct HBP recorder described
above. HoseLine delivers already-decoded PCMU/G.711 audio; HBP delivers raw DMR
frames/AMBE payloads and therefore needs the AMBE decoder pipeline.

## Privacy, legality, and network etiquette

This software records and republishes radio traffic. Before running it:

- Make sure recording, transcription, and redistribution are legal in your
  jurisdiction and acceptable for the talkgroups you monitor.
- Respect BrandMeister, repeater, hotspot, and talkgroup rules.
- Be careful with personally identifying information in transcripts and metadata.
- Do not publish bot tokens, hotspot passwords, real route configs, logs, state,
  or recordings by accident.
- Use a separate hotspot ESSID/client for monitoring when appropriate.

## Development checks

```bash
make smoke
python3 -m py_compile *.py
```

Before publishing or pushing changes, run a secret/privacy scan and verify that
only example configs are tracked.
