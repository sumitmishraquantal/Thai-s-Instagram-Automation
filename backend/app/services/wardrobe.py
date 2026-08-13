"""Per-render wardrobe picker.

Every reel gets a fresh outfit for BOTH characters so back-to-back reels don't
look like the same day's shoot. Kept deterministic per render_id (so a
regeneration / rerun uses the same outfit) but different across renders.

The outfits pool is designed for a modern in-person podcast studio: neutral,
natural, TV-appropriate. No loud patterns that would fight with the studio
back wall or the warm moody lighting canon.

Public surface:
  - wardrobe_for_render(render_id) -> dict with keys 'host', 'guest', 'both'
  - HOST and GUEST outfits are always visually distinct (different top color,
    different style) so the two people don't blur together on camera.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Curated shortlist. Each entry is a self-contained clothing description
# that reads naturally when appended after "the person is wearing ...".
# Kept intentionally small so the pool feels curated, not random.
_HOST_OUTFITS: list[str] = [
    "a charcoal-grey merino crewneck sweater over a plain white t-shirt, sleeves relaxed",
    "a matte-black henley shirt with the top button open, sleeves pushed up to the forearm",
    "a deep navy long-sleeve merino top, slim fit, neat collar",
    "a dark olive-green button-down shirt worn open over a plain white t-shirt, sleeves rolled once",
    "a soft slate-blue crewneck sweatshirt, clean and unbranded",
    "a fitted dark heather-grey long-sleeve tee, no logos",
    "a warm chocolate-brown suede-look overshirt buttoned to mid-chest over a black t-shirt",
    "a slim-fit black polo shirt with a subtle knit texture, top button open",
]

_GUEST_OUTFITS: list[str] = [
    "a cream-white ribbed knit sweater with a rolled crew neckline",
    "a warm rust-orange corduroy overshirt worn open over a black t-shirt, sleeves rolled once",
    "a soft sage-green long-sleeve henley, top button open",
    "a light stone-beige oversized button-down shirt, sleeves rolled to the elbow",
    "a muted burgundy crewneck sweater, clean and unbranded",
    "a dusty-blue chambray shirt, top two buttons open, sleeves rolled once",
    "a soft camel-tan cardigan over a plain black t-shirt",
    "a pale grey merino turtleneck, slim fit",
]


def _stable_index(render_id: str, salt: str, modulus: int) -> int:
    """Deterministic index in [0, modulus) from render_id + salt. Same
    render_id + salt always maps to the same index — so a rerun of the same
    render uses the same outfit (matches the cached identity images) and
    different renders get different outfits."""
    h = hashlib.sha256(f"{salt}:{render_id}".encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big") % modulus


def wardrobe_for_render(render_id: str) -> dict:
    """Return {'host': str, 'guest': str, 'both': str, 'render_id': str}.

    'both' is a compact description used by the 'both'-identity prompt so the
    two-shot renders the same outfits as the individual portraits.
    """
    host = _HOST_OUTFITS[_stable_index(render_id, "host", len(_HOST_OUTFITS))]
    guest = _GUEST_OUTFITS[_stable_index(render_id, "guest", len(_GUEST_OUTFITS))]
    both = f"the HOST (on the right) wearing {host}; the GUEST (on the left) wearing {guest}"
    return {"host": host, "guest": guest, "both": both, "render_id": render_id}


def save_wardrobe(render_dir: Path, wardrobe: dict) -> None:
    """Persist the render's chosen wardrobe alongside the other render
    metadata (visual_canon.json, blueprint.json, ...) so you can audit
    which outfits a given reel shipped with."""
    try:
        (render_dir / "wardrobe.json").write_text(
            json.dumps(wardrobe, indent=2), encoding="utf-8"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not save wardrobe.json: %s", e)


def load_wardrobe(render_dir: Path) -> dict | None:
    """Read back a previously-saved wardrobe (for reruns / debugging)."""
    p = render_dir / "wardrobe.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None