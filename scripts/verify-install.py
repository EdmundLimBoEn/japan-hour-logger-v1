"""Exercise the installed logger using only temporary data and loopback sockets."""

from __future__ import annotations

import csv
import io
import json
import os
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def available_port(kind: int) -> int:
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def qt_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack(">I", len(encoded)) + encoded


def packet_header(kind: int) -> bytes:
    return struct.pack(">III", 0xADBCCBDA, 2, kind) + qt_string("install-check")


def status_packet() -> bytes:
    return (
        packet_header(1)
        + struct.pack(">Q", 14_074_000)
        + qt_string("FT8") + qt_string("") + qt_string("") + qt_string("FT8")
        + struct.pack(">???II", False, False, False, 0, 0)
        + qt_string("") + qt_string("OJ11") + qt_string("")
    )


def decode_packet(time_ms: int) -> bytes:
    return (
        packet_header(2)
        + struct.pack(">?IidI", True, time_ms, -12, 0.2, 1234)
        + qt_string("~") + qt_string("CQ JA1XYZ PM95")
        + struct.pack(">??", False, False)
    )


@contextmanager
def running_logger(config: Path, directory: Path, environment: dict[str, str], base: str):
    output: deque[str] = deque(maxlen=60)
    process = subprocess.Popen(
        [sys.executable, "-m", "radio_logger", "run", "--config", str(config)],
        cwd=directory,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    def drain() -> None:
        for line in process.stdout:
            output.append(line.rstrip())

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        wait_for(lambda: get_json(base + "/health"), process)
        require(get_json(base + "/api/status")["receiver"]["id"] == "install-check",
                "The temporary HTTP port is occupied by another logger.")
        yield process
    except BaseException:
        if output:
            print("\nLogger output:\n" + "\n".join(output), file=sys.stderr)
        raise
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=5)
        process.stdout.close()


def get_text(url: str) -> str:
    with build_opener(ProxyHandler({})).open(url, timeout=3) as response:
        return response.read().decode("utf-8-sig")


def get_json(url: str):
    return json.loads(get_text(url))


def wait_for(check, process: subprocess.Popen, seconds: float = 20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        require(process.poll() is None, "Logger exited before the check completed.")
        try:
            result = check()
            if result:
                return result
        except (URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.1)
    raise RuntimeError("Timed out waiting for the temporary logger. Check the output above.")


def check_database(path: Path, count: int) -> None:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        require(connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
                f"SQLite integrity check failed for {path.name}.")
        require(connection.execute("SELECT count(*) FROM observations").fetchone()[0] == count,
                f"Expected {count} preserved observations in {path.name}.")
    finally:
        connection.close()


def verify(directory: Path) -> None:
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith("RADIO_LOGGER_")
    }
    environment.pop("PYTHONPATH", None)
    environment["PYTHONUTF8"] = "1"
    http_port = available_port(socket.SOCK_STREAM)
    udp_port = available_port(socket.SOCK_DGRAM)
    base = f"http://127.0.0.1:{http_port}"
    config = directory / "receiver.yaml"
    data = directory / "data"
    config.write_text(json.dumps({
        "receiver": {"id": "install-check", "name": "Temporary software check",
                     "locator": "OJ11", "timezone": "Asia/Singapore"},
        "http": {"host": "127.0.0.1", "port": http_port},
        "udp": {"host": "127.0.0.1", "port": udp_port},
        "database": {"url": f"sqlite:///{(data / 'radio.db').as_posix()}"},
        "paths": {"data_dir": str(data), "raw_dir": str(data / "raw"),
                  "exports_dir": str(data / "exports"), "backups_dir": str(data / "backups")},
    }), encoding="utf-8")

    with running_logger(config, directory, environment, base) as process:
        html = get_text(base + "/")
        require("JAPAN HOUR LOGGER" in html, "Installed dashboard template is missing.")
        require("https://" not in html and "http://" not in html,
                "Dashboard still depends on an external asset.")
        for asset in ("app.js", "app.css", "chart.umd.min.js"):
            require(len(get_text(base + "/static/" + asset)) > 100,
                    f"Installed dashboard asset {asset} is missing.")
        print("PASS: installed dashboard and local chart assets")

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            destination = ("127.0.0.1", udp_port)
            now = datetime.now(timezone.utc)
            millis = ((now.hour * 60 + now.minute) * 60 + now.second) * 1000
            sender.sendto(status_packet(), destination)
            sender.sendto(decode_packet(millis), destination)
            sender.sendto(decode_packet(millis), destination)
            sender.sendto(decode_packet((millis + 15_000) % 86_400_000), destination)
        wait_for(lambda: get_json(base + "/api/observations")["total"] == 2, process)
        rows = get_json(base + "/api/observations")["items"]
        require(all(row["tx_callsign"] == "JA1XYZ" and row["is_japan"]
                    and row["band"] == "20m" and row["distance_km"] > 0 for row in rows),
                "UDP decode, station enrichment, or distance calculation failed.")
        status = get_json(base + "/api/status")
        require(status["udp_recently_seen"] and status["duplicates_suppressed"] == 1
                and status["db_writable"] and status["last_error"] is None,
                "Logger did not report healthy UDP ingestion and duplicate suppression.")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as duplicate:
            try:
                duplicate.bind(destination)
            except OSError:
                pass
            else:
                raise RuntimeError("A second collector can steal the same unicast UDP port.")
        print("PASS: real UDP input, repeated station retention, duplicate suppression, enrichment")

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(b"invalid WSJT-X packet", destination)
            sender.sendto(status_packet(), destination)
            for index in range(200):
                packet = decode_packet((millis + (index + 2) * 15_000) % 86_400_000)
                sender.sendto(packet, destination)
                sender.sendto(packet, destination)
        expected_count = 202
        wait_for(lambda: get_json(base + "/api/observations")["total"] == expected_count,
                 process, seconds=45)
        wait_for(lambda: get_json(base + "/api/status")["duplicates_suppressed"] == 201,
                 process)
        status = get_json(base + "/api/status")
        require(status["parse_errors"] == 1 and status["dropped_datagrams"] == 0
                and status["db_writable"],
                "Malformed input or a duplicate burst damaged UDP collection.")
        print("PASS: malformed UDP recovery and a burst of 200 unique decodes plus 200 duplicates")

        competing = json.loads(config.read_text(encoding="utf-8"))
        competing["http"]["port"] = available_port(socket.SOCK_STREAM)
        competing["udp"]["port"] = available_port(socket.SOCK_DGRAM)
        second_config = directory / "second-receiver.yaml"
        second_config.write_text(json.dumps(competing), encoding="utf-8")
        second = subprocess.run(
            [sys.executable, "-m", "radio_logger", "run", "--config", str(second_config)],
            cwd=directory, env=environment, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
        require(second.returncode != 0 and "already in use" in second.stdout + second.stderr,
                "Another logger was allowed to collect into the same database.")
        require(process.poll() is None and get_json(base + "/api/observations")["total"] == expected_count,
                "Rejecting the second logger interrupted the original collector.")
        print("PASS: a second process cannot collect into the same database using different ports")

        query = urlencode({"since": (now - timedelta(days=1)).isoformat(),
                           "until": (now + timedelta(days=1)).isoformat()})
        require(get_json(base + "/api/stats/summary?" + query)["japan_decodes"] == expected_count,
                "Japan summary does not include all decodes.")
        require(get_json(base + "/api/stats/japan-hour?" + query)["totals"]["japan_decodes"] == expected_count,
                "Japan Hour chart data is incorrect.")
        api_csv = list(csv.DictReader(io.StringIO(get_text(base + "/api/export/csv"))))
        require(len(api_csv) == expected_count, "HTTP CSV export lost observations.")
        cli = [sys.executable, "-m", "radio_logger"]
        exported = data / "exports" / "check.csv"
        subprocess.run(cli + ["export", "--config", str(config), "--output", str(exported)],
                       cwd=directory, env=environment, check=True, timeout=20)
        with exported.open(encoding="utf-8-sig", newline="") as handle:
            require(len(list(csv.DictReader(handle))) == expected_count, "CLI CSV export lost observations.")
        subprocess.run(cli + ["db-backup", "--config", str(config)],
                       cwd=directory, env=environment, check=True, timeout=20)
        backups = list((data / "backups").glob("*.db"))
        require(len(backups) == 1, "Online backup did not produce one database.")
        check_database(backups[0], expected_count)
        require(any((data / "raw").rglob("*.jsonl")), "Raw WSJT-X events were not preserved.")
        print("PASS: Japan Hour analytics, HTTP/CLI CSV, raw events, live SQLite backup")

    with running_logger(config, directory, environment, base):
        require(get_json(base + "/api/observations")["total"] == expected_count,
                "Observations did not survive a logger restart.")
    check_database(data / "radio.db", expected_count)
    print("PASS: database integrity and observations survive restart")


def main() -> int:
    print("Checking the installed software with temporary data. School data is untouched.", flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix="japan logger 日本 & radio! check ") as temporary:
            verify(Path(temporary).resolve())
    except (Exception, KeyboardInterrupt) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: software check complete. Next verify real WSJT-X decodes on the school machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
