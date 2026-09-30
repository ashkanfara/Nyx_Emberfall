#!/usr/bin/env python3
"""Fanvue monetization setup: subscription price + promotions.
Requires the write:creator scope (subscription-price and promotions both
depend on it per Fanvue's own scope table).

    python3 fanvue_monetization.py price <cents>
    python3 fanvue_monetization.py promo <availableToGroup> [--free-trial-days N | --discount-percent N]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

import fanvue_runtime

API_BASE = "https://api.fanvue.com"


def _api(method: str, path: str, token: str, *, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API_BASE}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        return {"_error": True, "status": exc.code, "body": exc.read().decode("utf-8", "replace")}


def set_subscription_price(cents: int, *, force_opt_in: bool = False) -> dict:
    tok = fanvue_runtime.load_credentials()
    token = tok.get("access_token")
    if not token:
        return {"ok": False, "error": "no stored access_token"}
    if not (399 <= cents <= 10000):
        return {"ok": False, "error": "cents must be between 399 and 10000"}

    result = _api("PATCH", "/v1/users/me/subscription-price", token,
                  body={"subscriptionPrice": cents, "forceOptIn": force_opt_in})
    if isinstance(result, dict) and result.get("_error"):
        return {"ok": False, "stage": "set_price", "error": result}

    check = _api("GET", "/v1/users/account", token)
    if isinstance(check, dict) and check.get("_error"):
        return {"ok": False, "stage": "verify", "error": check}
    return {"ok": True, "confirmed_price_cents": check.get("subscriptionPrice", cents)}


def create_promotion(available_to_group: str, *, free_trial_days: int | None = None,
                     discount_percent: int | None = None, message: str = "") -> dict:
    tok = fanvue_runtime.load_credentials()
    token = tok.get("access_token")
    if not token:
        return {"ok": False, "error": "no stored access_token"}

    body = {"availableToGroup": available_to_group}
    if free_trial_days is not None:
        body["freeTrial"] = True
        body["freeTrialDays"] = free_trial_days
    if discount_percent is not None:
        body["discountPercent"] = discount_percent
    if message:
        body["message"] = message

    result = _api("POST", "/v1/promotions", token, body=body)
    if isinstance(result, dict) and result.get("_error"):
        return {"ok": False, "stage": "create_promo", "error": result}
    promo_id = result.get("uuid") or result.get("id")
    return {"ok": True, "promotion_id": promo_id}


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(json.dumps({"ok": False, "error": "usage: price <cents> | promo <group> [...]"}))
        return 1
    if argv[0] == "price" and len(argv) >= 2:
        result = set_subscription_price(int(argv[1]))
    elif argv[0] == "promo" and len(argv) >= 2:
        kwargs = {}
        if "--free-trial-days" in argv:
            kwargs["free_trial_days"] = int(argv[argv.index("--free-trial-days") + 1])
        if "--discount-percent" in argv:
            kwargs["discount_percent"] = int(argv[argv.index("--discount-percent") + 1])
        result = create_promotion(argv[1], **kwargs)
    else:
        result = {"ok": False, "error": "usage: price <cents> | promo <group> [...]"}
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
