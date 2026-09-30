"""Minimal persistent per-fan relationship state, so the Sales Manager has
continuity without requiring founder memory. Deliberately small -- only
what's needed to avoid re-treating a known fan as new and to track where
they are in visitor->subscriber->payer->repeat->retained.

Stores only operational, non-sensitive fields (platform id, handle, status,
interaction history, revenue, relationship stage) -- never anything beyond
what the platform itself already surfaces publicly for a subscriber."""

from __future__ import annotations

import json
import stat
from datetime import datetime, timezone
from pathlib import Path

PATH = Path(__file__).resolve().parent / "state" / "fan_memory.json"

RELATIONSHIP_STAGES = ("trial", "subscriber", "payer", "repeat_payer", "cancelled", "lapsed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _key(platform: str, fan_id: str) -> str:
    return f"{platform}:{fan_id}"


def load_all() -> dict:
    if not PATH.is_file():
        return {}
    try:
        return json.loads(PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save_all(d: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    PATH.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)


def get(platform: str, fan_id: str) -> dict | None:
    return load_all().get(_key(platform, fan_id))


def upsert_fan(platform: str, fan_id: str, *, handle: str, first_seen: str,
              relationship_stage: str, revenue_usd: float = 0.0) -> dict:
    assert relationship_stage in RELATIONSHIP_STAGES, f"unknown stage {relationship_stage!r}"
    all_fans = load_all()
    key = _key(platform, fan_id)
    entry = all_fans.get(key) or {
        "platform": platform, "fan_id": fan_id, "handle": handle,
        "first_seen": first_seen, "interaction_history": [], "revenue_usd": 0.0,
        "next_recommended_action": None,
    }
    entry["handle"] = handle
    entry["relationship_stage"] = relationship_stage
    entry["revenue_usd"] = revenue_usd
    entry["last_interaction_at"] = _now()
    all_fans[key] = entry
    _save_all(all_fans)
    return entry


def record_interaction(platform: str, fan_id: str, *, kind: str, detail: str,
                       next_recommended_action: str | None = None,
                       meta: dict | None = None) -> dict:
    """kind: e.g. comment_received, comment_moderated, reply_sent, spam_flagged.

    meta: optional small structured fields (e.g. intent_classification,
    inbound_message_at, action) alongside the free-text detail -- added so
    sales-loop KPIs (response latency, spam rate, etc.) can be computed
    reliably instead of parsing prose. Never required; existing callers with
    only kind/detail are unaffected."""
    all_fans = load_all()
    key = _key(platform, fan_id)
    entry = all_fans.setdefault(key, {
        "platform": platform, "fan_id": fan_id, "handle": "", "first_seen": _now(),
        "interaction_history": [], "revenue_usd": 0.0, "relationship_stage": "trial",
    })
    record = {"ts": _now(), "kind": kind, "detail": detail}
    if meta:
        record["meta"] = meta
    entry["interaction_history"].append(record)
    entry["last_interaction_at"] = _now()
    if next_recommended_action is not None:
        entry["next_recommended_action"] = next_recommended_action
    _save_all(all_fans)
    return entry
