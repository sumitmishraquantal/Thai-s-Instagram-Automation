from typing import Literal, Optional

from pydantic import BaseModel, Field


class TrendingTopic(BaseModel):
    topic: str
    angle: str


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


class TTSLineAudio(BaseModel):
    index: int
    speaker: str
    audio_base64: str
    mime_type: str = "audio/mpeg"


class VoiceInfo(BaseModel):
    voice_id: str
    name: str
    category: Optional[str] = None


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


class SegmentSpec(BaseModel):
    index: int
    start_second: float
    end_second: float
    text: str
    speaker: str = "HOST"


class Scene(BaseModel):
    scene_number: int
    start_second: float
    end_second: float
    speaker_on_camera: str
    character_action: str
    facial_expression: str
    camera_movement: str
    background_environment: str
    body_language: str = ""
    eye_contact: str = ""
    emotional_tone: str = ""
    humor: str = ""
    reaction_cue: str = ""


class SceneBlueprint(BaseModel):
    title: str
    scenes: list[Scene]


class PipelineRequest(BaseModel):
    category: Optional[str] = Field(None, max_length=80)


class PipelineResult(BaseModel):
    render_id: str
    job_id: str = ""
    title: str
    category: str
    selected_topic: TrendingTopic
    merged_video_path: str = ""
    audio_path: str
    status: str
    approval_id: Optional[str] = None
    message: Optional[str] = None
    gdrive_upload: Optional[dict] = None
