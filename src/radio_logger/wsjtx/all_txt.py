from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from radio_logger.models import RawDecode

UTC = timezone.utc

# 230415_081530    14.074 Rx FT8    -12  0.2 1234 CQ JA1XYZ PM95
CLASSIC = re.compile(
    r"""
    ^(?P<ymd>\d{6})_(?P<hms>\d{6})
    \s+(?P<freq>\d+(?:\.\d+)?)
    \s+(?P<dir>Rx|TX|Tx|tx)
    \s+(?P<mode>\S+)
    \s+(?P<snr>-?\d+)
    \s+(?P<dt>-?\d+(?:\.\d+)?)
    \s+(?P<df>-?\d+)
    \s+(?P<msg>.+?)\s*$
    """,
    re.VERBOSE,
)

# 230415_081530  -12  0.2 1234 ~  CQ JA1XYZ PM95
COMPACT = re.compile(
    r"""
    ^(?P<ymd>\d{6})_(?P<hms>\d{6})
    \s+(?P<snr>-?\d+)
    \s+(?P<dt>-?\d+(?:\.\d+)?)
    \s+(?P<df>-?\d+)
    \s+(?:~\s+)?(?P<msg>.+?)\s*$
    """,
    re.VERBOSE,
)

# 2023-04-15 08:15:30  14.074 Rx FT8  -12  0.2 1234 CQ JA1XYZ PM95
ISO = re.compile(
    r"""
    ^(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<hms>\d{2}:\d{2}:\d{2})
    \s+(?P<freq>\d+(?:\.\d+)?)
    \s+(?P<dir>Rx|TX|Tx|tx)
    \s+(?P<mode>\S+)
    \s+(?P<snr>-?\d+)
    \s+(?P<dt>-?\d+(?:\.\d+)?)
    \s+(?P<df>-?\d+)
    \s+(?:~\s+)?(?P<msg>.+?)\s*$
    """,
    re.VERBOSE,
)


def parse_all_txt(text: str, *, default_dial_hz: int | None = None) -> list[RawDecode]:
    return list(iter_all_txt_lines(text.splitlines(), default_dial_hz=default_dial_hz))


def parse_all_txt_file(path: Path, *, default_dial_hz: int | None = None) -> list[RawDecode]:
    with path.open(errors="replace") as lines:
        return list(iter_all_txt_lines(lines, default_dial_hz=default_dial_hz))


def iter_all_txt_lines(
    lines: Iterator[str] | list[str],
    *,
    default_dial_hz: int | None = None,
) -> Iterator[RawDecode]:
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = _parse_line(line, default_dial_hz=default_dial_hz)
        if parsed is None:
            continue
        parsed.raw_payload["all_txt_line"] = line_no
        parsed.raw_payload["all_txt_text"] = line
        yield parsed


def _parse_line(line: str, *, default_dial_hz: int | None) -> RawDecode | None:
    if re.search(r"\b(TX|Transmitting)\b", line, re.IGNORECASE) and not re.search(r"\bRx\b", line):
        return None
    match = CLASSIC.match(line) or ISO.match(line) or COMPACT.match(line)
    if not match:
        return None
    data = match.groupdict()
    when = _parse_stamp(data)
    if when is None:
        return None
    freq = data.get("freq")
    dial = _mhz_or_hz_to_hz(freq) if freq else default_dial_hz
    df = int(data["df"])
    return RawDecode(
        source="all_txt",
        instance_id=None,
        decode_time_utc=when,
        snr_db=float(data["snr"]),
        dt=float(data["dt"]),
        df=df,
        mode=data.get("mode") or "FT8",
        raw_message=data["msg"].strip(),
        dial_frequency_hz=dial,
        raw_payload={"format": "all_txt"},
    )


def _parse_stamp(data: dict[str, str]) -> datetime | None:
    try:
        if "date" in data and data.get("date"):
            return datetime.strptime(f"{data['date']} {data['hms']}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        ymd = data["ymd"]
        hms = data["hms"]
        year = int(ymd[0:2])
        year += 2000 if year < 80 else 1900
        month = int(ymd[2:4])
        day = int(ymd[4:6])
        hour = int(hms[0:2])
        minute = int(hms[2:4])
        second = int(hms[4:6])
        return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    except ValueError:
        return None


def _mhz_or_hz_to_hz(value: str) -> int:
    freq = float(value)
    if freq < 1000:
        return int(round(freq * 1_000_000))
    return int(round(freq))


def parse_jsonl_line(line: str) -> RawDecode | None:
    line = line.strip()
    if not line:
        return None
    data = json.loads(line)
    if not isinstance(data, dict):
        return None
    message = data.get("message") or data.get("raw_message")
    if not message:
        return None
    when = data.get("time") or data.get("decode_time_utc") or data.get("timestamp_utc")
    if isinstance(when, str):
        stamp = datetime.fromisoformat(when.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
    elif isinstance(when, datetime):
        stamp = when if when.tzinfo else when.replace(tzinfo=UTC)
    else:
        stamp = datetime.now(tz=UTC)
    return RawDecode(
        source="jsonl",
        instance_id=data.get("instance_id"),
        decode_time_utc=stamp,
        snr_db=_maybe_float(data.get("snr", data.get("snr_db"))),
        dt=_maybe_float(data.get("dt")),
        df=_maybe_int(data.get("df")),
        mode=data.get("mode") or "FT8",
        raw_message=str(message),
        low_confidence=bool(data.get("low_confidence", False)),
        off_air=bool(data.get("off_air", False)),
        dial_frequency_hz=_maybe_int(data.get("dial_frequency_hz") or data.get("dial")),
        raw_payload=data,
    )


def _maybe_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _maybe_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))
