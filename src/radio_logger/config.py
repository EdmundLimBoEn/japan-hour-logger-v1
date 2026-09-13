from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, get_origin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def _timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError(f"Unknown timezone: {value}") from exc
    return value


TimezoneName = Annotated[str, AfterValidator(_timezone)]
NonemptyString = Annotated[str, Field(min_length=1)]


class ConfigSection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReceiverConfig(ConfigSection):
    id: NonemptyString = "school-9v"
    name: NonemptyString = "Japan Hour Receiver"
    locator: str = "TODO"
    timezone: TimezoneName = "Asia/Singapore"

    @field_validator("locator")
    @classmethod
    def valid_locator(cls, value: str) -> str:
        from radio_logger.enrichment.maidenhead import is_grid

        value = value.upper()
        if value not in {"", "TODO"} and not is_grid(value):
            raise ValueError("Receiver locator must be a Maidenhead grid or TODO")
        return value


class UdpConfig(ConfigSection):
    host: NonemptyString = "127.0.0.1"
    port: int = Field(default=2237, ge=0, le=65535)
    multicast: bool = False


class InputConfig(ConfigSection):
    source: Literal["udp", "all_txt"] = "udp"
    path: str | None = None
    poll_seconds: float = Field(default=1.0, ge=0.1, le=60, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_file_path(self) -> InputConfig:
        if self.source == "all_txt" and not self.path:
            raise ValueError("input.path is required for all_txt input")
        return self


class HttpConfig(ConfigSection):
    host: NonemptyString = "127.0.0.1"
    port: int = Field(default=8080, ge=0, le=65535)


class DatabaseConfig(ConfigSection):
    url: NonemptyString = "sqlite:///data/radio.db"


class PathsConfig(ConfigSection):
    data_dir: NonemptyString = "data"
    raw_dir: NonemptyString = "data/raw/wsjtx"
    exports_dir: NonemptyString = "data/exports"
    backups_dir: NonemptyString = "data/backups"
    jsonl_events: bool = True


class DedupeConfig(ConfigSection):
    window_seconds: float = Field(default=2.0, ge=0, le=60, allow_inf_nan=False)


class JapanConfig(ConfigSection):
    dxcc_entities: list[str] = Field(
        default_factory=lambda: ["Japan", "Ogasawara", "Minami Torishima"]
    )


class AnalyticsConfig(ConfigSection):
    default_bucket_minutes: Literal[5, 15, 30, 60] = 15
    display_timezone: TimezoneName = "Asia/Singapore"


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RADIO_LOGGER_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    receiver: ReceiverConfig = Field(default_factory=ReceiverConfig)
    input: InputConfig = Field(default_factory=InputConfig)
    udp: UdpConfig = Field(default_factory=UdpConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    dedupe: DedupeConfig = Field(default_factory=DedupeConfig)
    japan: JapanConfig = Field(default_factory=JapanConfig)
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)

    def resolve_paths(self, root: Path | None = None) -> None:
        root = root or _project_root()
        if self.input.path:
            path = Path(self.input.path)
            self.input.path = str((root / path).resolve() if not path.is_absolute() else path.resolve())
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
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if data is None:
        data = {}
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


def load_config(
    config_path: str | Path | None = None, *, resolve_paths: bool = True
) -> AppConfig:
    root = _project_root()
    selected_path = config_path or os.environ.get("RADIO_LOGGER_CONFIG")
    path = Path(selected_path) if selected_path else root / "config" / "receiver.yaml"
    if not path.is_absolute():
        path = (root / path).resolve() if not path.exists() else path.resolve()
    if selected_path and not path.is_file():
        raise FileNotFoundError(f"Configuration file does not exist: {path}")
    data = _load_yaml(path)
    for section, values in _environment_overrides().items():
        existing = data.get(section)
        data[section] = {**(existing if isinstance(existing, dict) else {}), **values}
    cfg = AppConfig.model_validate(data)
    for field, port in (("udp.port", cfg.udp.port), ("http.port", cfg.http.port)):
        if port == 0:
            raise ValueError(f"Configured {field} must be between 1 and 65535")
    if resolve_paths:
        cfg.resolve_paths(root)
    return cfg


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return load_config()


def reset_config_cache() -> None:
    get_config.cache_clear()
