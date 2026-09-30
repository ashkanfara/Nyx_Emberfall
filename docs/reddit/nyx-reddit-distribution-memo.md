# Nyx Emberfall: Reddit distribution decision memo

Date: 2026-09-29. Status: research only. Nothing was posted, commented, created, messaged, bought, or changed. Item20 was not accessed.

## 0. Read this first: the rules are not verified

The brief said to check every community's current rules on the community itself. I could not do that from this environment, and I am not going to pretend otherwise.

| Route tried | Result (2026-09-29) |
|---|---|
| reddit.com, old.reddit.com, oauth/api JSON rules endpoints | HTTP 403, Reddit "Blocked" page (Reddit blocks this cloud IP) |
| Web search restricted to reddit.com | Refused: reddit.com opts out of Anthropic's crawler |
| Redlib mirrors (safereddit.com etc.) | Behind an anti-AI-scraper proof-of-work wall. I did not bypass it on purpose |
| Wayback Machine / archive.ph | Connection reset / unreachable |
| support.reddithelp.com | 403 |

**So every subreddit-specific rule below is a hypothesis.** It comes from my training knowledge (which may be months out of date) or from secondary blogs. It is not a verified current rule. Before anything goes live, someone has to spend about 30 minutes in a normal browser checking each subreddit (see section 5). Until then the certainty for every per-subreddit rule claim is **Low**.

The parts that don't depend on live rule text are more solid: the strategic conclusion, the avoid logic, the account approach, and the test design.

## 1. Decision

**Don't make Reddit a distribution channel. At most, use it as a cheap 30-day feedback tool with a small time cap (about 2 hours a week) and no selling. Put the growth effort into Instagram and TikTok, with X as the link-out and conversion surface.**

Why (certainty: Medium-High):
- The subreddits that fit a character with a story (art, OC, writing, worldbuilding) are the ones most likely to ban AI content. The CHI 2025 study found AI rules cluster in art-focused communities and in the largest subreddits (17.1% of the top 1% had AI rules by Nov 2024, more than double the July 2023 rate) [S1]. Blogs from 2026 report enforced AI bans or disclosure rules in r/writing, r/worldbuilding and r/creativewriting [S3]. r/Art bans AI art and has banned an artist just for mentioning prints were available [S4].
- The subreddits that do allow AI are audiences of other creators and tool users. They respond to process and technique, not to a persona. Most of them probably don't want the person you're after (a buyer), and they punish anything that looks like marketing.
- The brief rules out NSFW. Without the NSFW route, a Fanvue link in a post is almost certainly spam in every candidate below. Conversion from SFW Reddit to Fanvue should be treated as roughly zero until proven otherwise.
- Reddit has no sitewide ban on AI content. Enforcement happens per community, and accounts get removed mostly for acting like marketing accounts [S2][S3]. That means the real cost is human time spent participating genuinely, and that doesn't scale.
- Upside: Reddit gives more honest, less algorithm-shaped feedback than any other platform on whether the character, story and visuals hold up. That is the only reason to touch it.

## 2. Account approach (applies to everything)

- Use **one openly disclosed creator account** ("I make Nyx Emberfall, an AI-generated fictional character"), run by the human operator and posting about process. Don't run an in-character "Nyx" account commenting across Reddit as if she were a person. Reddit's authenticity rule and most AI-friendly subs react far better to a creator showing their work than to a persona. An in-character account that isn't clearly labelled is the fastest path to a sitewide "manipulation" judgment.
- No alt accounts, no vote swaps, no posting the same thing to several subs, no DMs to users.
- Karma and account-age minimums are often set in AutoModerator and not published. Expect them to exist. Build history with 2 weeks of useful comments before the first post. Don't try to probe the thresholds.
- Profile link: leave it off until verification. Even then, link the Instagram, never Fanvue, on a SFW-context account.

Disclosure line to reuse (adjust to each sub's flair or tag convention):
> "Nyx is a fictional adult character I create with AI tools ([tool names]). Not a real person. Happy to share the workflow."

## 3. Candidate shortlist (ranked, all rules unverified)

Existence, current rules, flair and minimums must be checked in a browser before use. "Expected" means my best guess, not a fact.

### 1. r/aiArt: first experiment
- **Audience fit:** Medium. AI art makers and viewers who value novelty and craft. Character-driven series do exist there. Low buyer intent.
- **Rules (expected, verify):** AI content allowed; NSFW restricted or banned; self-promotion limited; flair probably required; may require tool disclosure. Certainty: Low.
- **Value-first angle:** How one original character stays visually consistent across a whole story arc.
- **Fanvue link:** Assume not allowed. Don't post it.
- **Reputation:** Unknown; assume a minimum. Comment for 2 weeks first.
- **Post concept:** "Same character, 12 scenes, one story: how I kept Nyx consistent (workflow + failures)". A grid of SFW story frames, then the method, then what broke.
- **Failure/ban risks:** removed as low-effort or repetitive; removed for suggestive content if a frame reads as sexualised; downvoted for "AI influencer" framing; removed as promotion if a profile link or follow CTA is included.

### 2. r/aivideo (only if Nyx has short narrative video)
- **Audience fit:** Medium. Viewers reward short narrative AI video with a real hook.
- **Rules (expected, verify):** AI video is the point of the sub; quality bar enforced; promotion and watermark limits likely. Certainty: Low.
- **Value-first angle:** A self-contained 30–60s story beat, not a trailer.
- **Fanvue link:** Assume not allowed.
- **Reputation:** Unknown.
- **Post concept:** "Nyx Emberfall, ep.1: [one-line hook]". A complete micro-story, with the tool stack in a comment.
- **Failure/ban risks:** read as an ad or trailer; TikTok/IG watermarks; mismatched flair; suggestive thumbnail.

### 3. The subreddit for the tool Nyx is actually made with (e.g. r/midjourney, r/StableDiffusion, or a Higgsfield/Kling sub if one is active)
- **Audience fit:** Low–Medium. Technical audience. Good for credibility, not for fans.
- **Rules (expected, verify):** Posts must use that tool. r/StableDiffusion is expected to require open-source/local workflows and to ban NSFW. Certainty: Low.
- **Value-first angle:** A reproducible technique (character reference setup, lighting or palette lock for the "ember" look).
- **Fanvue link:** Assume not allowed.
- **Reputation:** Unknown.
- **Post concept:** "Locking a signature colour palette across 50 generations: settings that worked".
- **Failure/ban risks:** removed as off-topic if the tool doesn't match (for example, posting closed-tool output to r/StableDiffusion); seen as "showcase spam" without the workflow.

### 4. r/SillyTavernAI (or a similar AI-companion/character-card community)
- **Audience fit:** High for the eventual buyer (people who pay for AI characters), which is why it's also the riskiest.
- **Rules (expected, verify):** Character cards and presets shared openly; NSFW handling and promotion rules unknown. Certainty: Low.
- **Value-first angle:** Give away a free, SFW Nyx character card (personality, lore, voice) that people can use locally. It tests whether the persona itself has pull.
- **Fanvue link:** Assume not allowed, and it would destroy trust here even if it were.
- **Reputation:** Unknown.
- **Post concept:** "Free card: Nyx Emberfall, a morally grey fire-mage narrator (SFW, lore-heavy)".
- **Failure/ban risks:** read as commercial funnelling; card quality judged harshly; NSFW expectations pulling the account off-brief. **Do only if** you're comfortable giving the persona away free, and only after candidates 1 and 2 are clean.

### 5. r/aiArt-adjacent backup (e.g. r/AIGeneratedArt or a similar showcase sub)
- **Audience fit:** Low–Medium; lower-signal feedback.
- **Rules:** Unknown. Certainty: Low.
- **Use:** Only if candidate 1 turns out, on checking, to be closed or hostile. Same angle as candidate 1, adapted.
- **Risks:** Same as candidate 1; the smaller the sub, the less the result means.

I stopped at 5. I couldn't name more that I'd honestly call viable without live rule checks, and padding the list would break the "no invented claims" requirement.

## 4. Avoid list

| Community | Why | Basis / certainty |
|---|---|---|
| r/Art | Bans AI art; bans even a light self-promotion mention | Wikipedia, citing 2022 and late-2025 incidents [S4]. Medium |
| r/writing, r/worldbuilding, r/creativewriting | Enforced AI bans or disclosure rules; "story value" can't be tested with AI output here | Secondary blog, May–Jul 2026 [S3]. Low–Medium |
| r/WesAnderson and other "real photos only" subs | AI images banned outright | CHI 2025 paper [S1]. Medium |
| Human-art and OC communities (r/characterdesign, r/DigitalPainting, r/drawing, r/ArtistLounge, r/OriginalCharacter, the "Imaginary" network) | Art communities are where AI rules cluster; many have AI bans | Training knowledge plus the pattern in [S1]. Low per sub, High for the category |
| Selfie, face, fashion, goth-style and "rate me" subs | Posting an AI persona as a person is deception under Reddit's authenticity rules; bans hit the whole account | Sitewide principle. High |
| Any NSFW or "promo" sub, including Fanvue/OF-style promo subs | Excluded by the brief. They'd also brand the account as an adult promoter | Brief. High |
| Marketing/entrepreneur subs used to plug Nyx | Promotion-hostile, wrong audience | [S3] notes they allow AI, but self-promotion is the problem. Medium |

## 5. Verification protocol (about 30 minutes, human, normal browser)

For each candidate, record in a sheet with the date: (a) sidebar rules screenshot; (b) whether AI is allowed or disclosure required; (c) NSFW/suggestive policy; (d) self-promotion or link rule, including any ratio; (e) flair or title tags; (f) posting frequency limit; (g) any pinned mod post about AI or promotion in the last 6 months; (h) the top 20 posts of the month: do character or series posts do well, and are any creator-labelled? Drop any candidate that fails (b), (c) or (d). Don't message mods to ask about promotion; the brief rules that out.

## 6. 30-day validation plan (proposed, not executed)

Goal: test whether Nyx's **story, art and process** earn engagement from strangers. Not sales.

| Days | Action | Cap |
|---|---|---|
| 0 | Verification protocol; lock the disclosure line; decide which account | 30 min |
| 1–14 | Comment-only, useful replies in the verified subs; no Nyx mention | ~10–15 comments |
| 15 | Post 1: r/aiArt consistency/workflow post | 1 post |
| 22 | Post 2: r/aivideo micro-story, or the tool sub, depending on what worked in post 1 | 1 post |
| 29 | Post 3: repeat the best-performing format, or the SillyTavern free card if 1–2 were clean | 1 post |
| 30 | Review against the metrics below | — |

Max 3 posts, 1 per sub, no cross-posting, no links, reply to every substantive comment within 24h.

**Success (all needed to continue):**
- 0 removals, 0 mod warnings.
- At least 2 of 3 posts with ≥75% upvote ratio.
- At least 2 posts that each get ≥5 substantive comments from other people (questions about the character, story or workflow; not "cool").
- At least 1 unprompted "where can I see more / is there more story" request (a demand signal, not a click).

**Stop immediately:**
- Any sitewide warning or suspension: stop all Reddit activity.
- Removal or warning in a sub for AI or promotion: stop in that sub; don't argue with mods.
- 2 posts under 50% upvote ratio, or mostly hostile "AI slop" responses.
- Time spent over 3 hours a week.

**Conversion:** not measured in this 30 days on purpose. If the success bar is met, a phase-2 test could add an Instagram link in the profile (never Fanvue) and track profile-driven IG follows. Fanvue linking from Reddit stays off unless a verified SFW community explicitly allows it.

## 7. Reddit vs Instagram / TikTok / Threads / X

| | Reddit | Instagram | TikTok | Threads | X |
|---|---|---|---|---|---|
| Tolerance for AI personas | Per sub; hostile in most art and story subs | Allowed with AI labelling | Allowed with synthetic-content disclosure | Allowed with Meta labelling | Most permissive |
| Discovery | Manual, one sub at a time | Algorithmic (Reels) | Algorithmic, strongest for new accounts | Weak–medium | Medium |
| Link to Fanvue | Effectively no (SFW) | Bio link, policy-sensitive | Bio link, policy-sensitive | Bio link | Most permissive of the four |
| Main value | Honest feedback, credibility with other creators | Brand and fan base | Reach tests for story hooks | Cheap spillover from IG | Conversion surface |
| Cost | High human time, high ban risk per mistake | Medium | Medium | Low | Low |

Platform disclosure summaries come from secondary 2026 compliance write-ups [S5]; check each platform's own help pages before relying on them. Certainty: Medium.

**Verdict:** your scarce resource is operator attention. Reddit returns feedback, not revenue, and each mistake costs a lot. Run the 30-day test only if someone will genuinely participate as a creator. Otherwise skip it and spend the hours on TikTok hook testing, which gives faster, cheaper story-value signal at scale.

## 8. Recommended first experiment

After the r/aiArt rules check passes: 2 weeks of comment-only participation from a disclosed creator account, then one post, **"Same character, 12 scenes: how I kept Nyx Emberfall consistent across a story (workflow + what broke)"**. SFW frames only, disclosure line in the body, no links, no CTA. Judge it by removal (none), upvote ratio (≥75%) and substantive comments (≥5). If it's removed or ratioed, Reddit is done for Nyx. That's a cheap, useful answer.

## Sources (accessed 2026-09-29)

- [S1] Lloyd et al., "AI Rules? Characterizing Reddit Community Policies Towards AI-Generated Content," CHI 2025. https://arxiv.org/html/2410.11698v2 (data: Jul 2023 and Nov 2024 crawls). Also https://dl.acm.org/doi/10.1145/3706598.3713292
- [S2] Versely, "Reddit has no site-wide ban on AI content," 20 Aug 2026. https://www.versely.studio/blog/reddit-has-no-site-wide-ai-ban (secondary)
- [S3] MediaFast, "Is AI-Generated Content Allowed on Reddit? (2026 Rules)," May 2026, updated Jul 2026. https://www.mediafa.st/is-ai-generated-content-allowed-on-reddit (secondary; paraphrases the Reddit Help Center)
- [S4] Wikipedia, "r/Art." https://en.wikipedia.org/wiki/R/Art ; PC Gamer on the 2022 ban: https://www.pcgamer.com/artist-banned-from-art-subreddit-because-their-work-looked-ai-generated/
- [S5] AuditSocials, "AI Influencer Content 2026: Disclosure Rules + EU AI Act." https://www.auditsocials.com/blog/ai-generated-influencer-content-compliance-disclosure-rules-2026 (secondary; not opened in full)
- Reddit sitewide rules: https://redditinc.com/policies/reddit-rules (reachable, but the rule text didn't render in this environment; the authenticity and anti-manipulation principle is cited from general knowledge. Medium)
- Per-subreddit rule pages (r/aiArt, r/aivideo, r/StableDiffusion, r/midjourney, r/SillyTavernAI): **not accessible; unverified**.
