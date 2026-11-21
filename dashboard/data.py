"""Fuente de datos del dashboard (D-21).

La interfaz solo conoce `DataSource`. Hoy la implementa PostgreSQL con roles; para publicar
en internet se puede agregar una fuente basada en una foto enmascarada de Oro sin tocar app.py.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import streamlit as st
from psycopg_pool import ConnectionPool

from fleet_pipeline.config import get_settings
from fleet_pipeline.queries import dashboard as q
from fleet_pipeline.queries.dashboard import AlertFilter, QueryResult, Scope
from fleet_pipeline.security.auth import Role


class DataSource(Protocol):
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
