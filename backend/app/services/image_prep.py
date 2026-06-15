"""Normalize raw reference photos (host / guest / both) into the exact format the
pipeline wants, accepting ANY common image format.

Per role it: finds <role>.* in any format, applies EXIF orientation, flattens
transparency onto white, converts to RGB, and writes a clean <role>.png; any other
files for that role are archived into _originals/ so exactly one <role>.png remains.

Used in two places:
  - automatically at the start of every video job (higgsfield_video.run_video_job)
  - manually via the prepare_inputs.py CLI wrapper
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ROLES_DEFAULT = ["host", "guest", "both"]
SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic", ".heif"}
# Source preference when several files exist for one role: a non-PNG is the more
# likely original upload; PNG last (it may be an auto-converted copy).
PREF = [".heic", ".heif", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".bmp", ".gif", ".png"]


def _heic_enabled() -> bool:
    try:
        import pillow_heif  # type: ignore
        pillow_heif.register_heif_opener()
        return True
    except Exception:  # noqa: BLE001
        return False


def _candidates(assets_dir: Path, role: str) -> list[Path]:
    if not assets_dir.exists():
        return []
    return sorted(
        p for p in assets_dir.iterdir()
        if p.is_file() and p.stem.lower() == role and p.suffix.lower() in SUPPORTED
    )


def _pick_source(cands: list[Path]) -> Path:
    return sorted(cands, key=lambda p: (PREF.index(p.suffix.lower())
                                        if p.suffix.lower() in PREF else 99))[0]


def _to_clean_png(src: Path):
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(src))  # honor camera rotation
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])  # flatten transparency onto white
        return bg
    return img.convert("RGB")


def normalize_role(assets_dir: Path, role: str) -> str:
    """Normalize one role; returns a one-line human-readable status."""
    cands = _candidates(assets_dir, role)
    if not cands:
        return f"{role}: no image found"

    # If the only file is already a clean <role>.png we still re-save it (cheap) to
    # guarantee RGB + correct orientation; idempotent.
    src = _pick_source(cands)
    try:
        clean = _to_clean_png(src)
    except Exception as e:  # noqa: BLE001
        hint = (" — looks like HEIC/HEIF; run: pip install pillow-heif"
                if src.suffix.lower() in (".heic", ".heif") and not _heic_enabled() else "")
        logger.warning("normalize %s failed reading %s: %s%s", role, src.name, e, hint)
        return f"{role}: FAILED to read {src.name}: {e}{hint}"

    target = assets_dir / f"{role}.png"
    tmp = assets_dir / f".{role}.tmp.png"
    clean.save(tmp, "PNG")
    tmp.replace(target)

    moved = []
    for p in cands:
        if p.resolve() == target.resolve():
            continue
        originals = assets_dir / "_originals"
        originals.mkdir(exist_ok=True)
        try:
            p.replace(originals / p.name)
            moved.append(p.name)
        except Exception:  # noqa: BLE001
            pass

    w, h = clean.size
    extra = f" (archived: {', '.join(moved)})" if moved else ""
    return f"{role}: {src.name} -> {role}.png [{w}x{h}]{extra}"


def normalize_inputs(assets_dir: Path, roles: list[str] | None = None) -> dict:
    """Normalize all roles. Returns {'results': [str,...], 'ready': [roles with png]}.
    Never raises — a normalization hiccup must not break a render."""
    roles = roles or ROLES_DEFAULT
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        logger.warning("Pillow not installed; skipping input normalization (pip install Pillow)")
        return {"results": ["Pillow not installed — skipped normalization"], "ready": []}

    if _heic_enabled():
        logger.info("input normalization: HEIC/HEIF support on")
    results = []
    for r in roles:
        try:
            results.append(normalize_role(Path(assets_dir), r))
        except Exception as e:  # noqa: BLE001
            results.append(f"{r}: skipped ({e})")
    ready = [r for r in roles if (Path(assets_dir) / f"{r}.png").exists()]
    return {"results": results, "ready": ready}