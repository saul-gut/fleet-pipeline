"""Simulador de flota: rutas, composición, física simple y coherencia con las reglas de calidad."""

import random
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from fleet_pipeline.ingestion.quality import (
    PreviousReading,
    check_context,
    evaluate,
    parse_packet,
)
from fleet_pipeline.simulator.engine import (
    FAULT_KINDS,
    REFUEL_TARGET_PCT,
    VehicleState,
    corrupt,
    is_operating,
    make_packet,
    step,
)
from fleet_pipeline.simulator.fleet import (
    LOW_FUEL_START_PCT,
    MODELS,
    PROFILES,
    VehicleSpec,
    build_fleet,
)
from fleet_pipeline.simulator.routes import Route, build_route, load_routes
from fleet_pipeline.simulator.runner import FleetSimulation
from fleet_pipeline.simulator.zones import ZoneIndex, with_zone_limits

Routes = dict[str, Route]

# Ruta sintética: ~1,1 km hacia el norte en Bogotá, en dos segmentos.
ROUTE = build_route(
    "TEST",
    "Ruta de prueba",
    "urban_bogota",
    [(4.60, -74.07), (4.605, -74.07), (4.61, -74.07)],
    [40.0, 80.0],
)
HIGHWAY = replace(ROUTE, kind="interurban")
NOON_BOGOTA = datetime(2026, 11, 20, 17, 0, tzinfo=UTC)  # 12:00 en Colombia


def spec(profile: str = "prudent", model: str = "car", **overrides: object) -> VehicleSpec:
    values = {
        "vehicle_id": "VHC-8472-ABC",
        "model": MODELS[model],
        "profile": PROFILES[profile],
        "route_id": "TEST",
        "initial_fuel_pct": 80.0,
        "start_fraction": 0.0,
        "start_direction": 1,
    }
    values.update(overrides)
    return VehicleSpec(**values)  # type: ignore[arg-type]


# --- Rutas -----------------------------------------------------------------------------


def test_point_at_start_middle_and_end() -> None:
    assert (ROUTE.point_at(0).lat, ROUTE.point_at(0).lon) == (4.60, -74.07)
    assert ROUTE.point_at(ROUTE.length_m).lat == pytest.approx(4.61)
    middle = ROUTE.point_at(ROUTE.length_m / 2)
    assert middle.lat == pytest.approx(4.605, abs=1e-6)


def test_point_at_uses_speed_of_its_segment() -> None:
    assert ROUTE.point_at(10).road_speed_kmh == 40.0
    assert ROUTE.point_at(ROUTE.length_m - 10).road_speed_kmh == 80.0


def test_point_at_clamps_outside_the_route() -> None:
    assert ROUTE.point_at(-50).lat == 4.60
    assert ROUTE.point_at(ROUTE.length_m + 50).lat == pytest.approx(4.61)


def test_route_length_is_geodesic() -> None:
    # 0,01° de latitud cerca del ecuador ≈ 1 105,8 m
    assert ROUTE.length_m == pytest.approx(1105.8, abs=1)


def test_invalid_route_is_refused() -> None:
    with pytest.raises(ValueError):
        build_route("X", "x", "urban_bogota", [(4.6, -74.0)], [])


# --- Flota -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_routes() -> Routes:
    return load_routes()


def test_fleet_composition_matches_d023(real_routes: Routes) -> None:
    fleet = build_fleet(real_routes)
    groups = Counter(real_routes[v.route_id].kind for v in fleet)
    assert groups == {"urban_bogota": 10, "urban_medellin": 6, "interurban": 4}
    assert Counter(v.profile.name for v in fleet) == {"prudent": 12, "normal": 5, "aggressive": 3}
    assert len({v.vehicle_id for v in fleet}) == 20
    assert {"VHC-8472-ABC", "VHC-3391-ABC"} <= {v.vehicle_id for v in fleet}


def test_fleet_is_reproducible_with_same_seed(real_routes: Routes) -> None:
    assert build_fleet(real_routes, seed=7) == build_fleet(real_routes, seed=7)
    assert build_fleet(real_routes, seed=7) != build_fleet(real_routes, seed=8)


def test_one_truck_starts_with_insufficient_fuel(real_routes: Routes) -> None:
    trucks = [v for v in build_fleet(real_routes) if v.model.vehicle_type == "truck"]
    low = [v for v in trucks if v.initial_fuel_pct == LOW_FUEL_START_PCT]
    assert len(low) == 1
    route_km = real_routes["COR-01"].length_m / 1000
    needed_pct = (
        route_km * low[0].model.consumption_l_100km / 100 / low[0].model.tank_capacity_l * 100
    )
    assert needed_pct > LOW_FUEL_START_PCT


# --- Motor -----------------------------------------------------------------------------


def run_steps(vehicle: VehicleSpec, route: Route, steps: int, seed: int = 1) -> list[VehicleState]:
    rng = random.Random(seed)
    state = VehicleState(distance_m=0.0, direction=1, speed_kmh=0.0, fuel_pct=80.0)
    states = []
    for _ in range(steps):
        state = step(vehicle, state, route, 10, rng)
        states.append(state)
    return states


# --- Zonas (D-18) ----------------------------------------------------------------------

SCHOOL_ROUTE = replace(HIGHWAY, segment_zone_limits=(30, 30))


def test_prudent_driver_respects_school_zone() -> None:
    speeds = [s.speed_kmh for s in run_steps(spec("prudent"), SCHOOL_ROUTE, 200)]
    assert max(speeds) <= 30 - 2


def test_normal_driver_exceeds_zone_only_slightly() -> None:
    speeds = [s.speed_kmh for s in run_steps(spec("normal"), SCHOOL_ROUTE, 200)]
    assert 30 < max(speeds) <= 30 + 8


def test_aggressive_driver_ignores_the_zone() -> None:
    speeds = [s.speed_kmh for s in run_steps(spec("aggressive"), SCHOOL_ROUTE, 200)]
    assert max(speeds) > 60


@pytest.fixture(scope="module")
def zone_index() -> ZoneIndex:
    return ZoneIndex()


def test_zone_index_applies_most_restrictive_geofence(zone_index: ZoneIndex) -> None:
    assert zone_index.limit_at(4.7050, -74.0420) == 50  # Usaquén: solo perímetro urbano
    assert zone_index.limit_at(40.4168, -3.7038) is None  # Madrid: ninguna geocerca


def test_route_segments_get_zone_limits(zone_index: ZoneIndex, real_routes: Routes) -> None:
    route = with_zone_limits(real_routes["BOG-01"], zone_index)
    assert route.segment_zone_limits is not None
    assert len(route.segment_zone_limits) == len(route.coords) - 1
    # BOG-01 pasa por la zona escolar BOG-SCH-01 y el hotspot BOG-HR-03 (D-16).
    assert {30, 40, 50} <= set(route.segment_zone_limits)


def test_prudent_driver_never_exceeds_urban_cap() -> None:
    speeds = [s.speed_kmh for s in run_steps(spec("prudent"), ROUTE, 300)]
    assert max(speeds) <= PROFILES["prudent"].urban_cap_kmh


def test_aggressive_driver_goes_faster_on_highway() -> None:
    prudent = max(s.speed_kmh for s in run_steps(spec("prudent"), HIGHWAY, 300))
    aggressive = max(s.speed_kmh for s in run_steps(spec("aggressive"), HIGHWAY, 300))
    assert aggressive > prudent


def test_acceleration_is_physically_limited() -> None:
    states = run_steps(spec("aggressive"), HIGHWAY, 50)
    changes = [
        abs(b.speed_kmh - a.speed_kmh) / 10 for a, b in zip(states, states[1:], strict=False)
    ]
    assert max(changes) <= 10.8 + 1e-9


def test_vehicle_bounces_at_route_end() -> None:
    states = run_steps(spec("aggressive"), HIGHWAY, 200)
    assert any(s.direction == -1 for s in states)
    assert all(0 <= s.distance_m <= HIGHWAY.length_m for s in states)


def test_fuel_decreases_while_driving() -> None:
    states = run_steps(spec("normal", model="truck"), HIGHWAY, 30)
    assert states[-1].fuel_pct < 80.0


def test_low_fuel_triggers_refuel() -> None:
    rng = random.Random(1)
    state = VehicleState(distance_m=100, direction=1, speed_kmh=60, fuel_pct=12.001)
    new = step(spec("normal", model="car"), state, HIGHWAY, 10, rng)
    assert new.fuel_pct == REFUEL_TARGET_PCT
    assert new.speed_kmh == 0


def test_operating_hours_follow_colombian_time() -> None:
    assert is_operating(NOON_BOGOTA)
    assert not is_operating(datetime(2026, 11, 21, 3, 0, tzinfo=UTC))  # 22:00 en Colombia


def test_no_packets_outside_operating_hours() -> None:
    sim = FleetSimulation([spec()], {"TEST": ROUTE}, random.Random(1))
    assert sim.tick(datetime(2026, 11, 21, 3, 0, tzinfo=UTC), 10) == []


# --- Coherencia con las reglas de calidad de la ingesta -----------------------------------


def test_clean_packets_pass_quality_rules(real_routes: Routes) -> None:
    """Todo paquete sin falla inyectada debe ser aceptado por la validación real."""
    rng = random.Random(3)
    now = NOON_BOGOTA
    for vehicle in build_fleet(real_routes):
        route = real_routes[vehicle.route_id]
        state = VehicleState(route.length_m / 3, 1, 45.0, vehicle.initial_fuel_pct)
        packet, reasons = parse_packet(make_packet(vehicle, state, route, now, rng))
        assert reasons == []
        assert packet is not None
        assert check_context(packet, now) == []


@pytest.mark.parametrize("kind", FAULT_KINDS)
def test_every_injected_fault_is_caught(kind: str) -> None:
    rng = random.Random(1)
    state = VehicleState(100, 1, 40.0, 80.0)
    clean = make_packet(spec(), state, ROUTE, NOON_BOGOTA, rng)
    previous = PreviousReading(
        NOON_BOGOTA - timedelta(seconds=10), clean["lat"], clean["lon"], 40.0, 80.0
    )
    # Mismo orden que la ingesta: primero formato, después contexto e historia.
    packet, reasons = parse_packet(corrupt(clean, kind))
    if packet is not None:
        reasons = evaluate(packet, NOON_BOGOTA, previous).reasons
    assert reasons, f"La falla {kind} no fue detectada"


def test_backfill_timeline_produces_packets_for_whole_fleet() -> None:
    sim = FleetSimulation(
        [spec(), spec(vehicle_id="VHC-1111-AAA")], {"TEST": ROUTE}, random.Random(1)
    )
    packets = [p for i in range(3) for p in sim.tick(NOON_BOGOTA + timedelta(seconds=10 * i), 10)]
    assert len(packets) == 6
