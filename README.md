# Reel Studio — Automated Instagram Reel Pipeline

Backend-only automation for podcast-style Instagram Reels: live research from
YouTube + Serper, Groq picks category and topic, generates a 30–35s Q&A script,
synthesizes host/guest voices via ElevenLabs, renders and splits audio, builds a
scene blueprint, and generates the final video.

## Stack

- **Backend** — FastAPI (Python 3.12) · Groq (LLM) · YouTube Data API v3 + Serper (research) · ElevenLabs v3 (TTS) · Higgsfield/Seedance (video)
- **Deploy** — Docker Compose (backend on port 8000)

## Pipeline flow

1. Pick category (`DEFAULT_CATEGORY` env or Groq auto-select)
2. **Live research** — YouTube (recent popular videos) + Serper (web/news) → Groq distills 5 topics
3. Groq picks the best topic (`select_best_topic`)
4. Generate script (`generate_script`)
5. Synthesize + render audio, split into segments (`render_final_audio`)
6. Generate scene blueprint (`generate_scene_plan`)
7. Generate per-scene video clips and merge (`run_video_job`)

Output: `backend/renders/<render_id>/video/merged_reel.mp4`

## Setup

```bash
cd backend
python -m venv venv
# Windows:  venv\Scripts\activate     macOS/Linux:  source venv/bin/activate
pip install -r requirements.txt

copy .env.example .env        # Windows (or: cp .env.example .env)
# Edit backend/.env — at minimum:
#   GROQ_API_KEY, YOUTUBE_API_KEY, SERPER_API_KEY, ELEVENLABS_API_KEY, HF_API_KEY + HF_API_SECRET
```

### Topic research keys

| Env var | Source |
|---------|--------|
| `YOUTUBE_API_KEY` | [Google Cloud Console](https://console.cloud.google.com/) → enable **YouTube Data API v3** → Credentials → API key |
| `SERPER_API_KEY` | [serper.dev](https://serper.dev/) dashboard |

Optional tuning: `YOUTUBE_REGION_CODE`, `RESEARCH_LOOKBACK_DAYS` (default 30), `SERPER_GL` / `SERPER_HL`.

If both research keys are missing, Groq falls back to model-generated topic suggestions.

### Voice IDs (ElevenLabs)

Set in `.env`:

```
HOST_VOICE_ID=CwhRBWXzGAHq8TQ4Fs17
GUEST_VOICE_ID=RDjgzX0qNSGQZkgo5KTT
```

### Character photos

Place reference images in `backend/assets/characters/`:

- `host.png` (or `.jpg`)
- `guest.png`

Or lock identities ahead of time with `python lock_charachter_image.py`.

## Run once (CLI)

```bash
cd backend
python run_pipeline.py
python run_pipeline.py --category "Anxiety"
```

## Run on a schedule

In `backend/.env`:

```
SCHEDULE_ENABLED=true
SCHEDULE_CRON=0 9 * * *
SCHEDULE_TOPIC=
```

Start the server (scheduler fires inside the process):

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## HTTP API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Health check |
| POST | `/api/run-pipeline` | Run full pipeline (`{"category": "optional"}`) |
| GET | `/api/video-jobs/{job_id}` | Poll video job status |
| GET | `/api/schedule` | Cron scheduler status |
| GET | `/api/characters` | Character photo status |
| GET | `/api/identity-cache` | Locked identity cache status |

Static outputs are served at `/renders/<render_id>/...`.

## Docker

```bash
docker compose up --build -d
```

API: http://localhost:8000/api/health

## Local testing without API cost

Set `LLM_PROVIDER=mock` in `.env` to use canned topics/scripts/blueprints. TTS and
video steps still require valid ElevenLabs and Higgsfield credentials.

## Notes

- Script duration is validated at **30–35 seconds** in `backend/app/services/llm.py`.
- All render paths are relative to `backend/` (e.g. `renders/<id>/video/merged_reel.mp4`).
- Do not commit `backend/.env` — copy from `.env.example` instead.
