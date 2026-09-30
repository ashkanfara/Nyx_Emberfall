"""Canonical room references and the room-continuity gate.

brand/room_references/room_reference_manifest.json names, per environment
location_id, the ONE approved image a room is judged against. A room
continuity PASS is blocked while that image is not registered (or no longer
matches its recorded hash). The only exception is the room's establishing
story: its establishing slide is what creates the reference.

Stdlib only. Nothing here generates, uploads, publishes or spends; `register`
is a local bookkeeping write run by a human/executor after the founder
confirms which file is the approved room master.

    python3 room_references.py status
    python3 room_references.py gate <location_id> <story_id> <slide_index>
    python3 room_references.py register <location_id> <asset_path> <registered_by>
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "brand" / "room_references" / "room_reference_manifest.json"

ALLOWED_VERIFIED = "ALLOWED_REFERENCE_VERIFIED"
ALLOWED_ESTABLISHING = "ALLOWED_ESTABLISHING_STORY"
BLOCKED_NO_REFERENCE = "BLOCKED_NO_ROOM_REFERENCE"
BLOCKED_REFERENCE_MISSING = "BLOCKED_REFERENCE_FILE_MISSING"
BLOCKED_REFERENCE_CHANGED = "BLOCKED_REFERENCE_HASH_MISMATCH"
BLOCKED_UNKNOWN_ROOM = "BLOCKED_UNKNOWN_ROOM"


class RoomReferenceError(ValueError):
    pass


def load(path: Path | None = None) -> dict:
    return json.loads((path or MANIFEST_PATH).read_text())


def _sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _png_size(p: Path) -> tuple[int, int] | None:
    head = p.read_bytes()[:24]
    return struct.unpack(">II", head[16:24]) if head[:8] == b"\x89PNG\r\n\x1a\n" else None


def gate(location_id: str | None, story_id: str | None, slide_index: int | None, *,
         root: Path | None = None, manifest: dict | None = None) -> dict:
    """{"allowed": bool, "status": ..., "reason": ...}. Pure read."""
    root = root or ROOT
    rooms = (manifest or load()).get("rooms", {})
    room = rooms.get(location_id or "")
    base = {"location_id": location_id, "story_id": story_id, "slide_index": slide_index}
    if room is None:
        return {**base, "allowed": False, "status": BLOCKED_UNKNOWN_ROOM,
                "reason": f"{location_id!r} has no entry in the room reference manifest"}
    ref = room.get("reference_asset")
    if ref:
        p = root / ref["path"]
        if not p.is_file():
            return {**base, "allowed": False, "status": BLOCKED_REFERENCE_MISSING,
                    "reason": f"registered room reference {ref['path']} is not on disk"}
        if _sha16(p) != ref["sha256_16"]:
            return {**base, "allowed": False, "status": BLOCKED_REFERENCE_CHANGED,
                    "reason": f"{ref['path']} changed since it was registered -- re-register or restore it"}
        return {**base, "allowed": True, "status": ALLOWED_VERIFIED, "reference": ref["path"]}
    if story_id and story_id == room.get("establishing_story"):
        return {**base, "allowed": True, "status": ALLOWED_ESTABLISHING,
                "reason": (f"{story_id} establishes {location_id}: slide {room.get('establishing_slide')} "
                           "creates the reference; register it once approved")}
    return {**base, "allowed": False, "status": BLOCKED_NO_REFERENCE,
            "reason": (f"no registered reference image for {location_id}; expected the approved "
                       f"{room.get('expected_asset')} -- register it before any room-continuity PASS")}


def gate_for_lock(lock: dict | None, slide_index: int | None, **kw) -> dict:
    lock = lock or {}
    return gate((lock.get("environment_lock") or {}).get("location_id"), lock.get("story_id"),
                slide_index, **kw)


def register(location_id: str, asset_path: str, registered_by: str, *,
             root: Path | None = None, path: Path | None = None) -> dict:
    """Record the approved room master. Refuses a missing file, a non-PNG, or
    a canvas that is not one of the render masters."""
    import render_masters

    root = root or ROOT
    path = path or MANIFEST_PATH
    manifest = load(path)
    room = manifest["rooms"].get(location_id)
    if room is None:
        raise RoomReferenceError(f"unknown location_id {location_id!r}")
    if not str(registered_by).strip():
        raise RoomReferenceError("registered_by is required")
    p = Path(asset_path)
    p = p if p.is_absolute() else root / p
    if not p.is_file():
        raise RoomReferenceError(f"no file at {p}")
    size = _png_size(p)
    if size is None:
        raise RoomReferenceError("room references must be PNG (the pipeline's approved-slide format)")
    masters = render_masters.formats()["render_masters"]
    if not any(abs(size[0] / size[1] - m["canvas"][0] / m["canvas"][1]) <= 0.01 for m in masters.values()):
        raise RoomReferenceError(f"{size[0]}x{size[1]} matches no render master")
    rel = p.resolve().relative_to(root.resolve()).as_posix()
    room["reference_asset"] = {"path": rel, "sha256_16": _sha16(p), "width": size[0], "height": size[1],
                               "registered_by": registered_by,
                               "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    room["status"] = "REGISTERED"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return room


def status(manifest: dict | None = None, root: Path | None = None) -> dict:
    rooms = (manifest or load()).get("rooms", {})
    return {loc: {"status": r.get("status"),
                  "gate_for_non_establishing_story": gate(loc, None, None, root=root,
                                                          manifest=manifest)["status"],
                  "expected_asset": r.get("expected_asset"),
                  "establishing_story": r.get("establishing_story")}
            for loc, r in sorted(rooms.items())}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]
    if cmd == "status":
        out = status()
    elif cmd == "gate":
        loc, story, slide = rest
        out = gate(loc, story, int(slide))
    elif cmd == "register":
        loc, asset, *by = rest
        out = register(loc, asset, " ".join(by))
    else:
        print(__doc__)
        return 1
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if cmd != "gate" or out["allowed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
