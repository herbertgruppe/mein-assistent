"""
Regression tests for HBE-720: Auth/AuthZ and Supply-Chain security remediation.

Covers:
- Trust-Boundary: X-Forwarded-* headers from untrusted IPs are silently ignored (fail-closed)
- Trust-Boundary: trusted proxy IP allows X-Authentik-Username / X-Forwarded-Email
- Timing side-channel: all secret/token comparisons use hmac.compare_digest
- Supply-Chain: review.html uses local vendor path, no external CDN scripts
"""
import hmac
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class _MockClient:
    def __init__(self, host: str):
        self.host = host


class _MockRequest:
    def __init__(self, host: str = "1.2.3.4"):
        self.client = _MockClient(host)


def _call_get_auth(api, host, *, x_authentik_username=None, x_forwarded_email=None,
                   api_key=None, token=None):
    from fastapi import HTTPException
    req = _MockRequest(host=host)
    try:
        return api.get_authenticated_user(
            request=req,
            x_authentik_username=x_authentik_username,
            x_forwarded_email=x_forwarded_email,
            api_key=api_key,
            token=token,
        )
    except HTTPException as exc:
        return exc


# ---------------------------------------------------------------------------
# 1. Trust-Boundary: X-Forwarded-* header validation
# ---------------------------------------------------------------------------
class TestTrustedProxyBoundary:
    """X-Forwarded-* and X-Authentik-* headers must be silently ignored when the
    direct TCP peer is not in TRUSTED_PROXY_IPS (fail-closed per HBE-720)."""

    @pytest.fixture(autouse=True)
    def _load_api(self):
        import api
        self.api = api

    def test_forwarded_email_from_untrusted_ip_is_rejected(self, monkeypatch):
        """X-Forwarded-Email from untrusted IP must not authenticate."""
        monkeypatch.setattr(self.api, "_TRUSTED_PROXY_IPS", frozenset({"127.0.0.1"}))
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "")
        from fastapi import HTTPException
        result = _call_get_auth(self.api, host="1.2.3.4",
                                x_forwarded_email="attacker@evil.example")
        assert isinstance(result, HTTPException), "Untrusted IP must not authenticate via X-Forwarded-Email"
        assert result.status_code == 401

    def test_authentik_username_from_untrusted_ip_is_rejected(self, monkeypatch):
        """X-Authentik-Username from untrusted IP must not authenticate."""
        monkeypatch.setattr(self.api, "_TRUSTED_PROXY_IPS", frozenset({"127.0.0.1"}))
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "")
        from fastapi import HTTPException
        result = _call_get_auth(self.api, host="10.20.30.40",
                                x_authentik_username="admin")
        assert isinstance(result, HTTPException)
        assert result.status_code == 401

    def test_forwarded_email_from_trusted_proxy_authenticates(self, monkeypatch):
        """X-Forwarded-Email from trusted proxy IP must be accepted."""
        monkeypatch.setattr(self.api, "_TRUSTED_PROXY_IPS", frozenset({"127.0.0.1"}))
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "")
        result = _call_get_auth(self.api, host="127.0.0.1",
                                x_forwarded_email="sven.herbert@herbert.de")
        assert result == "sven.herbert@herbert.de"

    def test_authentik_username_from_trusted_proxy_authenticates(self, monkeypatch):
        monkeypatch.setattr(self.api, "_TRUSTED_PROXY_IPS", frozenset({"127.0.0.1"}))
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "")
        result = _call_get_auth(self.api, host="127.0.0.1",
                                x_authentik_username="sven.herbert")
        assert result == "sven.herbert"

    def test_authentik_username_takes_precedence_over_forwarded_email(self, monkeypatch):
        """X-Authentik-Username is checked first."""
        monkeypatch.setattr(self.api, "_TRUSTED_PROXY_IPS", frozenset({"127.0.0.1"}))
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "")
        result = _call_get_auth(self.api, host="127.0.0.1",
                                x_authentik_username="real-user",
                                x_forwarded_email="other@email.com")
        assert result == "real-user"

    def test_api_key_works_from_untrusted_ip(self, monkeypatch):
        """API-Key auth must remain functional even from non-proxy IPs."""
        monkeypatch.setattr(self.api, "_TRUSTED_PROXY_IPS", frozenset({"127.0.0.1"}))
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "correct-key")
        result = _call_get_auth(self.api, host="5.6.7.8", api_key="correct-key")
        assert result == "api-client"

    def test_wrong_api_key_from_untrusted_ip_is_rejected(self, monkeypatch):
        monkeypatch.setattr(self.api, "_TRUSTED_PROXY_IPS", frozenset({"127.0.0.1"}))
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "correct-key")
        from fastapi import HTTPException
        result = _call_get_auth(self.api, host="5.6.7.8", api_key="wrong-key")
        assert isinstance(result, HTTPException)
        assert result.status_code == 401

    def test_custom_trusted_proxy_ip_works(self, monkeypatch):
        """TRUSTED_PROXY_IPS can be set to arbitrary IPs via env var."""
        monkeypatch.setattr(self.api, "_TRUSTED_PROXY_IPS", frozenset({"10.0.0.1"}))
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "")
        # untrusted from 127.0.0.1 when it's not in the set
        from fastapi import HTTPException
        result = _call_get_auth(self.api, host="127.0.0.1",
                                x_forwarded_email="sven@herbert.de")
        assert isinstance(result, HTTPException)
        assert result.status_code == 401
        # trusted from 10.0.0.1
        result2 = _call_get_auth(self.api, host="10.0.0.1",
                                 x_forwarded_email="sven@herbert.de")
        assert result2 == "sven@herbert.de"


# ---------------------------------------------------------------------------
# 2. Timing side-channel: compare_digest on all secret comparisons
# ---------------------------------------------------------------------------
class TestTimingSafeComparisons:
    """All secret/token comparisons must use hmac.compare_digest (not ==)."""

    @pytest.fixture(autouse=True)
    def _load_api(self):
        import api
        self.api = api

    def _spy_compare_digest(self, monkeypatch):
        calls = []
        real = hmac.compare_digest

        def spy(a, b):
            calls.append((a, b))
            return real(a, b)

        monkeypatch.setattr(self.api.hmac, "compare_digest", spy)
        return calls

    # verify_api_key --------------------------------------------------------
    def test_verify_api_key_correct_key_accepted(self, monkeypatch):
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "correct")
        result = self.api.verify_api_key("correct")
        assert result == "correct"

    def test_verify_api_key_wrong_key_rejected(self, monkeypatch):
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "correct")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self.api.verify_api_key("wrong")
        assert exc_info.value.status_code == 403

    def test_verify_api_key_uses_compare_digest(self, monkeypatch):
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "correct")
        calls = self._spy_compare_digest(monkeypatch)
        try:
            self.api.verify_api_key("correct")
        except Exception:
            pass
        assert calls, "verify_api_key must call hmac.compare_digest"

    def test_verify_api_key_wrong_uses_compare_digest(self, monkeypatch):
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "correct")
        calls = self._spy_compare_digest(monkeypatch)
        from fastapi import HTTPException
        try:
            self.api.verify_api_key("wrong")
        except HTTPException:
            pass
        assert calls, "verify_api_key must call hmac.compare_digest even for wrong key"

    # _get_protocol_for_token: API key path --------------------------------
    def test_get_protocol_for_token_api_key_uses_compare_digest(self, monkeypatch, tmp_path):
        from database.protocols_db import ProtocolsDB
        db = ProtocolsDB(db_path=str(tmp_path / "test.db"))
        draft = db.create_draft(
            markdown="# T", meeting_name="M", meeting_datetime="2026-01-01T10:00:00",
            source="test", teilnehmer=[], reviewer_emails=[], ablageort="test/path",
        )
        monkeypatch.setattr(self.api, "_protocols_db", db)
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "secret")
        calls = self._spy_compare_digest(monkeypatch)
        result = self.api._get_protocol_for_token(draft["id"], token=None, allow_api_key="secret")
        assert result is not None
        assert any("secret" in pair for pair in calls), \
            "hmac.compare_digest must be called for API-key check in _get_protocol_for_token"

    def test_get_protocol_for_token_wrong_api_key_rejected(self, monkeypatch, tmp_path):
        from database.protocols_db import ProtocolsDB
        from fastapi import HTTPException
        db = ProtocolsDB(db_path=str(tmp_path / "test.db"))
        draft = db.create_draft(
            markdown="# T", meeting_name="M", meeting_datetime="2026-01-01T10:00:00",
            source="test", teilnehmer=[], reviewer_emails=[], ablageort="test/path",
        )
        monkeypatch.setattr(self.api, "_protocols_db", db)
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "secret")
        with pytest.raises(HTTPException) as exc_info:
            self.api._get_protocol_for_token(draft["id"], token=None, allow_api_key="wrong")
        assert exc_info.value.status_code == 401

    # _get_protocol_for_token: reviewer token path -------------------------
    def test_get_protocol_for_token_reviewer_token_uses_compare_digest(self, monkeypatch, tmp_path):
        from database.protocols_db import ProtocolsDB
        db = ProtocolsDB(db_path=str(tmp_path / "test.db"))
        draft = db.create_draft(
            markdown="# T", meeting_name="M", meeting_datetime="2026-01-01T10:00:00",
            source="test", teilnehmer=[], reviewer_emails=[], ablageort="test/path",
        )
        monkeypatch.setattr(self.api, "_protocols_db", db)
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "")
        real_token = draft["reviewer_token"]
        calls = self._spy_compare_digest(monkeypatch)
        result = self.api._get_protocol_for_token(draft["id"], token=real_token)
        assert result is not None
        assert any(c[0] == real_token for c in calls), \
            "hmac.compare_digest must be called for reviewer-token check"

    def test_get_protocol_for_token_wrong_token_rejected(self, monkeypatch, tmp_path):
        from database.protocols_db import ProtocolsDB
        from fastapi import HTTPException
        db = ProtocolsDB(db_path=str(tmp_path / "test.db"))
        draft = db.create_draft(
            markdown="# T", meeting_name="M", meeting_datetime="2026-01-01T10:00:00",
            source="test", teilnehmer=[], reviewer_emails=[], ablageort="test/path",
        )
        monkeypatch.setattr(self.api, "_protocols_db", db)
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "")
        with pytest.raises(HTTPException) as exc_info:
            self.api._get_protocol_for_token(draft["id"], token="definitely-wrong")
        assert exc_info.value.status_code == 403

    # get_authenticated_user: API key path ---------------------------------
    def test_get_authenticated_user_api_key_uses_compare_digest(self, monkeypatch):
        monkeypatch.setattr(self.api, "_TRUSTED_PROXY_IPS", frozenset({"127.0.0.1"}))
        monkeypatch.setattr(self.api, "_API_SECRET_KEY", "mykey")
        calls = self._spy_compare_digest(monkeypatch)
        result = _call_get_auth(self.api, host="5.6.7.8", api_key="mykey")
        assert result == "api-client"
        assert calls, "hmac.compare_digest must be called in get_authenticated_user"


# ---------------------------------------------------------------------------
# 3. Supply-Chain: review.html must not load from external CDN
# ---------------------------------------------------------------------------
class TestTemplateIntegrity:
    """review.html must reference SimpleMDE from local vendor path, not external CDN."""

    def test_no_cdn_references_in_review_html(self):
        """No external CDN domains allowed in review.html (supply-chain risk)."""
        template = (REPO_ROOT / "templates" / "review.html").read_text()
        assert "cdn.jsdelivr.net" not in template, \
            "review.html must not load from jsDelivr CDN"
        assert "unpkg.com" not in template, \
            "review.html must not load from unpkg CDN"
        assert "cdnjs.cloudflare.com" not in template, \
            "review.html must not load from cdnjs CDN"

    def test_simplemde_still_present_in_review_html(self):
        """SimpleMDE must still be loaded (from local path)."""
        template = (REPO_ROOT / "templates" / "review.html").read_text()
        assert "simplemde.min.js" in template
        assert "simplemde.min.css" in template

    def test_review_html_uses_local_vendor_path(self):
        template = (REPO_ROOT / "templates" / "review.html").read_text()
        assert "/static/vendor/simplemde/simplemde.min.js" in template
        assert "/static/vendor/simplemde/simplemde.min.css" in template

    def test_vendor_files_exist(self):
        vendor = REPO_ROOT / "static" / "vendor" / "simplemde"
        assert (vendor / "simplemde.min.js").exists(), \
            "simplemde.min.js must exist in static/vendor/simplemde/"
        assert (vendor / "simplemde.min.css").exists(), \
            "simplemde.min.css must exist in static/vendor/simplemde/"

    def test_vendor_js_is_not_empty(self):
        js = (REPO_ROOT / "static" / "vendor" / "simplemde" / "simplemde.min.js").stat().st_size
        assert js > 10_000, "Vendored simplemde.min.js appears too small — verify the download"
