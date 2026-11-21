"""Dashboard "Centro de control de flota" (D-20, D-21, D-23, D-23).

Ejecutar: uv run python tasks.py dashboard   (o: uv run streamlit run dashboard/app.py)
Lee solo la capa Oro y las geocercas, asumiendo en PostgreSQL el rol elegido.

Todo el tablero (KPIs, mapa, ranking y alertas) responde al mismo alcance: periodo, ciudades,
vehículo, tipos de infracción y severidad. Los filtros se aplican juntos con un botón, para no
recargar la página en cada clic; las capas del mapa se encienden dentro del mapa, al instante.
"""

from __future__ import annotations

from datetime import timedelta

import streamlit as st
import theme
import tour
import ui

from data import DataSource, PostgresSource
from fleet_pipeline.queries.dashboard import AlertFilter, QueryResult, Scope
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

ALL_VEHICLES = "Todos los vehículos"
CITIES = {"Bogotá": "bogota", "Medellín": "medellin", "Corredor Bogotá–Medellín": "corridor"}
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
SEVERITIES = {"🟡 Leve": "minor", "🟠 Moderada": "moderate", "🔴 Grave": "severe"}
# Con la flota completa se dibuja una lectura de cada 6 (~1 minuto): suficiente para el mapa.
FLEET_SAMPLE_EVERY = 6

source: DataSource = PostgresSource()


@st.cache_data(ttl=3600, show_spinner=False)
def cached_geofences() -> QueryResult:
    """Las geocercas casi nunca cambian: se consultan una vez por hora, no en cada clic."""
    return source.geofences(Role.ANALYST)


def group_title(text: str) -> None:
    st.markdown(f'<p class="group-title">{text}</p>', unsafe_allow_html=True)


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
    directory = source.vehicles(role).rows

    # Formulario: los cambios se aplican juntos con un solo clic (una sola recarga).
    with st.form("filters", border=False):
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
            st.markdown("### Ciudad")
            regions = [region for label, region in CITIES.items() if st.checkbox(label, value=True)]

        with st.container(key="tour-vehicle"):
            st.markdown("### Vehículo")
            labels = {v["vehicle_key"]: v["label"] for v in directory}
            options = [None, *labels]
            # ?vehicle=<clave> en la dirección abre el tablero con ese vehículo (enlace directo).
            linked = st.query_params.get("vehicle", "")
            preselected = int(linked) if linked.isdigit() and int(linked) in labels else None
            vehicle_key = st.selectbox(
                "Vehículo",
                options,
                index=options.index(preselected),
                format_func=lambda k: ALL_VEHICLES if k is None else labels[k],
                label_visibility="collapsed",
            )
            st.caption("Elige uno para ver su ruta en detalle; tiene prioridad sobre la ciudad.")

        with st.container(key="tour-filters"):
            st.markdown("### Infracciones")
            categories: list[str] = []
            for group, options in INFRACTION_GROUPS.items():
                group_title(group)
                for label, group_categories in options.items():
                    if st.checkbox(label, value=True, key=f"type-{label}"):
                        categories.extend(group_categories)
            group_title("Severidad")
            severities = [
                value
                for label, value in SEVERITIES.items()
                if st.checkbox(label, value=True, key=f"sev-{label}")
            ]
            group_title("Otras alertas")
            show_fuel = st.checkbox("⛽ Combustible insuficiente", value=True)

        st.form_submit_button("Aplicar filtros", type="primary", use_container_width=True)

# --- Alcance común a todas las consultas ------------------------------------------------
date_from, date_to = period if len(period) == 2 else (period[0], period[0])
if vehicle_key is not None:
    vehicle_keys = [vehicle_key]
else:
    vehicle_keys = [v["vehicle_key"] for v in directory if v["region"] in regions]
scope = Scope(date_from, date_to, vehicle_keys)
# Sin selección no se muestra nada (en vez de "todo"): así el filtro hace lo que dice.
alert_filter = AlertFilter(categories=categories, severities=severities)

# --- Consultas -------------------------------------------------------------------------
kpis = source.kpis(role, scope, alert_filter)
ranking = source.ranking(role, scope)
routes = source.routes(role, scope, 1 if vehicle_key is not None else FLEET_SAMPLE_EVERY)
infractions = source.infractions(role, scope, alert_filter)
alerts = source.recent_alerts(role, scope, alert_filter)
fuel = source.fuel_alerts(role, scope, severities if show_fuel else [])
geofences = cached_geofences()

# --- Página ----------------------------------------------------------------------------
ui.render(ui.hero(role_label, role is Role.ANALYST, bounds["last"].strftime("%d/%m/%Y")))
with st.container(key="tour-kpis"):
    ui.render(ui.kpi_cards(kpis.rows[0]))

if vehicle_key is not None:
    detail = ui.route_payload(
        routes.rows, f"{labels[vehicle_key]} · {date_from:%d/%m}–{date_to:%d/%m}"
    )
    fleet, subject = None, labels[vehicle_key]
else:
    detail = None
    fleet = ui.fleet_payload(routes.rows, labels)
    subject = f"{len(vehicle_keys)} vehículos"
    # Sugerencia una sola vez por sesión.
    if not st.session_state.get("tip_shown"):
        st.toast("Consejo: elige un vehículo en el panel para ver su ruta en detalle.", icon="💡")
        st.session_state["tip_shown"] = True

# Geocercas de las ciudades elegidas (con un vehículo elegido, todas: su ruta puede cruzarlas).
shown_regions = set(regions) if vehicle_key is None else set(CITIES.values())
html = ui.map_html(
    mode="heatmap",
    geofences=[g for g in geofences.rows if g["city"] in shown_regions],
    fleet=fleet,
    route=detail,
    infractions=infractions.rows,
    alerts=alerts.rows[:40],
    title=f"{subject} · {date_from:%d/%m} – {date_to:%d/%m}",
)
with st.container(key="tour-map"):
    # HTML propio (plantilla + datos de nuestra base serializados como JSON), nunca de usuarios.
    st.iframe(html, height=660)

left, right = st.columns([5, 7], gap="medium")
with left, st.container(key="tour-ranking"):
    ui.render(ui.ranking_panel(ranking.rows))
with right, st.container(key="tour-alerts"):
    ui.render(ui.alerts_panel(alerts.rows, fuel.rows))

with st.container(key="tour-timings"):
    ui.render(
        ui.timings(
            {
                "KPIs": kpis.ms,
                "ranking": ranking.ms,
                "recorridos": routes.ms,
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
