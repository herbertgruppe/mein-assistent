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
"""
import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]

_BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_WEBHOOK_SECRET": "",
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

    def _make_request(self, **kwargs):
        defaults = {"chat_id": "111222333", "text": "Hallo Sven"}
        defaults.update(kwargs)
        return self.api.LenaTelegramSendRequest(**defaults)

    def test_returns_success_and_msg_id_on_happy_path(self):
        req = self._make_request()
        with mock.patch.object(self.api, "_TG_BOT_TOKEN", "fake-token"), \
             mock.patch.object(self.api, "_tg_send_message", return_value=55) as m_send, \
             mock.patch.object(self.api, "_telegram_db") as mock_db:
            mock_db.return_value.__enter__ = mock.MagicMock(return_value=mock.MagicMock())
            mock_db.return_value.__exit__ = mock.MagicMock(return_value=False)
            resp = self.api.lena_telegram_send(req, _key="test-key")
        self.assertTrue(resp.success)
        self.assertEqual(resp.telegram_msg_id, 55)
        m_send.assert_called_once_with("111222333", "Hallo Sven", parse_mode="MarkdownV2")

    def test_stores_outbound_message_when_issue_id_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "telegram.db"
            with mock.patch.object(self.api, "_TG_BOT_TOKEN", "fake-token"), \
                 mock.patch.object(self.api, "_TELEGRAM_DB_PATH", db_path), \
                 mock.patch.object(self.api, "_tg_send_message", return_value=77):
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
            with mock.patch.object(self.api, "_TG_BOT_TOKEN", "fake-token"), \
                 mock.patch.object(self.api, "_TELEGRAM_DB_PATH", db_path), \
                 mock.patch.object(self.api, "_tg_send_message", return_value=88):
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
            with mock.patch.object(self.api, "_TG_BOT_TOKEN", "fake-token"), \
                 mock.patch.object(self.api, "_TELEGRAM_DB_PATH", db_path), \
                 mock.patch.object(self.api, "_tg_send_message", return_value=99):
                req = self._make_request(issue_id="HBE-999")  # comment_id omitted
                self.api.lena_telegram_send(req, _key="test-key")

            conn = sqlite3.connect(str(db_path))
            row = conn.execute("SELECT comment_id FROM outbound_messages WHERE telegram_msg_id = 99").fetchone()
            conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], "", "comment_id must be '' not None (NOT NULL constraint)")

    def test_returns_success_false_when_telegram_fails(self):
        with mock.patch.object(self.api, "_TG_BOT_TOKEN", "fake-token"), \
             mock.patch.object(self.api, "_tg_send_message", return_value=None):
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

    def _send(self, msg_id=1):
        with mock.patch.object(self.api, "_TG_BOT_TOKEN", "fake-token"), \
             mock.patch.object(self.api, "_TELEGRAM_DB_PATH", Path(tempfile.mkdtemp()) / "t.db"), \
             mock.patch.object(self.api, "_tg_send_message", return_value=msg_id):
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


if __name__ == "__main__":
    unittest.main()
