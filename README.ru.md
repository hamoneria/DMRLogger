# DMRLogger

[English README](README.md)

DMRLogger — self-hosted recorder/transcriber для BrandMeister DMR. Он напрямую подключается к BrandMeister master по HomeBrew/MMDVM HBP, записывает настроенные talkgroup/time slot маршруты, декодирует голос, делает расшифровку, обогащает metadata через RadioID.net и публикует записи/сводки в Telegram.

> Статус: экспериментальный, но рабочий проект для технически уверенных операторов.

## Что умеет

- Подключается к BrandMeister HBP master как отдельный listen-only hotspot/peer.
- Записывает настроенные пары `(Talkgroup, Timeslot)`.
- Сохраняет DMRD packets, AMBE payloads, metadata, WAV/MP3 и transcript-файлы.
- Декодирует AMBE через `mbelib` и `dmr_ambe33_to_wav`.
- Делает Telegram-friendly MP3 через `ffmpeg`.
- Расшифровывает речь через `faster-whisper`.
- Кеширует публичные данные RadioID.net.
- Публикует аудио, расшифровки и ежедневные summary в Telegram topics/chats/channels.
- Поддерживает русский и английский язык публичных постов через конфиг.

## Быстрый старт без Docker

```bash
git clone https://github.com/hamoneria/DMRLogger.git
cd DMRLogger
cp .env.example .env
cp configs/bm_direct_routes.example.json configs/bm_direct_routes.json
make
```

Отредактируйте `.env` и `configs/bm_direct_routes.json`. Минимально нужны:

```bash
BM_MASTER=2503.master.brandmeister.network
BM_PORT=62031
BM_RADIO_ID=123456789
BM_HOTSPOT_PASSWORD=change-me
BM_DIRECT_TELEGRAM_BOT_TOKEN=123456:replace-me
TELEGRAM_CHAT_ID=-1000000000000
```

## Язык публичных постов

DMRLogger может публиковать Telegram captions, заголовки расшифровок, Whisper language hints и daily summaries на русском или английском.

Глобально в `configs/bm_direct_routes.json`:

```json
"posting": {
  "enabled": true,
  "language": "ru"
},
"summary": {
  "enabled": true,
  "language": "ru"
}
```

Поддерживаются значения:

- `ru` — русский, значение по умолчанию для обратной совместимости.
- `en` — английский.

Можно переопределить язык на конкретном маршруте:

```json
{
  "tg": 2501,
  "slot": 1,
  "label": "TG2501",
  "language": "en",
  "destination": {"type": "topic", "message_thread_id": 2}
}
```

Также поддерживаются env defaults:

```bash
BM_POST_LANGUAGE=ru
BM_SUMMARY_LANGUAGE=ru
```

`posting.language` управляет отдельными аудио/transcript постами. `summary.language` управляет LLM prompt и fallback daily summary.

## Telegram destination modes

Маршрут задаётся через `tg` + `slot` и может публиковаться в разные layouts:

1. Один Telegram group с forum topic на каждый TG:

```json
"destination": {"type": "topic", "chat_id_env": "TELEGRAM_CHAT_ID", "message_thread_id": 2}
```

2. Один channel/chat на каждый TG:

```json
"destination": {"type": "chat", "chat_id_env": "TELEGRAM_CHAT_ID_TG2501"}
```

3. Один общий chat/channel с hashtags:

```json
"posting": {"enabled": true, "default_mode": "single_chat", "add_hashtags": true, "language": "ru"}
```

4. Только локальная запись без Telegram posting:

```json
"destination": {"type": "none"}
```

## Daily summaries

Summary настраивается верхнеуровневым блоком `summary` и может переопределяться в route-level `summary`:

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

LLM provider выбирается env-переменными; при ошибке provider публичный пост не должен раскрывать техническую ошибку и откатывается к stats-only fallback summary.

## Docker

Docker — рекомендуемый путь для обычного развёртывания. Published image должен включать Python, `uv`, `ffmpeg`, `mbelib`, `dmr_ambe33_to_wav`, Python dependencies и базовую Faster-Whisper модель.

Минимальный runtime directory не обязан быть git checkout: достаточно `.env`, routes config и volume-директорий для recordings/state/logs.

## Основные файлы

- `bm_hbp_recorder.py` — HBP recorder.
- `post_direct_recordings_telegram.py` — decode/transcribe/enrich/post.
- `daily_dmr_summary.py` — сбор статистики и публикация daily summaries.
- `configs/bm_direct_routes.example.json` — пример Telegram group + topics.
- `configs/bm_direct_routes.channels.example.json` — пример channel per TG.
- `configs/bm_direct_routes.single_channel.example.json` — пример один channel + hashtags.
- `.env.example` — пример environment variables.

## Тесты

```bash
pytest tests/ -q
```
