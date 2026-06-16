"""Approval gate before Higgsfield video generation."""
import json
import logging

from ..config import get_settings
from ..schemas import SceneBlueprint
from . import approval_email, approvals, higgsfield_video, render

logger = logging.getLogger(__name__)


def build_approval_payload(render_id: str, blueprint: SceneBlueprint) -> dict:
    """What owners review: full script + combined audio URL (not the blueprint)."""
    base = render.RENDERS_DIR / render_id
    script_lines: list[dict] = []
    title = blueprint.title or render_id

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

    audio_url = None
    mp3s = [p for p in base.glob("*.mp3") if not p.name.lower().startswith("segment")]
    if mp3s:
        mp3s.sort(key=lambda p: p.stat().st_size, reverse=True)
        audio_url = f"{get_settings().approval_base_url}/renders/{render_id}/{mp3s[0].name}"

    return {"title": title, "audio_url": audio_url, "script_lines": script_lines}


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


def gate_video_generation(render_id: str, blueprint: SceneBlueprint) -> dict:
    """Start video immediately, or create approval + send email first."""
    s = get_settings()
    if not s.require_approval:
        job_id = start_video_generation(render_id, blueprint)
        return {"status": "video_started", "job_id": job_id}

    owners = [o.strip() for o in s.owner_emails.split(",") if o.strip()]
    if not owners:
        raise ValueError("REQUIRE_APPROVAL is on but OWNER_EMAILS is empty in .env")

    save_blueprint(render_id, blueprint)
    record = approvals.create_request(
        workflow="podcast",
        render_id=render_id,
        owners=owners,
        payload=build_approval_payload(render_id, blueprint),
    )

    bp = blueprint

    def _resume(rec: dict):
        loaded = load_blueprint(rec["render_id"]) or bp
        start_video_generation(rec["render_id"], loaded)

    approvals.register_resume(record["id"], _resume)
    email_report = approval_email.send_approval_emails(record)
    return {
        "status": "awaiting_approval",
        "approval_id": record["id"],
        "owners": owners,
        "email": email_report,
        "message": "Sent for approval. Video generation starts when the first owner approves.",
    }


def resume_after_approval(record: dict) -> str | None:
    """Fallback when in-process callback was lost (e.g. server restart)."""
    blueprint = load_blueprint(record["render_id"])
    if blueprint is None:
        logger.error("Cannot resume render %s — blueprint.json missing", record["render_id"])
        return None
    return start_video_generation(record["render_id"], blueprint)
