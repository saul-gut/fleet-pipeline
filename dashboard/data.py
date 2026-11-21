"""Fuente de datos del dashboard (D-21).

La interfaz solo conoce `DataSource`. Hoy la implementa PostgreSQL con roles; para publicar
en internet se puede agregar una fuente basada en una foto enmascarada de Oro sin tocar app.py.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

import streamlit as st
from psycopg_pool import ConnectionPool

from fleet_pipeline.config import get_settings
from fleet_pipeline.queries import dashboard as q
from fleet_pipeline.queries.dashboard import QueryResult
from fleet_pipeline.security.auth import Role


class DataSource(Protocol):
    def date_bounds(self, role: Role) -> QueryResult: ...
    def vehicles(self, role: Role) -> QueryResult: ...
    def kpis(self, role: Role, date_from: date, date_to: date) -> QueryResult: ...
    def ranking(self, role: Role, date_from: date, date_to: date) -> QueryResult: ...
    def route(self, role: Role, vehicle_key: int, day: date) -> QueryResult: ...
    def infractions(
        self, role: Role, date_from: date, date_to: date, types: list[str]
    ) -> QueryResult: ...
    def recent_alerts(
        self, role: Role, date_from: date, date_to: date, types: list[str]
    ) -> QueryResult: ...
    def fuel_alerts(self, role: Role, date_from: date, date_to: date) -> QueryResult: ...
    def geofences(self, role: Role) -> QueryResult: ...


@st.cache_resource(show_spinner=False)
def _pool() -> ConnectionPool:
    """Un pool por proceso de Streamlit, compartido entre sesiones."""
    return ConnectionPool(get_settings().conninfo, min_size=1, max_size=4, open=True)


class PostgresSource:
    """Lee la capa Oro asumiendo el rol del usuario en PostgreSQL."""

    def _run(self, fn, *args):  # noqa: ANN001, ANN202 - delega en queries.dashboard
        with _pool().connection() as conn:
            return fn(conn, *args)

    def date_bounds(self, role: Role) -> QueryResult:
        return self._run(q.date_bounds, role)

    def vehicles(self, role: Role) -> QueryResult:
        return self._run(q.vehicles, role)

    def kpis(self, role: Role, date_from: date, date_to: date) -> QueryResult:
        return self._run(q.kpis, role, date_from, date_to)

    def ranking(self, role: Role, date_from: date, date_to: date) -> QueryResult:
        return self._run(q.ranking, role, date_from, date_to)

    def route(self, role: Role, vehicle_key: int, day: date) -> QueryResult:
        return self._run(q.route, role, vehicle_key, day)

    def infractions(
        self, role: Role, date_from: date, date_to: date, types: list[str]
    ) -> QueryResult:
        return self._run(q.infractions, role, date_from, date_to, types)

    def recent_alerts(
        self, role: Role, date_from: date, date_to: date, types: list[str]
    ) -> QueryResult:
        return self._run(q.recent_alerts, role, date_from, date_to, types)

    def fuel_alerts(self, role: Role, date_from: date, date_to: date) -> QueryResult:
        return self._run(q.fuel_alerts, role, date_from, date_to)

    def geofences(self, role: Role) -> QueryResult:
        return self._run(q.geofences, role)
