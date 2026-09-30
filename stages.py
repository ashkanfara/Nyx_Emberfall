"""Stage functions: one bounded Claude call (+ QA where applicable) per stage,
validated against a small explicit schema. Shape forked from research-
orchestrator's stage_plan/stage_execute/stage_qa pattern; content is entirely
new (see prompts.py)."""

from __future__ import annotations

import json
import re

import prompts
import wan_provider
from claude_client import claude_call, extract_json


class StageValidationError(ValueError):
    pass


def _require(d: dict, keys: tuple[str, ...], stage: str) -> None:
    if not isinstance(d, dict):
        raise StageValidationError(f"{stage}: result is not a JSON object")
    missing = [k for k in keys if k not in d]
    if missing:
        raise StageValidationError(f"{stage}: missing keys {missing}")


def manager_decide(state_json: str, recent_activity: str, valid_phases: tuple[str, ...]) -> dict:
    prompt = prompts.MANAGER_DECIDE.format(state_json=state_json, recent_activity=recent_activity,
                                           valid_phases=", ".join(valid_phases))
    # Explicit 300s (default is 180s): this call's own state_json payload has
    # grown to ~40K+ tokens as the venture has accumulated content_items/
    # queued_actions/commercial_events history, and the default timeout was
    # regularly too tight for it -- confirmed via activity_log.jsonl showing
    # repeated "claude call timed out after 180s" failures on manager_decide
    # specifically (multiple per day), each one silently losing an entire
    # tick with no decision and no recorded reasoning at all. No other stage
    # call carries a payload this large, so only this one call's timeout
    # changes.
    r = claude_call(prompt, tools=None, timeout=300)
    d = extract_json(r["text"])
    _require(d, ("action", "params", "reasoning", "bottleneck"), "manager_decide")
    valid_actions = {"run_research_subtask", "run_compliance_checker", "run_persona_designer",
                     "run_content_brief", "run_metrics_analysis", "run_go_kill_gate",
                     "run_growth_audit", "run_content_audit", "run_sales_audit",
                     "run_content_generation", "run_content_qa",
                     "request_approval", "advance_phase", "no_action_needed"}
    if d["action"] not in valid_actions:
        raise StageValidationError(f"manager_decide: unknown action {d['action']!r}")
    if d["action"] == "advance_phase" and d.get("params", {}).get("to_phase") not in valid_phases:
        raise StageValidationError(
            f"manager_decide: advance_phase to unknown phase {d.get('params', {}).get('to_phase')!r}")
    if d["action"] == "no_action_needed":
        # A no-op with no stated evaluation trigger is exactly the silent
        # stagnation this schema exists to prevent -- fail loudly (visible
        # as a real error in the tick log) rather than let it slide through.
        _require(d, ("next_evaluation_at", "awaiting"), "manager_decide (no_action_needed)")
    d["_cost_usd"] = r.get("cost_usd")
    return d


def research_subtask(question: str) -> dict:
    prompt = prompts.RESEARCH_SUBTASK.format(question=question)
    r = claude_call(prompt, tools=["WebSearch", "WebFetch"], timeout=240)
    d = extract_json(r["text"])
    _require(d, ("question", "findings", "sources", "confidence"), "research_subtask")

    qa_prompt = prompts.RESEARCH_QA.format(question=question, answer_json=json.dumps(d))
    qa_r = claude_call(qa_prompt, tools=None)
    qa = extract_json(qa_r["text"])
    _require(qa, ("verdict", "reasons"), "research_qa")

    d["qa_verdict"] = qa["verdict"]
    d["qa_reasons"] = qa["reasons"]
    d["_cost_usd"] = (r.get("cost_usd") or 0) + (qa_r.get("cost_usd") or 0)
    return d


def compliance_checker(platform: str, action_to_check: str) -> dict:
    prompt = prompts.COMPLIANCE_CHECKER.format(platform=platform, action_to_check=action_to_check)
    r = claude_call(prompt, tools=["WebSearch", "WebFetch"], timeout=240)
    d = extract_json(r["text"])
    _require(d, ("platform", "action_checked", "verdict", "constraints",
                "redesign_needed", "redesign_suggestion"), "compliance_checker")
    if d["verdict"] not in ("compliant", "not_compliant", "unknown_needs_more_research"):
        raise StageValidationError(f"compliance_checker: bad verdict {d['verdict']!r}")
    d["_cost_usd"] = r.get("cost_usd")
    return d


def persona_designer(research_context: str) -> dict:
    prompt = prompts.PERSONA_DESIGNER.format(research_context=research_context)
    r = claude_call(prompt, tools=None, timeout=180)
    d = extract_json(r["text"])
    _require(d, ("name", "niche", "visual_style", "visual_description",
                "voice_and_personality", "platform_fit_notes",
                "disclosure_statement"), "persona_designer")
    d["_cost_usd"] = r.get("cost_usd")
    return d


_CONTENT_OBJECTIVES = ("REACH", "ENGAGEMENT", "WORLD_BUILDING", "CONVERSION",
                       "RETENTION_MONETIZATION")
# Deterministic ceiling for a revision batch -- enforced here, never left to
# the model's own restraint, even though the prompt already asks for 2-6.
MAX_CONTENT_BRIEF_REVISION_ITEMS = 8



# Story carousel (2026-09-21): one more format inside the existing brief ->
# generate -> QA pipeline. These deterministic checks run on top of the
# model's own judgement; they can only FAIL a carousel, never pass one.
STORY_CAROUSEL = "story_carousel"
CAROUSEL_MIN_SLIDES, CAROUSEL_MAX_SLIDES = 4, 7
CAROUSEL_MAX_WORDS = 12
CAROUSEL_ROLES = ("HOOK", "CONTEXT", "ESCALATION", "PROGRESSION", "PAYOFF")
_CAROUSEL_FIELDS = ("story_thread", "content_objective", "commercial_hypothesis",
                    "target_platform", "final_interaction", "open_loop", "slides")
_SLIDE_FIELDS = ("role", "beat", "composition", "text_overlay", "continuity")
_ENGAGEMENT_BAIT = re.compile(
    r"comment below|comment for|like (and|&) (share|follow)|follow for (more|part)|"
    r"smash (that|the)|for engagement|part 2 in (the )?comments|tag a friend", re.I)
_DISCLAIMER_TEXT = re.compile(r"\bai[- ]generated\b|not a real person|fictional character|synthetic art", re.I)

# Public top-of-funnel guards (founder 2026-09-23). Public Instagram/TikTok
# carousels sell personality, mystery and story -- not glamour. These are
# deterministic backstops for the creative direction in prompts.py, so a brief
# cannot drift back to cinematic-fantasy or lingerie-adjacent framing just
# because a generation model or a prompt found it easier.
PUBLIC_PLATFORMS = ("instagram", "tiktok")
_CINEMATIC_FRAMING = re.compile(
    r"cinematic|movie still|film still|studio (campaign|lighting|shoot)|editorial shoot|"
    r"fantasy poster|key ?visual|splash[- ]art|magazine cover|dramatic volumetric|god rays", re.I)
_LINGERIE_WARDROBE = re.compile(
    r"lingerie|negligee|bra\b|panties|thong|underwear[- ]only|topless|nude|cleavage[- ]forward|"
    r"barely[- ]covered|see[- ]through", re.I)
_NARRATOR_VOICE = re.compile(
    r"\b(she|her|nyx) (?:slowly |quietly |carefully )?(?:walks|walked|stands|stood|gazes|gazed|"
    r"reaches|reached|wonders|wondered)\b|little did|unbeknownst|in that moment", re.I)


# A carousel costs one paid image PER SLIDE, so "generate it because it
# exists" is the single easiest way to burn the ceiling. Paid generation of a
# carousel therefore requires the founder's explicit per-item approval; the
# default route is carousel_handoff (no incremental cost).
CAROUSEL_PAID_APPROVAL_FIELD = "paid_generation_approved_by_founder"


def is_story_carousel(item: dict) -> bool:
    return item.get("format_variant") == STORY_CAROUSEL


def carousel_paid_generation_approved(item: dict) -> bool:
    return bool(item.get(CAROUSEL_PAID_APPROVAL_FIELD))


def paid_generation_approved(item: dict) -> bool:
    """Founder 2026-09-22: paid generation is NEVER automatic -- not as a
    fallback, not because a zero-cost route was unavailable or inconvenient.
    It needs the founder's explicit approval ON THIS ITEM, whatever the format
    (same field the carousel rule already used, now venture-wide)."""
    return bool(item.get(CAROUSEL_PAID_APPROVAL_FIELD))


def story_carousel_problems(item: dict) -> list[str]:
    """Structural problems with a story_carousel brief; empty list = structurally sound."""
    probs = [f"missing {k}" for k in _CAROUSEL_FIELDS
             if k != "story_thread" and not item.get(k)]   # story_thread may be "" (standalone)
    slides = item.get("slides")
    if not isinstance(slides, list):
        return probs + ["slides must be a list"]
    if not CAROUSEL_MIN_SLIDES <= len(slides) <= CAROUSEL_MAX_SLIDES:
        probs.append(f"{len(slides)} slides; need {CAROUSEL_MIN_SLIDES}-{CAROUSEL_MAX_SLIDES}")
    for i, s in enumerate(slides):
        if not isinstance(s, dict):
            probs.append(f"slide {i + 1} is not an object")
            continue
        for k in _SLIDE_FIELDS:
            if k not in s or (k != "text_overlay" and not str(s.get(k) or "").strip()):
                probs.append(f"slide {i + 1} missing {k}")
        if s.get("role") and s["role"] not in CAROUSEL_ROLES:
            probs.append(f"slide {i + 1} bad role {s['role']!r}")
        words = len(str(s.get("text_overlay") or "").split())
        if words > CAROUSEL_MAX_WORDS:
            probs.append(f"slide {i + 1} text has {words} words (max {CAROUSEL_MAX_WORDS})")
        if _DISCLAIMER_TEXT.search(str(s.get("text_overlay") or "")):
            probs.append(f"slide {i + 1} text is AI/disclaimer copy, not story")
    dicts = [s for s in slides if isinstance(s, dict)]
    if dicts and dicts[0].get("role") != "HOOK":
        probs.append("slide 1 must be the HOOK")
    if dicts and dicts[-1].get("role") != "PAYOFF":
        probs.append("last slide must be the PAYOFF")
    comps = [" ".join(str(s.get("composition") or "").lower().split()) for s in dicts]
    if len(set(comps)) != len(comps):
        probs.append("two or more slides share the same composition (visually repetitive)")
    beats = [" ".join(str(s.get("beat") or "").lower().split()) for s in dicts]
    if len(set(beats)) != len(beats):
        probs.append("two or more slides repeat the same beat (story does not progress)")
    if _ENGAGEMENT_BAIT.search(str(item.get("final_interaction") or "")):
        probs.append("final interaction is generic engagement bait")
    probs += public_carousel_problems(item)
    return probs


def public_carousel_problems(item: dict) -> list[str]:
    """Top-of-funnel guards for a PUBLIC carousel (founder 2026-09-23). Empty
    for Fanvue and other non-public targets, which may escalate sensuality and
    use a more produced look."""
    if item.get("target_platform") not in PUBLIC_PLATFORMS:
        return []
    slides = [s for s in (item.get("slides") or []) if isinstance(s, dict)]
    if not slides:
        return []
    probs = []
    cinematic = [i + 1 for i, s in enumerate(slides)
                 if _CINEMATIC_FRAMING.search(f"{s.get('composition', '')} {s.get('beat', '')}")]
    # ~10% of PUBLIC output may be a cinematic hero moment, so one slide in a
    # five-slide story is fine -- every slide reading that way is not.
    if len(cinematic) > 1:
        probs.append(f"slides {cinematic} are all cinematic/studio framing -- public carousels "
                     f"default to believable social-native photography, not movie stills")
    for i, s in enumerate(slides, start=1):
        blob = f"{s.get('composition', '')} {s.get('beat', '')} {s.get('continuity', '')}"
        if _LINGERIE_WARDROBE.search(blob):
            probs.append(f"slide {i} wardrobe/exposure is lingerie-adjacent -- not public "
                         f"top-of-funnel; that escalation belongs on Fanvue")
        if _NARRATOR_VOICE.search(str(s.get("text_overlay") or "")):
            probs.append(f"slide {i} overlay reads as third-person narration, not how Nyx "
                         f"would actually post")
    with_text = [s for s in slides if str(s.get("text_overlay") or "").strip()]
    if len(with_text) * 2 < len(slides):
        probs.append("most slides carry no overlay line -- the visual beat and its line are one "
                     "creative unit, written together")
    return probs

def _validate_content_brief(d: dict, stage: str, *, max_items: int | None = None) -> None:
    _require(d, ("content_items", "posting_cadence", "notes"), stage)
    if not isinstance(d["content_items"], list) or not d["content_items"]:
        raise StageValidationError(f"{stage}: content_items must be non-empty")
    if max_items is not None and len(d["content_items"]) > max_items:
        raise StageValidationError(
            f"{stage}: content_items has {len(d['content_items'])} item(s), "
            f"exceeds the bounded-batch ceiling of {max_items}")
    for i, item in enumerate(d["content_items"]):
        if item.get("content_objective") not in _CONTENT_OBJECTIVES:
            raise StageValidationError(
                f"{stage}: content_items[{i}] has bad/missing "
                f"content_objective {item.get('content_objective')!r}")
        if is_story_carousel(item):
            probs = story_carousel_problems(item)
            if probs:
                raise StageValidationError(
                    f"{stage}: content_items[{i}] story_carousel invalid: {'; '.join(probs)}")


def content_brief_generator(persona: dict) -> dict:
    prompt = prompts.CONTENT_BRIEF_GENERATOR.format(
        persona_json=json.dumps(persona),
        character_first_principles=prompts.CHARACTER_FIRST_PRINCIPLES)
    r = claude_call(prompt, tools=None, timeout=180)
    d = extract_json(r["text"])
    _validate_content_brief(d, "content_brief_generator")
    d["_cost_usd"] = r.get("cost_usd")
    return d


def content_brief_revision(persona: dict, active_story_threads: list, performance_notes: str,
                           existing_item_count: int) -> dict:
    """Founder-authorized (2026-09-15) standing top-up mechanism: proposes a
    SMALL new batch of content_items to be APPENDED to an existing content
    plan -- never a replacement. This function itself never touches
    v["content_plan"]; the caller (manager._dispatch) owns appending the
    returned items and is what actually preserves history. The model is
    deliberately never shown existing item text (only the count, active
    threads, and any performance notes) so it cannot "helpfully" try to
    restate or renumber prior items -- it can only produce new ones."""
    prompt = prompts.CONTENT_BRIEF_REVISION.format(
        persona_json=json.dumps(persona),
        character_first_principles=prompts.CHARACTER_FIRST_PRINCIPLES,
        active_story_threads_json=json.dumps(active_story_threads),
        performance_notes=performance_notes or "(none recorded yet)",
        existing_item_count=existing_item_count)
    r = claude_call(prompt, tools=None, timeout=180)
    d = extract_json(r["text"])
    _validate_content_brief(d, "content_brief_revision", max_items=MAX_CONTENT_BRIEF_REVISION_ITEMS)
    d["_cost_usd"] = r.get("cost_usd")
    return d


def metrics_analyst(metrics: dict, thresholds: dict) -> dict:
    prompt = prompts.METRICS_ANALYST.format(metrics_json=json.dumps(metrics),
                                            thresholds_json=json.dumps(thresholds))
    r = claude_call(prompt, tools=None)
    d = extract_json(r["text"])
    _require(d, ("h1_status", "h1_meets_success", "h1_meets_kill",
                "h2_status", "h2_meets_operability_target", "summary"), "metrics_analyst")
    d["_cost_usd"] = r.get("cost_usd")
    return d


_SPECIALIST_KEYS = ("findings", "recommendations", "priority_order", "summary")


def growth_manager_audit(live_state_context: str, persona: dict) -> dict:
    prompt = prompts.GROWTH_MANAGER_AUDIT.format(
        live_state_context=live_state_context, persona_json=json.dumps(persona),
        character_first_principles=prompts.CHARACTER_FIRST_PRINCIPLES)
    r = claude_call(prompt, tools=None, timeout=180)
    d = extract_json(r["text"])
    _require(d, _SPECIALIST_KEYS + ("funnel_gaps",), "growth_manager_audit")
    d["_cost_usd"] = r.get("cost_usd")
    return d


def content_director_audit(live_state_context: str, persona: dict, content_plan: dict) -> dict:
    prompt = prompts.CONTENT_DIRECTOR_AUDIT.format(
        live_state_context=live_state_context, persona_json=json.dumps(persona),
        content_plan_json=json.dumps(content_plan),
        character_first_principles=prompts.CHARACTER_FIRST_PRINCIPLES)
    r = claude_call(prompt, tools=None, timeout=180)
    d = extract_json(r["text"])
    _require(d, _SPECIALIST_KEYS + ("continuity_risks",), "content_director_audit")
    d["_cost_usd"] = r.get("cost_usd")
    return d


def sales_manager_audit(live_state_context: str, persona: dict) -> dict:
    prompt = prompts.SALES_MANAGER_AUDIT.format(
        live_state_context=live_state_context, persona_json=json.dumps(persona),
        character_first_principles=prompts.CHARACTER_FIRST_PRINCIPLES)
    r = claude_call(prompt, tools=None, timeout=180)
    d = extract_json(r["text"])
    _require(d, _SPECIALIST_KEYS + ("funnel_blockers",), "sales_manager_audit")
    d["_cost_usd"] = r.get("cost_usd")
    return d


_CHAT_ACTIONS = ("respond", "no_response", "offer", "ppv", "escalate")


def sales_manager_chat_response(fan_history: dict, message_thread: list[dict], persona: dict,
                                disclosure_policy: dict,
                                available_media: list[dict] | None = None) -> dict:
    available_media = available_media or []
    prompt = prompts.SALES_MANAGER_CHAT_RESPONSE.format(
        fan_history_json=json.dumps(fan_history, default=str),
        message_thread_json=json.dumps(message_thread, default=str),
        persona_json=json.dumps(persona), disclosure_policy_json=json.dumps(disclosure_policy),
        available_media_json=json.dumps(available_media))
    r = claude_call(prompt, tools=None, timeout=180)
    d = extract_json(r["text"])
    _require(d, ("intent_classification", "action", "response_text", "reasoning"),
            "sales_manager_chat_response")
    if d["action"] not in _CHAT_ACTIONS:
        raise StageValidationError(f"sales_manager_chat_response: bad action {d['action']!r}")
    if d["action"] in ("respond", "offer", "ppv") and not str(d.get("response_text", "")).strip():
        raise StageValidationError(
            f"sales_manager_chat_response: action {d['action']!r} requires non-empty response_text")
    if d["action"] == "ppv":
        price = d.get("ppv_price_cents")
        if not isinstance(price, int) or price < 300:
            raise StageValidationError(
                f"sales_manager_chat_response: ppv_price_cents must be an int >= 300, got {price!r}")
        media_uuid = str(d.get("ppv_media_uuid") or "").strip()
        available_uuids = {m["uuid"] for m in available_media if m.get("uuid")}
        if not media_uuid or media_uuid not in available_uuids:
            # Never trust the model's own claim of a media uuid -- it must
            # be one we actually handed it in available_media. This is
            # what makes "never fabricate a media UUID" a code-enforced
            # guarantee, not just a prompt instruction.
            raise StageValidationError(
                f"sales_manager_chat_response: action 'ppv' requires a real ppv_media_uuid from "
                f"available_media ({sorted(available_uuids)}), got {media_uuid!r}")
    d["_cost_usd"] = r.get("cost_usd")
    return d


_PROVIDER_CHOICES = ("reuse", "higgsfield", "wan", "skip")
# Providers that charge per generation. Wan's authorised model is
# billing_mode=free_quota_protected (Alibaba free quota, stop-on-exhaust) and
# "reuse"/"skip" cost nothing, so only Higgsfield is paid today.
PAID_PROVIDERS = ("higgsfield",)

# Narrow, evidence-based autonomous-routing policy (2026-09-12, two real
# benchmarks -- see research_log): wan2.7-i2v-2026-04-25 PASSED simple/
# routine motion and FAILED transformation/complex motion. Only THIS model,
# and only for content the Content Director explicitly and unambiguously
# classifies as "simple_routine", is autonomously selectable. No other Wan
# model is authorized yet (no paid Wan usage) -- everything else routes to
# Higgsfield, including any ambiguous case, per explicit founder instruction.
_WAN_AUTONOMOUS_ALLOWED_MODELS = {"wan2.7-i2v-2026-04-25"}
_CONTENT_COMPLEXITY_CHOICES = ("simple_routine", "complex")


def content_director_choose_provider(item: dict, persona: dict, *, business_context: str,
                                     higgsfield_credits_remaining: float,
                                     wan_usd_remaining: float) -> dict:
    """Pure-judgment routing decision -- no tool access (tools=None), no
    spend. Decides reuse/higgsfield/wan/skip; Python (not this LLM) executes
    whichever provider was chosen and hard-enforces its budget. Never trusts
    this stage's own numbers for the actual spend -- the caller recomputes
    Wan's cost deterministically via wan_provider.estimate_cost_usd before
    calling the real API."""
    prompt = prompts.CONTENT_DIRECTOR_CHOOSE_PROVIDER.format(
        item_json=json.dumps(item), persona_json=json.dumps(persona),
        business_context=business_context,
        wan_model_catalog_json=json.dumps(wan_provider.MODEL_SPECS),
        higgsfield_credits_remaining=higgsfield_credits_remaining,
        wan_usd_remaining=wan_usd_remaining)
    r = claude_call(prompt, tools=None, timeout=180)
    d = extract_json(r["text"])
    _require(d, ("provider", "reasoning"), "content_director_choose_provider")
    if d["provider"] not in _PROVIDER_CHOICES:
        raise StageValidationError(
            f"content_director_choose_provider: bad provider {d['provider']!r}")
    if d["provider"] != "skip" and not str(d.get("business_purpose", "")).strip():
        raise StageValidationError(
            "content_director_choose_provider: business_purpose required unless provider is 'skip'")
    if d["provider"] == "reuse" and not str(d.get("asset_ref", "")).strip():
        raise StageValidationError(
            "content_director_choose_provider: provider 'reuse' requires a real asset_ref")
    if d["provider"] in PAID_PROVIDERS and not paid_generation_approved(item):
        # Fail closed at the ROUTING layer too, not only at the generate call:
        # this stage is an LLM judgement, so the ban on auto-selecting a paid
        # provider is enforced here in Python where it cannot be argued with.
        d["provider"], d["rejected_provider"] = "skip", d["provider"]
        d["reasoning"] = (f"{d['rejected_provider']} charges per generation and this item has no "
                          f"{CAROUSEL_PAID_APPROVAL_FIELD} -- skipped instead of spending; "
                          f"original reasoning: {d.get('reasoning', '')}")[:600]
    if d["provider"] == "wan":
        model = d.get("model_used", "")
        if model not in wan_provider.MODEL_SPECS:
            raise StageValidationError(
                f"content_director_choose_provider: model_used {model!r} is not a whitelisted "
                f"Wan model -- refusing to trust a recommendation outside the reviewed catalog")
        if model not in _WAN_AUTONOMOUS_ALLOWED_MODELS:
            raise StageValidationError(
                f"content_director_choose_provider: {model} is not yet authorized for autonomous "
                f"selection (no paid Wan usage yet) -- only {sorted(_WAN_AUTONOMOUS_ALLOWED_MODELS)} "
                f"is/are currently approved")
        complexity = d.get("content_complexity", "")
        if complexity not in _CONTENT_COMPLEXITY_CHOICES:
            raise StageValidationError(
                f"content_director_choose_provider: provider 'wan' requires a real "
                f"content_complexity assessment ('simple_routine' or 'complex'), got {complexity!r}")
        if complexity != "simple_routine":
            raise StageValidationError(
                f"content_director_choose_provider: content_complexity={complexity!r} -- Wan is only "
                f"authorized for 'simple_routine' content; anything complex or ambiguous must route "
                f"to Higgsfield per standing policy")
        if not str(d.get("generation_prompt", "")).strip():
            raise StageValidationError(
                "content_director_choose_provider: provider 'wan' requires generation_prompt")
        if not str(d.get("reference_image_url", "")).strip():
            raise StageValidationError(
                "content_director_choose_provider: provider 'wan' requires reference_image_url")
        resolution = d.get("resolution", "")
        if resolution not in wan_provider.MODEL_SPECS[model]["resolutions"]:
            raise StageValidationError(
                f"content_director_choose_provider: {model} does not support resolution "
                f"{resolution!r}")
        if not isinstance(d.get("duration_seconds"), int) or d["duration_seconds"] <= 0:
            raise StageValidationError(
                f"content_director_choose_provider: bad duration_seconds {d.get('duration_seconds')!r}")
    d["_cost_usd"] = r.get("cost_usd")
    return d


_HIGGSFIELD_PREFIX = "mcp__claude_ai_Higgsfield__"
_HIGGSFIELD_GENERATION_TOOLS = [_HIGGSFIELD_PREFIX + t for t in (
    "show_generations", "generate_video", "generate_image", "jobs_wait",
    "job_display", "models_explore", "balance",
)]
_GENERATION_DECISIONS = ("reused", "generated", "skipped")


def content_director_generate(item: dict, persona: dict, *, business_context: str,
                              max_credits: float) -> dict:
    """A bounded call with real Higgsfield MCP tool access (a fresh `claude
    -p` subprocess reaches the account's connected Higgsfield connector via
    --allowedTools, entirely invisible to the TOP-LEVEL agent's own Bash
    permission gating -- same principle that already makes the nested
    specialist-audit calls permission-safe). Never exceeds max_credits;
    never fabricates a cost/job id/completion it didn't actually observe."""
    if not paid_generation_approved(item):
        # Refused BEFORE the tool call, so no credit can be spent by accident.
        # Widened from carousels to every item (founder 2026-09-22): the paid
        # Higgsfield path must not become the default just because it is
        # convenient, and a zero-cost route being unavailable is never on its
        # own a reason to spend. Zero-incremental-cost routes come first --
        # carousels to carousel_handoff, Wan's free-quota i2v for video.
        raise StageValidationError(
            "content_director_generate: paid generation needs the founder's explicit approval on "
            f"this item ({CAROUSEL_PAID_APPROVAL_FIELD}); zero-incremental-cost routes come first "
            "and a free route failing is never itself a reason to spend credits")
    prompt = prompts.CONTENT_DIRECTOR_GENERATE.format(
        item_json=json.dumps(item), persona_json=json.dumps(persona),
        business_context=business_context, max_credits=max_credits,
        character_first_principles=prompts.CHARACTER_FIRST_PRINCIPLES)
    r = claude_call(prompt, tools=_HIGGSFIELD_GENERATION_TOOLS, timeout=480)
    d = extract_json(r["text"])
    _require(d, ("decision", "reasoning", "asset_ref", "model_used", "cost_credits"),
            "content_director_generate")
    if d["decision"] not in _GENERATION_DECISIONS:
        raise StageValidationError(f"content_director_generate: bad decision {d['decision']!r}")
    if not isinstance(d["cost_credits"], (int, float)) or d["cost_credits"] < 0:
        raise StageValidationError(
            f"content_director_generate: bad cost_credits {d['cost_credits']!r}")
    if d["cost_credits"] > max_credits:
        raise StageValidationError(
            f"content_director_generate: reported cost {d['cost_credits']} exceeds "
            f"max_credits {max_credits} -- refusing to trust/apply this result")
    if d["decision"] in ("reused", "generated") and not d.get("asset_ref"):
        raise StageValidationError(
            f"content_director_generate: decision {d['decision']!r} requires a real asset_ref")
    if d["decision"] in ("reused", "generated") and is_story_carousel(item):
        refs = d.get("asset_refs")
        if not isinstance(refs, list) or len(refs) != len(item.get("slides") or []) or not all(refs):
            raise StageValidationError(
                "content_director_generate: story_carousel needs one asset_ref per slide "
                "(never a partial carousel)")
    d["_cost_usd"] = r.get("cost_usd")
    return d


def content_director_creative_qa(item: dict, persona: dict, recent_published: list[dict]) -> dict:
    """The QA gate between ASSET_GENERATED and QA_PASSED -- catches the
    profile-level 'duplicate-looking content' problem (near-identical
    opening frames/thumbnails across posts) that per-asset-only QA misses.
    No tool access, no spend."""
    prompt = prompts.CONTENT_DIRECTOR_CREATIVE_QA.format(
        item_json=json.dumps(item), persona_json=json.dumps(persona),
        recent_published_json=json.dumps(recent_published),
        character_first_principles=prompts.CHARACTER_FIRST_PRINCIPLES)
    r = claude_call(prompt, tools=None, timeout=180)
    d = extract_json(r["text"])
    _require(d, ("qa_verdict", "character_qa", "technical_qa", "creative_qa",
                "business_qa", "reasoning"), "content_director_creative_qa")
    if d["qa_verdict"] not in ("PASS", "FAIL"):
        raise StageValidationError(
            f"content_director_creative_qa: bad qa_verdict {d['qa_verdict']!r}")
    if is_story_carousel(item):
        probs = story_carousel_problems(item)
        if not str(d.get("carousel_qa") or "").strip():
            probs.append("QA returned no carousel_qa finding")
        if probs:
            d["qa_verdict"] = "FAIL"
            d["carousel_structural_failures"] = probs
    d["_cost_usd"] = r.get("cost_usd")
    return d


def go_kill_gate(h1_status: str, h2_status: str, iterate_count: int) -> dict:
    prompt = prompts.GO_KILL_GATE.format(h1_status=h1_status, h2_status=h2_status,
                                         iterate_count=iterate_count)
    r = claude_call(prompt, tools=None)
    d = extract_json(r["text"])
    _require(d, ("recommendation", "reasoning", "h1_summary", "h2_summary"), "go_kill_gate")
    if d["recommendation"] not in ("VALIDATE", "ITERATE", "KILL"):
        raise StageValidationError(f"go_kill_gate: bad recommendation {d['recommendation']!r}")
    d["_cost_usd"] = r.get("cost_usd")
    return d
