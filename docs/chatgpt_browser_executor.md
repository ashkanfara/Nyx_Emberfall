# ChatGPT browser image executor: readiness and contract

Module: `browser_executor.py`. Tests: `test_browser_executor.py`. Runtime state lives in
`state/browser_executor/chatgpt_browser_route.json` (gitignored).

## Readiness check, 2026-09-30 (read-only)

Run from the Claude Code cloud session that maintains this repo. Nothing was typed,
uploaded, generated, posted or changed. chatgpt.com was not opened.

| Check | Result |
|---|---|
| Browser-control tool available to this session | none |
| Local Chrome DevTools endpoint (9222/9223/9229) | none listening |
| Chromium profile with a signed-in session in the container | none |
| `~/Projects/chatgpt-claude-bridge` relay | not present (it lives on the founder's Mac) |

**Availability: `NO_BROWSER_ROUTE`.** The signed-in ChatGPT session exists only on the founder's
machine. Nothing here can reach its composer, so composer and image-capability visibility are
**unverified**, not failed. An unauthenticated headless visit from the cloud would prove nothing
about the signed-in session, so none was made.

## Policy blocker (decide before any execution)

OpenAI's Terms of Use forbid automatically or programmatically extracting Output from the
consumer service. An unattended executor that submits prompts on chatgpt.com and saves the
images fits that description whatever drives it, and the account at risk is the founder's own.
The automatic path OpenAI sanctions is the Images API (`openai_image_provider.py`, paid per image,
already gated by founder spend grants). `RouteStore.claim()` therefore refuses with
`POLICY_BLOCKED` until `authorize_execution(decided_by, terms_reviewed_on)` is recorded. Nothing in
the repo calls that function.

## Contract

- **Availability states:** `UNPROBED`, `NO_BROWSER_ROUTE`, `NOT_SIGNED_IN`, `CHALLENGE_PRESENT`,
  `COMPOSER_UNREACHABLE`, `COMPOSER_READY_NO_IMAGE`, `USAGE_CAPPED`, `IMAGE_CAPABLE`. They are
  derived only from six visibility booleans a prober reports. Any other field (cookie, token,
  prompt) is refused. A probe older than 15 minutes counts as `UNPROBED`.
- **Handoff:** its id comes from the item, slide, render master, prompt-pack bytes and reference
  bytes. Re-enqueueing the same work returns the same id, and a changed prompt gets a new id.
  States: `QUEUED → CLAIMED → SUBMITTED → RESULT_RECORDED`, plus `RETRY_WAIT`, `NEEDS_HUMAN` and
  `FAILED_TERMINAL`. The lease is 10 minutes. If a lease expires before submit, the handoff is
  re-queued. If it expires after submit, the outcome is `OUTCOME_UNKNOWN` and it goes to
  `NEEDS_HUMAN`, never a blind re-run. It gets at most 3 attempts.
- **Result:** the saved file must be a PNG at the render master's exact canvas (1080x1350 or
  1080x1920). Anything else is `OUTPUT_INVALID`. On success the store returns the
  `episode_ops.py record-generated ...` command. It does not run it.
- **Failure classes:**

  | Class | Outcome |
  |---|---|
  | `SESSION_EXPIRED`, `CHALLENGE_PRESENT`, `PERMISSION_DENIED`, `UI_CHANGED`, `OUTCOME_UNKNOWN`, `POLICY_BLOCKED` | human only |
  | `USAGE_CAPPED` | retry after 3h |
  | `RATE_LIMITED` | retry after 15m |
  | `NETWORK_ERROR` | retry after 1m, before submit only |
  | `DOWNLOAD_FAILED` | retry the save only, never regenerate |
  | `OUTPUT_INVALID` | retry |
  | `CONTENT_REFUSED` | terminal; needs a new prompt pack |

## Smallest next real test

Only after the founder decides the policy question. On the founder's Mac, founder present,
in the normal signed-in browser:

1. Open a new ChatGPT chat. Do not type anything.
2. An observer (the founder, or a read-only browser tool the founder attaches) records only the six
   booleans: browser attached, signed in, challenge visible, composer visible, image capability
   visible (the image tool/option shown in the composer's tools menu, opened and closed without
   selecting), usage-cap banner visible.
3. `python3 browser_executor.py record-probe probe.json` and then `python3 browser_executor.py status`.

Pass = `IMAGE_CAPABLE`. That proves reachability only. Unattended generation stays blocked by policy.
