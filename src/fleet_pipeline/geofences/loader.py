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


# Piezas de máximo 256 vértices para el cruce espacial (D-16). ST_Dump separa los
# MultiPolygon resultantes en polígonos simples.
MAX_VERTICES_PER_PART = 256
INSERT_PARTS_SQL = """
INSERT INTO silver.geofence_parts (geofence_id, geom)
SELECT geofence_id, (ST_Dump(ST_Subdivide(geom, %s))).geom
FROM silver.geofences
"""


def read_features(path: Path = DEFAULT_GEOFENCES_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["features"]


def load_geofences(conn: psycopg.Connection, features: list[dict[str, Any]]) -> int:
    """Inserta o actualiza cada geocerca por su `code` y regenera sus piezas.

    Devuelve cuántas geocercas procesó.
    """
    with conn.cursor() as cur:
        cur.executemany(
            UPSERT_SQL,
            [{**f["properties"], "geometry": json.dumps(f["geometry"])} for f in features],
        )
        cur.execute("TRUNCATE silver.geofence_parts")
        cur.execute(INSERT_PARTS_SQL, (MAX_VERTICES_PER_PART,))
        cur.execute("ANALYZE silver.geofence_parts")
        parts = cur.execute("SELECT count(*) FROM silver.geofence_parts").fetchone()
    log.info(
        "Geocercas cargadas o actualizadas: %d (%d piezas)", len(features), parts[0] if parts else 0
    )
    return len(features)
