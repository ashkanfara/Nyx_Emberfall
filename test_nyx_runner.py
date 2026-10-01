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

    def test_human_executor_gets_one_batch_and_two_sittings(self):
        nr.tick(self.env)
        st = self.episode()
        self.assertEqual((st["status"], st["exception"]["category"]), (nr.EXC_GENERATION_ACTION, 1))
        self.assertEqual(st["exception"]["slides"], [1, 2, 3, 4, 5, 6])
        self.assertIn("CURRENT.md", st["exception"]["human_action"])
        nr.simulated_executor(self.env)       # sitting 1: all six images dropped
        nr.tick(self.env)                     # ingest -> QA 1 ok, 2 rejected -> repair batch, no relay
        st = self.episode()
        self.assertEqual(st["slides"]["1"]["state"], "APPROVED")
        self.assertEqual(st["exception"]["slides"], [2])
        nr.simulated_executor(self.env)       # sitting 2: the one repair
        self.assertEqual(nr.tick(self.env)["episodes"]["s1e02_public"], nr.EXC_PUBLISH_APPROVAL)

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
                   "before_archive", "variants_rendered", "batch_written", "drop_ingested")

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
            names = (self.CHECKPOINTS + ("asset_generated",) if unattended else self.CHECKPOINTS)
            if unattended:
                names = tuple(n for n in names if n not in ("batch_written", "drop_ingested"))
            for name in names:
                occurrences = {"before_archive": (1,), "variants_rendered": (1, 2),
                               "batch_written": (1, 2)}.get(name, (1, 3))
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
        nr.record_asset(self.env, "s1e02_public", 1, self._make(1080, 1350), by="t", provider="t",
                        simulated=True)
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


class ZeroSpendBatch(_Sandbox):
    """Founder 2026-10-01: zero-spend mode. Every currently needed image goes out
    in ONE compact ChatGPT batch; dropped images are picked up with no relay."""

    def batch(self):
        return nr.current_batch(self.env, "s1e02_public")

    def setUp(self):
        super().setUp()
        self.only_public()
        nr.tick(self.env)

    def test_batch_contains_every_needed_image_with_dimensions_destinations_and_refs(self):
        b = self.batch()
        self.assertEqual([e["slide"] for e in b["entries"]], [1, 2, 3, 4, 5, 6])
        self.assertEqual((b["output"]["width"], b["output"]["height"]), (1080, 1350))
        self.assertEqual(b["chatgpt_output"], nr.CHATGPT_OUTPUT)
        self.assertEqual([Path(r).name for r in b["identity_references"]],
                         ["gen_ref_1_face_closeup.png", "gen_ref_2_head_fox_ears.png",
                          "gen_ref_3_fox_ear_detail.png"])           # never the full reference sheets
        self.assertTrue(all(e["destination"].endswith(f"item33_slide{e['slide']}.png") for e in b["entries"]))
        self.assertEqual(b["entries"][0]["character_presence"], "absent")
        self.assertTrue(all(e["env_master"] == f"batch:{b['batch_id']}" for e in b["entries"][1:]))
        bdir = self.env.episodes_dir / "s1e02_public/batch" / b["batch_id"]
        for e in b["entries"]:                                         # full prompts kept, sha-bound
            text = (bdir / "prompts" / f"slide{e['slide']}.txt").read_text().rstrip("\n")
            self.assertEqual(nr.sha256_text(text), e["prompt_sha256"])

    def test_batch_document_is_compact_and_orders_the_absent_slide_before_nyx(self):
        md = (self.env.episodes_dir / "s1e02_public/batch/CURRENT.md").read_text()
        full = sum(len(p.read_text()) for p in
                   (self.env.episodes_dir / "s1e02_public/batch" / self.batch()["batch_id"] / "prompts").iterdir())
        self.assertLess(len(md), full * 0.6)
        self.assertLess(md.index("slide 1 (no Nyx in frame)"), md.index("Nyx primer"))
        self.assertLess(md.index("Nyx primer"), md.index("-> save as `slide2.png`"))
        slide1 = md[md.index("slide 1 (no Nyx in frame)"):md.index("Nyx primer")]
        self.assertNotIn("gen_ref_1", slide1)
        self.assertNotIn("beauty mark", slide1)
        self.assertIn("portrait 2:3 (1024x1536)", md)

    def test_batch_is_stable_across_ticks(self):
        first = self.batch()["batch_id"]
        nr.tick(self.env)
        nr.tick(self.env)
        self.assertEqual(self.batch()["batch_id"], first)
        self.assertEqual(len(list((self.env.episodes_dir / "s1e02_public/batch").glob("*/manifest.json"))), 1)

    @unittest.skipUnless(nr.conform_ok(), "needs Pillow or macOS sips")
    def test_chatgpt_portrait_is_conformed_to_the_canvas(self):
        sitting = self.batch()["batch_id"]
        nr.simulated_executor(self.env, chatgpt_size=True)
        nr.tick(self.env)
        lock = ec.load_lock("s1e02_public", self.env.locks_dir)
        info = nr.png_info(nr.asset_path(self.env, lock, 1))
        self.assertEqual((info["width"], info["height"]), (1080, 1350))
        prov = nr.read_json(nr.provenance_path(nr.asset_path(self.env, lock, 1)))
        self.assertIn("1024x1536 centre-cropped to 1024x1280", prov["conform"])
        self.assertEqual(prov["batch_id"], sitting)
        self.assertTrue((nr.drop_dir(self.env, lock) / "ingested").is_dir())

    def test_stray_stale_and_reviewed_drops_are_never_used(self):
        lock = ec.load_lock("s1e02_public", self.env.locks_dir)
        drop = nr.drop_dir(self.env, lock)
        nr._pattern_png(drop / "slide9.png", 1080, 1350, 1)          # not in the batch
        nr._pattern_png(drop / "cover.png", 1080, 1350, 1)           # not a slide name: ignored
        old = drop / "slide1.png"
        nr._pattern_png(old, 1080, 1350, 1)
        import os
        os.utime(old, (1, 1))                                       # older than the batch
        nr.tick(self.env)
        unexpected = sorted(p.name.split("_", 1)[1] for p in (drop / "unexpected").iterdir())
        self.assertEqual(unexpected, ["slide1.png", "slide9.png"])
        self.assertEqual(self.env.reviewer.calls, 0)
        self.assertFalse(nr.asset_path(self.env, lock, 1).exists())

    def test_partial_sitting_continues_and_rebatches_only_what_is_left(self):
        lock = ec.load_lock("s1e02_public", self.env.locks_dir)
        nr._pattern_png(nr.drop_dir(self.env, lock) / "slide1.png", 1080, 1350, 11)
        nr.tick(self.env)
        st = self.episode()
        self.assertEqual(st["slides"]["1"]["state"], "APPROVED")
        b = self.batch()
        self.assertEqual([e["slide"] for e in b["entries"]], [2, 3, 4, 5, 6])
        self.assertTrue(all(e["env_master"].startswith("sha:") for e in b["entries"]))
        md = (self.env.episodes_dir / "s1e02_public/batch/CURRENT.md").read_text()
        first = md[md.index("## Message 1"):md.index("## Message 2")]
        self.assertIn("item33_slide1.png", first)                   # approved room master attached
        self.assertIn("gen_ref_1_face_closeup.png", first)          # every slide shows Nyx

    def test_rejected_slide_1_supersedes_later_images_without_spending_attempts(self):
        self.env.reviewer = nr.SimulatedReviewer(reject={(1, 1): ["environment_continuity"]})
        nr.simulated_executor(self.env)
        nr.tick(self.env)
        sc_state = self.sc_state()
        self.assertEqual(sc_state["retry_state"]["attempts"], {"1": 1})   # only slide 1 spent one
        b = self.batch()
        self.assertEqual([(e["slide"], e["attempt"]) for e in b["entries"]],
                         [(1, 2), (2, 1), (3, 1), (4, 1), (5, 1), (6, 1)])
        lock = ec.load_lock("s1e02_public", self.env.locks_dir)
        self.assertEqual(len(list((nr.package_dir(self.env, lock) / "superseded").glob("*.png"))), 5)
        nr.simulated_executor(self.env)
        self.assertEqual(nr.tick(self.env)["episodes"]["s1e02_public"], nr.EXC_PUBLISH_APPROVAL)

    def test_missing_conform_tool_stops_then_resumes_by_itself(self):
        lock = ec.load_lock("s1e02_public", self.env.locks_dir)
        nr._pattern_png(nr.drop_dir(self.env, lock) / "slide1.png", 1024, 1536, 11)
        real_conform, real_ok = nr.conform_image, nr.probe_ok
        available = {"ok": False}

        def no_tool(src, dest, aspect):
            if not available["ok"]:
                raise nr.Fatal("conform: neither Pillow nor macOS sips", kind="renderer", probe="conform")
            return real_conform(src, dest, aspect)
        nr.conform_image = no_tool
        nr.probe_ok = lambda name, env=None: available["ok"] if name == "conform" else real_ok(name, env)
        try:
            nr.tick(self.env)
            st = self.episode()
            self.assertEqual((st["status"], st["exception"]["category"]), (nr.EXC_NON_RETRYABLE, 4))
            self.assertTrue((nr.drop_dir(self.env, lock) / "slide1.png").is_file())   # kept, not lost
            available["ok"] = True
            if not nr.conform_ok():
                self.skipTest("resume half needs Pillow or sips")
            nr.tick(self.env)
            self.assertEqual(self.episode()["slides"]["1"]["state"], "APPROVED")
        finally:
            nr.conform_image, nr.probe_ok = real_conform, real_ok

    def test_generation_executor_stays_human_manual_in_the_repo(self):
        self.assertEqual(json.loads((ROOT / "episodes" / nr.EXECUTOR_NAME).read_text())["mode"],
                         "human_manual")


class Readiness(_Sandbox):
    def test_human_executor_means_no_meaningful_scheduled_progress(self):
        self.only_public()
        nr.tick(self.env)
        ready = nr.activation_readiness(self.env)
        self.assertFalse(ready["meaningful_progress"])
        self.assertIn("WOULD NOT MAKE MEANINGFUL PROGRESS", ready["verdict"])
        self.assertTrue(ready["blockers"][0].startswith("image generation (s1e02_public):"))
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

    # Founder 2026-10-01 trial: the official image provider and the Fanvue publisher may be
    # imported ONLY lazily, inside the four trial functions that the opt-in switches gate.
    TRIAL_ONLY = {"openai_image_provider": {"run_openai_trial"},
                  "openai_runtime": {"trial_generation_status", "trial_activation"},   # has_credentials only
                  "fanvue_media": {"publish"}, "fanvue_posts": {"publish"},
                  "fanvue_runtime": {"available"}}
    NEVER = {"nyx_instagram", "instagram_publish", "instagram_runtime", "reddit_ops", "nyx_reddit",
             "higgsfield_image_provider", "wan_provider", "local_image_provider", "requests",
             "urllib", "webbrowser", "playwright", "selenium"}

    def _imports(self):
        tree = ast.parse((ROOT / "nyx_runner.py").read_text())
        found = []                                  # (module, enclosing function or None)

        def visit(node, fn):
            for child in ast.iter_child_nodes(node):
                inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
                if isinstance(child, ast.Import):
                    found.extend((a.name.split(".")[0], fn) for a in child.names)
                elif isinstance(child, ast.ImportFrom):
                    found.append(((child.module or "").split(".")[0], fn))
                visit(child, inner)
        visit(tree, None)
        return found

    def test_imports_stay_inside_their_boundary(self):
        for mod, fn in self._imports():
            with self.subTest(module=mod, function=fn):
                self.assertNotIn(mod, self.NEVER)
                if mod in self.TRIAL_ONLY:
                    self.assertIn(fn, self.TRIAL_ONLY[mod])
                else:
                    self.assertIn(mod, self.ALLOWED)

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
