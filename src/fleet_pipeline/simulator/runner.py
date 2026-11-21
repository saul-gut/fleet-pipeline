"""Ejecución de la simulación: modo histórico y en vivo, enviando lotes a la API (D-14)."""

from __future__ import annotations

import logging
import random
import time
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from fleet_pipeline.simulator.engine import (
    initial_state,
    is_operating,
    make_packet,
    maybe_corrupt,
    step,
)
from fleet_pipeline.simulator.fleet import VehicleSpec
from fleet_pipeline.simulator.routes import Route

log = logging.getLogger(__name__)

MAX_BATCH_SIZE = 1000
SEND_RETRIES = 3


class FleetSimulation:
    """Estado de toda la flota. Cada `tick` avanza el reloj y devuelve los paquetes del paso."""

    def __init__(
        self, fleet: list[VehicleSpec], routes: dict[str, Route], rng: random.Random
    ) -> None:
        self._fleet = fleet
        self._routes = routes
        self._rng = rng
        self._states = {v.vehicle_id: initial_state(v, routes[v.route_id]) for v in fleet}

    @property
    def size(self) -> int:
        return len(self._fleet)

    def tick(self, ts: datetime, dt_s: float) -> list[dict[str, Any]]:
        if not is_operating(ts):
            return []  # motor apagado: no hay movimiento ni reportes
        packets = []
        for spec in self._fleet:
            route = self._routes[spec.route_id]
            state = step(spec, self._states[spec.vehicle_id], route, dt_s, self._rng)
            self._states[spec.vehicle_id] = state
            packet = make_packet(spec, state, route, ts, self._rng)
            packets.append(maybe_corrupt(packet, self._rng))
        return packets


class ApiSender:
    """Envía lotes a POST /telemetry con reintentos ante fallas de red o del servidor.

    `transport` permite inyectar un transporte simulado en los tests (sin red real).
    """

    def __init__(
        self, base_url: str, api_key: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url, headers={"X-API-Key": api_key}, timeout=60, transport=transport
        )
        self.totals: Counter[str] = Counter()

    def send(self, batch: list[dict[str, Any]]) -> None:
        """Reintenta solo lo transitorio (red caída o error 5xx). Un 4xx, como una API key
        inválida o un lote demasiado grande, no se arregla reintentando: falla de inmediato."""
        for attempt in range(1, SEND_RETRIES + 1):
            try:
                response = self._client.post("/telemetry", json=batch)
                response.raise_for_status()
                self.totals.update(response.json())
                return
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                is_client_error = (
                    isinstance(exc, httpx.HTTPStatusError) and exc.response.is_client_error
                )
                if is_client_error or attempt == SEND_RETRIES:
                    raise
                wait_s = 2**attempt
                log.warning("Envío fallido (%s); reintento en %d s", exc, wait_s)
                time.sleep(wait_s)

    def close(self) -> None:
        self._client.close()


def timeline(start: datetime, end: datetime, dt_s: float) -> Iterator[datetime]:
    ts = start
    while ts <= end:
        yield ts
        ts += timedelta(seconds=dt_s)


def run_backfill(
    sim: FleetSimulation, sender: ApiSender, start: datetime, end: datetime, dt_s: float
) -> None:
    """Simula de `start` a `end` lo más rápido posible, en lotes de hasta MAX_BATCH_SIZE."""
    buffer: list[dict[str, Any]] = []
    batches = 0
    for ts in timeline(start, end, dt_s):
        buffer.extend(sim.tick(ts, dt_s))
        if len(buffer) >= MAX_BATCH_SIZE - sim.size:
            sender.send(buffer)
            buffer, batches = [], batches + 1
            if batches % 20 == 0:
                log.info(
                    "Histórico: %s · %s", ts.astimezone().strftime("%d %H:%M"), dict(sender.totals)
                )
    if buffer:
        sender.send(buffer)
    log.info("Histórico terminado: %s", dict(sender.totals))


def run_live(
    sim: FleetSimulation, sender: ApiSender, dt_s: float, duration_s: float | None
) -> None:
    """Envía un lote cada `dt_s` segundos con la hora real, hasta `duration_s` o Ctrl+C."""
    started = time.monotonic()
    log.info("Modo en vivo: un lote cada %.0f s (Ctrl+C para detener)", dt_s)
    try:
        while duration_s is None or time.monotonic() - started < duration_s:
            tick_started = time.monotonic()
            packets = sim.tick(datetime.now(UTC), dt_s)
            if packets:
                sender.send(packets)
                log.info("Enviados %d paquetes · acumulado %s", len(packets), dict(sender.totals))
            else:
                log.info("Fuera del horario de operación (5:00–21:00): sin reportes")
            time.sleep(max(0.0, dt_s - (time.monotonic() - tick_started)))
    except KeyboardInterrupt:
        log.info("Detenido por el usuario")
