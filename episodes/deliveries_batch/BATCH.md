# The Deliveries -- five story-led post packages after "The Second Delivery"

PRODUCTION DRAFT -- not official canon. Plan, text and prompts only: no image, audio, upload, schedule, publish, spend or setting change. Does not alter brand/season_arcs/season_1.json, any content_items numbering, or any established room or lock fact.

Revised: 2026-10-08 -- visual direction re-based on the approved Nyx reference package; hard-coded door hardware, room layout, envelope colour and sleeve length removed; 4 masters per package (20 in total).

Rebuild with `python3 nyx_post_batch.py build --write` (it refuses to write unless `python3 nyx_post_batch.py check` passes).

## Open questions for the founder

1. The approved Nyx reference package is not in this repository. Every frame must be generated with it attached, and QA compares every frame against it. Add its images (or their paths) before the first sitting.
2. "The Second Delivery" is a production-draft anchor that assumes the s1e02 vote resolved to the default 'open it'. It is not in season_1.json and takes no episode number from it.
3. The black ribbon and key belong to the reference package. Their story meaning is not decided here: they appear unchanged wherever the reference places them and are never named in the voiceover.
4. No content_items index is assigned. Numbering happens only when a package is promoted.

## Anchor: The Second Delivery (production-draft anchor; not numbered, not in season_1.json)

Assumption: The s1e02 public fork resolved to the default 'open it'.

1. Same night, straight after s1e02. She opens the first envelope, sitting on the floor at the low table.
2. Inside: one torn corner of an old printed star chart, one small cluster circled in ink. No writing, no name.
3. She lays the chart corner on the low table beside the opened envelope.
4. A soft scrape at the door. A second envelope has come under it, into the warm strip of hallway light. This one has two inked starbursts.
5. She picks it up and does not open the door. She sets it, unopened, on the low table with the others.

Public cliffhanger: "two stars this time." It does not resolve: who is delivering, what the starburst means, what the ribbon and key are for, anything about the trunk.

## The mini-season

| # | Package | Hook type | Hook | Open loop | Reel |
|---|---|---|---|---|---|
| 1 | [Two Stars](deliveries_p1/PACKAGE.md) | object reveal | the second envelope gets opened, and what is inside is a smell | she knows the smell and cannot place where from | 21.3 s |
| 2 | [Above My Window](deliveries_p2/PACKAGE.md) | pattern match | the circled stars on the chart corner are in her own sky, right above her window | whoever sent it knows which window is hers | 22.4 s |
| 3 | [The Shadow Under the Door](deliveries_p3/PACKAGE.md) | presence | the light under her door breaks into two shadows in the middle of the night | someone came to the door and left nothing; why come | 22.5 s |
| 4 | [Return Mail](deliveries_p4/PACKAGE.md) | her move | she stops waiting and writes back | she has sent something out; now she has to wait for an answer | 21.2 s |
| 5 | [Three Stars](deliveries_p5/PACKAGE.md) | an answer | her envelope is gone, and the reply slides under the door while she is kneeling there | the third envelope is sealed and she has not opened it | 20.7 s |

Arc: an object (a smell) -> a pattern (her own sky) -> a presence (the shadows) -> her move (she writes back) -> an answer (they copied her moon). Nothing resolves: no sender, no meaning of the starburst, no meaning for the ribbon and key, the third envelope stays sealed, the trunk stays out of frame.

## Visual authority

The approved Nyx reference package is the visual authority for every frame: Nyx's face and body, her dark fur (fox ears and tail), wardrobe, the black cat, the moonlit window, the low-table and floor staging, the black ribbon and key, the door and every piece of architecture. Text in this plan says WHAT HAPPENS; the reference says WHAT IT LOOKS LIKE. On any conflict the reference wins and the frame is rejected.

- Nyx exactly as the reference: same face, amber eyes, dark hair, beauty mark on her anatomical left cheek, adult (25+)
- her dark fur: exactly two fox ears on top of her head and exactly one fox tail, fur colour and pattern as the reference; no human ears, no earrings
- wardrobe exactly as the reference: dark/black top, dark jeans, pendant necklace only
- the black cat, exactly as the reference; never a second animal
- the moonlit window exactly as the reference: same shape, same view, a visible moon
- the low table and the floor-level staging exactly as the reference
- the black ribbon and key exactly as the reference: same place, same look, never moved, duplicated, recoloured or removed
- the door, its hardware and the strip of hallway light beneath it exactly as the reference and the approved s1e02 frames
- every piece of architecture, furniture and decor as the reference shows it: nothing added, moved or redesigned
- night only: warm lamp and candle light against cool moonlight

Prompts never describe door hardware, locks, room geometry, furniture layout, wardrobe cut or fur colour in their own words. They point at the reference.

Prompts and per-frame checks for all 20 masters: [PROMPT_PACK.md](PROMPT_PACK.md).

## Story props (new to this draft)

| Prop | What it is | Enters | How |
|---|---|---|---|
| envelope_1 | the first envelope, exactly as in the approved s1e02 frames, one inked starburst, opened | anchor | - |
| chart_fragment | one torn, palm-sized corner of an old printed star chart, one small cluster circled in ink, no writing | anchor | - |
| envelope_2 | a second envelope of the same kind as the first, with two inked starbursts side by side | anchor | - |
| star_anise | one dried star anise pod | deliveries_p1 | it is inside the second envelope |
| reply_envelope | a plain dark-blue envelope of her own, one small crescent moon drawn on it in silver ink, nothing else | deliveries_p4 | her own stationery; she says so |
| silver_pen | one fine pen with silver ink, hers | deliveries_p4 | she draws the moon with it on screen |
| envelope_3 | a third envelope of the same kind as theirs: three inked starbursts in a row and, beside them, a small crescent moon in the same dark ink | deliveries_p5 | it slides in under the door while she kneels there |
| hallway_light | the warm strip of hallway light under the door | anchor | - |

## Checks the plan passed before any asset was built

- 5 packages, 4 masters each (20); distinct ids, titles, hook types, hooks and open loops
- the reference package is declared the visual authority; the plan declares no canon of its own
- the anchor's end state is the batch's start state; the story-prop ledger replays master by master across all five packages; new props enter only where the story explains them
- every master: full camera, names the reference items QA must match, and no invented door hardware, lock parts, sleeves, room furniture or drift words; face in frame means both fox ears in frame; no camera repeated anywhere in the batch
- carousel: the 4 masters in order, HOOK first and PAYOFF last, at most 2 overlays, each crop a full-width 1080x1350 window of the 1080x1920 master that keeps the subject whole
- story: 3 frames, subject clear of the Story UI, a sticker slot that clears her face
- reel: exactly ESTABLISH -> OBJECT -> REACTION -> CONSEQUENCE, one sentence per master; each named prop is on screen in that beat and a new prop never appears before it is named; the reaction shows her face; 18-25 s
- voice: nyx_reels' voice-bible rules; audience text: no premature resolution, no naming of the ribbon or key, no bait, no disclaimer in her mouth

After building, each master lock passes `story_continuity.plan_problems` (apart from the deliberately unassigned content number), each script passes the voice rules again, and every master compiles through the runner's `compile_prompt`.
