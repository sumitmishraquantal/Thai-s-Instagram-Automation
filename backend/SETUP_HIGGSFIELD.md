# Higgsfield Setup Guide — Reel Studio (Windows)

Follow this top to bottom **once**. Do not start a video job until step 7 shows
ALL CHECKS PASSED. The verification script never spends credits unless you ask it to.

---

## 1 · Files in the right places

```
backend/
├── setup_higgsfield.py          ← NEW (verification script)
├── app/
│   ├── config.py                ← latest version (has video_provider + hf_image_ref_model)
│   └── services/
│       ├── higgsfield_video.py  ← latest version (provider branch)
│       └── higgsfield_mcp.py    ← NEW (Seedance 2.0 client)
```

Windows download gotcha: enable "File name extensions" in Explorer and confirm
no file is secretly `higgsfield_mcp.py.txt` or `config (1).py`.

After replacing files, delete the bytecode caches (stale caches caused one of
your earlier errors):

```powershell
cd backend
rd /s /q app\__pycache__
rd /s /q app\services\__pycache__
```

## 2 · Packages

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
pip install -U fastapi          # mcp upgrades starlette; fastapi must follow
```

## 3 · backend\.env — the source of your 404s

`.env` values OVERRIDE the code. Your last two failures were stale `.env` lines.
Delete every old `HF_*` line and make the Higgsfield section exactly this
(keeping your real key values):

```
# Provider: hf_mcp = Seedance 2.0 (OAuth) | hf_platform = Seedance v1 Lite (REST)
VIDEO_PROVIDER=hf_mcp
HF_MCP_URL=https://mcp.higgsfield.ai/mcp
HF_MCP_CALLBACK_PORT=3030

HF_API_KEY=your-key-id
HF_API_SECRET=your-secret
HF_IMAGE_MODEL=higgsfield-ai/soul/standard
HF_IMAGE_REF_MODEL=higgsfield-ai/soul/reference
HF_VIDEO_MODEL=bytedance/seedance/v1/lite/image-to-video
HF_VIDEO_RESOLUTION=720
HF_IMAGE_RESOLUTION=1080p
HF_ASPECT_RATIO=9:16
```

Rules that prevent repeats of the old errors:
- Settings are read ONCE at startup. After ANY `.env` edit: **Ctrl+C uvicorn and
  start it again.** `--reload` is not enough.
- Never put `bytedance/seedream/...` or `bytedance/seedance/v2/...` anywhere —
  those IDs do not exist on the platform.

## 4 · Character photos

Either upload via the UI panel ("Characters — Ron & Jason"), or copy directly:

```powershell
copy C:\path\to\ron.png  backend\assets\characters\host.png
copy C:\path\to\jason.png backend\assets\characters\guest.png
```

Front-facing, well-lit, shoulders-up photos give the best identity lock.

## 5 · Higgsfield account requirements

- **Platform API (uploads + v1 fallback):** Key ID + Secret from cloud.higgsfield.ai.
  API access requires a paid plan.
- **Seedance 2.0 via MCP:** sign in happens through OAuth in your browser on first
  run. Seedance 2.0 is gated on some plans/regions (Team / business-email
  verification) — confirm you can select Seedance 2.0 in the Higgsfield web UI
  with the SAME account you'll sign in with. If you can't use it in the web UI,
  the API can't either.

## 6 · One-time OAuth (only if VIDEO_PROVIDER=hf_mcp)

The first MCP call (from the verification script or a job) will:
1. Print a sign-in URL in the console (and try to open your browser)
2. You sign in to Higgsfield → browser shows "Higgsfield connected"
3. Tokens are cached at `backend\assets\hf_mcp_tokens.json` — never again

If auth ever breaks (401), delete that JSON file and repeat.
Port 3030 must be free during sign-in (change `HF_MCP_CALLBACK_PORT` if not).

## 7 · Verify EVERYTHING (free)

```powershell
cd backend
.venv\Scripts\activate
python setup_higgsfield.py
```

This checks: packages → resolved settings (catches stale .env) → ffmpeg →
photos → platform credentials (free upload round-trip) → all three model IDs
(free empty-body probes) → MCP connection + whether Seedance is on your plan,
and prints the real generate_video schema.

Fix anything red, restart uvicorn, re-run. When you see
**ALL CHECKS PASSED**, optionally prove generation end-to-end with one cheap image:

```powershell
python setup_higgsfield.py --smoke
```

## 8 · Run the pipeline

Only now: start uvicorn + vite, open the studio, and run
Script → Render → Blueprint → Generate videos.

## Troubleshooting quick table

| Symptom | Cause | Fix |
|---|---|---|
| `Model not found` | stale `.env` model line, or uvicorn not restarted | step 3, restart, re-verify |
| `cannot import name 'higgsfield_mcp'` | file missing/misplaced/renamed | step 1 |
| `'Settings' has no attribute video_provider` | old config.py or stale `__pycache__` | step 1 |
| Job uses text-to-image fallback | photos not found on disk | step 4 |
| MCP 401 / auth loop | stale tokens | delete `assets\hf_mcp_tokens.json`, redo step 6 |
| MCP works but Seedance rejected | plan doesn't include Seedance 2.0 | upgrade plan, or set `VIDEO_PROVIDER=hf_platform` meanwhile |