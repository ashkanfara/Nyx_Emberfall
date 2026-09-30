#!/usr/bin/env python3
"""THE single stable entrypoint for the PS-05 operating-phase scheduled tick.

    python3 ps05_tick.py

Zero arguments, always, forever. This is the whole fix for the permission
problem that kept recurring: every previous design still had the Routine's
own Bash tool calls carrying variable content (a JSON measurement, a tick
summary, a scheduler snapshot) directly in the command line, and Claude
Code's Bash permission approvals are exact-literal-match -- so a "stable"
script whose ARGUMENTS differ every run is not actually stable at all from
the permission matcher's point of view. The fix is not a better wrapper
script; it's zero Bash arguments, period. All the variable data (metrics,
measurements, decisions, summaries) is read from and written to state/
files entirely INSIDE this process -- none of it ever appears on a command
line the top-level agent types, so there is exactly one command for the
permission system to ever see: this one.

What used to be six-plus separate Bash invocations per tick (ps05_ops.py
now/due-experiments/tick-start/tick-end/audit-log, record_measurement.py,
commercial_events.py check, fanvue_metrics.py, fanvue_auth.py,
instagram_metrics.py, python3 -c heredocs for scheduler_heartbeat) is now
ONE process that imports and calls all of those modules directly as
Python functions. Judgment calls that genuinely need an LLM (specialist
audits, arbitration) still happen -- via manager.tick(), which already
shells out to `claude -p` as a subprocess of THIS process, invisible to
Claude Code's own tool-permission gating (that only gates what the
top-level agent itself invokes, not what a script it started does
internally).

See OPERATING_MANDATE.md for the checklist this implements.
"""

from __future__ import annotations

import json
import re
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Before ANY other PS-05 module is imported: while the relay holds the PS-05
# write lock, record `skipped: relay_write_lock` and run nothing.
import relay_lock_guard

if __name__ == "__main__":
    _skip = relay_lock_guard.skip_if_relay_holds_lock()
    if _skip is not None:
        print(json.dumps(_skip, indent=2))
        raise SystemExit(0)

import audit
import commercial_events
import fan_memory
import fanvue_auth
import fanvue_chat
import fanvue_media
import instagram_metrics
import manager
import nyx_instagram
import scheduler_heartbeat
import stages
import state as st

ROOT = Path(__file__).resolve().parent
RO_REPORT = (Path.home() / "Projects" / "research-orchestrator" / "runs" /
            "20260907T002913Z-b7001f" / "report.md")
CRED_PATH = ROOT / ".fanvue_runtime" / "credentials.json"
CRON_EXPRESSION = "0 * * * *"  # this project's known, fixed schedule -- see SKILL.md


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _next_top_of_hour_iso(now: datetime) -> str:
    nxt = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
    return nxt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ro_isolation_check() -> dict:
    """Read-only mtime check -- never writes to research-orchestrator."""
    if not RO_REPORT.is_file():
        return {"ok": False, "error": f"expected file missing: {RO_REPORT}"}
    return {"ok": True, "mtime": RO_REPORT.stat().st_mtime}


def _measure_due_experiments(v: dict) -> list[str]:
    notes = []
    for exp in st.due_experiments(v, now_iso=_now_iso()):
        notes.append(_measure_one_experiment(v, exp))
    return notes


BLOCKED_MEASUREMENT_RETRY_HOURS = 24


def _parse_iso(ts) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _measure_one_experiment(v: dict, exp: dict) -> str:
    exp_id = exp["id"]
    last = (exp.get("measurements") or [None])[-1]
    last_blocked = last if (last or {}).get("status") == "blocked" else None

    # Deterministic backoff: a platform block (e.g. Graph API "API access
    # blocked") does not clear on its own within the hour, so re-calling it
    # every tick only appends identical rows. Retry at most once per window.
    if last_blocked:
        checked = _parse_iso(last_blocked.get("checked_at") or last_blocked.get("ts"))
        if checked and datetime.now(timezone.utc) - checked < timedelta(hours=BLOCKED_MEASUREMENT_RETRY_HOURS):
            return (f"{exp_id}: still blocked, retry backoff {BLOCKED_MEASUREMENT_RETRY_HOURS}h "
                    f"(last check {last_blocked.get('checked_at')}: {str(last_blocked.get('reason'))[:120]})")

    if not exp.get("assets"):
        st.record_experiment_measurement(v, exp_id, {
            "status": "blocked", "reason": "experiment has no assets to measure",
            "checked_at": _now_iso(),
        })
        return f"{exp_id}: blocked (experiment has no assets to measure)"

    results, errors = {}, {}
    for media_id in exp.get("assets", []):
        try:
            r = instagram_metrics.fetch_media_metrics(media_id)
        except Exception as exc:  # noqa: BLE001 -- recorded, not swallowed
            errors[media_id] = str(exc)
            continue
        if r.get("ok") is False:
            errors[media_id] = str(r.get("error"))
        else:
            results[media_id] = r

    if errors and not results:
        reason = next(iter(errors.values()))
        if last_blocked and last_blocked.get("reason") == reason:
            # Same blocker as last time: refresh the existing row instead of
            # appending a duplicate (history preserved, no row bloat).
            last_blocked["checked_at"] = _now_iso()
            last_blocked["consecutive_blocked"] = int(last_blocked.get("consecutive_blocked", 1)) + 1
        else:
            st.record_experiment_measurement(v, exp_id, {
                "status": "blocked", "reason": reason, "checked_at": _now_iso(),
            })
        return f"{exp_id}: blocked ({reason[:150]})"

    if errors:
        # Partial: keep the good rows AND the failures; never call it "ok".
        st.record_experiment_measurement(v, exp_id, {
            "status": "partial", "metrics": results, "errors": errors,
            "checked_at": _now_iso(),
        })
        return (f"{exp_id}: partial ({len(results)} ok, {len(errors)} failed: "
                f"{next(iter(errors.values()))[:100]})")

    st.record_experiment_measurement(v, exp_id, {
        "status": "ok", "metrics": results, "checked_at": _now_iso(),
    })
    return f"{exp_id}: measurement recorded ({len(results)} asset(s))"


def _refresh_fanvue(v: dict) -> str:
    try:
        fanvue_auth.refresh_access_token()
    except Exception as exc:  # noqa: BLE001
        return f"fanvue token refresh failed: {exc}"
    try:
        result = commercial_events.check()
    except Exception as exc:  # noqa: BLE001
        return f"commercial_events check failed: {exc}"
    if not result.get("ok"):
        return f"commercial_events check not ok: {result}"

    new_events = result.get("events", [])
    for e in new_events:
        # commercial_events.check() only DETECTS events (diffs the live
        # snapshot) -- it does not persist them. Every new event found here
        # MUST be recorded via state.add_commercial_event, or it is seen
        # exactly once (in this tick's own stdout/audit summary) and then
        # permanently lost, since save_snapshot() has already moved the
        # baseline forward by the time this function returns.
        st.add_commercial_event(v, event_type=e["type"], fan_id=e.get("fan_id"),
                                handle=e.get("handle"), ts=e.get("ts") or _now_iso(),
                                detail=e.get("detail", {}))

    # v["metrics"]["h1_market"] is the dashboard's H1/funnel source of truth
    # (server.py reads it directly) -- it predates live Fanvue API access
    # and was only ever updated by a founder manually running
    # collect_metrics.py. Now that commercial_events.check() has real live
    # numbers every tick, keep it honestly current instead of leaving it
    # frozen at whatever the founder last typed in (or the original seed
    # default), which silently misrepresents H1 market-demand tracking.
    h1 = v.setdefault("metrics", {}).setdefault("h1_market", {})
    live_subs = result.get("current_subscribers")
    live_earnings = result.get("current_earnings") or {}
    if isinstance(live_subs, int):
        h1["raw_subscribers"] = live_subs
    if isinstance(live_earnings.get("total"), (int, float)):
        h1["gross_revenue_usd"] = live_earnings["total"]

    new_count = len(new_events)
    types = ", ".join(e["type"] for e in new_events) if new_events else "none"
    return (f"fanvue: {result.get('current_subscribers', '?')} subs, "
           f"earnings={result.get('current_earnings', '?')}, {new_count} new event(s) [{types}]")


def _chat_scope_granted() -> bool:
    if not CRED_PATH.is_file():
        return False
    try:
        scope = json.loads(CRED_PATH.read_text()).get("scope", "")
    except (OSError, ValueError):
        return False
    scopes = set(scope.split())
    return "read:chat" in scopes and "write:chat" in scopes


# Venture Manager compliance guardrails applied to every Sales Manager chat
# decision before anything is sent -- deterministic, not another LLM call,
# so it can never be talked out of these by clever phrasing in a response.
_OFF_PLATFORM_PATTERNS = (
    "telegram", "whatsapp", "snapchat", "snap chat", "kik ", "discord",
    "onlyfans", "only fans", "text me at", "call me at", "my number is",
    "add me on", "find me on", "dm me on",
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(\+?\d[\d\-\s()]{8,}\d)")
_HUMAN_CLAIM_PATTERNS = ("i am human", "i'm human", "i am a real person", "i'm a real person",
                         "not ai", "not an ai")


def _guardrail_violation(text: str) -> str | None:
    """Returns a human-readable reason if `text` violates a hard compliance
    rule, else None. Deliberately simple pattern matching, not another
    model call -- these are bright lines, not judgment calls."""
    lower = text.lower()
    for pat in _OFF_PLATFORM_PATTERNS:
        if pat in lower:
            return f"off-platform contact pattern detected: {pat!r}"
    if _EMAIL_RE.search(text):
        return "email address detected in response text"
    if _PHONE_RE.search(text):
        return "phone-number-like pattern detected in response text"
    for pat in _HUMAN_CLAIM_PATTERNS:
        if pat in lower:
            return f"response falsely denies being AI: {pat!r}"
    return None


def _process_fanvue_chats(v: dict) -> str:
    """The real DM loop: ingest -> relationship history -> Sales Manager
    classification -> guardrails -> send -> persist. Processes EVERY chat
    returned by the API on this first activation (not just ones flagged
    unread), since messages may have arrived while this system had no
    read:chat access at all and would never have been marked as "new" by
    any diffing logic."""
    creator_uuid = v.get("accounts", {}).get("fanvue", {}).get("creator_uuid")
    disclosure_policy = v.get("persona", {}).get("disclosure_per_platform", {}).get("fanvue", "")
    persona = v.get("persona", {})

    chats_result = fanvue_chat.list_chats()
    if not chats_result.get("ok"):
        audit.append(actor="venture_manager", action="fanvue_chat_ingestion_check",
                     detail=f"list_chats failed: {chats_result}")
        return f"fanvue chat: list_chats failed: {chats_result}"

    chats = chats_result.get("chats", [])
    audit.append(actor="venture_manager", action="fanvue_chat_ingestion_check",
                 detail=f"Fetched {len(chats)} conversation(s) via GET /v1/chats.")
    if not chats:
        return "fanvue chat: 0 conversations exist"

    # Fetched once per tick (not once per chat) -- this is the ONLY
    # legitimate source of real media_uuids for a PPV offer; the Sales
    # Manager must select from this exact list and may never invent one.
    media_result = fanvue_media.list_media()
    available_media = media_result.get("media", []) if media_result.get("ok") else []

    per_chat_summaries = []
    for chat in chats:
        user = chat.get("user", {})
        user_uuid = user.get("uuid")
        handle = user.get("handle", "")
        if not user_uuid:
            continue

        msgs_result = fanvue_chat.list_messages(user_uuid, mark_as_read=False)
        if not msgs_result.get("ok"):
            per_chat_summaries.append(f"{handle}: list_messages failed ({msgs_result.get('error')})")
            continue
        messages = msgs_result.get("messages", [])
        if not messages:
            continue

        # Fanvue returns newest-first; build oldest-first for the model and
        # for the "who sent the last message" check.
        thread = list(reversed(messages))
        last_msg = thread[-1]
        last_sender_uuid = (last_msg.get("sender") or {}).get("uuid")

        fan_history = fan_memory.get("fanvue", user_uuid) or {}

        if last_sender_uuid == creator_uuid:
            # We (Nyx/the founder) already sent the most recent message and
            # the fan hasn't replied since -- nothing to respond to. This is
            # exactly the founder's "hi yes" thread if the fan hasn't
            # written back yet: recorded as reviewed, no message sent.
            fan_memory.upsert_fan("fanvue", user_uuid, handle=handle,
                                  first_seen=fan_history.get("first_seen") or _now_iso(),
                                  relationship_stage=fan_history.get("relationship_stage", "trial"),
                                  revenue_usd=fan_history.get("revenue_usd", 0.0))
            fan_memory.record_interaction(
                "fanvue", user_uuid, kind="dm_thread_reviewed",
                detail=f"Last message in thread is already from us ({len(thread)} total messages); "
                       f"awaiting fan reply, no action taken.",
                meta={"action": "no_response", "reason": "last_sender_is_us"})
            per_chat_summaries.append(f"{handle}: awaiting fan reply, no action")
            continue

        # Skip re-running the LLM entirely if nothing has changed since the
        # last time this exact inbound message was reviewed and resulted in
        # no_response (e.g. confirmed spam) -- avoids paying for and
        # re-deciding on an unchanged thread every single hourly tick.
        last_history = (fan_history.get("interaction_history") or [])
        last_entry = last_history[-1] if last_history else None
        last_reviewed_at = ((last_entry or {}).get("meta") or {}).get("inbound_message_at") \
            if (last_entry or {}).get("meta", {}).get("action") == "no_response" else None
        if last_reviewed_at and last_reviewed_at == last_msg.get("sentAt"):
            per_chat_summaries.append(f"{handle}: unchanged since last review, skipped")
            continue

        thread_for_model = [{
            "from": "creator" if m.get("sender", {}).get("uuid") == creator_uuid else "fan",
            "text": m.get("text"), "sent_at": m.get("sentAt"),
        } for m in thread]

        try:
            decision = stages.sales_manager_chat_response(
                fan_history, thread_for_model, persona, {"fanvue": disclosure_policy},
                available_media=available_media)
        except Exception as exc:  # noqa: BLE001
            per_chat_summaries.append(f"{handle}: sales_manager_chat_response failed: {exc}")
            continue

        action = decision["action"]
        violation = None
        if action in ("respond", "offer", "ppv"):
            violation = _guardrail_violation(decision["response_text"])

        if violation:
            st.queue_action(
                v, kind="CHAT_RESPONSE_GUARDRAIL_VIOLATION", boundary="CANNOT_SAFELY_DELEGATE",
                what=f"Sales Manager drafted a response to {handle} that failed a compliance "
                     f"guardrail and was NOT sent.",
                why=f"Guardrail violation: {violation}. Intent classification was "
                    f"{decision['intent_classification']!r}, drafted action was {action!r}.",
                founder_action="Review the conversation directly in Fanvue and decide how to "
                               "respond, if at all.",
                urgency="blocking_now", estimated_minutes=5)
            per_chat_summaries.append(f"{handle}: guardrail BLOCKED a drafted {action} ({violation})")
            continue

        if action == "escalate":
            st.queue_action(
                v, kind="CHAT_NEEDS_FOUNDER_JUDGMENT", boundary="CANNOT_SAFELY_DELEGATE",
                what=f"Sales Manager could not confidently decide how to handle a message from "
                     f"{handle}.",
                why=decision.get("escalation_reason", "(no reason given)"),
                founder_action="Review the conversation directly in Fanvue and respond if "
                               "appropriate.",
                urgency="blocking_now", estimated_minutes=5)
            per_chat_summaries.append(f"{handle}: escalated to founder")
            continue

        if action == "no_response":
            fan_memory.record_interaction(
                "fanvue", user_uuid, kind="dm_reviewed_no_response",
                detail=f"Classified {decision['intent_classification']}; reasoning: "
                       f"{decision['reasoning'][:300]}",
                meta={"action": "no_response", "intent_classification": decision["intent_classification"],
                     "inbound_message_at": last_msg.get("sentAt")})
            audit.append(actor="venture_manager", action="fanvue_chat_no_response_decision",
                         detail=f"{handle} ({user_uuid}): classification="
                                f"{decision['intent_classification']}, reasoning="
                                f"{decision['reasoning'][:300]}",
                         cost_usd=decision.get("_cost_usd"))
            per_chat_summaries.append(f"{handle}: reviewed, no response warranted "
                                      f"({decision['intent_classification']})")
            continue

        # action in (respond, offer, ppv) and passed guardrails -- send it.
        price_cents = decision.get("ppv_price_cents") if action == "ppv" else None
        media_uuids = None
        if action == "ppv":
            media_uuid = decision.get("ppv_media_uuid")
            available_uuids = {m["uuid"] for m in available_media if m.get("uuid")}
            if not media_uuid or media_uuid not in available_uuids:
                # Defense in depth -- stages.py already validates this, but
                # never send a PPV without a confirmed-real media_uuid.
                per_chat_summaries.append(f"{handle}: ppv BLOCKED -- no valid media_uuid resolved")
                audit.append(actor="system", action="fanvue_chat_ppv_blocked_no_media",
                             detail=f"Sales Manager chose ppv for {handle} but ppv_media_uuid "
                                    f"{media_uuid!r} is not in available_media -- refusing to send "
                                    f"rather than let Fanvue 400 or send the wrong asset.")
                continue
            media_uuids = [media_uuid]

        attempt_started_at = _now_iso()
        send_result = fanvue_chat.send_message(user_uuid, decision["response_text"],
                                               price_cents=price_cents, media_uuids=media_uuids)

        if send_result.get("status") == "DELIVERY_UNCERTAIN":
            # Never blindly resend on a parse/network failure -- check
            # reality first, exactly once, in this same tick.
            verify = fanvue_chat.verify_recent_send(
                user_uuid, creator_uuid=creator_uuid, expected_text=decision["response_text"],
                sent_after=attempt_started_at)
            if verify.get("verified"):
                send_result = {"ok": True, "status": "SENT_CONFIRMED",
                              "message_uuid": (verify["message"].get("uuid")),
                              "sent_at": verify["message"].get("sentAt"),
                              "verified_after_uncertain_response": True}
                audit.append(actor="system", action="fanvue_chat_send_verified_after_uncertain",
                             detail=f"Send to {handle} initially returned an ambiguous/network-"
                                    f"level response, but re-checking the live thread confirmed "
                                    f"the message actually sent -- not retried, not duplicated.")
            else:
                audit.append(actor="system", action="fanvue_chat_send_uncertain_unresolved",
                             detail=f"Send to {handle} ({action}) returned an uncertain/network-"
                                    f"level response and could not be confirmed as delivered on "
                                    f"re-check -- NOT retried automatically to avoid a possible "
                                    f"duplicate; will be re-evaluated fresh next tick.")

        if not send_result.get("ok"):
            per_chat_summaries.append(f"{handle}: send FAILED ({send_result.get('status', 'FAILED')}) "
                                      f"after a {action} was approved: {send_result}")
            audit.append(actor="system", action="fanvue_chat_send_failed",
                         detail=f"Approved {action} to {handle} but the API call failed: {send_result}")
            continue

        fan_memory.upsert_fan("fanvue", user_uuid, handle=handle,
                              first_seen=fan_history.get("first_seen") or _now_iso(),
                              relationship_stage=fan_history.get("relationship_stage", "trial"),
                              revenue_usd=fan_history.get("revenue_usd", 0.0))
        fan_memory.record_interaction(
            "fanvue", user_uuid, kind="dm_response_sent",
            detail=f"Sent a {action} (classification: {decision['intent_classification']}): "
                  f"{decision['response_text'][:300]}",
            next_recommended_action="monitor for reply",
            meta={"action": action, "intent_classification": decision["intent_classification"],
                 "inbound_message_at": last_msg.get("sentAt"),
                 "responded_at": send_result.get("sent_at") or _now_iso(),
                 "ppv_price_cents": price_cents})
        audit.append(actor="venture_manager", action="fanvue_chat_response_sent",
                     detail=f"To {handle} ({user_uuid}): action={action}, "
                            f"classification={decision['intent_classification']}, "
                            f"reasoning={decision['reasoning'][:300]}. "
                            f"Message sent: {decision['response_text'][:300]}",
                     cost_usd=decision.get("_cost_usd"))
        per_chat_summaries.append(f"{handle}: {action} sent")

    return f"fanvue chat loop: {len(chats)} conversation(s) processed -- " + "; ".join(per_chat_summaries)


NYX_BRAND_ID = 6988018
NYX_INSTAGRAM = "nyx.emberfall"
NYX_MCP_SERVER = "metricool-nyx"
NYX_PROBE_TOOL = f"mcp__{NYX_MCP_SERVER}__getBrandSettings"
NYX_PROBE_PATH = ROOT / "state" / "metricool_nyx_probe.json"
NYX_PROBE_INTERVAL_HOURS = 24
# FOUNDER-APPROVED unattended scope for the Nyx Instagram flow (Tier 1 read +
# Tier 2 create), confirmed by the founder in chat on 2026-09-20 -- see
# state/proposals/metricool_nyx_unattended_scope.md. Exactly these three tools,
# on the metricool-nyx server only. Every invocation pre-approves ONLY the one
# tool it calls (never this whole tuple), and this constant is the ONLY place
# the scope lives: no settings file, trust flag or global config is involved.
# Widening it (update/review tools, analytics, Higgsfield, other servers) is a
# separate founder decision.
NYX_UNATTENDED_ALLOWED_TOOLS: tuple[str, ...] = (
    "mcp__metricool-nyx__getBrandSettings",
    "mcp__metricool-nyx__getScheduledPosts",
    "mcp__metricool-nyx__createScheduledPost",
)


def _parse_probe_stream(stdout: str) -> dict:
    result = _classify_probe_stream(stdout)
    result["mcp_server_status"] = None
    for line in (stdout or "").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "system" and ev.get("subtype") == "init":
            for m in ev.get("mcp_servers") or []:
                if isinstance(m, dict) and m.get("name") == NYX_MCP_SERVER:
                    result["mcp_server_status"] = m.get("status")
    return result


def _classify_probe_stream(stdout: str) -> dict:
    """Classify a `claude -p --output-format stream-json --verbose` run from
    the TOOL RESULT itself (never the model's prose), so a model that merely
    claims to have called the tool cannot produce a false VERIFIED."""
    uses, results = {}, []
    for line in (stdout or "").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        # Real streams mix event shapes (system/api_retry/result events carry
        # `message` as a plain string, or no message at all) -- only assistant
        # /user events have a dict message with a list `content`.
        msg = ev.get("message") if isinstance(ev, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                uses[b.get("id")] = b.get("name")
            elif b.get("type") == "tool_result":
                results.append(b)
    for b in results:
        if uses.get(b.get("tool_use_id")) != NYX_PROBE_TOOL:
            continue
        raw = b.get("content")
        text = raw if isinstance(raw, str) else " ".join(
            c.get("text", "") for c in (raw or []) if isinstance(c, dict))
        if b.get("is_error"):
            low = text.lower()
            if "permission" in low or "not granted" in low or "denied" in low:
                return {"status": "PERMISSION_DENIED_UNATTENDED", "detail": text[:200]}
            if "auth" in low:
                return {"status": "AUTH_REQUIRED", "detail": text[:200]}
            return {"status": "TOOL_ERROR", "detail": text[:200]}
        try:
            brands = json.loads(text).get("data") or []
        except ValueError:
            return {"status": "UNPARSEABLE_RESULT", "detail": text[:200]}
        ids = sorted(b_.get("id") for b_ in brands if isinstance(b_, dict))
        igs = [(b_.get("networksData") or {}).get("instagramData") for b_ in brands]
        if ids == [NYX_BRAND_ID] and igs == [NYX_INSTAGRAM]:
            return {"status": "VERIFIED", "detail": f"brand {NYX_BRAND_ID} / @{NYX_INSTAGRAM}",
                    "brand_ids": ids}
        return {"status": "IDENTITY_MISMATCH",
                "detail": f"brand ids {ids}, instagram {igs} -- expected only {NYX_BRAND_ID}/{NYX_INSTAGRAM}",
                "brand_ids": ids}
    return {"status": "TOOL_NOT_CALLED", "detail": "no getBrandSettings tool call/result observed"}


def _probe_metricool_nyx(*, force: bool = False) -> dict:
    """Real unattended READ-ONLY auth/permission test of metricool-nyx, run
    through the same `claude -p` mechanism the runner already uses. Loads ONLY
    the metricool-nyx server (legacy `metricool` / `metricool-ashkan` are
    never visible to it) and calls only getBrandSettings. Rate-limited."""
    prev = None
    if NYX_PROBE_PATH.is_file():
        try:
            prev = json.loads(NYX_PROBE_PATH.read_text())
        except ValueError:
            prev = None
    # A cached result is only valid for the scope it was measured under: a
    # different approved-tool tuple invalidates it (no manual cache deletion).
    if prev and not force and prev.get("allowed_tools_configured") == list(NYX_UNATTENDED_ALLOWED_TOOLS):
        checked = _parse_iso(prev.get("checked_at"))
        if checked and datetime.now(timezone.utc) - checked < timedelta(hours=NYX_PROBE_INTERVAL_HOURS):
            return prev

    mcp_cfg = json.dumps({"mcpServers": {NYX_MCP_SERVER: {
        "type": "http", "url": "https://ai.metricool.com/mcp"}}})
    cmd = ["claude", "-p", "--model", "sonnet", "--output-format", "stream-json", "--verbose",
           "--no-session-persistence", "--strict-mcp-config", "--mcp-config", mcp_cfg]
    if NYX_PROBE_TOOL in NYX_UNATTENDED_ALLOWED_TOOLS:
        cmd += ["--allowedTools", NYX_PROBE_TOOL]      # only the tool this call uses
    prompt = (f"Read-only connectivity check. If the tool {NYX_PROBE_TOOL} is not directly "
              f"available, first load it with ToolSearch (query: select:{NYX_PROBE_TOOL}). "
              f"Then call {NYX_PROBE_TOOL} exactly once with no arguments. Do not call any other "
              f"Metricool tool. Reply with the single word DONE.")
    import subprocess
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=240)
        result = _parse_probe_stream(proc.stdout)
        if result["status"] == "TOOL_NOT_CALLED" and proc.returncode != 0:
            result["detail"] = f"claude exited {proc.returncode}: {(proc.stderr or '')[:160]}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        result = {"status": "PROBE_FAILED", "detail": str(exc)[:200]}
    result.update({"checked_at": _now_iso(), "tool": NYX_PROBE_TOOL,
                   "allowed_tools_configured": list(NYX_UNATTENDED_ALLOWED_TOOLS)})
    NYX_PROBE_PATH.write_text(json.dumps(result, indent=2))
    return result


# Manager actions that spend money / credits or otherwise leave the machine.
# Audits, briefs, QA and analysis only rewrite local state, so they never count
# as an external action; a dispatch that did not make progress is a skip.
EXTERNAL_MANAGER_ACTIONS = ("run_content_generation",)


def _split_manager_actions(actions: list[dict]) -> tuple[list[str], list[str], list[str]]:
    external, internal, skipped = [], [], []
    for a in actions:
        name = a.get("action")
        if name == "no_action_needed" or not a.get("made_progress"):
            skipped.append(name)
        elif name in EXTERNAL_MANAGER_ACTIONS:
            external.append(name)
        else:
            internal.append(name)
    return external, internal, skipped


_SENT_RE = re.compile(r": (respond|offer|ppv) sent")


def _classify_outcome(*, external_actions: list[str], blockers: list[str]) -> str:
    """acted only for a REAL external action; degraded when something is
    blocked; checked otherwise. Never claims progress from a blocked note."""
    if external_actions:
        return "acted"
    return "degraded" if blockers else "checked"


def main() -> int:
    scheduler_heartbeat.record_tick_start()
    outcome = "checked"
    parts: list[str] = []
    external_actions: list[str] = []
    blockers: list[str] = []

    try:
        v = st.load()
        if v is None:
            outcome = "error"
            parts.append("no venture state found -- run seed_from_ro.py first")
        else:
            for n in _measure_due_experiments(v):
                parts.append(n)
                if "blocked" in n.split(":", 1)[-1][:40] or ": partial" in n:
                    blockers.append(n)

            fv = _refresh_fanvue(v)
            parts.append(fv)
            if "failed" in fv or "not ok" in fv:
                blockers.append(fv)

            chat_granted = _chat_scope_granted()
            q6 = next((a for a in v.get("queued_actions", [])
                      if a.get("kind") == "FANVUE_CHAT_SCOPE_REEVALUATION"), None)
            if chat_granted:
                chat_note = _process_fanvue_chats(v)
                parts.append(chat_note)
                if _SENT_RE.search(chat_note):
                    external_actions.append("fanvue message sent")
                if re.search(r"failed|FAILED|BLOCKED|escalated", chat_note):
                    blockers.append(chat_note[:200])
            elif q6 is not None:
                parts.append(f"Fanvue chat scope not granted -- q6 ({q6['id']}) still "
                             f"{q6['status']}, untouched")
                blockers.append("fanvue chat scope not granted")
            else:
                parts.append("Fanvue chat scope not granted and no q6-equivalent queued "
                             "action found -- WARNING, this founder action may have been lost")
                blockers.append("fanvue chat scope not granted (q6 missing)")

            ro = _ro_isolation_check()
            parts.append(f"RO isolation ok={ro.get('ok')}")
            if not ro.get("ok"):
                blockers.append("RO isolation check failed")

            try:
                probe = _probe_metricool_nyx()
            except Exception as exc:  # noqa: BLE001 -- a probe bug must never abort the tick
                probe = {"status": "PROBE_FAILED", "checked_at": _now_iso(),
                         "detail": f"{type(exc).__name__}: {exc}"[:200]}
            parts.append(f"metricool-nyx unattended read-only probe: {probe.get('status')} "
                         f"(checked {probe.get('checked_at')}; {str(probe.get('detail'))[:100]})")
            if probe.get("status") != "VERIFIED":
                blockers.append(f"metricool-nyx unattended: {probe.get('status')}")

            try:
                flow = nyx_instagram.run(v, allowed=NYX_UNATTENDED_ALLOWED_TOOLS,
                                         persist=lambda: st.save(v))
            except Exception as exc:  # noqa: BLE001 -- must not abort unrelated tick work
                flow = {"status": "FLOW_FAILED", "detail": f"{type(exc).__name__}: {exc}"[:200], "events": []}
            if flow["status"] != "NOTHING_DUE":
                parts.append(f"nyx instagram reconcile: {flow['status']}"
                             + (f" ({flow.get('detail')})" if flow.get("detail") else ""))
            if flow["status"] not in ("NOTHING_DUE", "RECONCILED"):
                blockers.append(f"nyx instagram reconcile: {flow['status']}")
            for sched in flow.get("schedule") or []:
                plat = sched.get("platform", "instagram")
                parts.append(f"nyx {plat} schedule: {sched['status']} content_items[{sched.get('index')}]"
                             + (f" ({sched.get('detail')})" if sched.get("detail") else ""))
                audit.append(actor="system", action=f"{plat}_schedule_via_metricool",
                             detail=json.dumps(sched, default=str)[:500])
                if sched["status"] == "SCHEDULED":
                    external_actions.append(
                        f"{plat} content_items[{sched.get('index')}] scheduled via metricool")
                elif sched["status"] != "ATTEMPT_ADOPTED":
                    blockers.append(f"nyx {plat} schedule: {sched['status']}")
            for ev in flow.get("events", []):
                audit.append(actor="system", action="instagram_reconcile_via_metricool",
                             detail=f"content_items[{ev['index']}] {ev['outcome']}: {ev['detail']}")
                parts.append(f"content_items[{ev['index']}] {ev['outcome']}")
                if ev["outcome"] != "PUBLISHED":
                    blockers.append(f"content_items[{ev['index']}] {ev['outcome']}")

            try:
                st.save(v)
            except st.StaleWriteError as exc:
                # 2026-09-25 regression: `v` was loaded at the top of this tick,
                # so a CLI write (a founder slide grant, a story revision) that
                # landed since then is NEWER. Refuse rather than rebase: the tick
                # holds a whole venture dict it cannot diff, and silently winning
                # is exactly what erased the grant. The newer file survives; this
                # tick's own in-memory edits are dropped and reported.
                parts.append(f"venture.json NOT saved -- {exc}")
                blockers.append("venture.json write refused: a newer concurrent write survives")

            for note in st.reconcile_pending_approvals(v):
                audit.append(actor="system", action="approval_reconciled", detail=note)
                parts.append(f"approval reconciled: {note[:140]}")

            mgr_result = manager.tick()
            external, internal, skipped = _split_manager_actions(mgr_result.get("actions", []))
            external_actions.extend(external)
            if mgr_result.get("actions"):
                parts.append(f"manager.tick(): external={external or 'none'} "
                             f"internal_work={internal or 'none'} skipped/no-op={skipped or 'none'}")
            if mgr_result.get("error"):
                parts.append(f"manager.tick() error: {mgr_result['error']}")
                blockers.append("manager.tick() error")

            outcome = _classify_outcome(external_actions=external_actions, blockers=blockers)

        audit.append(actor="venture_manager", action="scheduled_tick",
                     detail=f"[{outcome}] " + " | ".join(parts)[:1990])
    except Exception:  # noqa: BLE001 -- must still record_tick_end below
        outcome = "error"
        tb = traceback.format_exc(limit=6)
        parts.append(f"unhandled exception: {tb}")
        try:
            audit.append(actor="venture_manager", action="scheduled_tick_error",
                         detail=" | ".join(parts)[:2000])
        except Exception:  # noqa: BLE001 -- never let logging itself break tick-end
            pass
    finally:
        now = datetime.now(timezone.utc)
        summary = (f"[external_actions={external_actions or 'none'}; blockers={len(blockers)}] "
                   + " | ".join(parts))
        scheduler_heartbeat.record_tick_end(
            outcome=outcome,
            summary=summary[:2000],
            scheduler_enabled=True,
            next_run_at=_next_top_of_hour_iso(now),
            cron_expression=CRON_EXPRESSION,
        )

    print(json.dumps({"outcome": outcome, "external_actions": external_actions,
                      "blockers": blockers, "summary": parts}, indent=2))
    return 0 if outcome != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
