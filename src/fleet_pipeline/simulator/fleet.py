"""Composición de la flota simulada (D-14). Determinista: la misma semilla da la misma flota."""

from __future__ import annotations

import random
import string
from dataclasses import dataclass

from fleet_pipeline.simulator.routes import Route


@dataclass(frozen=True)
class DriverProfile:
    name: str
    speed_factor: float  # multiplica la velocidad típica de la vía (OSRM)
    urban_cap_kmh: float  # tope de velocidad del conductor en ciudad
    highway_cap_kmh: float  # tope en carretera
    burst_probability: float  # probabilidad por paso de un exceso momentáneo
    burst_factor: float
    fuel_penalty: float  # consumo extra por conducción brusca
    # Cuánto respeta el límite de la geocerca donde va (D-18): límite + margen.
    # None = lo ignora (solo lo frenan sus propios topes).
    zone_margin_kmh: float | None


# Las velocidades de OSRM son de flujo libre, no de tráfico real: por eso cada perfil tiene
# un tope. Así los excesos dependen del conductor y no de la estimación de OSRM.
PROFILES: dict[str, DriverProfile] = {
    "prudent": DriverProfile("prudent", 0.85, 48, 88, 0.00, 1.00, 0.00, -2),
    "normal": DriverProfile("normal", 0.95, 56, 95, 0.02, 1.15, 0.05, 8),
    "aggressive": DriverProfile("aggressive", 1.10, 75, 115, 0.05, 1.30, 0.15, None),
}


@dataclass(frozen=True)
class VehicleModel:
    vehicle_type: str
    tank_capacity_l: float
    consumption_l_100km: float
    idle_l_per_h: float


MODELS: dict[str, VehicleModel] = {
    "car": VehicleModel("car", 45, 8, 0.8),
    "van": VehicleModel("van", 80, 12, 1.2),
    "truck": VehicleModel("truck", 400, 35, 2.5),
}


@dataclass(frozen=True)
class VehicleSpec:
    vehicle_id: str
    model: VehicleModel
    profile: DriverProfile
    route_id: str
    initial_fuel_pct: float
    start_fraction: float  # posición inicial como fracción de la ruta
    start_direction: int  # 1 = de inicio a fin, -1 = de fin a inicio


# (grupo de rutas, cantidad de vehículos, tipos posibles)
FLEET_LAYOUT = [
    (("BOG-01", "BOG-02", "BOG-03", "BOG-04", "BOG-05"), 10, ("car", "van")),
    (("MED-01", "MED-02", "MED-03"), 6, ("car", "van")),
    (("COR-01",), 4, ("truck",)),
]
# 12 prudentes, 5 normales y 3 agresivos: ~60 / 25 / 15 %.
PROFILE_MIX = ["prudent"] * 12 + ["normal"] * 5 + ["aggressive"] * 3
# El ID del enunciado y otro con las mismas letras: muestran la colisión de la máscara.
SHOWCASE_IDS = ("VHC-8472-ABC", "VHC-3391-ABC")
LOW_FUEL_START_PCT = 30.0  # insuficiente para los 450 km del corredor (necesita ~40 %)


def build_fleet(routes: dict[str, Route], seed: int = 42) -> list[VehicleSpec]:
    """Crea los 20 vehículos de la D-14 de forma reproducible."""
    rng = random.Random(seed)
    profiles = PROFILE_MIX.copy()
    rng.shuffle(profiles)
    ids = _unique_ids(rng, len(profiles))

    fleet: list[VehicleSpec] = []
    for route_ids, count, types in FLEET_LAYOUT:
        for i in range(count):
            route_id = route_ids[i % len(route_ids)]
            interurban = not routes[route_id].is_urban
            # Camiones: dos salen de Bogotá y dos de Medellín, desde el inicio de su viaje.
            direction = (1 if i % 2 == 0 else -1) if interurban else rng.choice((1, -1))
            fleet.append(
                VehicleSpec(
                    vehicle_id=ids[len(fleet)],
                    model=MODELS[rng.choice(types)],
                    profile=PROFILES[profiles[len(fleet)]],
                    route_id=route_id,
                    initial_fuel_pct=round(rng.uniform(50, 95), 1),
                    start_fraction=(0.0 if direction == 1 else 1.0) if interurban else rng.random(),
                    start_direction=direction,
                )
            )

    # El primer camión sale de Bogotá con combustible insuficiente para llegar a Medellín.
    first_truck = next(i for i, v in enumerate(fleet) if v.model.vehicle_type == "truck")
    fleet[first_truck] = _replace_fuel(fleet[first_truck], LOW_FUEL_START_PCT)
    return fleet


def _unique_ids(rng: random.Random, count: int) -> list[str]:
    ids = list(SHOWCASE_IDS)
    while len(ids) < count:
        candidate = (
            f"VHC-{rng.randint(1000, 9999)}-{''.join(rng.choices(string.ascii_uppercase, k=3))}"
        )
        if candidate not in ids:
            ids.append(candidate)
    return ids


def _replace_fuel(spec: VehicleSpec, fuel_pct: float) -> VehicleSpec:
    return VehicleSpec(
        vehicle_id=spec.vehicle_id,
        model=spec.model,
        profile=spec.profile,
        route_id=spec.route_id,
        initial_fuel_pct=fuel_pct,
        start_fraction=spec.start_fraction,
        start_direction=spec.start_direction,
    )
