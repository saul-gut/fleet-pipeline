"""Línea de comandos del simulador.

Ejemplos:
  uv run python -m fleet_pipeline.simulator register
  uv run python -m fleet_pipeline.simulator run --backfill-hours 24
  uv run python -m fleet_pipeline.simulator run --live --interval 10
  uv run python -m fleet_pipeline.simulator run --backfill-hours 48 --live
"""

from __future__ import annotations

import argparse
import logging
import random
from datetime import UTC, datetime, timedelta

import psycopg

from fleet_pipeline.config import get_settings
from fleet_pipeline.simulator.catalog import register_fleet, register_routes
from fleet_pipeline.simulator.fleet import build_fleet
from fleet_pipeline.simulator.routes import load_routes
from fleet_pipeline.simulator.runner import ApiSender, FleetSimulation, run_backfill, run_live

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("simulator")

MAX_BACKFILL_HOURS = 7 * 24  # la validación rechaza datos de más de 7 días (D-09)


def register(seed: int) -> None:
    """Da de alta rutas, vehículos y asignaciones. Es configuración, no telemetría."""
    routes = load_routes()
    fleet = build_fleet(routes, seed)
    with psycopg.connect(get_settings().conninfo) as conn:
        register_routes(conn, routes)
        register_fleet(conn, fleet)
    # El log muestra solo conteos: el catálogo con IDs reales queda en la base.
    log.info("Registradas %d rutas y %d vehículos", len(routes), len(fleet))


def run(args: argparse.Namespace) -> None:
    if not 0 <= args.backfill_hours <= MAX_BACKFILL_HOURS:
        raise SystemExit(f"--backfill-hours debe estar entre 0 y {MAX_BACKFILL_HOURS}")

    routes = load_routes()
    sim = FleetSimulation(build_fleet(routes, args.seed), routes, random.Random(args.seed))
    settings = get_settings()
    sender = ApiSender(args.api_url, settings.api_key_device.get_secret_value())
    try:
        if args.backfill_hours:
            end = datetime.now(UTC)
            run_backfill(
                sim, sender, end - timedelta(hours=args.backfill_hours), end, args.interval
            )
        if args.live:
            duration = args.duration_min * 60 if args.duration_min else None
            run_live(sim, sender, args.interval, duration)
    finally:
        sender.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="simulator", description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=42, help="semilla (flota reproducible)")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("register", help="registra la flota en bronze.vehicles")

    run_parser = commands.add_parser("run", help="envía telemetría a la API")
    run_parser.add_argument("--api-url", default="http://localhost:8000")
    run_parser.add_argument("--interval", type=float, default=10.0, help="segundos entre reportes")
    run_parser.add_argument("--backfill-hours", type=float, default=0, help="horas de histórico")
    run_parser.add_argument("--live", action="store_true", help="seguir en tiempo real")
    run_parser.add_argument(
        "--duration-min", type=float, help="minutos en vivo (por defecto: sin fin)"
    )

    args = parser.parse_args()
    if args.command == "register":
        register(args.seed)
    else:
        run(args)


if __name__ == "__main__":
    main()
