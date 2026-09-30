"""browser_executor: availability classification, the retryable handoff
contract, failure classes, and the structural no-browser/no-network boundary."""
from __future__ import annotations

import ast
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import browser_executor as be

ROOT = Path(__file__).resolve().parent


def _png(path: Path, w: int, h: int) -> Path:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x00" * w * 3 for _ in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


READY = {"browser_attached": True, "signed_in": True, "challenge_visible": False, "composer_visible": True,
         "image_capability_visible": True, "usage_cap_visible": False}


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class Boundary(unittest.TestCase):
    def test_no_browser_http_or_credential_library_is_imported(self):
        tree = ast.parse((ROOT / "browser_executor.py").read_text())
        mods = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
        mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertLessEqual(mods, {"__future__", "hashlib", "json", "sys", "time", "dataclasses", "pathlib",
                                    "typing", "struct", "render_masters"})

    def test_probe_refuses_anything_but_visibility_booleans(self):
        with self.assertRaisesRegex(ValueError, "non-observation"):
            be.classify_availability({**READY, "cookie": "x"})

    def test_this_environment_has_no_browser_route(self):
        self.assertEqual(be.classify_availability(be.UnattachedExecutor().probe()), be.NO_BROWSER_ROUTE)


class Availability(unittest.TestCase):
    def test_classification_order(self):
        c = be.classify_availability
        self.assertEqual(c({}), be.NO_BROWSER_ROUTE)
        self.assertEqual(c({**READY, "challenge_visible": True, "signed_in": False}), be.CHALLENGE_PRESENT)
        self.assertEqual(c({**READY, "signed_in": False}), be.NOT_SIGNED_IN)
        self.assertEqual(c({**READY, "composer_visible": False}), be.COMPOSER_UNREACHABLE)
        self.assertEqual(c({**READY, "usage_cap_visible": True}), be.USAGE_CAPPED)
        self.assertEqual(c({**READY, "image_capability_visible": False}), be.COMPOSER_READY_NO_IMAGE)
        self.assertEqual(c(READY), be.IMAGE_CAPABLE)

    def test_a_stale_probe_counts_as_unprobed(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = Clock()
            s = be.RouteStore(Path(tmp) / "r.json", clock)
            s.record_probe(READY)
            self.assertEqual(s.availability(), be.IMAGE_CAPABLE)
            clock.t += be.PROBE_MAX_AGE_S + 1
            self.assertEqual(s.availability(), be.UNPROBED)


class HandoffContract(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        d = Path(self._tmp.name)
        self.clock = Clock()
        self.store = be.RouteStore(d / "route.json", self.clock)
        self.pack = d / "slide1_prompt.txt"
        self.pack.write_text("prompt")
        self.ref = _png(d / "ref.png", 4, 5)
        self.out = d / "out.png"
        self.h = self.store.enqueue(be.new_handoff(33, 1, "public_carousel_4x5", self.pack, [self.ref], str(self.out)))

    def _ready(self, authorized=True):
        if authorized:
            self.store.authorize_execution("founder (test)", "2026-09-30")
        self.store.record_probe(READY)

    def test_execution_is_policy_blocked_by_default(self):
        self.store.record_probe(READY)
        with self.assertRaisesRegex(be.ExecutorContractError, "POLICY_BLOCKED"):
            self.store.claim("ex1")

    def test_claim_needs_a_fresh_image_capable_probe(self):
        self.store.authorize_execution("founder (test)", "2026-09-30")
        self.store.record_probe({**READY, "usage_cap_visible": True})
        with self.assertRaisesRegex(be.ExecutorContractError, "USAGE_CAPPED"):
            self.store.claim("ex1")

    def test_enqueue_is_idempotent_and_a_new_prompt_is_a_new_handoff(self):
        again = self.store.enqueue(be.new_handoff(33, 1, "public_carousel_4x5", self.pack, [self.ref], str(self.out)))
        self.assertEqual(again.handoff_id, self.h.handoff_id)
        self.pack.write_text("revised prompt")
        other = be.new_handoff(33, 1, "public_carousel_4x5", self.pack, [self.ref], str(self.out))
        self.assertNotEqual(other.handoff_id, self.h.handoff_id)

    def test_happy_path_validates_the_master_and_hands_back_the_bookkeeping_command(self):
        self._ready()
        h = self.store.claim("ex1")
        self.store.mark_submitted(h.handoff_id, "ex1")
        res = self.store.record_result(h.handoff_id, "ex1", _png(self.out, 1080, 1350))
        self.assertTrue(res["ok"])
        self.assertIn("episode_ops.py record-generated 33 1", res["next"])

    def test_wrong_canvas_is_output_invalid_and_retryable(self):
        self._ready()
        h = self.store.claim("ex1")
        self.store.mark_submitted(h.handoff_id, "ex1")
        res = self.store.record_result(h.handoff_id, "ex1", _png(self.out, 1024, 1536))
        self.assertEqual((res["failure"], res["state"]), ("OUTPUT_INVALID", be.RETRY_WAIT))

    def test_retries_stop_at_max_attempts(self):
        self._ready()
        for i in range(be.MAX_ATTEMPTS):
            h = self.store.claim("ex1")
            out = self.store.record_failure(h.handoff_id, "ex1", "NETWORK_ERROR")
            self.clock.t += 61
            self.store.record_probe(READY)
        self.assertEqual(out["state"], be.FAILED_TERMINAL)
        self.assertIsNone(self.store.claim("ex1"))

    def test_cooldown_is_respected(self):
        self._ready()
        h = self.store.claim("ex1")
        self.store.record_failure(h.handoff_id, "ex1", "USAGE_CAPPED")
        self.assertIsNone(self.store.claim("ex1"))
        self.clock.t += 3 * 3600 + 1
        self.store.record_probe(READY)
        self.assertEqual(self.store.claim("ex1").handoff_id, h.handoff_id)

    def test_human_failures_never_auto_retry(self):
        for code in ("SESSION_EXPIRED", "CHALLENGE_PRESENT", "PERMISSION_DENIED", "UI_CHANGED", "OUTCOME_UNKNOWN"):
            self.assertTrue(be.FAILURES[code].needs_human, code)
            self.assertFalse(be.FAILURES[code].retryable, code)
        self._ready()
        h = self.store.claim("ex1")
        self.assertEqual(self.store.record_failure(h.handoff_id, "ex1", "CHALLENGE_PRESENT")["state"], be.NEEDS_HUMAN)
        self.assertIsNone(self.store.claim("ex1"))

    def test_crash_before_submit_requeues_but_crash_after_submit_needs_a_human(self):
        self._ready()
        h = self.store.claim("ex1")
        self.clock.t += be.LEASE_S + 1
        self.store.record_probe(READY)
        h2 = self.store.claim("ex2")
        self.assertEqual(h2.handoff_id, h.handoff_id)            # safe: nothing was submitted
        self.store.mark_submitted(h2.handoff_id, "ex2")
        self.clock.t += be.LEASE_S + 1
        self.store.record_probe(READY)
        self.assertIsNone(self.store.claim("ex3"))               # never re-run a submitted prompt
        state = self.store.load()["handoffs"][h.handoff_id]
        self.assertEqual((state["state"], state["last_failure"]["code"]), (be.NEEDS_HUMAN, "OUTCOME_UNKNOWN"))

    def test_only_the_lease_holder_can_report(self):
        self._ready()
        h = self.store.claim("ex1")
        with self.assertRaises(be.ExecutorContractError):
            self.store.mark_submitted(h.handoff_id, "someone-else")


if __name__ == "__main__":
    unittest.main()
