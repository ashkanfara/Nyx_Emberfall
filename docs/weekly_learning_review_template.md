# Nyx Weekly Learning Review -- Template

Founder direction, 2026-09-29. This is a **template and process document**,
not a live report generator -- no code runs this automatically. Copy this
file's structure into a new dated review when a founder or a founder-
delegated session sits down to actually do the review, reading real data
from `scorecard.all_scorecards()` (see `scorecard.py`) and the existing
`state/venture.json` metrics.

## Core discipline: one meaningful variable at a time

Every week's review proposes **at most one** deliberate change to test next
week -- hook type, slide count within the tier range, posting time,
platform mix, cliffhanger style, episode pacing, etc. Never bundle two
changes into the same week; if last week already changed something, THIS
week's review must first read the result of THAT change before proposing
a new one. If evidence is inconclusive, the default action is "no change,
gather one more week" -- not "try something else instead," which would
burn the variable-isolation discipline for no reason.

This mirrors `unit_economics.py`'s own rule: a ratio with no real
denominator returns `insufficient_data`, it is never guessed. The same
applies here -- a review with too little data for a given metric says so
explicitly rather than drawing a conclusion from noise.

---

## Review: Week of <YYYY-MM-DD> to <YYYY-MM-DD>

### 1. What shipped this week
List every content item published this week, pulled from
`scorecard.all_scorecards()`:

| item_id | episode_id | platform | tier | format | slide_count | hook_type | published_at |
|---|---|---|---|---|---|---|---|

### 2. Raw numbers
For each item above, the scorecard's metric fields (`reach`, `views`,
`likes`, `comments`, `shares`, `saves`). Mark any field that is `None`
because it's `unavailable`/`unavailable_partial` per `scorecard.FIELD_SOURCES`
as **N/A (not collected)** -- never as zero, and never estimated.

| item_id | reach | views | likes | comments | shares | saves | completion/swipe proxy | profile/link clicks | subscriber conversion proxy |
|---|---|---|---|---|---|---|---|---|---|

### 3. Last week's variable: what did it show?
- **Variable changed last week:** <name it precisely, e.g. "public episode
  posting time moved from 20:00 to 21:30 Melbourne">
- **Prediction going in:** <what we expected to see, stated before this
  week's numbers were read>
- **What the data actually shows:** <compare against the prior baseline
  week(s); state the comparison window explicitly>
- **Conclusion:** KEEP the change / REVERT it / INCONCLUSIVE (need N more
  data points before deciding)
- **Confound check:** anything else that changed at the same time that
  could explain the result instead (a different episode's cliffhanger
  strength, a platform algorithm shift, a season-arc beat that's
  inherently higher/lower interest, a holiday, etc.)?

### 4. Qualitative QA notes worth carrying forward
Pull the `qa_notes` field from each scorecard -- anything a reviewer
flagged about clarity, swipe motivation, continuity, or filler (see the
CAROUSEL QA gates in `prompts.STORY_CAROUSEL_SPEC`) that the numbers alone
wouldn't show.

### 5. Season arc pacing check
Against `brand/season_arcs/season_1.json`:
- Which `episode_slots[]` shipped this week?
- Does the pacing (character_progression arc_beat for the episodes shipped
  so far) still match what's actually landing, or does the evidence
  suggest slowing down / speeding up a beat range?
- Any episode where the shipped `public_cliffhanger`/`fanvue_resolution`
  drifted from what the season file committed to, and why?

### 6. This week's ONE proposed variable
State exactly one change to test next week, the specific metric(s) it's
meant to move, and how many data points / how many days before it's
reviewed again. If nothing in sections 3-5 supports a specific hypothesis
yet, the correct entry here is **"No change -- insufficient signal yet,"**
not a change picked for its own sake.

- **Proposed variable:**
- **Target metric(s):**
- **Review again after:** <N items / N days>

### 7. Data gaps hit this week
Any field the review wanted but `scorecard.FIELD_SOURCES` marks
`unavailable`/`unavailable_partial` (per-slide swipe-through on Instagram,
profile/link clicks, per-post subscriber-conversion attribution -- see
`scorecard.py`'s NOTES 1-3) -- log it here so a real fix (a broader
Instagram insights scope, a Fanvue referrer scheme, etc.) has a running,
evidence-backed case file instead of being re-discovered from scratch
every week.
