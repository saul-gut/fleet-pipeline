"""Consultas del dashboard (D-20, D-23): el rol decide qué ID se ve y el alcance (periodo +
vehículos) se aplica igual a todas las consultas."""

from datetime import date

import psycopg
import pytest

from fleet_pipeline.queries import dashboard as q
from fleet_pipeline.security.auth import Role

pytestmark = pytest.mark.integration

ALL_SEVERITIES = ["minor", "moderate", "severe"]
URBAN_SPEEDING = q.AlertFilter(["SPEEDING:urban", "SPEEDING:none"], ALL_SEVERITIES)


def scope_of(trip_day: date, key: int) -> q.Scope:
    return q.Scope(trip_day, trip_day, [key])


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
    result = q.ranking(conn_autocommit, role, scope_of(trip_day, processed_trip[1]))
    assert [r["label"] for r in result.rows] == [expected]
    assert result.ms > 0


def test_analyst_never_receives_a_real_id(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int], trip_day: date
) -> None:
    real_id, key = processed_trip
    scope = scope_of(trip_day, key)
    results = [
        q.ranking(conn_autocommit, Role.ANALYST, scope),
        q.recent_alerts(conn_autocommit, Role.ANALYST, scope, URBAN_SPEEDING),
    ]
    for result in results:
        assert result.rows
        assert real_id not in str(result.rows)


def test_routes_return_the_whole_trip_in_order(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int], trip_day: date
) -> None:
    result = q.routes(conn_autocommit, Role.ANALYST, scope_of(trip_day, processed_trip[1]))
    assert len(result.rows) == 70
    timestamps = [r["ts"] for r in result.rows]
    assert timestamps == sorted(timestamps)


def test_route_sampling_keeps_a_fraction_of_the_points(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int], trip_day: date
) -> None:
    sampled = q.routes(
        conn_autocommit, Role.ANALYST, scope_of(trip_day, processed_trip[1]), sample_every=6
    )
    assert 5 <= len(sampled.rows) <= 20  # ~70 / 6


def test_vehicle_scope_excludes_other_vehicles(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int], trip_day: date
) -> None:
    """Fuera del alcance no aparece nada: ni en KPIs, ni en ranking, ni en alertas."""
    empty = q.Scope(trip_day, trip_day, [-1])
    assert q.ranking(conn_autocommit, Role.ANALYST, empty).rows == []
    assert q.recent_alerts(conn_autocommit, Role.ANALYST, empty, URBAN_SPEEDING).rows == []
    kpis = q.kpis(conn_autocommit, Role.ANALYST, empty, URBAN_SPEEDING).rows[0]
    assert kpis["active_vehicles"] == 0


def test_recent_alerts_groups_readings_into_one_episode(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int], trip_day: date
) -> None:
    result = q.recent_alerts(
        conn_autocommit, Role.ANALYST, scope_of(trip_day, processed_trip[1]), URBAN_SPEEDING
    )
    assert len(result.rows) == 1
    assert result.rows[0]["max_speed_kmh"] == 70
    assert result.rows[0]["severity"] == "moderate"


def test_alert_filter_by_severity_and_zone(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int], trip_day: date
) -> None:
    """El episodio de prueba es un exceso moderado en vía urbana."""
    scope = scope_of(trip_day, processed_trip[1])

    def count(alert_filter: q.AlertFilter) -> int:
        return len(q.recent_alerts(conn_autocommit, Role.ANALYST, scope, alert_filter).rows)

    assert count(q.AlertFilter(["SPEEDING:urban"], ["moderate"])) == 1
    assert count(q.AlertFilter(["SPEEDING:urban"], ["severe"])) == 0
    assert count(q.AlertFilter(["GEOFENCE_VIOLATION:school"], ["moderate"])) == 0


def test_kpis_follow_the_alert_filter(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int], trip_day: date
) -> None:
    scope = scope_of(trip_day, processed_trip[1])
    with_it = q.kpis(conn_autocommit, Role.ANALYST, scope, URBAN_SPEEDING).rows[0]
    without = q.kpis(
        conn_autocommit, Role.ANALYST, scope, q.AlertFilter(["SPEEDING:urban"], ["severe"])
    ).rows[0]
    assert (with_it["active_vehicles"], with_it["speeding"]) == (1, 1)
    assert without["speeding"] == 0


def test_directory_and_geofences_are_available_to_the_analyst(
    conn_autocommit: psycopg.Connection,
) -> None:
    regions = {r["region"] for r in q.vehicles(conn_autocommit, Role.ANALYST).rows}
    assert regions <= {"bogota", "medellin", "corridor"}
    geofences = q.geofences(conn_autocommit, Role.ANALYST).rows
    assert len(geofences) == 25
    assert geofences[0]["geometry"]["type"] == "MultiPolygon"


def test_heatmap_has_one_point_per_episode(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int], trip_day: date
) -> None:
    """El episodio de prueba tiene 3 lecturas sobre el umbral pero es una sola infracción."""
    result = q.infractions(
        conn_autocommit, Role.ANALYST, scope_of(trip_day, processed_trip[1]), URBAN_SPEEDING
    )
    assert len(result.rows) == 1
    assert result.rows[0]["severity"] == "moderate"
