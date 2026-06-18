# from functools import lru_cache
# from pydantic_settings import BaseSettings


# class Settings(BaseSettings):
#     # Provider for research + script agents: "anthropic" | "groq" | "mock"
#     llm_provider: str = "anthropic"

#     anthropic_api_key: str = ""
#     groq_api_key: str = ""
#     elevenlabs_api_key: str

#     # Video provider: "fal" (Seedance 2.0 — recommended) | "higgsfield" (Seedance v1)
#     video_provider: str = "fal"

#     # fal.ai (FAL_KEY from fal.ai/dashboard/keys)
#     fal_key: str = ""
#     fal_video_model: str = "bytedance/seedance-2.0/reference-to-video"
#     fal_image_edit_model: str = "fal-ai/bytedance/seedream/v4/edit"
#     fal_image_model: str = "fal-ai/bytedance/seedream/v4/text-to-image"
#     fal_video_resolution: str = "720p"

#     # Higgsfield (Key ID + Secret from cloud.higgsfield.ai)
#     hf_api_key: str = ""
#     hf_api_secret: str = ""
#     # Video pipeline provider: "hf_mcp" = Seedance 2.0 via mcp.higgsfield.ai (OAuth)
#     #                           "hf_platform" = Seedance v1 Lite via REST API (key id+secret)
#     video_provider: str = "hf_platform"
#     hf_mcp_url: str = "https://mcp.higgsfield.ai/mcp"
#     hf_mcp_callback_port: int = 3030
#     hf_max_credits_per_clip: int = 60   # safety ceiling; Seedance 2.0 std 720p ≈ 45
#     scene_limit: int = 0   # 0 = all scenes; set SCENE_LIMIT=2 to generate only the first 2 (testing)
#     chain_scenes: bool = True   # scene N+1 starts from scene N's last frame (continuous motion)
#     reaction_shots_enabled: bool = True   # cut to the listener reacting during long turns
#     reaction_min_seconds: float = 7.0     # add a listener reaction shot for segments at least this long
#     use_director_skills: bool = True      # use the gpt-image-2 / seedance director skills to write prompts
#     seedance_bilingual_prompt: bool = False  # also include the native ZH rewrite in the seedance prompt
#     establishing_two_shot: bool = True    # generate the 'both' two-shot image FOR THE THUMBNAIL (never shown as a scene)
#     two_shot_position: str = "none"        # DEPRECATED: the two-shot is no longer rendered as a video scene
#     vary_across_reels: bool = True        # change poster/decor/wardrobe between reels (consistent within a reel)
#     variation_profile: str = "auto"       # "auto" rotates each reel | a profile id to pin | "none" to disable

#     # ── GDrive upload (via the SAME rclone you use for the editing Routines) ──
#     # When a reel finishes, push ALL its raw scene clips to GDrive in ONE rclone
#     # command (not one-by-one) so the editing Routine picks up the complete set.
#     upload_to_gdrive: bool = True         # master switch — push finished clips to GDrive
#     rclone_remote: str = "gdrive:reel-projects/TEST"  # destination (remote:path); clips land here
#     rclone_exe: str = "rclone"            # path to rclone.exe if it isn't on PATH
#     rclone_config: str = ""   # full path to rclone.conf; "" = let rclone use its default
#     gdrive_upload_what: str = "scenes"    # "scenes" (raw clips) | "reel" (merged) | "all" (clips+reel+thumbnail)
#     gdrive_clip_prefix: str = "RawClip"   # raw clips are renamed <prefix>1, <prefix>2 ... in scene order
#     gdrive_subfolder_per_reel: bool = False  # false = straight into rclone_remote; true = a per-reel subfolder
#     gdrive_delete_local_after_upload: bool = False  # true = remove the local clips once the upload is confirmed

#     # ── Approval workflow ──
#     owner_emails: str = ""                       # comma-separated: a@x.com,b@y.com
#     approval_base_url: str = "http://localhost:8000"
#     smtp_host: str = ""
#     smtp_port: int = 587
#     smtp_user: str = ""
#     smtp_password: str = ""
#     # Email transport: "auto" tries Gmail-via-Composio first, then SMTP, then file.
#     # "gmail" forces Composio Gmail; "smtp" forces SMTP; "file" forces local files.
#     email_transport: str = "auto"
#     composio_api_key: str = ""                   # for automated Gmail send from the backend
#     gmail_sender: str = ""                        # optional explicit From; blank = authed Gmail user
#     composio_user_id: str = ""                    # Composio entity/user id for the Gmail connection; blank = "me"
#     composio_toolkit_version: str = ""            # optional explicit Gmail toolkit version; blank = use client default
#     require_approval: bool = True                # pause for approval before video gen
#     # ── Scheduled trigger ──
#     schedule_enabled: bool = False
#     schedule_cron: str = ""                      # e.g. "0 9 * * *" (daily 9am); empty = off
#     schedule_workflow: str = "podcast"
#     schedule_topic: str = ""                     # optional fixed topic for auto runs

#     # Verified platform.higgsfield.ai model IDs (June 2026)
#     hf_image_model: str = "higgsfield-ai/soul/standard"        # text-to-image fallback
#     hf_image_ref_model: str = "higgsfield-ai/soul/reference"   # 1 reference image -> styled image
#     hf_video_model: str = "bytedance/seedance/v1/lite/image-to-video"
#     hf_video_resolution: str = "1080p"  # seedance allowed: "480p" | "720p" | "1080p" (the 'p' is REQUIRED)
#     hf_image_resolution: str = "1080p" # soul/reference: "720p" | "1080p"
#     hf_aspect_ratio: str = "9:16"

#     anthropic_model: str = "claude-sonnet-4-5"
#     groq_model: str = "llama-3.3-70b-versatile"
#     elevenlabs_model: str = "eleven_v3"

#     # Comma-separated list of allowed frontend origins
#     cors_origins: str = "http://localhost:5173"

#     class Config:
#         env_file = ".env"
#         env_file_encoding = "utf-8"

#     @property
#     def cors_origin_list(self) -> list[str]:
#         return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


# @lru_cache
# def get_settings() -> Settings:
#     return Settings()


from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Provider for research + script agents: "anthropic" | "groq" | "mock"
    llm_provider: str = "anthropic"

    anthropic_api_key: str = ""
    groq_api_key: str = ""
    elevenlabs_api_key: str

    # Video provider: "fal" (Seedance 2.0 — recommended) | "higgsfield" (Seedance v1)
    video_provider: str = "fal"

    # fal.ai (FAL_KEY from fal.ai/dashboard/keys)
    fal_key: str = ""
    fal_video_model: str = "bytedance/seedance-2.0/reference-to-video"
    fal_image_edit_model: str = "fal-ai/bytedance/seedream/v4/edit"
    fal_image_model: str = "fal-ai/bytedance/seedream/v4/text-to-image"
    fal_video_resolution: str = "720p"

    # Higgsfield (Key ID + Secret from cloud.higgsfield.ai)
    hf_api_key: str = ""
    hf_api_secret: str = ""
    # Video pipeline provider: "hf_mcp" = Seedance 2.0 via mcp.higgsfield.ai (OAuth)
    #                           "hf_platform" = Seedance v1 Lite via REST API (key id+secret)
    video_provider: str = "hf_platform"
    hf_mcp_url: str = "https://mcp.higgsfield.ai/mcp"
    hf_mcp_callback_port: int = 3030
    hf_max_credits_per_clip: int = 60   # safety ceiling; Seedance 2.0 std 720p ≈ 45
    scene_limit: int = 0   # 0 = all scenes; set SCENE_LIMIT=2 to generate only the first 2 (testing)
    chain_scenes: bool = True   # scene N+1 starts from scene N's last frame (continuous motion)
    reaction_shots_enabled: bool = True   # cut to the listener reacting during long turns
    reaction_min_seconds: float = 7.0     # add a listener reaction shot for segments at least this long
    use_director_skills: bool = True      # use the gpt-image-2 / seedance director skills to write prompts
    seedance_bilingual_prompt: bool = False  # also include the native ZH rewrite in the seedance prompt
    establishing_two_shot: bool = True    # generate the 'both' two-shot image FOR THE THUMBNAIL (never shown as a scene)
    two_shot_position: str = "none"        # DEPRECATED: the two-shot is no longer rendered as a video scene
    vary_across_reels: bool = True        # change poster/decor/wardrobe between reels (consistent within a reel)
    variation_profile: str = "auto"       # "auto" rotates each reel | a profile id to pin | "none" to disable

    # ── GDrive upload (via the SAME rclone you use for the editing Routines) ──
    # When a reel finishes, push ALL its raw scene clips to GDrive in ONE rclone
    # command (not one-by-one) so the editing Routine picks up the complete set.
    upload_to_gdrive: bool = True         # master switch — push finished clips to GDrive
    rclone_remote: str = "gdrive:reel-projects/TEST"  # destination (remote:path); clips land here
    rclone_exe: str = "rclone"            # path to rclone.exe if it isn't on PATH
    rclone_config: str = ""               # full path to rclone.conf; "" = let rclone use its default location
    gdrive_upload_what: str = "scenes"    # "scenes" (raw clips) | "reel" (merged) | "all" (clips+reel+thumbnail)
    gdrive_clip_prefix: str = "RawClip"   # raw clips are renamed <prefix>1, <prefix>2 ... in scene order
    gdrive_subfolder_per_reel: bool = False  # false = straight into rclone_remote; true = a per-reel subfolder
    gdrive_delete_local_after_upload: bool = False  # true = remove the local clips once the upload is confirmed

    # ── Approval workflow ──
    owner_emails: str = ""                       # comma-separated: a@x.com,b@y.com
    approval_base_url: str = "http://localhost:8000"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    # Email transport: "auto" tries Gmail-via-Composio first, then SMTP, then file.
    # "gmail" forces Composio Gmail; "smtp" forces SMTP; "file" forces local files.
    email_transport: str = "auto"
    composio_api_key: str = ""                   # for automated Gmail send from the backend
    gmail_sender: str = ""                        # optional explicit From; blank = authed Gmail user
    composio_user_id: str = ""                    # Composio entity/user id for the Gmail connection; blank = "me"
    composio_toolkit_version: str = ""            # optional explicit Gmail toolkit version; blank = use client default
    require_approval: bool = True                # pause for approval before video gen
    # ── Scheduled trigger ──
    schedule_enabled: bool = False
    schedule_cron: str = ""                      # e.g. "0 9 * * *" (daily 9am); empty = off
    schedule_workflow: str = "podcast"
    schedule_topic: str = ""                     # optional fixed topic for auto runs

    # Verified platform.higgsfield.ai model IDs (June 2026)
    hf_image_model: str = "higgsfield-ai/soul/standard"        # text-to-image fallback
    hf_image_ref_model: str = "higgsfield-ai/soul/reference"   # 1 reference image -> styled image
    hf_video_model: str = "bytedance/seedance/v1/lite/image-to-video"
    hf_video_resolution: str = "1080p"  # seedance allowed: "480p" | "720p" | "1080p" (the 'p' is REQUIRED)
    hf_image_resolution: str = "1080p" # soul/reference: "720p" | "1080p"
    hf_image_quality: str = "high"     # gpt_image_2 quality: "low" | "medium" | "high" (never leave unset → it defaults to low)
    hf_aspect_ratio: str = "9:16"

    anthropic_model: str = "claude-sonnet-4-5"
    groq_model: str = "llama-3.3-70b-versatile"
    elevenlabs_model: str = "eleven_v3"

    # Comma-separated list of allowed frontend origins
    cors_origins: str = "http://localhost:5173"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()