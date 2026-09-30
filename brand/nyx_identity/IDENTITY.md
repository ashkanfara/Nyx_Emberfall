# Nyx Emberfall — Canonical Visual Identity (v2.0.0)

Canonical since 2026-09-22. This file is the human-readable companion to
[`identity_spec.json`](identity_spec.json), which is the machine-readable
source of truth code actually loads. If the two ever disagree, the JSON wins.

## Master references

| Rank | File | Source | Authority |
|---|---|---|---|
| 1 — **primary** | [`reference_primary.webp`](reference_primary.webp) | `2.webp` as received | Highest authority for face, body, ears, tail, eyes, skin, beauty mark, realism |
| 2 — secondary | [`reference_secondary.webp`](reference_secondary.webp) | `1.webp` as received | Supporting only — reinforces angle/consistency, never overrides the primary |

Hosted (CloudFront, durable): see `reference_hierarchy[*].hosted_url` in the JSON.

## Who Nyx is

Adult woman (mid-20s appearance, 5'6"/168cm, slim/athletic), fox-girl (human
hybrid): long dark brown/near-black hair, amber/golden eyes, warm light skin
with realistic natural texture (visible pores, not plastic-smooth), subtle
realistic fox ears, exactly one bushy dark-brown fox tail with a lighter tip,
small beauty mark on her **anatomical left** cheek (viewer-right when she faces
the camera), exactly as positioned and sized in `reference_primary.webp`.
Exactly two visible ears: her two fox ears (no human, duplicate or
costume/headband ears). Elegant, confident, mysterious,
highly realistic / photorealistic. Human-dominant appearance with restrained
fox traits.

**May vary:** clothing, hairstyle styling, expression, pose, environment,
lighting, camera, composition, props, time of day, mood.

**Must never vary:** face, eyes, ears, hair color/hairline, skin tone/texture,
tail, beauty-mark side, body proportions, age appearance, ethnicity/phenotype,
realism level. Full list: `prohibited_drift` in the JSON.

## Reusable base prompt

Prepend `base_generation_prompt.identity_block` (from the JSON) to every
generation request, then fill in:

```
SCENE:
[scene description]

WARDROBE:
[wardrobe]

POSE:
[pose]

CAMERA:
[camera]

LIGHTING:
[lighting]

MOOD:
[mood]
```

Attach `reference_primary.webp` (or its hosted URL) as the image reference
whenever the provider accepts one.

## What was superseded

The prior canonical identity (spec v1.0.0) was **2D anime/JRPG, semi-cel-shaded,
explicitly non-photorealistic** — silver/lavender hair, lavender-fur ears, a
silver-white constellation-pattern tail, an indigo/lavender/silver/gold-amber
palette, and a freckle under the *left* eye. It is preserved for provenance in
`identity_spec.json.superseded_visual_identity` and in
`state/venture.json persona.superseded_visual_identity` — not deleted.

That choice was partly a platform-compliance decision (stylized personas clear
Fanvue *and* Fansly moderation more easily than photorealistic ones). The
founder has explicitly and repeatedly overridden it in favor of these master
references; the tradeoff is recorded, not re-litigated.

Nyx's **name, species concept, voice/personality, disclosure statement and
story threads** (Star-Map / Changing Constellations, The Charm, …) are
untouched — only the visual description changed.

## How this gets used automatically

- **`carousel_handoff.py`** (ChatGPT story-carousel handoff) loads its identity
  constants from `identity_spec.json` at import time — no hardcoded duplicate.
- **`state/venture.json` → `persona`** (`visual_description`, `visual_style`,
  `reference_image_url`) is synced from this spec, so the Wan-provider
  generation path (`prompts.py` / `manager.py` / `stages.py`) picks it up too.
- **Creative QA** (`prompts.py` `CAROUSEL_QA`) now flags a *photorealism*
  drift, not an anime-style drift.
- **A relay task or ChatGPT-side generation** should point at this file
  (`brand/nyx_identity/identity_spec.json`) rather than copying its prose
  elsewhere — see `instructions_for_downstream_workers` in the JSON.

## Item 20

`content_items[20]` (story carousel, `BRIEF_READY`, not yet generated) had its
`generation_package.json` rebuilt against this identity — a zero-spend local
metadata rewrite, no images generated. Its old off-model files (one paid
`soul_2` test slide that already failed QA, plus stale anime-era reference
copies) were archived, not deleted, under
`generated_assets/carousel_item20/superseded_v1_anime/`.
