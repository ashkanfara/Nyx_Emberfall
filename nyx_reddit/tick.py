"""Safe scheduled tick for the Reddit workstream.

What it does: refresh drafts for the newest public episode lock if they are
missing or stale, re-run the gate on every draft, regenerate the docs, and
return a compact status with the human actions still outstanding.

What it cannot do: post, comment, vote, message, log in, upload or fetch
anything. This package has no network code at all; the tick only reads and
writes files in the repo. It never raises into its caller.
"""

from __future__ import annotations

import hashlib
import json

from . import gate, matrix, readiness, variant
from .rules import route_closures, rule_problems
from .store import Store, parse_date, today

LOCKS = "brand/story_locks"


def _lock_sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def newest_public_lock(store: Store):
    best = None
    for path in sorted((store.root / LOCKS).glob("*.json")):
        try:
            lock = json.loads(path.read_text())
        except ValueError:
            continue
        if lock.get("tone") in variant.PUBLIC_TONES and lock.get("visual_policy") in variant.PUBLIC_VISUAL_POLICIES:
            key = (lock.get("locked_since") or "", path.name)
            if best is None or key > best[0]:
                best = (key, path, lock)
    return (best[1], best[2]) if best else (None, None)


def human_actions(store: Store, on=None) -> list[str]:
    on = on or today()
    acts = []
    acct = store.account()
    if not acct:
        acts.append("Record the account snapshot (age, karma, posts, profile disclosure).")
    else:
        if (on - parse_date(acct["recorded_at"])).days > 7:
            acts.append("Re-read the account on its profile page: age, karma, posts.")
        if acct.get("discloses_creator_role") is not True:
            acts.append("Make sure the profile says it is a creator account for an AI-made fictional adult character, "
                        "then record it.")
    act = store.activity()
    live = [c for c in act.get("comments", []) if not c.get("removed")]
    if len(live) < 10:
        acts.append(f"Warm-up: {len(live)}/10 useful on-topic comments over 14+ days, no Nyx mentions "
                    "(a human decision; this system never comments).")
    comms = store.communities()["communities"]
    order = store.communities().get("first_three_when_eligible", {}).get("order", [])
    for name in order + [n for n in comms if n not in order]:
        c = comms.get(name, {})
        if c.get("status") == "excluded" or c.get("route") == "closed":
            continue
        if name not in order and c.get("route") != "designated_thread":
            continue                             # only the top 3 and thread routes are worth a human's time now
        probs = rule_problems(c, on)
        if probs:
            acts.append(f"{name}: copy the exact rules text (rules page, sidebar, wiki, pinned posts), "
                        f"including flair options and any age/karma minimum ({len(probs)} open items).")
        if c.get("route") == "designated_thread" or "native_post" in route_closures(c):
            acts.append(f"{name}: find the designated self-promotion thread, copy its rules and its URL.")
    return acts


def run(store: Store | None = None) -> dict:
    store = store or Store()
    try:
        on = today()
        refreshed = None
        path, lock = newest_public_lock(store)
        if path is not None:
            sha = _lock_sha(path)
            mine = [d for d in store.drafts()
                    if d.get("story_id") == lock.get("story_id") and d.get("status") == "draft"]
            if not mine or any(d.get("lock_sha") != sha for d in mine):
                out = variant.prepare(path.relative_to(store.root), store)
                for d in store.drafts():
                    if d.get("story_id") == lock.get("story_id") and d.get("status") == "draft":
                        d["lock_sha"] = sha
                        store.save_draft(d)
                refreshed = {"story_id": out.get("story_id"), "drafts": [x["id"] for x in out.get("drafts", [])]}
        drafts = []
        for d in store.drafts():
            d["gate"] = gate.evaluate(store, d)
            store.save_draft(d)
            drafts.append(f"{d['id']}:{d['gate']['decision']}({len(d['gate']['blocks'])})")
        matrix.write(store)
        docs = store.root / "docs" / "reddit"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "readiness.md").write_text(readiness.render(store))
        r = readiness.compute(store)
        return {
            "ok": True, "checked_on": on.isoformat(), "posting": "disabled (no code path posts)",
            "ready_to_post": r["ready_to_post"], "next_recheck": r["next_recheck"],
            "account_age_gate": r["dates"].get("account_age_gate"),
            "episode": lock.get("story_id") if lock else None, "refreshed": refreshed,
            "drafts": drafts, "human_actions": human_actions(store, on),
        }
    except Exception as exc:  # noqa: BLE001 -- a tick must never break its scheduler
        return {"ok": False, "posting": "disabled (no code path posts)", "error": f"{type(exc).__name__}: {exc}"}
