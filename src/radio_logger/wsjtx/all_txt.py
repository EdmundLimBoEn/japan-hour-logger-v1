from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from radio_logger.models import RawDecode

UTC = timezone.utc
log = logging.getLogger(__name__)

# 230415_081530    14.074 Rx FT8    -12  0.2 1234 CQ JA1XYZ PM95
CLASSIC = re.compile(
    r"""
    ^(?P<ymd>\d{6})_(?P<hms>\d{6})
    \s+(?P<freq>\d+(?:\.\d+)?)
    \s+(?P<dir>Rx|TX|Tx|tx)
    \s+(?P<mode>\S+)
    \s+(?P<snr>[+-]?\d+)
    \s+(?P<dt>[+-]?\d+(?:\.\d+)?)
    \s+(?P<df>-?\d+)
    \s+(?P<msg>.+?)\s*$
    """,
    re.VERBOSE,
)

# 230415_081530  -12  0.2 1234 ~  CQ JA1XYZ PM95
COMPACT = re.compile(
    r"""
    ^(?P<ymd>\d{6})_(?P<hms>\d{6})
    \s+(?P<snr>[+-]?\d+)
    \s+(?P<dt>[+-]?\d+(?:\.\d+)?)
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
    \s+(?P<snr>[+-]?\d+)
    \s+(?P<dt>[+-]?\d+(?:\.\d+)?)
    \s+(?P<df>-?\d+)
    \s+(?:~\s+)?(?P<msg>.+?)\s*$
    """,
    re.VERBOSE,
)


def parse_all_txt(text: str, *, default_dial_hz: int | None = None) -> list[RawDecode]:
    return list(iter_all_txt_lines(text.splitlines(), default_dial_hz=default_dial_hz))


def parse_all_txt_file(path: Path, *, default_dial_hz: int | None = None) -> list[RawDecode]:
    with path.open(encoding="utf-8-sig", errors="replace") as lines:
        return list(iter_all_txt_lines(lines, default_dial_hz=default_dial_hz))


def iter_all_txt_lines(
    lines: Iterator[str] | list[str],
    *,
    default_dial_hz: int | None = None,
) -> Iterator[RawDecode]:
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.lstrip("\ufeff").strip()
        if not line or line.startswith("#"):
            continue
        try:
            parsed = _parse_line(line, default_dial_hz=default_dial_hz)
        except (ValueError, TypeError, OverflowError) as exc:
            log.warning("skipping invalid ALL.TXT line %s: %s", line_no, exc)
            continue
        if parsed is None:
            continue
        parsed.raw_payload["all_txt_line"] = line_no
        parsed.raw_payload["all_txt_text"] = line
        yield parsed


def _parse_line(line: str, *, default_dial_hz: int | None) -> RawDecode | None:
    match = CLASSIC.match(line) or ISO.match(line) or COMPACT.match(line)
    if not match:
        return None
    data = match.groupdict()
    if data.get("dir", "Rx").lower() != "rx":
        return None
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
    if "." in value or freq < 1000:
        return int(round(freq * 1_000_000))
    return int(round(freq))


def parse_jsonl_line(line: str) -> RawDecode | None:
    line = line.lstrip("\ufeff").strip()
    if not line:
        return None
    try:
        data = json.loads(line, parse_float=_maybe_float, parse_constant=_reject_json_constant)
    except RecursionError as exc:
        raise ValueError("JSONL nesting is too deep") from exc
    _check_json_depth(data)
    if not isinstance(data, dict):
        return None
    message = data.get("message") or data.get("raw_message")
    if not message:
        return None
    if not isinstance(message, str):
        raise ValueError("JSONL message must be text")
    when = data.get("time") or data.get("decode_time_utc") or data.get("timestamp_utc")
    if isinstance(when, str):
        stamp = datetime.fromisoformat(when.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
    elif isinstance(when, datetime):
        stamp = when if when.tzinfo else when.replace(tzinfo=UTC)
    else:
        raise ValueError("JSONL decode requires a timestamp")
    return RawDecode(
        source="jsonl",
        instance_id=data.get("instance_id"),
        decode_time_utc=stamp,
        snr_db=_maybe_float(data.get("snr", data.get("snr_db"))),
        dt=_maybe_float(data.get("dt")),
        df=_maybe_int(data.get("df")),
        mode=data.get("mode") or "FT8",
        raw_message=message,
        low_confidence=data.get("low_confidence", False),
        off_air=data.get("off_air", False),
        dial_frequency_hz=_maybe_int(data.get("dial_frequency_hz") or data.get("dial")),
        raw_payload=data,
    )


def _maybe_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    result = float(value)
    if isinstance(value, bool) or not math.isfinite(result):
        raise ValueError("numeric value must be finite")
    return result


def _maybe_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("integer value required") from exc
    if not result.is_finite() or result != result.to_integral_value():
        raise ValueError("finite integer value required")
    if result < -(2**63) or result > 2**63 - 1:
        raise ValueError("integer exceeds signed 64-bit storage")
    return int(result)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON numeric constant {value}")


def _check_json_depth(data: object) -> None:
    pending = [(iter([data]), 0)]
    while pending:
        values, depth = pending[-1]
        try:
            value = next(values)
        except StopIteration:
            pending.pop()
            continue
        if isinstance(value, (dict, list)):
            if depth >= 64:
                raise ValueError("JSONL nesting is too deep")
            children = value.values() if isinstance(value, dict) else value
            pending.append((iter(children), depth + 1))
