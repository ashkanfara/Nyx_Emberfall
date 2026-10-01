"""Autonomous Nyx episode runner -- exception-only continuation.

Once a story lock is approved and enrolled in episodes/runner_registry.json,
`python3 nyx_runner.py tick` resumes every ordinary stage on its own and exits.
Each tick re-derives where the episode is from what is on disk, so a crash, an
app restart or a browser restart just means the next tick picks up there.

    approved lock -> brief + locked prompts          (episode_coordinator)
      -> per slide, in order:                         (story_continuity gates)
           handoff -> generation executor (an approved unattended one runs
                      automatically; otherwise a human delivers with record-asset)
           intake  -> provenance + deterministic technical gate
           QA      -> a reviewer that actually opens the image; verdict bound to the
                      file's sha256, recorded via story_continuity.qa_slide
           reject  -> archive, targeted repair prompt, next attempt (3 per slide)
      -> captions (reused if valid, else drafted + validated)
      -> per-platform variants (local render)
      -> release package in episodes/release_queue.json

EXCEPTION-ONLY POLICY (founder 2026-09-30). An episode stops for exactly four
durable, human-visible reasons. Everything else is an ordinary stage or a
transient failure retried with bounded backoff (5 min doubling, 6 h cap,
6 tries).

  1 EXC_GENERATION_ACTION_REQUIRED / EXC_GENERATION_EXECUTOR_UNAVAILABLE
      no approved unattended image executor, or the configured one is down.
      Resumes by itself when the image arrives or the executor is back.
  2 EXC_QA_REPAIR_BUDGET_EXHAUSTED
      a slide failed visual QA 3 times. Resumes after `grant-attempt` (a human
      decision) -- never by itself.
  3 EXC_PUBLISH_APPROVAL_REQUIRED
      the release package is ready. One `approve-release` covers the package; a
      human publishes and records it with `confirm-published`. The runner NEVER
      publishes externally.
  4 EXC_NON_RETRYABLE_FAILURE
      an access, credential, route or renderer failure that cannot be retried
      safely (or transient failures past their bound, or an invalid lock).
      A missing tool resumes by itself once installed; anything else via `unblock`.

Open exceptions are written to each episode's runner_state.json, to
episodes/exception_queue.json and to episodes/RUNNER_STATUS.md. `readiness`
states whether a scheduled runner would make meaningful progress right now.

Hard boundaries (brand/NYX_PRODUCTION_CONTRACT.md): no browser, social site,
upload, post, comment, spend or account change here. No evidence, no progress:
an image without matching provenance is never QA'd, and SIMULATED adapters,
executors, images and verdicts are refused outside a sandbox. The Reddit
workstream belongs to a separate agent and is never read or written.

    python3 nyx_runner.py tick
    python3 nyx_runner.py status | readiness
    python3 nyx_runner.py record-asset <story_id> <slot> <image.png> --by <who> --provider <name>
    python3 nyx_runner.py grant-attempt <story_id> <slot> --by <who>
    python3 nyx_runner.py approve-release <story_id> --by <who>
    python3 nyx_runner.py confirm-published <story_id> <platform> <url> --by <who>
    python3 nyx_runner.py unblock <story_id>
    python3 nyx_runner.py simulate [--interrupt] [--unattended]
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
EXCEPTIONS_NAME = "exception_queue.json"
EXECUTOR_NAME = "generation_executor.json"
STATUS_NAME = "RUNNER_STATUS.md"
LOCK_NAME = ".runner.lock"
CANVAS = {"4:5": (1080, 1350), "9:16": (1080, 1920)}
MAX_TICK_STEPS = 200            # a tick stops here even if it could go on -- no runaway loop
QA_ATTEMPT_BUDGET = 3           # failed visual-QA attempts per slide before exception 2
MAX_TRANSIENT_FAILURES = 6      # 5+10+20+40+80+160 min of backoff, then exception 4
MAX_REVIEWER_FAILURES = MAX_TRANSIENT_FAILURES

# Release entry states. Only a human moves an entry past READY_FOR_PUBLISH.
READY_FOR_PUBLISH = "READY_FOR_PUBLISH"
APPROVED_FOR_PUBLISH = "APPROVED_FOR_PUBLISH"
PUBLISHED_CONFIRMED = "PUBLISHED_CONFIRMED_BY_HUMAN"
HELD_NO_ROUTE = "HELD_NO_ROUTE"

# Ordinary (non-exception) episode states.
IN_PROGRESS = "IN_PROGRESS"
RETRY_SCHEDULED = "RETRY_SCHEDULED"
PUBLISHED = "PUBLISHED"

EXC_GENERATION_ACTION = "EXC_GENERATION_ACTION_REQUIRED"
EXC_EXECUTOR_DOWN = "EXC_GENERATION_EXECUTOR_UNAVAILABLE"
EXC_QA_BUDGET = "EXC_QA_REPAIR_BUDGET_EXHAUSTED"
EXC_PUBLISH_APPROVAL = "EXC_PUBLISH_APPROVAL_REQUIRED"
EXC_NON_RETRYABLE = "EXC_NON_RETRYABLE_FAILURE"
EXCEPTIONS = {
    EXC_GENERATION_ACTION: (1, "Image generation needs a human or an approved executor"),
    EXC_EXECUTOR_DOWN: (1, "Image-generation executor unavailable"),
    EXC_QA_BUDGET: (2, f"Quality repair budget exhausted ({QA_ATTEMPT_BUDGET} failed visual QA attempts)"),
    EXC_PUBLISH_APPROVAL: (3, "Release package ready: one final publish approval"),
    EXC_NON_RETRYABLE: (4, "Access, credential, route or renderer failure (not safely retryable)"),
}


class Unavailable(RuntimeError):
    """Transient: down or rate-limited right now. Retried with bounded backoff."""


class Fatal(RuntimeError):
    """Not safely retryable (missing tool, bad credential, no access). `probe`
    names a cheap check that, once it passes, lets the stage resume by itself."""

    def __init__(self, message: str, *, kind: str = "access", probe: str | None = None):
        super().__init__(message)
        self.kind, self.probe = kind, probe


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


# --- probes: cheap checks that let a stopped stage resume by itself ----------------------------
def probe_ok(name: str, env: "Env | None" = None) -> bool:
    if name == "claude":
        return bool(shutil.which("claude"))
    if name == "ffmpeg":
        return bool(shutil.which("ffmpeg"))
    if name == "pillow":
        try:
            import PIL  # noqa: F401
            return True
        except ImportError:
            return False
    if name == "conform":
        return conform_ok()
    if name == "executor" and env is not None:
        return executor_status(env)["available"]
    return False


_CREDENTIAL_MARKERS = ("not logged in", "please log in", "authenticat", "unauthorized",
                       "invalid api key", "401", "403", "permission denied", "forbidden")


def _classify_llm(exc: Exception, who: str):
    """claude_client errors -> transient (quota, timeout, bad reply) or fatal
    (CLI missing, credential rejected). A credential failure is never retried."""
    msg = str(exc)
    low = msg.lower()
    if "not found on path" in low:
        return Fatal(f"{who}: the claude CLI is not installed", kind="access", probe="claude")
    if any(m in low for m in _CREDENTIAL_MARKERS):
        return Fatal(f"{who}: credential rejected -- {msg[:200]}", kind="credential")
    return Unavailable(f"{who}: {msg[:300]}")


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
        except claude_client.LLMError as exc:
            raise _classify_llm(exc, "vision reviewer") from exc
        try:
            return {**claude_client.extract_json(out["text"]), "_model": claude_client.MODEL}
        except ValueError as exc:
            raise Unavailable(f"vision reviewer: unparseable reply ({exc})") from exc


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
        except claude_client.LLMError as exc:
            raise _classify_llm(exc, "caption author") from exc
        try:
            return claude_client.extract_json(out["text"])
        except ValueError as exc:
            raise Unavailable(f"caption author: unparseable reply ({exc})") from exc


class LocalRenderer:
    """Zero-cost local rendering of approved art into platform files: the
    existing overlay burn-in (carousel_handoff.apply_overlays, Pillow) and a
    TikTok slideshow (ffmpeg). A missing tool is Fatal with a probe, so the
    stage resumes by itself the tick after the tool is installed."""
    name, simulated = "local_render", False

    def render(self, job: dict) -> dict:
        item = {"slides": job["brief_slides"]}
        index, root = job["item_index"], job["assets_root"]
        if "tiktok" in job["platforms"] and not shutil.which("ffmpeg"):
            raise Fatal("renderer: ffmpeg is not installed (TikTok slideshow)", kind="renderer",
                        probe="ffmpeg")
        try:
            carousel_handoff.apply_overlays(item, index, root, safe_zones=job["safe_zones"])
        except carousel_handoff.OverlayUnavailable as exc:
            raise Fatal(f"renderer: Pillow is not installed (overlay burn-in): {exc}",
                        kind="renderer", probe="pillow") from exc
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


# --- generation executor ----------------------------------------------------------------------
def load_executor(env: Env) -> dict:
    return read_json(env.episodes_dir / EXECUTOR_NAME, {}) or {"mode": "human_manual"}


def executor_status(env: Env) -> dict:
    """Whether images can be produced WITHOUT a person. Only an explicitly
    approved, zero-cost, unattended `command` executor counts; paid generation
    is never automatic (founder policy 2026-09-22), and nothing here drives a
    browser. `code` is the exception to raise when it can't run."""
    cfg = load_executor(env)
    mode = cfg.get("mode", "human_manual")
    if mode == "human_manual":
        return {"mode": mode, "available": False, "code": EXC_GENERATION_ACTION,
                "reason": "no unattended executor is approved: a human generates each handoff "
                          "and delivers it with record-asset"}
    if mode != "command":
        return {"mode": mode, "available": False, "code": EXC_GENERATION_ACTION,
                "reason": f"unknown executor mode {mode!r}"}
    approval = []
    if not str(cfg.get("approved_by") or "").strip():
        approval.append("not approved (approved_by is empty)")
    if cfg.get("cost") != "zero":
        approval.append("not declared zero-cost -- paid generation needs a per-item founder "
                        "approval and is never run automatically")
    if cfg.get("simulated") and not env.sandbox:
        approval.append("SIMULATED executor refused outside a sandbox")
    if approval:
        return {"mode": mode, "available": False, "code": EXC_GENERATION_ACTION,
                "reason": "; ".join(approval)}
    cmd = cfg.get("command") or []
    if not cmd or not (shutil.which(cmd[0]) or Path(cmd[0]).is_file()):
        return {"mode": mode, "available": False, "code": EXC_EXECUTOR_DOWN,
                "reason": f"executor command {cmd[:1] or '(none)'} not found"}
    return {"mode": mode, "available": True, "code": None, "name": cfg.get("name", "command_executor"),
            "reason": f"approved zero-cost unattended executor {cfg.get('name', cmd[0])}"}


def run_executor(env: Env, packet_file: Path, packet: dict) -> Path:
    """Runs the approved command executor for one handoff. Returns the image it
    wrote; raises Unavailable (retry) or Fatal (stop)."""
    cfg = load_executor(env)
    out = package_dir_for(env, packet["item_index"]) / "incoming" / \
        f"slide{packet['slide_index']}_attempt{packet['attempt']}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    fill = {"packet": str(packet_file.resolve()), "output": str(out),
            "prompt": str((ROOT / packet["prompt_file"]) if not Path(packet["prompt_file"]).is_absolute()
                          else packet["prompt_file"]),
            "width": str(packet["output"]["width"]), "height": str(packet["output"]["height"])}
    cmd = [str(part).format(**fill) for part in cfg["command"]]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=int(cfg.get("timeout_s", 900)))
    except FileNotFoundError as exc:
        raise Fatal(f"executor command missing: {exc}", kind="executor", probe="executor") from exc
    except subprocess.TimeoutExpired as exc:
        raise Unavailable(f"executor timed out after {exc.timeout}s") from exc
    if proc.returncode != 0 or not out.is_file():
        raise Unavailable(f"executor exited {proc.returncode} without an image: {proc.stderr[:200]}")
    return out


# --- per-episode paths and state -------------------------------------------------------------
def load_registry(env: Env) -> list[dict]:
    return (read_json(env.episodes_dir / REGISTRY_NAME, {}) or {}).get("episodes", [])


def state_path(env: Env, story_id: str) -> Path:
    return env.episodes_dir / story_id / "runner_state.json"


def package_dir_for(env: Env, item_index) -> Path:
    return carousel_handoff.package_dir(int(item_index), env.assets_root)


def package_dir(env: Env, lock: dict) -> Path:
    return package_dir_for(env, lock["item_index"])


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


# --- retries, halts and exceptions -------------------------------------------------------------
def _retry_due(state: dict, key: str, env: Env) -> bool:
    r = (state.get("retry") or {}).get(key)
    return not r or env.clock() >= datetime.fromisoformat(r["next_at"])


def _retry_clear(state: dict, key: str) -> None:
    (state.get("retry") or {}).pop(key, None)


def _halt(state: dict, env: Env, key: str, code: str, detail: str, *, probe: str | None = None,
          kind: str = "access") -> dict:
    h = {"code": code, "detail": detail[:400], "probe": probe, "kind": kind, "since": _iso(env.clock())}
    state.setdefault("halts", {})[key] = h
    _event(state, env, f"{key} halted ({code}): {detail[:160]}")
    return h


def _halted(state: dict, env: Env, key: str) -> dict | None:
    """A stopped stage. If its probe now passes (tool installed, executor back)
    the halt is lifted here -- that is the automatic resume."""
    h = (state.get("halts") or {}).get(key)
    if h and h.get("probe") and probe_ok(h["probe"], env):
        del state["halts"][key]
        _retry_clear(state, key)
        _event(state, env, f"{key} resumed automatically: {h['probe']} is available again")
        return None
    return h


def _transient(state: dict, env: Env, key: str, error: str, code: str) -> dict:
    """Bounded backoff. Returns the halt once the bound is spent, else None."""
    r = state.setdefault("retry", {}).setdefault(key, {"failures": 0})
    r["failures"] += 1
    r["last_error"] = error[:300]
    r["next_at"] = _iso(env.clock() + timedelta(minutes=min(5 * 2 ** (r["failures"] - 1), 360)))
    _event(state, env, f"{key} transient failure {r['failures']}/{MAX_TRANSIENT_FAILURES}: {error[:120]}")
    if r["failures"] >= MAX_TRANSIENT_FAILURES:
        return _halt(state, env, key, code, f"gave up after {r['failures']} transient failures "
                     f"with backoff: {error}", kind="bounded_retry")
    return None


def _outcome(status: str, **fields) -> dict:
    return {"status": status, **fields}


def _exc(code: str, detail: str, human_action: str, resumes: str, **extra) -> dict:
    category, title = EXCEPTIONS[code]
    return _outcome(code, exception={"code": code, "category": category, "title": title,
                                     "detail": detail, "human_action": human_action,
                                     "resumes": resumes, **extra})


def _from_halt(h: dict, story_id: str, stage: str) -> dict:
    if h.get("probe"):
        resumes = f"automatically, the tick after {h['probe']} is available"
        action = f"fix: {h['detail']}"
    else:
        resumes = f"after `python3 nyx_runner.py unblock {story_id}`"
        action = f"fix the cause ({h['detail']}), then run unblock"
    return _exc(h["code"], f"{stage}: {h['detail']}", action, resumes, kind=h.get("kind"))


# --- slide pipeline --------------------------------------------------------------------------
def _handoff_paths(env: Env, story_id: str, slot: int, attempt: int) -> tuple[Path, Path]:
    base = env.episodes_dir / story_id / "handoff"
    return base / f"slide{slot}_attempt{attempt}.json", base / f"slide{slot}_attempt{attempt}.prompt.txt"


def _last_rejection(sc_state: dict, slot: int) -> dict | None:
    rej = [r for r in (sc_state.get("retry_state") or {}).get("rejected") or []
           if r["slide_index"] == slot]
    return rej[-1] if rej else None


def slide_budget(state: dict, slot: int) -> int:
    return QA_ATTEMPT_BUDGET + sum(g["extra"] for g in (state.get("grants") or {}).get(str(slot), []))


def ensure_handoff(env: Env, lock: dict, lock_sha: str, prompts: dict, active: dict,
                   sc_state: dict, slot: int, attempt: int, budget: int) -> dict:
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
                                attempt, budget)
    write_text_atomic(prompt_file, body)
    width, height = CANVAS[aspect_of(lock)]
    target = asset_path(env, lock, slot)
    packet = {
        "story_id": lock["story_id"], "item_index": lock["item_index"], "slide_index": slot,
        "attempt": attempt, "max_attempts": budget, "lock_sha256": lock_sha,
        "prompt_file": _rel(prompt_file), "prompt_sha256": sha256_text(body),
        "kind": "repair" if attempt > 1 else "first_attempt",
        "references": sc.reference_selection({}, active, record, index=int(lock["item_index"]),
                                             root=env.assets_root),
        "output": {"width": width, "height": height, "aspect_ratio": aspect_of(lock),
                   "format": "PNG", "expected_path": _rel(target)},
        "executor": "generation executor (human or an approved separate automation) -- never "
                    "this runner",
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
    if not (approved or attempts >= attempt):
        scores = {d: v["score"] == "PASS" for d, v in evidence["dimensions"].items()}
        out = sc.qa_slide(lock, sc_state, record, index=int(lock["item_index"]), scores=scores,
                          notes=evidence["notes"], root=env.assets_root)
        if out["status"] not in ("APPROVED", "REJECTED"):
            raise RunnerError(f"story_continuity refused the verdict: {out.get('error') or out['status']}")
        env.checkpoint("verdict_recorded_in_story_state")
    evidence["recorded"] = True
    write_json_atomic(evidence_file, evidence)
    return evidence


# --- zero-spend ChatGPT batch handoff (founder 2026-10-01) ------------------------------------
# While generation_executor is human_manual, every image comes from the founder's own ChatGPT
# subscription, by hand. To make that ONE sitting instead of one relay per slide, the runner
# assembles every image it currently needs into one compact batch, and ingests whatever the
# founder drops into the episode's drop folder on the next tick -- conformed to the canvas,
# provenance-bound to the batch, then QA, repair, captions, variants and the release package
# carry on with no further relay. Slides after slide 1 are drawn against slide 1 (the room
# master); if slide 1 is replaced, images drawn against the old one are superseded WITHOUT
# spending a QA attempt.
CHATGPT_OUTPUT = {"orientation": "portrait", "width": 1024, "height": 1536, "aspect_ratio": "2:3"}
DROP_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def drop_dir(env: Env, lock: dict) -> Path:
    return package_dir(env, lock) / "drop"


def _batch_root(env: Env, story_id: str) -> Path:
    return env.episodes_dir / story_id / "batch"


def current_batch(env: Env, story_id: str) -> dict | None:
    ptr = read_json(_batch_root(env, story_id) / "current.json")
    return read_json(_batch_root(env, story_id) / ptr["batch_id"] / "manifest.json") if ptr else None


def generation_refs() -> list[Path]:
    """The derived, generation-facing identity crops (identity_spec.generation_references):
    never the full reference sheets, whose human-ear panels and printed text models copy."""
    spec = carousel_handoff.load_identity_spec()
    return [carousel_handoff.PROJECT_ROOT / i["file"]
            for i in (spec.get("generation_references") or {}).get("images") or []]


def _hypothetical_state(lock: dict, sc_state: dict, slot: int) -> dict:
    """Story state as it WILL be when `slot` is generated, assuming the earlier
    slides land as planned: the same replay project_active_context does for
    approved slides, extended over the not-yet-approved ones. Slide 1 is the
    room master, so every later slide is compiled with the environment LOCKED
    to it -- exactly the text it will have once slide 1 is approved."""
    active = sc.project_active_context(sc_state, lock)
    if slot == 1:
        return active
    hyp = json.loads(json.dumps(active))
    done = {r["slide_index"] for r in hyp.get("approved_slide_refs") or []}
    story = dict(hyp.get("story_state") or {})
    history = list(hyp.get("composition_history") or [])
    for n in range(1, slot):
        if n in done:
            continue
        record = sc.plan_record(lock, n)
        story = {**story, **(record.get("state_after") or {}), "updated_from_slide": n}
        history.append(sc.composition_fingerprint(record))
    env_lock = dict(hyp.get("environment_lock") or {})
    if env_lock.get("status") != "LOCKED":
        env_lock.update(status="LOCKED", source_slide=1,
                        source_slide_ref=carousel_handoff.slide_filename(int(lock["item_index"]), 1))
    hyp.update(story_state=story, composition_history=history, environment_lock=env_lock)
    return hyp


def batch_prompt(lock: dict, sc_state: dict, slot: int, attempt: int, budget: int) -> dict:
    record = sc.plan_record(lock, slot)
    compiled = sc.compile_prompt({}, _hypothetical_state(lock, sc_state, slot), record)
    text = compiled["prompt"]
    repair = ""
    if attempt > 1:
        rej = _last_rejection(sc_state, slot) or {}
        full = ec.repair_prompt(lock, record, text, rej.get("failed") or [], rej.get("notes", ""),
                                attempt, budget)
        repair = full[len(text):].strip()
        text = full.rstrip("\n")
    blocks = [(s["title"], [ln for ln in s["text"].split("\n") if ln.strip()]) for s in compiled["sections"]]
    rest = text[len(compiled["prompt"]):] if repair else ""
    tail = compiled["prompt"].split("7. NEGATIVE CONSTRAINTS\n", 1)[1]
    negatives, _, after = tail.partition("\n\n")
    policy = [b for b in after.split("\n\n") if b.strip()]
    blocks.append(("7. NEGATIVE CONSTRAINTS", [n.strip() for n in negatives.split("; ") if n.strip()]))
    for b in policy:
        head, _, body = b.partition("\n")
        blocks.append((head, [ln for ln in body.split("\n") if ln.strip()]))
    if rest.strip():
        blocks.append(("TARGETED REPAIR", [ln for ln in rest.strip().split("\n") if ln.strip()]))
    return {"text": text, "sha256": sha256_text(text), "blocks": blocks,
            "character_presence": record.get("character_presence", "present"),
            "role": record["role"], "beat": record["story_beat"]}


def _valid_drop_asset(env: Env, lock: dict, slot: int, attempt: int) -> bool:
    asset = asset_path(env, lock, slot)
    prov = read_json(provenance_path(asset)) if asset.is_file() else None
    return bool(prov) and prov.get("attempt") == attempt


def _supersede(env: Env, lock: dict, slot: int, reason: str, state: dict) -> None:
    asset = asset_path(env, lock, slot)
    dest = package_dir(env, lock) / "superseded"
    dest.mkdir(parents=True, exist_ok=True)
    stamp = _iso(env.clock()).replace(":", "")
    for src in (asset, provenance_path(asset)):
        if src.is_file():
            os.replace(src, dest / f"{stamp}_{src.name}")
    _event(state, env, f"slide {slot} image superseded (no QA attempt spent): {reason}")


def needed_slides(env: Env, lock: dict, sc_state: dict, state: dict) -> list[tuple[int, int, int]]:
    """(slot, attempt, budget) for every unapproved slide that has no usable
    image for its current attempt. Stops at a slide whose budget is spent
    (that is exception 2, a human decision)."""
    active = sc.project_active_context(sc_state, lock)
    out = []
    for r in sc.story_plan(lock):
        n = r["slide_index"]
        if sc.approved_ref(active, n) is not None:
            continue
        used = int(sc_state["retry_state"]["attempts"].get(str(n), 0))
        budget = slide_budget(state, n)
        if used >= budget:
            break
        if not _valid_drop_asset(env, lock, n, used + 1):
            out.append((n, used + 1, budget))
    if out and out[0][0] == 1:
        # A new slide 1 means a new room master: images drawn against the old one
        # cannot be QA'd against it, so they are regenerated in the same sitting.
        for r in sc.story_plan(lock)[1:]:
            n = r["slide_index"]
            if sc.approved_ref(active, n) is None and asset_path(env, lock, n).is_file():
                _supersede(env, lock, n, "slide 1 (room master) is being regenerated", state)
            if sc.approved_ref(active, n) is None and all(o[0] != n for o in out):
                used = int(sc_state["retry_state"]["attempts"].get(str(n), 0))
                if used < slide_budget(state, n):
                    out.append((n, used + 1, slide_budget(state, n)))
        out.sort()
    return out


def _render_batch_md(lock: dict, manifest: dict, prompts: dict, room_ref: str | None,
                     drop: str) -> str:
    entries = manifest["entries"]

    def common(keys):
        if not keys:
            return []
        per = [dict(prompts[k]["blocks"]) for k in keys]
        out = []
        for title, lines in prompts[keys[0]]["blocks"]:
            shared = [ln for ln in lines if all(ln in p.get(title, []) for p in per)]
            if shared:
                out.append((title, shared))
        return out

    every = [str(e["slide"]) for e in entries]
    present = [str(e["slide"]) for e in entries if e["character_presence"] != "absent"]
    # One image: no primer split -- one message with the full prompt and every reference.
    room = common(every) if len(entries) > 1 else []
    room_lines = {(t, ln) for t, lines in room for ln in lines}
    nyx = ([(t, [ln for ln in lines if (t, ln) not in room_lines]) for t, lines in common(present)]
           if len(present) > 1 else [])
    nyx = [(t, ls) for t, ls in nyx if ls]
    nyx_lines = {(t, ln) for t, lines in nyx for ln in lines}

    def fmt(blocks):
        return "\n\n".join(f"{t}\n" + "\n".join(ls) for t, ls in blocks)

    def delta(key):
        skip = room_lines | (nyx_lines if key in present else set())
        out = [(t, [ln for ln in ls if (t, ln) not in skip]) for t, ls in prompts[key]["blocks"]]
        return fmt([(t, ls) for t, ls in out if ls])

    w, h = manifest["output"]["width"], manifest["output"]["height"]
    lines = [
        f"# {lock['story_id']} -- ChatGPT image batch `{manifest['batch_id']}` ({len(entries)} image"
        f"{'s' if len(entries) != 1 else ''})", "",
        "Zero cost: your own ChatGPT subscription, by hand. The runner uploads, posts and spends nothing.", "",
        "## Before you start", "",
        "1. Open ONE new ChatGPT conversation and send the messages below in order.",
        f"2. Every image: **portrait 2:3 ({CHATGPT_OUTPUT['width']}x{CHATGPT_OUTPUT['height']})**. Keep "
        f"everything that matters inside the central {manifest['output']['aspect_ratio']} area -- "
        f"the runner centre-crops and scales to **{w}x{h}**.",
        f"3. Save each image into **`{drop}/`** with the exact name given (png, jpg or webp).",
        "4. Run `python3 nyx_runner.py tick` (or let a scheduled tick find them). QA, repairs, "
        "captions, variants and the release package then continue on their own.",
        "5. If ChatGPT drifts on one image, paste that slide's full prompt from "
        "`prompts/slide<N>.txt` instead of the short message.", "",
    ]
    refs = ", ".join(f"`{r}`" for r in manifest["identity_references"])
    n = 0
    nyx_sent = False
    if room:
        n += 1
        identity_in_room = len(present) == len(every)
        lines += [f"## Message {n} -- shared rules (no image)", ""]
        attach = ([f"`{room_ref}` (approved slide 1: the room master)"] if room_ref else []) + \
                 ([refs + " (Nyx: face and fox ears)"] if identity_in_room else [])
        if attach:
            lines += ["Attach: " + "; ".join(attach), ""]
        lines += ["```text", "Context for a series of photos. Do not generate anything yet -- reply only "
                  "\"ready\". These rules apply to every image I ask for in this conversation:", "",
                  fmt(room), "```", ""]
        nyx_sent = identity_in_room
    for e in entries:
        key = str(e["slide"])
        if key in present and not nyx_sent and nyx:
            n += 1
            refs = ", ".join(f"`{r}`" for r in manifest["identity_references"])
            lines += [f"## Message {n} -- Nyx primer (no image)", "", f"Attach: {refs}", "",
                      "```text", "Identity rules for every image in which Nyx appears. The attached "
                      "crops are her face and fox ears -- match them exactly. Do not generate "
                      "anything yet -- reply only \"ready\".", "", fmt(nyx), "```", ""]
            nyx_sent = True
        n += 1
        tag = " (no Nyx in frame)" if e["character_presence"] == "absent" else ""
        kind = f", repair attempt {e['attempt']}" if e["attempt"] > 1 else ""
        lines += [f"## Message {n} -- slide {e['slide']}{tag}{kind} -> save as `{e['drop_name']}`", ""]
        attach = ([f"`{room_ref}` (approved slide 1: the room master)"]
                  if room_ref and not room and e["slide"] > 1 else []) + \
                 ([refs + " (Nyx: face and fox ears)"] if key in present and not nyx_sent else [])
        if attach:
            lines += ["Attach: " + "; ".join(attach), ""]
        lines += ["```text", f"Generate slide {e['slide']} now: one portrait 2:3 photo.", "",
                  delta(key), "```", ""]
    return "\n".join(lines)


def build_batch(env: Env, lock: dict, state: dict) -> dict | None:
    """Assemble (or reuse) the batch for every image currently needed. The id
    hashes the entries, so an unchanged need re-uses the same batch."""
    index = int(lock["item_index"])
    sc_state = sc.ensure_state(lock, index, root=env.assets_root)
    need = needed_slides(env, lock, sc_state, state)
    if not need:
        return None
    active = sc.project_active_context(sc_state, lock)
    slide1_ok = sc.approved_ref(active, 1) is not None
    s1 = asset_path(env, lock, 1)
    prompts, entries = {}, []
    for slot, attempt, budget in need:
        p = batch_prompt(lock, sc_state, slot, attempt, budget)
        prompts[str(slot)] = p
        env_master = None
        if slot > 1:
            env_master = f"sha:{sha256_file(s1)}" if slide1_ok else "batch:SELF"
        entries.append({"slide": slot, "attempt": attempt, "max_attempts": budget,
                        "prompt_sha256": p["sha256"], "character_presence": p["character_presence"],
                        "role": p["role"], "env_master": env_master,
                        "drop_name": f"slide{slot}.png",
                        "destination": _rel(asset_path(env, lock, slot))})
    batch_id = sha256_text(json.dumps(entries, sort_keys=True) + lock["story_id"])[:10]
    for e in entries:
        if e["env_master"] == "batch:SELF":
            e["env_master"] = f"batch:{batch_id}"
    root = _batch_root(env, lock["story_id"])
    existing = current_batch(env, lock["story_id"])
    if existing and existing["batch_id"] == batch_id:
        return existing
    width, height = CANVAS[aspect_of(lock)]
    manifest = {
        "story_id": lock["story_id"], "item_index": index, "batch_id": batch_id,
        "created_at": _iso(env.clock()),
        # Drop files are compared with the wall clock, never the (testable) runner clock.
        "created_wall": datetime.now(timezone.utc).timestamp(), "lock_sha256": sha256_file(ec.lock_path(lock["story_id"], env.locks_dir)),
        "executor": "founder, by hand, in ChatGPT (human_manual) -- zero spend",
        "chatgpt_output": CHATGPT_OUTPUT,
        "output": {"width": width, "height": height, "aspect_ratio": aspect_of(lock), "format": "PNG",
                   "conform": "centre-crop to the aspect ratio, then scale (Pillow or macOS sips)"},
        "drop_dir": _rel(drop_dir(env, lock)),
        "identity_references": [_rel(p) for p in generation_refs()],
        "room_reference": _rel(s1) if slide1_ok else "slide 1 of this batch (same conversation)",
        "entries": entries,
    }
    bdir = root / batch_id
    for key, p in prompts.items():
        write_text_atomic(bdir / "prompts" / f"slide{key}.txt", p["text"] + "\n")
    md = _render_batch_md(lock, manifest, prompts, _rel(s1) if slide1_ok else None, manifest["drop_dir"])
    write_text_atomic(bdir / "BATCH.md", md)
    write_json_atomic(bdir / "manifest.json", manifest)
    write_text_atomic(root / "CURRENT.md", md)
    write_json_atomic(root / "current.json", {"batch_id": batch_id, "created_at": manifest["created_at"]})
    drop_dir(env, lock).mkdir(parents=True, exist_ok=True)
    _event(state, env, f"batch {batch_id} assembled: slides {[e['slide'] for e in entries]}")
    env.checkpoint("batch_written")
    return manifest


def conform_ok(env: "Env | None" = None) -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return bool(shutil.which("sips"))


def conform_image(src: Path, dest: Path, aspect: str) -> dict:
    """Fit a ChatGPT image to the canvas: centre-crop to the aspect ratio, then
    scale. A file that is already the exact PNG canvas is copied untouched.
    Pillow if present, else macOS's built-in sips; both are free and local."""
    width, height = CANVAS[aspect]
    if src.suffix.lower() == ".png":
        info = png_info(src)
        if info.get("ok") and (info["width"], info["height"]) == (width, height):
            shutil.copyfile(src, dest)
            return {"conform": "none (already the exact canvas)"}
    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is not None:
        with Image.open(src) as img:
            img = img.convert("RGB")
            sw, sh = img.size
            target = width / height
            if sw / sh > target:
                cw, ch = int(round(sh * target)), sh
            else:
                cw, ch = sw, int(round(sw / target))
            left, top = (sw - cw) // 2, (sh - ch) // 2
            img.crop((left, top, left + cw, top + ch)).resize((width, height), Image.LANCZOS).save(dest, "PNG")
        return {"conform": f"Pillow: {sw}x{sh} centre-cropped to {cw}x{ch}, scaled to {width}x{height}"}
    sips = shutil.which("sips")
    if not sips:
        raise Fatal("conform: neither Pillow nor macOS sips is available to fit ChatGPT's "
                    f"{CHATGPT_OUTPUT['width']}x{CHATGPT_OUTPUT['height']} image to {width}x{height}",
                    kind="renderer", probe="conform")
    tmp = dest.with_suffix(".sips.png")
    shutil.copyfile(src, tmp)
    props = subprocess.run([sips, "-g", "pixelWidth", "-g", "pixelHeight", str(tmp)],
                           capture_output=True, text=True).stdout.split()
    sw, sh = int(props[props.index("pixelWidth:") + 1]), int(props[props.index("pixelHeight:") + 1])
    target = width / height
    cw, ch = (int(round(sh * target)), sh) if sw / sh > target else (sw, int(round(sw / target)))
    for cmd in ([sips, "-s", "format", "png", str(tmp), "--out", str(tmp)],
                [sips, "--cropToHeightWidth", str(ch), str(cw), str(tmp)],
                [sips, "-z", str(height), str(width), str(tmp)]):
        if subprocess.run(cmd, capture_output=True, text=True).returncode != 0:
            raise Unavailable(f"sips failed: {' '.join(cmd[1:3])}")
    os.replace(tmp, dest)
    return {"conform": f"sips: {sw}x{sh} centre-cropped to {cw}x{ch}, scaled to {width}x{height}"}


def ingest_drops(env: Env, lock: dict, state: dict) -> dict | None:
    """Pick up images the founder saved into the drop folder. Each one must
    match an entry of the CURRENT batch for the slide's current attempt and be
    newer than the batch; it is conformed, written to the slide's path with
    provenance bound to the batch, and the original is kept under ingested/.
    Anything else is moved to unexpected/ with the reason, never used."""
    folder = drop_dir(env, lock)
    files = sorted(p for p in folder.glob("slide*") if p.is_file() and p.suffix.lower() in DROP_SUFFIXES) \
        if folder.is_dir() else []
    if not files:
        return None
    halt = _halted(state, env, "ingest")
    if halt:
        return _from_halt(halt, lock["story_id"], "image intake")
    batch = current_batch(env, lock["story_id"])
    sc_state = sc.ensure_state(lock, int(lock["item_index"]), root=env.assets_root)
    for f in files:
        reason = None
        try:
            slot = int(f.stem.replace("slide", "").split("_")[0])
        except ValueError:
            slot, reason = None, "name is not slide<N>"
        entry = next((e for e in (batch or {}).get("entries", []) if e["slide"] == slot), None)
        attempt = int(sc_state["retry_state"]["attempts"].get(str(slot), 0)) + 1 if slot else 0
        if reason is None and entry is None:
            reason = "no open batch entry for this slide"
        elif reason is None and entry["attempt"] != attempt:
            reason = f"batch entry is attempt {entry['attempt']}, slide is on attempt {attempt}"
        elif reason is None and f.stat().st_mtime < batch.get("created_wall", 0) - 120:
            reason = "file is older than the batch it would answer"
        elif reason is None and (env.episodes_dir / lock["story_id"] / "qa" /
                                 f"slide{slot}_attempt{attempt}.json").is_file():
            reason = "this attempt was already reviewed"
        if reason:
            dest = folder / "unexpected"
            dest.mkdir(exist_ok=True)
            os.replace(f, dest / f"{_iso(env.clock()).replace(':', '')}_{f.name}")
            _event(state, env, f"drop {f.name} not used: {reason}")
            continue
        target = asset_path(env, lock, slot)
        if target.is_file():
            _supersede(env, lock, slot, f"replaced by a new drop ({f.name})", state)
        tmp = target.with_suffix(".conform.png")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            conform = conform_image(f, tmp, aspect_of(lock))
        except Fatal as exc:
            return _from_halt(_halt(state, env, "ingest", EXC_NON_RETRYABLE, str(exc), probe=exc.probe,
                                    kind=exc.kind), lock["story_id"], "image intake")
        except (Unavailable, OSError, ValueError) as exc:
            dest = folder / "unexpected"
            dest.mkdir(exist_ok=True)
            os.replace(f, dest / f"{_iso(env.clock()).replace(':', '')}_{f.name}")
            tmp.unlink(missing_ok=True)
            _event(state, env, f"drop {f.name} could not be read: {exc}")
            continue
        os.replace(tmp, target)
        write_json_atomic(provenance_path(target), {
            "story_id": lock["story_id"], "slide_index": slot, "attempt": attempt,
            "prompt_sha256": entry["prompt_sha256"], "batch_id": batch["batch_id"],
            "env_master": entry["env_master"], "generated_by": "founder via ChatGPT (batch drop)",
            "provider": carousel_handoff.PROVIDER, "original_name": f.name,
            "original_sha256": sha256_file(f), **conform, "asset_sha256": sha256_file(target),
            "delivered_at": _iso(env.clock()), "simulated": bool(env.sandbox)})
        kept = folder / "ingested"
        kept.mkdir(exist_ok=True)
        os.replace(f, kept / f"{batch['batch_id']}_{f.name}")
        _event(state, env, f"slide {slot} attempt {attempt} ingested from {f.name} ({conform['conform']})")
        env.checkpoint("drop_ingested")
    return None


def _prompt_matches(env: Env, story_id: str, slot: int, prov: dict, packet: dict) -> bool:
    if prov.get("batch_id"):
        manifest = read_json(_batch_root(env, story_id) / prov["batch_id"] / "manifest.json") or {}
        return any(e["slide"] == slot and e["attempt"] == prov.get("attempt")
                   and e["prompt_sha256"] == prov.get("prompt_sha256") for e in manifest.get("entries", []))
    return prov.get("prompt_sha256") == packet["prompt_sha256"]


def _env_master_valid(env: Env, lock: dict, prov: dict) -> bool:
    """A later slide is valid only against the APPROVED slide 1 it was drawn with."""
    s1 = asset_path(env, lock, 1)
    if not s1.is_file():
        return False
    s1prov = read_json(provenance_path(s1)) or {}
    return prov["env_master"] in (f"sha:{sha256_file(s1)}", f"batch:{s1prov.get('batch_id')}")


def _generate(env: Env, state: dict, lock: dict, slot: int, attempt: int, packet: dict) -> dict | None:
    """Ordinary generation stage. Returns None when an image was delivered
    (the slide loop continues), else the outcome to stop on."""
    story_id = lock["story_id"]
    packet_file = _handoff_paths(env, story_id, slot, attempt)[0]
    key = f"gen:{slot}:{attempt}"
    halt = _halted(state, env, key)
    if halt:
        return _from_halt(halt, story_id, f"slide {slot} generation")
    ex = executor_status(env)
    if not ex["available"]:
        if ex["code"] == EXC_EXECUTOR_DOWN:
            return _exc(EXC_EXECUTOR_DOWN, f"slide {slot} attempt {attempt}: {ex['reason']}",
                        "restore the approved executor, or deliver this image by hand with record-asset",
                        "automatically when the executor is available or the image is delivered",
                        slide=slot, attempt=attempt, handoff=_rel(packet_file))
        if ex["mode"] == "human_manual":
            batch = build_batch(env, lock, state)
            if batch:
                slides = [e["slide"] for e in batch["entries"]]
                doc = _rel(_batch_root(env, story_id) / "CURRENT.md")
                return _exc(EXC_GENERATION_ACTION,
                            f"batch {batch['batch_id']}: {len(slides)} image(s) needed, slides {slides}",
                            f"open {doc}, generate the {len(slides)} image(s) in one ChatGPT "
                            f"conversation and save them into {batch['drop_dir']}/ as "
                            + ", ".join(e["drop_name"] for e in batch["entries"])
                            + " -- then run `python3 nyx_runner.py tick`",
                            "automatically on the next tick after the images are dropped",
                            slide=slot, attempt=attempt, batch_id=batch["batch_id"], batch=doc,
                            slides=slides, drop_dir=batch["drop_dir"])
        return _exc(EXC_GENERATION_ACTION, f"slide {slot} attempt {attempt}: {ex['reason']}",
                    f"generate slide {slot} from {packet['prompt_file']} (handoff {_rel(packet_file)}) "
                    f"and run: {packet['deliver_with']}",
                    "automatically on the next tick after the image is delivered",
                    slide=slot, attempt=attempt, handoff=_rel(packet_file))
    if not _retry_due(state, key, env):
        return _outcome(RETRY_SCHEDULED, detail=f"slide {slot} generation retry at "
                        f"{state['retry'][key]['next_at']}: {state['retry'][key]['last_error']}")
    try:
        image = run_executor(env, packet_file, packet)
    except Fatal as exc:
        return _from_halt(_halt(state, env, key, EXC_EXECUTOR_DOWN, str(exc), probe=exc.probe,
                                kind=exc.kind), story_id, f"slide {slot} generation")
    except Unavailable as exc:
        halt = _transient(state, env, key, str(exc), EXC_EXECUTOR_DOWN)
        if halt:
            return _from_halt(halt, story_id, f"slide {slot} generation")
        return _outcome(RETRY_SCHEDULED, detail=f"slide {slot} generation: {exc}")
    cfg = load_executor(env)
    record_asset(env, story_id, slot, image, by=ex["name"], provider=cfg.get("provider", ex["name"]),
                 simulated=bool(cfg.get("simulated")))
    image.unlink(missing_ok=True)
    _retry_clear(state, key)
    _event(state, env, f"slide {slot} attempt {attempt} generated by {ex['name']}")
    env.checkpoint("asset_generated")
    return None


def advance_slides(env: Env, lock: dict, lock_sha: str, prompts: dict, state: dict) -> dict:
    """Walk slides in order until every slide is approved or a stop is reached."""
    index = int(lock["item_index"])
    story_id = lock["story_id"]
    stop = ingest_drops(env, lock, state)
    if stop:
        return stop
    for _ in range(MAX_TICK_STEPS):
        sc_state = sc.ensure_state(lock, index, root=env.assets_root)
        active = sc.project_active_context(sc_state, lock, root=env.assets_root)
        pending = [r for r in sc.story_plan(lock) if sc.approved_ref(active, r["slide_index"]) is None]
        slides = state.setdefault("slides", {})
        for r in sc.story_plan(lock):
            if sc.approved_ref(active, r["slide_index"]) is not None:
                slides[str(r["slide_index"])] = {"state": "APPROVED",
                                                 "asset": _rel(asset_path(env, lock, r["slide_index"]))}
        if not pending:
            return _outcome("SLIDES_APPROVED")
        record = pending[0]
        slot = record["slide_index"]
        used = int(sc_state["retry_state"]["attempts"].get(str(slot), 0))
        budget = slide_budget(state, slot)
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
        if used >= budget:
            failed = [r["failed"] for r in sc_state["retry_state"]["rejected"] if r["slide_index"] == slot]
            view.update(state="QA_BUDGET_EXHAUSTED", detail=f"{used} failed visual QA attempts")
            return _exc(EXC_QA_BUDGET, f"slide {slot}: {used} of {budget} attempts failed visual QA "
                        f"(failed dimensions per attempt: {failed})",
                        f"decide: simplify, re-sequence or drop slide {slot} in the lock, or allow one "
                        f"more attempt: python3 nyx_runner.py grant-attempt {story_id} {slot} --by <who>",
                        "after grant-attempt -- never automatically", slide=slot)
        gate = sc.readiness(active, record, root=env.assets_root)
        # The per-slide ceiling is this runner's policy (QA_ATTEMPT_BUDGET + grants);
        # every other readiness blocker (order, environment lock, staleness) still holds.
        blockers = [b for b in gate["blockers"] if "has used all" not in b]
        if blockers:
            view.update(state="BLOCKED", detail="; ".join(blockers))
            return _exc(EXC_NON_RETRYABLE, f"slide {slot} readiness: {'; '.join(blockers)}",
                        "inspect story state / the lock; run unblock once fixed",
                        "on the next tick after the cause is fixed", kind="state", slide=slot)
        attempt = used + 1
        packet = ensure_handoff(env, lock, lock_sha, prompts, active, sc_state, slot, attempt, budget)
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
            stop = _generate(env, state, lock, slot, attempt, packet)
            if stop:
                return stop
            continue
        prov = read_json(provenance_path(asset))
        problem = None
        if not prov:
            problem = "image has no provenance -- deliver it with record-asset"
        elif prov.get("attempt") != attempt or not _prompt_matches(env, story_id, slot, prov, packet):
            problem = "image provenance does not match the open handoff (stale or wrong prompt)"
        elif prov.get("simulated") and not env.sandbox:
            problem = "SIMULATED image refused outside a sandbox"
        if problem:
            view.update(state="ASSET_REFUSED", attempt=attempt, detail=problem)
            return _exc(EXC_GENERATION_ACTION, f"slide {slot} attempt {attempt}: {problem}",
                        f"replace the image through: {packet['deliver_with']}",
                        "automatically once a matching image is delivered", slide=slot, attempt=attempt)
        if slot > 1 and prov.get("env_master") and not _env_master_valid(env, lock, prov):
            _supersede(env, lock, slot, "drawn against a slide 1 that is not the approved room master", state)
            continue
        asset_sha = sha256_file(asset)
        ok, why = technical_gate(asset, aspect_of(lock))
        if not ok:
            dims = {d: {"score": "FAIL" if d == "technical_output" else "NOT_CHECKED",
                        "observation": why if d == "technical_output" else "not reviewed: failed technical gate"}
                    for d in sc.QA_DIMENSIONS}
            write_json_atomic(evidence_file, {
                "story_id": story_id, "slide_index": slot, "attempt": attempt,
                "asset_sha256": asset_sha, "reviewer": "deterministic_technical_gate",
                "simulated": False, "reviewed_at": _iso(env.clock()), "dimensions": dims,
                "notes": f"technical gate: {why}", "verdict": "REJECTED", "recorded": False})
            _event(state, env, f"slide {slot} attempt {attempt} rejected by technical gate: {why}")
            continue
        key = f"qa:{slot}:{attempt}"
        halt = _halted(state, env, key)
        if halt:
            view.update(state="HALTED", attempt=attempt, detail=halt["detail"])
            return _from_halt(halt, story_id, f"slide {slot} visual QA")
        if not _retry_due(state, key, env):
            view.update(state="QA_RETRY_SCHEDULED", attempt=attempt,
                        detail=state["retry"][key]["last_error"])
            return _outcome(RETRY_SCHEDULED, detail=f"slide {slot} visual QA retry at "
                            f"{state['retry'][key]['next_at']}")
        req = {"story_id": story_id, "slide_index": slot, "asset_abs": str(asset.resolve()),
               "references_abs": _reference_paths(env, lock, active, record),
               "character_presence": record.get("character_presence", "present"),
               "expectations": sc.qa_expectations(lock, record, active)}
        try:
            dims = _validate_verdict(env.reviewer.review(req))
        except Fatal as exc:
            halt = _halt(state, env, key, EXC_NON_RETRYABLE, str(exc), probe=exc.probe, kind=exc.kind)
            view.update(state="HALTED", attempt=attempt, detail=str(exc))
            return _from_halt(halt, story_id, f"slide {slot} visual QA")
        except Unavailable as exc:
            halt = _transient(state, env, key, str(exc), EXC_NON_RETRYABLE)
            if halt:
                view.update(state="HALTED", attempt=attempt, detail=halt["detail"])
                return _from_halt(halt, story_id, f"slide {slot} visual QA")
            view.update(state="QA_RETRY_SCHEDULED", attempt=attempt, detail=str(exc))
            return _outcome(RETRY_SCHEDULED, detail=f"slide {slot} visual QA: {exc}")
        if sha256_file(asset) != asset_sha:
            _event(state, env, f"slide {slot} image changed during review -- verdict discarded")
            continue
        _retry_clear(state, key)
        verdict = "APPROVED" if all(v["score"] == "PASS" for v in dims.values()) else "REJECTED"
        write_json_atomic(evidence_file, {
            "story_id": story_id, "slide_index": slot, "attempt": attempt,
            "asset_sha256": asset_sha, "reviewer": env.reviewer.name,
            "simulated": bool(getattr(env.reviewer, "simulated", False)),
            "reviewed_at": _iso(env.clock()), "dimensions": dims,
            "notes": "; ".join(f"{d}: {v['observation']}" for d, v in dims.items()
                               if v["score"] != "PASS") or "all dimensions pass",
            "verdict": verdict, "recorded": False})
        env.checkpoint("qa_evidence_written")
        _event(state, env, f"slide {slot} attempt {attempt} {verdict} by {env.reviewer.name}")
    return _outcome(IN_PROGRESS, detail="tick step limit reached; continues next tick")


# --- captions / variants / release ------------------------------------------------------------
def advance_captions(env: Env, lock: dict, state: dict) -> dict | None:
    """None = captions valid. Otherwise the outcome to stop on."""
    out = env.episodes_dir / lock["story_id"]
    check = ec.stage_captions(lock, out)
    if check["status"] == ec.PASS:
        state["captions"] = {"status": "PASS", "file": check["file"]}
        return None
    key = "captions"
    halt = _halted(state, env, key)
    if halt:
        return _from_halt(halt, lock["story_id"], "captions")
    if not _retry_due(state, key, env):
        return _outcome(RETRY_SCHEDULED, detail=f"captions retry at {state['retry'][key]['next_at']}")
    platforms = list(ec.platforms_for(lock))
    req = {"limits": {p: ec.CAPTION_LIMITS.get(p, 2200) for p in platforms},
           "needs_disclosure": [p for p in platforms if p not in ec.NATIVE_AI_LABEL],
           "slides": [{"beat": r["story_beat"], "overlay": r["overlay_copy"]} for r in sc.story_plan(lock)],
           "problems": check.get("problems")}
    try:
        drafted = env.author.write(req)
        caps = {p: str((drafted.get("captions") or {}).get(p) or "") for p in platforms}
    except Fatal as exc:
        return _from_halt(_halt(state, env, key, EXC_NON_RETRYABLE, str(exc), probe=exc.probe,
                                kind=exc.kind), lock["story_id"], "captions")
    except Unavailable as exc:
        halt = _transient(state, env, key, str(exc), EXC_NON_RETRYABLE)
        return (_from_halt(halt, lock["story_id"], "captions") if halt else
                _outcome(RETRY_SCHEDULED, detail=f"captions: {exc}"))
    write_json_atomic(out / "captions.json", {
        "story_id": lock["story_id"], "status": "DRAFT_AUTHORED_BY_RUNNER",
        "authored_by": env.author.name, "simulated": bool(getattr(env.author, "simulated", False)),
        "captions": caps})
    check = ec.stage_captions(lock, out)
    if check["status"] == ec.PASS:
        _retry_clear(state, key)
        state["captions"] = {"status": "PASS", "file": check["file"]}
        return None
    halt = _transient(state, env, key, "draft failed validation: " + "; ".join(check["problems"]),
                      EXC_NON_RETRYABLE)
    return (_from_halt(halt, lock["story_id"], "captions") if halt else
            _outcome(RETRY_SCHEDULED, detail="captions redraft scheduled"))


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


def advance_variants(env: Env, lock: dict, state: dict) -> dict | None:
    job = _render_job(env, lock)
    sources = {str(f["slide_index"]): sha256_file(Path(f["source_abs"])) for f in job["frames"]}
    have = state.get("variants") or {}
    if have.get("status") == "PASS" and have.get("source_sha256") == sources and all(
            Path(m["path_abs"]).is_file() and sha256_file(Path(m["path_abs"])) == m["sha256"]
            for files in have["platforms"].values() for m in files):
        return None                                     # idempotent: nothing changed
    key = "render"
    halt = _halted(state, env, key)
    if halt:
        return _from_halt(halt, lock["story_id"], "variants")
    if not _retry_due(state, key, env):
        return _outcome(RETRY_SCHEDULED, detail=f"render retry at {state['retry'][key]['next_at']}")
    try:
        rendered = env.renderer.render(job)
    except Fatal as exc:
        return _from_halt(_halt(state, env, key, EXC_NON_RETRYABLE, str(exc), probe=exc.probe,
                                kind=exc.kind), lock["story_id"], "variants")
    except Unavailable as exc:
        halt = _transient(state, env, key, str(exc), EXC_NON_RETRYABLE)
        return (_from_halt(halt, lock["story_id"], "variants") if halt else
                _outcome(RETRY_SCHEDULED, detail=f"render: {exc}"))
    _retry_clear(state, key)
    state["variants"] = {
        "status": "PASS", "renderer": env.renderer.name, "source_sha256": sources,
        "simulated": bool(getattr(env.renderer, "simulated", False)),
        "platforms": {p: [{"path": _rel(Path(f)), "path_abs": str(Path(f).resolve()),
                           "sha256": sha256_file(Path(f))} for f in files]
                      for p, files in rendered.items()}}
    env.checkpoint("variants_rendered")
    return None


def upsert_release(env: Env, lock: dict, state: dict, parent: str | None) -> dict:
    """The release package is the END of automation. Publishable entries wait for
    ONE package approval; routes that are not LIVE are HELD_NO_ROUTE and never
    block the package. Only a human moves anything past READY_FOR_PUBLISH."""
    qpath = env.episodes_dir / QUEUE_NAME
    queue = read_json(qpath, {"entries": {}})
    queue.setdefault("packages", {})
    story_id = lock["story_id"]
    captions = (read_json(env.episodes_dir / story_id / "captions.json", {}) or {}).get("captions", {})
    venture = read_json(env.venture_path, {}) or {}
    n_items = len((venture.get("content_plan") or {}).get("content_items") or [])
    invalidated = False
    for platform, files in state["variants"]["platforms"].items():
        key = f"{story_id}:{platform}"
        prev = queue["entries"].get(key) or {}
        ch = channels.CHANNELS.get(platform, {})
        media = [{"path": m["path"], "sha256": m["sha256"]} for m in files]
        if prev.get("status") == PUBLISHED_CONFIRMED:
            continue
        status = READY_FOR_PUBLISH if ch.get("publishes") else HELD_NO_ROUTE
        note = None
        if prev.get("status") == APPROVED_FOR_PUBLISH:
            if prev.get("media") == media and prev.get("caption") == captions.get(platform, ""):
                status = APPROVED_FOR_PUBLISH
            else:
                invalidated = True
                note = "publish approval invalidated: media or caption changed after approval"
        queue["entries"][key] = {
            "story_id": story_id, "platform": platform, "parent_episode": parent, "status": status,
            "route": ch.get("route"), "route_status": ch.get("status", channels.NOT_AVAILABLE),
            "hold_reason": None if ch.get("publishes") else ch.get("blocker"),
            "caption": captions.get(platform, ""), "media": media,
            "ai_disclosure": "platform AI label" if platform in ec.NATIVE_AI_LABEL else "in caption",
            "simulated": bool(state["variants"].get("simulated")),
            "requires_human_confirmation": True,
            "publish_prerequisites": {
                f"state/venture.json has content_items[{lock['item_index']}]":
                    int(lock["item_index"]) < n_items},
            "queued_at": prev.get("queued_at") or _iso(env.clock()),
            **({"note": note} if note else {}),
            **({k: prev[k] for k in ("approved_by", "approved_at") if k in prev and status == APPROVED_FOR_PUBLISH}),
        }
    mine = {k: e for k, e in queue["entries"].items() if e["story_id"] == story_id}
    publishable = sorted(k for k, e in mine.items() if e["status"] != HELD_NO_ROUTE)
    pkg = queue["packages"].get(story_id) or {}
    if publishable and all(mine[k]["status"] == PUBLISHED_CONFIRMED for k in publishable):
        pstatus = PUBLISHED
    elif pkg.get("status") == APPROVED_FOR_PUBLISH and not invalidated:
        pstatus = APPROVED_FOR_PUBLISH
    else:
        pstatus = "AWAITING_PUBLISH_APPROVAL"
    queue["packages"][story_id] = {
        "story_id": story_id, "parent_episode": parent, "status": pstatus,
        "entries": publishable, "held": sorted(k for k, e in mine.items() if e["status"] == HELD_NO_ROUTE),
        "approve_with": f"python3 nyx_runner.py approve-release {story_id} --by <who>",
        **({k: pkg[k] for k in ("approved_by", "approved_at") if k in pkg and pstatus != "AWAITING_PUBLISH_APPROVAL"}),
        **({"note": "earlier approval invalidated -- media or caption changed"} if invalidated else {}),
    }
    write_json_atomic(qpath, queue)
    return queue["packages"][story_id]


# --- one episode, one tick ---------------------------------------------------------------------
def _release_outcome(env: Env, lock: dict, pkg: dict) -> dict:
    story_id = lock["story_id"]
    if pkg["status"] == PUBLISHED:
        return _outcome(PUBLISHED, detail="every publishable entry confirmed published by a human")
    if pkg["status"] == APPROVED_FOR_PUBLISH:
        return _exc(EXC_PUBLISH_APPROVAL, f"package approved by {pkg.get('approved_by')}; awaiting the "
                    f"human publish of {pkg['entries']}",
                    "publish each approved entry yourself, then: python3 nyx_runner.py "
                    f"confirm-published {story_id} <platform> <live url> --by <who>",
                    "closes when every approved entry is confirmed published", package=story_id)
    return _exc(EXC_PUBLISH_APPROVAL, f"release package ready: {pkg['entries']}"
                + (f" (held, no live route: {pkg['held']})" if pkg["held"] else ""),
                f"review episodes/{QUEUE_NAME} and approve once: {pkg['approve_with']}; then publish "
                "and confirm-published per entry -- the runner never publishes",
                "after approve-release and confirm-published", package=story_id)


def advance_episode(env: Env, entry: dict) -> dict:
    story_id = entry["story_id"]
    lock = ec.load_lock(story_id, env.locks_dir)
    lock_sha = sha256_file(ec.lock_path(story_id, env.locks_dir))
    path = state_path(env, story_id)
    state = read_json(path) or {"story_id": story_id, "kind": entry.get("kind", "public_episode"),
                                "created_at": _iso(env.clock())}
    state.update(story_id=story_id, parent=entry.get("parent"), lock_sha256=lock_sha,
                 item_index=lock["item_index"], total_slides=len(lock.get("story_plan") or []),
                 qa_attempt_budget=QA_ATTEMPT_BUDGET)
    out = env.episodes_dir / story_id
    brief = ec.stage_brief(lock, ec.load_season(lock.get("season_id"), env.seasons_dir),
                           locks_dir=env.locks_dir)
    outcome = None
    if brief["status"] == ec.BLOCKED:
        outcome = _exc(EXC_NON_RETRYABLE, f"invalid story lock (brief): {brief['fix']}",
                       "fix the lock; the next tick re-checks it", "automatically once the lock is valid",
                       kind="lock")
    else:
        prompts = ec.stage_locked_prompts(lock, lock_sha, out, patch=None,
                                          assets_root=env.assets_root, write=True)
        if prompts["status"] != ec.PASS:
            outcome = _exc(EXC_NON_RETRYABLE, f"invalid story lock (prompts): {prompts['fix']}",
                           "fix the lock; the next tick re-checks it",
                           "automatically once the lock is valid", kind="lock")
        else:
            outcome = advance_slides(env, lock, lock_sha, prompts, state)
    if outcome["status"] == "SLIDES_APPROVED":
        outcome = advance_captions(env, lock, state) or advance_variants(env, lock, state)
        if outcome is None:
            outcome = _release_outcome(env, lock, upsert_release(env, lock, state, entry.get("parent")))
    if entry.get("kind") != "fanvue_chapter":
        chapter = next((e for e in load_registry(env)
                        if e.get("kind") == "fanvue_chapter" and e.get("parent") == story_id), None)
        state["fanvue_chapter"] = chapter["story_id"] if chapter else None
    exc = outcome.get("exception")
    prev = state.get("exception")
    if exc and prev and prev.get("code") == exc["code"]:
        exc["since"] = prev.get("since")
    elif exc:
        exc["since"] = _iso(env.clock())
    if state.get("status") != outcome["status"]:
        _event(state, env, f"status {state.get('status')} -> {outcome['status']}")
    state["status"] = outcome["status"]
    state["exception"] = exc
    state["detail"] = outcome.get("detail")
    state["updated_at"] = _iso(env.clock())
    write_json_atomic(path, state)
    return state


def sync_exception_queue(env: Env) -> dict:
    """episodes/exception_queue.json: every open stop, one row per episode, plus
    a short history of resolved ones -- the human's single inbox."""
    qpath = env.episodes_dir / EXCEPTIONS_NAME
    prev = read_json(qpath, {"open": {}, "resolved": []})
    open_now = {}
    for entry in load_registry(env):
        st = read_json(state_path(env, entry["story_id"]), {}) or {}
        if st.get("exception"):
            open_now[entry["story_id"]] = {**st["exception"], "story_id": entry["story_id"]}
    resolved = list(prev.get("resolved") or [])
    for key, old in (prev.get("open") or {}).items():
        now = open_now.get(key)
        if not now or now["code"] != old["code"]:
            resolved.append({**old, "resolved_at": _iso(env.clock())})
    data = {"policy": "exception-only continuation: the runner stops only for these; everything "
                      "else resumes automatically", "open": open_now, "resolved": resolved[-50:]}
    write_json_atomic(qpath, data)
    return data


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
        sync_exception_queue(env)
        write_text_atomic(env.episodes_dir / STATUS_NAME, render_status(env))
        return {"ran": True, "episodes": results}
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


# --- executor / human commands (bookkeeping only) -----------------------------------------------
def record_asset(env: Env, story_id: str, slot: int, image: Path, *, by: str, provider: str,
                 simulated: bool = False) -> dict:
    """The generation EXECUTOR's single delivery path: copies its image to the
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


def _need(value: str, what: str) -> None:
    if not str(value or "").strip():
        raise RunnerError(f"{what} is required")


def grant_attempt(env: Env, story_id: str, slot: int, *, by: str) -> dict:
    """Exception 2 resolution: a human allows one more attempt on ONE slide."""
    _need(by, "--by")
    state = read_json(state_path(env, story_id))
    exc = (state or {}).get("exception") or {}
    if exc.get("code") != EXC_QA_BUDGET or exc.get("slide") != slot:
        raise RunnerError(f"{story_id} slide {slot} is not in {EXC_QA_BUDGET}")
    state.setdefault("grants", {}).setdefault(str(slot), []).append(
        {"extra": 1, "by": by, "at": _iso(env.clock())})
    _event(state, env, f"grant-attempt slide {slot} by {by}")
    write_json_atomic(state_path(env, story_id), state)
    return {"story_id": story_id, "slide": slot, "budget": slide_budget(state, slot)}


def approve_release(env: Env, story_id: str, *, by: str) -> dict:
    """Exception 3's single approval: covers every publishable entry of the
    package as it is NOW (media + caption). It authorizes a human publish; it
    publishes nothing."""
    _need(by, "--by")
    qpath = env.episodes_dir / QUEUE_NAME
    queue = read_json(qpath, {"entries": {}, "packages": {}})
    pkg = (queue.get("packages") or {}).get(story_id)
    if not pkg or pkg["status"] != "AWAITING_PUBLISH_APPROVAL":
        raise RunnerError(f"{story_id} has no release package awaiting approval")
    now = _iso(env.clock())
    for key in pkg["entries"]:
        if queue["entries"][key]["status"] == READY_FOR_PUBLISH:
            queue["entries"][key].update(status=APPROVED_FOR_PUBLISH, approved_by=by, approved_at=now)
    pkg.update(status=APPROVED_FOR_PUBLISH, approved_by=by, approved_at=now)
    pkg.pop("note", None)
    write_json_atomic(qpath, queue)
    return pkg


def confirm_published(env: Env, story_id: str, platform: str, url: str, *, by: str) -> dict:
    """Records a publish a HUMAN already performed and verified. Never publishes."""
    _need(by, "--by")
    _need(url, "the live url")
    qpath = env.episodes_dir / QUEUE_NAME
    queue = read_json(qpath, {"entries": {}})
    entry = queue["entries"].get(f"{story_id}:{platform}")
    if not entry or entry["status"] != APPROVED_FOR_PUBLISH:
        raise RunnerError(f"{story_id}:{platform} is not {APPROVED_FOR_PUBLISH} -- approve the "
                          f"release package first")
    entry.update(status=PUBLISHED_CONFIRMED, live_url=url, confirmed_by=by,
                 confirmed_at=_iso(env.clock()))
    write_json_atomic(qpath, queue)
    return entry


def unblock(env: Env, story_id: str) -> dict:
    """Exception 4 resolution after the cause is fixed: clears halts and retry
    counters. Touches no verdict, approval or attempt count."""
    path = state_path(env, story_id)
    state = read_json(path)
    if not state:
        raise RunnerError(f"no runner state for {story_id}")
    cleared = sorted(set((state.get("retry") or {})) | set(state.get("halts") or {}))
    state["retry"], state["halts"] = {}, {}
    _event(state, env, f"unblocked: cleared {cleared}")
    write_json_atomic(path, state)
    return {"story_id": story_id, "cleared": cleared}


# --- activation readiness and status ---------------------------------------------------------------
def _can_progress_unattended(env: Env, st: dict) -> tuple[bool, str]:
    status = st.get("status", "NOT_RUN")
    exc = st.get("exception") or {}
    if status in ("NOT_RUN", IN_PROGRESS, RETRY_SCHEDULED):
        return True, f"{status}: the next tick continues on its own"
    if status in (EXC_GENERATION_ACTION, EXC_EXECUTOR_DOWN):
        ex = executor_status(env)
        return (ex["available"], "an approved unattended executor would generate the next image"
                if ex["available"] else f"needs a human image delivery ({ex['reason']})")
    if status == EXC_NON_RETRYABLE:
        halts = [h for h in (st.get("halts") or {}).values() if h.get("probe")]
        if halts and all(probe_ok(h["probe"], env) for h in halts):
            return True, "the missing tool is now available; the next tick resumes"
        return False, f"needs a human fix: {exc.get('detail', '')[:160]}"
    if status == EXC_QA_BUDGET:
        return False, "needs a human decision on the exhausted slide"
    if status == EXC_PUBLISH_APPROVAL:
        return False, "needs the final publish approval and a human publish"
    return False, status


def activation_readiness(env: Env) -> dict:
    """Would installing the scheduler (ops/com.nyx.episode-runner.plist) make
    meaningful progress right now? Read-only: installs nothing, calls nothing
    external. Based on the last tick's durable state plus live probes."""
    ex = executor_status(env)
    deps = {
        "generation executor": {"ok": ex["available"], "detail": ex["reason"]},
        "visual QA reviewer (claude CLI; credentials untested)": {"ok": probe_ok("claude"),
                                                                  "detail": "`claude` on PATH"},
        "image conform (Pillow or macOS sips)": {"ok": probe_ok("conform"),
                                                 "detail": "fits ChatGPT 1024x1536 to the canvas"},
        "overlay renderer (Pillow)": {"ok": probe_ok("pillow"), "detail": "import PIL"},
        "TikTok renderer (ffmpeg)": {"ok": probe_ok("ffmpeg"), "detail": "`ffmpeg` on PATH"},
        "release prerequisites (state/venture.json)": {"ok": env.venture_path.is_file(),
                                                       "detail": str(_rel(env.venture_path))},
    }
    episodes = {}
    for entry in load_registry(env):
        st = read_json(state_path(env, entry["story_id"]), {}) or {}
        ok, why = _can_progress_unattended(env, st)
        episodes[entry["story_id"]] = {"status": st.get("status", "NOT_RUN"), "progress_unattended": ok,
                                       "why": why}
    meaningful = any(e["progress_unattended"] for e in episodes.values())
    next_action = next(((sid, (read_json(state_path(env, sid), {}) or {}).get("exception") or {})
                        for sid in episodes
                        if (read_json(state_path(env, sid), {}) or {}).get("exception")), (None, {}))
    blockers = []
    if not ex["available"]:
        blockers.append(f"image generation: {ex['reason']} -- every slide stops at "
                        f"{ex['code']} until a human delivers it")
    blockers += [f"{name}: missing ({d['detail']})" for name, d in deps.items()
                 if not d["ok"] and name != "generation executor"]
    if meaningful:
        verdict = "WOULD MAKE PROGRESS: a scheduled tick has ordinary work it can do unattended."
    elif not ex["available"]:
        verdict = ("WOULD NOT MAKE MEANINGFUL PROGRESS: with no approved unattended generation "
                   "executor, a scheduled tick would only re-report the open generation exception. "
                   "No scheduler is needed in zero-spend mode: after dropping a batch's images, one "
                   "`python3 nyx_runner.py tick` runs QA, repairs, captions, variants and the "
                   "release package without further relays.")
    else:
        verdict = "WOULD NOT MAKE MEANINGFUL PROGRESS: every episode is at a human exception."
    return {"meaningful_progress": meaningful, "verdict": verdict, "dependencies": deps,
            "next_human_action": ({"episode": next_action[0], "exception": next_action[1].get("code"),
                                   "action": next_action[1].get("human_action")}
                                  if next_action[0] else None),
            "episodes": episodes, "blockers": blockers, "scheduler_installed_by_this_repo": False}


def render_status(env: Env) -> str:
    lines = ["# Nyx episode runner -- status", "",
             "Exception-only continuation. Ordinary stages resume on their own; the runner stops "
             "only for the four exceptions below and never publishes externally.", "",
             "| Episode | Status | Slides approved | Exception / next | Resumes |", "|---|---|---|---|---|"]
    for entry in load_registry(env):
        st = read_json(state_path(env, entry["story_id"]), {}) or {}
        approved = sum(1 for s in (st.get("slides") or {}).values() if s.get("state") == "APPROVED")
        exc = st.get("exception")
        name = entry["story_id"] + (f" (Fanvue chapter of {entry['parent']})" if entry.get("parent") else "")
        if exc:
            nxt = f"**#{exc['category']} {exc['title']}** -- {exc['human_action']}"
            resumes = exc["resumes"]
        else:
            nxt, resumes = st.get("detail") or "automatic", "automatically"
        lines.append(f"| {name} | {st.get('status', 'NOT_RUN')} | {approved}/{st.get('total_slides', '?')} "
                     f"| {nxt} | {resumes} |")
        if entry.get("kind") != "fanvue_chapter" and not st.get("fanvue_chapter"):
            lines.append(f"| ↳ Fanvue chapter | NO_APPROVED_LOCK | - | a Fanvue chapter needs its own "
                         f"founder-approved lock, enrolled with parent {entry['story_id']} | when enrolled |")
    queue = read_json(env.episodes_dir / QUEUE_NAME, {}) or {}
    lines += ["", "## Release packages (never published by the runner)", ""]
    pkgs = queue.get("packages") or {}
    if not pkgs:
        lines.append("None yet.")
    for sid, pkg in sorted(pkgs.items()):
        sim = " (SIMULATED)" if any(queue["entries"][k].get("simulated") for k in pkg["entries"]) else ""
        lines.append(f"- {sid}: {pkg['status']}{sim}; publishable {pkg['entries']}; held {pkg['held']}")
    ready = activation_readiness(env)
    lines += ["", "## Activation readiness (scheduler not installed)", "", f"**{ready['verdict']}**", ""]
    lines += [f"- {'ok' if d['ok'] else 'MISSING'}: {name} -- {d['detail']}"
              for name, d in ready["dependencies"].items()]
    if ready["blockers"]:
        lines += ["", "Remaining blockers to unattended progress:", ""]
        lines += [f"- {b}" for b in ready["blockers"]]
    return "\n".join(lines) + "\n"


# --- simulation (sandbox only) --------------------------------------------------------------------
def _pattern_png(path: Path, width: int, height: int, seed: int) -> None:
    """A varied, obviously synthetic test pattern (NOT a Nyx image) for sandbox
    executors. Passes the technical gate by design so the QA/repair/variant
    paths run; provenance marks it simulated, so a real run refuses it."""
    base = bytes((i * 7 + seed * 31) % 251 for i in range(width * 3))
    rows = b"".join(b"\x00" + base[(y * 3) % len(base):] + base[:(y * 3) % len(base)]
                    for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


class SimulatedReviewer:
    """Scripted verdicts for the sandbox. `reject` maps (slot, attempt) -> failed dims;
    `outages` transient failures first; `fatal` raises a non-retryable error."""
    name, simulated = "SIMULATED_reviewer", True

    def __init__(self, reject=None, outages: int = 0, fatal: Exception | None = None):
        self.reject, self.outages, self.fatal, self.calls = reject or {}, outages, fatal, 0

    def review(self, req: dict) -> dict:
        self.calls += 1
        if self.fatal:
            raise self.fatal
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
        return {"captions": {p: f"simulated {p} caption. {last}"
                             + (" #AIgenerated" if p in req["needs_disclosure"] else "")
                             for p in req["limits"]}}


class SimulatedRenderer:
    name, simulated = "SIMULATED_renderer", True

    def render(self, job: dict) -> dict:
        out = {}
        for f in job["frames"]:
            if Path(f["final_abs"]) != Path(f["source_abs"]):
                shutil.copyfile(f["source_abs"], f["final_abs"])
        finals = [Path(f["final_abs"]) for f in job["frames"]]
        for p in job["platforms"]:
            if p == "tiktok":
                video = job["package_dir"] / f"item{job['item_index']}_tiktok.SIMULATED.mp4"
                video.write_bytes(b"SIMULATED slideshow -- not a video")
                out[p] = [video]
            else:
                out[p] = finals
        return out


_FAKE_EXECUTOR = '''"""SIMULATED command executor (sandbox only): writes a synthetic test pattern."""
import json, sys
sys.path.insert(0, {root!r})
from pathlib import Path
import nyx_runner as nr
packet = json.loads(Path(sys.argv[1]).read_text())
nr._pattern_png(Path(sys.argv[2]), packet["output"]["width"], packet["output"]["height"],
                packet["slide_index"] * 10 + packet["attempt"])
'''


def simulated_executor(env: Env, *, chatgpt_size: bool = False) -> int:
    """Stands in for the FOUNDER: reads each episode's current batch and saves one
    image per entry into the drop folder, exactly as a person would after a
    ChatGPT sitting. `chatgpt_size` drops 1024x1536 so the conform step runs."""
    delivered = 0
    for entry in load_registry(env):
        lock = ec.load_lock(entry["story_id"], env.locks_dir)
        st = read_json(state_path(env, entry["story_id"]), {}) or {}
        batch = current_batch(env, entry["story_id"])
        if not batch or (st.get("exception") or {}).get("batch_id") != batch["batch_id"]:
            continue
        w, h = ((CHATGPT_OUTPUT["width"], CHATGPT_OUTPUT["height"]) if chatgpt_size
                else CANVAS[aspect_of(lock)])
        for e in batch["entries"]:
            _pattern_png(drop_dir(env, lock) / e["drop_name"], w, h, e["slide"] * 10 + e["attempt"])
            delivered += 1
    return delivered


def build_sandbox(base: Path, *, reviewer=None, unattended: bool = False) -> Env:
    """A throwaway copy of the real s1e02_public lock and captions plus a
    SIMULATED Fanvue chapter fixture (cloned from s1e01_fanvue_part1's plan,
    renamed, never written back to the real repo). `unattended` configures a
    SIMULATED approved command executor instead of a human one."""
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
    if unattended:
        script = base / "fake_executor.py"
        script.write_text(_FAKE_EXECUTOR.format(root=str(ROOT)))
        write_json_atomic(eps / EXECUTOR_NAME, {
            "mode": "command", "name": "SIMULATED_command_executor", "provider": "SIMULATED",
            "command": [sys.executable, str(script), "{packet}", "{output}"], "cost": "zero",
            "approved_by": "SIMULATED sandbox", "simulated": True})
    else:
        write_json_atomic(eps / EXECUTOR_NAME, {"mode": "human_manual"})
    clock = {"now": datetime(2026, 10, 1, tzinfo=timezone.utc)}
    env = Env(locks_dir=locks, episodes_dir=eps, assets_root=assets, venture_path=base / "no_venture.json",
              reviewer=reviewer or SimulatedReviewer(reject={(2, 1): ["identity"]}),
              author=SimulatedAuthor(), renderer=SimulatedRenderer(), sandbox=True,
              clock=lambda: clock["now"])
    env._clock_state = clock
    return env


def run_until_settled(env: Env, *, max_rounds: int = 80, human_executor: bool = True,
                      step: timedelta = timedelta(hours=1)) -> dict:
    """Tick (and, if asked, let the simulated HUMAN executor deliver), advance the
    fake clock, repeat until every episode sits at a human exception other than
    generation, or nothing moves."""
    result, stable = None, 0
    for _ in range(max_rounds):
        prev = result
        result = tick(env)
        delivered = simulated_executor(env) if human_executor else 0
        env._clock_state["now"] += step
        settled = all(s in (EXC_PUBLISH_APPROVAL, EXC_QA_BUDGET, EXC_NON_RETRYABLE, PUBLISHED)
                      for s in result["episodes"].values())
        if settled:
            return result
        stable = stable + 1 if (not delivered and result == prev) else 0
        if stable >= 8:                      # outlasts every backoff window at 1 h steps
            return result
    return result


def simulate(interrupt: bool = False, unattended: bool = False) -> dict:
    with tempfile.TemporaryDirectory(prefix="nyx_runner_sim_") as tmp:
        env = build_sandbox(Path(tmp), unattended=unattended)
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
                    result = run_until_settled(env, human_executor=not unattended)
                    break
                except KeyboardInterrupt:
                    crashes += 1
                    env.reviewer = SimulatedReviewer(reject={(2, 1): ["identity"]})   # fresh process
        else:
            result = run_until_settled(env, human_executor=not unattended)
        queue = read_json(env.episodes_dir / QUEUE_NAME, {"entries": {}, "packages": {}})
        exceptions = read_json(env.episodes_dir / EXCEPTIONS_NAME, {"open": {}})
        sc_state = sc.load_state(ec.load_lock("s1e02_public", env.locks_dir), 33, root=env.assets_root)
        return {"episodes": result["episodes"], "crashes_survived": crashes,
                "executor": "SIMULATED unattended command" if unattended else "SIMULATED human",
                "open_exceptions": {k: v["code"] for k, v in exceptions["open"].items()},
                "packages": {k: p["status"] for k, p in queue.get("packages", {}).items()},
                "queue": {k: e["status"] for k, e in sorted(queue["entries"].items())},
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
        return rest[rest.index(name) + 1] if name in rest and rest.index(name) + 1 < len(rest) else ""
    try:
        if cmd == "tick":
            out = tick()
        elif cmd == "status":
            env = real_env()
            text = render_status(env)
            write_text_atomic(env.episodes_dir / STATUS_NAME, text)
            print(text)
            return 0
        elif cmd == "readiness":
            out = activation_readiness(real_env())
        elif cmd == "record-asset":
            out = record_asset(real_env(), rest[0], int(rest[1]), Path(rest[2]),
                               by=opt("--by"), provider=opt("--provider"))
        elif cmd == "grant-attempt":
            out = grant_attempt(real_env(), rest[0], int(rest[1]), by=opt("--by"))
        elif cmd == "approve-release":
            out = approve_release(real_env(), rest[0], by=opt("--by"))
        elif cmd == "confirm-published":
            out = confirm_published(real_env(), rest[0], rest[1], rest[2], by=opt("--by"))
        elif cmd == "unblock":
            out = unblock(real_env(), rest[0])
        elif cmd == "simulate":
            out = simulate(interrupt="--interrupt" in rest, unattended="--unattended" in rest)
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
