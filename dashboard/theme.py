"""Paleta y estilos del dashboard (D-20).

Colores tomados de la paleta validada de la guía de visualización (modo oscuro):
- Zonas especiales: 3 tonos categóricos fijos (máximo 3 en mapas, por legibilidad y
  daltonismo). Perímetros y autopista van en gris: son contexto.
- Severidad: colores de estado, siempre acompañados de su etiqueta (nunca solo color).
- Heatmap: un solo tono (naranja), de oscuro transparente a claro intenso.
"""

from __future__ import annotations

from pathlib import Path

# Superficies y tinta
PAGE = "#07090d"
SURFACE = "#11141b"
SURFACE_2 = "#171b24"
BORDER = "rgba(255,255,255,0.08)"
INK = "#f0efec"
INK_2 = "#c3c2b7"
MUTED = "#898781"
ACCENT = "#3987e5"  # azul de la serie 1: acento de la marca del tablero
ACCENT_GLOW = "rgba(57,135,229,0.45)"

# Zonas (categóricos, modo oscuro)
ZONE_COLORS: dict[str, str] = {
    "school": "#3987e5",  # azul
    "high_risk": "#d95926",  # naranja
    "road_hazard": "#199e70",  # aguamarina
    "urban": "#898781",  # contexto
    "highway": "#5c5b57",  # contexto
}
ZONE_LABELS = {
    "school": "Zona escolar",
    "high_risk": "Alta siniestralidad",
    "road_hazard": "Vía deteriorada",
    "urban": "Perímetro urbano",
    "highway": "Autopista",
}

# Severidad (paleta de estado)
SEVERITY_COLORS: dict[str, str] = {
    "minor": "#fab219",  # warning
    "moderate": "#ec835a",  # serious
    "severe": "#d03b3b",  # critical
}
SEVERITY_LABELS = {"minor": "Leve", "moderate": "Moderada", "severe": "Grave"}
GOOD = "#0ca30c"

ALERT_LABELS = {
    "SPEEDING": "Exceso de velocidad",
    "GEOFENCE_VIOLATION": "Infracción de geocerca",
    "LOW_FUEL_FOR_ROUTE": "Combustible insuficiente",
}

# Rampa secuencial de un solo tono para el heatmap (naranja, oscuro -> claro)
HEAT_RAMP = ["#3b1607", "#6b2a0e", "#a33f14", "#d95926", "#f0844f", "#ffc2a1"]


def hex_to_rgb(color: str, alpha: int = 255) -> list[int]:
    color = color.lstrip("#")
    return [int(color[i : i + 2], 16) for i in (0, 2, 4)] + [alpha]


def score_color(score: float | None) -> str:
    """Bandas del Driver Score con colores de estado (siempre con su número al lado)."""
    if score is None:
        return MUTED
    if score >= 85:
        return GOOD
    if score >= 60:
        return SEVERITY_COLORS["minor"]
    return SEVERITY_COLORS["severe"]


FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800'
    '&family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet">'
)
CSS = (
    FONTS
    + "<style>"
    + Path(__file__).with_name("style.css").read_text(encoding="utf-8")
    + "</style>"
)
