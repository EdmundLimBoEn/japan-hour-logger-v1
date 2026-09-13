from __future__ import annotations

import argparse
import importlib.util
import sqlite3
from pathlib import Path

import yaml

import radio_logger.config as config_module
from radio_logger.config import load_config
from radio_logger.database.backup import sqlite_path_from_url


def test_backup_saves_effective_config_with_portable_paths(tmp_path, monkeypatch):
    launcher_path = Path(__file__).resolve().parents[1] / "deploy/windows/launcher.py"
    spec = importlib.util.spec_from_file_location("windows_backup_launcher", launcher_path)
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)

    launcher.ROOT = tmp_path
    launcher.CONFIG = tmp_path / "config/receiver.yaml"
    launcher.CONFIG.parent.mkdir()
    launcher.CONFIG.write_text(
        yaml.safe_dump(
            {
                "udp": {"port": 2237},
                "database": {"url": "sqlite:///data/radio.db"},
                "paths": {
                    "data_dir": "data",
                    "raw_dir": "data/raw/wsjtx",
                    "exports_dir": "data/exports",
                    "backups_dir": "data/backups",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    database = tmp_path / "data/radio.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE observations (message TEXT)")
    monkeypatch.setenv("RADIO_LOGGER_UDP_PORT", "2240")
    monkeypatch.setenv("RADIO_LOGGER_PATHS__RAW_DIR", "data/raw/from-environment")

    launcher.backup(argparse.Namespace())

    config_copy = next((tmp_path / "data/backups").glob("*.yaml"))
    snapshot = yaml.safe_load(config_copy.read_text(encoding="utf-8"))
    assert snapshot["udp"]["port"] == 2240
    assert snapshot["database"]["url"] == "sqlite:///data/radio.db"
    assert snapshot["paths"]["raw_dir"] == "data/raw/from-environment"

    restored_root = tmp_path / "restored"
    restored_config = restored_root / "config/receiver.yaml"
    restored_config.parent.mkdir(parents=True)
    restored_config.write_bytes(config_copy.read_bytes())
    monkeypatch.delenv("RADIO_LOGGER_UDP_PORT")
    monkeypatch.delenv("RADIO_LOGGER_PATHS__RAW_DIR")
    monkeypatch.setattr(config_module, "_project_root", lambda: restored_root)

    restored = load_config(restored_config)

    assert restored.udp.port == 2240
    assert sqlite_path_from_url(restored.database.url) == restored_root / "data/radio.db"
    assert Path(restored.paths.raw_dir) == restored_root / "data/raw/from-environment"
