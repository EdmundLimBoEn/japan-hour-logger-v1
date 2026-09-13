#!/usr/bin/env python3
"""Verify live ALL.TXT ingestion and HTTP output without inserting observations.

Run with the installed logger's Python environment. SQLite opens in read-only mode.
HTTP status and health checks use the application's existing database write probe.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta, timezone
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import NamedTuple
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener

from radio_logger.bands import band_from_hz
from radio_logger.config import load_config
from radio_logger.wsjtx.all_txt import parse_all_txt


class DecodeKey(NamedTuple):
    timestamp_utc: datetime
    dial_frequency_hz: int | None
    snr_db: float | None
    dt: float | None
    df: int | None
    raw_message: str


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def decode_key(row) -> DecodeKey:
    stamp = datetime.fromisoformat(row["timestamp_utc"].replace("Z", "+00:00"))
    stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    return DecodeKey(
        stamp,
        int(row["dial_frequency_hz"]) if row["dial_frequency_hz"] not in (None, "") else None,
        float(row["snr_db"]) if row["snr_db"] not in (None, "") else None,
        float(row["dt"]) if row["dt"] not in (None, "") else None,
        int(row["df"]) if row["df"] not in (None, "") else None,
        row["raw_message"],
    )


def source_snapshot(path: Path) -> tuple[Counter[DecodeKey], int, int]:
    with path.open("rb") as source:
        payload = source.read(os.fstat(source.fileno()).st_size)
    end = payload.rfind(b"\n") + 1
    text = payload[:end].decode("utf-8")
    expected: Counter[DecodeKey] = Counter()
    ignored = 0
    for number, line in enumerate(text.splitlines(), 1):
        decodes = parse_all_txt(line)
        require(decodes or not re.search(r"\bRx\s+FT8\b", line),
                f"Complete RX line {number} could not be parsed: {line!r}")
        if not decodes:
            ignored += 1
        for decode in decodes:
            expected[DecodeKey(decode.decode_time_utc, decode.dial_frequency_hz,
                               decode.snr_db, decode.dt, decode.df, decode.raw_message)] += 1
    require(bool(expected), f"No complete receive decodes in {path}.")
    return expected, ignored, len(payload) - end


def database_snapshot(path: Path, receiver_id: str, source: Path, expected: Counter[DecodeKey]):
    first = min(key.timestamp_utc for key in expected)
    last = max(key.timestamp_utc for key in expected)
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        total = connection.execute("SELECT count(*) FROM observations").fetchone()[0]
        matches = defaultdict(list)
        rows = connection.execute(
            "SELECT id, timestamp_utc, dial_frequency_hz, snr_db, dt, df, raw_message, "
            "band, source, receiver_id, is_japan, raw_payload FROM observations "
            "WHERE receiver_id = ? AND source = 'all_txt' "
            "AND timestamp_utc >= ? AND timestamp_utc < ?",
            (receiver_id, first.replace(tzinfo=None).isoformat(sep=" "),
             (last + timedelta(microseconds=1)).replace(tzinfo=None).isoformat(sep=" ")),
        )
        for row in rows:
            provenance = json.loads(row["raw_payload"] or "{}")
            if provenance.get("all_txt_path") != str(source.resolve()):
                continue
            key = decode_key(row)
            if key in expected:
                matches[key].append(dict(row))
        latest_counts = connection.execute(
            "SELECT count(*), coalesce(sum(is_japan), 0) FROM observations "
            "WHERE timestamp_utc >= ? AND timestamp_utc < ?",
            (last.replace(tzinfo=None).isoformat(sep=" "),
             (last + timedelta(microseconds=1)).replace(tzinfo=None).isoformat(sep=" ")),
        ).fetchone()
        return matches, total, tuple(latest_counts)
    finally:
        connection.close()


def verify(config_path: Path, source: Path, base: str, wait_seconds: float,
           expected_dial_hz: int, expected_grid: str) -> None:
    require(config_path.is_file(), f"Configuration does not exist: {config_path}")
    config = load_config(config_path)
    require(config.database.url.startswith("sqlite:///"), "Verification requires a SQLite database.")
    database = Path(config.database.url.removeprefix("sqlite:///"))
    require(database.is_file(), f"Database does not exist: {database}")
    require(config.receiver_grid == expected_grid.upper(),
            f"Receiver grid is {config.receiver_grid!r}, expected {expected_grid.upper()}.")
    expected, ignored, partial_bytes = source_snapshot(source)
    print(f"Source snapshot: {sum(expected.values())} receive lines, {len(expected)} unique decodes, "
          f"{ignored} ignored lines, {partial_bytes} incomplete trailing bytes.", flush=True)
    deadline = time.monotonic() + wait_seconds
    while True:
        matches, total, latest_counts = database_snapshot(database, config.receiver.id, source, expected)
        missing = [key for key, count in expected.items() if len(matches[key]) < count]
        repeated = [key for key, count in expected.items() if len(matches[key]) > count]
        require(not repeated,
                f"{len(repeated)} source decode tuples have extra database occurrences; "
                f"examples: {repeated[:3]!r}")
        if not missing or time.monotonic() >= deadline:
            break
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))
    require(not missing,
            f"{len(missing)} source decodes are missing from the database; examples: {missing[:3]!r}")
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    finally:
        connection.close()
    require(integrity == ["ok"], f"SQLite integrity check failed: {integrity!r}")
    newest = max(expected, key=lambda key: key.timestamp_utc)
    newest_row = matches[newest][0]
    require(newest.dial_frequency_hz == expected_dial_hz,
            f"Newest source dial is {newest.dial_frequency_hz}, expected {expected_dial_hz} Hz.")
    require(newest_row["band"] == band_from_hz(expected_dial_hz),
            f"Newest source band is {newest_row['band']!r}.")
    print(f"PASS: all {sum(expected.values())} source receive lines match database occurrence counts; "
          f"{total} total preserved observations; SQLite integrity ok.", flush=True)

    opener = build_opener(ProxyHandler({}))

    def get_text(route: str) -> str:
        with opener.open(base.rstrip("/") + route, timeout=15) as response:
            return response.read().decode("utf-8-sig")

    def get_json(route: str):
        return json.loads(get_text(route))

    status = get_json("/api/status")
    health = get_json("/health")
    for name, payload in (("status", status), ("health", health)):
        require(payload.get("ok") is True and payload.get("db_writable") is True,
                f"HTTP {name} does not report a writable database.")
        require(not payload.get("last_error"), f"HTTP {name} reports {payload.get('last_error')!r}.")
        require(payload["receiver"]["id"] == config.receiver.id,
                f"HTTP {name} serves a different receiver.")
        require(payload["receiver"]["locator"].upper() == expected_grid.upper(),
                f"HTTP {name} serves a different receiver grid.")
        require(payload.get("dial_frequency_hz") == expected_dial_hz,
                f"HTTP {name} dial is {payload.get('dial_frequency_hz')!r}.")
        require(payload.get("band") == band_from_hz(expected_dial_hz),
                f"HTTP {name} band is {payload.get('band')!r}.")
        require(payload.get("input_source") == "all_txt", f"HTTP {name} is not using ALL.TXT.")
        require(Path(payload.get("input_path") or "").resolve() == source.resolve(),
                f"HTTP {name} is reading a different input path.")
        require(payload.get("input_readable") is True and not payload.get("input_error"),
                f"HTTP {name} reports an unreadable input: {payload.get('input_error')!r}.")
        poll_at = payload.get("input_last_poll_at")
        require(bool(poll_at), f"HTTP {name} has not polled the input.")
        poll_stamp = datetime.fromisoformat(poll_at.replace("Z", "+00:00"))
        poll_age = (datetime.now(timezone.utc) - poll_stamp).total_seconds()
        require(poll_age < max(10, config.input.poll_seconds * 3),
                f"HTTP {name} input poll is {poll_age:.1f} seconds old.")
    latest = get_json("/api/observations/latest?limit=25")["items"]
    require(bool(latest) and decode_key(latest[0]).timestamp_utc >= newest.timestamp_utc,
            "Latest observations API has not reached the source snapshot.")

    query = urlencode({"since": newest.timestamp_utc.isoformat(),
                       "until": (newest.timestamp_utc + timedelta(microseconds=1)).isoformat()})
    required_rows = {
        row["id"]: key for key, rows in matches.items() for row in rows
        if key.timestamp_utc == newest.timestamp_utc
    }

    def check_rows(rows, label: str) -> None:
        found = {}
        for row in rows:
            row_id = int(row["id"])
            if row_id in required_rows:
                require(row_id not in found, f"{label} repeats observation {row_id}.")
                require(decode_key(row) == required_rows[row_id],
                        f"{label} changed decode fields for observation {row_id}.")
                require(row["receiver_id"] == config.receiver.id and row["source"] == "all_txt",
                        f"{label} changed the source of observation {row_id}.")
                found[row_id] = True
        require(set(found) == set(required_rows),
                f"{label} is missing {len(set(required_rows) - set(found))} newest source decodes.")

    observations = []
    offset = 0
    while True:
        page = get_json(f"/api/observations?{query}&limit=500&offset={offset}")
        observations.extend(page["items"])
        offset += len(page["items"])
        if offset >= page["total"]:
            break
        require(bool(page["items"]), "Observations API stopped before its reported total.")
    check_rows(observations, "Observations API")
    csv_rows = csv.DictReader(io.StringIO(get_text("/api/export/csv?" + query)))
    check_rows(csv_rows, "CSV export")
    for label, metrics in (
        ("Summary", get_json("/api/stats/summary?" + query)),
        ("Japan Hour", get_json("/api/stats/japan-hour?" + query)["totals"]),
    ):
        require(metrics["total_decodes"] >= latest_counts[0],
                f"{label} lost decodes from the newest source slot.")
        require(metrics["japan_decodes"] >= latest_counts[1],
                f"{label} lost Japanese decodes from the newest source slot.")
    html = get_text("/")
    require("JAPAN HOUR LOGGER" in html, "Dashboard template is missing.")
    for asset in ("app.js", "app.css", "chart.umd.min.js"):
        require(len(get_text("/static/" + asset)) > 100, f"Dashboard asset {asset} is missing.")
    print("PASS: HTTP status, health, latest observations, exact decode fields in JSON and CSV, "
          "summary, Japan Hour, dashboard, and local assets.")
    print(f"Newest matched decode: {newest.timestamp_utc.isoformat()} | "
          f"{newest.dial_frequency_hz} Hz {newest_row['band']} | "
          f"SNR {newest.snr_db:g} dB | DT {newest.dt:g} | DF {newest.df} Hz | {newest.raw_message}")
    print(f"Receiver: {config.receiver.id}, grid {config.receiver_grid}. "
          f"Source: {source}. Database: {database}.")
    if status.get("input_recovery_warning"):
        print(f"Input recovery warning: {status['input_recovery_warning']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/etc/japan-hour-logger/receiver.yaml"))
    parser.add_argument("--source", type=Path, default=Path("/var/lib/radio-ft8/ALL.TXT"))
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--wait-seconds", type=float, default=10,
                        help="Maximum time for ingestion to catch up to the fixed source snapshot.")
    parser.add_argument("--expected-dial-hz", type=int, default=21_074_000)
    parser.add_argument("--expected-grid", default="OJ11WH")
    args = parser.parse_args()
    try:
        require(math.isfinite(args.wait_seconds) and args.wait_seconds >= 0,
                "--wait-seconds must be finite and nonnegative.")
        verify(args.config, args.source, args.url, args.wait_seconds,
               args.expected_dial_hz, args.expected_grid)
    except (Exception, KeyboardInterrupt) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
