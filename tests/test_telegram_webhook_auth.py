"""
Tests for the Telegram webhook auth-bypass fix (HBE-417).

Covers:
- Startup guard: RuntimeError when TELEGRAM_BOT_TOKEN is set without TELEGRAM_WEBHOOK_SECRET
- Webhook handler: rejects ({"ok": True}, no issue created) when _TG_WEBHOOK_SECRET is empty
"""
import asyncio
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_api_module(module_name: str, env_overrides: dict):
    """Load api.py under a unique module name so module-level code re-runs
    with the given env overrides (without polluting sys.modules['api'])."""
    api_path = REPO_ROOT / "api.py"
    spec = importlib.util.spec_from_file_location(module_name, api_path)
    module = importlib.util.module_from_spec(spec)
    # Pre-set env vars before load_dotenv() runs inside the module so
    # dotenv's non-override behavior leaves our values intact.
    with mock.patch.dict(os.environ, env_overrides, clear=False):
        spec.loader.exec_module(module)
    return module


class TelegramStartupGuardTest(unittest.TestCase):
    def test_raises_runtime_error_when_token_set_but_secret_missing(self):
        """App must refuse to start when TELEGRAM_BOT_TOKEN is configured but
        TELEGRAM_WEBHOOK_SECRET is empty — preventing the auth-bypass entirely."""
        env = {
            "TELEGRAM_BOT_TOKEN": "123456:AAFakeToken",
            "TELEGRAM_WEBHOOK_SECRET": "",
            "API_SECRET_KEY": "test-key",
        }
        with self.assertRaises(RuntimeError) as ctx:
            _load_api_module("api_startup_guard_test", env)
        self.assertIn("TELEGRAM_WEBHOOK_SECRET", str(ctx.exception))


class TelegramWebhookHandlerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = {
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_WEBHOOK_SECRET": "",
            "API_SECRET_KEY": "test-key",
        }
        cls.api = _load_api_module("api_webhook_handler_test", env)

    def test_webhook_rejects_when_secret_not_configured(self):
        """Handler must return {"ok": True} without creating a Paperclip issue
        when _TG_WEBHOOK_SECRET is empty — closing the auth-bypass window."""
        mock_create_issue = mock.MagicMock(return_value=None)

        class _MockRequest:
            headers = {}

            async def json(self):
                return {}

        with mock.patch.object(self.api, "_TG_WEBHOOK_SECRET", ""), \
             mock.patch.object(self.api, "_pc_create_issue", mock_create_issue):
            result = asyncio.run(
                self.api.telegram_lena_webhook(_MockRequest())
            )

        self.assertEqual(result, {"ok": True})
        mock_create_issue.assert_not_called()

    def test_webhook_rejects_wrong_secret(self):
        mock_create_issue = mock.MagicMock(return_value=None)

        class _MockRequest:
            client = None
            headers = {"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"}

            async def json(self):
                return {}

        with mock.patch.object(self.api, "_TG_WEBHOOK_SECRET", "correct-secret"), \
             mock.patch.object(self.api, "_pc_create_issue", mock_create_issue):
            result = asyncio.run(self.api.telegram_lena_webhook(_MockRequest()))
        self.assertEqual(result, {"ok": True})
        mock_create_issue.assert_not_called()

    def test_webhook_rejects_missing_header(self):
        mock_create_issue = mock.MagicMock(return_value=None)

        class _MockRequest:
            client = None
            headers = {}

            async def json(self):
                return {}

        with mock.patch.object(self.api, "_TG_WEBHOOK_SECRET", "correct-secret"), \
             mock.patch.object(self.api, "_pc_create_issue", mock_create_issue):
            result = asyncio.run(self.api.telegram_lena_webhook(_MockRequest()))
        self.assertEqual(result, {"ok": True})
        mock_create_issue.assert_not_called()

    def test_webhook_logs_warning_on_wrong_secret(self):
        class _MockRequest:
            client = None
            headers = {"X-Telegram-Bot-Api-Secret-Token": "wrong"}

            async def json(self):
                return {}

        with mock.patch.object(self.api, "_TG_WEBHOOK_SECRET", "correct"), \
             self.assertLogs(self.api.__name__, level="WARNING") as cm:
            asyncio.run(self.api.telegram_lena_webhook(_MockRequest()))
        self.assertTrue(any("auth failure" in m for m in cm.output))

    def test_webhook_accepts_correct_secret(self):
        mock_create_issue = mock.MagicMock(return_value="issue-id-123")
        mock_tg_db = mock.MagicMock()
        mock_tg_db.__enter__ = mock.MagicMock(return_value=mock_tg_db)
        mock_tg_db.__exit__ = mock.MagicMock(return_value=False)
        mock_tg_db.execute = mock.MagicMock()

        class _MockRequest:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "correct-secret"}

            async def json(self):
                return {
                    "update_id": 1,
                    "message": {
                        "message_id": 42,
                        "chat": {"id": 12345},
                        "text": "Hallo Lena",
                        "from": {"id": 1, "is_bot": False, "first_name": "Sven"},
                        "date": 1700000000,
                    },
                }

        with mock.patch.object(self.api, "_TG_WEBHOOK_SECRET", "correct-secret"), \
             mock.patch.object(self.api, "_pc_create_issue", mock_create_issue), \
             mock.patch.object(self.api, "_telegram_db", return_value=mock_tg_db):
            result = asyncio.run(self.api.telegram_lena_webhook(_MockRequest()))
        self.assertEqual(result, {"ok": True})
        mock_create_issue.assert_called_once()


class TelegramInReviewResetTest(unittest.TestCase):
    """Regression test for HBE-794: user message on in_review issue resets to in_progress."""

    @classmethod
    def setUpClass(cls):
        env = {
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_WEBHOOK_SECRET": "",
            "API_SECRET_KEY": "test-key",
        }
        cls.api = _load_api_module("api_in_review_reset_test", env)

    def _make_request(self):
        class _Req:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "correct-secret"}

            async def json(self):
                return {
                    "update_id": 2,
                    "message": {
                        "message_id": 99,
                        "chat": {"id": 77777},
                        "text": "Werr@obw.de",
                        "from": {"id": 1, "is_bot": False, "first_name": "Sven"},
                        "date": 1700001000,
                    },
                }

        return _Req()

    def _make_db(self, issue_id):
        """Return a mock _telegram_db that has one pending issue."""
        row = mock.MagicMock()
        row.__getitem__ = mock.MagicMock(side_effect=lambda k: issue_id if k == "issue_id" else None)

        db = mock.MagicMock()
        db.__enter__ = mock.MagicMock(return_value=db)
        db.__exit__ = mock.MagicMock(return_value=False)
        db.execute = mock.MagicMock(return_value=mock.MagicMock(fetchall=mock.MagicMock(return_value=[row])))
        return db

    def test_in_review_issue_gets_reset_to_in_progress(self):
        issue_id = "hbe-786-test"
        mock_db = self._make_db(issue_id)
        mock_get_info = mock.MagicMock(return_value=("in_review", "lena-agent-id"))
        mock_set_status = mock.MagicMock(return_value=True)
        mock_add_comment = mock.MagicMock(return_value=True)

        with mock.patch.object(self.api, "_TG_WEBHOOK_SECRET", "correct-secret"), \
             mock.patch.object(self.api, "_PC_LENA_AGENT_ID", "lena-agent-id"), \
             mock.patch.object(self.api, "_telegram_db", return_value=mock_db), \
             mock.patch.object(self.api, "_pc_get_issue_info", mock_get_info), \
             mock.patch.object(self.api, "_pc_patch_issue_status", mock_set_status), \
             mock.patch.object(self.api, "_pc_add_comment_to_issue", mock_add_comment):
            result = asyncio.run(self.api.telegram_lena_webhook(self._make_request()))

        self.assertEqual(result, {"ok": True})
        mock_set_status.assert_called_once_with(issue_id, "in_progress")
        mock_add_comment.assert_called_once()

    def test_non_in_review_issue_is_not_reset(self):
        issue_id = "hbe-some-other"
        mock_db = self._make_db(issue_id)
        mock_get_info = mock.MagicMock(return_value=("in_progress", "lena-agent-id"))
        mock_set_status = mock.MagicMock(return_value=True)
        mock_add_comment = mock.MagicMock(return_value=True)

        with mock.patch.object(self.api, "_TG_WEBHOOK_SECRET", "correct-secret"), \
             mock.patch.object(self.api, "_PC_LENA_AGENT_ID", "lena-agent-id"), \
             mock.patch.object(self.api, "_telegram_db", return_value=mock_db), \
             mock.patch.object(self.api, "_pc_get_issue_info", mock_get_info), \
             mock.patch.object(self.api, "_pc_patch_issue_status", mock_set_status), \
             mock.patch.object(self.api, "_pc_add_comment_to_issue", mock_add_comment):
            result = asyncio.run(self.api.telegram_lena_webhook(self._make_request()))

        self.assertEqual(result, {"ok": True})
        mock_set_status.assert_not_called()
        mock_add_comment.assert_called_once()


class TelegramReplyThreadingTest(unittest.TestCase):
    """Tests for HBE-1026: Sven-Replies als Quote-prefixed Comments an Lena."""

    @classmethod
    def setUpClass(cls):
        env = {
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_WEBHOOK_SECRET": "",
            "API_SECRET_KEY": "test-key",
        }
        cls.api = _load_api_module("api_reply_threading_test", env)

    def _make_db_with_pending_issue(self, issue_id, outbound_row=None):
        """Mock _telegram_db with one pending issue and optional outbound_messages row."""
        pending_row = mock.MagicMock()
        pending_row.__getitem__ = mock.MagicMock(
            side_effect=lambda k: issue_id if k == "issue_id" else None
        )

        def _execute(sql, params=()):
            result = mock.MagicMock()
            if "pending_issues" in sql and "SELECT" in sql:
                result.fetchall.return_value = [pending_row]
                result.fetchone.return_value = None
            elif "outbound_messages" in sql and "SELECT" in sql:
                result.fetchone.return_value = outbound_row
                result.fetchall.return_value = []
            else:
                result.fetchone.return_value = None
                result.fetchall.return_value = []
            return result

        db = mock.MagicMock()
        db.__enter__ = mock.MagicMock(return_value=db)
        db.__exit__ = mock.MagicMock(return_value=False)
        db.execute = mock.MagicMock(side_effect=_execute)
        return db

    def test_reply_with_mapping_produces_quote_block(self):
        """Sven replies to a Lena message that IS in outbound_messages → quote block with comment ref."""
        issue_id = "HBE-1026-test-a"
        outbound_row = mock.MagicMock()
        outbound_row.__getitem__ = mock.MagicMock(side_effect=lambda k: {
            "comment_id": "abc12345-def6-7890-gh",
            "comment_excerpt": "Bitte schick mir die Unterlagen",
        }[k])

        mock_db = self._make_db_with_pending_issue(issue_id, outbound_row=outbound_row)
        mock_get_info = mock.MagicMock(return_value=("in_progress", "lena-agent-id"))
        mock_add_comment = mock.MagicMock(return_value=True)

        class _Req:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "correct-secret"}

            async def json(self):
                return {
                    "update_id": 10,
                    "message": {
                        "message_id": 200,
                        "chat": {"id": 99999},
                        "text": "Hier sind die Unterlagen.",
                        "from": {"id": 1, "is_bot": False, "first_name": "Sven"},
                        "date": 1700002000,
                        "reply_to_message": {
                            "message_id": 150,
                            "chat": {"id": 99999},
                            "text": "Bitte schick mir die Unterlagen",
                        },
                    },
                }

        with mock.patch.object(self.api, "_TG_WEBHOOK_SECRET", "correct-secret"), \
             mock.patch.object(self.api, "_PC_LENA_AGENT_ID", "lena-agent-id"), \
             mock.patch.object(self.api, "_telegram_db", return_value=mock_db), \
             mock.patch.object(self.api, "_pc_get_issue_info", mock_get_info), \
             mock.patch.object(self.api, "_pc_add_comment_to_issue", mock_add_comment):
            result = asyncio.run(self.api.telegram_lena_webhook(_Req()))

        self.assertEqual(result, {"ok": True})
        mock_add_comment.assert_called_once()
        comment_body = mock_add_comment.call_args[0][2]
        self.assertTrue(
            comment_body.startswith("> **Re Lena [Comment abc12345,"),
            f"Expected quote-block prefix, got: {comment_body!r}",
        )
        self.assertIn("Bitte schick mir die Unterlagen", comment_body)
        self.assertIn("Hier sind die Unterlagen.", comment_body)

    def test_reply_without_mapping_uses_raw_text(self):
        """Sven replies to a message NOT in outbound_messages → best-effort raw quote."""
        issue_id = "HBE-1026-test-b"
        mock_db = self._make_db_with_pending_issue(issue_id, outbound_row=None)
        mock_get_info = mock.MagicMock(return_value=("in_progress", "lena-agent-id"))
        mock_add_comment = mock.MagicMock(return_value=True)

        class _Req:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "correct-secret"}

            async def json(self):
                return {
                    "update_id": 11,
                    "message": {
                        "message_id": 201,
                        "chat": {"id": 99999},
                        "text": "Okay, verstanden.",
                        "from": {"id": 1, "is_bot": False, "first_name": "Sven"},
                        "date": 1700002100,
                        "reply_to_message": {
                            "message_id": 100,
                            "chat": {"id": 99999},
                            "text": "Eine alte Nachricht vor der Migration",
                        },
                    },
                }

        with mock.patch.object(self.api, "_TG_WEBHOOK_SECRET", "correct-secret"), \
             mock.patch.object(self.api, "_PC_LENA_AGENT_ID", "lena-agent-id"), \
             mock.patch.object(self.api, "_telegram_db", return_value=mock_db), \
             mock.patch.object(self.api, "_pc_get_issue_info", mock_get_info), \
             mock.patch.object(self.api, "_pc_add_comment_to_issue", mock_add_comment):
            result = asyncio.run(self.api.telegram_lena_webhook(_Req()))

        self.assertEqual(result, {"ok": True})
        mock_add_comment.assert_called_once()
        comment_body = mock_add_comment.call_args[0][2]
        self.assertTrue(
            comment_body.startswith('> **Re:** „Eine alte Nachricht'),
            f"Expected raw-quote prefix, got: {comment_body!r}",
        )
        self.assertIn("Okay, verstanden.", comment_body)

    def test_reply_with_mapping_uses_lena_send_timestamp(self):
        """Quote-Block enthält die UTC-Zeit aus reply_to_message.date (Lena-Sendezeit)."""
        issue_id = "HBE-1026-test-d"
        outbound_row = mock.MagicMock()
        outbound_row.__getitem__ = mock.MagicMock(side_effect=lambda k: {
            "comment_id": "deadbeef-1234-5678-ab",
            "comment_excerpt": "Wann ist der Termin?",
        }[k])

        mock_db = self._make_db_with_pending_issue(issue_id, outbound_row=outbound_row)
        mock_get_info = mock.MagicMock(return_value=("in_progress", "lena-agent-id"))
        mock_add_comment = mock.MagicMock(return_value=True)

        # Unix timestamp for 2026-06-18 14:30:00 UTC
        lena_ts = 1750257000

        class _Req:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "correct-secret"}

            async def json(self):
                return {
                    "update_id": 20,
                    "message": {
                        "message_id": 300,
                        "chat": {"id": 88888},
                        "text": "Morgen um 10 Uhr.",
                        "from": {"id": 1, "is_bot": False, "first_name": "Sven"},
                        "date": 1750257060,
                        "reply_to_message": {
                            "message_id": 250,
                            "chat": {"id": 88888},
                            "text": "Wann ist der Termin?",
                            "date": lena_ts,
                        },
                    },
                }

        with mock.patch.object(self.api, "_TG_WEBHOOK_SECRET", "correct-secret"), \
             mock.patch.object(self.api, "_PC_LENA_AGENT_ID", "lena-agent-id"), \
             mock.patch.object(self.api, "_telegram_db", return_value=mock_db), \
             mock.patch.object(self.api, "_pc_get_issue_info", mock_get_info), \
             mock.patch.object(self.api, "_pc_add_comment_to_issue", mock_add_comment):
            result = asyncio.run(self.api.telegram_lena_webhook(_Req()))

        self.assertEqual(result, {"ok": True})
        comment_body = mock_add_comment.call_args[0][2]
        # 14:30 UTC expected for lena_ts = 1750257000
        self.assertIn("14:30 UTC", comment_body, f"Expected UTC time in quote, got: {comment_body!r}")
        self.assertIn("deadbeef", comment_body)

    def test_normal_message_no_reply_unchanged(self):
        """Regression: normal Sven message without reply_to_message → no quote block."""
        issue_id = "HBE-1026-test-c"
        mock_db = self._make_db_with_pending_issue(issue_id)
        mock_get_info = mock.MagicMock(return_value=("in_progress", "lena-agent-id"))
        mock_add_comment = mock.MagicMock(return_value=True)

        class _Req:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "correct-secret"}

            async def json(self):
                return {
                    "update_id": 12,
                    "message": {
                        "message_id": 202,
                        "chat": {"id": 99999},
                        "text": "Normale Nachricht ohne Reply",
                        "from": {"id": 1, "is_bot": False, "first_name": "Sven"},
                        "date": 1700002200,
                    },
                }

        with mock.patch.object(self.api, "_TG_WEBHOOK_SECRET", "correct-secret"), \
             mock.patch.object(self.api, "_PC_LENA_AGENT_ID", "lena-agent-id"), \
             mock.patch.object(self.api, "_telegram_db", return_value=mock_db), \
             mock.patch.object(self.api, "_pc_get_issue_info", mock_get_info), \
             mock.patch.object(self.api, "_pc_add_comment_to_issue", mock_add_comment):
            result = asyncio.run(self.api.telegram_lena_webhook(_Req()))

        self.assertEqual(result, {"ok": True})
        mock_add_comment.assert_called_once()
        comment_body = mock_add_comment.call_args[0][2]
        self.assertEqual(comment_body, "Normale Nachricht ohne Reply")


if __name__ == "__main__":
    unittest.main()
