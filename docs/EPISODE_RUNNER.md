# Autonomous episode runner

`nyx_runner.py` takes an approved story lock to a release queue without anyone relaying
"move forward". Automation ends at **READY_FOR_PUBLISH**. It never generates an image,
opens a browser or social site, uploads, posts, comments, spends or changes an account.
The Reddit workstream belongs to its own agent and is not touched.

## One trigger

```
python3 nyx_runner.py tick      # idempotent; safe to run any number of times
```

Install `ops/com.nyx.episode-runner.plist` on the machine that holds `generated_assets/`.
It runs `tick` every 15 minutes and at login, so a reboot, app restart or browser restart
just means the next tick resumes:

```
sed "s#__NYX_ROOT__#$PWD#g" ops/com.nyx.episode-runner.plist > ~/Library/LaunchAgents/com.nyx.episode-runner.plist
launchctl load ~/Library/LaunchAgents/com.nyx.episode-runner.plist
```

Status: `episodes/RUNNER_STATUS.md` (rewritten every tick, or `python3 nyx_runner.py status`).

## What a tick does

| Step | Owner | Evidence required to move on |
|---|---|---|
| Brief + locked prompts | `episode_coordinator` | brief and plan compile clean |
| Handoff (per slide, in order) | runner writes `episodes/<id>/handoff/slide<N>_attempt<K>.json` + `.prompt.txt` | slide N-1 approved (`story_continuity.readiness`) |
| Generation | **executor** (founder or a separate automation) | image delivered with `record-asset`, provenance bound to that handoff's prompt sha |
| Technical gate | runner, deterministic | PNG, exact canvas (4:5 = 1080x1350, 9:16 = 1080x1920), not blank/flat |
| Visual QA | `claude -p` vision reviewer (opens the image with its Read tool) | every dimension scored with an observation; verdict bound to the file's sha256, recorded through `story_continuity.qa_slide` |
| Repair | runner | rejection archived to `rejected/`, targeted repair prompt as attempt K+1; after `retry.max_attempts_per_slide` the slide is a named gap (`HOLD_NAMED_GAP`) |
| Captions | existing valid `captions.json`, else drafted by `claude -p` | `episode_coordinator.stage_captions` PASS |
| Variants | local render: overlays (Pillow), TikTok slideshow (ffmpeg) | files rendered from approved art, sha recorded |
| Release queue | `episodes/release_queue.json` | `READY_FOR_PUBLISH` (live route) or `HELD_NO_ROUTE` |

A Fanvue extended chapter is its own approved lock. Enrol it in `episodes/runner_registry.json`
with `"kind": "fanvue_chapter", "parent": "<public story_id>"` and it runs the same pipeline into
the same queue. Without one, the status shows `NO_APPROVED_LOCK`.

## Commands for people and executors

```
python3 nyx_runner.py record-asset <story_id> <slot> <image.png> --by <who> --provider <name>
python3 nyx_runner.py confirm-published <story_id> <platform> <live url> --by <who>
python3 nyx_runner.py unblock <story_id>        # after fixing a reviewer/caption/render outage
```

`confirm-published` only records a publish a human already did and checked.

## Durability rules

- Every write is write-then-rename. Each step re-derives from disk, so it can be replayed.
- QA order: evidence file, then story state, then the `recorded` flag. A crash between any two
  is repaired on the next tick without a second verdict.
- Outages (CLI down, quota, missing Pillow/ffmpeg) are retried with backoff (5 min doubling,
  6 h cap). They are never verdicts. After 5 reviewer failures the slide is BLOCKED until `unblock`.
- One tick at a time (`flock` on `episodes/.runner.lock`, released by the OS on crash).
- Simulated adapters, simulated images and simulated verdicts are refused outside a sandbox.

## Proof

```
python3 nyx_runner.py simulate               # full simulated episode + Fanvue chapter, temp dir
python3 nyx_runner.py simulate --interrupt   # same, with three injected crashes
python3 -m unittest test_nyx_runner
```
