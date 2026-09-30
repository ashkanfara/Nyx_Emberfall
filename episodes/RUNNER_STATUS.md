# Nyx episode runner -- status

Exception-only continuation. Ordinary stages resume on their own; the runner stops only for the four exceptions below and never publishes externally.

| Episode | Status | Slides approved | Exception / next | Resumes |
|---|---|---|---|---|
| s1e02_public | EXC_GENERATION_ACTION_REQUIRED | 0/6 | **#1 Image generation needs a human or an approved executor** -- generate slide 1 from episodes/s1e02_public/handoff/slide1_attempt1.prompt.txt (handoff episodes/s1e02_public/handoff/slide1_attempt1.json) and run: python3 nyx_runner.py record-asset s1e02_public 1 <image.png> --by <who> --provider <name> | automatically on the next tick after the image is delivered |
| ↳ Fanvue chapter | NO_APPROVED_LOCK | - | a Fanvue chapter needs its own founder-approved lock, enrolled with parent s1e02_public | when enrolled |

## Release packages (never published by the runner)

None yet.

## Activation readiness (scheduler not installed)

**WOULD NOT MAKE MEANINGFUL PROGRESS: with no approved unattended generation executor, a scheduled tick would only re-report the open generation exception. It becomes useful once images start arriving -- it then runs QA, repairs, captions, variants and the release package without relays.**

- MISSING: generation executor -- no unattended executor is approved: a human generates each handoff and delivers it with record-asset
- ok: visual QA reviewer (claude CLI; credentials untested) -- `claude` on PATH
- MISSING: overlay renderer (Pillow) -- import PIL
- MISSING: TikTok renderer (ffmpeg) -- `ffmpeg` on PATH
- MISSING: release prerequisites (state/venture.json) -- state/venture.json

Remaining blockers to unattended progress:

- image generation: no unattended executor is approved: a human generates each handoff and delivers it with record-asset -- every slide stops at EXC_GENERATION_ACTION_REQUIRED until a human delivers it
- overlay renderer (Pillow): missing (import PIL)
- TikTok renderer (ffmpeg): missing (`ffmpeg` on PATH)
- release prerequisites (state/venture.json): missing (state/venture.json)
