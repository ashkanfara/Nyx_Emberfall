# s1e02_public -- ChatGPT image batch `76a690eada` (6 images)

Zero cost: your own ChatGPT subscription, by hand. The runner uploads, posts and spends nothing.

## Before you start

1. Open ONE new ChatGPT conversation and send the messages below in order.
2. Every image: **portrait 2:3 (1024x1536)**. Keep everything that matters inside the central 4:5 area -- the runner centre-crops and scales to **1080x1350**.
3. Save each image into **`generated_assets/carousel_item33/drop/`** with the exact name given (png, jpg or webp).
4. Run `python3 nyx_runner.py tick` (or let a scheduled tick find them). QA, repairs, captions, variants and the release package then continue on their own.
5. If ChatGPT drifts on one image, paste that slide's full prompt from `prompts/slide<N>.txt` instead of the short message.

## Message 1 -- shared rules (no image)

```text
Context for a series of photos. Do not generate anything yet -- reply only "ready". These rules apply to every image I ask for in this conversation:

2. EXPLICIT STORY FACTS FOR THIS SLIDE
Required continuity: same night loft, wardrobe and hair as every other slide; the inked star mark on the envelope matches the charm's starburst; NO legible letters, words, address or stamp anywhere in the image

3. ENVIRONMENT MASTER (immutable for this story)
Every one of these exists in this room, in the same geometry, in every slide. A closer shot may show some of them only partly or softly in the background, or leave them out of frame -- out of frame is not removal. None may be contradicted, moved or redesigned, and the room is never staged as a showcase to fit them all in:
- an intimate, one-person city attic/loft, a sense of being up above the street; sloped or high ceiling line
- a tall, arched or dormer-style window, sheer or no curtain, showing a dense night skyline and a visible moon
- black/navy bedding carrying subtle celestial motifs (stars, moon), a small amount of soft fur/throw texture -- not piled or overdone
- one warm practical lamp plus a small cluster of candles nearby -- the amber half of the core lighting contrast
- dark wood furniture and shelving as the dominant material throughout
- a framed or hung celestial star chart on the wall -- a meaningful, load-bearing prop tied directly to this story
- a shelf of real, lived-in-looking books, including the story's own star atlas
- one or two restrained plants -- enough to feel alive, not an overgrown jungle
- a calm, dark-coated cat, at ease in the space -- a consistent recurring companion, not a one-off
- intimate and lived-in -- a real apartment a person has shaped over time, not a styled set or a showroom
- the loft's front door -- dark wood, a warm strip of hallway light beneath it (introduced this episode, permanent from here)
Room geometry, furniture placement and decor are IDENTICAL in every slide. Only the camera and Nyx move.
Always emit the physical anchors above rather than a bare placeholder word for the room.
Time: night in every slide, lit by the lamp and candles against the cool window light

4. APPROVED STORY STATE (what is currently true)
Environment changes so far: none -- the room is unchanged
What is known in the story: the star cluster from the app was already circled in the atlas, in handwriting that isn't hers -- still unexplained

6. COMPOSITION AND STYLING (lowest authority)
Styling is the lowest authority: anything here that conflicts with a section above is dropped, never blended.

7. NEGATIVE CONSTRAINTS
no text, no letters, no captions, no speech bubbles, no watermark, no signature
highly realistic / photorealistic only -- no anime, cartoon, cel-shaded or 2D-illustration styling
no comic panel grid -- one single continuous image per slide
no new people, no extra characters, no crowd
never stone or dungeon architecture, literal fantasy-portal doorway staging, glowing magic effects
never crystal clusters, hanging trinkets, or dense ivy/vine overload -- greenery and decor stay restrained
never daylight of any kind
never a wide 'tour the apartment' shot -- always close and partial, the room is background, never the subject
never flat or even lighting -- the warm-vs-cool contrast is a hard rule, never dropped
never any pet or animal beyond the established cat
never luxury/penthouse/hotel-suite reinterpretation, or a larger/higher-ceilinged room than the approved slide 1
never redesigning, re-decorating or re-laying-out the room between slides
never removing an environment anchor because the camera moved
never a second or duplicate atlas/book appearing alongside the one physical atlas established in slide 1, or any loose chart pages shown separately from that single object -- there is exactly ONE atlas prop in this story, never two
never a second charm or duplicate small object appearing alongside the one charm introduced in this episode's slide 1 -- there is exactly ONE charm prop across this whole episode

9. PRECEDENCE ON CONFLICT
canonical_identity > explicit_story_facts > environment_master > approved_story_state > previous_slide_visual_state > creative_styling
```

## Message 2 -- slide 1 (no Nyx in frame) -> save as `slide1.png`

```text
Generate slide 1 now: one portrait 2:3 photo.

1. CANONICAL IDENTITY (highest authority -- never overridden)
NO CHARACTER IN THIS FRAME. Nyx does not appear in this image at all: no person, no face, no hands, arms, legs or feet, no fox ears or tail, no silhouette, no shadow of a person and no reflection of one. The frame shows only the environment and the story's props. This slide deliberately carries no identity description; Nyx's canonical identity lock still governs every slide in which she appears.

2. EXPLICIT STORY FACTS FOR THIS SLIDE
Slide 1 of 6 -- hook (HOOK).
Story beat: a plain envelope lying half-pushed under the loft door, the warm hallway strip behind it; she is not in frame yet
New information this slide reveals: an envelope has been pushed under her locked front door from the hallway side
Visual action: a plain envelope lies half under the closed door, the warm hallway strip behind it, its small inked star mark present but not yet the focus; Nyx is not in frame
Character-absent slide: nobody is in frame, so any wardrobe, hair or character continuity above is satisfied by her absence -- never by adding a person.

3. ENVIRONMENT MASTER (immutable for this story)
Nyx's own city attic/loft apartment at night -- the canonical cinematic-loft world-bible setting (warm amber lamp+candle light against cool blue city/moon window light, dark wood, celestial textiles, star chart, books, restrained greenery, a cat), NOT the plain Star-Map bedroom and NOT the earlier illustrated high-fantasy attic. Identical anchor set across every slide of this story. (authority: PROVISIONAL, from the approved slide 1 description)
Never: stone or dungeon architecture, literal fantasy-portal doorway staging, glowing magic effects; crystal clusters, hanging trinkets, or dense ivy/vine overload -- greenery and decor stay restrained; daylight of any kind; a wide 'tour the apartment' shot -- always close and partial, the room is background, never the subject; flat or even lighting -- the warm-vs-cool contrast is a hard rule, never dropped; any pet or animal beyond the established cat; luxury/penthouse/hotel-suite reinterpretation, or a larger/higher-ceilinged room than the approved slide 1; redesigning, re-decorating or re-laying-out the room between slides; removing an environment anchor because the camera moved; a second or duplicate atlas/book appearing alongside the one physical atlas established in slide 1, or any loose chart pages shown separately from that single object -- there is exactly ONE atlas prop in this story, never two; a second charm or duplicate small object appearing alongside the one charm introduced in this episode's slide 1 -- there is exactly ONE charm prop across this whole episode

4. APPROVED STORY STATE (what is currently true)
Story opening -- nothing approved yet.
Props: envelope: not yet discovered
Time: night.

5. PREVIOUS APPROVED SLIDE (action continuity only)
No approved previous slide -- this slide establishes the scene described by the environment master above.

6. COMPOSITION AND STYLING (lowest authority)
Composition: Low, tight, floor-level on the envelope and the gap under the door.
Camera: distance: tight; angle: floor-level, looking along the floorboards at the gap under the door; height: floor; subject: the envelope half-pushed under the door, warm hallway light in the gap; body_position: Nyx not in frame
Public social default: believable smartphone/iPhone-style photography, as if she took it herself; personal and homemade: slightly imperfect framing, natural angles, no art direction; normal household lighting and ordinary phone-camera depth of field; a lived-in, ordinary environment with real clutter -- never a styled set; no lingerie-adjacent styling and no cleavage-forward framing; no glamour-shoot or influencer posing; no movie-poster, cinematic, editorial or key-art look; no exaggerated orange/teal or heavy colour grading; restrained supernatural elements: physical evidence first, visual effects never; clean art only: no rendered text, letters, logo or watermark anywhere in the image
Must not repeat: A new composition is a NEW CAMERA POSITION IN THE SAME ROOM. Every environment anchor above still exists in the same geometry (a closer shot may show it partly or leave it out of frame, never contradict it) -- 'different composition' never means a different, bigger, tidier or redesigned room.

7. NEGATIVE CONSTRAINTS
no person anywhere in the frame -- no face, hands, arms, legs, feet, fox ears, tail, silhouette, shadow of a person or reflection
no woman, no fox-girl, no human figure, not even partially, cropped or out of focus

8. TEXT POLICY
Generate clean art with NO text baked in. The overlay line is applied afterwards by PS-05 (carousel_handoff.apply_overlays); leave the upper third, above the door gap, clear of the envelope readable.
```

## Message 3 -- Nyx primer (no image)

Attach: `brand/nyx_identity/generation_refs/gen_ref_1_face_closeup.png`, `brand/nyx_identity/generation_refs/gen_ref_2_head_fox_ears.png`, `brand/nyx_identity/generation_refs/gen_ref_3_fox_ear_detail.png`

```text
Identity rules for every image in which Nyx appears. The attached crops are her face and fox ears -- match them exactly. Do not generate anything yet -- reply only "ready".

1. CANONICAL IDENTITY (highest authority -- never overridden)
Nyx Emberfall -- adult woman (mid-20s appearance), the EXACT SAME character as the attached/referenced master identity image: long dark brown/near-black hair, large amber/golden eyes, warm light skin with realistic natural texture (visible pores, no plastic/AI-smooth skin), subtle realistic fox ears naturally integrated into her head, exactly one bushy dark-brown fox tail with a lighter tip, small beauty mark on her anatomical LEFT cheek (viewer-right when she faces the camera) in the exact position and size shown in the reference, ONE distinct canonical beauty mark (never replaced by freckles; freckles kept separately), exactly TWO visible ears in total -- both canonical fox ears on top of her head -- with NO human ears visible on either side, her hair covering the human-ear locations, slim/athletic adult build, 5'6"/168cm proportions. Highly realistic, photorealistic, premium quality. Elegant, confident, mysterious. Preserve facial bone structure, eye shape, nose, lips, jawline and hairline exactly as the reference. Do not redesign, beautify into a different face, or drift into a stylized/illustrated/cartoon look.
Nyx Emberfall is an ADULT woman, 25+ (mid-20s appearance). Adult face, adult proportions and adult height in every image, including close-ups and full-body shots. Never a minor, never teen- or child-coded, never de-aged, never chibi.
Identity anchors -- face: same face as the primary master reference in every image -- exact bone structure, eye shape, nose shape, lips, jawline and hairline; eyes: large amber/golden eyes, same shape and color every time; hair: long, dark brown / near-black hair, same hairline; styling (loose, tied, braided) may vary, color and hairline never do; ears: subtle, realistic fox ears integrated naturally into the head -- same shape, size, placement and fur pattern (dark with a lighter/paler inner-fur and tip pattern as shown in the master reference) in every image. Nyx has exactly two visible ears: her two anatomical fox ears. Do not generate additional human ears, duplicate ears, costume/headband ears, or four-ear anatomy. Exactly TWO visible ears in total, both canonical fox ears on top of her head. Absolutely NO human ears visible on either side of her head: her hair naturally covers the anatomical human-ear locations.; tail: exactly ONE bushy fox tail, dark brown with a lighter tip, matching the master reference -- never two, never absent when a scene requires full-body continuity; skin_and_marks: warm light skin tone with realistic, natural texture -- visible pores and subtle imperfections; never smoothed into plastic/AI-generic skin; Small beauty mark on Nyx's anatomical LEFT cheek (viewer-right when she faces the camera), preserving the exact position and size shown in reference_primary: ONE distinct small dark mark, mid-cheek below the outer corner of that eye, level with the lower part of her nose. It is never replaced by, or lost among, freckles; her natural freckles are preserved separately. Position and size never change.; body: slim / athletic adult build; same height and proportions across every image; realism: highly realistic / photorealistic, premium visual quality -- human-dominant appearance with restrained (not exaggerated/mascot) fox traits
A previously generated slide informs WHAT IS HAPPENING. It is never evidence for who Nyx is: on any conflict, the canonical master reference wins.

3. ENVIRONMENT MASTER (immutable for this story)
Nyx's own city attic/loft apartment at night -- the canonical cinematic-loft world-bible setting (warm amber lamp+candle light against cool blue city/moon window light, dark wood, celestial textiles, star chart, books, restrained greenery, a cat), NOT the plain Star-Map bedroom and NOT the earlier illustrated high-fantasy attic. Identical anchor set across every slide of this story. (authority: LOCKED, frozen by the approved slide 1 image)
Wardrobe: dark/black top and dark jeans in every slide -- as actually published; pendant necklace only, NO earrings; fox ears and tail throughout; hair covers the sides of her head so ONLY the two fox ears show, never a human ear
Hair: the same hairstyle as the approved slide 1 (down, loose), unchanged for the whole story
Never: stone or dungeon architecture, literal fantasy-portal doorway staging, glowing magic effects; crystal clusters, hanging trinkets, or dense ivy/vine overload -- greenery and decor stay restrained; daylight of any kind; a wide 'tour the apartment' shot -- always close and partial, the room is background, never the subject; flat or even lighting -- the warm-vs-cool contrast is a hard rule, never dropped; any pet or animal beyond the established cat; luxury/penthouse/hotel-suite reinterpretation, or a larger/higher-ceilinged room than the approved slide 1; redesigning, re-decorating or re-laying-out the room between slides; removing an environment anchor because the camera moved; a second or duplicate atlas/book appearing alongside the one physical atlas established in slide 1, or any loose chart pages shown separately from that single object -- there is exactly ONE atlas prop in this story, never two; a second charm or duplicate small object appearing alongside the one charm introduced in this episode's slide 1 -- there is exactly ONE charm prop across this whole episode; lingerie-adjacent or cleavage-forward styling; an outfit, hairstyle or time-of-day change that the story never states

4. APPROVED STORY STATE (what is currently true)
Props: envelope: unopened, one inked star mark; star_atlas: closed, on the shelf
Time: night. Wardrobe: dark/black top and dark jeans in every slide -- as actually published; pendant necklace only, NO earrings; fox ears and tail throughout; hair covers the sides of her head so ONLY the two fox ears show, never a human ear. Hair: down, loose, as established

6. COMPOSITION AND STYLING (lowest authority)
Public social default: believable smartphone/iPhone-style photography, as if she took it herself; personal and homemade: slightly imperfect framing, natural angles, no art direction; normal household lighting and ordinary phone-camera depth of field; a lived-in, ordinary environment with real clutter -- never a styled set; attractive but normal everyday clothing; no lingerie-adjacent styling and no cleavage-forward framing; no glamour-shoot or influencer posing; no movie-poster, cinematic, editorial or key-art look; no exaggerated orange/teal or heavy colour grading; restrained supernatural elements: physical evidence first, visual effects never; clean art only: no rendered text, letters, logo or watermark anywhere in the image

7. NEGATIVE CONSTRAINTS
no character redesign: same face, ears, hair, eyes and beauty mark every slide (outfit and environment are the story's own continuity fields, not identity)
anime, cartoon or cel-shaded styling
furry / mascot appearance
plastic AI skin / over-smoothed skin
generic influencer face drift
younger, teenage or childlike appearance
ethnicity or facial-phenotype drift
eye-color drift
different fox ear design
any ears beyond her two anatomical fox ears (human ears, duplicate ears, costume/headband ears, four-ear anatomy)
extra tails or a missing tail when the scene needs full-body continuity
changing the beauty-mark location, size or side (anatomical LEFT cheek, viewer-right)
changing facial or body proportions between images
redesigning Nyx because a generation model prefers a different face
beautifying/idealizing Nyx into a different-looking person
any real-person likeness -- Nyx remains an original synthetic character
suggestive and fully clothed: no nudity, no exposed nipples or genitals, no sexual acts, no underwear-only reveal -- public acquisition content teases, it never goes explicit
never lingerie-adjacent or cleavage-forward styling
never an outfit, hairstyle or time-of-day change that the story never states
```

## Message 4 -- slide 2 -> save as `slide2.png`

```text
Generate slide 2 now: one portrait 2:3 photo.

2. EXPLICIT STORY FACTS FOR THIS SLIDE
Slide 2 of 6 -- context (CONTEXT).
Story beat: Nyx crouched at the door picking the envelope up, the loft visible behind her
New information this slide reveals: it is Nyx, alone at night in her own attic apartment, and she has found it
Visual action: crouched at the door, picking the envelope up off the floor

4. APPROVED STORY STATE (what is currently true)
Carried from the APPROVED slide 1.
Nyx is: at the door or the shelf; the warm hallway strip behind it; she is not in frame yet

5. PREVIOUS APPROVED SLIDE (action continuity only)
Slide 1 was shot as: distance: tight, angle: floor-level, looking along the floorboards at the gap under the door, height: floor, subject: the envelope half-pushed under the door, warm hallway light in the gap, body_position: nyx not in frame. It looked like: low, tight, floor-level on the envelope and the gap under the door. Continue the action from it. It defines neither the room nor the face, and this slide must not reuse its camera.

6. COMPOSITION AND STYLING (lowest authority)
Composition: Medium-wide, low, crouched at the door with the room and window behind her
Camera: distance: medium-wide; angle: low, three-quarter from inside the room toward the door; height: low, near crouch height; subject: Nyx crouched at the door with the envelope, the room and window behind her; body_position: crouched, one hand lifting the envelope
Must not repeat: slide 1's floor-level envelope framing; slide 1's camera (distance: tight / angle: floor-level, looking along the floorboards at the gap under the door / height: floor / subject: the envelope half-pushed under the door, warm hallway light in the gap / body_position: nyx not in frame); information slide 1 already revealed: an envelope has been pushed under her locked front door from the hallway side; A new composition is a NEW CAMERA POSITION IN THE SAME ROOM. Every environment anchor above still exists in the same geometry (a closer shot may show it partly or leave it out of frame, never contradict it) -- 'different composition' never means a different, bigger, tidier or redesigned room.

8. TEXT POLICY
Generate clean art with NO text baked in. This slide carries no overlay line -- the image is final as generated.
```

## Message 5 -- slide 3 -> save as `slide3.png`

```text
Generate slide 3 now: one portrait 2:3 photo.

2. EXPLICIT STORY FACTS FOR THIS SLIDE
Slide 3 of 6 -- escalation (ESCALATION).
Story beat: close on the envelope in her hands: one inked star mark on it, and she recognises it
New information this slide reveals: the envelope carries the same inked starburst mark as the charm, and she recognises it
Visual action: holding the envelope in both hands, studying the single ink mark

4. APPROVED STORY STATE (what is currently true)
Carried from the APPROVED slide 2.
Nyx is: at the door or the shelf; Nyx crouched at the door picking the envelope up.

5. PREVIOUS APPROVED SLIDE (action continuity only)
Slide 2 was shot as: distance: medium-wide, angle: low, three-quarter from inside the room toward the door, height: low, near crouch height, subject: nyx crouched at the door with the envelope, the room and window behind her, body_position: crouched, one hand lifting the envelope. It looked like: medium-wide, low, crouched at the door with the room and window behind her. Continue the action from it. It defines neither the room nor the face, and this slide must not reuse its camera.

6. COMPOSITION AND STYLING (lowest authority)
Composition: Tight over her hands, the envelope sharp, her face soft above
Camera: distance: tight close-up; angle: over her hands, slightly above; height: chest height; subject: the envelope and its one inked star mark in sharp focus, her face soft above; body_position: standing, envelope held in both hands
Must not repeat: slide 2's wide door framing; slide 1's camera (distance: tight / angle: floor-level, looking along the floorboards at the gap under the door / height: floor / subject: the envelope half-pushed under the door, warm hallway light in the gap / body_position: nyx not in frame); information slide 1 already revealed: an envelope has been pushed under her locked front door from the hallway side; slide 2's camera (distance: medium-wide / angle: low, three-quarter from inside the room toward the door / height: low, near crouch height / subject: nyx crouched at the door with the envelope, the room and window behind her / body_position: crouched, one hand lifting the envelope); information slide 2 already revealed: it is nyx, alone at night in her own attic apartment, and she has found it; A new composition is a NEW CAMERA POSITION IN THE SAME ROOM. Every environment anchor above still exists in the same geometry (a closer shot may show it partly or leave it out of frame, never contradict it) -- 'different composition' never means a different, bigger, tidier or redesigned room.

8. TEXT POLICY
Generate clean art with NO text baked in. The overlay line is applied afterwards by PS-05 (carousel_handoff.apply_overlays); leave the lower third, below her hands, clear of the star mark readable.
```

## Message 6 -- slide 4 -> save as `slide4.png`

```text
Generate slide 4 now: one portrait 2:3 photo.

2. EXPLICIT STORY FACTS FOR THIS SLIDE
Slide 4 of 6 -- progression (PROGRESSION).
Story beat: she looks back at the door, unopened envelope still in hand; the black cat already staring at it
New information this slide reveals: whoever left it came right to her door without knocking, and the cat has noticed too
Visual action: turned back toward the closed door, envelope in hand, the black cat staring at the door

4. APPROVED STORY STATE (what is currently true)
Carried from the APPROVED slide 3.
Nyx is: at the door or the shelf; close on the envelope in her hands: one inked star mark on it, and she recognises it

5. PREVIOUS APPROVED SLIDE (action continuity only)
Slide 3 was shot as: distance: tight close-up, angle: over her hands, slightly above, height: chest height, subject: the envelope and its one inked star mark in sharp focus, her face soft above, body_position: standing, envelope held in both hands. It looked like: tight over her hands, the envelope sharp, her face soft above. Continue the action from it. It defines neither the room nor the face, and this slide must not reuse its camera.

6. COMPOSITION AND STYLING (lowest authority)
Composition: Medium, turned toward the door, the cat in frame looking the same way
Camera: distance: medium; angle: eye-level, from behind her shoulder toward the door; height: standing eye-level; subject: Nyx and the black cat both facing the closed door; body_position: standing, turned toward the door, envelope at her side
Must not repeat: slide 3's tight hands framing; slide 1's camera (distance: tight / angle: floor-level, looking along the floorboards at the gap under the door / height: floor / subject: the envelope half-pushed under the door, warm hallway light in the gap / body_position: nyx not in frame); information slide 1 already revealed: an envelope has been pushed under her locked front door from the hallway side; slide 2's camera (distance: medium-wide / angle: low, three-quarter from inside the room toward the door / height: low, near crouch height / subject: nyx crouched at the door with the envelope, the room and window behind her / body_position: crouched, one hand lifting the envelope); information slide 2 already revealed: it is nyx, alone at night in her own attic apartment, and she has found it; slide 3's camera (distance: tight close-up / angle: over her hands, slightly above / height: chest height / subject: the envelope and its one inked star mark in sharp focus, her face soft above / body_position: standing, envelope held in both hands); information slide 3 already revealed: the envelope carries the same inked starburst mark as the charm, and she recognises it; A new composition is a NEW CAMERA POSITION IN THE SAME ROOM. Every environment anchor above still exists in the same geometry (a closer shot may show it partly or leave it out of frame, never contradict it) -- 'different composition' never means a different, bigger, tidier or redesigned room.

8. TEXT POLICY
Generate clean art with NO text baked in. The overlay line is applied afterwards by PS-05 (carousel_handoff.apply_overlays); leave the upper third, clear of her face, the cat and the door gap readable.
```

## Message 7 -- slide 5 -> save as `slide5.png`

```text
Generate slide 5 now: one portrait 2:3 photo.

2. EXPLICIT STORY FACTS FOR THIS SLIDE
Slide 5 of 6 -- progression (PROGRESSION).
Story beat: she sets the envelope on the shelf beside the closed atlas, the two objects together under the star chart
New information this slide reveals: she puts the envelope beside the atlas under the star chart, linking the two objects
Visual action: setting the envelope on the shelf next to the closed atlas

4. APPROVED STORY STATE (what is currently true)
Carried from the APPROVED slide 4.
Nyx is: at the door or the shelf; she looks back at the door, unopened envelope still in hand; the black cat already staring at it

5. PREVIOUS APPROVED SLIDE (action continuity only)
Slide 4 was shot as: distance: medium, angle: eye-level, from behind her shoulder toward the door, height: standing eye-level, subject: nyx and the black cat both facing the closed door, body_position: standing, turned toward the door, envelope at her side. It looked like: medium, turned toward the door, the cat in frame looking the same way. Continue the action from it. It defines neither the room nor the face, and this slide must not reuse its camera.

6. COMPOSITION AND STYLING (lowest authority)
Composition: Medium-wide at the shelf, envelope and atlas side by side, star chart above
Camera: distance: medium-wide; angle: straight-on at shelf height; height: shelf height; subject: the envelope and the closed atlas side by side under the framed star chart, her hand withdrawing; body_position: standing at the shelf, one hand just leaving the envelope
Must not repeat: slide 4's turned-to-the-door framing; slide 1's camera (distance: tight / angle: floor-level, looking along the floorboards at the gap under the door / height: floor / subject: the envelope half-pushed under the door, warm hallway light in the gap / body_position: nyx not in frame); information slide 1 already revealed: an envelope has been pushed under her locked front door from the hallway side; slide 2's camera (distance: medium-wide / angle: low, three-quarter from inside the room toward the door / height: low, near crouch height / subject: nyx crouched at the door with the envelope, the room and window behind her / body_position: crouched, one hand lifting the envelope); information slide 2 already revealed: it is nyx, alone at night in her own attic apartment, and she has found it; slide 3's camera (distance: tight close-up / angle: over her hands, slightly above / height: chest height / subject: the envelope and its one inked star mark in sharp focus, her face soft above / body_position: standing, envelope held in both hands); information slide 3 already revealed: the envelope carries the same inked starburst mark as the charm, and she recognises it; slide 4's camera (distance: medium / angle: eye-level, from behind her shoulder toward the door / height: standing eye-level / subject: nyx and the black cat both facing the closed door / body_position: standing, turned toward the door, envelope at her side); information slide 4 already revealed: whoever left it came right to her door without knocking, and the cat has noticed too; A new composition is a NEW CAMERA POSITION IN THE SAME ROOM. Every environment anchor above still exists in the same geometry (a closer shot may show it partly or leave it out of frame, never contradict it) -- 'different composition' never means a different, bigger, tidier or redesigned room.

8. TEXT POLICY
Generate clean art with NO text baked in. This slide carries no overlay line -- the image is final as generated.
```

## Message 8 -- slide 6 -> save as `slide6.png`

```text
Generate slide 6 now: one portrait 2:3 photo.

2. EXPLICIT STORY FACTS FOR THIS SLIDE
Slide 6 of 6 -- payoff (PAYOFF).
Story beat: her hand resting on the unopened envelope, decision not made, calm not frightened
New information this slide reveals: she has not decided yet, open it or check the hallway, and leaves the choice open
Visual action: her hand resting flat on the unopened envelope, deciding

4. APPROVED STORY STATE (what is currently true)
Carried from the APPROVED slide 5.
Nyx is: at the door or the shelf; she sets the envelope on the shelf beside the closed atlas, the two objects together under the star chart

5. PREVIOUS APPROVED SLIDE (action continuity only)
Slide 5 was shot as: distance: medium-wide, angle: straight-on at shelf height, height: shelf height, subject: the envelope and the closed atlas side by side under the framed star chart, her hand withdrawing, body_position: standing at the shelf, one hand just leaving the envelope. It looked like: medium-wide at the shelf, envelope and atlas side by side, star chart above. Continue the action from it. It defines neither the room nor the face, and this slide must not reuse its camera.

6. COMPOSITION AND STYLING (lowest authority)
Composition: Close on her hand and the envelope, her face partly in frame and soft
Camera: distance: close-up; angle: slightly high, looking down at her hand on the envelope; height: just above shelf height; subject: her hand on the unopened envelope, her face partly in frame and soft; body_position: standing at the shelf, hand resting on the envelope
Must not repeat: slide 5's wide shelf framing; slide 1's camera (distance: tight / angle: floor-level, looking along the floorboards at the gap under the door / height: floor / subject: the envelope half-pushed under the door, warm hallway light in the gap / body_position: nyx not in frame); information slide 1 already revealed: an envelope has been pushed under her locked front door from the hallway side; slide 2's camera (distance: medium-wide / angle: low, three-quarter from inside the room toward the door / height: low, near crouch height / subject: nyx crouched at the door with the envelope, the room and window behind her / body_position: crouched, one hand lifting the envelope); information slide 2 already revealed: it is nyx, alone at night in her own attic apartment, and she has found it; slide 3's camera (distance: tight close-up / angle: over her hands, slightly above / height: chest height / subject: the envelope and its one inked star mark in sharp focus, her face soft above / body_position: standing, envelope held in both hands); information slide 3 already revealed: the envelope carries the same inked starburst mark as the charm, and she recognises it; slide 4's camera (distance: medium / angle: eye-level, from behind her shoulder toward the door / height: standing eye-level / subject: nyx and the black cat both facing the closed door / body_position: standing, turned toward the door, envelope at her side); information slide 4 already revealed: whoever left it came right to her door without knocking, and the cat has noticed too; slide 5's camera (distance: medium-wide / angle: straight-on at shelf height / height: shelf height / subject: the envelope and the closed atlas side by side under the framed star chart, her hand withdrawing / body_position: standing at the shelf, one hand just leaving the envelope); information slide 5 already revealed: she puts the envelope beside the atlas under the star chart, linking the two objects; A new composition is a NEW CAMERA POSITION IN THE SAME ROOM. Every environment anchor above still exists in the same geometry (a closer shot may show it partly or leave it out of frame, never contradict it) -- 'different composition' never means a different, bigger, tidier or redesigned room.

8. TEXT POLICY
Generate clean art with NO text baked in. The overlay line is applied afterwards by PS-05 (carousel_handoff.apply_overlays); leave the lower third, clear of her hand, the envelope and her face readable.
```
