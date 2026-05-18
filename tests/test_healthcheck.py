from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("healthcheck", ROOT / "healthcheck.py")
assert spec and spec.loader
healthcheck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(healthcheck)


def test_check_heartbeat_accepts_fresh(tmp_path: Path) -> None:
    hb = tmp_path / "service.heartbeat.json"
    hb.write_text(json.dumps({"time": time.time(), "component": "x"}), encoding="utf-8")
    healthcheck.check_heartbeat(hb, max_age=10)


def test_check_heartbeat_rejects_stale(tmp_path: Path) -> None:
    hb = tmp_path / "service.heartbeat.json"
    hb.write_text(json.dumps({"time": time.time() - 100, "component": "x"}), encoding="utf-8")
    try:
        healthcheck.check_heartbeat(hb, max_age=10)
    except RuntimeError as exc:
        assert "heartbeat stale" in str(exc)
    else:
        raise AssertionError("stale heartbeat was accepted")
