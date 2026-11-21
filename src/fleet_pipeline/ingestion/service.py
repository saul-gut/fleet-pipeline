"""Orquesta la ingesta de un lote: validar, comparar con la historia y persistir."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from fleet_pipeline.ingestion.quality import QualityRules, evaluate, parse_packet
from fleet_pipeline.ingestion.repository import IngestRepository
from fleet_pipeline.ingestion.schemas import TelemetryPacket

log = logging.getLogger(__name__)

MAX_STORED_VEHICLE_ID_LENGTH = 64


class IngestSummary(BaseModel):
    """Resultado de un lote. Los conteos suman `received`."""

    received: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    flagged: int = 0  # subconjunto de accepted: guardados con banderas de calidad


def ingest_batch(
    repo: IngestRepository,
    payloads: list[Any],
    now: datetime,
    rules: QualityRules | None = None,
) -> IngestSummary:
    """Procesa un lote de paquetes crudos y devuelve el resumen.

    Los paquetes válidos se procesan ordenados por vehículo y tiempo, para que cada uno se
    compare con la lectura inmediatamente anterior, incluso si llegó en el mismo lote.
    """
    rules = rules or QualityRules()
    summary = IngestSummary(received=len(payloads))

    parsed: list[tuple[TelemetryPacket, Any]] = []
    for raw in payloads:
        packet, reasons = parse_packet(raw)
        if packet is None:
            repo.insert_rejected(raw, _raw_vehicle_id(raw), reasons)
            summary.rejected += 1
        else:
            parsed.append((packet, raw))

    known = repo.registered_vehicles({packet.vehicle_id for packet, _ in parsed})

    for packet, raw in sorted(parsed, key=lambda item: (item[0].vehicle_id, item[0].ts)):
        is_known = packet.vehicle_id in known
        previous = (
            repo.previous_reading(packet.vehicle_id, packet.ts, packet.ts - rules.max_age)
            if is_known
            else None
        )
        result = evaluate(packet, now, previous, rules)
        if not is_known:
            result.reasons.insert(0, "unknown_vehicle")

        if result.is_rejected:
            repo.insert_rejected(raw, packet.vehicle_id, result.reasons)
            summary.rejected += 1
        elif repo.insert_reading(packet, result.flags):
            summary.accepted += 1
            summary.flagged += bool(result.flags)
        else:
            summary.duplicates += 1

    # Solo conteos en el log: nunca IDs reales de vehículos.
    log.info("Lote procesado: %s", summary.model_dump())
    return summary


def _raw_vehicle_id(raw: Any) -> str | None:
    """Extrae el ID de un paquete inválido, si existe, para facilitar la trazabilidad."""
    if isinstance(raw, dict) and isinstance(raw.get("vehicle_id"), str):
        return raw["vehicle_id"][:MAX_STORED_VEHICLE_ID_LENGTH]
    return None
