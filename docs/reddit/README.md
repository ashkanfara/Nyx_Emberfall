# Reddit workstream

Reddit stays **DEFERRED** in `channels.py`. This workstream only prepares and
gates drafts. No code here posts, comments, votes, messages or logs in, and
posting is always done by a person, by hand.

| File | What it is |
|---|---|
| `nyx-reddit-distribution-memo.md` | Research memo and decision (2026-09-29) |
| `community-matrix.md` | Generated. Dated rule-verification matrix |
| `no-post-list.md` | Generated. Communities that are never drafted for |
| `draft-queue.md` | Generated. Current drafts with gate result and fact sources |
| `../../nyx_reddit/` | Policy, rules, gate, variant stage, ledger |
| `../../reddit_ops.py` | CLI |

## How an episode becomes a Reddit draft

`ps05_ops.py apply-story <item> <lock>` runs the optional stage after the
story is saved. The stage can't change apply-story's result. To run it by hand:

    python3 reddit_ops.py variant brand/story_locks/<story>.json

The stage skips Fanvue and private episodes, observe-only communities, and
communities whose template doesn't fit the episode (no video, wrong tool). It
never considers the no-post list.

## What has to happen before anything is postable (all human steps)

1. **Verify the rules in a normal browser.** For each target sub, open its
   rules page, sidebar and wiki, and record all seven fields, quoting the rule
   text. Write `NOT PUBLISHED` where the sub says nothing.

       python3 reddit_ops.py record-rule r/aiArt ai_content '{"allowed":true,"disclosure_required":false,"disclosure_format":""}' https://www.reddit.com/r/aiArt/about/rules/ "<verbatim rule>" "<your name>"

   The fields are `ai_content`, `self_promotion` (allowed/ratio/promo_thread_only/none),
   `nsfw`, `links` (`fanvue_allowed` must be an explicit rule), `account_minimums`
   (null = not published), `flair` and `frequency`. Rules go stale after 14 days.

2. **Classify the sub:** `python3 reddit_ops.py classify r/aiArt approved_native "<your name>"`.
   This is refused unless all seven fields are fresh and consistent.

3. **Record the account snapshot** from the real profile:
   `python3 reddit_ops.py account <age_days> <karma> yes "<your name>"`.
   The profile must say it's a creator account for an AI-made fictional character.

4. **Warm up the account.** Leave at least 10 useful, on-topic comments over at
   least 14 days, with no Nyx mentions, and log each one:
   `python3 reddit_ops.py log-comment r/aiArt <permalink> "<what it helped with>" "<your name>"`.

5. **Generate the images** from each draft's image briefs (identity block and
   negative list included, 4:5, no overlay text). Save them under
   `generated_assets/reddit/<draft_id>/`, QA them against `reference_primary.webp`, then attach:
   `python3 reddit_ops.py attach-image <draft_id> <role> <path> yes pass "<your name>"`.

6. **Make the packet:** `python3 reddit_ops.py packet <draft_id>`. This re-runs the
   gate, and only a PASS writes `nyx_reddit/packets/<draft_id>.md`.

7. **Post by hand from the packet**, then record it:
   `record-posted <draft_id> <permalink> "<your name>"`.
   Record results with `record-outcome`. A removal, a ratio below 0.5, a clear
   negative response or any mod feedback halts the whole test.

Test limits: at most 3 posts in 30 days, one per sub, 7 days apart. No Fanvue
links during the test, whatever a sub allows.
