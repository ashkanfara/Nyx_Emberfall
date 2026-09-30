"""Channel registry + per-channel content variants for PS-05 distribution.

Why this exists (founder 2026-09-22): distribution is expanding past Instagram
and TikTok, but a channel is only LIVE once its route is verified end to end.
So this module is deliberately NOT a scheduler, orchestrator or dashboard --
it is ONE table plus small helpers, which lets the Content Director prepare a
platform-native variant of an EXISTING story asset while the publisher
(nyx_instagram.py) keeps publishing only to routes that are actually verified.

Adding a channel to the publisher is a Metricool routing change and needs the
founder's approval at action time; flipping `publishes` here does NOT by itself
publish anything (see test_manager's drift test, which pins the publishing set
to nyx_instagram.PLATFORMS so the two can never silently disagree).
"""

from __future__ import annotations

NOT_AVAILABLE = "NOT_AVAILABLE"          # same convention as dashboard_status

LIVE = "LIVE"                            # route verified end to end, publishing on
UNVERIFIED = "UNVERIFIED"                # route plausible, connection not yet proven
PREPARED_NOT_CONNECTED = "PREPARED_NOT_CONNECTED"   # copy may be prepared, no route
DEFERRED = "DEFERRED"                    # deliberately out of scope for now
CHANNEL_STATUSES = (LIVE, UNVERIFIED, PREPARED_NOT_CONNECTED, DEFERRED)

# `native_form` is what the Content Director adapts an item INTO -- not every
# item goes everywhere, and the same copy is never blasted across channels.
CHANNELS: dict[str, dict] = {
    "instagram": {
        "status": LIVE, "route": "metricool", "publishes": True, "cost": "none",
        "native_form": "visual story carousel or Reel",
    },
    "tiktok": {
        "status": LIVE, "route": "metricool", "publishes": True, "cost": "none",
        "native_form": "short vertical video, or a carousel adapted as a slideshow video",
    },
    "threads": {
        "status": UNVERIFIED, "route": "metricool", "publishes": False, "cost": "none",
        "native_form": ("conversational character voice -- a story fragment, an open loop or a "
                        "question, occasionally a short reply-thread; text-first, not a caption dump"),
        "blocker": ("Metricool supports Threads, but the Nyx Threads connection on brand 6988018 is "
                    "unverified: needs the founder's account link if missing, then a read-only "
                    "getBrandSettings probe, then the publisher edits (PLATFORMS, HANDLE_FIELD "
                    "threadsData, a settings builder and a verify_created branch)"),
    },
    "x": {
        "status": PREPARED_NOT_CONNECTED, "route": None, "publishes": False,
        "cost": "Metricool Starter+ AND the X add-on -- founder declined the spend",
        "native_form": "one concise hook line in character, no link dump",
        "blocker": ("paid Metricool add-on required and no PS-05 X account exists; creating one or "
                    "upgrading is founder-only. Copy may be prepared, never published."),
    },
    "reddit": {
        "status": DEFERRED, "route": None, "publishes": False, "cost": "none",
        "native_form": ("community-native story or image post written for ONE subreddit, in that "
                        "sub's voice, with the disclosure its own rules require"),
        "blocker": ("Metricool has no Reddit provider and PS-05 has no Reddit account or tooling. "
                    "Any revival is manual-only and must first clear that subreddit's self-promotion, "
                    "AI-content, account-age/karma and adult-link rules -- never a repeated blast."),
    },
    "fanvue": {
        "status": LIVE, "route": "fanvue_api", "publishes": True, "cost": "none",
        "native_form": ("a deeper continuation of the SAME open loop for subscribers -- never the "
                        "identical public slides"),
    },
}

# One variant field per channel on a content item, e.g. "threads_variant".
VARIANT_FIELD = {name: f"{name}_variant" for name in CHANNELS}


def publishing_channels() -> tuple[str, ...]:
    """Channels whose route is verified AND enabled. Everything else may be
    briefed and prepared, never published."""
    return tuple(sorted(n for n, c in CHANNELS.items() if c["publishes"]))


def status_of(channel: str) -> str:
    return CHANNELS[channel]["status"] if channel in CHANNELS else NOT_AVAILABLE


def variant_field(channel: str) -> str:
    if channel not in VARIANT_FIELD:
        raise KeyError(f"unknown channel {channel!r}")
    return VARIANT_FIELD[channel]


def variant(item: dict, channel: str) -> str:
    return str(item.get(variant_field(channel)) or "").strip()


def variants_present(item: dict) -> tuple[str, ...]:
    """Channels this item has actually been adapted for -- not a plan, a fact."""
    return tuple(sorted(n for n in CHANNELS if variant(item, n)))


def variant_problems(item: dict) -> list[str]:
    """A variant is only useful if it is real copy for a known channel, and if
    it is not the SAME text as another channel's (that is cross-post spam, the
    one thing this layer exists to avoid)."""
    probs = [f"{k} is not a known channel variant" for k in item
             if k.endswith("_variant") and k not in VARIANT_FIELD.values()]
    seen: dict[str, str] = {}
    for name in variants_present(item):
        text = variant(item, name)
        if text.lower() in seen:
            probs.append(f"{name} variant duplicates the {seen[text.lower()]} variant verbatim")
        seen[text.lower()] = name
    return probs


def per_channel_distribution(content_items: list[dict]) -> dict:
    """Per-channel distribution counts from this project's OWN records, plus an
    honest placeholder for every metric no connected route can currently read.
    Never fabricates a number: an unreadable metric reports NOT_AVAILABLE."""
    out: dict[str, dict] = {}
    for name, cfg in CHANNELS.items():
        out[name] = {
            "status": cfg["status"], "publishes": cfg["publishes"],
            "published": 0, "scheduled": 0, "variants_prepared": 0,
            # Only a connected, measurable route can fill these in.
            "reach_or_impressions": NOT_AVAILABLE, "engagement": NOT_AVAILABLE,
            "follows_or_profile_visits": NOT_AVAILABLE, "funnel_clicks": NOT_AVAILABLE,
        }
    for item in content_items or []:
        for name in variants_present(item):
            out[name]["variants_prepared"] += 1
        status = item.get("lifecycle_status")
        targets = set(item.get("live_platforms") or [])
        if item.get("target_platform") in out:
            targets.add(item["target_platform"])
        for xp in item.get("crossposts") or []:
            if xp.get("platform") in out and xp.get("status") == "SCHEDULED":
                out[xp["platform"]]["scheduled"] += 1
        for name in targets:
            if name not in out:
                continue
            if status == "PUBLISHED" or item.get("published"):
                out[name]["published"] += 1
            elif status == "SCHEDULED":
                out[name]["scheduled"] += 1
    return out
