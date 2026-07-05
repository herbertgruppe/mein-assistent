"""
Tests for HBE-1421: Generisches Telegram-Routing (Registry-Pattern).

Covers:
- _TELEGRAM_AGENTS registry built from legacy env vars (lena, mara)
- _TELEGRAM_AGENTS registry extended by TELEGRAM_AGENT_{SLUG}_TOKEN new-style vars
- New-style slug overrides legacy when both set for same slug
- POST /api/telegram/{slug}/send returns 404 for unknown slug
- POST /api/telegram/{slug}/send delegates to correct agent token
- POST /api/telegram/{slug}/webhook returns ok:True for unknown slug (safe no-op)
- POST /api/telegram/{slug}/webhook rejects wrong secret
- Backward-compat: /api/lena/telegram/send delegates to lena agent
- Backward-compat: /api/mara/telegram/send delegates to mara agent
- _TgAgentCfg db_path uses legacy filenames for lena/mara
- New slug gets generic db filename telegram_{slug}.db
"""
import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_api(module_name: str, env: dict):
    api_path = REPO_ROOT / "api.py"
    spec = importlib.util.spec_from_file_location(module_name, api_path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, env, clear=True):
        spec.loader.exec_module(module)
    return module


_BASE = {
    "API_SECRET_KEY": "test-key",
    "PAPERCLIP_API_URL_MA": "",
    "PAPERCLIP_COMPANY_ID_MA": "",
    "PAPERCLIP_API_KEY_MA": "",
}


class RegistryBuildTest(unittest.TestCase):
    """_TELEGRAM_AGENTS registry is built correctly from env vars."""

    def test_empty_registry_when_no_tokens_set(self):
        api = _load_api("api_reg_empty", {**_BASE})
        self.assertEqual(api._TELEGRAM_AGENTS, {})

    def test_lena_registered_via_legacy_vars(self):
        api = _load_api("api_reg_lena", {
            **_BASE,
            "TELEGRAM_BOT_TOKEN": "lena-token",
            "TELEGRAM_WEBHOOK_SECRET": "lena-secret",
            "PAPERCLIP_LENA_AGENT_ID": "lena-agent-uuid",
        })
        self.assertIn("lena", api._TELEGRAM_AGENTS)
        cfg = api._TELEGRAM_AGENTS["lena"]
        self.assertEqual(cfg.token, "lena-token")
        self.assertEqual(cfg.webhook_secret, "lena-secret")
        self.assertEqual(cfg.pc_agent_id, "lena-agent-uuid")

    def test_mara_registered_via_legacy_vars(self):
        api = _load_api("api_reg_mara", {
            **_BASE,
            "TELEGRAM_MARA_BOT_TOKEN": "mara-token",
            "TELEGRAM_MARA_WEBHOOK_SECRET": "mara-secret",
            "PAPERCLIP_MARA_AGENT_ID": "mara-agent-uuid",
        })
        self.assertIn("mara", api._TELEGRAM_AGENTS)
        cfg = api._TELEGRAM_AGENTS["mara"]
        self.assertEqual(cfg.token, "mara-token")

    def test_new_style_slug_registered(self):
        api = _load_api("api_reg_new_slug", {
            **_BASE,
            "TELEGRAM_AGENT_ANNA_TOKEN": "anna-token",
            "TELEGRAM_AGENT_ANNA_WEBHOOK_SECRET": "anna-secret",
            "TELEGRAM_AGENT_ANNA_PAPERCLIP_AGENT_ID": "anna-agent-uuid",
        })
        self.assertIn("anna", api._TELEGRAM_AGENTS)
        cfg = api._TELEGRAM_AGENTS["anna"]
        self.assertEqual(cfg.token, "anna-token")
        self.assertEqual(cfg.slug, "anna")

    def test_new_style_slug_is_lowercased(self):
        api = _load_api("api_reg_slug_case", {
            **_BASE,
            "TELEGRAM_AGENT_BOB_TOKEN": "bob-token",
            "TELEGRAM_AGENT_BOB_WEBHOOK_SECRET": "bob-secret",
            "TELEGRAM_AGENT_BOB_PAPERCLIP_AGENT_ID": "bob-uuid",
        })
        self.assertIn("bob", api._TELEGRAM_AGENTS)
        self.assertNotIn("BOB", api._TELEGRAM_AGENTS)

    def test_lena_db_path_uses_legacy_filename(self):
        api = _load_api("api_reg_lena_db", {
            **_BASE,
            "TELEGRAM_BOT_TOKEN": "lena-token",
            "TELEGRAM_WEBHOOK_SECRET": "lena-secret",
        })
        cfg = api._TELEGRAM_AGENTS["lena"]
        self.assertEqual(cfg.db_path.name, "telegram.db")

    def test_mara_db_path_uses_legacy_filename(self):
        api = _load_api("api_reg_mara_db", {
            **_BASE,
            "TELEGRAM_MARA_BOT_TOKEN": "mara-token",
            "TELEGRAM_MARA_WEBHOOK_SECRET": "mara-secret",
            "PAPERCLIP_MARA_AGENT_ID": "mara-uuid",
        })
        cfg = api._TELEGRAM_AGENTS["mara"]
        self.assertEqual(cfg.db_path.name, "telegram_mara.db")

    def test_new_slug_db_path_uses_generic_filename(self):
        api = _load_api("api_reg_new_db", {
            **_BASE,
            "TELEGRAM_AGENT_KLARA_TOKEN": "klara-token",
            "TELEGRAM_AGENT_KLARA_WEBHOOK_SECRET": "klara-secret",
            "TELEGRAM_AGENT_KLARA_PAPERCLIP_AGENT_ID": "klara-uuid",
        })
        cfg = api._TELEGRAM_AGENTS["klara"]
        self.assertEqual(cfg.db_path.name, "telegram_klara.db")


class BackwardCompatVarsTest(unittest.TestCase):
    """Module-level backward-compat globals still reflect registry."""

    def test_tg_bot_token_mirrors_lena(self):
        api = _load_api("api_bc_lena_token", {
            **_BASE,
            "TELEGRAM_BOT_TOKEN": "lena-token",
            "TELEGRAM_WEBHOOK_SECRET": "lena-secret",
        })
        self.assertEqual(api._TG_BOT_TOKEN, "lena-token")

    def test_tg_mara_bot_token_mirrors_mara(self):
        api = _load_api("api_bc_mara_token", {
            **_BASE,
            "TELEGRAM_MARA_BOT_TOKEN": "mara-token",
            "TELEGRAM_MARA_WEBHOOK_SECRET": "mara-secret",
            "PAPERCLIP_MARA_AGENT_ID": "mara-uuid",
        })
        self.assertEqual(api._TG_MARA_BOT_TOKEN, "mara-token")

    def test_bc_globals_empty_when_no_lena(self):
        api = _load_api("api_bc_no_lena", {**_BASE})
        self.assertEqual(api._TG_BOT_TOKEN, "")
        self.assertEqual(api._TG_WEBHOOK_SECRET, "")


class GenericSendEndpointTest(unittest.TestCase):
    """POST /api/telegram/{slug}/send generic routing."""

    @classmethod
    def setUpClass(cls):
        cls.api = _load_api("api_gs_test", {
            **_BASE,
            "TELEGRAM_BOT_TOKEN": "lena-token",
            "TELEGRAM_WEBHOOK_SECRET": "lena-secret",
            "TELEGRAM_MARA_BOT_TOKEN": "mara-token",
            "TELEGRAM_MARA_WEBHOOK_SECRET": "mara-secret",
            "PAPERCLIP_MARA_AGENT_ID": "mara-uuid",
        })

    def test_send_404_for_unknown_slug(self):
        from fastapi import HTTPException
        req = self.api.LenaTelegramSendRequest(chat_id="123", text="hi")
        with self.assertRaises(HTTPException) as ctx:
            self.api.telegram_agent_send("nonexistent", req, _key="")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_send_uses_correct_token_for_lena(self):
        req = self.api.LenaTelegramSendRequest(chat_id="123", text="hi")
        with mock.patch.object(self.api, "_tg_agent_send", return_value=42) as m_send, \
             mock.patch.object(self.api, "_tg_agent_db") as mock_db:
            mock_db.return_value.__enter__ = mock.MagicMock(return_value=mock.MagicMock())
            mock_db.return_value.__exit__ = mock.MagicMock(return_value=False)
            resp = self.api.telegram_agent_send("lena", req, _key="")
        self.assertTrue(resp.success)
        # first positional arg to _tg_agent_send must be lena's token
        args = m_send.call_args[0]
        self.assertEqual(args[0], "lena-token")

    def test_send_uses_correct_token_for_mara(self):
        req = self.api.LenaTelegramSendRequest(chat_id="123", text="hi")
        with mock.patch.object(self.api, "_tg_agent_send", return_value=43) as m_send, \
             mock.patch.object(self.api, "_tg_agent_db") as mock_db:
            mock_db.return_value.__enter__ = mock.MagicMock(return_value=mock.MagicMock())
            mock_db.return_value.__exit__ = mock.MagicMock(return_value=False)
            resp = self.api.telegram_agent_send("mara", req, _key="")
        self.assertTrue(resp.success)
        args = m_send.call_args[0]
        self.assertEqual(args[0], "mara-token")


class BackwardCompatAliasRoutesTest(unittest.TestCase):
    """/api/lena/telegram/send and /api/mara/telegram/send delegate to generic handler."""

    @classmethod
    def setUpClass(cls):
        cls.api = _load_api("api_alias_test", {
            **_BASE,
            "TELEGRAM_BOT_TOKEN": "lena-token",
            "TELEGRAM_WEBHOOK_SECRET": "lena-secret",
            "TELEGRAM_MARA_BOT_TOKEN": "mara-token",
            "TELEGRAM_MARA_WEBHOOK_SECRET": "mara-secret",
            "PAPERCLIP_MARA_AGENT_ID": "mara-uuid",
        })

    def test_lena_alias_calls_generic_with_lena_slug(self):
        req = self.api.LenaTelegramSendRequest(chat_id="111", text="hello")
        with mock.patch.object(self.api, "telegram_agent_send", return_value=mock.MagicMock(success=True)) as m:
            self.api.lena_telegram_send(req, _key="test-key")
        m.assert_called_once()
        self.assertEqual(m.call_args[0][0], "lena")

    def test_mara_alias_calls_generic_with_mara_slug(self):
        req = self.api.LenaTelegramSendRequest(chat_id="222", text="hello mara")
        with mock.patch.object(self.api, "telegram_agent_send", return_value=mock.MagicMock(success=True)) as m:
            self.api.mara_telegram_send(req, _key="test-key")
        m.assert_called_once()
        self.assertEqual(m.call_args[0][0], "mara")


class GenericWebhookAuthTest(unittest.TestCase):
    """POST /api/telegram/{slug}/webhook auth and unknown-slug handling."""

    @classmethod
    def setUpClass(cls):
        cls.api = _load_api("api_gwh_test", {
            **_BASE,
            "TELEGRAM_BOT_TOKEN": "lena-token",
            "TELEGRAM_WEBHOOK_SECRET": "correct-secret",
        })

    def _make_req(self, secret: str, body: dict | None = None):
        req = mock.AsyncMock()
        req.headers = {"X-Telegram-Bot-Api-Secret-Token": secret}
        req.client = mock.MagicMock()
        req.client.host = "1.2.3.4"
        req.json = mock.AsyncMock(return_value=body or {"update_id": 1})
        return req

    def test_unknown_slug_returns_ok_without_error(self):
        import asyncio
        req = self._make_req("any-secret")
        result = asyncio.run(
            self.api.telegram_agent_webhook("nonexistent", req)
        )
        self.assertEqual(result, {"ok": True})

    def test_wrong_secret_returns_ok_silently(self):
        import asyncio
        req = self._make_req("wrong-secret", {"update_id": 1})
        result = asyncio.run(
            self.api.telegram_agent_webhook("lena", req)
        )
        self.assertEqual(result, {"ok": True})

    def test_correct_secret_proceeds(self):
        import asyncio
        body = {"update_id": 1, "message": {"message_id": 1, "date": 0, "chat": {"id": 99, "type": "private"}, "text": "hi", "from": {"id": 1, "is_bot": False, "first_name": "T"}}}
        req = self._make_req("correct-secret", body)
        with mock.patch.object(self.api, "_pc_create_tg_issue", return_value=None), \
             mock.patch.object(self.api, "_tg_agent_db") as mock_db, \
             mock.patch.object(self.api, "_pc_get_issue_info", return_value=(None, None)):
            db_conn = mock.MagicMock()
            db_conn.execute.return_value.fetchall.return_value = []
            db_conn.execute.return_value.fetchone.return_value = None
            mock_db.return_value.__enter__ = mock.MagicMock(return_value=db_conn)
            mock_db.return_value.__exit__ = mock.MagicMock(return_value=False)
            result = asyncio.run(
                self.api.telegram_agent_webhook("lena", req)
            )
        self.assertEqual(result, {"ok": True})


if __name__ == "__main__":
    unittest.main()
