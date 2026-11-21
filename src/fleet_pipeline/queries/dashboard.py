"""Consultas del dashboard (D-20, D-23). Leen solo Oro y las geocercas, nunca Bronce.

Cada consulta corre dentro de una transacción que asume el rol del usuario en PostgreSQL
(`SET LOCAL ROLE`): el enmascaramiento lo aplica la base, no la interfaz. Para el admin, el
ID real sale de unir la clave sustituta con bronze.vehicles, algo que solo su rol puede hacer.

Todas las consultas reciben el mismo `Scope` (periodo + vehículos), así KPIs, ranking, mapa y
alertas responden juntos a los filtros. Cada resultado trae cuántos milisegundos tardó.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from fleet_pipeline.security.auth import Role
from fleet_pipeline.security.db_roles import assume_db_role

# Etiqueta del vehículo según el rol. Fragmentos fijos: nada viene del usuario.
_LABEL = {
    Role.ADMIN: sql.SQL("coalesce(v.vehicle_id, s.vehicle_id_masked)"),
    Role.ANALYST: sql.SQL("s.vehicle_id_masked"),
}
_JOIN_REAL_ID = {
    Role.ADMIN: sql.SQL("LEFT JOIN bronze.vehicles AS v ON v.vehicle_key = s.vehicle_key"),
    Role.ANALYST: sql.SQL(""),
}
_IN_SCOPE = sql.SQL("s.vehicle_key = ANY(%(vehicle_keys)s)")
# Categoría de cada lectura: "<tipo de alerta>:<zona aplicada>" ("none" = sin geocerca).
_ALERT_FILTER = sql.SQL(
    "(s.alert_type || ':' || coalesce(s.zone_type, 'none')) = ANY(%(categories)s) "
    "AND s.severity = ANY(%(severities)s)"
)


def _day_range(column: str) -> sql.Composed:
    """Días de negocio en Colombia -> rango de timestamps sobre `column` (nombre fijo del
    código, nunca del usuario). Así la partición diaria descarta los días fuera del rango."""
    return sql.SQL(
        "{col} >= (%(date_from)s::date)::timestamp AT TIME ZONE 'America/Bogota' "
        "AND {col} < (%(date_to)s::date + 1)::timestamp AT TIME ZONE 'America/Bogota'"
    ).format(col=sql.SQL(column))


_TS_RANGE = _day_range("s.ts")


@dataclass(frozen=True)
class Scope:
    """Periodo y vehículos que el usuario está mirando."""

    date_from: date
    date_to: date
    vehicle_keys: list[int]

    def params(self) -> dict[str, Any]:
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "vehicle_keys": self.vehicle_keys,
        }


@dataclass(frozen=True)
class AlertFilter:
    """Qué infracciones mostrar.

    `categories` combina tipo de alerta y zona aplicada, p. ej. "SPEEDING:urban" o
    "GEOFENCE_VIOLATION:school". `severities` usa minor, moderate y severe.
    """

    categories: list[str]
    severities: list[str]

    def params(self) -> dict[str, Any]:
        return {"categories": self.categories, "severities": self.severities}


@dataclass(frozen=True)
class QueryResult:
    rows: list[dict[str, Any]]
    ms: float


def run(
    conn: psycopg.Connection, role: Role, query: sql.Composable | str, params: dict | None = None
) -> QueryResult:
    """Ejecuta la consulta con el rol del usuario y mide su duración."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        assume_db_role(conn, role)
        started = time.perf_counter()
        cur.execute(query, params or {})
        rows = cur.fetchall()
        return QueryResult(rows, (time.perf_counter() - started) * 1000)


def date_bounds(conn: psycopg.Connection, role: Role) -> QueryResult:
    return run(
        conn,
        role,
        "SELECT min(event_date) AS first, max(event_date) AS last FROM gold.driver_score",
    )


def vehicles(conn: psycopg.Connection, role: Role) -> QueryResult:
    """Directorio de la flota: clave, etiqueta según el rol, tipo y región de operación."""
    query = sql.SQL(
        """
        SELECT s.vehicle_key, {label} AS label, s.vehicle_type, s.region
        FROM gold.vehicle_directory AS s {join}
        WHERE s.region IS NOT NULL
        ORDER BY s.vehicle_key
        """
    ).format(label=_LABEL[role], join=_JOIN_REAL_ID[role])
    return run(conn, role, query)


def kpis(
    conn: psycopg.Connection, role: Role, scope: Scope, alert_filter: AlertFilter
) -> QueryResult:
    query = sql.SQL(
        """
        WITH events AS (
            SELECT
                count(DISTINCT s.vehicle_key) AS active_vehicles,
                round((sum(s.step_m) / 1000)::numeric) AS km,
                round(100.0 * count(*) FILTER (WHERE s.is_over_threshold)
                      / nullif(count(*) FILTER (WHERE s.speed_kmh > 0), 0), 1) AS pct_over,
                count(DISTINCT s.alert_id)
                    FILTER (WHERE s.alert_type = 'SPEEDING' AND {alert_filter}) AS speeding,
                count(DISTINCT s.alert_id) FILTER (
                    WHERE s.alert_type = 'GEOFENCE_VIOLATION' AND {alert_filter}) AS geofence
            FROM gold.fleet_events AS s
            WHERE {in_scope} AND {ts_range}
        ), scores AS (
            SELECT gold.score_value(sum(s.penalty_points), sum(s.km)::numeric) AS fleet_score,
                   sum(s.low_fuel_alerts) AS low_fuel
            FROM gold.driver_score AS s
            WHERE {in_scope} AND s.event_date BETWEEN %(date_from)s AND %(date_to)s
        )
        SELECT * FROM events, scores
        """
    ).format(alert_filter=_ALERT_FILTER, in_scope=_IN_SCOPE, ts_range=_TS_RANGE)
    return run(conn, role, query, scope.params() | alert_filter.params())


def ranking(conn: psycopg.Connection, role: Role, scope: Scope) -> QueryResult:
    """Driver Score del periodo: puntos y km acumulados, misma fórmula que el diario."""
    query = sql.SQL(
        """
        SELECT s.vehicle_key, {label} AS label, s.vehicle_type,
               round(sum(s.km)::numeric) AS km,
               sum(s.speeding_minor + s.speeding_moderate + s.speeding_severe) AS speeding,
               sum(s.geofence_minor + s.geofence_moderate + s.geofence_severe) AS geofence,
               sum(s.speeding_severe + s.geofence_severe) AS severe,
               gold.score_value(sum(s.penalty_points), sum(s.km)::numeric) AS score
        FROM gold.driver_score AS s {join}
        WHERE {in_scope} AND s.event_date BETWEEN %(date_from)s AND %(date_to)s
        GROUP BY s.vehicle_key, label, s.vehicle_type
        ORDER BY score ASC NULLS LAST, s.vehicle_key
        """
    ).format(label=_LABEL[role], join=_JOIN_REAL_ID[role], in_scope=_IN_SCOPE)
    return run(conn, role, query, scope.params())


def routes(
    conn: psycopg.Connection, role: Role, scope: Scope, sample_every: int = 1
) -> QueryResult:
    """Lecturas en orden de los vehículos del alcance. Con `sample_every` > 1 se toma una de
    cada N (~N × 10 s): suficiente para dibujar la flota completa sin enviar miles de puntos."""
    query = sql.SQL(
        """
        SELECT s.vehicle_key, s.ts, ST_X(s.geom) AS lon, ST_Y(s.geom) AS lat, s.speed_kmh,
               s.speed_limit_kmh, s.zone_type, s.severity
        FROM gold.fleet_events AS s
        WHERE {in_scope} AND {ts_range}
          AND (%(every)s = 1 OR (extract(epoch FROM s.ts)::bigint / 10) %% %(every)s = 0)
        ORDER BY s.vehicle_key, s.ts
        """
    ).format(in_scope=_IN_SCOPE, ts_range=_TS_RANGE)
    return run(conn, role, query, scope.params() | {"every": sample_every})


def infractions(
    conn: psycopg.Connection, role: Role, scope: Scope, alert_filter: AlertFilter
) -> QueryResult:
    """Lecturas que forman parte de una alerta: la materia prima del heatmap."""
    query = sql.SQL(
        """
        SELECT ST_X(s.geom) AS lon, ST_Y(s.geom) AS lat, s.alert_type, s.severity,
               s.excess_kmh
        FROM gold.fleet_events AS s
        WHERE s.alert_type IS NOT NULL AND {alert_filter} AND {in_scope} AND {ts_range}
        """
    ).format(alert_filter=_ALERT_FILTER, in_scope=_IN_SCOPE, ts_range=_TS_RANGE)
    return run(conn, role, query, scope.params() | alert_filter.params())


def recent_alerts(
    conn: psycopg.Connection,
    role: Role,
    scope: Scope,
    alert_filter: AlertFilter,
    limit: int = 50,
) -> QueryResult:
    """Un registro por episodio de alerta (velocidad y geocerca), del más reciente al más viejo."""
    query = sql.SQL(
        """
        SELECT s.alert_id, {label} AS label, s.alert_type, s.severity,
               min(s.ts) AS started_at, max(s.ts) AS ended_at,
               max(s.speed_kmh) AS max_speed_kmh, min(s.speed_limit_kmh) AS limit_kmh,
               max(s.geofence_code) AS geofence_code,
               ST_X(ST_Centroid(ST_Collect(s.geom))) AS lon,
               ST_Y(ST_Centroid(ST_Collect(s.geom))) AS lat
        FROM gold.fleet_events AS s {join}
        WHERE s.alert_type IS NOT NULL AND {alert_filter} AND {in_scope} AND {ts_range}
        GROUP BY s.alert_id, label, s.alert_type, s.severity
        ORDER BY started_at DESC
        LIMIT %(limit)s
        """
    ).format(
        label=_LABEL[role],
        join=_JOIN_REAL_ID[role],
        alert_filter=_ALERT_FILTER,
        in_scope=_IN_SCOPE,
        ts_range=_TS_RANGE,
    )
    return run(conn, role, query, scope.params() | alert_filter.params() | {"limit": limit})


def fuel_alerts(
    conn: psycopg.Connection, role: Role, scope: Scope, severities: list[str]
) -> QueryResult:
    query = sql.SQL(
        """
        SELECT s.alert_id, {label} AS label, s.severity, s.started_at, s.ended_at,
               s.fuel_l, s.fuel_needed_l, s.remaining_km, ST_X(s.geom) AS lon, ST_Y(s.geom) AS lat
        FROM gold.fuel_alerts AS s {join}
        WHERE {in_scope} AND s.severity = ANY(%(severities)s) AND {ts_range}
        ORDER BY s.started_at DESC
        """
    ).format(
        label=_LABEL[role],
        join=_JOIN_REAL_ID[role],
        in_scope=_IN_SCOPE,
        ts_range=_day_range("s.started_at"),
    )
    return run(conn, role, query, scope.params() | {"severities": severities})


def geofences(conn: psycopg.Connection, role: Role) -> QueryResult:
    """Geocercas simplificadas a ~10 m para dibujar: menos vértices, mapa más fluido."""
    return run(
        conn,
        role,
        """
        SELECT code, name, city, zone_type, speed_limit_kmh, limit_source,
               ST_AsGeoJSON(ST_Multi(ST_SimplifyPreserveTopology(geom, 0.0001)), 5)::json
                   AS geometry
        FROM analytics.geofences
        ORDER BY speed_limit_kmh DESC
        """,
    )
