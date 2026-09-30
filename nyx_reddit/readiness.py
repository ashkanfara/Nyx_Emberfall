"""Readiness checklist and re-check dates, computed from the recorded evidence.
Nothing here is estimated beyond simple date arithmetic on recorded facts."""

from __future__ import annotations

import datetime as dt

from . import policy
from .rules import route_closures, rule_problems
from .store import Store, parse_date, today


def compute(store: Store, on=None) -> dict:
    on = on or today()
    acct = store.account()
    act = store.activity()
    comms = store.communities()["communities"]
    items, dates = [], {}

    # Account
    if not acct:
        items.append(("Account evidence recorded", False, "no snapshot"))
    else:
        rec = parse_date(acct["recorded_at"])
        age_now = acct["age_days"] + (on - rec).days
        created = rec - dt.timedelta(days=acct["age_days"])
        age_ok_on = created + dt.timedelta(days=policy.HOUSE_MIN_ACCOUNT_AGE_DAYS)
        dates["account_age_gate"] = age_ok_on.isoformat()
        items.append((f"Account age ≥ {policy.HOUSE_MIN_ACCOUNT_AGE_DAYS} days", age_now >= policy.HOUSE_MIN_ACCOUNT_AGE_DAYS,
                      f"{age_now}d now (recorded {acct['age_days']}d on {rec}); reaches {policy.HOUSE_MIN_ACCOUNT_AGE_DAYS}d on {age_ok_on}"))
        items.append((f"Karma ≥ {policy.HOUSE_MIN_KARMA}", acct["karma"] >= policy.HOUSE_MIN_KARMA,
                      f"{acct['karma']} as of {rec}; no date can be computed, it depends on real participation"))
        items.append(("Profile discloses creator/AI-fiction role", acct.get("discloses_creator_role") is True,
                      "not checked" if acct.get("discloses_creator_role") is None else str(acct["discloses_creator_role"])))
        stale_on = rec + dt.timedelta(days=policy.ACCOUNT_SNAPSHOT_MAX_AGE_DAYS)
        dates["account_snapshot_expires"] = stale_on.isoformat()

    # Warm-up
    comments = [c for c in act.get("comments", []) if not c.get("removed")]
    cdates = sorted(d for d in (parse_date(c.get("date")) for c in comments) if d)
    span = (cdates[-1] - cdates[0]).days if cdates else 0
    warm_ok = len(comments) >= policy.WARMUP_MIN_COMMENTS and span >= policy.WARMUP_MIN_SPAN_DAYS
    earliest = (cdates[0] if cdates else None)
    detail = (f"{len(comments)}/{policy.WARMUP_MIN_COMMENTS} useful comments over {span}d "
              f"(needs {policy.WARMUP_MIN_SPAN_DAYS}d)")
    if earliest:
        dates["warmup_span_complete"] = (earliest + dt.timedelta(days=policy.WARMUP_MIN_SPAN_DAYS)).isoformat()
    else:
        detail += "; not started, so the earliest completion is 14 days after the first logged comment"
    items.append(("Warm-up comments logged", warm_ok, detail))
    items.append(("Test not halted", not act.get("halt"), act.get("halt") or "no halt"))

    # Communities with any recorded evidence, plus every non-excluded candidate
    subs = []
    for name, c in comms.items():
        if c.get("status") == "excluded":
            continue
        rules = c.get("rules", {})
        verified = [parse_date(r.get("verified_at")) for r in rules.values() if r.get("verified_at")]
        if verified:
            expiry = min(verified) + dt.timedelta(days=policy.RULE_MAX_AGE_DAYS)
            dates[f"rules_expire:{name}"] = expiry.isoformat()
        subs.append({"community": name, "status": c.get("status"), "provisional": c.get("provisional"),
                     "closed_routes": route_closures(c),
                     "open_rule_items": rule_problems(c, on) if rules else ["no rules recorded"],
                     "evidence_recorded": sorted(rules)})

    future = sorted(d for d in dates.values() if d > on.isoformat())
    return {"checked_on": on.isoformat(), "ready_to_post": all(ok for _, ok, _ in items) and any(
                (d.get("gate") or {}).get("decision") == "PASS" for d in store.drafts()),
            "items": [{"item": i, "ok": ok, "detail": d} for i, ok, d in items],
            "communities": subs, "dates": dates,
            "next_recheck": future[0] if future else on.isoformat()}


def render(store: Store, on=None) -> str:
    r = compute(store, on)
    out = [f"# Nyx Reddit readiness", "",
           f"Checked {r['checked_on']}. **Next re-check: {r['next_recheck']}.** "
           "Regenerate with `python3 reddit_ops.py readiness`.", "",
           "Posting is blocked until every box is ticked AND a draft's gate passes. Nothing here posts.", "",
           "## Account and warm-up", ""]
    out += [f"- [{'x' if i['ok'] else ' '}] {i['item']}: {i['detail']}" for i in r["items"]]
    out += ["", "## Communities", ""]
    for s in r["communities"]:
        if s["evidence_recorded"] or s["provisional"] in ("approved_native", "approved_promo_thread"):
            out.append(f"### {s['community']} ({s['status']}, provisional {s['provisional']})")
            if s["closed_routes"]:
                out += [f"- Closed: {k}: {v}" for k, v in s["closed_routes"].items()]
            out += [f"- [ ] {p}" for p in s["open_rule_items"]]
            out.append("")
    out += ["## Key dates", ""] + [f"- {k}: {v}" for k, v in sorted(r["dates"].items(), key=lambda kv: kv[1])]
    return "\n".join(out) + "\n"
