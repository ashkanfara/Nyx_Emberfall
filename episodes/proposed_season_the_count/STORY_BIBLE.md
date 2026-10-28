# Nyx Emberfall -- proposed season "The Count" (story bible)

> **PROPOSED SEASON. NOT SCHEDULED, NOT PUBLISHED, NOT CANON.** This is a local content brief. No vote is
> live, no comment is being counted, nothing has been uploaded or scheduled, and no account or setting has
> changed. It does not edit `brand/season_arcs/season_1.json`, any `content_items` numbering, or any
> approved story lock. Every new fact below is a proposal until the founder approves it.

Written 2026-10-28.

The mechanic follows the general interactive-story pattern: the viewer is inside the scene, and every
episode ends on a binary choice. No other account's content, wording or structure is copied.

---

## 1. Where this picks up (canon inspected)

What exists and must not change:

| Source | What it fixes |
|---|---|
| `brand/nyx_identity/identity_spec.json` + the approved reference package | Nyx herself: adult (25+), dark fur on exactly two fox ears and one tail, amber eyes, beauty mark on her anatomical left cheek, no human ears, no earrings. Wardrobe as the reference. The black cat, the moonlit window, low-table and floor staging, the black ribbon and key, the door, all architecture. |
| `star_atlas_001`, `star_map_001` (pre-season) | The atlas has a phantom cluster circled in a hand that isn't hers. The star-map app shows a cluster that shouldn't be there. |
| `the_charm_001` (pre-season) | The charm (inked starburst) points at the old dark wooden trunk near the desk, half under boxes. She has never opened it and stopped with her hand on the latch: "bad idea?" |
| `music_box_001` parts 1-3 (pre-season) | The music box plays a tune she knows without knowing why. It lives **closed on the windowsill** with a **small worn photograph** and **her own folded note** inside. |
| `s1e01_public`, `s1e02_public` | The same hand in two places. An envelope with the starburst came under the door. |
| `episodes/deliveries_batch` (production draft; five reels approved by the founder) | Envelopes with one, two, then three stars. The star anise (a smell she can't place). The chart corner resting on the window glass (the circled stars are above her window). Shadows under the door. Her reply, a dark-blue envelope with a silver moon, was taken from the hallway side. The third envelope came back with her moon copied beside its three stars and is **still sealed on the low table**. |
| `season_1.json` (unchanged) | Central question: who has been leaving pieces of a life she doesn't remember? Planned future beats: the trunk and the charm, a recent dated note, the keeper's intent, opening the trunk, the finale reveal. |

**Pending vote carried into Episode 1.** Three Stars asked viewers to comment MOON if she should open the
third envelope. Its declared rule (`RELEASE_MANIFEST.md`): MOON ahead, or nobody answers -> she opens it;
"wait" ahead -> she waits. **The result is not recorded anywhere in this repo.** Episode 1 is written for
both outcomes. Whoever counts it records the real result in the vote log (section 4) before Episode 1's
master is made.

**How this sits against `season_1.json`.**
- It is a five-episode arc that escalates toward the season map's trunk and keeper beats without spending
  them. The trunk is approached, tried by the charm only if the audience chooses it, and **never opened**.
  The keeper is never identified.
- It uses the season map's "lock resists one notch short" beat only if the audience picks CHARM.
- It realises the "recent date / memory gap" beat (map slot s1e04) as a date written in moon phases.
- The founder decides whether this arc is its own season or folds into Season 1 ahead of the trunk
  finale. Nothing is renumbered here.

**Season-start continuity state** (the state every master and QA pass starts from):

| Thing | State |
|---|---|
| Third envelope (three starbursts + copied moon) | sealed, on the low table (until Episode 1's carry-forward) |
| First and second envelopes, star anise | opened / on the low table |
| Chart corner | resting against the window glass |
| Her dark-blue moon envelope | gone (taken from the hallway side) |
| Music box | closed on the windowsill; photograph and her folded note inside |
| Trunk | closed, near the desk, partly under boxes; never opened; lock untried |
| Charm | wherever the last approved frame shows it. Unclear in canon (`s1e01_fanvue_part3` puts it "in the closed box on the nightstand"), so the founder confirms its location before Episode 2 |
| Black ribbon and key | exactly as the reference package; meaning undecided; never named |
| Time | always night |

---

## 2. Format rules (every episode)

### 2.1 Reel structure (≤ 25 s, 9:16)

| Beat | Time (approx.) | What happens |
|---|---|---|
| 1. Carry-forward | 0-3 s | The visible consequence of the previous vote. If the vote was too low or tied, the declared canon branch is played and **Nyx says it was her own choice**. Optional community-recognition card here (2.4). |
| 2. Cold open | spoken over beat 1 or right after | One line from Nyx, **in second person**, putting the viewer in the room with her. |
| 3. Clue reveal | about 3-15 s | Camera exploration of the one master image: crops and push-ins reveal the clue exactly when she names it. |
| 4. Reaction | about 15-18 s | Crop to her face. |
| 5. Decision line | about 18-21 s | Nyx speaks a natural in-world decision line naming both options in her own words. Always **first**, before any card. |
| 6. Vote card | final ~3 s | Over the dimmed master: **exactly two visible choices** and the instruction **"Comment [A] or [B]"**, e.g. `Comment TRUNK or HIDE`. Nothing else on the card. |

### 2.2 Vote tokens
- One word, at most two, ALL CAPS on the card, easy to spell, distinct from each other and from every
  earlier token.
- Tokens are never in the voiceover as an instruction. She never says "comment", because the voice bible
  bans it. The card carries the instruction.

### 2.3 Caption format
Story line(s) in her voice, then the vote line, verbatim pattern:

```
<story line>

Comment <A> or <B> -- your vote shapes Episode <n>.
```
`<n>` is the next episode's number. Episode 5's vote shapes "Episode 6", the opener of whatever arc follows.
Instagram and TikTok use the platform AI label. Fanvue captions carry `#AIgenerated`.

### 2.4 Optional community-recognition slot
A 2-second card in beat 1, or a caption line, that can name **one real commenter**. It is **off by default**
and used only when a human has reviewed all of the following:
- [ ] The comment is real, on that episode, and in good faith. **Never invent or paraphrase a commenter.**
- [ ] The account shows no sign of being under 18 (when in doubt, don't feature it) and is not a brand, a
      bot or a promotion.
- [ ] The commenter has said yes to being featured, asked and answered by the founder personally.
- [ ] Only their handle and at most 8 words of their own comment appear; no personal details; nothing sexual.
- [ ] Removed promptly if they ask. Never on Fanvue, never paid, never implies they influenced more than
      their one vote.

### 2.5 Vote counting (manual; nothing automated exists)
- **Window:** 48 h from posting, Instagram and TikTok combined.
- **Valid vote:** a comment whose text contains exactly one of the two tokens (case-insensitive). One vote per
  account, and their latest valid comment counts. Comments containing both tokens don't count.
- **Too low:** fewer than **20** valid votes in total (the founder may change this before a season starts,
  never mid-vote), or a tie. Either way the declared canon branch plays, labelled as Nyx's choice.
- **Evidence:** screenshots of the counts saved before the next master is generated, and the result written to
  the vote log (section 4).
- **Never** show invented numbers on screen. Show the real count, or no count.
- **Risk to accept knowingly:** Meta and TikTok can demote "comment X" vote prompts as engagement bait.
  Tying each vote to a consequence the audience can see is the mitigation, not a guarantee. Watch reach
  against the account average after Episode 1.

### 2.6 Honesty rule for branches
The vote decides **what Nyx does and what the viewer sees next**: her action, the master variant, the
opening consequence, sometimes a location. It does **not** decide who the keeper is or what the season's
answer will be. Both branches reach the next clue by different routes. The episodes never claim more
control than that, and the captions say "your vote shapes Episode n", not "decides the story".

### 2.7 Single-master camera grammar
- **One detailed master image per reel.** A new master only when the location or the time genuinely
  changes. A carry-forward opener may reuse the previous episode's master as its winning-branch variant.
- **Master size: 2160x3840 (9:16, 2x).** Output is 1080x1920, so a crop down to 50% width keeps full
  sharpness. Every clue must be readable at its planned crop. Generate at 9:16 and upscale only with a
  non-generative upscaler.
- **Crop windows** below are written `[x, y, w]`: the left and top position, and the width, as fractions of
  the master. The height is always w × 16/9 of the width, so every crop stays 9:16. Minimum w = 0.5.
- Moves within a crop window ≤ 6% (the voice bible's subtle-motion limit). Changing window is a **cut** or
  a slow ≤ 1.5 s glide, never a whip, spin or shake.
- **Gap to close before production:** the voice bible currently allows only a 3-6% zoom per scene, and
  `nyx_reels.py` renders whole slides, not crop windows. This format needs a founder-approved
  "investigation format" amendment and a crop-window render path. Neither is built.
- Branch variants: only the **winning** variant's master is generated, after the vote closes. Each episode
  lists both.

---

## 3. The episodes

### Episode 1 -- "The Third Envelope"

**Cold open (second person), chosen by the Three Stars result:**
- MOON won: "you're just in time, and you said open it, so I did."
- Too low or tied: "it was quiet in the comments, so this one was my choice: I opened it."
- WAIT won: "you told me to wait, so it's still sealed, and you're waiting with me."

**Stakes.** Someone has drawn the lock of a trunk inside her home, a trunk she has never opened.

**Clue.** A sheet of tracing paper. On it, in the same ink: the outline of an old lock plate with its keyhole,
the starburst at its centre. Across the room, in the dark, the trunk sits near the desk.

**Master E1** (floor at the low table, night). Nyx kneels at the low table holding the tracing paper up
against the lamp, so the inked lock outline glows through it. In the deep background, past her shoulder,
the trunk near the desk, partly under boxes, its lock a small dark shape.
- Variant OPEN (MOON or low): the third envelope lies torn open on the table.
- Variant SEALED (WAIT): the envelope is still sealed and is what she holds to the lamp. The drawing shows
  through the paper.

| Shot | Crop [x, y, w] | Reveal | She says |
|---|---|---|---|
| 1 | [0.00, 0.00, 1.00] slow push 1.04 | the room: low table, window, her | cold open |
| 2 | [0.30, 0.45, 0.50] | the envelope: three stars, the copied moon, opened or sealed | "three stars, and my moon beside them." |
| 3 | [0.25, 0.18, 0.55] push 1.05 | the lock outline glowing through the paper | "inside, someone drew a lock." |
| 4 | [0.50, 0.30, 0.50] | the trunk's lock, dark and far behind her | "it's the lock on my trunk." |
| 5 | [0.20, 0.10, 0.55] | her face | reaction |
| 6 | [0.00, 0.00, 1.00] dim to 40% | decision, then the vote card | decision line |

**Decision line.** "they drew the lock on my trunk. I either go and check it right now, or hide this and try
to sleep."

**Vote card:** `TRUNK` / `HIDE`, with `Comment TRUNK or HIDE`.

**Branches.**
- TRUNK: Episode 2 opens with her already kneeling at the trunk, the paper laid against the lock.
- HIDE: Episode 2 opens on the atlas on the floor with the paper's corner sticking out of it. "I hid it. I
  lasted an hour." Then she is at the trunk.

**Canon if low:** TRUNK, labelled as her choice.

**Caption.** "someone drew the lock on my trunk and sent it to me. Comment TRUNK or HIDE -- your vote
shapes Episode 2."

**Assets.**
- Master E1 (winning variant only)
- voiceover script (≤ 25 s)
- vote card (template, tokens TRUNK / HIDE)
- Instagram and TikTok captions
- cover frame (crop 3)
- Fanvue frames (3-4, 9:16)
- optional licensed room-tone or paper sound effect

**Fanvue extension.** "The other side of the paper": in the window's moonlight a second, fainter outline
shows on the back. Close frames only. She says it looks like something small and square, and she doesn't
know what. Never shown clearly, never explained.

**QA continuity.**
- Identity and wardrobe exactly as the reference.
- The third envelope matches Three Stars (three starbursts, copied moon) and is in the winning variant's
  state.
- The trunk is in the reference/approved position near the desk, partly under boxes, **closed**. The drawn
  lock outline matches the real trunk lock's shape in the approved trunk frames.
- Chart corner still on the window glass. Star anise and the first two envelopes on the low table.
- The music box closed on the windowsill if in frame.
- No legible text except the card. Crop 3 and crop 4 each readable at 1080 px wide.

---

### Episode 2 -- "Fresh Scratches"

**Cold open:**
- TRUNK: "you sent me straight here, so come closer and look at this lock."
- HIDE: "I hid it like you said, and I lasted an hour. come and look at this lock."
- Too low: "nobody stopped me, so I came straight here. look at this lock."

**Stakes.** Someone has already tried to open her trunk, recently, inside her home.

**Clue.** Bright, fresh scratches around the keyhole, against the old dark patina. Someone tried a key or a
tool. The tracing paper's outline matches the lock exactly.

**Master E2** (the trunk near the desk, night: a new location). The boxes have been pushed aside. Nyx kneels at
the trunk, the tracing paper held beside the lock plate, the charm in her other hand, its starburst
facing up.
- Variant HIDE adds the atlas on the floor, paper-corner shaped gap visible.

| Shot | Crop [x, y, w] | Reveal | She says |
|---|---|---|---|
| 1 | [0.00, 0.00, 1.00] | the trunk, the boxes moved, her kneeling | cold open |
| 2 | [0.35, 0.50, 0.50] push 1.05 | the paper beside the lock: same shape | "it's a perfect match." |
| 3 | [0.42, 0.56, 0.50] | the fresh bright scratches around the keyhole | "and these scratches are new." |
| 4 | [0.10, 0.45, 0.50] | the charm in her hand | "the charm's been pointing here since the night I found it." |
| 5 | [0.20, 0.15, 0.55] | her face | reaction |
| 6 | [0.00, 0.00, 1.00] dim | decision, then the card | decision line |

**Decision line.** "someone's already tried this lock. I can try the charm in it right now, or find out who's
been in here first."

**Vote card:** `CHARM` / `WHO`, with `Comment CHARM or WHO`.

**Branches.**
- CHARM: Episode 3 opens on this master's CHARM variant. The charm sits in the lock, turned exactly one
  notch, stopped. "it turned once, and stopped." (Season map: the lock resists one notch short.) **The trunk
  stays closed.**
- WHO: Episode 3 opens directly at the windowsill, her searching.

**Canon if low:** WHO. It keeps the trunk untried for the season's later trunk beats. Labelled as her
choice.

**Caption.** "someone's been trying the lock on my trunk. the scratches are new. Comment CHARM or WHO --
your vote shapes Episode 3."

**Assets.**
- Master E2 (+ the CHARM-in-lock variant only if CHARM wins, used as Episode 3's opener)
- voiceover script
- vote card (CHARM / WHO)
- captions
- cover
- Fanvue frames

**Fanvue extension.** "Sitting with it": she sits back against the trunk, the cat in her lap, and admits
quietly what the scratches mean to her: "someone's been in my room while I slept." Intimate and
unguarded, non-explicit. The trunk stays shut.

**QA continuity.**
- The trunk, desk and boxes exactly as the reference and the approved `the_charm_001` frames. The boxes
  are moved aside, not gone.
- The trunk is **closed** in every frame. In the CHARM variant the charm is in the lock, turned slightly, the
  lid still down.
- The charm is the one charm (inked starburst); never two.
- The scratches read clearly at crop 3 and only around the keyhole.
- Night; reference lighting.

---

### Episode 3 -- "What They Took"

**Cold open:**
- CHARM: "you told me to try it, and it turned once and stopped. so I went looking for them instead."
- WHO: "you wanted to know who's been in here, so look where I found this."
- Too low: "it was my call, so I went looking instead. look where I found this."

**Stakes.** Someone has been inside her home and taken something from the music box.

**Clue.** She opens the music box on the windowsill. **The photograph is gone.** Her own folded note is still
there. In the photograph's place is **her dark-blue envelope with the silver moon**, the one taken from the
hallway. It came back from the inside.

**Master E3** (the windowsill, night: a new location). Nyx kneels at the window, the music box open on the
sill, her dark-blue envelope in her hand. Her folded note sits visibly inside the box, beside an empty
photograph-sized space. The chart corner rests on the glass above, the moon beyond.

| Shot | Crop [x, y, w] | Reveal | She says |
|---|---|---|---|
| 0 (CHARM only) | E2 CHARM variant [0.35, 0.50, 0.50] | the charm one notch in | carry-forward |
| 1 | [0.00, 0.00, 1.00] | the window, the box open, her | cold open |
| 2 | [0.30, 0.55, 0.50] push 1.05 | inside the box: her note, the empty space | "my note's still here. the photo isn't." |
| 3 | [0.15, 0.40, 0.50] | the dark-blue envelope, her moon | "and this is the envelope I sent them." |
| 4 | [0.20, 0.12, 0.55] | her face | reaction |
| 5 | [0.00, 0.00, 1.00] dim | decision, then the card | decision line |

**Decision line.** "they've been inside, more than once. tonight I either stay up and watch for them, or leave
the box out and ask for it back."

**Vote card:** `WATCH` / `ASK`, with `Comment WATCH or ASK`.

**Branches.**
- WATCH: Episode 4 opens with her awake on the floor near the door. Lamp off, one candle, the cat, a throw
  from the bed around her shoulders.
- ASK: Episode 4 opens on the open, empty music box set on the floor by the door, her silent request.

**Canon if low:** WATCH, labelled as her choice.

**Caption.** "they took the photo from my music box and left my own envelope in its place. Comment WATCH or
ASK -- your vote shapes Episode 4."

**Assets.**
- Master E3 (+ reuse of E2's CHARM variant as the opener if CHARM won)
- voiceover script
- vote card (WATCH / ASK)
- captions
- cover
- Fanvue frames
- optional licensed few-note music-box sting: **not** the canon tune unless the founder supplies it

**Fanvue extension.** "My own note": she unfolds the note she left in the box long ago and tells subscribers
what she wrote back then. Spoken, never shown legibly. Close and warm, slightly shaken.

**QA continuity.**
- The music box matches `music_box_001`: small worn wooden box, floral painted lid, brass wind-up key on the
  side. On the windowsill.
- Her note inside. **No photograph.**
- The dark-blue envelope matches Return Mail: plain dark blue, one small silver crescent moon, no text.
- Chart corner on the glass.
- The window exactly as the reference.
- The music box's wind-up key is **not** the black ribbon and key: never merge or swap them.

---

### Episode 4 -- "Four Stars"

**Cold open:**
- WATCH: "you kept me awake, so you're sitting by the door with me at three in the morning."
- ASK: "you said ask, so I left the box out for them. you're here when they answer."
- Too low: "I chose to stay up. you're sitting by the door with me."

**Stakes.** Whoever it is knows about a night she has lost from her own memory.

**Clue.** A fourth envelope slides under the door: **four starbursts**. Inside, a small card with **a row of
inked moon phases** and no words, a date written in moons. She knows the phases. The night they point to is
one she cannot remember.

**Master E4** (the floor by the door, deep night: a new time and location). Nyx kneels at the door. The
light strip is broken by one shadow, someone still outside. The four-star envelope lies on the boards.
The moon-phase card is in her hands.
- Variant WATCH: candle and throw behind her.
- Variant ASK: the open, empty music box beside the door.

| Shot | Crop [x, y, w] | Reveal | She says |
|---|---|---|---|
| 1 | [0.00, 0.00, 1.00] | the door, the strip, her | cold open |
| 2 | [0.30, 0.68, 0.50] | the shadow in the strip; the four-star envelope | "four stars, and they're still out there." |
| 3 | [0.25, 0.42, 0.50] push 1.06 | the row of moon phases on the card | "no words. it's a date, written in moons." |
| 4 | [0.20, 0.12, 0.55] | her face | "and it's a night I don't remember." |
| 5 | [0.00, 0.00, 1.00] dim | decision, then the card | decision line |

**Decision line.** "they're right outside. I can talk to them through the door, or stay quiet and let them
think I'm asleep."

**Vote card:** `TALK` / `QUIET`, with `Comment TALK or QUIET`.

**Branches.**
- TALK: Episode 5 opens with her close to the door. "I said, 'I know you're there.'" Then three soft knocks.
- QUIET: Episode 5 opens with her silent, the cat's ears up, then three soft knocks anyway.

**Canon if low:** QUIET, labelled as her choice.

**Caption.** "a fourth envelope. inside, a date written in moons, for a night I can't remember. Comment TALK
or QUIET -- your vote shapes Episode 5."

**Assets.**
- Master E4 (winning variant)
- voiceover script
- vote card (TALK / QUIET)
- captions
- cover
- Fanvue frames
- optional licensed low room tone

**Fanvue extension.** "The night I can't remember": she counts back to the date and says the one thing she
does remember from around then, the smell of star anise. That ties to Two Stars and resolves nothing.

**QA continuity.**
- The door exactly as the reference and approved s1e02 frames. **No invented hardware.**
- The four-star envelope is the same kind as the others, with four starbursts.
- The moon-phase card carries only drawn phases: no numbers, letters or words.
- The shadow is only in the light strip; no figure is seen.
- The variant matches the Episode 3 result.
- The cat as the reference.

---

### Episode 5 -- "Three Knocks"

**Cold open:**
- TALK: "you told me to talk, so I did, and they knocked back."
- QUIET: "you kept me quiet, and they knocked anyway."
- Too low: "I stayed quiet. it was my choice. they knocked anyway."

**Stakes.** The person who has been in her home is on the other side of the door, and has just given back
the photograph.

**Clue.** After three knocks, the missing photograph slides under the door **face-down**. On its back, inked
together: the starburst and her moon. She turns it over and sees the front; the camera never does.

**Master E5** (at the door, the same night, moments later; the E4 location with a new moment). Nyx sits on
the floor at the door, holding the photograph so only its back faces the camera. Two shadows break the
light strip: they haven't left.

| Shot | Crop [x, y, w] | Reveal | She says |
|---|---|---|---|
| 1 | [0.00, 0.00, 1.00] | the door, her | cold open |
| 2 | [0.30, 0.68, 0.50] | two shadows in the strip | "they're still standing there." |
| 3 | [0.28, 0.45, 0.50] push 1.06 | the photo's back: the starburst and her moon | "it's the photo from my music box." |
| 4 | [0.20, 0.12, 0.55] | her face, shaken, steady | **proposed:** "I finally know who's in it. it's me." |
| 5 | [0.00, 0.00, 1.00] dim | decision, then the card | decision line |

**Decision line.** "they're still on the other side. I open the door, or I keep it shut and let them go."

**Vote card:** `OPEN` / `SHUT`, with `Comment OPEN or SHUT`.

**Branches** (consequence lands in Episode 6, the next arc's opener; logged as a carried obligation):
- OPEN: the door opens on an empty hallway. "by the time it was open, they were gone." On the threshold, a
  fifth envelope with five stars. **This needs a founder-approved hallway reference first:** a new
  location, never invented.
- SHUT: the shadows leave, and a fifth envelope with five stars slides under.

The vote changes whether she has seen the hallway and whether they know she'd open it. The count continues
either way.

**Canon if low:** SHUT, labelled as her choice. This avoids generating an unapproved location.

**Caption.** "three knocks. then the photo from my music box came back under the door. Comment OPEN or SHUT
-- your vote shapes Episode 6."

**Assets.**
- Master E5
- voiceover script
- vote card (OPEN / SHUT)
- captions
- cover
- Fanvue frames
- licensed knock sound effect (three soft knocks)
- for Episode 6 OPEN only: a founder-approved hallway reference before any generation

**Fanvue extension.** "After the knocks": the minutes after, her hands not quite steady, the cat pressed
against her. She looks at the photo again; the camera stays on her face. "you'll see it when I'm ready.
not yet."

**QA continuity.**
- The photograph is the music-box photograph: small and worn, face-down toward camera in every frame.
  **Its front is never shown.**
- The back shows only the starburst and the crescent moon, no writing.
- Two shadows in the strip, no figure.
- Door and identity as the reference.
- **Founder decision:** "it's me" defines the photograph's content, which canon has never stated. If it isn't
  approved, use the fallback line "I know who's in it. I'm not ready to say." and keep everything else the
  same.

---

## 4. Vote log (fill in as each vote closes; empty now)

| Vote | Tokens | Posted (Melbourne) | Closed | Valid votes A / B | Low or tie? | Branch played | Evidence (screenshots) | Logged by |
|---|---|---|---|---|---|---|---|---|
| Three Stars | MOON / wait | | | | | | | |
| E1 | TRUNK / HIDE | | | | | | | |
| E2 | CHARM / WHO | | | | | | | |
| E3 | WATCH / ASK | | | | | | | |
| E4 | TALK / QUIET | | | | | | | |
| E5 | OPEN / SHUT | | | | | | | (carried to Episode 6) |

## 5. Season-wide QA checklist (before each episode's master is generated, and again before release)

- [ ] **The previous vote is logged with evidence.** The master generated is the winning (or canon-if-low)
      variant, and the cold open says which honestly.
- [ ] Identity, dark fur, wardrobe, cat, window, low table, ribbon and key, door and architecture match the
      reference package. Any conflict is rejected, never "close enough".
- [ ] Prop state matches section 1's table as updated by every earlier episode:
  - the trunk never opened;
  - the charm unique;
  - the photograph gone from E3 until it returns in E5, front never shown;
  - the envelope count 1-2-3-4 (5 only in Episode 6).
- [ ] One master per reel. A second master only for a genuine location or time change, or the
      carry-forward opener.
- [ ] Every clue readable at its crop at 1080 px wide. Each object is on screen when she names it. The
      subtitles match the voiceover word for word.
- [ ] The final beat order is exact:
  - her spoken decision line;
  - then the card with **exactly two choices** and `Comment A or B`;
  - nothing else on the card.
- [ ] The caption ends with `Comment A or B -- your vote shapes Episode n.`, with the same tokens as the card.
- [ ] No legible text in the art. Night only. Non-explicit. No "comment" in her voiceover. Platform AI label
      on (Instagram/TikTok); `#AIgenerated` on Fanvue.
- [ ] The community-recognition slot is empty unless every box in 2.4 is ticked.
- [ ] Nothing in the episode claims the vote controls more than section 2.6 allows.
- [ ] Nothing is scheduled or published from this brief until the founder approves the season and the
      format gaps in 2.7 are closed.
