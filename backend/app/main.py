import logging

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import get_settings
from .schemas import PipelineRequest, PipelineResult
from .services import approval_gate, approvals, higgsfield_video, pipeline, render, scheduler


class RerunRequest(BaseModel):
    token: str
    scenes: list[int]


def _scene_number_from_name(name: str) -> int | None:
    """'scene_03.mp4' -> 3."""
    stem = name.split(".")[0]
    digits = stem.replace("scene_", "").strip()
    try:
        return int(digits)
    except ValueError:
        return None

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
    uploads the render to Google Drive."""
    result = approvals.resolve_by_token(token, action)
    if result["outcome"] == "invalid":
        return HTMLResponse(
            _approval_page("Invalid link", result.get("message", ""), "#dc2626"),
            status_code=400,
        )

    fired = approvals.fire_resume_if_approved(result)
    if not fired and result.get("status") == approvals.APPROVED:
        rec = result.get("record", {})
        fired = approval_gate.resume_after_approval(rec)
        if fired:
            logger.info("Resumed GDrive upload after approval %s", rec.get("id"))

    if result["outcome"] == "already":
        return HTMLResponse(_approval_page(
            f"Already {result['status']}",
            result["message"],
            "#6b7280",
        ))

    if result["status"] == approvals.APPROVED:
        sub = "Uploading to Google Drive." if fired else "Approved."
        return HTMLResponse(_approval_page("Approved", sub, "#16a34a"))
    return HTMLResponse(_approval_page(
        "Declined",
        "The request was declined. Nothing will be uploaded.",
        "#dc2626",
    ))


@app.get("/api/approvals/review", response_class=HTMLResponse)
async def approvals_review(token: str):
    """Interactive review page (works on the backend machine, like the approve links).
    Owners preview the thumbnail, script and each scene clip, tick any clip that came
    out wrong, and hit Regenerate — only the ticked scenes are re-rendered and a fresh
    approval email is sent. Approve/Decline are also here."""
    found = approvals.find_by_token(token)
    if not found:
        return HTMLResponse(_approval_page("Invalid link", "This review link is invalid or expired.", "#dc2626"),
                            status_code=400)
    record, _owner = found
    if record.get("status") != approvals.PENDING:
        return HTMLResponse(_approval_page(
            f"Already {record['status']}",
            f"This request was already {record['status']}. Clips can no longer be regenerated here.",
            "#6b7280"))
    # speaker per scene (for HOST/GUEST badges) — best effort from the blueprint
    speakers: dict[int, str] = {}
    try:
        bp = approval_gate.load_blueprint(record["render_id"])
        if bp:
            speakers = {s.scene_number: (s.speaker_on_camera or "").upper() for s in bp.scenes}
    except Exception:  # noqa: BLE001
        speakers = {}
    return HTMLResponse(_review_page(record, token, get_settings().approval_base_url.rstrip("/"), speakers))


@app.post("/api/approvals/rerun")
async def approvals_rerun(req: RerunRequest):
    """Regenerate the selected scene clips, then re-send the approval email."""
    found = approvals.find_by_token(req.token)
    if not found:
        raise HTTPException(400, "Invalid or expired link")
    record, _owner = found
    result = approval_gate.rerun_selected_scenes(record, req.scenes)
    return result


def _review_page(record: dict, token: str, base_url: str, speakers: dict | None = None) -> str:
    import html as _html
    speakers = speakers or {}
    p = record.get("payload", {})
    title = _html.escape(str(p.get("title", "Untitled")))
    render_id = _html.escape(str(record.get("render_id", "")))
    scene_clips = p.get("scene_clips", [])
    thumbnail_url = p.get("thumbnail_url")
    script_lines = p.get("script_lines", [])

    # thumbnail block
    thumb_html = ""
    if thumbnail_url:
        thumb_html = (
            '<div class="thumb"><img src="' + _html.escape(thumbnail_url) + '" alt="thumbnail"/>'
            '<span class="cap">Thumbnail · not regenerated here</span></div>'
        )

    # clip cards
    cards = []
    for clip in scene_clips:
        name = clip.get("name", "")
        n = _scene_number_from_name(name)
        if n is None:
            continue
        label = _html.escape(clip.get("label", name))
        url = _html.escape(clip.get("url", ""))
        spk = (speakers.get(n) or "").upper()
        badge = ""
        if spk in ("HOST", "GUEST"):
            badge = f'<span class="badge {spk.lower()}">{spk}</span>'
        cards.append(
            f'<div class="card" data-scene="{n}" role="button" tabindex="0" aria-pressed="false">'
            f'  <div class="vidwrap"><video preload="metadata" controls playsinline src="{url}"></video>'
            f'    <div class="tick" aria-hidden="true">✓</div></div>'
            f'  <div class="foot">'
            f'    <div class="meta">{label} {badge}</div>'
            f'    <span class="pill">Redo</span>'
            f'  </div>'
            f'</div>'
        )
    cards_html = "".join(cards) or '<p class="empty">No scene clips found for this reel.</p>'

    # collapsible script
    script_html = ""
    if script_lines:
        rows = []
        for ln in script_lines:
            sp = _html.escape(str(ln.get("speaker", "")).upper())
            tx = _html.escape(str(ln.get("text", "")))
            cls = "host" if sp == "HOST" else ("guest" if sp == "GUEST" else "")
            rows.append(f'<p class="sline"><span class="badge {cls}">{sp or "—"}</span> {tx}</p>')
        script_html = (
            '<details class="script"><summary>Show full script</summary>'
            '<div class="scriptbody">' + "".join(rows) + '</div></details>'
        )

    tmpl = r"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Review · __TITLE__</title>
<style>
  :root{
    --bg:#0d1014; --panel:#161b22; --panel2:#1b222c; --line:#2a323d; --text:#e6edf3;
    --muted:#8b97a6; --accent:#f5b53d; --accent2:#3da9fc; --green:#2ea043; --red:#e5484d;
    --sel:#f5b53d;
  }
  *{box-sizing:border-box}
  body{margin:0;background:radial-gradient(1200px 600px at 50% -10%,#1a212b,#0d1014 60%);
       color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       padding:0 0 120px}
  .wrap{max-width:1040px;margin:0 auto;padding:28px 20px}
  header{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:8px}
  h1{font-size:24px;margin:0 0 2px;letter-spacing:-.01em}
  .sub{color:var(--muted);font-size:14px;margin:0}
  .rid{margin-left:auto;color:var(--muted);font-size:12px;background:var(--panel);border:1px solid var(--line);
       padding:6px 10px;border-radius:999px;white-space:nowrap}
  .help{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:14px 0 20px;
        color:#cdd6e0;font-size:13.5px;line-height:1.5}
  .help b{color:var(--accent)}
  .thumb{display:inline-flex;flex-direction:column;gap:8px;margin:0 0 22px}
  .thumb img{height:280px;width:auto;max-width:100%;border-radius:12px;border:1px solid var(--line);display:block}
  .thumb .cap{color:var(--muted);font-size:12px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:16px}
  .card{background:var(--panel);border:2px solid var(--line);border-radius:14px;overflow:hidden;cursor:pointer;
        transition:border-color .15s,transform .1s,box-shadow .15s;outline:none}
  .card:hover{transform:translateY(-2px);box-shadow:0 8px 26px rgba(0,0,0,.35)}
  .card:focus-visible{border-color:var(--accent2)}
  .vidwrap{position:relative;background:#000;aspect-ratio:9/16}
  .vidwrap video{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block;background:#000}
  .tick{position:absolute;top:10px;right:10px;width:30px;height:30px;border-radius:50%;
        background:rgba(255,255,255,.12);border:2px solid rgba(255,255,255,.35);color:transparent;
        display:flex;align-items:center;justify-content:center;font-weight:900;font-size:15px;transition:.15s}
  .foot{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:11px 13px}
  .meta{font-weight:700;font-size:14px;display:flex;align-items:center;gap:8px}
  .badge{font-size:10.5px;font-weight:800;letter-spacing:.04em;padding:3px 8px;border-radius:999px;
         border:1px solid var(--line);color:var(--muted)}
  .badge.host{color:#9cd2ff;background:rgba(61,169,252,.12);border-color:rgba(61,169,252,.4)}
  .badge.guest{color:#ffd58a;background:rgba(245,181,61,.12);border-color:rgba(245,181,61,.4)}
  .pill{font-size:12px;font-weight:700;color:var(--muted);border:1px solid var(--line);border-radius:999px;
        padding:5px 12px;user-select:none}
  .card.selected{border-color:var(--sel);box-shadow:0 0 0 1px var(--sel) inset}
  .card.selected .tick{background:var(--sel);border-color:var(--sel);color:#0d1014}
  .card.selected .pill{color:#0d1014;background:var(--sel);border-color:var(--sel)}
  .empty{color:var(--muted)}
  details.script{margin-top:24px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:4px 16px}
  details.script summary{cursor:pointer;padding:12px 0;font-weight:700;color:#cdd6e0}
  .scriptbody{padding:4px 0 14px}
  .sline{margin:0 0 10px;font-size:14px;line-height:1.5;color:#cdd6e0}
  .bar{position:fixed;left:0;right:0;bottom:0;background:rgba(13,16,20,.86);backdrop-filter:blur(10px);
       border-top:1px solid var(--line);padding:14px 20px;z-index:50}
  .barin{max-width:1040px;margin:0 auto;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  .count{font-size:14px;color:var(--muted);margin-right:auto}
  .count b{color:var(--text)}
  button,.btn{font:inherit;border:0;border-radius:10px;padding:12px 20px;font-weight:800;cursor:pointer;
       text-decoration:none;display:inline-flex;align-items:center;gap:8px}
  .regen{background:var(--accent);color:#0d1014}
  .regen:disabled{background:#3a3f47;color:#7c8693;cursor:not-allowed}
  .approve{background:var(--green);color:#fff}
  .decline{background:transparent;color:var(--red);border:1px solid var(--red)}
  .toast{position:fixed;left:50%;transform:translateX(-50%);bottom:96px;max-width:560px;width:calc(100% - 40px);
         background:var(--panel2);border:1px solid var(--line);border-left:4px solid var(--accent2);
         border-radius:10px;padding:14px 16px;color:#e6edf3;font-size:14px;line-height:1.5;
         box-shadow:0 12px 40px rgba(0,0,0,.5);opacity:0;pointer-events:none;transition:.2s;z-index:60}
  .toast.show{opacity:1}
  .toast.ok{border-left-color:var(--green)} .toast.err{border-left-color:var(--red)}
</style></head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>Review clips</h1>
        <p class="sub">__TITLE__</p>
      </div>
      <span class="rid">render __RID__</span>
    </header>
    <div class="help">
      Tick any clip that came out wrong, then hit <b>Regenerate</b>. Only the ticked scenes are
      re-rendered in place — the people, studio, other clips and thumbnail stay exactly the same,
      and a fresh email arrives when it's done. Approve once everything looks right.
    </div>
    __THUMB__
    <div class="grid">__CARDS__</div>
    __SCRIPT__
  </div>

  <div class="bar"><div class="barin">
    <span class="count"><b id="n">0</b> selected</span>
    <button class="regen" id="regen" disabled>🔁 Regenerate selected</button>
    <a class="btn approve" href="__BASE__/api/approvals/act?token=__TOKEN__&action=approve">✓ Approve</a>
    <a class="btn decline" href="__BASE__/api/approvals/act?token=__TOKEN__&action=decline">✕ Decline</a>
  </div></div>
  <div class="toast" id="toast"></div>

<script>
  const sel = new Set();
  const nEl = document.getElementById('n');
  const regen = document.getElementById('regen');
  const toast = document.getElementById('toast');
  let done = false;

  function refresh(){
    nEl.textContent = sel.size;
    regen.disabled = sel.size === 0 || done;
    regen.textContent = sel.size ? ('🔁 Regenerate selected (' + sel.size + ')') : '🔁 Regenerate selected';
  }
  function toggle(card){
    if (done) return;
    const n = parseInt(card.dataset.scene, 10);
    if (sel.has(n)){ sel.delete(n); card.classList.remove('selected'); card.setAttribute('aria-pressed','false'); }
    else { sel.add(n); card.classList.add('selected'); card.setAttribute('aria-pressed','true'); }
    refresh();
  }
  document.querySelectorAll('.card').forEach(card => {
    // clicking the video itself should play, not toggle
    const v = card.querySelector('video');
    if (v) v.addEventListener('click', e => e.stopPropagation());
    card.addEventListener('click', () => toggle(card));
    card.addEventListener('keydown', e => { if (e.key === ' ' || e.key === 'Enter'){ e.preventDefault(); toggle(card); }});
  });
  function showToast(msg, kind){
    toast.className = 'toast show ' + (kind || '');
    toast.textContent = msg;
    if (kind === 'ok') setTimeout(() => toast.classList.remove('show'), 9000);
  }
  regen.addEventListener('click', async () => {
    if (sel.size === 0) return;
    const scenes = Array.from(sel).sort((a,b)=>a-b);
    if (!confirm('Regenerate ' + scenes.length + ' clip(s)? This spends Higgsfield credits for those scenes only.')) return;
    regen.disabled = true; regen.textContent = 'Regenerating…';
    showToast('Sending regeneration request…');
    try {
      const r = await fetch('__BASE__/api/approvals/rerun', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ token:'__TOKEN__', scenes })
      });
      const data = await r.json();
      if (data.status === 'regenerating'){
        done = true;
        document.querySelectorAll('.card.selected').forEach(c => c.style.opacity = '.5');
        showToast(data.message || 'Regenerating. A new email will arrive when ready.', 'ok');
      } else {
        showToast(data.message || JSON.stringify(data), 'err');
        regen.disabled = false; refresh();
      }
    } catch(e){
      showToast('Request failed: ' + e, 'err');
      regen.disabled = false; refresh();
    }
  });
  refresh();
</script>
</body></html>"""
    return (tmpl
            .replace("__TITLE__", title)
            .replace("__RID__", render_id)
            .replace("__THUMB__", thumb_html)
            .replace("__CARDS__", cards_html)
            .replace("__SCRIPT__", script_html)
            .replace("__BASE__", _html.escape(base_url))
            .replace("__TOKEN__", _html.escape(token)))


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