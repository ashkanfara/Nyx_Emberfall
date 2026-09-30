#!/usr/bin/env python3
"""Lightweight Instagram Graph API metrics puller.

Reads like/comment counts and (best-effort) insights for a given media id,
using the same in-memory-only token pattern as instagram_publish.py -- this
module must never print, log, or return the access token itself.

    python3 instagram_metrics.py <media_id> [<media_id> ...]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

import instagram_runtime

GRAPH = "https://graph.instagram.com/v21.0"

# Reels/video posts expose a different insights metric set than image posts.
_IMAGE_METRICS = "reach,saved,likes,comments,shares,total_interactions"
_VIDEO_METRICS = ("reach,saved,likes,comments,shares,total_interactions,"
                  "ig_reels_video_view_total_time,ig_reels_avg_watch_time")


def describe_http_error(exc: urllib.error.HTTPError) -> str:
    """Graph API error body -> 'HTTP 400 OAuthException code 200: <message>'.
    Never includes the request URL (it carries the access token)."""
    try:
        err = json.loads(exc.read().decode("utf-8")).get("error", {})
    except Exception:  # noqa: BLE001 -- body may be empty/non-JSON
        err = {}
    parts = [f"HTTP {exc.code}", str(err.get("type") or "").strip()]
    if err.get("code") is not None:
        parts.append(f"code {err['code']}")
    if err.get("error_subcode") is not None:
        parts.append(f"subcode {err['error_subcode']}")
    head = " ".join(p for p in parts if p)
    msg = str(err.get("message") or exc.reason or "").strip()
    return f"{head}: {msg}"[:300] if msg else head


def _get(url: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(f"{url}?{qs}", timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(describe_http_error(exc)) from None


def fetch_media_metrics(media_id: str) -> dict:
    tok = instagram_runtime.load_token()
    if tok is None or not tok.get("access_token"):
        return {"ok": False, "media_id": media_id, "error": "no stored token"}
    token = tok["access_token"]

    base = _get(f"{GRAPH}/{media_id}", {
        "fields": "media_type,like_count,comments_count,timestamp,permalink",
        "access_token": token,
    })
    if "id" not in base and "media_type" not in base:
        return {"ok": False, "media_id": media_id, "error": base}

    metrics = _VIDEO_METRICS if base.get("media_type") == "VIDEO" else _IMAGE_METRICS
    insights = {}
    try:
        raw = _get(f"{GRAPH}/{media_id}/insights", {
            "metric": metrics, "access_token": token,
        })
        for entry in raw.get("data", []):
            values = entry.get("values", [])
            if values:
                insights[entry["name"]] = values[0].get("value")
    except Exception as exc:  # insights availability varies by media age/type
        insights = {"_error": str(exc)}

    return {
        "ok": True, "media_id": media_id,
        "media_type": base.get("media_type"),
        "like_count": base.get("like_count"),
        "comments_count": base.get("comments_count"),
        "timestamp": base.get("timestamp"),
        "permalink": base.get("permalink"),
        "insights": insights,
    }


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(json.dumps({"ok": False, "error": "usage: instagram_metrics.py <media_id> [...]"}))
        return 1
    results = [fetch_media_metrics(mid) for mid in argv]
    print(json.dumps(results, indent=2))
    return 0 if all(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
