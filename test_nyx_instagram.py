"""Tests for the gated Nyx Instagram flow (fake caller; no network, no model)."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import nyx_instagram as ni

# Tests never read the real state/publish_holds.json (which holds real items).
_NO_HOLDS = Path(tempfile.mkdtemp()) / "publish_holds.json"
ni.HOLDS_PATH = _NO_HOLDS

NOW = datetime(2026, 9, 22, 8, 30, tzinfo=timezone.utc)      # 18:30 Melbourne, 30 min past the slot
ITEM2_SCHEDULED_FOR = "2026-09-22T18:00:00+10:00 Australia/Melbourne"   # exactly what is stored in venture.json
MEDIA = "https://static.metricool.com/planner/202609/6988018-file-1.mp4"


def actual_item2():
    """The real stored shape of content_items[2] (publish_config as written by the live run)."""
    return {"target_platform": "instagram", "lifecycle_status": "SCHEDULED", "caption": "c2",
            "publish_config": {"route": "metricool", "blogId": "6988018", "metricool_post_id": 377654211,
                               "metricool_uuid": "5965913277594159518", "type": "REEL",
                               "isAiGenerated": True, "autoPublish": True,
                               "scheduled_for": ITEM2_SCHEDULED_FOR}}


def venture(**extra):
    items = [{} for _ in range(4)]
    items[2] = actual_item2()
    items[3] = {"target_platform": "instagram", "lifecycle_status": "QA_PASSED", "caption": "next one",
                "hosted_media_url": "https://cdn/x.mp4", **extra}
    return {"content_plan": {"content_items": items}}


def good_post(pid=377654211, text="c2", when="2026-09-22T18:00:00", status="PENDING", url=None, **over):
    p = {"id": pid, "uuid": f"u{pid}", "text": text, "media": [MEDIA], "autoPublish": True, "draft": False,
         "publicationDate": {"dateTime": when, "timezone": "Australia/Melbourne"},
         "instagramData": {"type": "REEL", "isAiGenerated": True, "autoPublish": True},
         "providers": [{"network": "instagram", "status": status, "publicUrl": url}]}
    p.update(over)
    return p


BRAND = [{"id": 6988018, "timezone": "Australia/Melbourne", "networksData": {"instagramData": "nyx.emberfall"}}]


def caller_for(posts, brands=BRAND, calls=None, create_data=None, after_create=()):
    state = {"created": False}

    def caller(tool, args, *, allowed):
        if calls is not None:
            calls.append(tool)
        if tool == ni.TOOL_BRAND:
            return {"ok": True, "status": "OK", "data": {"data": brands}}
        if tool == ni.TOOL_LIST:
            extra = list(after_create) if state["created"] else []
            return {"ok": True, "status": "OK", "data": {"data": list(posts) + extra}}
        if tool == ni.TOOL_CREATE:
            state["created"] = True
            return {"ok": True, "status": "OK", "data": {"data": create_data if create_data is not None else
                                                       {"id": 99, "uuid": "u99", "media": [MEDIA]}}}
        raise AssertionError(tool)
    return caller


class ScheduledForParsing(unittest.TestCase):
    def test_actual_item2_value_is_aware_and_becomes_due_after_its_slot(self):
        dt = ni.parse_scheduled_for(ITEM2_SCHEDULED_FOR)
        self.assertEqual(dt.utcoffset(), timedelta(hours=10))
        self.assertEqual(dt.astimezone(timezone.utc), datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc))
        v = venture()
        self.assertEqual([i for i, _ in ni.due_scheduled_items(v, NOW)], [2])
        self.assertEqual(ni.due_scheduled_items(v, NOW - timedelta(hours=1)), [])

    def test_naive_local_plus_zone_name_is_interpreted_in_that_zone(self):
        dt = ni.parse_scheduled_for("2026-09-22T18:00:00 Australia/Melbourne")
        self.assertEqual(dt.astimezone(timezone.utc), datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc))

    def test_naive_without_zone_uses_brand_zone_and_junk_is_none(self):
        self.assertEqual(ni.parse_scheduled_for("2026-09-22 18:00").utcoffset(), timedelta(hours=10))
        self.assertIsNone(ni.parse_scheduled_for("soon"))
        self.assertIsNone(ni.parse_scheduled_for(None))

    def test_dst_offset_comes_from_the_zone(self):
        # Melbourne DST starts 2026-10-04: an October slot is +11:00, not a hard-coded +10:00
        self.assertEqual(ni.parse_scheduled_for("2026-10-10T18:00:00 Australia/Melbourne").utcoffset(),
                         timedelta(hours=11))


class Reconcile(unittest.TestCase):
    def test_gate_closed_makes_no_calls_and_names_the_missing_approval(self):
        calls = []
        r = ni.run_reconcile(venture(), allowed=(), caller=caller_for([], calls=calls), now=NOW)
        self.assertEqual(r["status"], "GATED")
        self.assertIn(ni.TOOL_LIST, r["detail"])
        self.assertEqual(calls, [])

    def test_nothing_due_before_the_slot_needs_no_permission_or_calls(self):
        calls = []
        r = ni.run_reconcile(venture(), allowed=(), caller=caller_for([], calls=calls),
                             now=NOW - timedelta(hours=3))
        self.assertEqual((r["status"], calls), ("NOTHING_DUE", []))

    def test_published_only_when_metricool_says_published_with_url(self):
        v = venture()
        r = ni.run_reconcile(v, allowed=ni.READ_TIER, now=NOW, caller=caller_for(
            [good_post(status="PUBLISHED", url="https://instagram.com/reel/X/")]))
        self.assertEqual(r["events"][0]["outcome"], "PUBLISHED")
        it = v["content_plan"]["content_items"][2]
        self.assertEqual((it["lifecycle_status"], it["platform_post_id"]),
                         ("PUBLISHED", "https://instagram.com/reel/X/"))

    def test_pending_after_slot_stays_scheduled(self):
        v = venture()
        r = ni.run_reconcile(v, allowed=ni.READ_TIER, caller=caller_for([good_post()]), now=NOW)
        self.assertEqual(r["events"][0]["outcome"], "STILL_PENDING")
        self.assertEqual(v["content_plan"]["content_items"][2]["lifecycle_status"], "SCHEDULED")

    def test_missing_post_is_never_marked_published(self):
        v = venture()
        r = ni.run_reconcile(v, allowed=ni.READ_TIER, caller=caller_for([]), now=NOW)
        self.assertEqual(r["events"][0]["outcome"], "NOT_FOUND")
        self.assertEqual(v["content_plan"]["content_items"][2]["lifecycle_status"], "SCHEDULED")

    def test_published_without_url_is_not_accepted(self):
        r = ni.run_reconcile(venture(), allowed=ni.READ_TIER, now=NOW,
                             caller=caller_for([good_post(status="PUBLISHED")]))
        self.assertEqual(r["events"][0]["outcome"], "PUBLISHED_NO_URL")      # never claimed live without a URL

    def test_idempotent_second_run(self):
        v = venture()
        c = caller_for([good_post(status="PUBLISHED", url="https://instagram.com/reel/X/")])
        ni.run_reconcile(v, allowed=ni.READ_TIER, caller=c, now=NOW)
        self.assertEqual(ni.run_reconcile(v, allowed=ni.READ_TIER, caller=c, now=NOW)["status"], "NOTHING_DUE")

    def test_wrong_brand_stops_before_any_listing(self):
        calls = []
        bad = [{"id": 6641055, "networksData": {"instagramData": "x"}}]
        r = ni.run_reconcile(venture(), allowed=ni.READ_TIER, now=NOW,
                             caller=caller_for([], brands=bad, calls=calls))
        self.assertEqual(r["status"], "IDENTITY_MISMATCH")
        self.assertNotIn(ni.TOOL_LIST, calls)

    def test_read_tier_excludes_every_write_tool(self):
        self.assertTrue(all("create" not in t and "update" not in t and "Review" not in t
                            for t in ni.READ_TIER))


class VerifyCreated(unittest.TestCase):
    def check(self, post, **kw):
        return ni.verify_created([post], post["id"], "c2", "2026-09-22T18:00:00+10:00",
                                 "Australia/Melbourne", **kw)

    def test_fully_matching_post_passes(self):
        self.assertIsNone(self.check(good_post(), expect_media=[MEDIA]))

    def test_each_wrong_property_is_rejected(self):
        bad = {
            "failed provider": good_post(providers=[{"network": "instagram", "status": "ERROR"}]),
            "draft": good_post(draft=True),
            "not autoPublish": good_post(autoPublish=False),
            "not reel": good_post(instagramData={"type": "POST", "isAiGenerated": True}),
            "no AI disclosure": good_post(instagramData={"type": "REEL", "isAiGenerated": False}),
            "wrong caption": good_post(text="other"),
            "no media": good_post(media=[]),
            "two media": good_post(media=[MEDIA, MEDIA]),
            "other brand media": good_post(media=["https://static.metricool.com/planner/202609/6641055-file-1.mp4"]),
            "wrong time": good_post(when="2026-09-22T19:00:00"),
            "wrong tz": good_post(publicationDate={"dateTime": "2026-09-22T18:00:00", "timezone": "UTC"}),
            "no instagram": good_post(providers=[{"network": "facebook", "status": "PENDING"}]),
        }
        for name, post in bad.items():
            self.assertIsNotNone(self.check(post), name)

    def test_media_must_equal_what_the_create_call_returned(self):
        self.assertIsNotNone(self.check(good_post(), expect_media=[MEDIA.replace("file-1", "file-2")]))


class Scheduling(unittest.TestCase):
    WRITE = ni.WRITE_TIER
    WHEN = "2026-09-26T18:00:00+10:00"

    def ready(self, v=None):
        v = v or venture()
        return v, ni.plan_next_schedule(v, now=NOW)

    def created(self, **k):
        return caller_for([], after_create=[good_post(pid=99, text="next one", when="2026-09-26T18:00:00")], **k)

    def test_plan_enforces_cadence_gap_after_actual_item2_slot(self):
        _, plan = self.ready()
        self.assertEqual(plan["status"], "READY")
        self.assertGreaterEqual(datetime.fromisoformat(plan["earliest"]),
                                datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc) + ni.MIN_GAP_BETWEEN_IG_POSTS)

    def test_choose_slot_is_18_local_with_correct_offset(self):
        self.assertEqual(ni.choose_slot("2026-09-25T07:00:00+00:00"), "2026-09-25T18:00:00+10:00")   # 17:00 local
        self.assertEqual(ni.choose_slot("2026-10-10T05:00:00+00:00"), "2026-10-10T18:00:00+11:00")   # DST: +11
        self.assertEqual(ni.choose_slot("2026-09-25T09:30:00+00:00"), "2026-09-26T18:00:00+10:00")   # 19:30 local -> next day
        self.assertEqual(ni.choose_slot("2026-09-25T20:00:00+00:00"), "2026-09-26T18:00:00+10:00")   # 06:00 next local day

    def test_no_hosted_media_is_reported_not_guessed(self):
        v = venture()
        del v["content_plan"]["content_items"][3]["hosted_media_url"]
        self.assertEqual(ni.plan_next_schedule(v, now=NOW)["status"], "NEEDS_HOSTED_MEDIA")

    def test_scheduled_or_published_items_are_never_replanned(self):
        v = venture()
        v["content_plan"]["content_items"][3]["lifecycle_status"] = "SCHEDULED"
        self.assertEqual(ni.plan_next_schedule(v, now=NOW)["status"], "NO_ELIGIBLE_ITEM")

    def test_create_gated_without_create_tool_and_makes_no_calls(self):
        v, plan = self.ready()
        calls = []
        r = ni.schedule_item(v, plan, self.WHEN, "Australia/Melbourne", allowed=ni.READ_TIER,
                             caller=caller_for([], calls=calls), persist=lambda: None)
        self.assertEqual((r["status"], calls), ("GATED", []))

    def test_persist_must_be_wired_or_nothing_is_created(self):
        v, plan = self.ready()
        calls = []
        r = ni.schedule_item(v, plan, self.WHEN, "Australia/Melbourne", allowed=self.WRITE,
                             caller=caller_for([], calls=calls))
        self.assertEqual((r["status"], calls), ("PERSIST_NOT_WIRED", []))

    def test_duplicate_caption_in_metricool_blocks_create(self):
        v, plan = self.ready()
        calls = []
        r = ni.schedule_item(v, plan, self.WHEN, "Australia/Melbourne", allowed=self.WRITE, persist=lambda: None,
                             caller=caller_for([{"id": 5, "text": "next one"}], calls=calls))
        self.assertEqual(r["status"], "DUPLICATE_ALREADY_IN_METRICOOL")
        self.assertNotIn(ni.TOOL_CREATE, calls)

    def test_identity_is_read_immediately_before_create_and_attempt_persisted_first(self):
        v, plan = self.ready()
        order, saved = [], []
        base = self.created(calls=order)

        def persist():
            order.append("PERSIST")
            saved.append(json.loads(json.dumps(v["content_plan"]["content_items"][3].get("publish_attempt"))))
        r = ni.schedule_item(v, plan, self.WHEN, "Australia/Melbourne", allowed=self.WRITE, persist=persist,
                             caller=base, now=NOW)
        self.assertEqual(r["status"], "SCHEDULED")
        i = order.index(ni.TOOL_CREATE)
        self.assertEqual(order[i - 2:i], [ni.TOOL_BRAND, "PERSIST"])       # identity -> persist -> create
        self.assertEqual(saved[0]["status"], "CREATE_STARTED")             # recorded BEFORE the create ran
        it = v["content_plan"]["content_items"][3]
        self.assertEqual((it["lifecycle_status"], it["publish_attempt"]["status"]), ("SCHEDULED", "SCHEDULED"))

    def test_wrong_brand_before_create_stops_without_attempt_or_create(self):
        v, plan = self.ready()
        calls = []
        r = ni.schedule_item(v, plan, self.WHEN, "Australia/Melbourne", allowed=self.WRITE, persist=lambda: None,
                             caller=caller_for([], brands=[{"id": 1, "networksData": {}}], calls=calls))
        self.assertEqual(r["status"], "IDENTITY_MISMATCH")
        self.assertNotIn(ni.TOOL_CREATE, calls)
        self.assertNotIn("publish_attempt", v["content_plan"]["content_items"][3])

    def test_empty_create_response_is_uncertain_never_scheduled(self):
        v, plan = self.ready()
        r = ni.schedule_item(v, plan, self.WHEN, "Australia/Melbourne", allowed=self.WRITE, persist=lambda: None,
                             caller=caller_for([], create_data={}))
        self.assertEqual(r["status"], "CREATE_UNCERTAIN")
        it = v["content_plan"]["content_items"][3]
        self.assertEqual((it["lifecycle_status"], it["publish_attempt"]["status"]), ("QA_PASSED", "CREATE_UNCERTAIN"))

    def test_create_with_id_but_unconfirmed_by_listing_is_not_scheduled(self):
        v, plan = self.ready()
        r = ni.schedule_item(v, plan, self.WHEN, "Australia/Melbourne", allowed=self.WRITE, persist=lambda: None,
                             caller=caller_for([], after_create=[good_post(
                                 pid=99, text="next one", when="2026-09-26T18:00:00", draft=True)]))
        self.assertEqual(r["status"], "CREATE_UNVERIFIED")
        self.assertEqual(v["content_plan"]["content_items"][3]["lifecycle_status"], "QA_PASSED")

    def test_uncertain_attempt_blocks_a_new_plan_until_reconciled(self):
        v = venture()
        v["content_plan"]["content_items"][3]["publish_attempt"] = {
            "status": "CREATE_UNCERTAIN", "started_at": NOW.isoformat(), "when_iso": self.WHEN}
        self.assertEqual(ni.plan_next_schedule(v, now=NOW)["status"], "RECONCILE_ATTEMPT")

    def test_reconcile_adopts_a_post_that_really_exists(self):
        v = venture()
        v["content_plan"]["content_items"][3]["publish_attempt"] = {
            "status": "CREATE_UNCERTAIN", "started_at": NOW.isoformat(), "when_iso": self.WHEN}
        r = ni.reconcile_attempt(v, 3, allowed=ni.READ_TIER, now=NOW, caller=caller_for(
            [good_post(pid=99, text="next one", when="2026-09-26T18:00:00")]))
        self.assertEqual(r["status"], "ATTEMPT_ADOPTED")
        self.assertEqual(v["content_plan"]["content_items"][3]["lifecycle_status"], "SCHEDULED")

    def test_reconcile_waits_out_the_settle_window_before_releasing(self):
        v = venture()
        att = {"status": "CREATE_UNCERTAIN", "started_at": NOW.isoformat(), "when_iso": self.WHEN}
        v["content_plan"]["content_items"][3]["publish_attempt"] = att
        early = ni.reconcile_attempt(v, 3, allowed=ni.READ_TIER, now=NOW + timedelta(minutes=5),
                                     caller=caller_for([]))
        self.assertEqual(early["status"], "ATTEMPT_UNRESOLVED")
        late = ni.reconcile_attempt(v, 3, allowed=ni.READ_TIER, now=NOW + timedelta(minutes=31),
                                    caller=caller_for([]))
        self.assertEqual((late["status"], att["status"]), ("ATTEMPT_RELEASED", "NOT_CREATED_CONFIRMED"))

    def test_run_is_inert_when_gated(self):
        calls = []
        v = venture()
        out = ni.run(v, allowed=(), caller=caller_for([], calls=calls), now=NOW, persist=lambda: None)
        self.assertEqual(calls, [])
        self.assertEqual(out["status"], "GATED")
        self.assertEqual([(s["platform"], s["status"]) for s in out["schedule"]], [("instagram", "GATED")])
        self.assertEqual(v["content_plan"]["content_items"][3]["lifecycle_status"], "QA_PASSED")


BRAND_TT = [{"id": 6988018, "timezone": "Australia/Melbourne",
             "networksData": {"instagramData": "nyx.emberfall", "tiktokData": "nyx.emberfall1"}}]
TT_MEDIA = "https://static.metricool.com/planner/202609/6988018-file-77.mp4"
TT_WHEN = "2026-09-21T18:00:00"


def tiktok_venture(caption="found a new charm", objective=None):
    items = [{} for _ in range(6)]
    items[1] = {"target_platform": "tiktok", "lifecycle_status": "PUBLISHED",          # the live item 19 shape
                "lifecycle_updated_at": "2026-09-18T05:00:00+00:00", "platform_post_id": "https://www.tiktok.com/x"}
    items[2] = {"target_platform": "tiktok", "lifecycle_status": "QA_PASSED", "caption": caption,
                "hosted_media_url": "https://cdn/16.mp4", "content_objective": objective}
    return {"content_plan": {"content_items": items}}


def tt_post(pid=55, text="found a new charm", when=TT_WHEN, status="PENDING", **over):
    p = {"id": pid, "uuid": f"u{pid}", "text": text, "media": [TT_MEDIA], "autoPublish": True, "draft": False,
         "publicationDate": {"dateTime": when, "timezone": "Australia/Melbourne"},
         "tiktokData": ni.tiktok_settings(text),
         "providers": [{"network": "tiktok", "status": status, "publicUrl": None}]}
    p.update(over)
    return p


class TikTokFlow(unittest.TestCase):
    WHEN = "2026-09-21T18:00:00+10:00"

    def caller(self, calls=None, brands=BRAND_TT, seen=None, extra_posts=(), post=None):
        st = {"created": False}
        post = post or tt_post()

        def c(tool, args, *, allowed):
            if calls is not None:
                calls.append(tool)
            if tool == ni.TOOL_BRAND:
                return {"ok": True, "status": "OK", "data": {"data": brands}}
            if tool == ni.TOOL_LIST:
                return {"ok": True, "status": "OK", "data": {"data": list(extra_posts) + ([post] if st["created"] else [])}}
            if tool == ni.TOOL_CREATE:
                st["created"] = True
                if seen is not None:
                    seen.append(args)
                return {"ok": True, "status": "OK", "data": {"data": {"id": 55, "uuid": "u55", "media": [TT_MEDIA]}}}
            raise AssertionError(tool)
        return c

    def test_plan_respects_cadence_after_the_already_published_item19(self):
        plan = ni.plan_next_schedule(tiktok_venture(), now=datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc),
                                     platform="tiktok")
        self.assertEqual((plan["status"], plan["platform"], plan["index"]), ("READY", "tiktok", 2))
        self.assertGreaterEqual(datetime.fromisoformat(plan["earliest"]),
                                datetime(2026, 9, 18, 5, 0, tzinfo=timezone.utc) + ni.MIN_GAP_BETWEEN_POSTS)
        self.assertEqual(ni.choose_slot(plan["earliest"]), "2026-09-21T18:00:00+10:00")

    def test_existing_https_asset_ref_is_read_as_the_media_url_without_any_state_write(self):
        # item 16's real shape: asset_ref is already a public https URL, no hosted_media_url recorded
        v = tiktok_venture()
        it = v["content_plan"]["content_items"][2]
        del it["hosted_media_url"]
        it["asset_ref"] = "https://d8j0ntlcm91z4.cloudfront.net/user_x/hf_16.mp4"
        before = json.dumps(v, sort_keys=True)
        plan = ni.plan_next_schedule(v, now=datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc), platform="tiktok")
        self.assertEqual((plan["status"], plan["media"]), ("READY", "https://d8j0ntlcm91z4.cloudfront.net/user_x/hf_16.mp4"))
        self.assertEqual(json.dumps(v, sort_keys=True), before)

    def test_local_only_asset_still_needs_hosting_and_recorded_hosted_url_wins(self):
        v = tiktok_venture()
        it = v["content_plan"]["content_items"][2]
        del it["hosted_media_url"]
        it["asset_ref"] = "/Users/x/generated_assets/wan/content_item_17.mp4"      # item 17's real shape
        self.assertEqual(ni.plan_next_schedule(v, platform="tiktok")["status"], "NEEDS_HOSTED_MEDIA")
        it["asset_ref"] = "https://old/asset.mp4"
        it["hosted_media_url"] = "https://cdn/recorded.mp4"
        self.assertEqual(ni.plan_next_schedule(v, platform="tiktok")["media"], "https://cdn/recorded.mp4")

    def _failing_create(self, text):
        base = self.caller()

        def c(tool, args, *, allowed):
            if tool == ni.TOOL_CREATE:
                return {"ok": False, "status": "TOOL_ERROR", "detail": text}
            return base(tool, args, allowed=allowed)
        return c

    def test_create_failure_keeps_metricools_real_message_and_counts_failures(self):
        v = tiktok_venture()
        now = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
        plan = ni.plan_next_schedule(v, now=now, platform="tiktok")
        r = ni.schedule_item(v, plan, self.WHEN, "Australia/Melbourne", allowed=ni.WRITE_TIER,
                             persist=lambda: None, caller=self._failing_create("Invalid video duration"), now=now)
        self.assertEqual(r["status"], "CREATE_UNCERTAIN")
        it = v["content_plan"]["content_items"][2]
        self.assertIn("TOOL_ERROR: Invalid video duration", it["publish_attempt"]["detail"])
        self.assertEqual(it["create_failures"], 1)
        self.assertEqual(it["lifecycle_status"], "QA_PASSED")

    def test_repeated_failures_stop_the_retries_and_surface_the_cause(self):
        v = tiktok_venture()
        it = v["content_plan"]["content_items"][2]
        it["create_failures"] = ni.MAX_CREATE_FAILURES
        it["publish_attempt"] = {"status": "NOT_CREATED_CONFIRMED", "detail": "TOOL_ERROR: Invalid video duration"}
        plan = ni.plan_next_schedule(v, platform="tiktok")
        self.assertEqual(plan["status"], "BLOCKED")
        self.assertIn("Invalid video duration", plan["detail"])

    def test_released_attempt_retries_in_the_same_tick_and_success_resets_the_counter(self):
        v = tiktok_venture()
        now = datetime(2026, 9, 20, 4, 5, tzinfo=timezone.utc)
        it = v["content_plan"]["content_items"][2]
        it["create_failures"] = 1
        it["tiktok_consent_recorded_at"] = "2026-09-20T03:00:00+00:00"
        it["publish_attempt"] = {"status": "CREATE_UNCERTAIN", "detail": "TOOL_ERROR: x", "when_iso": self.WHEN,
                                 "started_at": (now - timedelta(minutes=63)).isoformat()}
        out = ni.run_schedule(v, allowed=ni.WRITE_TIER, caller=self.caller(), now=now, persist=lambda: None)
        self.assertEqual([s["status"] for s in out], ["ATTEMPT_RELEASED", "SCHEDULED"])
        self.assertEqual(it["create_failures"], 0)
        self.assertEqual(it["lifecycle_status"], "SCHEDULED")

    def test_live_item19_is_never_replanned(self):
        v = tiktok_venture()
        v["content_plan"]["content_items"][2]["lifecycle_status"] = "PUBLISHED"
        self.assertEqual(ni.plan_next_schedule(v, platform="tiktok")["status"], "NO_ELIGIBLE_ITEM")

    def test_tiktok_settings_disclose_own_brand_only_for_a_bio_link_pitch(self):
        self.assertFalse(ni.tiktok_settings("found a new charm")["commercialContentOwnBrand"])
        self.assertTrue(ni.tiktok_settings("the quieter stuff... link's in my bio if you want in")["commercialContentOwnBrand"])
        self.assertTrue(ni.tiktok_settings("x", "CONVERSION")["commercialContentOwnBrand"])
        s = ni.tiktok_settings("x")
        self.assertTrue(s["isAigc"])
        self.assertFalse(s["commercialContentThirdParty"])
        self.assertEqual(s["privacyOption"], "PUBLIC_TO_EVERYONE")

    def test_create_call_targets_only_tiktok_with_intended_settings(self):
        v = tiktok_venture()
        plan = ni.plan_next_schedule(v, now=datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc), platform="tiktok")
        seen = []
        r = ni.schedule_item(v, plan, self.WHEN, "Australia/Melbourne", allowed=ni.WRITE_TIER,
                             persist=lambda: None, caller=self.caller(seen=seen),
                             now=datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc))
        self.assertEqual(r["status"], "SCHEDULED")
        info = json.loads(seen[0]["info"])
        self.assertEqual(info["providers"], [{"network": "tiktok"}])
        self.assertNotIn("instagramData", info)
        self.assertTrue(info["tiktokData"]["isAigc"])
        it = v["content_plan"]["content_items"][2]
        self.assertEqual((it["lifecycle_status"], it["publish_config"]["type"]), ("SCHEDULED", "VIDEO"))

    def test_identity_requires_the_tiktok_handle_before_any_attempt_or_create(self):
        v = tiktok_venture()
        plan = ni.plan_next_schedule(v, now=datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc), platform="tiktok")
        calls = []
        wrong = [{"id": 6988018, "timezone": "Australia/Melbourne",
                  "networksData": {"instagramData": "nyx.emberfall", "tiktokData": "someone.else"}}]
        r = ni.schedule_item(v, plan, self.WHEN, "Australia/Melbourne", allowed=ni.WRITE_TIER,
                             persist=lambda: None, caller=self.caller(calls=calls, brands=wrong))
        self.assertEqual(r["status"], "IDENTITY_MISMATCH")
        self.assertNotIn(ni.TOOL_CREATE, calls)
        self.assertNotIn("publish_attempt", v["content_plan"]["content_items"][2])

    def test_missing_tiktok_connection_is_a_mismatch_not_a_guess(self):
        r = ni.verify_identity(lambda t, a, *, allowed: {"ok": True, "status": "OK", "data": {"data": BRAND}},
                               ni.READ_TIER, ("tiktok",))
        self.assertEqual(r["status"], "IDENTITY_MISMATCH")

    def test_same_caption_on_instagram_does_not_block_tiktok_but_a_tiktok_dup_does(self):
        v = tiktok_venture()
        plan = ni.plan_next_schedule(v, now=datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc), platform="tiktok")
        ig_same = good_post(pid=7, text="found a new charm")
        ok = ni.schedule_item(v, plan, self.WHEN, "Australia/Melbourne", allowed=ni.WRITE_TIER,
                              persist=lambda: None, caller=self.caller(extra_posts=[ig_same]),
                              now=datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc))
        self.assertEqual(ok["status"], "SCHEDULED")
        v2 = tiktok_venture()
        dup = ni.schedule_item(v2, plan, self.WHEN, "Australia/Melbourne", allowed=ni.WRITE_TIER,
                               persist=lambda: None, caller=self.caller(extra_posts=[tt_post(pid=9)]))
        self.assertEqual(dup["status"], "DUPLICATE_ALREADY_IN_METRICOOL")

    def test_verify_created_tiktok_rejects_wrong_network_settings_and_disclosure(self):
        def chk(p, **kw):
            return ni.verify_created([p], p["id"], "found a new charm", self.WHEN, "Australia/Melbourne",
                                     platform="tiktok", expect_tiktok=ni.tiktok_settings("found a new charm"), **kw)
        self.assertIsNone(chk(tt_post()))
        s = ni.tiktok_settings("found a new charm")
        self.assertIsNotNone(chk(tt_post(providers=[{"network": "instagram", "status": "PENDING"}])))
        self.assertIsNotNone(chk(tt_post(providers=[{"network": "tiktok", "status": "PENDING"},
                                                    {"network": "instagram", "status": "PENDING"}])))
        self.assertIsNotNone(chk(tt_post(tiktokData={**s, "isAigc": False})))
        self.assertIsNotNone(chk(tt_post(tiktokData={**s, "privacyOption": "SELF_ONLY"})))
        self.assertIsNotNone(chk(tt_post(providers=[{"network": "tiktok", "status": "ERROR"}])))

    def test_absent_or_partial_tiktok_data_is_unverified_never_assumed(self):
        want = ni.tiktok_settings("found a new charm")
        good = tt_post()

        def chk(p, expect=want):
            return ni.verify_created([p], p["id"], "found a new charm", self.WHEN, "Australia/Melbourne",
                                     platform="tiktok", expect_tiktok=expect)
        no_block = {k: v for k, v in good.items() if k != "tiktokData"}
        self.assertIn("UNVERIFIED", chk(no_block))                                   # whole block missing
        for missing in ("isAigc", "privacyOption", "title"):                     # any missing TRUE-valued key
            partial = {**good, "tiktokData": {k: v for k, v in want.items() if k != missing}}
            self.assertIn(f"tiktokData.{missing} absent", chk(partial))
        own = ni.tiktok_settings("found a new charm", "CONVERSION")               # own-brand intended True
        partial = {**good, "tiktokData": {k: v for k, v in own.items() if k != "commercialContentOwnBrand"}}
        self.assertIn("tiktokData.commercialContentOwnBrand absent", chk(partial, expect=own))
        # Metricool's real listing omits FALSE flags (observed 2026-09-21): absent == False, not unverified
        falses = {**good, "tiktokData": {k: v for k, v in want.items() if v is not False}}
        self.assertIsNone(chk(falses))
        self.assertIn("no intended TikTok settings", chk(good, expect=None))         # cannot verify -> refuse
        self.assertIn("no intended TikTok settings", chk(good, expect={}))
        self.assertIsNone(chk(good))                                                # fully echoed -> verified

    def test_unverifiable_created_post_is_never_marked_scheduled(self):
        v = tiktok_venture()
        plan = ni.plan_next_schedule(v, now=datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc), platform="tiktok")
        stripped = {k: val for k, val in tt_post().items() if k != "tiktokData"}   # Metricool did not echo settings
        r = ni.schedule_item(v, plan, self.WHEN, "Australia/Melbourne", allowed=ni.WRITE_TIER,
                             persist=lambda: None, caller=self.caller(post=stripped),
                             now=datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc))
        self.assertEqual(r["status"], "CREATE_UNVERIFIED")
        it = v["content_plan"]["content_items"][2]
        self.assertEqual((it["lifecycle_status"], it["publish_attempt"]["status"]), ("QA_PASSED", "CREATE_UNCERTAIN"))
        self.assertIn("UNVERIFIED", it["publish_attempt"]["detail"])

    def test_reconcile_marks_tiktok_published_only_with_a_public_url(self):
        v = tiktok_venture()
        it = v["content_plan"]["content_items"][2]
        it.update({"lifecycle_status": "SCHEDULED", "publish_config": {
            "route": "metricool", "metricool_post_id": 55, "scheduled_for": "2026-09-21T18:00:00+10:00 Australia/Melbourne"}})
        late = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
        no_url = ni.apply_reconcile(v, [tt_post(status="PUBLISHED")], late)
        self.assertEqual(no_url[0]["outcome"], "PUBLISHED_NO_URL")
        self.assertEqual(it["lifecycle_status"], "SCHEDULED")
        ok = ni.apply_reconcile(v, [{**tt_post(), "providers": [{"network": "tiktok", "status": "PUBLISHED",
                                                                  "publicUrl": "https://www.tiktok.com/@nyx.emberfall1/video/1"}]}], late)
        self.assertEqual(ok[0]["outcome"], "PUBLISHED")
        self.assertEqual((it["lifecycle_status"], it["tiktok_handle"]), ("PUBLISHED", "nyx.emberfall1"))

    def test_run_schedule_handles_both_platforms_independently(self):
        v = venture()
        v["content_plan"]["content_items"].extend([{} for _ in range(4)])
        v["content_plan"]["content_items"][5] = {"target_platform": "tiktok", "lifecycle_status": "QA_PASSED",
                                                 "caption": "tt", "hosted_media_url": "https://cdn/t.mp4"}
        out = ni.run_schedule(v, allowed=(), caller=self.caller(), now=NOW, persist=lambda: None)
        self.assertEqual({(s["platform"], s["status"]) for s in out},
                         {("instagram", "GATED"), ("tiktok", "GATED")})

    T0 = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)

    def _run(self, v, calls):
        return ni.run_schedule(v, allowed=ni.WRITE_TIER, caller=TikTokFlow().caller(calls=calls),
                               now=self.T0, persist=lambda: None)

    def test_metricool_is_the_default_tiktok_route_and_needs_no_ps05_consent(self):
        v = tiktok_venture()
        calls = []
        out = self._run(v, calls)
        self.assertEqual(ni.tiktok_route(v["content_plan"]["content_items"][2]), ni.TIKTOK_ROUTE_METRICOOL)
        self.assertIn(ni.TOOL_CREATE, calls)
        self.assertEqual([(s["platform"], s["status"]) for s in out], [("tiktok", "SCHEDULED")])

    def test_direct_route_still_requires_per_post_consent_and_never_creates_via_metricool(self):
        v = tiktok_venture()
        v["content_plan"]["content_items"][2]["tiktok_route"] = ni.TIKTOK_ROUTE_DIRECT
        calls = []
        out = self._run(v, calls)
        self.assertEqual([(s["platform"], s["status"]) for s in out], [("tiktok", "AWAITING_FOUNDER_CONSENT")])
        self.assertNotIn(ni.TOOL_CREATE, calls)
        self.assertEqual(v["content_plan"]["content_items"][2]["lifecycle_status"], "QA_PASSED")

    def test_open_direct_session_blocks_a_metricool_create_until_closed_or_expired(self):
        v = tiktok_venture()
        it = v["content_plan"]["content_items"][2]
        it["tiktok_publish_session"] = {"publish_session_id": "s1", "expires_at": "2026-09-20T04:00:00Z",
                                        "status": "AWAITING_FOUNDER_CONSENT_IN_WIDGET"}
        calls = []
        self.assertEqual(self._run(v, calls)[0]["status"], "DIRECT_SESSION_OPEN")
        self.assertNotIn(ni.TOOL_CREATE, calls)
        it["tiktok_publish_session"]["status"] = "ABANDONED"
        self.assertEqual(self._run(v, [])[0]["status"], "SCHEDULED")

    def test_expired_direct_session_no_longer_blocks(self):
        v = tiktok_venture()
        v["content_plan"]["content_items"][2]["tiktok_publish_session"] = {
            "publish_session_id": "s1", "expires_at": "2026-09-20T02:00:00Z", "status": "AWAITING_FOUNDER_CONSENT_IN_WIDGET"}
        self.assertEqual(self._run(v, [])[0]["status"], "SCHEDULED")


class PublishHolds(unittest.TestCase):
    """A held create is never retried by any tick; reconcile stays read-only."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.path = Path(self.d.name) / "holds.json"
        self.path.write_text(json.dumps({"holds": {"tiktok:2": {"reason": "permission denied; needs approval"}}}))
        p = mock.patch.object(ni, "HOLDS_PATH", self.path)
        p.start()
        self.addCleanup(p.stop)

    def test_held_item_is_never_planned(self):
        self.assertEqual(ni.plan_next_schedule(tiktok_venture(), platform="tiktok")["status"], "NO_ELIGIBLE_ITEM")

    def test_a_forced_plan_still_cannot_create(self):
        v = tiktok_venture()
        calls = []
        forced = {"status": "READY", "platform": "tiktok", "index": 2, "caption": "found a new charm",
                  "media": "https://cdn/16.mp4", "earliest": "2026-09-20T03:15:00+00:00"}
        r = ni.schedule_item(v, forced, "2026-09-21T18:00:00+10:00", "Australia/Melbourne",
                             allowed=ni.WRITE_TIER, persist=lambda: None, caller=TikTokFlow().caller(calls=calls))
        self.assertEqual(r["status"], "HELD")
        self.assertEqual(calls, [])
        self.assertNotIn("publish_attempt", v["content_plan"]["content_items"][2])

    def test_uncertain_attempt_is_reconciled_read_only_and_released_but_not_retried(self):
        v = tiktok_venture()
        now = datetime(2026, 9, 20, 4, 5, tzinfo=timezone.utc)
        it = v["content_plan"]["content_items"][2]
        it["publish_attempt"] = {"status": "CREATE_UNCERTAIN", "detail": "TOOL_ERROR", "when_iso": "2026-09-21T18:00:00+10:00",
                                 "started_at": (now - timedelta(minutes=63)).isoformat()}
        calls = []
        out = ni.run_schedule(v, allowed=ni.WRITE_TIER, caller=TikTokFlow().caller(calls=calls), now=now,
                              persist=lambda: None)
        self.assertEqual([s["status"] for s in out], ["ATTEMPT_RELEASED", "HELD"])
        self.assertEqual(sorted(set(calls)), sorted([ni.TOOL_BRAND, ni.TOOL_LIST]))     # reads only
        self.assertNotIn(ni.TOOL_CREATE, calls)
        self.assertEqual(it["lifecycle_status"], "QA_PASSED")

    def test_a_hold_on_one_item_does_not_block_a_different_item(self):
        v = tiktok_venture()
        v["content_plan"]["content_items"].append({"target_platform": "tiktok", "lifecycle_status": "QA_PASSED",
                                                   "caption": "other", "hosted_media_url": "https://cdn/o.mp4"})
        plan = ni.plan_next_schedule(v, platform="tiktok")
        self.assertEqual((plan["status"], plan["index"]), ("READY", 6))

    def test_releasing_the_hold_restores_normal_planning(self):
        self.path.write_text(json.dumps({"holds": {}}))
        self.assertEqual(ni.plan_next_schedule(tiktok_venture(), platform="tiktok")["status"], "READY")

    def test_missing_or_corrupt_hold_file_means_no_holds(self):
        self.path.write_text("not json")
        self.assertEqual(ni.load_holds(), {})
        self.path.unlink()
        self.assertEqual(ni.load_holds(), {})
        self.path.write_text(json.dumps({"holds": ["not", "a", "dict"]}))
        self.assertEqual(ni.load_holds(), {})


class VerifiedExecutor(unittest.TestCase):
    def _stream(self, tool, sent, text, is_error=False):
        ev = [{"type": "system", "subtype": "permission_denied", "message": "str shape"},
              {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "1", "name": tool, "input": sent}]}},
              {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "1",
                                                        "content": text, "is_error": is_error}]}}]
        return "\n".join(json.dumps(e) for e in ev)

    def test_result_comes_from_tool_result_and_args_must_match(self):
        s = self._stream(ni.TOOL_LIST, {"a": 1}, json.dumps({"data": []}))
        self.assertTrue(ni.parse_call_stream(s, ni.TOOL_LIST, {"a": 1})["ok"])
        self.assertEqual(ni.parse_call_stream(s, ni.TOOL_LIST, {"a": 2})["status"], "ARGS_MISMATCH")

    def test_call_with_no_readable_result_is_no_result_not_success(self):
        s = json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "1", "name": ni.TOOL_CREATE, "input": {}}]}})
        self.assertEqual(ni.parse_call_stream(s, ni.TOOL_CREATE, {})["status"], "NO_RESULT")

    def test_gate_blocks_unapproved_tool_without_spawning_anything(self):
        def boom(*a, **k):
            raise AssertionError("must not spawn")
        self.assertEqual(ni.call_tool(ni.TOOL_CREATE, {}, allowed=ni.READ_TIER, run=boom)["status"], "GATED")

    def test_each_invocation_preapproves_only_its_own_tool_and_loads_only_nyx(self):
        seen = {}

        def fake_run(cmd, **k):
            seen["cmd"] = cmd
            class P: stdout = ""; stderr = ""; returncode = 0
            return P()
        ni.call_tool(ni.TOOL_BRAND, {}, allowed=ni.WRITE_TIER, run=fake_run)   # create IS granted...
        cmd = seen["cmd"]
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], ni.TOOL_BRAND)   # ...but not pre-approved here
        self.assertEqual(list(json.loads(cmd[cmd.index("--mcp-config") + 1])["mcpServers"]), ["metricool-nyx"])


if __name__ == "__main__":
    unittest.main()


SLIDE_URLS = [f"https://cdn/slide{i}.png" for i in range(1, 6)]
CAROUSEL_MEDIA = [f"https://static.metricool.com/planner/202609/6988018-file-s{i}.png" for i in range(1, 6)]


def carousel_item(**over):
    it = {"target_platform": "instagram", "lifecycle_status": "QA_PASSED", "caption": "this wasn't here yesterday.",
          "format_variant": "story_carousel", "slides": [{} for _ in range(5)], "hosted_media_urls": SLIDE_URLS}
    it.update(over)
    return it


class StoryCarouselPublishing(unittest.TestCase):
    """story_carousel rides the same Metricool flow: native IG carousel, never TikTok photo mode."""

    def test_instagram_carousel_plans_all_slides_in_order_and_creates_a_multi_image_post(self):
        v = venture()
        v["content_plan"]["content_items"][3] = carousel_item()
        plan = ni.plan_next_schedule(v, now=NOW)
        self.assertEqual((plan["status"], plan["format"], plan["media"]), ("READY", "carousel", SLIDE_URLS))
        info = json.loads(ni._build_create_args(plan, "2026-09-26T18:00:00+10:00", "Australia/Melbourne")["info"])
        self.assertEqual(info["media"], SLIDE_URLS)
        self.assertEqual(info["instagramData"], {"type": "POST", "isAiGenerated": True})

    def test_carousel_with_a_missing_slide_url_is_never_published_partially(self):
        v = venture()
        v["content_plan"]["content_items"][3] = carousel_item(hosted_media_urls=SLIDE_URLS[:4])
        self.assertEqual(ni.plan_next_schedule(v, now=NOW)["status"], "NEEDS_HOSTED_MEDIA")

    def test_carousel_verification_requires_every_slide_and_post_type(self):
        post = good_post(pid=99, text="c", media=CAROUSEL_MEDIA,
                         instagramData={"type": "POST", "isAiGenerated": True})
        args = ([post], 99, "c", "2026-09-22T18:00:00+10:00", "Australia/Melbourne")
        self.assertIsNone(ni.verify_created(*args, media_count=5))
        self.assertIn("exactly 4", ni.verify_created(*args, media_count=4))
        self.assertIn("not REEL", ni.verify_created(*args))                       # a video item must stay a Reel

    def test_tiktok_carousel_needs_a_slideshow_video_never_photo_mode(self):
        v = tiktok_venture()
        v["content_plan"]["content_items"][2].update(format_variant="story_carousel", hosted_media_urls=SLIDE_URLS,
                                                     asset_ref="https://cdn/slide1.png")
        v["content_plan"]["content_items"][2].pop("hosted_media_url", None)
        self.assertEqual(ni.plan_next_schedule(v, platform="tiktok")["status"], "NEEDS_HOSTED_MEDIA")
        v["content_plan"]["content_items"][2]["hosted_media_url"] = "https://cdn/slideshow.mp4"
        plan = ni.plan_next_schedule(v, now=datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc), platform="tiktok")
        self.assertEqual((plan["format"], plan["media"]), ("video", "https://cdn/slideshow.mp4"))

    def test_crossposts_on_a_platform_count_toward_its_cadence_gap(self):
        v = venture()
        v["content_plan"]["content_items"].append({"target_platform": "tiktok", "lifecycle_status": "QA_PASSED",
                                                   "crossposts": [{"platform": "instagram",
                                                                   "scheduled_for": "2026-09-25T18:00:00+10:00 Australia/Melbourne"}]})
        plan = ni.plan_next_schedule(v, now=NOW)
        self.assertGreaterEqual(datetime.fromisoformat(plan["earliest"]),
                                datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc) + ni.MIN_GAP_BETWEEN_POSTS)


class MetricoolTikTokRealShape(unittest.TestCase):
    """Shapes observed live on 2026-09-21 (item16, Metricool post 379156358)."""

    CAP = "found a new charm buried in my gear box and now i physically cannot stop wearing it. keeper or bin, be honest"

    def listing(self, **tt):
        base = {"privacyOption": "PUBLIC_TO_EVERYONE", "title": self.CAP, "photoCoverIndex": 0, "isAigc": True}
        base.update(tt)
        return {"id": 379156358, "text": self.CAP, "autoPublish": True, "draft": False,
                "media": ["https://static.metricool.com/planner/202609/6988018-file-3868282859780547029.mp4"],
                "publicationDate": {"dateTime": "2026-09-21T18:00:00", "timezone": "Australia/Melbourne"},
                "providers": [{"network": "tiktok", "status": "PENDING"}], "tiktokData": base}

    def check(self, post, objective=None):
        return ni.verify_created([post], 379156358, self.CAP, "2026-09-21T18:00:00+10:00", "Australia/Melbourne",
                                 platform="tiktok", expect_tiktok=ni.tiktok_settings(self.CAP, objective))

    def test_create_always_carries_the_required_title(self):
        self.assertEqual(ni.tiktok_settings(self.CAP)["title"], self.CAP)

    def test_real_listing_that_omits_false_flags_verifies(self):
        self.assertIsNone(self.check(self.listing()))

    def test_a_true_setting_missing_or_wrong_still_fails_closed(self):
        no_label = {**self.listing(), "tiktokData": {"privacyOption": "PUBLIC_TO_EVERYONE", "title": self.CAP}}
        self.assertIn("isAigc", self.check(no_label))
        self.assertIn("privacyOption", self.check(self.listing(privacyOption="SELF_ONLY")))
        self.assertIn("commercialContentOwnBrand", self.check(self.listing(), objective="CONVERSION"))
