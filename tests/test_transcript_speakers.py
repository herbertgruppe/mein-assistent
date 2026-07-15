"""Tests for HBE-276: /api/transcripts/{message_id}/speakers endpoint.

Tests _parse_transcript_speakers directly (no HTTP/Outlook needed).
"""
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]

_BASE_ENV = {
    "API_SECRET_KEY": "test-key",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_WEBHOOK_SECRET": "",
}


def _load_api(module_name: str):
    api_path = REPO_ROOT / "api.py"
    spec = importlib.util.spec_from_file_location(module_name, api_path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, _BASE_ENV, clear=False):
        spec.loader.exec_module(module)
    return module


_API = None


def _get_parse():
    global _API
    if _API is None:
        _API = _load_api("api_hbe276_test")
    return _API._parse_transcript_speakers


class ParseTranscriptSpeakersTest(unittest.TestCase):
    def parse(self, text):
        return _get_parse()(text)

    def test_speaker_n_format_basic(self):
        text = (
            "SPEAKER_1: Guten Morgen, ich wollte heute über das Budget sprechen.\n"
            "SPEAKER_2: Danke. Das klingt gut.\n"
            "SPEAKER_1: Wir haben drei offene Punkte.\n"
        )
        result = self.parse(text)
        self.assertEqual(len(result), 2)
        labels = [s.speaker_label for s in result]
        self.assertIn("SPEAKER_1", labels)
        self.assertIn("SPEAKER_2", labels)

    def test_sorted_by_utterance_count_desc(self):
        text = (
            "SPEAKER_1: Erster Punkt.\n"
            "SPEAKER_2: Zweiter Punkt.\n"
            "SPEAKER_1: Dritter Punkt.\n"
            "SPEAKER_1: Vierter Punkt.\n"
        )
        result = self.parse(text)
        self.assertEqual(result[0].speaker_label, "SPEAKER_1")
        self.assertEqual(result[0].utterance_count, 3)
        self.assertEqual(result[1].speaker_label, "SPEAKER_2")
        self.assertEqual(result[1].utterance_count, 1)

    def test_word_count(self):
        text = "SPEAKER_1: Guten Morgen zusammen wie geht es euch heute?\n"
        result = self.parse(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].total_words, 8)

    def test_probable_names_extracted_for_speaker_n(self):
        text = (
            "SPEAKER_2: Danke, Sven. Ich wollte mit Thomas sprechen.\n"
            "SPEAKER_2: Sven, hast du die Zahlen?\n"
        )
        result = self.parse(text)
        self.assertEqual(len(result), 1)
        names = result[0].probable_names
        # "Sven" appears twice — should be first
        self.assertIn("Sven", names)
        self.assertEqual(names[0], "Sven")

    def test_no_probable_names_for_named_speakers(self):
        text = (
            "Thomas Winzer: Guten Morgen.\n"
            "Sven Herbert: Danke, Thomas.\n"
        )
        result = self.parse(text)
        for s in result:
            self.assertEqual(s.probable_names, [],
                             f"{s.speaker_label} should have no probable_names")

    def test_empty_text_returns_empty_list(self):
        self.assertEqual(self.parse(""), [])

    def test_no_speaker_labels_returns_empty_list(self):
        text = "Das ist ein normaler Text ohne Speaker-Labels."
        self.assertEqual(self.parse(text), [])

    def test_named_format(self):
        text = (
            "Thomas Winzer: Guten Morgen zusammen.\n"
            "Sven Herbert: Danke, Thomas.\n"
            "Frank Herbert: Die Zahlen sehen gut aus.\n"
            "Thomas Winzer: Sehr gut.\n"
        )
        result = self.parse(text)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].speaker_label, "Thomas Winzer")
        self.assertEqual(result[0].utterance_count, 2)

    def test_body_text_with_transcript_embedded(self):
        """Body text contains transcript inside email boilerplate."""
        text = (
            "Hallo Sven,\n\nhier ist dein Transkript:\n\n"
            "SPEAKER_1: Guten Morgen.\n"
            "SPEAKER_2: Hallo.\n\n"
            "Beste Grüße\nPlaud Notes\n"
        )
        result = self.parse(text)
        self.assertEqual(len(result), 2)

    def test_mixed_content_no_500(self):
        """Partial speaker lines must not raise."""
        text = (
            "SPEAKER_1: Hallo.\n"
            "Das hier ist fließtext.\n"
            "SPEAKER_1: Noch ein Satz.\n"
        )
        result = self.parse(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].utterance_count, 2)


if __name__ == "__main__":
    unittest.main()
