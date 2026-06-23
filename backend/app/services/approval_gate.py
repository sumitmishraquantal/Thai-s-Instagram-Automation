"""Approval gate after Higgsfield video — owners review the reel, then GDrive upload."""
import json
import logging
from pathlib import Path

from ..config import get_settings
from ..schemas import SceneBlueprint
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


def load_blueprint(render_id: str) -> SceneBlueprint | None:
    path = render.RENDERS_DIR / render_id / "blueprint.json"
    if not path.exists():
        return None
    return SceneBlueprint.model_validate_json(path.read_text(encoding="utf-8"))


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


def resume_after_approval(record: dict) -> bool:
    """Fallback when in-process callback was lost (e.g. server restart)."""
    render_id = record.get("render_id")
    if not render_id:
        logger.error("Cannot resume — approval record has no render_id")
        return False
    logger.info("Resuming GDrive upload for render %s after approval %s", render_id, record.get("id"))
    higgsfield_video.schedule_gdrive_upload(render_id)
    return True
