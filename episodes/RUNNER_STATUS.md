# Nyx episode runner -- status

Exception-only continuation. Ordinary stages resume on their own; the runner stops only for the four exceptions below and never publishes externally.

| Episode | Status | Slides approved | Exception / next | Resumes |
|---|---|---|---|---|
| s1e02_public | EXC_GENERATION_ACTION_REQUIRED | 0/6 | **#1 Image generation needs a human or an approved executor** -- open episodes/s1e02_public/batch/CURRENT.md, generate the 6 image(s) in one ChatGPT conversation and save them into generated_assets/carousel_item33/drop/ as slide1.png, slide2.png, slide3.png, slide4.png, slide5.png, slide6.png -- then run `python3 nyx_runner.py tick` | automatically on the next tick after the images are dropped |
| ↳ Fanvue chapter | NO_APPROVED_LOCK | - | a Fanvue chapter needs its own founder-approved lock, enrolled with parent s1e02_public | when enrolled |

## Release packages (never published by the runner)

None yet.

## Activation readiness (scheduler not installed)

**WOULD NOT MAKE MEANINGFUL PROGRESS: with no approved unattended generation executor, a scheduled tick would only re-report the open generation exception. No scheduler is needed in zero-spend mode: after dropping a batch's images, one `python3 nyx_runner.py tick` runs QA, repairs, captions, variants and the release package without further relays.**

- MISSING: generation executor -- no unattended executor is approved: a human generates each handoff and delivers it with record-asset
- ok: visual QA reviewer (claude CLI; credentials untested) -- `claude` on PATH
- ok: image conform (Pillow or macOS sips) -- fits ChatGPT 1024x1536 to the canvas
- ok: overlay renderer (Pillow) -- import PIL
- MISSING: TikTok renderer (ffmpeg) -- `ffmpeg` on PATH
- MISSING: release prerequisites (state/venture.json) -- state/venture.json

Remaining blockers to unattended progress:

- image generation: no unattended executor is approved: a human generates each handoff and delivers it with record-asset -- every slide stops at EXC_GENERATION_ACTION_REQUIRED until a human delivers it
- TikTok renderer (ffmpeg): missing (`ffmpeg` on PATH)
- release prerequisites (state/venture.json): missing (state/venture.json)
