"""Audit log (Protected B, AU-2/AU-3/AU-9): who did what, to which deck, and what was refused.

Written **before** `audit.py` exists — this file is the contract:

* one JSON object per line, append-only, UTC timestamp;
* the actor comes from the request identity (`auth.current_identity()`), not from the caller's word;
* every tool call and every asset request is recorded, including refusals;
* **no paths, no tokens, no deck content** — a log is not a second copy of the data it protects;
* a broken log must not break the product, but it must never fail silently either;
* the file rotates so an unbounded log cannot fill a Protected B disk.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "upstream"))

from _env import TMP  # noqa: E402,F401

import audit  # noqa: E402  (this import is what fails first)
import auth  # noqa: E402
import deck_preview  # noqa: E402


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class AuditFormatRegressions(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=TMP, prefix="audit-"))
        self.log = self.dir / "audit.jsonl"
        os.environ["PPT_AUDIT_LOG"] = str(self.log)
        audit.reset()

    def tearDown(self):
        os.environ.pop("PPT_AUDIT_LOG", None)
        audit.reset()

    def test_one_json_object_per_line_with_a_utc_timestamp(self):
        audit.record("tool.call", tool="build_deck", deck_id="d1234567890abcdef", outcome="ok", ms=42)
        audit.record("tool.call", tool="verify_deck", deck_id="d1234567890abcdef", outcome="ok", ms=7)
        events = read_events(self.log)
        self.assertEqual(len(events), 2)
        for event in events:
            self.assertIn("ts", event)
            self.assertTrue(event["ts"].endswith("Z"), event["ts"])
            self.assertEqual(event["event"], "tool.call")
        self.assertEqual([e["tool"] for e in events], ["build_deck", "verify_deck"])

    def test_the_actor_comes_from_the_request_identity(self):
        """Not from an argument: the identity is established by the auth gate, so it cannot be spoofed."""
        token = auth.set_current(auth.Identity(subject="user-alice", tenant="tenant-1", mode="entra"))
        try:
            audit.record("tool.call", tool="build_deck", outcome="ok")
        finally:
            auth.reset_current(token)
        event = read_events(self.log)[0]
        self.assertEqual(event["subject"], "user-alice")
        self.assertEqual(event["tenant"], "tenant-1")

    def test_no_paths_or_tokens_ever_reach_the_log(self):
        """A log is not a copy of the data it protects; and a leaked path is a leaked path."""
        secret = "super-secret-bearer-token-value"
        audit.record("tool.call", tool="preview_deck", deck_id="d1234567890abcdef",
                     file=str(TMP / "out" / "deck.pptx"), note=f"Authorization: Bearer {secret}",
                     outcome="ok")
        raw = self.log.read_text(encoding="utf-8")
        self.assertNotIn(str(TMP), raw)
        self.assertNotIn(secret, raw)
        self.assertNotIn("Bearer ", raw)
        # the useful fields survive
        event = json.loads(raw.strip())
        self.assertEqual(event["deck_id"], "d1234567890abcdef")
        self.assertEqual(event["file"], "[path]")
        self.assertEqual(event["note"], "Authorization: [redacted]")

    def test_a_failing_log_does_not_break_the_request(self):
        """A full disk must not stop somebody building a deck — but it must be visible."""
        os.environ["PPT_AUDIT_LOG"] = "/proc/definitely-not-writable/audit.jsonl"
        audit.reset()
        audit.record("tool.call", tool="build_deck", outcome="ok")  # must not raise
        self.assertTrue(audit.last_error(), "a broken audit sink has to be reported, not swallowed")

    def test_the_log_rotates_at_a_size_limit(self):
        os.environ["PPT_AUDIT_MAX_BYTES"] = "600"
        audit.reset()
        for i in range(40):
            audit.record("tool.call", tool="build_deck", deck_id=f"d{i:016d}", outcome="ok")
        self.assertTrue(self.log.is_file())
        self.assertTrue(self.log.with_suffix(".jsonl.1").is_file(), "no rotation happened")
        # Retention is deliberately bounded (one previous file), so totals are small — and the ship-to-SIEM
        # requirement is what makes that acceptable. What matters here: rotation kept valid JSON.
        events = read_events(self.log) + read_events(self.log.with_suffix(".jsonl.1"))
        self.assertGreaterEqual(len(events), 2)
        self.assertLess(len(events), 40, "rotation is supposed to bound the file count")
        os.environ.pop("PPT_AUDIT_MAX_BYTES", None)


class AuditCoverageRegressions(unittest.TestCase):
    """The wiring: tool calls and asset decisions land in the log without every caller remembering."""

    def setUp(self):
        import server

        self.dir = Path(tempfile.mkdtemp(dir=TMP, prefix="audit-cover-"))
        os.environ["PPT_AUDIT_LOG"] = str(self.dir / "audit.jsonl")
        os.environ["PPT_AUTH_MODE"] = "none"
        auth.reset_verifier()
        audit.reset()
        self.log = Path(os.environ["PPT_AUDIT_LOG"])
        self.tools = {n: t.fn for n, t in server.app._tool_manager._tools.items()}

    def tearDown(self):
        os.environ.pop("PPT_AUDIT_LOG", None)
        os.environ.pop("PPT_AUTH_MODE", None)
        auth.reset_verifier()
        audit.reset()

    def test_every_tool_call_is_recorded_with_its_deck_id(self):
        built = self.tools["build_deck"](template="corporate-2slide.pptx", title="Audited",
                                        slides=[{"title": "One", "bullets": ["a"]}])
        deck_id = built["deck_id"]
        events = [e for e in read_events(self.log) if e["event"] == "tool.call"]
        self.assertIn("build_deck", [e["tool"] for e in events])
        self.assertEqual([e for e in events if e["tool"] == "build_deck"][0]["deck_id"], deck_id)
        self.assertEqual([e for e in events if e["tool"] == "build_deck"][0]["outcome"], "ok")

    def test_a_failing_tool_is_recorded_as_an_error(self):
        with self.assertRaises(FileNotFoundError):
            self.tools["get_deck_outline"](file="d0123456789abcdef")
        events = [e for e in read_events(self.log) if e["event"] == "tool.call"]
        failures = [e for e in events if e["tool"] == "get_deck_outline"]
        self.assertTrue(failures)
        self.assertEqual(failures[-1]["outcome"], "error")
        self.assertIn("FileNotFoundError", failures[-1].get("error", ""))

    def test_an_asset_request_is_recorded_with_its_decision(self):
        deck = TMP / "out" / "audited.pptx"
        shutil.copyfile(ROOT / "templates" / "corporate-2slide.pptx", deck)
        data = self.tools["preview_deck"](file=deck_preview.remember(deck))
        audit.record_asset(deck_id=data["deck_id"], name="slide-1.png", outcome="ok")
        audit.record_asset(deck_id=data["deck_id"], name="deck.json", outcome="denied")
        events = [e for e in read_events(self.log) if e["event"] == "asset.get"]
        self.assertEqual([e["outcome"] for e in events], ["ok", "denied"])
        self.assertEqual(events[0]["deck_id"], data["deck_id"])

    def test_a_rejected_credential_is_recorded(self):
        audit.record_auth_failure(path="/mcp", status=401, error="invalid_token")
        event = [e for e in read_events(self.log) if e["event"] == "auth.reject"][0]
        self.assertEqual((event["status"], event["error"]), (401, "invalid_token"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
