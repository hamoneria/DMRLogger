#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

MSK = dt.timezone(dt.timedelta(hours=3), "MSK")
APP_DIR = Path(os.getenv("BM_APP_DIR", Path(__file__).resolve().parent))
DEFAULT_RECORDINGS = Path(os.getenv("BM_RECORDINGS_DIR", APP_DIR / "recordings"))
DEFAULT_STATE_DIR = Path(os.getenv("BM_STATE_DIR", APP_DIR / "state"))
DEFAULT_ROUTES = Path(os.getenv("BM_ROUTES_CONFIG", APP_DIR / "configs" / "bm_direct_routes.json"))
DEFAULT_STATE = Path(os.getenv("BM_DAILY_SUMMARY_STATE", DEFAULT_STATE_DIR / "bm_daily_summary_state.json"))
DEFAULT_POSTER_STATE = Path(os.getenv("BM_POSTER_STATE_FILE", DEFAULT_STATE_DIR / "bm_direct_dmrlogs_state.json"))


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
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def config_value(cfg: dict[str, Any], value_key: str, env_key: str) -> str:
    value = cfg.get(value_key)
    if value is not None and str(value).strip():
        return str(value)
    env_name = cfg.get(env_key)
    if env_name is not None and str(env_name).strip():
        return os.getenv(str(env_name), "")
    return ""


def telegram_chat_id(telegram_cfg: dict[str, Any], fallback: str | None) -> str:
    return resolve_telegram_chat_id(telegram_cfg, fallback)


def parse_utc(value: object) -> dt.datetime | None:
    if not value:
        return None
    try:
        out = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if out.tzinfo is None:
            out = out.replace(tzinfo=dt.timezone.utc)
        return out.astimezone(dt.timezone.utc)
    except Exception:
        return None


def parse_routes_config(cfg: dict[str, Any]) -> tuple[dict[str, Any], dict[tuple[int, int], dict[str, Any]], dict[str, Any]]:
    out: dict[tuple[int, int], dict[str, Any]] = {}
    summary_cfg = dict(cfg.get("summary", {}) if isinstance(cfg.get("summary", {}), dict) else {})
    summary_cfg.setdefault("enabled", False)
    summary_cfg.setdefault("mode", "per_route")
    summary_cfg.setdefault("interval", "24h")
    summary_cfg.setdefault("at", "09:00")
    summary_cfg.setdefault("timezone", "Europe/Moscow")
    summary_cfg.setdefault("pin", True)
    summary_cfg.setdefault("unpin_previous", True)
    summary_cfg.setdefault("use_llm", True)
    summary_cfg.setdefault("fallback", True)
    for peer in cfg.get("peers", []):
        for group in peer.get("groups", []):
            route = dict(group)
            out[(int(route["tg"]), int(route["slot"]))] = route
    return cfg.get("telegram", {}), out, summary_cfg


def route_maps(routes_path: Path) -> tuple[dict[str, Any], dict[tuple[int, int], dict[str, Any]]]:
    telegram_cfg, routes, _summary_cfg = parse_routes_config(load_json(routes_path))
    return telegram_cfg, routes


def resolve_telegram_chat_id(telegram_cfg: dict[str, Any], fallback: str | None = None) -> str:
    return (
        config_value(telegram_cfg, "default_chat_id", "default_chat_id_env")
        or config_value(telegram_cfg, "chat_id", "chat_id_env")
        or str(fallback or "")
    )


def resolve_route_destination(route: dict[str, Any], telegram_cfg: dict[str, Any], fallback_chat_id: str | None = None) -> dict[str, Any]:
    dest = route.get("destination") if isinstance(route.get("destination"), dict) else {}
    provider = str(dest.get("provider") or route.get("provider") or "telegram").lower()
    if provider not in {"telegram", ""}:
        return {"enabled": False, "type": "unsupported", "provider": provider, "chat_id": "", "message_thread_id": None}
    dest_type = str(dest.get("type") or ("topic" if route.get("message_thread_id") is not None else "chat")).lower()
    if dest_type in {"none", "disabled", "off"}:
        return {"enabled": False, "type": "none", "provider": provider, "chat_id": "", "message_thread_id": None}
    chat_id = (
        config_value(dest, "chat_id", "chat_id_env")
        or config_value(route, "telegram_chat_id", "telegram_chat_id_env")
        or config_value(route, "chat_id", "chat_id_env")
        or resolve_telegram_chat_id(telegram_cfg, fallback_chat_id)
    )
    thread_id = dest.get("message_thread_id", route.get("message_thread_id")) if dest_type == "topic" else None
    return {"enabled": True, "type": dest_type, "provider": provider, "chat_id": str(chat_id or ""), "message_thread_id": int(thread_id) if thread_id is not None and str(thread_id).strip() else None}


def resolve_route_summary_config(route: dict[str, Any], summary_cfg: dict[str, Any]) -> dict[str, Any]:
    merged = dict(summary_cfg or {})
    if isinstance(route.get("summary"), dict):
        merged.update(route["summary"])
    return merged

def speaker(meta: dict[str, Any]) -> str:
    callsign = str(meta.get("callsign") or "").strip()
    name = str(meta.get("operator_name") or "").strip()
    rid = meta.get("rf_src", "?")
    if callsign and name:
        return f"{callsign} / {name} ({rid})"
    if callsign or name:
        return f"{callsign or name} ({rid})"
    return f"DMR ID {rid}"


def location(meta: dict[str, Any]) -> str:
    city = str(meta.get("operator_city") or "").strip()
    country = str(meta.get("operator_country") or "").strip()
    if city and country:
        return f"{city}, {country}"
    return city or country


def enrich_from_radioid_cache(meta: dict[str, Any], radioid_lookup: dict[str, Any]) -> dict[str, Any]:
    if meta.get("callsign"):
        return meta
    rid = str(meta.get("rf_src") or "").strip()
    cached = radioid_lookup.get(rid) if rid else None
    data = cached.get("data") if isinstance(cached, dict) else None
    if not isinstance(data, dict) or not data:
        return meta
    meta = dict(meta)
    meta["callsign"] = str(data.get("callsign") or "").strip()
    meta["operator_name"] = str(data.get("name") or data.get("fname") or "").strip()
    meta["operator_city"] = str(data.get("city") or "").strip()
    meta["operator_country"] = str(data.get("country") or "").strip()
    return meta


def collect_payload(args: argparse.Namespace) -> dict[str, Any]:
    if not hasattr(args, "max_transcript_chars_per_item"):
        args.max_transcript_chars_per_item = 1200
    if not hasattr(args, "max_items_per_route"):
        args.max_items_per_route = 200
    if not hasattr(args, "max_transcript_chars_per_route"):
        args.max_transcript_chars_per_route = 8000
    _telegram_cfg, routes = route_maps(args.routes_config)
    poster_state = load_json(args.poster_state)
    radioid_lookup = poster_state.get("radioid_lookup", {}) if isinstance(poster_state.get("radioid_lookup", {}), dict) else {}
    now = dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(hours=args.hours)
    by_route: dict[str, dict[str, Any]] = {}
    for (tg, slot), route in routes.items():
        key = f"{tg}:{slot}"
        by_route[key] = {
            "key": key,
            "tg": tg,
            "slot": slot,
            "label": route.get("label") or f"TG{tg}",
            "message_thread_id": route.get("message_thread_id"),
            "count": 0,
            "total_seconds": 0.0,
            "speakers": {},
            "items": [],
        }

    for meta_path in sorted(args.recordings_dir.rglob("*.json")):
        meta = load_json(meta_path)
        meta = enrich_from_radioid_cache(meta, radioid_lookup)
        ts = parse_utc(meta.get("started_at_utc"))
        if not ts or ts < start or ts > now:
            continue
        tg = int(meta.get("talkgroup") or 0)
        slot = int(meta.get("slot") or 0)
        key = f"{tg}:{slot}"
        if key not in by_route:
            continue
        txt_path = meta_path.with_suffix(".txt")
        transcript = ""
        if txt_path.exists():
            transcript = txt_path.read_text(encoding="utf-8", errors="replace").strip()
        if not transcript or transcript.startswith("Расшифровка не получилась"):
            transcript = ""
        dur = float(meta.get("approx_audio_seconds") or meta.get("duration_seconds") or 0.0)
        sp = speaker(meta)
        loc = location(meta)
        route = by_route[key]
        route["count"] += 1
        route["total_seconds"] += dur
        speaker_entry = route["speakers"].setdefault(sp, {"count": 0, "seconds": 0.0, "location": loc})
        speaker_entry["count"] += 1
        speaker_entry["seconds"] += dur
        item = {
            "time_msk": ts.astimezone(MSK).strftime("%Y-%m-%d %H:%M:%S"),
            "speaker": sp,
            "location": loc,
            "duration_seconds": round(dur, 1),
            "transcript": transcript[: args.max_transcript_chars_per_item],
            "meta_path": str(meta_path),
        }
        route["items"].append(item)

    # Keep prompt bounded: oldest-to-newest, but cap total transcript text per route.
    for route in by_route.values():
        used = 0
        kept = []
        for item in route["items"]:
            text = item.get("transcript") or ""
            if used + len(text) > args.max_transcript_chars_per_route:
                item = dict(item)
                remaining = max(0, args.max_transcript_chars_per_route - used)
                item["transcript"] = text[:remaining]
                if remaining <= 0:
                    item["transcript"] = ""
            used += len(item.get("transcript") or "")
            kept.append(item)
        route["items"] = kept
        route["total_seconds"] = round(route["total_seconds"], 1)
        route["speakers"] = dict(sorted(
            route["speakers"].items(),
            key=lambda kv: (-kv[1]["seconds"], kv[0]),
        ))

    payload = {
        "window": {
            "start_utc": start.isoformat(),
            "end_utc": now.isoformat(),
            "start_msk": start.astimezone(MSK).strftime("%Y-%m-%d %H:%M:%S"),
            "end_msk": now.astimezone(MSK).strftime("%Y-%m-%d %H:%M:%S"),
            "hours": args.hours,
        },
        "routes": by_route,
    }
    return payload


def collect(args: argparse.Namespace) -> int:
    print(json.dumps(collect_payload(args), ensure_ascii=False, indent=2))
    return 0


def seconds_human(seconds: float) -> str:
    seconds = float(seconds or 0)
    minutes = int(seconds // 60)
    rest = int(seconds % 60)
    if minutes:
        return f"{minutes}м {rest}с"
    return f"{rest}с"


TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_SAFE_TEXT_LIMIT = 3800
TRUNCATION_NOTICE = "\n\n…обрезано из-за лимита Telegram."


def telegram_safe_text(text: str, limit: int = TELEGRAM_SAFE_TEXT_LIMIT) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    room = max(0, limit - len(TRUNCATION_NOTICE))
    return text[:room].rstrip() + TRUNCATION_NOTICE


def split_telegram_text(text: str, limit: int = TELEGRAM_SAFE_TEXT_LIMIT) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    blocks = text.split("\n\n")
    for block in blocks:
        candidates = [block]
        if len(block) > limit:
            candidates = block.splitlines() or [block]
        for candidate in candidates:
            candidate = candidate.strip()
            if not candidate:
                continue
            sep = "\n\n" if current else ""
            if current and len(current) + len(sep) + len(candidate) <= limit:
                current += sep + candidate
            else:
                if current:
                    chunks.append(current)
                    current = ""
                while len(candidate) > limit:
                    chunks.append(candidate[:limit].rstrip())
                    candidate = candidate[limit:].lstrip()
                current = candidate
    if current:
        chunks.append(current)

    if len(chunks) == 1:
        return chunks
    total = len(chunks)
    labelled: list[str] = []
    for idx, chunk in enumerate(chunks, 1):
        prefix = f"Часть {idx}/{total}\n\n"
        if len(prefix) + len(chunk) <= limit:
            labelled.append(prefix + chunk)
        else:
            labelled.append(prefix + chunk[: limit - len(prefix)].rstrip())
    return labelled


def validate_llm_summary(text: str, route: dict[str, Any], provider: str) -> str:
    text = text.strip()
    count = int(route.get("count") or 0)
    if count and len(text) < 200:
        raise RuntimeError(f"{provider} response too short ({len(text)} chars)")
    return text


def summary_prompt(payload: dict[str, Any], route_key: str) -> tuple[dict[str, Any], str]:
    route = payload.get("routes", {}).get(route_key, {})
    if not route:
        return {}, ""
    prompt = (
        "Ты пишешь краткое ежедневное summary русскоязычного DMR-эфира для Telegram. "
        "Не выдумывай темы: опирайся только на transcript/items. Если данных мало, так и скажи. "
        "Формат: заголовок, 3-6 пунктов что обсуждали, активные корреспонденты, короткая статистика. "
        "Без внутренних путей файлов. Уложись в 3500 символов.\n\n"
        + json.dumps({"window": payload.get("window"), "route": route}, ensure_ascii=False)
    )
    return route, prompt


def fallback_summary(payload: dict[str, Any], route_key: str) -> str:
    route = payload.get("routes", {}).get(route_key, {})
    window = payload.get("window", {})
    label = route.get("label") or route_key
    count = int(route.get("count") or 0)
    total = float(route.get("total_seconds") or 0)
    speakers = route.get("speakers") or {}
    lines = [
        f"📊 {label}: summary за {window.get('hours', '?')} ч",
        f"Период MSK: {window.get('start_msk', '?')} — {window.get('end_msk', '?')}",
        f"Передач: {count}, суммарно: {seconds_human(total)}.",
        "",
        "LLM-summary отключён или недоступен, поэтому опубликована только статистика без фрагментов разговоров.",
    ]
    if speakers:
        lines.append("")
        lines.append("Активные корреспонденты:")
        for name, data in list(speakers.items())[:10]:
            lines.append(f"- {name}: {data.get('count', 0)} передач, {seconds_human(float(data.get('seconds') or 0))}")
    return telegram_safe_text("\n".join(lines))


def gemini_summary(payload: dict[str, Any], route_key: str, model: str) -> str | None:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    route, prompt = summary_prompt(payload, route_key)
    if not route:
        return None
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 4096,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model)}:generateContent?key={api_key}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    try:
        candidate = result["candidates"][0]
        finish_reason = candidate.get("finishReason")
        if finish_reason and finish_reason != "STOP":
            raise RuntimeError(f"Gemini finishReason={finish_reason}")
        text = candidate["content"]["parts"][0]["text"]
        return validate_llm_summary(text, route, "Gemini")
    except Exception as exc:
        raise RuntimeError(f"Unexpected Gemini response: {result}") from exc


def openrouter_summary(payload: dict[str, Any], route_key: str, model: str) -> str | None:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None
    route, prompt = summary_prompt(payload, route_key)
    if not route:
        return None
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Ты аккуратный редактор ежедневных DMR-сводок на русском языке."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 1800,
    }
    data = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://github.com/dmrlogger/dmrlogger",
        "X-Title": "DMRLogger daily summary",
    }
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    try:
        choice = result["choices"][0]
        finish_reason = choice.get("finish_reason")
        if finish_reason and finish_reason not in {"stop", "end_turn"}:
            raise RuntimeError(f"OpenRouter finish_reason={finish_reason}")
        text = choice["message"]["content"]
        return validate_llm_summary(text, route, "OpenRouter")
    except Exception as exc:
        raise RuntimeError(f"Unexpected OpenRouter response: {result}") from exc


def summarize(args: argparse.Namespace) -> int:
    load_dotenv(Path.home() / ".hermes" / ".env")
    load_dotenv(APP_DIR / ".env")
    payload = collect_payload(args)
    routes = payload.get("routes", {})
    keys = [args.route_key] if args.route_key else list(routes.keys())
    summaries: dict[str, str] = {}
    for key in keys:
        if key not in routes:
            continue
        text = None
        provider = str(args.summary_provider or "gemini").lower()
        if not args.no_gemini:
            try:
                if provider == "openrouter":
                    try:
                        text = openrouter_summary(payload, key, args.openrouter_model)
                    except Exception as openrouter_exc:
                        print(
                            f"summary provider failure route={key} provider=openrouter: "
                            f"{type(openrouter_exc).__name__}: {openrouter_exc}",
                            file=sys.stderr,
                        )
                        try:
                            text = gemini_summary(payload, key, args.gemini_model)
                        except Exception as gemini_exc:
                            print(
                                f"summary provider failure route={key} provider=gemini: "
                                f"{type(gemini_exc).__name__}: {gemini_exc}",
                                file=sys.stderr,
                            )
                            text = fallback_summary(payload, key)
                elif provider == "gemini":
                    text = gemini_summary(payload, key, args.gemini_model)
                else:
                    raise RuntimeError(f"unknown summary provider: {provider}")
            except Exception as exc:
                print(
                    f"summary provider failure route={key} provider={provider}: "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                text = fallback_summary(payload, key)
        if not text:
            text = fallback_summary(payload, key)
        summaries[key] = text
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for key, text in summaries.items():
            safe = key.replace(":", "_")
            (args.output_dir / f"summary_{safe}.txt").write_text(text + "\n", encoding="utf-8")
    print(json.dumps({"window": payload.get("window"), "summaries": summaries}, ensure_ascii=False, indent=2))
    return 0


def tg_api(token: str, method: str, fields: dict[str, Any]) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(fields).encode()
    last_exc: object = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, method="POST"), timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if not payload.get("ok"):
                raise RuntimeError(payload)
            return payload.get("result") or {}
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
    raise RuntimeError(f"Telegram {method} failed: {last_exc}")


def post(args: argparse.Namespace) -> int:
    load_dotenv(Path.home() / ".hermes" / ".env")
    load_dotenv(APP_DIR / ".env")
    routes_cfg = load_json(args.routes_config)
    telegram_cfg, routes, summary_cfg = parse_routes_config(routes_cfg)
    token_env = args.bot_token_env or telegram_cfg.get("bot_token_env") or "TELEGRAM_BOT_TOKEN"
    token = os.getenv(str(token_env))
    if not token:
        raise SystemExit(f"{token_env} not found in env or ~/.hermes/.env")
    try:
        tg_s, slot_s = args.route_key.split(":", 1)
        route = routes[(int(tg_s), int(slot_s))]
    except Exception as exc:
        raise SystemExit(f"Unknown route_key={args.route_key}: {exc}")
    route_summary = resolve_route_summary_config(route, summary_cfg)
    if route_summary.get("enabled") is False:
        print(json.dumps({"skipped": True, "reason": "summary disabled", "route_key": args.route_key}, ensure_ascii=False))
        return 0
    dest = resolve_route_destination(route, telegram_cfg, args.chat_id or os.getenv("TELEGRAM_CHAT_ID"))
    if not dest.get("enabled") or not dest.get("chat_id"):
        raise SystemExit("summary destination chat_id not configured")
    chat_id = str(dest["chat_id"])
    thread_id = dest.get("message_thread_id")
    text = args.message_file.read_text(encoding="utf-8", errors="replace").strip()
    parts = split_telegram_text(text)
    if not parts:
        raise SystemExit("summary message is empty")
    state = load_json(args.state_file)
    route_state = state.setdefault(args.route_key, {})
    if args.dry_run:
        print(json.dumps({
            "dry_run": True,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "parts": parts,
            "part_lengths": [len(part) for part in parts],
            "pin_part": 1,
        }, ensure_ascii=False, indent=2))
        return 0

    old_message_id = route_state.get("pinned_message_id")
    if old_message_id and route_summary.get("unpin_previous", True):
        try:
            tg_api(token, "unpinChatMessage", {"chat_id": chat_id, "message_id": int(old_message_id)})
        except Exception as exc:
            route_state["last_unpin_error"] = f"{type(exc).__name__}: {exc}"

    message_ids: list[int] = []
    first_message_id: int | None = None
    for idx, part in enumerate(parts):
        fields: dict[str, Any] = {"chat_id": chat_id, "text": part, "disable_web_page_preview": True}
        if thread_id is not None:
            fields["message_thread_id"] = int(thread_id)
        if first_message_id is not None:
            fields["reply_to_message_id"] = first_message_id
            fields["allow_sending_without_reply"] = True
        result = tg_api(token, "sendMessage", fields)
        message_id = int(result["message_id"])
        if idx == 0:
            first_message_id = message_id
        message_ids.append(message_id)

    pinned_message_id = message_ids[0]
    pin_error = None
    if route_summary.get("pin", True):
        try:
            tg_api(token, "pinChatMessage", {"chat_id": chat_id, "message_id": pinned_message_id, "disable_notification": True})
        except Exception as exc:
            pin_error = f"{type(exc).__name__}: {exc}"

    route_state.update({
        "route_key": args.route_key,
        "label": route.get("label"),
        "chat_id": chat_id,
        "message_thread_id": thread_id,
        "pinned_message_id": pinned_message_id,
        "summary_message_ids": message_ids,
        "summary_part_count": len(parts),
        "posted_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pin_error": pin_error,
    })
    save_json(args.state_file, state)
    print(json.dumps(route_state, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Collect, summarize, and post DMR daily summaries")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect")
    c.add_argument("--routes-config", type=Path, default=DEFAULT_ROUTES)
    c.add_argument("--recordings-dir", type=Path, default=DEFAULT_RECORDINGS)
    c.add_argument("--poster-state", type=Path, default=DEFAULT_POSTER_STATE)
    c.add_argument("--hours", type=int, default=24)
    c.add_argument("--max-transcript-chars-per-item", type=int, default=1200)
    c.add_argument("--max-items-per-route", type=int, default=200)
    c.add_argument("--max-transcript-chars-per-route", type=int, default=8000)
    c.set_defaults(func=lambda args: (print(json.dumps(collect_payload(args), ensure_ascii=False, indent=2)) or 0))

    z = sub.add_parser("summarize")
    z.add_argument("--routes-config", type=Path, default=DEFAULT_ROUTES)
    z.add_argument("--recordings-dir", type=Path, default=DEFAULT_RECORDINGS)
    z.add_argument("--poster-state", type=Path, default=DEFAULT_POSTER_STATE)
    z.add_argument("--hours", type=int, default=24)
    z.add_argument("--max-transcript-chars-per-item", type=int, default=1200)
    z.add_argument("--max-items-per-route", type=int, default=200)
    z.add_argument("--max-transcript-chars-per-route", type=int, default=8000)
    z.add_argument("--route-key", default=None, help="Route key as tg:slot, e.g. 2501:1")
    z.add_argument("--summary-provider", default=os.getenv("BM_SUMMARY_PROVIDER", "gemini"), choices=["gemini", "openrouter"])
    z.add_argument("--gemini-model", default=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
    z.add_argument("--openrouter-model", default=os.getenv("OPENROUTER_MODEL", os.getenv("BM_SUMMARY_MODEL", "openrouter/free")))
    z.add_argument("--output-dir", type=Path, default=None, help="Write summary_<tg>_<slot>.txt files")
    z.add_argument("--no-gemini", action="store_true", help="Force stats-only fallback summary")
    z.set_defaults(func=summarize)

    s = sub.add_parser("post")
    s.add_argument("--routes-config", type=Path, default=DEFAULT_ROUTES)
    s.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    s.add_argument("--route-key", required=True, help="Route key as tg:slot, e.g. 2501:1")
    s.add_argument("--message-file", type=Path, required=True)
    s.add_argument("--chat-id", default=os.getenv("TELEGRAM_CHAT_ID"))
    s.add_argument("--bot-token-env", default=None)
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=post)

    return p.parse_args()


def main() -> int:
    args = parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

