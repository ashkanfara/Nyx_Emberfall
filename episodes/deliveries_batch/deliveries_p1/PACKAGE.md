# Two Stars (deliveries_p1)

PRODUCTION DRAFT, not canon. Plan and text only: no image, audio, upload, schedule or publish.

**Hook (object reveal):** the second envelope gets opened, and what is inside is a smell  
**Open loop:** she knows the smell and cannot place where from

The next night, on the floor at the low table, she opens the second envelope. Inside is one dried star anise. She knows the smell and can't place it. It stays on the low table with the others.

## Visual direction

The approved Nyx reference package is the authority for how everything looks: Nyx, her dark fur, wardrobe, the black cat, the moonlit window, the low-table and floor staging, the black ribbon and key, the door and all architecture. The directions below say only what happens and where the camera is. Nothing here specifies door hardware, locks, room geometry or furniture; if a frame conflicts with the reference, QA rejects it.

## Shot list (4 true 9:16 masters, 1080x1920)

| Master | Beat | Nyx | Camera | What happens | Story props on screen | Must match the reference | Subject rows |
|---|---|---|---|---|---|---|---|
| M1 | ESTABLISH | both fox ears; tail curled on the floor beside her | medium-wide, three-quarter, from across the low table, seated floor-level eye height | sitting on the floor at the low table, the unopened second envelope in her hands, two starbursts facing camera, the black cat beside her | envelope_2: unopened, in her hands; envelope_1: opened, on the low table; chart_fragment: on the low table beside the first envelope | moonlit window; low table; black cat; black ribbon and key wherever the reference places them | 420-1560 |
| M2 | OBJECT | hands only; ears, tail and face out of frame | macro close-up, top-down onto her palm, just above the low table | one dried star anise resting in her open palm above the low table, the torn-open envelope beside it | star_anise: in her open palm; envelope_2: opened, on the low table | low table surface | 560-1380 |
| M3 | REACTION | both fox ears, tipped forward; tail out of frame | close-up, profile, face height, seated | holding the star anise close under her nose, eyes half closed, trying to place a smell | star_anise: held near her face | moonlit window (soft, behind) | 420-1300 |
| M4 | CONSEQUENCE | hand only; ears, tail and face out of frame | medium close-up, low, across the table top, table height | her hand setting the star anise down on the low table beside both opened envelopes and the chart corner, the black cat leaning in to sniff it | star_anise: on the low table beside the opened envelopes; envelope_1: opened, on the low table; envelope_2: opened, on the low table; chart_fragment: on the low table beside the first envelope | low table; black cat; black ribbon and key wherever the reference places them | 480-1500 |

Frame prompts and per-frame continuity checks: [`../PROMPT_PACK.md`](../PROMPT_PACK.md).

## 1. Instagram carousel (4 frames, 4:5 vertical crops of the masters)

Each frame is the master's full 1080 px width, rows `crop_top` to `crop_top + 1350`. No scaling, no horizontal crop.

| # | Role | Master | Crop rows | Overlay |
|---|---|---|---|---|
| 1 | HOOK | M1 | 285-1635 | I opened the second one. |
| 2 | ESCALATION | M2 | 285-1635 | (wordless) |
| 3 | PROGRESSION | M3 | 230-1580 | (wordless) |
| 4 | PAYOFF | M4 | 285-1635 | I know this smell. I don't know why. |

## 2. Reel outline (21.3 s, 1080x1920)

One sentence per visual beat: the room, the object on screen exactly when it is named, her reaction, then the consequence.

| Time | Beat | Master | Motion | She says |
|---|---|---|---|---|
| 0.0-5.5 s | ESTABLISH | M1 | push_in 1.04 | it's late, and I'm finally opening the one with two stars. |
| 5.5-10.2 s | OBJECT | M2 | push_in 1.06 | inside, there's nothing at all but one dried star anise. |
| 10.2-15.6 s | REACTION | M3 | push_in 1.05 | I know this smell from somewhere, and I can't remember where. |
| 15.6-21.3 s | CONSEQUENCE | M4 | push_out 1.04 | so it stays here with the others, until I finally do. |

43 words at 138.0 wpm. Captions: `reel/voiceover_script.json`.

## 3. Story sequence (3 frames, full 9:16 masters)

| # | Master | Sticker line | Sticker rows |
|---|---|---|---|
| 1 | M1 | the second one. | upper, y 260-360 |
| 2 | M2 | this was inside. | upper, y 260-360 |
| 3 | M3 | I know this smell. | upper, y 260-360 |

Each sticker sits clear of her face and of the Story UI. No polls or question stickers. Apply Instagram's AI label.

## 4. Caption and voiceover

**Caption (Instagram, platform AI label on):**

> opened the second one. no note, no name. just one star anise, and a smell I know from somewhere I can't remember.

**Voiceover (first person, Nyx):**

1. it's late, and I'm finally opening the one with two stars.
2. inside, there's nothing at all but one dried star anise.
3. I know this smell from somewhere, and I can't remember where.
4. so it stays here with the others, until I finally do.

## Story-prop ledger

| Prop | Start | End |
|---|---|---|
| chart_fragment | on the low table beside the first envelope | on the low table beside the first envelope |
| envelope_1 | opened, on the low table | opened, on the low table |
| envelope_2 | unopened, on the low table | opened, on the low table |
| star_anise | (not yet in the story) | on the low table beside the opened envelopes |

Reference items (cat, window, low table, ribbon and key, door, architecture) are not tracked here: they are whatever the reference package shows, every frame.
