import asyncio
import base64
import logging

import httpx

from ..config import get_settings
from ..schemas import ScriptLine, TTSLineAudio, VoiceInfo

logger = logging.getLogger(__name__)

EL_BASE = "https://api.elevenlabs.io/v1"

EMOTION_TAGS = {
    "curious": "[curious]",
    "calm": "[calm]",
    "empathetic": "[warm]",
    "hopeful": "[hopeful]",
    "serious": "[serious]",
    "warm": "[warm]",
    "reassuring": "[reassuring]",
}


def _headers() -> dict:
    return {"xi-api-key": get_settings().elevenlabs_api_key}


async def list_voices() -> list[VoiceInfo]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{EL_BASE}/voices", headers=_headers())
        r.raise_for_status()
        data = r.json()
    return [
        VoiceInfo(
            voice_id=v["voice_id"],
            name=v.get("name", "Unnamed"),
            category=v.get("category"),
        )
        for v in data.get("voices", [])
    ]


async def _synthesize_line(client: httpx.AsyncClient, index: int,
                           line: ScriptLine, voice_id: str) -> TTSLineAudio:
    s = get_settings()
    tag = EMOTION_TAGS.get(line.emotion.lower(), "")
    payload = {
        "text": f"{tag} {line.text}".strip(),
        "model_id": s.elevenlabs_model,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    r = await client.post(
        f"{EL_BASE}/text-to-speech/{voice_id}",
        headers={**_headers(), "content-type": "application/json"},
        json=payload,
    )
    r.raise_for_status()
    return TTSLineAudio(
        index=index,
        speaker=line.speaker,
        audio_base64=base64.b64encode(r.content).decode(),
    )


async def synthesize_preview(lines: list[ScriptLine], host_voice_id: str,
                             guest_voice_id: str) -> list[TTSLineAudio]:
    """Synthesize each line with the mapped voice. Limited concurrency to
    respect ElevenLabs rate limits."""
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(timeout=120) as client:

        async def task(i: int, line: ScriptLine):
            voice = host_voice_id if line.speaker == "HOST" else guest_voice_id
            async with sem:
                return await _synthesize_line(client, i, line, voice)

        results = await asyncio.gather(*(task(i, l) for i, l in enumerate(lines)))
    return sorted(results, key=lambda c: c.index)
