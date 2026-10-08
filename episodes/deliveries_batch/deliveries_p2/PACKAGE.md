# Above My Window (deliveries_p2)

PRODUCTION DRAFT, not canon. Plan and text only: no image, audio, upload, schedule or publish.

**Hook (pattern match):** the circled stars on the chart corner are in her own sky, right above her window  
**Open loop:** whoever sent it knows which window is hers

A clear night. She takes the chart corner to the moonlit window and holds it to the glass. The circled cluster sits just above the moon, in her own sky. She leaves it at the window.

## Visual direction

The approved Nyx reference package is the authority for how everything looks: Nyx, her dark fur, wardrobe, the black cat, the moonlit window, the low-table and floor staging, the black ribbon and key, the door and all architecture. The directions below say only what happens and where the camera is. Nothing here specifies door hardware, locks, room geometry or furniture; if a frame conflicts with the reference, QA rejects it.

## Shot list (4 true 9:16 masters, 1080x1920)

| Master | Beat | Nyx | Camera | What happens | Story props on screen | Must match the reference | Subject rows |
|---|---|---|---|---|---|---|---|
| M1 | ESTABLISH | both fox ears; tail visible on the floor | medium-wide, three-quarter from inside the room, kneeling eye height | kneeling on the floor by the moonlit window, the chart corner in one hand, looking up at a very clear sky | chart_fragment: in her hand at the window | moonlit window; floor staging | 400-1560 |
| M2 | OBJECT | one fox ear edge at frame left; tail out of frame | close insert, over the shoulder, looking up through the glass, kneeling eye height, tilted up | over her shoulder: the chart corner held flat to the glass, its circled cluster lining up with a real cluster of stars just above the moon | chart_fragment: held flat against the window glass | moonlit window: same shape and view as the reference | 400-1440 |
| M3 | REACTION | both fox ears, upright; tail out of frame | close-up, profile against the window, face height, kneeling | her face in close profile at the window, eyebrows slightly raised, a small wry half-smile | chart_fragment: held flat against the window glass | moonlit window | 420-1320 |
| M4 | CONSEQUENCE | both fox ears; tail visible | medium, from beside the window, looking along the glass, floor sitting height | the chart corner left resting against the window glass; she sits back on her heels and looks over her shoulder into the dark room | chart_fragment: resting against the window glass | moonlit window; black cat; floor staging | 400-1560 |

Frame prompts and per-frame continuity checks: [`../PROMPT_PACK.md`](../PROMPT_PACK.md).

## 1. Instagram carousel (4 frames, 4:5 vertical crops of the masters)

Each frame is the master's full 1080 px width, rows `crop_top` to `crop_top + 1350`. No scaling, no horizontal crop.

| # | Role | Master | Crop rows | Overlay |
|---|---|---|---|---|
| 1 | HOOK | M1 | 285-1635 | it matches my sky. |
| 2 | ESCALATION | M2 | 240-1590 | (wordless) |
| 3 | PROGRESSION | M3 | 230-1580 | (wordless) |
| 4 | PAYOFF | M4 | 285-1635 | they know which window is mine. |

## 2. Reel outline (22.4 s, 1080x1920)

One sentence per visual beat: the room, the object on screen exactly when it is named, her reaction, then the consequence.

| Time | Beat | Master | Motion | She says |
|---|---|---|---|---|
| 0.0-6.7 s | ESTABLISH | M1 | push_in 1.04 | the sky is clear tonight, so I brought the chart corner to the window. |
| 6.7-11.4 s | OBJECT | M2 | push_in 1.06 | the circled stars are right there, just above the moon. |
| 11.4-15.4 s | REACTION | M3 | push_in 1.05 | okay, that's new, and a little too neat. |
| 15.4-22.4 s | CONSEQUENCE | M4 | push_out 1.04 | it stays at my window now, because they clearly know which one is mine. |

46 words at 139.4 wpm. Captions: `reel/voiceover_script.json`.

## 3. Story sequence (3 frames, full 9:16 masters)

| # | Master | Sticker line | Sticker rows |
|---|---|---|---|
| 1 | M1 | clear sky tonight. | upper, y 260-360 |
| 2 | M2 | the circled stars. | upper, y 260-360 |
| 3 | M4 | it stays here now. | upper, y 260-360 |

Each sticker sits clear of her face and of the Story UI. No polls or question stickers. Apply Instagram's AI label.

## 4. Caption and voiceover

**Caption (Instagram, platform AI label on):**

> held the chart corner up to my window. the circled stars are right there, just above the moon. that's not a coincidence anymore.

**Voiceover (first person, Nyx):**

1. the sky is clear tonight, so I brought the chart corner to the window.
2. the circled stars are right there, just above the moon.
3. okay, that's new, and a little too neat.
4. it stays at my window now, because they clearly know which one is mine.

## Story-prop ledger

| Prop | Start | End |
|---|---|---|
| chart_fragment | on the low table beside the first envelope | resting against the window glass |

Reference items (cat, window, low table, ribbon and key, door, architecture) are not tracked here: they are whatever the reference package shows, every frame.
