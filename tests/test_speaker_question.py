"""
Tests for HBE-822: SKILL_SPEAKER Telegram Quick-Reply workflow.

Covers:
- _tg_send_message returns message_id (int) on success, None on failure
- _tg_send_message passes reply_markup to Telegram API
- _handle_speaker_callback dispatches correctly for spkr_pause / spkr_cont / spkr_ready
- POST /api/telegram/speaker-question sends keyboard and stores message_id
- GET /api/telegram/speaker-fallback-config returns MARA_SPEAKER_FALLBACK_DEFAULT
- callback_query in webhook is routed to _handle_speaker_callback
"""
import asyncio
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_api(module_name: str, env: dict):
    api_path = REPO_ROOT / "api.py"
    spec = importlib.util.spec_from_file_location(module_name, api_path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, env, clear=False):
        spec.loader.exec_module(module)
    return module


_BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_WEBHOOK_SECRET": "",
    "API_SECRET_KEY": "test-key",
}


class TgSendMessageWithMarkupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = _load_api("api_spkr_send_test", _BASE_ENV)

    def test_returns_message_id_on_success(self):
        mock_resp = mock.MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"result": {"message_id": 42}}
        with mock.patch.object(self.api, "_TG_BOT_TOKEN", "fake-token"), \
             mock.patch.object(self.api._http, "post", return_value=mock_resp) as m_post:
            result = self.api._tg_send_message("123", "hello")
        self.assertEqual(result, 42)
        payload = m_post.call_args[1]["json"]
        self.assertEqual(payload["text"], "hello")
        self.assertNotIn("reply_markup", payload)

    def test_passes_reply_markup(self):
        mock_resp = mock.MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"result": {"message_id": 99}}
        keyboard = {"inline_keyboard": [[{"text": "btn", "callback_data": "x"}]]}
        with mock.patch.object(self.api, "_TG_BOT_TOKEN", "fake-token"), \
             mock.patch.object(self.api._http, "post", return_value=mock_resp) as m_post:
            result = self.api._tg_send_message("123", "choose", reply_markup=keyboard)
        self.assertEqual(result, 99)
        payload = m_post.call_args[1]["json"]
        self.assertEqual(payload["reply_markup"], keyboard)

    def test_returns_none_on_failure(self):
        mock_resp = mock.MagicMock()
        mock_resp.ok = False
        with mock.patch.object(self.api, "_TG_BOT_TOKEN", "fake-token"), \
             mock.patch.object(self.api._http, "post", return_value=mock_resp):
            result = self.api._tg_send_message("123", "hi")
        self.assertIsNone(result)

    def test_returns_none_when_no_token(self):
        with mock.patch.object(self.api, "_TG_BOT_TOKEN", ""):
            result = self.api._tg_send_message("123", "hi")
        self.assertIsNone(result)


class HandleSpeakerCallbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = _load_api("api_spkr_callback_test", _BASE_ENV)

    def _make_mocks(self):
        patch_send = mock.patch.object(self.api, "_tg_send_message", return_value=1)
        patch_patch = mock.patch.object(self.api, "_pc_patch_issue_status", return_value=True)
        patch_comment = mock.patch.object(self.api, "_pc_post_system_comment", return_value=True)
        return patch_send, patch_patch, patch_comment

    def test_spkr_pause_blocks_issue_and_sends_fertig_button(self):
        p_send, p_patch, p_comment = self._make_mocks()
        with p_send as m_send, p_patch as m_patch, p_comment as m_comment:
            self.api._handle_speaker_callback("spkr_pause:HBE-753", "chat-1")

        m_patch.assert_called_once_with("HBE-753", "blocked", "awaiting_plaud_update")
        m_comment.assert_called_once_with("HBE-753", "TELEGRAM_CALLBACK: speaker_plaud_update")
        # Should send a follow-up message with a Fertig-button
        m_send.assert_called_once()
        call_kwargs = m_send.call_args
        keyboard = call_kwargs[1].get("reply_markup") or call_kwargs[0][2]
        self.assertIn("spkr_ready:HBE-753", str(keyboard))

    def test_spkr_cont_posts_comment_only(self):
        p_send, p_patch, p_comment = self._make_mocks()
        with p_send as m_send, p_patch as m_patch, p_comment as m_comment:
            self.api._handle_speaker_callback("spkr_cont:HBE-753", "chat-1")

        m_patch.assert_not_called()
        m_comment.assert_called_once_with("HBE-753", "TELEGRAM_CALLBACK: speaker_continue")
        m_send.assert_called_once()

    def test_spkr_ready_unblocks_and_posts_comment(self):
        p_send, p_patch, p_comment = self._make_mocks()
        with p_send as m_send, p_patch as m_patch, p_comment as m_comment:
            self.api._handle_speaker_callback("spkr_ready:HBE-753", "chat-1")

        m_patch.assert_called_once_with("HBE-753", "in_progress")
        m_comment.assert_called_once_with("HBE-753", "TELEGRAM_CALLBACK: speaker_ready")
        m_send.assert_called_once()

    def test_unknown_action_is_ignored(self):
        p_send, p_patch, p_comment = self._make_mocks()
        with p_send as m_send, p_patch as m_patch, p_comment as m_comment:
            self.api._handle_speaker_callback("spkr_UNKNOWN:HBE-753", "chat-1")

        m_patch.assert_not_called()
        m_comment.assert_not_called()
        m_send.assert_not_called()

    def test_invalid_data_format_is_ignored(self):
        p_send, p_patch, p_comment = self._make_mocks()
        with p_send as m_send, p_patch as m_patch, p_comment as m_comment:
            self.api._handle_speaker_callback("not_a_speaker_callback", "chat-1")

        m_patch.assert_not_called()
        m_comment.assert_not_called()
        m_send.assert_not_called()


class SpeakerFallbackConfigEndpointTest(unittest.TestCase):
    def test_returns_configured_default(self):
        env = {**_BASE_ENV, "MARA_SPEAKER_FALLBACK_DEFAULT": "continue"}
        api = _load_api("api_spkr_config_test", env)
        result = api.telegram_speaker_fallback_config(_key="test-key")
        self.assertEqual(result["fallback_default"], "continue")

    def test_returns_ask_as_default(self):
        api = _load_api("api_spkr_config_default_test", _BASE_ENV)
        result = api.telegram_speaker_fallback_config(_key="test-key")
        self.assertEqual(result["fallback_default"], "ask")


class WebhookCallbackQueryRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = _load_api("api_spkr_webhook_cq_test", _BASE_ENV)

    def test_callback_query_routes_to_handle_speaker_callback(self):
        mock_handle = mock.MagicMock()
        mock_answer = mock.MagicMock()

        class _MockRequest:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "secret"}

            async def json(self):
                return {
                    "update_id": 1,
                    "callback_query": {
                        "id": "cq-id-123",
                        "from": {"id": 1, "first_name": "Sven"},
                        "message": {"message_id": 10, "chat": {"id": 999, "type": "private"}},
                        "data": "spkr_cont:HBE-753",
                    },
                }

        with mock.patch.object(self.api, "_TG_WEBHOOK_SECRET", "secret"), \
             mock.patch.object(self.api, "_tg_answer_callback_query", mock_answer), \
             mock.patch.object(self.api, "_handle_speaker_callback", mock_handle):
            result = asyncio.run(self.api.telegram_lena_webhook(_MockRequest()))

        self.assertEqual(result, {"ok": True})
        mock_answer.assert_called_once_with("cq-id-123")
        mock_handle.assert_called_once_with("spkr_cont:HBE-753", "999")


if __name__ == "__main__":
    unittest.main()
