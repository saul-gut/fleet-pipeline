"""Carga data/geofences/geofences.geojson en silver.geofences de forma idempotente."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import psycopg

log = logging.getLogger(__name__)

DEFAULT_GEOFENCES_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "geofences" / "geofences.geojson"
)

UPSERT_SQL = """
INSERT INTO silver.geofences
    (code, name, city, zone_type, speed_limit_kmh, limit_source, source, geom)
VALUES (
    %(code)s, %(name)s, %(city)s, %(zone_type)s, %(speed_limit_kmh)s, %(limit_source)s,
    %(source)s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%(geometry)s), 4326))
)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    city = EXCLUDED.city,
    zone_type = EXCLUDED.zone_type,
    speed_limit_kmh = EXCLUDED.speed_limit_kmh,
    limit_source = EXCLUDED.limit_source,
    source = EXCLUDED.source,
    geom = EXCLUDED.geom,
    loaded_at = now()
"""


def read_features(path: Path = DEFAULT_GEOFENCES_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["features"]


def load_geofences(conn: psycopg.Connection, features: list[dict[str, Any]]) -> int:
    """Inserta o actualiza cada geocerca por su `code`. Devuelve cuántas procesó."""
    with conn.cursor() as cur:
        cur.executemany(
            UPSERT_SQL,
            [{**f["properties"], "geometry": json.dumps(f["geometry"])} for f in features],
        )
    log.info("Geocercas cargadas o actualizadas: %d", len(features))
    return len(features)
