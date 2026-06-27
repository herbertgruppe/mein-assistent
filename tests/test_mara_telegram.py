"""
Tests for HBE-1205: Mara-eigener Telegram-Bot — Webhook-Auth, Issue-Routing,
Reply-Threading, Outbound-Tracking.

Covers:
- Startup guard: RuntimeError when TELEGRAM_MARA_BOT_TOKEN is set without TELEGRAM_MARA_WEBHOOK_SECRET
- Webhook-Auth: correct secret_token -> {"ok": True} + issue created; wrong/missing -> reject
- Issue-Routing: new message lands on Mara-Agent (ed26f194-...), NOT Lena (_pc_create_issue)
- Reply-Threading: quote block uses "Re Mara [...]" and reads from telegram_mara.db, not telegram.db
- Outbound-Tracking: mara_telegram_send without token -> 503; with token -> Telegram API + DB write
"""
import asyncio
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
    "TELEGRAM_MARA_BOT_TOKEN": "",
    "TELEGRAM_MARA_WEBHOOK_SECRET": "",
    "API_SECRET_KEY": "test-key",
}

_MARA_AGENT_ID = "ed26f194-f0a9-4f70-a52d-6e39be9013e3"


def _load_api(module_name: str, env: dict = None):
    env = env if env is not None else _BASE_ENV
    api_path = REPO_ROOT / "api.py"
    spec = importlib.util.spec_from_file_location(module_name, api_path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, env, clear=False):
        spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Startup Guard
# ---------------------------------------------------------------------------
class MaraStartupGuardTest(unittest.TestCase):
    def test_raises_when_mara_token_set_but_no_webhook_secret(self):
        """App must refuse to start when TELEGRAM_MARA_BOT_TOKEN is set but TELEGRAM_MARA_WEBHOOK_SECRET is empty."""
        env = {
            **_BASE_ENV,
            "TELEGRAM_MARA_BOT_TOKEN": "fake-mara-token",
            "TELEGRAM_MARA_WEBHOOK_SECRET": "",
        }
        with self.assertRaises(RuntimeError) as ctx:
            _load_api("api_mara_startup_guard_test", env)
        self.assertIn("TELEGRAM_MARA_WEBHOOK_SECRET", str(ctx.exception))

    def test_no_error_when_neither_mara_token_nor_secret(self):
        """Normal import without Mara bot configured must not raise."""
        _load_api("api_mara_startup_guard_ok_test")


# ---------------------------------------------------------------------------
# Webhook Auth
# ---------------------------------------------------------------------------
class MaraWebhookAuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = _load_api("api_mara_webhook_auth_test")

    def test_rejects_when_mara_secret_not_configured(self):
        """Handler returns {"ok": True} without creating issue when _TG_MARA_WEBHOOK_SECRET is empty."""
        mock_create = mock.MagicMock(return_value=None)

        class _Req:
            headers = {}

            async def json(self):
                return {}

        with mock.patch.object(self.api, "_TG_MARA_WEBHOOK_SECRET", ""), \
             mock.patch.object(self.api, "_pc_create_mara_issue", mock_create):
            result = asyncio.run(self.api.telegram_mara_webhook(_Req()))

        self.assertEqual(result, {"ok": True})
        mock_create.assert_not_called()

    def test_rejects_wrong_secret(self):
        mock_create = mock.MagicMock(return_value=None)

        class _Req:
            client = None
            headers = {"X-Telegram-Bot-Api-Secret-Token": "wrong-mara-secret"}

            async def json(self):
                return {}

        with mock.patch.object(self.api, "_TG_MARA_WEBHOOK_SECRET", "correct-mara-secret"), \
             mock.patch.object(self.api, "_pc_create_mara_issue", mock_create):
            result = asyncio.run(self.api.telegram_mara_webhook(_Req()))

        self.assertEqual(result, {"ok": True})
        mock_create.assert_not_called()

    def test_rejects_missing_header(self):
        mock_create = mock.MagicMock(return_value=None)

        class _Req:
            client = None
            headers = {}

            async def json(self):
                return {}

        with mock.patch.object(self.api, "_TG_MARA_WEBHOOK_SECRET", "correct-mara-secret"), \
             mock.patch.object(self.api, "_pc_create_mara_issue", mock_create):
            result = asyncio.run(self.api.telegram_mara_webhook(_Req()))

        self.assertEqual(result, {"ok": True})
        mock_create.assert_not_called()

    def test_logs_warning_on_wrong_secret(self):
        class _Req:
            client = None
            headers = {"X-Telegram-Bot-Api-Secret-Token": "wrong"}

            async def json(self):
                return {}

        with mock.patch.object(self.api, "_TG_MARA_WEBHOOK_SECRET", "correct"), \
             self.assertLogs(self.api.__name__, level="WARNING") as cm:
            asyncio.run(self.api.telegram_mara_webhook(_Req()))
        self.assertTrue(any("auth failure" in m or "mismatch" in m for m in cm.output))

    def test_accepts_correct_secret_and_creates_issue(self):
        mock_create = mock.MagicMock(return_value="mara-issue-id-001")
        mock_db = mock.MagicMock()
        mock_db.__enter__ = mock.MagicMock(return_value=mock_db)
        mock_db.__exit__ = mock.MagicMock(return_value=False)
        mock_db.execute = mock.MagicMock(
            return_value=mock.MagicMock(fetchall=mock.MagicMock(return_value=[]))
        )

        class _Req:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "correct-mara-secret"}

            async def json(self):
                return {
                    "update_id": 1,
                    "message": {
                        "message_id": 42,
                        "chat": {"id": 12345},
                        "text": "Hallo Mara",
                        "from": {"id": 1, "is_bot": False, "first_name": "Sven"},
                        "date": 1700000000,
                    },
                }

        with mock.patch.object(self.api, "_TG_MARA_WEBHOOK_SECRET", "correct-mara-secret"), \
             mock.patch.object(self.api, "_pc_create_mara_issue", mock_create), \
             mock.patch.object(self.api, "_telegram_mara_db", return_value=mock_db), \
             mock.patch.object(self.api, "_pc_get_issue_info",
                               mock.MagicMock(return_value=("in_progress", _MARA_AGENT_ID))):
            result = asyncio.run(self.api.telegram_mara_webhook(_Req()))

        self.assertEqual(result, {"ok": True})
        mock_create.assert_called_once()


# ---------------------------------------------------------------------------
# Issue Routing — must land on Mara, NOT Lena
# ---------------------------------------------------------------------------
class MaraIssueRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = _load_api("api_mara_routing_test")

    def _make_empty_mara_db(self):
        """Return a _telegram_mara_db mock with no pending issues."""
        def _execute(sql, params=()):
            result = mock.MagicMock()
            result.fetchall.return_value = []
            result.fetchone.return_value = None
            return result

        db = mock.MagicMock()
        db.__enter__ = mock.MagicMock(return_value=db)
        db.__exit__ = mock.MagicMock(return_value=False)
        db.execute = mock.MagicMock(side_effect=_execute)
        return db

    def test_new_message_calls_create_mara_issue_not_lena(self):
        """Incoming message to Mara bot must route to _pc_create_mara_issue, never _pc_create_issue."""
        mock_create_mara = mock.MagicMock(return_value="mara-new-issue")
        mock_create_lena = mock.MagicMock(return_value="lena-WRONG")
        mock_db = self._make_empty_mara_db()

        class _Req:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "mara-secret"}

            async def json(self):
                return {
                    "update_id": 5,
                    "message": {
                        "message_id": 10,
                        "chat": {"id": 55555},
                        "text": "Neue Mara Anfrage",
                        "from": {"id": 1, "is_bot": False, "first_name": "Sven"},
                        "date": 1700003000,
                    },
                }

        with mock.patch.object(self.api, "_TG_MARA_WEBHOOK_SECRET", "mara-secret"), \
             mock.patch.object(self.api, "_pc_create_mara_issue", mock_create_mara), \
             mock.patch.object(self.api, "_pc_create_issue", mock_create_lena), \
             mock.patch.object(self.api, "_telegram_mara_db", return_value=mock_db), \
             mock.patch.object(self.api, "_pc_get_issue_info",
                               mock.MagicMock(return_value=("in_progress", _MARA_AGENT_ID))):
            asyncio.run(self.api.telegram_mara_webhook(_Req()))

        mock_create_mara.assert_called_once()
        mock_create_lena.assert_not_called()

    def test_create_mara_issue_requests_mara_agent_id_as_assignee(self):
        """_pc_create_mara_issue must set assigneeAgentId to _PC_MARA_AGENT_ID in the Paperclip request."""
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": "mara-issue-789"}

        with mock.patch.object(self.api, "_PC_API_URL", "https://fake-pc"), \
             mock.patch.object(self.api, "_PC_API_KEY", "fake-api-key"), \
             mock.patch.object(self.api, "_PC_COMPANY_ID", "company-999"), \
             mock.patch.object(self.api, "_PC_MARA_AGENT_ID", _MARA_AGENT_ID), \
             mock.patch.object(self.api._http, "post", return_value=mock_resp) as m_post:
            result = self.api._pc_create_mara_issue("99999", 42, "sven", "Hello Mara")

        self.assertEqual(result, "mara-issue-789")
        payload = m_post.call_args[1]["json"]
        self.assertEqual(payload["assigneeAgentId"], _MARA_AGENT_ID,
                         "Mara issue must be assigned to Mara agent, not Lena")

    def test_mara_webhook_does_not_touch_lena_db(self):
        """New message on Mara webhook must use _telegram_mara_db, never _telegram_db."""
        mock_mara_db = self._make_empty_mara_db()
        mock_lena_db = mock.MagicMock()
        mock_lena_db.__enter__ = mock.MagicMock(return_value=mock_lena_db)
        mock_lena_db.__exit__ = mock.MagicMock(return_value=False)

        class _Req:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "mara-secret"}

            async def json(self):
                return {
                    "update_id": 6,
                    "message": {
                        "message_id": 11,
                        "chat": {"id": 66666},
                        "text": "DB-Isolationstest",
                        "from": {"id": 1, "is_bot": False, "first_name": "Sven"},
                        "date": 1700004000,
                    },
                }

        with mock.patch.object(self.api, "_TG_MARA_WEBHOOK_SECRET", "mara-secret"), \
             mock.patch.object(self.api, "_telegram_mara_db", return_value=mock_mara_db), \
             mock.patch.object(self.api, "_telegram_db", return_value=mock_lena_db), \
             mock.patch.object(self.api, "_pc_create_mara_issue",
                               mock.MagicMock(return_value="m-issue-db-test")), \
             mock.patch.object(self.api, "_pc_get_issue_info",
                               mock.MagicMock(return_value=("in_progress", _MARA_AGENT_ID))):
            asyncio.run(self.api.telegram_mara_webhook(_Req()))

        mock_mara_db.__enter__.assert_called()
        mock_lena_db.__enter__.assert_not_called()


# ---------------------------------------------------------------------------
# Reply Threading — Mara-eigene telegram_mara.db
# ---------------------------------------------------------------------------
class MaraReplyThreadingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = _load_api("api_mara_reply_threading_test")

    def _make_db_with_pending_issue(self, issue_id, outbound_row=None):
        """Mock _telegram_mara_db with one pending issue and optional outbound_messages row."""
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

    def test_reply_with_mara_mapping_produces_re_mara_quote(self):
        """Sven replies to Mara message in outbound_messages → quote block 'Re Mara [Comment …]'."""
        issue_id = "HBE-1205-thread-a"
        outbound_row = mock.MagicMock()
        outbound_row.__getitem__ = mock.MagicMock(side_effect=lambda k: {
            "comment_id": "feed1234-abcd-efgh-00",
            "comment_excerpt": "Bitte prüf die Unterlagen bis Freitag",
        }[k])

        mock_db = self._make_db_with_pending_issue(issue_id, outbound_row=outbound_row)
        mock_add_comment = mock.MagicMock(return_value=True)

        class _Req:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "mara-secret"}

            async def json(self):
                return {
                    "update_id": 20,
                    "message": {
                        "message_id": 300,
                        "chat": {"id": 77777},
                        "text": "Klar, mach ich.",
                        "from": {"id": 1, "is_bot": False, "first_name": "Sven"},
                        "date": 1700005000,
                        "reply_to_message": {
                            "message_id": 250,
                            "chat": {"id": 77777},
                            "text": "Bitte prüf die Unterlagen bis Freitag",
                        },
                    },
                }

        with mock.patch.object(self.api, "_TG_MARA_WEBHOOK_SECRET", "mara-secret"), \
             mock.patch.object(self.api, "_PC_MARA_AGENT_ID", _MARA_AGENT_ID), \
             mock.patch.object(self.api, "_telegram_mara_db", return_value=mock_db), \
             mock.patch.object(self.api, "_pc_get_issue_info",
                               mock.MagicMock(return_value=("in_progress", _MARA_AGENT_ID))), \
             mock.patch.object(self.api, "_pc_add_comment_to_issue", mock_add_comment):
            result = asyncio.run(self.api.telegram_mara_webhook(_Req()))

        self.assertEqual(result, {"ok": True})
        mock_add_comment.assert_called_once()
        comment_body = mock_add_comment.call_args[0][2]
        self.assertTrue(
            comment_body.startswith("> **Re Mara [Comment feed1234,"),
            f"Expected 'Re Mara' quote-block prefix, got: {comment_body!r}",
        )
        self.assertIn("Bitte prüf die Unterlagen bis Freitag", comment_body)
        self.assertIn("Klar, mach ich.", comment_body)

    def test_reply_without_mapping_falls_back_to_raw_text(self):
        """Reply to message NOT in outbound_messages → raw quote fallback."""
        issue_id = "HBE-1205-thread-b"
        mock_db = self._make_db_with_pending_issue(issue_id, outbound_row=None)
        mock_add_comment = mock.MagicMock(return_value=True)

        class _Req:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "mara-secret"}

            async def json(self):
                return {
                    "update_id": 21,
                    "message": {
                        "message_id": 301,
                        "chat": {"id": 77777},
                        "text": "Ja verstanden.",
                        "from": {"id": 1, "is_bot": False, "first_name": "Sven"},
                        "date": 1700005100,
                        "reply_to_message": {
                            "message_id": 100,
                            "chat": {"id": 77777},
                            "text": "Alte Mara-Nachricht vor DB-Migration",
                        },
                    },
                }

        with mock.patch.object(self.api, "_TG_MARA_WEBHOOK_SECRET", "mara-secret"), \
             mock.patch.object(self.api, "_PC_MARA_AGENT_ID", _MARA_AGENT_ID), \
             mock.patch.object(self.api, "_telegram_mara_db", return_value=mock_db), \
             mock.patch.object(self.api, "_pc_get_issue_info",
                               mock.MagicMock(return_value=("in_progress", _MARA_AGENT_ID))), \
             mock.patch.object(self.api, "_pc_add_comment_to_issue", mock_add_comment):
            result = asyncio.run(self.api.telegram_mara_webhook(_Req()))

        self.assertEqual(result, {"ok": True})
        mock_add_comment.assert_called_once()
        comment_body = mock_add_comment.call_args[0][2]
        self.assertTrue(
            comment_body.startswith('> **Re:** „Alte Mara'),
            f"Expected raw-quote fallback prefix, got: {comment_body!r}",
        )
        self.assertIn("Ja verstanden.", comment_body)

    def test_in_review_mara_issue_reset_to_in_progress(self):
        """Sven message on in_review Mara issue resets to in_progress (same pattern as Lena, HBE-794)."""
        issue_id = "HBE-1205-inreview"
        mock_db = self._make_db_with_pending_issue(issue_id)
        mock_patch_status = mock.MagicMock(return_value=True)
        mock_add_comment = mock.MagicMock(return_value=True)

        class _Req:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "mara-secret"}

            async def json(self):
                return {
                    "update_id": 22,
                    "message": {
                        "message_id": 302,
                        "chat": {"id": 77777},
                        "text": "Nochmal ein Update von Sven",
                        "from": {"id": 1, "is_bot": False, "first_name": "Sven"},
                        "date": 1700005200,
                    },
                }

        with mock.patch.object(self.api, "_TG_MARA_WEBHOOK_SECRET", "mara-secret"), \
             mock.patch.object(self.api, "_PC_MARA_AGENT_ID", _MARA_AGENT_ID), \
             mock.patch.object(self.api, "_telegram_mara_db", return_value=mock_db), \
             mock.patch.object(self.api, "_pc_get_issue_info",
                               mock.MagicMock(return_value=("in_review", _MARA_AGENT_ID))), \
             mock.patch.object(self.api, "_pc_patch_issue_status", mock_patch_status), \
             mock.patch.object(self.api, "_pc_add_comment_to_issue", mock_add_comment):
            result = asyncio.run(self.api.telegram_mara_webhook(_Req()))

        self.assertEqual(result, {"ok": True})
        mock_patch_status.assert_called_once_with(issue_id, "in_progress")
        mock_add_comment.assert_called_once()

    def test_normal_message_no_reply_no_quote_block(self):
        """Regression: plain Sven message without reply_to_message must not produce a quote block."""
        issue_id = "HBE-1205-thread-c"
        mock_db = self._make_db_with_pending_issue(issue_id)
        mock_add_comment = mock.MagicMock(return_value=True)

        class _Req:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "mara-secret"}

            async def json(self):
                return {
                    "update_id": 23,
                    "message": {
                        "message_id": 303,
                        "chat": {"id": 77777},
                        "text": "Normale Mara-Anfrage ohne Reply",
                        "from": {"id": 1, "is_bot": False, "first_name": "Sven"},
                        "date": 1700005300,
                    },
                }

        with mock.patch.object(self.api, "_TG_MARA_WEBHOOK_SECRET", "mara-secret"), \
             mock.patch.object(self.api, "_PC_MARA_AGENT_ID", _MARA_AGENT_ID), \
             mock.patch.object(self.api, "_telegram_mara_db", return_value=mock_db), \
             mock.patch.object(self.api, "_pc_get_issue_info",
                               mock.MagicMock(return_value=("in_progress", _MARA_AGENT_ID))), \
             mock.patch.object(self.api, "_pc_add_comment_to_issue", mock_add_comment):
            result = asyncio.run(self.api.telegram_mara_webhook(_Req()))

        self.assertEqual(result, {"ok": True})
        mock_add_comment.assert_called_once()
        comment_body = mock_add_comment.call_args[0][2]
        self.assertEqual(comment_body, "Normale Mara-Anfrage ohne Reply")


# ---------------------------------------------------------------------------
# Outbound Tracking — POST /api/mara/telegram/send
# ---------------------------------------------------------------------------
class MaraTelegramSendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = _load_api("api_mara_send_test")

    def _make_request(self, **kwargs):
        defaults = {"chat_id": "444555666", "text": "Hallo von Mara"}
        defaults.update(kwargs)
        return self.api.LenaTelegramSendRequest(**defaults)

    def test_raises_503_without_mara_bot_token(self):
        """mara_telegram_send must raise HTTPException(503) when TELEGRAM_MARA_BOT_TOKEN not configured."""
        req = self._make_request()
        with mock.patch.object(self.api, "_TG_MARA_BOT_TOKEN", ""):
            with self.assertRaises(Exception) as ctx:
                self.api.mara_telegram_send(req, _key="test-key")
        self.assertEqual(getattr(ctx.exception, "status_code", None), 503)

    def test_returns_success_and_telegram_msg_id(self):
        req = self._make_request()
        with mock.patch.object(self.api, "_TG_MARA_BOT_TOKEN", "mara-bot-token"), \
             mock.patch.object(self.api, "_tg_mara_send_message", return_value=55) as m_send, \
             mock.patch.object(self.api, "_telegram_mara_db") as mock_db_factory:
            mock_db_factory.return_value.__enter__ = mock.MagicMock(return_value=mock.MagicMock())
            mock_db_factory.return_value.__exit__ = mock.MagicMock(return_value=False)
            resp = self.api.mara_telegram_send(req, _key="test-key")

        self.assertTrue(resp.success)
        self.assertEqual(resp.telegram_msg_id, 55)
        m_send.assert_called_once_with("444555666", "Hallo von Mara", parse_mode="MarkdownV2")

    def test_stores_in_mara_db_not_lena_db(self):
        """mara_telegram_send must write to telegram_mara.db, not telegram.db."""
        with tempfile.TemporaryDirectory() as tmp:
            mara_db_path = Path(tmp) / "telegram_mara.db"
            lena_db_path = Path(tmp) / "telegram.db"

            with mock.patch.object(self.api, "_TG_MARA_BOT_TOKEN", "mara-bot-token"), \
                 mock.patch.object(self.api, "_TELEGRAM_MARA_DB_PATH", mara_db_path), \
                 mock.patch.object(self.api, "_TELEGRAM_DB_PATH", lena_db_path), \
                 mock.patch.object(self.api, "_tg_mara_send_message", return_value=77):
                req = self._make_request(issue_id="HBE-1205", comment_id="cmt-mara-01")
                self.api.mara_telegram_send(req, _key="test-key")

            self.assertTrue(mara_db_path.exists(), "telegram_mara.db must be created")
            conn = sqlite3.connect(str(mara_db_path))
            row = conn.execute(
                "SELECT telegram_msg_id, chat_id, issue_id, comment_id, comment_excerpt "
                "FROM outbound_messages WHERE telegram_msg_id = 77"
            ).fetchone()
            conn.close()

            self.assertFalse(lena_db_path.exists(), "telegram.db (Lena) must NOT be written")

        self.assertIsNotNone(row, "Row must be inserted in mara outbound_messages")
        self.assertEqual(row[0], 77)
        self.assertEqual(row[2], "HBE-1205")
        self.assertEqual(row[3], "cmt-mara-01")
        self.assertEqual(row[4], "Hallo von Mara")

    def test_no_db_insert_without_issue_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            mara_db_path = Path(tmp) / "telegram_mara.db"
            with mock.patch.object(self.api, "_TG_MARA_BOT_TOKEN", "mara-bot-token"), \
                 mock.patch.object(self.api, "_TELEGRAM_MARA_DB_PATH", mara_db_path), \
                 mock.patch.object(self.api, "_tg_mara_send_message", return_value=88):
                req = self._make_request()  # no issue_id
                self.api.mara_telegram_send(req, _key="test-key")

            if mara_db_path.exists():
                conn = sqlite3.connect(str(mara_db_path))
                count = conn.execute("SELECT COUNT(*) FROM outbound_messages").fetchone()[0]
                conn.close()
                self.assertEqual(count, 0, "No row must be inserted without issue_id")

    def test_comment_id_defaults_to_empty_string(self):
        """comment_id=None stored as '' to satisfy NOT NULL constraint (same as Lena)."""
        with tempfile.TemporaryDirectory() as tmp:
            mara_db_path = Path(tmp) / "telegram_mara.db"
            with mock.patch.object(self.api, "_TG_MARA_BOT_TOKEN", "mara-bot-token"), \
                 mock.patch.object(self.api, "_TELEGRAM_MARA_DB_PATH", mara_db_path), \
                 mock.patch.object(self.api, "_tg_mara_send_message", return_value=99):
                req = self._make_request(issue_id="HBE-1205")  # comment_id omitted
                self.api.mara_telegram_send(req, _key="test-key")

            conn = sqlite3.connect(str(mara_db_path))
            row = conn.execute(
                "SELECT comment_id FROM outbound_messages WHERE telegram_msg_id = 99"
            ).fetchone()
            conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], "", "comment_id must default to '' not None (NOT NULL constraint)")

    def test_returns_success_false_when_telegram_api_fails(self):
        with mock.patch.object(self.api, "_TG_MARA_BOT_TOKEN", "mara-bot-token"), \
             mock.patch.object(self.api, "_tg_mara_send_message", return_value=None):
            req = self._make_request(issue_id="HBE-1205")
            resp = self.api.mara_telegram_send(req, _key="test-key")
        self.assertFalse(resp.success)
        self.assertIsNone(resp.telegram_msg_id)

    def test_mara_send_uses_mara_bot_token_not_lena(self):
        """_tg_mara_send_message must POST to Mara's bot URL, not Lena's TELEGRAM_BOT_TOKEN URL."""
        mock_resp = mock.MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"result": {"message_id": 101}}

        with mock.patch.object(self.api, "_TG_MARA_BOT_TOKEN", "real-mara-token-xyz"), \
             mock.patch.object(self.api._http, "post", return_value=mock_resp) as m_post:
            result = self.api._tg_mara_send_message("123", "test via mara bot")

        self.assertEqual(result, 101)
        call_url = m_post.call_args[0][0]
        self.assertIn("real-mara-token-xyz", call_url,
                      "URL must contain Mara's own bot token")


if __name__ == "__main__":
    unittest.main()
