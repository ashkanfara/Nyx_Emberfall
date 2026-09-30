"""Durable Nyx production contract (founder authorization 2026-09-30).

This module is the structural enforcement of a strict separation of duties
between three roles in the Nyx content pipeline, after a session in which
repeated browser/upload denials made clear that role should never have been
blurred in the first place:

  - CLAUDE PS5 (this codebase's automated agent role): may only (1) author a
    story pack (a brand/story_locks/*.json lock file -- unchanged, existing
    mechanism), (2) author a prompt pack (the literal, final text to paste
    into an image generator -- pure text, no tool control), and (3) render a
    QA decision on an asset that already exists on disk (a read + judgement,
    never a generation). Claude PS5 must NEVER drive a browser, upload a
    reference image to any external tool, call an image-generation API,
    publish to any platform, change any permission/settings, or spend money.
    Nothing in this module gives Claude PS5 a way to do any of those things --
    there is no browser call, no upload call, no publish call anywhere below.

  - GENERATION EXECUTOR: a human (the founder) or a distinct, explicitly
    separate automation -- never this Claude PS5 session -- that takes a
    prompt pack this module already wrote to disk, pastes/feeds it into the
    already-prepared image-generation conversation, and reports back the
    resulting asset's file path. This module never calls out to fetch that
    asset itself; it only records that the executor said an asset now exists
    at a given path (record_generated), then hands the state back to PS5 for
    a QA decision.

  - PUBLISH EXECUTOR: a human or a distinct automation -- never this Claude
    PS5 session -- that takes only qa_passed assets forward to a live
    platform and reports back the resulting live URL/post id. This module
    never calls a publish API itself; record_published is a pure bookkeeping
    call made AFTER a real publish has already happened elsewhere.

Episode/slide lifecycle (a directed, mostly-linear state machine):

    planned -> prompted -> generated -> qa_passed -> ready_to_publish -> published
                              \\-> qa_failed -> prompted (re-prompt) or needs_human_action

    needs_human_action is reachable from ANY state whenever a tool or
    permission failure occurs (record_handoff). It is a dead end for
    automation: nothing in this module ever auto-retries out of it. A human
    must explicitly call reset_from_handoff() after resolving the underlying
    cause, which moves the slide back to the state it names, with the
    handoff entry kept (not deleted) in history for audit.

Manifests are plain JSON on disk, one per content item, at
generated_assets/carousel_item<N>/episode_manifest.json -- append-only
history, current state easy to read, no hidden state anywhere else.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

EPISODE_STATES = (
    "planned",
    "prompted",
    "generated",
    "qa_passed",
    "qa_failed",
    "ready_to_publish",
    "published",
    "needs_human_action",
)

# Legal forward transitions. needs_human_action is reachable from every state
# (encoded separately in record_handoff, not listed per-row below) and is only
# ever exited via reset_from_handoff(), never via advance().
_TRANSITIONS = {
    "planned": {"prompted"},
    "prompted": {"generated"},
    "generated": {"qa_passed", "qa_failed"},
    "qa_failed": {"prompted"},          # re-prompted for another generation pass
    "qa_passed": {"ready_to_publish"},
    "ready_to_publish": {"published"},
    "published": set(),                 # terminal
    "needs_human_action": set(),        # terminal until reset_from_handoff()
}

QA_DIMENSIONS = (
    "identity", "environment_continuity", "wardrobe_hair_continuity",
    "prop_continuity", "anatomy_correctness", "canonical_ears_visible",
    "prop_multiplicity", "sequence_consistency", "story_beat",
    "composition_novelty", "content_policy", "technical_output",
)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def manifest_path(item_index: int) -> Path:
    return ROOT / "generated_assets" / f"carousel_item{item_index}" / "episode_manifest.json"


def _load(item_index: int) -> dict:
    p = manifest_path(item_index)
    if not p.exists():
        raise FileNotFoundError(
            f"no manifest for item {item_index} -- call init_manifest() first"
        )
    return json.loads(p.read_text())


def _save(item_index: int, manifest: dict) -> None:
    p = manifest_path(item_index)
    p.parent.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = _now()
    p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def _find_slide(manifest: dict, slide_index: int) -> dict:
    for s in manifest["slides"]:
        if s["slide_index"] == slide_index:
            return s
    raise KeyError(f"slide {slide_index} not in manifest for item {manifest['item_index']}")


def _append_history(slide: dict, event: str, **fields) -> None:
    slide.setdefault("history", []).append({"at": _now(), "event": event, **fields})


# ---------------------------------------------------------------------------
# Claude-PS5-permitted actions
# ---------------------------------------------------------------------------

def init_manifest(item_index: int, episode_id: str, story_thread: str,
                   target_platform: str, slide_indices: list[int],
                   lock_file: str) -> dict:
    """Create (or return, idempotently) the manifest for an item, one entry
    per slide, all starting 'planned'. This is a story-pack-adjacent action
    (bookkeeping over an already-written lock file) -- no generation, no
    browser, nothing external."""
    p = manifest_path(item_index)
    if p.exists():
        return _load(item_index)
    manifest = {
        "item_index": item_index,
        "episode_id": episode_id,
        "story_thread": story_thread,
        "target_platform": target_platform,
        "lock_file": lock_file,
        "created_at": _now(),
        "slides": [
            {"slide_index": i, "state": "planned", "prompt_pack_path": None,
             "asset_path": None, "qa": None, "handoff": None, "history": [
                 {"at": _now(), "event": "planned", "note": "story pack exists in lock_file"}
             ]}
            for i in slide_indices
        ],
    }
    _save(item_index, manifest)
    return manifest


def create_prompt_pack(item_index: int, slide_index: int, prompt_text: str) -> dict:
    """Claude-PS5 action: write the final, literal generation prompt to disk
    as its own file and advance planned -> prompted. Pure text write, no
    tool/browser/API call of any kind."""
    manifest = _load(item_index)
    slide = _find_slide(manifest, slide_index)
    if slide["state"] not in _TRANSITIONS and slide["state"] != "planned":
        pass  # fall through to the explicit check below for a clear error
    if "prompted" not in _TRANSITIONS.get(slide["state"], set()) and slide["state"] != "qa_failed":
        raise ValueError(
            f"item {item_index} slide {slide_index} is '{slide['state']}', "
            f"cannot create a prompt pack from that state"
        )
    pack_dir = ROOT / "generated_assets" / f"carousel_item{item_index}" / "prompt_packs"
    pack_dir.mkdir(parents=True, exist_ok=True)
    pack_path = pack_dir / f"slide{slide_index}_prompt.txt"
    pack_path.write_text(prompt_text)
    slide["prompt_pack_path"] = str(pack_path)
    slide["state"] = "prompted"
    _append_history(slide, "prompted", prompt_pack_path=str(pack_path))
    _save(item_index, manifest)
    return manifest


def room_gate(manifest: dict, slide_index: int) -> dict:
    """The canonical room-reference gate for this manifest's story lock. A
    room-continuity PASS is only recordable against a registered room image
    (or inside the room's establishing story); see room_references.py."""
    import room_references

    try:
        lock = json.loads((ROOT / manifest["lock_file"]).read_text())
    except (KeyError, OSError, ValueError):
        return {"allowed": False, "status": "BLOCKED_NO_LOCK",
                "reason": f"lock_file {manifest.get('lock_file')!r} unreadable -- no room to check against"}
    return room_references.gate_for_lock(lock, slide_index)


def qa_decide(item_index: int, slide_index: int, scores: dict[str, str],
              notes: str = "") -> dict:
    """Claude-PS5 action: render a QA judgement on an asset that the
    generation executor already reported as present on disk (state must be
    'generated'). This function only READS the asset path it's given and
    records a verdict -- it never creates or fetches the asset itself."""
    manifest = _load(item_index)
    slide = _find_slide(manifest, slide_index)
    if slide["state"] != "generated":
        raise ValueError(
            f"item {item_index} slide {slide_index} is '{slide['state']}', "
            f"a QA decision requires state 'generated' (an asset must already "
            f"be recorded via record_generated())"
        )
    missing = [d for d in QA_DIMENSIONS if d not in scores]
    if missing:
        raise ValueError(f"scores missing dimensions: {missing}")
    failed = [d for d, v in scores.items() if not str(v).upper().startswith("PASS")]
    if "environment_continuity" not in failed:
        room = room_gate(manifest, slide_index)
        if not room["allowed"]:
            raise ValueError(f"environment_continuity PASS refused for item {item_index} slide "
                             f"{slide_index}: {room['reason']} ({room['status']}). Register the room "
                             f"reference first, or score environment_continuity as "
                             f"'BLOCKED: no room reference' to record the slide as qa_failed")
    verdict = "qa_failed" if failed else "qa_passed"
    slide["qa"] = {"scores": scores, "notes": notes, "verdict": verdict, "checked_at": _now(),
                    "failed_dimensions": failed}
    slide["state"] = verdict
    _append_history(slide, verdict, failed_dimensions=failed, notes=notes)
    _save(item_index, manifest)
    return manifest


# ---------------------------------------------------------------------------
# Generation-executor-permitted action (human or a distinct automation calls
# this -- Claude PS5 may run this CLI command ONLY when told by the human
# that an asset already exists at the given path; PS5 itself never produces
# that path by controlling a browser or calling a generation API)
# ---------------------------------------------------------------------------

def record_generated(item_index: int, slide_index: int, asset_path: str,
                      generated_by: str = "human_generation_executor") -> dict:
    manifest = _load(item_index)
    slide = _find_slide(manifest, slide_index)
    if slide["state"] not in ("prompted", "qa_failed"):
        raise ValueError(
            f"item {item_index} slide {slide_index} is '{slide['state']}', "
            f"expected 'prompted' (or 're-prompted after qa_failed') before recording a generated asset"
        )
    if not Path(asset_path).exists():
        raise FileNotFoundError(f"asset_path does not exist: {asset_path}")
    slide["asset_path"] = asset_path
    slide["state"] = "generated"
    _append_history(slide, "generated", asset_path=asset_path, generated_by=generated_by)
    _save(item_index, manifest)
    return manifest


# ---------------------------------------------------------------------------
# Publish-executor-permitted action (human or a distinct automation calls
# this AFTER a real, independently-verified publish -- never Claude PS5)
# ---------------------------------------------------------------------------

def mark_ready_to_publish(item_index: int) -> dict:
    """Advances the ITEM (not a single slide) to ready_to_publish once every
    slide is qa_passed. Pure bookkeeping/aggregation -- callable by PS5 since
    it only reads existing qa verdicts, makes no new judgement, and takes no
    external action."""
    manifest = _load(item_index)
    not_passed = [s["slide_index"] for s in manifest["slides"] if s["state"] != "qa_passed"]
    if not_passed:
        raise ValueError(f"slides not yet qa_passed: {not_passed}")
    for s in manifest["slides"]:
        s["state"] = "ready_to_publish"
        _append_history(s, "ready_to_publish")
    manifest["item_state"] = "ready_to_publish"
    _save(item_index, manifest)
    return manifest


def record_published(item_index: int, urls: dict[str, str],
                      published_by: str = "human_publish_executor") -> dict:
    manifest = _load(item_index)
    if manifest.get("item_state") != "ready_to_publish":
        raise ValueError(
            f"item {item_index} is not ready_to_publish (call mark_ready_to_publish() first)"
        )
    for s in manifest["slides"]:
        s["state"] = "published"
        _append_history(s, "published")
    manifest["item_state"] = "published"
    manifest["published_urls"] = urls
    manifest["published_by"] = published_by
    manifest["published_at"] = _now()
    _save(item_index, manifest)
    return manifest


# ---------------------------------------------------------------------------
# Handoff on any tool/permission failure -- reachable from any state, never
# auto-retried, always explicit and human-resolved
# ---------------------------------------------------------------------------

def record_handoff(item_index: int, slide_index: int, stage: str, reason: str,
                    denied_action: str) -> dict:
    """Record that a tool/permission failure occurred at `stage` and park the
    slide in needs_human_action. This function performs NO retry of
    `denied_action` -- it only writes a record. The calling code must not
    call the denied action again in this or any later invocation; that
    discipline lives in the caller (and in this project's standing policy),
    this function just gives it a durable place to land."""
    manifest = _load(item_index)
    slide = _find_slide(manifest, slide_index)
    slide["handoff"] = {
        "stage": stage, "reason": reason, "denied_action": denied_action,
        "recorded_at": _now(), "prior_state": slide["state"],
    }
    slide["state"] = "needs_human_action"
    _append_history(slide, "needs_human_action", stage=stage, reason=reason,
                     denied_action=denied_action)
    _save(item_index, manifest)
    return manifest


def reset_from_handoff(item_index: int, slide_index: int, to_state: str,
                        resolution_note: str) -> dict:
    """Human-invoked only: explicitly move a slide out of needs_human_action
    once the underlying cause is actually resolved (e.g. a permission was
    granted, or the human did the blocked step themselves). The handoff
    record is kept in history, not erased."""
    if to_state not in EPISODE_STATES or to_state == "needs_human_action":
        raise ValueError(f"invalid to_state: {to_state}")
    manifest = _load(item_index)
    slide = _find_slide(manifest, slide_index)
    if slide["state"] != "needs_human_action":
        raise ValueError(f"slide {slide_index} is '{slide['state']}', not needs_human_action")
    slide["state"] = to_state
    _append_history(slide, "reset_from_handoff", to_state=to_state, note=resolution_note)
    _save(item_index, manifest)
    return manifest


# ---------------------------------------------------------------------------
# Status / next-action helpers -- "continue unrelated stages" support
# ---------------------------------------------------------------------------

def status(item_index: int) -> dict:
    manifest = _load(item_index)
    by_state: dict[str, list[int]] = {}
    for s in manifest["slides"]:
        by_state.setdefault(s["state"], []).append(s["slide_index"])
    return {
        "item_index": item_index, "episode_id": manifest["episode_id"],
        "item_state": manifest.get("item_state", "in_progress"),
        "by_state": by_state,
    }


def next_ps5_actions(item_index: int) -> list[dict]:
    """What Claude PS5 may legitimately do next for this item, skipping any
    slide parked in needs_human_action -- this is the 'continue unrelated
    stages' rule made concrete: a stuck slide never blocks the others."""
    manifest = _load(item_index)
    actions = []
    for s in manifest["slides"]:
        if s["state"] == "planned":
            actions.append({"slide_index": s["slide_index"], "action": "create_prompt_pack"})
        elif s["state"] == "generated":
            actions.append({"slide_index": s["slide_index"], "action": "qa_decide"})
        elif s["state"] == "qa_failed":
            actions.append({"slide_index": s["slide_index"], "action": "create_prompt_pack (re-prompt)"})
        # prompted / qa_passed / ready_to_publish / published / needs_human_action:
        # nothing for PS5 to do -- waiting on the generation or publish executor,
        # or on a human to resolve a handoff.
    return actions
