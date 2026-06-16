import logging

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .schemas import PipelineRequest, PipelineResult
from .services import approval_gate, approvals, higgsfield_video, pipeline, render, scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reel-studio")

app = FastAPI(title="Reel Studio API", version="2.0.0")

app.mount("/renders", StaticFiles(directory=render.RENDERS_DIR), name="renders")
app.mount("/assets", StaticFiles(directory=higgsfield_video.ASSETS_DIR), name="assets")


@app.on_event("startup")
def _on_startup():
    import asyncio

    def _runner(workflow: str, topic: str):
        logger.info("Scheduled run starting: workflow=%s category=%r", workflow, topic or "(auto)")
        asyncio.run(pipeline.run_podcast_pipeline(category=topic or None))

    scheduler.register_runner(_runner)
    scheduler.start_scheduler()


@app.exception_handler(httpx.HTTPStatusError)
async def upstream_error_handler(request: Request, exc: httpx.HTTPStatusError):
    logger.error("Upstream API error: %s — %s", exc.response.status_code, exc.response.text[:500])
    return JSONResponse(
        status_code=502,
        content={"detail": f"Upstream service error ({exc.response.status_code}). Check API keys and quota."},
    )


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/run-pipeline", response_model=PipelineResult)
async def run_pipeline(req: PipelineRequest = PipelineRequest()):
    try:
        return await pipeline.run_podcast_pipeline(category=req.category)
    except Exception as e:  # noqa: BLE001
        logger.exception("Pipeline failed")
        raise HTTPException(500, str(e)) from e


@app.get("/api/approvals/act", response_class=HTMLResponse)
async def approvals_act(token: str, action: str):
    """Owners' email links land here. First valid action resolves and (on approve)
    starts video generation."""
    result = approvals.resolve_by_token(token, action)
    if result["outcome"] == "invalid":
        return HTMLResponse(
            _approval_page("Invalid link", result.get("message", ""), "#dc2626"),
            status_code=400,
        )

    fired = approvals.fire_resume_if_approved(result)
    if not fired and result.get("status") == approvals.APPROVED:
        rec = result.get("record", {})
        job_id = approval_gate.resume_after_approval(rec)
        fired = bool(job_id)
        if job_id:
            logger.info("Resumed video job %s after approval %s", job_id, rec.get("id"))

    if result["outcome"] == "already":
        return HTMLResponse(_approval_page(
            f"Already {result['status']}",
            result["message"],
            "#6b7280",
        ))

    if result["status"] == approvals.APPROVED:
        sub = "Video generation has started." if fired else "Approved."
        return HTMLResponse(_approval_page("Approved", sub, "#16a34a"))
    return HTMLResponse(_approval_page(
        "Declined",
        "The request was declined. Nothing will be generated.",
        "#dc2626",
    ))


@app.get("/api/approvals/{approval_id}")
async def approval_status(approval_id: str):
    rec = approvals.get(approval_id)
    if not rec:
        raise HTTPException(404, "Unknown approval id")
    return {k: v for k, v in rec.items() if k != "owner_tokens"}


def _approval_page(title: str, subtitle: str, color: str) -> str:
    return f"""<!doctype html><html><body style="font-family:Arial,sans-serif;background:#f4f5f7;
      display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
      <div style="background:#fff;border-radius:14px;padding:40px 48px;text-align:center;
                  border:1px solid #e5e7eb;max-width:460px">
        <h1 style="color:{color};margin:0 0 10px">{title}</h1>
        <p style="color:#444;margin:0">{subtitle}</p>
      </div></body></html>"""


@app.get("/api/video-jobs/{job_id}")
async def video_job_status(job_id: str):
    job = higgsfield_video.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job id")
    return job


@app.get("/api/schedule")
async def schedule_status():
    s = get_settings()
    return {
        "enabled": s.schedule_enabled,
        "cron": s.schedule_cron,
        "workflow": s.schedule_workflow,
        "next_run": scheduler.next_run_time(),
    }


@app.get("/api/characters")
async def characters():
    return higgsfield_video.character_status()


@app.get("/api/identity-cache")
async def identity_cache():
    return higgsfield_video.identity_cache_status()


@app.delete("/api/identity-cache")
async def clear_identity_cache(role: str | None = None):
    return {"cleared": higgsfield_video.clear_identity_cache(role)}
