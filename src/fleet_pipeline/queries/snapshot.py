"""Foto de la capa Oro en Parquet y sus consultas con DuckDB, para publicar el tablero (D-26).

Un hosting gratuito de Streamlit no puede correr PostgreSQL. `export_snapshot` guarda en
Parquet lo que el tablero consulta de Oro (y las geocercas ya simplificadas), y
`SnapshotQueries` responde las mismas consultas que `queries.dashboard` con DuckDB, en el
mismo proceso y sin servidor. Un test de integración verifica que ambas fuentes devuelven lo
mismo.

Diferencias frente a PostgreSQL, a propósito:
- Las horas se guardan en UTC sin zona y se devuelven con zona UTC, como las entrega psycopg.
- El rango de días usa `event_date` (día de negocio en Colombia, calculado en PostGIS al
  exportar) en lugar de convertir zonas horarias, que en DuckDB requiere una extensión.
- No hay roles de base de datos: la consulta del analista nunca toca la tabla de IDs. Los IDs
  de la foto son los del simulador, ficticios.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from psycopg.rows import dict_row

from fleet_pipeline.queries.dashboard import AlertFilter, QueryResult, Scope
from fleet_pipeline.security.auth import Role

DEFAULT_SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "data" / "snapshot"

# Qué se exporta: nombre del archivo -> consulta en PostGIS. Solo Oro, geocercas y el
# directorio de vehículos; nunca Bronce ni Plata.
EXPORTS: dict[str, str] = {
    "fleet_events": """
        SELECT vehicle_key, vehicle_id_masked,
               ts AT TIME ZONE 'UTC' AS ts, event_date,
               -- 6 decimales = ~10 cm, más fino que el GPS; comprime mucho mejor.
               round(ST_X(geom)::numeric, 6)::float8 AS lon,
               round(ST_Y(geom)::numeric, 6)::float8 AS lat,
               speed_kmh, speed_limit_kmh, is_over_threshold, zone_type,
               geofence_code, alert_id, alert_type, severity, step_m
        FROM gold.fleet_events
        ORDER BY vehicle_key, ts
    """,
    "driver_score": "SELECT * FROM gold.driver_score ORDER BY vehicle_key, event_date",
    "vehicle_directory": """
        SELECT vehicle_key, vehicle_id_masked, vehicle_type, region
        FROM gold.vehicle_directory ORDER BY vehicle_key
    """,
    # Equivale a unir con bronze.vehicles, que en PostgreSQL solo puede hacer el admin.
    "vehicle_ids": "SELECT vehicle_key, vehicle_id FROM bronze.vehicles ORDER BY vehicle_key",
    "fuel_alerts": """
        SELECT alert_id, vehicle_key, vehicle_id_masked, severity,
               started_at AT TIME ZONE 'UTC' AS started_at,
               ended_at AT TIME ZONE 'UTC' AS ended_at,
               (started_at AT TIME ZONE 'America/Bogota')::date AS start_date,
               fuel_l, fuel_needed_l, remaining_km, destination,
               ST_X(geom) AS lon, ST_Y(geom) AS lat
        FROM gold.fuel_alerts ORDER BY alert_id
    """,
    "geofences": """
        SELECT code, name, city, zone_type, speed_limit_kmh, limit_source,
               ST_AsGeoJSON(ST_Multi(ST_SimplifyPreserveTopology(geom, 0.0001)), 5)
                   AS geometry
        FROM analytics.geofences
        ORDER BY speed_limit_kmh DESC, code
    """,
}

# Columnas de hora que se devuelven con zona UTC, como las entrega psycopg.
_TIMESTAMP_COLUMNS = ("ts", "started_at", "ended_at")


def export_snapshot(conn: psycopg.Connection, out_dir: Path = DEFAULT_SNAPSHOT_DIR) -> dict:
    """Escribe un Parquet por tabla en `out_dir` y devuelve cuántas filas tiene cada uno."""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with conn.cursor(row_factory=dict_row) as cur:
        for name, query in EXPORTS.items():
            rows = cur.execute(query).fetchall()
            table = pa.Table.from_pylist(rows)
            pq.write_table(
                table, out_dir / f"{name}.parquet", compression="zstd", compression_level=19
            )
            counts[name] = len(rows)
    return counts


# --- Consultas sobre la foto ---------------------------------------------------------------

_SCORE_MACRO = """
CREATE MACRO score_value(points, km) AS
    CASE WHEN km IS NULL OR km < 5 THEN NULL
         ELSE round(100 * exp(-0.005 * points / km * 100), 1) END
"""
_IN_SCOPE = "list_contains($vehicle_keys::INTEGER[], s.vehicle_key)"
_DAYS = "s.event_date BETWEEN $date_from AND $date_to"
_ALERT_FILTER = (
    "list_contains($categories::VARCHAR[], s.alert_type || ':' || coalesce(s.zone_type, 'none')) "
    "AND list_contains($severities::VARCHAR[], s.severity)"
)
_LABEL = {
    Role.ADMIN: "coalesce(v.vehicle_id, s.vehicle_id_masked)",
    Role.ANALYST: "s.vehicle_id_masked",
}
_JOIN_REAL_ID = {
    Role.ADMIN: "LEFT JOIN vehicle_ids AS v ON v.vehicle_key = s.vehicle_key",
    Role.ANALYST: "",
}


class SnapshotQueries:
    """Las consultas del tablero sobre la foto en Parquet, cargada en memoria con DuckDB."""

    def __init__(self, snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR) -> None:
        self._con = duckdb.connect()
        for name in EXPORTS:
            path = (snapshot_dir / f"{name}.parquet").as_posix().replace("'", "''")
            self._con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet('{path}')")
        self._con.execute(_SCORE_MACRO)

    def _run(self, query: str, params: dict[str, Any] | None = None) -> QueryResult:
        # Un cursor por consulta: DuckDB permite usarlos desde varios hilos (sesiones).
        cur = self._con.cursor()
        started = time.perf_counter()
        cur.execute(query, params or {})
        columns = [d[0] for d in cur.description]
        rows = [dict(zip(columns, values, strict=True)) for values in cur.fetchall()]
        ms = (time.perf_counter() - started) * 1000
        return QueryResult([_as_psycopg(r) for r in rows], ms)

    def date_bounds(self, role: Role) -> QueryResult:
        return self._run(
            'SELECT min(event_date) AS "first", max(event_date) AS "last" FROM driver_score'
        )

    def vehicles(self, role: Role) -> QueryResult:
        return self._run(
            f"""
            SELECT s.vehicle_key, {_LABEL[role]} AS label, s.vehicle_type, s.region
            FROM vehicle_directory AS s {_JOIN_REAL_ID[role]}
            WHERE s.region IS NOT NULL
            ORDER BY s.vehicle_key
            """
        )

    def kpis(self, role: Role, scope: Scope, alert_filter: AlertFilter) -> QueryResult:
        return self._run(
            f"""
            WITH events AS (
                SELECT
                    count(DISTINCT s.vehicle_key) AS active_vehicles,
                    round(sum(s.step_m) / 1000) AS km,
                    round(100.0 * count(*) FILTER (WHERE s.is_over_threshold)
                          / nullif(count(*) FILTER (WHERE s.speed_kmh > 0), 0), 1) AS pct_over,
                    count(DISTINCT s.alert_id)
                        FILTER (WHERE s.alert_type = 'SPEEDING' AND {_ALERT_FILTER}) AS speeding,
                    count(DISTINCT s.alert_id) FILTER (
                        WHERE s.alert_type = 'GEOFENCE_VIOLATION' AND {_ALERT_FILTER}) AS geofence
                FROM fleet_events AS s
                WHERE {_IN_SCOPE} AND {_DAYS}
            ), scores AS (
                SELECT score_value(sum(s.penalty_points), sum(s.km)) AS fleet_score,
                       sum(s.low_fuel_alerts) AS low_fuel
                FROM driver_score AS s
                WHERE {_IN_SCOPE} AND {_DAYS}
            )
            SELECT * FROM events, scores
            """,
            scope.params() | alert_filter.params(),
        )

    def ranking(self, role: Role, scope: Scope) -> QueryResult:
        return self._run(
            f"""
            SELECT s.vehicle_key, {_LABEL[role]} AS label, s.vehicle_type,
                   round(sum(s.km)) AS km,
                   sum(s.speeding_minor + s.speeding_moderate + s.speeding_severe) AS speeding,
                   sum(s.geofence_minor + s.geofence_moderate + s.geofence_severe) AS geofence,
                   sum(s.speeding_severe + s.geofence_severe) AS severe,
                   score_value(sum(s.penalty_points), sum(s.km)) AS score
            FROM driver_score AS s {_JOIN_REAL_ID[role]}
            WHERE {_IN_SCOPE} AND {_DAYS}
            GROUP BY s.vehicle_key, label, s.vehicle_type
            ORDER BY score ASC NULLS LAST, s.vehicle_key
            """,
            scope.params(),
        )

    def routes(self, role: Role, scope: Scope, sample_every: int = 1) -> QueryResult:
        return self._run(
            f"""
            SELECT s.vehicle_key, s.ts, s.lon, s.lat, s.speed_kmh, s.speed_limit_kmh,
                   s.zone_type, s.alert_type, s.severity
            FROM fleet_events AS s
            WHERE {_IN_SCOPE} AND {_DAYS}
              AND ($every = 1 OR (epoch(s.ts)::BIGINT // 10) % $every = 0)
            ORDER BY s.vehicle_key, s.ts
            """,
            scope.params() | {"every": sample_every},
        )

    def infractions(self, role: Role, scope: Scope, alert_filter: AlertFilter) -> QueryResult:
        return self._run(
            f"""
            SELECT DISTINCT ON (s.alert_id)
                   s.alert_id, s.lon, s.lat, s.alert_type, s.severity,
                   s.speed_kmh - s.speed_limit_kmh AS excess_kmh
            FROM fleet_events AS s
            WHERE s.alert_type IS NOT NULL AND {_ALERT_FILTER} AND {_IN_SCOPE} AND {_DAYS}
            ORDER BY s.alert_id, s.speed_kmh DESC, s.ts
            """,
            scope.params() | alert_filter.params(),
        )

    def recent_alerts(
        self, role: Role, scope: Scope, alert_filter: AlertFilter, limit: int = 50
    ) -> QueryResult:
        return self._run(
            f"""
            SELECT alert_id, label, alert_type, severity, started_at, ended_at, max_speed_kmh,
                   limit_kmh, geofence_code, distance_m, duration_s,
                   peak[1] AS lon, peak[2] AS lat
            FROM (
                SELECT s.alert_id, {_LABEL[role]} AS label, s.alert_type, s.severity,
                       min(s.ts) AS started_at, max(s.ts) AS ended_at,
                       max(s.speed_kmh) AS max_speed_kmh, min(s.speed_limit_kmh) AS limit_kmh,
                       max(s.geofence_code) AS geofence_code,
                       round(sum(s.step_m)) AS distance_m,
                       date_diff('second', min(s.ts), max(s.ts))::INTEGER AS duration_s,
                       -- El punto del mapa es el pico de velocidad (empate: el primero).
                       first([s.lon, s.lat] ORDER BY s.speed_kmh DESC, s.ts) AS peak
                FROM fleet_events AS s {_JOIN_REAL_ID[role]}
                WHERE s.alert_type IS NOT NULL AND {_ALERT_FILTER} AND {_IN_SCOPE} AND {_DAYS}
                GROUP BY s.alert_id, label, s.alert_type, s.severity
            )
            ORDER BY started_at DESC, alert_id DESC
            LIMIT $limit
            """,
            scope.params() | alert_filter.params() | {"limit": limit},
        )

    def fuel_alerts(self, role: Role, scope: Scope, severities: list[str]) -> QueryResult:
        return self._run(
            f"""
            SELECT s.alert_id, {_LABEL[role]} AS label, s.severity, s.started_at, s.ended_at,
                   s.fuel_l, s.fuel_needed_l, s.remaining_km, s.destination, s.lon, s.lat
            FROM fuel_alerts AS s {_JOIN_REAL_ID[role]}
            WHERE {_IN_SCOPE} AND list_contains($severities::VARCHAR[], s.severity)
              AND s.start_date BETWEEN $date_from AND $date_to
            ORDER BY s.started_at DESC, s.alert_id DESC
            """,
            scope.params() | {"severities": severities},
        )

    def geofences(self, role: Role) -> QueryResult:
        return self._run(
            """
            SELECT code, name, city, zone_type, speed_limit_kmh, limit_source, geometry
            FROM geofences ORDER BY speed_limit_kmh DESC, code
            """
        )


def _as_psycopg(row: dict[str, Any]) -> dict[str, Any]:
    """Mismos tipos que entrega psycopg: horas con zona UTC y GeoJSON ya decodificado."""
    for column in _TIMESTAMP_COLUMNS:
        value = row.get(column)
        if isinstance(value, datetime) and value.tzinfo is None:
            row[column] = value.replace(tzinfo=UTC)
    if isinstance(row.get("geometry"), str):
        row["geometry"] = json.loads(row["geometry"])
    return row


def main() -> None:
    """Uso: uv run python -m fleet_pipeline.queries.snapshot  (exporta a data/snapshot/)."""
    from fleet_pipeline.config import get_settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with psycopg.connect(get_settings().conninfo) as conn:
        counts = export_snapshot(conn)
    size_mb = sum(f.stat().st_size for f in DEFAULT_SNAPSHOT_DIR.glob("*.parquet")) / 1e6
    logging.getLogger(__name__).info("Foto exportada (%.1f MB): %s", size_mb, counts)


if __name__ == "__main__":
    main()
