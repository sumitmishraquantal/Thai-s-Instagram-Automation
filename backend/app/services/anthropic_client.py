import json
import logging
import re

import httpx

from ..config import get_settings
from ..schemas import ScriptPackage, TrendingTopic

logger = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

MIN_SEC, MAX_SEC = 45.0, 50.0


def _headers() -> dict:
    s = get_settings()
    return {
        "x-api-key": s.anthropic_api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


def _extract_text(data: dict) -> str:
    return "\n".join(
        b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
    )


def _parse_json(text: str):
    clean = re.sub(r"```(?:json)?", "", text).strip()
    # find first [ or {
    candidates = [i for i in (clean.find("["), clean.find("{")) if i != -1]
    if not candidates:
        raise ValueError("No JSON found in model response")
    start = min(candidates)
    end = max(clean.rfind("]"), clean.rfind("}")) + 1
    return json.loads(clean[start:end])


async def _call(messages: list[dict], tools: list[dict] | None = None,
                max_tokens: int = 1500) -> str:
    s = get_settings()
    body: dict = {
        "model": s.anthropic_model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if tools:
        body["tools"] = tools
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(ANTHROPIC_URL, headers=_headers(), json=body)
        r.raise_for_status()
        return _extract_text(r.json())


async def fetch_trending_topics(category: str) -> list[TrendingTopic]:
    prompt = (
        f'Search the web for trending discussions, recent news, and viral conversations '
        f'this week about "{category}" that would resonate on Instagram Reels.\n'
        'Respond ONLY with a JSON array (no markdown, no preamble) of exactly 5 objects:\n'
        '[{"topic": "short punchy topic title", "angle": "one-line emotional hook angle"}]'
    )
    text = await _call(
        [{"role": "user", "content": prompt}],
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
    )
    raw = _parse_json(text)
    return [TrendingTopic(**t) for t in raw[:5]]


def _script_prompt(category: str, seed_topic: str | None, user_draft: str | None,
                   correction: str | None = None) -> str:
    if user_draft:
        source = f"Base it on this user draft/idea:\n{user_draft}"
    elif seed_topic:
        source = f"Topic: {seed_topic}"
    else:
        source = f"Topic category: {category}"

    base = f"""You are a script agent for podcast-style Instagram Reels about mental wellness and recovery.
{source}

Write a Q&A podcast reel script. Host asks, Guest answers. Hard rules:
- TOTAL spoken duration MUST be between {MIN_SEC:.0f} and {MAX_SEC:.0f} seconds. Estimate at ~2.4 words/second.
- Structure: thumbnail hook line (1-2s) -> host question (max 7s) -> guest answer split into 2-3 lines (~30-35s total) -> host acknowledgement + CTA (max 5s).
- Every line carries an emotion tag from: curious, calm, empathetic, hopeful, serious, warm, reassuring.
- Conversational, research-grounded, emotionally resonant. No medical advice or diagnosis claims.

Respond ONLY with JSON, no markdown:
{{"title": "...", "lines": [{{"speaker": "HOST", "text": "...", "emotion": "curious", "seconds": 6}}]}}"""
    if correction:
        base += f"\n\nIMPORTANT CORRECTION: {correction}"
    return base


async def generate_script(category: str, seed_topic: str | None,
                          user_draft: str | None) -> ScriptPackage:
    """Generate a script; retry once with a correction if duration is out of budget."""
    correction = None
    last_pkg: ScriptPackage | None = None
    for attempt in range(2):
        text = await _call(
            [{"role": "user", "content": _script_prompt(category, seed_topic, user_draft, correction)}],
            max_tokens=1800,
        )
        pkg = ScriptPackage(**_parse_json(text))
        total = pkg.total_seconds
        if MIN_SEC <= total <= MAX_SEC:
            return pkg
        last_pkg = pkg
        correction = (
            f"Your previous script totaled {total:.0f}s which is outside the "
            f"{MIN_SEC:.0f}-{MAX_SEC:.0f}s budget. Rewrite so line durations sum to ~47s."
        )
        logger.warning("Script attempt %d out of budget (%.1fs), retrying", attempt + 1, total)
    return last_pkg  # return best effort; frontend shows the budget warning
