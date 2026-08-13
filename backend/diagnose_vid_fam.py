"""Inspect a video-model FAMILY on your Higgsfield MCP account and decide
whether the pipeline can use it right now.

Usage:

    python diagnose_video_family.py            # defaults to 'seedance'
    python diagnose_video_family.py kling
    python diagnose_video_family.py sora        # any family name in your catalog

For the given family, this script:

  1. Finds every model id in your catalog that matches the family name.
  2. Fetches each model's full detail (parameters + medias.roles) so we
     know exactly which role names it accepts for images and audio.
  3. Runs the same picker the pipeline uses, and reports the model id and
     roles the pipeline WILL send when you set
     `hf_mcp_video_model_hint=<family>` in backend/.env.

No credits are spent — this is a pure catalog probe.
"""
import asyncio
import json
import re
import sys

from mcp import types as mcp_types


def _text(res) -> str:
    parts: list[str] = []
    if res.structuredContent:
        parts.append(json.dumps(res.structuredContent))
    for c in res.content or []:
        if isinstance(c, mcp_types.TextContent):
            parts.append(c.text)
    return "\n".join(parts)


async def main():
    family = (sys.argv[1] if len(sys.argv) > 1 else "seedance").strip().lower()

    from app.config import get_settings
    from app.services.higgsfield_mcp import _oauth_provider, HiggsfieldMCP
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    s = get_settings()
    mcp = HiggsfieldMCP()

    print(f"\nInspecting video family: '{family}'")
    print("=" * 78)

    async with streamablehttp_client(s.hf_mcp_url, auth=_oauth_provider(),
                                     timeout=120) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            mcp._schemas = {t.name: (t.inputSchema or {}) for t in tools.tools}

            # 1. Family match in the catalog
            try:
                cat = await mcp._fetch_catalog(session, family)
            except Exception as e:  # noqa: BLE001
                print(f"\n  models_explore(search='{family}') failed: {e}")
                return

            norm = family.replace(" ", "").replace("-", "").replace(".", "").replace("_", "")
            matching_ids = sorted(set(
                m.group(1) for m in re.finditer(r'"id"\s*:\s*"([A-Za-z0-9._\-/]{3,80})"', cat)
                if norm in m.group(1).lower().replace(" ", "").replace("-", "")
                              .replace(".", "").replace("_", "")
            ))
            if not matching_ids:
                print(f"\n  ❌ No models matching '{family}' found in your catalog.")
                print(f"     Your plan likely doesn't include this family.")
                print(f"     Catalog excerpt: {cat[:400]}")
                return

            print(f"\n  Models on your plan matching '{family}':")
            for mid in matching_ids:
                print(f"    - {mid}")

            # 2. Per-model detail: parameters, roles, description
            print(f"\n{'=' * 78}\nPer-model detail:\n{'=' * 78}")
            all_roles: dict[str, set] = {}
            for mid in matching_ids:
                print(f"\n  ── {mid} " + "─" * max(3, 60 - len(mid)))
                # Fetch full entry via _model_roles() and per-model get
                roles = await mcp._model_roles(session, mid)
                all_roles[mid] = roles

                # Also grab the raw entry for description + params + duration
                entry = None
                for args in ({"action": "get", "id": mid},
                             {"action": "get", "model": mid}):
                    try:
                        got = await mcp._call(session, "models_explore", args, raw=True)
                        entry = mcp._find_model_entry(got, mid)
                        if entry:
                            break
                    except Exception:  # noqa: BLE001
                        continue
                if entry is None:
                    entry = mcp._find_model_entry(cat, mid) or {}

                print(f"    description : {entry.get('description', '(n/a)')}")
                # duration limits
                params = entry.get("parameters") or entry.get("params") or []
                if isinstance(params, list):
                    for p in params:
                        if isinstance(p, dict) and p.get("name") == "duration":
                            opts = p.get("options") or p.get("range") or p.get("options")
                            print(f"    duration    : min={p.get('min')} max={p.get('max')} "
                                  f"options={opts} default={p.get('default')}")
                        elif isinstance(p, dict) and p.get("name") == "resolution":
                            print(f"    resolution  : options={p.get('options')} "
                                  f"default={p.get('default')}")
                pnames = [p.get("name") for p in params
                          if isinstance(p, dict) and p.get("name")]
                print(f"    parameters  : {pnames}")
                print(f"    media roles : {sorted(roles) if roles else '(none read)'}")

            # 3. What the pipeline will pick — simulate the resolver + picker
            print(f"\n{'=' * 78}\nWhat the pipeline will send:\n{'=' * 78}")
            try:
                chosen = mcp._rank_video_candidates(matching_ids)
                print(f"\n  Resolver would pick    : {chosen}")
                chosen_roles = all_roles.get(chosen, set())

                # Simulate the pick() logic
                def pick(*cands: str) -> str:
                    for c in cands:
                        if not chosen_roles or c in chosen_roles:
                            return c
                    return cands[0]

                primary = pick("start_image", "first_frame", "start_frame", "image")
                secondary = pick(
                    "image_references", "character_references",
                    "identity_references", "reference_images",
                    "element_references", "reference_image",
                    "character_reference", "identity_reference",
                    "reference", "image_reference", "element", "image",
                )
                audio = pick(
                    "audio_references", "audio_reference",
                    "voice_references", "voice_reference", "audio",
                )
                print(f"  Primary image role     : {primary}   (host speaks → speaker's identity)")
                print(f"  Secondary image role   : {secondary}   (guest's identity for reaction)")
                print(f"  Audio role             : {audio}   (ElevenLabs segment for lip-sync)")

                # Sanity checks
                print("\n  Verdicts:")
                if secondary in chosen_roles or not chosen_roles:
                    print(f"    ✅ Multi-image: '{secondary}' IS advertised — both host and")
                    print(f"       guest photos will reach the model per scene.")
                else:
                    print(f"    ⚠  Multi-image: '{secondary}' is NOT advertised by this model.")
                    print(f"       The server may auto-coerce, or the extra image may be dropped.")

                if audio in chosen_roles or not chosen_roles:
                    print(f"    ✅ Audio: '{audio}' IS advertised — your pre-rendered ElevenLabs")
                    print(f"       audio should drive lip-sync (verify with a single-scene test).")
                else:
                    print(f"    ⚠  Audio: '{audio}' is NOT advertised. This model may generate")
                    print(f"       audio natively and ignore uploaded audio, OR only accept it")
                    print(f"       as a voice-cloning reference. VERIFY BEFORE FULL RUN — Kling")
                    print(f"       Omni especially generates its own audio and may not accept")
                    print(f"       an external track for lip-sync.")

                print(f"\n  To use this family in production, set in backend/.env:")
                print(f"    HF_MCP_VIDEO_MODEL_HINT={family}")
                print(f"  Then restart uvicorn (Ctrl+C, not --reload).")
            except Exception as e:  # noqa: BLE001
                print(f"  Resolver simulation failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())