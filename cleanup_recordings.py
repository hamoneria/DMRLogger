#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APP_DIR = Path(os.getenv("BM_APP_DIR", Path(__file__).resolve().parent))
DEFAULT_RECORDINGS_DIR = Path(os.getenv("BM_RECORDINGS_DIR", APP_DIR / "recordings"))
DEFAULT_STATE_DIR = Path(os.getenv("BM_STATE_DIR", APP_DIR / "state"))
DEFAULT_LOG_DIR = Path(os.getenv("BM_LOG_DIR", APP_DIR / "logs"))
DEFAULT_STATE_FILE = Path(os.getenv("BM_CLEANUP_STATE_FILE", DEFAULT_STATE_DIR / "cleanup_state.json"))

ARTIFACT_SUFFIXES = (".dmrd", ".ambe33", ".json", ".wav", ".mp3", ".txt", ".log")
DEFAULT_KEEP_DAYS = 14
DEFAULT_MAX_BYTES = 5 * 1024 * 1024 * 1024
DEFAULT_MIN_FREE_BYTES = 1024 * 1024 * 1024


def parse_bytes(value: str | int | None) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, int):
        return value
    s = str(value).strip().lower().replace(" ", "")
    mult = 1
    for suffix, factor in (
        ("kib", 1024), ("kb", 1000), ("k", 1024),
        ("mib", 1024**2), ("mb", 1000**2), ("m", 1024**2),
        ("gib", 1024**3), ("gb", 1000**3), ("g", 1024**3),
        ("tib", 1024**4), ("tb", 1000**4), ("t", 1024**4),
    ):
        if s.endswith(suffix):
            mult = factor
            s = s[: -len(suffix)]
            break
    return int(float(s) * mult)


def parse_utc(value: Any) -> float | None:
    if not value:
        return None
    try:
        d = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


@dataclass
class RecordingGroup:
    base: Path
    meta_path: Path | None = None
    files: list[Path] = field(default_factory=list)
    started_ts: float = 0.0
    finished_ts: float = 0.0
    size: int = 0

    @property
    def age_ts(self) -> float:
        return self.finished_ts or self.started_ts or min((p.stat().st_mtime for p in self.files if p.exists()), default=0.0)


def find_recording_groups(recordings_dir: Path) -> list[RecordingGroup]:
    by_base: dict[Path, RecordingGroup] = {}
    if not recordings_dir.exists():
        return []
    for path in recordings_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.suffix not in ARTIFACT_SUFFIXES:
            continue
        base = path.with_suffix("")
        group = by_base.setdefault(base, RecordingGroup(base=base))
        group.files.append(path)
        if path.suffix == ".json":
            group.meta_path = path
    for group in by_base.values():
        group.size = sum(p.stat().st_size for p in group.files if p.exists())
        if group.meta_path and group.meta_path.exists():
            meta = load_json(group.meta_path)
            group.started_ts = parse_utc(meta.get("started_at_utc")) or 0.0
            group.finished_ts = parse_utc(meta.get("finished_at_utc")) or 0.0
    return sorted(by_base.values(), key=lambda g: g.age_ts)


def total_bytes(recordings_dir: Path) -> int:
    if not recordings_dir.exists():
        return 0
    return sum(p.stat().st_size for p in recordings_dir.rglob("*") if p.is_file())


def delete_group(group: RecordingGroup, dry_run: bool) -> tuple[int, int, list[str]]:
    deleted_files = 0
    deleted_bytes = 0
    paths: list[str] = []
    for path in sorted(group.files, key=lambda p: str(p)):
        if not path.exists():
            continue
        size = path.stat().st_size
        paths.append(str(path))
        deleted_files += 1
        deleted_bytes += size
        if not dry_run:
            path.unlink()
    return deleted_files, deleted_bytes, paths


def prune_empty_dirs(root: Path, dry_run: bool) -> int:
    if not root.exists():
        return 0
    removed = 0
    dirs = sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
    for d in dirs:
        try:
            next(d.iterdir())
            continue
        except StopIteration:
            removed += 1
            if not dry_run:
                d.rmdir()
        except FileNotFoundError:
            continue
    return removed


def cleanup(args: argparse.Namespace) -> dict[str, Any]:
    now = time.time()
    recordings_dir = args.recordings_dir
    groups = find_recording_groups(recordings_dir)
    before_bytes = total_bytes(recordings_dir)
    cutoff = now - args.keep_days * 86400 if args.keep_days > 0 else 0

    selected: dict[Path, str] = {}
    for group in groups:
        if cutoff and group.age_ts and group.age_ts < cutoff:
            selected[group.base] = f"older_than_{args.keep_days}d"

    projected_bytes = before_bytes - sum(g.size for g in groups if g.base in selected)
    max_bytes = args.max_bytes
    if max_bytes and projected_bytes > max_bytes:
        for group in groups:
            if group.base in selected:
                continue
            selected[group.base] = f"over_max_bytes_{max_bytes}"
            projected_bytes -= group.size
            if projected_bytes <= max_bytes:
                break

    if args.min_free_bytes:
        try:
            stat = os.statvfs(recordings_dir if recordings_dir.exists() else recordings_dir.parent)
            free_bytes = stat.f_bavail * stat.f_frsize
        except Exception:
            free_bytes = None
        if free_bytes is not None and free_bytes < args.min_free_bytes:
            target_extra = args.min_free_bytes - free_bytes
            freed_for_min = sum(g.size for g in groups if g.base in selected)
            for group in groups:
                if group.base in selected:
                    continue
                selected[group.base] = f"low_free_space_min_{args.min_free_bytes}"
                freed_for_min += group.size
                if freed_for_min >= target_extra:
                    break

    deleted_groups = 0
    deleted_files = 0
    deleted_bytes = 0
    examples: list[dict[str, Any]] = []
    for group in groups:
        reason = selected.get(group.base)
        if not reason:
            continue
        files_count, bytes_count, paths = delete_group(group, args.dry_run)
        deleted_groups += 1
        deleted_files += files_count
        deleted_bytes += bytes_count
        if len(examples) < args.report_limit:
            examples.append({"base": str(group.base), "reason": reason, "bytes": bytes_count, "files": paths[:10]})

    empty_dirs_removed = prune_empty_dirs(recordings_dir, args.dry_run)
    after_bytes = before_bytes - deleted_bytes if args.dry_run else total_bytes(recordings_dir)
    result = {
        "dry_run": args.dry_run,
        "recordings_dir": str(recordings_dir),
        "keep_days": args.keep_days,
        "max_bytes": max_bytes,
        "min_free_bytes": args.min_free_bytes,
        "groups_seen": len(groups),
        "bytes_before": before_bytes,
        "bytes_after": after_bytes,
        "deleted_groups": deleted_groups,
        "deleted_files": deleted_files,
        "deleted_bytes": deleted_bytes,
        "empty_dirs_removed": empty_dirs_removed,
        "examples": examples,
        "time_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if args.state_file:
        save_json_atomic(args.state_file, result)
    return result


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Retention cleanup for DMR recording artifacts")
    p.add_argument("--recordings-dir", type=Path, default=DEFAULT_RECORDINGS_DIR)
    p.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    p.add_argument("--keep-days", type=int, default=int(os.getenv("BM_RETENTION_DAYS", str(DEFAULT_KEEP_DAYS))))
    p.add_argument("--max-bytes", type=parse_bytes, default=parse_bytes(os.getenv("BM_RETENTION_MAX_BYTES", str(DEFAULT_MAX_BYTES))))
    p.add_argument("--min-free-bytes", type=parse_bytes, default=parse_bytes(os.getenv("BM_RETENTION_MIN_FREE_BYTES", str(DEFAULT_MIN_FREE_BYTES))))
    p.add_argument("--dry-run", action="store_true", default=os.getenv("BM_RETENTION_DRY_RUN", "0") == "1")
    p.add_argument("--apply", dest="dry_run", action="store_false", help="Actually delete files; dry-run is recommended for first use")
    p.add_argument("--report-limit", type=int, default=20)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    result = cleanup(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
