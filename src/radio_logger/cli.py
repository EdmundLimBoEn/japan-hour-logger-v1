from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from radio_logger.config import AppConfig, load_config, reset_config_cache
from radio_logger.database.backup import backup_sqlite, sqlite_path_from_url
from radio_logger.database.engine import database_size_bytes

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Japan Hour FT8 receive-only logger")


def _cfg(config: Optional[Path]) -> AppConfig:
    reset_config_cache()
    return load_config(config)


def _parse_utc(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


@app.command()
def run(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="YAML config path"),
    host: Optional[str] = typer.Option(None, help="HTTP bind host"),
    port: Optional[int] = typer.Option(None, help="HTTP bind port"),
    udp_host: Optional[str] = typer.Option(None, help="WSJT-X UDP host"),
    udp_port: Optional[int] = typer.Option(None, help="WSJT-X UDP port"),
    no_udp: bool = typer.Option(False, help="Serve API/dashboard without opening UDP"),
) -> None:
    """Start UDP ingest plus the API/dashboard."""
    import asyncio

    from radio_logger.service import run_server

    cfg = _cfg(config)
    if udp_host:
        cfg.udp.host = udp_host
    if udp_port:
        cfg.udp.port = udp_port
    asyncio.run(run_server(cfg, listen_udp=not no_udp, host=host, port=port))


@app.command()
def status(
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    url: Optional[str] = typer.Option(None, help="Running logger base URL"),
) -> None:
    """Show local DB counts or query a running logger."""
    if url:
        import httpx

        response = httpx.get(url.rstrip("/") + "/api/status", timeout=5.0)
        response.raise_for_status()
        typer.echo(response.text)
        return
    from radio_logger.analytics.japan_hour import summary
    from radio_logger.database.repository import Repository
    from radio_logger.service import init_database

    cfg = _cfg(config)
    factory = init_database(cfg)
    session = factory()
    try:
        repo = Repository(session)
        latest = repo.latest_observations(limit=3)
        stats = summary(session)
        typer.echo(f"database: {cfg.database.url}")
        typer.echo(f"receiver: {cfg.receiver.id} locator={cfg.receiver.locator}")
        typer.echo(f"decodes: {stats['total_decodes']}  japan: {stats['japan_decodes']}  stations: {stats['unique_callsigns']}")
        for row in latest:
            typer.echo(f"  {row.timestamp_utc} {row.snr_db} {row.tx_callsign} {row.country} {row.raw_message}")
    finally:
        session.close()


@app.command()
def replay(
    path: Path = typer.Argument(..., exists=True, readable=True),
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    speed: float = typer.Option(0.0, help="Playback speed. 0 = as fast as possible. 1 = original timing."),
) -> None:
    """Import/replay WSJT-X ALL.TXT or JSONL into the same NormalizedDecode path as UDP."""
    from radio_logger.service import build_ingestor, replay_file

    cfg = _cfg(config)
    ingestor, _runtime, _factory = build_ingestor(cfg)
    stored = replay_file(ingestor, path, speed=speed)
    typer.echo(f"stored {stored} observations from {path}")


@app.command("export")
def export_cmd(
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
    since: Optional[str] = typer.Option(None, "--from", help="UTC start (ISO-8601)"),
    until: Optional[str] = typer.Option(None, "--to", help="UTC end (ISO-8601)"),
    fmt: str = typer.Option("csv", "--format"),
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Export observations."""
    if fmt != "csv":
        raise typer.BadParameter("V1 supports --format csv only")
    from radio_logger.database.repository import Repository
    from radio_logger.export_csv import observations_to_csv
    from radio_logger.service import init_database

    cfg = _cfg(config)
    factory = init_database(cfg)
    session = factory()
    try:
        rows, total = Repository(session).list_observations(
            limit=1_000_000, offset=0, since=_parse_utc(since), until=_parse_utc(until)
        )
        csv_text = observations_to_csv(rows)
    finally:
        session.close()
    dest = output or Path(cfg.paths.exports_dir) / "observations.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(csv_text, encoding="utf-8")
    typer.echo(f"wrote {total} rows to {dest}")


analyze_app = typer.Typer(help="Analytics commands")
app.add_typer(analyze_app, name="analyze")


@analyze_app.command("japan-hour")
def analyze_japan_hour(
    bucket: int = typer.Option(15, help="Bucket minutes: 5, 15, 30, 60"),
    since: Optional[str] = typer.Option(None, "--from"),
    until: Optional[str] = typer.Option(None, "--to"),
    tz: Optional[str] = typer.Option(None, help="Display timezone (default Asia/Singapore)"),
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Print Japan Hour absolute and relative metrics."""
    from radio_logger.analytics.japan_hour import japan_hour_buckets
    from radio_logger.service import init_database

    cfg = _cfg(config)
    factory = init_database(cfg)
    session = factory()
    try:
        report = japan_hour_buckets(
            session,
            since=_parse_utc(since),
            until=_parse_utc(until),
            bucket_minutes=bucket,
            display_timezone=tz or cfg.analytics.display_timezone,
        )
    finally:
        session.close()
    totals = report["totals"]
    typer.echo(f"buckets={report['bucket_minutes']}m tz={report['display_timezone']}")
    typer.echo(f"definition: {report['japan_relative_snr_definition']}")
    typer.echo(
        "totals: decodes={total_decodes} japan={japan_decodes} share={japan_decode_share} "
        "stations={unique_callsigns}/{japan_unique_callsigns} "
        "median_snr={median_all_snr}/{median_japan_snr} rel_snr={japan_relative_snr} "
        "median_km={median_japan_distance_km}".format(**{k: totals.get(k) for k in totals})
    )
    if report["peak_local"]:
        peak = report["peak_local"]
        typer.echo(f"peak SGT {peak['local_hour']}: japan_decodes={peak['japan_decodes']} share={peak['japan_decode_share']}")
    typer.echo("local  utc   all  ja  share  uniq  ja_uniq  medJA  rel  med_km")
    for b in report["buckets"]:
        typer.echo(
            f"{b['local_hour']:5} {b['utc_hour']:5} {b['total_decodes']:4} {b['japan_decodes']:3} "
            f"{(b['japan_decode_share'] or 0):5.2f} {b['unique_callsigns']:4} {b['japan_unique_callsigns']:4} "
            f"{b['median_japan_snr']!s:>6} {b['japan_relative_snr']!s:>5} {b['median_japan_distance_km']!s:>7}"
        )


@app.command("db-info")
def db_info(config: Optional[Path] = typer.Option(None, "--config", "-c")) -> None:
    from radio_logger.database.models import Observation, Station
    from radio_logger.service import init_database
    from sqlalchemy import func, select

    cfg = _cfg(config)
    factory = init_database(cfg)
    session = factory()
    try:
        obs = session.scalar(select(func.count()).select_from(Observation)) or 0
        stations = session.scalar(select(func.count()).select_from(Station)) or 0
        japan = session.scalar(select(func.count()).select_from(Observation).where(Observation.is_japan.is_(True))) or 0
    finally:
        session.close()
    size = database_size_bytes(cfg.database.url)
    path = sqlite_path_from_url(cfg.database.url)
    typer.echo(f"url={cfg.database.url}")
    typer.echo(f"path={path}")
    typer.echo(f"size_bytes={size}")
    typer.echo(f"observations={obs} japan={japan} stations={stations}")


@app.command("db-backup")
def db_backup(config: Optional[Path] = typer.Option(None, "--config", "-c")) -> None:
    cfg = _cfg(config)
    dest = backup_sqlite(cfg.database.url, cfg.paths.backups_dir)
    typer.echo(f"backup {dest}")


@app.command("seed")
def seed(
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    fixture: Optional[Path] = typer.Option(None, help="ALL.TXT fixture (default tests/fixtures/sample_all.txt)"),
) -> None:
    """Load sample decodes so the dashboard works without WSJT-X."""
    from importlib import resources

    from radio_logger.service import build_ingestor, replay_file

    cfg = _cfg(config)
    ingestor, _runtime, _factory = build_ingestor(cfg)
    if fixture is not None:
        path = fixture
    else:
        root = Path(__file__).resolve().parents[2]
        path = root / "tests" / "fixtures" / "sample_all.txt"
        if not path.exists():
            bundled = resources.files("radio_logger").joinpath("resources/sample_all.txt")
            path = Path(str(bundled))
    stored = replay_file(ingestor, path, speed=0.0, preserve_copy=False)
    typer.echo(f"seeded {stored} observations from {path}")


@app.command("simulate")
def simulate(
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    japan_spike: bool = typer.Option(False, help="Generate a Japan-activity spike for analytics QA"),
    count: int = typer.Option(40, help="Used when --japan-spike is off"),
) -> None:
    from radio_logger.service import build_ingestor
    from radio_logger.simulator import japan_spike_series, simulate_decode

    cfg = _cfg(config)
    ingestor, _runtime, _factory = build_ingestor(cfg)
    series = japan_spike_series() if japan_spike else [simulate_decode() for _ in range(count)]
    stored = 0
    for raw in series:
        if ingestor.ingest_raw(raw, skip_dedupe=True):
            stored += 1
    typer.echo(f"simulated {stored} observations")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
