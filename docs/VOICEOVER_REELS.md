# Nyx voiceover Reels

Each story episode can become a **15-25 s vertical Reel** (1080x1920, 30 fps):
- the episode's QA-approved slides, with subtle push or pan motion
- Nyx's voice
- burned-in captions
- an optional licensed music bed, ducked under the voice

`nyx_reels.py` **never generates audio, never calls an API, never uploads and never publishes**.
It validates what you supply, writes a render plan, and runs local ffmpeg only when you pass
`--execute`. None of it touches `automation_trial.json` or `generation_executor.json`.

| File | Role |
|---|---|
| `brand/nyx_voice/voice_bible.json` (+ `VOICE_BIBLE.md`) | Voice rules: origin, delivery, accent-neutrality, pace, vocabulary, prohibited traits, Reel format, and the (not yet locked) production voice |
| `episodes/<story_id>/reel/voiceover_script.json` | Per-episode script: timed lines, caption chunks, scenes (slide, timing, motion), music |
| `generated_assets/carousel_item<N>/reel/voiceover.wav` (or `.mp3`) + `voiceover.provenance.json` | The supplied voice, and who or what made it |
| `episodes/voice_generation.json` | Cost gate for an optional paid voice provider (**off**) |
| `episodes/<story_id>/reel/render_plan.json`, `captions.ass` | Written by `plan` |

## Script schema (`voiceover_script.json`)

```json
{"story_id": "s1e02_public", "voice_bible_version": "1.0.0", "duration_s": 23.5,
 "lines":  [{"id": "hook", "start_s": 0.4, "end_s": 3.4, "text": "someone pushed this under my door tonight.",
             "captions": [{"text": "someone pushed this", "start_s": 0.4, "end_s": 1.8}, ...]}],
 "scenes": [{"slide": 1, "start_s": 0.0, "end_s": 3.6, "motion": "push_in", "zoom": 1.05}, ...],
 "music":  null | {"path": "...", "license": "...", "gain_db": -20}}
```

## What blocks a Reel (`python3 nyx_reels.py validate <story_id>`)

- **Missing audio**, unreadable audio, audio over 25 s, or audio more than 0.75 s off the
  script's duration.
- Audio with **no provenance**, provenance for an older script version, or a voice that is not
  `original_synthetic` or is marked as cloned.
- Paid-provider audio while the cost gate is off, over its cap, or from another provider.
- **Overlong (over 25 s) or too-short (under 15 s)** scripts.
- Pace outside 120-165 wpm, a sentence over 14 words, an exclamation mark, ALL CAPS, a banned
  phrase, third-person narration, or engagement bait.
- **Missing captions** on any line, captions that don't match the spoken words, a chunk over
  6 words, or a chunk on screen under 0.6 s.
- **Broken vertical aspect**: a non-portrait source, or a canvas other than 1080x1920 9:16.
- Scenes with gaps or overlaps, a motion outside push/pan/hold, or zoom over 1.06.
- **Art that is not QA-approved**. Reels only use slides that passed the identity checks.
- Music with no licence, or louder than -12 dB.

Only audio in the founder's **locked production voice** makes a Reel release-eligible. Every other
render is written as `<story>_reel.TEST.mp4`.

## Test it without spending

The checks and plan, with no audio and no network:

```
python3 -m unittest test_nyx_reels              # sandbox: placeholder slides + a SILENT wav fixture
python3 nyx_reels.py validate s1e02_public      # today: BLOCKED (no approved slides, no audio)
python3 nyx_reels.py script-text s1e02_public   # the six lines, plain
```

### Free manual test voice (TEST only, never released)

This uses a **generic** built-in system voice (or the open-source Piper/espeak-ng on Linux). It
checks timing, captions, motion and mix, not Nyx's real voice. Stock system voices are for
testing: their licences may not cover commercial use. Do this once s1e02's slides are approved.

```
python3 nyx_reels.py script-text s1e02_public --say > /tmp/nyx_vo.txt     # includes [[slnc]] pauses
say -r 150 -f /tmp/nyx_vo.txt -o /tmp/nyx_vo.aiff                          # macOS, any generic voice
mkdir -p generated_assets/carousel_item33/reel
afconvert -f WAVE -d LEI16@44100 /tmp/nyx_vo.aiff generated_assets/carousel_item33/reel/voiceover.wav
python3 nyx_reels.py audio-provenance s1e02_public --source free_local_tts --tool "macOS say" --voice "system default"
python3 nyx_reels.py validate s1e02_public      # if "timed for 23.5 s": nudge -r (higher = shorter) and redo
python3 nyx_reels.py plan s1e02_public          # writes render_plan.json + captions.ass
brew install ffmpeg                             # once; local, free (needs libass for captions)
python3 nyx_reels.py render s1e02_public --execute   # -> generated_assets/carousel_item33/reel/s1e02_public_reel.TEST.mp4
```

## Optional higher-quality voice (paid, gated)

This repo calls no voice provider. A natural voice is produced **outside** it, in the provider's
own app or API, and then supplied like the test voice. Three things must all be true before such
audio is accepted:

1. **The founder locks the production voice** in `brand/nyx_voice/voice_bible.json` →
   `production_voice` (`locked: true`, `provider`, `voice`, `settings`, `locked_by`). It must be an
   *original* designed or stock voice, never cloned or imitated.
2. **The cost gate is on** in `episodes/voice_generation.json`: `enabled: true`, `enabled_by`,
   `provider` the same as above, and `cap_usd_per_episode` (US$1.00 by default). A 20 s script
   is about 300 characters, normally cents.
3. **The audio is recorded with its real cost:**
   `python3 nyx_reels.py audio-provenance <story> --source paid_provider --provider <p> --voice <v> --cost-usd <usd>`.
   Over the cap, another provider or another voice is blocked. Only the locked voice is
   release-eligible.

Publishing a Reel is out of scope here. It goes through the existing release and approval flow.
