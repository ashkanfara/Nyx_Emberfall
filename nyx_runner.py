"""Autonomous Nyx episode runner -- one trigger, no manual relay.

Once a story lock is approved and enrolled in episodes/runner_registry.json,
`python3 nyx_runner.py tick` advances it as far as real evidence allows and
exits. Run it on a timer (ops/com.nyx.episode-runner.plist: every 15 min and
at login) and nobody has to say "move forward": each tick re-derives where the
episode is from what is on disk, so a crash, an app restart or a browser
restart just means the next tick picks up where the evidence left off.

    approved lock -> brief + locked prompts          (episode_coordinator)
      -> per slide, in order:                         (story_continuity gates)
           handoff packet  -> the generation EXECUTOR drops an image + provenance
           intake          -> provenance + deterministic technical gate
           visual QA       -> a reviewer that actually opens the image; verdict bound
                              to the file's sha256, recorded via story_continuity.qa_slide
           reject          -> archive, targeted repair prompt, next attempt (bounded by
                              the lock's retry.max_attempts_per_slide), then a named gap
      -> captions (reused if valid, else authored + validated)
      -> per-platform variants (local render: overlays, TikTok slideshow)
      -> release queue: READY_FOR_PUBLISH, never published by this module
    A Fanvue extended chapter is its own approved lock, enrolled with `parent`,
    and runs through the same pipeline into the same queue.

Hard boundaries (brand/NYX_PRODUCTION_CONTRACT.md + founder 2026-09-30):
  * Nothing here generates an image, opens a browser or social site, uploads,
    posts, comments, spends or changes an account. Generation belongs to the
    executor; publishing to a human who confirms at action time
    (`confirm-published` only records what a human already did).
  * No evidence, no progress: an image without matching provenance is never
    QA'd, a slide is approved only by a reviewer verdict on the exact bytes on
    disk, and SIMULATED adapters/provenance are refused outside a sandbox.
  * The Reddit workstream is owned by a separate agent; this module never reads
    or writes it.

    python3 nyx_runner.py tick                  # the trigger (idempotent, safe to over-run)
    python3 nyx_runner.py status                # concise report (episodes/RUNNER_STATUS.md)
    python3 nyx_runner.py record-asset <story_id> <slot> <image.png> --by <who> --provider <name>
    python3 nyx_runner.py confirm-published <story_id> <platform> <url> --by <who>
    python3 nyx_runner.py unblock <story_id>    # after fixing a reviewer/caption/render outage
    python3 nyx_runner.py simulate [--interrupt] # full simulated episode in a temp sandbox
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import carousel_handoff
import channels
import episode_coordinator as ec
import story_continuity as sc

ROOT = Path(__file__).resolve().parent
REGISTRY_NAME = "runner_registry.json"
QUEUE_NAME = "release_queue.json"
STATUS_NAME = "RUNNER_STATUS.md"
LOCK_NAME = ".runner.lock"
CANVAS = {"4:5": (1080, 1350), "9:16": (1080, 1920)}
MAX_TICK_STEPS = 200            # a tick stops here even if it could go on -- no runaway loop
MAX_REVIEWER_FAILURES = 5       # then the slide is BLOCKED on the reviewer, not retried forever
MAX_CAPTION_ATTEMPTS = 3

READY_FOR_PUBLISH = "READY_FOR_PUBLISH"
HELD_NO_ROUTE = "HELD_NO_ROUTE"
PUBLISHED_CONFIRMED = "PUBLISHED_CONFIRMED_BY_HUMAN"


class Unavailable(RuntimeError):
    """A dependency is down or missing right now (CLI, quota, Pillow, ffmpeg).
    Never a verdict: the step is retried on a later tick with backoff."""


class RunnerError(ValueError):
    """A refusal that must never be worked around."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def write_json_atomic(path: Path, data) -> None:
    """Write-then-rename: a crash leaves either the old file or the new one,
    never half of each -- the property every resume below depends on."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


# --- deterministic technical gate --------------------------------------------------------
def png_info(path: Path) -> dict:
    """PNG header + pixel-variety check, stdlib only. Catches the failure a
    person cannot see in a status line: a blank, flat or wrong-size file."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return {"ok": False, "reason": "not a PNG file"}
    width, height = struct.unpack(">II", data[16:24])
    idat, pos = bytearray(), 8
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        if kind == b"IDAT":
            idat += data[pos + 8:pos + 8 + length]
        pos += 12 + length
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error:
        return {"ok": False, "reason": "corrupt PNG pixel data", "width": width, "height": height}
    step = max(1, len(raw) // 20000)
    variety = len(set(raw[::step]))
    return {"ok": True, "width": width, "height": height, "distinct_byte_values": variety}


def technical_gate(path: Path, aspect: str) -> tuple[bool, str]:
    want = CANVAS.get(aspect, CANVAS["4:5"])
    info = png_info(path)
    if not info["ok"]:
        return False, info["reason"]
    if (info["width"], info["height"]) != want:
        return False, f"{info['width']}x{info['height']}, expected {want[0]}x{want[1]} ({aspect})"
    if info["distinct_byte_values"] < 16:
        return False, "blank or flat image (fewer than 16 distinct pixel byte values)"
    return True, f"{want[0]}x{want[1]} PNG, {info['distinct_byte_values']} distinct byte values"


# --- adapters -------------------------------------------------------------------------------
class ClaudeVisionReviewer:
    """Real visual QA through the existing `claude -p` integration
    (claude_client, the same route stages.py uses): the reviewer is told to
    OPEN the candidate and the reference images with its Read tool and score
    every dimension with a concrete observation. It never sees a filename as
    evidence -- the runner binds its verdict to the file's sha256."""
    name, simulated = "claude_cli_vision", False

    def review(self, req: dict) -> dict:
        import claude_client
        lines = [
            "You are the visual QA reviewer for one generated image in the Nyx Emberfall story "
            "pipeline. Use the Read tool to OPEN each image below and look at it. Do not judge "
            "from filenames or from this text.",
            f"CANDIDATE (the image under review): {req['asset_abs']}",
        ]
        for ref in req["references_abs"]:
            lines.append(f"REFERENCE ({ref['role']}, decides {ref['decides']}): {ref['path']}")
        lines += [
            f"Slide {req['slide_index']} of story {req['story_id']}, character presence: "
            f"{req['character_presence']}.",
            "Expectations per dimension (JSON):", json.dumps(req["expectations"], ensure_ascii=False),
            "Reply with ONLY a JSON object: {\"dimensions\": {<dimension>: {\"score\": \"PASS\" or "
            "\"FAIL\", \"observation\": \"what you actually see in the candidate\"}}, \"notes\": "
            "\"...\"}. Every dimension must be present. When unsure, FAIL.",
        ]
        try:
            out = claude_client.claude_call("\n".join(lines), tools=["Read"], timeout=420)
            return {**claude_client.extract_json(out["text"]), "_model": claude_client.MODEL}
        except (claude_client.LLMError, ValueError) as exc:
            raise Unavailable(f"vision reviewer: {exc}") from exc


class ClaudeCaptionAuthor:
    """Caption drafting through the same `claude -p` route. Output is always
    validated by episode_coordinator.stage_captions before it is used."""
    name, simulated = "claude_cli_captions", False

    def write(self, req: dict) -> dict:
        import claude_client
        prompt = (
            "Write social captions for one Nyx Emberfall story episode, in Nyx's own first-person "
            "voice: short, natural, lowercase-casual like the overlay lines, no em dashes, no "
            "engagement bait, never narrated in third person.\n"
            f"Platforms and max characters: {json.dumps(req['limits'])}\n"
            f"Platforms WITHOUT a native AI label (the caption must include #AIgenerated): "
            f"{req['needs_disclosure']}\n"
            f"The slides, in order (beat and overlay line): {json.dumps(req['slides'], ensure_ascii=False)}\n"
            f"If the last overlay ends in a question, the first platform's caption must ask it.\n"
            f"Problems with the previous draft, if any: {req.get('problems') or 'none'}\n"
            "No two platforms may share identical text. Reply with ONLY JSON: "
            "{\"captions\": {<platform>: \"...\"}}")
        try:
            out = claude_client.claude_call(prompt, timeout=240)
            return claude_client.extract_json(out["text"])
        except (claude_client.LLMError, ValueError) as exc:
            raise Unavailable(f"caption author: {exc}") from exc


class LocalRenderer:
    """Zero-cost local rendering of approved art into platform files: the
    existing overlay burn-in (carousel_handoff.apply_overlays, Pillow) and a
    TikTok slideshow (ffmpeg). Missing tools raise Unavailable -- nothing is
    faked, the tick just retries later."""
    name, simulated = "local_render", False

    def render(self, job: dict) -> dict:
        item = {"slides": job["brief_slides"]}
        index, root = job["item_index"], job["assets_root"]
        try:
            carousel_handoff.apply_overlays(item, index, root, safe_zones=job["safe_zones"])
        except carousel_handoff.OverlayUnavailable as exc:
            raise Unavailable(f"overlay renderer: {exc} (install Pillow)") from exc
        finals = [Path(f["final_abs"]) for f in job["frames"]]
        out = {}
        for platform in job["platforms"]:
            if platform == "tiktok":
                out[platform] = [self._slideshow(finals, job["package_dir"] / f"item{index}_tiktok.mp4")]
            else:
                out[platform] = finals
        return out

    @staticmethod
    def _slideshow(frames: list[Path], dest: Path) -> Path:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise Unavailable("TikTok slideshow renderer: ffmpeg not found on PATH")
        cmd = [ffmpeg, "-y", "-loglevel", "error"]
        for f in frames:
            cmd += ["-loop", "1", "-t", "3", "-i", str(f)]
        chains = "".join(f"[{i}:v]scale=1080:-2,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}];"
                         for i in range(len(frames)))
        chains += "".join(f"[v{i}]" for i in range(len(frames))) + f"concat=n={len(frames)}:v=1:a=0[out]"
        cmd += ["-filter_complex", chains, "-map", "[out]", "-r", "30", "-pix_fmt", "yuv420p", str(dest)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise Unavailable(f"ffmpeg failed: {proc.stderr[:200]}")
        return dest


# --- environment ------------------------------------------------------------------------------
@dataclass
class Env:
    locks_dir: Path
    episodes_dir: Path
    assets_root: Path
    venture_path: Path
    reviewer: object
    author: object
    renderer: object
    seasons_dir: Path | None = None
    sandbox: bool = False
    clock: object = _utcnow
    checkpoint_hook: object = None       # tests: raise here to simulate a crash
    log: list = field(default_factory=list)

    def checkpoint(self, name: str) -> None:
        if self.checkpoint_hook:
            self.checkpoint_hook(name)

    def guard(self) -> None:
        if not self.sandbox:
            fake = [a.name for a in (self.reviewer, self.author, self.renderer)
                    if getattr(a, "simulated", False)]
            if fake:
                raise RunnerError(f"simulated adapters {fake} are refused outside a sandbox")


def real_env() -> Env:
    return Env(locks_dir=sc.STORY_LOCKS_DIR, episodes_dir=ec.EPISODES_DIR,
               assets_root=carousel_handoff.ASSETS_ROOT, venture_path=ROOT / "state" / "venture.json",
               reviewer=ClaudeVisionReviewer(), author=ClaudeCaptionAuthor(), renderer=LocalRenderer())


# --- per-episode paths and state -------------------------------------------------------------
def load_registry(env: Env) -> list[dict]:
    return (read_json(env.episodes_dir / REGISTRY_NAME, {}) or {}).get("episodes", [])


def state_path(env: Env, story_id: str) -> Path:
    return env.episodes_dir / story_id / "runner_state.json"


def package_dir(env: Env, lock: dict) -> Path:
    return carousel_handoff.package_dir(int(lock["item_index"]), env.assets_root)


def asset_path(env: Env, lock: dict, slot: int) -> Path:
    return sc.candidate_path(int(lock["item_index"]), slot, env.assets_root)


def provenance_path(asset: Path) -> Path:
    return asset.with_suffix(".provenance.json")


def aspect_of(lock: dict) -> str:
    return lock.get("aspect_ratio") if lock.get("aspect_ratio") in CANVAS else "4:5"


def _event(state: dict, env: Env, text: str) -> None:
    state.setdefault("events", []).append({"at": _iso(env.clock()), "event": text})
    state["events"] = state["events"][-60:]
    env.log.append(f"{state['story_id']}: {text}")


def _retry_due(state: dict, key: str, env: Env) -> bool:
    r = (state.get("retry") or {}).get(key)
    return not r or env.clock() >= datetime.fromisoformat(r["next_at"])


def _retry_fail(state: dict, key: str, env: Env, error: str) -> dict:
    r = state.setdefault("retry", {}).setdefault(key, {"failures": 0})
    r["failures"] += 1
    r["last_error"] = error[:300]
    r["next_at"] = _iso(env.clock() + timedelta(minutes=min(5 * 2 ** (r["failures"] - 1), 360)))
    return r


def _retry_clear(state: dict, key: str) -> None:
    (state.get("retry") or {}).pop(key, None)


# --- slide pipeline --------------------------------------------------------------------------
def _handoff_paths(env: Env, story_id: str, slot: int, attempt: int) -> tuple[Path, Path]:
    base = env.episodes_dir / story_id / "handoff"
    return base / f"slide{slot}_attempt{attempt}.json", base / f"slide{slot}_attempt{attempt}.prompt.txt"


def _last_rejection(sc_state: dict, slot: int) -> dict | None:
    rej = [r for r in (sc_state.get("retry_state") or {}).get("rejected") or []
           if r["slide_index"] == slot]
    return rej[-1] if rej else None


def ensure_handoff(env: Env, lock: dict, lock_sha: str, prompts: dict, active: dict,
                   sc_state: dict, slot: int, attempt: int, max_attempts: int) -> dict:
    packet_file, prompt_file = _handoff_paths(env, lock["story_id"], slot, attempt)
    packet = read_json(packet_file)
    if packet and packet.get("lock_sha256") == lock_sha:
        return packet                                   # idempotent: same attempt, same canon
    pack = next(s for s in prompts["slides"] if s["slide_index"] == slot)
    text = (Path(pack["prompt_pack"]) if Path(pack["prompt_pack"]).is_absolute()
            else ROOT / pack["prompt_pack"]).read_text()
    # The executor gets exactly the compiled prompt: the pack's "# ..." header is
    # bookkeeping, so it is stripped (and the sha matches the coordinator's).
    lines = text.split("\n")
    while lines and (lines[0].startswith("# ") or not lines[0].strip()):
        lines.pop(0)
    body = "\n".join(lines).rstrip("\n")
    record = sc.plan_record(lock, slot)
    if attempt > 1:
        rej = _last_rejection(sc_state, slot) or {}
        body = ec.repair_prompt(lock, record, body, rej.get("failed") or [], rej.get("notes", ""),
                                attempt, max_attempts)
    write_text_atomic(prompt_file, body)
    width, height = CANVAS[aspect_of(lock)]
    target = asset_path(env, lock, slot)
    packet = {
        "story_id": lock["story_id"], "item_index": lock["item_index"], "slide_index": slot,
        "attempt": attempt, "max_attempts": max_attempts, "lock_sha256": lock_sha,
        "prompt_file": _rel(prompt_file), "prompt_sha256": sha256_text(body),
        "kind": "repair" if attempt > 1 else "first_attempt",
        "references": sc.reference_selection({}, active, record, index=int(lock["item_index"]),
                                             root=env.assets_root),
        "output": {"width": width, "height": height, "aspect_ratio": aspect_of(lock),
                   "format": "PNG", "expected_path": _rel(target)},
        "executor": "generation executor (human or separate automation) -- never this runner",
        "deliver_with": f"python3 nyx_runner.py record-asset {lock['story_id']} {slot} <image.png> "
                        f"--by <who> --provider <name>",
        "cost": {"incremental_cost_usd": 0, "paid_providers_prohibited": True},
        "created_at": _iso(env.clock()),
    }
    write_json_atomic(packet_file, packet)
    return packet


def _reference_paths(env: Env, lock: dict, active: dict, record: dict) -> list[dict]:
    out = []
    for ref in sc.reference_selection({}, active, record, index=int(lock["item_index"]),
                                      root=env.assets_root):
        if ref.get("kind") != "local":
            continue
        path = carousel_handoff.resolve_reference(ref, index=int(lock["item_index"]), root=env.assets_root)
        if path.is_file():
            out.append({"role": ref["role"], "decides": ref.get("decides", ""), "path": str(path)})
    return out


def _validate_verdict(raw: dict) -> dict:
    dims = raw.get("dimensions") if isinstance(raw, dict) else None
    if not isinstance(dims, dict):
        raise Unavailable("reviewer reply has no dimensions object")
    clean = {}
    for dim in sc.QA_DIMENSIONS:
        d = dims.get(dim)
        if not isinstance(d, dict):
            raise Unavailable(f"reviewer skipped dimension {dim}")
        score = str(d.get("score", "")).strip().upper()
        obs = str(d.get("observation", "")).strip()
        if score not in ("PASS", "FAIL") or len(obs) < 8:
            raise Unavailable(f"reviewer gave no usable score/observation for {dim}")
        clean[dim] = {"score": score, "observation": obs}
    return clean


def _archive_rejected(env: Env, lock: dict, slot: int, attempt: int) -> None:
    asset = asset_path(env, lock, slot)
    dest = package_dir(env, lock) / "rejected"
    dest.mkdir(parents=True, exist_ok=True)
    for src in (asset, provenance_path(asset)):
        if src.is_file():
            os.replace(src, dest / src.name.replace(f"slide{slot}", f"slide{slot}_attempt{attempt}"))


def _record_verdict(env: Env, lock: dict, sc_state: dict, record: dict, evidence: dict,
                    evidence_file: Path) -> dict:
    """Idempotent: the evidence file is written first, story_continuity state
    second, the `recorded` flag last. A crash anywhere in between is repaired on
    the next tick by looking at which of the three already happened."""
    slot, attempt = record["slide_index"], evidence["attempt"]
    approved = sc.approved_ref(sc_state, slot) is not None
    attempts = int((sc_state.get("retry_state") or {}).get("attempts", {}).get(str(slot), 0))
    already = approved or attempts >= attempt
    if not already:
        scores = {d: v["score"] == "PASS" for d, v in evidence["dimensions"].items()}
        out = sc.qa_slide(lock, sc_state, record, index=int(lock["item_index"]), scores=scores,
                          notes=evidence["notes"], root=env.assets_root)
        if out["status"] not in ("APPROVED", "REJECTED"):
            raise RunnerError(f"story_continuity refused the verdict: {out.get('error') or out['status']}")
        env.checkpoint("verdict_recorded_in_story_state")
    evidence["recorded"] = True
    write_json_atomic(evidence_file, evidence)
    return evidence


def advance_slides(env: Env, lock: dict, lock_sha: str, prompts: dict, state: dict) -> str:
    """Walk slides in order; return the episode's slide-phase status."""
    index = int(lock["item_index"])
    story_id = lock["story_id"]
    for _ in range(MAX_TICK_STEPS):
        sc_state = sc.ensure_state(lock, index, root=env.assets_root)
        active = sc.project_active_context(sc_state, lock, root=env.assets_root)
        max_attempts = int(sc_state["retry_state"]["max_attempts_per_slide"])
        pending = [r for r in sc.story_plan(lock) if sc.approved_ref(active, r["slide_index"]) is None]
        slides = state.setdefault("slides", {})
        for r in sc.story_plan(lock):
            if sc.approved_ref(active, r["slide_index"]) is not None:
                slides[str(r["slide_index"])] = {"state": "APPROVED",
                                                 "asset": _rel(asset_path(env, lock, r["slide_index"]))}
        if not pending:
            return "SLIDES_APPROVED"
        record = pending[0]
        slot = record["slide_index"]
        used = int(sc_state["retry_state"]["attempts"].get(str(slot), 0))
        view = slides.setdefault(str(slot), {})
        if used:
            # Finish the previous attempt's bookkeeping if a crash cut it short:
            # its rejection is already in story state, so flag the evidence and
            # move the rejected image out of the executor's slot.
            prev_file = env.episodes_dir / story_id / "qa" / f"slide{slot}_attempt{used}.json"
            prev = read_json(prev_file)
            if prev and prev["verdict"] == "REJECTED" and not prev.get("recorded"):
                prev["recorded"] = True
                write_json_atomic(prev_file, prev)
            stale = asset_path(env, lock, slot)
            if stale.is_file() and (read_json(provenance_path(stale)) or {}).get("attempt") == used:
                _archive_rejected(env, lock, slot, used)
        if used >= max_attempts:
            view.update(state="NEEDS_HUMAN_ACTION", detail=f"all {max_attempts} attempts rejected -- "
                        "simplify, re-sequence or drop this frame (founder decision)")
            return "HOLD_NAMED_GAP"
        gate = sc.readiness(active, record, root=env.assets_root)
        if not gate["ready"]:
            view.update(state="BLOCKED", detail="; ".join(gate["blockers"]))
            return "BLOCKED"
        attempt = used + 1
        packet = ensure_handoff(env, lock, lock_sha, prompts, active, sc_state, slot, attempt, max_attempts)
        env.checkpoint("handoff_written")
        evidence_file = env.episodes_dir / story_id / "qa" / f"slide{slot}_attempt{attempt}.json"
        evidence = read_json(evidence_file)
        if evidence and not evidence.get("recorded"):
            _record_verdict(env, lock, sc_state, record, evidence, evidence_file)
            if evidence["verdict"] == "REJECTED":
                env.checkpoint("before_archive")
                _archive_rejected(env, lock, slot, attempt)
            continue
        asset = asset_path(env, lock, slot)
        if not asset.is_file():
            view.update(state="WAITING_ON_EXECUTOR", attempt=attempt,
                        handoff=_rel(_handoff_paths(env, story_id, slot, attempt)[0]))
            return "WAITING_ON_EXECUTOR"
        prov = read_json(provenance_path(asset))
        problem = None
        if not prov:
            problem = "image has no provenance -- deliver it with record-asset"
        elif prov.get("prompt_sha256") != packet["prompt_sha256"] or prov.get("attempt") != attempt:
            problem = "image provenance does not match the open handoff (stale or wrong prompt)"
        elif prov.get("simulated") and not env.sandbox:
            problem = "SIMULATED image refused outside a sandbox"
        if problem:
            view.update(state="ASSET_REFUSED", attempt=attempt, detail=problem)
            return "WAITING_ON_EXECUTOR"
        asset_sha = sha256_file(asset)
        ok, why = technical_gate(asset, aspect_of(lock))
        if not ok:
            dims = {d: {"score": "FAIL" if d == "technical_output" else "NOT_CHECKED",
                        "observation": why if d == "technical_output" else "not reviewed: failed technical gate"}
                    for d in sc.QA_DIMENSIONS}
            evidence = {"story_id": story_id, "slide_index": slot, "attempt": attempt,
                        "asset_sha256": asset_sha, "reviewer": "deterministic_technical_gate",
                        "simulated": False, "reviewed_at": _iso(env.clock()), "dimensions": dims,
                        "notes": f"technical gate: {why}", "verdict": "REJECTED", "recorded": False}
            write_json_atomic(evidence_file, evidence)
            _event(state, env, f"slide {slot} attempt {attempt} rejected by technical gate: {why}")
            continue
        key = f"qa:{slot}:{attempt}"
        failures = int(((state.get("retry") or {}).get(key) or {}).get("failures", 0))
        if failures >= MAX_REVIEWER_FAILURES:
            view.update(state="BLOCKED", detail=f"visual reviewer failed {failures} times -- fix it, "
                        f"then `python3 nyx_runner.py unblock {story_id}`")
            return "BLOCKED"
        if not _retry_due(state, key, env):
            view.update(state="WAITING_ON_QA_RETRY", attempt=attempt,
                        detail=state["retry"][key]["last_error"])
            return "WAITING_ON_QA_RETRY"
        req = {"story_id": story_id, "slide_index": slot, "asset_abs": str(asset.resolve()),
               "references_abs": _reference_paths(env, lock, active, record),
               "character_presence": record.get("character_presence", "present"),
               "expectations": sc.qa_expectations(lock, record, active)}
        try:
            dims = _validate_verdict(env.reviewer.review(req))
        except Unavailable as exc:
            r = _retry_fail(state, key, env, str(exc))
            _event(state, env, f"slide {slot} QA unavailable ({r['failures']}): {exc}")
            if r["failures"] >= MAX_REVIEWER_FAILURES:
                view.update(state="BLOCKED", detail=f"visual reviewer failed {r['failures']} times "
                            f"({exc}) -- fix it, then `python3 nyx_runner.py unblock {story_id}`")
                return "BLOCKED"
            view.update(state="WAITING_ON_QA_RETRY", attempt=attempt, detail=str(exc))
            return "WAITING_ON_QA_RETRY"
        if sha256_file(asset) != asset_sha:
            _event(state, env, f"slide {slot} image changed during review -- verdict discarded")
            continue
        _retry_clear(state, key)
        verdict = "APPROVED" if all(v["score"] == "PASS" for v in dims.values()) else "REJECTED"
        evidence = {"story_id": story_id, "slide_index": slot, "attempt": attempt,
                    "asset_sha256": asset_sha, "reviewer": env.reviewer.name,
                    "simulated": bool(getattr(env.reviewer, "simulated", False)),
                    "reviewed_at": _iso(env.clock()), "dimensions": dims,
                    "notes": "; ".join(f"{d}: {v['observation']}" for d, v in dims.items()
                                       if v["score"] != "PASS") or "all dimensions pass",
                    "verdict": verdict, "recorded": False}
        write_json_atomic(evidence_file, evidence)
        env.checkpoint("qa_evidence_written")
        _event(state, env, f"slide {slot} attempt {attempt} {verdict} by {env.reviewer.name}")
    return "STEP_LIMIT"


# --- captions / variants / release ------------------------------------------------------------
def advance_captions(env: Env, lock: dict, state: dict) -> str:
    out = env.episodes_dir / lock["story_id"]
    check = ec.stage_captions(lock, out)
    if check["status"] == ec.PASS:
        state["captions"] = {"status": "PASS", "file": check["file"]}
        return "PASS"
    key = "captions"
    tries = int(((state.get("retry") or {}).get(key) or {}).get("failures", 0))
    if tries >= MAX_CAPTION_ATTEMPTS:
        state["captions"] = {"status": "BLOCKED", "problems": check.get("problems") or [check.get("fix")]}
        return "BLOCKED"
    if not _retry_due(state, key, env):
        return "WAITING_ON_CAPTION_RETRY"
    platforms = list(ec.platforms_for(lock))
    req = {"limits": {p: ec.CAPTION_LIMITS.get(p, 2200) for p in platforms},
           "needs_disclosure": [p for p in platforms if p not in ec.NATIVE_AI_LABEL],
           "slides": [{"beat": r["story_beat"], "overlay": r["overlay_copy"]} for r in sc.story_plan(lock)],
           "problems": check.get("problems")}
    try:
        drafted = env.author.write(req)
        caps = {p: str((drafted.get("captions") or {}).get(p) or "") for p in platforms}
    except Unavailable as exc:
        _retry_fail(state, key, env, str(exc))
        return "WAITING_ON_CAPTION_RETRY"
    write_json_atomic(out / "captions.json", {
        "story_id": lock["story_id"], "status": "DRAFT_AUTHORED_BY_RUNNER",
        "authored_by": env.author.name, "simulated": bool(getattr(env.author, "simulated", False)),
        "captions": caps})
    check = ec.stage_captions(lock, out)
    if check["status"] == ec.PASS:
        _retry_clear(state, key)
        state["captions"] = {"status": "PASS", "file": check["file"]}
        return "PASS"
    _retry_fail(state, key, env, "; ".join(check["problems"]))
    return "WAITING_ON_CAPTION_RETRY"


def _render_job(env: Env, lock: dict) -> dict:
    index = int(lock["item_index"])
    pkg = package_dir(env, lock)
    frames = []
    for r in sc.story_plan(lock):
        n = r["slide_index"]
        final = pkg / (carousel_handoff.overlay_filename(index, n) if r["overlay_copy"].strip()
                       else carousel_handoff.slide_filename(index, n))
        frames.append({"slide_index": n, "source_abs": str(pkg / carousel_handoff.slide_filename(index, n)),
                       "final_abs": str(final), "overlay": r["overlay_copy"] or None,
                       "safe_zone": r["overlay_safe_zone"]})
    return {"story_id": lock["story_id"], "item_index": index, "assets_root": env.assets_root,
            "package_dir": pkg, "platforms": list(ec.platforms_for(lock)), "frames": frames,
            "brief_slides": sc.brief_slides(lock),
            "safe_zones": {f["slide_index"]: f["safe_zone"] for f in frames}}


def advance_variants(env: Env, lock: dict, state: dict) -> str:
    job = _render_job(env, lock)
    sources = {str(f["slide_index"]): sha256_file(Path(f["source_abs"])) for f in job["frames"]}
    have = state.get("variants") or {}
    if have.get("status") == "PASS" and have.get("source_sha256") == sources and all(
            Path(m["path_abs"]).is_file() and sha256_file(Path(m["path_abs"])) == m["sha256"]
            for files in have["platforms"].values() for m in files):
        return "PASS"                                   # idempotent: nothing changed
    if not _retry_due(state, "render", env):
        return "WAITING_ON_RENDER_RETRY"
    try:
        rendered = env.renderer.render(job)
    except Unavailable as exc:
        _retry_fail(state, "render", env, str(exc))
        state["variants"] = {"status": "BLOCKED_RENDERER", "detail": str(exc)}
        return "BLOCKED_RENDERER"
    _retry_clear(state, "render")
    state["variants"] = {
        "status": "PASS", "renderer": env.renderer.name, "source_sha256": sources,
        "simulated": bool(getattr(env.renderer, "simulated", False)),
        "platforms": {p: [{"path": _rel(Path(f)), "path_abs": str(Path(f).resolve()),
                           "sha256": sha256_file(Path(f))} for f in files]
                      for p, files in rendered.items()}}
    env.checkpoint("variants_rendered")
    return "PASS"


def upsert_release(env: Env, lock: dict, state: dict, parent: str | None) -> dict:
    """The release queue is the END of automation. An entry is READY_FOR_PUBLISH
    (a verified live route) or HELD_NO_ROUTE; only a human moves it further."""
    qpath = env.episodes_dir / QUEUE_NAME
    queue = read_json(qpath, {"entries": {}})
    captions = (read_json(env.episodes_dir / lock["story_id"] / "captions.json", {}) or {}).get("captions", {})
    items = []
    venture = read_json(env.venture_path, {}) or {}
    n_items = len((venture.get("content_plan") or {}).get("content_items") or [])
    for platform, files in state["variants"]["platforms"].items():
        key = f"{lock['story_id']}:{platform}"
        prev = queue["entries"].get(key)
        ch = channels.CHANNELS.get(platform, {})
        media = [{"path": m["path"], "sha256": m["sha256"]} for m in files]
        if prev and prev["status"] == PUBLISHED_CONFIRMED:
            items.append(prev)
            continue
        entry = {
            "story_id": lock["story_id"], "platform": platform, "parent_episode": parent,
            "status": READY_FOR_PUBLISH if ch.get("publishes") else HELD_NO_ROUTE,
            "route": ch.get("route"), "route_status": ch.get("status", channels.NOT_AVAILABLE),
            "hold_reason": None if ch.get("publishes") else ch.get("blocker"),
            "caption": captions.get(platform, ""), "media": media,
            "ai_disclosure": "platform AI label" if platform in ec.NATIVE_AI_LABEL else "in caption",
            "simulated": bool(state["variants"].get("simulated")),
            "requires_human_confirmation": True,
            "publish_prerequisites": {
                f"state/venture.json has content_items[{lock['item_index']}]":
                    int(lock["item_index"]) < n_items},
            "after_publishing_run": f"python3 nyx_runner.py confirm-published {lock['story_id']} "
                                    f"{platform} <live url> --by <who>",
            "queued_at": (prev or {}).get("queued_at") or _iso(env.clock()),
        }
        if prev and prev.get("media") != media:
            entry["note"] = "media changed since first queued -- re-check before publishing"
        queue["entries"][key] = entry
        items.append(entry)
    write_json_atomic(qpath, queue)
    return {"entries": len(items), "ready": sum(e["status"] == READY_FOR_PUBLISH for e in items)}


# --- one episode, one tick ---------------------------------------------------------------------
def advance_episode(env: Env, entry: dict) -> dict:
    story_id = entry["story_id"]
    lock = ec.load_lock(story_id, env.locks_dir)
    lock_sha = sha256_file(ec.lock_path(story_id, env.locks_dir))
    path = state_path(env, story_id)
    state = read_json(path) or {"story_id": story_id, "kind": entry.get("kind", "public_episode"),
                                "created_at": _iso(env.clock())}
    state.update(story_id=story_id, parent=entry.get("parent"), lock_sha256=lock_sha,
                 item_index=lock["item_index"], total_slides=len(lock.get("story_plan") or []))
    out = env.episodes_dir / story_id
    patched = None
    brief = ec.stage_brief(lock, ec.load_season(lock.get("season_id"), env.seasons_dir),
                           locks_dir=env.locks_dir, patched=patched)
    status = None
    if brief["status"] == ec.BLOCKED:
        status, state["blocker"] = "BLOCKED_BRIEF", brief["fix"]
    else:
        prompts = ec.stage_locked_prompts(lock, lock_sha, out, patch=None,
                                          assets_root=env.assets_root, write=True)
        if prompts["status"] != ec.PASS:
            status, state["blocker"] = "BLOCKED_PROMPTS", prompts["fix"]
        else:
            status = advance_slides(env, lock, lock_sha, prompts, state)
    if status == "SLIDES_APPROVED":
        cap = advance_captions(env, lock, state)
        status = "WAITING_ON_CAPTIONS" if cap != "PASS" else None
        if cap == "BLOCKED":
            status = "BLOCKED_CAPTIONS"
        if status is None:
            var = advance_variants(env, lock, state)
            status = None if var == "PASS" else var
        if status is None:
            summary = upsert_release(env, lock, state, entry.get("parent"))
            queue = read_json(env.episodes_dir / QUEUE_NAME, {"entries": {}})["entries"]
            mine = [e for k, e in queue.items() if k.startswith(f"{story_id}:")]
            status = ("PUBLISHED" if mine and all(e["status"] in (PUBLISHED_CONFIRMED, HELD_NO_ROUTE)
                                                   for e in mine) and any(
                e["status"] == PUBLISHED_CONFIRMED for e in mine) else READY_FOR_PUBLISH)
            state["release"] = summary
    if status not in ("BLOCKED_BRIEF", "BLOCKED_PROMPTS"):
        state.pop("blocker", None)
    if entry.get("kind") != "fanvue_chapter":
        chapter = next((e for e in load_registry(env)
                        if e.get("kind") == "fanvue_chapter" and e.get("parent") == story_id), None)
        state["fanvue_chapter"] = chapter["story_id"] if chapter else None
    if state.get("status") != status:
        _event(state, env, f"status {state.get('status')} -> {status}")
    state["status"] = status
    state["updated_at"] = _iso(env.clock())
    write_json_atomic(path, state)
    return state


def tick(env: Env | None = None) -> dict:
    """The trigger. Takes an exclusive lock so two overlapping timers never
    race; the OS drops the lock if the process dies, so a crash never wedges it."""
    env = env or real_env()
    env.guard()
    env.episodes_dir.mkdir(parents=True, exist_ok=True)
    lock_file = open(env.episodes_dir / LOCK_NAME, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return {"ran": False, "reason": "another tick is already running"}
    try:
        results = {}
        for entry in load_registry(env):
            try:
                results[entry["story_id"]] = advance_episode(env, entry)["status"]
            except (RunnerError, ec.CoordinatorError, sc.ContinuityError) as exc:
                results[entry["story_id"]] = f"ERROR: {exc}"
        write_text_atomic(env.episodes_dir / STATUS_NAME, render_status(env))
        return {"ran": True, "episodes": results}
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


# --- executor / human commands (bookkeeping only) -----------------------------------------------
def record_asset(env: Env, story_id: str, slot: int, image: Path, *, by: str, provider: str,
                 simulated: bool = False) -> dict:
    """The generation EXECUTOR's single delivery command: copies its image to the
    expected path with provenance bound to the open handoff. Refuses when there
    is no open handoff for that slide -- an image can't arrive for work nobody asked for."""
    if simulated and not env.sandbox:
        raise RunnerError("simulated delivery refused outside a sandbox")
    lock = ec.load_lock(story_id, env.locks_dir)
    sc_state = sc.load_state(lock, int(lock["item_index"]), root=env.assets_root)
    if sc.approved_ref(sc_state, slot) is not None:
        raise RunnerError(f"slide {slot} is already approved")
    attempt = int((sc_state.get("retry_state") or {}).get("attempts", {}).get(str(slot), 0)) + 1
    packet = read_json(_handoff_paths(env, story_id, slot, attempt)[0])
    if not packet:
        raise RunnerError(f"no open handoff for {story_id} slide {slot} attempt {attempt}")
    if not image.is_file():
        raise RunnerError(f"no image at {image}")
    target = asset_path(env, lock, slot)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".incoming")
    shutil.copyfile(image, tmp)
    os.replace(tmp, target)
    prov = {"story_id": story_id, "slide_index": slot, "attempt": attempt,
            "prompt_sha256": packet["prompt_sha256"], "generated_by": by, "provider": provider,
            "asset_sha256": sha256_file(target), "delivered_at": _iso(env.clock()),
            "simulated": simulated}
    write_json_atomic(provenance_path(target), prov)
    return prov


def unblock(env: Env, story_id: str) -> dict:
    """Clear exhausted retry counters after the cause (reviewer, caption author,
    renderer) has been fixed. Touches no verdict, approval or attempt count."""
    path = state_path(env, story_id)
    state = read_json(path)
    if not state:
        raise RunnerError(f"no runner state for {story_id}")
    cleared = sorted((state.get("retry") or {}).keys())
    state["retry"] = {}
    _event(state, env, f"unblocked: cleared retry counters {cleared}")
    write_json_atomic(path, state)
    return {"story_id": story_id, "cleared": cleared}


def confirm_published(env: Env, story_id: str, platform: str, url: str, *, by: str) -> dict:
    """Records a publish a HUMAN already performed and verified. Never publishes."""
    if not by.strip() or not url.strip():
        raise RunnerError("confirm-published needs --by and the live url")
    qpath = env.episodes_dir / QUEUE_NAME
    queue = read_json(qpath, {"entries": {}})
    entry = queue["entries"].get(f"{story_id}:{platform}")
    if not entry or entry["status"] != READY_FOR_PUBLISH:
        raise RunnerError(f"{story_id}:{platform} is not READY_FOR_PUBLISH")
    entry.update(status=PUBLISHED_CONFIRMED, live_url=url, confirmed_by=by,
                 confirmed_at=_iso(env.clock()))
    write_json_atomic(qpath, queue)
    return entry


# --- status report ---------------------------------------------------------------------------------
NEXT_ACTION = {
    "WAITING_ON_EXECUTOR": "generation executor: deliver the open handoff with record-asset",
    "WAITING_ON_QA_RETRY": "automatic: reviewer retried on a later tick",
    "WAITING_ON_CAPTIONS": "automatic: captions retried on a later tick",
    "WAITING_ON_CAPTION_RETRY": "automatic: captions retried on a later tick",
    "WAITING_ON_RENDER_RETRY": "automatic: render retried on a later tick",
    "BLOCKED_RENDERER": "install the missing render tool (see detail); retried automatically",
    "BLOCKED": "see the slide detail; after fixing the cause run unblock",
    "BLOCKED_CAPTIONS": "fix captions.json by hand or unblock to redraft",
    "HOLD_NAMED_GAP": "founder: simplify, re-sequence or drop the exhausted frame",
    READY_FOR_PUBLISH: "human: publish each READY entry, then confirm-published",
    "PUBLISHED": "none",
}


def live_blockers(env: Env) -> list[str]:
    """What stands between this machine and an unattended LIVE run, probed now."""
    out = []
    if not shutil.which("claude"):
        out.append("visual QA + captions: `claude` CLI not on PATH (claude_client route)")
    try:
        import PIL  # noqa: F401
    except ImportError:
        out.append("variants: Pillow not installed (overlay burn-in)")
    if not shutil.which("ffmpeg"):
        out.append("variants: ffmpeg not installed (TikTok slideshow)")
    if not env.venture_path.is_file():
        out.append("release: state/venture.json absent here -- the publisher reads content_items from it")
    out.append("visual QA: the claude_cli_vision reviewer is wired and tested with a simulated "
               "reviewer only; it has never scored a real Nyx image -- spot-check its first verdicts")
    out.append("generation: no autonomous zero-cost executor exists; each handoff needs the founder "
               "(ChatGPT, interactive) or an approved separate automation to run record-asset")
    out.append("trigger: ops/com.nyx.episode-runner.plist must be installed with launchctl on the "
               "machine that holds generated_assets/")
    return out


def render_status(env: Env) -> str:
    lines = ["# Nyx episode runner -- status", "",
             "Automation ends at READY_FOR_PUBLISH. Nothing here generates, uploads, posts or spends.",
             "", "| Episode | Status | Slides approved | Next action |", "|---|---|---|---|"]
    for entry in load_registry(env):
        st = read_json(state_path(env, entry["story_id"]), {}) or {}
        approved = sum(1 for s in (st.get("slides") or {}).values() if s.get("state") == "APPROVED")
        status = st.get("status", "NOT_RUN")
        nxt = st.get("blocker") or NEXT_ACTION.get(status, "")
        cur = next((f"slide {k}: {v.get('detail') or v['state']}" for k, v in
                    sorted((st.get("slides") or {}).items(), key=lambda kv: int(kv[0]))
                    if v.get("state") not in ("APPROVED",)), "")
        name = entry["story_id"] + (f" (Fanvue chapter of {entry['parent']})" if entry.get("parent") else "")
        lines.append(f"| {name} | {status} | {approved}/{st.get('total_slides', '?')} | "
                     f"{nxt}{'; ' + cur if cur and status != READY_FOR_PUBLISH else ''} |")
        if entry.get("kind") != "fanvue_chapter" and not st.get("fanvue_chapter"):
            lines.append(f"| ↳ Fanvue chapter | NO_APPROVED_LOCK | - | founder: approve a Fanvue "
                         f"chapter lock and enrol it with parent {entry['story_id']} |")
    queue = (read_json(env.episodes_dir / QUEUE_NAME, {}) or {}).get("entries", {})
    lines += ["", "## Release queue", ""]
    if not queue:
        lines.append("Empty.")
    else:
        lines += ["| Entry | Status | Media |", "|---|---|---|"]
        lines += [f"| {k} | {e['status']}{' (SIMULATED)' if e.get('simulated') else ''} | "
                  f"{len(e['media'])} file(s) |" for k, e in sorted(queue.items())]
    lines += ["", "## Live-integration blockers (this machine)", ""]
    lines += [f"- {b}" for b in live_blockers(env)]
    return "\n".join(lines) + "\n"


# --- simulation (sandbox only) --------------------------------------------------------------------
def _pattern_png(path: Path, width: int, height: int, seed: int) -> None:
    """A varied, obviously synthetic test pattern (NOT a Nyx image) for the
    sandboxed simulated executor. Passes the technical gate by design so the
    QA/repair/variant paths run; the provenance says simulated, so a real run
    refuses it."""
    base = bytes((i * 7 + seed * 31) % 251 for i in range(width * 3))
    rows = b"".join(b"\x00" + base[(y * 3) % len(base):] + base[:(y * 3) % len(base)]
                    for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


class SimulatedReviewer:
    """Scripted verdicts for the sandbox. `reject` maps (slot, attempt) -> failed dims."""
    name, simulated = "SIMULATED_reviewer", True

    def __init__(self, reject=None, outages: int = 0):
        self.reject, self.outages, self.calls = reject or {}, outages, 0

    def review(self, req: dict) -> dict:
        self.calls += 1
        if self.outages > 0:
            self.outages -= 1
            raise Unavailable("SIMULATED reviewer outage")
        prov = read_json(provenance_path(Path(req["asset_abs"])), {})
        failed = self.reject.get((req["slide_index"], prov.get("attempt")), [])
        return {"dimensions": {d: {"score": "FAIL" if d in failed else "PASS",
                                   "observation": f"SIMULATED {'defect' if d in failed else 'check ok'} for {d}"}
                               for d in sc.QA_DIMENSIONS}}


class SimulatedAuthor:
    name, simulated = "SIMULATED_caption_author", True

    def write(self, req: dict) -> dict:
        last = req["slides"][-1]["overlay"] or "what happens next?"
        caps = {}
        for p in req["limits"]:
            text = f"simulated {p} caption. {last}"
            caps[p] = text + (" #AIgenerated" if p in req["needs_disclosure"] else "")
        return {"captions": caps}


class SimulatedRenderer:
    name, simulated = "SIMULATED_renderer", True

    def render(self, job: dict) -> dict:
        out = {}
        for f in job["frames"]:
            final = Path(f["final_abs"])
            if final != Path(f["source_abs"]):
                shutil.copyfile(f["source_abs"], final)
        finals = [Path(f["final_abs"]) for f in job["frames"]]
        for p in job["platforms"]:
            if p == "tiktok":
                video = job["package_dir"] / f"item{job['item_index']}_tiktok.SIMULATED.mp4"
                video.write_bytes(b"SIMULATED slideshow -- not a video")
                out[p] = [video]
            else:
                out[p] = finals
        return out


def simulated_executor(env: Env) -> int:
    """Stands in for the generation executor: answers every open handoff through
    the real record-asset path, with provenance marked simulated."""
    delivered = 0
    for entry in load_registry(env):
        lock = ec.load_lock(entry["story_id"], env.locks_dir)
        st = read_json(state_path(env, entry["story_id"]), {}) or {}
        for slot, view in (st.get("slides") or {}).items():
            if view.get("state") != "WAITING_ON_EXECUTOR":
                continue
            w, h = CANVAS[aspect_of(lock)]
            tmp = env.assets_root.parent / "executor_out" / f"{entry['story_id']}_{slot}_{view['attempt']}.png"
            _pattern_png(tmp, w, h, int(slot) * 10 + view["attempt"])
            record_asset(env, entry["story_id"], int(slot), tmp, by="SIMULATED executor",
                         provider="SIMULATED", simulated=True)
            delivered += 1
    return delivered


def build_sandbox(base: Path, *, reviewer=None) -> Env:
    """A throwaway copy of the real s1e02_public lock and captions plus a
    SIMULATED Fanvue chapter fixture (cloned from s1e01_fanvue_part1's plan,
    renamed, never written back to the real repo)."""
    locks, eps, assets = base / "locks", base / "episodes", base / "assets"
    locks.mkdir(parents=True)
    shutil.copy(ec.lock_path("s1e02_public"), locks / "s1e02_public.json")
    chapter = ec.load_lock("s1e01_fanvue_part1")
    chapter.update(story_id="sim_s1e02_fanvue", item_id="SIMULATED", item_index=990,
                   episode_id="s1e02", approved_by="SIMULATED fixture -- not canon")
    (locks / "sim_s1e02_fanvue.json").write_text(json.dumps(chapter, indent=1))
    (eps / "s1e02_public").mkdir(parents=True)
    shutil.copy(ec.EPISODES_DIR / "s1e02_public" / "captions.json", eps / "s1e02_public" / "captions.json")
    write_json_atomic(eps / REGISTRY_NAME, {"episodes": [
        {"story_id": "s1e02_public", "kind": "public_episode", "approved_by": "sandbox copy"},
        {"story_id": "sim_s1e02_fanvue", "kind": "fanvue_chapter", "parent": "s1e02_public",
         "approved_by": "SIMULATED fixture"}]})
    clock = {"now": datetime(2026, 10, 1, tzinfo=timezone.utc)}
    env = Env(locks_dir=locks, episodes_dir=eps, assets_root=assets, venture_path=base / "no_venture.json",
              reviewer=reviewer or SimulatedReviewer(reject={(2, 1): ["identity"]}),
              author=SimulatedAuthor(), renderer=SimulatedRenderer(), sandbox=True,
              clock=lambda: clock["now"])
    env._clock_state = clock
    return env


def run_until_settled(env: Env, *, max_rounds: int = 60) -> dict:
    """Tick, let the simulated executor answer, advance the fake clock, repeat
    until every episode is READY_FOR_PUBLISH or nothing moves."""
    last = None
    for _ in range(max_rounds):
        result = tick(env)
        delivered = simulated_executor(env)
        env._clock_state["now"] += timedelta(hours=1)
        if all(s == READY_FOR_PUBLISH for s in result["episodes"].values()):
            return result
        if not delivered and result == last:
            return result
        last = result
    return result


def simulate(interrupt: bool = False) -> dict:
    with tempfile.TemporaryDirectory(prefix="nyx_runner_sim_") as tmp:
        env = build_sandbox(Path(tmp))
        crashes = 0
        if interrupt:
            state = {"n": 0}

            def crash(name):
                state["n"] += 1
                if state["n"] in (3, 9, 17):
                    raise KeyboardInterrupt(f"simulated crash at {name}")
            env.checkpoint_hook = crash
            for _ in range(10):
                try:
                    result = run_until_settled(env)
                    break
                except KeyboardInterrupt:
                    crashes += 1
                    env.reviewer = SimulatedReviewer(reject={(2, 1): ["identity"]})   # fresh process
        else:
            result = run_until_settled(env)
        queue = read_json(env.episodes_dir / QUEUE_NAME, {"entries": {}})["entries"]
        sc_state = sc.load_state(ec.load_lock("s1e02_public", env.locks_dir), 33, root=env.assets_root)
        return {"episodes": result["episodes"], "crashes_survived": crashes,
                "queue": {k: e["status"] for k, e in sorted(queue.items())},
                "qa_evidence_files": sorted(p.name for p in (env.episodes_dir / "s1e02_public" / "qa").glob("*.json")),
                "slide2_rejections": [r["attempt"] for r in sc_state["retry_state"]["rejected"]
                                      if r["slide_index"] == 2]}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]

    def opt(name):
        return rest[rest.index(name) + 1] if name in rest else ""
    try:
        if cmd == "tick":
            out = tick()
        elif cmd == "status":
            env = real_env()
            text = render_status(env)
            write_text_atomic(env.episodes_dir / STATUS_NAME, text)
            print(text)
            return 0
        elif cmd == "record-asset":
            out = record_asset(real_env(), rest[0], int(rest[1]), Path(rest[2]),
                               by=opt("--by"), provider=opt("--provider"))
        elif cmd == "unblock":
            out = unblock(real_env(), rest[0])
        elif cmd == "confirm-published":
            out = confirm_published(real_env(), rest[0], rest[1], rest[2], by=opt("--by"))
        elif cmd == "simulate":
            out = simulate(interrupt="--interrupt" in rest)
        else:
            print(f"unknown command: {cmd}\n\n{__doc__}")
            return 1
    except (RunnerError, ec.CoordinatorError, sc.ContinuityError, IndexError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
