# Nyx Reddit draft queue

Generated 2026-09-30. Nothing here is posted. A draft becomes postable only via `reddit_ops.py packet`, which re-runs the gate.

## s1e02_public__aiart

- Community: r/aiArt · kind: promo_thread_comment · status: **draft** · gate: **BLOCK** (2026-09-30)
- Angle: one-image thread entry, process-first, no link

**Title:** 

**Body:**

> Nyx Emberfall, an ongoing serialised story with one original character. Nyx Emberfall is a fictional adult character and this image is AI-generated. Not a real person.
> 
> Episode 2, "The Same Handwriting", opens with a plain envelope lying half-pushed under the loft door, the warm hallway strip behind it. This frame: She looks back at the door, unopened envelope still in hand.
> 
> What I'm working on is continuity: one fixed reference sheet for her, one room with 11 locked anchors, and every beat planned before any image is made. Happy to talk process.

**Images:** frame_1_progression (slide 4, brief only)

**Facts and their sources:**
- hook ← `story_plan[HOOK].beat`
- frame beat ← `story_plan[4].beat`
- 11 anchors ← `environment_lock.anchors`

**Gate blocks:**
- r/aiArt rules are not verified (provisional: approved_promo_thread)
- r/aiArt: rule 'ai_content' is an operator summary; capture the verbatim text
- r/aiArt: rule 'ai_content' has unobserved values ['allowed', 'disclosure_required', 'disclosure_format']
- r/aiArt: rule 'self_promotion' is an operator summary; capture the verbatim text
- r/aiArt: rule 'nsfw' not verified
- r/aiArt: rule 'links' not verified
- r/aiArt: rule 'account_minimums' not verified
- r/aiArt: rule 'flair' not verified
- r/aiArt: rule 'frequency' not verified
- promo-thread unit needs the thread URL
- image 'frame_1_progression' not generated yet (brief only)
- account age 4d < required 30d
- account karma 1 < required 100
- account profile disclosure not checked yet
- warm-up: 0/10 useful comments logged

## s1e02_public__aiartwork

- Community: r/AIArtwork · kind: native_post · status: **draft** · gate: **BLOCK** (2026-09-30)
- Angle: single atmospheric still with a short story hook

**Title:** Nyx crouched at the door picking the envelope up, the loft visible behind her (AI-made still from an ongoing story)

**Body:**

> Nyx crouched at the door picking the envelope up, the loft visible behind her.
> 
> One still from episode 2 of "The Same Handwriting". Every frame of the series is set in the same room, lit the same way; only she and the camera move.
> 
> Nyx Emberfall is a fictional adult character and this image is AI-generated. Not a real person.

**Images:** frame_1_context (slide 2, brief only)

**Facts and their sources:**
- this frame ← `story_plan[2].beat`
- same room, same light ← `environment_lock.geometry_rule`

**Gate blocks:**
- r/AIArtwork rules are not verified (provisional: approved_native)
- r/AIArtwork: rule 'ai_content' not verified
- r/AIArtwork: rule 'self_promotion' not verified
- r/AIArtwork: rule 'nsfw' not verified
- r/AIArtwork: rule 'links' not verified
- r/AIArtwork: rule 'account_minimums' not verified
- r/AIArtwork: rule 'flair' not verified
- r/AIArtwork: rule 'frequency' not verified
- image 'frame_1_context' not generated yet (brief only)
- account age 4d < required 30d
- account karma 1 < required 100
- account profile disclosure not checked yet
- warm-up: 0/10 useful comments logged

## s1e02_public__aigeneratedart

- Community: r/AIGeneratedArt · kind: native_post · status: **draft** · gate: **BLOCK** (2026-09-30)
- Angle: story-first micro-fiction, image set carries the story

**Title:** This wasn't here when I locked up. (a short AI-made story, 3 frames)

**Body:**

> "The Same Handwriting", episode 2 of a small ongoing story. Nyx Emberfall is a fictional adult character and these images are AI-generated. Not a real person.
> 
> 1. A plain envelope lying half-pushed under the loft door, the warm hallway strip behind it. "This wasn't here when I locked up."
> 2. The envelope in her hands: one inked star mark on it, and she recognises it. "That's the same mark."
> 3. Her hand resting on the unopened envelope, decision not made, calm not frightened. "Open it, or check the hallway first?"

**Images:** frame_1_hook (slide 1, brief only); frame_2_escalation (slide 3, brief only); frame_3_payoff (slide 6, brief only)

**Facts and their sources:**
- scene lines ← `story_plan[HOOK/ESCALATION/PAYOFF].beat`
- quoted lines ← `story_plan[*].overlay_copy`

**Gate blocks:**
- r/AIGeneratedArt rules are not verified (provisional: approved_native)
- r/AIGeneratedArt: rule 'ai_content' is an operator summary; capture the verbatim text
- r/AIGeneratedArt: rule 'ai_content' has unobserved values ['allowed', 'disclosure_required', 'disclosure_format']
- r/AIGeneratedArt: rule 'self_promotion' is an operator summary; capture the verbatim text
- r/AIGeneratedArt: rule 'self_promotion' has unobserved values ['mode']
- r/AIGeneratedArt: rule 'nsfw' is an operator summary; capture the verbatim text
- r/AIGeneratedArt: rule 'nsfw' has unobserved values ['allowed']
- r/AIGeneratedArt: rule 'links' is an operator summary; capture the verbatim text
- r/AIGeneratedArt: rule 'links' has unobserved values ['external_allowed']
- r/AIGeneratedArt: rule 'account_minimums' is an operator summary; capture the verbatim text
- r/AIGeneratedArt: rule 'flair' is an operator summary; capture the verbatim text
- r/AIGeneratedArt: rule 'frequency' is an operator summary; capture the verbatim text
- sub requires flair; none chosen
- image 'frame_1_hook' not generated yet (brief only)
- image 'frame_2_escalation' not generated yet (brief only)
- image 'frame_3_payoff' not generated yet (brief only)
- account age 4d < required 30d
- account karma 1 < required 100
- account profile disclosure not checked yet
- warm-up: 0/10 useful comments logged

## s1e02_public__chatgpt

- Community: r/ChatGPT · kind: native_post · status: **draft** · gate: **BLOCK** (2026-09-30)
- Angle: reproducible ChatGPT image generation prompt structure for character consistency

**Title:** Keeping one character consistent across a whole episode in ChatGPT image generation: the prompt order that worked

**Body:**

> Nyx Emberfall is a fictional adult character and this image is AI-generated. Not a real person. I use ChatGPT image generation for an ongoing story with one recurring character, and the thing that made the biggest difference to consistency is building every prompt in the same order:
> 
> 1. Identity block first: the same paragraph every time, describing only what must never change, with the reference image attached.
> 2. Scene: the same room description every frame (11 fixed details), then this frame's single story beat.
> 3. Wardrobe and time of day, locked for the whole episode.
> 4. Camera: only the camera and the character move.
> 5. A short negative list: no text, no restyling, no extra or missing features.
> 
> Earlier frames can tell the model what is happening, never who she is. Only the reference sheet decides the face, otherwise the drift compounds frame to frame.
> 
> This frame: She sets the envelope on the shelf beside the closed atlas, the two objects together under the star chart.
> 
> Anyone found a better way to stop a face drifting over a long series?

**Images:** frame_1_progression (slide 5, brief only)

**Facts and their sources:**
- tool ← `generation.intended_model`
- 11 fixed details ← `environment_lock.anchors`
- prompt order ← `identity_spec.base_generation_prompt + story lock layers`
- this frame ← `story_plan[5].beat`
- Earlier frames can tell the model what is happening, never who she is. Only the reference sheet decides the face, otherwise the drift compounds frame to frame. ← `identity_lock.never_from_previous_slides`

**Gate blocks:**
- r/ChatGPT rules are not verified (provisional: approved_native)
- r/ChatGPT: rule 'ai_content' not verified
- r/ChatGPT: rule 'self_promotion' not verified
- r/ChatGPT: rule 'nsfw' not verified
- r/ChatGPT: rule 'links' not verified
- r/ChatGPT: rule 'account_minimums' not verified
- r/ChatGPT: rule 'flair' not verified
- r/ChatGPT: rule 'frequency' not verified
- image 'frame_1_progression' not generated yet (brief only)
- account age 4d < required 30d
- account karma 1 < required 100
- account profile disclosure not checked yet
- warm-up: 0/10 useful comments logged

