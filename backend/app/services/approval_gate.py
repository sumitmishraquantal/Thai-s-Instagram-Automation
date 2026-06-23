"""Approval gate after Higgsfield video — owners review the reel, then GDrive upload."""
import asyncio
import json
import logging
from pathlib import Path

from ..config import get_settings
from ..schemas import Scene, SceneBlueprint
from . import approval_email, approvals, higgsfield_video, render

logger = logging.getLogger(__name__)


def _load_script_lines(base: Path) -> tuple[str, list[dict]]:
    """Read title + script lines from *.script.json or segments.json under a render folder."""
    title = base.name
    script_lines: list[dict] = []

    script_files = list(base.glob("*.script.json"))
    if script_files:
        try:
            data = json.loads(script_files[0].read_text(encoding="utf-8"))
            title = data.get("title", title)
            script_lines = [
                {"speaker": line.get("speaker", ""), "text": line.get("text", "")}
                for line in data.get("lines", [])
            ]
        except Exception:  # noqa: BLE001
            script_lines = []

    if not script_lines:
        segf = base / "segments.json"
        if segf.exists():
            segs = json.loads(segf.read_text(encoding="utf-8"))
            script_lines = [
                {"speaker": s.get("speaker", ""), "text": s.get("text", "")} for s in segs
            ]

    return title, script_lines


def build_approval_payload(render_id: str, blueprint: SceneBlueprint | None = None) -> dict:
    """What owners review: scene clips, thumbnail, and full script."""
    base = render.RENDERS_DIR / render_id
    title = blueprint.title if blueprint else render_id
    script_title, script_lines = _load_script_lines(base)
    if script_title and script_title != render_id:
        title = script_title

    s = get_settings()
    base_url = s.approval_base_url.rstrip("/")

    thumbnail_url = None
    thumb = base / "thumbnail.png"
    if thumb.exists():
        thumbnail_url = f"{base_url}/renders/{render_id}/thumbnail.png"

    scene_clips: list[dict] = []
    vid_dir = base / "video"
    if vid_dir.is_dir():
        for clip in sorted(vid_dir.glob("scene_*.mp4")):
            if "_raw" in clip.stem:
                continue
            scene_num = clip.stem.replace("scene_", "")
            scene_clips.append({
                "name": clip.name,
                "label": f"Scene {scene_num}",
                "url": f"{base_url}/renders/{render_id}/video/{clip.name}",
            })

    return {
        "title": title,
        "thumbnail_url": thumbnail_url,
        "scene_clips": scene_clips,
        "script_lines": script_lines,
    }


def save_blueprint(render_id: str, blueprint: SceneBlueprint) -> None:
    path = render.RENDERS_DIR / render_id / "blueprint.json"
    path.write_text(blueprint.model_dump_json(indent=2), encoding="utf-8")


def _reconstruct_blueprint_from_disk(base: Path) -> SceneBlueprint | None:
    """Build a usable SceneBlueprint when no blueprint file exists, using segments.json
    (always written at render time). Scene numbers map to segment 'index', which is how
    the video job pairs scenes to segments. Descriptive fields use neutral studio-canon
    defaults — enough to regenerate the clip faithfully (same speaker, timing, studio)."""
    segf = base / "segments.json"
    if not segf.exists():
        return None
    try:
        segs = json.loads(segf.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not segs:
        return None

    # title from any *.script.json, else the folder name
    title = base.name
    for sf in base.glob("*.script.json"):
        try:
            title = json.loads(sf.read_text(encoding="utf-8")).get("title", title)
            break
        except Exception:  # noqa: BLE001
            pass

    scenes: list[Scene] = []
    for sg in segs:
        idx = sg.get("index")
        if idx is None:
            continue
        speaker = (sg.get("speaker") or "HOST").upper()
        scenes.append(Scene(
            scene_number=int(idx),
            start_second=float(sg.get("start_second", 0.0)),
            end_second=float(sg.get("end_second", sg.get("start_second", 0.0) + sg.get("duration", 0.0))),
            speaker_on_camera=speaker,
            character_action="talking naturally to the other person, relaxed and engaged",
            facial_expression="natural, engaged expression",
            camera_movement="locked medium shot with a slow, subtle push-in",
            background_environment="same fixed studio",
        ))
    if not scenes:
        return None
    return SceneBlueprint(title=title, scenes=scenes)


def load_blueprint(render_id: str) -> SceneBlueprint | None:
    """Find the scene blueprint for a render — dynamically, since the saved name can
    vary per script. Order: exact blueprint.json → any *blueprint*.json in the folder →
    reconstruct from segments.json. Returns None only if nothing usable exists."""
    base = render.RENDERS_DIR / render_id

    exact = base / "blueprint.json"
    if exact.exists():
        try:
            return SceneBlueprint.model_validate_json(exact.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass

    # any blueprint-ish file (topic-named, e.g. "<topic>.blueprint.json")
    candidates = sorted(base.glob("*blueprint*.json")) + sorted(base.glob("*.blueprint.json"))
    for cand in candidates:
        if cand.name == "blueprint.json":
            continue
        try:
            return SceneBlueprint.model_validate_json(cand.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue

    # last resort: rebuild from segments.json so regeneration still works
    rebuilt = _reconstruct_blueprint_from_disk(base)
    if rebuilt is not None:
        logger.info("load_blueprint: no blueprint file for %s — reconstructed from segments.json (%d scenes)",
                    render_id, len(rebuilt.scenes))
    return rebuilt


def start_video_generation(render_id: str, blueprint: SceneBlueprint) -> str:
    job_id = higgsfield_video.start_job(render_id, blueprint, False, False)
    logger.info("Video generation started (job %s) for render %s", job_id, render_id)
    return job_id


def gate_publish(render_id: str, job_id: str, blueprint: SceneBlueprint | None = None) -> dict:
    """After video is ready: email owners for publish approval, or skip if disabled."""
    s = get_settings()
    if not s.require_approval:
        return {"status": "no_gate", "job_id": job_id}

    owners = [o.strip() for o in s.owner_emails.split(",") if o.strip()]
    if not owners:
        raise ValueError("REQUIRE_APPROVAL is on but OWNER_EMAILS is empty in .env")

    record = approvals.create_request(
        workflow="publish",
        render_id=render_id,
        owners=owners,
        payload={**build_approval_payload(render_id, blueprint), "job_id": job_id},
    )

    def _resume(rec: dict):
        rid = rec["render_id"]
        logger.info("Publish approved — uploading render %s to GDrive", rid)
        higgsfield_video.schedule_gdrive_upload(rid)

    approvals.register_resume(record["id"], _resume)
    email_report = approval_email.send_approval_emails(record)
    return {
        "status": "awaiting_approval",
        "approval_id": record["id"],
        "job_id": job_id,
        "owners": owners,
        "email": email_report,
        "message": "Video is ready. Sent for approval — GDrive upload starts when the first owner approves.",
    }


def rerun_selected_scenes(record: dict, scene_numbers: list[int]) -> dict:
    """Regenerate ONLY the selected scene clips, replace them in place, then re-send
    the SAME approval email with everything unchanged except the new clips. The
    approval is NOT consumed — owners keep reviewing. Non-blocking: returns at once
    while regeneration runs in the background; a fresh email is sent on completion."""
    if record.get("status") != approvals.PENDING:
        return {"status": "not_pending",
                "message": f"This request is already {record.get('status')}; nothing was regenerated."}

    render_id = record["render_id"]
    scene_numbers = sorted({int(n) for n in scene_numbers})
    if not scene_numbers:
        return {"status": "noop", "message": "No clips were selected."}

    blueprint = load_blueprint(render_id)
    if blueprint is None:
        return {"status": "error", "message": "blueprint.json not found for this render — cannot regenerate."}

    # A clip can only be re-rendered if the full render context is present. Check
    # BEFORE deleting anything, so a context-less render (e.g. a fixtures-only test)
    # refuses cleanly instead of deleting a clip and then failing.
    seg = render.RENDERS_DIR / render_id / "segments.json"
    if not seg.exists():
        return {"status": "error",
                "message": ("This render has no segments.json (per-scene audio), so clips can't be "
                            "regenerated here — nothing was deleted. Use a real pipeline render "
                            "(which has segments.json + audio + identity) to regenerate.")}

    job_id = higgsfield_video.regenerate_selected_scenes(render_id, scene_numbers, blueprint)
    logger.info("Rerun: render=%s scenes=%s job=%s", render_id, scene_numbers, job_id)

    async def _watch_and_resend():
        # Wait for the regeneration job, then rebuild the payload (fresh clip URLs)
        # and re-send the approval email to the owners.
        for _ in range(720):  # ~1 hour ceiling at 5s
            job = higgsfield_video.JOBS.get(job_id)
            if job and job.get("status") == "completed":
                break
            if job and job.get("status") == "failed":
                logger.error("Rerun job %s failed: %s", job_id, job.get("error"))
                return
            await asyncio.sleep(5)
        payload = {
            **build_approval_payload(render_id, blueprint),
            "job_id": job_id,
            "regenerated_scenes": scene_numbers,
        }
        approvals.update_payload(record["id"], payload)
        fresh = approvals.get(record["id"]) or {**record, "payload": payload}
        if fresh.get("status") == approvals.PENDING:
            approval_email.send_approval_emails(fresh)
            logger.info("Rerun complete for %s — re-sent approval email", render_id)
        else:
            logger.info("Rerun done for %s but approval already %s — not re-sending",
                        render_id, fresh.get("status"))

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    loop.create_task(_watch_and_resend())

    return {
        "status": "regenerating",
        "job_id": job_id,
        "scenes": scene_numbers,
        "message": (f"Regenerating scene(s) {', '.join(str(n) for n in scene_numbers)}. "
                    f"A new approval email with the updated clips will arrive when it's done."),
    }


def resume_after_approval(record: dict) -> bool:
    """Fallback when in-process callback was lost (e.g. server restart)."""
    render_id = record.get("render_id")
    if not render_id:
        logger.error("Cannot resume — approval record has no render_id")
        return False
    logger.info("Resuming GDrive upload for render %s after approval %s", render_id, record.get("id"))
    higgsfield_video.schedule_gdrive_upload(render_id)
    return True