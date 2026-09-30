"""Bounded, idempotent Nyx Instagram + TikTok flow over the metricool-nyx MCP connector.

Scope is GATED on `allowed` (ps05_tick.NYX_UNATTENDED_ALLOWED_TOOLS = exactly
getBrandSettings, getScheduledPosts, createScheduledPost). Only the metricool-nyx
server is ever loaded (never legacy `metricool` / `metricool-ashkan`), and each
invocation pre-approves ONLY the single tool it is calling. Founder-approved
2026-09-20: no per-post HUMAN approval; the automatic checks below stay mandatory.

  * reconcile: SCHEDULED Metricool items whose slot has passed -> PUBLISHED only
    when Metricool itself reports PUBLISHED with a public URL.
  * schedule: at most one QA-passed item per platform per tick, cadence-limited,
    duplicate-checked, brand/handle identity read immediately before the create,
    attempt record persisted BEFORE the create, SCHEDULED only after an
    independent listing confirms the post; uncertain outcomes are reconciled
    before any retry.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NYX_BRAND_ID = 6988018
NYX_INSTAGRAM = "nyx.emberfall"
NYX_TIKTOK = "nyx.emberfall1"
BRAND_TZ = "Australia/Melbourne"
SERVER = "metricool-nyx"
MCP_URL = "https://ai.metricool.com/mcp"

TOOL_BRAND = f"mcp__{SERVER}__getBrandSettings"
TOOL_LIST = f"mcp__{SERVER}__getScheduledPosts"
TOOL_CREATE = f"mcp__{SERVER}__createScheduledPost"
READ_TIER = (TOOL_BRAND, TOOL_LIST)
WRITE_TIER = READ_TIER + (TOOL_CREATE,)

PLATFORMS = ("instagram", "tiktok")
HANDLES = {"instagram": NYX_INSTAGRAM, "tiktok": NYX_TIKTOK}
HANDLE_FIELD = {"instagram": "instagramData", "tiktok": "tiktokData"}

RECONCILE_GRACE = timedelta(minutes=10)
ATTEMPT_SETTLE = timedelta(minutes=30)            # wait this long before calling an attempt "not created"
MIN_GAP_BETWEEN_POSTS = timedelta(hours=72)       # per platform: ~3-4 day stagger, no daily grind
MIN_GAP_BETWEEN_IG_POSTS = MIN_GAP_BETWEEN_POSTS  # kept name (tests/back-compat)
PREFERRED_LOCAL_HOUR = 18                         # Metricool best-time data: 10:00 and 18:00 lead
STORY_CAROUSEL = "story_carousel"                   # same tag as stages.STORY_CAROUSEL
OPEN_ATTEMPT = ("CREATE_STARTED", "CREATE_UNCERTAIN")
MAX_CREATE_FAILURES = 3                           # then stop hammering Metricool and surface the cause

# A HOLD stops any create for one item until a human releases it (deletes the
# entry). Used for creates that were permission-denied / need a legitimate
# approval: the flow keeps reconciling read-only but will NOT retry the create,
# not in this tick and not in any later tick.
HOLDS_PATH = Path(__file__).resolve().parent / "state" / "publish_holds.json"


def load_holds() -> dict:
    try:
        d = json.loads(Path(HOLDS_PATH).read_text())
    except (OSError, ValueError):
        return {}
    holds = d.get("holds") if isinstance(d, dict) else None
    return holds if isinstance(holds, dict) else {}


def hold_for(platform: str, idx: int) -> dict | None:
    return load_holds().get(f"{platform}:{idx}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


_SCHED_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s*(\S+)?$")


def parse_scheduled_for(text, default_tz: str = BRAND_TZ) -> datetime | None:
    """Every format this project has stored: aware ISO ('2026-09-22T18:00:00+10:00
    Australia/Melbourne'), naive local + zone name ('2026-09-22T18:00:00
    Australia/Melbourne'), and space-separated variants. Always returns an
    AWARE datetime (naive local times use the named zone, else the brand zone)."""
    m = _SCHED_RE.match(str(text or "").strip())
    if not m:
        return None
    date, clock, zone = m.groups()
    clock = clock.replace("Z", "+00:00")
    clock = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", clock)
    try:
        dt = datetime.fromisoformat(f"{date}T{clock}")
    except ValueError:
        return None
    if dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(zone or default_tz))
        except (ZoneInfoNotFoundError, ValueError):
            return None
    return dt


def missing_tools(allowed, needed) -> list[str]:
    return [t for t in needed if t not in (allowed or ())]


# --------------------------------------------------------------------------
# Verified tool executor: the model is only the MCP client. Python decides the
# call, pre-approves ONLY that tool, checks the call that actually ran, and
# reads the tool RESULT event (never the model's prose).
# --------------------------------------------------------------------------
def call_tool(tool: str, args: dict, *, allowed, run=subprocess.run, timeout: int = 240) -> dict:
    if tool not in (allowed or ()):
        return {"ok": False, "status": "GATED", "detail": f"{tool} not in approved scope"}
    cfg = json.dumps({"mcpServers": {SERVER: {"type": "http", "url": MCP_URL}}})
    cmd = ["claude", "-p", "--model", "sonnet", "--output-format", "stream-json", "--verbose",
           "--no-session-persistence", "--strict-mcp-config", "--mcp-config", cfg,
           "--allowedTools", tool]
    prompt = (f"Call the tool {tool} exactly once with EXACTLY these JSON arguments and nothing "
              f"else: {json.dumps(args)}. If the tool is not directly available, first load it "
              f"with ToolSearch (query: select:{tool}). Do not call any other Metricool tool. "
              f"Reply with the single word DONE.")
    try:
        proc = run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "status": "EXEC_FAILED", "detail": str(exc)[:200]}
    return parse_call_stream(proc.stdout, tool, args)


def parse_call_stream(stdout: str, tool: str, args: dict) -> dict:
    uses, results = {}, []
    for line in (stdout or "").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        msg = ev.get("message") if isinstance(ev, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == tool:
                uses[b.get("id")] = b.get("input") or {}
            elif b.get("type") == "tool_result":
                results.append(b)
    for b in results:
        if b.get("tool_use_id") not in uses:
            continue
        if uses[b["tool_use_id"]] != args:
            return {"ok": False, "status": "ARGS_MISMATCH",
                    "detail": "the call that ran did not match the intended arguments"}
        raw = b.get("content")
        text = raw if isinstance(raw, str) else " ".join(
            c.get("text", "") for c in (raw or []) if isinstance(c, dict))
        if b.get("is_error"):
            low = text.lower()
            status = "PERMISSION_DENIED" if ("permission" in low or "not granted" in low) else "TOOL_ERROR"
            return {"ok": False, "status": status, "detail": text[:200]}
        try:
            return {"ok": True, "status": "OK", "data": json.loads(text)}
        except ValueError:
            return {"ok": False, "status": "UNPARSEABLE_RESULT", "detail": text[:200]}
    # A call that ran but returned nothing we could read is UNCERTAIN, not failed.
    return {"ok": False, "status": "TOOL_NOT_CALLED" if not uses else "NO_RESULT",
            "detail": "no matching tool result observed"}


def verify_identity(caller, allowed, platforms=("instagram",)) -> dict:
    """Exactly the Nyx brand (only that brand) with the Nyx handle for EVERY
    platform about to be touched. Returns {ok, tz} or a stop reason."""
    ident = caller(TOOL_BRAND, {}, allowed=allowed)
    if not ident.get("ok"):
        return {"ok": False, "status": ident["status"], "detail": ident.get("detail")}
    brands = (ident["data"] or {}).get("data") or []
    nets = [(b.get("networksData") or {}) for b in brands]
    need = {"instagram", *platforms}                    # brand identity is always the Nyx Instagram
    bad = [p for p in need if [n.get(HANDLE_FIELD[p]) for n in nets] != [HANDLES[p]]]
    if [b.get("id") for b in brands] != [NYX_BRAND_ID] or bad:
        return {"ok": False, "status": "IDENTITY_MISMATCH",
                "detail": f"expected only brand {NYX_BRAND_ID} with " +
                          ", ".join(f"{p}={HANDLES[p]}" for p in sorted(need)) +
                          f"; got {[b.get('id') for b in brands]}/{nets}"}
    return {"ok": True, "tz": brands[0].get("timezone") or BRAND_TZ}


def _list_posts(caller, allowed, lo: datetime, hi: datetime, tz: str) -> dict:
    return caller(TOOL_LIST, {"brandId": str(NYX_BRAND_ID), "fromDate": lo.isoformat(timespec="seconds"),
                              "toDate": hi.isoformat(timespec="seconds"), "timezone": tz}, allowed=allowed)


def _platform_of(it: dict) -> str | None:
    p = it.get("target_platform")
    return p if p in PLATFORMS else None


# --------------------------------------------------------------------------
# Reconcile
# --------------------------------------------------------------------------
def due_scheduled_items(v: dict, now: datetime | None = None) -> list[tuple[int, dict]]:
    now = now or _now()
    out = []
    for i, it in enumerate(v.get("content_plan", {}).get("content_items", [])):
        cfg = it.get("publish_config") or {}
        if (it.get("lifecycle_status") != "SCHEDULED" or cfg.get("route") != "metricool"
                or _platform_of(it) is None):
            continue
        when = parse_scheduled_for(cfg.get("scheduled_for"))
        if when and now >= when.astimezone(timezone.utc) + RECONCILE_GRACE:
            out.append((i, it))
    return out


def apply_reconcile(v: dict, posts: list[dict], now: datetime | None = None) -> list[dict]:
    """Pure + idempotent. Mutates only items Metricool itself confirms as
    PUBLISHED with a public URL."""
    by_id = {str(p.get("id")): p for p in posts if isinstance(p, dict)}
    events = []
    for idx, it in due_scheduled_items(v, now):
        platform = _platform_of(it)
        post = by_id.get(str(it["publish_config"].get("metricool_post_id")))
        if post is None:
            events.append({"index": idx, "outcome": "NOT_FOUND",
                           "detail": "post id absent from Metricool listing -- NOT marked published"})
            continue
        prov = next((p for p in post.get("providers", []) if p.get("network") == platform), {})
        status, url = prov.get("status"), prov.get("publicUrl")
        if status == "PUBLISHED" and url:
            it["lifecycle_status"] = "PUBLISHED"
            it["public_status"] = "PUBLICLY_LIVE"
            it["live_platforms"] = sorted(set(it.get("live_platforms", [])) | {platform})
            it[f"{platform}_handle"] = HANDLES[platform]
            it["platform_post_id"] = url
            it["lifecycle_updated_at"] = _now().isoformat(timespec="seconds")
            events.append({"index": idx, "outcome": "PUBLISHED", "detail": url})
        elif status == "PUBLISHED":
            events.append({"index": idx, "outcome": "PUBLISHED_NO_URL",
                           "detail": "Metricool says PUBLISHED but returned no public URL -- NOT verified live"})
        elif status == "PENDING":
            events.append({"index": idx, "outcome": "STILL_PENDING",
                           "detail": "past its slot but Metricool still reports PENDING"})
        else:
            events.append({"index": idx, "outcome": "FAILED_OR_UNKNOWN",
                           "detail": f"Metricool status {status!r} / {prov.get('detailedStatus')!r}"})
    return events


def run_reconcile(v: dict, *, allowed, caller=call_tool, now: datetime | None = None) -> dict:
    now = now or _now()
    due = due_scheduled_items(v, now)
    if not due:
        return {"status": "NOTHING_DUE", "events": []}
    gap = missing_tools(allowed, READ_TIER)
    if gap:
        return {"status": "GATED", "detail": f"needs approval for: {', '.join(gap)}",
                "waiting_items": [i for i, _ in due], "events": []}
    ident = verify_identity(caller, allowed, {_platform_of(i) for _, i in due})
    if not ident["ok"]:
        return {"status": ident["status"], "detail": ident.get("detail"), "events": []}
    starts = [parse_scheduled_for(i["publish_config"].get("scheduled_for")) for _, i in due]
    listed = _list_posts(caller, allowed, min(starts) - timedelta(days=1), max(starts) + timedelta(days=1),
                         ident["tz"])
    if not listed.get("ok"):
        return {"status": listed["status"], "detail": listed.get("detail"), "events": []}
    return {"status": "RECONCILED", "events": apply_reconcile(v, (listed["data"] or {}).get("data") or [], now)}


# --------------------------------------------------------------------------
# Scheduling
# --------------------------------------------------------------------------
def _posts_on(v: dict, platform: str):
    for i, it in enumerate(v.get("content_plan", {}).get("content_items", [])):
        if it.get("target_platform") == platform:
            yield i, it


def _ig_posts(v: dict):                      # back-compat name
    return _posts_on(v, "instagram")


def plan_next_schedule(v: dict, now: datetime | None = None, platform: str = "instagram") -> dict:
    """Pure. Never plans an item that is already scheduled/published, and never
    plans anything while an earlier create attempt on this platform is unresolved."""
    now = now or _now()
    for idx, it in _posts_on(v, platform):
        att = it.get("publish_attempt") or {}
        if att.get("status") in OPEN_ATTEMPT and it.get("lifecycle_status") not in ("SCHEDULED", "PUBLISHED"):
            return {"status": "RECONCILE_ATTEMPT", "index": idx, "platform": platform}
    for idx, it in _posts_on(v, platform):
        if (it.get("lifecycle_status") in ("QA_PASSED", "PUBLISH_READY")
                and not hold_for(platform, idx)
                and int(it.get("create_failures", 0)) >= MAX_CREATE_FAILURES):
            return {"status": "BLOCKED", "index": idx, "platform": platform,
                    "detail": f"{it['create_failures']} consecutive create failures -- stopped retrying; last: "
                              f"{(it.get('publish_attempt') or {}).get('detail')}"}
    latest = None
    for _, it in _posts_on(v, platform):
        if it.get("lifecycle_status") in ("SCHEDULED", "PUBLISHED"):
            cfg = it.get("publish_config") or {}
            t = parse_scheduled_for(cfg.get("scheduled_for"))
            if t is None and it.get("lifecycle_updated_at"):
                try:
                    t = datetime.fromisoformat(str(it["lifecycle_updated_at"]).replace("Z", "+00:00"))
                except ValueError:
                    t = None
            if t is not None and t.tzinfo:
                t = t.astimezone(timezone.utc)
                latest = t if latest is None or t > latest else latest
    for it in v.get("content_plan", {}).get("content_items", []):
        for xp in it.get("crossposts") or []:          # reuse of another item's asset on this platform
            t = parse_scheduled_for(xp.get("scheduled_for")) if xp.get("platform") == platform else None
            if t is not None and t.tzinfo:
                t = t.astimezone(timezone.utc)
                latest = t if latest is None or t > latest else latest
    earliest = max(now + timedelta(minutes=15), (latest + MIN_GAP_BETWEEN_POSTS) if latest else now)
    for idx, it in _posts_on(v, platform):
        if it.get("lifecycle_status") not in ("QA_PASSED", "PUBLISH_READY"):
            continue
        if hold_for(platform, idx):
            continue                              # held: never planned; run_schedule reports it
        if not it.get("caption"):
            return {"status": "BLOCKED", "index": idx, "platform": platform, "detail": "no approved caption"}
        carousel = it.get("format_variant") == STORY_CAROUSEL
        if carousel and platform == "instagram":
            # native multi-image carousel: every slide must already be public, in slide order
            urls = it.get("hosted_media_urls") or it.get("asset_refs") or []
            media = list(urls) if urls and all(str(u).startswith("https://") for u in urls) \
                and len(urls) == len(it.get("slides") or urls) else None
        else:
            # video, or a carousel adapted for TikTok as a slideshow VIDEO (TikTok photo posts
            # cannot carry the AI-generated label through Metricool) -- never photo mode
            media = it.get("hosted_media_url")
            if not media and not carousel and str(it.get("asset_ref") or "").startswith("https://"):
                media = it["asset_ref"]          # already-recorded, QA-approved public URL: read, never written
        if not media:
            return {"status": "NEEDS_HOSTED_MEDIA", "index": idx, "platform": platform,
                    "detail": "no public media URL recorded (hosting needs its own approval tier)"}
        return {"status": "READY", "index": idx, "platform": platform,
                "earliest": earliest.isoformat(timespec="seconds"),
                "caption": it["caption"], "media": media,
                "format": "carousel" if isinstance(media, list) else "video",
                "content_objective": it.get("content_objective")}
    return {"status": "NO_ELIGIBLE_ITEM", "platform": platform}


def choose_slot(earliest_iso: str, tz: str = BRAND_TZ) -> str:
    """Next PREFERRED_LOCAL_HOUR:00 in the brand zone at/after the cadence floor
    (DST-correct offset comes from the zone, not from a hard-coded +10:00)."""
    local = datetime.fromisoformat(earliest_iso).astimezone(ZoneInfo(tz))
    slot = local.replace(hour=PREFERRED_LOCAL_HOUR, minute=0, second=0, microsecond=0)
    if slot < local:
        slot += timedelta(days=1)
    return slot.isoformat(timespec="seconds")


def tiktok_settings(caption: str, content_objective: str | None = None) -> dict:
    """Public, all interactions on, AI-generated label on, no added music. A
    post that pitches Nyx's own Fanvue (CTA / CONVERSION) is disclosed as the
    creator's OWN brand -- accurate, not a third-party paid partnership."""
    own_brand = content_objective == "CONVERSION" or bool(
        re.search(r"link'?s? in (my )?bio|link in bio", caption or "", re.I))
    # Metricool rejects a TikTok post without a title ("TikTok title is required",
    # observed 2026-09-21 -- the cause of the 2026-09-20 TOOL_ERROR); the approved caption is it.
    return {"title": caption, "disableComment": False, "disableDuet": False, "disableStitch": False,
            "privacyOption": "PUBLIC_TO_EVERYONE", "commercialContentThirdParty": False,
            "commercialContentOwnBrand": own_brand, "autoAddMusic": False, "isAigc": True}


def verify_created(posts: list[dict], post_id, caption: str, when_iso: str, tz: str,
                   expect_media: list | None = None, platform: str = "instagram",
                   expect_tiktok: dict | None = None, media_count: int = 1) -> str | None:
    """None only when an independent listing confirms EVERY intended property:
    the post exists on the intended network with a PENDING/PUBLISHED provider,
    autoPublish on, not a draft, the platform's AI disclosure, the approved
    caption, exactly one media file under the Nyx brand id (and, when the create
    response returned its media, the same durable media), at the intended time."""
    post = next((p for p in posts if isinstance(p, dict) and str(p.get("id")) == str(post_id)), None)
    if post is None:
        return "post id not present in Metricool listing"
    provs = post.get("providers", [])
    prov = next((p for p in provs if p.get("network") == platform), None)
    if prov is None:
        return f"listing post has no {platform} provider"
    if len(provs) != 1:
        return f"listing post targets {[p.get('network') for p in provs]}, expected only {platform}"
    if prov.get("status") not in ("PENDING", "PUBLISHED"):
        return f"{platform} provider status {prov.get('status')!r} is not PENDING/PUBLISHED"
    if post.get("draft"):
        return "listing post is a draft"
    if post.get("autoPublish") is not True:
        return "listing post is not autoPublish"
    if platform == "instagram":
        ig = post.get("instagramData") or {}
        want_type = "POST" if media_count > 1 else "REEL"
        if ig.get("type") != want_type:
            return f"instagram type {ig.get('type')!r} is not {want_type}"
        if ig.get("isAiGenerated") is not True:
            return "AI-generated disclosure is not set"
    else:
        # Fail closed: privacy / interactions / commercial disclosure / AI label are
        # confirmed ONLY from what Metricool's own listing echoes back. Never inferred
        # from the create request, and an absent field is UNVERIFIED, not "fine".
        if not expect_tiktok:
            return "no intended TikTok settings supplied to verify against"
        tt = post.get("tiktokData")
        if not isinstance(tt, dict):
            return "tiktokData absent from Metricool listing -- privacy/interaction/AI-label UNVERIFIED"
        for k, want in expect_tiktok.items():
            if k not in tt and want is False:
                continue                     # Metricool's listing omits false flags (observed 2026-09-21)
            if k not in tt:
                return f"tiktokData.{k} absent from Metricool listing -- UNVERIFIED"
            if tt[k] != want:
                return f"tiktok setting {k}={tt[k]!r} differs from intended {want!r}"
        if tt.get("isAigc") is not True:
            return "AI-generated disclosure is not set"
    if (post.get("text") or "").strip() != caption.strip():
        return "listing caption differs from the approved caption"
    media = post.get("media") or []
    if len(media) != media_count:
        return f"expected exactly {media_count} media file(s), listing has {len(media)}"
    if any(f"/{NYX_BRAND_ID}-" not in str(m) for m in media):
        return "media is not stored under the Nyx brand id"
    if expect_media is not None and list(media) != list(expect_media):
        return "listing media differs from the media the create call returned"
    pd = post.get("publicationDate") or {}
    if str(pd.get("dateTime", ""))[:16] != when_iso[:16] or pd.get("timezone") != tz:
        return f"listing schedule {pd} differs from intended {when_iso} {tz}"
    return None


def _media_count(it: dict, platform: str) -> int:
    if it.get("format_variant") == STORY_CAROUSEL and platform == "instagram":
        return len(it.get("hosted_media_urls") or it.get("asset_refs") or it.get("slides") or [1])
    return 1


def _attempt_key(idx: int, caption: str, when_iso: str) -> str:
    return hashlib.sha1(f"{idx}|{caption}|{when_iso}".encode()).hexdigest()[:12]


def _adopt(v: dict, idx: int, post: dict, when_iso: str, tz: str, platform: str = "instagram") -> None:
    it = v["content_plan"]["content_items"][idx]
    it["lifecycle_status"] = "SCHEDULED"
    it["lifecycle_updated_at"] = _now().isoformat(timespec="seconds")
    cfg = {"route": "metricool", "blogId": str(NYX_BRAND_ID),
           "metricool_post_id": post.get("id"), "metricool_uuid": post.get("uuid"),
           "autoPublish": True, "scheduled_for": f"{when_iso} {tz}"}
    if platform == "instagram":
        cfg.update({"type": "POST" if _media_count(it, platform) > 1 else "REEL", "isAiGenerated": True})
    else:
        cfg.update({"type": "VIDEO", "tiktok_settings": tiktok_settings(
            it.get("caption", ""), it.get("content_objective"))})
    it["publish_config"] = cfg
    it.setdefault("publish_attempt", {})["status"] = "SCHEDULED"


def reconcile_attempt(v: dict, idx: int, *, allowed, caller=call_tool, now: datetime | None = None) -> dict:
    """Resolve an unresolved create attempt from Metricool's own listing BEFORE
    any retry: adopt the post if it exists, else release the item only after
    the settle window so an eventually-consistent listing is not mistaken for
    'not created'."""
    now = now or _now()
    it = v["content_plan"]["content_items"][idx]
    platform = _platform_of(it) or "instagram"
    att = it["publish_attempt"]
    ident = verify_identity(caller, allowed, (platform,))
    if not ident["ok"]:
        return {"status": ident["status"], "detail": ident.get("detail")}
    when = datetime.fromisoformat(att["when_iso"])
    listed = _list_posts(caller, allowed, when - timedelta(days=2), when + timedelta(days=2), ident["tz"])
    if not listed.get("ok"):
        return {"status": listed["status"], "detail": listed.get("detail")}
    expect_tt = tiktok_settings(it["caption"], it.get("content_objective")) if platform == "tiktok" else None
    for p in (listed["data"] or {}).get("data") or []:
        if verify_created([p], p.get("id"), it["caption"], att["when_iso"], ident["tz"],
                          platform=platform, expect_tiktok=expect_tt,
                          media_count=_media_count(it, platform)) is None:
            _adopt(v, idx, p, att["when_iso"], ident["tz"], platform)
            return {"status": "ATTEMPT_ADOPTED", "metricool_post_id": p.get("id")}
    started = datetime.fromisoformat(att["started_at"])
    if now - started >= ATTEMPT_SETTLE:
        att["status"] = "NOT_CREATED_CONFIRMED"
        return {"status": "ATTEMPT_RELEASED", "detail": "no matching Metricool post after the settle window"}
    return {"status": "ATTEMPT_UNRESOLVED", "detail": "no matching post yet; inside the settle window"}


# Instagram surfaces PS-05 can actually create (verified 2026-09-21): REEL
# (single video) and POST (native multi-image FEED carousel -- what a
# format_variant "story_carousel" item publishes as; the name describes the
# serial-story BRIEF format, not Instagram Stories). Instagram STORIES are NOT
# supported: nothing here builds or verifies a Story, and whether
# createScheduledPost accepts one -- and whether getScheduledPosts echoes the
# AI-generated disclosure for it, which verify_created requires before anything
# is recorded SCHEDULED -- is unverified against the live connector. Adding
# Stories needs that verification first, plus a reconcile path for a post that
# expires in 24h and may never expose a public permalink.
def _build_create_args(plan: dict, when_iso: str, tz: str) -> dict:
    platform = plan.get("platform", "instagram")
    media = plan["media"] if isinstance(plan["media"], list) else [plan["media"]]
    info = {"autoPublish": True, "draft": False, "media": media,
            "providers": [{"network": platform}],
            "publicationDate": {"dateTime": when_iso[:19], "timezone": tz},
            "text": plan["caption"]}
    if platform == "instagram":
        info["instagramData"] = ({"type": "POST", "isAiGenerated": True} if plan.get("format") == "carousel"
                                 else {"type": "REEL", "isAiGenerated": True, "showReelOnFeed": True})
    else:
        info["tiktokData"] = tiktok_settings(plan["caption"], plan.get("content_objective"))
    return {"blogId": str(NYX_BRAND_ID), "date": when_iso, "info": json.dumps(info)}


def schedule_item(v: dict, plan: dict, when_iso: str, tz: str, *, allowed, caller=call_tool,
                  persist=None, now: datetime | None = None) -> dict:
    now = now or _now()
    if persist is None:
        return {"status": "PERSIST_NOT_WIRED",
                "detail": "refusing to create: no way to persist the attempt record before the call"}
    if plan.get("status") != "READY":
        return {"status": "NOT_READY", "detail": plan.get("status")}
    held = hold_for(plan.get("platform", "instagram"), plan.get("index"))
    if held:                                      # defense in depth: even a forced plan cannot create
        return {"status": "HELD", "detail": str(held.get("reason"))[:200]}
    gap = missing_tools(allowed, WRITE_TIER)
    if gap:
        return {"status": "GATED", "detail": f"needs approval for: {', '.join(gap)}"}
    platform = plan.get("platform", "instagram")
    when = datetime.fromisoformat(when_iso)
    if when.tzinfo is None or when < datetime.fromisoformat(plan["earliest"]):
        return {"status": "TOO_SOON_OR_NAIVE", "detail": f"earliest allowed {plan['earliest']}"}
    caption, idx = plan["caption"], plan["index"]

    # 1. duplicate check against Metricool's own listing (any network: same caption = same post)
    listed = _list_posts(caller, allowed, when - timedelta(days=2), when + timedelta(days=2), tz)
    if not listed.get("ok"):
        return {"status": listed["status"], "detail": listed.get("detail")}
    if any((p.get("text") or "").strip() == caption.strip()
           and (not p.get("providers") or any(pr.get("network") == platform for pr in p["providers"]))
           for p in (listed["data"] or {}).get("data") or []):
        return {"status": "DUPLICATE_ALREADY_IN_METRICOOL"}

    # 2. identity (brand + this platform's handle) read IMMEDIATELY before the create
    ident = verify_identity(caller, allowed, (platform,))
    if not ident["ok"]:
        return {"status": ident["status"], "detail": ident.get("detail")}
    if ident["tz"] != tz:
        return {"status": "TZ_MISMATCH", "detail": f"brand tz {ident['tz']} != {tz}"}

    # 3. persist the attempt BEFORE the create
    it = v["content_plan"]["content_items"][idx]
    it["publish_attempt"] = {"key": _attempt_key(idx, caption, when_iso), "status": "CREATE_STARTED",
                             "started_at": now.isoformat(timespec="seconds"), "when_iso": when_iso}
    persist()

    made = caller(TOOL_CREATE, _build_create_args(plan, when_iso, tz), allowed=allowed)
    post_id = ((made.get("data") or {}).get("data") or {}).get("id") if made.get("ok") else None
    if not post_id:
        # Unknown outcome (timeout / empty response / mismatched call): never SCHEDULED,
        # never blindly retried -- reconcile_attempt() resolves it from Metricool's listing.
        it["publish_attempt"]["status"] = "CREATE_UNCERTAIN"
        # keep Metricool's actual message, not just the label, so the cause is diagnosable
        why = f"{made.get('status') or 'no id in create response'}: {made.get('detail') or ''}".strip(": ")
        it["publish_attempt"]["detail"] = why[:240]
        it["create_failures"] = int(it.get("create_failures", 0)) + 1
        persist()
        return {"status": "CREATE_UNCERTAIN", "detail": it["publish_attempt"]["detail"]}

    # 4. independent listing must confirm before SCHEDULED is recorded
    check = _list_posts(caller, allowed, when - timedelta(days=2), when + timedelta(days=2), tz)
    posts = (check["data"] or {}).get("data") or [] if check.get("ok") else []
    returned_media = ((made.get("data") or {}).get("data") or {}).get("media")
    expect_tt = tiktok_settings(caption, plan.get("content_objective")) if platform == "tiktok" else None
    reason = ("create response returned no media" if not returned_media
              else verify_created(posts, post_id, caption, when_iso, tz, expect_media=returned_media,
                                  platform=platform, expect_tiktok=expect_tt,
                                  media_count=len(plan["media"]) if isinstance(plan["media"], list) else 1))
    if reason:
        it["publish_attempt"]["status"] = "CREATE_UNCERTAIN"
        it["publish_attempt"]["detail"] = reason[:120]
        it["publish_attempt"]["metricool_post_id"] = post_id
        persist()
        return {"status": "CREATE_UNVERIFIED", "detail": reason}
    _adopt(v, idx, next(p for p in posts if str(p.get("id")) == str(post_id)), when_iso, tz, platform)
    it["create_failures"] = 0
    persist()
    return {"status": "SCHEDULED", "metricool_post_id": post_id}


# TikTok routes (founder decision 2026-09-21, verified against Metricool's TikTok
# help docs + TikTok's Content Sharing Guidelines). "metricool" (DEFAULT) is
# Metricool's own authorized TikTok auto-publish integration -- Metricool is the
# TikTok API client, the settings are passed explicitly (tiktok_settings), and
# there is no separate per-post form, so PS-05 adds no redundant consent step.
# "direct" (FALLBACK) is the Higgsfield/TikTok Direct Post widget: the founder's
# genuine per-post consent in that widget stays mandatory, and this flow never
# creates for a direct-routed item. No double publishing: no Metricool create
# while a direct publish session for the same item could still post.
TIKTOK_ROUTE_METRICOOL = "metricool"
TIKTOK_ROUTE_DIRECT = "direct"
DIRECT_SESSION_CLOSED = ("ABANDONED", "EXPIRED", "PUBLISHED", "FAILED")


def tiktok_route(it: dict) -> str:
    return it.get("tiktok_route") or TIKTOK_ROUTE_METRICOOL


def _tiktok_metricool_blocker(v: dict, idx: int, now: datetime) -> tuple[str, str] | None:
    it = v.get("content_plan", {}).get("content_items", [])[idx]
    if tiktok_route(it) == TIKTOK_ROUTE_DIRECT:
        return ("AWAITING_FOUNDER_CONSENT", "routed to direct TikTok: per-post consent happens in its publish widget")
    sess = it.get("tiktok_publish_session") or {}
    if sess and sess.get("status") not in DIRECT_SESSION_CLOSED:
        exp = parse_scheduled_for(sess.get("expires_at"), "UTC")
        if exp is None or exp > now:
            return ("DIRECT_SESSION_OPEN", f"direct publish session {sess.get('publish_session_id')} may still post")
    return None


def run_schedule(v: dict, *, allowed, caller=call_tool, now: datetime | None = None,
                 persist=None) -> list[dict]:
    """At most one create per platform per tick. Returns one result per platform
    that had something to do."""
    now = now or _now()
    results = []
    for platform in PLATFORMS:
        plan = plan_next_schedule(v, now, platform)
        st = plan["status"]
        if st == "RECONCILE_ATTEMPT" and not missing_tools(allowed, READ_TIER):
            out = reconcile_attempt(v, plan["index"], allowed=allowed, caller=caller, now=now)
            if persist is not None:
                persist()
            results.append({**out, "index": plan["index"], "platform": platform})
            if out.get("status") != "ATTEMPT_RELEASED":
                continue
            plan = plan_next_schedule(v, now, platform)          # released -> may retry in this same tick
            st = plan["status"]
        if st == "NO_ELIGIBLE_ITEM":
            continue
        if st in ("BLOCKED", "NEEDS_HOSTED_MEDIA"):
            results.append({"status": st, "detail": plan.get("detail"), "index": plan.get("index"),
                            "platform": platform})
            continue
        gap = missing_tools(allowed, READ_TIER if st == "RECONCILE_ATTEMPT" else WRITE_TIER)
        if gap:
            results.append({"status": "GATED", "detail": f"needs approval for: {', '.join(gap)}",
                            "index": plan["index"], "platform": platform})
            continue
        if st == "RECONCILE_ATTEMPT":
            out = reconcile_attempt(v, plan["index"], allowed=allowed, caller=caller, now=now)
            if persist is not None:
                persist()
            results.append({**out, "index": plan["index"], "platform": platform})
            continue
        if platform == "tiktok":
            blocked = _tiktok_metricool_blocker(v, plan["index"], now)
            if blocked:
                results.append({"status": blocked[0], "index": plan["index"],
                                "platform": platform, "detail": blocked[1]})
                continue
        slot = choose_slot(plan["earliest"])
        out = schedule_item(v, plan, slot, BRAND_TZ, allowed=allowed, caller=caller, persist=persist, now=now)
        results.append({**out, "index": plan["index"], "platform": platform, "slot": slot})
    for platform in PLATFORMS:                    # report every hold that is actually stopping ready stock
        for idx, it in _posts_on(v, platform):
            h = hold_for(platform, idx)
            if h and it.get("lifecycle_status") in ("QA_PASSED", "PUBLISH_READY"):
                results.append({"status": "HELD", "index": idx, "platform": platform,
                                "detail": str(h.get("reason"))[:200]})
    return results


def run(v: dict, *, allowed, caller=call_tool, now: datetime | None = None,
        persist=None) -> dict:
    """Per-tick entry point: reconcile what is due, then schedule eligible items."""
    out = run_reconcile(v, allowed=allowed, caller=caller, now=now)
    out["schedule"] = run_schedule(v, allowed=allowed, caller=caller, now=now, persist=persist)
    return out
