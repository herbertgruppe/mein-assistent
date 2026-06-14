"""Unit-Tests für lena_asana_poller.py (HBE-893).

Abdeckung:
- _load_state / _save_state Roundtrip
- _save_state Truncate behält die neuesten GIDs (Recency-Order)
- _save_state bei 50k+ GIDs: nur MAX_PROCESSED_GIDS neueste bleiben
- _get_workspace_gid: ENV-Override vs Auto-Discovery
- _is_mention_story / _is_assignment_story Filter-Logik
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_poller() -> types.ModuleType:
    """Import lena_asana_poller with minimal stubs for heavy dependencies."""
    stubs: dict[str, types.ModuleType] = {}

    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = lambda: None  # type: ignore[attr-defined]
    stubs["dotenv"] = dotenv_mod

    requests_mod = types.ModuleType("requests")
    stubs["requests"] = requests_mod

    with mock.patch.dict(sys.modules, stubs):
        os.environ.setdefault("ASANA_ACCESS_TOKEN", "test-token")
        os.environ.setdefault("LENA_ASANA_STATE_FILE", "/tmp/lena-test-state.json")
        os.environ.setdefault("LENA_ASANA_LOG_FILE", "/tmp/lena-test.log")

        spec = importlib.util.spec_from_file_location(
            "lena_asana_poller",
            REPO_ROOT / "lena_asana_poller.py",
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["lena_asana_poller"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod


poller = _load_poller()


# ── State Roundtrip ───────────────────────────────────────────────────────────

class TestStateRoundtrip(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path("/tmp/lena-test-roundtrip.state")
        if self._tmp.exists():
            self._tmp.unlink()

    def tearDown(self) -> None:
        if self._tmp.exists():
            self._tmp.unlink()

    def _with_state_file(self) -> mock.patch:
        return mock.patch.object(poller, "STATE_FILE", str(self._tmp))

    def test_roundtrip_preserves_timestamp_and_gids(self) -> None:
        gids = {"gid-1": None, "gid-2": None, "gid-3": None}
        ts = "2026-06-14T12:00:00+00:00"

        with self._with_state_file():
            poller._save_state(ts, gids)
            loaded_ts, loaded_gids = poller._load_state()

        self.assertEqual(loaded_ts, ts)
        self.assertEqual(set(loaded_gids.keys()), {"gid-1", "gid-2", "gid-3"})

    def test_missing_state_file_returns_fallback_and_empty(self) -> None:
        with self._with_state_file():
            ts, gids = poller._load_state()

        self.assertFalse(self._tmp.exists() and json.loads(self._tmp.read_text()).get("processed_story_gids"))
        self.assertEqual(gids, {})
        self.assertIn("T", ts)  # ISO timestamp

    def test_corrupt_state_file_resets_gracefully(self) -> None:
        self._tmp.write_text("NOT JSON {{{{")
        with self._with_state_file():
            ts, gids = poller._load_state()
        self.assertEqual(gids, {})


# ── Truncate Recency Order ────────────────────────────────────────────────────

class TestSaveStateTruncation(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path("/tmp/lena-test-truncate.state")

    def tearDown(self) -> None:
        if self._tmp.exists():
            self._tmp.unlink()

    def _with_state_file(self) -> mock.patch:
        return mock.patch.object(poller, "STATE_FILE", str(self._tmp))

    def test_truncate_keeps_most_recent(self) -> None:
        """_save_state must drop the oldest GIDs, not arbitrary ones."""
        cap = poller.MAX_PROCESSED_GIDS
        # Build ordered dict: gid-0 is oldest, gid-{cap} is newest
        gids: dict[str, None] = {f"gid-{i}": None for i in range(cap + 10)}

        with self._with_state_file():
            poller._save_state("2026-01-01T00:00:00+00:00", gids)
            saved = json.loads(self._tmp.read_text())["processed_story_gids"]

        self.assertEqual(len(saved), cap)
        # Oldest 10 must be gone
        for i in range(10):
            self.assertNotIn(f"gid-{i}", saved, f"gid-{i} should have been evicted")
        # Newest cap entries must be present
        for i in range(10, cap + 10):
            self.assertIn(f"gid-{i}", saved, f"gid-{i} should be retained")

    def test_no_truncation_below_cap(self) -> None:
        cap = poller.MAX_PROCESSED_GIDS
        gids: dict[str, None] = {f"gid-{i}": None for i in range(cap)}

        with self._with_state_file():
            poller._save_state("2026-01-01T00:00:00+00:00", gids)
            saved = json.loads(self._tmp.read_text())["processed_story_gids"]

        self.assertEqual(len(saved), cap)

    def test_50k_overflow_scenario(self) -> None:
        """Simulates 50k+1 GIDs; only the newest 50k survive."""
        overflow_count = poller.MAX_PROCESSED_GIDS + 1
        gids: dict[str, None] = {f"story-{i}": None for i in range(overflow_count)}
        oldest_gid = "story-0"
        newest_gid = f"story-{overflow_count - 1}"

        with mock.patch.object(poller, "STATE_FILE", str(self._tmp)):
            poller._save_state("2026-01-01T00:00:00+00:00", gids)
            saved_set = set(json.loads(self._tmp.read_text())["processed_story_gids"])

        self.assertNotIn(oldest_gid, saved_set, "oldest GID must be evicted on overflow")
        self.assertIn(newest_gid, saved_set, "newest GID must be retained on overflow")
        self.assertEqual(len(saved_set), poller.MAX_PROCESSED_GIDS)


# ── Workspace GID Discovery ───────────────────────────────────────────────────

class TestGetWorkspaceGid(unittest.TestCase):
    def test_env_override_skips_api(self) -> None:
        with mock.patch.object(poller, "WORKSPACE_GID_ENV", "env-ws-gid"):
            gid = poller._get_workspace_gid()
        self.assertEqual(gid, "env-ws-gid")

    def test_auto_discovery_calls_api(self) -> None:
        fake_resp = [{"gid": "discovered-gid", "name": "HBE Workspace"}]
        with mock.patch.object(poller, "WORKSPACE_GID_ENV", ""), \
                mock.patch.object(poller, "_asana_get", return_value=fake_resp) as mock_get:
            gid = poller._get_workspace_gid()

        mock_get.assert_called_once()
        self.assertEqual(gid, "discovered-gid")

    def test_auto_discovery_raises_on_empty_workspaces(self) -> None:
        with mock.patch.object(poller, "WORKSPACE_GID_ENV", ""), \
                mock.patch.object(poller, "_asana_get", return_value=[]):
            with self.assertRaises(RuntimeError, msg="should raise when no workspaces returned"):
                poller._get_workspace_gid()


# ── Mention / Assignment Filter ───────────────────────────────────────────────

class TestStoryFilters(unittest.TestCase):
    def _comment(self, text: str) -> dict:
        return {"resource_subtype": "comment_added", "text": text}

    def _system(self, subtype: str, text: str) -> dict:
        return {"resource_subtype": subtype, "text": text}

    # _is_mention_story
    def test_mention_detected_lowercase(self) -> None:
        self.assertTrue(poller._is_mention_story(self._comment("Bitte @lena prüfen")))

    def test_mention_detected_uppercase(self) -> None:
        self.assertTrue(poller._is_mention_story(self._comment("Hey @Lena, schau mal")))

    def test_no_mention_in_comment(self) -> None:
        self.assertFalse(poller._is_mention_story(self._comment("Kein Mention hier")))

    def test_non_comment_story_not_a_mention(self) -> None:
        story = {"resource_subtype": "assigned", "text": "@lena assigned"}
        self.assertFalse(poller._is_mention_story(story))

    def test_mention_with_word_boundary(self) -> None:
        # @lena-foo should not match \blena\b — the regex uses \b after lena
        self.assertFalse(poller._is_mention_story(self._comment("@lenaherbert please check")))

    # _is_assignment_story
    def test_assignment_detected(self) -> None:
        story = self._system("assigned", "assigned to Lena Herbert")
        self.assertTrue(poller._is_assignment_story(story))

    def test_assignment_case_insensitive(self) -> None:
        story = self._system("assigned", "Zugewiesen an LENA")
        self.assertTrue(poller._is_assignment_story(story))

    def test_non_assigned_subtype_not_assignment(self) -> None:
        story = self._system("comment_added", "assigned to Lena")
        self.assertFalse(poller._is_assignment_story(story))

    def test_assigned_without_lena_not_assignment(self) -> None:
        story = self._system("assigned", "assigned to Max Mustermann")
        self.assertFalse(poller._is_assignment_story(story))


if __name__ == "__main__":
    unittest.main()
