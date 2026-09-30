# Nyx Emberfall

Dedicated workspace for Nyx’s canon, content production, quality assurance, distribution, Reddit experimentation, and measurement.

## Episode coordinator

`python3 episode_coordinator.py dry-run <story_id>` walks one episode through brief, locked prompts, generation handoff, visual QA, targeted repair, captions, platform variants and the publish gate. It never generates, uploads, publishes or spends. Stage status and the smallest blocker to a live run are written to `episodes/<story_id>/run_ledger.json` and `STATUS.md` (tracked). Current: `episodes/s1e02_public/STATUS.md`.

## Autonomous episode runner

`python3 nyx_runner.py tick` advances every approved, enrolled episode (`episodes/runner_registry.json`) through handoff, visual QA, bounded repair, captions, platform variants and the Fanvue chapter into `episodes/release_queue.json`, stopping at READY_FOR_PUBLISH for a human. Status: `episodes/RUNNER_STATUS.md`. Trigger, commands and rules: `docs/EPISODE_RUNNER.md`.
