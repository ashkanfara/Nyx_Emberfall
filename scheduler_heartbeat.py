"""Self-reported scheduler health. The scheduled task (a fresh Claude run
with zero memory of any conversation) writes this at the start and end of
every tick -- this is the ONLY truthful way to know whether a tick actually
ran, succeeded, or silently failed, since the dashboard's own server process
cannot query the scheduler directly. Never infer scheduler health from
venture.json/audit-log activity alone -- a correct no-op tick may write
nothing to either."""

from __future__ import annotations

import json
import stat
from datetime import datetime, timezone
from pathlib import Path

PATH = Path(__file__).resolve().parent / "state" / "scheduler_heartbeat.json"

# acted    = a real external action happened (message sent, manager dispatched work)
# checked  = ran its checks, nothing changed, nothing blocked
# degraded = ran, but at least one component is blocked/failed (see summary)
# no_op    = legacy value, kept valid for old rows
# error    = the tick itself crashed
# skipped  = the tick deliberately did not run (see record_tick_skipped)
OUTCOMES = ("no_op", "checked", "degraded", "acted", "error", "skipped")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict:
    if not PATH.is_file():
        return {}
    try:
        return json.loads(PATH.read_text())
    except (OSError, ValueError):
        return {}


def _write(d: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    PATH.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)


def record_tick_start() -> None:
    d = _load()
    d["last_attempted_tick"] = _now()
    d["tick_in_progress"] = True
    # A real run clears the previous skip's reason: left behind, it reads as
    # the CURRENT state and misdescribes why the last tick did nothing
    # (observed 2026-09-22 -- a day-old "relay_paused_write" sat beside a
    # completed "degraded" tick). last_attempted_tick vs the clock is what
    # tells you whether the scheduler is firing at all.
    d.pop("last_skip_reason", None)
    _write(d)


def record_tick_end(*, outcome: str, summary: str, scheduler_enabled: bool | None = None,
                    next_run_at: str | None = None, cron_expression: str | None = None) -> None:
    """outcome: one of OUTCOMES. scheduler_enabled/next_run_at/cron_expression
    are the tick's own self-check via list_scheduled_tasks, if it called it --
    optional, since a genuinely errored tick may not get that far."""
    assert outcome in OUTCOMES, f"unknown outcome {outcome!r}"
    d = _load()
    started = d.get("last_attempted_tick")
    ended = _now()
    duration_s = None
    if started:
        try:
            t0 = datetime.fromisoformat(started)
            t1 = datetime.fromisoformat(ended)
            duration_s = round((t1 - t0).total_seconds(), 1)
        except ValueError:
            duration_s = None

    d["tick_in_progress"] = False
    d["last_tick_outcome"] = outcome
    d["last_tick_summary"] = summary
    d["last_tick_duration_s"] = duration_s
    if outcome == "error":
        d["consecutive_failures"] = d.get("consecutive_failures", 0) + 1
    else:
        d["last_successful_tick"] = ended
        d["consecutive_failures"] = 0
    if scheduler_enabled is not None:
        d["scheduler_enabled_self_reported"] = scheduler_enabled
    if next_run_at:
        d["next_run_at_self_reported"] = next_run_at
    if cron_expression:
        d["cron_expression_self_reported"] = cron_expression
    _write(d)


def record_tick_skipped(*, reason: str, summary: str) -> None:
    """The scheduler fired but the tick deliberately ran nothing (e.g. the
    relay held the project's write lock). The attempt is recorded so the
    dashboard never mistakes it for a stall, but it is neither a success nor
    a failure: last_successful_tick and consecutive_failures are untouched.
    Nothing is queued -- the next scheduled tick runs normally."""
    d = _load()
    d["last_attempted_tick"] = _now()
    d["tick_in_progress"] = False
    d["last_tick_outcome"] = "skipped"
    d["last_tick_summary"] = summary
    d["last_tick_duration_s"] = 0.0
    d["last_skip_reason"] = reason
    _write(d)


def status() -> dict:
    """For the dashboard -- never claims a tick succeeded that never ran."""
    d = _load()
    if not d:
        return {"ever_run": False}
    return {
        "ever_run": True,
        "last_attempted_tick": d.get("last_attempted_tick"),
        "last_successful_tick": d.get("last_successful_tick"),
        "last_tick_outcome": d.get("last_tick_outcome"),
        "last_tick_summary": d.get("last_tick_summary"),
        "last_tick_duration_s": d.get("last_tick_duration_s"),
        "consecutive_failures": d.get("consecutive_failures", 0),
        "tick_in_progress": d.get("tick_in_progress", False),
        "scheduler_enabled_self_reported": d.get("scheduler_enabled_self_reported"),
        "next_run_at_self_reported": d.get("next_run_at_self_reported"),
        "cron_expression_self_reported": d.get("cron_expression_self_reported"),
    }
