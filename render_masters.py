"""Render masters and platform-safe text placement for Nyx carousels.

Founder direction 2026-09-30, from the visual QA benchmark:
  - 4:5 (1080x1350) is THE public carousel master -- Instagram, Threads, X.
  - A separate 9:16 (1080x1920) master exists ONLY for TikTok and Fanvue.
  - Overlay text sits inside each master's platform safe text zone, never at
    one generic margin that lands under TikTok/Reels UI on a vertical canvas.

Pure data + arithmetic over brand/platform_formats.json. Stdlib only; nothing
here generates, uploads, publishes or spends."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FORMATS_PATH = ROOT / "brand" / "platform_formats.json"

PUBLIC_MASTER = "public_carousel_4x5"
VERTICAL_MASTER = "vertical_9x16"
ANCHORS = ("top", "bottom", "none")


class RenderMasterError(ValueError):
    pass


@lru_cache(maxsize=1)
def formats() -> dict:
    return json.loads(FORMATS_PATH.read_text())


def master(master_id: str) -> dict:
    try:
        m = formats()["render_masters"][master_id]
    except KeyError:
        raise RenderMasterError(f"unknown render master {master_id!r}") from None
    w, h = m["canvas"]
    return {"id": master_id, "aspect": m["aspect"], "width": w, "height": h,
            "platforms": list(m["platforms"]), "filename_suffix": m["filename_suffix"]}


def masters_for_platform(target_platform: str | None) -> list[str]:
    """Primary master first. Unknown/missing platform -> the public master,
    the conservative default every existing 4:5 carousel already uses."""
    return list(formats()["target_platform_masters"].get(target_platform or "instagram",
                                                         [PUBLIC_MASTER]))


def masters_for_lock(lock: dict | None) -> list[str]:
    """What a story lock must be rendered as. Derived from target_platform so a
    lock can never ask for a 9:16 Instagram carousel again; a lock's own
    render_masters field, when present, must agree (see lock_master_problems)."""
    return masters_for_platform((lock or {}).get("target_platform"))


def primary_master(lock: dict | None) -> dict:
    return master(masters_for_lock(lock)[0])


def master_for_aspect(aspect: str | None) -> dict:
    for mid, m in formats()["render_masters"].items():
        if m["aspect"] == aspect:
            return master(mid)
    return master(PUBLIC_MASTER)


def file_suffix(target_platform: str | None, master_id: str) -> str:
    """"" for the item's primary master (historical filenames stay valid), the
    master's own suffix for a secondary master rendered beside it."""
    if master_id == masters_for_platform(target_platform)[0]:
        return ""
    return master(master_id)["filename_suffix"]


def lock_master_problems(lock: dict) -> list[str]:
    want = masters_for_lock(lock)
    probs = []
    declared = lock.get("render_masters")
    if declared is not None and list(declared) != want:
        probs.append(f"render_masters {declared} disagrees with target_platform "
                     f"{lock.get('target_platform')!r} -> {want}")
    if lock.get("aspect_ratio") and lock["aspect_ratio"] != master(want[0])["aspect"]:
        probs.append(f"aspect_ratio {lock['aspect_ratio']} is not the primary master's "
                     f"{master(want[0])['aspect']}")
    return probs


def safe_text_zone(master_id: str) -> dict:
    """The strictest inset of every platform that shares this master, so one
    rendered overlay is safe on all of them (e.g. 9:16 = TikTok UI rail AND
    Fanvue's feed chrome)."""
    plats = formats()["platforms"]
    zones = [plats[p]["formats"][plats[p]["carousel_format"]]["safe_text_zone"]
             for p in master(master_id)["platforms"]]
    return {k: max(z[k] for z in zones) for k in ("top", "bottom", "left", "right")}


def resolve_anchor(slide: dict | None) -> tuple[str, str]:
    """(anchor, source). An explicit text_placement.anchor wins; the older
    worded overlay_safe_zone ('upper third, clear of ...') is read next.
    Neither -> bottom, reported as 'default' so QA can flag it."""
    slide = slide or {}
    explicit = (slide.get("text_placement") or {}).get("anchor")
    if explicit:
        if explicit not in ANCHORS:
            raise RenderMasterError(f"text_placement.anchor must be one of {ANCHORS}, got {explicit!r}")
        return explicit, "text_placement"
    zone = str(slide.get("overlay_safe_zone") or "").lower()
    if re.search(r"\b(upper|top)\b", zone):
        return "top", "overlay_safe_zone"
    if re.search(r"\b(lower|bottom)\b", zone):
        return "bottom", "overlay_safe_zone"
    return "bottom", "default"


def text_box(master_id: str, anchor: str, *, width: int, height: int, block_height: int,
             max_width_frac: float) -> dict:
    """Pixel box for an overlay block of `block_height` px on a canvas of this
    master. Raises when the canvas is not this master's aspect -- burning a
    4:5 layout onto a 9:16 image (or vice versa) is exactly the bug this
    module exists to prevent."""
    m = master(master_id)
    if abs(width / height - m["width"] / m["height"]) > formats()["aspect_tolerance"]:
        raise RenderMasterError(f"{width}x{height} is not a {m['aspect']} canvas for {master_id}")
    z = safe_text_zone(master_id)
    x = round(width * z["left"])
    max_w = round(width * min(max_width_frac, 1 - z["left"] - z["right"]))
    top, bottom = round(height * z["top"]), height - round(height * z["bottom"])
    if block_height > bottom - top:
        raise RenderMasterError("overlay block is taller than the safe text zone")
    y = top if anchor == "top" else bottom - block_height
    return {"x": x, "y": y, "max_width": max_w, "safe_top": top, "safe_bottom": bottom,
            "zone": z}
