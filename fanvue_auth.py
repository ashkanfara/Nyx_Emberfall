#!/usr/bin/env python3
"""Fanvue OAuth 2.0 + PKCE code exchange and refresh.

Per https://api.fanvue.com/docs/authentication/overview : the token endpoint
authenticates with client_id/client_secret via HTTP Basic Auth and takes the
authorization code plus the original PKCE code_verifier in the body. This
module reads client_secret from local storage in-memory only and never
prints/logs/returns raw token values to a caller that would display them --
callers get back an {"ok": bool, ...} result with secrets omitted.

    python3 fanvue_auth.py exchange <code> <state>
    python3 fanvue_auth.py refresh
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

import fanvue_runtime

TOKEN_URL = "https://auth.fanvue.com/oauth2/token"
AUTHORIZE_URL = "https://auth.fanvue.com/oauth2/auth"  # from the live openapi-v1.json securitySchemes
API_BASE = "https://api.fanvue.com"
REDIRECT_URI = "http://127.0.0.1:8790/fanvue/oauth/callback"


def build_authorize_url(scopes: list[str]) -> str:
    """Starts a fresh authorization-code+PKCE flow for a re-consent (e.g.
    scope expansion) -- never call this to fabricate consent; it only
    produces the URL a human must open and click Allow on themselves.
    Persists the PKCE verifier/state so exchange_code_for_tokens can
    complete the flow afterward."""
    creds = fanvue_runtime.load_credentials()
    client_id = creds.get("client_id")
    if not client_id:
        raise RuntimeError("no client_id in local credentials store")

    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    state = secrets.token_urlsafe(24)

    fanvue_runtime.store_pending_pkce(state=state, code_verifier=code_verifier)

    params = {
        "client_id": client_id, "redirect_uri": REDIRECT_URI, "response_type": "code",
        "scope": " ".join(scopes), "state": state,
        "code_challenge": code_challenge, "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _post_form(url: str, data: dict, *, basic_auth: tuple[str, str]) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    user, pwd = basic_auth
    token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"error": "http_error", "status": exc.code, "body": exc.read().decode("utf-8", "replace")}


def exchange_code_for_tokens(code: str, state: str) -> dict:
    """Complete step 4 of the OAuth flow using the code from the redirect
    and the PKCE verifier generated before sending the user to authorize.
    Returns only non-secret confirmation fields -- never the tokens."""
    pending = fanvue_runtime.load_pending_pkce()
    if pending is None:
        return {"ok": False, "error": "no pending PKCE verifier/state stored locally"}
    if state != pending.get("state"):
        return {"ok": False, "error": "state mismatch -- possible CSRF, refusing to exchange"}

    creds = fanvue_runtime.load_credentials()
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")
    if not client_id or not client_secret:
        return {"ok": False, "error": "no client_id/client_secret in local credentials store"}

    result = _post_form(TOKEN_URL, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "http://127.0.0.1:8790/fanvue/oauth/callback",
        "code_verifier": pending["code_verifier"],
    }, basic_auth=(client_id, client_secret))

    if "access_token" not in result:
        return {"ok": False, "stage": "token_exchange", "error": result}

    fanvue_runtime.store_tokens(
        access_token=result["access_token"],
        refresh_token=result.get("refresh_token", ""),
        expires_in=result.get("expires_in", 0),
        scope=result.get("scope", ""),
    )
    fanvue_runtime.clear_pending_pkce()
    return {"ok": True, "expires_in": result.get("expires_in"), "scope": result.get("scope"),
           "token_type": result.get("token_type")}


def refresh_access_token() -> dict:
    """Refresh tokens rotate and are single-use (Fanvue docs) -- always
    persist the new refresh_token from the response and discard the old one,
    which store_tokens does by overwriting the credentials file."""
    creds = fanvue_runtime.load_credentials()
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")
    refresh_token = creds.get("refresh_token")
    if not refresh_token:
        return {"ok": False, "error": "no refresh_token in local credentials store"}

    result = _post_form(TOKEN_URL, {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }, basic_auth=(client_id, client_secret))

    if "access_token" not in result:
        return {"ok": False, "stage": "refresh", "error": result}

    fanvue_runtime.store_tokens(
        access_token=result["access_token"],
        refresh_token=result.get("refresh_token", refresh_token),
        expires_in=result.get("expires_in", 0),
        scope=result.get("scope", creds.get("scope", "")),
    )
    return {"ok": True, "expires_in": result.get("expires_in")}


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    usage = "usage: exchange <code> <state> | refresh | authorize-url <scope1,scope2,...>"
    if not argv:
        print(json.dumps({"ok": False, "error": usage}))
        return 1
    if argv[0] == "exchange" and len(argv) == 3:
        result = exchange_code_for_tokens(argv[1], argv[2])
    elif argv[0] == "refresh":
        result = refresh_access_token()
    elif argv[0] == "authorize-url" and len(argv) == 2:
        result = {"ok": True, "url": build_authorize_url(argv[1].split(","))}
    else:
        result = {"ok": False, "error": usage}
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
