"""Nyx episode coordinator -- one durable, tracked pass over the whole pipeline.

Why this exists (PS-05 distribution execution, 2026-09-30): the pipeline has
every individual piece -- story locks, the continuity prompt compiler, the
visual-QA/approval state, overlay placement, the channel registry -- but no
single thing that walks ONE episode through all of them in order and writes
down, in git, exactly where it stands and what the smallest thing standing
between it and a live run is. Stage status lived in chat, in gitignored
generated_assets/, or nowhere.

    brief -> locked_prompts -> generation_handoff -> visual_qa
          -> targeted_repair -> captions -> platform_variants -> publish_gate

It is a COORDINATOR, not a new pipeline. Every stage delegates to the module
that already owns it:

    brief              story_continuity.lock_problems, stages.story_carousel_problems /
                       public_carousel_problems, the season file's episode slot
    locked_prompts     story_continuity.plan_problems / compile_prompt / readiness
    generation_handoff story_continuity.reference_selection; the executor is a human
                       or a separate automation (brand/NYX_PRODUCTION_CONTRACT.md)
    visual_qa          story_continuity state (approve_slide / reject_slide) -- the
                       ONLY approval record; `qa` below calls story_continuity.qa_slide
    targeted_repair    the rejected attempt's failed dimensions + reviewer notes,
                       bounded by the lock's retry.max_attempts_per_slide
    captions           a PS5-authored captions.json, validated here
    platform_variants  channels.CHANNELS + carousel_handoff.overlay_position
    publish_gate       reports live prerequisites; NEVER publishes

Structural boundary (the production contract, enforced by construction): this
module imports no image provider, no publisher, no network client, no browser
driver and no credential store, so it cannot generate, upload, publish or
spend. test_episode_coordinator pins that import list. Everything it writes
lands under episodes/<story_id>/ (tracked) -- it never edits a story lock,
the identity spec or a season file, except via `apply-lock-patch`, which is
additive-only and must be run deliberately.

    python3 episode_coordinator.py dry-run <story_id>
    python3 episode_coordinator.py status <story_id>
    python3 episode_coordinator.py qa <story_id> <slot> <scores.json>     (records a verdict)
    python3 episode_coordinator.py apply-lock-patch <story_id>            (additive only)
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import struct
import sys
import tempfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

import carousel_handoff
import channels
import stages
import story_continuity as sc

ROOT = Path(__file__).resolve().parent
EPISODES_DIR = ROOT / "episodes"
SEASONS_DIR = ROOT / "brand" / "season_arcs"
IDENTITY_SPEC = ROOT / "brand" / "nyx_identity" / "identity_spec.json"
MODE = "DRY_RUN"          # there is no other mode: live steps belong to the executors

STAGES = ("brief", "locked_prompts", "generation_handoff", "visual_qa", "targeted_repair",
          "captions", "platform_variants", "publish_gate")

PASS = "PASS"
PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
BLOCKED = "BLOCKED"
WAITING_ON_EXECUTOR = "WAITING_ON_EXECUTOR"
WAITING_ON_VISUAL_QA = "WAITING_ON_VISUAL_QA"
WAITING_ON_UPSTREAM = "WAITING_ON_UPSTREAM"
NOT_EXECUTED_DRY_RUN = "NOT_EXECUTED_DRY_RUN"

# The pixels every PS-05 carousel slide is actually rendered at
# (story_continuity.dry_run / ps05_ops._slide_request both emit 1080x1350).
OUTPUT = {"width": 1080, "height": 1350, "aspect_ratio": "4:5"}

# Which channels an episode is adapted for, by the lock's target_platform.
# "instagram" still gets TikTok: the SAME slides as a slideshow video (prompts.py).
PLATFORM_PLANS = {
    "public_multi": ("instagram", "tiktok", "threads", "x"),
    "instagram": ("instagram", "tiktok"),
    "tiktok": ("tiktok",),
    "fanvue": ("fanvue",),
}
CAPTION_LIMITS = {"instagram": 2200, "tiktok": 2200, "threads": 500, "x": 280, "fanvue": 5000}
# Routes where the platform's own AI label carries disclosure (nyx_instagram.verify_created
# refuses a Metricool post without it). Everywhere else the caption has to carry it.
NATIVE_AI_LABEL = frozenset({"instagram", "tiktok"})
_DISCLOSURE = re.compile(r"#ai(?:generated|art)?\b|\bai[- ]generated\b|\bai character\b|"
                         r"\bvirtual creator\b", re.I)

# One targeted instruction per QA dimension (story_continuity.QA_DIMENSIONS).
# A repair names the exact defect and changes nothing else -- a full re-roll is
# how the room, the face and the wardrobe drifted on earlier stories.
REPAIR_DIRECTIVES = {
    "identity": "Match the canonical master reference exactly: same face, amber eyes, dark "
                "hair, exactly two fox ears, the single beauty mark on her anatomical LEFT "
                "cheek (viewer-right). Do not restyle or de-age her.",
    "environment_continuity": "Restore the room to the environment master: same geometry, "
                              "same anchors, nothing added, moved or redesigned.",
    "wardrobe_hair_continuity": "Restore the locked wardrobe and hair exactly: {outfit}; {hair}.",
    "prop_continuity": "Restore every prop to its planned state: {props}.",
    "anatomy_correctness": "Fix hands and limbs only: correct joints, no fused, missing or "
                           "extra fingers, no impossible bends.",
    "canonical_ears_visible": "Where the top of her head is in frame, show her canonical fox "
                              "ears; hair covers the sides so no human ear shows; no earrings.",
    "prop_multiplicity": "Every story prop appears exactly once -- remove any duplicate.",
    "sequence_consistency": "Remove anything that contradicts the approved earlier slides.",
    "story_beat": "The frame must visibly show: {new_information_revealed}.",
    "composition_novelty": "Use the planned camera ({camera}); do not reuse an earlier "
                           "slide's framing.",
    "public_social_style": "Candid, social-native photograph: no glamour stance, no "
                           "cinematic or studio styling.",
    "technical_output": "Exactly {width}x{height}; no rendered text, letters, logo or "
                        "watermark; no artefacts.",
}

# What this session may never do, restated in every handoff packet so the
# executor sees the boundary where the work is handed over.
PS5_FORBIDDEN = ("drive or open a browser", "upload a reference image anywhere",
                 "call or trigger an image-generation API or UI", "publish or schedule a post",
                 "change a permission or setting", "spend money or credits")


class CoordinatorError(ValueError):
    """A refusal that must never be worked around."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_file(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# --- inputs ----------------------------------------------------------------------
def lock_path(story_id: str, locks_dir: Path | None = None) -> Path:
    return (locks_dir or sc.STORY_LOCKS_DIR) / f"{story_id}.json"


def load_lock(story_id: str, locks_dir: Path | None = None) -> dict:
    path = lock_path(story_id, locks_dir)
    if not path.is_file():
        raise CoordinatorError(f"no story lock at {path}")
    lock = json.loads(path.read_text())
    if lock.get("story_id") != story_id:
        raise CoordinatorError(f"{path} holds story {lock.get('story_id')!r}, not {story_id!r}")
    return lock


def load_season(season_id: str | None, seasons_dir: Path | None = None) -> dict | None:
    if not season_id:
        return None
    path = (seasons_dir or SEASONS_DIR) / f"{season_id}.json"
    return json.loads(path.read_text()) if path.is_file() else None


def episode_dir(story_id: str, episodes_dir: Path | None = None) -> Path:
    return (episodes_dir or EPISODES_DIR) / story_id


def platforms_for(lock: dict) -> tuple[str, ...]:
    return PLATFORM_PLANS.get(lock.get("target_platform", ""), (lock.get("target_platform", ""),))


# --- lock patch (additive only) --------------------------------------------------------
def _empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def merge_lock_patch(lock: dict, patch: dict) -> tuple[dict, list[str]]:
    """Fill fields a lock's story_plan is MISSING. Never overwrites a non-empty
    value: a patch that would change an approved beat, composition or overlay
    line is refused, so applying it cannot alter canon, only complete it."""
    merged = copy.deepcopy(lock)
    by_slot = {s.get("slide_index"): s for s in merged.get("story_plan") or []}
    conflicts, filled = [], []
    for entry in patch.get("slides") or []:
        slot = entry.get("slide_index")
        target = by_slot.get(slot)
        if target is None:
            conflicts.append(f"patch names slide {slot}, which the lock does not have")
            continue
        for key, value in entry.items():
            if key == "slide_index":
                continue
            if key == "camera" and isinstance(target.get("camera"), dict):
                for ck, cv in value.items():
                    if _empty(target["camera"].get(ck)):
                        target["camera"][ck] = cv
                        filled.append(f"slide {slot} camera.{ck}")
                    elif target["camera"][ck] != cv:
                        conflicts.append(f"slide {slot} camera.{ck} already set -- not overwritten")
                continue
            if _empty(target.get(key)):
                target[key] = value
                filled.append(f"slide {slot} {key}")
            elif target[key] != value:
                conflicts.append(f"slide {slot} {key} already set -- not overwritten")
    if conflicts:
        raise CoordinatorError("lock patch is not additive: " + "; ".join(conflicts))
    return merged, filled


# --- stage 1: brief ----------------------------------------------------------------------
def stage_brief(lock: dict, season: dict | None, *, locks_dir: Path | None = None,
                patched: dict | None = None) -> dict:
    errors, warnings, unverifiable = [], [], []
    errors += [f"lock: {p}" for p in sc.lock_problems(lock)]
    plan = lock.get("story_plan") or []
    item = {"target_platform": "instagram" if lock.get("tone") == "public" else
            lock.get("target_platform"), "slides": sc.brief_slides(lock)}
    for p in stages.story_carousel_problems(item):
        # Item-level brief fields (content_objective, open_loop, ...) live in
        # state/venture.json content_items[N], which is never tracked.
        (unverifiable if p.startswith("missing ") else errors).append(p)
    if lock.get("tone") == "public":
        errors += stages.public_carousel_problems(item)
    if (lock.get("generation") or {}).get("paid_generation_authorized"):
        errors.append("lock claims paid generation is authorized -- the coordinator never spends")

    slot = None
    if lock.get("season_id"):
        if season is None:
            errors.append(f"season file for {lock['season_id']!r} not found")
        else:
            slot = next((s for s in season.get("episode_slots") or []
                         if s.get("episode_id") == lock.get("episode_id")), None)
            if slot is None:
                errors.append(f"{lock['season_id']} has no slot for episode {lock.get('episode_id')!r}")
    if slot:
        # Narrative fields only: authorization boilerplate names every earlier story.
        text = json.dumps([lock.get("story_plan"), lock.get("initial_story_state")]).lower()
        for dep in slot.get("continuity_dependencies") or []:
            token = str(dep).split()[0].lower()
            stem = re.sub(r"_\d{3}$", "", token)
            if not any(t in text for t in {token, stem, stem.replace("_", " ")}):
                warnings.append(
                    f"season slot {slot['episode_id']} ('{slot.get('working_title')}') depends on "
                    f"{dep}, which this lock never references -- either the founder-approved brief "
                    f"supersedes the slot (record it in the season file) or the lock drifted")

    ratio = lock.get("aspect_ratio")
    if ratio and ratio != OUTPUT["aspect_ratio"]:
        warnings.append(f"lock aspect_ratio is {ratio} but every slide is rendered "
                        f"{OUTPUT['width']}x{OUTPUT['height']} ({OUTPUT['aspect_ratio']}); "
                        f"the variants below use {OUTPUT['aspect_ratio']}")
    known = {p.stem for p in (locks_dir or sc.STORY_LOCKS_DIR).glob("*.json")}
    named = set(re.findall(r"[a-z0-9]+(?:_[a-z0-9]+)+", str(lock.get("authority") or "")))
    foreign = sorted((named & known) - {lock.get("story_id")})
    if foreign and lock.get("story_id") not in named:
        warnings.append(f"lock.authority names {foreign}, not {lock.get('story_id')} -- "
                        f"copied from an earlier lock; wording only, no generation effect")
    initial_wardrobe = (lock.get("initial_story_state") or {}).get("wardrobe")
    outfit = (lock.get("wardrobe_lock") or {}).get("outfit")
    if initial_wardrobe and outfit and initial_wardrobe != outfit:
        warnings.append("initial_story_state.wardrobe differs from wardrobe_lock.outfit")

    status = BLOCKED if errors else (PASS_WITH_WARNINGS if warnings else PASS)
    result = {"status": status, "slides": len(plan), "errors": errors, "warnings": warnings,
              "unverifiable_here": unverifiable,
              "season_slot": ({k: slot.get(k) for k in ("episode_id", "working_title", "status")}
                              if slot else None),
              "fix": "; ".join(errors) if errors else None}
    if errors and patched is not None:
        after = stage_brief(patched, season, locks_dir=locks_dir)["errors"]
        result["errors_after_patch"] = after
        if not after:
            result["fix"] = (f"apply the proposed lock patch (python3 episode_coordinator.py "
                             f"apply-lock-patch {lock.get('story_id')}) -- it clears every brief error")
    return result


# --- stage 2: locked prompts --------------------------------------------------------------
def _summarise_problems(problems: list[str]) -> dict:
    by_field: dict[str, list[int]] = {}
    other = []
    for p in problems:
        m = re.match(r"slide (\d+) (?:missing |camera missing )(\w+)", p)
        if m:
            key = ("camera." if "camera missing" in p else "") + m.group(2)
            by_field.setdefault(key, []).append(int(m.group(1)))
        else:
            other.append(p)
    return {"count": len(problems), "missing_fields": by_field, "other": other}


def _active_state(lock: dict, assets_root: Path | None) -> dict:
    """The CURRENT approval state (read-only): story_continuity's own state
    file if QA has ever run, otherwise the lock's opening state."""
    state = sc.load_state(lock, int(lock["item_index"]), root=assets_root)
    return sc.project_active_context(state, lock, root=assets_root)


def stage_locked_prompts(lock: dict, lock_sha: str, out: Path, *, patch: dict | None,
                         assets_root: Path | None, write: bool) -> dict:
    problems = sc.plan_problems(lock)
    result = {"plan_problems": _summarise_problems(problems), "slides": []}
    effective, mode = lock, "LOCKED"
    if problems:
        if not patch:
            return {**result, "status": BLOCKED, "mode": None,
                    "fix": "complete the lock's story_plan planning fields listed in "
                           "plan_problems.missing_fields (additive only), then re-run"}
        try:
            effective, filled = merge_lock_patch(lock, patch)
        except CoordinatorError as exc:
            return {**result, "status": BLOCKED, "mode": None, "fix": str(exc)}
        remaining = sc.plan_problems(effective)
        result["patch"] = {"file": "proposed_lock_patch.json", "fields_filled": len(filled),
                           "problems_after_patch": _summarise_problems(remaining)}
        if remaining:
            return {**result, "status": BLOCKED, "mode": None,
                    "fix": "the proposed patch does not make the plan compilable -- see "
                           "patch.problems_after_patch"}
        mode = "PREVIEW_UNDER_PROPOSED_PATCH"

    state = _active_state(effective, assets_root)
    pack_dir = out / ("prompt_packs" if mode == "LOCKED" else "prompt_packs_preview")
    for record in sc.story_plan(effective):
        n = record["slide_index"]
        compiled = sc.compile_prompt({}, state, record)
        gate = sc.readiness(state, record, root=assets_root)
        header = (f"# {effective['story_id']} slide {n}/{record['total_slides']} -- {mode}\n"
                  f"# lock sha256: {lock_sha}\n"
                  f"# compiled by story_continuity.compile_prompt; paste as-is, attach the "
                  f"references listed in the handoff packet\n\n")
        text = header + compiled["prompt"] + "\n"
        path = pack_dir / f"slide{n}.txt"
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        result["slides"].append({
            "slide_index": n, "role": record["role"], "prompt_pack": _rel(path),
            "prompt_sha256": _sha256_text(compiled["prompt"]),
            "precedence_conflicts": len(compiled["precedence_conflicts"]),
            "gate": ("HANDOFF_READY" if mode == "LOCKED" and gate["ready"] else "PREVIEW_ONLY"),
            "gate_blockers": gate["blockers"]})
    if mode == "LOCKED":
        return {**result, "status": PASS, "mode": mode, "fix": None}
    return {**result, "status": BLOCKED, "mode": mode,
            "fix": "apply the proposed lock patch (python3 episode_coordinator.py "
                   f"apply-lock-patch {lock['story_id']}) -- prompts already compile under it"}


# --- stage 3: generation handoff ----------------------------------------------------------
def _asset_path(lock: dict, slot: int, assets_root: Path | None) -> Path:
    return sc.candidate_path(int(lock["item_index"]), slot, assets_root)


def stage_generation_handoff(lock: dict, prompts: dict, out: Path, *,
                             assets_root: Path | None, write: bool) -> dict:
    gen = lock.get("generation") or {}
    if gen.get("paid_generation_authorized"):
        return {"status": BLOCKED, "fix": "paid generation is never routed by this coordinator"}
    if prompts["status"] != PASS:
        return {"status": WAITING_ON_UPSTREAM, "handed_off": [], "fix": None,
                "detail": "no locked prompt exists yet -- nothing may be handed to an executor"}
    state = _active_state(lock, assets_root)
    handed, present, waiting = [], [], []
    for entry in prompts["slides"]:
        n = entry["slide_index"]
        asset = _asset_path(lock, n, assets_root)
        if asset.is_file():
            present.append(n)
            continue
        if entry["gate"] != "HANDOFF_READY":
            waiting.append(n)
            continue
        record = sc.plan_record(lock, n)
        packet = {
            "story_id": lock["story_id"], "item_index": lock["item_index"], "slide_index": n,
            "mode": MODE, "executor": "human generation executor (founder) or a separate "
                                      "automation -- never the Claude PS5 session",
            "intended_provider": gen.get("intended_provider"),
            "prompt_pack": entry["prompt_pack"], "prompt_sha256": entry["prompt_sha256"],
            "references": sc.reference_selection({}, state, record, index=int(lock["item_index"]),
                                                 root=assets_root),
            "output": {**OUTPUT, "expected_path": _rel(asset)},
            "text_in_image": "none -- overlay copy is burned in afterwards by PS-05",
            "report_back": "save the image at output.expected_path; the next coordinator run "
                           "detects it and queues visual QA -- nothing else to report",
            "ps5_forbidden": list(PS5_FORBIDDEN),
            "cost": {"incremental_cost_usd": 0, "paid_providers_prohibited": True},
        }
        path = out / "handoff" / f"slide{n}.json"
        if write:
            _write_json(path, packet)
        handed.append({"slide_index": n, "packet": _rel(path)})
    status = (WAITING_ON_EXECUTOR if handed else
              PASS if len(present) == len(prompts["slides"]) else WAITING_ON_UPSTREAM)
    return {"status": status, "handed_off": handed, "assets_present": present,
            "gated_on_previous_approval": waiting,
            "generation_enabled_in_lock": bool(gen.get("enabled")),
            "fix": None}


# --- stage 4: visual QA -----------------------------------------------------------------------
def _dimensions(path: Path) -> str | None:
    """WxH from the file itself: the PNG header directly (no dependency), any
    other format through Pillow when it is installed, else unknown."""
    head = path.read_bytes()[:24]
    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        return f"{struct.unpack('>I', head[16:20])[0]}x{struct.unpack('>I', head[20:24])[0]}"
    try:
        from PIL import Image
    except ImportError:                                  # pragma: no cover - environment
        return None
    with Image.open(path) as img:
        return f"{img.width}x{img.height}"


def _placeholder_png(path: Path, width: int = OUTPUT["width"], height: int = OUTPUT["height"]) -> None:
    """A flat grey PNG at the real output size -- a rehearsal stand-in for an
    executor's image, so the QA/repair/variant code paths run without any
    generation. Only ever written inside a temporary directory."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + b"\x80" * (width * 3) for _ in range(height))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def stage_visual_qa(lock: dict, *, assets_root: Path | None) -> dict:
    state = sc.load_state(lock, int(lock["item_index"]), root=assets_root)
    active = sc.project_active_context(state, lock, root=assets_root)
    approved = {r["slide_index"] for r in active.get("approved_slide_refs") or []}
    rejected = {}
    for r in (state.get("retry_state") or {}).get("rejected") or []:
        rejected[r["slide_index"]] = r                   # latest rejection wins
    slides = []
    for record in sc.story_plan(lock):
        n = record["slide_index"]
        asset = _asset_path(lock, n, assets_root)
        entry = {"slide_index": n, "asset": _rel(asset), "asset_present": asset.is_file()}
        if n in approved:
            entry["state"] = "APPROVED"
        elif not asset.is_file():
            entry["state"] = "NO_ASSET"
        elif n in rejected:
            entry["state"] = "REJECTED"
            entry["rejection"] = rejected[n]
        else:
            entry["state"] = sc.PENDING_VISUAL_QA
        if asset.is_file():
            dims = _dimensions(asset)
            entry["dimensions"] = dims
            entry["technical_gate"] = (None if dims is None else
                                       dims == f"{OUTPUT['width']}x{OUTPUT['height']}")
        slides.append(entry)
    states = [s["state"] for s in slides]
    if all(s == "APPROVED" for s in states):
        status = PASS
    elif sc.PENDING_VISUAL_QA in states:
        status = WAITING_ON_VISUAL_QA
    elif "REJECTED" in states:
        status = WAITING_ON_EXECUTOR                     # repair stage owns what happens next
    elif any(s["asset_present"] for s in slides):
        status = WAITING_ON_EXECUTOR
    else:
        status = WAITING_ON_UPSTREAM
    return {"status": status, "reviewer": sc.visual_qa_provider(lock), "slides": slides,
            "record_with": f"python3 episode_coordinator.py qa {lock['story_id']} <slot> <scores.json>",
            "fix": None}


# --- stage 5: targeted repair -----------------------------------------------------------------
def repair_prompt(lock: dict, record: dict, base_prompt: str, failed: list[str], notes: str,
                  attempt: int, max_attempts: int) -> str:
    wardrobe = lock.get("wardrobe_lock") or {}
    props = "; ".join(f"{k}: {v}" for k, v in
                      ((record.get("state_after") or {}).get("props") or {}).items()) or "as planned"
    camera = "; ".join(f"{k}: {v}" for k, v in (record.get("camera") or {}).items() if v)
    fill = {"outfit": wardrobe.get("outfit", ""), "hair": wardrobe.get("hair", ""),
            "props": props, "camera": camera or record.get("composition", ""),
            "new_information_revealed": record.get("new_information_revealed")
            or record.get("story_beat", ""), **{k: OUTPUT[k] for k in ("width", "height")}}
    lines = [f"TARGETED REPAIR -- attempt {attempt} of {max_attempts} for slide "
             f"{record['slide_index']}. Keep EVERYTHING else identical to the locked prompt "
             f"above; change only what is named here:"]
    for dim in failed:
        lines.append(f"- {dim}: " + REPAIR_DIRECTIVES.get(dim, "fix this dimension").format(**fill))
    if notes:
        lines.append(f"Reviewer notes on the rejected attempt: {notes}")
    return base_prompt.rstrip() + "\n\n" + "\n".join(lines) + "\n"


def stage_targeted_repair(lock: dict, prompts: dict, qa: dict, out: Path, *,
                          assets_root: Path | None, write: bool) -> dict:
    state = sc.load_state(lock, int(lock["item_index"]), root=assets_root)
    retry = state.get("retry_state") or {}
    max_attempts = int(retry.get("max_attempts_per_slide")
                       or (lock.get("retry") or {}).get("max_attempts_per_slide") or 2)
    rejected = [s for s in qa["slides"] if s["state"] == "REJECTED"]
    if not rejected:
        any_qa = any(s["state"] == "APPROVED" for s in qa["slides"])
        return {"status": PASS if any_qa else WAITING_ON_UPSTREAM, "issued": [], "named_gaps": [],
                "max_attempts_per_slide": max_attempts, "fix": None,
                "detail": "no rejected slide" if any_qa else "no QA verdict yet -- nothing to repair"}
    packs = {s["slide_index"]: s for s in prompts.get("slides") or []}
    issued, gaps = [], []
    for s in rejected:
        n = s["slide_index"]
        used = int((retry.get("attempts") or {}).get(str(n), 0))
        if used >= max_attempts:
            gaps.append({"slide_index": n, "attempts_used": used,
                         "failed": s["rejection"].get("failed"),
                         "state": "needs_human_action",
                         "next": "simplify, re-sequence or drop this frame -- a founder decision"})
            continue
        pack = packs.get(n)
        base = (ROOT / pack["prompt_pack"]).read_text() if pack and (ROOT / pack["prompt_pack"]).is_file() else ""
        text = repair_prompt(lock, sc.plan_record(lock, n), base, s["rejection"].get("failed") or [],
                             s["rejection"].get("notes", ""), used + 1, max_attempts)
        path = out / "repair" / f"slide{n}_attempt{used + 1}.txt"
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        issued.append({"slide_index": n, "attempt": used + 1, "repair_prompt": _rel(path),
                       "failed": s["rejection"].get("failed")})
    status = BLOCKED if gaps else WAITING_ON_EXECUTOR
    return {"status": status, "issued": issued, "named_gaps": gaps,
            "max_attempts_per_slide": max_attempts,
            "fix": (f"slides {[g['slide_index'] for g in gaps]} exhausted their attempts"
                    if gaps else None)}


# --- stage 6: captions ------------------------------------------------------------------------
def stage_captions(lock: dict, out: Path) -> dict:
    wanted = platforms_for(lock)
    path = out / "captions.json"
    if not path.is_file():
        return {"status": BLOCKED, "platforms": list(wanted),
                "fix": f"write {_rel(path)} with one caption per platform {list(wanted)}"}
    data = json.loads(path.read_text())
    caps = data.get("captions") or {}
    problems, per = [], {}
    for p in wanted:
        text = str(caps.get(p) or "").strip()
        issues = []
        if not text:
            issues.append("missing")
        else:
            if len(text) > CAPTION_LIMITS.get(p, 2200):
                issues.append(f"{len(text)} chars > {CAPTION_LIMITS.get(p)}")
            if stages._ENGAGEMENT_BAIT.search(text):
                issues.append("engagement-bait phrasing")
            if p not in NATIVE_AI_LABEL and not _DISCLOSURE.search(text):
                issues.append("no AI disclosure and this route has no native AI label")
        per[p] = {"chars": len(text), "issues": issues,
                  "disclosure": "platform AI label" if p in NATIVE_AI_LABEL else "in caption"}
        problems += [f"{p}: {i}" for i in issues]
    item = {channels.variant_field(p): caps.get(p, "") for p in wanted if p in channels.CHANNELS}
    problems += channels.variant_problems(item)
    payoff = (sc.story_plan(lock) or [{}])[-1].get("overlay_copy", "")
    if payoff.strip().endswith("?") and "?" not in str(caps.get(wanted[0]) or ""):
        problems.append(f"{wanted[0]}: the episode ends on a choice but the caption never asks it")
    return {"status": BLOCKED if problems else PASS, "file": _rel(path),
            "draft_status": data.get("status", "DRAFT"), "platforms": per, "problems": problems,
            "fix": "; ".join(problems) if problems else None}


# --- stage 7: platform variants ---------------------------------------------------------------
def stage_platform_variants(lock: dict, qa: dict, captions: dict, out: Path, *,
                            assets_root: Path | None, write: bool) -> dict:
    index = int(lock["item_index"])
    pkg = carousel_handoff.package_dir(index, assets_root)
    frames = []
    for record in sc.story_plan(lock):
        n, copy_line = record["slide_index"], record["overlay_copy"].strip()
        frames.append({
            "slide_index": n, "clean_source": _rel(pkg / carousel_handoff.slide_filename(index, n)),
            "overlay": copy_line or None,
            "overlay_position": carousel_handoff.overlay_position(record["overlay_safe_zone"])
            if copy_line else None,
            "final": _rel(pkg / (carousel_handoff.overlay_filename(index, n) if copy_line
                                 else carousel_handoff.slide_filename(index, n)))})
    art_ready = qa["status"] == PASS
    variants = {}
    for p in platforms_for(lock):
        ch = channels.CHANNELS.get(p, {})
        spec = {"platform": p, "route_status": ch.get("status", channels.NOT_AVAILABLE),
                "route": ch.get("route"), "publishes": bool(ch.get("publishes")),
                "native_form": ch.get("native_form"), "caption_ok": captions["status"] == PASS,
                "ai_disclosure": "platform AI label" if p in NATIVE_AI_LABEL else "in caption"}
        if p == "instagram":
            spec.update(form="native multi-image carousel", size=OUTPUT, frames=frames)
        elif p == "tiktok":
            spec.update(form="slideshow video (TikTok photo posts cannot carry the AI label via "
                             "Metricool)", canvas="1080x1920, 4:5 frames padded, never cropped",
                        seconds_per_frame=3, frames=[f["final"] for f in frames],
                        render="local ffmpeg, zero cost -- rendered only once every frame is approved")
        elif p == "fanvue":
            spec.update(form="continuation chapter -- never the identical public slides")
        else:
            spec.update(form=ch.get("native_form"), images=[f["final"] for f in frames],
                        blocker=ch.get("blocker"))
        if not spec["publishes"]:
            spec["state"] = "PREPARED_NOT_PUBLISHABLE"
        elif not art_ready:
            spec["state"] = "WAITING_ON_APPROVED_ART"
        elif not spec["caption_ok"]:
            spec["state"] = "WAITING_ON_CAPTION"
        else:
            spec["state"] = "READY_TO_RENDER"
        variants[p] = spec
        if write:
            _write_json(out / "variants" / f"{p}.json", spec)
    status = (PASS if all(v["state"] in ("READY_TO_RENDER", "PREPARED_NOT_PUBLISHABLE")
                          for v in variants.values()) else WAITING_ON_UPSTREAM)
    return {"status": status,
            "platforms": {p: {"state": v["state"], "route_status": v["route_status"]}
                          for p, v in variants.items()},
            "fix": None}


# --- stage 8: publish gate --------------------------------------------------------------------
def stage_publish_gate(lock: dict, qa: dict, captions: dict, variants: dict,
                       venture_path: Path | None = None) -> dict:
    venture_path = venture_path or ROOT / "state" / "venture.json"
    item_present = False
    if venture_path.is_file():
        items = json.loads(venture_path.read_text()).get("content_plan", {}).get("content_items", [])
        item_present = 0 <= int(lock["item_index"]) < len(items)
    live = [p for p, v in variants["platforms"].items() if v["route_status"] == channels.LIVE]
    prereqs = [
        {"check": "every slide APPROVED in visual QA", "met": qa["status"] == PASS},
        {"check": "captions validated", "met": captions["status"] == PASS},
        {"check": f"state/venture.json carries content_items[{lock['item_index']}] "
                  "(the publisher reads caption/media from it)", "met": item_present},
        {"check": "at least one LIVE route", "met": bool(live), "live_routes": live},
    ]
    return {"status": NOT_EXECUTED_DRY_RUN, "live_prerequisites": prereqs,
            "publish_executor": "a human or separate automation via nyx_instagram.py -- never "
                                "this coordinator", "fix": None}


# --- the run ----------------------------------------------------------------------------------
def smallest_blocker(stage_results: dict) -> dict | None:
    """The first stage that is actually BLOCKED (something is wrong and has a
    concrete fix); failing that, the first stage waiting on someone else."""
    for wanted in (BLOCKED, WAITING_ON_EXECUTOR, WAITING_ON_VISUAL_QA):
        for name in STAGES:
            r = stage_results.get(name) or {}
            if r.get("status") == wanted:
                return {"stage": name, "status": wanted,
                        "fix": r.get("fix") or r.get("detail") or r.get("record_with")}
    return None


def rehearse(lock: dict, lock_sha: str, out: Path, *, venture_path: Path | None = None) -> dict:
    """Exercise the stages the real state cannot reach yet (QA, repair,
    variants) on the SAME plan, inside a throwaway directory: flat placeholder
    PNGs stand in for the executor's images and SIMULATED verdicts stand in for
    the reviewer. Nothing is generated, nothing leaves the temp dir, and no real
    story state, asset or ledger is touched. It proves the code path, never the
    art -- a rehearsal verdict is not a QA result."""
    if sc.plan_problems(lock):
        return {"ran": False, "reason": "plan is not compilable, even under the proposed patch"}
    index = int(lock["item_index"])
    steps = []
    with tempfile.TemporaryDirectory(prefix="nyx_rehearsal_") as tmp:
        assets, eps = Path(tmp) / "assets", Path(tmp) / "episodes"
        prompts = stage_locked_prompts(lock, lock_sha, eps, patch=None, assets_root=assets, write=True)
        handoff = stage_generation_handoff(lock, prompts, eps, assets_root=assets, write=True)
        steps.append({"step": "locked prompts + slide-1 handoff", "prompts": prompts["status"],
                      "handoff": handoff["status"],
                      "handed_off": [h["slide_index"] for h in handoff["handed_off"]],
                      "gated_on_previous_approval": handoff["gated_on_previous_approval"]})
        all_pass = {d: True for d in sc.QA_DIMENSIONS}
        state = sc.ensure_state(lock, index, root=assets)
        _placeholder_png(sc.candidate_path(index, 1, assets))
        sc.qa_slide(lock, state, sc.plan_record(lock, 1), index=index, root=assets,
                    scores={**all_pass, "canonical_ears_visible": False,
                            "wardrobe_hair_continuity": False},
                    notes="SIMULATED: human ear visible on the left; earring present")
        qa = stage_visual_qa(lock, assets_root=assets)
        repair = stage_targeted_repair(lock, prompts, qa, eps, assets_root=assets, write=True)
        excerpt = ""
        if repair["issued"]:
            text = Path(repair["issued"][0]["repair_prompt"]).read_text()
            excerpt = text[text.index("TARGETED REPAIR"):].strip()
        steps.append({"step": "slide 1 simulated REJECT -> targeted repair",
                      "visual_qa": qa["status"], "repair": repair["status"],
                      "repair_attempt": [(r["slide_index"], r["attempt"]) for r in repair["issued"]],
                      "repair_section": excerpt})
        for record in sc.story_plan(lock):
            n = record["slide_index"]
            _placeholder_png(sc.candidate_path(index, n, assets))
            state = sc.ensure_state(lock, index, root=assets)
            outcome = sc.qa_slide(lock, state, record, index=index, scores=dict(all_pass),
                                  notes="SIMULATED pass (rehearsal only)", root=assets)
            if not outcome["approved"]:
                steps.append({"step": f"slide {n} simulated approve", "result": outcome["status"],
                              "error": outcome.get("error")})
                break
        qa = stage_visual_qa(lock, assets_root=assets)
        captions = stage_captions(lock, out)
        variants = stage_platform_variants(lock, qa, captions, eps, assets_root=assets, write=False)
        gate = stage_publish_gate(lock, qa, captions, variants, venture_path)
        steps.append({"step": "all slides simulated APPROVE -> captions -> variants -> gate",
                      "visual_qa": qa["status"], "captions": captions["status"],
                      "variants": variants["platforms"], "publish_gate": gate["status"],
                      "technical_gate": [s.get("technical_gate") for s in qa["slides"]]})
    return {"ran": True, "simulated": True, "sandbox": "temporary directory, deleted after the run",
            "steps": steps}


def _canon_hashes(lock_file: Path, season_id: str | None, seasons_dir: Path | None) -> dict:
    season_file = (seasons_dir or SEASONS_DIR) / f"{season_id}.json" if season_id else None
    return {"lock_sha256": _sha256_file(lock_file),
            "identity_spec_sha256": _sha256_file(IDENTITY_SPEC),
            "season_file_sha256": _sha256_file(season_file) if season_file else None}


def dry_run(story_id: str, *, episodes_dir: Path | None = None, assets_root: Path | None = None,
            locks_dir: Path | None = None, seasons_dir: Path | None = None,
            venture_path: Path | None = None, write: bool = True) -> dict:
    lock_file = lock_path(story_id, locks_dir)
    lock = load_lock(story_id, locks_dir)
    before = _canon_hashes(lock_file, lock.get("season_id"), seasons_dir)
    season = load_season(lock.get("season_id"), seasons_dir)
    out = episode_dir(story_id, episodes_dir)
    patch_file = out / "proposed_lock_patch.json"
    patch = json.loads(patch_file.read_text()) if patch_file.is_file() else None
    patched = None
    if patch:
        try:
            patched = merge_lock_patch(lock, patch)[0]
        except CoordinatorError:
            patched = None                               # reported by stage_locked_prompts

    results = {}
    results["brief"] = stage_brief(lock, season, locks_dir=locks_dir, patched=patched)
    results["locked_prompts"] = stage_locked_prompts(lock, before["lock_sha256"], out, patch=patch,
                                                     assets_root=assets_root, write=write)
    results["generation_handoff"] = stage_generation_handoff(lock, results["locked_prompts"], out,
                                                             assets_root=assets_root, write=write)
    results["visual_qa"] = stage_visual_qa(lock, assets_root=assets_root)
    results["targeted_repair"] = stage_targeted_repair(lock, results["locked_prompts"],
                                                       results["visual_qa"], out,
                                                       assets_root=assets_root, write=write)
    results["captions"] = stage_captions(lock, out)
    results["platform_variants"] = stage_platform_variants(lock, results["visual_qa"],
                                                           results["captions"], out,
                                                           assets_root=assets_root, write=write)
    results["publish_gate"] = stage_publish_gate(lock, results["visual_qa"], results["captions"],
                                                 results["platform_variants"], venture_path)

    rehearsal = rehearse(patched if results["locked_prompts"]["mode"] != "LOCKED" and patched
                         else lock, before["lock_sha256"], out, venture_path=venture_path)

    after = _canon_hashes(lock_file, lock.get("season_id"), seasons_dir)
    if after != before:                                  # pragma: no cover - structural
        raise CoordinatorError("canon changed during a dry run -- this must never happen")
    ledger_path = out / "run_ledger.json"
    history = []
    if ledger_path.is_file():
        history = json.loads(ledger_path.read_text()).get("runs", [])
    snapshot = {
        "story_id": story_id, "item_index": lock.get("item_index"),
        "episode_id": lock.get("episode_id"), "mode": MODE, "run_at": _now(),
        "lock_file": _rel(lock_file), "canon": before, "canon_preserved": True,
        "side_effects": {"browser": False, "uploads": False, "generation": False,
                         "publications": False, "spend_usd": 0, "spend_credits": 0},
        "stage_status": {n: results[n]["status"] for n in STAGES},
        "smallest_blocker": smallest_blocker(results),
        "stages": results,
        "rehearsal": rehearsal,
    }
    history = (history + [{k: snapshot[k] for k in ("run_at", "mode", "canon", "stage_status",
                                                    "smallest_blocker")}])[-20:]
    ledger = {**snapshot, "runs": history}
    if write:
        _write_json(ledger_path, ledger)
        (out / "STATUS.md").write_text(render_status(ledger))
    return ledger


def render_status(ledger: dict) -> str:
    lines = [f"# {ledger['story_id']} -- coordinator status", "",
             f"Mode: **{ledger['mode']}** (no browser, no uploads, no generation, no publishing, "
             f"$0). Last run: {ledger['run_at']}. Canon preserved: {ledger['canon_preserved']}.",
             f"Lock: `{ledger['lock_file']}` sha256 `{ledger['canon']['lock_sha256'][:12]}`", "",
             "| Stage | Status |", "|---|---|"]
    lines += [f"| {n} | {s} |" for n, s in ledger["stage_status"].items()]
    b = ledger.get("smallest_blocker")
    lines += ["", "## Smallest blocker to a live run", ""]
    lines += ([f"**{b['stage']}** ({b['status']}): {b['fix']}"] if b else ["None."])
    brief = ledger["stages"]["brief"]
    if brief["warnings"]:
        lines += ["", "## Canon findings (recorded, not changed)", ""]
        lines += [f"- {w}" for w in brief["warnings"]]
    gate = ledger["stages"]["publish_gate"]["live_prerequisites"]
    lines += ["", "## Live-run prerequisites", ""]
    lines += [f"- [{'x' if c['met'] else ' '}] {c['check']}" for c in gate]
    reh = ledger.get("rehearsal") or {}
    lines += ["", "## Rehearsal (SIMULATED, temp dir, proves code paths not art)", ""]
    if not reh.get("ran"):
        lines.append(f"Not run: {reh.get('reason')}")
    for step in reh.get("steps") or []:
        detail = {k: v for k, v in step.items() if k not in ("step", "repair_section")}
        lines.append(f"- {step['step']}: `{json.dumps(detail, ensure_ascii=False)}`")
    return "\n".join(lines) + "\n"


# --- commands that record or apply (never generate or publish) ---------------------------------
def record_qa(story_id: str, slot: int, scores: dict, *, locks_dir: Path | None = None,
              assets_root: Path | None = None) -> dict:
    """A visual-QA verdict on an asset that is ALREADY on disk, through the one
    canonical approval path (story_continuity.qa_slide). PS5 judges; it never
    produces the image it is judging."""
    lock = load_lock(story_id, locks_dir)
    index = int(lock["item_index"])
    record = sc.plan_record(lock, slot)
    state = sc.ensure_state(lock, index, root=assets_root)
    notes = str(scores.pop("notes", "") or "")
    return sc.qa_slide(lock, state, record, index=index, scores=scores, notes=notes,
                       root=assets_root)


def apply_lock_patch(story_id: str, *, locks_dir: Path | None = None,
                     episodes_dir: Path | None = None, by: str = "episode_coordinator") -> dict:
    """Write the proposed patch into the lock -- additive only (merge_lock_patch
    refuses to overwrite anything), with a canon_revisions entry so the change
    is on the record. Never run automatically."""
    path = lock_path(story_id, locks_dir)
    lock = load_lock(story_id, locks_dir)
    patch_file = episode_dir(story_id, episodes_dir) / "proposed_lock_patch.json"
    if not patch_file.is_file():
        raise CoordinatorError(f"no proposed patch at {patch_file}")
    merged, filled = merge_lock_patch(lock, json.loads(patch_file.read_text()))
    problems = sc.plan_problems(merged)
    if problems:
        raise CoordinatorError("patched plan still not compilable: " + "; ".join(problems))
    merged.setdefault("canon_revisions", []).append({
        "at": _now(), "by": by, "change": f"additive planning fields ({len(filled)})",
        "why": "story_continuity.plan_problems requires them before any prompt compiles; no "
               "approved beat, composition or overlay line was changed"})
    path.write_text(json.dumps(merged, indent=1, ensure_ascii=False) + "\n")
    return {"ok": True, "lock_file": _rel(path), "fields_filled": filled}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "dry-run":
            ledger = dry_run(rest[0])
            out = {k: ledger[k] for k in ("story_id", "mode", "canon_preserved", "side_effects",
                                          "stage_status", "smallest_blocker")}
        elif cmd == "status":
            path = episode_dir(rest[0]) / "run_ledger.json"
            ledger = json.loads(path.read_text())
            out = {k: ledger[k] for k in ("story_id", "run_at", "stage_status", "smallest_blocker")}
        elif cmd == "qa":
            out = record_qa(rest[0], int(rest[1]), json.loads(Path(rest[2]).read_text()))
        elif cmd == "apply-lock-patch":
            out = apply_lock_patch(rest[0])
        else:
            print(f"unknown command: {cmd}\n\n{__doc__}")
            return 1
    except (CoordinatorError, sc.ContinuityError, FileNotFoundError, IndexError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
