#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

APP_DIR = Path(os.getenv("BM_APP_DIR", Path(__file__).resolve().parent))
DEFAULT_RECORDINGS_DIR = Path(os.getenv("BM_RECORDINGS_DIR", APP_DIR / "recordings"))
DEFAULT_ROUTES_CONFIG = Path(os.getenv("BM_ROUTES_CONFIG", APP_DIR / "configs" / "bm_direct_routes.json"))

from bm_hbp_listen import (
    DMRD,
    MSTNAK_PREFIX,
    MSTPONG_PREFIX,
    RPTACK_PREFIX,
    RPTC,
    RPTCL,
    RPTK,
    RPTL,
    RPTPING,
    b4,
    build_config,
    fetch_pistar_password,
    parse_dmrd,
)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def local_ts() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def safe_ts(t: dt.datetime) -> str:
    return t.strftime("%Y%m%dT%H%M%SZ")


def save_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_heartbeat(path: Path | None, component: str, **fields: Any) -> None:
    if not path:
        return
    payload = {"component": component, "time": time.time(), "time_utc": utc_now().isoformat()}
    payload.update(fields)
    save_json_atomic(path, payload)


@dataclass
class StreamRec:
    stream_id: int
    rf_src: int
    dst_id: int
    slot: int
    peer_id: int
    started_at: dt.datetime
    last_seen: float
    base: Path
    raw_fh: BinaryIO
    ambe_fh: BinaryIO
    label: str = ""
    frames_total: int = 0
    voice_frames: int = 0
    header_frames: int = 0
    terminator_frames: int = 0
    data_sync_frames: int = 0
    first_seq: int | None = None
    last_seq: int | None = None
    frame_types: dict[str, int] = field(default_factory=dict)
    dtype_vseq: dict[str, int] = field(default_factory=dict)

    @property
    def raw_path(self) -> Path:
        return self.base.with_suffix(".dmrd")

    @property
    def ambe_path(self) -> Path:
        return self.base.with_suffix(".ambe33")

    @property
    def meta_path(self) -> Path:
        return self.base.with_suffix(".json")

    def add(self, pkt: bytes, d: dict[str, object]) -> None:
        self.frames_total += 1
        self.last_seen = time.time()
        seq = int(d["seq"])
        if self.first_seq is None:
            self.first_seq = seq
        self.last_seq = seq
        ft = int(d["frame_type"])
        dv = int(d["dtype_vseq"])
        self.frame_types[str(ft)] = self.frame_types.get(str(ft), 0) + 1
        self.dtype_vseq[str(dv)] = self.dtype_vseq.get(str(dv), 0) + 1
        # Save full on-wire DMRD UDP payload (usually 55 bytes from BM; 53-byte core + optional RSSI bytes).
        self.raw_fh.write(pkt)
        self.raw_fh.flush()
        if ft == 0:
            # Voice bursts carry the 33-byte DMR payload at offsets 20:53 in the DMRD packet.
            self.voice_frames += 1
            self.ambe_fh.write(pkt[20:53])
            self.ambe_fh.flush()
        else:
            self.data_sync_frames += 1
            if dv == 1:
                self.header_frames += 1
            elif dv == 2:
                self.terminator_frames += 1

    def close(self, reason: str) -> dict[str, object]:
        self.raw_fh.close()
        self.ambe_fh.close()
        finished_at = utc_now()
        approx_audio_seconds = round(self.voice_frames * 0.06, 2)
        meta = {
            "source": "brandmeister-hbp",
            "stream_id": self.stream_id,
            "rf_src": self.rf_src,
            "talkgroup": self.dst_id,
            "slot": self.slot,
            "label": self.label,
            "peer_id": self.peer_id,
            "started_at_utc": self.started_at.isoformat(),
            "finished_at_utc": finished_at.isoformat(),
            "close_reason": reason,
            "frames_total": self.frames_total,
            "voice_frames": self.voice_frames,
            "header_frames": self.header_frames,
            "terminator_frames": self.terminator_frames,
            "data_sync_frames": self.data_sync_frames,
            "first_seq": self.first_seq,
            "last_seq": self.last_seq,
            "frame_types": self.frame_types,
            "dtype_vseq": self.dtype_vseq,
            "approx_audio_seconds": approx_audio_seconds,
            "raw_dmrd_path": str(self.raw_path),
            "ambe33_path": str(self.ambe_path),
            "notes": "ambe33 contains raw 33-byte DMR payloads from voice frames only; not yet decoded to PCM.",
        }
        save_json_atomic(self.meta_path, meta)
        return meta


def make_base(out_dir: Path, peer_id: int, tg: int, slot: int, label: str, rf_src: int, stream_id: int, started: dt.datetime) -> Path:
    day = started.strftime("%Y-%m-%d")
    route = label or f"tg{tg}_ts{slot}"
    # Keep paths stable and shell-friendly.
    route = "".join(c if c.isalnum() or c in "._-" else "_" for c in route)
    d = out_dir / f"peer{peer_id}" / f"{route}_tg{tg}_ts{slot}" / day
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe_ts(started)}_tg{tg}_ts{slot}_src{rf_src}_stream{stream_id}"


def load_routes(path: Path | None, radio_id: int, fallback_tg: int) -> dict[tuple[int, int], dict[str, Any]]:
    if not path:
        return {(fallback_tg, 1): {"tg": fallback_tg, "slot": 1, "label": f"TG{fallback_tg}"}, (fallback_tg, 2): {"tg": fallback_tg, "slot": 2, "label": f"TG{fallback_tg}"}}
    cfg = json.loads(path.read_text(encoding="utf-8"))
    routes: dict[tuple[int, int], dict[str, Any]] = {}
    for peer in cfg.get("peers", []):
        if int(peer.get("radio_id")) != int(radio_id):
            continue
        for group in peer.get("groups", []):
            tg = int(group["tg"])
            slot = int(group["slot"])
            routes[(tg, slot)] = dict(group)
    if not routes:
        raise SystemExit(f"No routes for radio_id={radio_id} in {path}")
    return routes


def connect_hbp(args: argparse.Namespace, password: str) -> tuple[socket.socket, tuple[str, int], bytes]:
    pass_b = password.encode("utf-8")
    master_addr = socket.getaddrinfo(args.master, args.port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    sock.settimeout(1.0)
    rid = b4(args.radio_id)
    print(f"[{local_ts()}] connect radio_id={args.radio_id} local={sock.getsockname()} master={master_addr}", flush=True)
    sock.sendto(RPTL + rid, master_addr)
    state = "LOGIN"
    deadline = time.time() + 20
    while time.time() < deadline:
        pkt, addr = sock.recvfrom(2048)
        if addr != master_addr:
            continue
        cmd4 = pkt[:4]
        if cmd4 == MSTNAK_PREFIX:
            raise RuntimeError(f"MSTNAK during connect: {pkt!r}")
        if cmd4 != RPTACK_PREFIX:
            continue
        if state == "LOGIN":
            salt = pkt[6:10]
            digest = hashlib.sha256(salt + pass_b).digest()
            sock.sendto(RPTK + rid + digest, master_addr)
            state = "AUTH"
            print(f"[{local_ts()}] auth challenge -> RPTK", flush=True)
        elif state == "AUTH":
            sock.sendto(RPTC + build_config(args), master_addr)
            state = "CONFIG"
            print(f"[{local_ts()}] auth accepted -> RPTC", flush=True)
        elif state == "CONFIG":
            print(f"[{local_ts()}] config accepted; recording", flush=True)
            return sock, master_addr, rid
    raise TimeoutError("HBP connect timeout")


def is_terminator(d: dict[str, object]) -> bool:
    # In observed BM traffic the final frame appears as frame_type=2,dtype=2;
    # keep the broader dtype=2 test for compatibility with other HBP variants.
    return int(d["dtype_vseq"]) == 2 and int(d["frame_type"]) in {1, 2}


def main() -> int:
    p = argparse.ArgumentParser(description="Record direct BrandMeister HBP DMRD streams to raw files")
    p.add_argument("--master", default="2503.master.brandmeister.network")
    p.add_argument("--port", type=int, default=62031)
    p.add_argument("--radio-id", type=int, required=True)
    p.add_argument("--tg", type=int, default=2501, help="Fallback single-TG mode if --routes-config is not provided")
    p.add_argument("--routes-config", type=Path, default=DEFAULT_ROUTES_CONFIG, help="JSON routes config with peers/groups; records only configured (tg, slot) pairs")
    p.add_argument("--duration", type=float, default=3600)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_RECORDINGS_DIR)
    p.add_argument("--min-voice-frames", type=int, default=6, help="Delete metadata-worthy streams below this many voice frames")
    p.add_argument("--gap-timeout", type=float, default=2.5)
    p.add_argument("--password", default=None)
    p.add_argument("--fetch-password-from-pistar", action="store_true")
    p.add_argument("--pistar-host", default=os.getenv("PISTAR_HOST", ""))
    p.add_argument("--pistar-user", default="pi-star")
    p.add_argument("--pistar-login-password", default=os.getenv("PISTAR_LOGIN_PASSWORD", ""))
    # RPTC fields; defaults mirror the captured Pi-Star config.
    p.add_argument("--callsign", default="R1BIN")
    p.add_argument("--rx-freq", default="438025000")
    p.add_argument("--tx-freq", default="430425000")
    p.add_argument("--tx-power", default="01")
    p.add_argument("--colorcode", default="1")
    p.add_argument("--latitude", default="47.52973")
    p.add_argument("--longitude", default="19.057562")
    p.add_argument("--height", default="0")
    p.add_argument("--location", default="Budapest")
    p.add_argument("--description", default="Hungary")
    p.add_argument("--slots", default="3")
    p.add_argument("--url", default="http://www.qrz.ru/db/r1bin")
    p.add_argument("--software-id", default="20240210_PS4")
    p.add_argument("--package-id", default="MMDVM_Nano_hotSPOT")
    p.add_argument("--heartbeat-file", type=Path, default=None, help="Write liveness heartbeat JSON for supervisors/healthchecks")
    p.add_argument("--heartbeat-interval", type=float, default=10.0)
    args = p.parse_args()

    password = args.password or os.getenv("BM_HOTSPOT_PASSWORD")
    if args.fetch_password_from_pistar:
        password = fetch_pistar_password(args.pistar_host, args.pistar_user, args.pistar_login_password)
    if not password:
        raise SystemExit("Need --password, BM_HOTSPOT_PASSWORD, or --fetch-password-from-pistar")

    sock, master_addr, rid = connect_hbp(args, password)
    routes = load_routes(args.routes_config, args.radio_id, args.tg)
    print(f"[{local_ts()}] active routes: " + ", ".join(f"TG{tg}/TS{slot}:{r.get('label','')}" for (tg, slot), r in sorted(routes.items())), flush=True)
    deadline = time.time() + args.duration
    next_ping = time.time() + 5
    next_heartbeat = 0.0
    active: dict[tuple[int, int, int], StreamRec] = {}
    saved = 0
    discarded = 0
    frames_total = 0

    def finalize(key: tuple[int, int, int], reason: str) -> None:
        nonlocal saved, discarded
        rec = active.pop(key, None)
        if not rec:
            return
        meta = rec.close(reason)
        if rec.voice_frames < args.min_voice_frames:
            for path in (rec.raw_path, rec.ambe_path, rec.meta_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            discarded += 1
            print(f"[{local_ts()}] discard stream={rec.stream_id} src={rec.rf_src} tg={rec.dst_id} slot={rec.slot} voice_frames={rec.voice_frames} reason={reason}", flush=True)
        else:
            saved += 1
            print(
                f"[{local_ts()}] saved stream={rec.stream_id} src={rec.rf_src} tg={rec.dst_id} slot={rec.slot} "
                f"voice_frames={rec.voice_frames} approx={meta['approx_audio_seconds']}s file={rec.raw_path}",
                flush=True,
            )

    try:
        while time.time() < deadline:
            now = time.time()
            if now >= next_ping:
                sock.sendto(RPTPING + rid, master_addr)
                next_ping = now + 10
            if args.heartbeat_file and now >= next_heartbeat:
                write_heartbeat(
                    args.heartbeat_file,
                    "recorder",
                    radio_id=args.radio_id,
                    master=args.master,
                    routes=[f"{tg}:{slot}" for (tg, slot) in sorted(routes)],
                    frames_total=frames_total,
                    saved=saved,
                    discarded=discarded,
                    active_streams=len(active),
                )
                next_heartbeat = now + max(1.0, args.heartbeat_interval)
            for key, rec in list(active.items()):
                if now - rec.last_seen > args.gap_timeout:
                    finalize(key, "gap_timeout")
            try:
                pkt, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            if addr != master_addr:
                continue
            cmd4 = pkt[:4]
            if cmd4 == MSTNAK_PREFIX:
                print(f"[{local_ts()}] MSTNAK: {pkt!r}", flush=True)
                continue
            if cmd4 == MSTPONG_PREFIX or cmd4 == RPTACK_PREFIX:
                continue
            if cmd4 != DMRD:
                print(f"[{local_ts()}] other packet {pkt[:16]!r} len={len(pkt)}", flush=True)
                continue
            frames_total += 1
            d = parse_dmrd(pkt)
            tg = int(d["dst_id"])
            slot = int(d["slot"])
            route = routes.get((tg, slot))
            if d["call_type"] != "group" or route is None:
                continue
            sid = int(d["stream_id"])
            key = (sid, tg, slot)
            if key not in active:
                started = utc_now()
                label = str(route.get("label") or f"TG{tg}")
                base = make_base(args.out_dir, args.radio_id, tg, slot, label, int(d["rf_src"]), sid, started)
                rec = StreamRec(
                    stream_id=sid,
                    rf_src=int(d["rf_src"]),
                    dst_id=tg,
                    slot=slot,
                    peer_id=int(d["peer_id"]),
                    label=label,
                    started_at=started,
                    last_seen=time.time(),
                    base=base,
                    raw_fh=base.with_suffix(".dmrd").open("ab"),
                    ambe_fh=base.with_suffix(".ambe33").open("ab"),
                )
                active[key] = rec
                print(f"[{local_ts()}] start stream={sid} src={rec.rf_src} tg={rec.dst_id} slot={rec.slot} label={rec.label}", flush=True)
            rec = active[key]
            rec.add(pkt, d)
            if is_terminator(d):
                finalize(key, "terminator")
    finally:
        for sid in list(active.keys()):
            finalize(sid, "shutdown")
        try:
            sock.sendto(RPTCL + rid, master_addr)
        except Exception:
            pass
        sock.close()
    write_heartbeat(args.heartbeat_file, "recorder", radio_id=args.radio_id, master=args.master, frames_total=frames_total, saved=saved, discarded=discarded, active_streams=0, exiting=True)
    print(f"[{local_ts()}] done frames_total={frames_total} saved={saved} discarded={discarded}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
