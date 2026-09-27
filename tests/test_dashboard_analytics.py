from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from radio_logger.api.app import create_app
from radio_logger.database.models import Observation, Receiver


@pytest.fixture
def analytics_client(session_factory, config, runtime):
    return TestClient(create_app(config, session_factory, runtime))


@pytest.fixture
def add_observations(session_factory, config):
    def add(rows):
        with session_factory() as session:
            if session.get(Receiver, config.receiver.id) is None:
                session.add(
                    Receiver(
                        id=config.receiver.id,
                        name=config.receiver.name,
                        timezone=config.receiver.timezone,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                session.flush()
            for index, row in enumerate(rows):
                session.add(
                    Observation(
                        receiver_id=config.receiver.id,
                        timestamp_local=row["timestamp_utc"],
                        raw_message="analytics fixture",
                        fingerprint=f"analytics-{index}",
                        **row,
                    )
                )
            session.commit()

    return add


def test_lifetime_totals_include_old_rows_and_distinct_known_callsigns(
    analytics_client, add_observations
):
    first = datetime(2020, 9, 10, 23, 30, tzinfo=timezone.utc)
    rows = [
        ("JA1XYZ", "Japan", True),
        ("JA1XYZ", "Japan", True),
        ("VK2ABC", "Australia", False),
        (None, None, False),
        ("", "", True),
        ("JA2XYZ", "Japan", True),
    ]
    add_observations(
        [
            {
                "timestamp_utc": first + timedelta(hours=index),
                "tx_callsign": callsign,
                "country": country,
                "is_japan": japan,
            }
            for index, (callsign, country, japan) in enumerate(rows)
        ]
    )

    response = analytics_client.get("/api/stats/totals")

    assert response.status_code == 200
    assert response.json() == {
        "total_decodes": 6,
        "unique_callsigns": 3,
        "unique_countries": 2,
        "japan_decodes": 4,
        "japan_unique_callsigns": 2,
        "first_decode_at": first.isoformat(),
        "last_decode_at": (first + timedelta(hours=5)).isoformat(),
    }
    assert analytics_client.get("/api/stats/summary").json()["total_decodes"] == 0


def test_empty_lifetime_totals_have_zero_counts_and_no_dates(analytics_client):
    response = analytics_client.get("/api/stats/totals")

    assert response.status_code == 200
    assert response.json() == {
        "total_decodes": 0,
        "unique_callsigns": 0,
        "unique_countries": 0,
        "japan_decodes": 0,
        "japan_unique_callsigns": 0,
        "first_decode_at": None,
        "last_decode_at": None,
    }


def test_empty_24_hour_chart_retains_every_bucket_and_unknown_measurements(analytics_client):
    start = datetime(2026, 9, 10, tzinfo=timezone.utc)
    response = analytics_client.get(
        "/api/stats/japan-hour",
        params={"since": start.isoformat(), "until": (start + timedelta(days=1)).isoformat()},
    )

    assert response.status_code == 200
    report = response.json()
    assert report["since"] == start.isoformat()
    assert report["until"] == (start + timedelta(days=1)).isoformat()
    assert report["peak_local"] is None
    assert len(report["buckets"]) == 96
    assert [item["bucket_start_utc"] for item in report["buckets"]] == [
        (start + timedelta(minutes=15 * index)).isoformat() for index in range(96)
    ]
    for item in report["buckets"]:
        for key in (
            "total_decodes", "japan_decodes", "unique_callsigns", "japan_unique_callsigns",
            "unique_countries", "japan_unique_countries",
        ):
            assert item[key] == 0
        for key in (
            "japan_decode_share", "japan_unique_station_share", "median_japan_snr",
            "median_all_snr", "median_non_japan_snr", "japan_relative_snr",
            "median_japan_distance_km",
        ):
            assert item[key] is None
    assert len(report["time_of_day_local"]) == len(report["time_of_day_utc"]) == 24


def test_chart_pads_partial_midnight_range_and_honors_exact_boundaries(
    analytics_client, add_observations
):
    start = datetime(2026, 9, 10, 23, 50, tzinfo=timezone.utc)
    end = start + timedelta(minutes=45)
    add_observations(
        [
            {
                "timestamp_utc": when,
                "tx_callsign": callsign,
                "country": "Japan" if japan else "Australia",
                "is_japan": japan,
                "snr_db": -10 if japan else -16,
            }
            for when, callsign, japan in (
                (start - timedelta(seconds=1), "JA0OUT", True),
                (start, "JA1XYZ", True),
                (start + timedelta(minutes=15), "JA1XYZ", True),
                (end - timedelta(minutes=5), "VK2ABC", False),
                (end, "JA2OUT", True),
            )
        ]
    )

    response = analytics_client.get(
        "/api/stats/japan-hour",
        params={
            "since": start.astimezone(timezone(timedelta(hours=8))).isoformat(),
            "until": end.isoformat(),
            "bucket": 15,
            "tz": "UTC",
        },
    )

    assert response.status_code == 200
    report = response.json()
    assert report["since"] == start.isoformat()
    assert report["until"] == end.isoformat()
    assert [item["bucket_start_utc"] for item in report["buckets"]] == [
        "2026-09-10T23:45:00+00:00",
        "2026-09-11T00:00:00+00:00",
        "2026-09-11T00:15:00+00:00",
        "2026-09-11T00:30:00+00:00",
    ]
    assert [item["total_decodes"] for item in report["buckets"]] == [1, 1, 0, 1]
    assert report["totals"]["total_decodes"] == 3
    assert report["totals"]["unique_callsigns"] == 2
    assert report["totals"]["japan_unique_callsigns"] == 1
    assert report["totals"]["japan_decode_share"] == 0.6667
    assert report["totals"]["japan_relative_snr"] == 6.0


def test_chart_local_timestamps_distinguish_repeated_dst_hour(analytics_client):
    response = analytics_client.get(
        "/api/stats/japan-hour",
        params={
            "since": "2026-11-01T04:00:00Z",
            "until": "2026-11-01T09:00:00Z",
            "bucket": 60,
            "tz": "America/New_York",
        },
    )

    assert response.status_code == 200
    assert [item["bucket_start_local"] for item in response.json()["buckets"]] == [
        "2026-11-01T00:00:00-04:00",
        "2026-11-01T01:00:00-04:00",
        "2026-11-01T01:00:00-05:00",
        "2026-11-01T02:00:00-05:00",
        "2026-11-01T03:00:00-05:00",
    ]


@pytest.mark.parametrize(
    "params",
    [
        {"since": "2026-09-11T00:00:00Z", "until": "2026-09-11T00:00:00Z"},
        {"since": "2026-09-12T00:00:00Z", "until": "2026-09-11T00:00:00Z"},
        {"since": "2026-01-01T00:00:00Z", "until": "2026-03-01T00:00:00Z", "bucket": 5},
        {"until": "2026-09-11T00:00:00Z", "tz": "Invalid/Timezone"},
    ],
    ids=["empty-range", "reversed-range", "excessive-buckets", "invalid-zone-empty-database"],
)
def test_chart_rejects_invalid_ranges_and_timezone(analytics_client, params):
    response = analytics_client.get("/api/stats/japan-hour", params=params)

    assert response.status_code == 400
    assert response.json()["detail"]


def test_until_only_chart_keeps_history_without_imposing_start(analytics_client, add_observations):
    add_observations(
        [
            {
                "timestamp_utc": datetime(2020, 1, 1, tzinfo=timezone.utc),
                "tx_callsign": "JA1XYZ",
                "is_japan": True,
            },
            {
                "timestamp_utc": datetime(2026, 9, 11, tzinfo=timezone.utc),
                "tx_callsign": "JA2XYZ",
                "is_japan": True,
            },
        ]
    )

    response = analytics_client.get(
        "/api/stats/japan-hour", params={"until": "2026-09-11T00:00:00Z"}
    )

    assert response.status_code == 200
    report = response.json()
    assert report["since"] is None
    assert report["until"] == "2026-09-11T00:00:00+00:00"
    assert report["totals"]["total_decodes"] == 1
    assert len(report["buckets"]) == 1


def test_summary_and_chart_totals_preserve_metrics_with_repeated_calls_and_missing_data(
    analytics_client, add_observations
):
    start = datetime(2026, 9, 11, tzinfo=timezone.utc)
    add_observations(
        [
            {
                "timestamp_utc": start + timedelta(minutes=minute),
                "tx_callsign": callsign,
                "country": country,
                "is_japan": japan,
                "snr_db": snr,
                "distance_km": distance,
            }
            for minute, callsign, country, japan, snr, distance in (
                (0, "JA1XYZ", "Japan", True, -10, 5000),
                (1, "JA1XYZ", "Japan", True, -6, 6000),
                (16, "JA2XYZ", "Japan", True, None, None),
                (17, "VK2ABC", "Australia", False, -20, 1000),
                (31, None, None, False, None, None),
                (32, "", "", False, 0, None),
            )
        ]
    )
    bounds = {"since": start.isoformat(), "until": (start + timedelta(hours=1)).isoformat()}
    expected = {
        "total_decodes": 6,
        "unique_callsigns": 3,
        "unique_countries": 2,
        "japan_decodes": 3,
        "japan_unique_callsigns": 2,
        "japan_unique_countries": 1,
        "japan_decode_share": 0.5,
        "japan_unique_station_share": 0.6667,
        "median_all_snr": -8.0,
        "mean_all_snr": -9.0,
        "min_all_snr": -20.0,
        "max_all_snr": 0.0,
        "median_japan_snr": -8.0,
        "mean_japan_snr": -8.0,
        "min_japan_snr": -10.0,
        "max_japan_snr": -6.0,
        "median_non_japan_snr": -10.0,
        "japan_relative_snr": 2.0,
        "japan_relative_snr_definition": "median_japan_snr - median_non_japan_snr",
        "median_japan_distance_km": 5500.0,
        "max_japan_distance_km": 6000.0,
        "min_japan_distance_km": 5000.0,
        "mean_japan_distance_km": 5500.0,
    }

    summary = analytics_client.get("/api/stats/summary", params=bounds)
    chart = analytics_client.get("/api/stats/japan-hour", params=bounds)

    assert summary.status_code == chart.status_code == 200
    assert summary.json() == chart.json()["totals"] == expected
    buckets = chart.json()["buckets"]
    assert [bucket["total_decodes"] for bucket in buckets] == [2, 2, 2, 0]
    assert [bucket["unique_callsigns"] for bucket in buckets] == [1, 2, 0, 0]
    assert [bucket["median_japan_snr"] for bucket in buckets] == [-8.0, None, None, None]
    assert [bucket["median_all_snr"] for bucket in buckets] == [-8.0, -20.0, 0.0, None]
    assert chart.json()["time_of_day_local"][8] == {
        "hour": "08:00",
        "decodes": 6,
        "japan_decodes": 3,
        "unique_callsigns": 3,
        "japan_unique_callsigns": 2,
    }


@pytest.mark.parametrize("endpoint", ["totals", "japan-hour"])
def test_dashboard_stats_need_one_query_without_loading_full_observations(
    analytics_client, add_observations, session_factory, endpoint
):
    add_observations(
        [{"timestamp_utc": datetime(2026, 9, 11, tzinfo=timezone.utc), "tx_callsign": "JA1XYZ"}]
    )
    with session_factory() as session:
        engine = session.get_bind()
    queries = []

    def record_query(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    def reject_observation_load(_observation, _context):
        raise AssertionError("Dashboard analytics loaded a full observation object")

    event.listen(engine, "before_cursor_execute", record_query)
    event.listen(Observation, "load", reject_observation_load)
    try:
        response = analytics_client.get(
            f"/api/stats/{endpoint}",
            params={"since": "2026-09-11T00:00:00Z", "until": "2026-09-12T00:00:00Z"},
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_query)
        event.remove(Observation, "load", reject_observation_load)

    assert response.status_code == 200
    assert len(queries) == 1
    totals = response.json() if endpoint == "totals" else response.json()["totals"]
    assert totals["total_decodes"] == 1
