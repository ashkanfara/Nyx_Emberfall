"""Focused tests for the 2026-09-19 autonomy repair (no network, no real state)."""

import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import instagram_metrics
import ps05_tick
import state as st


def _http_error(code, body):
    return urllib.error.HTTPError("https://graph.instagram.com/x?access_token=SECRET", code,
                                  "Bad Request", {}, io.BytesIO(body.encode()))


class DescribeHttpError(unittest.TestCase):
    def test_graph_error_body_is_surfaced_without_token(self):
        body = json.dumps({"error": {"message": "API access blocked.", "type": "OAuthException",
                                     "code": 200}})
        msg = instagram_metrics.describe_http_error(_http_error(400, body))
        self.assertEqual(msg, "HTTP 400 OAuthException code 200: API access blocked.")
        self.assertNotIn("SECRET", msg)

    def test_non_json_body_falls_back_to_status(self):
        msg = instagram_metrics.describe_http_error(_http_error(400, "<html>"))
        self.assertTrue(msg.startswith("HTTP 400"))

    def test_get_raises_readable_error_not_url(self):
        body = json.dumps({"error": {"message": "nope", "type": "OAuthException", "code": 200}})
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(400, body)):
            with self.assertRaises(RuntimeError) as cm:
                instagram_metrics._get("https://graph.instagram.com/x", {"access_token": "SECRET"})
        self.assertIn("OAuthException code 200", str(cm.exception))
        self.assertNotIn("SECRET", str(cm.exception))


class MeasurementBackoff(unittest.TestCase):
    def _exp(self, hours_ago, reason="HTTP 400 OAuthException code 200: API access blocked."):
        ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
        return {"id": "E", "assets": ["m1"], "status": "measuring", "measurements": [
            {"ts": ts, "status": "blocked", "reason": reason, "checked_at": ts}]}

    def test_within_backoff_makes_no_api_call_and_appends_nothing(self):
        exp = self._exp(1)
        v = {"experiments": [exp]}
        with mock.patch.object(instagram_metrics, "fetch_media_metrics") as fetch:
            note = ps05_tick._measure_one_experiment(v, exp)
        fetch.assert_not_called()
        self.assertIn("still blocked", note)
        self.assertEqual(len(exp["measurements"]), 1)

    def test_after_backoff_same_reason_refreshes_row_not_append(self):
        exp = self._exp(25)
        v = {"experiments": [exp]}
        reason = exp["measurements"][0]["reason"]
        with mock.patch.object(instagram_metrics, "fetch_media_metrics", side_effect=RuntimeError(reason)):
            ps05_tick._measure_one_experiment(v, exp)
        self.assertEqual(len(exp["measurements"]), 1)
        self.assertEqual(exp["measurements"][0]["consecutive_blocked"], 2)

    def test_after_backoff_new_reason_appends(self):
        exp = self._exp(25)
        v = {"experiments": [exp]}
        with mock.patch.object(instagram_metrics, "fetch_media_metrics", side_effect=RuntimeError("different")):
            ps05_tick._measure_one_experiment(v, exp)
        self.assertEqual(len(exp["measurements"]), 2)

    def test_ok_false_result_is_not_recorded_as_a_measurement(self):
        exp = {"id": "E", "assets": ["m1"], "status": "measuring", "measurements": []}
        v = {"experiments": [exp]}
        with mock.patch.object(instagram_metrics, "fetch_media_metrics",
                               return_value={"ok": False, "error": "no stored token"}):
            note = ps05_tick._measure_one_experiment(v, exp)
        self.assertIn("blocked", note)
        self.assertEqual(exp["measurements"][0]["status"], "blocked")


class ApprovalReconciliation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(st, "PENDING_APPROVAL_PATH", Path(self.tmp.name) / "pending.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        st._write_pending_approvals([
            {"id": "ig2", "reason": "CANNOT_SAFELY_DELEGATE", "created_at": "t", "estimated_minutes": 3,
             "what": "content_items[2] (Instagram video) has never been attempted; content_items[10] other",
             "why": "w", "founder_action": "Manually publish x.mp4 to Instagram as a Reel"},
            {"id": "tt16", "reason": "CANNOT_SAFELY_DELEGATE", "created_at": "t", "estimated_minutes": 3,
             "what": "TikTok publish-consent click for content_items[16] (QA_PASSED)",
             "why": "w", "founder_action": "Review the preview and reply"},
            {"id": "tt19", "reason": "CANNOT_SAFELY_DELEGATE", "created_at": "t", "estimated_minutes": 3,
             "what": "TikTok consent for content_items[19]", "why": "w", "founder_action": "Review"},
            {"id": "cred", "reason": "SPEND_REQUIRED", "created_at": "t", "estimated_minutes": 2,
             "what": "Higgsfield credits", "why": "w", "founder_action": "decide"},
        ])

    def _v(self, ig2="SCHEDULED", tt16="QA_PASSED", tt19="PUBLISHED"):
        items = [{} for _ in range(20)]
        items[2] = {"target_platform": "instagram", "lifecycle_status": ig2,
                    "publish_config": {"route": "metricool", "metricool_post_id": 1,
                                       "scheduled_for": "2026-09-22T18:00"}}
        items[16] = {"target_platform": "tiktok", "lifecycle_status": tt16}
        items[19] = {"target_platform": "tiktok", "lifecycle_status": tt19,
                     "platform_post_id": "https://www.tiktok.com/@n/video/1"}
        return {"content_plan": {"content_items": items}}

    def test_resolves_only_provable_cases_and_keeps_history(self):
        notes = st.reconcile_pending_approvals(self._v())
        self.assertEqual(len(notes), 2)
        active = [a["id"] for a in st.load_pending_approvals()]
        self.assertEqual(active, ["tt16", "cred"])          # unconsented TikTok + spend untouched
        raw = st._read_pending_approvals()
        self.assertEqual([a["id"] for a in raw], ["ig2", "tt16", "tt19", "cred"])  # nothing deleted
        self.assertEqual(raw[0]["resolution"]["status"], "SUPERSEDED")
        self.assertEqual(raw[0]["what"].split()[0], "content_items[2]")           # original text intact

    def test_idempotent(self):
        st.reconcile_pending_approvals(self._v())
        self.assertEqual(st.reconcile_pending_approvals(self._v()), [])

    def test_unscheduled_item_is_not_resolved(self):
        notes = st.reconcile_pending_approvals(self._v(ig2="QA_PASSED", tt19="QA_PASSED"))
        self.assertEqual(notes, [])
        self.assertEqual(len(st.load_pending_approvals()), 4)

    def test_clear_front_skips_resolved_history(self):
        st.reconcile_pending_approvals(self._v())
        st.clear_pending_approval()                          # what /api/approve does
        self.assertEqual([a["id"] for a in st.load_pending_approvals()], ["cred"])
        self.assertEqual(len(st._read_pending_approvals()), 3)  # only the front ACTIVE one removed


def _stream(tool_name, content, is_error=False):
    ev = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": tool_name, "input": {}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": content, "is_error": is_error}]}},
    ]
    return "\n".join(json.dumps(e) for e in ev)


class NyxProbeClassification(unittest.TestCase):
    T = ps05_tick.NYX_PROBE_TOOL

    def test_permission_denied_is_reported_not_verified(self):
        r = ps05_tick._parse_probe_stream(_stream(self.T, "permissions not granted", True))
        self.assertEqual(r["status"], "PERMISSION_DENIED_UNATTENDED")

    def test_verified_only_for_exactly_the_nyx_brand_and_instagram(self):
        ok = json.dumps({"data": [{"id": 6988018, "networksData": {"instagramData": "nyx.emberfall"}}]})
        self.assertEqual(ps05_tick._parse_probe_stream(_stream(self.T, ok))["status"], "VERIFIED")

    def test_ashkan_brand_is_identity_mismatch(self):
        bad = json.dumps({"data": [{"id": 6641055, "networksData": {"instagramData": "x"}}]})
        self.assertEqual(ps05_tick._parse_probe_stream(_stream(self.T, bad))["status"], "IDENTITY_MISMATCH")

    def test_extra_brand_alongside_nyx_is_mismatch(self):
        two = json.dumps({"data": [{"id": 6988018, "networksData": {"instagramData": "nyx.emberfall"}},
                                   {"id": 1, "networksData": {}}]})
        self.assertEqual(ps05_tick._parse_probe_stream(_stream(self.T, two))["status"], "IDENTITY_MISMATCH")

    def test_model_prose_without_tool_call_is_not_verified(self):
        prose = json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "brand 6988018 nyx.emberfall"}]}})
        self.assertEqual(ps05_tick._parse_probe_stream(prose)["status"], "TOOL_NOT_CALLED")

    def test_other_tool_result_is_ignored(self):
        self.assertEqual(ps05_tick._parse_probe_stream(_stream("mcp__metricool-ashkan__getBrandSettings", "{}"))["status"],
                         "TOOL_NOT_CALLED")

    def test_approved_scope_is_exactly_the_three_founder_confirmed_tools(self):
        import nyx_instagram
        self.assertEqual(ps05_tick.NYX_UNATTENDED_ALLOWED_TOOLS, nyx_instagram.WRITE_TIER)
        self.assertEqual(sorted(ps05_tick.NYX_UNATTENDED_ALLOWED_TOOLS), [
            "mcp__metricool-nyx__createScheduledPost",
            "mcp__metricool-nyx__getBrandSettings",
            "mcp__metricool-nyx__getScheduledPosts"])
        joined = " ".join(ps05_tick.NYX_UNATTENDED_ALLOWED_TOOLS)
        for forbidden in ("ashkan", "updateScheduledPost", "ForReview", "Higgsfield", "Analytics", "__metricool__"):
            self.assertNotIn(forbidden, joined)


class OutcomeAndMeasurementHonesty(unittest.TestCase):
    def test_blocked_measurement_is_degraded_never_acted(self):
        self.assertEqual(ps05_tick._classify_outcome(external_actions=[], blockers=["x blocked"]), "degraded")
        self.assertEqual(ps05_tick._classify_outcome(external_actions=[], blockers=[]), "checked")
        self.assertEqual(ps05_tick._classify_outcome(external_actions=["fanvue message sent"], blockers=["b"]), "acted")

    def test_no_assets_is_blocked_not_ok(self):
        exp = {"id": "E", "assets": [], "measurements": []}
        note = ps05_tick._measure_one_experiment({"experiments": [exp]}, exp)
        self.assertIn("blocked", note)
        self.assertEqual(exp["measurements"][0]["status"], "blocked")

    def test_partial_results_keep_errors_and_are_not_ok(self):
        exp = {"id": "E", "assets": ["a", "b"], "measurements": []}
        def fake(mid):
            if mid == "b":
                raise RuntimeError("HTTP 400 boom")
            return {"ok": True, "media_id": mid}
        with mock.patch.object(instagram_metrics, "fetch_media_metrics", side_effect=fake):
            note = ps05_tick._measure_one_experiment({"experiments": [exp]}, exp)
        m = exp["measurements"][0]
        self.assertEqual(m["status"], "partial")
        self.assertIn("b", m["errors"])
        self.assertIn("partial", note)

    def test_heartbeat_accepts_new_outcomes(self):
        import scheduler_heartbeat
        for o in ("checked", "degraded", "acted", "no_op", "error"):
            self.assertIn(o, scheduler_heartbeat.OUTCOMES)


class LegacyConnectorRouting(unittest.TestCase):
    def _cmd(self, tools):
        import claude_client
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(stdout=json.dumps({"result": "x", "is_error": False}),
                                         stderr="", returncode=0)
            claude_client.claude_call("p", tools=tools)
        return run.call_args[0][0]

    def test_judgment_only_calls_load_no_connectors(self):
        cmd = self._cmd(None)
        self.assertIn("--strict-mcp-config", cmd)
        self.assertEqual(cmd[cmd.index("--mcp-config") + 1], '{"mcpServers":{}}')

    def test_calls_that_name_tools_keep_their_connectors(self):
        cmd = self._cmd(["mcp__claude_ai_Higgsfield__generate_video"])
        self.assertNotIn("--strict-mcp-config", cmd)
        self.assertIn("--allowedTools", cmd)

    def test_probe_loads_only_nyx_and_preapproves_only_its_own_tool(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(ps05_tick, "NYX_PROBE_PATH", Path(d) / "probe.json"), \
                mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(stdout="", stderr="", returncode=0)
            ps05_tick._probe_metricool_nyx(force=True)
        cmd = run.call_args[0][0]
        servers = json.loads(cmd[cmd.index("--mcp-config") + 1])["mcpServers"]
        self.assertEqual(list(servers), ["metricool-nyx"])
        self.assertIn("--strict-mcp-config", cmd)
        # createScheduledPost IS approved for the flow, but the read-only probe must not carry it
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "mcp__metricool-nyx__getBrandSettings")

    def test_probe_cache_is_invalidated_when_the_approved_scope_changes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "probe.json"
            fresh = {"status": "PERMISSION_DENIED_UNATTENDED", "checked_at": ps05_tick._now_iso(),
                     "allowed_tools_configured": []}                 # measured under the OLD (empty) scope
            path.write_text(json.dumps(fresh))
            with mock.patch.object(ps05_tick, "NYX_PROBE_PATH", path), mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(stdout="", stderr="", returncode=0)
                ps05_tick._probe_metricool_nyx()                    # not forced, cache is <24h old
                self.assertTrue(run.called)                         # ...but stale for the new scope -> re-probed
                run.reset_mock()
                ps05_tick._probe_metricool_nyx()                    # now cached under the current scope
                self.assertFalse(run.called)


REAL_DENIED_STREAM = "\n".join(json.dumps(e) for e in [
    {"type": "system", "subtype": "init", "mcp_servers": [{"name": "metricool-nyx", "status": "connected"}]},
    {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "..."}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "ToolSearch", "input": {}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a",
        "content": [{"type": "tool_reference", "tool_name": "mcp__metricool-nyx__getBrandSettings"}]}]}},
    {"type": "rate_limit_event"},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "b",
        "name": "mcp__metricool-nyx__getBrandSettings", "input": {}}]}},
    # the event shape that crashed the live run: `message` is a plain string
    {"type": "system", "subtype": "permission_denied",
     "message": "Claude requested permissions to use mcp__metricool-nyx__getBrandSettings, but you haven't granted it yet."},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "b", "is_error": True,
        "content": "Claude requested permissions to use mcp__metricool-nyx__getBrandSettings, but you haven't granted it yet."}]}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "not executed"}]}},
    {"type": "system", "subtype": "post_turn_summary"},
    {"type": "result", "subtype": "success", "permission_denials": [{"tool_name": "mcp__metricool-nyx__getBrandSettings"}]},
])


class RealStreamShapes(unittest.TestCase):
    def test_real_denied_stream_does_not_crash_and_reports_gate_not_auth(self):
        r = ps05_tick._parse_probe_stream(REAL_DENIED_STREAM)
        self.assertEqual(r["status"], "PERMISSION_DENIED_UNATTENDED")
        self.assertEqual(r["mcp_server_status"], "connected")     # authenticated; only the tool gate blocks

    def test_garbage_and_string_events_never_raise(self):
        junk = "\n".join(['not json', '"a string"', '[1,2]', json.dumps({"message": "s"}),
                          json.dumps({"type": "assistant", "message": "s"}),
                          json.dumps({"type": "user", "message": {"content": "str"}})])
        self.assertEqual(ps05_tick._parse_probe_stream(junk)["status"], "TOOL_NOT_CALLED")

    def test_probe_crash_cannot_abort_main(self):
        with mock.patch.object(ps05_tick, "_probe_metricool_nyx", side_effect=ValueError("boom")):
            with mock.patch.object(ps05_tick.st, "load", return_value=None):
                pass  # main() with no venture exits early; the guard is exercised by the source check below
        src = Path(ps05_tick.__file__).read_text()
        guarded = src.split("probe = _probe_metricool_nyx()")[0].rstrip().endswith("try:")
        self.assertTrue(guarded)


class ManagerActionClassification(unittest.TestCase):
    def test_skipped_brief_is_not_an_external_action(self):
        # the exact shape from the live 2026-09-19 run: dispatched but skipped
        ext, internal, skipped = ps05_tick._split_manager_actions(
            [{"action": "run_content_brief", "detail": "skipped: 4 usable item(s)", "made_progress": False}])
        self.assertEqual((ext, internal, skipped), ([], [], ["run_content_brief"]))
        self.assertEqual(ps05_tick._classify_outcome(external_actions=ext, blockers=[]), "checked")

    def test_internal_work_is_not_external_and_generation_is(self):
        ext, internal, skipped = ps05_tick._split_manager_actions([
            {"action": "run_sales_audit", "made_progress": True},
            {"action": "run_content_generation", "made_progress": True},
            {"action": "no_action_needed", "made_progress": True}])
        self.assertEqual((ext, internal, skipped),
                         (["run_content_generation"], ["run_sales_audit"], ["no_action_needed"]))

    def test_old_shape_without_made_progress_never_claims_progress(self):
        self.assertEqual(ps05_tick._split_manager_actions([{"action": "run_content_brief"}])[0], [])


if __name__ == "__main__":
    unittest.main()
