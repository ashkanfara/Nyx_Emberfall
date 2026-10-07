"""Tests for nyx_reels.py. Everything runs in a temp sandbox: placeholder
portrait PNGs stand in for approved slides and the "voice" is a SILENT WAV
written with the stdlib wave module (a timing fixture -- no speech, no TTS,
no API). ffmpeg is never executed."""

import ast
import copy
import json
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import nyx_reels as nr
import nyx_runner

ROOT = Path(__file__).resolve().parent
REAL_SCRIPT = ROOT / "episodes" / "s1e02_public" / "reel" / nr.SCRIPT_NAME


def silent_wav(path: Path, seconds: float, rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reels_test_"))
        locks = self.tmp / "locks"
        locks.mkdir()
        shutil.copy(ROOT / "brand/story_locks/s1e02_public.json", locks)
        bible = self.tmp / "voice_bible.json"
        shutil.copy(nr.BIBLE_PATH, bible)
        self.paths = nr.Paths(locks_dir=locks, episodes_dir=self.tmp / "episodes",
                              assets_root=self.tmp / "assets", bible=bible)
        self.script = json.loads(REAL_SCRIPT.read_text())
        self.write_script()
        self.slides = {}
        for n in range(1, 7):
            p = self.tmp / "assets/carousel_item33" / f"item33_slide{n}.png"
            nyx_runner._pattern_png(p, 1080, 1350, n)
            self.slides[n] = p
        patcher = mock.patch.object(nr, "approved_slides", side_effect=lambda lock, paths: dict(self.slides))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_script(self, script=None):
        p = self.paths.script("s1e02_public")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(script or self.script))

    def lock(self):
        return nr.ec.load_lock("s1e02_public", self.paths.locks_dir)

    def supply_voice(self, seconds=23.5, **prov):
        silent_wav(self.paths.reel_dir(self.lock()) / "voiceover.wav", seconds)
        args = {"source": "free_local_tts", "tool": "macOS say", "voice": "generic test voice"} | prov
        return nr.write_audio_provenance("s1e02_public", paths=self.paths, **args)

    def validate(self):
        return nr.validate("s1e02_public", self.paths)

    def errors(self):
        return " | ".join(self.validate()["errors"])


class RealEpisodeScript(unittest.TestCase):
    def test_s1e02_script_passes_every_text_rule_and_only_lacks_assets(self):
        r = nr.validate("s1e02_public")
        self.assertEqual(r["status"], "BLOCKED")
        for e in r["errors"]:
            self.assertTrue("QA-approved" in e or "missing audio" in e, e)
        self.assertTrue(120 <= r["stats"]["wpm"] <= 165)

    def test_voice_bible_is_consistent_with_the_identity_spec(self):
        bible = json.loads(nr.BIBLE_PATH.read_text())
        spec = json.loads((ROOT / "brand/nyx_identity/identity_spec.json").read_text())
        self.assertIn(str(spec["character"]["minimum_age"]), bible["character_link"]["age_impression"])
        self.assertEqual(bible["character_link"]["signature_line"], spec["character"]["signature_line"])
        self.assertEqual(bible["origin"]["required"], "original_synthetic")
        self.assertFalse(bible["production_voice"]["locked"])

    def test_paid_voice_gate_ships_off(self):
        gate = json.loads((ROOT / "episodes" / nr.VOICE_GATE_NAME).read_text())
        self.assertFalse(gate["enabled"])
        self.assertIsNone(gate["provider"])


class ReadyPath(_Sandbox):
    def test_free_test_voice_makes_a_test_render_plan(self):
        self.supply_voice()
        r = self.validate()
        self.assertEqual(r["status"], "READY", r["errors"])
        self.assertFalse(r["release_eligible"])
        self.assertTrue(any("TEST voice" in w for w in r["warnings"]))
        p = nr.plan("s1e02_public", self.paths)
        self.assertEqual(p["status"], "PLANNED")
        self.assertEqual(p["canvas"], "1080x1920")
        self.assertTrue(p["output"].endswith("s1e02_public_reel.TEST.mp4"))
        args = p["ffmpeg_args"]
        self.assertEqual(args.count("-loop"), 6)
        graph = args[args.index("-filter_complex") + 1]
        self.assertEqual(graph.count("zoompan="), 6)
        self.assertIn("concat=n=6", graph)
        self.assertIn("subtitles=", graph)
        self.assertEqual(args[args.index("-s") + 1], "1080x1920")
        ass = (self.paths.plan_dir("s1e02_public") / "captions.ass").read_text()
        chunks = sum(len(l["captions"]) for l in self.script["lines"])
        self.assertEqual(ass.count(",Nyx,"), chunks)
        self.assertIn(f"Dialogue: 1,0:00:00.00,0:00:23.50,Test,{nr.TEST_LABEL}", ass)   # burned-in TEST label
        self.assertIn(",537\n", ass)                          # MarginV: captions above 72% height

    def test_music_is_ducked_under_the_voice(self):
        music = self.tmp / "bed.wav"
        silent_wav(music, 30)
        self.script["music"] = {"path": str(music), "license": "test fixture", "gain_db": -20}
        self.write_script()
        self.supply_voice()
        graph = nr.plan("s1e02_public", self.paths)["ffmpeg_args"]
        graph = graph[graph.index("-filter_complex") + 1]
        self.assertIn("volume=-20.0dB", graph)
        self.assertIn("sidechaincompress", graph)
        self.assertIn("amix=inputs=2:duration=first", graph)

    def test_locked_production_voice_is_release_eligible(self):
        bible = json.loads(self.paths.bible.read_text())
        bible["production_voice"].update(locked=True, provider="example_tts", voice="nyx-original-1")
        self.paths.bible.write_text(json.dumps(bible))
        gate = {"enabled": True, "enabled_by": "founder test", "provider": "example_tts",
                "cap_usd_per_episode": 1.0}
        self.paths.episodes_dir.mkdir(parents=True, exist_ok=True)
        (self.paths.episodes_dir / nr.VOICE_GATE_NAME).write_text(json.dumps(gate))
        self.supply_voice(source="paid_provider", provider="example_tts", voice="nyx-original-1",
                          tool="example_tts", cost_usd=0.4)
        r = self.validate()
        self.assertEqual(r["status"], "READY", r["errors"])
        self.assertTrue(r["release_eligible"])
        self.assertTrue(nr.plan("s1e02_public", self.paths)["output"].endswith("s1e02_public_reel.mp4"))
        self.assertNotIn(nr.TEST_LABEL, (self.paths.plan_dir("s1e02_public") / "captions.ass").read_text())

    def test_free_voice_is_never_production_even_if_locked(self):
        bible = json.loads(self.paths.bible.read_text())
        bible["production_voice"].update(locked=True, provider=None, voice="generic test voice")
        self.paths.bible.write_text(json.dumps(bible))
        self.supply_voice()
        r = self.validate()
        self.assertEqual(r["status"], "READY", r["errors"])
        self.assertFalse(r["release_eligible"])
        p = nr.plan("s1e02_public", self.paths)
        self.assertTrue(p["output"].endswith(".TEST.mp4"))
        self.assertIn(nr.TEST_LABEL, (self.paths.plan_dir("s1e02_public") / "captions.ass").read_text())

    def test_render_never_runs_without_execute(self):
        self.supply_voice()
        run = mock.Mock()
        out = nr.render("s1e02_public", self.paths, run=run)
        self.assertFalse(out["executed"])
        run.assert_not_called()

    def test_render_with_execute_needs_local_ffmpeg(self):
        self.supply_voice()
        run = mock.Mock(return_value=mock.Mock(returncode=0))
        with mock.patch.object(nr.shutil, "which", return_value=None):
            self.assertEqual(nr.render("s1e02_public", self.paths, execute=True, run=run)["status"], "BLOCKED")
        run.assert_not_called()
        with mock.patch.object(nr.shutil, "which", return_value="/usr/bin/ffmpeg"):
            out = nr.render("s1e02_public", self.paths, execute=True, run=run)
        self.assertEqual(out["status"], "RENDERED")
        self.assertEqual(run.call_args[0][0][0], "ffmpeg")


class Blocks(_Sandbox):
    def test_missing_audio(self):
        self.assertIn("missing audio", self.errors())

    def test_overlong_and_too_short_scripts(self):
        long = copy.deepcopy(self.script)
        long["duration_s"] = 26.0
        long["scenes"][-1]["end_s"] = 26.0
        self.write_script(long)
        self.assertIn("overlong", self.errors())
        short = copy.deepcopy(self.script)
        short["duration_s"] = 12.0
        self.write_script(short)
        self.assertIn("too short", self.errors())

    def test_overlong_audio(self):
        self.supply_voice(seconds=27)
        self.assertIn("audio is overlong", self.errors())

    def test_audio_must_match_script_timing_and_version(self):
        self.supply_voice(seconds=19)
        self.assertIn("timed for 23.5 s", self.errors())
        self.supply_voice()
        self.script["lines"][0]["text"] = "someone pushed this under my door again."
        self.script["lines"][0]["captions"][1]["text"] = "under my door again."
        self.write_script()
        self.assertIn("different version of the script", self.errors())

    def test_missing_or_mismatched_captions(self):
        s = copy.deepcopy(self.script)
        s["lines"][2]["captions"] = []
        self.write_script(s)
        self.assertIn("has no captions", self.errors())
        s = copy.deepcopy(self.script)
        s["lines"][2]["captions"][2]["text"] = "I checked."
        self.write_script(s)
        self.assertIn("captions do not match the spoken words", self.errors())
        s = copy.deepcopy(self.script)
        s["lines"][0]["captions"] = [{"text": "someone pushed this under my door tonight.",
                                      "start_s": 0.4, "end_s": 3.4}]
        self.write_script(s)
        self.assertIn("more than 6 words", self.errors())

    def test_broken_vertical_aspect(self):
        nyx_runner._pattern_png(self.slides[3], 1350, 1080, 3)       # landscape
        self.assertIn("broken vertical aspect ratio: scene 3", self.errors())
        bible = json.loads(self.paths.bible.read_text())
        bible["reel_format"]["canvas"].update(width=1080, height=1350, aspect_ratio="4:5")
        self.paths.bible.write_text(json.dumps(bible))
        self.assertIn("Reel canvas", self.errors())

    def test_unapproved_art_is_never_used(self):
        del self.slides[4]
        self.assertIn("scene 4 (slide 4) has no QA-approved image", self.errors())

    def test_scene_gap_and_motion_limits(self):
        s = copy.deepcopy(self.script)
        s["scenes"][2]["start_s"] = 7.2
        s["scenes"][3]["motion"] = "spin"
        s["scenes"][4]["zoom"] = 1.3
        self.write_script(s)
        e = self.errors()
        self.assertIn("gap or overlap", e)
        self.assertIn("motion 'spin'", e)
        self.assertIn("subtle-motion limit", e)

    def test_off_voice_text(self):
        for bad, why in (("follow for more, link in bio.", "prohibited phrase"),
                         ("okay that is wild!", "exclamation"),
                         ("she slowly reaches for the door.", "narration"),
                         ("I SWEAR it moved.", "ALL-CAPS")):
            with self.subTest(bad=bad):
                s = copy.deepcopy(self.script)
                s["lines"][1]["text"] = bad
                s["lines"][1]["captions"] = [{"text": bad, "start_s": 3.8, "end_s": 6.6}] \
                    if len(bad.split()) <= 6 else [{"text": " ".join(bad.split()[:3]), "start_s": 3.8, "end_s": 5.2},
                                                   {"text": " ".join(bad.split()[3:]), "start_s": 5.2, "end_s": 6.6}]
                self.write_script(s)
                self.assertIn(why, self.errors())

    def test_pace_outside_the_bible(self):
        s = copy.deepcopy(self.script)
        for line in s["lines"]:
            line["end_s"] = round(line["start_s"] + 1.5, 2)
            for c in line["captions"]:
                c["start_s"], c["end_s"] = line["start_s"], line["end_s"]
        self.write_script(s)
        self.assertIn("outside the voice bible", self.errors())

    def test_cloned_or_unapproved_paid_voice(self):
        prov = self.supply_voice()
        path = self.paths.reel_dir(self.lock()) / "voiceover.provenance.json"
        path.write_text(json.dumps({**prov, "cloned_from_real_person": True}))
        self.assertIn("not cloned from a real person", self.errors())
        self.supply_voice(source="paid_provider", provider="example_tts", tool="example_tts", cost_usd=0.3)
        self.assertIn("voice_generation.json is not switched on", self.errors())

    def test_music_needs_a_licence_and_must_sit_low(self):
        music = self.tmp / "bed.wav"
        silent_wav(music, 30)
        self.script["music"] = {"path": str(music), "license": "", "gain_db": -6}
        self.write_script()
        e = self.errors()
        self.assertIn("no licence", e)
        self.assertIn("too loud", e)


class ScriptText(_Sandbox):
    def test_say_markup_reproduces_the_script_pauses(self):
        text = nr.script_text("s1e02_public", self.paths, say=True)
        self.assertTrue(text.startswith("[[slnc 400]] someone pushed this"))
        self.assertIn("tonight. [[slnc 400]] no knock.", text)
        self.assertTrue(text.rstrip().endswith("[[slnc 900]]"))


class Probes(unittest.TestCase):
    def test_mp3_duration_from_frame_headers(self):
        frame = struct.pack(">I", 0xFFFB9064) + b"\x00" * 413          # MPEG-1 L3, 128 kbps, 44.1 kHz
        tmp = Path(tempfile.mkdtemp())
        try:
            p = tmp / "t.mp3"
            p.write_bytes(frame * 383)
            self.assertAlmostEqual(nr.audio_duration(p), 383 * 1152 / 44100, places=2)
        finally:
            shutil.rmtree(tmp)


class Boundary(unittest.TestCase):
    def test_no_network_provider_or_publisher_imports(self):
        tree = ast.parse((ROOT / "nyx_reels.py").read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        self.assertEqual(names - {"__future__", "hashlib", "json", "re", "shutil", "struct", "subprocess",
                                  "sys", "wave", "dataclasses", "pathlib", "carousel_handoff",
                                  "episode_coordinator", "stages", "story_continuity"}, set())

    def test_trial_switches_untouched(self):
        trial = json.loads((ROOT / "episodes/automation_trial.json").read_text())
        self.assertFalse(trial["auto_publish"]["enabled"])
        self.assertEqual(json.loads((ROOT / "episodes/generation_executor.json").read_text())["mode"],
                         "human_manual")


if __name__ == "__main__":
    unittest.main()
