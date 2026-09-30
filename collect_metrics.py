#!/usr/bin/env python3
"""Lightweight metrics collection for PS-05 H1 tracking.

TikTok: pulls video/profile insights via the already-connected publish
connector's granted scopes (user.insights, video.insights) once posts exist.
Fanvue: no API app has been registered yet (a separate one-time founder-
assisted setup, same shape as the Instagram one) -- until then this prompts
for manual entry of subscriber/revenue numbers, which METRICS_ANALYST
consumes exactly the same way regardless of source.

    python3 collect_metrics.py fanvue --subscribers 12 --revenue 45.00
    python3 collect_metrics.py summary
"""

from __future__ import annotations

import argparse
import json
import sys

import audit
import state as st


def record_fanvue_manual(v: dict, *, subscribers: int, revenue: float, spend: float) -> None:
    h1 = v["metrics"]["h1_market"]
    h1["raw_subscribers"] = subscribers
    h1["gross_revenue_usd"] = revenue
    h1["spend_usd"] = spend
    h1["cac_usd"] = round(spend / subscribers, 2) if subscribers else None
    audit.append(actor="founder", action="fanvue_metrics_recorded",
                 detail=f"Manual Fanvue metrics: {subscribers} subscribers, "
                        f"${revenue:.2f} revenue, ${spend:.2f} spend.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    fv = sub.add_parser("fanvue", help="record manually-reported Fanvue numbers")
    fv.add_argument("--subscribers", type=int, required=True)
    fv.add_argument("--revenue", type=float, required=True)
    fv.add_argument("--spend", type=float, default=0.0)

    sub.add_parser("summary", help="print current H1 metrics")

    args = ap.parse_args(argv)
    v = st.load()
    if v is None:
        print("no venture state", file=sys.stderr)
        return 1

    if args.cmd == "fanvue":
        st.update(lambda fresh: record_fanvue_manual(fresh, subscribers=args.subscribers,
                                                     revenue=args.revenue, spend=args.spend))
    elif args.cmd == "summary":
        print(json.dumps(v["metrics"]["h1_market"], indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
