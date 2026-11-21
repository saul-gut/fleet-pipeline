"""Carga incremental Plata -> Oro y refresco del Driver Score."""

from __future__ import annotations

import logging
from datetime import datetime

import psycopg

log = logging.getLogger(__name__)


def run_gold(conn: psycopg.Connection) -> tuple[int, datetime]:
    """Actualiza gold.fleet_events y refresca gold.driver_score sin bloquear lecturas."""
    row = conn.execute("SELECT * FROM gold.process_new()").fetchone()
    if row is None:
        raise RuntimeError("gold.process_new no devolvió resultado")
    events, watermark = row
    # CONCURRENTLY: el tablero puede seguir leyendo el puntaje mientras se recalcula.
    conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY gold.driver_score")
    log.info("Oro: %d eventos actualizados, marca de agua %s", events, watermark)
    return events, watermark
