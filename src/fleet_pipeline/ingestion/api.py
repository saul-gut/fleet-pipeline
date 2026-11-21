"""API de ingesta (capa Bronce).

Ejecutar: uv run uvicorn fleet_pipeline.ingestion.api:app --reload
Documentación interactiva: http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

import psycopg
from fastapi import Body, Depends, FastAPI, HTTPException, Request, status
from psycopg_pool import ConnectionPool

from fleet_pipeline.config import Settings, get_settings
from fleet_pipeline.ingestion.repository import PostgresIngestRepository
from fleet_pipeline.ingestion.service import IngestSummary, ingest_batch
from fleet_pipeline.security.auth import Role, require_roles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)

# Ventana de particiones que la API garantiza al arrancar: la misma que acepta la validación.
PARTITION_DAYS_BACK = 7
PARTITION_DAYS_AHEAD = 2


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    with ConnectionPool(settings.conninfo, min_size=1, max_size=10, open=True) as pool:
        _ensure_partitions(pool)
        app.state.pool = pool
        yield


def _ensure_partitions(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        created = conn.execute(
            """
            SELECT bronze.ensure_partitions(
                (now() AT TIME ZONE 'America/Bogota')::date - %s,
                (now() AT TIME ZONE 'America/Bogota')::date + %s
            )
            """,
            (PARTITION_DAYS_BACK, PARTITION_DAYS_AHEAD),
        ).fetchone()
    log.info("Particiones nuevas creadas al iniciar: %s", created[0] if created else 0)


app = FastAPI(
    title="Fleet Pipeline · Ingesta",
    description="Recepción de telemetría IoT/AVL con validación de calidad (capa Bronce).",
    version="0.1.0",
    lifespan=lifespan,
)


def get_connection(request: Request) -> Iterator[psycopg.Connection]:
    """Una conexión del pool por petición. El bloque `with` hace commit al terminar sin
    errores y rollback si algo falla: el lote se guarda completo o no se guarda."""
    with request.app.state.pool.connection() as conn:
        yield conn


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


@app.post("/telemetry", response_model=IngestSummary, tags=["ingesta"])
def post_telemetry(
    payloads: Annotated[list[Any], Body(examples=[EXAMPLE_BATCH])],
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
    settings: Annotated[Settings, Depends(get_settings)],
    _role: Annotated[Role, Depends(require_roles(Role.DEVICE))],
) -> IngestSummary:
    """Recibe un lote de paquetes. Los inválidos no detienen el lote: quedan registrados en
    `bronze.telemetry_rejected` con su motivo y se cuentan en el resumen."""
    if len(payloads) > settings.max_batch_size:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"El lote supera el máximo de {settings.max_batch_size} paquetes",
        )
    return ingest_batch(PostgresIngestRepository(conn), payloads, now=datetime.now(UTC))


@app.get("/health", tags=["operación"])
def health(conn: Annotated[psycopg.Connection, Depends(get_connection)]) -> dict[str, str]:
    """Verifica que la API y la base de datos respondan."""
    conn.execute("SELECT 1")
    return {"status": "ok"}
