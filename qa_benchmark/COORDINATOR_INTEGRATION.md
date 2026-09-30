# Coordinator integration: Nyx render masters, safe text zones, room references

Branch `claude/vigilant-brahmagupta-st9fga`. Everything here is zero-cost. None of these steps
generates, uploads, publishes, opens a social site or spends. Generation and publishing stay
with the human/executor roles in `brand/NYX_PRODUCTION_CONTRACT.md`.

## 1. Before every run: read the benchmark

```
python3 visual_qa_benchmark.py --check      # exit 0 = committed report matches the repo
```

If it exits 1, run `python3 visual_qa_benchmark.py` and commit the regenerated report. Then read
`qa_benchmark/report.json`:

| Field | Use |
|---|---|
| `coordinator_summary.overall` | `PASS` / `WARN` / `FAIL` |
| `coordinator_summary.global_blockers` | Identity and room failures. Non-empty = nothing moves to `ready_to_publish` on any platform. |
| `coordinator_summary.platform_readiness.<p>.status` | `BLOCKED` / `READY_WITH_WARNINGS` / `READY`. This already includes the global blockers. |
| `coordinator_summary.platform_readiness.<p>.format_status` | Format fitness for that platform alone. |
| `coordinator_summary.next_actions` | Ordered, most urgent first. |
| `inputs_sha256_16` | Unchanged = skip re-reading. |

A platform may take an asset only when **both** hold:
`platform_readiness[p].status != "BLOCKED"` and `channels.CHANNELS[p]["publishes"] is True`.
Right now (2026-09-30) every platform is `BLOCKED` by the room-reference gate, and X/Threads also
fail the channel check.

## 2. Render masters: what to ask the generation executor for

Source of truth: `brand/platform_formats.json → render_masters`, read through `render_masters.py`.

| Master | Canvas | Platforms | Filename |
|---|---|---|---|
| `public_carousel_4x5` | 1080x1350 | Instagram, Threads, X | `item<N>_slide<k>.png` |
| `vertical_9x16` | 1080x1920 | TikTok, Fanvue **only** | `item<N>_slide<k>.png` when it is the lock's only master, `item<N>_slide<k>_9x16.png` when it is the second master of a `public_multi` lock |

```python
import render_masters as rm
rm.masters_for_lock(lock)   # primary first, e.g. ['public_carousel_4x5', 'vertical_9x16'] for s1e02_public
```

- Write **one prompt pack per master per slide**. The story_continuity DRY_RUN payload already
  carries it: `generation_request.output` has the primary `width/height/aspect_ratio/render_master`,
  and `generation_request.output.additional_masters[]` lists each extra master with its own filename.
- Never crop a 4:5 file into 9:16 (or back) and call it the other master. Each master is composed
  for its own canvas.
- Current locks: Instagram-bound locks and `s1e02_public` are 4:5 primary. Only `s1e02_public`
  (`public_multi`) also needs 9:16. All Fanvue locks are 9:16 only. Each changed lock records
  `render_master_revision`.
- The paid Higgsfield route now refuses a 9:16-primary lock (`WRONG_RENDER_MASTER`). The OpenAI
  route sizes its request per master (4:5 → 1088x1360, 9:16 → 1152x2048). Neither is called by
  anything in this change.

## 3. Overlays: platform-safe placement

After a slide is approved, burn in the approved copy (local Pillow write, no model):

```python
import ps05_ops, render_masters as rm
ps05_ops.render_slide_overlay(index, slot)                                   # primary master
ps05_ops.render_slide_overlay(index, slot, master_id=rm.VERTICAL_MASTER)     # 9:16 file of a public_multi lock
```

Placement order per slide: the lock's `text_placement.anchor` (`top` / `bottom` / `none`), then the
worded `overlay_safe_zone`. Text is confined to the master's safe zone:

| Master | top | bottom | left | right |
|---|---|---|---|---|
| 4:5 public | 7% | 7% | 7% | 7% |
| 9:16 vertical (strictest of TikTok + Fanvue) | 10% | 25% | 6% | 15% |

A 9:16 image passed with a 4:5 master (or the reverse) raises `RenderMasterError`. Nothing is
silently squeezed. The result includes `master` and `placement_source`. Treat
`placement_source == "default"` as a QA fail. `SZ-PLACEMENT` in the benchmark already enforces
this for every lock.

`s1e02_public` placements (from the approved beats): 1 top, 2 none, 3 bottom, 4 top, 5 none,
6 bottom. Each has `keep_clear` and a rationale, and the generation prompt's TEXT POLICY now
names that region.

## 4. Room references: the gate and how to open it

Source of truth: `brand/room_references/room_reference_manifest.json`, read through `room_references.py`.

A room-continuity (`environment_continuity`) PASS is refused unless the room has a registered
reference image that exists and still matches its hash. The only exception is the room's
establishing story (loft: `star_atlas_001`; bedroom: `the_charm_001`; music-box bedroom:
`music_box_001_part1`). The gate is enforced in both QA paths:

- `episode_ops.py qa-decide` raises `environment_continuity PASS refused ... BLOCKED_NO_ROOM_REFERENCE`.
  To record the slide as failed instead, score `environment_continuity` as
  `"BLOCKED: no room reference"`.
- `story_continuity.qa_slide` returns `status: BLOCKED_NO_ROOM_REFERENCE`, `verdict: BLOCKED`, and
  leaves state untouched. Rejections are still recorded.

```
python3 room_references.py status
python3 room_references.py gate nyx_loft_star_atlas s1e02_public 1     # exit 2 while blocked
```

To open it (human/founder action on the machine that holds `generated_assets/`, zero cost):

1. The founder confirms which file is the approved room master. Expected:
   `generated_assets/carousel_item28/item28_slide1.png` (the loft; do this first, since all of Season 1 uses it).
2. `python3 room_references.py register nyx_loft_star_atlas generated_assets/carousel_item28/item28_slide1.png "founder 2026-MM-DD"`
3. `python3 visual_qa_benchmark.py`: `RM-REFERENCE nyx_loft_star_atlas` turns PASS. Commit the
   updated manifest and report.
4. Do **not** register `nyx_apartment_bedroom` until star_map_001 and the_charm_001 anchors are
   reconciled (`RM-CONSISTENCY`).

`register` refuses a missing file, a non-PNG, or a canvas that matches no render master.
It is a local JSON write, and only a human/executor should run it. Claude PS5 must not pick the file.

## 5. Hard rules unchanged

- The 2026-09-29 Metricool handoff set stays unpublished (`AS-IDENTITY` FAIL: human ears,
  banded tail, hair/face drift).
- No step in this document generates, uploads, publishes or spends.
