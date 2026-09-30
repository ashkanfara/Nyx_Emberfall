"""Tests for visual_qa_benchmark.py: deterministic, read-only, and the
committed qa_benchmark/report.json never goes stale against its inputs."""
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

import visual_qa_benchmark as vq

ROOT = Path(__file__).resolve().parent


class Determinism(unittest.TestCase):
    def test_two_runs_are_byte_identical(self):
        self.assertEqual(vq.serialise(vq.build_report()), vq.serialise(vq.build_report()))

    def test_committed_report_is_fresh(self):
        self.assertEqual(vq.main(["--check"]), 0,
                         "qa_benchmark/report.json is stale -- run python3 visual_qa_benchmark.py")


class ReadOnlyBoundary(unittest.TestCase):
    """Same structural boundary as episode_manifest.py: no module that could
    generate, upload, publish or reach the network is even imported."""

    def test_stdlib_only_no_network(self):
        tree = ast.parse((ROOT / "visual_qa_benchmark.py").read_text())
        mods = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
        mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        allowed = {"__future__", "argparse", "glob", "hashlib", "json", "re", "struct", "sys", "fnmatch", "pathlib"}
        self.assertLessEqual(mods, allowed)

    def test_summary_reports_zero_side_effects(self):
        s = vq.build_report()["coordinator_summary"]
        self.assertEqual((s["spend_usd"], s["generations"], s["uploads"], s["publishes"]), (0, 0, 0, 0))


class Helpers(unittest.TestCase):
    def test_image_size_reads_png_and_webp_headers(self):
        self.assertEqual(vq.image_size(ROOT / "brand/nyx_identity/reference_primary.webp"), (1224, 1285))
        self.assertEqual(vq.image_size(ROOT / "brand/nyx_identity/generation_refs/gen_ref_1_face_closeup.png"),
                         (280, 582))

    def test_aspect_name(self):
        names = ["9:16", "4:5", "1:1"]
        self.assertEqual(vq.aspect_name(1080, 1920, names, 0.01), "9:16")
        self.assertEqual(vq.aspect_name(768, 960, names, 0.01), "4:5")
        self.assertIsNone(vq.aspect_name(1080, 1632, names, 0.01))


class ManifestShape(unittest.TestCase):
    def setUp(self):
        self.report = vq.build_report()
        self.formats = json.loads((ROOT / "qa_benchmark/platform_formats.json").read_text())

    def test_all_five_platforms_have_canvas_aspect_and_safe_zone(self):
        self.assertEqual(set(self.formats["platforms"]), {"instagram", "tiktok", "threads", "x", "fanvue"})
        for p in self.formats["platforms"].values():
            for f in p["formats"].values():
                w, h = f["canvas"]
                self.assertAlmostEqual(w / h, vq.ratio(f["aspect"]), places=2)
                self.assertIn(f["aspect"], f["accepted_aspects"])
                z = f["safe_text_zone"]
                self.assertLess(z["top"] + z["bottom"], 1)
                self.assertLess(z["left"] + z["right"], 1)
                self.assertIn(f["confidence"], self.formats["confidence_legend"])

    def test_every_case_has_known_status(self):
        for c in self.report["cases"]:
            self.assertIn(c["status"], (vq.PASS, vq.WARN, vq.FAIL, vq.NA), c)

    def test_every_failing_id_has_a_next_action(self):
        s = self.report["coordinator_summary"]
        self.assertEqual(len(s["next_actions"]),
                         len([i for i in s["failing_case_ids"] if i in vq.NEXT_ACTIONS]))
        self.assertLessEqual(set(s["failing_case_ids"]), set(vq.NEXT_ACTIONS))


if __name__ == "__main__":
    unittest.main()
