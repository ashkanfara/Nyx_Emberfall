# s1e02_public -- coordinator status

Mode: **DRY_RUN** (no browser, no uploads, no generation, no publishing, $0). Last run: 2026-09-30T14:04:42+00:00. Canon preserved: True.
Lock: `brand/story_locks/s1e02_public.json` sha256 `1c3eb32f49f3`

| Stage | Status |
|---|---|
| brief | BLOCKED |
| locked_prompts | BLOCKED |
| generation_handoff | WAITING_ON_UPSTREAM |
| visual_qa | WAITING_ON_UPSTREAM |
| targeted_repair | WAITING_ON_UPSTREAM |
| captions | PASS |
| platform_variants | WAITING_ON_UPSTREAM |
| publish_gate | NOT_EXECUTED_DRY_RUN |

## Smallest blocker to a live run

**brief** (BLOCKED): apply the proposed lock patch (python3 episode_coordinator.py apply-lock-patch s1e02_public) -- it clears every brief error

## Canon findings (recorded, not changed)

- season slot s1e02 ('Not Mine, But Mine') depends on music_box_001 (pre-season), which this lock never references -- either the founder-approved brief supersedes the slot (record it in the season file) or the lock drifted
- lock aspect_ratio is 9:16 but every slide is rendered 1080x1350 (4:5); the variants below use 4:5
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
