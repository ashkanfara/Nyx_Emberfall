#!/usr/bin/env python3
"""PS-05 Venture Manager -- the autonomous state-aware driver.

    python3 manager.py tick              # one bounded tick, then exit
    python3 manager.py --watch           # foreground loop (forked from
    python3 manager.py --watch --interval 900   # research-orchestrator's
                                                  # poller.py --watch shape)

Each tick: read state -> if a founder approval is pending, do nothing -> else
ask MANAGER_DECIDE for the single highest-value next action -> dispatch to
one stage -> validate -> persist -> log -> loop (bounded) or stop.

No daemon, no scheduler, no DB -- flat JSON files under state/, exactly the
research-orchestrator philosophy this was forked from. Fully independent
process from research-orchestrator; never imports anything from it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

import audit
import carousel_handoff
import dashboard_status
import stages
import state as st
import wan_provider
from claude_client import LLMError
from stages import StageValidationError

MAX_LLM_CALLS_PER_TICK = 3
# 2026-09-13 fix: bounded regeneration -- without this, nothing in code
# stops the same content_item from being regenerated indefinitely (one
# real item hit 5 generations + 5 QA failures on the same repeated
# gesture). After this many real generation attempts with no PASS, the
# item needs a rebrief/different concept, not another attempt.
MAX_REGENERATION_ATTEMPTS = 3
DEFAULT_WATCH_INTERVAL_S = 900  # 15 min -- this system has far less to poll than a Gmail inbox

# 2026-09-15 standing founder authorization (commercial-autonomy correction
# to the earlier one-off "authorize a content-brief revision" ask): once
# usable content inventory is at or below this many items, run_content_brief
# is allowed to append a new bounded batch WITHOUT a fresh founder approval
# each time -- deterministic gates here in code, never left to the model's
# own judgment alone (same reasoning as MAX_REGENERATION_ATTEMPTS above).
CONTENT_INVENTORY_LOW_THRESHOLD = 2
# Never revise more than once per this interval, even if repeatedly eligible
# -- prevents thrashing a brief-revision call every single tick.
MIN_CONTENT_BRIEF_REVISION_INTERVAL_S = 6 * 3600
# Terminal states that make an item's inventory "spent" -- it no longer
# occupies a distribution slot, whether it succeeded (published) or was
# intentionally dropped (deferred).
_TERMINAL_CONTENT_LIFECYCLE = {"PUBLISHED", "DEFERRED"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _seconds_since(iso_ts: str | None) -> float:
    if not iso_ts:
        return float("inf")
    try:
        then = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds()


def _usable_content_inventory(v: dict) -> list[dict]:
    """Content items still occupying a real distribution slot: not a
    benchmark-only asset (never meant to publish), not targeting a channel
    the founder has explicitly deferred out of v1 scope (e.g. reddit --
    inventory for a channel nothing is allowed to act on isn't real slack),
    not yet published, and not deliberately deferred. Used to decide
    whether the content pipeline is genuinely running low, independent of
    any platform-specific publishing block (that's a separate concern,
    tracked via queued_actions/pending_approval, not something a brief
    revision should try to solve)."""
    deferred_channels = {d.get("channel") for d in
                         v.get("distribution_channels", {}).get("deferred", [])}
    items = v.get("content_plan", {}).get("content_items", [])
    out = []
    for it in items:
        platform = it.get("target_platform")
        if platform == "benchmark_internal" or platform in deferred_channels:
            continue
        if it.get("lifecycle_status") in _TERMINAL_CONTENT_LIFECYCLE:
            continue
        if it.get("public_status") == "PUBLICLY_LIVE" or it.get("published"):
            continue
        out.append(it)
    return out


def _recent_activity_text(limit: int = 8) -> str:
    entries = audit.load(limit=limit)
    if not entries:
        return "(none yet)"
    return "\n".join(f"- [{e['ts']}] {e['actor']}/{e['action']}: {e['detail']}" for e in entries)


def _mark_done(v: dict, milestone: str) -> None:
    if milestone not in v.setdefault("completed_milestones", []):
        v["completed_milestones"].append(milestone)


def _recent_published_items(v: dict, *, limit: int = 5, exclude_index: int | None = None) -> list[dict]:
    """Feed-diversity comparison set for run_content_qa -- a small, real
    summary of what's actually live, not the full content plan. 'published'
    covers both the current lifecycle_status tracking and the older
    published=True/founder_completed_publish=True items that predate it."""
    items = v.get("content_plan", {}).get("content_items", [])
    published = []
    for i, it in enumerate(items):
        if i == exclude_index:
            continue
        if it.get("lifecycle_status") == "PUBLISHED" or it.get("published") or it.get("founder_completed_publish"):
            published.append(it)
    published.sort(key=lambda it: (it.get("lifecycle_updated_at") or it.get("published_at")
                                   or it.get("fanvue_published_at") or ""), reverse=True)
    return [{"type": it.get("type"), "target_platform": it.get("target_platform"),
            "generation_prompt": (it.get("generation_prompt") or "")[:400],
            "caption": (it.get("caption") or "")[:200]} for it in published[:limit]]


# Non-idempotent stages -- once done, re-running them would silently redo
# founder-facing planning work rather than refine it. Enforced here in code
# (not just via the MANAGER_DECIDE prompt instruction) so a model choosing to
# ignore that instruction still can't cause a repeat -- see the correction
# that added this after phase state was found stale relative to actual
# completed work. run_content_brief is deliberately NOT here (2026-09-15):
# it used to be, because content_brief_generator's contract replaces
# v["content_plan"] wholesale -- but it now has its own dedicated,
# non-destructive revision path below (see the run_content_brief branch in
# _dispatch), gated by CONTENT_INVENTORY_LOW_THRESHOLD/
# MIN_CONTENT_BRIEF_REVISION_INTERVAL_S instead of a one-shot milestone.
_ONCE_PER_ROUND_MILESTONES = {
    "run_persona_designer": "persona_designed",
}


def _dispatch(v: dict, action: str, params: dict, *, reasoning: str = "",
              next_evaluation_at: str = "", awaiting: str = "") -> tuple[bool, str]:
    """Execute one decided action. Returns (made_progress, activity_detail)."""

    milestone = _ONCE_PER_ROUND_MILESTONES.get(action)
    if milestone and milestone in v.get("completed_milestones", []):
        return False, (f"skipped {action}: '{milestone}' is already recorded as complete "
                       f"-- use a founder-directed revision instead of re-running it")

    if action == "no_action_needed":
        # A no-op must carry an explicit business reason (observation window
        # still open, platform-blocked, founder-blocked, deliberate cadence,
        # etc.) -- manager_decide's prompt REQUIRES "reasoning" on every
        # decision, so log it instead of a static placeholder. A no-op with
        # no visible reason is not distinguishable from the system silently
        # doing nothing, which is exactly the failure mode this guards
        # against. next_evaluation_at/awaiting are enforced by
        # stages.manager_decide's own validation, so both are present
        # whenever we get here via the real pipeline.
        base = (reasoning.strip() or "no useful action right now "
               "(model returned no reasoning text -- this itself is worth noticing)")
        trailer = ""
        if awaiting:
            trailer += f" | awaiting: {awaiting}"
        if next_evaluation_at:
            trailer += f" | re-evaluate at: {next_evaluation_at}"
        return False, base + trailer

    if action == "advance_phase":
        to_phase = params.get("to_phase")
        if to_phase not in st.PHASES:
            raise StageValidationError(f"advance_phase: unknown phase {to_phase!r}")
        v["phase"] = to_phase
        return True, f"phase advanced -> {to_phase}"

    if action == "request_approval":
        # 2026-09-21 founder authorization: publishing/scheduling on Nyx's own
        # accounts is no longer a founder boundary, so an ask that is ONLY that
        # is refused here and the publish path is used instead. Spend, real-
        # person messaging, consent/hold/approval-mechanism, routing and
        # identity/safety asks are unaffected (see state.publish_only_approval_is_authorized).
        if st.publish_only_approval_is_authorized(
                reason=params["reason"], what=params["what"], why=params["why"],
                founder_action=params["founder_action"]):
            return False, ("not requested: publishing/scheduling on Nyx-owned accounts is standing-"
                           "authorized (founder 2026-09-21) -- publish it through the existing "
                           f"Metricool flow instead: {params['what'][:120]}")
        # 2026-09-16 fix: dedup by SAME underlying need (reason + what),
        # never by "any approval is pending at all" -- that used to let one
        # stuck, unrelated approval (e.g. an Instagram tooling failure)
        # silently block every other genuinely independent founder-consent
        # request (e.g. TikTok publish consent) for as long as it stayed
        # open. Multiple genuinely different approvals may now coexist;
        # only re-deriving the SAME one is refused as a duplicate.
        existing = st.find_pending_approval(reason=params["reason"], what_prefix=params["what"])
        if existing is not None:
            return False, (f"a matching founder approval is already pending ({existing['what'][:120]}) -- "
                          f"not creating a duplicate; waiting for the founder to resolve it")
        entry = st.write_pending_approval(
            reason=params["reason"], what=params["what"], why=params["why"],
            founder_action=params["founder_action"],
            estimated_minutes=int(params.get("estimated_minutes", 5)))
        return True, f"approval requested ({entry['reason']}): {entry['what']}"

    if action == "run_research_subtask":
        r = stages.research_subtask(params["question"])
        v.setdefault("research_log", []).append(r)
        _mark_done(v, f"researched: {r['question'][:80]}")
        detail = f"research: {r['question']} -> {r['findings'][:200]}"
        return True, detail

    if action == "run_compliance_checker":
        r = stages.compliance_checker(params["platform"], params["action_to_check"])
        v.setdefault("compliance_log", []).append(r)
        _mark_done(v, f"compliance_checked: {r['platform']} -- {r['action_checked'][:60]}")
        detail = f"compliance[{r['platform']}]: {r['verdict']} -- {r['action_checked']}"
        return True, detail

    if action == "run_persona_designer":
        research_context = json.dumps(v.get("hypothesis", {}))
        r = stages.persona_designer(research_context)
        v["persona"] = r
        _mark_done(v, "persona_designed")
        return True, f"persona designed: {r['name']} ({r['niche']})"

    if action == "run_content_brief":
        if not v.get("persona"):
            raise StageValidationError("run_content_brief: no persona defined yet")
        if "content_brief_generated" not in v.get("completed_milestones", []):
            # First-ever brief for this venture -- unchanged from before.
            r = stages.content_brief_generator(v["persona"])
            v["content_plan"] = r
            _mark_done(v, "content_brief_generated")
            return True, (f"content brief: {len(r['content_items'])} item(s), "
                          f"cadence: {r['posting_cadence']}")

        # Revision path (2026-09-15 standing founder authorization): a plan
        # already exists, so this APPENDS a small bounded batch instead of
        # replacing anything -- published history, QA results, and
        # active_story_threads are never touched. Only actually fires when
        # BOTH deterministic gates are met; otherwise this is a cheap,
        # harmless no-op (never wastes an LLM call on a premature revision).
        cp = v.setdefault("content_plan", {})
        usable = _usable_content_inventory(v)
        if len(usable) > CONTENT_INVENTORY_LOW_THRESHOLD:
            return False, (f"run_content_brief (revision) skipped: {len(usable)} usable "
                          f"item(s) still in the pipeline -- not low enough yet "
                          f"(threshold {CONTENT_INVENTORY_LOW_THRESHOLD})")
        since = _seconds_since(cp.get("last_brief_revision_at"))
        if since < MIN_CONTENT_BRIEF_REVISION_INTERVAL_S:
            return False, (f"run_content_brief (revision) skipped: last revision was "
                          f"{int(since)}s ago, minimum interval is "
                          f"{MIN_CONTENT_BRIEF_REVISION_INTERVAL_S}s")

        existing_items = cp.setdefault("content_items", [])
        start_index = len(existing_items)
        r = stages.content_brief_revision(
            v["persona"], cp.get("active_story_threads", []),
            params.get("performance_notes", ""), start_index)
        existing_items.extend(r["content_items"])
        cp.setdefault("brief_revisions", []).append({
            "at": _now_iso(), "added_count": len(r["content_items"]),
            "start_index": start_index, "posting_cadence_note": r.get("posting_cadence"),
            "notes": r.get("notes"),
        })
        cp["last_brief_revision_at"] = _now_iso()
        return True, (f"content brief revision: +{len(r['content_items'])} item(s) "
                      f"(indices {start_index}-{start_index + len(r['content_items']) - 1}, "
                      f"now {len(existing_items)} total) -- published history/story threads "
                      f"untouched")

    if action == "run_growth_audit":
        r = stages.growth_manager_audit(params["live_state_context"], v.get("persona", {}))
        st.record_specialist_audit(v, "growth", r)
        return True, f"growth audit: {r['summary'][:200]}"

    if action == "run_content_audit":
        r = stages.content_director_audit(params["live_state_context"], v.get("persona", {}),
                                          v.get("content_plan", {}))
        st.record_specialist_audit(v, "content", r)
        return True, f"content audit: {r['summary'][:200]}"

    if action == "run_sales_audit":
        r = stages.sales_manager_audit(params["live_state_context"], v.get("persona", {}))
        st.record_specialist_audit(v, "sales", r)
        return True, f"sales audit: {r['summary'][:200]}"

    if action == "run_content_generation":
        idx = params["content_item_index"]
        items = v.get("content_plan", {}).get("content_items", [])
        if not (0 <= idx < len(items)):
            raise StageValidationError(f"run_content_generation: no content_items[{idx}]")
        attempts_so_far = items[idx].get("regeneration_attempts", 0)
        if attempts_so_far >= MAX_REGENERATION_ATTEMPTS:
            return False, (f"content_items[{idx}]: regeneration bound reached "
                          f"({attempts_so_far} attempts, still not QA-passed) -- this needs a "
                          f"rebrief/different concept, not another attempt at the same one; "
                          f"skipping until the brief itself changes")
        higgsfield_remaining = st.credits_remaining(v)
        wan_remaining = st.wan_usd_remaining(v)
        business_context = params.get("business_context", "")

        if stages.is_story_carousel(items[idx]):
            # Carousels route deterministically (no routing LLM call, no
            # spend): ingest the slides ChatGPT already returned, or write the
            # one package it needs. Only an explicit founder approval on the
            # item falls through to the paid providers below.
            decision = carousel_handoff.provider_decision(
                items[idx], credits_remaining=higgsfield_remaining)
            if decision["provider"] == carousel_handoff.PROVIDER:
                missing = carousel_handoff.missing_slides(items[idx], idx)
                if not missing:
                    r = carousel_handoff.ingest_slides(v, idx)
                    items[idx]["regeneration_attempts"] = items[idx].get(
                        "regeneration_attempts", 0) + 1
                    return True, (f"content_items[{idx}]: ASSET_GENERATED from {r['slides']} "
                                 f"ChatGPT-returned slide(s), 0 Higgsfield credits")
                path = carousel_handoff.write_package(items[idx], v.get("persona", {}), index=idx)
                return False, (f"content_items[{idx}]: {len(missing)} slide(s) still missing "
                              f"({', '.join(missing)}) -- ChatGPT generation package at {path}, "
                              f"0 credits spent ({decision['reasoning']})")

        choice = stages.content_director_choose_provider(
            items[idx], v.get("persona", {}), business_context=business_context,
            higgsfield_credits_remaining=higgsfield_remaining, wan_usd_remaining=wan_remaining)

        if choice["provider"] == "skip":
            return False, f"content_items[{idx}]: not generated -- {choice['reasoning']}"

        if choice["provider"] == "reuse":
            st.set_content_item_lifecycle(v, idx, "QA_PASSED", asset_ref=choice["asset_ref"],
                                          model_used=None, cost_credits=0, cost_usd=0)
            return True, (f"content_items[{idx}]: reused {choice['asset_ref']} -- "
                         f"{choice['reasoning'][:200]}")

        if choice["provider"] == "higgsfield":
            if higgsfield_remaining <= 0:
                return False, (f"content_items[{idx}]: skipped -- credit ceiling already "
                              f"exhausted ({higgsfield_remaining} remaining)")
            r = stages.content_director_generate(
                items[idx], v.get("persona", {}), business_context=business_context,
                max_credits=higgsfield_remaining)
            if r["decision"] == "skipped":
                return False, f"content_items[{idx}]: not generated -- {r['reasoning']}"
            st.set_content_item_lifecycle(
                v, idx, "ASSET_GENERATED" if r["decision"] == "generated" else "QA_PASSED",
                asset_ref=r["asset_ref"], model_used=r.get("model_used") or None,
                cost_credits=r["cost_credits"] if r["decision"] == "generated" else 0)
            if r["decision"] == "generated":
                items[idx]["regeneration_attempts"] = items[idx].get("regeneration_attempts", 0) + 1
            if r["cost_credits"]:
                st.record_credit_spend(v, credits=r["cost_credits"],
                                       what=f"content_items[{idx}] ({r.get('model_used', '')}): "
                                            f"{r['business_purpose']}")
            return True, (f"content_items[{idx}]: {r['decision']} via higgsfield "
                         f"({r.get('model_used', 'reuse')}, {r['cost_credits']} credits) -- "
                         f"{r['reasoning'][:200]}")

        if choice["provider"] == "wan":
            model = choice["model_used"]
            spec = wan_provider.MODEL_SPECS[model]
            # free_quota_only is decided HERE, from the model's own reviewed
            # spec -- never from anything the LLM's choice dict said. This
            # is the structural guarantee that Wan cannot generate paid
            # content: a free_quota_protected model is ALWAYS called with
            # free_quota_only=True, regardless of what the routing stage
            # returned, and generate_video() itself refuses the opposite
            # combination (see wan_provider.py).
            if spec["billing_mode"] == "free_quota_protected":
                try:
                    result = wan_provider.generate_video(
                        model=model, image_url=choice["reference_image_url"],
                        prompt=choice["generation_prompt"], resolution=choice["resolution"],
                        duration_seconds=choice["duration_seconds"], free_quota_only=True,
                        item_label=f"content_item_{idx}")
                except wan_provider.WanNotConfigured as exc:
                    return False, f"content_items[{idx}]: Wan skipped -- {exc}"
                except wan_provider.WanFreeQuotaExhausted as exc:
                    # Fails safely: recorded, no automatic paid fallback, no
                    # automatic Higgsfield fallback -- the NEXT tick's own
                    # fresh routing decision decides again independently.
                    st.record_wan_generation_failure(
                        v, what=f"content_items[{idx}]: {exc}", reason="free_quota_exhausted")
                    return False, (f"content_items[{idx}]: Wan free quota exhausted -- not "
                                  f"generated this tick, no fallback triggered -- {exc}")
                except wan_provider.WanGenerationError as exc:
                    st.record_wan_generation_failure(v, what=f"content_items[{idx}]: {exc}")
                    return False, f"content_items[{idx}]: Wan generation FAILED -- {exc}"
            else:
                # Pay-per-second Wan models are not currently authorized for
                # autonomous selection (content_director_choose_provider's
                # validation blocks them) -- this branch stays here so the
                # dispatch code is ready when/if one is ever approved,
                # without needing a second generation pipeline later.
                if wan_remaining <= 0:
                    return False, (f"content_items[{idx}]: skipped -- Wan USD budget already "
                                  f"exhausted (${wan_remaining} remaining)")
                try:
                    result = wan_provider.generate_video(
                        model=model, image_url=choice["reference_image_url"],
                        prompt=choice["generation_prompt"], resolution=choice["resolution"],
                        duration_seconds=choice["duration_seconds"],
                        budget_usd_remaining=wan_remaining, item_label=f"content_item_{idx}")
                except wan_provider.WanNotConfigured as exc:
                    return False, f"content_items[{idx}]: Wan skipped -- {exc}"
                except wan_provider.WanBudgetExceeded as exc:
                    return False, f"content_items[{idx}]: Wan skipped -- {exc}"
                except wan_provider.WanGenerationError as exc:
                    st.record_wan_generation_failure(v, what=f"content_items[{idx}]: {exc}")
                    return False, f"content_items[{idx}]: Wan generation FAILED -- {exc}"

            st.set_content_item_lifecycle(
                v, idx, "ASSET_GENERATED", asset_ref=result["asset_ref"],
                model_used=result["model_used"], cost_credits=0, cost_usd=result["cost_usd"],
                generation_prompt=choice["generation_prompt"])
            items[idx]["regeneration_attempts"] = items[idx].get("regeneration_attempts", 0) + 1
            if result["cost_usd"]:
                st.record_wan_spend(v, usd=result["cost_usd"],
                                    what=f"content_items[{idx}] ({result['model_used']}): "
                                         f"{choice['business_purpose']}")
            return True, (f"content_items[{idx}]: generated via wan ({result['model_used']}, "
                         f"${result['cost_usd']:.2f}, 0 Higgsfield credits) -- "
                         f"{choice['reasoning'][:200]}")

        raise StageValidationError(f"run_content_generation: unknown provider {choice['provider']!r}")

    if action == "run_content_qa":
        idx = params["content_item_index"]
        items = v.get("content_plan", {}).get("content_items", [])
        if not (0 <= idx < len(items)):
            raise StageValidationError(f"run_content_qa: no content_items[{idx}]")
        item = items[idx]
        if item.get("lifecycle_status") != "ASSET_GENERATED":
            return False, (f"content_items[{idx}]: skipped -- not ASSET_GENERATED "
                          f"(status: {item.get('lifecycle_status', 'none')!r})")
        recent = _recent_published_items(v, exclude_index=idx)
        qa = stages.content_director_creative_qa(item, v.get("persona", {}), recent)
        item["qa_result"] = {k: qa[k] for k in
                             ("qa_verdict", "character_qa", "technical_qa",
                              "creative_qa", "business_qa", "reasoning")}
        if qa["qa_verdict"] == "PASS":
            st.set_content_item_lifecycle(v, idx, "QA_PASSED")
            return True, f"content_items[{idx}]: QA PASSED -- {qa['reasoning'][:200]}"
        return False, f"content_items[{idx}]: QA FAILED -- {qa['reasoning'][:200]}"

    if action == "run_metrics_analysis":
        r = stages.metrics_analyst(v["metrics"], v["thresholds"])
        v["last_metrics_analysis"] = r
        return True, f"metrics analysis: {r['summary'][:200]}"

    if action == "run_go_kill_gate":
        h1 = v.get("last_metrics_analysis", {}).get("h1_status", "no analysis yet")
        h2 = v.get("last_metrics_analysis", {}).get("h2_status", "no analysis yet")
        r = stages.go_kill_gate(h1, h2, v.get("iterate_count", 0))
        v["last_go_kill_recommendation"] = r
        st.write_pending_approval(
            reason="MAJOR_GO_KILL_DECISION",
            what=f"Manager recommends: {r['recommendation']}",
            why=r["reasoning"],
            founder_action=f"Review the recommendation and approve/override "
                           f"({r['recommendation']}) in the UI.",
            estimated_minutes=10)
        return True, f"go/kill gate -> recommends {r['recommendation']}, escalated to founder"

    raise StageValidationError(f"unknown action {action!r}")


def tick() -> dict:
    """One bounded tick. Returns a small summary dict for the caller/CLI."""
    v = st.load()
    if v is None:
        return {"error": "no venture state -- run seed_from_ro.py first"}

    pending_at_start = st.load_pending_approval()
    if pending_at_start is not None:
        audit.append(actor="manager", action="tick",
                     detail=f"pending founder approval exists ({pending_at_start['what'][:150]}) -- "
                            f"preserving it and searching for unrelated safe work rather than "
                            f"pausing the whole venture")

    # Cheap, no-LLM check every tick: start the 14-day monetization clock
    # the first time storefront readiness is actually reached (commercial-
    # acceleration rules, 2026-09-13). Idempotent -- a no-op on every tick
    # after the first time it fires.
    if dashboard_status.storefront_readiness(v)["ready"]:
        if st.start_monetization_clock(v):
            audit.append(actor="venture_manager", action="monetization_clock_started",
                        detail="Storefront readiness reached -- starting the 14-day commercial "
                               "acceleration window.")
            st.save(v)

    calls_made = 0
    summary = {"actions": []}
    while calls_made < MAX_LLM_CALLS_PER_TICK:
        if pending_at_start is None and st.load_pending_approval() is not None:
            break  # an action THIS tick's own loop just created one -- stop
                  # immediately (a pending_approval that already existed
                  # BEFORE this tick started does not stop the loop -- see
                  # pending_at_start below; a founder-only boundary blocks
                  # only the specific action it names, never the whole venture)
        state_dict = {k: v[k] for k in v if k not in ("research_log", "compliance_log")}
        # Plural (2026-09-16): the model needs to see the FULL queue, not
        # just the oldest entry, to correctly recognize that a second,
        # unrelated founder-consent need is never a duplicate of the first.
        state_dict["pending_founder_approvals"] = st.load_pending_approvals()
        state_json = json.dumps(state_dict, default=str)
        try:
            decision = stages.manager_decide(state_json, _recent_activity_text(), st.PHASES)
        except (LLMError, StageValidationError, ValueError) as exc:
            audit.append(actor="manager", action="error", detail=f"manager_decide failed: {exc}")
            summary["error"] = str(exc)
            break

        v["current_stage_kind"] = decision["action"]
        if decision.get("bottleneck"):
            st.set_bottleneck(v, decision["bottleneck"])
        for role, fields in (decision.get("manager_status") or {}).items():
            if role in st.MANAGER_ROLES and isinstance(fields, dict):
                st.set_manager_status(v, role, current_objective=fields.get("current_objective"),
                                      current_blocker=fields.get("current_blocker"))
        try:
            made_progress, detail = _dispatch(v, decision["action"], decision.get("params", {}),
                                              reasoning=decision.get("reasoning", ""),
                                              next_evaluation_at=decision.get("next_evaluation_at", ""),
                                              awaiting=decision.get("awaiting", ""))
        except (LLMError, StageValidationError, ValueError) as exc:
            audit.append(actor="manager", action="error",
                         detail=f"{decision['action']} failed: {exc}")
            summary["error"] = str(exc)
            break

        v["next_action"] = None
        st.set_manager_status(v, "venture", current_objective=decision.get("bottleneck", ""),
                              current_blocker=decision.get("bottleneck", "") if decision["action"] == "no_action_needed" else "none",
                              next_action=decision["action"],
                              next_action_at=decision.get("next_evaluation_at"),
                              last_action_result=detail[:300])
        st.save(v)
        audit.append(actor="manager", action=decision["action"], detail=detail,
                     cost_usd=decision.get("_cost_usd"))
        summary["actions"].append({"action": decision["action"], "detail": detail,
                                   "made_progress": bool(made_progress)})
        calls_made += 1
        if not made_progress or decision["action"] in ("request_approval", "no_action_needed"):
            break

    return summary


def watch(interval: int = DEFAULT_WATCH_INTERVAL_S) -> int:
    print(f"[manager] watching every {interval}s -- Ctrl-C to stop")
    try:
        while True:
            result = tick()
            if result.get("actions") or result.get("error"):
                print(json.dumps(result, indent=2))
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[manager] stopped")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", nargs="?", default="tick", choices=["tick", "watch"])
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=DEFAULT_WATCH_INTERVAL_S)
    args = ap.parse_args(argv)

    if args.watch or args.cmd == "watch":
        return watch(interval=args.interval)

    result = tick()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
