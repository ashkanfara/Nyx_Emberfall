"""Render the community matrix, no-post list and draft queue as dated Markdown."""

from __future__ import annotations

from . import policy
from .rules import route_closures, rule_problems
from .store import Store, today

FIELD_SUMMARY = {
    "ai_content": lambda r: f"allowed={r['allowed']}, disclosure={r['disclosure_required']}"
                            + (f" ({r['disclosure_format']})" if r.get("disclosure_format") else ""),
    "self_promotion": lambda r: r["mode"],
    "nsfw": lambda r: f"allowed={r['allowed']}",
    "links": lambda r: f"external={r['external_allowed']}, fanvue={r['fanvue_allowed']}",
    "account_minimums": lambda r: f"age={r['min_age_days']}, karma={r['min_karma']}",
    "flair": lambda r: f"required={r['required']}" + (f" {r['options']}" if r.get("options") else ""),
    "frequency": lambda r: f"{r['max_posts']}/{r['per_days']}d" if r.get("max_posts") is not None else "none published",
}


def _cell(rule):
    if not rule:
        return "—"
    return rule["verified_at"] + (" (summary)" if rule.get("evidence") == "operator_summary" else "")


def render(store: Store) -> dict[str, str]:
    on = today()
    comms = store.communities()["communities"]
    fields = list(policy.REQUIRED_RULE_FIELDS)
    candidates = {k: v for k, v in comms.items() if v.get("status") != "excluded"}
    excluded = {k: v for k, v in comms.items() if v.get("status") == "excluded"}

    m = [f"# Nyx Reddit community matrix", "",
         f"Generated {on.isoformat()} by `python3 reddit_ops.py matrix`. Do not hand-edit; record rules with "
         f"`reddit_ops.py record-rule`. A rule older than {policy.RULE_MAX_AGE_DAYS} days counts as unverified.", "",
         "| Rank | Community | Route | Route basis | Status | Gate-ready | " + " | ".join(fields) + " |",
         "|---|---|---|---|---|---|" + "---|" * len(fields)]
    for name, c in sorted(candidates.items(), key=lambda kv: (kv[1].get("rank", 99), kv[0])):
        problems = rule_problems(c, on)
        ready = "yes" if not problems and c.get("status", "").startswith("approved") else "no"
        m.append(f"| {c.get('rank', '')} | {name} | {c.get('route', 'unknown')} | {c.get('route_certainty', '')} | "
                 f"**{c.get('status')}** | {ready} | "
                 + " | ".join(_cell(c.get("rules", {}).get(f)) for f in fields) + " |")
    top = store.communities().get("first_three_when_eligible")
    if top:
        m += ["", f"**First three once the account is eligible** (decided {top['decided_on']}): "
              + " → ".join(top["order"]) + f". {top['certainty']}"]
        m += [f"- {n}: {why}" for n, why in top["why"].items()] + [f"- Not in the top three: {top['not_in_top_three']}"]
    m += ["", "Route basis: observed_summary = the founder read the live page; provisional = training knowledge "
          "only, the rules page could not be read from the build environment.",
          "", "Cells show the date each rule was verified in a browser. \"(summary)\" = an operator's summary, "
          "which can close a route but never approve one; — = not verified.", ""]
    for name, c in sorted(candidates.items(), key=lambda kv: (kv[1].get("rank", 99), kv[0])):
        m += [f"## {name}", "", f"- Route: {c.get('route', 'unknown')} ({c.get('route_certainty', '')}): "
              f"{c.get('route_basis', '')}", f"- Audience fit: {c.get('audience_fit', '')}",
              f"- Value angle: {c.get('value_angle', '')}",
              f"- Provisional basis: {c.get('provisional_basis', '')}"]
        if c.get("status_note"):
            m.append(f"- Status note: {c['status_note']}")
        for k, v in route_closures(c).items():
            m.append(f"- **Closed route** `{k}`: {v}")
        for snap in c.get("snapshots", []):
            m.append(f"- Snapshot {snap['verified_at']} by {snap['verified_by']} ({snap['evidence']}, "
                     f"[source]({snap['source_url']})): {snap['summary']} Rules shown: {', '.join(snap['rules_shown'])}.")
        for f in fields:
            r = c.get("rules", {}).get(f)
            if r:
                m.append(f"- **{f}**: {FIELD_SUMMARY[f](r)}. \"{r['quote']}\" "
                         f"[source]({r['source_url']}), verified {r['verified_at']} by {r['verified_by']}, "
                         f"evidence: {r.get('evidence', 'verbatim')}" + (f". Note: {r['note']}" if r.get("note") else ""))
            else:
                m.append(f"- **{f}**: NOT VERIFIED")
        m.append("")

    n = ["# Nyx Reddit no-post list", "", f"Generated {on.isoformat()}. Never drafted for, never posted to.", "",
         "| Community | Reason | Source |", "|---|---|---|"]
    for name, c in sorted(excluded.items()):
        n.append(f"| {name} | {c.get('exclusion_reason', '')} | {c.get('exclusion_source', '')} |")

    q = ["# Nyx Reddit draft queue", "", f"Generated {on.isoformat()}. Nothing here is posted. "
         "A draft becomes postable only via `reddit_ops.py packet`, which re-runs the gate.", ""]
    for d in store.drafts():
        g = d.get("gate") or {}
        q += [f"## {d['id']}", "",
              f"- Community: {d['community']} · kind: {d.get('kind')} · status: **{d.get('status')}** · "
              f"gate: **{g.get('decision', 'not run')}** ({g.get('checked_on', '')})",
              f"- Angle: {d.get('angle', '')}", "", f"**Title:** {d['title']}", "", "**Body:**", "",
              "> " + d["body"].replace("\n", "\n> "), "",
              "**Images:** " + "; ".join(f"{i['role']} (slide {i.get('source_slide')}, "
                                         f"{'attached' if i.get('path') else 'brief only'})" for i in d["images"]),
              "", "**Facts and their sources:**"]
        q += [f"- {f['claim']} ← `{f['source']}`" for f in d.get("facts", [])]
        if g.get("blocks"):
            q += ["", "**Gate blocks:**"] + [f"- {b}" for b in g["blocks"]]
        q.append("")
    return {"docs/reddit/community-matrix.md": "\n".join(m) + "\n",
            "docs/reddit/no-post-list.md": "\n".join(n) + "\n",
            "docs/reddit/draft-queue.md": "\n".join(q) + "\n"}


def write(store: Store) -> list[str]:
    out = []
    for rel, text in render(store).items():
        p = store.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        out.append(rel)
    return out
