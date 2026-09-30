#!/usr/bin/env python3
"""PS-05 Venture Manager UI server. Forked from research-orchestrator's
server.py skeleton (ThreadingHTTPServer + BaseHTTPRequestHandler + a fixed
JSON route table + static-file allowlist) -- routes and state model are new.

Runs on port 8790 (research-orchestrator uses 8780 -- deliberately
different, deliberately independent, can run at the same time).
"""

from __future__ import annotations

import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import audit
import commercial_events
import dashboard_status
import fan_memory
import fanvue_auth
import scheduler_heartbeat
import state as st
import unit_economics

HOST, PORT = "127.0.0.1", 8790
ORCH_DIR = Path(__file__).resolve().parent
STATIC_DIR = ORCH_DIR / "static"

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def _fanvue_chat_scope_granted() -> bool:
    """Reads ONLY the non-secret 'scope' field from the OAuth credentials
    file -- never the access/refresh token values."""
    cred_path = ORCH_DIR / ".fanvue_runtime" / "credentials.json"
    if not cred_path.is_file():
        return False
    try:
        scope = json.loads(cred_path.read_text()).get("scope", "")
    except (OSError, ValueError):
        return False
    scopes = set(scope.split())
    return "read:chat" in scopes and "write:chat" in scopes


def _scheduler_task_snapshot() -> dict | None:
    """Ground truth from a real list_scheduled_tasks call, persisted by an
    active Claude session (this HTTP server can't call MCP tools itself) --
    see ps05_ops.py record_scheduler_snapshot for why this exists."""
    path = ORCH_DIR / "state" / "scheduler_task_snapshot.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def build_status() -> dict:
    v = st.load()
    if v is None:
        return {"seeded": False}
    pending = st.load_pending_approval()
    heartbeat = scheduler_heartbeat.status()
    activity_log = audit.load(limit=100)[::-1]  # newest first for the UI
    chat_scope_granted = _fanvue_chat_scope_granted()
    task_snapshot = _scheduler_task_snapshot()
    sales_metrics = dashboard_status.fanvue_sales_metrics(fan_memory.load_all())
    # h1_market's raw_subscribers/gross_revenue_usd are the persisted,
    # scheduler-refreshed numbers -- the dashboard never makes a live Fanvue
    # API call itself (a stale/expired token would otherwise break every
    # page load); it only reads what the last successful tick recorded.
    fanvue_summary = {"ok": True, "subscribers": v["metrics"]["h1_market"]["raw_subscribers"],
                      "gross_revenue_usd": v["metrics"]["h1_market"]["gross_revenue_usd"]}

    return {
        "seeded": True,
        "venture": v,
        "pending_approval": pending,
        # 2026-09-16: pending_approval stays the single oldest entry for the
        # existing single-item card (unchanged contract); this is the FULL
        # queue, for the Founder Action Center to show every independent
        # approval, not just the front one.
        "pending_approvals": st.load_pending_approvals(),
        "unit_economics": unit_economics.compute(v),
        "activity_log": activity_log,
        "activity_feed": [{**e, "category": dashboard_status.categorize_actor(e)}
                          for e in activity_log[:30]],
        "scheduler_heartbeat": heartbeat,
        "tool_permission_health": dashboard_status.tool_permission_health(
            activity_log, heartbeat, task_snapshot=task_snapshot),
        "scheduler_trigger_health": dashboard_status.scheduler_trigger_health(
            heartbeat, task_snapshot=task_snapshot),
        "fanvue_chat_ingestion_health": dashboard_status.fanvue_chat_ingestion_health(
            v, activity_log, chat_scope_granted=chat_scope_granted),
        "sales_response_loop_health": dashboard_status.sales_response_loop_health(
            activity_log, chat_scope_granted=chat_scope_granted),
        "fanvue_sales_metrics": sales_metrics,
        "content_pipeline_health": dashboard_status.content_pipeline_health(v.get("content_plan", {})),
        "manager_accountability": dashboard_status.manager_accountability(v),
        "business_scoreboard": dashboard_status.business_scoreboard(v, fanvue_summary, sales_metrics),
        "storefront_readiness": dashboard_status.storefront_readiness(v),
        "commercial_scoreboard": dashboard_status.commercial_scoreboard(
            v, fanvue_summary, sales_metrics,
            dashboard_status.content_pipeline_health(v.get("content_plan", {}))),
        "stall_detection": dashboard_status.stall_detection(v, activity_log, heartbeat,
                                                            pending_approval=pending),
        "system_status": dashboard_status.system_status(v, pending_approval=pending,
                                                         heartbeat=heartbeat),
        "next_checkpoint": dashboard_status.next_checkpoint(v),
        "funnel": dashboard_status.funnel(v, fanvue_summary),
        "platform_health": dashboard_status.platform_health(
            v, commercial_events.load_previous_snapshot()),
        "commercial_events": dashboard_status.commercial_events_view(v),
        "h1_status": dashboard_status.h1_status(v, fanvue_summary),
        "h2_status": dashboard_status.h2_status(v),
        "next_actions_queue": dashboard_status.next_actions_queue(v),
    }


def _fanvue_oauth_callback_page(query: dict) -> tuple[int, str]:
    """Handles Fanvue's OAuth redirect after the founder clicks Allow on the
    consent screen -- this route's only job is completing the token
    exchange (a mechanical step) the instant the founder's own click lands;
    it never simulates or substitutes for that click itself."""
    codes = query.get("code")
    states = query.get("state")
    if not codes or not states:
        error = query.get("error", ["unknown"])[0]
        error_desc = query.get("error_description", [""])[0]
        return 400, (f"<h3>Fanvue authorization failed</h3><p>{error}: {error_desc}</p>"
                    f"<p>No changes made. Ask the assistant to regenerate the authorize link.</p>")

    result = fanvue_auth.exchange_code_for_tokens(codes[0], states[0])
    if not result.get("ok"):
        audit.append(actor="founder", action="fanvue_oauth_reauth_failed",
                     detail=f"Token exchange failed after founder clicked Allow: {result}")
        return 500, (f"<h3>Fanvue authorization exchange failed</h3><p>{result}</p>"
                    f"<p>The consent click succeeded but the token exchange did not -- "
                    f"this needs investigation, not a retry of the same link.</p>")

    granted_scopes = (result.get("scope") or "").split()
    with st.venture_lock():           # read-modify-write: no tick write lands mid-span
        v = st.load()
        if v is not None:
            v.setdefault("accounts", {}).setdefault("fanvue", {})["scopes_granted"] = granted_scopes
            if "read:chat" in granted_scopes and "write:chat" in granted_scopes:
                for a in v.get("queued_actions", []):
                    if a.get("kind") == "FANVUE_CHAT_SCOPE_REEVALUATION" and a["status"] == "WAITING_FOR_FOUNDER_CONSENT":
                        st.resolve_queued_action(v, a["id"], status="RESOLVED_BY_FOUNDER")
            st.save(v)
    if v is not None:
        audit.append(actor="founder", action="fanvue_oauth_reauth_completed",
                     detail=f"Founder completed Fanvue OAuth re-consent via the browser. Granted "
                            f"scopes now: {', '.join(granted_scopes)}. read:chat+write:chat "
                            f"{'present' if 'read:chat' in granted_scopes and 'write:chat' in granted_scopes else 'STILL MISSING'}.")

    return 200, ("<h3>Fanvue authorization complete</h3>"
                "<p>read:chat/write:chat scope has been granted and the token exchange succeeded. "
                "You can close this tab -- the next scheduled tick (or the assistant, right now) "
                "will pick this up automatically.</p>")


def approve(answer: str, note: str = "") -> dict:
    with st.venture_lock():           # read-modify-write: no tick write lands mid-span
        v = st.load()
        pending = st.load_pending_approval()
        if v is None or pending is None:
            return {"error": "nothing pending"}

        minutes = float(pending.get("estimated_minutes", 5))
        category_map = {
            "IDENTITY_KYC_REQUIRED": "kyc", "CREDENTIALS_REQUIRED": "credentials",
            "SPEND_REQUIRED": "spend", "LEGAL_REPUTATIONAL_DECISION": "legal",
            "MAJOR_GO_KILL_DECISION": "go_kill",
        }
        category = category_map.get(pending["reason"], "could_not_delegate")
        st.record_intervention(v, minutes=minutes, reason=pending["what"], category=category)

        if pending["reason"] == "MAJOR_GO_KILL_DECISION":
            rec = v.get("last_go_kill_recommendation", {})
            applied = answer if answer in st.DECISIONS else rec.get("recommendation", "TEST")
            v["decision"] = applied
            if applied == "ITERATE":
                v["iterate_count"] = v.get("iterate_count", 0) + 1
            if applied == "KILL":
                v["phase"] = "VENTURE_CLOSED"
            if applied == "VALIDATED":
                v["phase"] = "AUTOMATION_FEASIBILITY"
            audit.append(actor="founder", action="go_kill_decision",
                         detail=f"founder set decision -> {applied}" + (f" ({note})" if note else ""))
        else:
            audit.append(actor="founder", action="approval",
                         detail=f"approved: {pending['what']}" + (f" ({note})" if note else ""))

        st.save(v)
        st.clear_pending_approval()
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    server_version = "PS05VentureManagerUI/1"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name: str, ctype: str) -> None:
        data = (STATIC_DIR / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/fanvue/oauth/callback":
            status, html = _fanvue_oauth_callback_page(parse_qs(parsed.query))
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in STATIC_FILES:
            name, ctype = STATIC_FILES[path]
            try:
                self._file(name, ctype)
            except OSError:
                self._json({"error": "static file missing"}, status=500)
            return
        if path == "/api/state":
            self._json(build_status())
            return
        self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            self._json({"error": "invalid JSON body"}, status=400)
            return
        if not isinstance(payload, dict):
            payload = {}

        if path == "/api/approve":
            result = approve(payload.get("answer", "approve"), payload.get("note", ""))
            self._json(result, status=200 if result.get("ok") else 400)
            return

        if path == "/api/tick":
            proc = subprocess.run([sys.executable, str(ORCH_DIR / "manager.py"), "tick"],
                                  cwd=str(ORCH_DIR), capture_output=True, text=True, timeout=300)
            try:
                result = json.loads(proc.stdout or "{}")
            except ValueError:
                result = {"error": proc.stderr[-500:] or "tick produced no output"}
            self._json(result)
            return

        self._json({"error": "not found"}, status=404)


def main() -> None:
    STATIC_DIR.mkdir(exist_ok=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"PS-05 Venture Manager UI: http://{HOST}:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
