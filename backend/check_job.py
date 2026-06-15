"""Check a single Higgsfield job's status and print the output URL when ready.
Usage: python check_job.py <jobId>"""
import asyncio, json, sys
from mcp import types as mcp_types

def text_of(res):
    p=[]
    if res.structuredContent: p.append(json.dumps(res.structuredContent, indent=2))
    for c in res.content or []:
        if isinstance(c, mcp_types.TextContent): p.append(c.text)
    return "\n".join(p)

async def main():
    jid = sys.argv[1]
    from app.config import get_settings
    from app.services.higgsfield_mcp import _oauth_provider
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    s = get_settings()
    async with streamablehttp_client(s.hf_mcp_url, auth=_oauth_provider(), timeout=180) as (r,w,_):
        async with ClientSession(r,w) as sess:
            await sess.initialize()
            for i in range(30):
                res = await sess.call_tool("job_status", {"jobId": jid, "sync": True})
                t = text_of(res)
                import re
                st = re.search(r'"status"\s*:\s*"([^"]+)"', t)
                status = st.group(1) if st else "?"
                print(f"poll {i+1}: status={status}")
                if status in ("completed","succeeded","success","done","ready","finished"):
                    print("\n=== FULL COMPLETED RESPONSE ===")
                    print(t)
                    # find the output mp4
                    for m in re.finditer(r'https?://[^\s"\'<>\\]+\.mp4[^\s"\'<>\\]*', t):
                        if "d2ol7oe51mr4n9" not in m.group(0) and "preset" not in m.group(0):
                            print("\n>>> OUTPUT VIDEO URL:", m.group(0))
                    break
                if status in ("failed","error","nsfw","canceled","cancelled"):
                    print("\nFAILED:", t[:600]); break
                await asyncio.sleep(10)

asyncio.run(main())