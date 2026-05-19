#!/usr/bin/env python3
# /// script
# dependencies = ["faster-whisper>=1.1.0"]
# ///
from __future__ import annotations

import argparse
import datetime as dt
import json
import mimetypes
import os
import subprocess
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

APP_DIR = Path(os.getenv("BM_APP_DIR", Path(__file__).resolve().parent))
DEFAULT_RECORDINGS_DIR = Path(os.getenv("BM_RECORDINGS_DIR", APP_DIR / "recordings"))
DEFAULT_STATE_DIR = Path(os.getenv("BM_STATE_DIR", APP_DIR / "state"))
DEFAULT_ROUTES_CONFIG = Path(os.getenv("BM_ROUTES_CONFIG", APP_DIR / "configs" / "bm_direct_routes.json"))
DEFAULT_DECODER = Path(os.getenv("BM_AMBE_DECODER", APP_DIR / "dmr_ambe33_to_wav"))


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_heartbeat(path: Path | None, component: str, **fields: Any) -> None:
    if not path:
        return
    payload = {"component": component, "time": time.time(), "time_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    payload.update(fields)
    save_json(path, payload)


def normalize_language(value: object, default: str = "ru") -> str:
    lang = str(value or default or "ru").strip().lower()
    if lang in {"en", "eng", "english"}:
        return "en"
    return "ru"


def route_language(route: dict[str, Any], posting_cfg: dict[str, Any] | None = None) -> str:
    if not isinstance(posting_cfg, dict):
        posting_cfg = route.get("_posting_cfg") if isinstance(route.get("_posting_cfg"), dict) else {}
    return normalize_language(
        route.get("language")
        or route.get("post_language")
        or posting_cfg.get("language")
        or posting_cfg.get("post_language")
        or os.getenv("BM_POST_LANGUAGE")
        or "ru"
    )


def parse_routes_config(cfg: dict[str, Any]) -> tuple[dict[str, Any], dict[tuple[int, int], dict[str, Any]]]:
    route_map: dict[tuple[int, int], dict[str, Any]] = {}
    posting_cfg = cfg.get("posting", {}) if isinstance(cfg.get("posting", {}), dict) else {}
    posting_cfg.setdefault("language", os.getenv("BM_POST_LANGUAGE", "ru"))
    for peer in cfg.get("peers", []):
        for group in peer.get("groups", []):
            route = dict(group)
            route.setdefault("posting", {})
            route["_posting_cfg"] = dict(posting_cfg)
            route.setdefault("language", route_language(route, posting_cfg))
            route_map[(int(route["tg"]), int(route["slot"]))] = route
    return cfg.get("telegram", {}), route_map


def load_routes(path: Path | None) -> tuple[dict[str, Any], dict[tuple[int, int], dict[str, Any]]]:
    if not path:
        return {}, {}
    return parse_routes_config(load_json(path))


def resolve_telegram_chat_id(telegram_cfg: dict[str, Any], fallback: str | None = None) -> str:
    return (
        config_value(telegram_cfg, "default_chat_id", "default_chat_id_env")
        or config_value(telegram_cfg, "chat_id", "chat_id_env")
        or str(fallback or "")
    )


def resolve_route_destination(
    route: dict[str, Any],
    telegram_cfg: dict[str, Any],
    posting_cfg: dict[str, Any] | None = None,
    fallback_chat_id: str | None = None,
) -> dict[str, Any]:
    posting_cfg = posting_cfg or route.get("_posting_cfg") or {}
    if posting_cfg.get("enabled") is False:
        return {"enabled": False, "type": "none", "chat_id": "", "message_thread_id": None, "add_hashtags": False, "hashtag": ""}
    dest = route.get("destination") if isinstance(route.get("destination"), dict) else {}
    provider = str(dest.get("provider") or route.get("provider") or "telegram").lower()
    if provider not in {"telegram", ""}:
        return {"enabled": False, "type": "unsupported", "provider": provider, "chat_id": "", "message_thread_id": None, "add_hashtags": False, "hashtag": ""}
    dest_type = str(dest.get("type") or ("topic" if route.get("message_thread_id") is not None else "chat")).lower()
    if dest_type in {"none", "disabled", "off"}:
        return {"enabled": False, "type": "none", "provider": provider, "chat_id": "", "message_thread_id": None, "add_hashtags": False, "hashtag": ""}
    chat_id = (
        config_value(dest, "chat_id", "chat_id_env")
        or config_value(route, "telegram_chat_id", "telegram_chat_id_env")
        or config_value(route, "chat_id", "chat_id_env")
        or resolve_telegram_chat_id(telegram_cfg, fallback_chat_id)
    )
    thread_id = dest.get("message_thread_id", route.get("message_thread_id")) if dest_type == "topic" else None
    add_hashtags = bool(route.get("add_hashtags", posting_cfg.get("add_hashtags", False)))
    hashtag = str(route.get("hashtag") or dest.get("hashtag") or "").strip()
    if add_hashtags and not hashtag and route.get("tg") is not None:
        hashtag = f"#TG{route['tg']}"
    return {
        "enabled": True,
        "type": dest_type,
        "provider": provider,
        "chat_id": str(chat_id or ""),
        "message_thread_id": int(thread_id) if thread_id is not None and str(thread_id).strip() else None,
        "add_hashtags": add_hashtags,
        "hashtag": hashtag,
    }

def route_for(meta: dict[str, Any], route_map: dict[tuple[int, int], dict[str, Any]]) -> dict[str, Any]:
    if not route_map:
        return {}
    return route_map.get((int(meta.get("talkgroup", 0)), int(meta.get("slot", 0))), {})


def add_thread(fields: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
    thread_id = route.get("message_thread_id")
    if thread_id is not None:
        fields["message_thread_id"] = int(thread_id)
    return fields

def add_destination_thread(fields: dict[str, Any], dest: dict[str, Any]) -> dict[str, Any]:
    thread_id = dest.get("message_thread_id")
    if thread_id is not None:
        fields["message_thread_id"] = int(thread_id)
    return fields


def config_value(cfg: dict[str, Any], value_key: str, env_key: str) -> str:
    value = cfg.get(value_key)
    if value is not None and str(value).strip():
        return str(value)
    env_name = cfg.get(env_key)
    if env_name is not None and str(env_name).strip():
        return os.getenv(str(env_name), "")
    return ""


def bool_value(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled", ""}


def should_send_transcript(route: dict[str, Any], posting_cfg: dict[str, Any] | None, transcribe_enabled: bool) -> bool:
    if not transcribe_enabled:
        return False
    cfg = posting_cfg if isinstance(posting_cfg, dict) else route.get("_posting_cfg")
    if not isinstance(cfg, dict):
        cfg = {}
    raw_route_posting = route.get("posting")
    route_posting: dict[str, Any] = raw_route_posting if isinstance(raw_route_posting, dict) else {}
    sources: list[dict[str, Any]] = [route_posting, route, cfg]
    for source in sources:
        if "send_transcript" in source:
            return bool_value(source.get("send_transcript"), True)
        if "post_transcript" in source:
            return bool_value(source.get("post_transcript"), True)
    return True


def telegram_chat_id(telegram_cfg: dict[str, Any], fallback: str | None) -> str:
    return resolve_telegram_chat_id(telegram_cfg, fallback)


def route_chat_id(route: dict[str, Any], fallback: str | None) -> str:
    return (
        config_value(route, "telegram_chat_id", "telegram_chat_id_env")
        or config_value(route, "chat_id", "chat_id_env")
        or str(fallback or "")
    )


def tg_api(token: str, method: str, fields: dict[str, Any], files: dict[str, Path] | None = None) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    if not files:
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(url, data=data, method="POST")
    else:
        boundary = "----bm-direct-" + uuid.uuid4().hex
        body = bytearray()
        for name, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")
        for name, path in files.items():
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'.encode())
            body.extend(f"Content-Type: {ctype}\r\n\r\n".encode())
            body.extend(path.read_bytes())
            body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(
            url,
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
    last_exc: object = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if not payload.get("ok"):
                raise RuntimeError(f"Telegram {method} failed: {payload}")
            return payload["result"]
        except urllib.error.HTTPError as exc:
            try:
                details = exc.read().decode("utf-8", errors="replace")
            except Exception:
                details = ""
            last_exc = f"HTTP {exc.code} {exc.reason}: {details}"
        except Exception as exc:
            last_exc = exc
        if attempt < 3:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Telegram {method} failed after retries: {last_exc}")


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def lookup_radioid(radio_id: object, cache: dict[str, Any]) -> dict[str, Any]:
    rid = str(radio_id or "").strip()
    if not rid or rid == "?":
        return {}
    lookup_cache = cache.setdefault("radioid_lookup", {})
    cached = lookup_cache.get(rid)
    now = time.time()
    if isinstance(cached, dict) and now - float(cached.get("cached_at", 0)) < 7 * 24 * 3600:
        return dict(cached.get("data") or {})
    url = "https://radioid.net/api/dmr/user/?" + urllib.parse.urlencode({"id": rid})
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        results = payload.get("results") or []
        data = dict(results[0]) if results else {}
        lookup_cache[rid] = {"cached_at": now, "data": data}
        return data
    except Exception as exc:
        lookup_cache[rid] = {"cached_at": now, "data": {}, "error": f"{type(exc).__name__}: {exc}"}
        return {}


def enrich_radioid(meta: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any]:
    if meta.get("callsign"):
        return meta
    data = lookup_radioid(meta.get("rf_src"), cache)
    if not data:
        return meta
    meta["callsign"] = str(data.get("callsign") or "").strip()
    meta["operator_name"] = str(data.get("name") or data.get("fname") or "").strip()
    meta["operator_city"] = str(data.get("city") or "").strip()
    meta["operator_country"] = str(data.get("country") or "").strip()
    return meta


def operator_line(meta: dict[str, Any]) -> str:
    callsign = str(meta.get("callsign") or "").strip()
    name = str(meta.get("operator_name") or "").strip()
    if callsign and name:
        return f"{callsign} / {name}"
    return callsign or name or f"DMR ID: {meta.get('rf_src', '?')}"


def location_line(meta: dict[str, Any]) -> str:
    city = str(meta.get("operator_city") or "").strip()
    country = str(meta.get("operator_country") or "").strip()
    if city and country:
        return f"{city}, {country}"
    return city or country


def transcribe(path: Path, model_name: str, language: str = "ru") -> str:
    from faster_whisper import WhisperModel
    lang = normalize_language(language)
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(
        str(path),
        language=lang,
        vad_filter=True,
        beam_size=5,
        condition_on_previous_text=False,
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    if text:
        return text
    if lang == "en":
        return "Transcription failed: speech was not recognized or the recording is too noisy/short."
    return "Расшифровка не получилась: речь не распознана или запись слишком шумная/короткая."


def ensure_media(meta_path: Path, args: argparse.Namespace) -> tuple[dict[str, Any], Path, Path]:
    meta = load_json(meta_path)
    base = meta_path.with_suffix("")
    ambe = Path(meta.get("ambe33_path") or base.with_suffix(".ambe33"))
    wav = base.with_suffix(".wav")
    mp3 = base.with_suffix(".mp3")
    txt = base.with_suffix(".txt")
    if not ambe.exists():
        raise FileNotFoundError(ambe)
    if not wav.exists() or wav.stat().st_mtime < ambe.stat().st_mtime:
        run([str(args.decoder), str(ambe), str(wav)])
    if not mp3.exists() or mp3.stat().st_mtime < wav.stat().st_mtime:
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav), "-ar", "16000", "-ac", "1", "-codec:a", "libmp3lame", "-b:a", "48k", str(mp3)])
    if args.transcribe and (not txt.exists() or txt.stat().st_mtime < mp3.stat().st_mtime):
        txt.write_text(transcribe(mp3, args.model, getattr(args, "language", "ru")) + "\n", encoding="utf-8")
    meta["wav_path"] = str(wav)
    meta["mp3_path"] = str(mp3)
    if txt.exists():
        meta["transcript_path"] = str(txt)
    save_json(meta_path, meta)
    return meta, mp3, txt


def format_msk_time(value: object) -> str:
    if not value:
        return "?"
    try:
        s = str(value)
        parsed = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        msk = parsed.astimezone(dt.timezone(dt.timedelta(hours=3), "MSK"))
        return msk.strftime("%Y-%m-%d, %H:%M:%S")
    except Exception:
        return str(value)


def build_caption(meta: dict[str, Any], route: dict[str, Any] | None = None) -> str:
    route = route or {}
    lang = route_language(route)
    dur = meta.get("approx_audio_seconds") or meta.get("duration_seconds") or "?"
    if isinstance(dur, (int, float)):
        dur = f"{float(dur):.1f}"
    label = route.get("label") or meta.get("label") or f"TG{meta.get('talkgroup', '?')}"
    time_label = "MSK"
    duration_unit = "sec" if lang == "en" else "сек"
    lines = [
        f"{label}",
        f"👤 {operator_line(meta)}",
    ]
    loc = location_line(meta)
    if loc:
        lines.append(f"📍 {loc}")
    lines.extend([
        f"🆔 DMR ID: {meta.get('rf_src', '?')}",
        f"⏱ {dur} {duration_unit}",
        f"🕒 {time_label}: {format_msk_time(meta.get('started_at_utc'))}",
    ])
    hashtag = str(route.get("hashtag") or "").strip()
    if route.get("add_hashtags") and hashtag and hashtag not in lines:
        lines.append(hashtag)
    return "\n".join(lines)


def caption(meta: dict[str, Any], route: dict[str, Any] | None = None) -> str:
    return build_caption(meta, route)


def transcript_message(txt: Path, language: str = "ru") -> str:
    lang = normalize_language(language)
    if txt.exists():
        text = txt.read_text(encoding="utf-8", errors="replace").strip()
    else:
        text = "Transcript was not generated." if lang == "en" else "Расшифровка не создавалась."
    header = "📝 Transcript:\n\n" if lang == "en" else "📝 Расшифровка:\n\n"
    out = header + text
    if len(out) > 3900:
        notice = "\n\n…truncated due to Telegram limit." if lang == "en" else "\n\n…обрезано из-за лимита Telegram."
        out = out[:3800].rstrip() + notice
    return out


def ignore_existing(args: argparse.Namespace, state: dict[str, Any]) -> int:
    skipped = 0
    startup = time.time()
    for meta_path in sorted(args.recordings_dir.rglob("*.json")):
        if meta_path.stat().st_mtime > startup:
            continue
        key = str(meta_path)
        entry = state.setdefault(key, {})
        if entry.get("audio_message_id") or entry.get("skipped"):
            continue
        entry["skipped"] = True
        entry["skip_reason"] = "pre-start backlog ignored"
        skipped += 1
    if skipped:
        save_json(args.state_file, state)
    return skipped


def process_one(meta_path: Path, args: argparse.Namespace, state: dict[str, Any], token: str, route_map: dict[tuple[int, int], dict[str, Any]], telegram_cfg: dict[str, Any] | None = None, posting_cfg: dict[str, Any] | None = None) -> bool:
    key = str(meta_path)
    entry = state.setdefault(key, {})
    if entry.get("skipped"):
        return False
    meta0 = load_json(meta_path)
    route0 = route_for(meta0, route_map)
    if route_map and not route0:
        return False
    post_language = route_language(route0, posting_cfg or route0.get("_posting_cfg") or {})
    setattr(args, "language", post_language)
    send_transcript = should_send_transcript(route0, posting_cfg or route0.get("_posting_cfg"), bool(args.transcribe))
    if entry.get("audio_message_id") and (entry.get("transcript_message_id") or not send_transcript):
        return False
    dur = float(meta0.get("approx_audio_seconds") or meta0.get("duration_seconds") or 0)
    if dur and dur < args.min_duration:
        entry["skipped"] = True
        entry["skip_reason"] = f"short recording: {dur:.2f}s < {args.min_duration:.2f}s"
        save_json(args.state_file, state)
        print(f"[skip] {meta_path} {entry['skip_reason']}", flush=True)
        return True
    meta, mp3, txt = ensure_media(meta_path, args)
    meta = enrich_radioid(meta, state)
    save_json(meta_path, meta)
    save_json(args.state_file, state)
    route = route_for(meta, route_map)
    post_language = route_language(route, posting_cfg or route.get("_posting_cfg") or {})
    send_transcript = should_send_transcript(route, posting_cfg or route.get("_posting_cfg"), bool(args.transcribe))
    dest = resolve_route_destination(route, telegram_cfg or {}, posting_cfg or route.get("_posting_cfg") or {}, args.chat_id)
    if not dest.get("enabled"):
        entry["skipped"] = True
        entry["skip_reason"] = "posting disabled for route"
        save_json(args.state_file, state)
        return True
    chat_id = dest.get("chat_id") or ""
    if not chat_id:
        raise RuntimeError("Telegram chat_id not configured; set --chat-id, telegram.default_chat_id_env, destination.chat_id_env, or route chat_id")
    route_for_caption = dict(route)
    route_for_caption.update({"hashtag": dest.get("hashtag"), "add_hashtags": dest.get("add_hashtags")})
    if not entry.get("audio_message_id"):
        dur = meta.get("approx_audio_seconds") or meta.get("duration_seconds") or "?"
        if isinstance(dur, (int, float)):
            dur = f"{float(dur):.1f}s"
        label = route.get("label") or meta.get("label") or f"TG{meta.get('talkgroup', args.tg)}"
        op = operator_line(meta)
        fields = add_thread({
            "chat_id": chat_id,
            "caption": build_caption(meta, route_for_caption),
            "performer": op,
            "title": f"{label} · {dur}",
        }, dest)
        result = tg_api(token, "sendAudio", fields, {"audio": mp3})
        entry["audio_message_id"] = result["message_id"]
        entry["chat_id"] = str(chat_id)
        if dest.get("message_thread_id") is not None:
            entry["message_thread_id"] = int(dest["message_thread_id"])
        entry["talkgroup"] = meta.get("talkgroup")
        entry["slot"] = meta.get("slot")
        entry["label"] = label
        entry["callsign"] = meta.get("callsign")
        entry["operator_name"] = meta.get("operator_name")
        save_json(args.state_file, state)
        print(f"[sent-audio] {mp3} chat_id={chat_id} thread={dest.get('message_thread_id')}", flush=True)
    if send_transcript and not entry.get("transcript_message_id"):
        fields = add_thread({
            "chat_id": chat_id,
            "text": transcript_message(txt, post_language),
        }, dest)
        result = tg_api(token, "sendMessage", fields)
        entry["transcript_message_id"] = result["message_id"]
        if dest.get("message_thread_id") is not None:
            entry["transcript_message_thread_id"] = int(dest["message_thread_id"])
        save_json(args.state_file, state)
        print(f"[sent-text] {txt} chat_id={chat_id} thread={dest.get('message_thread_id')}", flush=True)
    return True


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Decode and post direct BrandMeister HBP recordings to Telegram")
    p.add_argument("--recordings-dir", type=Path, default=DEFAULT_RECORDINGS_DIR)
    p.add_argument("--state-file", type=Path, default=DEFAULT_STATE_DIR / "bm_direct_dmrlogs_state.json")
    p.add_argument("--decoder", type=Path, default=DEFAULT_DECODER)
    p.add_argument("--chat-id", default=os.getenv("TELEGRAM_CHAT_ID"))
    p.add_argument("--routes-config", type=Path, default=DEFAULT_ROUTES_CONFIG, help="JSON routes config; supplies chat_id/message_thread_id per (tg, slot)")
    p.add_argument("--bot-token-env", default=None, help="Env var containing Telegram bot token; defaults to routes telegram.bot_token_env or TELEGRAM_BOT_TOKEN")
    p.add_argument("--tg", default="2501")
    p.add_argument("--model", default=os.getenv("BM_WHISPER_MODEL") or os.getenv("WHISPER_MODEL", "base"))
    p.add_argument("--min-duration", type=float, default=3.0)
    p.add_argument("--poll", type=float, default=5.0)
    p.add_argument("--max-per-loop", type=int, default=3)
    p.add_argument("--once", action="store_true")
    p.add_argument("--transcribe", action="store_true", default=True)
    p.add_argument("--no-transcribe", dest="transcribe", action="store_false")
    p.add_argument("--ignore-existing-on-start", action="store_true")
    p.add_argument("--heartbeat-file", type=Path, default=None, help="Write liveness heartbeat JSON for supervisors/healthchecks")
    return p.parse_args()


def main() -> int:
    load_dotenv(Path.home() / ".hermes" / ".env")
    load_dotenv(APP_DIR / ".env")
    args = parse_args()
    routes_cfg = load_json(args.routes_config) if args.routes_config else {}
    telegram_cfg, route_map = parse_routes_config(routes_cfg)
    posting_cfg = routes_cfg.get("posting", {}) if isinstance(routes_cfg.get("posting", {}), dict) else {}
    args.chat_id = telegram_chat_id(telegram_cfg, args.chat_id)
    token_env = args.bot_token_env or telegram_cfg.get("bot_token_env") or "TELEGRAM_BOT_TOKEN"
    token = os.getenv(str(token_env))
    if not token:
        raise SystemExit(f"{token_env} not found in env or ~/.hermes/.env")
    args.recordings_dir.mkdir(parents=True, exist_ok=True)
    print(
        f"[startup] poster chat_id={args.chat_id} routes={len(route_map)} transcribe={args.transcribe} "
        f"state_file={args.state_file}",
        flush=True,
    )
    state = load_json(args.state_file)
    write_heartbeat(args.heartbeat_file, "poster", state_file=str(args.state_file), recordings_dir=str(args.recordings_dir), routes=len(route_map), startup=True)
    if args.ignore_existing_on_start:
        skipped = ignore_existing(args, state)
        print(f"[startup] ignored {skipped} pre-start backlog recording(s)", flush=True)
    loop_count = 0
    while True:
        loop_count += 1
        state = load_json(args.state_file)
        processed = 0
        errors = 0
        for meta_path in sorted(args.recordings_dir.rglob("*.json")):
            if processed >= args.max_per_loop:
                break
            try:
                if process_one(meta_path, args, state, token, route_map, telegram_cfg, posting_cfg):
                    processed += 1
            except Exception as exc:
                errors += 1
                key = str(meta_path)
                entry = state.setdefault(key, {})
                entry["last_error"] = f"{type(exc).__name__}: {exc}"
                save_json(args.state_file, state)
                print(f"[error] {meta_path}: {entry['last_error']}", flush=True)
        write_heartbeat(args.heartbeat_file, "poster", state_file=str(args.state_file), recordings_dir=str(args.recordings_dir), loop_count=loop_count, processed=processed, errors=errors)
        if args.once:
            break
        time.sleep(args.poll)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
