"""Reglas de calidad del dato en la ingesta (D-09)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from fleet_pipeline.ingestion.quality import (
    PreviousReading,
    check_against_previous,
    check_context,
    evaluate,
    parse_packet,
)
from fleet_pipeline.ingestion.schemas import TelemetryPacket

NOW = datetime(2026, 11, 20, 23, 0, tzinfo=UTC)


def make_raw(**overrides: Any) -> dict[str, Any]:
    """Paquete válido en Bogotá; cada test cambia solo el campo que le interesa."""
    raw: dict[str, Any] = {
        "vehicle_id": "VHC-8472-ABC",
        "ts": NOW.isoformat(),
        "lat": 4.59808,
        "lon": -74.07609,
        "speed_kmh": 42.5,
        "fuel_pct": 81.3,
    }
    raw.update(overrides)
    return raw


def make_packet(**overrides: Any) -> TelemetryPacket:
    return TelemetryPacket.model_validate(make_raw(**overrides))


def previous_from(packet: TelemetryPacket, **overrides: Any) -> PreviousReading:
    values = {
        "ts": packet.ts - timedelta(seconds=10),
        "lat": packet.lat,
        "lon": packet.lon,
        "speed_kmh": packet.speed_kmh,
        "fuel_pct": packet.fuel_pct,
    }
    values.update(overrides)
    return PreviousReading(**values)


# --- Nivel 1: formato y rangos físicos -------------------------------------------------


def test_valid_packet_parses_without_reasons() -> None:
    packet, reasons = parse_packet(make_raw())
    assert packet is not None
    assert reasons == []


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"vehicle_id": "VHC-12-X"}, "vehicle_id:string_pattern_mismatch"),
        ({"vehicle_id": "ABC"}, "vehicle_id:string_pattern_mismatch"),
        ({"vehicle_id": ""}, "vehicle_id:string_pattern_mismatch"),
        ({"vehicle_id": None}, "vehicle_id:string_type"),
        ({"ts": "2026-11-20T18:00:00"}, "ts:timezone_aware"),
        ({"ts": "ayer"}, "ts:datetime_from_date_parsing"),
        ({"lat": 91}, "lat:less_than_equal"),
        ({"lon": -181}, "lon:greater_than_equal"),
        ({"speed_kmh": -10}, "speed_kmh:greater_than_equal"),
        ({"speed_kmh": 900}, "speed_kmh:less_than_equal"),
        ({"fuel_pct": 130}, "fuel_pct:less_than_equal"),
        ({"fuel_pct": "NaN"}, "fuel_pct:finite_number"),
    ],
)
def test_invalid_field_is_rejected_with_reason(
    overrides: dict[str, Any], expected_reason: str
) -> None:
    packet, reasons = parse_packet(make_raw(**overrides))
    assert packet is None
    assert expected_reason in reasons


def test_missing_fields_are_all_reported() -> None:
    raw = make_raw()
    del raw["lat"], raw["fuel_pct"]
    _, reasons = parse_packet(raw)
    assert set(reasons) == {"lat:missing", "fuel_pct:missing"}


def test_non_object_payload_is_rejected() -> None:
    _, reasons = parse_packet(["no", "soy", "un", "paquete"])
    assert reasons == ["payload:model_type"]


def test_unknown_extra_fields_are_ignored() -> None:
    packet, reasons = parse_packet(make_raw(firmware="v2.1"))
    assert packet is not None
    assert reasons == []


def test_zero_speed_and_zero_fuel_are_valid() -> None:
    """Vehículo detenido y tanque vacío son datos reales, no errores."""
    packet, reasons = parse_packet(make_raw(speed_kmh=0, fuel_pct=0))
    assert packet is not None
    assert reasons == []


# --- Nivel 1b: contexto (hora actual y territorio) ------------------------------------


def test_valid_packet_has_no_context_problems() -> None:
    assert check_context(make_packet(), NOW) == []


@pytest.mark.parametrize(
    ("lat", "lon", "expected"),
    [
        (0.0, 0.0, ["coords_zero", "outside_colombia"]),  # "Null Island": GPS sin señal
        (0.0, -74.0, ["coords_zero"]),  # lat 0 cruza el sur de Colombia: solo se marca el cero
        (40.4168, -3.7038, ["outside_colombia"]),  # Madrid
        (12.5847, -81.7006, []),  # San Andrés sí es Colombia
        (-4.2153, -69.9406, []),  # Leticia
    ],
)
def test_coordinates_rules(lat: float, lon: float, expected: list[str]) -> None:
    assert check_context(make_packet(lat=lat, lon=lon), NOW) == expected


def test_timestamp_slightly_in_future_is_tolerated() -> None:
    packet = make_packet(ts=(NOW + timedelta(minutes=4)).isoformat())
    assert check_context(packet, NOW) == []


def test_timestamp_far_in_future_is_rejected() -> None:
    packet = make_packet(ts=(NOW + timedelta(minutes=6)).isoformat())
    assert check_context(packet, NOW) == ["ts_in_future"]


def test_timestamp_older_than_seven_days_is_rejected() -> None:
    packet = make_packet(ts=(NOW - timedelta(days=7, seconds=1)).isoformat())
    assert check_context(packet, NOW) == ["ts_too_old"]


def test_timestamp_in_other_timezone_is_compared_correctly() -> None:
    """18:00 en Bogotá es 23:00 UTC: es el mismo instante que NOW."""
    packet = make_packet(ts="2026-11-20T18:00:00-05:00")
    assert check_context(packet, NOW) == []


# --- Nivel 2: comparación con la lectura anterior --------------------------------------


def test_first_reading_of_vehicle_has_nothing_to_compare() -> None:
    result = check_against_previous(make_packet(), previous=None)
    assert result.reasons == []
    assert result.flags == []


def test_normal_movement_passes() -> None:
    packet = make_packet()
    # ~120 m en 10 s = ~43 km/h, coherente con la velocidad reportada
    previous = previous_from(packet, lat=packet.lat - 0.00108)
    result = check_against_previous(packet, previous)
    assert result.reasons == []
    assert result.flags == []


def test_impossible_jump_is_rejected() -> None:
    """Bogotá a Tunja (~120 km) en 10 segundos."""
    packet = make_packet()
    previous = previous_from(packet, lat=5.5353, lon=-73.3678)
    result = check_against_previous(packet, previous)
    assert result.reasons == ["impossible_jump"]


def test_small_gps_jitter_is_not_a_jump() -> None:
    """60 m en 1 s son 216 km/h implícitos, pero a esa distancia es ruido del GPS."""
    packet = make_packet()
    previous = previous_from(packet, ts=packet.ts - timedelta(seconds=1), lat=packet.lat - 0.00054)
    result = check_against_previous(packet, previous)
    assert result.reasons == []


def test_abrupt_speed_change_is_flagged_not_rejected() -> None:
    """De 90 a 0 km/h en 2 s: puede ser un choque, se guarda marcado."""
    packet = make_packet(speed_kmh=0)
    previous = previous_from(packet, ts=packet.ts - timedelta(seconds=2), speed_kmh=90)
    result = check_against_previous(packet, previous)
    assert result.reasons == []
    assert result.flags == ["abrupt_speed_change"]


def test_abrupt_fuel_drop_is_flagged() -> None:
    packet = make_packet(fuel_pct=70)
    previous = previous_from(packet, ts=packet.ts - timedelta(minutes=5), fuel_pct=80)
    result = check_against_previous(packet, previous)
    assert result.flags == ["abrupt_fuel_drop"]


def test_slow_fuel_consumption_is_not_flagged() -> None:
    """Bajar 10 puntos en 2 horas es consumo normal de carretera."""
    packet = make_packet(fuel_pct=70)
    previous = previous_from(packet, ts=packet.ts - timedelta(hours=2), fuel_pct=80)
    result = check_against_previous(packet, previous)
    assert result.flags == []


def test_refuel_is_not_flagged() -> None:
    """Si el combustible sube es una recarga; se trata en la capa Plata."""
    packet = make_packet(fuel_pct=95)
    previous = previous_from(packet, fuel_pct=20)
    result = check_against_previous(packet, previous)
    assert result.flags == []


def test_same_timestamp_as_previous_is_skipped() -> None:
    packet = make_packet()
    previous = previous_from(packet, ts=packet.ts, lat=10.0)
    result = check_against_previous(packet, previous)
    assert result.reasons == []


# --- Evaluación completa ---------------------------------------------------------------


def test_evaluate_combines_context_and_history() -> None:
    packet = make_packet(ts=(NOW + timedelta(hours=1)).isoformat())
    previous = previous_from(packet, lat=5.5353, lon=-73.3678)
    result = evaluate(packet, NOW, previous)
    assert result.reasons == ["ts_in_future", "impossible_jump"]
    assert result.is_rejected
