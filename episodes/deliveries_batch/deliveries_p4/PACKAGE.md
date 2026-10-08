# Return Mail (deliveries_p4)

PRODUCTION DRAFT, not canon. Plan and text only: no image, audio, upload, schedule or publish.

**Hook (her move):** she stops waiting and writes back  
**Open loop:** she has sent something out; now she has to wait for an answer

She decides to answer. On the floor at the low table: one dark-blue envelope of her own, no words, a small crescent moon in silver ink. She holds it beside theirs, then slides it out under the door.

## Visual direction

The approved Nyx reference package is the authority for how everything looks: Nyx, her dark fur, wardrobe, the black cat, the moonlit window, the low-table and floor staging, the black ribbon and key, the door and all architecture. The directions below say only what happens and where the camera is. Nothing here specifies door hardware, locks, room geometry or furniture; if a frame conflicts with the reference, QA rejects it.

## Shot list (4 true 9:16 masters, 1080x1920)

| Master | Beat | Nyx | Camera | What happens | Story props on screen | Must match the reference | Subject rows |
|---|---|---|---|---|---|---|---|
| M1 | ESTABLISH | both fox ears; tail around her on the floor | medium, straight-on from across the low table, slightly above, seated eye height, slightly above | sitting on the floor at the low table, decided, a blank dark-blue envelope in her hands beside their two opened envelopes | reply_envelope: blank, in her hands; envelope_1: opened, on the low table; envelope_2: opened, on the low table; star_anise: on the low table beside the opened envelopes | low table; moonlit window; black ribbon and key wherever the reference places them | 420-1560 |
| M2 | OBJECT | hands only; ears, tail and face out of frame | macro close-up, top-down onto the envelope, low table height | drawing one small crescent moon on the dark-blue envelope in silver ink, the envelope flat on the low table | reply_envelope: a silver crescent moon drawn on it; silver_pen: in her hand | low table surface | 560-1360 |
| M3 | REACTION | both fox ears; tail out of frame | medium close-up, three-quarter from the window side, seated, slightly below her eyes | holding her moon envelope up beside their two opened ones on the table, a small private smile | reply_envelope: a silver crescent moon drawn on it; envelope_1: opened, on the low table; envelope_2: opened, on the low table; silver_pen: on the low table | low table; moonlit window (soft, behind) | 420-1500 |
| M4 | CONSEQUENCE | fingertips only; ears, tail and face out of frame | close-up, floor-level, side-on to the door, floor height | her fingertips sliding the dark-blue envelope out under the door into the warm strip of hallway light | reply_envelope: pushed out under the door; hallway_light: a steady unbroken warm strip | the bottom of the door exactly as the reference | 900-1560 |

Frame prompts and per-frame continuity checks: [`../PROMPT_PACK.md`](../PROMPT_PACK.md).

## 1. Instagram carousel (4 frames, 4:5 vertical crops of the masters)

Each frame is the master's full 1080 px width, rows `crop_top` to `crop_top + 1350`. No scaling, no horizontal crop.

| # | Role | Master | Crop rows | Overlay |
|---|---|---|---|---|
| 1 | HOOK | M1 | 285-1635 | I'm writing back. |
| 2 | ESCALATION | M2 | 285-1635 | (wordless) |
| 3 | PROGRESSION | M3 | 285-1635 | (wordless) |
| 4 | PAYOFF | M4 | 470-1820 | my turn to wait. |

## 2. Reel outline (21.2 s, 1080x1920)

One sentence per visual beat: the room, the object on screen exactly when it is named, her reaction, then the consequence.

| Time | Beat | Master | Motion | She says |
|---|---|---|---|---|
| 0.0-5.5 s | ESTABLISH | M1 | push_in 1.04 | tonight I'm writing back, on one plain envelope of my own. |
| 5.5-10.2 s | OBJECT | M2 | push_in 1.06 | no words, just a little moon, drawn in silver ink. |
| 10.2-14.7 s | REACTION | M3 | push_in 1.05 | it looks small next to theirs, but it's mine. |
| 14.7-21.2 s | CONSEQUENCE | M4 | push_out 1.04 | I slid it under the door, and now it's my turn to wait. |

43 words at 138.7 wpm. Captions: `reel/voiceover_script.json`.

## 3. Story sequence (3 frames, full 9:16 masters)

| # | Master | Sticker line | Sticker rows |
|---|---|---|---|
| 1 | M1 | my turn. | upper, y 260-360 |
| 2 | M2 | no words. just this. | upper, y 260-360 |
| 3 | M4 | sent. | upper, y 260-360 |

Each sticker sits clear of her face and of the Story UI. No polls or question stickers. Apply Instagram's AI label.

## 4. Caption and voiceover

**Caption (Instagram, platform AI label on):**

> wrote back. no words, just a little silver moon on a dark-blue envelope. slid it out under the door the same way theirs came in. now I wait.

**Voiceover (first person, Nyx):**

1. tonight I'm writing back, on one plain envelope of my own.
2. no words, just a little moon, drawn in silver ink.
3. it looks small next to theirs, but it's mine.
4. I slid it under the door, and now it's my turn to wait.

## Story-prop ledger

| Prop | Start | End |
|---|---|---|
| envelope_1 | opened, on the low table | opened, on the low table |
| envelope_2 | opened, on the low table | opened, on the low table |
| hallway_light | a steady unbroken warm strip | a steady unbroken warm strip |
| reply_envelope | (not yet in the story) | pushed out under the door |
| silver_pen | (not yet in the story) | on the low table |
| star_anise | on the low table beside the opened envelopes | on the low table beside the opened envelopes |

Reference items (cat, window, low table, ribbon and key, door, architecture) are not tracked here: they are whatever the reference package shows, every frame.
