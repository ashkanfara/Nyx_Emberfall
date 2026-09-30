# Nyx visual QA benchmark

Deterministic, read-only QA for identity anchors, room anchors, render masters, platform aspect
ratios and safe text zones (Instagram, TikTok, Threads, X, Fanvue). Stdlib only.
No generation, spend, upload, browser or publish.

```
python3 visual_qa_benchmark.py           # rebuild report.json + REPORT.md
python3 visual_qa_benchmark.py --check   # exit 1 if the committed report is stale
python3 visual_qa_benchmark.py --strict  # exit 1 if any case FAILs
python3 -m unittest test_visual_qa_benchmark test_render_masters
```

| File | Role |
|---|---|
| `qa_benchmark/test_manifest.json` | Input. Identity anchors (keyed to `identity_spec.locked_traits`), room rules, asset inventory, recorded visual observations. |
| `brand/platform_formats.json` | Input and production source of truth. Render masters, canvases, accepted aspects, crop behaviour, safe text zones, confidence per platform surface. |
| `brand/room_references/room_reference_manifest.json` | Input and production source of truth. One canonical reference image per room. |
| `qa_benchmark/report.json` | Output for the coordinator. Read `coordinator_summary` first. |
| `qa_benchmark/REPORT.md` | Same result, human-readable. Generated, never hand-edited. |
| `qa_benchmark/COORDINATOR_INTEGRATION.md` | How the coordinator consumes all of this. |

## Case families

| ID | What it proves |
|---|---|
| `ID-*` | Identity references exist, hashes match, crops are pure, every locked trait has a QA anchor. |
| `RM-LOCK`, `RM-CONSISTENCY` | Every lock has a room anchor set; locks sharing a room agree (additive props allowed). |
| `RM-REFERENCE` | The room has a registered, hash-verified reference image. FAIL = room-continuity PASS is blocked. |
| `FMT-LOCK-MASTER`, `FMT-LOCK`, `FMT-MASTER` | Each lock's `aspect_ratio`/`render_masters` match its platform; every platform it reaches is served by an accepted master. |
| `FMT-PIPELINE` | No hardcoded 1080x1350 output left; the provider has an exact-aspect request size per master. |
| `SZ-OVERLAY` | The production overlay box (`render_masters.text_box`, shadow included) sits inside every served platform's own safe zone. |
| `SZ-PLACEMENT` | Every slide with copy resolves to an explicit or worded top/bottom placement, never the default. |
| `AS-*` | On-disk handoff assets: format fitness (computed) and identity (recorded observation, goes stale on hash change). |

## Grading rules

- A breach of a `low`-confidence platform rule (no published spec: Threads, Fanvue) is a WARN, never a FAIL.
- `AS-IDENTITY` cases are recorded observations carried in the manifest, not computed.
- Identity and room failures are global blockers: they block every platform regardless of format status.
