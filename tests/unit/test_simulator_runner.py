"""Envío de la simulación a la API: reintentos, lotes y modo en vivo, sin red real."""

import random
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from fleet_pipeline.simulator import runner
from fleet_pipeline.simulator.fleet import build_fleet
from fleet_pipeline.simulator.routes import load_routes
from fleet_pipeline.simulator.runner import (
    MAX_BATCH_SIZE,
    ApiSender,
    FleetSimulation,
    run_backfill,
    run_live,
)

SUMMARY = {"accepted": 2, "rejected": 1, "duplicates": 0}
OPERATING_TS = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)  # 10:00 en Colombia


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Los reintentos y el modo en vivo esperan con time.sleep: en los tests no se espera."""
    waits: list[float] = []
    monkeypatch.setattr(runner.time, "sleep", waits.append)
    return waits


def sender_with(responses: list[httpx.Response | Exception]) -> tuple[ApiSender, list]:
    """Un ApiSender cuyo servidor responde la lista dada, en orden."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        answer = responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return ApiSender("http://api", "device-key", httpx.MockTransport(handler)), requests


def test_send_posts_the_batch_with_the_api_key_and_counts_the_summary() -> None:
    sender, requests = sender_with([httpx.Response(200, json=SUMMARY)])
    sender.send([{"vehicle_id": "VHC-1234-ABC"}])
    assert requests[0].url.path == "/telemetry"
    assert requests[0].headers["X-API-Key"] == "device-key"
    assert sender.totals == SUMMARY


def test_send_retries_after_server_and_network_errors(no_sleep: list[float]) -> None:
    sender, requests = sender_with(
        [
            httpx.Response(503),
            httpx.ConnectError("sin conexión"),
            httpx.Response(200, json=SUMMARY),
        ]
    )
    sender.send([{}])
    assert len(requests) == 3
    assert no_sleep == [2, 4]  # espera exponencial entre intentos
    assert sender.totals == SUMMARY


def test_send_gives_up_after_three_attempts() -> None:
    sender, requests = sender_with([httpx.Response(500)] * 3)
    with pytest.raises(httpx.HTTPStatusError):
        sender.send([{}])
    assert len(requests) == 3


@pytest.mark.parametrize("status", [401, 413, 422])
def test_client_errors_fail_at_once_without_retrying(status: int, no_sleep: list[float]) -> None:
    """Una key inválida o un lote rechazado no se arreglan reintentando."""
    sender, requests = sender_with([httpx.Response(status)])
    with pytest.raises(httpx.HTTPStatusError):
        sender.send([{}])
    assert len(requests) == 1
    assert no_sleep == []


class FakeSender:
    def __init__(self) -> None:
        self.batches: list[list[dict[str, Any]]] = []

    def send(self, batch: list[dict[str, Any]]) -> None:
        self.batches.append(batch)

    @property
    def totals(self) -> dict[str, int]:
        return {"accepted": sum(len(b) for b in self.batches)}


@pytest.fixture(scope="module")
def simulation() -> FleetSimulation:
    routes = load_routes()
    return FleetSimulation(build_fleet(routes, seed=1), routes, random.Random(1))


def test_backfill_sends_batches_within_the_api_limit(simulation: FleetSimulation) -> None:
    sender = FakeSender()
    start = OPERATING_TS
    run_backfill(simulation, sender, start, start + timedelta(minutes=30), 10)  # type: ignore[arg-type]
    sizes = [len(b) for b in sender.batches]
    assert len(sizes) >= 2
    assert max(sizes) <= MAX_BATCH_SIZE
    assert sum(sizes) == simulation.size * 181  # 30 min cada 10 s = 181 instantes


def test_live_mode_sends_one_batch_per_tick(
    simulation: FleetSimulation, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = iter(float(i) for i in range(100))
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return OPERATING_TS

    monkeypatch.setattr(runner, "datetime", FixedClock)
    sender = FakeSender()
    run_live(simulation, sender, dt_s=10, duration_s=6)  # type: ignore[arg-type]
    assert sender.batches
    assert all(len(b) == simulation.size for b in sender.batches)


def test_live_mode_stops_cleanly_on_ctrl_c(
    simulation: FleetSimulation, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupt(_: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(runner.time, "sleep", interrupt)
    run_live(simulation, FakeSender(), dt_s=10, duration_s=None)  # type: ignore[arg-type]
