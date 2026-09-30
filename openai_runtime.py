"""Local OpenAI API credential storage -- same discipline as wan_runtime.py /
instagram_runtime.py / fanvue_runtime.py: lives under .openai_runtime/
(chmod 700, gitignored), NEVER logged, NEVER printed, NEVER returned from a
status function, NEVER placed in a prompt, NEVER written to venture.json /
activity_log / story JSON.

Stored for the premium Nyx still-image route (founder 2026-09-25). The
scheduler tick and relay tasks run PS-05 code as fresh processes that do not
inherit an interactive shell's environment, so the key is read from this file
and exported into OPENAI_API_KEY only inside the process that asks for it
(export_env), never into a plist, shell profile or tracked file.

Storing a key authorizes nothing: every paid call stays behind the existing
founder spend gates."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parent / ".openai_runtime"
CREDENTIALS_PATH = RUNTIME_DIR / "credentials.json"
ENV_VAR = "OPENAI_API_KEY"


def _ensure_dir() -> None:
    RUNTIME_DIR.mkdir(exist_ok=True)
    RUNTIME_DIR.chmod(stat.S_IRWXU)  # 700 -- owner only


def key_problems(api_key: str) -> list[str]:
    """Shape check only (no network): never echoes any part of the key."""
    k = api_key.strip()
    probs = []
    if not k.startswith("sk-"):
        probs.append("does not start with 'sk-'")
    if len(k) < 40:
        probs.append("too short to be an OpenAI API key")
    if any(c.isspace() for c in k):
        probs.append("contains whitespace")
    return probs


def store_api_key(api_key: str) -> None:
    """Write the key locally. Caller must never have printed/logged it."""
    _ensure_dir()
    CREDENTIALS_PATH.write_text(json.dumps({"api_key": api_key.strip()}))
    CREDENTIALS_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600


def has_credentials() -> bool:
    return CREDENTIALS_PATH.is_file()


def load_api_key() -> str | None:
    """For the provider making an API call -- never display/log the result.
    None until the founder has stored a key (the route stays inert)."""
    if not CREDENTIALS_PATH.is_file():
        return None
    return json.loads(CREDENTIALS_PATH.read_text()).get("api_key") or None


def export_env() -> bool:
    """Make OPENAI_API_KEY available to THIS process (and its children) from
    the local file, without overriding a value already set. Returns whether
    the variable is now present."""
    if not os.environ.get(ENV_VAR):
        key = load_api_key()
        if key:
            os.environ[ENV_VAR] = key
    return bool(os.environ.get(ENV_VAR))


def status() -> dict:
    """Booleans only -- safe to print."""
    mode = oct(CREDENTIALS_PATH.stat().st_mode & 0o777) if CREDENTIALS_PATH.is_file() else None
    dir_mode = oct(RUNTIME_DIR.stat().st_mode & 0o777) if RUNTIME_DIR.is_dir() else None
    key = load_api_key()
    return {"stored": key is not None, "path": str(CREDENTIALS_PATH), "file_mode": mode,
            "dir_mode": dir_mode, "shape_ok": bool(key) and not key_problems(key),
            "env_available_after_export": export_env()}


def _store_from_stdin() -> int:
    """`pbpaste | python3 openai_runtime.py store` -- the key never touches
    argv, the terminal or a log."""
    key = sys.stdin.read().strip()
    probs = key_problems(key)
    if probs:
        print(json.dumps({"ok": False, "stored": False, "error": "; ".join(probs)}))
        return 1
    store_api_key(key)
    print(json.dumps({"ok": True, **status()}))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "store":
        raise SystemExit(_store_from_stdin())
    print(json.dumps(status()))
