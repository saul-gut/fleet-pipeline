"""Uso: uv run python -m fleet_pipeline.processing silver"""

import argparse
import logging

import psycopg

from fleet_pipeline.config import get_settings
from fleet_pipeline.processing.silver import run_silver

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(prog="processing")
    parser.add_argument("layer", choices=["silver"], help="capa a procesar")
    parser.parse_args()
    with psycopg.connect(get_settings().conninfo) as conn:
        run_silver(conn)


if __name__ == "__main__":
    main()
