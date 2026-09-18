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

from plaud_poller import (
    _parse_recent_ids,
    _parse_file_metadata,
    _extract_duration_sec,
    _id_variants,
    _init_db,
    _is_processed,
    _get_status,
    _mark_processed,
)


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
