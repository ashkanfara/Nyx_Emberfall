# The Shadow Under the Door (deliveries_p3)

PRODUCTION DRAFT, not canon. Plan and text only: no image, audio, upload, schedule or publish.

**Hook (presence):** the light under her door breaks into two shadows in the middle of the night  
**Open loop:** someone came to the door and left nothing; why come

Past midnight the cat sits up first. The light under the door breaks into two shadows. She freezes on the floor, hand on the cat, and they are gone. Nothing came under. She sits with her back to the door.

## Visual direction

The approved Nyx reference package is the authority for how everything looks: Nyx, her dark fur, wardrobe, the black cat, the moonlit window, the low-table and floor staging, the black ribbon and key, the door and all architecture. The directions below say only what happens and where the camera is. Nothing here specifies door hardware, locks, room geometry or furniture; if a frame conflicts with the reference, QA rejects it.

## Shot list (4 true 9:16 masters, 1080x1920)

| Master | Beat | Nyx | Camera | What happens | Story props on screen | Must match the reference | Subject rows |
|---|---|---|---|---|---|---|---|
| M1 | ESTABLISH | both fox ears, one turned to the right; tail on the floor | medium-wide, straight-on across the low table, seated floor-level eye height | sitting on the floor at the low table late at night, the black cat bolt upright beside her staring off-frame to the right | - | low table; moonlit window; black cat; black ribbon and key wherever the reference places them | 420-1560 |
| M2 | OBJECT | absent | medium close-up, low, straight-on toward the closed door, floor height | the bottom of the closed door at night, the warm strip of hallway light beneath it broken by two dark shadows, as if someone stands outside | hallway_light: broken by two shadows | the door exactly as the reference and the approved s1e02 frames | 900-1560 |
| M3 | REACTION | both fox ears, flattened slightly; tail low behind her | medium close-up, low three-quarter, from the room side, kneeling height | kneeling on the floor a few steps from the door, frozen, one hand on the black cat, eyes on the bottom of the door; the strip of light is whole again | hallway_light: a steady unbroken warm strip | the door exactly as the reference (soft background); black cat | 420-1560 |
| M4 | CONSEQUENCE | both fox ears; tail across the floor | medium-wide, from across the room toward the door, seated floor height | sitting on the floor with her back against the closed door, the black cat in her lap, the unbroken light strip and the empty floor beside her | hallway_light: a steady unbroken warm strip | the door exactly as the reference; moonlit window; black cat; floor staging | 420-1560 |

Frame prompts and per-frame continuity checks: [`../PROMPT_PACK.md`](../PROMPT_PACK.md).

## 1. Instagram carousel (4 frames, 4:5 vertical crops of the masters)

Each frame is the master's full 1080 px width, rows `crop_top` to `crop_top + 1350`. No scaling, no horizontal crop.

| # | Role | Master | Crop rows | Overlay |
|---|---|---|---|---|
| 1 | HOOK | M1 | 285-1635 | the cat heard it first. |
| 2 | ESCALATION | M2 | 470-1820 | (wordless) |
| 3 | PROGRESSION | M3 | 285-1635 | (wordless) |
| 4 | PAYOFF | M4 | 285-1635 | they left nothing. so why come? |

## 2. Reel outline (22.5 s, 1080x1920)

One sentence per visual beat: the room, the object on screen exactly when it is named, her reaction, then the consequence.

| Time | Beat | Master | Motion | She says |
|---|---|---|---|---|
| 0.0-6.2 s | ESTABLISH | M1 | push_in 1.04 | it was past midnight when the cat sat up, before I'd heard anything. |
| 6.2-11.1 s | OBJECT | M2 | push_in 1.06 | then the light under my door broke into two shadows. |
| 11.1-16.4 s | REACTION | M3 | push_in 1.05 | I held my breath, and just like that they were gone. |
| 16.4-22.5 s | CONSEQUENCE | M4 | push_out 1.04 | nothing came under the door this time, so why come at all? |

46 words at 138.7 wpm. Captions: `reel/voiceover_script.json`.

## 3. Story sequence (3 frames, full 9:16 masters)

| # | Master | Sticker line | Sticker rows |
|---|---|---|---|
| 1 | M1 | past midnight. | upper, y 260-360 |
| 2 | M2 | someone was out there. | upper, y 260-360 |
| 3 | M4 | nothing this time. | upper, y 260-360 |

Each sticker sits clear of her face and of the Story UI. No polls or question stickers. Apply Instagram's AI label.

## 4. Caption and voiceover

**Caption (Instagram, platform AI label on):**

> the light under my door broke into two shadows tonight. I held my breath and they were gone. nothing came under. the cat hasn't looked away since.

**Voiceover (first person, Nyx):**

1. it was past midnight when the cat sat up, before I'd heard anything.
2. then the light under my door broke into two shadows.
3. I held my breath, and just like that they were gone.
4. nothing came under the door this time, so why come at all?

## Story-prop ledger

| Prop | Start | End |
|---|---|---|
| hallway_light | a steady unbroken warm strip | a steady unbroken warm strip |

Reference items (cat, window, low table, ribbon and key, door, architecture) are not tracked here: they are whatever the reference package shows, every frame.
