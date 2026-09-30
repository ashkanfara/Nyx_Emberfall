"""Executor interface + durable state for the ChatGPT browser image route.

The founder wants the normal signed-in ChatGPT browser session to act as an
automatic generation executor (the GENERATION EXECUTOR role in
brand/NYX_PRODUCTION_CONTRACT.md) instead of a manual relay. This module is
the contract around that route, NOT a driver for it:

  - availability states, derived only from observations a separate prober
    reports (this module never opens, reads or drives a browser);
  - a retryable, idempotent, lease-based handoff contract per slide/master;
  - explicit failure classification with a fixed retry policy.

Structural boundary (tested): stdlib only. No browser automation library, no
HTTP client, no credential/cookie access, no API key, no publish call. Every
action that would touch ChatGPT belongs to an executor implementation outside
this module, and `authorize_execution` stays False until the founder records a
decision on the policy question in POLICY_NOTE.

    python3 browser_executor.py status
    python3 browser_executor.py record-probe <observations.json>
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state" / "browser_executor" / "chatgpt_browser_route.json"   # gitignored runtime state
ROUTE_ID = "chatgpt_browser_session"
SCHEMA_VERSION = 1

POLICY_NOTE = (
    "OpenAI's Terms of Use forbid automatically or programmatically extracting Output from the "
    "consumer service. An unattended executor that submits prompts to chatgpt.com and saves the "
    "images is that, whatever tool drives it, and it puts the founder's own ChatGPT account at risk. "
    "The sanctioned automatic path is the OpenAI Images API (openai_image_provider.py, paid per "
    "image). Execution through this route therefore stays disabled until the founder records an "
    "explicit decision (authorize_execution) after reading the current terms.")

# --- availability ------------------------------------------------------------
UNPROBED = "UNPROBED"
NO_BROWSER_ROUTE = "NO_BROWSER_ROUTE"              # no attached browser/session at all
NOT_SIGNED_IN = "NOT_SIGNED_IN"
CHALLENGE_PRESENT = "CHALLENGE_PRESENT"            # captcha/verification: human only, never bypassed
COMPOSER_UNREACHABLE = "COMPOSER_UNREACHABLE"      # signed in, composer not found (UI change?)
COMPOSER_READY_NO_IMAGE = "COMPOSER_READY_NO_IMAGE"
USAGE_CAPPED = "USAGE_CAPPED"                      # plan/image limit banner visible
IMAGE_CAPABLE = "IMAGE_CAPABLE"                    # composer + image capability visible
AVAILABILITY_STATES = (UNPROBED, NO_BROWSER_ROUTE, NOT_SIGNED_IN, CHALLENGE_PRESENT,
                       COMPOSER_UNREACHABLE, COMPOSER_READY_NO_IMAGE, USAGE_CAPPED, IMAGE_CAPABLE)
PROBE_MAX_AGE_S = 15 * 60       # an older probe is treated as UNPROBED

# Only these observation keys are accepted from a prober: booleans about what is
# VISIBLE. Nothing that could carry a cookie, token, prompt or account detail.
OBSERVATION_KEYS = ("browser_attached", "signed_in", "challenge_visible", "composer_visible",
                    "image_capability_visible", "usage_cap_visible")


def classify_availability(obs: dict) -> str:
    extra = set(obs) - set(OBSERVATION_KEYS) - {"observed_at", "observer", "method"}
    if extra:
        raise ValueError(f"probe carries non-observation fields {sorted(extra)} -- refused")
    if not obs.get("browser_attached"):
        return NO_BROWSER_ROUTE
    if obs.get("challenge_visible"):
        return CHALLENGE_PRESENT
    if not obs.get("signed_in"):
        return NOT_SIGNED_IN
    if not obs.get("composer_visible"):
        return COMPOSER_UNREACHABLE
    if obs.get("usage_cap_visible"):
        return USAGE_CAPPED
    return IMAGE_CAPABLE if obs.get("image_capability_visible") else COMPOSER_READY_NO_IMAGE


# --- failure classification --------------------------------------------------
@dataclass(frozen=True)
class FailureClass:
    code: str
    retryable: bool
    needs_human: bool
    cooldown_s: int
    meaning: str


FAILURES = {f.code: f for f in (
    FailureClass("SESSION_EXPIRED", False, True, 0, "signed out mid-run; founder signs in again"),
    FailureClass("CHALLENGE_PRESENT", False, True, 0, "captcha/verification shown; a human solves it, never bypassed"),
    FailureClass("PERMISSION_DENIED", False, True, 0, "a tool/permission prompt was refused; never retried with new wording"),
    FailureClass("USAGE_CAPPED", True, False, 3 * 3600, "plan image limit hit; retry after cooldown"),
    FailureClass("RATE_LIMITED", True, False, 15 * 60, "transient throttling message"),
    FailureClass("NETWORK_ERROR", True, False, 60, "page/network failure BEFORE submit"),
    FailureClass("UI_CHANGED", False, True, 0, "composer/control not found; executor code must be fixed first"),
    FailureClass("CONTENT_REFUSED", False, False, 0, "model refused the prompt; re-prompt (new prompt pack), not retry"),
    FailureClass("OUTCOME_UNKNOWN", False, True, 0,
                 "failed AFTER submit with no result seen; a blind retry could double-generate and burn allowance"),
    FailureClass("DOWNLOAD_FAILED", True, False, 60, "image exists but save failed; retry the save only, never regenerate"),
    FailureClass("OUTPUT_INVALID", True, False, 0, "saved file is not a PNG of the requested render master"),
    FailureClass("POLICY_BLOCKED", False, True, 0, "route not authorized for execution (see POLICY_NOTE)"),
)}

# --- handoff contract --------------------------------------------------------
QUEUED, CLAIMED, SUBMITTED, RESULT_RECORDED = "QUEUED", "CLAIMED", "SUBMITTED", "RESULT_RECORDED"
RETRY_WAIT, NEEDS_HUMAN, FAILED_TERMINAL = "RETRY_WAIT", "NEEDS_HUMAN", "FAILED_TERMINAL"
HANDOFF_STATES = (QUEUED, CLAIMED, SUBMITTED, RESULT_RECORDED, RETRY_WAIT, NEEDS_HUMAN, FAILED_TERMINAL)
_TRANSITIONS = {QUEUED: {CLAIMED}, CLAIMED: {SUBMITTED, RETRY_WAIT, NEEDS_HUMAN, FAILED_TERMINAL, QUEUED},
                SUBMITTED: {RESULT_RECORDED, RETRY_WAIT, NEEDS_HUMAN, FAILED_TERMINAL},
                RETRY_WAIT: {CLAIMED}, RESULT_RECORDED: set(), NEEDS_HUMAN: set(), FAILED_TERMINAL: set()}
MAX_ATTEMPTS = 3
LEASE_S = 10 * 60


class ExecutorContractError(ValueError):
    pass


def _sha16(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


@dataclass
class Handoff:
    handoff_id: str
    item_index: int
    slide_index: int
    render_master: str
    prompt_pack: str
    prompt_sha256_16: str
    references: list[dict]
    expected_output: dict
    state: str = QUEUED
    attempts: int = 0
    max_attempts: int = MAX_ATTEMPTS
    claimed_by: str | None = None
    lease_expires_at: float | None = None
    not_before: float = 0.0
    result: dict | None = None
    last_failure: dict | None = None
    history: list[dict] = field(default_factory=list)


def new_handoff(item_index: int, slide_index: int, render_master: str, prompt_pack: Path,
                references: list[Path], output_path: str) -> Handoff:
    """Idempotency key = item/slide/master + prompt bytes + reference bytes, so
    re-enqueueing the same work returns the same handoff, and changing the
    prompt pack is a NEW handoff rather than a silent retry of the old one."""
    import render_masters

    m = render_masters.master(render_master)
    refs = [{"path": str(p), "sha256_16": _sha16(p)} for p in references]
    psha = _sha16(prompt_pack)
    key = json.dumps([item_index, slide_index, render_master, psha, [r["sha256_16"] for r in refs]])
    hid = "h_" + hashlib.sha256(key.encode()).hexdigest()[:16]
    return Handoff(hid, item_index, slide_index, render_master, str(prompt_pack), psha, refs,
                   {"path": output_path, "width": m["width"], "height": m["height"], "aspect": m["aspect"]})


# --- durable store -----------------------------------------------------------
class RouteStore:
    """One JSON file: route availability, founder authorization, handoffs.
    Every write replaces the file atomically; history is append-only."""

    def __init__(self, path: Path = STATE_PATH, clock=time.time):
        self.path, self.clock = Path(path), clock

    def load(self) -> dict:
        if self.path.is_file():
            return json.loads(self.path.read_text())
        return {"schema_version": SCHEMA_VERSION, "route_id": ROUTE_ID, "availability": UNPROBED,
                "last_probe": None, "execution_authorized": False, "authorization": None,
                "policy_note": POLICY_NOTE, "handoffs": {}}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        tmp.replace(self.path)

    # availability
    def record_probe(self, obs: dict) -> dict:
        state = classify_availability(obs)
        data = self.load()
        data["availability"] = state
        data["last_probe"] = {"at": self.clock(), "availability": state,
                              "observations": {k: bool(obs.get(k)) for k in OBSERVATION_KEYS},
                              "observer": str(obs.get("observer", "")), "method": str(obs.get("method", ""))}
        self.save(data)
        return data["last_probe"]

    def availability(self) -> str:
        data = self.load()
        probe = data.get("last_probe")
        if not probe or self.clock() - probe["at"] > PROBE_MAX_AGE_S:
            return UNPROBED
        return probe["availability"]

    def authorize_execution(self, decided_by: str, terms_reviewed_on: str, note: str = "") -> dict:
        """Founder-only decision record. Nothing in this repo calls it."""
        if not decided_by.strip() or not terms_reviewed_on.strip():
            raise ExecutorContractError("authorization needs decided_by and terms_reviewed_on")
        data = self.load()
        data["execution_authorized"] = True
        data["authorization"] = {"decided_by": decided_by, "terms_reviewed_on": terms_reviewed_on,
                                 "note": note, "at": self.clock()}
        self.save(data)
        return data["authorization"]

    # handoffs
    def enqueue(self, h: Handoff) -> Handoff:
        data = self.load()
        if h.handoff_id not in data["handoffs"]:
            h.history.append({"at": self.clock(), "event": QUEUED})
            data["handoffs"][h.handoff_id] = asdict(h)
            self.save(data)
        return Handoff(**data["handoffs"][h.handoff_id])

    def _move(self, data: dict, h: dict, to: str, **detail) -> None:
        if to not in _TRANSITIONS[h["state"]]:
            raise ExecutorContractError(f"{h['handoff_id']}: {h['state']} -> {to} is not allowed")
        h["state"] = to
        h["history"].append({"at": self.clock(), "event": to, **detail})

    def claim(self, executor_id: str) -> Handoff | None:
        """Lease the next runnable handoff. Refuses unless the route is
        authorized AND a fresh probe says IMAGE_CAPABLE. An expired lease on a
        CLAIMED handoff (executor crashed before submit) is safely re-queued;
        an expired lease on a SUBMITTED one is OUTCOME_UNKNOWN, never re-run."""
        data = self.load()
        if not data.get("execution_authorized"):
            raise ExecutorContractError(f"POLICY_BLOCKED: {POLICY_NOTE}")
        if self.availability() != IMAGE_CAPABLE:
            raise ExecutorContractError(f"route availability is {self.availability()}, not {IMAGE_CAPABLE}")
        now = self.clock()
        for h in data["handoffs"].values():
            if h["lease_expires_at"] and h["lease_expires_at"] < now:
                if h["state"] == CLAIMED:
                    self._move(data, h, QUEUED, reason="lease expired before submit")
                    h["claimed_by"] = h["lease_expires_at"] = None
                elif h["state"] == SUBMITTED:
                    self._fail(data, h, "OUTCOME_UNKNOWN", "lease expired after submit")
        for h in sorted(data["handoffs"].values(), key=lambda x: x["handoff_id"]):
            if h["state"] in (QUEUED, RETRY_WAIT) and h["not_before"] <= now:
                self._move(data, h, CLAIMED, executor=executor_id)
                h["claimed_by"], h["lease_expires_at"] = executor_id, now + LEASE_S
                h["attempts"] += 1
                self.save(data)
                return Handoff(**h)
        self.save(data)
        return None

    def mark_submitted(self, handoff_id: str, executor_id: str) -> None:
        data = self.load()
        h = self._owned(data, handoff_id, executor_id)
        self._move(data, h, SUBMITTED)
        self.save(data)

    def record_result(self, handoff_id: str, executor_id: str, saved_path: Path) -> dict:
        """Validate the file the executor saved against the render master.
        Returns the exact bookkeeping command for the contract's
        record-generated step; it does not run it."""
        data = self.load()
        h = self._owned(data, handoff_id, executor_id)
        size = _png_size(Path(saved_path))
        exp = h["expected_output"]
        if size != (exp["width"], exp["height"]):
            self._fail(data, h, "OUTPUT_INVALID", f"got {size}, expected {exp['width']}x{exp['height']}")
            self.save(data)
            return {"ok": False, "failure": "OUTPUT_INVALID", "state": h["state"]}
        h["result"] = {"path": str(saved_path), "sha256_16": _sha16(Path(saved_path)), "size": list(size)}
        self._move(data, h, RESULT_RECORDED)
        self.save(data)
        return {"ok": True, "state": RESULT_RECORDED,
                "next": f"python3 episode_ops.py record-generated {h['item_index']} {h['slide_index']} "
                        f"{saved_path} {ROUTE_ID}"}

    def record_failure(self, handoff_id: str, executor_id: str, code: str, detail: str = "") -> dict:
        data = self.load()
        h = self._owned(data, handoff_id, executor_id)
        self._fail(data, h, code, detail)
        self.save(data)
        return {"state": h["state"], "not_before": h["not_before"]}

    def _fail(self, data: dict, h: dict, code: str, detail: str) -> None:
        if code not in FAILURES:
            raise ExecutorContractError(f"unknown failure class {code!r}")
        f = FAILURES[code]
        if f.needs_human:
            to = NEEDS_HUMAN
        elif f.retryable and h["attempts"] < h["max_attempts"]:
            to = RETRY_WAIT
        else:
            to = FAILED_TERMINAL
        h["last_failure"] = {"code": code, "detail": detail, "at": self.clock()}
        h["not_before"] = self.clock() + f.cooldown_s
        h["claimed_by"] = h["lease_expires_at"] = None
        self._move(data, h, to, failure=code, detail=detail)

    def _owned(self, data: dict, handoff_id: str, executor_id: str) -> dict:
        h = data["handoffs"].get(handoff_id)
        if h is None:
            raise ExecutorContractError(f"unknown handoff {handoff_id}")
        if h["claimed_by"] != executor_id:
            raise ExecutorContractError(f"{handoff_id} is not leased to {executor_id}")
        return h


def _png_size(p: Path) -> tuple[int, int] | None:
    import struct

    head = p.read_bytes()[:24] if p.is_file() else b""
    return struct.unpack(">II", head[16:24]) if head[:8] == b"\x89PNG\r\n\x1a\n" else None


# --- executor interface ------------------------------------------------------
class ImageExecutor(Protocol):
    """What a real executor (outside this repo module) must implement. probe()
    reports ONLY the boolean OBSERVATION_KEYS and must not type, upload,
    click send or change settings. run() works one leased handoff and reports
    back through RouteStore.mark_submitted / record_result / record_failure."""

    executor_id: str

    def probe(self) -> dict: ...

    def run(self, handoff: Handoff, store: RouteStore) -> None: ...


class UnattachedExecutor:
    """The executor this environment actually has: no browser attached."""

    executor_id = "unattached"

    def probe(self) -> dict:
        return {"browser_attached": False, "observer": self.executor_id, "method": "no browser route"}

    def run(self, handoff: Handoff, store: RouteStore) -> None:
        raise ExecutorContractError("no browser route attached -- nothing can run")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    store = RouteStore()
    if argv[:1] == ["status"]:
        d = store.load()
        out = {"availability": store.availability(), "execution_authorized": d["execution_authorized"],
               "last_probe": d["last_probe"],
               "handoffs": {k: v["state"] for k, v in sorted(d["handoffs"].items())}}
    elif argv[:1] == ["record-probe"] and len(argv) == 2:
        out = store.record_probe(json.loads(Path(argv[1]).read_text()))
    else:
        print(__doc__)
        return 1
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
