"""Render masters (4:5 public / 9:16 TikTok+Fanvue), platform-safe overlay
placement, s1e02_public's explicit text placement, and the canonical room
reference gate. All zero-cost: no generation, upload, publish or network."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import carousel_handoff
import episode_manifest
import openai_image_provider as oip
import render_masters as rm
import room_references as rr
import story_continuity as sc

ROOT = Path(__file__).resolve().parent
LOCKS = {p.stem: json.loads(p.read_text()) for p in sorted((ROOT / "brand/story_locks").glob("*.json"))}

try:
    from PIL import Image, ImageChops
except ImportError:              # pragma: no cover - environment-dependent
    Image = None


class Masters(unittest.TestCase):
    def test_public_carousel_is_4x5_and_vertical_is_only_tiktok_and_fanvue(self):
        self.assertEqual(rm.master(rm.PUBLIC_MASTER)["aspect"], "4:5")
        self.assertEqual((rm.master(rm.PUBLIC_MASTER)["width"], rm.master(rm.PUBLIC_MASTER)["height"]),
                         (1080, 1350))
        self.assertEqual(sorted(rm.master(rm.VERTICAL_MASTER)["platforms"]), ["fanvue", "tiktok"])
        for p in ("instagram", "threads", "x"):
            self.assertEqual(rm.masters_for_platform(p), [rm.PUBLIC_MASTER])
        for p in ("tiktok", "fanvue"):
            self.assertEqual(rm.masters_for_platform(p), [rm.VERTICAL_MASTER])
        self.assertEqual(rm.masters_for_platform("public_multi"), [rm.PUBLIC_MASTER, rm.VERTICAL_MASTER])
        self.assertEqual(rm.masters_for_platform(None), [rm.PUBLIC_MASTER])

    def test_every_story_lock_agrees_with_its_masters(self):
        for sid, lock in LOCKS.items():
            self.assertEqual(rm.lock_master_problems(lock), [], sid)
            self.assertEqual(sc.lock_problems(lock), [], sid)
            if lock["target_platform"] in ("instagram", "public_multi"):
                self.assertEqual(lock["aspect_ratio"], "4:5", sid)

    def test_a_9x16_instagram_lock_is_now_a_lock_problem(self):
        bad = {**LOCKS["star_map_001"], "aspect_ratio": "9:16"}
        self.assertTrue(any("aspect_ratio 9:16" in p for p in sc.lock_problems(bad)))

    def test_secondary_master_gets_its_own_filename(self):
        self.assertEqual(rm.file_suffix("public_multi", rm.PUBLIC_MASTER), "")
        self.assertEqual(rm.file_suffix("public_multi", rm.VERTICAL_MASTER), "_9x16")
        self.assertEqual(rm.file_suffix("fanvue", rm.VERTICAL_MASTER), "")   # primary keeps its name
        self.assertEqual(carousel_handoff.slide_filename(33, 1, "_9x16"), "item33_slide1_9x16.png")


class SafeZones(unittest.TestCase):
    def test_vertical_zone_is_the_strictest_of_tiktok_and_fanvue(self):
        z = rm.safe_text_zone(rm.VERTICAL_MASTER)
        self.assertEqual(z, {"top": 0.10, "bottom": 0.25, "left": 0.06, "right": 0.15})

    def test_text_box_refuses_a_canvas_of_the_wrong_master(self):
        with self.assertRaises(rm.RenderMasterError):
            rm.text_box(rm.VERTICAL_MASTER, "bottom", width=1080, height=1350, block_height=100,
                        max_width_frac=0.78)

    def test_bottom_text_on_vertical_clears_the_tiktok_ui(self):
        box = rm.text_box(rm.VERTICAL_MASTER, "bottom", width=1080, height=1920, block_height=200,
                          max_width_frac=0.78)
        self.assertLessEqual(box["y"] + 200, 1920 * 0.75)
        self.assertLessEqual(box["x"] + box["max_width"], 1080 * 0.85)

    def test_anchor_resolution_order(self):
        self.assertEqual(rm.resolve_anchor({"text_placement": {"anchor": "top"},
                                            "overlay_safe_zone": "lower third"}), ("top", "text_placement"))
        self.assertEqual(rm.resolve_anchor({"overlay_safe_zone": "upper third, clear of x"}),
                         ("top", "overlay_safe_zone"))
        self.assertEqual(rm.resolve_anchor({}), ("bottom", "default"))
        with self.assertRaises(rm.RenderMasterError):
            rm.resolve_anchor({"text_placement": {"anchor": "middle"}})


def _item(platform, copies=("line one", "", "line three")):
    return {"target_platform": platform,
            "slides": [{"text_overlay": c, "role": "HOOK", "beat": "b", "composition": "c"} for c in copies]}


class OverlayPlan(unittest.TestCase):
    def test_fanvue_item_uses_the_vertical_zone_and_explicit_none_stays_wordless(self):
        plan = carousel_handoff.overlay_plan(_item("fanvue"), 31, Path("/tmp/x"),
                                             placements={1: {"anchor": "top"}, 3: {"anchor": "none"}})
        self.assertEqual([p["slot"] for p in plan], [1])
        self.assertEqual(plan[0]["master"], rm.VERTICAL_MASTER)
        self.assertEqual(plan[0]["safe_text_zone"]["bottom"], 0.25)
        self.assertEqual(plan[0]["placement_source"], "text_placement")
        self.assertTrue(plan[0]["source"].endswith("item31_slide1.png"))

    def test_public_multi_secondary_master_renders_beside_the_public_file(self):
        plan = carousel_handoff.overlay_plan(_item("public_multi"), 33, Path("/tmp/x"),
                                             master_id=rm.VERTICAL_MASTER)
        self.assertTrue(plan[0]["source"].endswith("item33_slide1_9x16.png"))
        self.assertTrue(plan[0]["output"].endswith("item33_slide1_9x16_captioned.png"))

    @unittest.skipIf(Image is None, "Pillow not installed")
    def test_burned_in_text_stays_inside_the_safe_zone_on_both_masters(self):
        for platform, (w, h), anchor in (("fanvue", (1080, 1920), "bottom"), ("fanvue", (1080, 1920), "top"),
                                         ("instagram", (1080, 1350), "bottom")):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                d = carousel_handoff.package_dir(40, root)
                d.mkdir(parents=True)
                src = d / carousel_handoff.slide_filename(40, 1)
                Image.new("RGB", (w, h), (40, 40, 40)).save(src)
                item = _item(platform, copies=("open it, or check the hallway first?",))
                out = carousel_handoff.apply_overlays(item, 40, root, placements={1: {"anchor": anchor}})
                with Image.open(src) as a, Image.open(out[0]) as b:
                    bbox = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox()
                z = rm.safe_text_zone(rm.masters_for_platform(platform)[0])
                self.assertIsNotNone(bbox)
                self.assertGreaterEqual(bbox[0], w * z["left"] - 1)
                self.assertGreaterEqual(bbox[1], h * z["top"] - 1)
                self.assertLessEqual(bbox[3], h * (1 - z["bottom"]) + 1, (platform, anchor))

    @unittest.skipIf(Image is None, "Pillow not installed")
    def test_a_9x16_image_is_refused_for_a_4x5_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = carousel_handoff.package_dir(41, root)
            d.mkdir(parents=True)
            Image.new("RGB", (1080, 1920)).save(d / carousel_handoff.slide_filename(41, 1))
            with self.assertRaises(rm.RenderMasterError):
                carousel_handoff.apply_overlays(_item("instagram", copies=("x",)), 41, root)


class S1E02Placement(unittest.TestCase):
    def setUp(self):
        self.lock = LOCKS["s1e02_public"]

    def test_every_slide_has_an_explicit_placement_matching_its_copy(self):
        for s in self.lock["story_plan"]:
            tp = s["text_placement"]
            self.assertIn(tp["anchor"], rm.ANCHORS)
            if s["overlay_copy"].strip():
                self.assertIn(tp["anchor"], ("top", "bottom"), s["slide_index"])
                self.assertTrue(tp["keep_clear"], s["slide_index"])
            else:
                self.assertEqual(tp["anchor"], "none", s["slide_index"])
        self.assertEqual([s["text_placement"]["anchor"] for s in self.lock["story_plan"]],
                         ["top", "none", "bottom", "top", "none", "bottom"])

    def test_story_plan_carries_placement_and_copy_to_the_prompt(self):
        plan = sc.story_plan(self.lock)
        self.assertEqual(plan[0]["overlay_copy"], "this wasn't here when I locked up.")
        self.assertEqual(plan[0]["text_placement"]["anchor"], "top")
        self.assertTrue(sc._placement_wording(plan[0]).startswith("upper third, clear of the envelope"))

    def test_public_multi_renders_both_masters(self):
        self.assertEqual(self.lock["render_masters"], [rm.PUBLIC_MASTER, rm.VERTICAL_MASTER])


class ProviderSizes(unittest.TestCase):
    def _refs(self):
        return [{"role": "identity", "decides": "who", "never_identity": False}]

    def test_request_and_output_sizes_follow_the_master(self):
        pub = oip.request_spec("p", self._refs())
        self.assertEqual((pub["size"], pub["aspect_ratio"], pub["output_size"]), ("1088x1360", "4:5", [1080, 1350]))
        vert = oip.request_spec("p", self._refs(), master_id=rm.VERTICAL_MASTER)
        self.assertEqual((vert["size"], vert["aspect_ratio"], vert["output_size"]),
                         ("1152x2048", "9:16", [1080, 1920]))
        self.assertEqual(vert["estimate"]["size"], "1152x2048")
        with self.assertRaises(oip.OpenAIImageError):
            oip.request_spec("p", self._refs(), master_id="square")


class RoomGate(unittest.TestCase):
    def test_unregistered_rooms_block_everyone_but_the_establishing_story(self):
        self.assertFalse(rr.gate_for_lock(LOCKS["s1e02_public"], 1)["allowed"])
        self.assertEqual(rr.gate_for_lock(LOCKS["s1e02_public"], 1)["status"], rr.BLOCKED_NO_REFERENCE)
        self.assertTrue(rr.gate_for_lock(LOCKS["star_atlas_001"], 1)["allowed"])
        self.assertEqual(rr.gate("nyx_new_room", "x", 1)["status"], rr.BLOCKED_UNKNOWN_ROOM)

    def test_every_lock_room_is_in_the_manifest(self):
        rooms = rr.load()["rooms"]
        for sid, lock in LOCKS.items():
            loc = lock["environment_lock"]["location_id"]
            self.assertIn(loc, rooms, sid)
            self.assertIn(sid, rooms[loc]["used_by"])

    @unittest.skipIf(Image is None, "Pillow not installed")
    def test_register_verifies_then_a_changed_file_blocks_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "rooms.json"
            shutil.copy(rr.MANIFEST_PATH, manifest)
            asset = root / "loft.png"
            Image.new("RGB", (1080, 1350), (10, 20, 30)).save(asset)
            rr.register("nyx_loft_star_atlas", str(asset), "founder (test)", root=root, path=manifest)
            m = rr.load(manifest)
            self.assertEqual(rr.gate("nyx_loft_star_atlas", "s1e02_public", 1, root=root, manifest=m)["status"],
                             rr.ALLOWED_VERIFIED)
            Image.new("RGB", (1080, 1350), (99, 20, 30)).save(asset)
            self.assertEqual(rr.gate("nyx_loft_star_atlas", "s1e02_public", 1, root=root, manifest=m)["status"],
                             rr.BLOCKED_REFERENCE_CHANGED)
            wide = root / "wide.png"
            Image.new("RGB", (1600, 900)).save(wide)
            with self.assertRaises(rr.RoomReferenceError):
                rr.register("nyx_loft_star_atlas", str(wide), "founder (test)", root=root, path=manifest)

    def test_episode_manifest_refuses_a_room_pass_without_a_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "episode_manifest.json"
            with mock.patch.object(episode_manifest, "manifest_path", lambda i: path):
                episode_manifest.init_manifest(33, "s1e02", "t", "public_multi", [1],
                                               "brand/story_locks/s1e02_public.json")
                m = json.loads(path.read_text())
                m["slides"][0]["state"] = "generated"
                path.write_text(json.dumps(m))
                passing = {d: "PASS" for d in episode_manifest.QA_DIMENSIONS}
                with self.assertRaisesRegex(ValueError, "BLOCKED_NO_ROOM_REFERENCE"):
                    episode_manifest.qa_decide(33, 1, passing)
                self.assertEqual(json.loads(path.read_text())["slides"][0]["state"], "generated")
                blocked = {**passing, "environment_continuity": "BLOCKED: no room reference"}
                out = episode_manifest.qa_decide(33, 1, blocked)
                self.assertEqual(out["slides"][0]["state"], "qa_failed")


if __name__ == "__main__":
    unittest.main()
