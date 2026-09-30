"""Append-only activity log -- forked pattern from research-orchestrator's
audit_log.jsonl (_append_audit/load_audit_log), generalized beyond email/run
transitions to any Manager/agent action."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent / "state" / "activity_log.jsonl"


def append(*, actor: str, action: str, detail: str, cost_usd: float | None = None) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": actor,      # "manager" | "founder" | "correction"
        "action": action,    # e.g. "research_subtask", "compliance_check", "approval_requested"
        "detail": detail,
        "cost_usd": cost_usd,
    }
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load(limit: int | None = None) -> list[dict]:
    if not LOG_PATH.is_file():
        return []
    lines = LOG_PATH.read_text().splitlines()
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out[-limit:] if limit else out
