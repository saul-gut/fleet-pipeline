"""Reglas de presentación del dashboard: qué tramo se pinta y cuándo hay flechas."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))

import ui  # noqa: E402  (módulo del dashboard, fuera del paquete)

from fleet_pipeline.queries.dashboard import AlertFilter  # noqa: E402

ALL = ["minor", "moderate", "severe"]
T0 = datetime(2026, 11, 19, 15, 0, tzinfo=UTC)


def reading(i: int, alert_type: str | None = None, severity: str | None = None) -> dict[str, Any]:
    return {
        "ts": T0 + timedelta(seconds=10 * i),
        "lon": -74.10,
        "lat": 4.60 + 0.004 * i,  # ~440 m entre lecturas
        "speed_kmh": 60.0,
        "speed_limit_kmh": 50,
        "zone_type": "urban",
        "alert_type": alert_type,
        "severity": severity,
    }


@pytest.mark.parametrize(
    ("alert_filter", "expected"),
    [
        (AlertFilter(["SPEEDING:urban"], ALL), "minor"),
        (AlertFilter(["SPEEDING:urban"], ["severe"]), None),  # severidad filtrada
        (AlertFilter(["GEOFENCE_VIOLATION:school"], ALL), None),  # tipo filtrado
    ],
)
def test_segment_is_painted_only_if_it_passes_the_filters(
    alert_filter: AlertFilter, expected: str | None
) -> None:
    assert ui.shown_severity(reading(1, "SPEEDING", "minor"), alert_filter) == expected


def test_reading_without_alert_is_never_painted() -> None:
    assert ui.shown_severity(reading(1), AlertFilter(["SPEEDING:urban"], ALL)) is None


def test_arrows_only_when_requested() -> None:
    rows = [reading(i) for i in range(6)]
    alert_filter = AlertFilter([], ALL)
    with_arrows = ui.route_payload(rows, "x", alert_filter, with_arrows=True)
    without = ui.route_payload(rows, "x", alert_filter, with_arrows=False)
    assert with_arrows is not None and with_arrows["arrows"]
    assert without is not None and without["arrows"] == []
