#!/usr/bin/env python3
"""Smoke-test GDrive upload without running the full reel pipeline.

Uses the same rclone batch upload as higgsfield_video.py (scene clips + thumbnail).

Usage (from backend/):
    python test_gdrive_upload.py              # upload hardcoded dummy clips
    python test_gdrive_upload.py --create     # create tiny dummy .mp4 files first
    python test_gdrive_upload.py --dry-run    # print what would upload, no rclone
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.config import get_settings
from app.services.higgsfield_video import _rclone_upload_all

# ── Edit these paths to point at any local files you want to test with ────────
BACKEND_ROOT = Path(__file__).resolve().parent
FIXTURE_DIR = BACKEND_ROOT / "test_fixtures" / "gdrive"

# Local sources (scene-style names). Renamed on upload → RawClip1.mp4, RawClip2.mp4, …
DUMMY_CLIPS: list[Path] = [
    FIXTURE_DIR / "scene_01.mp4",
    FIXTURE_DIR / "scene_02.mp4",
    FIXTURE_DIR / "scene_03.mp4",
]


def _create_dummy_clips() -> list[Path]:
    """Tiny placeholder files — enough to verify rclone copy, not real video."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for i, path in enumerate(DUMMY_CLIPS, start=1):
        path.write_bytes(f"gdrive-upload-test clip {i}\n".encode())
        created.append(path)
        print(f"  created {path}")
    return created


def _build_upload_items(clips: list[Path]) -> list[tuple[Path, str]]:
    s = get_settings()
    prefix = s.gdrive_clip_prefix or "RawClip"
    existing = [p for p in clips if p.exists()]
    return [(p, f"{prefix}{i}{p.suffix}") for i, p in enumerate(existing, start=1)]


async def _run(*, create: bool, dry_run: bool) -> int:
    s = get_settings()

    if create:
        print("Creating dummy clip files…")
        _create_dummy_clips()

    items = _build_upload_items(DUMMY_CLIPS)
    missing = [p for p in DUMMY_CLIPS if not p.exists()]
    if missing:
        print("Missing files (create them or run with --create):", file=sys.stderr)
        for p in missing:
            print(f"  {p}", file=sys.stderr)
        return 1

    dest = (s.rclone_remote or "").strip()
    if not dest:
        print("RCLONE_REMOTE is empty in .env", file=sys.stderr)
        return 1

    print("GDrive upload test")
    print(f"  remote : {dest}")
    print(f"  rclone : {s.rclone_exe}")
    print(f"  config : {s.rclone_config or '(default)'}")
    print("  files  :")
    for src, name in items:
        print(f"    {src}  ->  {name}")

    if dry_run:
        print("\nDry run — no upload performed.")
        return 0

    ok, log = await _rclone_upload_all(items, dest, rclone_exe=s.rclone_exe)
    result = {
        "status": "ok" if ok else "failed",
        "dest": dest,
        "files": [name for _, name in items],
        "log_tail": log[-800:],
    }
    print("\n" + json.dumps(result, indent=2))
    if not ok:
        print("\nUpload failed. Check rclone is on PATH, gdrive remote is configured, and the destination exists.", file=sys.stderr)
        return 1

    print(f"\nUpload OK — check {dest} on Google Drive for {result['files']}.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Test GDrive scene-clip upload (no full pipeline)")
    parser.add_argument("--create", action="store_true", help="Create tiny dummy .mp4 files under test_fixtures/gdrive/")
    parser.add_argument("--dry-run", action="store_true", help="Show what would upload without calling rclone")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(create=args.create, dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
