"""Ejecuta el procesamiento incremental Bronce -> Plata con los umbrales de RuleParams.

Los umbrales viven en un solo lugar (RuleParams) y se pasan a la función SQL como parámetros:
la especificación en Python y la ejecución en PostGIS no pueden divergir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import psycopg

from fleet_pipeline.processing.rules import RuleParams

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SilverRunResult:
    readings_enriched: int
    alerts_upserted: int
    watermark: datetime


def run_silver(conn: psycopg.Connection, params: RuleParams | None = None) -> SilverRunResult:
    params = params or RuleParams()
    row = conn.execute(
        """
        SELECT * FROM silver.process_new(
            p_default_limit_kmh => %(default_limit_kmh)s,
            p_tolerance_kmh => %(tolerance_kmh)s,
            p_min_consecutive => %(min_consecutive_readings)s,
            p_max_gap => %(max_gap)s,
            p_minor_max_excess => %(minor_max_excess)s,
            p_moderate_max_excess => %(moderate_max_excess)s,
            p_fuel_window_m => %(fuel_window_m)s,
            p_min_fuel_window_m => %(min_fuel_window_m)s,
            p_min_step_m => %(min_step_m)s,
            p_refuel_jump_pct => %(refuel_jump_pct)s,
            p_fuel_safety_factor => %(fuel_safety_factor)s,
            p_low_fuel_severe_coverage => %(low_fuel_severe_coverage)s,
            p_low_fuel_moderate_coverage => %(low_fuel_moderate_coverage)s
        )
        """,
        params.__dict__,
    ).fetchone()
    if row is None:
        raise RuntimeError("silver.process_new no devolvió resultado")
    result = SilverRunResult(*row)
    log.info(
        "Plata: %d lecturas enriquecidas, %d alertas, marca de agua %s",
        result.readings_enriched,
        result.alerts_upserted,
        result.watermark,
    )
    return result
