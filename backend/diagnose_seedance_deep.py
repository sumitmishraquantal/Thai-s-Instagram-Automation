"""Deep-probe Higgsfield MCP for any multi-image video path on your plan.

This is the follow-up to diagnose_seedance_refmode.py. That first script proved
your account exposes only seedance1_5 / seedance_2_0 / seedance_2_0_mini with
no *_reference variant. But the generate_video schema explicitly said the
role vocabulary is per-model, and the catalog `search` dump got truncated
before we could see each model's medias/parameters spec. So this script:

  1. Lists EVERY MCP tool the server exposes (not just generate_video). If
     there's a characters_create / soul_reference / identity endpoint that
     supports multi-person input, it will show up here.

  2. Calls models_explore action=get for each seedance variant on your plan
     so we see the FULL, un-truncated model entry — its medias.roles list,
     its parameters list, and any hint of a reference / image_2 input.

  3. Reports a plain-English verdict: which model+role combination (if any)
     can carry both host and guest into ONE generate_video call.

No credits are spent. Run from backend (venv active):

    python diagnose_seedance_deep.py
"""
import asyncio
import json
import re

from mcp import types as mcp_types


def _text(res) -> str:
    parts: list[str] = []
    if res.structuredContent:
        parts.append(json.dumps(res.structuredContent))
    for c in res.content or []:
        if isinstance(c, mcp_types.TextContent):
            parts.append(c.text)
    return "\n".join(parts)


def _find_json_object(text: str, id_value: str) -> dict | None:
    """Locate the JSON object in `text` whose "id" field equals id_value.
    Does a bracket-balanced scan around each occurrence so we get the WHOLE
    object even if the outer response has been truncated elsewhere."""
    for m in re.finditer(rf'"id"\s*:\s*"{re.escape(id_value)}"', text):
        # Walk backward to the nearest '{' that opens the enclosing object.
        i = m.start()
        depth = 0
        start = None
        # Scan back to find the opening brace of the object containing this id
        for j in range(i, -1, -1):
            if text[j] == '}':
                depth += 1
            elif text[j] == '{':
                if depth == 0:
                    start = j
                    break
                depth -= 1
        if start is None:
            continue
        # Now walk forward to find its matching close brace
        depth = 0
        for k in range(start, len(text)):
            if text[k] == '{':
                depth += 1
            elif text[k] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:k + 1])
                    except Exception:  # noqa: BLE001
                        break
    return None


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

            # 1. EVERY MCP tool on this account
            print("\n=== ALL MCP tools exposed on your account ===")
            for t in sorted(tools.tools, key=lambda x: x.name):
                desc = (t.description or "").strip().splitlines()[0][:80]
                print(f"  - {t.name:<32}  {desc}")
            # Flag anything that hints at multi-identity / character features
            candidates = [t.name for t in tools.tools if any(
                w in t.name.lower() for w in
                ("character", "identity", "soul", "reference", "person", "avatar"))]
            if candidates:
                print(f"\n  ⭐ potentially multi-identity tools to investigate: {candidates}")
            else:
                print("\n  (no character/identity/soul tools on this MCP endpoint)")

            # 2. Full per-model detail for every Seedance variant
            print("\n=== per-model detail for each Seedance variant ===")
            try:
                catalog_search = await mcp._fetch_catalog(session, "seedance")
            except Exception as e:  # noqa: BLE001
                print(f"  models_explore(search) failed: {e}")
                return
            seedance_ids = sorted(set(
                m.group(1) for m in re.finditer(r'"id"\s*:\s*"([^"]+)"', catalog_search)
                if "seedance" in m.group(1).lower()
            ))
            print(f"  variants: {seedance_ids}\n")

            for mid in seedance_ids:
                print(f"\n  ── {mid} " + "─" * (60 - len(mid)))
                entry = None

                # Try action=get first (proper per-model detail endpoint)
                for args in ({"action": "get", "id": mid},
                             {"action": "get", "model": mid},
                             {"action": "get", "query": mid}):
                    try:
                        got_text = await mcp._call(session, "models_explore", args, raw=True)
                        cand = _find_json_object(got_text, mid)
                        if cand:
                            entry = cand
                            break
                    except Exception:  # noqa: BLE001
                        continue

                # Fallback: extract from the search dump we already have
                if entry is None:
                    entry = _find_json_object(catalog_search, mid)

                if entry is None:
                    print("    (could not locate full entry — the response was likely truncated)")
                    continue

                # Show the shape we care about: params, medias/roles, anything image-ish
                params = entry.get("parameters") or entry.get("params") or []
                medias = (entry.get("medias") or entry.get("media")
                          or entry.get("inputs") or {})
                print(f"    output_type   : {entry.get('output_type')}")
                print(f"    description   : {entry.get('description')}")

                # Parameter names — where a 'reference_image' / 'image_2' would live
                pnames = []
                for p in params if isinstance(params, list) else []:
                    if isinstance(p, dict):
                        pnames.append(p.get("name") or p.get("key") or str(p)[:40])
                    else:
                        pnames.append(str(p)[:40])
                print(f"    parameters    : {pnames}")

                # Medias / roles — this is the definitive answer for multi-image
                if isinstance(medias, dict):
                    roles = medias.get("roles") or medias.get("role") or []
                    max_n = medias.get("max") or medias.get("count") or medias.get("limit")
                    print(f"    medias.roles  : {roles}")
                    print(f"    medias.max    : {max_n}")
                elif isinstance(medias, list):
                    print(f"    medias        : {medias}")
                else:
                    print(f"    medias        : (not present in entry)")

                # Also grep the raw entry JSON for anything that smells like
                # a secondary image slot, in case the field lives under a
                # different key we didn't anticipate.
                raw = json.dumps(entry)
                hits = sorted(set(re.findall(
                    r'"([a-z_]*(?:reference|ref_image|image_2|second_image|character)[a-z_]*)"',
                    raw, flags=re.I,
                )))
                if hits:
                    print(f"    ref-shaped keys anywhere in entry : {hits}")

                # Print a trimmed but useful chunk of the raw entry for eyeballing
                print("    ── raw entry (first 1200 chars) ──")
                pretty = json.dumps(entry, indent=2)
                for line in pretty.splitlines()[:40]:
                    print(f"    {line}")
                if len(pretty) > 1200:
                    print("    …(truncated for readability)")

            # 3. Plain-English verdict
            print("\n=== VERDICT ===")
            verdict_ref_variant = any(
                "reference" in mid.lower() or "_ref" in mid.lower() or "-ref" in mid.lower()
                for mid in seedance_ids
            )
            if verdict_ref_variant:
                print("  ✅ You have a Seedance reference-mode variant. The pipeline")
                print("     should already pick it (see diagnose_seedance_refmode.py).")
            else:
                print("  ⚠  No *_reference variant is exposed on your Higgsfield plan.")
                print("     Look at each model's `medias.roles` above:")
                print("       • if any variant lists more than one image role")
                print("         (e.g. start_image AND reference_image), we CAN send")
                print("         both host and guest — I just need the role name")
                print("         above to wire in.")
                print("       • if every variant lists only start_image + audio, then")
                print("         Higgsfield's answer is 'one image per clip' on this")
                print("         plan and the fix is either (a) an account upgrade, or")
                print("         (b) a per-scene composite (host+guest side-by-side)")
                print("         used as the single start_image.")
                if candidates:
                    print(f"     Also check the multi-identity-looking tools: {candidates}")


if __name__ == "__main__":
    asyncio.run(main())