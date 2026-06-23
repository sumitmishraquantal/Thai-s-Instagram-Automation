#!/usr/bin/env python3
"""Send a test approval email from local fixture files.

Put files in:
    backend/test_fixtures/approval_mail/

  *.mp4         -> scene clips (sorted by filename)
  *.png         -> thumbnail (prefers thumbnail.png)
  script.json   -> optional script (or any *.script.json)

Usage (from backend/):
    python test_approval_mail.py

Requires OWNER_EMAILS and email transport in .env (SMTP / Composio).
Approve/decline links need the backend running:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path

from app.config import get_settings
from app.services import approval_email, approval_gate, approvals, higgsfield_video

BACKEND_ROOT = Path(__file__).resolve().parent
FIXTURE_DIR = BACKEND_ROOT / "test_fixtures" / "approval_mail"
RENDERS_DIR = BACKEND_ROOT / "renders"
TEST_RENDER_ID = "_approval_mail_test"


def scan_fixture_media(fixture_dir: Path) -> tuple[list[Path], Path | None, Path | None]:
    if not fixture_dir.is_dir():
        return [], None, None

    clips = sorted(fixture_dir.glob("*.mp4"))
    pngs = sorted(fixture_dir.glob("*.png"))

    thumbnail: Path | None = None
    for p in pngs:
        if p.name.lower() == "thumbnail.png":
            thumbnail = p
            break
    if thumbnail is None and pngs:
        thumbnail = pngs[0]

    script_file: Path | None = fixture_dir / "script.json"
    if not script_file.is_file():
        script_candidates = list(fixture_dir.glob("*.script.json"))
        script_file = script_candidates[0] if script_candidates else None

    return clips, thumbnail, script_file


def _sync_render_folder(
    clips: list[Path],
    thumbnail: Path | None,
    script_file: Path | None,
) -> None:
    """Copy fixtures into renders/ so media URLs and script loading match production."""
    base = RENDERS_DIR / TEST_RENDER_ID
    vid_dir = base / "video"
    if base.exists():
        shutil.rmtree(base)
    vid_dir.mkdir(parents=True)
    for clip in clips:
        shutil.copy2(clip, vid_dir / clip.name)
    if thumbnail is not None:
        shutil.copy2(thumbnail, base / "thumbnail.png")
    if script_file is not None:
        dest_name = script_file.name if script_file.name.endswith(".script.json") else "test.script.json"
        shutil.copy2(script_file, base / dest_name)
    # Optional: blueprint.json / segments.json enable the per-clip Regenerate flow.
    for extra in ("blueprint.json", "segments.json"):
        src = FIXTURE_DIR / extra
        if src.is_file():
            shutil.copy2(src, base / extra)


def main() -> int:
    logging.basicConfig(level=logging.INFO)

    s = get_settings()
    owners = [o.strip() for o in s.owner_emails.split(",") if o.strip()]
    if not owners:
        print("Set OWNER_EMAILS in .env", file=sys.stderr)
        return 1

    # Optional: target an EXISTING real render by id (it already has clips, thumbnail,
    # script, blueprint.json AND segments.json — so the per-clip Regenerate works for
    # real). Usage:  python test_approval_mail.py 2cc36edffa
    arg_render_id = sys.argv[1] if len(sys.argv) > 1 else None
    if arg_render_id:
        render_id = arg_render_id
        base = RENDERS_DIR / render_id
        if not base.is_dir():
            print(f"No render folder at {base}", file=sys.stderr)
            return 1
        if not (base / "video").is_dir() or not list((base / "video").glob("scene_*.mp4")):
            print(f"No scene clips under {base / 'video'}", file=sys.stderr)
            return 1
        print(f"Targeting existing render: {render_id}")
        if (base / "segments.json").exists() and (base / "blueprint.json").exists():
            print("  has segments.json + blueprint.json — per-clip Regenerate will work for real.")
        else:
            print("  WARNING: missing segments.json/blueprint.json — Regenerate will refuse for this render.")
    else:
        fixture_dir = FIXTURE_DIR.resolve()
        clips, thumbnail, script_file = scan_fixture_media(fixture_dir)
        if not clips:
            print(f"No .mp4 files in {fixture_dir}", file=sys.stderr)
            print("Add scene clips there, e.g. scene_01.mp4, scene_02.mp4", file=sys.stderr)
            print("Or target a real render:  python test_approval_mail.py <render_id>", file=sys.stderr)
            return 1
        _sync_render_folder(clips, thumbnail, script_file)
        render_id = TEST_RENDER_ID

    payload = approval_gate.build_approval_payload(render_id)
    if payload.get("title") in (render_id, "", None):
        payload["title"] = "Approval mail test reel"

    record = approvals.create_request(
        workflow="publish",
        render_id=render_id,
        owners=owners,
        payload=payload,
    )

    def _resume(rec: dict) -> None:
        higgsfield_video.schedule_gdrive_upload(rec["render_id"])

    approvals.register_resume(record["id"], _resume)

    n_clips = len(payload.get("scene_clips", []))
    script_note = f", {len(payload.get('script_lines', []))} script line(s)" if payload.get("script_lines") else ""
    print(f"Render {render_id}: {n_clips} clip(s)" + script_note)
    if not payload.get("script_lines"):
        print("Tip: add a *.script.json to the render folder to include the script in the email.")
    print(f"Approval id: {record['id']}")
    print(f"Approve/decline base URL: {s.approval_base_url}")

    report = approval_email.send_approval_emails(record)
    print("Send report:", json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())