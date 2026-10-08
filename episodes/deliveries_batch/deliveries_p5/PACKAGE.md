# Three Stars (deliveries_p5)

PRODUCTION DRAFT, not canon. Plan and text only: no image, audio, upload, schedule or publish.

**Hook (an answer):** her envelope is gone, and the reply slides under the door while she is kneeling there  
**Open loop:** the third envelope is sealed and she has not opened it

The next night her envelope is gone from under the door. While she is still kneeling there, a third envelope slides under: three starbursts and, beside them, her crescent moon drawn in their ink. She opens neither the door nor the envelope. It goes on the low table with the others, sealed.

## Visual direction

The approved Nyx reference package is the authority for how everything looks: Nyx, her dark fur, wardrobe, the black cat, the moonlit window, the low-table and floor staging, the black ribbon and key, the door and all architecture. The directions below say only what happens and where the camera is. Nothing here specifies door hardware, locks, room geometry or furniture; if a frame conflicts with the reference, QA rejects it.

## Shot list (4 true 9:16 masters, 1080x1920)

| Master | Beat | Nyx | Camera | What happens | Story props on screen | Must match the reference | Subject rows |
|---|---|---|---|---|---|---|---|
| M1 | ESTABLISH | both fox ears, turned toward the door; tail on the floor | medium, three-quarter from the room side, kneeling height | kneeling on the floor at the bottom of the door, one palm flat on the empty floorboards where her envelope was, the black cat beside her | hallway_light: a steady unbroken warm strip; MISSING: reply_envelope | the door exactly as the reference; black cat; floor staging | 420-1560 |
| M2 | OBJECT | absent | close, low three-quarter at the bottom of the door, just above the floor | an envelope sliding in under the door through the warm strip, one shadow breaking the light behind it, three inked starbursts on the part already inside; its far end still under the door | envelope_3: unopened, sliding in under the door; hallway_light: broken by one shadow | the bottom of the door exactly as the reference | 900-1560 |
| M3 | REACTION | both fox ears, upright; tail low behind her | medium close-up, low, straight-on facing her, kneeling chest height | kneeling, holding the envelope up: three starbursts and, beside them, a small crescent moon in the same dark ink; her eyes wide, lips slightly parted, calm but caught off guard; the strip of light whole again | envelope_3: unopened, in her hands; hallway_light: a steady unbroken warm strip | the door exactly as the reference (soft background) | 420-1500 |
| M4 | CONSEQUENCE | hand only; ears, tail and face out of frame | medium close-up, high three-quarter onto the table top, standing height, looking down | her hand placing the sealed third envelope on the low table beside the two opened ones and the star anise | envelope_3: unopened, on the low table; envelope_1: opened, on the low table; envelope_2: opened, on the low table; star_anise: on the low table beside the opened envelopes | low table; black ribbon and key wherever the reference places them | 420-1500 |

Frame prompts and per-frame continuity checks: [`../PROMPT_PACK.md`](../PROMPT_PACK.md).

## 1. Instagram carousel (4 frames, 4:5 vertical crops of the masters)

Each frame is the master's full 1080 px width, rows `crop_top` to `crop_top + 1350`. No scaling, no horizontal crop.

| # | Role | Master | Crop rows | Overlay |
|---|---|---|---|---|
| 1 | HOOK | M1 | 285-1635 | mine was gone. |
| 2 | ESCALATION | M2 | 470-1820 | (wordless) |
| 3 | PROGRESSION | M3 | 285-1635 | (wordless) |
| 4 | PAYOFF | M4 | 285-1635 | still sealed. for now. |

## 2. Reel outline (20.7 s, 1080x1920)

One sentence per visual beat: the room, the object on screen exactly when it is named, her reaction, then the consequence.

| Time | Beat | Master | Motion | She says |
|---|---|---|---|---|
| 0.0-6.2 s | ESTABLISH | M1 | push_in 1.04 | the next night, the envelope I'd sent was gone from under my door. |
| 6.2-11.1 s | OBJECT | M2 | push_in 1.06 | then another one slid under, with three stars on it. |
| 11.1-15.8 s | REACTION | M3 | push_in 1.05 | and right beside the stars, they'd drawn my little moon. |
| 15.8-20.7 s | CONSEQUENCE | M4 | push_out 1.04 | it's still sealed, here with the others, for now. |

42 words at 139.2 wpm. Captions: `reel/voiceover_script.json`.

## 3. Story sequence (3 frames, full 9:16 masters)

| # | Master | Sticker line | Sticker rows |
|---|---|---|---|
| 1 | M1 | mine's gone. | upper, y 260-360 |
| 2 | M3 | they drew my moon. | upper, y 260-360 |
| 3 | M4 | not opening it yet. | upper, y 260-360 |

Each sticker sits clear of her face and of the Story UI. No polls or question stickers. Apply Instagram's AI label.

## 4. Caption and voiceover

**Caption (Instagram, platform AI label on):**

> my envelope was gone. then, while I was still kneeling there, a third one slid under: three stars, and my little moon drawn right next to them. it's still sealed.

**Voiceover (first person, Nyx):**

1. the next night, the envelope I'd sent was gone from under my door.
2. then another one slid under, with three stars on it.
3. and right beside the stars, they'd drawn my little moon.
4. it's still sealed, here with the others, for now.

## Story-prop ledger

| Prop | Start | End |
|---|---|---|
| envelope_1 | opened, on the low table | opened, on the low table |
| envelope_2 | opened, on the low table | opened, on the low table |
| envelope_3 | (not yet in the story) | unopened, on the low table |
| hallway_light | a steady unbroken warm strip | a steady unbroken warm strip |
| reply_envelope | pushed out under the door | gone, taken from the hallway side |
| star_anise | on the low table beside the opened envelopes | on the low table beside the opened envelopes |

Reference items (cat, window, low table, ribbon and key, door, architecture) are not tracked here: they are whatever the reference package shows, every frame.
