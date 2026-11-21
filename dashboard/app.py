"""Dashboard "Centro de control de flota" (D-20, D-21, D-23).

Ejecutar: uv run python tasks.py dashboard   (o: uv run streamlit run dashboard/app.py)
Lee solo la capa Oro y las geocercas, asumiendo en PostgreSQL el rol elegido.
"""

from __future__ import annotations

from datetime import timedelta

import streamlit as st
import theme
import tour
import ui

from data import DataSource, PostgresSource
from fleet_pipeline.queries.dashboard import AlertFilter, QueryResult
from fleet_pipeline.security.auth import Role

st.set_page_config(
    page_title="Centro de control de flota",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)
ui.render(theme.CSS)
# Aviso "Cargando datos…": visible solo mientras Streamlit vuelve a ejecutar la página.
ui.render(ui.loading_overlay())

# Vistas a las que el mapa puede "volar" sin recargar la página.
VIEWS = {
    "Bogotá": {"longitude": -74.10, "latitude": 4.65, "zoom": 10.6},
    "Medellín": {"longitude": -75.575, "latitude": 6.25, "zoom": 11.6},
    "Corredor": {"longitude": -74.85, "latitude": 5.45, "zoom": 7.4},
}
# Tipos de infracción que el usuario puede combinar, agrupados: tipo de alerta + zona.
INFRACTION_GROUPS = {
    "Exceso de velocidad": {
        "🚗 En vía urbana": ["SPEEDING:urban", "SPEEDING:none"],
        "🛣️ En autopista": ["SPEEDING:highway"],
    },
    "Infracción de geocerca": {
        "🏫 Zona escolar": ["GEOFENCE_VIOLATION:school"],
        "⚠️ Alta siniestralidad": ["GEOFENCE_VIOLATION:high_risk"],
        "🚧 Vía deteriorada": ["GEOFENCE_VIOLATION:road_hazard"],
    },
}
SEVERITIES = {"Leve": "minor", "Moderada": "moderate", "Grave": "severe"}

source: DataSource = PostgresSource()


@st.cache_data(ttl=3600, show_spinner=False)
def cached_geofences() -> QueryResult:
    """Las geocercas casi nunca cambian: se consultan una vez por hora, no en cada clic."""
    return source.geofences(Role.ANALYST)


# --- Barra lateral -------------------------------------------------------------------
with st.sidebar:
    if st.button("🧭  Ver guía del tablero", use_container_width=True):
        st.session_state["tour_force"] = True
        st.session_state["tour_nonce"] = st.session_state.get("tour_nonce", 0) + 1

    with st.container(key="tour-role"):
        st.markdown("### Rol")
        role_label = (
            st.segmented_control(
                "Rol",
                ["Administrador", "Analista"],
                default="Analista",
                label_visibility="collapsed",
            )
            or "Analista"
        )
        st.caption(
            "El rol se aplica en PostgreSQL (`SET ROLE`): el analista solo puede leer datos "
            "enmascarados."
        )
    role = Role.ADMIN if role_label == "Administrador" else Role.ANALYST

    bounds = source.date_bounds(role).rows[0]
    if bounds["last"] is None:
        st.warning("La capa Oro está vacía. Corre `uv run python tasks.py seed`.")
        st.stop()

    with st.container(key="tour-period"):
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

    ranking = source.ranking(role, date_from, date_to)
    vehicles = ranking.rows
    with st.container(key="tour-vehicle"):
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

    with st.container(key="tour-filters"):
        st.markdown("### Infracciones")
        categories: list[str] = []
        for group, options in INFRACTION_GROUPS.items():
            st.markdown(f'<p class="group-title">{group}</p>', unsafe_allow_html=True)
            for label, group_categories in options.items():
                if st.checkbox(label, value=True, key=f"type-{label}"):
                    categories.extend(group_categories)
        chosen_severities = st.multiselect(
            "Severidad", list(SEVERITIES), default=list(SEVERITIES), placeholder="Elige severidad"
        )
        st.markdown('<p class="group-title">Otras alertas</p>', unsafe_allow_html=True)
        show_fuel = st.checkbox("⛽ Combustible insuficiente", value=True)
        st.markdown("### Capas del mapa")
        show = {
            "geofences": st.toggle("Geocercas", value=True),
            "route": st.toggle("Ruta del vehículo", value=True),
            "infractions": st.toggle("Infracciones", value=True),
            "alerts": st.toggle("Alertas que pulsan", value=True),
        }
        mode = (
            st.segmented_control(
                "Infracciones como", ["Heatmap", "Hexágonos 3D"], default="Heatmap"
            )
            or "Heatmap"
        )
    # Sin selección no se muestra nada (en vez de "todo"): así el filtro hace lo que dice.
    alert_filter = AlertFilter(
        categories=categories,
        severities=[SEVERITIES[s] for s in chosen_severities],
    )

# --- Consultas -------------------------------------------------------------------------
kpis = source.kpis(role, date_from, date_to)
route = source.route(role, vehicle["vehicle_key"], date_to) if vehicle else None
infractions = source.infractions(role, date_from, date_to, alert_filter)
alerts = source.recent_alerts(role, date_from, date_to, alert_filter)
fuel = source.fuel_alerts(role, date_from, date_to)
fuel_rows = [r for r in fuel.rows if r["severity"] in alert_filter.severities] if show_fuel else []
geofences = cached_geofences()

# --- Página ----------------------------------------------------------------------------
ui.render(ui.hero(role_label, role is Role.ANALYST, bounds["last"].strftime("%d/%m/%Y")))
with st.container(key="tour-kpis"):
    ui.render(ui.kpi_cards(kpis.rows[0]))

route_payload = (
    ui.route_payload(route.rows, f"{vehicle['label']} · {date_to:%d/%m}") if route else None
)
html = ui.map_html(
    views=VIEWS,
    mode="hexagon" if mode == "Hexágonos 3D" else "heatmap",
    show=show,
    geofences=geofences.rows,
    route=route_payload,
    infractions=infractions.rows,
    alerts=alerts.rows[:40],
    title=f"Periodo {date_from:%d/%m} – {date_to:%d/%m}",
)
with st.container(key="tour-map"):
    # HTML propio (plantilla + datos de nuestra base serializados como JSON), nunca de usuarios.
    st.iframe(html, height=640)

left, right = st.columns([5, 7], gap="medium")
with left, st.container(key="tour-ranking"):
    ui.render(ui.ranking_panel(ranking.rows))
with right, st.container(key="tour-alerts"):
    ui.render(ui.alerts_panel(alerts.rows, fuel_rows))

with st.container(key="tour-timings"):
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

# Recorrido guiado: la primera vez automáticamente, después solo con el botón.
# Con ?tour=0 en la dirección no se muestra (útil para grabar el video).
force = st.session_state.pop("tour_force", False)
if force or st.query_params.get("tour") != "0":
    with st.container(key="tour-runner"):
        st.iframe(tour.tour_html(force, st.session_state.get("tour_nonce", 0)), height=1)
