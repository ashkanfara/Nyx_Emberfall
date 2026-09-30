"""Stdlib unittest suite for the PS-05 Venture Manager -- same convention as
research-orchestrator's test_*.py. No real Claude CLI calls: every stage
function is monkeypatched with a fake at the module level actually used by
manager.py (stages.*), matching how research-orchestrator's own tests fake
run.claude_call rather than hitting the network."""

from __future__ import annotations

import io
import hashlib
import json
import types
import base64
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import audit
import carousel_handoff
import channels
import claude_client
import collect_metrics
import commercial_events
import dashboard_status
import fan_memory
import scheduler_heartbeat
import fanvue_auth
import fanvue_chat
import fanvue_media
import fanvue_metrics
import fanvue_automated_messages
import fanvue_monetization
import fanvue_posts
import fanvue_runtime
import instagram_metrics
import instagram_publish
import instagram_runtime
import local_image_provider
import manager
import nyx_instagram
import prompts
import ps05_ops
import ps05_tick
import server
import stages
import state as st
import story_continuity
import unit_economics
import wan_provider
import wan_runtime


def _paid_ok(**over):
    """An item the founder HAS approved for paid generation -- required since
    2026-09-22, when paid generation stopped being automatic for any format."""
    return {stages.CAROUSEL_PAID_APPROVAL_FIELD: True, **over}


class PaidGenerationFailsClosed(unittest.TestCase):
    """Founder 2026-09-22: the paid Higgsfield path must never become the
    default. Zero-incremental-cost routes come first, and a free route being
    unavailable is NOT on its own a reason to spend credits."""

    def setUp(self):
        self._orig_call = stages.claude_call
        self.addCleanup(lambda: setattr(stages, "claude_call", self._orig_call))

    def test_paid_generation_is_refused_before_any_tool_call_without_approval(self):
        called = []
        stages.claude_call = lambda prompt, **kw: called.append(1) or {"text": "{}", "cost_usd": 0}
        for item in ({}, {"target_platform": "tiktok"}, _good_carousel()):
            with self.subTest(item=sorted(item)):
                with self.assertRaises(stages.StageValidationError) as e:
                    stages.content_director_generate(item, {}, business_context="x", max_credits=10)
                self.assertIn(stages.CAROUSEL_PAID_APPROVAL_FIELD, str(e.exception))
        self.assertEqual(called, [])          # never reached the paid provider at all

    def test_routing_skips_instead_of_auto_selecting_a_paid_provider(self):
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "provider": "higgsfield", "reasoning": "free route unavailable, just use credits",
            "model_used": "soul_2", "business_purpose": "acquisition"}), "cost_usd": 0}
        d = stages.content_director_choose_provider(
            {"target_platform": "instagram"}, {}, business_context="x",
            higgsfield_credits_remaining=40, wan_usd_remaining=2.0)
        self.assertEqual(d["provider"], "skip")               # no automatic fallback
        self.assertEqual(d["rejected_provider"], "higgsfield")
        self.assertIn(stages.CAROUSEL_PAID_APPROVAL_FIELD, d["reasoning"])

    def test_an_explicitly_approved_item_still_reaches_the_paid_provider(self):
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "provider": "higgsfield", "reasoning": "founder approved this one",
            "model_used": "soul_2", "business_purpose": "acquisition"}), "cost_usd": 0}
        d = stages.content_director_choose_provider(
            _paid_ok(target_platform="instagram"), {}, business_context="x",
            higgsfield_credits_remaining=40, wan_usd_remaining=2.0)
        self.assertEqual(d["provider"], "higgsfield")         # approval still works
        self.assertNotIn("rejected_provider", d)

    def test_free_and_costless_choices_are_never_blocked_by_the_paid_gate(self):
        for provider, extra in (("wan", {"model_used": "wan2.7-i2v-2026-04-25",
                                         "resolution": "720P", "duration_seconds": 5,
                                         "content_complexity": "simple_routine",
                                         "generation_prompt": "idle gesture, stable background",
                                         # i2v: Wan ANIMATES an existing still, it never makes one
                                         "reference_image_url": "https://cdn/nyx_reference.webp"}),
                                ("reuse", {"asset_ref": "https://cdn/x.mp4"}),
                                ("skip", {})):
            with self.subTest(provider=provider):
                stages.claude_call = lambda prompt, _p=provider, _e=extra, **kw: {
                    "text": json.dumps({"provider": _p, "reasoning": "r",
                                        "business_purpose": "acquisition", **_e}), "cost_usd": 0}
                d = stages.content_director_choose_provider(
                    {"target_platform": "tiktok", "generation_prompt": "idle gesture, stable bg"},
                    {}, business_context="x",
                    higgsfield_credits_remaining=40, wan_usd_remaining=2.0)
                self.assertEqual(d["provider"], provider)

    def test_every_autonomously_selectable_wan_model_is_free_quota(self):
        """The other half of free-first: Wan is only exempt from the paid gate
        because the one model it may auto-select bills free_quota_protected.
        Adding a pay_per_second model to that set would silently reopen
        automatic paid generation through a provider the gate lets through."""
        for model in stages._WAN_AUTONOMOUS_ALLOWED_MODELS:
            with self.subTest(model=model):
                self.assertEqual(wan_provider.MODEL_SPECS[model]["billing_mode"],
                                 "free_quota_protected")
        self.assertNotIn("wan", stages.PAID_PROVIDERS)

    def test_item20_stays_brief_ready_when_no_free_route_can_generate_it(self):
        v = {"content_plan": {"content_items": [_good_carousel()]}}
        item = v["content_plan"]["content_items"][0]
        with self.assertRaises(stages.StageValidationError):
            stages.content_director_generate(item, {}, business_context="x", max_credits=10)
        self.assertNotIn("lifecycle_status", item)            # never advanced by a refusal
        self.assertNotIn("asset_refs", item)


class _TmpState(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_state_dir = st.STATE_DIR
        self._orig_venture = st.VENTURE_PATH
        self._orig_pending = st.PENDING_APPROVAL_PATH
        self._orig_experiments = st.EXPERIMENTS_DIR
        self._orig_log = audit.LOG_PATH

        tmp_state = Path(self._tmp.name) / "state"
        st.STATE_DIR = tmp_state
        st.VENTURE_PATH = tmp_state / "venture.json"
        st.PENDING_APPROVAL_PATH = tmp_state / "pending_approval.json"
        st.EXPERIMENTS_DIR = tmp_state / "experiments"
        audit.LOG_PATH = tmp_state / "activity_log.jsonl"

        self.v = st.new_venture(
            hypothesis={"concept": "test concept", "primary_constraint": "distribution"},
            thresholds={"operability": {"note": "test"}},
            evidence_gaps=["gap 1"],
            source="unit-test",
        )
        st.save(self.v)

    def tearDown(self):
        st.STATE_DIR = self._orig_state_dir
        st.VENTURE_PATH = self._orig_venture
        st.PENDING_APPROVAL_PATH = self._orig_pending
        st.EXPERIMENTS_DIR = self._orig_experiments
        audit.LOG_PATH = self._orig_log
        self._tmp.cleanup()


class StateBasics(_TmpState):
    def test_new_venture_starts_at_research_complete_with_test_decision(self):
        self.assertEqual(self.v["phase"], "RESEARCH_COMPLETE")
        self.assertEqual(self.v["decision"], "TEST")

    def test_pending_approval_round_trip(self):
        self.assertIsNone(st.load_pending_approval())
        st.write_pending_approval(reason="SPEND_REQUIRED", what="buy credits",
                                  why="need images", founder_action="approve $10 spend",
                                  estimated_minutes=2)
        p = st.load_pending_approval()
        self.assertEqual(p["reason"], "SPEND_REQUIRED")
        st.clear_pending_approval()
        self.assertIsNone(st.load_pending_approval())

    def test_kyc_and_credentials_minutes_go_to_setup_overhead_not_operational(self):
        v = self.v
        st.record_intervention(v, minutes=120, reason="fanvue KYC", category="kyc")
        self.assertEqual(v["metrics"]["h2_operability"]["operational_minutes_this_week"], 0)
        self.assertEqual(v["metrics"]["h2_operability"]["operational_minutes_cumulative"], 0)
        self.assertEqual(v["metrics"]["h2_operability"]["setup_overhead_minutes_cumulative"], 120)

    def test_non_exempt_minutes_count_toward_weekly_trend_not_setup_overhead(self):
        v = self.v
        st.record_intervention(v, minutes=20, reason="review posts", category="could_not_delegate")
        self.assertEqual(v["metrics"]["h2_operability"]["operational_minutes_this_week"], 20)
        self.assertEqual(v["metrics"]["h2_operability"]["setup_overhead_minutes_cumulative"], 0)
        self.assertEqual(v["metrics"]["h2_operability"]["trend_vs_target"], "on_track")

    def test_weekly_minutes_over_target_trend_above(self):
        v = self.v
        st.record_intervention(v, minutes=90, reason="lots of manual review",
                               category="could_not_delegate")
        self.assertEqual(v["metrics"]["h2_operability"]["trend_vs_target"], "above")

    def test_could_not_delegate_log_records_task_and_tier(self):
        v = self.v
        st.record_could_not_delegate(v, task="fanvue posting", why_not_delegated="no API found",
                                     tier_attempted="existing_tool")
        self.assertEqual(len(v["metrics"]["h2_operability"]["could_not_delegate_log"]), 1)
        self.assertEqual(v["metrics"]["h2_operability"]["could_not_delegate_log"][0]["task"],
                         "fanvue posting")


class MetricsCollection(_TmpState):
    def test_record_fanvue_manual_computes_cac(self):
        v = st.load()
        collect_metrics.record_fanvue_manual(v, subscribers=10, revenue=50.0, spend=20.0)
        st.save(v)
        h1 = st.load()["metrics"]["h1_market"]
        self.assertEqual(h1["raw_subscribers"], 10)
        self.assertEqual(h1["gross_revenue_usd"], 50.0)
        self.assertEqual(h1["cac_usd"], 2.0)

    def test_record_fanvue_manual_zero_subscribers_no_divide_by_zero(self):
        v = st.load()
        collect_metrics.record_fanvue_manual(v, subscribers=0, revenue=0.0, spend=5.0)
        self.assertIsNone(v["metrics"]["h1_market"]["cac_usd"])


class QueuedActions(_TmpState):
    def test_queue_action_defaults_to_waiting_status(self):
        v = st.load()
        entry = st.queue_action(v, kind="tiktok_publish", boundary="CANNOT_SAFELY_DELEGATE",
                                what="post ready", why="needs consent", founder_action="click accept")
        self.assertEqual(entry["status"], "WAITING_FOR_FOUNDER_CONSENT")
        self.assertEqual(len(v["queued_actions"]), 1)

    def test_queue_action_rejects_unknown_boundary(self):
        v = st.load()
        with self.assertRaises(AssertionError):
            st.queue_action(v, kind="x", boundary="NOT_A_REAL_BOUNDARY", what="w", why="y",
                            founder_action="z")

    def test_resolve_queued_action_updates_status(self):
        v = st.load()
        entry = st.queue_action(v, kind="tiktok_publish", boundary="CANNOT_SAFELY_DELEGATE",
                                what="w", why="y", founder_action="z")
        st.resolve_queued_action(v, entry["id"], status="PUBLISHED")
        self.assertEqual(v["queued_actions"][0]["status"], "PUBLISHED")

    def test_resolve_unknown_queued_action_raises(self):
        v = st.load()
        with self.assertRaises(KeyError):
            st.resolve_queued_action(v, "does-not-exist", status="PUBLISHED")


class CreditBudget(_TmpState):
    def test_set_and_track_credit_budget(self):
        v = st.load()
        st.set_credit_budget(v, session_limit=40)
        st.record_credit_spend(v, credits=9.62, what="wave 1 assets")
        self.assertEqual(st.credits_remaining(v), 30.38)
        self.assertEqual(v["credit_budget"]["spent_total"], 9.62)


class InstagramRuntimeCredentialStorage(unittest.TestCase):
    """The token value itself is never asserted against in these tests --
    only storage/retrieval mechanics -- consistent with never printing/
    logging the real value anywhere, including test output."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_dir = instagram_runtime.RUNTIME_DIR
        self._orig_path = instagram_runtime.TOKEN_PATH
        instagram_runtime.RUNTIME_DIR = Path(self._tmp.name) / ".instagram_runtime"
        instagram_runtime.TOKEN_PATH = instagram_runtime.RUNTIME_DIR / "token.json"

    def tearDown(self):
        instagram_runtime.RUNTIME_DIR = self._orig_dir
        instagram_runtime.TOKEN_PATH = self._orig_path
        self._tmp.cleanup()

    def test_no_token_initially(self):
        self.assertFalse(instagram_runtime.has_token())
        self.assertIsNone(instagram_runtime.load_token())

    def test_store_then_has_token_true(self):
        instagram_runtime.store_token("dummy-test-value", ig_user_id="123")
        self.assertTrue(instagram_runtime.has_token())

    def test_stored_file_is_owner_only_permissions(self):
        instagram_runtime.store_token("dummy-test-value")
        mode = instagram_runtime.TOKEN_PATH.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


class CommercialEventLifecycle(_TmpState):
    """DISCOVERED -> ANALYZED -> ACTIONED (or FOUNDER_BLOCKED) -- a
    discovered event must never silently present as handled, and
    acquisition_source defaults to UNKNOWN rather than a guess."""

    def test_add_event_starts_discovered_with_unknown_attribution_by_default(self):
        v = st.load()
        e = st.add_commercial_event(v, event_type="NEW_COMMENT", fan_id="f1", handle="h1",
                                    ts="t0", detail={"text": "hi"})
        self.assertEqual(e["lifecycle_status"], "DISCOVERED")
        self.assertEqual(e["acquisition_source"], "UNKNOWN")

    def test_update_status_progresses_lifecycle(self):
        v = st.load()
        e = st.add_commercial_event(v, event_type="TRIAL_STARTED", fan_id="f1", handle="h1",
                                    ts="t0", detail={})
        st.update_commercial_event_status(v, e["id"], status="ANALYZED",
                                          sales_manager_note="looks like a real trial")
        self.assertEqual(v["commercial_events"][0]["lifecycle_status"], "ANALYZED")
        self.assertEqual(v["commercial_events"][0]["sales_manager_note"], "looks like a real trial")

    def test_update_unknown_event_raises(self):
        v = st.load()
        with self.assertRaises(KeyError):
            st.update_commercial_event_status(v, "ce99", status="ACTIONED")

    def test_rejects_unknown_status(self):
        v = st.load()
        e = st.add_commercial_event(v, event_type="NEW_COMMENT", fan_id="f1", handle="h1",
                                    ts="t0", detail={})
        with self.assertRaises(AssertionError):
            st.update_commercial_event_status(v, e["id"], status="DONE")


class DisclosureStrategy(_TmpState):
    """set_disclosure_strategy must ADD a short form, never remove the full
    disclosure_statement -- legal/platform-required disclosure is never
    silently dropped, only de-duplicated where a platform's own native
    AI-label already carries it."""

    def test_adds_short_form_without_removing_full_statement(self):
        v = st.load()
        original_full = "full disclosure sentence"
        v["persona"] = {"disclosure_statement": original_full}
        st.set_disclosure_strategy(
            v, short_form="#AIGenerated",
            positioning_note="character-first, disclosure still present but not repeated",
            per_platform={"instagram": "native AI-info label + #AIGenerated tag",
                          "fanvue": "full statement retained, character-first ordering"},
        )
        self.assertEqual(v["persona"]["disclosure_statement"], original_full)
        self.assertEqual(v["persona"]["disclosure_statement_short"], "#AIGenerated")
        self.assertIn("instagram", v["persona"]["disclosure_per_platform"])


class Ps05TickRefreshFanvue(_TmpState):
    """Regression coverage for a real bug: ps05_tick.py's single-entrypoint
    rewrite called commercial_events.check() (which only DETECTS new events
    by diffing the live snapshot -- it does not persist them) but originally
    discarded the returned events instead of calling
    state.add_commercial_event(). A real SUBSCRIPTION_CANCELLED event was
    silently lost this way -- detect_events() found it, the tick's own log
    line said "1 new event(s)", but it was never written to
    venture.json's commercial_events list and no Sales Manager audit was
    ever triggered by it, because save_snapshot() had already moved the
    diff baseline forward by the time anyone could look again."""

    def setUp(self):
        super().setUp()
        self._orig_refresh = fanvue_auth.refresh_access_token
        self._orig_check = commercial_events.check
        fanvue_auth.refresh_access_token = lambda: {"ok": True}

    def tearDown(self):
        fanvue_auth.refresh_access_token = self._orig_refresh
        commercial_events.check = self._orig_check
        super().tearDown()

    def test_new_events_are_persisted_to_venture_json(self):
        commercial_events.check = lambda: {
            "ok": True, "current_subscribers": 2, "current_earnings": {"total": 0},
            "events": [{"type": "SUBSCRIPTION_CANCELLED", "fan_id": "f1", "handle": "someone",
                       "ts": "2026-09-11T14:02:00Z", "detail": {}}],
        }
        v = st.load()
        summary = ps05_tick._refresh_fanvue(v)
        self.assertEqual(len(v["commercial_events"]), 1)
        self.assertEqual(v["commercial_events"][0]["type"], "SUBSCRIPTION_CANCELLED")
        self.assertEqual(v["commercial_events"][0]["handle"], "someone")
        self.assertIn("SUBSCRIPTION_CANCELLED", summary)

    def test_h1_market_subscribers_and_revenue_kept_current_from_live_check(self):
        """Regression: v['metrics']['h1_market'] (server.py's H1/funnel data
        source) was never updated by any tick -- it stayed frozen at
        whatever a founder last typed into collect_metrics.py (or the seed
        default), silently misrepresenting the dashboard's H1 market-demand
        numbers even while commercial_events.check() had real live data."""
        commercial_events.check = lambda: {
            "ok": True, "current_subscribers": 2,
            "current_earnings": {"total": 12.5, "availableBalance": 12.5}, "events": [],
        }
        v = st.load()
        ps05_tick._refresh_fanvue(v)
        self.assertEqual(v["metrics"]["h1_market"]["raw_subscribers"], 2)
        self.assertEqual(v["metrics"]["h1_market"]["gross_revenue_usd"], 12.5)

    def test_no_new_events_persists_nothing(self):
        commercial_events.check = lambda: {
            "ok": True, "current_subscribers": 2, "current_earnings": {"total": 0}, "events": [],
        }
        v = st.load()
        ps05_tick._refresh_fanvue(v)
        self.assertEqual(v.get("commercial_events", []), [])


class FanvueChatListMessages(unittest.TestCase):
    """Regression: default limit was 100, but Fanvue's own API caps it at
    50 (confirmed via the live openapi-v1.json) and rejects anything higher
    with a 400 -- found by actually running the DM loop against the real
    API, not just reading the docs."""

    def setUp(self):
        self._orig_api = fanvue_chat._api
        self._orig_token = fanvue_runtime.load_credentials
        fanvue_runtime.load_credentials = lambda: {"access_token": "tok"}

    def tearDown(self):
        fanvue_chat._api = self._orig_api
        fanvue_runtime.load_credentials = self._orig_token

    def test_default_limit_is_within_fanvues_max_of_50(self):
        captured = {}

        def fake_api(method, path, token, body=None):
            captured["path"] = path
            return {"data": []}

        fanvue_chat._api = fake_api
        fanvue_chat.list_messages("u1")
        self.assertIn("limit=50", captured["path"])


class FanvueChatSendMessage(unittest.TestCase):
    """2026-09-13 fix: Fanvue's own documented response for POST
    /v1/chats/{userUuid}/message is {"messageUuid": "..."} (confirmed
    against the real OpenAPI spec) -- the old code checked for "uuid",
    which never appears in that response, so every historically successful
    send was misclassified as a failure."""

    def setUp(self):
        self._orig_api = fanvue_chat._api
        self._orig_token = fanvue_runtime.load_credentials
        self._orig_list_messages = fanvue_chat.list_messages
        fanvue_runtime.load_credentials = lambda: {"access_token": "tok"}

    def tearDown(self):
        fanvue_chat._api = self._orig_api
        fanvue_runtime.load_credentials = self._orig_token
        fanvue_chat.list_messages = self._orig_list_messages

    def test_message_uuid_field_is_recognized_as_confirmed_send(self):
        fanvue_chat._api = lambda method, path, token, body=None: {"messageUuid": "m-real-1"}
        result = fanvue_chat.send_message("u1", "hi")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "SENT_CONFIRMED")
        self.assertEqual(result["message_uuid"], "m-real-1")

    def test_legacy_uuid_field_is_still_accepted(self):
        """Backward/forward compatibility, in case a different endpoint or
        a future revision uses 'uuid' instead."""
        fanvue_chat._api = lambda method, path, token, body=None: {"uuid": "m-real-2"}
        result = fanvue_chat.send_message("u1", "hi")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "SENT_CONFIRMED")
        self.assertEqual(result["message_uuid"], "m-real-2")

    def test_genuine_http_error_is_a_real_failure(self):
        fanvue_chat._api = lambda method, path, token, body=None: {
            "_error": True, "status": 400, "body": "bad request"}
        result = fanvue_chat.send_message("u1", "hi")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "FAILED")

    def test_network_level_failure_is_uncertain_not_failed(self):
        """A timeout/connection-reset means we genuinely don't know if
        Fanvue received the request -- must never be silently treated as
        'sent' or 'failed'."""
        fanvue_chat._api = lambda method, path, token, body=None: {
            "_network_error": True, "reason": "timed out"}
        result = fanvue_chat.send_message("u1", "hi")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "DELIVERY_UNCERTAIN")

    def test_ppv_without_media_uuids_is_refused_locally(self):
        """Never make a request Fanvue is guaranteed to 400 on -- refuse
        before the network call."""
        fanvue_chat._api = lambda method, path, token, body=None: self.fail(
            "must not call the API for an invalid PPV request")
        result = fanvue_chat.send_message("u1", "here's a little something", price_cents=500)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "FAILED")

    def test_ppv_with_media_uuids_includes_them_in_the_request_body(self):
        captured = {}

        def fake_api(method, path, token, body=None):
            captured["body"] = body
            return {"messageUuid": "m-real-3"}

        fanvue_chat._api = fake_api
        result = fanvue_chat.send_message("u1", "here's a little something",
                                          price_cents=500, media_uuids=["media-1"])
        self.assertTrue(result["ok"])
        self.assertEqual(captured["body"]["mediaUuids"], ["media-1"])
        self.assertEqual(captured["body"]["price"], 500)

    def test_verify_recent_send_confirms_a_message_that_actually_landed(self):
        fanvue_chat.list_messages = lambda u, mark_as_read=False: {"ok": True, "messages": [
            {"text": "hi there", "sender": {"uuid": "creator-1"}, "sentAt": "2026-09-13T10:00:00+00:00"},
        ]}
        result = fanvue_chat.verify_recent_send(
            "u1", creator_uuid="creator-1", expected_text="hi there",
            sent_after="2026-09-13T09:00:00+00:00")
        self.assertTrue(result["verified"])

    def test_verify_recent_send_does_not_confirm_when_nothing_matches(self):
        """Never blindly resend -- if verification finds nothing, the
        caller must treat this as a genuine unresolved failure, not retry
        automatically."""
        fanvue_chat.list_messages = lambda u, mark_as_read=False: {"ok": True, "messages": [
            {"text": "unrelated fan message", "sender": {"uuid": "fan-1"}, "sentAt": "2026-09-13T10:00:00+00:00"},
        ]}
        result = fanvue_chat.verify_recent_send(
            "u1", creator_uuid="creator-1", expected_text="hi there",
            sent_after="2026-09-13T09:00:00+00:00")
        self.assertFalse(result["verified"])


class Ps05TickFanvueChatLoop(_TmpState):
    """The real DM loop: ingest -> history -> Sales Manager -> guardrails ->
    send -> persist. Every external call (fanvue_chat.*, the LLM stage) is
    faked; these tests verify the ORCHESTRATION logic, especially the two
    safety properties the founder explicitly required: never double-text a
    thread where we already sent the last message, and never send a
    guardrail-violating response."""

    CREATOR_UUID = "creator-uuid-1"

    def setUp(self):
        super().setUp()
        self._tmp_fm = tempfile.TemporaryDirectory()
        self._orig_fm_path = fan_memory.PATH
        fan_memory.PATH = Path(self._tmp_fm.name) / "fan_memory.json"

        self._orig_list_chats = fanvue_chat.list_chats
        self._orig_list_messages = fanvue_chat.list_messages
        self._orig_send_message = fanvue_chat.send_message
        self._orig_verify_send = fanvue_chat.verify_recent_send
        self._orig_chat_response = stages.sales_manager_chat_response
        self._orig_list_media = fanvue_media.list_media
        # Default: no media available -- individual PPV tests override this.
        # Without this mock, an unpatched call would hit the real Fanvue API.
        fanvue_media.list_media = lambda **k: {"ok": True, "media": []}

        v = st.load()
        v["accounts"] = {"fanvue": {"creator_uuid": self.CREATOR_UUID}}
        v["persona"] = {"disclosure_per_platform": {"fanvue": "full disclosure text"}}
        st.save(v)

    def tearDown(self):
        fan_memory.PATH = self._orig_fm_path
        self._tmp_fm.cleanup()
        fanvue_chat.list_chats = self._orig_list_chats
        fanvue_chat.list_messages = self._orig_list_messages
        fanvue_chat.send_message = self._orig_send_message
        fanvue_chat.verify_recent_send = self._orig_verify_send
        stages.sales_manager_chat_response = self._orig_chat_response
        fanvue_media.list_media = self._orig_list_media
        super().tearDown()

    def _one_chat(self, handle="fan1", uuid="fan-uuid-1"):
        fanvue_chat.list_chats = lambda: {"ok": True, "chats": [
            {"user": {"uuid": uuid, "handle": handle}}]}
        return uuid

    def test_skips_response_when_we_sent_the_last_message(self):
        """The exact 'hi yes' scenario: founder already replied, fan hasn't
        written back -- must not double-text."""
        uuid = self._one_chat()
        fanvue_chat.list_messages = lambda u, mark_as_read=False: {"ok": True, "messages": [
            {"text": "hi yes 🙂", "sender": {"uuid": self.CREATOR_UUID}, "sentAt": "t2"},
            {"text": "hi", "sender": {"uuid": uuid}, "sentAt": "t1"},
        ]}
        called = {"n": 0}
        stages.sales_manager_chat_response = lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {}
        fanvue_chat.send_message = lambda *a, **k: self.fail("must not send")

        v = st.load()
        summary = ps05_tick._process_fanvue_chats(v)
        self.assertEqual(called["n"], 0)  # Sales Manager not even consulted
        self.assertIn("awaiting fan reply", summary)

    def test_genuine_fan_message_gets_a_response_sent(self):
        uuid = self._one_chat()
        fanvue_chat.list_messages = lambda u, mark_as_read=False: {"ok": True, "messages": [
            {"text": "hi is anyone there?", "sender": {"uuid": uuid}, "sentAt": "t1"},
        ]}
        stages.sales_manager_chat_response = lambda *a, **k: {
            "intent_classification": "genuine_fan", "action": "respond",
            "response_text": "hey! sorry for the quiet, yes I'm here 🦊", "reasoning": "real fan, reply",
            "_cost_usd": 0.02}
        sent = {}
        fanvue_chat.send_message = lambda u, text, price_cents=None, media_uuids=None: sent.update(
            uuid=u, text=text) or {"ok": True, "status": "SENT_CONFIRMED", "message_uuid": "m1",
                                   "sent_at": "now"}

        v = st.load()
        summary = ps05_tick._process_fanvue_chats(v)
        self.assertEqual(sent["uuid"], uuid)
        self.assertIn("sorry for the quiet", sent["text"])
        entries = audit.load(limit=1)
        self.assertEqual(entries[0]["action"], "fanvue_chat_response_sent")
        fan = fan_memory.get("fanvue", uuid)
        self.assertEqual(fan["interaction_history"][-1]["kind"], "dm_response_sent")

    def test_no_response_decision_is_logged_to_audit_for_dashboard_visibility(self):
        uuid = self._one_chat()
        fanvue_chat.list_messages = lambda u, mark_as_read=False: {"ok": True, "messages": [
            {"text": "check out my telegram for deals", "sender": {"uuid": uuid}, "sentAt": "t1"}]}
        stages.sales_manager_chat_response = lambda *a, **k: {
            "intent_classification": "spam_or_scam", "action": "no_response",
            "response_text": "", "reasoning": "spam pattern"}
        fanvue_chat.send_message = lambda *a, **k: self.fail("must not send")

        v = st.load()
        ps05_tick._process_fanvue_chats(v)
        actions = [e["action"] for e in audit.load(limit=10)]
        self.assertIn("fanvue_chat_no_response_decision", actions)
        self.assertIn("fanvue_chat_ingestion_check", actions)

    def test_guardrail_blocks_off_platform_response_and_does_not_send(self):
        uuid = self._one_chat()
        fanvue_chat.list_messages = lambda u, mark_as_read=False: {"ok": True, "messages": [
            {"text": "hey", "sender": {"uuid": uuid}, "sentAt": "t1"}]}
        stages.sales_manager_chat_response = lambda *a, **k: {
            "intent_classification": "genuine_fan", "action": "respond",
            "response_text": "text me on telegram @nyx for more", "reasoning": "x"}
        fanvue_chat.send_message = lambda *a, **k: self.fail("must not send a guardrail violation")

        v = st.load()
        summary = ps05_tick._process_fanvue_chats(v)
        self.assertIn("guardrail BLOCKED", summary)
        blocking = [a for a in v["queued_actions"] if a["kind"] == "CHAT_RESPONSE_GUARDRAIL_VIOLATION"]
        self.assertEqual(len(blocking), 1)

    def test_escalate_action_queues_founder_review_without_sending(self):
        uuid = self._one_chat()
        fanvue_chat.list_messages = lambda u, mark_as_read=False: {"ok": True, "messages": [
            {"text": "angry hostile message", "sender": {"uuid": uuid}, "sentAt": "t1"}]}
        stages.sales_manager_chat_response = lambda *a, **k: {
            "intent_classification": "hostile_or_abusive", "action": "escalate",
            "response_text": "", "reasoning": "x", "escalation_reason": "hostile message, unsure how to handle"}
        fanvue_chat.send_message = lambda *a, **k: self.fail("must not send")

        v = st.load()
        ps05_tick._process_fanvue_chats(v)
        escalated = [a for a in v["queued_actions"] if a["kind"] == "CHAT_NEEDS_FOUNDER_JUDGMENT"]
        self.assertEqual(len(escalated), 1)

    def test_guardrail_violation_detects_email_and_human_denial(self):
        self.assertIsNotNone(ps05_tick._guardrail_violation("email me at fan@example.com"))
        self.assertIsNotNone(ps05_tick._guardrail_violation("call me at 555-123-4567"))
        self.assertIsNotNone(ps05_tick._guardrail_violation("I am human, not a bot"))
        self.assertIsNone(ps05_tick._guardrail_violation("hey! how's your day going? 🦊"))

    def test_ppv_decision_includes_media_uuids_in_the_actual_send(self):
        """End-to-end: available media reaches the Sales Manager call and a
        chosen ppv_media_uuid actually reaches send_message's media_uuids."""
        uuid = self._one_chat()
        fanvue_chat.list_messages = lambda u, mark_as_read=False: {"ok": True, "messages": [
            {"text": "can I see a pic?", "sender": {"uuid": uuid}, "sentAt": "t1"}]}
        fanvue_media.list_media = lambda **k: {"ok": True, "media": [
            {"uuid": "media-real-1", "media_type": "image"}]}
        stages.sales_manager_chat_response = lambda *a, **k: {
            "intent_classification": "buyer_signal", "action": "ppv",
            "response_text": "here's a little something 😉", "ppv_price_cents": 500,
            "ppv_media_uuid": "media-real-1", "reasoning": "x"}
        sent = {}
        fanvue_chat.send_message = lambda u, text, price_cents=None, media_uuids=None: sent.update(
            price_cents=price_cents, media_uuids=media_uuids) or {
            "ok": True, "status": "SENT_CONFIRMED", "message_uuid": "m1", "sent_at": "now"}

        v = st.load()
        ps05_tick._process_fanvue_chats(v)
        self.assertEqual(sent["media_uuids"], ["media-real-1"])
        self.assertEqual(sent["price_cents"], 500)

    def test_ppv_with_no_valid_media_is_blocked_not_sent(self):
        """Fails safely: if the resolved media_uuid isn't in the real
        available list (e.g. media disappeared/became unready between the
        decision and the send, or the model didn't comply), never send --
        no PPV, no crash, just a visible blocked note."""
        uuid = self._one_chat()
        fanvue_chat.list_messages = lambda u, mark_as_read=False: {"ok": True, "messages": [
            {"text": "can I see a pic?", "sender": {"uuid": uuid}, "sentAt": "t1"}]}
        fanvue_media.list_media = lambda **k: {"ok": True, "media": []}
        # A decision claiming a uuid that isn't real -- stages.py would
        # normally reject this itself, but this test exercises ps05_tick's
        # own defense-in-depth guard directly, as if that layer were ever
        # bypassed.
        stages.sales_manager_chat_response = lambda *a, **k: {
            "intent_classification": "buyer_signal", "action": "ppv",
            "response_text": "here's a little something 😉", "ppv_price_cents": 500,
            "ppv_media_uuid": "not-a-real-uuid", "reasoning": "x"}
        fanvue_chat.send_message = lambda *a, **k: self.fail("must not send an invalid ppv")

        v = st.load()
        summary = ps05_tick._process_fanvue_chats(v)
        self.assertIn("ppv BLOCKED", summary)

    def test_delivery_uncertain_is_verified_and_confirmed_not_retried(self):
        """A network-level/ambiguous send response must be checked against
        reality before being logged as a failure -- and must never trigger
        an automatic duplicate send."""
        uuid = self._one_chat()
        fanvue_chat.list_messages = lambda u, mark_as_read=False: {"ok": True, "messages": [
            {"text": "hi is anyone there?", "sender": {"uuid": uuid}, "sentAt": "t1"}]}
        stages.sales_manager_chat_response = lambda *a, **k: {
            "intent_classification": "genuine_fan", "action": "respond",
            "response_text": "hey! I'm here 🦊", "reasoning": "x"}
        send_calls = {"n": 0}

        def fake_send(u, text, price_cents=None, media_uuids=None):
            send_calls["n"] += 1
            return {"ok": False, "status": "DELIVERY_UNCERTAIN", "error": {"reason": "timed out"}}

        fanvue_chat.send_message = fake_send
        fanvue_chat.verify_recent_send = lambda u, creator_uuid, expected_text, sent_after: {
            "ok": True, "verified": True, "message": {"uuid": "m-verified", "sentAt": "now"}}

        v = st.load()
        ps05_tick._process_fanvue_chats(v)
        self.assertEqual(send_calls["n"], 1)  # never retried within this tick
        actions = [e["action"] for e in audit.load(limit=10)]
        self.assertIn("fanvue_chat_send_verified_after_uncertain", actions)
        self.assertIn("fanvue_chat_response_sent", actions)  # treated as a real success
        self.assertNotIn("fanvue_chat_send_failed", actions)

    def test_delivery_uncertain_unresolved_is_recorded_as_failure_not_retried(self):
        uuid = self._one_chat()
        fanvue_chat.list_messages = lambda u, mark_as_read=False: {"ok": True, "messages": [
            {"text": "hi is anyone there?", "sender": {"uuid": uuid}, "sentAt": "t1"}]}
        stages.sales_manager_chat_response = lambda *a, **k: {
            "intent_classification": "genuine_fan", "action": "respond",
            "response_text": "hey! I'm here 🦊", "reasoning": "x"}
        send_calls = {"n": 0}

        def fake_send(u, text, price_cents=None, media_uuids=None):
            send_calls["n"] += 1
            return {"ok": False, "status": "DELIVERY_UNCERTAIN", "error": {"reason": "timed out"}}

        fanvue_chat.send_message = fake_send
        fanvue_chat.verify_recent_send = lambda u, creator_uuid, expected_text, sent_after: {
            "ok": True, "verified": False}

        v = st.load()
        ps05_tick._process_fanvue_chats(v)
        self.assertEqual(send_calls["n"], 1)  # never retried within this tick
        actions = [e["action"] for e in audit.load(limit=10)]
        self.assertIn("fanvue_chat_send_uncertain_unresolved", actions)
        self.assertIn("fanvue_chat_send_failed", actions)

    def test_unchanged_thread_after_a_no_response_decision_is_skipped(self):
        """Do not re-run the LLM (and re-pay for it) on a thread that
        hasn't changed since it was already reviewed and correctly given
        no_response."""
        uuid = self._one_chat()
        fanvue_chat.list_messages = lambda u, mark_as_read=False: {"ok": True, "messages": [
            {"text": "check out my telegram for deals", "sender": {"uuid": uuid}, "sentAt": "t1"}]}
        fan_memory.upsert_fan("fanvue", uuid, handle="fan1", first_seen="t0",
                              relationship_stage="trial")
        fan_memory.record_interaction(
            "fanvue", uuid, kind="dm_reviewed_no_response", detail="spam",
            meta={"action": "no_response", "intent_classification": "spam_or_scam",
                 "inbound_message_at": "t1"})
        called = {"n": 0}
        stages.sales_manager_chat_response = lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {}
        fanvue_chat.send_message = lambda *a, **k: self.fail("must not send")

        v = st.load()
        summary = ps05_tick._process_fanvue_chats(v)
        self.assertEqual(called["n"], 0)  # LLM never consulted -- nothing changed
        self.assertIn("unchanged since last review", summary)

    def test_changed_thread_after_a_no_response_decision_is_still_processed(self):
        """The skip must only apply when nothing changed -- a genuinely new
        inbound message must still be reviewed."""
        uuid = self._one_chat()
        fanvue_chat.list_messages = lambda u, mark_as_read=False: {"ok": True, "messages": [
            {"text": "a new message", "sender": {"uuid": uuid}, "sentAt": "t2"}]}
        fan_memory.upsert_fan("fanvue", uuid, handle="fan1", first_seen="t0",
                              relationship_stage="trial")
        fan_memory.record_interaction(
            "fanvue", uuid, kind="dm_reviewed_no_response", detail="spam",
            meta={"action": "no_response", "intent_classification": "spam_or_scam",
                 "inbound_message_at": "t1"})
        called = {"n": 0}
        stages.sales_manager_chat_response = lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {
            "intent_classification": "genuine_fan", "action": "no_response",
            "response_text": "", "reasoning": "still reviewing"}
        fanvue_chat.send_message = lambda *a, **k: self.fail("must not send")

        v = st.load()
        ps05_tick._process_fanvue_chats(v)
        self.assertEqual(called["n"], 1)  # new message -> LLM consulted again


class FanMemory(unittest.TestCase):
    """A small, non-sensitive per-fan relationship record -- only what's
    needed for the Sales Manager to have continuity across ticks."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_path = fan_memory.PATH
        fan_memory.PATH = Path(self._tmp.name) / "fan_memory.json"

    def tearDown(self):
        fan_memory.PATH = self._orig_path
        self._tmp.cleanup()

    def test_unknown_fan_returns_none(self):
        self.assertIsNone(fan_memory.get("fanvue", "nope"))

    def test_upsert_then_get_round_trips(self):
        fan_memory.upsert_fan("fanvue", "f1", handle="h1", first_seen="t0",
                              relationship_stage="trial")
        entry = fan_memory.get("fanvue", "f1")
        self.assertEqual(entry["handle"], "h1")
        self.assertEqual(entry["relationship_stage"], "trial")

    def test_upsert_rejects_unknown_stage(self):
        with self.assertRaises(AssertionError):
            fan_memory.upsert_fan("fanvue", "f1", handle="h1", first_seen="t0",
                                  relationship_stage="best_friend")

    def test_record_interaction_appends_history_and_creates_fan_if_new(self):
        fan_memory.record_interaction("fanvue", "f2", kind="comment_received",
                                      detail="spam-like comment", next_recommended_action="ignore")
        entry = fan_memory.get("fanvue", "f2")
        self.assertEqual(len(entry["interaction_history"]), 1)
        self.assertEqual(entry["interaction_history"][0]["kind"], "comment_received")
        self.assertEqual(entry["next_recommended_action"], "ignore")

    def test_record_interaction_stores_optional_structured_meta(self):
        fan_memory.record_interaction("fanvue", "f3", kind="dm_response_sent", detail="x",
                                      meta={"action": "respond", "intent_classification": "genuine_fan"})
        entry = fan_memory.get("fanvue", "f3")
        self.assertEqual(entry["interaction_history"][0]["meta"]["action"], "respond")

    def test_record_interaction_without_meta_omits_the_key(self):
        fan_memory.record_interaction("fanvue", "f4", kind="comment_received", detail="x")
        entry = fan_memory.get("fanvue", "f4")
        self.assertNotIn("meta", entry["interaction_history"][0])


class FanvueSalesMetrics(unittest.TestCase):
    """Per the founder's explicit tracking request: outcomes, not volume.
    Every metric with an empty/zero denominator must report NOT_AVAILABLE
    rather than a fabricated 0% -- a handful of real conversations is not a
    validated rate."""

    def test_no_data_yet_reports_not_available_everywhere(self):
        result = dashboard_status.fanvue_sales_metrics({})
        self.assertEqual(result["genuine_inbound_dm_threads"], dashboard_status.NOT_AVAILABLE)
        self.assertEqual(result["response_rate"], dashboard_status.NOT_AVAILABLE)
        self.assertEqual(result["conversion_to_paid"], dashboard_status.NOT_AVAILABLE)

    def test_spam_only_threads_show_full_spam_rate_and_no_available_response_rate(self):
        data = {
            "fanvue:f1": {"revenue_usd": 0.0, "relationship_stage": "trial", "interaction_history": [
                {"meta": {"action": "no_response", "intent_classification": "spam_or_scam"}}]},
            "fanvue:f2": {"revenue_usd": 0.0, "relationship_stage": "trial", "interaction_history": [
                {"meta": {"action": "no_response", "intent_classification": "spam_or_scam"}}]},
        }
        result = dashboard_status.fanvue_sales_metrics(data)
        self.assertEqual(result["spam_scam_rate"], 1.0)
        self.assertEqual(result["genuine_inbound_dm_threads"], 0)
        self.assertEqual(result["response_rate"], dashboard_status.NOT_AVAILABLE)

    def test_awaiting_fan_reply_threads_are_excluded_from_genuine_count(self):
        data = {"fanvue:f1": {"revenue_usd": 0.0, "relationship_stage": "trial", "interaction_history": [
            {"meta": {"action": "no_response", "reason": "last_sender_is_us"}}]}}
        result = dashboard_status.fanvue_sales_metrics(data)
        self.assertEqual(result["genuine_inbound_dm_threads"], 0)

    def test_response_sent_computes_latency_and_response_rate(self):
        data = {"fanvue:f1": {"revenue_usd": 0.0, "relationship_stage": "trial", "interaction_history": [
            {"meta": {"action": "respond", "intent_classification": "genuine_fan",
                     "inbound_message_at": "2026-09-11T15:00:00+00:00",
                     "responded_at": "2026-09-11T15:10:00+00:00"}}]}}
        result = dashboard_status.fanvue_sales_metrics(data)
        self.assertEqual(result["response_rate"], 1.0)
        self.assertEqual(result["median_response_latency_minutes"], 10.0)

    def test_conversion_to_paid_uses_revenue_field(self):
        data = {
            "fanvue:f1": {"revenue_usd": 5.0, "relationship_stage": "payer", "interaction_history": []},
            "fanvue:f2": {"revenue_usd": 0.0, "relationship_stage": "trial", "interaction_history": []},
        }
        result = dashboard_status.fanvue_sales_metrics(data)
        self.assertEqual(result["conversion_to_paid"], 0.5)


class ContentPipelineHealth(unittest.TestCase):
    """A creative brief (prompt+caption+platform, no asset yet) must never
    be reported as 'ready content' -- explicit lifecycle states distinguish
    BRIEF_READY from an actual generated/publishable asset. An Instagram-
    specific block must never read as 'therefore nothing is prepared for
    Fanvue/TikTok either.'"""

    def test_empty_plan_is_not_flagged_as_a_frozen_pipeline(self):
        result = dashboard_status.content_pipeline_health({})
        self.assertEqual(result["publish_ready_count"], 0)
        self.assertEqual(result["brief_only_count"], 0)

    def test_legacy_items_infer_lifecycle_from_ad_hoc_flags(self):
        plan = {"content_items": [
            {"target_platform": "tiktok"},  # no flags at all -> BRIEF_READY
            {"target_platform": "tiktok", "published": True},
            {"target_platform": "instagram", "published_at": "t0"},
            {"target_platform": "reddit", "deferred": True},
        ]}
        result = dashboard_status.content_pipeline_health(plan)
        self.assertEqual(result["by_platform"]["tiktok"]["BRIEF_READY"], 1)
        self.assertEqual(result["by_platform"]["tiktok"]["PUBLISHED"], 1)
        self.assertEqual(result["by_platform"]["instagram"]["PUBLISHED"], 1)
        self.assertEqual(result["by_platform"]["reddit"]["DEFERRED"], 1)
        self.assertEqual(result["brief_only_count"], 1)
        self.assertFalse(result["pipeline_empty"])

    def test_explicit_lifecycle_status_is_respected_over_legacy_flags(self):
        plan = {"content_items": [
            {"target_platform": "tiktok", "lifecycle_status": "PUBLISH_READY"}]}
        result = dashboard_status.content_pipeline_health(plan)
        self.assertEqual(result["by_platform"]["tiktok"]["PUBLISH_READY"], 1)
        self.assertEqual(result["publish_ready_count"], 1)
        self.assertEqual(result["brief_only_count"], 0)

    def test_pipeline_empty_when_everything_is_published_or_deferred(self):
        plan = {"content_items": [{"target_platform": "tiktok", "published": True},
                                  {"target_platform": "reddit", "deferred": True}]}
        result = dashboard_status.content_pipeline_health(plan)
        self.assertTrue(result["pipeline_empty"])


class ManagerAccountabilityView(_TmpState):
    def test_combines_manager_status_and_specialist_audit(self):
        v = st.load()
        st.set_manager_status(v, "growth", current_objective="grow reach", current_blocker="none",
                              next_action="run_growth_audit", next_action_at="2026-09-13T00:00:00Z")
        st.record_specialist_audit(v, "growth", {"summary": "profile looks fine", "findings": [],
                                                 "recommendations": [], "priority_order": []})
        st.set_bottleneck(v, "no genuine Fanvue traffic yet")
        result = dashboard_status.manager_accountability(v)
        self.assertEqual(result["current_bottleneck"], "no genuine Fanvue traffic yet")
        self.assertEqual(result["managers"]["growth"]["current_objective"], "grow reach")
        self.assertEqual(result["managers"]["growth"]["last_audit_summary"], "profile looks fine")

    def test_missing_status_reports_not_available(self):
        v = st.load()
        result = dashboard_status.manager_accountability(v)
        self.assertEqual(result["managers"]["sales"]["current_objective"], dashboard_status.NOT_AVAILABLE)
        self.assertEqual(result["current_bottleneck"], dashboard_status.NOT_AVAILABLE)


class BusinessScoreboard(unittest.TestCase):
    def test_reports_not_available_for_missing_subscriber_data(self):
        v = {"metrics": {"h2_operability": {}}, "experiments": []}
        result = dashboard_status.business_scoreboard(v, {"gross_revenue_usd": 0.0}, {})
        self.assertEqual(result["revenue_per_payer"], dashboard_status.NOT_AVAILABLE)

    def test_computes_revenue_per_payer_when_subscribers_known(self):
        v = {"metrics": {"h2_operability": {"operational_minutes_this_week": 20}}, "experiments": []}
        result = dashboard_status.business_scoreboard(
            v, {"gross_revenue_usd": 10.0, "subscribers": 2}, {})
        self.assertEqual(result["revenue_per_payer"], 5.0)
        self.assertEqual(result["founder_minutes_this_week"], 20)


class StallDetection(unittest.TestCase):
    def test_no_flags_for_a_healthy_venture(self):
        v = {"experiments": [], "content_plan": {}, "queued_actions": []}
        result = dashboard_status.stall_detection(v, [], {})
        self.assertFalse(result["stalled"])

    def test_flags_stale_pending_approval_still_open_after_the_autonomy_fix(self):
        """Regression: a real incident where a pending_approval was manually
        superseded (the underlying generation was done another way) but
        never cleared -- it sat open with no other visible symptom. As of
        the 2026-09-13 autonomy fix a stale approval no longer blocks the
        whole loop, but it's still worth flagging as likely-stale so the
        founder can clear it rather than re-approve something already
        moot; the flag text must reflect the corrected (per-item, not
        whole-loop) blocking behavior."""
        from datetime import datetime, timezone
        now = datetime(2026, 9, 12, 5, 0, 0, tzinfo=timezone.utc)
        v = {"experiments": [], "content_plan": {}, "queued_actions": []}
        pending = {"reason": "SPEND_REQUIRED", "created_at": "2026-09-12T02:03:21+00:00"}
        result = dashboard_status.stall_detection(v, [], {}, pending_approval=pending, now=now)
        self.assertTrue(result["stalled"])
        self.assertTrue(any("pending_approval" in f and "ONLY the specific" in f
                            and "ENTIRE" not in f for f in result["flags"]))

    def test_recent_pending_approval_is_not_flagged(self):
        from datetime import datetime, timezone
        now = datetime(2026, 9, 12, 2, 10, 0, tzinfo=timezone.utc)
        v = {"experiments": [], "content_plan": {}, "queued_actions": []}
        pending = {"reason": "SPEND_REQUIRED", "created_at": "2026-09-12T02:03:21+00:00"}
        result = dashboard_status.stall_detection(v, [], {}, pending_approval=pending, now=now)
        self.assertFalse(result["stalled"])

    def test_flags_experiment_expired_and_unaddressed(self):
        from datetime import datetime, timezone
        now = datetime(2026, 9, 15, tzinfo=timezone.utc)
        v = {"experiments": [{"id": "e1", "status": "measuring",
                              "observation_window_end": "2026-09-10T00:00:00+00:00"}],
            "content_plan": {}, "queued_actions": []}
        result = dashboard_status.stall_detection(v, [], {}, now=now)
        self.assertTrue(result["stalled"])
        self.assertTrue(any("e1" in f for f in result["flags"]))

    def test_flags_empty_content_pipeline_only_when_items_exist(self):
        v = {"experiments": [], "queued_actions": [],
            "content_plan": {"content_items": [{"target_platform": "tiktok", "published": True}]}}
        result = dashboard_status.stall_detection(v, [], {})
        self.assertTrue(result["stalled"])

    def test_flags_long_blocking_founder_action(self):
        from datetime import datetime, timezone
        now = datetime(2026, 9, 15, tzinfo=timezone.utc)
        v = {"experiments": [], "content_plan": {}, "queued_actions": [
            {"id": "q5", "kind": "X", "status": "WAITING_FOR_FOUNDER_CONSENT",
             "urgency": "blocking_now", "created_at": "2026-09-10T00:00:00+00:00"}]}
        result = dashboard_status.stall_detection(v, [], {}, now=now)
        self.assertTrue(any("q5" in f for f in result["flags"]))


class CommercialEventDetection(unittest.TestCase):
    """First-snapshot bootstrapping and diff-based detection must only ever
    report events backed by real data -- never fabricate a type we can't
    actually observe."""

    def test_first_snapshot_reports_existing_subscriber_as_new(self):
        current = {"subscribers": [{"uuid": "s1", "handle": "h1",
                                    "firstSubscribedAt": "t0",
                                    "subscription": {"amountPaid": 0, "status": "active"}}],
                  "posts": []}
        events = commercial_events.detect_events(None, current)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "TRIAL_STARTED")

    def test_paid_subscriber_reports_new_subscriber_not_trial(self):
        current = {"subscribers": [{"uuid": "s1", "handle": "h1",
                                    "firstSubscribedAt": "t0",
                                    "subscription": {"amountPaid": 499, "status": "active"}}],
                  "posts": []}
        events = commercial_events.detect_events(None, current)
        self.assertEqual(events[0]["type"], "NEW_SUBSCRIBER")

    def test_trial_conversion_detected_on_amount_paid_transition(self):
        previous = {"subscribers": [{"uuid": "s1", "handle": "h1",
                                     "subscription": {"amountPaid": 0, "status": "active"}}],
                   "posts": []}
        current = {"subscribers": [{"uuid": "s1", "handle": "h1",
                                    "subscription": {"amountPaid": 499, "status": "active"}}],
                  "posts": []}
        events = commercial_events.detect_events(previous, current)
        self.assertEqual([e["type"] for e in events], ["TRIAL_CONVERTED"])

    def test_confirmed_cancellation_detected_on_status_transition(self):
        """2026-09-13 fix: a real, directly-observed status change to
        'cancelled' -- genuine evidence, unlike mere disappearance."""
        previous = {"subscribers": [{"uuid": "s1", "handle": "h1",
                                     "subscription": {"amountPaid": 499, "status": "active"}}],
                   "posts": []}
        current = {"subscribers": [{"uuid": "s1", "handle": "h1",
                                    "subscription": {"amountPaid": 499, "status": "cancelled"}}],
                  "posts": []}
        events = commercial_events.detect_events(previous, current)
        self.assertEqual([e["type"] for e in events], ["SUBSCRIPTION_CANCELLED_CONFIRMED"])

    def test_disappearance_after_confirmed_cancellation_is_not_a_new_event(self):
        """The record disappearing AFTER we already saw status=cancelled is
        Fanvue's own expected cleanup, not a second event."""
        previous = {"subscribers": [{"uuid": "s1", "handle": "h1",
                                     "subscription": {"amountPaid": 499, "status": "cancelled"}}],
                   "posts": []}
        current = {"subscribers": [], "posts": []}
        events = commercial_events.detect_events(previous, current)
        self.assertEqual(events, [])

    def test_trial_expired_detected_when_period_end_evidenced_in_the_past(self):
        """A free trial whose own recorded end date already passed --
        evidenced natural expiry, not an inferred cancellation."""
        previous = {"subscribers": [{"uuid": "s1", "handle": "h1", "subscription": {
            "amountPaid": 0, "status": "active", "currentPeriodEnd": "2020-01-01T00:00:00+00:00"}}],
                   "posts": []}
        current = {"subscribers": [], "posts": []}
        events = commercial_events.detect_events(previous, current)
        self.assertEqual([e["type"] for e in events], ["TRIAL_EXPIRED"])

    def test_disappearance_without_evidence_is_labeled_unknown_not_cancelled(self):
        """2026-09-13 fix (the core finding): disappearance alone, with no
        observed cancelled status and no evidenced expired trial window,
        must never be labeled a confirmed cancellation -- the evidence
        genuinely doesn't support that specific cause."""
        previous = {"subscribers": [{"uuid": "s1", "handle": "h1",
                                     "subscription": {"amountPaid": 499, "status": "active",
                                                      "currentPeriodEnd": "2099-01-01T00:00:00+00:00"}}],
                   "posts": []}
        current = {"subscribers": [], "posts": []}
        events = commercial_events.detect_events(previous, current)
        self.assertEqual([e["type"] for e in events], ["SUBSCRIBER_DISAPPEARED_UNKNOWN"])

    def test_new_comment_detected_and_not_re_flagged_next_time(self):
        previous = {"subscribers": [], "posts": []}
        current = {"subscribers": [], "posts": [{"uuid": "p1", "tips": {"count": 0},
                   "comments": [{"uuid": "c1", "createdAt": "t0", "text": "hi",
                                "user": {"uuid": "u1", "handle": "h1"}}]}]}
        events = commercial_events.detect_events(previous, current)
        self.assertEqual([e["type"] for e in events], ["NEW_COMMENT"])
        # Same comment present in both snapshots now -> not re-reported.
        events2 = commercial_events.detect_events(current, current)
        self.assertEqual(events2, [])

    def test_no_events_when_nothing_changed(self):
        state = {"subscribers": [{"uuid": "s1", "handle": "h1",
                                  "subscription": {"amountPaid": 499, "status": "active"}}],
                "posts": []}
        self.assertEqual(commercial_events.detect_events(state, state), [])


class SchedulerHeartbeat(unittest.TestCase):
    """Regression coverage for the scheduler self-reporting mechanism -- the
    only truthful source for 'has the scheduler actually run'. A heartbeat
    that never got a tick_end call (e.g. the process died) must not silently
    read back as a success."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_path = scheduler_heartbeat.PATH
        scheduler_heartbeat.PATH = Path(self._tmp.name) / "scheduler_heartbeat.json"

    def tearDown(self):
        scheduler_heartbeat.PATH = self._orig_path
        self._tmp.cleanup()

    def test_never_run_reports_ever_run_false(self):
        self.assertEqual(scheduler_heartbeat.status(), {"ever_run": False})

    def test_start_without_end_shows_tick_in_progress(self):
        scheduler_heartbeat.record_tick_start()
        s = scheduler_heartbeat.status()
        self.assertTrue(s["ever_run"])
        self.assertTrue(s["tick_in_progress"])
        self.assertIsNone(s["last_tick_outcome"])

    def test_successful_tick_records_duration_and_resets_failures(self):
        scheduler_heartbeat.record_tick_start()
        scheduler_heartbeat.record_tick_end(outcome="no_op", summary="nothing due")
        s = scheduler_heartbeat.status()
        self.assertFalse(s["tick_in_progress"])
        self.assertEqual(s["last_tick_outcome"], "no_op")
        self.assertIsNotNone(s["last_successful_tick"])
        self.assertEqual(s["consecutive_failures"], 0)

    def test_error_tick_increments_consecutive_failures_and_no_success_update(self):
        scheduler_heartbeat.record_tick_start()
        scheduler_heartbeat.record_tick_end(outcome="error", summary="boom")
        s = scheduler_heartbeat.status()
        self.assertEqual(s["last_tick_outcome"], "error")
        self.assertIsNone(s["last_successful_tick"])
        self.assertEqual(s["consecutive_failures"], 1)

        scheduler_heartbeat.record_tick_start()
        scheduler_heartbeat.record_tick_end(outcome="error", summary="boom again")
        self.assertEqual(scheduler_heartbeat.status()["consecutive_failures"], 2)

    def test_rejects_unknown_outcome(self):
        with self.assertRaises(AssertionError):
            scheduler_heartbeat.record_tick_end(outcome="fine_i_guess", summary="x")

    def test_a_real_run_clears_the_previous_skip_reason(self):
        """A leftover last_skip_reason reads as the CURRENT reason the tick did
        nothing -- it misdescribed a day of missed runs on 2026-09-22."""
        scheduler_heartbeat.record_tick_skipped(reason="relay_paused_write", summary="skipped: x")
        self.assertEqual(json.loads(scheduler_heartbeat.PATH.read_text())["last_skip_reason"],
                         "relay_paused_write")
        scheduler_heartbeat.record_tick_start()
        scheduler_heartbeat.record_tick_end(outcome="degraded", summary="ran, something blocked")
        d = json.loads(scheduler_heartbeat.PATH.read_text())
        self.assertNotIn("last_skip_reason", d)
        self.assertEqual(d["last_tick_outcome"], "degraded")


class SpecialistReview(_TmpState):
    """set_specialist_review is how the dashboard shows real arbitration
    (accepted/rejected/deferred/modified) instead of 3 disconnected audits."""

    def test_fresh_audit_has_no_review_yet(self):
        v = st.load()
        st.record_specialist_audit(v, "content", {"summary": "s", "findings": [],
                                                   "recommendations": [], "priority_order": []})
        self.assertIsNone(v["specialists"]["content"]["venture_manager_review"])

    def test_set_review_records_disposition_and_reason(self):
        v = st.load()
        st.record_specialist_audit(v, "content", {"summary": "s", "findings": [],
                                                   "recommendations": [], "priority_order": []})
        st.set_specialist_review(v, "content", disposition="rejected",
                                 reason="would contaminate the running experiment")
        review = v["specialists"]["content"]["venture_manager_review"]
        self.assertEqual(review["disposition"], "rejected")
        self.assertIn("contaminate", review["reason"])

    def test_new_audit_resets_review_from_prior_audit(self):
        v = st.load()
        st.record_specialist_audit(v, "growth", {"summary": "s1", "findings": [],
                                                  "recommendations": [], "priority_order": []})
        st.set_specialist_review(v, "growth", disposition="accepted", reason="fine")
        st.record_specialist_audit(v, "growth", {"summary": "s2", "findings": [],
                                                  "recommendations": [], "priority_order": []})
        self.assertIsNone(v["specialists"]["growth"]["venture_manager_review"])

    def test_rejects_unknown_disposition(self):
        v = st.load()
        st.record_specialist_audit(v, "sales", {"summary": "s", "findings": [],
                                                 "recommendations": [], "priority_order": []})
        with self.assertRaises(AssertionError):
            st.set_specialist_review(v, "sales", disposition="ignored", reason="x")


class ContentItemLifecycle(_TmpState):
    """A creative brief is not a publishable asset -- explicit lifecycle
    states make that distinction impossible to silently skip."""

    def setUp(self):
        super().setUp()
        v = st.load()
        v.setdefault("content_plan", {})["content_items"] = [
            {"type": "video", "target_platform": "tiktok", "caption": "x"}]
        st.save(v)

    def test_set_lifecycle_updates_status_and_timestamp(self):
        v = st.load()
        item = st.set_content_item_lifecycle(v, 0, "ASSET_GENERATED", asset_ref="job-123",
                                             model_used="wan3_0", cost_credits=3)
        self.assertEqual(item["lifecycle_status"], "ASSET_GENERATED")
        self.assertEqual(item["asset_ref"], "job-123")
        self.assertEqual(item["generation_cost_credits"], 3)
        self.assertIn("lifecycle_updated_at", item)

    def test_rejects_unknown_state(self):
        v = st.load()
        with self.assertRaises(AssertionError):
            st.set_content_item_lifecycle(v, 0, "SUPER_READY")

    def test_rejects_out_of_range_index(self):
        v = st.load()
        with self.assertRaises(IndexError):
            st.set_content_item_lifecycle(v, 5, "ASSET_GENERATED")


class ContentDirectorGenerateValidation(unittest.TestCase):
    """Never trust a reported cost above the caller's budget, never accept
    a 'generated'/'reused' decision with no real asset reference -- this is
    the one stage function allowed to actually spend credits, so its output
    validation is the last line of defense against a fabricated result."""

    def setUp(self):
        self._orig_call = stages.claude_call

    def tearDown(self):
        stages.claude_call = self._orig_call

    def test_rejects_cost_above_budget(self):
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "decision": "generated", "reasoning": "x", "asset_ref": "job-1",
            "model_used": "seedance_2_5", "cost_credits": 32.5, "business_purpose": "acquisition",
        }), "cost_usd": 0.1}
        with self.assertRaises(stages.StageValidationError):
            stages.content_director_generate(_paid_ok(), {}, business_context="x", max_credits=10)

    def test_generated_decision_requires_asset_ref(self):
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "decision": "generated", "reasoning": "x", "asset_ref": "",
            "model_used": "wan3_0", "cost_credits": 3, "business_purpose": "acquisition",
        }), "cost_usd": 0.1}
        with self.assertRaises(stages.StageValidationError):
            stages.content_director_generate(_paid_ok(), {}, business_context="x", max_credits=10)

    def test_skipped_decision_needs_no_asset_ref(self):
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "decision": "skipped", "reasoning": "nothing fit the budget", "asset_ref": "",
            "model_used": "", "cost_credits": 0, "business_purpose": "",
        }), "cost_usd": 0.1}
        d = stages.content_director_generate(_paid_ok(), {}, business_context="x", max_credits=10)
        self.assertEqual(d["decision"], "skipped")

    def test_valid_generation_within_budget_is_accepted(self):
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "decision": "generated", "reasoning": "cheapest adequate option, within budget",
            "asset_ref": "https://cdn.example/job-1.mp4", "model_used": "wan3_0",
            "cost_credits": 3, "business_purpose": "acquisition",
        }), "cost_usd": 0.1}
        d = stages.content_director_generate(_paid_ok(), {}, business_context="x", max_credits=30)
        self.assertEqual(d["cost_credits"], 3)
        self.assertEqual(d["asset_ref"], "https://cdn.example/job-1.mp4")


class ContentDirectorCreativeQaValidation(unittest.TestCase):
    """The QA gate between ASSET_GENERATED and QA_PASSED -- this is what
    catches the profile-level 'duplicate-looking content' problem (near-
    identical opening frames across posts), not just per-asset defects."""

    def setUp(self):
        self._orig_call = stages.claude_call

    def tearDown(self):
        stages.claude_call = self._orig_call

    def _call(self, response: dict):
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps(response), "cost_usd": 0.02}
        return stages.content_director_creative_qa({}, {}, [])

    def test_rejects_bad_verdict(self):
        with self.assertRaises(stages.StageValidationError):
            self._call({"qa_verdict": "MAYBE", "character_qa": "x", "technical_qa": "x",
                       "creative_qa": "x", "business_qa": "x", "reasoning": "x"})

    def test_missing_field_rejected(self):
        with self.assertRaises(stages.StageValidationError):
            self._call({"qa_verdict": "PASS", "character_qa": "x"})

    def test_valid_pass_accepted(self):
        d = self._call({"qa_verdict": "PASS", "character_qa": "consistent", "technical_qa": "clean",
                       "creative_qa": "distinct from recent posts", "business_qa": "engagement",
                       "reasoning": "clears all four checks"})
        self.assertEqual(d["qa_verdict"], "PASS")

    def test_valid_fail_accepted(self):
        d = self._call({"qa_verdict": "FAIL", "character_qa": "consistent", "technical_qa": "clean",
                       "creative_qa": "opening frame nearly identical to a recent post",
                       "business_qa": "engagement", "reasoning": "feed-diversity concern"})
        self.assertEqual(d["qa_verdict"], "FAIL")


class RunContentQaDispatch(_TmpState):
    """End-to-end (mocked at the LLM boundary) coverage of the run_content_qa
    dispatch action manager_decide can now actually choose (it was missing
    from the valid_actions whitelist entirely -- a real bug this session
    found via a genuine tick failure in the activity log, fixed alongside
    adding this QA gate)."""

    def setUp(self):
        super().setUp()
        self.v = st.load()
        self.v["content_plan"] = {"content_items": [
            {"concept": "candidate", "lifecycle_status": "ASSET_GENERATED",
             "generation_prompt": "a blink and a wave", "caption": "hi!"},
            {"concept": "already live", "lifecycle_status": "PUBLISHED",
             "generation_prompt": "a blink and a wave", "caption": "hi there!",
             "lifecycle_updated_at": "2026-09-10T00:00:00+00:00"},
        ]}
        st.save(self.v)
        self._orig_qa = stages.content_director_creative_qa

    def tearDown(self):
        stages.content_director_creative_qa = self._orig_qa
        super().tearDown()

    def test_pass_advances_to_qa_passed(self):
        stages.content_director_creative_qa = lambda *a, **kw: {
            "qa_verdict": "PASS", "character_qa": "ok", "technical_qa": "ok",
            "creative_qa": "distinct enough", "business_qa": "engagement",
            "reasoning": "clears all checks", "_cost_usd": 0.02,
        }
        v = st.load()
        made_progress, detail = manager._dispatch(v, "run_content_qa", {"content_item_index": 0})
        self.assertTrue(made_progress)
        self.assertEqual(v["content_plan"]["content_items"][0]["lifecycle_status"], "QA_PASSED")

    def test_fail_keeps_asset_generated_and_records_reason(self):
        stages.content_director_creative_qa = lambda *a, **kw: {
            "qa_verdict": "FAIL", "character_qa": "ok", "technical_qa": "ok",
            "creative_qa": "opening frame matches 'already live' almost exactly",
            "business_qa": "engagement", "reasoning": "feed-diversity failure", "_cost_usd": 0.02,
        }
        v = st.load()
        made_progress, detail = manager._dispatch(v, "run_content_qa", {"content_item_index": 0})
        self.assertFalse(made_progress)
        item = v["content_plan"]["content_items"][0]
        self.assertEqual(item["lifecycle_status"], "ASSET_GENERATED")  # not advanced
        self.assertEqual(item["qa_result"]["qa_verdict"], "FAIL")

    def test_skips_item_not_asset_generated(self):
        v = st.load()
        made_progress, detail = manager._dispatch(v, "run_content_qa", {"content_item_index": 1})
        self.assertFalse(made_progress)
        self.assertIn("not ASSET_GENERATED", detail)

    def test_recent_published_excludes_the_candidate_itself(self):
        captured = {}
        def _capture(item, persona, recent):
            captured["recent"] = recent
            return {"qa_verdict": "PASS", "character_qa": "ok", "technical_qa": "ok",
                    "creative_qa": "ok", "business_qa": "ok", "reasoning": "ok", "_cost_usd": 0.0}
        stages.content_director_creative_qa = _capture
        v = st.load()
        manager._dispatch(v, "run_content_qa", {"content_item_index": 0})
        self.assertEqual(len(captured["recent"]), 1)
        self.assertEqual(captured["recent"][0]["caption"], "hi there!")

    def test_run_content_generation_and_run_content_qa_are_valid_manager_decide_actions(self):
        # Regression test for the real bug found this session: these two
        # dispatch actions existed in manager.py but were missing from
        # manager_decide's own whitelist, so the CEO loop could never
        # validly choose them -- confirmed via a real failed tick in the
        # activity log ("unknown action 'run_content_generation'").
        orig_call = stages.claude_call
        try:
            for action in ("run_content_generation", "run_content_qa"):
                stages.claude_call = lambda prompt, action=action, **kw: {"text": json.dumps({
                    "action": action, "params": {"content_item_index": 0},
                    "reasoning": "x", "bottleneck": "x",
                }), "cost_usd": 0.01}
                d = stages.manager_decide("{}", "", st.PHASES)
                self.assertEqual(d["action"], action)
        finally:
            stages.claude_call = orig_call


class ManagerAccountability(_TmpState):
    """CEO-loop accountability: current_objective/current_blocker/next_action
    per manager role, kept separate from specialists[role] (which
    record_specialist_audit fully replaces every audit) so accountability
    survives a tick that doesn't refresh a given specialist's audit."""

    def test_set_manager_status_creates_entry(self):
        v = st.load()
        st.set_manager_status(v, "growth", current_objective="grow qualified reach",
                              current_blocker="none", next_action="run growth audit",
                              next_action_at="2026-09-13T00:00:00Z")
        entry = v["manager_status"]["growth"]
        self.assertEqual(entry["current_objective"], "grow qualified reach")
        self.assertEqual(entry["next_action_at"], "2026-09-13T00:00:00Z")

    def test_partial_update_preserves_other_fields(self):
        v = st.load()
        st.set_manager_status(v, "content", current_objective="keep pipeline healthy",
                              current_blocker="none")
        st.set_manager_status(v, "content", current_blocker="waiting on q5")
        entry = v["manager_status"]["content"]
        self.assertEqual(entry["current_objective"], "keep pipeline healthy")
        self.assertEqual(entry["current_blocker"], "waiting on q5")

    def test_survives_a_fresh_specialist_audit_replacing_specialists_dict(self):
        v = st.load()
        st.set_manager_status(v, "sales", current_objective="convert genuine DMs")
        st.record_specialist_audit(v, "sales", {"summary": "s", "findings": [],
                                                "recommendations": [], "priority_order": []})
        self.assertEqual(v["manager_status"]["sales"]["current_objective"], "convert genuine DMs")

    def test_rejects_unknown_role(self):
        v = st.load()
        with self.assertRaises(AssertionError):
            st.set_manager_status(v, "marketing_intern", current_objective="x")

    def test_set_bottleneck_records_description_and_timestamp(self):
        v = st.load()
        st.set_bottleneck(v, "no genuine Fanvue traffic yet despite live content")
        self.assertIn("no genuine Fanvue traffic", v["current_bottleneck"]["description"])
        self.assertIn("identified_at", v["current_bottleneck"])


class QueueActionEstimatedMinutes(_TmpState):
    def test_estimated_minutes_optional_and_stored(self):
        v = st.load()
        a = st.queue_action(v, kind="k", boundary="CREDENTIALS_REQUIRED", what="w",
                            why="y", founder_action="f", estimated_minutes=2.0)
        self.assertEqual(a["estimated_minutes"], 2.0)

    def test_estimated_minutes_defaults_to_none(self):
        v = st.load()
        a = st.queue_action(v, kind="k", boundary="CREDENTIALS_REQUIRED", what="w",
                            why="y", founder_action="f")
        self.assertIsNone(a["estimated_minutes"])


class DashboardStatusComputations(_TmpState):
    """Regression coverage for the Operations Console's derived views --
    each must reflect real state, never stale fields or fabricated data."""

    def test_stale_current_stage_kind_does_not_cause_founder_blocked(self):
        v = st.load()
        v["current_stage_kind"] = "request_approval"  # stale, from a resolved tick
        result = dashboard_status.system_status(v, pending_approval=None,
                                                heartbeat={})
        self.assertNotEqual(result["state"], "FOUNDER_BLOCKED")

    def test_real_pending_approval_is_founder_blocked(self):
        v = st.load()
        result = dashboard_status.system_status(
            v, pending_approval={"what": "approve spend"}, heartbeat={})
        self.assertEqual(result["state"], "FOUNDER_BLOCKED")

    def test_system_status_never_crashes_on_a_pending_approval_missing_what(self):
        """2026-09-16 incident: bracket access (pending_approval["what"])
        crashed the entire status read path with an unhandled KeyError the
        moment pending_approval's shape didn't have that key -- exactly
        what happened when a long-running server process (stale state.py
        in memory) read the new {"items": [...]} file shape as if it were
        still a flat approval object. Must degrade gracefully, never
        raise, regardless of what shape it's handed."""
        v = st.load()
        result = dashboard_status.system_status(
            v, pending_approval={"items": ["not a flat approval object"]}, heartbeat={})
        self.assertEqual(result["state"], "FOUNDER_BLOCKED")  # still truthy -> still blocked
        self.assertIsInstance(result["reason"], str)  # degraded to "", not a crash

    def test_blocking_queued_action_is_founder_blocked_non_urgent_is_not(self):
        v = st.load()
        st.queue_action(v, kind="k", boundary="CREDENTIALS_REQUIRED", what="w",
                        why="y", founder_action="f", urgency="blocking_now")
        result = dashboard_status.system_status(v, pending_approval=None, heartbeat={})
        self.assertEqual(result["state"], "FOUNDER_BLOCKED")

        v2 = st.load()
        st.queue_action(v2, kind="k", boundary="CREDENTIALS_REQUIRED", what="w",
                        why="y", founder_action="f", urgency="non_urgent")
        result2 = dashboard_status.system_status(v2, pending_approval=None, heartbeat={})
        self.assertNotEqual(result2["state"], "FOUNDER_BLOCKED")

    def test_tick_in_progress_is_executing(self):
        v = st.load()
        result = dashboard_status.system_status(
            v, pending_approval=None, heartbeat={"tick_in_progress": True})
        self.assertEqual(result["state"], "EXECUTING")

    def test_open_observation_window_is_waiting_for_data(self):
        v = st.load()
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 9, 9, tzinfo=timezone.utc)
        future = (now + timedelta(hours=5)).isoformat()
        st.add_experiment(v, st.new_experiment(
            experiment_id="e1", hypothesis="h", variable="v", control="c", assets=[],
            channels=[], observation_window_end=future, success_metric="m"))
        result = dashboard_status.system_status(v, pending_approval=None, heartbeat={}, now=now)
        self.assertEqual(result["state"], "WAITING_FOR_DATA")

    def test_funnel_shows_not_available_for_unwired_stages(self):
        v = st.load()
        result = dashboard_status.funnel(v, fanvue_summary=None)
        by_stage = {f["stage"]: f["value"] for f in result}
        self.assertEqual(by_stage["Fanvue Visits"], dashboard_status.NOT_AVAILABLE)
        self.assertEqual(by_stage["Profile Visits"], dashboard_status.NOT_AVAILABLE)

    def test_funnel_shows_real_zero_for_verified_subscriber_count(self):
        v = st.load()
        result = dashboard_status.funnel(v, fanvue_summary={"ok": True, "subscribers": 0,
                                                             "gross_revenue_usd": 0.0})
        by_stage = {f["stage"]: f["value"] for f in result}
        self.assertEqual(by_stage["Paid Subscribers"], 0)  # real zero, not NOT_AVAILABLE

    def test_h1_unknown_with_no_revenue_and_no_conclusions(self):
        v = st.load()
        result = dashboard_status.h1_status(v, fanvue_summary=None)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_h1_does_not_credit_a_bot_flagged_subscriber_as_signal(self):
        v = st.load()
        v["metrics"]["h1_market"]["raw_subscribers"] = 1
        st.add_commercial_event(v, event_type="TRIAL_STARTED", fan_id="f1", handle="h1",
                                ts="t0", detail={})
        st.update_commercial_event_status(v, "ce1", status="ANALYZED",
                                          sales_manager_note="looks like a bot account")
        result = dashboard_status.h1_status(v, fanvue_summary=None)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("bot", result["evidence"].lower())

    def test_h1_credits_a_real_non_flagged_subscriber_as_weak_signal(self):
        v = st.load()
        v["metrics"]["h1_market"]["raw_subscribers"] = 1
        result = dashboard_status.h1_status(v, fanvue_summary=None)
        self.assertEqual(result["status"], "WEAK_SIGNAL")

    def test_h1_never_validated_merely_because_h2_works(self):
        v = st.load()
        v["metrics"]["h2_operability"]["trend_vs_target"] = "on_track"
        result = dashboard_status.h1_status(v, fanvue_summary=None)
        self.assertNotEqual(result["status"], "VALIDATED")

    def test_next_actions_reflects_blocking_queue_first(self):
        v = st.load()
        st.queue_action(v, kind="k", boundary="CREDENTIALS_REQUIRED", what="fix the thing",
                        why="y", founder_action="f", urgency="blocking_now")
        steps = dashboard_status.next_actions_queue(v)
        self.assertEqual(steps[0]["status"], "BLOCKED")

    def test_platform_health_fanvue_shows_not_available_without_snapshot(self):
        v = st.load()
        ph = dashboard_status.platform_health(v, None)
        self.assertEqual(ph["fanvue"]["trials"], dashboard_status.NOT_AVAILABLE)

    def test_platform_health_fanvue_splits_trial_vs_paid_from_snapshot(self):
        v = st.load()
        snapshot = {"subscribers": [
            {"subscription": {"amountPaid": 0}}, {"subscription": {"amountPaid": 499}},
        ], "account": {"earnings": {"total": 499}}, "fetched_at": "t0"}
        ph = dashboard_status.platform_health(v, snapshot)
        self.assertEqual(ph["fanvue"]["trials"], 1)
        self.assertEqual(ph["fanvue"]["paid_subscribers"], 1)
        self.assertEqual(ph["fanvue"]["revenue_usd"], 4.99)

    def test_commercial_events_view_most_recent_first_and_capped(self):
        v = st.load()
        for i in range(15):
            st.add_commercial_event(v, event_type="NEW_COMMENT", fan_id=f"f{i}",
                                    handle=f"h{i}", ts=f"t{i}", detail={})
        view = dashboard_status.commercial_events_view(v, limit=10)
        self.assertEqual(len(view), 10)
        self.assertEqual(view[0]["fan_id"], "f14")  # most recent first

    def test_tool_permission_health_untested_before_any_tick(self):
        result = dashboard_status.tool_permission_health([], {"ever_run": False})
        self.assertEqual(result["status"], "UNTESTED")

    def test_tool_permission_health_blocked_when_prompt_logged_after_last_tick(self):
        heartbeat = {"ever_run": True, "last_attempted_tick": "2026-09-10T00:00:00Z",
                    "last_tick_outcome": "no_op"}
        log = [{"ts": "2026-09-10T00:00:05Z", "actor": "system",
               "action": "permission_prompt_blocked", "detail": "Allow Bash for X?"}]
        result = dashboard_status.tool_permission_health(log, heartbeat)
        self.assertEqual(result["status"], "BLOCKED")

    def test_tool_permission_health_healthy_when_tick_completed_with_no_blocks(self):
        heartbeat = {"ever_run": True, "last_attempted_tick": "2026-09-10T00:00:00Z",
                    "last_tick_outcome": "no_op", "tick_in_progress": False}
        result = dashboard_status.tool_permission_health([], heartbeat)
        self.assertEqual(result["status"], "HEALTHY")

    def test_tool_permission_health_ignores_blocks_from_before_last_tick(self):
        heartbeat = {"ever_run": True, "last_attempted_tick": "2026-09-10T00:00:00Z",
                    "last_tick_outcome": "no_op", "tick_in_progress": False}
        log = [{"ts": "2026-09-09T00:00:00Z", "actor": "system",
               "action": "permission_prompt_blocked", "detail": "stale, from an earlier tick"}]
        result = dashboard_status.tool_permission_health(log, heartbeat)
        self.assertEqual(result["status"], "HEALTHY")

    def test_tool_permission_health_blocked_when_tick_stuck_far_past_typical_duration(self):
        from datetime import datetime, timezone
        now = datetime(2026, 9, 10, 16, 15, 0, tzinfo=timezone.utc)
        heartbeat = {"ever_run": True, "last_attempted_tick": "2026-09-10T16:01:37+00:00",
                    "tick_in_progress": True, "last_tick_outcome": "no_op"}
        result = dashboard_status.tool_permission_health([], heartbeat, now=now)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("elapsed_minutes", result)

    def test_tool_permission_health_unknown_when_tick_recently_started(self):
        from datetime import datetime, timezone
        now = datetime(2026, 9, 10, 16, 2, 30, tzinfo=timezone.utc)
        heartbeat = {"ever_run": True, "last_attempted_tick": "2026-09-10T16:01:37+00:00",
                    "tick_in_progress": True, "last_tick_outcome": "no_op"}
        result = dashboard_status.tool_permission_health([], heartbeat, now=now)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_tool_permission_health_blocked_when_dispatch_never_started(self):
        from datetime import datetime, timezone
        now = datetime(2026, 9, 10, 17, 15, 0, tzinfo=timezone.utc)
        heartbeat = {"ever_run": True, "last_attempted_tick": "2026-09-10T16:01:37+00:00",
                    "tick_in_progress": False, "last_tick_outcome": "no_op"}
        snapshot = {"last_run_at": "2026-09-10T17:01:19.421Z"}
        result = dashboard_status.tool_permission_health([], heartbeat, now=now, task_snapshot=snapshot)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("tick-start", result["detail"])

    def test_tool_permission_health_healthy_when_snapshot_matches_completed_tick(self):
        from datetime import datetime, timezone
        now = datetime(2026, 9, 10, 17, 5, 0, tzinfo=timezone.utc)
        heartbeat = {"ever_run": True, "last_attempted_tick": "2026-09-10T17:01:19+00:00",
                    "tick_in_progress": False, "last_tick_outcome": "no_op"}
        snapshot = {"last_run_at": "2026-09-10T17:01:19.421Z"}
        result = dashboard_status.tool_permission_health([], heartbeat, now=now, task_snapshot=snapshot)
        self.assertEqual(result["status"], "HEALTHY")

    def test_scheduler_trigger_health_untested_before_any_tick(self):
        result = dashboard_status.scheduler_trigger_health({"ever_run": False})
        self.assertEqual(result["status"], "UNTESTED")

    def test_scheduler_trigger_health_healthy_when_recent(self):
        from datetime import datetime, timezone
        now = datetime(2026, 9, 10, 16, 15, 0, tzinfo=timezone.utc)
        heartbeat = {"ever_run": True, "last_attempted_tick": "2026-09-10T16:01:37+00:00",
                    "scheduler_enabled_self_reported": True,
                    "cron_expression_self_reported": "0 * * * *"}
        result = dashboard_status.scheduler_trigger_health(heartbeat, now=now)
        self.assertEqual(result["status"], "HEALTHY")

    def test_scheduler_trigger_health_stalled_when_no_recent_dispatch(self):
        from datetime import datetime, timezone
        now = datetime(2026, 9, 10, 20, 0, 0, tzinfo=timezone.utc)
        heartbeat = {"ever_run": True, "last_attempted_tick": "2026-09-10T16:01:37+00:00",
                    "scheduler_enabled_self_reported": True,
                    "cron_expression_self_reported": "0 * * * *"}
        result = dashboard_status.scheduler_trigger_health(heartbeat, now=now)
        self.assertEqual(result["status"], "STALLED")

    def test_scheduler_trigger_health_disabled(self):
        heartbeat = {"ever_run": True, "last_attempted_tick": "2026-09-10T16:01:37+00:00",
                    "scheduler_enabled_self_reported": False}
        result = dashboard_status.scheduler_trigger_health(heartbeat)
        self.assertEqual(result["status"], "DISABLED")

    def test_fanvue_chat_ingestion_blocked_on_scope(self):
        v = st.load()
        v["queued_actions"] = [{"id": "q6", "kind": "FANVUE_CHAT_SCOPE_REEVALUATION",
                                "status": "WAITING_FOR_FOUNDER_CONSENT"}]
        result = dashboard_status.fanvue_chat_ingestion_health(v, [], chat_scope_granted=False)
        self.assertEqual(result["status"], "BLOCKED_ON_SCOPE")
        self.assertIn("q6", result["detail"])

    def test_fanvue_chat_ingestion_granted_but_unverified(self):
        v = st.load()
        result = dashboard_status.fanvue_chat_ingestion_health(v, [], chat_scope_granted=True)
        self.assertEqual(result["status"], "GRANTED_NOT_YET_VERIFIED")

    def test_fanvue_chat_ingestion_active_once_a_check_is_logged(self):
        v = st.load()
        log = [{"action": "fanvue_chat_ingestion_check", "detail": "Fetched 3 conversation(s)."}]
        result = dashboard_status.fanvue_chat_ingestion_health(v, log, chat_scope_granted=True)
        self.assertEqual(result["status"], "INGESTING")
        self.assertIn("3 conversation", result["detail"])

    def test_sales_response_loop_not_built_when_scope_missing(self):
        result = dashboard_status.sales_response_loop_health([], chat_scope_granted=False)
        self.assertEqual(result["status"], "NOT_BUILT_YET")

    def test_sales_response_loop_active_when_real_decisions_logged(self):
        log = [{"action": "fanvue_chat_response_sent"}, {"action": "fanvue_chat_no_response_decision"}]
        result = dashboard_status.sales_response_loop_health(log, chat_scope_granted=True)
        self.assertEqual(result["status"], "ACTIVE")
        self.assertEqual(result["count"], 2)

    def test_categorize_actor_distinguishes_specialist_roles(self):
        self.assertEqual(dashboard_status.categorize_actor(
            {"actor": "manager", "action": "run_growth_audit"}), "GROWTH")
        self.assertEqual(dashboard_status.categorize_actor(
            {"actor": "manager", "action": "venture_manager_arbitration"}), "VENTURE MANAGER")
        self.assertEqual(dashboard_status.categorize_actor(
            {"actor": "founder", "action": "approval"}), "FOUNDER")
        self.assertEqual(dashboard_status.categorize_actor(
            {"actor": "manager", "action": "tick"}), "SYSTEM")


class FanvueMetricsSummary(unittest.TestCase):
    """Cents-to-dollars conversion is correct and errors surface as ok:False
    rather than partial/zeroed data."""

    def setUp(self):
        self._orig_get = fanvue_metrics._get
        self._orig_load = fanvue_runtime.load_credentials
        fanvue_runtime.load_credentials = lambda: {"access_token": "dummy-test-token"}

    def tearDown(self):
        fanvue_metrics._get = self._orig_get
        fanvue_runtime.load_credentials = self._orig_load

    def test_converts_cents_to_dollars(self):
        def fake_get(path, token):
            if "earnings" in path:
                return {"totals": {"allTime": {"gross": 1000, "net": 850},
                                   "thisMonth": {"gross": 500, "net": 425}}}
            return {"fanCounts": {"subscribersCount": 3, "followersCount": 10}}

        fanvue_metrics._get = fake_get
        result = fanvue_metrics.collect_summary()
        self.assertTrue(result["ok"])
        self.assertEqual(result["gross_revenue_usd"], 10.0)
        self.assertEqual(result["net_revenue_usd"], 8.5)
        self.assertEqual(result["subscribers"], 3)

    def test_api_error_surfaces_as_not_ok(self):
        fanvue_metrics._get = lambda path, token: {"_error": True, "status": 401}
        result = fanvue_metrics.collect_summary()
        self.assertFalse(result["ok"])


class UnitEconomics(_TmpState):
    """Every ratio must report insufficient_data rather than a fabricated
    number when its denominator is zero or the underlying data is absent."""

    def test_zero_denominators_report_insufficient_data_not_zero(self):
        v = st.load()
        result = unit_economics.compute(v)
        self.assertEqual(result["cac_usd"], "insufficient_data")
        self.assertEqual(result["arppu_usd"], "insufficient_data")
        self.assertEqual(result["revenue_per_1000_impressions"], "insufficient_data")

    def test_real_numbers_compute_correct_ratios(self):
        v = st.load()
        v["metrics"]["h1_market"]["gross_revenue_usd"] = 100.0
        v["metrics"]["h1_market"]["spend_usd"] = 20.0
        v["metrics"]["h1_market"]["raw_subscribers"] = 4
        result = unit_economics.compute(v)
        self.assertEqual(result["cac_usd"], 5.0)
        self.assertEqual(result["arppu_usd"], 25.0)


class ExperimentFramework(_TmpState):
    """Regression coverage for the persistent experiment schema: a control
    must be named (not just a variant), due_experiments only surfaces
    non-concluded experiments past their window, and measurements/conclusion
    update status correctly."""

    def test_new_experiment_requires_control_and_variable(self):
        e = st.new_experiment(experiment_id="e1", hypothesis="h", variable="caption",
                              control="wave1_baseline", assets=["m1"], channels=["instagram"],
                              observation_window_end="2026-09-10T00:00:00+00:00",
                              success_metric="engagement delta")
        self.assertEqual(e["variable"], "caption")
        self.assertEqual(e["control"], "wave1_baseline")
        self.assertEqual(e["status"], "running")

    def test_due_experiments_excludes_future_and_concluded(self):
        v = st.load()
        e1 = st.new_experiment(experiment_id="due", hypothesis="h", variable="v", control="c",
                               assets=[], channels=[], observation_window_end="2026-09-09T00:00:00+00:00",
                               success_metric="m")
        e2 = st.new_experiment(experiment_id="future", hypothesis="h", variable="v", control="c",
                               assets=[], channels=[], observation_window_end="2026-12-01T00:00:00+00:00",
                               success_metric="m")
        e3 = st.new_experiment(experiment_id="concluded", hypothesis="h", variable="v", control="c",
                               assets=[], channels=[], observation_window_end="2026-09-09T00:00:00+00:00",
                               success_metric="m")
        st.add_experiment(v, e1)
        st.add_experiment(v, e2)
        st.add_experiment(v, e3)
        st.conclude_experiment(v, "concluded", result="LOST", confidence="low", next_decision="n")

        due = st.due_experiments(v, now_iso="2026-09-09T12:00:00+00:00")
        self.assertEqual([e["id"] for e in due], ["due"])

    def test_record_measurement_sets_status_measuring(self):
        v = st.load()
        st.add_experiment(v, st.new_experiment(
            experiment_id="e1", hypothesis="h", variable="v", control="c", assets=[],
            channels=[], observation_window_end="2026-09-10T00:00:00+00:00", success_metric="m"))
        st.record_experiment_measurement(v, "e1", {"likes": 5})
        e = v["experiments"][0]
        self.assertEqual(e["status"], "measuring")
        self.assertEqual(len(e["measurements"]), 1)

    def test_conclude_unknown_experiment_raises(self):
        v = st.load()
        with self.assertRaises(KeyError):
            st.conclude_experiment(v, "nope", result="LOST", confidence="low", next_decision="n")

    def test_conclude_rejects_non_enum_result(self):
        v = st.load()
        st.add_experiment(v, st.new_experiment(
            experiment_id="e1", hypothesis="h", variable="v", control="c", assets=[],
            channels=[], observation_window_end="2026-09-10T00:00:00+00:00", success_metric="m"))
        with self.assertRaises(AssertionError):
            st.conclude_experiment(v, "e1", result="maybe", confidence="low", next_decision="n")


class QueuedActionUrgency(_TmpState):
    """Regression coverage for the BLOCKING_NOW/NON_URGENT/HISTORICAL_RESOLVED
    3-way split -- a resolved action must never present as still blocking."""

    def test_default_urgency_is_blocking_now(self):
        v = st.load()
        a = st.queue_action(v, kind="k", boundary="CREDENTIALS_REQUIRED", what="w",
                            why="y", founder_action="f")
        self.assertEqual(st.queued_action_status(a), "BLOCKING_NOW")

    def test_non_urgent_flag_respected(self):
        v = st.load()
        a = st.queue_action(v, kind="k", boundary="CREDENTIALS_REQUIRED", what="w",
                            why="y", founder_action="f", urgency="non_urgent")
        self.assertEqual(st.queued_action_status(a), "NON_URGENT")

    def test_resolved_action_is_historical_regardless_of_urgency(self):
        v = st.load()
        a = st.queue_action(v, kind="k", boundary="CREDENTIALS_REQUIRED", what="w",
                            why="y", founder_action="f")
        st.resolve_queued_action(v, a["id"], status="RESOLVED")
        self.assertEqual(st.queued_action_status(a), "HISTORICAL_RESOLVED")


class FanvueRuntimeCredentialStorage(unittest.TestCase):
    """Same discipline as InstagramRuntimeCredentialStorage -- storage
    mechanics only, never asserts against real secret values."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_dir = fanvue_runtime.RUNTIME_DIR
        self._orig_cred = fanvue_runtime.CRED_PATH
        self._orig_pending = fanvue_runtime.PENDING_PATH
        fanvue_runtime.RUNTIME_DIR = Path(self._tmp.name) / ".fanvue_runtime"
        fanvue_runtime.CRED_PATH = fanvue_runtime.RUNTIME_DIR / "credentials.json"
        fanvue_runtime.PENDING_PATH = fanvue_runtime.RUNTIME_DIR / "oauth_pending.json"

    def tearDown(self):
        fanvue_runtime.RUNTIME_DIR = self._orig_dir
        fanvue_runtime.CRED_PATH = self._orig_cred
        fanvue_runtime.PENDING_PATH = self._orig_pending
        self._tmp.cleanup()

    def test_no_access_token_initially(self):
        self.assertFalse(fanvue_runtime.has_access_token())
        self.assertEqual(fanvue_runtime.load_credentials(), {})

    def test_store_tokens_merges_with_existing_client_credentials(self):
        fanvue_runtime._ensure_dir()
        fanvue_runtime.CRED_PATH.write_text(json.dumps({
            "app_id": "a1", "client_id": "c1", "client_secret": "dummy-test-secret"}))
        fanvue_runtime.store_tokens(access_token="dummy-test-token",
                                    refresh_token="dummy-test-refresh", expires_in=3600)
        creds = fanvue_runtime.load_credentials()
        self.assertTrue(fanvue_runtime.has_access_token())
        self.assertEqual(creds["client_id"], "c1")  # existing fields preserved

    def test_stored_file_is_owner_only_permissions(self):
        fanvue_runtime.store_tokens(access_token="dummy-test-token",
                                    refresh_token="dummy-test-refresh", expires_in=3600)
        mode = fanvue_runtime.CRED_PATH.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_pending_pkce_round_trip_and_clear(self):
        self.assertIsNone(fanvue_runtime.load_pending_pkce())
        fanvue_runtime._ensure_dir()
        fanvue_runtime.PENDING_PATH.write_text(json.dumps({
            "code_verifier": "dummy-verifier", "state": "dummy-state"}))
        self.assertEqual(fanvue_runtime.load_pending_pkce()["state"], "dummy-state")
        fanvue_runtime.clear_pending_pkce()
        self.assertIsNone(fanvue_runtime.load_pending_pkce())


class FanvueAuthExchange(unittest.TestCase):
    """Regression coverage for the OAuth code-exchange/refresh flow: state
    mismatch is refused (CSRF guard), successful exchange never returns the
    raw token values, and refresh_token rotation overwrites the stored value
    per Fanvue's single-use rotation rule."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_dir = fanvue_runtime.RUNTIME_DIR
        self._orig_cred = fanvue_runtime.CRED_PATH
        self._orig_pending = fanvue_runtime.PENDING_PATH
        fanvue_runtime.RUNTIME_DIR = Path(self._tmp.name) / ".fanvue_runtime"
        fanvue_runtime.CRED_PATH = fanvue_runtime.RUNTIME_DIR / "credentials.json"
        fanvue_runtime.PENDING_PATH = fanvue_runtime.RUNTIME_DIR / "oauth_pending.json"
        fanvue_runtime._ensure_dir()
        fanvue_runtime.CRED_PATH.write_text(json.dumps({
            "app_id": "a1", "client_id": "c1", "client_secret": "dummy-test-secret"}))
        self._orig_post = fanvue_auth._post_form

    def tearDown(self):
        fanvue_runtime.RUNTIME_DIR = self._orig_dir
        fanvue_runtime.CRED_PATH = self._orig_cred
        fanvue_runtime.PENDING_PATH = self._orig_pending
        fanvue_auth._post_form = self._orig_post
        self._tmp.cleanup()

    def test_exchange_rejects_state_mismatch_without_calling_token_endpoint(self):
        fanvue_runtime.PENDING_PATH.write_text(json.dumps({
            "code_verifier": "v1", "state": "expected-state"}))
        called = []
        fanvue_auth._post_form = lambda *a, **k: called.append(1) or {"access_token": "x"}

        result = fanvue_auth.exchange_code_for_tokens("some-code", "wrong-state")
        self.assertFalse(result["ok"])
        self.assertIn("state mismatch", result["error"])
        self.assertEqual(called, [])

    def test_successful_exchange_stores_tokens_and_never_returns_them(self):
        fanvue_runtime.PENDING_PATH.write_text(json.dumps({
            "code_verifier": "v1", "state": "expected-state"}))
        fanvue_auth._post_form = lambda *a, **k: {
            "access_token": "dummy-test-access", "refresh_token": "dummy-test-refresh",
            "expires_in": 3600, "scope": "read:self", "token_type": "bearer"}

        result = fanvue_auth.exchange_code_for_tokens("some-code", "expected-state")
        self.assertTrue(result["ok"])
        self.assertNotIn("access_token", result)
        self.assertNotIn("refresh_token", result)
        self.assertTrue(fanvue_runtime.has_access_token())
        self.assertIsNone(fanvue_runtime.load_pending_pkce())  # single-use, cleared

    def test_refresh_overwrites_old_refresh_token(self):
        fanvue_runtime.store_tokens(access_token="old-access", refresh_token="old-refresh",
                                    expires_in=3600)
        fanvue_auth._post_form = lambda *a, **k: {
            "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}

        result = fanvue_auth.refresh_access_token()
        self.assertTrue(result["ok"])
        creds = fanvue_runtime.load_credentials()
        self.assertEqual(creds["refresh_token"], "new-refresh")


class InstagramPublishMediaTypeVerification(unittest.TestCase):
    """Regression coverage for a real incident: publish_video() originally
    reused publish_image()'s image_url param for a video file. Instagram
    silently mishandled it (re-served a cached image) instead of erroring,
    so the bug was only caught by verifying the published media_type
    matches what was requested -- these tests lock that verification in."""

    def setUp(self):
        self._orig_post = instagram_publish._post
        self._orig_get = instagram_publish._get
        self._orig_load = instagram_runtime.load_token
        instagram_runtime.load_token = lambda: {
            "access_token": "dummy-test-token", "ig_user_id": "999"}

    def tearDown(self):
        instagram_publish._post = self._orig_post
        instagram_publish._get = self._orig_get
        instagram_runtime.load_token = self._orig_load

    def test_publish_video_uses_video_url_param_not_image_url(self):
        captured = {}

        def fake_post(url, params):
            if url.endswith("/media"):
                captured.update(params)
                return {"id": "container1"}
            return {"id": "published1"}

        instagram_publish._post = fake_post
        instagram_publish._get = lambda url, params: (
            {"status_code": "FINISHED"} if "container1" in url
            else {"media_type": "VIDEO", "permalink": "https://x/reel/1"})

        result = instagram_publish.publish_video("https://x/video.mp4", "caption")
        self.assertTrue(result["ok"])
        self.assertIn("video_url", captured)
        self.assertNotIn("image_url", captured)
        self.assertEqual(captured.get("media_type"), "REELS")

    def test_publish_video_flags_mismatch_if_platform_returns_wrong_media_type(self):
        """Exactly the bug that happened: container/publish calls succeed,
        but the platform's own record shows IMAGE for a requested video."""
        instagram_publish._post = lambda url, params: (
            {"id": "container1"} if url.endswith("/media") else {"id": "published1"})
        instagram_publish._get = lambda url, params: (
            {"status_code": "FINISHED"} if "container1" in url
            else {"media_type": "IMAGE", "permalink": "https://x/p/1"})

        result = instagram_publish.publish_video("https://x/video.mp4", "caption")
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "verify")
        self.assertIn("expected VIDEO", result["error"])

    def test_publish_image_uses_image_url_param(self):
        captured = {}

        def fake_post(url, params):
            if url.endswith("/media"):
                captured.update(params)
                return {"id": "container1"}
            return {"id": "published1"}

        instagram_publish._post = fake_post
        instagram_publish._get = lambda url, params: (
            {"status_code": "FINISHED"} if "container1" in url
            else {"media_type": "IMAGE", "permalink": "https://x/p/1"})

        result = instagram_publish.publish_image("https://x/pic.jpg", "caption")
        self.assertTrue(result["ok"])
        self.assertIn("image_url", captured)
        self.assertNotIn("video_url", captured)

    def test_main_auto_detects_video_by_file_extension(self):
        instagram_publish._post = lambda url, params: (
            {"id": "c1"} if url.endswith("/media") else {"id": "p1"})
        instagram_publish._get = lambda url, params: (
            {"status_code": "FINISHED"} if "c1" in url
            else {"media_type": "VIDEO", "permalink": "https://x/reel/1"})
        rc = instagram_publish.main(["https://x/clip.mp4", "caption"])
        self.assertEqual(rc, 0)


class FanvueMediaUpload(unittest.TestCase):
    """Regression coverage for the S3-style multipart upload flow: parts are
    numbered/sized correctly and the complete call carries every part's
    ETag, matching Fanvue's own multipart contract."""

    def setUp(self):
        self._orig_api = fanvue_media._api
        self._orig_load = fanvue_runtime.load_credentials
        fanvue_runtime.load_credentials = lambda: {"access_token": "dummy-test-token"}

    def tearDown(self):
        fanvue_media._api = self._orig_api
        fanvue_runtime.load_credentials = self._orig_load

    def test_upload_completes_with_etags_for_every_part(self):
        calls = []

        def fake_api(method, path, token, body=None):
            calls.append((method, path, body))
            if path == "/v1/media/uploads":
                return {"mediaUuid": "m1", "uploadId": "u1", "partSize": 1_000_000,
                        "maxParts": 10, "totalParts": 1}
            if "/parts/" in path:
                return "https://s3.example.com/signed-url"
            if method == "PATCH":
                return {"ok": True}
            return {}

        fanvue_media._api = fake_api

        import io
        import unittest.mock as mock
        fake_resp = mock.MagicMock()
        fake_resp.headers = {"ETag": '"abc123"'}
        fake_resp.__enter__ = lambda self: self
        fake_resp.__exit__ = lambda *a: None
        with mock.patch("urllib.request.urlopen", return_value=fake_resp):
            with tempfile.NamedTemporaryFile(suffix=".png") as f:
                f.write(b"fake-image-bytes")
                f.flush()
                result = fanvue_media.upload_media(f.name, "image")

        self.assertTrue(result["ok"])
        self.assertEqual(result["media_uuid"], "m1")
        complete_call = next(c for c in calls if c[0] == "PATCH")
        self.assertEqual(complete_call[2]["parts"], [{"PartNumber": 1, "ETag": "abc123"}])


class FanvuePostCreation(unittest.TestCase):
    """Regression coverage: a post is verified by re-fetching it, not just
    trusting the create call's 2xx -- same discipline as
    instagram_publish.py's media-type verification."""

    def setUp(self):
        self._orig_api = fanvue_posts._api
        self._orig_load = fanvue_runtime.load_credentials
        fanvue_runtime.load_credentials = lambda: {"access_token": "dummy-test-token"}

    def tearDown(self):
        fanvue_posts._api = self._orig_api
        fanvue_runtime.load_credentials = self._orig_load

    def test_create_post_verifies_via_get_before_reporting_ok(self):
        calls = []

        def fake_api(method, path, token, body=None):
            calls.append((method, path))
            if method == "POST":
                return {"uuid": "p1"}
            return {"audience": "followers-and-subscribers", "mediaUuids": ["m1"]}

        fanvue_posts._api = fake_api
        result = fanvue_posts.create_post("hello", ["m1"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["post_id"], "p1")
        self.assertIn(("GET", "/v1/posts/p1"), calls)

    def test_create_post_reports_failure_on_create_error(self):
        fanvue_posts._api = lambda *a, **k: {"_error": True, "status": 403}
        result = fanvue_posts.create_post("hello")
        self.assertFalse(result["ok"])


class FanvueMonetizationSetup(unittest.TestCase):
    """Regression coverage for pricing/promotions: price is verified via a
    GET (not just trusting the PATCH), and out-of-range prices are rejected
    client-side before ever calling the API."""

    def setUp(self):
        self._orig_api = fanvue_monetization._api
        self._orig_load = fanvue_runtime.load_credentials
        fanvue_runtime.load_credentials = lambda: {"access_token": "dummy-test-token"}

    def tearDown(self):
        fanvue_monetization._api = self._orig_api
        fanvue_runtime.load_credentials = self._orig_load

    def test_set_price_rejects_out_of_range_without_calling_api(self):
        called = []
        fanvue_monetization._api = lambda *a, **k: called.append(1) or {}
        result = fanvue_monetization.set_subscription_price(100)  # below 399 minimum
        self.assertFalse(result["ok"])
        self.assertEqual(called, [])

    def test_set_price_verifies_via_get_after_patch(self):
        calls = []

        def fake_api(method, path, token, body=None):
            calls.append(method)
            if method == "PATCH":
                return {}
            return {"subscriptionPrice": 499}

        fanvue_monetization._api = fake_api
        result = fanvue_monetization.set_subscription_price(499)
        self.assertTrue(result["ok"])
        self.assertEqual(result["confirmed_price_cents"], 499)
        self.assertIn("GET", calls)

    def test_create_promotion_with_free_trial(self):
        captured = {}

        def fake_api(method, path, token, body=None):
            captured.update(body or {})
            return {"uuid": "promo1"}

        fanvue_monetization._api = fake_api
        result = fanvue_monetization.create_promotion("new_subscribers", free_trial_days=3)
        self.assertTrue(result["ok"])
        self.assertTrue(captured["freeTrial"])
        self.assertEqual(captured["freeTrialDays"], 3)


class FanvueAutomatedMessages(unittest.TestCase):
    """Same discipline as fanvue_monetization's tests -- verify via a real
    re-fetch after the write, never trust the PUT response alone."""

    def setUp(self):
        self._orig_api = fanvue_automated_messages._api
        self._orig_load = fanvue_runtime.load_credentials
        fanvue_runtime.load_credentials = lambda: {"access_token": "dummy-test-token"}

    def tearDown(self):
        fanvue_automated_messages._api = self._orig_api
        fanvue_runtime.load_credentials = self._orig_load

    def test_unknown_trigger_rejected_without_calling_api(self):
        called = []
        fanvue_automated_messages._api = lambda *a, **k: called.append(1) or {}
        result = fanvue_automated_messages.set_automated_message("not_a_real_trigger", "hi")
        self.assertFalse(result["ok"])
        self.assertEqual(called, [])

    def test_set_verifies_via_get_after_put(self):
        calls = []

        def fake_api(method, path, token, body=None):
            calls.append(method)
            if method == "PUT":
                return {}
            return {"data": [{"trigger": "new_subscriber", "text": "hi there!",
                              "enabled": True, "updatedAt": "2026-09-13T00:00:00Z"}]}

        fanvue_automated_messages._api = fake_api
        result = fanvue_automated_messages.set_automated_message("new_subscriber", "hi there!")
        self.assertTrue(result["ok"])
        self.assertTrue(result["enabled"])
        self.assertIn("PUT", calls)
        self.assertIn("GET", calls)

    def test_set_fails_if_verification_text_does_not_match(self):
        def fake_api(method, path, token, body=None):
            if method == "PUT":
                return {}
            return {"data": [{"trigger": "new_subscriber", "text": "different text",
                              "enabled": True, "updatedAt": "2026-09-13T00:00:00Z"}]}

        fanvue_automated_messages._api = fake_api
        result = fanvue_automated_messages.set_automated_message("new_subscriber", "hi there!")
        self.assertFalse(result["ok"])


class InstagramMetricsFetch(unittest.TestCase):
    """instagram_metrics.py never prints/returns the token, picks the right
    insights metric set per media_type, and degrades gracefully (never
    raises) when the insights call itself errors."""

    def setUp(self):
        self._orig_get = instagram_metrics._get
        self._orig_load = instagram_runtime.load_token
        instagram_runtime.load_token = lambda: {
            "access_token": "dummy-test-token", "ig_user_id": "999"}

    def tearDown(self):
        instagram_metrics._get = self._orig_get
        instagram_runtime.load_token = self._orig_load

    def test_uses_video_metric_set_for_video_media(self):
        captured = {}

        def fake_get(url, params):
            if url.endswith("/insights"):
                captured.update(params)
                return {"data": []}
            return {"media_type": "VIDEO", "like_count": 1, "comments_count": 2,
                    "timestamp": "t", "permalink": "p"}

        instagram_metrics._get = fake_get
        result = instagram_metrics.fetch_media_metrics("m1")
        self.assertTrue(result["ok"])
        self.assertIn("ig_reels_avg_watch_time", captured["metric"])
        self.assertNotIn("access_token", result)

    def test_uses_image_metric_set_for_image_media(self):
        captured = {}

        def fake_get(url, params):
            if url.endswith("/insights"):
                captured.update(params)
                return {"data": []}
            return {"media_type": "IMAGE", "like_count": 0, "comments_count": 0,
                    "timestamp": "t", "permalink": "p"}

        instagram_metrics._get = fake_get
        result = instagram_metrics.fetch_media_metrics("m2")
        self.assertTrue(result["ok"])
        self.assertNotIn("ig_reels_avg_watch_time", captured["metric"])

    def test_insights_failure_does_not_crash_the_whole_fetch(self):
        def fake_get(url, params):
            if url.endswith("/insights"):
                raise RuntimeError("HTTP 400")
            return {"media_type": "IMAGE", "like_count": 5, "comments_count": 1,
                    "timestamp": "t", "permalink": "p"}

        instagram_metrics._get = fake_get
        result = instagram_metrics.fetch_media_metrics("m3")
        self.assertTrue(result["ok"])
        self.assertIn("_error", result["insights"])
        self.assertEqual(result["like_count"], 5)


class ExtractJsonTolerance(unittest.TestCase):
    """Regression: a real tick failed with 'Expecting , delimiter' because a
    long free-text reasoning field contained a literal unescaped newline --
    a real, observed failure mode once fields got longer (bottleneck/
    manager_status), not a hypothetical one. strict=False tolerates literal
    control characters inside strings without relaxing the required schema
    (stages.py's _require still runs on whatever this returns)."""

    def test_strict_valid_json_still_parses(self):
        d = claude_client.extract_json('{"a": 1}')
        self.assertEqual(d["a"], 1)

    def test_literal_newline_inside_string_value_is_tolerated(self):
        raw = '{"reasoning": "line one\nline two", "action": "no_action_needed"}'
        d = claude_client.extract_json(raw)
        self.assertIn("line one", d["reasoning"])

    def test_genuinely_invalid_json_still_raises(self):
        with self.assertRaises(ValueError):
            claude_client.extract_json("not json at all, no braces either")


class ManagerDecidePhaseValidation(unittest.TestCase):
    """Regression coverage for a real incident: the model proposed a
    plausible-sounding but non-existent phase name ('READY_FOR_CONTENT_
    PRODUCTION') for advance_phase. manager_decide must reject any to_phase
    not in the fixed st.PHASES enum, not just any dispatch-time check."""

    def setUp(self):
        self._orig_call = stages.claude_call

    def tearDown(self):
        stages.claude_call = self._orig_call

    def test_advance_phase_to_invalid_phase_name_is_rejected(self):
        import json
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "action": "advance_phase",
            "params": {"to_phase": "READY_FOR_CONTENT_PRODUCTION"},
            "reasoning": "sounds plausible but is not a real phase",
            "bottleneck": "phase gate",
        }), "cost_usd": 0.01}
        with self.assertRaises(stages.StageValidationError):
            stages.manager_decide("{}", "(none)", st.PHASES)

    def test_advance_phase_to_valid_phase_name_is_accepted(self):
        import json
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "action": "advance_phase",
            "params": {"to_phase": "CONTENT_TEST_ACTIVE"},
            "reasoning": "accounts are set up",
            "bottleneck": "none -- ready to advance",
        }), "cost_usd": 0.01}
        d = stages.manager_decide("{}", "(none)", st.PHASES)
        self.assertEqual(d["params"]["to_phase"], "CONTENT_TEST_ACTIVE")

    def test_no_action_needed_requires_evaluation_trigger(self):
        import json
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "action": "no_action_needed", "params": {}, "reasoning": "waiting on q5",
            "bottleneck": "Instagram API blocked",
        }), "cost_usd": 0.01}
        with self.assertRaises(stages.StageValidationError):
            stages.manager_decide("{}", "(none)", st.PHASES)

    def test_no_action_needed_with_full_schema_is_accepted(self):
        import json
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "action": "no_action_needed", "params": {}, "reasoning": "waiting on q5",
            "bottleneck": "Instagram API blocked", "next_evaluation_at": "2026-09-13T00:00:00Z",
            "awaiting": "q5 resolved or 48h cadence window for next TikTok post",
        }), "cost_usd": 0.01}
        d = stages.manager_decide("{}", "(none)", st.PHASES)
        self.assertEqual(d["awaiting"], "q5 resolved or 48h cadence window for next TikTok post")


class SalesManagerChatResponseValidation(unittest.TestCase):
    def setUp(self):
        self._orig_call = stages.claude_call

    def tearDown(self):
        stages.claude_call = self._orig_call

    def test_rejects_unknown_action(self):
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "intent_classification": "genuine_fan", "action": "send_gift",
            "response_text": "", "reasoning": "x"}), "cost_usd": 0.01}
        with self.assertRaises(stages.StageValidationError):
            stages.sales_manager_chat_response({}, [], {}, {})

    def test_respond_action_requires_nonempty_response_text(self):
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "intent_classification": "genuine_fan", "action": "respond",
            "response_text": "", "reasoning": "x"}), "cost_usd": 0.01}
        with self.assertRaises(stages.StageValidationError):
            stages.sales_manager_chat_response({}, [], {}, {})

    def test_ppv_requires_price_at_or_above_300_cents(self):
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "intent_classification": "buyer_signal", "action": "ppv",
            "response_text": "here's a little something 😉", "ppv_price_cents": 100,
            "reasoning": "x"}), "cost_usd": 0.01}
        with self.assertRaises(stages.StageValidationError):
            stages.sales_manager_chat_response({}, [], {}, {})

    def test_valid_no_response_decision_is_accepted(self):
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "intent_classification": "spam_or_scam", "action": "no_response",
            "response_text": "", "reasoning": "off-platform redirect spam"}), "cost_usd": 0.01}
        d = stages.sales_manager_chat_response({}, [], {}, {})
        self.assertEqual(d["action"], "no_response")

    def test_ppv_with_no_media_uuid_is_rejected_even_with_valid_price(self):
        """2026-09-13 fix (the core PPV bug): Fanvue itself rejects a priced
        message with no attached media -- this must be caught in code, not
        discovered as a live HTTP 400 against a real fan."""
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "intent_classification": "buyer_signal", "action": "ppv",
            "response_text": "here's a little something 😉", "ppv_price_cents": 500,
            "ppv_media_uuid": "", "reasoning": "x"}), "cost_usd": 0.01}
        with self.assertRaises(stages.StageValidationError):
            stages.sales_manager_chat_response({}, [], {}, {}, available_media=[
                {"uuid": "real-media-1", "media_type": "image"}])

    def test_ppv_media_uuid_must_be_in_available_media_never_fabricated(self):
        """The model claiming a uuid isn't enough -- it must be one we
        actually handed it. Prevents ever sending an unintended/invented
        asset."""
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "intent_classification": "buyer_signal", "action": "ppv",
            "response_text": "here's a little something 😉", "ppv_price_cents": 500,
            "ppv_media_uuid": "invented-uuid-not-in-list", "reasoning": "x"}), "cost_usd": 0.01}
        with self.assertRaises(stages.StageValidationError):
            stages.sales_manager_chat_response({}, [], {}, {}, available_media=[
                {"uuid": "real-media-1", "media_type": "image"}])

    def test_ppv_with_real_available_media_uuid_is_accepted(self):
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "intent_classification": "buyer_signal", "action": "ppv",
            "response_text": "here's a little something 😉", "ppv_price_cents": 500,
            "ppv_media_uuid": "real-media-1", "reasoning": "x"}), "cost_usd": 0.01}
        d = stages.sales_manager_chat_response({}, [], {}, {}, available_media=[
            {"uuid": "real-media-1", "media_type": "image"}])
        self.assertEqual(d["ppv_media_uuid"], "real-media-1")

    def test_ppv_with_empty_available_media_list_always_rejected(self):
        """Missing media must fail safely -- no media exists at all this
        tick, so no ppv_media_uuid can ever validate."""
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "intent_classification": "buyer_signal", "action": "ppv",
            "response_text": "here's a little something 😉", "ppv_price_cents": 500,
            "ppv_media_uuid": "anything", "reasoning": "x"}), "cost_usd": 0.01}
        with self.assertRaises(stages.StageValidationError):
            stages.sales_manager_chat_response({}, [], {}, {}, available_media=[])


class ManagerDispatch(_TmpState):
    def setUp(self):
        super().setUp()
        self._orig_research = stages.research_subtask
        self._orig_compliance = stages.compliance_checker
        self._orig_persona = stages.persona_designer
        self._orig_content = stages.content_brief_generator
        self._orig_metrics = stages.metrics_analyst
        self._orig_gate = stages.go_kill_gate
        self._orig_decide = stages.manager_decide
        self._orig_growth = stages.growth_manager_audit
        self._orig_content_audit = stages.content_director_audit
        self._orig_sales = stages.sales_manager_audit

    def tearDown(self):
        stages.research_subtask = self._orig_research
        stages.compliance_checker = self._orig_compliance
        stages.persona_designer = self._orig_persona
        stages.content_brief_generator = self._orig_content
        stages.metrics_analyst = self._orig_metrics
        stages.go_kill_gate = self._orig_gate
        stages.manager_decide = self._orig_decide
        stages.growth_manager_audit = self._orig_growth
        stages.content_director_audit = self._orig_content_audit
        stages.sales_manager_audit = self._orig_sales
        super().tearDown()

    def test_dispatch_run_growth_audit_records_specialist_findings(self):
        v = st.load()
        stages.growth_manager_audit = lambda ctx, persona: {
            "findings": ["no profile picture"], "funnel_gaps": ["no Fanvue link in bio"],
            "recommendations": [{"action": "add link", "platform": "instagram",
                                 "executable_now": True, "why_not_executable": ""}],
            "priority_order": ["add link"], "summary": "profile incomplete", "_cost_usd": 0.01}
        made_progress, detail = manager._dispatch(v, "run_growth_audit",
                                                   {"live_state_context": "observed: ..."})
        self.assertTrue(made_progress)
        self.assertEqual(v["specialists"]["growth"]["summary"], "profile incomplete")
        self.assertIsNotNone(v["specialists"]["growth"]["last_run_at"])

    def test_dispatch_run_content_audit_records_specialist_findings(self):
        v = st.load()
        stages.content_director_audit = lambda ctx, persona, plan: {
            "findings": ["three near-identical thumbnails"], "continuity_risks": ["repetition"],
            "recommendations": [], "priority_order": [], "summary": "grid too repetitive",
            "_cost_usd": 0.01}
        manager._dispatch(v, "run_content_audit", {"live_state_context": "observed: ..."})
        self.assertEqual(v["specialists"]["content"]["summary"], "grid too repetitive")

    def test_dispatch_run_sales_audit_records_specialist_findings(self):
        v = st.load()
        stages.sales_manager_audit = lambda ctx, persona: {
            "findings": ["no subscription price set"], "funnel_blockers": ["zero posts"],
            "recommendations": [], "priority_order": [], "summary": "not monetization-ready",
            "_cost_usd": 0.01}
        manager._dispatch(v, "run_sales_audit", {"live_state_context": "observed: ..."})
        self.assertEqual(v["specialists"]["sales"]["summary"], "not monetization-ready")

    def test_dispatch_research_subtask_appends_to_log(self):
        v = st.load()
        stages.research_subtask = lambda q: {
            "question": q, "findings": "found it", "sources": ["http://x"],
            "confidence": "high", "qa_verdict": "PASS", "qa_reasons": [], "_cost_usd": 0.01}
        made_progress, detail = manager._dispatch(v, "run_research_subtask", {"question": "q?"})
        self.assertTrue(made_progress)
        self.assertEqual(len(v["research_log"]), 1)

    def test_dispatch_request_approval_writes_pending(self):
        v = st.load()
        manager._dispatch(v, "request_approval", {
            "reason": "CREDENTIALS_REQUIRED", "what": "connect tiktok",
            "why": "need to post", "founder_action": "click the OAuth link",
            "estimated_minutes": 3})
        p = st.load_pending_approval()
        self.assertEqual(p["reason"], "CREDENTIALS_REQUIRED")

    def test_dispatch_advance_phase_rejects_unknown_phase(self):
        v = st.load()
        with self.assertRaises(stages.StageValidationError):
            manager._dispatch(v, "advance_phase", {"to_phase": "NOT_A_REAL_PHASE"})

    def test_content_brief_requires_persona_first(self):
        v = st.load()
        with self.assertRaises(stages.StageValidationError):
            manager._dispatch(v, "run_content_brief", {})

    def test_go_kill_gate_always_creates_founder_approval(self):
        v = st.load()
        stages.go_kill_gate = lambda h1, h2, n: {
            "recommendation": "VALIDATE", "reasoning": "strong signal",
            "h1_summary": "good", "h2_summary": "good", "_cost_usd": 0.02}
        manager._dispatch(v, "run_go_kill_gate", {})
        p = st.load_pending_approval()
        self.assertIsNotNone(p)
        self.assertEqual(p["reason"], "MAJOR_GO_KILL_DECISION")

    def test_dispatch_no_action_needed_logs_the_actual_reasoning(self):
        """Regression: no_action_needed used to discard manager_decide's
        required 'reasoning' field and log a static placeholder instead --
        indistinguishable from the system silently doing nothing. A no-op
        must carry a visible, current business reason."""
        v = st.load()
        made_progress, detail = manager._dispatch(
            v, "no_action_needed", {},
            reasoning="A_vs_C_stage1 observation window closed but Instagram Graph API access is "
                      "hard-blocked (q5, founder-only remediation); nothing new is due under the "
                      "2-posts/week cadence for another ~1 day.")
        self.assertFalse(made_progress)
        self.assertIn("Instagram Graph API access is hard-blocked", detail)
        self.assertNotEqual(detail, "no useful action right now")

    def test_dispatch_no_action_needed_falls_back_if_reasoning_missing(self):
        v = st.load()
        made_progress, detail = manager._dispatch(v, "no_action_needed", {})
        self.assertFalse(made_progress)
        self.assertIn("no reasoning", detail)

    def test_tick_persists_no_action_reasoning_into_audit_log(self):
        stages.manager_decide = lambda state_json, recent, phases: {
            "action": "no_action_needed", "params": {},
            "reasoning": "waiting on q5 (Instagram API block) before A_vs_C_stage1 can conclude",
            "_cost_usd": 0.01}
        manager.tick()
        entries = audit.load(limit=1)
        self.assertIn("waiting on q5", entries[0]["detail"])

    def test_tick_preserves_pending_approval_while_doing_unrelated_work(self):
        """The core fix (2026-09-13): a founder-only boundary blocks ONLY
        the specific action it names, never the whole venture. A pending
        approval that already existed before this tick started must
        survive untouched while the tick still does other genuinely
        independent work."""
        st.write_pending_approval(reason="CANNOT_SAFELY_DELEGATE",
                                  what="content_items[10] needs manual Instagram publish",
                                  why="upload tooling limitation",
                                  founder_action="publish manually", estimated_minutes=3)
        original = st.load_pending_approval()

        stages.manager_decide = lambda state_json, recent, phases: {
            "action": "run_metrics_analysis", "params": {},
            "reasoning": "unrelated safe work -- does not touch the blocked item",
            "_cost_usd": 0.0}
        stages.metrics_analyst = lambda metrics, thresholds: {
            "h1_status": "ok", "h1_meets_success": False, "h1_meets_kill": False,
            "h2_status": "ok", "h2_meets_operability_target": True,
            "summary": "fine", "_cost_usd": 0.0}

        result = manager.tick()

        self.assertEqual(result["actions"][0]["action"], "run_metrics_analysis")
        p = st.load_pending_approval()
        self.assertIsNotNone(p)
        self.assertEqual(p, original)  # untouched, not rewritten/duplicated

    def test_tick_informs_manager_decide_of_the_pending_approval(self):
        """manager_decide must actually see the pending approval (so it can
        route around the blocked resource) rather than being kept in the
        dark about why one action is off-limits."""
        st.write_pending_approval(reason="CANNOT_SAFELY_DELEGATE",
                                  what="content_items[10] needs manual Instagram publish",
                                  why="upload tooling limitation",
                                  founder_action="publish manually", estimated_minutes=3)
        captured = {}

        def fake_decide(state_json, recent, phases):
            captured["state_json"] = state_json
            return {"action": "no_action_needed", "params": {},
                   "reasoning": "nothing independent left this tick",
                   "next_evaluation_at": "2026-01-01T00:00:00Z", "awaiting": "founder",
                   "_cost_usd": 0.0}

        stages.manager_decide = fake_decide
        manager.tick()
        self.assertIn("pending_founder_approval", captured["state_json"])
        self.assertIn("content_items[10] needs manual Instagram publish", captured["state_json"])

    def test_tick_waits_safely_when_pending_and_nothing_independent_left(self):
        """When a real founder boundary exists and no independent work
        remains, the tick must wait -- not invent busy-work, not clear the
        approval, not touch the blocked item."""
        st.write_pending_approval(reason="CANNOT_SAFELY_DELEGATE", what="x", why="y",
                                  founder_action="z", estimated_minutes=3)
        stages.manager_decide = lambda state_json, recent, phases: {
            "action": "no_action_needed", "params": {},
            "reasoning": "everything else genuinely depends on the blocked item too",
            "next_evaluation_at": "2026-01-01T00:00:00Z", "awaiting": "founder",
            "_cost_usd": 0.0}
        result = manager.tick()
        self.assertIsNotNone(st.load_pending_approval())
        self.assertEqual(result["actions"][0]["action"], "no_action_needed")

    def test_dispatch_request_approval_refuses_a_true_duplicate(self):
        """A true duplicate -- same reason AND same underlying need -- is
        still refused; never silently overwrite/re-derive an unresolved
        one."""
        v = st.load()
        st.write_pending_approval(reason="SPEND_REQUIRED", what="first issue", why="y",
                                  founder_action="z", estimated_minutes=1)
        made_progress, detail = manager._dispatch(v, "request_approval", {
            "reason": "SPEND_REQUIRED", "what": "first issue", "why": "y2 -- re-derived",
            "founder_action": "z2", "estimated_minutes": 2})
        self.assertFalse(made_progress)
        items = st.load_pending_approvals()
        self.assertEqual(len(items), 1)  # not duplicated
        self.assertEqual(items[0]["what"], "first issue")  # unchanged, not overwritten

    def test_dispatch_request_approval_allows_a_genuinely_different_one(self):
        """2026-09-16 fix: an unrelated pending approval (different reason,
        different underlying need -- e.g. a stuck Instagram upload) must
        never block a genuinely different founder-consent request (e.g.
        TikTok publish consent) from being queued alongside it."""
        v = st.load()
        st.write_pending_approval(reason="CANNOT_SAFELY_DELEGATE",
                                  what="Instagram video upload is stuck", why="y",
                                  founder_action="z", estimated_minutes=3)
        made_progress, detail = manager._dispatch(v, "request_approval", {
            "reason": "AUTHORIZATION_REQUIRED", "what": "TikTok publish consent for item 16",
            "why": "y2", "founder_action": "z2", "estimated_minutes": 1})
        self.assertTrue(made_progress)
        items = st.load_pending_approvals()
        self.assertEqual(len(items), 2)  # both coexist
        self.assertEqual({it["what"] for it in items},
                         {"Instagram video upload is stuck", "TikTok publish consent for item 16"})

    def test_dispatch_refuses_an_approval_that_only_asks_to_publish_on_a_nyx_account(self):
        """Founder authorization 2026-09-21: publishing/scheduling on Nyx's own
        accounts is not a founder boundary, so asking for it is refused and the
        publish path is used instead -- no founder time, no queue entry."""
        v = st.load()
        made_progress, detail = manager._dispatch(v, "request_approval", {
            "reason": "CANNOT_SAFELY_DELEGATE",
            "what": "schedule content_items[17] to TikTok @nyx.emberfall1 via Metricool",
            "why": "it is QA_PASSED and the cadence slot is free",
            "founder_action": "approve the post", "estimated_minutes": 3})
        self.assertFalse(made_progress)
        self.assertIn("standing-authorized", detail)
        self.assertEqual(st.load_pending_approvals(), [])

    def test_publish_authorization_never_covers_spend_people_gates_routing_or_identity(self):
        """Everything the founder kept: an ask that also touches spend, a real
        person, a consent gate / hold / approval mechanism, Metricool routing,
        Nyx's identity or a non-Nyx account is still filed normally."""
        still_founder = [
            ("SPEND_REQUIRED", "generate and publish 5 carousel slides on Instagram @nyx.emberfall",
             "needs 2.5 Higgsfield credits"),
            ("CANNOT_SAFELY_DELEGATE", "publish to Instagram @nyx.emberfall and DM the fan who asked",
             "a message to a real person"),
            ("AUTHORIZATION_REQUIRED", "release the publish hold on tiktok:16 so it can be scheduled",
             "a hold release is a safety control"),
            ("AUTHORIZATION_REQUIRED", "switch the TikTok route to direct before publishing item 17",
             "changes Metricool routing"),
            ("CANNOT_SAFELY_DELEGATE", "publish a Reel to @ashkanfaraa",
             "not a Nyx-owned account"),
        ]
        for reason, what, why in still_founder:
            with self.subTest(what=what):
                self.assertFalse(st.publish_only_approval_is_authorized(
                    reason=reason, what=what, why=why, founder_action="decide"), what)
        self.assertTrue(st.publish_only_approval_is_authorized(
            reason="CANNOT_SAFELY_DELEGATE", what="publish content_items[2] on Instagram",
            why="QA_PASSED and ready", founder_action="approve it"))

    def test_tick_advances_unrelated_publish_ready_work_instead_of_repeated_waiting(self):
        """The actual systemic failure this whole audit is about, reproduced
        end to end through the real tick() loop rather than only at the
        _dispatch unit level: a commercially-stagnant-but-safe-work-exists
        state (one platform's approval stuck for days, three fully
        QA_PASSED, publish-ready items sitting idle on a DIFFERENT,
        unrelated platform) must make real progress -- request the genuinely
        independent founder consent it needs -- rather than the Manager
        repeatedly deciding "no_action_needed" purely because something else
        is already pending. Before the 2026-09-16 fix, manager_decide was
        explicitly instructed never to even propose a second
        "request_approval" while one was open, and the dispatcher would
        have refused it outright regardless; this proves the whole path is
        now open end to end."""
        st.write_pending_approval(reason="CANNOT_SAFELY_DELEGATE",
                                  what="content_items[10] Instagram upload is stuck",
                                  why="upload tooling limitation",
                                  founder_action="publish manually", estimated_minutes=3)

        stages.manager_decide = lambda state_json, recent, phases: {
            "action": "request_approval",
            "params": {"reason": "AUTHORIZATION_REQUIRED",
                      "what": "TikTok publish consent for items 16/17/19",
                      "why": "3 QA_PASSED TikTok items are publish-ready and unrelated to the "
                             "stuck Instagram item",
                      "founder_action": "click through TikTok's per-post consent for each",
                      "estimated_minutes": 3},
            "reasoning": "3 publish-ready TikTok assets are sitting idle behind a platform "
                        "consent click that has nothing to do with the pending Instagram issue",
            "_cost_usd": 0.0}

        result = manager.tick()

        self.assertEqual(result["actions"][0]["action"], "request_approval")
        self.assertNotIn("already pending", result["actions"][0]["detail"])
        items = st.load_pending_approvals()
        self.assertEqual(len(items), 2)
        whats = {it["what"] for it in items}
        self.assertIn("content_items[10] Instagram upload is stuck", whats)
        self.assertIn("TikTok publish consent for items 16/17/19", whats)

    def test_tick_stops_after_request_approval_even_under_call_budget(self):
        calls = {"n": 0}

        def fake_decide(state_json, recent, valid_phases):
            calls["n"] += 1
            return {"action": "request_approval",
                   "params": {"reason": "SPEND_REQUIRED", "what": "buy credits",
                             "why": "images", "founder_action": "approve $5",
                             "estimated_minutes": 2},
                   "reasoning": "need spend", "_cost_usd": 0.01}

        stages.manager_decide = fake_decide
        result = manager.tick()
        self.assertEqual(calls["n"], 1)  # must not keep deciding once blocked
        self.assertEqual(len(result["actions"]), 1)
        self.assertIsNotNone(st.load_pending_approval())


class FanvueOAuthReconsent(_TmpState):
    """The founder-click-Allow step can never be simulated -- these tests
    cover only the mechanical parts around it: generating a correct
    authorize URL with PKCE, and completing the exchange once Fanvue
    redirects back with a real code."""

    def setUp(self):
        super().setUp()
        self._orig_exchange = fanvue_auth.exchange_code_for_tokens
        v = st.load()
        v.setdefault("accounts", {})["fanvue"] = {"scopes_granted": ["read:self"]}
        st.queue_action(v, kind="FANVUE_CHAT_SCOPE_REEVALUATION", boundary="AUTHORIZATION_REQUIRED",
                        what="add scopes", why="fans messaging", founder_action="click allow",
                        urgency="blocking_now", estimated_minutes=10)
        st.save(v)

    def tearDown(self):
        fanvue_auth.exchange_code_for_tokens = self._orig_exchange
        super().tearDown()

    def test_callback_success_resolves_q6_and_updates_scopes(self):
        fanvue_auth.exchange_code_for_tokens = lambda code, state: {
            "ok": True, "scope": "read:self read:chat write:chat", "expires_in": 3600}
        status, html = server._fanvue_oauth_callback_page(
            {"code": ["realcode"], "state": ["realstate"]})
        self.assertEqual(status, 200)
        self.assertIn("complete", html.lower())
        v = st.load()
        self.assertIn("read:chat", v["accounts"]["fanvue"]["scopes_granted"])
        q6 = next(a for a in v["queued_actions"] if a["kind"] == "FANVUE_CHAT_SCOPE_REEVALUATION")
        self.assertEqual(q6["status"], "RESOLVED_BY_FOUNDER")

    def test_callback_missing_code_does_not_touch_state(self):
        status, html = server._fanvue_oauth_callback_page({"error": ["access_denied"]})
        self.assertEqual(status, 400)
        v = st.load()
        q6 = next(a for a in v["queued_actions"] if a["kind"] == "FANVUE_CHAT_SCOPE_REEVALUATION")
        self.assertEqual(q6["status"], "WAITING_FOR_FOUNDER_CONSENT")

    def test_callback_exchange_failure_does_not_resolve_q6(self):
        fanvue_auth.exchange_code_for_tokens = lambda code, state: {"ok": False, "error": "boom"}
        status, html = server._fanvue_oauth_callback_page(
            {"code": ["realcode"], "state": ["realstate"]})
        self.assertEqual(status, 500)
        v = st.load()
        q6 = next(a for a in v["queued_actions"] if a["kind"] == "FANVUE_CHAT_SCOPE_REEVALUATION")
        self.assertEqual(q6["status"], "WAITING_FOR_FOUNDER_CONSENT")

    def test_callback_success_without_chat_scope_leaves_q6_open(self):
        """A re-consent that somehow doesn't include chat scope must not be
        mistaken for resolving q6 -- only the actual presence of both
        read:chat and write:chat counts."""
        fanvue_auth.exchange_code_for_tokens = lambda code, state: {
            "ok": True, "scope": "read:self", "expires_in": 3600}
        server._fanvue_oauth_callback_page({"code": ["c"], "state": ["s"]})
        v = st.load()
        q6 = next(a for a in v["queued_actions"] if a["kind"] == "FANVUE_CHAT_SCOPE_REEVALUATION")
        self.assertEqual(q6["status"], "WAITING_FOR_FOUNDER_CONSENT")


class BuildAuthorizeUrl(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_runtime_dir = fanvue_runtime.RUNTIME_DIR
        self._orig_cred_path = fanvue_runtime.CRED_PATH
        self._orig_pending_path = fanvue_runtime.PENDING_PATH
        fanvue_runtime.RUNTIME_DIR = Path(self._tmp.name)
        fanvue_runtime.CRED_PATH = fanvue_runtime.RUNTIME_DIR / "credentials.json"
        fanvue_runtime.PENDING_PATH = fanvue_runtime.RUNTIME_DIR / "oauth_pending.json"
        fanvue_runtime.CRED_PATH.write_text(json.dumps({"client_id": "test-client-id"}))

    def tearDown(self):
        fanvue_runtime.RUNTIME_DIR = self._orig_runtime_dir
        fanvue_runtime.CRED_PATH = self._orig_cred_path
        fanvue_runtime.PENDING_PATH = self._orig_pending_path
        self._tmp.cleanup()

    def test_url_contains_all_requested_scopes_and_pkce_params(self):
        url = fanvue_auth.build_authorize_url(["read:chat", "write:chat"])
        self.assertIn("auth.fanvue.com/oauth2/auth", url)
        self.assertIn("client_id=test-client-id", url)
        self.assertIn("code_challenge=", url)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("read%3Achat", url)
        self.assertIn("write%3Achat", url)

    def test_persists_pending_pkce_for_later_exchange(self):
        fanvue_auth.build_authorize_url(["openid"])
        pending = fanvue_runtime.load_pending_pkce()
        self.assertIsNotNone(pending)
        self.assertIn("state", pending)
        self.assertIn("code_verifier", pending)


class ApprovalFlow(_TmpState):
    def test_approve_go_kill_validate_advances_phase(self):
        v = st.load()
        v["last_go_kill_recommendation"] = {"recommendation": "VALIDATE", "reasoning": "r"}
        st.save(v)
        st.write_pending_approval(reason="MAJOR_GO_KILL_DECISION", what="recommend VALIDATE",
                                  why="r", founder_action="approve", estimated_minutes=10)
        result = server.approve("VALIDATED")
        self.assertTrue(result.get("ok"))
        v2 = st.load()
        self.assertEqual(v2["decision"], "VALIDATED")
        self.assertEqual(v2["phase"], "AUTOMATION_FEASIBILITY")
        self.assertIsNone(st.load_pending_approval())

    def test_approve_go_kill_kill_closes_venture(self):
        v = st.load()
        v["last_go_kill_recommendation"] = {"recommendation": "KILL", "reasoning": "r"}
        st.save(v)
        st.write_pending_approval(reason="MAJOR_GO_KILL_DECISION", what="recommend KILL",
                                  why="r", founder_action="approve", estimated_minutes=10)
        server.approve("KILL")
        self.assertEqual(st.load()["phase"], "VENTURE_CLOSED")

    def test_approve_kyc_records_as_setup_overhead_not_operational(self):
        st.write_pending_approval(reason="IDENTITY_KYC_REQUIRED", what="verify identity",
                                  why="fanvue requires it", founder_action="upload ID",
                                  estimated_minutes=30)
        server.approve("approve")
        v = st.load()
        self.assertEqual(v["metrics"]["h2_operability"]["operational_minutes_this_week"], 0)
        self.assertEqual(v["metrics"]["h2_operability"]["operational_minutes_cumulative"], 0)
        self.assertEqual(v["metrics"]["h2_operability"]["setup_overhead_minutes_cumulative"], 30)

    def test_approve_with_nothing_pending_errors(self):
        result = server.approve("approve")
        self.assertIn("error", result)


class WanRuntimeCredentialStorage(unittest.TestCase):
    """Same discipline test as instagram_runtime's -- never asserts on the
    actual key value, only on presence/permissions."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_dir = wan_runtime.RUNTIME_DIR
        self._orig_path = wan_runtime.CREDENTIALS_PATH
        wan_runtime.RUNTIME_DIR = Path(self._tmp.name) / ".wan_runtime"
        wan_runtime.CREDENTIALS_PATH = wan_runtime.RUNTIME_DIR / "credentials.json"

    def tearDown(self):
        wan_runtime.RUNTIME_DIR = self._orig_dir
        wan_runtime.CREDENTIALS_PATH = self._orig_path
        self._tmp.cleanup()

    def test_no_credentials_stored_initially(self):
        self.assertFalse(wan_runtime.has_credentials())
        self.assertIsNone(wan_runtime.load_api_key())

    def test_store_and_load_round_trip(self):
        wan_runtime.store_api_key("dummy-test-key")
        self.assertTrue(wan_runtime.has_credentials())
        self.assertEqual(wan_runtime.load_api_key()["api_key"], "dummy-test-key")

    def test_credentials_file_is_owner_only(self):
        wan_runtime.store_api_key("dummy-test-key")
        mode = wan_runtime.CREDENTIALS_PATH.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


class WanProviderCostEstimation(unittest.TestCase):
    """The budget guardrail is only as good as this math -- never trust an
    LLM's self-reported estimate, always recompute deterministically.

    Isolates wan_runtime's credential path to an empty tmp dir for every
    test in this class (a real key is now stored on this machine, and
    these tests must never depend on -- or accidentally exercise a real
    network call against -- the real stored credentials)."""

    def setUp(self):
        self._orig_dir = wan_runtime.RUNTIME_DIR
        self._orig_path = wan_runtime.CREDENTIALS_PATH
        self._tmp = tempfile.TemporaryDirectory()
        wan_runtime.RUNTIME_DIR = Path(self._tmp.name) / ".wan_runtime"
        wan_runtime.CREDENTIALS_PATH = wan_runtime.RUNTIME_DIR / "credentials.json"

    def tearDown(self):
        wan_runtime.RUNTIME_DIR = self._orig_dir
        wan_runtime.CREDENTIALS_PATH = self._orig_path
        self._tmp.cleanup()

    def test_flash_720p_5s_matches_confirmed_baseline_rate(self):
        cost = wan_provider.estimate_cost_usd("wan2.2-i2v-flash", "720P", 5)
        self.assertEqual(cost, 0.5)  # $0.10/s x 5s, Alibaba's own confirmed baseline rate

    def test_flash_480p_5s_cheaper_than_720p(self):
        cost_480 = wan_provider.estimate_cost_usd("wan2.2-i2v-flash", "480P", 5)
        cost_720 = wan_provider.estimate_cost_usd("wan2.2-i2v-flash", "720P", 5)
        self.assertLess(cost_480, cost_720)

    def test_unknown_model_rejected(self):
        with self.assertRaises(ValueError):
            wan_provider.estimate_cost_usd("wan2.2-i2v-ultra-deluxe", "720P", 5)

    def test_unsupported_resolution_for_model_rejected(self):
        with self.assertRaises(ValueError):
            wan_provider.estimate_cost_usd("wan2.2-i2v-flash", "1080P", 5)  # flash caps at 720p

    def test_free_quota_protected_model_has_no_dollar_estimate(self):
        # wan2.7-i2v's economics are governed by the account's free quota +
        # Stop-on-Exhaust, not a confirmed $/sec rate -- estimate_cost_usd
        # must refuse rather than silently return a number for it.
        with self.assertRaises(ValueError):
            wan_provider.estimate_cost_usd("wan2.7-i2v-2026-04-25", "1080P", 5)

    def test_legacy_flat_request_body_shape(self):
        body = wan_provider._build_request_body(
            model="wan2.2-i2v-flash", image_url="https://x/ref.jpg", prompt="p",
            resolution="720P", duration_seconds=5)
        self.assertEqual(body["input"]["img_url"], "https://x/ref.jpg")
        self.assertNotIn("media", body["input"])

    def test_media_array_request_body_shape(self):
        body = wan_provider._build_request_body(
            model="wan2.7-i2v-2026-04-25", image_url="https://x/ref.jpg", prompt="p",
            resolution="1080P", duration_seconds=5)
        self.assertNotIn("img_url", body["input"])
        self.assertEqual(body["input"]["media"], [{"type": "first_frame", "url": "https://x/ref.jpg"}])
        self.assertEqual(body["parameters"]["watermark"], False)

    def test_generate_video_refuses_without_stored_credentials(self):
        with self.assertRaises(wan_provider.WanNotConfigured):
            wan_provider.generate_video(
                model="wan2.2-i2v-flash", image_url="https://example.com/ref.jpg",
                prompt="x", resolution="720P", duration_seconds=5,
                budget_usd_remaining=10.0)

    def test_generate_video_refuses_over_budget_before_credentials_even_matter(self):
        # No credentials stored in this isolated tmp dir either -- whichever
        # guard fires first (budget or credentials), no network call ever
        # happens for a request this clearly invalid.
        with self.assertRaises((wan_provider.WanBudgetExceeded, wan_provider.WanNotConfigured)):
            wan_provider.generate_video(
                model="wan2.2-i2v-flash", image_url="https://example.com/ref.jpg",
                prompt="x", resolution="720P", duration_seconds=5,
                budget_usd_remaining=0.01)

    def test_free_quota_only_true_rejected_for_pay_per_second_model(self):
        wan_runtime.store_api_key("dummy-test-key")
        with self.assertRaises(ValueError):
            wan_provider.generate_video(
                model="wan2.2-i2v-flash", image_url="https://x/ref.jpg", prompt="p",
                resolution="720P", duration_seconds=5, free_quota_only=True)

    def test_free_quota_only_false_rejected_for_free_quota_protected_model(self):
        wan_runtime.store_api_key("dummy-test-key")
        with self.assertRaises(ValueError):
            wan_provider.generate_video(
                model="wan2.7-i2v-2026-04-25", image_url="https://x/ref.jpg", prompt="p",
                resolution="1080P", duration_seconds=5, free_quota_only=False,
                budget_usd_remaining=0)


class WanBudgetLedger(_TmpState):
    """Kept deliberately separate from credit_budget -- see state.py's
    comment on why Higgsfield credits and Wan USD are not the same money."""

    def test_defaults_to_zero_authorized_spend(self):
        v = st.load()
        self.assertEqual(st.wan_usd_remaining(v), 0)

    def test_set_and_track_wan_budget(self):
        v = st.load()
        st.set_wan_budget(v, session_limit_usd=5.0)
        st.record_wan_spend(v, usd=0.5, what="test clip 1")
        st.record_wan_spend(v, usd=0.5, what="test clip 2")
        self.assertEqual(st.wan_usd_remaining(v), 4.0)
        self.assertEqual(v["wan_budget"]["spent_total_usd"], 1.0)

    def test_failed_generation_tracked_separately_from_spend(self):
        v = st.load()
        st.set_wan_budget(v, session_limit_usd=5.0)
        st.record_wan_generation_failure(v, what="task timed out")
        self.assertEqual(st.wan_usd_remaining(v), 5.0)  # a failure costs nothing
        self.assertEqual(v["wan_budget"]["failed_generation_count"], 1)

    def test_works_on_a_venture_predating_the_wan_budget_field(self):
        v = st.load()
        del v["wan_budget"]  # simulate an old venture.json seeded before this field existed
        self.assertEqual(st.wan_usd_remaining(v), 0)
        st.record_wan_spend(v, usd=0.25, what="clip")
        self.assertEqual(v["wan_budget"]["spent_total_usd"], 0.25)


class StorefrontReadiness(_TmpState):
    """The gate against AGGRESSIVELY SCALING acquisition, not a freeze --
    verified never to block ordinary posting itself (that's enforced simply
    by no dispatch action ever consulting it)."""

    def test_unchecked_storefront_is_not_ready(self):
        v = st.load()
        r = dashboard_status.storefront_readiness(v)
        self.assertFalse(r["ready"])
        self.assertFalse(r["checked"])

    def test_checked_but_incomplete_is_not_ready_and_lists_whats_missing(self):
        v = st.load()
        st.set_storefront_state(v, checked_via="live_api", bio_set=False, banner_distinct=False)
        r = dashboard_status.storefront_readiness(v)
        self.assertFalse(r["ready"])
        self.assertTrue(r["checked"])
        self.assertTrue(any("bio" in m for m in r["missing"]))
        self.assertTrue(any("banner" in m for m in r["missing"]))

    def test_fully_ready_when_checked_complete_and_enough_live_content(self):
        v = st.load()
        st.set_storefront_state(v, checked_via="live_api", bio_set=True, banner_distinct=True)
        v["content_plan"] = {"content_items": [
            {"public_status": "PUBLICLY_LIVE"}, {"public_status": "PUBLICLY_LIVE"},
            {"public_status": "PUBLICLY_LIVE"},
        ]}
        r = dashboard_status.storefront_readiness(v)
        self.assertTrue(r["ready"])
        self.assertEqual(r["missing"], [])

    def test_unknown_field_rejected(self):
        v = st.load()
        with self.assertRaises(ValueError):
            st.set_storefront_state(v, checked_via="live_api", not_a_real_field=True)


class MonetizationClock(_TmpState):
    """Idempotent start, first-occurrence-wins milestones -- never recount
    or overwrite a real event."""

    def test_starts_once_and_is_idempotent(self):
        v = st.load()
        self.assertTrue(st.start_monetization_clock(v))
        self.assertIsNotNone(v["monetization_clock"]["started_at"])
        self.assertFalse(st.start_monetization_clock(v))  # already running

    def test_milestone_recorded_with_evidence(self):
        v = st.load()
        st.start_monetization_clock(v)
        st.record_monetization_milestone(v, "first_paid_conversion",
                                         evidence="Fanvue earnings summary showed a new gross total")
        m = v["monetization_clock"]["milestones"]["first_paid_conversion"]
        self.assertIsNotNone(m)
        self.assertIn("evidence", m)

    def test_first_occurrence_wins_not_overwritten(self):
        v = st.load()
        st.record_monetization_milestone(v, "revenue_10", evidence="first observation")
        first = v["monetization_clock"]["milestones"]["revenue_10"]
        st.record_monetization_milestone(v, "revenue_10", evidence="second observation -- should be ignored")
        self.assertEqual(v["monetization_clock"]["milestones"]["revenue_10"], first)

    def test_unknown_milestone_rejected(self):
        v = st.load()
        with self.assertRaises(ValueError):
            st.record_monetization_milestone(v, "not_a_real_milestone", evidence="x")

    def test_tick_starts_clock_automatically_once_ready(self):
        v = st.load()
        st.set_storefront_state(v, checked_via="live_api", bio_set=True, banner_distinct=True)
        v["content_plan"] = {"content_items": [
            {"public_status": "PUBLICLY_LIVE"}, {"public_status": "PUBLICLY_LIVE"},
            {"public_status": "PUBLICLY_LIVE"},
        ]}
        st.save(v)
        orig_decide = stages.manager_decide
        # The clock check happens before the LLM loop even starts -- mock
        # manager_decide anyway so this test never makes a real API call
        # regardless of that ordering.
        stages.manager_decide = lambda state_json, recent, phases: {
            "action": "no_action_needed", "params": {}, "reasoning": "x", "bottleneck": "x",
            "next_evaluation_at": "2026-09-14T00:00:00+00:00", "awaiting": "x",
        }
        try:
            manager.tick()
        finally:
            stages.manager_decide = orig_decide
        self.assertIsNotNone(st.load()["monetization_clock"]["started_at"])


class ContentBriefContentObjectiveValidation(unittest.TestCase):
    def setUp(self):
        self._orig_call = stages.claude_call

    def tearDown(self):
        stages.claude_call = self._orig_call

    def test_missing_content_objective_rejected(self):
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "content_items": [{"type": "video", "generation_prompt": "p", "caption": "c",
                              "target_platform": "tiktok", "aspect_ratio": "9:16"}],
            "posting_cadence": "daily", "notes": "",
        }), "cost_usd": 0.05}
        with self.assertRaises(stages.StageValidationError):
            stages.content_brief_generator({})

    def test_valid_content_objective_accepted(self):
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({
            "content_items": [{"type": "video", "generation_prompt": "p", "caption": "c",
                              "target_platform": "tiktok", "aspect_ratio": "9:16",
                              "content_objective": "WORLD_BUILDING"}],
            "posting_cadence": "daily", "notes": "",
        }), "cost_usd": 0.05}
        d = stages.content_brief_generator({})
        self.assertEqual(d["content_items"][0]["content_objective"], "WORLD_BUILDING")


class CommercialScoreboard(_TmpState):
    def test_uses_not_available_not_invented_zeros(self):
        v = st.load()
        board = dashboard_status.commercial_scoreboard(
            v, {"subscribers": 1, "gross_revenue_usd": 0.0}, {},
            dashboard_status.content_pipeline_health(v.get("content_plan", {})))
        self.assertEqual(board["qualified_reach"], dashboard_status.NOT_AVAILABLE)
        self.assertEqual(board["current_subscribers"], 1)
        self.assertEqual(board["publish_ready_target"], 5)

    def test_below_inventory_target_flagged(self):
        v = st.load()
        board = dashboard_status.commercial_scoreboard(
            v, {}, {}, dashboard_status.content_pipeline_health(v.get("content_plan", {})))
        self.assertTrue(board["below_inventory_target"])


class ContentDirectorChooseProviderValidation(unittest.TestCase):
    """The provider-routing decision never spends anything itself, but a bad
    recommendation here (an unwhitelisted model, a missing reference image)
    must never reach wan_provider.generate_video -- catch it here instead."""

    def setUp(self):
        self._orig_call = stages.claude_call

    def tearDown(self):
        stages.claude_call = self._orig_call

    def _call(self, response: dict):
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps(response), "cost_usd": 0.05}
        return stages.content_director_choose_provider(
            {}, {}, business_context="x", higgsfield_credits_remaining=10,
            wan_usd_remaining=5.0)

    def test_rejects_unknown_provider(self):
        with self.assertRaises(stages.StageValidationError):
            self._call({"provider": "pixverse", "reasoning": "x"})

    def test_skip_needs_no_business_purpose(self):
        d = self._call({"provider": "skip", "reasoning": "nothing adequate in budget"})
        self.assertEqual(d["provider"], "skip")

    def test_reuse_requires_asset_ref(self):
        with self.assertRaises(stages.StageValidationError):
            self._call({"provider": "reuse", "reasoning": "x", "business_purpose": "acquisition",
                       "asset_ref": ""})

    def test_wan_requires_whitelisted_model(self):
        with self.assertRaises(stages.StageValidationError):
            self._call({
                "provider": "wan", "reasoning": "x", "business_purpose": "acquisition",
                "model_used": "some-model-the-llm-made-up", "generation_prompt": "p",
                "reference_image_url": "https://x/ref.jpg", "resolution": "720P",
                "duration_seconds": 5,
            })

    def test_wan_requires_reference_image_url(self):
        with self.assertRaises(stages.StageValidationError):
            self._call({
                "provider": "wan", "reasoning": "x", "business_purpose": "acquisition",
                "content_complexity": "simple_routine",
                "model_used": "wan2.7-i2v-2026-04-25", "generation_prompt": "p",
                "reference_image_url": "", "resolution": "720P", "duration_seconds": 5,
            })

    def test_wan_rejects_resolution_unsupported_by_chosen_model(self):
        with self.assertRaises(stages.StageValidationError):
            self._call({
                "provider": "wan", "reasoning": "x", "business_purpose": "acquisition",
                "content_complexity": "simple_routine",
                "model_used": "wan2.7-i2v-2026-04-25", "generation_prompt": "p",
                "reference_image_url": "https://x/ref.jpg", "resolution": "480P",  # wan2.7-i2v has no 480P
                "duration_seconds": 5,
            })

    def test_valid_wan_recommendation_is_accepted(self):
        d = self._call({
            "provider": "wan", "reasoning": "simple idle gesture, benchmark-passed model",
            "business_purpose": "acquisition", "content_complexity": "simple_routine",
            "model_used": "wan2.7-i2v-2026-04-25",
            "generation_prompt": "Nyx gives a small warm wave, stable background",
            "reference_image_url": "https://x/ref.jpg", "resolution": "720P",
            "duration_seconds": 5,
        })
        self.assertEqual(d["provider"], "wan")
        self.assertEqual(d["model_used"], "wan2.7-i2v-2026-04-25")

    def test_wan_requires_content_complexity_field(self):
        with self.assertRaises(stages.StageValidationError):
            self._call({
                "provider": "wan", "reasoning": "x", "business_purpose": "acquisition",
                "model_used": "wan2.7-i2v-2026-04-25", "generation_prompt": "p",
                "reference_image_url": "https://x/ref.jpg", "resolution": "720P",
                "duration_seconds": 5,
            })

    def test_complex_content_complexity_rejected_for_wan(self):
        # This is the structural guarantee behind "if ambiguous/complex,
        # default to Higgsfield" -- Wan can never be chosen for anything
        # the router itself labels complex, no matter how it's framed.
        with self.assertRaises(stages.StageValidationError):
            self._call({
                "provider": "wan", "reasoning": "x", "business_purpose": "acquisition",
                "content_complexity": "complex",
                "model_used": "wan2.7-i2v-2026-04-25", "generation_prompt": "p",
                "reference_image_url": "https://x/ref.jpg", "resolution": "720P",
                "duration_seconds": 5,
            })

    def test_unauthorized_wan_model_rejected_even_with_simple_complexity(self):
        # wan2.2-i2v-flash is a real, whitelisted model (MODEL_SPECS
        # contains it) but is NOT yet authorized for autonomous selection
        # ("no paid Wan usage yet") -- must be rejected regardless of how
        # the complexity is classified.
        with self.assertRaises(stages.StageValidationError):
            self._call({
                "provider": "wan", "reasoning": "x", "business_purpose": "acquisition",
                "content_complexity": "simple_routine",
                "model_used": "wan2.2-i2v-flash", "generation_prompt": "p",
                "reference_image_url": "https://x/ref.jpg", "resolution": "720P",
                "duration_seconds": 5,
            })


class RunContentGenerationDispatch(_TmpState):
    """End-to-end (mocked at the provider boundary) coverage of the
    reuse/higgsfield/wan branch that manager._dispatch now routes through --
    this is the routing policy the founder approved: Wan preferred over
    Higgsfield when adequate, Higgsfield kept as fallback."""

    def setUp(self):
        super().setUp()
        self.v = st.load()
        self.v["content_plan"] = {"content_items": [{"concept": "test clip"}]}
        st.save(self.v)
        self._orig_choose = stages.content_director_choose_provider
        self._orig_generate = stages.content_director_generate
        self._orig_wan_generate = wan_provider.generate_video

    def tearDown(self):
        stages.content_director_choose_provider = self._orig_choose
        stages.content_director_generate = self._orig_generate
        wan_provider.generate_video = self._orig_wan_generate
        super().tearDown()

    def test_reuse_decision_updates_lifecycle_with_zero_cost(self):
        stages.content_director_choose_provider = lambda *a, **kw: {
            "provider": "reuse", "reasoning": "matches existing Nyx asset",
            "business_purpose": "acquisition", "asset_ref": "https://cdn/existing.mp4",
        }
        v = st.load()
        made_progress, detail = manager._dispatch(v, "run_content_generation",
                                                   {"content_item_index": 0})
        self.assertTrue(made_progress)
        item = v["content_plan"]["content_items"][0]
        self.assertEqual(item["lifecycle_status"], "QA_PASSED")
        self.assertEqual(item["asset_ref"], "https://cdn/existing.mp4")

    def test_wan_decision_generates_and_records_usd_spend_not_credits(self):
        stages.content_director_choose_provider = lambda *a, **kw: {
            "provider": "wan", "reasoning": "cheapest adequate, preserves Higgsfield credits",
            "business_purpose": "acquisition", "model_used": "wan2.2-i2v-flash",
            "generation_prompt": "p", "reference_image_url": "https://x/ref.jpg",
            "resolution": "720P", "duration_seconds": 5,
        }
        wan_provider.generate_video = lambda **kw: {
            "asset_ref": "/tmp/fake_wan_clip.mp4", "source_url": "https://oss/clip.mp4",
            "model_used": "wan2.2-i2v-flash", "cost_usd": 0.5, "duration_seconds": 5,
            "resolution": "720P", "generation_seconds": 42.0, "watermark": "unconfirmed",
        }
        v = st.load()
        st.set_wan_budget(v, session_limit_usd=5.0)
        made_progress, detail = manager._dispatch(v, "run_content_generation",
                                                   {"content_item_index": 0})
        self.assertTrue(made_progress)
        item = v["content_plan"]["content_items"][0]
        self.assertEqual(item["lifecycle_status"], "ASSET_GENERATED")
        self.assertEqual(item["generation_cost_usd"], 0.5)
        self.assertEqual(item.get("generation_cost_credits"), 0)
        self.assertEqual(v["wan_budget"]["spent_total_usd"], 0.5)
        self.assertEqual(v["credit_budget"]["spent_total"], 0)  # zero Higgsfield credits touched
        # 2026-09-13 fix: the ACTUAL prompt used for this attempt must be
        # persisted, not just the item's original brief prompt.
        self.assertEqual(item["generation_prompt"], "p")
        self.assertEqual(item["regeneration_attempts"], 1)

    def test_free_quota_protected_wan_always_called_with_free_quota_only_true(self):
        # This is the structural guarantee that Wan cannot generate paid
        # content: free_quota_only is decided by manager.py from the
        # model's OWN billing_mode, never from anything the routing
        # decision (an LLM call) said.
        stages.content_director_choose_provider = lambda *a, **kw: {
            "provider": "wan", "reasoning": "simple idle gesture", "business_purpose": "engagement",
            "content_complexity": "simple_routine", "model_used": "wan2.7-i2v-2026-04-25",
            "generation_prompt": "p", "reference_image_url": "https://x/ref.jpg",
            "resolution": "720P", "duration_seconds": 5,
        }
        captured_kwargs = {}
        def _fake_generate(**kw):
            captured_kwargs.update(kw)
            return {"asset_ref": "/tmp/fake.mp4", "source_url": "https://oss/clip.mp4",
                    "model_used": "wan2.7-i2v-2026-04-25", "cost_usd": 0.0,
                    "duration_seconds": 5, "resolution": "720P",
                    "generation_seconds": 40.0, "watermark": False}
        wan_provider.generate_video = _fake_generate
        v = st.load()
        made_progress, detail = manager._dispatch(v, "run_content_generation",
                                                   {"content_item_index": 0})
        self.assertTrue(made_progress)
        self.assertTrue(captured_kwargs.get("free_quota_only"))
        self.assertNotIn("budget_usd_remaining", captured_kwargs)
        item = v["content_plan"]["content_items"][0]
        self.assertEqual(item["generation_cost_usd"], 0.0)
        self.assertEqual(v["wan_budget"]["spent_total_usd"], 0)  # $0 -- never touches the USD ledger
        self.assertEqual(v["credit_budget"]["spent_total"], 0)  # zero Higgsfield credits touched

    def test_free_quota_exhausted_fails_safely_with_no_higgsfield_fallback(self):
        stages.content_director_choose_provider = lambda *a, **kw: {
            "provider": "wan", "reasoning": "simple idle gesture", "business_purpose": "engagement",
            "content_complexity": "simple_routine", "model_used": "wan2.7-i2v-2026-04-25",
            "generation_prompt": "p", "reference_image_url": "https://x/ref.jpg",
            "resolution": "720P", "duration_seconds": 5,
        }
        def _raise(**kw):
            raise wan_provider.WanFreeQuotaExhausted("simulated: free quota exhausted")
        wan_provider.generate_video = _raise
        # If this ever got called, that would BE the forbidden automatic
        # Higgsfield fallback -- fail the test loudly if it's reached.
        def _forbidden_fallback(*a, **kw):
            self.fail("Higgsfield must never be called automatically when Wan's free quota is exhausted")
        stages.content_director_generate = _forbidden_fallback
        v = st.load()
        made_progress, detail = manager._dispatch(v, "run_content_generation",
                                                   {"content_item_index": 0})
        self.assertFalse(made_progress)
        self.assertIn("free quota exhausted", detail.lower())
        self.assertEqual(v["wan_budget"]["failed_generation_count"], 1)
        self.assertEqual(v["wan_budget"]["free_quota_exhausted_count"], 1)
        self.assertEqual(v["wan_budget"]["spent_total_usd"], 0)
        self.assertEqual(v["credit_budget"]["spent_total"], 0)

    def test_wan_decision_skipped_when_budget_exhausted(self):
        stages.content_director_choose_provider = lambda *a, **kw: {
            "provider": "wan", "reasoning": "x", "business_purpose": "acquisition",
            "model_used": "wan2.2-i2v-flash", "generation_prompt": "p",
            "reference_image_url": "https://x/ref.jpg", "resolution": "720P",
            "duration_seconds": 5,
        }
        v = st.load()  # wan_budget defaults to session_limit_usd=0 -- nothing authorized
        made_progress, detail = manager._dispatch(v, "run_content_generation",
                                                   {"content_item_index": 0})
        self.assertFalse(made_progress)
        self.assertIn("Wan", detail)

    def test_wan_generation_failure_recorded_and_costs_nothing(self):
        stages.content_director_choose_provider = lambda *a, **kw: {
            "provider": "wan", "reasoning": "x", "business_purpose": "acquisition",
            "model_used": "wan2.2-i2v-flash", "generation_prompt": "p",
            "reference_image_url": "https://x/ref.jpg", "resolution": "720P",
            "duration_seconds": 5,
        }
        def _raise(**kw):
            raise wan_provider.WanGenerationError("task FAILED: simulated provider error")
        wan_provider.generate_video = _raise
        v = st.load()
        st.set_wan_budget(v, session_limit_usd=5.0)
        made_progress, detail = manager._dispatch(v, "run_content_generation",
                                                   {"content_item_index": 0})
        self.assertFalse(made_progress)
        self.assertEqual(v["wan_budget"]["failed_generation_count"], 1)
        self.assertEqual(v["wan_budget"]["spent_total_usd"], 0)

    def test_higgsfield_fallback_path_still_works_unchanged(self):
        stages.content_director_choose_provider = lambda *a, **kw: {
            "provider": "higgsfield", "reasoning": "Wan inadequate for this asset",
            "business_purpose": "acquisition",
        }
        stages.content_director_generate = lambda *a, **kw: {
            "decision": "generated", "reasoning": "x", "asset_ref": "job-1",
            "model_used": "wan3_0", "cost_credits": 3, "business_purpose": "acquisition",
            "_cost_usd": 0.1,
        }
        v = st.load()
        st.set_credit_budget(v, session_limit=40)
        made_progress, detail = manager._dispatch(v, "run_content_generation",
                                                   {"content_item_index": 0})
        self.assertTrue(made_progress)
        self.assertEqual(v["credit_budget"]["spent_total"], 3)
        self.assertIn("higgsfield", detail)

    def test_higgsfield_generated_decision_also_increments_regeneration_attempts(self):
        stages.content_director_choose_provider = lambda *a, **kw: {
            "provider": "higgsfield", "reasoning": "x", "business_purpose": "acquisition"}
        stages.content_director_generate = lambda *a, **kw: {
            "decision": "generated", "reasoning": "x", "asset_ref": "job-1",
            "model_used": "wan3_0", "cost_credits": 3, "business_purpose": "acquisition",
            "_cost_usd": 0.1}
        v = st.load()
        st.set_credit_budget(v, session_limit=40)
        manager._dispatch(v, "run_content_generation", {"content_item_index": 0})
        self.assertEqual(v["content_plan"]["content_items"][0]["regeneration_attempts"], 1)

    def test_regeneration_bound_blocks_further_generation_without_calling_the_llm(self):
        """2026-09-13 fix: a real item hit 5 generations + 5 QA failures on
        the same repeated gesture -- nothing in code stopped it. After
        MAX_REGENERATION_ATTEMPTS, run_content_generation must refuse
        without even calling content_director_choose_provider (an LLM call
        we'd otherwise keep paying for on a concept that keeps failing)."""
        v = st.load()
        v["content_plan"]["content_items"][0]["regeneration_attempts"] = manager.MAX_REGENERATION_ATTEMPTS
        st.save(v)
        called = {"n": 0}
        stages.content_director_choose_provider = lambda *a, **kw: called.__setitem__(
            "n", called["n"] + 1) or self.fail("must not call the LLM past the bound")
        made_progress, detail = manager._dispatch(v, "run_content_generation",
                                                   {"content_item_index": 0})
        self.assertFalse(made_progress)
        self.assertEqual(called["n"], 0)
        self.assertIn("regeneration bound reached", detail)

    def test_below_bound_generation_still_proceeds_normally(self):
        v = st.load()
        v["content_plan"]["content_items"][0]["regeneration_attempts"] = manager.MAX_REGENERATION_ATTEMPTS - 1
        st.save(v)
        stages.content_director_choose_provider = lambda *a, **kw: {
            "provider": "reuse", "reasoning": "x", "business_purpose": "acquisition",
            "asset_ref": "https://cdn/existing.mp4"}
        made_progress, detail = manager._dispatch(v, "run_content_generation",
                                                   {"content_item_index": 0})
        self.assertTrue(made_progress)


class RunContentBriefRevisionDispatch(_TmpState):
    """Coverage for the 2026-09-15 standing founder authorization: once a
    content plan already exists, run_content_brief must safely APPEND a
    small bounded batch instead of replacing anything, and must only
    actually fire when both deterministic gates (inventory low, minimum
    interval elapsed) are met -- never on the model's judgment alone."""

    def setUp(self):
        super().setUp()
        self.v = st.load()
        self.v["persona"] = {"name": "Test Persona", "niche": "test"}
        self.v["completed_milestones"] = ["content_brief_generated"]
        self.v["content_plan"] = {
            "content_items": [
                {"target_platform": "tiktok", "lifecycle_status": "PUBLISHED",
                 "public_status": "PUBLICLY_LIVE", "published": True},
                {"target_platform": "instagram", "lifecycle_status": "QA_PASSED",
                 "public_status": "INTERNAL_ONLY"},
                {"target_platform": "benchmark_internal", "lifecycle_status": "ASSET_GENERATED",
                 "public_status": "INTERNAL_ONLY"},
            ],
            "posting_cadence": "2x/week -- do not touch",
            "active_story_threads": [{"name": "Star-Map", "status": "active"}],
            "notes": "original brief notes -- do not touch",
        }
        st.save(self.v)
        self._orig_revision = stages.content_brief_revision
        self._orig_generator = stages.content_brief_generator

    def tearDown(self):
        stages.content_brief_revision = self._orig_revision
        stages.content_brief_generator = self._orig_generator
        super().tearDown()

    def _fake_revision(self, n=2):
        calls = {"n": 0}
        def fn(persona, active_story_threads, performance_notes, existing_item_count):
            calls["n"] += 1
            return {"content_items": [
                {"type": "video", "target_platform": "tiktok", "content_objective": "REACH",
                 "generation_prompt": f"new item {i}", "caption": "c", "aspect_ratio": "9:16",
                 "story_thread": "", "open_loop": "", "format_variant": "visual_only"}
                for i in range(n)
            ], "posting_cadence": "new cadence note", "notes": "revision notes", "_cost_usd": 0.01}
        stages.content_brief_revision = fn
        return calls

    def test_skipped_when_inventory_not_low(self):
        # Base fixture has exactly 1 usable item (item1; item0 is published,
        # item2 is benchmark-only). Add 2 more real usable items so the
        # count (3) is genuinely ABOVE the threshold (2), not merely at it.
        for _ in range(2):
            self.v["content_plan"]["content_items"].append(
                {"target_platform": "instagram", "lifecycle_status": "ASSET_GENERATED",
                 "public_status": "INTERNAL_ONLY"})
        st.save(self.v)
        calls = self._fake_revision()
        v = st.load()
        self.assertEqual(len(manager._usable_content_inventory(v)), 3)
        made_progress, detail = manager._dispatch(v, "run_content_brief", {})
        self.assertFalse(made_progress)
        self.assertIn("not low enough yet", detail)
        self.assertEqual(calls["n"], 0)  # never even called the LLM stage
        self.assertEqual(len(v["content_plan"]["content_items"]), 5)  # unchanged

    def test_fires_when_inventory_low_and_appends_without_touching_history(self):
        calls = self._fake_revision(n=2)
        v = st.load()
        before_items = [dict(it) for it in v["content_plan"]["content_items"]]
        made_progress, detail = manager._dispatch(v, "run_content_brief", {})
        self.assertTrue(made_progress)
        self.assertEqual(calls["n"], 1)
        items = v["content_plan"]["content_items"]
        self.assertEqual(len(items), 5)  # 3 original + 2 new
        # Every original item is byte-for-byte untouched, at its original index.
        for i, orig in enumerate(before_items):
            self.assertEqual(items[i], orig)
        self.assertEqual(items[3]["generation_prompt"], "new item 0")
        self.assertEqual(items[4]["generation_prompt"], "new item 1")
        # Nothing else in content_plan was overwritten.
        self.assertEqual(v["content_plan"]["posting_cadence"], "2x/week -- do not touch")
        self.assertEqual(v["content_plan"]["notes"], "original brief notes -- do not touch")
        self.assertEqual(v["content_plan"]["active_story_threads"],
                         [{"name": "Star-Map", "status": "active"}])
        # The revision itself is recorded, not silently absorbed.
        rev = v["content_plan"]["brief_revisions"][0]
        self.assertEqual(rev["added_count"], 2)
        self.assertEqual(rev["start_index"], 3)
        self.assertIn("last_brief_revision_at", v["content_plan"])

    def test_second_revision_within_min_interval_is_skipped(self):
        # Isolate the interval gate specifically: inventory stays genuinely
        # low (base fixture's usable count of 1), but a revision was
        # recorded moments ago -- must be blocked by the interval alone,
        # not by inventory (which a real just-fired revision would also
        # have changed, conflating the two gates).
        calls = self._fake_revision(n=2)
        v = st.load()
        v["content_plan"]["last_brief_revision_at"] = manager._now_iso()
        st.save(v)
        self.assertEqual(len(manager._usable_content_inventory(v)), 1)  # still low

        made_progress, detail = manager._dispatch(v, "run_content_brief", {})
        self.assertFalse(made_progress)
        self.assertIn("minimum interval", detail)
        self.assertEqual(calls["n"], 0)  # never called
        self.assertEqual(len(v["content_plan"]["content_items"]), 3)  # unchanged

    def test_deferred_channel_inventory_never_counts_as_usable(self):
        # A real production edge case: items targeting a founder-deferred
        # channel (e.g. reddit) sitting unpublished are NOT real slack --
        # nothing is allowed to act on them, so they must never mask a
        # genuinely low count on the actually-active channels.
        v = st.load()
        v["distribution_channels"] = {"v1_required": ["tiktok", "instagram", "fanvue"],
                                      "deferred": [{"channel": "reddit", "reason": "test"}]}
        v["content_plan"]["content_items"].extend([
            {"target_platform": "reddit", "public_status": "INTERNAL_ONLY"},
            {"target_platform": "reddit", "public_status": "INTERNAL_ONLY"},
        ])
        st.save(v)
        usable = manager._usable_content_inventory(v)
        self.assertEqual(len(usable), 1)  # only item1 (instagram) -- the 2 reddit items excluded

    def test_first_ever_brief_unaffected_still_replaces(self):
        v = st.load()
        v["completed_milestones"] = []  # no brief exists yet
        v["content_plan"] = {}
        called = {"n": 0}
        def fake_generator(persona):
            called["n"] += 1
            return {"content_items": [{"target_platform": "tiktok",
                                       "content_objective": "REACH"}],
                   "posting_cadence": "first cadence", "notes": "first notes", "_cost_usd": 0.01}
        stages.content_brief_generator = fake_generator
        made_progress, detail = manager._dispatch(v, "run_content_brief", {})
        self.assertTrue(made_progress)
        self.assertEqual(called["n"], 1)
        self.assertEqual(v["content_plan"]["posting_cadence"], "first cadence")
        self.assertIn("content_brief_generated", v["completed_milestones"])

    def test_milestone_gate_no_longer_blocks_run_content_brief(self):
        self.assertNotIn("run_content_brief", manager._ONCE_PER_ROUND_MILESTONES)


class ContentBriefRevisionValidation(_TmpState):
    """stages.content_brief_revision's own deterministic ceiling -- never
    trusts the model's own restraint on batch size."""

    def setUp(self):
        super().setUp()
        self._orig_call = claude_client.claude_call

    def tearDown(self):
        claude_client.claude_call = self._orig_call
        super().tearDown()

    def test_rejects_a_batch_over_the_ceiling(self):
        too_many = [{"type": "video", "target_platform": "tiktok",
                    "content_objective": "REACH"} for _ in range(9)]
        claude_client.claude_call = lambda *a, **kw: {
            "text": json.dumps({"content_items": too_many, "posting_cadence": "x", "notes": "x"}),
            "cost_usd": 0.01}
        stages.claude_call = claude_client.claude_call
        with self.assertRaises(stages.StageValidationError):
            stages.content_brief_revision({"name": "P"}, [], "", 3)

    def test_accepts_a_batch_within_the_ceiling(self):
        ok_batch = [{"type": "video", "target_platform": "tiktok",
                    "content_objective": "REACH"} for _ in range(3)]
        claude_client.claude_call = lambda *a, **kw: {
            "text": json.dumps({"content_items": ok_batch, "posting_cadence": "x", "notes": "x"}),
            "cost_usd": 0.01}
        stages.claude_call = claude_client.claude_call
        d = stages.content_brief_revision({"name": "P"}, [], "", 3)
        self.assertEqual(len(d["content_items"]), 3)


class OperationsConsoleStatusPayload(_TmpState):
    """Regression coverage for the 2026-09-16 dashboard-crash incident: the
    live Operations Console (server.build_status(), consumed by
    static/app.js) went completely blank right after the pending-approval
    queue repair, because dashboard_status.system_status() did bracket
    access (pending_approval["what"]) on whatever load_pending_approval()
    returns -- fine for the old single-object file shape, but an unhandled
    KeyError against the new {"items": [...]} shape (hit in production by a
    long-running server process that had the pre-fix state.py loaded in
    memory). The whole bare BaseHTTPRequestHandler read path has no
    exception handling, so one KeyError drops the connection with zero
    bytes sent -- exactly the "everything is blank" symptom. This proves
    the full status payload builds successfully, with valid scheduler/CEO/
    business/content/founder-action data, when MULTIPLE independent
    pending approvals exist -- the exact real-world shape that triggered
    the incident (Instagram item10 + TikTok item16)."""

    def setUp(self):
        super().setUp()
        st.write_pending_approval(reason="CANNOT_SAFELY_DELEGATE",
                                  what="content_items[10] Instagram upload is stuck",
                                  why="upload tooling limitation",
                                  founder_action="publish manually", estimated_minutes=3)
        st.write_pending_approval(reason="AUTHORIZATION_REQUIRED",
                                  what="TikTok publish consent for content_items[16]",
                                  why="QA_PASSED and ready", founder_action="give consent",
                                  estimated_minutes=3)

    def test_status_payload_builds_without_crashing_with_multiple_approvals(self):
        status = server.build_status()  # must not raise
        self.assertTrue(status["seeded"])

    def test_scheduler_fields_present(self):
        # Isolated heartbeat: never read the live state/scheduler_heartbeat.json.
        orig = scheduler_heartbeat.PATH
        scheduler_heartbeat.PATH = Path(self._tmp.name) / "state" / "scheduler_heartbeat.json"
        self.addCleanup(setattr, scheduler_heartbeat, "PATH", orig)
        scheduler_heartbeat.record_tick_start()
        scheduler_heartbeat.record_tick_end(outcome="checked", summary="unit-test tick",
                                            next_run_at="2026-01-01T01:00:00Z")
        status = server.build_status()
        hb = status["scheduler_heartbeat"]
        for key in ("ever_run", "last_attempted_tick", "next_run_at_self_reported",
                   "consecutive_failures"):
            self.assertIn(key, hb)

    def test_ceo_bottleneck_populated_as_founder_blocked(self):
        status = server.build_status()
        self.assertEqual(status["system_status"]["state"], "FOUNDER_BLOCKED")
        # Both approvals' text are real business-blocking reasons, not blank.
        self.assertTrue(status["system_status"]["reason"])

    def test_business_scoreboard_populated(self):
        status = server.build_status()
        scoreboard = status["business_scoreboard"]
        self.assertIsInstance(scoreboard, dict)
        self.assertIn("gross_revenue_usd", scoreboard)

    def test_content_pipeline_health_populated(self):
        status = server.build_status()
        self.assertIsInstance(status["content_pipeline_health"], dict)

    def test_founder_action_center_shows_both_independent_approvals(self):
        status = server.build_status()
        # Backward-compatible single card: the OLDEST approval, unchanged.
        self.assertEqual(status["pending_approval"]["what"],
                         "content_items[10] Instagram upload is stuck")
        # New: the full queue, both entries visible, neither lost/merged.
        whats = {a["what"] for a in status["pending_approvals"]}
        self.assertEqual(whats, {"content_items[10] Instagram upload is stuck",
                                 "TikTok publish consent for content_items[16]"})

    def test_neither_pending_approval_is_resolved_by_reading_status(self):
        """Building the status payload is READ-ONLY -- it must never
        resolve, clear, or mutate a pending approval as a side effect."""
        before = server.build_status()["pending_approvals"]
        server.build_status()
        after = st.load_pending_approvals()
        self.assertEqual(len(after), 2)
        self.assertEqual(before, after)



def _good_carousel(**over):
    item = {
        "type": "image", "target_platform": "instagram", "format_variant": "story_carousel",
        "content_objective": "ENGAGEMENT", "story_thread": "", "caption": "this wasn't here yesterday.",
        "commercial_hypothesis": "a mid-event hook lifts swipe-through and profile visits vs 1.5s Reel watch",
        "final_interaction": "open it or leave it?", "open_loop": "what's inside the box",
        "slides": [
            {"role": "HOOK", "beat": "a box on the pillow", "composition": "close-up, top-down on the box",
             "text_overlay": "this wasn't here yesterday.", "continuity": "den bedroom, night"},
            {"role": "CONTEXT", "beat": "Nyx freezes in the doorway", "composition": "wide, doorway, low angle",
             "text_overlay": "", "continuity": "same hoodie, same lamp"},
            {"role": "ESCALATION", "beat": "the box is addressed to her old name", "composition": "macro on the label",
             "text_overlay": "that's not my name anymore", "continuity": "same box"},
            {"role": "PROGRESSION", "beat": "she lifts it and it hums", "composition": "medium, side angle, hands",
             "text_overlay": "", "continuity": "same box, same hoodie"},
            {"role": "PAYOFF", "beat": "the lid glows along one seam", "composition": "over-shoulder, box in focus",
             "text_overlay": "open it or leave it?", "continuity": "same room, lamp off"}]}
    item.update(over)
    return item


class StoryCarouselFormat(unittest.TestCase):
    """story_carousel is one more format in the existing brief -> generate -> QA
    pipeline; deterministic checks can only fail a carousel, never pass one."""

    def setUp(self):
        self._orig_call = stages.claude_call

    def tearDown(self):
        stages.claude_call = self._orig_call

    def test_good_carousel_brief_validates(self):
        self.assertEqual(stages.story_carousel_problems(_good_carousel()), [])
        stages._validate_content_brief({"content_items": [_good_carousel()], "posting_cadence": "x",
                                        "notes": "x"}, "t")

    def test_slide_count_hook_payoff_and_text_limits(self):
        c = _good_carousel()
        probs = stages.story_carousel_problems({**c, "slides": c["slides"][:3]})
        self.assertTrue(any("need 4-7" in p for p in probs))
        swapped = [c["slides"][1], c["slides"][0]] + c["slides"][2:]
        self.assertIn("slide 1 must be the HOOK", stages.story_carousel_problems({**c, "slides": swapped}))
        long_text = [dict(c["slides"][0], text_overlay="one two three four five six seven eight nine ten eleven twelve thirteen")] + c["slides"][1:]
        self.assertTrue(any("13 words" in p for p in stages.story_carousel_problems({**c, "slides": long_text})))

    def test_repetitive_slides_bait_and_disclaimer_copy_fail(self):
        c = _good_carousel()
        same = [dict(s, composition="close-up, top-down on the box") for s in c["slides"]]
        self.assertTrue(any("repetitive" in p for p in stages.story_carousel_problems({**c, "slides": same})))
        self.assertIn("final interaction is generic engagement bait",
                      stages.story_carousel_problems({**c, "final_interaction": "COMMENT BELOW for part 2!"}))
        disc = [dict(c["slides"][0], text_overlay="100% AI-generated character")] + c["slides"][1:]
        self.assertTrue(any("disclaimer" in p for p in stages.story_carousel_problems({**c, "slides": disc})))

    def test_invalid_carousel_rejects_the_whole_brief(self):
        with self.assertRaises(stages.StageValidationError):
            stages._validate_content_brief({"content_items": [_good_carousel(slides=[])],
                                            "posting_cadence": "x", "notes": "x"}, "t")

    def test_qa_pass_is_overridden_by_structural_failures_or_missing_carousel_qa(self):
        ok = {"qa_verdict": "PASS", "character_qa": "x", "technical_qa": "x", "creative_qa": "x",
              "business_qa": "x", "reasoning": "x"}
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps(ok), "cost_usd": 0}
        self.assertEqual(stages.content_director_creative_qa(_good_carousel(), {}, [])["qa_verdict"], "FAIL")
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps({**ok, "carousel_qa": "every swipe adds"}),
                                                   "cost_usd": 0}
        self.assertEqual(stages.content_director_creative_qa(_good_carousel(), {}, [])["qa_verdict"], "PASS")
        bad = _good_carousel(final_interaction="like and share!")
        self.assertEqual(stages.content_director_creative_qa(bad, {}, [])["qa_verdict"], "FAIL")

    def test_paid_generation_is_refused_before_any_provider_call_without_founder_approval(self):
        called = []
        stages.claude_call = lambda prompt, **kw: called.append(prompt) or {"text": "{}", "cost_usd": 0}
        with self.assertRaises(stages.StageValidationError):
            stages.content_director_generate(_good_carousel(), {}, business_context="x", max_credits=5)
        self.assertEqual(called, [])          # refused BEFORE the paid provider is ever invoked

    def test_generation_never_accepts_a_partial_carousel(self):
        # founder-approved item: the paid path is the only one that can return a
        # PARTIAL carousel, so that is what this exercises -- the approval guard
        # itself is covered above, not weakened here.
        approved = _good_carousel(**{stages.CAROUSEL_PAID_APPROVAL_FIELD: True})
        res = {"decision": "generated", "reasoning": "x", "asset_ref": "https://a/1.png", "model_used": "m",
               "cost_credits": 1, "business_purpose": "engagement", "asset_refs": ["https://a/1.png"] * 4}
        stages.claude_call = lambda prompt, **kw: {"text": json.dumps(res), "cost_usd": 0}
        with self.assertRaises(stages.StageValidationError):
            stages.content_director_generate(approved, {}, business_context="x", max_credits=5)
        res["asset_refs"] = [f"https://a/{i}.png" for i in range(5)]
        self.assertEqual(len(stages.content_director_generate(approved, {}, business_context="x",
                                                              max_credits=5)["asset_refs"]), 5)

    def test_carousel_uses_the_existing_lifecycle_states(self):
        v = {"content_plan": {"content_items": [_good_carousel()]}}
        for s in ("BRIEF_READY", "ASSET_GENERATED", "QA_PASSED"):
            self.assertEqual(st.set_content_item_lifecycle(v, 0, s)["lifecycle_status"], s)


class PublicCarouselCreativeDirection(unittest.TestCase):
    """Founder 2026-09-23: public Instagram/TikTok carousels sell personality,
    mystery and story -- believable social-native photography, not cinematic
    glamour, and not lingerie-adjacent. Fanvue may escalate; public may not."""

    def test_an_all_cinematic_public_carousel_is_refused(self):
        item = _good_carousel()
        for s in item["slides"]:
            s["composition"] = "cinematic key visual, dramatic volumetric god rays"
        probs = stages.story_carousel_problems(item)
        self.assertTrue(any("social-native" in p for p in probs), probs)

    def test_one_cinematic_hero_slide_is_allowed(self):
        item = _good_carousel()
        item["slides"][4]["composition"] = "cinematic wide hero shot, the charm glowing"
        self.assertEqual(stages.public_carousel_problems(item), [])   # ~10% of the mix

    def test_lingerie_adjacent_wardrobe_is_refused_publicly_but_allowed_off_platform(self):
        item = _good_carousel()
        item["slides"][1]["continuity"] = "sheer lingerie, same bedroom"
        self.assertTrue(any("lingerie-adjacent" in p for p in stages.public_carousel_problems(item)))
        self.assertEqual(stages.public_carousel_problems({**item, "target_platform": "fanvue"}), [])

    def test_overlay_lines_must_sound_like_her_not_a_narrator(self):
        item = _good_carousel()
        item["slides"][0]["text_overlay"] = "she slowly reaches for the charm"
        self.assertTrue(any("narration" in p for p in stages.public_carousel_problems(item)))

    def test_a_mostly_wordless_public_carousel_is_refused(self):
        item = _good_carousel()
        for s in item["slides"][1:]:
            s["text_overlay"] = ""
        self.assertTrue(any("one creative unit" in p for p in stages.public_carousel_problems(item)))

    def test_story_progression_and_identity_rules_are_unchanged(self):
        repeated = _good_carousel()
        for s in repeated["slides"]:
            s["beat"], s["composition"] = "a box on the pillow", "close-up, top-down"
        probs = stages.story_carousel_problems(repeated)
        self.assertTrue(any("repeat the same beat" in p for p in probs))
        self.assertTrue(any("same composition" in p for p in probs))
        # identity authority untouched by this creative change
        self.assertEqual(carousel_handoff.MIN_CHARACTER_AGE, 25)
        self.assertTrue(str(carousel_handoff.CANONICAL_PORTRAIT).endswith("reference_primary.webp"))

    def test_the_direction_is_durable_in_the_brief_and_qa_prompts(self):
        for needle in ("social-native", "lingerie-adjacent", "ONE creative unit",
                       "normal to HER"):
            self.assertIn(needle, prompts.STORY_CAROUSEL_SPEC, needle)
        self.assertIn("social-native photography", prompts.CONTENT_DIRECTOR_CREATIVE_QA)


def _persona():
    # Short stand-in for the canonical persona (brand/nyx_identity/identity_spec.json):
    # the superseded anime wording must not survive even as a fixture, or the next
    # test written from it quietly reinstates the old identity.
    return {"name": "Nyx Emberfall",
            "visual_style": "highly realistic / photorealistic, premium visual quality",
            "visual_description": "adult woman, mid-20s, dark brown hair, amber eyes, "
                                  "subtle realistic fox ears, one bushy tail",
            "disclosure_statement_short": "#AIGenerated",
            "reference_image_url": "https://cdn/nyx_reference_primary.webp"}


def _png(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)


def _stub_photo(size):
    """A real, PIL-openable stand-in for a generated slide with actual pixel
    variance -- a single solid fill (any colour, including plain
    Image.new's default black) has zero variance and is correctly rejected
    by local_image_provider.is_degenerate_image (2026-09-27); tests that
    stand in for a real render must not accidentally look like one."""
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(0)
    arr = rng.integers(60, 180, size=(size[1], size[0], 3), dtype=np.uint8)
    return Image.fromarray(arr)


class CarouselHandoffPackage(unittest.TestCase):
    """The provider-independent handoff: the identity/continuity lock must be
    complete before any provider is asked to draw, art stays clean of text, and
    slides are ingested all-or-nothing."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.item = _good_carousel()

    def pkg(self):
        return carousel_handoff.build_package(self.item, _persona(), index=0, root=self.root)

    def test_identity_pack_locks_age_face_body_and_every_drift_prone_anchor(self):
        pack = carousel_handoff.identity_pack(_persona())
        self.assertEqual(carousel_handoff.identity_pack_problems(pack), [])
        self.assertTrue(pack["adult"])
        self.assertGreaterEqual(pack["minimum_age"], 25)
        # palette/outfit are deliberately NOT locked anchors as of identity spec v2.0.0
        # (2026-09-22): wardrobe/environment are the story's own per-scene continuity
        # fields (ALLOWED VARIATION), not part of Nyx's physical identity lock.
        for anchor in ("face", "eyes", "hair", "ears", "tail", "skin_and_marks", "body", "realism"):
            self.assertTrue(str(pack["anchors"][anchor]).strip(), anchor)
        self.assertTrue(pack["canonical_references"])          # a real reference, never a promise
        self.assertTrue(pack["negative_identity"])

    def test_an_incomplete_or_under_age_lock_refuses_the_handoff(self):
        pack = carousel_handoff.identity_pack(_persona())
        self.assertTrue(carousel_handoff.identity_pack_problems({**pack, "minimum_age": 17}))
        self.assertTrue(carousel_handoff.identity_pack_problems({**pack, "adult": False}))
        self.assertTrue(carousel_handoff.identity_pack_problems({**pack, "canonical_references": []}))
        self.assertTrue(carousel_handoff.identity_pack_problems(
            {**pack, "anchors": {**pack["anchors"], "tail": ""}}))
        with self.assertRaises(carousel_handoff.HandoffError):
            carousel_handoff.build_package(self.item, {**_persona(), "visual_style": ""},
                                           index=0, root=self.root)

    def test_continuity_sheet_carries_environment_outfit_and_prop_continuity(self):
        sheet = self.pkg()["continuity_sheet"]
        self.assertEqual(carousel_handoff.continuity_sheet_problems(sheet), [])
        self.assertTrue(sheet["environment"])
        self.assertTrue(sheet["outfit_and_props"])
        self.assertTrue(sheet["aspect_ratio"] and sheet["spatial_rule"] and sheet["text_policy"])
        for missing in ("environment", "aspect_ratio", "spatial_rule", "text_policy"):
            self.assertTrue(carousel_handoff.continuity_sheet_problems({**sheet, missing: ""}), missing)
        self.assertTrue(carousel_handoff.continuity_sheet_problems({**sheet, "outfit_and_props": []}))
        self.assertTrue(carousel_handoff.continuity_sheet_problems({**sheet, "negative_constraints": []}))

    def test_every_slide_carries_the_whole_lock_a_delta_and_clean_art_only(self):
        pkg = self.pkg()
        self.assertEqual(pkg["slide_order"], [1, 2, 3, 4, 5])
        self.assertIn("first slide", pkg["slides"][0]["delta_from_previous"])
        for i, s in enumerate(pkg["slides"]):
            self.assertEqual(s["expected_filename"], f"item0_slide{i + 1}.png")
            self.assertIn("25+", s["image_prompt"])                   # adult lock, every slide
            self.assertIn("tail", s["image_prompt"])                  # identity anchors, every slide
            self.assertTrue(s["negative_constraints"])
            self.assertEqual(s["text_in_image"], "none -- generate clean art, no letters of any kind")
            if s["text_overlay"]:
                self.assertNotIn(s["text_overlay"].lower(), s["image_prompt"].lower())
            if i:
                self.assertIn("camera/composition changes to", s["delta_from_previous"])

    def test_overlay_copy_leaking_into_the_image_prompt_is_refused(self):
        slides = [dict(self.item["slides"][0], beat="a box on the pillow: this wasn't here yesterday.")]
        with self.assertRaises(carousel_handoff.HandoffError):
            carousel_handoff.build_package({**self.item, "slides": slides + self.item["slides"][1:]},
                                           _persona(), index=0, root=self.root)

    def test_written_package_is_reloadable_and_names_the_exact_expected_files(self):
        path = carousel_handoff.write_package(self.item, _persona(), index=0, root=self.root)
        pkg = json.loads(path.read_text())
        self.assertEqual(pkg["provider"], carousel_handoff.PROVIDER)
        self.assertFalse(pkg["unattended"])                           # never claim it is an API
        self.assertTrue(pkg["provider_independent"])
        self.assertEqual(pkg["ingestion"]["filenames"],
                         [f"item0_slide{i}.png" for i in range(1, 6)])
        self.assertEqual(carousel_handoff.resolve_reference(pkg["ingestion"]["directory"],
                                                            index=0, root=self.root),
                         carousel_handoff.package_dir(0, self.root))

    def test_package_records_no_absolute_paths_and_every_reference_resolves(self):
        # The package is handed to a worker elsewhere, so a machine-specific
        # absolute path in it is a relocation bug waiting to happen.
        _png(carousel_handoff.package_dir(0, self.root) / "ref.png")
        path = carousel_handoff.write_package(self.item, _persona(), index=0, root=self.root)
        raw = path.read_text()
        for absolute in (str(self.root), str(carousel_handoff.PROJECT_ROOT)):
            self.assertNotIn(absolute, raw, absolute)

        def strings(node):
            if isinstance(node, dict):
                for value in node.values():
                    yield from strings(value)
            elif isinstance(node, list):
                for value in node:
                    yield from strings(value)
            elif isinstance(node, str):
                yield node

        pkg = json.loads(raw)
        for text in strings(pkg):
            self.assertFalse(text.startswith("/"), text)

        refs = [r for r in pkg["continuity_sheet"]["identity"]["canonical_references"]
                if r["kind"] == "local"] + [pkg["ingestion"]["directory"]]
        self.assertTrue(any(r.get("base") == "package" for r in refs))    # the working ref copy
        for ref in refs:
            resolved = carousel_handoff.resolve_reference(ref, index=0, root=self.root)
            self.assertTrue(resolved.exists(), ref)
        for name in pkg["ingestion"]["filenames"]:
            directory = carousel_handoff.resolve_reference(pkg["ingestion"]["directory"],
                                                           index=0, root=self.root)
            self.assertIn(directory / name,
                          carousel_handoff.expected_slide_paths(self.item, 0, self.root))

    def test_a_partial_or_fake_delivery_never_reaches_asset_generated(self):
        v = {"content_plan": {"content_items": [self.item]}}
        paths = carousel_handoff.expected_slide_paths(self.item, 0, self.root)
        self.assertEqual(len(carousel_handoff.missing_slides(self.item, 0, self.root)), 5)
        for p in paths[:4]:
            _png(p)
        paths[4].parent.mkdir(parents=True, exist_ok=True)
        paths[4].write_text("not an image")                           # right name, wrong bytes
        self.assertEqual(carousel_handoff.missing_slides(self.item, 0, self.root), [paths[4].name])
        with self.assertRaises(carousel_handoff.HandoffError):
            carousel_handoff.ingest_slides(v, 0, root=self.root)
        self.assertNotIn("lifecycle_status", self.item)

    def test_a_complete_delivery_ingests_deterministically_for_zero_credits(self):
        v = {"content_plan": {"content_items": [self.item]}}
        for p in carousel_handoff.expected_slide_paths(self.item, 0, self.root):
            _png(p)
        res = carousel_handoff.ingest_slides(v, 0, root=self.root)
        self.assertEqual((res["status"], res["slides"], res["cost_credits"]), ("ASSET_GENERATED", 5, 0))
        self.assertEqual(self.item["lifecycle_status"], "ASSET_GENERATED")
        self.assertEqual(len(self.item["asset_refs"]), 5)
        self.assertEqual(self.item["carousel_generation"]["provider"], carousel_handoff.PROVIDER)
        self.assertEqual(len(self.item["carousel_generation"]["slides"]), 5)

    def test_one_bad_slide_is_replaced_without_redoing_the_other_four(self):
        v = {"content_plan": {"content_items": [self.item]}}
        for p in carousel_handoff.expected_slide_paths(self.item, 0, self.root):
            _png(p)
        carousel_handoff.ingest_slides(v, 0, root=self.root)
        before = list(self.item["asset_refs"])
        fresh = self.root / "redraw.png"
        _png(fresh)
        carousel_handoff.replace_slide(v, 0, 3, fresh, root=self.root)
        self.assertEqual(self.item["asset_refs"], before)             # same filenames, new bytes
        self.assertEqual(self.item["carousel_generation"]["replacements"][0]["slot"], 3)
        self.assertEqual(self.item["lifecycle_status"], "ASSET_GENERATED")   # back to QA

    def test_overlay_plan_keeps_text_out_of_generation_and_wordless_slides_wordless(self):
        plan = carousel_handoff.overlay_plan(self.item, 0, self.root)
        self.assertEqual([p["slot"] for p in plan], [1, 3, 5])        # slides 2 and 4 are wordless
        self.assertTrue(plan[0]["output"].endswith("item0_slide1_captioned.png"))

    def test_zero_spend_is_the_default_route_even_with_credits_available(self):
        d = carousel_handoff.provider_decision(self.item, credits_remaining=40)
        self.assertEqual((d["provider"], d["incremental_cost"]), (carousel_handoff.PROVIDER, "none"))
        approved = _good_carousel(**{stages.CAROUSEL_PAID_APPROVAL_FIELD: True})
        self.assertEqual(carousel_handoff.provider_decision(approved, credits_remaining=40)["provider"],
                         "higgsfield")
        self.assertEqual(carousel_handoff.provider_decision(approved, credits_remaining=0)["provider"],
                         carousel_handoff.PROVIDER)


class ChannelRegistry(unittest.TestCase):
    """The channel-aware distribution layer (founder 2026-09-22): the Content
    Director may prepare variants for any channel, but only a channel whose
    route is verified may publish -- and that set must never drift from the
    publisher's own."""

    def test_only_verified_routes_publish_and_the_set_matches_the_publisher(self):
        metricool = tuple(n for n in channels.publishing_channels()
                          if channels.CHANNELS[n]["route"] == "metricool")
        self.assertEqual(metricool, nyx_instagram.PLATFORMS)   # no silent drift
        for name in ("threads", "x", "reddit"):
            with self.subTest(channel=name):
                self.assertFalse(channels.CHANNELS[name]["publishes"])
                self.assertNotIn(name, channels.publishing_channels())
                self.assertTrue(channels.CHANNELS[name]["blocker"])
        self.assertEqual(channels.status_of("threads"), channels.UNVERIFIED)
        self.assertEqual(channels.status_of("x"), channels.PREPARED_NOT_CONNECTED)
        self.assertEqual(channels.status_of("reddit"), channels.DEFERRED)
        for cfg in channels.CHANNELS.values():
            self.assertIn(cfg["status"], channels.CHANNEL_STATUSES)

    def test_variants_are_per_channel_and_never_the_same_copy_twice(self):
        item = {"threads_variant": "the charm moved again. i left it on the shelf.",
                "x_variant": "the charm moved again. i left it on the shelf."}
        self.assertEqual(channels.variants_present(item), ("threads", "x"))
        self.assertTrue(channels.variant_problems(item))          # verbatim cross-post
        item["x_variant"] = "it moved. again."
        self.assertEqual(channels.variant_problems(item), [])
        self.assertTrue(channels.variant_problems({"mastodon_variant": "x"}))
        self.assertEqual(channels.variant("threads" and item, "threads"),
                         "the charm moved again. i left it on the shelf.")

    def test_distribution_counts_are_real_and_unreadable_metrics_say_so(self):
        items = [{"target_platform": "tiktok", "lifecycle_status": "PUBLISHED"},
                 {"target_platform": "instagram", "lifecycle_status": "SCHEDULED",
                  "threads_variant": "a fragment of the same night"},
                 {"target_platform": "instagram", "lifecycle_status": "BRIEF_READY",
                  "crossposts": [{"platform": "instagram", "status": "SCHEDULED"}]}]
        d = channels.per_channel_distribution(items)
        self.assertEqual(d["tiktok"]["published"], 1)
        self.assertEqual(d["instagram"]["scheduled"], 2)          # one item + one crosspost
        self.assertEqual(d["threads"]["variants_prepared"], 1)
        self.assertEqual(d["threads"]["published"], 0)
        for metric in ("reach_or_impressions", "engagement",
                       "follows_or_profile_visits", "funnel_clicks"):
            self.assertEqual(d["threads"][metric], channels.NOT_AVAILABLE)


class ApplyStoryOpsCommand(_TmpState):
    """`apply-story <item> <story.json>` -- the sanctioned way to revise a
    carousel's story, so venture.json is never hand-edited. Validated before
    it writes, and it may only touch a brief."""

    def setUp(self):
        super().setUp()
        self.item = _good_carousel()
        self.v["content_plan"] = {"content_items": [self.item, {"target_platform": "tiktok"}]}
        st.save(self.v)
        self.story = Path(self._tmp.name) / "story.json"

    def _write(self, slides, **over):
        self.story.write_text(json.dumps({"story_id": "the_charm_001", "slides": slides, **over}))
        return str(self.story)

    def _social_native(self):
        return [dict(s, composition=f"handheld phone shot, angle {i}, lived-in room",
                     text_overlay=f"line {i}")
                for i, s in enumerate(self.item["slides"], start=1)]

    def test_an_approved_revision_replaces_the_slides_and_keeps_the_old_ones(self):
        out = ps05_ops.apply_story("0", self._write(self._social_native(),
                                                    approved_by="founder 2026-09-23"))
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["status"], out["slides"]), ("STORY_REVISED", 5))
        item = st.load()["content_plan"]["content_items"][0]
        self.assertEqual([s["text_overlay"] for s in item["slides"]],
                         [f"line {i}" for i in range(1, 6)])
        rev = item["story_revisions"][0]
        self.assertEqual(rev["approved_by"], "founder 2026-09-23")
        self.assertEqual(len(rev["superseded_slides"]), 5)      # history preserved, not deleted
        self.assertNotIn("lifecycle_status", item)              # a story change advances nothing

    def test_a_revision_breaking_the_public_creative_rules_is_rejected_unsaved(self):
        cinematic = [dict(s, composition="cinematic key visual, dramatic volumetric god rays",
                          text_overlay=f"line {i}")
                     for i, s in enumerate(self.item["slides"], start=1)]
        out = ps05_ops.apply_story("0", self._write(cinematic))
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "REJECTED")
        self.assertTrue(any("social-native" in p for p in out["problems"]))
        self.assertEqual(st.load()["content_plan"]["content_items"][0]["slides"],
                         self.item["slides"])                   # untouched

    def test_generated_content_and_non_carousels_are_never_rewritten(self):
        good = self._write(self._social_native())
        self.assertFalse(ps05_ops.apply_story("1", good)["ok"])          # not a carousel
        self.assertFalse(ps05_ops.apply_story("9", good)["ok"])          # no such item
        v = st.load()
        v["content_plan"]["content_items"][0]["lifecycle_status"] = "ASSET_GENERATED"
        st.save(v)
        out = ps05_ops.apply_story("0", good)
        self.assertFalse(out["ok"])
        self.assertIn("only a brief may be rewritten", out["error"])


class StagedItem20StoryRevision(unittest.TestCase):
    """The revision staged for item20 must actually survive apply-story's own
    validation -- otherwise the next task discovers that only on execution.
    Skipped when the file is absent (generated_assets/ is gitignored)."""

    PATH = (Path(__file__).resolve().parent / "generated_assets" / "carousel_item20"
            / "item20_story_revision.json")

    def setUp(self):
        if not self.PATH.is_file():
            self.skipTest("no staged revision on this machine")
        self.story = json.loads(self.PATH.read_text())

    def test_the_staged_revision_would_be_accepted_not_rejected(self):
        candidate = {**_good_carousel(), "slides": self.story["slides"]}
        self.assertEqual(stages.story_carousel_problems(candidate), [])

    def test_it_carries_the_approved_overlays_and_keeps_the_story_id(self):
        self.assertEqual(self.story["story_id"], "the_charm_001")
        self.assertEqual([s["text_overlay"] for s in self.story["slides"]],
                         ["this wasn't here when I left.",
                          "I thought maybe I'd forgotten about it.",
                          "then it started doing this.",
                          "I've never opened that trunk.",
                          "bad idea?"])
        self.assertEqual([s["role"] for s in self.story["slides"]],
                         ["HOOK", "CONTEXT", "ESCALATION", "PROGRESSION", "PAYOFF"])


_REAL_LOAD_STORY_LOCK = story_continuity.load_story_lock


def _patch_charm_lock_with_fixture(case, generation_enabled=None, founder_approvals=True,
                                   visual_qa_authorized=None):
    """ps05_ops loads the REAL lock from disk; the slide-2 demonstrations need
    the simulated slide-1 fixture the real lock no longer carries, so serve a
    copy of the real lock with it re-injected (see _charm_lock).

    generation_enabled explicitly fixes the fixture's generation.enabled
    value instead of passing through whatever the real file currently has --
    this flag is now a genuine, mutable deployment toggle (the founder
    authorized the local zero-cost route 2026-09-26), so a test asserting a
    DRY_RUN-only code path must say so explicitly rather than depend on
    production's current setting. visual_qa_authorized is the same idea for
    visual_qa.authorized (the founder separately delegated visual-QA review
    2026-09-26)."""
    original = story_continuity.load_story_lock
    story_continuity.load_story_lock = (
        lambda index, *a, **k: _charm_lock(founder_approvals=founder_approvals,
                                          generation_enabled=generation_enabled,
                                          visual_qa_authorized=visual_qa_authorized)
        if index == 20 else original(index, *a, **k))
    case.addCleanup(setattr, story_continuity, "load_story_lock", original)


def _charm_lock(founder_approvals=True, generation_enabled=None, visual_qa_authorized=None):
    """The real story lock for ITEM 20 / the_charm_001 -- it is the regression
    fixture for sequential story-image continuity, so the tests run against the
    file that actually compiles prompts, not a copy of it.

    founder_approvals=False drops every approval, which is how a story looks the
    moment it is created: nothing approved, environment PROVISIONAL, slide 2
    refused. founder_approvals=True serves the simulated slide-1 fixture INSTEAD
    of whatever real approval the live lock now carries, so the simulated-path
    demonstrations keep testing the simulated path.

    generation_enabled=None (default) passes through whatever the real file
    currently has -- real drift in the plan/identity/environment sections is
    still caught. Pass True/False to pin an EXPLICIT, isolated value for a
    test whose whole point IS the enabled/disabled code path, so it can never
    flip pass/fail just because a founder toggled production generation on.
    visual_qa_authorized=None/True/False is the same pattern for
    visual_qa.authorized (the founder separately delegated visual-QA review
    2026-09-26, a second independently-toggleable production flag)."""
    lock = _REAL_LOAD_STORY_LOCK(20)
    assert lock is not None, "brand/story_locks/the_charm_001.json is missing"
    # The real lock no longer carries the simulated fixture (removed by the
    # founder 2026-09-24 before real generation); the demonstration re-injects
    # the exact entry it used to carry, from the lock's own history. Real
    # founder approvals (2026-09-26 slide 1) may be there and are not simulated.
    assert not any(e.get("simulated") for e in lock.get("founder_approvals") or []), \
        "the real lock must not carry a simulated approval"
    if generation_enabled is not None:
        lock = {**lock, "generation": {**lock["generation"], "enabled": generation_enabled}}
    if visual_qa_authorized is not None:
        lock = {**lock, "visual_qa": {**lock["visual_qa"], "authorized": visual_qa_authorized,
                                      "provider": (lock["visual_qa"].get("provider")
                                                  if visual_qa_authorized else None)}}
    if not founder_approvals:
        return {**lock, "founder_approvals": []}
    fixture = {k: v for k, v in lock["founder_approvals_history"][0].items()
               if not k.startswith("remov")}
    assert fixture["simulated"] and fixture["slide_index"] == 1
    return {**lock, "founder_approvals": [fixture]}


def _qa_all(**over):
    return {**{d: True for d in story_continuity.QA_DIMENSIONS}, **over}


class AppendOnlyApprovalRevocationProposal(_TmpState):
    """PROPOSAL code, not yet live against any real state (founder review
    2026-09-26, Manual mode): revoke_approval() reverses an approval without
    ever editing the SUBSTANTIVE fields of the original approved_slide_refs
    entry (value/approved_by/approved_at/simulated/slide_index/base) -- the
    one documented exception is a bookkeeping `approval_id` stamped onto
    that one entry, which is the actual mechanism revocation binds to (see
    below). Every fixture here uses its OWN tmp directory (self.root, from
    _TmpState) for any real file it writes -- nothing in this class reads or
    writes brand/story_locks/the_charm_001.json, the real generated_assets
    directory, or any real story_state file.

    Binding went through TWO real, review-caught bugs before landing here --
    both are worth knowing, since either mistake is easy to make again:
      1. Filename-only matching: the real production path (ps05_ops.
         generate_slide) always writes every attempt to content_items[20]'s
         SAME canonical filename, so filename-only matching would
         permanently reject any genuinely fixed re-render saved under that
         normal, reused name.
      2. Content-hash matching (the first fix for #1): comparing the
         CURRENT file's bytes against what was revoked seemed like the
         fix, but it meant simply overwriting the canonical file with new,
         NEVER-REVIEWED bytes -- with NO new approve_slide()/QA call at all
         -- would silently revive a revoked approval under the original
         reviewer's name and timestamp. Reproduced and confirmed in review.

    The actual, correct binding (implemented here) is to the APPROVAL EVENT:
    revoke_approval() stamps a unique `approval_id` onto the SPECIFIC entry
    it revokes. A genuinely new approve_slide() call always creates a FRESH
    entry (existing, unmodified behavior: it replaces the slot's live
    entry), which therefore starts with NO approval_id -- so it can never be
    confused with a revoked one, regardless of filename or byte content.
    Editing bytes on disk without ever calling approve_slide() again changes
    nothing: the exact same (already-id-stamped, already-revoked) entry is
    still what's stored, so it is still found revoked."""

    def setUp(self):
        super().setUp()
        self.root = Path(self._tmp.name) / "assets"

    def _lock(self):
        return _charm_lock(generation_enabled=False, founder_approvals=False)

    def _approve(self, lock, state, slot, *, ref):
        record = story_continuity.plan_record(lock, slot)
        story_continuity.approve_slide(state, record, slide_ref=ref, approved_by="test 2026-09-26")

    def _write(self, filename: str, content: bytes) -> Path:
        path = carousel_handoff.package_dir(20, self.root) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_existing_behaviour_is_unchanged_when_no_revocation_exists(self):
        # The exact case that matters most, and needs no real file at all
        # (no revocation exists, so no hash check is ever attempted): a
        # story with zero revocations must behave byte-identically to
        # before this code existed.
        lock = self._lock()
        state = story_continuity.new_state(lock)
        self._approve(lock, state, 1, ref="item20_slide1.png")
        ref = story_continuity.approved_ref(state, 1)
        self.assertIsNotNone(ref)
        self.assertEqual(ref["value"], "item20_slide1.png")

    def test_slide_one_cannot_be_revoked(self):
        lock = self._lock()
        state = story_continuity.new_state(lock)
        self._write("item20_slide1.png", b"slide-1-bytes")
        self._approve(lock, state, 1, ref="item20_slide1.png")
        with self.assertRaises(story_continuity.ContinuityError) as caught:
            story_continuity.revoke_approval(state, 1, reason="test", revoked_by="test", root=self.root)
        self.assertIn("slide 1 cannot be revoked", str(caught.exception))
        # And it must still be approved afterward -- the refusal changed nothing.
        self.assertIsNotNone(story_continuity.approved_ref(state, 1, root=self.root))

    def test_revoking_makes_the_slide_read_as_unapproved_again(self):
        lock = self._lock()
        state = story_continuity.new_state(lock)
        self._write("item20_slide1.png", b"slide-1-bytes")
        self._write("item20_slide2.png", b"slide-2-flawed-bytes")
        self._approve(lock, state, 1, ref="item20_slide1.png")
        self._approve(lock, state, 2, ref="item20_slide2.png")
        self.assertIsNotNone(story_continuity.approved_ref(state, 2, root=self.root))

        result = story_continuity.revoke_approval(state, 2, reason="malformed hand",
                                                  revoked_by="founder 2026-09-26", root=self.root)
        self.assertEqual(result["status"], "REVOKED")
        self.assertIsNone(story_continuity.approved_ref(state, 2, root=self.root))

    def test_the_original_approval_entry_is_never_edited_or_removed(self):
        # The whole point of "append-only": the ORIGINAL record must survive,
        # unchanged, so the true history (it WAS approved, then revoked) is
        # never lost.
        lock = self._lock()
        state = story_continuity.new_state(lock)
        self._write("item20_slide1.png", b"slide-1-bytes")
        self._write("item20_slide2.png", b"slide-2-flawed-bytes")
        self._approve(lock, state, 1, ref="item20_slide1.png")
        self._approve(lock, state, 2, ref="item20_slide2.png")
        original = dict(next(r for r in state["approved_slide_refs"] if r["slide_index"] == 2))

        story_continuity.revoke_approval(state, 2, reason="malformed hand", revoked_by="founder",
                                         root=self.root)

        still_there = next(r for r in state["approved_slide_refs"] if r["slide_index"] == 2)
        # Every SUBSTANTIVE field is untouched -- value/approved_by/
        # approved_at/simulated/slide_index/base, exactly as approve_slide()
        # wrote them.
        for key, value in original.items():
            self.assertEqual(still_there[key], value, key)
        # The one documented, intentional exception: revoke_approval stamps
        # a bookkeeping approval_id onto the entry it revokes (see
        # approved_ref's docstring for exactly why) -- it wasn't there
        # before, and its presence doesn't alter any field checked above.
        self.assertNotIn("approval_id", original)
        self.assertIn("approval_id", still_there)
        self.assertEqual(len(state["approved_slide_refs"]), 2)   # nothing removed either

    def test_original_approval_metadata_survives_serialize_reload_and_reapproval_inside_the_revocation_record(self):
        # Reviewer-flagged gap (2026-09-26): the test above proves the LIVE
        # approved_slide_refs entry is untouched -- but that entry is exactly
        # what approve_slide() REPLACES on the slot's next reapproval. Before
        # this fix, revoke_approval's revocation record carried only value/
        # approval_id/content_hash -- not approved_by/approved_at/simulated
        # -- so once a fresh reapproval happened, NOTHING anywhere still held
        # who originally approved the rejected candidate or when. Proven
        # here through a REAL save_state()/load_state() JSON round trip
        # before the reapproval, not a retained Python object reference
        # (which would pass even without the fix, since Python objects
        # in memory don't get replaced just because a dict key does).
        lock = self._lock()
        state = story_continuity.new_state(lock)
        self._write("item20_slide1.png", b"slide-1-bytes")
        self._write("item20_slide2.png", b"slide-2-flawed-bytes")
        self._approve(lock, state, 1, ref="item20_slide1.png")
        story_continuity.approve_slide(
            state, story_continuity.plan_record(lock, 2), slide_ref="item20_slide2.png",
            approved_by="claude_agent_delegated_review (founder-authorized 2026-09-26)")
        story_continuity.revoke_approval(state, 2, reason="malformed hand", revoked_by="founder",
                                         root=self.root)
        story_continuity.save_state(state, 20, root=self.root)

        # Reload from disk -- this is now a brand new dict, not the same
        # Python object revoke_approval mutated -- then reapprove with a
        # DIFFERENT approver, replacing the live entry.
        reloaded = story_continuity.load_state(lock, 20, root=self.root)
        story_continuity.approve_slide(reloaded, story_continuity.plan_record(lock, 2),
                                       slide_ref="item20_slide2.png",
                                       approved_by="test-fresh-qa-2026-09-26")
        story_continuity.save_state(reloaded, 20, root=self.root)

        final = story_continuity.load_state(lock, 20, root=self.root)
        live_entry = next(r for r in final["approved_slide_refs"] if r["slide_index"] == 2)
        self.assertEqual(live_entry["approved_by"], "test-fresh-qa-2026-09-26")   # replaced, as designed

        revocation = next(r for r in final["revocations"] if r["slide_index"] == 2)
        original = revocation["original_approval"]
        self.assertEqual(original["approved_by"],
                         "claude_agent_delegated_review (founder-authorized 2026-09-26)")
        self.assertEqual(original["value"], "item20_slide2.png")
        self.assertEqual(original["slide_index"], 2)
        self.assertEqual(original["base"], "package")
        self.assertFalse(original["simulated"])
        self.assertIn("approved_at", original)
        self.assertNotIn("approval_id", original)   # bookkeeping added AFTER this snapshot, correctly excluded

    def test_readiness_re_blocks_the_next_slide_after_a_revocation(self):
        lock = self._lock()
        state = story_continuity.new_state(lock)
        self._write("item20_slide1.png", b"slide-1-bytes")
        self._write("item20_slide2.png", b"slide-2-flawed-bytes")
        self._approve(lock, state, 1, ref="item20_slide1.png")
        self._approve(lock, state, 2, ref="item20_slide2.png")
        story_continuity.revoke_approval(state, 2, reason="malformed hand", revoked_by="founder",
                                         root=self.root)

        # readiness() forwards root to approved_ref() (2026-09-26 fix) so it
        # checks the SAME directory the caller is actually using -- pass it
        # here, exactly as the real ps05_ops.py call sites now do.
        record3 = story_continuity.plan_record(lock, 3)
        gate = story_continuity.readiness(state, record3, root=self.root)
        self.assertFalse(gate["ready"])
        self.assertIn("slide 2 is not approved", gate["blockers"][0])

    def test_revoking_an_earlier_slide_cascades_to_later_approved_ones(self):
        # Order semantics: slide 3's approval was granted on the assumption
        # that slide 2 was (and would stay) good. Revoking 2 must not leave
        # 3 looking independently valid.
        lock = self._lock()
        state = story_continuity.new_state(lock)
        self._write("item20_slide1.png", b"slide-1-bytes")
        self._write("item20_slide2.png", b"slide-2-flawed-bytes")
        self._write("item20_slide3.png", b"slide-3-bytes")
        self._approve(lock, state, 1, ref="item20_slide1.png")
        self._approve(lock, state, 2, ref="item20_slide2.png")
        self._approve(lock, state, 3, ref="item20_slide3.png")

        result = story_continuity.revoke_approval(state, 2, reason="malformed hand",
                                                  revoked_by="founder", root=self.root)
        self.assertEqual(result["also_revoked"], [3])
        self.assertIsNone(story_continuity.approved_ref(state, 2, root=self.root))
        self.assertIsNone(story_continuity.approved_ref(state, 3, root=self.root))
        # The cascade is labelled distinctly from a direct, reviewed rejection.
        cascade_entry = next(r for r in state["revocations"] if r["slide_index"] == 3)
        self.assertTrue(cascade_entry["cascaded"])
        self.assertEqual(cascade_entry["cascaded_from"], 2)
        direct_entry = next(r for r in state["revocations"] if r["slide_index"] == 2)
        self.assertFalse(direct_entry["cascaded"])
        # readiness() for slide 4 is consistent with the cascade: it must
        # not be reachable now that slide 3's approval is gone too.
        record4 = story_continuity.plan_record(lock, 4)
        gate4 = story_continuity.readiness(state, record4, root=self.root)
        self.assertFalse(gate4["ready"])

    def test_normal_path_canonical_filename_reuse_regenerate_reapprove(self):
        # THE bug this whole correction pass exists for: ps05_ops.
        # generate_slide always writes to the SAME canonical filename
        # (carousel_handoff.slide_filename) on every attempt -- a real
        # regenerated, fixed candidate lands at the identical path the
        # flawed one did. Simulates that exact normal path: mocked
        # generation writes new bytes to the SAME filename, a fresh QA pass
        # approves it, and it must read as genuinely re-approved.
        lock = self._lock()
        state = story_continuity.new_state(lock)
        self._write("item20_slide1.png", b"slide-1-bytes")
        self._approve(lock, state, 1, ref="item20_slide1.png")

        # Attempt 1: flawed candidate, written to the canonical filename.
        self._write("item20_slide2.png", b"attempt-1-malformed-hand-bytes")
        self._approve(lock, state, 2, ref="item20_slide2.png")
        story_continuity.revoke_approval(state, 2, reason="malformed hand", revoked_by="founder",
                                         root=self.root)
        self.assertIsNone(story_continuity.approved_ref(state, 2, root=self.root))

        # "Regeneration": a mocked generation call overwrites the SAME
        # canonical filename with genuinely different bytes (the fix).
        self._write("item20_slide2.png", b"attempt-2-fixed-bytes")
        # Fresh QA pass, reapproving the same slot/filename.
        self._approve(lock, state, 2, ref="item20_slide2.png")

        current = story_continuity.approved_ref(state, 2, root=self.root)
        self.assertIsNotNone(current, "a genuinely regenerated file under the reused canonical "
                                      "filename must be recognised as a new candidate, not stuck "
                                      "revoked forever")
        self.assertEqual(current["value"], "item20_slide2.png")

        # readiness() for slide 3 must now correctly unblock again.
        record3 = story_continuity.plan_record(lock, 3)
        self.assertTrue(story_continuity.readiness(state, record3, root=self.root)["ready"])

    def test_editing_the_file_without_any_new_approval_call_never_revives_it(self):
        # THE reported bug, reproduced then fixed: revoke slide 2, then
        # overwrite the canonical PNG with brand new, NEVER-REVIEWED bytes --
        # but do NOT call approve_slide() again. No real QA/review event
        # happened, so this must still read as revoked. An earlier
        # (content-hash-based) version of this code got this wrong: it
        # treated "the bytes on disk differ from what was revoked" as
        # sufficient evidence of a new approval, which let a mere file edit
        # silently resurrect a dead approval under the original reviewer's
        # name and timestamp. Binding to the approval EVENT (see
        # approved_ref's docstring) fixes this: without a new approve_slide()
        # call, the exact same (already-revoked) entry is still what's
        # stored, no matter what is on disk.
        lock = self._lock()
        state = story_continuity.new_state(lock)
        self._write("item20_slide1.png", b"slide-1-bytes")
        self._write("item20_slide2.png", b"attempt-1-malformed-hand-bytes")
        self._approve(lock, state, 1, ref="item20_slide1.png")
        self._approve(lock, state, 2, ref="item20_slide2.png")
        story_continuity.revoke_approval(state, 2, reason="malformed hand", revoked_by="founder",
                                         root=self.root)
        self.assertIsNone(story_continuity.approved_ref(state, 2, root=self.root))

        # Overwrite the canonical file with new bytes -- NO approve_slide()
        # call, i.e. no real review of these bytes ever happened.
        self._write("item20_slide2.png", b"nobody-reviewed-these-bytes")
        self.assertIsNone(story_continuity.approved_ref(state, 2, root=self.root),
                         "editing the file alone must never revive a revoked approval")
        record3 = story_continuity.plan_record(lock, 3)
        self.assertFalse(story_continuity.readiness(state, record3, root=self.root)["ready"])

    def test_after_a_failed_qa_reattempt_downstream_stays_blocked(self):
        # "After failed QA": a real re-review happens and FAILS again
        # (reject_slide, not approve_slide) -- must not revive the
        # revocation either; reject_slide never touches approved_slide_refs.
        lock = self._lock()
        state = story_continuity.new_state(lock)
        self._write("item20_slide1.png", b"slide-1-bytes")
        self._write("item20_slide2.png", b"attempt-1-malformed-hand-bytes")
        self._approve(lock, state, 1, ref="item20_slide1.png")
        self._approve(lock, state, 2, ref="item20_slide2.png")
        story_continuity.revoke_approval(state, 2, reason="malformed hand", revoked_by="founder",
                                         root=self.root)

        self._write("item20_slide2.png", b"attempt-2-still-malformed-bytes")
        record2 = story_continuity.plan_record(lock, 2)
        qa = story_continuity.qa_result({"anatomy_correctness": False})
        story_continuity.reject_slide(state, record2, qa)   # a real, failed re-review

        self.assertIsNone(story_continuity.approved_ref(state, 2, root=self.root))
        record3 = story_continuity.plan_record(lock, 3)
        self.assertFalse(story_continuity.readiness(state, record3, root=self.root)["ready"])

    def test_a_genuine_new_approval_call_is_a_new_event_even_with_identical_bytes(self):
        # The flip side, stated explicitly: a REAL approve_slide() call is a
        # genuine review decision regardless of what bytes result -- even if
        # a reviewer re-approves the exact same bytes (e.g. having decided
        # the original rejection was overly strict), that is a deliberate
        # new event and must be honoured, not second-guessed by comparing
        # bytes. The system's only real protection is requiring an actual
        # approve_slide() call; it does not (and should not) try to out-
        # guess a reviewer's genuine new decision by hashing pixels.
        lock = self._lock()
        state = story_continuity.new_state(lock)
        self._write("item20_slide1.png", b"slide-1-bytes")
        self._write("item20_slide2.png", b"slide-2-bytes")
        self._approve(lock, state, 1, ref="item20_slide1.png")
        self._approve(lock, state, 2, ref="item20_slide2.png")
        story_continuity.revoke_approval(state, 2, reason="second thoughts", revoked_by="founder",
                                         root=self.root)
        self.assertIsNone(story_continuity.approved_ref(state, 2, root=self.root))

        self._approve(lock, state, 2, ref="item20_slide2.png")   # same file, but a REAL new call
        self.assertIsNotNone(story_continuity.approved_ref(state, 2, root=self.root))

    def test_revoking_a_slide_with_no_current_approval_is_refused(self):
        lock = self._lock()
        state = story_continuity.new_state(lock)
        self._write("item20_slide1.png", b"slide-1-bytes")
        self._approve(lock, state, 1, ref="item20_slide1.png")
        with self.assertRaises(story_continuity.ContinuityError) as caught:
            story_continuity.revoke_approval(state, 2, reason="x", revoked_by="founder", root=self.root)
        self.assertIn("no current (non-revoked) approval", str(caught.exception))

    def test_staleness_blocks_generation_when_story_state_rests_on_a_revoked_slide(self):
        # Reviewer-flagged gap: revoking a slide does not rewind story_state/
        # composition_history, so without this check a later slide could be
        # generated from narrative context a since-revoked approval
        # produced. History is preserved (nothing recomputed or deleted);
        # generation is blocked instead.
        lock = self._lock()
        state = story_continuity.new_state(lock)
        self._write("item20_slide1.png", b"slide-1-bytes")
        self._write("item20_slide2.png", b"slide-2-bytes")
        self._approve(lock, state, 1, ref="item20_slide1.png")
        self._approve(lock, state, 2, ref="item20_slide2.png")
        self.assertEqual(state["story_state"]["updated_from_slide"], 2)

        story_continuity.revoke_approval(state, 2, reason="malformed hand", revoked_by="founder",
                                         root=self.root)
        record3 = story_continuity.plan_record(lock, 3)
        gate = story_continuity.readiness(state, record3, root=self.root)
        self.assertFalse(gate["ready"])
        self.assertTrue(any("story_state was last advanced by slide 2" in b for b in gate["blockers"]))
        # Nothing was deleted or recomputed -- the stale value is still
        # right there for a human to inspect.
        self.assertEqual(state["story_state"]["updated_from_slide"], 2)

    def test_cost_state_approved_slides_reflects_the_true_current_count(self):
        lock = self._lock()
        state = story_continuity.new_state(lock)
        self._write("item20_slide1.png", b"slide-1-bytes")
        self._write("item20_slide2.png", b"slide-2-flawed-bytes")
        self._write("item20_slide3.png", b"slide-3-bytes")
        self._approve(lock, state, 1, ref="item20_slide1.png")
        self._approve(lock, state, 2, ref="item20_slide2.png")
        self._approve(lock, state, 3, ref="item20_slide3.png")
        self.assertEqual(state["cost_state"]["approved_slides"], 3)

        story_continuity.revoke_approval(state, 2, reason="malformed hand", revoked_by="founder",
                                         root=self.root)   # cascades to slide 3 too
        self.assertEqual(state["cost_state"]["approved_slides"], 1)   # only slide 1 remains valid

    def test_project_active_context_never_admits_an_approved_slide_whose_plan_record_is_missing(self):
        # Reviewer-flagged gap (2026-09-26): the walk used to append `ref` to
        # projected_approved_slide_refs BEFORE confirming plan_record(lock,
        # slot) actually exists, so a slide with a valid approval but a
        # missing/mismatched plan entry (a lock/plan drift this system
        # should never produce, but must never silently trust either) was
        # admitted into the projected approved list while its narrative
        # facts were never replayed into story_state/composition_history --
        # an inconsistent, silently fail-OPEN context. Fixed: a slide is
        # admitted only once both its approval AND its plan record are
        # confirmed, atomically; a missing plan record is treated exactly
        # like a gap, same as a revoked or missing approval.
        lock = self._lock()
        # A lock whose plan skips straight from slide 2 to slide 4 -- slide
        # 3 is approved in state (as a real, or drifted, production story
        # could end up), but plan_record(lock, 3) has nothing to return.
        truncated_lock = {**lock, "story_plan": [r for r in lock["story_plan"]
                                                 if r["slide_index"] != 3]}
        state = story_continuity.new_state(lock)
        self._write("item20_slide1.png", b"slide-1-bytes")
        self._write("item20_slide2.png", b"slide-2-bytes")
        self._write("item20_slide3.png", b"slide-3-bytes")
        self._approve(lock, state, 1, ref="item20_slide1.png")
        self._approve(lock, state, 2, ref="item20_slide2.png")
        self._approve(lock, state, 3, ref="item20_slide3.png")
        self.assertIsNotNone(story_continuity.approved_ref(state, 3, root=self.root))  # genuinely approved

        active = story_continuity.project_active_context(state, truncated_lock, root=self.root)
        projected_slots = [r["slide_index"] for r in active["approved_slide_refs"]]
        self.assertEqual(projected_slots, [1, 2])                # slide 3 never admitted
        self.assertEqual(active["story_state"]["updated_from_slide"], 2)   # never advanced past slide 2
        self.assertEqual([fp["slide_index"] for fp in active["composition_history"]], [1, 2])


class ActiveContextProjectionThroughTheRealOrchestrator(_TmpState):
    """PROPOSAL, not yet live against any real state (founder review
    2026-09-26): project_active_context() derives a clean, contiguous,
    currently-valid view of story_state/composition_history for prompt
    compilation, so a revoked slide's narrative advance can never leak into
    what a later (or earlier, regenerated) slide is generated from. Unlike
    AppendOnlyApprovalRevocationProposal above (which tests the projection
    helper and revoke_approval() directly against bare state dicts), every
    test here drives the REAL entry point (ps05_ops.generate_slide) end to
    end, against the REAL content_items[20] story lock (patched only for
    generation.enabled / founder_approvals -- see _patch_charm_lock_with_
    fixture), with ONLY the local provider call itself mocked (no GPU/MFLUX
    work, no real render). Nothing here calls approve_slide/revoke_approval
    against the real, live story_state.json -- every approval and revocation
    happens against this test's own tmp self.root, exactly like the class
    above.

    The scenario is the one actually reported by the founder 2026-09-26:
    slides 2-4 were rejected (malformed hands, missing ears). Revoking slide
    2 cascades to slide 3. Regenerating slide 2 must compile a prompt built
    from slide 1 alone -- not from the stale story_state a since-rejected
    slide 3 had advanced it to. Slide 3 stays blocked until slide 2 gets a
    genuinely fresh approval; once it does, slide 3 can regenerate from
    slide-1+2-only context; and once slide 3 itself gets a fresh approval,
    its own real narrative facts correctly flow forward into slide 4 --
    proving this is a live projection, not a story that got permanently
    stuck the moment one slide was revoked."""

    def setUp(self):
        super().setUp()
        self.root = Path(self._tmp.name) / "assets"
        self.v["persona"] = _persona()
        self.v["content_plan"] = {"content_items": [{} for _ in range(20)] + [_good_carousel()]}
        st.save(self.v)
        _patch_charm_lock_with_fixture(self, generation_enabled=True, founder_approvals=False)
        self.lock = story_continuity.load_story_lock(20)

    def _approve(self, slot, *, ref, approved_by="test 2026-09-26"):
        state = story_continuity.ensure_state(self.lock, 20, root=self.root)
        record = story_continuity.plan_record(self.lock, slot)
        story_continuity.approve_slide(state, record, slide_ref=ref, approved_by=approved_by)
        story_continuity.save_state(state, 20, root=self.root)

    def _revoke(self, slot, *, reason):
        state = story_continuity.ensure_state(self.lock, 20, root=self.root)
        result = story_continuity.revoke_approval(state, slot, reason=reason, revoked_by="founder")
        story_continuity.save_state(state, 20, root=self.root)
        return result

    def _mock_local_provider(self):
        calls = []

        def fake_generate(prompt, out_path, **kw):
            calls.append({"prompt": prompt, "reference": kw.get("reference")})
            from PIL import Image
            out_path.parent.mkdir(parents=True, exist_ok=True)
            _stub_photo((768, 960)).save(out_path)
            return {"ok": True, "status": "GENERATED", "path": str(out_path), "width": 768,
                    "height": 960, "model": local_image_provider.MODEL_ID,
                    "license": local_image_provider.MODEL_LICENSE, "route": "local",
                    "cost_usd": 0, "cost_credits": 0, "generated": True,
                    "reference_used": str(kw.get("reference"))}

        real_generate, real_preflight = local_image_provider.generate, local_image_provider.preflight
        local_image_provider.generate = fake_generate
        local_image_provider.preflight = lambda: {**real_preflight(), "ready": True, "status": "READY",
                                                  "model": local_image_provider.MODEL_ID,
                                                  "license": local_image_provider.MODEL_LICENSE}
        self.addCleanup(setattr, local_image_provider, "generate", real_generate)
        self.addCleanup(setattr, local_image_provider, "preflight", real_preflight)
        return calls

    def _audit(self, slot):
        path = (carousel_handoff.package_dir(20, self.root)
               / f"{Path(carousel_handoff.slide_filename(20, slot)).stem}.audit.json")
        return json.loads(path.read_text())

    def test_cascade_from_slide_two_removes_slide_threes_facts_then_a_fresh_approval_restores_them(self):
        # --- build the story up to slide 3, real founder rejection scenario ---
        for slot in (1, 2, 3):
            ref = carousel_handoff.slide_filename(20, slot)
            _png(self.root / "carousel_item20" / ref)
            self._approve(slot, ref=ref)
        state = story_continuity.ensure_state(self.lock, 20, root=self.root)
        self.assertEqual(state["story_state"]["updated_from_slide"], 3)

        revoked = self._revoke(2, reason="founder rejection 2026-09-26: malformed hand")
        self.assertEqual(revoked["also_revoked"], [3])           # cascade, exactly as designed

        # --- (1) & (2): slide 2 regenerates from slide-1-only context; slide
        # 3's now-invalid facts are gone from the compiled prompt ---
        calls = self._mock_local_provider()
        out2 = ps05_ops.generate_slide("20", "2", root=self.root)
        self.assertTrue(out2["ok"], out2)
        self.assertEqual(out2["status"], "GENERATED")
        self.assertEqual(len(calls), 1)
        audit2 = self._audit(2)["full_compiled_prompt"]
        self.assertIn("Carried from the APPROVED slide 1.", audit2)
        self.assertNotIn("Carried from the APPROVED slide 3.", audit2)
        self.assertNotIn("Carried from the APPROVED slide 2.", audit2)
        # slide 3's own narrative_knowledge/state_after fact, rejected along
        # with it -- must not leak into slide 2's regenerated prompt
        self.assertNotIn("points somewhere", audit2)
        self.assertNotIn("slide 3's camera", audit2)              # composition_history projected too

        # --- (3): slide 3 remains BLOCKED -- regenerating slide 2 is not the
        # same thing as approving it, so the order gate still refuses slide 3 ---
        out3_before = ps05_ops.generate_slide("20", "3", root=self.root)
        self.assertFalse(out3_before["ok"], out3_before)
        self.assertEqual(out3_before["status"], "BLOCKED_NOT_READY")
        self.assertTrue(any("slide 2 is not approved" in b for b in out3_before["readiness"]["blockers"]))
        self.assertEqual(len(calls), 1)                          # provider was never reached for slide 3

        # A genuinely fresh QA/approval of slide 2 (a real, new approve_slide
        # call -- the same canonical filename production always reuses)
        self._approve(2, ref="item20_slide2.png", approved_by="claude_agent_delegated_review "
                                                              "(fresh QA 2026-09-26)")

        # --- slide 3 is unblocked now, and regenerates from slide 1+2-only
        # context (its OWN prior, now-revoked attempt's stale facts are gone) ---
        out3_after = ps05_ops.generate_slide("20", "3", root=self.root)
        self.assertTrue(out3_after["ok"], out3_after)
        self.assertEqual(out3_after["status"], "GENERATED")
        self.assertEqual(len(calls), 2)
        audit3 = self._audit(3)["full_compiled_prompt"]
        self.assertIn("Carried from the APPROVED slide 2.", audit3)
        self.assertNotIn("Carried from the APPROVED slide 3.", audit3)

        # --- (4): only once slide 3 ITSELF gets a fresh approval do its real
        # facts correctly flow forward into slide 4 ---
        out4_before = ps05_ops.generate_slide("20", "4", root=self.root)
        self.assertFalse(out4_before["ok"], out4_before)
        self.assertEqual(out4_before["status"], "BLOCKED_NOT_READY")
        self.assertEqual(len(calls), 2)

        self._approve(3, ref="item20_slide3.png", approved_by="claude_agent_delegated_review "
                                                              "(fresh QA 2026-09-26)")
        out4_after = ps05_ops.generate_slide("20", "4", root=self.root)
        self.assertTrue(out4_after["ok"], out4_after)
        self.assertEqual(out4_after["status"], "GENERATED")
        self.assertEqual(len(calls), 3)
        audit4 = self._audit(4)["full_compiled_prompt"]
        self.assertIn("Carried from the APPROVED slide 3.", audit4)
        self.assertIn("points somewhere", audit4)                # slide 3's real fact, now legitimate

    def test_editing_the_regenerated_file_without_a_fresh_approval_never_revives_slide_two(self):
        # Same no-revival guarantee as AppendOnlyApprovalRevocationProposal's
        # test_editing_the_file_without_any_new_approval_call_never_revives_it,
        # proven here through the real orchestrator + projection instead of
        # against a bare state dict.
        for slot in (1, 2):
            ref = carousel_handoff.slide_filename(20, slot)
            _png(self.root / "carousel_item20" / ref)
            self._approve(slot, ref=ref)
        self._revoke(2, reason="founder rejection 2026-09-26: malformed hand")

        calls = self._mock_local_provider()
        out = ps05_ops.generate_slide("20", "2", root=self.root)   # regenerates the canonical file
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(calls), 1)

        # No approve_slide call happened for this new file -- it must still
        # read as unapproved, and slide 3 must still be refused.
        state = story_continuity.ensure_state(self.lock, 20, root=self.root)
        self.assertIsNone(story_continuity.approved_ref(state, 2, root=self.root))
        out3 = ps05_ops.generate_slide("20", "3", root=self.root)
        self.assertFalse(out3["ok"], out3)
        self.assertEqual(out3["status"], "BLOCKED_NOT_READY")

    def test_original_slide_two_approval_metadata_is_never_rewritten_by_the_regenerate_reapprove_cycle(self):
        _png(self.root / "carousel_item20" / "item20_slide1.png")
        _png(self.root / "carousel_item20" / "item20_slide2.png")
        self._approve(1, ref="item20_slide1.png")
        self._approve(2, ref="item20_slide2.png", approved_by="claude_agent_delegated_review "
                                                              "(founder-authorized 2026-09-26)")
        state = story_continuity.ensure_state(self.lock, 20, root=self.root)
        original = next(r for r in state["approved_slide_refs"] if r["slide_index"] == 2)
        original_snapshot = dict(original)

        self._revoke(2, reason="founder rejection 2026-09-26: malformed hand")
        state = story_continuity.ensure_state(self.lock, 20, root=self.root)
        still_there = next(r for r in state["approved_slide_refs"] if r["slide_index"] == 2)
        # revoke_approval's one documented exception: it may ADD approval_id
        # to this exact entry, and nothing else.
        for key in ("value", "approved_by", "approved_at", "simulated", "slide_index", "base"):
            self.assertEqual(still_there[key], original_snapshot[key], key)
        self.assertNotIn("approval_id", original_snapshot)
        self.assertIn("approval_id", still_there)

        calls = self._mock_local_provider()
        ps05_ops.generate_slide("20", "2", root=self.root)
        self._approve(2, ref="item20_slide2.png", approved_by="claude_agent_delegated_review "
                                                              "(fresh QA 2026-09-26)")
        state = story_continuity.ensure_state(self.lock, 20, root=self.root)
        refs_for_slide_2 = [r for r in state["approved_slide_refs"] if r["slide_index"] == 2]
        self.assertEqual(len(refs_for_slide_2), 1)                # approve_slide replaces, never appends
        fresh = refs_for_slide_2[0]
        self.assertEqual(fresh["approved_by"], "claude_agent_delegated_review (fresh QA 2026-09-26)")
        self.assertNotIn("approval_id", fresh)                    # a brand new event, unstamped
        # the ORIGINAL rejected approval is still sitting in revocations,
        # completely untouched, for anyone auditing this story later --
        # including who/when originally approved it, not just its filename
        # (reviewer-flagged gap 2026-09-26: the LIVE entry above was just
        # replaced by the fresh reapproval, so the revocation record is now
        # the ONLY place approved_by/approved_at/simulated for the original,
        # rejected candidate still exist).
        original_revocation = next(r for r in state["revocations"] if r["slide_index"] == 2)
        self.assertEqual(original_revocation["revoked_value"], "item20_slide2.png")
        self.assertEqual(original_revocation["reason"],
                         "founder rejection 2026-09-26: malformed hand")
        self.assertEqual(original_revocation["original_approval"]["approved_by"],
                         "claude_agent_delegated_review (founder-authorized 2026-09-26)")
        self.assertFalse(original_revocation["original_approval"]["simulated"])

    def test_generate_slide_refuses_before_reaching_projection_when_the_deployed_plan_has_a_gap(self):
        # Integration-level companion to AppendOnlyApprovalRevocationProposal.
        # test_project_active_context_never_admits_an_approved_slide_whose_
        # plan_record_is_missing (which proves project_active_context's own
        # fail-closed fix directly). Through the REAL generate_slide() entry
        # point, the SAME lock/plan mismatch (slide 3 approved, then dropped
        # from the deployed plan) never actually reaches project_active_
        # context at all: plan_problems() -- an earlier, pre-existing gate --
        # already refuses any lock whose story_plan is not a complete,
        # gapless 1..N sequence, before ensure_state/project_active_context
        # ever run. That is what this test demonstrates and locks in: the
        # system as a whole still fails closed for this class of drift, via
        # whichever gate actually sees it first. It is not, and is not
        # claimed to be, a demonstration of project_active_context's own
        # defensive check being reached -- that check is real, but this
        # earlier gate makes it unreachable through either real entry point
        # (generate_slide, dry_run), since both call plan_problems() first.
        for slot in (1, 2, 3):
            ref = carousel_handoff.slide_filename(20, slot)
            _png(self.root / "carousel_item20" / ref)
            self._approve(slot, ref=ref)
        state = story_continuity.ensure_state(self.lock, 20, root=self.root)
        self.assertIsNotNone(story_continuity.approved_ref(state, 3, root=self.root))

        truncated = {**self.lock, "story_plan": [r for r in self.lock["story_plan"]
                                                 if r["slide_index"] != 3]}
        current_load = story_continuity.load_story_lock
        story_continuity.load_story_lock = lambda index, *a, **k: (
            truncated if index == 20 else current_load(index, *a, **k))
        self.addCleanup(setattr, story_continuity, "load_story_lock", current_load)

        calls = self._mock_local_provider()
        out4 = ps05_ops.generate_slide("20", "4", root=self.root)
        self.assertFalse(out4["ok"], out4)
        self.assertEqual(out4["status"], "BLOCKED")
        self.assertIn("out of order", out4["error"])
        self.assertEqual(len(calls), 0)               # provider never reached -- refused before generation


class StricterAnatomyIdentityPropAndSequenceQA(unittest.TestCase):
    """Founder-directed correction (2026-09-26): content_items[20] slides 2-4
    were wrongly approved because malformed hands and a missing fox-ear
    silhouette were only ever checked as part of the broad 'identity'
    dimension, where a correct face could mask them. Each defect category
    that was actually observed on real renders now has its OWN critical
    dimension, so a reviewer is forced to look at that specific spot."""

    def test_the_four_new_dimensions_are_present_and_critical(self):
        for dim in ("anatomy_correctness", "canonical_ears_visible",
                   "prop_multiplicity", "sequence_consistency"):
            with self.subTest(dim=dim):
                self.assertIn(dim, story_continuity.QA_DIMENSIONS)
                self.assertIn(dim, story_continuity.CRITICAL_QA_DIMENSIONS)

    def test_qa_expectations_gives_each_new_dimension_a_real_expectation(self):
        lock = _charm_lock(generation_enabled=False)
        state = story_continuity.new_state(lock)
        record = story_continuity.plan_record(lock, 2)
        expectations = story_continuity.qa_expectations(lock, record, state)
        for dim in ("anatomy_correctness", "canonical_ears_visible",
                   "prop_multiplicity", "sequence_consistency"):
            with self.subTest(dim=dim):
                self.assertTrue(expectations[dim]["critical"])
                self.assertTrue(expectations[dim]["expectation"].strip())

    def test_anatomy_expectation_is_visibility_aware_not_a_five_finger_demand(self):
        lock = _charm_lock(generation_enabled=False)
        state = story_continuity.new_state(lock)
        record = story_continuity.plan_record(lock, 2)
        exp = story_continuity.qa_expectations(lock, record, state)["anatomy_correctness"]["expectation"]
        self.assertIn("do not require every finger", exp.lower())
        self.assertIn("impossib", exp.lower())

    def test_ears_expectation_allows_legitimate_occlusion_and_cropping(self):
        lock = _charm_lock(generation_enabled=False)
        state = story_continuity.new_state(lock)
        record = story_continuity.plan_record(lock, 2)
        exp = story_continuity.qa_expectations(lock, record, state)["canonical_ears_visible"]["expectation"]
        low = exp.lower()
        # Cropped out of frame entirely: not a failure.
        self.assertIn("cropped so the top of her head is out of frame entirely", low)
        self.assertIn("is not a failure", low)
        # A profile/3-quarter angle showing only the near ear (the far one
        # anatomically hidden by her own head): also not a failure -- this
        # was the missing nuance, not merely "cropping".
        self.assertIn("profile", low)
        self.assertIn("only the near ear", low)
        self.assertIn("not a failure", low)
        # The actual real defect this exists to catch: head/hair in frame,
        # from an angle where an ear WOULD show, with none there at all.
        self.assertIn("no fox-ear silhouette there at all", low)

    def test_sequence_consistency_does_not_treat_rejected_slides_as_authoritative(self):
        lock = _charm_lock(generation_enabled=False)
        state = story_continuity.new_state(lock)
        record = story_continuity.plan_record(lock, 2)
        exp = story_continuity.qa_expectations(lock, record, state)["sequence_consistency"]["expectation"]
        low = exp.lower()
        self.assertIn("currently valid", low)
        self.assertIn("automatically authoritative", low)
        self.assertIn("overturned/rejected", low)

    def test_approved_ref_is_not_retroactively_invalidated_by_the_new_dimensions(self):
        # Precise scope check: an EXISTING stored approval is untouched by
        # these new dimensions existing -- approved_ref() only reads what
        # was already recorded; it does not call qa_result() or re-evaluate
        # anything. Only a FRESH qa_result() call is held to the new bar.
        lock = _charm_lock(generation_enabled=False)
        state = story_continuity.new_state(lock)
        state["approved_slide_refs"] = [{"slide_index": 2, "base": "package",
                                         "value": "item20_slide2.png",
                                         "approved_at": "2026-09-26T00:00:00+00:00",
                                         "approved_by": "claude_agent_delegated_review",
                                         "simulated": False}]
        # This old approval was recorded before anatomy_correctness etc.
        # existed, and carries no qa/dimensions field at all.
        ref = story_continuity.approved_ref(state, 2)
        self.assertIsNotNone(ref)
        self.assertEqual(ref["value"], "item20_slide2.png")   # still returned, unchanged, un-re-checked

    def test_an_old_style_qa_score_missing_the_new_dimensions_fails_closed(self):
        # A scores dict built before these four dimensions existed (the exact
        # shape of the QA that wrongly approved slides 2-4) must not silently
        # pass under the new, stricter criteria.
        old_style_scores = {"identity": True, "environment_continuity": True,
                            "wardrobe_hair_continuity": True, "prop_continuity": True,
                            "story_beat": True, "composition_novelty": True,
                            "public_social_style": True, "technical_output": True}
        qa = story_continuity.qa_result(old_style_scores)
        self.assertEqual(qa["verdict"], "REJECTED")
        for dim in ("anatomy_correctness", "canonical_ears_visible",
                   "prop_multiplicity", "sequence_consistency"):
            self.assertIn(dim, qa["critical_failures"])
            self.assertEqual(qa["dimensions"][dim], "NOT_CHECKED")

    def test_a_fully_scored_candidate_including_the_new_dimensions_can_still_pass(self):
        qa = story_continuity.qa_result(_qa_all())
        self.assertEqual(qa["verdict"], "APPROVED")
        self.assertEqual(qa["critical_failures"], [])

    def test_a_real_anatomy_or_ear_failure_is_rejected_even_if_everything_else_passes(self):
        for failing_dim in ("anatomy_correctness", "canonical_ears_visible",
                           "prop_multiplicity", "sequence_consistency"):
            with self.subTest(failing_dim=failing_dim):
                qa = story_continuity.qa_result(_qa_all(**{failing_dim: False}))
                self.assertEqual(qa["verdict"], "REJECTED")
                self.assertIn(failing_dim, qa["critical_failures"])

    def test_retry_ceiling_is_unchanged(self):
        self.assertEqual(story_continuity.MAX_ATTEMPTS_PER_SLIDE, 3)


class StoryContinuityAuthorities(unittest.TestCase):
    """The three separate authorities (founder 2026-09-23). Individual slides
    were already good; the STORY drifted -- a regenerated slide 2 kept Nyx and
    redesigned her bedroom into a larger, more polished apartment. Identity is
    immutable and cross-story, the environment master is immutable per story and
    frozen by the first APPROVED slide, and story state is the only mutable
    part -- and only an approval may move it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # generation_enabled=False: these tests are about identity/environment/
        # story-state authority, not about the mutable production toggle --
        # pin it explicitly so a founder authorizing local generation elsewhere
        # can never flip this fixture out from under them.
        self.lock = _charm_lock(generation_enabled=False)
        # A story as it is the moment it is created: nothing approved yet, so
        # these tests drive the PROVISIONAL -> LOCKED transition themselves.
        self.unapproved = _charm_lock(founder_approvals=False, generation_enabled=False)
        self.plan = story_continuity.story_plan(self.lock)
        self.item = _good_carousel()

    def _anchors(self):
        return [a["description"] for a in self.lock["environment_lock"]["anchors"]]

    def test_the_item20_story_lock_is_complete_and_its_plan_is_generatable(self):
        self.assertEqual((self.lock["story_id"], self.lock["item_index"]), ("the_charm_001", 20))
        self.assertEqual(story_continuity.plan_problems(self.lock, self.item), [])
        self.assertEqual([r["role"] for r in self.plan],
                         ["HOOK", "CONTEXT", "ESCALATION", "PROGRESSION", "PAYOFF"])
        self.assertEqual([r["overlay_copy"] for r in self.plan[:3]],
                         ["this wasn't here when I left.",
                          "I thought maybe I'd forgotten about it.",
                          "then it started doing this."])
        for record in self.plan:                       # the whole planning contract, every slide
            for field in story_continuity.PLAN_FIELDS:
                self.assertIn(field, record)
            self.assertEqual(record["public_vs_fanvue"], "public")
            self.assertEqual(record["state_after"]["time"], "night")
            self.assertIn("black T-shirt", record["state_after"]["wardrobe"])
        # No provider is enabled and none may be claimed as authorized.
        self.assertFalse(self.lock["generation"]["enabled"])
        self.assertFalse(self.lock["generation"]["paid_generation_authorized"])
        self.assertEqual(self.lock["cost_state"]["ceiling_usd"], 0)

    def test_five_poses_are_rejected_at_planning_time_not_after_five_images(self):
        def broken(mutate):
            lock = json.loads(json.dumps(self.lock))
            mutate(lock["story_plan"])
            return story_continuity.plan_problems(lock, self.item)

        same_info = broken(lambda s: s[2].__setitem__("new_information_revealed",
                                                      s[1]["new_information_revealed"]))
        self.assertTrue(any("same information" in p for p in same_info), same_info)
        same_shot = broken(lambda s: s[2].__setitem__("camera", dict(s[1]["camera"])))
        self.assertTrue(any("camera fingerprint" in p for p in same_shot), same_shot)
        no_forbid = broken(lambda s: s[1].__setitem__("forbidden_repetition", []))
        self.assertTrue(any("must not repeat" in p for p in no_forbid), no_forbid)
        no_anchors = broken(lambda s: self.lock)          # plan untouched
        self.assertEqual(no_anchors, [])

    def test_prompt_precedence_is_identity_then_story_then_environment(self):
        state = story_continuity.new_state(self.lock)
        compiled = story_continuity.compile_prompt(_persona(), state, self.plan[1])
        self.assertEqual([s["layer"] for s in compiled["sections"]],
                         list(story_continuity.PRECEDENCE))
        self.assertEqual([s["authority"] for s in compiled["sections"]], [1, 2, 3, 4, 5, 6])
        prompt = compiled["prompt"]
        self.assertIn("beauty mark on her anatomical LEFT cheek (viewer-right", prompt)       # identity lock, every slide
        self.assertIn("never evidence for who Nyx is", prompt)        # prev slide cannot redefine her
        for anchor in self._anchors():                                # environment master, in full
            self.assertIn(anchor, prompt)
        self.assertIn(" > ".join(story_continuity.PRECEDENCE), prompt)
        self.assertNotIn(self.plan[1]["overlay_copy"].lower(), prompt.lower())   # clean art only

    def test_styling_may_not_reintroduce_a_luxury_or_cinematic_room(self):
        state = story_continuity.new_state(self.lock)
        drifted = {**self.plan[1],
                   "composition": "cinematic key visual in a luxury penthouse bedroom with a "
                                  "marble floor and a chandelier"}
        compiled = story_continuity.compile_prompt(_persona(), state, drifted)
        conflicts = compiled["precedence_conflicts"]
        self.assertTrue(conflicts)
        self.assertEqual(conflicts[0]["layer"], "creative_styling")
        self.assertNotIn("marble floor and a chandelier", compiled["prompt"])
        for anchor in self._anchors():                    # the room survives the drop
            self.assertIn(anchor, compiled["prompt"])
        # A clause that PROHIBITS the same thing is policy, not drift -- keep it.
        self.assertIn("no glow, no magic", compiled["prompt"])
        self.assertIn("no movie-poster, cinematic, editorial or key-art look", compiled["prompt"])

    def test_a_different_composition_may_not_become_a_different_room(self):
        state = story_continuity.new_state(self.lock)
        story_continuity.approve_slide(state, self.plan[0], slide_ref="item20_slide1.png",
                                       qa=story_continuity.qa_result(_qa_all()))
        forbidden = story_continuity.forbidden_repetition(state, self.plan[1])
        self.assertTrue(any("slide 1's camera" in f for f in forbidden), forbidden)
        self.assertIn(story_continuity.ANTI_REPETITION_RULE, forbidden)
        prompt = story_continuity.compile_prompt(_persona(), state, self.plan[1])["prompt"]
        self.assertIn(story_continuity.ANTI_REPETITION_RULE, prompt)
        for anchor in self._anchors():
            self.assertIn(anchor, prompt)

    def test_approving_slide_one_freezes_the_environment_master_for_every_later_slide(self):
        state = story_continuity.ensure_state(self.unapproved, 20, root=self.root)
        self.assertEqual(state["environment_lock"]["status"], "PROVISIONAL")
        out = story_continuity.approve_slide(state, self.plan[0], slide_ref="item20_slide1.png",
                                             qa=story_continuity.qa_result(_qa_all()))
        self.assertEqual(out["environment_lock_status"], "LOCKED")
        story_continuity.save_state(state, 20, root=self.root)

        # Restart: the authority is the persisted file, not this process.
        resumed = story_continuity.load_state(self.lock, 20, root=self.root)
        self.assertEqual(resumed["environment_lock"]["source_slide_ref"], "item20_slide1.png")
        self.assertEqual([r["slide_index"] for r in resumed["approved_slide_refs"]], [1])
        self.assertEqual(resumed["story_state"]["updated_from_slide"], 1)
        for slot in (2, 5):            # supplied on EVERY later slide, not just the next one
            refs = story_continuity.reference_selection(_persona(), resumed, self.plan[slot - 1],
                                                        index=20, root=self.root)
            roles = [r["role"] for r in refs]
            self.assertIn("identity_reference", roles)
            self.assertIn("environment_reference", roles)
            environment = next(r for r in refs if r["role"] == "environment_reference")
            self.assertEqual(environment["value"], "item20_slide1.png")
            self.assertIn("never identity", environment["decides"])

    def test_a_rejected_slide_two_cannot_mutate_the_environment_or_the_story_state(self):
        state = story_continuity.ensure_state(self.unapproved, 20, root=self.root)
        story_continuity.approve_slide(state, self.plan[0], slide_ref="item20_slide1.png",
                                       qa=story_continuity.qa_result(_qa_all()))
        story_continuity.save_state(state, 20, root=self.root)
        before = {k: json.loads(json.dumps(state[k])) for k in
                  ("environment_lock", "story_state", "composition_history", "approved_slide_refs")}

        qa = story_continuity.qa_result(_qa_all(environment_continuity=False),
                                        notes="bedroom redrawn as a larger, polished apartment")
        self.assertEqual(qa["verdict"], "REJECTED")
        self.assertEqual(qa["critical_failures"], ["environment_continuity"])
        out = story_continuity.record_qa(state, self.plan[1], qa)
        self.assertEqual((out["status"], out["attempt"], out["state_mutated"]),
                         ("REJECTED", 1, False))
        self.assertEqual(out["retry_from_slide"], 1)        # retry from the SAME approved state
        self.assertEqual(out["retry_from"], before["story_state"])
        for key, value in before.items():
            self.assertEqual(state[key], value, key)
        story_continuity.save_state(state, 20, root=self.root)
        for key, value in before.items():                   # and after a restart, still unchanged
            self.assertEqual(story_continuity.load_state(self.lock, 20, root=self.root)[key],
                             value, key)

        for expected in (2, 3):                             # bounded retries, then stop
            out = story_continuity.record_qa(state, self.plan[1], qa)
            self.assertEqual(out["attempt"], expected)
        self.assertTrue(out["exhausted"])
        self.assertEqual(out["attempts_remaining"], 0)
        self.assertEqual(len(state["retry_state"]["rejected"]), 3)
        self.assertEqual(state["environment_lock"], before["environment_lock"])

    def test_qa_is_fail_closed_and_a_rejected_image_can_never_be_approved(self):
        state = story_continuity.new_state(self.lock)
        unchecked = story_continuity.qa_result({d: True for d in story_continuity.QA_DIMENSIONS
                                                if d != "identity"})
        self.assertEqual(unchecked["dimensions"]["identity"], "NOT_CHECKED")
        self.assertEqual(unchecked["verdict"], "REJECTED")
        self.assertIn("identity", unchecked["critical_failures"])
        soft = story_continuity.qa_result(_qa_all(composition_novelty=False))
        self.assertEqual((soft["verdict"], soft["critical_failures"]), ("REJECTED", []))
        self.assertEqual(story_continuity.qa_result(_qa_all())["verdict"], "APPROVED")
        with self.assertRaises(story_continuity.ContinuityError):
            story_continuity.approve_slide(state, self.plan[0], slide_ref="x.png", qa=soft)
        expectations = story_continuity.qa_expectations(self.lock, self.plan[1], state)
        self.assertEqual(sorted(expectations), sorted(story_continuity.QA_DIMENSIONS))
        self.assertEqual([d for d, e in expectations.items() if e["critical"]],
                         list(story_continuity.CRITICAL_QA_DIMENSIONS))

    def test_a_state_file_from_another_story_is_refused_not_overwritten(self):
        path = story_continuity.state_path(20, self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"story_id": "some_other_story",
                                    "schema_version": story_continuity.SCHEMA_VERSION}))
        with self.assertRaises(story_continuity.ContinuityError):
            story_continuity.load_state(self.lock, 20, root=self.root)
        self.assertIn("some_other_story", path.read_text())          # untouched


class StorySlideLifecycleFailsClosed(_TmpState):
    """The per-slide lifecycle, FAIL CLOSED (founder 2026-09-23). As far as this
    system is concerned slide N does not exist until slide N-1 is APPROVED; the
    approved slide 1 image -- not a prose description of the room -- is the
    environment authority for every later slide; and a candidate file sitting in
    the package directory is never an approval.

    The story lock is copied into a temp directory with the item20 demo's
    simulated founder approval removed, so these tests exercise a story from its
    natural starting point: nothing approved."""

    def setUp(self):
        super().setUp()
        self.root = Path(self._tmp.name) / "assets"
        self.v["persona"] = _persona()
        self.v["content_plan"] = {"content_items": [{} for _ in range(20)] + [_good_carousel()]}
        st.save(self.v)
        self.lock = _charm_lock(founder_approvals=False, generation_enabled=False)
        self.authorized = {**self.lock,
                           "visual_qa": {"provider": "founder_visual_review", "authorized": True}}
        locks = Path(self._tmp.name) / "story_locks"
        locks.mkdir(parents=True, exist_ok=True)
        (locks / "the_charm_001.json").write_text(json.dumps(self.lock))
        original = story_continuity.STORY_LOCKS_DIR
        story_continuity.STORY_LOCKS_DIR = locks
        self.addCleanup(setattr, story_continuity, "STORY_LOCKS_DIR", original)
        self.plan = story_continuity.story_plan(self.lock)
        self.item = st.load()["content_plan"]["content_items"][20]

    def _state(self):
        return story_continuity.ensure_state(self.lock, 20, root=self.root)

    def _approve(self, state, slot, ref):
        return story_continuity.approve_slide(state, self.plan[slot - 1], slide_ref=ref,
                                              qa=story_continuity.qa_result(_qa_all()))

    def _qa(self, state, slot, scores=None, lock=None, **kw):
        return story_continuity.qa_slide(lock or self.lock, state, self.plan[slot - 1],
                                         index=20, scores=scores, root=self.root, **kw)

    def test_slide_two_refuses_generation_until_slide_one_is_approved(self):
        state = self._state()
        gate = story_continuity.readiness(state, self.plan[1])
        self.assertFalse(gate["ready"])
        self.assertEqual((gate["status"], gate["requires_approved_slide"]), ("BLOCKED", 2 - 1))
        self.assertTrue(any("slide 1 is not approved" in b for b in gate["blockers"]), gate)
        self.assertTrue(any("no text-only fallback" in b for b in gate["blockers"]), gate)
        with self.assertRaises(story_continuity.NotReady):
            story_continuity.dry_run(self.item, _persona(), index=20, slot=2, root=self.root)

        out = ps05_ops.generate_slide("20", "2", root=self.root)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "BLOCKED_NOT_READY")
        self.assertFalse(out["generated"])
        self.assertEqual((out["cost_usd"], out["cost_credits"]), (0, 0))
        self.assertEqual(out["readiness"]["requires_approved_slide"], 1)
        self.assertIn("slide 1", out["next"])
        # Slide 1 is the one slide that may be prepared from the lock alone.
        self.assertTrue(story_continuity.readiness(state, self.plan[0])["ready"])
        self.assertTrue(ps05_ops.generate_slide("20", "1", root=self.root)["ok"])

    def test_approving_slide_one_locks_the_environment_onto_that_actual_image(self):
        state = self._state()
        self.assertEqual(state["environment_lock"]["status"], "PROVISIONAL")
        self.assertIsNone(state["environment_lock"]["source_slide_ref"])
        out = self._approve(state, 1, "item20_slide1.png")
        self.assertEqual((out["environment_lock_status"], out["environment_source_slide_ref"]),
                         ("LOCKED", "item20_slide1.png"))
        self.assertTrue(out["advanced"])
        self.assertFalse(out["duplicate"])
        # There is no text-only fallback: an approval without the real image ref
        # would lock the room onto nothing, so it is refused outright.
        with self.assertRaises(story_continuity.ContinuityError):
            story_continuity.approve_slide(story_continuity.new_state(self.lock), self.plan[0],
                                           slide_ref="", qa=story_continuity.qa_result(_qa_all()))
        # And the story still advances in order.
        with self.assertRaises(story_continuity.ContinuityError):
            story_continuity.approve_slide(story_continuity.new_state(self.lock), self.plan[2],
                                           slide_ref="item20_slide3.png")

    def test_slide_two_after_approval_carries_identity_refs_and_slide_one_as_environment(self):
        state = self._state()
        self._approve(state, 1, "item20_slide1.png")
        story_continuity.save_state(state, 20, root=self.root)

        out = ps05_ops.generate_slide("20", "2", root=self.root)
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["status"], out["readiness"]), ("DRY_RUN", "READY"))
        self.assertEqual(out["environment_lock_status"], "LOCKED")
        self.assertEqual(out["environment_source_slide_ref"], "item20_slide1.png")
        payload = json.loads(Path(out["dry_run_file"]).read_text())
        refs = payload["generation_request"]["references"]
        identity = [r for r in refs if r["role"].startswith("identity_reference")]
        self.assertTrue(identity)
        self.assertFalse(any(r["never_identity"] for r in identity))
        environment = next(r for r in refs if r["role"] == "environment_reference")
        self.assertEqual(environment["value"], "item20_slide1.png")
        self.assertTrue(environment["never_identity"])
        self.assertIn("never identity", environment["decides"])
        # For slide 2 the environment master IS the immediately previous slide,
        # so it is attached once and says so rather than being listed twice.
        self.assertNotIn("previous_slide_reference", [r["role"] for r in refs])
        self.assertIn("also the immediately previous slide", environment["note"])
        self.assertEqual(payload["story_state"]["updated_from_slide"], 1)
        self.assertIn("Carried from the APPROVED slide 1.",
                      payload["generation_request"]["prompt"])

    def test_slide_five_keeps_slide_one_as_environment_and_slide_four_as_previous(self):
        state = self._state()
        for slot in (1, 2, 3, 4):
            self._approve(state, slot, f"item20_slide{slot}.png")
        story_continuity.save_state(state, 20, root=self.root)

        payload = json.loads(Path(ps05_ops.generate_slide("20", "5", root=self.root)
                                  ["dry_run_file"]).read_text())
        refs = {r["role"]: r for r in payload["generation_request"]["references"]}
        self.assertEqual(refs["environment_reference"]["value"], "item20_slide1.png")
        self.assertEqual(refs["previous_slide_reference"]["value"], "item20_slide4.png")
        self.assertTrue(refs["previous_slide_reference"]["never_identity"])
        self.assertIn("environment master", refs["previous_slide_reference"]["note"])
        self.assertIn("identity_reference", refs)
        self.assertEqual(payload["environment_lock"]["source_slide_ref"], "item20_slide1.png")
        self.assertEqual(payload["story_state"]["updated_from_slide"], 4)
        self.assertEqual(payload["readiness"]["status"], "READY")

    def test_public_social_policy_outranks_cinematic_provider_wording(self):
        """PROMPT AUDIT: the provider vocabulary that keeps pulling this back
        towards a movie poster is dropped from the lower-authority layers and
        REPORTED, never balanced against the policy -- while the policy's own
        prohibitions, which name the same words, survive intact."""
        state = self._state()
        self._approve(state, 1, "item20_slide1.png")
        drifted = {**self.plan[1],
                   "composition": "fantasy key art of the scene. dramatic volumetric god rays "
                                  "through the window. glamour editorial studio lighting. "
                                  "a luxury penthouse bedroom with a marble floor"}
        compiled = story_continuity.compile_prompt(_persona(), state, drifted)
        phrases = {c["phrase"].lower() for c in compiled["precedence_conflicts"]}
        self.assertTrue({"key art", "volumetric", "glamour", "luxury"} <= phrases, phrases)
        self.assertTrue(all(c["layer"] in story_continuity._SANITISED_LAYERS
                            for c in compiled["precedence_conflicts"]))
        for dropped in ("god rays", "marble floor", "studio lighting", "fantasy key art"):
            self.assertNotIn(dropped, compiled["prompt"], dropped)
        for kept in ("no movie-poster, cinematic, editorial or key-art look",
                     "no glamour-shoot or influencer posing",
                     "no lingerie-adjacent styling and no cleavage-forward framing"):
            self.assertIn(kept, compiled["prompt"], kept)
        for anchor in [a["description"] for a in self.lock["environment_lock"]["anchors"]]:
            self.assertIn(anchor, compiled["prompt"])   # the real room survives the drop

    def test_a_candidate_file_never_advances_the_story_on_its_own(self):
        state = self._state()
        _png(story_continuity.candidate_path(20, 1, self.root))
        before = json.loads(json.dumps(state))
        out = self._qa(state, 1, lock=self.authorized)          # present, but nothing scored
        self.assertTrue(out["candidate_present"])
        self.assertEqual(out["status"], story_continuity.PENDING_VISUAL_QA)
        self.assertFalse(out["approved"])
        self.assertFalse(out["state_mutated"])
        self.assertEqual(state, before)
        self.assertEqual(state["environment_lock"]["status"], "PROVISIONAL")

    def test_a_missing_or_unauthorized_visual_qa_provider_never_auto_approves(self):
        state = self._state()
        _png(story_continuity.candidate_path(20, 1, self.root))
        before = json.loads(json.dumps(state))
        for visual_qa in (None, {}, {"provider": "some_scorer", "authorized": False},
                          {"provider": None, "authorized": True}):
            with self.subTest(visual_qa=visual_qa):
                out = self._qa(state, 1, scores=_qa_all(),
                               lock={**self.lock, "visual_qa": visual_qa})
                self.assertEqual(out["status"], story_continuity.PENDING_VISUAL_QA)
                self.assertFalse(out["approved"])
                self.assertFalse(out["state_mutated"])
                self.assertFalse(out["visual_qa_provider"]["available"])
        self.assertEqual(state, before)

    def test_qa_without_a_candidate_image_is_refused_even_on_a_full_pass(self):
        state = self._state()
        out = self._qa(state, 1, scores=_qa_all(), lock=self.authorized)
        self.assertEqual(out["status"], "NO_CANDIDATE")
        self.assertFalse(out["candidate_present"])
        self.assertFalse(out["state_mutated"])

    def test_qa_on_slide_two_is_blocked_before_slide_one_is_approved(self):
        state = self._state()
        _png(story_continuity.candidate_path(20, 2, self.root))
        out = self._qa(state, 2, scores=_qa_all(), lock=self.authorized)
        self.assertEqual(out["status"], "BLOCKED_NOT_READY")
        self.assertFalse(out["approved"])
        self.assertFalse(out["state_mutated"])
        self.assertEqual(state["environment_lock"]["status"], "PROVISIONAL")

    def test_a_qa_pass_advances_the_story_exactly_once_and_a_duplicate_is_safe(self):
        state = self._state()
        _png(story_continuity.candidate_path(20, 1, self.root))
        out = self._qa(state, 1, scores=_qa_all(), lock=self.authorized)
        self.assertTrue(out["approved"])
        self.assertEqual((out["status"], out["verdict"]), ("APPROVED", "APPROVED"))
        self.assertTrue(out["state_mutated"])
        # Atomic: refs, composition history, story state, environment lock and
        # the retry/cost bookkeeping all move together, and are persisted.
        self.assertEqual(state["environment_lock"]["status"], "LOCKED")
        self.assertEqual(state["environment_lock"]["source_slide_ref"], "item20_slide1.png")
        self.assertEqual(state["story_state"]["updated_from_slide"], 1)
        self.assertEqual([f["slide_index"] for f in state["composition_history"]], [1])
        self.assertEqual(state["cost_state"]["approved_slides"], 1)
        self.assertEqual(state["cost_state"]["qa_passes"], 1)
        self.assertFalse(state["cost_state"]["paid_generation_authorized"])
        resumed = story_continuity.load_state(self.lock, 20, root=self.root)
        self.assertEqual(resumed["environment_lock"]["source_slide_ref"], "item20_slide1.png")

        locked_at = state["environment_lock"]["locked_at"]
        again = self._qa(state, 1, scores=_qa_all(), lock=self.authorized)
        self.assertTrue(again["approved"])
        self.assertTrue(again["outcome"]["duplicate"])
        self.assertFalse(again["outcome"]["advanced"])
        self.assertEqual(len(state["approved_slide_refs"]), 1)
        self.assertEqual(len(state["composition_history"]), 1)
        self.assertEqual(state["environment_lock"]["locked_at"], locked_at)   # never re-frozen

    def test_rejecting_slide_two_touches_only_the_retry_bookkeeping(self):
        state = self._state()
        _png(story_continuity.candidate_path(20, 1, self.root))
        self._qa(state, 1, scores=_qa_all(), lock=self.authorized)
        _png(story_continuity.candidate_path(20, 2, self.root))
        before = {k: json.loads(json.dumps(state[k])) for k in
                  ("environment_lock", "story_state", "composition_history", "approved_slide_refs")}

        out = self._qa(state, 2, lock=self.authorized,
                       scores=_qa_all(environment_continuity=False),
                       notes="bedroom redrawn as a larger, polished apartment")
        self.assertFalse(out["approved"])
        self.assertEqual((out["status"], out["verdict"]), ("REJECTED", "REJECTED"))
        self.assertEqual(out["qa"]["critical_failures"], ["environment_continuity"])
        self.assertEqual(out["outcome"]["attempt"], 1)
        for key, value in before.items():
            self.assertEqual(state[key], value, key)
        self.assertEqual(state["retry_state"]["attempts"]["2"], 1)
        self.assertEqual(state["cost_state"]["qa_failures"], 1)
        resumed = story_continuity.load_state(self.lock, 20, root=self.root)
        for key, value in before.items():                  # and the same after a restart
            self.assertEqual(resumed[key], value, key)
        # Bounded: once the attempts are spent the slide refuses rather than looping.
        for _ in range(2):
            self._qa(state, 2, lock=self.authorized, scores=_qa_all(identity=False))
        self.assertFalse(story_continuity.readiness(state, self.plan[1])["ready"])
        self.assertEqual(ps05_ops.generate_slide("20", "2", root=self.root)["status"],
                         "BLOCKED_NOT_READY")


class Item20SlideTwoDemonstration(_TmpState):
    """ITEM 20 / the_charm_001. The story lock carries a SIMULATED founder
    approval of slide 1, applied by the orchestrator through the same approval
    path a QA pass uses -- so the slide-2 request can be compiled and inspected
    exactly as it will be compiled once a real slide 1 is approved, without an
    image existing and without a provider."""

    def setUp(self):
        super().setUp()
        self.root = Path(self._tmp.name) / "assets"
        self.v["persona"] = _persona()
        self.v["content_plan"] = {"content_items": [{} for _ in range(20)] + [_good_carousel()]}
        st.save(self.v)
        # This whole class is "without a provider" (see class docstring) --
        # pin generation_enabled=False and visual_qa_authorized=False
        # explicitly so it never depends on whatever the founder has
        # toggled in the real production lock (both are independently
        # mutable production flags as of 2026-09-26).
        _patch_charm_lock_with_fixture(self, generation_enabled=False, visual_qa_authorized=False)

    def test_the_founder_fixture_approval_is_applied_by_the_orchestrator_not_by_hand(self):
        lock = _charm_lock()
        entry = lock["founder_approvals"][0]
        self.assertEqual(entry["slide_index"], 1)
        self.assertTrue(entry["simulated"])              # a ref, never an image
        state = story_continuity.ensure_state(lock, 20, root=self.root)
        self.assertEqual(state["environment_lock"]["status"], "LOCKED")
        self.assertEqual(state["environment_lock"]["source_slide_ref"], entry["slide_ref"])
        self.assertTrue(state["environment_lock"]["simulated"])
        self.assertTrue(state["approved_slide_refs"][0]["simulated"])
        self.assertEqual(state["approved_slide_refs"][0]["approved_by"], entry["approved_by"])
        resumed = story_continuity.ensure_state(lock, 20, root=self.root)
        self.assertEqual(len(resumed["approved_slide_refs"]), 1)      # applied once, then resumed

    def test_a_simulated_approval_can_never_authorize_generation(self):
        lock = _charm_lock()
        enabled = {**lock, "generation": {**lock["generation"], "enabled": True}}
        with self.assertRaises(story_continuity.ContinuityError) as caught:
            story_continuity.dry_run(_good_carousel(), _persona(), index=20, slot=2,
                                     root=self.root, lock=enabled)
        self.assertIn("SIMULATED", str(caught.exception))

    def test_the_slide_two_dry_run_is_locked_social_native_and_free(self):
        out = ps05_ops.generate_slide("20", "2", root=self.root)
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["status"], out["readiness"]), ("DRY_RUN", "READY"))
        self.assertEqual(out["environment_lock_status"], "LOCKED")
        self.assertFalse(out["generation_enabled"])
        self.assertFalse(out["paid_generation_authorized"])
        self.assertFalse(out["visual_qa_provider"]["available"])
        self.assertEqual((out["cost_usd"], out["cost_credits"]), (0, 0))
        self.assertEqual(out["precedence_conflicts"], [])

        payload = json.loads(Path(out["dry_run_file"]).read_text())
        request = payload["generation_request"]
        roles = [r["role"] for r in request["references"]]
        self.assertIn("identity_reference", roles)
        self.assertEqual(next(r for r in request["references"]
                              if r["role"] == "environment_reference")["value"],
                         payload["environment_lock"]["source_slide_ref"])
        prompt = request["prompt"]
        for needle in ("believable smartphone/iPhone-style photography",
                       "no movie-poster, cinematic, editorial or key-art look",
                       "attractive but normal everyday clothing",
                       "Carried from the APPROVED slide 1.",
                       story_continuity.ANTI_REPETITION_RULE):
            self.assertIn(needle, prompt, needle)
        self.assertNotIn(request["overlay_copy"].lower(), prompt.lower())
        self.assertEqual(payload["cost_state"]["this_call_cost_usd"], 0)
        # Not one pixel: no slide exists after the dry run.
        item = st.load()["content_plan"]["content_items"][20]
        self.assertEqual(carousel_handoff.missing_slides(item, 20, self.root),
                         [f"item20_slide{i}.png" for i in range(1, 6)])


class StoryDryRunOpsCommand(_TmpState):
    """`python3 ps05_ops.py generate-slide 20 <slot>` under a story lock: the
    orchestrator compiles the plan, the prompt, the references and the QA
    expectations and generates NOTHING until a provider is authorized. Prompt
    authoring is the system's job -- never the founder's, never ChatGPT's."""

    def setUp(self):
        super().setUp()
        self.root = Path(self._tmp.name) / "assets"
        self.v["persona"] = _persona()
        self.v["content_plan"] = {"content_items": [{} for _ in range(20)] + [_good_carousel()]}
        st.save(self.v)
        # generation.enabled=False is the entire point of this class (see its
        # docstring) -- pin it explicitly rather than reading whatever the
        # founder currently has toggled in the real production lock.
        _patch_charm_lock_with_fixture(self, generation_enabled=False)
        # Any attempt to reach the image provider from the dry-run path is a bug.
        for name in ("generate", "preflight"):
            original = getattr(local_image_provider, name)
            setattr(local_image_provider, name,
                    lambda *a, **k: self.fail("the dry run reached the image provider"))
            self.addCleanup(setattr, local_image_provider, name, original)

    def test_the_dry_run_emits_plan_prompt_references_and_qa_and_no_image(self):
        out = ps05_ops.generate_slide("20", "2", root=self.root)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["status"], "DRY_RUN")
        self.assertFalse(out["generated"])
        self.assertEqual((out["cost_usd"], out["cost_credits"]), (0, 0))
        self.assertFalse(out["generation_enabled"])
        self.assertFalse(out["paid_generation_authorized"])
        self.assertEqual(out["precedence_conflicts"], [])

        payload = json.loads(Path(out["dry_run_file"]).read_text())
        for key in ("story_plan", "identity_lock", "environment_lock", "story_state",
                    "approved_slide_refs", "composition_history", "generation_request",
                    "qa_result", "retry_state", "cost_state"):
            self.assertIn(key, payload)
        self.assertEqual((payload["story_id"], payload["slide_index"]), ("the_charm_001", 2))
        self.assertIsNone(payload["qa_result"])                    # nothing generated, nothing scored
        self.assertEqual(sorted(payload["qa_expectations"]), sorted(story_continuity.QA_DIMENSIONS))
        request = payload["generation_request"]
        self.assertIn("beauty mark on her anatomical LEFT cheek (viewer-right", request["prompt"])
        self.assertIn("dark charcoal bedding with one large charcoal pillow", request["prompt"])
        self.assertNotIn(request["overlay_copy"].lower(), request["prompt"].lower())
        self.assertTrue(request["negative_constraints"])
        self.assertEqual(request["output"]["filename"], "item20_slide2.png")
        self.assertEqual(payload["cost_state"]["this_call_cost_usd"], 0)
        # Not one pixel: every slide is still missing after a dry run.
        item = st.load()["content_plan"]["content_items"][20]
        self.assertEqual(carousel_handoff.missing_slides(item, 20, self.root),
                         [f"item20_slide{i}.png" for i in range(1, 6)])

    def test_a_stale_brief_is_reported_with_the_command_that_fixes_it(self):
        out = ps05_ops.generate_slide("20", "2", root=self.root)
        self.assertEqual(out["brief_sync"], "STALE")
        lock_path = story_continuity.STORY_LOCKS_DIR / "the_charm_001.json"
        applied = ps05_ops.apply_story("20", str(lock_path))       # story_plan -> brief fields
        self.assertTrue(applied["ok"], applied)
        self.assertEqual((applied["status"], applied["story_id"]),
                         ("STORY_REVISED", "the_charm_001"))
        item = st.load()["content_plan"]["content_items"][20]
        self.assertEqual(sorted(item["slides"][0]), ["beat", "composition", "continuity",
                                                     "role", "text_overlay"])
        self.assertEqual(ps05_ops.generate_slide("20", "2", root=self.root)["brief_sync"],
                         "IN_SYNC")

    def test_the_persisted_state_is_created_once_and_resumed_not_rebuilt(self):
        ps05_ops.generate_slide("20", "1", root=self.root)
        path = story_continuity.state_path(20, self.root)
        created = json.loads(path.read_text())["created_at"]
        state = json.loads(path.read_text())
        state["story_state"]["character_action"] = "carried across a restart"
        path.write_text(json.dumps(state))
        payload = json.loads(Path(ps05_ops.generate_slide("20", "2", root=self.root)
                                  ["dry_run_file"]).read_text())
        self.assertEqual(json.loads(path.read_text())["created_at"], created)
        self.assertEqual(payload["story_state"]["character_action"], "carried across a restart")

    def test_an_item_without_a_story_lock_keeps_its_existing_behaviour(self):
        self.assertIsNone(story_continuity.load_story_lock(0))
        for index, slot in (("21", "1"), ("0", "1"), ("20", "9")):
            with self.subTest(index=index, slot=slot):
                out = ps05_ops.generate_slide(index, slot, root=self.root)
                self.assertFalse(out["ok"])
                self.assertTrue(out["error"])


class AuthorizedLocalGenerationReachesProvider(_TmpState):
    """Founder 2026-09-26: authorizes the free local route. With generation.
    enabled explicitly True (an isolated fixture -- never the live production
    file), generate_slide must actually reach local_image_provider instead of
    the dry-run path -- proven through the real orchestrator entry point, not
    only a direct provider call. No real MFLUX/GPU work runs here: the
    provider call itself is mocked, since this test is about ROUTING, not
    about image quality."""

    def setUp(self):
        super().setUp()
        self.root = Path(self._tmp.name) / "assets"
        self.v["persona"] = _persona()
        self.v["content_plan"] = {"content_items": [{} for _ in range(20)] + [_good_carousel()]}
        st.save(self.v)
        # founder_approvals=False: slot 1 must have NOTHING approved yet for
        # this routing test -- a simulated (or real) slide-1 approval would
        # make generate_slide("20", "1", ...) refuse ALREADY_APPROVED before
        # ever reaching the provider, which is a different (also tested)
        # behaviour, not this one.
        _patch_charm_lock_with_fixture(self, generation_enabled=True, founder_approvals=False)

    def test_generate_slide_calls_the_local_provider_when_authorized_and_ready(self):
        calls = []

        def fake_generate(prompt, out_path, **kw):
            calls.append({"prompt": prompt, "out_path": out_path, **kw})
            from PIL import Image
            out_path.parent.mkdir(parents=True, exist_ok=True)
            _stub_photo((768, 960)).save(out_path)
            return {"ok": True, "status": "GENERATED", "path": str(out_path),
                    "width": 768, "height": 960, "model": local_image_provider.MODEL_ID,
                    "license": local_image_provider.MODEL_LICENSE, "route": "local",
                    "cost_usd": 0, "cost_credits": 0, "generated": True,
                    "reference_used": str(kw.get("reference"))}

        real_generate, real_preflight = local_image_provider.generate, local_image_provider.preflight
        local_image_provider.generate = fake_generate
        local_image_provider.preflight = lambda: {**real_preflight(), "ready": True, "status": "READY",
                                                  "model": local_image_provider.MODEL_ID,
                                                  "license": local_image_provider.MODEL_LICENSE}
        self.addCleanup(setattr, local_image_provider, "generate", real_generate)
        self.addCleanup(setattr, local_image_provider, "preflight", real_preflight)

        out = ps05_ops.generate_slide("20", "1", root=self.root)

        self.assertTrue(out["ok"], out)
        self.assertNotEqual(out["status"], "DRY_RUN")             # reached the real branch, not the dry run
        self.assertEqual(out["status"], "GENERATED")
        self.assertEqual(out["route"], "local")
        self.assertEqual((out["cost_usd"], out["cost_credits"]), (0, 0))
        self.assertEqual(len(calls), 1)                            # the orchestrator actually called it
        self.assertIn("25+", calls[0]["prompt"])                   # the compiled identity-locked prompt

    def _guard_provider_never_called(self):
        for name in ("generate", "preflight"):
            original = getattr(local_image_provider, name)
            setattr(local_image_provider, name,
                    lambda *a, **k: self.fail(f"local_image_provider.{name} must not be called here"))
            self.addCleanup(setattr, local_image_provider, name, original)

    def _approve_slide_one(self, lock, *, ref):
        # story_continuity.approve_slide() MUTATES `state` in place and
        # returns a small operation-summary dict (status/slide_ref/etc), NOT
        # the state itself -- reassigning `state = approve_slide(...)` (an
        # earlier version of this helper did exactly that) throws away the
        # real state and keeps only the summary, which looks like corruption
        # but is a caller mistake, not a bug in approve_slide(). Verified by
        # direct repro 2026-09-26: calling it correctly (below) round-trips
        # story_id/every field cleanly through save_state()/load_state().
        state = story_continuity.ensure_state(lock, 20, root=self.root)
        record = story_continuity.plan_record(lock, 1)
        story_continuity.approve_slide(state, record, slide_ref=ref, approved_by="test 2026-09-26")
        story_continuity.save_state(state, 20, root=self.root)

    def _approve(self, lock, slot, *, ref):
        state = story_continuity.ensure_state(lock, 20, root=self.root)
        record = story_continuity.plan_record(lock, slot)
        story_continuity.approve_slide(state, record, slide_ref=ref, approved_by="test 2026-09-26")
        story_continuity.save_state(state, 20, root=self.root)

    def test_wide_shot_slide_uses_the_ears_reference_not_the_face_closeup(self):
        # Real diagnosis 2026-09-26: slide 4 (the wide doorway/whole-room
        # shot) rendered with no fox ears because the only identity
        # reference it was ever given (GENERATION_SAFE_REFERENCE) is a
        # face-only closeup that excludes the ear region. Slide 4 must now
        # receive GENERATION_SAFE_REFERENCE_HEAD instead.
        lock = story_continuity.load_story_lock(20)
        for slot, ref in ((1, "item20_slide1.png"), (2, "item20_slide2.png"), (3, "item20_slide3.png")):
            self._approve(lock, slot, ref=ref)
        for f in ("item20_slide1.png", "item20_slide2.png", "item20_slide3.png"):
            _png(self.root / "carousel_item20" / f)

        calls = []

        def fake_generate(prompt, out_path, **kw):
            calls.append(kw.get("reference"))
            from PIL import Image
            out_path.parent.mkdir(parents=True, exist_ok=True)
            _stub_photo((768, 960)).save(out_path)
            return {"ok": True, "status": "GENERATED", "path": str(out_path), "width": 768,
                    "height": 960, "model": local_image_provider.MODEL_ID,
                    "license": local_image_provider.MODEL_LICENSE, "route": "local",
                    "cost_usd": 0, "cost_credits": 0, "generated": True,
                    "reference_used": str(kw.get("reference"))}

        real_generate, real_preflight = local_image_provider.generate, local_image_provider.preflight
        local_image_provider.generate = fake_generate
        local_image_provider.preflight = lambda: {**real_preflight(), "ready": True, "status": "READY",
                                                  "model": local_image_provider.MODEL_ID,
                                                  "license": local_image_provider.MODEL_LICENSE}
        self.addCleanup(setattr, local_image_provider, "generate", real_generate)
        self.addCleanup(setattr, local_image_provider, "preflight", real_preflight)

        out = ps05_ops.generate_slide("20", "4", root=self.root)
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(calls), 1)
        refs = calls[0]
        self.assertIsInstance(refs, list)
        self.assertIn(carousel_handoff.GENERATION_SAFE_REFERENCE_HEAD, refs)
        self.assertNotIn(carousel_handoff.GENERATION_SAFE_REFERENCE, refs)

    def test_hand_shot_slide_still_uses_the_face_closeup_not_the_ears_crop(self):
        lock = story_continuity.load_story_lock(20)
        self._approve(lock, 1, ref="item20_slide1.png")
        _png(self.root / "carousel_item20" / "item20_slide1.png")

        calls = []

        def fake_generate(prompt, out_path, **kw):
            calls.append(kw.get("reference"))
            from PIL import Image
            out_path.parent.mkdir(parents=True, exist_ok=True)
            _stub_photo((768, 960)).save(out_path)
            return {"ok": True, "status": "GENERATED", "path": str(out_path), "width": 768,
                    "height": 960, "model": local_image_provider.MODEL_ID,
                    "license": local_image_provider.MODEL_LICENSE, "route": "local",
                    "cost_usd": 0, "cost_credits": 0, "generated": True,
                    "reference_used": str(kw.get("reference"))}

        real_generate, real_preflight = local_image_provider.generate, local_image_provider.preflight
        local_image_provider.generate = fake_generate
        local_image_provider.preflight = lambda: {**real_preflight(), "ready": True, "status": "READY",
                                                  "model": local_image_provider.MODEL_ID,
                                                  "license": local_image_provider.MODEL_LICENSE}
        self.addCleanup(setattr, local_image_provider, "generate", real_generate)
        self.addCleanup(setattr, local_image_provider, "preflight", real_preflight)

        out = ps05_ops.generate_slide("20", "2", root=self.root)
        self.assertTrue(out["ok"], out)
        refs = calls[0]
        self.assertIn(carousel_handoff.GENERATION_SAFE_REFERENCE, refs)
        self.assertNotIn(carousel_handoff.GENERATION_SAFE_REFERENCE_HEAD, refs)

    def test_slide_three_with_slide_two_unapproved_never_invokes_the_provider(self):
        lock = story_continuity.load_story_lock(20)
        self._approve_slide_one(lock, ref="item20_slide1.png")     # slide 1 approved, slide 2 is not
        self._guard_provider_never_called()

        out = ps05_ops.generate_slide("20", "3", root=self.root)

        self.assertFalse(out["ok"], out)
        self.assertEqual(out["status"], "BLOCKED_NOT_READY")
        self.assertIn("slide 2", out["error"])
        self.assertEqual((out["cost_usd"], out["cost_credits"], out["generated"]), (0, 0, False))

    def test_an_approved_slide_one_cannot_be_overwritten(self):
        lock = story_continuity.load_story_lock(20)
        self._approve_slide_one(lock, ref="item20_slide1.png")
        self._guard_provider_never_called()

        out = ps05_ops.generate_slide("20", "1", root=self.root)

        self.assertFalse(out["ok"], out)
        self.assertEqual(out["status"], "ALREADY_APPROVED")
        self.assertIn("item20_slide1.png", out["error"])
        self.assertEqual((out["cost_usd"], out["cost_credits"], out["generated"]), (0, 0, False))


class IngestSlidesOpsCommand(_TmpState):
    """`python3 ps05_ops.py ingest-slides <item>` -- the PS-05 side of the
    ChatGPT-side worker route (founder 2026-09-23). Externally generated art
    enters the EXISTING lifecycle deterministically: all-or-nothing, magic-byte
    validated, 0 credits, and never a publish."""

    def setUp(self):
        super().setUp()
        self.root = Path(self._tmp.name) / "assets"
        self.item = _good_carousel()
        self.v["persona"] = _persona()
        self.v["content_plan"] = {"content_items": [self.item, {"target_platform": "tiktok"}]}
        st.save(self.v)

    def _deliver(self, slots=range(1, 6)):
        for i in slots:
            _png(carousel_handoff.package_dir(0, self.root) / f"item0_slide{i}.png")

    def test_a_partial_delivery_is_refused_and_names_only_the_missing_files(self):
        self._deliver(slots=(1, 2, 3))
        out = ps05_ops.ingest_slides("0", root=self.root)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "INCOMPLETE")
        self.assertEqual(out["missing_slides"], ["item0_slide4.png", "item0_slide5.png"])
        self.assertEqual(st.load()["content_plan"]["content_items"][0].get("lifecycle_status"), None)

    def test_a_file_with_the_right_name_but_wrong_bytes_is_not_accepted(self):
        self._deliver(slots=(1, 2, 3, 4))
        fake = carousel_handoff.package_dir(0, self.root) / "item0_slide5.png"
        fake.write_text("not an image")                     # right name, wrong bytes
        out = ps05_ops.ingest_slides("0", root=self.root)
        self.assertFalse(out["ok"])
        self.assertIn("item0_slide5.png", out["missing_slides"])

    def test_a_complete_delivery_reaches_asset_generated_at_zero_cost_and_persists(self):
        self._deliver()
        out = ps05_ops.ingest_slides("0", root=self.root)
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["status"], out["slides"]), ("ASSET_GENERATED", 5))
        self.assertEqual((out["cost_credits"], out["cost_usd"]), (0, 0))
        self.assertEqual(len(out["asset_refs"]), 5)
        self.assertEqual(out["overlay_slots"], [1, 3, 5])      # slides 2 and 4 stay wordless
        reloaded = st.load()["content_plan"]["content_items"][0]
        self.assertEqual(reloaded["lifecycle_status"], "ASSET_GENERATED")   # saved, not just in memory
        self.assertEqual(reloaded["carousel_generation"]["provider"], carousel_handoff.PROVIDER)

    def test_it_never_publishes_schedules_or_calls_a_paid_provider(self):
        self._deliver()
        called = []
        for name in ("content_director_generate", "content_director_choose_provider"):
            original = getattr(stages, name)
            self.addCleanup(setattr, stages, name, original)
            setattr(stages, name, lambda *a, _n=name, **kw: called.append(_n))
        out = ps05_ops.ingest_slides("0", root=self.root)
        self.assertTrue(out["ok"])
        self.assertEqual(called, [])
        reloaded = st.load()["content_plan"]["content_items"][0]
        for never in ("publish_config", "publish_attempt", "metricool_post_id"):
            self.assertNotIn(never, reloaded)

    def test_a_non_carousel_or_unknown_item_is_refused(self):
        for index in ("1", "9", "x"):
            with self.subTest(index=index):
                out = ps05_ops.ingest_slides(index, root=self.root)
                self.assertFalse(out["ok"])
                self.assertTrue(out["error"])

    def test_single_slide_rework_survives_on_both_sides_of_ingestion(self):
        # BEFORE ingest: a bad slide is simply re-delivered under the same name.
        self._deliver(slots=(1, 2, 4, 5))
        self.assertEqual(ps05_ops.ingest_slides("0", root=self.root)["missing_slides"],
                         ["item0_slide3.png"])
        self._deliver(slots=(3,))
        out = ps05_ops.ingest_slides("0", root=self.root)
        self.assertTrue(out["ok"], out)

        # AFTER ingest: replace_slide swaps one slot and returns it to QA,
        # keeping the other four -- the workflow this command must not break.
        v = st.load()
        fresh = self.root / "redraw.png"
        _png(fresh)
        carousel_handoff.replace_slide(v, 0, 3, fresh, root=self.root)
        st.save(v)
        item = st.load()["content_plan"]["content_items"][0]
        self.assertEqual(item["lifecycle_status"], "ASSET_GENERATED")
        self.assertEqual(item["carousel_generation"]["replacements"][0]["slot"], 3)
        self.assertEqual(len(item["asset_refs"]), 5)


class LocalProviderProcessGroupReliability(unittest.TestCase):
    """Two rounds of review, both against this same fix (2026-09-26):

    Round 1 found: a real steps=12 benchmark render exceeded
    GENERATION_TIMEOUT_S, and the OLD _run_studio (plain subprocess.run(...,
    timeout=...)) only killed studio.py's own python wrapper -- the
    mflux-generate-flux2[-edit] GRANDCHILD it spawns (the actual GPU work)
    was orphaned, kept running unlocked and untracked, and finished 10s
    later on its own, invisible to this code.

    Round 2 (this class) found the FIRST fix was still incomplete:
      1. proc.wait() only reaps the ONE pid this code spawned (the group
         leader) -- it returned as soon as the WRAPPER exited, without ever
         confirming a resistant GRANDCHILD had also stopped.
      2. os.getpgid(proc.pid), called AFTER the fact, can itself raise
         ProcessLookupError once the leader is gone even while a live
         descendant still shares that same pgid -- a false all-clear.
      3. Signalling the whole group at once does not, by itself, guarantee
         the flock's release matches real compute stopping: a plain
         SIGTERM's default disposition can kill the WRAPPER (releasing its
         own flock) before a slow/resistant grandchild dies. Closed at the
         ROOT in studio.py itself (see StudioWrapperNeverExitsBeforeItsOwnChild
         above): the wrapper now forwards SIGTERM to its own child and BLOCKS
         on it before ever exiting, so its flock cannot outlive its own
         compute. What remains this module's own job is honest, fail-closed
         verification -- never assuming the group is gone, always confirming
         it -- which is what _ensure_process_group_gone (pgid captured AT
         SPAWN, polls _process_group_is_empty, escalates SIGTERM -> SIGKILL,
         raises rather than lies if it still can't confirm death) now does.

    Every test here uses a FAKE wrapper script that mirrors studio.py's OWN
    real SIGTERM-forwarding fix (never the real studio.py/mflux, never any
    GPU work, never the real shared SSD installation), so escalation,
    resistant descendants, premature leader exit and exclusion-throughout-
    cleanup can all be proven deterministically in seconds, not real 232s+
    renders."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.pidfile = self.root / "pids.txt"
        self.wrapper = self.root / "fake_wrapper.py"
        self.wrapper.write_text('''
import fcntl, os, signal, subprocess, sys

mode = sys.argv[1]
pidfile = sys.argv[2]

_current_child = None
def _wait_for_child_then_exit(signum, frame):
    # Mirrors studio.py's own real 2026-09-26 fix: never exit (and so never
    # release whatever lock this process holds) before a child it started
    # has actually stopped, no matter what signal arrives.
    if _current_child is not None and _current_child.poll() is None:
        _current_child.terminate()
        _current_child.wait()
    sys.exit(128 + signum)
signal.signal(signal.SIGTERM, _wait_for_child_then_exit)

def _lock(path):
    lock = open(path, "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("EXCLUDED")
        sys.exit(5)
    return lock

def _spawn(resistant, heartbeat=None):
    # stdout/stderr=DEVNULL: matches the REAL studio.py, whose own
    # generation subprocess is redirected to a log FILE, never inherited
    # from studio.py's own stdout pipe. Without this, the grandchild here
    # would inherit THIS wrapper's stdout pipe (back to whatever spawned
    # it), and communicate() would then block on that pipe reaching EOF --
    # which only happens once the grandchild ALSO closes it -- confusing a
    # test of process lifetimes with an unrelated pipe-buffering artifact.
    code = "import signal, time\\n"
    if resistant:
        code += "signal.signal(signal.SIGTERM, signal.SIG_IGN)\\n"
    if heartbeat:
        # Proves "is the real work still going" via a FILE, not a pid --
        # debugged 2026-09-26: a contender-exclusion test spawns many
        # short-lived processes in a tight loop, and the OS reused a
        # just-freed pid for one of THOSE well before the test noticed the
        # ORIGINAL grandchild was gone (kill(pid,0)/ps -p succeed against
        # the new, unrelated process at that recycled pid) -- a pid-based
        # check cannot be made robust against this under that much process
        # churn; a heartbeat this specific process keeps writing while it
        # is genuinely still running has no such ambiguity.
        code += (f"end = time.time() + 300\\n"
                f"while time.time() < end:\\n"
                f"    open({heartbeat!r}, 'w').write(str(time.time()))\\n"
                f"    time.sleep(0.02)\\n")
    else:
        code += "time.sleep(300)"
    return subprocess.Popen([sys.executable, "-c", code],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _report(child):
    with open(pidfile, "w") as f:
        f.write(f"{os.getpid()} {child.pid}")

if mode == "quick_success":
    print("wrapper ok")
    sys.exit(0)
elif mode == "quick_fail":
    sys.stderr.write("wrapper failed on purpose\\n")
    sys.exit(3)
elif mode == "hang":
    _current_child = _spawn(resistant=False)
    _report(_current_child)
    _current_child.wait()
elif mode == "hang_resistant":
    _current_child = _spawn(resistant=True)
    _report(_current_child)
    _current_child.wait()
elif mode == "leader_exits_immediately_leaving_a_descendant":
    # No signal handler involvement at all -- this wrapper exits (with
    # whatever code sys.argv[3] names) WITHOUT tracking or waiting on what
    # it spawned. Models a wrapper bug distinct from timeout/cancellation:
    # by the time any cleanup code looks, the LEADER is already gone.
    child = _spawn(resistant=False)
    _report(child)
    sys.exit(int(sys.argv[3]))
elif mode == "hold_lock":
    lock = _lock(sys.argv[3])   # kept alive for the process's whole lifetime --
    _current_child = _spawn(resistant=False)   # an unassigned open() would be
    _report(_current_child)                    # refcounted to zero and closed
    _current_child.wait()                      # (releasing flock) immediately
elif mode == "hold_lock_resistant":
    lock = _lock(sys.argv[3])
    _current_child = _spawn(resistant=True, heartbeat=sys.argv[4])
    _report(_current_child)
    _current_child.wait()
elif mode == "hold_and_exit":
    lock = _lock(sys.argv[3])
    print("ACQUIRED")
    sys.exit(0)
''')

    def _cmd(self, mode, *extra):
        import sys
        return [sys.executable, str(self.wrapper), mode, str(self.pidfile), *extra]

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        # A ZOMBIE (dead, not yet reaped by its own parent) still responds
        # to kill(pid, 0) as present -- but the kernel releases ALL of its
        # resources, including any flock, at actual process termination;
        # reaping is bookkeeping for the exit status only, not resource
        # cleanup. Debugged 2026-09-26: a contender correctly acquiring a
        # lock the instant a resistant grandchild was SIGKILLed, while this
        # test's own driver thread hadn't yet called proc.wait() on the
        # zombie leader, looked like a false "premature admission" until
        # this distinction was made -- treat a zombie as dead here, since
        # that's what actually determines whether the lock is still held.
        # (This simple pid-based check is fine for the tests below that
        # check it only a handful of times; the contender-exclusion test,
        # which spawns MANY short-lived processes in a tight loop and risks
        # pid reuse colliding with these exact pids, uses a heartbeat FILE
        # instead of pid polling for that reason -- see its own comment.)
        import subprocess
        try:
            state = subprocess.run(["ps", "-o", "state=", "-p", str(pid)],
                                   capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return True
        return "Z" not in state

    def _read_pids(self) -> tuple[int, int]:
        import time
        deadline = time.time() + 5
        while not self.pidfile.exists() and time.time() < deadline:
            time.sleep(0.1)
        self.assertTrue(self.pidfile.exists(), "fake wrapper never reported its pids")
        wrapper_pid, child_pid = (int(x) for x in self.pidfile.read_text().split())
        return wrapper_pid, child_pid

    def _assert_eventually_dead(self, *pids: int) -> None:
        import time
        deadline = time.time() + 8
        while any(self._alive(p) for p in pids) and time.time() < deadline:
            time.sleep(0.2)
        for p in pids:
            self.assertFalse(self._alive(p), f"pid {p} should have been reaped")

    def test_normal_success_returns_the_real_stdout_and_returncode(self):
        res = local_image_provider._run_with_process_group(
            self._cmd("quick_success"), cwd=str(self.root), timeout=10)
        self.assertEqual(res.returncode, 0)
        self.assertIn("wrapper ok", res.stdout)

    def test_wrapper_failure_is_reported_without_hanging(self):
        res = local_image_provider._run_with_process_group(
            self._cmd("quick_fail"), cwd=str(self.root), timeout=10)
        self.assertEqual(res.returncode, 3)
        self.assertIn("wrapper failed on purpose", res.stdout)   # merged stderr

    def test_timeout_kills_the_whole_process_tree_not_just_the_wrapper(self):
        import subprocess
        with self.assertRaises(subprocess.TimeoutExpired):
            local_image_provider._run_with_process_group(
                self._cmd("hang"), cwd=str(self.root), timeout=2)
        wrapper_pid, child_pid = self._read_pids()
        self._assert_eventually_dead(wrapper_pid, child_pid)   # the original gap: child used to survive

    def test_cancellation_also_kills_the_whole_tree(self):
        # Simulates a caller-side cancellation (e.g. KeyboardInterrupt)
        # arriving mid-wait, distinct from a plain timeout -- proves the
        # cleanup is in a general exception handler, not a timeout special
        # case only.
        import subprocess
        import unittest.mock as mock

        def flaky_communicate(popen_self, *a, **kw):
            # Wait for the fake grandchild to actually be up before
            # "cancelling" -- a realistic cancellation interrupts a job
            # that's genuinely already running, not one still in interpreter
            # startup; this also avoids a race against this test's own
            # _read_pids() below. (Named popen_self, not self: this replaces
            # subprocess.Popen.communicate, so its first argument is the
            # Popen instance, not this TestCase -- self.pidfile below must
            # still reach the outer test method's `self`.)
            import time
            deadline = time.time() + 5
            while not self.pidfile.exists() and time.time() < deadline:
                time.sleep(0.05)
            raise KeyboardInterrupt("simulated cancellation")

        with mock.patch("subprocess.Popen.communicate", flaky_communicate):
            with self.assertRaises(KeyboardInterrupt):
                local_image_provider._run_with_process_group(
                    self._cmd("hang"), cwd=str(self.root), timeout=30)
        wrapper_pid, child_pid = self._read_pids()
        self._assert_eventually_dead(wrapper_pid, child_pid)

    def test_a_sigterm_ignoring_grandchild_requires_escalation_to_sigkill(self):
        # Review round 2's bug #1: proc.wait() alone would have returned as
        # soon as this fake wrapper's OWN handler got stuck forwarding to
        # (and blocking on) a grandchild that ignores SIGTERM entirely --
        # the wrapper itself never exits from SIGTERM alone here. Only
        # SIGKILL (unblockable, hits the whole group at once) can end it;
        # _ensure_process_group_gone must actually reach that escalation and
        # confirm BOTH are gone, not declare victory after the SIGTERM phase.
        import unittest.mock as mock
        import subprocess

        with mock.patch.object(local_image_provider, "PROCESS_KILL_GRACE_S", 1):
            with self.assertRaises(subprocess.TimeoutExpired):
                local_image_provider._run_with_process_group(
                    self._cmd("hang_resistant"), cwd=str(self.root), timeout=1)
            wrapper_pid, child_pid = self._read_pids()
            self._assert_eventually_dead(wrapper_pid, child_pid)

    def test_leader_already_exited_before_cleanup_still_finds_and_kills_its_descendant(self):
        # Review round 2's bug #2: os.getpgid(proc.pid), called AFTER the
        # leader already exited, can itself raise ProcessLookupError -- a
        # false all-clear that would leave this descendant running forever,
        # invisible, exactly like the original gap this whole fix targets.
        # The pgid used here is the one CAPTURED AT SPAWN inside
        # _run_with_process_group, never re-derived. Covers both framings
        # at once: the leader is already gone by the time cleanup runs, via
        # a perfectly normal (zero) exit code.
        res = local_image_provider._run_with_process_group(
            self._cmd("leader_exits_immediately_leaving_a_descendant", "0"),
            cwd=str(self.root), timeout=10)
        self.assertEqual(res.returncode, 0)   # the wrapper's own exit was totally unremarkable
        wrapper_pid, child_pid = self._read_pids()
        self._assert_eventually_dead(wrapper_pid, child_pid)   # yet its descendant is still reaped

    def test_leader_exits_nonzero_before_cleanup_still_finds_and_kills_its_descendant(self):
        # Same gap, via a nonzero wrapper exit instead of zero -- the
        # cleanup-verification path must not be conditioned on the wrapper's
        # returncode at all.
        res = local_image_provider._run_with_process_group(
            self._cmd("leader_exits_immediately_leaving_a_descendant", "3"),
            cwd=str(self.root), timeout=10)
        self.assertEqual(res.returncode, 3)
        wrapper_pid, child_pid = self._read_pids()
        self._assert_eventually_dead(wrapper_pid, child_pid)

    def test_a_contender_is_excluded_throughout_the_entire_cleanup_window_and_admitted_only_once_reaped(self):
        # Review round 2's bug #3, tested as directly as this module can:
        # the contender must be refused on EVERY attempt for as long as
        # job A's tree is genuinely alive -- including during the SIGTERM
        # grace period and the SIGKILL escalation, not just at one sampled
        # instant -- and admitted only once that whole tree is confirmed
        # gone. Uses a RESISTANT grandchild specifically so the full
        # escalation window is actually exercised, not skipped.
        #
        # This loops making contender attempts UNTIL one is finally
        # admitted, rather than trying to independently verify "is the tree
        # still alive" via a pid or a heartbeat file first. Both of those
        # were tried and DEBUGGED AWAY 2026-09-26, for two different but
        # equally real reasons:
        #   - pid-based liveness: this loop spawns 100+ short-lived
        #     processes; the OS reused a just-freed pid for one of THOSE
        #     well before a pid check noticed the ORIGINAL process was gone
        #     (kill/ps succeed against the new, unrelated process now
        #     holding that recycled pid).
        #   - heartbeat-file freshness: even a heartbeat written every 20ms
        #     is ambiguous right at the moment of death -- a file "written
        #     22ms ago" is indistinguishable from "the process that wrote it
        #     died 19ms ago", because its LAST write necessarily happened
        #     some finite time before death. No freshness threshold can
        #     resolve that near the boundary; tightening it only shrinks,
        #     never removes, the ambiguous window. (Verified directly: a
        #     real admission at age=0.022s, well within a supposedly-safe
        #     0.5s threshold, that was in fact perfectly correct -- SIGKILL
        #     had already landed.)
        # The contender's OWN returncode stream is the only ground truth
        # that isn't racy: EXCLUDED means the flock was genuinely still
        # held at that instant; ACQUIRED means it genuinely was not. Looping
        # until the first ACQUIRED, and asserting more than one EXCLUDED
        # came before it, directly proves exclusion held for a real,
        # multi-attempt span of the cleanup window without needing any
        # external liveness oracle at all.
        import threading
        import subprocess as sp
        import unittest.mock as mock

        lockfile = self.root / "generation.lock"
        heartbeat = self.root / "heartbeat.txt"   # not polled for freshness (see above) -- its mere
                                                  # existence still confirms a real grandchild ran
        outcome = {}

        def run_job_a():
            try:
                with mock.patch.object(local_image_provider, "PROCESS_KILL_GRACE_S", 2):
                    local_image_provider._run_with_process_group(
                        self._cmd("hold_lock_resistant", str(lockfile), str(heartbeat)),
                        cwd=str(self.root), timeout=1)
                outcome["a"] = "unexpected_success"
            except sp.TimeoutExpired:
                outcome["a"] = "timeout"

        t = threading.Thread(target=run_job_a)
        t.start()
        self._read_pids()   # blocks until job A has acquired the lock and spawned its grandchild

        attempts, excluded_count = 0, 0
        while True:
            res = local_image_provider._run_with_process_group(
                self._cmd("hold_and_exit", str(lockfile)), cwd=str(self.root), timeout=10)
            attempts += 1
            if res.returncode == 0:
                self.assertIn("ACQUIRED", res.stdout)
                break
            self.assertEqual(res.returncode, 5, res.stdout)
            self.assertIn("EXCLUDED", res.stdout)
            excluded_count += 1
            self.assertLess(attempts, 500, "never got admitted -- job A's cleanup may be stuck")

        t.join(timeout=15)
        self.assertEqual(outcome.get("a"), "timeout")
        self.assertGreater(excluded_count, 1, "test didn't actually span the cleanup window more than once")
        self.assertEqual(excluded_count, attempts - 1)   # every attempt before the successful one was refused
        self.assertTrue(heartbeat.exists())   # a real grandchild genuinely ran during that window

    def test_cleanup_reports_honestly_if_the_group_cannot_be_confirmed_dead(self):
        # Review round 2's explicit ask: "honest fail-closed cleanup
        # reporting" -- if even SIGKILL escalation can't make
        # _process_group_is_empty return True (simulated here by making the
        # emptiness check itself always report "still there"), this must
        # surface as a clear failure, never a silent, optimistic success.
        import unittest.mock as mock

        with mock.patch.object(local_image_provider, "_process_group_is_empty", return_value=False), \
             mock.patch.object(local_image_provider, "PROCESS_KILL_GRACE_S", 1):
            with self.assertRaises(RuntimeError) as ctx:
                local_image_provider._run_with_process_group(
                    self._cmd("quick_success"), cwd=str(self.root), timeout=10)
        self.assertIn("could not be confirmed fully terminated", str(ctx.exception))


class StudioLockSurvivesWrapperDeathViaFdInheritance(unittest.TestCase):
    """Codex review round 3 (2026-09-26): even with the SIGTERM-forwarding
    fix (StudioWrapperNeverExitsBeforeItsOwnChild below), the lock was still
    owned SOLELY by studio.py's own process -- a SIGKILL (which cannot be
    caught, so that forwarding handler never runs), a crash, or any other
    external force-kill of just the wrapper could still release the flock
    while the real generation child kept running. Timing-based tests
    (however many exclusion attempts observed) cannot PROVE this is
    impossible -- they can only fail to catch it, which round 2's own
    debugging already showed is unreliable to reason about from timing
    alone (see LocalProviderProcessGroupReliability's own docstring).

    Fixed with a DETERMINISTIC ownership mechanism instead: studio.py now
    passes its lock's file descriptor into the generation child via
    pass_fds. BSD flock (what fcntl.flock uses) is owned by the OPEN FILE
    DESCRIPTION, not by any one process -- as long as EITHER process still
    holds an open reference to that fd, the lock stays held, regardless of
    which one dies first or how.

    Proven here with NO timing races at all: a fake generation binary with
    an explicit READINESS HANDSHAKE (writes a .ready marker derived from its
    own --output path the instant it starts, then blocks until a .release
    marker appears) gives full, deterministic control over exactly when the
    'real work' starts and ends. The wrapper is killed -- ONLY the
    wrapper's own pid, never a group kill -- while the child is confirmed
    still running and responsive; the lock is proven still held; only
    after the child is explicitly released is admission proven. Never the
    real shared studio.py installation, never the real mflux binary, never
    any GPU work."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fake_root = Path(self._tmp.name) / "fake_studio"
        (self.fake_root / "runtime" / "venv" / "bin").mkdir(parents=True)
        shutil.copyfile(Path(local_image_provider.STUDIO_ROOT) / "studio.py",
                        self.fake_root / "studio.py")
        for sub in ("references", "jobs", "outputs"):
            (self.fake_root / "projects" / "test" / sub).mkdir(parents=True)
        fake_binary = self.fake_root / "runtime" / "venv" / "bin" / "mflux-generate-flux2"
        fake_binary.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, time\n"
            "from pathlib import Path\n"
            "output = Path(sys.argv[sys.argv.index('--output') + 1])\n"
            "ready = output.with_suffix('.ready')\n"
            "release = output.with_suffix('.release')\n"
            "ready.write_text('ready')\n"
            "deadline = time.time() + 60\n"
            "while not release.exists() and time.time() < deadline:\n"
            "    time.sleep(0.02)\n")
        fake_binary.chmod(0o755)

    @staticmethod
    def _flock_probe(lockfile: Path) -> bool:
        """True if the lock could be acquired (and is immediately released
        again) -- i.e. nobody else genuinely holds it right now. Uses the
        EXACT SAME primitive studio.py itself uses (fcntl.flock), so this
        is a direct test of the real lock, not a proxy for it."""
        import fcntl
        fh = open(lockfile, "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        finally:
            fh.close()   # releases the flock this probe itself just took, if it got it
        return True

    def _find_ready_and_release_paths(self) -> tuple[Path, Path]:
        import time
        deadline = time.time() + 5
        ready_files = []
        while time.time() < deadline and not ready_files:
            ready_files = list((self.fake_root / "projects" / "test" / "outputs").glob("*/01.ready"))
            time.sleep(0.02)
        self.assertTrue(ready_files, "the fake generation child never signalled readiness")
        return ready_files[0], ready_files[0].with_suffix(".release")

    def _run_the_scenario(self, kill_signal) -> None:
        import signal as signal_module
        import subprocess
        import sys
        import time

        manifest = self.fake_root / "projects" / "test" / "jobs" / f"job_{kill_signal}.json"
        manifest.write_text(json.dumps({"width": 768, "height": 960, "references": [],
                                        "slides": [{"prompt": "x", "seed": int(kill_signal)}]}))
        lockfile = self.fake_root / "runtime" / "generation.lock"

        wrapper = subprocess.Popen(
            [sys.executable, str(self.fake_root / "studio.py"), "--project", "test",
             "--manifest", str(manifest)],
            cwd=str(self.fake_root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(lambda: wrapper.poll() is None and wrapper.kill())

        _, release_file = self._find_ready_and_release_paths()

        # The lock is held (by the wrapper) while its child is confirmed
        # alive and responsive (it wrote .ready and is actively watching
        # for .release -- "can acknowledge a command").
        self.assertFalse(self._flock_probe(lockfile), "lock should be held while the wrapper is alive")

        # Kill ONLY the wrapper's own pid -- never a group kill, never
        # anything touching the child. Models a wrapper crash or an
        # external supervisor force-killing it: neither SIGKILL (unblockable)
        # nor an uncaught crash can be intercepted by the wrapper's own
        # SIGTERM-forwarding handler, so this is exactly the scenario that
        # fix alone cannot cover.
        os.kill(wrapper.pid, kill_signal)
        deadline = time.time() + 5
        while wrapper.poll() is None and time.time() < deadline:
            time.sleep(0.02)
        self.assertIsNotNone(wrapper.poll(), "the wrapper should be dead now")

        # THE deterministic proof: the lock is STILL held, purely because
        # the child (deliberately never touched) still holds its own
        # reference to the same open file description.
        self.assertFalse(self._flock_probe(lockfile),
                        "the lock released when the wrapper died, even though its child (the real "
                        "compute) is still genuinely running -- fd inheritance is not protecting it")

        # Release the child on our own terms (this test's explicit control,
        # not a signal) and confirm the lock becomes available -- and only
        # now.
        release_file.write_text("release")
        deadline = time.time() + 5
        admitted = False
        while time.time() < deadline:
            if self._flock_probe(lockfile):
                admitted = True
                break
            time.sleep(0.02)
        self.assertTrue(admitted, "the lock never became available after the child was released")

    def test_lock_survives_sigkill_to_the_wrapper_while_its_child_is_confirmed_alive(self):
        import signal
        self._run_the_scenario(signal.SIGKILL)

    def test_lock_survives_sigint_to_the_wrapper_too(self):
        # Covers the "spawn/signal window" more broadly than SIGKILL alone
        # -- e.g. a Ctrl-C landing on just the wrapper in a terminal
        # session. studio.py installs no SIGINT handler, so this is the
        # same "uncaught, immediate wrapper death" class of failure.
        import signal
        self._run_the_scenario(signal.SIGINT)


class StudioWrapperNeverExitsBeforeItsOwnChild(unittest.TestCase):
    """Codex-review-flagged gap (2026-09-26): 'signalling a group
    simultaneously does not guarantee lock lifetime: wrapper can exit and
    release its flock before a slow/resistant GPU child.' Root cause: the
    OLD studio.py ran its generation subprocess via subprocess.run(...,
    check=True) with no signal handler -- a plain SIGTERM's default
    disposition kills studio.py's own process immediately, releasing the
    flock its own fd holds, regardless of whether the child it just started
    has actually stopped.

    Fixed in studio.py itself (not just in local_image_provider.py's own
    cleanup): a SIGTERM handler now forwards the signal to (and BLOCKS on)
    whatever generation subprocess is currently running before this process
    is allowed to exit -- so the flock can never release before the real
    work it represents has actually stopped, no matter what killed it.

    Tested here via an ISOLATED copy of studio.py (never the real shared
    installation) with a FAKE mflux-generate-flux2 stand-in that ignores
    SIGTERM on purpose -- proving the wrapper stays alive (and therefore the
    flock stays held) for as long as that resistant child does, and only
    exits once the child is actually gone (here: force-killed directly, the
    same escalation local_image_provider's own driver performs on top of
    this)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fake_root = Path(self._tmp.name) / "fake_studio"
        (self.fake_root / "runtime" / "venv" / "bin").mkdir(parents=True)
        shutil.copyfile(Path(local_image_provider.STUDIO_ROOT) / "studio.py",
                        self.fake_root / "studio.py")
        for sub in ("references", "jobs", "outputs"):
            (self.fake_root / "projects" / "test" / sub).mkdir(parents=True)
        self.marker = self.fake_root / "child_actually_died.marker"
        fake_binary = self.fake_root / "runtime" / "venv" / "bin" / "mflux-generate-flux2"
        fake_binary.write_text(
            "#!/usr/bin/env python3\n"
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"   # the resistant grandchild
            "time.sleep(30)\n"
            f"open({str(self.marker)!r}, 'w').close()\n")
        fake_binary.chmod(0o755)

    def test_wrapper_stays_alive_while_its_sigterm_ignoring_child_does_and_exits_once_it_is_gone(self):
        import signal
        import subprocess
        import sys
        import time

        manifest = self.fake_root / "projects" / "test" / "jobs" / "job.json"
        manifest.write_text(json.dumps({"width": 768, "height": 960, "references": [],
                                        "slides": [{"prompt": "x", "seed": 1}]}))
        proc = subprocess.Popen(
            [sys.executable, str(self.fake_root / "studio.py"), "--project", "test",
             "--manifest", str(manifest)],
            cwd=str(self.fake_root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True)   # its own process group -- matches real production usage via
                                      # local_image_provider._run_with_process_group, and makes the
                                      # killpg below safe: it can never reach this TEST RUNNER's own
                                      # group, only the isolated group this one job just created.
        self.addCleanup(lambda: proc.poll() is None and os.killpg(proc.pid, signal.SIGKILL))

        deadline = time.time() + 5
        log_files = []
        while time.time() < deadline and not log_files:
            log_files = list((self.fake_root / "projects" / "test" / "outputs").glob("*/01.log"))
            time.sleep(0.05)
        self.assertTrue(log_files, "generation never started")

        os.kill(proc.pid, signal.SIGTERM)
        time.sleep(1.0)
        # THE gap this closes: the old wrapper would already be dead here --
        # default SIGTERM disposition kills a plain subprocess.run() call
        # near-instantly. The fixed wrapper is still alive, blocked inside
        # its own handler's wait() for the resistant child.
        self.assertIsNone(proc.poll(),
                          "the wrapper exited before its SIGTERM-ignoring child did -- the flock it "
                          "holds would have released while real work was still running")
        self.assertFalse(self.marker.exists())   # the fake child genuinely hasn't died either

        # Escalate exactly as local_image_provider's own _kill_process_tree
        # does: SIGKILL cannot be ignored, so this ends both together. pgid
        # is proc.pid directly (start_new_session guarantees that), never
        # re-derived via getpgid() -- see that function's own docstring for
        # why re-deriving it later is unreliable once the leader is gone.
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        self.assertIsNotNone(proc.returncode)


class StudioRunnerStepsAndNegativePromptSchema(unittest.TestCase):
    """studio.py (the external SSD runner at local_image_provider.STUDIO_ROOT,
    shared with an unrelated project -- see local_image_provider's module
    docstring) is not part of this repo, so it is tested here through an
    ISOLATED COPY under this test's own tmpdir -- never the real, shared
    installation at "/Volumes/Media Library/Local Image Studio". Every
    assertion runs studio.py's own --dry-run (prints the constructed CLI
    command; generates nothing, touches no GPU, takes no lock) against a
    disposable project folder, so this proves the ACTUAL script's behaviour,
    not a reimplementation of it.

    2026-09-26: added manifest-level steps/negative_prompt so this shared
    runner's already-installed --negative-prompt/--steps flags (documented
    in its own runtime/generate-help.txt and runtime/edit-help.txt, but never
    wired to anything before) are usable. Proven backward-compatible here: a
    manifest that doesn't name either field must still produce the exact
    prior hardcoded command (--steps 4, no --negative-prompt), so the OTHER
    project sharing this installation is unaffected."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fake_root = Path(self._tmp.name) / "fake_studio"
        self.fake_root.mkdir(parents=True)
        real_studio_py = Path(local_image_provider.STUDIO_ROOT) / "studio.py"
        shutil.copyfile(real_studio_py, self.fake_root / "studio.py")
        for sub in ("references", "jobs", "outputs"):
            (self.fake_root / "projects" / "test" / sub).mkdir(parents=True)

    def _dry_run(self, manifest: dict) -> dict:
        import subprocess
        import sys
        manifest_path = self.fake_root / "projects" / "test" / "jobs" / "job.json"
        manifest_path.write_text(json.dumps(manifest))
        res = subprocess.run(
            [sys.executable, str(self.fake_root / "studio.py"), "--project", "test",
             "--manifest", str(manifest_path), "--dry-run"],
            cwd=str(self.fake_root), capture_output=True, text=True, timeout=15)
        return {"returncode": res.returncode, "stdout": res.stdout, "stderr": res.stderr}

    def test_a_manifest_without_the_new_fields_produces_the_exact_prior_command(self):
        out = self._dry_run({"width": 768, "height": 960, "references": [],
                             "slides": [{"prompt": "a plain prompt", "seed": 42}]})
        self.assertEqual(out["returncode"], 0, out)
        cmd = json.loads(out["stdout"])["commands"][0]
        self.assertEqual(cmd[cmd.index("--steps") + 1], "4")
        self.assertNotIn("--negative-prompt", cmd)

    def test_manifest_level_steps_and_negative_prompt_reach_the_real_cli_command(self):
        out = self._dry_run({"width": 768, "height": 960, "references": [],
                             "steps": 12, "negative_prompt": "malformed hands, extra fingers",
                             "slides": [{"prompt": "a plain prompt", "seed": 42}]})
        self.assertEqual(out["returncode"], 0, out)
        cmd = json.loads(out["stdout"])["commands"][0]
        self.assertEqual(cmd[cmd.index("--steps") + 1], "12")
        self.assertEqual(cmd[cmd.index("--negative-prompt") + 1], "malformed hands, extra fingers")

    def test_per_slide_steps_overrides_the_job_level_default(self):
        out = self._dry_run({"width": 768, "height": 960, "references": [], "steps": 12,
                             "slides": [{"prompt": "a plain prompt", "seed": 42, "steps": 6}]})
        self.assertEqual(out["returncode"], 0, out)
        cmd = json.loads(out["stdout"])["commands"][0]
        self.assertEqual(cmd[cmd.index("--steps") + 1], "6")

    def test_an_out_of_range_steps_value_is_refused_by_the_real_script(self):
        out = self._dry_run({"width": 768, "height": 960, "references": [], "steps": 999,
                             "slides": [{"prompt": "a plain prompt", "seed": 42}]})
        self.assertNotEqual(out["returncode"], 0)
        self.assertIn("Steps must be an integer between 1 and 50", out["stderr"])

    def test_a_non_string_negative_prompt_is_refused_by_the_real_script(self):
        out = self._dry_run({"width": 768, "height": 960, "references": [], "negative_prompt": 5,
                             "slides": [{"prompt": "a plain prompt", "seed": 42}]})
        self.assertNotEqual(out["returncode"], 0)
        self.assertIn("negative_prompt must be a string", out["stderr"])


class StructuralDegenerateImageCheckIsNotAVisionCheck(unittest.TestCase):
    """Founder direction 2026-09-27: build repeatable automatic checks
    without lowering the quality bar or claiming false passes. Assessed
    what genuine image-quality inspection this local stack can actually run
    (numpy + PIL only -- no cv2/dlib/face_recognition/mediapipe/skimage are
    installed here, confirmed by import probe, and none may be newly
    installed this turn): NONE of that is semantic vision. pixel_std/
    is_degenerate_image is a STRUCTURAL sanity floor only -- it catches a
    genuinely blank/near-solid-colour output (a real, distinct failure
    mode PIL's own verify()+dimension check cannot see), and nothing more.

    The held-out benchmark proving this honestly: every REAL image this
    story has ever produced -- founder-APPROVED slide 1, founder-REJECTED
    slides 2-4 (malformed hands, missing ears), and the two identity/ears/
    hands benchmark samples -- all measure well above the floor. A
    malformed-hands image and a clean one are INDISTINGUISHABLE to this
    check, on purpose: it must never claim to assess what it cannot."""

    REAL_IMAGES = [
        "generated_assets/carousel_item20/item20_slide1.png",              # founder-approved
        "generated_assets/carousel_item20/item20_slide2.png",              # founder-rejected (hands)
        "generated_assets/carousel_item20/item20_slide3.png",              # founder-rejected (hands)
        "generated_assets/carousel_item20/item20_slide4.png",              # founder-rejected (ears)
        "generated_assets/carousel_item20/benchmarks/"
        "nyx_identity_ears_hands_benchmark_2026-09-27/pose1_holding_charm_up.png",
        "generated_assets/carousel_item20/benchmarks/"
        "nyx_identity_ears_hands_benchmark_2026-09-27/pose2_cupping_charm.png",
    ]

    def test_no_vision_library_is_installed_here(self):
        # Documents the actual assessed capability, not an assumption --
        # re-checked directly rather than asserted from memory, since this
        # is the fact the whole design decision rests on.
        for mod in ("cv2", "face_recognition", "dlib", "mediapipe", "skimage"):
            with self.assertRaises(ImportError):
                __import__(mod)

    def test_every_real_image_this_story_has_produced_passes_the_structural_floor(self):
        # Good AND bad (semantically) real images all pass -- proving this
        # check does not, and cannot, distinguish quality at that level.
        for rel in self.REAL_IMAGES:
            path = Path(rel)
            if not path.is_file():
                self.skipTest(f"{rel} not present in this checkout")
            self.assertFalse(local_image_provider.is_degenerate_image(path),
                            f"{rel} measured std {local_image_provider.pixel_std(path):.1f}, "
                            f"below the floor -- should never happen for a real photo")

    def test_a_genuinely_blank_render_is_caught(self):
        from PIL import Image
        blank = Path(self._tmp_path()) / "blank.png"
        Image.new("RGB", (768, 960), color=(40, 40, 45)).save(blank)
        self.assertTrue(local_image_provider.is_degenerate_image(blank))

    def test_ordinary_photographic_noise_is_not_flagged(self):
        import numpy as np
        from PIL import Image
        rng = np.random.default_rng(0)
        arr = rng.integers(0, 255, size=(960, 768, 3), dtype=np.uint8)
        noisy = Path(self._tmp_path()) / "noisy.png"
        Image.fromarray(arr).save(noisy)
        self.assertFalse(local_image_provider.is_degenerate_image(noisy))

    def _tmp_path(self) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name


class LocalStillProviderPreflight(_TmpState):
    """The free-first still route (founder 2026-09-22): FLUX.2 klein 4B,
    Apache-2.0, run locally at zero incremental cost. Preflight-first -- it
    must measure and refuse rather than download weights, install packages or
    reach a paid provider when the machine or setup is not ready."""

    def setUp(self):
        super().setUp()
        self.v["persona"] = _persona()
        self.v["content_plan"] = {"content_items": [_good_carousel(), {"target_platform": "tiktok"}]}
        st.save(self.v)
        self.root = Path(self._tmp.name) / "assets"
        # Isolate STUDIO_ROOT to a scratch dir under THIS test's own tmpdir --
        # a focused test must never write references/jobs/outputs into the
        # real SSD installation.
        self.studio_root = Path(self._tmp.name) / "fake_studio"
        self.studio_root.mkdir(parents=True, exist_ok=True)
        self._orig = (local_image_provider.hardware, local_image_provider.ssd_mounted,
                      local_image_provider.missing_modules, local_image_provider.weights_present,
                      local_image_provider.STUDIO_ROOT)

        def restore():
            (local_image_provider.hardware, local_image_provider.ssd_mounted,
             local_image_provider.missing_modules, local_image_provider.weights_present,
             local_image_provider.STUDIO_ROOT) = self._orig
        self.addCleanup(restore)
        local_image_provider.STUDIO_ROOT = self.studio_root

    def _fake(self, *, ram=64.0, disk=500.0, mounted=True, missing=(), weights=True):
        local_image_provider.hardware = lambda: {
            "chip": "Apple M-series", "arch": "arm64", "os": "Darwin 25.5.0",
            "ram_gb": ram, "free_disk_gb": disk, "apple_silicon": True}
        local_image_provider.ssd_mounted = lambda: mounted
        local_image_provider.missing_modules = lambda: list(missing)
        local_image_provider.weights_present = lambda: weights

    def test_the_licence_is_the_reason_this_model_was_chosen(self):
        self.assertEqual(local_image_provider.MODEL_LICENSE, "Apache-2.0")
        self.assertIn("FLUX.2-klein-4B", local_image_provider.MODEL_ID)
        self.assertEqual(local_image_provider.COST_CLASS, "zero_incremental_cost")

    def test_thin_hardware_blocks_and_missing_setup_is_itemised(self):
        self._fake(ram=8.0, disk=1.0)
        p = local_image_provider.preflight()
        self.assertFalse(p["ready"])
        self.assertEqual(p["status"], "HARDWARE_BLOCKED")
        self.assertTrue(any("RAM" in b for b in p["hardware_blockers"]))
        self.assertTrue(any("disk" in b for b in p["hardware_blockers"]))

        self._fake(mounted=False)
        p = local_image_provider.preflight()
        self.assertFalse(p["ready"])
        self.assertEqual(p["status"], "HARDWARE_BLOCKED")
        self.assertTrue(any("SSD" in b for b in p["hardware_blockers"]))

        self._fake(missing=("runtime binary missing: mflux-generate-flux2",), weights=False)
        p = local_image_provider.preflight()
        self.assertEqual(p["status"], "SETUP_REQUIRED")
        self.assertEqual(p["hardware_blockers"], [])
        self.assertEqual(len(p["setup_required"]), 2)      # runtime binary + weights, itemised

        self._fake()
        self.assertTrue(local_image_provider.preflight()["ready"])

    def test_generate_slide_refuses_without_generating_or_spending(self):
        self._fake(mounted=False)
        out = ps05_ops.generate_slide("0", "1", root=self.root if hasattr(self, "root") else None)
        self.assertFalse(out["ok"])
        self.assertFalse(out["generated"])
        self.assertEqual((out["cost_usd"], out["cost_credits"]), (0, 0))
        self.assertEqual(out["status"], "REQUEST_EMITTED")   # request, never a fake image
        self.assertEqual(out["local_status"], "HARDWARE_BLOCKED")
        self.assertTrue(out["hardware_blockers"])

    def test_a_ready_machine_generates_the_slide_at_zero_cost(self):
        import subprocess
        import unittest.mock as mock
        from PIL import Image

        self._fake()
        seen = {}

        def fake_runner(project, manifest_path):
            manifest = json.loads(Path(manifest_path).read_text())
            seen["prompt"] = manifest["slides"][0]["prompt"]
            seen["references"] = manifest["references"]
            job_dir = local_image_provider.STUDIO_ROOT / "projects" / project / "outputs" / "fakejob"
            job_dir.mkdir(parents=True, exist_ok=True)
            # A REAL, correctly-sized PNG -- generate() now opens and measures
            # the produced file before it will report success, so a magic-
            # byte-only stub (the shared _png() helper) is not enough here.
            _stub_photo((768, 960)).save(job_dir / "01.png")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        def fake_dry_run(*a, **kw):
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps({"job": "fakejob"}), stderr="")

        real_generate = local_image_provider.generate
        orig_run = subprocess.run

        def routed_run(cmd, **kw):
            if "--dry-run" in cmd:
                return fake_dry_run()
            return orig_run(cmd, **kw)

        with mock.patch("local_image_provider.subprocess.run", side_effect=routed_run):
            local_image_provider.generate = lambda prompt, out, **kw: real_generate(
                prompt, out, **{**kw, "runner": fake_runner})
            self.addCleanup(lambda: setattr(local_image_provider, "generate", real_generate))
            out = ps05_ops.generate_slide("0", "1", root=self.root)

        self.assertTrue(out["ok"], out)
        self.assertEqual((out["cost_usd"], out["cost_credits"]), (0, 0))
        self.assertEqual(out["status"], "GENERATED")
        self.assertEqual(out["dimensions"], "768x960")          # this installation's tested default
        self.assertIn("25+", seen["prompt"])                    # identity lock, from the package
        # Content-addressed: the manifest names the reference by its hash, not
        # its bare filename, so two different images can never collide under
        # the same basename.
        self.assertEqual(len(seen["references"]), 1)
        self.assertTrue(seen["references"][0].endswith(carousel_handoff.GENERATION_SAFE_REFERENCE.name))
        self.assertRegex(seen["references"][0], r"^[0-9a-f]{16}_")
        copied = local_image_provider.STUDIO_ROOT / "projects" / "nyx" / "references" / seen["references"][0]
        self.assertEqual(copied.read_bytes(), carousel_handoff.GENERATION_SAFE_REFERENCE.read_bytes())
        self.assertTrue(out["slide_readable"])                  # passes the ingestion magic-byte check
        self.assertEqual(len(out["slides_still_missing"]), 4)   # only slide 1 exists
        self.assertEqual(out["route"], "local")

    def test_a_bad_project_slug_is_refused_before_any_filesystem_write(self):
        self._fake()
        res = local_image_provider.generate("prompt", self.root / "out.png",
                                            project="../escape")
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "INVALID_PROJECT")
        self.assertEqual(list(self.studio_root.glob("projects/**/*")), [])  # nothing written

    def test_an_out_of_range_steps_value_is_refused_before_any_filesystem_write(self):
        # 2026-09-26: studio.py's own --steps validation range is 1-50; this
        # client-side check fails the SAME way, but before any manifest/
        # reference is ever written, matching this file's existing
        # validate-before-write pattern for project slugs and dimensions.
        self._fake()
        for bad in (0, 51, "4"):
            res = local_image_provider.generate("prompt", self.root / "out.png", steps=bad)
            self.assertFalse(res["ok"], res)
            self.assertEqual(res["status"], "INVALID_STEPS")
        self.assertEqual(list(self.studio_root.glob("projects/**/*")), [])

    def test_a_non_string_negative_prompt_is_refused_before_any_filesystem_write(self):
        self._fake()
        res = local_image_provider.generate("prompt", self.root / "out.png", negative_prompt=123)
        self.assertFalse(res["ok"], res)
        self.assertEqual(res["status"], "INVALID_NEGATIVE_PROMPT")
        self.assertEqual(list(self.studio_root.glob("projects/**/*")), [])

    def test_default_steps_is_written_into_the_manifest_and_negative_prompt_defaults_off(self):
        # 2026-09-26: studio.py now forwards a manifest-level negative_prompt/
        # steps to the real mflux CLI (previously --steps was hardcoded to 4
        # and no negative prompt was ever sent). generate()'s steps defaults
        # to DEFAULT_STEPS (4, the only verified value) -- proven here via the
        # manifest actually written to disk. negative_prompt defaults to None:
        # a real benchmark render against this exact model (see generated_
        # assets/carousel_item20/benchmarks/local_engine_improvement_2026-09-
        # 26/) found FLUX.2 Klein 4B rejects --negative-prompt outright at
        # runtime, despite it appearing in --help -- so this provider must
        # NOT send one by default, or every real generation call would fail.
        import subprocess
        import unittest.mock as mock
        from PIL import Image

        self._fake()
        seen = {}

        def fake_runner(project, manifest_path):
            manifest = json.loads(Path(manifest_path).read_text())
            seen["slide"] = manifest["slides"][0]
            job_dir = local_image_provider.STUDIO_ROOT / "projects" / project / "outputs" / "fakejob"
            job_dir.mkdir(parents=True, exist_ok=True)
            _stub_photo((768, 960)).save(job_dir / "01.png")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        def fake_dry_run(*a, **kw):
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps({"job": "fakejob"}), stderr="")

        orig_run = subprocess.run

        def routed_run(cmd, **kw):
            if "--dry-run" in cmd:
                return fake_dry_run()
            return orig_run(cmd, **kw)

        with mock.patch("local_image_provider.subprocess.run", side_effect=routed_run):
            out = local_image_provider.generate("a benchmark prompt", self.root / "out.png",
                                                runner=fake_runner)

        self.assertTrue(out["ok"], out)
        self.assertIsNone(local_image_provider.DEFAULT_NEGATIVE_PROMPT)   # the safe default for THIS model
        self.assertEqual(seen["slide"]["steps"], local_image_provider.DEFAULT_STEPS)
        self.assertNotIn("negative_prompt", seen["slide"])   # never sent unless the caller opts in
        self.assertEqual(out["steps"], local_image_provider.DEFAULT_STEPS)
        self.assertIsNone(out["negative_prompt"])

    def test_a_caller_can_override_steps_and_negative_prompt_is_never_written_when_absent(self):
        import subprocess
        import unittest.mock as mock
        from PIL import Image

        self._fake()
        seen = {}

        def fake_runner(project, manifest_path):
            manifest = json.loads(Path(manifest_path).read_text())
            seen["slide"] = manifest["slides"][0]
            job_dir = local_image_provider.STUDIO_ROOT / "projects" / project / "outputs" / "fakejob"
            job_dir.mkdir(parents=True, exist_ok=True)
            _stub_photo((768, 960)).save(job_dir / "01.png")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        def fake_dry_run(*a, **kw):
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps({"job": "fakejob"}), stderr="")

        orig_run = subprocess.run

        def routed_run(cmd, **kw):
            if "--dry-run" in cmd:
                return fake_dry_run()
            return orig_run(cmd, **kw)

        with mock.patch("local_image_provider.subprocess.run", side_effect=routed_run):
            out = local_image_provider.generate("a benchmark prompt", self.root / "out.png",
                                                steps=8, runner=fake_runner)

        self.assertTrue(out["ok"], out)
        self.assertEqual(seen["slide"]["steps"], 8)
        self.assertNotIn("negative_prompt", seen["slide"])
        self.assertIsNone(out["negative_prompt"])

    def test_a_truthy_negative_prompt_is_refused_before_any_subprocess_is_launched(self):
        # Reviewer-flagged (2026-09-26): FLUX.2 Klein 4B rejects
        # --negative-prompt at real runtime (exit code 2). Merely defaulting
        # to None was not enough -- a caller passing ANY truthy value
        # (including UNVALIDATED_NEGATIVE_PROMPT_TEXT, kept only as
        # reference text for a possible future non-FLUX.2 config) must be
        # refused HERE, before studio.py or the mflux CLI is ever reached,
        # never merely fail late at the real binary.
        self._fake()

        def must_not_be_called(project, manifest_path):
            self.fail("the runner must never be reached for an unsupported negative_prompt")

        for bad in ("anything", local_image_provider.UNVALIDATED_NEGATIVE_PROMPT_TEXT):
            res = local_image_provider.generate("prompt", self.root / "out.png",
                                                negative_prompt=bad, runner=must_not_be_called)
            self.assertFalse(res["ok"], res)
            self.assertEqual(res["status"], "NEGATIVE_PROMPT_UNSUPPORTED")
        self.assertEqual(list(self.studio_root.glob("projects/**/*")), [])   # nothing written either

    def test_two_same_named_references_with_different_content_never_collide(self):
        a = self.root / "shared_name.png"
        b = self.root / "other" / "shared_name.png"
        b.parent.mkdir(parents=True)
        from PIL import Image
        Image.new("RGB", (4, 4), color=(1, 2, 3)).save(a)
        Image.new("RGB", (4, 4), color=(9, 8, 7)).save(b)
        name_a = local_image_provider._content_addressed_name(a)
        name_b = local_image_provider._content_addressed_name(b)
        self.assertNotEqual(name_a, name_b)                  # same basename, different content
        self.assertTrue(name_a.endswith("shared_name.png"))
        self.assertTrue(name_b.endswith("shared_name.png"))

    def test_an_unavailable_local_provider_emits_one_job_request_per_slide(self):
        """When local generation cannot run, the slide request is still emitted
        so the authorized external worker has something to consume -- five
        requests, ONE job id, each carrying the previous slide's continuity."""
        self._fake(ram=8.0, disk=10.0)
        job_ids, requests = set(), []
        for slot in range(1, 6):
            out = ps05_ops.generate_slide("0", str(slot), root=self.root)
            self.assertEqual(out["status"], "REQUEST_EMITTED")
            self.assertFalse(out["generated"])
            self.assertEqual((out["cost_usd"], out["cost_credits"]), (0, 0))
            job_ids.add(out["job_id"])
            requests.append(json.loads(Path(out["request_file"]).read_text()))

        self.assertEqual(len(job_ids), 1)                   # one job, not five
        self.assertEqual([r["slide_index"] for r in requests], [1, 2, 3, 4, 5])
        self.assertIsNone(requests[0]["carry_forward_from"])
        for i, r in enumerate(requests[1:], start=2):
            self.assertEqual(r["carry_forward_from"]["slide_index"], i - 1)
        for i, r in enumerate(requests, start=1):
            self.assertEqual(r["output"]["filename"], f"item0_slide{i}.png")
            self.assertEqual((r["output"]["width"], r["output"]["height"]), (1080, 1350))
            self.assertIn("none", r["text_in_image"])       # clean art, no baked-in overlay
            self.assertTrue(r["identity_reference"]["project_path"])
            self.assertTrue(r["cost"]["paid_providers_prohibited"])
        self.assertEqual(carousel_handoff.missing_slides(self.v["content_plan"]["content_items"][0],
                                                         0, self.root),
                         [f"item0_slide{i}.png" for i in range(1, 6)])   # no image was faked

    def test_bad_targets_are_refused_before_any_preflight(self):
        self._fake()
        for index, slot in (("1", "1"), ("9", "1"), ("x", "1"), ("0", "99")):
            with self.subTest(index=index, slot=slot):
                out = ps05_ops.generate_slide(index, slot)
                self.assertFalse(out["ok"])
                self.assertTrue(out["error"])


class LocalPromptAdaptation(unittest.TestCase):
    """FLUX.2 Klein 4B (small, distilled) drifted to face-forward portraits
    on two real content_items[20] slide-2 candidates (2026-09-26) despite the
    full ~2000-word layered compiler prompt explicitly asking for hands/an
    object in the foreground -- a compact, shot-first prompt fixed it (real
    renders, reviewed). This is a COMPRESSION of the same facts, not a
    lossless restatement, and the hands/object reinforcement must be scoped
    to records that are ACTUALLY about hands -- never applied blanket."""

    def setUp(self):
        self.lock = _charm_lock(generation_enabled=True)
        self.state = story_continuity.new_state(self.lock)
        # slide 1 approved so the environment master is real (LOCKED), the
        # way every slot > 1 test below needs it.
        self.state["approved_slide_refs"] = [{"slide_index": 1, "base": "package",
                                              "value": "item20_slide1.png",
                                              "approved_at": "2026-09-26T00:00:00+00:00",
                                              "approved_by": "test", "simulated": False}]
        self.state["environment_lock"] = {**self.state["environment_lock"], "status": "LOCKED",
                                          "source_slide": 1, "source_slide_ref": "item20_slide1.png",
                                          "locked_at": "2026-09-26T00:00:00+00:00", "simulated": False}

    def test_hands_reinforcement_is_scoped_to_records_that_are_actually_about_hands(self):
        # Real content: slide 1 is face-forward, slide 2/3/5 are hand/palm
        # shots, slide 4 is a wide room shot. "handheld" (a camera-technique
        # word in slide 1's OWN composition text) must never false-positive.
        expect_hand_shot = {1: False, 2: True, 3: True, 4: False, 5: True}
        for slot, expected in expect_hand_shot.items():
            with self.subTest(slot=slot):
                record = story_continuity.plan_record(self.lock, slot)
                out = local_image_provider.adapt_prompt_for_local(
                    record, self.state, has_environment_reference=(slot > 1))
                self.assertEqual(out["is_hand_shot"], expected, out["prompt"])
                if expected:
                    self.assertIn("HER OWN hands ONLY", out["prompt"])
                else:
                    self.assertNotIn("main subject of this photo", out["prompt"])

    def test_no_environment_anchor_is_arbitrarily_truncated(self):
        record = story_continuity.plan_record(self.lock, 2)
        real_anchor_count = len(self.state["environment_lock"]["anchors"])
        self.assertEqual(real_anchor_count, 10)          # this story's real, verified anchor count
        out = local_image_provider.adapt_prompt_for_local(record, self.state,
                                                          has_environment_reference=True)
        self.assertEqual(out["environment_anchors_used"], real_anchor_count)
        for anchor in self.state["environment_lock"]["anchors"]:
            self.assertIn(anchor["description"], out["prompt"])

    def test_first_slide_without_a_room_reference_still_gets_every_room_anchor(self):
        """The slide that FREEZES the room has no room reference photo yet, so the
        anchors are the only thing standing between the model and an invented room --
        the real cause of the wrong-room renders on items 30 and 31 (2026-09-30)."""
        record = story_continuity.plan_record(self.lock, 1)
        out = local_image_provider.adapt_prompt_for_local(record, self.state,
                                                          has_environment_reference=False)
        self.assertEqual(out["environment_anchors_used"],
                         len(self.state["environment_lock"]["anchors"]))
        for anchor in self.state["environment_lock"]["anchors"]:
            self.assertIn(anchor["description"], out["prompt"])
        self.assertIn("never invent another room", out["prompt"])
        # and it must not claim a room photo it does not have
        self.assertNotIn("Room reference photo attached", out["prompt"])

    def test_the_critical_no_human_ears_rule_reaches_the_model(self):
        """canonical_ears_visible is a CRITICAL QA dimension, so the model has to be
        told the rule -- it was previously dropped in compression and cost content_items[31]
        slide 1 a critical failure it was never given a chance to avoid (2026-09-30)."""
        for slot in (1, 2):
            with self.subTest(slot=slot):
                record = story_continuity.plan_record(self.lock, slot)
                out = local_image_provider.adapt_prompt_for_local(
                    record, self.state, has_environment_reference=(slot > 1))
                self.assertIn("EXACTLY TWO EARS TOTAL", out["prompt"])
                self.assertIn("no human ear visible", out["prompt"].lower())

    def test_spent_attempts_block_generating_but_never_block_judging_a_candidate(self):
        """The ceiling stops the system re-rolling on its own; it must not stop a good
        image from the founder-authorized external fallback being approved once local
        attempts are spent (item31 slide 3, 2026-09-30). Slide-order still blocks."""
        record = story_continuity.plan_record(self.lock, 2)
        state = json.loads(json.dumps(self.state))
        state.setdefault("retry_state", {})["attempts"] = {"2": 99}
        gate = story_continuity.readiness(state, record)
        self.assertFalse(gate["ready"])
        self.assertEqual([b for b in gate["blockers"] if "has used all" not in b], [])
        out = story_continuity.qa_slide(self.lock, state, record, index=20, scores=None)
        self.assertNotEqual(out["status"], "BLOCKED_NOT_READY")   # ceiling alone never blocks QA

        state["approved_slide_refs"] = []          # slide order IS still a hard blocker
        blocked = story_continuity.qa_slide(self.lock, state, record, index=20, scores=None)
        self.assertEqual(blocked["status"], "BLOCKED_NOT_READY")

    def test_reconcile_story_state_breaks_the_post_revocation_deadlock(self):
        """revoke_approval leaves story_state pointing at a revoked slide, which makes
        _staleness_reason block every slide of the story -- including the one that must be
        approved first. Observed live on item30 (2026-09-30). reconcile_story_state rewinds
        the basis to the last still-approved slide, mirroring approve_slide's own replay."""
        lock = self.lock
        state = json.loads(json.dumps(self.state))
        state["story_state"] = {**state.get("story_state", {}), "updated_from_slide": 5}
        state["approved_slide_refs"] = [{"slide_index": 1, "base": "package", "value": "item20_slide1.png",
                                         "approved_at": "2026-09-26T00:00:00+00:00",
                                         "approved_by": "test", "simulated": False}]
        self.assertIsNotNone(story_continuity._staleness_reason(state))      # deadlocked
        out = story_continuity.reconcile_story_state(state, lock)
        self.assertEqual(out["basis_now"], 1)
        self.assertEqual(out["rewound_from_slide"], 5)
        self.assertIsNone(story_continuity._staleness_reason(state))         # unblocked
        # it must not resurrect or alter any approval
        self.assertEqual([r["slide_index"] for r in state["approved_slide_refs"]], [1])
        # and the rewound state matches replaying slide 1's own state_after
        expected = {**(lock.get("initial_story_state") or {}),
                    **(story_continuity.plan_record(lock, 1).get("state_after") or {})}
        for k, v in expected.items():
            self.assertEqual(state["story_state"][k], v)

    def test_compression_is_labelled_not_claimed_lossless(self):
        record = story_continuity.plan_record(self.lock, 2)
        out = local_image_provider.adapt_prompt_for_local(record, self.state,
                                                          has_environment_reference=True)
        self.assertIn("compression", out["compression_note"].lower())
        # The adapted prompt is deliberately much shorter than the full
        # compiler output -- if it were the same length it would just be a
        # worse copy of compile_prompt(), not an adaptation.
        full = story_continuity.compile_prompt(_persona(), self.state, record)["prompt"]
        self.assertLess(len(out["prompt"].split()), len(full.split()) // 2)

    def test_wide_shot_detection_matches_the_real_slide_that_dropped_ears(self):
        # Real content: only slide 4 (the doorway/whole-room shot) is wide --
        # this is the exact slide whose real render (2026-09-26) had no fox
        # ears at all. "handheld" (slide 1's own composition word) must not
        # false-positive on "wide" style detection either.
        expect_wide = {1: False, 2: False, 3: False, 4: True, 5: False}
        for slot, expected in expect_wide.items():
            with self.subTest(slot=slot):
                record = story_continuity.plan_record(self.lock, slot)
                self.assertEqual(local_image_provider.is_wide_or_full_body_shot(record), expected)

    def test_generation_safe_reference_head_actually_differs_and_shows_ears(self):
        # GENERATION_SAFE_REFERENCE (face closeup) and _HEAD (ears crop) must
        # be two distinct, real files -- not the same path silently reused.
        self.assertNotEqual(carousel_handoff.GENERATION_SAFE_REFERENCE,
                            carousel_handoff.GENERATION_SAFE_REFERENCE_HEAD)
        self.assertTrue(carousel_handoff.GENERATION_SAFE_REFERENCE_HEAD.is_file())
        self.assertIn("fox_ears", carousel_handoff.GENERATION_SAFE_REFERENCE_HEAD.name)

    def test_canonical_visible_traits_are_restated_in_every_adapted_prompt(self):
        # Not just "match the photo": the ears/tail/eyes/beauty-mark line is
        # present regardless of shot type or which reference crop is used,
        # as a second, independent (text) anchor for traits an image
        # reference might not depict at every framing.
        for slot in range(1, 6):
            with self.subTest(slot=slot):
                record = story_continuity.plan_record(self.lock, slot)
                out = local_image_provider.adapt_prompt_for_local(
                    record, self.state, has_environment_reference=(slot > 1))
                self.assertIn("fox ears on top of her head", out["prompt"])
                self.assertIn("anatomical LEFT cheek", out["prompt"])

    def test_forbidden_repetition_is_restated_as_an_explicit_negative(self):
        # Real production defect (content_items[24], 2026-09-27): three
        # independent local renders all converged on the same reclined,
        # over-the-shoulder pose despite each slide's positive composition
        # text asking for something else -- forbidden_repetition was never
        # actually reaching this small model's prompt at all. Slide 1 has no
        # forbidden_repetition (nothing precedes it); slides 2-5 do.
        record1 = story_continuity.plan_record(self.lock, 1)
        out1 = local_image_provider.adapt_prompt_for_local(
            record1, self.state, has_environment_reference=False)
        self.assertNotIn("Do NOT repeat these earlier shots", out1["prompt"])
        for slot in range(2, 6):
            with self.subTest(slot=slot):
                record = story_continuity.plan_record(self.lock, slot)
                out = local_image_provider.adapt_prompt_for_local(
                    record, self.state, has_environment_reference=True)
                self.assertIn("Do NOT repeat these earlier shots", out["prompt"])
                for forbidden in record["forbidden_repetition"]:
                    self.assertIn(forbidden, out["prompt"])


class LocalGenerationUsesAdaptedPromptWithFullPromptAudited(_TmpState):
    """The normal generate_slide route must send the ADAPTED prompt to the
    local provider (not the full 2000-word compiled one), while still saving
    the full compiled prompt alongside it for audit -- proven through the
    real orchestrator entry point with the provider mocked (no real render:
    this is about wiring, not image quality)."""

    def setUp(self):
        super().setUp()
        self.root = Path(self._tmp.name) / "assets"
        self.v["persona"] = _persona()
        self.v["content_plan"] = {"content_items": [{} for _ in range(20)] + [_good_carousel()]}
        st.save(self.v)
        _patch_charm_lock_with_fixture(self, generation_enabled=True, founder_approvals=False)

    def test_generate_slide_sends_the_adapted_prompt_and_writes_a_full_prompt_audit(self):
        calls = []

        def fake_generate(prompt, out_path, **kw):
            calls.append(prompt)
            from PIL import Image
            out_path.parent.mkdir(parents=True, exist_ok=True)
            _stub_photo((768, 960)).save(out_path)
            return {"ok": True, "status": "GENERATED", "path": str(out_path),
                    "width": 768, "height": 960, "model": local_image_provider.MODEL_ID,
                    "license": local_image_provider.MODEL_LICENSE, "route": "local",
                    "cost_usd": 0, "cost_credits": 0, "generated": True,
                    "reference_used": str(kw.get("reference"))}

        real_generate, real_preflight = local_image_provider.generate, local_image_provider.preflight
        local_image_provider.generate = fake_generate
        local_image_provider.preflight = lambda: {**real_preflight(), "ready": True, "status": "READY",
                                                  "model": local_image_provider.MODEL_ID,
                                                  "license": local_image_provider.MODEL_LICENSE}
        self.addCleanup(setattr, local_image_provider, "generate", real_generate)
        self.addCleanup(setattr, local_image_provider, "preflight", real_preflight)

        out = ps05_ops.generate_slide("20", "1", root=self.root)

        self.assertTrue(out["ok"], out)
        self.assertEqual(len(calls), 1)
        sent_prompt = calls[0]
        self.assertIn("MOST IMPORTANT INSTRUCTION", sent_prompt)     # the adapted prompt...
        self.assertLess(len(sent_prompt.split()), 500)                # ...short, not ~2000 words

        audit_path = Path(out["prompt_audit_file"])
        self.assertTrue(audit_path.is_file())
        audit = json.loads(audit_path.read_text())
        self.assertEqual(audit["adapted_prompt_used"], sent_prompt)
        self.assertIn("25+", audit["full_compiled_prompt"])           # the full compiler's own text
        self.assertGreater(len(audit["full_compiled_prompt"].split()), len(sent_prompt.split()))

    def test_a_correction_retry_uses_a_different_seed_than_the_attempt_it_replaces(self):
        """A rejected slide re-rendered byte-identically because generate() always got
        its default seed (verified on content_items[32] slide 1: attempt 2 had attempt 1's
        SHA1). A retry that cannot differ can only reproduce the failure it was meant to
        correct, and it still burns one of the two attempts the founder rule allows."""
        seeds = []

        def fake_generate(prompt, out_path, **kw):
            seeds.append(kw.get("seed"))
            out_path.parent.mkdir(parents=True, exist_ok=True)
            _stub_photo((768, 960)).save(out_path)
            return {"ok": True, "status": "GENERATED", "path": str(out_path), "width": 768,
                    "height": 960, "model": local_image_provider.MODEL_ID,
                    "license": local_image_provider.MODEL_LICENSE, "route": "local",
                    "cost_usd": 0, "cost_credits": 0, "generated": True,
                    "reference_used": str(kw.get("reference"))}

        real_generate, real_preflight = local_image_provider.generate, local_image_provider.preflight
        local_image_provider.generate = fake_generate
        local_image_provider.preflight = lambda: {**real_preflight(), "ready": True, "status": "READY"}
        self.addCleanup(setattr, local_image_provider, "generate", real_generate)
        self.addCleanup(setattr, local_image_provider, "preflight", real_preflight)

        ps05_ops.generate_slide("20", "1", root=self.root)
        lock = story_continuity.load_story_lock(20)
        state = story_continuity.ensure_state(lock, 20, root=self.root)
        state.setdefault("retry_state", {}).setdefault("attempts", {})["1"] = 1   # one rejection recorded
        story_continuity.save_state(state, 20, root=self.root)
        ps05_ops.generate_slide("20", "1", root=self.root)

        self.assertEqual(len(seeds), 2)
        self.assertNotEqual(seeds[0], seeds[1], "a correction retry must not repeat the same seed")


class MetricoolConnectionsOpsCommand(unittest.TestCase):
    """`python3 ps05_ops.py connections` -- the read-only probe that answers the
    Threads/X question from Metricool's own payload. It must call exactly one
    tool, pre-approve only that tool, and never guess a network's key name."""

    def test_reports_connected_networks_and_pre_approves_only_the_read_tool(self):
        seen = {}

        def fake(tool, args, *, allowed):
            seen.update(tool=tool, args=args, allowed=allowed)
            return {"ok": True, "data": {"data": [{
                "id": nyx_instagram.NYX_BRAND_ID, "label": "nyx.emberfall",
                "timezone": "Australia/Melbourne",
                "networksData": {"instagramData": "nyx.emberfall", "tiktokData": "nyx.emberfall1",
                                 "threadsData": "", "facebookData": None}}]}}

        out = ps05_ops.metricool_connections(caller=fake)
        self.assertEqual(seen["tool"], nyx_instagram.TOOL_BRAND)
        self.assertEqual(seen["allowed"], (nyx_instagram.TOOL_BRAND,))   # read tool only
        self.assertEqual(seen["args"], {})
        self.assertEqual(out["brands"][0]["connected"], ["instagramData", "tiktokData"])
        self.assertFalse(out["threads_connected"])                       # empty handle != connected

    def test_a_connected_threads_profile_is_reported_and_a_failure_is_not_faked(self):
        def connected(tool, args, *, allowed):
            return {"ok": True, "data": {"data": [{
                "id": nyx_instagram.NYX_BRAND_ID,
                "networksData": {"instagramData": "nyx.emberfall", "threadsData": "nyx.emberfall"}}]}}

        self.assertTrue(ps05_ops.metricool_connections(caller=connected)["threads_connected"])
        denied = ps05_ops.metricool_connections(
            caller=lambda *a, **k: {"ok": False, "status": "GATED", "detail": "not approved"})
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["status"], "GATED")
        self.assertNotIn("threads_connected", denied)     # no verdict without real data


class CarouselPackageOpsCommand(_TmpState):
    """`python3 ps05_ops.py carousel-package <index>` -- the sanctioned way to
    rebuild one carousel's handoff package without a provider, spend or publish
    path (founder 2026-09-22: routine zero-spend repairs must be doable by the
    system itself, inside the existing boundaries)."""

    def setUp(self):
        super().setUp()
        self.root = Path(self._tmp.name) / "assets"
        self.v["persona"] = _persona()
        self.v["content_plan"] = {"content_items": [_good_carousel(), {"target_platform": "tiktok"}]}
        st.save(self.v)

    def test_rebuilds_the_package_in_the_current_format_and_lists_every_missing_slide(self):
        out = ps05_ops.carousel_package("0", root=self.root)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["package_version"], 4)               # not the stale pre-relocatable 3
        self.assertEqual((out["provider"], out["incremental_cost"], out["cost_credits"]),
                         (carousel_handoff.PROVIDER, "none", 0))
        self.assertEqual(out["missing_slides"],
                         [f"item0_slide{i}.png" for i in range(1, 6)])
        self.assertEqual(out["slides_expected"], 5)
        written = json.loads(Path(out["package_path"]).read_text())
        self.assertEqual(written["ingestion"]["filenames"], out["missing_slides"])
        _png(carousel_handoff.package_dir(0, self.root) / "item0_slide1.png")
        self.assertEqual(ps05_ops.carousel_package("0", root=self.root)["missing_slides"],
                         [f"item0_slide{i}.png" for i in range(2, 6)])

    def test_never_touches_the_item_lifecycle_or_the_paid_pipeline(self):
        paid = _good_carousel(**{stages.CAROUSEL_PAID_APPROVAL_FIELD: True})
        self.v["content_plan"]["content_items"].append(paid)
        st.save(self.v)
        refused = [ps05_ops.carousel_package("2", root=self.root),   # founder-approved PAID item
                   ps05_ops.carousel_package("1", root=self.root),   # not a carousel
                   ps05_ops.carousel_package("9", root=self.root),   # no such item
                   ps05_ops.carousel_package("x", root=self.root)]   # not an index
        for out in refused:
            with self.subTest(error=out.get("error")):
                self.assertFalse(out["ok"])
                self.assertTrue(out["error"])
        ps05_ops.carousel_package("0", root=self.root)
        reloaded = st.load()["content_plan"]["content_items"][0]
        self.assertNotIn("lifecycle_status", reloaded)             # read-only towards the item
        self.assertNotIn("asset_refs", reloaded)


class TickSkipsWhileRelayHoldsWriteLock(unittest.TestCase):
    """Founder decision 2026-09-21: while the relay holds the PS-05 write lock,
    the hourly tick must run NO PS-05 logic, record `skipped: relay_write_lock`,
    queue no catch-up, and run normally again once the lock is released.

    Each case runs the real ps05_tick.py as a subprocess (exactly as the
    scheduler does) in a temp directory where every other PS-05 module it
    imports is a stub that leaves a trace -- so "ran" and "did not run" are
    observed, not inferred. The live state/ and the live relay lock are never
    touched."""

    STUBS = ("audit", "commercial_events", "fan_memory", "fanvue_auth", "fanvue_chat",
             "fanvue_media", "instagram_metrics", "manager", "nyx_instagram", "stages", "state")

    def setUp(self):
        import os
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        here = Path(__file__).resolve().parent
        for name in ("ps05_tick.py", "relay_lock_guard.py", "scheduler_heartbeat.py"):
            shutil.copy(here / name, self.root / name)
        for mod in self.STUBS:
            (self.root / f"{mod}.py").write_text(
                "from pathlib import Path\n"
                "with open(Path(__file__).parent / 'ran.log', 'a') as f:\n"
                f"    f.write('{mod}\\n')\n"
                "raise SystemExit(0)\n")
        self.lock = self.root / "locks" / "ps05.lock"
        self.lock.parent.mkdir()
        self.env = {**os.environ, "PS05_RELAY_LOCK_PATH": str(self.lock),
                    "PYTHONDONTWRITEBYTECODE": "1"}

    def tearDown(self):
        self._tmp.cleanup()

    def _hold_lock(self, pid=None):
        import os
        self.lock.write_text(json.dumps({"task_id": "task_x", "pid": pid or os.getpid(),
                                         "started_at": "2026-09-21T12:00:00.000Z",
                                         "project_id": "ps05"}))

    def _tick(self):
        import subprocess
        import sys
        return subprocess.run([sys.executable, "ps05_tick.py"], cwd=self.root, env=self.env,
                              capture_output=True, text=True, timeout=60)

    def _ran(self):
        log = self.root / "ran.log"
        return log.read_text().split() if log.exists() else []

    def _heartbeat(self):
        return json.loads((self.root / "state" / "scheduler_heartbeat.json").read_text())

    def test_a_no_lock_tick_runs(self):
        r = self._tick()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._ran(), ["audit"])  # reached PS-05 application code
        self.assertFalse((self.root / "state" / "scheduler_heartbeat.json").exists())

    def test_b_lock_held_runs_no_ps05_logic_and_records_skip(self):
        self._hold_lock()
        r = self._tick()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._ran(), [])  # not one PS-05 module imported
        self.assertEqual(json.loads(r.stdout)["skipped"], "relay_write_lock")
        hb = self._heartbeat()
        self.assertEqual(hb["last_tick_outcome"], "skipped")
        self.assertEqual(hb["last_skip_reason"], "relay_write_lock")
        self.assertTrue(hb["last_tick_summary"].startswith("skipped: relay_write_lock"))
        self.assertIn("task_x", hb["last_tick_summary"])
        self.assertFalse(hb["tick_in_progress"])
        self.assertTrue(self.lock.exists())  # the guard never touches the relay's lock

    def test_b2_skip_is_neither_success_nor_failure(self):
        (self.root / "state").mkdir()
        (self.root / "state" / "scheduler_heartbeat.json").write_text(json.dumps(
            {"last_successful_tick": "2026-09-21T11:03:19+00:00", "consecutive_failures": 2}))
        self._hold_lock()
        self._tick()
        hb = self._heartbeat()
        self.assertEqual(hb["last_successful_tick"], "2026-09-21T11:03:19+00:00")
        self.assertEqual(hb["consecutive_failures"], 2)

    def test_b3_unreadable_lock_is_treated_as_held(self):
        self.lock.write_text("{half-writ")
        self._tick()
        self.assertEqual(self._ran(), [])
        self.assertEqual(self._heartbeat()["last_skip_reason"], "relay_write_lock")

    def test_b4_stale_lock_of_a_dead_process_does_not_block(self):
        import subprocess
        import sys
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self._hold_lock(pid=dead.pid)
        self._tick()
        self.assertEqual(self._ran(), ["audit"])
        self.assertTrue(self.lock.exists())  # still read-only: reclaiming is the relay's job

    def test_c_lock_released_next_tick_runs(self):
        self._hold_lock()
        self._tick()
        self.assertEqual(self._ran(), [])
        self.lock.unlink()
        self._tick()
        self.assertEqual(self._ran(), ["audit"])

    def test_d_no_duplicate_catch_up(self):
        self._hold_lock()
        for _ in range(3):  # three skipped hours
            self._tick()
        before = self._heartbeat()
        self.lock.unlink()
        self._tick()  # one scheduled tick after release
        self.assertEqual(self._ran(), ["audit"])  # ran exactly once, not once per skipped hour
        self.assertEqual(set(before) - {"last_attempted_tick", "tick_in_progress", "last_tick_outcome",
                                        "last_tick_summary", "last_tick_duration_s",
                                        "last_skip_reason"}, set())  # nothing queued

    def test_importing_the_tick_module_is_never_gated(self):
        # test suites and tools import ps05_tick; only the scheduled run is gated.
        self._hold_lock()
        import subprocess
        import sys
        r = subprocess.run([sys.executable, "-c", "import ps05_tick"], cwd=self.root, env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._ran(), ["audit"])
        self.assertFalse((self.root / "state").exists())


class TickSkipsWhileRelayWriteIsUnresolved(TickSkipsWhileRelayHoldsWriteLock):
    """Founder decision 2026-09-21 (post-#70 hardening): a relay write task that
    PAUSES or FAILS releases the lock but can leave its own uncommitted edits.
    The relay then keeps a durable marker (data/locks/ps05.unresolved/<task>.json,
    not a lock, no pid); the tick must keep skipping -- `skipped:
    relay_paused_write` -- while any listed path still differs from HEAD, and run
    normally the moment those edits are committed or restored. No catch-up.

    Same harness as the lock tests (real ps05_tick.py, stub PS-05 modules), in
    a temp git repo with its own marker directory."""

    def setUp(self):
        import subprocess
        super().setUp()
        self.markers = self.root / "locks" / "ps05.unresolved"
        self.env["PS05_RELAY_UNRESOLVED_DIR"] = str(self.markers)
        g = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        self._git = lambda *a: subprocess.run([*g, *a], cwd=self.root, check=True,
                                              capture_output=True, text=True)
        (self.root / ".gitignore").write_text("state/\nlocks/\nran.log\n")
        (self.root / "feature.py").write_text("X = 1\n")
        self._git("init", "-q")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "base")

    def _marker(self, state="unresolved", paths=("feature.py",), name="task_p", issue=71,
                stop_reason="founder_input"):
        self.markers.mkdir(parents=True, exist_ok=True)
        (self.markers / f"{name}.json").write_text(json.dumps({
            "schema": "relay-unresolved-write-v1", "project_id": "ps05", "task_id": name,
            "issue": issue, "state": state, "status": "needs_input", "stop_reason": stop_reason,
            "paths": list(paths), "footprint": {}}))

    def test_a_active_write_lock_skips(self):
        self._hold_lock()
        self._marker(state="running")
        self._tick()
        self.assertEqual(self._ran(), [])
        self.assertEqual(self._heartbeat()["last_skip_reason"], "relay_write_lock")

    def test_b_paused_write_with_dirty_task_edits_skips(self):
        (self.root / "feature.py").write_text("X = 2  # half done\n")
        self._marker()
        r = self._tick()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._ran(), [])  # not one PS-05 module imported
        out = json.loads(r.stdout)
        self.assertEqual(out["skipped"], "relay_paused_write")
        self.assertEqual(out["unresolved"][0]["paths"], ["feature.py"])
        hb = self._heartbeat()
        self.assertEqual(hb["last_skip_reason"], "relay_paused_write")
        self.assertIn("issue #71", hb["last_tick_summary"])
        self.assertIn("feature.py", hb["last_tick_summary"])
        self.assertTrue((self.markers / "task_p.json").exists())  # read-only towards markers

    def test_b2_new_untracked_task_file_skips(self):
        (self.root / "new_module.py").write_text("Y = 1\n")
        self._marker(paths=("new_module.py",))
        self._tick()
        self.assertEqual(self._ran(), [])

    def test_c_paused_write_with_no_edits_runs(self):
        self._marker()  # listed path identical to HEAD
        self._tick()
        self.assertEqual(self._ran(), ["audit"])

    def test_d_resumed_and_committed_clears_and_runs(self):
        (self.root / "feature.py").write_text("X = 2\n")
        self._marker()
        self._tick()
        self.assertEqual(self._ran(), [])
        self._git("commit", "-qam", "relay continuation commit")
        self._tick()
        self.assertEqual(self._ran(), ["audit"])  # exactly one run, no catch-up

    def test_e_failed_task_restored_cleanly_runs(self):
        (self.root / "feature.py").write_text("X = broken(\n")
        (self.root / "junk.py").write_text("Z\n")
        self._marker(paths=("feature.py", "junk.py"), stop_reason="error")
        self._tick()
        self.assertEqual(self._ran(), [])
        self._git("checkout", "-q", "--", "feature.py")
        self._tick()
        self.assertEqual(self._ran(), [])  # junk.py still there
        (self.root / "junk.py").unlink()
        self._tick()
        self.assertEqual(self._ran(), ["audit"])

    def test_f_interrupted_write_skips_even_with_a_dead_lock(self):
        import subprocess
        import sys
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self._hold_lock(pid=dead.pid)
        self._marker(state="running", paths=())
        self._tick()
        self.assertEqual(self._ran(), [])
        self.assertEqual(self._heartbeat()["last_skip_reason"], "relay_paused_write")

    def test_unreadable_marker_skips(self):
        self.markers.mkdir(parents=True)
        (self.markers / "task_x.json").write_text("{half")
        self._tick()
        self.assertEqual(self._ran(), [])

    def test_git_failure_is_treated_as_unsafe(self):
        import shutil as _sh
        (self.root / "feature.py").write_text("X = 2\n")
        self._marker()
        _sh.rmtree(self.root / ".git")  # git cannot tell -> fail safe
        self._tick()
        self.assertEqual(self._ran(), [])


if __name__ == "__main__":
    unittest.main()


class HiggsfieldPaidSlideRoute(_TmpState):
    """`generate-slide 20 1 --provider higgsfield [--confirm-spend]` (founder
    2026-09-24: GPT Image 2 through the existing Higgsfield connector). Every
    gate fails closed before anything is submitted, the charge recorded is the
    account's own balance delta, and the output is a CANDIDATE that stays
    PENDING_VISUAL_QA. A slide grant never opens the autonomous paid path."""

    def setUp(self):
        super().setUp()
        import higgsfield_image_provider as hip
        self.hip = hip
        self.root = Path(self._tmp.name) / "assets"
        self.v["persona"] = _persona()
        self.v["content_plan"] = {"content_items": [{} for _ in range(20)] + [_good_carousel()]}
        self.v["credit_budget"].update(session_limit=40.12, spent_this_session=39.74,
                                       spent_total=39.74)
        st.save(self.v)
        self.calls = []

    # -- fakes -----------------------------------------------------------------
    def _png_3x4(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (1536, 2048), (90, 60, 50)).save(buf, format="PNG")
        return buf.getvalue()

    def _caller(self, **over):
        reply = {"status": "GENERATED", "reference_media_id": "m1", "preflight_credits": 6.5,
                 "job_id": "job-123", "image_url": "https://cdn.example/out.png",
                 "balance_before": 897.76, "balance_after": 891.26,
                 "transactions": [{"display_name": "GPT Image 2", "credits": -6.5}],
                 "detail": "ok", **over}

        def call(prompt):
            self.calls.append(prompt)
            return {"text": json.dumps(reply), "cost_usd": 0.01}
        return call

    def _run(self, confirm=True, caller=None, slot="1"):
        return ps05_ops.generate_slide("20", slot, root=self.root, provider="higgsfield",
                                       confirm_spend=confirm, caller=caller or self._caller(),
                                       downloader=lambda url: self._png_3x4())

    def _grant(self, credits="7", slot="1"):
        return ps05_ops.grant_paid_slide("20", slot, credits, "founder 2026-09-24 (test)")

    def _item(self):
        return st.load()["content_plan"]["content_items"][20]

    # -- gates -----------------------------------------------------------------
    def test_no_grant_refuses_before_any_call(self):
        out = self._run()
        self.assertEqual(out["status"], "NO_PAID_SLIDE_GRANT")
        self.assertFalse(out["ok"])
        self.assertEqual(self.calls, [])

    def test_grant_is_bounded_and_never_opens_the_autonomous_paid_path(self):
        self.assertFalse(ps05_ops.grant_paid_slide("20", "1", "25", "founder")["ok"])   # > cap
        self.assertFalse(ps05_ops.grant_paid_slide("20", "1", "7", "  ")["ok"])         # no approver
        out = self._grant()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["credit_ceiling"], {"from": 40.12, "to": 47.12})
        item = self._item()
        self.assertFalse(stages.paid_generation_approved(item))          # item gate untouched
        decision = carousel_handoff.provider_decision(item, credits_remaining=7.38)
        self.assertEqual(decision["provider"], carousel_handoff.PROVIDER)  # tick stays free
        self.assertFalse(self._grant()["ok"])                            # one open grant per slide

    def test_without_confirm_spend_it_only_reports_what_it_would_send(self):
        self._grant()
        out = self._run(confirm=False)
        self.assertEqual(out["status"], "READY_TO_SPEND")
        self.assertEqual(self.calls, [])
        req = out["request"]
        self.assertEqual((req["model"], req["settings"]["quality"]), ("gpt_image_2", "high"))
        self.assertEqual(req["reference_url"], carousel_handoff.MASTER_REFERENCE_URL)
        self.assertIn("anatomical LEFT cheek (viewer-right", req["prompt"])
        self.assertEqual(st.credits_remaining(st.load()), 7.38)          # nothing spent

    def test_ceiling_and_slide_order_gates(self):
        self._grant()
        v = st.load(); v["credit_budget"]["spent_this_session"] = 45.0; st.save(v)
        self.assertEqual(self._run()["status"], "CREDIT_CEILING")
        self._grant(slot="2")
        v = st.load(); v["credit_budget"]["spent_this_session"] = 30.0; st.save(v)
        self.assertEqual(self._run(slot="2")["status"], "BLOCKED_NOT_READY")
        self.assertEqual(self.calls, [])

    def test_simulated_approvals_block_paid_work(self):
        _patch_charm_lock_with_fixture(self)
        self._grant(slot="2")
        self.assertEqual(self._run(slot="2")["status"], "SIMULATED_APPROVALS")
        self.assertEqual(self.calls, [])

    # -- the one paid call ---------------------------------------------------------
    def test_success_records_the_real_charge_and_leaves_a_pending_candidate(self):
        self._grant()
        out = self._run()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["status"], story_continuity.PENDING_VISUAL_QA)
        self.assertFalse(out["approved"])
        self.assertEqual((out["cost_credits"], out["preflight_credits"]), (6.5, 6.5))
        self.assertEqual(len(self.calls), 1)
        self.assertIn("never submit more than ONE generation", self.calls[0])
        from PIL import Image
        cand = story_continuity.candidate_path(20, 1, self.root)
        with Image.open(cand) as im:
            self.assertEqual(im.size, (1080, 1350))
        self.assertTrue(Path(out["raw_path"]).is_file())
        meta = json.loads(Path(out["metadata_file"]).read_text())
        self.assertEqual(meta["status_after"], story_continuity.PENDING_VISUAL_QA)
        v = st.load()
        self.assertEqual(v["credit_budget"]["spent_this_session"], 46.24)   # 39.74 + 6.5
        self.assertEqual(v["credit_budget"]["session_limit"], 46.62)        # 0.5 unused returned
        grant = v["content_plan"]["content_items"][20]["paid_slide_grants"][0]
        self.assertEqual((grant["charged_credits"], grant["job_id"]), (6.5, "job-123"))
        lock = story_continuity.load_story_lock(20)
        state = story_continuity.load_state(lock, 20, root=self.root)
        self.assertEqual(state["approved_slide_refs"], [])                 # never auto-approved
        self.assertEqual(state["environment_lock"]["status"], "PROVISIONAL")
        self.assertEqual(state["cost_state"]["credits_spent"], 6.5)
        # single use: the grant is gone, and an existing candidate blocks re-spend anyway
        self.assertEqual(self._run()["status"], "NO_PAID_SLIDE_GRANT")
        self._grant()
        self.assertEqual(self._run()["status"], "CANDIDATE_EXISTS")
        self.assertEqual(len(self.calls), 1)

    def test_an_unreconciled_charge_is_recorded_but_yields_no_candidate(self):
        self._grant()
        out = self._run(caller=self._caller(balance_after=884.76))           # 13 charged
        self.assertFalse(out["ok"])
        self.assertEqual(out["cost_credits"], 13.0)
        self.assertTrue(out["validation_problems"])
        self.assertFalse(story_continuity.candidate_path(20, 1, self.root).exists())
        self.assertEqual(st.load()["credit_budget"]["spent_this_session"], 52.74)

    def test_refused_cost_spends_nothing_and_keeps_the_grant(self):
        self._grant()
        out = self._run(caller=self._caller(status="REFUSED_COST", preflight_credits=9,
                                            balance_before=None, balance_after=None,
                                            job_id="", image_url="", transactions=[]))
        self.assertFalse(out["ok"])
        self.assertEqual(out["cost_credits"], 0)
        self.assertFalse(self._item()["paid_slide_grants"][0].get("consumed_at"))
        self.assertEqual(st.load()["credit_budget"]["spent_this_session"], 39.74)

    def test_an_unknown_outcome_consumes_the_grant_and_guesses_no_charge(self):
        self._grant()

        def boom(prompt):
            self.calls.append(prompt)
            raise TimeoutError("claude call timed out after 600s")
        out = self._run(caller=boom)
        self.assertEqual(out["status"], "OUTCOME_UNKNOWN")
        self.assertEqual(self._item()["paid_slide_grants"][0]["outcome"], "UNKNOWN")
        self.assertEqual(st.load()["credit_budget"]["spent_this_session"], 39.74)
        self.assertEqual(self._run()["status"], "NO_PAID_SLIDE_GRANT")     # no blind resubmit

    def test_crop_is_deterministic_4x5(self):
        a, b = self.hip.crop_to_4x5(self._png_3x4()), self.hip.crop_to_4x5(self._png_3x4())
        self.assertEqual(a, b)


class OpenAIRuntimeCredentialStore(unittest.TestCase):
    """openai_runtime keeps the key in a 600 file under a 700 gitignored dir,
    exposes only booleans, and exports OPENAI_API_KEY into the calling process
    without overriding an existing value."""

    def setUp(self):
        import openai_runtime
        self.o = openai_runtime
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = (openai_runtime.RUNTIME_DIR, openai_runtime.CREDENTIALS_PATH)
        openai_runtime.RUNTIME_DIR = Path(self._tmp.name) / ".openai_runtime"
        openai_runtime.CREDENTIALS_PATH = openai_runtime.RUNTIME_DIR / "credentials.json"
        self._env = os.environ.pop("OPENAI_API_KEY", None)

    def tearDown(self):
        self.o.RUNTIME_DIR, self.o.CREDENTIALS_PATH = self._orig
        os.environ.pop("OPENAI_API_KEY", None)
        if self._env is not None:
            os.environ["OPENAI_API_KEY"] = self._env
        self._tmp.cleanup()

    def test_store_is_private_and_status_never_contains_the_key(self):
        key = "sk-test-" + "x" * 48
        self.assertFalse(self.o.has_credentials())
        self.assertFalse(self.o.export_env())
        self.o.store_api_key(key)
        self.assertEqual(self.o.CREDENTIALS_PATH.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.o.RUNTIME_DIR.stat().st_mode & 0o777, 0o700)
        s = self.o.status()
        self.assertTrue(s["stored"] and s["shape_ok"] and s["env_available_after_export"])
        self.assertNotIn(key, json.dumps(s))
        self.assertEqual(os.environ["OPENAI_API_KEY"], key)

    def test_existing_env_value_is_not_overridden_and_bad_shapes_are_refused(self):
        os.environ["OPENAI_API_KEY"] = "sk-already-set-" + "y" * 40
        self.o.store_api_key("sk-file-" + "z" * 48)
        self.o.export_env()
        self.assertTrue(os.environ["OPENAI_API_KEY"].startswith("sk-already-set-"))
        self.assertTrue(self.o.key_problems("not-a-key"))
        self.assertTrue(self.o.key_problems("sk-short"))

    def test_runtime_dir_is_gitignored(self):
        self.assertIn(".openai_runtime/", (Path(__file__).parent / ".gitignore").read_text().split())


class OpenAIPaidSlideRoute(_TmpState):
    """`generate-slide 20 1 --provider openai` (founder 2026-09-25: direct
    OpenAI, gpt-image-2.5-sunburst). Without --confirm-spend it is a dry run
    that sends nothing; with it, every gate must pass; the usage OpenAI returns
    is what gets recorded; the output is a PENDING_VISUAL_QA candidate."""

    def setUp(self):
        super().setUp()
        import openai_image_provider as oip
        import openai_runtime
        self.oip, self.rt = oip, openai_runtime
        self.root = Path(self._tmp.name) / "assets"
        self.v["persona"] = _persona()
        self.v["content_plan"] = {"content_items": [{} for _ in range(20)] + [_good_carousel()]}
        st.save(self.v)
        ps05_ops.apply_story("20", str(Path(__file__).parent / "brand/story_locks/the_charm_001.json"))
        orig = openai_runtime.has_credentials
        openai_runtime.has_credentials = lambda: True
        self.addCleanup(setattr, openai_runtime, "has_credentials", orig)
        self.sent = []

    def _client(self, usage=None, fail=None):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (1088, 1360), (70, 60, 55)).save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        usage = usage if usage is not None else {
            "input_tokens": 7000, "output_tokens": 1587,
            "input_tokens_details": {"text_tokens": 3000, "image_tokens": 4000}}
        test = self

        class _Images:
            def edit(self, **kw):
                test.sent.append({k: v for k, v in kw.items() if k != "image"} |
                                 {"image_count": len(kw["image"])})
                if fail:
                    raise fail
                return types.SimpleNamespace(model_dump=lambda: {
                    "created": 1, "data": [{"b64_json": b64}], "usage": usage})
        return types.SimpleNamespace(images=_Images())

    def _run(self, confirm=False, client=None, quality=None):
        return ps05_ops.generate_slide("20", "1", root=self.root, provider="openai",
                                       confirm_spend=confirm, client=client or self._client(),
                                       quality=quality)

    def _grant(self, usd="0.50"):
        return ps05_ops.grant_paid_slide("20", "1", usd, "founder 2026-09-25 (test)", provider="openai")

    def test_dry_run_reports_the_exact_request_and_sends_nothing(self):
        out = self._run()
        self.assertEqual((out["status"], out["api_request_sent"]), ("PAID_DRY_RUN", False))
        self.assertEqual(self.sent, [])
        req = out["request"]
        self.assertEqual((req["model"], req["quality"], req["size"]),
                         ("gpt-image-2.5-sunburst", "high", "1088x1360"))
        paths = [r["path"] for r in req["references"]]
        self.assertTrue(paths[0].endswith("generation_refs/gen_ref_1_face_closeup.png"))
        self.assertFalse(any(p.endswith(("reference_primary.webp", "reference_secondary.webp"))
                             for p in paths))                       # sheets with human ears never sent
        self.assertFalse(any("benchmark" in p for p in paths))     # evaluation-only, never a reference
        self.assertIn("Image 1 [identity_generation_ref_1]", req["prompt"])
        self.assertIn("leaning slightly toward the pillow", req["prompt"])
        self.assertIn("NOT a centered full-body portrait", req["prompt"])
        self.assertIn("never replaced by, or lost among, freckles", req["prompt"])
        self.assertIn("hair naturally covers the anatomical human-ear locations", req["prompt"])
        self.assertIn("antique brass charm", req["prompt"])
        self.assertNotIn("Nyx not in frame", req["prompt"])
        self.assertEqual(req["estimate"]["output_tokens"], 1587)
        self.assertIn("founder_slide_grant", out["blocking_gates"])
        self.assertTrue(Path(out["dry_run_file"]).is_file())

    def test_confirm_without_grant_is_refused_before_any_request(self):
        out = self._run(confirm=True)
        self.assertEqual(out["status"], "GATES_BLOCKED")
        self.assertFalse(out["api_request_sent"])
        self.assertEqual(self.sent, [])

    def test_grant_is_bounded_usd_and_never_opens_the_autonomous_path(self):
        self.assertFalse(ps05_ops.grant_paid_slide("20", "1", "5", "founder", provider="openai")["ok"])
        out = self._grant()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["openai_ceiling_usd"], {"from": 0, "to": 0.5})
        item = st.load()["content_plan"]["content_items"][20]
        self.assertFalse(stages.paid_generation_approved(item))
        self.assertEqual(st.credits_remaining(st.load()), st.credits_remaining(self.v))  # Higgsfield untouched

    def test_success_records_returned_usage_and_leaves_a_pending_candidate(self):
        self._grant()
        out = self._run(confirm=True)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["status"], story_continuity.PENDING_VISUAL_QA)
        self.assertFalse(out["approved"])
        self.assertEqual(len(self.sent), 1)
        sent = self.sent[0]
        self.assertEqual((sent["model"], sent["size"], sent["quality"], sent["n"], sent["image_count"]),
                         ("gpt-image-2.5-sunburst", "1088x1360", "high", 1, 3))
        expected = round((3000 * 5 + 4000 * 8 + 1587 * 30) / 1e6, 5)
        self.assertEqual(out["cost_usd"], expected)
        from PIL import Image
        with Image.open(story_continuity.candidate_path(20, 1, self.root)) as im:
            self.assertEqual(im.size, (1080, 1350))
        b = st.load()["openai_budget"]
        self.assertEqual(b["spent_this_session_usd"], expected)
        self.assertEqual(b["session_limit_usd"], expected)             # unused part returned
        lock = story_continuity.load_story_lock(20)
        state = story_continuity.load_state(lock, 20, root=self.root)
        self.assertEqual(state["approved_slide_refs"], [])
        self.assertEqual(state["generation_log"][0]["usage"]["output_tokens"], 1587)
        self.assertEqual(self._run(confirm=True)["status"], "GATES_BLOCKED")  # single use
        self.assertEqual(len(self.sent), 1)

    def test_unknown_outcome_consumes_the_grant_and_records_no_cost(self):
        self._grant()
        out = self._run(confirm=True, client=self._client(fail=TimeoutError("read timed out")))
        self.assertEqual(out["status"], "OUTCOME_UNKNOWN")
        self.assertEqual(st.load()["openai_budget"]["spent_this_session_usd"], 0)
        self.assertEqual(self._run(confirm=True)["status"], "GATES_BLOCKED")

    def test_missing_key_blocks_the_send(self):
        self._grant()
        self.rt.has_credentials = lambda: False
        out = self._run(confirm=True)
        self.assertIn("api_key_stored", out["blocking_gates"])
        self.assertEqual(self.sent, [])

    def test_output_token_formula_and_reference_order(self):
        self.assertEqual(self.oip.output_tokens(1088, 1360, "high"), 1587)
        self.assertEqual(self.oip.output_tokens(1088, 1360, "low"), 181)
        self.assertEqual(self.oip.output_tokens(1024, 1024, "high"), 1756)
        with self.assertRaises(self.oip.OpenAIImageError):
            self.oip.resolve_references([{"kind": "local", "base": "project", "authority": 3,
                                          "value": "brand/nyx_identity/reference_primary.webp",
                                          "never_identity": True}],
                                        project_root=Path(__file__).parent, package_dir=self.root)


class CleanGenerationReferencesAndFounderRejection(_TmpState):
    """Identity spec 2.2.0 registers pure crops of reference_primary as the
    generation-facing identity inputs (no human-ear panels, no printed text); the
    ChatGPT benchmark is evaluation-only; a founder rejection goes through the
    story's own retry lifecycle and archives the candidate."""

    def setUp(self):
        super().setUp()
        import openai_image_provider as oip
        self.oip = oip
        self.root = Path(self._tmp.name) / "assets"
        self.v["persona"] = _persona()
        self.v["content_plan"] = {"content_items": [{} for _ in range(20)] + [_good_carousel()]}
        st.save(self.v)
        self.project = Path(__file__).parent

    def test_generation_refs_are_registered_hashed_crops_of_the_primary(self):
        spec = json.loads((self.project / "brand/nyx_identity/identity_spec.json").read_text())
        gen = spec["generation_references"]
        self.assertEqual(gen["source"]["sha256_16"], "50ec9aaa809fd3da")
        from PIL import Image
        with Image.open(self.project / "brand/nyx_identity/reference_primary.webp") as src:
            master = src.convert("RGB")
            for img in gen["images"]:
                path = self.project / img["file"]
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest()[:16], img["sha256_16"])
                with Image.open(path) as crop:
                    self.assertEqual(list(crop.convert("RGB").getdata()),
                                     list(master.crop(tuple(img["crop_box"])).getdata()))  # pure crop

    def test_override_replaces_master_sheets_and_keeps_environment_refs(self):
        sel = [{"role": "identity_reference", "kind": "url", "value": "https://x", "authority": 1},
               {"role": "identity_reference", "kind": "local", "base": "project", "authority": 1,
                "value": "brand/nyx_identity/reference_primary.webp"},
               {"role": "identity_reference_secondary", "kind": "local", "base": "project", "authority": 1,
                "value": "brand/nyx_identity/reference_secondary.webp"},
               {"role": "environment_reference", "kind": "local", "base": "project", "authority": 3,
                "value": "brand/nyx_identity/generation_refs/gen_ref_3_fox_ear_detail.png",
                "never_identity": True}]
        override = self.oip.generation_identity_references(self.project)
        refs = self.oip.resolve_references(sel, project_root=self.project, package_dir=self.root,
                                           identity_override=override)
        roles = [r["role"] for r in refs]
        self.assertEqual(roles[:3], ["identity_generation_ref_1", "identity_generation_ref_2",
                                     "identity_generation_ref_3"])
        self.assertNotIn("identity_reference_secondary", roles)
        bad = [{**override[0], "expected_sha256_16": "0000000000000000"}]
        with self.assertRaises(self.oip.OpenAIImageError):
            self.oip.resolve_references([], project_root=self.project, package_dir=self.root,
                                        identity_override=bad)

    def test_identity_spec_2_2_wording(self):
        spec = json.loads((self.project / "brand/nyx_identity/identity_spec.json").read_text())
        mark = spec["locked_traits"]["beauty_mark"].lower()
        self.assertIn("one distinct small dark mark", mark)
        self.assertIn("never replaced by, or lost among, freckles", mark)
        self.assertIn("hair naturally covers the anatomical human-ear locations",
                      spec["locked_traits"]["fox_ears"])

    def test_benchmark_is_registered_as_evaluation_only(self):
        lock = story_continuity.load_story_lock(20)
        bench = lock["evaluation_benchmarks"][0]
        self.assertIn("NEVER a generation reference", bench["use"])
        state = story_continuity.ensure_state(lock, 20, root=self.root)
        record = story_continuity.plan_record(lock, 1)
        refs = story_continuity.reference_selection(_persona(), state, record, index=20, root=self.root)
        self.assertFalse(any("benchmark" in str(r.get("value")) for r in refs))
        self.assertNotIn("benchmark", story_continuity.compile_prompt(_persona(), state, record)["prompt"].lower())

    def test_founder_rejection_moves_only_the_retry_counter_and_archives(self):
        lock = story_continuity.load_story_lock(20)
        cand = story_continuity.candidate_path(20, 1, self.root)
        cand.parent.mkdir(parents=True, exist_ok=True)
        cand.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        cand.with_name("item20_slide1.openai_raw.png").write_bytes(b"raw")
        cand.with_name("item20_slide1.openai.dryrun.json").write_text("{}")
        before = story_continuity.ensure_state(lock, 20, root=self.root)
        self.assertFalse(ps05_ops.reject_slide("20", "1", "nonsense", "r", "f", root=self.root)["ok"])
        out = ps05_ops.reject_slide("20", "1", "identity,public_social_style", "human ear; no mark",
                                    "founder (test)", root=self.root)
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["attempt"], out["attempts_remaining"]), (1, 2))
        self.assertEqual(out["critical_failures"], ["identity"])
        self.assertFalse(cand.exists())
        self.assertTrue((Path(out["archived_to"]) / "item20_slide1.png").is_file())
        self.assertTrue(cand.with_name("item20_slide1.openai.dryrun.json").is_file())   # dry run kept
        after = story_continuity.load_state(lock, 20, root=self.root)
        self.assertEqual(after["story_state"], before["story_state"])
        self.assertEqual(after["approved_slide_refs"], [])
        self.assertEqual(after["retry_state"]["attempts"], {"1": 1})


class PaidCallSurvivesConcurrentTickWrite(_TmpState):
    """2026-09-25 regression: the hourly tick saved an older venture.json while a
    paid OpenAI call was in flight, erasing the grant; the post-call bookkeeping
    crashed (StopIteration) and the returned usage was lost. Now the usage goes to
    an append-only ledger first, the grant is restored instead of crashing, and
    grants/paid calls refuse to start while a tick is running."""

    def setUp(self):
        super().setUp()
        import openai_runtime
        self.root = Path(self._tmp.name) / "assets"
        self.v["persona"] = _persona()
        self.v["content_plan"] = {"content_items": [{} for _ in range(20)] + [_good_carousel()]}
        st.save(self.v)
        ps05_ops.apply_story("20", str(Path(__file__).parent / "brand/story_locks/the_charm_001.json"))
        orig = openai_runtime.has_credentials
        openai_runtime.has_credentials = lambda: True
        self.addCleanup(setattr, openai_runtime, "has_credentials", orig)
        orig_status = scheduler_heartbeat.status
        self.tick = {"tick_in_progress": False}
        scheduler_heartbeat.status = lambda: dict(self.tick)
        self.addCleanup(setattr, scheduler_heartbeat, "status", orig_status)

    def _clobbering_client(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (1088, 1360), (70, 60, 55)).save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        class _Images:
            def edit(self_inner, **kw):
                v = st.load()                                   # the tick's stale save lands now
                v["content_plan"]["content_items"][20]["paid_slide_grants"] = []
                v["openai_budget"]["ceiling_history"] = []
                v["openai_budget"]["session_limit_usd"] = 0
                st.save(v)
                return types.SimpleNamespace(model_dump=lambda: {
                    "data": [{"b64_json": b64}],
                    "usage": {"input_tokens": 7000, "output_tokens": 1587,
                              "input_tokens_details": {"text_tokens": 3000, "image_tokens": 4000}}})
        return types.SimpleNamespace(images=_Images())

    def test_grant_and_spend_refuse_while_a_tick_runs(self):
        self.tick["tick_in_progress"] = True
        self.assertEqual(ps05_ops.grant_paid_slide("20", "1", "0.5", "founder", provider="openai")["status"],
                         "TICK_IN_PROGRESS")
        self.tick["tick_in_progress"] = False
        self.assertTrue(ps05_ops.grant_paid_slide("20", "1", "0.5", "founder", provider="openai")["ok"])
        self.tick["tick_in_progress"] = True
        out = ps05_ops.generate_slide("20", "1", root=self.root, provider="openai", confirm_spend=True,
                                      client=self._clobbering_client())
        self.assertEqual(out["status"], "TICK_IN_PROGRESS")
        self.assertFalse(out["api_request_sent"])

    def test_usage_survives_a_grant_erased_mid_call(self):
        self.assertTrue(ps05_ops.grant_paid_slide("20", "1", "0.5", "founder", provider="openai")["ok"])
        out = ps05_ops.generate_slide("20", "1", root=self.root, provider="openai", confirm_spend=True,
                                      client=self._clobbering_client())
        expected = round((3000 * 5 + 4000 * 8 + 1587 * 30) / 1e6, 5)
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["bookkeeping_verified"])
        self.assertEqual(out["cost_usd"], expected)
        v = st.load()
        g = v["content_plan"]["content_items"][20]["paid_slide_grants"][0]
        self.assertTrue(g["restored_after_concurrent_write"])
        self.assertEqual(g["charged_usd"], expected)
        b = v["openai_budget"]
        self.assertEqual((b["spent_this_session_usd"], b["session_limit_usd"]), (expected, expected))
        ledger = [json.loads(l) for l in Path(out["paid_ledger"]).read_text().splitlines()]
        call = [e for e in ledger if e["event"] == "paid_call"][0]
        self.assertEqual(call["usage"]["output_tokens"], 1587)
        self.assertEqual(call["actual_usd"], expected)
        self.assertTrue(json.loads(Path(out["metadata_file"]).read_text())["result"]["usage"])


class VentureJsonConcurrentWriteRace(_TmpState):
    """2026-09-25 incident, root cause. The hourly tick loaded venture.json at
    05:09:24Z, a CLI run wrote a founder slide grant at 05:09:37Z, and the tick
    saved its stale copy at 05:09:45Z -- silently erasing the grant. Nothing
    serialized the two read-modify-write cycles and nothing noticed the newer
    file. The append-only paid ledger added the same day preserved the money
    trail, not the state."""

    def test_a_stale_save_is_refused_and_the_newer_write_survives(self):
        tick_copy = st.load()                       # the tick loads venture.json
        tick_copy["phase"] = "MEASURING"

        cli = st.load()                             # the CLI writes something newer
        cli["content_plan"] = {"content_items": [{"paid_slide_grants": [{"slot": 1}]}]}
        st.save(cli)

        with self.assertRaises(st.StaleWriteError):
            st.save(tick_copy)                      # the tick's save lands last

        after = st.load()
        self.assertEqual(after["content_plan"]["content_items"][0]["paid_slide_grants"],
                         [{"slot": 1}])             # the newer write survived
        self.assertNotEqual(after["phase"], "MEASURING")   # the stale one did not win

    def test_the_tick_reports_the_refusal_instead_of_crashing_or_clobbering(self):
        tick_copy = st.load()
        tick_copy["phase"] = "MEASURING"
        cli = st.load()
        cli["decision"] = "VALIDATED"
        st.save(cli)

        parts, blockers = [], []
        try:                                        # exactly ps05_tick's save site
            st.save(tick_copy)
        except st.StaleWriteError as exc:
            parts.append(f"venture.json NOT saved -- {exc}")
            blockers.append("venture.json write refused: a newer concurrent write survives")

        self.assertEqual(len(blockers), 1)
        self.assertIn("NOT saved", parts[0])
        self.assertEqual(st.load()["decision"], "VALIDATED")

    def test_update_rebases_on_the_newer_state_instead_of_refusing(self):
        """The safe read-modify-write: load and save inside one lock, so what it
        writes is always derived from the newest file on disk."""
        stale = st.load()
        newer = st.load()
        newer["decision"] = "VALIDATED"
        st.save(newer)

        st.update(lambda v: v.update({"phase": "MEASURING"}))
        after = st.load()
        self.assertEqual((after["phase"], after["decision"]), ("MEASURING", "VALIDATED"))
        self.assertGreater(after["state_revision"], stale["state_revision"])

    def test_a_locked_span_is_reentrant_and_atomic(self):
        with st.venture_lock():
            v = st.load()                           # nested acquire: must not deadlock
            v["phase"] = "MEASURING"
            st.save(v)
        self.assertEqual(st.load()["phase"], "MEASURING")
        self.assertTrue(st.venture_lock_path().name.endswith("venture.json.lock"))

    def test_grant_paid_slide_survives_a_write_that_lands_during_its_own_span(self):
        """The 2026-09-25 shape, through the real CLI entry point."""
        self.v["persona"] = _persona()
        self.v["content_plan"] = {"content_items": [{} for _ in range(20)] + [_good_carousel()]}
        st.save(self.v)
        orig_status = scheduler_heartbeat.status
        scheduler_heartbeat.status = lambda: {"tick_in_progress": False}
        self.addCleanup(setattr, scheduler_heartbeat, "status", orig_status)

        tick_copy = st.load()                       # loaded before the grant
        out = ps05_ops.grant_paid_slide("20", "1", "0.5", "founder 2026-09-26", provider="openai")
        self.assertTrue(out["ok"], out)
        with self.assertRaises(st.StaleWriteError):
            st.save(tick_copy)
        grants = st.load()["content_plan"]["content_items"][20]["paid_slide_grants"]
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0]["max_usd"], 0.5)


class FounderApprovesSlideOne(_TmpState):
    """Founder approval as an explicit, auditable lifecycle transition -- the
    counterpart of reject-slide. The founder looking at the image replaces the
    visual-QA SCORE, never the structural gates, and never becomes a fabricated
    score in the state file."""

    def setUp(self):
        super().setUp()
        self.root = Path(self._tmp.name) / "assets"
        self.v["persona"] = _persona()
        self.v["content_plan"] = {"content_items": [{} for _ in range(20)] + [_good_carousel()]}
        st.save(self.v)
        real = Path(__file__).parent / "brand/story_locks/the_charm_001.json"
        ps05_ops.apply_story("20", str(real))
        # A private copy of the lock: approving here never edits the real one.
        self.lock_path = Path(self._tmp.name) / "locks" / "the_charm_001.json"
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = json.loads(real.read_text())
        lock["founder_approvals"] = []
        # This class tests the approval LIFECYCLE (never the provider), and
        # asserts DRY_RUN for slide 2 -- pin generation.enabled=False in this
        # private copy explicitly, rather than inheriting whatever the real
        # production lock currently has, so it can never trigger an actual
        # local-provider generation. Same for visual_qa.authorized: this
        # class specifically tests founder approval WITHOUT any visual-QA
        # scorer authorized -- pin it False explicitly rather than inheriting
        # whatever the real lock's delegated-QA setting currently is.
        lock["generation"] = {**lock["generation"], "enabled": False}
        lock["visual_qa"] = {**lock["visual_qa"], "provider": None, "authorized": False}
        self.lock_path.write_text(json.dumps(lock, indent=2) + "\n")
        orig_dir = story_continuity.STORY_LOCKS_DIR
        story_continuity.STORY_LOCKS_DIR = self.lock_path.parent
        self.addCleanup(setattr, story_continuity, "STORY_LOCKS_DIR", orig_dir)
        self.candidate = story_continuity.candidate_path(20, 1, self.root)
        self._real_png(self.candidate)

    @staticmethod
    def _real_png(path: Path):
        from PIL import Image
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1080, 1350), (58, 48, 44)).save(path, format="PNG")

    def test_founder_approval_advances_the_story_once_and_locks_the_environment(self):
        out = ps05_ops.founder_approve_slide("20", "1", "founder 2026-09-26", "image is good",
                                             root=self.root)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["status"], "APPROVED")
        self.assertFalse(out["visual_qa_scorer_used"])
        self.assertEqual(out["environment_lock_status"], "LOCKED")
        self.assertEqual(out["environment_source_slide_ref"], self.candidate.name)
        self.assertEqual(out["approved_slides"], [1])
        self.assertEqual((out["cost_usd"], out["cost_credits"], out["generated"]), (0, 0, False))

        state = story_continuity.load_state(story_continuity.load_story_lock(20), 20, root=self.root)
        self.assertEqual(len(state["approved_slide_refs"]), 1)
        self.assertFalse(state["approved_slide_refs"][0]["simulated"])
        self.assertEqual(state["story_state"]["updated_from_slide"], 1)
        self.assertEqual(len(state["composition_history"]), 1)
        self.assertIsNone(story_continuity.approved_ref(state, 2))   # never a later slide

    def test_no_authorized_visual_qa_scorer_is_needed_or_invented(self):
        lock = story_continuity.load_story_lock(20)
        self.assertFalse(story_continuity.visual_qa_provider(lock)["available"])
        out = ps05_ops.founder_approve_slide("20", "1", "founder 2026-09-26", root=self.root)
        self.assertTrue(out["ok"], out)
        state = story_continuity.load_state(lock, 20, root=self.root)
        self.assertNotIn("qa", state["approved_slide_refs"][0])
        self.assertEqual(state["approved_slide_refs"][0]["approved_by"], "founder 2026-09-26")

    def test_retry_and_cost_history_survive_the_approval(self):
        lock = story_continuity.load_story_lock(20)
        state = story_continuity.ensure_state(lock, 20, root=self.root)
        state["retry_state"]["rejected"].append({"slide_index": 1, "attempt": 1,
                                                 "failed": ["identity"]})
        state["cost_state"]["actual_cost_usd"] = 0.1842
        state["generation_log"] = [{"slide_index": 1, "actual_usd": 0.08499}]
        story_continuity.save_state(state, 20, root=self.root)

        out = ps05_ops.founder_approve_slide("20", "1", "founder 2026-09-26", root=self.root)
        self.assertTrue(out["ok"], out)
        after = story_continuity.load_state(lock, 20, root=self.root)
        self.assertEqual(len(after["retry_state"]["rejected"]), 1)
        self.assertEqual(after["cost_state"]["actual_cost_usd"], 0.1842)
        self.assertEqual(len(after["generation_log"]), 1)

    def test_duplicate_approval_is_refused_safely_and_changes_nothing(self):
        first = ps05_ops.founder_approve_slide("20", "1", "founder 2026-09-26", root=self.root)
        self.assertTrue(first["advanced"])
        before = json.loads(story_continuity.state_path(20, self.root).read_text())
        second = ps05_ops.founder_approve_slide("20", "1", "someone else", root=self.root)
        self.assertEqual(second["status"], "ALREADY_APPROVED")
        self.assertFalse(second["advanced"])
        after = json.loads(story_continuity.state_path(20, self.root).read_text())
        self.assertEqual(after["approved_slide_refs"], before["approved_slide_refs"])
        self.assertEqual(after["environment_lock"], before["environment_lock"])
        self.assertEqual(len(json.loads(self.lock_path.read_text())["founder_approvals"]), 1)

    def test_a_missing_candidate_is_never_approved(self):
        self.candidate.unlink()
        out = ps05_ops.founder_approve_slide("20", "1", "founder 2026-09-26", root=self.root)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "BLOCKED")
        self.assertEqual(json.loads(self.lock_path.read_text())["founder_approvals"], [])

    def test_slide_two_cannot_be_founder_approved_before_slide_one(self):
        self._real_png(story_continuity.candidate_path(20, 2, self.root))
        out = ps05_ops.founder_approve_slide("20", "2", "founder 2026-09-26", root=self.root)
        self.assertFalse(out["ok"])
        self.assertIn("before slide 1", out["error"])

    def test_the_public_slide_is_the_deterministic_overlay_not_a_new_generation(self):
        from PIL import Image
        out = ps05_ops.founder_approve_slide("20", "1", "founder 2026-09-26", root=self.root)
        overlay = out["overlay"]
        self.assertTrue(overlay["ok"], overlay)
        self.assertFalse(overlay["image_model_used"])
        self.assertEqual(overlay["text"], "this wasn't here when I left.")
        self.assertEqual(overlay["position"], "bottom-left")        # lower-third safe zone
        self.assertIn("lower third", overlay["safe_zone"])
        self.assertEqual(overlay["dimensions"], "1080x1350")
        self.assertEqual(overlay["clean_source"], str(self.candidate))
        self.assertTrue(Path(overlay["output"]).is_file())
        self.assertNotEqual(overlay["output"], overlay["clean_source"])
        # the clean approved source -- the environment master -- is untouched
        with Image.open(self.candidate) as clean, Image.open(overlay["output"]) as captioned:
            self.assertEqual(clean.size, captioned.size)
            self.assertNotEqual(clean.tobytes(), captioned.tobytes())
        self.assertEqual(out["environment_source_slide_ref"], self.candidate.name)

    def test_the_overlay_font_is_phone_readable_not_the_pillow_default(self):
        size = int(1350 * carousel_handoff.OVERLAY_FONT_PCT / 100)
        font, source = carousel_handoff._overlay_font(size)
        self.assertNotIn("load_default", source)
        self.assertGreater(font.getlength("this wasn't here when I left."), 400)

    def test_overlay_position_comes_from_the_slides_own_safe_zone(self):
        self.assertEqual(carousel_handoff.overlay_position(
            "lower third, clear of her face, the charm and the pillow edge"), "bottom-left")
        self.assertEqual(carousel_handoff.overlay_position(
            "upper third, clear of her hands and the charm"), "top-left")

    def test_slide_two_becomes_dry_runnable_only_after_slide_one_is_approved(self):
        blocked = ps05_ops.generate_slide("20", "2", root=self.root)
        self.assertEqual(blocked["status"], "BLOCKED_NOT_READY")
        self.assertTrue(ps05_ops.founder_approve_slide("20", "1", "founder 2026-09-26",
                                                       root=self.root)["ok"])
        out = ps05_ops.generate_slide("20", "2", root=self.root)
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["status"], out["readiness"]), ("DRY_RUN", "READY"))
        self.assertEqual(out["environment_lock_status"], "LOCKED")
        self.assertEqual(out["environment_source_slide_ref"], self.candidate.name)
        self.assertEqual((out["cost_usd"], out["cost_credits"], out["generated"]), (0, 0, False))
        self.assertFalse(out["paid_generation_authorized"])
