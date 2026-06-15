"""Manually normalize the raw reference photos (host / guest / both) into the clean
PNG format the pipeline wants — accepting ANY common image format as input.

You normally DON'T need to run this: the pipeline now auto-normalizes the photos at
the start of every video job. This script is here for when you want to normalize
up front and see the result, or fix files outside a run.

Run from the backend folder:
    python prepare_inputs.py

HEIC/HEIF (iPhone) support needs the optional 'pillow-heif' package:
    pip install pillow-heif
"""
from __future__ import annotations

import sys
from pathlib import Path

# assets/characters lives next to this script: backend/assets/characters
ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "characters"


def main() -> int:
    try:
        from app.services.image_prep import normalize_inputs, _heic_enabled
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: could not import the normalizer ({e}).\n"
              f"Run this from the backend/ folder so 'app' is importable.")
        return 1

    print(f"Normalizing reference photos in: {ASSETS_DIR}")
    print(f"HEIC/HEIF support: {'on' if _heic_enabled() else 'off (pip install pillow-heif for iPhone HEIC)'}\n")
    if not ASSETS_DIR.exists():
        print(f"ERROR: folder not found: {ASSETS_DIR}\n"
              f"Create it and drop your host / guest / both photos there.")
        return 1

    out = normalize_inputs(ASSETS_DIR)
    for line in out["results"]:
        print("  " + line)
    ready = out["ready"]
    print(f"\nReady: {', '.join(ready) if ready else 'NONE'} ({len(ready)}/3 roles have a clean <role>.png).")
    if "both" not in ready:
        print("Note: no both.png — the establishing two-shot will fall back to a single talking head.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())