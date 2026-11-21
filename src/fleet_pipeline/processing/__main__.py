"""Uso: uv run python -m fleet_pipeline.processing [silver|gold|all]"""

import argparse
import logging

import psycopg

from fleet_pipeline.config import get_settings
from fleet_pipeline.processing.gold import run_gold
from fleet_pipeline.processing.silver import run_silver

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(prog="processing")
    parser.add_argument("layer", choices=["silver", "gold", "all"], help="capa a procesar")
    args = parser.parse_args()
    with psycopg.connect(get_settings().conninfo) as conn:
        if args.layer in ("silver", "all"):
            run_silver(conn)
            conn.commit()
        if args.layer in ("gold", "all"):
            run_gold(conn)


if __name__ == "__main__":
    main()
