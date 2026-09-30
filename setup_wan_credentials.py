#!/usr/bin/env python3
"""One-time Wan (Alibaba Cloud Model Studio) credential setup.

Run this yourself, in your own terminal, AFTER you have completed Model
Studio's own account/region/billing/API-key setup (see the founder setup
instructions -- this script does not perform any of that).

Usage:
    export WAN_API_KEY="sk-..."   # paste the key into YOUR shell, not chat
    python3 setup_wan_credentials.py
    unset WAN_API_KEY

This script reads the key from the environment only -- it is never logged,
printed, or returned, matching the discipline already used for Instagram/
Fanvue credentials in this project (see wan_runtime.py)."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

import wan_runtime


def main() -> int:
    api_key = os.environ.get("WAN_API_KEY", "").strip()
    if not api_key:
        print(json.dumps({"ok": False, "error": "WAN_API_KEY not set in environment"}))
        return 1

    # Cheapest possible live check: list available models. Does not submit
    # a generation job, so it cannot spend anything even if free-quota-only
    # mode is not yet enabled on the account.
    req = urllib.request.Request(
        "https://dashscope-intl.aliyuncs.com/api/v1/models",
        headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        verified = True
        verify_note = "live API check succeeded"
    except urllib.error.HTTPError as exc:
        verified = False
        verify_note = f"live API check failed: HTTP {exc.code} (key stored anyway -- verify manually)"
    except Exception as exc:  # noqa: BLE001
        verified = False
        verify_note = f"live API check failed: {str(exc)[:150]} (key stored anyway -- verify manually)"

    wan_runtime.store_api_key(api_key)
    print(json.dumps({"ok": True, "stored": True, "verified": verified, "note": verify_note}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
