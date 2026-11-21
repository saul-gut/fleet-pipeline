"""Ingesta de punta a punta: HTTP -> validación -> PostGIS."""

from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def packet(vehicle_id: str, seconds_ago: int, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "vehicle_id": vehicle_id,
        "ts": (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat(),
        "lat": 4.59808,
        "lon": -74.07609,
        "speed_kmh": 40.0,
        "fuel_pct": 80.0,
    }
    values.update(overrides)
    return values


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_batch_is_stored_and_resend_is_idempotent(
    client: TestClient,
    headers: dict[str, dict[str, str]],
    conninfo: str,
    test_vehicles: tuple[str, ...],
) -> None:
    vehicle = test_vehicles[0]
    batch = [packet(vehicle, 20), packet(vehicle, 10), packet(vehicle, 0)]

    first = client.post("/telemetry", json=batch, headers=headers["device"])
    second = client.post("/telemetry", json=batch, headers=headers["device"])

    assert first.json()["accepted"] == 3
    assert second.json()["duplicates"] == 3
    with psycopg.connect(conninfo) as conn:
        row = conn.execute(
            "SELECT count(*), bool_and(geom IS NOT NULL) FROM bronze.telemetry_raw "
            "WHERE vehicle_id = %s",
            (vehicle,),
        ).fetchone()
    assert row == (3, True)


def test_rejected_packet_is_stored_with_reasons(
    client: TestClient,
    headers: dict[str, dict[str, str]],
    conninfo: str,
    test_vehicles: tuple[str, ...],
) -> None:
    vehicle = test_vehicles[0]
    response = client.post(
        "/telemetry", json=[packet(vehicle, 0, lat=0, lon=0)], headers=headers["device"]
    )

    assert response.json()["rejected"] == 1
    with psycopg.connect(conninfo) as conn:
        row = conn.execute(
            "SELECT reasons, payload->>'lat' FROM bronze.telemetry_rejected WHERE vehicle_id = %s",
            (vehicle,),
        ).fetchone()
    assert row is not None
    assert "coords_zero" in row[0]
    assert row[1] == "0"


def test_flagged_reading_keeps_its_flags(
    client: TestClient,
    headers: dict[str, dict[str, str]],
    conninfo: str,
    test_vehicles: tuple[str, ...],
) -> None:
    vehicle = test_vehicles[0]
    batch = [packet(vehicle, 2, speed_kmh=90), packet(vehicle, 0, speed_kmh=0)]

    response = client.post("/telemetry", json=batch, headers=headers["device"])

    assert response.json()["flagged"] == 1
    with psycopg.connect(conninfo) as conn:
        flags = conn.execute(
            "SELECT quality_flags FROM bronze.telemetry_raw "
            "WHERE vehicle_id = %s AND speed_kmh = 0",
            (vehicle,),
        ).fetchone()
    assert flags == (["abrupt_speed_change"],)


def test_oversized_batch_is_refused(client: TestClient, headers: dict[str, dict[str, str]]) -> None:
    response = client.post("/telemetry", json=[{}] * 1001, headers=headers["device"])
    assert response.status_code == 413


def test_analyst_cannot_send_telemetry(
    client: TestClient, headers: dict[str, dict[str, str]]
) -> None:
    response = client.post("/telemetry", json=[], headers=headers["analyst"])
    assert response.status_code == 403
