"""Piezas visuales del dashboard construidas en HTML, con los estilos de theme.py.

Todo texto que viene de la base pasa por html.escape antes de insertarse en el HTML.
"""

from __future__ import annotations

import json
import random
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import streamlit as st
import theme

from fleet_pipeline.geo import geodesic_bearing_deg, geodesic_distance_m
from fleet_pipeline.processing.rules import ScoreParams
from fleet_pipeline.queries.dashboard import AlertFilter

SEVERITY_WEIGHT = dict(ScoreParams().severity_points)

BOGOTA = ZoneInfo("America/Bogota")
MAP_TEMPLATE = Path(__file__).with_name("deck_map.html")


def compact(html: str) -> str:
    """Quita sangrías y líneas en blanco. Streamlit procesa el texto como Markdown, donde una
    línea en blanco corta un bloque HTML y una sangría de 4 espacios lo vuelve código."""
    return " ".join(line.strip() for line in html.splitlines() if line.strip())


def render(html: str) -> None:
    st.markdown(compact(html), unsafe_allow_html=True)


def _fmt(value: float | int | None, decimals: int = 0) -> str:
    """Número con separador de miles con espacio y coma decimal (formato es-CO)."""
    if value is None:
        return "—"
    text = f"{float(value):,.{decimals}f}"
    return text.replace(",", " ").replace(".", ",")


def _local(ts: datetime) -> str:
    return ts.astimezone(BOGOTA).strftime("%d/%m %H:%M")


def loading_overlay(engine: str) -> str:
    """Ventana "Cargando datos…". El CSS la muestra solo mientras Streamlit ejecuta la página
    (atributo data-test-script-state="running") y con un retraso, para que no parpadee en las
    recargas rápidas. La barra es indeterminada: Streamlit no informa cuánto falta."""
    return f"""
    <div class="app-loading"><div class="card">
      <div class="spinner"></div>
      <div><b>Cargando datos…</b><span>Consultando la capa Oro ({escape(engine)})</span>
        <div class="track"><div class="fill"></div></div></div>
    </div></div>"""


def hero(role_label: str, masked: bool, last_ts: str, engine: str, snapshot: bool) -> str:
    role_class = "mask" if masked else "admin"
    role_text = f"🔒 {role_label} · IDs enmascarados" if masked else f"🛡️ {role_label} · IDs reales"
    demo = (
        '<span class="badge" title="Demo pública: una foto de la capa Oro consultada con DuckDB, '
        'sin servidor de base de datos. En local, el tablero consulta PostGIS.">'
        "📸 Demo · foto de los datos</span>"
        if snapshot
        else ""
    )
    return f"""
    <div class="hero">
      <div>
        <h1>Centro de control de flota</h1>
        <p>Telemetría IoT/AVL · Bogotá, Medellín y corredor Ruta 60 ·
           Capa Oro ({escape(engine)})</p>
      </div>
      <div class="badges">
        <span class="badge"><span class="dot"></span>Datos hasta {escape(last_ts)}</span>
        <span class="badge {role_class}">{escape(role_text)}</span>{demo}
      </div>
    </div>"""


def kpi_cards(k: dict[str, Any]) -> str:
    score = k.get("fleet_score")
    cards = [
        ("Vehículos activos", _fmt(k.get("active_vehicles")), "", "en el periodo", theme.ACCENT),
        ("Km recorridos", _fmt(k.get("km")), "km", "distancia sin ruido GPS", "#199e70"),
        (
            "Tiempo en exceso",
            _fmt(k.get("pct_over"), 1),
            "%",
            "de las lecturas en movimiento",
            theme.SEVERITY_COLORS["minor"],
        ),
        (
            "Excesos de velocidad",
            _fmt(k.get("speeding")),
            "",
            "episodios sostenidos",
            theme.SEVERITY_COLORS["moderate"],
        ),
        (
            "Infracciones de geocerca",
            _fmt(k.get("geofence")),
            "",
            f"{_fmt(k.get('low_fuel'))} alertas de combustible",
            theme.SEVERITY_COLORS["severe"],
        ),
        (
            "Driver Score flota",
            _fmt(score, 1),
            "/100",
            "ponderado por km",
            theme.score_color(float(score) if score is not None else None),
        ),
    ]
    html = "".join(
        f"""<div class="kpi" style="--kpi-color:{color}">
              <div class="label">{label}</div>
              <div class="value">{value}<small>{unit}</small></div>
              <div class="hint">{hint}</div>
            </div>"""
        for label, value, unit, hint, color in cards
    )
    return f'<div class="kpis">{html}</div>'


def ranking_panel(rows: list[dict[str, Any]]) -> str:
    """Todos los vehículos del alcance, de mayor a menor riesgo, con desplazamiento interno."""
    count = f"{len(rows)} vehículo" + ("" if len(rows) == 1 else "s")
    title = f"Driver Score · {count}, primero los de mayor riesgo"
    subtitle = (
        "0–100 · penalización por cada 100 km; las infracciones en zonas especiales pesan el doble"
    )
    if not rows:
        return _panel(title, subtitle, '<div class="empty">Sin vehículos en el filtro.</div>')
    # En un centro de control lo urgente va primero: de menor a mayor puntaje.
    riskiest = sorted(rows, key=lambda r: r["score"] if r["score"] is not None else 101)
    body = ""
    for pos, r in enumerate(riskiest, 1):
        score = float(r["score"]) if r["score"] is not None else None
        detail = f"{_fmt(r['km'])} km · {r['speeding']} excesos · {r['geofence']} en geocercas"
        name = f"{escape(str(r['label']))}<small>{escape(r['vehicle_type'])}</small>"
        body += f"""
        <div class="rank-row" title="{detail}">
          <span class="pos">{pos:02d}</span>
          <span class="name">{name}</span>
          <span class="bar"><span style="width:{score or 0}%;--c:{theme.score_color(score)}">
          </span></span>
          <span class="score">{_fmt(score, 1)}</span>
        </div>"""
    return _panel(title, subtitle, f'<div class="scroll">{body}</div>')


def _destination(name: str | None) -> str:
    """ "Medellín (Guayabal)" -> "Medellín": el barrio sobra en una tabla."""
    return (name or "destino").split(" (")[0]


def _panel(title: str, subtitle: str, body: str) -> str:
    return f'<div class="panel"><h4>{title}</h4><div class="sub">{subtitle}</div>{body}</div>'


def alerts_panel(
    rows: list[dict[str, Any]], fuel_rows: list[dict[str, Any]], limit: int = 50
) -> str:
    # Combustible primero: son pocas y críticas (un camión que no llega a destino); si se
    # mezclaran por fecha con cientos de excesos quedarían fuera de las más recientes.
    fuel_items = sorted(
        (
            (
                r["started_at"],
                r["label"],
                "⛽ " + theme.ALERT_LABELS["LOW_FUEL_FOR_ROUTE"],
                r["severity"],
                f"Tiene {_fmt(r['fuel_l'])} L; necesita {_fmt(r['fuel_needed_l'])} L",
                f"Rumbo a {_destination(r['destination'])} · {_fmt(r['remaining_km'])} km",
            )
            for r in fuel_rows
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    speed_items = [
        (
            r["started_at"],
            r["label"],
            theme.ALERT_LABELS[r["alert_type"]],
            r["severity"],
            f"{_fmt(r['max_speed_kmh'])} / {r['limit_kmh']} km/h",
            r["geofence_code"] or "—",
        )
        for r in rows[:limit]
    ]
    items = fuel_items + speed_items
    title = (
        f"Alertas · {len(fuel_items)} de combustible y {len(speed_items)} de velocidad "
        "más recientes"
    )
    subtitle = "Combustible insuficiente arriba; después excesos e infracciones de geocerca"
    if not items:
        return _panel(title, subtitle, '<div class="empty">Sin alertas.</div>')
    rows_html = ""
    for ts, label, kind, sev, detail, where in items:
        pill = f'<span class="pill" style="--c:{theme.SEVERITY_COLORS[sev]}">'
        pill += f"{theme.SEVERITY_LABELS[sev]}</span>"
        rows_html += (
            f'<tr><td>{_local(ts)}</td><td class="mono">{escape(str(label))}</td>'
            f"<td>{escape(kind)}</td><td>{pill}</td>"
            f"<td>{escape(detail)}</td><td>{escape(str(where))}</td></tr>"
        )
    header = "".join(
        f"<th>{h}</th>" for h in ("Inicio", "Vehículo", "Tipo", "Severidad", "Detalle", "Zona")
    )
    table = (
        f'<table class="alerts"><thead><tr>{header}</tr></thead><tbody>{rows_html}</tbody></table>'
    )
    return _panel(title, subtitle, f'<div class="scroll">{table}</div>')


def timings(items: dict[str, float], engine: str) -> str:
    chips = "".join(
        f'<span class="chip">{escape(k)} <b>{v:.1f} ms</b></span>' for k, v in items.items()
    )
    total = sum(items.values())
    return (
        f'<div class="timings">⏱ Consultas a la capa Oro ({escape(engine)}): {chips}'
        f'<span class="chip">total <b>{total:.1f} ms</b></span>'
        '<span class="chip">geocercas <b>en caché (1 h)</b></span></div>'
    )


# --- Mapa ------------------------------------------------------------------------------


def shown_severity(row: dict[str, Any], alert_filter: AlertFilter) -> str | None:
    """Severidad con la que se pinta una lectura: solo si su infracción pasa los filtros de
    tipo y severidad; si no, el tramo va en el color base ("dentro de lo filtrado")."""
    if row["alert_type"] is None or row["severity"] not in alert_filter.severities:
        return None
    category = f"{row['alert_type']}:{row['zone_type'] or 'none'}"
    return row["severity"] if category in alert_filter.categories else None


def route_payload(
    rows: list[dict[str, Any]], label: str, alert_filter: AlertFilter, with_arrows: bool
) -> dict[str, Any] | None:
    """Segmentos coloreados según los filtros + recorrido con tiempos relativos para animarlo.
    Las flechas de sentido solo se dibujan para un día: con varios, las idas y vueltas de
    distintos días se superponen y confunden."""
    if len(rows) < 2:
        return None
    severities = [shown_severity(r, alert_filter) for r in rows]
    segments = []
    for (a, b), severity in zip(zip(rows, rows[1:], strict=False), severities[1:], strict=True):
        segments.append(
            {
                "path": [[a["lon"], a["lat"]], [b["lon"], b["lat"]]],
                "color": theme.SEVERITY_COLORS.get(severity, theme.ROUTE_OK),
                "severity": severity,
                "speed": float(b["speed_kmh"]),
                "limit": b["speed_limit_kmh"],
                "zone": b["zone_type"],
                "ts": b["ts"].isoformat(),
            }
        )
    t0 = rows[0]["ts"]
    trip = {
        "path": [[r["lon"], r["lat"]] for r in rows],
        "timestamps": [(r["ts"] - t0).total_seconds() for r in rows],
    }
    arrows = _arrows(rows, severities) if with_arrows else []
    return {"segments": segments, "trip": trip, "arrows": arrows, "label": label}


ARROW_SPACING_M = 400.0
MIN_ARROW_STEP_M = 20.0  # bajo esta distancia el rumbo es ruido del GPS


ARROW_CELL_DEG = 0.003  # ~330 m: una flecha por celda y por sentido


def _arrows(rows: list[dict[str, Any]], severities: list[str | None]) -> list[dict[str, Any]]:
    """Flechas cada ~400 m de recorrido, orientadas con el rumbo del tramo.

    Un vehículo repite su ruta de ida y vuelta varias veces al día: para que las flechas no se
    amontonen se deja una por celda de ~330 m y por sentido (ida y vuelta se ven ambas).
    """
    arrows: dict[tuple[int, int, int], dict[str, Any]] = {}
    travelled = 0.0
    for (a, b), severity in zip(zip(rows, rows[1:], strict=False), severities[1:], strict=True):
        step = geodesic_distance_m(a["lat"], a["lon"], b["lat"], b["lon"])
        travelled += step
        if travelled < ARROW_SPACING_M or step < MIN_ARROW_STEP_M:
            continue
        travelled = 0.0
        bearing = geodesic_bearing_deg(a["lat"], a["lon"], b["lat"], b["lon"])
        cell = (
            round(b["lat"] / ARROW_CELL_DEG),
            round(b["lon"] / ARROW_CELL_DEG),
            round(bearing / 90) % 4,  # sentido aproximado: N, E, S u O
        )
        # La más reciente gana: muestra la severidad de la última pasada por ahí.
        arrows[cell] = {
            "p": [b["lon"], b["lat"]],
            "bearing": round(bearing),
            "color": theme.SEVERITY_COLORS.get(severity, theme.ROUTE_OK),
        }
    return list(arrows.values())


def fleet_payload(rows: list[dict[str, Any]], labels: dict[int, str]) -> dict[str, Any] | None:
    """Recorridos simplificados de toda la flota, con tiempos relativos al mismo inicio para
    animar a todos los vehículos a la vez."""
    if not rows:
        return None
    t0 = min(r["ts"] for r in rows)
    by_vehicle: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by_vehicle.setdefault(r["vehicle_key"], []).append(r)
    vehicles = [
        {
            "key": key,
            "label": labels.get(key, str(key)),
            "path": [[round(r["lon"], 5), round(r["lat"], 5)] for r in points],
            "timestamps": [(r["ts"] - t0).total_seconds() for r in points],
        }
        for key, points in by_vehicle.items()
        if len(points) >= 2
    ]
    end = max((v["timestamps"][-1] for v in vehicles), default=0)
    return {"vehicles": vehicles, "end": end}


def map_html(
    *,
    mode: str,
    geofences: list[dict[str, Any]],
    fleet: dict[str, Any] | None,
    route: dict[str, Any] | None,
    infractions: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    fuel: list[dict[str, Any]],
    title: str,
) -> str:
    """HTML del mapa. `fleet` = toda la flota (vista general); `route` = un vehículo en detalle.
    Las capas se encienden y apagan dentro del mapa, sin volver a ejecutar Streamlit."""
    rng = random.Random(7)  # desfase estable del pulso de cada alerta
    data = {
        "mode": mode,
        "title": title,
        "fleet": fleet,
        "zones": {
            "colors": theme.ZONE_COLORS,
            "labels": theme.ZONE_LABELS,
            "patterns": theme.ZONE_PATTERNS,
        },
        "severity": {"colors": theme.SEVERITY_COLORS, "labels": theme.SEVERITY_LABELS},
        "heatRamp": theme.HEAT_RAMP,
        "geofences": [
            {
                "type": "Feature",
                "geometry": g["geometry"],
                "properties": {
                    k: g[k]
                    for k in ("code", "name", "zone_type", "speed_limit_kmh", "limit_source")
                },
            }
            for g in geofences
        ],
        "route": route,
        # Un punto por episodio, pesado como en el Driver Score: leve 1, moderado 3, grave 6.
        "infractions": [
            {"p": [round(r["lon"], 5), round(r["lat"], 5)], "w": SEVERITY_WEIGHT[r["severity"]]}
            for r in infractions
        ],
        # Dónde empezó cada alerta de combustible insuficiente (camiones del corredor).
        "fuel": [
            {
                "p": [f["lon"], f["lat"]],
                "sev": f["severity"],
                "label": f["label"],
                "fuel": round(float(f["fuel_l"])),
                "needed": round(float(f["fuel_needed_l"])),
                "remaining": round(float(f["remaining_km"])),
                "destination": _destination(f["destination"]),
                "ts": f["started_at"].isoformat(),
            }
            for f in fuel
        ],
        "alerts": [
            {
                "p": [a["lon"], a["lat"]],
                "sev": a["severity"],
                "type": theme.ALERT_LABELS[a["alert_type"]],
                "label": a["label"],
                "speed": float(a["max_speed_kmh"]),
                "limit": a["limit_kmh"],
                "ts": a["started_at"].isoformat(),
                "duration": a["duration_s"],
                "km": round(float(a["distance_m"] or 0) / 1000, 2),
                "phase": rng.random(),
            }
            for a in alerts
        ],
    }
    # json.dumps escapa comillas; "</" se reemplaza para que ningún texto cierre el <script>.
    payload = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    return MAP_TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload)
