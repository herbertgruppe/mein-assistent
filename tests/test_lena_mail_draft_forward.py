"""
Tests fuer POST /api/lena/mail/draft — Forward/Reply-Entwuerfe (HBE-3350).

Sven meldete: Bei "Weiterleiten"-Kategorisierungen wird ein Entwurf mit Text
und Anlage erstellt, aber die Outlook-Weiterleiten-Funktion wird nicht richtig
genutzt — der urspruengliche E-Mail-Text geht verloren.

Ursache: createForward/createReply legen automatisch einen Entwurf mit dem
zitierten Original darunter an. Wird der eigene Text ueber "message.body"
gesetzt (statt ueber das Feld "comment"), ERSETZT das den kompletten
Entwurfstext inklusive des automatisch eingefuegten Originals — dokumentiertes
Microsoft-Graph-Verhalten, comment und message.body schliessen sich aus. Die
Anlage blieb erhalten (sie gehoert zum Forward-Template, nicht zum Body),
weshalb der Fehler aussah wie "Text weg, Anhang da".
"""
import importlib.util
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_WEBHOOK_SECRET": "test-secret",
    "API_SECRET_KEY": "test-key",
}


def _load_api():
    api_path = REPO_ROOT / "api.py"
    spec = importlib.util.spec_from_file_location("api_draft_forward_test", api_path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, _BASE_ENV, clear=False):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def api():
    return _load_api()


class _OutlookOk:
    access_token = "fake-ms-token"

    def is_authenticated(self):
        return True


class _GraphResponse:
    def __init__(self, status=201, body=None):
        self.status_code = status
        self._body = body or {"id": "draft-123"}

    def json(self):
        return self._body

    @property
    def text(self):
        return str(self._body)


def _request(api, **overrides):
    defaults = dict(
        to=[api.LenaEmailAddress(name="Frank", email="f.herbert@herbert.de")],
        cc=[],
        subject="WG: Zahlungserinnerung 7193",
        body_text="Hallo Frank,\n\nhier geht es um die Rechnung.\n\nViele Grüße\nSven",
        reply_to_message_id="AAMk-original",
        mode="forward",
    )
    defaults.update(overrides)
    return api.LenaDraftRequest(**defaults)


def _post_forward(api, **overrides):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _GraphResponse()

    req = _request(api, **overrides)
    with mock.patch.object(api, "_get_outlook_tool", return_value=_OutlookOk()), \
         mock.patch("requests.post", side_effect=fake_post):
        api.lena_mail_draft(req, _key="test-key")
    return captured


# ── Der Kernfehler: comment statt message.body ────────────────────────────

def test_forward_nutzt_comment_nicht_message_body(api):
    """Der Fall aus Svens Meldung: Original-Text ging verloren."""
    captured = _post_forward(api)
    payload = captured["payload"]
    assert "comment" in payload, \
        "Ohne 'comment' ersetzt Graph den kompletten Entwurf und loescht das zitierte Original"
    assert payload["comment"] == (
        "Hallo Frank,\n\nhier geht es um die Rechnung.\n\nViele Grüße\nSven"
    )
    if "message" in payload:
        assert "body" not in payload["message"], \
            "message.body und comment schliessen sich bei createForward gegenseitig aus"


def test_forward_ruft_createforward_auf(api):
    captured = _post_forward(api)
    assert captured["url"].endswith("/messages/AAMk-original/createForward")


def test_forward_recipients_bleiben_erhalten(api):
    captured = _post_forward(api)
    empf = captured["payload"]["message"]["toRecipients"]
    assert empf == [{"emailAddress": {"name": "Frank", "address": "f.herbert@herbert.de"}}]


def test_forward_subject_wird_uebernommen(api):
    captured = _post_forward(api)
    assert captured["payload"]["message"]["subject"] == "WG: Zahlungserinnerung 7193"


# ── Reply: derselbe Fehler, nicht gemeldet aber identisch im Code ─────────

def test_reply_nutzt_ebenfalls_comment(api):
    captured = _post_forward(api, mode="reply", to=[])
    assert "comment" in captured["payload"]
    if "message" in captured["payload"]:
        assert "body" not in captured["payload"]["message"]


def test_reply_ohne_empfaenger_ueberschreibt_nicht_mit_leerer_liste(api):
    """
    Nebenfund: Der Poller uebergibt bei Antwort-Entwuerfen nie 'to' (der
    Empfaenger ist der urspruengliche Absender). Der alte Code schickte
    trotzdem "toRecipients": [] mit — das haette Graphs automatisch
    eingetragenen Antwort-Empfaenger geloescht. Ohne eigene Empfaenger darf
    das Feld schlicht fehlen, damit Graph seinen Standard-Empfaenger behaelt.
    """
    captured = _post_forward(api, mode="reply", to=[])
    msg = captured["payload"].get("message", {})
    assert "toRecipients" not in msg, \
        "Leere Empfaengerliste wuerde Graphs automatischen Antwort-Empfaenger loeschen"


def test_reply_mit_empfaenger_wird_trotzdem_gesetzt(api):
    captured = _post_forward(
        api, mode="reply",
        to=[api.LenaEmailAddress(name="Kundin", email="kundin@extern.de")],
    )
    assert captured["payload"]["message"]["toRecipients"] == [
        {"emailAddress": {"name": "Kundin", "address": "kundin@extern.de"}}
    ]


# ── Neuer Entwurf ohne reply_to_message_id bleibt unveraendert ────────────

def test_neuer_entwurf_ohne_bezug_nutzt_weiterhin_body(api):
    """Nur Forward/Reply haben das comment-vs-body-Problem — ein frischer
    Entwurf ohne Bezug zu einer Mail ist davon nicht betroffen."""
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _GraphResponse()

    req = api.LenaDraftRequest(
        to=[api.LenaEmailAddress(name="X", email="x@y.de")],
        subject="Neu", body_text="Text", reply_to_message_id=None,
    )
    with mock.patch.object(api, "_get_outlook_tool", return_value=_OutlookOk()), \
         mock.patch("requests.post", side_effect=fake_post):
        api.lena_mail_draft(req, _key="test-key")
    assert captured["url"] == "https://graph.microsoft.com/v1.0/me/messages"
    assert captured["payload"]["body"]["content"] == "Text"
