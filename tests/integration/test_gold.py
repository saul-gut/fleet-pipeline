"""Capa Oro: privacidad por diseño y Driver Score idéntico al de rules.py (D-19)."""

import psycopg
import pytest
from psycopg import errors

from fleet_pipeline.processing.rules import driver_score, penalty_points

pytestmark = pytest.mark.integration


def test_gold_never_stores_real_ids(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int]
) -> None:
    vehicle, key = processed_trip
    ids = conn_autocommit.execute(
        "SELECT DISTINCT vehicle_id_masked FROM gold.fleet_events WHERE vehicle_key = %s", (key,)
    ).fetchall()
    assert ids == [("VHC-****-TST",)]
    leaked = conn_autocommit.execute(
        "SELECT count(*) FROM gold.fleet_events WHERE vehicle_id_masked = %s", (vehicle,)
    ).fetchone()
    assert leaked == (0,)


def test_analyst_reads_gold_but_not_bronze(conninfo: str, processed_trip: tuple[str, int]) -> None:
    with psycopg.connect(conninfo) as conn:
        conn.execute("SET LOCAL ROLE bi_analyst")
        row = conn.execute(
            "SELECT count(*) FROM gold.fleet_events WHERE vehicle_key = %s", (processed_trip[1],)
        ).fetchone()
        assert row == (70,)
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM bronze.vehicles LIMIT 1")


def test_alert_readings_are_linked_to_their_episode(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int]
) -> None:
    rows = conn_autocommit.execute(
        "SELECT alert_type, severity, count(*) FROM gold.fleet_events "
        "WHERE vehicle_key = %s AND alert_id IS NOT NULL GROUP BY 1, 2",
        (processed_trip[1],),
    ).fetchall()
    assert rows == [("SPEEDING", "moderate", 3)]  # 70 en zona de 50 = +40 %


def test_sql_driver_score_matches_python(
    conn_autocommit: psycopg.Connection, processed_trip: tuple[str, int]
) -> None:
    row = conn_autocommit.execute(
        "SELECT km, penalty_points, score FROM gold.driver_score WHERE vehicle_key = %s",
        (processed_trip[1],),
    ).fetchone()
    assert row is not None
    km, points, score = row
    assert points == penalty_points([("SPEEDING", "moderate")]) == 3
    assert km == pytest.approx(7.66, abs=0.1)
    assert float(score) == pytest.approx(driver_score(points, km), abs=0.05)
