"""Aplicación FastAPI.

Ejecutar: uv run uvicorn fleet_pipeline.api.main:app --reload
Documentación interactiva: http://localhost:8000/docs
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI
from psycopg_pool import ConnectionPool

from fleet_pipeline.api import ingest, vehicles
from fleet_pipeline.api.dependencies import get_connection
from fleet_pipeline.config import get_settings

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
        row = conn.execute(
            """
            SELECT bronze.ensure_partitions(
                (now() AT TIME ZONE 'America/Bogota')::date - %s,
                (now() AT TIME ZONE 'America/Bogota')::date + %s
            )
            """,
            (PARTITION_DAYS_BACK, PARTITION_DAYS_AHEAD),
        ).fetchone()
    log.info("Particiones nuevas creadas al iniciar: %s", row[0] if row else 0)


app = FastAPI(
    title="Fleet Pipeline API",
    description="Ingesta de telemetría IoT/AVL y consultas con enmascaramiento por rol.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(ingest.router)
app.include_router(vehicles.router)


@app.get("/health", tags=["operación"])
def health(conn: Annotated[psycopg.Connection, Depends(get_connection)]) -> dict[str, str]:
    """Verifica que la API y la base de datos respondan."""
    conn.execute("SELECT 1")
    return {"status": "ok"}
