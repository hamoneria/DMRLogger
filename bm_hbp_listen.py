#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

DMRD = b"DMRD"
RPTL = b"RPTL"
RPTK = b"RPTK"
RPTC = b"RPTC"
RPTPING = b"RPTPING"
RPTCL = b"RPTCL"
RPTO = b"RPTO"
RPTACK_PREFIX = b"RPTA"  # RPTACK packets begin with these 4 bytes
MSTNAK_PREFIX = b"MSTN"
MSTPONG_PREFIX = b"MSTP"


def b4(n: int) -> bytes:
    return int(n).to_bytes(4, "big")


def int3(b: bytes) -> int:
    return int.from_bytes(b, "big")


def int4(b: bytes) -> int:
    return int.from_bytes(b, "big")


def fixed(s: str, n: int, align: str = "left", fill: str = " ") -> bytes:
    s = str(s)
    if align == "right":
        s = s.rjust(n, fill)
    else:
        s = s.ljust(n, fill)
    return s[:n].encode("utf-8", errors="replace")


def fetch_pistar_password(host: str, user: str, login_password: str) -> str:
    # Do not print the BrandMeister password. It is read from Pi-Star config and kept in memory only.
    env = os.environ.copy()
    env["SSHPASS"] = login_password
    cmd = [
        "sshpass", "-e", "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/tmp/hermes_pistar_known_hosts",
        "-o", "ConnectTimeout=5",
        f"{user}@{host}",
        "awk -F= '/^Password=/{gsub(/\"/,\"\",$2); print $2; exit}' /etc/mmdvmhost",
    ]
    out = subprocess.check_output(cmd, env=env, text=True, stderr=subprocess.DEVNULL, timeout=15).strip()
    if not out:
        raise RuntimeError("Could not read Password= from /etc/mmdvmhost")
    return out


def build_config(args: argparse.Namespace) -> bytes:
    """Build the Pi-Star/MMDVMHost-style BrandMeister RPTC body.

    Verified from an on-wire Pi-Star capture against BM 2503 on 2026-05-16:
    RPTC payload length is 302 bytes total: 4-byte command + 298-byte body.
    The body starts with the binary 4-byte peer ID, then callsign/frequency/info
    fields. A single ASCII slots byte follows description: "1" slot1, "2" slot2,
    "3" both slots.
    """
    slots = str(args.slots)
    if slots.lower() in {"both", "1,2", "2,1", "12"}:
        slots = "3"
    elif slots not in {"1", "2", "3"}:
        slots = "3"
    return b"".join([
        b4(args.radio_id),
        fixed(args.callsign, 8),
        fixed(args.rx_freq, 9),
        fixed(args.tx_freq, 9),
        fixed(args.tx_power, 2, align="right", fill="0"),
        fixed(args.colorcode, 2, align="right", fill="0"),
        fixed(args.latitude, 8),
        fixed(args.longitude, 9),
        fixed(args.height, 3, align="right", fill="0"),
        fixed(args.location, 20),
        fixed(args.description, 19),
        fixed(slots, 1),
        fixed(args.url, 124),
        fixed(args.software_id, 40),
        fixed(args.package_id, 40),
    ])


def parse_dmrd(packet: bytes) -> dict[str, object]:
    data = packet[:53]
    bits = data[15]
    slot = 2 if bits & 0x80 else 1
    if bits & 0x40:
        call_type = "unit"
    elif (bits & 0x23) == 0x23:
        call_type = "vcsbk"
    else:
        call_type = "group"
    frame_type = (bits & 0x30) >> 4
    dtype_vseq = bits & 0x0F
    return {
        "seq": data[4],
        "rf_src": int3(data[5:8]),
        "dst_id": int3(data[8:11]),
        "peer_id": int4(data[11:15]),
        "slot": slot,
        "call_type": call_type,
        "frame_type": frame_type,
        "dtype_vseq": dtype_vseq,
        "stream_id": int4(data[16:20]),
        "payload": data[20:53],
        "raw53": data,
    }


def ts() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def main() -> int:
    p = argparse.ArgumentParser(description="Minimal listen-only BrandMeister Homebrew/MMDVM peer client")
    p.add_argument("--master", default="2503.master.brandmeister.network")
    p.add_argument("--port", type=int, default=62031)
    p.add_argument("--radio-id", type=int, required=True, help="Use a separate hotspot ESSID, e.g. baseID03")
    p.add_argument("--tg", type=int, default=2501)
    p.add_argument("--duration", type=float, default=900)
    p.add_argument("--password", default=None, help="Hotspot security password. Prefer --fetch-password-from-pistar.")
    p.add_argument("--fetch-password-from-pistar", action="store_true")
    p.add_argument("--pistar-host", default=os.getenv("PISTAR_HOST", ""))
    p.add_argument("--pistar-user", default="pi-star")
    p.add_argument("--pistar-login-password", default=os.getenv("PISTAR_LOGIN_PASSWORD", ""))
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
    p.add_argument("--slots", default="3", help="1=slot1, 2=slot2, 3=both; Pi-Star sends 3 when both network slots are enabled")
    p.add_argument("--url", default="http://www.qrz.ru/db/r1bin")
    p.add_argument("--software-id", default="20240210_PS4")
    p.add_argument("--package-id", default="MMDVM_Nano_hotSPOT")
    p.add_argument("--options", default="", help="Optional RPTO options string. Leave empty unless known-good.")
    p.add_argument("--save-raw", type=Path, default=None, help="Optional file to append raw 53-byte DMRD frames for selected TG")
    args = p.parse_args()

    password = args.password
    if args.fetch_password_from_pistar:
        password = fetch_pistar_password(args.pistar_host, args.pistar_user, args.pistar_login_password)
    if not password:
        raise SystemExit("Need --password or --fetch-password-from-pistar")
    pass_b = password.encode("utf-8")

    master_addr = socket.getaddrinfo(args.master, args.port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    sock.settimeout(1.0)
    local = sock.getsockname()
    rid = b4(args.radio_id)
    print(f"[{ts()}] connecting HBP peer radio_id={args.radio_id} local={local[0]}:{local[1]} master={master_addr[0]}:{master_addr[1]} tg={args.tg}", flush=True)

    state = "LOGIN"
    sock.sendto(RPTL + rid, master_addr)
    print(f"[{ts()}] sent RPTL", flush=True)
    deadline = time.time() + args.duration
    next_ping = time.time() + 5
    connected = False
    frames = 0
    selected_frames = 0
    streams: dict[int, dict[str, object]] = {}
    raw_fh = None
    if args.save_raw:
        args.save_raw.parent.mkdir(parents=True, exist_ok=True)
        raw_fh = args.save_raw.open("ab")

    try:
        while time.time() < deadline:
            if connected and time.time() >= next_ping:
                sock.sendto(RPTPING + rid, master_addr)
                next_ping = time.time() + 10
            try:
                pkt, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            if addr != master_addr:
                print(f"[{ts()}] ignore packet from unexpected {addr}: {pkt[:12]!r}", flush=True)
                continue
            cmd4 = pkt[:4]
            if cmd4 == MSTNAK_PREFIX:
                print(f"[{ts()}] MSTNAK from master: {pkt!r}", flush=True)
                return 2
            if cmd4 == RPTACK_PREFIX:
                if state == "LOGIN":
                    salt = pkt[6:10]
                    digest = hashlib.sha256(salt + pass_b).digest()
                    sock.sendto(RPTK + rid + digest, master_addr)
                    state = "AUTH"
                    print(f"[{ts()}] got RPTACK challenge; sent RPTK", flush=True)
                elif state == "AUTH":
                    sock.sendto(RPTC + build_config(args), master_addr)
                    state = "CONFIG"
                    print(f"[{ts()}] auth accepted; sent RPTC config", flush=True)
                elif state == "CONFIG":
                    if args.options:
                        opt = args.options.encode("utf-8")
                        sock.sendto(RPTO + rid + opt, master_addr)
                        state = "OPTIONS"
                        print(f"[{ts()}] config accepted; sent RPTO options ({len(opt)} bytes)", flush=True)
                    else:
                        state = "YES"
                        connected = True
                        print(f"[{ts()}] config accepted; connected/listening", flush=True)
                elif state == "OPTIONS":
                    state = "YES"
                    connected = True
                    print(f"[{ts()}] options accepted; connected/listening", flush=True)
                else:
                    print(f"[{ts()}] RPTACK in state={state}: {pkt!r}", flush=True)
                continue
            if cmd4 == MSTPONG_PREFIX:
                continue
            if cmd4 == DMRD:
                frames += 1
                d = parse_dmrd(pkt)
                if d["call_type"] == "group" and int(d["dst_id"]) == args.tg:
                    selected_frames += 1
                    sid = int(d["stream_id"])
                    st = streams.setdefault(sid, {"rf_src": d["rf_src"], "dst_id": d["dst_id"], "start": time.time(), "frames": 0})
                    st["frames"] = int(st["frames"]) + 1
                    # frame_type 1 = data sync, dtype 1 voice header / 2 terminator; frame_type 0 = voice, dtype 0..5 bursts
                    print(f"[{ts()}] DMRD tg={d['dst_id']} src={d['rf_src']} slot={d['slot']} stream={sid} ft={d['frame_type']} dv={d['dtype_vseq']} seq={d['seq']}", flush=True)
                    if raw_fh:
                        raw_fh.write(bytes(d["raw53"]))
                        raw_fh.flush()
                continue
            print(f"[{ts()}] other packet {pkt[:24]!r} len={len(pkt)}", flush=True)
    finally:
        if raw_fh:
            raw_fh.close()
        try:
            sock.sendto(RPTCL + rid, master_addr)
        except Exception:
            pass
        sock.close()
    print(f"[{ts()}] done connected={connected} total_dmrd={frames} tg{args.tg}_frames={selected_frames} streams={len(streams)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
