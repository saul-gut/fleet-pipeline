"""Reglas de la capa Plata (D-17): geocerca aplicada, alertas y consumo predictivo."""

from datetime import UTC, datetime, timedelta

import pytest

from fleet_pipeline.processing.rules import (
    AlertType,
    Episode,
    Reading,
    Severity,
    ZoneLimit,
    alert_type_for,
    applied_zone,
    consumption_l_per_km,
    driver_score,
    effective_step_m,
    fuel_needed_l,
    is_low_fuel,
    is_over_threshold,
    is_refuel,
    low_fuel_severity,
    penalty_points,
    remaining_route_km,
    severity,
    speeding_episodes,
)

URBAN = ZoneLimit("BOG-URB", "urban", 50)
SCHOOL = ZoneLimit("BOG-SCH-01", "school", 30)
HOTSPOT = ZoneLimit("BOG-HR-01", "high_risk", 40)
HIGHWAY = ZoneLimit("COR-HWY", "highway", 90)
T0 = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)


# --- Geocerca más restrictiva -----------------------------------------------------------


def test_most_restrictive_zone_wins() -> None:
    assert applied_zone([URBAN, SCHOOL, HOTSPOT]) == SCHOOL


def test_urban_overrides_highway_inside_the_city() -> None:
    assert applied_zone([HIGHWAY, URBAN]) == URBAN


def test_no_zone_returns_none() -> None:
    assert applied_zone([]) is None


def test_tie_is_broken_by_risk_priority() -> None:
    urban_40 = ZoneLimit("X-URB", "urban", 40)
    assert applied_zone([urban_40, HOTSPOT]) == HOTSPOT


# --- Umbral, severidad y tipo -----------------------------------------------------------


@pytest.mark.parametrize(
    ("speed", "limit", "expected"),
    [(55.0, 50, False), (55.1, 50, True), (35.0, 30, False), (36.0, 30, True)],
)
def test_threshold_includes_gps_tolerance(speed: float, limit: int, expected: bool) -> None:
    assert is_over_threshold(speed, limit) is expected


@pytest.mark.parametrize(
    ("speed", "limit", "expected"),
    [
        (60.0, 50, Severity.MINOR),  # +20 %: límite superior de leve
        (61.0, 50, Severity.MODERATE),
        (45.0, 30, Severity.MODERATE),  # +50 %: límite superior de moderada
        (46.0, 30, Severity.SEVERE),
        (140.0, 90, Severity.SEVERE),
    ],
)
def test_severity_by_excess_percentage(speed: float, limit: int, expected: Severity) -> None:
    assert severity(speed, limit) == expected


@pytest.mark.parametrize(
    ("zone_type", "expected"),
    [
        ("school", AlertType.GEOFENCE_VIOLATION),
        ("high_risk", AlertType.GEOFENCE_VIOLATION),
        ("road_hazard", AlertType.GEOFENCE_VIOLATION),
        ("urban", AlertType.SPEEDING),
        ("highway", AlertType.SPEEDING),
        (None, AlertType.SPEEDING),
    ],
)
def test_alert_type_depends_on_zone(zone_type: str | None, expected: AlertType) -> None:
    assert alert_type_for(zone_type) == expected


# --- Episodios ------------------------------------------------------------------------


def readings(
    speeds: list[float], zone: str = "BOG-URB", limit: int = 50, step_s: int = 10
) -> list[Reading]:
    return [
        Reading(T0 + timedelta(seconds=step_s * i), s, zone, limit) for i, s in enumerate(speeds)
    ]


def test_single_spike_is_not_an_episode() -> None:
    assert speeding_episodes(readings([40, 70, 40, 70, 40])) == []


def test_two_consecutive_readings_make_an_episode() -> None:
    assert speeding_episodes(readings([40, 70, 72, 40])) == [Episode(1, 2)]


def test_episode_at_the_end_of_the_series_is_kept() -> None:
    assert speeding_episodes(readings([40, 70, 72, 75])) == [Episode(1, 3)]


def test_long_excess_is_one_episode_not_many() -> None:
    assert speeding_episodes(readings([40] + [70] * 18 + [40])) == [Episode(1, 18)]


def test_episode_splits_when_zone_changes() -> None:
    series = readings([70, 70]) + [
        Reading(T0 + timedelta(seconds=20 + 10 * i), 45, "BOG-SCH-01", 30) for i in range(2)
    ]
    assert speeding_episodes(series) == [Episode(0, 1), Episode(2, 3)]


def test_episode_splits_after_a_time_gap() -> None:
    """Dos pares de excesos separados por 5 minutos sin datos son dos episodios."""
    offsets_s = [0, 10, 310, 320]
    series = [Reading(T0 + timedelta(seconds=s), 70, "BOG-URB", 50) for s in offsets_s]
    assert speeding_episodes(series) == [Episode(0, 1), Episode(2, 3)]


# --- Combustible -----------------------------------------------------------------------


def test_stopped_vehicle_adds_no_distance() -> None:
    assert effective_step_m(8.0, 0) == 0
    assert effective_step_m(12.0, 30) == 0  # menos de 15 m: ruido GPS
    assert effective_step_m(120.0, 40) == 120.0


def test_refuel_detection() -> None:
    assert is_refuel(20.0, 95.0)
    assert not is_refuel(50.0, 50.4)  # ruido del sensor


def test_consumption_uses_real_data_when_window_is_long_enough() -> None:
    # 7 L en 20 km = 0,35 L/km (35 L/100 km)
    assert consumption_l_per_km(7.0, 20_000, reference_l_100km=30) == pytest.approx(0.35)


@pytest.mark.parametrize(("used_l", "distance_m"), [(1.0, 3_000), (0.0, 20_000), (-2.0, 20_000)])
def test_consumption_falls_back_to_reference(used_l: float, distance_m: float) -> None:
    assert consumption_l_per_km(used_l, distance_m, reference_l_100km=35) == pytest.approx(0.35)


def test_remaining_km_depends_on_direction() -> None:
    assert remaining_route_km(0.25, 1, 450_000) == pytest.approx(337.5)
    assert remaining_route_km(0.25, -1, 450_000) == pytest.approx(112.5)


def test_low_fuel_alert_for_truck_starting_with_30_percent() -> None:
    """Camión de 400 L con 30 % (120 L), 35 L/100 km y 450 km por delante."""
    needed = fuel_needed_l(remaining_km=450, consumption_l_km=0.35)
    assert needed == pytest.approx(181.125)  # 450 × 0,35 × 1,15
    assert is_low_fuel(120.0, needed)


@pytest.mark.parametrize(
    ("fuel_l", "needed_l", "expected"),
    [
        (60.0, 181.1, Severity.SEVERE),  # 33 % de lo necesario
        (119.4, 181.1, Severity.MODERATE),  # 66 %: el camión que sale con 30 %
        (170.0, 181.1, Severity.MINOR),  # 94 %
    ],
)
def test_low_fuel_severity_by_coverage(fuel_l: float, needed_l: float, expected: Severity) -> None:
    assert low_fuel_severity(fuel_l, needed_l) == expected


def test_enough_fuel_does_not_alert() -> None:
    assert not is_low_fuel(300.0, fuel_needed_l(remaining_km=450, consumption_l_km=0.35))


# --- Driver Score (D-19) ----------------------------------------------------------------


def test_penalty_points_weight_severity_and_geofence() -> None:
    alerts = [
        ("SPEEDING", "minor"),  # 1
        ("SPEEDING", "severe"),  # 6
        ("GEOFENCE_VIOLATION", "moderate"),  # 3 × 2 = 6
        ("LOW_FUEL_FOR_ROUTE", "severe"),  # no cuenta
    ]
    assert penalty_points(alerts) == 13


def test_clean_driver_scores_100() -> None:
    assert driver_score(0, 420) == 100


def test_score_uses_points_per_100_km() -> None:
    """Las mismas alertas en el doble de km pesan la mitad."""
    assert driver_score(100, 200) > driver_score(100, 100)


def test_score_reference_values() -> None:
    # 100 puntos por 100 km -> 100 × e^-0,5 = 60,65
    assert driver_score(100, 100) == pytest.approx(60.653, abs=0.001)
    # Vehículo agresivo real: 322,5 puntos por 100 km -> 19,9
    assert driver_score(322.5, 100) == pytest.approx(19.94, abs=0.01)


def test_score_never_reaches_zero_and_does_not_saturate() -> None:
    assert 0 < driver_score(1000, 100) < driver_score(500, 100)


def test_too_few_km_has_no_score() -> None:
    assert driver_score(0, 4.9) is None
