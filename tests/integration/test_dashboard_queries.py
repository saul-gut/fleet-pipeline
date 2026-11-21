"""Consultas del dashboard (D-20): el rol decide qué ID se ve; la base lo garantiza."""

from datetime import date

import psycopg
import pytest

from fleet_pipeline.queries import dashboard as q
from fleet_pipeline.security.auth import Role

pytestmark = pytest.mark.integration

SPEEDING_ALL = q.AlertFilter(["SPEEDING:urban", "SPEEDING:none"], ["minor", "moderate", "severe"])


@pytest.mark.parametrize(
    ("role", "expected"), [(Role.ADMIN, "VHC-9001-TST"), (Role.ANALYST, "VHC-****-TST")]
)
def test_ranking_label_depends_on_role(
    conn_autocommit: psycopg.Connection,
    processed_trip: tuple[str, int],
    trip_day: date,
    role: Role,
    expected: str,
) -> None:
    result = q.ranking(conn_autocommit, role, trip_day, trip_day)
    rows = [r for r in result.rows if r["vehicle_key"] == processed_trip[1]]
    assert len(rows) == 1
    assert rows[0]["label"] == expected
    assert result.ms > 0


def test_analyst_never_receives_a_real_id(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int], trip_day: date
) -> None:
    real_id = processed_trip[0]
    results = [
        q.vehicles(conn_autocommit, Role.ANALYST),
        q.ranking(conn_autocommit, Role.ANALYST, trip_day, trip_day),
        q.recent_alerts(conn_autocommit, Role.ANALYST, trip_day, trip_day, SPEEDING_ALL),
    ]
    for result in results:
        assert real_id not in str(result.rows)


def test_route_returns_the_whole_trip_in_order(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int], trip_day: date
) -> None:
    result = q.route(conn_autocommit, Role.ANALYST, processed_trip[1], trip_day)
    assert len(result.rows) == 70
    timestamps = [r["ts"] for r in result.rows]
    assert timestamps == sorted(timestamps)


def test_recent_alerts_groups_readings_into_one_episode(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int], trip_day: date
) -> None:
    result = q.recent_alerts(conn_autocommit, Role.ANALYST, trip_day, trip_day, SPEEDING_ALL)
    mine = [r for r in result.rows if r["label"] == "VHC-****-TST"]
    assert len(mine) == 1
    assert mine[0]["max_speed_kmh"] == 70
    assert mine[0]["severity"] == "moderate"


def test_kpis_and_geofences_are_available_to_the_analyst(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int], trip_day: date
) -> None:
    kpis = q.kpis(conn_autocommit, Role.ANALYST, trip_day, trip_day).rows[0]
    assert kpis["active_vehicles"] >= 1
    assert kpis["speeding"] >= 1
    geofences = q.geofences(conn_autocommit, Role.ANALYST).rows
    assert len(geofences) == 25
    assert geofences[0]["geometry"]["type"] == "MultiPolygon"


def test_alert_filter_by_severity_and_zone(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int], trip_day: date
) -> None:
    """El episodio de prueba es un exceso moderado en vía urbana."""

    def mine(alert_filter: q.AlertFilter) -> list[dict]:
        result = q.recent_alerts(conn_autocommit, Role.ANALYST, trip_day, trip_day, alert_filter)
        return [r for r in result.rows if r["label"] == "VHC-****-TST"]

    assert len(mine(q.AlertFilter(["SPEEDING:urban"], ["moderate"]))) == 1
    assert mine(q.AlertFilter(["SPEEDING:urban"], ["severe"])) == []
    assert mine(q.AlertFilter(["GEOFENCE_VIOLATION:school"], ["moderate"])) == []
