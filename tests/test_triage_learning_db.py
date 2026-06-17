"""Unit-Tests für TriageLearningDB (HBE-974).

Abdeckung:
- record_override — Dedup via INSERT OR IGNORE (zweiter Aufruf mit gleicher
  message_id → changed == 0, returns False)
- upsert_pattern — Counter-Increment (3 Aufrufe → count == 3)
- recall_pattern — Threshold-Grenze (count < threshold → None;
  count == threshold → Pattern zurück)
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_poller() -> types.ModuleType:
    """Import lena_mail_triage_poller with minimal stubs for heavy deps."""
    stubs: dict[str, types.ModuleType] = {}

    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = lambda: None  # type: ignore[attr-defined]
    stubs["dotenv"] = dotenv_mod

    requests_mod = types.ModuleType("requests")
    stubs["requests"] = requests_mod

    anthropic_mod = types.ModuleType("anthropic")
    anthropic_mod.Anthropic = None  # type: ignore[attr-defined]
    stubs["anthropic"] = anthropic_mod

    yaml_mod = types.ModuleType("yaml")
    yaml_mod.safe_load = lambda _: {}  # type: ignore[attr-defined]
    stubs["yaml"] = yaml_mod

    with mock.patch.dict(sys.modules, stubs):
        os.environ.setdefault("API_SECRET_KEY", "test-key")
        os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
        os.environ.setdefault("LENA_MAIL_TRIAGE_STATE_FILE", "/tmp/lena-triage-test.state")
        os.environ.setdefault("LENA_MAIL_TRIAGE_LOG_FILE", "/tmp/lena-triage-test.log")
        os.environ.setdefault("LENA_MAIL_TRIAGE_LEARNING_DB", "/tmp/lena-triage-test.db")

        spec = importlib.util.spec_from_file_location(
            "lena_mail_triage_poller_test",
            REPO_ROOT / "lena_mail_triage_poller.py",
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["lena_mail_triage_poller_test"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod


_poller = _load_poller()
TriageLearningDB = _poller.TriageLearningDB


class TestTriageLearningDB(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="triage_learning_db_test_")
        self._db_path = os.path.join(self._tmpdir, "triage.db")
        self.db = TriageLearningDB(self._db_path)

    # ── record_override ───────────────────────────────────────────────────────

    def test_record_override_first_call_returns_true(self) -> None:
        result = self.db.record_override(
            message_id="msg-001",
            sender_domain="example.com",
            subject_prefix="rechnung",
            original_action="antworten",
            original_priority="mittel",
            override_action="ablegen",
            override_priority="niedrig",
        )
        self.assertTrue(result)

    def test_record_override_dedup_second_call_returns_false(self) -> None:
        """INSERT OR IGNORE: identical message_id must not create a second row."""
        params = dict(
            message_id="msg-001",
            sender_domain="example.com",
            subject_prefix="rechnung",
            original_action="antworten",
            original_priority="mittel",
            override_action="ablegen",
            override_priority="niedrig",
        )
        self.db.record_override(**params)
        second = self.db.record_override(**params)
        self.assertFalse(second)

    def test_record_override_distinct_ids_both_inserted(self) -> None:
        base = dict(
            sender_domain="example.com",
            subject_prefix="rechnung",
            original_action="antworten",
            original_priority="mittel",
            override_action="ablegen",
            override_priority="niedrig",
        )
        r1 = self.db.record_override(message_id="msg-A", **base)
        r2 = self.db.record_override(message_id="msg-B", **base)
        self.assertTrue(r1)
        self.assertTrue(r2)

    # ── upsert_pattern ────────────────────────────────────────────────────────

    def test_upsert_pattern_three_calls_gives_count_three(self) -> None:
        """Three upsert calls for the same key → count == 3."""
        for _ in range(3):
            count = self.db.upsert_pattern("example.com", "rechnung", "ablegen", "niedrig")
        self.assertEqual(count, 3)

    def test_upsert_pattern_first_call_gives_count_one(self) -> None:
        count = self.db.upsert_pattern("example.com", "test", "antworten", "hoch")
        self.assertEqual(count, 1)

    def test_upsert_pattern_different_keys_independent(self) -> None:
        """Different (domain, prefix, action, priority) combinations have separate counters."""
        self.db.upsert_pattern("a.com", "subject", "ablegen", "niedrig")
        self.db.upsert_pattern("a.com", "subject", "ablegen", "niedrig")
        count_b = self.db.upsert_pattern("b.com", "subject", "ablegen", "niedrig")
        self.assertEqual(count_b, 1)

    # ── recall_pattern ────────────────────────────────────────────────────────

    def test_recall_pattern_below_threshold_returns_none(self) -> None:
        """count == 2 with threshold == 3 → None."""
        self.db.upsert_pattern("example.com", "rechnung", "ablegen", "niedrig")
        self.db.upsert_pattern("example.com", "rechnung", "ablegen", "niedrig")
        result = self.db.recall_pattern("example.com", "rechnung", threshold=3)
        self.assertIsNone(result)

    def test_recall_pattern_at_threshold_returns_pattern(self) -> None:
        """count == 3 with threshold == 3 → full pattern dict."""
        for _ in range(3):
            self.db.upsert_pattern("example.com", "rechnung", "ablegen", "niedrig")
        result = self.db.recall_pattern("example.com", "rechnung", threshold=3)
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "ablegen")
        self.assertEqual(result["priority"], "niedrig")
        self.assertEqual(result["count"], 3)

    def test_recall_pattern_above_threshold_returns_pattern(self) -> None:
        """count == 5 with threshold == 3 → pattern returned."""
        for _ in range(5):
            self.db.upsert_pattern("example.com", "rechnung", "ablegen", "niedrig")
        result = self.db.recall_pattern("example.com", "rechnung", threshold=3)
        self.assertIsNotNone(result)
        self.assertEqual(result["count"], 5)

    def test_recall_pattern_unknown_key_returns_none(self) -> None:
        result = self.db.recall_pattern("unknown.com", "nope", threshold=1)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
