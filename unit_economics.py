#!/usr/bin/env python3
"""Derived H1 unit economics. Every ratio requires a non-zero denominator
with real data behind it -- returns "insufficient_data" rather than
fabricating a number from zero/zero. Never invents figures.

    python3 unit_economics.py
"""

from __future__ import annotations

import json
import sys

import state as st

INSUFFICIENT = "insufficient_data"


def _ratio(numerator, denominator, *, scale: float = 1.0):
    if not denominator:
        return INSUFFICIENT
    return round((numerator / denominator) * scale, 4)


def compute(v: dict) -> dict:
    h1 = v["metrics"]["h1_market"]
    revenue = h1.get("gross_revenue_usd", 0.0)
    spend = h1.get("spend_usd", 0.0)
    subscribers = h1.get("raw_subscribers", 0)

    # Impressions/profile-visit/Fanvue-visit data isn't tracked as a single
    # aggregate anywhere yet (it lives per-post in content_plan.metrics_snapshots
    # and per-experiment in experiments[].measurements) -- pull what's real.
    impressions = 0
    for snap in v.get("content_plan", {}).get("metrics_snapshots", []):
        for post in snap.get("posts", []):
            impressions += (post.get("insights", {}) or {}).get("reach") or 0

    return {
        "revenue_per_1000_impressions": _ratio(revenue, impressions, scale=1000),
        "cac_usd": _ratio(spend, subscribers),
        "subscriber_to_payer_conversion": INSUFFICIENT,  # needs payer-vs-subscriber split data
        "arppu_usd": _ratio(revenue, subscribers),
        "approximate_ltv_usd": INSUFFICIENT,  # needs churn/retention history, not available yet
        "_inputs": {"revenue_usd": revenue, "spend_usd": spend,
                   "subscribers": subscribers, "impressions_observed": impressions},
        "_note": ("Figures show 'insufficient_data' rather than 0 or a misleading ratio "
                 "when the denominator is zero or the required data doesn't exist yet -- "
                 "never treat those as real economics."),
    }


def main(argv=None) -> int:
    v = st.load()
    if v is None:
        print(json.dumps({"ok": False, "error": "no venture state"}))
        return 1
    print(json.dumps(compute(v), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
