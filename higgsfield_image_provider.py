"""Premium still-image generation for ONE story-carousel slide through the
account's EXISTING Higgsfield connector (founder decision 2026-09-24: OpenAI
GPT Image 2 via Higgsfield is the first premium Nyx route; Cloudflare was
rejected for Nyx identity).

Reuses the same transport stages.content_director_generate already uses -- a
bounded `claude -p` subprocess (claude_client.claude_call) with an explicit
Higgsfield tool allowlist -- but with no creative latitude: the model, the
settings, the reference and the prompt are fixed here, and the subprocess only
executes them and reports what it observed.

Spend safety lives in the caller (ps05_ops.generate_slide): a single-use,
slide-scoped founder grant, the venture credit ceiling and an explicit
--confirm-spend. This module adds two more fail-closed checks of its own:
  * a free cost preflight (generate_image get_cost) must come in at or under
    max_credits BEFORE anything is submitted;
  * the credits actually charged (balance before/after + the transaction line)
    must reconcile, and are what the caller records -- never an estimate.

It never approves anything: the returned file is a CANDIDATE that the story
continuity lifecycle keeps at PENDING_VISUAL_QA."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from claude_client import claude_call, extract_json

PROVIDER = "higgsfield"
MODEL = "gpt_image_2"
# GPT Image 2 has no 4:5 option on Higgsfield; 3:4 is the nearest portrait
# ratio. The 4:5 feed crop is taken deterministically afterwards (crop_to_4x5).
SETTINGS = {"aspect_ratio": "3:4", "resolution": "2k", "quality": "high", "count": 1}
OUTPUT_SIZE = (1080, 1350)          # public carousel master only (render_masters.PUBLIC_MASTER);
                                    # never used for the 9:16 TikTok/Fanvue master

_P = "mcp__claude_ai_Higgsfield__"
TOOLS = [_P + t for t in ("media_import_url", "generate_image", "jobs_wait", "job_display",
                          "balance", "transactions")]

_INSTRUCTIONS = """You are an execution worker. Do EXACTLY these steps with the Higgsfield
tools, nothing else. Do not choose another model, do not change any setting, do not
rewrite the prompt, never submit more than ONE generation, never resubmit on a timeout,
and never set use_unlim to true.

INPUT
- reference_url: {reference_url}
- max_credits: {max_credits}
- model: {model}
- settings: {settings}
- prompt (use verbatim, between the markers):
<<<PROMPT
{prompt}
PROMPT>>>

STEPS
1. media_import_url(url=reference_url, type="image") -> reference media_id.
2. generate_image with params = {{"model": model, **settings, "prompt": <prompt verbatim>,
   "medias": [{{"role": "image", "value": <reference media_id>}}], "use_unlim": false,
   "get_cost": true}}. This submits nothing. Record credits_exact as preflight_credits.
   If preflight_credits > max_credits: STOP and return status "REFUSED_COST".
3. balance() -> record credits as balance_before.
4. generate_image with the SAME params but WITHOUT get_cost (use_unlim false). Submit once.
5. jobs_wait on the returned job id until it finishes (job_display if you need the result
   URL). If the job fails, return status "FAILED" with the job id.
6. balance() -> record credits as balance_after.
7. transactions(size=5) -> copy the newest spend line(s) created by step 4 verbatim.

Reply with ONLY this JSON object:
{{"status": "GENERATED" | "REFUSED_COST" | "FAILED",
  "reference_media_id": "...", "preflight_credits": <number>,
  "job_id": "...", "image_url": "https://... (the generated image result URL)",
  "balance_before": <number|null>, "balance_after": <number|null>,
  "transactions": [ ... verbatim spend lines from step 7 ... ],
  "detail": "one line"}}
"""


class HiggsfieldError(RuntimeError):
    pass


def request_spec(prompt: str, reference_url: str) -> dict:
    """What will be sent -- recorded with the candidate, reproducible, no secrets."""
    return {"provider": PROVIDER, "model": MODEL, "settings": dict(SETTINGS),
            "reference_url": reference_url, "reference_role": "image",
            "prompt": prompt,
            "prompt_sha256_16": hashlib.sha256(prompt.encode()).hexdigest()[:16],
            "post_processing": f"center crop 3:4 -> 4:5, resize to {OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]}, PNG"}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def charged_credits(result: dict) -> float | None:
    """Credits actually charged, from the account's own numbers. The balance
    delta is authoritative; the transaction line must agree with it."""
    before, after = _num(result.get("balance_before")), _num(result.get("balance_after"))
    if before is None or after is None:
        return None
    return round(before - after, 2)


def validate(result: dict, *, max_credits: float) -> dict:
    """Fail closed on anything the subprocess reports that the code cannot
    reconcile. Returns {"ok", "status", "charged_credits", "problems"}."""
    problems: list[str] = []
    status = result.get("status")
    if status not in ("GENERATED", "REFUSED_COST", "FAILED"):
        problems.append(f"unknown status {status!r}")
    pre = _num(result.get("preflight_credits"))
    if pre is None:
        problems.append("no preflight cost reported")
    elif pre > max_credits:
        if status == "GENERATED":
            problems.append(f"generated although preflight {pre} > max_credits {max_credits}")
    charged = charged_credits(result)
    if status == "GENERATED":
        if not str(result.get("job_id") or "").strip():
            problems.append("no job_id")
        if not str(result.get("image_url") or "").startswith("https://"):
            problems.append("no https image_url")
        if charged is None:
            problems.append("balance before/after missing -- actual charge unknown")
        elif charged > max_credits + 0.01:
            problems.append(f"charged {charged} credits > max_credits {max_credits}")
        elif pre is not None and abs(charged - pre) > 0.51:
            problems.append(f"charged {charged} credits does not match preflight {pre}")
        if not result.get("transactions"):
            problems.append("no transaction line reported for the charge")
    return {"ok": status == "GENERATED" and not problems, "status": status,
            "preflight_credits": pre, "charged_credits": charged, "problems": problems}


def crop_to_4x5(raw: bytes) -> bytes:
    """Deterministic: centre-crop to exactly 4:5, resize to 1080x1350, PNG."""
    from PIL import Image

    im = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = im.size
    target = 4 / 5
    if w / h > target:                       # too wide -> trim sides
        nw = round(h * target)
        box = ((w - nw) // 2, 0, (w - nw) // 2 + nw, h)
    else:                                    # too tall -> trim top/bottom equally
        nh = round(w / target)
        box = (0, (h - nh) // 2, w, (h - nh) // 2 + nh)
    out = im.crop(box).resize(OUTPUT_SIZE, Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def _download(url: str) -> bytes:
    import requests

    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r.content


def generate(prompt: str, *, reference_url: str, out_path: Path, max_credits: float,
             caller=None, downloader=None) -> dict:
    """ONE paid generation. Writes the raw provider file next to out_path and the
    4:5 candidate at out_path. Returns the validated result; raises
    HiggsfieldError (after recording what is known) when it cannot reconcile."""
    caller = caller or (lambda p: claude_call(p, tools=TOOLS, timeout=600))
    downloader = downloader or _download
    if not reference_url.startswith("https://"):
        raise HiggsfieldError("reference_url must be the hosted https master reference")
    text = _INSTRUCTIONS.format(reference_url=reference_url, max_credits=max_credits,
                                model=MODEL, settings=json.dumps(SETTINGS), prompt=prompt)
    r = caller(text)
    result = extract_json(r["text"])
    check = validate(result, max_credits=max_credits)
    record = {"request": request_spec(prompt, reference_url), "provider_result": result,
              "validation": check, "orchestrator_cost_usd": r.get("cost_usd")}
    if not check["ok"]:
        return {**record, "ok": False, "path": None}
    try:
        raw = downloader(result["image_url"])
    except Exception as exc:                 # charged but not retrieved: keep the record
        return {**record, "ok": False, "path": None,
                "validation": {**check, "ok": False,
                               "problems": check["problems"] + [f"download failed: {exc}"]}}
    ext = ".png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg" if raw[:3] == b"\xff\xd8\xff" \
        else ".webp" if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP" else ".bin"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = out_path.with_name(f"{out_path.stem}.{PROVIDER}_raw{ext}")
    raw_path.write_bytes(raw)
    if ext == ".bin":
        return {**record, "ok": False, "path": None, "raw_path": str(raw_path),
                "validation": {**check, "ok": False,
                               "problems": check["problems"] + ["downloaded file is not an image"]}}
    out_path.write_bytes(crop_to_4x5(raw))
    return {**record, "ok": True, "path": str(out_path), "raw_path": str(raw_path),
            "raw_sha256_16": hashlib.sha256(raw).hexdigest()[:16],
            "sha256_16": hashlib.sha256(out_path.read_bytes()).hexdigest()[:16],
            "dimensions": f"{OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]}"}
