"""After-the-fact bookkeeping: account snapshot, warm-up comments, image
attachment, packets, posts and outcomes. Nothing here talks to Reddit."""

from __future__ import annotations

import hashlib
import json

from . import gate, policy
from .rules import NON_HUMAN_VERIFIERS
from .store import Store, sha256_file, today


class LedgerError(ValueError):
    pass


def _human(by: str) -> str:
    if by.strip().lower() in NON_HUMAN_VERIFIERS:
        raise LedgerError("must be recorded by a named human")
    return by.strip()


def record_account(store: Store, age_days: int, karma: int, discloses_creator_role: bool, by: str) -> dict:
    doc = {"age_days": int(age_days), "karma": int(karma),
           "discloses_creator_role": bool(discloses_creator_role),
           "recorded_by": _human(by), "recorded_at": today().isoformat()}
    store.save_account(doc)
    return doc


def log_comment(store: Store, community: str, url: str, summary: str, by: str, removed: bool = False) -> dict:
    if "reddit.com/" not in url:
        raise LedgerError("comment url must be a reddit.com permalink")
    act = store.activity()
    entry = {"community": community, "url": url, "summary": summary, "removed": removed,
             "date": today().isoformat(), "by": _human(by)}
    act["comments"].append(entry)
    if removed:
        act["halt"] = act.get("halt") or f"{today()}: warm-up comment removed in {community}"
    store.save_activity(act)
    return entry


def attach_image(store: Store, draft_id: str, role: str, path: str, sfw: bool, identity_qa: str, by: str) -> dict:
    """Record a Reddit-native image a human/executor generated from the draft's brief."""
    d = store.draft(draft_id)
    p = store.resolve(path)
    if not p.is_file():
        raise LedgerError(f"no file at {path}")
    if store.assets not in p.parents:
        raise LedgerError(f"Reddit images must live under {store.assets.relative_to(store.root)}/")
    if identity_qa not in ("pass", "fail"):
        raise LedgerError("identity_qa must be pass or fail")
    for img in d["images"]:
        if img["role"] == role:
            img.update(path=str(p.relative_to(store.root)), sfw=bool(sfw), identity_qa=identity_qa,
                       sha256=sha256_file(p), attached_by=_human(by), attached_on=today().isoformat())
            break
    else:
        raise LedgerError(f"draft has no image role {role!r}")
    d["gate"] = gate.evaluate(store, d)
    store.save_draft(d)
    return d


def _rules_fingerprint(store: Store, community: str) -> str:
    rules = store.communities()["communities"].get(community, {}).get("rules", {})
    return hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest()[:16]


def make_packet(store: Store, draft_id: str) -> dict:
    """The ONLY way a draft becomes postable. Writes a copy-paste packet for a human."""
    d = store.draft(draft_id)
    result = gate.evaluate(store, d)
    d["gate"] = result
    if result["decision"] != "PASS":
        store.save_draft(d)
        return {"ok": False, "decision": "BLOCK", "blocks": result["blocks"]}
    rules = store.communities()["communities"][d["community"]]["rules"]
    lines = [
        f"# Post packet: {d['id']}",
        f"Gate passed {result['checked_on']}. Rules fingerprint {_rules_fingerprint(store, d['community'])}.",
        "Post by hand from the disclosed creator account. Re-open the sub's rules page first; "
        "if anything differs from the matrix, stop and re-verify.",
        f"\n**Community:** {d['community']}  \n**Flair:** {d.get('flair') or '(none)'}",
        f"\n**Title**\n\n{d['title']}",
        f"\n**Images (in order)**\n" + "\n".join(f"- {i['path']}" for i in d["images"]),
        f"\n**Body**\n\n{d['body']}",
        "\n**Rule sources**\n" + "\n".join(f"- {k}: {v['source_url']} (verified {v['verified_at']} by "
                                           f"{v['verified_by']})" for k, v in rules.items()),
        "\nAfter posting: `python3 reddit_ops.py record-posted <draft_id> <permalink> <your name>`.",
        "Do not cross-post, do not add links in comments, do not ask for votes.",
    ]
    store.packets.mkdir(parents=True, exist_ok=True)
    out = store.packets / f"{d['id']}.md"
    out.write_text("\n".join(lines) + "\n")
    d.update(status="packet_ready", packet=str(out.relative_to(store.root)),
             rules_fingerprint=_rules_fingerprint(store, d["community"]))
    store.save_draft(d)
    return {"ok": True, "decision": "PASS", "packet": d["packet"]}


def record_posted(store: Store, draft_id: str, permalink: str, by: str) -> dict:
    d = store.draft(draft_id)
    if d.get("status") != "packet_ready":
        raise LedgerError(f"{draft_id} is {d.get('status')}; only a packet_ready draft can have been posted")
    if d.get("rules_fingerprint") != _rules_fingerprint(store, d["community"]):
        raise LedgerError("community rules changed after the packet was made; re-run the gate")
    if "reddit.com/r/" not in permalink:
        raise LedgerError("permalink must be a reddit.com post URL")
    act = store.activity()
    act["posts"].append({"draft": draft_id, "community": d["community"], "url": permalink,
                         "date": today().isoformat(), "by": _human(by), "outcomes": []})
    store.save_activity(act)
    d.update(status="posted", permalink=permalink)
    store.save_draft(d)
    return d


def record_outcome(store: Store, draft_id: str, state: str, upvote_ratio: float | None,
                   substantive_comments: int | None, rule_feedback: str, negative: bool, by: str) -> dict:
    if state not in ("live", "removed"):
        raise LedgerError("state must be live or removed")
    act = store.activity()
    post = next((p for p in act["posts"] if p["draft"] == draft_id), None)
    if post is None:
        raise LedgerError(f"{draft_id} was never recorded as posted")
    entry = {"date": today().isoformat(), "state": state, "upvote_ratio": upvote_ratio,
             "substantive_comments": substantive_comments, "rule_feedback": rule_feedback,
             "negative_response": negative, "by": _human(by)}
    post["outcomes"].append(entry)
    reasons = []
    if state == "removed":
        reasons.append("post removed")
    if upvote_ratio is not None and upvote_ratio < policy.NEGATIVE_UPVOTE_RATIO:
        reasons.append(f"upvote ratio {upvote_ratio}")
    if negative:
        reasons.append("clear negative response")
    if rule_feedback.strip():
        reasons.append(f"mod/rule feedback: {rule_feedback.strip()}")
    if reasons and not act.get("halt"):
        act["halt"] = f"{today()}: {draft_id}: " + "; ".join(reasons)
    store.save_activity(act)
    if state == "removed":
        d = store.draft(draft_id)
        d["status"] = "removed"
        store.save_draft(d)
    return {"outcome": entry, "halt": act.get("halt")}
