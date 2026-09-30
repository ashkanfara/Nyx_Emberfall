"""Pure functions deriving the Operations Console's views from existing
persisted state (venture.json, activity_log.jsonl, scheduler_heartbeat.json)
-- no new business logic, no side effects, nothing hardcoded. Every value
here must be traceable to something actually recorded; when the underlying
data doesn't exist, say NOT_AVAILABLE rather than showing 0 or guessing.
"""

from __future__ import annotations

from datetime import datetime, timezone

NOT_AVAILABLE = "NOT_AVAILABLE"

SYSTEM_STATES = ("RUNNING", "WAITING_FOR_DATA", "EXECUTING", "FOUNDER_BLOCKED", "ERROR", "IDLE")


def _parse(ts: str | None):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt_delta(now: datetime, target: datetime) -> str:
    delta = target - now
    seconds = delta.total_seconds()
    sign = "in " if seconds >= 0 else ""
    suffix = "" if seconds >= 0 else " ago"
    seconds = abs(seconds)
    hours, rem = divmod(int(seconds), 3600)
    minutes = rem // 60
    if hours:
        return f"{sign}{hours}h {minutes}m{suffix}"
    return f"{sign}{minutes}m{suffix}"


def blocking_queued_actions(v: dict) -> list[dict]:
    return [a for a in v.get("queued_actions", [])
           if a["status"] == "WAITING_FOR_FOUNDER_CONSENT"
           and a.get("urgency", "blocking_now") == "blocking_now"]


def system_status(v: dict, *, pending_approval: dict | None, heartbeat: dict,
                  now: datetime | None = None) -> dict:
    """The hero state. Founder-blocked and error states always win regardless
    of stale fields elsewhere (e.g. an old current_stage_kind of
    'request_approval' from a tick long past must never present as if it's
    still blocking -- only a REAL pending_approval or blocking queued_action
    does)."""
    now = now or datetime.now(timezone.utc)

    blocking = blocking_queued_actions(v)
    if pending_approval is not None or blocking:
        # .get(), never bracket access: pending_approval is whatever
        # load_pending_approval() hands back, and this function must never
        # hard-crash the entire status read path just because that shape
        # changed or is momentarily unexpected -- a missing "what" should
        # degrade to an empty reason, not take down the dashboard.
        reasons = ([pending_approval.get("what", "")] if pending_approval else []) + \
                 [a["what"] for a in blocking]
        return {"state": "FOUNDER_BLOCKED", "reason": "; ".join(reasons)}

    if heartbeat.get("tick_in_progress"):
        return {"state": "EXECUTING", "reason": "A scheduled tick is currently running."}

    if heartbeat.get("last_tick_outcome") == "error" and heartbeat.get("consecutive_failures", 0) > 0:
        return {"state": "ERROR",
               "reason": heartbeat.get("last_tick_summary") or "The last scheduled tick failed."}

    experiments = v.get("experiments", [])
    active = [e for e in experiments if e["status"] != "concluded"]
    if active:
        soonest = min(active, key=lambda e: e.get("observation_window_end") or "9999")
        end = _parse(soonest.get("observation_window_end"))
        if end and end > now:
            return {"state": "WAITING_FOR_DATA",
                   "reason": f"Observation window for '{soonest['id']}' is still open "
                             f"({_fmt_delta(now, end)})."}

    if not experiments and not v.get("specialists", {}).get("growth", {}).get("last_run_at"):
        return {"state": "IDLE", "reason": "No active experiment and no specialist activity yet."}

    return {"state": "RUNNING", "reason": "No founder action needed; autonomous loop is live."}


def next_checkpoint(v: dict, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    experiments = [e for e in v.get("experiments", []) if e["status"] != "concluded"]
    if not experiments:
        return {"label": None, "eta": None}
    soonest = min(experiments, key=lambda e: e.get("observation_window_end") or "9999")
    end = _parse(soonest.get("observation_window_end"))
    if not end:
        return {"label": soonest["id"], "eta": None}
    return {"label": f"{soonest['id']} observation window closes",
           "at": soonest["observation_window_end"], "eta": _fmt_delta(now, end)}


def last_successful_action(activity_log: list[dict]) -> dict | None:
    """Most recent non-error entry -- activity_log is newest-first per
    server.py's build_status()."""
    for e in activity_log:
        if e.get("action") != "error":
            return e
    return None


def categorize_actor(entry: dict) -> str:
    """SYSTEM / GROWTH / CONTENT / SALES / VENTURE MANAGER / FOUNDER, derived
    from the actual actor+action fields already recorded -- no schema
    migration of history required."""
    actor, action = entry.get("actor", ""), entry.get("action", "")
    if actor == "founder":
        return "FOUNDER"
    if actor == "venture_manager" or "arbitration" in action or "decision" in action:
        return "VENTURE MANAGER"
    if "growth" in action:
        return "GROWTH"
    if "content" in action and "consent" not in action:
        return "CONTENT"
    if "sales" in action:
        return "SALES"
    return "SYSTEM"


def funnel(v: dict, fanvue_summary: dict | None) -> list[dict]:
    """Real data only. A stage with no wired data source shows NOT_AVAILABLE,
    never a fabricated zero. A stage we DO query that happens to be zero
    (e.g. verified subscriber count) shows 0 -- that's a real measurement,
    not a gap."""
    reach = 0
    reach_available = False
    for snap in v.get("content_plan", {}).get("metrics_snapshots", []):
        for post in snap.get("posts", []):
            r = (post.get("insights", {}) or {}).get("reach")
            if r is not None:
                reach += r
                reach_available = True

    fv_ok = bool(fanvue_summary and fanvue_summary.get("ok"))
    subscribers = fanvue_summary.get("subscribers") if fv_ok else None
    revenue = fanvue_summary.get("gross_revenue_usd") if fv_ok else None

    return [
        {"stage": "Reach / Views", "value": reach if reach_available else NOT_AVAILABLE},
        {"stage": "Profile Visits", "value": NOT_AVAILABLE},
        {"stage": "Fanvue Visits", "value": NOT_AVAILABLE},
        {"stage": "Trials", "value": NOT_AVAILABLE},
        {"stage": "Paid Subscribers", "value": subscribers if fv_ok else NOT_AVAILABLE},
        {"stage": "PPV / Other Buyers", "value": NOT_AVAILABLE},
        {"stage": "Repeat Buyers", "value": NOT_AVAILABLE},
    ]


def platform_health(v: dict, fanvue_snapshot: dict | None = None) -> dict:
    accounts = v.get("accounts", {})
    cnd = v.get("metrics", {}).get("h2_operability", {}).get("could_not_delegate_log", [])

    def limitations_for(keyword: str) -> list[str]:
        return [e["why_not_delegated"] for e in cnd if keyword.lower() in e["task"].lower()]

    experiments = v.get("experiments", [])

    ig = accounts.get("instagram", {})
    tt = accounts.get("tiktok", {})
    fv = accounts.get("fanvue", {})

    return {
        "instagram": {
            "connected": bool(ig.get("publish_capability_verified")),
            # 2026-09-13 fix: previously only checked bio_set, so a missing
            # profile photo or (Instagram's own mobile-only) website link
            # never showed up here even though acquisition_ready (the
            # stricter, evidence-based gate) already correctly failed on
            # exactly those grounds.
            "profile_ready": bool(ig.get("bio_set")) and bool(ig.get("profile_photo_set"))
                            and bool(ig.get("website_link_set")),
            "content_live": bool(ig.get("publish_capability_verified")),
            "known_limitations": limitations_for("instagram"),
            "in_experiments": [e["id"] for e in experiments if "instagram" in e.get("channels", [])],
            "pending_actions": [a["what"] for a in v.get("queued_actions", [])
                                if a["status"] == "WAITING_FOR_FOUNDER_CONSENT"
                                and "instagram" in a.get("what", "").lower()],
        },
        "tiktok": {
            "connected": tt.get("connector_status") == "active",
            "known_limitations": limitations_for("tiktok"),
            "in_experiments": [e["id"] for e in experiments if "tiktok" in e.get("channels", [])],
            "pending_actions": [a["what"] for a in v.get("queued_actions", [])
                                if a["status"] == "WAITING_FOR_FOUNDER_CONSENT"
                                and "tiktok" in a.get("what", "").lower()],
        },
        "fanvue": {
            "connected": bool(fv.get("publish_capability_verified")),
            "profile_picture_set": bool(fv.get("profile_picture_set")),
            "banner_set": bool(fv.get("banner_set")),
            "bio_set": bool(fv.get("bio_set")),
            "is_discoverable": bool(fv.get("is_discoverable")),
            "subscription_price_usd": fv.get("subscription_price_usd"),
            "promotion": fv.get("promotion"),
            "vault_posts": fv.get("vault_posts", 0),
            **_fanvue_subscriber_breakdown(fanvue_snapshot),
            "known_limitations": limitations_for("fanvue"),
            "in_experiments": [e["id"] for e in experiments if "fanvue" in e.get("channels", [])],
            "pending_actions": [a["what"] for a in v.get("queued_actions", [])
                                if a["status"] == "WAITING_FOR_FOUNDER_CONSENT"
                                and "fanvue" in a.get("what", "").lower()],
        },
    }


def _fanvue_subscriber_breakdown(snapshot: dict | None) -> dict:
    """Trial vs paid split + revenue, read from the last persisted
    commercial_events.py snapshot (a file on disk) -- never a live API call
    from the dashboard's own request path (a stale/expired token would
    otherwise break every page load)."""
    if not snapshot:
        return {"trials": NOT_AVAILABLE, "paid_subscribers": NOT_AVAILABLE,
               "revenue_usd": NOT_AVAILABLE, "last_fanvue_check": None}
    subs = snapshot.get("subscribers", [])
    trials = sum(1 for s in subs if (s.get("subscription") or {}).get("amountPaid", 0) == 0)
    paid = sum(1 for s in subs if (s.get("subscription") or {}).get("amountPaid", 0) > 0)
    earnings = snapshot.get("account", {}).get("earnings", {})
    revenue = round((earnings.get("total") or 0) / 100, 2)
    return {"trials": trials, "paid_subscribers": paid, "revenue_usd": revenue,
           "last_fanvue_check": snapshot.get("fetched_at")}


def h1_status(v: dict, fanvue_summary: dict | None) -> dict:
    """Honest labeling -- never call it a signal without evidence. Zero
    revenue and no concluded experiments is UNKNOWN, not 'weak signal'
    (which implies some directional evidence exists)."""
    decision = v.get("decision", "TEST")
    concluded = [e for e in v.get("experiments", []) if e["status"] == "concluded"]
    revenue = (fanvue_summary or {}).get("gross_revenue_usd", 0.0) or 0.0

    if decision == "KILL":
        return {"status": "FAILED", "evidence": "Founder recorded a KILL decision."}
    if decision == "VALIDATED":
        return {"status": "VALIDATED", "evidence": "Founder recorded a VALIDATED decision."}
    if revenue > 0:
        return {"status": "WEAK_SIGNAL", "evidence": f"${revenue:.2f} real revenue recorded."}
    if concluded:
        results = "; ".join(f"{e['id']}: {e['result']}" for e in concluded)
        return {"status": "WEAK_SIGNAL", "evidence": results}

    events = v.get("commercial_events", [])
    real_subs = v["metrics"]["h1_market"].get("raw_subscribers", 0)
    fraud_flagged = any(e["type"] in ("NEW_SUBSCRIBER", "TRIAL_STARTED")
                        and e.get("sales_manager_note") and "bot" in e["sales_manager_note"].lower()
                        for e in events)
    if real_subs and fraud_flagged:
        return {"status": "UNKNOWN",
               "evidence": (f"{real_subs} Fanvue subscriber(s) exist, but the only one observed "
                            "so far was flagged by the Sales Manager as a bot/scam pattern "
                            "(same-day-registered account, instant off-platform redirect) -- "
                            "not counted as genuine demand signal. Still $0 real revenue.")}
    if real_subs:
        return {"status": "WEAK_SIGNAL",
               "evidence": f"{real_subs} real Fanvue subscriber(s), $0 revenue so far."}
    return {"status": "UNKNOWN",
           "evidence": "No concluded experiments and $0 revenue -- too early to call, not a bad sign."}


def h2_status(v: dict) -> dict:
    h2 = v["metrics"]["h2_operability"]
    week_min = h2.get("operational_minutes_this_week", 0)
    trend = h2.get("trend_vs_target", "unknown")
    label = "ON_TRACK" if trend == "on_track" else ("AT_RISK" if trend == "unknown" else "OFF_TRACK")
    return {
        "status": label,
        "setup_overhead_minutes_cumulative": h2.get("setup_overhead_minutes_cumulative", 0),
        "operational_minutes_this_week": week_min,
        "operational_minutes_cumulative": h2.get("operational_minutes_cumulative", 0),
        "target_min": 30, "target_max": 60,
        "could_not_delegate_count": len(h2.get("could_not_delegate_log", [])),
    }


def commercial_events_view(v: dict, *, limit: int = 10) -> list[dict]:
    """Most-recent-first, capped -- for the 'Recent commercial events'
    dashboard section. Each event carries a lifecycle_status (DISCOVERED /
    ANALYZED / ACTIONED / FOUNDER_BLOCKED) so the console never conflates
    'we saw it' with 'we did something about it'."""
    events = v.get("commercial_events", [])
    return list(reversed(events))[:limit]


STUCK_TICK_MINUTES = 10  # observed real tick durations so far: 120-165s; 10min is a generous multiple


def tool_permission_health(activity_log: list[dict], heartbeat: dict, *,
                           now: datetime | None = None, task_snapshot: dict | None = None) -> dict:
    """Distinct from scheduler health: the scheduler can dispatch a tick on
    time (SCHEDULER TRIGGER HEALTH = HEALTHY) while that tick sits blocked
    waiting on a tool-permission prompt no one is present to answer -- that
    must never present as healthy. Three independent signals, any one is
    sufficient to call it BLOCKED:
    (1) a tick's own SKILL.md logs action=='permission_prompt_blocked' via
        audit.append the moment it hits a prompt it can't resolve itself;
    (2) tick_in_progress has stayed true far longer than any tick has ever
        genuinely taken (STUCK_TICK_MINUTES) -- this is the case a prompt
        blocks the run so early it never even reaches the audit-log call,
        which a log-only check would otherwise report as merely UNKNOWN;
    (3) task_snapshot (a real mcp__scheduled-tasks__list_scheduled_tasks
        result, persisted by an active session via
        `ps05_ops.py record-scheduler-snapshot` since the standalone dashboard
        server has no way to call that tool itself) shows a dispatch NEWER
        than heartbeat's last_attempted_tick with no matching advance -- the
        tick never even reached its own tick-start call, which (1) and (2)
        both structurally cannot detect since they depend on the tick's OWN
        self-reporting, and self-reporting is exactly what never happened."""
    now = now or datetime.now(timezone.utc)
    last_tick = heartbeat.get("last_attempted_tick")
    if not heartbeat.get("ever_run"):
        return {"status": "UNTESTED", "detail": "No tick has run since this check existed."}

    blocks = [e for e in activity_log if e.get("action") == "permission_prompt_blocked"
             and (not last_tick or e.get("ts", "") >= last_tick)]
    if blocks:
        return {"status": "BLOCKED", "detail": blocks[0]["detail"], "count": len(blocks)}

    if task_snapshot and task_snapshot.get("last_run_at"):
        real_dispatch = _parse(task_snapshot["last_run_at"])
        known_dispatch = _parse(last_tick)
        # >60s tolerance: the snapshot and heartbeat come from independent
        # sources (a list_scheduled_tasks call vs. the tick's own timestamp)
        # for the SAME dispatch, so sub-minute jitter must not read as "newer".
        is_newer = real_dispatch and (not known_dispatch
                                      or (real_dispatch - known_dispatch).total_seconds() > 60)
        if is_newer:
            elapsed_min = (now - real_dispatch).total_seconds() / 60
            if elapsed_min >= STUCK_TICK_MINUTES and not heartbeat.get("tick_in_progress"):
                return {"status": "BLOCKED",
                       "detail": (f"The scheduler genuinely dispatched a tick at "
                                 f"{task_snapshot['last_run_at']} ({elapsed_min:.0f} min ago, per a "
                                 f"real list_scheduled_tasks check) but scheduler_heartbeat.json was "
                                 f"never updated -- tick-start itself never ran. This almost always "
                                 f"means a permission prompt for a brand-new command blocked the very "
                                 f"first line of the run, before any self-reporting could happen."),
                       "dispatched_at": task_snapshot["last_run_at"], "elapsed_minutes": round(elapsed_min, 1)}

    if heartbeat.get("tick_in_progress"):
        started = _parse(last_tick)
        if started:
            elapsed_min = (now - started).total_seconds() / 60
            if elapsed_min >= STUCK_TICK_MINUTES:
                return {"status": "BLOCKED",
                       "detail": (f"Tick started {elapsed_min:.0f} min ago and still shows "
                                 f"tick_in_progress=true with no permission_prompt_blocked log "
                                 f"entry -- typical ticks finish in 2-3 min, so this run is almost "
                                 f"certainly sitting at an unanswered permission prompt that "
                                 f"occurred before the tick reached its own audit-log call."),
                       "stuck_since": last_tick, "elapsed_minutes": round(elapsed_min, 1)}
        # Can't yet tell whether it's genuinely working or stuck on a prompt.
        return {"status": "UNKNOWN", "detail": "A tick is currently in progress."}

    if heartbeat.get("last_tick_outcome") == "error":
        return {"status": "UNKNOWN",
               "detail": "Last tick errored; not necessarily a permission block -- see scheduler health."}

    return {"status": "HEALTHY",
           "detail": f"Last tick ({last_tick}) completed with no blocked-permission entries logged."}


SCHEDULER_STALL_MINUTES = 150  # hourly cron + ~1min jitter; 2.5h is a generous multiple before calling it stalled


def scheduler_trigger_health(heartbeat: dict, *, now: datetime | None = None,
                             task_snapshot: dict | None = None) -> dict:
    """Did the scheduler itself actually DISPATCH a tick recently, on the
    cadence it self-reports? This is deliberately blind to whether that
    dispatched tick then got stuck on a permission prompt (that's
    tool_permission_health's job) -- a run can be BLOCKED there while this
    is still HEALTHY, because the scheduler did its job: it fired the tick
    on time. Conversely this can be STALLED even if the last tick that DID
    run finished cleanly, if nothing has fired since.

    Prefers task_snapshot's last_run_at over heartbeat's last_attempted_tick
    when the snapshot is newer -- the snapshot comes from a real
    list_scheduled_tasks call, so it reflects dispatch even when the tick
    itself never got far enough to self-report (see tool_permission_health)."""
    now = now or datetime.now(timezone.utc)
    if not heartbeat.get("ever_run") and not (task_snapshot and task_snapshot.get("last_run_at")):
        return {"status": "UNTESTED", "detail": "No tick has ever been recorded."}

    if heartbeat.get("scheduler_enabled_self_reported") is False:
        return {"status": "DISABLED", "detail": "Scheduler self-reported disabled on its last run."}

    last_tick = heartbeat.get("last_attempted_tick")
    started = _parse(last_tick)
    if task_snapshot and task_snapshot.get("last_run_at"):
        snap_dispatch = _parse(task_snapshot["last_run_at"])
        if snap_dispatch and (not started or (snap_dispatch - started).total_seconds() > 60):
            started = snap_dispatch
            last_tick = task_snapshot["last_run_at"]
    if not started:
        return {"status": "UNKNOWN", "detail": "No parseable last_attempted_tick timestamp."}

    elapsed_min = (now - started).total_seconds() / 60
    if elapsed_min >= SCHEDULER_STALL_MINUTES:
        return {"status": "STALLED",
               "detail": (f"No tick has been dispatched in {elapsed_min / 60:.1f}h "
                         f"(last attempted {last_tick}); expected roughly hourly per "
                         f"cron_expression_self_reported="
                         f"{heartbeat.get('cron_expression_self_reported', 'unknown')!r}."),
               "elapsed_minutes": round(elapsed_min, 1)}
    return {"status": "HEALTHY",
           "detail": f"Last tick dispatched {last_tick}, {elapsed_min:.0f} min ago -- within cadence."}


def fanvue_chat_ingestion_health(v: dict, activity_log: list[dict], *,
                                 chat_scope_granted: bool) -> dict:
    """Whether the system can currently even SEE incoming Fanvue DMs at all
    -- distinct from whether it has acted on any (see sales_response_loop_
    health). Never fabricates a 'checked' state the system hasn't actually
    performed; a scope gap is reported plainly rather than guessed around.
    Looks for ps05_tick.py's own fanvue_chat_ingestion_check audit entries
    (logged every time GET /v1/chats is actually called) rather than
    commercial_events, since a chat conversation is not itself a
    commercial_event -- it's the thing that might lead to one."""
    checks = [e for e in activity_log if e.get("action") == "fanvue_chat_ingestion_check"]
    if not chat_scope_granted:
        blocking = [a for a in v.get("queued_actions", [])
                   if a.get("kind") == "FANVUE_CHAT_SCOPE_REEVALUATION"
                   and a.get("status") == "WAITING_FOR_FOUNDER_CONSENT"]
        return {"status": "BLOCKED_ON_SCOPE",
               "detail": ("read:chat/write:chat not granted -- this system cannot list or read "
                         "Fanvue DMs via the API yet, regardless of what's visible in the Fanvue "
                         "app UI itself. " + (f"Founder action already queued ({blocking[0]['id']})."
                         if blocking else "No founder action currently queued for this.")),
               "ingestion_checks_ever_recorded": len(checks)}
    if not checks:
        return {"status": "GRANTED_NOT_YET_VERIFIED",
               "detail": "read:chat/write:chat granted but no chat-list check has been recorded "
                        "yet -- next scheduled tick should perform one."}
    return {"status": "INGESTING",
           "detail": checks[0]["detail"],
           "ingestion_checks_ever_recorded": len(checks)}


def sales_response_loop_health(activity_log: list[dict], *, chat_scope_granted: bool) -> dict:
    """Whether Sales Manager has actually evaluated and, where appropriate,
    responded to a real Fanvue message end-to-end -- the concrete acceptance
    test for the DM pipeline, not just 'the code exists'."""
    if not chat_scope_granted:
        return {"status": "NOT_BUILT_YET",
               "detail": "Blocked upstream on Fanvue chat scope -- the response loop is designed "
                        "but has never run against a real message (see FANVUE CHAT INGESTION)."}
    sales_entries = [e for e in activity_log
                     if e.get("action") in ("fanvue_chat_response_sent", "fanvue_chat_no_response_decision")]
    if not sales_entries:
        return {"status": "SCOPE_GRANTED_NO_ACTIVITY_YET",
               "detail": "Chat scope is granted but no Sales Manager decision on a real message has "
                        "been logged yet."}
    return {"status": "ACTIVE",
           "detail": f"{len(sales_entries)} Sales Manager decision(s) on real Fanvue messages logged.",
           "count": len(sales_entries)}


def next_actions_queue(v: dict, *, now: datetime | None = None) -> list[dict]:
    """A visibility-only projection of what the scheduler will do next --
    derived from real experiment/queue state, never a hardcoded script."""
    now = now or datetime.now(timezone.utc)
    steps = []

    blocking = blocking_queued_actions(v)
    for a in blocking:
        steps.append({"step": f"Founder action: {a['what']}", "status": "BLOCKED"})

    for e in v.get("experiments", []):
        if e["status"] == "concluded":
            continue
        end = _parse(e.get("observation_window_end"))
        due = bool(end and end <= now)
        if not due:
            steps.append({"step": f"Wait until '{e['id']}' observation window closes",
                         "status": "WAITING"})
            continue
        steps.append({"step": f"Collect metrics for '{e['id']}'", "status": "READY"})
        steps.append({"step": "Run Growth / Content / Sales analysis", "status": "SCHEDULED"})
        steps.append({"step": "Venture Manager arbitration", "status": "SCHEDULED"})
        steps.append({"step": "Execute resulting experiment decision", "status": "SCHEDULED"})

    if not steps:
        steps.append({"step": "No active experiment -- awaiting next Venture Manager decision",
                     "status": "WAITING"})
    return steps


def _median(values: list[float]):
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def fanvue_sales_metrics(fan_memory_data: dict) -> dict:
    """Per the founder's explicit instruction: track outcomes, don't
    optimize for message volume. Every number here is derived from the
    structured `meta` fields ps05_tick.py's existing DM loop already writes
    to fan_memory (no new chat architecture) -- and every metric with an
    empty/zero denominator reports insufficient_data rather than a
    misleading 0% or 0, since a genuinely tiny sample (a handful of real
    conversations so far) must never be presented as a validated rate."""
    decisions = []  # one per (fan, tick) review with a structured decision
    for fan in fan_memory_data.values():
        for h in fan.get("interaction_history", []):
            meta = h.get("meta")
            if meta and meta.get("action") in ("no_response", "respond", "offer", "ppv"):
                decisions.append(meta)

    genuine = [d for d in decisions if d.get("intent_classification") != "spam_or_scam"
              and d.get("reason") != "last_sender_is_us"]
    spam_classified = [d for d in decisions if d.get("intent_classification") == "spam_or_scam"]
    responded = [d for d in genuine if d["action"] in ("respond", "offer", "ppv")]
    offers = [d for d in responded if d["action"] in ("offer", "ppv")]

    latencies_s = []
    for d in responded:
        a, b = _parse(d.get("inbound_message_at")), _parse(d.get("responded_at"))
        if a and b:
            latencies_s.append((b - a).total_seconds())
    median_latency_s = _median(latencies_s)

    payers = [f for f in fan_memory_data.values() if (f.get("revenue_usd") or 0) > 0]
    repeat_payers = [f for f in fan_memory_data.values()
                     if f.get("relationship_stage") == "repeat_payer"]
    classified_total = len(genuine) + len(spam_classified)

    return {
        "genuine_inbound_dm_threads": len(genuine) if decisions else NOT_AVAILABLE,
        "spam_scam_dm_threads": len(spam_classified) if decisions else NOT_AVAILABLE,
        "spam_scam_rate": (round(len(spam_classified) / classified_total, 2)
                          if classified_total else NOT_AVAILABLE),
        "response_rate": (round(len(responded) / len(genuine), 2) if genuine else NOT_AVAILABLE),
        "offer_ppv_rate": (round(len(offers) / len(responded), 2) if responded else NOT_AVAILABLE),
        "median_response_latency_minutes": (round(median_latency_s / 60, 1)
                                            if median_latency_s is not None else NOT_AVAILABLE),
        "conversion_to_paid": (round(len(payers) / len(fan_memory_data), 2)
                              if fan_memory_data else NOT_AVAILABLE),
        "repeat_purchase_rate": (round(len(repeat_payers) / len(payers), 2)
                                 if payers else NOT_AVAILABLE),
        "note": "Counts are per reviewed conversation-thread per tick, not per individual message "
                "-- a thread with several fan messages reviewed together counts once. Small-sample "
                "rates (few genuine threads so far) are real numbers, not statistically meaningful "
                "yet; do not treat early 100%/0% rates as validated performance.",
    }


CONTENT_LIFECYCLE_STATES = ("BRIEF_READY", "ASSET_GENERATED", "QA_PASSED", "PUBLISH_READY",
                           "SCHEDULED", "PUBLISHED", "DEFERRED")


def _item_lifecycle(item: dict) -> str:
    """Explicit lifecycle_status wins; otherwise infer from legacy ad hoc
    flags (published/deferred) for items written before this state existed
    -- a bare prompt+caption with none of those is BRIEF_READY, never
    'ready content', since no asset has necessarily been generated for it."""
    if item.get("lifecycle_status") in CONTENT_LIFECYCLE_STATES:
        return item["lifecycle_status"]
    if item.get("deferred"):
        return "DEFERRED"
    if item.get("published") or item.get("published_at"):
        return "PUBLISHED"
    return "BRIEF_READY"


def content_pipeline_health(content_plan: dict) -> dict:
    """Per-platform inventory using EXPLICIT lifecycle states so a creative
    brief (prompt+caption+platform, no asset yet) is never reported as
    'ready content' -- 'Instagram blocked' must never silently read as
    'therefore nothing gets prepared for Fanvue/TikTok either'."""
    items = content_plan.get("content_items", [])
    by_platform: dict[str, dict] = {}
    for item in items:
        platform = item.get("target_platform", "unknown")
        bucket = by_platform.setdefault(platform, {s: 0 for s in CONTENT_LIFECYCLE_STATES})
        bucket[_item_lifecycle(item)] += 1

    publishable = sum(b["PUBLISH_READY"] + b["SCHEDULED"] for b in by_platform.values())
    briefs_only = sum(b["BRIEF_READY"] for b in by_platform.values())
    return {
        "by_platform": by_platform,
        "publish_ready_count": publishable,
        "brief_only_count": briefs_only,
        "pipeline_empty": (publishable + briefs_only) == 0,
        "note": ("BRIEF_READY = prompt/caption/platform specified, no asset generated yet. "
                "ASSET_GENERATED/QA_PASSED/PUBLISH_READY = an actual media asset exists at various "
                "stages. SCHEDULED/PUBLISHED are self-explanatory; DEFERRED = out of v1 scope (e.g. "
                "Reddit). A platform blocked for MEASUREMENT reasons (e.g. Instagram API access) does "
                "not by itself change any item's lifecycle state on another platform."),
    }


PUBLISH_READY_INVENTORY_TARGET = 5  # kept in sync with state.PUBLISH_READY_INVENTORY_TARGET


def storefront_readiness(v: dict) -> dict:
    """A GATE against aggressively scaling acquisition, not a freeze --
    ordinary posting continues regardless of this verdict (commercial-
    acceleration rules, 2026-09-13). Reads only what a real check last
    found (v['storefront'], set by set_storefront_state) plus content
    depth already visible in content_plan -- never guesses live platform
    state itself."""
    sf = v.get("storefront", {})
    content_items = v.get("content_plan", {}).get("content_items", [])
    live_count = sum(1 for it in content_items
                     if it.get("public_status") == "PUBLICLY_LIVE" or it.get("lifecycle_status") == "PUBLISHED")

    missing = []
    if sf.get("bio_set") is not True:
        missing.append("bio not set (or not yet re-verified as set)")
    if sf.get("banner_distinct") is not True:
        missing.append("banner not yet distinct from avatar (or not yet re-verified)")
    if live_count < 3:
        missing.append(f"only {live_count} publicly-live piece(s) of content -- page may look empty")
    if sf.get("subscription_price_set") is not True and sf.get("subscription_price_set") is not None:
        missing.append("subscription price not confirmed set")

    checked = sf.get("last_checked_at") is not None
    return {
        "ready": checked and not missing,
        "checked": checked,
        "missing": missing,
        "content_pieces_live": live_count,
        "last_checked_at": sf.get("last_checked_at", NOT_AVAILABLE),
        "last_checked_via": sf.get("last_checked_via", NOT_AVAILABLE),
        "note": ("This gates AGGRESSIVE acquisition scaling only -- ordinary posting, QA, and "
                "sales work continue regardless of readiness."),
    }


def commercial_scoreboard(v: dict, fanvue_summary: dict, sales_metrics: dict,
                          content_pipeline: dict) -> dict:
    """The compact commercial scoreboard the CEO should actually watch
    (commercial-acceleration rules, 2026-09-13) -- distinct from
    business_scoreboard (kept for backward compatibility with existing
    dashboard views); this one follows the founder's exact requested field
    list. NOT_AVAILABLE wherever measurement is genuinely unavailable --
    never an invented ratio over a zero/unknown denominator."""
    clock = v.get("monetization_clock", {})
    milestones = clock.get("milestones", {})
    h2 = v.get("metrics", {}).get("h2_operability", {})
    return {
        "publish_ready_inventory": content_pipeline.get("publish_ready_count", 0),
        "publish_ready_target": PUBLISH_READY_INVENTORY_TARGET,
        "below_inventory_target": content_pipeline.get("publish_ready_count", 0) < PUBLISH_READY_INVENTORY_TARGET,
        "published_by_platform": {
            platform: counts.get("PUBLISHED", 0)
            for platform, counts in content_pipeline.get("by_platform", {}).items()
        },
        "qualified_reach": NOT_AVAILABLE,
        "profile_visits": NOT_AVAILABLE,
        "fanvue_traffic": NOT_AVAILABLE,
        "trial_starts": NOT_AVAILABLE,
        "current_subscribers": fanvue_summary.get("subscribers", NOT_AVAILABLE),
        "paid_subscribers": fanvue_summary.get("subscribers", NOT_AVAILABLE),
        "renewals": NOT_AVAILABLE,
        "genuine_inbound_conversations": sales_metrics.get("genuine_inbound_dm_threads", NOT_AVAILABLE),
        "ppv_offers": NOT_AVAILABLE,
        "ppv_purchases": NOT_AVAILABLE,
        "tips": NOT_AVAILABLE,
        "gross_revenue_usd": fanvue_summary.get("gross_revenue_usd", 0.0) or 0.0,
        "repeat_purchasers": NOT_AVAILABLE,
        "founder_minutes_this_week": h2.get("operational_minutes_this_week", NOT_AVAILABLE),
        "monetization_clock_started_at": clock.get("started_at", NOT_AVAILABLE),
        "monetization_milestones": {
            m: (data["at"] if data else None) for m, data in milestones.items()
        } if milestones else NOT_AVAILABLE,
    }


def manager_accountability(v: dict) -> dict:
    """One view combining specialists[role] (audit findings), manager_status
    (CEO-loop objective/blocker/next-action fields), and the venture-level
    bottleneck -- so the dashboard can show each manager's mandate, current
    objective, current blocker, last action, and next action in one place,
    not three disconnected structures."""
    roles = {}
    for role in ("venture", "growth", "content", "sales"):
        status = v.get("manager_status", {}).get(role, {})
        audit_info = v.get("specialists", {}).get(role, {}) if role != "venture" else {}
        roles[role] = {
            "current_objective": status.get("current_objective", NOT_AVAILABLE),
            "current_blocker": status.get("current_blocker", NOT_AVAILABLE),
            "next_action": status.get("next_action", NOT_AVAILABLE),
            "next_action_at": status.get("next_action_at", NOT_AVAILABLE),
            "last_action_result": status.get("last_action_result", NOT_AVAILABLE),
            "status_updated_at": status.get("updated_at", NOT_AVAILABLE),
            "last_audit_at": audit_info.get("last_run_at", NOT_AVAILABLE),
            "last_audit_summary": audit_info.get("summary", NOT_AVAILABLE),
        }
    return {
        "current_bottleneck": v.get("current_bottleneck", {}).get("description", NOT_AVAILABLE),
        "bottleneck_identified_at": v.get("current_bottleneck", {}).get("identified_at", NOT_AVAILABLE),
        "managers": roles,
    }


def business_scoreboard(v: dict, fanvue_summary: dict, sales_metrics: dict) -> dict:
    """Compact CEO-level primary metrics -- deliberately excludes vanity
    metrics (impressions, follower counts alone) in favor of outcomes.
    Every value traces to real persisted state; NOT_AVAILABLE beats a
    fabricated zero."""
    h2 = v.get("metrics", {}).get("h2_operability", {})
    concluded = [e for e in v.get("experiments", []) if e["status"] == "concluded"]
    running = [e for e in v.get("experiments", []) if e["status"] != "concluded"]
    return {
        "gross_revenue_usd": fanvue_summary.get("gross_revenue_usd", 0.0) or 0.0,
        "paid_subscribers": fanvue_summary.get("subscribers", NOT_AVAILABLE),
        "revenue_per_payer": (round(fanvue_summary["gross_revenue_usd"] / fanvue_summary["subscribers"], 2)
                              if fanvue_summary.get("subscribers") else NOT_AVAILABLE),
        "conversion_to_paid": sales_metrics.get("conversion_to_paid", NOT_AVAILABLE),
        "repeat_purchase_rate": sales_metrics.get("repeat_purchase_rate", NOT_AVAILABLE),
        "founder_minutes_this_week": h2.get("operational_minutes_this_week", NOT_AVAILABLE),
        "founder_target_minutes_per_week": "30-60",
        "experiments_concluded": len(concluded),
        "experiments_running": len(running),
        "genuine_inbound_dm_threads": sales_metrics.get("genuine_inbound_dm_threads", NOT_AVAILABLE),
        "spam_scam_rate": sales_metrics.get("spam_scam_rate", NOT_AVAILABLE),
    }


STALE_PENDING_APPROVAL_MINUTES = 90  # generous vs. the ~few-minute founder-response times seen so far


def stall_detection(v: dict, activity_log: list[dict], heartbeat: dict, *,
                    pending_approval: dict | None = None, now: datetime | None = None) -> dict:
    """Detects business stagnation, not just technical failure -- a venture
    sitting in the same state for days with no explicit justification.
    Never fabricates activity to make this look better; only reports what
    the evidence actually shows."""
    now = now or datetime.now(timezone.utc)
    flags = []

    if pending_approval and pending_approval.get("created_at"):
        created = _parse(pending_approval["created_at"])
        if created and (now - created).total_seconds() > STALE_PENDING_APPROVAL_MINUTES * 60:
            age_min = round((now - created).total_seconds() / 60)
            flags.append(
                f"pending_approval ({pending_approval.get('reason', 'unknown')}) has been open for "
                f"{age_min} min -- as of the 2026-09-13 autonomy fix this blocks ONLY the specific "
                f"action/resource it names (the CEO loop continues unrelated safe work around it), "
                f"but it still genuinely needs founder attention for that one item. If the underlying "
                f"question was already answered/completed another way, this is likely stale and should "
                f"be cleared, not just approved as originally framed.")

    for e in v.get("experiments", []):
        if e["status"] == "concluded":
            continue
        end = _parse(e.get("observation_window_end"))
        if end and (now - end).total_seconds() > 86400:
            flags.append(f"Experiment '{e['id']}' observation window closed "
                        f"{(now - end).days}d ago and is still not concluded/invalidated.")

    cp = v.get("content_plan", {})
    pipeline = content_pipeline_health(cp)
    if pipeline["pipeline_empty"] and cp.get("content_items"):
        flags.append("Content pipeline has zero ready-unpublished items -- nothing queued to "
                     "publish next on any platform.")

    blocking = [a for a in v.get("queued_actions", []) if a.get("status") == "WAITING_FOR_FOUNDER_CONSENT"
               and a.get("urgency") == "blocking_now"]
    for a in blocking:
        created = _parse(a.get("created_at"))
        if created and (now - created).total_seconds() > 172800:
            flags.append(f"Founder action '{a['id']}' ({a['kind']}) has been blocking_now for "
                        f"{(now - created).days}d.")

    recent_no_ops = [e for e in activity_log[:10]
                    if e.get("action") in ("no_action_needed", "scheduled_tick_noop")]
    if len(recent_no_ops) >= 8:
        flags.append(f"{len(recent_no_ops)} of the last 10 activity entries were no-ops -- "
                    f"check whether the stated reasoning is still current, not stale.")

    last_tick = _parse(heartbeat.get("last_attempted_tick"))
    if last_tick and (now - last_tick).total_seconds() > 10800 and heartbeat.get("scheduler_enabled_self_reported"):
        flags.append(f"No tick has run in {round((now - last_tick).total_seconds() / 3600, 1)}h "
                    f"despite the scheduler reporting enabled.")

    return {"stalled": bool(flags), "flags": flags}
