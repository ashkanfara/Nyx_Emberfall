"""Tests for the founder's two-episode automation trial (2026-10-01): the capped
official image provider and opt-in auto-publish. Everything runs in a temp
sandbox with a FAKE OpenAI client and a FAKE Fanvue publisher -- no key, no
network, no spend, no post."""

import base64
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import episode_coordinator as ec
import nyx_runner as nr
import openai_runtime

ROOT = Path(__file__).resolve().parent

try:
    from PIL import Image
except ImportError:                                   # pragma: no cover - environment
    Image = None

USAGE = {"input_tokens": 6000, "output_tokens": 4000,
         "input_tokens_details": {"text_tokens": 3000, "image_tokens": 3000}}
USAGE_USD = round((3000 * 5 + 3000 * 8 + 4000 * 30) / 1e6, 5)        # 0.159


def _png(w=1088, h=1360, seed=1):
    img = Image.new("RGB", (w, h))
    img.putdata([((x * 7 + seed) % 256, (y * 3) % 256, (x + y) % 256)
                 for y in range(h) for x in range(w)][: w * h])
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class FakeImages:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail
        self._png = _png(272, 340)                     # small, scaled up by to_output

    def _result(self):
        return {"data": [{"b64_json": base64.b64encode(self._png).decode()}], "usage": USAGE,
                "created": 1}

    def edit(self, **kw):
        self.calls.append(("edit", kw))
        if self.fail:
            raise self.fail
        return mock.Mock(model_dump=self._result)

    def generate(self, **kw):
        self.calls.append(("generate", kw))
        if self.fail:
            raise self.fail
        return mock.Mock(model_dump=self._result)


class FakeClient:
    def __init__(self, fail=None):
        self.images = FakeImages(fail)


class FakeFanvue:
    name, simulated = "SIMULATED_fanvue", True

    def __init__(self):
        self.posts = []

    def available(self):
        return True, "simulated"

    def publish(self, entry, files):
        self.posts.append((entry["story_id"], [f.name for f in files]))
        return {"post_id": f"post-{len(self.posts)}", "audience": "followers-and-subscribers",
                "media_count": len(files)}


@unittest.skipUnless(Image is not None, "the OpenAI provider post-processes with Pillow")
class _Trial(unittest.TestCase):
    gen = {"enabled": True, "enabled_by": "founder test", "provider": "openai", "quality": "medium",
           "cap_usd_per_episode": 5.0}
    pub = {"enabled": False, "enabled_by": None, "routes": ["fanvue"]}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="trial_test_"))
        self.env = nr.build_sandbox(self.tmp)
        self.env.image_client = FakeClient()
        self.env.publishers = {"fanvue": FakeFanvue()}
        self.write_trial()
        patcher = mock.patch.object(openai_runtime, "has_credentials", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_trial(self, **over):
        cfg = {"episodes": ["s1e02_public"], "image_generation": dict(self.gen),
               "auto_publish": dict(self.pub)}
        for k, v in over.items():
            cfg[k] = {**cfg.get(k, {}), **v} if isinstance(v, dict) else v
        nr.write_json_atomic(self.env.episodes_dir / nr.TRIAL_NAME, cfg)

    def run_all(self, rounds=12):
        result = None
        for _ in range(rounds):
            result = nr.tick(self.env)
            self.env._clock_state["now"] += nr.timedelta(hours=1)
        return result

    def queue(self):
        return nr.read_json(self.env.episodes_dir / nr.QUEUE_NAME, {"entries": {}})["entries"]

    def state(self, story="s1e02_public"):
        return nr.read_json(nr.state_path(self.env, story))


class CappedOfficialProvider(_Trial):
    def test_generation_runs_unattended_to_the_release_package(self):
        result = self.run_all()
        self.assertEqual(result["episodes"], {"s1e02_public": nr.EXC_PUBLISH_APPROVAL,
                                              "sim_s1e02_fanvue": nr.EXC_PUBLISH_APPROVAL})
        calls = self.env.image_client.images.calls
        # 6 + slide-2 repair, for the episode and for its chapter (same scripted reviewer)
        self.assertEqual(len(calls), 7 + 7)
        spend = nr.spend_summary(self.env, "s1e02_public")  # chapter shares its parent's cap
        self.assertEqual(spend["calls"], 14)
        self.assertAlmostEqual(spend["committed_usd"], round(14 * USAGE_USD, 4), places=3)
        self.assertLessEqual(spend["committed_usd"], nr.TRIAL_HARD_CAP_USD)
        prov = nr.read_json(nr.provenance_path(nr.asset_path(
            self.env, ec.load_lock("s1e02_public", self.env.locks_dir), 3)))
        self.assertEqual((prov["provider"], prov["generated_by"]), ("openai", "openai_images_api"))

    def test_absent_slide_one_is_generated_without_any_identity_reference(self):
        self.run_all(rounds=1)
        kind, kw = self.env.image_client.images.calls[0]
        self.assertEqual(kind, "generate")                  # no images attached at all
        self.assertNotIn("REFERENCE IMAGES", kw["prompt"])
        kind2, kw2 = self.env.image_client.images.calls[1]
        self.assertEqual(kind2, "edit")
        self.assertTrue(kw2["image"][0].name.endswith("gen_ref_1_face_closeup.png"))
        self.assertEqual(kw2["size"], "1088x1360")

    def test_cap_is_hard_and_config_can_only_lower_it(self):
        self.write_trial(image_generation={"cap_usd_per_episode": 99})
        self.assertEqual(nr.trial_cap_usd(self.env), nr.TRIAL_HARD_CAP_USD)
        self.write_trial(image_generation={"cap_usd_per_episode": 0.7})
        result = self.run_all()
        spend = nr.spend_summary(self.env, "s1e02_public")
        self.assertLessEqual(spend["committed_usd"], 0.7)
        st = self.state()
        self.assertEqual(st["status"], nr.EXC_GENERATION_ACTION)         # falls back to the batch
        self.assertIn("cap", st["exception"]["detail"])
        self.assertIsNotNone(nr.current_batch(self.env, "s1e02_public"))
        self.assertNotEqual(result["episodes"]["s1e02_public"], nr.EXC_NON_RETRYABLE)

    def test_unknown_outcome_is_counted_and_never_resent(self):
        self.env.image_client = FakeClient(fail=TimeoutError("read timed out"))
        self.run_all(rounds=4)
        st = self.state()
        self.assertEqual((st["status"], st["exception"]["kind"]), (nr.EXC_NON_RETRYABLE, "spend"))
        self.assertEqual(len(self.env.image_client.images.calls), 1)
        spend = nr.spend_summary(self.env, "s1e02_public")
        self.assertEqual(len(spend["unknown_outcomes"]), 1)
        self.assertGreater(spend["committed_usd"], 0)                     # full estimate counted
        nr.unblock(self.env, "s1e02_public")                   # unblock alone does not reopen spend
        nr.tick(self.env)
        self.assertEqual(len(self.env.image_client.images.calls), 1)
        call_id = spend["unknown_outcomes"][0]["call_id"]
        with self.assertRaises(nr.RunnerError):
            nr.settle_spend(self.env, "s1e02_public", call_id, 0.2, by="")
        nr.settle_spend(self.env, "s1e02_public", call_id, 0.2, by="founder")   # read off the dashboard
        self.env.image_client = FakeClient()
        nr.unblock(self.env, "s1e02_public")
        nr.tick(self.env)
        self.assertGreater(len(self.env.image_client.images.calls), 0)   # a NEW call, next attempt
        self.assertEqual(nr.spend_summary(self.env, "s1e02_public")["unknown_outcomes"], [])

    def test_crash_after_reserving_never_resends(self):
        def crash(name):
            if name == "spend_reserved":
                raise KeyboardInterrupt
        self.env.checkpoint_hook = crash
        with self.assertRaises(KeyboardInterrupt):
            nr.tick(self.env)
        self.env.checkpoint_hook = None
        nr.tick(self.env)
        self.assertEqual(len(self.env.image_client.images.calls), 0)
        self.assertEqual(self.state()["exception"]["kind"], "spend")

    def test_no_key_is_executor_unavailable_and_resumes_by_itself(self):
        with mock.patch.object(openai_runtime, "has_credentials", return_value=False):
            nr.tick(self.env)
            st = self.state()
            self.assertEqual((st["status"], st["exception"]["category"]), (nr.EXC_EXECUTOR_DOWN, 1))
            self.assertIn("openai_runtime.py store", st["exception"]["detail"])
        nr.tick(self.env)                                       # key stored -> continues
        self.assertGreater(len(self.env.image_client.images.calls), 0)

    def test_switch_needs_enabled_by_and_only_two_episodes_count(self):
        self.write_trial(image_generation={"enabled_by": ""})
        self.assertIsNone(nr.trial_generation_status(self.env, "s1e02_public"))
        self.write_trial(episodes=["a", "b", "s1e02_public"])
        self.assertIsNone(nr.trial_generation_status(self.env, "s1e02_public"))


class GenerationOffByDefault(_Trial):
    gen = {"enabled": False, "enabled_by": None, "provider": "openai", "cap_usd_per_episode": 5.0}

    def test_switch_off_means_zero_spend_batch_mode(self):
        nr.tick(self.env)
        self.assertEqual(self.state()["status"], nr.EXC_GENERATION_ACTION)
        self.assertEqual(self.env.image_client.images.calls, [])
        self.assertFalse((self.env.episodes_dir / "s1e02_public" / nr.SPEND_LEDGER).exists())


class OptInAutoPublish(_Trial):
    pub = {"enabled": True, "enabled_by": "founder test", "routes": ["fanvue"]}

    def test_verified_route_publishes_and_unverified_routes_stay_held(self):
        result = self.run_all()
        q = self.queue()
        self.assertEqual(q["sim_s1e02_fanvue:fanvue"]["status"], nr.PUBLISHED_AUTOMATICALLY)
        self.assertEqual(q["sim_s1e02_fanvue:fanvue"]["post_id"], "post-1")
        self.assertEqual(result["episodes"]["sim_s1e02_fanvue"], nr.PUBLISHED)
        for key in ("s1e02_public:instagram", "s1e02_public:tiktok"):
            self.assertEqual(q[key]["status"], nr.READY_FOR_PUBLISH)
            self.assertIn("media-hosting", q[key]["auto_publish_hold"])
        self.assertEqual(q["s1e02_public:x"]["status"], nr.HELD_NO_ROUTE)
        self.assertEqual(result["episodes"]["s1e02_public"], nr.EXC_PUBLISH_APPROVAL)
        self.assertEqual(len(self.env.publishers["fanvue"].posts), 1)     # never twice
        self.assertEqual(len(self.env.publishers["fanvue"].posts[0][1]), 6)

    def test_uncertain_publish_stops_instead_of_posting_twice(self):
        def crash(name):
            if name == "publish_attempt_marked":
                raise KeyboardInterrupt
        self.env.checkpoint_hook = crash
        with self.assertRaises(KeyboardInterrupt):
            self.run_all()
        self.env.checkpoint_hook = None
        self.run_all(rounds=2)
        st = self.state("sim_s1e02_fanvue")
        self.assertEqual((st["status"], st["exception"]["kind"]), (nr.EXC_NON_RETRYABLE, "publish"))
        self.assertEqual(self.env.publishers["fanvue"].posts, [])

    def test_simulated_publisher_refused_outside_sandbox(self):
        self.env.sandbox = False
        self.assertIn("SIMULATED", nr.route_hold_reason(self.env, "fanvue", ["fanvue"]))

    def test_unlisted_route_is_held(self):
        self.assertIn("not listed", nr.route_hold_reason(self.env, "fanvue", []))


class AutoPublishOffByDefault(_Trial):
    def test_off_means_exception_3_and_no_publisher_call(self):
        self.run_all()
        self.assertEqual(self.state("sim_s1e02_fanvue")["status"], nr.EXC_PUBLISH_APPROVAL)
        self.assertEqual(self.env.publishers["fanvue"].posts, [])


class ShippedConfiguration(unittest.TestCase):
    def test_trial_ships_with_both_switches_off(self):
        cfg = json.loads((ROOT / "episodes" / nr.TRIAL_NAME).read_text())
        self.assertFalse(cfg["image_generation"]["enabled"])
        self.assertFalse(cfg["auto_publish"]["enabled"])
        self.assertLessEqual(cfg["image_generation"]["cap_usd_per_episode"], nr.TRIAL_HARD_CAP_USD)
        self.assertLessEqual(len(cfg["episodes"]), nr.TRIAL_MAX_EPISODES)
        self.assertEqual(json.loads((ROOT / "episodes" / nr.EXECUTOR_NAME).read_text())["mode"],
                         "human_manual")

    def test_no_credentials_live_in_tracked_config(self):
        for name in (nr.TRIAL_NAME, nr.EXECUTOR_NAME):
            text = (ROOT / "episodes" / name).read_text()
            self.assertNotIn("sk-", text)
            self.assertNotIn("access_token", text)


if __name__ == "__main__":
    unittest.main()
