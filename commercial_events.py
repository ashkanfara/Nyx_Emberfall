#!/usr/bin/env python3
"""Fanvue commercial event detection: fetches live state via the existing
API integration and diffs it against the previous persisted snapshot to
produce typed events. Only event types backed by real, currently-queryable
data are ever emitted -- never fabricated, never inferred from unavailable
fields.

    python3 commercial_events.py check
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import fanvue_runtime

API_BASE = "https://api.fanvue.com"
SNAPSHOT_PATH = Path(__file__).resolve().parent / "state" / "fanvue_snapshot.json"

EVENT_TYPES = ("NEW_SUBSCRIBER", "TRIAL_STARTED", "TRIAL_CONVERTED",
              "SUBSCRIPTION_CANCELLED_CONFIRMED", "TRIAL_EXPIRED",
              "SUBSCRIBER_DISAPPEARED_UNKNOWN", "ACCOUNT_DELETED_CONFIRMED",
              "NEW_COMMENT", "NEW_LIKE", "TIP")
# ACCOUNT_DELETED_CONFIRMED is not currently auto-detected by detect_events()
# below (the /v1/subscribers diff alone can't distinguish it from an
# ordinary lapse) -- it is only ever set when a message-thread read
# independently confirms Fanvue's own '<handle>_deleted_<timestamp>'
# rewrite, which is real evidence, never inferred from disappearance alone.


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _get(path: str, token: str) -> dict:
    req = urllib.request.Request(f"{API_BASE}{path}")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_live_state() -> dict:
    """Real, independently-queried Fanvue state -- never founder-reported."""
    tok = fanvue_runtime.load_credentials()
    token = tok.get("access_token")
    if not token:
        return {"ok": False, "error": "no stored access_token"}

    account = _get("/v1/users/account", token)
    subscribers = _get("/v1/subscribers", token).get("data", [])
    posts_resp = _get("/v1/posts", token).get("data", [])
    posts = []
    for p in posts_resp:
        uuid = p["uuid"]
        comments = _get(f"/v1/posts/{uuid}/comments", token).get("data", [])
        posts.append({"uuid": uuid, "text": p.get("text", ""), "tips": p.get("tips", {}),
                     "comments": comments})

    return {
        "ok": True, "fetched_at": _now(),
        "subscribers": subscribers, "posts": posts,
        "account": {"fans": account.get("account", {}).get("fans", {}),
                   "earnings": account.get("account", {}).get("earnings", {})},
    }


def load_previous_snapshot() -> dict | None:
    if not SNAPSHOT_PATH.is_file():
        return None
    try:
        return json.loads(SNAPSHOT_PATH.read_text())
    except (OSError, ValueError):
        return None


def save_snapshot(state: dict) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def detect_events(previous: dict | None, current: dict) -> list[dict]:
    """Diffs previous vs current live state. On the very first snapshot
    (previous is None), every subscriber/comment/tip present is reported as
    a real event -- correct, since the baseline genuinely was zero."""
    events = []
    prev_subs = {s["uuid"]: s for s in (previous or {}).get("subscribers", [])}

    for s in current.get("subscribers", []):
        uuid = s["uuid"]
        sub = s.get("subscription") or {}
        if uuid not in prev_subs:
            etype = "TRIAL_STARTED" if sub.get("amountPaid", 0) == 0 else "NEW_SUBSCRIBER"
            events.append({"type": etype, "fan_id": uuid, "handle": s["handle"],
                          "ts": s.get("firstSubscribedAt"), "detail": sub})
        else:
            prev_sub = prev_subs[uuid].get("subscription") or {}
            prev_paid = prev_sub.get("amountPaid", 0)
            if prev_paid == 0 and sub.get("amountPaid", 0) > 0:
                events.append({"type": "TRIAL_CONVERTED", "fan_id": uuid, "handle": s["handle"],
                              "ts": _now(), "detail": sub})
            # A real, directly-observed status transition -- this is
            # genuine evidence of a cancellation, unlike mere disappearance
            # from the list (see below). Fanvue's own subscriber schema
            # documents 'cancelled' as a status the record can carry WHILE
            # STILL VISIBLE (e.g. during an access grace period), so this
            # is the correct place to detect it, not at removal time.
            if prev_sub.get("status") != "cancelled" and sub.get("status") == "cancelled":
                events.append({"type": "SUBSCRIPTION_CANCELLED_CONFIRMED", "fan_id": uuid,
                              "handle": s["handle"], "ts": _now(), "detail": sub})

    cur_sub_uuids = {s["uuid"] for s in current.get("subscribers", [])}
    for uuid, s in prev_subs.items():
        if uuid in cur_sub_uuids:
            continue
        prev_sub = s.get("subscription") or {}
        prev_status = prev_sub.get("status")
        if prev_status == "cancelled":
            # Already reported as SUBSCRIPTION_CANCELLED_CONFIRMED when the
            # status transition itself was observed -- this is just Fanvue
            # removing the record afterward, not a new event.
            continue
        period_end = _parse_iso(prev_sub.get("currentPeriodEnd"))
        now_dt = datetime.now(timezone.utc)
        if prev_sub.get("amountPaid", 0) == 0 and period_end and period_end <= now_dt:
            # A free trial whose own recorded end date had already passed
            # -- evidenced natural expiry, not an inferred cancellation.
            events.append({"type": "TRIAL_EXPIRED", "fan_id": uuid, "handle": s["handle"],
                          "ts": _now(), "detail": prev_sub})
        else:
            # Disappeared without ever showing status=cancelled and without
            # an evidenced expired trial window -- we genuinely do not know
            # why (voluntary cancellation we missed the status flip on,
            # payment failure, account deletion, a platform-side removal).
            # Never invent a specific cause the evidence doesn't support.
            events.append({"type": "SUBSCRIBER_DISAPPEARED_UNKNOWN", "fan_id": uuid,
                          "handle": s["handle"], "ts": _now(), "detail": prev_sub})

    prev_comment_uuids = {c["uuid"] for post in (previous or {}).get("posts", [])
                          for c in post.get("comments", [])}
    prev_tips = {p["uuid"]: p.get("tips", {}).get("count", 0) for p in (previous or {}).get("posts", [])}

    for post in current.get("posts", []):
        for c in post.get("comments", []):
            if c["uuid"] not in prev_comment_uuids:
                events.append({"type": "NEW_COMMENT", "fan_id": c["user"]["uuid"],
                              "handle": c["user"]["handle"], "ts": c["createdAt"],
                              "detail": {"post_uuid": post["uuid"], "text": c["text"]}})
        cur_tip_count = post.get("tips", {}).get("count", 0)
        if cur_tip_count > prev_tips.get(post["uuid"], 0):
            events.append({"type": "TIP", "fan_id": None, "handle": None, "ts": _now(),
                          "detail": {"post_uuid": post["uuid"], "tips": post.get("tips", {})}})

    return events


def check() -> dict:
    current = fetch_live_state()
    if not current.get("ok"):
        return current
    previous = load_previous_snapshot()
    events = detect_events(previous, current)
    save_snapshot(current)
    return {"ok": True, "events": events, "is_first_snapshot": previous is None,
           "current_subscribers": len(current.get("subscribers", [])),
           "current_earnings": current.get("account", {}).get("earnings", {})}


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if not argv or argv[0] != "check":
        print(json.dumps({"ok": False, "error": "usage: commercial_events.py check"}))
        return 1
    result = check()
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
