"""Optional Reddit-native variant stage.

Input: one canonical story lock (brand/story_locks/*.json) plus the canonical
identity spec (brand/nyx_identity/identity_spec.json). Output: subreddit-specific
SFW drafts in nyx_reddit/queue/, each with its own image brief (never the public
carousel files), a context-first body and a plain AI-fiction disclosure.

`optional_variant` is the only entry point the main pipeline calls. It never
raises and never changes the caller's result: Reddit can never block production.
Every fact in a draft body is derived from the lock, and listed under `facts`
with the lock field it came from, so the editor can check each claim.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import gate
from .store import Store, today

DISCLOSURE = ("Nyx Emberfall is a fictional adult character and these images are AI-generated. "
              "Not a real person.")
REDDIT_ASPECT = "4:5"
ID_SPEC = Path("brand/nyx_identity/identity_spec.json")
PUBLIC_TONES = {"public"}
PUBLIC_VISUAL_POLICIES = {"public_social"}
LOCKED_STATUSES = {"packet_ready", "posted", "removed"}   # never regenerated over


def _sub_slug(community: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", community.lower().removeprefix("r/"))


def _plan(lock: dict) -> list[dict]:
    return [s for s in lock.get("story_plan") or [] if s.get("beat")]


def _pick_frames(plan: list[dict]) -> list[dict]:
    """Three frames: the hook, the escalation and the payoff, when they exist.
    A short set, not the whole carousel."""
    by_role: dict[str, dict] = {}
    for s in plan:
        by_role.setdefault(s.get("role", ""), s)
    picked = [by_role.get(r) for r in ("HOOK", "ESCALATION", "PAYOFF")]
    picked = [s for s in picked if s]
    return picked or plan[:3]


def _episode_number(episode_id: str) -> str:
    m = re.search(r"e(\d+)$", episode_id or "", re.I)
    return str(int(m.group(1))) if m else episode_id


def _tool(lock: dict) -> str:
    return str((lock.get("generation") or {}).get("intended_model") or "").strip()


def _lessons(lock: dict) -> list[tuple[str, str]]:
    """Plain-language lessons backed by the lock's own revision history."""
    out = []
    for i, rev in enumerate(lock.get("canon_revisions") or []):
        blob = json.dumps(rev).lower()
        src = f"canon_revisions[{i}]"
        if "demoted" in blob and "6 of 7" in blob:
            out.append(("A local model I tested failed 6 of 7 story beats, so I dropped it for this series.", src))
        elif "wardrobe" in blob and "match" in blob:
            out.append(("The written outfit notes drifted from what the images actually showed, so I corrected "
                        "the notes to match the images rather than regenerating to match stale notes.", src))
    if (lock.get("identity_lock") or {}).get("never_from_previous_slides"):
        out.append(("Earlier frames can tell the model what is happening, never who she is. Only the reference "
                    "sheet decides the face, otherwise the drift compounds frame to frame.",
                    "identity_lock.never_from_previous_slides"))
    return out


def _image_brief(lock: dict, identity: dict, frame: dict, n: int) -> dict:
    env = lock.get("environment_lock") or {}
    wardrobe = lock.get("wardrobe_lock") or {}
    block = (identity.get("base_generation_prompt") or {}).get("identity_block", "")
    negative = list(identity.get("negative_prompt") or []) + list(env.get("forbidden") or [])
    prompt = "\n\n".join([
        block,
        f"SCENE:\n{env.get('summary', '')}\nBeat: {frame.get('beat', '')}",
        f"WARDROBE:\n{wardrobe.get('outfit', '')}; hair: {wardrobe.get('hair', '')}",
        f"CAMERA:\n{REDDIT_ASPECT} portrait crop for a Reddit feed. Re-compose this beat; do not reproduce "
        f"the public carousel frame ({frame.get('composition', '')}). Close and partial, never a room tour.",
        f"LIGHTING:\n{wardrobe.get('time_of_day', '')}",
        "TEXT:\nNo overlay text, no captions, no letters anywhere in the image.",
        "NEGATIVE:\n- " + "\n- ".join(negative),
    ])
    return {"role": f"frame_{n}_{str(frame.get('role', '')).lower()}",
            "source_slide": frame.get("slide_index"),
            "beat": frame.get("beat", ""),
            "aspect_ratio": REDDIT_ASPECT,
            "prompt": prompt,
            "reference": "brand/nyx_identity/reference_primary.webp",
            "path": None, "sfw": None, "identity_qa": None, "origin": "reddit_native"}


# One template per subreddit. Each returns (draft_fields, None) or (None, skip_reason).

def _process_post(lock, identity, frames):
    env = lock.get("environment_lock") or {}
    anchors = env.get("anchors") or []
    tool = _tool(lock)
    hook = frames[0].get("beat", "") if frames else ""
    lessons = _lessons(lock)
    thread, ep = lock.get("story_thread", ""), lock.get("episode_id", "")
    body = "\n\n".join(filter(None, [
        f"I'm making a serialised visual story around one original character, Nyx Emberfall. {DISCLOSURE}",
        f"These {len(frames)} frames are from episode {_episode_number(ep)} of the thread "
        f"\"{thread}\": {hook}.",
        "What I'm actually testing is continuity across a series, not single-image polish:\n"
        "- one locked reference sheet (face, eyes, fox ears, one tail, which cheek the beauty mark is on) "
        "that overrides everything, including earlier frames\n"
        f"- one locked room with {len(anchors)} fixed anchors "
        f"({', '.join(a['id'].replace('_', ' ') for a in anchors[:5])}, ...) that must match in every frame; "
        "only the camera and the character move\n"
        "- the whole story planned beat by beat before any image is generated",
        ("What broke so far:\n" + "\n".join(f"- {text}" for text, _ in lessons)) if lessons else "",
        f"Made with {tool}. Happy to share the prompt structure." if tool else "Happy to share the prompt structure.",
        "How do you keep a location consistent across a long series without it slowly redecorating itself?",
    ]))
    facts = [("story_thread", "story_thread"), ("episode", "episode_id"), ("hook beat", "story_plan[HOOK].beat"),
             (f"{len(anchors)} anchors", "environment_lock.anchors"), ("tool", "generation.intended_model")]
    facts += [(text, src) for text, src in lessons]
    return {
        "kind": "native_post",
        "angle": "value-first process: series continuity workflow and what broke",
        "title": f"Same character, same room, {len(frames)} frames of an ongoing story: "
                 "what's kept her consistent so far (and what broke)",
        "body": body,
        "facts": [{"claim": c, "source": s} for c, s in facts],
    }, None


def _story_post(lock, identity, frames):
    thread = lock.get("story_thread", "")
    beats = [b[:1].upper() + b[1:] + ("" if b.endswith(".") else ".") for b in (f.get("beat", "") for f in frames)]
    overlay = [s.get("overlay_copy") for s in lock.get("story_plan") or [] if s.get("overlay_copy")]
    question = overlay[-1] if overlay and overlay[-1].endswith("?") else "What would you do next?"
    body = "\n\n".join([
        f"\"{thread}\", a small ongoing story. {DISCLOSURE}",
        "\n".join(f"{i + 1}. {b}" for i, b in enumerate(beats)),
        f"She hasn't opened it yet. {question[0].upper() + question[1:]}",
    ])
    return {
        "kind": "native_post",
        "angle": "story-first micro-fiction; backup only if r/aiArt fails verification",
        "title": f"{overlay[0][0].upper() + overlay[0][1:] if overlay else thread} "
                 f"(a short AI-made story, {len(frames)} frames)",
        "body": body,
        "backup_for": "r/aiArt",
        "facts": [{"claim": "beats", "source": "story_plan[*].beat"},
                  {"claim": "closing question", "source": "story_plan[PAYOFF].overlay_copy"}],
    }, None


def _video_post(lock, identity, frames):
    if not (lock.get("video") or lock.get("video_asset")):
        return None, "episode concept has no finished short narrative video"
    return None, "video template not built yet; draft by hand"


def _tool_sub(tool_keyword):
    def template(lock, identity, frames):
        if tool_keyword not in _tool(lock).lower():
            return None, f"images are made with {_tool(lock) or 'an unknown tool'}, not {tool_keyword}"
        return None, "tool-specific template not built yet; draft by hand"
    return template


TEMPLATES = {
    "r/aiArt": _process_post,
    "r/AIGeneratedArt": _story_post,
    "r/aivideo": _video_post,
    "r/midjourney": _tool_sub("midjourney"),
    "r/StableDiffusion": _tool_sub("stable diffusion"),
}


def build(lock: dict, identity: dict, matrix: dict) -> tuple[list[dict], list[dict]]:
    drafts, skipped = [], []
    story_id = lock.get("story_id", "unknown")
    if lock.get("tone") not in PUBLIC_TONES or lock.get("visual_policy") not in PUBLIC_VISUAL_POLICIES:
        return [], [{"community": "*", "reason": f"{story_id} is not a public/SFW episode "
                                                 f"(tone={lock.get('tone')}, visual_policy={lock.get('visual_policy')})"}]
    frames = _pick_frames(_plan(lock))
    if not frames:
        return [], [{"community": "*", "reason": f"{story_id} has no story_plan beats"}]
    for community, comm in matrix.get("communities", {}).items():
        status = comm.get("status")
        if status == "excluded":
            continue                                 # the no-post list is never drafted for
        if status == "observe_only" or (status == "unverified" and comm.get("provisional") == "observe_only"):
            skipped.append({"community": community, "reason": "observe/comment only"})
            continue
        template = TEMPLATES.get(community)
        if template is None:
            skipped.append({"community": community, "reason": "no template for this community"})
            continue
        fields, reason = template(lock, identity, frames)
        if fields is None:
            skipped.append({"community": community, "reason": reason})
            continue
        drafts.append({
            "id": f"{story_id}__{_sub_slug(community)}",
            "community": community,
            "story_id": story_id,
            "episode_id": lock.get("episode_id"),
            "lock_file": lock.get("lock_file", ""),
            "created_on": today().isoformat(),
            "status": "draft",
            "nsfw": False,
            "disclosure": DISCLOSURE,
            "flair": None,
            "links": [],
            "images": [_image_brief(lock, identity, f, i + 1) for i, f in enumerate(frames)],
            **fields,
        })
    return drafts, skipped


def prepare(lock_path, store: Store | None = None) -> dict:
    store = store or Store()
    path = store.resolve(lock_path)
    lock = json.loads(Path(path).read_text())
    lock.setdefault("lock_file", str(lock_path))
    identity = json.loads(store.resolve(ID_SPEC).read_text())
    drafts, skipped = build(lock, identity, store.communities())
    written = []
    for d in drafts:
        try:
            existing = store.draft(d["id"])
        except KeyError:
            existing = None
        if existing and existing.get("status") in LOCKED_STATUSES:
            skipped.append({"community": d["community"], "reason": f"draft {d['id']} is {existing['status']}; not overwritten"})
            continue
        store.save_draft(d)
        written.append(d)
    # Gate after all drafts exist, so the similarity check sees the whole queue.
    results = []
    for d in written:
        d["gate"] = gate.evaluate(store, d)
        store.save_draft(d)
        results.append({"id": d["id"], "community": d["community"], "decision": d["gate"]["decision"],
                        "blocks": len(d["gate"]["blocks"])})
    return {"ok": True, "story_id": lock.get("story_id"), "drafts": results, "skipped": skipped}


def optional_variant(lock_path, store: Store | None = None) -> dict:
    """Pipeline hook. Never raises; a failure here is reported, never propagated."""
    try:
        return prepare(lock_path, store)
    except Exception as exc:  # noqa: BLE001 -- by design: Reddit must never block production
        return {"ok": False, "optional": True, "error": f"{type(exc).__name__}: {exc}"}
