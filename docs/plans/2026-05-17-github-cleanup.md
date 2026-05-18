# DMRLogger GitHub Cleanup Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Привести текущий рабочий прототип BrandMeister DMR recorder/transcriber к состоянию, которое не стыдно выложить на GitHub.

**Architecture:** Сохраняем текущую рабочую HBP-архитектуру: recorder принимает DMRD/AMBE из BrandMeister, poster декодирует AMBE в audio, транскрибирует через Whisper, обогащает RadioID и публикует в Telegram topics. Перед публикацией отделяем production-конфиги/секреты/логи/записи от кода, документируем зависимости и делаем воспроизводимый запуск.

**Tech Stack:** Python 3.10+, uv, faster-whisper, ffmpeg, mbelib, gcc, Telegram Bot API, RadioID.net API, BrandMeister HBP/HomeBrew Protocol, optional Gemini API for daily summaries.

---

## Current State Summary

Рабочая директория:

```text
<project-dir>
```

Боевые данные/состояние вне репозитория:

```text
<recordings-dir>
<state-dir>/bm_direct_dmrlogs_state.json
<state-dir>/bm_daily_summary_state.json
```

Текущие основные компоненты:

- `bm_hbp_recorder.py` — прямой HBP/UDP recorder BrandMeister.
- `bm_hbp_listen.py` — HBP helpers/protocol parsing/build config.
- `post_direct_recordings_telegram.py` — AMBE decode, MP3, Whisper, RadioID enrichment, Telegram posting.
- `daily_dmr_summary.py` — ежедневная статистика/summary/pin в Telegram.
- `dmr_ambe33_to_wav.c` + `dmr_ambe33_to_wav` — AMBE33 -> WAV decoder wrapper на mbelib.
- `run_direct_recorder.sh` — bash supervisor для recorder.
- `run_direct_poster.sh` — bash supervisor для poster.
- `bm_direct_routes.json` — текущий боевой route config.
- `bm_hose_recorder.py`, `post_and_transcribe_telegram.py`, `bm_hose_debug_events.py` — legacy HoseLine-прототипы.

---

## GitHub Readiness Checklist

### Task 1: Create repository hygiene files

**Objective:** Исключить боевые данные, секреты, логи, кеши и записи из будущего GitHub repo.

**Files:**

- Create: `.gitignore`
- Create: `.env.example`
- Create: `configs/bm_direct_routes.example.json`

**Actions:**

1. Add `.gitignore` with at least:

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
venv/
.cache/

# Local env/secrets
.env
*.env
secrets.*

# Runtime state/logs
logs/
*.log
state/
*.sqlite
*.db

# Recordings/media
recordings/
brandmeister-direct-recordings*/
*.dmrd
*.ambe33
*.wav
*.mp3
*.ogg
*.txt

# Local compiled artifacts
/dmr_ambe33_to_wav
```

2. Add `.env.example` with placeholder variables:

```bash
BM_MASTER=2503.master.brandmeister.network
BM_RADIO_ID=123456789
BM_HOTSPOT_PASSWORD=change-me
BM_DIRECT_TELEGRAM_BOT_TOKEN=123456:replace-me
TELEGRAM_CHAT_ID=-1000000000000
PISTAR_HOST=<pistar-host>
PISTAR_USER=pi-star
PISTAR_LOGIN_PASSWORD=change-me
GEMINI_API_KEY=replace-me-if-used
```

3. Add example route config with fake IDs only.

**Verification:**

Run:

```bash
git status --short
```

Expected: no recordings/logs/state files should appear as trackable intended files.

---

### Task 2: Move production config out of publishable defaults

**Objective:** Убрать реальные chat IDs/radio IDs/пути из publishable config, оставить только examples и env-based defaults.

**Files:**

- Modify: `bm_direct_routes.json` or move to local-only config.
- Create: `configs/bm_direct_routes.example.json`
- Modify: scripts that assume `absolute local paths` paths.

**Actions:**

1. Treat current `bm_direct_routes.json` as local config and do not publish it as example.
2. Add `--routes-config` defaults that can point to `configs/bm_direct_routes.json` or env var.
3. Replace hardcoded Telegram chat/topic example values with placeholders in docs.

**Verification:**

Run content search:

```bash
rg --hidden --glob '!logs/**' --glob '!*.json' '(<telegram-chat-id>|<radio-id>|192\.168\.0\.10|ttt)' .
```

Expected: remaining occurrences are either documented as examples/placeholders or intentionally local-only ignored files.

---

### Task 3: Parameterize absolute paths

**Objective:** Сделать запуск переносимым, без `absolute local paths` в коде/скриптах.

**Files:**

- Modify: `run_direct_recorder.sh`
- Modify: `run_direct_poster.sh`
- Modify: `daily_dmr_summary.py`
- Modify: `post_direct_recordings_telegram.py` if needed.

**Actions:**

1. Add env-based root paths:

```bash
APP_DIR="${BM_APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
RECORDINGS_DIR="${BM_RECORDINGS_DIR:-$APP_DIR/recordings}"
STATE_DIR="${BM_STATE_DIR:-$APP_DIR/state}"
ROUTES_CONFIG="${BM_ROUTES_CONFIG:-$APP_DIR/configs/bm_direct_routes.json}"
```

2. Use these variables in recorder/poster scripts.
3. Keep current local paths configurable through env, not hardcoded.

**Verification:**

Run:

```bash
bash -n run_direct_recorder.sh
bash -n run_direct_poster.sh
python3 -m py_compile *.py
```

Expected: syntax OK.

---

### Task 4: Add pyproject.toml / dependency story

**Objective:** Сделать Python environment воспроизводимым.

**Files:**

- Create: `pyproject.toml`
- Optionally create: `requirements.txt` if simpler for users.

**Actions:**

1. Define project metadata.
2. Add runtime dependencies:

```toml
dependencies = [
  "faster-whisper>=1.1.0",
]
```

3. Decide whether to keep PEP 723 inline script deps or move to package-level dependencies.

**Verification:**

Run:

```bash
uv run python -m py_compile *.py
uv run --with faster-whisper python -c "import faster_whisper; print('ok')"
```

Expected: imports and compile succeed.

---

### Task 5: Make AMBE decoder build reproducible

**Objective:** Документировать и автоматизировать сборку `dmr_ambe33_to_wav`.

**Files:**

- Create: `Makefile`
- Modify: `README.md`
- Possibly modify: `dmr_ambe33_to_wav.c`

**Actions:**

1. Add Makefile target:

```makefile
CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra
LDLIBS ?= -lmbe -lm

all: dmr_ambe33_to_wav

dmr_ambe33_to_wav: dmr_ambe33_to_wav.c
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)

clean:
	rm -f dmr_ambe33_to_wav
```

2. Document required system packages:

```bash
sudo apt-get update
sudo apt-get install -y gcc make ffmpeg sshpass libmbe-dev
```

If `libmbe-dev` package is unavailable on target distro, document build-from-source alternative.

**Verification:**

Run:

```bash
make clean && make
./dmr_ambe33_to_wav 2>&1 | head
```

Expected: binary builds; usage message appears when called without args.

---

### Task 6: Rewrite README around current HBP architecture

**Objective:** README должен описывать реальный текущий проект, а HoseLine — только как legacy/fallback.

**Files:**

- Rewrite: `README.md`

**Required sections:**

1. What this is.
2. Architecture diagram/text:

```text
BrandMeister HBP master
  -> bm_hbp_recorder.py
  -> .dmrd/.ambe33/.json
  -> post_direct_recordings_telegram.py
  -> dmr_ambe33_to_wav + ffmpeg + faster-whisper
  -> RadioID.net enrichment
  -> Telegram topics
  -> daily_dmr_summary.py
```

3. Requirements.
4. Installation.
5. Config.
6. Running recorder.
7. Running poster.
8. Daily summaries.
9. Data layout.
10. Legal/ethics/privacy notice.
11. Legacy HoseLine note.

**Verification:**

README should allow a new user to understand setup without reading Telegram history.

---

### Task 7: Separate legacy HoseLine code

**Objective:** Убрать смешение текущей HBP-архитектуры и старого HoseLine прототипа.

**Files:**

- Move: `bm_hose_recorder.py` -> `legacy/hoseline/bm_hose_recorder.py`
- Move: `post_and_transcribe_telegram.py` -> `legacy/hoseline/post_and_transcribe_telegram.py`
- Move: `bm_hose_debug_events.py` -> `legacy/hoseline/bm_hose_debug_events.py`
- Modify imports/docs if needed.

**Decision point:** Можно пока не делать полноценный package refactor; достаточно аккуратного `legacy/`.

**Verification:**

Run:

```bash
python3 -m py_compile *.py legacy/hoseline/*.py
```

Expected: current HBP scripts still compile.

---

### Task 8: Add minimal tests/smoke checks

**Objective:** Иметь быстрые проверки перед коммитом/публикацией.

**Files:**

- Create: `tests/test_routes.py`
- Create: `tests/test_metadata_helpers.py` if helpers are extracted.
- Create: `scripts/smoke_check.sh`

**Actions:**

1. Add tests for route config parsing.
2. Add tests for RadioID enrichment helper behavior with cached data.
3. Add smoke script:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
python3 -m py_compile *.py
bash -n run_direct_recorder.sh
bash -n run_direct_poster.sh
make -n
```

**Verification:**

Run:

```bash
pytest -q
bash scripts/smoke_check.sh
```

Expected: pass.

---

### Task 9: Add service/deployment docs

**Objective:** Описать нормальный production запуск, не только `nohup`/manual/background.

**Files:**

- Create: `docs/systemd.md`
- Optionally create: `deploy/systemd/bm-recorder.service`
- Optionally create: `deploy/systemd/bm-poster.service`

**Actions:**

1. Document current bash supervisors.
2. Provide systemd unit examples.
3. Explain env file location, e.g. `/etc/bm-dmr-recorder.env`.
4. Explain logs via `journalctl`.

**Verification:**

Docs include exact commands but no real secrets.

---

### Task 10: Finish autonomous daily summary path

**Objective:** Убрать зависимость daily summary от Hermes cron/LLM, чтобы проект был self-contained.

**Files:**

- Modify: `daily_dmr_summary.py`
- Create/modify: config/env docs.

**Actions:**

1. Add optional Gemini API call controlled by `GEMINI_API_KEY`.
2. Keep fallback mode: stats-only summary if no LLM key.
3. Keep Telegram pin/unpin behavior.
4. Add schedule docs: cron/systemd timer.

**Verification:**

Run dry-run collection:

```bash
python3 daily_dmr_summary.py --hours 24 --dry-run
```

Expected: summary content generated without posting.

---

### Task 11: Security and privacy review before publishing

**Objective:** Финальный fail-closed review перед GitHub.

**Files:**

- Whole repo.

**Actions:**

1. Search for secrets and private values:

```bash
rg --hidden -i '(token|secret|password|passwd|api[_-]?key|chat_id|bot)' .
```

2. Ensure ignored dirs are not tracked.
3. Ensure no recordings/logs/state are committed.
4. Run independent code review using Hermes `requesting-code-review` skill before commit/push.

**Verification:**

Expected:

- no real Telegram token;
- no real hotspot password;
- no accidental logs/recordings/state;
- README contains ethics/legal notice.

---

## Suggested Publish Sequence

1. Create clean branch or clean repo.
2. Add `.gitignore`, examples, README first.
3. Parameterize paths/config.
4. Add pyproject and Makefile.
5. Move legacy code.
6. Add smoke tests.
7. Run security scan.
8. Commit with verified status.
9. Only then create GitHub repo/push.

## Current Verdict

Текущий прототип технически интересный и рабочий, но текущую папку напрямую публиковать не стоит. После этого cleanup-плана проект можно выложить как нормальный self-hosted DMR logging/transcription bot.
