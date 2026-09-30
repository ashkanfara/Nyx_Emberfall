#!/usr/bin/env python3
"""One-off verification that the stored Instagram token is live and usable.

Reads the token from instagram_runtime.load_token() and uses it in-memory
only -- this script must never print, log, or return the token value itself,
only non-secret result fields (id/username) or a boolean success/failure."""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

import instagram_runtime


def main() -> int:
    tok = instagram_runtime.load_token()
    if tok is None or not tok.get("access_token"):
        print(json.dumps({"ok": False, "error": "no token stored"}))
        return 1

    params = urllib.parse.urlencode({
        "fields": "id,username",
        "access_token": tok["access_token"],
    })
    url = f"https://graph.instagram.com/v21.0/me?{params}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            err = json.loads(body).get("error", {})
            msg = err.get("message", body[:200])
        except ValueError:
            msg = body[:200]
        print(json.dumps({"ok": False, "http_status": exc.code, "error": msg}))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)[:200]}))
        return 1

    if "id" in data:
        instagram_runtime.store_token(tok["access_token"], ig_user_id=data["id"])
        print(json.dumps({"ok": True, "id": data["id"], "username": data.get("username")}))
        return 0

    print(json.dumps({"ok": False, "error": "unexpected response shape"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
