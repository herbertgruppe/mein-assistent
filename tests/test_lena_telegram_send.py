"""
Tests for HBE-1091/HBE-1212: POST /api/lena/telegram/send — Telegram-Sendung + Flood-Schutz.

Covers:
- lena_telegram_send sends message and returns telegram_msg_id on success
- lena_telegram_send stores row in outbound_messages when issue_id is provided
- lena_telegram_send stores row in outbound_messages even WITHOUT issue_id (HBE-1212)
- lena_telegram_send uses empty string for comment_id when not provided (NOT NULL constraint)
- lena_telegram_send returns success=False without raising when Telegram fails
- _tg_send_message passes parse_mode to Telegram API payload
- Rate limiter returns HTTP 429 after 10 calls/min (HBE-1212)
- Alert-send outside lock: parallel rate-check calls are not blocked by slow Telegram I/O (HBE-1323)
"""
import importlib.util
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]

_BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": "test-lena-token",
    "TELEGRAM_WEBHOOK_SECRET": "test-lena-secret",
    "API_SECRET_KEY": "test-key",
}


def _load_api(module_name: str, env: dict = _BASE_ENV):
    api_path = REPO_ROOT / "api.py"
    spec = importlib.util.spec_from_file_location(module_name, api_path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, env, clear=False):
        spec.loader.exec_module(module)
    return module


class LenaTelegramSendEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = _load_api("api_lena_tg_send_test")

    def _lena_cfg(self):
        return self.api._TELEGRAM_AGENTS["lena"]

    def _make_request(self, **kwargs):
        defaults = {"chat_id": "111222333", "text": "Hallo Sven"}
        defaults.update(kwargs)
        return self.api.LenaTelegramSendRequest(**defaults)

    def test_returns_success_and_msg_id_on_happy_path(self):
        req = self._make_request()
        mock_db = mock.MagicMock()
        mock_db.__enter__ = mock.MagicMock(return_value=mock_db)
        mock_db.__exit__ = mock.MagicMock(return_value=False)
        with mock.patch.object(self.api, "_tg_agent_send", return_value=55) as m_send, \
             mock.patch.object(self.api, "_tg_agent_db", return_value=mock_db):
            resp = self.api.lena_telegram_send(req, _key="test-key")
        self.assertTrue(resp.success)
        self.assertEqual(resp.telegram_msg_id, 55)
        # _tg_agent_send(token, chat_id, text, reply_markup=..., parse_mode=...)
        call_args = m_send.call_args
        self.assertEqual(call_args[0][1], "111222333")
        self.assertEqual(call_args[0][2], "Hallo Sven")

    def test_stores_outbound_message_when_issue_id_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "telegram.db"
            with mock.patch.object(self._lena_cfg(), "db_path", db_path), \
                 mock.patch.object(self.api, "_tg_agent_send", return_value=77):
                req = self._make_request(issue_id="HBE-999", comment_id="cmt-abc")
                self.api.lena_telegram_send(req, _key="test-key")

            conn = sqlite3.connect(str(db_path))
            row = conn.execute("SELECT * FROM outbound_messages WHERE telegram_msg_id = 77").fetchone()
            conn.close()

        self.assertIsNotNone(row, "Row must be inserted in outbound_messages")
        # columns: telegram_msg_id, chat_id, issue_id, comment_id, comment_excerpt, sent_at
        self.assertEqual(row[0], 77)
        self.assertEqual(row[2], "HBE-999")
        self.assertEqual(row[3], "cmt-abc")
        self.assertEqual(row[4], "Hallo Sven")

    def test_db_insert_even_without_issue_id(self):
        """HBE-1212: every send is tracked in outbound_messages for retroactive flood analysis."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "telegram.db"
            with mock.patch.object(self._lena_cfg(), "db_path", db_path), \
                 mock.patch.object(self.api, "_tg_agent_send", return_value=88):
                req = self._make_request()  # no issue_id
                self.api.lena_telegram_send(req, _key="test-key")

            conn = sqlite3.connect(str(db_path))
            row = conn.execute("SELECT issue_id FROM outbound_messages WHERE telegram_msg_id = 88").fetchone()
            conn.close()
            self.assertIsNotNone(row, "Row must be inserted even without issue_id (HBE-1212)")
            self.assertEqual(row[0], "", "issue_id must be '' (empty string) when not provided")

    def test_comment_id_defaults_to_empty_string(self):
        """comment_id=None must be stored as '' to satisfy NOT NULL constraint."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "telegram.db"
            with mock.patch.object(self._lena_cfg(), "db_path", db_path), \
                 mock.patch.object(self.api, "_tg_agent_send", return_value=99):
                req = self._make_request(issue_id="HBE-999")  # comment_id omitted
                self.api.lena_telegram_send(req, _key="test-key")

            conn = sqlite3.connect(str(db_path))
            row = conn.execute("SELECT comment_id FROM outbound_messages WHERE telegram_msg_id = 99").fetchone()
            conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], "", "comment_id must be '' not None (NOT NULL constraint)")

    def test_returns_success_false_when_telegram_fails(self):
        with mock.patch.object(self.api, "_tg_agent_send", return_value=None), \
             mock.patch.object(self.api, "_tg_agent_db") as mock_db_factory:
            mock_db_factory.return_value.__enter__ = mock.MagicMock(return_value=mock.MagicMock())
            mock_db_factory.return_value.__exit__ = mock.MagicMock(return_value=False)
            req = self._make_request(issue_id="HBE-999")
            resp = self.api.lena_telegram_send(req, _key="test-key")
        self.assertFalse(resp.success)
        self.assertIsNone(resp.telegram_msg_id)


class TgSendMessageParseModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = _load_api("api_lena_tg_parsemode_test")

    def test_parse_mode_included_in_payload(self):
        mock_resp = mock.MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"result": {"message_id": 10}}
        with mock.patch.object(self.api, "_TG_BOT_TOKEN", "fake-token"), \
             mock.patch.object(self.api._http, "post", return_value=mock_resp) as m_post:
            self.api._tg_send_message("123", "hi", parse_mode="MarkdownV2")
        payload = m_post.call_args[1]["json"]
        self.assertEqual(payload.get("parse_mode"), "MarkdownV2")

    def test_parse_mode_omitted_when_none(self):
        mock_resp = mock.MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"result": {"message_id": 11}}
        with mock.patch.object(self.api, "_TG_BOT_TOKEN", "fake-token"), \
             mock.patch.object(self.api._http, "post", return_value=mock_resp) as m_post:
            self.api._tg_send_message("123", "hi")
        payload = m_post.call_args[1]["json"]
        self.assertNotIn("parse_mode", payload)


class TgRateLimiterTest(unittest.TestCase):
    """HBE-1212: rate limiter blocks > 10 lena/telegram/send calls per minute."""

    def setUp(self):
        # Load a fresh module instance so rate-limiter state is reset between tests
        self.api = _load_api("api_ratelimit_test")
        # Override rate limit to a small number for fast testing
        self.api._TG_RATE_LIMIT = 3
        # Clear the buckets
        self.api._TG_RATE_BUCKETS.clear()

    def _lena_cfg(self):
        return self.api._TELEGRAM_AGENTS["lena"]

    def _send(self, msg_id=1):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "t.db"
            with mock.patch.object(self._lena_cfg(), "db_path", db_path), \
                 mock.patch.object(self.api, "_tg_agent_send", return_value=msg_id):
                req = self.api.LenaTelegramSendRequest(chat_id="111", text="x")
                return self.api.lena_telegram_send(req, _key="test-key")

    def test_first_calls_succeed(self):
        for _ in range(3):
            resp = self._send()
            self.assertTrue(resp.success)

    def test_429_after_limit(self):
        from fastapi import HTTPException as FHE
        for _ in range(3):
            self._send()
        with self.assertRaises(FHE) as ctx:
            self._send()
        self.assertEqual(ctx.exception.status_code, 429)

    def test_mara_bucket_independent_from_lena(self):
        """Mara and Lena use separate rate-limit buckets."""
        self.api._TG_RATE_BUCKETS.clear()
        # Fill lena bucket
        for _ in range(3):
            self._send()
        # Mara bucket should still be empty — _tg_rate_check("mara") must return True
        result = self.api._tg_rate_check("mara")
        self.assertTrue(result)


class TgRateLockNonBlockingTest(unittest.TestCase):
    """HBE-1323: Alert-send must not block concurrent _tg_rate_check calls.

    _tg_send_message fires OUTSIDE _TG_RATE_LOCK, so a slow Telegram API during
    an alert must not serialise other rate-check callers.
    """

    ALERT_SLEEP = 0.3  # seconds the monkey-patched _tg_send_message sleeps

    def setUp(self):
        self.api = _load_api("api_nonblocking_test")
        self.api._TG_RATE_LIMIT = 2
        self.api._TG_RATE_BUCKETS.clear()
        self.api._TG_RATE_LAST_ALERT.clear()
        # Set admin chat ID so should_alert evaluates to True on first breach
        self.api._TG_ADMIN_CHAT_ID = "999"

    def test_concurrent_rate_check_not_blocked_by_slow_alert(self):
        # Fill the bucket to exactly the limit so the next call triggers a breach + alert
        for _ in range(self.api._TG_RATE_LIMIT):
            self.api._TG_RATE_BUCKETS["lena"].append(self.api._time.monotonic())

        alert_started = threading.Event()
        second_check_done = threading.Event()
        second_check_elapsed = []

        def slow_send(chat_id, text, **kwargs):
            alert_started.set()
            time.sleep(self.ALERT_SLEEP)

        def run_alerting_check():
            with mock.patch.object(self.api, "_tg_send_message", side_effect=slow_send):
                self.api._tg_rate_check("lena")

        def run_second_check():
            # Wait until the first check has started its slow I/O, then probe
            alert_started.wait(timeout=1.0)
            t0 = time.monotonic()
            self.api._tg_rate_check("lena")
            second_check_elapsed.append(time.monotonic() - t0)
            second_check_done.set()

        t1 = threading.Thread(target=run_alerting_check, daemon=True)
        t2 = threading.Thread(target=run_second_check, daemon=True)
        t1.start()
        t2.start()

        second_check_done.wait(timeout=2.0)
        t1.join(timeout=self.ALERT_SLEEP + 0.5)

        self.assertTrue(second_check_done.is_set(), "Second rate-check never completed")
        elapsed = second_check_elapsed[0]
        # The second check must complete well below the alert sleep duration —
        # if it were blocked inside the lock it would take >= ALERT_SLEEP seconds
        self.assertLess(
            elapsed,
            self.ALERT_SLEEP * 0.5,
            f"Second _tg_rate_check took {elapsed:.3f}s — likely blocked by alert I/O inside lock",
        )


if __name__ == "__main__":
    unittest.main()
