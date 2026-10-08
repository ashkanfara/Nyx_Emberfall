# Prompt pack -- 20 true-vertical 9:16 masters

PRODUCTION DRAFT. Nothing has been generated. No upload, schedule, publish or spend.

**Use:** attach every image in the approved Nyx reference package; brand/nyx_identity/generation_refs/gen_ref_1_face_closeup.png; brand/nyx_identity/generation_refs/gen_ref_2_head_fox_ears.png; brand/nyx_identity/generation_refs/gen_ref_3_fox_ear_detail.png. Paste the shared block once, then one frame prompt per message, in order. Output a 9:16 master conformed to 1080x1920 (official API 1152x2048; ChatGPT 1024x1536 trimmed at the sides to 864 px, so keep the subject central). Save as `<package>/masters/M<N>.png`; the 4:5 frames are cut later with `python3 nyx_post_batch.py crop`.

## Shared block

```
The approved Nyx reference package is the visual authority for every frame: Nyx's face and body, her dark fur (fox ears and tail), wardrobe, the black cat, the moonlit window, the low-table and floor staging, the black ribbon and key, the door and every piece of architecture. Text in this plan says WHAT HAPPENS; the reference says WHAT IT LOOKS LIKE. On any conflict the reference wins and the frame is rejected.

Preserve exactly:
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
No legible letters, words, numbers, addresses, stamps or logos in any generated image. Overlay lines are added afterwards.

Story props (they look like this every time):
- the first envelope: the first envelope, exactly as in the approved s1e02 frames, one inked starburst, opened
- the chart corner: one torn, palm-sized corner of an old printed star chart, one small cluster circled in ink, no writing
- the two-star envelope: a second envelope of the same kind as the first, with two inked starbursts side by side
- the star anise: one dried star anise pod
- her dark-blue envelope: a plain dark-blue envelope of her own, one small crescent moon drawn on it in silver ink, nothing else
- the silver pen: one fine pen with silver ink, hers
- the three-star envelope: a third envelope of the same kind as theirs: three inked starbursts in a row and, beside them, a small crescent moon in the same dark ink
- the light strip under the door: the warm strip of hallway light under the door
```

## Two Stars (deliveries_p1)

**01. M1 -- establish**

```
9:16 vertical photo, night. Medium-wide, floor-level staging as in the reference: Nyx seated at the low table, the moonlit window behind her, the cat at her side, the opened first envelope and the chart corner on the table. Sitting on the floor at the low table, the unopened second envelope in her hands, two starbursts facing camera, the black cat beside her. Camera: medium-wide, three-quarter, from across the low table, seated floor-level eye height. Nyx as the reference (both fox ears; tail curled on the floor beside her). On screen: the two-star envelope (unopened, in her hands); the first envelope (opened, on the low table); the chart corner (on the low table beside the first envelope); the black cat (beside her). As the reference: moonlit window; low table; black cat; black ribbon and key wherever the reference places them. Subject within rows 420-1560 of 1920. No text.
```
- [ ] Nyx: face, amber eyes, left-cheek beauty mark and adult build match reference_primary; both fox ears; tail curled on the floor beside her; dark fur as the reference, never a human ear or earring; wardrobe as the reference wherever visible
- [ ] Reference match: moonlit window; low table; black cat; black ribbon and key wherever the reference places them -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: the two-star envelope unopened, in her hands; the first envelope opened, on the low table; the chart corner on the low table beside the first envelope (changes on screen here: the two-star envelope)
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 285-1635 and clear of Story sticker rows 260-360
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

**02. M2 -- object**

```
9:16 vertical photo, night. Macro close-up looking down: the star anise sharp in her palm, the opened two-star envelope soft on the table below. One dried star anise resting in her open palm above the low table, the torn-open envelope beside it. Camera: macro close-up, top-down onto her palm, just above the low table. Only part of Nyx in frame, as the reference (hands only; ears, tail and face out of frame). On screen: the star anise (in her open palm); the two-star envelope (opened, on the low table). As the reference: low table surface. Subject within rows 560-1380 of 1920. No text.
```
- [ ] Nyx: hands only; ears, tail and face out of frame; hands, skin tone and any visible wardrobe as the reference; any fur that enters the frame is hers and matches the reference
- [ ] Reference match: low table surface -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: the star anise in her open palm; the two-star envelope opened, on the low table (changes on screen here: the two-star envelope)
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 285-1635 and clear of Story sticker rows 260-360
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

**03. M3 -- reaction**

```
9:16 vertical photo, night. Close-up profile: her face and the star anise just below it, warm candle light on her cheek, cool moonlight from the window behind. Holding the star anise close under her nose, eyes half closed, trying to place a smell. Camera: close-up, profile, face height, seated. Nyx as the reference (both fox ears, tipped forward; tail out of frame). On screen: the star anise (held near her face). As the reference: moonlit window (soft, behind). Subject within rows 420-1300 of 1920. No text.
```
- [ ] Nyx: face, amber eyes, left-cheek beauty mark and adult build match reference_primary; both fox ears, tipped forward; tail out of frame; dark fur as the reference, never a human ear or earring; wardrobe as the reference wherever visible
- [ ] Reference match: moonlit window (soft, behind) -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: the star anise held near her face (changes on screen here: the star anise)
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 230-1580 and clear of Story sticker rows 260-360
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

**04. M4 -- consequence**

```
9:16 vertical photo, night. Medium close-up across the low table: star anise, both opened envelopes and the chart corner in a loose row, the cat's nose at the star anise, her hand withdrawing. Her hand setting the star anise down on the low table beside both opened envelopes and the chart corner, the black cat leaning in to sniff it. Camera: medium close-up, low, across the table top, table height. Only part of Nyx in frame, as the reference (hand only; ears, tail and face out of frame). On screen: the star anise (on the low table beside the opened envelopes); the first envelope (opened, on the low table); the two-star envelope (opened, on the low table); the chart corner (on the low table beside the first envelope); the black cat (sniffing the star anise). As the reference: low table; black cat; black ribbon and key wherever the reference places them. Subject within rows 480-1500 of 1920. No text.
```
- [ ] Nyx: hand only; ears, tail and face out of frame; hands, skin tone and any visible wardrobe as the reference; any fur that enters the frame is hers and matches the reference
- [ ] Reference match: low table; black cat; black ribbon and key wherever the reference places them -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: the star anise on the low table beside the opened envelopes; the first envelope opened, on the low table; the two-star envelope opened, on the low table; the chart corner on the low table beside the first envelope (changes on screen here: the star anise)
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 285-1635
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

## Above My Window (deliveries_p2)

**05. M1 -- establish**

```
9:16 vertical photo, night. Medium-wide, floor-level staging as in the reference: Nyx kneeling below the moonlit window, the chart corner in her hand, moonlight on her face, a candle warm at the frame's edge. Kneeling on the floor by the moonlit window, the chart corner in one hand, looking up at a very clear sky. Camera: medium-wide, three-quarter from inside the room, kneeling eye height. Nyx as the reference (both fox ears; tail visible on the floor). On screen: the chart corner (in her hand at the window). As the reference: moonlit window; floor staging. Subject within rows 400-1560 of 1920. No text.
```
- [ ] Nyx: face, amber eyes, left-cheek beauty mark and adult build match reference_primary; both fox ears; tail visible on the floor; dark fur as the reference, never a human ear or earring; wardrobe as the reference wherever visible
- [ ] Reference match: moonlit window; floor staging -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: the chart corner in her hand at the window (changes on screen here: the chart corner)
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 285-1635 and clear of Story sticker rows 260-360
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

**06. M2 -- object**

```
9:16 vertical photo, night. Close over-the-shoulder insert: the chart corner against the glass in the lower half, the matching real stars and the moon above it. Over her shoulder: the chart corner held flat to the glass, its circled cluster lining up with a real cluster of stars just above the moon. Camera: close insert, over the shoulder, looking up through the glass, kneeling eye height, tilted up. Only part of Nyx in frame, as the reference (one fox ear edge at frame left; tail out of frame). On screen: the chart corner (held flat against the window glass). As the reference: moonlit window: same shape and view as the reference. Subject within rows 400-1440 of 1920. No text.
```
- [ ] Nyx: one fox ear edge at frame left; tail out of frame; hands, skin tone and any visible wardrobe as the reference; any fur that enters the frame is hers and matches the reference
- [ ] Reference match: moonlit window: same shape and view as the reference -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: the chart corner held flat against the window glass (changes on screen here: the chart corner)
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 240-1590 and clear of Story sticker rows 260-360
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

**07. M3 -- reaction**

```
9:16 vertical photo, night. Close-up profile against the glass: cool moonlight on her face, a warm candle edge behind, ears upright. Her face in close profile at the window, eyebrows slightly raised, a small wry half-smile. Camera: close-up, profile against the window, face height, kneeling. Nyx as the reference (both fox ears, upright; tail out of frame). On screen: the chart corner (held flat against the window glass). As the reference: moonlit window. Subject within rows 420-1320 of 1920. No text.
```
- [ ] Nyx: face, amber eyes, left-cheek beauty mark and adult build match reference_primary; both fox ears, upright; tail out of frame; dark fur as the reference, never a human ear or earring; wardrobe as the reference wherever visible
- [ ] Reference match: moonlit window -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: the chart corner held flat against the window glass
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 230-1580
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

**08. M4 -- consequence**

```
9:16 vertical photo, night. Medium from beside the window: the chart corner small against the glass, the moon above it, Nyx sitting back and glancing over her shoulder, the black cat watching from the floor. The chart corner left resting against the window glass; she sits back on her heels and looks over her shoulder into the dark room. Camera: medium, from beside the window, looking along the glass, floor sitting height. Nyx as the reference (both fox ears; tail visible). On screen: the chart corner (resting against the window glass); the black cat (watching from the floor). As the reference: moonlit window; black cat; floor staging. Subject within rows 400-1560 of 1920. No text.
```
- [ ] Nyx: face, amber eyes, left-cheek beauty mark and adult build match reference_primary; both fox ears; tail visible; dark fur as the reference, never a human ear or earring; wardrobe as the reference wherever visible
- [ ] Reference match: moonlit window; black cat; floor staging -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: the chart corner resting against the window glass (changes on screen here: the chart corner)
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 285-1635 and clear of Story sticker rows 260-360
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

## The Shadow Under the Door (deliveries_p3)

**09. M1 -- establish**

```
9:16 vertical photo, night. Medium-wide, floor-level staging as in the reference: Nyx at the low table, candles burned low, moonlit window behind, the cat upright and alert. Sitting on the floor at the low table late at night, the black cat bolt upright beside her staring off-frame to the right. Camera: medium-wide, straight-on across the low table, seated floor-level eye height. Nyx as the reference (both fox ears, one turned to the right; tail on the floor). On screen: the black cat (sitting bolt upright, staring off-frame). As the reference: low table; moonlit window; black cat; black ribbon and key wherever the reference places them. Subject within rows 420-1560 of 1920. No text.
```
- [ ] Nyx: face, amber eyes, left-cheek beauty mark and adult build match reference_primary; both fox ears, one turned to the right; tail on the floor; dark fur as the reference, never a human ear or earring; wardrobe as the reference wherever visible
- [ ] Reference match: low table; moonlit window; black cat; black ribbon and key wherever the reference places them -- REJECT if anything is moved, redesigned, added or missing
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 285-1635 and clear of Story sticker rows 260-360
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

**10. M2 -- object**

```
9:16 vertical photo, night. Low, floor-level shot of the lower part of the closed door exactly as the reference shows it: the light strip across the frame, broken by two shadows. The bottom of the closed door at night, the warm strip of hallway light beneath it broken by two dark shadows, as if someone stands outside. Camera: medium close-up, low, straight-on toward the closed door, floor height. No person in frame: no Nyx, hands, ears, tail, silhouette, shadow or reflection. On screen: the light strip under the door (broken by two shadows). As the reference: the door exactly as the reference and the approved s1e02 frames. Subject within rows 900-1560 of 1920. No text.
```
- [ ] Nyx: absent -- no person, hands, ears, tail, silhouette, shadow or reflection
- [ ] Reference match: the door exactly as the reference and the approved s1e02 frames -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: the light strip under the door broken by two shadows (changes on screen here: the light strip under the door)
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 470-1820 and clear of Story sticker rows 260-360
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

**11. M3 -- reaction**

```
9:16 vertical photo, night. Medium close-up, low: Nyx kneeling and holding still, the cat under her hand, the bottom of the door and its unbroken light strip soft in the background. Kneeling on the floor a few steps from the door, frozen, one hand on the black cat, eyes on the bottom of the door; the strip of light is whole again. Camera: medium close-up, low three-quarter, from the room side, kneeling height. Nyx as the reference (both fox ears, flattened slightly; tail low behind her). On screen: the light strip under the door (a steady unbroken warm strip); the black cat (under her hand). As the reference: the door exactly as the reference (soft background); black cat. Subject within rows 420-1560 of 1920. No text.
```
- [ ] Nyx: face, amber eyes, left-cheek beauty mark and adult build match reference_primary; both fox ears, flattened slightly; tail low behind her; dark fur as the reference, never a human ear or earring; wardrobe as the reference wherever visible
- [ ] Reference match: the door exactly as the reference (soft background); black cat -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: the light strip under the door a steady unbroken warm strip (changes on screen here: the light strip under the door)
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 285-1635
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

**12. M4 -- consequence**

```
9:16 vertical photo, night. Medium-wide from inside the room: Nyx seated against the door, the cat in her lap, empty floorboards and the unbroken light strip at her side, the moonlit window far across the room. Sitting on the floor with her back against the closed door, the black cat in her lap, the unbroken light strip and the empty floor beside her. Camera: medium-wide, from across the room toward the door, seated floor height. Nyx as the reference (both fox ears; tail across the floor). On screen: the light strip under the door (a steady unbroken warm strip); the black cat (in her lap). As the reference: the door exactly as the reference; moonlit window; black cat; floor staging. Subject within rows 420-1560 of 1920. No text.
```
- [ ] Nyx: face, amber eyes, left-cheek beauty mark and adult build match reference_primary; both fox ears; tail across the floor; dark fur as the reference, never a human ear or earring; wardrobe as the reference wherever visible
- [ ] Reference match: the door exactly as the reference; moonlit window; black cat; floor staging -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: the light strip under the door a steady unbroken warm strip
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 285-1635 and clear of Story sticker rows 260-360
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

## Return Mail (deliveries_p4)

**13. M1 -- establish**

```
9:16 vertical photo, night. Medium, floor-level staging as in the reference: Nyx at the low table with her own blank envelope, their opened envelopes and the star anise on the table, the moonlit window behind. Sitting on the floor at the low table, decided, a blank dark-blue envelope in her hands beside their two opened envelopes. Camera: medium, straight-on from across the low table, slightly above, seated eye height, slightly above. Nyx as the reference (both fox ears; tail around her on the floor). On screen: her dark-blue envelope (blank, in her hands); the first envelope (opened, on the low table); the two-star envelope (opened, on the low table); the star anise (on the low table beside the opened envelopes). As the reference: low table; moonlit window; black ribbon and key wherever the reference places them. Subject within rows 420-1560 of 1920. No text.
```
- [ ] Nyx: face, amber eyes, left-cheek beauty mark and adult build match reference_primary; both fox ears; tail around her on the floor; dark fur as the reference, never a human ear or earring; wardrobe as the reference wherever visible
- [ ] Reference match: low table; moonlit window; black ribbon and key wherever the reference places them -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: her dark-blue envelope blank, in her hands; the first envelope opened, on the low table; the two-star envelope opened, on the low table; the star anise on the low table beside the opened envelopes
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 285-1635 and clear of Story sticker rows 260-360
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

**14. M2 -- object**

```
9:16 vertical photo, night. Macro close-up: the pen tip finishing a small silver crescent moon on dark-blue paper, nothing else on the paper. Drawing one small crescent moon on the dark-blue envelope in silver ink, the envelope flat on the low table. Camera: macro close-up, top-down onto the envelope, low table height. Only part of Nyx in frame, as the reference (hands only; ears, tail and face out of frame). On screen: her dark-blue envelope (a silver crescent moon drawn on it); the silver pen (in her hand). As the reference: low table surface. Subject within rows 560-1360 of 1920. No text.
```
- [ ] Nyx: hands only; ears, tail and face out of frame; hands, skin tone and any visible wardrobe as the reference; any fur that enters the frame is hers and matches the reference
- [ ] Reference match: low table surface -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: her dark-blue envelope a silver crescent moon drawn on it; the silver pen in her hand (changes on screen here: her dark-blue envelope)
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 285-1635 and clear of Story sticker rows 260-360
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

**15. M3 -- reaction**

```
9:16 vertical photo, night. Medium close-up: her face, her dark-blue envelope held level with their two opened envelopes on the low table, warm candle light against cool moonlight. Holding her moon envelope up beside their two opened ones on the table, a small private smile. Camera: medium close-up, three-quarter from the window side, seated, slightly below her eyes. Nyx as the reference (both fox ears; tail out of frame). On screen: her dark-blue envelope (a silver crescent moon drawn on it); the first envelope (opened, on the low table); the two-star envelope (opened, on the low table); the silver pen (on the low table). As the reference: low table; moonlit window (soft, behind). Subject within rows 420-1500 of 1920. No text.
```
- [ ] Nyx: face, amber eyes, left-cheek beauty mark and adult build match reference_primary; both fox ears; tail out of frame; dark fur as the reference, never a human ear or earring; wardrobe as the reference wherever visible
- [ ] Reference match: low table; moonlit window (soft, behind) -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: her dark-blue envelope a silver crescent moon drawn on it; the first envelope opened, on the low table; the two-star envelope opened, on the low table; the silver pen on the low table (changes on screen here: the silver pen)
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 285-1635
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

**16. M4 -- consequence**

```
9:16 vertical photo, night. Floor-level close-up at the bottom of the door exactly as the reference shows it: the envelope half under the door, moon mark up, lit by the warm strip, her fingertips releasing it. Her fingertips sliding the dark-blue envelope out under the door into the warm strip of hallway light. Camera: close-up, floor-level, side-on to the door, floor height. Only part of Nyx in frame, as the reference (fingertips only; ears, tail and face out of frame). On screen: her dark-blue envelope (pushed out under the door); the light strip under the door (a steady unbroken warm strip). As the reference: the bottom of the door exactly as the reference. Subject within rows 900-1560 of 1920. No text.
```
- [ ] Nyx: fingertips only; ears, tail and face out of frame; hands, skin tone and any visible wardrobe as the reference; any fur that enters the frame is hers and matches the reference
- [ ] Reference match: the bottom of the door exactly as the reference -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: her dark-blue envelope pushed out under the door; the light strip under the door a steady unbroken warm strip (changes on screen here: her dark-blue envelope)
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 470-1820 and clear of Story sticker rows 260-360
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

## Three Stars (deliveries_p5)

**17. M1 -- establish**

```
9:16 vertical photo, night. Medium, floor-level: Nyx kneeling at the door with her palm on the bare boards, the unbroken light strip under the door, the cat at her side, the room dark behind. Kneeling on the floor at the bottom of the door, one palm flat on the empty floorboards where her envelope was, the black cat beside her. Camera: medium, three-quarter from the room side, kneeling height. Nyx as the reference (both fox ears, turned toward the door; tail on the floor). On screen: the light strip under the door (a steady unbroken warm strip); the black cat (beside her); NOT her dark-blue envelope: the floor where it lay is empty. As the reference: the door exactly as the reference; black cat; floor staging. Subject within rows 420-1560 of 1920. No text.
```
- [ ] Nyx: face, amber eyes, left-cheek beauty mark and adult build match reference_primary; both fox ears, turned toward the door; tail on the floor; dark fur as the reference, never a human ear or earring; wardrobe as the reference wherever visible
- [ ] Reference match: the door exactly as the reference; black cat; floor staging -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: the light strip under the door a steady unbroken warm strip; her dark-blue envelope gone
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 285-1635 and clear of Story sticker rows 260-360
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

**18. M2 -- object**

```
9:16 vertical photo, night. Low three-quarter close shot of the bottom of the door exactly as the reference shows it: the envelope half under the door, three starbursts readable as marks, one shadow in the strip. An envelope sliding in under the door through the warm strip, one shadow breaking the light behind it, three inked starbursts on the part already inside; its far end still under the door. Camera: close, low three-quarter at the bottom of the door, just above the floor. No person in frame: no Nyx, hands, ears, tail, silhouette, shadow or reflection. On screen: the three-star envelope (unopened, sliding in under the door); the light strip under the door (broken by one shadow). As the reference: the bottom of the door exactly as the reference. Subject within rows 900-1560 of 1920. No text.
```
- [ ] Nyx: absent -- no person, hands, ears, tail, silhouette, shadow or reflection
- [ ] Reference match: the bottom of the door exactly as the reference -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: the three-star envelope unopened, sliding in under the door; the light strip under the door broken by one shadow (changes on screen here: the light strip under the door)
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 470-1820
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

**19. M3 -- reaction**

```
9:16 vertical photo, night. Medium close-up, low and straight-on: her face above the envelope, its three starbursts and copied moon sharp, the bottom of the door and its unbroken strip soft behind. Kneeling, holding the envelope up: three starbursts and, beside them, a small crescent moon in the same dark ink; her eyes wide, lips slightly parted, calm but caught off guard; the strip of light whole again. Camera: medium close-up, low, straight-on facing her, kneeling chest height. Nyx as the reference (both fox ears, upright; tail low behind her). On screen: the three-star envelope (unopened, in her hands); the light strip under the door (a steady unbroken warm strip). As the reference: the door exactly as the reference (soft background). Subject within rows 420-1500 of 1920. No text.
```
- [ ] Nyx: face, amber eyes, left-cheek beauty mark and adult build match reference_primary; both fox ears, upright; tail low behind her; dark fur as the reference, never a human ear or earring; wardrobe as the reference wherever visible
- [ ] Reference match: the door exactly as the reference (soft background) -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: the three-star envelope unopened, in her hands; the light strip under the door a steady unbroken warm strip (changes on screen here: the three-star envelope, the light strip under the door)
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 285-1635 and clear of Story sticker rows 260-360
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text

**20. M4 -- consequence**

```
9:16 vertical photo, night. Medium close-up across the low table from a higher angle than before: three envelopes in a row, two opened and one sealed, the star anise beside them, her hand leaving the sealed one. Her hand placing the sealed third envelope on the low table beside the two opened ones and the star anise. Camera: medium close-up, high three-quarter onto the table top, standing height, looking down. Only part of Nyx in frame, as the reference (hand only; ears, tail and face out of frame). On screen: the three-star envelope (unopened, on the low table); the first envelope (opened, on the low table); the two-star envelope (opened, on the low table); the star anise (on the low table beside the opened envelopes). As the reference: low table; black ribbon and key wherever the reference places them. Subject within rows 420-1500 of 1920. No text.
```
- [ ] Nyx: hand only; ears, tail and face out of frame; hands, skin tone and any visible wardrobe as the reference; any fur that enters the frame is hers and matches the reference
- [ ] Reference match: low table; black ribbon and key wherever the reference places them -- REJECT if anything is moved, redesigned, added or missing
- [ ] Story props: the three-star envelope unopened, on the low table; the first envelope opened, on the low table; the two-star envelope opened, on the low table; the star anise on the low table beside the opened envelopes (changes on screen here: the three-star envelope)
- [ ] Framing: true 9:16 master; subject whole in 4:5 crop rows 285-1635 and clear of Story sticker rows 260-360
- [ ] REJECT: any door, lock, hardware, window, furniture or room geometry unlike the reference; a second animal; daylight; legible text
