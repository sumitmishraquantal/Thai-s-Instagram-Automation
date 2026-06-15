# """Media path diagnostic — find the reliable way to register media with Higgsfield.

# The pipeline keeps failing at media_import_url with 'fetch failed'. This script
# isolates JUST the media step: it inspects every media-related MCP tool, prints
# their input schemas, and tries to register a small local image three ways:

#   A) media_upload         (direct file/bytes upload — no URL fetch)
#   B) media_import_url      (current method — fetch by URL; the flaky one)
#   C) generate_image then reuse its job_id as a media reference

# It prints which methods return a usable media UUID. We then wire the pipeline
# to whichever works. Run from backend (venv active):

#     python diagnose_media.py
# """
# import asyncio
# import json
# import sys
# from pathlib import Path

# GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"

# TINY_PNG = bytes.fromhex(
#     "89504e470d0a1a0a0000000d49484452000000040000000408060000007f5ccd"
#     "e10000001249444154789c63fccfc0f09f8118a8a8a80100ffff0a0d02fe8a8b"
#     "5b8d0000000049454e44ae426082"
# )


# def show(status, name, detail=""):
#     c = {"PASS": GREEN, "FAIL": RED, "INFO": YELLOW}[status]
#     print(f"  {c}[{status}]{RESET} {name}" + (f" — {detail}" if detail else ""))


# async def main():
#     from app.config import get_settings
#     from app.services import higgsfield_video as hv
#     from app.services.higgsfield_mcp import _oauth_provider
#     from mcp.client.session import ClientSession
#     from mcp.client.streamable_http import streamablehttp_client
#     from mcp import types as mcp_types

#     s = get_settings()
#     tmp = Path("_media_probe.png")
#     tmp.write_bytes(TINY_PNG)

#     def text_of(res):
#         parts = []
#         if res.structuredContent:
#             parts.append(json.dumps(res.structuredContent))
#         for c in res.content or []:
#             if isinstance(c, mcp_types.TextContent):
#                 parts.append(c.text)
#         return "\n".join(parts)

#     import re
#     UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

#     print("\nMEDIA PATH DIAGNOSTIC\n" + "─" * 60)
#     async with streamablehttp_client(s.hf_mcp_url, auth=_oauth_provider(),
#                                      timeout=120) as (read, write, _):
#         async with ClientSession(read, write) as session:
#             await session.initialize()
#             tools = {t.name: t for t in (await session.list_tools()).tools}

#             # Show schemas for every media tool
#             print("\nMedia-related tool schemas:")
#             for name in ("media_upload", "media_import_url", "media_confirm",
#                          "media_upload_widget", "show_medias"):
#                 if name in tools:
#                     sch = json.dumps(tools[name].inputSchema or {})
#                     print(f"\n  • {name}:\n    {sch[:700]}")
#                 else:
#                     show("INFO", name, "not present")

#             print("\n" + "─" * 60 + "\nRegistration attempts:")

#             # First upload the file to Higgsfield's CDN (this part worked in logs)
#             cdn_url = None
#             try:
#                 cdn_url = await hv._hf_upload(tmp)
#                 show("PASS", "platform upload -> CDN url", cdn_url[:60] + "…")
#             except Exception as e:  # noqa: BLE001
#                 show("FAIL", "platform upload", str(e)[:160])

#             async def call(tool, args):
#                 res = await session.call_tool(tool, args)
#                 t = text_of(res)
#                 if res.isError:
#                     raise RuntimeError(t[:300])
#                 return t

#             # METHOD A — media_upload (direct). Try a few likely arg shapes.
#             if "media_upload" in tools:
#                 worked = False
#                 for desc, args in [
#                     ("params.url", {"params": {"url": cdn_url}}),
#                     ("url", {"url": cdn_url}),
#                     ("params.file_url", {"params": {"file_url": cdn_url}}),
#                 ]:
#                     try:
#                         t = await call("media_upload", args)
#                         u = UUID.search(t)
#                         if u:
#                             show("PASS", f"media_upload [{desc}]", f"uuid {u.group(0)}")
#                             worked = True
#                             break
#                         else:
#                             show("INFO", f"media_upload [{desc}]", f"no uuid: {t[:120]}")
#                     except Exception as e:  # noqa: BLE001
#                         show("INFO", f"media_upload [{desc}]", str(e)[:120])
#                 if not worked:
#                     show("FAIL", "media_upload", "no arg shape returned a uuid")

#             # METHOD B — media_import_url (current; flaky)
#             if cdn_url and "media_import_url" in tools:
#                 ok = False
#                 for attempt in range(3):
#                     try:
#                         t = await call("media_import_url", {"url": cdn_url})
#                         u = UUID.search(t)
#                         if u:
#                             show("PASS", f"media_import_url (try {attempt+1})", f"uuid {u.group(0)}")
#                             ok = True
#                             break
#                         show("INFO", f"media_import_url (try {attempt+1})", t[:120])
#                     except Exception as e:  # noqa: BLE001
#                         show("INFO", f"media_import_url (try {attempt+1})", str(e)[:120])
#                     await asyncio.sleep(2)
#                 if not ok:
#                     show("FAIL", "media_import_url", "failed across 3 retries (the 'fetch failed' bug)")

#     tmp.unlink(missing_ok=True)
#     print("\n" + "─" * 60)
#     print("Send me this whole output. The PASS method is the one we wire into the pipeline.")


# if __name__ == "__main__":
#     asyncio.run(main())

"""Single-scene Seedance 2.0 diagnostic — find the REAL output URL.

The full run returned the same preset clip for every scene
(cdn.higgsfield.ai/job_set_chain_preset/...) — meaning the result parser grabbed
a decoy URL from the submit response instead of polling the job to completion.

This script submits ONE Seedance video (using your already-generated host
identity image + one audio segment) and prints the FULL raw response from
generate_video AND from job_status polling, so we can see exactly which field
holds the finished clip. Cost: one Seedance clip (~45 credits).

Run from backend (venv active):
    python diagnose_seedance.py <render_id>

<render_id> is a folder under backend/renders/ that already has images/host.png
and segments.json (use the most recent successful job's render id).
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import types as mcp_types


def text_of(res):
    parts = []
    if res.structuredContent:
        parts.append("STRUCTURED: " + json.dumps(res.structuredContent, indent=2))
    for c in res.content or []:
        if isinstance(c, mcp_types.TextContent):
            parts.append("TEXT: " + c.text)
    return "\n".join(parts)


async def main():
    if len(sys.argv) != 2:
        print("Usage: python diagnose_seedance.py <render_id>")
        sys.exit(1)
    render_id = sys.argv[1]

    from app.config import get_settings
    from app.services import higgsfield_video as hv
    from app.services.higgsfield_mcp import _oauth_provider, HiggsfieldMCP
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    s = get_settings()
    base = hv.RENDERS_DIR / render_id
    img = base / "images" / "host.png"
    if not img.exists():
        print(f"No {img} — run an image-gen job first or pass a render id that has it.")
        sys.exit(1)
    segs = json.loads((base / "segments.json").read_text())
    seg_audio_mp3 = base / Path(segs[0]["audio_url"]).name
    wav = hv._to_wav(seg_audio_mp3)

    mcp = HiggsfieldMCP()

    async with streamablehttp_client(s.hf_mcp_url, auth=_oauth_provider(),
                                     timeout=180) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}
            mcp._schemas = {n: (t.inputSchema or {}) for n, t in tools.items()}

            print("\n=== generate_video FULL SCHEMA ===")
            print(json.dumps(tools["generate_video"].inputSchema, indent=2)[:2500])

            print("\n=== job_status FULL SCHEMA ===")
            print(json.dumps(tools["job_status"].inputSchema, indent=2)[:1500])

            # Upload media via the working path
            print("\nUploading host image + audio…")
            img_id = await mcp._upload_media_file(session, img)
            aud_id = await mcp._upload_media_file(session, wav)
            print("image media id:", img_id)
            print("audio media id:", aud_id)

            params = {
                "model": "seedance_2_0",
                "prompt": ("Podcast host speaking to camera in a studio, natural lip movement "
                           "synced to the audio, realistic, static shot."),
                "aspect_ratio": "9:16",
                "duration": 6,
                "resolution": "720p",
                "medias": [
                    {"value": img_id, "role": "start_image"},
                    {"value": aud_id, "role": "audio"},
                ],
            }

            print("\n=== get_cost preflight ===")
            res = await session.call_tool("generate_video", {"params": {**params, "get_cost": True}})
            print(text_of(res))

            print("\n=== SUBMIT generate_video — FULL RAW RESPONSE ===")
            res = await session.call_tool("generate_video", {"params": params})
            submit_text = text_of(res)
            print(submit_text)

            import re
            jid = None
            m = re.search(r'"(?:job_id|job_set_id|generation_id|id)"\s*:\s*"([^"]+)"', submit_text)
            if m:
                jid = m.group(1)
            print("\n>>> extracted job id:", jid)

            if jid:
                print("\n=== POLLING job_status (every 8s) ===")
                for i in range(40):
                    r = await session.call_tool("job_status", {"jobId": jid, "sync": True})
                    t = text_of(r)
                    print(f"\n--- poll {i+1} ---\n{t[:900]}")
                    if re.search(r'"status"\s*:\s*"(completed|succeeded|done|failed|error|nsfw)"', t, re.I):
                        print("\n>>> TERMINAL STATUS REACHED. Full response above.")
                        break
                    await asyncio.sleep(3)

    print("\nSend me everything above — especially the SUBMIT response and the final poll.")


if __name__ == "__main__":
    asyncio.run(main())