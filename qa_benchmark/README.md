# Nyx visual QA benchmark

Deterministic, read-only QA for identity anchors, room anchors, platform aspect
ratios and safe text zones (Instagram, TikTok, Threads, X, Fanvue). Stdlib only.
No generation, spend, upload, browser or publish.

```
python3 visual_qa_benchmark.py           # rebuild report.json + REPORT.md
python3 visual_qa_benchmark.py --check   # exit 1 if the committed report is stale
python3 visual_qa_benchmark.py --strict  # exit 1 if any case FAILs
python3 -m unittest test_visual_qa_benchmark
```

| File | Role |
|---|---|
| `test_manifest.json` | Input. Identity anchors (keyed to `identity_spec.locked_traits`), room rules, asset inventory, recorded visual observations. |
| `platform_formats.json` | Input. Canvas, accepted aspects, crop behaviour, safe text zone and confidence per platform surface. |
| `report.json` | Output for the coordinator. Read `coordinator_summary` first. |
| `REPORT.md` | Same result, human-readable. Generated, never hand-edited. |

## Coordinator contract

`report.json → coordinator_summary`:

- `overall`: `PASS` / `WARN` / `FAIL`
- `platform_readiness.<platform>.status`: `READY` / `READY_WITH_WARNINGS` / `BLOCKED`, plus the case ids behind it. This covers format fitness only. Whether a channel can publish at all is still `channels.CHANNELS` (X is not connected, Threads is unverified).
- `next_actions`: ordered, most urgent first.
- `spend_usd`, `generations`, `uploads`, `publishes`: always 0.

`inputs_sha256_16` fingerprints every input, so a coordinator can skip re-reading an unchanged report.

## Grading rules

- A breach of a `low`-confidence platform rule (no published spec: Threads, Fanvue) is a WARN, never a FAIL.
- Computed cases (hashes, dimensions, aspect maths, overlay geometry, anchor diffs) come from the runner.
  `AS-IDENTITY` cases are recorded human-style observations carried in the manifest. They go stale (WARN) as soon as the file hash changes.
- Room anchors are text-only in git. Approved room master images live in gitignored `generated_assets/`, so `RM-MASTER-IMAGE` stays WARN in a clean checkout.

## Using the identity anchors on a new asset

Score every `IA-*` anchor as PASS / FAIL / N/A against `brand/nyx_identity/generation_refs/*` and `reference_primary.webp`. Any hard-anchor FAIL means the asset is `qa_failed` (`episode_ops.py qa-decide`). N/A is never PASS: if the beauty-mark cheek or the ears are out of frame, say so.
