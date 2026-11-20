"""Reglas de calidad del dato en la ingesta (D-09).

Funciones puras: reciben todo lo que necesitan (incluida la hora actual) y no tocan la base
de datos ni la red. Por eso se prueban en milisegundos y con casos borde exactos.

Cada problema se reporta con un código estable (ej. "outside_colombia"), que permite agrupar
los rechazos por motivo en SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError

from fleet_pipeline.geo import geodesic_distance_m
from fleet_pipeline.ingestion.schemas import TelemetryPacket


@dataclass(frozen=True)
class QualityRules:
    """Umbrales de calidad. Los valores por defecto son los aprobados en la D-09."""

    max_clock_skew: timedelta = timedelta(minutes=5)
    max_age: timedelta = timedelta(days=7)
    # Rectángulo que contiene a Colombia, incluido San Andrés y Providencia. Es un primer
    # filtro barato; incluye franjas de países vecinos, lo que se acepta a propósito.
    min_lat: float = -4.3
    max_lat: float = 13.5
    min_lon: float = -82.0
    max_lon: float = -66.8
    max_implied_speed_kmh: float = 200.0
    # Bajo esta distancia un "salto" puede ser ruido del GPS con lecturas muy seguidas.
    min_jump_distance_m: float = 100.0
    max_speed_change_kmh_per_s: float = 30.0
    max_fuel_drop_pct: float = 5.0
    fuel_drop_window: timedelta = timedelta(minutes=10)


@dataclass(frozen=True)
class PreviousReading:
    """Última lectura aceptada del mismo vehículo anterior al paquete evaluado."""

    ts: datetime
    lat: float
    lon: float
    speed_kmh: float
    fuel_pct: float


@dataclass
class QualityResult:
    """Resultado de evaluar un paquete: motivos de rechazo y banderas de calidad."""

    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def is_rejected(self) -> bool:
        return bool(self.reasons)


def parse_packet(raw: Any) -> tuple[TelemetryPacket | None, list[str]]:
    """Convierte el JSON recibido en un paquete tipado, o devuelve los motivos de rechazo.

    Los motivos tienen la forma "campo:tipo_de_error" (ej. "speed_kmh:less_than_equal").
    """
    try:
        return TelemetryPacket.model_validate(raw), []
    except ValidationError as exc:
        reasons = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "payload"
            reasons.append(f"{location}:{error['type']}")
        return None, reasons


def check_context(
    packet: TelemetryPacket, now: datetime, rules: QualityRules = QualityRules()
) -> list[str]:
    """Reglas que dependen de la hora actual y del territorio."""
    reasons: list[str] = []

    if packet.lat == 0 or packet.lon == 0:
        reasons.append("coords_zero")
    if not (
        rules.min_lat <= packet.lat <= rules.max_lat
        and rules.min_lon <= packet.lon <= rules.max_lon
    ):
        reasons.append("outside_colombia")

    if packet.ts > now + rules.max_clock_skew:
        reasons.append("ts_in_future")
    elif packet.ts < now - rules.max_age:
        reasons.append("ts_too_old")

    return reasons


def check_against_previous(
    packet: TelemetryPacket,
    previous: PreviousReading | None,
    rules: QualityRules = QualityRules(),
) -> QualityResult:
    """Compara el paquete con la lectura anterior del vehículo: saltos y cambios bruscos."""
    result = QualityResult()
    if previous is None:
        return result

    elapsed_s = (packet.ts - previous.ts).total_seconds()
    if elapsed_s <= 0:
        # Sin tiempo transcurrido no hay velocidad que calcular; el duplicado exacto
        # (mismo vehicle_id y ts) lo resuelve la clave primaria de la base.
        return result

    distance_m = geodesic_distance_m(previous.lat, previous.lon, packet.lat, packet.lon)
    implied_speed_kmh = distance_m / elapsed_s * 3.6
    if distance_m >= rules.min_jump_distance_m and implied_speed_kmh > rules.max_implied_speed_kmh:
        result.reasons.append("impossible_jump")

    speed_change_per_s = abs(packet.speed_kmh - previous.speed_kmh) / elapsed_s
    if speed_change_per_s > rules.max_speed_change_kmh_per_s:
        result.flags.append("abrupt_speed_change")

    fuel_drop = previous.fuel_pct - packet.fuel_pct
    if fuel_drop >= rules.max_fuel_drop_pct and elapsed_s <= rules.fuel_drop_window.total_seconds():
        result.flags.append("abrupt_fuel_drop")

    return result


def evaluate(
    packet: TelemetryPacket,
    now: datetime,
    previous: PreviousReading | None,
    rules: QualityRules = QualityRules(),
) -> QualityResult:
    """Aplica todas las reglas de contexto e historia a un paquete ya parseado."""
    result = check_against_previous(packet, previous, rules)
    result.reasons = check_context(packet, now, rules) + result.reasons
    return result
