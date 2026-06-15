# from typing import Literal, Optional
# from pydantic import BaseModel, Field


# # ── Research ──────────────────────────────────────────────
# class ResearchRequest(BaseModel):
#     category: str = Field(..., min_length=2, max_length=80)


# class TrendingTopic(BaseModel):
#     topic: str
#     angle: str


# class ResearchResponse(BaseModel):
#     topics: list[TrendingTopic]


# # ── Script (canonical Script Package — reused by all later stages) ──
# class ScriptRequest(BaseModel):
#     category: str = Field(..., min_length=2, max_length=80)
#     seed_topic: Optional[str] = Field(None, max_length=300)
#     user_draft: Optional[str] = Field(None, max_length=4000)


# class ScriptLine(BaseModel):
#     speaker: Literal["HOST", "GUEST"]
#     text: str
#     emotion: str
#     seconds: float


# class ScriptPackage(BaseModel):
#     title: str
#     lines: list[ScriptLine]

#     @property
#     def total_seconds(self) -> float:
#         return sum(l.seconds for l in self.lines)


# # ── TTS Preview ───────────────────────────────────────────
# class TTSPreviewRequest(BaseModel):
#     lines: list[ScriptLine]
#     host_voice_id: str
#     guest_voice_id: str


# class TTSLineAudio(BaseModel):
#     index: int
#     speaker: str
#     audio_base64: str  # mp3 bytes, base64-encoded
#     mime_type: str = "audio/mpeg"


# class TTSPreviewResponse(BaseModel):
#     clips: list[TTSLineAudio]


# class VoiceInfo(BaseModel):
#     voice_id: str
#     name: str
#     category: Optional[str] = None


# # ── Render (final audio + subtitles) ─────────────────────
# class RenderRequest(BaseModel):
#     title: str = "reel"
#     lines: list[ScriptLine]
#     host_voice_id: str
#     guest_voice_id: str


# class SegmentInfo(BaseModel):
#     index: int
#     start_second: float
#     end_second: float
#     duration: float
#     text: str
#     audio_url: str
#     speaker: str = "HOST"


# class RenderResponse(BaseModel):
#     render_id: str
#     audio_url: str
#     srt_url: str
#     script_url: str
#     total_seconds: float
#     segments: list[SegmentInfo] = []


# # ── Scene Blueprint (Stage 4) ─────────────────────────────
# class SegmentSpec(BaseModel):
#     index: int
#     start_second: float
#     end_second: float
#     text: str
#     speaker: str = "HOST"


# class ScenePlanRequest(BaseModel):
#     title: str
#     lines: list[ScriptLine]
#     segments: Optional[list[SegmentSpec]] = None


# class Scene(BaseModel):
#     scene_number: int
#     start_second: float
#     end_second: float
#     speaker_on_camera: str
#     character_action: str
#     facial_expression: str
#     camera_movement: str
#     background_environment: str
#     # richer, script-derived direction (optional so old blueprints still load)
#     body_language: str = ""        # gestures, posture, hand movement tied to the line
#     eye_contact: str = ""          # where they look: at the other person, mic, away, occasional camera
#     emotional_tone: str = ""       # the felt tone of the line (serious, light, playful, reflective)
#     humor: str = ""                # optional off-hand light moment / aside, if the topic allows
#     reaction_cue: str = ""         # what the LISTENER is doing (for reaction-shot inserts)


# class SceneBlueprint(BaseModel):
#     title: str
#     scenes: list[Scene]


# # ── Video generation (Higgsfield) ─────────────────────────
# class VideoGenRequest(BaseModel):
#     render_id: str
#     blueprint: SceneBlueprint
#     force_regen_identity: bool = False  # set true to ignore the identity cache
#     force_regen_scenes: bool = False    # set true to regenerate scene clips that already exist



from typing import Literal, Optional
from pydantic import BaseModel, Field


# ── Research ──────────────────────────────────────────────
class ResearchRequest(BaseModel):
    category: str = Field(..., min_length=2, max_length=80)


class TrendingTopic(BaseModel):
    topic: str
    angle: str


class ResearchResponse(BaseModel):
    topics: list[TrendingTopic]


# ── Script (canonical Script Package — reused by all later stages) ──
class ScriptRequest(BaseModel):
    category: str = Field(..., min_length=2, max_length=80)
    seed_topic: Optional[str] = Field(None, max_length=300)
    user_draft: Optional[str] = Field(None, max_length=4000)


class ScriptLine(BaseModel):
    speaker: Literal["HOST", "GUEST"]
    text: str
    emotion: str
    seconds: float


class ScriptPackage(BaseModel):
    title: str
    lines: list[ScriptLine]

    @property
    def total_seconds(self) -> float:
        return sum(l.seconds for l in self.lines)


# ── TTS Preview ───────────────────────────────────────────
class TTSPreviewRequest(BaseModel):
    lines: list[ScriptLine]
    host_voice_id: str
    guest_voice_id: str


class TTSLineAudio(BaseModel):
    index: int
    speaker: str
    audio_base64: str  # mp3 bytes, base64-encoded
    mime_type: str = "audio/mpeg"


class TTSPreviewResponse(BaseModel):
    clips: list[TTSLineAudio]


class VoiceInfo(BaseModel):
    voice_id: str
    name: str
    category: Optional[str] = None


# ── Render (final audio + subtitles) ─────────────────────
class RenderRequest(BaseModel):
    title: str = "reel"
    lines: list[ScriptLine]
    host_voice_id: str
    guest_voice_id: str


class SegmentInfo(BaseModel):
    index: int
    start_second: float
    end_second: float
    duration: float
    text: str
    audio_url: str
    speaker: str = "HOST"


class RenderResponse(BaseModel):
    render_id: str
    audio_url: str
    srt_url: str
    script_url: str
    total_seconds: float
    segments: list[SegmentInfo] = []


# ── Scene Blueprint (Stage 4) ─────────────────────────────
class SegmentSpec(BaseModel):
    index: int
    start_second: float
    end_second: float
    text: str
    speaker: str = "HOST"


class ScenePlanRequest(BaseModel):
    title: str
    lines: list[ScriptLine]
    segments: Optional[list[SegmentSpec]] = None


class Scene(BaseModel):
    scene_number: int
    start_second: float
    end_second: float
    speaker_on_camera: str
    character_action: str
    facial_expression: str
    camera_movement: str
    background_environment: str
    # richer, script-derived direction (optional so old blueprints still load)
    body_language: str = ""        # gestures, posture, hand movement tied to the line
    eye_contact: str = ""          # where they look: at the other person, mic, away, occasional camera
    emotional_tone: str = ""       # the felt tone of the line (serious, light, playful, reflective)
    humor: str = ""                # optional off-hand light moment / aside, if the topic allows
    reaction_cue: str = ""         # what the LISTENER is doing (for reaction-shot inserts)


class SceneBlueprint(BaseModel):
    title: str
    scenes: list[Scene]


# ── Video generation (Higgsfield) ─────────────────────────
class VideoGenRequest(BaseModel):
    render_id: str
    blueprint: SceneBlueprint
    force_regen_identity: bool = False  # set true to ignore the identity cache
    force_regen_scenes: bool = False    # set true to regenerate scene clips that already exist