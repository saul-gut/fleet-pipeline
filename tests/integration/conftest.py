"""Fixtures para tests contra PostGIS real (docker compose up).

Si la base no responde, los tests de integración se omiten con un aviso en lugar de fallar,
para que los unitarios se puedan correr en cualquier máquina.
"""

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient

from fleet_pipeline.api.main import app
from fleet_pipeline.config import Settings, get_settings
from fleet_pipeline.geofences.loader import load_geofences, read_features
from fleet_pipeline.processing.gold import run_gold
from fleet_pipeline.processing.silver import run_silver

# Vehículos exclusivos de los tests. Comparten las letras a propósito: enmascarados se ven
# iguales (VHC-****-TST) y solo vehicle_key los distingue.
TEST_VEHICLES = ("VHC-9001-TST", "VHC-9002-TST")

TRIP_START = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
TRIP_STEP_DEG = 0.001  # ~111 m hacia el norte cada 10 s = 40 km/h


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session")
def conninfo(settings: Settings) -> str:
    try:
        with psycopg.connect(settings.conninfo, connect_timeout=3):
            pass
    except psycopg.OperationalError:
        # En CI (GitHub define CI=true) saltarse estos tests daría un verde falso: ahí fallan.
        if os.environ.get("CI"):
            raise
        pytest.skip("PostGIS no está disponible: ejecuta `docker compose up -d`")
    return settings.conninfo


def _delete_test_data(conn: psycopg.Connection) -> None:
    ids = list(TEST_VEHICLES)
    conn.execute(
        "DELETE FROM gold.fleet_events WHERE vehicle_key IN "
        "(SELECT vehicle_key FROM bronze.vehicles WHERE vehicle_id = ANY(%s))",
        (ids,),
    )
    conn.execute("DELETE FROM silver.alerts WHERE vehicle_id = ANY(%s)", (ids,))
    conn.execute("DELETE FROM silver.telemetry_enriched WHERE vehicle_id = ANY(%s)", (ids,))
    conn.execute("DELETE FROM silver.vehicle_routes WHERE vehicle_id = ANY(%s)", (ids,))
    conn.execute("DELETE FROM bronze.telemetry_raw WHERE vehicle_id = ANY(%s)", (ids,))
    conn.execute("DELETE FROM bronze.telemetry_rejected WHERE vehicle_id = ANY(%s)", (ids,))
    conn.execute("DELETE FROM bronze.vehicles WHERE vehicle_id = ANY(%s)", (ids,))
    # La vista materializada no se actualiza sola: sin esto quedan filas de los tests.
    conn.execute("REFRESH MATERIALIZED VIEW gold.driver_score")


@pytest.fixture
def test_vehicles(conninfo: str) -> Iterator[tuple[str, ...]]:
    """Registra los vehículos de prueba y borra todo lo que generen los tests al terminar."""
    with psycopg.connect(conninfo, autocommit=True) as conn:
        _delete_test_data(conn)
        conn.execute(
            """
            INSERT INTO bronze.vehicles
                (vehicle_id, vehicle_type, tank_capacity_l, ref_consumption_l_100km)
            VALUES (%s, 'truck', 400, 35), (%s, 'van', 80, 12)
            """,
            TEST_VEHICLES,
        )
    yield TEST_VEHICLES
    with psycopg.connect(conninfo, autocommit=True) as conn:
        _delete_test_data(conn)


@pytest.fixture
def conn(conninfo: str) -> Iterator[psycopg.Connection]:
    """Conexión cuya transacción se deshace al final: el test no deja rastro."""
    with psycopg.connect(conninfo) as connection:
        yield connection
        connection.rollback()


@pytest.fixture
def client(conninfo: str) -> Iterator[TestClient]:
    """Cliente HTTP contra la API real (arranca el pool y crea particiones)."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def headers(settings: Settings) -> dict[str, dict[str, str]]:
    return {
        role: {"X-API-Key": getattr(settings, f"api_key_{role}").get_secret_value()}
        for role in ("device", "admin", "analyst")
    }


@pytest.fixture
def conn_autocommit(conninfo: str) -> Iterator[psycopg.Connection]:
    """Conexión con autocommit y geocercas cargadas (los procesos hacen su propio commit)."""
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
            (vehicle_id, TRIP_START + timedelta(seconds=10 * i), 4.7000 + TRIP_STEP_DEG * i, speed),
        )


@pytest.fixture
def processed_trip(
    conn_autocommit: psycopg.Connection, test_vehicles: tuple[str, ...]
) -> tuple[str, int]:
    """~7,7 km a 40 km/h con un exceso sostenido de 3 lecturas a 70 km/h, procesado hasta Oro.

    Devuelve (ID real, vehicle_key).
    """
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


@pytest.fixture
def trip_day() -> date:
    """Día de negocio en Colombia del recorrido de prueba."""
    return TRIP_START.astimezone(ZoneInfo("America/Bogota")).date()
