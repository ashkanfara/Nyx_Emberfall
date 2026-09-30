"""Premium still-image generation for ONE story-carousel slide through the
OpenAI Images API DIRECTLY (founder decision 2026-09-25: gpt-image-2.5-sunburst,
"for workflows where editing precision matters most", for Nyx identity).

The story continuity orchestrator owns the prompt and the reference selection;
this adapter only (a) orders the selected references by authority and tells
the model what each one is allowed to decide, (b) estimates the cost before a
call, (c) makes exactly ONE images.edit call when the caller has cleared every
spend gate, and (d) records the usage OpenAI actually returns.

The key comes from openai_runtime (gitignored .openai_runtime/), is passed to
the client object only, and is never logged, printed or written anywhere.
No fallback: if this call fails, nothing else is tried.

Pricing and the output-token formula were read from OpenAI's own docs on
2026-09-25 (developers.openai.com/api/docs/models/gpt-image-2.5-sunburst and the
image-generation guide's GptImageTokenCalculator). OpenAI does not publish an
image-INPUT token rule for GPT Image 2.5, so input cost is bounded, not
predicted; the response's `usage` is the number that gets recorded."""
from __future__ import annotations

import base64
import hashlib
import io
import math
from pathlib import Path

PROVIDER = "openai"
MODEL = "gpt-image-2.5-sunburst"
DEFAULT_QUALITY = "high"
QUALITIES = ("low", "medium", "high", "xhigh", "max")
# Per render master (render_masters / brand/platform_formats.json). Request
# sizes are exact ratios with both edges multiples of 16, inside
# 655,360..8,294,400 px; output is the platform canvas, resized with no crop.
#   public_carousel_4x5: 1088x1360 (1.48 MP) -> 1080x1350  Instagram/Threads/X
#   vertical_9x16:       1152x2048 (2.36 MP) -> 1080x1920  TikTok/Fanvue only
REQUEST_SIZES = {"public_carousel_4x5": (1088, 1360), "vertical_9x16": (1152, 2048)}
REQUEST_SIZE = REQUEST_SIZES["public_carousel_4x5"]
OUTPUT_SIZE = (1080, 1350)          # the public carousel master (default)

PRICES_USD_PER_M = {"text_input": 5.00, "image_input": 8.00, "image_output": 30.00}
PRICES_SOURCE = "developers.openai.com/api/docs/models/gpt-image-2.5-sunburst (read 2026-09-25)"
_BASE = {"low": 16, "medium": 24, "high": 48, "xhigh": 64, "max": 96}   # gpt-image-2.5
# Conservative per-reference bound: gpt-image-1's documented high-fidelity rule
# (65 base + 129/tile at 512px short side + 6240 for non-square). Not a GPT
# Image 2.5 rule -- only a ceiling for the pre-call guard.
_REF_TOKENS_LOW, _REF_TOKENS_HIGH = 1_000, 7_000

AUTHORITY_LAYERS = ("canonical identity", "explicit story facts", "environment master",
                    "approved story state", "previous approved slide", "creative styling")


class OpenAIImageError(RuntimeError):
    pass


def output_tokens(width: int, height: int, quality: str) -> int:
    """OpenAI's published calculator formula for GPT Image 2.5 output tokens."""
    o = _BASE[quality]
    long_, short = max(width, height), min(width, height)
    s = o / (long_ / short)
    fl = math.floor(s)
    u = fl + fl % 2 if s - fl == 0.5 else round(s)
    d = (o if width >= height else u) * (u if width >= height else o)
    return math.ceil(d * (2e6 + width * height) / 4e6)


def estimate_cost(prompt: str, n_refs: int, quality: str = DEFAULT_QUALITY,
                  size: tuple[int, int] = REQUEST_SIZE) -> dict:
    out_tok = output_tokens(*size, quality)
    text_tok = math.ceil(len(prompt) / 4)            # rough; exact count comes from usage
    p = PRICES_USD_PER_M
    out_usd = out_tok * p["image_output"] / 1e6
    text_usd = text_tok * p["text_input"] / 1e6
    lo = out_usd + text_usd + n_refs * _REF_TOKENS_LOW * p["image_input"] / 1e6
    hi = out_usd + text_usd * 1.3 + n_refs * _REF_TOKENS_HIGH * p["image_input"] / 1e6
    return {"quality": quality, "size": f"{size[0]}x{size[1]}", "output_tokens": out_tok,
            "output_usd": round(out_usd, 5), "text_input_tokens_approx": text_tok,
            "reference_images": n_refs, "estimated_usd_low": round(lo, 4),
            "estimated_usd_high": round(hi, 4), "prices_usd_per_million": dict(p),
            "prices_source": PRICES_SOURCE,
            "note": "output tokens exact per OpenAI's formula; text approx; image-input "
                    "tokens for GPT Image 2.5 are unpublished, so bounded 1k-7k per reference"}


def cost_from_usage(usage: dict | None) -> float | None:
    """USD from the usage object OpenAI returns (input_tokens_details split)."""
    if not usage:
        return None
    det = usage.get("input_tokens_details") or {}
    text_in = det.get("text_tokens")
    img_in = det.get("image_tokens")
    out = usage.get("output_tokens")
    if text_in is None and img_in is None:
        text_in, img_in = usage.get("input_tokens"), 0      # no split: price all input as text
    if out is None or text_in is None:
        return None
    p = PRICES_USD_PER_M
    return round((text_in * p["text_input"] + (img_in or 0) * p["image_input"]
                  + out * p["image_output"]) / 1e6, 5)


# --- references ---------------------------------------------------------------
def generation_identity_references(project_root: Path) -> list[dict]:
    """The clean, generation-facing identity crops registered in
    identity_spec.generation_references (pure crops of reference_primary that
    show no human ears and no printed text). Empty if none are registered."""
    import json

    spec = json.loads((project_root / "brand/nyx_identity/identity_spec.json").read_text())
    gen = spec.get("generation_references") or {}
    out = []
    for img in gen.get("images") or []:
        out.append({"role": f"identity_generation_ref_{img['order']}", "kind": "local",
                    "base": "project", "value": img["file"], "authority": 1,
                    "never_identity": False, "decides": f"identity only -- {img['shows']}",
                    "expected_sha256_16": img.get("sha256_16")})
    return out


def resolve_references(references: list[dict], *, project_root: Path, package_dir: Path,
                       identity_override: list[dict] | None = None) -> list[dict]:
    """Local files to upload, in authority order, one per distinct image. The
    hosted URL entry duplicates the local primary, so it is skipped when the
    local file is present (OpenAI's edit endpoint takes files, not URLs).
    identity_override replaces every identity reference (the master sheets) with
    the registered clean crops; environment / previous-slide refs are kept."""
    if identity_override:
        references = identity_override + [r for r in references
                                          if not str(r.get("role", "")).startswith("identity_reference")]
    out, seen = [], set()
    for r in sorted(references, key=lambda r: r.get("authority", 99)):
        if r.get("kind") != "local":
            continue
        base = project_root if r.get("base") == "project" else package_dir
        path = (base / r["value"]).resolve()
        if not path.is_file():
            raise OpenAIImageError(f"reference {r.get('role')} is missing on disk: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        if r.get("expected_sha256_16") and r["expected_sha256_16"] != digest:
            raise OpenAIImageError(f"{r['role']} does not match its registered hash -- refusing")
        if digest in seen:
            continue
        seen.add(digest)
        out.append({"role": r.get("role"), "authority": r.get("authority"),
                    "decides": r.get("decides", ""), "never_identity": bool(r.get("never_identity")),
                    "path": str(path), "sha256_16": digest,
                    "simulated": bool(r.get("simulated"))})
    if not out or out[0]["never_identity"]:
        raise OpenAIImageError("the canonical identity reference must be the first image")
    return out


def reference_preamble(refs: list[dict]) -> str:
    lines = ["0. REFERENCE IMAGES (attached in this order; each may decide ONLY what is stated)"]
    for i, r in enumerate(refs, 1):
        if r["never_identity"]:
            who = "NOT evidence for who Nyx is"
        elif i == 1:
            who = "the SAME woman, Nyx Emberfall -- the canonical identity master, highest authority"
        elif r["role"].startswith("identity_generation_ref"):
            who = "the same woman, same canonical source as Image 1 -- identity detail, never a different look"
        else:
            who = ("the same woman from other angles -- SUPPORTING identity detail only, it never "
                   "overrides Image 1; ignore any printed text and any human ears on this sheet")
        lines.append(f"Image {i} [{r['role']}]: {r['decides']} ({who}).")
    lines.append("If an image and the text disagree about identity, Image 1 wins; the environment "
                 "and previous-slide images never change her face, eyes, ears, hair, skin or marks.")
    lines.append("Precedence on conflict: " + " > ".join(AUTHORITY_LAYERS) + ".")
    return "\n".join(lines)


def request_spec(compiled_prompt: str, refs: list[dict], quality: str = DEFAULT_QUALITY,
                 master_id: str = "public_carousel_4x5") -> dict:
    import render_masters

    if quality not in QUALITIES:
        raise OpenAIImageError(f"quality must be one of {QUALITIES}")
    if master_id not in REQUEST_SIZES:
        raise OpenAIImageError(f"no request size for render master {master_id!r}")
    m = render_masters.master(master_id)
    req, out = REQUEST_SIZES[master_id], (m["width"], m["height"])
    prompt = reference_preamble(refs) + "\n\n" + compiled_prompt
    return {"provider": PROVIDER, "endpoint": "POST /v1/images/edits", "model": MODEL,
            "quality": quality, "size": f"{req[0]}x{req[1]}",
            "aspect_ratio": m["aspect"], "render_master": master_id,
            "output_size": list(out), "n": 1, "output_format": "png",
            "references": refs, "prompt": prompt,
            "prompt_sha256_16": hashlib.sha256(prompt.encode()).hexdigest()[:16],
            "post_processing": f"resize {req[0]}x{req[1]} -> "
                               f"{out[0]}x{out[1]} (same {m['aspect']}, no crop), PNG",
            "estimate": estimate_cost(prompt, len(refs), quality, size=req)}


# --- the one call -----------------------------------------------------------------
def _client():
    import openai_runtime
    from openai import OpenAI

    key = openai_runtime.load_api_key()
    if not key:
        raise OpenAIImageError("no OpenAI API key stored (openai_runtime) -- nothing sent")
    return OpenAI(api_key=key, max_retries=0, timeout=600)   # no silent re-submission


def to_output(raw_png: bytes, size: tuple[int, int] = OUTPUT_SIZE) -> bytes:
    from PIL import Image

    im = Image.open(io.BytesIO(raw_png)).convert("RGB")
    buf = io.BytesIO()
    im.resize(tuple(size), Image.LANCZOS).save(buf, format="PNG")
    return buf.getvalue()


def generate(spec: dict, *, out_path: Path, max_usd: float, client=None) -> dict:
    """ONE images.edit call. Refuses before sending if the estimate's upper
    bound exceeds max_usd. Returns {ok, submitted, usage, actual_usd, ...};
    raises only when the outcome of a sent request is unknown (timeout etc.)."""
    est = spec["estimate"]
    if est["estimated_usd_high"] > max_usd:
        return {"ok": False, "submitted": False, "status": "REFUSED_COST", "actual_usd": 0,
                "problems": [f"estimate up to ${est['estimated_usd_high']} > grant ${max_usd}"]}
    client = client or _client()
    files = [open(r["path"], "rb") for r in spec["references"]]
    try:
        result = client.images.edit(model=spec["model"], image=files, prompt=spec["prompt"],
                                    size=spec["size"], quality=spec["quality"], n=1,
                                    output_format="png")
    finally:
        for f in files:
            f.close()
    data = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    usage = data.get("usage")
    actual = cost_from_usage(usage)
    problems = []
    items = data.get("data") or []
    b64 = items[0].get("b64_json") if items else None
    if not b64:
        problems.append("no image returned")
    if actual is None:
        problems.append("usage missing -- actual cost unknown")
    elif actual > max_usd:
        problems.append(f"actual ${actual} exceeded grant ${max_usd}")
    base = {"submitted": True, "usage": usage, "actual_usd": actual or 0,
            "actual_usd_known": actual is not None, "created": data.get("created"),
            "revised_prompt": items[0].get("revised_prompt") if items else None}
    if not b64:
        return {**base, "ok": False, "status": "NO_IMAGE", "problems": problems}
    raw = base64.b64decode(b64)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = out_path.with_name(f"{out_path.stem}.{PROVIDER}_raw.png")
    raw_path.write_bytes(raw)
    size = tuple(spec.get("output_size") or OUTPUT_SIZE)
    out_path.write_bytes(to_output(raw, size))
    return {**base, "ok": not [p for p in problems if "exceeded" in p], "status": "GENERATED",
            "problems": problems, "path": str(out_path), "raw_path": str(raw_path),
            "raw_sha256_16": hashlib.sha256(raw).hexdigest()[:16],
            "sha256_16": hashlib.sha256(out_path.read_bytes()).hexdigest()[:16],
            "dimensions": f"{size[0]}x{size[1]}"}
