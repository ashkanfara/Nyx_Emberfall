#!/usr/bin/env python3
"""Per-post scorecard: the data structure for the weekly learning review
(founder direction 2026-09-29).

Data structure and a recorder function only -- this module is NOT called by
manager.py's tick loop or any other autonomous path. Nothing here
generates, publishes, schedules, spends, creates an account, or changes a
connection; it only appends a scorecard dict onto an EXISTING content item
already tracked in state.py's content_plan.content_items[], the same way
set_content_item_lifecycle() does for lifecycle status. Wiring this into
the live tick loop (so it is populated automatically rather than by a
manual/founder-triggered call) is separate, future, explicitly-approved
work.

    from state import update
    from scorecard import record_scorecard, new_scorecard
    update(lambda v: record_scorecard(v, item_index, new_scorecard(...)))
"""

from __future__ import annotations

from datetime import datetime, timezone

import state as st

SCHEMA_VERSION = 1

# Every field this scorecard can hold, and HONESTLY what actually backs it
# today (2026-09-29) -- per the existing metrics integrations already in
# this repo (collect_metrics.py, instagram_metrics.py, fanvue_metrics.py).
# "api" = pulled directly from a platform API call already wired up here.
# "manual" = no API field exists for this; a human enters it (or it is
# left None) -- never silently fabricated.
# "derived" = computed from other fields on this same scorecard.
# "unavailable" = no known API or reliable manual path today; see NOTES.
FIELD_SOURCES = {
    "platform":                    "manual",   # set at brief time, not pulled
    "format":                      "manual",   # e.g. "story_carousel", "reel", "video"
    "tier":                        "manual",   # "public" | "fanvue" | None
    "episode_id":                  "manual",   # links back to a season_arcs slot, if any
    "scheduled_at":                "api",      # Metricool (metricool-nyx) getScheduledPosts
    "published_at":                "api",      # Metricool (metricool-nyx) once live
    "hook_type":                   "manual",   # e.g. "pattern_interrupt", "question", "reveal" -- a QA/creative label, not platform data
    "slide_count":                 "derived",  # len(story_plan) at brief time
    "reach":                       "api",      # Instagram Graph API `reach` (image+video); TikTok video.insights views
    "views":                       "api",      # TikTok video.insights; Instagram video/reel plays
    "completion_or_swipe_proxy":   "unavailable_partial",  # see NOTES 1
    "profile_or_link_clicks":      "unavailable_partial",  # see NOTES 2
    "subscriber_conversion_proxy": "unavailable_partial",  # see NOTES 3
    "likes":                       "api",
    "comments":                    "api",
    "shares":                      "api",
    "saves":                       "api",      # Instagram `saved`
    "qa_notes":                    "manual",   # qualitative QA/creative-review notes, free text
}

# NOTES -- read before trusting a value pulled with these sources:
#
# 1. completion_or_swipe_proxy: Instagram's Graph API exposes NO per-slide
#    swipe-through metric for a standard feed carousel (only Stories carry
#    a `taps_forward`/`exits`-style metric, and this format does not post
#    to Stories) -- for an Instagram story_carousel, this field can only
#    ever be a WEAK proxy: `saved` and `shares` relative to `reach` (a
#    carousel that gets swiped through is more likely to be saved/shared
#    than one abandoned on slide 1), never a direct measurement. For
#    TikTok's slideshow adaptation of the same slides, the real,
#    comparatively strong proxy IS available: `average watch time` /
#    `video duration` from video.insights (see collect_metrics.py) is a
#    genuine completion-rate proxy for that platform. Record the TikTok
#    number when present; leave the Instagram number None rather than
#    inventing one from reach/saves alone unless the qa_notes explicitly
#    say the review is using it as an acknowledged weak proxy.
#
# 2. profile_or_link_clicks: not present in this repo's current Instagram
#    field set (_IMAGE_METRICS/_VIDEO_METRICS in instagram_metrics.py) or
#    in fanvue_metrics.collect_summary(). Instagram's Graph API can expose
#    account-level `profile_views`/`website_clicks` under some insights
#    scopes, but not reliably attributed to ONE post without a broader
#    insights grant this integration does not currently request. Until
#    that scope is added (a separate, founder-approved change), this field
#    stays manual/None for Instagram and TikTok; do not estimate it.
#
# 3. subscriber_conversion_proxy: fanvue_metrics.collect_summary() returns
#    ACCOUNT-level subscribers/revenue, not per-post attribution -- Fanvue
#    has no referrer/UTM-style linkage back to a specific Instagram/TikTok
#    post in this integration. The only honest proxy available is a
#    manually-recorded subscriber-count delta in a stated time window
#    immediately after this post's published_at (e.g. "+3 subscribers in
#    the 24h after this post, vs a same-length baseline"), and it MUST be
#    recorded as an explicit assumption (baseline window, confounds noted)
#    rather than presented as attributed conversion -- see
#    unit_economics.py's insufficient_data discipline; this proxy follows
#    the same rule: no reliable delta yet means leave it None, not zero.


def new_scorecard(*, platform: str, format: str, tier: str | None = None,
                  episode_id: str | None = None, slide_count: int | None = None,
                  hook_type: str | None = None) -> dict:
    """A fresh, mostly-empty scorecard for one post -- fill in the rest as
    data actually arrives (scheduling, then publishing, then metrics days
    later); never backfill a field with a guess."""
    return {
        "schema_version": SCHEMA_VERSION,
        "platform": platform,
        "format": format,
        "tier": tier,
        "episode_id": episode_id,
        "scheduled_at": None,
        "published_at": None,
        "hook_type": hook_type,
        "slide_count": slide_count,
        "reach": None,
        "views": None,
        "completion_or_swipe_proxy": None,
        "completion_or_swipe_proxy_note": None,   # required free-text if the value above is set from a weak proxy (see NOTES 1)
        "profile_or_link_clicks": None,
        "subscriber_conversion_proxy": None,
        "subscriber_conversion_proxy_window": None,  # e.g. "24h post-publish vs 7d baseline" -- required if the proxy above is set
        "likes": None,
        "comments": None,
        "shares": None,
        "saves": None,
        "qa_notes": "",
        "recorded_at": None,
        "last_updated_at": None,
    }


def record_scorecard(v: dict, item_index: int, scorecard: dict) -> dict:
    """Attach/update a scorecard on an existing content_plan.content_items[
    item_index] -- the same attach-by-index pattern as
    state.set_content_item_lifecycle(). Raises the same IndexError that
    function raises for an out-of-range index, so callers can't silently
    create a scorecard for a nonexistent item."""
    items = v.setdefault("content_plan", {}).setdefault("content_items", [])
    if not (0 <= item_index < len(items)):
        raise IndexError(f"no content_items[{item_index}]")
    now = datetime.now(timezone.utc).isoformat()
    existing = items[item_index].get("scorecard")
    scorecard = dict(scorecard)
    scorecard["recorded_at"] = existing["recorded_at"] if existing else now
    scorecard["last_updated_at"] = now
    items[item_index]["scorecard"] = scorecard
    return scorecard


def all_scorecards(v: dict) -> list[dict]:
    """Read-only: every content item that has a scorecard, item_index
    attached, in content_plan order -- the weekly review's raw input."""
    items = v.get("content_plan", {}).get("content_items", [])
    out = []
    for i, item in enumerate(items):
        sc = item.get("scorecard")
        if sc:
            out.append({"item_index": i, "item_id": item.get("item_id"), **sc})
    return out


if __name__ == "__main__":
    import json
    print(json.dumps({"field_sources": FIELD_SOURCES, "schema_version": SCHEMA_VERSION}, indent=2))
