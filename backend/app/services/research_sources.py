"""Live research signals for topic discovery — YouTube Data API v3 + Serper web search."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
SERPER_SEARCH_URL = "https://google.serper.dev/search"


@dataclass
class ResearchSignal:
    source: str
    title: str
    snippet: str
    url: str | None = None


def _category_query(category: str) -> str:
    return f"{category} mental health wellness shorts reels trending"


async def fetch_youtube_signals(category: str, *, max_results: int = 8) -> list[ResearchSignal]:
    """Recent high-interest YouTube videos for the category (search API)."""
    key = get_settings().youtube_api_key.strip()
    if not key:
        return []

    s = get_settings()
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=s.research_lookback_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "part": "snippet",
        "q": _category_query(category),
        "type": "video",
        "order": "viewCount",
        "publishedAfter": published_after,
        "maxResults": min(max_results, 25),
        "regionCode": s.youtube_region_code,
        "relevanceLanguage": s.youtube_relevance_language,
        "key": key,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(YOUTUBE_SEARCH_URL, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("YouTube research failed: %s", e)
        return []

    out: list[ResearchSignal] = []
    for item in data.get("items", []):
        snip = item.get("snippet") or {}
        vid = (item.get("id") or {}).get("videoId", "")
        title = (snip.get("title") or "").strip()
        if not title:
            continue
        desc = (snip.get("description") or "")[:240].replace("\n", " ").strip()
        channel = (snip.get("channelTitle") or "").strip()
        snippet = f"{desc} (channel: {channel})" if channel else desc
        out.append(ResearchSignal(
            source="youtube",
            title=title,
            snippet=snippet or title,
            url=f"https://www.youtube.com/watch?v={vid}" if vid else None,
        ))
    logger.info("YouTube research: %d signals for %r", len(out), category)
    return out


async def fetch_serper_signals(category: str, *, max_results: int = 8) -> list[ResearchSignal]:
    """Web / news snippets via Serper (Google results API)."""
    key = get_settings().serper_api_key.strip()
    if not key:
        return []

    s = get_settings()
    queries = [
        f"trending {category} mental health instagram reels this week",
        f"viral {category} wellness topics news {datetime.now(timezone.utc).year}",
    ]

    out: list[ResearchSignal] = []
    headers = {"X-API-KEY": key, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as client:
        for q in queries:
            if len(out) >= max_results:
                break
            body = {
                "q": q,
                "num": min(max_results, 10),
                "gl": s.serper_gl,
                "hl": s.serper_hl,
            }
            try:
                r = await client.post(SERPER_SEARCH_URL, headers=headers, json=body)
                r.raise_for_status()
                data = r.json()
            except Exception as e:  # noqa: BLE001
                logger.warning("Serper research failed for %r: %s", q, e)
                continue

            for block in ("organic", "news"):
                for item in data.get(block, []):
                    if len(out) >= max_results:
                        break
                    title = (item.get("title") or "").strip()
                    if not title:
                        continue
                    snippet = (item.get("snippet") or item.get("description") or "")[:280]
                    out.append(ResearchSignal(
                        source="serper",
                        title=title,
                        snippet=snippet.replace("\n", " "),
                        url=item.get("link"),
                    ))

    logger.info("Serper research: %d signals for %r", len(out), category)
    return out


async def gather_research_signals(category: str) -> list[ResearchSignal]:
    """Fetch YouTube + Serper in parallel; failures on one side do not block the other."""
    yt_task = fetch_youtube_signals(category)
    serper_task = fetch_serper_signals(category)
    yt, serper = await asyncio.gather(yt_task, serper_task)
    return yt + serper


def format_signals_for_prompt(signals: list[ResearchSignal]) -> str:
    if not signals:
        return "(No live research data — use your best judgment for timely topics.)"
    lines = []
    for i, sig in enumerate(signals, 1):
        src = sig.source.upper()
        extra = f" | {sig.url}" if sig.url else ""
        lines.append(f"{i}. [{src}] {sig.title} — {sig.snippet}{extra}")
    return "\n".join(lines)
