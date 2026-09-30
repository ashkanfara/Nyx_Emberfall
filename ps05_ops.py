#!/usr/bin/env python3
"""Stable CLI entrypoint for PS-05's own recurring scheduled-tick operations.

Exists so unattended Routine runs invoke ONE fixed, permission-matchable
command shape (python3 <this-fixed-path> <subcommand>) instead of writing a
new uniquely-pathed scratchpad script every tick -- a scratchpad path is
different every session, so no persistent permission rule can ever match it
twice. Anything here is read-only or writes only to this project's own
state/ files; nothing destructive, nothing outside this directory.

    python3 ps05_ops.py now
    python3 ps05_ops.py due-experiments
    python3 ps05_ops.py ro-isolation-check
    python3 ps05_ops.py stale-queued-actions
    python3 ps05_ops.py set-next-measurement <iso_ts> '<note text>'
    python3 ps05_ops.py set-specialist-review <role> <disposition> '<reason text>'
    python3 ps05_ops.py tick-start
    python3 ps05_ops.py tick-end '<json: outcome, summary, scheduler_enabled?, next_run_at?, cron_expression?>'
    python3 ps05_ops.py audit-log '<json: actor, action, detail, cost_usd?>'
    python3 ps05_ops.py carousel-package <content_item_index>   (zero spend, no publish)
    python3 ps05_ops.py connections                             (read-only: connected networks)
    python3 ps05_ops.py generate-slide <content_item_index> [slot]  (local, $0; preflight-first)
    python3 ps05_ops.py qa-slide <content_item_index> <slot> ['<json scores>']  (scored approval)
    python3 ps05_ops.py founder-approve-slide <content_item_index> <slot> '<approved_by>' ['<note>']
    python3 ps05_ops.py reject-slide <content_item_index> <slot> <failed_dims> '<reasons>' '<by>'
    python3 ps05_ops.py apply-story <content_item_index> <story.json>  (brief only, validated)
    python3 ps05_ops.py ingest-slides <content_item_index>      (all-or-nothing, $0, no publish)

tick-start/tick-end/audit-log take their variable content as ONE trailing JSON
argument specifically so the stable *command prefix* (`python3 ps05_ops.py
tick-end`, etc.) stays permission-matchable across ticks even though the
JSON payload itself differs every run -- see SKILL.md's note on why a single
`Bash(python3 ps05_ops.py *)` prefix-wildcard rule is the fix, not another
exact-match approval.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import audit
import scheduler_heartbeat
import state as st

RO_REPORT = (Path.home() / "Projects" / "research-orchestrator" / "runs" /
            "20260907T002913Z-b7001f" / "report.md")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def due_experiments() -> dict:
    v = st.load()
    due = st.due_experiments(v, now_iso=now())
    return {"now": now(), "due": [{"id": e["id"], "observation_window_end": e["observation_window_end"]}
                                  for e in due],
           "all_experiments": [{"id": e["id"], "status": e["status"],
                                "observation_window_end": e["observation_window_end"]}
                               for e in v.get("experiments", [])]}


def ro_isolation_check() -> dict:
    """Read-only mtime check -- never writes to research-orchestrator."""
    if not RO_REPORT.is_file():
        return {"ok": False, "error": f"expected file missing: {RO_REPORT}"}
    stat = RO_REPORT.stat()
    return {"ok": True, "path": str(RO_REPORT), "mtime": stat.st_mtime}


def stale_queued_actions() -> dict:
    v = st.load()
    pending = [a for a in v.get("queued_actions", []) if a["status"] == "WAITING_FOR_FOUNDER_CONSENT"]
    return {"pending": pending}


def set_next_measurement(iso_ts: str, note: str) -> dict:
    def _apply(v):
        v["next_measurement_at"] = iso_ts
        v["next_measurement_note"] = note
    st.update(_apply)
    return {"ok": True, "next_measurement_at": iso_ts}


def set_specialist_review(role: str, disposition: str, reason: str) -> dict:
    st.update(lambda v: st.set_specialist_review(v, role, disposition=disposition, reason=reason))
    return {"ok": True, "role": role, "disposition": disposition}


def apply_story(index_arg: str, story_path: str) -> dict:
    """Replace ONE story_carousel's slides from an approved story file -- the
    sanctioned way to revise a story, so nobody hand-edits venture.json.

    The revision is validated BEFORE it is written: it must clear
    stages.story_carousel_problems, which since 2026-09-23 includes the public
    top-of-funnel guards (no all-cinematic framing, no lingerie-adjacent
    wardrobe, no narrator-voice overlays, beats and overlay lines paired). A
    revision that fails is reported and nothing is saved. Never touches
    lifecycle, assets or schedules: a story change alone cannot advance an
    item or publish anything."""
    import stages

    try:
        index = int(index_arg)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"index must be an integer, got {index_arg!r}"}
    try:
        story = json.loads(Path(story_path).read_text())
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"cannot read story file {story_path}: {exc}"}
    slides = story.get("slides")
    if not slides and isinstance(story.get("story_plan"), list):
        # A story lock (brand/story_locks/<story_id>.json) carries the plan under
        # story_plan with the orchestrator's extra planning fields; only the brief
        # fields belong in venture.json, so the plan and the brief can never
        # become two independently edited stories.
        import story_continuity

        slides = story_continuity.brief_slides(story)
    if not isinstance(slides, list) or not slides:
        return {"ok": False, "error": "story file has no slides/story_plan list"}
    with st.venture_lock():               # read-modify-write: no tick write lands mid-span
        v = st.load()
        items = v.get("content_plan", {}).get("content_items", [])
        if not 0 <= index < len(items):
            return {"ok": False, "error": f"no content_items[{index}]"}
        item = items[index]
        if not stages.is_story_carousel(item):
            return {"ok": False, "error": f"content_items[{index}] is not a story_carousel"}
        if item.get("lifecycle_status") not in (None, "BRIEF_READY"):
            return {"ok": False, "error": f"content_items[{index}] is {item['lifecycle_status']} -- "
                                          f"only a brief may be rewritten, never generated content"}
        candidate = {**item, "slides": slides}
        problems = stages.story_carousel_problems(candidate)
        if problems:
            return {"ok": False, "status": "REJECTED", "index": index, "problems": problems,
                    "error": "revision does not satisfy the story/creative rules -- nothing saved"}
        previous = item.get("slides")
        item["slides"] = slides
        item.setdefault("story_revisions", []).append(
            {"at": now(), "story_id": story.get("story_id"), "source": str(story_path),
             "approved_by": story.get("approved_by"), "superseded_slides": previous})
        st.save(v)
    audit.append(actor="venture_manager", action="carousel_story_revised",
                 detail=f"content_items[{index}]: story replaced from {story_path} "
                        f"({len(slides)} slides); lifecycle untouched", cost_usd=None)
    return {"ok": True, "status": "STORY_REVISED", "index": index, "slides": len(slides),
            "story_id": story.get("story_id"),
            "lifecycle_status": item.get("lifecycle_status"),
            "overlays": [s.get("text_overlay", "") for s in slides],
            "next": f"re-emit the slide requests: generate-slide {index} 1 .. {index} {len(slides)}"}


def ingest_slides(index_arg: str, root: Path | None = None) -> dict:
    """Ingest the five externally-generated slides for ONE story_carousel
    (founder-approved 2026-09-23: a ChatGPT-side relay worker generates, PS-05
    stays the system of record). A thin wrapper over the existing
    carousel_handoff.ingest_slides -- all the guarantees already live there:
    story_carousel only, every expected filename present, magic-byte validated,
    all-or-nothing, ASSET_GENERATED at 0 credits / 0 USD only once all slides
    pass. Never publishes, never schedules, never calls a provider.

    Reports the overlay plan rather than silently baking text in, and burns the
    approved copy only when Pillow is available -- a missing Pillow blocks the
    burn-in, not the ingestion (the art is unaffected)."""
    import carousel_handoff

    try:
        index = int(index_arg)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"index must be an integer, got {index_arg!r}"}
    with st.venture_lock():               # read-modify-write: no tick write lands mid-span
        v = st.load()
        items = v.get("content_plan", {}).get("content_items", [])
        if not 0 <= index < len(items):
            return {"ok": False, "error": f"no content_items[{index}]"}
        item = items[index]
        try:
            missing = carousel_handoff.missing_slides(item, index, root)
        except carousel_handoff.HandoffError as exc:
            return {"ok": False, "error": str(exc)}
        if missing:
            # Named individually so ONE bad slide can be re-delivered (or replaced
            # via carousel_handoff.replace_slide) without redoing the other four.
            return {"ok": False, "status": "INCOMPLETE", "index": index,
                    "directory": str(carousel_handoff.package_dir(index, root)),
                    "missing_slides": missing, "cost_credits": 0,
                    "error": f"{len(missing)} slide(s) missing or unreadable -- partial carousels are "
                             f"never ingested; re-deliver just these filenames"}
        try:
            result = carousel_handoff.ingest_slides(v, index, root=root)
        except (carousel_handoff.HandoffError, IndexError) as exc:
            return {"ok": False, "error": str(exc)}
        st.save(v)

    overlays = carousel_handoff.overlay_plan(item, index, root)
    try:
        written = carousel_handoff.apply_overlays(item, index, root)
        overlay_status = f"applied to {len(written)} slide(s)"
    except Exception as exc:                  # noqa: BLE001 - ingestion already succeeded
        # Overlay burn-in is a separate, re-runnable step: Pillow missing, or a
        # file it cannot decode, must never undo a completed ingest or leave
        # the lifecycle half-advanced. Report it instead of raising.
        written, overlay_status = [], f"not applied: {type(exc).__name__}: {exc}"
    audit.append(actor="venture_manager", action="carousel_slides_ingested",
                 detail=f"content_items[{index}]: {result['slides']} slide(s) ingested from the "
                        f"ChatGPT-side worker, ASSET_GENERATED, 0 credits", cost_usd=None)
    return {"ok": True, **result, "index": index, "cost_usd": 0,
            "lifecycle_status": item.get("lifecycle_status"),
            "asset_refs": item.get("asset_refs", []),
            "overlay_slots": [o["slot"] for o in overlays],
            "overlay_status": overlay_status, "overlay_files": written,
            "next": "creative QA, then the existing Metricool publish path -- nothing is "
                    "published or scheduled by this command"}


def _slide_request(package: dict, spec: dict, index: int, slot: int,
                   root: Path | None = None) -> Path:
    """One slide's generation request, written beside the package. All five
    share job_id/story_thread/reference so a worker can see they are ONE job,
    and each carries the previous slide's continuity so 2-5 follow 1 rather
    than becoming five unrelated images."""
    import carousel_handoff

    sheet = package["continuity_sheet"]
    previous = package["slides"][slot - 2] if slot > 1 else None
    # Anchor the job id on the PERSISTED package, not on this call: build_package
    # stamps a fresh built_at every time, which would give each slide its own
    # job id and lose the "these five are one job" guarantee.
    persisted = carousel_handoff.package_dir(index, root) / carousel_handoff.PACKAGE_FILENAME
    try:
        built_at = json.loads(persisted.read_text())["built_at"]
    except (OSError, ValueError, KeyError):
        built_at = package["built_at"]
    request = {
        "job_id": f"{package['item_id']}@{built_at}",
        "item_id": package["item_id"], "item_index": index,
        "story_thread": package.get("story_thread") or sheet.get("story_thread", ""),
        "slide_index": slot, "slides_total": len(package["slides"]),
        "role": spec["role"], "beat": spec["beat"], "composition": spec["composition"],
        "continuity": spec["continuity"], "delta_from_previous": spec["delta_from_previous"],
        "carry_forward_from": ({"slide_index": slot - 1,
                                "expected_filename": previous["expected_filename"],
                                "continuity": previous["continuity"]} if previous else None),
        "identity_reference": {
            "project_path": str(carousel_handoff.CANONICAL_PORTRAIT),
            "hosted_url": carousel_handoff.MASTER_REFERENCE_URL,
            "authority": "attach on EVERY slide -- same woman in all five"},
        "image_prompt": spec["image_prompt"],
        "negative_constraints": spec["negative_constraints"],
        "text_in_image": spec["text_in_image"],
        "output": {"filename": spec["expected_filename"], "width": 1080, "height": 1350,
                   "directory": str(carousel_handoff.package_dir(index, root)),
                   "note": "all five slides must share identical dimensions"},
        "cost": {"incremental_cost": "none", "paid_providers_prohibited": True},
    }
    path = carousel_handoff.package_dir(index, root) / f"{Path(spec['expected_filename']).stem}.request.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(request, indent=2, ensure_ascii=False) + "\n")
    return path


def _story_dry_run(item: dict, index: int, slot: int, lock: dict, root: Path | None) -> dict:
    """The dry-run mode for a story with a continuity lock: the orchestrator
    compiles the plan, the prompt, the reference selection and the QA
    expectations and generates NOTHING. No provider is imported on this path,
    so it is structurally incapable of spending or of drawing a slide."""
    import story_continuity as sc

    try:
        payload = sc.dry_run(item, st.load().get("persona", {}), index=index, slot=slot, root=root)
    except sc.NotReady as exc:
        # The fail-closed refusal: slide N before slide N-1 is approved. Named
        # separately from a malformed-lock refusal because the fix is different
        # -- QA the previous slide, not repair the plan.
        return {"ok": False, "status": "BLOCKED_NOT_READY", "index": index, "slot": slot,
                "cost_usd": 0, "cost_credits": 0, "generated": False,
                "readiness": exc.gate, "error": str(exc),
                "next": f"QA and approve slide {slot - 1} first: "
                        f"qa-slide {index} {slot - 1} '<json scores>'"}
    except sc.ContinuityError as exc:
        return {"ok": False, "status": "BLOCKED", "index": index, "slot": slot,
                "cost_usd": 0, "cost_credits": 0, "generated": False, "error": str(exc)}
    request = payload["generation_request"]
    return {"ok": True, "status": "DRY_RUN", "index": index, "slot": slot,
            "generated": False, "cost_usd": 0, "cost_credits": 0,
            "story_id": payload["story_id"], "lock_file": payload["lock_file"],
            "generation_enabled": request["enabled"],
            "paid_generation_authorized": request["paid_generation_authorized"],
            "intended_provider": request["intended_provider"],
            "intended_model": request["intended_model"],
            "readiness": payload["readiness"]["status"],
            "visual_qa_provider": payload["visual_qa_provider"],
            "environment_lock_status": payload["environment_lock"]["status"],
            "environment_source_slide_ref": payload["environment_lock"].get("source_slide_ref"),
            "brief_sync": payload["brief_sync"]["status"],
            "precedence_conflicts": request["precedence_conflicts"],
            "references": [r.get("role") for r in request["references"]],
            "dry_run_file": payload.get("dry_run_file"),
            "blocked_reason": payload["blocked_reason"],
            "next": "inspect the compiled prompt and QA expectations in the dry-run file; "
                    "enabling a provider for this story is a founder decision"}


def qa_slide(index_arg: str, slot_arg: str, scores_json: str = "", root: Path | None = None) -> dict:
    """QA ONE candidate slide of a story under a continuity lock -- the only
    route from a candidate image to an approved slide, and the only thing that
    moves story state.

    A pass approves atomically: approved refs, composition history, story state,
    retry and cost bookkeeping move together, and a slide-1 pass freezes the
    environment master onto that image. A failure rejects only that candidate
    and touches nothing but the retry counter. No authorized visual-QA provider
    (or no scores) leaves the slide PENDING_VISUAL_QA -- a file sitting in the
    package directory is never an approval."""
    import story_continuity as sc

    try:
        index, slot = int(index_arg), int(slot_arg)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"index/slot must be integers, got {index_arg!r}/{slot_arg!r}"}
    try:
        scores = json.loads(scores_json) if str(scores_json or "").strip() else None
    except ValueError as exc:
        return {"ok": False, "error": f"scores must be a JSON object: {exc}"}
    if scores is not None and not isinstance(scores, dict):
        return {"ok": False, "error": "scores must be a JSON object of dimension -> pass/fail"}
    items = st.load().get("content_plan", {}).get("content_items", [])
    if not 0 <= index < len(items):
        return {"ok": False, "error": f"no content_items[{index}]"}
    lock = sc.load_story_lock(index)
    if lock is None:
        return {"ok": False, "error": f"content_items[{index}] has no story continuity lock"}
    try:
        record = sc.plan_record(lock, slot)
        state = sc.ensure_state(lock, index, root=root)
        notes = str((scores or {}).pop("notes", "") or "")
        out = sc.qa_slide(lock, state, record, index=index, scores=scores, notes=notes, root=root)
    except sc.ContinuityError as exc:
        return {"ok": False, "status": "BLOCKED", "index": index, "slot": slot, "error": str(exc)}
    if out["state_mutated"]:
        audit.append(actor="venture_manager", action="carousel_slide_qa",
                     detail=f"content_items[{index}] slide {slot}: {out['status']} "
                            f"({out['verdict']}); story state advanced only on approval",
                     cost_usd=None)
    return {"ok": out["approved"], "cost_usd": 0, "cost_credits": 0, "generated": False, **out}


PAID_SLIDE_GRANT_MAX_CREDITS = 20      # sanity cap on ONE slide grant, not a budget


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def grant_paid_slide(index_arg: str, slot_arg: str, credits_arg: str, approved_by: str,
                     provider: str = "higgsfield") -> dict:
    """FOUNDER ACTION: authorize exactly ONE paid Higgsfield generation for ONE
    slide. Deliberately narrower than the item-level
    paid_generation_approved_by_founder flag, which would let the autonomous
    tick route the WHOLE carousel to Higgsfield on its own: a slide grant is
    invisible to stages.paid_generation_approved / carousel_handoff.
    provider_decision, is consumed by the first submission, and raises the
    credit ceiling by exactly its own amount (any unused part is returned on
    use, the same one-time-grant pattern as credit_budget.ceiling_history)."""
    if _tick_in_progress():
        return {"ok": False, "status": "TICK_IN_PROGRESS",
                "error": "the hourly tick is running and rewrites venture.json when it ends -- a grant "
                         "written now could be silently erased; retry after the tick finishes"}
    if provider == "openai":
        return _grant_paid_slide_openai(index_arg, slot_arg, credits_arg, approved_by)
    if provider != "higgsfield":
        return {"ok": False, "error": f"unknown provider {provider!r} (higgsfield | openai)"}
    import higgsfield_image_provider as hip

    try:
        index, slot, credits = int(index_arg), int(slot_arg), float(credits_arg)
    except (TypeError, ValueError):
        return {"ok": False, "error": "usage: grant-paid-slide <index> <slot> <max_credits> <approved_by>"}
    if not str(approved_by or "").strip():
        return {"ok": False, "error": "approved_by is required (who approved this spend, and when)"}
    if not 0 < credits <= PAID_SLIDE_GRANT_MAX_CREDITS:
        return {"ok": False, "error": f"max_credits must be in (0, {PAID_SLIDE_GRANT_MAX_CREDITS}]"}
    with st.venture_lock():               # read-modify-write: no tick write lands mid-span
        v = st.load()
        items = v.get("content_plan", {}).get("content_items", [])
        if not 0 <= index < len(items):
            return {"ok": False, "error": f"no content_items[{index}]"}
        grants = items[index].setdefault("paid_slide_grants", [])
        if any(g.get("slot") == slot and not g.get("consumed_at") for g in grants):
            return {"ok": False, "error": f"slide {slot} already has an unconsumed grant"}
        grant = {"slot": slot, "provider": hip.PROVIDER, "model": hip.MODEL, "max_credits": credits,
                 "approved_by": approved_by.strip(), "granted_at": _now_iso(),
                 "scope": f"ONE paid generation of content_items[{index}] slide {slot}; single use; "
                          "never the whole carousel, never autonomous"}
        grants.append(grant)
        b = v["credit_budget"]
        before = b["session_limit"]
        st.set_credit_budget(v, session_limit=round(before + credits, 2),
                             purchases_allowed=b.get("purchases_allowed", False))
        b.setdefault("ceiling_history", []).append(
            {"at": grant["granted_at"], "from": before, "to": b["session_limit"],
             "scope": f"ONE-TIME: content_items[{index}] slide {slot} paid slide grant "
                      f"({hip.MODEL}); unused credits are returned when it is used",
             "approved_by": grant["approved_by"]})
        st.save(v)
    audit.append(actor="founder", action="paid_slide_grant",
                 detail=f"content_items[{index}] slide {slot}: up to {credits} credits via "
                        f"{hip.PROVIDER}/{hip.MODEL} ({grant['approved_by']})", cost_usd=None)
    return {"ok": True, "grant": grant, "credit_ceiling": {"from": before, "to": b["session_limit"]},
            "credits_remaining": st.credits_remaining(v)}


def _paid_ledger_path() -> Path:
    return Path(st.STATE_DIR) / "paid_generation_ledger.jsonl"


def _ledger_append(entry: dict) -> None:
    """Append-only record of every paid-slide grant and paid call, written the
    moment it happens. The hourly tick rewrites venture.json wholesale and can
    overwrite a concurrent ps05_ops change (2026-09-25: it erased a grant and the
    usage of the call that consumed it); nothing ever rewrites this file, so the
    real spend is never lost and venture.json can always be reconciled from it."""
    path = _paid_ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"at": _now_iso(), **entry}, ensure_ascii=False, default=str) + "\n")


def _tick_in_progress() -> bool:
    return bool(scheduler_heartbeat.status().get("tick_in_progress"))


PAID_SLIDE_GRANT_MAX_USD = 2.0          # sanity cap on ONE OpenAI slide grant, not a budget
_OPENAI_BUDGET_DEFAULTS = {"session_limit_usd": 0, "spent_this_session_usd": 0,
                           "spent_total_usd": 0, "ceiling_history": []}


def _openai_budget(v: dict) -> dict:
    """Founder-set USD ceiling for direct OpenAI image calls -- 0 (nothing
    authorized) until a slide grant raises it by exactly its own amount."""
    return v.setdefault("openai_budget", json.loads(json.dumps(_OPENAI_BUDGET_DEFAULTS)))


def _grant_paid_slide_openai(index_arg: str, slot_arg: str, usd_arg: str, approved_by: str) -> dict:
    import openai_image_provider as oip

    try:
        index, slot, usd = int(index_arg), int(slot_arg), float(usd_arg)
    except (TypeError, ValueError):
        return {"ok": False, "error": "usage: grant-paid-slide <index> <slot> <max_usd> <approved_by> "
                                      "--provider openai"}
    if not str(approved_by or "").strip():
        return {"ok": False, "error": "approved_by is required (who approved this spend, and when)"}
    if not 0 < usd <= PAID_SLIDE_GRANT_MAX_USD:
        return {"ok": False, "error": f"max_usd must be in (0, {PAID_SLIDE_GRANT_MAX_USD}]"}
    with st.venture_lock():               # read-modify-write: no tick write lands mid-span
        v = st.load()
        items = v.get("content_plan", {}).get("content_items", [])
        if not 0 <= index < len(items):
            return {"ok": False, "error": f"no content_items[{index}]"}
        grants = items[index].setdefault("paid_slide_grants", [])
        if any(g.get("slot") == slot and not g.get("consumed_at") for g in grants):
            return {"ok": False, "error": f"slide {slot} already has an unconsumed grant"}
        grant = {"slot": slot, "provider": oip.PROVIDER, "model": oip.MODEL, "unit": "usd",
                 "max_usd": usd, "approved_by": approved_by.strip(), "granted_at": _now_iso(),
                 "scope": f"ONE paid generation of content_items[{index}] slide {slot}; single use; "
                          "never the whole carousel, never autonomous"}
        grants.append(grant)
        b = _openai_budget(v)
        before = b["session_limit_usd"]
        b["session_limit_usd"] = round(before + usd, 4)
        b["ceiling_history"].append({"at": grant["granted_at"], "from": before,
                                     "to": b["session_limit_usd"],
                                     "scope": f"ONE-TIME: content_items[{index}] slide {slot} OpenAI "
                                              f"slide grant ({oip.MODEL}); unused USD returned when used",
                                     "approved_by": grant["approved_by"]})
        st.save(v)
    _ledger_append({"event": "grant", "index": index, "slot": slot, "grant": grant})
    audit.append(actor="founder", action="paid_slide_grant",
                 detail=f"content_items[{index}] slide {slot}: up to ${usd} via {oip.PROVIDER}/"
                        f"{oip.MODEL} ({grant['approved_by']})", cost_usd=None)
    return {"ok": True, "grant": grant,
            "openai_ceiling_usd": {"from": before, "to": b["session_limit_usd"]}}


def _generate_slide_openai(index: int, slot: int, *, root: Path | None, confirm_spend: bool,
                           quality: str | None = None, client=None) -> dict:
    """ONE direct OpenAI candidate for ONE story slide (founder 2026-09-25:
    gpt-image-2.5-sunburst). The orchestrator's compiled prompt and reference
    selection are used as-is; without --confirm-spend this is a DRY RUN that
    reports the exact request, the estimate and every gate, and sends nothing.
    With --confirm-spend every gate must pass. The result is a CANDIDATE left
    at PENDING_VISUAL_QA; no fallback to any other provider."""
    import carousel_handoff
    import openai_image_provider as oip
    import openai_runtime
    import story_continuity as sc

    quality = quality or oip.DEFAULT_QUALITY
    base = {"index": index, "slot": slot, "provider": oip.PROVIDER, "model": oip.MODEL,
            "generated": False, "api_request_sent": False, "cost_usd": 0, "cost_credits": 0}
    v = st.load()
    item = v["content_plan"]["content_items"][index]
    lock = sc.load_story_lock(index)
    if lock is None:
        return {**base, "ok": False, "status": "NO_STORY_LOCK",
                "error": "premium slides are generated only under a story continuity lock"}
    probs = sc.lock_problems(lock) + sc.plan_problems(lock, item)
    if probs:
        return {**base, "ok": False, "status": "LOCK_INVALID", "error": "; ".join(probs)}
    try:
        record = sc.plan_record(lock, slot)
        state = sc.ensure_state(lock, index, root=root)
    except sc.ContinuityError as exc:
        return {**base, "ok": False, "status": "BLOCKED", "error": str(exc)}
    gate = sc.readiness(state, record, root=root)
    if not gate["ready"]:
        return {**base, "ok": False, "status": "BLOCKED_NOT_READY", "readiness": gate,
                "error": "; ".join(gate["blockers"])}
    persona = v.get("persona", {})
    compiled = sc.compile_prompt(persona, state, record)
    selected = sc.reference_selection(persona, state, record, index=index, root=root)
    try:
        project_root = Path(__file__).resolve().parent
        refs = oip.resolve_references(selected, project_root=project_root,
                                      package_dir=carousel_handoff.package_dir(index, root),
                                      identity_override=oip.generation_identity_references(project_root))
        spec = oip.request_spec(compiled["prompt"], refs, quality)
    except oip.OpenAIImageError as exc:
        return {**base, "ok": False, "status": "REQUEST_INVALID", "error": str(exc)}
    out = sc.candidate_path(index, slot, root)
    grant = next((g for g in item.get("paid_slide_grants") or []
                  if g.get("slot") == slot and not g.get("consumed_at")
                  and g.get("provider") == oip.PROVIDER and g.get("model") == oip.MODEL), None)
    budget = _openai_budget(v)
    remaining = round(budget["session_limit_usd"] - budget["spent_this_session_usd"], 4)
    simulated = [r["slide_index"] for r in state["approved_slide_refs"] if r.get("simulated")]
    gates = {
        "founder_slide_grant": grant is not None,
        "openai_ceiling_covers_grant": bool(grant) and remaining >= float(grant["max_usd"]),
        "estimate_within_grant": bool(grant) and spec["estimate"]["estimated_usd_high"] <= float(grant["max_usd"]),
        "no_simulated_approvals": not simulated,
        "no_existing_candidate": not out.exists(),
        "api_key_stored": openai_runtime.has_credentials(),
        "confirm_spend_flag": bool(confirm_spend),
    }
    blocking = [k for k, ok in gates.items() if not ok]
    qa = sc.qa_expectations(lock, record, state)
    report = {**base, "quality": quality, "request": spec, "readiness": gate, "gates": gates,
              "blocking_gates": blocking, "candidate_output": str(out),
              "raw_output": str(out.with_name(f"{out.stem}.{oip.PROVIDER}_raw.png")),
              "qa_expectations": qa, "visual_qa_provider": sc.visual_qa_provider(lock),
              "openai_budget_usd": {"limit": budget["session_limit_usd"],
                                    "spent": budget["spent_this_session_usd"], "remaining": remaining}}
    if not confirm_spend:
        directory = carousel_handoff.package_dir(index, root)
        directory.mkdir(parents=True, exist_ok=True)
        dry = directory / f"{out.stem}.{oip.PROVIDER}.dryrun.json"
        dry.write_text(json.dumps({**report, "mode": "PAID_DRY_RUN", "built_at": _now_iso()},
                                  indent=2, ensure_ascii=False) + "\n")
        return {**report, "ok": True, "status": "PAID_DRY_RUN", "dry_run_file": str(dry),
                "next": "a founder grant (grant-paid-slide ... --provider openai) plus "
                        "--confirm-spend are both required before anything is sent"}
    if blocking:
        return {**report, "ok": False, "status": "GATES_BLOCKED",
                "error": "refusing to send: " + ", ".join(blocking)}
    if _tick_in_progress():
        return {**report, "ok": False, "status": "TICK_IN_PROGRESS",
                "error": "the hourly tick is running and rewrites venture.json when it ends; nothing sent"}

    try:
        res = oip.generate(spec, out_path=out, max_usd=float(grant["max_usd"]), client=client)
    except Exception as exc:
        # Sent but outcome unknown (timeout / transport). Consume the grant so
        # nothing resubmits blindly; record no guessed cost.
        _ledger_append({"event": "paid_call", "index": index, "slot": slot, "provider": oip.PROVIDER,
                        "model": oip.MODEL, "outcome": "UNKNOWN", "error": type(exc).__name__,
                        "prompt_sha256_16": spec["prompt_sha256_16"], "grant_granted_at": grant["granted_at"]})
        with st.venture_lock():
            v = st.load()
            g = _grant_for_update(v, index, slot, grant)
            g.update(consumed_at=_now_iso(), outcome="UNKNOWN", error=type(exc).__name__)
            st.save(v)
        audit.append(actor="venture_manager", action="carousel_slide_generated_paid",
                     detail=f"content_items[{index}] slide {slot}: OUTCOME UNKNOWN via "
                            f"{oip.PROVIDER}/{oip.MODEL} ({type(exc).__name__}) -- grant consumed; "
                            "check the OpenAI usage dashboard before any retry", cost_usd=None)
        return {**report, "ok": False, "status": "OUTCOME_UNKNOWN", "api_request_sent": True,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    actual = float(res.get("actual_usd") or 0)
    meta_path = out.with_name(f"{out.stem}.generation.json")
    if res.get("submitted"):
        # First, before touching venture.json: the returned usage goes to the
        # append-only ledger and the metadata file, so no later failure can lose it.
        _ledger_append({"event": "paid_call", "index": index, "slot": slot, "provider": oip.PROVIDER,
                        "model": oip.MODEL, "quality": quality, "status": res.get("status"),
                        "usage": res.get("usage"), "actual_usd": actual,
                        "actual_usd_known": res.get("actual_usd_known"),
                        "prompt_sha256_16": spec["prompt_sha256_16"],
                        "reference_sha256_16": [r["sha256_16"] for r in spec["references"]],
                        "grant_granted_at": grant["granted_at"], "candidate": res.get("path")})
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps({"request": spec, "result": res, "grant": grant,
                                         "recorded_at": _now_iso(), "status_after": None},
                                        indent=2, ensure_ascii=False, default=str) + "\n")
    with st.venture_lock():               # read-modify-write: no tick write lands mid-span
        v = st.load()
        g = _grant_for_update(v, index, slot, grant)
        if g.get("restored_after_concurrent_write"):
            b = _openai_budget(v)
            if not any(h.get("at") == grant["granted_at"] for h in b["ceiling_history"]):
                before = b["session_limit_usd"]
                b["session_limit_usd"] = round(before + float(grant["max_usd"]), 5)
                b["ceiling_history"].append({"at": grant["granted_at"], "from": before,
                                             "to": b["session_limit_usd"], "restored": True,
                                             "scope": "grant ceiling raise restored after a concurrent "
                                                      "venture.json write erased it"})
        if res.get("submitted"):
            g.update(consumed_at=_now_iso(), charged_usd=actual, usage=res.get("usage"),
                     actual_usd_known=res.get("actual_usd_known"))
            b = _openai_budget(v)
            b["spent_this_session_usd"] = round(b["spent_this_session_usd"] + actual, 5)
            b["spent_total_usd"] = round(b["spent_total_usd"] + actual, 5)
            unused = round(float(grant["max_usd"]) - actual, 5)
            if unused > 0:
                before = b["session_limit_usd"]
                b["session_limit_usd"] = round(before - unused, 5)
                b["ceiling_history"].append(
                    {"at": g["consumed_at"], "from": before, "to": b["session_limit_usd"],
                     "scope": f"OpenAI slide grant content_items[{index}] slide {slot} used: ${actual} "
                              f"charged, unused ${unused} returned so no other item can spend it"})
            st.save(v)
            state = sc.load_state(lock, index, root=root)
            cs = state["cost_state"]
            cs["actual_cost_usd"] = round(float(cs.get("actual_cost_usd") or 0) + actual, 5)
            state.setdefault("generation_log", []).append(
                {"slide_index": slot, "at": g["consumed_at"], "provider": oip.PROVIDER,
                 "model": oip.MODEL, "quality": quality, "usage": res.get("usage"),
                 "actual_usd": actual, "status": res.get("status"),
                 "candidate": out.name if res.get("ok") else None})
            sc.save_state(state, index, root=root)
    qa_out = None
    if res.get("ok"):
        qa_out = sc.qa_slide(lock, sc.load_state(lock, index, root=root), record, index=index,
                             scores=None, root=root, write=False)
    meta_path.write_text(json.dumps({"request": {k: val for k, val in spec.items()}, "result": res,
                                     "grant": g, "recorded_at": _now_iso(),
                                     "status_after": qa_out["status"] if qa_out else None},
                                    indent=2, ensure_ascii=False, default=str) + "\n")
    persisted = _grant_for_update(st.load(), index, slot, grant, restore=False)
    bookkeeping_ok = bool(persisted and persisted.get("consumed_at"))
    audit.append(actor="venture_manager", action="carousel_slide_generated_paid",
                 detail=f"content_items[{index}] slide {slot}: {res.get('status')} via "
                        f"{oip.PROVIDER}/{oip.MODEL} ({quality}), ${actual} per returned usage; "
                        f"{'candidate PENDING_VISUAL_QA' if res.get('ok') else 'no candidate'}",
                 cost_usd=actual or None)
    return {**report, "ok": bool(res.get("ok")), "api_request_sent": bool(res.get("submitted")),
            "status": qa_out["status"] if qa_out else res.get("status"),
            "generated": bool(res.get("ok")), "cost_usd": actual, "usage": res.get("usage"),
            "problems": res.get("problems"), "path": res.get("path"), "raw_path": res.get("raw_path"),
            "metadata_file": str(meta_path), "approved": False,
            "bookkeeping_verified": bookkeeping_ok,
            "paid_ledger": str(_paid_ledger_path())}


def _grant_for_update(v: dict, index: int, slot: int, grant: dict, *, restore: bool = True) -> dict | None:
    """The persisted copy of `grant`. If a concurrent venture.json write erased
    it, restore the pre-call copy (flagged) instead of crashing mid-bookkeeping."""
    grants = v["content_plan"]["content_items"][index].setdefault("paid_slide_grants", [])
    g = next((x for x in grants if x.get("slot") == slot
              and x.get("granted_at") == grant["granted_at"]), None)
    if g is None and restore:
        g = {**grant, "restored_after_concurrent_write": _now_iso()}
        grants.append(g)
    return g


def _generate_slide_higgsfield(index: int, slot: int, *, root: Path | None, confirm_spend: bool,
                               caller=None, downloader=None) -> dict:
    """ONE premium candidate for ONE story slide (founder 2026-09-24: GPT Image 2
    through the existing Higgsfield connector). Every gate fails closed and is
    checked before anything is submitted; the story continuity orchestrator owns
    the prompt and references, and the result is a CANDIDATE left at
    PENDING_VISUAL_QA -- never an approval."""
    import carousel_handoff
    import higgsfield_image_provider as hip
    import story_continuity as sc

    base = {"index": index, "slot": slot, "provider": hip.PROVIDER, "model": hip.MODEL,
            "settings": dict(hip.SETTINGS), "generated": False, "cost_credits": 0}
    v = st.load()
    item = v["content_plan"]["content_items"][index]
    lock = sc.load_story_lock(index)
    if lock is None:
        return {**base, "ok": False, "status": "NO_STORY_LOCK",
                "error": "premium slides are generated only under a story continuity lock"}
    probs = sc.lock_problems(lock)
    if probs:
        return {**base, "ok": False, "status": "LOCK_INVALID", "error": "; ".join(probs)}
    grant = next((g for g in item.get("paid_slide_grants") or []
                  if g.get("slot") == slot and not g.get("consumed_at")
                  and g.get("provider") == hip.PROVIDER and g.get("model") == hip.MODEL), None)
    if grant is None:
        return {**base, "ok": False, "status": "NO_PAID_SLIDE_GRANT",
                "error": f"no unconsumed founder grant for slide {slot} on {hip.PROVIDER}/"
                         f"{hip.MODEL}; run grant-paid-slide first (a founder decision)"}
    max_credits = float(grant["max_credits"])
    remaining = st.credits_remaining(v)
    if remaining < max_credits:
        return {**base, "ok": False, "status": "CREDIT_CEILING",
                "error": f"credit ceiling has {remaining} left, grant needs {max_credits}"}
    try:
        record = sc.plan_record(lock, slot)
        state = sc.ensure_state(lock, index, root=root)
    except sc.ContinuityError as exc:
        return {**base, "ok": False, "status": "BLOCKED", "error": str(exc)}
    simulated = [r["slide_index"] for r in state["approved_slide_refs"] if r.get("simulated")]
    if simulated:
        return {**base, "ok": False, "status": "SIMULATED_APPROVALS",
                "error": f"slides {simulated} are simulated approvals -- never a basis for paid work"}
    gate = sc.readiness(state, record, root=root)
    if not gate["ready"]:
        return {**base, "ok": False, "status": "BLOCKED_NOT_READY", "readiness": gate,
                "error": "; ".join(gate["blockers"])}
    out = sc.candidate_path(index, slot, root)
    if out.exists():
        return {**base, "ok": False, "status": "CANDIDATE_EXISTS",
                "error": f"{out.name} already exists -- QA or reject it before paying for another"}
    compiled = sc.compile_prompt(v.get("persona", {}), state, record)
    reference_url = carousel_handoff.MASTER_REFERENCE_URL
    spec = hip.request_spec(compiled["prompt"], reference_url)
    base.update(max_credits=max_credits, grant=grant, request=spec,
                candidate=str(out), readiness=gate)
    if confirm_spend and _tick_in_progress():
        return {**base, "ok": False, "status": "TICK_IN_PROGRESS",
                "error": "the hourly tick is running and rewrites venture.json when it ends; nothing sent"}
    if not confirm_spend:
        return {**base, "ok": True, "status": "READY_TO_SPEND",
                "next": f"re-run with --confirm-spend to submit ONE generation (<= {max_credits} credits)"}

    try:
        res = hip.generate(compiled["prompt"], reference_url=reference_url, out_path=out,
                           max_credits=max_credits, caller=caller, downloader=downloader)
    except Exception as exc:
        # Outcome unknown (timeout / unparseable reply): a job may have been
        # submitted. Fail closed -- consume the grant so nothing resubmits
        # blindly, record no guessed charge, and hand it to the founder.
        with st.venture_lock():
            v = st.load()
            g = _grant_for_update(v, index, slot, grant)
            g.update(consumed_at=_now_iso(), outcome="UNKNOWN", error=str(exc)[:300])
            st.save(v)
        audit.append(actor="venture_manager", action="carousel_slide_generated_paid",
                     detail=f"content_items[{index}] slide {slot}: OUTCOME UNKNOWN via "
                            f"{hip.PROVIDER}/{hip.MODEL} ({str(exc)[:160]}) -- grant consumed; "
                            "check Higgsfield transactions before any retry", cost_usd=None)
        return {**base, "ok": False, "status": "OUTCOME_UNKNOWN", "error": str(exc)[:300],
                "next": "check Higgsfield job history/transactions; do not resubmit blindly"}
    pr, check = res.get("provider_result") or {}, res["validation"]
    submitted = pr.get("status") in ("GENERATED", "FAILED")
    charged = check.get("charged_credits") or 0
    with st.venture_lock():               # read-modify-write: no tick write lands mid-span
        v = st.load()                               # re-read: the call took minutes
        item = v["content_plan"]["content_items"][index]
        if submitted:
            _ledger_append({"event": "paid_call", "index": index, "slot": slot,
                            "provider": hip.PROVIDER, "model": hip.MODEL, "status": pr.get("status"),
                            "charged_credits": charged, "job_id": pr.get("job_id"),
                            "grant_granted_at": grant["granted_at"]})
        g = _grant_for_update(v, index, slot, grant)
        if submitted:
            g.update(consumed_at=_now_iso(), charged_credits=charged, job_id=pr.get("job_id"),
                     preflight_credits=check.get("preflight_credits"))
            if charged:
                st.record_credit_spend(v, credits=charged, what=f"content_items[{index}] slide {slot} "
                                                                f"{hip.MODEL}")
            unused = round(max_credits - charged, 2)
            if unused > 0:                          # return the unused part of the one-time grant
                b = v["credit_budget"]
                before = b["session_limit"]
                b["session_limit"] = round(before - unused, 2)
                b.setdefault("ceiling_history", []).append(
                    {"at": g["consumed_at"], "from": before, "to": b["session_limit"],
                     "scope": f"slide grant content_items[{index}] slide {slot} used: {charged} "
                              f"charged, unused {unused} returned so no other item can spend it"})
            st.save(v)
            state = sc.load_state(lock, index, root=root)
            cs = state["cost_state"]
            cs["credits_spent"] = round(float(cs.get("credits_spent") or 0) + charged, 2)
            state.setdefault("generation_log", []).append(
                {"slide_index": slot, "at": g["consumed_at"], "provider": hip.PROVIDER,
                 "model": hip.MODEL, "job_id": pr.get("job_id"), "charged_credits": charged,
                 "preflight_credits": check.get("preflight_credits"), "status": pr.get("status"),
                 "candidate": out.name if res.get("ok") else None})
            sc.save_state(state, index, root=root)
    meta_path = out.with_name(f"{out.stem}.generation.json")
    meta = {**{k: val for k, val in res.items() if k != "ok"}, "grant": g,
            "recorded_at": _now_iso(), "status_after": None}
    qa = None
    if res.get("ok"):
        qa = sc.qa_slide(lock, sc.load_state(lock, index, root=root), record, index=index,
                         scores=None, root=root, write=False)
        meta["status_after"] = qa["status"]
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    audit.append(actor="venture_manager", action="carousel_slide_generated_paid",
                 detail=f"content_items[{index}] slide {slot}: {pr.get('status')} via "
                        f"{hip.PROVIDER}/{hip.MODEL}, {charged} credits charged "
                        f"(preflight {check.get('preflight_credits')}); "
                        f"{'candidate PENDING_VISUAL_QA' if res.get('ok') else 'no candidate'}",
                 cost_usd=None)
    return {**base, "ok": bool(res.get("ok")),
            "status": qa["status"] if qa else ("PROVIDER_FAILED" if submitted else pr.get("status")
                                               or "NOT_SUBMITTED"),
            "generated": bool(res.get("ok")), "cost_credits": charged,
            "preflight_credits": check.get("preflight_credits"),
            "validation_problems": check.get("problems"), "job_id": pr.get("job_id"),
            "path": res.get("path"), "raw_path": res.get("raw_path"),
            "metadata_file": str(meta_path), "approved": False,
            "next": "visual QA against brand/nyx_identity/reference_primary.webp "
                    "(qa-slide); nothing is approved automatically"}


def reject_slide(index_arg: str, slot_arg: str, failed_dims: str, reasons: str, rejected_by: str,
                 root: Path | None = None) -> dict:
    """FOUNDER ACTION: reject ONE candidate slide through the story's own retry
    lifecycle (story_continuity.reject_slide) -- the same path a failed visual QA
    takes: only the retry counter moves, the story state and environment lock do
    not. The rejected files are archived (never deleted) under
    rejected/slide<N>_attempt<k>/ so the slot is free for a new candidate."""
    import shutil

    import carousel_handoff
    import story_continuity as sc

    try:
        index, slot = int(index_arg), int(slot_arg)
    except (TypeError, ValueError):
        return {"ok": False, "error": "index/slot must be integers"}
    failed = [d.strip() for d in str(failed_dims or "").split(",") if d.strip()]
    unknown = [d for d in failed if d not in sc.QA_DIMENSIONS]
    if not failed or unknown:
        return {"ok": False, "error": f"failed dimensions must be a comma list of {list(sc.QA_DIMENSIONS)}"
                                      + (f"; unknown: {unknown}" if unknown else "")}
    if not str(rejected_by or "").strip() or not str(reasons or "").strip():
        return {"ok": False, "error": "reasons and rejected_by are required"}
    lock = sc.load_story_lock(index)
    if lock is None:
        return {"ok": False, "error": f"content_items[{index}] has no story continuity lock"}
    record = sc.plan_record(lock, slot)
    state = sc.ensure_state(lock, index, root=root)
    cand = sc.candidate_path(index, slot, root)
    if not cand.is_file():
        return {"ok": False, "status": "NO_CANDIDATE", "error": f"no candidate at {cand}"}
    qa = sc.qa_result({d: d not in failed for d in sc.QA_DIMENSIONS},
                      notes=f"FOUNDER REJECTION by {rejected_by.strip()}: {reasons.strip()}")
    outcome = sc.reject_slide(state, record, qa)
    sc.save_state(state, index, root=root)
    dest = carousel_handoff.package_dir(index, root) / "rejected" / f"slide{slot}_attempt{outcome['attempt']}"
    dest.mkdir(parents=True, exist_ok=True)
    moved = []
    for f in sorted(cand.parent.glob(f"{cand.stem}*")):
        if f.is_file() and not f.name.endswith((".dryrun.json", ".prompt.txt", ".request.json")):
            shutil.move(str(f), dest / f.name)
            moved.append(f.name)
    (dest / "rejection.json").write_text(json.dumps({"qa": qa, "outcome": outcome, "archived": moved},
                                                    indent=2, ensure_ascii=False) + "\n")
    audit.append(actor="founder", action="carousel_slide_rejected",
                 detail=f"content_items[{index}] slide {slot} attempt {outcome['attempt']}: REJECTED "
                        f"({', '.join(failed)}) -- {reasons.strip()[:200]}", cost_usd=None)
    return {"ok": True, "status": "REJECTED", "attempt": outcome["attempt"],
            "attempts_remaining": outcome["attempts_remaining"], "critical_failures": qa["critical_failures"],
            "archived_to": str(dest), "archived": moved, "story_state_changed": False}


def _overlay_safe_zones(lock: dict) -> dict:
    """slot -> the story lock's worded overlay_safe_zone. The brief in
    venture.json carries only the approved copy; where that copy may sit is a
    story-lock fact, so the deterministic renderer reads it from there."""
    import story_continuity as sc

    return {r["slide_index"]: r.get("overlay_safe_zone", "") for r in sc.story_plan(lock)}


def render_slide_overlay(index: int, slot: int, *, root: Path | None = None,
                         lock: dict | None = None, item: dict | None = None) -> dict:
    """The final PUBLIC image for ONE approved slide: the approved art plus the
    approved copy, burned in deterministically by the existing PS-05 overlay
    tooling (carousel_handoff.overlay_plan/apply_overlays). No image model, no
    provider, no cost -- and the clean approved source is left untouched beside
    it, because that file, not the captioned one, is the environment master."""
    import carousel_handoff
    import story_continuity as sc

    lock = lock if lock is not None else sc.load_story_lock(index)
    if item is None:
        item = st.load()["content_plan"]["content_items"][index]
    zones = _overlay_safe_zones(lock) if lock else None
    plan = carousel_handoff.overlay_plan(item, index, root, safe_zones=zones, slots=[slot])
    if not plan:
        return {"ok": False, "status": "NO_OVERLAY_COPY",
                "error": f"slide {slot} is briefed wordless -- the approved art is already final"}
    step = plan[0]
    if not Path(step["source"]).is_file():
        return {"ok": False, "status": "NO_SOURCE", "error": f"no approved art at {step['source']}"}
    try:
        written = carousel_handoff.apply_overlays(item, index, root, safe_zones=zones, slots=[slot])
    except carousel_handoff.OverlayUnavailable as exc:   # pragma: no cover - environment-dependent
        return {"ok": False, "status": "OVERLAY_UNAVAILABLE", "plan": step, "error": str(exc)}
    out = Path(written[0])
    dimensions = None
    try:
        from PIL import Image

        with Image.open(out) as img:
            dimensions = f"{img.width}x{img.height}"
    except Exception:                                    # pragma: no cover - already written
        pass
    return {"ok": True, "status": "OVERLAY_APPLIED", "slot": slot, "text": step["text"],
            "safe_zone": step["safe_zone"], "position": step["position"],
            "clean_source": step["source"], "output": str(out), "dimensions": dimensions,
            "cost_usd": 0, "cost_credits": 0, "image_model_used": False}


def founder_approve_slide(index_arg: str, slot_arg: str, approved_by: str, note: str = "",
                          root: Path | None = None) -> dict:
    """FOUNDER ACTION: approve ONE candidate slide because the founder looked at
    the image and says it is good -- the counterpart of reject-slide, and an
    explicit auditable transition, never a fabricated visual-QA score.

    The decision is recorded in the story lock's founder_approvals (the
    founder-editable, version-controlled surface) and then applied through the
    SAME lifecycle a QA pass uses: story_continuity.apply_founder_approvals ->
    approve_slide, which atomically writes the approved ref, the composition
    fingerprint and the advanced story state, and -- for slide 1 -- LOCKS the
    environment master onto that real image. No visual-QA provider is required
    or consulted; only the named slide is approved, never a later one; a second
    run on an already-approved slide changes nothing. Retry and cost history are
    left exactly as they are. Finally the deterministic overlay renders the
    public image; nothing here can call a provider or spend."""
    import story_continuity as sc

    try:
        index, slot = int(index_arg), int(slot_arg)
    except (TypeError, ValueError):
        return {"ok": False, "error": "index/slot must be integers"}
    if not str(approved_by or "").strip():
        return {"ok": False, "error": "approved_by is required (who approved this image, and when)"}
    lock = sc.load_story_lock(index)
    if lock is None:
        return {"ok": False, "error": f"content_items[{index}] has no story continuity lock"}
    lock_path = Path(lock["lock_path"])
    state = sc.ensure_state(lock, index, root=root)
    cand = sc.candidate_path(index, slot, root)
    existing = sc.approved_ref(state, slot)
    if existing is not None:
        # Idempotent, and deliberately not a re-approval: the environment master
        # is already frozen onto a real file and a second approval could point
        # it somewhere else.
        return {"ok": True, "status": "ALREADY_APPROVED", "index": index, "slot": slot,
                "approved_ref": existing, "advanced": False, "duplicate": True,
                "environment_lock_status": state["environment_lock"]["status"],
                "environment_source_slide_ref": state["environment_lock"].get("source_slide_ref"),
                "note": "slide already approved -- nothing re-approved, nothing re-advanced"}
    entry = {"slide_index": slot, "slide_ref": cand.name, "simulated": False,
             "approved_by": approved_by.strip(), "approved_at": _now_iso(),
             "approval_kind": "founder_explicit_visual_approval",
             "visual_qa_scorer_used": False, "note": str(note or "").strip()}
    problems = sc.founder_approval_problems(lock, state, entry, index=index, root=root)
    if problems:
        return {"ok": False, "status": "BLOCKED", "index": index, "slot": slot,
                "problems": problems, "error": "; ".join(problems)}

    on_disk = json.loads(lock_path.read_text())
    on_disk.setdefault("founder_approvals", []).append(entry)
    lock_path.write_text(json.dumps(on_disk, indent=2, ensure_ascii=False) + "\n")
    try:
        # ensure_state applies the entry through apply_founder_approvals ->
        # approve_slide and persists the result; it is the one lifecycle route.
        fresh = sc.load_story_lock(index)
        state = sc.ensure_state(fresh, index, root=root)
        approved = sc.approved_ref(state, slot)
        if approved is None:
            raise sc.ContinuityError(f"slide {slot} was not approved by the lifecycle")
    except sc.ContinuityError as exc:
        on_disk["founder_approvals"] = [e for e in on_disk["founder_approvals"] if e is not entry]
        lock_path.write_text(json.dumps(on_disk, indent=2, ensure_ascii=False) + "\n")
        return {"ok": False, "status": "BLOCKED", "index": index, "slot": slot, "error": str(exc)}
    env = state["environment_lock"]
    overlay = render_slide_overlay(index, slot, root=root, lock=fresh)
    record = {"index": index, "slot": slot, "entry": entry, "approved_ref": approved,
              "environment_lock": {"status": env["status"],
                                   "source_slide_ref": env.get("source_slide_ref"),
                                   "locked_at": env.get("locked_at")},
              "story_state": state["story_state"], "overlay": overlay,
              "retry_state": state["retry_state"], "cost_state": state["cost_state"]}
    approval_file = cand.with_name(f"{cand.stem}.founder_approval.json")
    approval_file.parent.mkdir(parents=True, exist_ok=True)
    approval_file.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n")
    audit.append(actor="founder", action="carousel_slide_founder_approved",
                 detail=f"content_items[{index}] slide {slot}: APPROVED by {approved_by.strip()[:120]} "
                        f"({cand.name}); story state advanced once, environment_lock "
                        f"{env['status']}; no visual-QA scorer used, 0 spend", cost_usd=None)
    return {"ok": True, "status": "APPROVED", "index": index, "slot": slot,
            "approved_by": entry["approved_by"], "approval_kind": entry["approval_kind"],
            "visual_qa_scorer_used": False, "advanced": True, "duplicate": False,
            "approved_ref": approved, "clean_approved_image": str(cand),
            "environment_lock_status": env["status"],
            "environment_source_slide_ref": env.get("source_slide_ref"),
            "environment_locked_at": env.get("locked_at"),
            "approved_slides": [r["slide_index"] for r in state["approved_slide_refs"]],
            "story_state": state["story_state"], "retry_state": state["retry_state"],
            "overlay": overlay, "approval_file": str(approval_file),
            "state_file": str(sc.state_path(index, root)), "lock_file": str(lock_path),
            "cost_usd": 0, "cost_credits": 0, "generated": False,
            "next": f"slide {slot + 1} may now be dry-run: generate-slide {index} {slot + 1}"}


def generate_slide(index_arg: str, slot_arg: str = "1", root: Path | None = None, *,
                   provider: str | None = None, confirm_spend: bool = False,
                   caller=None, downloader=None, quality: str | None = None,
                   client=None) -> dict:
    """Generate ONE carousel slide through the LOCAL, zero-incremental-cost
    provider (founder-authorized 2026-09-22). Preflight-first: if the machine
    or the setup is not ready it reports exactly what is missing and generates
    nothing -- it never downloads weights, installs packages, calls a paid
    provider or touches a credit. Prompts come from the existing handoff
    package, so the canonical identity lock is reused, never re-invented.

    An item with a story continuity lock (brand/story_locks/) runs in DRY_RUN
    instead while that lock's generation.enabled is false: the orchestrator owns
    the prompt, and nothing draws until the founder authorizes a provider."""
    import carousel_handoff
    import local_image_provider as lip
    import stages
    import story_continuity

    try:
        index, slot = int(index_arg), int(slot_arg)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"index/slot must be integers, got {index_arg!r}/{slot_arg!r}"}
    items = st.load().get("content_plan", {}).get("content_items", [])
    if not 0 <= index < len(items):
        return {"ok": False, "error": f"no content_items[{index}]"}
    item = items[index]
    if not stages.is_story_carousel(item):
        return {"ok": False, "error": f"content_items[{index}] is not a story_carousel"}
    slides = item.get("slides") or []
    if not 1 <= slot <= len(slides):
        return {"ok": False, "error": f"slot {slot} is outside 1..{len(slides)}"}

    if provider not in (None, "", "local", "higgsfield", "openai"):
        return {"ok": False, "error": f"unknown provider {provider!r} (local | higgsfield | openai)"}
    if provider == "openai":
        return _generate_slide_openai(index, slot, root=root, confirm_spend=confirm_spend,
                                      quality=quality, client=client)
    if provider == "higgsfield":
        return _generate_slide_higgsfield(index, slot, root=root, confirm_spend=confirm_spend,
                                          caller=caller, downloader=downloader)
    if confirm_spend:
        return {"ok": False, "error": "--confirm-spend only applies to --provider higgsfield"}

    lock = story_continuity.load_story_lock(index)
    if lock is not None and not (lock.get("generation") or {}).get("enabled"):
        return _story_dry_run(item, index, slot, lock, root)

    persona = st.load().get("persona", {})

    if lock is not None:
        # Under a story continuity lock, the REAL generation path must be
        # gated by exactly the same authorities the dry run reports --
        # readiness (slide order + environment lock + retry ceiling),
        # simulated-approval refusal, and the story compiler's own layered
        # prompt/reference selection. There is no local-provider-only
        # shortcut around any of these, and an already-APPROVED slide is
        # never regenerated to fix a later one.
        problems = story_continuity.plan_problems(lock, item)
        if problems:
            return {"ok": False, "status": "BLOCKED", "index": index, "slot": slot,
                    "cost_usd": 0, "cost_credits": 0, "generated": False,
                    "error": f"story plan is not generatable: {'; '.join(problems)}"}
        try:
            record = story_continuity.plan_record(lock, slot)
        except story_continuity.ContinuityError as e:
            return {"ok": False, "status": "BLOCKED", "index": index, "slot": slot,
                    "cost_usd": 0, "cost_credits": 0, "generated": False, "error": str(e)}
        state = story_continuity.ensure_state(lock, index, root=root)
        existing = story_continuity.approved_ref(state, slot)
        if existing is not None:
            return {"ok": False, "status": "ALREADY_APPROVED", "index": index, "slot": slot,
                    "cost_usd": 0, "cost_credits": 0, "generated": False,
                    "error": f"slide {slot} is already an APPROVED slide ({existing['value']}) -- "
                             f"an approved slide is never regenerated to fix a later one"}
        simulated = [r["slide_index"] for r in state["approved_slide_refs"] if r.get("simulated")]
        if simulated:
            return {"ok": False, "status": "SIMULATED_APPROVALS", "index": index, "slot": slot,
                    "cost_usd": 0, "cost_credits": 0, "generated": False,
                    "error": f"slides {simulated} are SIMULATED fixture approvals -- they exist to "
                             f"compile and inspect later slides, never to authorize drawing one"}
        # Prompt/readiness consumers below read the PROJECTED, currently-valid
        # context (see project_active_context's docstring) -- not the raw,
        # persisted state -- so a since-revoked slide's narrative advance can
        # never leak into what this slide is generated from. The raw `state`
        # is still what ALREADY_APPROVED/SIMULATED_APPROVALS checked above,
        # and what approve_slide/reject_slide/revoke_approval operate on.
        active = story_continuity.project_active_context(state, lock, root=root)
        gate = story_continuity.readiness(active, record, root=root)
        if not gate["ready"]:
            return {"ok": False, "status": "BLOCKED_NOT_READY", "index": index, "slot": slot,
                    "cost_usd": 0, "cost_credits": 0, "generated": False,
                    "readiness": gate, "error": "; ".join(gate["blockers"]),
                    "next": f"QA and approve slide {slot - 1} first: "
                            f"qa-slide {index} {slot - 1} '<json scores>'"}
        compiled = story_continuity.compile_prompt(persona, active, record)
        full_compiled_prompt = compiled["prompt"]
        expected_filename = carousel_handoff.slide_filename(index, slot)
        # Resolve the story compiler's reference SELECTION (roles/authorities)
        # into actual local files for this 2-reference-max installation. The
        # full reference sheet is never used here -- GENERATION_SAFE_REFERENCE
        # is the derived, human-ear-free crop this identity spec designates
        # for generation. A previous/environment reference the state claims
        # is LOCKED but whose file is actually missing on disk REFUSES; it is
        # never silently dropped in favour of the identity crop alone.
        selection = story_continuity.reference_selection(persona, active, record, index=index, root=root)
        env_entry = next((r for r in selection if r["role"] == "environment_reference"), None)
        # Shot-aware identity reference (2026-09-26 fix): a wide/full-body
        # shot needs the crop that actually shows the ears
        # (GENERATION_SAFE_REFERENCE_HEAD), not the face-only closeup -- see
        # local_image_provider.is_wide_or_full_body_shot's docstring for the
        # real slide-4 render this was diagnosed from.
        identity_ref = (carousel_handoff.GENERATION_SAFE_REFERENCE_HEAD
                        if lip.is_wide_or_full_body_shot(record)
                        else carousel_handoff.GENERATION_SAFE_REFERENCE)
        references = [identity_ref]
        if slot > 1:
            if env_entry is None:
                return {"ok": False, "status": "BLOCKED_NOT_READY", "index": index, "slot": slot,
                        "cost_usd": 0, "cost_credits": 0, "generated": False,
                        "error": f"slide {slot} has no environment reference in this story's state "
                                 f"-- refusing rather than falling back to the identity crop alone"}
            env_path = carousel_handoff.package_dir(index, root) / env_entry["value"]
            if not env_path.is_file():
                return {"ok": False, "status": "BLOCKED_NOT_READY", "index": index, "slot": slot,
                        "cost_usd": 0, "cost_credits": 0, "generated": False,
                        "error": f"environment reference {env_path} is missing on disk -- refusing "
                                 f"rather than falling back to the identity crop alone"}
            references = [env_path, identity_ref]
        # FLUX.2 Klein 4B is a small model: the full ~2000-word layered
        # compiler prompt above subordinated the actual shot instruction and
        # measurably produced face-forward drift on real candidates (2026-09-
        # 26) -- see local_image_provider.adapt_prompt_for_local's docstring.
        # This is a narrow, local-model-only adaptation of the SAME record/
        # state facts; full_compiled_prompt (the audited source of truth)
        # is still saved alongside every candidate, never discarded.
        adapted = lip.adapt_prompt_for_local(record, active, has_environment_reference=isinstance(
            references, list) and len(references) == 2)
        prompt = adapted["prompt"]
        prompt_audit = {"full_compiled_prompt": full_compiled_prompt,
                        "adapted_prompt_used": prompt, "adaptation": adapted}
    else:
        package = carousel_handoff.build_package(item, persona, index=index, root=root)
        spec = package["slides"][slot - 1]
        prompt = spec["image_prompt"]
        expected_filename = spec["expected_filename"]
        references = carousel_handoff.GENERATION_SAFE_REFERENCE
        prompt_audit = None

    pre = lip.preflight()
    if not pre["ready"]:
        # Local generation is unavailable, so emit the machine-readable request
        # for THIS slide instead of failing silently: one file per slide, all
        # five sharing a job identity, which is exactly what the authorized
        # external worker consumes. Still generates nothing and spends nothing.
        package = carousel_handoff.build_package(item, persona, index=index, root=root)
        spec = package["slides"][slot - 1]
        req = _slide_request(package, spec, index, slot, root)
        return {"ok": False, "status": "REQUEST_EMITTED", "index": index, "slot": slot,
                "cost_usd": 0, "cost_credits": 0, "generated": False,
                "local_status": pre["status"], "hardware": pre["hardware"],
                "hardware_blockers": pre["hardware_blockers"],
                "request_file": str(req),
                "job_id": json.loads(Path(req).read_text())["job_id"],
                "awaiting": "an external worker to write "
                            f"{spec['expected_filename']} into this directory",
                "error": "local provider not ready -- request emitted, nothing generated"}
    out = carousel_handoff.package_dir(index, root) / expected_filename
    # 2026-09-30 defect fix: generate() defaults to seed 42 and was never given one, so a
    # CORRECTION RETRY re-rendered byte-identically (verified: content_items[32] slide 1
    # attempt 2 had the same SHA1 as attempt 1) and could only reproduce the failure that
    # caused the rejection -- which is also exactly what content_items[30] slide 1's
    # manifest recorded as "two identical, non-varying failing candidates". The founder's
    # retry rule ("at most one correction retry per slide") only means something if the
    # retry actually differs, so each attempt of a slide gets its own seed.
    attempt_no = 0
    if lock is not None:
        attempt_no = int(((active.get("retry_state") or {}).get("attempts") or {}).get(str(slot), 0))
    res = lip.generate(prompt, out, reference=references, seed=42 + attempt_no)
    if not res.get("ok"):
        return {"ok": False, "index": index, "slot": slot, **res}
    # The same magic-byte check ingestion will apply, run now so a broken write
    # is caught here rather than surfacing later as a "missing" slide.
    remaining = carousel_handoff.missing_slides(item, index, root)
    audit_path = None
    if prompt_audit is not None:
        # Keep the full compiled prompt on record alongside whatever was
        # actually sent to the local model -- never only the adapted one.
        audit_path = out.with_suffix("")
        audit_path = audit_path.parent / f"{audit_path.name}.audit.json"
        audit_path.write_text(json.dumps({
            "item_index": index, "slide_index": slot,
            "full_compiled_prompt": prompt_audit["full_compiled_prompt"],
            "adapted_prompt_used": prompt_audit["adapted_prompt_used"],
            "adaptation": {k: v for k, v in prompt_audit["adaptation"].items() if k != "prompt"},
        }, indent=2))
    return {"ok": True, "status": "GENERATED", "index": index, "slot": slot,
            "cost_usd": 0, "cost_credits": 0, "route": lip.ROUTE,
            "model": pre["model"], "license": pre["license"],
            "path": res["path"], "dimensions": f"{res['width']}x{res['height']}",
            "reference_used": res["reference_used"],
            "expected_filename": expected_filename,
            "slide_readable": expected_filename not in remaining,
            "slides_still_missing": remaining,
            "prompt_audit_file": str(audit_path) if audit_path else None,
            "next": "QA this slide against brand/nyx_identity/reference_primary.webp "
                    "before generating the rest; overlays and ingestion stay with carousel_handoff"}


def metricool_connections(caller=None) -> dict:
    """Read-only: which networks are ACTUALLY connected on the Nyx brand, so
    the Threads/X question is answered from Metricool's own payload instead of
    guessed. Calls exactly ONE tool (getBrandSettings) with only that tool
    pre-approved, so it is structurally incapable of creating or changing a
    post; `connected` lists the raw networksData keys Metricool returns (its
    key name for X is not assumed here -- read the list)."""
    import nyx_instagram as nx

    caller = caller or nx.call_tool
    res = caller(nx.TOOL_BRAND, {}, allowed=(nx.TOOL_BRAND,))
    if not res.get("ok"):
        return {"ok": False, "status": res.get("status"), "detail": res.get("detail")}
    brands = []
    for b in (res.get("data") or {}).get("data") or []:
        nets = b.get("networksData") or {}
        brands.append({"brand_id": b.get("id"), "label": b.get("label"),
                       "timezone": b.get("timezone"),
                       "connected": sorted(k for k, v in nets.items() if v)})
    return {"ok": True, "brands": brands,
            "nyx_brand_id": nx.NYX_BRAND_ID,
            "threads_connected": any("threadsData" in b["connected"] for b in brands
                                     if b["brand_id"] == nx.NYX_BRAND_ID)}


def carousel_package(index_arg: str, root: Path | None = None) -> dict:
    """Rebuild ONE story_carousel item's zero-spend handoff package and report
    the slides still missing -- the routine repair when a package on disk was
    written in an older format (e.g. the pre-relocatable package_version 3).

    Structurally incapable of spending or publishing: it only calls
    carousel_handoff.write_package/missing_slides, reads venture.json without
    ever saving it (no lifecycle change), imports no provider or Metricool
    module, and refuses outright when the item carries the founder's paid-
    generation approval -- that route belongs to the paid pipeline and to a
    spend decision made at action time, never to this command."""
    import carousel_handoff                 # local import: the tick's own import cost is unchanged
    import stages

    try:
        index = int(index_arg)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"index must be an integer, got {index_arg!r}"}
    items = st.load().get("content_plan", {}).get("content_items", [])
    if not 0 <= index < len(items):
        return {"ok": False, "error": f"no content_items[{index}]"}
    item = items[index]
    if not stages.is_story_carousel(item):
        return {"ok": False, "error": f"content_items[{index}] is not a story_carousel"}
    if stages.carousel_paid_generation_approved(item):
        return {"ok": False, "error": f"content_items[{index}] is approved for PAID generation -- "
                                      f"this command never runs the paid pipeline"}
    try:
        path = carousel_handoff.write_package(item, st.load().get("persona", {}),
                                              index=index, root=root)
    except carousel_handoff.HandoffError as exc:
        return {"ok": False, "error": f"package not written: {exc}"}
    package = json.loads(path.read_text())
    out = {"ok": True, "index": index, "package_path": str(path),
           "package_version": package["package_version"],
           "provider": carousel_handoff.PROVIDER, "incremental_cost": "none", "cost_credits": 0,
           "lifecycle_status": item.get("lifecycle_status"),
           "slides_expected": len(package["slides"]),
           "missing_slides": carousel_handoff.missing_slides(item, index, root)}
    # An item under a story continuity lock also gets its persisted state
    # created (once) and reported here -- a stale brief has to be visible from
    # the same routine command that rebuilds the package, not discovered later.
    import story_continuity as sc

    lock = sc.load_story_lock(index)
    if lock is not None:
        problems = sc.plan_problems(lock, item)
        try:
            state = sc.ensure_state(lock, index, root=root)
        except sc.ContinuityError as exc:
            return {**out, "ok": False, "status": "CONTINUITY_BLOCKED", "error": str(exc)}
        approved = [r["slide_index"] for r in state["approved_slide_refs"]
                    if str(r.get("value") or "").strip() and not r.get("simulated")]
        # The public deliverable of an APPROVED slide is the captioned image, so
        # rebuilding the package also (re-)renders the overlay for the slides the
        # story has actually approved -- deterministically, from the clean art
        # already on disk. Never for an unapproved slide, never an image model.
        overlays = [render_slide_overlay(index, slot, root=root, lock=lock, item=item)
                    for slot in approved]
        out["story_continuity"] = {
            "story_id": lock["story_id"], "lock_file": lock.get("lock_file", ""),
            "state_file": str(sc.state_path(index, root)),
            "environment_lock_status": state["environment_lock"]["status"],
            "environment_source_slide_ref": state["environment_lock"].get("source_slide_ref"),
            "approved_slides": [r["slide_index"] for r in state["approved_slide_refs"]],
            "brief_sync": sc.brief_sync(lock, item),
            "plan_problems": problems,
            "overlays": overlays,
            "generation_enabled": bool((lock.get("generation") or {}).get("enabled"))}
    return out


def tick_start() -> dict:
    scheduler_heartbeat.record_tick_start()
    return {"ok": True}


def tick_end(payload_json: str) -> dict:
    payload = json.loads(payload_json)
    scheduler_heartbeat.record_tick_end(
        outcome=payload["outcome"],
        summary=payload["summary"],
        scheduler_enabled=payload.get("scheduler_enabled"),
        next_run_at=payload.get("next_run_at"),
        cron_expression=payload.get("cron_expression"),
    )
    return {"ok": True}


def audit_log(payload_json: str) -> dict:
    payload = json.loads(payload_json)
    audit.append(
        actor=payload["actor"],
        action=payload["action"],
        detail=payload["detail"],
        cost_usd=payload.get("cost_usd"),
    )
    return {"ok": True}


SCHED_SNAPSHOT_PATH = Path(__file__).resolve().parent / "state" / "scheduler_task_snapshot.json"


def record_scheduler_snapshot(payload_json: str) -> dict:
    """Persists the ground truth from a real mcp__scheduled-tasks__list_scheduled_tasks
    call (lastRunAt/nextRunAt/enabled/cronExpression) so the dashboard -- which
    cannot call that tool itself, only an active Claude session can -- can
    detect the one failure mode scheduler_heartbeat.json can never see on its
    own: a tick the scheduler genuinely DISPATCHED but that never even reached
    its own tick-start call (e.g. stalled on a brand-new command's first-ever
    permission prompt). Call this as early as possible in a run, right after
    STEP 0, and also from any interactive session checking on scheduler health."""
    payload = json.loads(payload_json)
    snapshot = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_run_at": payload.get("last_run_at"),
        "next_run_at": payload.get("next_run_at"),
        "enabled": payload.get("enabled"),
        "cron_expression": payload.get("cron_expression"),
    }
    SCHED_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHED_SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2) + "\n")
    return {"ok": True, "snapshot": snapshot}


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(json.dumps({"ok": False, "error": "usage: now | due-experiments | "
                          "ro-isolation-check | stale-queued-actions | set-next-measurement | "
                          "set-specialist-review"}))
        return 1
    cmd = argv[0]
    if cmd == "now":
        print(now())
    elif cmd == "due-experiments":
        print(json.dumps(due_experiments(), indent=2))
    elif cmd == "ro-isolation-check":
        print(json.dumps(ro_isolation_check(), indent=2))
    elif cmd == "stale-queued-actions":
        print(json.dumps(stale_queued_actions(), indent=2))
    elif cmd == "set-next-measurement":
        if len(argv) != 3:
            print(json.dumps({"ok": False, "error": "usage: set-next-measurement <iso_ts> '<note>'"}))
            return 1
        print(json.dumps(set_next_measurement(argv[1], argv[2]), indent=2))
    elif cmd == "set-specialist-review":
        if len(argv) != 4:
            print(json.dumps({"ok": False, "error": "usage: set-specialist-review <role> <disposition> '<reason>'"}))
            return 1
        print(json.dumps(set_specialist_review(argv[1], argv[2], argv[3]), indent=2))
    elif cmd == "tick-start":
        print(json.dumps(tick_start(), indent=2))
    elif cmd == "tick-end":
        if len(argv) != 2:
            print(json.dumps({"ok": False, "error": "usage: tick-end '<json>'"}))
            return 1
        print(json.dumps(tick_end(argv[1]), indent=2))
    elif cmd == "audit-log":
        if len(argv) != 2:
            print(json.dumps({"ok": False, "error": "usage: audit-log '<json>'"}))
            return 1
        print(json.dumps(audit_log(argv[1]), indent=2))
    elif cmd == "apply-story":
        if len(argv) != 3:
            print(json.dumps({"ok": False, "error": "usage: apply-story <content_item_index> <story.json>"}))
            return 1
        result = apply_story(argv[1], argv[2])
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    elif cmd == "ingest-slides":
        if len(argv) != 2:
            print(json.dumps({"ok": False, "error": "usage: ingest-slides <content_item_index>"}))
            return 1
        result = ingest_slides(argv[1])
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    elif cmd == "generate-slide":
        flags = [a for a in argv[1:] if a.startswith("--")]
        pos = [a for a in argv[1:] if not a.startswith("--")]
        provider = None
        for f in flags:
            if f.startswith("--provider="):
                provider = f.split("=", 1)[1]
        if "--provider" in flags:
            i = argv.index("--provider")
            provider = argv[i + 1] if i + 1 < len(argv) else ""
            pos = [a for a in pos if a != provider]
        quality = None
        for f in flags:
            if f.startswith("--quality="):
                quality = f.split("=", 1)[1]
        if "--quality" in flags:
            i = argv.index("--quality")
            quality = argv[i + 1] if i + 1 < len(argv) else ""
            pos = [a for a in pos if a != quality]
        unknown = [f for f in flags if f not in ("--provider", "--confirm-spend", "--quality")
                   and not f.startswith(("--provider=", "--quality="))]
        if unknown or len(pos) not in (1, 2):
            print(json.dumps({"ok": False, "error": "usage: generate-slide <content_item_index> [slot] "
                                                    "[--provider local|higgsfield|openai] [--quality Q] [--confirm-spend]"}))
            return 1
        result = generate_slide(pos[0], pos[1] if len(pos) == 2 else "1", provider=provider,
                                confirm_spend="--confirm-spend" in flags, quality=quality)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    elif cmd == "grant-paid-slide":
        gprov = "higgsfield"
        rest = list(argv[1:])
        if "--provider" in rest:
            i = rest.index("--provider")
            gprov = rest[i + 1] if i + 1 < len(rest) else ""
            del rest[i:i + 2]
        if len(rest) != 4:
            print(json.dumps({"ok": False, "error": "usage: grant-paid-slide <content_item_index> "
                                                    "<slot> <max_credits|max_usd> '<approved_by>' "
                                                    "[--provider higgsfield|openai]"}))
            return 1
        result = grant_paid_slide(rest[0], rest[1], rest[2], rest[3], provider=gprov)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    elif cmd == "founder-approve-slide":
        if len(argv) not in (4, 5):
            print(json.dumps({"ok": False, "error": "usage: founder-approve-slide "
                                                    "<content_item_index> <slot> '<approved_by>' "
                                                    "['<note>']"}))
            return 1
        result = founder_approve_slide(argv[1], argv[2], argv[3],
                                       argv[4] if len(argv) == 5 else "")
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    elif cmd == "reject-slide":
        if len(argv) != 6:
            print(json.dumps({"ok": False, "error": "usage: reject-slide <content_item_index> <slot> "
                                                    "<failed_dim,...> '<reasons>' '<rejected_by>'"}))
            return 1
        result = reject_slide(argv[1], argv[2], argv[3], argv[4], argv[5])
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    elif cmd == "qa-slide":
        if len(argv) not in (3, 4):
            print(json.dumps({"ok": False,
                              "error": "usage: qa-slide <content_item_index> <slot> ['<json scores>']"}))
            return 1
        result = qa_slide(argv[1], argv[2], argv[3] if len(argv) == 4 else "")
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    elif cmd == "connections":
        result = metricool_connections()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    elif cmd == "carousel-package":
        if len(argv) != 2:
            print(json.dumps({"ok": False, "error": "usage: carousel-package <content_item_index>"}))
            return 1
        result = carousel_package(argv[1])
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    elif cmd == "record-scheduler-snapshot":
        if len(argv) != 2:
            print(json.dumps({"ok": False, "error": "usage: record-scheduler-snapshot '<json>'"}))
            return 1
        print(json.dumps(record_scheduler_snapshot(argv[1]), indent=2))
    else:
        print(json.dumps({"ok": False, "error": f"unknown subcommand {cmd!r}"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
