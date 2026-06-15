"""Prompt-director skills integration.

Two uploaded skills are used as the *creative* prompt writers:
  - gpt_image_2_director.md  -> styles the identity-image prompt (GPT Image 2.0)
  - seedance_director.md      -> styles the per-scene video prompt (Seedance 2.0)

Architecture (two-stage, by design):
  Stage 1 — the skill (loaded as a SYSTEM prompt) + a concept brief assembled from
            the scene's script-driven fields -> an LLM returns a production-ready
            creative prompt in the skill's own format.
  Stage 2 — we APPEND a non-negotiable enforcement suffix that the skills don't
            know about: identity preservation, fixed studio canon, single-person
            framing, no headphones, 9:16, and (for video) PRECISE LIP-SYNC to the
            supplied audio plus the one sanctioned in-clip reaction beat.

Why a suffix and not trust the skill alone:
  - The Seedance skill is built for multi-cut cinematic sequences and has NO notion
    of lip-syncing to a provided audio track; left unconstrained it would add hard
    cuts that destroy lip-sync. We keep it as a styling layer and enforce the
    technical contract on top.
  - The image skill deliberately avoids the word "photorealistic" for faces (GPT
    Image 2 renders plasticky skin) and prefers film/editorial framing — which is
    the smarter route to realistic faces — but it doesn't know to preserve the
    reference identity or lock the studio set.

Everything degrades safely: if the skill LLM call returns None (mock provider,
missing key, error, or unparseable output) the caller falls back to the built-in
hand-written prompt, so a flaky prompt step never breaks a paid render.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from . import llm

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


@lru_cache(maxsize=4)
def _load_skill(name: str) -> str:
    p = SKILLS_DIR / name
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning("could not load director skill %s: %s", name, e)
        return ""


def _strip_code_fences(text: str) -> str:
    """The image skill returns the prompt inside a ``` or ```json fence. Peel it."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        return m.group(1).strip()
    return t


# ── IMAGE (GPT Image 2.0) ─────────────────────────────────
async def image_prompt(concept_brief: str, enforcement: str) -> str | None:
    """Run the gpt-image-2 director on the concept brief and return the styled
    prompt with the enforcement suffix appended. None -> caller uses built-in."""
    skill = _load_skill("gpt_image_2_director.md")
    if not skill:
        return None
    raw = await llm.director_complete(system=skill, user=concept_brief, max_tokens=1100)
    if not raw:
        return None
    prompt = _strip_code_fences(raw)
    if len(prompt) < 40:  # implausibly short -> don't trust it
        return None
    return f"{prompt}\n\n{enforcement}"


# ── VIDEO (Seedance 2.0) ──────────────────────────────────
def _extract_seedance_prompts(raw: str) -> tuple[str | None, str | None]:
    """The seedance skill returns a JSON array [{lang:en,prompt},{lang:zh,prompt}].
    Parse leniently and return (en, zh). Either may be None."""
    en = zh = None
    try:
        start = raw.find("[")
        end = raw.rfind("]") + 1
        arr = json.loads(raw[start:end]) if start != -1 and end > start else None
        if isinstance(arr, list):
            for obj in arr:
                if isinstance(obj, dict):
                    lang = (obj.get("lang") or "").lower()
                    if lang.startswith("en"):
                        en = obj.get("prompt")
                    elif lang.startswith("zh"):
                        zh = obj.get("prompt")
    except Exception:  # noqa: BLE001
        pass
    # Fallback: if it wasn't valid JSON but is plain prose, treat the whole thing as EN
    if not en and not zh:
        cleaned = _strip_code_fences(raw)
        if len(cleaned) >= 40 and "{" not in cleaned[:5]:
            en = cleaned
    return en, zh


async def video_prompt(concept_brief: str, enforcement: str,
                       bilingual: bool = False) -> str | None:
    """Run the seedance director on the concept brief and return the styled prompt
    with the enforcement suffix appended. Optionally include the native ZH rewrite
    (Seedance is Chinese-origin, so ZH can help). None -> caller uses built-in."""
    skill = _load_skill("seedance_director.md")
    if not skill:
        return None
    raw = await llm.director_complete(system=skill, user=concept_brief, max_tokens=1600)
    if not raw:
        return None
    en, zh = _extract_seedance_prompts(raw)
    if not en:
        return None
    if bilingual and zh:
        creative = f"{en}\n\n[中文 / Chinese]\n{zh}"
    else:
        creative = en
    return f"{creative}\n\n{enforcement}"