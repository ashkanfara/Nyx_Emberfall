"""Local Alibaba Cloud Model Studio (Wan) credential storage -- same
discipline as instagram_runtime.py/fanvue_runtime.py: lives under
.wan_runtime/ (chmod 700, gitignored), NEVER logged, NEVER printed, NEVER
returned from any function here, NEVER placed in a prompt, NEVER written to
venture.json/activity_log.

This module only records WHETHER a credential is stored (booleans for the
UI and audit trail) -- never the value itself. The founder obtains the key
by completing Alibaba Cloud Model Studio's own account/billing/API-key
setup (out of scope for this system to perform) and it lands here only via
setup_wan_credentials.py, never pasted into chat."""

from __future__ import annotations

import json
import stat
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parent / ".wan_runtime"
CREDENTIALS_PATH = RUNTIME_DIR / "credentials.json"


def _ensure_dir() -> None:
    RUNTIME_DIR.mkdir(exist_ok=True)
    RUNTIME_DIR.chmod(stat.S_IRWXU)  # 700 -- owner only


def store_api_key(api_key: str, *, region: str = "ap-southeast-1") -> None:
    """Write the DashScope/Model Studio API key locally. Caller must never
    have printed/logged api_key before calling this."""
    _ensure_dir()
    CREDENTIALS_PATH.write_text(json.dumps({"api_key": api_key, "region": region}))
    CREDENTIALS_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600


def has_credentials() -> bool:
    return CREDENTIALS_PATH.is_file()


def load_api_key() -> dict | None:
    """For use by wan_provider.py to make an API call -- never call this to
    display/log/print the result. Returns None if no key has been stored
    yet (the system must stay inert until the founder completes setup)."""
    if not CREDENTIALS_PATH.is_file():
        return None
    return json.loads(CREDENTIALS_PATH.read_text())
