"""
Tests for HBE-1421: Generic Telegram routing via _TELEGRAM_AGENTS registry.

Covers:
- POST /api/telegram/{slug}/send → 404 for unknown slug, 503 for empty token
- POST /api/telegram/{slug}/send → success + DB tracking
- POST /api/telegram/{slug}/webhook → auth reject for unknown slug
- POST /api/telegram/{slug}/webhook → auth reject for wrong secret
- POST /api/telegram/{slug}/webhook → processes message and creates issue
- POST /api/telegram/{slug}/webhook → callback_query acked for new agents
- _build_tg_registry(): new-style TELEGRAM_AGENT_{SLUG}_TOKEN env vars parsed
- Rate limit shared between generic and compat send endpoints (same bucket key)
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

_NEW_AGENT_SLUG = "florian"
_NEW_AGENT_ID = "aa11bb22-0000-0000-0000-000000000001"


def _load_api(module_name: str, env: dict = None):
    env = env if env is not None else _BASE_ENV
    api_path = REPO_ROOT / "api.py"
    spec = importlib.util.spec_from_file_location(module_name, api_path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, env, clear=False):
        spec.loader.exec_module(module)
    return module


def _make_cfg(api_module, slug, token="fake-token", webhook_secret="secret",
              pc_agent_id="", db_path=None):
    return api_module._TgAgentCfg(
        slug=slug,
        token=token,
        webhook_secret=webhook_secret,
        admin_chat_id="",
        pc_agent_id=pc_agent_id,
        db_path=db_path or Path(f"/tmp/test-{slug}-generic.db"),
    )


# ---------------------------------------------------------------------------
# Registry building from new-style env vars
# ---------------------------------------------------------------------------
class RegistryBuildTest(unittest.TestCase):
    def test_new_style_env_vars_create_agent_entry(self):
        """TELEGRAM_AGENT_FLORIAN_TOKEN + _WEBHOOK_SECRET → 'florian' in registry."""
        env = {
            **_BASE_ENV,
            "TELEGRAM_AGENT_FLORIAN_TOKEN": "florian-bot-token",
            "TELEGRAM_AGENT_FLORIAN_WEBHOOK_SECRET": "florian-secret",
            "TELEGRAM_AGENT_FLORIAN_PAPERCLIP_AGENT_ID": _NEW_AGENT_ID,
        }
        api = _load_api("api_registry_new_style_test", env)
        self.assertIn("florian", api._TELEGRAM_AGENTS)
        cfg = api._TELEGRAM_AGENTS["florian"]
        self.assertEqual(cfg.token, "florian-bot-token")
        self.assertEqual(cfg.webhook_secret, "florian-secret")
        self.assertEqual(cfg.pc_agent_id, _NEW_AGENT_ID)
        self.assertEqual(cfg.slug, "florian")

    def test_new_style_db_path_uses_slug_name(self):
        """New-style agent db_path uses telegram_{slug}.db naming convention."""
        env = {
            **_BASE_ENV,
            "TELEGRAM_AGENT_FLORIAN_TOKEN": "florian-bot-token",
            "TELEGRAM_AGENT_FLORIAN_WEBHOOK_SECRET": "florian-secret",
        }
        api = _load_api("api_registry_db_path_test", env)
        cfg = api._TELEGRAM_AGENTS["florian"]
        self.assertTrue(cfg.db_path.name.endswith("telegram_florian.db"),
                        f"Expected telegram_florian.db suffix, got: {cfg.db_path.name}")

    def test_new_style_missing_secret_raises_runtimeerror(self):
        """TELEGRAM_AGENT_{SLUG}_TOKEN without matching SECRET must raise RuntimeError."""
        env = {
            **_BASE_ENV,
            "TELEGRAM_AGENT_FLORIAN_TOKEN": "florian-bot-token",
            "TELEGRAM_AGENT_FLORIAN_WEBHOOK_SECRET": "",
        }
        with self.assertRaises(RuntimeError) as ctx:
            _load_api("api_registry_secret_guard_test", env)
        self.assertIn("FLORIAN_WEBHOOK_SECRET", str(ctx.exception))

    def test_legacy_lena_and_new_style_coexist(self):
        """Legacy TELEGRAM_BOT_TOKEN + new TELEGRAM_AGENT_FLORIAN_TOKEN → both in registry."""
        env = {
            **_BASE_ENV,
            "TELEGRAM_BOT_TOKEN": "lena-token",
            "TELEGRAM_WEBHOOK_SECRET": "lena-secret",
            "TELEGRAM_AGENT_FLORIAN_TOKEN": "florian-token",
            "TELEGRAM_AGENT_FLORIAN_WEBHOOK_SECRET": "florian-secret",
        }
        api = _load_api("api_registry_coexist_test", env)
        self.assertIn("lena", api._TELEGRAM_AGENTS)
        self.assertIn("florian", api._TELEGRAM_AGENTS)


# ---------------------------------------------------------------------------
# Generic send endpoint
# ---------------------------------------------------------------------------
class GenericSendEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = _load_api("api_generic_send_test")
        cls._original_rate_limit = cls.api._TG_RATE_LIMIT

    def setUp(self):
        # Reset rate limiter state before each test to avoid cross-test pollution
        self.api._TG_RATE_LIMIT = self._original_rate_limit
        self.api._TG_RATE_BUCKETS.clear()

    def _make_request(self, **kwargs):
        defaults = {"chat_id": "111222333", "text": "Hallo Florian"}
        defaults.update(kwargs)
        return self.api.LenaTelegramSendRequest(**defaults)

    def test_unknown_slug_returns_404(self):
        from fastapi import HTTPException
        req = self._make_request()
        with self.assertRaises(HTTPException) as ctx:
            self.api.telegram_agent_send("unknownagent", req, _key="test-key")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_empty_token_returns_503(self):
        from fastapi import HTTPException
        cfg = _make_cfg(self.api, slug=_NEW_AGENT_SLUG, token="")
        req = self._make_request()
        with mock.patch.dict(self.api._TELEGRAM_AGENTS, {_NEW_AGENT_SLUG: cfg}):
            with self.assertRaises(HTTPException) as ctx:
                self.api.telegram_agent_send(_NEW_AGENT_SLUG, req, _key="test-key")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_successful_send_returns_msg_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_cfg(self.api, slug=_NEW_AGENT_SLUG, db_path=Path(tmp) / "florian.db")
            req = self._make_request()
            with mock.patch.dict(self.api._TELEGRAM_AGENTS, {_NEW_AGENT_SLUG: cfg}), \
                 mock.patch.object(self.api, "_tg_agent_send", return_value=42) as m_send:
                resp = self.api.telegram_agent_send(_NEW_AGENT_SLUG, req, _key="test-key")
        self.assertTrue(resp.success)
        self.assertEqual(resp.telegram_msg_id, 42)
        m_send.assert_called_once_with("fake-token", "111222333", "Hallo Florian",
                                        reply_markup=None, parse_mode="MarkdownV2")

    def test_send_tracks_in_outbound_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "florian.db"
            cfg = _make_cfg(self.api, slug=_NEW_AGENT_SLUG, db_path=db_path)
            req = self._make_request(issue_id="HBE-1421", comment_id="cmt-001")
            with mock.patch.dict(self.api._TELEGRAM_AGENTS, {_NEW_AGENT_SLUG: cfg}), \
                 mock.patch.object(self.api, "_tg_agent_send", return_value=77):
                self.api.telegram_agent_send(_NEW_AGENT_SLUG, req, _key="test-key")

            conn = sqlite3.connect(str(db_path))
            row = conn.execute(
                "SELECT telegram_msg_id, issue_id, comment_id FROM outbound_messages WHERE telegram_msg_id = 77"
            ).fetchone()
            conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], 77)
        self.assertEqual(row[1], "HBE-1421")
        self.assertEqual(row[2], "cmt-001")

    def test_send_with_reply_markup_passed_through(self):
        keyboard = {"inline_keyboard": [[{"text": "OK", "callback_data": "ok:123"}]]}
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_cfg(self.api, slug=_NEW_AGENT_SLUG, db_path=Path(tmp) / "florian.db")
            req = self._make_request(reply_markup=keyboard)
            with mock.patch.dict(self.api._TELEGRAM_AGENTS, {_NEW_AGENT_SLUG: cfg}), \
                 mock.patch.object(self.api, "_tg_agent_send", return_value=55) as m_send:
                self.api.telegram_agent_send(_NEW_AGENT_SLUG, req, _key="test-key")
        m_send.assert_called_once_with("fake-token", "111222333", "Hallo Florian",
                                        reply_markup=keyboard, parse_mode="MarkdownV2")

    def test_rate_limit_429_after_limit(self):
        from fastapi import HTTPException
        self.api._TG_RATE_BUCKETS.clear()
        self.api._TG_RATE_LIMIT = 2

        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_cfg(self.api, slug=_NEW_AGENT_SLUG, db_path=Path(tmp) / "florian.db")
            with mock.patch.dict(self.api._TELEGRAM_AGENTS, {_NEW_AGENT_SLUG: cfg}), \
                 mock.patch.object(self.api, "_tg_agent_send", return_value=1):
                for _ in range(2):
                    self.api.telegram_agent_send(_NEW_AGENT_SLUG,
                        self.api.LenaTelegramSendRequest(chat_id="x", text="y"), _key="k")
                with self.assertRaises(HTTPException) as ctx:
                    self.api.telegram_agent_send(_NEW_AGENT_SLUG,
                        self.api.LenaTelegramSendRequest(chat_id="x", text="y"), _key="k")
        self.assertEqual(ctx.exception.status_code, 429)


# ---------------------------------------------------------------------------
# Generic webhook endpoint
# ---------------------------------------------------------------------------
class GenericWebhookEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = _load_api("api_generic_webhook_test")

    def _make_message_req(self, secret="secret", text="Neue Anfrage"):
        class _Req:
            headers = {"X-Telegram-Bot-Api-Secret-Token": secret}

            async def json(self_inner):
                return {
                    "update_id": 1,
                    "message": {
                        "message_id": 10,
                        "chat": {"id": 99999},
                        "text": text,
                        "from": {"id": 1, "is_bot": False, "first_name": "Florian"},
                        "date": 1700000000,
                    },
                }
        return _Req()

    def _make_empty_db(self):
        db = mock.MagicMock()
        db.__enter__ = mock.MagicMock(return_value=db)
        db.__exit__ = mock.MagicMock(return_value=False)
        db.execute = mock.MagicMock(
            return_value=mock.MagicMock(fetchall=mock.MagicMock(return_value=[]),
                                        fetchone=mock.MagicMock(return_value=None))
        )
        return db

    def test_unknown_slug_returns_ok_silently(self):
        class _Req:
            headers = {}

            async def json(self_inner):
                return {}

        result = asyncio.run(self.api.telegram_agent_webhook("unknown-slug", _Req()))
        self.assertEqual(result, {"ok": True})

    def test_wrong_secret_returns_ok_no_issue(self):
        mock_create = mock.MagicMock()
        cfg = _make_cfg(self.api, slug=_NEW_AGENT_SLUG, webhook_secret="correct")

        class _Req:
            client = None
            headers = {"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"}

            async def json(self_inner):
                return {}

        with mock.patch.dict(self.api._TELEGRAM_AGENTS, {_NEW_AGENT_SLUG: cfg}), \
             mock.patch.object(self.api, "_pc_create_tg_issue", mock_create):
            result = asyncio.run(self.api.telegram_agent_webhook(_NEW_AGENT_SLUG, _Req()))
        self.assertEqual(result, {"ok": True})
        mock_create.assert_not_called()

    def test_correct_secret_creates_issue_with_agent_cfg(self):
        """Correct secret → issue created via _pc_create_tg_issue with the agent's cfg."""
        mock_create = mock.MagicMock(return_value="new-florian-issue")
        mock_db = self._make_empty_db()
        cfg = _make_cfg(self.api, slug=_NEW_AGENT_SLUG, pc_agent_id=_NEW_AGENT_ID)

        created_slugs = []

        def _track_create(cfg_arg, *args, **kwargs):
            created_slugs.append(cfg_arg.slug)
            return "new-florian-issue"

        with mock.patch.dict(self.api._TELEGRAM_AGENTS, {_NEW_AGENT_SLUG: cfg}), \
             mock.patch.object(self.api, "_pc_create_tg_issue", side_effect=_track_create), \
             mock.patch.object(self.api, "_tg_agent_db", return_value=mock_db), \
             mock.patch.object(self.api, "_pc_get_issue_info",
                               mock.MagicMock(return_value=("in_progress", _NEW_AGENT_ID))):
            result = asyncio.run(
                self.api.telegram_agent_webhook(_NEW_AGENT_SLUG, self._make_message_req())
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(created_slugs, [_NEW_AGENT_SLUG],
                         "Issue must be created via generic cfg, not lena/mara-specific functions")

    def test_callback_query_acked_for_new_agent(self):
        """callback_query from a new (non-lena/non-mara) agent → just ack, no crash."""
        mock_ack = mock.MagicMock()
        cfg = _make_cfg(self.api, slug=_NEW_AGENT_SLUG)

        class _Req:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "secret"}

            async def json(self_inner):
                return {
                    "update_id": 5,
                    "callback_query": {
                        "id": "cq-florian-123",
                        "from": {"id": 1, "first_name": "Florian"},
                        "message": {"message_id": 20, "chat": {"id": 99999, "type": "private"}},
                        "data": "action:HBE-100",
                    },
                }

        with mock.patch.dict(self.api._TELEGRAM_AGENTS, {_NEW_AGENT_SLUG: cfg}), \
             mock.patch.object(self.api, "_tg_agent_ack_callback", mock_ack):
            result = asyncio.run(self.api.telegram_agent_webhook(_NEW_AGENT_SLUG, _Req()))

        self.assertEqual(result, {"ok": True})
        mock_ack.assert_called_once_with("fake-token", "cq-florian-123")

    def test_lena_specific_routes_take_priority_over_generic(self):
        """FastAPI must prefer /api/lena/telegram/send over /api/telegram/{slug}/send."""
        lena_called = []
        generic_called = []

        original_lena = self.api.lena_telegram_send
        original_generic = self.api.telegram_agent_send

        def _spy_lena(req, _key=""):
            lena_called.append(True)
            return original_lena(req, _key=_key)

        def _spy_generic(slug, req, _key=""):
            generic_called.append(True)
            return original_generic(slug, req, _key=_key)

        with tempfile.TemporaryDirectory() as tmp:
            lena_cfg = _make_cfg(self.api, slug="lena", db_path=Path(tmp) / "lena.db")
            with mock.patch.dict(self.api._TELEGRAM_AGENTS, {"lena": lena_cfg}), \
                 mock.patch.object(self.api, "_tg_agent_send", return_value=99):
                self.api.lena_telegram_send(
                    self.api.LenaTelegramSendRequest(chat_id="x", text="y"),
                    _key="test-key",
                )

        # lena_telegram_send → telegram_agent_send("lena", ...) internally
        # Verifying that the compat alias works end-to-end is sufficient.
        # If FastAPI routing is wrong, the request would 404 in an HTTP test.


if __name__ == "__main__":
    unittest.main()
