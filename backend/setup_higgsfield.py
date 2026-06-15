"""Higgsfield setup verification — run BEFORE any video job.

From the backend folder (venv active):
    python setup_higgsfield.py            # all checks, zero generation cost
    python setup_higgsfield.py --smoke    # + one tiny paid image generation (~1 credit)

Checks, in order:
  1. Python packages installed at compatible versions
  2. .env resolved settings (catches stale model IDs overriding code defaults)
  3. ffmpeg on PATH
  4. Character photos present (host/guest)
  5. Platform API auth (file upload round-trip — free)
  6. Platform model IDs exist (empty-body probe — free: validation error = exists)
  7. MCP server connection + OAuth + tool list + Seedance 2.0 availability
Every check prints [PASS] / [FAIL] / [WARN] with the exact fix.
"""
import argparse
import asyncio
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"
FAILURES = []


def p(status: str, name: str, detail: str = ""):
    color = {"PASS": GREEN, "FAIL": RED, "WARN": YELLOW}[status]
    print(f"  {color}[{status}]{RESET} {name}" + (f" — {detail}" if detail else ""))
    if status == "FAIL":
        FAILURES.append(name)


def header(title: str):
    print(f"\n{'─' * 60}\n  {title}\n{'─' * 60}")


# Tiny valid 1x1 PNG for upload tests
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f0300050001a5f6f0bd0000000049454e44ae426082"
)


def check_packages():
    header("1 · Python packages")
    ok = True
    for pkg, mod in [("fastapi", "fastapi"), ("higgsfield-client", "higgsfield_client"),
                     ("mcp", "mcp"), ("pydub", "pydub"), ("httpx", "httpx"),
                     ("python-multipart", "multipart")]:
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "?")
            p("PASS", f"{pkg}", f"v{ver}")
        except ImportError:
            p("FAIL", f"{pkg}", f"run: pip install {pkg}")
            ok = False
    # fastapi/starlette compatibility (mcp upgrades starlette)
    try:
        from fastapi import FastAPI
        FastAPI()
        p("PASS", "fastapi + starlette compatible")
    except Exception as e:  # noqa: BLE001
        p("FAIL", "fastapi + starlette", f"run: pip install -U fastapi  ({e})")
        ok = False
    return ok


def check_settings():
    header("2 · Resolved settings (.env + defaults)")
    try:
        from app.config import get_settings
        get_settings.cache_clear()
        s = get_settings()
    except Exception as e:  # noqa: BLE001
        p("FAIL", "load app.config", str(e))
        return None

    expected = {
        "hf_image_model": "higgsfield-ai/soul/standard",
        "hf_image_ref_model": "higgsfield-ai/soul/reference",
        "hf_video_model": "bytedance/seedance/v1/lite/image-to-video",
    }
    stale_markers = ("seedream/v4", "seedance/v2", "seedream")
    for attr, exp in expected.items():
        val = getattr(s, attr, None)
        if val is None:
            p("FAIL", attr, "missing — config.py is the old version; replace it")
        elif any(m in val for m in stale_markers):
            p("FAIL", attr, f"STALE value '{val}' — your .env still has an old HF_* line; "
                            f"delete it or set it to '{exp}', then FULLY restart uvicorn")
        else:
            p("PASS", attr, val)

    vp = getattr(s, "video_provider", "missing")
    if vp == "missing":
        p("FAIL", "video_provider", "config.py missing the provider switch — replace config.py")
    else:
        p("PASS", "video_provider", f"{vp} ({'Seedance 2.0 via MCP' if vp == 'hf_mcp' else 'Seedance v1 Lite via REST'})")

    if s.hf_api_key and s.hf_api_secret:
        p("PASS", "HF_API_KEY / HF_API_SECRET", f"key id …{s.hf_api_key[-4:]}")
    else:
        p("FAIL", "HF_API_KEY / HF_API_SECRET", "missing in backend/.env")
    return s


def check_ffmpeg():
    header("3 · ffmpeg")
    path = shutil.which("ffmpeg")
    if not path:
        p("FAIL", "ffmpeg on PATH",
          "install ffmpeg and ensure 'ffmpeg -version' works in THIS terminal")
        return
    r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
    p("PASS", "ffmpeg", r.stdout.splitlines()[0][:60])


def check_characters():
    header("4 · Character photos (identity anchors)")
    assets = Path(__file__).parent / "assets" / "characters"
    found = {}
    exts = (".png", ".jpg", ".jpeg", ".webp")
    for role in ("host", "guest"):
        for f in sorted(assets.glob(f"{role}.*")):
            if f.suffix.lower() in exts:
                found[role] = f
                break
    for role in ("host", "guest"):
        if role in found:
            kb = found[role].stat().st_size // 1024
            if kb < 5:
                p("WARN", f"{role} photo", f"{found[role].name} is only {kb}KB — is this a real photo?")
            else:
                p("PASS", f"{role} photo", f"{found[role].name} ({kb}KB)")
        else:
            p("FAIL", f"{role} photo",
              f"missing — upload via the UI panel, or copy your photo to {assets / (role + '.png')}")


async def check_platform_auth(s):
    header("5 · Platform API auth (free upload round-trip)")
    import os
    os.environ["HF_API_KEY"] = s.hf_api_key
    os.environ["HF_API_SECRET"] = s.hf_api_secret
    try:
        import higgsfield_client
        url = await higgsfield_client.upload_async(TINY_PNG, "image/png")
        if isinstance(url, str) and url.startswith("http"):
            p("PASS", "credentials valid + upload works", url[:60] + "…")
            return True
        p("FAIL", "upload", f"unexpected response: {str(url)[:120]}")
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "401" in msg or "403" in msg or "credential" in msg.lower():
            p("FAIL", "credentials", "rejected — regenerate Key ID + Secret at cloud.higgsfield.ai "
                                     "and update backend/.env")
        else:
            p("FAIL", "upload", msg[:200])
    return False


async def probe_model(endpoint: str) -> tuple[bool, str]:
    """Empty-body probe: 'Model not found' = bad ID; validation error = model EXISTS.
    Zero generation cost either way."""
    import higgsfield_client
    try:
        await higgsfield_client.submit_async(endpoint, arguments={})
        return True, "accepted (unexpected but exists)"
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "model not found" in msg.lower() or "404" in msg:
            return False, "Model not found — wrong ID for your account"
        return True, f"exists (validation reply: {msg[:80]})"


VIDEO_MODEL_CANDIDATES = [
    "bytedance/seedance/v1/lite/image-to-video",
    "bytedance/seedance/v1/pro/fast/image-to-video",
    "bytedance/seedance/v1/pro/image-to-video",
    "kling-video/v2.5-turbo/standard/image-to-video",
    "kling-video/v2.5-turbo/pro/image-to-video",
    "kling-video/v2.1/standard/image-to-video",
    "kling-video/v2.1/pro/image-to-video",
    "minimax/hailuo-2.3/standard/image-to-video",
    "minimax/hailuo-2.3-fast/standard/image-to-video",
    "minimax/hailuo-02/standard/image-to-video",
    "veo3.1/fast/image-to-video",
    "veo3.1/image-to-video",
    "sora-2/image-to-video",
    "wan-25-preview/image-to-video",
]


async def check_platform_models(s):
    header("6 · Platform model IDs (free probes)")
    for label, ep in [("image (soul/standard)", s.hf_image_model),
                      ("image ref (soul/reference)", s.hf_image_ref_model),
                      ("video (configured)", s.hf_video_model)]:
        ok, detail = await probe_model(ep)
        p("PASS" if ok else "FAIL", f"{label}: {ep}", detail)

    header("6b · Video model DISCOVERY — which i2v models YOUR account has (free)")
    available = []
    for ep in VIDEO_MODEL_CANDIDATES:
        if ep == s.hf_video_model:
            continue
        ok, _ = await probe_model(ep)
        mark = f"{GREEN}AVAILABLE{RESET}" if ok else f"{RED}not found{RESET}"
        print(f"    {mark}  {ep}")
        if ok:
            available.append(ep)
    if available:
        print(f"{YELLOW}→ Set HF_VIDEO_MODEL in backend\.env to one of the AVAILABLE IDs "
              f"above (top of the list = best fit for this pipeline), then restart uvicorn.{RESET}")
    else:
        print(f"{YELLOW}→ No image-to-video models found on your plan via REST. "
              f"Use VIDEO_PROVIDER=hf_mcp (Seedance 2.0 via MCP) instead.{RESET}")


# Every documented platform video endpoint (June 2026 catalog) — probes are free
VIDEO_MODEL_CANDIDATES = [
    "bytedance/seedance/v1/lite/image-to-video",
    "bytedance/seedance/v1/pro/fast/image-to-video",
    "bytedance/seedance/v1/lite/text-to-video",
    "bytedance/seedance/v1/pro/fast/text-to-video",
    "higgsfield-ai/dop/standard",
    "higgsfield-ai/dop/turbo",
    "higgsfield-ai/dop/lite",
    "kling-video/v2.5-turbo/pro/image-to-video",
    "kling-video/v2.5-turbo/standard/image-to-video",
    "kling-video/v2.1/master/image-to-video",
    "kling-video/v2.1/pro/image-to-video",
    "kling-video/v2.1/standard/image-to-video",
    "minimax/hailuo-2.3/standard/image-to-video",
    "minimax/hailuo-2.3/pro/image-to-video",
    "minimax/hailuo-2.3-fast/standard/image-to-video",
    "minimax/hailuo-02/standard/image-to-video",
    "sora-2/image-to-video",
    "sora-2/image-to-video/pro",
    "veo3.1/image-to-video",
    "veo3.1/fast/image-to-video",
    "wan-25-preview/image-to-video",
]


async def probe_video_catalog():
    header("VIDEO MODEL DISCOVERY (free probes of the full catalog)")
    available = []
    for ep in VIDEO_MODEL_CANDIDATES:
        ok, detail = await probe_model(ep)
        p("PASS" if ok else "WARN", ep, "AVAILABLE" if ok else "not on your account")
        if ok:
            available.append(ep)
    print(f"\n  Available video models on your account: {len(available)}")
    for ep in available:
        print(f"    → {ep}")
    if available:
        best = next((e for e in available if "image-to-video" in e), available[0])
        print(f"\n  Recommended fallback for .env:  HF_VIDEO_MODEL={best}")
    else:
        print(f"\n  {YELLOW}No platform video models available — your plan may gate API video access."
              f" The MCP route (VIDEO_PROVIDER=hf_mcp) is then your only path.{RESET}")
    return available


async def check_mcp(s):
    header("7 · Higgsfield MCP (Seedance 2.0)")
    if getattr(s, "video_provider", "") != "hf_mcp":
        p("WARN", "skipped", "VIDEO_PROVIDER is not 'hf_mcp' — set it in .env to use Seedance 2.0")
        return
    try:
        from app.services.higgsfield_mcp import _oauth_provider
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except Exception as e:  # noqa: BLE001
        p("FAIL", "import MCP client", f"{e} — is higgsfield_mcp.py in app/services/?")
        return

    tokens = Path(__file__).parent / "assets" / "hf_mcp_tokens.json"
    if not tokens.exists():
        print(f"  {YELLOW}First-time OAuth: a browser window/URL will appear — "
              f"sign in with your Higgsfield account.{RESET}")
    try:
        async with streamablehttp_client(s.hf_mcp_url, auth=_oauth_provider(),
                                         timeout=120) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = [t.name for t in tools.tools]
                p("PASS", "connected + authenticated", f"tools: {names}")

                # Query the model catalog for Seedance entries (the schema doesn't list models)
                try:
                    res = None
                    for args in ({"action": "search", "query": "seedance"},
                                 {"action": "search", "q": "seedance"},
                                 {"action": "list"}):
                        try:
                            res = await session.call_tool("models_explore", args)
                            if not res.isError:
                                break
                        except Exception:
                            continue
                    cat = ""
                    for c in res.content or []:
                        cat += getattr(c, "text", "")
                    if res.structuredContent:
                        cat += json.dumps(res.structuredContent)
                    hits = sorted(set(
                        m.group(1) for m in
                        __import__("re").finditer(r'"([^"]*[sS]eedance[^"]*)"', cat)
                    ))
                    if hits:
                        p("PASS", "Seedance in model catalog", str(hits[:8]))
                        # print the seedance entry with its media roles
                        import re as _re
                        for mm in _re.finditer(r"\{[^{}]*[sS]eedance[^{}]*\}", cat):
                            print("  seedance entry:", mm.group(0)[:600])
                            break
                    else:
                        p("WARN", "Seedance not in catalog",
                          "your plan may not include it — catalog excerpt below")
                        print("  catalog excerpt:", cat[:800])
                except Exception as e:
                    p("WARN", "models_explore", str(e)[:200])

                gen_video = next((t for t in tools.tools if t.name == "generate_video"), None)
                if gen_video:
                    schema = json.dumps(gen_video.inputSchema or {})
                    if "seedance" in schema.lower():
                        p("PASS", "Seedance available on your plan",
                          "found in generate_video schema")
                    else:
                        p("WARN", "Seedance not visible in schema",
                          "your plan may not include Seedance 2.0 — check higgsfield.ai; "
                          "model field will be sent as 'seedance' and validated server-side")
                    print(f"\n  generate_video input schema (save this if anything fails):\n"
                          f"  {schema[:1500]}")
                else:
                    p("FAIL", "generate_video tool", f"not found among {names}")
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "401" in msg or "unauthorized" in msg.lower():
            p("FAIL", "MCP auth", f"delete {tokens} and re-run to redo OAuth")
        else:
            p("FAIL", "MCP connection", msg[:300])


async def smoke_test(s):
    header("8 · SMOKE TEST (paid: ~1 image credit)")
    import higgsfield_client
    try:
        result = await higgsfield_client.subscribe_async(s.hf_image_model, arguments={
            "prompt": "A single red apple on a wooden table, studio lighting",
            "num_images": 1, "resolution": "2K", "aspect_ratio": "9:16",
        })
        text = json.dumps(result)[:300]
        p("PASS", "end-to-end image generation", text)
    except Exception as e:  # noqa: BLE001
        p("FAIL", "image generation", str(e)[:300])


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="also run one tiny paid generation")
    args = ap.parse_args()

    print("\nHIGGSFIELD SETUP VERIFICATION")
    if not check_packages():
        print(f"\n{RED}Fix package issues first, then re-run.{RESET}")
        sys.exit(1)
    s = check_settings()
    check_ffmpeg()
    check_characters()
    if s and s.hf_api_key and s.hf_api_secret:
        if await check_platform_auth(s):
            await check_platform_models(s)
        await check_mcp(s)
        if args.smoke and not FAILURES:
            await smoke_test(s)

    print(f"\n{'═' * 60}")
    if FAILURES:
        print(f"{RED}  {len(FAILURES)} check(s) FAILED:{RESET} " + ", ".join(FAILURES))
        print("  Fix the items above, FULLY restart uvicorn (Ctrl+C, start again), re-run this script.")
        sys.exit(1)
    print(f"{GREEN}  ALL CHECKS PASSED — the pipeline is safe to run.{RESET}")
    print("  Optional: python setup_higgsfield.py --smoke  (one paid image to prove generation)")


if __name__ == "__main__":
    asyncio.run(main())