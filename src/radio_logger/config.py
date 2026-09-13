from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, get_origin

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


class ReceiverConfig(BaseModel):
    id: str = "school-9v"
    name: str = "Japan Hour Receiver"
    locator: str = "TODO"
    timezone: str = "Asia/Singapore"


class UdpConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 2237
    multicast: bool = False


class HttpConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080


class DatabaseConfig(BaseModel):
    url: str = "sqlite:///data/radio.db"


class PathsConfig(BaseModel):
    data_dir: str = "data"
    raw_dir: str = "data/raw/wsjtx"
    exports_dir: str = "data/exports"
    backups_dir: str = "data/backups"
    jsonl_events: bool = True


class DedupeConfig(BaseModel):
    window_seconds: float = 2.0


class JapanConfig(BaseModel):
    dxcc_entities: list[str] = Field(
        default_factory=lambda: ["Japan", "Ogasawara", "Minami Torishima"]
    )


class AnalyticsConfig(BaseModel):
    default_bucket_minutes: int = 15
    display_timezone: str = "Asia/Singapore"


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RADIO_LOGGER_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    receiver: ReceiverConfig = Field(default_factory=ReceiverConfig)
    udp: UdpConfig = Field(default_factory=UdpConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    dedupe: DedupeConfig = Field(default_factory=DedupeConfig)
    japan: JapanConfig = Field(default_factory=JapanConfig)
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)

    def resolve_paths(self, root: Path | None = None) -> None:
        root = root or _project_root()
        url = self.database.url
        if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
            rel = url.removeprefix("sqlite:///")
            if rel not in {":memory:", ""} and not Path(rel).is_absolute():
                self.database.url = f"sqlite:///{(root / rel).as_posix()}"
        for attr in ("data_dir", "raw_dir", "exports_dir", "backups_dir"):
            value = getattr(self.paths, attr)
            path = Path(value)
            if not path.is_absolute():
                setattr(self.paths, attr, str(root / path))

    def ensure_directories(self) -> None:
        for attr in ("data_dir", "raw_dir", "exports_dir", "backups_dir"):
            Path(getattr(self.paths, attr)).mkdir(parents=True, exist_ok=True)

    @property
    def receiver_grid(self) -> str | None:
        locator = (self.receiver.locator or "").strip().upper()
        if not locator or locator == "TODO":
            return None
        return locator


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a mapping")
    return data


def _environment_overrides() -> dict[str, dict[str, Any]]:
    overrides: dict[str, dict[str, Any]] = {}
    for section_name, section_field in AppConfig.model_fields.items():
        section_type = section_field.annotation
        if not isinstance(section_type, type) or not issubclass(section_type, BaseModel):
            continue
        for field_name, field in section_type.model_fields.items():
            single_name = f"RADIO_LOGGER_{section_name}_{field_name}".upper()
            nested_name = f"RADIO_LOGGER_{section_name}__{field_name}".upper()
            value = os.environ.get(nested_name, os.environ.get(single_name))
            if value is not None:
                if get_origin(field.annotation) is list:
                    value = json.loads(value)
                overrides.setdefault(section_name, {})[field_name] = value
    return overrides


def load_config(config_path: str | Path | None = None) -> AppConfig:
    root = _project_root()
    selected_path = config_path or os.environ.get("RADIO_LOGGER_CONFIG")
    path = Path(selected_path) if selected_path else root / "config" / "receiver.yaml"
    if not path.is_absolute():
        path = (root / path).resolve() if not path.exists() else path.resolve()
    data = _load_yaml(path)
    for section, values in _environment_overrides().items():
        existing = data.get(section)
        data[section] = {**(existing if isinstance(existing, dict) else {}), **values}
    cfg = AppConfig.model_validate(data)
    cfg.resolve_paths(root)
    return cfg


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return load_config()


def reset_config_cache() -> None:
    get_config.cache_clear()
