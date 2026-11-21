"""Capa Oro: privacidad por diseño y Driver Score idéntico al de rules.py (D-19)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import errors

from fleet_pipeline.geofences.loader import load_geofences, read_features
from fleet_pipeline.processing.gold import run_gold
from fleet_pipeline.processing.rules import driver_score, penalty_points
from fleet_pipeline.processing.silver import run_silver

pytestmark = pytest.mark.integration

START = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
STEP_DEG = 0.001  # ~111 m hacia el norte cada 10 s = 40 km/h


@pytest.fixture
def conn_autocommit(conninfo: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(conninfo, autocommit=True) as connection:
        if connection.execute("SELECT count(*) FROM silver.geofence_parts").fetchone() == (0,):
            load_geofences(connection, read_features())
        yield connection


def drive_north(conn: psycopg.Connection, vehicle_id: str, speeds: list[float]) -> None:
    """Un recorrido de ~111 m por lectura por el norte de Bogotá (solo perímetro urbano)."""
    for i, speed in enumerate(speeds):
        conn.execute(
            """
            INSERT INTO bronze.telemetry_raw (vehicle_id, ts, lat, lon, speed_kmh, fuel_pct)
            VALUES (%s, %s, %s, -74.0350, %s, 80)
            """,
            (vehicle_id, START + timedelta(seconds=10 * i), 4.7000 + STEP_DEG * i, speed),
        )


@pytest.fixture
def processed_trip(
    conn_autocommit: psycopg.Connection, test_vehicles: tuple[str, ...]
) -> tuple[str, int]:
    """~7,7 km a 40 km/h con un exceso sostenido de 3 lecturas a 70 km/h."""
    vehicle = test_vehicles[0]
    speeds = [40.0] * 70
    speeds[10:13] = [70.0, 70.0, 70.0]
    drive_north(conn_autocommit, vehicle, speeds)
    run_silver(conn_autocommit)
    run_gold(conn_autocommit)
    key = conn_autocommit.execute(
        "SELECT vehicle_key FROM bronze.vehicles WHERE vehicle_id = %s", (vehicle,)
    ).fetchone()
    assert key is not None
    return vehicle, key[0]


def test_gold_never_stores_real_ids(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int]
) -> None:
    vehicle, key = processed_trip
    ids = conn_autocommit.execute(
        "SELECT DISTINCT vehicle_id_masked FROM gold.fleet_events WHERE vehicle_key = %s", (key,)
    ).fetchall()
    assert ids == [("VHC-****-TST",)]
    leaked = conn_autocommit.execute(
        "SELECT count(*) FROM gold.fleet_events WHERE vehicle_id_masked = %s", (vehicle,)
    ).fetchone()
    assert leaked == (0,)


def test_analyst_reads_gold_but_not_bronze(conninfo: str, processed_trip: tuple[str, int]) -> None:
    with psycopg.connect(conninfo) as conn:
        conn.execute("SET LOCAL ROLE bi_analyst")
        row = conn.execute(
            "SELECT count(*) FROM gold.fleet_events WHERE vehicle_key = %s", (processed_trip[1],)
        ).fetchone()
        assert row == (70,)
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM bronze.vehicles LIMIT 1")


def test_alert_readings_are_linked_to_their_episode(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int]
) -> None:
    rows = conn_autocommit.execute(
        "SELECT alert_type, severity, count(*) FROM gold.fleet_events "
        "WHERE vehicle_key = %s AND alert_id IS NOT NULL GROUP BY 1, 2",
        (processed_trip[1],),
    ).fetchall()
    assert rows == [("SPEEDING", "moderate", 3)]  # 70 en zona de 50 = +40 %


def test_sql_driver_score_matches_python(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int]
) -> None:
    row = conn_autocommit.execute(
        "SELECT km, penalty_points, score FROM gold.driver_score WHERE vehicle_key = %s",
        (processed_trip[1],),
    ).fetchone()
    assert row is not None
    km, points, score = row
    assert points == penalty_points([("SPEEDING", "moderate")]) == 3
    assert km == pytest.approx(7.66, abs=0.1)
    assert float(score) == pytest.approx(driver_score(points, km), abs=0.05)
