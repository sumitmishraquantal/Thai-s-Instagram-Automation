"""End-to-end automated reel pipeline: research → script → render → blueprint → video."""
import asyncio
import logging
import time
from pathlib import Path

from ..config import get_settings
from ..schemas import PipelineResult, TrendingTopic
from . import approval_gate, higgsfield_video, llm, render

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
POLL_INTERVAL_SEC = 5.0
JOB_TIMEOUT_SEC = 3600.0


def _relative_path(path: Path) -> str:
    try:
        return path.relative_to(BACKEND_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


async def _wait_for_video_job(job_id: str) -> dict:
    deadline = time.monotonic() + JOB_TIMEOUT_SEC
    while time.monotonic() < deadline:
        job = higgsfield_video.JOBS.get(job_id)
        if not job:
            raise RuntimeError(f"Video job {job_id} disappeared")
        status = job.get("status", "")
        if status == "completed":
            return job
        if status == "failed":
            raise RuntimeError(job.get("error") or "Video generation failed")
        await asyncio.sleep(POLL_INTERVAL_SEC)
    raise TimeoutError(f"Video job {job_id} did not finish within {JOB_TIMEOUT_SEC:.0f}s")


async def run_podcast_pipeline(category: str | None = None) -> PipelineResult:
    """Run the full podcast reel pipeline without UI interaction."""
    s = get_settings()

    if category:
        chosen_category = category
    elif s.default_category.strip():
        chosen_category = s.default_category.strip()
    else:
        logger.info("Picking category via LLM…")
        chosen_category = await llm.pick_category()
    logger.info("Category: %s", chosen_category)

    logger.info("Fetching trending topics…")
    topics = await llm.fetch_trending_topics(chosen_category)
    chosen_topic = await llm.select_best_topic(chosen_category, topics)
    logger.info("Selected topic: %s — %s", chosen_topic.topic, chosen_topic.angle)

    seed = f"{chosen_topic.topic} — {chosen_topic.angle}"
    logger.info("Generating script…")
    script_pkg = await llm.generate_script(chosen_category, seed, None)
    if script_pkg is None:
        raise RuntimeError("Script generation failed")

    logger.info("Rendering audio (%d lines, ~%.0fs)…", len(script_pkg.lines), script_pkg.total_seconds)
    render_result = await render.render_final_audio(
        script_pkg.title,
        script_pkg.lines,
        s.host_voice_id,
        s.guest_voice_id,
    )

    logger.info("Generating scene blueprint…")
    blueprint = await llm.generate_scene_plan(
        script_pkg.title,
        script_pkg.lines,
        render_result.segments,
    )
    print("\n--- Scene blueprint ---\n")
    print(blueprint.model_dump_json(indent=2))
    print()

    logger.info("Approval gate / video generation for render %s…", render_result.render_id)
    gate = approval_gate.gate_video_generation(render_result.render_id, blueprint)

    if gate["status"] == "awaiting_approval":
        logger.info("Awaiting owner approval (id=%s)", gate["approval_id"])
        return PipelineResult(
            render_id=render_result.render_id,
            job_id="",
            title=script_pkg.title,
            category=chosen_category,
            selected_topic=chosen_topic,
            merged_video_path="",
            audio_path=_relative_path(BACKEND_ROOT / "renders" / render_result.render_id),
            status="awaiting_approval",
            approval_id=gate["approval_id"],
            message=gate.get("message"),
        )

    job_id = gate["job_id"]
    job = await _wait_for_video_job(job_id)

    merged_rel = f"renders/{render_result.render_id}/video/merged_reel.mp4"
    merged_path = BACKEND_ROOT / "renders" / render_result.render_id / "video" / "merged_reel.mp4"
    if merged_path.exists():
        merged_rel = _relative_path(merged_path)

    result = PipelineResult(
        render_id=render_result.render_id,
        job_id=job_id,
        title=script_pkg.title,
        category=chosen_category,
        selected_topic=chosen_topic,
        merged_video_path=merged_rel,
        audio_path=_relative_path(BACKEND_ROOT / "renders" / render_result.render_id),
        status=job.get("status", "completed"),
    )
    logger.info("Pipeline complete: %s", result.merged_video_path)
    return result
