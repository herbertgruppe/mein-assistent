"""Tests for plaud_poller parsing functions (HBE-853).

All three functions are pure data transforms — no I/O, no external deps.
"""
import json
import sys
import os

# Ensure the repo root is importable without installing any deps
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Patch required env vars before importing the module so startup validation passes
os.environ.setdefault("PAPERCLIP_COMPANY_ID_MA", "00000000-0000-0000-0000-000000000001")
os.environ.setdefault("PAPERCLIP_PROTOKOLL_AGENT_ID", "00000000-0000-0000-0000-000000000002")

import base64
import json as _json
import time

import plaud_poller
from plaud_poller import (
    _parse_recent_ids,
    _parse_file_metadata,
    _extract_duration_sec,
    _id_variants,
    _init_db,
    _is_processed,
    _get_status,
    _mark_processed,
    _parse_reported_count,
    _alert_throttled,
    _clear_alert,
    _check_silence,
    _check_refresh_token_expiry,
    _jwt_expiry,
    _meta_get,
    _meta_set,
)


def _jwt(exp_unix):
    """Minimal unsigned JWT carrying just an exp claim."""
    payload = base64.urlsafe_b64encode(_json.dumps({"exp": int(exp_unix)}).encode()).rstrip(b"=")
    return "hdr." + payload.decode() + ".sig"


# ── _parse_recent_ids ─────────────────────────────────────────────────────────

class TestParseRecentIds:
    def test_empty_string(self):
        assert _parse_recent_ids("") == []

    def test_whitespace_only(self):
        assert _parse_recent_ids("   \n  ") == []

    def test_json_array_of_dicts_with_id(self):
        data = [{"id": "a" * 32, "name": "rec1"}, {"id": "b" * 32}]
        result = _parse_recent_ids(json.dumps(data))
        assert result == ["a" * 32, "b" * 32]

    def test_json_array_of_dicts_fallback_to_recording_id(self):
        data = [{"recording_id": "c" * 32}]
        result = _parse_recent_ids(json.dumps(data))
        assert result == ["c" * 32]

    def test_json_array_of_dicts_fallback_to_uuid(self):
        data = [{"uuid": "d" * 32}]
        result = _parse_recent_ids(json.dumps(data))
        assert result == ["d" * 32]

    def test_json_array_of_strings(self):
        ids = ["e" * 32, "f" * 32]
        result = _parse_recent_ids(json.dumps(ids))
        assert result == ids

    def test_json_array_skips_short_strings(self):
        result = _parse_recent_ids(json.dumps(["short"]))
        assert result == []

    def test_json_array_dicts_skips_missing_id(self):
        data = [{"name": "no-id"}]
        result = _parse_recent_ids(json.dumps(data))
        assert result == []

    def test_invalid_json_falls_back_to_line_parse(self):
        valid_id = "a" * 32
        output = f"{valid_id} 2026-07-15 some name\njunk line"
        result = _parse_recent_ids(output)
        assert result == [valid_id]

    def test_line_parse_32_hex_chars(self):
        valid_id = "0123456789abcdef" * 2  # 32 chars
        result = _parse_recent_ids(valid_id)
        assert result == [valid_id]

    def test_line_parse_ignores_comment_lines(self):
        valid_id = "a" * 32
        output = f"# comment\n{valid_id}"
        result = _parse_recent_ids(output)
        assert result == [valid_id]

    def test_line_parse_rejects_31_char_token(self):
        short = "a" * 31
        result = _parse_recent_ids(short)
        assert result == []

    def test_line_parse_rejects_non_hex(self):
        not_hex = "g" * 32
        result = _parse_recent_ids(not_hex)
        assert result == []

    def test_multiple_ids_line_parse(self):
        ids = ["a" * 32, "b" * 32]
        output = "\n".join(ids)
        result = _parse_recent_ids(output)
        assert result == ids

    # ── prefixed IDs (Plaud CLI format change ~2026-09-15) ────────────────────

    def test_line_parse_accepts_prefixed_id(self):
        """Plaud emits `of_<32hex>` since ~2026-09-15 — must not be dropped."""
        prefixed = "of_" + "a" * 32
        assert _parse_recent_ids(prefixed) == [prefixed]

    def test_prefixed_id_returned_verbatim_not_stripped(self):
        """The CLI only resolves the exact spelling it printed."""
        prefixed = "of_cecd2ad55fff73d23ac083c1e2f7c646"
        output = f"  {prefixed}  09-14 Abstimmung: Heizungsanlage  2026-09-14  1h01m"
        assert _parse_recent_ids(output) == [prefixed]

    def test_line_parse_still_accepts_bare_id(self):
        """Backwards compatible in case Plaud reverts the format."""
        bare = "cecd2ad55fff73d23ac083c1e2f7c646"
        assert _parse_recent_ids(bare) == [bare]

    def test_real_world_recent_output(self):
        """Verbatim `plaud recent` output as of 2026-09-18."""
        output = (
            "- Fetching recordings from the last 7 days...\n"
            "\n"
            "Recordings in the last 7 days: 2\n"
            "\n"
            "  of_9e2f24bf7ca2f447b3b2bbf6ddca4d4d  09-18 Gespraech: Struktur  2026-09-18  3m21s\n"
            "  of_1dc2b2b20bd9f18a230d11f74aaabb6d  09-18 Abstimmung: TGA      2026-09-18  47m04s\n"
        )
        assert _parse_recent_ids(output) == [
            "of_9e2f24bf7ca2f447b3b2bbf6ddca4d4d",
            "of_1dc2b2b20bd9f18a230d11f74aaabb6d",
        ]

    def test_header_line_still_rejected(self):
        """The 'Recordings in the last 7 days: N' header must not parse as an ID."""
        assert _parse_recent_ids("Recordings in the last 7 days: 4") == []

    def test_rejects_prefix_without_hex_body(self):
        assert _parse_recent_ids("of_notahexstring") == []


# ── _id_variants / dedup across the format change ─────────────────────────────

class TestIdVariants:
    def test_prefixed_id_yields_both_spellings(self):
        assert _id_variants("of_" + "a" * 32) == ["of_" + "a" * 32, "a" * 32]

    def test_bare_id_yields_itself_only(self):
        assert _id_variants("a" * 32) == ["a" * 32]


class TestDedupAcrossFormatChange:
    def _db(self):
        return _init_db(":memory:")

    def test_fresh_db_accepts_write(self):
        """_init_db must provide every column _mark_processed writes."""
        conn = self._db()
        _mark_processed(conn, "e" * 32, "2026-09-18T10:00:00", "HBE-1", "/opt/x", recording_title="Titel")
        assert _is_processed(conn, "e" * 32) is True

    def test_bare_row_matches_prefixed_lookup(self):
        """The regression this fixes: recording processed pre-change, seen again post-change."""
        conn = self._db()
        bare = "cecd2ad55fff73d23ac083c1e2f7c646"
        _mark_processed(conn, bare, "2026-09-14T12:05:19", "HBE-3042", "/opt/x")
        assert _is_processed(conn, "of_" + bare) is True

    def test_prefixed_row_matches_prefixed_lookup(self):
        conn = self._db()
        prefixed = "of_" + "b" * 32
        _mark_processed(conn, prefixed, "2026-09-18T09:59:21", "HBE-3100", "/opt/x")
        assert _is_processed(conn, prefixed) is True

    def test_unknown_recording_is_not_processed(self):
        conn = self._db()
        assert _is_processed(conn, "of_" + "c" * 32) is False

    def test_status_lookup_matches_across_spellings(self):
        """Cancelled recordings must stay cancelled after the format change."""
        conn = self._db()
        bare = "d" * 32
        conn.execute(
            "INSERT INTO plaud_processed_recordings"
            " (recording_id, start_at, processed_at, issue_identifier, account_home, status)"
            " VALUES (?, '', '2026-09-01', 'HBE-1', '/opt/x', 'cancelled')",
            (bare,),
        )
        conn.commit()
        assert _get_status(conn, "of_" + bare) == "cancelled"


# ── _parse_file_metadata ──────────────────────────────────────────────────────

class TestParseFileMetadata:
    def test_empty_string(self):
        assert _parse_file_metadata("") == {}

    def test_json_object(self):
        data = {"name": "Test", "duration": 120}
        result = _parse_file_metadata(json.dumps(data))
        assert result == data

    def test_json_invalid_falls_back_to_key_value(self):
        output = "name: My Recording\nduration: 90"
        result = _parse_file_metadata(output)
        assert result["name"] == "My Recording"
        assert result["duration"] == "90"

    def test_key_value_normalises_keys(self):
        output = "Start At: 2026-07-15\nfile-size: 1024"
        result = _parse_file_metadata(output)
        assert "start_at" in result
        assert "file_size" in result

    def test_key_value_colon_in_value(self):
        output = "url: https://example.com/path"
        result = _parse_file_metadata(output)
        assert result["url"] == "https://example.com/path"

    def test_key_value_ignores_lines_without_colon(self):
        output = "no-colon-here\nkey: value"
        result = _parse_file_metadata(output)
        assert list(result.keys()) == ["key"]


# ── _extract_duration_sec ─────────────────────────────────────────────────────

class TestExtractDurationSec:
    def test_missing_key(self):
        assert _extract_duration_sec({}) == 0

    def test_integer_value(self):
        assert _extract_duration_sec({"duration": 300}) == 300

    def test_float_value(self):
        assert _extract_duration_sec({"duration": 90.5}) == 90

    def test_string_plain_number(self):
        assert _extract_duration_sec({"duration": "180"}) == 180

    def test_string_with_s_suffix(self):
        assert _extract_duration_sec({"duration": "45s"}) == 45

    def test_mm_ss_format(self):
        assert _extract_duration_sec({"duration": "2:30"}) == 150

    def test_hh_mm_ss_format(self):
        assert _extract_duration_sec({"duration": "1:02:03"}) == 3723

    def test_hh_mm_ss_with_float_seconds(self):
        assert _extract_duration_sec({"duration": "0:01:30.5"}) == 90

    def test_fallback_key_duration_sec(self):
        assert _extract_duration_sec({"duration_sec": 60}) == 60

    def test_fallback_key_length(self):
        assert _extract_duration_sec({"length": "1:00"}) == 60

    def test_unparseable_string(self):
        assert _extract_duration_sec({"duration": "unknown"}) == 0

    def test_empty_string(self):
        assert _extract_duration_sec({"duration": ""}) == 0


# ── Dead-man switch ───────────────────────────────────────────────────────────

class _AlertSpy:
    """Captures Telegram alerts instead of sending them."""

    def __init__(self, monkeypatch):
        self.sent = []
        monkeypatch.setattr(plaud_poller, "_tg_alert", lambda text: self.sent.append(text))


class TestParseReportedCount:
    def test_reads_count_from_header(self):
        assert _parse_reported_count("Recordings in the last 7 days: 4") == 4

    def test_singular_day(self):
        assert _parse_reported_count("Recordings in the last 1 day: 2") == 2

    def test_zero_is_not_none(self):
        """0 must be distinguishable from 'header absent'."""
        assert _parse_reported_count("Recordings in the last 7 days: 0") == 0

    def test_absent_header_returns_none(self):
        assert _parse_reported_count("of_" + "a" * 32) is None

    def test_finds_header_in_full_output(self):
        output = (
            "- Fetching recordings from the last 7 days...\n\n"
            "Recordings in the last 7 days: 4\n\n"
            "  of_9e2f24bf7ca2f447b3b2bbf6ddca4d4d  Titel  2026-09-18  3m21s\n"
        )
        assert _parse_reported_count(output) == 4


class TestAlertThrottling:
    def test_first_alert_is_sent(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        assert _alert_throttled(conn, "k", "boom") is True
        assert spy.sent == ["boom"]

    def test_second_alert_within_cooldown_is_suppressed(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        _alert_throttled(conn, "k", "boom")
        assert _alert_throttled(conn, "k", "boom again") is False
        assert len(spy.sent) == 1

    def test_distinct_keys_do_not_share_cooldown(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        _alert_throttled(conn, "a", "x")
        _alert_throttled(conn, "b", "y")
        assert len(spy.sent) == 2

    def test_alert_resends_after_cooldown_expires(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        _alert_throttled(conn, "k", "boom")
        stale = time.time() - (plaud_poller.ALERT_COOLDOWN_HOURS + 1) * 3600
        _meta_set(conn, "alert:k", str(stale))
        assert _alert_throttled(conn, "k", "boom") is True
        assert len(spy.sent) == 2

    def test_clear_alert_allows_immediate_resend(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        _alert_throttled(conn, "k", "boom")
        _clear_alert(conn, "k")
        assert _alert_throttled(conn, "k", "boom") is True
        assert len(spy.sent) == 2

    def test_corrupt_timestamp_does_not_block_alerting(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        _meta_set(conn, "alert:k", "not-a-number")
        assert _alert_throttled(conn, "k", "boom") is True


class TestSilenceWatchdog:
    def test_first_run_starts_clock_without_alerting(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        _check_silence(conn)
        assert spy.sent == []
        assert _meta_get(conn, "last_recording_seen") is not None

    def test_quiet_but_within_threshold_stays_silent(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        recent = time.time() - (plaud_poller.SILENCE_ALERT_HOURS - 1) * 3600
        _meta_set(conn, "last_recording_seen", str(recent))
        _check_silence(conn)
        assert spy.sent == []

    def test_alerts_past_threshold(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        old = time.time() - (plaud_poller.SILENCE_ALERT_HOURS + 1) * 3600
        _meta_set(conn, "last_recording_seen", str(old))
        _check_silence(conn)
        assert len(spy.sent) == 1
        assert "ohne neue Aufnahme" in spy.sent[0]

    def test_corrupt_timestamp_resets_instead_of_alerting(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        _meta_set(conn, "last_recording_seen", "garbage")
        _check_silence(conn)
        assert spy.sent == []


class TestTokenExpiryWatchdog:
    def test_healthy_token_stays_silent(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        tokens = {"refresh_token": _jwt(time.time() + 6 * 24 * 3600)}
        _check_refresh_token_expiry(conn, "/opt/x", tokens)
        assert spy.sent == []

    def test_warns_inside_window(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        tokens = {"refresh_token": _jwt(time.time() + 12 * 3600)}
        _check_refresh_token_expiry(conn, "/opt/x", tokens)
        assert len(spy.sent) == 1
        assert "laeuft ab" in spy.sent[0]
        assert "plaud login" in spy.sent[0]

    def test_expired_token_reports_as_dead(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        tokens = {"refresh_token": _jwt(time.time() - 3600)}
        _check_refresh_token_expiry(conn, "/opt/x", tokens)
        assert len(spy.sent) == 1
        assert "abgelaufen" in spy.sent[0]

    def test_unreadable_token_is_ignored(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        _check_refresh_token_expiry(conn, "/opt/x", {"refresh_token": "not-a-jwt"})
        assert spy.sent == []

    def test_missing_token_is_ignored(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        _check_refresh_token_expiry(conn, "/opt/x", {})
        assert spy.sent == []

    def test_recovery_clears_cooldown(self, monkeypatch):
        """After a renewed token, a later expiry must alert again immediately."""
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        _check_refresh_token_expiry(conn, "/opt/x", {"refresh_token": _jwt(time.time() + 12 * 3600)})
        _check_refresh_token_expiry(conn, "/opt/x", {"refresh_token": _jwt(time.time() + 6 * 24 * 3600)})
        _check_refresh_token_expiry(conn, "/opt/x", {"refresh_token": _jwt(time.time() + 12 * 3600)})
        assert len(spy.sent) == 2

    def test_per_account_isolation(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        tokens = {"refresh_token": _jwt(time.time() + 12 * 3600)}
        _check_refresh_token_expiry(conn, "/opt/a", tokens)
        _check_refresh_token_expiry(conn, "/opt/b", tokens)
        assert len(spy.sent) == 2


class TestParseMismatchWatchdog:
    """The regression that cost four days: CLI lists recordings, parser sees none."""

    def _poll(self, monkeypatch, recent_output, conn):
        monkeypatch.setattr(plaud_poller, "_auto_refresh_token", lambda *a, **k: None)
        monkeypatch.setattr(plaud_poller, "_run_plaud", lambda args, home, **k: recent_output)
        return plaud_poller._poll_account("/opt/x", "agent-1", conn)

    def test_unparseable_format_triggers_alert(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        # A future format change the current regex cannot handle.
        output = (
            "Recordings in the last 7 days: 2\n"
            "  REC-9e2f24bf7ca2f447b3b2bbf6ddca4d4d  Titel A  2026-09-18  3m21s\n"
            "  REC-1dc2b2b20bd9f18a230d11f74aaabb6d  Titel B  2026-09-18  47m04s\n"
        )
        self._poll(monkeypatch, output, conn)
        assert len(spy.sent) == 1
        assert "erkennt Aufnahmen nicht" in spy.sent[0]
        assert "meldet <b>2</b>" in spy.sent[0]

    def test_partial_loss_triggers_alert(self, monkeypatch):
        """Even losing one of three recordings must be reported."""
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        output = (
            "Recordings in the last 7 days: 3\n"
            f"  of_{'a' * 32}  Titel A  2026-09-18  3m21s\n"
            f"  of_{'b' * 32}  Titel B  2026-09-18  5m00s\n"
            "  BROKEN-ID-HERE  Titel C  2026-09-18  9m00s\n"
        )
        monkeypatch.setattr(plaud_poller, "_auto_refresh_token", lambda *a, **k: None)
        calls = {"n": 0}

        def fake_run(args, home, **k):
            if args[0] == "recent":
                return output
            calls["n"] += 1
            return "duration: 10m00s\nname: Titel\nstart_at: 2026-09-18T09:00:00"

        monkeypatch.setattr(plaud_poller, "_run_plaud", fake_run)
        monkeypatch.setattr(plaud_poller, "_create_pc_issue", lambda payload: "HBE-9999")
        plaud_poller._poll_account("/opt/x", "agent-1", conn)
        assert len(spy.sent) == 1
        assert "meldet <b>3</b>" in spy.sent[0]
        assert "erkannt wurden <b>2</b>" in spy.sent[0]

    def test_healthy_poll_stays_silent(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        output = "Recordings in the last 7 days: 0\n"
        self._poll(monkeypatch, output, conn)
        assert spy.sent == []

    def test_missing_header_does_not_alert(self, monkeypatch):
        """No count to compare against — stay quiet rather than guess."""
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        self._poll(monkeypatch, "some unexpected output\n", conn)
        assert spy.sent == []

    def test_recovery_clears_cooldown(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        conn = _init_db(":memory:")
        broken = "Recordings in the last 7 days: 1\n  REC-abc  Titel  2026-09-18  3m21s\n"
        self._poll(monkeypatch, broken, conn)
        self._poll(monkeypatch, "Recordings in the last 7 days: 0\n", conn)
        self._poll(monkeypatch, broken, conn)
        assert len(spy.sent) == 2


class TestTwoStageFlow:
    """Stufe 1 meldet nur — das Transkript bleibt bis zu Svens Bestätigung unberührt."""

    def _setup(self, monkeypatch, two_stage=True):
        monkeypatch.setattr(plaud_poller, "TWO_STAGE", two_stage)
        monkeypatch.setattr(plaud_poller, "MA_API_KEY", "test-key")
        monkeypatch.setattr(plaud_poller, "_auto_refresh_token", lambda *a, **k: None)
        cli_calls = []

        def fake_run(args, home, **k):
            cli_calls.append(args[0])
            if args[0] == "recent":
                return (
                    "Recordings in the last 7 days: 1\n"
                    f"  of_{'a' * 32}  Titel  2026-09-18  47m04s\n"
                )
            return "name: 09-18 Abstimmung: TGA\nduration: 47m04s\nstart_at: 2026-09-18T09:59:21"

        monkeypatch.setattr(plaud_poller, "_run_plaud", fake_run)
        return cli_calls

    def test_stage_one_does_not_fetch_transcript(self, monkeypatch):
        """Der Kern des Umbaus: kein `plaud summary` vor der Zuordnung."""
        cli_calls = self._setup(monkeypatch)
        monkeypatch.setattr(
            plaud_poller, "_create_assignment", lambda *a, **k: "assignment:abc"
        )
        conn = _init_db(":memory:")
        new_ids, created, skipped, errors = plaud_poller._poll_account("/opt/x", "agent", conn)

        assert "summary" not in cli_calls
        assert created == ["assignment:abc"]
        assert errors == []

    def test_legacy_mode_still_fetches_summary(self, monkeypatch):
        """Mit TWO_STAGE=false bleibt der alte Weg unverändert."""
        cli_calls = self._setup(monkeypatch, two_stage=False)
        monkeypatch.setattr(plaud_poller, "_create_pc_issue", lambda payload: "HBE-1")
        conn = _init_db(":memory:")
        plaud_poller._poll_account("/opt/x", "agent", conn)
        assert "summary" in cli_calls

    def test_recording_marked_processed_after_assignment(self, monkeypatch):
        """Ohne Vermerk würde die Aufnahme alle zehn Minuten erneut gemeldet."""
        self._setup(monkeypatch)
        monkeypatch.setattr(
            plaud_poller, "_create_assignment", lambda *a, **k: "assignment:abc"
        )
        conn = _init_db(":memory:")
        plaud_poller._poll_account("/opt/x", "agent", conn)
        assert _is_processed(conn, "of_" + "a" * 32) is True

    def test_failed_assignment_is_not_marked_processed(self, monkeypatch):
        """Sonst ginge die Aufnahme still verloren."""
        self._setup(monkeypatch)

        def boom(*a, **k):
            raise RuntimeError("API weg")

        monkeypatch.setattr(plaud_poller, "_create_assignment", boom)
        conn = _init_db(":memory:")
        _, created, _, errors = plaud_poller._poll_account("/opt/x", "agent", conn)

        assert created == []
        assert errors == ["of_" + "a" * 32]
        assert _is_processed(conn, "of_" + "a" * 32) is False


class TestCreateAssignment:
    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def test_sends_link_and_speaker_hint(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        monkeypatch.setattr(plaud_poller, "MA_API_KEY", "k")
        sent = {}

        def fake_post(url, **kwargs):
            sent.update(kwargs.get("json") or {})
            return self._Resp({
                "draft_id": "d1",
                "assignment_url": "https://mein-assistent.herbertgruppe.com/review/tok",
                "created": True,
            })

        monkeypatch.setattr(plaud_poller.requests, "post", fake_post)
        ref = plaud_poller._create_assignment("of_x", "09-18 Abstimmung: TGA", "2026-09-18T09:59:21", 2824)

        assert ref == "assignment:d1"
        assert sent["recording_title"] == "09-18 Abstimmung: TGA"
        assert len(spy.sent) == 1
        # Der Hinweis auf die Sprecherkorrektur ist der Zweck der Meldung
        assert "Sprecher" in spy.sent[0]
        assert "/review/tok" in spy.sent[0]
        assert "09-18 Abstimmung: TGA" in spy.sent[0]

    def test_known_recording_does_not_notify_again(self, monkeypatch):
        spy = _AlertSpy(monkeypatch)
        monkeypatch.setattr(plaud_poller, "MA_API_KEY", "k")
        monkeypatch.setattr(
            plaud_poller.requests, "post",
            lambda url, **k: self._Resp({"draft_id": "d1", "assignment_url": "u", "created": False}),
        )
        ref = plaud_poller._create_assignment("of_x", "Titel", "2026-09-18T09:59:21", 60)
        assert ref == "assignment:d1"
        assert spy.sent == []

    def test_missing_api_key_reports_failure(self, monkeypatch):
        monkeypatch.setattr(plaud_poller, "MA_API_KEY", "")
        assert plaud_poller._create_assignment("of_x", "T", "2026-09-18T09:59:21", 60) is None


class TestAssignmentReminderTrigger:
    class _Resp:
        def __init__(self, status=200, payload=None, text=""):
            self.status_code = status
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    def test_calls_endpoint_with_api_key(self, monkeypatch):
        monkeypatch.setattr(plaud_poller, "MA_API_KEY", "k")
        seen = {}

        def fake_post(url, **kwargs):
            seen["url"] = url
            seen["headers"] = kwargs.get("headers", {})
            return self._Resp(200, {"checked": 0, "reminded": []})

        monkeypatch.setattr(plaud_poller.requests, "post", fake_post)
        plaud_poller._trigger_assignment_reminders()

        assert seen["url"].endswith("/api/protocols/assignment-reminders")
        assert seen["headers"].get("X-API-Key") == "k"

    def test_without_api_key_does_nothing(self, monkeypatch):
        monkeypatch.setattr(plaud_poller, "MA_API_KEY", "")
        called = {"n": 0}
        monkeypatch.setattr(
            plaud_poller.requests, "post",
            lambda *a, **k: called.__setitem__("n", called["n"] + 1),
        )
        plaud_poller._trigger_assignment_reminders()
        assert called["n"] == 0

    def test_errors_do_not_escalate(self, monkeypatch):
        """Eine verpasste Erinnerung wird beim naechsten Zyklus nachgeholt —
        sie darf den Poll-Lauf nicht abbrechen."""
        monkeypatch.setattr(plaud_poller, "MA_API_KEY", "k")

        def boom(*a, **k):
            raise RuntimeError("API weg")

        monkeypatch.setattr(plaud_poller.requests, "post", boom)
        plaud_poller._trigger_assignment_reminders()  # darf nicht werfen

    def test_http_error_does_not_escalate(self, monkeypatch):
        monkeypatch.setattr(plaud_poller, "MA_API_KEY", "k")
        monkeypatch.setattr(
            plaud_poller.requests, "post",
            lambda *a, **k: self._Resp(503, {}, "Telegram nicht konfiguriert"),
        )
        plaud_poller._trigger_assignment_reminders()


class TestJwtExpiry:
    def test_reads_exp(self):
        assert _jwt_expiry(_jwt(1789741854)) == 1789741854

    def test_malformed_returns_none(self):
        assert _jwt_expiry("garbage") is None

    def test_missing_exp_returns_none(self):
        payload = base64.urlsafe_b64encode(b'{"sub":"x"}').rstrip(b"=").decode()
        assert _jwt_expiry(f"hdr.{payload}.sig") is None
