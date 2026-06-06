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


if __name__ == "__main__":
    unittest.main()
