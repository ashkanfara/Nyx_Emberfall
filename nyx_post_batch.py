#!/usr/bin/env python3
"""Plan-first batches of story-led post packages.

A batch plan (episodes/<batch>/batch_plan.json) describes several packages.
Each package is a short story told from ONE set of true 9:16 masters
(1080x1920), and every format is cut from those masters:

  * a 4-frame Instagram carousel: 4:5 crops (1080x1350) taken vertically
    from the masters at full width, with no scaling and no horizontal crop
  * an 18-25 s vertical Reel: one spoken sentence per master, in story order
  * a 3-frame Story sequence: full 9:16 masters with a short sticker line
  * a caption and Nyx's first-person voiceover script

The order of work is fixed:
  1. `check` validates the plan: continuity ledger, shot list, crops,
     cause-and-effect order in the Reel, voice rules and audience-text rules.
  2. `build` refuses to write anything unless step 1 is clean. It then builds
     every asset in memory, re-validates them with the repo's own checkers
     (story_continuity.plan_problems, nyx_reels' script rules,
     compile_prompt), and only then writes them.
  3. `crop` (later, once masters exist) cuts the 4:5 carousel frames.

Nothing here generates an image or audio, uploads, schedules or publishes.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import nyx_reels
import nyx_runner as nr
import stages
import story_continuity as sc

ROOT = Path(__file__).resolve().parent
DEFAULT_BATCH = ROOT / "episodes" / "deliveries_batch"
PLAN_NAME = "batch_plan.json"
CANONICAL_LOCK = ROOT / "brand" / "story_locks" / "s1e02_public.json"

PACKAGE_COUNT = 5
CAROUSEL_FRAMES = 4
STORY_FRAMES = 3
REEL_ROLES = ("ESTABLISH", "OBJECT", "ACTION", "REACTION", "CONSEQUENCE")
ROLE_TO_CAROUSEL = {"ESTABLISH": "CONTEXT", "OBJECT": "ESCALATION", "ACTION": "PROGRESSION",
                    "REACTION": "PROGRESSION", "CONSEQUENCE": "PAYOFF"}
MOTION = {"ESTABLISH": ("push_in", 1.04), "OBJECT": ("push_in", 1.06), "ACTION": ("pan_right", 1.04),
          "REACTION": ("push_in", 1.05), "CONSEQUENCE": ("push_out", 1.04)}
CAMERA_KEYS = sc.CAMERA_FIELDS
DOOR_WORDS = re.compile(r"\b(door|peephole|deadbolt|knob)\b", re.I)
# Drift words that must never appear in a shot description. The canon blocks
# already say "no earrings"; a shot that names one is drifting.
SHOT_DRIFT = re.compile(
    r"\b(earrings?|human ears?|lever|keypad|smart lock|chain|mail ?slot|daylight|sunrise|sunset|"
    r"morning|afternoon|hoodie|dress|skirt|shorts|lingerie|bra|trunk|second cat|dog|glow(?:ing)?|"
    r"magic|sparkl\w*|shoes?|socks?|barefoot|text|letters?|words? written|handwriting)\b", re.I)


class PlanError(Exception):
    pass


def load_plan(batch: Path = DEFAULT_BATCH) -> dict:
    return json.loads((batch / PLAN_NAME).read_text())


def _words(text: str) -> list[str]:
    return nyx_reels._words(text)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.?!])\s+", text.strip()) if s]


def named_props(text: str, props: dict) -> set[str]:
    low = text.lower()
    return {pid for pid, p in props.items()
            if any(re.search(rf"(?<![a-z]){re.escape(a.lower())}(?![a-z])", low) for a in p["aliases"])}


def _masters(pkg: dict) -> dict:
    return {m["id"]: m for m in pkg["masters"]}


def _fingerprint(master: dict) -> tuple:
    return tuple(" ".join(str(master["camera"].get(k, "")).lower().split()) for k in CAMERA_KEYS)


# --- timing -------------------------------------------------------------------
def _chunks(text: str, limit: int) -> list[str]:
    """Caption chunks of at most `limit` words, as even as possible, so no
    chunk is a lone word flashing past under the minimum on-screen time."""
    tokens = text.split()
    k = max(1, math.ceil(len(tokens) / limit))
    size, extra = divmod(len(tokens), k)
    out, i = [], 0
    for j in range(k):
        n = size + (1 if j < extra else 0)
        out.append(" ".join(tokens[i:i + n]))
        i += n
    return out


def reel_script(pkg: dict, plan: dict, bible: dict) -> dict:
    t = plan["reel_timing"]
    cap = bible["reel_format"]["captions"]
    per_word = 60.0 / t["wpm"]
    order = [m["id"] for m in pkg["masters"]]
    lines, scenes, cursor = [], [], t["lead_s"]
    for i, beat in enumerate(pkg["reel"], 1):
        n = len(_words(beat["text"]))
        dur = max(math.ceil(n * per_word * 10) / 10, 1.2)
        start, end = round(cursor, 1), round(cursor + dur, 1)
        chunks = _chunks(beat["text"], 4)
        counts = [len(_words(c)) for c in chunks]
        caps, c0 = [], start
        for j, (c, k) in enumerate(zip(chunks, counts)):
            c1 = end if j == len(chunks) - 1 else round(c0 + (end - start) * k / n, 1)
            caps.append({"text": c, "start_s": c0, "end_s": c1})
            c0 = c1
        lines.append({"id": f"b{i}_{beat['role'].lower()}", "start_s": start, "end_s": end,
                      "text": beat["text"], "captions": caps})
        cursor = end + t["gap_s"]
    duration = round(lines[-1]["end_s"] + t["tail_s"], 1)
    edges = [0.0] + [round((lines[k]["end_s"] + lines[k + 1]["start_s"]) / 2, 2)
                     for k in range(len(lines) - 1)] + [duration]
    for i, beat in enumerate(pkg["reel"]):
        motion, zoom = MOTION[beat["role"]]
        scenes.append({"slide": order.index(beat["master"]) + 1, "master": beat["master"],
                       "role": beat["role"], "start_s": edges[i], "end_s": edges[i + 1],
                       "motion": motion, "zoom": zoom})
    return {"story_id": pkg["id"], "voice_bible_version": bible["spec_version"],
            "title": f"{pkg['title']} -- voiceover Reel", "duration_s": duration,
            "status": "DRAFT script -- no audio, no approved masters yet",
            "lines": lines, "scenes": scenes, "music": None,
            "music_note": "voice-only until a licensed instrumental bed is chosen"}


# --- plan checks ----------------------------------------------------------------
def _ledger_walk(pkg: dict, start: dict, props: dict, probs: list) -> dict:
    """Replay the package's prop events master by master; every prop a master
    shows must be in exactly the state the ledger says it is in by then."""
    state = dict(start)
    for m in pkg["masters"]:
        tag = f"{pkg['id']} {m['id']}"
        for pid, st in (m.get("events") or {}).items():
            if pid not in props:
                probs.append(f"{tag}: event on unknown prop {pid!r}")
                continue
            intro = props[pid].get("introduced_in")
            if pid not in state and intro != pkg["id"]:
                probs.append(f"{tag}: {pid} appears with no introduction in this package "
                             f"(introduced_in={intro!r}) -- an unexplained prop")
            state[pid] = st
        for pid, st in (m.get("props_visible") or {}).items():
            if pid not in props:
                probs.append(f"{tag}: shows unknown prop {pid!r}")
            elif not props[pid].get("tracked", True):
                continue
            elif state.get(pid) != st:
                probs.append(f"{tag}: {pid} shown as {st!r} but the ledger has {state.get(pid)!r} "
                             "-- an unexplained prop change")
        for pid in m.get("shows_absence_of") or []:
            if pid not in state or "gone" not in state[pid]:
                probs.append(f"{tag}: shows {pid} missing but the ledger has {state.get(pid)!r}")
    return state


def _check_master(pkg: dict, m: dict, plan: dict, probs: list) -> None:
    tag = f"{pkg['id']} {m['id']}"
    for k in ("location", "presence", "action", "composition", "new_info", "ears_tail", "subject_band"):
        if not m.get(k):
            probs.append(f"{tag}: missing {k}")
    if m.get("presence") not in sc.CHARACTER_PRESENCE:
        probs.append(f"{tag}: presence must be one of {sc.CHARACTER_PRESENCE}")
    missing = [k for k in CAMERA_KEYS if not str(m.get("camera", {}).get(k, "")).strip()]
    if missing:
        probs.append(f"{tag}: camera missing {missing}")
    shot = " ".join([m.get("action", ""), m.get("composition", "")]
                    + [str(v) for v in m.get("camera", {}).values()])
    for hit in sorted({h.lower() for h in SHOT_DRIFT.findall(shot)}):
        probs.append(f"{tag}: shot description drifts from canon ({hit!r})")
    if m.get("presence") == "absent" and m.get("face_visible"):
        probs.append(f"{tag}: character-absent shot cannot show a face")
    if m.get("face_visible") and "both fox ears" not in m.get("ears_tail", ""):
        probs.append(f"{tag}: her face is in frame, so both fox ears must be too")
    if DOOR_WORDS.search(shot) and not m.get("door_in_frame"):
        probs.append(f"{tag}: describes door hardware but door_in_frame is false")
    y0, y1 = (m.get("subject_band") or [0, 0])
    if not 0 <= y0 < y1 <= plan["master_canvas"]["height"]:
        probs.append(f"{tag}: subject_band {m.get('subject_band')} is outside the 9:16 master")


def _check_reel(pkg: dict, plan: dict, bible: dict, probs: list) -> None:
    ms, props = _masters(pkg), plan["props"]
    order = [m["id"] for m in pkg["masters"]]
    beats = pkg.get("reel") or []
    roles = [b.get("role") for b in beats]
    if any(r not in REEL_ROLES for r in roles):
        probs.append(f"{pkg['id']} reel: roles must be from {REEL_ROLES}, got {roles}")
        return
    if roles[0] != "ESTABLISH":
        probs.append(f"{pkg['id']} reel must open by establishing the room")
    if roles[-1] != "CONSEQUENCE":
        probs.append(f"{pkg['id']} reel must end on a consequence (the open loop)")
    try:
        o, r = roles.index("OBJECT"), roles.index("REACTION")
        if not o < r < len(roles) - 1:
            probs.append(f"{pkg['id']} reel order must be object -> reaction -> consequence, got {roles}")
    except ValueError:
        probs.append(f"{pkg['id']} reel needs an OBJECT beat and a REACTION beat, got {roles}")
    used = [b["master"] for b in beats]
    if len(set(used)) != len(used):
        probs.append(f"{pkg['id']} reel reuses a master: one visual beat per sentence")
    if any(u not in ms for u in used):
        probs.append(f"{pkg['id']} reel names an unknown master")
        return
    if [order.index(u) for u in used] != sorted(order.index(u) for u in used):
        probs.append(f"{pkg['id']} reel plays masters out of story order (cause must precede effect)")
    if set(used) != set(order):
        probs.append(f"{pkg['id']} reel skips masters {sorted(set(order) - set(used))}")
    first_named: dict[str, int] = {}
    for i, b in enumerate(beats):
        tag = f"{pkg['id']} reel beat {i + 1}"
        if len(_sentences(b["text"])) != 1:
            probs.append(f"{tag}: one sentence per visual beat, got {b['text']!r}")
        m = ms[b["master"]]
        shown = set(m.get("props_visible") or {}) | set(m.get("shows_absence_of") or [])
        for pid in named_props(b["text"], props):
            first_named.setdefault(pid, i)
            if pid not in shown:
                probs.append(f"{tag}: names {pid} but {b['master']} does not show it")
        if DOOR_WORDS.search(b["text"]) and not m.get("door_in_frame"):
            probs.append(f"{tag}: mentions the door but {b['master']} does not show it")
        if b["role"] == "REACTION" and not m.get("face_visible"):
            probs.append(f"{tag}: a reaction beat must show Nyx's face")
        if b["role"] == "ESTABLISH" and m.get("presence") == "present" and m["camera"]["distance"] not in (
                "medium", "medium-wide"):
            probs.append(f"{tag}: the establishing beat must show the room (medium or medium-wide)")
    # The object appears exactly when it is first named, never before.
    for pid, p in props.items():
        if p.get("introduced_in") != pkg["id"]:
            continue
        if pid not in first_named:
            probs.append(f"{pkg['id']}: new prop {pid} is never named in the reel -- unexplained")
            continue
        for b in beats[:first_named[pid]]:
            if pid in (ms[b["master"]].get("props_visible") or {}):
                probs.append(f"{pkg['id']}: {pid} is on screen ({b['master']}) before it is named")


def _check_text(pkg: dict, plan: dict, probs: list) -> None:
    rules = plan["audience_text_rules"]
    texts = ([("caption", pkg.get("caption", ""))]
             + [(f"overlay {f['master']}", f.get("overlay", "")) for f in pkg.get("carousel", [])]
             + [(f"sticker {f['master']}", f.get("sticker", "")) for f in pkg.get("story", [])]
             + [(f"reel {b['master']}", b["text"]) for b in pkg.get("reel", [])])
    for where, text in texts:
        low = text.lower()
        for bad in rules["forbidden_resolution"]:
            if bad in low:
                probs.append(f"{pkg['id']} {where}: premature resolution {bad!r}")
        for bad in rules["forbidden_props"]:
            if re.search(rf"\b{re.escape(bad)}\b", low):
                probs.append(f"{pkg['id']} {where}: names a prop this batch never shows ({bad!r})")
        if stages._ENGAGEMENT_BAIT.search(text) or stages._NARRATOR_VOICE.search(text):
            probs.append(f"{pkg['id']} {where}: engagement bait or third-person narration")
        if stages._DISCLAIMER_TEXT.search(text):
            probs.append(f"{pkg['id']} {where}: disclosure belongs to the platform AI label")
        if "!" in text or re.search(r"\b[A-Z]{4,}\b", text):
            probs.append(f"{pkg['id']} {where}: off-voice (shouting)")
    cap = pkg.get("caption", "")
    if not cap or len(cap) > rules["caption_max_chars"]:
        probs.append(f"{pkg['id']} caption must be 1-{rules['caption_max_chars']} characters")
    for f in pkg.get("carousel", []):
        if len(_words(f.get("overlay", ""))) > rules["overlay_max_words"]:
            probs.append(f"{pkg['id']} overlay on {f['master']} is over {rules['overlay_max_words']} words")
    for f in pkg.get("story", []):
        n = len(_words(f.get("sticker", "")))
        if not 0 < n <= rules["sticker_max_words"]:
            probs.append(f"{pkg['id']} story sticker on {f['master']} must be 1-{rules['sticker_max_words']} words")


def sticker_slot(master: dict, plan: dict) -> str | None:
    """The Story sticker slot that clears the subject: upper if the subject
    starts below it, else lower if the subject ends above it, else none."""
    band = plan["story_safe_band"]
    gap = band.get("sticker_clearance_px", 20)
    y0, y1 = master["subject_band"]
    slots = band["sticker_slots"]
    if y0 >= slots["upper"][1] + gap:
        return "upper"
    if y1 <= slots["lower"][0] - gap:
        return "lower"
    return None


def _check_formats(pkg: dict, plan: dict, probs: list) -> None:
    ms = _masters(pkg)
    crop, canvas = plan["carousel_crop"], plan["master_canvas"]
    margin = crop["subject_margin_px"]
    if (canvas["width"], canvas["height"]) != (1080, 1920):
        probs.append("masters must be true 9:16 at 1080x1920")
    if crop["width"] != canvas["width"] or crop["x"] != 0 or crop["width"] * 5 != crop["height"] * 4:
        probs.append("carousel crop must be full-width 4:5 (1080x1350, x=0): no zoom, no horizontal crop")
    frames = pkg.get("carousel") or []
    if len(frames) != CAROUSEL_FRAMES:
        probs.append(f"{pkg['id']} carousel needs exactly {CAROUSEL_FRAMES} frames, has {len(frames)}")
    if [f.get("role") for f in frames][:1] != ["HOOK"] or [f.get("role") for f in frames][-1:] != ["PAYOFF"]:
        probs.append(f"{pkg['id']} carousel must open on HOOK and close on PAYOFF")
    if len({f["master"] for f in frames}) != len(frames):
        probs.append(f"{pkg['id']} carousel repeats a master")
    order = [m["id"] for m in pkg["masters"]]
    if [order.index(f["master"]) for f in frames if f["master"] in ms] != sorted(
            order.index(f["master"]) for f in frames if f["master"] in ms):
        probs.append(f"{pkg['id']} carousel frames are out of story order")
    if not frames or not frames[0].get("overlay"):
        probs.append(f"{pkg['id']} carousel hook frame needs its overlay line")
    if sum(1 for f in frames if f.get("overlay")) > 2:
        probs.append(f"{pkg['id']} carousel must stay mostly wordless (at most 2 overlays)")
    for f in frames:
        m = ms.get(f["master"])
        if m is None:
            probs.append(f"{pkg['id']} carousel names unknown master {f['master']}")
            continue
        top = f.get("crop_top", -1)
        if not 0 <= top <= canvas["height"] - crop["height"]:
            probs.append(f"{pkg['id']} {f['master']}: crop_top {top} leaves the 9:16 master")
            continue
        y0, y1 = m["subject_band"]
        if y0 < top + margin or y1 > top + crop["height"] - margin:
            probs.append(f"{pkg['id']} {f['master']}: subject band {y0}-{y1} is cut by the 4:5 crop "
                         f"{top}-{top + crop['height']}")
    story = pkg.get("story") or []
    band = plan["story_safe_band"]
    if len(story) != STORY_FRAMES:
        probs.append(f"{pkg['id']} story needs exactly {STORY_FRAMES} frames, has {len(story)}")
    if len({f["master"] for f in story}) != len(story):
        probs.append(f"{pkg['id']} story repeats a master")
    for f in story:
        m = ms.get(f["master"])
        if m is None:
            probs.append(f"{pkg['id']} story names unknown master {f['master']}")
        elif m["subject_band"][0] < band["top"] or m["subject_band"][1] > band["bottom"]:
            probs.append(f"{pkg['id']} story {f['master']}: subject band {m['subject_band']} runs under "
                         f"the Story UI (safe {band['top']}-{band['bottom']})")
        elif sticker_slot(m, plan) is None:
            probs.append(f"{pkg['id']} story {f['master']}: no sticker slot clears the subject "
                         f"{m['subject_band']}")


def check_plan(plan: dict, bible: dict | None = None) -> list[str]:
    bible = bible or nyx_reels.load_bible(nyx_reels.Paths(ROOT))
    probs: list[str] = []
    pkgs = plan.get("packages") or []
    if len(pkgs) != PACKAGE_COUNT:
        probs.append(f"batch needs exactly {PACKAGE_COUNT} packages, has {len(pkgs)}")
    for key in ("id", "title", "hook_type", "hook", "open_loop"):
        values = [str(p.get(key, "")).strip().lower() for p in pkgs]
        if not all(values):
            probs.append(f"every package needs a {key}")
        if len(set(values)) != len(values):
            probs.append(f"packages share a {key}: every hook and open loop must be distinct")
    init, anchor_end = plan.get("initial_state") or {}, plan.get("anchor_episode", {}).get("end_state") or {}
    for k, v in anchor_end.items():
        if init.get(k) != v:
            probs.append(f"initial_state.{k} {init.get(k)!r} contradicts the anchor episode's end_state {v!r}")
    if not plan.get("canon", {}).get("front_door_hardware", {}).get("description"):
        probs.append("canonical front-door hardware is not defined")
    seen_shots: dict[tuple, str] = {}
    state = dict(plan.get("initial_state") or {})
    for pkg in pkgs:
        for m in pkg.get("masters") or []:
            _check_master(pkg, m, plan, probs)
            fp = _fingerprint(m)
            if fp in seen_shots:
                probs.append(f"{pkg['id']} {m['id']} repeats the camera of {seen_shots[fp]}")
            seen_shots[fp] = f"{pkg['id']} {m['id']}"
        state = _ledger_walk(pkg, state, plan["props"], probs)
        _check_reel(pkg, plan, bible, probs)
        _check_formats(pkg, plan, probs)
        _check_text(pkg, plan, probs)
        script = reel_script(pkg, plan, bible)
        t = plan["reel_timing"]
        if not t["min_s"] <= script["duration_s"] <= t["max_s"]:
            probs.append(f"{pkg['id']} reel runs {script['duration_s']} s, outside {t['min_s']}-{t['max_s']} s")
        errors: list[str] = []
        nyx_reels._check_script(script, bible, {}, errors, [])
        probs += [f"{pkg['id']} voice: {e}" for e in errors]
    return probs


# --- build ----------------------------------------------------------------------
def _canon_lock() -> dict:
    return json.loads(CANONICAL_LOCK.read_text())


def master_lock(pkg: dict, plan: dict, start_state: dict, base: dict) -> dict:
    canon, props = plan["canon"], plan["props"]
    door = canon["front_door_hardware"]
    env = json.loads(json.dumps(base["environment_lock"]))
    env["status_note"] = ("Reused from s1e02_public (the established loft), plus this batch's DRAFT "
                          "front-door hardware and fixed layout.")
    env["anchors"] = [dict(a, description=a["description"] + "; hardware: " + door["description"])
                      if a["id"] == "front_door" else a for a in env["anchors"]]
    env["layout"] = canon["environment"]["layout"]
    env["forbidden"] = list(env.get("forbidden") or []) + [
        "door hardware other than the canonical set: " + ", ".join(door["never"]),
        "the trunk in frame (it belongs to later season episodes)",
        "a second envelope design: every delivered envelope is the same plain cream envelope"]
    wardrobe = dict(base["wardrobe_lock"])
    wardrobe["feet"] = canon["wardrobe"]["feet"]
    frames = {f["master"]: f for f in pkg["carousel"]}
    roles = {b["master"]: b["role"] for b in pkg["reel"]}
    state, slides, prev = dict(start_state), [], None
    for i, m in enumerate(pkg["masters"], 1):
        state.update(m.get("events") or {})
        frame = frames.get(m["id"])
        overlay = (frame or {}).get("overlay", "")
        role = frame["role"] if frame else ROLE_TO_CAROUSEL[roles[m["id"]]]
        required = [f"same night loft, wardrobe and hair as s1e02_public; {canon['lighting']}",
                    canon["text_in_image"],
                    f"true 9:16 composition for 1080x1920; keep the subject between y={m['subject_band'][0]} "
                    f"and y={m['subject_band'][1]} so the 4:5 and Story crops never cut it"]
        if m["presence"] == "present":
            required.append(f"visible identity: {m['ears_tail']}; exactly one tail, never a human ear, no earrings")
        required += [f"{props[p]['description']}: {s}" for p, s in (m.get("props_visible") or {}).items()]
        required += [f"{props[p]['description']}: NOT here any more" for p in m.get("shows_absence_of") or []]
        if m.get("door_in_frame"):
            required.append("front door exactly as canon: " + door["description"])
        slides.append({
            "slide_index": i, "master_id": m["id"], "role": role,
            "narrative_role": roles[m["id"]].lower(), "character_presence": m["presence"],
            "beat": m["action"], "story_beat": m["action"], "new_information_revealed": m["new_info"],
            "visual_action": m["action"], "composition": m["composition"], "camera": dict(m["camera"]),
            "overlay_copy": overlay,
            "overlay_safe_zone": (f"top band of the 4:5 crop: master y {frame['crop_top'] + 90}-"
                                  f"{frame['crop_top'] + 330}, clear of her face" if overlay else ""),
            "continuity": ("Opens the package in the established loft." if prev is None else
                           f"Directly follows {prev['id']} ({prev['new_info']})."),
            "required_continuity": required,
            "forbidden_repetition": [] if prev is None else [f"{prev['id']}'s framing: {prev['composition']}"],
            "state_after": {"time": "night", "wardrobe": wardrobe["outfit"],
                            "character_position": m["location"], "character_action": m["action"],
                            "props": {p: state[p] for p in sorted(state)}},
        })
        prev = m
    return {
        "schema_version": 1, "spec_version": "1.0.0",
        "identity_lock": base["identity_lock"], "environment_lock": env, "wardrobe_lock": wardrobe,
        "visual_qa": base.get("visual_qa"),
        "story_id": pkg["id"],
        "status": "DRAFT -- not approved, not canon, not enrolled",
        "approved_by": None, "locked_since": None,
        "item_index": pkg["item_index"], "item_id": f"content_items[{pkg['item_index']}]",
        "item_index_note": "placeholder: confirm the next free content_items index in state/venture.json",
        "story_thread": plan["title"].split(" -- ")[0], "season_id": "season_1",
        "episode_id": f"after_s1e03:{pkg['id']}", "target_platform": "instagram",
        "tone": "public", "visual_policy": "public_social", "aspect_ratio": "9:16",
        "output": {"master": "1080x1920 (9:16)",
                   "carousel": "4:5 crops 1080x1350 at full width, crop_top per frame in carousel.json",
                   "story": "full 9:16 master", "reel": "full 9:16 master"},
        "authority": "DRAFT master lock for one story-led post package. Every format is cut from these masters.",
        "initial_story_state": {"character_position": "in the loft", "character_action": "the next night",
                                "time": "night", "wardrobe": wardrobe["outfit"], "props": dict(start_state)},
        "generation": {"enabled": False, "mode": "EXTERNAL_CHATGPT_PRIMARY",
                       "intended_provider": "chatgpt_handoff (free batch)", "fallback_provider": "none",
                       "paid_generation_authorized": False,
                       "authorization_note": "DRAFT: nothing is generated until the founder approves this lock."},
        "cost_state": {"currency": "USD", "estimated_cost_usd": 0, "actual_cost_usd": 0, "ceiling_usd": 0},
        "founder_approvals": [],
        "retry": {"max_attempts_per_slide": 3, "rule": "exception-only policy: 3 visual-QA attempts per slide"},
        "story_plan": slides,
    }


def prompt_pack(lock: dict) -> list[dict]:
    """Every master's compiled prompt, the same way the runner compiles a batch."""
    base_state = sc.new_state(lock)
    out = []
    for rec in sc.story_plan(lock):
        compiled = sc.compile_prompt({}, nr._hypothetical_state(lock, base_state, rec["slide_index"]), rec)
        out.append({"slide": rec["slide_index"], "master": lock["story_plan"][rec["slide_index"] - 1]["master_id"],
                    "presence": rec["character_presence"], "prompt": compiled["prompt"]})
    return out


def _md_package(pkg: dict, plan: dict, script: dict, start: dict, end: dict) -> str:
    ms = _masters(pkg)
    crop = plan["carousel_crop"]
    L = [f"# {pkg['title']} ({pkg['id']})", "",
         "DRAFT. Plan and text only: no image, audio, upload, schedule or publish.", "",
         f"**Hook ({pkg['hook_type']}):** {pkg['hook']}  ", f"**Open loop:** {pkg['open_loop']}", "",
         pkg["synopsis"], "", "## Shot list (true 9:16 masters, 1080x1920)", "",
         "| Master | Nyx | Camera | Action | Props on screen | Subject band (y) |", "|---|---|---|---|---|---|"]
    for m in pkg["masters"]:
        cam = m["camera"]
        shown = "; ".join(f"{p}: {s}" for p, s in (m.get("props_visible") or {}).items()) or "-"
        if m.get("shows_absence_of"):
            shown += "; MISSING: " + ", ".join(m["shows_absence_of"])
        who = "absent" if m["presence"] == "absent" else m["ears_tail"]
        L.append(f"| {m['id']} | {who} | {cam['distance']}, {cam['angle']}, {cam['height']} | {m['action']} | "
                 f"{shown} | {m['subject_band'][0]}-{m['subject_band'][1]} |")
    L += ["", "## 1. Instagram carousel (4 frames, 4:5 vertical crops of the masters)", "",
          f"Each frame is the master's full 1080 px width, rows `crop_top` to `crop_top + {crop['height']}`. "
          "No scaling, no horizontal crop.", "",
          "| # | Role | Master | Crop rows | Overlay |", "|---|---|---|---|---|"]
    for i, f in enumerate(pkg["carousel"], 1):
        L.append(f"| {i} | {f['role']} | {f['master']} | {f['crop_top']}-{f['crop_top'] + crop['height']} | "
                 f"{f['overlay'] or '(wordless)'} |")
    L += ["", f"## 2. Reel outline ({script['duration_s']} s, 1080x1920)", "",
          "One sentence per visual beat. The room comes first, each object is on screen exactly when it is "
          "named, then her reaction, then the consequence.", "",
          "| Time | Beat | Master | Motion | She says |", "|---|---|---|---|---|"]
    for line, sc_ in zip(script["lines"], script["scenes"]):
        L.append(f"| {sc_['start_s']:.1f}-{sc_['end_s']:.1f} s | {sc_['role']} | {sc_['master']} | "
                 f"{sc_['motion']} {sc_['zoom']} | {line['text']} |")
    st = script.get("_stats", {})
    L += ["", f"{st.get('words', '?')} words at {st.get('wpm', '?')} wpm. Captions: `reel/voiceover_script.json`.", "",
          "## 3. Story sequence (3 frames, full 9:16 masters)", "",
          "| # | Master | Sticker line | Sticker rows |", "|---|---|---|---|"]
    for i, f in enumerate(pkg["story"], 1):
        slot = sticker_slot(ms[f["master"]], plan)
        rows = plan["story_safe_band"]["sticker_slots"][slot]
        L.append(f"| {i} | {f['master']} | {f['sticker']} | {slot}, y {rows[0]}-{rows[1]} |")
    L += ["", "Each sticker sits clear of her face and of the Story UI. No polls or question stickers. "
          "Apply Instagram's AI label.", "",
          "## 4. Caption and voiceover", "", "**Caption (Instagram, platform AI label on):**", "",
          f"> {pkg['caption']}", "", "**Voiceover (first person, Nyx):**", ""]
    L += [f"{i}. {b['text']}" for i, b in enumerate(pkg["reel"], 1)]
    L += ["", "## Continuity ledger", "", "| Prop | Start | End |", "|---|---|---|"]
    for p in sorted(set(start) | set(end)):
        if start.get(p) != end.get(p) or p in {k for m in ms.values() for k in (m.get('props_visible') or {})}:
            L.append(f"| {p} | {start.get(p, '(not yet in the story)')} | {end.get(p)} |")
    L += ["", "## Visual continuity checklist (tick per master at QA)", ""]
    L += [f"- [ ] {c}" for c in plan["canon"]["identity"]["checks"]]
    L += [f"- [ ] wardrobe: {plan['canon']['wardrobe']['outfit']}; hair {plan['canon']['wardrobe']['hair']}; "
          f"feet: {plan['canon']['wardrobe']['feet']}",
          f"- [ ] {plan['canon']['lighting']}",
          f"- [ ] layout unchanged: {plan['canon']['environment']['layout']}",
          f"- [ ] door (when in frame): {plan['canon']['front_door_hardware']['description']}; never "
          + ", ".join(plan['canon']['front_door_hardware']['never']),
          f"- [ ] {plan['canon']['environment']['cat']}",
          f"- [ ] {plan['canon']['environment']['trunk_rule']}",
          f"- [ ] {plan['canon']['text_in_image']}",
          "- [ ] every prop on screen matches the ledger state above for that master",
          "- [ ] 4:5 frames are vertical crops of the master at full width (no zoom, no horizontal crop)"]
    return "\n".join(L) + "\n"


def _md_batch(plan: dict, scripts: dict) -> str:
    a = plan["anchor_episode"]
    L = [f"# {plan['title']}", "", plan["status"], "",
         "Regenerate everything below with `python3 nyx_post_batch.py build --write` (it refuses to write "
         "unless `python3 nyx_post_batch.py check` passes).", "",
         "## Founder decisions needed before anything is generated", ""]
    L += [f"{i}. {d}" for i, d in enumerate(plan["founder_decisions_needed"], 1)]
    L += ["", f"## Anchor: {a['episode_id']} \"{a['title']}\" ({a['status']})", "",
          f"Assumption: {a['assumption']}", ""]
    L += [f"{i}. {b}" for i, b in enumerate(a["beats"], 1)]
    L += ["", f"Public cliffhanger: \"{a['public_cliffhanger']}\" It does not resolve: "
          + ", ".join(a["does_not_resolve"]) + ".", "",
          "## The mini-season", "",
          "| # | Package | Hook type | Hook | Open loop | Reel |", "|---|---|---|---|---|---|"]
    for i, p in enumerate(plan["packages"], 1):
        L.append(f"| {i} | [{p['title']}]({p['id']}/PACKAGE.md) | {p['hook_type']} | {p['hook']} | "
                 f"{p['open_loop']} | {scripts[p['id']]['duration_s']} s |")
    L += ["", "Arc: an object (smell) -> a pattern (her own sky) -> a presence (the shadow) -> her move "
          "(she writes back) -> an answer (they copied her moon). Nothing resolves: no sender, no meaning "
          "of the starburst, the third envelope stays sealed, the trunk stays out of frame.", "",
          "## Canon this batch fixes (DRAFT until approved)", "",
          f"- **Front door hardware:** {plan['canon']['front_door_hardware']['description']}. Never: "
          + ", ".join(plan["canon"]["front_door_hardware"]["never"]) + ". "
          + plan["canon"]["front_door_hardware"]["why"],
          f"- **Layout:** {plan['canon']['environment']['layout']}",
          f"- **Wardrobe:** {plan['canon']['wardrobe']['outfit']}; hair {plan['canon']['wardrobe']['hair']}; "
          f"feet {plan['canon']['wardrobe']['feet']}.",
          f"- **Trunk:** {plan['canon']['environment']['trunk_rule']}", "",
          "## Prop ledger", "", "| Prop | What it is | Enters | How |", "|---|---|---|---|"]
    for pid, pr in plan["props"].items():
        L.append(f"| {pid} | {pr['description']} | {pr.get('introduced_in', 'already in canon')} | "
                 f"{pr.get('introduced_by', '-')} |")
    L += ["", "## Checks the plan passed before any asset was built", "",
          "- 5 packages; distinct ids, titles, hook types, hooks and open loops",
          "- the anchor episode's end state is the batch's start state",
          "- the prop ledger replays master by master across all five packages: every prop on screen is "
          "in the state the ledger says, new props enter only where the story explains them",
          "- every master: full camera, no canon-drift words, face in frame means both fox ears in frame, "
          "door words mean the door is in frame; no camera repeated anywhere in the batch",
          "- carousel: 4 frames, HOOK first and PAYOFF last, story order, at most 2 overlays, every crop a "
          "full-width 1080x1350 window of the 1080x1920 master that keeps the subject band inside",
          "- story: 3 frames, subject clear of the Story UI, a sticker slot that clears her face",
          "- reel: ESTABLISH first, OBJECT before REACTION before the final CONSEQUENCE; one sentence per "
          "master, masters in story order, all masters used; each named prop is on screen in that beat and "
          "a new prop never appears before it is named; reactions show her face; 18-25 s",
          "- voice: nyx_reels' voice-bible rules (pace, sentence length, captions, banned phrases)",
          "- audience text: no premature resolution, no props the batch never shows, no bait, no "
          "disclaimer in her mouth", "",
          "After building, each master lock passes `story_continuity.plan_problems`, each script passes "
          "the voice rules again against its lock, and every master compiles through `compile_prompt`."]
    return "\n".join(L) + "\n"


def build(plan: dict, batch: Path = DEFAULT_BATCH, bible: dict | None = None) -> dict[str, object]:
    bible = bible or nyx_reels.load_bible(nyx_reels.Paths(ROOT))
    probs = check_plan(plan, bible)
    if probs:
        raise PlanError("plan fails its checks; nothing was built:\n  " + "\n  ".join(probs))
    base = _canon_lock()
    files: dict[str, object] = {}
    state = dict(plan["initial_state"])
    post: list[str] = []
    scripts: dict[str, dict] = {}
    for pkg in plan["packages"]:
        start = dict(state)
        lock = master_lock(pkg, plan, start, base)
        for p in sc.plan_problems(lock):
            post.append(f"{pkg['id']} master lock: {p}")
        for pid, st in ((k, v) for m in pkg["masters"] for k, v in (m.get("events") or {}).items()):
            state[pid] = st
        script = reel_script(pkg, plan, bible)
        scripts[pkg["id"]] = script
        errors: list[str] = []
        nyx_reels._check_script(script, bible, lock, errors, [])
        post += [f"{pkg['id']} reel script: {e}" for e in errors]
        try:
            pack = prompt_pack(lock)
        except sc.ContinuityError as exc:
            post.append(f"{pkg['id']} prompt compile: {exc}")
            pack = []
        for entry in pack:
            if "NO text baked in" not in entry["prompt"]:
                post.append(f"{pkg['id']} {entry['master']}: compiled prompt lost the no-text rule")
        d = pkg["id"]
        stats = script.pop("_stats", {})
        files[f"{d}/masters_lock.DRAFT.json"] = lock
        files[f"{d}/carousel.json"] = {
            "story_id": d, "format": "instagram carousel, 4 frames, 4:5 (1080x1350)",
            "source": "vertical crop of the 1080x1920 masters, full width, no scaling",
            "frames": [{"frame": i, "role": f["role"], "master": f["master"],
                        "crop_box": [0, f["crop_top"], 1080, f["crop_top"] + 1350],
                        "overlay": f["overlay"]} for i, f in enumerate(pkg["carousel"], 1)],
            "caption": pkg["caption"], "ai_disclosure": "Instagram AI label (platform)"}
        files[f"{d}/story.json"] = {
            "story_id": d, "format": "Instagram Story, 3 frames, full 9:16 masters",
            "frames": [{"frame": i, "master": f["master"], "sticker": f["sticker"],
                        "sticker_slot": (slot := sticker_slot(_masters(pkg)[f["master"]], plan)),
                        "sticker_rows": plan["story_safe_band"]["sticker_slots"][slot]}
                       for i, f in enumerate(pkg["story"], 1)],
            "ai_disclosure": "Instagram AI label (platform)", "interactive_stickers": "none (no engagement bait)"}
        files[f"{d}/reel/voiceover_script.json"] = script
        files[f"{d}/prompts.json"] = {"story_id": d, "status": "DRAFT -- for review, not a live batch",
                                      "output": "9:16 master 1080x1920. Official API: 1152x2048. ChatGPT: "
                                                "1024x1536, then centre-crop to 9:16 (keep the subject inside "
                                                "the central 864 px).", "masters": pack}
        script["_stats"] = stats
        files[f"{d}/PACKAGE.md"] = _md_package(pkg, plan, script, start, dict(state))
        script.pop("_stats", None)
    files["BATCH.md"] = _md_batch(plan, scripts)
    if post:
        raise PlanError("built assets fail validation; nothing was written:\n  " + "\n  ".join(post))
    return files


def write(files: dict[str, object], batch: Path = DEFAULT_BATCH) -> list[Path]:
    out = []
    for rel, body in files.items():
        p = batch / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        text = body if isinstance(body, str) else json.dumps(body, indent=2, ensure_ascii=False) + "\n"
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(text)
        tmp.replace(p)
        out.append(p)
    return out


# --- crop (later, once real masters exist) ---------------------------------------
def crop(package_dir: Path, masters_dir: Path, out_dir: Path) -> list[Path]:
    """Cut the 4:5 carousel frames from approved 1080x1920 masters named
    <MASTER>.png (M1.png ...). Refuses anything that is not a true 9:16 master."""
    from PIL import Image
    spec = json.loads((package_dir / "carousel.json").read_text())
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for f in spec["frames"]:
        src = masters_dir / f"{f['master']}.png"
        with Image.open(src) as im:
            if im.size != (1080, 1920):
                raise PlanError(f"{src.name} is {im.size[0]}x{im.size[1]}, not a 1080x1920 9:16 master")
            frame = im.crop(tuple(f["crop_box"]))
            dest = out_dir / f"frame{f['frame']}.png"
            frame.save(dest)
            written.append(dest)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "build"):
        s = sub.add_parser(name)
        s.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
        if name == "build":
            s.add_argument("--write", action="store_true", help="write the assets (default: dry run)")
    c = sub.add_parser("crop")
    c.add_argument("package_dir", type=Path)
    c.add_argument("masters_dir", type=Path)
    c.add_argument("out_dir", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "crop":
        for p in crop(a.package_dir, a.masters_dir, a.out_dir):
            print(p)
        return 0
    plan = load_plan(a.batch)
    if a.cmd == "check":
        probs = check_plan(plan)
        print("PLAN PASSES" if not probs else "PLAN FAILS\n  " + "\n  ".join(probs))
        return 1 if probs else 0
    try:
        files = build(plan, a.batch)
    except PlanError as exc:
        print(exc)
        return 1
    if a.write:
        for p in write(files, a.batch):
            print(p.relative_to(ROOT))
    else:
        print(f"BUILD OK (dry run): {len(files)} files; rerun with --write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
