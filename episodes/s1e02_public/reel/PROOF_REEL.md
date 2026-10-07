# s1e02 "Under the Door" -- free proof Reel + Fanvue companion

**TEST ONLY.** The proof Reel uses a free, generic macOS system voice. It is never Nyx's
production voice and never published. `nyx_reels.py` enforces this: a `free_local_tts` source is
never release-eligible, the file is written as `s1e02_public_reel.TEST.mp4`, and
"TEST - generic voice - not for release" is burned into every frame.

No paid API, no new images, no upload, no schedule, no publish. The trial switches
(`automation_trial.json`, `generation_executor.json`) are not touched.

## Status (2026-10-07)

| Piece | Status | Where |
|---|---|---|
| Voiceover script (23.5 s, 6 lines, captions, scenes) | Ready, passes every text rule (151.5 wpm) | `voiceover_script.json` |
| Voice bible | Draft, production voice not locked (correct for a TEST) | `brand/nyx_voice/` |
| Approved s1e02 slides | **0 of 6: the blocker** | `generated_assets/carousel_item33/` |
| Test voice (macOS `say`) | Not recorded. Needs the Mac | `generated_assets/carousel_item33/reel/voiceover.wav` |
| ffmpeg (with libass) | Not installed here. Needs the Mac | `brew install ffmpeg` |
| Music bed | None. Renders voice-only (a warning, not a block) | add `music` to the script with a licence |
| Fanvue companion | DRAFT story lock + caption, validated, **not approved** | `../fanvue_companion/` |

**Smallest exact blocker:** there is no QA-approved image for any s1e02 slide. Reels use approved
art only. The only tracked story images (the 2026-09-29 Metricool handoff set) are recorded
identity failures: human ears, a banded tail, hair and face drift. They can't stand in. ffmpeg and
the test voice are both one-command steps on your Mac. They come after the art.

## Run it (on the Mac that holds `generated_assets/`)

**1. Art (free, by hand).** This is already prepared:

1. Open `episodes/s1e02_public/batch/CURRENT.md`.
2. Generate its 6 images in one ChatGPT conversation.
3. Save them as `generated_assets/carousel_item33/drop/slide1.png` … `slide6.png`.
4. Run `python3 nyx_runner.py tick`. It picks them up, runs visual QA and repairs, and approves them.

Leave `image_generation` off if you want the proof to stay at $0.

**2. Test voice (free, local):**
```
python3 nyx_reels.py script-text s1e02_public --say > /tmp/nyx_vo.txt
say -r 150 -f /tmp/nyx_vo.txt -o /tmp/nyx_vo.aiff
mkdir -p generated_assets/carousel_item33/reel
afconvert -f WAVE -d LEI16@44100 /tmp/nyx_vo.aiff generated_assets/carousel_item33/reel/voiceover.wav
python3 nyx_reels.py audio-provenance s1e02_public --source free_local_tts --tool "macOS say" --voice "system default (generic, TEST)"
```

**3. Validate, plan and render (free, local):**
```
python3 nyx_reels.py validate s1e02_public      # if "timed for 23.5 s": adjust `say -r` (higher = shorter), redo step 2
brew install ffmpeg                             # once
python3 nyx_reels.py plan s1e02_public
python3 nyx_reels.py render s1e02_public --execute
```
Result: `generated_assets/carousel_item33/reel/s1e02_public_reel.TEST.mp4`. Watch it locally only.

## Fanvue companion (draft)

`../fanvue_companion/companion_lock.DRAFT.json` is 4 frames on the 9:16 Fanvue canvas: a deeper
continuation of the same open loop. The envelope is heavier than paper, something small shifts
inside, and she still won't open it ("you decide first"). It never shows or names the contents,
so it can't pre-empt the public vote that decides episode 3. It reuses the s1e02 loft, wardrobe
and identity locks.

It passes `story_continuity.plan_problems` with no problems and the coordinator brief check. Its
caption draft carries `#AIgenerated` (Fanvue has no native AI label on this route).

To make it real, which is a founder decision:
1. Approve it: copy it to `brand/story_locks/s1e02_fanvue_companion.json`, set `approved_by` and
   `locked_since`, and confirm `item_index`.
2. Enrol it in `episodes/runner_registry.json` as `{"story_id": "s1e02_fanvue_companion",
   "kind": "fanvue_chapter", "parent": "s1e02_public", "approved_by": "..."}`.
3. The next tick opens its own free ChatGPT batch, 4 images at 1080x1920.

Nothing publishes: auto-publish stays off, and the release queue stops for your approval.
