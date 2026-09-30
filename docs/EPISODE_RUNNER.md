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
