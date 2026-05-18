#!/usr/bin/env python3
# /// script
# dependencies = ["websockets>=15.0", "msgpack>=1.1"]
# ///
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import msgpack
import time
import websockets

SPOTTER_URL = "wss://hose.brandmeister.network/spotter/"
TOKEN = "test:test"
TYPE_GROUP_JOIN = 1
TYPE_CALL_START = 11
TYPE_CALL_DROP = 12
TYPE_CALL_END = 13
TYPE_CALL_AUDIO = 20
TYPE_CALL_ALIAS = 21
TYPE_CALL_METER = 22
TYPE_SYSTEM_RESCUE = 80


def stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=3))).isoformat(timespec="seconds")


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tg", type=int, default=2501)
    p.add_argument("--duration", type=float, default=900)
    p.add_argument("--interesting-source", type=int, action="append", default=[])
    args = p.parse_args()
    interesting = set(args.interesting_source)
    uri = f"{SPOTTER_URL}?token={TOKEN}"
    deadline = time.time() + args.duration
    print(f"{stamp()} connect {SPOTTER_URL} tg={args.tg} duration={args.duration}s interesting={sorted(interesting)}", flush=True)
    async with websockets.connect(uri, subprotocols=["spotter"], max_size=None, ping_interval=None, close_timeout=5) as ws:
        await ws.send(msgpack.packb([TYPE_GROUP_JOIN, [args.tg]], use_bin_type=True))
        current = None
        audio_bytes = 0
        audio_frames = 0
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if isinstance(msg, str):
                print(f"{stamp()} TEXT {msg[:200]!r}", flush=True)
                continue
            obj = msgpack.unpackb(msg, raw=False)
            if not isinstance(obj, list) or not obj:
                print(f"{stamp()} OTHER {obj!r}", flush=True)
                continue
            typ = obj[0]
            if typ == TYPE_CALL_START:
                source = obj[2] if len(obj) > 2 else None
                tg = obj[3] if len(obj) > 3 else None
                mark = " ***" if source in interesting else ""
                current = {"source": source, "tg": tg, "start": time.time(), "alias": ""}
                audio_bytes = 0
                audio_frames = 0
                print(f"{stamp()} START tg={tg} source={source}{mark} raw={obj!r}", flush=True)
            elif typ == TYPE_CALL_ALIAS:
                alias = str(obj[1] if len(obj) > 1 else "").replace("\x00", "").strip()
                if current:
                    current["alias"] = alias
                    source = current.get("source")
                else:
                    source = None
                print(f"{stamp()} ALIAS source={source} alias={alias!r}", flush=True)
            elif typ == TYPE_CALL_AUDIO:
                data = obj[1] if len(obj) > 1 else b""
                if isinstance(data, bytearray):
                    data = bytes(data)
                if isinstance(data, bytes):
                    audio_bytes += len(data)
                    audio_frames += 1
            elif typ in (TYPE_CALL_END, TYPE_CALL_DROP):
                dur = audio_bytes / 8000.0
                if current:
                    wall = time.time() - current["start"]
                    print(f"{stamp()} {'END' if typ == TYPE_CALL_END else 'DROP'} source={current.get('source')} tg={current.get('tg')} alias={current.get('alias')!r} audio={dur:.2f}s frames={audio_frames} wall={wall:.2f}s", flush=True)
                else:
                    print(f"{stamp()} {'END' if typ == TYPE_CALL_END else 'DROP'} no_current audio={dur:.2f}s frames={audio_frames}", flush=True)
                current = None
                audio_bytes = 0
                audio_frames = 0
            elif typ == TYPE_SYSTEM_RESCUE:
                print(f"{stamp()} RESCUE {obj!r}", flush=True)
            elif typ not in (TYPE_CALL_METER,):
                print(f"{stamp()} EVENT {obj!r}", flush=True)
    print(f"{stamp()} done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
