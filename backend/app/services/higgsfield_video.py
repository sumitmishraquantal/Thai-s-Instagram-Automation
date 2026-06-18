# """Higgsfield video generation pipeline — Milestone 3 (verified API schemas).

# Platform reality (platform.higgsfield.ai, June 2026):
#   - Seedance 2.0 is NOT yet on the API — Seedance v1 Lite/Pro Fast only.
#   - soul/reference takes exactly ONE reference image (prompt + image_reference_url).
#   - seedance i2v takes exactly ONE start image (prompt + image_url), no audio input,
#     no video chaining. duration 2-12s, resolution 480/720/1080, aspect 9:16 supported.

# Consistency strategy under these constraints:
#   1. Soul Reference turns YOUR Ron photo into "Ron in the studio armchair" once,
#      and YOUR Jason photo into "Jason in the studio armchair" once.
#   2. Every scene where Ron speaks starts from the SAME Ron image; same for Jason.
#      Identical start frames = locked identity, wardrobe, set across the reel.
#   3. Each clip is trimmed to the exact segment duration, concatenated, and the
#      full ElevenLabs reel audio is laid underneath (no API lip-sync available yet;
#      mouth motion is prompt-driven "speaking" animation).

# Outputs in backend/renders/<render_id>/: images/host.png, images/guest.png,
# video/scene_XX.mp4, video/merged_reel.mp4
# """
# import asyncio
# import json
# import logging
# import math
# import os
# import re
# import subprocess
# import uuid
# from pathlib import Path
# from typing import Any

# import httpx

# from ..config import get_settings
# from ..schemas import Scene, SceneBlueprint
# from . import director_skills, higgsfield_mcp, image_prep, llm
# from .render import RENDERS_DIR

# logger = logging.getLogger(__name__)

# JOBS: dict[str, dict] = {}

# ASSETS_DIR = RENDERS_DIR.parent / "assets" / "characters"
# ASSETS_DIR.mkdir(parents=True, exist_ok=True)

# _EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
# _PHOTO_EXTS = (".png", ".jpg", ".jpeg", ".webp")

# # Identity images are expensive to imply per-reel, but the characters never change.
# # Cache the generated studio identity image + its reusable Higgsfield ref, keyed on
# # a hash of (source photo bytes + studio description + model). Reused across all jobs
# # until the photo or studio canon changes, or force_regen is set.
# IDENTITY_CACHE_DIR = ASSETS_DIR.parent / "identity_cache"
# IDENTITY_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# def _identity_signature(photo: Path, studio: str, model: str) -> str:
#     import hashlib
#     h = hashlib.sha256()
#     h.update(photo.read_bytes())
#     h.update(studio.encode("utf-8"))
#     h.update(model.encode("utf-8"))
#     return h.hexdigest()[:16]


# def _cache_stem(role: str, key: str = "") -> str:
#     """Cache filename stem. With a variation profile key, each look is cached
#     separately (role__profile) so a new reel's profile regenerates while a repeat
#     profile reuses; without a key it's the legacy single-look stem (role)."""
#     return f"{role}__{key}" if key else role


# def _identity_cache_load(role: str, signature: str, key: str = "") -> dict | None:
#     stem = _cache_stem(role, key)
#     meta = IDENTITY_CACHE_DIR / f"{stem}.json"
#     png = IDENTITY_CACHE_DIR / f"{stem}.png"
#     if meta.exists() and png.exists():
#         try:
#             data = json.loads(meta.read_text(encoding="utf-8"))
#             # A user-locked identity (set via lock_character_image.py) is reused
#             # regardless of studio/script signature — it's a deliberate pin.
#             if data.get("locked") and data.get("ref"):
#                 return data
#             if data.get("signature") == signature and data.get("ref"):
#                 return data
#         except Exception:  # noqa: BLE001
#             return None
#     return None


# def _identity_cache_save(role: str, signature: str, url: str, ref: str, src_png: Path,
#                           locked: bool = False, key: str = ""):
#     import shutil
#     stem = _cache_stem(role, key)
#     dest_png = IDENTITY_CACHE_DIR / f"{stem}.png"
#     if Path(src_png).resolve() != dest_png.resolve():
#         shutil.copyfile(src_png, dest_png)
#     (IDENTITY_CACHE_DIR / f"{stem}.json").write_text(
#         json.dumps({"signature": signature, "url": url, "ref": ref, "locked": locked}, indent=2),
#         encoding="utf-8",
#     )

# SEEDANCE_MIN_DUR, SEEDANCE_MAX_DUR = 2, 12

# _VALID_VIDEO_RES = {"480p", "720p", "1080p"}


# def _norm_video_resolution(value: str) -> str:
#     """Coerce a config/env resolution into a value Seedance accepts. Seedance only
#     allows 480p / 720p / 1080p (the trailing 'p' is REQUIRED), so '1080' -> '1080p'.
#     Anything unrecognized falls back to 720p rather than erroring the whole clip."""
#     v = (value or "").strip().lower()
#     if v and not v.endswith("p") and v.isdigit():
#         v = v + "p"
#     return v if v in _VALID_VIDEO_RES else "720p"


# # ── Character photo storage ───────────────────────────────
# def _character_path(role: str) -> Path | None:
#     """Find the character photo regardless of extension (.png/.jpg/.jpeg/.webp,
#     any case)."""
#     for p in sorted(ASSETS_DIR.glob(f"{role}.*")):
#         if p.suffix.lower() in _PHOTO_EXTS:
#             return p
#     return None


# def save_character(role: str, data: bytes, content_type: str):
#     old = _character_path(role)
#     if old:
#         old.unlink()
#     (ASSETS_DIR / f"{role}{_EXT[content_type]}").write_bytes(data)


# def character_status() -> dict:
#     out = {}
#     for role in ("host", "guest"):
#         p = _character_path(role)
#         out[role] = f"/assets/{p.name}" if p else None
#     return out


# # ── SDK helpers ───────────────────────────────────────────
# def _ensure_credentials():
#     s = get_settings()
#     if not s.hf_api_key or not s.hf_api_secret:
#         raise RuntimeError("HF_API_KEY / HF_API_SECRET not set in backend/.env")
#     os.environ["HF_API_KEY"] = s.hf_api_key
#     os.environ["HF_API_SECRET"] = s.hf_api_secret


# async def _hf_subscribe(endpoint: str, arguments: dict) -> dict:
#     import higgsfield_client
#     _ensure_credentials()
#     logger.info("Higgsfield call %s | args keys: %s", endpoint, list(arguments.keys()))
#     return await higgsfield_client.subscribe_async(endpoint, arguments=arguments)


# _CONTENT_TYPES = {
#     ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
#     ".gif": "image/gif", ".webp": "image/webp",
#     ".wav": "audio/wav",
# }


# async def _hf_upload(path: Path) -> str:
#     """Upload a file to Higgsfield, forcing the correct content-type.
#     Higgsfield rejects audio/mpeg — audio must be wav. Images: png/jpeg/webp/gif."""
#     import higgsfield_client
#     _ensure_credentials()
#     ext = path.suffix.lower()
#     ctype = _CONTENT_TYPES.get(ext)
#     if ctype is None:
#         # never upload mp3/unknown; convert mp3 to wav upstream before calling this
#         raise RuntimeError(f"Unsupported upload type '{ext}' for {path.name}; "
#                            f"Higgsfield accepts png/jpeg/webp/gif images and wav audio.")
#     data = path.read_bytes()
#     return await higgsfield_client.upload_async(data, ctype)


# def _first_url(result: dict, *keys: str) -> str:
#     """Tolerant media-URL extraction across response shapes."""
#     if isinstance(result, str) and result.startswith("http"):
#         return result
#     for k in (*keys, "images", "image", "video", "videos", "output", "result", "url"):
#         v = result.get(k) if isinstance(result, dict) else None
#         if isinstance(v, dict):
#             if v.get("url"):
#                 return v["url"]
#         if isinstance(v, list) and v:
#             first = v[0]
#             if isinstance(first, dict) and first.get("url"):
#                 return first["url"]
#             if isinstance(first, str) and first.startswith("http"):
#                 return first
#         if isinstance(v, str) and v.startswith("http"):
#             return v
#     raise ValueError(f"No media URL found in result: {str(result)[:300]}")


# async def _download(url: str, dest: Path):
#     async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
#         r = await client.get(url)
#         r.raise_for_status()
#         dest.write_bytes(r.content)


# # The UNCHANGING room structure — same in every reel. The specific poster art,
# # decor objects and wardrobe are NOT fixed here; they come from a per-reel variation
# # profile (below) so different reels look fresh while staying consistent within a reel.
# _STUDIO_STRUCTURE = (
#     "a modern in-person podcast studio: a tall matte-black built-in bookshelf-and-cabinet back wall with "
#     "warm interior LED accent lighting, glass-front upper cabinets and cane/rattan-front lower cabinets; the "
#     "people sit in mid-century grey upholstered armchairs with wooden frames; a black dynamic microphone on a "
#     "black boom arm reaches in from the side; a neutral area rug on a dark wood floor; warm, moody, low-key "
#     "cinematic lighting"
# )

# # Per-reel VARIATION PROFILES. Each new reel rotates to the next profile so the wall
# # art, the objects on the shelves and the speakers' clothing change between reels —
# # while the room, the faces and (within one reel) everything stay consistent. Faces
# # are always preserved from the real reference photo; only background + wardrobe vary.
# _VARIATION_PROFILES = [
#     {
#         "id": "modern_abstract",
#         "poster": "a large framed original abstract painting with bold muted color blocks and gestural brush "
#                   "strokes — NO text, NO words, no logos and no recognizable artwork",
#         "decor": "stacked plain hardcover books, a small green potted plant, a smooth ceramic vase and a "
#                  "polished live-edge wood-slice sculpture on a stand",
#         "wardrobe": {"host": "a plain charcoal crew-neck sweater (no logos)",
#                      "guest": "a plain black short-sleeve polo shirt (no logos or text)"},
#     },
#     {
#         "id": "nature_photo",
#         "poster": "a large framed black-and-white nature photograph of misty forest trees in a thin matte-black "
#                   "frame — NO text, no words, no logos",
#         "decor": "a softly glowing geode lamp, a small succulent, a folded wool throw and a few plain books",
#         "wardrobe": {"host": "a plain dark navy button-down shirt (no logos)",
#                      "guest": "a plain black quarter-zip sweater (no logos)"},
#     },
#     {
#         "id": "line_art",
#         "poster": "a large framed minimalist single-line botanical line-art print in black ink on a cream "
#                   "background — abstract lines only, NO words, no logos",
#         "decor": "a matte black ceramic vase with dried pampas grass, a short stack of plain design books and "
#                  "a small abstract brass sculpture",
#         "wardrobe": {"host": "a plain slate-grey henley (no logos)",
#                      "guest": "a plain dark olive crew-neck sweater (no logos)"},
#     },
#     {
#         "id": "calm_typography",
#         "poster": "a large framed minimalist poster showing a single common everyday word in clean white "
#                   "uppercase sans-serif on a plain dark background — a generic ordinary word only, NOT a brand "
#                   "name, slogan, quote, book title, song lyric or any recognizable phrase",
#         "decor": "a potted fern, a plain ceramic mug, a small stack of unbranded notebooks and a smooth river "
#                  "stone",
#         "wardrobe": {"host": "a plain black crew-neck long-sleeve sweater (no logos)",
#                      "guest": "a plain dark charcoal polo shirt (no logos or text)"},
#     },
# ]
# _VARIATION_STATE = IDENTITY_CACHE_DIR / "variation_state.json"


# def _profile_by_id(pid: str) -> dict | None:
#     return next((p for p in _VARIATION_PROFILES if p["id"] == pid), None)


# def _select_variation_profile() -> dict | None:
#     """Pick the variation profile for THIS reel. 'auto' rotates to the next profile
#     each render (so consecutive reels differ); a specific id pins one; 'none' or
#     variation off returns None (→ the legacy single fixed look)."""
#     s = get_settings()
#     if not getattr(s, "vary_across_reels", True):
#         return None
#     choice = (getattr(s, "variation_profile", "auto") or "auto").strip().lower()
#     if choice in ("none", "off", ""):
#         return None
#     if choice != "auto":
#         return _profile_by_id(choice)  # pinned profile (or None if unknown)
#     # auto-rotate via a small persistent counter
#     n = 0
#     try:
#         n = int(json.loads(_VARIATION_STATE.read_text(encoding="utf-8")).get("n", 0))
#     except Exception:  # noqa: BLE001
#         n = 0
#     profile = _VARIATION_PROFILES[n % len(_VARIATION_PROFILES)]
#     try:
#         _VARIATION_STATE.write_text(json.dumps({"n": n + 1}), encoding="utf-8")
#     except Exception:  # noqa: BLE001
#         pass
#     return profile


# def _studio_for_profile(profile: dict | None) -> str:
#     """The full studio description used for IMAGE generation: fixed structure plus
#     this reel's specific poster art and decor objects."""
#     if not profile:
#         return (_STUDIO_STRUCTURE + "; on the back wall, a large framed original abstract art print with NO "
#                 "text or logos; stacked plain books, small plants and tasteful unbranded decor on the shelves")
#     return (f"{_STUDIO_STRUCTURE}; on the back wall, {profile['poster']}; on and around the shelves, "
#             f"{profile['decor']}")


# # A FIXED studio reference used by the VIDEO prompts/enforcement. It is deliberately
# # poster-agnostic: it defers to the per-reel identity image so a reel stays internally
# # consistent (same poster/decor/wardrobe as that reel's locked image) without pinning
# # one specific poster across all reels.
# _FIXED_SET = (
#     "SAME EXACT studio in every shot of this reel — " + _STUDIO_STRUCTURE + ". Keep the wall art/posters, the "
#     "framed pieces, the objects on the shelves AND each person's clothing EXACTLY as they appear in the "
#     "supplied reference image — do not invent different posters, different decor or different clothes. "
#     "Everything matches the reference image so all scenes look like one continuous recording in the same room."
# )

# # Short canonical studio phrase for the (token-limited) director briefs.
# _STUDIO_SHORT = (
#     "the same fixed studio as the reference image — matte-black bookshelf/cabinet wall with warm LED accent "
#     "lighting, mid-century grey upholstered armchairs, a black boom microphone, neutral rug, warm moody "
#     "lighting; keep the SAME wall art, decor and clothing as the reference image"
# )

# # ── Fixed seating canon (viewer's perspective) ────────────
# # The seating is a FACT of the real set and never changes between clips: the GUEST
# # sits on the LEFT of frame, the HOST sits on the RIGHT. Everything directional
# # (which way a speaker faces, where their eyes go) is DERIVED from this, so it is
# # deterministic — never invented per scene. A person on the right looks LEFT toward
# # the other; a person on the left looks RIGHT.
# SEAT_SIDE = {"host": "right", "guest": "left"}

# # Hard rule against Seedance burning ANY text into the picture. Seedance is
# # Chinese-origin and will sometimes render subtitles/captions (often Chinese)
# # unless explicitly forbidden; this is repeated in every video layer.
# _NO_TEXT = (
#     "ABSOLUTELY NO on-screen text of any kind anywhere in the frame: no captions, no subtitles, no closed "
#     "captions, no burned-in words, no karaoke text, no lower-thirds, no titles, no name tags, no caption "
#     "bar, no watermark, no logos, and no Chinese or English characters rendered into the video. The frame "
#     "shows ONLY the filmed scene — clean image, zero graphics or text overlays."
# )

# # Hard rule against rendering anyone's intellectual property. Real generative
# # platforms (Higgsfield included) refuse a job with "IP detected" if the prompt or
# # the reference image contains a brand logo, trademark, real book/album title,
# # slogan or recognizable copyrighted artwork. Since WE produce the image that is fed
# # to the video step, we forbid IP at the source.
# _NO_IP = (
#     "Do NOT depict any brand logos, trademarks, company names, sports/team marks, real book, album or movie "
#     "titles, advertising slogans, song lyrics, famous quotes, or any recognizable copyrighted artwork or "
#     "character — anywhere in the frame, including on clothing, posters, wall art, mugs, books, screens or "
#     "props. Use only generic, original, unbranded designs; any clothing must be plain with no visible logos "
#     "or text."
# )


# def _other_dir(side: str) -> str:
#     """The direction someone on `side` must look to face the other person."""
#     return "left" if side == "right" else "right"


# def _seat_phrase(role: str) -> str:
#     """e.g. 'seated on the RIGHT side of the frame (the host's fixed seat)'."""
#     side = SEAT_SIDE.get(role, "right")
#     return f"seated on the {side.upper()} side of the frame (the {role}'s fixed seat)"


# def _eyeline_phrase(speaker: str, listener: str) -> str:
#     """Deterministic eye-line: the speaker turns toward the listener's fixed side."""
#     s_side = SEAT_SIDE.get(speaker, "right")
#     look = _other_dir(s_side)                       # direction to face the other person
#     l_side = SEAT_SIDE.get(listener, _other_dir(s_side))
#     return (f"the {speaker} is {_seat_phrase(speaker)} and turns to look toward their {look} "
#             f"(screen-{look}), where the {listener} sits ({l_side} of frame), NOT into the camera")


# # ── Prompt building ───────────────────────────────────────
# def _wardrobe_for(profile: dict | None, role: str) -> str | None:
#     if not profile:
#         return None
#     return (profile.get("wardrobe") or {}).get(role)


# def _identity_image_prompt(role: str, studio: str, profile: dict | None = None) -> str:
#     set_desc = _studio_for_profile(profile)
#     if role == "both":
#         wh = _wardrobe_for(profile, "host")
#         wg = _wardrobe_for(profile, "guest")
#         clothes = (f" Dress the HOST in {wh} and the GUEST in {wg}, but keep BOTH people's faces, hair, "
#                    f"beards and build EXACTLY as in the reference photo." if (wh and wg) else
#                    " Keep BOTH people's faces, hair, beards, build and their black/dark clothing exactly as shown.")
#         return (
#             f"The TWO exact people from the reference photo together as podcast co-hosts, seated SIDE BY SIDE "
#             f"in two separate mid-century grey upholstered armchairs angled slightly toward each other — the "
#             f"GUEST on the LEFT of frame, the HOST on the RIGHT (viewer's perspective) — both fully visible in "
#             f"one balanced two-shot. {set_desc}. A black dynamic microphone on a black boom arm reaches in "
#             f"toward each of them from the side, NO headphones (in-person conversation), relaxed natural "
#             f"posture, mid-conversation.{clothes} Do not swap or merge their identities. Wide-to-medium "
#             f"vertical 9:16 two-shot, photographic and lifelike (natural skin texture, visible pores, "
#             f"catchlights), warm moody low-key lighting, true-to-life colour, sharp focus. No on-screen text, "
#             f"no watermark, no logos, NOT a 3D render, NOT illustration, NOT anime. " + _NO_IP
#         )
#     label = "HOST" if role == "host" else "GUEST"
#     w = _wardrobe_for(profile, role)
#     clothes = (f"Keep the person's face, hair, beard and build EXACTLY as in the reference photo, but dress "
#                f"them in {w} (their clothing for this episode)." if w else
#                "Keep the person's face, hair, beard, build and their black/dark clothing exactly as in the "
#                "reference photo.")
#     return (
#         f"Faithfully reproduce this exact person as a podcast {label}, seated in a mid-century grey "
#         f"upholstered armchair with a wooden frame, framed as a SINGLE-PERSON SHOT — ONLY this one person is "
#         f"visible in frame, no second person, no one sitting opposite. {set_desc}. A black dynamic microphone "
#         f"on a black boom arm reaches in from the side, NO headphones (this is an in-person conversation). The "
#         f"person is {_seat_phrase(role)}, body and gaze turned toward their "
#         f"{_other_dir(SEAT_SIDE.get(role, 'right'))} (screen-{_other_dir(SEAT_SIDE.get(role, 'right'))}) where "
#         f"the other person sits off-camera, hands resting naturally. {clothes} Looking toward the other person "
#         f"off-camera (not into the lens). Vertical 9:16 composition. "
#         f"PHOTOREALISTIC like a real photograph of a real human shot on a cinema camera with a 50mm lens and "
#         f"shallow depth of field; lifelike skin with natural texture, visible pores and subtle subsurface "
#         f"tones (never airbrushed, plastic or waxy); catchlights in the eyes; warm moody studio lighting; "
#         f"true-to-life colour. Sharp focus, no text, no watermark, "
#         f"no logos, NOT a 3D render, NOT illustration, NOT anime/cartoon. " + _NO_IP
#     )


# def _sanitize_camera(cam: str) -> str:
#     """Never allow a two-shot / two-person framing to slip in from the planner —
#     the whole reel is strictly one person on camera at a time."""
#     c = (cam or "static medium shot, slight handheld sway").strip()
#     c = re.sub(r"two[\s\-]?shot", "single-person medium shot", c, flags=re.I)
#     c = re.sub(r"\b(both|two)\s+(people|persons|of them)\b", "the speaker", c, flags=re.I)
#     return c


# def _scene_video_prompt(scene: Scene, studio: str, speaker_role: str,
#                         listener_role: str, want_reaction: bool) -> str:
#     """Single-clip prompt. Two reference images are supplied to the model: the
#     FIRST is the speaker (primary subject, lip-syncs to the audio), the SECOND is
#     the listener (used only for an optional brief in-clip reaction cut). The hard
#     rule baked into the prompt: only ONE person is ever on screen at any instant —
#     never a two-shot. Everything (gesture, tone, expression, eye-line, optional
#     reaction) is driven by the script fields of THIS scene."""
#     speaker = speaker_role.lower()
#     listener = listener_role.lower()
#     cam = _sanitize_camera(scene.camera_movement)
#     body = (scene.body_language or "").strip()
#     eyes = (scene.eye_contact or "natural engaged expression, with occasional brief glances down "
#             "or to the microphone in thought").strip()
#     tone = (scene.emotional_tone or "").strip()
#     humor = (scene.humor or "").strip()
#     cue = (scene.reaction_cue or "listening intently and nodding slowly, attentive and engaged").strip()

#     parts = [
#         # Subjects + the iron framing rule
#         f"A photorealistic in-person podcast clip, all filmed in ONE studio. TWO reference images are "
#         f"provided: the FIRST reference person is the {speaker} (the speaker in this clip); the SECOND "
#         f"reference person is the {listener} (the listener). They are two different real people. "
#         f"CRITICAL FRAMING RULE: only ONE person is ever visible in the frame at any single moment — this "
#         f"is a tight single-person vertical shot. NEVER show both people in the same frame; no two-shot, "
#         f"no split-screen, no second person seated or blurred in the background.",

#         # The speaking action, hard lip-sync to the audio
#         f"Primary action: the {speaker} is mid-conversation, speaking into the studio microphone. The mouth "
#         f"movements must EXACTLY and precisely lip-sync to the provided audio track — match every word, "
#         f"syllable and pause, and say ONLY what the audio says (never invent or mouth words that are not in "
#         f"the audio). {scene.character_action}.",

#         f"Facial expression: {scene.facial_expression.lower()}.",
#     ]
#     if body:
#         parts.append(f"Body language: {body}.")
#     if tone:
#         parts.append(f"Emotional tone of the delivery: {tone}.")
#     if humor:
#         parts.append(f"Subtle, natural touch (only if it fits the moment): {humor}.")
#     parts.append(
#         f"Position & eye contact: {_eyeline_phrase(speaker, listener)}. {eyes}. "
#         f"This is a real in-person conversation — no fixed camera stare."
#     )

#     if want_reaction:
#         parts.append(
#             f"REACTION BEAT (still single-person — no two-shot): for a brief moment, about 1 to 2 seconds in "
#             f"the middle of the clip, cut to the {listener} ALONE in frame (use the SECOND reference image), "
#             f"{_seat_phrase(listener)}, looking toward their {_other_dir(SEAT_SIDE.get(listener, 'left'))} "
#             f"(screen-{_other_dir(SEAT_SIDE.get(listener, 'left'))}) where the {speaker} sits off-camera — "
#             f"{cue}; mouth closed, NOT speaking, just a natural listening reaction such as a slow nod or a small "
#             f"empathetic look — then cut back to the {speaker} speaking. The audio keeps playing throughout (it is "
#             f"the {speaker}'s voice); the {listener} never talks. Even during this brief cut, only ONE person is on "
#             f"screen, and it is the same studio and lighting."
#         )

#     # Identity + set lock
#     parts.append(
#         f"Preserve each person's exact face, hair, beard, skin tone and their black/dark clothing from their own "
#         f"reference image with zero identity changes. No headphones. They sit in mid-century grey upholstered "
#         f"armchairs. " + _FIXED_SET + " Do not change the room, the cabinetry, the books, the posters, the decor "
#         f"or the lighting."
#     )
#     # Realism
#     parts.append(
#         f"Camera: {cam}; single continuous take with a natural handheld feel. ULTRA-PHOTOREALISTIC: shot on a "
#         f"professional cinema camera with a 50mm lens and shallow depth of field; lifelike skin with natural "
#         f"texture and visible pores (never plastic, waxy or airbrushed), catchlights in the eyes, realistic "
#         f"blinking and micro-expressions, natural subtle head and shoulder motion."
#     )
#     # Negative guidance
#     parts.append(
#         f"Negative: do NOT change the people, do NOT change the location; no two people in one frame, no second "
#         f"person in the background, no headphones, no cartoon/anime/3D-render look, no plastic or waxy skin, no "
#         f"fixed camera stare. " + _NO_TEXT + " " + _NO_IP
#     )
#     return " ".join(parts)


# # ── Director-skill briefs + enforcement suffixes ──────────
# # The uploaded skills (loaded as SYSTEM prompts) do the *creative* prompt
# # writing; these briefs frame our exact use case for the skill, and the
# # enforcement suffixes lock the non-negotiable technical contract on top
# # (identity, fixed studio, single-person framing, no headphones, lip-sync).
# # They deliberately reuse the same canon (_FIXED_SET) and constraint language as
# # the built-in fallback prompts so directed and fallback prompts stay aligned.
# def _image_concept_brief(role: str, studio: str, profile: dict | None = None) -> str:
#     set_desc = _studio_for_profile(profile)
#     if role == "both":
#         wh, wg = _wardrobe_for(profile, "host"), _wardrobe_for(profile, "guest")
#         wear = (f" Dress the host in {wh} and the guest in {wg}, faces unchanged from the reference."
#                 if (wh and wg) else " Same wardrobe and faces as the reference photo.")
#         return (
#             f"An editorial two-shot photograph of TWO real podcast co-hosts seated SIDE BY SIDE in two "
#             f"separate mid-century grey upholstered armchairs angled slightly toward each other — GUEST on the "
#             f"LEFT of frame, HOST on the RIGHT (viewer perspective) — a black boom microphone reaching in "
#             f"toward each from the side, relaxed and mid-conversation. Both people fully visible in one "
#             f"balanced frame. Vertical 9:16. Keep BOTH faces exactly from the reference photo.{wear} Aim for a "
#             f"believable real-photograph look (film/editorial, not glossy CGI). Studio: {set_desc}"
#         )
#     who = "podcast host" if role == "host" else "podcast guest"
#     w = _wardrobe_for(profile, role)
#     wear = (f" Dress them in {w}; keep the face/hair/beard exactly from the reference photo."
#             if w else " Same face and black/dark wardrobe as the reference photo.")
#     return (
#         f"Single editorial portrait of one real {who} seated in a mid-century grey upholstered armchair in a "
#         f"warm in-person podcast studio, a black boom microphone reaching in from the side, body relaxed and "
#         f"angled slightly toward the other person off to the side (off-camera), looking off to the side (not at "
#         f"the lens). One person only in frame. Vertical 9:16. Keep the face exactly from the reference "
#         f"photo.{wear} Aim for a believable real-photograph look (film/editorial, not glossy CGI). "
#         f"Studio: {set_desc}"
#     )


# def _image_enforcement(role: str, profile: dict | None = None) -> str:
#     set_desc = _studio_for_profile(profile)
#     if role == "both":
#         wh, wg = _wardrobe_for(profile, "host"), _wardrobe_for(profile, "guest")
#         wear = (f" Dress the HOST in {wh} and the GUEST in {wg}; keep BOTH faces, hair, beards and build "
#                 f"EXACTLY from the reference photo." if (wh and wg)
#                 else " Keep BOTH people's faces, hair, beards, build and clothing EXACTLY from the reference.")
#         return (
#             "NON-NEGOTIABLE CONSTRAINTS (override anything above that conflicts): Studio = " + set_desc + "." +
#             wear + " Do not swap, merge or invent identities. A balanced TWO-SHOT: both people fully visible, "
#             "seated SIDE BY SIDE in separate grey armchairs angled slightly toward each other — the GUEST on "
#             "the LEFT of frame and the HOST on the RIGHT (viewer perspective). NO headphones. Vertical 9:16. "
#             "No on-screen text, no captions, no watermark, no logos. Render as a real photograph (natural skin "
#             "texture, visible pores, catchlights), NOT a 3D render, NOT illustration, NOT anime. " + _NO_IP
#         )
#     w = _wardrobe_for(profile, role)
#     wear = (f" Dress the person in {w}, but keep their face, hair, beard and build EXACTLY from the supplied "
#             f"reference image — zero identity changes." if w
#             else " Keep the person's face, hair, beard, build and black/dark clothing EXACTLY from the "
#                  "supplied reference image — zero identity changes.")
#     return (
#         "NON-NEGOTIABLE CONSTRAINTS (override anything above that conflicts): Studio = " + set_desc + "." +
#         wear + " SINGLE-PERSON SHOT: only this one person is visible, no second person, no one sitting "
#         "opposite. They sit in a mid-century grey upholstered armchair. NO headphones. Vertical 9:16. No "
#         "on-screen text, no captions, no watermark, no logos. Render as a real photograph (natural skin "
#         "texture, visible pores, catchlights), NOT a 3D render, NOT illustration, NOT anime. " + _NO_IP
#     )


# def _video_concept_brief(scene: Scene, speaker: str, listener: str,
#                          want_reaction: bool, duration: int, cam: str) -> str:
#     bits = [
#         f"A calm, friendly IN-PERSON podcast conversation clip, about {duration}s, vertical 9:16, single "
#         f"camera, minimal cuts. The {speaker} is the on-camera speaker; the {listener} is the other "
#         f"participant (off-camera). {_eyeline_phrase(speaker, listener)}. This is a relaxed studio chat — not action, not a "
#         f"confrontation.",
#         f"The {speaker}'s spoken audio is SUPPLIED separately — do NOT write any dialogue, narration or "
#         f"subtitles; only describe the visible performance that lip-syncs to that audio. Render a CLEAN frame with NO on-screen text, captions or subtitles burned into the picture.",
#         f"What the {speaker} does on this line: {scene.character_action}.",
#         f"Facial expression: {scene.facial_expression}.",
#     ]
#     if (scene.body_language or "").strip():
#         bits.append(f"Body language: {scene.body_language}.")
#     if (scene.emotional_tone or "").strip():
#         bits.append(f"Emotional tone: {scene.emotional_tone}.")
#     if (scene.humor or "").strip():
#         bits.append(f"Light optional touch (only if it fits): {scene.humor}.")
#     eyes = (scene.eye_contact or "natural engaged expression, brief glances down in thought").strip()
#     bits.append(f"Eye-line: {eyes} (not staring at the camera).")
#     bits.append(f"Camera: {cam}.")
#     if want_reaction:
#         cue = (scene.reaction_cue or "nods slowly, listening, attentive").strip()
#         bits.append(
#             f"Include ONE brief (~1-2s) cut to the {listener} ALONE reacting ({cue}, not speaking), then "
#             f"back to the {speaker}. Never both people in one frame."
#         )
#     else:
#         bits.append("Single continuous take, no cuts.")
#     bits.append(f"Studio: {_STUDIO_SHORT}.")
#     return " ".join(bits)


# def _video_enforcement(scene: Scene, speaker: str, listener: str, want_reaction: bool) -> str:
#     parts = [
#         "NON-NEGOTIABLE TECHNICAL CONTRACT (override anything above that conflicts):",
#         f"Only ONE person is ever visible at any instant — tight single-person vertical shot. NEVER show "
#         f"both people in the same frame; no two-shot, no split-screen, no second person in the background.",
#         f"The {speaker} is the primary subject (FIRST reference image). The mouth movements must EXACTLY "
#         f"lip-sync to the PROVIDED audio track — say only what the audio says, never invent or mouth words "
#         f"not in the audio. Do NOT add any spoken dialogue, narration, subtitles or an Audio section; the "
#         f"audio is supplied separately.",
#         f"FIXED SEATING (viewer's perspective, identical in every clip): the GUEST sits on the LEFT of the "
#         f"frame, the HOST sits on the RIGHT. The on-camera {speaker} is therefore {_seat_phrase(speaker)} and "
#         f"must face/lean toward their {_other_dir(SEAT_SIDE.get(speaker, 'right'))} (screen-"
#         f"{_other_dir(SEAT_SIDE.get(speaker, 'right'))}) toward the off-camera {listener} — never the wrong way, "
#         f"never straight into the lens.",
#     ]
#     if want_reaction:
#         parts.append(
#             f"The ONE permitted cut is a brief ~1-2s glimpse of the {listener} ALONE (SECOND reference "
#             f"image) listening/reacting with mouth closed, then back to the {speaker}; otherwise a single "
#             f"continuous take. Even during this cut, only one person is on screen."
#         )
#     else:
#         parts.append("Single continuous take, no hard cuts.")
#     parts.append(
#         "Preserve each person's exact face, hair, beard, skin tone and clothing from their OWN reference "
#         "image, zero identity changes. NO headphones (they sit face to face). " + _FIXED_SET +
#         " Ultra-real human look: natural skin texture and pores, catchlights, realistic blinking and "
#         "micro-expressions; not plastic, waxy, cartoon, anime or 3D-render; no fixed camera stare. " + _NO_TEXT
#         + " " + _NO_IP
#     )
#     return " ".join(parts)


# def _two_shot_video_prompt(scene: Scene, studio: str, speaker: str, listener: str) -> str:
#     """Built-in fallback prompt for the ESTABLISHING two-shot: both people visible
#     in one frame, the speaker lip-syncing to the supplied audio while the other
#     listens. This is the ONE clip in the reel where two people share the frame."""
#     spk = speaker.lower()
#     lis = listener.lower()
#     return (
#         f"A photorealistic establishing TWO-SHOT of an in-person podcast: BOTH the {spk} and the {lis} are "
#         f"visible together in one frame, seated SIDE BY SIDE in two separate mid-century grey upholstered "
#         f"armchairs angled slightly toward each other — the GUEST on the LEFT of frame, the HOST on the RIGHT "
#         f"(viewer perspective) — in the studio. "
#         f"This is a wide-to-medium vertical 9:16 two-shot that establishes the scene. The {spk} is speaking "
#         f"into the microphone — the {spk}'s mouth movements must lip-sync to the provided audio track (say "
#         f"only what the audio says, never invent words); the {lis} listens and gives small natural nods. "
#         f"{scene.character_action}. Keep BOTH people's exact faces, hair, beards, skin tone and clothing from "
#         f"the reference images with zero identity changes — the {spk} from one reference and the {lis} from "
#         f"the other; do not swap or merge them. No headphones (in-person). " + _FIXED_SET + " Camera: slow gentle "
#         f"push-in, single continuous take. Ultra-real human look: natural skin texture and pores, "
#         f"catchlights, realistic blinking and micro-expressions; not plastic, waxy, cartoon, anime or "
#         f"3D-render. " + _NO_TEXT
#     )


# def _two_shot_concept_brief(scene: Scene, speaker: str, listener: str, duration: int, cam: str) -> str:
#     return (
#         f"An establishing TWO-SHOT for an in-person podcast, about {duration}s, vertical 9:16. BOTH people "
#         f"are visible together in one frame, seated SIDE BY SIDE in separate grey armchairs angled toward each other: the {speaker} is "
#         f"speaking, the {listener} is listening and nodding. This opening shot establishes the room and the "
#         f"pair before the conversation cuts to single talking heads. The {speaker}'s spoken audio is SUPPLIED "
#         f"separately — do NOT write dialogue; only describe the visible performance lip-syncing to it. Keep the frame CLEAN — no captions, subtitles or on-screen text. "
#         f"What the {speaker} does: {scene.character_action}. Camera: {cam}. Keep it a calm studio chat, not "
#         f"action. Studio: {_STUDIO_SHORT}."
#     )


# def _two_shot_enforcement(scene: Scene, speaker: str, listener: str) -> str:
#     return (
#         "NON-NEGOTIABLE TECHNICAL CONTRACT (override anything above that conflicts): This is the ONE "
#         "establishing two-shot — BOTH people are intentionally visible together in one balanced frame, "
#         "seated SIDE BY SIDE in separate grey armchairs angled toward each other — the GUEST on the LEFT of "
#         "frame and the HOST on the RIGHT (viewer perspective). Do NOT add extra people. " +
#         f"The {speaker} is the speaker and lip-syncs EXACTLY to the PROVIDED audio (say only what the audio "
#         f"says, never invent or mouth words; no added dialogue, narration, subtitles or Audio section); the "
#         f"{listener} listens with mouth closed and small natural nods. Preserve BOTH identities exactly from "
#         f"their reference images — the {speaker} from one, the {listener} from the other; never swap or merge "
#         f"faces. NO headphones. " + _FIXED_SET +
#         " Single continuous take, gentle push-in. Ultra-real human look: natural skin texture and pores, "
#         "catchlights, realistic blinking and micro-expressions; not plastic, waxy, cartoon, anime or "
#         "3D-render. " + _NO_TEXT
#     )


# def _is_static_camera(camera_movement: str) -> bool:
#     cm = camera_movement.lower()
#     return any(w in cm for w in ("static", "fixed", "locked", "still"))


# # ── ffmpeg helpers ────────────────────────────────────────
# def _trim_clip(src: Path, dest: Path, seconds: float):
#     r = subprocess.run(
#         ["ffmpeg", "-y", "-i", str(src), "-t", f"{seconds:.2f}",
#          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(dest)],
#         capture_output=True, text=True,
#     )
#     if r.returncode != 0:
#         raise RuntimeError(f"ffmpeg trim failed: {r.stderr[-300:]}")


# def _merge_with_reel_audio(files: list[Path], reel_audio: Path | None, out: Path):
#     n = len(files)
#     inputs: list[str] = []
#     for f in files:
#         inputs += ["-i", str(f)]
#     fc = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1[v]"
#     cmd = ["ffmpeg", "-y", *inputs]
#     if reel_audio:
#         cmd += ["-i", str(reel_audio)]
#     cmd += ["-filter_complex", fc, "-map", "[v]"]
#     if reel_audio:
#         cmd += ["-map", f"{n}:a", "-c:a", "aac", "-shortest"]
#     cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
#     r = subprocess.run(cmd, capture_output=True, text=True)
#     if r.returncode != 0:
#         raise RuntimeError(f"ffmpeg merge failed: {r.stderr[-500:]}")


# def _to_wav(mp3_path: Path) -> Path:
#     """Higgsfield media upload accepts audio/wav, not audio/mpeg. Convert the
#     segment mp3 to a 16-bit PCM wav next to it (cached)."""
#     wav_path = mp3_path.with_suffix(".wav")
#     if wav_path.exists() and wav_path.stat().st_mtime >= mp3_path.stat().st_mtime:
#         return wav_path
#     r = subprocess.run(
#         ["ffmpeg", "-y", "-i", str(mp3_path), "-ar", "44100", "-ac", "2",
#          "-c:a", "pcm_s16le", str(wav_path)],
#         capture_output=True, text=True,
#     )
#     if r.returncode != 0:
#         raise RuntimeError(f"mp3->wav conversion failed: {r.stderr[-300:]}")
#     return wav_path


# def _extract_last_frame(video: Path, out_png: Path) -> Path:
#     """Grab the final frame of a clip as a PNG, for chaining the next scene's
#     start_image so motion continues seamlessly."""
#     # -sseof -0.1 seeks to ~0.1s before the end, then grabs one frame
#     r = subprocess.run(
#         ["ffmpeg", "-y", "-sseof", "-0.2", "-i", str(video),
#          "-vframes", "1", "-q:v", "2", str(out_png)],
#         capture_output=True, text=True,
#     )
#     if r.returncode != 0 or not out_png.exists():
#         # fallback: first-frame-from-end via select
#         r2 = subprocess.run(
#             ["ffmpeg", "-y", "-i", str(video), "-vf",
#              r"select=eq(n\,0)+gte(t\,0)", "-vsync", "0", "-update", "1",
#              str(out_png)], capture_output=True, text=True)
#         if not out_png.exists():
#             raise RuntimeError(f"last-frame extract failed: {r.stderr[-200:]}")
#     return out_png


# def _merge_av(files: list[Path], out: Path):
#     n = len(files)
#     inputs: list[str] = []
#     for f in files:
#         inputs += ["-i", str(f)]
#     fc = "".join(f"[{i}:v][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
#     r = subprocess.run(
#         ["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
#          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
#          "-movflags", "+faststart", str(out)],
#         capture_output=True, text=True,
#     )
#     if r.returncode != 0:
#         raise RuntimeError(f"A/V concat failed: {r.stderr[-300:]}")


# def _find_reel_audio(base_dir: Path) -> Path | None:
#     for p in base_dir.glob("*.mp3"):
#         if not p.name.startswith("segment_"):
#             return p
#     return None


# # ── Job pipeline ──────────────────────────────────────────
# def _update(job: dict, **kw):
#     job.update(kw)
#     job["log"].append(kw.get("step", job.get("step", "")))


# async def run_video_job(job_id: str, render_id: str, blueprint: SceneBlueprint):
#     job = JOBS[job_id]
#     s = get_settings()
#     base_dir = RENDERS_DIR / render_id
#     img_dir = base_dir / "images"
#     vid_dir = base_dir / "video"
#     img_dir.mkdir(exist_ok=True)
#     vid_dir.mkdir(exist_ok=True)

#     try:
#         seg_manifest = json.loads((base_dir / "segments.json").read_text(encoding="utf-8"))
#         seg_by_index = {sgm["index"]: sgm for sgm in seg_manifest}

#         # 1 · studio canon
#         _update(job, status="running", step="Building studio canon")
#         canon = await llm.build_visual_canon(blueprint)
#         studio = canon["studio"]
#         (base_dir / "visual_canon.json").write_text(json.dumps(canon, indent=2), encoding="utf-8")

#         # Per-reel VARIATION PROFILE: chosen once for the whole reel so every scene in
#         # this reel shares one look (same poster art, decor and wardrobe), while the
#         # NEXT reel rotates to a different look. Faces always come from the real photo.
#         reel_profile = _select_variation_profile()
#         job["variation_profile"] = (reel_profile or {}).get("id", "default")
#         if reel_profile:
#             _update(job, step=(f"This reel's look: '{reel_profile['id']}' — "
#                                f"{reel_profile['poster'][:60]}... (consistent across this reel)"))

#         use_mcp = s.video_provider.lower() == "hf_mcp"
#         mcp_client = higgsfield_mcp.HiggsfieldMCP(job=job) if use_mcp else None

#         # 2 · identity images — one locked frame per character
#         # First, AUTO-NORMALIZE the raw reference photos: accept host/guest/both in
#         # ANY format (jpg/jpeg/png/webp/heic/...), fix EXIF rotation, flatten alpha,
#         # and write a single clean <role>.png. This runs every job (idempotent, cheap)
#         # so you never have to run a separate prepare step by hand.
#         if use_mcp:
#             norm = image_prep.normalize_inputs(ASSETS_DIR)
#             for line in norm["results"]:
#                 _update(job, step=f"Input photo — {line}")
#         host_photo, guest_photo = _character_path("host"), _character_path("guest")
#         identity_urls: dict[str, str] = {}
#         identity_refs: dict[str, str] = {}

#         if use_mcp:
#             import shutil
#             both_photo = _character_path("both")
#             photos = {"host": host_photo, "guest": guest_photo, "both": both_photo}
#             # host + guest are the on-camera talking heads. 'both' is generated ONLY to
#             # be used as the reel THUMBNAIL (it is never shown as a video scene), and
#             # only when we have a both-photo or a locked/cached 'both' image to make it.
#             roles = ["host", "guest"]
#             if get_settings().establishing_two_shot:
#                 _pk = reel_profile["id"] if reel_profile else ""
#                 if both_photo or _identity_cache_load("both", "", key=_pk) or (IDENTITY_CACHE_DIR / f"{_cache_stem('both', _pk)}.png").exists():
#                     roles.append("both")
#                 else:
#                     _update(job, step=("No 'both' image available (assets/characters/both.*) — the reel will "
#                                        "render normally; the thumbnail will fall back to the host image."))
#             for role in roles:
#                 photo = photos[role]
#                 # 1) Locked/cached identity is self-sufficient — reuse it even if
#                 #    the original reference photo is no longer on disk. Cache is keyed
#                 #    by the per-reel variation profile, so a NEW look regenerates while
#                 #    a repeated look reuses (generate-once-per-look).
#                 pkey = reel_profile["id"] if reel_profile else ""
#                 studio_for_sig = _studio_for_profile(reel_profile)
#                 sig = _identity_signature(photo, studio_for_sig, "gpt_image_2") if photo else None
#                 cached = None
#                 if not job.get("force_regen_identity"):
#                     cached = _identity_cache_load(role, sig if sig else "", key=pkey)
#                     if not cached and sig:
#                         cached = _identity_cache_load(role, sig, key=pkey)
#                 if cached:
#                     _update(job, step=f"Reusing cached {role} identity image for this look (0 credits)")
#                     identity_urls[role] = cached["url"]
#                     identity_refs[role] = cached["ref"]
#                     cpng = IDENTITY_CACHE_DIR / f"{_cache_stem(role, pkey)}.png"
#                     if cpng.exists():
#                         shutil.copyfile(cpng, img_dir / f"{role}.png")
#                     continue

#                 # 2) No cache — need the source photo to generate the studio image.
#                 if not photo:
#                     raise RuntimeError(
#                         f"No locked/cached identity for {role} and no reference photo at "
#                         f"assets/characters/{role}.*. Add the photo or lock an image first.")
#                 _update(job, step=f"Generating {role} identity image (look: {pkey or 'default'})")
#                 # The gpt-image-2 director skill writes the creative prompt; our
#                 # enforcement suffix locks identity + studio + single-person + no
#                 # headphones. Falls back to the built-in prompt if the skill is off
#                 # or its LLM call returns nothing.
#                 img_prompt = None
#                 if get_settings().use_director_skills:
#                     img_prompt = await director_skills.image_prompt(
#                         _image_concept_brief(role, studio, reel_profile),
#                         _image_enforcement(role, reel_profile))
#                     if img_prompt:
#                         _update(job, step=f"{role}: prompt written by gpt-image-2 director skill")
#                 if not img_prompt:
#                     img_prompt = (_identity_image_prompt(role, studio, reel_profile) +
#                                   " Keep the person's face, hair and likeness exactly as in the reference image.")
#                 gen = await mcp_client.generate(
#                     "image",
#                     prompt=img_prompt,
#                     model_hint="gpt_image_2",
#                     image_files=[photo],
#                     aspect_ratio=s.hf_aspect_ratio,
#                     resolution="2k",
#                 )
#                 identity_urls[role] = gen["url"]
#                 dest = img_dir / f"{role}.png"
#                 await _download(gen["url"], dest)

#                 # AUTO-LOCK this look: persist under (role, profile) so this exact
#                 # look is never regenerated; a different reel/profile makes its own.
#                 import shutil as _sh
#                 cache_png = IDENTITY_CACHE_DIR / f"{_cache_stem(role, pkey)}.png"
#                 _sh.copyfile(dest, cache_png)
#                 locked_ref = gen.get("ref")
#                 try:
#                     _update(job, step=f"Locking {role} identity image for this look (reused free afterwards)")
#                     reg = await mcp_client.register_local_image(cache_png)
#                     if reg.get("ref"):
#                         locked_ref = reg["ref"]
#                 except Exception as e:  # noqa: BLE001
#                     logger.warning("auto-lock register failed for %s (using gen ref): %s", role, e)
#                 identity_refs[role] = locked_ref
#                 if locked_ref:
#                     _identity_cache_save(role, sig, gen["url"], locked_ref, cache_png, locked=True, key=pkey)
#                     _update(job, step=f"{role.title()} identity locked for look '{pkey or 'default'}'")
#         elif host_photo and guest_photo:
#             for role, photo in (("host", host_photo), ("guest", guest_photo)):
#                 _update(job, step=f"Uploading {role} reference photo")
#                 ref_url = await _hf_upload(photo)
#                 _update(job, step=f"Generating {role} studio identity image (Soul Reference)")
#                 res = await _hf_subscribe(s.hf_image_ref_model, {
#                     "prompt": _identity_image_prompt(role, studio),
#                     "image_reference_url": ref_url,
#                     "aspect_ratio": s.hf_aspect_ratio,
#                     "resolution": s.hf_image_resolution,
#                     "enhance_prompt": False,  # our prompt is deliberate; don't let it drift
#                 })
#                 url = _first_url(res)
#                 identity_urls[role] = url
#                 await _download(url, img_dir / f"{role}.png")
#         else:
#             # Fallback: invent characters from text (testing only)
#             for role in ("host", "guest"):
#                 desc = canon[role]
#                 _update(job, step=f"No photo for {role} — generating from text canon (testing only)")
#                 res = await _hf_subscribe(s.hf_image_model, {
#                     "prompt": f"{desc}. {_identity_image_prompt(role, studio)}",
#                     "aspect_ratio": s.hf_aspect_ratio,
#                     "resolution": "2K",  # soul/standard uses 2K|4K
#                 })
#                 url = _first_url(res)
#                 identity_urls[role] = url
#                 await _download(url, img_dir / f"{role}.png")

#         job["images"] = {r: f"/renders/{render_id}/images/{r}.png" for r in identity_urls}

#         # 3 · per-scene clips — same identity image every time a speaker is on camera
#         scene_limit = get_settings().scene_limit
#         scenes_to_do = blueprint.scenes[:scene_limit] if scene_limit else blueprint.scenes
#         if scene_limit:
#             _update(job, step=f"SCENE_LIMIT={scene_limit}: generating only the first {scene_limit} scene(s)")
#         chain_enabled = get_settings().chain_scenes
#         prev_clip: Path | None = None
#         prev_role: str | None = None
#         scene_files: list[Path] = []
#         force_regen = job.get("force_regen_scenes", False)

#         # The two-shot of BOTH people is NO LONGER rendered as a video scene — every
#         # clip in the reel is a single talking head. The 'both' image is still produced
#         # (above), but only to serve as the reel THUMBNAIL (created after the merge).
#         two_shot_scene_number: int | None = None

#         for scene in scenes_to_do:
#             seg = seg_by_index.get(scene.scene_number)
#             target_sec = float(seg["duration"]) if seg else (scene.end_second - scene.start_second)
#             duration = int(min(SEEDANCE_MAX_DUR, max(SEEDANCE_MIN_DUR, math.ceil(target_sec))))
#             seg_speaker = (seg.get("speaker") if seg else None) or scene.speaker_on_camera
#             role = "host" if str(seg_speaker).upper() == "HOST" else "guest"

#             final_clip = vid_dir / f"scene_{scene.scene_number:02d}.mp4"

#             # RESUME: if this scene's clip already exists from a prior run, reuse it
#             # (no regeneration, no credits). A retry thus continues from where it
#             # stopped. force_regen_scenes bypasses this.
#             if not force_regen and final_clip.exists() and final_clip.stat().st_size > 10000:
#                 _update(job, step=f"Scene {scene.scene_number}: already generated — reusing existing clip (0 credits)")
#                 scene_files.append(final_clip)
#                 prev_clip = final_clip
#                 prev_role = role
#                 job["scenes"].append({
#                     "scene_number": scene.scene_number,
#                     "speaker": role,
#                     "url": f"/renders/{render_id}/video/{final_clip.name}",
#                     "reused": True,
#                 })
#                 continue

#             _update(job, step=f"Scene {scene.scene_number}/{len(blueprint.scenes)}: video generation ({duration}s, takes minutes)")
#             if use_mcp:
#                 # Seedance 2.0: identity image(s) + segment audio -> lip-synced clip.
#                 audio_files = None
#                 if seg:
#                     seg_path = base_dir / Path(seg["audio_url"]).name
#                     audio_files = [_to_wav(seg_path)]  # Higgsfield needs wav, not mp3

#                 speaker_role = role
#                 listener_role = "guest" if speaker_role == "host" else "host"

#                 # ESTABLISHING TWO-SHOT: the FIRST clip opens on both people in one
#                 # frame (over the opening line's audio), then the reel cuts to single
#                 # talking heads. This is the only clip where two people share frame.
#                 # This scene is the two-shot only if it's the one chosen above
#                 # (default: a single middle scene). Everything else is single-person.
#                 both_ref = identity_refs.get("both")
#                 both_img = img_dir / "both.png"
#                 two_shot = bool(two_shot_scene_number is not None
#                                 and scene.scene_number == two_shot_scene_number
#                                 and (both_ref or both_img.exists()))

#                 if two_shot:
#                     # Primary = the composed 'both' image; the two singles ride along
#                     # as secondary identity anchors so each face stays exact.
#                     ordered_refs, ordered_files = [], []
#                     for r_role, r_img in (("both", both_img),
#                                           (speaker_role, img_dir / f"{speaker_role}.png"),
#                                           (listener_role, img_dir / f"{listener_role}.png")):
#                         r_ref = identity_refs.get(r_role)
#                         if r_ref:
#                             ordered_refs.append(r_ref)
#                         elif r_img.exists():
#                             ordered_files.append(r_img)
#                     image_refs_arg = ordered_refs or None
#                     image_files_arg = ordered_files or None
#                     want_reaction = False
#                     cam = (scene.camera_movement or "slow push-in").strip()  # NOT sanitized: two-shot is intended here
#                     _update(job, step=f"Scene {scene.scene_number}: ESTABLISHING TWO-SHOT (both on camera)")
#                     vid_prompt = None
#                     if get_settings().use_director_skills:
#                         vid_prompt = await director_skills.video_prompt(
#                             _two_shot_concept_brief(scene, speaker_role, listener_role, duration, cam),
#                             _two_shot_enforcement(scene, speaker_role, listener_role),
#                             bilingual=get_settings().seedance_bilingual_prompt,
#                         )
#                         if vid_prompt:
#                             _update(job, step=f"Scene {scene.scene_number}: two-shot prompt written by seedance director skill")
#                     if not vid_prompt:
#                         vid_prompt = _two_shot_video_prompt(scene, studio, speaker_role, listener_role)
#                 else:
#                     # BOTH identity images go into EVERY single-person clip: the
#                     # speaker first (primary subject / start_image, lip-syncs to the
#                     # audio) and the listener second (secondary reference, available
#                     # for a brief in-clip reaction cut). Locked refs are reused
#                     # directly — no re-upload, no quality loss, no last-frame
#                     # chaining. If a ref is missing we fall back to the local PNG.
#                     ordered_refs, ordered_files = [], []
#                     for r_role in (speaker_role, listener_role):
#                         r_ref = identity_refs.get(r_role)
#                         r_img = img_dir / f"{r_role}.png"
#                         if r_ref:
#                             ordered_refs.append(r_ref)
#                         elif r_img.exists():
#                             ordered_files.append(r_img)
#                     image_refs_arg = ordered_refs or None
#                     image_files_arg = ordered_files or None

#                     # Ask for a brief in-clip reaction glimpse of the listener only
#                     # when the turn is long enough to warrant a cutaway (config-gated).
#                     react_threshold = get_settings().reaction_min_seconds
#                     want_reaction = bool(
#                         get_settings().reaction_shots_enabled and react_threshold
#                         and target_sec >= react_threshold
#                     )

#                     _update(job, step=(f"Scene {scene.scene_number}: single talking head (full quality"
#                                        f"{', with listener reaction beat' if want_reaction else ''})"))
#                     cam = _sanitize_camera(scene.camera_movement)
#                     # The seedance director skill writes the creative prompt; our
#                     # enforcement suffix locks single-person framing + precise
#                     # lip-sync + identity + studio + the one reaction beat. Falls
#                     # back to the built-in prompt if the skill is off or returns
#                     # nothing, so a flaky prompt step never blocks a paid render.
#                     vid_prompt = None
#                     if get_settings().use_director_skills:
#                         vid_prompt = await director_skills.video_prompt(
#                             _video_concept_brief(scene, speaker_role, listener_role, want_reaction, duration, cam),
#                             _video_enforcement(scene, speaker_role, listener_role, want_reaction),
#                             bilingual=get_settings().seedance_bilingual_prompt,
#                         )
#                         if vid_prompt:
#                             _update(job, step=f"Scene {scene.scene_number}: prompt written by seedance director skill")
#                     if not vid_prompt:
#                         vid_prompt = _scene_video_prompt(scene, studio, speaker_role, listener_role, want_reaction)

#                 # Persist the exact prompt used, for inspection and reproducible retries.
#                 try:
#                     (vid_dir / f"scene_{scene.scene_number:02d}.prompt.txt").write_text(vid_prompt, encoding="utf-8")
#                 except Exception:  # noqa: BLE001
#                     pass
#                 gen = await mcp_client.generate(
#                     "video",
#                     prompt=vid_prompt,
#                     model_hint="seedance",
#                     image_refs=image_refs_arg,
#                     image_files=image_files_arg,
#                     audio_files=audio_files,
#                     duration=duration,
#                     aspect_ratio=s.hf_aspect_ratio,
#                     resolution=_norm_video_resolution(s.hf_video_resolution),
#                 )
#                 await _download(gen["url"], final_clip)  # keep native synced audio; no trim
#                 prev_clip = final_clip
#                 prev_role = role
#                 scene_files.append(final_clip)
#                 job["scenes"].append({
#                     "scene_number": scene.scene_number,
#                     "speaker": speaker_role,
#                     "reaction_of": listener_role if want_reaction else None,
#                     "video_url": f"/renders/{render_id}/video/{final_clip.name}",
#                 })
#             else:
#                 res = await _hf_subscribe(s.hf_video_model, {
#                     "prompt": _scene_video_prompt(scene, studio),
#                     "image_url": identity_urls[role],
#                     "duration": duration,
#                     "resolution": _norm_video_resolution(s.hf_video_resolution),
#                     "aspect_ratio": s.hf_aspect_ratio,
#                     "camera_fixed": _is_static_camera(scene.camera_movement),
#                 })
#                 raw = vid_dir / f"scene_{scene.scene_number:02d}_raw.mp4"
#                 await _download(_first_url(res), raw)
#                 _update(job, step=f"Scene {scene.scene_number}: trimming to {target_sec:.1f}s")
#                 _trim_clip(raw, final_clip, min(target_sec, duration))
#                 raw.unlink()
#                 scene_files.append(final_clip)
#                 job["scenes"].append({
#                     "scene_number": scene.scene_number,
#                     "video_url": f"/renders/{render_id}/video/{final_clip.name}",
#                 })

#         # 4 · merge
#         _update(job, step="Merging clips into final reel (ffmpeg)")
#         merged = vid_dir / "merged_reel.mp4"
#         if use_mcp:
#             # MCP clips carry native lip-synced audio; concat A/V, fallback to reel audio
#             try:
#                 _merge_av(scene_files, merged)
#             except RuntimeError:
#                 _merge_with_reel_audio(scene_files, _find_reel_audio(base_dir), merged)
#         else:
#             _merge_with_reel_audio(scene_files, _find_reel_audio(base_dir), merged)
#         job["merged_url"] = f"/renders/{render_id}/video/{merged.name}"

#         # 5 · thumbnail — the two-shot 'both' image (both people in one frame) is used
#         # ONLY here, as the reel thumbnail; it is never rendered as a video scene.
#         # Falls back to the host image if no 'both' image was produced.
#         try:
#             import shutil as _sh
#             thumb_src = None
#             for cand in (img_dir / "both.png", img_dir / "host.png"):
#                 if cand.exists():
#                     thumb_src = cand
#                     break
#             if thumb_src:
#                 thumb = base_dir / "thumbnail.png"
#                 _sh.copyfile(thumb_src, thumb)
#                 job["thumbnail_url"] = f"/renders/{render_id}/{thumb.name}"
#                 _update(job, step=(f"Thumbnail saved from the {'two-shot' if thumb_src.name == 'both.png' else 'host'} "
#                                    f"image ({thumb.name})"))
#             else:
#                 _update(job, step="No image available for a thumbnail (skipped).")
#         except Exception as e:  # noqa: BLE001 — thumbnail must never fail the reel
#             logger.warning("thumbnail generation failed: %s", e)

#         _update(job, status="completed", step="Done")
#     except Exception as e:  # noqa: BLE001 — job must capture any failure
#         logger.exception("Video job %s failed", job_id)
#         _update(job, status="failed", step="Failed", error=str(e))


# def clear_identity_cache(role: str | None = None) -> list[str]:
#     cleared = []
#     roles = [role] if role else ["host", "guest", "both"]
#     for r in roles:
#         # the legacy single-look files AND every per-profile look (role__profile.*)
#         targets = [IDENTITY_CACHE_DIR / f"{r}.json", IDENTITY_CACHE_DIR / f"{r}.png"]
#         targets += list(IDENTITY_CACHE_DIR.glob(f"{r}__*.json"))
#         targets += list(IDENTITY_CACHE_DIR.glob(f"{r}__*.png"))
#         for f in targets:
#             if f.exists():
#                 f.unlink(); cleared.append(f.name)
#     return cleared


# def identity_cache_status() -> dict:
#     out = {}
#     for r in ("host", "guest", "both"):
#         looks = {}
#         legacy = IDENTITY_CACHE_DIR / f"{r}.json"
#         if legacy.exists():
#             try:
#                 looks["default"] = json.loads(legacy.read_text(encoding="utf-8")).get("ref")
#             except Exception:  # noqa: BLE001
#                 pass
#         for meta in IDENTITY_CACHE_DIR.glob(f"{r}__*.json"):
#             look = meta.stem.split("__", 1)[1]
#             try:
#                 looks[look] = json.loads(meta.read_text(encoding="utf-8")).get("ref")
#             except Exception:  # noqa: BLE001
#                 pass
#         out[r] = looks or None
#     return out


# def start_job(render_id: str, blueprint: SceneBlueprint, force_regen_identity: bool = False, force_regen_scenes: bool = False) -> str:
#     job_id = uuid.uuid4().hex[:10]
#     JOBS[job_id] = {
#         "job_id": job_id, "render_id": render_id,
#         "status": "queued", "step": "Queued",
#         "scenes": [], "images": {}, "merged_url": None, "error": None, "log": [],
#         "cost_estimate": 0, "force_regen_identity": force_regen_identity,
#         "force_regen_scenes": force_regen_scenes,
#     }
#     asyncio.get_event_loop().create_task(run_video_job(job_id, render_id, blueprint))
#     return job_id


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


def _cache_stem(role: str, key: str = "") -> str:
    """Cache filename stem. With a variation profile key, each look is cached
    separately (role__profile) so a new reel's profile regenerates while a repeat
    profile reuses; without a key it's the legacy single-look stem (role)."""
    return f"{role}__{key}" if key else role


def _identity_cache_load(role: str, signature: str, key: str = "") -> dict | None:
    stem = _cache_stem(role, key)
    meta = IDENTITY_CACHE_DIR / f"{stem}.json"
    png = IDENTITY_CACHE_DIR / f"{stem}.png"
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
                          locked: bool = False, key: str = ""):
    import shutil
    stem = _cache_stem(role, key)
    dest_png = IDENTITY_CACHE_DIR / f"{stem}.png"
    if Path(src_png).resolve() != dest_png.resolve():
        shutil.copyfile(src_png, dest_png)
    (IDENTITY_CACHE_DIR / f"{stem}.json").write_text(
        json.dumps({"signature": signature, "url": url, "ref": ref, "locked": locked}, indent=2),
        encoding="utf-8",
    )

SEEDANCE_MIN_DUR, SEEDANCE_MAX_DUR = 2, 12

_VALID_VIDEO_RES = {"480p", "720p", "1080p"}


def _norm_video_resolution(value: str) -> str:
    """Coerce a config/env resolution into a value Seedance accepts. Seedance only
    allows 480p / 720p / 1080p (the trailing 'p' is REQUIRED), so '1080' -> '1080p'.
    Anything unrecognized falls back to 720p rather than erroring the whole clip."""
    v = (value or "").strip().lower()
    if v and not v.endswith("p") and v.isdigit():
        v = v + "p"
    return v if v in _VALID_VIDEO_RES else "720p"


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


# The UNCHANGING room structure — same in every reel. The specific poster art,
# decor objects and wardrobe are NOT fixed here; they come from a per-reel variation
# profile (below) so different reels look fresh while staying consistent within a reel.
_STUDIO_STRUCTURE = (
    "a modern in-person podcast studio: a tall matte-black built-in bookshelf-and-cabinet back wall with "
    "warm interior LED accent lighting, glass-front upper cabinets and cane/rattan-front lower cabinets; the "
    "people sit in mid-century grey upholstered armchairs with wooden frames; a black dynamic microphone on a "
    "black boom arm reaches in from the side; a neutral area rug on a dark wood floor; warm, moody, low-key "
    "cinematic lighting"
)

# Per-reel VARIATION PROFILES. Each new reel rotates to the next profile so the wall
# art, the objects on the shelves and the speakers' clothing change between reels —
# while the room, the faces and (within one reel) everything stay consistent. Faces
# are always preserved from the real reference photo; only background + wardrobe vary.
_VARIATION_PROFILES = [
    {
        "id": "modern_abstract",
        "poster": "a large framed original abstract painting with bold muted color blocks and gestural brush "
                  "strokes — NO text, NO words, no logos and no recognizable artwork",
        "decor": "stacked plain hardcover books, a small green potted plant, a smooth ceramic vase and a "
                 "polished live-edge wood-slice sculpture on a stand",
        "wardrobe": {"host": "a plain charcoal crew-neck sweater (no logos)",
                     "guest": "a plain black short-sleeve polo shirt (no logos or text)"},
    },
    {
        "id": "nature_photo",
        "poster": "a large framed black-and-white nature photograph of misty forest trees in a thin matte-black "
                  "frame — NO text, no words, no logos",
        "decor": "a softly glowing geode lamp, a small succulent, a folded wool throw and a few plain books",
        "wardrobe": {"host": "a plain dark navy button-down shirt (no logos)",
                     "guest": "a plain black quarter-zip sweater (no logos)"},
    },
    {
        "id": "line_art",
        "poster": "a large framed minimalist single-line botanical line-art print in black ink on a cream "
                  "background — abstract lines only, NO words, no logos",
        "decor": "a matte black ceramic vase with dried pampas grass, a short stack of plain design books and "
                 "a small abstract brass sculpture",
        "wardrobe": {"host": "a plain slate-grey henley (no logos)",
                     "guest": "a plain dark olive crew-neck sweater (no logos)"},
    },
    {
        "id": "calm_typography",
        "poster": "a large framed minimalist poster showing a single common everyday word in clean white "
                  "uppercase sans-serif on a plain dark background — a generic ordinary word only, NOT a brand "
                  "name, slogan, quote, book title, song lyric or any recognizable phrase",
        "decor": "a potted fern, a plain ceramic mug, a small stack of unbranded notebooks and a smooth river "
                 "stone",
        "wardrobe": {"host": "a plain black crew-neck long-sleeve sweater (no logos)",
                     "guest": "a plain dark charcoal polo shirt (no logos or text)"},
    },
]
_VARIATION_STATE = IDENTITY_CACHE_DIR / "variation_state.json"


def _profile_by_id(pid: str) -> dict | None:
    return next((p for p in _VARIATION_PROFILES if p["id"] == pid), None)


def _select_variation_profile() -> dict | None:
    """Pick the variation profile for THIS reel. 'auto' rotates to the next profile
    each render (so consecutive reels differ); a specific id pins one; 'none' or
    variation off returns None (→ the legacy single fixed look)."""
    s = get_settings()
    if not getattr(s, "vary_across_reels", True):
        return None
    choice = (getattr(s, "variation_profile", "auto") or "auto").strip().lower()
    if choice in ("none", "off", ""):
        return None
    if choice != "auto":
        return _profile_by_id(choice)  # pinned profile (or None if unknown)
    # auto-rotate via a small persistent counter
    n = 0
    try:
        n = int(json.loads(_VARIATION_STATE.read_text(encoding="utf-8")).get("n", 0))
    except Exception:  # noqa: BLE001
        n = 0
    profile = _VARIATION_PROFILES[n % len(_VARIATION_PROFILES)]
    try:
        _VARIATION_STATE.write_text(json.dumps({"n": n + 1}), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return profile


def _studio_for_profile(profile: dict | None) -> str:
    """The full studio description used for IMAGE generation: fixed structure plus
    this reel's specific poster art and decor objects."""
    if not profile:
        return (_STUDIO_STRUCTURE + "; on the back wall, a large framed original abstract art print with NO "
                "text or logos; stacked plain books, small plants and tasteful unbranded decor on the shelves")
    return (f"{_STUDIO_STRUCTURE}; on the back wall, {profile['poster']}; on and around the shelves, "
            f"{profile['decor']}")


# A FIXED studio reference used by the VIDEO prompts/enforcement. It is deliberately
# poster-agnostic: it defers to the per-reel identity image so a reel stays internally
# consistent (same poster/decor/wardrobe as that reel's locked image) without pinning
# one specific poster across all reels.
_FIXED_SET = (
    "SAME EXACT studio in every shot of this reel — " + _STUDIO_STRUCTURE + ". Keep the wall art/posters, the "
    "framed pieces, the objects on the shelves AND each person's clothing EXACTLY as they appear in the "
    "supplied reference image — do not invent different posters, different decor or different clothes. "
    "Everything matches the reference image so all scenes look like one continuous recording in the same room."
)

# Short canonical studio phrase for the (token-limited) director briefs.
_STUDIO_SHORT = (
    "the same fixed studio as the reference image — matte-black bookshelf/cabinet wall with warm LED accent "
    "lighting, mid-century grey upholstered armchairs, a black boom microphone, neutral rug, warm moody "
    "lighting; keep the SAME wall art, decor and clothing as the reference image"
)

# ── Fixed seating canon (viewer's perspective) ────────────
# The seating is a FACT of the real set and never changes between clips: the GUEST
# sits on the LEFT of frame, the HOST sits on the RIGHT. Everything directional
# (which way a speaker faces, where their eyes go) is DERIVED from this, so it is
# deterministic — never invented per scene. A person on the right looks LEFT toward
# the other; a person on the left looks RIGHT.
SEAT_SIDE = {"host": "right", "guest": "left"}

# Hard rule against Seedance burning ANY text into the picture. Seedance is
# Chinese-origin and will sometimes render subtitles/captions (often Chinese)
# unless explicitly forbidden; this is repeated in every video layer.
_NO_TEXT = (
    "ABSOLUTELY NO on-screen text of any kind anywhere in the frame: no captions, no subtitles, no closed "
    "captions, no burned-in words, no karaoke text, no lower-thirds, no titles, no name tags, no caption "
    "bar, no watermark, no logos, and no Chinese or English characters rendered into the video. The frame "
    "shows ONLY the filmed scene — clean image, zero graphics or text overlays."
)

# Hard rule against rendering anyone's intellectual property. Real generative
# platforms (Higgsfield included) refuse a job with "IP detected" if the prompt or
# the reference image contains a brand logo, trademark, real book/album title,
# slogan or recognizable copyrighted artwork. Since WE produce the image that is fed
# to the video step, we forbid IP at the source.
_NO_IP = (
    "Do NOT depict any brand logos, trademarks, company names, sports/team marks, real book, album or movie "
    "titles, advertising slogans, song lyrics, famous quotes, or any recognizable copyrighted artwork or "
    "character — anywhere in the frame, including on clothing, posters, wall art, mugs, books, screens or "
    "props. Use only generic, original, unbranded designs; any clothing must be plain with no visible logos "
    "or text."
)


def _other_dir(side: str) -> str:
    """The direction someone on `side` must look to face the other person."""
    return "left" if side == "right" else "right"


def _seat_phrase(role: str) -> str:
    """e.g. 'seated on the RIGHT side of the frame (the host's fixed seat)'."""
    side = SEAT_SIDE.get(role, "right")
    return f"seated on the {side.upper()} side of the frame (the {role}'s fixed seat)"


def _eyeline_phrase(speaker: str, listener: str) -> str:
    """Deterministic eye-line: the speaker turns toward the listener's fixed side."""
    s_side = SEAT_SIDE.get(speaker, "right")
    look = _other_dir(s_side)                       # direction to face the other person
    l_side = SEAT_SIDE.get(listener, _other_dir(s_side))
    return (f"the {speaker} is {_seat_phrase(speaker)} and turns to look toward their {look} "
            f"(screen-{look}), where the {listener} sits ({l_side} of frame), NOT into the camera")


# ── Prompt building ───────────────────────────────────────
def _wardrobe_for(profile: dict | None, role: str) -> str | None:
    if not profile:
        return None
    return (profile.get("wardrobe") or {}).get(role)


def _identity_image_prompt(role: str, studio: str, profile: dict | None = None) -> str:
    set_desc = _studio_for_profile(profile)
    if role == "both":
        wh = _wardrobe_for(profile, "host")
        wg = _wardrobe_for(profile, "guest")
        clothes = (f" Dress the HOST in {wh} and the GUEST in {wg}, but keep BOTH people's faces, hair, "
                   f"beards and build EXACTLY as in the reference photo." if (wh and wg) else
                   " Keep BOTH people's faces, hair, beards, build and their black/dark clothing exactly as shown.")
        return (
            f"The TWO exact people from the reference photo together as podcast co-hosts, seated SIDE BY SIDE "
            f"in two separate mid-century grey upholstered armchairs angled slightly toward each other — the "
            f"GUEST on the LEFT of frame, the HOST on the RIGHT (viewer's perspective) — both fully visible in "
            f"one balanced two-shot. {set_desc}. A black dynamic microphone on a black boom arm reaches in "
            f"toward each of them from the side, NO headphones (in-person conversation), relaxed natural "
            f"posture, mid-conversation.{clothes} Do not swap or merge their identities. Wide-to-medium "
            f"vertical 9:16 two-shot, photographic and lifelike (natural skin texture, visible pores, "
            f"catchlights), warm moody low-key lighting, true-to-life colour, sharp focus. No on-screen text, "
            f"no watermark, no logos, NOT a 3D render, NOT illustration, NOT anime. " + _NO_IP
        )
    label = "HOST" if role == "host" else "GUEST"
    w = _wardrobe_for(profile, role)
    clothes = (f"Keep the person's face, hair, beard and build EXACTLY as in the reference photo, but dress "
               f"them in {w} (their clothing for this episode)." if w else
               "Keep the person's face, hair, beard, build and their black/dark clothing exactly as in the "
               "reference photo.")
    return (
        f"Faithfully reproduce this exact person as a podcast {label}, seated in a mid-century grey "
        f"upholstered armchair with a wooden frame, framed as a SINGLE-PERSON SHOT — ONLY this one person is "
        f"visible in frame, no second person, no one sitting opposite. {set_desc}. A black dynamic microphone "
        f"on a black boom arm reaches in from the side, NO headphones (this is an in-person conversation). The "
        f"person is {_seat_phrase(role)}, body and gaze turned toward their "
        f"{_other_dir(SEAT_SIDE.get(role, 'right'))} (screen-{_other_dir(SEAT_SIDE.get(role, 'right'))}) where "
        f"the other person sits off-camera, hands resting naturally. {clothes} Looking toward the other person "
        f"off-camera (not into the lens). Vertical 9:16 composition. "
        f"PHOTOREALISTIC like a real photograph of a real human shot on a cinema camera with a 50mm lens and "
        f"shallow depth of field; lifelike skin with natural texture, visible pores and subtle subsurface "
        f"tones (never airbrushed, plastic or waxy); catchlights in the eyes; warm moody studio lighting; "
        f"true-to-life colour. Sharp focus, no text, no watermark, "
        f"no logos, NOT a 3D render, NOT illustration, NOT anime/cartoon. " + _NO_IP
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
    eyes = (scene.eye_contact or "natural engaged expression, with occasional brief glances down "
            "or to the microphone in thought").strip()
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
        f"Position & eye contact: {_eyeline_phrase(speaker, listener)}. {eyes}. "
        f"This is a real in-person conversation — no fixed camera stare."
    )

    if want_reaction:
        parts.append(
            f"REACTION BEAT (still single-person — no two-shot): for a brief moment, about 1 to 2 seconds in "
            f"the middle of the clip, cut to the {listener} ALONE in frame (use the SECOND reference image), "
            f"{_seat_phrase(listener)}, looking toward their {_other_dir(SEAT_SIDE.get(listener, 'left'))} "
            f"(screen-{_other_dir(SEAT_SIDE.get(listener, 'left'))}) where the {speaker} sits off-camera — "
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
        f"person in the background, no headphones, no cartoon/anime/3D-render look, no plastic or waxy skin, no "
        f"fixed camera stare. " + _NO_TEXT + " " + _NO_IP
    )
    return " ".join(parts)


# ── Director-skill briefs + enforcement suffixes ──────────
# The uploaded skills (loaded as SYSTEM prompts) do the *creative* prompt
# writing; these briefs frame our exact use case for the skill, and the
# enforcement suffixes lock the non-negotiable technical contract on top
# (identity, fixed studio, single-person framing, no headphones, lip-sync).
# They deliberately reuse the same canon (_FIXED_SET) and constraint language as
# the built-in fallback prompts so directed and fallback prompts stay aligned.
def _image_concept_brief(role: str, studio: str, profile: dict | None = None) -> str:
    set_desc = _studio_for_profile(profile)
    if role == "both":
        wh, wg = _wardrobe_for(profile, "host"), _wardrobe_for(profile, "guest")
        wear = (f" Dress the host in {wh} and the guest in {wg}, faces unchanged from the reference."
                if (wh and wg) else " Same wardrobe and faces as the reference photo.")
        return (
            f"An editorial two-shot photograph of TWO real podcast co-hosts seated SIDE BY SIDE in two "
            f"separate mid-century grey upholstered armchairs angled slightly toward each other — GUEST on the "
            f"LEFT of frame, HOST on the RIGHT (viewer perspective) — a black boom microphone reaching in "
            f"toward each from the side, relaxed and mid-conversation. Both people fully visible in one "
            f"balanced frame. Vertical 9:16. Keep BOTH faces exactly from the reference photo.{wear} Aim for a "
            f"believable real-photograph look (film/editorial, not glossy CGI). Studio: {set_desc}"
        )
    who = "podcast host" if role == "host" else "podcast guest"
    w = _wardrobe_for(profile, role)
    wear = (f" Dress them in {w}; keep the face/hair/beard exactly from the reference photo."
            if w else " Same face and black/dark wardrobe as the reference photo.")
    return (
        f"Single editorial portrait of one real {who} seated in a mid-century grey upholstered armchair in a "
        f"warm in-person podcast studio, a black boom microphone reaching in from the side, body relaxed and "
        f"angled slightly toward the other person off to the side (off-camera), looking off to the side (not at "
        f"the lens). One person only in frame. Vertical 9:16. Keep the face exactly from the reference "
        f"photo.{wear} Aim for a believable real-photograph look (film/editorial, not glossy CGI). "
        f"Studio: {set_desc}"
    )


def _image_enforcement(role: str, profile: dict | None = None) -> str:
    set_desc = _studio_for_profile(profile)
    if role == "both":
        wh, wg = _wardrobe_for(profile, "host"), _wardrobe_for(profile, "guest")
        wear = (f" Dress the HOST in {wh} and the GUEST in {wg}; keep BOTH faces, hair, beards and build "
                f"EXACTLY from the reference photo." if (wh and wg)
                else " Keep BOTH people's faces, hair, beards, build and clothing EXACTLY from the reference.")
        return (
            "NON-NEGOTIABLE CONSTRAINTS (override anything above that conflicts): Studio = " + set_desc + "." +
            wear + " Do not swap, merge or invent identities. A balanced TWO-SHOT: both people fully visible, "
            "seated SIDE BY SIDE in separate grey armchairs angled slightly toward each other — the GUEST on "
            "the LEFT of frame and the HOST on the RIGHT (viewer perspective). NO headphones. Vertical 9:16. "
            "No on-screen text, no captions, no watermark, no logos. Render as a real photograph (natural skin "
            "texture, visible pores, catchlights), NOT a 3D render, NOT illustration, NOT anime. " + _NO_IP
        )
    w = _wardrobe_for(profile, role)
    wear = (f" Dress the person in {w}, but keep their face, hair, beard and build EXACTLY from the supplied "
            f"reference image — zero identity changes." if w
            else " Keep the person's face, hair, beard, build and black/dark clothing EXACTLY from the "
                 "supplied reference image — zero identity changes.")
    return (
        "NON-NEGOTIABLE CONSTRAINTS (override anything above that conflicts): Studio = " + set_desc + "." +
        wear + " SINGLE-PERSON SHOT: only this one person is visible, no second person, no one sitting "
        "opposite. They sit in a mid-century grey upholstered armchair. NO headphones. Vertical 9:16. No "
        "on-screen text, no captions, no watermark, no logos. Render as a real photograph (natural skin "
        "texture, visible pores, catchlights), NOT a 3D render, NOT illustration, NOT anime. " + _NO_IP
    )


def _video_concept_brief(scene: Scene, speaker: str, listener: str,
                         want_reaction: bool, duration: int, cam: str) -> str:
    bits = [
        f"A calm, friendly IN-PERSON podcast conversation clip, about {duration}s, vertical 9:16, single "
        f"camera, minimal cuts. The {speaker} is the on-camera speaker; the {listener} is the other "
        f"participant (off-camera). {_eyeline_phrase(speaker, listener)}. This is a relaxed studio chat — not action, not a "
        f"confrontation.",
        f"The {speaker}'s spoken audio is SUPPLIED separately — do NOT write any dialogue, narration or "
        f"subtitles; only describe the visible performance that lip-syncs to that audio. Render a CLEAN frame with NO on-screen text, captions or subtitles burned into the picture.",
        f"What the {speaker} does on this line: {scene.character_action}.",
        f"Facial expression: {scene.facial_expression}.",
    ]
    if (scene.body_language or "").strip():
        bits.append(f"Body language: {scene.body_language}.")
    if (scene.emotional_tone or "").strip():
        bits.append(f"Emotional tone: {scene.emotional_tone}.")
    if (scene.humor or "").strip():
        bits.append(f"Light optional touch (only if it fits): {scene.humor}.")
    eyes = (scene.eye_contact or "natural engaged expression, brief glances down in thought").strip()
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
        f"FIXED SEATING (viewer's perspective, identical in every clip): the GUEST sits on the LEFT of the "
        f"frame, the HOST sits on the RIGHT. The on-camera {speaker} is therefore {_seat_phrase(speaker)} and "
        f"must face/lean toward their {_other_dir(SEAT_SIDE.get(speaker, 'right'))} (screen-"
        f"{_other_dir(SEAT_SIDE.get(speaker, 'right'))}) toward the off-camera {listener} — never the wrong way, "
        f"never straight into the lens.",
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
        "micro-expressions; not plastic, waxy, cartoon, anime or 3D-render; no fixed camera stare. " + _NO_TEXT
        + " " + _NO_IP
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
        f"armchairs angled slightly toward each other — the GUEST on the LEFT of frame, the HOST on the RIGHT "
        f"(viewer perspective) — in the studio. "
        f"This is a wide-to-medium vertical 9:16 two-shot that establishes the scene. The {spk} is speaking "
        f"into the microphone — the {spk}'s mouth movements must lip-sync to the provided audio track (say "
        f"only what the audio says, never invent words); the {lis} listens and gives small natural nods. "
        f"{scene.character_action}. Keep BOTH people's exact faces, hair, beards, skin tone and clothing from "
        f"the reference images with zero identity changes — the {spk} from one reference and the {lis} from "
        f"the other; do not swap or merge them. No headphones (in-person). " + _FIXED_SET + " Camera: slow gentle "
        f"push-in, single continuous take. Ultra-real human look: natural skin texture and pores, "
        f"catchlights, realistic blinking and micro-expressions; not plastic, waxy, cartoon, anime or "
        f"3D-render. " + _NO_TEXT
    )


def _two_shot_concept_brief(scene: Scene, speaker: str, listener: str, duration: int, cam: str) -> str:
    return (
        f"An establishing TWO-SHOT for an in-person podcast, about {duration}s, vertical 9:16. BOTH people "
        f"are visible together in one frame, seated SIDE BY SIDE in separate grey armchairs angled toward each other: the {speaker} is "
        f"speaking, the {listener} is listening and nodding. This opening shot establishes the room and the "
        f"pair before the conversation cuts to single talking heads. The {speaker}'s spoken audio is SUPPLIED "
        f"separately — do NOT write dialogue; only describe the visible performance lip-syncing to it. Keep the frame CLEAN — no captions, subtitles or on-screen text. "
        f"What the {speaker} does: {scene.character_action}. Camera: {cam}. Keep it a calm studio chat, not "
        f"action. Studio: {_STUDIO_SHORT}."
    )


def _two_shot_enforcement(scene: Scene, speaker: str, listener: str) -> str:
    return (
        "NON-NEGOTIABLE TECHNICAL CONTRACT (override anything above that conflicts): This is the ONE "
        "establishing two-shot — BOTH people are intentionally visible together in one balanced frame, "
        "seated SIDE BY SIDE in separate grey armchairs angled toward each other — the GUEST on the LEFT of "
        "frame and the HOST on the RIGHT (viewer perspective). Do NOT add extra people. " +
        f"The {speaker} is the speaker and lip-syncs EXACTLY to the PROVIDED audio (say only what the audio "
        f"says, never invent or mouth words; no added dialogue, narration, subtitles or Audio section); the "
        f"{listener} listens with mouth closed and small natural nods. Preserve BOTH identities exactly from "
        f"their reference images — the {speaker} from one, the {listener} from the other; never swap or merge "
        f"faces. NO headphones. " + _FIXED_SET +
        " Single continuous take, gentle push-in. Ultra-real human look: natural skin texture and pores, "
        "catchlights, realistic blinking and micro-expressions; not plastic, waxy, cartoon, anime or "
        "3D-render. " + _NO_TEXT
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

        # Per-reel VARIATION PROFILE: chosen once for the whole reel so every scene in
        # this reel shares one look (same poster art, decor and wardrobe), while the
        # NEXT reel rotates to a different look. Faces always come from the real photo.
        reel_profile = _select_variation_profile()
        job["variation_profile"] = (reel_profile or {}).get("id", "default")
        if reel_profile:
            _update(job, step=(f"This reel's look: '{reel_profile['id']}' — "
                               f"{reel_profile['poster'][:60]}... (consistent across this reel)"))

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
            # host + guest are the on-camera talking heads. 'both' is generated ONLY to
            # be used as the reel THUMBNAIL (it is never shown as a video scene), and
            # only when we have a both-photo or a locked/cached 'both' image to make it.
            roles = ["host", "guest"]
            if get_settings().establishing_two_shot:
                _pk = reel_profile["id"] if reel_profile else ""
                if both_photo or _identity_cache_load("both", "", key=_pk) or (IDENTITY_CACHE_DIR / f"{_cache_stem('both', _pk)}.png").exists():
                    roles.append("both")
                else:
                    _update(job, step=("No 'both' image available (assets/characters/both.*) — the reel will "
                                       "render normally; the thumbnail will fall back to the host image."))
            for role in roles:
                photo = photos[role]
                # 1) Locked/cached identity is self-sufficient — reuse it even if
                #    the original reference photo is no longer on disk. Cache is keyed
                #    by the per-reel variation profile, so a NEW look regenerates while
                #    a repeated look reuses (generate-once-per-look).
                pkey = reel_profile["id"] if reel_profile else ""
                studio_for_sig = _studio_for_profile(reel_profile)
                sig = _identity_signature(photo, studio_for_sig, "gpt_image_2") if photo else None
                cached = None
                if not job.get("force_regen_identity"):
                    cached = _identity_cache_load(role, sig if sig else "", key=pkey)
                    if not cached and sig:
                        cached = _identity_cache_load(role, sig, key=pkey)
                if cached:
                    _update(job, step=f"Reusing cached {role} identity image for this look (0 credits)")
                    identity_urls[role] = cached["url"]
                    identity_refs[role] = cached["ref"]
                    cpng = IDENTITY_CACHE_DIR / f"{_cache_stem(role, pkey)}.png"
                    if cpng.exists():
                        shutil.copyfile(cpng, img_dir / f"{role}.png")
                    continue

                # 2) No cache — need the source photo to generate the studio image.
                if not photo:
                    raise RuntimeError(
                        f"No locked/cached identity for {role} and no reference photo at "
                        f"assets/characters/{role}.*. Add the photo or lock an image first.")
                _update(job, step=f"Generating {role} identity image (look: {pkey or 'default'})")
                # The gpt-image-2 director skill writes the creative prompt; our
                # enforcement suffix locks identity + studio + single-person + no
                # headphones. Falls back to the built-in prompt if the skill is off
                # or its LLM call returns nothing.
                img_prompt = None
                if get_settings().use_director_skills:
                    img_prompt = await director_skills.image_prompt(
                        _image_concept_brief(role, studio, reel_profile),
                        _image_enforcement(role, reel_profile))
                    if img_prompt:
                        _update(job, step=f"{role}: prompt written by gpt-image-2 director skill")
                if not img_prompt:
                    img_prompt = (_identity_image_prompt(role, studio, reel_profile) +
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

                # AUTO-LOCK this look: persist under (role, profile) so this exact
                # look is never regenerated; a different reel/profile makes its own.
                import shutil as _sh
                cache_png = IDENTITY_CACHE_DIR / f"{_cache_stem(role, pkey)}.png"
                _sh.copyfile(dest, cache_png)
                locked_ref = gen.get("ref")
                try:
                    _update(job, step=f"Locking {role} identity image for this look (reused free afterwards)")
                    reg = await mcp_client.register_local_image(cache_png)
                    if reg.get("ref"):
                        locked_ref = reg["ref"]
                except Exception as e:  # noqa: BLE001
                    logger.warning("auto-lock register failed for %s (using gen ref): %s", role, e)
                identity_refs[role] = locked_ref
                if locked_ref:
                    _identity_cache_save(role, sig, gen["url"], locked_ref, cache_png, locked=True, key=pkey)
                    _update(job, step=f"{role.title()} identity locked for look '{pkey or 'default'}'")
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

        # The two-shot of BOTH people is NO LONGER rendered as a video scene — every
        # clip in the reel is a single talking head. The 'both' image is still produced
        # (above), but only to serve as the reel THUMBNAIL (created after the merge).
        two_shot_scene_number: int | None = None

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
                # This scene is the two-shot only if it's the one chosen above
                # (default: a single middle scene). Everything else is single-person.
                both_ref = identity_refs.get("both")
                both_img = img_dir / "both.png"
                two_shot = bool(two_shot_scene_number is not None
                                and scene.scene_number == two_shot_scene_number
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
                    resolution=_norm_video_resolution(s.hf_video_resolution),
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
                    "resolution": _norm_video_resolution(s.hf_video_resolution),
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

        # 5 · thumbnail — the two-shot 'both' image (both people in one frame) is used
        # ONLY here, as the reel thumbnail; it is never rendered as a video scene.
        # Falls back to the host image if no 'both' image was produced.
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

        # 6 · GDrive upload — push ALL of this reel's raw clips to GDrive in ONE rclone
        # command (the same rclone remote the editing Routines read), only after every
        # clip is done. Nothing is uploaded one-by-one during generation.
        if get_settings().upload_to_gdrive:
            remote = (get_settings().rclone_remote or "").strip()
            if not remote:
                _update(job, step="GDrive upload is ON but RCLONE_REMOTE is empty — skipped.")
            else:
                what = (get_settings().gdrive_upload_what or "scenes").lower()
                prefix = get_settings().gdrive_clip_prefix or "RawClip"
                # (source_path, dest_name) pairs. Raw clips are renamed sequentially in
                # SCENE ORDER -> RawClip1, RawClip2, ... so the editing Routine merges
                # them in order. The merged reel / thumbnail keep their own names.
                items: list[tuple[Path, str]] = []
                ordered_scenes = sorted(vid_dir.glob("scene_*.mp4"))
                if what in ("scenes", "all"):
                    for i, clip in enumerate(ordered_scenes, start=1):
                        items.append((clip, f"{prefix}{i}{clip.suffix}"))
                if what in ("reel", "all") and merged.exists():
                    items.append((merged, merged.name))
                if what == "all":
                    _thumb = base_dir / "thumbnail.png"
                    if _thumb.exists():
                        items.append((_thumb, _thumb.name))
                dest = remote
                _update(job, step=(f"Uploading {len(items)} file(s) to GDrive in one batch → {dest} "
                                   f"(clips named {prefix}1..{prefix}{len(ordered_scenes)})"))
                ok, log = await _rclone_upload_all(items, dest, rclone_exe=get_settings().rclone_exe)
                job["gdrive_upload"] = {"status": "ok" if ok else "failed", "dest": dest,
                                        "files": [n for _, n in items], "log": log[-600:]}
                if ok:
                    _update(job, step=f"Uploaded {len(items)} file(s) to GDrive ({dest}).")
                    if get_settings().gdrive_delete_local_after_upload:
                        removed = 0
                        for src, _name in items:
                            try:
                                src.unlink(); removed += 1
                            except Exception:  # noqa: BLE001
                                pass
                        _update(job, step=f"Removed {removed} local file(s) after confirmed upload.")
                else:
                    # Don't fail the whole render — clips are still on disk as a fallback.
                    _update(job, step=f"GDrive upload FAILED — clips kept locally. rclone says: {log[-300:]}")

        _update(job, status="completed", step="Done")
    except Exception as e:  # noqa: BLE001 — job must capture any failure
        logger.exception("Video job %s failed", job_id)
        _update(job, status="failed", step="Failed", error=str(e))


async def _rclone_upload_all(items: list[tuple[Path, str]], dest: str, *,
                             rclone_exe: str = "rclone") -> tuple[bool, str]:
    """Upload files to the rclone destination in ONE command (not one at a time).
    `items` is a list of (source_path, dest_name) pairs — each file is staged FLAT
    under its dest_name, so they land directly in `dest` with the names we choose
    (e.g. RawClip1.mp4, RawClip2.mp4 in order). Returns (ok, log)."""
    import shutil, tempfile
    items = [(Path(s), n) for (s, n) in items if s and Path(s).exists()]
    if not items:
        return False, "no files to upload"
    stage = Path(tempfile.mkdtemp(prefix="rclone_stage_"))
    try:
        for src, name in items:
            shutil.copy2(src, stage / name)   # flat, renamed
        cmd = [rclone_exe, "copy", str(stage), dest,
               "--transfers", "8", "--checkers", "8",
               "--drive-chunk-size", "64M", "--no-traverse", "-v"]
        env = os.environ.copy()
        _cfg = (get_settings().rclone_config or "").strip()
        if _cfg:
            env["RCLONE_CONFIG"] = _cfg   # pin the exact rclone.conf for this subprocess
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
        # the legacy single-look files AND every per-profile look (role__profile.*)
        targets = [IDENTITY_CACHE_DIR / f"{r}.json", IDENTITY_CACHE_DIR / f"{r}.png"]
        targets += list(IDENTITY_CACHE_DIR.glob(f"{r}__*.json"))
        targets += list(IDENTITY_CACHE_DIR.glob(f"{r}__*.png"))
        for f in targets:
            if f.exists():
                f.unlink(); cleared.append(f.name)
    return cleared


def identity_cache_status() -> dict:
    out = {}
    for r in ("host", "guest", "both"):
        looks = {}
        legacy = IDENTITY_CACHE_DIR / f"{r}.json"
        if legacy.exists():
            try:
                looks["default"] = json.loads(legacy.read_text(encoding="utf-8")).get("ref")
            except Exception:  # noqa: BLE001
                pass
        for meta in IDENTITY_CACHE_DIR.glob(f"{r}__*.json"):
            look = meta.stem.split("__", 1)[1]
            try:
                looks[look] = json.loads(meta.read_text(encoding="utf-8")).get("ref")
            except Exception:  # noqa: BLE001
                pass
        out[r] = looks or None
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