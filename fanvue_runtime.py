"""Local Fanvue credential storage -- same discipline as instagram_runtime.py:
lives under .fanvue_runtime/ (chmod 700, gitignored), NEVER logged, NEVER
printed, NEVER returned to a caller that displays it, NEVER written to
venture.json/activity_log.

This module only records WHETHER a credential is stored (booleans/timestamps
for the UI and audit trail) -- never the value itself."""

from __future__ import annotations

import json
import stat
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parent / ".fanvue_runtime"
CRED_PATH = RUNTIME_DIR / "credentials.json"
PENDING_PATH = RUNTIME_DIR / "oauth_pending.json"


def _ensure_dir() -> None:
    RUNTIME_DIR.mkdir(exist_ok=True)
    RUNTIME_DIR.chmod(stat.S_IRWXU)  # 700 -- owner only


def load_credentials() -> dict:
    """For use by scripts that need app_id/client_id/client_secret/tokens to
    make an API call -- never call this to display/log/print the result."""
    if not CRED_PATH.is_file():
        return {}
    return json.loads(CRED_PATH.read_text())


def store_tokens(*, access_token: str, refresh_token: str, expires_in: int,
                 scope: str = "") -> None:
    """Merge freshly-issued tokens into the existing credentials file
    (which already holds app_id/client_id/client_secret). Caller must never
    have printed/logged the token values before calling this."""
    _ensure_dir()
    creds = load_credentials()
    creds["access_token"] = access_token
    creds["refresh_token"] = refresh_token
    creds["expires_in"] = expires_in
    creds["scope"] = scope
    CRED_PATH.write_text(json.dumps(creds))
    CRED_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600


def has_access_token() -> bool:
    return bool(load_credentials().get("access_token"))


def store_pending_pkce(*, state: str, code_verifier: str) -> None:
    """Persists the PKCE verifier + CSRF state generated before sending the
    founder to Fanvue's consent screen, so exchange_code_for_tokens can
    complete the flow after they click Allow and are redirected back."""
    _ensure_dir()
    PENDING_PATH.write_text(json.dumps({"state": state, "code_verifier": code_verifier}))
    PENDING_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600


def load_pending_pkce() -> dict | None:
    if not PENDING_PATH.is_file():
        return None
    return json.loads(PENDING_PATH.read_text())


def clear_pending_pkce() -> None:
    if PENDING_PATH.is_file():
        PENDING_PATH.unlink()
