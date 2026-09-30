#!/usr/bin/env python3
"""Fanvue chat (DM) read/send -- requires read:chat/write:chat scope.
Same discipline as fanvue_posts.py: verify what actually happened via a
real API response, never trust intent alone; never fabricate message
content or pretend a send succeeded without checking the response.

    python3 fanvue_chat.py list-chats
    python3 fanvue_chat.py list-messages <user_uuid>
    python3 fanvue_chat.py send <user_uuid> "<text>"
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
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
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        return {"_error": True, "status": exc.code, "body": exc.read().decode("utf-8", "replace")}
    except urllib.error.URLError as exc:
        # Network-level failure (timeout, connection reset, DNS, etc.) --
        # distinct from an HTTP error response: we genuinely don't know
        # whether the request reached Fanvue at all, so this must never be
        # silently treated as either "sent" or "failed".
        return {"_network_error": True, "reason": str(exc.reason)}


def _token() -> str | None:
    return fanvue_runtime.load_credentials().get("access_token")


def list_chats(*, size: int = 50) -> dict:
    """Chats sorted by Fanvue's own default (most recently active first)."""
    token = _token()
    if not token:
        return {"ok": False, "error": "no stored access_token"}
    result = _api("GET", f"/v1/chats?{urllib.parse.urlencode({'size': size})}", token)
    if isinstance(result, dict) and result.get("_error"):
        return {"ok": False, "stage": "list_chats", "error": result}
    return {"ok": True, "chats": result.get("data", [])}


def list_messages(user_uuid: str, *, limit: int = 50, mark_as_read: bool = False) -> dict:
    """Returns messages newest-first (Fanvue's own order) for one chat.
    mark_as_read=False by default -- reading via this script for analysis
    should not silently clear the fan's unread badge; callers that are
    actually about to respond should pass True."""
    token = _token()
    if not token:
        return {"ok": False, "error": "no stored access_token"}
    qs = urllib.parse.urlencode({"limit": limit, "markAsRead": "true" if mark_as_read else "false"})
    result = _api("GET", f"/v1/chats/{user_uuid}/messages?{qs}", token)
    if isinstance(result, dict) and result.get("_error"):
        return {"ok": False, "stage": "list_messages", "error": result}
    return {"ok": True, "messages": result.get("data", [])}


def send_message(user_uuid: str, text: str, *, price_cents: int | None = None,
                 media_uuids: list[str] | None = None) -> dict:
    """price_cents, if set, must be >= 300 ($3.00) per Fanvue's own minimum
    -- sends a pay-to-view message; per Fanvue's own API, a PPV message
    cannot be sent without at least one attached media_uuid, so this
    refuses locally rather than making a request guaranteed to 400.

    Response parsing (2026-09-13 fix): Fanvue's OWN documented response
    schema for POST /v1/chats/{userUuid}/message returns {"messageUuid":
    "<uuid>"} on success -- NOT "uuid" (confirmed against the real
    OpenAPI spec). The previous code checked for "uuid", which never
    appears in this endpoint's response, so every historically successful
    send was misclassified as a failure. Both field names are accepted
    here for forward/backward compatibility, in case a different Fanvue
    endpoint or a future API revision uses "uuid" instead.

    Returns one of three "status" values so callers can distinguish real
    outcomes rather than guessing:
      SENT_CONFIRMED    -- the response contained a real message id.
      FAILED            -- a genuine HTTP error response (e.g. 400).
      DELIVERY_UNCERTAIN -- a network-level failure (timeout/connection
                            reset) where we truly don't know if Fanvue
                            received the request; the caller MUST verify
                            via verify_recent_send() before ever
                            considering a retry, never resend blindly."""
    token = _token()
    if not token:
        return {"ok": False, "status": "FAILED", "error": "no stored access_token"}
    if price_cents is not None and price_cents < 300:
        return {"ok": False, "status": "FAILED",
               "error": f"price_cents must be >= 300 (Fanvue minimum), got {price_cents}"}
    if price_cents is not None and not media_uuids:
        # Fanvue itself rejects a PPV with no attached media (HTTP 400,
        # "It's not possible to send a pay-to-view message with no media
        # attached") -- refuse locally before ever making the request.
        return {"ok": False, "status": "FAILED",
               "error": "a PPV (price_cents set) requires at least one media_uuid -- "
                        "Fanvue rejects a priced message with no attached media"}

    body: dict = {"text": text}
    if price_cents is not None:
        body["price"] = price_cents
    if media_uuids:
        body["mediaUuids"] = media_uuids

    result = _api("POST", f"/v1/chats/{user_uuid}/message", token, body=body)
    if isinstance(result, dict) and result.get("_network_error"):
        return {"ok": False, "status": "DELIVERY_UNCERTAIN", "error": result}
    if isinstance(result, dict) and result.get("_error"):
        return {"ok": False, "status": "FAILED", "stage": "send_message", "error": result}
    message_uuid = result.get("messageUuid") or result.get("uuid")
    if not message_uuid:
        # A 2xx with neither expected field -- genuinely unexpected shape,
        # not a confirmed send. Treat as uncertain (verify, never assume).
        return {"ok": False, "status": "DELIVERY_UNCERTAIN", "stage": "send_message",
               "error": result}
    return {"ok": True, "status": "SENT_CONFIRMED", "message_uuid": message_uuid,
           "sent_at": result.get("sentAt")}


def verify_recent_send(user_uuid: str, *, creator_uuid: str, expected_text: str,
                       sent_after: str) -> dict:
    """Re-fetches the thread and checks whether WE (creator_uuid) actually
    sent a message matching expected_text after sent_after (ISO
    timestamp) -- the correct way to resolve a DELIVERY_UNCERTAIN result
    before ever considering a retry. Never resend based on a parser
    failure alone; always check reality first."""
    result = list_messages(user_uuid, mark_as_read=False)
    if not result.get("ok"):
        return {"ok": False, "verified": False, "error": result.get("error")}
    for m in result.get("messages", []):
        sender_uuid = (m.get("sender") or {}).get("uuid")
        if (sender_uuid == creator_uuid and m.get("text") == expected_text
                and (m.get("sentAt") or "") >= sent_after):
            return {"ok": True, "verified": True, "message": m}
    return {"ok": True, "verified": False}


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    usage = "usage: list-chats | list-messages <user_uuid> | send <user_uuid> \"<text>\""
    if not argv:
        print(json.dumps({"ok": False, "error": usage}))
        return 1
    if argv[0] == "list-chats":
        result = list_chats()
    elif argv[0] == "list-messages" and len(argv) == 2:
        result = list_messages(argv[1])
    elif argv[0] == "send" and len(argv) == 3:
        result = send_message(argv[1], argv[2])
    else:
        result = {"ok": False, "error": usage}
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
