#!/usr/bin/env python3
"""Real Fanvue H1 metrics via API (replaces manual entry now that
read:insights/read:fan are granted). Falls back honestly to zero/None --
never fabricates a number.

    python3 fanvue_metrics.py summary
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

import fanvue_runtime

API_BASE = "https://api.fanvue.com"


def _get(path: str, token: str) -> dict:
    req = urllib.request.Request(f"{API_BASE}{path}")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"_error": True, "status": exc.code, "body": exc.read().decode("utf-8", "replace")}


def collect_summary() -> dict:
    tok = fanvue_runtime.load_credentials()
    token = tok.get("access_token")
    if not token:
        return {"ok": False, "error": "no stored access_token"}

    earnings = _get("/v1/insights/earnings/summary", token)
    account = _get("/v1/users/account", token)

    if earnings.get("_error") or account.get("_error"):
        return {"ok": False, "error": {"earnings": earnings, "account": account}}

    totals = earnings.get("totals", {}) or {}
    all_time = totals.get("allTime", {}) or {}
    this_month = totals.get("thisMonth", {}) or {}

    return {
        "ok": True,
        "gross_revenue_usd": round((all_time.get("gross") or 0) / 100, 2),
        "net_revenue_usd": round((all_time.get("net") or 0) / 100, 2),
        "revenue_this_month_usd": round((this_month.get("net") or 0) / 100, 2),
        "subscribers": account.get("fanCounts", {}).get("subscribersCount", 0),
        "followers": account.get("fanCounts", {}).get("followersCount", 0),
    }


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if not argv or argv[0] != "summary":
        print(json.dumps({"ok": False, "error": "usage: fanvue_metrics.py summary"}))
        return 1
    result = collect_summary()
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
