"""Local Instagram credential storage -- same discipline as research-
orchestrator's .gmail_runtime/: lives under .instagram_runtime/ (chmod 700,
gitignored), NEVER logged, NEVER printed, NEVER returned from any function
here, NEVER placed in a prompt, NEVER written to venture.json/activity_log.

This module only records WHETHER a credential is stored (booleans/timestamps
for the UI and audit trail) -- never the value itself."""

from __future__ import annotations

import json
import stat
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parent / ".instagram_runtime"
TOKEN_PATH = RUNTIME_DIR / "token.json"


def _ensure_dir() -> None:
    RUNTIME_DIR.mkdir(exist_ok=True)
    RUNTIME_DIR.chmod(stat.S_IRWXU)  # 700 -- owner only


def store_token(access_token: str, *, ig_user_id: str = "", expires_at: str = "") -> None:
    """Write the token locally. Caller must never have printed/logged
    access_token before calling this -- read it once (e.g. from a page's DOM)
    and pass it straight here."""
    _ensure_dir()
    TOKEN_PATH.write_text(json.dumps({
        "access_token": access_token, "ig_user_id": ig_user_id, "expires_at": expires_at,
    }))
    TOKEN_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600


def has_token() -> bool:
    return TOKEN_PATH.is_file()


def load_token() -> dict | None:
    """For use by scripts that need the token to make an API call --
    never call this to display/log/print the result."""
    if not TOKEN_PATH.is_file():
        return None
    return json.loads(TOKEN_PATH.read_text())
