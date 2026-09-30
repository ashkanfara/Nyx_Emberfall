"""Tests for nyx_runner.py (exception-only continuation). Every test runs in a
temp sandbox built from the real s1e02_public lock; nothing touches the real
repo state, generated_assets, a network, a provider or a platform."""

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
AT_APPROVAL = {"s1e02_public": nr.EXC_PUBLISH_APPROVAL, "sim_s1e02_fanvue": nr.EXC_PUBLISH_APPROVAL}


class _Sandbox(unittest.TestCase):
    unattended = False

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="runner_test_"))
        self.env = nr.build_sandbox(self.tmp, unattended=self.unattended)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def only_public(self):
        reg = nr.read_json(self.env.episodes_dir / nr.REGISTRY_NAME)
        reg["episodes"] = reg["episodes"][:1]
        nr.write_json_atomic(self.env.episodes_dir / nr.REGISTRY_NAME, reg)

    def queue(self, env=None):
        env = env or self.env
        return {k: e["status"] for k, e in
                nr.read_json(env.episodes_dir / nr.QUEUE_NAME, {"entries": {}})["entries"].items()}

    def exceptions(self, env=None):
        env = env or self.env
        return nr.read_json(env.episodes_dir / nr.EXCEPTIONS_NAME, {"open": {}})["open"]

    def sc_state(self, env=None, story="s1e02_public"):
        env = env or self.env
        lock = ec.load_lock(story, env.locks_dir)
        return sc.load_state(lock, int(lock["item_index"]), root=env.assets_root)

    def evidence(self, env=None):
        env = env or self.env
        return sorted(p.name for p in (env.episodes_dir / "s1e02_public" / "qa").glob("*.json"))

    def episode(self, story="s1e02_public", env=None):
        return nr.read_json(nr.state_path(env or self.env, story))

    def run_with_clock(self, rounds=40, step=timedelta(hours=7), deliver=True):
        """Ticks through real backoff windows."""
        result = None
        for _ in range(rounds):
            result = nr.tick(self.env)
            if deliver:
                nr.simulated_executor(self.env)
            self.env._clock_state["now"] += step
        return result


class SimulatedEpisode(_Sandbox):
    """Acceptance: one automated simulated episode reaches the single publish
    approval stop, through a real bounded repair, and never publishes."""

    def test_runs_to_the_publish_approval_exception(self):
        result = nr.run_until_settled(self.env)
        self.assertEqual(result["episodes"], AT_APPROVAL)
        self.assertEqual(self.queue(), EXPECTED_QUEUE)
        self.assertEqual({k: v["category"] for k, v in self.exceptions().items()},
                         {"s1e02_public": 3, "sim_s1e02_fanvue": 3})

    def test_bounded_repair_happened_through_real_story_state(self):
        nr.run_until_settled(self.env)
        rejected = self.sc_state()["retry_state"]["rejected"]
        self.assertEqual([(r["slide_index"], r["attempt"], r["failed"]) for r in rejected],
                         [(2, 1, ["identity"])])
        packet = json.loads((self.env.episodes_dir / "s1e02_public/handoff/slide2_attempt2.json").read_text())
        self.assertEqual((packet["kind"], packet["max_attempts"]), ("repair", 3))
        prompt = Path(packet["prompt_file"]).read_text()
        self.assertIn("TARGETED REPAIR -- attempt 2 of 3", prompt)
        self.assertIn("- identity:", prompt)

    def test_every_approval_has_sha_bound_evidence(self):
        nr.run_until_settled(self.env)
        lock = ec.load_lock("s1e02_public", self.env.locks_dir)
        for n in range(1, 7):
            files = sorted((self.env.episodes_dir / "s1e02_public" / "qa").glob(f"slide{n}_attempt*.json"))
            ev = json.loads(files[-1].read_text())
            self.assertEqual((ev["verdict"], ev["recorded"]), ("APPROVED", True))
            self.assertEqual(ev["asset_sha256"], nr.sha256_file(nr.asset_path(self.env, lock, n)))

    def test_one_approval_then_human_publish_then_published(self):
        nr.run_until_settled(self.env)
        with self.assertRaises(nr.RunnerError):     # no publish before the approval
            nr.confirm_published(self.env, "s1e02_public", "instagram", "https://x/1", by="founder")
        with self.assertRaises(nr.RunnerError):
            nr.approve_release(self.env, "s1e02_public", by="")
        pkg = nr.approve_release(self.env, "s1e02_public", by="founder")
        self.assertEqual(pkg["entries"], ["s1e02_public:instagram", "s1e02_public:tiktok"])
        nr.tick(self.env)
        st = self.episode()
        self.assertEqual(st["status"], nr.EXC_PUBLISH_APPROVAL)      # open until published
        self.assertIn("awaiting the human publish", st["exception"]["detail"])
        with self.assertRaises(nr.RunnerError):     # held routes can never be confirmed
            nr.confirm_published(self.env, "s1e02_public", "x", "https://x/1", by="founder")
        nr.confirm_published(self.env, "s1e02_public", "instagram", "https://ig/p/1", by="founder")
        nr.confirm_published(self.env, "s1e02_public", "tiktok", "https://tt/v/1", by="founder")
        nr.tick(self.env)
        self.assertEqual(self.episode()["status"], nr.PUBLISHED)
        self.assertIsNone(self.episode()["exception"])
        self.assertNotIn("s1e02_public", self.exceptions())
        resolved = nr.read_json(self.env.episodes_dir / nr.EXCEPTIONS_NAME)["resolved"]
        self.assertTrue(any(r["story_id"] == "s1e02_public" for r in resolved))

    def test_changed_caption_invalidates_the_approval(self):
        nr.run_until_settled(self.env)
        nr.approve_release(self.env, "s1e02_public", by="founder")
        cap = self.env.episodes_dir / "s1e02_public/captions.json"
        data = json.loads(cap.read_text())
        data["captions"]["instagram"] += " edited"
        cap.write_text(json.dumps(data))
        nr.tick(self.env)
        self.assertEqual(self.queue()["s1e02_public:instagram"], nr.READY_FOR_PUBLISH)
        pkg = nr.read_json(self.env.episodes_dir / nr.QUEUE_NAME)["packages"]["s1e02_public"]
        self.assertEqual(pkg["status"], "AWAITING_PUBLISH_APPROVAL")

    def test_human_executor_stops_only_at_generation_between_deliveries(self):
        nr.tick(self.env)
        st = self.episode()
        self.assertEqual(st["status"], nr.EXC_GENERATION_ACTION)
        self.assertEqual(st["exception"]["category"], 1)
        self.assertIn("record-asset", st["exception"]["human_action"])
        nr.simulated_executor(self.env)
        nr.tick(self.env)                     # delivery -> QA -> slide 2 handoff, no relay
        st = self.episode()
        self.assertEqual(st["slides"]["1"]["state"], "APPROVED")
        self.assertEqual((st["status"], st["exception"]["slide"]), (nr.EXC_GENERATION_ACTION, 2))

    def test_status_report_shows_exceptions_and_readiness(self):
        nr.run_until_settled(self.env)
        text = (self.env.episodes_dir / nr.STATUS_NAME).read_text()
        self.assertIn("| s1e02_public | EXC_PUBLISH_APPROVAL_REQUIRED | 6/6 |", text)
        self.assertIn("#3 Release package ready", text)
        self.assertIn("Activation readiness", text)


class UnattendedExecutor(_Sandbox):
    """With an approved zero-cost unattended executor, one tick runs every
    ordinary stage and the only stop is the publish approval."""
    unattended = True

    def _break_executor(self):
        cfg = nr.read_json(self.env.episodes_dir / nr.EXECUTOR_NAME)
        cfg["command"][1] = str(self.tmp / "missing.py")          # command exits non-zero
        nr.write_json_atomic(self.env.episodes_dir / nr.EXECUTOR_NAME, cfg)

    def test_single_tick_reaches_publish_approval_without_relays(self):
        result = nr.tick(self.env)
        self.assertEqual(result["episodes"], AT_APPROVAL)
        self.assertEqual(self.queue(), EXPECTED_QUEUE)
        prov = nr.read_json(nr.provenance_path(nr.asset_path(
            self.env, ec.load_lock("s1e02_public", self.env.locks_dir), 1)))
        self.assertEqual(prov["generated_by"], "SIMULATED_command_executor")
        self.assertTrue(prov["simulated"])

    def test_readiness_says_a_schedule_would_progress(self):
        self.only_public()
        self._break_executor()
        nr.tick(self.env)
        self.assertEqual(self.episode()["status"], nr.RETRY_SCHEDULED)
        self.assertTrue(nr.activation_readiness(self.env)["meaningful_progress"])

    def test_paid_or_unapproved_executor_is_never_run(self):
        for change in ({"cost": "paid"}, {"approved_by": ""}):
            with self.subTest(change=change):
                cfg = nr.read_json(self.env.episodes_dir / nr.EXECUTOR_NAME)
                cfg.update(cost="zero", approved_by="SIMULATED sandbox")
                cfg.update(change)
                nr.write_json_atomic(self.env.episodes_dir / nr.EXECUTOR_NAME, cfg)
                nr.tick(self.env)
                self.assertEqual(self.episode()["status"], nr.EXC_GENERATION_ACTION)
                self.assertFalse(nr.asset_path(
                    self.env, ec.load_lock("s1e02_public", self.env.locks_dir), 1).exists())

    def test_executor_failing_transiently_backs_off_then_escalates(self):
        self.only_public()
        self._break_executor()
        result = self.run_with_clock(deliver=False)
        self.assertEqual(result["episodes"]["s1e02_public"], nr.EXC_EXECUTOR_DOWN)
        self.assertEqual(self.episode()["exception"]["category"], 1)

    def test_simulated_executor_refused_outside_sandbox(self):
        self.env.sandbox = False
        self.assertEqual(nr.executor_status(self.env)["code"], nr.EXC_GENERATION_ACTION)


class InterruptedAndResumed(_Sandbox):
    """A crash at every durable step, then a fresh process resumes to exactly
    the uninterrupted result -- no duplicate verdict, no lost repair."""
    CHECKPOINTS = ("handoff_written", "qa_evidence_written", "verdict_recorded_in_story_state",
                   "before_archive", "variants_rendered")

    def _crash_run(self, name, occurrence, unattended=False):
        env = nr.build_sandbox(self.tmp / f"{name}_{occurrence}_{unattended}", unattended=unattended)
        seen = {"n": 0, "fired": False}

        def hook(cp):
            if cp == name:
                seen["n"] += 1
                if seen["n"] == occurrence and not seen["fired"]:
                    seen["fired"] = True
                    raise KeyboardInterrupt(f"crash at {cp}")
        env.checkpoint_hook = hook
        with self.assertRaises(KeyboardInterrupt):
            nr.run_until_settled(env, human_executor=not unattended)
        env.checkpoint_hook = None
        env.reviewer = nr.SimulatedReviewer(reject={(2, 1): ["identity"]})   # restarted process
        return env, nr.run_until_settled(env, human_executor=not unattended)

    def test_resume_after_crash_at_each_checkpoint(self):
        for unattended in (False, True):
            names = self.CHECKPOINTS + (("asset_generated",) if unattended else ())
            for name in names:
                occurrences = {"before_archive": (1,), "variants_rendered": (1, 2)}.get(name, (1, 3))
                for occ in occurrences:
                    with self.subTest(unattended=unattended, checkpoint=name, occurrence=occ):
                        env, result = self._crash_run(name, occ, unattended)
                        self.assertEqual(result["episodes"], AT_APPROVAL)
                        self.assertEqual(self.queue(env), EXPECTED_QUEUE)
                        rej = [(r["slide_index"], r["attempt"]) for r in
                               self.sc_state(env)["retry_state"]["rejected"]]
                        self.assertEqual(rej, [(2, 1)])
                        self.assertEqual(len(self.evidence(env)), 7)

    def test_tick_is_idempotent(self):
        nr.run_until_settled(self.env)
        queue = (self.env.episodes_dir / nr.QUEUE_NAME).read_text()
        handoffs = {p.name: p.read_text() for p in (self.env.episodes_dir / "s1e02_public/handoff").iterdir()}
        nr.tick(self.env)
        nr.tick(self.env)
        self.assertEqual((self.env.episodes_dir / nr.QUEUE_NAME).read_text(), queue)
        self.assertEqual({p.name: p.read_text() for p in
                          (self.env.episodes_dir / "s1e02_public/handoff").iterdir()}, handoffs)

    def test_overlapping_tick_is_refused(self):
        self.env.episodes_dir.mkdir(parents=True, exist_ok=True)
        with open(self.env.episodes_dir / nr.LOCK_NAME, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertFalse(nr.tick(self.env)["ran"])


class ExceptionPolicy(_Sandbox):
    def setUp(self):
        super().setUp()
        self.only_public()

    def test_transient_reviewer_outage_resumes_without_an_exception(self):
        self.env.reviewer = nr.SimulatedReviewer(reject={(2, 1): ["identity"]}, outages=2)
        nr.tick(self.env)
        nr.simulated_executor(self.env)
        nr.tick(self.env)
        st = self.episode()
        self.assertEqual(st["status"], nr.RETRY_SCHEDULED)
        self.assertIsNone(st["exception"])
        self.assertEqual(self.exceptions(), {})
        nr.tick(self.env)                                  # backoff not due: no call
        self.assertEqual(self.env.reviewer.calls, 1)
        result = self.run_with_clock()
        self.assertEqual(result["episodes"]["s1e02_public"], nr.EXC_PUBLISH_APPROVAL)

    def test_transient_failures_past_the_bound_become_exception_4(self):
        self.env.reviewer = nr.SimulatedReviewer(outages=10 ** 6)
        result = self.run_with_clock()
        self.assertEqual(result["episodes"]["s1e02_public"], nr.EXC_NON_RETRYABLE)
        self.assertEqual(self.env.reviewer.calls, nr.MAX_TRANSIENT_FAILURES)
        self.assertIn("unblock", self.episode()["exception"]["resumes"])
        self.env.reviewer = nr.SimulatedReviewer()
        nr.unblock(self.env, "s1e02_public")
        self.assertEqual(self.run_with_clock()["episodes"]["s1e02_public"], nr.EXC_PUBLISH_APPROVAL)

    def test_credential_failure_stops_at_once_and_is_not_retried(self):
        self.env.reviewer = nr.SimulatedReviewer(fatal=nr._classify_llm(
            RuntimeError("claude reported an error: not logged in"), "vision reviewer"))
        self.run_with_clock(rounds=6)
        st = self.episode()
        self.assertEqual(st["status"], nr.EXC_NON_RETRYABLE)
        self.assertEqual(st["exception"]["kind"], "credential")
        self.assertEqual(self.env.reviewer.calls, 1)
        self.assertEqual(self.exceptions()["s1e02_public"]["category"], 4)

    def test_quota_is_transient_and_missing_cli_is_fatal_with_auto_resume(self):
        self.assertIsInstance(nr._classify_llm(RuntimeError("usage limit reached"), "x"), nr.Unavailable)
        missing = nr._classify_llm(RuntimeError("claude CLI not found on PATH"), "x")
        self.assertIsInstance(missing, nr.Fatal)
        self.assertEqual(missing.probe, "claude")

    def test_missing_renderer_tool_resumes_by_itself_once_installed(self):
        installed = {"ok": False}

        class ToolRenderer(nr.SimulatedRenderer):
            def render(self, job):
                if not installed["ok"]:
                    raise nr.Fatal("renderer: ffmpeg is not installed", kind="renderer", probe="fake_tool")
                return super().render(job)
        original = nr.probe_ok
        nr.probe_ok = lambda name, env=None: installed["ok"] if name == "fake_tool" else original(name, env)
        try:
            self.env.renderer = ToolRenderer()
            result = self.run_with_clock(rounds=12)
            self.assertEqual(result["episodes"]["s1e02_public"], nr.EXC_NON_RETRYABLE)
            self.assertIn("automatically", self.episode()["exception"]["resumes"])
            self.assertFalse(nr.activation_readiness(self.env)["meaningful_progress"])
            installed["ok"] = True                                   # someone installs it
            self.assertTrue(nr.activation_readiness(self.env)["meaningful_progress"])
            self.assertEqual(nr.tick(self.env)["episodes"]["s1e02_public"], nr.EXC_PUBLISH_APPROVAL)
        finally:
            nr.probe_ok = original

    def test_three_failed_qa_attempts_stop_at_exception_2(self):
        self.env.reviewer = nr.SimulatedReviewer(reject={(1, k): ["canonical_ears_visible"] for k in (1, 2, 3)})
        result = nr.run_until_settled(self.env)
        st = self.episode()
        self.assertEqual(result["episodes"]["s1e02_public"], nr.EXC_QA_BUDGET)
        self.assertEqual((st["exception"]["category"], st["exception"]["slide"]), (2, 1))
        self.assertEqual(len(self.sc_state()["retry_state"]["rejected"]), 3)
        self.assertNotIn("s1e02_public:instagram", self.queue())
        for _ in range(3):                         # stays stopped: no automatic 4th attempt
            nr.tick(self.env)
            nr.simulated_executor(self.env)
        self.assertEqual(len(self.sc_state()["retry_state"]["rejected"]), 3)
        nr.grant_attempt(self.env, "s1e02_public", 1, by="founder")     # human allows one more
        self.assertEqual(nr.run_until_settled(self.env)["episodes"]["s1e02_public"], nr.EXC_PUBLISH_APPROVAL)
        with self.assertRaises(nr.RunnerError):
            nr.grant_attempt(self.env, "s1e02_public", 1, by="founder")

    def test_invalid_lock_is_exception_4(self):
        lock = json.loads((self.env.locks_dir / "s1e02_public.json").read_text())
        lock["aspect_ratio"] = "9:16"
        (self.env.locks_dir / "s1e02_public.json").write_text(json.dumps(lock))
        nr.tick(self.env)
        self.assertEqual(self.episode()["exception"]["kind"], "lock")


class NoEvidenceNoProgress(_Sandbox):
    def _slot1(self):
        nr.tick(self.env)
        return nr.asset_path(self.env, ec.load_lock("s1e02_public", self.env.locks_dir), 1)

    def _make(self, w, h):
        p = self.tmp / f"{w}x{h}.png"
        nr._pattern_png(p, w, h, 3)
        return p

    def test_image_without_provenance_is_never_reviewed(self):
        nr._pattern_png(self._slot1(), 1080, 1350, 1)
        nr.tick(self.env)
        st = self.episode()
        self.assertEqual((st["slides"]["1"]["state"], st["status"]), ("ASSET_REFUSED", nr.EXC_GENERATION_ACTION))
        self.assertEqual(self.env.reviewer.calls, 0)
        self.assertEqual(self.evidence(), [])

    def test_flat_image_is_rejected_by_the_technical_gate_not_the_reviewer(self):
        self._slot1()
        flat = self.tmp / "flat.png"
        ec._placeholder_png(flat)
        nr.record_asset(self.env, "s1e02_public", 1, flat, by="t", provider="t", simulated=True)
        nr.tick(self.env)
        ev = json.loads((self.env.episodes_dir / "s1e02_public/qa/slide1_attempt1.json").read_text())
        self.assertEqual((ev["reviewer"], ev["verdict"]), ("deterministic_technical_gate", "REJECTED"))
        self.assertEqual(self.env.reviewer.calls, 0)

    def test_wrong_size_is_rejected(self):
        ok, why = nr.technical_gate(self._make(1080, 1920), "4:5")
        self.assertFalse(ok)
        self.assertIn("expected 1080x1350", why)

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
        self.only_public()
        nr.tick(self.env)
        self.assertIsNone(self.episode()["fanvue_chapter"])
        self.assertIn("NO_APPROVED_LOCK", (self.env.episodes_dir / nr.STATUS_NAME).read_text())


class Readiness(_Sandbox):
    def test_human_executor_means_no_meaningful_scheduled_progress(self):
        self.only_public()
        nr.tick(self.env)
        ready = nr.activation_readiness(self.env)
        self.assertFalse(ready["meaningful_progress"])
        self.assertIn("WOULD NOT MAKE MEANINGFUL PROGRESS", ready["verdict"])
        self.assertTrue(ready["blockers"][0].startswith("image generation:"))
        self.assertFalse(ready["scheduler_installed_by_this_repo"])

    def test_pending_ordinary_work_means_progress(self):
        self.only_public()
        nr.tick(self.env)
        st = self.episode()
        st["status"], st["exception"] = nr.IN_PROGRESS, None
        nr.write_json_atomic(nr.state_path(self.env, "s1e02_public"), st)
        self.assertTrue(nr.activation_readiness(self.env)["meaningful_progress"])


class Boundary(unittest.TestCase):
    ALLOWED = {"__future__", "fcntl", "hashlib", "json", "os", "shutil", "struct", "subprocess",
               "sys", "tempfile", "zlib", "dataclasses", "datetime", "pathlib", "carousel_handoff",
               "channels", "episode_coordinator", "story_continuity", "claude_client",
               "PIL"}   # PIL: availability probe only

    def test_imports_nothing_that_can_publish_or_touch_reddit(self):
        tree = ast.parse((ROOT / "nyx_runner.py").read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        self.assertEqual(names - self.ALLOWED, set())

    def test_only_confirm_published_can_mark_an_entry_published(self):
        src = (ROOT / "nyx_runner.py").read_text()
        self.assertEqual(src.count("status=PUBLISHED_CONFIRMED"), 1)
        self.assertLess(src.index("def confirm_published"), src.index("status=PUBLISHED_CONFIRMED"))

    def test_real_repo_configuration(self):
        reg = json.loads((ROOT / "episodes" / nr.REGISTRY_NAME).read_text())
        for e in reg["episodes"]:
            self.assertTrue(e.get("approved_by"))
            self.assertTrue(ec.lock_path(e["story_id"]).is_file())
        cfg = json.loads((ROOT / "episodes" / nr.EXECUTOR_NAME).read_text())
        self.assertEqual(cfg["mode"], "human_manual")
        self.assertEqual(json.loads(ec.lock_path("s1e02_public").read_text())["retry"]
                         ["max_attempts_per_slide"], nr.QA_ATTEMPT_BUDGET)


if __name__ == "__main__":
    unittest.main()
