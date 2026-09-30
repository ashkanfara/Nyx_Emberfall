"""Recording browser-verified subreddit rules and classifying communities.

Two kinds of evidence:
  verbatim          the rule text copied word for word (or 'NOT PUBLISHED')
  operator_summary  a human's dated summary of what the live page showed

A summary may CLOSE or RESTRICT a route (it is recorded and the gate obeys
it), but it can never APPROVE one: approval needs verbatim evidence for every
field, with no unknown (null) values.
"""

import re

from . import policy
from .store import parse_date, today

REDDIT_URL = re.compile(r"^https://(www\.|old\.|new\.|sh\.)?reddit\.com/r/([A-Za-z0-9_]+)(/|$)", re.I)
NON_HUMAN_VERIFIERS = {"", "claude", "agent", "bot", "ai", "automation", "script"}
EVIDENCE_KINDS = ("verbatim", "operator_summary")
# Keys a rule record may carry beyond the required ones.
OPTIONAL_KEYS = {"ai_content": ["project_mod_approval_required", "mod_approval_granted"]}


class RuleError(ValueError):
    pass


def _sub_name(community):
    return community[2:] if community.lower().startswith("r/") else community


def record_rule(store, community, field, values, source_url, quote, verified_by,
                method="browser", verified_at=None, evidence="verbatim", note=""):
    """Store one browser-verified rule field. Raises RuleError on anything unverifiable."""
    if field not in policy.REQUIRED_RULE_FIELDS:
        raise RuleError(f"unknown rule field {field!r}; expected one of {sorted(policy.REQUIRED_RULE_FIELDS)}")
    if method != "browser":
        raise RuleError("rules must be verified in a normal Reddit browser session (method=browser)")
    if evidence not in EVIDENCE_KINDS:
        raise RuleError(f"evidence must be one of {EVIDENCE_KINDS}")
    if verified_by.strip().lower() in NON_HUMAN_VERIFIERS:
        raise RuleError("verified_by must name the human who checked the page")
    m = REDDIT_URL.match(source_url or "")
    if not m or m.group(2).lower() != _sub_name(community).lower():
        raise RuleError(f"source_url must be a reddit.com page of {community} (rules, sidebar, wiki or pinned mod post)")
    if not quote or len(quote.strip()) < 3:
        raise RuleError("quote the rule text verbatim, or write 'NOT PUBLISHED' if the sub states nothing")
    missing = [k for k in policy.REQUIRED_RULE_FIELDS[field] if k not in values]
    if missing:
        raise RuleError(f"{field} needs values for {missing}")
    unknown = sorted(set(values) - set(policy.REQUIRED_RULE_FIELDS[field]) - set(OPTIONAL_KEYS.get(field, [])))
    if unknown:
        raise RuleError(f"{field} does not take {unknown}")
    _validate_values(field, values, allow_unknown=(evidence == "operator_summary"))

    doc = store.communities()
    comm = doc["communities"].get(community)
    if comm is None:
        raise RuleError(f"{community} is not in the matrix; add it first")
    comm.setdefault("rules", {})[field] = {
        **values,
        "source_url": source_url,
        "quote": quote.strip(),
        "evidence": evidence,
        "note": note.strip(),
        "verified_by": verified_by.strip(),
        "method": method,
        "verified_at": (verified_at or today().isoformat()),
    }
    # Any new rule evidence invalidates a previous approval until re-classified.
    if comm.get("status", "").startswith("approved"):
        comm["status"] = "unverified"
        comm["status_note"] = f"reset on {today().isoformat()}: rule {field} re-recorded; re-classify"
    store.save_communities(doc)
    return comm["rules"][field]


def _validate_values(field, v, allow_unknown=False):
    """null means 'not published' for the int fields; for booleans it means
    'not observed' and is only accepted in an operator summary."""
    def is_bool(k):
        if v[k] is None and allow_unknown:
            return
        if not isinstance(v[k], bool):
            raise RuleError(f"{field}.{k} must be true/false" + ("" if allow_unknown else
                            " (null = not observed is only allowed with evidence=operator_summary)"))

    def is_int_or_null(k):
        if v[k] is not None and (not isinstance(v[k], int) or v[k] < 0):
            raise RuleError(f"{field}.{k} must be a non-negative integer or null (not published)")

    if field == "ai_content":
        is_bool("allowed"); is_bool("disclosure_required")
        for k in OPTIONAL_KEYS["ai_content"]:
            if k in v:
                is_bool(k)
        if v["disclosure_required"] and not v["disclosure_format"]:
            raise RuleError("ai_content.disclosure_format is required when disclosure is required")
    elif field == "self_promotion":
        if v["mode"] not in policy.SELF_PROMO_MODES and not (v["mode"] is None and allow_unknown):
            raise RuleError(f"self_promotion.mode must be one of {sorted(policy.SELF_PROMO_MODES)}")
    elif field == "nsfw":
        is_bool("allowed")
    elif field == "links":
        is_bool("external_allowed"); is_bool("fanvue_allowed")
    elif field == "account_minimums":
        is_int_or_null("min_age_days"); is_int_or_null("min_karma")
    elif field == "flair":
        is_bool("required")
        if not isinstance(v["options"], list):
            raise RuleError("flair.options must be a list (empty if unknown)")
    elif field == "frequency":
        is_int_or_null("max_posts"); is_int_or_null("per_days")


def rule_problems(comm, on=None):
    """Return a list of reasons this community's rule record cannot support an approval."""
    on = on or today()
    problems = []
    rules = comm.get("rules", {})
    for field in policy.REQUIRED_RULE_FIELDS:
        r = rules.get(field)
        if not r:
            problems.append(f"rule '{field}' not verified")
            continue
        if r.get("method") != "browser":
            problems.append(f"rule '{field}' not browser-verified")
        if r.get("evidence", "verbatim") != "verbatim":
            problems.append(f"rule '{field}' is an operator summary; capture the verbatim text")
        nulls = [k for k in policy.REQUIRED_RULE_FIELDS[field]
                 if r.get(k) is None and field not in ("account_minimums", "frequency")]
        if nulls:
            problems.append(f"rule '{field}' has unobserved values {nulls}")
        verified = parse_date(r.get("verified_at"))
        if verified is None:
            problems.append(f"rule '{field}' has no verification date")
        elif (on - verified).days > policy.RULE_MAX_AGE_DAYS:
            problems.append(f"rule '{field}' verified {verified} is older than {policy.RULE_MAX_AGE_DAYS} days")
    return problems


def route_closures(comm):
    """Restrictions already evidenced (summary or verbatim) that close a route
    regardless of how complete the rest of the record is."""
    rules = comm.get("rules", {})
    ai = rules.get("ai_content", {})
    closed = {}
    if ai.get("allowed") is False:
        closed["native_post"] = closed["promo_thread_comment"] = "AI content not allowed"
    if ai.get("project_mod_approval_required") and not ai.get("mod_approval_granted"):
        closed["native_post"] = ("AI projects need mod approval, not granted "
                                 "(contacting mods is out of scope)")
    mode = rules.get("self_promotion", {}).get("mode")
    if mode == "promo_thread_only":
        closed.setdefault("native_post", "self-promotion only in the designated thread")
    if mode == "none":
        closed["promo_thread_comment"] = "no self-promotion anywhere"
    return closed


def classify(store, community, status, by, note=""):
    if status not in policy.STATUSES:
        raise RuleError(f"status must be one of {sorted(policy.STATUSES)}")
    doc = store.communities()
    comm = doc["communities"].get(community)
    if comm is None:
        raise RuleError(f"{community} is not in the matrix")
    if comm.get("status") == "excluded" and status != "excluded" and comm.get("exclusion_reason"):
        raise RuleError(f"{community} is on the no-post list ({comm['exclusion_reason']}); "
                        "remove it from the no-post list deliberately in communities.json first")
    if status.startswith("approved"):
        problems = rule_problems(comm)
        if problems:
            raise RuleError("cannot approve: " + "; ".join(problems))
        r = comm["rules"]
        if not r["ai_content"]["allowed"]:
            raise RuleError("cannot approve: AI content is not allowed there -> classify as excluded")
        mode = r["self_promotion"]["mode"]
        if status == "approved_promo_thread" and mode != "promo_thread_only":
            raise RuleError("approved_promo_thread requires self_promotion.mode == promo_thread_only")
        closed = route_closures(comm)
        kind = "native_post" if status == "approved_native" else "promo_thread_comment"
        if kind in closed:
            raise RuleError(f"cannot approve {status}: {closed[kind]}")
    if by.strip().lower() in NON_HUMAN_VERIFIERS:
        raise RuleError("classification must be made by a named human")
    comm["status"] = status
    comm["classified_by"] = by
    comm["classified_at"] = today().isoformat()
    comm["status_note"] = note
    store.save_communities(doc)
    return comm


def record_snapshot(store, community, source_url, summary, rules_shown, verified_by, verified_at=None):
    """What a human saw on the live rules page, as one dated record. Kept even
    when the individual fields are only summaries, so the matrix shows exactly
    what was observed and when."""
    if verified_by.strip().lower() in NON_HUMAN_VERIFIERS:
        raise RuleError("verified_by must name the human who checked the page")
    m = REDDIT_URL.match(source_url or "")
    if not m or m.group(2).lower() != _sub_name(community).lower():
        raise RuleError(f"source_url must be a reddit.com page of {community}")
    doc = store.communities()
    comm = doc["communities"].get(community)
    if comm is None:
        raise RuleError(f"{community} is not in the matrix")
    snap = {"verified_at": verified_at or today().isoformat(), "verified_by": verified_by.strip(),
            "method": "browser", "evidence": "operator_summary", "source_url": source_url,
            "summary": summary.strip(), "rules_shown": list(rules_shown)}
    comm.setdefault("snapshots", []).append(snap)
    store.save_communities(doc)
    return snap
