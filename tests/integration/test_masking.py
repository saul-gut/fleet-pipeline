"""Enmascaramiento y mínimo privilegio (D-13): el analista nunca ve el ID real."""

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import errors

pytestmark = pytest.mark.integration


def insert_reading(conninfo: str, vehicle_id: str) -> None:
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO bronze.telemetry_raw (vehicle_id, ts, lat, lon, speed_kmh, fuel_pct)
            VALUES (%s, %s, 4.6, -74.07, 40, 50)
            """,
            (vehicle_id, datetime.now(UTC) - timedelta(minutes=1)),
        )


# --- Función de máscara -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("VHC-8472-ABC", "VHC-****-ABC"),
        ("VHC-12-X", "****"),  # formato inesperado: se oculta todo
        ("placa ABC123", "****"),
        ("", "****"),
        ("vhc-8472-abc", "****"),  # minúsculas no cumplen el formato
    ],
)
def test_mask_function(conn: psycopg.Connection, value: str, expected: str) -> None:
    row = conn.execute("SELECT analytics.mask_vehicle_id(%s)", (value,)).fetchone()
    assert row == (expected,)


# --- Permisos en la base de datos -------------------------------------------------------


@pytest.mark.parametrize(
    "relation",
    [
        "bronze.telemetry_raw",
        "bronze.vehicles",
        "bronze.telemetry_rejected",
        "bronze.v_latest_positions",
    ],
)
def test_analyst_cannot_read_bronze(conn: psycopg.Connection, relation: str) -> None:
    conn.execute("SET LOCAL ROLE bi_analyst")
    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute(f"SELECT 1 FROM {relation} LIMIT 1")  # nombre fijo del parametrize


def test_analyst_views_never_expose_real_ids(
    conn: psycopg.Connection, conninfo: str, test_vehicles: tuple[str, ...]
) -> None:
    for vehicle_id in test_vehicles:
        insert_reading(conninfo, vehicle_id)

    conn.execute("SET LOCAL ROLE bi_analyst")
    for view in ("analytics.latest_positions", "analytics.vehicles"):
        rows = conn.execute(f"SELECT * FROM {view}").fetchall()
        all_values = {str(value) for row in rows for value in row}
        assert not all_values & set(test_vehicles), f"{view} expone un ID real"
        assert "VHC-****-TST" in all_values


def test_masked_ids_collide_but_vehicle_key_distinguishes_them(
    conn: psycopg.Connection, test_vehicles: tuple[str, ...]
) -> None:
    conn.execute("SET LOCAL ROLE bi_analyst")
    rows = conn.execute(
        "SELECT vehicle_key, vehicle_id FROM analytics.vehicles WHERE vehicle_id = 'VHC-****-TST'"
    ).fetchall()
    assert len(rows) == 2
    assert len({key for key, _ in rows}) == 2


def test_ingest_role_is_append_only(conn: psycopg.Connection) -> None:
    conn.execute("SET LOCAL ROLE fleet_ingest")
    conn.execute("SAVEPOINT before_update")
    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute("UPDATE bronze.telemetry_raw SET speed_kmh = 0")
    conn.execute("ROLLBACK TO SAVEPOINT before_update")
    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute("DELETE FROM bronze.telemetry_raw")


# --- Misma regla a través de la API -----------------------------------------------------


def test_api_analyst_gets_masked_ids(
    client: TestClient,
    headers: dict[str, dict[str, str]],
    conninfo: str,
    test_vehicles: tuple[str, ...],
) -> None:
    for vehicle_id in test_vehicles:
        insert_reading(conninfo, vehicle_id)

    response = client.get("/vehicles/positions", headers=headers["analyst"])

    assert response.status_code == 200
    assert not set(test_vehicles) & set(response.text.split('"'))
    ids = [item["vehicle_id"] for item in response.json()]
    assert ids.count("VHC-****-TST") == 2


def test_api_admin_gets_real_ids(
    client: TestClient,
    headers: dict[str, dict[str, str]],
    conninfo: str,
    test_vehicles: tuple[str, ...],
) -> None:
    for vehicle_id in test_vehicles:
        insert_reading(conninfo, vehicle_id)

    response = client.get("/vehicles/positions", headers=headers["admin"])

    assert response.status_code == 200
    ids = {item["vehicle_id"] for item in response.json()}
    assert set(test_vehicles) <= ids


def test_api_device_cannot_read_positions(
    client: TestClient, headers: dict[str, dict[str, str]]
) -> None:
    response = client.get("/vehicles/positions", headers=headers["device"])
    assert response.status_code == 403


def test_api_positions_require_a_key(client: TestClient) -> None:
    assert client.get("/vehicles/positions").status_code == 401
