"""Diagnose whether Higgsfield gives your account a Seedance REFERENCE-mode
variant (the one that actually accepts host + guest as separate inputs) and
which role names its generate_video schema allows.

Run from backend (venv active):

    python diagnose_seedance_refmode.py

Prints:
  * every seedance-* model id the catalog exposes on your plan
  * the generate_video input schema, with the medias[].role enum highlighted
  * a suggestion for which model / role names the pipeline will use for a
    2-image (host + guest) video call

No credits are spent. This is just a schema + catalog probe."""
import asyncio
import json
import re

from mcp import types as mcp_types


def _text(res):
    parts = []
    if res.structuredContent:
        parts.append(json.dumps(res.structuredContent))
    for c in res.content or []:
        if isinstance(c, mcp_types.TextContent):
            parts.append(c.text)
    return "\n".join(parts)


async def main():
    from app.config import get_settings
    from app.services.higgsfield_mcp import _oauth_provider, HiggsfieldMCP
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    s = get_settings()
    mcp = HiggsfieldMCP()

    async with streamablehttp_client(s.hf_mcp_url, auth=_oauth_provider(),
                                     timeout=120) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            mcp._schemas = {t.name: (t.inputSchema or {}) for t in tools.tools}

            # 1. Full generate_video schema (roles enum + everything else)
            print("\n=== generate_video input schema ===")
            gv = mcp._schemas.get("generate_video")
            if not gv:
                print("  generate_video is NOT exposed on this account — cannot proceed.")
                return
            print(json.dumps(gv, indent=2)[:3500])

            # 2. Detected role vocabulary from the schema (what the code uses)
            roles = mcp._schema_media_roles("generate_video")
            print("\n=== detected medias[].role values (from schema) ===")
            print(" ", sorted(roles) if roles else "(none detected — the schema doesn't expose enums)")

            # 3. Seedance variants in the catalog
            print("\n=== Seedance variants in your model catalog ===")
            cat = ""
            try:
                cat = await mcp._fetch_catalog(session, "seedance")
            except Exception as e:  # noqa: BLE001
                print(f"  models_explore failed: {e}")
                return
            seedance_ids = sorted(set(
                m.group(1) for m in re.finditer(r'"([A-Za-z0-9._\-/]{3,80})"', cat)
                if "seedance" in m.group(1).lower()
            ))
            for mid in seedance_ids:
                marker = ""
                low = mid.lower()
                if "reference" in low or "_ref" in low or "-ref" in low:
                    marker = "   <-- REFERENCE MODE (multi-image)"
                print(f"  {mid}{marker}")
            if not seedance_ids:
                print("  (no seedance ids found — check your Higgsfield plan)")

            # 4. What the pipeline will pick for a 2-image video call
            print("\n=== what the pipeline will send for host + guest (2 images) ===")
            try:
                chosen = await mcp._resolve_model(session, "seedance", "video",
                                                  prefer_reference=True)
                print(f"  model         = {chosen}")
                is_ref = ("reference" in chosen.lower() or "_ref" in chosen.lower()
                          or "-ref" in chosen.lower())
                if is_ref:
                    sec = "reference_image" if (not roles or "reference_image" in roles) else \
                          ("reference" if "reference" in roles else "image_reference")
                    print(f"  primary role  = start_image (host / speaker)")
                    print(f"  secondary role= {sec} (guest / listener)")
                    print("  RESULT: both images should reach Higgsfield in reference mode. ✅")
                else:
                    print("  primary role  = start_image (host / speaker)")
                    print(f"  secondary role= {'reference_image' if not roles or 'reference_image' in roles else 'image'} "
                          f"(guest / listener) — but this model is i2v")
                    print("  RESULT: Seedance i2v only reads start_image; the guest image will")
                    print("          likely be dropped by the server. Enable a Seedance reference")
                    print("          variant on your Higgsfield plan for multi-image support. ⚠️")
            except Exception as e:  # noqa: BLE001
                print(f"  resolve failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())