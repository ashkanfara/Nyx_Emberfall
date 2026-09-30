#!/usr/bin/env python3
"""PS-05 READ-ONLY observability bridge (Phase 2).

A standalone MCP server that lets an external observer (ChatGPT, or any
other MCP client) inspect PS-05's CURRENT state. It exposes exactly two
tools, both read-only, and makes exactly one kind of network call: a plain
HTTP GET against PS-05's own already-running, already-read-only status
endpoint --

    GET http://127.0.0.1:8790/api/state

-- which is server.py's existing build_status() function. This module
never imports any PS-05 Python module, never touches venture.json or any
other state file on disk, and never calls /api/approve or /api/tick (the
two mutating routes on that same server). There is no code path in this
file that can trigger a CEO tick, clear an approval, publish content, send
a message, or change any PS-05 state.

INDEPENDENCE (both directions):
  - If this bridge is never started, is stopped, crashes, or ChatGPT never
    connects to it, PS-05's own scheduled autonomous operation is
    completely unaffected -- it doesn't know this file exists.
  - If PS-05's dashboard server (server.py) isn't running, this bridge's
    tools return a clear error to the caller rather than fabricating or
    caching stale data.

RUNS IN ITS OWN ISOLATED VIRTUALENV (deliberately NOT PS-05's own Python
3.9 runtime -- this keeps the observability bridge's dependency (the `mcp`
SDK) completely out of PS-05's production environment):

    /opt/homebrew/bin/python3.12 -m venv .observability_venv
    .observability_venv/bin/pip install "mcp[cli]<2"

RUN:
    .observability_venv/bin/python ps05_observability_mcp.py

This starts a Streamable-HTTP MCP server bound to 127.0.0.1 only (never
0.0.0.0, never exposed beyond this machine) at:

    http://127.0.0.1:8791/mcp

TOOLS EXPOSED (both tagged read-only/non-destructive in their MCP
annotations):
    get_ps05_status()             -- full current sanitized state snapshot
    get_recent_activity(hours=6)  -- sanitized, time-windowed activity log

Both tools sanitize their output before returning it (see _sanitize below):
any key that looks credential-shaped is redacted defensively (PS-05's own
/api/state already never includes real secrets -- they live only in
.fanvue_runtime/, .instagram_runtime/, .wan_runtime/, none of which
build_status() ever reads -- this is a second, independent guard against
that ever changing without this bridge needing an update), and free-text
fields are lightly scrubbed of off-platform contact patterns (emails,
@handles, phone-number-like sequences, messaging-app mentions) that
sometimes appear inside a spam/scam fan message quoted in the activity log.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

PS05_STATE_URL = "http://127.0.0.1:8790/api/state"
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 8791

_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                             idempotentHint=True, openWorldHint=False)

mcp = FastMCP(
    "ps05-observability",
    host=BRIDGE_HOST,
    port=BRIDGE_PORT,
    instructions=(
        "Read-only observability bridge for PS-05 (\"Nyx Emberfall\"), an "
        "autonomous AI-virtual-creator venture that is operated entirely by "
        "a separate system (Claude / a scheduled Python tick), not by you. "
        "You cannot trigger any action, approve anything, publish anything, "
        "or change any PS-05 state through these tools -- they only report "
        "what is currently true. Use get_ps05_status for an overall "
        "snapshot (phase, scheduler/tick health, any pending founder "
        "approval, content pipeline inventory, commercial metrics, "
        "platform status, next intended actions) and get_recent_activity "
        "for a time-windowed view of what the system has actually done "
        "recently -- useful for questions like 'why hasn't it posted in "
        "the last 3 hours?'. Every response includes a fetched_at "
        "timestamp; treat data older than a fresh fetch as potentially "
        "stale and say so rather than assuming it's still current."))


# --------------------------------------------------------------------------
# Sanitization -- defense in depth, applied to every tool's output.
# --------------------------------------------------------------------------

_SECRET_KEY_PATTERN = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|credential|cookie|"
    r"private[_-]?key|client[_-]?secret|refresh[_-]?token|access[_-]?token)",
    re.IGNORECASE)

_CONTACT_PATTERNS = (
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),          # email
    re.compile(r"@[A-Za-z0-9_]{3,}"),                                       # @handle mention
    # A realistic phone-number shape (country code + area code + exchange +
    # line number, 9-15 digits total) -- deliberately NOT a loose
    # "digits-and-separators" pattern, which would (and during testing,
    # did) false-positive on ISO date/timestamp strings like
    # "2026-09-13T10:51:51+00:00".
    re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)"),
)

# Timestamp-shaped keys are never PII and must never be touched by the
# contact-pattern scrubber -- freshness data is exactly what ChatGPT needs
# to distinguish current from stale state, so it is deliberately exempted
# from the free-text pass rather than relying solely on the phone-pattern
# fix above.
_TIMESTAMP_KEY_PATTERN = re.compile(
    r"(^ts$|_at$|_ts$|^fetched_at$|^checked_at$|^created_at$|^updated_at$)",
    re.IGNORECASE)


def _redact_contact_info(text: str) -> str:
    for pattern in _CONTACT_PATTERNS:
        text = pattern.sub("[redacted contact info]", text)
    return text


def _sanitize(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact anything credential-shaped by key name, and
    lightly scrub off-platform contact patterns out of free text (never
    out of timestamp-shaped fields)."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _SECRET_KEY_PATTERN.search(str(k)):
                out[k] = "[redacted]"
            else:
                out[k] = _sanitize(v, key=k)
        return out
    if isinstance(value, list):
        return [_sanitize(v, key=key) for v in value]
    if isinstance(value, str):
        if key and _TIMESTAMP_KEY_PATTERN.search(key):
            return value
        return _redact_contact_info(value)
    return value


# --------------------------------------------------------------------------
# The one and only network call this module ever makes.
# --------------------------------------------------------------------------

def _fetch_state() -> dict:
    """Plain HTTP GET against PS-05's own already-running status endpoint.
    Never POSTs. Never calls /api/approve or /api/tick. Raises a clear,
    honest error if the dashboard server isn't reachable -- never returns
    fabricated or silently-stale data."""
    req = urllib.request.Request(PS05_STATE_URL, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach PS-05's status endpoint at {PS05_STATE_URL} -- "
            f"is server.py running? ({exc})") from exc
    data = json.loads(raw)
    data["_bridge_fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return data


# --------------------------------------------------------------------------
# Tools.
# --------------------------------------------------------------------------

@mcp.tool(annotations=_READ_ONLY, description=(
    "Get PS-05's full current status: venture phase/bottleneck, scheduler/"
    "tick health (last tick, next tick, outcome, consecutive failures), "
    "any pending founder approval (what it is, why automation can't do it, "
    "what action is needed), content pipeline inventory by platform and "
    "lifecycle stage, commercial scoreboard (monetization clock, trials, "
    "subscribers, PPV, renewals, recognized revenue, founder time), "
    "funnel, per-platform health/blockers (Instagram/TikTok/Fanvue), "
    "recent commercial events, and the next-actions queue. Sourced live "
    "from PS-05's own running status endpoint -- reflects this exact "
    "moment, not a cached or manually-written report."))
def get_ps05_status() -> dict:
    data = _fetch_state()
    if not data.get("seeded", True):
        return {"error": "PS-05 has no seeded venture state yet."}

    v = data.get("venture", {})
    snapshot = {
        "fetched_at": data["_bridge_fetched_at"],
        "venture": {
            "phase": v.get("phase"),
            "decision": v.get("decision"),
            "current_bottleneck": v.get("current_bottleneck"),
            "manager_status": v.get("manager_status"),
            "updated_at": v.get("updated_at"),
        },
        "monetization_clock": v.get("monetization_clock"),
        "queued_founder_actions": v.get("queued_actions"),
        "pending_founder_approval": data.get("pending_approval"),
        "scheduler": data.get("scheduler_heartbeat"),
        "scheduler_trigger_health": data.get("scheduler_trigger_health"),
        "tool_permission_health": data.get("tool_permission_health"),
        "system_status": data.get("system_status"),
        "stall_detection": data.get("stall_detection"),
        "content_pipeline": data.get("content_pipeline_health"),
        "commercial_scoreboard": data.get("commercial_scoreboard"),
        "storefront_readiness": data.get("storefront_readiness"),
        "funnel": data.get("funnel"),
        "platform_health": data.get("platform_health"),
        "recent_commercial_events": data.get("commercial_events"),
        "h1_market_status": data.get("h1_status"),
        "h2_operability_status": data.get("h2_status"),
        "next_actions_queue": data.get("next_actions_queue"),
        "next_checkpoint": data.get("next_checkpoint"),
    }
    return _sanitize(snapshot)


@mcp.tool(annotations=_READ_ONLY, description=(
    "Get PS-05's recent autonomous activity within the last N hours "
    "(ticks, content generation, QA results, publishes, fan-message "
    "decisions, errors) -- use this to answer 'what has it been doing?' "
    "or 'why hasn't it posted in 3 hours?'. Sourced from the same live "
    "status endpoint as get_ps05_status; entries are capped at whatever "
    "the underlying activity log currently retains (the most recent 100 "
    "entries), so a very large hours value may return fewer entries than "
    "the window technically covers -- entry_count tells you exactly how "
    "many were found."))
def get_recent_activity(hours: float = 6.0) -> dict:
    data = _fetch_state()
    if not data.get("seeded", True):
        return {"error": "PS-05 has no seeded venture state yet."}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    entries = []
    for e in data.get("activity_log", []):
        ts = e.get("ts")
        try:
            when = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when >= cutoff:
            entries.append(e)

    return _sanitize({
        "fetched_at": data["_bridge_fetched_at"],
        "window_hours": hours,
        "entry_count": len(entries),
        "entries_oldest_first": list(reversed(entries)),
    })


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
