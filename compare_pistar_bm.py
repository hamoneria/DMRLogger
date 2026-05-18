#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

APP_DIR = Path(os.getenv("BM_APP_DIR", Path(__file__).resolve().parent))
DEFAULT_HOSE_RECORDINGS_DIR = Path(os.getenv("BM_HOSE_RECORDINGS_DIR", APP_DIR / "recordings-hoseline"))

END_RE = re.compile(
    r"M: (?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) DMR Slot (?P<slot>\d+), "
    r"received network end of voice transmission from (?P<call>\S+) to TG (?P<tg>\d+), "
    r"(?P<dur>[0-9.]+) seconds, (?P<loss>\d+)% packet loss, BER: (?P<ber>[0-9.]+)%"
)
HEADER_RE = re.compile(
    r"M: (?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) DMR Slot (?P<slot>\d+), "
    r"received network voice header from (?P<call>\S+) to TG (?P<tg>\d+)"
)
ALIAS_RE = re.compile(r'M: (?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) DMR Slot (?P<slot>\d+), Talker Alias "(?P<alias>.*)"')


def parse_ts(s: str) -> dt.datetime:
    # Pi-Star MMDVM log timestamps are local wall time on this hotspot (MSK here).
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=dt.timezone(dt.timedelta(hours=3)))


def load_bm_events(recordings_dir: Path, start_utc: dt.datetime, end_utc: dt.datetime):
    events = []
    for meta_path in recordings_dir.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            st = dt.datetime.fromisoformat(meta["started_at_utc"])
            dur = float(meta.get("duration_seconds") or 0)
        except Exception:
            continue
        en = st + dt.timedelta(seconds=dur)
        if start_utc <= en <= end_utc:
            events.append({
                "start": st,
                "end": en,
                "duration": dur,
                "source": meta.get("source"),
                "alias": str(meta.get("alias") or ""),
                "file": str(meta_path.with_suffix(".mp3")),
            })
    return events


def match_bm(call: str, end_msk: dt.datetime, dur: float, recordings_dir: Path, tolerance: float):
    end_utc = end_msk.astimezone(dt.timezone.utc)
    events = load_bm_events(recordings_dir, end_utc - dt.timedelta(seconds=120), end_utc + dt.timedelta(seconds=120))
    scored = []
    for ev in events:
        alias = ev["alias"].upper()
        dt_sec = abs((ev["end"] - end_utc).total_seconds())
        dur_diff = abs(float(ev["duration"]) - dur)
        call_match = call.upper() in alias
        if call_match and dt_sec <= tolerance:
            scored.append((0, dt_sec, dur_diff, ev))
        elif call_match and dt_sec <= 20:
            scored.append((1, dt_sec, dur_diff, ev))
    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    return scored[0][3]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.getenv("PISTAR_HOST", ""))
    ap.add_argument("--user", default="pi-star")
    ap.add_argument("--password", default=os.getenv("PISTAR_LOGIN_PASSWORD", ""))
    ap.add_argument("--tg", default="2501")
    ap.add_argument("--recordings-dir", type=Path, default=DEFAULT_HOSE_RECORDINGS_DIR)
    ap.add_argument("--duration", type=float, default=900)
    ap.add_argument("--match-delay", type=float, default=4.0)
    ap.add_argument("--tolerance", type=float, default=4.0)
    args = ap.parse_args()

    env = os.environ.copy()
    env["SSHPASS"] = args.password
    cmd = [
        "sshpass", "-e", "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/tmp/hermes_pistar_known_hosts",
        "-o", "ConnectTimeout=5",
        f"{args.user}@{args.host}",
        "tail -n 0 -F /var/log/pi-star/MMDVM-$(date +%F).log",
    ]
    print(f"[monitor] tailing Pi-Star {args.host}, TG {args.tg}, duration {args.duration}s", flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
    deadline = time.time() + args.duration
    recent_header = {}
    recent_alias = {}
    pending = []
    summary = {"pistar": 0, "matched": 0, "missed": 0}

    try:
        while time.time() < deadline:
            # Non-blocking-ish readline via select.
            import select
            r, _, _ = select.select([proc.stdout], [], [], 0.5)
            if r:
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.rstrip("\n")
                hm = HEADER_RE.search(line)
                if hm and hm.group("tg") == args.tg:
                    ts = parse_ts(hm.group("ts"))
                    call = hm.group("call")
                    recent_header[call] = ts
                    print(f"[pistar-start] {ts.time()} {call} -> TG {args.tg}", flush=True)
                    continue
                am = ALIAS_RE.search(line)
                if am:
                    recent_alias[am.group("slot")] = am.group("alias")
                    continue
                em = END_RE.search(line)
                if em and em.group("tg") == args.tg:
                    ts = parse_ts(em.group("ts"))
                    call = em.group("call")
                    dur = float(em.group("dur"))
                    loss = em.group("loss")
                    summary["pistar"] += 1
                    pending.append((time.time() + args.match_delay, ts, call, dur, loss, line))
                    print(f"[pistar-end]   {ts.time()} {call} {dur:.1f}s loss={loss}% queued compare", flush=True)
            now = time.time()
            todo = [p for p in pending if p[0] <= now]
            pending = [p for p in pending if p[0] > now]
            for _, ts, call, dur, loss, line in todo:
                ev = match_bm(call, ts, dur, args.recordings_dir, args.tolerance)
                if ev:
                    summary["matched"] += 1
                    print(f"[MATCH] {call} Pi-Star end={ts.time()} dur={dur:.1f}s -> BM {Path(ev['file']).name} end={ev['end'].astimezone(dt.timezone(dt.timedelta(hours=3))).time()} dur={ev['duration']:.1f}s", flush=True)
                else:
                    summary["missed"] += 1
                    print(f"[MISS]  {call} Pi-Star end={ts.time()} dur={dur:.1f}s loss={loss}% -> no matching BM/Hoseline recording", flush=True)
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        print(f"[summary] pistar={summary['pistar']} matched={summary['matched']} missed={summary['missed']}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
