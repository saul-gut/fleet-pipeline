"""Reglas de negocio de la capa Plata (D-17), como funciones puras.

Son la especificación ejecutable de las reglas: se prueban en milisegundos con casos borde,
y `RuleParams` entrega los mismos umbrales a la función SQL `silver.process_new`, que las
aplica a escala dentro de PostGIS. Un test de integración verifica que ambas coinciden.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

SPECIAL_ZONE_TYPES = frozenset({"school", "high_risk", "road_hazard"})
# Desempate entre geocercas con el mismo límite: gana la de mayor riesgo.
ZONE_PRIORITY = {"school": 0, "high_risk": 1, "road_hazard": 2, "urban": 3, "highway": 4}


@dataclass(frozen=True)
class RuleParams:
    default_limit_kmh: int = 90
    tolerance_kmh: float = 5.0
    min_consecutive_readings: int = 2
    max_gap: timedelta = timedelta(seconds=30)
    minor_max_excess: float = 0.20
    moderate_max_excess: float = 0.50
    fuel_window_m: float = 20_000.0
    min_fuel_window_m: float = 5_000.0
    min_step_m: float = 15.0
    refuel_jump_pct: float = 5.0
    fuel_safety_factor: float = 1.15


class Severity(StrEnum):
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"


class AlertType(StrEnum):
    SPEEDING = "SPEEDING"
    GEOFENCE_VIOLATION = "GEOFENCE_VIOLATION"
    LOW_FUEL_FOR_ROUTE = "LOW_FUEL_FOR_ROUTE"


@dataclass(frozen=True)
class ZoneLimit:
    code: str
    zone_type: str
    speed_limit_kmh: int


@dataclass(frozen=True)
class Reading:
    ts: datetime
    speed_kmh: float
    zone_code: str | None
    limit_kmh: int


@dataclass(frozen=True)
class Episode:
    start: int  # índice de la primera lectura del episodio
    end: int  # índice de la última lectura (incluida)


# --- Geocercas y velocidad -----------------------------------------------------------


def applied_zone(zones: Sequence[ZoneLimit]) -> ZoneLimit | None:
    """La geocerca más restrictiva entre las que contienen el punto (None si no hay)."""
    if not zones:
        return None
    return min(zones, key=lambda z: (z.speed_limit_kmh, ZONE_PRIORITY.get(z.zone_type, 99), z.code))


def is_over_threshold(
    speed_kmh: float, limit_kmh: float, params: RuleParams = RuleParams()
) -> bool:
    """Exceso solo si supera el límite más la tolerancia por ruido del GPS."""
    return speed_kmh > limit_kmh + params.tolerance_kmh


def severity(speed_kmh: float, limit_kmh: float, params: RuleParams = RuleParams()) -> Severity:
    """Severidad según el porcentaje de exceso sobre el límite (no sobre el umbral)."""
    excess = (speed_kmh - limit_kmh) / limit_kmh
    if excess <= params.minor_max_excess:
        return Severity.MINOR
    if excess <= params.moderate_max_excess:
        return Severity.MODERATE
    return Severity.SEVERE


def alert_type_for(zone_type: str | None) -> AlertType:
    """Exceso dentro de una zona especial = infracción de geocerca; si no, exceso general."""
    return AlertType.GEOFENCE_VIOLATION if zone_type in SPECIAL_ZONE_TYPES else AlertType.SPEEDING


def speeding_episodes(
    readings: Sequence[Reading], params: RuleParams = RuleParams()
) -> list[Episode]:
    """Agrupa lecturas consecutivas sobre el umbral en episodios.

    Un episodio se corta si cambia la geocerca aplicada o si pasan más de `max_gap` entre
    lecturas, y solo cuenta si tiene al menos `min_consecutive_readings` lecturas.
    """
    episodes: list[Episode] = []
    start: int | None = None
    for i, reading in enumerate(readings):
        over = is_over_threshold(reading.speed_kmh, reading.limit_kmh, params)
        continues = (
            start is not None
            and over
            and reading.zone_code == readings[i - 1].zone_code
            and reading.ts - readings[i - 1].ts <= params.max_gap
        )
        if start is not None and not continues:
            if i - start >= params.min_consecutive_readings:
                episodes.append(Episode(start, i - 1))
            start = None
        if over and start is None:
            start = i
    if start is not None and len(readings) - start >= params.min_consecutive_readings:
        episodes.append(Episode(start, len(readings) - 1))
    return episodes


# --- Combustible -----------------------------------------------------------------------


def effective_step_m(step_m: float, speed_kmh: float, params: RuleParams = RuleParams()) -> float:
    """Distancia que cuenta: cero si el vehículo está detenido o el salto es ruido del GPS."""
    return 0.0 if speed_kmh == 0 or step_m < params.min_step_m else step_m


def is_refuel(previous_pct: float, current_pct: float, params: RuleParams = RuleParams()) -> bool:
    return current_pct - previous_pct > params.refuel_jump_pct


def consumption_l_per_km(
    fuel_used_l: float,
    distance_m: float,
    reference_l_100km: float,
    params: RuleParams = RuleParams(),
) -> float:
    """Consumo real en la ventana; si hay pocos datos o el valor no tiene sentido,
    el consumo de referencia del catálogo."""
    if distance_m < params.min_fuel_window_m or fuel_used_l <= 0:
        return reference_l_100km / 100
    return fuel_used_l / (distance_m / 1000)


def remaining_route_km(route_fraction: float, direction: int, route_length_m: float) -> float:
    """Km hasta el extremo de la ruta hacia el que va el vehículo (1 = hacia el final)."""
    remaining = (1 - route_fraction) if direction == 1 else route_fraction
    return remaining * route_length_m / 1000


def fuel_needed_l(
    remaining_km: float, consumption_l_km: float, params: RuleParams = RuleParams()
) -> float:
    return remaining_km * consumption_l_km * params.fuel_safety_factor


def is_low_fuel(fuel_l: float, needed_l: float) -> bool:
    return fuel_l < needed_l
