from __future__ import annotations

import csv
import io
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml
import pytest
from fastapi.testclient import TestClient

from radio_logger.analytics.japan_hour import japan_hour_buckets, today_bounds
from radio_logger.api.app import create_app
from radio_logger.cli import export_cmd
from radio_logger.config import load_config
from radio_logger.database.models import Receiver
from radio_logger.models import RawDecode


def _raw(when: datetime, message: str, *, source: str = "simulator") -> RawDecode:
    return RawDecode(
        source=source,
        instance_id="TEST",
        decode_time_utc=when,
        snr_db=-12,
        dt=0.2,
        df=1000,
        raw_message=message,
        dial_frequency_hz=14_074_000,
    )


def test_config_environment_overrides_yaml_with_documented_names(tmp_path, monkeypatch):
    config_path = tmp_path / "receiver.yaml"
    config_path.write_text(yaml.safe_dump({"udp": {"port": 1111}, "receiver": {"locator": "AA00"}}))
    monkeypatch.setenv("RADIO_LOGGER_CONFIG", str(config_path))
    monkeypatch.setenv("RADIO_LOGGER_UDP_PORT", "23456")
    monkeypatch.setenv("RADIO_LOGGER_RECEIVER__LOCATOR", "OJ11")
    monkeypatch.setenv("RADIO_LOGGER_JAPAN__DXCC_ENTITIES", '["Japan"]')

    config = load_config()

    assert config.udp.port == 23456
    assert config.receiver.locator == "OJ11"
    assert config.japan.dxcc_entities == ["Japan"]


def test_today_bounds_follow_local_midnights_across_dst():
    spring_start, spring_end = today_bounds(
        datetime(2026, 3, 8, 16, tzinfo=timezone.utc), "America/New_York"
    )
    fall_start, fall_end = today_bounds(
        datetime(2026, 11, 1, 16, tzinfo=timezone.utc), "America/New_York"
    )

    assert (spring_end - spring_start).total_seconds() == 23 * 60 * 60
    assert (fall_end - fall_start).total_seconds() == 25 * 60 * 60


def test_japan_hour_peak_is_null_without_japan(ingestor, session_factory):
    ingestor.ingest_raw(
        _raw(datetime(2026, 9, 11, tzinfo=timezone.utc), "CQ VK2ABC QF56"),
        skip_dedupe=True,
    )
    session = session_factory()
    try:
        result = japan_hour_buckets(session)
    finally:
        session.close()

    assert result["peak_local"] is None


def test_stats_preserve_explicit_until_and_reject_invalid_timezone(
    ingestor, session_factory, config, runtime
):
    before = datetime(2026, 9, 10, 23, tzinfo=timezone.utc)
    after = datetime(2026, 9, 11, 1, tzinfo=timezone.utc)
    ingestor.ingest_raw(_raw(before, "CQ JA1XYZ PM95"), skip_dedupe=True)
    ingestor.ingest_raw(_raw(after, "CQ JA2XYZ PM95"), skip_dedupe=True)
    client = TestClient(create_app(config, session_factory, runtime), raise_server_exceptions=False)

    until = "2026-09-11T00:00:00Z"
    assert client.get(f"/api/stats/summary?until={until}").json()["total_decodes"] == 1
    assert client.get(f"/api/stats/countries?until={until}").json()["items"][0]["decodes"] == 1
    distance = client.get(f"/api/stats/distance?until={until}").json()["items"]
    assert sum(item["count"] for item in distance) == 1
    snr = client.get(f"/api/stats/snr?until={until}").json()["all"]
    assert sum(item["count"] for item in snr) == 1
    assert client.get(f"/api/stats/japan-hour?until={until}").json()["totals"]["total_decodes"] == 1
    assert client.get("/api/stats/japan-hour?tz=Not/A_Zone").status_code == 400


def test_status_online_and_today_counts_follow_recent_udp_activity(
    ingestor, session_factory, config, runtime
):
    now = datetime.now(tz=timezone.utc)
    ingestor.ingest_raw(_raw(now, "CQ JA1XYZ PM95", source="all_txt"), skip_dedupe=True)
    ingestor.ingest_raw(_raw(now.replace(year=now.year + 1), "CQ JA2XYZ PM95"), skip_dedupe=True)
    client = TestClient(create_app(config, session_factory, runtime))

    offline = client.get("/api/status").json()
    assert offline["online"] is False
    assert offline["decodes_today"] == 1
    runtime.last_udp_at = datetime.now(tz=timezone.utc)
    assert client.get("/api/status").json()["online"] is True


def test_api_normalizes_sqlite_timestamps_and_uses_receiver_timezone(
    ingestor, session_factory, config, runtime
):
    session = session_factory()
    try:
        session.get(Receiver, config.receiver.id).timezone = "America/New_York"
        session.commit()
    finally:
        session.close()
    ingestor.ingest_raw(
        _raw(datetime(2026, 9, 11, 12, tzinfo=timezone.utc), "CQ JA1XYZ PM95"),
        skip_dedupe=True,
    )
    item = TestClient(create_app(config, session_factory, runtime)).get(
        "/api/observations/latest"
    ).json()["items"][0]

    assert item["timestamp_utc"] == "2026-09-11T12:00:00+00:00"
    assert item["timestamp_local"] == "2026-09-11T08:00:00-04:00"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is needed for the DOM harness")
def test_latest_table_renders_stored_messages_as_text():
    app_js = Path("src/radio_logger/web/static/app.js").resolve()
    payload = json.dumps(
        {
            "items": [
                {
                    "timestamp_local": "2026-09-11T20:00:00+08:00",
                    "timestamp_utc": "2026-09-11T12:00:00+00:00",
                    "snr_db": -12,
                    "df": 1000,
                    "tx_callsign": "JA1XYZ",
                    "tx_grid": "PM95",
                    "country": "Japan",
                    "distance_km": 5300,
                    "raw_message": '<img src=x onerror="globalThis.injected=true"> <HASHED:CALL>',
                    "is_japan": True,
                }
            ]
        }
    )
    script = f"""
const fs = require('fs');
const vm = require('vm');
class Node {{
  constructor(tag) {{ this.tag = tag; this.children = []; this.textContent = ''; this.className = ''; }}
  append(...nodes) {{ this.children.push(...nodes); }}
  replaceChildren(...nodes) {{ this.children = nodes; }}
}}
const body = new Node('tbody');
const context = {{
  document: {{ getElementById: () => body, createElement: (tag) => new Node(tag) }},
  fetch: async () => ({{ ok: true, json: async () => ({payload}) }}),
  Chart: function () {{}},
  setInterval: () => {{}},
  console,
}};
vm.createContext(context);
let source = fs.readFileSync({json.dumps(str(app_js))}, 'utf8');
source = source.split('tickFast();')[0];
vm.runInContext(source, context);
context.refreshLatest().then(() => {{
  const message = body.children[0].children[8];
  process.stdout.write(JSON.stringify({{ text: message.textContent, children: message.children.length, injected: context.injected }}));
}});
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    assert json.loads(result.stdout) == {
        "text": '<img src=x onerror="globalThis.injected=true"> <HASHED:CALL>',
        "children": 0,
    }


def test_dashboard_metadata_is_json_encoded(session_factory, config, runtime):
    config.receiver.locator = '</script><script>globalThis.injected=true</script>'
    response = TestClient(create_app(config, session_factory, runtime)).get("/")

    assert response.status_code == 200
    assert '"receiverLocator": "\\u003c/script\\u003e' in response.text
    assert "globalThis.injected=true</script>" not in response.text


def test_api_csv_uses_uncapped_iterator(
    ingestor, session_factory, config, runtime, monkeypatch
):
    for hour, callsign in enumerate(("JA1AAA", "JA1BBB", "JA1CCC")):
        ingestor.ingest_raw(
            _raw(
                datetime(2026, 9, 11, hour, tzinfo=timezone.utc),
                f"CQ {callsign} PM95",
            ),
            skip_dedupe=True,
        )

    def reject_capped_query(*args, **kwargs):
        raise AssertionError("CSV export used the paginated observation query")

    monkeypatch.setattr(
        "radio_logger.database.repository.Repository.list_observations",
        reject_capped_query,
    )
    response = TestClient(create_app(config, session_factory, runtime)).get(
        "/api/export/csv?since=2026-09-11T00:30:00Z&until=2026-09-11T02:00:00Z"
    )
    rows = list(csv.DictReader(io.StringIO(response.text)))

    assert response.status_code == 200
    assert [row["tx_callsign"] for row in rows] == ["JA1BBB"]
    assert rows[0]["timestamp_utc"] == "2026-09-11T01:00:00+00:00"


def test_cli_csv_uses_uncapped_iterator_and_honors_date_bounds(
    ingestor, session_factory, config, tmp_path, monkeypatch, capsys
):
    for hour, callsign in enumerate(("JA1AAA", "JA1BBB", "JA1CCC")):
        ingestor.ingest_raw(
            _raw(
                datetime(2026, 9, 11, hour, tzinfo=timezone.utc),
                f"CQ {callsign} PM95",
            ),
            skip_dedupe=True,
        )

    def reject_capped_query(*args, **kwargs):
        raise AssertionError("CSV export used the paginated observation query")

    monkeypatch.setattr(
        "radio_logger.database.repository.Repository.list_observations",
        reject_capped_query,
    )
    monkeypatch.setattr("radio_logger.cli._cfg", lambda _path: config)
    monkeypatch.setattr("radio_logger.service.init_database", lambda _config: session_factory)
    output = tmp_path / "bounded.csv"

    export_cmd(
        output=output,
        since="2026-09-11T00:30:00Z",
        until="2026-09-11T02:00:00Z",
        fmt="csv",
        config=None,
    )
    with output.open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert [row["tx_callsign"] for row in rows] == ["JA1BBB"]
    assert rows[0]["timestamp_utc"] == "2026-09-11T01:00:00+00:00"
    assert "wrote 1 rows" in capsys.readouterr().out
