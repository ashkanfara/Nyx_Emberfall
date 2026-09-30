# Nyx episode runner -- status

Automation ends at READY_FOR_PUBLISH. Nothing here generates, uploads, posts or spends.

| Episode | Status | Slides approved | Next action |
|---|---|---|---|
| s1e02_public | WAITING_ON_EXECUTOR | 0/6 | generation executor: deliver the open handoff with record-asset; slide 1: WAITING_ON_EXECUTOR |
| ↳ Fanvue chapter | NO_APPROVED_LOCK | - | founder: approve a Fanvue chapter lock and enrol it with parent s1e02_public |

## Release queue

Empty.

## Live-integration blockers (this machine)

- variants: Pillow not installed (overlay burn-in)
- variants: ffmpeg not installed (TikTok slideshow)
- release: state/venture.json absent here -- the publisher reads content_items from it
- visual QA: the claude_cli_vision reviewer is wired and tested with a simulated reviewer only; it has never scored a real Nyx image -- spot-check its first verdicts
- generation: no autonomous zero-cost executor exists; each handoff needs the founder (ChatGPT, interactive) or an approved separate automation to run record-asset
- trigger: ops/com.nyx.episode-runner.plist must be installed with launchctl on the machine that holds generated_assets/
