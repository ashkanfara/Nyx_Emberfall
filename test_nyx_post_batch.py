"""Tests for nyx_post_batch: the plan gate, the continuity ledger, the crop
rules, the Reel's cause-and-effect order, and that nothing is built from a
failing plan. No image, audio or network is used."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import nyx_post_batch as pb
import nyx_reels
import story_continuity as sc

PLAN = pb.load_plan()
BIBLE = nyx_reels.load_bible(nyx_reels.Paths(pb.ROOT))


def plan():
    return copy.deepcopy(PLAN)


def pkg(p, pid):
    return next(x for x in p["packages"] if x["id"] == pid)


def master(p, pid, mid):
    return next(m for m in pkg(p, pid)["masters"] if m["id"] == mid)


def problems(p):
    return pb.check_plan(p, BIBLE)


def has(probs, text):
    return any(text in x for x in probs)


class RealBatch(unittest.TestCase):
    def test_tracked_plan_passes(self):
        self.assertEqual(problems(plan()), [])

    def test_build_produces_every_format_per_package(self):
        files = pb.build(plan(), bible=BIBLE)
        for p in PLAN["packages"]:
            d = p["id"]
            carousel = files[f"{d}/carousel.json"]
            self.assertEqual(len(carousel["frames"]), 4)
            for f in carousel["frames"]:
                x0, y0, x1, y1 = f["crop_box"]
                self.assertEqual((x0, x1 - x0, y1 - y0), (0, 1080, 1350))
            self.assertEqual(len(files[f"{d}/story.json"]["frames"]), 3)
            script = files[f"{d}/reel/voiceover_script.json"]
            self.assertTrue(18 <= script["duration_s"] <= 25)
            self.assertEqual(sc.plan_problems(files[f"{d}/masters_lock.DRAFT.json"]), [])
            self.assertEqual(len(files[f"{d}/prompts.json"]["masters"]), len(p["masters"]))
        self.assertIn("BATCH.md", files)

    def test_written_assets_match_a_fresh_build(self):
        files = pb.build(plan(), bible=BIBLE)
        for rel, body in files.items():
            on_disk = (pb.DEFAULT_BATCH / rel).read_text()
            fresh = body if isinstance(body, str) else json.dumps(body, indent=2, ensure_ascii=False) + "\n"
            self.assertEqual(on_disk, fresh, f"{rel} is stale: rerun build --write")

    def test_mini_season_never_resolves(self):
        p5 = pkg(PLAN, "deliveries_p5")
        self.assertEqual(master(PLAN, "deliveries_p5", "M6")["props_visible"]["envelope_3"], "unopened, on the shelf")
        self.assertIn("sealed", p5["open_loop"])


class PlanGate(unittest.TestCase):
    def test_build_refuses_a_failing_plan_and_writes_nothing(self):
        p = plan()
        p["packages"].pop()
        with self.assertRaises(pb.PlanError):
            pb.build(p, bible=BIBLE)

    def test_hooks_and_open_loops_must_be_distinct(self):
        p = plan()
        p["packages"][1]["open_loop"] = p["packages"][0]["open_loop"]
        self.assertTrue(has(problems(p), "packages share a open_loop"))

    def test_start_state_must_follow_the_anchor_episode(self):
        p = plan()
        p["initial_state"]["envelope_2"] = "opened, on the shelf"
        self.assertTrue(has(problems(p), "contradicts the anchor episode"))


class Continuity(unittest.TestCase):
    def test_unexplained_prop_change_is_caught(self):
        p = plan()
        master(p, "deliveries_p1", "M6")["props_visible"]["charm"] = "in her hand"
        self.assertTrue(has(problems(p), "unexplained prop change"))

    def test_state_carries_across_packages(self):
        p = plan()
        # p2 never moves the star anise; showing it anywhere but the shelf breaks p1's ending.
        master(p, "deliveries_p2", "M2")["props_visible"]["star_anise"] = "in her open palm"
        self.assertTrue(has(problems(p), "star_anise shown as 'in her open palm'"))

    def test_prop_from_nowhere_is_caught(self):
        p = plan()
        master(p, "deliveries_p2", "M3")["events"]["envelope_3"] = "unopened, in her hands"
        self.assertTrue(has(problems(p), "unexplained prop"))

    def test_object_on_screen_before_it_is_named(self):
        p = plan()
        master(p, "deliveries_p1", "M3")["props_visible"]["star_anise"] = "in her open palm"
        self.assertTrue(has(problems(p), "star_anise is on screen (M3) before it is named"))

    def test_named_object_must_be_on_screen_in_that_beat(self):
        p = plan()
        del master(p, "deliveries_p1", "M2")["props_visible"]["envelope_2"]
        self.assertTrue(has(problems(p), "names envelope_2 but M2 does not show it"))

    def test_wardrobe_and_identity_drift_words_are_caught(self):
        p = plan()
        master(p, "deliveries_p4", "M1")["action"] += ", silver hoop earrings catching the lamp"
        master(p, "deliveries_p4", "M6")["composition"] += ", soft morning light"
        probs = problems(p)
        self.assertTrue(has(probs, "('earrings')"))
        self.assertTrue(has(probs, "('morning')"))

    def test_mismatched_lock_hardware_is_caught(self):
        p = plan()
        master(p, "deliveries_p3", "M3")["action"] = "her hand on a brass lever with a security chain"
        probs = problems(p)
        self.assertTrue(has(probs, "('lever')"))
        self.assertTrue(has(probs, "('chain')"))

    def test_face_in_frame_requires_both_fox_ears(self):
        p = plan()
        master(p, "deliveries_p2", "M5")["ears_tail"] = "hair down"
        self.assertTrue(has(problems(p), "both fox ears must be too"))

    def test_repeated_camera_across_the_batch_is_caught(self):
        p = plan()
        master(p, "deliveries_p5", "M6")["camera"] = dict(master(p, "deliveries_p1", "M6")["camera"])
        self.assertTrue(has(problems(p), "repeats the camera of deliveries_p1 M6"))


class Crops(unittest.TestCase):
    def test_horizontal_crop_or_zoom_is_refused(self):
        p = plan()
        p["carousel_crop"].update(width=864, height=1080)
        self.assertTrue(has(problems(p), "no zoom, no horizontal crop"))

    def test_crop_must_keep_the_subject(self):
        p = plan()
        pkg(p, "deliveries_p3")["carousel"][0]["crop_top"] = 570
        self.assertTrue(has(problems(p), "is cut by the 4:5 crop"))

    def test_crop_cannot_leave_the_master(self):
        p = plan()
        pkg(p, "deliveries_p1")["carousel"][1]["crop_top"] = 600
        self.assertTrue(has(problems(p), "leaves the 9:16 master"))

    def test_story_subject_must_clear_the_ui(self):
        p = plan()
        master(p, "deliveries_p1", "M1")["subject_band"] = [520, 1700]
        self.assertTrue(has(problems(p), "runs under the Story UI"))

    def test_carousel_needs_four_frames(self):
        p = plan()
        pkg(p, "deliveries_p2")["carousel"].pop(1)
        self.assertTrue(has(problems(p), "exactly 4 frames"))

    def test_crop_tool_cuts_true_4x5_from_9x16(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            masters = t / "masters"
            masters.mkdir()
            for f in json.loads((pb.DEFAULT_BATCH / "deliveries_p1" / "carousel.json").read_text())["frames"]:
                Image.new("RGB", (1080, 1920), (20, 20, 40)).save(masters / f"{f['master']}.png")
            out = pb.crop(pb.DEFAULT_BATCH / "deliveries_p1", masters, t / "out")
            self.assertEqual(len(out), 4)
            for o in out:
                with Image.open(o) as im:
                    self.assertEqual(im.size, (1080, 1350))
            Image.new("RGB", (1024, 1536)).save(masters / "M2.png")
            with self.assertRaises(pb.PlanError):
                pb.crop(pb.DEFAULT_BATCH / "deliveries_p1", masters, t / "out2")


class Reel(unittest.TestCase):
    def test_reel_must_establish_first(self):
        p = plan()
        r = pkg(p, "deliveries_p1")["reel"]
        r[0]["role"] = "OBJECT"
        self.assertTrue(has(problems(p), "must open by establishing the room"))

    def test_reaction_must_precede_the_final_consequence(self):
        p = plan()
        r = pkg(p, "deliveries_p3")["reel"]
        r[4]["role"], r[5]["role"] = "CONSEQUENCE", "REACTION"
        self.assertTrue(has(problems(p), "end on a consequence"))

    def test_masters_play_in_story_order(self):
        p = plan()
        r = pkg(p, "deliveries_p4")["reel"]
        r[1]["master"], r[2]["master"] = r[2]["master"], r[1]["master"]
        self.assertTrue(has(problems(p), "out of story order"))

    def test_one_sentence_per_beat(self):
        p = plan()
        pkg(p, "deliveries_p2")["reel"][4]["text"] = "okay. that's new."
        self.assertTrue(has(problems(p), "one sentence per visual beat"))

    def test_reaction_needs_her_face(self):
        p = plan()
        master(p, "deliveries_p5", "M5")["face_visible"] = False
        self.assertTrue(has(problems(p), "a reaction beat must show Nyx's face"))

    def test_door_line_needs_the_door_in_frame(self):
        p = plan()
        master(p, "deliveries_p3", "M5")["door_in_frame"] = False
        self.assertTrue(has(problems(p), "mentions the door but M5 does not show it"))

    def test_duration_window(self):
        p = plan()
        pkg(p, "deliveries_p1")["reel"][0]["text"] = "it's late."
        pkg(p, "deliveries_p1")["reel"][2]["text"] = "so I opened it."
        pkg(p, "deliveries_p1")["reel"][4]["text"] = "I know this smell."
        self.assertTrue(has(problems(p), "outside 18.0-25.0 s"))

    def test_voice_bible_rules_apply(self):
        p = plan()
        pkg(p, "deliveries_p2")["reel"][6]["text"] = "comment below if you know which window is mine."
        self.assertTrue(has(problems(p), "engagement bait"))

    def test_premature_resolution_is_caught(self):
        p = plan()
        pkg(p, "deliveries_p5")["caption"] = "inside the third one was everything."
        self.assertTrue(has(problems(p), "premature resolution"))

    def test_captions_have_no_lone_flash_words(self):
        for p in PLAN["packages"]:
            script = pb.reel_script(p, PLAN, BIBLE)
            for line in script["lines"]:
                for c in line["captions"]:
                    self.assertGreaterEqual(c["end_s"] - c["start_s"], 0.6, c)


if __name__ == "__main__":
    unittest.main()
