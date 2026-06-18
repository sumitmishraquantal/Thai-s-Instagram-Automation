"""Higgsfield video generation pipeline — Milestone 3 (verified API schemas).

Platform reality (platform.higgsfield.ai, June 2026):
  - Seedance 2.0 is NOT yet on the API — Seedance v1 Lite/Pro Fast only.
  - soul/reference takes exactly ONE reference image (prompt + image_reference_url).
  - seedance i2v takes exactly ONE start image (prompt + image_url), no audio input,
    no video chaining. duration 2-12s, resolution 480/720/1080, aspect 9:16 supported.

Consistency strategy under these constraints:
  1. Soul Reference turns YOUR Ron photo into "Ron in the studio armchair" once,
     and YOUR Jason photo into "Jason in the studio armchair" once.
  2. Every scene where Ron speaks starts from the SAME Ron image; same for Jason.
     Identical start frames = locked identity, wardrobe, set across the reel.
  3. Each clip is trimmed to the exact segment duration, concatenated, and the
     full ElevenLabs reel audio is laid underneath (no API lip-sync available yet;
     mouth motion is prompt-driven "speaking" animation).

Outputs in backend/renders/<render_id>/: images/host.png, images/guest.png,
video/scene_XX.mp4, video/merged_reel.mp4
"""
import asyncio
import json
import logging
import math
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx

from ..config import get_settings
from ..schemas import Scene, SceneBlueprint
from . import director_skills, higgsfield_mcp, image_prep, llm
from .render import RENDERS_DIR

logger = logging.getLogger(__name__)

JOBS: dict[str, dict] = {}

ASSETS_DIR = RENDERS_DIR.parent / "assets" / "characters"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_PHOTO_EXTS = (".png", ".jpg", ".jpeg", ".webp")

# Identity images are expensive to imply per-reel, but the characters never change.
# Cache the generated studio identity image + its reusable Higgsfield ref, keyed on
# a hash of (source photo bytes + studio description + model). Reused across all jobs
# until the photo or studio canon changes, or force_regen is set.
IDENTITY_CACHE_DIR = ASSETS_DIR.parent / "identity_cache"
IDENTITY_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _identity_signature(photo: Path, studio: str, model: str) -> str:
    import hashlib
    h = hashlib.sha256()
    h.update(photo.read_bytes())
    h.update(studio.encode("utf-8"))
    h.update(model.encode("utf-8"))
    return h.hexdigest()[:16]


def _identity_cache_load(role: str, signature: str) -> dict | None:
    meta = IDENTITY_CACHE_DIR / f"{role}.json"
    png = IDENTITY_CACHE_DIR / f"{role}.png"
    if meta.exists() and png.exists():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            # A user-locked identity (set via lock_character_image.py) is reused
            # regardless of studio/script signature — it's a deliberate pin.
            if data.get("locked") and data.get("ref"):
                return data
            if data.get("signature") == signature and data.get("ref"):
                return data
        except Exception:  # noqa: BLE001
            return None
    return None


def _identity_cache_save(role: str, signature: str, url: str, ref: str, src_png: Path,
                          locked: bool = False):
    import shutil
    dest_png = IDENTITY_CACHE_DIR / f"{role}.png"
    if Path(src_png).resolve() != dest_png.resolve():
        shutil.copyfile(src_png, dest_png)
    (IDENTITY_CACHE_DIR / f"{role}.json").write_text(
        json.dumps({"signature": signature, "url": url, "ref": ref, "locked": locked}, indent=2),
        encoding="utf-8",
    )

SEEDANCE_MIN_DUR, SEEDANCE_MAX_DUR = 2, 12


# ── Character photo storage ───────────────────────────────
def _character_path(role: str) -> Path | None:
    """Find the character photo regardless of extension (.png/.jpg/.jpeg/.webp,
    any case)."""
    for p in sorted(ASSETS_DIR.glob(f"{role}.*")):
        if p.suffix.lower() in _PHOTO_EXTS:
            return p
    return None


def save_character(role: str, data: bytes, content_type: str):
    old = _character_path(role)
    if old:
        old.unlink()
    (ASSETS_DIR / f"{role}{_EXT[content_type]}").write_bytes(data)


def character_status() -> dict:
    out = {}
    for role in ("host", "guest"):
        p = _character_path(role)
        out[role] = f"/assets/{p.name}" if p else None
    return out


# ── SDK helpers ───────────────────────────────────────────
def _ensure_credentials():
    s = get_settings()
    if not s.hf_api_key or not s.hf_api_secret:
        raise RuntimeError("HF_API_KEY / HF_API_SECRET not set in backend/.env")
    os.environ["HF_API_KEY"] = s.hf_api_key
    os.environ["HF_API_SECRET"] = s.hf_api_secret


async def _hf_subscribe(endpoint: str, arguments: dict) -> dict:
    import higgsfield_client
    _ensure_credentials()
    logger.info("Higgsfield call %s | args keys: %s", endpoint, list(arguments.keys()))
    return await higgsfield_client.subscribe_async(endpoint, arguments=arguments)


_CONTENT_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
    ".wav": "audio/wav",
}


async def _hf_upload(path: Path) -> str:
    """Upload a file to Higgsfield, forcing the correct content-type.
    Higgsfield rejects audio/mpeg — audio must be wav. Images: png/jpeg/webp/gif."""
    import higgsfield_client
    _ensure_credentials()
    ext = path.suffix.lower()
    ctype = _CONTENT_TYPES.get(ext)
    if ctype is None:
        # never upload mp3/unknown; convert mp3 to wav upstream before calling this
        raise RuntimeError(f"Unsupported upload type '{ext}' for {path.name}; "
                           f"Higgsfield accepts png/jpeg/webp/gif images and wav audio.")
    data = path.read_bytes()
    return await higgsfield_client.upload_async(data, ctype)


def _first_url(result: dict, *keys: str) -> str:
    """Tolerant media-URL extraction across response shapes."""
    if isinstance(result, str) and result.startswith("http"):
        return result
    for k in (*keys, "images", "image", "video", "videos", "output", "result", "url"):
        v = result.get(k) if isinstance(result, dict) else None
        if isinstance(v, dict):
            if v.get("url"):
                return v["url"]
        if isinstance(v, list) and v:
            first = v[0]
            if isinstance(first, dict) and first.get("url"):
                return first["url"]
            if isinstance(first, str) and first.startswith("http"):
                return first
        if isinstance(v, str) and v.startswith("http"):
            return v
    raise ValueError(f"No media URL found in result: {str(result)[:300]}")


async def _download(url: str, dest: Path):
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        dest.write_bytes(r.content)


# A FIXED, literal description of the REAL studio seen in the reference photos,
# used identically across all prompts so host, guest and the two-shot all render in
# the SAME room. This is the one shared studio look that standardizes every image.
_FIXED_SET = (
    "SAME EXACT modern podcast studio in every shot (do not vary it): a tall matte-black "
    "built-in bookshelf-and-cabinet back wall with warm interior LED accent lighting, "
    "glass-front upper cabinets and cane/rattan-front lower cabinets, styled with hardcover "
    "books, small green potted plants and white flowers, a polished live-edge wood-slice "
    "sculpture on a stand, a small white astronaut figurine, and framed black-background "
    "white-text motivational typography posters (e.g. 'DISCIPLINE EQUALS FREEDOM'); the people "
    "sit in mid-century grey upholstered armchairs with wooden frames; a black dynamic "
    "microphone on a black boom arm reaches in from the side; a neutral patterned area rug on a "
    "dark wood floor. Warm, moody, low-key cinematic lighting. This identical studio, "
    "cabinetry, books, decor, posters, armchairs, microphone and warm lighting must appear in "
    "every single shot so all scenes look like one continuous recording in the same room."
)

# A short canonical studio phrase for the (token-limited) director briefs, so the
# brief never depends on the planner's free-text background (which can drift).
_STUDIO_SHORT = (
    "the same fixed studio — matte-black bookshelf/cabinet wall with warm LED accent lighting, "
    "books, plants, a live-edge wood slice, an astronaut figurine and motivational typography "
    "posters, mid-century grey upholstered armchairs, a black boom microphone, neutral rug, warm "
    "moody lighting"
)


# ── Prompt building ───────────────────────────────────────
def _identity_image_prompt(role: str, studio: str) -> str:
    if role == "both":
        return (
            f"The TWO exact people from the reference photo together as podcast co-hosts, seated SIDE BY SIDE "
            f"in two separate mid-century grey upholstered armchairs, angled slightly toward each other, both "
            f"fully visible in one balanced two-shot. {_FIXED_SET} A black dynamic microphone on a black boom "
            f"arm reaches in toward each of them from the side, NO headphones (in-person conversation), relaxed "
            f"natural posture, mid-conversation. Faithfully REPLICATE the reference photo: keep BOTH people's "
            f"faces, hair, beards, build and their black/dark clothing exactly as shown — do not swap or merge "
            f"their identities. Wide-to-medium vertical 9:16 two-shot, photographic and lifelike (natural skin "
            f"texture, visible pores, catchlights), warm moody low-key lighting, true-to-life colour, sharp "
            f"focus. No on-screen text, no watermark, no logos, NOT a 3D render, NOT illustration, NOT anime."
        )
    label = "HOST" if role == "host" else "GUEST"
    return (
        f"Faithfully REPLICATE the reference photo of this exact person as a podcast {label}, seated in a "
        f"mid-century grey upholstered armchair with a wooden frame, framed as a SINGLE-PERSON SHOT — ONLY "
        f"this one person is visible in frame, no second person, no one sitting opposite. {_FIXED_SET} A black "
        f"dynamic microphone on a black boom arm reaches in from the side, NO headphones (this is an in-person "
        f"conversation), body relaxed and angled slightly toward the other person (off-camera to the side), "
        f"hands resting naturally. Keep the person's face, hair, beard, build and their black/dark clothing "
        f"exactly as in the reference photo — same wardrobe, same studio, same warm lighting. Looking off to "
        f"the side toward the other person (not into the lens). Vertical 9:16 composition. "
        f"PHOTOREALISTIC like a real photograph of a real human shot on a cinema camera with a 50mm lens and "
        f"shallow depth of field; lifelike skin with natural texture, visible pores and subtle subsurface "
        f"tones (never airbrushed, plastic or waxy); catchlights in the eyes; warm moody studio lighting; "
        f"true-to-life colour. Sharp focus, no text, no watermark, "
        f"no logos, NOT a 3D render, NOT illustration, NOT anime/cartoon."
    )


def _sanitize_camera(cam: str) -> str:
    """Never allow a two-shot / two-person framing to slip in from the planner —
    the whole reel is strictly one person on camera at a time."""
    c = (cam or "static medium shot, slight handheld sway").strip()
    c = re.sub(r"two[\s\-]?shot", "single-person medium shot", c, flags=re.I)
    c = re.sub(r"\b(both|two)\s+(people|persons|of them)\b", "the speaker", c, flags=re.I)
    return c


def _scene_video_prompt(scene: Scene, studio: str, speaker_role: str,
                        listener_role: str, want_reaction: bool) -> str:
    """Single-clip prompt. Two reference images are supplied to the model: the
    FIRST is the speaker (primary subject, lip-syncs to the audio), the SECOND is
    the listener (used only for an optional brief in-clip reaction cut). The hard
    rule baked into the prompt: only ONE person is ever on screen at any instant —
    never a two-shot. Everything (gesture, tone, expression, eye-line, optional
    reaction) is driven by the script fields of THIS scene."""
    speaker = speaker_role.lower()
    listener = listener_role.lower()
    cam = _sanitize_camera(scene.camera_movement)
    body = (scene.body_language or "").strip()
    eyes = (scene.eye_contact or "looking naturally toward the other person to the side, "
            "with occasional brief glances down or to the microphone in thought").strip()
    tone = (scene.emotional_tone or "").strip()
    humor = (scene.humor or "").strip()
    cue = (scene.reaction_cue or "listening intently and nodding slowly, attentive and engaged").strip()

    parts = [
        # Subjects + the iron framing rule
        f"A photorealistic in-person podcast clip, all filmed in ONE studio. TWO reference images are "
        f"provided: the FIRST reference person is the {speaker} (the speaker in this clip); the SECOND "
        f"reference person is the {listener} (the listener). They are two different real people. "
        f"CRITICAL FRAMING RULE: only ONE person is ever visible in the frame at any single moment — this "
        f"is a tight single-person vertical shot. NEVER show both people in the same frame; no two-shot, "
        f"no split-screen, no second person seated or blurred in the background.",

        # The speaking action, hard lip-sync to the audio
        f"Primary action: the {speaker} is mid-conversation, speaking into the studio microphone. The mouth "
        f"movements must EXACTLY and precisely lip-sync to the provided audio track — match every word, "
        f"syllable and pause, and say ONLY what the audio says (never invent or mouth words that are not in "
        f"the audio). {scene.character_action}.",

        f"Facial expression: {scene.facial_expression.lower()}.",
    ]
    if body:
        parts.append(f"Body language: {body}.")
    if tone:
        parts.append(f"Emotional tone of the delivery: {tone}.")
    if humor:
        parts.append(f"Subtle, natural touch (only if it fits the moment): {humor}.")
    parts.append(
        f"Eye contact: {eyes}. This is a real in-person conversation — the {speaker} looks mostly toward the "
        f"OTHER person off to the side, NOT into the camera (no fixed camera stare)."
    )

    if want_reaction:
        parts.append(
            f"REACTION BEAT (still single-person — no two-shot): for a brief moment, about 1 to 2 seconds in "
            f"the middle of the clip, cut to the {listener} ALONE in frame (use the SECOND reference image) — "
            f"{cue}; mouth closed, NOT speaking, just a natural listening reaction such as a slow nod or a small "
            f"empathetic look — then cut back to the {speaker} speaking. The audio keeps playing throughout (it is "
            f"the {speaker}'s voice); the {listener} never talks. Even during this brief cut, only ONE person is on "
            f"screen, and it is the same studio and lighting."
        )

    # Identity + set lock
    parts.append(
        f"Preserve each person's exact face, hair, beard, skin tone and their black/dark clothing from their own "
        f"reference image with zero identity changes. No headphones. They sit in mid-century grey upholstered "
        f"armchairs. " + _FIXED_SET + " Do not change the room, the cabinetry, the books, the posters, the decor "
        f"or the lighting."
    )
    # Realism
    parts.append(
        f"Camera: {cam}; single continuous take with a natural handheld feel. ULTRA-PHOTOREALISTIC: shot on a "
        f"professional cinema camera with a 50mm lens and shallow depth of field; lifelike skin with natural "
        f"texture and visible pores (never plastic, waxy or airbrushed), catchlights in the eyes, realistic "
        f"blinking and micro-expressions, natural subtle head and shoulder motion."
    )
    # Negative guidance
    parts.append(
        f"Negative: do NOT change the people, do NOT change the location; no two people in one frame, no second "
        f"person in the background, no headphones, no on-screen text, no captions, no logos, no cartoon/anime/3D-"
        f"render look, no plastic or waxy skin, no fixed camera stare."
    )
    return " ".join(parts)


# ── Director-skill briefs + enforcement suffixes ──────────
# The uploaded skills (loaded as SYSTEM prompts) do the *creative* prompt
# writing; these briefs frame our exact use case for the skill, and the
# enforcement suffixes lock the non-negotiable technical contract on top
# (identity, fixed studio, single-person framing, no headphones, lip-sync).
# They deliberately reuse the same canon (_FIXED_SET) and constraint language as
# the built-in fallback prompts so directed and fallback prompts stay aligned.
def _image_concept_brief(role: str, studio: str) -> str:
    if role == "both":
        return (
            f"An editorial two-shot photograph of TWO real podcast co-hosts seated SIDE BY SIDE in two "
            f"separate mid-century grey upholstered armchairs, angled slightly toward each other, a black boom "
            f"microphone reaching in toward each from the side, relaxed and mid-conversation. Both people fully "
            f"visible in one balanced frame. Vertical 9:16. Faithfully reproduce the reference photo (same "
            f"people, same wardrobe, same studio). Aim for a believable real-photograph look (film/editorial, "
            f"not glossy CGI). Studio: {_FIXED_SET}"
        )
    who = "podcast host" if role == "host" else "podcast guest"
    return (
        f"Single editorial portrait of one real {who} seated in a mid-century grey upholstered armchair in a "
        f"warm in-person podcast studio, a black boom microphone reaching in from the side, body relaxed and "
        f"angled slightly toward the other person off to the side (off-camera), looking off to the side (not at "
        f"the lens). One person only in frame. Vertical 9:16. Faithfully reproduce the reference photo (same "
        f"person, same black/dark wardrobe, same studio). Aim for a believable real-photograph look "
        f"(film/editorial, not glossy CGI). Studio: {_FIXED_SET}"
    )


def _image_enforcement(role: str) -> str:
    if role == "both":
        return (
            "NON-NEGOTIABLE CONSTRAINTS (override anything above that conflicts): " + _FIXED_SET +
            " Keep BOTH people's faces, hair, beards, build and their black/dark clothing EXACTLY as in the "
            "supplied reference photo — do not swap, merge or invent identities. A balanced TWO-SHOT: both "
            "people fully visible, seated SIDE BY SIDE in separate grey armchairs angled slightly toward each "
            "other. NO headphones (in-person conversation). Vertical 9:16. No on-screen text, no captions, no "
            "watermark, no logos. Render as a real photograph (natural skin texture, visible pores, "
            "catchlights), NOT a 3D render, NOT illustration, NOT anime."
        )
    return (
        "NON-NEGOTIABLE CONSTRAINTS (override anything above that conflicts): " + _FIXED_SET +
        " Keep the person's face, hair, beard, build and their black/dark clothing EXACTLY as in the supplied "
        "reference image — zero identity changes, same wardrobe. SINGLE-PERSON SHOT: only this one person is "
        "visible, no second person, no one sitting opposite. They sit in a mid-century grey upholstered "
        "armchair. NO headphones (in-person conversation). Vertical 9:16. No on-screen text, no captions, no "
        "watermark, no logos. Render as a real photograph (natural skin texture, visible pores, catchlights), "
        "NOT a 3D render, NOT illustration, NOT anime."
    )


def _video_concept_brief(scene: Scene, speaker: str, listener: str,
                         want_reaction: bool, duration: int, cam: str) -> str:
    bits = [
        f"A calm, friendly IN-PERSON podcast conversation clip, about {duration}s, vertical 9:16, single "
        f"camera, minimal cuts. The {speaker} is the on-camera speaker; the {listener} is the other "
        f"participant (off-camera, seated to the side). This is a relaxed studio chat — not action, not a "
        f"confrontation.",
        f"The {speaker}'s spoken audio is SUPPLIED separately — do NOT write any dialogue, narration or "
        f"subtitles; only describe the visible performance that lip-syncs to that audio.",
        f"What the {speaker} does on this line: {scene.character_action}.",
        f"Facial expression: {scene.facial_expression}.",
    ]
    if (scene.body_language or "").strip():
        bits.append(f"Body language: {scene.body_language}.")
    if (scene.emotional_tone or "").strip():
        bits.append(f"Emotional tone: {scene.emotional_tone}.")
    if (scene.humor or "").strip():
        bits.append(f"Light optional touch (only if it fits): {scene.humor}.")
    eyes = (scene.eye_contact or "looks mostly toward the other person to the side, brief glances down").strip()
    bits.append(f"Eye-line: {eyes} (not staring at the camera).")
    bits.append(f"Camera: {cam}.")
    if want_reaction:
        cue = (scene.reaction_cue or "nods slowly, listening, attentive").strip()
        bits.append(
            f"Include ONE brief (~1-2s) cut to the {listener} ALONE reacting ({cue}, not speaking), then "
            f"back to the {speaker}. Never both people in one frame."
        )
    else:
        bits.append("Single continuous take, no cuts.")
    bits.append(f"Studio: {_STUDIO_SHORT}.")
    return " ".join(bits)


def _video_enforcement(scene: Scene, speaker: str, listener: str, want_reaction: bool) -> str:
    parts = [
        "NON-NEGOTIABLE TECHNICAL CONTRACT (override anything above that conflicts):",
        f"Only ONE person is ever visible at any instant — tight single-person vertical shot. NEVER show "
        f"both people in the same frame; no two-shot, no split-screen, no second person in the background.",
        f"The {speaker} is the primary subject (FIRST reference image). The mouth movements must EXACTLY "
        f"lip-sync to the PROVIDED audio track — say only what the audio says, never invent or mouth words "
        f"not in the audio. Do NOT add any spoken dialogue, narration, subtitles or an Audio section; the "
        f"audio is supplied separately.",
    ]
    if want_reaction:
        parts.append(
            f"The ONE permitted cut is a brief ~1-2s glimpse of the {listener} ALONE (SECOND reference "
            f"image) listening/reacting with mouth closed, then back to the {speaker}; otherwise a single "
            f"continuous take. Even during this cut, only one person is on screen."
        )
    else:
        parts.append("Single continuous take, no hard cuts.")
    parts.append(
        "Preserve each person's exact face, hair, beard, skin tone and clothing from their OWN reference "
        "image, zero identity changes. NO headphones (they sit face to face). " + _FIXED_SET +
        " Ultra-real human look: natural skin texture and pores, catchlights, realistic blinking and "
        "micro-expressions; not plastic, waxy, cartoon, anime or 3D-render. No on-screen text, no captions, "
        "no logos, no fixed camera stare."
    )
    return " ".join(parts)


def _two_shot_video_prompt(scene: Scene, studio: str, speaker: str, listener: str) -> str:
    """Built-in fallback prompt for the ESTABLISHING two-shot: both people visible
    in one frame, the speaker lip-syncing to the supplied audio while the other
    listens. This is the ONE clip in the reel where two people share the frame."""
    spk = speaker.lower()
    lis = listener.lower()
    return (
        f"A photorealistic establishing TWO-SHOT of an in-person podcast: BOTH the {spk} and the {lis} are "
        f"visible together in one frame, seated SIDE BY SIDE in two separate mid-century grey upholstered "
        f"armchairs angled slightly toward each other in the studio. "
        f"This is a wide-to-medium vertical 9:16 two-shot that establishes the scene. The {spk} is speaking "
        f"into the microphone — the {spk}'s mouth movements must lip-sync to the provided audio track (say "
        f"only what the audio says, never invent words); the {lis} listens and gives small natural nods. "
        f"{scene.character_action}. Keep BOTH people's exact faces, hair, beards, skin tone and clothing from "
        f"the reference images with zero identity changes — the {spk} from one reference and the {lis} from "
        f"the other; do not swap or merge them. No headphones (in-person). " + _FIXED_SET + " Camera: slow gentle "
        f"push-in, single continuous take. Ultra-real human look: natural skin texture and pores, "
        f"catchlights, realistic blinking and micro-expressions; not plastic, waxy, cartoon, anime or "
        f"3D-render. No on-screen text, no captions, no logos."
    )


def _two_shot_concept_brief(scene: Scene, speaker: str, listener: str, duration: int, cam: str) -> str:
    return (
        f"An establishing TWO-SHOT for an in-person podcast, about {duration}s, vertical 9:16. BOTH people "
        f"are visible together in one frame, seated SIDE BY SIDE in separate grey armchairs angled toward each other: the {speaker} is "
        f"speaking, the {listener} is listening and nodding. This opening shot establishes the room and the "
        f"pair before the conversation cuts to single talking heads. The {speaker}'s spoken audio is SUPPLIED "
        f"separately — do NOT write dialogue; only describe the visible performance lip-syncing to it. "
        f"What the {speaker} does: {scene.character_action}. Camera: {cam}. Keep it a calm studio chat, not "
        f"action. Studio: {_STUDIO_SHORT}."
    )


def _two_shot_enforcement(scene: Scene, speaker: str, listener: str) -> str:
    return (
        "NON-NEGOTIABLE TECHNICAL CONTRACT (override anything above that conflicts): This is the ONE "
        "establishing two-shot — BOTH people are intentionally visible together in one balanced frame, "
        "seated SIDE BY SIDE in separate grey armchairs angled toward each other. Do NOT add extra people. " +
        f"The {speaker} is the speaker and lip-syncs EXACTLY to the PROVIDED audio (say only what the audio "
        f"says, never invent or mouth words; no added dialogue, narration, subtitles or Audio section); the "
        f"{listener} listens with mouth closed and small natural nods. Preserve BOTH identities exactly from "
        f"their reference images — the {speaker} from one, the {listener} from the other; never swap or merge "
        f"faces. NO headphones. " + _FIXED_SET +
        " Single continuous take, gentle push-in. Ultra-real human look: natural skin texture and pores, "
        "catchlights, realistic blinking and micro-expressions; not plastic, waxy, cartoon, anime or "
        "3D-render. No on-screen text, no captions, no logos."
    )


def _is_static_camera(camera_movement: str) -> bool:
    cm = camera_movement.lower()
    return any(w in cm for w in ("static", "fixed", "locked", "still"))


# ── ffmpeg helpers ────────────────────────────────────────
def _trim_clip(src: Path, dest: Path, seconds: float):
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-t", f"{seconds:.2f}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(dest)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg trim failed: {r.stderr[-300:]}")


def _merge_with_reel_audio(files: list[Path], reel_audio: Path | None, out: Path):
    n = len(files)
    inputs: list[str] = []
    for f in files:
        inputs += ["-i", str(f)]
    fc = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1[v]"
    cmd = ["ffmpeg", "-y", *inputs]
    if reel_audio:
        cmd += ["-i", str(reel_audio)]
    cmd += ["-filter_complex", fc, "-map", "[v]"]
    if reel_audio:
        cmd += ["-map", f"{n}:a", "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg merge failed: {r.stderr[-500:]}")


def _to_wav(mp3_path: Path) -> Path:
    """Higgsfield media upload accepts audio/wav, not audio/mpeg. Convert the
    segment mp3 to a 16-bit PCM wav next to it (cached)."""
    wav_path = mp3_path.with_suffix(".wav")
    if wav_path.exists() and wav_path.stat().st_mtime >= mp3_path.stat().st_mtime:
        return wav_path
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp3_path), "-ar", "44100", "-ac", "2",
         "-c:a", "pcm_s16le", str(wav_path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"mp3->wav conversion failed: {r.stderr[-300:]}")
    return wav_path


def _extract_last_frame(video: Path, out_png: Path) -> Path:
    """Grab the final frame of a clip as a PNG, for chaining the next scene's
    start_image so motion continues seamlessly."""
    # -sseof -0.1 seeks to ~0.1s before the end, then grabs one frame
    r = subprocess.run(
        ["ffmpeg", "-y", "-sseof", "-0.2", "-i", str(video),
         "-vframes", "1", "-q:v", "2", str(out_png)],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not out_png.exists():
        # fallback: first-frame-from-end via select
        r2 = subprocess.run(
            ["ffmpeg", "-y", "-i", str(video), "-vf",
             r"select=eq(n\,0)+gte(t\,0)", "-vsync", "0", "-update", "1",
             str(out_png)], capture_output=True, text=True)
        if not out_png.exists():
            raise RuntimeError(f"last-frame extract failed: {r.stderr[-200:]}")
    return out_png


def _merge_av(files: list[Path], out: Path):
    n = len(files)
    inputs: list[str] = []
    for f in files:
        inputs += ["-i", str(f)]
    fc = "".join(f"[{i}:v][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    r = subprocess.run(
        ["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-movflags", "+faststart", str(out)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"A/V concat failed: {r.stderr[-300:]}")


def _find_reel_audio(base_dir: Path) -> Path | None:
    for p in base_dir.glob("*.mp3"):
        if not p.name.startswith("segment_"):
            return p
    return None


# ── Job pipeline ──────────────────────────────────────────
def _update(job: dict, **kw):
    job.update(kw)
    job["log"].append(kw.get("step", job.get("step", "")))


async def run_video_job(job_id: str, render_id: str, blueprint: SceneBlueprint):
    job = JOBS[job_id]
    s = get_settings()
    base_dir = RENDERS_DIR / render_id
    img_dir = base_dir / "images"
    vid_dir = base_dir / "video"
    img_dir.mkdir(exist_ok=True)
    vid_dir.mkdir(exist_ok=True)

    try:
        seg_manifest = json.loads((base_dir / "segments.json").read_text(encoding="utf-8"))
        seg_by_index = {sgm["index"]: sgm for sgm in seg_manifest}

        # 1 · studio canon
        _update(job, status="running", step="Building studio canon")
        canon = await llm.build_visual_canon(blueprint)
        studio = canon["studio"]
        (base_dir / "visual_canon.json").write_text(json.dumps(canon, indent=2), encoding="utf-8")

        use_mcp = s.video_provider.lower() == "hf_mcp"
        mcp_client = higgsfield_mcp.HiggsfieldMCP(job=job) if use_mcp else None

        # 2 · identity images — one locked frame per character
        # First, AUTO-NORMALIZE the raw reference photos: accept host/guest/both in
        # ANY format (jpg/jpeg/png/webp/heic/...), fix EXIF rotation, flatten alpha,
        # and write a single clean <role>.png. This runs every job (idempotent, cheap)
        # so you never have to run a separate prepare step by hand.
        if use_mcp:
            norm = image_prep.normalize_inputs(ASSETS_DIR)
            for line in norm["results"]:
                _update(job, step=f"Input photo — {line}")
        host_photo, guest_photo = _character_path("host"), _character_path("guest")
        identity_urls: dict[str, str] = {}
        identity_refs: dict[str, str] = {}

        if use_mcp:
            import shutil
            both_photo = _character_path("both")
            photos = {"host": host_photo, "guest": guest_photo, "both": both_photo}
            # host + guest always; 'both' only when the establishing two-shot is on
            # AND we have either a locked 'both' identity or a both-photo to make one.
            roles = ["host", "guest"]
            if get_settings().establishing_two_shot:
                if both_photo or _identity_cache_load("both", "") or (IDENTITY_CACHE_DIR / "both.png").exists():
                    roles.append("both")
                else:
                    _update(job, step=("Establishing two-shot is ON but no 'both' photo/locked image found "
                                       "(assets/characters/both.*) — the opening will fall back to a single "
                                       "talking head."))
            for role in roles:
                photo = photos[role]
                # 1) Locked/cached identity is self-sufficient — reuse it even if
                #    the original reference photo is no longer on disk.
                sig = _identity_signature(photo, studio, "gpt_image_2") if photo else None
                cached = None
                if not job.get("force_regen_identity"):
                    # locked entries match regardless of signature; non-locked need sig
                    cached = _identity_cache_load(role, sig if sig else "")
                    if not cached and sig:
                        cached = _identity_cache_load(role, sig)
                if cached:
                    _update(job, step=f"Reusing cached {role} identity image (0 credits)")
                    identity_urls[role] = cached["url"]
                    identity_refs[role] = cached["ref"]
                    cpng = IDENTITY_CACHE_DIR / f"{role}.png"
                    if cpng.exists():
                        shutil.copyfile(cpng, img_dir / f"{role}.png")
                    continue

                # 2) No cache — need the source photo to generate the studio image.
                if not photo:
                    raise RuntimeError(
                        f"No locked/cached identity for {role} and no reference photo at "
                        f"assets/characters/{role}.*. Add the photo or lock an image first.")
                _update(job, step=f"Generating {role} identity image (first time for this photo/studio)")
                # The gpt-image-2 director skill writes the creative prompt; our
                # enforcement suffix locks identity + studio + single-person + no
                # headphones. Falls back to the built-in prompt if the skill is off
                # or its LLM call returns nothing.
                img_prompt = None
                if get_settings().use_director_skills:
                    img_prompt = await director_skills.image_prompt(
                        _image_concept_brief(role, studio), _image_enforcement(role))
                    if img_prompt:
                        _update(job, step=f"{role}: prompt written by gpt-image-2 director skill")
                if not img_prompt:
                    img_prompt = (_identity_image_prompt(role, studio) +
                                  " Keep the person's face, hair and likeness exactly as in the reference image.")
                gen = await mcp_client.generate(
                    "image",
                    prompt=img_prompt,
                    model_hint="gpt_image_2",
                    image_files=[photo],
                    aspect_ratio=s.hf_aspect_ratio,
                    resolution="2k",
                )
                identity_urls[role] = gen["url"]
                dest = img_dir / f"{role}.png"
                await _download(gen["url"], dest)

                # AUTO-LOCK: persist the generated studio image as the permanent
                # identity so it is NEVER generated or re-uploaded again. We copy
                # the PNG into the identity_cache and register it once to get a
                # durable, reusable media ref (exactly what lock_character_image.py
                # does, but automatic). Every future scene/run reuses this ref —
                # zero generation, zero media_upload of the raw photo.
                import shutil as _sh
                cache_png = IDENTITY_CACHE_DIR / f"{role}.png"
                _sh.copyfile(dest, cache_png)
                locked_ref = gen.get("ref")
                try:
                    _update(job, step=f"Locking {role} identity image (one-time, reused free afterwards)")
                    reg = await mcp_client.register_local_image(cache_png)
                    if reg.get("ref"):
                        locked_ref = reg["ref"]
                except Exception as e:  # noqa: BLE001
                    logger.warning("auto-lock register failed for %s (using gen ref): %s", role, e)
                identity_refs[role] = locked_ref
                if locked_ref:
                    _identity_cache_save(role, sig, gen["url"], locked_ref, cache_png, locked=True)
                    _update(job, step=f"{role.title()} identity locked — future reels reuse it for 0 credits")
        elif host_photo and guest_photo:
            for role, photo in (("host", host_photo), ("guest", guest_photo)):
                _update(job, step=f"Uploading {role} reference photo")
                ref_url = await _hf_upload(photo)
                _update(job, step=f"Generating {role} studio identity image (Soul Reference)")
                res = await _hf_subscribe(s.hf_image_ref_model, {
                    "prompt": _identity_image_prompt(role, studio),
                    "image_reference_url": ref_url,
                    "aspect_ratio": s.hf_aspect_ratio,
                    "resolution": s.hf_image_resolution,
                    "enhance_prompt": False,  # our prompt is deliberate; don't let it drift
                })
                url = _first_url(res)
                identity_urls[role] = url
                await _download(url, img_dir / f"{role}.png")
        else:
            # Fallback: invent characters from text (testing only)
            for role in ("host", "guest"):
                desc = canon[role]
                _update(job, step=f"No photo for {role} — generating from text canon (testing only)")
                res = await _hf_subscribe(s.hf_image_model, {
                    "prompt": f"{desc}. {_identity_image_prompt(role, studio)}",
                    "aspect_ratio": s.hf_aspect_ratio,
                    "resolution": "2K",  # soul/standard uses 2K|4K
                })
                url = _first_url(res)
                identity_urls[role] = url
                await _download(url, img_dir / f"{role}.png")

        job["images"] = {r: f"/renders/{render_id}/images/{r}.png" for r in identity_urls}

        # 3 · per-scene clips — same identity image every time a speaker is on camera
        scene_limit = get_settings().scene_limit
        scenes_to_do = blueprint.scenes[:scene_limit] if scene_limit else blueprint.scenes
        if scene_limit:
            _update(job, step=f"SCENE_LIMIT={scene_limit}: generating only the first {scene_limit} scene(s)")
        chain_enabled = get_settings().chain_scenes
        prev_clip: Path | None = None
        prev_role: str | None = None
        scene_files: list[Path] = []
        force_regen = job.get("force_regen_scenes", False)
        for scene in scenes_to_do:
            seg = seg_by_index.get(scene.scene_number)
            target_sec = float(seg["duration"]) if seg else (scene.end_second - scene.start_second)
            duration = int(min(SEEDANCE_MAX_DUR, max(SEEDANCE_MIN_DUR, math.ceil(target_sec))))
            seg_speaker = (seg.get("speaker") if seg else None) or scene.speaker_on_camera
            role = "host" if str(seg_speaker).upper() == "HOST" else "guest"

            final_clip = vid_dir / f"scene_{scene.scene_number:02d}.mp4"

            # RESUME: if this scene's clip already exists from a prior run, reuse it
            # (no regeneration, no credits). A retry thus continues from where it
            # stopped. force_regen_scenes bypasses this.
            if not force_regen and final_clip.exists() and final_clip.stat().st_size > 10000:
                _update(job, step=f"Scene {scene.scene_number}: already generated — reusing existing clip (0 credits)")
                scene_files.append(final_clip)
                prev_clip = final_clip
                prev_role = role
                job["scenes"].append({
                    "scene_number": scene.scene_number,
                    "speaker": role,
                    "url": f"/renders/{render_id}/video/{final_clip.name}",
                    "reused": True,
                })
                continue

            _update(job, step=f"Scene {scene.scene_number}/{len(blueprint.scenes)}: video generation ({duration}s, takes minutes)")
            if use_mcp:
                # Seedance 2.0: identity image(s) + segment audio -> lip-synced clip.
                audio_files = None
                if seg:
                    seg_path = base_dir / Path(seg["audio_url"]).name
                    audio_files = [_to_wav(seg_path)]  # Higgsfield needs wav, not mp3

                speaker_role = role
                listener_role = "guest" if speaker_role == "host" else "host"

                # ESTABLISHING TWO-SHOT: the FIRST clip opens on both people in one
                # frame (over the opening line's audio), then the reel cuts to single
                # talking heads. This is the only clip where two people share frame.
                is_first = bool(scenes_to_do) and scene.scene_number == scenes_to_do[0].scene_number
                both_ref = identity_refs.get("both")
                both_img = img_dir / "both.png"
                two_shot = bool(get_settings().establishing_two_shot and is_first
                                and (both_ref or both_img.exists()))

                if two_shot:
                    # Primary = the composed 'both' image; the two singles ride along
                    # as secondary identity anchors so each face stays exact.
                    ordered_refs, ordered_files = [], []
                    for r_role, r_img in (("both", both_img),
                                          (speaker_role, img_dir / f"{speaker_role}.png"),
                                          (listener_role, img_dir / f"{listener_role}.png")):
                        r_ref = identity_refs.get(r_role)
                        if r_ref:
                            ordered_refs.append(r_ref)
                        elif r_img.exists():
                            ordered_files.append(r_img)
                    image_refs_arg = ordered_refs or None
                    image_files_arg = ordered_files or None
                    want_reaction = False
                    cam = (scene.camera_movement or "slow push-in").strip()  # NOT sanitized: two-shot is intended here
                    _update(job, step=f"Scene {scene.scene_number}: ESTABLISHING TWO-SHOT (both on camera)")
                    vid_prompt = None
                    if get_settings().use_director_skills:
                        vid_prompt = await director_skills.video_prompt(
                            _two_shot_concept_brief(scene, speaker_role, listener_role, duration, cam),
                            _two_shot_enforcement(scene, speaker_role, listener_role),
                            bilingual=get_settings().seedance_bilingual_prompt,
                        )
                        if vid_prompt:
                            _update(job, step=f"Scene {scene.scene_number}: two-shot prompt written by seedance director skill")
                    if not vid_prompt:
                        vid_prompt = _two_shot_video_prompt(scene, studio, speaker_role, listener_role)
                else:
                    # BOTH identity images go into EVERY single-person clip: the
                    # speaker first (primary subject / start_image, lip-syncs to the
                    # audio) and the listener second (secondary reference, available
                    # for a brief in-clip reaction cut). Locked refs are reused
                    # directly — no re-upload, no quality loss, no last-frame
                    # chaining. If a ref is missing we fall back to the local PNG.
                    ordered_refs, ordered_files = [], []
                    for r_role in (speaker_role, listener_role):
                        r_ref = identity_refs.get(r_role)
                        r_img = img_dir / f"{r_role}.png"
                        if r_ref:
                            ordered_refs.append(r_ref)
                        elif r_img.exists():
                            ordered_files.append(r_img)
                    image_refs_arg = ordered_refs or None
                    image_files_arg = ordered_files or None

                    # Ask for a brief in-clip reaction glimpse of the listener only
                    # when the turn is long enough to warrant a cutaway (config-gated).
                    react_threshold = get_settings().reaction_min_seconds
                    want_reaction = bool(
                        get_settings().reaction_shots_enabled and react_threshold
                        and target_sec >= react_threshold
                    )

                    _update(job, step=(f"Scene {scene.scene_number}: single talking head (full quality"
                                       f"{', with listener reaction beat' if want_reaction else ''})"))
                    cam = _sanitize_camera(scene.camera_movement)
                    # The seedance director skill writes the creative prompt; our
                    # enforcement suffix locks single-person framing + precise
                    # lip-sync + identity + studio + the one reaction beat. Falls
                    # back to the built-in prompt if the skill is off or returns
                    # nothing, so a flaky prompt step never blocks a paid render.
                    vid_prompt = None
                    if get_settings().use_director_skills:
                        vid_prompt = await director_skills.video_prompt(
                            _video_concept_brief(scene, speaker_role, listener_role, want_reaction, duration, cam),
                            _video_enforcement(scene, speaker_role, listener_role, want_reaction),
                            bilingual=get_settings().seedance_bilingual_prompt,
                        )
                        if vid_prompt:
                            _update(job, step=f"Scene {scene.scene_number}: prompt written by seedance director skill")
                    if not vid_prompt:
                        vid_prompt = _scene_video_prompt(scene, studio, speaker_role, listener_role, want_reaction)

                # Persist the exact prompt used, for inspection and reproducible retries.
                try:
                    (vid_dir / f"scene_{scene.scene_number:02d}.prompt.txt").write_text(vid_prompt, encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass
                gen = await mcp_client.generate(
                    "video",
                    prompt=vid_prompt,
                    model_hint="seedance",
                    image_refs=image_refs_arg,
                    image_files=image_files_arg,
                    audio_files=audio_files,
                    duration=duration,
                    aspect_ratio=s.hf_aspect_ratio,
                    resolution="720p",
                )
                await _download(gen["url"], final_clip)  # keep native synced audio; no trim
                prev_clip = final_clip
                prev_role = role
                scene_files.append(final_clip)
                job["scenes"].append({
                    "scene_number": scene.scene_number,
                    "speaker": speaker_role,
                    "reaction_of": listener_role if want_reaction else None,
                    "video_url": f"/renders/{render_id}/video/{final_clip.name}",
                })
            else:
                res = await _hf_subscribe(s.hf_video_model, {
                    "prompt": _scene_video_prompt(scene, studio),
                    "image_url": identity_urls[role],
                    "duration": duration,
                    "resolution": s.hf_video_resolution,
                    "aspect_ratio": s.hf_aspect_ratio,
                    "camera_fixed": _is_static_camera(scene.camera_movement),
                })
                raw = vid_dir / f"scene_{scene.scene_number:02d}_raw.mp4"
                await _download(_first_url(res), raw)
                _update(job, step=f"Scene {scene.scene_number}: trimming to {target_sec:.1f}s")
                _trim_clip(raw, final_clip, min(target_sec, duration))
                raw.unlink()
                scene_files.append(final_clip)
                job["scenes"].append({
                    "scene_number": scene.scene_number,
                    "video_url": f"/renders/{render_id}/video/{final_clip.name}",
                })

        # 4 · merge
        _update(job, step="Merging clips into final reel (ffmpeg)")
        merged = vid_dir / "merged_reel.mp4"
        if use_mcp:
            # MCP clips carry native lip-synced audio; concat A/V, fallback to reel audio
            try:
                _merge_av(scene_files, merged)
            except RuntimeError:
                _merge_with_reel_audio(scene_files, _find_reel_audio(base_dir), merged)
        else:
            _merge_with_reel_audio(scene_files, _find_reel_audio(base_dir), merged)
        job["merged_url"] = f"/renders/{render_id}/video/{merged.name}"

        # 5 · thumbnail — two-shot image for the reel cover (never a video scene)
        try:
            import shutil as _sh
            thumb_src = None
            for cand in (img_dir / "both.png", img_dir / "host.png"):
                if cand.exists():
                    thumb_src = cand
                    break
            if thumb_src:
                thumb = base_dir / "thumbnail.png"
                _sh.copyfile(thumb_src, thumb)
                job["thumbnail_url"] = f"/renders/{render_id}/{thumb.name}"
                _update(job, step=(f"Thumbnail saved from the {'two-shot' if thumb_src.name == 'both.png' else 'host'} "
                                   f"image ({thumb.name})"))
            else:
                _update(job, step="No image available for a thumbnail (skipped).")
        except Exception as e:  # noqa: BLE001 — thumbnail must never fail the reel
            logger.warning("thumbnail generation failed: %s", e)

        # 6 · GDrive upload — raw scene clips only (no merged reel, thumbnail, or audio)
        if get_settings().upload_to_gdrive:
            remote = (get_settings().rclone_remote or "").strip()
            if not remote:
                _update(job, step="GDrive upload is ON but RCLONE_REMOTE is empty — skipped.")
            else:
                prefix = get_settings().gdrive_clip_prefix or "RawClip"
                ordered_scenes = sorted(vid_dir.glob("scene_*.mp4"))
                items: list[tuple[Path, str]] = [
                    (clip, f"{prefix}{i}{clip.suffix}")
                    for i, clip in enumerate(ordered_scenes, start=1)
                ]
                if not items:
                    _update(job, step="GDrive upload skipped — no scene clips found.")
                else:
                    dest = remote
                    _update(job, step=(f"Uploading {len(items)} scene clip(s) to GDrive → {dest} "
                                       f"({prefix}1..{prefix}{len(items)})"))
                    ok, log = await _rclone_upload_all(items, dest, rclone_exe=get_settings().rclone_exe)
                    job["gdrive_upload"] = {"status": "ok" if ok else "failed", "dest": dest,
                                            "files": [n for _, n in items], "log": log[-600:]}
                    if ok:
                        _update(job, step=f"Uploaded {len(items)} scene clip(s) to GDrive ({dest}).")
                        if get_settings().gdrive_delete_local_after_upload:
                            removed = 0
                            for src, _name in items:
                                try:
                                    src.unlink()
                                    removed += 1
                                except Exception:  # noqa: BLE001
                                    pass
                            _update(job, step=f"Removed {removed} local scene clip(s) after confirmed upload.")
                    else:
                        _update(job, step=f"GDrive upload FAILED — clips kept locally. rclone says: {log[-300:]}")

        _update(job, status="completed", step="Done")
    except Exception as e:  # noqa: BLE001 — job must capture any failure
        logger.exception("Video job %s failed", job_id)
        _update(job, status="failed", step="Failed", error=str(e))


async def _rclone_upload_all(items: list[tuple[Path, str]], dest: str, *,
                             rclone_exe: str = "rclone") -> tuple[bool, str]:
    """Upload files to the rclone destination in one command. Returns (ok, log)."""
    import shutil
    import tempfile

    items = [(Path(s), n) for (s, n) in items if s and Path(s).exists()]
    if not items:
        return False, "no files to upload"
    stage = Path(tempfile.mkdtemp(prefix="rclone_stage_"))
    try:
        for src, name in items:
            shutil.copy2(src, stage / name)
        cmd = [rclone_exe, "copy", str(stage), dest,
               "--transfers", "8", "--checkers", "8",
               "--drive-chunk-size", "64M", "--no-traverse", "-v"]
        env = os.environ.copy()
        _cfg = (get_settings().rclone_config or "").strip()
        if _cfg:
            env["RCLONE_CONFIG"] = _cfg
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env)
        out, _ = await proc.communicate()
        log = (out or b"").decode("utf-8", "replace")[-4000:]
        return proc.returncode == 0, (log or f"rclone exit {proc.returncode}")
    except FileNotFoundError:
        return False, (f"rclone executable not found ('{rclone_exe}'). Install rclone or set "
                       f"RCLONE_EXE to its full path.")
    except Exception as e:  # noqa: BLE001
        return False, f"rclone failed to launch: {e}"
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def clear_identity_cache(role: str | None = None) -> list[str]:
    cleared = []
    roles = [role] if role else ["host", "guest", "both"]
    for r in roles:
        for f in (IDENTITY_CACHE_DIR / f"{r}.json", IDENTITY_CACHE_DIR / f"{r}.png"):
            if f.exists():
                f.unlink(); cleared.append(f.name)
    return cleared


def identity_cache_status() -> dict:
    out = {}
    for r in ("host", "guest", "both"):
        meta = IDENTITY_CACHE_DIR / f"{r}.json"
        out[r] = json.loads(meta.read_text(encoding="utf-8")).get("ref") if meta.exists() else None
    return out


def start_job(render_id: str, blueprint: SceneBlueprint, force_regen_identity: bool = False, force_regen_scenes: bool = False) -> str:
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {
        "job_id": job_id, "render_id": render_id,
        "status": "queued", "step": "Queued",
        "scenes": [], "images": {}, "merged_url": None, "error": None, "log": [],
        "cost_estimate": 0, "force_regen_identity": force_regen_identity,
        "force_regen_scenes": force_regen_scenes,
    }
    asyncio.get_event_loop().create_task(run_video_job(job_id, render_id, blueprint))
    return job_id
