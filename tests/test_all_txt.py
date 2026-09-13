from __future__ import annotations

from pathlib import Path
import json

import pytest

from radio_logger.wsjtx.all_txt import parse_all_txt, parse_all_txt_file, parse_jsonl_line


def test_sample_all_txt_parses():
    path = Path(__file__).parent / "fixtures" / "sample_all.txt"
    rows = parse_all_txt_file(path)
    assert len(rows) >= 40
    first = rows[0]
    assert first.raw_message == "CQ JA1XYZ PM95"
    assert first.snr_db == -12
    assert first.df == 1234
    assert first.dial_frequency_hz == 14_074_000
    assert first.source == "all_txt"


def test_compact_and_iso_formats():
    compact = parse_all_txt("230415_081530  -12  0.2 1234 ~  CQ JA1XYZ PM95\n")
    assert compact[0].raw_message == "CQ JA1XYZ PM95"
    iso = parse_all_txt("2023-04-15 08:15:30  14.074 Rx FT8  -12  0.2 1234 CQ JA1XYZ PM95\n")
    assert iso[0].decode_time_utc.year == 2023
    tx = parse_all_txt("230415_081530    14.074 Tx FT8      0  0.0 1500 CQ 9V1XX OJ11\n")
    assert tx == []


def test_signed_reports_and_utf8_bom():
    rows = parse_all_txt("\ufeff230415_081530 14.074 Rx FT8 +12 +0.2 1234 CQ JA1XYZ PM95\r\n")
    assert len(rows) == 1
    assert rows[0].snr_db == 12
    assert rows[0].dt == 0.2


def test_all_txt_uses_utf8_on_windows(tmp_path):
    source = tmp_path / "ALL.TXT"
    source.write_bytes("\ufeff230415_081530 14.074 Rx FT8 -12 0.2 1234 HELLO 日本\r\n".encode())
    assert parse_all_txt_file(source)[0].raw_message == "HELLO 日本"


def test_microwave_frequency_stays_in_mhz():
    rows = parse_all_txt("230415_081530 1296.174 Rx Q65 -12 0.2 1234 CQ JA1XYZ PM95")
    assert rows[0].dial_frequency_hz == 1_296_174_000


def test_bad_numeric_line_does_not_stop_following_decodes():
    line = "230415_081530 14.074 Rx FT8 -12 0.2 1234 CQ JA1XYZ PM95"
    corrupt = line.replace("14.074", "9" * 400 + ".0")
    rows = parse_all_txt(corrupt + "\n" + line)
    assert len(rows) == 1
    assert rows[0].raw_payload["all_txt_line"] == 2


def test_compact_free_text_tx_word_is_still_received():
    rows = parse_all_txt("230415_081530 -12 0.2 1234 ~ TX IS OFF")
    assert rows[0].raw_message == "TX IS OFF"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_jsonl_rejects_nonfinite_numeric_values(value):
    line = '{"time":"2026-09-14T12:00:00Z","message":"CQ JA1XYZ PM95","dt":' + value + '}'
    with pytest.raises(ValueError):
        parse_jsonl_line(line)


def test_jsonl_preserves_integer_precision():
    line = json.dumps({"time": "2026-09-14T12:00:00Z", "message": "CQ JA1XYZ PM95", "dial": 9_007_199_254_740_993})
    assert parse_jsonl_line(line).dial_frequency_hz == 9_007_199_254_740_993


def test_jsonl_requires_decode_timestamp():
    with pytest.raises(ValueError, match="timestamp"):
        parse_jsonl_line('{"message":"CQ JA1XYZ PM95"}')


def test_jsonl_does_not_cast_false_string_to_true():
    raw = parse_jsonl_line('{"time":"2026-09-14T12:00:00Z","message":"CQ JA1XYZ PM95","off_air":"false"}')
    assert raw.off_air is False


def test_jsonl_rejects_object_as_message():
    with pytest.raises(ValueError):
        parse_jsonl_line('{"time":"2026-09-14T12:00:00Z","message":{"bad":"data"}}')


@pytest.mark.parametrize("value", ["1.5", "1e400", "NaN", "true"])
def test_jsonl_rejects_invalid_integer_fields(value):
    line = '{"time":"2026-09-14T12:00:00Z","message":"CQ JA1XYZ PM95","df":' + value + '}'
    with pytest.raises(ValueError):
        parse_jsonl_line(line)


@pytest.mark.parametrize("value", ["-1", str(2**63), '"1e400000000"'])
def test_jsonl_rejects_invalid_dial_frequency(value):
    line = '{"time":"2026-09-14T12:00:00Z","message":"CQ JA1XYZ PM95","dial":' + value + '}'
    with pytest.raises(ValueError):
        parse_jsonl_line(line)


def test_jsonl_rejects_nonfinite_values_in_metadata():
    with pytest.raises(ValueError):
        parse_jsonl_line('{"time":"2026-09-14T12:00:00Z","message":"CQ JA1XYZ PM95","raw_payload":{"bad":1e400}}')


def test_jsonl_deep_nesting_is_a_recoverable_parse_error():
    with pytest.raises(ValueError, match="nesting"):
        parse_jsonl_line("[" * 2000 + "0" + "]" * 2000)
