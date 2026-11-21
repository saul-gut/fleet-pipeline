"""Endpoint de ingesta de telemetría (capa Bronce)."""

from datetime import UTC, datetime
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Body, Depends, HTTPException, status

from fleet_pipeline.api.dependencies import get_connection
from fleet_pipeline.config import Settings, get_settings
from fleet_pipeline.ingestion.repository import PostgresIngestRepository
from fleet_pipeline.ingestion.service import IngestSummary, ingest_batch
from fleet_pipeline.security.auth import Role, require_roles
from fleet_pipeline.security.db_roles import assume_db_role

router = APIRouter(tags=["ingesta"])

EXAMPLE_BATCH = [
    {
        "vehicle_id": "VHC-8472-ABC",
        "ts": "2026-11-20T18:30:00-05:00",
        "lat": 4.59808,
        "lon": -74.07609,
        "speed_kmh": 42.5,
        "fuel_pct": 81.3,
    }
]


@router.post("/telemetry", response_model=IngestSummary)
def post_telemetry(
    payloads: Annotated[list[Any], Body(examples=[EXAMPLE_BATCH])],
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
    settings: Annotated[Settings, Depends(get_settings)],
    role: Annotated[Role, Depends(require_roles(Role.DEVICE))],
) -> IngestSummary:
    """Recibe un lote de paquetes. Los inválidos no detienen el lote: quedan registrados en
    `bronze.telemetry_rejected` con su motivo y se cuentan en el resumen."""
    if len(payloads) > settings.max_batch_size:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"El lote supera el máximo de {settings.max_batch_size} paquetes",
        )
    assume_db_role(conn, role)
    return ingest_batch(PostgresIngestRepository(conn), payloads, now=datetime.now(UTC))
