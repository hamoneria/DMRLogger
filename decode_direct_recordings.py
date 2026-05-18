#!/usr/bin/env python3
# /// script
# dependencies = ["faster-whisper>=1.1.0"]
# ///
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

APP_DIR = Path(os.getenv("BM_APP_DIR", Path(__file__).resolve().parent))
DEFAULT_RECORDINGS_DIR = Path(os.getenv("BM_RECORDINGS_DIR", APP_DIR / "recordings"))
DEFAULT_DECODER = Path(os.getenv("BM_AMBE_DECODER", APP_DIR / "dmr_ambe33_to_wav"))


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


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
    return text


def process(meta_path: Path, args: argparse.Namespace) -> bool:
    meta = load_json(meta_path)
    base = meta_path.with_suffix("")
    ambe = Path(meta.get("ambe33_path") or base.with_suffix(".ambe33"))
    wav = base.with_suffix(".wav")
    mp3 = base.with_suffix(".mp3")
    transcript = base.with_suffix(".txt")

    if float(meta.get("approx_audio_seconds") or 0) < args.min_duration:
        return False
    if not ambe.exists():
        print(f"[missing] {ambe}", flush=True)
        return False
    changed = False
    if not wav.exists() or wav.stat().st_mtime < ambe.stat().st_mtime:
        run([str(args.decoder), str(ambe), str(wav)])
        changed = True
    if not mp3.exists() or mp3.stat().st_mtime < wav.stat().st_mtime:
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav), "-ar", "16000", "-ac", "1", "-codec:a", "libmp3lame", "-b:a", "48k", str(mp3)])
        changed = True
    if args.transcribe and (not transcript.exists() or transcript.stat().st_mtime < mp3.stat().st_mtime):
        text = transcribe(mp3, args.model)
        transcript.write_text(text + "\n", encoding="utf-8")
        meta["transcript_text"] = text
        changed = True
    meta["wav_path"] = str(wav)
    meta["mp3_path"] = str(mp3)
    if transcript.exists():
        meta["transcript_path"] = str(transcript)
    save_json(meta_path, meta)
    print(f"[ok] {meta_path} -> {mp3}", flush=True)
    return changed


def main() -> int:
    p = argparse.ArgumentParser(description="Decode/transcribe direct BrandMeister HBP recordings")
    p.add_argument("--recordings-dir", type=Path, default=DEFAULT_RECORDINGS_DIR)
    p.add_argument("--decoder", type=Path, default=DEFAULT_DECODER)
    p.add_argument("--model", default="base")
    p.add_argument("--min-duration", type=float, default=3.0)
    p.add_argument("--transcribe", action="store_true")
    p.add_argument("--limit", type=int, default=10)
    args = p.parse_args()
    count = 0
    for meta_path in sorted(args.recordings_dir.rglob("*.json")):
        if count >= args.limit:
            break
        if process(meta_path, args):
            count += 1
    print(f"done changed={count}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
