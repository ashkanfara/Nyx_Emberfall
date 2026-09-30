#!/usr/bin/env python3
"""Fanvue post creation (POST /v1/posts). Verifies the actual published
result rather than trusting a 2xx alone -- same discipline as
instagram_publish.py after the media-type bug there.

    python3 fanvue_posts.py "<text>" [media_uuid ...]
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


def create_post(text: str, media_uuids: list[str] | None = None,
                audience: str = "followers-and-subscribers") -> dict:
    tok = fanvue_runtime.load_credentials()
    token = tok.get("access_token")
    if not token:
        return {"ok": False, "error": "no stored access_token"}

    body = {"text": text, "audience": audience}
    if media_uuids:
        body["mediaUuids"] = media_uuids

    result = _api("POST", "/v1/posts", token, body=body)
    if isinstance(result, dict) and result.get("_error"):
        return {"ok": False, "stage": "create_post", "error": result}
    if "uuid" not in result and "id" not in result:
        return {"ok": False, "stage": "create_post", "error": result}

    post_id = result.get("uuid") or result.get("id")
    # Verify: re-fetch the post rather than trusting the create response alone.
    check = _api("GET", f"/v1/posts/{post_id}", token)
    if isinstance(check, dict) and check.get("_error"):
        return {"ok": False, "stage": "verify", "post_id": post_id, "error": check}

    return {"ok": True, "post_id": post_id, "audience": check.get("audience"),
           "media_count": len(check.get("mediaUuids") or check.get("media") or [])}


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(json.dumps({"ok": False, "error": "usage: fanvue_posts.py <text> [media_uuid ...]"}))
        return 1
    text, media_uuids = argv[0], argv[1:]
    result = create_post(text, media_uuids or None)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
