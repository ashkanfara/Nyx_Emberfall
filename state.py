"""venture.json persistence + schema for the PS-05 Venture Manager.

One venture, one state file -- deliberately NOT research-orchestrator's
many-parallel-runs model (see README for why). Everything lives under
state/, fully separate from research-orchestrator's runs/.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:                       # pragma: no cover - POSIX only; no lock on Windows
    fcntl = None

STATE_DIR = Path(__file__).resolve().parent / "state"
VENTURE_PATH = STATE_DIR / "venture.json"
PENDING_APPROVAL_PATH = STATE_DIR / "pending_approval.json"
EXPERIMENTS_DIR = STATE_DIR / "experiments"

PHASES = (
    "RESEARCH_COMPLETE",
    "VALIDATION_PLANNING",
    "PERSONA_DEFINED",
    "READY_FOR_ACCOUNT_SETUP",  # persona + content brief + compliance/feasibility
                                # research all done; blocked only on founder-owned
                                # account creation/KYC before CONTENT_TEST_ACTIVE
    "CONTENT_TEST_ACTIVE",
    "DISTRIBUTION_ACTIVE",
    "MONETIZATION_ACTIVE",
    "MEASURING",
    "DECISION_PENDING",
    "AUTOMATION_FEASIBILITY",
    "SCALE_DECISION",
    "VENTURE_CLOSED",
)

# Founder approval boundary reason codes -- fixed, closed set (see README).
APPROVAL_REASONS = (
    "AUTHORIZATION_REQUIRED",       # A
    "IDENTITY_KYC_REQUIRED",        # B
    "SPEND_REQUIRED",               # C
    "CREDENTIALS_REQUIRED",         # D
    "LEGAL_REPUTATIONAL_DECISION",  # E
    "MAJOR_GO_KILL_DECISION",       # F
    "CANNOT_SAFELY_DELEGATE",       # G
)

DECISIONS = ("TEST", "VALIDATED", "ITERATE", "KILL")

OPERABILITY_TARGET_MIN_MINUTES_PER_WEEK = 30
OPERABILITY_TARGET_MAX_MINUTES_PER_WEEK = 60
# KYC/credentials time is exempted from the operability trend (temporary
# setup cost, not recurring operational load) -- see those categories below.
_EXEMPT_INTERVENTION_CATEGORIES = frozenset({"kyc", "credentials"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, obj) -> None:
    """Atomic: a reader (or a crash) never sees a half-written state file --
    the new bytes land in a sibling temp file and replace the old one in one
    rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read_json(path: Path, default=None):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def new_venture(*, hypothesis: dict, thresholds: dict, evidence_gaps: list[str],
                source: str) -> dict:
    """The initial venture.json shape. Called once by seed_from_ro.py."""
    return {
        "project_id": "PS-05",
        "phase": "RESEARCH_COMPLETE",
        "created_at": _now(),
        "updated_at": _now(),
        "source": source,
        "hypothesis": hypothesis,
        "hypothesis_history": [],
        "thresholds": thresholds,
        "threshold_history": [],
        "evidence_gaps": evidence_gaps,
        # Milestone names the Manager has already completed -- MANAGER_DECIDE
        # is instructed to check this before proposing a stage again, so a
        # non-idempotent stage (persona/content design) is never silently
        # redone. Distinct from `phase`: a phase can span several milestones.
        "completed_milestones": [],
        # Which distribution channels are in scope for THIS validation round
        # vs. deliberately deferred (with why) -- e.g. Reddit deferred at
        # founder direction pending evidence justifying the extra manual
        # workload its non-automatable posting would add (H2 concern).
        "distribution_channels": {"v1_required": [], "deferred": []},
        "metrics": {
            "h1_market": {
                "raw_subscribers": 0, "gross_revenue_usd": 0.0,
                "spend_usd": 0.0, "cac_usd": None,
                "moderation_strikes": 0, "days_elapsed": 0,
            },
            "h2_operability": {
                # Recurring operational load -- the number the 30-60 min/week
                # target and trend actually govern. Never includes one-time
                # setup (kyc/credentials) minutes.
                "operational_minutes_this_week": 0,
                "operational_minutes_cumulative": 0,
                # One-time setup/authorization overhead (kyc, credentials) --
                # tracked separately by design: it is real founder time and
                # is recorded, but it is not "the recurring workload" H2 is
                # actually testing, so it must never be added to the two
                # fields above or silently blended into the same number.
                "setup_overhead_minutes_cumulative": 0,
                "interventions": [],
                "could_not_delegate_log": [],
                "trend_vs_target": "unknown",
            },
        },
        "decision": "TEST",
        "iterate_count": 0,
        "next_action": None,
        "current_stage_kind": None,
        # Founder-set spend ceiling for autonomous generation work -- the
        # Manager may spend up to session_limit without asking again;
        # anything beyond it is a fresh SPEND_REQUIRED boundary.
        "credit_budget": {"session_limit": 0, "spent_this_session": 0, "spent_total": 0,
                          "purchases_allowed": False},
        # Separate real-USD guardrail for Wan (Alibaba Cloud Model Studio) --
        # kept apart from credit_budget deliberately: Higgsfield credits are
        # an already-paid monthly subscription allowance, Wan is incremental
        # pay-as-you-go spend on a card. Blending them into one number would
        # misrepresent which one is genuinely new money. Defaults to 0 --
        # real spend is never authorized until the founder sets a ceiling.
        "wan_budget": {"session_limit_usd": 0, "spent_this_session_usd": 0,
                      "spent_total_usd": 0, "failed_generation_count": 0,
                      "failed_generation_cost_usd": 0},
        # Actions blocked on a genuine founder-only step (consent/2FA/KYC/
        # credentials/spend-beyond-ceiling/legal/GO-KILL) that do NOT halt
        # the rest of the autonomous loop -- see queue_action/resolve_queued.
        "queued_actions": [],
        # Manager-of-specialists layer: Growth/Marketing, Content Director,
        # Sales/Relationship each return structured audits/recommendations
        # HERE -- they never talk to the founder or decide strategy directly.
        # The Venture Manager (this state machine + the human/agent driving
        # it) remains the single orchestrator and arbitrates between them.
        "specialists": {
            "growth": {"last_run_at": None, "findings": [], "recommendations": [],
                      "priority_order": [], "summary": ""},
            "content": {"last_run_at": None, "findings": [], "recommendations": [],
                       "priority_order": [], "summary": ""},
            "sales": {"last_run_at": None, "findings": [], "recommendations": [],
                     "priority_order": [], "summary": ""},
        },
        # What the Venture Manager is currently testing -- surfaced in the UI
        # so the founder can see the single active experiment at a glance.
        # Deprecated in favor of `experiments` (a persistent list) -- kept
        # only so old UI reads don't break; new code should use experiments.
        "active_experiment": None,
        # Persistent experiment framework: every test the venture runs gets
        # one entry here (hypothesis/variable/control/assets/channels/cost/
        # founder_minutes/observation_window/result/confidence/next_decision)
        # -- see new_experiment(). This replaces one-off ad-hoc tracking so
        # the Venture Manager can resume/decide on due experiments without
        # re-deriving context each time.
        "experiments": [],
    }


EXPERIMENT_STATUSES = ("running", "measuring", "concluded")


def new_experiment(*, experiment_id: str, hypothesis: str, variable: str, control: str,
                   assets: list[str], channels: list[str], observation_window_end: str,
                   success_metric: str, guardrails: str = "", cost_credits: float = 0.0,
                   founder_minutes: float = 0.0) -> dict:
    """One persistent experiment record. `variable` names the single thing
    being changed vs `control` (the baseline it's compared against) -- the
    schema itself enforces naming a control, not just a variant, so a future
    experiment can't be logged without stating what it's isolated against."""
    return {
        "id": experiment_id, "hypothesis": hypothesis, "variable": variable,
        "control": control, "assets": assets, "channels": channels,
        "published_at": _now(), "observation_window_end": observation_window_end,
        "success_metric": success_metric, "guardrails": guardrails,
        "cost_credits": cost_credits, "founder_minutes": founder_minutes,
        "status": "running", "measurements": [], "result": None,
        "confidence": None, "next_decision": None,
    }


def add_experiment(v: dict, experiment: dict) -> None:
    v.setdefault("experiments", []).append(experiment)


def due_experiments(v: dict, *, now_iso: str) -> list[dict]:
    """Experiments whose observation window has passed and are not yet
    concluded -- what the scheduler should act on this tick."""
    return [e for e in v.get("experiments", [])
           if e["status"] != "concluded" and e.get("observation_window_end")
           and e["observation_window_end"] <= now_iso]


def record_experiment_measurement(v: dict, experiment_id: str, measurement: dict) -> None:
    for e in v.get("experiments", []):
        if e["id"] == experiment_id:
            e.setdefault("measurements", []).append({"ts": _now(), **measurement})
            e["status"] = "measuring"
            return
    raise KeyError(f"no experiment {experiment_id!r}")


EXPERIMENT_RESULTS = ("WON", "LOST", "INCONCLUSIVE", "INVALIDATED", "SUPERSEDED")


def conclude_experiment(v: dict, experiment_id: str, *, result: str, confidence: str,
                        next_decision: str) -> None:
    """result must be one of EXPERIMENT_RESULTS -- an experiment may not sit
    concluded with a free-text/ambiguous outcome; every concluded experiment
    has an unambiguous, dashboard-legible result."""
    assert result in EXPERIMENT_RESULTS, f"unknown experiment result {result!r}"
    for e in v.get("experiments", []):
        if e["id"] == experiment_id:
            e["status"] = "concluded"
            e["result"] = result
            e["confidence"] = confidence
            e["next_decision"] = next_decision
            return
    raise KeyError(f"no experiment {experiment_id!r}")


# --- concurrent access to venture.json ----------------------------------------
# 2026-09-25 incident: the hourly tick loaded venture.json at 05:09:24Z, a
# founder slide grant was written by a CLI run at 05:09:37Z, and the tick saved
# its stale in-memory copy at 05:09:45Z -- erasing the grant and its ceiling
# raise. Nothing serialized the two read-modify-write cycles and nothing noticed
# that one of them was writing over a newer file.
#
# Two mechanisms, both needed:
#   venture_lock()  serializes a whole load->mutate->save span across processes,
#                   so a short CLI write and a tick write can never interleave.
#   state_revision  a counter bumped on every save. A save whose dict was loaded
#                   at an older revision is REFUSED (StaleWriteError) instead of
#                   silently winning -- the long-running tick cannot hold the
#                   lock for its whole run, so it needs to be told, not blocked.
VENTURE_LOCK_TIMEOUT_S = 30
_LOCK_POLL_S = 0.05
_lock_reentrant = threading.RLock()
_lock_state = {"depth": 0, "fd": None}


class StaleWriteError(RuntimeError):
    """venture.json changed on disk after this copy of it was loaded, so saving
    it would erase somebody else's newer write. Re-load, re-apply, save again --
    or, for a span that must be all-or-nothing, do the whole read-modify-write
    inside venture_lock()."""


def venture_lock_path() -> Path:
    """Beside venture.json, and derived at call time so tests that repoint
    VENTURE_PATH at a temp directory lock there too."""
    return VENTURE_PATH.with_name(VENTURE_PATH.name + ".lock")


@contextlib.contextmanager
def venture_lock(timeout: float = VENTURE_LOCK_TIMEOUT_S):
    """Serialize one venture.json read-modify-write against every other process
    doing the same (the scheduled tick, any ps05_ops CLI run, the dashboard).

    Re-entrant within a process, so a save() nested inside a locked span cannot
    deadlock against itself, and time-bounded, so a stale lock file can never
    wedge the scheduled runner -- it raises TimeoutError and the caller reports
    the refusal. Hold it only for the write span; never across an LLM call."""
    with _lock_reentrant:
        _lock_state["depth"] += 1
        outermost = _lock_state["depth"] == 1
        try:
            if outermost and fcntl is not None:
                path = venture_lock_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
                _lock_state["fd"] = fd
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            os.close(fd)
                            _lock_state["fd"] = None
                            raise TimeoutError(
                                f"venture.json stayed locked for {timeout}s -- another PS-05 "
                                f"write is still running; nothing was written") from None
                        time.sleep(_LOCK_POLL_S)
            yield
        finally:
            _lock_state["depth"] -= 1
            if _lock_state["depth"] == 0 and _lock_state["fd"] is not None:
                fd, _lock_state["fd"] = _lock_state["fd"], None
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


def load() -> dict | None:
    with venture_lock():
        v = _read_json(VENTURE_PATH)
    if isinstance(v, dict):
        v.setdefault("state_revision", 0)
    return v


def save(v: dict, *, force: bool = False) -> None:
    """Persist venture.json, refusing to overwrite a copy NEWER than the one
    this dict was loaded from. `force` is the deliberate override (seeding a
    fresh venture, or a repair that has already reconciled the newer write)."""
    with venture_lock():
        on_disk = _read_json(VENTURE_PATH) or {}
        disk_revision = int(on_disk.get("state_revision") or 0)
        loaded_at = v.get("state_revision")
        if not force and loaded_at is not None and int(loaded_at) != disk_revision:
            raise StaleWriteError(
                f"venture.json is at revision {disk_revision} but this copy was loaded at "
                f"revision {loaded_at} -- saving it would erase the newer write; re-load and "
                f"re-apply instead")
        v["state_revision"] = disk_revision + 1
        v["updated_at"] = _now()
        _write_json(VENTURE_PATH, v)


def update(mutate):
    """The safe read-modify-write in one call: load and save under a single
    lock, so nothing can land between them. Returns the saved venture dict."""
    with venture_lock():
        v = load()
        if v is None:
            raise StaleWriteError("no venture.json to update -- run seed_from_ro.py first")
        mutate(v)
        save(v)
        return v


def _read_pending_approvals() -> list[dict]:
    """Internal. The file may still be on disk in the pre-2026-09-16 shape
    (a bare single approval object, no list wrapper) -- read either shape
    transparently so existing production data upgrades in place on the next
    write, with no migration step needed."""
    raw = _read_json(PENDING_APPROVAL_PATH)
    if raw is None:
        return []
    if isinstance(raw, dict) and "items" in raw:
        return raw["items"]
    if isinstance(raw, dict):
        return [raw]  # legacy single-object shape
    return []


def _write_pending_approvals(items: list[dict]) -> None:
    if not items:
        if PENDING_APPROVAL_PATH.is_file():
            PENDING_APPROVAL_PATH.unlink()
        return
    _write_json(PENDING_APPROVAL_PATH, {"items": items})


def load_pending_approval() -> dict | None:
    """The single OLDEST pending approval -- unchanged contract for every
    existing caller (dashboard/server/the manager's own tick-loop gate)
    that only ever needed "is something blocking, and what's the most
    urgent one". See load_pending_approvals() for the full queue (2026-09-16
    fix: more than one genuinely independent founder approval can now
    coexist, so a stuck one on one platform never silently blocks an
    unrelated request on another)."""
    items = load_pending_approvals()
    return items[0] if items else None


def approval_is_resolved(entry: dict) -> bool:
    """An entry stays in pending_approval.json forever (audit history) once
    reconcile_pending_approvals() marks it resolved -- it just stops being
    an ACTIVE blocker everywhere the queue is consumed."""
    return bool((entry.get("resolution") or {}).get("status"))


def load_pending_approvals() -> list[dict]:
    """The ACTIVE pending-approval queue, oldest first (resolved/superseded
    entries are kept on disk for history but excluded here)."""
    return [it for it in _read_pending_approvals() if not approval_is_resolved(it)]


# Founder authorization 2026-09-21: on Nyx's OWN accounts (Instagram
# @nyx.emberfall, TikTok @nyx.emberfall1, Metricool brand 6988018) reputation
# exposure is not a founder concern, so publishing and scheduling are standing-
# authorized and must never be queued as a founder approval on publish/schedule
# grounds ALONE. Everything in _RESERVED_FOUNDER_TOPICS is untouched by that
# authorization: an ask that also involves one of those is still a real founder
# boundary and is filed normally. Deliberately fails OPEN (files the approval)
# whenever the ask is not unambiguously publish-only.
# PS-05's publish paths can only reach Nyx's own destinations (nyx_instagram.py
# hard-codes brand 6988018 / @nyx.emberfall / @nyx.emberfall1), so naming one of
# those surfaces is what makes an ask recognisably Nyx-owned. A personal handle
# appearing anywhere in the ask is a reserved topic below, never authorized.
NYX_OWNED_SURFACES = ("nyx", "instagram", "tiktok", "metricool", "6988018")
_PUBLISH_TOPICS = ("publish", "post to", "posting", "schedul", "upload", "go live", "distribut")
_RESERVED_FOUNDER_TOPICS = (
    "credit", "spend", "spent", "budget", "ceiling", "top-up", "top up", "purchase", "pay", "paid",
    "usd", "$", "invoice", "subscription",                       # money / paid generation
    "dm", "message", "reply", "chat", "conversation", "fan ",    # a real person
    "consent", "hold", "approval mechanism", "safety", "guardrail", "boundary", "consent gate",
    "routing", "route to", "tiktok_route", "reroute", "integration",
    "credential", "token", "oauth", "permission", "scope",
    "age lock", "underage", "minor", "identity lock", "persona lock", "disclosure",
    "ashkanfaraa", "ashtonfaraa", "ashkan.properties",           # never a Nyx-owned account
)


def publish_only_approval_is_authorized(*, reason: str, what: str, why: str = "",
                                        founder_action: str = "") -> bool:
    """True when a would-be founder approval is asking for nothing except
    permission to publish/schedule Nyx's own content on a Nyx-owned account --
    which the founder already granted. Callers refuse to file such an approval
    and act instead; see manager._dispatch's request_approval branch."""
    if reason not in ("CANNOT_SAFELY_DELEGATE", "AUTHORIZATION_REQUIRED"):
        return False                              # spend/KYC/credentials/legal/go-kill are never publish-only
    text = " ".join((what, why, founder_action)).lower()
    if not any(t in text for t in _PUBLISH_TOPICS):
        return False
    if not any(s in text for s in NYX_OWNED_SURFACES):
        return False                              # cannot confirm it is a Nyx-owned destination
    return not any(t in text for t in _RESERVED_FOUNDER_TOPICS)


def find_pending_approval(*, reason: str, what_prefix: str) -> dict | None:
    """Duplicate-detection: an approval for the SAME underlying need (same
    reason + same leading description) already exists. Deliberately never
    matches across different reasons/resources -- treating any two pending
    approvals as "the same" regardless of what they're actually for is
    exactly the bug this exists to prevent (an Instagram tooling approval
    is not a duplicate of an unrelated TikTok consent approval merely
    because both happen to be pending at once)."""
    prefix = what_prefix[:60]
    for item in load_pending_approvals():
        if item.get("reason") == reason and item.get("what", "")[:60] == prefix:
            return item
    return None


def write_pending_approval(*, reason: str, what: str, why: str,
                           founder_action: str, estimated_minutes: int) -> dict:
    """Appends a new, independent pending approval -- never overwrites an
    existing, unrelated one. Callers are responsible for their own duplicate
    check (see find_pending_approval) before calling this, exactly as
    before; this function itself only guarantees it never destroys a
    different pending item to make room for a new one."""
    assert reason in APPROVAL_REASONS, f"unknown approval reason {reason!r}"
    entry = {
        "id": uuid.uuid4().hex[:8],
        "created_at": _now(), "reason": reason, "what": what, "why": why,
        "founder_action": founder_action, "estimated_minutes": estimated_minutes,
    }
    items = _read_pending_approvals()
    items.append(entry)
    _write_pending_approvals(items)
    return entry


def clear_pending_approval(approval_id: str | None = None) -> None:
    """No id given: clears the FRONT (oldest) approval -- preserves the
    exact single-item-era behavior (resolve what's shown, it's gone) when
    there is only ever one pending. With multiple pending, resolving the
    front one reveals the next rather than discarding the whole queue."""
    items = _read_pending_approvals()
    if not items:
        return
    if approval_id is None:
        # Front of the ACTIVE queue (same entry load_pending_approval()
        # shows); resolved history entries are never what "front" means.
        idx = next((i for i, it in enumerate(items) if not approval_is_resolved(it)), None)
        if idx is None:
            return
        items = items[:idx] + items[idx + 1:]
    else:
        items = [it for it in items if it.get("id") != approval_id]
    _write_pending_approvals(items)


def record_intervention(v: dict, *, minutes: float, reason: str, category: str) -> None:
    """Append one founder-time entry to H2 tracking and recompute the trend.
    category one of: kyc, credentials, spend, legal, go_kill, could_not_delegate.

    kyc/credentials minutes are one-time setup/authorization overhead: they
    are recorded (setup_overhead_minutes_cumulative) but NEVER added to the
    operational_minutes fields the 30-60 min/week target and trend are
    actually about -- conflating the two would make a one-time account-setup
    session look like ongoing operational load, which is exactly the
    distinction H2 exists to protect."""
    op = v["metrics"]["h2_operability"]
    op["interventions"].append({"ts": _now(), "minutes": minutes,
                                "reason": reason, "category": category})
    if category in _EXEMPT_INTERVENTION_CATEGORIES:
        op["setup_overhead_minutes_cumulative"] = round(
            op["setup_overhead_minutes_cumulative"] + minutes, 1)
    else:
        op["operational_minutes_cumulative"] = round(
            op["operational_minutes_cumulative"] + minutes, 1)
        op["operational_minutes_this_week"] = round(
            op["operational_minutes_this_week"] + minutes, 1)
        if op["operational_minutes_this_week"] <= OPERABILITY_TARGET_MAX_MINUTES_PER_WEEK:
            op["trend_vs_target"] = "on_track"
        else:
            op["trend_vs_target"] = "above"


def record_could_not_delegate(v: dict, *, task: str, why_not_delegated: str,
                              tier_attempted: str) -> None:
    v["metrics"]["h2_operability"]["could_not_delegate_log"].append({
        "date": _now(), "task": task, "why_not_delegated": why_not_delegated,
        "tier_attempted": tier_attempted,
    })


def set_credit_budget(v: dict, *, session_limit: float, purchases_allowed: bool = False) -> None:
    v["credit_budget"]["session_limit"] = session_limit
    v["credit_budget"]["purchases_allowed"] = purchases_allowed


def credits_remaining(v: dict) -> float:
    b = v["credit_budget"]
    return round(b["session_limit"] - b["spent_this_session"], 2)


def record_credit_spend(v: dict, *, credits: float, what: str) -> None:
    b = v["credit_budget"]
    b["spent_this_session"] = round(b["spent_this_session"] + credits, 2)
    b["spent_total"] = round(b["spent_total"] + credits, 2)


_WAN_BUDGET_DEFAULTS = {"session_limit_usd": 0, "spent_this_session_usd": 0,
                        "spent_total_usd": 0, "failed_generation_count": 0,
                        "failed_generation_cost_usd": 0, "free_quota_exhausted_count": 0}


def set_wan_budget(v: dict, *, session_limit_usd: float) -> None:
    """Founder-set real-USD ceiling for Wan generation. Defaults to 0 (no
    spend authorized) on any venture.json predating this field."""
    v.setdefault("wan_budget", dict(_WAN_BUDGET_DEFAULTS))["session_limit_usd"] = session_limit_usd


def wan_usd_remaining(v: dict) -> float:
    b = v.setdefault("wan_budget", dict(_WAN_BUDGET_DEFAULTS))
    return round(b["session_limit_usd"] - b["spent_this_session_usd"], 4)


def record_wan_spend(v: dict, *, usd: float, what: str) -> None:
    """Only ever call this for a CONFIRMED SUCCEEDED generation -- Alibaba's
    own billing rule (and this system's mirror of it) is that a failed job
    costs nothing, so failures go through record_wan_generation_failure
    instead, never here."""
    b = v.setdefault("wan_budget", dict(_WAN_BUDGET_DEFAULTS))
    b["spent_this_session_usd"] = round(b["spent_this_session_usd"] + usd, 4)
    b["spent_total_usd"] = round(b["spent_total_usd"] + usd, 4)


def record_wan_generation_failure(v: dict, *, what: str, reason: str = "generation_error") -> None:
    """Tracked separately from spend per founder instruction -- failed
    generations cost $0 (Alibaba does not bill them) but are still a
    reliability signal worth counting on their own. reason="free_quota_exhausted"
    is counted separately too -- the CEO/dashboard needs to distinguish
    "the account's free quota ran out" (an account-level condition, not a
    bug) from a generic generation error, without digging through raw
    activity-log text."""
    b = v.setdefault("wan_budget", dict(_WAN_BUDGET_DEFAULTS))
    b["failed_generation_count"] = b["failed_generation_count"] + 1
    if reason == "free_quota_exhausted":
        b["free_quota_exhausted_count"] = b.get("free_quota_exhausted_count", 0) + 1


# Storefront readiness (commercial-acceleration rules, 2026-09-13) -- a small
# cache of what a live audit last found, so dashboard_status.storefront_readiness
# doesn't need a fresh API call to reason about it. Updated by whoever last
# actually checked the live platform (a founder-directed audit, a future
# growth-audit tick) -- never guessed. NOT_AVAILABLE (None) beats a stale
# assumption when nothing has checked yet.
_STOREFRONT_DEFAULTS = {
    "bio_set": None, "avatar_set": None, "banner_distinct": None,
    "content_depth_items": None, "subscription_price_set": None,
    "last_checked_at": None, "last_checked_via": None,
}


def set_storefront_state(v: dict, *, checked_via: str, **fields) -> dict:
    """Record what a real check actually found. Only pass the fields that
    check genuinely observed -- omit the rest rather than guessing."""
    s = v.setdefault("storefront", dict(_STOREFRONT_DEFAULTS))
    for key, value in fields.items():
        if key not in _STOREFRONT_DEFAULTS:
            raise ValueError(f"unknown storefront field {key!r}")
        s[key] = value
    s["last_checked_at"] = _now()
    s["last_checked_via"] = checked_via
    return s


_MONETIZATION_MILESTONES = (
    "first_paid_conversion", "first_renewal", "first_ppv_purchase",
    "revenue_10", "revenue_50", "revenue_100", "revenue_500_30day",
)


def start_monetization_clock(v: dict) -> bool:
    """Idempotent -- only sets started_at the first time this is called
    after storefront readiness is reached. Returns True if this call
    actually started it (False if already running)."""
    clock = v.setdefault("monetization_clock", {
        "started_at": None, "milestones": {m: None for m in _MONETIZATION_MILESTONES},
    })
    if clock["started_at"] is not None:
        return False
    clock["started_at"] = _now()
    return True


def record_monetization_milestone(v: dict, milestone: str, *, evidence: str) -> None:
    """Only call this for CONFIRMED real platform revenue/conversion events
    -- never for a trial signup alone, and never for spam/recruitment
    conversations. evidence should cite the real observation (e.g. a
    Fanvue earnings-summary delta actually seen)."""
    if milestone not in _MONETIZATION_MILESTONES:
        raise ValueError(f"unknown monetization milestone {milestone!r}")
    clock = v.setdefault("monetization_clock", {
        "started_at": None, "milestones": {m: None for m in _MONETIZATION_MILESTONES},
    })
    if clock["milestones"].get(milestone) is not None:
        return  # already recorded -- first occurrence wins, never overwritten
    clock["milestones"][milestone] = {"at": _now(), "evidence": evidence}


QUEUED_ACTION_URGENCIES = ("blocking_now", "non_urgent")


def queue_action(v: dict, *, kind: str, boundary: str, what: str, why: str,
                 founder_action: str, payload: dict | None = None,
                 urgency: str = "blocking_now", estimated_minutes: float | None = None) -> dict:
    """A founder-only-blocked action that does NOT halt the rest of the
    autonomous loop -- e.g. a TikTok post ready to go except for its
    required per-post human consent click. boundary is one of the closed
    APPROVAL_REASONS categories (reused here, not a separate vocabulary).
    urgency distinguishes something actively blocking a channel/decision
    right now (blocking_now) from something real but batchable, not worth
    interrupting the founder for on its own (non_urgent) -- resolved items
    are neither: see queued_action_status(). estimated_minutes is optional
    (omit rather than guess) -- shown on the dashboard so the founder can
    judge urgency/effort at a glance."""
    assert boundary in APPROVAL_REASONS, f"unknown boundary {boundary!r}"
    assert urgency in QUEUED_ACTION_URGENCIES, f"unknown urgency {urgency!r}"
    entry = {
        "id": f"q{len(v['queued_actions']) + 1}", "kind": kind, "boundary": boundary,
        "what": what, "why": why, "founder_action": founder_action,
        "payload": payload or {}, "status": "WAITING_FOR_FOUNDER_CONSENT",
        "urgency": urgency, "estimated_minutes": estimated_minutes, "created_at": _now(),
    }
    v["queued_actions"].append(entry)
    return entry


def resolve_queued_action(v: dict, action_id: str, *, status: str) -> None:
    for a in v["queued_actions"]:
        if a["id"] == action_id:
            a["status"] = status
            a["resolved_at"] = _now()
            return
    raise KeyError(f"no queued action {action_id!r}")


def queued_action_status(a: dict) -> str:
    """BLOCKING_NOW / NON_URGENT / HISTORICAL_RESOLVED -- the 3-way split a
    founder-facing view should use instead of showing every ever-queued
    action as if it still needs attention."""
    if a["status"] != "WAITING_FOR_FOUNDER_CONSENT":
        return "HISTORICAL_RESOLVED"
    return "BLOCKING_NOW" if a.get("urgency", "blocking_now") == "blocking_now" else "NON_URGENT"


SPECIALIST_ROLES = ("growth", "content", "sales")


SPECIALIST_DISPOSITIONS = ("accepted", "rejected", "deferred", "modified")


def record_specialist_audit(v: dict, role: str, result: dict) -> None:
    """Persist one specialist's structured audit. Specialists never talk to
    the founder or decide strategy themselves -- the Venture Manager reads
    these findings/recommendations and arbitrates/synthesizes. A fresh audit
    supersedes the prior one's recommendations, so venture_manager_review
    resets to None until the Venture Manager reviews THIS audit."""
    assert role in SPECIALIST_ROLES, f"unknown specialist role {role!r}"
    v.setdefault("specialists", {})[role] = {
        "last_run_at": _now(),
        "findings": result.get("findings", []),
        "recommendations": result.get("recommendations", []),
        "priority_order": result.get("priority_order", []),
        "summary": result.get("summary", ""),
        "venture_manager_review": None,
    }


def set_specialist_review(v: dict, role: str, *, disposition: str, reason: str) -> None:
    """Record the Venture Manager's arbitration of a specialist's CURRENT
    audit -- accepted/rejected/deferred/modified, and why. This is what
    turns 3 independent audits into a coordinated management team on the
    dashboard rather than 3 disconnected outputs."""
    assert role in SPECIALIST_ROLES, f"unknown specialist role {role!r}"
    assert disposition in SPECIALIST_DISPOSITIONS, f"unknown disposition {disposition!r}"
    entry = v.setdefault("specialists", {}).setdefault(role, {})
    entry["venture_manager_review"] = {
        "disposition": disposition, "reason": reason, "reviewed_at": _now(),
    }


MANAGER_ROLES = ("venture", "growth", "content", "sales")


def set_manager_status(v: dict, role: str, *, current_objective: str | None = None,
                       current_blocker: str | None = None, next_action: str | None = None,
                       next_action_at: str | None = None,
                       last_action_result: str | None = None) -> dict:
    """CEO-loop accountability fields, kept SEPARATE from `specialists[role]`
    (which record_specialist_audit fully replaces on every fresh audit) so
    accountability survives independent of whether a specialist audit ran
    this tick. Only supplied fields are updated -- others persist from the
    prior tick until explicitly changed, so a role's objective/blocker don't
    silently vanish just because this tick didn't touch them."""
    assert role in MANAGER_ROLES, f"unknown manager role {role!r}"
    entry = v.setdefault("manager_status", {}).setdefault(role, {})
    if current_objective is not None:
        entry["current_objective"] = current_objective
    if current_blocker is not None:
        entry["current_blocker"] = current_blocker
    if next_action is not None:
        entry["next_action"] = next_action
    if next_action_at is not None:
        entry["next_action_at"] = next_action_at
    if last_action_result is not None:
        entry["last_action_result"] = last_action_result
    entry["updated_at"] = _now()
    return entry


def set_bottleneck(v: dict, bottleneck: str) -> dict:
    """The CEO's (Venture Manager's) current answer to 'what is the single
    highest-leverage constraint preventing this business from moving toward
    validated revenue' -- distinct from system/scheduler health, which can
    be perfectly green while this says e.g. 'no genuine Fanvue traffic yet'."""
    v["current_bottleneck"] = {"description": bottleneck, "identified_at": _now()}
    return v["current_bottleneck"]


CONTENT_LIFECYCLE_STATES = ("BRIEF_READY", "ASSET_GENERATED", "QA_PASSED", "PUBLISH_READY",
                           "SCHEDULED", "PUBLISHED", "DEFERRED")

# Every new content brief should carry exactly one of these (2026-09-13,
# commercial-acceleration rules) so the CEO can eventually learn WHICH
# content produces WHICH business outcome -- deliberately just five labels,
# not a taxonomy.
CONTENT_OBJECTIVES = ("REACH", "ENGAGEMENT", "WORLD_BUILDING", "CONVERSION",
                      "RETENTION_MONETIZATION")

PUBLIC_STATUSES = ("PUBLICLY_LIVE", "INTERNAL_ONLY", "LEGACY_UNMAPPED")

# Minimum rolling PUBLISH_READY+SCHEDULED inventory before replenishment
# becomes an operational priority (commercial-acceleration rules, 2026-09-13).
PUBLISH_READY_INVENTORY_TARGET = 5


def set_content_item_lifecycle(v: dict, item_index: int, status: str, *,
                               asset_ref: str | None = None, model_used: str | None = None,
                               cost_credits: float | None = None,
                               cost_usd: float | None = None,
                               generation_prompt: str | None = None) -> dict:
    """A creative brief (prompt+caption+platform) is NOT publishable content
    -- this makes that distinction an explicit, dashboard-visible lifecycle
    instead of an implicit 'ready' that conflates the two. Never skips
    states silently; the caller states exactly which state was reached and
    why (asset_ref/model_used/cost_credits/cost_usd when a generation
    happened -- cost_usd is for real-dollar providers like Wan, kept
    separate from Higgsfield's cost_credits for the same reason
    wan_budget is kept separate from credit_budget)."""
    assert status in CONTENT_LIFECYCLE_STATES, f"unknown content lifecycle state {status!r}"
    items = v.setdefault("content_plan", {}).setdefault("content_items", [])
    if not (0 <= item_index < len(items)):
        raise IndexError(f"no content_items[{item_index}]")
    item = items[item_index]
    item["lifecycle_status"] = status
    item["lifecycle_updated_at"] = _now()
    if asset_ref is not None:
        item["asset_ref"] = asset_ref
    if model_used is not None:
        item["generation_model"] = model_used
    if cost_credits is not None:
        item["generation_cost_credits"] = cost_credits
    if cost_usd is not None:
        item["generation_cost_usd"] = cost_usd
    if generation_prompt is not None:
        # 2026-09-13 fix: the routing stage (content_director_choose_provider)
        # can propose a REVISED prompt for this attempt (e.g. after a prior
        # QA failure asked for a different gesture) -- without persisting
        # it here, the item's stored generation_prompt stays the stale
        # original forever, so the next attempt never sees what was
        # actually tried and rejected, and can repeat the same failure.
        item["generation_prompt"] = generation_prompt
    return item


def experiment_dir(experiment_id: str) -> Path:
    d = EXPERIMENTS_DIR / experiment_id
    d.mkdir(parents=True, exist_ok=True)
    return d


COMMERCIAL_EVENT_STATUSES = ("DISCOVERED", "ANALYZED", "ACTIONED", "FOUNDER_BLOCKED")


def add_commercial_event(v: dict, *, event_type: str, fan_id: str | None, handle: str | None,
                         ts: str, detail: dict, acquisition_source: str = "UNKNOWN") -> dict:
    """A real Fanvue event discovered by commercial_events.py -- starts life
    as DISCOVERED; the Sales Manager (or Venture Manager) advances its
    lifecycle_status as it's analyzed/acted on. acquisition_source defaults
    to UNKNOWN -- never guess which channel (Instagram/TikTok/organic) sent
    this fan without real attribution data."""
    entry = {
        "id": f"ce{len(v.setdefault('commercial_events', [])) + 1}",
        "type": event_type, "fan_id": fan_id, "handle": handle, "ts": ts, "detail": detail,
        "acquisition_source": acquisition_source, "lifecycle_status": "DISCOVERED",
        "sales_manager_note": None, "recorded_at": _now(),
    }
    v["commercial_events"].append(entry)
    return entry


def update_commercial_event_status(v: dict, event_id: str, *, status: str,
                                   sales_manager_note: str | None = None) -> None:
    assert status in COMMERCIAL_EVENT_STATUSES, f"unknown status {status!r}"
    for e in v.get("commercial_events", []):
        if e["id"] == event_id:
            e["lifecycle_status"] = status
            if sales_manager_note is not None:
                e["sales_manager_note"] = sales_manager_note
            return
    raise KeyError(f"no commercial event {event_id!r}")


def set_disclosure_strategy(v: dict, *, short_form: str, positioning_note: str,
                            per_platform: dict) -> None:
    """Character-first-but-still-compliant disclosure: `disclosure_statement`
    (the full sentence) is preserved unchanged for platforms/contexts where
    it's needed in full (Fanvue bio, DM first contact) -- this only ADDS a
    short form for surfaces where a platform's own native AI-label already
    carries the disclosure, so future captions aren't redundant. Never
    deletes the full statement; a caller must still choose per-context which
    form to use, never silently drop disclosure altogether."""
    persona = v.setdefault("persona", {})
    persona["disclosure_statement_short"] = short_form
    persona["disclosure_positioning_note"] = positioning_note
    persona["disclosure_per_platform"] = per_platform


_ITEM_REF_RE = re.compile(r"content_items(?:\[(\d+)\]| index (\d+))")


def reconcile_pending_approvals(v: dict) -> list[str]:
    """Mark pending approvals whose underlying need is provably gone, from
    CURRENT content-item lifecycle evidence only. Edit-in-place (adds a
    `resolution` record); nothing is deleted, original text is untouched.
    Idempotent. Returns one audit line per entry newly resolved.

    Only two provable cases, deliberately narrow:
      * an Instagram "Manually publish ..." upload ask whose item is now
        SCHEDULED/PUBLISHED through the Metricool route (publish_config);
      * a TikTok per-post consent ask whose item is now PUBLISHED with a
        recorded platform_post_id (consent was given and acted on).
    A consent ask for an item that is NOT published is never touched."""
    items = _read_pending_approvals()
    content = v.get("content_plan", {}).get("content_items", [])
    notes: list[str] = []
    for it in items:
        if approval_is_resolved(it):
            continue
        m = _ITEM_REF_RE.search(it.get("what", ""))
        if not m:
            continue
        idx = int(m.group(1) or m.group(2))
        if not (0 <= idx < len(content)):
            continue
        ci = content[idx]
        status, platform = ci.get("lifecycle_status"), ci.get("target_platform")
        cfg = ci.get("publish_config") or {}
        resolution = None
        if (platform == "instagram" and status in ("SCHEDULED", "PUBLISHED")
                and cfg.get("route") == "metricool"
                and str(it.get("founder_action", "")).lstrip().startswith("Manually publish")):
            resolution = ("SUPERSEDED", f"content_items[{idx}] is {status} via Metricool "
                          f"(metricool_post_id {cfg.get('metricool_post_id')}"
                          f"{', ' + str(cfg.get('scheduled_for')) if cfg.get('scheduled_for') else ''}"
                          f"{', ' + str(ci.get('platform_post_id')) if ci.get('platform_post_id') else ''})")
        elif platform == "tiktok" and status == "PUBLISHED" and ci.get("platform_post_id"):
            resolution = ("COMPLETED", f"content_items[{idx}] PUBLISHED: {ci['platform_post_id']}")
        if resolution:
            it["resolution"] = {"status": resolution[0], "at": _now(), "evidence": resolution[1]}
            notes.append(f"approval {it.get('id', '?')} {resolution[0]}: {resolution[1]}")
    if notes:
        _write_pending_approvals(items)
    return notes
