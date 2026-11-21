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


def loading_overlay() -> str:
    """Ventana "Cargando datos…". El CSS la muestra solo mientras Streamlit ejecuta la página
    (atributo data-test-script-state="running") y con un retraso, para que no parpadee en las
    recargas rápidas. La barra es indeterminada: Streamlit no informa cuánto falta."""
    return """
    <div class="app-loading"><div class="card">
      <div class="spinner"></div>
      <div><b>Cargando datos…</b><span>Consultando la capa Oro en PostGIS</span>
        <div class="track"><div class="fill"></div></div></div>
    </div></div>"""


def hero(role_label: str, masked: bool, last_ts: str) -> str:
    role_class = "mask" if masked else "admin"
    role_text = f"🔒 {role_label} · IDs enmascarados" if masked else f"🛡️ {role_label} · IDs reales"
    return f"""
    <div class="hero">
      <div>
        <h1>Centro de control de flota</h1>
        <p>Telemetría IoT/AVL · Bogotá, Medellín y corredor Ruta 60 · Capa Oro (PostGIS)</p>
      </div>
      <div class="badges">
        <span class="badge"><span class="dot"></span>Datos hasta {escape(last_ts)}</span>
        <span class="badge {role_class}">{escape(role_text)}</span>
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


def _panel(title: str, subtitle: str, body: str) -> str:
    return f'<div class="panel"><h4>{title}</h4><div class="sub">{subtitle}</div>{body}</div>'


def alerts_panel(
    rows: list[dict[str, Any]], fuel_rows: list[dict[str, Any]], limit: int = 50
) -> str:
    items = [
        (
            r["started_at"],
            r["label"],
            theme.ALERT_LABELS[r["alert_type"]],
            r["severity"],
            f"{_fmt(r['max_speed_kmh'])} / {r['limit_kmh']} km/h",
            r["geofence_code"] or "—",
        )
        for r in rows
    ] + [
        (
            r["started_at"],
            r["label"],
            theme.ALERT_LABELS["LOW_FUEL_FOR_ROUTE"],
            r["severity"],
            f"{_fmt(r['fuel_l'])} de {_fmt(r['fuel_needed_l'])} L",
            f"{_fmt(r['remaining_km'])} km restantes",
        )
        for r in fuel_rows
    ]
    items.sort(key=lambda item: item[0], reverse=True)
    title = f"Alertas recientes · las {min(len(items), limit)} más recientes"
    subtitle = "Episodios de exceso, infracciones de geocerca y combustible insuficiente"
    if not items:
        return _panel(title, subtitle, '<div class="empty">Sin alertas.</div>')
    rows_html = ""
    for ts, label, kind, sev, detail, where in items[:limit]:
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


def timings(items: dict[str, float]) -> str:
    chips = "".join(
        f'<span class="chip">{escape(k)} <b>{v:.1f} ms</b></span>' for k, v in items.items()
    )
    total = sum(items.values())
    return (
        f'<div class="timings">⏱ Consultas a la capa Oro (PostGIS): {chips}'
        f'<span class="chip">total <b>{total:.1f} ms</b></span>'
        '<span class="chip">geocercas <b>en caché (1 h)</b></span></div>'
    )


# --- Mapa ------------------------------------------------------------------------------


def route_payload(rows: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    """Segmentos coloreados por severidad + recorrido con tiempos relativos para animarlo."""
    if len(rows) < 2:
        return None
    segments = []
    for a, b in zip(rows, rows[1:], strict=False):
        segments.append(
            {
                "path": [[a["lon"], a["lat"]], [b["lon"], b["lat"]]],
                "color": theme.SEVERITY_COLORS.get(b["severity"], theme.ACCENT),
                "severity": b["severity"],
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
    return {"segments": segments, "trip": trip, "arrows": _arrows(rows), "label": label}


ARROW_SPACING_M = 400.0
MIN_ARROW_STEP_M = 20.0  # bajo esta distancia el rumbo es ruido del GPS


ARROW_CELL_DEG = 0.003  # ~330 m: una flecha por celda y por sentido


def _arrows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flechas cada ~400 m de recorrido, orientadas con el rumbo del tramo.

    Un vehículo repite su ruta de ida y vuelta varias veces al día: para que las flechas no se
    amontonen se deja una por celda de ~330 m y por sentido (ida y vuelta se ven ambas).
    """
    arrows: dict[tuple[int, int, int], dict[str, Any]] = {}
    travelled = 0.0
    for a, b in zip(rows, rows[1:], strict=False):
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
            "color": theme.SEVERITY_COLORS.get(b["severity"], theme.ACCENT),
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
        "infractions": [
            {"p": [round(r["lon"], 5), round(r["lat"], 5)], "w": round(float(r["excess_kmh"]), 1)}
            for r in infractions
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
                "phase": rng.random(),
            }
            for a in alerts
        ],
    }
    # json.dumps escapa comillas; "</" se reemplaza para que ningún texto cierre el <script>.
    payload = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    return MAP_TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload)
