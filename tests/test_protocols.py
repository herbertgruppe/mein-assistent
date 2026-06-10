"""
Tests für den Protokoll-Review-Workflow.

Teil 1: DB-Layer (ProtocolsDB) — direkt gegen temporäre SQLite-DB.
Teil 2: API-Endpoints — TestClient mit Temp-DB; Outlook/Asana/LLM gemockt.

Ausführen:
    pytest tests/test_protocols.py -v
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.protocols_db import ProtocolsDB


@pytest.fixture
def db(tmp_path):
    """Frische ProtocolsDB in temporärem Verzeichnis."""
    return ProtocolsDB(db_path=str(tmp_path / "protocols_test.db"))


@pytest.fixture
def draft(db):
    """Standard-Draft für Tests."""
    result = db.create_draft(
        markdown="# Test\n## TOP 1\nTest-Inhalt",
        meeting_name="1:1 KNE/SH",
        meeting_datetime="2026-05-12T13:15:00+02:00",
        source="plaud-poller",
        teilnehmer=["Sven Herbert", "Tim Kneusels"],
        reviewer_emails=["sven.herbert@herbert.de"],
        ablageort="03 Bereiche/Mitarbeiterführung/Tim Kneusels/Protokolle",
        recording_id="plaud-rec-abc-123",
    )
    return result


# ---------------------------------------------------------------------------
# DB-Layer
# ---------------------------------------------------------------------------
class TestCreateDraft:
    def test_draft_create(self, db, draft):
        """create_draft gibt id, reviewer_token und expires_at zurück."""
        assert draft["id"]
        assert draft["reviewer_token"]
        assert len(draft["reviewer_token"]) >= 32
        assert draft["expires_at"]

        stored = db.get_by_id(draft["id"])
        assert stored is not None
        assert stored["meeting_name"] == "1:1 KNE/SH"
        assert stored["status"] == "draft"
        assert stored["draft_markdown"] == stored["current_markdown"]
        assert stored["teilnehmer"] == ["Sven Herbert", "Tim Kneusels"]
        assert stored["reviewer_emails"] == ["sven.herbert@herbert.de"]

    def test_draft_create_without_event_id(self, db):
        """event_id=None ist erlaubt (Pflichtfeld erst bei approve)."""
        result = db.create_draft(
            markdown="# Minimal",
            meeting_name="Test-Meeting",
            meeting_datetime="2026-06-10T14:00:00+02:00",
            source="manual",
        )
        stored = db.get_by_id(result["id"])
        assert stored["event_id"] is None
        assert stored["asana_board_gid"] is None
        assert stored["asana_section_gid"] is None

    def test_expires_at_30_days(self, db, draft):
        """Token läuft nach 30 Tagen ab."""
        expires = datetime.fromisoformat(draft["expires_at"])
        delta = expires - datetime.now(timezone.utc)
        assert timedelta(days=29) < delta <= timedelta(days=30)

    def test_tokens_unique(self, db):
        """Jeder Draft bekommt einen eigenen Token."""
        tokens = set()
        for i in range(5):
            r = db.create_draft(
                markdown="x",
                meeting_name=f"M{i}",
                meeting_datetime="2026-06-10T14:00:00+02:00",
                source="manual",
            )
            tokens.add(r["reviewer_token"])
        assert len(tokens) == 5


class TestGetByToken:
    def test_get_by_token(self, db, draft):
        stored = db.get_by_token(draft["reviewer_token"])
        assert stored is not None
        assert stored["id"] == draft["id"]

    def test_unknown_token(self, db):
        assert db.get_by_token("invalid-token") is None

    def test_token_expired(self, db, draft):
        """Token mit expires_at in der Vergangenheit → is_expired True."""
        stored = db.get_by_token(draft["reviewer_token"])
        assert ProtocolsDB.is_expired(stored) is False

        # expires_at künstlich in die Vergangenheit setzen
        with db._get_connection() as conn:
            conn.execute(
                "UPDATE protocols SET expires_at = ? WHERE id = ?",
                (
                    (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                    draft["id"],
                ),
            )
            conn.commit()

        stored = db.get_by_token(draft["reviewer_token"])
        assert ProtocolsDB.is_expired(stored) is True


class TestUpdateMarkdown:
    def test_patch_markdown(self, db, draft):
        """update_markdown speichert neuen Stand, draft_markdown bleibt."""
        ok = db.update_markdown(draft["id"], "# Geändert", modified_by="sven")
        assert ok is True

        stored = db.get_by_id(draft["id"])
        assert stored["current_markdown"] == "# Geändert"
        assert stored["draft_markdown"] == "# Test\n## TOP 1\nTest-Inhalt"
        assert stored["last_modified_by"] == "sven"

    def test_update_unknown_id(self, db):
        assert db.update_markdown("no-such-id", "x") is False


class TestStatusFlow:
    def test_set_status_in_review(self, db, draft):
        assert db.set_status(draft["id"], "in_review") is True
        assert db.get_by_id(draft["id"])["status"] == "in_review"

    def test_set_status_invalid(self, db, draft):
        with pytest.raises(ValueError):
            db.set_status(draft["id"], "kaputt")

    def test_approve_stores_selections(self, db, draft):
        """set_approved speichert event_id, board_gid, section_gid."""
        ok = db.set_approved(
            draft["id"],
            event_id="AAMkAGI-test",
            asana_board_gid="1234567890",
            asana_section_gid="9876543210",
            approved_by="sven",
        )
        assert ok is True

        stored = db.get_by_id(draft["id"])
        assert stored["status"] == "approved"
        assert stored["event_id"] == "AAMkAGI-test"
        assert stored["asana_board_gid"] == "1234567890"
        assert stored["asana_section_gid"] == "9876543210"
        assert stored["approved_at"] is not None

    def test_approve_without_asana(self, db, draft):
        """Checkbox aus: Approve ohne Board/Section ist erlaubt."""
        ok = db.set_approved(
            draft["id"], event_id="AAMkAGI-test", create_asana_task=False
        )
        assert ok is True

        stored = db.get_by_id(draft["id"])
        assert stored["status"] == "approved"
        assert stored["create_asana_task"] is False
        assert stored["asana_board_gid"] is None
        assert stored["asana_section_gid"] is None

    def test_create_asana_task_default_true(self, db, draft):
        """Neue Drafts haben create_asana_task=True als Default."""
        stored = db.get_by_id(draft["id"])
        assert stored["create_asana_task"] is True

    def test_set_finalized(self, db, draft):
        db.set_approved(draft["id"], "ev", "board", "section")
        ok = db.set_finalized(
            draft["id"],
            asana_task_gid="task-123",
            asana_task_url="https://app.asana.com/0/x/task-123",
        )
        assert ok is True

        stored = db.get_by_id(draft["id"])
        assert stored["status"] == "finalized"
        assert stored["finalized_at"] is not None
        assert stored["asana_task_gid"] == "task-123"

    def test_finalization_error_resets_to_in_review(self, db, draft):
        """Bei Fehler im Hintergrund-Job: zurück auf in_review + Fehlertext."""
        db.set_approved(draft["id"], "ev", "board", "section")
        ok = db.set_finalization_error(draft["id"], "Outlook-Token abgelaufen")
        assert ok is True

        stored = db.get_by_id(draft["id"])
        assert stored["status"] == "in_review"
        assert stored["finalization_error"] == "Outlook-Token abgelaufen"

    def test_reapprove_clears_finalization_error(self, db, draft):
        """Erneute Freigabe löscht alten Fehlertext."""
        db.set_approved(draft["id"], "ev", "board", "section")
        db.set_finalization_error(draft["id"], "Fehler X")
        db.set_approved(draft["id"], "ev", "board", "section")
        assert db.get_by_id(draft["id"])["finalization_error"] is None

    def test_set_rejected(self, db, draft):
        ok = db.set_rejected(draft["id"], "Inhalt unvollständig", rejected_by="sven")
        assert ok is True

        stored = db.get_by_id(draft["id"])
        assert stored["status"] == "rejected"
        assert stored["rejection_reason"] == "Inhalt unvollständig"


class TestListFinalized:
    def test_finalized_list(self, db):
        """list_finalized_since gibt nur finalisierte zurück, ab `since`."""
        ids = []
        for i in range(3):
            r = db.create_draft(
                markdown=f"# P{i}",
                meeting_name=f"Meeting {i}",
                meeting_datetime="2026-06-10T14:00:00+02:00",
                source="manual",
            )
            ids.append(r["id"])

        # Nur die ersten beiden finalisieren
        for draft_id in ids[:2]:
            db.set_approved(draft_id, "ev", "board", "section")
            db.set_finalized(draft_id)

        result = db.list_finalized_since()
        assert len(result) == 2
        assert all(p["status"] == "finalized" for p in result)

        # since in der Zukunft → leer
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        assert db.list_finalized_since(since=future) == []

        # since in der Vergangenheit → beide
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        assert len(db.list_finalized_since(since=past)) == 2

    def test_finalized_list_limit(self, db):
        for i in range(5):
            r = db.create_draft(
                markdown="x",
                meeting_name=f"M{i}",
                meeting_datetime="2026-06-10T14:00:00+02:00",
                source="manual",
            )
            db.set_approved(r["id"], "ev", "b", "s")
            db.set_finalized(r["id"])
        assert len(db.list_finalized_since(limit=3)) == 3


# ===========================================================================
# Teil 2: API-Endpoints
# ===========================================================================
TEST_API_KEY = "test-secret-key"
KEY_HEADER = {"X-API-Key": TEST_API_KEY}

DRAFT_PAYLOAD = {
    "markdown": "# Test\n## TOP 1\nTest-Inhalt",
    "meeting_name": "1:1 KNE/SH",
    "meeting_datetime": "2026-06-10T14:00:00+02:00",
    "teilnehmer": ["Sven Herbert", "Tim Kneusels"],
    "source": "manual",
    "reviewer_emails": ["sven.herbert@herbert.de"],
}


class _FakeAsanaAgent:
    """Minimaler AsanaAgent-Ersatz mit Call-Zähler für Cache-Tests."""

    def __init__(self):
        self.list_projects_calls = 0
        self.sections_calls = 0

    def is_connected(self):
        return True

    def list_projects(self, limit=None):
        self.list_projects_calls += 1
        return [{"gid": "111", "name": "1:1 KNE/SH"}, {"gid": "222", "name": "HBO"}]

    def get_project_sections(self, project_gid):
        self.sections_calls += 1
        return [{"gid": "s1", "name": "Protokolle"}, {"gid": "s2", "name": "Backlog"}]


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    """
    TestClient mit frischer Temp-DB. Outlook/Asana/LLM komplett gemockt:
      - process_reviewed_protocol → Erfolg (kein Outlook-Call)
      - _create_asana_protocol_task → fester Task-GID
      - _get_asana_agent → _FakeAsanaAgent
    """
    import api
    from fastapi.testclient import TestClient

    db = ProtocolsDB(db_path=str(tmp_path / "protocols_api_test.db"))
    monkeypatch.setattr(api, "_protocols_db", db)
    monkeypatch.setattr(api, "_API_SECRET_KEY", TEST_API_KEY)
    monkeypatch.setattr(api, "_asana_cache", {})

    fake_agent = _FakeAsanaAgent()
    monkeypatch.setattr(api, "_get_asana_agent", lambda: fake_agent)

    class _OkResult:
        success = True
        errors = []

    monkeypatch.setattr(
        api, "process_reviewed_protocol", lambda req, _key=None: _OkResult()
    )
    monkeypatch.setattr(
        api,
        "_create_asana_protocol_task",
        lambda protocol: ("task-gid-1", "https://app.asana.com/task-gid-1"),
    )

    client = TestClient(api.app)
    return {"api": api, "client": client, "db": db, "asana": fake_agent}


def _create_draft(api_env, **overrides):
    payload = {**DRAFT_PAYLOAD, **overrides}
    resp = api_env["client"].post(
        "/api/protocols/draft", json=payload, headers=KEY_HEADER
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _token_of(api_env, draft_id):
    with api_env["db"]._get_connection() as conn:
        row = conn.execute(
            "SELECT reviewer_token FROM protocols WHERE id = ?", (draft_id,)
        ).fetchone()
    return row["reviewer_token"]


class TestDraftEndpoint:
    def test_draft_create(self, api_env):
        """POST /api/protocols/draft gibt draft_id und reviewer_url zurück."""
        data = _create_draft(api_env)
        assert data["draft_id"]
        assert "/review/" in data["reviewer_url"]
        assert data["expires_at"]

    def test_draft_create_without_event_id(self, api_env):
        """event_id=null ist erlaubt (Pflichtfeld erst bei approve)."""
        data = _create_draft(api_env, event_id=None)
        stored = api_env["db"].get_by_id(data["draft_id"])
        assert stored["event_id"] is None

    def test_draft_create_requires_api_key(self, api_env):
        resp = api_env["client"].post("/api/protocols/draft", json=DRAFT_PAYLOAD)
        assert resp.status_code in (401, 403)


class TestReviewPage:
    def test_get_review_page(self, api_env):
        data = _create_draft(api_env)
        token = _token_of(api_env, data["draft_id"])
        resp = api_env["client"].get(f"/review/{token}")
        assert resp.status_code == 200
        assert "1:1 KNE/SH" in resp.text
        # Aufruf setzt draft → in_review
        assert api_env["db"].get_by_id(data["draft_id"])["status"] == "in_review"

    def test_get_review_page_unknown_token(self, api_env):
        """GET /review/invalid-token → 404 mit review_error.html."""
        resp = api_env["client"].get("/review/invalid-token")
        assert resp.status_code == 404
        assert "ungültig" in resp.text

    def test_token_expired(self, api_env):
        """Token mit expires_at in der Vergangenheit → 410."""
        data = _create_draft(api_env)
        token = _token_of(api_env, data["draft_id"])
        with api_env["db"]._get_connection() as conn:
            conn.execute(
                "UPDATE protocols SET expires_at = ? WHERE id = ?",
                (
                    (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                    data["draft_id"],
                ),
            )
            conn.commit()
        resp = api_env["client"].get(f"/review/{token}")
        assert resp.status_code == 410
        assert "abgelaufen" in resp.text


class TestGetAndPatch:
    def test_get_draft_with_token(self, api_env):
        data = _create_draft(api_env)
        token = _token_of(api_env, data["draft_id"])
        resp = api_env["client"].get(
            f"/api/protocols/{data['draft_id']}?token={token}"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["meeting_name"] == "1:1 KNE/SH"
        assert "reviewer_token" not in body

    def test_get_draft_wrong_token(self, api_env):
        data = _create_draft(api_env)
        resp = api_env["client"].get(
            f"/api/protocols/{data['draft_id']}?token=falsch"
        )
        assert resp.status_code == 403

    def test_patch_markdown(self, api_env):
        """PATCH /api/protocols/{id}?token=... → 200, Markdown gespeichert."""
        data = _create_draft(api_env)
        token = _token_of(api_env, data["draft_id"])
        resp = api_env["client"].patch(
            f"/api/protocols/{data['draft_id']}?token={token}",
            json={"markdown": "# Geändert"},
        )
        assert resp.status_code == 200
        stored = api_env["db"].get_by_id(data["draft_id"])
        assert stored["current_markdown"] == "# Geändert"
        assert stored["draft_markdown"] == DRAFT_PAYLOAD["markdown"]


class TestApprove:
    def _approve(self, api_env, draft_id, token, body=None):
        default = {
            "event_id": "AAMkAGI-test",
            "create_asana_task": True,
            "asana_board_gid": "111",
            "asana_section_gid": "s1",
        }
        return api_env["client"].post(
            f"/api/protocols/{draft_id}/approve?token={token}",
            json=body if body is not None else default,
        )

    def test_approve_returns_202(self, api_env):
        """POST .../approve gibt sofort 202 zurück."""
        data = _create_draft(api_env)
        token = _token_of(api_env, data["draft_id"])
        resp = self._approve(api_env, data["draft_id"], token)
        assert resp.status_code == 202
        assert resp.json()["status"] == "approved"

    def test_approve_stores_selections(self, api_env):
        """approve speichert event_id, board_gid, section_gid in der DB."""
        data = _create_draft(api_env)
        token = _token_of(api_env, data["draft_id"])
        self._approve(api_env, data["draft_id"], token)
        stored = api_env["db"].get_by_id(data["draft_id"])
        assert stored["event_id"] == "AAMkAGI-test"
        assert stored["asana_board_gid"] == "111"
        assert stored["asana_section_gid"] == "s1"

    def test_approve_triggers_background_finalization(self, api_env):
        """BackgroundTask läuft (TestClient: nach Response) → finalized."""
        data = _create_draft(api_env)
        token = _token_of(api_env, data["draft_id"])
        self._approve(api_env, data["draft_id"], token)
        stored = api_env["db"].get_by_id(data["draft_id"])
        assert stored["status"] == "finalized"
        assert stored["asana_task_gid"] == "task-gid-1"

    def test_approve_missing_event_id(self, api_env):
        """approve ohne event_id → 422 Validation Error."""
        data = _create_draft(api_env)
        token = _token_of(api_env, data["draft_id"])
        resp = self._approve(
            api_env, data["draft_id"], token, body={"create_asana_task": False}
        )
        assert resp.status_code == 422

    def test_approve_asana_checked_requires_board(self, api_env):
        """Checkbox an, aber kein Board/Section → 422."""
        data = _create_draft(api_env)
        token = _token_of(api_env, data["draft_id"])
        resp = self._approve(
            api_env,
            data["draft_id"],
            token,
            body={"event_id": "ev", "create_asana_task": True},
        )
        assert resp.status_code == 422

    def test_approve_without_asana_allowed(self, api_env):
        """Checkbox aus → Board/Section nicht nötig, kein Asana-Task."""
        data = _create_draft(api_env)
        token = _token_of(api_env, data["draft_id"])
        resp = self._approve(
            api_env,
            data["draft_id"],
            token,
            body={"event_id": "ev", "create_asana_task": False},
        )
        assert resp.status_code == 202
        stored = api_env["db"].get_by_id(data["draft_id"])
        assert stored["status"] == "finalized"
        assert stored["asana_task_gid"] is None

    def test_approve_twice_conflict(self, api_env):
        """Doppelte Freigabe → 409."""
        data = _create_draft(api_env)
        token = _token_of(api_env, data["draft_id"])
        assert self._approve(api_env, data["draft_id"], token).status_code == 202
        assert self._approve(api_env, data["draft_id"], token).status_code == 409

    def test_finalization_error_resets_status(self, api_env, monkeypatch):
        """Outlook-Fehler im Hintergrund-Job → in_review + finalization_error."""
        api = api_env["api"]

        class _FailResult:
            success = False
            errors = ["Outlook nicht authentifiziert"]

        monkeypatch.setattr(
            api, "process_reviewed_protocol", lambda req, _key=None: _FailResult()
        )
        data = _create_draft(api_env)
        token = _token_of(api_env, data["draft_id"])
        self._approve(api_env, data["draft_id"], token)
        stored = api_env["db"].get_by_id(data["draft_id"])
        assert stored["status"] == "in_review"
        assert "Outlook" in stored["finalization_error"]


class TestReject:
    def test_reject(self, api_env):
        data = _create_draft(api_env)
        token = _token_of(api_env, data["draft_id"])
        resp = api_env["client"].post(
            f"/api/protocols/{data['draft_id']}/reject?token={token}",
            json={"reason": "Unvollständig"},
        )
        assert resp.status_code == 200
        stored = api_env["db"].get_by_id(data["draft_id"])
        assert stored["status"] == "rejected"
        assert stored["rejection_reason"] == "Unvollständig"


class TestFinalizedEndpoint:
    def test_finalized_list(self, api_env):
        """GET /api/protocols/finalized?since=... gibt nur finalisierte zurück."""
        # Einen finalisieren, einen nicht
        d1 = _create_draft(api_env)
        t1 = _token_of(api_env, d1["draft_id"])
        api_env["client"].post(
            f"/api/protocols/{d1['draft_id']}/approve?token={t1}",
            json={
                "event_id": "ev",
                "create_asana_task": True,
                "asana_board_gid": "111",
                "asana_section_gid": "s1",
            },
        )
        _create_draft(api_env)  # bleibt draft

        resp = api_env["client"].get("/api/protocols/finalized", headers=KEY_HEADER)
        assert resp.status_code == 200
        protocols = resp.json()["protocols"]
        assert len(protocols) == 1
        p = protocols[0]
        assert p["id"] == d1["draft_id"]
        assert p["frontmatter"]["asana_protokoll_task_gid"] == "task-gid-1"
        assert p["frontmatter"]["teilnehmer"] == DRAFT_PAYLOAD["teilnehmer"]

        # since in der Zukunft → leer
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        resp = api_env["client"].get(
            f"/api/protocols/finalized?since={future}", headers=KEY_HEADER
        )
        assert resp.json()["protocols"] == []


class TestAsanaEndpoints:
    def test_asana_boards_cached(self, api_env):
        """Zwei Board-Calls → nur ein Asana-API-Call (15-Min-Cache)."""
        c = api_env["client"]
        r1 = c.get("/api/asana/boards", headers=KEY_HEADER)
        r2 = c.get("/api/asana/boards", headers=KEY_HEADER)
        assert r1.status_code == r2.status_code == 200
        assert r1.json() == [
            {"gid": "111", "name": "1:1 KNE/SH"},
            {"gid": "222", "name": "HBO"},
        ]
        assert api_env["asana"].list_projects_calls == 1

    def test_asana_sections(self, api_env):
        resp = api_env["client"].get(
            "/api/asana/boards/111/sections", headers=KEY_HEADER
        )
        assert resp.status_code == 200
        assert {"gid": "s1", "name": "Protokolle"} in resp.json()

    def test_asana_boards_with_reviewer_token(self, api_env):
        """Reviewer-Token als dritter Auth-Weg für Dropdown-Daten."""
        data = _create_draft(api_env)
        token = _token_of(api_env, data["draft_id"])
        resp = api_env["client"].get(f"/api/asana/boards?token={token}")
        assert resp.status_code == 200

    def test_asana_boards_unauthorized(self, api_env):
        resp = api_env["client"].get("/api/asana/boards")
        assert resp.status_code == 401

    def test_asana_boards_authentik_header(self, api_env):
        resp = api_env["client"].get(
            "/api/asana/boards", headers={"X-Authentik-Username": "sven"}
        )
        assert resp.status_code == 200


class TestCalendarDualAuth:
    def test_calendar_requires_auth(self, api_env):
        resp = api_env["client"].get("/api/calendar/events")
        assert resp.status_code == 401
