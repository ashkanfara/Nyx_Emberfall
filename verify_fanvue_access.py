#!/usr/bin/env python3
"""Verify stored Fanvue access token works, printing only non-secret fields.

    python3 verify_fanvue_access.py
"""

from __future__ import annotations

import json
import urllib.request

import fanvue_runtime

API_BASE = "https://api.fanvue.com"


def main() -> int:
    creds = fanvue_runtime.load_credentials()
    token = creds.get("access_token")
    if not token:
        print(json.dumps({"ok": False, "error": "no access_token stored"}))
        return 1

    req = urllib.request.Request(f"{API_BASE}/users/me")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    safe = {k: v for k, v in data.items() if k not in ("access_token", "refresh_token")}
    print(json.dumps({"ok": True, "user": safe}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
