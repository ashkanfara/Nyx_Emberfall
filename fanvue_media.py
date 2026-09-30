#!/usr/bin/env python3
"""Fanvue vault media upload (POST /v1/media/uploads multipart flow).

    python3 fanvue_media.py <local_file_path> <media_type: image|video>
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import fanvue_runtime

API_BASE = "https://api.fanvue.com"
PART_SIZE_FALLBACK = 6 * 1024 * 1024  # matches the API's own example partSize


def _api(method: str, path: str, token: str, *, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API_BASE}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except ValueError:
                return raw  # some endpoints (signed part URLs) return text/plain
    except urllib.error.HTTPError as exc:
        return {"_error": True, "status": exc.code, "body": exc.read().decode("utf-8", "replace")}


def list_media(*, size: int = 50) -> dict:
    """Lists existing Fanvue vault media (GET /v1/media) -- the real,
    already-authorized assets a PPV offer may reference. Only 'ready'
    items are returned (created/processing/error are not usable yet).
    Never invents a media_uuid -- this is the ONLY legitimate source of
    real ones for the Sales Manager to choose from."""
    tok = fanvue_runtime.load_credentials()
    token = tok.get("access_token")
    if not token:
        return {"ok": False, "error": "no stored access_token"}
    result = _api("GET", f"/v1/media?size={size}", token)
    if isinstance(result, dict) and result.get("_error"):
        return {"ok": False, "stage": "list_media", "error": result}
    items = [
        {"uuid": m["uuid"], "media_type": m.get("mediaType"), "name": m.get("name"),
         "caption": m.get("caption"), "description": m.get("description")}
        for m in result.get("data", []) if m.get("status") == "ready" and m.get("uuid")
    ]
    return {"ok": True, "media": items}


def upload_media(file_path: str, media_type: str) -> dict:
    """Uploads a local file to the Fanvue vault, returns {"ok": bool, "media_uuid": ...}."""
    tok = fanvue_runtime.load_credentials()
    token = tok.get("access_token")
    if not token:
        return {"ok": False, "error": "no stored access_token"}

    p = Path(file_path)
    if not p.is_file():
        return {"ok": False, "error": f"file not found: {file_path}"}
    size_bytes = p.stat().st_size

    session = _api("POST", "/v1/media/uploads", token, body={
        "name": p.stem, "filename": p.name, "mediaType": media_type, "sizeBytes": size_bytes,
    })
    if session.get("_error") or "uploadId" not in session:
        return {"ok": False, "stage": "create_session", "error": session}

    upload_id = session["uploadId"]
    media_uuid = session["mediaUuid"]
    part_size = session.get("partSize", PART_SIZE_FALLBACK)
    total_parts = session.get("totalParts") or -(-size_bytes // part_size)

    data = p.read_bytes()
    parts_result = []
    for part_number in range(1, total_parts + 1):
        url_resp = _api("GET", f"/v1/media/uploads/{upload_id}/parts/{part_number}/url", token)
        if isinstance(url_resp, dict) and url_resp.get("_error"):
            return {"ok": False, "stage": "get_part_url", "part": part_number, "error": url_resp}
        signed_url = url_resp if isinstance(url_resp, str) else url_resp.get("url") or url_resp

        start = (part_number - 1) * part_size
        chunk = data[start:start + part_size]
        put_req = urllib.request.Request(signed_url, data=chunk, method="PUT")
        try:
            with urllib.request.urlopen(put_req, timeout=120) as put_resp:
                etag = put_resp.headers.get("ETag", "").strip('"')
        except urllib.error.HTTPError as exc:
            return {"ok": False, "stage": "put_part", "part": part_number,
                   "error": exc.read().decode("utf-8", "replace")}
        parts_result.append({"PartNumber": part_number, "ETag": etag})

    complete = _api("PATCH", f"/v1/media/uploads/{upload_id}", token, body={"parts": parts_result})
    if isinstance(complete, dict) and complete.get("_error"):
        return {"ok": False, "stage": "complete_upload", "error": complete}

    return {"ok": True, "media_uuid": media_uuid}


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) < 2:
        print(json.dumps({"ok": False, "error": "usage: fanvue_media.py <file_path> <image|video>"}))
        return 1
    result = upload_media(argv[0], argv[1])
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
