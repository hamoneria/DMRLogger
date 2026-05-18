#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

APP_DIR = Path(os.getenv("BM_APP_DIR", Path(__file__).resolve().parent))
DEFAULT_RECORDINGS_DIR = Path(os.getenv("BM_RECORDINGS_DIR", APP_DIR / "recordings"))
DEFAULT_STATE_DIR = Path(os.getenv("BM_STATE_DIR", APP_DIR / "state"))
DEFAULT_LOG_DIR = Path(os.getenv("BM_LOG_DIR", APP_DIR / "logs"))
DEFAULT_ROUTES_CONFIG = Path(os.getenv("BM_ROUTES_CONFIG", APP_DIR / "configs" / "bm_direct_routes.json"))
DEFAULT_DECODER = Path(os.getenv("BM_AMBE_DECODER", APP_DIR / "dmr_ambe33_to_wav"))


def fail(msg: str) -> int:
    print(f"UNHEALTHY: {msg}", file=sys.stderr)
    return 1


def ok(msg: str) -> int:
    print(f"OK: {msg}")
    return 0


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def check_writable_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f".healthcheck-{os.getpid()}"
    probe.write_text(str(time.time()), encoding="utf-8")
    probe.unlink()


def check_routes(path: Path) -> None:
    cfg = load_json(path)
    if not isinstance(cfg, dict):
        raise RuntimeError("routes config is not a JSON object")
    peers = cfg.get("peers")
    if not isinstance(peers, list) or not peers:
        raise RuntimeError("routes config has no peers[]")
    count = 0
    for peer in peers:
        for group in peer.get("groups", []):
            int(group["tg"])
            int(group["slot"])
            count += 1
    if count < 1:
        raise RuntimeError("routes config has no peer groups")


def check_decoder(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"decoder not found: {path}")
    if not os.access(path, os.X_OK):
        raise RuntimeError(f"decoder not executable: {path}")


def check_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found in PATH")


def check_python_imports(component: str) -> None:
    if component in {"poster", "summary", "all"}:
        subprocess.run([sys.executable, "-c", "import faster_whisper"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def check_master(host: str, port: int) -> None:
    # UDP services cannot be positively proven without protocol auth, but DNS resolution catches many config/network failures.
    socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)


def check_heartbeat(path: Path, max_age: float) -> None:
    if not path.exists():
        raise RuntimeError(f"heartbeat missing: {path}")
    data = load_json(path)
    ts = float(data.get("time", 0))
    age = time.time() - ts
    if age > max_age:
        raise RuntimeError(f"heartbeat stale: {path} age={age:.1f}s max={max_age:.1f}s last={data}")


def main() -> int:
    p = argparse.ArgumentParser(description="Healthcheck for dmrlogger services")
    p.add_argument("--component", choices=["recorder", "poster", "summary", "all"], default=os.getenv("BM_HEALTH_COMPONENT", "all"))
    p.add_argument("--routes-config", type=Path, default=DEFAULT_ROUTES_CONFIG)
    p.add_argument("--recordings-dir", type=Path, default=DEFAULT_RECORDINGS_DIR)
    p.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    p.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    p.add_argument("--decoder", type=Path, default=DEFAULT_DECODER)
    p.add_argument("--master", default=os.getenv("BM_MASTER", "2503.master.brandmeister.network"))
    p.add_argument("--port", type=int, default=int(os.getenv("BM_PORT", "62031")))
    p.add_argument("--heartbeat-file", type=Path, default=None)
    p.add_argument("--max-heartbeat-age", type=float, default=float(os.getenv("BM_HEALTH_MAX_HEARTBEAT_AGE", "180")))
    p.add_argument("--no-heartbeat", action="store_true", help="Only run static dependency/config checks")
    args = p.parse_args()

    try:
        check_routes(args.routes_config)
        check_writable_dir(args.recordings_dir)
        check_writable_dir(args.state_dir)
        check_writable_dir(args.log_dir)
        if args.component in {"recorder", "poster", "all"}:
            check_decoder(args.decoder)
        if args.component in {"poster", "all"}:
            check_ffmpeg()
        check_python_imports(args.component)
        if args.component in {"recorder", "all"}:
            check_master(args.master, args.port)
        if not args.no_heartbeat:
            hb = args.heartbeat_file
            if hb is None:
                hb = args.state_dir / f"{args.component}.heartbeat.json"
            check_heartbeat(hb, args.max_heartbeat_age)
    except Exception as exc:
        return fail(str(exc))
    return ok(f"component={args.component}")


if __name__ == "__main__":
    raise SystemExit(main())
