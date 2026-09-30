# Nyx Reddit community matrix

Generated 2026-09-30 by `python3 reddit_ops.py matrix`. Do not hand-edit; record rules with `reddit_ops.py record-rule`. A rule older than 14 days counts as unverified.

| Rank | Community | Route | Route basis | Status | Gate-ready | ai_content | self_promotion | nsfw | links | account_minimums | flair | frequency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | r/AIGeneratedArt | native_post | observed_summary | **unverified** | no | 2026-09-30 (summary) | 2026-09-30 (summary) | 2026-09-30 (summary) | 2026-09-30 (summary) | 2026-09-30 (summary) | 2026-09-30 (summary) | 2026-09-30 (summary) |
| 2 | r/AIArtwork | native_post | provisional | **unverified** | no | — | — | — | — | — | — | — |
| 3 | r/ChatGPT | native_post | provisional | **unverified** | no | — | — | — | — | — | — | — |
| 4 | r/aiArt | designated_thread | observed_summary | **unverified** | no | 2026-09-30 (summary) | 2026-09-30 (summary) | — | — | — | — | — |
| 5 | r/aivideo | native_post | provisional | **unverified** | no | — | — | — | — | — | — | — |
| 6 | r/dalle2 | unknown | provisional | **unverified** | no | — | — | — | — | — | — | — |
| 7 | r/OpenAI | unknown | provisional | **unverified** | no | — | — | — | — | — | — | — |
| 8 | r/midjourney | closed | provisional | **unverified** | no | — | — | — | — | — | — | — |
| 9 | r/StableDiffusion | closed | provisional | **unverified** | no | — | — | — | — | — | — | — |
| 10 | r/SillyTavernAI | unknown | provisional | **unverified** | no | — | — | — | — | — | — | — |

**First three once the account is eligible** (decided 2026-09-30): r/AIGeneratedArt → r/AIArtwork → r/ChatGPT. Order is provisional for everything except r/AIGeneratedArt's observed summary; exact rule text is required before any of them can be approved.
- r/AIGeneratedArt: Only candidate whose live rules were actually observed (by the founder) and showed no self-promotion or AI restriction; fits the story set.
- r/AIArtwork: General AI-art showcase; native route expected; different angle (single atmospheric image) from the story set.
- r/ChatGPT: Matches the real generator, so a technique post is on-topic; highest removal risk for low effort, so it goes third.
- Not in the top three: r/aiArt is designated-thread only (not native); r/aivideo needs a video; r/midjourney and r/StableDiffusion are tool mismatches; r/SillyTavernAI, r/dalle2 and r/OpenAI are observe-only.

Route basis: observed_summary = the founder read the live page; provisional = training knowledge only, the rules page could not be read from the build environment.

Cells show the date each rule was verified in a browser. "(summary)" = an operator's summary, which can close a route but never approve one; — = not verified.

## r/AIGeneratedArt

- Route: native_post (observed_summary): Founder saw only flair, no-children and NSFW rules on 2026-09-30; no self-promotion or AI restriction shown.
- Audience fit: Low-medium.
- Value angle: Story-first micro-fiction from the same episode, not a copy of the r/aiArt process post.
- Provisional basis: Founder browser check 2026-09-30: only flair, no-children and NSFW rules shown. Now the primary native candidate; verbatim rule text and flair options still to capture.
- Snapshot 2026-09-30 by founder (operator_summary, [source](https://www.reddit.com/r/AIGeneratedArt/about/rules/)): Only three rules shown: flair, no children, NSFW. Rules shown: flair, no children, NSFW.
- **ai_content**: allowed=None, disclosure=None. "OPERATOR SUMMARY: no AI-content or disclosure rule shown." [source](https://www.reddit.com/r/AIGeneratedArt/about/rules/), verified 2026-09-30 by founder, evidence: operator_summary. Note: sub topic is AI art; disclosure still included by house policy
- **self_promotion**: None. "OPERATOR SUMMARY: no self-promotion rule shown (only flair, no-children, NSFW)." [source](https://www.reddit.com/r/AIGeneratedArt/about/rules/), verified 2026-09-30 by founder, evidence: operator_summary. Note: absence on the rules page is not permission; check sidebar/wiki
- **nsfw**: allowed=None. "OPERATOR SUMMARY: an NSFW rule is shown; wording not captured." [source](https://www.reddit.com/r/AIGeneratedArt/about/rules/), verified 2026-09-30 by founder, evidence: operator_summary. Note: irrelevant to SFW-only drafts but wording still needed
- **links**: external=None, fanvue=False. "OPERATOR SUMMARY: no link rule shown." [source](https://www.reddit.com/r/AIGeneratedArt/about/rules/), verified 2026-09-30 by founder, evidence: operator_summary. Note: fanvue_allowed=false: nothing explicitly allows it
- **account_minimums**: age=None, karma=None. "OPERATOR SUMMARY: no account-age or karma rule shown." [source](https://www.reddit.com/r/AIGeneratedArt/about/rules/), verified 2026-09-30 by founder, evidence: operator_summary. Note: hidden AutoModerator thresholds may still apply
- **flair**: required=True. "OPERATOR SUMMARY: a flair rule is shown; flair options not captured." [source](https://www.reddit.com/r/AIGeneratedArt/about/rules/), verified 2026-09-30 by founder, evidence: operator_summary. Note: treated as required (conservative)
- **frequency**: none published. "OPERATOR SUMMARY: no posting-frequency rule shown." [source](https://www.reddit.com/r/AIGeneratedArt/about/rules/), verified 2026-09-30 by founder, evidence: operator_summary

## r/AIArtwork

- Route: native_post (provisional): General AI-art showcase; expected to accept AI images natively. Rules page not readable from the build environment on 2026-09-30 (reddit.com returns 403 and opts out of Anthropic's crawler); nothing below is a verified rule.
- Audience fit: Medium: general AI-art viewers; single striking images do best.
- Value angle: One atmospheric single image from the episode with a two-line story hook; no process lecture.
- Provisional basis: Training knowledge only; existence, activity and rules unverified.
- **ai_content**: NOT VERIFIED
- **self_promotion**: NOT VERIFIED
- **nsfw**: NOT VERIFIED
- **links**: NOT VERIFIED
- **account_minimums**: NOT VERIFIED
- **flair**: NOT VERIFIED
- **frequency**: NOT VERIFIED

## r/ChatGPT

- Route: native_post (provisional): Tool community for the generator Nyx actually uses (ChatGPT image generation). Very high volume; low-effort image posts are commonly removed. Rules page not readable from the build environment on 2026-09-30 (reddit.com returns 403 and opts out of Anthropic's crawler); nothing below is a verified rule.
- Audience fit: Medium: tool users; rewards reproducible technique, not showcases.
- Value angle: Reference-first prompt structure for keeping one character consistent across an episode.
- Provisional basis: Training knowledge only; current image-post and self-promotion rules unverified.
- **ai_content**: NOT VERIFIED
- **self_promotion**: NOT VERIFIED
- **nsfw**: NOT VERIFIED
- **links**: NOT VERIFIED
- **account_minimums**: NOT VERIFIED
- **flair**: NOT VERIFIED
- **frequency**: NOT VERIFIED

## r/aiArt

- Route: designated_thread (observed_summary): Founder 2026-09-30: ordinary self-promotion forbidden, AI projects need mod approval, self-promo thread is the only route.
- Audience fit: Medium: AI art makers/viewers; rewards craft and process; low buyer intent.
- Value angle: One-image, process-first entry in the designated self-promo thread; no link.
- Provisional basis: Founder browser check 2026-09-30: ordinary self-promotion forbidden; AI projects need mod approval; the designated self-promo thread is the only route. Native posting is closed (mods are not to be contacted).
- **Closed route** `native_post`: AI projects need mod approval, not granted (contacting mods is out of scope)
- Snapshot 2026-09-30 by founder (operator_summary, [source](https://www.reddit.com/r/aiArt/about/rules/)): Ordinary self-promotion is forbidden. AI projects need mod approval. The designated self-promo thread is the only possible route. Rules shown: no ordinary self-promotion, AI projects require mod approval, designated self-promotion thread.
- **ai_content**: allowed=None, disclosure=None. "OPERATOR SUMMARY: AI projects need mod approval." [source](https://www.reddit.com/r/aiArt/about/rules/), verified 2026-09-30 by founder, evidence: operator_summary. Note: general AI-post rules and disclosure format not captured
- **self_promotion**: promo_thread_only. "OPERATOR SUMMARY: ordinary self-promotion is forbidden; the designated self-promo thread is the only route." [source](https://www.reddit.com/r/aiArt/about/rules/), verified 2026-09-30 by founder, evidence: operator_summary. Note: verbatim text not captured
- **nsfw**: NOT VERIFIED
- **links**: NOT VERIFIED
- **account_minimums**: NOT VERIFIED
- **flair**: NOT VERIFIED
- **frequency**: NOT VERIFIED

## r/aivideo

- Route: native_post (provisional): Only once an episode has a finished short narrative video; none exists.
- Audience fit: Medium: short narrative AI video with a real hook.
- Value angle: A complete 30-60s story beat, not a trailer.
- Provisional basis: Training knowledge only. Only relevant if Nyx has a finished short narrative video.
- **ai_content**: NOT VERIFIED
- **self_promotion**: NOT VERIFIED
- **nsfw**: NOT VERIFIED
- **links**: NOT VERIFIED
- **account_minimums**: NOT VERIFIED
- **flair**: NOT VERIFIED
- **frequency**: NOT VERIFIED

## r/dalle2

- Route: unknown (provisional): OpenAI image community named for DALL-E; unclear whether ChatGPT image generation output is on-topic. Rules page not readable from the build environment on 2026-09-30 (reddit.com returns 403 and opts out of Anthropic's crawler); nothing below is a verified rule.
- Audience fit: Low-medium.
- Value angle: Only if the sub confirms current OpenAI image output is on-topic.
- Provisional basis: Training knowledge only.
- **ai_content**: NOT VERIFIED
- **self_promotion**: NOT VERIFIED
- **nsfw**: NOT VERIFIED
- **links**: NOT VERIFIED
- **account_minimums**: NOT VERIFIED
- **flair**: NOT VERIFIED
- **frequency**: NOT VERIFIED

## r/OpenAI

- Route: unknown (provisional): Company/news community; image showcases likely off-topic or low-effort. Rules page not readable from the build environment on 2026-09-30 (reddit.com returns 403 and opts out of Anthropic's crawler); nothing below is a verified rule.
- Audience fit: Low: news and product discussion.
- Value angle: None for now; observe.
- Provisional basis: Training knowledge only.
- **ai_content**: NOT VERIFIED
- **self_promotion**: NOT VERIFIED
- **nsfw**: NOT VERIFIED
- **links**: NOT VERIFIED
- **account_minimums**: NOT VERIFIED
- **flair**: NOT VERIFIED
- **frequency**: NOT VERIFIED

## r/midjourney

- Route: closed (provisional): Tool mismatch: Nyx frames are made with ChatGPT image generation, not Midjourney.
- Audience fit: Low-medium: technical tool users; credibility, not fans.
- Value angle: Reproducible technique for a signature palette/character lock.
- Provisional basis: Training knowledge only. Eligible ONLY if the posted frames were actually made with Midjourney. Swap for the sub of the real tool.
- **ai_content**: NOT VERIFIED
- **self_promotion**: NOT VERIFIED
- **nsfw**: NOT VERIFIED
- **links**: NOT VERIFIED
- **account_minimums**: NOT VERIFIED
- **flair**: NOT VERIFIED
- **frequency**: NOT VERIFIED

## r/StableDiffusion

- Route: closed (provisional): Expected open-source/local-workflow requirement; Nyx uses a closed tool.
- Audience fit: Low-medium: technical; hostile to closed-tool showcases.
- Value angle: Only a genuinely open-source workflow write-up.
- Provisional basis: Training knowledge: expected open-source/local-workflow requirement and SFW-only. Excluded in practice if Nyx is made with closed tools.
- **ai_content**: NOT VERIFIED
- **self_promotion**: NOT VERIFIED
- **nsfw**: NOT VERIFIED
- **links**: NOT VERIFIED
- **account_minimums**: NOT VERIFIED
- **flair**: NOT VERIFIED
- **frequency**: NOT VERIFIED

## r/SillyTavernAI

- Route: unknown (provisional): Character-card community; highest funnel-perception risk; observe only until native posts are clean.
- Audience fit: High for AI-character users; high promo sensitivity.
- Value angle: Free SFW lore-heavy character card given away with no strings.
- Provisional basis: Training knowledge only. Closest audience to eventual buyers, highest perceived-funnel risk. Observe until posts 1-2 are clean.
- **ai_content**: NOT VERIFIED
- **self_promotion**: NOT VERIFIED
- **nsfw**: NOT VERIFIED
- **links**: NOT VERIFIED
- **account_minimums**: NOT VERIFIED
- **flair**: NOT VERIFIED
- **frequency**: NOT VERIFIED

