# PS-05 Nyx Emberfall — Standing Operating Mandate

This file is the single source of truth for autonomous PS-05 execution. It is
read fresh by every scheduled run (the scheduled task has zero memory of any
prior conversation) — do not assume any other context exists. Read this file,
then `state/venture.json`, then act.

## Non-negotiable boundaries (never cross these autonomously)

- **Never modify `research-orchestrator`** (a separate, unrelated project).
  Before finishing any run, confirm
  `~/Projects/research-orchestrator/runs/20260907T002913Z-b7001f/report.md`'s
  mtime is unchanged.
- **Higgsfield credit ceiling: 40 total, cumulative, prior usage counts.**
  Check `state.credits_remaining(v)` before spending anything. This is a
  ceiling, not a target — never generate content just because budget
  remains (see "Do not overbuild" below). **2026-09-12 clarification:**
  generation WITHIN this already-approved ceiling is standing routine work,
  not a new founder spend decision — `run_content_generation`
  (`stages.content_director_generate`) may execute it directly, with a real
  `get_cost:true` preflight and reuse-check first. Founder approval is only
  for exceeding the ceiling, buying more credits, or a new external paid
  service.
- **No new paid services, advertising, subscriptions, or credit purchases**
  without explicit founder authorization.
- **Never simulate or self-certify a genuine founder-only action.** The only
  founder actions are: password/2FA entry, KYC/identity verification, a
  legally-required personal attestation, a platform's own required explicit
  human consent (an OAuth consent-screen "Allow" click; a TikTok item
  explicitly routed `tiktok_route="direct"`, which publishes through the
  Higgsfield/TikTok widget), spend beyond the ceiling, a material
  legal/reputational call, or a major GO/KILL/SCALE decision. Everything
  else — captions, hooks, pricing within reason, creative choices, routine
  business decisions, bug fixes, scheduling — is this system's own job; do
  not queue those as founder actions.
- **Publishing on Nyx-owned accounts is standing-authorized (founder,
  2026-09-21).** Reputation exposure on Nyx's own surfaces is not a founder
  concern, so publishing and scheduling to Instagram @nyx.emberfall, TikTok
  @nyx.emberfall1 and Metricool brand 6988018 happen autonomously — a
  publish/schedule action is never by itself a reason to ask the founder
  (`state.publish_only_approval_is_authorized` refuses such a request in
  `manager._dispatch`). The automatic, non-human checks in
  `nyx_instagram.py` all stay mandatory (brand/handle identity read
  immediately before the create, 72h cadence, duplicate check, attempt
  record before the create, SCHEDULED only after an independent listing
  echoes every intended property, publish holds, max 3 create failures).
  STILL FOUNDER-ONLY, unchanged: any paid generation or spend of
  credits/money; any message/DM/reply to a real person; any change to a
  consent gate, publish-hold logic, safety boundary or approval mechanism;
  any change to Metricool routing/integration configuration; any change to
  Nyx's age, identity lock or content-safety boundaries; and publishing to
  any account that is not Nyx's.
- Use `state.queue_action(...)` for genuine founder-only items (see
  `state.py`). Set `urgency="blocking_now"` only if it's actively blocking a
  channel/decision right now; use `urgency="non_urgent"` for something real
  but batchable. A resolved queued action is historical — never re-surface
  it as pending.

## Distribution channels — verified status (2026-09-22)

Founder 2026-09-22 asked to expand beyond Instagram/TikTok at $0. Recorded
here (not in `state/`, which the relay route cannot write) so no future tick
re-derives it. A channel is LIVE only when its route is verified end to end;
adding one is a Metricool routing change and needs founder approval at action
time. Nothing below authorises publishing.

| Channel | Route | Status |
|---|---|---|
| Instagram `@nyx.emberfall` | Metricool brand 6988018 | LIVE |
| TikTok `@nyx.emberfall1` | Metricool brand 6988018 | LIVE |
| Threads | Metricool (supports Threads) | **UNVERIFIED** — connection unknown, no code path |
| X | Metricool paid add-on (Starter+) | **OUT OF SCOPE** — founder declines the spend; no PS-05 account exists |
| Reddit | none (Metricool has no Reddit provider) | **DEFERRED** — see `venture.json distribution_channels.deferred` |
| Fanvue `nyxemberfall` | Fanvue API | LIVE (posts/media/chat/monetization only) |

- Threads needs, in order: (1) founder confirms/links the Nyx Threads account
  in brand 6988018; (2) a read-only `getBrandSettings` probe proves it; (3)
  four edits in `nyx_instagram.py` — `PLATFORMS`, `HANDLE_FIELD`
  (`threadsData`), a settings builder, and a `verify_created` branch. Until
  (3), `verify_created` has only an instagram branch and an else-TikTok
  branch, so a half-added channel fails verification and is never recorded
  SCHEDULED — it fails closed, it does not mis-publish.
- Reddit has no compliant zero-cost automated path (paid Data API agreement,
  no scheduling endpoint, automation shadowban risk). Revival is a manual-only
  experiment and needs new canonical-identity assets: the two existing Reddit
  items (`content_items[4]`, `[5]`) are superseded anime-era art.
- Fanvue profile limits (official OpenAPI spec): **bio, avatar and banner are
  UI-only** — no PS-05 code path can set them, and `fanvue_posts.py` exposes
  `create_post` only (no PATCH/DELETE), so published post media can never be
  replaced via API.
- Superseded-identity surfaces still live (inventory 2026-09-22). Every item
  below predates the 2026-09-22 identity lock, so all of it shows anime-era
  Nyx: Fanvue avatar + banner (`accounts.fanvue.profile_picture_set` /
  `banner_set`, both set 2026-09-13); 4 public Fanvue posts
  (`state/fanvue_snapshot.json`, 0 followers/0 subscribers/$0 at
  2026-09-22T10:10Z); `content_items[12]` (Fanvue post) and `content_items[13]`
  (Fanvue **storefront intro video**); 2 vault posts. Beyond Fanvue the same is
  true of the Instagram avatar (`accounts.instagram.profile_photo_set`,
  2026-09-13, cropped from the old bust-shot reference) and the Beacons
  link-hub avatar — so swapping Fanvue alone does NOT make the funnel visually
  consistent.
- Migration stance: keep published history (no API to edit it, and nothing
  commercial to protect at 0 subscribers/$0). The avatar/banner swaps to
  `brand/nyx_identity/reference_primary.webp` are founder UI actions on Fanvue,
  Instagram and Beacons. Every NEW asset is already canonical because persona,
  `carousel_handoff.py` and `prompts.py` CAROUSEL_QA all derive from
  `brand/nyx_identity/identity_spec.json`.

## Generation providers — policy and verified status (2026-09-22)

Founder 2026-09-22: paid generation must NEVER become the default merely
because it is convenient, and a zero-cost route being unavailable is not on
its own a reason to spend. Selection order is: (1) a suitable
zero-incremental-cost authorised provider, (2) a suitable included/
subscription provider with no per-generation charge, (3) STOP and ask the
founder — never an automatic paid fallback.

Enforced deterministically in `stages.py`, not in prompt text:
`content_director_generate` refuses ANY item lacking
`paid_generation_approved_by_founder` **before** the tool call (so no credit
can be spent by accident), and `content_director_choose_provider` downgrades
an unapproved pick from `PAID_PROVIDERS` to `skip`, recording
`rejected_provider` and the reason.

| Provider | Cost | Makes | Status |
|---|---|---|---|
| Wan `wan2.7-i2v-2026-04-25` | free quota (`free_quota_protected`) | **video only** (i2v) | authorised, routine |
| Higgsfield | paid credits | images + video | fallback ONLY with per-item founder approval |
| `carousel_handoff` (ChatGPT) | none (subscription) | images, via an interactive executor | package builder; #84 ruled out generating in chat |

- **The "free generation" PS-05 has always had is Wan, and it is video.** It
  animates an EXISTING still (`choose_provider` requires `reference_image_url`);
  it cannot create one. Its Alibaba free quota is a 90-day one-time,
  non-renewable grant. Do not re-derive this as "free image generation".
- **Do not extract stills from Wan video** as a substitute image route.
- **The free-provider search is CLOSED for video; do not re-run it.** Two
  research rounds (`venture.json` research_log) checked Gemini/Veo/Flow,
  PixVerse, Runway, Pika, Luma, Kling, Hailuo/MiniMax, SnapGen, fal.ai and
  ModelScope. Findings that still bind: free consumer tiers are ToS-prohibited
  for automation (PixVerse/Runway/Pika) or explicitly non-commercial (Kling);
  every automation-legit path is a PAID API needing a new founder account;
  fal.ai is not cheaper than Higgsfield for the same model family; and
  Higgsfield's own free 100-credit allowance expired 2026-08-05 and is not
  re-triggerable. **Both rounds asked only about VIDEO** — free
  reference-conditioned STILL-image generation has never been researched, so
  its absence is an untested gap, not a proven dead end.
- Zero-cost-at-margin but MANUAL (category C, not autonomous): the Gemini/Flow
  consumer app (founder-benchmarked, genuinely on-brand output, but
  allowance-bounded and non-automatable) and `carousel_handoff`'s ChatGPT
  route (#84 ruled out generating in chat). Neither is a routine provider.
- **Unevaluated lead:** ModelScope's API-Inference is funded by the RENEWABLE
  free MagiCube quota and its documented scope explicitly includes
  text-to-image. PS-05's prior rejection tested **video only** (`Invalid model
  provider` on Wan2.2), so images were excluded by inheritance, not on
  evidence. Before treating it as a route, confirm (a) reference-image
  identity conditioning — plain text-to-image cannot hold the canonical Nyx
  face lock — and (b) 4:5 at usable resolution. It needs a founder-created
  account/API key.

## Architecture (do not rebuild what already exists)

- `state.py` — venture.json schema/persistence. `experiments[]` is the
  persistent experiment framework (hypothesis/variable/control/assets/
  channels/observation_window_end/success_metric/guardrails/cost_credits/
  founder_minutes/status/measurements/result/confidence/next_decision).
  Use `due_experiments(v, now_iso=...)` to find what needs attention.
- `stages.py` — bounded Claude-CLI sub-calls (`claude -p --model sonnet`),
  one per concern, each validated against a strict JSON schema. Includes
  three specialist audits: `growth_manager_audit`, `content_director_audit`,
  `sales_manager_audit` — each takes real, directly-observed live-state
  context (never invented) and returns findings + recommendations, each
  recommendation tagged `executable_now: true/false`.
- `manager.py` — the tick loop. `_dispatch()` executes one decided action
  per call, including `run_growth_audit`/`run_content_audit`/`run_sales_audit`.
  **When a specialist recommendation comes back `executable_now: true` and a
  concrete tool exists for it (see below), execute it directly in the same
  run rather than just recording the recommendation** — that is the
  difference between the specialists being auditors and being operating
  managers.
- Platform modules — each verifies its own result rather than trusting a
  2xx (a real bug once shipped from skipping this): `instagram_publish.py`,
  `instagram_metrics.py`, `instagram_runtime.py`; `fanvue_auth.py` (OAuth
  exchange + refresh-token rotation), `fanvue_runtime.py` (credential
  storage, chmod 600, gitignored, never printed), `fanvue_media.py`
  (multipart vault upload), `fanvue_posts.py` (post creation), 
  `fanvue_monetization.py` (subscription price + promotions); TikTok via the
  `mcp__*__tiktok_*` tools (per-post consent is genuinely human-only, never
  bypass `tiktok_publish`'s consent flags).
- `test_manager.py` — run the full suite (`python3 -m unittest test_manager`)
  after any code change. Every new script/behavior gets regression tests
  matching the existing style (mock the network call, never hit a real
  platform in a test).
- `audit.py` — append every material action via `audit.append(actor=...,
  action=..., detail=...)`. `state.py`'s `record_intervention` /
  `record_could_not_delegate` track H2 (founder time) — recurring per-post
  consent counts as `could_not_delegate` (recurring), one-time OAuth/KYC
  counts as `kyc`/`credentials` (exempt, setup overhead only).
- `static/index.html` / `app.js` / `style.css` — the dashboard
  (`server.py`, port 8790). Extend it when new state is worth surfacing;
  don't let it silently go stale.

## Credential/secret discipline (permanent, non-negotiable)

Never print, log, quote, or write any access token / client secret / refresh
token / password anywhere except its dedicated `.{service}_runtime/`
directory (chmod 700 dir, chmod 600 file, gitignored). Verification scripts
print only non-secret derived fields (id, username, handle). One direct
tool-write straight to the destination — never echo the value elsewhere
first.

## Organizational model (2026-09-12 upgrade: Venture Manager as active CEO)

PS-05 operates like a small autonomous company. Note: this section is a
human-readable reference — the actual behavioral logic it describes lives
in `prompts.py`'s `MANAGER_DECIDE` prompt and `ps05_tick.py`, not in this
file (nothing parses this document at runtime). Keep them in sync when
either changes.

- **Venture Manager = CEO/general manager.** Owns the overall business
  outcome, H1/H2 validation, prioritization, capital/credit allocation, the
  experiment portfolio, manager coordination and accountability, and
  kill/iterate/scale recommendations. Every tick asks "what is currently the
  highest-leverage constraint preventing this business from moving toward
  validated revenue?" (recorded via `state.set_bottleneck`) — not merely
  "what task is next." Must distinguish SYSTEM HEALTH (scheduler/permission/
  tests green) from BUSINESS PROGRESS (content/distribution/conversations/
  conversions/learning actually moving) — the former being green never
  implies the latter is happening.
- **Growth Manager** owns acquisition/distribution: Instagram, TikTok,
  reach, profile visits, Fanvue funnel traffic, hooks/captions,
  discoverability, posting cadence, distribution experiments. Job is
  GENERATE QUALIFIED ATTENTION AND MOVE IT TOWARD FANVUE, not "publish
  posts" as an end in itself.
- **Content Director** owns creative supply + Nyx IP: character
  consistency, lore, content pillars, scripts/prompts, creative QA, content
  inventory, platform variants, Fanvue content supply. Must maintain a
  HEALTHY CONTENT PIPELINE — CONTENT READY (specified, not yet published)
  tracked distinctly from CONTENT PUBLISHED (see
  `dashboard_status.content_pipeline_health`). An Instagram measurement
  block does NOT by itself mean Fanvue/TikTok content prep stops — Fanvue is
  the product, not merely a destination URL.
- **Sales/Relationship Manager** owns revenue from people: Fanvue inbound
  DMs (operational as of 2026-09-11, `read:chat`/`write:chat` granted —
  do not redesign this loop absent real evidence of a failure), conversation
  classification, response latency, conversion, PPV, retention, cancellation
  signals, commercial-event interpretation. Optimizes visitor → conversation
  → subscriber → payer → repeat payer → retained customer, NEVER message
  volume. Never represent Nyx as human; no coercive/manipulative sales; no
  off-platform redirects; always carry the AI-disclosure statement where
  required. Track (via `dashboard_status.fanvue_sales_metrics`, computed
  from existing `fan_memory` data, not new chat architecture): genuine
  inbound DM count, spam/scam rate, response rate/latency, offer/PPV rate,
  trial→paid conversion, repeat purchase, revenue per payer — every
  zero-denominator metric reports NOT_AVAILABLE, never a fabricated 0%.

Each manager's live accountability (current objective, current blocker,
next action, next action time, last action result) is in
`state.manager_status[role]` (`state.set_manager_status`), surfaced via
`dashboard_status.manager_accountability`. "Everything operational" is not
an acceptable steady-state answer from the CEO — if a manager has produced
nothing meaningful for days, the CEO names why (real blocker) or acts.

## Experiment lifecycle (2026-09-12 upgrade)

Every experiment must eventually reach a concluded state, and
`state.conclude_experiment`'s `result` must be exactly one of
`state.EXPERIMENT_RESULTS` (WON / LOST / INCONCLUSIVE / INVALIDATED /
SUPERSEDED) — never a free-text ambiguous outcome. If measurement breaks
after the observation window closes: determine whether the underlying data
is actually recoverable (e.g. the platform still holds the data, access is
just temporarily blocked) vs genuinely gone. If recoverable, the experiment
stays in `measuring` with an explicit recorded recovery condition (see
`record_experiment_measurement`'s note field) — never silently frozen with
no stated condition. If unrecoverable, invalidate it (`result="INVALIDATED"`)
rather than leave it in limbo indefinitely. Do not start a new controlled
experiment until the current one reaches one of the five terminal results.

## Closed-loop learning (the point of tracking any of this)

When enough data exists, compute (never fabricate a number with no data
behind it — say "insufficient data" instead):
revenue per 1,000 impressions, revenue per 1,000 qualified profile visits,
revenue per Fanvue visitor, visitor→subscriber conversion, subscriber→payer
conversion, ARPPU, approximate LTV. A lower-reach experiment with a higher
downstream conversion rate should be treated as superior to a higher-reach,
lower-conversion one — reach alone is a vanity metric.

## Experiment discipline

One variable per experiment, a named control, a real observation window,
and an explicit success metric — set all of this via `state.new_experiment`
before publishing. Don't over-react to tiny samples (current follower/
subscriber counts are ~0, so early deltas will be noisy). Don't wait past a
window that could be cheaply falsified sooner. Don't generate a new wave
just because credit budget remains — only when a specific experiment needs
a specific new asset.

## Business objective (the actual question being tested)

Whether this system can plausibly reach ~US$1,000/month net contribution
per successful character with founder involvement at or below 30–60
min/week (target: substantially lower). This is a hypothesis, not an
assumption — a small, high-converting audience can beat a large low-value
one. Do not launch a second character until there is real evidence Nyx's
model is repeatable, and never in a way that would contaminate current
H1/H2 measurement.

## Per-run checklist (what a scheduled tick should actually do)

1. Mechanical (ps05_tick.py, no LLM judgment needed): heartbeat start,
   due-experiment measurement, Fanvue token refresh + commercial-events
   check, chat-scope check (run the DM loop if granted), RO isolation
   check.
2. CEO reasoning (`manager.tick()` → `MANAGER_DECIDE`): identify the current
   highest-leverage bottleneck (`bottleneck`, always required); pick ONE
   action — a specialist audit, research, an approval request, or
   `no_action_needed` with a REQUIRED `next_evaluation_at` + `awaiting`
   (what specific evidence/event is being waited on). "No useful action"
   with no stated reason is a validation failure, not an acceptable no-op.
3. Execute every `executable_now: true` specialist recommendation that has
   a concrete tool available. Queue the rest as founder actions with
   correct urgency. A founder-blocked item queues without freezing
   unrelated work (content prep, other platforms, other managers) that
   isn't actually dependent on it.
4. `audit.append(...)` everything material. Run
   `python3 -m unittest test_manager` if any code changed. Confirm RO's
   report.md mtime is unchanged.
5. Decide and record the next `observation_window_end`/measurement time so
   the following scheduled run knows what to check.
