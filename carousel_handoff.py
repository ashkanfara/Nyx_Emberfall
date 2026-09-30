"""ChatGPT image-generation handoff for story_carousel items.

Why this exists (founder decision 2026-09-21): multi-slide carousels were the
only format whose generation was hard-wired to paid Higgsfield image calls
(stages.content_director_generate -> Higgsfield MCP tools). A 0.12-credit
soul_2 test slide drifted semi-photoreal and missed its beat, and the standing
ceiling has 0.38 credits left -- so paying per slide for a 5-slide story is
both wrong creatively and unaffordable. ChatGPT product image generation is
already available to the founder at no incremental PS-05 cost, but it is a
PRODUCT, not an API: nothing in this project can invoke it unattended (see
`UNATTENDED` below). So this module is deliberately NOT a new pipeline -- it is
the smallest provider/handoff adapter around the EXISTING brief -> generate ->
QA -> lifecycle -> Metricool flow:

  brief (stages.story_carousel_problems)   unchanged
  -> build_package()  deterministic, continuity-first prompt package
  -> [ChatGPT generates the slides, files land in the package directory]
  -> ingest_slides()  all-or-nothing, then the existing ASSET_GENERATED state
  -> existing content_director_creative_qa / Metricool carousel publishing

Art is generated WITHOUT baked-in text; the approved short overlays are
applied deterministically afterwards (overlay_plan/apply_overlays) so copy can
change without regenerating art and so no model has to render typography.

Identity note (2026-09-22): Nyx's visual identity is now canonically defined
in brand/nyx_identity/identity_spec.json (founder-provided photorealistic
master references, superseding the prior anime/JRPG spec) -- see
load_identity_spec() below. content_items[20]'s generation_package.json was
rebuilt against this identity the same day (a zero-spend local write); its
prior off-model files were archived under
generated_assets/carousel_item20/superseded_v1_anime/, not deleted.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import render_masters as rm
import stages
import state as st

PROVIDER = "chatgpt_handoff"
# Honest capability record -- never let a caller assume this is an API.
UNATTENDED = False
USAGE_CONSTRAINTS = ("ChatGPT product image generation, used interactively by the founder or by a "
                     "ChatGPT-side relay worker. No OpenAI API key, no PS-05 billing, no browser "
                     "credential scraping. Not unlimited: it is bounded by the founder's own "
                     "ChatGPT plan limits, so it is 'no incremental cost', not 'free forever'.")

PROJECT_ROOT = Path(__file__).resolve().parent
ASSETS_ROOT = PROJECT_ROOT / "generated_assets"
PACKAGE_FILENAME = "generation_package.json"
ACCEPTED_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
# Magic bytes, so a truncated download or a stray text file can never be
# ingested as a slide and silently published.
_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF")

NEGATIVE_CONSTRAINTS = (
    "no text, no letters, no captions, no speech bubbles, no watermark, no signature",
    "highly realistic / photorealistic only -- no anime, cartoon, cel-shaded or 2D-illustration styling",
    "no comic panel grid -- one single continuous image per slide",
    "no character redesign: same face, ears, hair, eyes and beauty mark every slide (outfit and "
    "environment are the story's own continuity fields, not identity)",
    "no new people, no extra characters, no crowd",
)


# --- Nyx identity pack (provider-independent) --------------------------------
# CANONICAL SOURCE: brand/nyx_identity/identity_spec.json. That file -- not
# this module -- is the single source of truth for Nyx's visual identity
# (founder-locked 2026-09-22, superseding the prior anime/JRPG spec, which is
# preserved for provenance inside the JSON itself). Loaded once at import time
# so every provider (ChatGPT today, Higgsfield or another one later) is handed
# an identical lock, and so QA can name the exact anchor that drifted -- do
# NOT hand-edit the constants below independently of the JSON; edit the JSON
# and re-import.
IDENTITY_SPEC_PATH = PROJECT_ROOT / "brand" / "nyx_identity" / "identity_spec.json"


def load_identity_spec(path: Path | None = None) -> dict:
    p = path or IDENTITY_SPEC_PATH
    if not p.is_file():
        raise HandoffError(
            f"canonical Nyx identity spec not found at {p} -- this file is required, never "
            "reconstructed from a hardcoded fallback (that is exactly how identity drift happens)")
    return json.loads(p.read_text())


def _build_identity_constants(spec: dict) -> dict:
    """Derive the same shape this module has always exposed (MIN_CHARACTER_AGE,
    ADULT_AGE_LOCK, IDENTITY_ANCHORS, NEGATIVE_IDENTITY, CANONICAL_PORTRAIT,
    MASTER_REFERENCE_URL) from the canonical JSON, so every downstream
    function in this file (identity_pack, _image_prompt, canonical_references)
    needs no change when the spec is revised."""
    char = spec["character"]
    traits = spec["locked_traits"]
    refs = spec["reference_hierarchy"]
    primary = refs[0]
    min_age = int(char["minimum_age"])
    age_lock = (
        f"{char['name']} is an ADULT woman, {min_age}+ ({char['age_appearance']} appearance). "
        "Adult face, adult proportions and adult height in every image, including close-ups and "
        "full-body shots. Never a minor, never teen- or child-coded, never de-aged, never chibi.")
    anchors = {
        "face": traits["facial_identity"],
        "eyes": traits["eyes"],
        "hair": traits["hair"],
        "ears": traits["fox_ears"],
        "tail": traits["tail"],
        "skin_and_marks": f"{traits['skin']}; {traits['beauty_mark']}",
        "body": traits["body"],
        "realism": traits["realism_level"],
    }
    negative_identity = tuple(spec["prohibited_drift"]) + (
        "suggestive and fully clothed: no nudity, no exposed nipples or genitals, no sexual acts, "
        "no underwear-only reveal -- public acquisition content teases, it never goes explicit",
    )
    return {
        "spec": spec,
        "MIN_CHARACTER_AGE": min_age,
        "ADULT_AGE_LOCK": age_lock,
        "IDENTITY_ANCHORS": anchors,
        "NEGATIVE_IDENTITY": negative_identity,
        # The primary master reference, on disk and hosted -- referenced, never regenerated.
        "CANONICAL_PORTRAIT": PROJECT_ROOT / primary["project_file"],
        "CANONICAL_PORTRAIT_SECONDARY": PROJECT_ROOT / refs[1]["project_file"],
        "MASTER_REFERENCE_URL": primary["hosted_url"],
        # generation_references (founder decision 2026-09-25): the full reference
        # sheets' 3/4 and profile panels show human ears and printed text that an
        # image model copies -- that is exactly what caused item20 slide 1's first
        # attempt to be rejected. This is the DERIVED, generation-facing crop
        # (order 1: canonical face closeup) a provider should actually be shown;
        # CANONICAL_PORTRAIT remains the identity authority and QA comparison
        # source, never the generation input, when this section is present.
        "GENERATION_SAFE_REFERENCE": (
            PROJECT_ROOT / spec["generation_references"]["images"][0]["file"]
            if spec.get("generation_references", {}).get("images")
            else PROJECT_ROOT / primary["project_file"]),
        # order 2 in generation_references.images: "FRONT panel, top of head
        # down to the eyes -- shows both canonical fox ears". Verified by
        # direct visual inspection 2026-09-26: order 1 (face closeup) crops
        # inside the cheek line and excludes the ear/top-of-head region
        # entirely; this is the only generation-facing crop that actually
        # shows the ears. Falls back to GENERATION_SAFE_REFERENCE (never to
        # the full, ear-and-text-carrying reference sheet) if this story's
        # identity spec doesn't carry a second generation-facing image.
        "GENERATION_SAFE_REFERENCE_HEAD": (
            PROJECT_ROOT / spec["generation_references"]["images"][1]["file"]
            if len(spec.get("generation_references", {}).get("images") or []) > 1
            else (PROJECT_ROOT / spec["generation_references"]["images"][0]["file"]
                  if spec.get("generation_references", {}).get("images")
                  else PROJECT_ROOT / primary["project_file"])),
    }


_IDENTITY = _build_identity_constants(load_identity_spec())
MIN_CHARACTER_AGE = _IDENTITY["MIN_CHARACTER_AGE"]
ADULT_AGE_LOCK = _IDENTITY["ADULT_AGE_LOCK"]
IDENTITY_ANCHORS = _IDENTITY["IDENTITY_ANCHORS"]
NEGATIVE_IDENTITY = _IDENTITY["NEGATIVE_IDENTITY"]
CANONICAL_PORTRAIT = _IDENTITY["CANONICAL_PORTRAIT"]
CANONICAL_PORTRAIT_SECONDARY = _IDENTITY["CANONICAL_PORTRAIT_SECONDARY"]
MASTER_REFERENCE_URL = _IDENTITY["MASTER_REFERENCE_URL"]
GENERATION_SAFE_REFERENCE = _IDENTITY["GENERATION_SAFE_REFERENCE"]
GENERATION_SAFE_REFERENCE_HEAD = _IDENTITY["GENERATION_SAFE_REFERENCE_HEAD"]


class HandoffError(ValueError):
    """A refusal that must never be worked around (e.g. a partial carousel)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def package_dir(index: int, root: Path | None = None) -> Path:
    return (root or ASSETS_ROOT) / f"carousel_item{index}"


# --- relocatable paths -------------------------------------------------------
# A package is handed to a worker outside this process (and this machine), so it
# records no absolute paths: every stored path names the base it is relative to
# -- "package" (the directory generation_package.json itself sits in) or
# "project" (this project root) -- and is resolved back through
# resolve_reference(). Moving the project or the package directory then keeps
# every reference valid.
def _base_dir(base: str, index: int | None, root: Path | None) -> Path:
    if base == "project":
        return PROJECT_ROOT
    if base == "package":
        if index is None:
            raise HandoffError("a package-relative path needs the item index")
        return package_dir(index, root)
    raise HandoffError(f"unknown path base {base!r}")


def _relative(path: Path, base: str, index: int | None = None, root: Path | None = None) -> str:
    directory = _base_dir(base, index, root)
    try:
        return path.relative_to(directory).as_posix()
    except ValueError as exc:
        raise HandoffError(f"{path} is not inside the {base} base {directory}") from exc


def resolve_reference(ref: dict, *, index: int | None = None, root: Path | None = None) -> Path:
    """Turn a stored {"base": ..., "value": ...} path back into a real one."""
    return Path(os.path.normpath(_base_dir(ref.get("base", ""), index, root) / ref.get("value", "")))


def slide_filename(index: int, slide_number: int, suffix: str = "") -> str:
    """An item's PRIMARY master keeps the historical name. A second master
    (the 9:16 TikTok/Fanvue file of a public_multi item) carries
    render_masters.file_suffix() and sits beside it, never overwriting it."""
    return f"item{index}_slide{slide_number}{suffix}.png"


def overlay_filename(index: int, slide_number: int, suffix: str = "") -> str:
    return f"item{index}_slide{slide_number}{suffix}_captioned.png"


def _slides(item: dict) -> list[dict]:
    slides = item.get("slides")
    if not isinstance(slides, list) or not slides:
        raise HandoffError("story_carousel item has no slides")
    return slides


def canonical_references(persona: dict, *, index: int | None = None,
                         root: Path | None = None) -> list[dict]:
    """Pointers to the EXISTING approved Nyx assets -- this never generates a
    new reference. Only references that really exist are listed, so a provider
    is never told to match a file that isn't there. The primary master
    (brand/nyx_identity/identity_spec.json reference_hierarchy[0]) always leads
    when present; persona.reference_image_url is a fallback for providers that
    were only ever pointed at persona, not the identity spec directly."""
    refs = []
    url = persona.get("reference_image_url", "") or MASTER_REFERENCE_URL
    if url:
        refs.append({"role": "identity_reference", "kind": "url", "value": url,
                     "note": "primary master reference (hosted) -- highest authority"})
    if CANONICAL_PORTRAIT.is_file():
        refs.append({"role": "identity_reference", "kind": "local", "base": "project",
                     "value": _relative(CANONICAL_PORTRAIT, "project"),
                     "note": "primary master reference on disk, for providers that take a file upload"})
    if CANONICAL_PORTRAIT_SECONDARY.is_file():
        refs.append({"role": "identity_reference_secondary", "kind": "local", "base": "project",
                     "value": _relative(CANONICAL_PORTRAIT_SECONDARY, "project"),
                     "note": "secondary supporting reference -- reinforces angles only, never "
                             "overrides the primary master on conflict"})
    if index is not None:
        for name, note in (("ref.png", "working copy in this package's directory"),
                           ("ref_small.png", "downscaled copy for upload size limits")):
            path = package_dir(index, root) / name
            if path.is_file():
                refs.append({"role": "identity_reference", "kind": "local", "base": "package",
                             "value": _relative(path, "package", index, root), "note": note})
    return refs


def identity_pack(persona: dict, *, index: int | None = None, root: Path | None = None) -> dict:
    """The provider-independent Nyx identity lock. Character consistency must
    not depend on which provider drew a slide, so exactly this dict is what
    every provider (and every single-slide replacement) is handed."""
    return {
        "character": persona.get("name", "Nyx Emberfall"),
        "adult": True,
        "minimum_age": MIN_CHARACTER_AGE,
        "age_lock": ADULT_AGE_LOCK,
        "identity_lock": persona.get("visual_description", ""),
        "style_lock": persona.get("visual_style", ""),
        "anchors": dict(IDENTITY_ANCHORS),
        "negative_identity": list(NEGATIVE_IDENTITY),
        "canonical_references": canonical_references(persona, index=index, root=root),
        "disclosure": persona.get("disclosure_statement_short", "#AIGenerated"),
    }


def identity_pack_problems(pack: dict) -> list[str]:
    """A package is only worth handing to a provider if the identity lock is
    complete -- an empty persona field or a missing reference asset must stop
    the handoff, not quietly produce five off-model images."""
    probs = [f"missing {k}" for k in ("age_lock", "identity_lock", "style_lock")
             if not str(pack.get(k) or "").strip()]
    probs += [f"missing anchor {k}" for k in IDENTITY_ANCHORS
              if not str((pack.get("anchors") or {}).get(k) or "").strip()]
    if not pack.get("negative_identity"):
        probs.append("missing negative identity constraints")
    if not pack.get("canonical_references"):
        probs.append("no canonical Nyx reference asset available")
    if pack.get("minimum_age", 0) < MIN_CHARACTER_AGE or not pack.get("adult"):
        probs.append(f"age lock is not adult {MIN_CHARACTER_AGE}+")
    return probs


def continuity_sheet_problems(sheet: dict) -> list[str]:
    """The identity pack locks WHO she is; this also checks WHERE she is. An
    empty environment or an empty prop list leaves the provider free to redraw
    the room between slides, which is the other half of a carousel drifting."""
    probs = identity_pack_problems(sheet.get("identity") or {})
    probs += [f"missing {k}" for k in ("aspect_ratio", "environment", "spatial_rule", "text_policy")
              if not str(sheet.get(k) or "").strip()]
    if not sheet.get("outfit_and_props"):
        probs.append("missing outfit/prop continuity")
    if not sheet.get("negative_constraints"):
        probs.append("missing negative style constraints")
    return probs


def package_master(item: dict) -> dict:
    """The canvas this item's slides are generated at: the primary render
    master of its target_platform (4:5 for any public carousel, 9:16 only for
    TikTok/Fanvue). An item's own aspect_ratio can no longer override it."""
    return rm.master(rm.masters_for_platform(item.get("target_platform"))[0])


def continuity_sheet(item: dict, persona: dict, *, index: int | None = None,
                     root: Path | None = None) -> dict:
    """The ONE sheet every slide shares: the identity pack plus this story's
    own outfit/environment/prop continuity. Continuity is the whole point of
    the format, so it is assembled deterministically from the locked persona
    plus the brief's own continuity fields -- never re-invented per slide."""
    slides = _slides(item)
    props = list(dict.fromkeys(s.get("continuity", "") for s in slides if s.get("continuity")))
    return {
        "identity": identity_pack(persona, index=index, root=root),
        "aspect_ratio": package_master(item)["aspect"],
        "story_thread": item.get("story_thread", ""),
        "environment": slides[0].get("continuity", ""),
        "outfit_and_props": props,
        "continuity_source": item.get("continuity_source", ""),
        "spatial_rule": ("Every slide is the same location and the same continuous scene, minutes "
                         "apart at most; camera moves, the world does not."),
        "text_policy": ("Generate clean art with NO text baked in. Approved overlays are applied "
                        "afterwards by apply_overlays()."),
        "negative_constraints": list(NEGATIVE_CONSTRAINTS) + list(NEGATIVE_IDENTITY),
    }


def _delta(prev: dict | None, cur: dict) -> str:
    """Per-slide delta: ONLY what changes from the previous slide. Describing
    the full scene again is how carousels drift into five unrelated images."""
    if prev is None:
        return "first slide -- establishes the scene described in the continuity sheet"
    parts = [f"camera/composition changes to: {cur.get('composition', '')}",
             f"action changes to: {cur.get('beat', '')}"]
    if cur.get("continuity") and cur.get("continuity") != prev.get("continuity"):
        parts.append(f"continuity to hold: {cur['continuity']}")
    else:
        parts.append("everything else is unchanged from the previous slide")
    return "; ".join(p for p in parts if p)


def _image_prompt(item: dict, persona: dict, slide: dict) -> str:
    """Art only -- the slide's text_overlay is deliberately NOT included. Each
    prompt carries the whole identity lock on its own, so one slide can be
    regenerated in isolation (or by a different provider) and still match."""
    anchors = "; ".join(f"{k}: {v}" for k, v in IDENTITY_ANCHORS.items())
    return (f"{persona.get('visual_style', '')} "
            f"Single photorealistic image, aspect ratio {package_master(item)['aspect']}. "
            f"{ADULT_AGE_LOCK} "
            f"Character: {persona.get('visual_description', '')} "
            f"Identity anchors -- {anchors}. "
            f"Scene beat: {slide.get('beat', '')} "
            f"Composition: {slide.get('composition', '')} "
            f"Continuity to preserve: {slide.get('continuity', '')}").strip()


def build_package(item: dict, persona: dict, *, index: int, root: Path | None = None) -> dict:
    """The complete, deterministic package a ChatGPT-side worker needs to
    produce the slides and for this system to ingest them without guessing."""
    problems = stages.story_carousel_problems(item)
    if problems:
        raise HandoffError(f"brief is not structurally sound: {'; '.join(problems)}")
    slides = _slides(item)
    directory = package_dir(index, root)
    sheet = continuity_sheet(item, persona, index=index, root=root)
    sheet_problems = continuity_sheet_problems(sheet)
    if sheet_problems:
        raise HandoffError(f"Nyx continuity sheet is incomplete: {'; '.join(sheet_problems)}")

    built = []
    for i, slide in enumerate(slides):
        image_prompt = _image_prompt(item, persona, slide)
        overlay = str(slide.get("text_overlay") or "").strip()
        if overlay and overlay.lower() in image_prompt.lower():
            raise HandoffError(
                f"slide {i + 1} overlay copy leaked into the image prompt -- art is generated "
                "clean and the approved overlay is applied afterwards")
        built.append({
            "slot": i + 1,
            "role": slide.get("role", ""),
            "beat": slide.get("beat", ""),
            "composition": slide.get("composition", ""),
            "continuity": slide.get("continuity", ""),
            "delta_from_previous": _delta(slides[i - 1] if i else None, slide),
            "image_prompt": image_prompt,
            "text_in_image": "none -- generate clean art, no letters of any kind",
            # Kept strictly separate from image_prompt: never rendered by the
            # image model, applied later by the overlay step.
            "text_overlay": slide.get("text_overlay", ""),
            "negative_constraints": list(NEGATIVE_CONSTRAINTS) + list(NEGATIVE_IDENTITY),
            "expected_filename": slide_filename(index, i + 1),
        })
    return {
        "package_version": 4,
        "provider": PROVIDER,
        # The identity pack, deltas and prompts below are provider-independent;
        # only "provider" names the route this package was built for.
        "provider_independent": True,
        "unattended": UNATTENDED,
        "usage_constraints": USAGE_CONSTRAINTS,
        "incremental_cost": "none (no Higgsfield credits, no API billing)",
        "item_index": index,
        "item_id": f"content_items[{index}]",
        "format_variant": stages.STORY_CAROUSEL,
        "target_platform": item.get("target_platform", ""),
        "caption": item.get("caption", ""),
        "final_interaction": item.get("final_interaction", ""),
        "open_loop": item.get("open_loop", ""),
        "continuity_sheet": sheet,
        "slide_order": [s["slot"] for s in built],
        "slides": built,
        "ingestion": {
            "directory": {"base": "package", "value": _relative(directory, "package", index, root),
                          "note": "the directory this generation_package.json sits in"},
            "filenames": [s["expected_filename"] for s in built],
            "accepted_formats": list(ACCEPTED_SUFFIXES),
            "rule": (f"All {len(built)} files must exist in slide order before this item can reach "
                     f"ASSET_GENERATED. A partial carousel is refused, never published."),
            "single_slide_replacement": ("Re-deliver one filename to replace just that slide; the "
                                         "other slides are kept and the item returns to QA."),
        },
        "built_at": _now(),
    }


def write_package(item: dict, persona: dict, *, index: int, root: Path | None = None) -> Path:
    directory = package_dir(index, root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / PACKAGE_FILENAME
    path.write_text(json.dumps(build_package(item, persona, index=index, root=root),
                               indent=2, ensure_ascii=False))
    return path


def expected_slide_paths(item: dict, index: int, root: Path | None = None) -> list[Path]:
    directory = package_dir(index, root)
    return [directory / slide_filename(index, i + 1) for i in range(len(_slides(item)))]


def _real_image(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if path.suffix.lower() not in ACCEPTED_SUFFIXES:
        return False
    with path.open("rb") as f:
        head = f.read(12)
    return any(head.startswith(m) for m in _MAGIC)


def missing_slides(item: dict, index: int, root: Path | None = None) -> list[str]:
    """Deterministic completeness gate -- checked before ASSET_GENERATED so a
    half-delivered carousel can never enter QA or the publish queue."""
    return [p.name for p in expected_slide_paths(item, index, root) if not _real_image(p)]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def ingest_slides(v: dict, index: int, *, root: Path | None = None) -> dict:
    """All-or-nothing ingestion into the EXISTING lifecycle: on success the
    item becomes ASSET_GENERATED with one asset_ref per slide, 0 credits and
    0 USD, exactly as a provider generation would."""
    items = v.get("content_plan", {}).get("content_items", [])
    if not (0 <= index < len(items)):
        raise IndexError(f"no content_items[{index}]")
    item = items[index]
    if not stages.is_story_carousel(item):
        raise HandoffError(f"content_items[{index}] is not a story_carousel")
    missing = missing_slides(item, index, root)
    if missing:
        raise HandoffError(
            f"partial carousel refused: {len(missing)} of {len(_slides(item))} slide(s) missing "
            f"({', '.join(missing)})")
    paths = expected_slide_paths(item, index, root)
    item["asset_refs"] = [str(p) for p in paths]
    item["carousel_generation"] = {
        "provider": PROVIDER,
        "unattended": UNATTENDED,
        "usage_constraints": USAGE_CONSTRAINTS,
        "ingested_at": _now(),
        "slides": [{"slot": i + 1, "file": str(p), "sha256_16": _digest(p)}
                   for i, p in enumerate(paths)],
    }
    st.set_content_item_lifecycle(v, index, "ASSET_GENERATED", asset_ref=str(paths[0]),
                                  model_used=PROVIDER, cost_credits=0, cost_usd=0)
    return {"status": "ASSET_GENERATED", "slides": len(paths), "cost_credits": 0}


def replace_slide(v: dict, index: int, slide_number: int, source: Path | str, *,
                  root: Path | None = None) -> dict:
    """One bad slide replaces one bad slide. Regenerating a whole carousel
    because slide 3 missed its beat throws away four good, continuous images."""
    items = v.get("content_plan", {}).get("content_items", [])
    item = items[index]
    slides = _slides(item)
    if not 1 <= slide_number <= len(slides):
        raise HandoffError(f"slide {slide_number} is outside 1..{len(slides)}")
    if not item.get("asset_refs"):
        raise HandoffError("carousel has no ingested slides yet -- use ingest_slides()")
    source = Path(source)
    if not _real_image(source):
        raise HandoffError(f"replacement {source.name} is not a usable image file")
    target = package_dir(index, root) / slide_filename(index, slide_number)
    if source != target:
        shutil.copyfile(source, target)
    item["asset_refs"][slide_number - 1] = str(target)
    gen = item.setdefault("carousel_generation", {})
    gen.setdefault("slides", [])
    for rec in gen["slides"]:
        if rec.get("slot") == slide_number:
            rec.update({"file": str(target), "sha256_16": _digest(target)})
    gen.setdefault("replacements", []).append(
        {"slot": slide_number, "at": _now(), "sha256_16": _digest(target)})
    # Back to ASSET_GENERATED: a replaced slide must face creative QA again.
    st.set_content_item_lifecycle(v, index, "ASSET_GENERATED",
                                  asset_ref=item["asset_refs"][0], cost_credits=0, cost_usd=0)
    return {"status": "SLIDE_REPLACED", "slot": slide_number, "file": str(target)}


# --- text overlay (kept deliberately tiny: placement policy, not a design engine)
# Margins are no longer one constant: each render master's platform safe text
# zone (brand/platform_formats.json via render_masters) decides where text may sit.
OVERLAY_MAX_WIDTH_PCT = 78
OVERLAY_FONT_PCT = 4.5
OVERLAY_COLOR = "#F5F1FF"
OVERLAY_SHADOW_COLOR = "#120C1F"
OVERLAY_LINE_SPACING = 1.18
# One typographic style for every PS-05 slide, in priority order: a stock
# grotesque that exists on the machines PS-05 runs on, so the same approved copy
# renders the same way everywhere. Pillow's bitmap default is ~11px -- unreadable
# on a 1080x1350 phone slide -- so it is a last resort, never the design.
OVERLAY_FONTS = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


class OverlayUnavailable(RuntimeError):
    """Pillow is not installed -- art is still fine, only burn-in is blocked."""


def overlay_position(safe_zone: str) -> str:
    """Turn the story lock's worded overlay_safe_zone ("lower third, clear of
    her face, the charm and the pillow edge") into one of the two anchors the
    renderer knows. Kept for callers that only have the wording; the explicit
    per-slide text_placement field is read by overlay_plan first."""
    anchor, _ = rm.resolve_anchor({"overlay_safe_zone": safe_zone})
    return f"{anchor}-left"


def overlay_plan(item: dict, index: int, root: Path | None = None, *,
                 safe_zones: dict | None = None, slots=None,
                 placements: dict | None = None, master_id: str | None = None,
                 target_platform: str | None = None) -> list[dict]:
    """Deterministic placement for the APPROVED text only -- same typography
    every slide, and slides briefed as wordless stay wordless.

    `placements` maps slot -> the lock's explicit text_placement dict
    ({"anchor": "top"|"bottom"|"none", ...}); `safe_zones` maps slot -> the
    older worded overlay_safe_zone. text_placement wins. `master_id` picks the
    canvas (4:5 public or 9:16 TikTok/Fanvue) and therefore the safe text zone;
    it defaults to the primary master of the item's target_platform.
    `slots` restricts the plan to specific slides, so approving slide 1 renders
    slide 1 and never touches the four that are not approved yet."""
    zones = {int(k): v for k, v in (safe_zones or {}).items()}
    explicit = {int(k): v for k, v in (placements or {}).items()}
    only = {int(s) for s in slots} if slots is not None else None
    platform = target_platform or item.get("target_platform")
    master_id = master_id or rm.masters_for_platform(platform)[0]
    suffix = rm.file_suffix(platform, master_id)
    safe = rm.safe_text_zone(master_id)
    plan = []
    for i, slide in enumerate(_slides(item)):
        slot = i + 1
        text = str(slide.get("text_overlay") or "").strip()
        if not text or (only is not None and slot not in only):
            continue
        anchor, source = rm.resolve_anchor({"text_placement": explicit.get(slot),
                                            "overlay_safe_zone": zones.get(slot, "")})
        if anchor == "none":
            continue
        plan.append({
            "slot": slot,
            "text": text,
            "source": str(package_dir(index, root) / slide_filename(index, slot, suffix)),
            "output": str(package_dir(index, root) / overlay_filename(index, slot, suffix)),
            "master": master_id,
            "safe_zone": zones.get(slot, ""),
            "anchor": anchor,
            "placement_source": source,
            "position": f"{anchor}-left",
            "safe_text_zone": safe,
            "max_width_pct": OVERLAY_MAX_WIDTH_PCT,
            "font_pct_of_height": OVERLAY_FONT_PCT,
            "color": OVERLAY_COLOR,
            "shadow": True,
        })
    return plan


def _overlay_font(size: int):
    """The first OVERLAY_FONTS face that loads, at the slide's own scale."""
    from PIL import ImageFont  # noqa: PLC0415 -- optional, only needed to burn text in

    for candidate in OVERLAY_FONTS:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size), candidate
            except OSError:               # pragma: no cover - unreadable font file
                continue
    return ImageFont.load_default(), "PIL.ImageFont.load_default"


def _wrap(text: str, font, max_width: int) -> list[str]:
    """Greedy word wrap inside the safe zone's width -- deterministic, so the
    same copy always breaks in the same place."""
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and font.getlength(trial) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines or [text]


def apply_overlays(item: dict, index: int, root: Path | None = None, *,
                   safe_zones: dict | None = None, slots=None,
                   placements: dict | None = None, master_id: str | None = None,
                   target_platform: str | None = None) -> list[str]:
    """Burn the approved copy into a SEPARATE _captioned.png. The clean
    generated art is never overwritten: it stays the approved source of record
    and the environment master, and this step can be re-run on it at will."""
    try:
        from PIL import Image, ImageDraw  # noqa: PLC0415 -- optional, only needed to burn text in
    except ImportError as exc:           # pragma: no cover - environment-dependent
        raise OverlayUnavailable(
            "Pillow is not installed; slide art is unaffected -- install Pillow or apply the "
            "approved overlays in the founder's existing editor using overlay_plan()") from exc
    written = []
    for step in overlay_plan(item, index, root, safe_zones=safe_zones, slots=slots,
                             placements=placements, master_id=master_id,
                             target_platform=target_platform):
        img = Image.open(step["source"]).convert("RGB")
        draw = ImageDraw.Draw(img)
        size = int(img.height * step["font_pct_of_height"] / 100)
        font, _ = _overlay_font(size)
        # Width and position come from the master's safe text zone; text_box()
        # also refuses a canvas of the wrong aspect for this master. The drop
        # shadow's offset is counted in both, so it never leaves the zone.
        offset = max(2, size // 20)
        probe = rm.text_box(step["master"], step["anchor"], width=img.width, height=img.height,
                            block_height=0, max_width_frac=step["max_width_pct"] / 100)
        lines = _wrap(step["text"], font, probe["max_width"] - offset)
        step_height = int(size * OVERLAY_LINE_SPACING)
        box = rm.text_box(step["master"], step["anchor"], width=img.width, height=img.height,
                          block_height=step_height * len(lines) + offset,
                          max_width_frac=step["max_width_pct"] / 100)
        x, y = box["x"], box["y"]
        for line in lines:
            draw.text((x + offset, y + offset), line, font=font, fill=OVERLAY_SHADOW_COLOR)
            draw.text((x, y), line, font=font, fill=step["color"])
            y += step_height
        img.save(step["output"], format="PNG")
        written.append(step["output"])
    return written


def provider_decision(item: dict, *, credits_remaining: float) -> dict:
    """Cost policy for carousels (founder, 2026-09-21): the no-incremental-cost
    route is the default; paid image generation happens only when the founder
    has explicitly approved the spend on THIS item. A carousel existing is
    never on its own a reason to consume Higgsfield credits."""
    if stages.carousel_paid_generation_approved(item) and credits_remaining > 0:
        return {"provider": "higgsfield", "incremental_cost": "higgsfield_credits",
                "reasoning": (f"founder approved paid generation for this carousel and "
                              f"{credits_remaining} credit(s) remain")}
    if stages.carousel_paid_generation_approved(item):
        return {"provider": PROVIDER, "incremental_cost": "none",
                "reasoning": ("paid generation is approved but the credit ceiling is exhausted -- "
                              "using the no-incremental-cost ChatGPT route")}
    return {"provider": PROVIDER, "incremental_cost": "none",
            "reasoning": ("default route for story carousels: no Higgsfield credits are spent "
                          "without explicit founder approval on this item")}
