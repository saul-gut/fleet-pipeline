"""Fixtures para tests contra PostGIS real (docker compose up).

Si la base no responde, los tests de integración se omiten con un aviso en lugar de fallar,
para que los unitarios se puedan correr en cualquier máquina.
"""

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from fleet_pipeline.api.main import app
from fleet_pipeline.config import Settings, get_settings

# Vehículos exclusivos de los tests. Comparten las letras a propósito: enmascarados se ven
# iguales (VHC-****-TST) y solo vehicle_key los distingue.
TEST_VEHICLES = ("VHC-9001-TST", "VHC-9002-TST")


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session")
def conninfo(settings: Settings) -> str:
    try:
        with psycopg.connect(settings.conninfo, connect_timeout=3):
            pass
    except psycopg.OperationalError:
        pytest.skip("PostGIS no está disponible: ejecuta `docker compose up -d`")
    return settings.conninfo


def _delete_test_data(conn: psycopg.Connection) -> None:
    ids = list(TEST_VEHICLES)
    conn.execute("DELETE FROM silver.alerts WHERE vehicle_id = ANY(%s)", (ids,))
    conn.execute("DELETE FROM silver.telemetry_enriched WHERE vehicle_id = ANY(%s)", (ids,))
    conn.execute("DELETE FROM silver.vehicle_routes WHERE vehicle_id = ANY(%s)", (ids,))
    conn.execute("DELETE FROM bronze.telemetry_raw WHERE vehicle_id = ANY(%s)", (ids,))
    conn.execute("DELETE FROM bronze.telemetry_rejected WHERE vehicle_id = ANY(%s)", (ids,))
    conn.execute("DELETE FROM bronze.vehicles WHERE vehicle_id = ANY(%s)", (ids,))


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
