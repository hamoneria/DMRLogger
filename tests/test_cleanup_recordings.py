from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cleanup_recordings", ROOT / "cleanup_recordings.py")
assert spec and spec.loader
c = importlib.util.module_from_spec(spec)
sys.modules["cleanup_recordings"] = c
spec.loader.exec_module(c)


def make_group(root: Path, name: str, days_old: int, size: int = 10) -> Path:
    base = root / "peer1" / "TG1_tg1_ts1" / "2026-01-01" / name
    base.parent.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_old)
    meta = {
        "started_at_utc": started.isoformat(),
        "finished_at_utc": (started + dt.timedelta(seconds=3)).isoformat(),
    }
    base.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
    base.with_suffix(".dmrd").write_bytes(b"x" * size)
    base.with_suffix(".ambe33").write_bytes(b"y" * size)
    return base


def args(tmp_path: Path, **kwargs) -> argparse.Namespace:
    defaults = dict(
        recordings_dir=tmp_path / "recordings",
        state_file=tmp_path / "state" / "cleanup_state.json",
        keep_days=14,
        max_bytes=0,
        min_free_bytes=0,
        dry_run=True,
        report_limit=20,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_parse_bytes() -> None:
    assert c.parse_bytes("1k") == 1024
    assert c.parse_bytes("2M") == 2 * 1024 * 1024
    assert c.parse_bytes("1.5g") == int(1.5 * 1024**3)


def test_dry_run_does_not_delete_old_group(tmp_path: Path) -> None:
    root = tmp_path / "recordings"
    make_group(root, "old", days_old=30)
    result = c.cleanup(args(tmp_path, dry_run=True))
    assert result["deleted_groups"] == 1
    assert len(list(root.rglob("*.*"))) == 3


def test_apply_deletes_old_group_and_prunes_dirs(tmp_path: Path) -> None:
    root = tmp_path / "recordings"
    make_group(root, "old", days_old=30)
    result = c.cleanup(args(tmp_path, dry_run=False))
    assert result["deleted_groups"] == 1
    assert result["deleted_files"] == 3
    assert not list(root.rglob("*.*"))
    assert (tmp_path / "state" / "cleanup_state.json").exists()


def test_max_bytes_deletes_oldest_until_under_limit(tmp_path: Path) -> None:
    root = tmp_path / "recordings"
    old = make_group(root, "old", days_old=10, size=100)
    new = make_group(root, "new", days_old=1, size=100)
    result = c.cleanup(args(tmp_path, keep_days=0, max_bytes=350, dry_run=False))
    assert result["deleted_groups"] == 1
    assert not old.with_suffix(".json").exists()
    assert new.with_suffix(".json").exists()
