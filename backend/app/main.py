# import logging

# import httpx
# from fastapi import FastAPI, File, HTTPException, Request, UploadFile
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.responses import JSONResponse, HTMLResponse
# from fastapi.staticfiles import StaticFiles

# from .config import get_settings
# from .schemas import (
#     RenderRequest, RenderResponse, VideoGenRequest,
#     ResearchRequest, ResearchResponse,
#     SceneBlueprint, ScenePlanRequest,
#     ScriptRequest, ScriptPackage,
#     TTSPreviewRequest, TTSPreviewResponse, VoiceInfo,
# )
# from .services import (elevenlabs_client, higgsfield_video, llm, render,
#                        approvals, approval_email, scheduler)

# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger("reel-studio")

# app = FastAPI(title="Reel Studio API", version="1.1.0")

# @app.on_event("startup")
# def _on_startup():
#     # The scheduled trigger runs a workflow up to the approval gate. For now it
#     # logs intent; full auto-run wiring (research->script->render->blueprint) can
#     # be added per workflow. Podcast auto-run is stubbed to the existing pipeline.
#     def _runner(workflow: str, topic: str):
#         logger.info("AUTO-TRIGGER fired: workflow=%s topic=%r — "
#                     "(hook your research->render->blueprint->approval chain here)",
#                     workflow, topic)
#     scheduler.register_runner(_runner)
#     scheduler.start_scheduler()

# app.mount("/renders", StaticFiles(directory=render.RENDERS_DIR), name="renders")
# app.mount("/assets", StaticFiles(directory=higgsfield_video.ASSETS_DIR), name="assets")

# settings = get_settings()
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=settings.cors_origin_list,
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )


# @app.exception_handler(httpx.HTTPStatusError)
# async def upstream_error_handler(request: Request, exc: httpx.HTTPStatusError):
#     logger.error("Upstream API error: %s — %s", exc.response.status_code, exc.response.text[:500])
#     return JSONResponse(
#         status_code=502,
#         content={"detail": f"Upstream service error ({exc.response.status_code}). Check API keys and quota."},
#     )


# @app.get("/api/health")
# async def health():
#     return {"status": "ok"}


# @app.post("/api/research", response_model=ResearchResponse)
# async def research(req: ResearchRequest):
#     topics = await llm.fetch_trending_topics(req.category)
#     return ResearchResponse(topics=topics)


# @app.post("/api/script", response_model=ScriptPackage)
# async def script(req: ScriptRequest):
#     pkg = await llm.generate_script(req.category, req.seed_topic, req.user_draft)
#     if pkg is None:
#         raise HTTPException(500, "Script generation failed")
#     return pkg


# @app.get("/api/voices", response_model=list[VoiceInfo])
# async def voices():
#     return await elevenlabs_client.list_voices()


# @app.post("/api/tts-preview", response_model=TTSPreviewResponse)
# async def tts_preview(req: TTSPreviewRequest):
#     if not req.lines:
#         raise HTTPException(400, "No script lines provided")
#     if len(req.lines) > 12:
#         raise HTTPException(400, "Too many lines for a preview (max 12)")
#     clips = await elevenlabs_client.synthesize_preview(
#         req.lines, req.host_voice_id, req.guest_voice_id
#     )
#     return TTSPreviewResponse(clips=clips)


# @app.post("/api/render", response_model=RenderResponse)
# async def render_audio(req: RenderRequest):
#     if not req.lines:
#         raise HTTPException(400, "No script lines provided")
#     if len(req.lines) > 12:
#         raise HTTPException(400, "Too many lines (max 12)")
#     return await render.render_final_audio(
#         req.title, req.lines, req.host_voice_id, req.guest_voice_id
#     )


# @app.post("/api/scene-plan", response_model=SceneBlueprint)
# async def scene_plan(req: ScenePlanRequest):
#     if not req.lines:
#         raise HTTPException(400, "No script lines provided")
#     return await llm.generate_scene_plan(req.title, req.lines, req.segments)




# def _approval_payload(render_id: str, blueprint) -> dict:
#     """Assemble what the owners review BEFORE video generation:
#       - the complete script (from step 4), and
#       - the single full combined audio file (from step 5).
#     No scene blueprint and no segmented audios are included."""
#     base = render.RENDERS_DIR / render_id
#     import json as _json

#     # 1) Full script: prefer the saved <Title>.script.json (the real script from
#     #    the script step). Fall back to segments.json only if that's missing.
#     script_lines = []
#     title = getattr(blueprint, "title", None) or render_id
#     script_files = list(base.glob("*.script.json"))
#     if script_files:
#         try:
#             data = _json.loads(script_files[0].read_text(encoding="utf-8"))
#             title = data.get("title", title)
#             script_lines = [{"speaker": l.get("speaker", ""), "text": l.get("text", "")}
#                             for l in data.get("lines", [])]
#         except Exception:  # noqa: BLE001
#             script_lines = []
#     if not script_lines:
#         segf = base / "segments.json"
#         if segf.exists():
#             segs = _json.loads(segf.read_text())
#             script_lines = [{"speaker": s.get("speaker", ""), "text": s.get("text", "")} for s in segs]

#     # 2) Single combined audio: the title-named .mp3 (NOT the segment_*.mp3 clips).
#     audio_url = None
#     mp3s = [p for p in base.glob("*.mp3") if not p.name.lower().startswith("segment")]
#     if mp3s:
#         # prefer the largest (the full reel) if several exist
#         mp3s.sort(key=lambda p: p.stat().st_size, reverse=True)
#         audio_url = f"{get_settings().approval_base_url}/renders/{render_id}/{mp3s[0].name}"

#     return {
#         "title": title,
#         "audio_url": audio_url,
#         "script_lines": script_lines,
#     }


# def _start_video_generation(render_id: str, blueprint) -> str:
#     job_id = higgsfield_video.start_job(render_id, blueprint, False, False)
#     logger.info("Video generation started (job %s) for render %s", job_id, render_id)
#     return job_id


# @app.post("/api/generate-videos")
# async def generate_videos(req: VideoGenRequest):
#     base = render.RENDERS_DIR / req.render_id
#     if not (base / "segments.json").exists():
#         raise HTTPException(400, f"Unknown render_id '{req.render_id}' — render the audio first")
#     if not req.blueprint.scenes:
#         raise HTTPException(400, "Blueprint has no scenes")
#     s = get_settings()
#     provider = s.video_provider.lower()

#     # APPROVAL GATE: if enabled (and not explicitly bypassed), don't generate yet.
#     # Create a pending approval, email the owners, and only resume on first approve.
#     if s.require_approval and not req.force_regen_scenes:
#         owners = [o.strip() for o in s.owner_emails.split(",") if o.strip()]
#         if not owners:
#             raise HTTPException(400, "REQUIRE_APPROVAL is on but OWNER_EMAILS is empty in .env")
#         record = approvals.create_request(
#             workflow="podcast", render_id=req.render_id, owners=owners,
#             payload=_approval_payload(req.render_id, req.blueprint),
#         )
#         # capture the blueprint so the resume can use it
#         bp = req.blueprint
#         approvals.register_resume(record["id"], lambda rec: _start_video_generation(rec["render_id"], bp))
#         email_report = approval_email.send_approval_emails(record)
#         return {"status": "awaiting_approval", "approval_id": record["id"],
#                 "owners": owners, "email": email_report,
#                 "message": "Sent for approval. Video generation starts when the first owner approves."}

#     job_id = higgsfield_video.start_job(req.render_id, req.blueprint,
#                                         req.force_regen_identity, req.force_regen_scenes)
#     return {"job_id": job_id, "provider": provider}


# @app.get("/api/approvals/act", response_class=HTMLResponse)
# async def approvals_act(token: str, action: str):
#     """Owners' email links land here. First valid action resolves it and (on
#     approve) fires video generation. Later clicks just report the resolved state."""
#     result = approvals.resolve_by_token(token, action)
#     if result["outcome"] == "invalid":
#         return HTMLResponse(_approval_page("Invalid link", result.get("message", ""), "#dc2626"), status_code=400)

#     fired = approvals.fire_resume_if_approved(result)
#     rec = result.get("record", {})
#     if result["outcome"] == "already":
#         return HTMLResponse(_approval_page(
#             f"Already {result['status']}",
#             result["message"], "#6b7280"))
#     # freshly resolved
#     if result["status"] == approvals.APPROVED:
#         sub = "Video generation has started." if fired else "Approved."
#         return HTMLResponse(_approval_page("Approved ✓", sub, "#16a34a"))
#     return HTMLResponse(_approval_page("Declined ✕", "The request was declined. Nothing will be generated.", "#dc2626"))


# @app.get("/api/approvals/{approval_id}")
# async def approval_status(approval_id: str):
#     rec = approvals.get(approval_id)
#     if not rec:
#         raise HTTPException(404, "Unknown approval id")
#     # don't leak tokens
#     safe = {k: v for k, v in rec.items() if k != "owner_tokens"}
#     return safe


# def _approval_page(title: str, subtitle: str, color: str) -> str:
#     return f"""<!doctype html><html><body style="font-family:Arial,sans-serif;background:#f4f5f7;
#       display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
#       <div style="background:#fff;border-radius:14px;padding:40px 48px;text-align:center;
#                   border:1px solid #e5e7eb;max-width:460px">
#         <h1 style="color:{color};margin:0 0 10px">{title}</h1>
#         <p style="color:#444;margin:0">{subtitle}</p>
#       </div></body></html>"""


# @app.get("/api/schedule")
# async def schedule_status():
#     s = get_settings()
#     return {"enabled": s.schedule_enabled, "cron": s.schedule_cron,
#             "workflow": s.schedule_workflow, "next_run": scheduler.next_run_time()}


# @app.post("/api/retry-videos")
# async def retry_videos(req: VideoGenRequest):
#     """Resume video generation for the SAME render. Scenes whose clips already
#     exist on disk are reused (no regen, no credits); only missing/failed scenes
#     are generated. The script/audio/blueprint stay identical."""
#     base = render.RENDERS_DIR / req.render_id
#     if not (base / "segments.json").exists():
#         raise HTTPException(400, f"Unknown render_id '{req.render_id}'")
#     if not req.blueprint.scenes:
#         raise HTTPException(400, "Blueprint has no scenes")
#     # retry never force-regenerates existing scenes unless explicitly asked
#     job_id = higgsfield_video.start_job(req.render_id, req.blueprint,
#                                         force_regen_identity=False,
#                                         force_regen_scenes=req.force_regen_scenes)
#     return {"job_id": job_id, "provider": get_settings().video_provider.lower(), "resumed": True}


# @app.get("/api/video-jobs/{job_id}")
# async def video_job_status(job_id: str):
#     job = higgsfield_video.JOBS.get(job_id)
#     if not job:
#         raise HTTPException(404, "Unknown job id")
#     return job


# @app.get("/api/characters")
# async def characters():
#     return higgsfield_video.character_status()


# @app.post("/api/characters/{role}")
# async def upload_character(role: str, file: UploadFile = File(...)):
#     if role not in ("host", "guest"):
#         raise HTTPException(400, "role must be 'host' or 'guest'")
#     if file.content_type not in ("image/png", "image/jpeg", "image/webp"):
#         raise HTTPException(400, "Upload a PNG, JPEG, or WEBP image")
#     data = await file.read()
#     if len(data) > 15 * 1024 * 1024:
#         raise HTTPException(400, "Image too large (max 15MB)")
#     higgsfield_video.save_character(role, data, file.content_type)
#     return higgsfield_video.character_status()


# @app.get("/api/identity-cache")
# async def identity_cache():
#     return higgsfield_video.identity_cache_status()


# @app.delete("/api/identity-cache")
# async def clear_identity_cache(role: str | None = None):
#     return {"cleared": higgsfield_video.clear_identity_cache(role)}



import logging

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .schemas import (
    RenderRequest, RenderResponse, VideoGenRequest,
    ResearchRequest, ResearchResponse,
    SceneBlueprint, ScenePlanRequest,
    ScriptRequest, ScriptPackage,
    TTSPreviewRequest, TTSPreviewResponse, VoiceInfo,
)
from .services import (elevenlabs_client, higgsfield_video, llm, render,
                       approvals, approval_email, scheduler)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reel-studio")

app = FastAPI(title="Reel Studio API", version="1.1.0")

@app.on_event("startup")
def _on_startup():
    # The scheduled trigger runs a workflow up to the approval gate. For now it
    # logs intent; full auto-run wiring (research->script->render->blueprint) can
    # be added per workflow. Podcast auto-run is stubbed to the existing pipeline.
    def _runner(workflow: str, topic: str):
        logger.info("AUTO-TRIGGER fired: workflow=%s topic=%r — "
                    "(hook your research->render->blueprint->approval chain here)",
                    workflow, topic)
    scheduler.register_runner(_runner)
    scheduler.start_scheduler()

app.mount("/renders", StaticFiles(directory=render.RENDERS_DIR), name="renders")
app.mount("/assets", StaticFiles(directory=higgsfield_video.ASSETS_DIR), name="assets")

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.post("/api/research", response_model=ResearchResponse)
async def research(req: ResearchRequest):
    topics = await llm.fetch_trending_topics(req.category)
    return ResearchResponse(topics=topics)


@app.post("/api/script", response_model=ScriptPackage)
async def script(req: ScriptRequest):
    pkg = await llm.generate_script(req.category, req.seed_topic, req.user_draft)
    if pkg is None:
        raise HTTPException(500, "Script generation failed")
    return pkg


@app.get("/api/voices", response_model=list[VoiceInfo])
async def voices():
    return await elevenlabs_client.list_voices()


@app.post("/api/tts-preview", response_model=TTSPreviewResponse)
async def tts_preview(req: TTSPreviewRequest):
    if not req.lines:
        raise HTTPException(400, "No script lines provided")
    if len(req.lines) > 12:
        raise HTTPException(400, "Too many lines for a preview (max 12)")
    clips = await elevenlabs_client.synthesize_preview(
        req.lines, req.host_voice_id, req.guest_voice_id
    )
    return TTSPreviewResponse(clips=clips)


@app.post("/api/render", response_model=RenderResponse)
async def render_audio(req: RenderRequest):
    if not req.lines:
        raise HTTPException(400, "No script lines provided")
    if len(req.lines) > 12:
        raise HTTPException(400, "Too many lines (max 12)")
    return await render.render_final_audio(
        req.title, req.lines, req.host_voice_id, req.guest_voice_id
    )


@app.post("/api/scene-plan", response_model=SceneBlueprint)
async def scene_plan(req: ScenePlanRequest):
    if not req.lines:
        raise HTTPException(400, "No script lines provided")
    return await llm.generate_scene_plan(req.title, req.lines, req.segments)




def _approval_payload(render_id: str, blueprint) -> dict:
    """Assemble what the owners review BEFORE video generation:
      - the complete script (from step 4), and
      - the single full combined audio file (from step 5).
    No scene blueprint and no segmented audios are included."""
    base = render.RENDERS_DIR / render_id
    import json as _json

    # 1) Full script: prefer the saved <Title>.script.json (the real script from
    #    the script step). Fall back to segments.json only if that's missing.
    script_lines = []
    title = getattr(blueprint, "title", None) or render_id
    script_files = list(base.glob("*.script.json"))
    if script_files:
        try:
            data = _json.loads(script_files[0].read_text(encoding="utf-8"))
            title = data.get("title", title)
            script_lines = [{"speaker": l.get("speaker", ""), "text": l.get("text", "")}
                            for l in data.get("lines", [])]
        except Exception:  # noqa: BLE001
            script_lines = []
    if not script_lines:
        segf = base / "segments.json"
        if segf.exists():
            segs = _json.loads(segf.read_text())
            script_lines = [{"speaker": s.get("speaker", ""), "text": s.get("text", "")} for s in segs]

    # 2) Single combined audio: the title-named .mp3 (NOT the segment_*.mp3 clips).
    audio_url = None
    mp3s = [p for p in base.glob("*.mp3") if not p.name.lower().startswith("segment")]
    if mp3s:
        # prefer the largest (the full reel) if several exist
        mp3s.sort(key=lambda p: p.stat().st_size, reverse=True)
        audio_url = f"{get_settings().approval_base_url}/renders/{render_id}/{mp3s[0].name}"

    return {
        "title": title,
        "audio_url": audio_url,
        "script_lines": script_lines,
    }


def _start_video_generation(render_id: str, blueprint) -> str:
    job_id = higgsfield_video.start_job(render_id, blueprint, False, False)
    logger.info("Video generation started (job %s) for render %s", job_id, render_id)
    return job_id


@app.post("/api/generate-videos")
async def generate_videos(req: VideoGenRequest):
    base = render.RENDERS_DIR / req.render_id
    if not (base / "segments.json").exists():
        raise HTTPException(400, f"Unknown render_id '{req.render_id}' — render the audio first")
    if not req.blueprint.scenes:
        raise HTTPException(400, "Blueprint has no scenes")
    s = get_settings()
    provider = s.video_provider.lower()

    # APPROVAL GATE: if enabled (and not explicitly bypassed), don't generate yet.
    # Create a pending approval, email the owners, and only resume on first approve.
    if s.require_approval and not req.force_regen_scenes:
        owners = [o.strip() for o in s.owner_emails.split(",") if o.strip()]
        if not owners:
            raise HTTPException(400, "REQUIRE_APPROVAL is on but OWNER_EMAILS is empty in .env")
        record = approvals.create_request(
            workflow="podcast", render_id=req.render_id, owners=owners,
            payload=_approval_payload(req.render_id, req.blueprint),
        )
        # capture the blueprint so the resume can use it
        bp = req.blueprint
        approvals.register_resume(record["id"], lambda rec: _start_video_generation(rec["render_id"], bp))
        email_report = approval_email.send_approval_emails(record)
        return {"status": "awaiting_approval", "approval_id": record["id"],
                "owners": owners, "email": email_report,
                "message": "Sent for approval. Video generation starts when the first owner approves."}

    job_id = higgsfield_video.start_job(req.render_id, req.blueprint,
                                        req.force_regen_identity, req.force_regen_scenes)
    return {"job_id": job_id, "provider": provider}


@app.get("/api/approvals/act", response_class=HTMLResponse)
async def approvals_act(token: str, action: str):
    """Owners' email links land here. First valid action resolves it and (on
    approve) fires video generation. Later clicks just report the resolved state."""
    result = approvals.resolve_by_token(token, action)
    if result["outcome"] == "invalid":
        return HTMLResponse(_approval_page("Invalid link", result.get("message", ""), "#dc2626"), status_code=400)

    fired = approvals.fire_resume_if_approved(result)
    rec = result.get("record", {})
    if result["outcome"] == "already":
        return HTMLResponse(_approval_page(
            f"Already {result['status']}",
            result["message"], "#6b7280"))
    # freshly resolved
    if result["status"] == approvals.APPROVED:
        sub = "Video generation has started." if fired else "Approved."
        return HTMLResponse(_approval_page("Approved ✓", sub, "#16a34a"))
    return HTMLResponse(_approval_page("Declined ✕", "The request was declined. Nothing will be generated.", "#dc2626"))


@app.get("/api/approvals/{approval_id}")
async def approval_status(approval_id: str):
    rec = approvals.get(approval_id)
    if not rec:
        raise HTTPException(404, "Unknown approval id")
    # don't leak tokens
    safe = {k: v for k, v in rec.items() if k != "owner_tokens"}
    return safe


def _approval_page(title: str, subtitle: str, color: str) -> str:
    return f"""<!doctype html><html><body style="font-family:Arial,sans-serif;background:#f4f5f7;
      display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
      <div style="background:#fff;border-radius:14px;padding:40px 48px;text-align:center;
                  border:1px solid #e5e7eb;max-width:460px">
        <h1 style="color:{color};margin:0 0 10px">{title}</h1>
        <p style="color:#444;margin:0">{subtitle}</p>
      </div></body></html>"""


@app.get("/api/schedule")
async def schedule_status():
    s = get_settings()
    return {"enabled": s.schedule_enabled, "cron": s.schedule_cron,
            "workflow": s.schedule_workflow, "next_run": scheduler.next_run_time()}


@app.post("/api/retry-videos")
async def retry_videos(req: VideoGenRequest):
    """Resume video generation for the SAME render. Scenes whose clips already
    exist on disk are reused (no regen, no credits); only missing/failed scenes
    are generated. The script/audio/blueprint stay identical."""
    base = render.RENDERS_DIR / req.render_id
    if not (base / "segments.json").exists():
        raise HTTPException(400, f"Unknown render_id '{req.render_id}'")
    if not req.blueprint.scenes:
        raise HTTPException(400, "Blueprint has no scenes")
    # retry never force-regenerates existing scenes unless explicitly asked
    job_id = higgsfield_video.start_job(req.render_id, req.blueprint,
                                        force_regen_identity=False,
                                        force_regen_scenes=req.force_regen_scenes)
    return {"job_id": job_id, "provider": get_settings().video_provider.lower(), "resumed": True}


@app.get("/api/video-jobs/{job_id}")
async def video_job_status(job_id: str):
    job = higgsfield_video.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job id")
    return job


@app.get("/api/characters")
async def characters():
    return higgsfield_video.character_status()


@app.post("/api/characters/{role}")
async def upload_character(role: str, file: UploadFile = File(...)):
    if role not in ("host", "guest"):
        raise HTTPException(400, "role must be 'host' or 'guest'")
    if file.content_type not in ("image/png", "image/jpeg", "image/webp"):
        raise HTTPException(400, "Upload a PNG, JPEG, or WEBP image")
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "Image too large (max 15MB)")
    higgsfield_video.save_character(role, data, file.content_type)
    return higgsfield_video.character_status()


@app.get("/api/identity-cache")
async def identity_cache():
    return higgsfield_video.identity_cache_status()


@app.delete("/api/identity-cache")
async def clear_identity_cache(role: str | None = None):
    return {"cleared": higgsfield_video.clear_identity_cache(role)}