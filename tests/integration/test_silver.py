"""Procesamiento Bronce -> Plata en PostGIS: debe aplicar las mismas reglas que rules.py.

Cada escenario inserta lecturas en una geocerca real, ejecuta silver.process_new y compara el
resultado del SQL con el de las funciones puras de Python.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from fleet_pipeline.geofences.loader import load_geofences, read_features
from fleet_pipeline.processing.rules import (
    AlertType,
    alert_type_for,
    is_over_threshold,
    severity,
)
from fleet_pipeline.processing.silver import run_silver

pytestmark = pytest.mark.integration

T0 = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=30)


@pytest.fixture
def geofences_loaded(conninfo: str) -> None:
    with psycopg.connect(conninfo) as conn:
        row = conn.execute("SELECT count(*) FROM silver.geofence_parts").fetchone()
        if not row or row[0] == 0:
            load_geofences(conn, read_features())


@pytest.fixture
def conn_autocommit(conninfo: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(conninfo, autocommit=True) as connection:
        yield connection


def point_inside(conn: psycopg.Connection, code: str) -> tuple[float, float]:
    """Un punto garantizado dentro de la geocerca (lat, lon)."""
    row = conn.execute(
        "SELECT ST_Y(p), ST_X(p) FROM (SELECT ST_PointOnSurface(geom) AS p "
        "FROM silver.geofences WHERE code = %s) AS s",
        (code,),
    ).fetchone()
    assert row is not None, f"No existe la geocerca {code}"
    return row[0], row[1]


def insert_series(
    conn: psycopg.Connection, vehicle_id: str, lat: float, lon: float, speeds: list[float]
) -> None:
    """Lecturas cada 10 s en el mismo punto (la posición no importa para la velocidad)."""
    for i, speed in enumerate(speeds):
        conn.execute(
            """
            INSERT INTO bronze.telemetry_raw (vehicle_id, ts, lat, lon, speed_kmh, fuel_pct)
            VALUES (%s, %s, %s, %s, %s, 80)
            """,
            (vehicle_id, T0 + timedelta(seconds=10 * i), lat, lon, speed),
        )


def alerts_of(conn: psycopg.Connection, vehicle_id: str) -> list[tuple]:
    return conn.execute(
        """
        SELECT alert_type, severity, (details->>'max_speed_kmh')::float,
               (details->>'readings')::int, details->>'zone_type'
        FROM silver.alerts WHERE vehicle_id = %s ORDER BY started_at
        """,
        (vehicle_id,),
    ).fetchall()


def test_school_zone_excess_is_a_geofence_violation(
    conn_autocommit: psycopg.Connection, geofences_loaded: None, test_vehicles: tuple[str, ...]
) -> None:
    vehicle = test_vehicles[0]
    lat, lon = point_inside(conn_autocommit, "BOG-SCH-01")
    insert_series(conn_autocommit, vehicle, lat, lon, [28, 45, 46, 30])

    run_silver(conn_autocommit)

    # La zona escolar (30) gana sobre el perímetro urbano (50): regla más restrictiva.
    limits = conn_autocommit.execute(
        "SELECT DISTINCT speed_limit_kmh, zone_type FROM silver.telemetry_enriched "
        "WHERE vehicle_id = %s",
        (vehicle,),
    ).fetchall()
    assert limits == [(30, "school")]
    expected = (alert_type_for("school"), severity(46, 30), 46.0, 2, "school")
    assert alerts_of(conn_autocommit, vehicle) == [expected]
    assert expected[:2] == (AlertType.GEOFENCE_VIOLATION, "severe")


def test_single_spike_does_not_alert_but_sustained_excess_does(
    conn_autocommit: psycopg.Connection, geofences_loaded: None, test_vehicles: tuple[str, ...]
) -> None:
    vehicle = test_vehicles[1]
    lat, lon = 4.7050, -74.0420  # Usaquén: solo dentro del perímetro urbano
    insert_series(conn_autocommit, vehicle, lat, lon, [40, 70, 40, 70, 72, 40])

    run_silver(conn_autocommit)

    flags = conn_autocommit.execute(
        "SELECT speed_kmh, speed_limit_kmh, is_over_threshold FROM silver.telemetry_enriched "
        "WHERE vehicle_id = %s ORDER BY ts",
        (vehicle,),
    ).fetchall()
    assert [over for _, _, over in flags] == [
        is_over_threshold(speed, limit) for speed, limit, _ in flags
    ]
    assert alerts_of(conn_autocommit, vehicle) == [
        (AlertType.SPEEDING, severity(72, 50), 72.0, 2, "urban")
    ]


def test_reprocessing_does_not_duplicate_alerts(
    conn_autocommit: psycopg.Connection, geofences_loaded: None, test_vehicles: tuple[str, ...]
) -> None:
    vehicle = test_vehicles[0]
    lat, lon = point_inside(conn_autocommit, "BOG-SCH-01")
    insert_series(conn_autocommit, vehicle, lat, lon, [45, 46])

    run_silver(conn_autocommit)
    run_silver(conn_autocommit)  # el solape de la marca de agua vuelve a leer las mismas filas

    assert len(alerts_of(conn_autocommit, vehicle)) == 1


def test_enriched_rows_match_bronze(
    conn_autocommit: psycopg.Connection, geofences_loaded: None, test_vehicles: tuple[str, ...]
) -> None:
    vehicle = test_vehicles[1]
    insert_series(conn_autocommit, vehicle, 4.7050, -74.0420, [30, 31, 32])

    run_silver(conn_autocommit)

    row = conn_autocommit.execute(
        """
        SELECT count(*), bool_and(e.fuel_l = 0.8 * 80)
        FROM silver.telemetry_enriched e JOIN bronze.telemetry_raw b USING (vehicle_id, ts)
        WHERE e.vehicle_id = %s
        """,
        (vehicle,),
    ).fetchone()
    assert row == (3, True)  # 80 % de un tanque de 80 L = 64 L
