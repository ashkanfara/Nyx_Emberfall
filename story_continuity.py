"""Sequential story-image continuity orchestrator (founder direction 2026-09-23).

Why this exists: individual slides were already coming out good, but the STORY
drifted between them -- a regenerated slide 2 kept Nyx recognisable and quietly
redesigned her bedroom into a larger, more polished apartment. That is not an
identity problem (carousel_handoff already locks WHO she is, from
brand/nyx_identity/identity_spec.json); it is a missing ENVIRONMENT authority
and a missing STORY STATE. Handing the model "the previous slide" is exactly
what causes it: the previous image silently becomes the new definition of the
room.

So this module adds the two authorities the pipeline was missing, and keeps
them SEPARATE, rather than adding a second generation pipeline:

  IDENTITY_LOCK     immutable, cross-story  -> carousel_handoff / identity_spec.json
  ENVIRONMENT_LOCK  immutable, per-story    -> brand/story_locks/<story_id>.json,
                                               frozen by the first APPROVED slide
                                               and supplied on EVERY later slide
  STORY_STATE       mutable, per-story      -> what is currently happening;
                                               updated ONLY after an approval

It owns story planning, prompt compilation, composition memory, the QA/retry
gate and the (still disabled) generation request, so nobody -- founder or
model -- hand-writes a per-slide prompt. It fits the existing flow rather than
replacing it:

  brief (stages.story_carousel_problems)          unchanged
  -> story lock + plan validation (here)
  -> readiness()           FAIL CLOSED: slide N refuses until slide N-1 is approved
  -> compile_prompt()      identity > story facts > environment > state > prev slide > styling
  -> dry_run()             plan + prompt + references + QA expectations, NO image
  -> [generation, only once a story lock enables it -- see COST/PROVIDER below]
  -> qa_slide()            candidate -> authorized scorer -> approve or reject;
                           a file on disk is never an approval
  -> existing carousel_handoff ingestion, overlays, QA and Metricool publishing

COST/PROVIDER SAFETY: nothing here can generate or spend. It imports no
provider, no network client and no credential; `generation.enabled` in the
story lock is false, so every call is a dry run that emits the exact structured
plan and compiled prompt for inspection. Paid generation stays fail-closed in
stages.paid_generation_approved and is not touched by this module.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

import carousel_handoff
import render_masters
import room_references
import stages

SCHEMA_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parent
STORY_LOCKS_DIR = PROJECT_ROOT / "brand" / "story_locks"
STATE_FILENAME = "story_state.json"

# Prompt precedence, highest authority first. Every compiled prompt states it
# explicitly, and every layer below the environment master is sanitised against
# it (see _sanitise) -- a styling phrase can never redefine the room.
PRECEDENCE = ("canonical_identity", "explicit_story_facts", "environment_master",
              "approved_story_state", "previous_slide_visual_state", "creative_styling")
_LAYER_TITLES = {
    "canonical_identity": "1. CANONICAL IDENTITY (highest authority -- never overridden)",
    "explicit_story_facts": "2. EXPLICIT STORY FACTS FOR THIS SLIDE",
    "environment_master": "3. ENVIRONMENT MASTER (immutable for this story)",
    "approved_story_state": "4. APPROVED STORY STATE (what is currently true)",
    "previous_slide_visual_state": "5. PREVIOUS APPROVED SLIDE (action continuity only)",
    "creative_styling": "6. COMPOSITION AND STYLING (lowest authority)",
}
# Layers 4-6 describe what a prior image happened to look like or how this one
# should be framed; if any of them contradicts a layer above, the higher layer
# wins and the offending clause is dropped, not "balanced".
_SANITISED_LAYERS = ("approved_story_state", "previous_slide_visual_state", "creative_styling")

# The public-social default, as machine-readable rules. The prose source of
# truth for the creative direction is prompts.STORY_CAROUSEL_SPEC (PUBLIC
# VISUAL MIX / PUBLIC WARDROBE AND SEXUALITY); these lines are what actually
# reaches a provider.
PUBLIC_SOCIAL_POLICY = (
    "believable smartphone/iPhone-style photography, as if she took it herself",
    "personal and homemade: slightly imperfect framing, natural angles, no art direction",
    "normal household lighting and ordinary phone-camera depth of field",
    "a lived-in, ordinary environment with real clutter -- never a styled set",
    "attractive but normal everyday clothing",
    "no lingerie-adjacent styling and no cleavage-forward framing",
    "no glamour-shoot or influencer posing",
    "no movie-poster, cinematic, editorial or key-art look",
    "no exaggerated orange/teal or heavy colour grading",
    "restrained supernatural elements: physical evidence first, visual effects never",
    "clean art only: no rendered text, letters, logo or watermark anywhere in the image",
)

ANTI_REPETITION_RULE = (
    "A new composition is a NEW CAMERA POSITION IN THE SAME ROOM. Every environment anchor "
    "above still exists in the same geometry (a closer shot may show it partly or leave it out "
    "of frame, never contradict it) -- 'different composition' never means a different, bigger, "
    "tidier or redesigned room.")

# Each dimension is checked on its own so a rejection can name what actually
# drifted. The critical ones are the failures that made this module necessary.
#
# anatomy_correctness / canonical_ears_visible / prop_multiplicity /
# sequence_consistency (added 2026-09-26, founder-directed after real content_
# items[20] slides 2-4 were wrongly approved): these were previously folded
# into the broad "identity"/"prop_continuity" dimensions, where a correct
# face could mask a malformed hand or a missing ear. Each is now its own
# dimension so a reviewer (human or delegated) is forced to look at that
# specific spot rather than pass on an overall impression. All four are
# CRITICAL, and (via qa_result()'s existing NOT_CHECKED-is-a-failure rule) a
# FRESH qa_result() call made with an old-style scores dict that omits them
# fails closed automatically. Scope of that guarantee, stated precisely:
# approved_ref() simply returns whatever is already stored in
# approved_slide_refs -- it does not call qa_result() and is not re-evaluated
# by these new dimensions existing. An already-APPROVED slide (e.g. content_
# items[20] slides 2-4, approved before these dimensions existed) is NOT
# automatically invalidated or re-checked; only a NEW qa_result() call is
# held to the stricter bar. Un-approving an existing approval is a separate,
# not-yet-built mechanism (see the append-only revocation proposal) and nothing
# here does that.
QA_DIMENSIONS = ("identity", "environment_continuity", "wardrobe_hair_continuity",
                 "prop_continuity", "anatomy_correctness", "canonical_ears_visible",
                 "prop_multiplicity", "sequence_consistency", "story_beat",
                 "composition_novelty", "public_social_style", "technical_output")
CRITICAL_QA_DIMENSIONS = ("identity", "environment_continuity", "wardrobe_hair_continuity",
                          "prop_continuity", "anatomy_correctness", "canonical_ears_visible",
                          "prop_multiplicity", "sequence_consistency")
MAX_ATTEMPTS_PER_SLIDE = 3
# Scoring a slide is a PROVIDER capability (somebody or something has to look at
# the image), not an inference from the filesystem. Without an authorized one a
# candidate stays here for ever -- it is never promoted because a file exists.
PENDING_VISUAL_QA = "PENDING_VISUAL_QA"

CAMERA_FIELDS = ("distance", "angle", "height", "subject", "body_position")
PLAN_FIELDS = ("item_id", "story_id", "slide_index", "total_slides", "narrative_role",
               "story_beat", "new_information_revealed", "visual_action", "composition",
               "camera", "overlay_copy", "overlay_safe_zone", "required_continuity",
               "forbidden_repetition", "tone")
_BRIEF_FIELDS = ("role", "beat", "composition", "text_overlay", "continuity")

# The drift vocabulary that actually happened (luxury/polished apartment) plus
# the styling words the public-social policy forbids. Matched only in the
# lower-authority layers, and only in a clause that is not itself a prohibition.
_DRIFT = re.compile(
    r"luxur\w*|upscale|penthouse|loft\b|hotel suite|spacious|palatial|high[- ]ceiling\w*|"
    r"ornate|chandelier|marble|velvet|designer furniture|showroom|apartment tour|"
    r"cinematic|movie still|film still|movie[- ]poster|editorial|magazine|glamour|"
    r"key ?visual|key ?art|fantasy poster|splash[- ]?art|god rays|volumetric|"
    r"studio (campaign|lighting|shoot)|"
    r"lingerie|negligee|cleavage|magical glow|glowing rune\w*|light beam|beam of light|"
    r"energy effect|vfx", re.I)
_PROHIBITION = re.compile(r"\b(no|not|never|avoid|without|non-)\b", re.I)


class ContinuityError(ValueError):
    """A refusal that must never be worked around (an incomplete story lock, a
    plan that does not advance, a state file from another story)."""


class NotReady(ContinuityError):
    """Slide N was asked for before slide N-1 was APPROVED. Carries the
    structured gate so a caller can report exactly what is missing instead of
    re-deriving it from the message."""

    def __init__(self, gate: dict):
        self.gate = gate
        super().__init__("; ".join(gate.get("blockers") or ["not ready"]))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- story lock (the per-story contract) -------------------------------------
def load_story_lock(index: int, *, directory: Path | None = None) -> dict | None:
    """The versioned story lock for ONE content item, or None when the item has
    no lock yet (every other carousel keeps its existing behaviour)."""
    folder = directory or STORY_LOCKS_DIR
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.json")):
        lock = json.loads(path.read_text())
        if lock.get("item_index") == index:
            lock.setdefault("lock_file", path.relative_to(PROJECT_ROOT).as_posix()
                            if path.is_relative_to(PROJECT_ROOT) else path.name)
            # The absolute source, so a writer (founder-approve-slide) edits the
            # file this dict actually came from rather than re-deriving it.
            lock["lock_path"] = str(path)
            return lock
    return None


def lock_problems(lock: dict) -> list[str]:
    """A story lock is only worth compiling a prompt from if both authorities
    are complete -- a missing environment anchor set is how the room drifts."""
    probs = [f"missing {k}" for k in ("story_id", "item_index", "story_plan", "generation")
             if not lock.get(k) and lock.get(k) != 0]
    env = lock.get("environment_lock") or {}
    if not env.get("anchors"):
        probs.append("environment_lock has no anchors")
    for key in ("summary", "geometry_rule", "vocabulary_rule"):
        if not str(env.get(key) or "").strip():
            probs.append(f"environment_lock missing {key}")
    if not env.get("forbidden"):
        probs.append("environment_lock has no forbidden list")
    wardrobe = lock.get("wardrobe_lock") or {}
    for key in ("outfit", "hair", "time_of_day"):
        if not str(wardrobe.get(key) or "").strip():
            probs.append(f"wardrobe_lock missing {key}")
    if not lock.get("initial_story_state"):
        probs.append("missing initial_story_state")
    if (lock.get("generation") or {}).get("paid_generation_authorized"):
        probs.append("story lock claims paid generation is authorized -- not granted")
    # 4:5 is the public carousel master; 9:16 only for TikTok/Fanvue.
    probs += render_masters.lock_master_problems(lock)
    return probs


def brief_slides(lock: dict) -> list[dict]:
    """The story plan reduced to the EXISTING brief shape, so the same file is
    what `apply-story` writes into venture.json -- the plan and the brief can
    never be two independently edited stories."""
    return [{k: s.get(k, "") for k in _BRIEF_FIELDS} for s in lock.get("story_plan") or []]


def story_plan(lock: dict) -> list[dict]:
    """The complete narrative plan, generated and validated BEFORE any image
    model is called. Brief fields (role/beat/composition/text_overlay/
    continuity) are kept under their existing names; the planning fields the
    brief never carried are added alongside them."""
    slides = lock.get("story_plan") or []
    total = len(slides)
    plan = []
    for s in slides:
        tone = s.get("tone") or lock.get("tone", "")
        plan.append({
            "item_id": lock.get("item_id", ""),
            "story_id": lock.get("story_id", ""),
            "slide_index": s.get("slide_index"),
            "total_slides": total,
            "narrative_role": s.get("narrative_role", ""),
            "role": s.get("role", ""),
            "story_beat": s.get("story_beat") or s.get("beat", ""),
            "beat": s.get("beat", ""),
            "new_information_revealed": s.get("new_information_revealed", ""),
            "visual_action": s.get("visual_action", ""),
            "composition": s.get("composition", ""),
            "camera": dict(s.get("camera") or {}),
            # Newer locks (s1e02_public) name the copy overlay_copy; older ones text_overlay.
            "overlay_copy": s.get("text_overlay") or s.get("overlay_copy", ""),
            "overlay_safe_zone": s.get("overlay_safe_zone", ""),
            "text_placement": dict(s.get("text_placement") or {}),
            "continuity": s.get("continuity", ""),
            "required_continuity": list(s.get("required_continuity") or []),
            "forbidden_repetition": list(s.get("forbidden_repetition") or []),
            "tone": tone,
            "public_vs_fanvue": tone,
            "state_after": dict(s.get("state_after") or {}),
        })
    return plan


def _placement_wording(record: dict) -> str:
    """What the generator must keep clean for the overlay: the explicit
    text_placement when the lock has one, else the older worded safe zone."""
    tp = record.get("text_placement") or {}
    if tp.get("anchor") in ("top", "bottom"):
        third = "upper third" if tp["anchor"] == "top" else "lower third"
        clear = ", ".join(tp.get("keep_clear") or [])
        return f"{third}{', clear of ' + clear if clear else ''}"
    return record.get("overlay_safe_zone") or "overlay safe zone"


def plan_record(lock: dict, slide_index: int) -> dict:
    for record in story_plan(lock):
        if record["slide_index"] == slide_index:
            return record
    raise ContinuityError(f"story {lock.get('story_id')!r} has no slide {slide_index}")


def plan_problems(lock: dict, item: dict | None = None) -> list[str]:
    """Structural problems with the plan; empty list = plannable. The point of
    planning before generation is that five poses are not a story, so a slide
    that reveals nothing new, or repeats another slide's camera, fails here --
    before an image model is ever asked for it."""
    probs = lock_problems(lock)
    plan = story_plan(lock)
    if not plan:
        return probs + ["story_plan is empty"]
    for i, record in enumerate(plan, start=1):
        label = f"slide {record.get('slide_index') or i}"
        if record["slide_index"] != i:
            probs.append(f"{label} is out of order (expected slide_index {i})")
        for field in PLAN_FIELDS:
            value = record.get(field)
            if field == "forbidden_repetition":
                if not isinstance(value, list):
                    probs.append(f"{label} forbidden_repetition must be a list")
                elif i > 1 and not value:
                    probs.append(f"{label} names nothing it must not repeat")
                continue
            if field == "camera":
                missing = [k for k in CAMERA_FIELDS if not str((value or {}).get(k) or "").strip()]
                probs += [f"{label} camera missing {k}" for k in missing]
                continue
            if field == "required_continuity":
                if not value:
                    probs.append(f"{label} names no required continuity")
                continue
            if not str(value or "").strip():
                probs.append(f"{label} missing {field}")
        if record["role"] and record["role"] not in stages.CAROUSEL_ROLES:
            probs.append(f"{label} bad role {record['role']!r}")
        if not record.get("state_after"):
            probs.append(f"{label} declares no state_after -- story state could not advance")
    news = [" ".join(str(r["new_information_revealed"]).lower().split()) for r in plan]
    if len(set(news)) != len(news):
        probs.append("two or more slides reveal the same information -- that is a set of poses, "
                     "not a story where every slide changes viewer understanding")
    shots = [tuple(str((r["camera"] or {}).get(k, "")).strip().lower() for k in CAMERA_FIELDS)
             for r in plan]
    if len(set(shots)) != len(shots):
        probs.append("two or more slides share the same camera fingerprint")
    if item is not None:
        probs += [f"brief rules: {p}"
                  for p in stages.story_carousel_problems({**item, "slides": brief_slides(lock)})]
    return probs


def brief_sync(lock: dict, item: dict) -> dict:
    """Whether venture.json's brief already carries this plan. A stale brief is
    reported, never silently generated from: the plan is what gets drawn."""
    current = [{k: s.get(k, "") for k in _BRIEF_FIELDS} for s in item.get("slides") or []]
    if current == brief_slides(lock):
        return {"status": "IN_SYNC", "detail": "venture.json carries this exact plan"}
    return {"status": "STALE",
            "detail": "venture.json's brief is not this plan -- the plan is authoritative for "
                      "generation, the brief still drives QA and publishing",
            "fix": f"python3 ps05_ops.py apply-story {lock.get('item_index')} "
                   f"{lock.get('lock_file', '')}"}


# --- persisted continuity state ----------------------------------------------
def state_path(index: int, root: Path | None = None) -> Path:
    return carousel_handoff.package_dir(index, root) / STATE_FILENAME


def new_state(lock: dict) -> dict:
    """The state a story starts in: environment authority PROVISIONAL (the
    founder-approved slide-1 description) until the real slide 1 is approved."""
    env = dict(lock.get("environment_lock") or {})
    return {
        "schema_version": SCHEMA_VERSION,
        "story_id": lock.get("story_id", ""),
        "item_index": lock.get("item_index"),
        "item_id": lock.get("item_id", ""),
        "identity_lock": dict(lock.get("identity_lock") or {}),
        "environment_lock": {**env, "status": "PROVISIONAL", "source_slide_ref": None,
                             "locked_at": None,
                             "authority": "immutable for this story; outranks the approved state, "
                                          "the previous slide and all styling"},
        "wardrobe_lock": dict(lock.get("wardrobe_lock") or {}),
        "story_state": {**dict(lock.get("initial_story_state") or {}),
                        "updated_from_slide": None, "updated_at": None},
        "approved_slide_refs": [],
        "revocations": [],
        "composition_history": [],
        "retry_state": {"max_attempts_per_slide": int((lock.get("retry") or {}).get(
                            "max_attempts_per_slide", MAX_ATTEMPTS_PER_SLIDE)),
                        "attempts": {}, "rejected": []},
        "cost_state": {**dict(lock.get("cost_state") or {}),
                       "intended_provider": (lock.get("generation") or {}).get("intended_provider"),
                       "intended_model": (lock.get("generation") or {}).get("intended_model"),
                       "paid_generation_authorized": False},
        "created_at": _now(),
        "updated_at": _now(),
    }


def load_state(lock: dict, index: int, *, root: Path | None = None) -> dict:
    """Resume, never restart: the persisted state is the continuity. A file
    belonging to another story or another schema is refused rather than
    overwritten -- losing the environment authority silently is the whole
    failure this module exists to prevent."""
    path = state_path(index, root)
    if not path.is_file():
        return new_state(lock)
    data = json.loads(path.read_text())
    if data.get("story_id") != lock.get("story_id"):
        raise ContinuityError(
            f"{path} holds story {data.get('story_id')!r}, not {lock.get('story_id')!r}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ContinuityError(
            f"{path} is schema v{data.get('schema_version')}, this build speaks v{SCHEMA_VERSION}")
    return data


def save_state(state: dict, index: int, *, root: Path | None = None) -> Path:
    path = state_path(index, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now()
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    return path


def ensure_state(lock: dict, index: int, *, root: Path | None = None) -> dict:
    state = load_state(lock, index, root=root)
    fresh = not state_path(index, root).is_file()
    # apply_founder_approvals is a no-op once they are already in the state, so
    # a resumed story is never re-approved or rewound.
    if apply_founder_approvals(lock, state, index=index, root=root) or fresh:
        save_state(state, index, root=root)
    return state


# --- composition memory -------------------------------------------------------
def composition_fingerprint(record: dict) -> dict:
    camera = record.get("camera") or {}
    return {"slide_index": record.get("slide_index"),
            **{k: " ".join(str(camera.get(k, "")).lower().split()) for k in CAMERA_FIELDS},
            "composition": " ".join(str(record.get("composition", "")).lower().split()),
            "information": " ".join(str(record.get("new_information_revealed", "")).lower().split())}


def forbidden_repetition(state: dict, record: dict) -> list[str]:
    """What this slide may not repeat: its own planned exclusions plus every
    APPROVED slide's camera and revealed information. Ends with the rule that
    keeps 'change the composition' from becoming 'invent a different room'."""
    lines = list(record.get("forbidden_repetition") or [])
    for fp in state.get("composition_history") or []:
        shot = " / ".join(f"{k}: {fp.get(k, '')}" for k in CAMERA_FIELDS if fp.get(k))
        lines.append(f"slide {fp.get('slide_index')}'s camera ({shot})")
        if fp.get("information"):
            lines.append(f"information slide {fp.get('slide_index')} already revealed: "
                         f"{fp['information']}")
    lines.append(ANTI_REPETITION_RULE)
    return lines


# --- QA + retry gate ----------------------------------------------------------
def qa_expectations(lock: dict, record: dict, state: dict) -> dict:
    """What the generated image must satisfy, per dimension, BEFORE it is
    approved -- written with the plan so QA is never improvised afterwards."""
    env = state.get("environment_lock") or {}
    wardrobe = state.get("wardrobe_lock") or {}
    anchors = [a.get("description", "") for a in env.get("anchors") or []]
    return {
        "identity": {"critical": True,
                     "expectation": "the same woman as the canonical master reference: face, eye "
                                    "shape and amber colour, hair, fox-ear design, exactly two "
                                    "ears (her fox ears, no human ears), beauty mark on her "
                                    "anatomical LEFT cheek (viewer-right), adult build, exactly "
                                    "one tail when visible"},
        "environment_continuity": {"critical": True,
                                   "expectation": f"the same room: every environment anchor that is in "
                                                  f"frame matches, in the same geometry, and none is "
                                                  f"contradicted, moved or redesigned (anchors may be "
                                                  f"partial or out of frame in a closer shot): "
                                                  f"{'; '.join(anchors)}"},
        "wardrobe_hair_continuity": {"critical": True,
                                     "expectation": f"{wardrobe.get('outfit', '')}; "
                                                    f"{wardrobe.get('hair', '')}; "
                                                    f"{wardrobe.get('time_of_day', '')}"},
        "prop_continuity": {"critical": True,
                            "expectation": "; ".join(f"{k}: {v}" for k, v in
                                                     (state.get("story_state", {}).get("props")
                                                      or {}).items())},
        "anatomy_correctness": {"critical": True,
                                "expectation": "hands and any other visible limbs are anatomically "
                                              "plausible: correct joint structure, no fused or extra "
                                              "digits, no impossible bends. VISIBILITY-AWARE: do not "
                                              "require every finger to be countable if the pose/framing "
                                              "naturally occludes or crops some -- reject only a genuine "
                                              "anatomical impossibility, or a case where anatomy cannot "
                                              "be assessed with confidence (unresolved critical "
                                              "uncertainty is a failure, never a pass)."},
        "canonical_ears_visible": {"critical": True,
                                   "expectation": "whenever the top of her head is actually visible in "
                                                 "frame, at least the ear(s) that are anatomically "
                                                 "capable of being seen from that angle show the "
                                                 "canonical fox-ear placement and fur pattern. This is a "
                                                 "visibility judgment made from the actual image, never "
                                                 "inferred from the shot's composition text, and it "
                                                 "allows genuine anatomical occlusion: a profile or "
                                                 "three-quarter angle that shows only the near ear (the "
                                                 "far one hidden by her own head, exactly as a real ear "
                                                 "would be) is NOT a failure, and a shot legitimately "
                                                 "cropped so the top of her head is out of frame entirely "
                                                 "is NOT a failure either. The failure this catches is "
                                                 "specific: her head/hair in frame, from an angle where "
                                                 "an ear WOULD be visible, with no fox-ear silhouette "
                                                 "there at all (ordinary human hair/head instead)."},
        "prop_multiplicity": {"critical": True,
                              "expectation": "every object she is currently interacting with (see "
                                            "prop_continuity above) appears EXACTLY ONCE in the frame -- "
                                            "never a second copy of the same object elsewhere in the "
                                            "image."},
        "sequence_consistency": {"critical": True,
                                 "expectation": "this candidate does not contradict the story's "
                                               "currently VALID established canon (room geometry, "
                                               "wardrobe, narrative facts, prop/state progression) -- "
                                               "checked against the full sequence, not only the "
                                               "immediately preceding slide, but NOT against every raw "
                                               "entry in composition_history/story_state as if it were "
                                               "automatically authoritative. An approval a reviewer or "
                                               "founder currently knows to have been overturned/rejected "
                                               "(e.g. content_items[20] slides 2-4, rejected 2026-09-26) "
                                               "is not valid canon and must not be treated as something "
                                               "this or later slides need to stay consistent with; use "
                                               "the intended, currently-valid changes, not stale state."},
        "story_beat": {"critical": False,
                       "expectation": f"{record.get('story_beat', '')} -- and it must actually "
                                      f"show: {record.get('new_information_revealed', '')}"},
        "composition_novelty": {"critical": False,
                                "expectation": "a camera position not used by any approved slide, "
                                               "in the same room: "
                                               + "; ".join(forbidden_repetition(state, record))},
        "public_social_style": {"critical": False,
                                "expectation": "; ".join(PUBLIC_SOCIAL_POLICY)},
        "technical_output": {"critical": False,
                             "expectation": "correct aspect ratio, no rendered text, letters, "
                                            "logo or watermark, no artefacts or extra limbs"},
    }


def _approved_file_hash(state: dict, value: str, root: Path | None) -> str | None:
    """SHA-256 of the actual bytes at one approval's file, or None if it
    cannot be read. AUDIT/DISPLAY ONLY -- see approved_ref's docstring for
    why this is never used to decide whether a revocation still applies."""
    if not value:
        return None
    try:
        path = carousel_handoff.package_dir(state.get("item_index"), root) / value
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def approved_ref(state: dict, slide_index: int, *, root: Path | None = None) -> dict | None:
    """The APPROVED image for one slide, or None. An entry without a value is
    not an approval -- there is nothing to hand a provider.

    PROPOSAL, not yet live (2026-09-26): a revoked approval (see
    revoke_approval below) also returns None here. This is the ONLY place
    revocation takes effect.

    CORRECTED 2026-09-26 after a review caught a real bug: an earlier
    version of this function matched revocations by CONTENT HASH (was the
    current file's bytes the same as what got revoked?). That is wrong --
    it means simply overwriting the canonical file's bytes on disk, with NO
    new approve_slide()/QA call at all, would make a revoked slide read as
    approved again (using the OLD entry's approved_by/approved_at, for
    content nobody ever reviewed). Reproduced and confirmed in review.

    The actual, correct binding is to the APPROVAL EVENT, not the bytes on
    disk: revoke_approval() stamps the SPECIFIC approved_slide_refs entry it
    is revoking with a unique `approval_id` (the ONLY field ever added to an
    existing entry -- value/approved_by/approved_at/simulated are never
    touched). A later, genuinely new approve_slide() call always creates a
    FRESH dict (existing behavior: it replaces the slot's entry), which
    therefore has NO approval_id yet -- so it can never be confused with the
    revoked one, regardless of what filename or bytes it uses. Editing bytes
    on disk without a real approve_slide() call changes nothing here: the
    SAME (already-id-stamped, already-revoked) dict is still what's stored,
    so it is still found revoked. `root`/content hash remain available
    (recorded by revoke_approval, computed here) purely for human-readable
    audit, never for this decision."""
    for ref in state.get("approved_slide_refs") or []:
        if ref.get("slide_index") == slide_index and str(ref.get("value") or "").strip():
            revs = [r for r in state.get("revocations") or [] if r.get("slide_index") == slide_index]
            if not revs:
                return ref
            aid = ref.get("approval_id")
            # No id -- this entry has never been the specific target of any
            # revocation (revoke_approval only ever stamps the exact entry
            # it revokes, at the moment it revokes it) -- so this is
            # unambiguously a later, different, unrevoked approval event,
            # even though revocations exist for this slide_index from a
            # prior (superseded) entry.
            if aid is None:
                return ref
            if any(r.get("revoked_approval_id") == aid for r in revs):
                return None
            return ref
    return None


def revoke_approval(state: dict, slide_index: int, *, reason: str, revoked_by: str,
                    root: Path | None = None) -> dict:
    """PROPOSAL, not yet live: the only way an approved slide stops being
    approved, without ever editing the SUBSTANTIVE fields of the original
    approval record (value/approved_by/approved_at/simulated) -- pure
    append, plus one bookkeeping id stamped onto that one entry (see
    approved_ref's docstring for exactly why, and what "never edited" does
    and doesn't cover). Requires normal native review + explicit
    authorization before any real call against live state.

    Slide 1 is PROTECTED and can never be revoked through this function: it
    owns environment_lock, the immutable room/wardrobe master every later
    slide depends on. Un-anchoring that safely is a materially bigger,
    separate problem this function does not attempt.

    Order/cascade semantics: revoking slide N also revokes every slide > N
    that CURRENTLY holds a non-revoked approval, since those approvals were
    granted on the assumption that slide N was (and would stay) valid. Each
    cascaded entry is recorded distinctly (cascaded=True, cascaded_from=N).

    KNOWN GAP, stated plainly: this does NOT recompute story_state or
    composition_history back to the last still-valid slide -- those fields
    stay as the (now stale) revoked slides left them. It DOES recompute
    cost_state['approved_slides'] to the true current count. See
    readiness()'s staleness check (added alongside this) for how downstream
    generation is protected from that staleness in the meantime: it BLOCKS
    rather than silently compiling a prompt from stale narrative context.

    Reapproval semantics: once revoked, slide N reads exactly like an
    unapproved slide again -- approved_ref() returns None, readiness() for
    slide N+1 blocks again, and generate_slide() will regenerate it (never
    ALREADY_APPROVED). A later approve_slide() call for slide N is accepted
    normally and is a GENUINELY NEW, unrevoked event (see approved_ref) --
    this holds regardless of whether the new candidate reuses the same
    canonical filename (the real production path always does) or even
    coincidentally the same bytes. What does NOT create a new event, and
    therefore does NOT clear a revocation: merely overwriting the file on
    disk without ever calling approve_slide() again."""
    if slide_index == 1:
        raise ContinuityError(
            "slide 1 cannot be revoked through this function -- it owns the environment_lock every "
            "later slide depends on; revoking it needs a separate, not-yet-designed mechanism")
    current = approved_ref(state, slide_index, root=root)
    if current is None:
        raise ContinuityError(f"slide {slide_index} has no current (non-revoked) approval to revoke")
    now = _now()

    def _stamp_and_revoke(ref: dict, *, cascaded_from: int | None) -> dict:
        # Reviewer-flagged gap (2026-09-26): approve_slide() REPLACES the live
        # approved_slide_refs entry for this slot on any later reapproval, so
        # the original approver/timestamp/simulated flag would otherwise be
        # gone from every place the persisted state carries once that happens
        # -- a Python reference to `ref` surviving in memory is not the same
        # guarantee as a value that has actually been copied into the
        # revocation record before that replacement, and is not what
        # save_state()/load_state() round-trip through JSON either. Snapshot
        # BEFORE stamping approval_id, since that id is bookkeeping this
        # function adds, never part of the original approval.
        original_snapshot = dict(ref)
        if not ref.get("approval_id"):
            ref["approval_id"] = secrets.token_hex(8)
        return {"slide_index": ref["slide_index"], "revoked_approval_id": ref["approval_id"],
                "revoked_value": ref.get("value"),
                "revoked_content_hash": _approved_file_hash(state, ref.get("value"), root),
                "original_approval": original_snapshot,
                "revoked_at": now, "revoked_by": revoked_by,
                "reason": (reason if cascaded_from is None else
                          f"cascaded: depended on slide {cascaded_from}'s now-revoked approval"),
                "cascaded": cascaded_from is not None, "cascaded_from": cascaded_from}

    cascaded_refs = []
    for other in state.get("approved_slide_refs") or []:
        idx = other.get("slide_index")
        if idx is not None and idx > slide_index:
            other_current = approved_ref(state, idx, root=root)
            if other_current is not None:
                cascaded_refs.append(other_current)
    cascaded_refs.sort(key=lambda r: r["slide_index"])
    revocations = state.setdefault("revocations", [])
    revocations.append(_stamp_and_revoke(current, cascaded_from=None))
    for ref in cascaded_refs:
        revocations.append(_stamp_and_revoke(ref, cascaded_from=slide_index))
    cost = state.setdefault("cost_state", {})
    cost["approved_slides"] = sum(
        1 for r in state.get("approved_slide_refs") or []
        if approved_ref(state, r["slide_index"], root=root) is not None)
    state["updated_at"] = now
    return {"status": "REVOKED", "slide_index": slide_index, "revoked_at": now,
           "revoked_by": revoked_by, "also_revoked": [r["slide_index"] for r in cascaded_refs]}


def project_active_context(state: dict, lock: dict, *, root: Path | None = None) -> dict:
    """PROPOSAL, not yet live against real state: derives a CLEAN,
    non-stale view of the narrative-dependent fields (story_state,
    composition_history, approved_slide_refs) for prompt/readiness/
    reference-selection consumers to read INSTEAD OF the raw ones, so a
    revoked slide's now-invalid narrative advance can never leak into what a
    later slide is generated from.

    Derivation: walks slide_index = 1, 2, 3, ... replaying each slide's own
    plan record (state_after / composition fingerprint) for as long as that
    slide CURRENTLY has a valid (non-revoked) approval -- stopping at the
    first gap. This mirrors the order the system already enforces at
    approval time (approve_slide refuses slide N before slide N-1), so a
    gap here should only ever arise from a slide having been revoked after
    the fact.

    Preserves ALL real history: this returns a NEW dict; nothing in `state`
    (the real, persisted approved_slide_refs/revocations/story_state/
    composition_history) is read destructively, mutated, or recomputed in
    place. environment_lock, wardrobe_lock, identity_lock, retry_state,
    cost_state and every other field pass through UNCHANGED (spread from
    the real state) -- slide 1 can never be revoked (see revoke_approval),
    so once environment_lock is LOCKED it is always still valid and is
    never re-derived here.

    Fail-closed composition: this does not replace readiness()'s own order/
    retry checks -- it composes with them. If slide 1 itself has no valid
    approval, the walk immediately stops and every later slide's projected
    story_state/composition_history is empty, exactly as it would be before
    any approval ever existed; readiness() still separately refuses
    generation past slide 1 without a LOCKED environment, regardless of
    what this returns.

    Reviewer-flagged gap (2026-09-26), fixed here: a slide is only ever
    admitted into the projected approved_slide_refs/story_state/
    composition_history TOGETHER, atomically, once its plan record is
    confirmed to actually exist -- never a version where the ref is listed
    as valid but its narrative facts were never replayed because the plan
    lookup failed. A slide whose approval exists but whose plan record is
    missing (a lock/plan mismatch this system should never produce, but
    must never silently admit either) is treated exactly like a gap: the
    walk stops before it, same as an outright revoked/missing approval."""
    projected_approved: list[dict] = []
    projected_history: list[dict] = []
    story_state = {**dict(lock.get("initial_story_state") or {}),
                   "updated_from_slide": None, "updated_at": None}
    slot = 1
    while True:
        ref = approved_ref(state, slot, root=root)
        if ref is None:
            break
        try:
            record = plan_record(lock, slot)
        except ContinuityError:
            break
        projected_approved.append(ref)
        story_state = {**story_state, **(record.get("state_after") or {}),
                       "updated_from_slide": slot, "updated_at": ref.get("approved_at")}
        projected_history.append(composition_fingerprint(record))
        slot += 1
    return {**state, "approved_slide_refs": projected_approved,
           "composition_history": projected_history, "story_state": story_state}


def _staleness_reason(state: dict, *, root: Path | None = None) -> str | None:
    """PROPOSAL, not yet live: defense-in-depth ONLY, for a caller that
    reads story_state directly without going through project_active_context
    above (which is the actual, real fix -- see its docstring). None if
    story_state's own recorded basis is still validly approved; otherwise a
    message naming exactly what is stale. This does not inspect
    composition_history: an entry there for a slide that has since been
    revoked is exactly what project_active_context is for (it is correctly
    excluded from the PROJECTED view fed to the compiler), and blocking
    generation entirely over a raw, un-projected list would incorrectly
    refuse to regenerate that very slide once its own fresh reapproval is
    what's actually needed."""
    story_state = state.get("story_state") or {}
    basis = story_state.get("updated_from_slide")
    if basis is not None and approved_ref(state, basis, root=root) is None:
        return (f"story_state was last advanced by slide {basis}, whose approval has since been "
                f"revoked -- narrative context is stale until slide {basis} is reapproved (or the "
                f"story is otherwise reconciled)")
    return None


def reconcile_story_state(state: dict, lock: dict, *, root: Path | None = None) -> dict:
    """Rewind story_state to the last slide that still holds a valid approval.

    revoke_approval()'s own docstring names this as a KNOWN GAP: it revokes the
    cascade but leaves story_state pointing at a slide whose approval it just
    removed. _staleness_reason() then blocks EVERY slide of that story --
    including the low-numbered one that has to be approved first to work back
    up -- so a legitimate replacement deadlocks (observed live on 2026-09-30:
    replacing item30 slide 3 left story_state.updated_from_slide = 6 and
    nothing could be approved at all). This is the "or the story is otherwise
    reconciled" half of that error message.

    The rewind mirrors approve_slide() exactly: start from the lock's own
    initial story state and replay each still-approved slide's `state_after`
    in order, then point the basis at the highest of them. It never invents a
    state, never touches approved_slide_refs, and never resurrects a revoked
    approval. composition_history is deliberately left alone -- entries for
    revoked slides are excluded by project_active_context(), which is where
    that concern is already handled."""
    valid = []
    for rec in story_plan(lock):
        slot = rec["slide_index"]
        if approved_ref(state, slot, root=root) is None:
            break                      # approvals are ordered; stop at the first gap
        valid.append(rec)
    base = dict((lock.get("initial_story_state") or {}))
    for rec in valid:
        base.update(rec.get("state_after") or {})
    basis = valid[-1]["slide_index"] if valid else None
    before = (state.get("story_state") or {}).get("updated_from_slide")
    state["story_state"] = {**base, "updated_from_slide": basis, "updated_at": _now()}
    return {"rewound_from_slide": before, "basis_now": basis,
            "replayed_slides": [r["slide_index"] for r in valid]}


def readiness(state: dict, record: dict, *, root: Path | None = None) -> dict:
    """Fail closed on slide ORDER. Slide N is not generatable -- not even
    preparable -- until slide N-1 has actually been approved, and until the
    environment authority is LOCKED onto a real approved image ref. There is no
    text-only fallback: describing the room in prose is exactly what let a
    regenerated slide 2 redesign the bedroom. Slide 1 is the only slide that may
    be prepared from the lock alone; it is the slide that freezes the room.

    `root` (added 2026-09-26, PROPOSAL plumbing): kept for forward
    compatibility and forwarded to approved_ref(); approved_ref's own
    revocation decision no longer depends on the filesystem at all (see its
    docstring -- it's bound to an approval event id, not file content), so
    this mostly matters for anything that still computes an audit hash.

    Also blocks (added 2026-09-26) when story_state or composition_history
    are STALE relative to the currently-valid (non-revoked) approvals -- see
    _staleness_reason -- rather than silently compiling a prompt from
    narrative context that a since-revoked slide's approval produced."""
    slot = record["slide_index"]
    env = state.get("environment_lock") or {}
    source = int(env.get("source_slide", 1) or 1)
    blockers = []
    if slot > 1:
        if approved_ref(state, slot - 1, root=root) is None:
            blockers.append(
                f"slide {slot - 1} is not approved -- slide {slot} may not be generated or "
                f"prepared until slide {slot - 1} passes visual QA")
        if env.get("status") != "LOCKED" or not str(env.get("source_slide_ref") or "").strip():
            blockers.append(
                f"environment_lock is {env.get('status', 'PROVISIONAL')} -- slide {slot} requires "
                f"the APPROVED slide {source} image as its environment master, and there is no "
                f"text-only fallback for it")
    stale = _staleness_reason(state, root=root)
    if stale:
        blockers.append(stale)
    retry = state.get("retry_state") or {}
    ceiling = int(retry.get("max_attempts_per_slide", MAX_ATTEMPTS_PER_SLIDE))
    attempts = int((retry.get("attempts") or {}).get(str(slot), 0))
    if attempts >= ceiling:
        blockers.append(f"slide {slot} has used all {ceiling} attempts -- another retry is a "
                        f"founder decision, not an automatic one")
    return {"slide_index": slot, "ready": not blockers,
            "status": "READY" if not blockers else "BLOCKED",
            "requires_approved_slide": slot - 1 if slot > 1 else None,
            "environment_lock_status": env.get("status"),
            "environment_source_slide_ref": env.get("source_slide_ref"),
            "approved_slides": [r["slide_index"] for r in state.get("approved_slide_refs") or []
                                if str(r.get("value") or "").strip()],
            "attempts_used": attempts, "attempts_remaining": max(ceiling - attempts, 0),
            "blockers": blockers}


def visual_qa_provider(lock: dict) -> dict:
    """Who is allowed to score a candidate image for this story. A missing or
    unauthorized provider is never a pass -- it keeps the slide PENDING."""
    cfg = lock.get("visual_qa") or {}
    provider, authorized = cfg.get("provider"), bool(cfg.get("authorized"))
    return {"provider": provider, "authorized": authorized,
            "available": bool(provider) and authorized,
            "note": cfg.get("note", "no visual-QA provider is authorized for this story")}


def candidate_path(index: int, slot: int, root: Path | None = None) -> Path:
    return carousel_handoff.package_dir(index, root) / carousel_handoff.slide_filename(index, slot)


# Reviewer-flagged gap (2026-09-29), real incident: Music Box slide 2 was
# approved with composition_novelty marked PASS while the QA notes
# themselves honestly disclosed "did NOT follow the composition brief...
# nearly the same pose/framing as slide1" -- a knowingly-caveated duplicate
# that read, in persisted state, identically to a clean pass. A caller
# marking a dimension True is not evidence the dimension actually passed;
# their own prose can contradict it. This pattern is matched against notes
# and, when found, the affected dimension is force-failed regardless of
# what the caller passed -- closing the exact loophole that let this
# through, not a general duplicate-image detector (no such capability
# exists here; this is a text-consistency guard on self-reported QA).
_DUPLICATE_DISCLOSED = re.compile(
    r"near[- ]ident(?:ical|ity)|near[- ]duplicate|did not follow the composition brief|"
    r"nearly the same (?:pose|composition|framing)|same .{0,40}framing as slide|"
    r"no new (?:visual )?(?:story )?beat|does not advance the story|"
    r"repeats? slide ?\d|still shows the same|essentially repeating", re.I)
_DUPLICATE_GUARDED_DIMS = ("composition_novelty", "story_beat")


def _duplicate_disclosed_in_notes(notes: str) -> bool:
    return bool(_DUPLICATE_DISCLOSED.search(str(notes or "")))


def qa_result(scores: dict, *, notes: str = "") -> dict:
    """Fail-closed: a dimension nobody checked counts as a critical failure, not
    as a pass. An unchecked environment is exactly how the room drifted.

    Also fail-closed on SELF-CONTRADICTION (2026-09-29): if `notes` itself
    discloses a near-duplicate / no-new-beat finding, composition_novelty and
    story_beat cannot be forced to PASS by the caller -- see
    _DUPLICATE_DISCLOSED's docstring above for the real incident this closes."""
    dims = {}
    for dim in QA_DIMENSIONS:
        raw = scores.get(dim)
        if raw is None:
            dims[dim] = "NOT_CHECKED"
        elif isinstance(raw, bool):
            dims[dim] = "PASS" if raw else "FAIL"
        else:
            dims[dim] = str(raw).upper()
    if _duplicate_disclosed_in_notes(notes):
        for dim in _DUPLICATE_GUARDED_DIMS:
            if dims.get(dim) == "PASS":
                dims[dim] = "FAIL"
    failed = [d for d, v in dims.items() if v != "PASS"]
    critical = [d for d in failed if d in CRITICAL_QA_DIMENSIONS or dims[d] == "NOT_CHECKED"]
    return {"schema_version": SCHEMA_VERSION, "checked_at": _now(), "dimensions": dims,
            "failed": failed, "critical_failures": critical,
            "verdict": "REJECTED" if failed else "APPROVED", "notes": notes}


def approve_slide(state: dict, record: dict, *, slide_ref: str = "", qa: dict | None = None,
                  simulated: bool = False, approved_by: str = "visual_qa") -> dict:
    """The ONLY way story state moves. Approving slide 1 also freezes the
    environment authority onto that real image, which every later slide is then
    given -- not just the immediately previous one. Idempotent: approving the
    same slide twice leaves exactly one approved ref, one composition
    fingerprint and the already-frozen environment master untouched."""
    if qa is not None and qa.get("verdict") != "APPROVED":
        raise ContinuityError("approve_slide called with a non-approved QA result")
    # Defense in depth (2026-09-29): qa_result() already force-fails
    # composition_novelty/story_beat when its own notes disclose a
    # duplicate/no-new-beat finding, but that only helps callers who go
    # through qa_result(). A qa dict can also be hand-built and passed
    # directly (exactly how the real Music Box slide 2 incident happened --
    # notes were written honestly, the dimension was still hand-set True).
    # Re-check independently here, at the actual state-changing gate, so
    # this cannot be bypassed by skipping qa_result().
    if qa is not None and _duplicate_disclosed_in_notes(qa.get("notes", "")):
        dims = qa.get("dimensions") or {}
        contradicted = [d for d in _DUPLICATE_GUARDED_DIMS if dims.get(d) == "PASS"]
        if contradicted:
            raise ContinuityError(
                f"slide {record.get('slide_index')} refused: qa notes disclose a near-duplicate/"
                f"no-new-beat finding but {', '.join(contradicted)} is marked PASS -- this is the "
                f"exact caveated-duplicate inconsistency that must reject, not approve; call "
                f"reject_slide with this qa instead")
    slot = record["slide_index"]
    if not str(slide_ref or "").strip():
        raise ContinuityError(
            f"slide {slot} cannot be approved without its approved image reference -- that file "
            "IS the environment master and every later slide's reference set")
    if slot > 1 and approved_ref(state, slot - 1) is None:
        raise ContinuityError(
            f"slide {slot} cannot be approved before slide {slot - 1} -- the story advances in "
            "order, and slide 1 is what locks the environment")
    duplicate = approved_ref(state, slot) is not None
    state["approved_slide_refs"] = [r for r in state.get("approved_slide_refs") or []
                                    if r.get("slide_index") != slot]
    # Reviewer-flagged gap (2026-09-29): this function used to check
    # qa.get("verdict") above and then discard the rest of the qa dict --
    # any caveat a reviewer wrote in qa["notes"] (e.g. "approved despite a
    # disclosed near-duplicate composition, to preserve retry budget") never
    # reached persisted state, so a later reader of approved_slide_refs
    # alone (including this same story's own salvage/publish selection) saw
    # only "APPROVED", indistinguishable from a clean pass. Persist the full
    # qa dict now so that distinction survives -- this is additive only
    # (a new "qa" key), never rewrites value/approved_by/approved_at/
    # simulated, and existing entries approved before this fix simply have
    # no "qa" key, which callers must treat as "not recorded", never as
    # "clean".
    new_ref = {"slide_index": slot, "base": "package", "value": slide_ref,
              "approved_at": _now(), "approved_by": approved_by, "simulated": bool(simulated)}
    if qa is not None:              # omit the key entirely rather than "qa": None --
        new_ref["qa"] = qa          # absence means "not recorded", never "recorded as clean"
    state["approved_slide_refs"].append(new_ref)
    state["approved_slide_refs"].sort(key=lambda r: r["slide_index"])
    state["composition_history"] = [f for f in state.get("composition_history") or []
                                    if f.get("slide_index") != slot]
    state["composition_history"].append(composition_fingerprint(record))
    state["composition_history"].sort(key=lambda f: f["slide_index"])
    state["story_state"] = {**state.get("story_state", {}), **(record.get("state_after") or {}),
                            "updated_from_slide": slot, "updated_at": _now()}
    env = state["environment_lock"]
    if slot == env.get("source_slide", 1) and env.get("status") != "LOCKED":
        env.update({"status": "LOCKED", "source_slide_ref": slide_ref, "locked_at": _now(),
                    "simulated": bool(simulated),
                    "locked_by": "the approved slide 1 image is now this story's environment and "
                                 "wardrobe master; it is supplied on every later slide"})
    state["retry_state"]["attempts"].pop(str(slot), None)
    cost = state.setdefault("cost_state", {})
    cost["approved_slides"] = len(state["approved_slide_refs"])
    cost["qa_passes"] = int(cost.get("qa_passes", 0)) + 1
    cost["paid_generation_authorized"] = False
    state["updated_at"] = _now()
    return {"status": "APPROVED", "slide_index": slot, "slide_ref": slide_ref,
            "duplicate": duplicate, "advanced": not duplicate, "simulated": bool(simulated),
            "environment_lock_status": env.get("status"),
            "environment_source_slide_ref": env.get("source_slide_ref"),
            "approved_slides": [r["slide_index"] for r in state["approved_slide_refs"]],
            "story_state": dict(state["story_state"])}


def reject_slide(state: dict, record: dict, qa: dict) -> dict:
    """A rejected image changes NOTHING except the retry counter: not the
    environment lock, not the story state, not the composition history. The
    retry runs from the same approved state that produced the rejected one."""
    slot = record["slide_index"]
    retry = state["retry_state"]
    attempt = int(retry["attempts"].get(str(slot), 0)) + 1
    retry["attempts"][str(slot)] = attempt
    retry["rejected"].append({"slide_index": slot, "at": _now(), "attempt": attempt,
                              "failed": list(qa.get("failed") or []),
                              "critical_failures": list(qa.get("critical_failures") or []),
                              "notes": qa.get("notes", "")})
    cost = state.setdefault("cost_state", {})
    cost["qa_failures"] = int(cost.get("qa_failures", 0)) + 1
    state["updated_at"] = _now()
    ceiling = int(retry.get("max_attempts_per_slide", MAX_ATTEMPTS_PER_SLIDE))
    return {"status": "REJECTED", "slide_index": slot, "attempt": attempt,
            "attempts_remaining": max(ceiling - attempt, 0),
            "exhausted": attempt >= ceiling,
            "state_mutated": False,
            "retry_from_slide": state["story_state"].get("updated_from_slide"),
            "retry_from": dict(state["story_state"]),
            "note": "only this slide is rejected; approved slides are never regenerated for it"}


def record_qa(state: dict, record: dict, qa: dict, *, slide_ref: str = "",
              simulated: bool = False, approved_by: str = "visual_qa") -> dict:
    return (approve_slide(state, record, slide_ref=slide_ref, qa=qa, simulated=simulated,
                          approved_by=approved_by)
            if qa.get("verdict") == "APPROVED" else reject_slide(state, record, qa))


def qa_slide(lock: dict, state: dict, record: dict, *, index: int, scores: dict | None = None,
             notes: str = "", slide_ref: str = "", root: Path | None = None,
             write: bool = True) -> dict:
    """The per-slide QA lifecycle -- the ONLY route from a candidate image to an
    approved slide, and the only thing that moves story state.

    Deliberately NOT a filesystem check: a candidate appearing in the package
    directory proves that something was written, never that it is the same woman
    in the same room, so its existence can never advance the story on its own.
    The order is gate -> candidate -> authorized scorer -> scores -> approve or
    reject, and every step short of the last leaves the state byte-identical."""
    slot = record["slide_index"]
    path = candidate_path(index, slot, root)
    base = {"index": index, "slide_index": slot, "candidate": path.name,
            "candidate_present": path.is_file(), "approved": False, "state_mutated": False,
            "visual_qa_provider": visual_qa_provider(lock),
            "story_state": dict(state.get("story_state") or {}),
            "environment_lock_status": (state.get("environment_lock") or {}).get("status")}
    gate = readiness(state, record, root=root)
    base["readiness"] = gate
    # The attempt ceiling exists to stop this system re-rolling a slide on its own
    # ("another retry is a founder decision"). Judging a candidate that ALREADY
    # exists is not a retry, and blocking it here means a good image from the
    # founder-authorized external fallback route can never be approved once the
    # local attempts are spent -- which is exactly the state item31 slide 3 was in
    # on 2026-09-30 (its blockers were nothing but the spent ceiling). Every other
    # blocker -- slide order, environment lock, staleness -- still blocks here.
    hard_blockers = [b for b in gate["blockers"] if "has used all" not in b]
    if hard_blockers:
        return {**base, "status": "BLOCKED_NOT_READY", "verdict": "BLOCKED",
                "error": "; ".join(hard_blockers)}
    if not base["candidate_present"]:
        return {**base, "status": "NO_CANDIDATE", "verdict": "BLOCKED",
                "error": f"no candidate image at {path} -- nothing to QA"}
    if not base["visual_qa_provider"]["available"]:
        # Pending, never approved: an unscored candidate that sat on disk is
        # exactly the failure mode this lifecycle exists to make impossible.
        return {**base, "status": PENDING_VISUAL_QA, "verdict": "PENDING",
                "error": "no authorized visual-QA provider -- the candidate stays pending and the "
                         "story state is unchanged; a file on disk is not an approval"}
    if scores is None:
        return {**base, "status": PENDING_VISUAL_QA, "verdict": "PENDING",
                "error": "no QA scores supplied -- nothing was checked, so nothing is approved"}
    qa = qa_result(scores, notes=notes)
    # A room-continuity PASS needs a canonical room reference to have been
    # judged against (brand/room_references). A rejection is always recordable.
    room = room_references.gate_for_lock(lock, slot)
    base["room_reference"] = room
    if qa["verdict"] == "APPROVED" and not room["allowed"]:
        return {**base, "status": room["status"], "verdict": "BLOCKED", "qa": qa,
                "error": f"environment_continuity PASS refused: {room['reason']}"}
    outcome = record_qa(state, record, qa, slide_ref=str(slide_ref or "").strip() or path.name)
    if write:
        base["state_file"] = str(save_state(state, index, root=root))
    return {**base, "status": outcome["status"], "verdict": qa["verdict"], "qa": qa,
            "approved": qa["verdict"] == "APPROVED", "state_mutated": True,
            "story_state": dict(state.get("story_state") or {}),
            "environment_lock_status": state["environment_lock"].get("status"),
            "outcome": outcome}


def founder_approval_problems(lock: dict, state: dict, entry: dict, *, index: int | None = None,
                              root: Path | None = None) -> list[str]:
    """Why this founder approval may NOT be applied. A founder saying "the image
    is good" replaces the visual-QA score, never the structural gates: the slide
    order, the retry ceiling and -- for a real (non-simulated) approval -- the
    existence of the image file that is about to become the environment master."""
    slot, ref = entry.get("slide_index"), str(entry.get("slide_ref") or "").strip()
    problems = []
    if not ref:
        problems.append(f"slide {slot} founder approval has no slide_ref -- the approved image file "
                        "IS the environment master, so there is nothing to approve")
    try:
        record = plan_record(lock, slot)
    except ContinuityError as exc:
        return problems + [str(exc)]
    if slot > 1 and approved_ref(state, slot - 1) is None:
        problems.append(f"slide {slot} cannot be approved before slide {slot - 1} -- the story "
                        "advances in order")
    if ref and not entry.get("simulated") and index is not None:
        path = carousel_handoff.package_dir(index, root) / ref
        if not path.is_file():
            problems.append(f"no image at {path} -- a founder approval names a real candidate file")
    del record
    return problems


def apply_founder_approvals(lock: dict, state: dict, *, index: int | None = None,
                            root: Path | None = None) -> list[dict]:
    """Founder approvals declared in the story lock, applied through the SAME
    approval path a QA pass uses. Nobody -- founder or model -- hand-maintains
    story_state.json; the lock is the founder-editable surface and the
    orchestrator owns the state file. An entry marked `simulated` registers the
    approval for planning only and makes the whole story refuse generation.

    Only the slides the founder actually named are touched, so approving slide 1
    never advances slide 2, and re-running is a no-op once an entry is applied."""
    applied = []
    for entry in lock.get("founder_approvals") or []:
        slot, ref = entry.get("slide_index"), str(entry.get("slide_ref") or "").strip()
        if not ref or approved_ref(state, slot) is not None:
            continue
        problems = founder_approval_problems(lock, state, entry, index=index, root=root)
        if problems:
            raise ContinuityError(f"founder approval for slide {slot} refused: "
                                  + "; ".join(problems))
        applied.append({**approve_slide(state, plan_record(lock, slot), slide_ref=ref,
                                        simulated=bool(entry.get("simulated")),
                                        approved_by=entry.get("approved_by", "founder")),
                        "source": "story_lock.founder_approvals",
                        "approval_kind": "founder_explicit",
                        "visual_qa_scorer_used": False,
                        "note": entry.get("note", "")})
    return applied


# --- prompt compilation -------------------------------------------------------
def _sanitise(text: str, layer: str, conflicts: list[dict]) -> str:
    """Drop a lower-authority clause that contradicts an authority above it
    (luxury interiors, cinematic/editorial styling, lingerie, magic effects)
    instead of letting a provider 'balance' the contradiction. A clause that
    PROHIBITS the same thing is kept -- that is the policy, not a violation."""
    lines = []
    for line in str(text or "").split("\n"):
        kept = []
        for clause in re.split(r"(?<=[;.])\s+", line):
            hit = _DRIFT.search(clause)
            if hit and not _PROHIBITION.search(clause):
                conflicts.append({"layer": layer, "phrase": hit.group(0), "clause": clause.strip(),
                                  "resolution": "dropped -- lower authority than the environment "
                                                "master and the public-social policy"})
                continue
            kept.append(clause)
        lines.append(" ".join(c for c in kept if c.strip()).strip())
    return "\n".join(line for line in lines if line)


def _identity_layer() -> str:
    spec = carousel_handoff.load_identity_spec()
    anchors = "; ".join(f"{k}: {v}" for k, v in carousel_handoff.IDENTITY_ANCHORS.items())
    return (f"{spec['base_generation_prompt']['identity_block']}\n"
            f"{carousel_handoff.ADULT_AGE_LOCK}\n"
            f"Identity anchors -- {anchors}\n"
            "A previously generated slide informs WHAT IS HAPPENING. It is never evidence for "
            "who Nyx is: on any conflict, the canonical master reference wins.")


def _story_facts_layer(record: dict) -> str:
    return ("\n".join([
        f"Slide {record['slide_index']} of {record['total_slides']} -- "
        f"{record['narrative_role']} ({record['role']}).",
        f"Story beat: {record['story_beat']}",
        f"New information this slide reveals: {record['new_information_revealed']}",
        f"Visual action: {record['visual_action']}",
        "Required continuity: " + "; ".join(record.get("required_continuity") or []),
    ]))


def _environment_layer(state: dict) -> str:
    env = state.get("environment_lock") or {}
    wardrobe = state.get("wardrobe_lock") or {}
    anchors = "\n".join(f"- {a.get('description', '')}" for a in env.get("anchors") or [])
    return "\n".join([
        f"{env.get('summary', '')} (authority: {env.get('status', 'PROVISIONAL')}"
        + (f", frozen by the approved slide {env.get('source_slide')} image"
           if env.get("status") == "LOCKED" else ", from the approved slide 1 description") + ")",
        "Every one of these exists in this room, in the same geometry, in every slide. A closer "
        "shot may show some of them only partly or softly in the background, or leave them out "
        "of frame -- out of frame is not removal. None may be contradicted, moved or redesigned, "
        "and the room is never staged as a showcase to fit them all in:",
        anchors,
        env.get("geometry_rule", ""),
        env.get("vocabulary_rule", ""),
        f"Wardrobe: {wardrobe.get('outfit', '')}",
        f"Hair: {wardrobe.get('hair', '')}",
        f"Time: {wardrobe.get('time_of_day', '')}",
        "Never: " + "; ".join(list(env.get("forbidden") or []) + list(wardrobe.get("forbidden") or [])),
    ])


def _state_layer(state: dict, conflicts: list[dict]) -> str:
    story = state.get("story_state") or {}
    source = story.get("updated_from_slide")
    props = "; ".join(f"{k}: {v}" for k, v in (story.get("props") or {}).items())
    body = "\n".join([
        f"Nyx is: {story.get('character_position', '')}; {story.get('character_action', '')}",
        f"Props: {props}",
        f"Time: {story.get('time', '')}. Wardrobe: {story.get('wardrobe', '')}. "
        f"Hair: {story.get('hairstyle', '')}",
        "Environment changes so far: "
        + ("; ".join(story.get("environment_changes") or []) or "none -- the room is unchanged"),
        f"What is known in the story: {story.get('narrative_knowledge', '')}",
    ])
    header = (f"Carried from the APPROVED slide {source}."
              if source else "Story opening -- nothing approved yet.")
    return f"{header}\n{_sanitise(body, 'approved_story_state', conflicts)}"


def _previous_slide_layer(state: dict, record: dict, conflicts: list[dict]) -> str:
    previous = [f for f in state.get("composition_history") or []
                if f.get("slide_index") == record["slide_index"] - 1]
    if not previous:
        return ("No approved previous slide -- this slide establishes the scene described by the "
                "environment master above.")
    fp = previous[0]
    shot = ", ".join(f"{k}: {fp.get(k, '')}" for k in CAMERA_FIELDS if fp.get(k))
    body = (f"Slide {fp['slide_index']} was shot as: {shot}. It looked like: {fp.get('composition', '')}. "
            "Continue the action from it. It defines neither the room nor the face, and this slide "
            "must not reuse its camera.")
    return _sanitise(body, "previous_slide_visual_state", conflicts)


def _styling_layer(record: dict, state: dict, conflicts: list[dict]) -> str:
    camera = "; ".join(f"{k}: {(record.get('camera') or {}).get(k, '')}" for k in CAMERA_FIELDS)
    variable = _sanitise(f"Composition: {record.get('composition', '')}", "creative_styling",
                         conflicts)
    variable_camera = _sanitise(f"Camera: {camera}", "creative_styling", conflicts)
    return "\n".join([
        variable, variable_camera,
        "Public social default: " + "; ".join(PUBLIC_SOCIAL_POLICY),
        "Must not repeat: " + "; ".join(forbidden_repetition(state, record)),
        "Styling is the lowest authority: anything here that conflicts with a section above is "
        "dropped, never blended.",
    ])


def negative_constraints(state: dict) -> list[str]:
    env = state.get("environment_lock") or {}
    wardrobe = state.get("wardrobe_lock") or {}
    return (list(carousel_handoff.NEGATIVE_CONSTRAINTS)
            + list(carousel_handoff.NEGATIVE_IDENTITY)
            + [f"never {f}" for f in env.get("forbidden") or []]
            + [f"never {f}" for f in wardrobe.get("forbidden") or []])


def reference_selection(persona: dict, state: dict, record: dict, *, index: int,
                        root: Path | None = None) -> list[dict]:
    """Which images the provider is handed, and what each one is allowed to
    decide. The identity master is attached to EVERY slide; the approved slide 1
    is attached as the environment master to every later slide, so continuity
    never depends on a chain of previous images."""
    refs = [{**r, "authority": 1, "never_identity": False,
             "decides": "identity only -- face, eyes, ears, tail, marks"}
            for r in carousel_handoff.canonical_references(persona, index=index, root=root)]
    env = state.get("environment_lock") or {}
    previous_ref = approved_ref(state, record["slide_index"] - 1)
    if env.get("status") == "LOCKED" and env.get("source_slide_ref"):
        doubles = bool(previous_ref) and previous_ref["value"] == env["source_slide_ref"]
        refs.append({"role": "environment_reference", "kind": "local", "base": "package",
                     "value": env["source_slide_ref"], "authority": 3, "never_identity": True,
                     "decides": "room geometry, decor, lamp light and wardrobe -- never identity",
                     "simulated": bool(env.get("simulated")),
                     "note": f"the APPROVED slide {env.get('source_slide')} of this story"
                             + (" -- also the immediately previous slide, so it carries the "
                                "current action state as well" if doubles else "")})
    if previous_ref and previous_ref["value"] != env.get("source_slide_ref"):
        refs.append({"role": "previous_slide_reference", "kind": "local", "base": "package",
                     "value": previous_ref["value"], "authority": 5, "never_identity": True,
                     "simulated": bool(previous_ref.get("simulated")),
                     "decides": "what is currently happening -- never identity or the room",
                     "note": f"the APPROVED slide {record['slide_index'] - 1}: current action and "
                             f"prop state only; slide {env.get('source_slide')} remains the "
                             f"environment master"})
    return refs


def compile_prompt(persona: dict, state: dict, record: dict) -> dict:
    """The compiled provider prompt. The system owns this: no founder and no
    model hand-writes a per-slide prompt, and the precedence is stated in the
    prompt itself so a rewriting provider cannot reorder it quietly."""
    conflicts: list[dict] = []
    text = {
        "canonical_identity": _identity_layer(),
        "explicit_story_facts": _story_facts_layer(record),
        "environment_master": _environment_layer(state),
        "approved_story_state": _state_layer(state, conflicts),
        "previous_slide_visual_state": _previous_slide_layer(state, record, conflicts),
        "creative_styling": _styling_layer(record, state, conflicts),
    }
    sections = [{"authority": i + 1, "layer": layer, "title": _LAYER_TITLES[layer],
                 "text": text[layer]} for i, layer in enumerate(PRECEDENCE)]
    negatives = negative_constraints(state)
    prompt = "\n\n".join(
        [f"{s['title']}\n{s['text']}" for s in sections]
        + ["7. NEGATIVE CONSTRAINTS\n" + "; ".join(negatives),
           "8. TEXT POLICY\nGenerate clean art with NO text baked in. The overlay line is applied "
           "afterwards by PS-05 (carousel_handoff.apply_overlays); leave the "
           f"{_placement_wording(record)} readable.",
           "9. PRECEDENCE ON CONFLICT\n" + " > ".join(PRECEDENCE)])
    overlay = str(record.get("overlay_copy") or "").strip()
    if overlay and overlay.lower() in prompt.lower():
        raise ContinuityError(
            f"slide {record['slide_index']} overlay copy leaked into the compiled prompt -- art is "
            "generated clean and the approved overlay is applied afterwards")
    return {"prompt": prompt, "sections": sections, "negative_constraints": negatives,
            "precedence": list(PRECEDENCE), "precedence_conflicts": conflicts,
            "sanitised_layers": list(_SANITISED_LAYERS)}


# --- dry run ------------------------------------------------------------------
def dry_run(item: dict, persona: dict, *, index: int, slot: int, root: Path | None = None,
            lock: dict | None = None, write: bool = True) -> dict:
    """Everything a generation WOULD use, and no generation: the structured
    plan, the compiled prompt, the reference selection, the QA expectations and
    the cost/retry state. This is the mode the story runs in until the founder
    authorizes a provider."""
    lock = lock or load_story_lock(index)
    if lock is None:
        raise ContinuityError(f"no story lock for content_items[{index}]")
    problems = plan_problems(lock, item)
    if problems:
        raise ContinuityError(f"story plan is not generatable: {'; '.join(problems)}")
    record = plan_record(lock, slot)
    state = ensure_state(lock, index, root=root)
    generation = lock.get("generation") or {}
    simulated = [r["slide_index"] for r in state["approved_slide_refs"] if r.get("simulated")]
    if generation.get("enabled") and simulated:
        raise ContinuityError(
            f"slides {simulated} are SIMULATED fixture approvals -- they exist to compile and "
            "inspect later slides, never to authorize drawing one; approve the real images "
            "through visual QA before generation is enabled")
    active = project_active_context(state, lock, root=root)
    gate = readiness(active, record, root=root)
    if not gate["ready"]:
        raise NotReady(gate)
    compiled = compile_prompt(persona, active, record)
    directory = carousel_handoff.package_dir(index, root)
    master = render_masters.primary_master(lock)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "mode": "DRY_RUN",
        "generated": False,
        "story_id": lock["story_id"],
        "item_id": lock.get("item_id", f"content_items[{index}]"),
        "item_index": index,
        "slide_index": slot,
        "total_slides": record["total_slides"],
        "lock_file": lock.get("lock_file", ""),
        "brief_sync": brief_sync(lock, item),
        "story_plan": record,
        "identity_lock": {"source": (lock.get("identity_lock") or {}).get(
                              "source", "brand/nyx_identity/identity_spec.json"),
                          "anchors": dict(carousel_handoff.IDENTITY_ANCHORS),
                          "age_lock": carousel_handoff.ADULT_AGE_LOCK},
        "environment_lock": state["environment_lock"],
        "story_state": state["story_state"],
        "approved_slide_refs": state["approved_slide_refs"],
        "composition_history": state["composition_history"],
        "readiness": gate,
        "visual_qa_provider": visual_qa_provider(lock),
        "generation_request": {
            "enabled": bool(generation.get("enabled")),
            "intended_provider": generation.get("intended_provider"),
            "intended_model": generation.get("intended_model"),
            "paid_generation_authorized": False,
            "prompt": compiled["prompt"],
            "prompt_sections": compiled["sections"],
            "precedence": compiled["precedence"],
            "precedence_conflicts": compiled["precedence_conflicts"],
            "negative_constraints": compiled["negative_constraints"],
            "references": reference_selection(persona, active, record, index=index, root=root),
            "text_in_image": "none -- generate clean art, no letters of any kind",
            "overlay_copy": record["overlay_copy"],
            "overlay_safe_zone": record["overlay_safe_zone"],
            "output": {"filename": carousel_handoff.slide_filename(index, slot),
                       "directory": {"base": "package", "value": "."},
                       "width": master["width"], "height": master["height"],
                       "aspect_ratio": master["aspect"], "render_master": master["id"],
                       "additional_masters": [
                           {"render_master": m["id"], "aspect_ratio": m["aspect"],
                            "width": m["width"], "height": m["height"],
                            "filename": carousel_handoff.slide_filename(
                                index, slot, render_masters.file_suffix(lock.get("target_platform"), m["id"])),
                            "platforms": m["platforms"]}
                           for m in map(render_masters.master, render_masters.masters_for_lock(lock)[1:])]},
        },
        "qa_result": None,
        "qa_expectations": qa_expectations(lock, record, active),
        "retry_state": state["retry_state"],
        "cost_state": {**state["cost_state"], "this_call_cost_usd": 0, "this_call_credits": 0},
        "blocked_reason": generation.get("authorization_note", ""),
        "built_at": _now(),
    }
    if write:
        directory.mkdir(parents=True, exist_ok=True)
        out = directory / f"{Path(payload['generation_request']['output']['filename']).stem}.dryrun.json"
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        payload["dry_run_file"] = str(out)
    return payload
