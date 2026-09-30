"""Tests for nyx_runner.py. Every test runs in a temp sandbox built from the
real s1e02_public lock; nothing touches the real repo state, generated_assets,
a network, a provider or a platform."""

import ast
import fcntl
import json
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import episode_coordinator as ec
import nyx_runner as nr
import story_continuity as sc

ROOT = Path(__file__).resolve().parent
EXPECTED_QUEUE = {
    "s1e02_public:instagram": nr.READY_FOR_PUBLISH,
    "s1e02_public:threads": nr.HELD_NO_ROUTE,
    "s1e02_public:tiktok": nr.READY_FOR_PUBLISH,
    "s1e02_public:x": nr.HELD_NO_ROUTE,
    "sim_s1e02_fanvue:fanvue": nr.READY_FOR_PUBLISH,
}


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="runner_test_"))
        self.env = nr.build_sandbox(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def queue(self, env=None):
        env = env or self.env
        return {k: e["status"] for k, e in
                nr.read_json(env.episodes_dir / nr.QUEUE_NAME, {"entries": {}})["entries"].items()}

    def sc_state(self, env=None, story="s1e02_public"):
        env = env or self.env
        lock = ec.load_lock(story, env.locks_dir)
        return sc.load_state(lock, int(lock["item_index"]), root=env.assets_root)

    def evidence(self, env=None):
        env = env or self.env
        return sorted(p.name for p in (env.episodes_dir / "s1e02_public" / "qa").glob("*.json"))

    def episode(self, story="s1e02_public", env=None):
        env = env or self.env
        return nr.read_json(nr.state_path(env, story))


class SimulatedEpisode(_Sandbox):
    """Acceptance 1: one automated simulated episode, lock -> release queue."""

    def test_runs_from_approved_lock_to_release_queue_without_relay(self):
        result = nr.run_until_settled(self.env)
        self.assertEqual(result["episodes"], {"s1e02_public": nr.READY_FOR_PUBLISH,
                                              "sim_s1e02_fanvue": nr.READY_FOR_PUBLISH})
        self.assertEqual(self.queue(), EXPECTED_QUEUE)

    def test_bounded_repair_happened_through_real_story_state(self):
        nr.run_until_settled(self.env)
        rejected = self.sc_state()["retry_state"]["rejected"]
        self.assertEqual([(r["slide_index"], r["attempt"], r["failed"]) for r in rejected],
                         [(2, 1, ["identity"])])
        packet = json.loads((self.env.episodes_dir / "s1e02_public" / "handoff" /
                             "slide2_attempt2.json").read_text())
        self.assertEqual(packet["kind"], "repair")
        prompt = Path(packet["prompt_file"]).read_text()
        self.assertIn("TARGETED REPAIR -- attempt 2 of 2", prompt)
        self.assertIn("- identity:", prompt)
        rejected_dir = nr.package_dir(self.env, ec.load_lock("s1e02_public", self.env.locks_dir)) / "rejected"
        self.assertTrue((rejected_dir / "item33_slide2_attempt1.png").is_file())

    def test_every_approval_has_sha_bound_evidence(self):
        nr.run_until_settled(self.env)
        lock = ec.load_lock("s1e02_public", self.env.locks_dir)
        for n in range(1, 7):
            files = sorted((self.env.episodes_dir / "s1e02_public" / "qa").glob(f"slide{n}_attempt*.json"))
            ev = json.loads(files[-1].read_text())
            self.assertEqual(ev["verdict"], "APPROVED")
            self.assertTrue(ev["recorded"])
            self.assertEqual(ev["asset_sha256"], nr.sha256_file(nr.asset_path(self.env, lock, n)))
            self.assertTrue(all(v["observation"] for v in ev["dimensions"].values()))

    def test_queue_never_publishes_and_needs_a_human(self):
        nr.run_until_settled(self.env)
        entries = nr.read_json(self.env.episodes_dir / nr.QUEUE_NAME)["entries"]
        for e in entries.values():
            self.assertTrue(e["requires_human_confirmation"])
            self.assertTrue(e["simulated"])
            self.assertNotIn("live_url", e)
        self.assertEqual(entries["s1e02_public:instagram"]["caption"],
                         json.loads((ROOT / "episodes/s1e02_public/captions.json").read_text())
                         ["captions"]["instagram"])       # valid existing captions are reused
        with self.assertRaises(nr.RunnerError):
            nr.confirm_published(self.env, "s1e02_public", "instagram", "https://x", by="")
        nr.confirm_published(self.env, "s1e02_public", "instagram", "https://example/p/1", by="founder")
        nr.tick(self.env)
        self.assertEqual(self.queue()["s1e02_public:instagram"], nr.PUBLISHED_CONFIRMED)
        with self.assertRaises(nr.RunnerError):
            nr.confirm_published(self.env, "s1e02_public", "x", "https://x/1", by="founder")

    def test_status_report_is_written(self):
        nr.run_until_settled(self.env)
        text = (self.env.episodes_dir / nr.STATUS_NAME).read_text()
        self.assertIn("| s1e02_public | READY_FOR_PUBLISH | 6/6 |", text)
        self.assertIn("Fanvue chapter of s1e02_public", text)
        self.assertIn("Live-integration blockers", text)


class InterruptedAndResumed(_Sandbox):
    """Acceptance 2: a crash at every durable step, then a fresh process resumes
    to exactly the uninterrupted result -- no duplicate verdict, no lost repair."""
    CHECKPOINTS = ("handoff_written", "qa_evidence_written", "verdict_recorded_in_story_state",
                   "before_archive", "variants_rendered")

    def _crash_run(self, name, occurrence):
        env = nr.build_sandbox(self.tmp / f"{name}_{occurrence}")
        seen = {"n": 0, "fired": False}

        def hook(cp):
            if cp == name:
                seen["n"] += 1
                if seen["n"] == occurrence and not seen["fired"]:
                    seen["fired"] = True
                    raise KeyboardInterrupt(f"crash at {cp}")
        env.checkpoint_hook = hook
        with self.assertRaises(KeyboardInterrupt):
            nr.run_until_settled(env)
        # "Restart": new adapter instances, no in-memory state carried over.
        env.checkpoint_hook = None
        env.reviewer = nr.SimulatedReviewer(reject={(2, 1): ["identity"]})
        return env, nr.run_until_settled(env)

    def test_resume_after_crash_at_each_checkpoint(self):
        for name in self.CHECKPOINTS:
            # variants render once per episode (2); the slide-2 rejection archives once.
            occurrences = {"before_archive": (1,), "variants_rendered": (1, 2)}.get(name, (1, 3))
            for occ in occurrences:
                with self.subTest(checkpoint=name, occurrence=occ):
                    env, result = self._crash_run(name, occ)
                    self.assertEqual(set(result["episodes"].values()), {nr.READY_FOR_PUBLISH})
                    self.assertEqual(self.queue(env), EXPECTED_QUEUE)
                    rej = [(r["slide_index"], r["attempt"]) for r in
                           self.sc_state(env)["retry_state"]["rejected"]]
                    self.assertEqual(rej, [(2, 1)])
                    self.assertEqual(len(self.evidence(env)), 7)

    def test_tick_is_idempotent(self):
        nr.run_until_settled(self.env)
        queue = (self.env.episodes_dir / nr.QUEUE_NAME).read_text()
        handoffs = {p.name: p.read_text() for p in
                    (self.env.episodes_dir / "s1e02_public" / "handoff").iterdir()}
        nr.tick(self.env)
        nr.tick(self.env)
        self.assertEqual((self.env.episodes_dir / nr.QUEUE_NAME).read_text(), queue)
        self.assertEqual({p.name: p.read_text() for p in
                          (self.env.episodes_dir / "s1e02_public" / "handoff").iterdir()}, handoffs)

    def test_overlapping_tick_is_refused(self):
        self.env.episodes_dir.mkdir(parents=True, exist_ok=True)
        with open(self.env.episodes_dir / nr.LOCK_NAME, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertFalse(nr.tick(self.env)["ran"])


class Retries(_Sandbox):
    def setUp(self):
        super().setUp()
        # Public episode only, so reviewer call counts belong to one story.
        reg = nr.read_json(self.env.episodes_dir / nr.REGISTRY_NAME)
        reg["episodes"] = reg["episodes"][:1]
        nr.write_json_atomic(self.env.episodes_dir / nr.REGISTRY_NAME, reg)

    def run_with_clock(self, rounds=30, step=timedelta(hours=7)):
        """Tick through real backoff windows (run_until_settled stops at a wait)."""
        result = None
        for _ in range(rounds):
            result = nr.tick(self.env)
            nr.simulated_executor(self.env)
            self.env._clock_state["now"] += step
        return result

    def test_reviewer_outage_backs_off_then_resumes(self):
        self.env.reviewer = nr.SimulatedReviewer(reject={(2, 1): ["identity"]}, outages=2)
        nr.tick(self.env)
        nr.simulated_executor(self.env)
        nr.tick(self.env)
        st = self.episode()
        self.assertEqual(st["status"], "WAITING_ON_QA_RETRY")
        self.assertEqual(st["retry"]["qa:1:1"]["failures"], 1)
        nr.tick(self.env)                                  # not due yet: no second call
        self.assertEqual(self.env.reviewer.calls, 1)
        result = self.run_with_clock()
        self.assertEqual(result["episodes"]["s1e02_public"], nr.READY_FOR_PUBLISH)
        self.assertNotIn("qa:1:1", self.episode().get("retry", {}))

    def test_persistent_reviewer_failure_blocks_instead_of_looping(self):
        self.env.reviewer = nr.SimulatedReviewer(outages=10 ** 6)
        result = self.run_with_clock()
        self.assertEqual(result["episodes"]["s1e02_public"], "BLOCKED")
        self.assertEqual(self.env.reviewer.calls, nr.MAX_REVIEWER_FAILURES)
        self.assertEqual(self.evidence(), [])
        self.env.reviewer = nr.SimulatedReviewer()
        nr.unblock(self.env, "s1e02_public")
        self.assertEqual(self.run_with_clock()["episodes"]["s1e02_public"], nr.READY_FOR_PUBLISH)

    def test_exhausted_repairs_become_a_named_gap(self):
        self.env.reviewer = nr.SimulatedReviewer(reject={(1, 1): ["canonical_ears_visible"],
                                                         (1, 2): ["canonical_ears_visible"]})
        result = nr.run_until_settled(self.env)
        self.assertEqual(result["episodes"]["s1e02_public"], "HOLD_NAMED_GAP")
        self.assertEqual(self.episode()["slides"]["1"]["state"], "NEEDS_HUMAN_ACTION")
        self.assertNotIn("s1e02_public:instagram", self.queue())

    def test_renderer_unavailable_blocks_then_recovers(self):
        class Broken:
            name, simulated = "broken", True

            def render(self, job):
                raise nr.Unavailable("ffmpeg not found on PATH")
        good, self.env.renderer = self.env.renderer, Broken()
        result = nr.run_until_settled(self.env)
        self.assertEqual(result["episodes"]["s1e02_public"], "BLOCKED_RENDERER")
        self.assertEqual(self.queue(), {})
        self.env.renderer = good
        self.env._clock_state["now"] += timedelta(hours=7)
        self.assertEqual(nr.run_until_settled(self.env)["episodes"]["s1e02_public"], nr.READY_FOR_PUBLISH)


class NoEvidenceNoProgress(_Sandbox):
    def _slot1(self):
        nr.tick(self.env)
        return nr.asset_path(self.env, ec.load_lock("s1e02_public", self.env.locks_dir), 1)

    def test_image_without_provenance_is_never_reviewed(self):
        target = self._slot1()
        nr._pattern_png(target, 1080, 1350, 1)
        nr.tick(self.env)
        self.assertEqual(self.episode()["slides"]["1"]["state"], "ASSET_REFUSED")
        self.assertEqual(self.env.reviewer.calls, 0)
        self.assertEqual(self.evidence(), [])

    def test_flat_image_is_rejected_by_the_technical_gate_not_the_reviewer(self):
        self._slot1()
        flat = self.tmp / "flat.png"
        ec._placeholder_png(flat)
        nr.record_asset(self.env, "s1e02_public", 1, flat, by="t", provider="t", simulated=True)
        nr.tick(self.env)
        ev = json.loads((self.env.episodes_dir / "s1e02_public/qa/slide1_attempt1.json").read_text())
        self.assertEqual(ev["reviewer"], "deterministic_technical_gate")
        self.assertEqual(ev["verdict"], "REJECTED")
        self.assertEqual(self.env.reviewer.calls, 0)
        self.assertEqual(self.sc_state()["retry_state"]["attempts"], {"1": 1})

    def test_wrong_size_is_rejected(self):
        ok, why = nr.technical_gate(self._make(1080, 1920), "4:5")
        self.assertFalse(ok)
        self.assertIn("expected 1080x1350", why)

    def _make(self, w, h):
        p = self.tmp / f"{w}x{h}.png"
        nr._pattern_png(p, w, h, 3)
        return p

    def test_delivery_needs_an_open_handoff(self):
        self._slot1()
        with self.assertRaises(nr.RunnerError):
            nr.record_asset(self.env, "s1e02_public", 3, self._make(1080, 1350), by="t", provider="t",
                            simulated=True)

    def test_simulation_is_refused_outside_a_sandbox(self):
        self.env.sandbox = False
        with self.assertRaises(nr.RunnerError):
            nr.tick(self.env)
        with self.assertRaises(nr.RunnerError):
            nr.record_asset(self.env, "s1e02_public", 1, self._make(1080, 1350), by="t",
                            provider="t", simulated=True)

    def test_simulated_image_is_refused_by_a_real_run(self):
        self._slot1()
        nr.simulated_executor(self.env)
        self.env.sandbox = False
        self.env.reviewer = self.env.author = self.env.renderer = type(
            "Real", (), {"name": "real", "simulated": False, "review": lambda s, r: {}})()
        nr.tick(self.env)
        self.assertIn("SIMULATED image refused", self.episode()["slides"]["1"]["detail"])

    def test_missing_fanvue_chapter_is_reported(self):
        reg = nr.read_json(self.env.episodes_dir / nr.REGISTRY_NAME)
        reg["episodes"] = reg["episodes"][:1]
        nr.write_json_atomic(self.env.episodes_dir / nr.REGISTRY_NAME, reg)
        nr.tick(self.env)
        self.assertIsNone(self.episode()["fanvue_chapter"])
        self.assertIn("NO_APPROVED_LOCK", (self.env.episodes_dir / nr.STATUS_NAME).read_text())


class Boundary(unittest.TestCase):
    ALLOWED = {"__future__", "fcntl", "hashlib", "json", "os", "shutil", "struct", "subprocess",
               "sys", "tempfile", "zlib", "dataclasses", "datetime", "pathlib", "carousel_handoff",
               "channels", "episode_coordinator", "story_continuity", "claude_client",
               "PIL"}   # PIL: read-only availability probe in live_blockers

    def test_imports_nothing_that_can_generate_publish_or_touch_reddit(self):
        tree = ast.parse((ROOT / "nyx_runner.py").read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        self.assertEqual(names - self.ALLOWED, set())

    def test_real_registry_enrols_only_approved_locks(self):
        reg = json.loads((ROOT / "episodes" / nr.REGISTRY_NAME).read_text())
        for e in reg["episodes"]:
            self.assertTrue(e.get("approved_by"))
            self.assertTrue(ec.lock_path(e["story_id"]).is_file())


if __name__ == "__main__":
    unittest.main()
