#!/usr/bin/env python3
"""Preview script + scene plan without TTS, video, or approval.

Runs: category → topic research → script → estimated segments → scene blueprint.

Usage (from backend/):
    python run_script_preview.py
    python run_script_preview.py --category Anxiety
    python run_script_preview.py --script-only
    python run_script_preview.py --json
    python run_script_preview.py --save previews/run_01

Set LLM_PROVIDER=mock in .env for zero API cost.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from app.config import get_settings
from app.schemas import ScriptLine, SegmentInfo
from app.services import llm
from app.services.render import LINE_PAUSE_MS, TURN_PAUSE_MS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

INITIAL_SILENCE_MS = 150


def estimate_segments(lines: list[ScriptLine]) -> list[SegmentInfo]:
    """Build segment timings from script line durations (no TTS needed)."""
    cursor_ms = INITIAL_SILENCE_MS
    segments: list[SegmentInfo] = []
    for i, line in enumerate(lines):
        start_ms = cursor_ms
        dur_ms = int(line.seconds * 1000)
        end_ms = start_ms + dur_ms
        segments.append(SegmentInfo(
            index=len(segments) + 1,
            start_second=round(start_ms / 1000, 2),
            end_second=round(end_ms / 1000, 2),
            duration=round(dur_ms / 1000, 2),
            text=line.text,
            audio_url="",
            speaker=line.speaker,
        ))
        cursor_ms = end_ms
        if i < len(lines) - 1:
            pause = (LINE_PAUSE_MS if lines[i + 1].speaker == line.speaker
                     else TURN_PAUSE_MS)
            cursor_ms += pause
    return segments


def _print_preview(category: str, topic, script_pkg, segments, blueprint) -> None:
    print("\n" + "=" * 60)
    print(f"Category: {category}")
    print(f"Topic:    {topic.topic}")
    print(f"Angle:    {topic.angle}")
    print(f"Title:    {script_pkg.title}")
    print(f"Duration: ~{script_pkg.total_seconds:.0f}s ({len(script_pkg.lines)} lines)")
    print("=" * 60)

    print("\n--- Script (one line = one scene clip) ---\n")
    for i, line in enumerate(script_pkg.lines, 1):
        print(f"  [{i}] {line.speaker} ({line.emotion}, ~{line.seconds:.0f}s)")
        print(f"      {line.text}\n")

    if segments:
        print("--- Estimated segments ---\n")
        for seg in segments:
            print(f"  Scene {seg.index}: {seg.start_second}s–{seg.end_second}s | {seg.speaker}")
            print(f"      {seg.text}\n")

    if blueprint:
        print("--- Scene blueprint ---\n")
        for scene in blueprint.scenes:
            print(f"  Scene {scene.scene_number}: {scene.start_second}s–{scene.end_second}s | "
                  f"{scene.speaker_on_camera}")
            print(f"      Action: {scene.character_action}")
            print(f"      Face:   {scene.facial_expression}\n")


async def run_preview(category: str | None, script_only: bool) -> dict:
    s = get_settings()

    if category:
        chosen_category = category
    elif s.default_category.strip():
        chosen_category = s.default_category.strip()
    else:
        logger.info("Picking random category…")
        chosen_category = await llm.pick_category()
    logger.info("Category: %s", chosen_category)

    logger.info("Fetching trending topics…")
    topics = await llm.fetch_trending_topics(chosen_category)
    chosen_topic = await llm.select_best_topic(chosen_category, topics)
    logger.info("Selected topic: %s", chosen_topic.topic)

    seed = f"{chosen_topic.topic} — {chosen_topic.angle}"
    logger.info("Generating script…")
    script_pkg = await llm.generate_script(chosen_category, seed, None)
    if script_pkg is None:
        raise RuntimeError("Script generation failed")

    segments = estimate_segments(script_pkg.lines)
    blueprint = None
    if not script_only:
        logger.info("Generating scene blueprint…")
        blueprint = await llm.generate_scene_plan(
            script_pkg.title, script_pkg.lines, segments
        )

    return {
        "category": chosen_category,
        "topic": chosen_topic.model_dump(),
        "script": script_pkg.model_dump(),
        "segments": [s.model_dump() for s in segments],
        "blueprint": blueprint.model_dump() if blueprint else None,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Preview script and scene plan without rendering audio or video"
    )
    parser.add_argument("--category", help="Override content category")
    parser.add_argument(
        "--script-only",
        action="store_true",
        help="Skip scene blueprint (script + segment timing only)",
    )
    parser.add_argument("--json", action="store_true", help="Print raw JSON only")
    parser.add_argument(
        "--save",
        metavar="DIR",
        help="Save script.json, segments.json, and blueprint.json to this folder",
    )
    args = parser.parse_args()

    try:
        result = asyncio.run(run_preview(args.category, args.script_only))
    except Exception as e:  # noqa: BLE001
        print(f"Preview failed: {e}", file=sys.stderr)
        sys.exit(1)

    if args.save:
        out = Path(args.save)
        out.mkdir(parents=True, exist_ok=True)
        (out / "script.json").write_text(
            json.dumps(result["script"], indent=2), encoding="utf-8"
        )
        (out / "segments.json").write_text(
            json.dumps(result["segments"], indent=2), encoding="utf-8"
        )
        if result["blueprint"]:
            (out / "blueprint.json").write_text(
                json.dumps(result["blueprint"], indent=2), encoding="utf-8"
            )
        meta = {"category": result["category"], "topic": result["topic"]}
        (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"Saved to {out.resolve()}")

    if args.json:
        print(json.dumps(result, indent=2))
        return

    from app.schemas import TrendingTopic, ScriptPackage, SceneBlueprint

    script_pkg = ScriptPackage(**result["script"])
    segments = [SegmentInfo(**s) for s in result["segments"]]
    blueprint = SceneBlueprint(**result["blueprint"]) if result["blueprint"] else None
    topic = TrendingTopic(**result["topic"])
    _print_preview(result["category"], topic, script_pkg, segments, blueprint)


if __name__ == "__main__":
    main()
