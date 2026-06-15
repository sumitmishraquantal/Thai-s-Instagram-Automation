# """Inspect Seedance 2.0 model details + preflight cost via Higgsfield MCP.
# Zero cost - uses models_explore + generate_video(get_cost=True).

# Run from backend/ with venv active:
#     python check_seedance.py
# """
# import asyncio
# import json

# from app.config import get_settings
# from app.services.higgsfield_mcp import _oauth_provider
# from mcp.client.session import ClientSession
# from mcp.client.streamable_http import streamablehttp_client


# async def main():
#     s = get_settings()
#     async with streamablehttp_client(s.hf_mcp_url, auth=_oauth_provider(), timeout=120) as (read, write, _):
#         async with ClientSession(read, write) as session:
#             await session.initialize()

#             print("=== seedance_2_0 model details (models_explore) ===")
#             for args in (
#                 {"action": "search", "query": "seedance_2_0"},
#                 {"action": "get", "model": "seedance_2_0"},
#                 {"action": "search", "query": "seedance 2.0"},
#             ):
#                 try:
#                     res = await session.call_tool("models_explore", args)
#                     if not res.isError:
#                         for c in res.content or []:
#                             print(getattr(c, "text", ""))
#                         if res.structuredContent:
#                             print(json.dumps(res.structuredContent, indent=2))
#                         print(f"--- (used args: {args}) ---\n")
#                         break
#                     else:
#                         print(f"attempt {args} -> isError, trying next")
#                 except Exception as e:  # noqa: BLE001
#                     print(f"attempt {args} failed: {e}")

#             print("\n=== generate_video preflight (get_cost=True, zero spend) ===")
#             try:
#                 res = await session.call_tool("generate_video", {
#                     "params": {
#                         "model": "seedance_2_0",
#                         "prompt": "test prompt, static shot",
#                         "duration": 10,
#                         "aspect_ratio": "9:16",
#                         "get_cost": True,
#                     }
#                 })
#                 for c in res.content or []:
#                     print(getattr(c, "text", ""))
#                 if res.structuredContent:
#                     print(json.dumps(res.structuredContent, indent=2))
#                 if res.isError:
#                     print("(call returned isError=True - see message above)")
#             except Exception as e:  # noqa: BLE001
#                 print("preflight failed:", e)


# if __name__ == "__main__":
#     asyncio.run(main())

"""Check GPT Image 2 model details + current credit balance via Higgsfield MCP.
Zero cost.

Run from backend/ with venv active:
    python check_gptimage2.py
"""
import asyncio
import json

from app.config import get_settings
from app.services.higgsfield_mcp import _oauth_provider
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    s = get_settings()
    async with streamablehttp_client(s.hf_mcp_url, auth=_oauth_provider(), timeout=120) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("=== gpt_image_2 model details (models_explore) ===")
            for args in (
                {"action": "search", "query": "gpt_image_2"},
                {"action": "get", "model": "gpt_image_2"},
                {"action": "search", "query": "gpt image 2"},
            ):
                try:
                    res = await session.call_tool("models_explore", args)
                    if not res.isError:
                        for c in res.content or []:
                            print(getattr(c, "text", ""))
                        if res.structuredContent:
                            print(json.dumps(res.structuredContent, indent=2))
                        print(f"--- (used args: {args}) ---\n")
                        break
                    else:
                        print(f"attempt {args} -> isError, trying next")
                except Exception as e:  # noqa: BLE001
                    print(f"attempt {args} failed: {e}")

            print("\n=== generate_image preflight for gpt_image_2 (get_cost=True, zero spend) ===")
            try:
                res = await session.call_tool("generate_image", {
                    "params": {
                        "model": "gpt_image_2",
                        "prompt": "test prompt",
                        "get_cost": True,
                    }
                })
                for c in res.content or []:
                    print(getattr(c, "text", ""))
                if res.structuredContent:
                    print(json.dumps(res.structuredContent, indent=2))
                if res.isError:
                    print("(call returned isError=True - see message above)")
            except Exception as e:  # noqa: BLE001
                print("preflight failed:", e)

            print("\n=== current balance ===")
            try:
                res = await session.call_tool("balance", {})
                for c in res.content or []:
                    print(getattr(c, "text", ""))
                if res.structuredContent:
                    print(json.dumps(res.structuredContent, indent=2))
            except Exception as e:  # noqa: BLE001
                print("balance check failed:", e)


if __name__ == "__main__":
    asyncio.run(main())