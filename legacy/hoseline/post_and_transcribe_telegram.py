#!/usr/bin/env python3
# /// script
# dependencies = ["faster-whisper>=1.1.0"]
# ///
"""Post new BrandMeister recordings to Telegram, then edit a transcript placeholder."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def tg_api(token: str, method: str, fields: dict[str, Any], files: dict[str, Path] | None = None) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    if not files:
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(url, data=data, method="POST")
    else:
        boundary = "----bm-hose-" + uuid.uuid4().hex
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
    last_exc = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
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
            if attempt == 3:
                break
            time.sleep(2 * (attempt + 1))
        except Exception as exc:
            last_exc = exc
            if attempt == 3:
                break
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Telegram {method} failed after retries: {last_exc}")


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def caption(meta: dict[str, Any]) -> str:
    return "\n".join([
        f"📻 BrandMeister TG {meta.get('talkgroup') or 'unknown'}",
        f"👤 DMR ID: {meta.get('source') or 'unknown'}",
        f"🏷 TA: {(meta.get('alias') or 'No TA').replace(chr(0), '').strip() or 'No TA'}",
        f"⏱ {meta.get('duration_seconds') or '?'} сек",
        f"🕒 UTC: {meta.get('started_at_utc') or ''}",
    ])


def transcribe(path: Path, model_name: str) -> str:
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(path),
        language="ru",
        vad_filter=True,
        beam_size=5,
        condition_on_previous_text=False,
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    if not text:
        return "📝 Расшифровка не получилась: речь не распознана или запись слишком шумная/короткая."
    return f"📝 Расшифровка:\n\n{text}"


def process_one(path: Path, args: argparse.Namespace, state: dict[str, Any], token: str) -> None:
    key = str(path)
    entry = state.setdefault(key, {})
    meta = load_json(path.with_suffix(".json"))
    duration = float(meta.get("duration_seconds") or 0)
    if duration and duration < args.min_duration:
        entry["skipped"] = True
        entry["skip_reason"] = f"short recording: {duration:.2f}s < {args.min_duration:.2f}s"
        save_json(args.state_file, state)
        print(f"[skip] {path} ({entry['skip_reason']})", flush=True)
        return

    if not entry.get("audio_message_id"):
        result = tg_api(token, "sendAudio", {
            "chat_id": args.chat_id,
            "caption": caption(meta),
            "title": f"TG {meta.get('talkgroup') or args.tg} — {meta.get('source') or 'unknown'}",
        }, {"audio": path})
        entry["audio_message_id"] = result["message_id"]
        save_json(args.state_file, state)

    if not entry.get("transcript_message_id"):
        result = tg_api(token, "sendMessage", {
            "chat_id": args.chat_id,
            "text": "📝 Расшифровка готовится...",
        })
        entry["transcript_message_id"] = result["message_id"]
        save_json(args.state_file, state)

    if not entry.get("transcript_done"):
        try:
            text = transcribe(path, args.model)
        except Exception as exc:
            text = f"📝 Расшифровка не получилась: {type(exc).__name__}: {exc}"
        # Telegram text limit is 4096. Keep margin.
        if len(text) > 3900:
            text = text[:3800].rstrip() + "\n\n…расшифровка обрезана из-за лимита Telegram."
        tg_api(token, "editMessageText", {
            "chat_id": args.chat_id,
            "message_id": entry["transcript_message_id"],
            "text": text,
        })
        entry["transcript_done"] = True
        entry["transcript_text"] = text
        save_json(args.state_file, state)


def ignore_existing_backlog(args: argparse.Namespace, state: dict[str, Any]) -> int:
    """Skip all currently present, not-yet-posted MP3s.

    Live/supervised mode: after a crash or downtime, do not deliver a burst of
    old recordings on restart. Only files created after this worker starts will
    be posted.
    """
    skipped = 0
    startup_ts = time.time()
    for path in sorted(args.recordings_dir.glob("*.mp3")):
        if path.stat().st_mtime > startup_ts:
            continue
        key = str(path)
        entry = state.setdefault(key, {})
        if entry.get("audio_message_id") or entry.get("transcript_done") or entry.get("skipped"):
            continue
        entry["skipped"] = True
        entry["skip_reason"] = "pre-start backlog ignored"
        skipped += 1
    if skipped:
        save_json(args.state_file, state)
    return skipped


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--recordings-dir", type=Path, required=True)
    p.add_argument("--state-file", type=Path, required=True)
    p.add_argument("--chat-id", default=os.getenv("TELEGRAM_CHAT_ID", "69713619"))
    p.add_argument("--tg", default="2501")
    p.add_argument("--model", default=os.getenv("BM_WHISPER_MODEL", "base"))
    p.add_argument("--min-duration", type=float, default=3.0, help="Do not post/transcribe recordings shorter than this many seconds")
    p.add_argument("--poll", type=float, default=5.0)
    p.add_argument("--once", action="store_true")
    p.add_argument("--ignore-existing-on-start", action="store_true", help="Skip existing unprocessed MP3s at startup; post only recordings created after this worker starts")
    p.add_argument("--max-per-loop", type=int, default=3)
    return p.parse_args()


def main() -> int:
    load_dotenv(Path.home() / ".hermes" / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN not found in env or ~/.hermes/.env")
    args = parse_args()
    args.recordings_dir.mkdir(parents=True, exist_ok=True)
    if args.ignore_existing_on_start:
        state = load_json(args.state_file)
        skipped = ignore_existing_backlog(args, state)
        print(f"[startup] ignored {skipped} pre-start backlog recording(s)", flush=True)
    while True:
        state = load_json(args.state_file)
        count = 0
        for path in sorted(args.recordings_dir.glob("*.mp3")):
            entry = state.get(str(path), {})
            if entry.get("transcript_done") or entry.get("skipped"):
                continue
            print(f"[post] {path}", flush=True)
            try:
                process_one(path, args, state, token)
                count += 1
            except Exception as exc:
                state = load_json(args.state_file)
                entry = state.setdefault(str(path), {})
                entry["post_error"] = f"{type(exc).__name__}: {exc}"
                entry["post_error_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                entry["skipped"] = True
                entry["skip_reason"] = "post failed; skipped to keep live queue moving"
                save_json(args.state_file, state)
                print(f"[error] {path}: {entry['post_error']}", flush=True)
                continue
            if count >= args.max_per_loop:
                break
        if args.once:
            return 0
        time.sleep(args.poll)


if __name__ == "__main__":
    raise SystemExit(main())
