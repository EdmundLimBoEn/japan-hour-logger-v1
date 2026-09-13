from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import SQLAlchemyError

from radio_logger import __version__
from radio_logger.analytics.japan_hour import (
    country_mix,
    distance_histogram,
    japan_hour_buckets,
    snr_histogram,
    summary,
    today_bounds,
)
from radio_logger.config import AppConfig
from radio_logger.bands import band_from_hz
from radio_logger.database.engine import database_size_bytes, db_writable
from radio_logger.database.models import Observation
from radio_logger.database.repository import Repository
from radio_logger.export_csv import iter_observations_csv
from radio_logger.models import RuntimeState
from radio_logger.timeutil import as_utc, to_local

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def observation_dict(row: Observation, receiver_timezone: str = "Asia/Singapore") -> dict[str, Any]:
    timestamp_utc = as_utc(row.timestamp_utc) if row.timestamp_utc else None
    timezone_name = row.receiver.timezone if row.receiver else receiver_timezone
    return {
        "id": row.id,
        "receiver_id": row.receiver_id,
        "timestamp_utc": timestamp_utc.isoformat() if timestamp_utc else None,
        "timestamp_local": (
            to_local(timestamp_utc, timezone_name).isoformat() if timestamp_utc else None
        ),
        "dial_frequency_hz": row.dial_frequency_hz,
        "audio_frequency_hz": row.audio_frequency_hz,
        "signal_frequency_hz": row.signal_frequency_hz,
        "band": row.band,
        "mode": row.mode,
        "snr_db": row.snr_db,
        "dt": row.dt,
        "df": row.df,
        "raw_message": row.raw_message,
        "message_type": row.message_type,
        "is_cq": row.is_cq,
        "tx_callsign": row.tx_callsign,
        "rx_callsign": row.rx_callsign,
        "tx_grid": row.tx_grid,
        "grid_source": row.grid_source,
        "country": row.country,
        "dxcc": row.dxcc,
        "continent": row.continent,
        "cqz": row.cqz,
        "ituz": row.ituz,
        "distance_km": row.distance_km,
        "bearing_deg": row.bearing_deg,
        "low_confidence": row.low_confidence,
        "off_air": row.off_air,
        "source": row.source,
        "is_japan": row.is_japan,
    }


def create_app(
    config: AppConfig,
    session_factory: sessionmaker[Session],
    runtime: RuntimeState,
) -> FastAPI:
    app = FastAPI(title="Japan Hour Logger", version=__version__)
    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
    static_dir = WEB_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    def db_session() -> Session:
        return session_factory()

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "receiver": config.receiver,
                "udp": config.udp,
                "version": __version__,
            },
        )

    @app.get("/api/status")
    def api_status() -> dict[str, Any]:
        now = datetime.now(tz=timezone.utc)
        last_15 = now - timedelta(minutes=15)
        start_today, end_today = today_bounds(now, config.analytics.display_timezone)
        last_decode_age = (
            (now - runtime.last_decode_at).total_seconds()
            if runtime.last_decode_at
            else None
        )
        udp_age = (
            (now - runtime.last_udp_at).total_seconds()
            if runtime.last_udp_at
            else None
        )
        session = db_session()
        file_mode = config.input.source == "all_txt"
        file_input = runtime.file_input
        try:
            repo = Repository(session)
            last_row = repo.latest_observations(limit=1, receiver_id=config.receiver.id)
            last_obs = (
                observation_dict(last_row[0], config.receiver.timezone) if last_row else None
            )
            last_decode_at = runtime.last_decode_at
            if file_mode and last_decode_at is None and last_row:
                last_decode_at = as_utc(last_row[0].timestamp_utc)
            last_decode_age = (now - last_decode_at).total_seconds() if last_decode_at else None
            udp_recent = udp_age is not None and 0 <= udp_age < 60 and not runtime.udp_error
            size = database_size_bytes(config.database.url)
            status = runtime.receiver_status
            writable = db_writable(session.get_bind())
            dial = status.dial_frequency_hz
            mode = status.mode
            if file_mode and last_row:
                dial = dial if dial is not None else last_row[0].dial_frequency_hz
                mode = mode or last_row[0].mode
            online = (
                file_input.readable and last_decode_age is not None and 0 <= last_decode_age < 120
                if file_mode else udp_recent
            )
            file_ok = (not file_mode) or file_input.readable
            return {
                "ok": writable and not runtime.storage_error and not runtime.udp_error and file_ok,
                "version": __version__,
                "online": online,
                "input_source": config.input.source,
                "input_path": config.input.path,
                "input_readable": file_input.readable if file_mode else None,
                "input_last_poll_at": file_input.last_poll_at.isoformat() if file_input.last_poll_at else None,
                "input_offset": file_input.offset if file_mode else None,
                "input_size": file_input.size if file_mode else None,
                "input_backlog_bytes": max(0, file_input.size - file_input.offset) if file_mode else None,
                "input_error": file_input.error if file_mode else None,
                "input_recovery_warning": file_input.recovery_warning if file_mode else None,
                "udp_bound": runtime.udp_bound,
                "udp_recently_seen": udp_recent,
                "udp_age_seconds": udp_age,
                "last_heartbeat_at": runtime.last_heartbeat_at.isoformat() if runtime.last_heartbeat_at else None,
                "last_decode_at": last_decode_at.isoformat() if last_decode_at else None,
                "last_decode_age_seconds": last_decode_age,
                "last_decode": last_obs,
                "decodes_15m": repo.count_since(last_15, until=now),
                "japan_decodes_15m": repo.count_since(
                    last_15, until=now, japan_only=True
                ),
                "decodes_today": repo.count_since(start_today, until=end_today),
                "japan_decodes_today": repo.count_since(
                    start_today, until=end_today, japan_only=True
                ),
                "session_decodes": runtime.decode_count_session,
                "duplicates_suppressed": runtime.duplicate_suppressed,
                "ignored_decodes": runtime.ignored_decodes,
                "parse_errors": runtime.parse_errors,
                "storage_failures": runtime.storage_failures,
                "storage_error": runtime.storage_error,
                "udp_error": runtime.udp_error,
                "queue_depth": runtime.queue_depth,
                "dropped_datagrams": runtime.dropped_datagrams,
                "db_writable": writable,
                "db_size_bytes": size,
                "uptime_seconds": (now - runtime.started_at).total_seconds(),
                "dial_frequency_hz": dial,
                "band": band_from_hz(dial),
                "mode": mode,
                "receiver": {
                    "id": config.receiver.id,
                    "name": config.receiver.name,
                    "locator": config.receiver.locator,
                    "timezone": config.receiver.timezone,
                },
                "last_error": (file_input.error or runtime.last_error) if file_mode else runtime.last_error,
                "display_timezone": config.analytics.display_timezone,
                "today_start_utc": start_today.isoformat(),
                "today_end_utc": end_today.isoformat(),
            }
        except (OSError, SQLAlchemyError) as exc:
            return {
                "ok": False,
                "version": __version__,
                "online": False,
                "input_source": config.input.source,
                "input_path": config.input.path,
                "input_readable": file_input.readable if file_mode else None,
                "input_last_poll_at": file_input.last_poll_at.isoformat() if file_input.last_poll_at else None,
                "input_offset": file_input.offset if file_mode else None,
                "input_size": file_input.size if file_mode else None,
                "input_backlog_bytes": max(0, file_input.size - file_input.offset) if file_mode else None,
                "input_error": file_input.error if file_mode else None,
                "input_recovery_warning": file_input.recovery_warning if file_mode else None,
                "udp_recently_seen": False,
                "udp_bound": runtime.udp_bound,
                "udp_age_seconds": udp_age,
                "last_heartbeat_at": runtime.last_heartbeat_at.isoformat() if runtime.last_heartbeat_at else None,
                "last_decode_at": runtime.last_decode_at.isoformat() if runtime.last_decode_at else None,
                "last_decode_age_seconds": last_decode_age,
                "last_decode": None,
                "decodes_15m": None,
                "japan_decodes_15m": None,
                "decodes_today": None,
                "japan_decodes_today": None,
                "session_decodes": runtime.decode_count_session,
                "duplicates_suppressed": runtime.duplicate_suppressed,
                "ignored_decodes": runtime.ignored_decodes,
                "parse_errors": runtime.parse_errors,
                "db_writable": False,
                "storage_error": str(exc),
                "last_error": f"Database unavailable: {exc}",
                "storage_failures": runtime.storage_failures,
                "dropped_datagrams": runtime.dropped_datagrams,
                "queue_depth": runtime.queue_depth,
                "udp_error": runtime.udp_error,
                "db_size_bytes": None,
                "uptime_seconds": (now - runtime.started_at).total_seconds(),
                "dial_frequency_hz": runtime.receiver_status.dial_frequency_hz,
                "band": band_from_hz(runtime.receiver_status.dial_frequency_hz),
                "mode": runtime.receiver_status.mode,
                "receiver": {
                    "id": config.receiver.id,
                    "name": config.receiver.name,
                    "locator": config.receiver.locator,
                    "timezone": config.receiver.timezone,
                },
                "display_timezone": config.analytics.display_timezone,
                "today_start_utc": start_today.isoformat(),
                "today_end_utc": end_today.isoformat(),
            }
        finally:
            session.close()

    @app.get("/api/observations")
    def api_observations(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        callsign: str | None = None,
        country: str | None = None,
        japan_only: bool = False,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, Any]:
        session = db_session()
        try:
            rows, total = Repository(session).list_observations(
                limit=limit,
                offset=offset,
                callsign=callsign,
                country=country,
                japan_only=japan_only,
                since=since,
                until=until,
            )
            return {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [observation_dict(r, config.receiver.timezone) for r in rows],
            }
        finally:
            session.close()

    @app.get("/api/observations/latest")
    def api_latest(limit: int = Query(25, ge=1, le=200)) -> dict[str, Any]:
        session = db_session()
        try:
            rows = Repository(session).latest_observations(limit=limit)
            return {
                "items": [observation_dict(r, config.receiver.timezone) for r in rows]
            }
        finally:
            session.close()

    @app.get("/api/stations")
    def api_stations(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        japan_only: bool = False,
    ) -> dict[str, Any]:
        session = db_session()
        try:
            rows, total = Repository(session).list_stations(limit=limit, offset=offset, japan_only=japan_only)
            return {
                "total": total,
                "items": [
                    {
                        "callsign": s.callsign,
                        "last_grid": s.last_grid,
                        "grid_source": s.grid_source,
                        "country": s.country,
                        "dxcc": s.dxcc,
                        "continent": s.continent,
                        "decode_count": s.decode_count,
                        "last_heard_utc": s.last_heard_utc.isoformat() if s.last_heard_utc else None,
                        "last_snr_db": s.last_snr_db,
                        "last_distance_km": s.last_distance_km,
                    }
                    for s in rows
                ],
            }
        finally:
            session.close()

    @app.get("/api/stations/{callsign}/history")
    def api_station_history(callsign: str, limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
        session = db_session()
        try:
            repo = Repository(session)
            station = repo.station_get(callsign)
            if station is None:
                raise HTTPException(status_code=404, detail="station not heard")
            history = repo.station_history(callsign, limit=limit)
            return {
                "station": {
                    "callsign": station.callsign,
                    "last_grid": station.last_grid,
                    "grid_source": station.grid_source,
                    "country": station.country,
                    "dxcc": station.dxcc,
                    "decode_count": station.decode_count,
                },
                "items": [observation_dict(r, config.receiver.timezone) for r in history],
            }
        finally:
            session.close()

    @app.get("/api/stats/summary")
    def api_summary(since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
        session = db_session()
        try:
            if since is None and until is None:
                since, until = today_bounds(datetime.now(tz=timezone.utc), config.analytics.display_timezone)
            return summary(session, since=since, until=until)
        finally:
            session.close()

    @app.get("/api/stats/countries")
    def api_countries(since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
        session = db_session()
        try:
            if since is None and until is None:
                since, until = today_bounds(datetime.now(tz=timezone.utc), config.analytics.display_timezone)
            return {"items": country_mix(session, since=since, until=until)}
        finally:
            session.close()

    @app.get("/api/stats/distance")
    def api_distance(since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
        session = db_session()
        try:
            if since is None and until is None:
                since, until = today_bounds(datetime.now(tz=timezone.utc), config.analytics.display_timezone)
            return {"items": distance_histogram(session, since=since, until=until)}
        finally:
            session.close()

    @app.get("/api/stats/snr")
    def api_snr(since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
        session = db_session()
        try:
            if since is None and until is None:
                since, until = today_bounds(datetime.now(tz=timezone.utc), config.analytics.display_timezone)
            return snr_histogram(session, since=since, until=until)
        finally:
            session.close()

    @app.get("/api/stats/japan-hour")
    def api_japan_hour(
        bucket: int = Query(15),
        since: datetime | None = None,
        until: datetime | None = None,
        tz: str | None = None,
    ) -> dict[str, Any]:
        session = db_session()
        try:
            display_tz = tz or config.analytics.display_timezone
            if since is None and until is None:
                since, until = today_bounds(datetime.now(tz=timezone.utc), display_tz)
            return japan_hour_buckets(
                session,
                since=since,
                until=until,
                bucket_minutes=bucket,
                display_timezone=display_tz,
            )
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            session.close()

    @app.get("/api/export/csv")
    def api_export_csv(since: datetime | None = None, until: datetime | None = None) -> StreamingResponse:
        def stream_csv():
            session = db_session()
            try:
                rows = Repository(session).iter_observations(since=since, until=until)
                yield from iter_observations_csv(rows, config.receiver.timezone)
            finally:
                session.close()

        return StreamingResponse(
            stream_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=observations.csv"},
        )

    @app.get("/health")
    def health() -> JSONResponse:
        payload = api_status()
        code = 200 if payload.get("ok") else 503
        return JSONResponse(payload, status_code=code)

    @app.get("/favicon.ico", response_model=None)
    def favicon() -> FileResponse | JSONResponse:
        icon = static_dir / "favicon.ico"
        if icon.exists():
            return FileResponse(icon)
        return JSONResponse({}, status_code=204)

    return app
