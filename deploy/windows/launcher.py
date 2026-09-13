from __future__ import annotations

import argparse
import asyncio
from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import uuid
import venv
import webbrowser
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[2]
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
CONFIG = ROOT / "config" / "receiver.yaml"


def run_command(*args: str | Path) -> None:
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def load_settings():
    from radio_logger.config import load_config

    if not CONFIG.is_file():
        raise RuntimeError("Configuration is missing. Run Setup Windows.cmd first.")
    return load_config(CONFIG)


def setup(_args: argparse.Namespace) -> None:
    if sys.version_info < (3, 12):
        raise RuntimeError("Install Python 3.12 or newer, then run Setup Windows.cmd again.")
    try:
        usable = subprocess.run(
            [str(PYTHON), "-c", "import pathlib, sys; "
             "sys.exit(sys.version_info < (3, 12) or "
             "pathlib.Path(sys.prefix).resolve() != pathlib.Path(sys.argv[1]).resolve())",
             str(VENV)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        usable = False
    if not usable:
        if VENV.exists():
            preserved = ROOT / f".venv.broken-{uuid.uuid4().hex[:12]}"
            VENV.rename(preserved)
            print(f"Preserved the unusable environment at {preserved}", flush=True)
        print("Creating the local Python environment...", flush=True)
        venv.EnvBuilder(with_pip=True).create(VENV)
    guard = nullcontext()
    if usable and CONFIG.is_file():
        try:
            from radio_logger.lifecycle import logger_lock
        except ImportError:
            print("This older installation requires you to stop the logger before updating.", flush=True)
        else:
            guard = logger_lock(load_settings())
    with guard:
        pip_available = subprocess.run(
            [str(PYTHON), "-m", "pip", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
        if not pip_available:
            run_command(PYTHON, "-m", "ensurepip", "--upgrade")
        print("Installing the logger and its pinned dependencies...", flush=True)
        run_command(PYTHON, "-m", "pip", "install", "--disable-pip-version-check", "-r", ROOT / "requirements.txt", ROOT)
        if not CONFIG.exists():
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            run_command(
                PYTHON,
                "-c",
                "from pathlib import Path; import yaml; from radio_logger.config import AppConfig; "
                "Path('config/receiver.yaml').write_text(yaml.safe_dump(AppConfig().model_dump(), sort_keys=False), encoding='utf-8')",
            )
    run_command(PYTHON, __file__, "check")
    print("\nSetup complete. Existing configuration and observations were preserved.")
    print(f"Receiver settings: {CONFIG}")
    print("Set the school's locator in that file, then run Start Logger.cmd.")


def check(_args: argparse.Namespace) -> None:
    from zoneinfo import ZoneInfo

    from radio_logger.database.backup import sqlite_path_from_url

    cfg = load_settings()
    ZoneInfo(cfg.receiver.timezone)
    ZoneInfo(cfg.analytics.display_timezone)
    run_command(PYTHON, "-m", "pip", "check")
    print(f"\nConfiguration loaded: {CONFIG}", flush=True)
    database = sqlite_path_from_url(cfg.database.url)
    if database is None:
        raise RuntimeError("The beginner setup requires an on-disk SQLite database.")
    print(f"Production database: {database}")
    if database.exists():
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            connection.close()
        if result != "ok":
            raise RuntimeError(f"Database integrity check failed: {result}")
        print("Existing database passed its read-only integrity check.")
    else:
        print("No production database yet. Start Logger creates it on first use.")
    print("\nTesting the installed software with temporary data only...", flush=True)
    run_command(PYTHON, ROOT / "scripts" / "verify-install.py")
    print("\nPASS: local software installation and isolated self-test.")
    print("LIVE RADIO NOT VERIFIED by this test.")
    print("Start Logger and WSJT-X. Enable UDP to 127.0.0.1:2237 with decoded messages.")
    print("Then confirm recent UDP and new real decodes on the dashboard.")
    if not cfg.receiver_grid:
        print("Receiver locator is TODO. Enter the school's grid for distances and bearings.")


def open_when_ready(url: str, stopped: threading.Event) -> None:
    for _ in range(120):
        if stopped.wait(0.25):
            return
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url + "/health", timeout=1) as response:
                status = json.load(response)
            if status.get("ok") and status.get("db_writable"):
                if not stopped.is_set():
                    webbrowser.open(url)
                return
        except (OSError, ValueError, urllib.error.URLError):
            continue
    print(f"Browser did not open. Check the console errors, then open {url} manually.")


def start(args: argparse.Namespace) -> None:
    from radio_logger.service import run_server

    cfg = load_settings()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((cfg.http.host, cfg.http.port))
    except OSError as exc:
        raise RuntimeError(
            f"HTTP address {cfg.http.host}:{cfg.http.port} is already in use or unavailable. "
            "Check whether the logger is already running."
        ) from exc
    browser_host = "127.0.0.1" if cfg.http.host in {"0.0.0.0", "localhost"} else cfg.http.host
    url = f"http://{browser_host}:{cfg.http.port}"
    print(f"Dashboard: {url}", flush=True)
    print(f"WSJT-X UDP: {cfg.udp.host}:{cfg.udp.port}", flush=True)
    print("Keep this window open while logging. Press Ctrl+C to stop safely.", flush=True)
    stopped = threading.Event()
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url, stopped), daemon=True).start()
    try:
        asyncio.run(run_server(cfg))
    finally:
        stopped.set()


def backup(_args: argparse.Namespace) -> None:
    from radio_logger.database.backup import backup_sqlite, sqlite_path_from_url

    cfg = load_settings()
    database = sqlite_path_from_url(cfg.database.url)
    if database is None or not database.is_file():
        raise RuntimeError("No SQLite database to back up yet. Start the logger first.")
    destination = backup_sqlite(cfg.database.url, cfg.paths.backups_dir)
    config_copy = destination.with_suffix(".yaml")
    shutil.copy2(CONFIG, config_copy)
    print(f"Database backup: {destination}")
    print(f"Configuration copy: {config_copy}")
    print("The database backup includes all stored observations and is safe while logging.")
    print(f"Raw log files are separate: {cfg.paths.raw_dir}")
    print("For a complete archive, stop the logger and also copy the raw log folder.")
    print("Copy your backups to another drive or computer.")


def export(_args: argparse.Namespace) -> None:
    from radio_logger.database.backup import sqlite_path_from_url

    cfg = load_settings()
    database = sqlite_path_from_url(cfg.database.url)
    if database is None or not database.is_file():
        raise RuntimeError("No database to export yet. Start the logger first.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = Path(cfg.paths.exports_dir) / f"observations-{stamp}.csv"
    run_command(PYTHON, "-m", "radio_logger.cli", "export", "--config", CONFIG, "--output", destination)


def main() -> int:
    actions = {"setup": setup, "start": start, "check": check, "backup": backup, "export": export}
    parser = argparse.ArgumentParser(description="Japan Hour Logger setup and daily actions")
    parser.add_argument("action", choices=actions)
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser when starting")
    parser.add_argument("--no-pause", action="store_true", help="Do not pause the Windows CMD launcher at exit")
    args = parser.parse_args()
    try:
        os.chdir(ROOT)
        if args.action != "setup" and Path(sys.prefix).resolve() != VENV.resolve():
            raise RuntimeError("Use the project's .venv Python or the CMD launcher. Run Setup Windows.cmd first.")
        actions[args.action](args)
        return 0
    except KeyboardInterrupt:
        print("\nLogger stopped. Stored observations remain on disk.")
        return 0
    except (OSError, ValueError, RuntimeError, ImportError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        print("Read the error above. Setup instructions are in docs/SETUP.md.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
