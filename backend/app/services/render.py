"""Render service — Milestone 2.

Synthesizes the full conversation, stitches it into one continuous mp3 with
natural pauses between speaker turns, and generates an SRT subtitle file timed
from the *actual* audio durations (not the script estimates).

Outputs land in backend/renders/<render_id>/ and are served statically.
"""
import base64
import io
import json
import logging
import uuid
from pathlib import Path

from pydub import AudioSegment, silence

from ..schemas import RenderResponse, ScriptLine, SegmentInfo
from . import elevenlabs_client

logger = logging.getLogger(__name__)

RENDERS_DIR = Path(__file__).resolve().parent.parent.parent / "renders"
RENDERS_DIR.mkdir(exist_ok=True)

TURN_PAUSE_MS = 380   # natural beat between speaker turns
LINE_PAUSE_MS = 280   # brief pause between consecutive lines from the same speaker


def _format_ts(ms: int) -> str:
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms_ = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms_:03d}"


def _build_srt(cues: list[tuple[int, int, str]]) -> str:
    out = []
    for i, (start, end, text) in enumerate(cues, 1):
        out.append(f"{i}\n{_format_ts(start)} --> {_format_ts(end)}\n{text}\n")
    return "\n".join(out)


def _split_for_subtitles(text: str, start_ms: int, end_ms: int,
                         max_chars: int = 42) -> list[tuple[int, int, str]]:
    """Split a long turn into short subtitle cues, time-allocated by length."""
    words = text.split()
    chunks, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > max_chars and cur:
            chunks.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        chunks.append(cur)

    total_chars = sum(len(c) for c in chunks) or 1
    span = end_ms - start_ms
    cues, cursor = [], start_ms
    for c in chunks:
        dur = int(span * len(c) / total_chars)
        cues.append((cursor, cursor + dur, c))
        cursor += dur
    if cues:
        cues[-1] = (cues[-1][0], end_ms, cues[-1][2])
    return cues


# ── Segmentation for video generation ─────────────────────
# One script line = one TTS clip = one video scene. Cuts never land mid-sentence
# because we do not merge lines or time-split guest answers anymore.
MAX_LINE_MS = 13_500   # fallback split only if a single line runs long in practice


def _detected_silences_within(final: AudioSegment, start: int, end: int) -> list[int]:
    """Midpoints of natural pauses inside one speaker turn (for sub-splitting
    long turns). Cuts only land in silence, so no word is clipped."""
    seg = final[start:end]
    sils = silence.detect_silence(seg, min_silence_len=160, silence_thresh=seg.dBFS - 14)
    out = []
    for sstart, send in sils:
        if 400 < sstart and send < len(seg) - 400:
            out.append(start + (sstart + send) // 2)
    return out


def compute_segments(final: AudioSegment,
                     turn_spans: list[tuple[int, int, str, str]]) -> list[tuple[int, int, str]]:
    """One segment per script line so every scene ends on a complete sentence.

    Returns (start_ms, end_ms, speaker).
    """
    segments: list[tuple[int, int, str]] = []
    for (t_start, t_end, _text, speaker) in turn_spans:
        turn_len = t_end - t_start
        if turn_len <= MAX_LINE_MS:
            segments.append((t_start, t_end, speaker))
            continue

        # Rare fallback: a single line ran longer than expected — split only at
        # detected pauses inside that line (never merge lines upstream).
        cands = _detected_silences_within(final, t_start, t_end)
        if not cands:
            segments.append((t_start, t_end, speaker))
            continue
        n = max(2, -(-turn_len // MAX_LINE_MS))
        piece = turn_len / n
        boundaries = [t_start]
        prev = t_start
        for k in range(1, n):
            ideal = int(t_start + k * piece)
            ahead = [c for c in cands if prev < c < t_end]
            cut = min(ahead, key=lambda c: abs(c - ideal)) if ahead else ideal
            if cut <= prev:
                cut = ideal
            boundaries.append(cut)
            prev = cut
        boundaries.append(t_end)
        for i in range(len(boundaries) - 1):
            segments.append((boundaries[i], boundaries[i + 1], speaker))

    if not segments:
        segments = [(0, len(final), "HOST")]
    return segments


def _segment_text(start: int, end: int,
                  turn_spans: list[tuple[int, int, str, str]]) -> str:
    for s, e, text, _sp in turn_spans:
        if s == start and e == end:
            return text
    parts = [t for (s, e, t, _sp) in turn_spans if s < end and e > start]
    return " ".join(parts)


async def render_final_audio(title: str, lines: list[ScriptLine],
                             host_voice_id: str, guest_voice_id: str) -> RenderResponse:
    clips = await elevenlabs_client.synthesize_preview(lines, host_voice_id, guest_voice_id)

    render_id = uuid.uuid4().hex[:10]
    out_dir = RENDERS_DIR / render_id
    out_dir.mkdir(parents=True)

    final = AudioSegment.silent(duration=150)
    cues: list[tuple[int, int, str]] = []

    turn_spans: list[tuple[int, int, str, str]] = []
    for i, (line, clip) in enumerate(zip(lines, clips)):
        seg = AudioSegment.from_file(
            io.BytesIO(base64.b64decode(clip.audio_base64)), format="mp3"
        )
        start_ms = len(final)
        final += seg
        end_ms = len(final)
        turn_spans.append((start_ms, end_ms, line.text, line.speaker))
        cues.extend(_split_for_subtitles(line.text, start_ms, end_ms))
        if i < len(lines) - 1:
            pause = (LINE_PAUSE_MS if lines[i + 1].speaker == line.speaker
                     else TURN_PAUSE_MS)
            final += AudioSegment.silent(duration=pause)

    final = final.fade_out(200)
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in title)[:40] or "reel"

    audio_path = out_dir / f"{safe}.mp3"
    final.export(audio_path, format="mp3", bitrate="192k")

    srt_path = out_dir / f"{safe}.srt"
    srt_path.write_text(_build_srt(cues), encoding="utf-8")

    script_path = out_dir / f"{safe}.script.json"
    script_path.write_text(
        json.dumps({"title": title, "lines": [l.model_dump() for l in lines]}, indent=2),
        encoding="utf-8",
    )

    # One segment per script line — scene cuts align with sentence boundaries
    seg_spans = compute_segments(final, turn_spans)
    seg_infos: list[SegmentInfo] = []
    for i, (s, e, speaker) in enumerate(seg_spans, 1):
        seg_audio = final[s:e]
        seg_name = f"segment_{i:02d}.mp3"
        seg_audio.export(out_dir / seg_name, format="mp3", bitrate="192k")
        seg_infos.append(SegmentInfo(
            index=i,
            start_second=round(s / 1000, 2),
            end_second=round(e / 1000, 2),
            duration=round((e - s) / 1000, 2),
            text=_segment_text(s, e, turn_spans),
            audio_url=f"/renders/{render_id}/{seg_name}",
            speaker=speaker,
        ))
    (out_dir / "segments.json").write_text(
        json.dumps([si.model_dump() for si in seg_infos], indent=2), encoding="utf-8"
    )

    total = len(final) / 1000
    logger.info("Render %s complete: %.1fs, %d segments", render_id, total, len(seg_infos))
    return RenderResponse(
        render_id=render_id,
        audio_url=f"/renders/{render_id}/{audio_path.name}",
        srt_url=f"/renders/{render_id}/{srt_path.name}",
        script_url=f"/renders/{render_id}/{script_path.name}",
        total_seconds=round(total, 1),
        segments=seg_infos,
    )
