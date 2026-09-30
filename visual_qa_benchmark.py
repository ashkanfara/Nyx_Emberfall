"""Deterministic visual-QA benchmark for Nyx: identity anchors, room anchors,
platform aspect ratios and safe text zones (Instagram, TikTok, Threads, X,
Fanvue).

Read-only by construction: stdlib only, reads files already in this repo,
writes exactly two files (qa_benchmark/report.json + REPORT.md). It never
generates, uploads, opens a platform, publishes or spends -- same boundary
as NYX_PRODUCTION_CONTRACT.md. Same inputs always produce byte-identical
output (no timestamps, sorted everything), so a coordinator can diff runs.

    python3 visual_qa_benchmark.py            # write report
    python3 visual_qa_benchmark.py --check    # exit 1 if committed report is stale
    python3 visual_qa_benchmark.py --strict   # exit 1 if any case FAILs
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import struct
import sys
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QA_DIR = ROOT / "qa_benchmark"
MANIFEST = QA_DIR / "test_manifest.json"
REPORT_JSON = QA_DIR / "report.json"
REPORT_MD = QA_DIR / "REPORT.md"

PASS, WARN, FAIL, NA = "PASS", "WARN", "FAIL", "N/A"
# Which platform format a story_carousel lock is judged against.
LOCK_FORMAT = {"instagram": "feed_carousel", "tiktok": "video_or_photo_mode",
               "threads": "feed_image", "x": "timeline_single_image", "fanvue": "feed_post"}
OVERLAY_ASSUMED_LINES = 2   # carousel_handoff wraps short hooks to 1-3 lines; 2 is the median case


# --- small pure helpers -----------------------------------------------------

def sha16(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def image_size(path: Path) -> tuple[int, int] | None:
    """PNG / WebP header dimensions without Pillow."""
    b = path.read_bytes()[:64]
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", b[16:24])
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        chunk = b[12:16]
        if chunk == b"VP8X":
            return (int.from_bytes(b[24:27], "little") + 1, int.from_bytes(b[27:30], "little") + 1)
        if chunk == b"VP8 ":
            w, h = struct.unpack("<HH", b[26:30])
            return (w & 0x3FFF, h & 0x3FFF)
        if chunk == b"VP8L":
            v = int.from_bytes(b[21:25], "little")
            return ((v & 0x3FFF) + 1, ((v >> 14) & 0x3FFF) + 1)
    return None


def ratio(aspect: str) -> float:
    w, h = aspect.split(":")
    return int(w) / int(h)


def aspect_name(w: int, h: int, names, tol: float) -> str | None:
    r = w / h
    return next((n for n in names if abs(r - ratio(n)) <= tol), None)


def load_json(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text())


def graded(fmt: dict) -> str:
    """A breach of a low-confidence (unpublished) platform rule is a WARN, not a FAIL."""
    return WARN if fmt.get("confidence") == "low" else FAIL


def case(cid, category, subject, status, detail, **evidence) -> dict:
    return {"id": cid, "category": category, "subject": subject, "status": status,
            "detail": detail, "evidence": evidence}


# --- identity ---------------------------------------------------------------

def identity_cases(m: dict) -> list[dict]:
    spec = load_json(m["sources"]["identity_spec"])
    out = []
    refs = [(r["project_file"], r["sha256_16"], None) for r in spec["reference_hierarchy"]]
    refs += [(g["file"], g["sha256_16"], g["crop_box"]) for g in spec["generation_references"]["images"]]
    for rel, want, crop in refs:
        p = ROOT / rel
        if not p.is_file():
            out.append(case("ID-REF", "identity", rel, FAIL, "reference file missing"))
            continue
        got = sha16(p)
        out.append(case("ID-REF", "identity", rel, PASS if got == want else FAIL,
                        "hash matches identity_spec" if got == want else "hash drift vs identity_spec",
                        expected=want, actual=got))
        if crop:
            size = image_size(p)
            box = (crop[2] - crop[0], crop[3] - crop[1])
            out.append(case("ID-CROP", "identity", rel, PASS if tuple(size) == box else FAIL,
                            "pixel size equals crop_box extent (pure crop, no resample)",
                            crop_box=crop, size=list(size)))

    traits = set(spec["locked_traits"])
    covered = {a["trait_key"] for a in m["identity_anchors"]}
    missing, extra = sorted(traits - covered), sorted(covered - traits)
    out.append(case("ID-ANCHOR-COVERAGE", "identity", "identity_spec.locked_traits",
                    PASS if not missing and not extra else FAIL,
                    "every locked trait has a QA anchor" if not missing and not extra
                    else "manifest identity_anchors out of sync with locked_traits",
                    missing=missing, extra=extra))

    block = spec["base_generation_prompt"]["identity_block"]
    absent = [p for p in m["identity_required_phrases"] if p not in block]
    out.append(case("ID-PROMPT", "identity", "base_generation_prompt.identity_block",
                    PASS if not absent else FAIL,
                    "identity block carries every hard-anchor phrase" if not absent
                    else "identity block lost a hard-anchor phrase", absent=absent))

    demoted = [d["file"] for d in spec["generation_references"].get("demoted_from_generation", [])]
    out.append(case("ID-GEN-INPUTS", "identity", "generation_references", WARN if demoted else PASS,
                    "Whole master sheets contain human-ear profile panels and printed text; only the 3 "
                    "clean crops may be fed to a generator. Wrong input here reproduces the human-ear "
                    "failure seen in the 2026-09-29 handoff.", demoted=demoted))
    return out


# --- rooms ------------------------------------------------------------------

def load_locks(m: dict) -> list[dict]:
    locks = [json.loads(Path(f).read_text()) for f in sorted(glob.glob(str(ROOT / m["sources"]["story_locks_glob"])))]
    return sorted(locks, key=lambda d: (d.get("item_index", 0), d["story_id"]))


def room_cases(m: dict, locks: list[dict]) -> tuple[list[dict], dict]:
    rules = m["room_rules"]
    out, by_loc = [], {}
    for d in locks:
        env = d["environment_lock"]
        n = len(env.get("anchors", []))
        ok = bool(env.get("location_id")) and n >= rules["min_anchors_per_lock"] and env.get("geometry_rule")
        out.append(case("RM-LOCK", "room", d["story_id"], PASS if ok else FAIL,
                        f"{env.get('location_id')}: {n} anchors, geometry_rule "
                        f"{'present' if env.get('geometry_rule') else 'MISSING'}"))
        by_loc.setdefault(env["location_id"], []).append(d)

    registry = {}
    for loc, group in sorted(by_loc.items()):
        base = group[0]
        base_a = {a["id"]: a["description"] for a in base["environment_lock"]["anchors"]}
        registry[loc] = {"reference_lock": base["story_id"], "anchor_ids": sorted(base_a),
                         "used_by": [g["story_id"] for g in group]}
        for d in group[1:]:
            a = {x["id"]: x["description"] for x in d["environment_lock"]["anchors"]}
            added = sorted(set(a) - set(base_a))
            removed = sorted(set(base_a) - set(a))
            changed = sorted(k for k in set(a) & set(base_a) if a[k] != base_a[k])
            if removed or changed:
                st, why = FAIL, "anchor changed/removed vs reference lock -- same location_id, different room"
            elif added:
                st, why = PASS, "additive only (new permanent prop)"
            else:
                st, why = PASS, "identical anchor set"
            out.append(case("RM-CONSISTENCY", "room", f"{loc}: {d['story_id']} vs {base['story_id']}",
                            st, why, added=added, removed=removed, changed=changed))

        idxs = sorted({g["item_index"] for g in group})
        present = [i for i in idxs if any((ROOT / f"generated_assets/carousel_item{i}").glob("*.png"))]
        out.append(case("RM-MASTER-IMAGE", "room", loc, PASS if present else WARN,
                        "approved room master image on disk" if present else
                        "Room anchors are TEXT ONLY in git. The approved slide-1 masters live in "
                        "generated_assets/ (gitignored, absent here), so no image room anchor can be "
                        "QA'd against in this checkout.", item_indexes=idxs, found_in=present))

    homes = sorted(loc for loc, g in by_loc.items()
                   if "nyx's own" in g[0]["environment_lock"].get("summary", "").lower())
    out.append(case("RM-HOME-CANON", "room", "Nyx's own home", WARN if len(homes) > 1 else PASS,
                    f"{len(homes)} different rooms are each described as Nyx's own home. Season 1 locks "
                    "use nyx_loft_star_atlas; the two bedrooms are legacy and must never be mixed into "
                    "a Season 1 episode.", locations=homes))
    return out, registry


# --- formats + safe zones ---------------------------------------------------

def lock_platforms(d: dict, formats: dict) -> list[str]:
    p = d["target_platform"]
    return formats["public_multi_expands_to"] if p == "public_multi" else [p]


def format_cases(m: dict, locks: list[dict], formats: dict) -> list[dict]:
    out, tol = [], formats["aspect_tolerance"]
    plats = formats["platforms"]

    for d in locks:
        for p in lock_platforms(d, formats):
            fmt = plats[p]["formats"][LOCK_FORMAT[p]]
            ok = d["aspect_ratio"] in fmt["accepted_aspects"]
            detail = (f"{d['aspect_ratio']} accepted by {p}.{LOCK_FORMAT[p]}" if ok else
                      f"{d['aspect_ratio']} not accepted by {p}.{LOCK_FORMAT[p]} "
                      f"(accepts {', '.join(fmt['accepted_aspects'])}). {fmt.get('crop_behaviour', '')}".strip())
            out.append(case("FMT-LOCK", "format", f"{d['story_id']} -> {p}", PASS if ok else graded(fmt), detail,
                            lock_aspect=d["aspect_ratio"], platform_format=LOCK_FORMAT[p]))
            if not ok and ratio(d["aspect_ratio"]) < min(ratio(a) for a in fmt["accepted_aspects"]):
                keep = ratio(d["aspect_ratio"]) / min(ratio(a) for a in fmt["accepted_aspects"])
                lost = round((1 - keep) / 2, 3)
                margin = _overlay(m)["margin"]
                out.append(case("SZ-CROP", "safe_zone", f"{d['story_id']} -> {p}", graded(fmt),
                                f"Centre-crop removes {lost:.1%} at top and bottom; overlays anchored at a "
                                f"{margin:.0%} margin land inside the cropped band and are cut off.",
                                cropped_each_edge=lost, overlay_margin=margin))

    lock_aspects = sorted({d["aspect_ratio"] for d in locks})
    for src in m["sources"]["pipeline_output_constants"]:
        text = (ROOT / src["file"]).read_text()
        hit = re.search(src["pattern"], text)
        if not hit:
            out.append(case("FMT-PIPELINE", "format", src["file"], WARN, "output-size constant not found"))
            continue
        w, h = int(hit.group(1)), int(hit.group(2))
        name = aspect_name(w, h, lock_aspects, tol) or f"{w}x{h}"
        bad = sorted(d["story_id"] for d in locks if abs(w / h - ratio(d["aspect_ratio"])) > tol)
        out.append(case("FMT-PIPELINE", "format", f"{src['file']} ({w}x{h}, {name})",
                        FAIL if bad else PASS,
                        "hardcoded output size ignores the lock's aspect_ratio; these locks would be "
                        "generated at the wrong shape" if bad else "matches every lock",
                        mismatched_locks=bad))
    return out


def _overlay(m: dict) -> dict:
    src = m["sources"]["overlay_policy"]
    text = (ROOT / src["file"]).read_text()
    vals = {}
    for c in src["constants"]:
        hit = re.search(rf"^{c}\s*=\s*([0-9.]+)", text, re.M)
        vals[c] = float(hit.group(1))
    return {"margin": vals["OVERLAY_MARGIN_PCT"] / 100, "max_width": vals["OVERLAY_MAX_WIDTH_PCT"] / 100,
            "font": vals["OVERLAY_FONT_PCT"] / 100, "spacing": vals["OVERLAY_LINE_SPACING"], "raw": vals}


def safe_zone_cases(m: dict, locks: list[dict], formats: dict) -> list[dict]:
    """Place carousel_handoff's overlay block (top-left and bottom-left anchors,
    worst-case full max width) on every platform canvas and test it against
    that platform's safe text zone. Margins are fractions of height
    vertically and of width horizontally, exactly as apply_overlays() does."""
    ov, out = _overlay(m), []
    block_h = ov["font"] * ov["spacing"] * OVERLAY_ASSUMED_LINES      # fraction of height
    x0, x1 = round(ov["margin"], 4), round(ov["margin"] + ov["max_width"], 4)   # fraction of width
    for p, spec in sorted(formats["platforms"].items()):
        for fname, fmt in sorted(spec["formats"].items()):
            z = fmt["safe_text_zone"]
            for anchor, (y0, y1) in (("top", (ov["margin"], ov["margin"] + block_h)),
                                     ("bottom", (1 - ov["margin"] - block_h, 1 - ov["margin"]))):
                y0, y1 = round(y0, 4), round(y1, 4)
                breaches = []
                if y0 < z["top"]:
                    breaches.append(f"top edge at {y0:.1%} < safe {z['top']:.0%}")
                if y1 > round(1 - z["bottom"], 4):
                    breaches.append(f"bottom edge at {y1:.1%} > safe {1 - z['bottom']:.0%}")
                if x0 < z["left"]:
                    breaches.append(f"left edge at {x0:.0%} < safe {z['left']:.0%}")
                if x1 > round(1 - z["right"], 4):
                    breaches.append(f"right edge at {x1:.0%} > safe {1 - z['right']:.0%}")
                out.append(case("SZ-OVERLAY", "safe_zone", f"{p}.{fname} / {anchor} anchor",
                                graded(fmt) if breaches else PASS,
                                "; ".join(breaches) or "overlay block inside safe text zone",
                                block=[round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)],
                                safe_text_zone=z, confidence=fmt["confidence"]))

    for d in locks:
        slides = d["story_plan"]["slides"] if isinstance(d["story_plan"], dict) else d["story_plan"]
        vague = [s.get("slot", s.get("slide_index", i + 1)) for i, s in enumerate(slides)
                 if not re.search(r"upper|lower|top|bottom", str(s.get("overlay_safe_zone") or ""), re.I)]
        out.append(case("SZ-WORDING", "safe_zone", d["story_id"], WARN if vague else PASS,
                        "every slide's overlay_safe_zone resolves to a top/bottom anchor" if not vague else
                        "overlay_safe_zone missing or unresolvable; carousel_handoff silently falls back "
                        "to bottom-left, the anchor most likely to hit platform UI", slides=vague))
    return out


# --- on-disk assets ---------------------------------------------------------

def asset_cases(m: dict, formats: dict) -> list[dict]:
    out, tol = [], formats["aspect_tolerance"]
    all_aspects = sorted({a for p in formats["platforms"].values() for f in p["formats"].values()
                          for a in f["accepted_aspects"]}, key=ratio)
    files = sorted({str(Path(f).relative_to(ROOT)) for g in m["asset_inventory_globs"]
                    for f in glob.glob(str(ROOT / g), recursive=True)})
    for rel in files:
        size = image_size(ROOT / rel)
        use = next((v for k, v in m["asset_intended_use"].items() if fnmatch(rel, k)), None)
        w, h = size
        name = aspect_name(w, h, all_aspects, tol) or f"non-standard {w / h:.3f}"
        ev = {"size": [w, h], "aspect": name, "sha256_16": sha16(ROOT / rel)}
        if not use or not use["platform"]:
            out.append(case("AS-FORMAT", "asset", rel, NA, "not a publishable asset", **ev))
            continue
        fmt = formats["platforms"][use["platform"]]["formats"][use["format"]]
        fits = name in fmt["accepted_aspects"]
        problems = []
        if not fits:
            problems.append(f"{name} not accepted by {use['platform']}.{use['format']} "
                            f"({', '.join(fmt['accepted_aspects'])})")
        if max(w, h) < formats["min_long_edge_px"]:
            problems.append(f"long edge {max(w, h)}px < {formats['min_long_edge_px']}px (upscaled on device)")
        st = FAIL if not fits else (WARN if problems else PASS)
        out.append(case("AS-FORMAT", "asset", rel, st, "; ".join(problems) or "format OK",
                        intended=f"{use['platform']}.{use['format']}", story_id=use["story_id"], **ev))

    for ob in m["recorded_observations"]["items"]:
        p = ROOT / ob["file"]
        cur = sha16(p) if p.is_file() else None
        if cur != ob["sha256_16"]:
            out.append(case("AS-IDENTITY", "asset", ob["file"], WARN,
                            "file changed since recorded review -- observation stale, re-review",
                            recorded=ob["sha256_16"], actual=cur))
            continue
        st = FAIL if ob["fails"] else (WARN if ob.get("na") else PASS)
        out.append(case("AS-IDENTITY", "asset", ob["file"], st, ob["detail"],
                        failed_anchors=ob["fails"], not_verifiable=ob.get("na", []),
                        source="recorded_observation"))
    return out


# --- report -----------------------------------------------------------------

def platform_readiness(cases: list[dict], formats: dict) -> dict:
    ready = {}
    for p in sorted(formats["platforms"]):
        mine = [c for c in cases if f"-> {p}" in c["subject"] or c["subject"].startswith(f"{p}.")
                or c["evidence"].get("intended", "").startswith(f"{p}.")]
        blocking = sorted(c["id"] + ": " + c["subject"] for c in mine if c["status"] == FAIL)
        warns = sorted(c["id"] + ": " + c["subject"] for c in mine if c["status"] == WARN)
        ready[p] = {"status": "BLOCKED" if blocking else ("READY_WITH_WARNINGS" if warns else "READY"),
                    "channel_status_ref": formats["platforms"][p]["channel_status_ref"],
                    "blocking": blocking, "warnings": warns}
    return ready


# Priority order: the first entry is the first thing the coordinator should act on.
NEXT_ACTIONS = {
    "AS-IDENTITY": "Do not publish the 2026-09-29 handoff set as Nyx: recorded review finds human ears, a banded "
                   "tail and hair/face drift. Treat it as off-model; re-generate through the contract flow if the "
                   "stories still matter.",
    "FMT-PIPELINE": "Make openai_image_provider.OUTPUT_SIZE and story_continuity's output width/height derive from "
                    "the lock's aspect_ratio (9:16 -> 1080x1920, 4:5 -> 1080x1350). Code change, zero spend.",
    "FMT-LOCK": "Instagram-bound locks are 9:16 but IG carousels max out at 4:5. Either re-lock IG stories to 4:5, "
                "or produce a 4:5 IG master plus a 9:16 TikTok/Fanvue master per slide.",
    "SZ-OVERLAY": "carousel_handoff's single 7% overlay margin puts text inside the UI zones of every 9:16 surface. "
                  "Read the margin per platform from qa_benchmark/platform_formats.json instead.",
    "SZ-CROP": "Until FMT-LOCK is fixed, any 9:16 slide sent to an IG carousel loses its overlay text to the crop.",
    "RM-CONSISTENCY": "star_map_001 and the_charm_001 share location_id nyx_apartment_bedroom with different anchors "
                      "(window, trunk). Reconcile them or split the id. Founder call, text-only edit.",
    "AS-FORMAT": "x_story_image is ~2:3 and gets centre-cropped on X; rebuild at 4:5 if X is ever connected.",
}


def build_report() -> dict:
    m = json.loads(MANIFEST.read_text())
    formats = load_json(m["sources"]["platform_formats"])
    locks = load_locks(m)
    rooms, registry = room_cases(m, locks)
    cases = (identity_cases(m) + rooms + format_cases(m, locks, formats)
             + safe_zone_cases(m, locks, formats) + asset_cases(m, formats))
    counts = {s: sum(c["status"] == s for c in cases) for s in (PASS, WARN, FAIL, NA)}
    failing_ids = sorted({c["id"] for c in cases if c["status"] == FAIL})
    return {
        "benchmark_id": m["benchmark_id"],
        "schema_version": m["schema_version"],
        "as_of": m["as_of"],
        "inputs_sha256_16": {rel: sha16(ROOT / rel) for rel in sorted(
            [str(MANIFEST.relative_to(ROOT)), m["sources"]["platform_formats"], m["sources"]["identity_spec"]]
            + [str(Path(f).relative_to(ROOT)) for f in glob.glob(str(ROOT / m["sources"]["story_locks_glob"]))])},
        "coordinator_summary": {
            "overall": FAIL if counts[FAIL] else (WARN if counts[WARN] else PASS),
            "counts": counts,
            "failing_case_ids": failing_ids,
            "platform_readiness": platform_readiness(cases, formats),
            "next_actions": [a for i, a in NEXT_ACTIONS.items() if i in failing_ids],
            "spend_usd": 0, "generations": 0, "uploads": 0, "publishes": 0,
        },
        "identity_anchors": m["identity_anchors"],
        "room_registry": registry,
        "overlay_policy": _overlay(m)["raw"],
        "cases": cases,
    }


def render_md(r: dict) -> str:
    s = r["coordinator_summary"]
    lines = [f"# Nyx visual QA benchmark -- {r['as_of']}", "",
             "Generated by `visual_qa_benchmark.py` from `qa_benchmark/test_manifest.json`. Do not hand-edit.", "",
             f"**Overall: {s['overall']}** -- " + ", ".join(f"{k} {v}" for k, v in s["counts"].items()),
             "", "Spend $0, 0 generations, 0 uploads, 0 publishes.", "", "## Platform readiness", "",
             "| Platform | Status | Blocking cases |", "|---|---|---|"]
    for p, v in s["platform_readiness"].items():
        lines.append(f"| {p} | {v['status']} | {len(v['blocking'])} |")
    lines += ["", "## Next actions", ""] + [f"{i}. {a}" for i, a in enumerate(s["next_actions"], 1)]
    lines += ["", "## Room registry", "", "| location_id | reference lock | used by |", "|---|---|---|"]
    for loc, v in r["room_registry"].items():
        lines.append(f"| {loc} | {v['reference_lock']} | {', '.join(v['used_by'])} |")
    lines += ["", "## Cases", "", "| Status | ID | Subject | Detail |", "|---|---|---|---|"]
    order = {FAIL: 0, WARN: 1, PASS: 2, NA: 3}
    for c in sorted(r["cases"], key=lambda c: (order[c["status"]], c["id"], c["subject"])):
        lines.append(f"| {c['status']} | {c['id']} | {c['subject']} | {c['detail'].replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def serialise(r: dict) -> tuple[str, str]:
    return json.dumps(r, indent=2, sort_keys=True, ensure_ascii=False) + "\n", render_md(r)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if committed report is stale")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any case FAILs")
    a = ap.parse_args(argv)
    js, md = serialise(build_report())
    if a.check:
        stale = [p.name for p, want in ((REPORT_JSON, js), (REPORT_MD, md))
                 if not p.is_file() or p.read_text() != want]
        if stale:
            print("stale:", ", ".join(stale), "-- run python3 visual_qa_benchmark.py")
            return 1
    else:
        REPORT_JSON.write_text(js)
        REPORT_MD.write_text(md)
    overall = json.loads(js)["coordinator_summary"]
    print(f"overall={overall['overall']} counts={overall['counts']}")
    return 1 if a.strict and overall["counts"][FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
