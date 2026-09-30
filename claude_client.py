"""Generic LLM-invocation primitive for the PS-05 Venture Manager.

Forked from research-orchestrator/run.py's claude_call() (copied, not
imported — this project has zero runtime dependency on research-orchestrator).
Only the generic, provider-agnostic mechanics were kept: subprocess `claude -p`
call, JSON-envelope parsing, budget guard. All B2B/email-specific budget modes
(_email_mode, _post_human, etc.) were dropped — this system has one simple
per-invocation call budget instead (see MAX_LLM_CALLS_PER_TICK in manager.py).
"""

from __future__ import annotations

import json
import subprocess

MODEL = "sonnet"

_UNAVAILABLE_MARKERS = (
    "usage limit", "rate limit", "quota", "credit balance", "insufficient_quota",
)


class LLMError(RuntimeError):
    """Any Claude CLI invocation failure (auth, quota, malformed output)."""


def claude_call(prompt: str, *, tools=None, timeout: int = 180) -> dict:
    """One model turn via the Claude Max CLI (`claude -p`), no API key needed.

    `tools` truthy -> live web research is allowed (e.g. WebSearch/WebFetch).
    Returns {text, cost_usd, num_turns, duration_ms}. Raises LLMError on any
    failure -- callers decide whether/how to retry, this function never does.
    """
    cmd = ["claude", "-p", "--model", MODEL,
           "--output-format", "json", "--no-session-persistence"]
    if tools:
        cmd += ["--allowedTools", ",".join(tools)]
    else:
        # Pure-judgment calls need no connectors. Without this they inherit
        # every user-scope MCP server (legacy `metricool` needing auth,
        # `metricool-ashkan`, ...) and burn turns probing tools they can never
        # use unattended. Narrowing only -- grants nothing.
        cmd += ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']

    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise LLMError("claude CLI not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise LLMError(f"claude call timed out after {timeout}s") from exc

    out = (proc.stdout or "").strip()
    env = None
    if out:
        try:
            env = json.loads(out)
        except ValueError:
            env = None

    ok = (env is not None and not env.get("is_error")
          and str(env.get("result") or "").strip() != "")
    if not ok:
        stderr = (proc.stderr or "").strip()
        detail = ""
        if env is not None:
            detail = next((env[k] for k in ("error", "message", "result")
                          if isinstance(env.get(k), str) and env[k].strip()), "")
        scan = (stderr + "\n" + str(detail)).lower()
        if any(m in scan for m in _UNAVAILABLE_MARKERS):
            raise LLMError("claude usage/quota limit reached: " + (stderr or str(detail))[:200])
        if not out:
            raise LLMError(f"claude exited {proc.returncode} with no output: {stderr[:200]}")
        if env is None:
            raise LLMError("could not parse claude json envelope: " + out[:200])
        raise LLMError(f"claude reported an error (is_error={env.get('is_error')}, "
                       f"subtype={env.get('subtype')}): {str(detail)[:160]}")

    return {
        "text": env.get("result", ""),
        "cost_usd": env.get("total_cost_usd"),
        "num_turns": env.get("num_turns"),
        "duration_ms": env.get("duration_ms"),
    }


def strip_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()


def extract_json(text: str):
    """Best-effort JSON extraction from a model turn's text (may be fenced,
    may have leading/trailing prose). Raises ValueError if nothing parses.

    Tries strict parsing first; falls back to strict=False (tolerates
    literal control characters like an unescaped newline inside a string)
    since longer free-text fields (e.g. multi-sentence reasoning) make that
    the single most common real-world malformation -- this is a parsing
    tolerance, not a schema relaxation; _require() below still enforces
    exactly the same required keys either way."""
    t = strip_fences(text)
    candidates = [t]
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(t[start:end + 1])

    last_exc: ValueError | None = None
    for candidate in candidates:
        for strict in (True, False):
            try:
                return json.loads(candidate, strict=strict)
            except ValueError as exc:
                last_exc = exc
    raise ValueError(f"no JSON object found in model output: {last_exc}")
