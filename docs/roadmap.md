# DMRLogger Roadmap

This roadmap is intentionally conservative about configuration compatibility. New
features must be additive: users should be able to pull a newer image/release and
continue running their existing `.env` and `configs/bm_direct_routes.json`.

## Configuration compatibility contract

DMRLogger config is split into:

- `.env` — secrets and runtime knobs.
- `configs/bm_direct_routes.json` — monitored routes and routing policy.

Rules for future changes:

1. Existing configs without `config_version` remain valid and are treated as v1.
2. New top-level sections must be optional.
3. New fields must have defaults that preserve old behavior.
4. Unknown top-level fields and unknown route fields must not break recorder/poster startup.
5. Existing Telegram fields stay supported:
   - `telegram.chat_id_env`
   - `telegram.default_chat_id_env`
   - route-level `message_thread_id`
   - route-level `destination.type/chat_id_env/message_thread_id`
6. Provider abstractions must default to today's behavior:
   - publishing: Telegram
   - transcription: local faster-whisper
   - summaries: Gemini if configured, otherwise local stats fallback
7. Deprecations require at least one compatibility release and a documented migration path.

Recommended future marker:

```json
{
  "config_version": 1
}
```

This marker is optional. Absence means v1.

## Planned provider config shape

These sections are reserved for future use. They should be accepted/ignored by
older v1-compatible code and should not be required for existing deployments.

```json
{
  "providers": {
    "transcription": {
      "provider": "faster-whisper",
      "model_env": "WHISPER_MODEL"
    },
    "summary": {
      "provider": "gemini",
      "model_env": "DMR_SUMMARY_MODEL"
    },
    "publishing": {
      "default_provider": "telegram"
    }
  }
}
```

## v0.1 — current baseline

- BrandMeister direct HBP recorder.
- Local AMBE decode via `mbelib`.
- Local transcription via `faster-whisper`.
- Telegram publishing:
  - group topics,
  - one channel/chat per route,
  - one shared chat with hashtags,
  - local-only route mode.
- Daily summary pipeline with Gemini/local fallback.
- Docker Compose services:
  - `recorder`,
  - `poster`,
  - `cleanup`,
  - optional `summary`.
- Watchdogs, heartbeats, healthchecks, retention cleanup.

## v0.2 — summary provider abstraction

Add summary providers without changing existing `summary` behavior.

Planned `.env`:

```env
DMR_SUMMARY_PROVIDER=gemini
DMR_SUMMARY_MODEL=gemini-2.5-flash
OPENROUTER_API_KEY=
OPENAI_API_KEY=
OPENAI_BASE_URL=
```

Planned providers:

- `gemini` — existing/default when `GEMINI_API_KEY` is set.
- `openrouter` — OpenAI-compatible API via OpenRouter.
- `openai-compatible` — custom base URL, useful for OpenAI, vLLM, LiteLLM, etc.
- `ollama` — local HTTP model server.
- `none` — stats-only summary.

Compatibility requirement: if no new provider env/config is set, current Gemini +
stats fallback behavior remains unchanged.

## v0.3 — transcription provider abstraction

Keep local faster-whisper as default. Add optional cloud/API transcription.

Planned `.env`:

```env
DMR_TRANSCRIBE_PROVIDER=faster-whisper
DMR_TRANSCRIBE_MODEL=base
OPENAI_API_KEY=
GROQ_API_KEY=
DEEPGRAM_API_KEY=
ASSEMBLYAI_API_KEY=
```

Planned providers:

- `faster-whisper` — current local default.
- `openai` — OpenAI audio transcription.
- `groq` — fast Whisper-compatible transcription.
- `deepgram` — speech-to-text API.
- `assemblyai` — speech-to-text API.
- `none` — post audio without transcript.

Compatibility requirement: existing `WHISPER_MODEL` and `BM_WHISPER_MODEL` stay
supported. If no provider is set, use faster-whisper exactly as today.

## v0.4 — publishing provider abstraction

Keep Telegram as default. Add additional destinations as optional providers.

Planned destination shapes:

### Telegram — current behavior plus explicit provider

```json
{
  "destination": {
    "provider": "telegram",
    "type": "topic",
    "chat_id_env": "TELEGRAM_CHAT_ID",
    "message_thread_id": 2
  }
}
```

### Discord webhook

```json
{
  "destination": {
    "provider": "discord",
    "webhook_url_env": "DISCORD_WEBHOOK_TG2501"
  }
}
```

### Matrix room

```json
{
  "destination": {
    "provider": "matrix",
    "homeserver_env": "MATRIX_HOMESERVER",
    "access_token_env": "MATRIX_ACCESS_TOKEN",
    "room_id_env": "MATRIX_ROOM_ID"
  }
}
```

### Generic webhook

```json
{
  "destination": {
    "provider": "webhook",
    "url_env": "DMR_WEBHOOK_URL",
    "format": "json"
  }
}
```

Compatibility requirement: destination objects without `provider` are Telegram.
Legacy route-level `message_thread_id` remains supported.

## Future implementation shape

Avoid spreading provider logic across scripts. Introduce small internal modules
when implementing the roadmap:

```text
Transcriber
  transcribe(audio_path, metadata) -> transcript

Summarizer
  summarize(route_payload) -> text

Publisher
  publish_audio(meta, audio_path, transcript_path)
  publish_text(meta, text)
```

The current Telegram/faster-whisper/Gemini logic should become default
implementations behind these interfaces, not a breaking rewrite of config.
