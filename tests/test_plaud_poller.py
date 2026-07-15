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

from plaud_poller import _parse_recent_ids, _parse_file_metadata, _extract_duration_sec


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
