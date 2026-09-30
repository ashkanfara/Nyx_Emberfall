"""Direct integration with Alibaba Cloud Model Studio's Wan video-generation
API (DashScope). Unlike Higgsfield (reached via MCP tools inside a nested
`claude -p` subprocess), Wan has no MCP surface -- this is a plain REST
client called directly from Python so that budget/quota enforcement is a
hard, deterministic check in code, never something an LLM merely self-reports.

ENDPOINT (verified 2026-09-12 against alibabacloud.com/help/en/model-studio):
uses the GENERIC region domain (dashscope-intl.aliyuncs.com), which Alibaba's
own docs confirm "accepts an API Key from any workspace in the same region" --
no Workspace ID required. A workspace-dedicated domain
({WorkspaceId}.ap-southeast-1.maas.aliyuncs.com) is Alibaba's own
"recommended for production" path (higher throughput/lower latency/traffic
isolation) but is NOT required for correctness -- fine to add later if this
ever needs production-grade throughput; not needed for a single benchmark.
Native path confirmed: /api/v1/services/aigc/video-generation/video-synthesis
(NOT /image2video/ -- that was an earlier, unverified guess, now corrected).

TWO DIFFERENT REQUEST SCHEMAS exist across Wan model generations, confirmed
NOT interchangeable:
- "legacy_flat" (wan2.1/2.2/2.6 i2v models): input = {"prompt", "img_url"},
  first-frame-only conditioning, resolution values like "480P"/"720P".
- "media_array" (wan2.7-i2v): input = {"prompt", "negative_prompt", "media":
  [{"type": "first_frame"|"last_frame"|"driving_audio"|"first_clip", "url"}]},
  supports first+last frame and driving-audio combinations, resolution only
  "720P"/"1080P", duration a flexible 2-15s integer, and an explicit
  "watermark": bool parameter (default false) that wan2.2-era models don't
  expose. Resolution strings are the exact wire values Alibaba's docs use
  (uppercase P) -- stored as-is in MODEL_SPECS to avoid a case-mapping bug.

BILLING MODE distinguishes two genuinely different situations:
- "pay_per_second": confirmed baseline rate ($0.05/s 480P, $0.10/s 720P,
  $0.20/s 1080P -- primary-sourced 2026-09-12; a flash-tier-specific
  discount some secondary sources claim could not be confirmed, so the
  higher, confirmed rate is used for budget math). estimate_cost_usd()
  applies here.
- "free_quota_protected": the founder's account has a free quota + the
  account-level "Stop on Exhaust" ("Free Quota Only") protection enabled
  for this specific model (confirmed 2026-09-12 that this protection is
  enforced server-side for API calls, not just console usage, via the
  AllocationQuota.FreeTierOnly error code) -- no confirmed per-second
  dollar rate exists for these, so there is nothing to estimate; the
  account-side protection IS the budget guard. generate_video() for these
  models refuses to run unless called with free_quota_only=True, and
  never falls back to another model on any error."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import wan_runtime

API_BASE = "https://dashscope-intl.aliyuncs.com/api/v1"
ASSETS_DIR = Path(__file__).resolve().parent / "generated_assets" / "wan"

# Cheapest-first within pay_per_second; wan2.7-i2v-2026-04-25 is the current
# free-quota-protected benchmark candidate (founder-verified in-console,
# 2026-09-12) -- kept separate from the "cheapest adequate paid default"
# question, since its economics are governed by account quota, not a $/sec
# rate. Content Director may be extended later to choose among these based
# on cost/quality once more than one is benchmarked; nothing here forces a
# single hardcoded model.
MODEL_SPECS = {
    "wan2.2-i2v-flash": {
        "request_schema": "legacy_flat", "billing_mode": "pay_per_second",
        "resolutions": ("480P", "720P"), "default_resolution": "720P",
        "fixed_duration_seconds": 5, "silent_by_default": True,
        "reference_conditioning": "first_frame_only", "watermark": "unconfirmed",
    },
    "wan2.2-i2v-plus": {
        "request_schema": "legacy_flat", "billing_mode": "pay_per_second",
        "resolutions": ("480P", "1080P"), "default_resolution": "1080P",
        "fixed_duration_seconds": 5, "silent_by_default": True,
        "reference_conditioning": "first_frame_only", "watermark": "unconfirmed",
    },
    "wan2.6-i2v": {
        "request_schema": "legacy_flat", "billing_mode": "pay_per_second",
        "resolutions": ("720P", "1080P"), "default_resolution": "720P",
        "duration_range_seconds": (2, 15), "silent_by_default": False,
        "reference_conditioning": "first_frame_only", "watermark": "unconfirmed",
    },
    "wan2.7-i2v-2026-04-25": {
        "request_schema": "media_array", "billing_mode": "free_quota_protected",
        "resolutions": ("720P", "1080P"), "default_resolution": "1080P",
        "duration_range_seconds": (2, 15),
        # CORRECTED 2026-09-12 by a real benchmark call: without any
        # driving_audio input, the output still had a real (non-silent)
        # AAC track at -34.4dB -- this model generates its own audio by
        # default, unlike the wan2.2-era models above.
        "silent_by_default": False,
        "reference_conditioning": "first_frame_and_last_frame_and_driving_audio",
        "watermark": False,  # confirmed: explicit "watermark" bool param, default false
    },
}

_PRICE_PER_SECOND_USD = {"480P": 0.05, "720P": 0.10, "1080P": 0.20}


class WanNotConfigured(RuntimeError):
    """No API key stored yet -- founder has not completed Model Studio
    setup. The system must stay inert (never attempt a call) in this state."""


class WanBudgetExceeded(RuntimeError):
    pass


class WanFreeQuotaExhausted(RuntimeError):
    """The account's free-quota-only protection refused the request
    (AllocationQuota.FreeTierOnly or equivalent) -- by design this is NOT
    caught and silently retried/rerouted; the caller must fail safely."""


class WanGenerationError(RuntimeError):
    pass


def estimate_cost_usd(model: str, resolution: str, duration_seconds: int) -> float:
    if model not in MODEL_SPECS:
        raise ValueError(f"unknown Wan model {model!r} -- not in the reviewed whitelist")
    spec = MODEL_SPECS[model]
    if spec["billing_mode"] != "pay_per_second":
        raise ValueError(
            f"{model} is billing_mode={spec['billing_mode']!r} -- has no $/sec estimate; "
            f"use free_quota_only=True on generate_video() instead of a USD budget check")
    if resolution not in spec["resolutions"]:
        raise ValueError(f"{model} does not support resolution {resolution!r}")
    if resolution not in _PRICE_PER_SECOND_USD:
        raise ValueError(f"no confirmed price for resolution {resolution!r}")
    return round(_PRICE_PER_SECOND_USD[resolution] * duration_seconds, 4)


def _headers(api_key: str, *, async_job: bool = False) -> dict:
    h = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if async_job:
        h["X-DashScope-Async"] = "enable"
    return h


def _build_request_body(*, model: str, image_url: str, prompt: str,
                        resolution: str, duration_seconds: int) -> dict:
    schema = MODEL_SPECS[model]["request_schema"]
    if schema == "legacy_flat":
        return {
            "model": model,
            "input": {"prompt": prompt, "img_url": image_url},
            "parameters": {"resolution": resolution, "duration": duration_seconds},
        }
    if schema == "media_array":
        return {
            "model": model,
            "input": {"prompt": prompt,
                      "media": [{"type": "first_frame", "url": image_url}]},
            "parameters": {"resolution": resolution, "duration": duration_seconds,
                          "watermark": False},
        }
    raise ValueError(f"unknown request_schema {schema!r} for model {model!r}")


def _is_free_quota_exhausted_error(body_text: str) -> bool:
    return "AllocationQuota.FreeTierOnly" in body_text or "FreeTierOnly" in body_text


def _submit_task(api_key: str, *, model: str, image_url: str, prompt: str,
                 resolution: str, duration_seconds: int) -> str:
    body = json.dumps(_build_request_body(
        model=model, image_url=image_url, prompt=prompt,
        resolution=resolution, duration_seconds=duration_seconds)).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/services/aigc/video-generation/video-synthesis", data=body,
        headers=_headers(api_key, async_job=True), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        if _is_free_quota_exhausted_error(detail):
            raise WanFreeQuotaExhausted(
                f"free quota unavailable/exhausted for {model}: HTTP {exc.code} -- {detail}") from exc
        raise WanGenerationError(f"submit failed: HTTP {exc.code} -- {detail}") from exc
    task_id = data.get("output", {}).get("task_id")
    if not task_id:
        raise WanGenerationError(f"submit succeeded but no task_id in response: {data}")
    return task_id


def _poll_task(api_key: str, task_id: str, *, timeout: int = 480, interval: int = 5) -> dict:
    deadline = time.monotonic() + timeout
    req = urllib.request.Request(f"{API_BASE}/tasks/{task_id}", headers=_headers(api_key))
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if _is_free_quota_exhausted_error(detail):
                raise WanFreeQuotaExhausted(
                    f"free quota unavailable/exhausted while polling {task_id}: "
                    f"HTTP {exc.code} -- {detail}") from exc
            raise WanGenerationError(f"poll failed: HTTP {exc.code} -- {detail}") from exc
        status = data.get("output", {}).get("task_status")
        if status == "SUCCEEDED":
            result = dict(data["output"])
            if "usage" in data:  # sibling of "output" per Alibaba's schema -- preserve it
                result["_usage"] = data["usage"]
            return result
        if status in ("FAILED", "UNKNOWN"):
            msg = str(data.get("output", {}))
            if _is_free_quota_exhausted_error(msg):
                raise WanFreeQuotaExhausted(f"generation {status} -- free quota exhausted: {msg}")
            raise WanGenerationError(f"generation {status}: {data.get('output', {}).get('message', data)}")
        time.sleep(interval)
    raise WanGenerationError(f"task {task_id} did not complete within {timeout}s")


def _download_asset(url: str, *, item_label: str) -> Path:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    dest = ASSETS_DIR / f"{item_label}_{int(time.time())}.mp4"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        f.write(resp.read())
    return dest


def verify_connection(*, model: str) -> dict:
    """Zero-cost, no-generation connectivity/auth check.

    CONFIRMED BY A REAL CALL (2026-09-12): this API does NOT reject an
    incomplete body synchronously -- it returns HTTP 200 with a real
    task_id in PENDING status even when required fields are missing, then
    fails the task asynchronously (observed: FAILED within ~0.1s, code
    "InvalidParameter", message "Field required: input.media", no `usage`
    block in the response -- consistent with Alibaba's confirmed billing
    rule that failed jobs are not billed and do not consume free quota).
    So this function submits a DELIBERATELY incomplete body (missing
    "input" entirely) and then POLLS the resulting task to completion,
    treating a fast FAILED/InvalidParameter outcome as the expected,
    zero-cost confirmation that: DNS/TLS/routing reach the endpoint, the
    stored key authenticates, and the account can address this model.

    Anything OTHER than a fast validation-shaped FAILED is the discrepancy
    to report before proceeding -- a SUCCEEDED status here would mean a
    real (uncontrolled) video got generated from an incomplete request,
    and must never be treated as a good outcome."""
    creds = wan_runtime.load_api_key()
    if creds is None:
        raise WanNotConfigured("no Wan API key stored -- founder setup not yet complete")
    if model not in MODEL_SPECS:
        raise ValueError(f"unknown Wan model {model!r} -- not in the reviewed whitelist")

    body = json.dumps({"model": model}).encode("utf-8")  # deliberately missing "input"
    req = urllib.request.Request(
        f"{API_BASE}/services/aigc/video-generation/video-synthesis", data=body,
        headers=_headers(creds["api_key"], async_job=True), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            submit_data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        auth_ok = exc.code not in (401, 403)
        return {"reachable": True, "http_status": exc.code, "auth_ok": auth_ok,
               "outcome": "rejected synchronously (no task created)", "raw": detail}
    except urllib.error.URLError as exc:
        return {"reachable": False, "http_status": None, "auth_ok": None,
               "outcome": "unreachable", "raw": str(exc)}

    task_id = submit_data.get("output", {}).get("task_id")
    if not task_id:
        return {"reachable": True, "http_status": 200, "auth_ok": True,
               "outcome": "unexpected: 200 with no task_id", "raw": submit_data}

    deadline = time.monotonic() + 30
    poll_req = urllib.request.Request(f"{API_BASE}/tasks/{task_id}",
                                      headers=_headers(creds["api_key"]))
    final = None
    while time.monotonic() < deadline:
        with urllib.request.urlopen(poll_req, timeout=20) as resp:
            final = json.loads(resp.read().decode("utf-8"))
        status = final.get("output", {}).get("task_status")
        if status in ("SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"):
            break
        time.sleep(2)

    output = (final or {}).get("output", {})
    status = output.get("task_status")
    had_usage = "usage" in (final or {})
    validation_shaped = status == "FAILED" and output.get("code") in (
        "InvalidParameter", "InvalidParameter.DataInspection") and not had_usage

    if validation_shaped:
        outcome = "CONFIRMED: auth + model access verified, zero cost (task failed validation, no usage block)"
    elif status == "SUCCEEDED":
        outcome = "DISCREPANCY: task SUCCEEDED from a deliberately-incomplete request -- STOP, do not proceed"
    else:
        outcome = f"DISCREPANCY: unexpected terminal state {status!r} (code={output.get('code')!r}) -- investigate before proceeding"

    return {"reachable": True, "http_status": 200, "auth_ok": True, "task_id": task_id,
           "final_status": status, "final_code": output.get("code"), "had_usage_block": had_usage,
           "outcome": outcome, "raw": final}


def generate_video(*, model: str, image_url: str, prompt: str, resolution: str,
                   duration_seconds: int, budget_usd_remaining: float = 0.0,
                   free_quota_only: bool = False, item_label: str = "clip") -> dict:
    """Deterministic, budget/quota-hard-capped Wan generation.

    Two mutually exclusive modes, matching the two billing_modes:
    - free_quota_only=True: for billing_mode="free_quota_protected" models
      ONLY. No USD estimate is computed or checked (none exists) -- the
      account's own Stop-on-Exhaust setting is the guard. Any error
      (including a detected free-quota-exhausted signal) is raised
      immediately; this function never retries with a different model and
      never falls back to another provider -- that is the CALLER's
      responsibility to not do, by simply not coding a fallback path here.
    - free_quota_only=False (default): for billing_mode="pay_per_second"
      models. Hard USD budget check happens BEFORE any network call.

    Raises rather than silently degrading: WanNotConfigured if no key is
    stored, WanBudgetExceeded / WanFreeQuotaExhausted if the relevant guard
    trips, WanGenerationError for any other provider-side failure. Never
    bills a failed job (Alibaba's own billing rule, confirmed 2026-09-12) --
    only a confirmed SUCCEEDED task's real duration/cost is reported."""
    creds = wan_runtime.load_api_key()
    if creds is None:
        raise WanNotConfigured("no Wan API key stored -- founder setup not yet complete")
    if model not in MODEL_SPECS:
        raise ValueError(f"unknown Wan model {model!r} -- not in the reviewed whitelist")
    spec = MODEL_SPECS[model]

    if free_quota_only:
        if spec["billing_mode"] != "free_quota_protected":
            raise ValueError(
                f"free_quota_only=True requested but {model} is billing_mode="
                f"{spec['billing_mode']!r}, not 'free_quota_protected' -- refusing to proceed "
                f"on an assumption of free-quota protection this model doesn't have")
        estimated_cost = None
    else:
        if spec["billing_mode"] != "pay_per_second":
            raise ValueError(
                f"{model} is billing_mode={spec['billing_mode']!r} -- call with "
                f"free_quota_only=True instead of a USD budget")
        estimated_cost = estimate_cost_usd(model, resolution, duration_seconds)
        if estimated_cost > budget_usd_remaining:
            raise WanBudgetExceeded(
                f"estimated cost ${estimated_cost:.2f} exceeds remaining Wan budget "
                f"${budget_usd_remaining:.2f} -- refusing to submit")

    started = time.monotonic()
    task_id = _submit_task(creds["api_key"], model=model, image_url=image_url, prompt=prompt,
                           resolution=resolution, duration_seconds=duration_seconds)
    output = _poll_task(creds["api_key"], task_id)
    generation_seconds = round(time.monotonic() - started, 1)

    video_url = output.get("video_url") or output.get("results", {}).get("video_url")
    if not video_url:
        raise WanGenerationError(f"task SUCCEEDED but no video_url in output: {output}")
    actual_duration = output.get("actual_duration", duration_seconds)
    actual_cost = 0.0 if free_quota_only else estimate_cost_usd(model, resolution, actual_duration)

    local_path = _download_asset(video_url, item_label=item_label)

    return {
        "asset_ref": str(local_path), "source_url": video_url, "model_used": model,
        "cost_usd": actual_cost, "billing_mode": spec["billing_mode"],
        "duration_seconds": actual_duration, "resolution": resolution,
        "generation_seconds": generation_seconds, "watermark": spec["watermark"],
        "provider_usage": output.get("_usage"),  # raw usage block Alibaba returned, if any
    }
