# Nyx Reddit draft queue

Generated 2026-09-30. Nothing here is posted. A draft becomes postable only via `reddit_ops.py packet`, which re-runs the gate.

## s1e02_public__aiart

- Community: r/aiArt · kind: native_post · status: **draft** · gate: **BLOCK** (2026-09-30)
- Angle: value-first process: series continuity workflow and what broke

**Title:** Same character, same room, 3 frames of an ongoing story: what's kept her consistent so far (and what broke)

**Body:**

> I'm making a serialised visual story around one original character, Nyx Emberfall. Nyx Emberfall is a fictional adult character and these images are AI-generated. Not a real person.
> 
> These 3 frames are from episode 2 of the thread "The Same Handwriting": a plain envelope lying half-pushed under the loft door, the warm hallway strip behind it; she is not in frame yet.
> 
> What I'm actually testing is continuity across a series, not single-image polish:
> - one locked reference sheet (face, eyes, fox ears, one tail, which cheek the beauty mark is on) that overrides everything, including earlier frames
> - one locked room with 11 fixed anchors (room scale, window, bedding, lighting warm, wood, ...) that must match in every frame; only the camera and the character move
> - the whole story planned beat by beat before any image is generated
> 
> What broke so far:
> - The written outfit notes drifted from what the images actually showed, so I corrected the notes to match the images rather than regenerating to match stale notes.
> - A local model I tested failed 6 of 7 story beats, so I dropped it for this series.
> - Earlier frames can tell the model what is happening, never who she is. Only the reference sheet decides the face, otherwise the drift compounds frame to frame.
> 
> Made with ChatGPT image generation. Happy to share the prompt structure.
> 
> How do you keep a location consistent across a long series without it slowly redecorating itself?

**Images:** frame_1_hook (slide 1, brief only); frame_2_escalation (slide 3, brief only); frame_3_payoff (slide 6, brief only)

**Facts and their sources:**
- story_thread ← `story_thread`
- episode ← `episode_id`
- hook beat ← `story_plan[HOOK].beat`
- 11 anchors ← `environment_lock.anchors`
- tool ← `generation.intended_model`
- The written outfit notes drifted from what the images actually showed, so I corrected the notes to match the images rather than regenerating to match stale notes. ← `canon_revisions[0]`
- A local model I tested failed 6 of 7 story beats, so I dropped it for this series. ← `canon_revisions[1]`
- Earlier frames can tell the model what is happening, never who she is. Only the reference sheet decides the face, otherwise the drift compounds frame to frame. ← `identity_lock.never_from_previous_slides`

**Gate blocks:**
- r/aiArt rules are not verified (provisional: approved_native)
- r/aiArt: rule 'ai_content' not verified
- r/aiArt: rule 'self_promotion' not verified
- r/aiArt: rule 'nsfw' not verified
- r/aiArt: rule 'links' not verified
- r/aiArt: rule 'account_minimums' not verified
- r/aiArt: rule 'flair' not verified
- r/aiArt: rule 'frequency' not verified
- image 'frame_1_hook' not generated yet (brief only)
- image 'frame_2_escalation' not generated yet (brief only)
- image 'frame_3_payoff' not generated yet (brief only)
- no account snapshot recorded
- warm-up: 0/10 useful comments logged

## s1e02_public__aigeneratedart

- Community: r/AIGeneratedArt · kind: native_post · status: **draft** · gate: **BLOCK** (2026-09-30)
- Angle: story-first micro-fiction; backup only if r/aiArt fails verification

**Title:** This wasn't here when I locked up. (a short AI-made story, 3 frames)

**Body:**

> "The Same Handwriting", a small ongoing story. Nyx Emberfall is a fictional adult character and these images are AI-generated. Not a real person.
> 
> 1. A plain envelope lying half-pushed under the loft door, the warm hallway strip behind it; she is not in frame yet.
> 2. Close on the envelope in her hands: one inked star mark on it, and she recognises it.
> 3. Her hand resting on the unopened envelope, decision not made, calm not frightened.
> 
> She hasn't opened it yet. Open it, or check the hallway first?

**Images:** frame_1_hook (slide 1, brief only); frame_2_escalation (slide 3, brief only); frame_3_payoff (slide 6, brief only)

**Facts and their sources:**
- beats ← `story_plan[*].beat`
- closing question ← `story_plan[PAYOFF].overlay_copy`

**Gate blocks:**
- r/AIGeneratedArt rules are not verified (provisional: approved_native)
- r/AIGeneratedArt: rule 'ai_content' not verified
- r/AIGeneratedArt: rule 'self_promotion' not verified
- r/AIGeneratedArt: rule 'nsfw' not verified
- r/AIGeneratedArt: rule 'links' not verified
- r/AIGeneratedArt: rule 'account_minimums' not verified
- r/AIGeneratedArt: rule 'flair' not verified
- r/AIGeneratedArt: rule 'frequency' not verified
- image 'frame_1_hook' not generated yet (brief only)
- image 'frame_2_escalation' not generated yet (brief only)
- image 'frame_3_payoff' not generated yet (brief only)
- no account snapshot recorded
- warm-up: 0/10 useful comments logged

