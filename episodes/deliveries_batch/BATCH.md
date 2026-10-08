# The Deliveries -- five story-led post packages after s1e03 "The Second Delivery"

DRAFT -- plan and text assets only. No image, audio, upload, schedule or publish. Nothing here is canon until the founder approves it.

Regenerate everything below with `python3 nyx_post_batch.py build --write` (it refuses to write unless `python3 nyx_post_batch.py check` passes).

## Founder decisions needed before anything is generated

1. Approve s1e03 "The Second Delivery" as written below. It assumes the s1e02 vote resolved to the default 'open it' (season map: tie or no meaningful response -> open it). If the real vote said 'check the hallway first', s1e03 and package 1 must be re-cut.
2. Approve the canonical front-door hardware below. No earlier lock specifies it; from here on it is fixed.
3. Approve the spatial layout below (window on the far wall, shelf and star chart left of it, front door on the right-hand wall, lamp and candles beside the bed).
4. Re-slot the season map: s1e03's old slot ('The Trunk Has a Shape-Sized Space') is displaced by 'The Second Delivery', and these five packages sit before the trunk episodes. This batch does not edit brand/season_arcs/season_1.json.
5. Confirm item_index values (placeholders 35-39) against state/venture.json before any lock is promoted.

## Anchor: s1e03 "The Second Delivery" (DRAFT CANON -- written 2026-10-08 on founder instruction ('Define it as canon s1e03'); awaits founder approval)

Assumption: The s1e02 public fork resolved to the default 'open it'.

1. Same night loft, straight after s1e02. She opens the first envelope at the shelf.
2. Inside: one torn corner of an old printed star chart, a single small cluster circled in ink. No writing, no name.
3. She opens the star atlas: the same cluster is circled on its page, in what looks like the same ink.
4. She tucks the chart corner inside the atlas at that page and closes it. The charm and the opened first envelope stay on the shelf beside it.
5. A soft scrape at the door. A second envelope has come under it, into the warm hallway strip. This one has two inked starbursts.
6. She picks it up and does not open the door. She sets it, unopened, on the shelf with the others.

Public cliffhanger: "two stars this time." It does not resolve: who is delivering, what the starburst means, anything about the trunk.

## The mini-season

| # | Package | Hook type | Hook | Open loop | Reel |
|---|---|---|---|---|---|
| 1 | [Two Stars](deliveries_p1/PACKAGE.md) | object reveal | the second envelope gets opened, and what is inside is a smell | she knows the smell and cannot place where from | 23.3 s |
| 2 | [Above My Window](deliveries_p2/PACKAGE.md) | pattern match | the circled stars on the chart corner are in her own sky, right above her window | whoever sent it knows which window is hers | 24.4 s |
| 3 | [The Shadow Under the Door](deliveries_p3/PACKAGE.md) | presence | a shadow breaks the light under her door in the middle of the night | someone came to the door and left nothing; why come | 23.7 s |
| 4 | [Return Mail](deliveries_p4/PACKAGE.md) | her move | she stops waiting and writes back | she has sent something out; now she has to wait for an answer | 22.8 s |
| 5 | [Three Stars](deliveries_p5/PACKAGE.md) | an answer | her envelope is gone, and the reply slides under the door while she is kneeling there | the third envelope is sealed and she has not opened it | 23.7 s |

Arc: an object (smell) -> a pattern (her own sky) -> a presence (the shadow) -> her move (she writes back) -> an answer (they copied her moon). Nothing resolves: no sender, no meaning of the starburst, the third envelope stays sealed, the trunk stays out of frame.

## Canon this batch fixes (DRAFT until approved)

- **Front door hardware:** dark wood door; a warm strip of hallway light beneath it; ONE aged-brass round knob; ONE aged-brass deadbolt above the knob showing only a small oval thumb-turn on the inside; ONE small round brass peephole at eye height. Never: a lever handle, a keypad or smart lock, a security chain, a mail slot, a second lock, a visible keyhole on the inside face, glass panels in the door. No mail slot is why every delivery comes under the door. The thumb-turn is why no key is ever tried from inside.
- **Layout:** From the bed: the tall arched window is on the far wall, the dark-wood bookshelf stands to the left of the window with the framed star chart hanging above it, the front door is on the right-hand wall, and the lamp and candle cluster sit on a low dark-wood surface beside the bed. The layout never changes.
- **Wardrobe:** dark/black long-sleeved top and dark jeans in every frame; pendant necklace only; NO earrings; hair down, loose, unchanged in every frame; feet kept out of frame; no shoe or sock appears in any shot of this batch.
- **Trunk:** The trunk exists in the loft (the_charm_001) but stays out of frame for this whole batch; its beats belong to later season episodes.

## Prop ledger

| Prop | What it is | Enters | How |
|---|---|---|---|
| charm | the one small dark-metal charm with an inked starburst mark | already in canon | - |
| star_atlas | the one old star atlas, dark cloth cover | already in canon | - |
| envelope_1 | plain cream envelope, ONE inked starburst (the s1e02 envelope) | already in canon | - |
| chart_fragment | one torn corner of an old printed star chart, about palm-sized, a single small cluster circled in ink, no writing | already in canon | - |
| envelope_2 | plain cream envelope, the same as the first, with TWO inked starbursts side by side | already in canon | - |
| star_anise | one dried star anise pod, eight-pointed, warm brown | deliveries_p1 | it is inside the second envelope |
| reply_envelope | plain dark-blue envelope, hers, with one small crescent moon drawn in silver ink, no writing | deliveries_p4 | her own stationery; she says so |
| silver_pen | one fine pen with silver ink, hers | deliveries_p4 | she draws the moon with it on screen |
| envelope_3 | plain cream envelope like the first two, THREE inked starbursts in a row and, beside them, a small crescent moon in the same dark ink | deliveries_p5 | it slides in under the door while she kneels there |
| hallway_light | the warm strip of hallway light under the front door | already in canon | - |
| cat | the solid black cat | already in canon | - |

## Checks the plan passed before any asset was built

- 5 packages; distinct ids, titles, hook types, hooks and open loops
- the anchor episode's end state is the batch's start state
- the prop ledger replays master by master across all five packages: every prop on screen is in the state the ledger says, new props enter only where the story explains them
- every master: full camera, no canon-drift words, face in frame means both fox ears in frame, door words mean the door is in frame; no camera repeated anywhere in the batch
- carousel: 4 frames, HOOK first and PAYOFF last, story order, at most 2 overlays, every crop a full-width 1080x1350 window of the 1080x1920 master that keeps the subject band inside
- story: 3 frames, subject clear of the Story UI, a sticker slot that clears her face
- reel: ESTABLISH first, OBJECT before REACTION before the final CONSEQUENCE; one sentence per master, masters in story order, all masters used; each named prop is on screen in that beat and a new prop never appears before it is named; reactions show her face; 18-25 s
- voice: nyx_reels' voice-bible rules (pace, sentence length, captions, banned phrases)
- audience text: no premature resolution, no props the batch never shows, no bait, no disclaimer in her mouth

After building, each master lock passes `story_continuity.plan_problems`, each script passes the voice rules again against its lock, and every master compiles through `compile_prompt`.
