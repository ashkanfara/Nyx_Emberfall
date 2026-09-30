"""Local, zero-incremental-cost still-image provider for PS-05 stories.

Founder direction 2026-09-22: routine content is story-driven STILL sequences,
generated free-first. Video/Wan is not the default. Paid generation stays
fail-closed (see stages.paid_generation_approved) and this module can never
reach it -- it imports no provider SDK, no network client and no credential.

Selected model: FLUX.2 klein 4B (Black Forest Labs), Apache-2.0, text-to-image
plus single- and multi-reference editing, so Nyx's canonical primary reference
can condition every slide. Apache-2.0 is what makes it usable for PS-05's
commercial social/Fanvue content -- Qwen-Image-2.1 was rejected because its
licence is non-commercial-only.

Runtime (integrated 2026-09-26): a verified MFLUX (Apple MLX) installation on
the external SSD at STUDIO_ROOT runs the actual 4-bit FLUX.2 Klein 4B weights
-- MFLUX 0.20.0, MLX 0.32.2, real M1 Pro 16GB results 27.4s/4.28GB (512 T2I)
and 61.8s/4.35GB (768x960 reference edit). This replaces the never-executed
diffusers/bf16 design (which assumed >=24GB RAM and internal-disk weights
that were never actually fetched): this process never imports torch/
diffusers/mlx itself and never touches the network -- it shells out to that
installation's own studio.py via its documented batch-manifest interface (its
README's "Agent / batch use" section), which sets HF_HUB_OFFLINE itself and
has no paid-API fallback of its own.

DELIBERATELY PREFLIGHT-FIRST. `preflight()` measures the real machine and the
real SSD/runtime/weights state and returns a structured verdict; it downloads
nothing, installs nothing and generates nothing. Weight integrity is checked
against the model's OWN provenance manifest (every listed file present at its
declared size), not "some safetensors file exists somewhere". `generate()`
refuses unless preflight is READY; validates the project slug and every
resolved path BEFORE any adapter-side mkdir/copy/write; copies references
under content-addressed names (never a same-named-but-different-content
collision); and fails closed -- never a paid fallback, never invented
dimensions, never a claimed success the produced file doesn't back up -- on a
missing SSD, a missing reference file, an invalid/wrong-size output image, or
a non-zero-exit/timed-out/OS-level runtime error.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
ROUTE = "local"
COST_CLASS = "zero_incremental_cost"

MODEL_ID = "black-forest-labs/FLUX.2-klein-4B (mflux 4-bit: Runpod/FLUX.2-klein-4B-mflux-4bit)"
MODEL_LICENSE = "Apache-2.0"
MODEL_SOURCE = "https://github.com/black-forest-labs/flux2"

# The SSD installation Codex installed and verified 2026-09-26. Overridable so
# this is never a machine-specific hardcode; the default matches what is
# actually documented/verified. Every other path below is derived from this
# ONE global at CALL time (never cached at import time), so tests can isolate
# an entire fake studio tree by reassigning just this name.
STUDIO_ROOT = Path(os.environ.get("PS05_LOCAL_IMAGE_STUDIO",
                                  "/Volumes/Media Library/Local Image Studio"))
REQUIRED_BINARIES = ("mflux-generate-flux2", "mflux-generate-flux2-edit")

DEFAULT_PROJECT = "nyx"
# studio.py's own rule (project_dir() in studio.py): lowercase letters,
# digits, hyphens and underscores, 1-64 chars. Checked here too, and BEFORE
# any adapter-side filesystem write, so a bad slug never reaches mkdir.
_PROJECT_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")

# The ONLY benchmarked resolution (verification.json, 2026-09-26). studio.py's
# own validation additionally requires 256..1024, a multiple of 16 -- both
# checked below so this module can never silently ask for an untested or
# out-of-range size.
PORTRAIT_WIDTH, PORTRAIT_HEIGHT = 768, 960
MIN_DIM, MAX_DIM = 256, 1024

# STRUCTURAL sanity floor only -- catches a genuinely blank/near-solid-colour
# output (a real generation-failure mode PIL's own verify()+dimension check
# cannot see: a technically valid, correctly-sized PNG that still isn't a
# real photo). This is NOT a vision/quality check and must never be read as
# one -- it says nothing about hands, ears, identity or props; a candidate
# with malformed hands or missing ears has plenty of pixel variance and
# passes this exactly like a good one. Calibrated 2026-09-27 against every
# real image this story has produced (content_items[20] slides 1-4, both
# founder-approved and founder-rejected, plus the two identity/ears/hands
# benchmark samples): the lowest real photo measured std 37.4; a
# synthetic flat-colour image measures std ~2. MIN_PIXEL_STD leaves wide
# margin on both sides rather than sitting close to either real number.
MIN_PIXEL_STD = 10.0

# Real measured peak MLX allocation on this model is ~4.35GB; 12GB leaves
# meaningful headroom above that for the OS and other apps, rather than the
# old design's 24GB figure, which assumed a different (never-run) bf16/
# diffusers pipeline this machine was never going to satisfy.
MIN_RAM_GB = 12.0
MIN_FREE_DISK_GB = 2.0          # weights already live on the SSD; only job/output space is local
GENERATION_TIMEOUT_S = 320      # slowest measured case (61.8s) with real margin for a loaded machine
DRY_RUN_TIMEOUT_S = 30
PROCESS_KILL_GRACE_S = 10       # time given to SIGTERM before escalating to SIGKILL
MIN_STEPS, MAX_STEPS = 1, 50    # studio.py's own validated range for its (shared) --steps flag
# Raised from 4 (this installation's original, install-verification-only
# value -- see verification.json, which measured render time/memory, never
# hand-anatomy quality) after a real benchmark render 2026-09-26 against
# content_items[20] slide 2's own real prompt/references/seed -- see
# generated_assets/carousel_item20/benchmarks/local_engine_improvement_2026-
# 09-26/. steps=8 (232s) and steps=12 (332s, recovered after a client
# timeout -- see that benchmark's own honest note on the orphaned-process
# gap this surfaced) both showed clean, correctly-countable fingers with no
# fusion/extra digits, visibly unlike the founder-rejected steps=4 baseline
# (item20_slide2.png). steps=8 is chosen over 12 for cost: no visible further
# gain was observed at 12, at ~100s more per slide. IMPORTANT CAVEAT: this is
# ONE seed, ONE slide/prompt, ONE reviewer (this session) -- a real
# improvement signal, not a proven, statistically validated fix; still
# subject to the SAME real visual QA (native or delegated) as any other
# candidate before approval.
DEFAULT_STEPS = 8

# Both mflux binaries this installation runs (mflux-generate-flux2[-edit])
# document --negative-prompt in --help (runtime/generate-help.txt, runtime/
# edit-help.txt) -- studio.py forwards it faithfully (2026-09-26) when a
# manifest sets one, which is real, working, generic plumbing this shared
# installation's OTHER model configurations may use. But a real benchmark
# render against THIS model (2026-09-26, content_items[20] slide 2's own
# prompt/references, see generated_assets/carousel_item20/benchmarks/
# local_engine_improvement_2026-09-26/) found FLUX.2 Klein 4B rejects it
# outright at runtime -- exit code 2, "--negative-prompt is not supported
# for FLUX.2. Focus on describing what you want." -- a restriction --help
# does not mention. DEFAULT_NEGATIVE_PROMPT is kept as reference text (never
# claimed to be validated for FLUX.2) for a possible future non-FLUX.2
# config; the safe, empirically-correct default for THIS provider is None.
DEFAULT_NEGATIVE_PROMPT = None
UNVALIDATED_NEGATIVE_PROMPT_TEXT = (
    "malformed hands, mutated hands, fused fingers, extra fingers, missing fingers, extra limbs, "
    "extra arms, duplicate person, second person, missing fox ears, human ears, cat ears, no ears, "
    "duplicate object, two copies of the same object, disfigured face, distorted anatomy, blurry, "
    "anime, cartoon, illustration, text, watermark, logo"
)


def _model_dir() -> Path:
    return STUDIO_ROOT / "models" / "flux2-klein-4b"


def _provenance_path() -> Path:
    return STUDIO_ROOT / "models" / "provenance.json"


def _studio_script() -> Path:
    return STUDIO_ROOT / "studio.py"


def _runtime_dir() -> Path:
    return STUDIO_ROOT / "runtime" / "venv" / "bin"


def _runtime_python() -> Path:
    return _runtime_dir() / "python"


def _chip() -> str:
    """Apple's own marketing string (e.g. 'Apple M1 Pro'). Read-only sysctl;
    falls back to the portable identifiers if it is unavailable."""
    try:
        out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return f"{platform.system()} {platform.machine()}"


def pixel_std(path: Path) -> float:
    """Standard deviation of pixel values across the whole image, as a
    STRUCTURAL sanity signal only -- see MIN_PIXEL_STD's own comment for
    exactly what this can and cannot tell you. Raises on an unreadable file
    -- callers that already verified the file opens should never see that."""
    import numpy as np
    from PIL import Image
    with Image.open(path) as im:
        arr = np.asarray(im.convert("RGB"), dtype=np.float32)
    return float(arr.std())


def is_degenerate_image(path: Path, *, min_std: float = MIN_PIXEL_STD) -> bool:
    """True if the image is suspiciously close to a single flat colour.
    NOT a vision or quality check -- a candidate with malformed hands,
    missing ears or a completely wrong identity has normal pixel variance
    and reads as False here exactly like a good one. This exists only to
    catch a real, distinct failure mode: a technically valid, correctly-
    sized, non-corrupt PNG that still isn't a real rendered photo."""
    return pixel_std(path) < min_std


def hardware() -> dict:
    ram_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    free_disk = shutil.disk_usage(PROJECT_ROOT).free
    return {
        "chip": _chip(),
        "arch": platform.machine(),
        "os": f"{platform.system()} {platform.release()}",
        "ram_gb": round(ram_bytes / 1024**3, 1),
        "free_disk_gb": round(free_disk / 1024**3, 1),
        "apple_silicon": platform.system() == "Darwin" and platform.machine() == "arm64",
    }


def ssd_mounted() -> bool:
    return STUDIO_ROOT.is_dir()


def missing_modules() -> list[str]:
    """Kept as a name for parity with the previous interface, repurposed:
    this provider never imports a Python package for generation (MFLUX runs
    in its OWN venv on the SSD), so this reports missing RUNTIME PIECES --
    the studio script, its venv interpreter, and its two generation binaries
    -- rather than importable modules in this process."""
    if not ssd_mounted():
        return [f"SSD not mounted at {STUDIO_ROOT}"]
    missing = []
    if not _runtime_python().is_file():
        missing.append(f"runtime python missing: {_runtime_python()}")
    if not _studio_script().is_file():
        missing.append(f"studio.py missing: {_studio_script()}")
    for name in REQUIRED_BINARIES:
        if not (_runtime_dir() / name).is_file():
            missing.append(f"runtime binary missing: {name}")
    return missing


def weights_present() -> bool:
    """Every file the model's OWN provenance manifest lists must actually be
    on disk at its declared size -- not just 'a safetensors file exists
    somewhere plus a static integrity flag', which can't tell a genuinely
    complete install from a partial download or a stale marker."""
    if not ssd_mounted():
        return False
    try:
        provenance = json.loads(_provenance_path().read_text())
    except (OSError, ValueError):
        return False
    files = provenance.get("files") or []
    if not files:
        return False
    model_dir = _model_dir()
    for entry in files:
        rel = entry.get("rfilename")
        if not rel:
            return False
        path = model_dir / rel
        if not path.is_file():
            return False
        expected_size = (entry.get("lfs") or {}).get("size", entry.get("size"))
        if expected_size is not None and path.stat().st_size != expected_size:
            return False
    return True


def preflight() -> dict:
    """Can this Mac run the model right now, at $0? Measures; never installs,
    downloads, or falls back to a paid provider."""
    hw = hardware()
    blockers, setup = [], []
    if not hw["apple_silicon"]:
        blockers.append(f"unsupported architecture {hw['arch']} -- MFLUX/MLX needs Apple Silicon")
    if hw["ram_gb"] < MIN_RAM_GB:
        blockers.append(f"RAM {hw['ram_gb']}GB is below the {MIN_RAM_GB}GB advisory minimum "
                        f"(measured peak MLX allocation for this model is ~4.35GB; the margin is "
                        f"for the OS and other apps, not the model itself)")
    if hw["free_disk_gb"] < MIN_FREE_DISK_GB:
        blockers.append(f"free disk {hw['free_disk_gb']}GB is below the {MIN_FREE_DISK_GB}GB needed "
                        f"for job manifests and copied outputs (model weights already live on the SSD)")
    if not ssd_mounted():
        blockers.append(f"Local Image Studio SSD not mounted at {STUDIO_ROOT}")
    missing = missing_modules() if ssd_mounted() else []
    setup.extend(missing)
    weights_ok = weights_present()
    if ssd_mounted() and not weights_ok:
        setup.append(f"model weights/provenance check failed under {_model_dir()} "
                    f"(see {_provenance_path()})")
    ready = not blockers and not setup
    return {
        "ready": ready,
        "status": "READY" if ready else ("HARDWARE_BLOCKED" if blockers else "SETUP_REQUIRED"),
        "model": MODEL_ID, "license": MODEL_LICENSE, "source": MODEL_SOURCE,
        "route": ROUTE, "cost_class": COST_CLASS, "cost_usd": 0, "cost_credits": 0,
        "hardware": hw, "missing_modules": missing,
        "weights_dir": str(_model_dir()), "weights_present": weights_ok,
        "hardware_blockers": blockers, "setup_required": setup,
        "tested_resolution": [PORTRAIT_WIDTH, PORTRAIT_HEIGHT],
    }


def _validate_project(project: str) -> Path:
    """Refuses a bad project slug or a resolved path that would escape
    STUDIO_ROOT BEFORE any mkdir/copy/write happens -- studio.py itself only
    validates the slug once it is invoked as a subprocess, which is too late
    for the reference copies and manifest write this adapter does first."""
    if not _PROJECT_SLUG_RE.fullmatch(project or ""):
        raise ValueError(f"invalid project slug {project!r} -- lowercase letters, digits, "
                         f"hyphens and underscores only, 1-64 chars (studio.py's own rule)")
    root = STUDIO_ROOT.resolve()
    project_dir = (STUDIO_ROOT / "projects" / project).resolve()
    if not project_dir.is_relative_to(root):
        raise ValueError(f"resolved project path {project_dir} escapes {root}")
    return project_dir


def _references(reference) -> list[Path]:
    """Normalizes the caller's `reference` argument (None, one Path, or a
    list of up to two Paths) to the list studio.py's manifest expects. Fails
    CLOSED on anything that isn't a real file -- there is no silent drop of a
    missing reference."""
    if reference is None:
        return []
    items = reference if isinstance(reference, (list, tuple)) else [reference]
    if len(items) > 2:
        raise ValueError(f"at most two references are supported on this installation, got {len(items)}")
    out = []
    for item in items:
        p = Path(item)
        if not p.is_file():
            raise FileNotFoundError(f"reference image not found: {p}")
        out.append(p)
    return out


def _content_addressed_name(path: Path) -> str:
    """A same-sized-but-different-content (or same-basename-different-item)
    reference can never silently collide: the destination name IS the
    content hash, so two different images always get two different files,
    and an unchanged source is never re-copied for no reason."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f"{digest}_{path.name}"


def _process_group_is_empty(pgid: int) -> bool:
    """True only once NO process remains in this group -- signal 0 delivers
    nothing but still raises ProcessLookupError the instant the group is
    truly empty. This is the ONLY check in this module that decides whether
    a job is actually finished; proc.wait() (which only reaps the ONE pid
    this code originally spawned, the group leader) is not a substitute for
    it -- a second review (2026-09-26) correctly flagged that the first
    version of this fix returned as soon as the LEADER exited, which can
    happen well before a slow or SIGTERM-resistant grandchild does."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False  # exists, just not signalable by us -- never assume it's gone
    return False


def _reap_leader(proc: subprocess.Popen) -> None:
    """Never blocks. Clears the leader's own zombie entry once it has
    exited, so it stops being counted as a group member by
    _process_group_is_empty; a no-op while it's still genuinely running."""
    try:
        proc.wait(timeout=0)
    except subprocess.TimeoutExpired:
        pass


def _ensure_process_group_gone(pgid: int, proc: subprocess.Popen) -> bool:
    """Blocks until the WHOLE group -- the leader (studio.py) AND whatever
    it spawned (the real mflux GPU process) -- is confirmed gone, not merely
    until the ONE pid this code originally started has exited. This is what
    makes 'the job is over' an honest claim rather than a guess: a second
    review (2026-09-26) correctly flagged that the first version of this
    fix (a) declared success the instant proc.wait() returned, which only
    proves the LEADER died, not its grandchild, and (b) re-derived the
    group id later via os.getpgid(proc.pid), which can itself raise
    ProcessLookupError once the leader is gone even while a live grandchild
    still shares that same pgid value -- reporting a false all-clear.

    pgid must be the value CAPTURED AT SPAWN TIME (== the leader's own pid
    under start_new_session=True, which is permanent for the life of the
    group), never re-derived afterwards.

    Escalates SIGTERM -> SIGKILL, polling for TRUE emptiness throughout each
    grace window (not just once at its end) -- see PROCESS_KILL_GRACE_S.
    Returns False (never an optimistic True) if the group could still not
    be confirmed empty even after SIGKILL -- honest, fail-closed cleanup
    reporting: a caller must never treat that as a safe, finished job."""
    _reap_leader(proc)
    if _process_group_is_empty(pgid):
        return True
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass  # some members already gone; the poll loop below checks the rest
        deadline = time.monotonic() + PROCESS_KILL_GRACE_S
        while time.monotonic() < deadline:
            _reap_leader(proc)
            if _process_group_is_empty(pgid):
                return True
            time.sleep(0.1)
    _reap_leader(proc)
    return _process_group_is_empty(pgid)


def _run_with_process_group(cmd: list[str], *, cwd: str, timeout: float) -> subprocess.CompletedProcess:
    """Runs `cmd` in its own process group so its ENTIRE process tree --
    itself plus whatever it spawns -- can be reaped and verified together,
    rather than only the immediate child.

    _ensure_process_group_gone runs UNCONDITIONALLY, whether communicate()
    returned normally, raised TimeoutExpired, or was interrupted by anything
    else (cancellation) -- never only in an exception path. A real studio.py
    (see its own 2026-09-26 fix) can never finish successfully while its own
    generation subprocess is still alive, so this never adds real delay on
    the ordinary path; it exists to catch the abnormal one, e.g. a wrapper
    that exits nonzero (or even zero) while something it spawned is still
    running for an unrelated reason -- never assumed away.

    If the group cannot be confirmed fully gone even after escalating to
    SIGKILL, this raises RuntimeError rather than silently returning as if
    cleanup succeeded -- honest fail-closed reporting, per review."""
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True)
    pgid = proc.pid   # captured HERE, at spawn -- see _ensure_process_group_gone's docstring
    stdout, pending = "", None
    try:
        stdout, _ = proc.communicate(timeout=timeout)
    except BaseException as e:
        pending = e
    if not _ensure_process_group_gone(pgid, proc):
        raise RuntimeError(
            f"process group {pgid} ({cmd[0]}) could not be confirmed fully terminated after "
            f"SIGTERM+SIGKILL -- refusing to report this job as finished or safe for a new one to "
            f"start")
    if pending is not None:
        raise pending
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, "")


def _run_studio(project: str, manifest_path: Path, *,
                timeout: float = GENERATION_TIMEOUT_S) -> subprocess.CompletedProcess:
    """The real runner: studio.py's own documented batch interface (README
    "Agent / batch use"), invoked with its own venv interpreter so nothing in
    the PS-05 process needs torch/diffusers/mlx installed."""
    return _run_with_process_group(
        [str(_runtime_python()), str(_studio_script()), "--project", project,
         "--manifest", str(manifest_path)],
        cwd=str(STUDIO_ROOT), timeout=timeout)


def _resolve_job_id(project: str, manifest_path: Path) -> tuple[str | None, str | None]:
    """studio.py's own --dry-run tells us the content-addressed job id (and
    therefore the output path) without generating anything. Returns
    (job_id, error) -- exactly one of which is not None."""
    try:
        dry = subprocess.run(
            [str(_runtime_python()), str(_studio_script()), "--project", project,
             "--manifest", str(manifest_path), "--dry-run"],
            cwd=str(STUDIO_ROOT), capture_output=True, text=True, timeout=DRY_RUN_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"could not resolve the job id: {e}"
    try:
        return json.loads(dry.stdout)["job"], None
    except (ValueError, KeyError):
        return None, f"could not resolve the job id: dry-run returned {dry.stdout!r} / {dry.stderr!r}"


def generate(prompt: str, out_path: Path, *, reference=None,
             width: int = PORTRAIT_WIDTH, height: int = PORTRAIT_HEIGHT,
             seed: int = 42, steps: int = DEFAULT_STEPS,
             negative_prompt: str | None = DEFAULT_NEGATIVE_PROMPT,
             project: str = DEFAULT_PROJECT, runner=_run_studio) -> dict:
    """Generate ONE image locally, at zero incremental cost, via the SSD's
    verified MFLUX installation. Deliberately does exactly one thing: no
    batching, no retry loop, no lifecycle change -- the caller (ps05_ops.
    generate_slide and the existing carousel_handoff) owns those.

    negative_prompt defaults to None and any other truthy value is REFUSED
    (status NEGATIVE_PROMPT_UNSUPPORTED) before any subprocess is launched --
    a real benchmark render (2026-09-26) found FLUX.2 Klein 4B rejects
    --negative-prompt outright at runtime ("--negative-prompt is not
    supported for FLUX.2"), despite it appearing in --help, so this is
    refused here rather than merely defaulted away, closing the gap where a
    caller could otherwise reach that failure by surprise. studio.py still
    forwards a manifest-level negative_prompt to --negative-prompt when
    non-empty regardless, since that plumbing is real and may suit a
    different model configuration later -- this provider (hardcoded to
    FLUX.2 Klein 4B, see MODEL_ID) just never sends one. steps defaults
    to DEFAULT_STEPS, a value chosen from a real (single-seed) benchmark
    render, not the original installation's verification.json; passing a
    different value is an explicit, caller-owned
    experiment, never this function's own silent choice.

    Dimensions are honest: whatever width/height are actually requested (and
    actually within studio.py's own supported range) are what gets reported
    back, and the produced file is opened and measured before any success is
    reported -- never a caller's aspirational size the runtime never
    rendered, and never a claim the file on disk doesn't back up."""
    pre = preflight()
    if not pre["ready"]:
        return {"ok": False, "status": pre["status"], "cost_usd": 0, "cost_credits": 0,
                "generated": False, "hardware_blockers": pre["hardware_blockers"],
                "setup_required": pre["setup_required"]}
    if any(type(x) is not int or x < MIN_DIM or x > MAX_DIM or x % 16 for x in (width, height)):
        return {"ok": False, "status": "INVALID_DIMENSIONS", "cost_usd": 0, "cost_credits": 0,
                "generated": False,
                "error": f"width/height must be integers, multiples of 16, between {MIN_DIM} and "
                         f"{MAX_DIM} (got {width}x{height}) -- this installation's tested default "
                         f"is {PORTRAIT_WIDTH}x{PORTRAIT_HEIGHT}; a larger size is unverified"}
    if type(steps) is not int or steps < MIN_STEPS or steps > MAX_STEPS:
        return {"ok": False, "status": "INVALID_STEPS", "cost_usd": 0, "cost_credits": 0,
                "generated": False,
                "error": f"steps must be an integer between {MIN_STEPS} and {MAX_STEPS} (got {steps}) "
                         f"-- studio.py's own validated range; this installation's verified default "
                         f"is {DEFAULT_STEPS}"}
    if negative_prompt is not None and not isinstance(negative_prompt, str):
        return {"ok": False, "status": "INVALID_NEGATIVE_PROMPT", "cost_usd": 0, "cost_credits": 0,
                "generated": False, "error": "negative_prompt must be a string or None"}
    # Reviewer-flagged (2026-09-26): a truthy value here used to reach
    # studio.py and the real mflux CLI, which then fails at exit code 2 --
    # "--negative-prompt is not supported for FLUX.2." Refused HERE instead,
    # before any subprocess is ever launched, so a caller can never reach
    # that failure by surprise; DEFAULT_NEGATIVE_PROMPT=None only prevented
    # the DEFAULT path from hitting it; this closes the same gap for an
    # explicit opt-in too (see UNVALIDATED_NEGATIVE_PROMPT_TEXT's docstring).
    if negative_prompt:
        return {"ok": False, "status": "NEGATIVE_PROMPT_UNSUPPORTED", "cost_usd": 0, "cost_credits": 0,
                "generated": False,
                "error": "FLUX.2 Klein 4B rejects --negative-prompt at runtime (confirmed by a real "
                         "benchmark render, 2026-09-26: exit code 2, \"--negative-prompt is not "
                         "supported for FLUX.2\") -- refused before launching the subprocess rather "
                         "than defaulted away, so this can never be reached by surprise"}
    # Validate the project slug and every resolved path BEFORE any adapter
    # mkdir/copy/write -- studio.py only validates once invoked as a
    # subprocess, which is too late for the side effects below.
    try:
        project_dir = _validate_project(project)
    except ValueError as e:
        return {"ok": False, "status": "INVALID_PROJECT", "cost_usd": 0, "cost_credits": 0,
                "generated": False, "error": str(e)}
    try:
        refs = _references(reference)
    except (FileNotFoundError, ValueError) as e:
        return {"ok": False, "status": "REFERENCE_ERROR", "cost_usd": 0, "cost_credits": 0,
                "generated": False, "error": str(e)}

    refs_dir = project_dir / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)
    ref_names = []
    for p in refs:
        name = _content_addressed_name(p)
        dest = refs_dir / name
        if not dest.is_file():
            shutil.copyfile(p, dest)
        ref_names.append(name)

    jobs_dir = project_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = jobs_dir / f"{out_path.stem}.json"
    # negative_prompt is never truthy past the NEGATIVE_PROMPT_UNSUPPORTED
    # guard above, so it is never written into the manifest here -- studio.py
    # still supports a manifest-level negative_prompt (for a possible future
    # non-FLUX.2 model config), this provider just never sends one.
    manifest_path.write_text(json.dumps(
        {"width": width, "height": height, "references": ref_names,
         "slides": [{"prompt": prompt, "seed": seed, "steps": steps}]}, indent=2))

    try:
        result = runner(project, manifest_path)
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": "GENERATION_TIMEOUT", "cost_usd": 0, "cost_credits": 0,
                "generated": False, "error": f"studio.py did not finish within {GENERATION_TIMEOUT_S}s"}
    except OSError as e:
        return {"ok": False, "status": "GENERATION_FAILED", "cost_usd": 0, "cost_credits": 0,
                "generated": False, "error": f"could not run studio.py: {e}"}
    if result.returncode != 0:
        return {"ok": False, "status": "GENERATION_FAILED", "cost_usd": 0, "cost_credits": 0,
                "generated": False,
                "error": (result.stderr or result.stdout or "non-zero exit").strip()[-2000:]}

    # studio.py writes to projects/<project>/outputs/<job-id>/01.png for a
    # single-slide manifest; the job id is content-addressed (prompt+seed+
    # reference BYTES), so re-running an unchanged job is naturally
    # idempotent and never produces a second image for the same request.
    job_id, err = _resolve_job_id(project, manifest_path)
    if job_id is None:
        return {"ok": False, "status": "GENERATION_UNVERIFIED", "cost_usd": 0, "cost_credits": 0,
                "generated": False, "error": err}
    produced = project_dir / "outputs" / job_id / "01.png"
    if not produced.is_file():
        return {"ok": False, "status": "GENERATION_UNVERIFIED", "cost_usd": 0, "cost_credits": 0,
                "generated": False, "error": f"expected output not found: {produced}"}

    # Never trust "a file appeared" -- open it, verify it decodes, and check
    # its actual dimensions match what was requested before copying it
    # anywhere or reporting success.
    from PIL import Image
    try:
        with Image.open(produced) as im:
            im.verify()
        with Image.open(produced) as im:
            actual_size = im.size
    except Exception as e:
        return {"ok": False, "status": "GENERATION_UNVERIFIED", "cost_usd": 0, "cost_credits": 0,
                "generated": False, "error": f"produced file is not a valid image: {e}"}
    if actual_size != (width, height):
        return {"ok": False, "status": "GENERATION_UNVERIFIED", "cost_usd": 0, "cost_credits": 0,
                "generated": False,
                "error": f"produced image is {actual_size[0]}x{actual_size[1]}, requested "
                         f"{width}x{height}"}
    # STRUCTURAL sanity only -- see MIN_PIXEL_STD's own comment. Catches a
    # genuinely blank/near-solid-colour output; says nothing about hands,
    # ears, identity or props, and never suppresses the real, separate need
    # for actual visual review of anything that passes it.
    measured_std = pixel_std(produced)
    if measured_std < MIN_PIXEL_STD:
        return {"ok": False, "status": "GENERATION_DEGENERATE", "cost_usd": 0, "cost_credits": 0,
                "generated": False,
                "error": f"produced image is suspiciously close to a flat colour (pixel std "
                         f"{measured_std:.1f}, floor is {MIN_PIXEL_STD}) -- refusing rather than "
                         f"reporting a technically-valid but almost certainly failed render as a "
                         f"success"}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(produced, out_path)
    return {"ok": True, "status": "GENERATED", "path": str(out_path),
            "width": width, "height": height, "model": MODEL_ID, "license": MODEL_LICENSE,
            "route": ROUTE, "cost_usd": 0, "cost_credits": 0, "generated": True,
            "reference_used": [str(p) for p in refs] if len(refs) != 1 else str(refs[0]),
            "steps": steps, "negative_prompt": negative_prompt or None,
            "source_output": str(produced)}


def is_wide_or_full_body_shot(record: dict) -> bool:
    """Detects, from the record's OWN composition/action text, a shot where
    her whole head (and likely more) is visible at a distance -- distinct
    from this story's many tight/close crops. Diagnosed 2026-09-26: slide 4
    (content_items[20]) is exactly this kind of shot and rendered with no
    fox ears at all, while GENERATION_SAFE_REFERENCE (a face-only closeup,
    verified by direct visual inspection to exclude the ear/top-of-head
    region) was the only identity reference ever supplied for it. Callers
    should prefer carousel_handoff.GENERATION_SAFE_REFERENCE_HEAD (which
    does show the ears) when this returns True."""
    shot = record.get("composition") or record.get("visual_action") or record.get("story_beat", "")
    action = record.get("visual_action") or record.get("beat", "")
    text = f"{shot} {action}"
    return bool(re.search(r"\bwide(r)?\b|\bwhole\b.{0,20}\broom\b|\bfull[- ]?body\b|\bdoorway\b", text, re.I))


def adapt_prompt_for_local(record: dict, state: dict, *, has_environment_reference: bool) -> dict:
    """FLUX.2 Klein 4B is a small, distilled model -- the full layered
    compiler prompt (story_continuity.compile_prompt: ~2000 words across nine
    authority sections) subordinates the actual shot instruction under a huge
    identity/environment block. Two real local candidates for content_items[20]
    slide 2 (2026-09-26, seeds 42/43) both drifted toward a face-forward
    portrait with that full prompt despite its composition text explicitly
    asking for hands/an object in the foreground; a compact, shot-first
    rewrite fixed it (see the saved audit sidecars next to those candidates).

    This is a COMPRESSION, not a lossless restatement -- say so honestly
    rather than claim otherwise:
      * KEPT in full: the record's own composition/action/beat text (the shot
        itself), every environment anchor (verified count on this story: 10 --
        none are dropped or arbitrarily truncated), wardrobe/hair/time-of-day,
        and a short adult-age / no-extra-people / not-anime safety line.
      * ADDED 2026-09-27 after a real production defect: the record's own
        forbidden_repetition list (previously dropped -- see the NOT-restated
        bullet below, which is about compile_prompt()'s separate, story-wide
        negative_constraints, not this per-slide field) is now restated here
        as an explicit negative instruction. Root cause of that defect: three
        independent content_items[24] renders (slides 1, 2, and 3's own
        attempt-2 retry) all converged on the same reclined, over-the-shoulder,
        phone-in-hand pose despite each slide's positive composition text
        asking for something else -- this small, distilled, 8-step model was
        never actually told what to avoid, only what to aim for, and positive
        instruction alone was not enough to overcome its apparent default bias
        toward that one pose.
      * COMPRESSED: the full identity block (eyes/hair/ears/tail/skin/beauty-
        mark prose) is replaced by "copy the attached identity reference photo"
        -- appropriate because an actual reference IMAGE is attached, so the
        model does not need the same facts restated as a wall of text too.
      * NOT restated here: the full compiler's complete negative_constraints
        list and precedence-conflict machinery -- those remain compile_prompt()
        text only. Nothing here overrides or contradicts them; this is simply
        a smaller model getting a smaller, differently-shaped prompt from the
        SAME underlying facts, never a fact this function invented itself.

    The hands/object-foreground reinforcement below is added ONLY when this
    SPECIFIC record's own composition/action text actually describes a hand-
    or palm-centred shot (checked against THIS record, never assumed for every
    slide) -- slide 1 (face-forward) and slide 4 (wide room shot) must never
    get it, and don't.

    compile_prompt()'s full text remains the authoritative, audited prompt for
    every other provider and for dry-run inspection; callers of this function
    should save it alongside the adapted prompt for any real candidate, never
    only the adapted one."""
    env = state.get("environment_lock") or {}
    wardrobe = state.get("wardrobe_lock") or {}
    anchors = [a.get("description", "") for a in (env.get("anchors") or []) if a.get("description")]
    shot = record.get("composition") or record.get("visual_action") or record.get("story_beat", "")
    action = record.get("visual_action") or record.get("beat", "")
    # Word-boundary match, not substring: a plain "hand" in b"handheld" (a
    # camera-technique word, not a shot about hands) is exactly the false
    # positive a naive substring check would produce.
    is_hand_shot = bool(re.search(r"\b(hands?|palm|holding)\b", f"{shot} {action}", re.I))
    is_wide_shot = is_wide_or_full_body_shot(record)

    lines = [
        f"MOST IMPORTANT INSTRUCTION -- THE SHOT: {shot}",
        f"What is happening: {action}",
    ]
    forbidden = [str(f).strip() for f in (record.get("forbidden_repetition") or []) if str(f).strip()]
    if forbidden:
        lines.append(
            "Do NOT repeat these earlier shots from this same sequence -- this photo must look "
            "visibly different in pose/framing/background from all of them, not a near-duplicate: "
            + "; ".join(forbidden) + ".")
    if is_hand_shot:
        lines.append(
            "Her hand(s) and whatever they are holding or touching are the main subject of this photo "
            "-- large, sharp, in the foreground. These are HER OWN hands ONLY -- no other person, no "
            "extra hands or arms, no second set of sleeves. Her face may be partially visible, "
            "soft-focus, further back in the frame; it is not the subject of this shot. Her tail is "
            "naturally outside this tight framing and does not need to appear. Whatever object she is "
            "handling appears EXACTLY ONCE in this photo, in her hands only -- never a second copy of "
            "it anywhere else in the frame (not lying on the bed, floor, shelf or pillow). If the shot "
            "above describes her two hands doing two DIFFERENT things (e.g. one hand on one object, the "
            "other hand holding something else), show BOTH exactly as described -- do not simplify by "
            "dropping one hand's object.")
    # If the shot names an object that is ALSO one of this story's own named
    # environment anchors (e.g. "trunk"), restate that anchor's own
    # description as an explicit clarification. A small model can otherwise
    # substitute a generic stand-in (e.g. a door/door handle instead of the
    # established storage trunk and its latch) for an object it only knows
    # by a bare noun.
    named_objects = []
    for a in (env.get("anchors") or []):
        anchor_id = str(a.get("id") or "").strip()
        if anchor_id and re.search(rf"\b{re.escape(anchor_id)}\b", f"{shot} {action}", re.I):
            named_objects.append((anchor_id, a.get("description", "")))
    if named_objects:
        clauses = "; ".join(f"the '{aid}' is specifically: {desc}" for aid, desc in named_objects if desc)
        if clauses:
            lines.append(f"Object clarification (do not substitute a generic stand-in): {clauses}.")
    lines.append(
        "Identity reference photo attached: copy HER FACE/IDENTITY from it only (same woman, same "
        "eyes, ears, features). Do not copy that photo's framing or pose -- this shot is different.")
    # Restating the canonical VISIBLE traits explicitly, not just "match the
    # photo": a real content_items[20] slide 4 render (2026-09-26) dropped the
    # fox ears entirely in a wide shot where the identity reference crop in
    # use (a face-only closeup) never showed them to begin with -- the image
    # was carrying the whole identity burden alone. This line is retained
    # regardless of which reference crop the caller selects, as a second,
    # independent (text) anchor for the traits an image reference might not
    # depict at every framing.
    lines.append(
        "Canonical visible identity traits to preserve, in addition to the reference photo: two "
        "subtle fox ears on top of her head (dark fur, pale inner fur) whenever the top of her head "
        "is in frame; exactly one bushy fox tail (dark brown, lighter tip) whenever a full-body "
        "framing would show it; amber/golden eyes; a small beauty mark on her anatomical LEFT cheek "
        "(viewer-right) whenever her face is in frame. "
        # 2026-09-30: canonical_ears_visible is a CRITICAL QA dimension ("exactly two ears
        # -- her fox ears, no human ears"), but this compression never told the model about
        # it, so content_items[31] slide 1 came back with a human ear AND an earring in
        # frame beside the fox ears -- an automatic critical failure the model was never
        # given a chance to avoid. The wardrobe's stud earrings stay satisfiable: they sit
        # under hair that covers the side of her head.
        "EXACTLY TWO EARS TOTAL -- only the fox ears on top; no human ear visible, her hair "
        "covers the sides of her head.")
    if has_environment_reference:
        lines.append(
            "Room reference photo attached: copy ONLY the room -- same furniture, décor and lighting "
            "-- never that photo's camera framing, pose or which part of her is in view.")
        if anchors:
            lines.append("Room details to keep recognisable when in frame: " + "; ".join(anchors) + ".")
    elif anchors:
        # 2026-09-30 defect fix: the anchors were previously emitted ONLY alongside a room
        # reference photo, so the FIRST slide of a story -- the one slide that has no
        # approved room image yet, and the slide that FREEZES the room for every later
        # slide -- was sent with no environment text at all and the model invented a room.
        # That is exactly how content_items[30] slide 1 (twice) and content_items[31]
        # slide 1 each came back in a plain daylit bedroom instead of the canonical night
        # attic loft. No room reference is precisely when these anchors matter most, and
        # this function's own docstring already promised none of them are dropped.
        lines.append(
            "THE ROOM (no photo of it exists yet -- draw exactly this, never invent another "
            "room): " + "; ".join(anchors) + ".")
    if wardrobe.get("outfit") or wardrobe.get("hair"):
        lines.append(f"Wardrobe/continuity: {wardrobe.get('outfit', '')}; {wardrobe.get('hair', '')}; "
                     f"{wardrobe.get('time_of_day', '')}.")
    lines.append(
        "Adult woman (25+, mid-20s appearance) -- never a minor, never teen- or child-coded. No "
        "extra/duplicate people, hands or limbs beyond what is described above. Photorealistic "
        "smartphone snapshot, not a posed studio portrait, not anime/cartoon/illustration. No text, "
        "no logo, no watermark, no comic panel.")
    prompt = "\n".join(line for line in lines if line.strip())
    return {"prompt": prompt, "is_hand_shot": is_hand_shot, "is_wide_shot": is_wide_shot,
            "environment_anchors_used": len(anchors),
            "compression_note": "shot-first compression for a small model; NOT a lossless restatement "
                                "of compile_prompt() -- see this function's docstring for exactly what "
                                "is kept vs compressed vs left to the full compiled prompt only"}
