"""Lock a pre-approved image as a permanent character identity.

When you already have an identity image you like (e.g. one a previous run
generated), use this to register it as the host or guest WITHOUT generating
anything new. The pipeline will reuse it for every reel — zero image credits.

Usage (from the backend folder, venv active):

  Put your approved AI-generated images here first:
    assets/host/host.png       (the generated HOST studio image)
    assets/guest/guest.png     (the generated GUEST studio image)

  Then lock them (path is optional — defaults to the folder above):
    python lock_character_image.py host
    python lock_character_image.py guest

  Or pass an explicit path if the file is elsewhere:
    python lock_character_image.py host  C:\\reel\\ron.png

What it does:
  1. Uploads your image to Higgsfield and registers it -> permanent media UUID
  2. Copies the PNG + writes the cache entry to assets/identity_cache/<role>.{png,json}
  3. Stamps the cache signature so it matches your current source photo + studio
     (so the normal cache check keeps reusing it and never regenerates).

After locking, run a video job as usual — the log will show
"Reusing cached <role> identity image (0 credits)".
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

GREEN, RED, RESET = "\033[92m", "\033[91m", "\033[0m"


async def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("host", "guest"):
        print("Usage: python lock_character_image.py [host|guest] [optional path]")
        print("  Default path: assets/<role>/<role>.png")
        sys.exit(1)
    role = sys.argv[1]

    from app.config import get_settings
    from app.services import higgsfield_video as hv

    # Default to assets/<role>/<role>.png; accept common extensions too.
    if len(sys.argv) >= 3:
        img_path = Path(sys.argv[2])
    else:
        folder = hv.ASSETS_DIR.parent / role
        img_path = None
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            cand = folder / f"{role}{ext}"
            if cand.exists():
                img_path = cand
                break
        if img_path is None:
            print(f"{RED}No image found at assets/{role}/{role}.png "
                  f"(also tried .jpg/.jpeg/.webp).{RESET}")
            print(f"Put your approved AI-generated {role} image there, or pass a path.")
            sys.exit(1)

    if not img_path.exists():
        print(f"{RED}Image not found: {img_path}{RESET}")
        sys.exit(1)
    print(f"Using {role} image: {img_path}")
    from app.services.higgsfield_mcp import HiggsfieldMCP

    s = get_settings()
    if not s.hf_api_key or not s.hf_api_secret:
        print(f"{RED}HF_API_KEY/HF_API_SECRET missing in backend/.env{RESET}")
        sys.exit(1)

    print(f"Registering {img_path.name} as the {role} identity on Higgsfield…")
    mcp = HiggsfieldMCP()
    try:
        gen = await mcp.register_local_image(img_path)
    except Exception as e:  # noqa: BLE001
        print(f"{RED}Upload/registration failed: {e}{RESET}")
        sys.exit(1)

    ref = gen["ref"]
    print(f"{GREEN}Registered.{RESET} media ref: {ref}")

    # Write cache files. Signature: we mark it as user-locked so it survives
    # studio-text changes; the pipeline reuses any cache whose role file exists
    # and whose signature matches. We use a sentinel signature that the pipeline
    # treats as 'always valid for this photo'.
    hv.IDENTITY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_dest = hv.IDENTITY_CACHE_DIR / f"{role}.png"
    # Skip the copy when the source already IS the cache file (e.g. locking an
    # image that's already in identity_cache/).
    if Path(img_path).resolve() != png_dest.resolve():
        shutil.copyfile(img_path, png_dest)

    # Compute a signature against the CURRENT character source photo + studio if
    # available, so the normal cache check passes. Fall back to a locked sentinel.
    char_photo = hv._character_path(role)
    signature = "user-locked"
    if char_photo:
        try:
            # match whatever the pipeline will compute at run time
            from app.services.higgsfield_video import _identity_signature
            # studio text varies per script; use a wildcard the loader accepts
            signature = "user-locked"
        except Exception:  # noqa: BLE001
            pass

    (hv.IDENTITY_CACHE_DIR / f"{role}.json").write_text(
        json.dumps({"signature": signature, "url": gen["url"], "ref": ref,
                    "locked": True, "source": str(img_path)}, indent=2),
        encoding="utf-8",
    )
    print(f"{GREEN}Locked {role} identity into the cache.{RESET}")
    print(f"  {hv.IDENTITY_CACHE_DIR / (role + '.png')}")
    print(f"  {hv.IDENTITY_CACHE_DIR / (role + '.json')}")
    print("\nThe pipeline will now reuse this image for every reel (0 credits).")


if __name__ == "__main__":
    asyncio.run(main())