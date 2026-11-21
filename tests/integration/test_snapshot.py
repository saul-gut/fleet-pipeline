"""La foto en Parquet + DuckDB responde lo mismo que PostGIS (D-26).

Se exporta la base de prueba, se consulta con las dos fuentes y se compara fila por fila,
para los dos roles. Así el tablero publicado muestra lo mismo que el local.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest

from fleet_pipeline.queries import dashboard as q
from fleet_pipeline.queries.snapshot import SnapshotQueries, export_snapshot
from fleet_pipeline.security.auth import Role

pytestmark = pytest.mark.integration

ALL_SEVERITIES = ["minor", "moderate", "severe"]
ALL_CATEGORIES = [
    f"{alert}:{zone}"
    for alert in ("SPEEDING", "GEOFENCE_VIOLATION")
    for zone in ("urban", "highway", "school", "high_risk", "road_hazard", "none")
]
# Métricas redondeadas que PostgreSQL calcula con `numeric` exacto y DuckDB con `double`:
# en el borde del redondeo pueden diferir en la última cifra.
# excess_kmh: PostgreSQL lo guarda como `real` (32 bits) y la foto lo calcula en `double`.
ROUNDED = {
    "km": 1.0,
    "distance_m": 1.0,
    "score": 0.1,
    "fleet_score": 0.1,
    "pct_over": 0.1,
    "excess_kmh": 1e-4,
}


@pytest.fixture
def snapshot(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int], tmp_path: Path
) -> SnapshotQueries:
    counts = export_snapshot(conn_autocommit, tmp_path)
    assert counts["fleet_events"] >= 70  # incluye el recorrido de prueba
    return SnapshotQueries(tmp_path)


def assert_same_rows(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> None:
    assert len(actual) == len(expected)
    for exp, act in zip(expected, actual, strict=True):
        assert act.keys() == exp.keys()
        for key, value in exp.items():
            other = act[key]
            if isinstance(value, int | float | Decimal) and not isinstance(value, bool):
                assert other is not None, key
                tolerance = ROUNDED.get(key, 1e-6)
                assert float(other) == pytest.approx(float(value), abs=tolerance), key
            else:
                assert other == value, key


def both(
    conn: psycopg.Connection, snap: SnapshotQueries, name: str, *args: Any
) -> tuple[list, list]:
    postgres: Callable[..., q.QueryResult] = getattr(q, name)
    return postgres(conn, *args).rows, getattr(snap, name)(*args).rows


@pytest.mark.parametrize("role", [Role.ADMIN, Role.ANALYST])
def test_snapshot_matches_postgis_for_every_dashboard_query(
    conn_autocommit: psycopg.Connection,
    snapshot: SnapshotQueries,
    processed_trip: tuple[str, int],
    trip_day: date,
    role: Role,
) -> None:
    keys = [r["vehicle_key"] for r in q.vehicles(conn_autocommit, Role.ADMIN).rows]
    scopes = [
        q.Scope(trip_day, trip_day, [processed_trip[1]]),  # un vehículo, un día
        q.Scope(date(2000, 1, 1), date(2100, 1, 1), keys + [processed_trip[1]]),  # todo
    ]
    alert_filter = q.AlertFilter(ALL_CATEGORIES, ALL_SEVERITIES)

    calls: list[tuple[str, tuple]] = [("date_bounds", ()), ("vehicles", ()), ("geofences", ())]
    for scope in scopes:
        calls += [
            ("kpis", (scope, alert_filter)),
            ("ranking", (scope,)),
            ("routes", (scope, 1)),
            ("routes", (scope, 6)),
            ("infractions", (scope, alert_filter)),
            ("recent_alerts", (scope, alert_filter)),
            ("fuel_alerts", (scope, ALL_SEVERITIES)),
        ]
    for name, args in calls:
        expected, actual = both(conn_autocommit, snapshot, name, role, *args)
        assert_same_rows(expected, actual)


def test_analyst_sees_only_masked_ids_in_the_snapshot(
    snapshot: SnapshotQueries, processed_trip: tuple[str, int], trip_day: date
) -> None:
    real_id, key = processed_trip
    scope = q.Scope(trip_day, trip_day, [key])
    results = [
        snapshot.vehicles(Role.ANALYST),
        snapshot.ranking(Role.ANALYST, scope),
        snapshot.recent_alerts(Role.ANALYST, scope, q.AlertFilter(ALL_CATEGORIES, ALL_SEVERITIES)),
    ]
    assert results[1].rows
    for result in results:
        assert real_id not in str(result.rows)
