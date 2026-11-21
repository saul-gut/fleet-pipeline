"""Orquestación de un lote de ingesta, con un repositorio en memoria (sin base de datos)."""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from fleet_pipeline.ingestion.quality import PreviousReading
from fleet_pipeline.ingestion.schemas import TelemetryPacket
from fleet_pipeline.ingestion.service import ingest_batch

NOW = datetime(2026, 11, 20, 23, 0, tzinfo=UTC)
KNOWN_VEHICLE = "VHC-8472-ABC"


class InMemoryRepository:
    """Imita a PostgresIngestRepository guardando todo en listas."""

    def __init__(self, vehicles: set[str]) -> None:
        self.vehicles = vehicles
        self.readings: dict[tuple[str, datetime], tuple[TelemetryPacket, list[str]]] = {}
        self.rejected: list[tuple[Any, str | None, list[str]]] = []

    def registered_vehicles(self, vehicle_ids: Iterable[str]) -> set[str]:
        return self.vehicles & set(vehicle_ids)

    def previous_reading(
        self, vehicle_id: str, before: datetime, since: datetime
    ) -> PreviousReading | None:
        candidates = [
            packet
            for (vid, ts), (packet, _) in self.readings.items()
            if vid == vehicle_id and since <= ts < before
        ]
        if not candidates:
            return None
        last = max(candidates, key=lambda packet: packet.ts)
        return PreviousReading(last.ts, last.lat, last.lon, last.speed_kmh, last.fuel_pct)

    def insert_reading(self, packet: TelemetryPacket, flags: list[str]) -> bool:
        key = (packet.vehicle_id, packet.ts)
        if key in self.readings:
            return False
        self.readings[key] = (packet, flags)
        return True

    def insert_rejected(self, payload: Any, vehicle_id: str | None, reasons: list[str]) -> None:
        self.rejected.append((payload, vehicle_id, reasons))


def raw(seconds_ago: int = 0, **overrides: Any) -> dict[str, Any]:
    packet: dict[str, Any] = {
        "vehicle_id": KNOWN_VEHICLE,
        "ts": (NOW - timedelta(seconds=seconds_ago)).isoformat(),
        "lat": 4.59808,
        "lon": -74.07609,
        "speed_kmh": 40.0,
        "fuel_pct": 80.0,
    }
    packet.update(overrides)
    return packet


def test_valid_batch_is_fully_accepted() -> None:
    repo = InMemoryRepository({KNOWN_VEHICLE})
    summary = ingest_batch(repo, [raw(20), raw(10), raw(0)], NOW)
    assert summary.model_dump() == {
        "received": 3,
        "accepted": 3,
        "duplicates": 0,
        "rejected": 0,
        "flagged": 0,
    }


def test_resending_the_same_batch_creates_no_new_rows() -> None:
    repo = InMemoryRepository({KNOWN_VEHICLE})
    batch = [raw(10), raw(0)]
    ingest_batch(repo, batch, NOW)
    summary = ingest_batch(repo, batch, NOW)
    assert summary.duplicates == 2
    assert summary.accepted == 0
    assert len(repo.readings) == 2


def test_invalid_packets_do_not_stop_the_batch() -> None:
    repo = InMemoryRepository({KNOWN_VEHICLE})
    summary = ingest_batch(repo, [raw(10), raw(0, speed_kmh=900), "basura"], NOW)
    assert summary.accepted == 1
    assert summary.rejected == 2
    reasons = [reasons for _, _, reasons in repo.rejected]
    assert ["speed_kmh:less_than_equal"] in reasons
    assert ["payload:model_type"] in reasons


def test_rejected_packet_keeps_vehicle_id_for_traceability() -> None:
    repo = InMemoryRepository({KNOWN_VEHICLE})
    ingest_batch(repo, [raw(0, lat=0, lon=0)], NOW)
    payload, vehicle_id, reasons = repo.rejected[0]
    assert vehicle_id == KNOWN_VEHICLE
    assert payload["lat"] == 0
    assert "coords_zero" in reasons


def test_unknown_vehicle_is_rejected() -> None:
    repo = InMemoryRepository({KNOWN_VEHICLE})
    summary = ingest_batch(repo, [raw(0, vehicle_id="VHC-0000-ZZZ")], NOW)
    assert summary.rejected == 1
    assert repo.rejected[0][2] == ["unknown_vehicle"]


def test_out_of_order_batch_is_compared_in_time_order() -> None:
    """Llegan desordenados, pero cada paquete se compara con su anterior real en el tiempo."""
    repo = InMemoryRepository({KNOWN_VEHICLE})
    # Avanza ~111 m cada 10 s (40 km/h): normal si se ordena; un salto si no.
    batch = [raw(0, lat=4.60008), raw(20, lat=4.59808), raw(10, lat=4.59908)]
    summary = ingest_batch(repo, batch, NOW)
    assert summary.accepted == 3
    assert summary.rejected == 0


def test_jump_within_the_same_batch_is_detected() -> None:
    """Un salto a Tunja dentro del mismo lote se detecta contra la lectura recién insertada."""
    repo = InMemoryRepository({KNOWN_VEHICLE})
    summary = ingest_batch(repo, [raw(10), raw(0, lat=5.5353, lon=-73.3678)], NOW)
    assert summary.accepted == 1
    assert summary.rejected == 1
    assert repo.rejected[0][2] == ["impossible_jump"]


def test_flagged_reading_is_stored_with_its_flags() -> None:
    repo = InMemoryRepository({KNOWN_VEHICLE})
    summary = ingest_batch(repo, [raw(2, speed_kmh=90), raw(0, speed_kmh=0)], NOW)
    assert summary.accepted == 2
    assert summary.flagged == 1
    flags = [flags for _, flags in repo.readings.values()]
    assert ["abrupt_speed_change"] in flags


def test_empty_batch_returns_zero_counts() -> None:
    summary = ingest_batch(InMemoryRepository(set()), [], NOW)
    assert summary.received == 0
    assert summary.accepted == 0
