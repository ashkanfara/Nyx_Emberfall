"""Regression tests for episode_coordinator.py (and the story_continuity fixes
it depends on). Everything runs against temp copies: no real lock, asset,
ledger, network or provider is touched."""

import ast
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import episode_coordinator as ec
import story_continuity as sc

ROOT = Path(__file__).resolve().parent
REAL_LOCK = ROOT / "brand" / "story_locks" / "s1e02_public.json"
REAL_PATCH = ROOT / "episodes" / "s1e02_public" / "proposed_lock_patch.json"
REAL_CAPTIONS = ROOT / "episodes" / "s1e02_public" / "captions.json"


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coord_test_"))
        self.locks = self.tmp / "locks"
        self.locks.mkdir()
        shutil.copy(REAL_LOCK, self.locks / REAL_LOCK.name)
        self.eps = self.tmp / "episodes"
        (self.eps / "s1e02_public").mkdir(parents=True)
        shutil.copy(REAL_PATCH, self.eps / "s1e02_public" / REAL_PATCH.name)
        shutil.copy(REAL_CAPTIONS, self.eps / "s1e02_public" / REAL_CAPTIONS.name)
        self.assets = self.tmp / "assets"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_dry(self):
        return ec.dry_run("s1e02_public", episodes_dir=self.eps, assets_root=self.assets,
                          locks_dir=self.locks, venture_path=self.tmp / "no_venture.json")


class StructuralBoundary(unittest.TestCase):
    ALLOWED = {"__future__", "copy", "hashlib", "json", "re", "struct", "sys", "tempfile", "zlib",
               "datetime", "pathlib", "carousel_handoff", "channels", "stages", "story_continuity"}

    def test_imports_nothing_that_can_generate_upload_publish_or_spend(self):
        tree = ast.parse((ROOT / "episode_coordinator.py").read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        names.discard("PIL")        # read-only image header fallback
        self.assertEqual(names - self.ALLOWED, set())

    def test_there_is_no_live_mode(self):
        self.assertEqual(ec.MODE, "DRY_RUN")


class StoryContinuityFixes(unittest.TestCase):
    def test_overlay_copy_spelling_is_read_as_the_overlay_line(self):
        lock = json.loads(REAL_LOCK.read_text())
        plan = sc.story_plan(lock)
        self.assertEqual(plan[0]["overlay_copy"], "this wasn't here when I locked up.")
        self.assertEqual(sc.brief_slides(lock)[0]["text_overlay"], "this wasn't here when I locked up.")

    def test_wordless_slides_need_no_overlay_or_safe_zone(self):
        lock = json.loads(REAL_LOCK.read_text())
        patched, _ = ec.merge_lock_patch(lock, json.loads(REAL_PATCH.read_text()))
        self.assertEqual(sc.plan_problems(patched), [])
        self.assertEqual(sc.story_plan(patched)[1]["overlay_copy"], "")

    def test_a_slide_with_copy_still_needs_a_safe_zone(self):
        lock = json.loads(REAL_LOCK.read_text())
        patch = json.loads(REAL_PATCH.read_text())
        del patch["slides"][0]["overlay_safe_zone"]
        patched, _ = ec.merge_lock_patch(lock, patch)
        self.assertIn("slide 1 missing overlay_safe_zone", sc.plan_problems(patched))

    def test_existing_published_lock_still_plans_clean(self):
        lock = json.loads((ROOT / "brand" / "story_locks" / "s1e01_public.json").read_text())
        self.assertEqual(sc.plan_problems(lock), [])


class LockPatchIsAdditiveOnly(unittest.TestCase):
    def test_refuses_to_overwrite_an_approved_value(self):
        lock = json.loads(REAL_LOCK.read_text())
        with self.assertRaises(ec.CoordinatorError):
            ec.merge_lock_patch(lock, {"slides": [{"slide_index": 1, "beat": "something else"}]})

    def test_patch_changes_no_existing_value(self):
        lock = json.loads(REAL_LOCK.read_text())
        patched, filled = ec.merge_lock_patch(lock, json.loads(REAL_PATCH.read_text()))
        self.assertTrue(filled)
        for before, after in zip(lock["story_plan"], patched["story_plan"]):
            for key, value in before.items():
                self.assertEqual(after[key], value)
        for key in lock:
            if key != "story_plan":
                self.assertEqual(patched[key], lock[key])


class DryRunOnTheRealLock(_Sandbox):
    def test_canon_untouched_and_nothing_generated(self):
        before = (self.locks / REAL_LOCK.name).read_bytes()
        ledger = self.run_dry()
        self.assertEqual((self.locks / REAL_LOCK.name).read_bytes(), before)
        self.assertTrue(ledger["canon_preserved"])
        self.assertFalse(self.assets.exists(), "a dry run must not create any asset")
        self.assertEqual(ledger["side_effects"]["spend_usd"], 0)
        self.assertFalse(any(ledger["side_effects"][k] for k in
                             ("browser", "uploads", "generation", "publications")))

    def test_statuses_and_smallest_blocker(self):
        ledger = self.run_dry()
        s = ledger["stage_status"]
        self.assertEqual(s["brief"], ec.BLOCKED)
        self.assertEqual(s["locked_prompts"], ec.BLOCKED)
        self.assertEqual(s["generation_handoff"], ec.WAITING_ON_UPSTREAM)
        self.assertEqual(s["captions"], ec.PASS)
        self.assertEqual(s["publish_gate"], ec.NOT_EXECUTED_DRY_RUN)
        self.assertEqual(ledger["stages"]["locked_prompts"]["mode"], "PREVIEW_UNDER_PROPOSED_PATCH")
        self.assertEqual(ledger["stages"]["brief"]["errors_after_patch"], [])
        self.assertIn("apply-lock-patch", ledger["smallest_blocker"]["fix"])
        self.assertFalse((self.eps / "s1e02_public" / "handoff").exists())
        self.assertTrue((self.eps / "s1e02_public" / "STATUS.md").is_file())

    def test_season_slot_drift_is_reported_not_fixed(self):
        warnings = self.run_dry()["stages"]["brief"]["warnings"]
        self.assertTrue(any("music_box_001" in w for w in warnings))

    def test_rehearsal_reaches_the_publish_gate_in_a_sandbox(self):
        reh = self.run_dry()["rehearsal"]
        self.assertTrue(reh["ran"] and reh["simulated"])
        self.assertEqual(reh["steps"][0]["handed_off"], [1])
        self.assertIn("canonical_ears_visible", reh["steps"][1]["repair_section"])
        last = reh["steps"][-1]
        self.assertEqual(last["visual_qa"], ec.PASS)
        self.assertEqual(last["variants"]["instagram"]["state"], "READY_TO_RENDER")
        self.assertEqual(last["variants"]["x"]["state"], "PREPARED_NOT_PUBLISHABLE")
        self.assertEqual(last["publish_gate"], ec.NOT_EXECUTED_DRY_RUN)

    def test_run_history_is_appended(self):
        self.run_dry()
        ledger = self.run_dry()
        self.assertEqual(len(ledger["runs"]), 2)


class AfterThePatchIsApplied(_Sandbox):
    def setUp(self):
        super().setUp()
        ec.apply_lock_patch("s1e02_public", locks_dir=self.locks, episodes_dir=self.eps)

    def test_apply_records_a_canon_revision(self):
        lock = json.loads((self.locks / REAL_LOCK.name).read_text())
        self.assertIn("additive planning fields", lock["canon_revisions"][-1]["change"])
        self.assertEqual(sc.plan_problems(lock), [])

    def test_prompts_lock_and_only_slide_one_is_handed_off(self):
        ledger = self.run_dry()
        self.assertEqual(ledger["stage_status"]["locked_prompts"], ec.PASS)
        handoff = ledger["stages"]["generation_handoff"]
        self.assertEqual(handoff["status"], ec.WAITING_ON_EXECUTOR)
        self.assertEqual([h["slide_index"] for h in handoff["handed_off"]], [1])
        packet = json.loads((self.eps / "s1e02_public" / "handoff" / "slide1.json").read_text())
        self.assertTrue(packet["cost"]["paid_providers_prohibited"])
        self.assertIn("never the Claude PS5 session", packet["executor"])
        self.assertEqual(ledger["smallest_blocker"]["stage"], "generation_handoff")

    def test_rejection_issues_a_targeted_repair_then_a_named_gap(self):
        lock = ec.load_lock("s1e02_public", self.locks)
        index = int(lock["item_index"])
        scores = {d: True for d in sc.QA_DIMENSIONS}
        scores["identity"] = False
        ec._placeholder_png(sc.candidate_path(index, 1, self.assets))
        ec.record_qa("s1e02_public", 1, dict(scores, notes="beauty mark on wrong cheek"),
                     locks_dir=self.locks, assets_root=self.assets)
        ledger = self.run_dry()
        repair = ledger["stages"]["targeted_repair"]
        self.assertEqual(repair["status"], ec.WAITING_ON_EXECUTOR)
        text = (ROOT / repair["issued"][0]["repair_prompt"]).read_text()   # absolute in a temp dir
        self.assertIn("- identity:", text)
        self.assertNotIn("- wardrobe_hair_continuity:", text)
        ec.record_qa("s1e02_public", 1, dict(scores, notes="still wrong"),
                     locks_dir=self.locks, assets_root=self.assets)
        ledger = self.run_dry()
        self.assertEqual(ledger["stages"]["targeted_repair"]["status"], ec.BLOCKED)
        self.assertEqual(ledger["stages"]["targeted_repair"]["named_gaps"][0]["state"],
                         "needs_human_action")


class Captions(_Sandbox):
    def _captions(self, **overrides):
        path = self.eps / "s1e02_public" / "captions.json"
        data = json.loads(path.read_text())
        data["captions"].update(overrides)
        path.write_text(json.dumps(data))
        return ec.stage_captions(ec.load_lock("s1e02_public", self.locks), self.eps / "s1e02_public")

    def test_route_without_ai_label_needs_disclosure_in_caption(self):
        out = self._captions(threads="an envelope under my door. open it or not?")
        self.assertIn("threads: no AI disclosure and this route has no native AI label",
                      out["problems"])

    def test_engagement_bait_and_duplicates_fail(self):
        out = self._captions(tiktok="comment below if I should open it?",
                             x="same text #AIgenerated", threads="same text #AIgenerated")
        self.assertTrue(any("engagement-bait" in p for p in out["problems"]))
        self.assertTrue(any("duplicates" in p for p in out["problems"]))

    def test_x_length_limit(self):
        out = self._captions(x="a" * 300 + " #AIgenerated")
        self.assertTrue(any(p.startswith("x:") and "> 280" in p for p in out["problems"]))


if __name__ == "__main__":
    unittest.main()
