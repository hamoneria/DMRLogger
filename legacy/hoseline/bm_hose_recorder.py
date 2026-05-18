#!/usr/bin/env python3
# /// script
# dependencies = ["websockets>=15.0", "msgpack>=1.1"]
# ///
"""
BrandMeister Hoseline recorder.

Connects to wss://hose.brandmeister.network/spotter/, subscribes to one or
more talkgroups, records each completed transmission as 8 kHz mono WAV, and
optionally converts to OGG/Opus + sends it to Telegram via Bot API.

Usage with uv:
  uv run bm_hose_recorder.py --tg 91 --max-recordings 3
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.request
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import msgpack
import websockets

SPOTTER_URL = "wss://hose.brandmeister.network/spotter/"
TOKEN = "test:test"

TYPE_GROUP_JOIN = 1
TYPE_GROUP_LEAVE = 2
TYPE_GROUP_RESET = 3
TYPE_CALL_START = 11
TYPE_CALL_DROP = 12
TYPE_CALL_END = 13
TYPE_CALL_AUDIO = 20
TYPE_CALL_ALIAS = 21
TYPE_CALL_METER = 22
TYPE_SYSTEM_RESCUE = 80

SAMPLE_RATE = 8000

# Same G.711 μ-law decode table logic as Hoseline JS: Sy(byte).
ULAW_TABLE = []
for i in range(256):
    e = (~i) & 0xFF
    sign = e & 0x80
    exponent = (e >> 4) & 0x07
    mantissa = e & 0x0F
    value = [0, 132, 396, 924, 1980, 4092, 8316, 16764][exponent] + (mantissa << (exponent + 3))
    if sign:
        value = -value
    # Clamp just in case.
    value = max(-32768, min(32767, value))
    ULAW_TABLE.append(value)


def ulaw_to_pcm16(data: bytes, gain: float = 1.0) -> bytes:
    """Convert 8-bit μ-law bytes from Hoseline to little-endian signed PCM16."""
    out = bytearray(len(data) * 2)
    pos = 0
    for b in data:
        v = int(ULAW_TABLE[b] * gain)
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        struct.pack_into("<h", out, pos, v)
        pos += 2
    return bytes(out)


def safe_part(value: object, default: str = "unknown") -> str:
    s = str(value if value not in (None, "") else default)
    s = re.sub(r"[^A-Za-z0-9_.@+-]+", "_", s).strip("_")
    return s[:80] or default


@dataclass
class Recording:
    tg: Optional[int] = None
    source: Optional[int] = None
    alias: str = ""
    started_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    last_audio_at: float = field(default_factory=time.time)
    pcm_chunks: list[bytes] = field(default_factory=list)
    ulaw_bytes: int = 0
    vu_peak: Optional[float] = None
    uuid: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def duration(self) -> float:
        return self.ulaw_bytes / SAMPLE_RATE

    def add_audio(self, data: bytes, gain: float = 1.0) -> None:
        self.pcm_chunks.append(ulaw_to_pcm16(data, gain=gain))
        self.ulaw_bytes += len(data)
        self.last_audio_at = time.time()

    def filename_base(self) -> str:
        stamp = self.started_at.strftime("%Y%m%d_%H%M%S")
        return f"{stamp}_tg{safe_part(self.tg)}_src{safe_part(self.source)}_{self.uuid}"


def write_wav(rec: Recording, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    wav_path = out_dir / f"{rec.filename_base()}.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        for chunk in rec.pcm_chunks:
            wf.writeframes(chunk)
    meta = {
        "talkgroup": rec.tg,
        "source": rec.source,
        "alias": rec.alias,
        "started_at_utc": rec.started_at.isoformat(),
        "duration_seconds": round(rec.duration, 3),
        "sample_rate": SAMPLE_RATE,
        "codec_on_wire": "G.711 mu-law 8 kHz from BrandMeister Hoseline spotter websocket",
        "wav": str(wav_path),
        "vu_peak_dbm": rec.vu_peak,
    }
    wav_path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return wav_path


def convert_to_ogg(wav_path: Path) -> Optional[Path]:
    if not shutil.which("ffmpeg"):
        return None
    ogg_path = wav_path.with_suffix(".ogg")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(wav_path),
        "-c:a", "libopus", "-b:a", "24k", "-ar", "48000", "-ac", "1",
        str(ogg_path),
    ]
    subprocess.run(cmd, check=True)
    return ogg_path


def convert_to_mp3(wav_path: Path) -> Optional[Path]:
    if not shutil.which("ffmpeg"):
        return None
    mp3_path = wav_path.with_suffix(".mp3")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(wav_path),
        "-c:a", "libmp3lame", "-b:a", "64k", "-ar", "48000", "-ac", "1",
        str(mp3_path),
    ]
    subprocess.run(cmd, check=True)
    return mp3_path


def telegram_send_file(bot_token: str, chat_id: str, file_path: Path, caption: str, as_voice: bool = True) -> None:
    endpoint = "sendVoice" if as_voice and file_path.suffix.lower() == ".ogg" else "sendDocument"
    field_name = "voice" if endpoint == "sendVoice" else "document"
    url = f"https://api.telegram.org/bot{bot_token}/{endpoint}"

    boundary = "----bm-hose-" + uuid.uuid4().hex
    body = bytearray()

    def add_field(name: str, value: str) -> None:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(str(value).encode())
        body.extend(b"\r\n")

    add_field("chat_id", chat_id)
    add_field("caption", caption)
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="{field_name}"; filename="{file_path.name}"\r\n'.encode()
    )
    body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
    body.extend(file_path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error: {payload}")


class Recorder:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.current: Optional[Recording] = None
        self.saved = 0

    async def run(self) -> None:
        uri = f"{SPOTTER_URL}?token={TOKEN}"
        while self.saved < self.args.max_recordings:
            try:
                await self._session(uri)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[warn] connection/session failed: {exc}", file=sys.stderr)
                if self.current and self.current.duration >= self.args.min_duration:
                    self.finish_current("connection_lost")
                if self.saved >= self.args.max_recordings:
                    break
                print(f"[info] reconnecting in {self.args.reconnect_delay}s", file=sys.stderr)
                await asyncio.sleep(self.args.reconnect_delay)

    async def _session(self, uri: str) -> None:
        print(f"[info] connecting {SPOTTER_URL}")
        async with websockets.connect(
            uri,
            subprotocols=["spotter"],
            max_size=None,
            ping_interval=None,
            close_timeout=5,
        ) as ws:
            await ws.send(msgpack.packb([TYPE_GROUP_JOIN, self.args.tg], use_bin_type=True))
            print(f"[info] subscribed TG(s): {','.join(map(str, self.args.tg))}")
            last_rx = time.time()
            while self.saved < self.args.max_recordings:
                timeout = max(0.1, self.args.idle_timeout)
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    if self.current and time.time() - self.current.last_audio_at > self.args.end_gap:
                        self.finish_current("audio_gap")
                    elif time.time() - last_rx > self.args.overall_timeout:
                        print("[info] no websocket messages; stopping")
                        return
                    continue
                last_rx = time.time()
                self.handle_message(msg)

    def handle_message(self, msg: bytes | str) -> None:
        if isinstance(msg, str):
            print(f"[debug] text message: {msg[:200]}")
            return
        obj = msgpack.unpackb(msg, raw=False)
        if not isinstance(obj, list) or not obj:
            print(f"[debug] unexpected message: {obj!r}")
            return
        typ = obj[0]
        if typ == TYPE_CALL_START:
            if self.current and self.current.duration >= self.args.min_duration:
                self.finish_current("new_call")
            # Observed format: [11, 0, source_id, destination_tg, 0]
            source = obj[2] if len(obj) > 2 else None
            tg = obj[3] if len(obj) > 3 else None
            self.current = Recording(tg=tg, source=source)
            print(f"[call] start TG {tg} source {source}")
        elif typ == TYPE_CALL_AUDIO:
            if not self.current:
                self.current = Recording(tg=self.args.tg[0] if len(self.args.tg) == 1 else None)
                print("[call] implicit start from audio")
            data = obj[1]
            if isinstance(data, bytearray):
                data = bytes(data)
            if not isinstance(data, bytes):
                print(f"[debug] non-bytes audio: {type(data)}")
                return
            self.current.add_audio(data, gain=self.args.gain)
        elif typ == TYPE_CALL_ALIAS:
            alias = obj[1] if len(obj) > 1 else ""
            if self.current:
                self.current.alias = str(alias).replace("\x00", "").strip()
            print(f"[call] alias {alias!r}")
        elif typ == TYPE_CALL_METER:
            meter = float(obj[1]) if len(obj) > 1 else None
            if self.current and meter is not None:
                self.current.vu_peak = meter if self.current.vu_peak is None else max(self.current.vu_peak, meter)
        elif typ == TYPE_CALL_END:
            self.finish_current("call_end")
        elif typ == TYPE_SYSTEM_RESCUE:
            print("[info] system rescue event")
        else:
            print(f"[debug] event {obj!r}")

    def finish_current(self, reason: str) -> None:
        rec = self.current
        self.current = None
        if not rec:
            return
        if rec.duration < self.args.min_duration:
            print(f"[call] drop short recording {rec.duration:.2f}s ({reason})")
            return
        wav_path = write_wav(rec, self.args.out_dir)
        send_path = wav_path
        if self.args.ogg:
            ogg = convert_to_ogg(wav_path)
            if ogg:
                send_path = ogg
        if self.args.mp3:
            mp3 = convert_to_mp3(wav_path)
            if mp3:
                send_path = mp3
        self.saved += 1
        caption = (
            f"BrandMeister TG {rec.tg} / source {rec.source}\n"
            f"{rec.alias or 'No TA'}\n"
            f"Duration: {rec.duration:.1f}s\n"
            f"UTC: {rec.started_at:%Y-%m-%d %H:%M:%S}"
        )
        print(f"[call] saved {wav_path} ({rec.duration:.1f}s, {reason})")
        if send_path != wav_path:
            print(f"[call] converted {send_path}")
        if self.args.telegram_bot_token and self.args.telegram_chat_id:
            telegram_send_file(
                self.args.telegram_bot_token,
                self.args.telegram_chat_id,
                send_path,
                caption,
                as_voice=self.args.telegram_voice,
            )
            print("[telegram] sent")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Record BrandMeister Hoseline talkgroup transmissions")
    p.add_argument("--tg", type=int, action="append", required=True, help="Talkgroup to subscribe. Can be repeated.")
    p.add_argument("--out-dir", type=Path, default=Path.home() / "brandmeister-recordings")
    p.add_argument("--max-recordings", type=int, default=3)
    p.add_argument("--min-duration", type=float, default=1.0, help="Ignore shorter transmissions")
    p.add_argument("--end-gap", type=float, default=2.5, help="Finish recording after this many seconds without audio")
    p.add_argument("--idle-timeout", type=float, default=1.0, help="Internal websocket read timeout")
    p.add_argument("--overall-timeout", type=float, default=3600.0, help="Stop after this many seconds without any websocket messages")
    p.add_argument("--reconnect-delay", type=float, default=5.0)
    p.add_argument("--gain", type=float, default=1.0, help="PCM gain before writing WAV")
    p.add_argument("--ogg", action="store_true", help="Also convert WAV to OGG/Opus using ffmpeg")
    p.add_argument("--mp3", action="store_true", help="Also convert WAV to MP3 using ffmpeg; useful for Telegram chats where voice messages are forbidden")
    p.add_argument("--telegram-bot-token", default=os.getenv("TELEGRAM_BOT_TOKEN"))
    p.add_argument("--telegram-chat-id", default=os.getenv("TELEGRAM_CHAT_ID"))
    p.add_argument("--telegram-voice", action=argparse.BooleanOptionalAction, default=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        asyncio.run(Recorder(args).run())
        return 0
    except KeyboardInterrupt:
        print("\n[info] interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
