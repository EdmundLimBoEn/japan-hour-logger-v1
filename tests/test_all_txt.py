from __future__ import annotations

from pathlib import Path

from radio_logger.wsjtx.all_txt import parse_all_txt, parse_all_txt_file


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
