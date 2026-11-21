"""Dashboard "Centro de control de flota" (D-20, D-21).

Ejecutar: uv run streamlit run dashboard/app.py
Lee solo la capa Oro y las geocercas, asumiendo en PostgreSQL el rol elegido.
"""

from __future__ import annotations

from datetime import timedelta

import streamlit as st
import theme
import ui

from data import DataSource, PostgresSource
from fleet_pipeline.queries.dashboard import QueryResult
from fleet_pipeline.security.auth import Role

st.set_page_config(
    page_title="Centro de control de flota",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)
ui.render(theme.CSS)

VIEWS = {
    "Bogotá": {"longitude": -74.10, "latitude": 4.65, "zoom": 10.6, "pitch": 0, "bearing": 0},
    "Medellín": {"longitude": -75.575, "latitude": 6.25, "zoom": 11.6, "pitch": 0, "bearing": 0},
    "Corredor Bogotá–Medellín": {
        "longitude": -74.85,
        "latitude": 5.45,
        "zoom": 7.4,
        "pitch": 0,
        "bearing": 0,
    },
}
ALERT_TYPES = {"Exceso de velocidad": "SPEEDING", "Infracción de geocerca": "GEOFENCE_VIOLATION"}

source: DataSource = PostgresSource()


@st.cache_data(ttl=3600, show_spinner=False)
def cached_geofences() -> QueryResult:
    """Las geocercas casi nunca cambian: se consultan una vez por hora, no en cada clic."""
    return source.geofences(Role.ANALYST)


# --- Barra lateral -------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Rol")
    role_label = (
        st.segmented_control(
            "Rol", ["Administrador", "Analista"], default="Analista", label_visibility="collapsed"
        )
        or "Analista"
    )
    role = Role.ADMIN if role_label == "Administrador" else Role.ANALYST
    st.caption(
        "El rol se aplica en PostgreSQL (`SET ROLE`): el analista solo puede leer datos "
        "enmascarados."
    )

    bounds = source.date_bounds(role).rows[0]
    if bounds["last"] is None:
        st.warning("La capa Oro está vacía. Corre `uv run python tasks.py seed`.")
        st.stop()

    st.markdown("### Periodo")
    period = st.date_input(
        "Periodo",
        value=(max(bounds["first"], bounds["last"] - timedelta(days=1)), bounds["last"]),
        min_value=bounds["first"],
        max_value=bounds["last"],
        label_visibility="collapsed",
        format="DD/MM/YYYY",
    )
    date_from, date_to = period if len(period) == 2 else (period[0], period[0])

    st.markdown("### Vista del mapa")
    view_name = st.selectbox("Vista", list(VIEWS), label_visibility="collapsed")

    ranking = source.ranking(role, date_from, date_to)
    vehicles = ranking.rows
    st.markdown("### Vehículo (ruta)")
    # Por defecto, el de menor Driver Score: el que más historia tiene para mostrar.
    worst = min(
        range(len(vehicles)),
        key=lambda i: vehicles[i]["score"] if vehicles[i]["score"] is not None else 101,
        default=0,
    )
    vehicle = st.selectbox(
        "Vehículo",
        vehicles,
        index=worst,
        format_func=lambda v: f"{v['label']} · {v['vehicle_type']} · {v['score'] or '—'}",
        label_visibility="collapsed",
    )

    st.markdown("### Alertas")
    chosen = st.pills(
        "Tipos",
        list(ALERT_TYPES),
        default=list(ALERT_TYPES),
        selection_mode="multi",
        label_visibility="collapsed",
    )
    alert_types = [ALERT_TYPES[c] for c in chosen] or list(ALERT_TYPES.values())

    st.markdown("### Capas")
    show = {
        "geofences": st.toggle("Geocercas", value=True),
        "route": st.toggle("Ruta del vehículo", value=True),
        "infractions": st.toggle("Infracciones", value=True),
        "alerts": st.toggle("Alertas que pulsan", value=True),
    }
    mode = (
        st.segmented_control("Infracciones como", ["Heatmap", "Hexágonos 3D"], default="Heatmap")
        or "Heatmap"
    )

# --- Consultas -------------------------------------------------------------------------
kpis = source.kpis(role, date_from, date_to)
route = source.route(role, vehicle["vehicle_key"], date_to) if vehicle else None
infractions = source.infractions(role, date_from, date_to, alert_types)
alerts = source.recent_alerts(role, date_from, date_to, alert_types)
fuel = source.fuel_alerts(role, date_from, date_to)
geofences = cached_geofences()

# --- Página ----------------------------------------------------------------------------
last_ts = bounds["last"].strftime("%d/%m/%Y")
ui.render(ui.hero(role_label, role is Role.ANALYST, last_ts))
ui.render(ui.kpi_cards(kpis.rows[0]))

view = dict(VIEWS[view_name])
if mode == "Hexágonos 3D":
    view.update(pitch=50, bearing=-18)
route_payload = (
    ui.route_payload(route.rows, f"{vehicle['label']} · {date_to:%d/%m}") if route else None
)
html = ui.map_html(
    view=view,
    mode="hexagon" if mode == "Hexágonos 3D" else "heatmap",
    show=show,
    geofences=geofences.rows,
    route=route_payload,
    infractions=infractions.rows,
    alerts=alerts.rows[:40],
    title=f"{view_name} · {date_from:%d/%m} – {date_to:%d/%m}",
)
# HTML propio (plantilla + datos de nuestra base serializados como JSON), nunca de usuarios.
st.iframe(html, height=640)

left, right = st.columns([5, 7], gap="medium")
with left:
    ui.render(ui.ranking_panel(ranking.rows))
with right:
    ui.render(ui.alerts_panel(alerts.rows, fuel.rows))

ui.render(
    ui.timings(
        {
            "KPIs": kpis.ms,
            "ranking": ranking.ms,
            "ruta": route.ms if route else 0.0,
            "heatmap": infractions.ms,
            "alertas": alerts.ms + fuel.ms,
        }
    )
)
