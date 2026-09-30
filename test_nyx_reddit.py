"""Tests for the Reddit workstream (nyx_reddit/, reddit_ops.py).

The properties that matter: nothing becomes postable before every rule is
browser-verified and fresh; no Fanvue link, NSFW, real-person framing or
public-pipeline image can pass; the 30-day test halts on the first bad signal;
and the optional stage can never break the main pipeline.

Run: python3 -m unittest test_nyx_reddit
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import channels
from nyx_reddit import gate, ledger, matrix, rules, variant
from nyx_reddit.store import Store

ROOT = Path(__file__).resolve().parent
LOCK = "brand/story_locks/s1e02_public.json"
FANVUE_LOCK = "brand/story_locks/s1e01_fanvue_part1.json"

VERIFIED = {
    "ai_content": {"allowed": True, "disclosure_required": True, "disclosure_format": "AI"},
    "self_promotion": {"mode": "ratio"},
    "nsfw": {"allowed": False},
    "links": {"external_allowed": False, "fanvue_allowed": False},
    "account_minimums": {"min_age_days": None, "min_karma": None},
    "flair": {"required": True, "options": ["Series", "Image - Other"]},
    "frequency": {"max_posts": 1, "per_days": 1},
}


class RedditTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for rel in ("nyx_reddit/data/communities.json", "brand/nyx_identity/identity_spec.json", LOCK, FANVUE_LOCK):
            (self.tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / rel, self.tmp / rel)
        self.store = Store(self.tmp)
        self.env = mock.patch.dict(os.environ, {"NYX_TODAY": "2026-10-01"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp)

    def verify_all(self, community="r/aiArt", when="2026-09-30"):
        for field, values in VERIFIED.items():
            rules.record_rule(self.store, community, field, values,
                              f"https://www.reddit.com/{community}/about/rules/", "quoted rule text",
                              "Ashkan", verified_at=when)

    def ready_everything(self):
        """A draft that should pass: verified sub, account, warm-up, attached image."""
        self.verify_all()
        rules.classify(self.store, "r/aiArt", "approved_native", "Ashkan")
        ledger.record_account(self.store, 90, 400, True, "Ashkan")
        act = self.store.activity()
        for i in range(10):
            act["comments"].append({"community": "r/aiArt", "url": f"https://www.reddit.com/r/aiArt/c/{i}",
                                    "summary": "useful", "removed": False, "date": f"2026-09-{10 + i * 2:02d}",
                                    "by": "Ashkan"})
        self.store.save_activity(act)
        variant.prepare(LOCK, self.store)
        d = self.store.draft("s1e02_public__aiart")
        d["flair"] = "Series"
        d["title"] = "[AI] " + d["title"]
        self.store.save_draft(d)
        for img in d["images"]:
            p = self.store.assets / d["id"] / f"{img['role']}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(os.urandom(64))
            ledger.attach_image(self.store, d["id"], img["role"], str(p), True, "pass", "Ashkan")
        return self.store.draft(d["id"])


class RuleRecording(RedditTestCase):
    def test_rejects_unverifiable_evidence(self):
        ok = ("r/aiArt", "nsfw", {"allowed": False}, "https://www.reddit.com/r/aiArt/about/rules/", "No NSFW", "Ashkan")
        for bad, kwargs in [
            ((ok[0], ok[1], ok[2], "https://safereddit.com/r/aiArt", ok[4], ok[5]), {}),   # mirror, not reddit
            ((ok[0], ok[1], ok[2], "https://www.reddit.com/r/aivideo/about/rules/", ok[4], ok[5]), {}),  # wrong sub
            ((ok[0], ok[1], ok[2], ok[3], ok[4], "claude"), {}),                        # not a human
            ((ok[0], ok[1], ok[2], ok[3], "", ok[5]), {}),                              # no quote
            ((ok[0], ok[1], {}, ok[3], ok[4], ok[5]), {}),                              # missing values
            (ok, {"method": "search_snippet"}),                                        # not a browser
        ]:
            with self.assertRaises(rules.RuleError):
                rules.record_rule(self.store, *bad, **kwargs)
        rules.record_rule(self.store, *ok)

    def test_cannot_approve_until_every_rule_is_fresh(self):
        with self.assertRaises(rules.RuleError):
            rules.classify(self.store, "r/aiArt", "approved_native", "Ashkan")
        self.verify_all(when="2026-09-01")                      # 30 days old -> stale
        with self.assertRaises(rules.RuleError):
            rules.classify(self.store, "r/aiArt", "approved_native", "Ashkan")
        self.verify_all()
        self.assertEqual(rules.classify(self.store, "r/aiArt", "approved_native", "Ashkan")["status"],
                         "approved_native")

    def test_new_rule_evidence_resets_approval(self):
        self.verify_all()
        rules.classify(self.store, "r/aiArt", "approved_native", "Ashkan")
        rules.record_rule(self.store, "r/aiArt", "nsfw", {"allowed": False},
                          "https://www.reddit.com/r/aiArt/about/rules/", "changed", "Ashkan")
        self.assertEqual(self.store.communities()["communities"]["r/aiArt"]["status"], "unverified")

    def test_ai_ban_and_promo_thread_are_enforced(self):
        self.verify_all()
        rules.record_rule(self.store, "r/aiArt", "self_promotion", {"mode": "promo_thread_only"},
                          "https://www.reddit.com/r/aiArt/about/rules/", "weekly thread only", "Ashkan")
        with self.assertRaises(rules.RuleError):
            rules.classify(self.store, "r/aiArt", "approved_native", "Ashkan")
        rules.classify(self.store, "r/aiArt", "approved_promo_thread", "Ashkan")
        rules.record_rule(self.store, "r/aiArt", "ai_content",
                          {"allowed": False, "disclosure_required": False, "disclosure_format": ""},
                          "https://www.reddit.com/r/aiArt/about/rules/", "no AI", "Ashkan")
        with self.assertRaises(rules.RuleError):
            rules.classify(self.store, "r/aiArt", "approved_promo_thread", "Ashkan")

    def test_no_post_list_cannot_be_approved(self):
        with self.assertRaises(rules.RuleError):
            rules.classify(self.store, "r/Art", "approved_native", "Ashkan")


class VariantStage(RedditTestCase):
    def test_real_episode_produces_differentiated_sfw_drafts(self):
        out = variant.prepare(LOCK, self.store)
        ids = {d["id"] for d in out["drafts"]}
        self.assertEqual(ids, {"s1e02_public__aiart", "s1e02_public__aigeneratedart"})
        skipped = {s["community"]: s["reason"] for s in out["skipped"]}
        self.assertIn("r/aivideo", skipped)
        self.assertIn("r/midjourney", skipped)
        self.assertIn("r/SillyTavernAI", skipped)
        self.assertNotIn("r/Art", skipped)                       # no-post list is never even considered
        for d in self.store.drafts():
            text = (d["title"] + d["body"]).lower()
            self.assertFalse(d["nsfw"])
            self.assertIn(d["disclosure"].lower(), text)
            self.assertNotIn("fanvue", text)
            self.assertEqual(d["links"], [])
            self.assertTrue(all(i["path"] is None and i["origin"] == "reddit_native" for i in d["images"]))
            self.assertTrue(all("No overlay text" in i["prompt"] for i in d["images"]))
            self.assertEqual(d["gate"]["decision"], "BLOCK")
        a, b = (self.store.draft(i) for i in sorted(ids))
        self.assertNotEqual(a["title"], b["title"])
        self.assertFalse(any("too similar" in x for x in a["gate"]["blocks"]))

    def test_fanvue_episode_is_never_drafted(self):
        out = variant.prepare(FANVUE_LOCK, self.store)
        self.assertEqual(out["drafts"], [])

    def test_optional_variant_never_raises(self):
        out = variant.optional_variant("brand/story_locks/does_not_exist.json", self.store)
        self.assertFalse(out["ok"])
        self.assertTrue(out["optional"])

    def test_pipeline_hook_swallows_any_failure(self):
        import ps05_ops

        with mock.patch("nyx_reddit.variant.optional_variant", side_effect=RuntimeError("boom")):
            out = ps05_ops._optional_reddit_variant({"story_plan": []}, "x.json")
        self.assertFalse(out["ok"])
        self.assertIn("boom", out["error"])
        self.assertEqual(ps05_ops._optional_reddit_variant({"slides": []}, "x.json")["skipped"], "not a story lock")

    def test_pipeline_hook_writes_only_beside_the_pipeline_state(self):
        import ps05_ops
        import state as st

        lock = json.loads((ROOT / LOCK).read_text())
        queue = ROOT / "nyx_reddit" / "queue"
        before = sorted(queue.glob("*.json")) if queue.exists() else []
        with mock.patch.object(st, "VENTURE_PATH", self.tmp / "state" / "venture.json"):
            out = ps05_ops._optional_reddit_variant(lock, str(ROOT / LOCK))
        self.assertTrue(out["ok"], out)
        self.assertTrue((self.tmp / "nyx_reddit" / "queue" / "s1e02_public__aiart.json").is_file())
        self.assertEqual(before, sorted(queue.glob("*.json")) if queue.exists() else [])

    def test_posted_draft_is_not_overwritten(self):
        variant.prepare(LOCK, self.store)
        d = self.store.draft("s1e02_public__aiart")
        d["status"] = "posted"
        self.store.save_draft(d)
        out = variant.prepare(LOCK, self.store)
        self.assertEqual(self.store.draft("s1e02_public__aiart")["status"], "posted")
        self.assertTrue(any("not overwritten" in s["reason"] for s in out["skipped"]))

    def test_reddit_channel_stays_deferred(self):
        self.assertEqual(channels.status_of("reddit"), channels.DEFERRED)
        self.assertNotIn("reddit", channels.publishing_channels())


class Gate(RedditTestCase):
    def test_unverified_community_blocks(self):
        variant.prepare(LOCK, self.store)
        blocks = self.store.draft("s1e02_public__aiart")["gate"]["blocks"]
        self.assertTrue(any("not verified" in b for b in blocks))
        self.assertTrue(any("not generated yet" in b for b in blocks))
        self.assertTrue(any("no account snapshot" in b for b in blocks))

    def test_complete_draft_passes_and_packet_is_written(self):
        d = self.ready_everything()
        self.assertEqual(d["gate"]["decision"], "PASS", d["gate"]["blocks"])
        out = ledger.make_packet(self.store, d["id"])
        self.assertTrue(out["ok"])
        self.assertTrue((self.tmp / out["packet"]).is_file())
        self.assertEqual(self.store.draft(d["id"])["status"], "packet_ready")

    def assertBlocks(self, draft, needle):
        result = gate.evaluate(self.store, draft)
        self.assertEqual(result["decision"], "BLOCK")
        self.assertTrue(any(needle in b for b in result["blocks"]), result["blocks"])

    def test_fanvue_link_blocks_even_if_rules_allowed_it(self):
        d = self.ready_everything()
        self.assertBlocks({**d, "body": d["body"] + "\nfanvue.com/nyxemberfall"}, "Fanvue")
        self.assertBlocks({**d, "links": ["https://www.fanvue.com/nyxemberfall"]}, "Fanvue")

    def test_real_person_nsfw_and_missing_disclosure_block(self):
        d = self.ready_everything()
        self.assertBlocks({**d, "body": d["body"] + " I'm a real girl btw"}, "real person")
        self.assertBlocks({**d, "nsfw": None}, "nsfw=false")
        self.assertBlocks({**d, "body": d["body"].replace(d["disclosure"], "")}, "disclosure")
        self.assertBlocks({**d, "flair": "Made up"}, "flair")
        self.assertBlocks({**d, "body": d["body"] + " {{WHAT_BROKE}}"}, "placeholder")

    def test_public_pipeline_image_is_a_blind_crosspost(self):
        d = self.ready_everything()
        public = self.tmp / "nyx_metricool_handoff_2026-09-29" / "slide.png"
        public.parent.mkdir(parents=True)
        shutil.copy(self.tmp / d["images"][0]["path"], public)
        self.assertBlocks(d, "blind cross-post")

    def test_stale_rules_and_account_block(self):
        d = self.ready_everything()
        with mock.patch.dict(os.environ, {"NYX_TODAY": "2026-10-20"}):
            result = gate.evaluate(self.store, d)
        self.assertTrue(any("older than 14 days" in b for b in result["blocks"]))
        self.assertTrue(any("account snapshot older" in b for b in result["blocks"]))

    def test_warmup_is_required(self):
        d = self.ready_everything()
        act = self.store.activity()
        act["comments"] = act["comments"][:3]
        self.store.save_activity(act)
        self.assertBlocks(d, "warm-up")

    def test_removal_halts_the_whole_test(self):
        d = self.ready_everything()
        ledger.make_packet(self.store, d["id"])
        ledger.record_posted(self.store, d["id"], "https://www.reddit.com/r/aiArt/comments/abc", "Ashkan")
        out = ledger.record_outcome(self.store, d["id"], "removed", None, None, "Rule 3", False, "Ashkan")
        self.assertIn("post removed", out["halt"])
        other = self.store.draft("s1e02_public__aigeneratedart")
        self.assertBlocks(other, "test halted")

    def test_low_ratio_halts_and_budget_caps_at_three(self):
        d = self.ready_everything()
        ledger.make_packet(self.store, d["id"])
        ledger.record_posted(self.store, d["id"], "https://www.reddit.com/r/aiArt/comments/abc", "Ashkan")
        out = ledger.record_outcome(self.store, d["id"], "live", 0.41, 2, "-", False, "Ashkan")
        self.assertIn("upvote ratio", out["halt"])
        act = self.store.activity()
        act["halt"] = None
        act["posts"] += [{"draft": f"x{i}", "community": f"r/x{i}", "date": "2026-08-01"} for i in range(2)]
        self.store.save_activity(act)
        self.assertBlocks(self.store.draft("s1e02_public__aigeneratedart"), "test budget spent")

    def test_posting_requires_a_packet(self):
        variant.prepare(LOCK, self.store)
        with self.assertRaises(ledger.LedgerError):
            ledger.record_posted(self.store, "s1e02_public__aiart",
                                 "https://www.reddit.com/r/aiArt/comments/abc", "Ashkan")
        self.assertFalse(ledger.make_packet(self.store, "s1e02_public__aiart")["ok"])
        self.assertFalse((self.store.packets / "s1e02_public__aiart.md").exists())


class MatrixRender(RedditTestCase):
    def test_matrix_is_dated_and_lists_no_post(self):
        self.verify_all()
        variant.prepare(LOCK, self.store)
        docs = matrix.render(self.store)
        self.assertIn("Generated 2026-10-01", docs["docs/reddit/community-matrix.md"])
        self.assertIn("2026-09-30", docs["docs/reddit/community-matrix.md"])
        self.assertIn("NOT VERIFIED", docs["docs/reddit/community-matrix.md"])
        self.assertIn("r/Art", docs["docs/reddit/no-post-list.md"])
        self.assertIn("s1e02_public__aiart", docs["docs/reddit/draft-queue.md"])


if __name__ == "__main__":
    unittest.main()
