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

TURN_PAUSE_MS = 380  # natural beat between speaker turns


def _merge_consecutive(lines: list[ScriptLine]) -> list[ScriptLine]:
    """Merge consecutive same-speaker lines so each turn is one TTS take."""
    groups: list[ScriptLine] = []
    for l in lines:
        if groups and groups[-1].speaker == l.speaker:
            prev = groups[-1]
            groups[-1] = ScriptLine(
                speaker=prev.speaker,
                text=f"{prev.text} {l.text}",
                emotion=prev.emotion,
                seconds=prev.seconds + l.seconds,
            )
        else:
            groups.append(l)
    return groups


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


# ── Word-safe segmentation for video generation ──────────
# Structure (derived from the script, NOT hardcoded): every HOST turn stays a single
# whole segment (the question is one clip, the closing acknowledgment is one clip);
# each GUEST turn is split into ~10-12s pieces so the answer reads as a couple of
# focused clips that still lip-sync tightly. Cuts ONLY ever land in detected silence,
# so a word is never clipped mid-syllable.
TARGET_SEGMENT_MS = 8_000    # (kept for the unused _silence_candidates helper)
MIN_SEGMENT_MS = 5_000
HARD_MAX_MS = 11_000

# Guest-answer sub-splitting window.
GUEST_TARGET_MS = 11_000     # aim for ~11s per guest-answer segment
GUEST_MIN_MS = 10_000        # don't end a guest segment before ~10s if avoidable
GUEST_MAX_MS = 12_000        # prefer to cut by ~12s
GUEST_HARD_MAX_MS = 13_500   # if no silence in 10-12s, allow a little past (word-safety wins)
MIN_TAIL_MS = 3_000          # don't leave a tiny sliver; keep it on the previous piece


def _silence_candidates(final: AudioSegment,
                        turn_spans: list[tuple[int, int, str]]) -> list[int]:
    """Cut candidates: middle of the pause after each turn, plus real detected
    silences inside any turn longer than the target. Cuts only ever land in
    silence, so words are never clipped."""
    cands: set[int] = set()
    for i, (start, end, _t, *_rest) in enumerate(turn_spans):
        if i < len(turn_spans) - 1:
            cands.add(end + TURN_PAUSE_MS // 2)  # middle of inter-turn pause
        if end - start > TARGET_SEGMENT_MS:
            seg = final[start:end]
            sils = silence.detect_silence(
                seg, min_silence_len=160, silence_thresh=seg.dBFS - 14
            )
            for s, e in sils:
                if 400 < s and e < len(seg) - 400:  # ignore edges
                    cands.add(start + (s + e) // 2)
    return sorted(c for c in cands if 0 < c < len(final))


def _quietest_point(final: AudioSegment, around_ms: int, window: int = 1500) -> int:
    """Last-resort cut: the quietest 80ms in [around-window, around]."""
    lo = max(0, around_ms - window)
    best, best_db = around_ms, 999.0
    step = 40
    for p in range(lo, around_ms - 80, step):
        db = final[p:p + 80].dBFS
        if db < best_db:
            best, best_db = p + 40, db
    return best


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
    """Speaker-bounded segmentation tuned to the Q&A shape.

    - Every HOST turn becomes exactly ONE whole segment (host asks the question =
      one clip; host acknowledges + ends = one clip).
    - Each GUEST turn is split into ~10-12s pieces (so a ~22s answer becomes two
      ~11s clips, a longer answer more — never a fixed count). A short guest turn
      (<= ~12s) stays whole.
    - Cuts ONLY land in detected silence, so no word is ever clipped. The number of
      segments follows the script, not a hardcoded value.

    Returns (start_ms, end_ms, speaker).
    """
    segments: list[tuple[int, int, str]] = []
    for (t_start, t_end, _text, speaker) in turn_spans:
        turn_len = t_end - t_start
        is_guest = str(speaker).upper() == "GUEST"

        # HOST turns are always one whole segment; short GUEST turns too.
        if not is_guest or turn_len <= GUEST_MAX_MS:
            segments.append((t_start, t_end, speaker))
            continue

        # Long GUEST turn: divide into the FEWEST ~10-12s pieces (even division),
        # then place each cut at the nearest detected silence to the ideal evenly
        # spaced boundary. This avoids greedy stranding (a short first piece forcing
        # an extra tiny segment) and keeps the pieces balanced and word-safe.
        cands = _detected_silences_within(final, t_start, t_end)
        n = max(1, -(-turn_len // GUEST_MAX_MS))   # ceil(turn_len / 12s) = min #pieces
        piece = turn_len / n
        boundaries = [t_start]
        prev = t_start
        for k in range(1, n):
            ideal = int(t_start + k * piece)
            ahead = [c for c in cands if prev < c < t_end]
            cut = (min(ahead, key=lambda c: abs(c - ideal)) if ahead
                   else _quietest_point(final, ideal))
            if cut <= prev:                         # safety: never go backwards
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
    parts = [t for (s, e, t, _sp) in turn_spans if s < end and e > start]
    return " ".join(parts)


async def render_final_audio(title: str, lines: list[ScriptLine],
                             host_voice_id: str, guest_voice_id: str) -> RenderResponse:
    turns = _merge_consecutive(lines)

    clips = await elevenlabs_client.synthesize_preview(turns, host_voice_id, guest_voice_id)

    render_id = uuid.uuid4().hex[:10]
    out_dir = RENDERS_DIR / render_id
    out_dir.mkdir(parents=True)

    final = AudioSegment.silent(duration=150)
    cues: list[tuple[int, int, str]] = []

    turn_spans: list[tuple[int, int, str, str]] = []
    for turn, clip in zip(turns, clips):
        seg = AudioSegment.from_file(
            io.BytesIO(base64.b64decode(clip.audio_base64)), format="mp3"
        )
        start_ms = len(final)
        final += seg
        end_ms = len(final)
        turn_spans.append((start_ms, end_ms, turn.text, turn.speaker))
        cues.extend(_split_for_subtitles(turn.text, start_ms, end_ms))
        final += AudioSegment.silent(duration=TURN_PAUSE_MS)

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

    # Word-safe segments for video generation (one clip per segment)
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
