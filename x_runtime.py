"""Local X (Twitter) OAuth2 credential storage -- same discipline as
fanvue_runtime.py: lives under .x_runtime/ (chmod 700, gitignored), NEVER
logged, NEVER printed, NEVER returned to a caller that displays it, NEVER
written to venture.json/activity_log.

This module only records WHETHER a credential is stored (booleans/timestamps
for the UI and audit trail) -- never the value itself. Nothing in this file
ever performs the OAuth consent step -- that happens in the founder's own
browser against X's own developer portal / authorize screen."""

from __future__ import annotations

import json
import stat
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parent / ".x_runtime"
CRED_PATH = RUNTIME_DIR / "credentials.json"


def _ensure_dir() -> None:
    RUNTIME_DIR.mkdir(exist_ok=True)
    RUNTIME_DIR.chmod(stat.S_IRWXU)  # 700 -- owner only


def load_credentials() -> dict:
    """For use by scripts that need client_id/access_token to make an API
    call -- never call this to display/log/print the result."""
    if not CRED_PATH.is_file():
        return {}
    return json.loads(CRED_PATH.read_text())


def store_client(*, client_id: str, client_secret: str = "") -> None:
    """Founder pastes these from the X Developer Portal (Project -> App ->
    Keys and tokens) into a local call to this function -- never into chat."""
    _ensure_dir()
    creds = load_credentials()
    creds["client_id"] = client_id
    if client_secret:
        creds["client_secret"] = client_secret
    CRED_PATH.write_text(json.dumps(creds))
    CRED_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600


def store_tokens(*, access_token: str, refresh_token: str, expires_in: int,
                 scope: str = "") -> None:
    _ensure_dir()
    creds = load_credentials()
    creds["access_token"] = access_token
    creds["refresh_token"] = refresh_token
    creds["expires_in"] = expires_in
    creds["scope"] = scope
    CRED_PATH.write_text(json.dumps(creds))
    CRED_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600


def has_client_registered() -> bool:
    """True once the founder has placed client_id from the X Developer
    Portal -- BEFORE any OAuth token exists. Presence-only, never exposes
    the value."""
    return bool(load_credentials().get("client_id"))


def has_access_token() -> bool:
    return bool(load_credentials().get("access_token"))
