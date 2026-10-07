"""Nyx voiceover Reels: one story episode -> one 15-25 s vertical Reel.

    approved slides (story_continuity) + a SUPPLIED voice file + optional licensed music
      -> validate  (blocks missing audio, overlong scripts, missing captions, broken 9:16)
      -> plan      (ASS captions + one ffmpeg command; nothing executed)
      -> render    (local ffmpeg, only with --execute; zero cost)

This module never generates audio, never calls a paid API, never uploads and
never publishes. The voice comes from outside: a free local TTS for TEST renders
(docs/VOICEOVER_REELS.md), or -- later, behind episodes/voice_generation.json and
the founder's locked production voice -- a paid provider. Only the locked voice
makes a Reel release-eligible.

Inputs, per episode:
    episodes/<story_id>/reel/voiceover_script.json          the script (schema below)
    generated_assets/carousel_item<N>/reel/voiceover.wav|mp3 the supplied voice
    ...voiceover.provenance.json                             who/what made it (audio-provenance)
Rules: brand/nyx_voice/voice_bible.json.

    python3 nyx_reels.py script-text <story_id> [--say]   lines for a TTS tool ([[slnc]] pauses for macOS say)
    python3 nyx_reels.py audio-provenance <story_id> --source free_local_tts --tool "<tool>" --voice "<voice>"
    python3 nyx_reels.py validate <story_id>
    python3 nyx_reels.py plan <story_id>
    python3 nyx_reels.py render <story_id> --execute
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import carousel_handoff
import episode_coordinator as ec
import stages
import story_continuity as sc

ROOT = Path(__file__).resolve().parent
BIBLE_PATH = ROOT / "brand" / "nyx_voice" / "voice_bible.json"
VOICE_GATE_NAME = "voice_generation.json"
SCRIPT_NAME = "voiceover_script.json"
AUDIO_STEM = "voiceover"
AUDIO_SUFFIXES = (".wav", ".mp3")
TIMING_TOLERANCE_S = 0.05
AUDIO_MATCH_TOLERANCE_S = 0.75
SOURCES = ("free_local_tts", "paid_provider")


@dataclass
class Paths:
    """Where things live; tests point these at a sandbox."""
    locks_dir: Path = sc.STORY_LOCKS_DIR
    episodes_dir: Path = ec.EPISODES_DIR
    assets_root: Path = carousel_handoff.ASSETS_ROOT
    bible: Path = BIBLE_PATH

    def script(self, story_id: str) -> Path:
        return self.episodes_dir / story_id / "reel" / SCRIPT_NAME

    def reel_dir(self, lock: dict) -> Path:
        return carousel_handoff.package_dir(int(lock["item_index"]), self.assets_root) / "reel"

    def plan_dir(self, story_id: str) -> Path:
        return self.episodes_dir / story_id / "reel"


def load_bible(paths: Paths) -> dict:
    return json.loads(paths.bible.read_text())


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def script_sha(script: dict) -> str:
    """Binds a voice file to the exact words it must say."""
    return hashlib.sha256("\n".join(l["text"].strip() for l in script.get("lines") or []).encode()).hexdigest()


# --- media probes (stdlib only) ---------------------------------------------------------------
_MP3_BITRATES = {1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],   # MPEG-1 L3
                 2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]}       # MPEG-2/2.5 L3
_MP3_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _mp3_duration(data: bytes) -> float | None:
    pos = 0
    if data[:3] == b"ID3":
        pos = 10 + ((data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F))
    total, frames = 0.0, 0
    while pos + 4 <= len(data):
        h = struct.unpack(">I", data[pos:pos + 4])[0]
        if (h >> 21) & 0x7FF != 0x7FF or ((h >> 17) & 0x3) != 1:          # sync + layer III
            pos += 1
            continue
        version = (h >> 19) & 0x3
        br_i, sr_i, pad = (h >> 12) & 0xF, (h >> 10) & 0x3, (h >> 9) & 0x1
        if version == 1 or br_i in (0, 15) or sr_i == 3:
            pos += 1
            continue
        rate = _MP3_RATES[version][sr_i]
        kbps = _MP3_BITRATES[1 if version == 3 else 2][br_i]
        samples = 1152 if version == 3 else 576
        size = (samples // 8) * kbps * 1000 // rate + pad
        if size <= 0:
            break
        total += samples / rate
        frames += 1
        pos += size
    return round(total, 3) if frames else None


def audio_duration(path: Path) -> float | None:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path)) as w:
                return round(w.getnframes() / float(w.getframerate()), 3)
        except (wave.Error, EOFError):
            return None
    if path.suffix.lower() == ".mp3":
        return _mp3_duration(path.read_bytes())
    return None


def image_size(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":                                           # JPEG: find a SOF marker
        pos = 2
        while pos + 9 < len(data):
            if data[pos] != 0xFF:
                pos += 1
                continue
            marker, length = data[pos + 1], struct.unpack(">H", data[pos + 2:pos + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[pos + 5:pos + 9])
                return w, h
            pos += 2 + length
    return None


# --- approved art ---------------------------------------------------------------------------------
def approved_slides(lock: dict, paths: Paths) -> dict[int, Path]:
    """slot -> the clean, QA-APPROVED slide image (story_continuity). Only approved
    art may appear in a Reel: the identity rules were checked on exactly these files."""
    index = int(lock["item_index"])
    state = sc.load_state(lock, index, root=paths.assets_root)
    active = sc.project_active_context(state, lock, root=paths.assets_root)
    return {r["slide_index"]: sc.candidate_path(index, r["slide_index"], paths.assets_root)
            for r in sc.story_plan(lock) if sc.approved_ref(active, r["slide_index"]) is not None}


def find_audio(lock: dict, paths: Paths) -> Path | None:
    for suffix in AUDIO_SUFFIXES:
        p = paths.reel_dir(lock) / f"{AUDIO_STEM}{suffix}"
        if p.is_file():
            return p
    return None


def load_voice_gate(paths: Paths) -> dict:
    p = paths.episodes_dir / VOICE_GATE_NAME
    return json.loads(p.read_text()) if p.is_file() else {"enabled": False}


# --- validation ---------------------------------------------------------------------------------
def _check_script(script: dict, bible: dict, lock: dict, errors: list, warnings: list) -> None:
    fmt, vocab, delivery = bible["reel_format"], bible["vocabulary"], bible["delivery"]
    dur = float(script.get("duration_s") or 0)
    lo, hi = fmt["duration_s"]["min"], fmt["duration_s"]["max"]
    if dur > hi:
        errors.append(f"script is overlong: {dur} s > {hi} s maximum")
    elif dur < lo:
        errors.append(f"script is too short: {dur} s < {lo} s minimum")
    lines = script.get("lines") or []
    if not lines:
        errors.append("script has no spoken lines")
        return
    prev_end, words, spoken = 0.0, 0, 0.0
    banned = [p.lower() for p in vocab["prohibited_phrases"]]
    full_text = " ".join(l.get("text", "") for l in lines)
    for i, line in enumerate(lines, 1):
        text, start, end = line.get("text", "").strip(), float(line.get("start_s", -1)), float(line.get("end_s", -1))
        tag = f"line {i} ({line.get('id', '?')})"
        if not text:
            errors.append(f"{tag} has no text")
            continue
        if start < prev_end - TIMING_TOLERANCE_S or end <= start:
            errors.append(f"{tag} timing {start}-{end} s overlaps or runs backwards")
        if end > dur + TIMING_TOLERANCE_S:
            errors.append(f"{tag} ends at {end} s, after the {dur} s Reel")
        prev_end = end
        for sentence in re.split(r"(?<=[.?!])\s+", text):
            if len(_words(sentence)) > vocab["max_words_per_sentence"]:
                errors.append(f"{tag}: sentence over {vocab['max_words_per_sentence']} words: {sentence!r}")
        words += len(_words(text))
        spoken += max(end - start, 0)
        _check_captions(line, tag, fmt["captions"], errors)
    low = full_text.lower()
    for phrase in banned:
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", low):
            errors.append(f"prohibited phrase in the voice script: {phrase!r}")
    if full_text.count("!") > vocab["max_exclamations_per_script"]:
        errors.append("exclamation marks are off-voice (calm, understated delivery)")
    if stages._NARRATOR_VOICE.search(full_text) or stages._ENGAGEMENT_BAIT.search(full_text):
        errors.append("third-person narration or engagement bait in the voice script")
    if re.search(r"\b[A-Z]{4,}\b", full_text):
        errors.append("ALL-CAPS words are shouting -- off-voice")
    wpm = round(words / spoken * 60, 1) if spoken else 0
    pace = delivery["pacing_wpm"]
    if not pace["min"] <= wpm <= pace["max"]:
        errors.append(f"pace {wpm} wpm is outside the voice bible's {pace['min']}-{pace['max']} wpm")
    script["_stats"] = {"words": words, "spoken_s": round(spoken, 2), "wpm": wpm}


def _check_captions(line: dict, tag: str, cap: dict, errors: list) -> None:
    chunks = line.get("captions") or []
    if not chunks:
        errors.append(f"{tag} has no captions -- every spoken word must be captioned")
        return
    start, end, prev = float(line["start_s"]), float(line["end_s"]), float(line["start_s"])
    for c in chunks:
        cs, ce, ct = float(c.get("start_s", -1)), float(c.get("end_s", -1)), c.get("text", "")
        if len(_words(ct)) > cap["max_words_per_chunk"]:
            errors.append(f"{tag} caption {ct!r} has more than {cap['max_words_per_chunk']} words")
        if ce - cs < cap["min_chunk_s"] - TIMING_TOLERANCE_S:
            errors.append(f"{tag} caption {ct!r} is on screen under {cap['min_chunk_s']} s")
        if cs < prev - TIMING_TOLERANCE_S or cs < start - TIMING_TOLERANCE_S or ce > end + TIMING_TOLERANCE_S:
            errors.append(f"{tag} caption {ct!r} ({cs}-{ce} s) falls outside its line or overlaps")
        prev = ce
    if _words(" ".join(c.get("text", "") for c in chunks)) != _words(line["text"]):
        errors.append(f"{tag} captions do not match the spoken words")


def _check_scenes(script: dict, bible: dict, lock: dict, paths: Paths, errors: list) -> list[dict]:
    fmt = bible["reel_format"]
    canvas = fmt["canvas"]
    if (canvas["width"], canvas["height"]) != (1080, 1920) or canvas["aspect_ratio"] != "9:16":
        errors.append(f"broken vertical aspect ratio: Reel canvas {canvas} is not 1080x1920 9:16")
    dur = float(script.get("duration_s") or 0)
    scenes = script.get("scenes") or []
    if not scenes:
        errors.append("script has no scenes")
        return []
    approved = approved_slides(lock, paths)
    plan_slots = {r["slide_index"] for r in sc.story_plan(lock)}
    resolved, cursor = [], 0.0
    for i, s in enumerate(scenes, 1):
        start, end, slot = float(s.get("start_s", -1)), float(s.get("end_s", -1)), s.get("slide")
        tag = f"scene {i} (slide {slot})"
        if abs(start - cursor) > TIMING_TOLERANCE_S or end <= start:
            errors.append(f"{tag} {start}-{end} s leaves a gap or overlap (expected start {cursor} s)")
        cursor = end
        motion, zoom = s.get("motion"), float(s.get("zoom", 1.04))
        if motion not in fmt["motion"]["allowed"]:
            errors.append(f"{tag} motion {motion!r} not in {fmt['motion']['allowed']}")
        if not 1.0 <= zoom <= fmt["motion"]["max_zoom"]:
            errors.append(f"{tag} zoom {zoom} exceeds the subtle-motion limit {fmt['motion']['max_zoom']}")
        if slot not in plan_slots:
            errors.append(f"{tag} is not a slide of {lock['story_id']}")
            continue
        img = approved.get(slot)
        if img is None:
            errors.append(f"{tag} has no QA-approved image yet -- Reels use approved art only")
            continue
        if not img.is_file():
            errors.append(f"{tag} approved image missing on disk: {img}")
            continue
        size = image_size(img)
        if size is None:
            errors.append(f"{tag} image size unreadable: {img.name}")
            continue
        w, h = size
        if h <= w:
            errors.append(f"broken vertical aspect ratio: {tag} source is {w}x{h} (not portrait)")
            continue
        resolved.append({**s, "image": str(img), "width": w, "height": h,
                         "duration_s": round(end - start, 3)})
    if abs(cursor - dur) > TIMING_TOLERANCE_S:
        errors.append(f"scenes end at {cursor} s but the Reel is {dur} s")
    return resolved


def _check_audio(script: dict, bible: dict, lock: dict, paths: Paths, errors: list, warnings: list) -> dict:
    audio = find_audio(lock, paths)
    if audio is None:
        errors.append(f"missing audio: supply {paths.reel_dir(lock) / (AUDIO_STEM + '.wav')} (or .mp3)")
        return {"release_eligible": False}
    length = audio_duration(audio)
    if length is None:
        errors.append(f"audio unreadable as WAV/MP3: {audio.name}")
        return {"release_eligible": False}
    dur = float(script.get("duration_s") or 0)
    if length > bible["reel_format"]["duration_s"]["max"] + AUDIO_MATCH_TOLERANCE_S:
        errors.append(f"audio is overlong: {length} s")
    if abs(length - dur) > AUDIO_MATCH_TOLERANCE_S:
        errors.append(f"audio is {length} s but the script is timed for {dur} s "
                      f"(re-time the script or re-record; tolerance {AUDIO_MATCH_TOLERANCE_S} s)")
    prov_path = audio.with_name(f"{AUDIO_STEM}.provenance.json")
    prov = json.loads(prov_path.read_text()) if prov_path.is_file() else None
    info = {"path": str(audio), "duration_s": length, "release_eligible": False}
    if not prov:
        errors.append("audio has no provenance -- run: python3 nyx_reels.py audio-provenance ...")
        return info
    if prov.get("script_sha256") != script_sha(script):
        errors.append("audio provenance is for a different version of the script -- re-record")
    if prov.get("voice_origin") != "original_synthetic" or prov.get("cloned_from_real_person") is not False:
        errors.append("voice must be original synthetic and not cloned from a real person (voice bible)")
    if prov.get("source") not in SOURCES:
        errors.append(f"audio source must be one of {SOURCES}")
    if prov.get("source") == "paid_provider":
        gate = load_voice_gate(paths)
        cap = float(gate.get("cap_usd_per_episode") or 0)
        if not (gate.get("enabled") is True and str(gate.get("enabled_by") or "").strip()):
            errors.append("paid voice audio supplied but episodes/voice_generation.json is not switched on")
        elif prov.get("provider") != gate.get("provider"):
            errors.append(f"paid voice provider {prov.get('provider')!r} is not the approved {gate.get('provider')!r}")
        elif float(prov.get("cost_usd", 1e9)) > cap:
            errors.append(f"paid voice cost US${prov.get('cost_usd')} exceeds the US${cap} episode cap")
    locked = bible.get("production_voice") or {}
    # A free local / generic stock TTS voice is a TEST voice by definition: it can never
    # be Nyx's production voice, whatever production_voice says.
    production = (prov.get("source") == "paid_provider" and locked.get("locked") is True
                  and prov.get("provider") == locked.get("provider")
                  and prov.get("voice") == locked.get("voice"))
    info.update(source=prov.get("source"), tool=prov.get("tool") or prov.get("provider"),
                voice=prov.get("voice"), release_eligible=production)
    if not production:
        warnings.append("TEST voice: not the founder's locked production voice -- the render is marked "
                        "TEST and is not release-eligible")
    return info


def _check_music(script: dict, bible: dict, paths: Paths, errors: list, warnings: list) -> dict | None:
    music = script.get("music")
    if not music:
        warnings.append("no music bed: renders voice-only")
        return None
    path = Path(music.get("path", ""))
    path = path if path.is_absolute() else ROOT / path
    if not path.is_file():
        errors.append(f"music file missing: {music.get('path')}")
    if not str(music.get("license") or "").strip():
        errors.append("music has no licence recorded")
    gain = float(music.get("gain_db", bible["reel_format"]["music"]["bed_gain_db"]))
    if gain > -12:
        errors.append(f"music bed at {gain} dB is too loud to sit under the voice (max -12 dB)")
    return {"path": str(path), "gain_db": gain, "license": music.get("license")}


def validate(story_id: str, paths: Paths | None = None) -> dict:
    paths = paths or Paths()
    errors, warnings = [], []
    bible = load_bible(paths)
    lock = ec.load_lock(story_id, paths.locks_dir)
    script_path = paths.script(story_id)
    if not script_path.is_file():
        return {"status": "BLOCKED", "errors": [f"no voiceover script at {script_path}"], "warnings": []}
    script = json.loads(script_path.read_text())
    if script.get("story_id") != story_id:
        errors.append(f"script is for {script.get('story_id')!r}, not {story_id!r}")
    if script.get("voice_bible_version") != bible["spec_version"]:
        errors.append(f"script targets voice bible {script.get('voice_bible_version')!r}, current is "
                      f"{bible['spec_version']!r}")
    _check_script(script, bible, lock, errors, warnings)
    scenes = _check_scenes(script, bible, lock, paths, errors)
    audio = _check_audio(script, bible, lock, paths, errors, warnings)
    music = _check_music(script, bible, paths, errors, warnings)
    return {"status": "BLOCKED" if errors else "READY", "story_id": story_id, "errors": errors,
            "warnings": warnings, "stats": script.get("_stats"), "audio": audio, "music": music,
            "scenes": scenes, "script": script, "release_eligible": bool(audio.get("release_eligible")),
            "lock": lock}


# --- render plan --------------------------------------------------------------------------------
def _ts(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


TEST_LABEL = "TEST - generic voice - not for release"


def captions_ass(script: dict, bible: dict, *, test: bool = False) -> str:
    canvas = bible["reel_format"]["canvas"]
    margin_v = int(canvas["height"] * 0.28)              # text sits above 72% of the height
    head = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {canvas['width']}",
            f"PlayResY: {canvas['height']}", "WrapStyle: 0", "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV",
            f"Style: Nyx,DejaVu Sans,66,&H00FFFFFF,&H00180C12,&H64000000,1,1,4,1,2,90,90,{margin_v}",
            "Style: Test,DejaVu Sans,34,&H0000D7FF,&H00000000,&H64000000,1,1,2,0,9,40,40,300", "",
            "[Events]", "Format: Layer, Start, End, Style, Text"]
    rows = []
    if test:          # burned in for the whole Reel: a TEST render can never pass for production
        rows.append(f"Dialogue: 1,{_ts(0)},{_ts(float(script['duration_s']))},Test,{TEST_LABEL}")
    for line in script["lines"]:
        for c in line["captions"]:
            text = c["text"].replace("\\", "").replace("{", "(").replace("}", ")")
            rows.append(f"Dialogue: 0,{_ts(float(c['start_s']))},{_ts(float(c['end_s']))},Nyx,{text}")
    return "\n".join(head + rows) + "\n"


def _motion(scene: dict, frames: int) -> tuple[str, str, str]:
    z, n = float(scene.get("zoom", 1.04)), max(frames - 1, 1)
    centre_x, centre_y = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    m = scene["motion"]
    if m == "push_in":
        return f"1+{z - 1:.4f}*on/{n}", centre_x, centre_y
    if m == "push_out":
        return f"{z:.4f}-{z - 1:.4f}*on/{n}", centre_x, centre_y
    if m == "pan_left":
        return f"{z:.4f}", f"(iw-iw/zoom)*(1-on/{n})", centre_y
    if m == "pan_right":
        return f"{z:.4f}", f"(iw-iw/zoom)*on/{n}", centre_y
    return "1", "0", "0"                                  # hold


def build_plan(result: dict, paths: Paths) -> dict:
    """Pure: the ffmpeg command that would render this Reel. Nothing runs here."""
    bible = load_bible(paths)
    fmt = bible["reel_format"]
    W, H, fps = fmt["canvas"]["width"], fmt["canvas"]["height"], fmt["fps"]
    script, scenes, lock = result["script"], result["scenes"], result["lock"]
    out_dir = paths.plan_dir(result["story_id"])
    ass_path = out_dir / "captions.ass"
    suffix = "" if result["release_eligible"] else ".TEST"
    output = paths.reel_dir(lock) / f"{result['story_id']}_reel{suffix}.mp4"
    args, chains = ["ffmpeg", "-y", "-loglevel", "error"], []
    for i, s in enumerate(scenes):
        args += ["-loop", "1", "-framerate", str(fps), "-t", f"{s['duration_s']:.3f}", "-i", s["image"]]
        fg_h = int(round(W * s["height"] / s["width"] / 2) * 2)
        frames = int(round(s["duration_s"] * fps))
        z, x, y = _motion(s, frames)
        chains.append(
            f"[{i}:v]scale={W}:{fg_h},setsar=1,split=2[fg{i}][bs{i}];"
            f"[bs{i}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=30,"
            f"eq=brightness=-0.08[bg{i}];"
            f"[fg{i}]zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{fg_h}:fps={fps}[fz{i}];"
            f"[bg{i}][fz{i}]overlay=(W-w)/2:(H-h)/2:shortest=1,trim=duration={s['duration_s']:.3f},"
            f"setpts=PTS-STARTPTS,format=yuv420p[v{i}]")
    n = len(scenes)
    chains.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vc]")
    chains.append(f"[vc]subtitles='{ass_path}'[vout]")
    args += ["-i", result["audio"]["path"]]
    music = result.get("music")
    if music:
        args += ["-stream_loop", "-1", "-i", music["path"]]
        chains.append(f"[{n}:a]aresample=48000,asplit=2[vomix][vosc];"
                      f"[{n + 1}:a]aresample=48000,volume={music['gain_db']}dB[mus];"
                      f"[mus][vosc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[duck];"
                      f"[vomix][duck]amix=inputs=2:duration=first:normalize=0[aout]")
    else:
        chains.append(f"[{n}:a]aresample=48000[aout]")
    dur = float(script["duration_s"])
    args += ["-filter_complex", ";".join(chains), "-map", "[vout]", "-map", "[aout]",
             "-t", f"{dur:.3f}", "-r", str(fps), "-s", f"{W}x{H}",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output)]
    return {"story_id": result["story_id"], "output": str(output), "captions_file": str(ass_path),
            "canvas": f"{W}x{H}", "duration_s": dur, "release_eligible": result["release_eligible"],
            "test_render": not result["release_eligible"], "ffmpeg_args": args,
            "scenes": [{k: s[k] for k in ("slide", "start_s", "end_s", "motion", "image")} for s in scenes],
            "audio": result["audio"], "music": music, "warnings": result["warnings"],
            "note": "plan only -- nothing was executed; `render --execute` runs local ffmpeg (zero cost)"}


def plan(story_id: str, paths: Paths | None = None, *, write: bool = True) -> dict:
    paths = paths or Paths()
    result = validate(story_id, paths)
    if result["status"] != "READY":
        return {k: result[k] for k in ("status", "errors", "warnings") if k in result}
    p = build_plan(result, paths)
    if write:
        out = paths.plan_dir(story_id)
        out.mkdir(parents=True, exist_ok=True)
        (out / "captions.ass").write_text(captions_ass(result["script"], load_bible(paths),
                                                       test=not result["release_eligible"]))
        (out / "render_plan.json").write_text(json.dumps(p, indent=2) + "\n")
    return {"status": "PLANNED", **p}


def render(story_id: str, paths: Paths | None = None, *, execute: bool = False, run=subprocess.run) -> dict:
    planned = plan(story_id, paths)
    if planned["status"] != "PLANNED":
        return planned
    if not execute:
        return {**planned, "status": "PLANNED", "executed": False}
    if not shutil.which(planned["ffmpeg_args"][0]):
        return {**planned, "status": "BLOCKED", "executed": False,
                "errors": ["ffmpeg is not installed (brew install ffmpeg) -- local, free"]}
    Path(planned["output"]).parent.mkdir(parents=True, exist_ok=True)
    proc = run(planned["ffmpeg_args"], capture_output=True, text=True)
    if proc.returncode != 0:
        return {**planned, "status": "RENDER_FAILED", "executed": True, "errors": [proc.stderr[-400:]]}
    return {**planned, "status": "RENDERED", "executed": True}


# --- bookkeeping commands -----------------------------------------------------------------------
def script_text(story_id: str, paths: Paths | None = None, *, say: bool = False) -> str:
    """The lines to hand to a TTS tool. With say=True, macOS `say` silence
    markers ([[slnc ms]]) reproduce the script's lead-in, gaps and tail, so a
    free test recording lands close to the scripted timing."""
    paths = paths or Paths()
    script = json.loads(paths.script(story_id).read_text())
    lines = script["lines"]
    if not say:
        return "\n".join(l["text"].strip() for l in lines) + "\n"
    out, cursor = [], 0.0
    for line in lines:
        gap = int(round((float(line["start_s"]) - cursor) * 1000))
        out.append((f"[[slnc {gap}]] " if gap > 0 else "") + line["text"].strip())
        cursor = float(line["end_s"])
    tail = int(round((float(script["duration_s"]) - cursor) * 1000))
    return " ".join(out) + (f" [[slnc {tail}]]" if tail > 0 else "") + "\n"


def write_audio_provenance(story_id: str, *, source: str, tool: str, voice: str, cost_usd: float = 0.0,
                           provider: str | None = None, paths: Paths | None = None) -> dict:
    """Records who/what made the supplied voice file. Writes a JSON sidecar only;
    generates nothing."""
    paths = paths or Paths()
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    lock = ec.load_lock(story_id, paths.locks_dir)
    audio = find_audio(lock, paths)
    if audio is None:
        raise FileNotFoundError(f"put the voice file at {paths.reel_dir(lock) / (AUDIO_STEM + '.wav')} first")
    script = json.loads(paths.script(story_id).read_text())
    prov = {"story_id": story_id, "source": source, "tool": tool, "voice": voice,
            "provider": provider, "cost_usd": cost_usd, "voice_origin": "original_synthetic",
            "cloned_from_real_person": False, "voice_bible_version": load_bible(paths)["spec_version"],
            "script_sha256": script_sha(script),
            "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest()}
    audio.with_name(f"{AUDIO_STEM}.provenance.json").write_text(json.dumps(prov, indent=2) + "\n")
    return prov


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd, story, rest = argv[0], argv[1], argv[2:]

    def opt(name, default=""):
        return rest[rest.index(name) + 1] if name in rest and rest.index(name) + 1 < len(rest) else default
    if cmd == "script-text":
        print(script_text(story, say="--say" in rest), end="")
        return 0
    if cmd == "audio-provenance":
        out = write_audio_provenance(story, source=opt("--source"), tool=opt("--tool"), voice=opt("--voice"),
                                     cost_usd=float(opt("--cost-usd", "0")), provider=opt("--provider") or None)
    elif cmd == "validate":
        r = validate(story)
        out = {k: r.get(k) for k in ("status", "errors", "warnings", "stats", "release_eligible")}
    elif cmd == "plan":
        out = plan(story)
    elif cmd == "render":
        out = render(story, execute="--execute" in rest)
    else:
        print(__doc__)
        return 1
    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("status") not in ("BLOCKED", "RENDER_FAILED") else 2


if __name__ == "__main__":
    raise SystemExit(main())
