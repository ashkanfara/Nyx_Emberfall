#!/usr/bin/env python3
"""Lightweight Instagram Graph API publisher (Instagram Login flow).

Two calls, per Meta's own documented flow: create a media container from a
public image URL, poll until it finishes processing, then publish it. The
access token is read from local storage and used in-memory only -- this
module must never print, log, or return the token value itself.

    python3 instagram_publish.py <public_image_url> "<caption>"
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request

import instagram_runtime

GRAPH = "https://graph.instagram.com/v21.0"


def _post(url: str, params: dict) -> dict:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(url: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url}?{qs}", timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def publish_image(image_url: str, caption: str) -> dict:
    return _publish(caption, image_url=image_url)


def publish_video(video_url: str, caption: str) -> dict:
    """Instagram video posts are Reels -- a distinct media_type + video_url,
    not image_url. A prior bug used image_url for a video file, which
    Instagram silently mishandled (re-served a cached image instead of
    erroring) rather than rejecting outright -- always verify media_type on
    the published result matches what was intended."""
    return _publish(caption, video_url=video_url, media_type="REELS")


def _publish(caption: str, *, image_url: str | None = None, video_url: str | None = None,
            media_type: str | None = None) -> dict:
    tok = instagram_runtime.load_token()
    if tok is None or not tok.get("access_token") or not tok.get("ig_user_id"):
        return {"ok": False, "error": "no stored token/ig_user_id"}
    token = tok["access_token"]
    ig_user_id = tok["ig_user_id"]

    params = {"caption": caption, "access_token": token}
    if image_url:
        params["image_url"] = image_url
    if video_url:
        params["video_url"] = video_url
    if media_type:
        params["media_type"] = media_type

    container = _post(f"{GRAPH}/{ig_user_id}/media", params)
    if "id" not in container:
        return {"ok": False, "stage": "create_container", "error": container}
    creation_id = container["id"]

    max_polls = 60 if video_url else 20  # Reels processing takes noticeably longer than images
    for _ in range(max_polls):
        status = _get(f"{GRAPH}/{creation_id}", {
            "fields": "status_code", "access_token": token,
        })
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            return {"ok": False, "stage": "container_processing", "error": status}
        time.sleep(3)
    else:
        return {"ok": False, "stage": "container_processing",
               "error": "timed out waiting", "creation_id": creation_id}

    result = _post(f"{GRAPH}/{ig_user_id}/media_publish", {
        "creation_id": creation_id, "access_token": token,
    })
    if "id" not in result:
        return {"ok": False, "stage": "media_publish", "error": result}

    # Verify the published media_type actually matches what was requested --
    # the exact check that would have caught the image/video mixup earlier.
    check = _get(f"{GRAPH}/{result['id']}", {"fields": "media_type,permalink",
                                             "access_token": token})
    expected = "VIDEO" if video_url else "IMAGE"
    actual = check.get("media_type")
    if actual and actual not in (expected, "CAROUSEL_ALBUM"):
        return {"ok": False, "stage": "verify", "media_id": result["id"],
               "error": f"published as {actual}, expected {expected} -- do not trust this post"}
    return {"ok": True, "media_id": result["id"], "media_type": actual,
           "permalink": check.get("permalink")}


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) < 2:
        print(json.dumps({"ok": False, "error": "usage: instagram_publish.py <media_url> <caption> [--video]"}))
        return 1
    url, caption = argv[0], argv[1]
    is_video = "--video" in argv[2:] or url.lower().endswith((".mp4", ".mov", ".webm"))
    result = publish_video(url, caption) if is_video else publish_image(url, caption)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
