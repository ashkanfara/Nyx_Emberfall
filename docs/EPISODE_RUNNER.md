# Autonomous episode runner: exception-only continuation

`nyx_runner.py` takes an approved story lock to a release package. Every ordinary stage resumes
on its own. The runner stops for only **four** durable, human-visible exceptions. It never
publishes externally, never opens a browser or social site, never uploads, posts or comments,
and never spends or changes an account. The Reddit workstream belongs to its own agent.

## The four exceptions

| # | Code | Raised when | Human action | Resumes |
|---|---|---|---|---|
| 1 | `EXC_GENERATION_ACTION_REQUIRED` | No approved unattended image executor (today: `human_manual`), a paid/unapproved one, or a delivered image with bad provenance | Generate from the handoff, then `record-asset` | Automatically on the next tick after the image arrives |
| 1 | `EXC_GENERATION_EXECUTOR_UNAVAILABLE` | The approved executor is missing, or still failing after its bounded retries | Restore it, or deliver by hand | Automatically when it is back or the image arrives |
| 2 | `EXC_QA_REPAIR_BUDGET_EXHAUSTED` | A slide failed visual QA **3** times | Simplify/re-sequence/drop the frame, or `grant-attempt` | Only after `grant-attempt`. Never automatic |
| 3 | `EXC_PUBLISH_APPROVAL_REQUIRED` | The release package is ready | `approve-release` once, publish yourself, then `confirm-published` per entry | Closes when every approved entry is confirmed published |
| 4 | `EXC_NON_RETRYABLE_FAILURE` | Access, credential, route or renderer failure that cannot be retried safely; transient failures past their bound; an invalid lock | Fix the cause | A missing tool (claude CLI, Pillow, ffmpeg) resumes automatically once installed. Everything else resumes after `unblock` |

Everything else is ordinary: QA verdicts, targeted repairs within the budget, captions, variants,
and **transient** failures (quota, timeout, an unparseable reply, a failed ffmpeg run). Transient
failures retry with bounded backoff (5, 10, 20, 40, 80, 160 min; 6 tries), then become exception 4.

Where each exception appears:
- `episodes/<id>/runner_state.json`: `status` and `exception`, with `category`, `title`, `detail`,
  `human_action`, `resumes` and `since`.
- `episodes/exception_queue.json`: the human inbox, with open exceptions plus resolved history.
- `episodes/RUNNER_STATUS.md`: the concise report.
- `episodes/release_queue.json`: the release packages and their entries.

## Activation readiness (no scheduler is installed)

```
python3 nyx_runner.py readiness
```

This states whether a scheduled runner would make meaningful progress **right now**, checks each
dependency (generation executor, reviewer CLI, Pillow, ffmpeg, `state/venture.json`) and lists the
remaining blockers. It is read-only. `ops/com.nyx.episode-runner.plist` exists but is **not**
installed. Install it only once readiness says `WOULD MAKE PROGRESS` (or once images are being
delivered regularly):

```
sed "s#__NYX_ROOT__#$PWD#g" ops/com.nyx.episode-runner.plist > ~/Library/LaunchAgents/com.nyx.episode-runner.plist
launchctl load ~/Library/LaunchAgents/com.nyx.episode-runner.plist
```

## Zero-spend mode: the ChatGPT batch (founder, 2026-10-01)

Until Nyx has proven income: `generation_executor` stays `human_manual`. No API, no paid
provider, no browser automation, no scheduler. One sitting per round:

1. When images are needed, the tick writes `episodes/<id>/batch/CURRENT.md`. It holds **every**
   image currently needed, as numbered ChatGPT messages:
   - shared rules
   - the character-absent slide
   - the Nyx primer, with the three generation-reference crops attached
   - one short message per slide

   It also gives the output size (ask ChatGPT for portrait 2:3, 1024x1536; keep the subject inside
   the central 4:5 area), the drop folder and each file name. Full per-slide prompts are kept
   alongside it, in `prompts/`.
2. Generate them in one conversation and save them as `slide<N>.png` (png, jpg or webp) in
   `generated_assets/carousel_item<N>/drop/`.
3. Run `python3 nyx_runner.py tick`. The tick then:
   - picks up each image and conforms it to 1080x1350 (Pillow, or macOS `sips`), keeping the original
   - binds provenance to the batch
   - runs visual QA, then any repairs as a new, smaller batch, then captions, variants and the release package

Drops that don't match the open batch are moved to `drop/unexpected/` with the reason and never
used: a wrong name, a slide not in the batch, a file older than the batch, or an attempt already reviewed.
Slides after slide 1 are drawn against slide 1 (the room master). If slide 1 is rejected, those
images are superseded **without** spending a QA attempt, and the next batch regenerates them with
the new slide 1. In the simulated episode, s1e02 takes two sittings: 6 images, then 1 repair.

## Two-episode automation trial (founder, 2026-10-01)

`episodes/automation_trial.json` has two switches, and **both ship off**. A switch counts as on
only with `"enabled": true` **and** a non-empty `enabled_by`.

| Switch | What it allows | Hard limits |
|---|---|---|
| `image_generation` | The runner calls the official OpenAI Images API (`openai_image_provider`) for the listed episodes. Sizes: 1088x1360 → 1080x1350 (4:5); 1152x2048 → 1080x1920 (9:16). The Nyx-free slide 1 is generated with **no** identity reference. | Max 2 episodes. **US$5 per episode** (`TRIAL_HARD_CAP_USD`); config can only lower it. A Fanvue chapter enrolled with `parent` shares its episode's cap. |
| `auto_publish` | READY entries on a **verified** route are published by the runner instead of stopping at exception 3. | Verified today: Fanvue only. Max one post per route per 24 h. |

Spend safety (`episodes/<episode>/spend_ledger.jsonl`, append-only, fsync'd):
- Before each call, a `reserve` entry at the call's upper-bound estimate. After it, a `settle`
  entry at the actual cost from OpenAI's returned usage. If `reserve + estimate` would pass the
  cap, nothing is sent; a `cap_reached` entry is written and the remaining images go to the
  zero-spend ChatGPT batch.
- A `reserve` with no `settle` is an **unknown outcome**. It counts at its full estimate and
  freezes that episode's paid generation, chapter included, until the founder reads the real cost
  off the OpenAI usage dashboard and runs
  `python3 nyx_runner.py settle-spend <episode> <call_id> <usd> --by <who>`. Nothing is ever re-sent blindly.

Publish safety: an attempt marker is written before each post and survives every queue rebuild.
A marker without a result is exception 4: check the account, then `confirm-published` or `unblock`.
Fanvue posts cannot be deleted through the API.

Routes held, with the reason recorded on each queue entry (`auto_publish_hold`):
- **Instagram and TikTok** (Metricool brand 6988018 is connected): `createScheduledPost` needs
  public https media URLs, and PS-05 has no approved media-hosting route.
- **Threads:** connection unverified.
- **X:** paid add-on, no account.

### Activation (founder, on the machine that holds `generated_assets/`)

1. `pip install openai Pillow`
2. `pbpaste | python3 openai_runtime.py store`. The key goes to gitignored `.openai_runtime/`
   (chmod 600) and never into a tracked file.
3. Fanvue token present (`.fanvue_runtime/`, refreshed by the existing tick), if auto-publish is wanted.
4. In `episodes/automation_trial.json`, set `image_generation.enabled: true` and `enabled_by`. When
   ready to publish unattended, do the same for `auto_publish`.
5. `python3 nyx_runner.py readiness` must show `image_generation_ready: true`, the episode's
   `executor: openai_trial` and a `next_tick` line. `generation_executor.json` stays
   `human_manual`: a trial episode uses the capped OpenAI route whatever that file says. Readiness
   never runs a tick, so the episode status it shows is the last tick's.
6. First paid run, one image only: `python3 nyx_runner.py tick --max-paid-calls 1`. That one tick:
   - closes the open ChatGPT batch (its files stay, marked `retired.json`)
   - makes exactly one OpenAI call
   - runs visual QA on the result
   - stops with `IN_PROGRESS`

   Check `generated_assets/carousel_item33/item33_slide1.png`, the QA evidence in
   `episodes/s1e02_public/qa/` and `episodes/s1e02_public/spend_ledger.jsonl`. Then repeat with a
   larger N, or drop the flag. Auto-publish stays off unless you switch it on.

## Generation executor

`episodes/generation_executor.json` decides who produces images:

- `"mode": "human_manual"` (current): every handoff raises exception 1 until a person delivers it.
- `"mode": "command"`: an unattended executor the runner may call. All of these are required:
  `approved_by`, `"cost": "zero"`, and `command` (placeholders `{packet} {prompt} {output} {width} {height}`).
  Paid generation is never run automatically, and no browser route belongs here.

## Commands

```
python3 nyx_runner.py tick                      # idempotent; the scheduler's only command
python3 nyx_runner.py status | readiness
python3 nyx_runner.py record-asset <story_id> <slot> <image.png> --by <who> --provider <name>
python3 nyx_runner.py grant-attempt <story_id> <slot> --by <who>          # exception 2
python3 nyx_runner.py approve-release <story_id> --by <who>              # exception 3
python3 nyx_runner.py confirm-published <story_id> <platform> <url> --by <who>
python3 nyx_runner.py unblock <story_id>                                 # exception 4
```

A package approval covers media and captions exactly as they were. If either changes, the approval
is invalidated and exception 3 reopens. Routes that are not LIVE (Threads, X) are `HELD_NO_ROUTE`
and never block the package.

## Durability

- Write-then-rename for every file. Each step re-derives from disk.
- QA order: evidence file, then story state, then the `recorded` flag. A crash between any two never
  double-counts a verdict.
- One tick at a time (`flock`, released by the OS on crash).
- Simulated adapters, executors, images and verdicts are refused outside a sandbox.

## Proof

```
python3 nyx_runner.py simulate                 # human executor: stops only at generation and approval
python3 nyx_runner.py simulate --unattended    # approved simulated executor: one run to approval
python3 nyx_runner.py simulate --interrupt     # injected crashes, same end state
python3 -m unittest test_nyx_runner test_episode_coordinator
```
