"""Skip the hourly tick while the ChatGPT->GitHub->Claude relay is editing
this project (Founder decision, 2026-09-21).

The relay (~/Projects/chatgpt-claude-bridge) holds data/locks/ps05.lock for
the whole of a PS-05 write task -- every segment, verification and the local
checkpoint commit. A tick that ran in that window would import and execute
half-edited application code. So ps05_tick.py calls this BEFORE importing any
other PS-05 module: if the lock is held, it records `skipped: relay_write_lock`
in the heartbeat and exits. Nothing is queued; the next scheduled tick simply
runs as usual once the lock is gone (no catch-up run, no schedule change).

A relay write task that PAUSES or FAILS releases the lock but can leave its
own uncommitted edits in this tree. For that the relay keeps a durable marker
per task in data/locks/ps05.unresolved/<task>.json (not a lock, no pid). The
tick also skips, with `skipped: relay_paused_write`, while any marker is
`running` (a crash mid-task) or lists a path that still differs from HEAD
here. Once those paths are committed (a verified continuation) or restored,
the marker no longer blocks, even before the relay prunes it.

Stdlib only, plus scheduler_heartbeat (itself stdlib only). Read-only towards
the lock and the markers: it never creates, removes or "reclaims" either --
that is the relay's job.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

LOCK_PATH = Path(os.environ.get(
    "PS05_RELAY_LOCK_PATH",
    Path.home() / "Projects" / "chatgpt-claude-bridge" / "data" / "locks" / "ps05.lock",
))
UNRESOLVED_DIR = Path(os.environ.get("PS05_RELAY_UNRESOLVED_DIR", LOCK_PATH.parent / "ps05.unresolved"))
PROJECT_ROOT = Path(__file__).resolve().parent
SKIP_REASON = "relay_write_lock"
PAUSED_REASON = "relay_paused_write"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def relay_lock_holder(path: Path | None = None) -> dict | None:
    """The lock's contents if the relay currently holds it, else None.

    A lock whose holder process is gone is stale (the relay reclaims those
    itself) and does not block the tick. A lock that exists but cannot be read
    or parsed -- e.g. caught mid-write -- is treated as HELD: skipping one tick
    is cheap, running over a half-edited tree is not."""
    path = LOCK_PATH if path is None else path
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None
    except OSError:
        return {"task_id": None, "unreadable": True}
    try:
        info = json.loads(raw)
        pid = int(info["pid"])
    except (ValueError, KeyError, TypeError):
        return {"task_id": None, "unreadable": True}
    return info if _alive(pid) else None


def _dirty(root: Path, paths: list[str]) -> list[str] | None:
    """Which of `paths` differ from HEAD in `root` (None if git cannot tell)."""
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *paths],
            cwd=root, capture_output=True, text=True, timeout=30,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    out, parts, i = [], r.stdout.split("\0"), 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) >= 4:
            out.append(entry[3:])
            if entry[0] in "RC":
                i += 1  # a rename's original path follows
        i += 1
    return out


def unresolved_relay_writes(directory: Path | None = None, root: Path | None = None) -> list[dict]:
    """Relay write tasks whose edits this tree must not run: markers still
    `running`, unreadable, or listing a path that differs from HEAD (or when
    git cannot tell). Each entry says why."""
    directory = UNRESOLVED_DIR if directory is None else directory
    root = PROJECT_ROOT if root is None else root
    try:
        files = sorted(directory.glob("*.json"))
    except OSError:
        return []
    unsafe = []
    for f in files:
        try:
            m = json.loads(f.read_text())
            if not isinstance(m, dict):
                raise ValueError
        except (OSError, ValueError):
            unsafe.append({"task_id": f.stem, "issue": None, "why": "marker unreadable", "paths": []})
            continue
        base = {"task_id": m.get("task_id") or f.stem, "issue": m.get("issue"),
                "stop_reason": m.get("stop_reason")}
        if m.get("state") == "running":
            unsafe.append({**base, "why": "write task still marked running (interrupted?)", "paths": []})
            continue
        paths = [p for p in (m.get("paths") or []) if isinstance(p, str) and p]
        if not paths:
            continue
        dirty = _dirty(root, paths)
        if dirty is None:
            unsafe.append({**base, "why": "git status failed", "paths": paths})
        elif dirty:
            unsafe.append({**base, "why": "task edits still uncommitted", "paths": sorted(set(dirty))})
    return unsafe


def skip_if_relay_holds_lock() -> dict | None:
    """Called by ps05_tick.py before anything else. Returns the skip record
    (and the tick must exit) when the relay holds the lock or has unresolved
    write edits in this tree, else None."""
    holder = relay_lock_holder()
    if holder is not None:
        import scheduler_heartbeat
        task = holder.get("task_id") or "unknown task (lock unreadable)"
        summary = f"skipped: {SKIP_REASON} (relay task {task}, since {holder.get('started_at', '?')})"
        scheduler_heartbeat.record_tick_skipped(reason=SKIP_REASON, summary=summary)
        return {"skipped": SKIP_REASON, "relay_task_id": holder.get("task_id"),
                "lock_started_at": holder.get("started_at"), "summary": summary}
    unsafe = unresolved_relay_writes()
    if not unsafe:
        return None
    import scheduler_heartbeat
    first = unsafe[0]
    who = f"issue #{first['issue']}" if first.get("issue") else f"task {first['task_id']}"
    paths = ", ".join(first["paths"][:5]) or "-"
    more = f" (+{len(unsafe) - 1} more)" if len(unsafe) > 1 else ""
    summary = (f"skipped: {PAUSED_REASON} (relay {who}, {first.get('stop_reason') or first['why']}; "
               f"{first['why']}: {paths}){more}")
    scheduler_heartbeat.record_tick_skipped(reason=PAUSED_REASON, summary=summary)
    return {"skipped": PAUSED_REASON, "unresolved": unsafe, "summary": summary}
