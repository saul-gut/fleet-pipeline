"""Acceso a la capa Bronce. Todas las consultas son parametrizadas (%s): nunca se arma SQL
concatenando valores, lo que elimina el riesgo de inyección SQL."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Protocol

import psycopg
from psycopg.types.json import Jsonb

from fleet_pipeline.ingestion.quality import PreviousReading
from fleet_pipeline.ingestion.schemas import TelemetryPacket


class IngestRepository(Protocol):
    """Operaciones que necesita el servicio de ingesta. Los tests usan una versión en memoria."""

    def registered_vehicles(self, vehicle_ids: Iterable[str]) -> set[str]: ...

    def previous_reading(
        self, vehicle_id: str, before: datetime, since: datetime
    ) -> PreviousReading | None: ...

    def insert_reading(self, packet: TelemetryPacket, flags: list[str]) -> bool: ...

    def insert_rejected(self, payload: Any, vehicle_id: str | None, reasons: list[str]) -> None: ...


class PostgresIngestRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def registered_vehicles(self, vehicle_ids: Iterable[str]) -> set[str]:
        rows = self._conn.execute(
            "SELECT vehicle_id FROM bronze.vehicles WHERE vehicle_id = ANY(%s)",
            (list(vehicle_ids),),
        ).fetchall()
        return {row[0] for row in rows}

    def previous_reading(
        self, vehicle_id: str, before: datetime, since: datetime
    ) -> PreviousReading | None:
        # El límite inferior (since) permite descartar particiones viejas (partition pruning);
        # el orden descendente usa la clave primaria (vehicle_id, ts) de cada partición.
        row = self._conn.execute(
            """
            SELECT ts, lat, lon, speed_kmh::float8, fuel_pct::float8
            FROM bronze.telemetry_raw
            WHERE vehicle_id = %s AND ts < %s AND ts >= %s
            ORDER BY ts DESC
            LIMIT 1
            """,
            (vehicle_id, before, since),
        ).fetchone()
        return PreviousReading(*row) if row else None

    def insert_reading(self, packet: TelemetryPacket, flags: list[str]) -> bool:
        """Inserta la lectura. Devuelve False si ya existía (duplicado idempotente)."""
        row = self._conn.execute(
            """
            INSERT INTO bronze.telemetry_raw
                (vehicle_id, ts, lat, lon, speed_kmh, fuel_pct, quality_flags)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (vehicle_id, ts) DO NOTHING
            RETURNING 1
            """,
            (
                packet.vehicle_id,
                packet.ts,
                packet.lat,
                packet.lon,
                packet.speed_kmh,
                packet.fuel_pct,
                flags,
            ),
        ).fetchone()
        return row is not None

    def insert_rejected(self, payload: Any, vehicle_id: str | None, reasons: list[str]) -> None:
        self._conn.execute(
            """
            INSERT INTO bronze.telemetry_rejected (vehicle_id, payload, reasons)
            VALUES (%s, %s, %s)
            """,
            (vehicle_id, Jsonb(payload), reasons),
        )
