# Nyx Emberfall

Dedicated workspace for Nyx’s canon, content production, quality assurance, distribution, Reddit experimentation, and measurement.

## Episode coordinator

`python3 episode_coordinator.py dry-run <story_id>` walks one episode through brief, locked prompts, generation handoff, visual QA, targeted repair, captions, platform variants and the publish gate. It never generates, uploads, publishes or spends. Stage status and the smallest blocker to a live run are written to `episodes/<story_id>/run_ledger.json` and `STATUS.md` (tracked). Current: `episodes/s1e02_public/STATUS.md`.

## Autonomous episode runner

`python3 nyx_runner.py tick` advances every approved, enrolled episode (`episodes/runner_registry.json`) through handoff, visual QA, bounded repair, captions, platform variants and the Fanvue chapter into `episodes/release_queue.json`, stopping only for four explicit exceptions (generation, 3 failed QA attempts, final publish approval, non-retryable failure). `python3 nyx_runner.py readiness` says whether a scheduler would make progress. Status: `episodes/RUNNER_STATUS.md`. Trigger, commands and rules: `docs/EPISODE_RUNNER.md`.

## Voiceover Reels

`python3 nyx_reels.py validate|plan|render <story_id>` turns an episode's approved slides plus a supplied voice file into a 15-25 s 1080x1920 Reel, with captions and ducked music. It never generates audio, calls an API or publishes. Voice rules: `brand/nyx_voice/`. Guide, free test path and paid-voice gate: `docs/VOICEOVER_REELS.md`.
