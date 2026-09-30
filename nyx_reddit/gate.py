"""The publication gate. A draft may only become a copy-paste packet when every
check below passes. There is no code path in this package that posts to Reddit;
the gate decides whether a HUMAN may post the packet.

Every check reports; nothing short-circuits, so one run shows the full list of
what is still missing.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import policy
from .rules import route_closures, rule_problems
from .store import parse_date, sha256_file, today

URL = re.compile(r"(https?://\S+|www\.\S+|\b[a-z0-9-]+\.(com|co|net|io|me|link|bio)\b\S*)", re.I)
PLACEHOLDER = re.compile(r"\{\{.*?\}\}")
DISCLOSURE_WORDS = re.compile(r"\b(ai|ai-generated|fictional)\b|not a real person", re.I)


def _text(draft: dict) -> str:
    return f"{draft.get('title', '')}\n{draft.get('body', '')}".lower()


def _words(draft: dict) -> set[str]:
    return set(re.findall(r"[a-z']{4,}", _text(draft)))


def evaluate(store, draft: dict, on=None) -> dict:
    on = on or today()
    blocks: list[str] = []
    warnings: list[str] = []
    community = draft.get("community", "")
    comm = store.communities()["communities"].get(community)

    # 1. community classification and rule freshness
    rules = {}
    if comm is None:
        blocks.append(f"{community} is not in the matrix")
    else:
        status = comm.get("status")
        rules = comm.get("rules", {})
        if status == "excluded":
            blocks.append(f"{community} is on the no-post list: {comm.get('exclusion_reason', '')}")
        elif status == "observe_only":
            blocks.append(f"{community} is observe/comment only")
        elif status == "unverified":
            blocks.append(f"{community} rules are not verified (provisional: {comm.get('provisional')})")
        elif status not in ("approved_native", "approved_promo_thread"):
            blocks.append(f"{community} has unknown status {status!r}")
        blocks += [f"{community}: {p}" for p in rule_problems(comm, on)]
        if status == "approved_native" and draft.get("kind") != "native_post":
            blocks.append("community is approved for native posts only")
        if status == "approved_promo_thread" and draft.get("kind") != "promo_thread_comment":
            blocks.append("community is approved only for its scheduled promo thread")
        if draft.get("kind") == "promo_thread_comment" and not draft.get("promo_thread_url"):
            blocks.append("promo-thread unit needs the thread URL")
        closed = route_closures(comm)
        if draft.get("kind") in closed:
            blocks.append(f"{community}: {draft['kind']} route closed: {closed[draft['kind']]}")

    # 2. content completeness
    title = (draft.get("title") or "").strip()
    if not title and draft.get("kind") != "promo_thread_comment":   # thread comments have no title
        blocks.append("title is empty")
    if len(title) > policy.TITLE_MAX_CHARS:
        blocks.append(f"title longer than {policy.TITLE_MAX_CHARS} chars")
    if not (draft.get("body") or "").strip():
        blocks.append("body is empty")
    unresolved = PLACEHOLDER.findall(f"{title}\n{draft.get('body', '')}")
    if unresolved:
        blocks.append(f"unresolved placeholders: {sorted(set(unresolved))}")

    # 3. safety: SFW, not a real person, no funnel
    text = _text(draft)
    if draft.get("nsfw") is not False:
        blocks.append("draft must be explicitly marked nsfw=false (SFW-only workstream)")
    for claim in policy.REAL_PERSON_CLAIMS:
        if claim in text:
            blocks.append(f"presents Nyx as a real person: {claim!r}")
    minors = [t for t in policy.MINOR_TERMS if re.search(rf"\b{re.escape(t)}\b", text)]
    if minors:
        blocks.append(f"minor-coded language (adult-only canon, subs ban it): {minors}")
    promo_hits = [t for t in policy.PROMO_TERMS if t in text]
    if promo_hits:
        blocks.append(f"funnel/promo language: {promo_hits}")
    links = list(draft.get("links") or []) + [m[0] for m in URL.findall(draft.get("body", "") + " " + title)]
    fanvue = [l for l in links if any(d in l.lower() for d in policy.FANVUE_DOMAINS)]
    if fanvue and not (rules.get("links", {}).get("fanvue_allowed") is True):
        blocks.append("Fanvue link without a verified rule that explicitly allows it")
    if fanvue:
        blocks.append("Fanvue links are disabled for the 30-day test by owner instruction")
    if links and not rules.get("links", {}).get("external_allowed"):
        blocks.append(f"external links present but not verified as allowed: {links}")

    # 4. disclosure: house policy requires it always; sub format wins if stated
    disclosure = (draft.get("disclosure") or "").strip()
    if not disclosure:
        blocks.append("AI-fiction disclosure missing")
    else:
        if disclosure.lower() not in text:
            blocks.append("disclosure is not actually in the title/body")
        if not DISCLOSURE_WORDS.search(disclosure):
            blocks.append("disclosure does not say AI / fictional")
    ai = rules.get("ai_content", {})
    fmt = (ai.get("disclosure_format") or "").strip()
    if ai.get("disclosure_required") and fmt and fmt.lower() not in text \
            and fmt.lower() != (draft.get("flair") or "").lower():
        blocks.append(f"sub requires disclosure format {fmt!r} (in title/body or as flair)")

    # 5. flair
    flair = rules.get("flair", {})
    if flair.get("required"):
        if not draft.get("flair"):
            blocks.append("sub requires flair; none chosen")
        elif flair.get("options") and draft["flair"] not in flair["options"]:
            blocks.append(f"flair {draft['flair']!r} not in verified options {flair['options']}")

    # 6. images: original, SFW, on disk, not a public-pipeline asset
    images = draft.get("images") or []
    if not images:
        blocks.append("no image attached (needs one original SFW image or a short set)")
    if len(images) > policy.MAX_IMAGES:
        blocks.append(f"more than {policy.MAX_IMAGES} images")
    public = store.public_asset_hashes() if images else set()
    for img in images:
        path = img.get("path")
        if not path:
            blocks.append(f"image '{img.get('role', '?')}' not generated yet (brief only)")
            continue
        p = store.resolve(path)
        if not Path(p).is_file():
            blocks.append(f"image file missing: {path}")
            continue
        if img.get("sfw") is not True:
            blocks.append(f"image {path} not QA-marked sfw=true")
        if img.get("identity_qa") != "pass":
            blocks.append(f"image {path} has no identity QA pass against reference_primary")
        if img.get("origin") != "reddit_native":
            blocks.append(f"image {path} is not a Reddit-native asset")
        if sha256_file(p) in public:
            blocks.append(f"image {path} is a public-pipeline asset (blind cross-post)")

    # 7. account reputation (operator snapshot, never inferred)
    acct = store.account()
    mins = rules.get("account_minimums", {})
    snap = parse_date(acct.get("recorded_at"))
    if not acct:
        blocks.append("no account snapshot recorded")
    else:
        if snap is None or (on - snap).days > policy.ACCOUNT_SNAPSHOT_MAX_AGE_DAYS:
            blocks.append(f"account snapshot older than {policy.ACCOUNT_SNAPSHOT_MAX_AGE_DAYS} days")
        need_age = max(policy.HOUSE_MIN_ACCOUNT_AGE_DAYS, mins.get("min_age_days") or 0)
        need_karma = max(policy.HOUSE_MIN_KARMA, mins.get("min_karma") or 0)
        if (acct.get("age_days") or 0) < need_age:
            blocks.append(f"account age {acct.get('age_days')}d < required {need_age}d")
        if (acct.get("karma") or 0) < need_karma:
            blocks.append(f"account karma {acct.get('karma')} < required {need_karma}")
        if acct.get("discloses_creator_role") is None:
            blocks.append("account profile disclosure not checked yet")
        elif not acct.get("discloses_creator_role"):
            blocks.append("account profile does not disclose the creator/AI-fiction role")
        if mins and mins.get("min_age_days") is None and mins.get("min_karma") is None:
            warnings.append("sub publishes no minimums; hidden AutoModerator thresholds may still apply")

    # 8. warm-up, halt and 30-day test budget
    act = store.activity()
    if act.get("halt"):
        blocks.append(f"test halted: {act['halt']}")
    comments = [c for c in act.get("comments", []) if not c.get("removed")]
    if any(c.get("removed") for c in act.get("comments", [])):
        blocks.append("a warm-up comment was removed; review before posting")
    dates = sorted(d for d in (parse_date(c.get("date")) for c in comments) if d)
    if len(comments) < policy.WARMUP_MIN_COMMENTS:
        blocks.append(f"warm-up: {len(comments)}/{policy.WARMUP_MIN_COMMENTS} useful comments logged")
    elif (dates[-1] - dates[0]).days < policy.WARMUP_MIN_SPAN_DAYS:
        blocks.append(f"warm-up spans {(dates[-1] - dates[0]).days}d < {policy.WARMUP_MIN_SPAN_DAYS}d")
    posts = act.get("posts", [])
    recent = [p for p in posts if (d := parse_date(p.get("date"))) and (on - d).days < policy.TEST_WINDOW_DAYS]
    if len(posts) >= policy.TEST_MAX_POSTS:
        blocks.append(f"test budget spent: {len(posts)}/{policy.TEST_MAX_POSTS} posts")
    if any(p.get("community") == community for p in posts):
        blocks.append(f"already posted once to {community} in this test")
    last = max((parse_date(p.get("date")) for p in recent), default=None)
    if last and (on - last).days < policy.MIN_DAYS_BETWEEN_POSTS:
        blocks.append(f"last post {last}; wait {policy.MIN_DAYS_BETWEEN_POSTS} days between posts")
    freq = rules.get("frequency", {})
    if freq.get("max_posts") is not None and freq.get("per_days"):
        mine = [p for p in posts if p.get("community") == community
                and (d := parse_date(p.get("date"))) and (on - d).days < freq["per_days"]]
        if len(mine) >= freq["max_posts"]:
            blocks.append(f"sub frequency limit {freq['max_posts']}/{freq['per_days']}d reached")

    # 9. differentiation from every other draft in the queue (text and images)
    mine = _words(draft)
    my_images = {i.get("sha256") for i in images if i.get("sha256")}
    for other in store.drafts():
        if other.get("id") == draft.get("id"):
            continue
        shared = my_images & {i.get("sha256") for i in other.get("images") or [] if i.get("sha256")}
        if shared:
            blocks.append(f"shares {len(shared)} image(s) with draft {other.get('id')} (cross-post)")
        if not mine:
            continue
        theirs = _words(other)
        sim = len(mine & theirs) / max(1, len(mine | theirs))
        if sim > policy.MAX_DRAFT_SIMILARITY:
            blocks.append(f"too similar to draft {other.get('id')} ({sim:.2f})")

    return {"draft": draft.get("id"), "community": community, "checked_on": on.isoformat(),
            "decision": "PASS" if not blocks else "BLOCK", "blocks": blocks, "warnings": warnings}
