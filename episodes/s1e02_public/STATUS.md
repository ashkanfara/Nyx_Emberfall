# s1e02_public -- coordinator status

Mode: **DRY_RUN** (no browser, no uploads, no generation, no publishing, $0). Last run: 2026-09-30T14:37:52+00:00. Canon preserved: True.
Lock: `brand/story_locks/s1e02_public.json` sha256 `fe081e485094`

| Stage | Status |
|---|---|
| brief | PASS_WITH_WARNINGS |
| locked_prompts | PASS |
| generation_handoff | WAITING_ON_EXECUTOR |
| visual_qa | WAITING_ON_UPSTREAM |
| targeted_repair | WAITING_ON_UPSTREAM |
| captions | PASS |
| platform_variants | WAITING_ON_UPSTREAM |
| publish_gate | NOT_EXECUTED_DRY_RUN |

## Smallest blocker to a live run

**generation_handoff** (WAITING_ON_EXECUTOR): generation executor (not PS5): generate slide 1 from episodes/s1e02_public/handoff/slide1.json and save each image at its output.expected_path

## Canon findings (recorded, not changed)

- lock.authority names ['s1e01_public', 'star_atlas_001'], not s1e02_public -- copied from an earlier lock; wording only, no generation effect

## Live-run prerequisites

- [ ] every slide APPROVED in visual QA
- [x] captions validated
- [ ] state/venture.json carries content_items[33] (the publisher reads caption/media from it)
- [x] at least one LIVE route

## Rehearsal (SIMULATED, temp dir, proves code paths not art)

- locked prompts + slide-1 handoff: `{"prompts": "PASS", "handoff": "WAITING_ON_EXECUTOR", "handed_off": [1], "gated_on_previous_approval": [2, 3, 4, 5, 6]}`
- slide 1 simulated REJECT -> targeted repair: `{"visual_qa": "WAITING_ON_EXECUTOR", "repair": "WAITING_ON_EXECUTOR", "repair_attempt": [[1, 2]]}`
- all slides simulated APPROVE -> captions -> variants -> gate: `{"visual_qa": "PASS", "captions": "PASS", "variants": {"instagram": {"state": "READY_TO_RENDER", "route_status": "LIVE"}, "tiktok": {"state": "READY_TO_RENDER", "route_status": "LIVE"}, "threads": {"state": "PREPARED_NOT_PUBLISHABLE", "route_status": "UNVERIFIED"}, "x": {"state": "PREPARED_NOT_PUBLISHABLE", "route_status": "PREPARED_NOT_CONNECTED"}}, "publish_gate": "NOT_EXECUTED_DRY_RUN", "technical_gate": [true, true, true, true, true, true]}`
