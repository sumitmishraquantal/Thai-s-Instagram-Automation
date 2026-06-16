from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Provider for research + script agents: "anthropic" | "groq" | "mock"
    llm_provider: str = "groq"

    anthropic_api_key: str = ""
    groq_api_key: str = ""
    elevenlabs_api_key: str = ""

    # ElevenLabs voice IDs for HOST / GUEST
    host_voice_id: str = "CwhRBWXzGAHq8TQ4Fs17"
    guest_voice_id: str = "RDjgzX0qNSGQZkgo5KTT"

    # Blank = LLM picks from CATEGORIES in llm.py
    default_category: str = ""

    # Video pipeline provider: "hf_mcp" | "hf_platform" | "fal"
    video_provider: str = "hf_platform"

    # fal.ai (FAL_KEY from fal.ai/dashboard/keys)
    fal_key: str = ""
    fal_video_model: str = "bytedance/seedance-2.0/reference-to-video"
    fal_image_edit_model: str = "fal-ai/bytedance/seedream/v4/edit"
    fal_image_model: str = "fal-ai/bytedance/seedream/v4/text-to-image"
    fal_video_resolution: str = "720p"

    # Higgsfield (Key ID + Secret from cloud.higgsfield.ai)
    hf_api_key: str = ""
    hf_api_secret: str = ""
    hf_mcp_url: str = "https://mcp.higgsfield.ai/mcp"
    hf_mcp_callback_port: int = 3030
    hf_max_credits_per_clip: int = 60
    scene_limit: int = 0
    chain_scenes: bool = True
    reaction_shots_enabled: bool = True
    reaction_min_seconds: float = 7.0
    use_director_skills: bool = True
    seedance_bilingual_prompt: bool = False
    establishing_two_shot: bool = True

    # Scheduled trigger
    schedule_enabled: bool = False
    schedule_cron: str = ""
    schedule_workflow: str = "podcast"
    schedule_topic: str = ""

    # Verified platform.higgsfield.ai model IDs
    hf_image_model: str = "higgsfield-ai/soul/standard"
    hf_image_ref_model: str = "higgsfield-ai/soul/reference"
    hf_video_model: str = "bytedance/seedance/v1/lite/image-to-video"
    hf_video_resolution: str = "720"
    hf_image_resolution: str = "1080p"
    hf_aspect_ratio: str = "9:16"

    anthropic_model: str = "claude-sonnet-4-5"
    groq_model: str = "llama-3.3-70b-versatile"
    elevenlabs_model: str = "eleven_v3"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
