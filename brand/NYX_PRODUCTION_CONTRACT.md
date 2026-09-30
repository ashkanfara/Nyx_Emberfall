# Nyx Production Contract (durable, effective 2026-09-30)

Founder-authorized structural fix, written after a session where the boundary
between "Claude decides the content" and "Claude drives the browser" got
blurred in the moment, under real time pressure, across many small requests.
This document — and `episode_manifest.py` / `episode_ops.py`, which enforce
it in code — replace relying on in-conversation judgment call each time.

## The three roles

**Claude PS5** (this codebase's agent role, whichever session is running it)
may only:
1. Author a **story pack** — a `brand/story_locks/*.json` lock file (unchanged
   existing mechanism: `ps05_ops.py apply-story`).
2. Author a **prompt pack** — the literal, final text for one slide's image
   generation, written to `generated_assets/carousel_item<N>/prompt_packs/`.
   Pure text. No browser call. No upload. No API call to any generation
   service. This is exactly the kind of prompt already demonstrated safe
   during this session's later turns (slides 1-4 of `s1e01_public`), now
   made a first-class, tracked artifact instead of ephemeral chat text.
3. Render a **QA decision** on an asset that already exists on disk — reading
   the file (the ordinary `Read` tool) and recording PASS/FAIL per dimension
   via `episode_ops.py qa-decide`. Claude PS5 never creates the asset it is
   judging.

Claude PS5 must **never**: open or drive a browser tab, upload a reference
image to ChatGPT or any other tool, call an image-generation API or trigger
one through a UI, publish or post to any platform, change any permission or
settings, or spend money. `episode_manifest.py` contains no function that
does any of these things — the boundary is structural, not a promise to
remember.

**Generation executor** — a human (the founder) or an explicitly separate
automation, never the Claude-PS5 session itself. Takes a prompt pack this
module wrote, pastes/feeds it into the already-prepared image-generation
conversation, and reports back a real file path. Claude PS5 may then run
`episode_ops.py record-generated` on that reported path — that command reads
a path it's told, it does not go fetch one.

**Publish executor** — a human or an explicitly separate automation, never
the Claude-PS5 session. Takes only `qa_passed` assets forward to a live
platform, verifies the result independently, and reports back the real live
URL/post id. Claude PS5 may then run `episode_ops.py record-published` — pure
bookkeeping after the fact, never the publish action itself.

## Episode/slide states

```
planned -> prompted -> generated -> qa_passed -> ready_to_publish -> published
                          \-> qa_failed -> prompted (re-prompt)

needs_human_action  (reachable from any state on a tool/permission failure)
```

- `needs_human_action` is a dead end for automation. Nothing auto-retries out
  of it, ever. A human calls `episode_ops.py reset-handoff` explicitly, only
  after the underlying cause is actually resolved — the handoff record stays
  in the slide's history, it is never erased.
- A slide parked in `needs_human_action` never blocks its siblings:
  `episode_ops.py next-actions <item>` always skips it and lists what's still
  actionable elsewhere in the same episode. This is the concrete form of
  "on failure, record a precise handoff state and continue unrelated stages."
- The one rule this contract exists to enforce end-to-end: **never retry the
  same denied action.** A handoff is recorded once; it is resolved by a human
  taking the actual step outside this pipeline (or by fixing the real
  permission), never by Claude PS5 calling the same tool again with different
  wording.

## Manifests

One JSON file per content item: `generated_assets/carousel_item<N>/episode_manifest.json`.
Append-only `history` per slide; current `state` is always the last word.
Nothing about pipeline state lives anywhere else (not in venture.json, not in
a chat message) — this file is the single source of truth for "what stage is
this slide actually at."

## Formats (founder, 2026-09-30)

- **Public story carousel standard: 4:5, 1080x1350.** Every public carousel slide
  is generated, QA'd and published at 4:5. It is what the pipeline actually renders
  (`story_continuity.dry_run`, `ps05_ops._slide_request`) and what
  `episode_coordinator.OUTPUT` enforces; a public lock that says otherwise fails the
  coordinator's brief stage. Older published locks that still read 9:16 are history,
  not precedent.
- TikTok gets the same 4:5 slides as a slideshow video on a 1080x1920 canvas, padded,
  never cropped.
- **Character-absent slides.** A slide may set `character_presence: "absent"` in its
  lock (e.g. `s1e02_public` slide 1, the Nyx-free opening at the door). Its compiled
  prompt replaces the identity block with an explicit no-person rule, attaches no
  identity reference, and QA checks that nobody is in frame. The identity lock is
  unchanged for every slide she appears in.

## Autonomous runner (founder, 2026-09-30)

`nyx_runner.py` is the Claude-PS5 side of this contract running unattended. It writes prompt
packs and handoffs, records QA from a reviewer that opens the image (`claude -p` vision,
verdict bound to the file's sha256), drafts captions and renders variants locally. It never
generates, uploads or publishes: images arrive from the generation executor through
`record-asset`, and every external publish stops at READY_FOR_PUBLISH in
`episodes/release_queue.json` for action-time human confirmation. See `docs/EPISODE_RUNNER.md`.

Exception-only continuation (founder, 2026-09-30): the runner stops only for (1) image
generation needing a human or an approved executor, (2) 3 failed visual-QA attempts on a slide,
(3) the one final publish approval of a release package, (4) a non-retryable access, credential,
route or renderer failure. Transient failures retry with bounded backoff. An unattended
generation executor runs only if `episodes/generation_executor.json` names it as approved and
zero-cost; the current mode is `human_manual`.

## Current real state (2026-09-30)

- `content_items[29]` (`s1e01_public`, "The Same Handwriting"): slides 1-4
  have prompt packs already written from this session's chat output; slide 1
  additionally carries pre-contract history (two rejected local-model
  attempts, recorded in the older `story_continuity` retry mechanism, kept
  for audit) and one recorded `needs_human_action` handoff (ChatGPT
  upload/navigation denials). Slides 5-6 are `planned` — the story pack
  covers them, no prompt pack written yet.
- `content_items[30-32]` (`s1e01_fanvue_part{1,2,3}`, Fanvue chapter): all
  slides `planned`. Slide 1 of part 1 also carries one pre-contract handoff
  record (two rejected local attempts) for audit.

No image has been generated, no asset has passed QA, nothing is
`ready_to_publish`, and nothing is published under this contract yet.
