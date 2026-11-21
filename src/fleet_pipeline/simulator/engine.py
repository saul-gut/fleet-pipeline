"""Motor de simulación: avanza cada vehículo paso a paso y produce paquetes de telemetría.

Todas las funciones reciben el generador aleatorio (rng) como parámetro: con la misma
semilla la simulación es idéntica, lo que permite probarla.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from fleet_pipeline.simulator.fleet import VehicleSpec
from fleet_pipeline.simulator.routes import Route

BOGOTA_TZ = ZoneInfo("America/Bogota")
OPERATING_START = time(5, 0)  # los vehículos operan de 5:00 a 21:00 hora de Colombia;
OPERATING_END = time(21, 0)  # fuera de ese horario el motor está apagado y no reporta

MAX_ACCEL_KMH_PER_S = 5.4  # ~1,5 m/s²
MAX_DECEL_KMH_PER_S = 10.8  # ~3 m/s²
URBAN_STOP_PROBABILITY = 0.06  # por paso: semáforos y trancones
URBAN_STOP_STEPS = (1, 6)
END_OF_ROUTE_DWELL_STEPS = 30  # cargue y descargue al final de cada recorrido
REFUEL_THRESHOLD_PCT = 12.0
REFUEL_TARGET_PCT = 95.0
REFUEL_DWELL_STEPS = 60
GPS_NOISE_M = 4.0
FUEL_SENSOR_NOISE_PCT = 0.3
METERS_PER_DEGREE_LAT = 111_320.0

FAULT_RATE = 0.02
FAULT_KINDS = ("coords_zero", "far_jump", "bad_id", "absurd_speed", "missing_field", "naive_ts")


@dataclass(frozen=True)
class VehicleState:
    distance_m: float
    direction: int
    speed_kmh: float
    fuel_pct: float
    stopped_steps: int = 0


def initial_state(spec: VehicleSpec, route: Route) -> VehicleState:
    return VehicleState(
        distance_m=spec.start_fraction * route.length_m,
        direction=spec.start_direction,
        speed_kmh=0.0,
        fuel_pct=spec.initial_fuel_pct,
    )


def is_operating(ts: datetime) -> bool:
    local = ts.astimezone(BOGOTA_TZ).time()
    return OPERATING_START <= local < OPERATING_END


def target_speed(
    spec: VehicleSpec, route: Route, road_speed_kmh: float, rng: random.Random
) -> float:
    """Velocidad a la que el conductor quiere ir en este punto."""
    profile = spec.profile
    cap = profile.urban_cap_kmh if route.is_urban else profile.highway_cap_kmh
    speed = min(road_speed_kmh * profile.speed_factor, cap)
    if rng.random() < profile.burst_probability:
        speed *= profile.burst_factor
    return speed


def step(
    spec: VehicleSpec, state: VehicleState, route: Route, dt_s: float, rng: random.Random
) -> VehicleState:
    """Avanza un vehículo `dt_s` segundos."""
    if state.stopped_steps > 0:
        return _consume_fuel(
            spec, replace(state, speed_kmh=0.0, stopped_steps=state.stopped_steps - 1), 0, dt_s
        )

    if route.is_urban and rng.random() < URBAN_STOP_PROBABILITY:
        return _consume_fuel(
            spec,
            replace(state, speed_kmh=0.0, stopped_steps=rng.randint(*URBAN_STOP_STEPS) - 1),
            0,
            dt_s,
        )

    desired = target_speed(spec, route, route.point_at(state.distance_m).road_speed_kmh, rng)
    change = max(
        -MAX_DECEL_KMH_PER_S * dt_s, min(MAX_ACCEL_KMH_PER_S * dt_s, desired - state.speed_kmh)
    )
    new_speed = max(0.0, state.speed_kmh + change)
    travelled_m = (state.speed_kmh + new_speed) / 2 / 3.6 * dt_s
    new_distance = state.distance_m + travelled_m * state.direction

    moved = replace(state, distance_m=new_distance, speed_kmh=new_speed)
    if new_distance >= route.length_m or new_distance <= 0:
        # Fin del recorrido: se detiene a cargar/descargar y vuelve en sentido contrario.
        moved = replace(
            moved,
            distance_m=min(max(new_distance, 0.0), route.length_m),
            direction=-state.direction,
            speed_kmh=0.0,
            stopped_steps=END_OF_ROUTE_DWELL_STEPS,
        )
    moved = _consume_fuel(spec, moved, travelled_m, dt_s)

    if moved.fuel_pct < REFUEL_THRESHOLD_PCT:
        # Tanqueo: se detiene y llena el tanque. En la capa Plata se ve como un aumento.
        return replace(
            moved, fuel_pct=REFUEL_TARGET_PCT, speed_kmh=0.0, stopped_steps=REFUEL_DWELL_STEPS
        )
    return moved


def _consume_fuel(
    spec: VehicleSpec, state: VehicleState, travelled_m: float, dt_s: float
) -> VehicleState:
    model = spec.model
    litres = travelled_m / 100_000 * model.consumption_l_100km * (1 + spec.profile.fuel_penalty)
    if travelled_m == 0:
        litres += model.idle_l_per_h * dt_s / 3600
    fuel_pct = max(0.0, state.fuel_pct - litres / model.tank_capacity_l * 100)
    return replace(state, fuel_pct=fuel_pct)


def make_packet(
    spec: VehicleSpec, state: VehicleState, route: Route, ts: datetime, rng: random.Random
) -> dict[str, Any]:
    """Paquete tal como lo enviaría el dispositivo, con ruido de GPS y del sensor."""
    point = route.point_at(state.distance_m)
    noise_lat = rng.gauss(0, GPS_NOISE_M) / METERS_PER_DEGREE_LAT
    noise_lon = rng.gauss(0, GPS_NOISE_M) / (
        METERS_PER_DEGREE_LAT * math.cos(math.radians(point.lat))
    )
    speed = max(0.0, state.speed_kmh + rng.gauss(0, 1.0)) if state.speed_kmh > 0 else 0.0
    fuel = min(100.0, max(0.0, state.fuel_pct + rng.gauss(0, FUEL_SENSOR_NOISE_PCT)))
    return {
        "vehicle_id": spec.vehicle_id,
        "ts": ts.astimezone(BOGOTA_TZ).isoformat(timespec="seconds"),
        "lat": round(point.lat + noise_lat, 6),
        "lon": round(point.lon + noise_lon, 6),
        "speed_kmh": round(speed, 1),
        "fuel_pct": round(fuel, 2),
    }


def maybe_corrupt(packet: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Con probabilidad FAULT_RATE, daña el paquete como lo haría un dispositivo real."""
    if rng.random() >= FAULT_RATE:
        return packet
    return corrupt(packet, rng.choice(FAULT_KINDS))


def corrupt(packet: dict[str, Any], kind: str) -> dict[str, Any]:
    bad = dict(packet)
    if kind == "coords_zero":
        bad["lat"], bad["lon"] = 0.0, 0.0
    elif kind == "far_jump":
        bad["lat"] = round(bad["lat"] + 1.5, 6)  # ~165 km al norte
    elif kind == "bad_id":
        bad["vehicle_id"] = bad["vehicle_id"][:6]
    elif kind == "absurd_speed":
        bad["speed_kmh"] = 900.0
    elif kind == "missing_field":
        del bad["fuel_pct"]
    elif kind == "naive_ts":
        bad["ts"] = bad["ts"][:19]  # sin zona horaria
    else:
        raise ValueError(f"Tipo de falla desconocido: {kind}")
    return bad
