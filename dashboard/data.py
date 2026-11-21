"""Fuentes de datos del dashboard (D-21, D-26).

La interfaz solo conoce `DataSource`. Hay dos implementaciones:
- `PostgresSource`: la capa Oro en PostGIS, asumiendo el rol del usuario (uso local).
- `SnapshotSource`: una foto de Oro en Parquet consultada con DuckDB, sin servidor de base
  de datos (demo pública en Streamlit Community Cloud).

`select_source` elige sola: PostgreSQL si hay configuración de base (variables de entorno o
`.env`) y la foto si no. La variable FLEET_DATA_SOURCE=postgres|snapshot fuerza una de las dos.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Protocol

import streamlit as st
from psycopg_pool import ConnectionPool
from pydantic import ValidationError

from fleet_pipeline.config import get_settings
from fleet_pipeline.queries import dashboard as q
from fleet_pipeline.queries.dashboard import AlertFilter, QueryResult, Scope
from fleet_pipeline.queries.snapshot import SnapshotQueries
from fleet_pipeline.security.auth import Role


class DataSource(Protocol):
    engine: str  # nombre visible del motor, p. ej. "PostGIS"
    is_snapshot: bool

    def date_bounds(self, role: Role) -> QueryResult: ...
    def vehicles(self, role: Role) -> QueryResult: ...
    def kpis(self, role: Role, scope: Scope, alert_filter: AlertFilter) -> QueryResult: ...
    def ranking(self, role: Role, scope: Scope) -> QueryResult: ...
    def routes(self, role: Role, scope: Scope, sample_every: int) -> QueryResult: ...
    def infractions(self, role: Role, scope: Scope, alert_filter: AlertFilter) -> QueryResult: ...
    def recent_alerts(self, role: Role, scope: Scope, alert_filter: AlertFilter) -> QueryResult: ...
    def fuel_alerts(self, role: Role, scope: Scope, severities: list[str]) -> QueryResult: ...
    def geofences(self, role: Role) -> QueryResult: ...


@st.cache_resource(show_spinner=False)
def _pool() -> ConnectionPool:
    """Un pool por proceso de Streamlit, compartido entre sesiones."""
    return ConnectionPool(get_settings().conninfo, min_size=1, max_size=4, open=True)


class PostgresSource:
    """Lee la capa Oro asumiendo el rol del usuario en PostgreSQL."""

    engine = "PostGIS"
    is_snapshot = False

    def _run(self, fn: Callable[..., QueryResult], *args: Any) -> QueryResult:
        with _pool().connection() as conn:
            return fn(conn, *args)

    def date_bounds(self, role: Role) -> QueryResult:
        return self._run(q.date_bounds, role)

    def vehicles(self, role: Role) -> QueryResult:
        return self._run(q.vehicles, role)

    def kpis(self, role: Role, scope: Scope, alert_filter: AlertFilter) -> QueryResult:
        return self._run(q.kpis, role, scope, alert_filter)

    def ranking(self, role: Role, scope: Scope) -> QueryResult:
        return self._run(q.ranking, role, scope)

    def routes(self, role: Role, scope: Scope, sample_every: int) -> QueryResult:
        return self._run(q.routes, role, scope, sample_every)

    def infractions(self, role: Role, scope: Scope, alert_filter: AlertFilter) -> QueryResult:
        return self._run(q.infractions, role, scope, alert_filter)

    def recent_alerts(self, role: Role, scope: Scope, alert_filter: AlertFilter) -> QueryResult:
        return self._run(q.recent_alerts, role, scope, alert_filter)

    def fuel_alerts(self, role: Role, scope: Scope, severities: list[str]) -> QueryResult:
        return self._run(q.fuel_alerts, role, scope, severities)

    def geofences(self, role: Role) -> QueryResult:
        return self._run(q.geofences, role)


@st.cache_resource(show_spinner=False)
def _snapshot() -> SnapshotQueries:
    """La foto se carga en memoria una vez por proceso y la comparten todas las sesiones."""
    return SnapshotQueries()


class SnapshotSource:
    """Lee una foto de la capa Oro (Parquet) con DuckDB. Mismas consultas y resultados que
    PostgreSQL (lo verifica un test); la consulta del analista nunca toca los IDs."""

    engine = "foto en DuckDB"
    is_snapshot = True

    def __getattr__(self, name: str) -> Callable[..., QueryResult]:
        return getattr(_snapshot(), name)


def select_source() -> DataSource:
    choice = os.environ.get("FLEET_DATA_SOURCE", "").lower()
    if choice == "snapshot":
        return SnapshotSource()
    if choice == "postgres":
        return PostgresSource()
    try:
        get_settings()
    except ValidationError:  # sin configuración de base: demo pública con la foto
        return SnapshotSource()
    return PostgresSource()
