"""
Tests for HBE-1559: KeyError-Guard in backward-compat wrappers.

Covers:
- Lena-only deployment: _telegram_mara_db() raises RuntimeError (not KeyError)
- Lena-only deployment: _pc_create_mara_issue() raises RuntimeError (not KeyError)
- Mara-only deployment: _telegram_db() raises RuntimeError (not KeyError)
- Mara-only deployment: _pc_create_issue() raises RuntimeError (not KeyError)
"""
import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]

_LENA_ONLY_ENV = {
    "TELEGRAM_BOT_TOKEN": "lena-only-token",
    "TELEGRAM_WEBHOOK_SECRET": "lena-only-secret",
    "TELEGRAM_MARA_BOT_TOKEN": "",
    "TELEGRAM_MARA_WEBHOOK_SECRET": "",
    "API_SECRET_KEY": "test-key",
}

_MARA_ONLY_ENV = {
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_WEBHOOK_SECRET": "",
    "TELEGRAM_MARA_BOT_TOKEN": "mara-only-token",
    "TELEGRAM_MARA_WEBHOOK_SECRET": "mara-only-secret",
    "PAPERCLIP_MARA_AGENT_ID": "ed26f194-f0a9-4f70-a52d-6e39be9013e3",
    "API_SECRET_KEY": "test-key",
}


def _load_api(module_name: str, env: dict):
    api_path = REPO_ROOT / "api.py"
    spec = importlib.util.spec_from_file_location(module_name, api_path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, env, clear=False):
        spec.loader.exec_module(module)
    return module


class LenaOnlyDeploymentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = _load_api("api_lena_only_test", _LENA_ONLY_ENV)

    def test_mara_cfg_is_none(self):
        self.assertIsNone(self.api._mara_cfg,
                          "_mara_cfg must be None in lena-only deployment")

    def test_lena_cfg_is_present(self):
        self.assertIsNotNone(self.api._lena_cfg,
                             "_lena_cfg must be set in lena-only deployment")

    def test_telegram_mara_db_raises_runtime_error_not_key_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.api._telegram_mara_db()
        self.assertIn("mara", str(ctx.exception).lower(),
                      "RuntimeError message must mention 'mara'")
        self.assertNotIsInstance(ctx.exception, KeyError)

    def test_pc_create_mara_issue_raises_runtime_error_not_key_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.api._pc_create_mara_issue("chat1", 1, "user", "text")
        self.assertIn("mara", str(ctx.exception).lower())
        self.assertNotIsInstance(ctx.exception, KeyError)

    def test_telegram_db_works_normally(self):
        """_telegram_db() must not raise in lena-only deployment."""
        with self.api._telegram_db() as db:
            self.assertIsNotNone(db)


class MaraOnlyDeploymentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = _load_api("api_mara_only_test", _MARA_ONLY_ENV)

    def test_lena_cfg_is_none(self):
        self.assertIsNone(self.api._lena_cfg,
                          "_lena_cfg must be None in mara-only deployment")

    def test_mara_cfg_is_present(self):
        self.assertIsNotNone(self.api._mara_cfg,
                             "_mara_cfg must be set in mara-only deployment")

    def test_telegram_db_raises_runtime_error_not_key_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.api._telegram_db()
        self.assertIn("lena", str(ctx.exception).lower(),
                      "RuntimeError message must mention 'lena'")
        self.assertNotIsInstance(ctx.exception, KeyError)

    def test_pc_create_issue_raises_runtime_error_not_key_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.api._pc_create_issue("chat1", 1, "user", "text")
        self.assertIn("lena", str(ctx.exception).lower())
        self.assertNotIsInstance(ctx.exception, KeyError)

    def test_telegram_mara_db_works_normally(self):
        """_telegram_mara_db() must not raise in mara-only deployment."""
        with self.api._telegram_mara_db() as db:
            self.assertIsNotNone(db)


if __name__ == "__main__":
    unittest.main()
