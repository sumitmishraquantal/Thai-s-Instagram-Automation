# Reel Studio — Automated Instagram Reel Pipeline

Backend automation for podcast-style Instagram Reels. It researches a trending topic,
writes a short Q&A script, synthesizes host/guest voices, renders and segments the
audio, builds a per-scene blueprint, generates the studio identity images and the
per-scene talking-head video clips, merges them into a vertical reel, makes a clickbait
thumbnail, gates publishing behind an email approval (with a per-clip **review &
regenerate** page), and uploads the raw clips to Google Drive for editing.

It is backend-only (FastAPI). Reviewers interact through the approval email and the
browser pages it links to.

---

## Stack

- **API** — FastAPI (Python 3.12)
- **LLM** — Groq (`llama-3.3-70b-versatile`) for category/topic/script/blueprint
- **Research** — YouTube Data API v3 + Serper (web/news)
- **Voice** — ElevenLabs (`eleven_v3`)
- **Audio/Video assembly** — ffmpeg (v8.x)
- **Image + video generation** — Higgsfield via MCP (GPT-Image-2 for stills, Seedance 2.0 for clips)
- **Delivery** — rclone → Google Drive
- **Deploy** — Docker Compose (backend on port 8000)

---

## End-to-end flow

```
Research → Topic → Script → Audio (+segments) → Scene blueprint
        → Identity images (host/guest) → Per-scene video clips → Merge → Thumbnail
        → Approval email  ──(review / regenerate clips)──►  Approve
        → Upload raw clips to Google Drive
```

1. **Category** — `DEFAULT_CATEGORY` env, or Groq auto-selects.
2. **Live research** — YouTube (recent popular videos) + Serper (web/news); Groq distills topics.
3. **Topic** — Groq picks the best topic + angle.
4. **Script** — Groq writes a ~30–35s HOST/GUEST Q&A script.
5. **Audio** — ElevenLabs synthesizes each line; ffmpeg renders and splits into per-scene
   segments (`segments.json` + `segment_NN.wav`). Long guest answers are split into
   ~10–12s segments on natural silences.
6. **Blueprint** — Groq produces a `SceneBlueprint` (one `Scene` per segment: speaker,
   timing, camera, expression, etc.). Saved as `blueprint.json`.
7. **Identity images** — one studio image per role (host/guest, plus a `both` two-shot
   used only for the thumbnail), generated once from your reference photos and locked
   into the identity cache for reuse.
8. **Per-scene clips** — each segment becomes one Seedance clip (lip-synced to that
   segment's audio), using the locked identity as the reference. Saved as
   `video/scene_NN.mp4`.
9. **Merge** — clips are concatenated into `video/merged_reel.mp4`.
10. **Thumbnail** — a clickbait-style cover (`thumbnail.png`).
11. **Approval gate** (if `REQUIRE_APPROVAL=true`) — owners get an email to review the
    thumbnail, full script, and every clip, then Approve / Decline / **Regenerate**.
12. **Delivery** — on approval, all raw clips upload to Google Drive in one rclone
    command, renamed `RawClip1.mp4, RawClip2.mp4, …` for the downstream editing routine.

Primary output: `backend/renders/<render_id>/video/merged_reel.mp4`
Raw clips for editing: Google Drive → `RCLONE_REMOTE` (default `gdrive:reel-projects/TEST`).

---

## Approval, review & per-clip regeneration

When a render finishes and approval is required, each owner receives an email with the
script, thumbnail, and clips, plus three actions: **Approve**, **Decline**, and
**Review & regenerate clips**.

**Why a review page (and not buttons in the email):** email clients strip JavaScript and
forms, so interactive checkboxes can't work inside the email. The "Review & regenerate"
button opens a backend-served page (`/api/approvals/review`) where the controls actually
function. It runs on the backend machine, like the approve links.

**The review page** shows each clip as a card (with HOST/GUEST badge), the thumbnail, and
the full script. Tick any clip that came out wrong and press **Regenerate** — only those
scenes are re-rendered; a fresh approval email arrives when they're done. The approval is
not consumed by reviewing or regenerating.

**Everything is locked during a per-clip regeneration.** A rerun re-renders only the
selected scene(s); the studio canon (`visual_canon.json`), the identity AI images, the
thumbnail, and all non-selected clips are reused exactly — zero re-generation of any of
them. This keeps the people, studio, look, and thumbnail identical across reruns.

A regeneration refuses cleanly (deleting nothing) if the render lacks the context it
needs (`blueprint.json` / `segments.json`).

---

## Google Drive delivery (rclone)

On approval (or, when `REQUIRE_APPROVAL=false`, right after merge) the raw scene clips
are pushed to Google Drive through the same rclone remote your editing routine reads.

- Uploads happen **once**, after the whole reel is done — never clip-by-clip.
- Clips are staged flat and renamed in scene order: `scene_01.mp4 → RawClip1.mp4`,
  `scene_02.mp4 → RawClip2.mp4`, … so the editor merges them in sequence.
- On a failed upload the reel is still marked complete and the clips are kept locally;
  locals are deleted only after a confirmed upload, and only if you enable that option.

Configure with `UPLOAD_TO_GDRIVE`, `RCLONE_REMOTE`, `RCLONE_EXE`, `RCLONE_CONFIG`,
`GDRIVE_CLIP_PREFIX`, `GDRIVE_SUBFOLDER_PER_REEL`, `GDRIVE_DELETE_LOCAL_AFTER_UPLOAD`.

---

## Project structure

```
backend/
  app/
    main.py                  FastAPI app + routes + approval/review HTML pages
    config.py                Settings (read from .env)
    schemas.py               Pydantic models (Scene, SceneBlueprint, PipelineResult, …)
    services/
      pipeline.py            Orchestrates the whole run
      llm.py                 Groq: category, research distill, topic, script, blueprint, canon
      research_sources.py    YouTube + Serper research
      elevenlabs_client.py   TTS
      render.py              Audio render + silence-aware segmentation
      higgsfield_mcp.py      Higgsfield MCP client (image/video gen, uploads, polling)
      higgsfield_video.py    Identity images, per-scene clips, merge, thumbnail, GDrive upload
      director_skills.py     Loads prompt "skills" (Seedance / GPT-Image director prompts)
      image_prep.py          Normalizes reference photos
      approval_gate.py       Builds approval payload, sends email, GDrive resume, rerun orchestration
      approvals.py           Approval records + tokens (pending/approved/declined)
      approval_email.py      Approval email HTML (with the review link)
      scheduler.py           Optional cron-driven autonomous runs
  assets/
    characters/              Your reference photos: host.png, guest.png (+ optional both.png)
    identity_cache/          Locked identity images/refs (reused across runs)
  renders/<render_id>/       Per-run output (segments, blueprint, images, video, thumbnail)
  run_pipeline.py            CLI entry point for a full run
  setup_higgsfield.py        One-time Higgsfield MCP OAuth
  lock_charachter_image.py   Lock an identity image so it's never regenerated
  test_approval_mail.py      Test the approval email / review page (fixtures or a real render id)
  test_gdrive_upload.py      Test the rclone upload
```

---

## Setup

**Prerequisites:** Python 3.12, ffmpeg on PATH, rclone configured for your Google Drive,
a Higgsfield account.

```bash
cd backend
python -m venv .venv
# Windows:  .venv\Scripts\activate     macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

copy .env.example .env     # Windows  (macOS/Linux: cp .env.example .env)
```

Fill in `backend/.env` (see the reference below). At minimum:
`GROQ_API_KEY`, `YOUTUBE_API_KEY`, `SERPER_API_KEY`, `ELEVENLABS_API_KEY`.

**Reference photos:** put real, logo-free photos at `backend/assets/characters/host.png`
and `guest.png` (optionally `both.png` for a two-shot thumbnail).

**Higgsfield (one-time OAuth for the MCP):**
```bash
python setup_higgsfield.py     # opens a browser to authorize; writes assets/hf_mcp_tokens.json
```

**Google Drive (rclone):** ensure your `gdrive:` remote works —
`rclone lsd gdrive:reel-projects` should list your folders. Set `RCLONE_REMOTE` to the
exact destination your editing routine reads.

---

## Running

**Full pipeline (CLI):**
```bash
python run_pipeline.py
```

**API server:**
```bash
uvicorn app.main:app --port 8000
# (do NOT use --reload; the in-memory job/approval state resets on reload)
```
Trigger a run via `POST /api/run-pipeline`. Approval/review links and `/renders/...`
media are served by this same server, so it must be running for owners to review.

> After editing any service file, clear bytecode and restart:
> `Remove-Item -Recurse -Force app\__pycache__, app\services\__pycache__` then restart uvicorn.

**Test the approval/review flow without a full run:**
```bash
python test_approval_mail.py                 # uses fixtures in test_fixtures/approval_mail/
python test_approval_mail.py <render_id>     # uses an existing real render (regeneration works)
```

---

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| GET  | `/api/health` | Liveness check |
| POST | `/api/run-pipeline` | Run the full pipeline (optional `category`) |
| GET  | `/api/approvals/act` | Approve/Decline via emailed token link |
| GET  | `/api/approvals/review` | Interactive review page (clip checkboxes + regenerate) |
| POST | `/api/approvals/rerun` | Regenerate selected scenes, then re-send the email |
| GET  | `/api/approvals/{id}` | Approval record status |
| GET  | `/api/video-jobs/{id}` | Video job status/progress |
| GET  | `/api/schedule` | Scheduler status |
| GET  | `/api/characters` | Which reference photos are present |
| GET  | `/api/identity-cache` | Locked identity images on file |

---

## Configuration reference (`.env`)

**LLM / voice**
- `GROQ_API_KEY`, `GROQ_MODEL` — script/blueprint LLM
- `ELEVENLABS_API_KEY`, `ELEVENLABS_MODEL`, `HOST_VOICE_ID`, `GUEST_VOICE_ID`

**Research**
- `YOUTUBE_API_KEY`, `SERPER_API_KEY`
- `YOUTUBE_REGION_CODE`, `YOUTUBE_RELEVANCE_LANGUAGE`, `SERPER_GL`, `SERPER_HL`, `RESEARCH_LOOKBACK_DAYS`
- `DEFAULT_CATEGORY` — fixed category, or blank to auto-pick

**Higgsfield / video**
- `VIDEO_PROVIDER` — `hf_mcp` (Seedance via MCP, the primary path)
- `HF_MCP_URL`, `HF_MCP_CALLBACK_PORT` — MCP endpoint + OAuth callback port (3030)
- `HF_VIDEO_RESOLUTION` — `480p` | `720p` | `1080p` (the trailing `p` is required)
- `HF_IMAGE_RESOLUTION`, `HF_ASPECT_RATIO` (`9:16`)
- `HF_MAX_CREDITS_PER_CLIP` — per-clip spend ceiling (safety)
- `USE_DIRECTOR_SKILLS`, `SEEDANCE_BILINGUAL_PROMPT`, `ESTABLISHING_TWO_SHOT`,
  `REACTION_SHOTS_ENABLED`, `SCENE_LIMIT`

**Approval / email**
- `REQUIRE_APPROVAL` — gate publishing behind email approval
- `OWNER_EMAILS` — comma-separated reviewer addresses
- `APPROVAL_BASE_URL` — base URL for the approve/review links (e.g. `http://localhost:8000`)
- `EMAIL_TRANSPORT` — `smtp` | `file` | `auto`
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`

**Google Drive (rclone)**
- `UPLOAD_TO_GDRIVE`, `RCLONE_REMOTE` (e.g. `gdrive:reel-projects/TEST`)
- `RCLONE_EXE` (full path if not on PATH), `RCLONE_CONFIG` (path to `rclone.conf`, optional)
- `GDRIVE_CLIP_PREFIX` (`RawClip`), `GDRIVE_SUBFOLDER_PER_REEL`, `GDRIVE_DELETE_LOCAL_AFTER_UPLOAD`

**Scheduler (optional)**
- `SCHEDULE_ENABLED`, `SCHEDULE_CRON`, `SCHEDULE_WORKFLOW`, `SCHEDULE_TOPIC`

---

## Identity, look & thumbnail

- **Identity is generated once and locked.** Host/guest studio images are made from your
  reference photos and saved to `assets/identity_cache/`; later runs and all reruns reuse
  them (0 credits). Lock a specific image manually with `lock_charachter_image.py`.
- **Faces never change** — only the studio/wardrobe/background described in the prompt.
- **No on-screen text / no IP.** Prompts forbid burned-in captions and any logos,
  trademarks, real titles, or slogans, so Seedance doesn't render text and Higgsfield's
  IP filter doesn't reject the job.
- **Thumbnail** is a separate clickbait-style cover; on a per-clip rerun it is reused, not
  remade.

---

## Troubleshooting

- **Higgsfield `"Something went wrong. Please try again."`** — usually a stale MCP token.
  Delete `backend/assets/hf_mcp_tokens.json` and restart to re-authorize. If a manual
  generation in the Higgsfield web UI also fails, it's an account/credits/service issue
  on their side; keep the `request_id` for their support.
- **`resolution ... not in allowed options [480p, 720p, 1080p]`** — set
  `HF_VIDEO_RESOLUTION` with the trailing `p` (e.g. `1080p`).
- **`IP detected`** — something in the image/prompt looked like a trademark/logo/title;
  keep posters and clothing generic and unbranded.
- **`extra_forbidden` on startup** — an `.env` key that isn't a known setting. Remove it,
  or add it as a field in `config.py`.
- **rclone upload failed** — confirm `RCLONE_EXE`/`RCLONE_REMOTE`, and that
  `rclone lsd <remote>` works from the shell that launched uvicorn.

---

## Data handling & privacy

The following are git-ignored and stay local (never commit them):
`backend/.env`, `assets/hf_mcp_tokens.json`, `assets/hf_media_uploads.json`,
`assets/identity_cache/`, `assets/characters/*` (real people's photos),
`renders/`, `app/assets/approvals.json`, `app/assets/sent_emails/`.

`.env.example` holds placeholder keys only. Never commit real credentials or reference
photos to a public repository.