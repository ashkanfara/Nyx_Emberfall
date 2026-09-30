#!/usr/bin/env python3
"""Fanvue automated (chat) messages -- PUT /chats/automated-messages/{trigger}.
Same shape as fanvue_monetization.py/fanvue_posts.py (real, documented,
writable endpoint -- confirmed via the official OpenAPI spec, not a UI-only
setting like bio/avatar/banner).

    python3 fanvue_automated_messages.py get
    python3 fanvue_automated_messages.py set new_subscriber "<text>" [price_cents]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

import fanvue_runtime

API_BASE = "https://api.fanvue.com"
TRIGGERS = ("new_subscriber", "new_follower", "subscription_canceled",
           "re_subscribed", "renewed", "new_purchase", "first_message_reply")


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


def get_automated_messages() -> dict:
    tok = fanvue_runtime.load_credentials()
    token = tok.get("access_token")
    if not token:
        return {"ok": False, "error": "no stored access_token"}
    result = _api("GET", "/chats/automated-messages", token)
    if isinstance(result, dict) and result.get("_error"):
        return {"ok": False, "error": result}
    return {"ok": True, "data": result.get("data", [])}


def set_automated_message(trigger: str, text: str, *, price_cents: int = 0) -> dict:
    """price_cents=0 means free (no PPV attached) -- the standard shape for
    a welcome message. A non-zero price requires attached media, which this
    function does not support (routine welcome messages shouldn't silently
    acquire a paywall)."""
    if trigger not in TRIGGERS:
        return {"ok": False, "error": f"unknown trigger {trigger!r} -- must be one of {TRIGGERS}"}
    tok = fanvue_runtime.load_credentials()
    token = tok.get("access_token")
    if not token:
        return {"ok": False, "error": "no stored access_token"}

    result = _api("PUT", f"/chats/automated-messages/{trigger}", token,
                  body={"text": text, "price": price_cents})
    if isinstance(result, dict) and result.get("_error"):
        return {"ok": False, "stage": "set_automated_message", "error": result}

    check = get_automated_messages()
    if not check.get("ok"):
        return {"ok": False, "stage": "verify", "error": check}
    row = next((r for r in check["data"] if r["trigger"] == trigger), None)
    if row is None or row.get("text") != text:
        return {"ok": False, "stage": "verify", "error": f"trigger {trigger!r} did not verify: {row}"}
    return {"ok": True, "trigger": trigger, "enabled": row.get("enabled"), "updated_at": row.get("updatedAt")}


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(json.dumps({"ok": False, "error": "usage: get | set <trigger> <text> [price_cents]"}))
        return 1
    if argv[0] == "get":
        result = get_automated_messages()
    elif argv[0] == "set" and len(argv) >= 3:
        price = int(argv[3]) if len(argv) > 3 else 0
        result = set_automated_message(argv[1], argv[2], price_cents=price)
    else:
        result = {"ok": False, "error": "usage: get | set <trigger> <text> [price_cents]"}
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
