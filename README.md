# Reel Studio — AI Podcast Shorts: Script & Voice Studio

Page 1 of the Instagram automation pipeline: trending-topic research, 45–50s
podcast-style (Q&A) script generation, ElevenLabs host/guest voice mapping,
and in-browser voice preview.

## Stack
- **Backend** — FastAPI (Python 3.12) · Anthropic API (research + script agents) · ElevenLabs v3 (TTS)
- **Frontend** — React 18 + Vite, proxied to the backend (`/api/*`)
- **Deploy** — Docker Compose (nginx serves the built frontend and proxies the API)

## API endpoints
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Health check |
| POST | `/api/research` | Trending topics for a category (Claude + web search) |
| POST | `/api/script` | Generate 45–50s Q&A script (auto-retries if out of budget) |
| GET | `/api/voices` | Live voice list from your ElevenLabs account |
| POST | `/api/tts-preview` | Per-line TTS with host/guest voice mapping → base64 mp3 clips |

---

## Local development (step by step)

### 1 · Backend
```bash
cd backend
python -m venv venv
# Windows:  venv\Scripts\activate     macOS/Linux:  source venv/bin/activate
pip install -r requirements.txt

# Add your keys
copy .env.example .env        # Windows (or: cp .env.example .env)
# edit backend/.env → ANTHROPIC_API_KEY, ELEVENLABS_API_KEY

uvicorn app.main:app --reload --port 8000
```
Check: http://localhost:8000/api/health → `{"status":"ok"}`
Interactive docs: http://localhost:8000/docs

### 2 · Frontend (new terminal)
```bash
cd frontend
npm install
npm run dev
```
Open http://localhost:5173 — Vite proxies all `/api` calls to port 8000, so
no CORS issues and no key ever reaches the browser.

### 3 · Smoke test
1. Pick a category → **Find trending topics** (uses Claude web search — ~10–20s)
2. Click a topic chip, or type your own idea → **Generate 45–50s reel script**
3. Confirm the timing strip shows green (within 45–50s)
4. Pick Host + Guest voices (loaded live from your ElevenLabs account)
5. **▶ Preview with voices** — lines synthesize and play back in order

---

## Production deploy (Docker)
```bash
# from the project root, with backend/.env filled in:
docker compose up --build -d
```
Open http://localhost:8080. nginx serves the built frontend and proxies
`/api/*` to the backend container — keys stay server-side.

For a real domain: put this behind your existing reverse proxy / Cloudflare,
or deploy backend to any container host (Railway, Render, EC2) and the
frontend `dist/` to any static host, pointing nginx/proxy at the backend URL.

## Notes
- **Script budget**: backend validates total seconds and auto-retries once
  with a correction prompt if the model lands outside 45–50s.
- **Emotion → voice**: emotion tags map to ElevenLabs v3 audio tags
  (`[curious]`, `[warm]`, …) in `backend/app/services/elevenlabs_client.py`.
- **Rate limits**: TTS preview synthesizes max 12 lines with concurrency 3.
- **Next stages**: the Script Package JSON (`speaker/text/emotion/seconds`)
  is the canonical format — feed it directly into scene planning, video
  generation, and QA stages.
