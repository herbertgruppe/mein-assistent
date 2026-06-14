"""
Regression test for HBE-461: BOT_TOKEN must not appear in logs when
_tg_send_message or _pc_create_issue raise a network exception.

The Telegram API embeds the token in the URL path:
  https://api.telegram.org/bot<TOKEN>/sendMessage
Python requests serialises the full URL into str(ConnectionError), so a naive
`logger.warning("... %s", exc)` would expose the secret.  This test asserts
that neither caplog nor stderr contains the token string.
"""
import importlib.util
import logging
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FAKE_TOKEN = "123456:AAFakeTokenForHBE461Testing"


def _load_api(module_name: str, env: dict):
    api_path = REPO_ROOT / "api.py"
    spec = importlib.util.spec_from_file_location(module_name, api_path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, env, clear=False):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def api():
    env = {
        "TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
        "TELEGRAM_WEBHOOK_SECRET": "some-secret",
        "API_SECRET_KEY": "test-key",
        "PAPERCLIP_API_URL_MA": "https://paperclip.example.com",
        "PAPERCLIP_API_KEY_MA": "pc-key",
        "PAPERCLIP_COMPANY_ID_MA": "company-id",
    }
    return _load_api("api_hbe461_test", env)


class TestTgSendMessageNoTokenLeak:
    def test_connection_error_does_not_log_token(self, api, caplog):
        """ConnectionError with the full URL must not surface the token in logs."""
        error_with_url = Exception(
            f"HTTPSConnectionPool(host='api.telegram.org', port=443): "
            f"Max retries exceeded with url: /bot{FAKE_TOKEN}/sendMessage"
        )
        import requests.exceptions as req_exc

        conn_error = req_exc.ConnectionError(error_with_url)

        with mock.patch.object(api, "_TG_BOT_TOKEN", FAKE_TOKEN), \
             mock.patch("requests.post", side_effect=conn_error), \
             caplog.at_level(logging.WARNING):
            result = api._tg_send_message("123", "hello")

        assert not result
        for record in caplog.records:
            assert FAKE_TOKEN not in record.getMessage(), (
                f"BOT_TOKEN leaked in log record: {record.getMessage()!r}"
            )

    def test_generic_exception_does_not_log_token(self, api, caplog):
        """Unexpected exceptions must also not expose the token."""
        with mock.patch.object(api, "_TG_BOT_TOKEN", FAKE_TOKEN), \
             mock.patch("requests.post", side_effect=RuntimeError(f"boom {FAKE_TOKEN}")), \
             caplog.at_level(logging.WARNING):
            result = api._tg_send_message("123", "hello")

        assert not result
        for record in caplog.records:
            assert FAKE_TOKEN not in record.getMessage()

    def test_returns_false_on_network_error(self, api):
        """Sanity: function must still return falsy (not raise) on network error."""
        import requests.exceptions as req_exc

        with mock.patch.object(api, "_TG_BOT_TOKEN", FAKE_TOKEN), \
             mock.patch("requests.post", side_effect=req_exc.Timeout("timed out")):
            assert not api._tg_send_message("123", "hello")


class TestPcCreateIssueNoTokenLeak:
    """_pc_create_issue has no secret in its URL, but the same logging fix applies."""

    def test_connection_error_does_not_log_any_secret(self, api, caplog):
        import requests.exceptions as req_exc

        conn_error = req_exc.ConnectionError("connection refused")

        with mock.patch.object(api, "_PC_API_URL", "https://paperclip.example.com"), \
             mock.patch.object(api, "_PC_API_KEY", "secret-api-key"), \
             mock.patch("requests.post", side_effect=conn_error), \
             caplog.at_level(logging.WARNING):
            result = api._pc_create_issue("chat1", 1, "user", "text")

        assert result is None
        for record in caplog.records:
            assert "secret-api-key" not in record.getMessage()
